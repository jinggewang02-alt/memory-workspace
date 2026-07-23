#!/usr/bin/env python3
"""store.py — 精确个人档案记忆库 CLI（纯 stdlib，无第三方依赖）。

底层用 JSON 存储（机器精确、结构化友好），可 export 成 md 供人阅读。
两种 value 类型：
  - single      单值：邮箱、学号、地址、卡号…   key -> "一段文本"
  - entries     结构化条目：经历、获奖…         key -> [ {字段:值, ...}, ... ]

逐字保真：get 命令原样打印 value，不做任何改写/格式化，供直接复制。

文件路径优先级：
  1. PMEM_FILE            —— 完整文件路径（最高）
  2. PMEM_DIR            —— 目录，文件名固定
  3. HOME                —— 默认放 ~/.personal-memory/store.json（个人档案跨项目通用）

PMEM_PROJECT_DIR 已弃用并忽略，防止档案意外落入项目或临时沙箱。
临时目录默认拒绝写入；仅测试时可显式设置 PMEM_ALLOW_TRANSIENT=1。

用法：
  store.py set    <key> <value>                     存/覆盖一个单值
  store.py add    <key> --field k=v [--field k=v...] 往结构化条目 key 追加一条
  store.py get    <key> [--index N] [--field F]      逐字打印 value（复制用，无修饰）
  store.py search <关键词>                            按 key 模糊检索
  store.py list                                       列出所有 key（不打印敏感全文）
  store.py remove <key> [--index N]                   删除一个 key 或某条目
  store.py export [--out FILE]                        导出为 md（人可读）
  store.py path                                       打印当前存储文件路径
  store.py doctor                                     检查路径来源、权限和临时目录风险
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time

SCHEMA_VERSION = 1


class StoreError(Exception):
    """可安全展示给用户的存储错误。"""


def store_path_info():
    p = os.environ.get("PMEM_FILE")
    if p:
        return os.path.abspath(os.path.expanduser(p)), "PMEM_FILE"
    base = os.environ.get("PMEM_DIR")
    if base:
        base = os.path.abspath(os.path.expanduser(base))
        return os.path.join(base, "store.json"), "PMEM_DIR"
    home = os.path.expanduser("~")
    if not home or home == "~":
        raise StoreError("无法解析用户 HOME；请用 PMEM_DIR 指定本机持久目录。")
    base = os.path.join(os.path.abspath(home), ".personal-memory")
    return os.path.join(base, "store.json"), "default-home"


def store_path():
    return store_path_info()[0]


def _is_within(path, root):
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def transient_reason(path):
    """若路径位于常见临时目录，返回原因；否则返回 None。"""
    resolved = os.path.realpath(path)
    candidates = {"/tmp", "/private/tmp"}
    for value in (
        tempfile.gettempdir(),
        os.environ.get("TMPDIR"),
        os.environ.get("TEMP"),
        os.environ.get("TMP"),
    ):
        if value:
            candidates.add(os.path.realpath(os.path.expanduser(value)))
    for root in sorted(candidates, key=len, reverse=True):
        root = os.path.realpath(root)
        if _is_within(resolved, root):
            return f"路径位于临时目录 {root}"
    return None


def ensure_safe_write_path(path):
    reason = transient_reason(path)
    if reason and os.environ.get("PMEM_ALLOW_TRANSIENT") != "1":
        raise StoreError(
            f"{reason}，已拒绝写入。请用 PMEM_DIR 或 PMEM_FILE 指向本机持久目录；"
            "仅测试时可设置 PMEM_ALLOW_TRANSIENT=1。"
        )


def nearest_existing_parent(path):
    current = os.path.abspath(path)
    while not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def load(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except json.JSONDecodeError as exc:
        raise StoreError(
            f"档案 JSON 已损坏（{path}:{exc.lineno}:{exc.colno}），为避免覆盖已停止。"
        ) from exc
    except OSError as exc:
        raise StoreError(f"无法读取档案 {path}：{exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), dict):
        raise StoreError(f"档案结构无效（{path}），为避免覆盖已停止。")
    return doc


def save(path, data):
    ensure_safe_write_path(path)
    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
    except OSError as exc:
        raise StoreError(
            f"无法创建本机档案目录 {directory}：{exc}。没有回退到临时目录。"
        ) from exc

    tmp = path + ".tmp"
    backup = path + ".bak"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        if os.path.exists(path):
            shutil.copy2(path, backup)
            try:
                os.chmod(backup, 0o600)
            except OSError:
                pass
        os.replace(tmp, path)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise StoreError(
            f"无法持久化档案到 {path}：{exc}。没有回退到临时目录。"
        ) from exc


def new_doc():
    return {"schema_version": SCHEMA_VERSION, "updated_at": int(time.time()), "items": {}}


def load_or_new(path):
    return load(path) or new_doc()


def touch(doc):
    doc["updated_at"] = int(time.time())


# ── 子命令 ──────────────────────────────────────────────

def cmd_set(args, path):
    """存/覆盖一个单值。"""
    doc = load_or_new(path)
    doc["items"][args.key] = {"type": "single", "value": args.value}
    touch(doc)
    save(path, doc)
    print(f"[pmem] 已记住 [{args.key}] = {args.value}")


def _parse_fields(field_args):
    fields = {}
    for raw in field_args or []:
        if "=" not in raw:
            print(f"[pmem] 错误：--field 需 k=v 格式，收到 '{raw}'", file=sys.stderr)
            sys.exit(1)
        k, v = raw.split("=", 1)
        fields[k.strip()] = v
    return fields


def cmd_add(args, path):
    """往结构化条目 key 追加一条（key 不存在则创建为 entries 类型）。"""
    doc = load_or_new(path)
    fields = _parse_fields(args.field)
    if not fields:
        print("[pmem] 错误：add 至少需要一个 --field k=v", file=sys.stderr)
        sys.exit(1)
    item = doc["items"].get(args.key)
    if item is None:
        item = {"type": "entries", "value": []}
        doc["items"][args.key] = item
    if item["type"] != "entries":
        print(f"[pmem] 错误：[{args.key}] 已是单值类型，不能 add 条目（用 set 覆盖或换 key）。", file=sys.stderr)
        sys.exit(1)
    item["value"].append(fields)
    touch(doc)
    save(path, doc)
    print(f"[pmem] 已向 [{args.key}] 追加第 {len(item['value'])} 条：{fields}")


def cmd_get(args, path):
    """逐字打印 value（复制用，绝不改写/润色）。"""
    doc = load(path)
    if doc is None or args.key not in doc["items"]:
        print(f"[pmem] 未找到 [{args.key}]", file=sys.stderr)
        sys.exit(1)
    item = doc["items"][args.key]
    if item["type"] == "single":
        # 单值：原样输出，不加任何前缀/换行修饰
        sys.stdout.write(item["value"])
        sys.stdout.write("\n")
        return
    # entries
    entries = item["value"]
    if args.index is not None:
        if args.index < 1 or args.index > len(entries):
            print(f"[pmem] 错误：[{args.key}] 只有 {len(entries)} 条，index 越界。", file=sys.stderr)
            sys.exit(1)
        entry = entries[args.index - 1]
        if args.field:
            if args.field not in entry:
                print(f"[pmem] 错误：第 {args.index} 条没有字段 '{args.field}'。", file=sys.stderr)
                sys.exit(1)
            sys.stdout.write(entry[args.field]); sys.stdout.write("\n")
        else:
            for k, v in entry.items():
                print(f"{k}: {v}")
        return
    # 无 index：打印全部条目（逐字）
    for i, entry in enumerate(entries, 1):
        print(f"# 第 {i} 条")
        for k, v in entry.items():
            print(f"{k}: {v}")
        if i < len(entries):
            print()


def cmd_search(args, path):
    """按 key 模糊检索（大小写不敏感，子串匹配）。"""
    doc = load(path)
    if doc is None or not doc["items"]:
        print("[pmem] （空档案）")
        return
    kw = args.keyword.lower()
    hits = [k for k in doc["items"] if kw in k.lower()]
    if not hits:
        print(f"[pmem] 没有匹配 '{args.keyword}' 的 key")
        return
    for k in hits:
        item = doc["items"][k]
        if item["type"] == "single":
            preview = item["value"] if len(item["value"]) <= 40 else item["value"][:40] + "…"
            print(f"  {k} (single): {preview}")
        else:
            print(f"  {k} (entries): {len(item['value'])} 条")


def cmd_list(args, path):
    """列出所有 key 概览。"""
    doc = load(path)
    if doc is None or not doc["items"]:
        print("[pmem] （空档案）")
        return
    for k, item in doc["items"].items():
        if item["type"] == "single":
            print(f"  {k}  [single]")
        else:
            print(f"  {k}  [entries × {len(item['value'])}]")


def cmd_remove(args, path):
    doc = load(path)
    if doc is None or args.key not in doc["items"]:
        print(f"[pmem] 未找到 [{args.key}]", file=sys.stderr)
        sys.exit(1)
    item = doc["items"][args.key]
    if args.index is not None:
        if item["type"] != "entries":
            print(f"[pmem] 错误：[{args.key}] 是单值，不能按 index 删。", file=sys.stderr)
            sys.exit(1)
        if args.index < 1 or args.index > len(item["value"]):
            print(f"[pmem] 错误：index 越界。", file=sys.stderr)
            sys.exit(1)
        item["value"].pop(args.index - 1)
        touch(doc); save(path, doc)
        print(f"[pmem] 已删除 [{args.key}] 第 {args.index} 条")
        return
    del doc["items"][args.key]
    touch(doc); save(path, doc)
    print(f"[pmem] 已删除 [{args.key}]")


def cmd_export(args, path):
    """导出为 md（人可读）。"""
    doc = load(path)
    if doc is None or not doc["items"]:
        print("[pmem] （空档案，无可导出）", file=sys.stderr)
        sys.exit(1)
    lines = ["# 个人档案", ""]
    for k, item in doc["items"].items():
        if item["type"] == "single":
            lines.append(f"- **{k}**: {item['value']}")
        else:
            lines.append(f"## {k}")
            for i, entry in enumerate(item["value"], 1):
                lines.append(f"### 第 {i} 条")
                for fk, fv in entry.items():
                    lines.append(f"- **{fk}**: {fv}")
            lines.append("")
    md = "\n".join(lines) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"[pmem] 已导出到 {args.out}")
    else:
        sys.stdout.write(md)


def cmd_path(args, path):
    print(path)


def cmd_doctor(args, path):
    _, source = store_path_info()
    reason = transient_reason(path)
    parent = nearest_existing_parent(os.path.dirname(path))
    writable = os.access(parent, os.W_OK)
    warnings = []
    if os.environ.get("PMEM_PROJECT_DIR"):
        warnings.append("PMEM_PROJECT_DIR 已弃用并被忽略。")
    if reason:
        warnings.append(reason + "；正式写入将被拒绝。")
    if not writable:
        warnings.append(f"现有父目录不可写：{parent}")

    print(f"store_path: {path}")
    print(f"source: {source}")
    print(f"exists: {'yes' if os.path.exists(path) else 'no'}")
    print(f"backup_path: {path}.bak")
    print(f"nearest_existing_parent: {parent}")
    print(f"filesystem_writable: {'yes' if writable else 'no'}")
    print(f"transient_risk: {'yes' if reason else 'no'}")
    print(f"status: {'WARNING' if warnings else 'OK'}")
    for warning in warnings:
        print(f"warning: {warning}")
    print("note: 沙箱可能仍需对上述确切目录单独授权。")


def build_parser():
    p = argparse.ArgumentParser(description="精确个人档案记忆库 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("set"); s.add_argument("key"); s.add_argument("value")
    s = sub.add_parser("add"); s.add_argument("key"); s.add_argument("--field", action="append")
    s = sub.add_parser("get"); s.add_argument("key")
    s.add_argument("--index", type=int); s.add_argument("--field")
    s = sub.add_parser("search"); s.add_argument("keyword")
    sub.add_parser("list")
    s = sub.add_parser("remove"); s.add_argument("key"); s.add_argument("--index", type=int)
    s = sub.add_parser("export"); s.add_argument("--out")
    sub.add_parser("path")
    sub.add_parser("doctor")
    return p


def main():
    try:
        args = build_parser().parse_args()
        path = store_path()
        dispatch = {
            "set": cmd_set, "add": cmd_add, "get": cmd_get, "search": cmd_search,
            "list": cmd_list, "remove": cmd_remove, "export": cmd_export,
            "path": cmd_path, "doctor": cmd_doctor,
        }
        dispatch[args.cmd](args, path)
    except StoreError as exc:
        print(f"[pmem] 错误：{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
