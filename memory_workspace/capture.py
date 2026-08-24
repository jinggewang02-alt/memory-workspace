"""Durable, review-first queue for asynchronous memory capture candidates."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from .io import (
    MemoryWorkspaceError,
    nearest_existing_parent,
    transient_reason,
    write_bytes_once,
)
from .schema import load_json_object, validate


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PACKAGE_ROOT / "schemas"
EVENT_SCHEMA = SCHEMA_DIR / "capture-event.schema.json"
RESOLUTION_SCHEMA = SCHEMA_DIR / "capture-resolution.schema.json"
DECISION_SCHEMA = SCHEMA_DIR / "capture-decision.schema.json"
APPLICATION_SCHEMA = SCHEMA_DIR / "capture-application.schema.json"

EVENT_ID_PATTERN = re.compile(r"^evt_[A-Za-z0-9_-]+$")
CANDIDATE_ID_PATTERN = re.compile(r"^cand_[A-Za-z0-9_-]+$")
RESOLUTION_DECISIONS = {"ignore", "session", "project", "profile"}
CANDIDATE_SCOPES = {"project", "profile"}
SENSITIVITY_LEVELS = {"normal", "sensitive"}
REVIEW_DECISIONS = {"approved", "rejected"}

# The queue is local plaintext staging, not a secret vault. Reject common secret
# shapes before any bytes are written. Exact personal identifiers such as a UID
# remain allowed but should be marked sensitive by the asynchronous reviewer.
SECRET_PATTERNS = (
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I), "private key"),
    (re.compile(r"\b(?:password|passwd|密码)\s*[:=：]\s*\S+", re.I), "password"),
    (
        re.compile(
            r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|cookie)\s*[:=：]\s*\S+",
            re.I,
        ),
        "credential",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "API key"),
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise MemoryWorkspaceError(f"时间格式无效：{value}") from exc
    if parsed.tzinfo is None:
        raise MemoryWorkspaceError(f"时间缺少时区：{value}")
    return parsed.astimezone(timezone.utc)


def capture_path_info() -> tuple[Path, str]:
    explicit = os.environ.get("MWORK_CAPTURE_DIR")
    if explicit:
        return Path(os.path.abspath(os.path.expanduser(explicit))), "MWORK_CAPTURE_DIR"
    home = Path.home()
    if str(home) in {"", "."}:
        raise MemoryWorkspaceError("无法解析用户目录；请用 MWORK_CAPTURE_DIR 指定持久目录。")
    return home / ".memory-workspace" / "capture", "default-home"


def capture_path() -> Path:
    return capture_path_info()[0]


def allow_transient() -> bool:
    return os.environ.get("MWORK_ALLOW_TRANSIENT") == "1"


def _paths(root: Path | None = None) -> dict[str, Path]:
    base = root or capture_path()
    return {
        "root": base,
        "events": base / "events",
        "resolutions": base / "resolutions",
        "decisions": base / "candidate-decisions",
        "applications": base / "applications",
    }


def _validate_identifier(value: str, pattern: re.Pattern[str], label: str) -> None:
    if pattern.fullmatch(value) is None:
        raise MemoryWorkspaceError(f"{label} 格式无效：{value}")


def _event_path(event_id: str, *, root: Path | None = None) -> Path:
    _validate_identifier(event_id, EVENT_ID_PATTERN, "event id")
    return _paths(root)["events"] / f"{event_id}.json"


def _resolution_path(event_id: str, *, root: Path | None = None) -> Path:
    _validate_identifier(event_id, EVENT_ID_PATTERN, "event id")
    return _paths(root)["resolutions"] / f"{event_id}.json"


def _candidate_id(event_id: str) -> str:
    _validate_identifier(event_id, EVENT_ID_PATTERN, "event id")
    return "cand_" + event_id.removeprefix("evt_")


def _event_id(candidate_id: str) -> str:
    _validate_identifier(candidate_id, CANDIDATE_ID_PATTERN, "candidate id")
    return "evt_" + candidate_id.removeprefix("cand_")


def _decision_path(candidate_id: str, *, root: Path | None = None) -> Path:
    _validate_identifier(candidate_id, CANDIDATE_ID_PATTERN, "candidate id")
    return _paths(root)["decisions"] / f"{candidate_id}.json"


def _application_path(candidate_id: str, *, root: Path | None = None) -> Path:
    _validate_identifier(candidate_id, CANDIDATE_ID_PATTERN, "candidate id")
    return _paths(root)["applications"] / f"{candidate_id}.json"


def _load_document(path: Path, schema_path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise MemoryWorkspaceError(f"未找到 {label}：{path.stem}")
    try:
        document = load_json_object(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MemoryWorkspaceError(f"无法读取 {label} {path}：{exc}") from exc
    errors = validate(document, load_json_object(schema_path))
    if errors:
        raise MemoryWorkspaceError(f"{label} 校验失败：" + "; ".join(errors))
    return document


def _write_document_once(path: Path, document: dict[str, Any], schema_path: Path, label: str) -> None:
    errors = validate(document, load_json_object(schema_path))
    if errors:
        raise MemoryWorkspaceError(f"{label} 校验失败：" + "; ".join(errors))
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    write_bytes_once(path, payload, allow_transient=allow_transient())


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _reject_secrets(*values: str | None) -> None:
    text = "\n".join(value for value in values if value)
    for pattern, label in SECRET_PATTERNS:
        if pattern.search(text):
            raise MemoryWorkspaceError(f"异步队列不保存 {label}；事件未入队。")


def _safe_preview(value: str, *, sensitive: bool = False, limit: int = 120) -> str:
    if sensitive:
        return "<sensitive candidate; use show after authorization>"
    preview = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "<redacted-email>",
        value,
    )
    preview = re.sub(r"(?<!\d)\d{6,}(?!\d)", "<redacted-number>", preview)
    return preview[:limit]


def enqueue_event(
    user_message: str,
    *,
    conversation_id: str | None = None,
    message_id: str | None = None,
    workspace_id: str | None = None,
    source_agent: str = "unknown",
    assistant_summary: str | None = None,
    retention_days: int = 7,
    root: Path | None = None,
) -> dict[str, Any]:
    """Persist one immutable event without running a model or editing canonical memory."""

    message = user_message.strip()
    if not message:
        raise MemoryWorkspaceError("user message 不能为空。")
    if retention_days < 1 or retention_days > 30:
        raise MemoryWorkspaceError("retention days 必须在 1–30 之间。")
    _reject_secrets(message, assistant_summary)

    created = now_utc()
    event_id = f"evt_{created.strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:12]}"
    document = {
        "schema_version": 1,
        "event_id": event_id,
        "created_at": format_datetime(created),
        "expires_at": format_datetime(created + timedelta(days=retention_days)),
        "source": {
            "agent": source_agent.strip() or "unknown",
            "conversation_id": _optional_text(conversation_id),
            "message_id": _optional_text(message_id),
            "workspace_id": _optional_text(workspace_id),
        },
        "payload": {
            "user_message": message,
            "assistant_summary": _optional_text(assistant_summary),
        },
        "privacy": "local_plaintext_staging",
    }
    path = _event_path(event_id, root=root)
    _write_document_once(path, document, EVENT_SCHEMA, "capture event")
    return {
        "event_id": event_id,
        "event_path": str(path),
        "created_at": document["created_at"],
        "expires_at": document["expires_at"],
        "status": "pending",
    }


def _event_status(event: dict[str, Any], *, root: Path | None = None) -> str:
    if _resolution_path(event["event_id"], root=root).is_file():
        return "resolved"
    if parse_datetime(event["expires_at"]) <= now_utc():
        return "expired"
    return "pending"


def list_events(
    *, status: str = "pending", limit: int = 50, root: Path | None = None
) -> list[dict[str, Any]]:
    if status not in {"pending", "resolved", "expired", "all"}:
        raise MemoryWorkspaceError("event status 必须是 pending/resolved/expired/all。")
    if limit < 1 or limit > 500:
        raise MemoryWorkspaceError("limit 必须在 1–500 之间。")
    events_dir = _paths(root)["events"]
    if not events_dir.exists():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(events_dir.glob("evt_*.json")):
        event = _load_document(path, EVENT_SCHEMA, "capture event")
        derived = _event_status(event, root=root)
        if status != "all" and derived != status:
            continue
        message = event["payload"]["user_message"]
        result.append(
            {
                "event_id": event["event_id"],
                "created_at": event["created_at"],
                "expires_at": event["expires_at"],
                "status": derived,
                "source": event["source"],
                "message_preview": _safe_preview(message),
                "message_length": len(message),
            }
        )
        if len(result) >= limit:
            break
    return result


def show_event(event_id: str, *, root: Path | None = None) -> dict[str, Any]:
    event = _load_document(_event_path(event_id, root=root), EVENT_SCHEMA, "capture event")
    resolution_path = _resolution_path(event_id, root=root)
    resolution = (
        _load_document(resolution_path, RESOLUTION_SCHEMA, "capture resolution")
        if resolution_path.is_file()
        else None
    )
    return {"event": event, "status": _event_status(event, root=root), "resolution": resolution}


def resolve_event(
    event_id: str,
    *,
    decision: str,
    reason: str,
    resolved_by: str = "async_worker",
    content: str | None = None,
    confidence: float | None = None,
    sensitivity: str = "normal",
    workspace_id: str | None = None,
    profile_key: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Resolve an event once. Project/profile decisions create review candidates only."""

    event = _load_document(_event_path(event_id, root=root), EVENT_SCHEMA, "capture event")
    if parse_datetime(event["expires_at"]) <= now_utc():
        raise MemoryWorkspaceError(f"event 已过期，拒绝生成候选：{event_id}")
    if decision not in RESOLUTION_DECISIONS:
        raise MemoryWorkspaceError("decision 必须是 ignore/session/project/profile。")
    clean_reason = reason.strip()
    if not clean_reason:
        raise MemoryWorkspaceError("reason 不能为空。")
    resolution_path = _resolution_path(event_id, root=root)
    if resolution_path.exists():
        raise MemoryWorkspaceError(f"event 已被处理，拒绝重复解析：{event_id}")

    candidate: dict[str, Any] | None = None
    if decision in CANDIDATE_SCOPES:
        clean_content = (content or "").strip()
        if not clean_content:
            raise MemoryWorkspaceError("project/profile candidate 必须提供 content。")
        if confidence is None or not 0 <= confidence <= 1:
            raise MemoryWorkspaceError("confidence 必须是 0–1 之间的数字。")
        if sensitivity not in SENSITIVITY_LEVELS:
            raise MemoryWorkspaceError("sensitivity 必须是 normal 或 sensitive。")
        _reject_secrets(clean_content)
        candidate = {
            "candidate_id": _candidate_id(event_id),
            "scope": decision,
            "content": clean_content,
            "confidence": confidence,
            "sensitivity": sensitivity,
            "proposed_at": format_datetime(now_utc()),
            "proposed_by": resolved_by.strip() or "async_worker",
            "target_hint": {
                "workspace_id": _optional_text(workspace_id),
                "profile_key": _optional_text(profile_key),
            },
        }
    elif any(value is not None for value in (content, confidence, workspace_id, profile_key)):
        raise MemoryWorkspaceError("ignore/session 解析不得携带 candidate 字段。")

    document = {
        "schema_version": 1,
        "event_id": event_id,
        "resolved_at": format_datetime(now_utc()),
        "resolved_by": resolved_by.strip() or "async_worker",
        "decision": decision,
        "reason": clean_reason,
        "candidate": candidate,
    }
    _write_document_once(resolution_path, document, RESOLUTION_SCHEMA, "capture resolution")
    return {
        "event_id": event_id,
        "decision": decision,
        "candidate_id": candidate["candidate_id"] if candidate else None,
        "resolution_path": str(resolution_path),
    }


