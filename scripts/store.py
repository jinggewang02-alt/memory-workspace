#!/usr/bin/env python3
"""Compatibility CLI for exact Profile Memory storage.

Existing text commands remain stable. Add ``--json`` after a subcommand for a
machine-readable response consumed by the Skill and future UI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_workspace.io import MemoryWorkspaceError  # noqa: E402
from memory_workspace import profile  # noqa: E402


def parse_fields(values: list[str] | None) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw in values or []:
        if "=" not in raw:
            raise MemoryWorkspaceError(f"--field 需 k=v 格式，收到 '{raw}'")
        key, value = raw.split("=", 1)
        fields[key.strip()] = value
    return fields


def emit_json(command: str, path: Path, payload: Any) -> None:
    print(
        json.dumps(
            {"ok": True, "command": command, "path": str(path), "result": payload},
            ensure_ascii=False,
        )
    )


def render_get(result: dict[str, Any]) -> None:
    if result["type"] == "single" or "field" in result:
        sys.stdout.write(str(result["value"]) + "\n")
        return
    if "index" in result:
        for key, value in result["value"].items():
            print(f"{key}: {value}")
        return
    entries = result["value"]
    for index, entry in enumerate(entries, 1):
        print(f"# 第 {index} 条")
        for key, value in entry.items():
            print(f"{key}: {value}")
        if index < len(entries):
            print()


def run_command(args: argparse.Namespace, path: Path) -> Any:
    if args.cmd == "set":
        return profile.set_single(path, args.key, args.value)
    if args.cmd == "add":
        return profile.add_entry(path, args.key, parse_fields(args.field))
    if args.cmd == "get":
        return profile.get_item(path, args.key, index=args.index, field=args.field)
    if args.cmd == "search":
        return profile.search_items(path, args.keyword)
    if args.cmd == "list":
        return profile.list_items(path)
    if args.cmd == "remove":
        return profile.remove_item(path, args.key, index=args.index)
    if args.cmd == "export":
        markdown = profile.export_markdown(path)
        if args.out:
            output = Path(args.out).expanduser()
            output.write_text(markdown, encoding="utf-8")
            return {"output_path": str(output), "display_path": args.out}
        return {"markdown": markdown}
    if args.cmd == "path":
        return {"store_path": str(path)}
    if args.cmd == "doctor":
        return profile.doctor(path)
    raise MemoryWorkspaceError(f"未知命令：{args.cmd}")


def render_text(args: argparse.Namespace, result: Any, path: Path) -> None:
    if args.cmd == "set":
        print(f"[pmem] 已记住 [{result['key']}] = {result['value']}")
    elif args.cmd == "add":
        print(f"[pmem] 已向 [{result['key']}] 追加第 {result['index']} 条：{result['value']}")
    elif args.cmd == "get":
        render_get(result)
    elif args.cmd == "search":
        document = profile.load_store(path)
        if document is None or not document["items"]:
            print("[pmem] （空档案）")
        elif not result:
            print(f"[pmem] 没有匹配 '{args.keyword}' 的 key")
        for item in result:
            if item["type"] == "single":
                print(f"  {item['key']} (single): {item['preview']}")
            else:
                print(f"  {item['key']} (entries): {item['count']} 条")
    elif args.cmd == "list":
        if not result:
            print("[pmem] （空档案）")
        for item in result:
            if item["type"] == "single":
                print(f"  {item['key']}  [single]")
            else:
                print(f"  {item['key']}  [entries × {item['count']}]")
    elif args.cmd == "remove":
        if "index" in result:
            print(f"[pmem] 已删除 [{result['key']}] 第 {result['index']} 条")
        else:
            print(f"[pmem] 已删除 [{result['key']}]")
    elif args.cmd == "export":
        if args.out:
            print(f"[pmem] 已导出到 {result['display_path']}")
        else:
            sys.stdout.write(result["markdown"])
    elif args.cmd == "path":
        print(path)
    elif args.cmd == "doctor":
        report = result
        for key in (
            "store_path",
            "source",
            "exists",
            "backup_path",
            "nearest_existing_parent",
            "filesystem_writable",
            "transient_risk",
            "status",
        ):
            value = report[key]
            if isinstance(value, bool):
                value = "yes" if value else "no"
            print(f"{key}: {value}")
        for warning in report["warnings"]:
            print(f"warning: {warning}")
        print(f"note: {report['note']}")


def add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="精确个人档案记忆库 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    command = sub.add_parser("set")
    command.add_argument("key")
    command.add_argument("value")
    add_json_flag(command)

    command = sub.add_parser("add")
    command.add_argument("key")
    command.add_argument("--field", action="append")
    add_json_flag(command)

    command = sub.add_parser("get")
    command.add_argument("key")
    command.add_argument("--index", type=int)
    command.add_argument("--field")
    add_json_flag(command)

    command = sub.add_parser("search")
    command.add_argument("keyword")
    add_json_flag(command)

    command = sub.add_parser("list")
    add_json_flag(command)

    command = sub.add_parser("remove")
    command.add_argument("key")
    command.add_argument("--index", type=int)
    add_json_flag(command)

    command = sub.add_parser("export")
    command.add_argument("--out")
    add_json_flag(command)

    command = sub.add_parser("path")
    add_json_flag(command)

    command = sub.add_parser("doctor")
    add_json_flag(command)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    path = profile.store_path()
    try:
        result = run_command(args, path)
    except MemoryWorkspaceError as exc:
        if getattr(args, "as_json", False):
            print(json.dumps({"ok": False, "command": args.cmd, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"[pmem] 错误：{exc}", file=sys.stderr)
        return 1
    if args.as_json:
        emit_json(args.cmd, path, result)
    else:
        render_text(args, result, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
