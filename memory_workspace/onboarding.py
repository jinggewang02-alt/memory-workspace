"""Idempotent first-run learning over an Agent-authorized 30-day history window."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import capture, habits, history, policy
from .history_sources import discover_history_source
from .io import MemoryWorkspaceError, atomic_write_json
from .schema import load_json_object, validate


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
STATE_SCHEMA = PACKAGE_ROOT / "schemas" / "onboarding-state.schema.json"


def state_path(root: Path) -> Path:
    return root / "onboarding" / "state.json"


def _root(root: Path | None) -> Path:
    return root or capture.capture_path()


def _load_state(root: Path) -> dict[str, Any] | None:
    path = state_path(root)
    if not path.is_file():
        return None
    try:
        document = load_json_object(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MemoryWorkspaceError(f"无法读取首次学习状态：{exc}") from exc
    errors = validate(document, load_json_object(STATE_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("首次学习状态校验失败：" + "; ".join(errors))
    return document


def _write_state(document: dict[str, Any], *, root: Path) -> None:
    errors = validate(document, load_json_object(STATE_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("首次学习状态校验失败：" + "; ".join(errors))
    atomic_write_json(
        state_path(root),
        document,
        allow_transient=capture.allow_transient(),
    )


def _public_status(document: dict[str, Any], *, root: Path) -> dict[str, Any]:
    report = habits.load_report(root=root)
    active = policy.load_active_policy(root=root)
    return {
        "status": document["status"],
        "run": document,
        "habits": report,
        "policy_active": active is not None
        and active["policy_id"] == document["policy_id"],
        "can_confirm": document["status"] == "awaiting_review",
    }


def get_status(
    *,
    root: Path | None = None,
    history_file: Path | None = None,
    adapter: str | None = None,
) -> dict[str, Any]:
    selected_root = _root(root)
    document = _load_state(selected_root)
    if document is not None:
        return _public_status(document, root=selected_root)
    source = discover_history_source(path=history_file, adapter=adapter)
    return {
        "status": "ready" if source["available"] else "needs_history_source",
        "run": None,
        "habits": None,
        "policy_active": False,
        "can_confirm": False,
        "history_source": source,
    }


def run_first_learning(
    *,
    root: Path | None = None,
    history_file: Path | None = None,
    adapter: str | None = None,
    days: int = 30,
    limit: int = 1000,
    force: bool = False,
    current_time: datetime | None = None,
) -> dict[str, Any]:
    if days < 1 or days > 30:
        raise MemoryWorkspaceError("首次历史学习只能读取最近 1–30 天。")
    selected_root = _root(root)
    existing = _load_state(selected_root)
    if existing is not None and not force:
        result = _public_status(existing, root=selected_root)
        result["deduplicated"] = True
        return result

    source = discover_history_source(path=history_file, adapter=adapter)
    if not source["available"]:
        raise MemoryWorkspaceError(source["boundary"])
    end = current_time or capture.now_utc()
    if end.tzinfo is None:
        raise MemoryWorkspaceError("current_time 缺少时区。")
    end = end.astimezone(timezone.utc)
    start = end - timedelta(days=days)
    start_text = capture.format_datetime(start)
    end_text = capture.format_datetime(end)

    imported = history.import_jsonl(
        Path(source["source_path"]),
        adapter=str(source["adapter"]),
        retention_days=30,
        limit=limit,
        since=start,
        until=end,
        root=selected_root,
    )
    corpus = [
        event
        for event in capture.all_event_documents(root=selected_root)
        if event.get("source_kind") == "history_import"
        and event["source"].get("adapter") == source["adapter"]
        and start <= capture.parse_datetime(event.get("occurred_at", event["created_at"])) <= end
    ]
    report_document = habits.build_baseline_document(
        corpus,
        coverage_start=start_text,
        coverage_end=end_text,
        days=days,
    )
    report_result = habits.write_report(report_document, root=selected_root)
    policy_result = policy.build_policy(corpus, root=selected_root)
    started_at = capture.format_datetime(end)
    state = {
        "schema_version": 1,
        "run_id": f"onboard_{end.strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:10]}",
        "status": "awaiting_review",
        "started_at": started_at,
        "window": {"start": start_text, "end": end_text, "days": days},
        "source": {
            "adapter": str(source["adapter"]),
            "source_path": str(source["source_path"]),
            "boundary": str(source["boundary"]),
        },
        "import_summary": {
            key: int(imported[key])
            for key in (
                "input_rows",
                "user_rows",
                "imported_count",
                "duplicate_count",
                "rejected_secret_count",
                "ignored_non_user_count",
                "outside_window_count",
            )
        },
        "habits_report_id": report_document["report_id"],
        "habits_markdown_path": report_result["markdown_path"],
        "policy_id": policy_result["policy"]["policy_id"],
        "confirmed_at": None,
    }
    _write_state(state, root=selected_root)
    result = _public_status(state, root=selected_root)
    result.update(
        {
            "deduplicated": False,
            "import": imported,
            "policy": policy_result["policy"],
        }
    )
    return result


def confirm_first_learning(
    *, actor: str = "owner_via_local_ui", root: Path | None = None
) -> dict[str, Any]:
    selected_root = _root(root)
    document = _load_state(selected_root)
    if document is None:
        raise MemoryWorkspaceError("首次历史学习尚未运行。")
    if document["status"] == "completed":
        result = _public_status(document, root=selected_root)
        result["deduplicated"] = True
        return result

    active = policy.load_active_policy(root=selected_root)
    if active is None or active["policy_id"] != document["policy_id"]:
        policy.activate_policy(document["policy_id"], actor=actor, root=selected_root)
    habits.confirm_report(root=selected_root)
    completed = copy.deepcopy(document)
    completed["status"] = "completed"
    completed["confirmed_at"] = capture.format_datetime(capture.now_utc())
    _write_state(completed, root=selected_root)
    result = _public_status(completed, root=selected_root)
    result["deduplicated"] = False
    return result


def refine_habits_from_agent(
    path: Path, *, root: Path | None = None
) -> dict[str, Any]:
    """Replace only the tentative projection after bounded semantic review."""

    selected_root = _root(root)
    state = _load_state(selected_root)
    if state is None:
        raise MemoryWorkspaceError("首次历史学习尚未运行。")
    if state["status"] != "awaiting_review":
        raise MemoryWorkspaceError("已确认的 Query habits 不得被静默替换。")
    current = habits.load_report(root=selected_root)
    if current is None:
        raise MemoryWorkspaceError("首次学习的 Query habits 草稿缺失。")
    document = habits.load_agent_semantic_report(
        path,
        root=selected_root,
        expected_coverage=current["coverage"],
    )
    written = habits.write_report(document, root=selected_root)
    updated = copy.deepcopy(state)
    updated["habits_report_id"] = document["report_id"]
    updated["habits_markdown_path"] = written["markdown_path"]
    _write_state(updated, root=selected_root)
    result = _public_status(updated, root=selected_root)
    result["deduplicated"] = False
    return result