def _candidate_bundle(candidate_id: str, *, root: Path | None = None) -> dict[str, Any]:
    event_id = _event_id(candidate_id)
    resolution = _load_document(
        _resolution_path(event_id, root=root), RESOLUTION_SCHEMA, "capture resolution"
    )
    candidate = resolution.get("candidate")
    if not isinstance(candidate, dict) or candidate.get("candidate_id") != candidate_id:
        raise MemoryWorkspaceError(f"未找到 candidate：{candidate_id}")
    decision_path = _decision_path(candidate_id, root=root)
    application_path = _application_path(candidate_id, root=root)
    review = (
        _load_document(decision_path, DECISION_SCHEMA, "candidate decision")
        if decision_path.is_file()
        else None
    )
    application = (
        _load_document(application_path, APPLICATION_SCHEMA, "candidate application")
        if application_path.is_file()
        else None
    )
    if application is not None:
        status = "applied"
    elif review is not None:
        status = review["decision"]
    else:
        status = "proposed"
    return {
        "candidate": candidate,
        "status": status,
        "resolution": resolution,
        "review": review,
        "application": application,
    }


def list_candidates(
    *, status: str = "proposed", limit: int = 50, root: Path | None = None
) -> list[dict[str, Any]]:
    if status not in {"proposed", "approved", "rejected", "applied", "all"}:
        raise MemoryWorkspaceError("candidate status 必须是 proposed/approved/rejected/applied/all。")
    if limit < 1 or limit > 500:
        raise MemoryWorkspaceError("limit 必须在 1–500 之间。")
    directory = _paths(root)["resolutions"]
    if not directory.exists():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(directory.glob("evt_*.json")):
        resolution = _load_document(path, RESOLUTION_SCHEMA, "capture resolution")
        candidate = resolution.get("candidate")
        if not isinstance(candidate, dict):
            continue
        bundle = _candidate_bundle(candidate["candidate_id"], root=root)
        if status != "all" and bundle["status"] != status:
            continue
        result.append(
            {
                "candidate_id": candidate["candidate_id"],
                "scope": candidate["scope"],
                "content_preview": _safe_preview(
                    candidate["content"],
                    sensitive=candidate["sensitivity"] == "sensitive",
                    limit=160,
                ),
                "confidence": candidate["confidence"],
                "sensitivity": candidate["sensitivity"],
                "proposed_at": candidate["proposed_at"],
                "proposed_by": candidate["proposed_by"],
                "target_hint": candidate["target_hint"],
                "event_id": resolution["event_id"],
                "status": bundle["status"],
                "reason": resolution["reason"],
            }
        )
        if len(result) >= limit:
            break
    return result


