"""Append-only user feedback for adaptive capture policies."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

from .capture import (
    _reject_secrets,
    allow_transient,
    capture_path,
    format_datetime,
    now_utc,
    show_candidate,
    show_event,
)
from .io import MemoryWorkspaceError, write_bytes_once
from .schema import load_json_object, validate


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FEEDBACK_SCHEMA = PACKAGE_ROOT / "schemas" / "capture-feedback.schema.json"
ACTIONS = {
    "approved",
    "rejected",
    "edited",
    "suppress_similar",
    "explicit_save_followup",
    "recalled",
    "forgotten_complaint",
}
ID_RE = re.compile(r"^(?:cand|evt)_[A-Za-z0-9_-]+$")


def _root(root: Path | None) -> Path:
    return root or capture_path()


def record_feedback(
    *,
    action: str,
    actor: str,
    candidate_id: str | None = None,
    event_id: str | None = None,
    fingerprint: str | None = None,
    reason_code: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    if action not in ACTIONS:
        raise MemoryWorkspaceError(f"feedback action 无效：{action}")
    clean_actor = actor.strip()
    if not clean_actor:
        raise MemoryWorkspaceError("actor 不能为空。")
    if candidate_id is None and event_id is None:
        raise MemoryWorkspaceError("feedback 必须关联 candidate-id 或 event-id。")
    for value, label in ((candidate_id, "candidate id"), (event_id, "event id")):
        if value is not None and ID_RE.fullmatch(value) is None:
            raise MemoryWorkspaceError(f"{label} 格式无效：{value}")
    if candidate_id is not None:
        show_candidate(candidate_id, root=root)
    if event_id is not None:
        show_event(event_id, root=root)
    if fingerprint is not None and re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None:
        raise MemoryWorkspaceError("fingerprint 必须是 64 位十六进制 SHA-256。")
    clean_reason = reason_code.strip() if reason_code else None
    _reject_secrets(clean_reason)
    created = now_utc()
    feedback_id = f"fb_{created.strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:12]}"
    document = {
        "schema_version": 1,
        "feedback_id": feedback_id,
        "recorded_at": format_datetime(created),
        "actor": clean_actor,
        "action": action,
        "candidate_id": candidate_id,
        "event_id": event_id,
        "fingerprint": fingerprint,
        "reason_code": clean_reason,
    }
    errors = validate(document, load_json_object(FEEDBACK_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("capture feedback 校验失败：" + "; ".join(errors))
    path = _root(root) / "feedback" / f"{feedback_id}.json"
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
    write_bytes_once(path, payload, allow_transient=allow_transient())
    return {"feedback": document, "feedback_path": str(path)}


def list_feedback(*, root: Path | None = None) -> list[dict[str, Any]]:
    directory = _root(root) / "feedback"
    if not directory.exists():
        return []
    result = []
    for path in sorted(directory.glob("fb_*.json")):
        document = load_json_object(path)
        errors = validate(document, load_json_object(FEEDBACK_SCHEMA))
        if errors:
            raise MemoryWorkspaceError("capture feedback 校验失败：" + "; ".join(errors))
        result.append(document)
    return result


def summarize_feedback(*, root: Path | None = None) -> dict[str, int]:
    return dict(sorted(Counter(item["action"] for item in list_feedback(root=root)).items()))
