"""Durable, review-first queue for asynchronous memory capture candidates."""

from __future__ import annotations

import json
import hashlib
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
CANDIDATE_SCHEMA = SCHEMA_DIR / "capture-candidate.schema.json"

EVENT_ID_PATTERN = re.compile(r"^evt_[A-Za-z0-9_-]+$")
CANDIDATE_ID_PATTERN = re.compile(r"^cand_[A-Za-z0-9_-]+$")
RESOLUTION_DECISIONS = {"ignore", "session", "project", "profile"}
CANDIDATE_SCOPES = {"project", "profile"}
SENSITIVITY_LEVELS = {"normal", "sensitive"}
REVIEW_DECISIONS = {"approved", "rejected"}
CANDIDATE_KINDS = {"fact", "preference", "decision", "learning"}
SOURCE_KINDS = {"live", "history_import"}
DIRECT_ROUTES = {"none", "workspace", "remember", "recall"}

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
        "candidates": base / "candidates",
        "decisions": base / "candidate-decisions",
        "applications": base / "applications",
        "feedback": base / "feedback",
        "policies": base / "policies",
        "policy_activations": base / "policy-activations",
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


def _legacy_candidate_id(event_id: str) -> str:
    _validate_identifier(event_id, EVENT_ID_PATTERN, "event id")
    return "cand_" + event_id.removeprefix("evt_")


def _event_id(candidate_id: str) -> str:
    _validate_identifier(candidate_id, CANDIDATE_ID_PATTERN, "candidate id")
    return "evt_" + candidate_id.removeprefix("cand_")


def _candidate_path(candidate_id: str, *, root: Path | None = None) -> Path:
    _validate_identifier(candidate_id, CANDIDATE_ID_PATTERN, "candidate id")
    return _paths(root)["candidates"] / f"{candidate_id}.json"


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


def _occurred_at(value: str | datetime | None, *, fallback: datetime) -> str:
    if value is None:
        return format_datetime(fallback)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise MemoryWorkspaceError("occurred_at 缺少时区。")
        return format_datetime(value)
    return format_datetime(parse_datetime(value))