def show_candidate(candidate_id: str, *, root: Path | None = None) -> dict[str, Any]:
    return _candidate_bundle(candidate_id, root=root)


def decide_candidate(
    candidate_id: str,
    *,
    decision: str,
    actor: str,
    target_ref: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    bundle = _candidate_bundle(candidate_id, root=root)
    if bundle["review"] is not None:
        raise MemoryWorkspaceError(f"candidate 已经完成审阅：{candidate_id}")
    if decision not in REVIEW_DECISIONS:
        raise MemoryWorkspaceError("review decision 必须是 approved 或 rejected。")
    clean_actor = actor.strip()
    if not clean_actor:
        raise MemoryWorkspaceError("actor 不能为空。")
    clean_target = _optional_text(target_ref)
    if decision == "approved" and clean_target is None:
        raise MemoryWorkspaceError("批准 candidate 时必须明确 target-ref。")
    if decision == "approved":
        _validate_approval_target(bundle["candidate"], clean_target)
    document = {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "decision": decision,
        "decided_at": format_datetime(now_utc()),
        "actor": clean_actor,
        "target_ref": clean_target,
    }
    path = _decision_path(candidate_id, root=root)
    _write_document_once(path, document, DECISION_SCHEMA, "candidate decision")
    return {"candidate_id": candidate_id, "status": decision, "decision_path": str(path)}


def _validate_approval_target(candidate: dict[str, Any], target_ref: str) -> None:
    if candidate["scope"] == "project":
        match = re.fullmatch(r"workspace:([a-z0-9]+(?:-[a-z0-9]+)*)/(.+)", target_ref)
        if match is None:
            raise MemoryWorkspaceError(
                "project candidate 的 target-ref 必须是 workspace:<id>/<relative-path>。"
            )
        workspace_id, relative_value = match.groups()
        relative = PurePosixPath(relative_value)
        if relative.is_absolute() or ".." in relative.parts:
            raise MemoryWorkspaceError("project target-ref 不能是绝对路径或包含 '..'。")
        hinted = candidate["target_hint"].get("workspace_id")
        if hinted is not None and hinted != workspace_id:
            raise MemoryWorkspaceError(
                f"target workspace 与 Candidate hint 不一致：{workspace_id} != {hinted}"
            )
        from .workspace import load_manifest

        load_manifest(workspace_id)
        return

    match = re.fullmatch(r"profile:(.+)", target_ref)
    if match is None or not match.group(1).strip():
        raise MemoryWorkspaceError("profile candidate 的 target-ref 必须是 profile:<key>。")
    profile_key = match.group(1).strip()
    hinted = candidate["target_hint"].get("profile_key")
    if hinted is not None and hinted != profile_key:
        raise MemoryWorkspaceError(
            f"target profile key 与 Candidate hint 不一致：{profile_key} != {hinted}"
        )


def mark_candidate_applied(
    candidate_id: str,
    *,
    actor: str,
    verification: str,
    root: Path | None = None,
) -> dict[str, Any]:
    bundle = _candidate_bundle(candidate_id, root=root)
    review = bundle["review"]
    if not isinstance(review, dict) or review.get("decision") != "approved":
        raise MemoryWorkspaceError("candidate 必须先由用户批准，才能标记为 applied。")
    if bundle["application"] is not None:
        raise MemoryWorkspaceError(f"candidate 已标记 applied：{candidate_id}")
    clean_actor = actor.strip()
    clean_verification = verification.strip()
    if not clean_actor or not clean_verification:
        raise MemoryWorkspaceError("actor 和 verification 不能为空。")
    document = {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "applied_at": format_datetime(now_utc()),
        "actor": clean_actor,
        "target_ref": review["target_ref"],
        "verification": clean_verification,
    }
    path = _application_path(candidate_id, root=root)
    _write_document_once(path, document, APPLICATION_SCHEMA, "candidate application")
    return {"candidate_id": candidate_id, "status": "applied", "application_path": str(path)}


def cleanup_expired(*, apply: bool = False, root: Path | None = None) -> dict[str, Any]:
    """Delete only expired raw event payloads; keep review/audit documents."""

    events_dir = _paths(root)["events"]
    expired: list[Path] = []
    if events_dir.exists():
        for path in sorted(events_dir.glob("evt_*.json")):
            event = _load_document(path, EVENT_SCHEMA, "capture event")
            if parse_datetime(event["expires_at"]) <= now_utc():
                expired.append(path)
    removed: list[str] = []
    if apply:
        for path in expired:
            try:
                path.unlink()
            except OSError as exc:
                raise MemoryWorkspaceError(f"无法清理过期事件 {path}：{exc}") from exc
            removed.append(str(path))
    return {
        "dry_run": not apply,
        "expired_count": len(expired),
        "removed_count": len(removed),
        "event_ids": [path.stem for path in expired],
    }


def doctor(*, root: Path | None = None) -> dict[str, Any]:
    directory, source = capture_path_info() if root is None else (root, "argument")
    reason = transient_reason(directory)
    parent = nearest_existing_parent(directory)
    writable = os.access(parent, os.W_OK)
    warnings: list[str] = []
    if reason:
        if allow_transient():
            warnings.append(reason + "；测试 override 已启用，禁止用于正式数据。")
        else:
            warnings.append(reason + "；正式入队将被拒绝。")
    if not writable:
        warnings.append(f"现有父目录不可写：{parent}")
    return {
        "capture_root": str(directory),
        "source": source,
        "exists": directory.exists(),
        "nearest_existing_parent": str(parent),
        "filesystem_writable": writable,
        "transient_risk": reason is not None,
        "status": "WARNING" if warnings else "OK",
        "warnings": warnings,
        "note": "队列是本机明文暂存；默认关闭，开启后应定期处理和清理过期事件。",
    }
