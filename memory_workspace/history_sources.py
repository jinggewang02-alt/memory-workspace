"""Discover a platform-neutral local history handoff without widening access."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from .io import MemoryWorkspaceError


def discover_history_source(
    *,
    path: Path | None = None,
    adapter: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve only an explicitly supplied or host-configured history source."""

    environment = os.environ if environ is None else environ
    configured = environment.get("MWORK_HISTORY_FILE")
    selected = path or (Path(configured) if configured else None)
    selected_adapter = (
        adapter or environment.get("MWORK_HISTORY_ADAPTER") or "agent-visible-jsonl"
    ).strip()
    if not selected_adapter:
        raise MemoryWorkspaceError("history adapter 不能为空。")
    if selected is None:
        return {
            "status": "needs_history_source",
            "available": False,
            "auto_configured": False,
            "adapter": None,
            "source_path": None,
            "boundary": (
                "当前环境没有提供可读取的历史适配器。请由当前 Agent 将其本来有权限"
                "看到的近 30 天用户 Query 转为标准 JSONL；不得搜索其他账号或目录。"
            ),
        }

    resolved = Path(os.path.abspath(os.path.expanduser(str(selected))))
    if not resolved.is_file():
        raise MemoryWorkspaceError(f"历史来源不存在或不是文件：{resolved}")
    if resolved.suffix.lower() not in {".jsonl", ".ndjson"}:
        raise MemoryWorkspaceError("历史来源必须是 .jsonl 或 .ndjson 文件。")
    return {
        "status": "ready",
        "available": True,
        "auto_configured": path is None and configured is not None,
        "adapter": selected_adapter,
        "source_path": str(resolved),
        "boundary": "只读取该适配器明确交付、且位于本次 30 天窗口内的用户 Query。",
    }