def _dedupe_key(
    *,
    source_adapter: str,
    conversation_id: str | None,
    message_id: str | None,
    occurred_at: str,
    user_message: str,
) -> str:
    value = "\n".join(
        (source_adapter, conversation_id or "", message_id or "", occurred_at, user_message)
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def all_event_documents(*, root: Path | None = None) -> list[dict[str, Any]]:
    directory = _paths(root)["events"]
    if not directory.exists():
        return []
    documents = [
        _load_document(path, EVENT_SCHEMA, "capture event")
        for path in sorted(directory.glob("evt_*.json"))
    ]
    return sorted(
        documents,
        key=lambda event: (
            event.get("occurred_at", event["created_at"]),
            event["event_id"],
        ),
    )


def _candidate_fingerprint(
    *, scope: str, kind: str, content: str, workspace_id: str | None, profile_key: str | None
) -> str:
    normalized = re.sub(r"\s+", " ", content).strip().casefold()
    value = "\n".join((scope, kind, workspace_id or "", profile_key or "", normalized))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _find_candidate_by_fingerprint(
    fingerprint: str, *, root: Path | None = None
) -> dict[str, Any] | None:
    directory = _paths(root)["candidates"]
    if not directory.exists():
        return None
    for path in sorted(directory.glob("cand_*.json")):
        candidate = _load_document(path, CANDIDATE_SCHEMA, "capture candidate")
        if candidate["fingerprint"] == fingerprint:
            return candidate
    return None


def enqueue_event(
    user_message: str,
    *,
    conversation_id: str | None = None,
    message_id: str | None = None,
    workspace_id: str | None = None,
    source_agent: str = "unknown",
    source_adapter: str = "generic-hook",
    source_kind: str = "live",
    occurred_at: str | datetime | None = None,
    direct_route: str = "none",
    capture_eligible: bool = True,
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
    if source_kind not in SOURCE_KINDS:
        raise MemoryWorkspaceError("source kind 必须是 live 或 history_import。")
    if direct_route not in DIRECT_ROUTES:
        raise MemoryWorkspaceError("direct route 必须是 none/workspace/remember/recall。")
    _reject_secrets(message, assistant_summary)

    created = now_utc()
    occurred = _occurred_at(occurred_at, fallback=created)
    clean_adapter = source_adapter.strip() or "generic-hook"
    clean_conversation = _optional_text(conversation_id)
    clean_message_id = _optional_text(message_id)
    dedupe_key = _dedupe_key(
        source_adapter=clean_adapter,
        conversation_id=clean_conversation,
        message_id=clean_message_id,
        occurred_at=occurred,
        user_message=message,
    )
    # The content-derived ID keeps the synchronous Hook path O(1) as the queue
    # grows. It also lets concurrent adapters converge on the same immutable
    # event without maintaining a mutable index.
    event_id = f"evt_{dedupe_key}"
    path = _event_path(event_id, root=root)
    if path.is_file():
        existing = _load_document(path, EVENT_SCHEMA, "capture event")
        return {
            "event_id": existing["event_id"],
            "event_path": str(path),
            "created_at": existing["created_at"],
            "expires_at": existing["expires_at"],
            "status": _event_status(existing, root=root),
            "deduplicated": True,
        }
    document = {
        "schema_version": 2,
        "event_id": event_id,
        "created_at": format_datetime(created),
        "occurred_at": occurred,
        "expires_at": format_datetime(created + timedelta(days=retention_days)),
        "source_kind": source_kind,
        "source": {
            "agent": source_agent.strip() or "unknown",
            "adapter": clean_adapter,
            "conversation_id": clean_conversation,
            "message_id": clean_message_id,
            "workspace_id": _optional_text(workspace_id),
            "dedupe_key": dedupe_key,
        },
        "routing": {
            "direct_route": direct_route,
            "capture_eligible": bool(capture_eligible),
        },
        "payload": {
            "user_message": message,
            "assistant_summary": _optional_text(assistant_summary),
        },
        "privacy": "local_plaintext_staging",
    }
    try:
        _write_document_once(path, document, EVENT_SCHEMA, "capture event")
    except MemoryWorkspaceError:
        # A concurrent writer may have won the exclusive create after our
        # existence check. Return its validated event instead of failing the
        # user's Query; other I/O errors still propagate.
        if not path.is_file():
            raise
        existing = _load_document(path, EVENT_SCHEMA, "capture event")
        if existing.get("source", {}).get("dedupe_key") != dedupe_key:
            raise
        return {
            "event_id": existing["event_id"],
            "event_path": str(path),
            "created_at": existing["created_at"],
            "expires_at": existing["expires_at"],
            "status": _event_status(existing, root=root),
            "deduplicated": True,
        }
    return {
        "event_id": event_id,
        "event_path": str(path),
        "created_at": document["created_at"],
        "expires_at": document["expires_at"],
        "status": "pending",
        "deduplicated": False,
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
    result: list[dict[str, Any]] = []
    for event in all_event_documents(root=root):
        derived = _event_status(event, root=root)
        if status != "all" and derived != status:
            continue
        message = event["payload"]["user_message"]
        result.append(
            {
                "event_id": event["event_id"],
                "created_at": event["created_at"],
                "occurred_at": event.get("occurred_at", event["created_at"]),
                "expires_at": event["expires_at"],
                "status": derived,
                "source_kind": event.get("source_kind", "live"),
                "source": event["source"],
                "routing": event.get(
                    "routing", {"direct_route": "none", "capture_eligible": True}
                ),
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
    kind: str | None = None,
    episode_id: str | None = None,
    evidence_event_ids: list[str] | None = None,
    policy_version: str | None = None,
    policy_rule: str | None = None,
    trigger_phase: str | None = None,
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

    evidence_ids = list(dict.fromkeys(evidence_event_ids or [event_id]))
    if event_id not in evidence_ids:
        evidence_ids.append(event_id)
    for evidence_id in evidence_ids:
        _load_document(_event_path(evidence_id, root=root), EVENT_SCHEMA, "capture event")

    candidate_id: str | None = None
    deduplicated = False
    if decision in CANDIDATE_SCOPES:
        if event.get("schema_version") == 2 and not event["routing"]["capture_eligible"]:
            raise MemoryWorkspaceError("该事件已走明确写入/召回路径，不得重复生成 Candidate。")
        clean_content = (content or "").strip()
        if not clean_content:
            raise MemoryWorkspaceError("project/profile candidate 必须提供 content。")
        if confidence is None or not 0 <= confidence <= 1:
            raise MemoryWorkspaceError("confidence 必须是 0–1 之间的数字。")
        if sensitivity not in SENSITIVITY_LEVELS:
            raise MemoryWorkspaceError("sensitivity 必须是 normal 或 sensitive。")
        clean_kind = kind or ("decision" if decision == "project" else "fact")
        if clean_kind not in CANDIDATE_KINDS:
            raise MemoryWorkspaceError("candidate kind 必须是 fact/preference/decision/learning。")
        _reject_secrets(clean_content)
        fingerprint = _candidate_fingerprint(
            scope=decision,
            kind=clean_kind,
            content=clean_content,
            workspace_id=_optional_text(workspace_id),
            profile_key=_optional_text(profile_key),
        )
        existing = _find_candidate_by_fingerprint(fingerprint, root=root)
        if existing is not None:
            candidate_id = existing["candidate_id"]
            deduplicated = True
        else:
            proposed = now_utc()
            candidate_id = f"cand_{proposed.strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:12]}"
            candidate = {
                "schema_version": 1,
                "candidate_id": candidate_id,
                "fingerprint": fingerprint,
                "trigger_event_id": event_id,
                "episode_id": _optional_text(episode_id),
                "evidence_event_ids": evidence_ids,
                "scope": decision,
                "kind": clean_kind,
                "content": clean_content,
                "confidence": confidence,
                "sensitivity": sensitivity,
                "proposed_at": format_datetime(proposed),
                "proposed_by": resolved_by.strip() or "async_worker",
                "policy_version": _optional_text(policy_version),
                "policy_rule": _optional_text(policy_rule),
                "trigger_phase": _optional_text(trigger_phase),
                "target_hint": {
                    "workspace_id": _optional_text(workspace_id),
                    "profile_key": _optional_text(profile_key),
                },
            }
            _write_document_once(
                _candidate_path(candidate_id, root=root),
                candidate,
                CANDIDATE_SCHEMA,
                "capture candidate",
            )
    elif any(value is not None for value in (content, confidence, workspace_id, profile_key, kind)):
        raise MemoryWorkspaceError("ignore/session 解析不得携带 candidate 字段。")

    document = {
        "schema_version": 2,
        "event_id": event_id,
        "episode_id": _optional_text(episode_id),
        "evidence_event_ids": evidence_ids,
        "resolved_at": format_datetime(now_utc()),
        "resolved_by": resolved_by.strip() or "async_worker",
        "decision": decision,
        "reason": clean_reason,
        "policy_version": _optional_text(policy_version),
        "trigger_phase": _optional_text(trigger_phase),
        "candidate_id": candidate_id,
        "deduplicated": deduplicated,
    }
    _write_document_once(resolution_path, document, RESOLUTION_SCHEMA, "capture resolution")
    return {
        "event_id": event_id,
        "decision": decision,
        "candidate_id": candidate_id,
        "deduplicated": deduplicated,
        "resolution_path": str(resolution_path),
    }


def _candidate_bundle(candidate_id: str, *, root: Path | None = None) -> dict[str, Any]:
    candidate_path = _candidate_path(candidate_id, root=root)
    if candidate_path.is_file():
        candidate = _load_document(candidate_path, CANDIDATE_SCHEMA, "capture candidate")
        resolution = _load_document(
            _resolution_path(candidate["trigger_event_id"], root=root),
            RESOLUTION_SCHEMA,
            "capture resolution",
        )
    else:
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
    result: list[dict[str, Any]] = []
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    candidate_directory = _paths(root)["candidates"]
    if candidate_directory.exists():
        for path in sorted(candidate_directory.glob("cand_*.json")):
            candidate = _load_document(path, CANDIDATE_SCHEMA, "capture candidate")
            resolution = _load_document(
                _resolution_path(candidate["trigger_event_id"], root=root),
                RESOLUTION_SCHEMA,
                "capture resolution",
            )
            candidates.append((candidate, resolution))
    legacy_directory = _paths(root)["resolutions"]
    if legacy_directory.exists():
        for path in sorted(legacy_directory.glob("evt_*.json")):
            resolution = _load_document(path, RESOLUTION_SCHEMA, "capture resolution")
            candidate = resolution.get("candidate")
            if isinstance(candidate, dict):
                candidates.append((candidate, resolution))

    for candidate, resolution in candidates:
        bundle = _candidate_bundle(candidate["candidate_id"], root=root)
        if status != "all" and bundle["status"] != status:
            continue
        result.append(
            {
                "candidate_id": candidate["candidate_id"],
                "scope": candidate["scope"],
                "kind": candidate.get("kind", "fact" if candidate["scope"] == "profile" else "decision"),
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
                "episode_id": candidate.get("episode_id"),
                "evidence_count": len(candidate.get("evidence_event_ids", [resolution["event_id"]])),
                "policy_version": candidate.get("policy_version"),
                "policy_rule": candidate.get("policy_rule"),
                "trigger_phase": candidate.get("trigger_phase"),
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
    edited_content: str | None = None,
    feedback_reason: str | None = None,
    suppress_similar: bool = False,
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
    clean_edited = _optional_text(edited_content)
    if decision == "approved" and clean_target is None:
        raise MemoryWorkspaceError("批准 candidate 时必须明确 target-ref。")
    if decision == "rejected" and clean_edited is not None:
        raise MemoryWorkspaceError("拒绝 candidate 时不得提供 edited-content。")
    if decision == "approved":
        _validate_approval_target(bundle["candidate"], clean_target)
        _reject_secrets(clean_edited)
    document = {
        "schema_version": 2,
        "candidate_id": candidate_id,
        "decision": decision,
        "decided_at": format_datetime(now_utc()),
        "actor": clean_actor,
        "target_ref": clean_target,
        "approved_content": (
            (clean_edited or bundle["candidate"]["content"])
            if decision == "approved"
            else None
        ),
    }
    path = _decision_path(candidate_id, root=root)
    _write_document_once(path, document, DECISION_SCHEMA, "candidate decision")
    from .feedback import record_feedback

    feedback_paths = []
    recorded = record_feedback(
        action=decision,
        actor=clean_actor,
        candidate_id=candidate_id,
        fingerprint=bundle["candidate"].get("fingerprint"),
        reason_code=feedback_reason,
        root=root,
    )
    feedback_paths.append(recorded["feedback_path"])
    if clean_edited is not None:
        edited = record_feedback(
            action="edited",
            actor=clean_actor,
            candidate_id=candidate_id,
            fingerprint=bundle["candidate"].get("fingerprint"),
            reason_code=feedback_reason or "owner_edited_candidate",
            root=root,
        )
        feedback_paths.append(edited["feedback_path"])
    if suppress_similar:
        suppressed = record_feedback(
            action="suppress_similar",
            actor=clean_actor,
            candidate_id=candidate_id,
            fingerprint=bundle["candidate"].get("fingerprint"),
            reason_code=feedback_reason or "owner_suppressed_similar",
            root=root,
        )
        feedback_paths.append(suppressed["feedback_path"])
    return {
        "candidate_id": candidate_id,
        "status": decision,
        "decision_path": str(path),
        "feedback_paths": feedback_paths,
    }


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
