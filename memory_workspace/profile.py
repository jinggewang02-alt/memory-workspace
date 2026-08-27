"""Exact Profile Memory operations used by the compatibility CLI and UI."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .home import exact_profile_path_info, init_home as init_memory_home
from .io import (
    MemoryWorkspaceError,
    atomic_write_json,
    load_json,
    nearest_existing_parent,
    transient_reason,
)


SCHEMA_VERSION = 1


def store_path_info() -> tuple[Path, str]:
    return exact_profile_path_info()


def store_path() -> Path:
    return store_path_info()[0]


def new_store() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "updated_at": int(time.time()), "items": {}}


def load_store(path: Path) -> dict[str, Any] | None:
    document = load_json(path)
    if document is None:
        return None
    if not isinstance(document.get("items"), dict):
        raise MemoryWorkspaceError(f"档案结构无效（{path}），为避免覆盖已停止。")
    return document


def load_or_new(path: Path) -> dict[str, Any]:
    return load_store(path) or new_store()


def save_store(path: Path, document: dict[str, Any]) -> None:
    default_path, source = store_path_info()
    if source == "memory-home" and path.resolve(strict=False) == default_path.resolve(strict=False):
        init_memory_home(root=path.parents[2])
    document["updated_at"] = int(time.time())
    atomic_write_json(
        path,
        document,
        backup=True,
        allow_transient=os.environ.get("PMEM_ALLOW_TRANSIENT") == "1",
    )


def set_single(path: Path, key: str, value: str) -> dict[str, Any]:
    document = load_or_new(path)
    document["items"][key] = {"type": "single", "value": value}
    save_store(path, document)
    return {"key": key, "type": "single", "value": value}


def add_entry(path: Path, key: str, fields: dict[str, str]) -> dict[str, Any]:
    if not fields:
        raise MemoryWorkspaceError("add 至少需要一个字段。")
    document = load_or_new(path)
    item = document["items"].get(key)
    if item is None:
        item = {"type": "entries", "value": []}
        document["items"][key] = item
    if item.get("type") != "entries" or not isinstance(item.get("value"), list):
        raise MemoryWorkspaceError(f"[{key}] 已是单值类型，不能 add 条目（用 set 覆盖或换 key）。")
    item["value"].append(dict(fields))
    save_store(path, document)
    return {"key": key, "type": "entries", "index": len(item["value"]), "value": dict(fields)}


def get_item(
    path: Path,
    key: str,
    *,
    index: int | None = None,
    field: str | None = None,
) -> dict[str, Any]:
    document = load_store(path)
    if document is None or key not in document["items"]:
        raise MemoryWorkspaceError(f"未找到 [{key}]")
    item = document["items"][key]
    if item.get("type") == "single":
        return {"key": key, "type": "single", "value": item.get("value", "")}

    entries = item.get("value")
    if not isinstance(entries, list):
        raise MemoryWorkspaceError(f"[{key}] 的结构化条目已损坏。")
    if index is None:
        return {"key": key, "type": "entries", "value": entries}
    if index < 1 or index > len(entries):
        raise MemoryWorkspaceError(f"[{key}] 只有 {len(entries)} 条，index 越界。")
    entry = entries[index - 1]
    if field is not None:
        if field not in entry:
            raise MemoryWorkspaceError(f"第 {index} 条没有字段 '{field}'。")
        return {
            "key": key,
            "type": "entries",
            "index": index,
            "field": field,
            "value": entry[field],
        }
    return {"key": key, "type": "entries", "index": index, "value": entry}


def search_items(path: Path, keyword: str) -> list[dict[str, Any]]:
    document = load_store(path)
    if document is None:
        return []
    matches: list[dict[str, Any]] = []
    lowered = keyword.lower()
    for key, item in document["items"].items():
        if lowered not in key.lower():
            continue
        if item.get("type") == "single":
            value = str(item.get("value", ""))
            preview = value if len(value) <= 40 else value[:40] + "…"
            matches.append({"key": key, "type": "single", "preview": preview})
        else:
            entries = item.get("value", [])
            matches.append({"key": key, "type": "entries", "count": len(entries)})
    return matches


def list_items(path: Path) -> list[dict[str, Any]]:
    document = load_store(path)
    if document is None:
        return []
    result: list[dict[str, Any]] = []
    for key, item in document["items"].items():
        if item.get("type") == "single":
            result.append({"key": key, "type": "single"})
        else:
            result.append({"key": key, "type": "entries", "count": len(item.get("value", []))})
    return result


def remove_item(path: Path, key: str, *, index: int | None = None) -> dict[str, Any]:
    document = load_store(path)
    if document is None or key not in document["items"]:
        raise MemoryWorkspaceError(f"未找到 [{key}]")
    item = document["items"][key]
    if index is not None:
        if item.get("type") != "entries":
            raise MemoryWorkspaceError(f"[{key}] 是单值，不能按 index 删。")
        entries = item.get("value", [])
        if index < 1 or index > len(entries):
            raise MemoryWorkspaceError("index 越界。")
        removed = entries.pop(index - 1)
        save_store(path, document)
        return {"key": key, "type": "entries", "index": index, "removed": removed}
    removed = document["items"].pop(key)
    save_store(path, document)
    return {"key": key, "type": removed.get("type"), "removed": True}


def export_markdown(path: Path) -> str:
    document = load_store(path)
    if document is None or not document["items"]:
        raise MemoryWorkspaceError("空档案，无可导出。")
    lines = ["# 个人档案", ""]
    for key, item in document["items"].items():
        if item.get("type") == "single":
            lines.append(f"- **{key}**: {item.get('value', '')}")
        else:
            lines.append(f"## {key}")
            for index, entry in enumerate(item.get("value", []), 1):
                lines.append(f"### 第 {index} 条")
                for field, value in entry.items():
                    lines.append(f"- **{field}**: {value}")
            lines.append("")
    return "\n".join(lines) + "\n"


def doctor(path: Path) -> dict[str, Any]:
    _, source = store_path_info()
    reason = transient_reason(path)
    parent = nearest_existing_parent(path.parent)
    writable = os.access(parent, os.W_OK)
    warnings: list[str] = []
    if os.environ.get("PMEM_PROJECT_DIR"):
        warnings.append("PMEM_PROJECT_DIR 已弃用并被忽略。")
    if source in {"PMEM_FILE", "PMEM_DIR"}:
        warnings.append("正在使用组件级旧路径覆盖；新安装建议统一配置 MEMORY_HOME。")
    if reason:
        warnings.append(reason + "；正式写入将被拒绝。")
    if not writable:
        warnings.append(f"现有父目录不可写：{parent}")
    return {
        "store_path": str(path),
        "source": source,
        "exists": path.exists(),
        "backup_path": str(path) + ".bak",
        "nearest_existing_parent": str(parent),
        "filesystem_writable": writable,
        "transient_risk": reason is not None,
        "status": "WARNING" if warnings else "OK",
        "warnings": warnings,
        "note": "沙箱可能仍需对上述确切目录单独授权。",
    }
