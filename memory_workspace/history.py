"""Platform-neutral, bounded import of user query history."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .capture import enqueue_event, parse_datetime
from .episodes import explicit_save
from .io import MemoryWorkspaceError


def _load_rows(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if limit < 1 or limit > 5000:
        raise MemoryWorkspaceError("history import limit 必须在 1–5000 之间。")
    rows = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                if len(rows) >= limit:
                    raise MemoryWorkspaceError(
                        f"历史记录超过本次上限 {limit}；请缩小时间范围或显式提高 limit。"
                    )
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise MemoryWorkspaceError(
                        f"历史 JSONL 第 {line_number} 行无效：{exc.msg}"
                    ) from exc
                if not isinstance(value, dict):
                    raise MemoryWorkspaceError(f"历史 JSONL 第 {line_number} 行必须是对象。")
                value["_line_number"] = line_number
                rows.append(value)
    except OSError as exc:
        raise MemoryWorkspaceError(f"无法读取历史文件 {path}：{exc}") from exc
    return rows


def _normalize_row(row: dict[str, Any], *, adapter: str) -> dict[str, Any] | None:
    line = row["_line_number"]
    role = str(row.get("role") or "user").strip().lower()
    if role != "user":
        return None
    message = row.get("user_message", row.get("content"))
    if not isinstance(message, str) or not message.strip():
        raise MemoryWorkspaceError(f"历史 JSONL 第 {line} 行缺少 user_message/content。")
    conversation_id = row.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise MemoryWorkspaceError(f"历史 JSONL 第 {line} 行缺少 conversation_id。")
    occurred_at = row.get("occurred_at")
    if not isinstance(occurred_at, str):
        raise MemoryWorkspaceError(f"历史 JSONL 第 {line} 行缺少 occurred_at。")
    parse_datetime(occurred_at)
    message_id = row.get("message_id")
    workspace_id = row.get("workspace_id")
    assistant_summary = row.get("assistant_summary")
    for value, label in (
        (message_id, "message_id"),
        (workspace_id, "workspace_id"),
        (assistant_summary, "assistant_summary"),
    ):
        if value is not None and not isinstance(value, str):
            raise MemoryWorkspaceError(f"历史 JSONL 第 {line} 行的 {label} 必须是字符串。")
    direct_route = str(row.get("direct_route") or "none")
    if direct_route == "none" and explicit_save(message):
        direct_route = (
            "workspace"
            if re.search(r"wiki|知识库|工作区|workspace", message, re.I)
            else "remember"
        )
    return {
        "user_message": message,
        "assistant_summary": assistant_summary,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "workspace_id": workspace_id,
        "occurred_at": occurred_at,
        "source_agent": str(row.get("source_agent") or f"history:{adapter}"),
        "source_adapter": adapter,
        "direct_route": direct_route,
        "capture_eligible": direct_route == "none",
    }


def import_jsonl(
    path: Path,
    *,
    adapter: str = "generic-jsonl",
    retention_days: int = 30,
    limit: int = 1000,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    clean_adapter = adapter.strip()
    if not clean_adapter:
        raise MemoryWorkspaceError("adapter 不能为空。")
    if retention_days < 1 or retention_days > 30:
        raise MemoryWorkspaceError("retention days 必须在 1–30 之间。")
    rows = _load_rows(path, limit=limit)
    start = parse_datetime(since) if isinstance(since, str) else since
    end = parse_datetime(until) if isinstance(until, str) else until
    for value, label in ((start, "since"), (end, "until")):
        if value is not None and value.tzinfo is None:
            raise MemoryWorkspaceError(f"{label} 缺少时区。")
    if start is not None and end is not None and start >= end:
        raise MemoryWorkspaceError("history window 的 since 必须早于 until。")

    normalized = []
    ignored_roles = 0
    outside_window = 0
    for row in rows:
        item = _normalize_row(row, adapter=clean_adapter)
        if item is None:
            ignored_roles += 1
        else:
            occurred = parse_datetime(item["occurred_at"])
            if (start is not None and occurred < start) or (
                end is not None and occurred > end
            ):
                outside_window += 1
                continue
            if item["direct_route"] not in {"none", "workspace", "remember", "recall"}:
                raise MemoryWorkspaceError(
                    f"历史 direct_route 无效：{item['direct_route']}"
                )
            normalized.append(item)

    imported_ids = []
    duplicates = 0
    rejected_secrets = 0
    for item in normalized:
        try:
            result = enqueue_event(
                item["user_message"],
                assistant_summary=item["assistant_summary"],
                conversation_id=item["conversation_id"],
                message_id=item["message_id"],
                workspace_id=item["workspace_id"],
                source_agent=item["source_agent"],
                source_adapter=item["source_adapter"],
                source_kind="history_import",
                occurred_at=item["occurred_at"],
                direct_route=item["direct_route"],
                capture_eligible=item["capture_eligible"],
                retention_days=retention_days,
                root=root,
            )
        except MemoryWorkspaceError as exc:
            if "事件未入队" in str(exc):
                rejected_secrets += 1
                continue
            raise
        if result.get("deduplicated"):
            duplicates += 1
        else:
            imported_ids.append(result["event_id"])
    return {
        "adapter": clean_adapter,
        "source_path": str(path),
        "input_rows": len(rows),
        "user_rows": len(normalized),
        "imported_count": len(imported_ids),
        "duplicate_count": duplicates,
        "rejected_secret_count": rejected_secrets,
        "ignored_non_user_count": ignored_roles,
        "outside_window_count": outside_window,
        "window_start": start.isoformat() if start is not None else None,
        "window_end": end.isoformat() if end is not None else None,
        "event_ids": imported_ids,
    }
