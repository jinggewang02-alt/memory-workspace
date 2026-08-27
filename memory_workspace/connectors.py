"""Explicit, provider-gated connector configuration and sync planning.

This module never invokes an external CLI.  It only records an owner's explicit
connector choice, decides whether a bounded read is due, and returns a reviewable
read plan.  A host must execute the plan and record a checkpoint separately.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from . import workspace as workspace_store
from .io import MemoryWorkspaceError, atomic_write_json, is_within
from .schema import load_json_object, validate


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONNECTOR_SCHEMA = PACKAGE_ROOT / "schemas" / "connector-config.schema.json"
CHECKPOINT_SCHEMA = PACKAGE_ROOT / "schemas" / "sync-checkpoint.schema.json"
SUPPORTED_PROVIDERS = {"lark"}
LARK_CONNECTOR_ID = "lark"
LARK_REQUIRED_SCOPES = (
    "im:chat:read",
    "im:message:readonly",
    "search:docs:read",
    "drive:drive.metadata:readonly",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise MemoryWorkspaceError(f"时间格式无效：{value}") from exc
    if parsed.tzinfo is None:
        raise MemoryWorkspaceError(f"时间缺少时区：{value}")
    return parsed.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _format_lark_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _connector_path(workspace: Path, provider: str) -> Path:
    return workspace / "config" / "connectors" / f"{provider}.json"


def _checkpoint_path(workspace: Path, provider: str) -> Path:
    return workspace / ".llm-wiki" / "connectors" / provider / "checkpoint.json"


def _validate_document(
    document: dict[str, Any], schema_path: Path, *, label: str
) -> None:
    errors = validate(document, load_json_object(schema_path))
    if errors:
        raise MemoryWorkspaceError(f"{label} 校验失败：" + "; ".join(errors))


def _load_document(path: Path, schema_path: Path, *, label: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        document = load_json_object(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MemoryWorkspaceError(f"无法读取 {label} {path}：{exc}") from exc
    _validate_document(document, schema_path, label=label)
    return document


def _validate_provider(provider: str) -> str:
    clean = provider.strip().lower()
    if clean not in SUPPORTED_PROVIDERS:
        raise MemoryWorkspaceError(
            f"尚未实现 connector provider：{provider}；当前支持：{', '.join(sorted(SUPPORTED_PROVIDERS))}"
        )
    return clean


def _validate_lark_limits(
    *,
    lookback_days: int,
    max_active_chats: int,
    initial_messages_per_chat: int,
    expanded_messages_per_chat: int,
    minimum_interval_hours: int,
) -> None:
    if not 1 <= lookback_days <= 90:
        raise MemoryWorkspaceError("lookback_days 必须在 1 到 90 之间。")
    if not 1 <= max_active_chats <= 100:
        raise MemoryWorkspaceError("max_active_chats 必须在 1 到 100 之间。")
    if not 1 <= initial_messages_per_chat <= 50:
        raise MemoryWorkspaceError("initial_messages_per_chat 必须在 1 到 50 之间。")
    if not initial_messages_per_chat <= expanded_messages_per_chat <= 50:
        raise MemoryWorkspaceError(
            "expanded_messages_per_chat 必须不小于初始样本且不超过 50。"
        )
    if not 1 <= minimum_interval_hours <= 168:
        raise MemoryWorkspaceError("minimum_interval_hours 必须在 1 到 168 之间。")


def connector_status(
    workspace_id: str,
    *,
    provider: str = "lark",
    root: Path | None = None,
) -> dict[str, Any]:
    """Return connector state without probing the provider or its CLI."""

    clean_provider = _validate_provider(provider)
    workspace, _ = workspace_store.load_manifest(workspace_id, root=root)
    config_path = _connector_path(workspace, clean_provider)
    config = _load_document(
        config_path, CONNECTOR_SCHEMA, label=f"{clean_provider} connector config"
    )
    checkpoint = _load_document(
        _checkpoint_path(workspace, clean_provider),
        CHECKPOINT_SCHEMA,
        label=f"{clean_provider} sync checkpoint",
    )
    if config is None:
        return {
            "workspace_id": workspace_id,
            "provider": clean_provider,
            "configured": False,
            "enabled": False,
            "activation_required": True,
            "lark_cli_required": False,
            "config_path": str(config_path),
            "checkpoint": None,
            "boundary": (
                "Connector 未由用户显式启用；不得探测 lark-cli、请求飞书权限或读取飞书数据。"
            ),
        }
    return {
        "workspace_id": workspace_id,
        "provider": clean_provider,
        "configured": True,
        "enabled": bool(config["enabled"]),
        "activation_required": not bool(config["enabled"]),
        "lark_cli_required": False,
        "config_path": str(config_path),
        "config": config,
        "checkpoint": checkpoint,
        "boundary": (
            "Connector 已启用；只有 plan 返回 due 后，宿主才能按计划执行只读 lark-cli 命令。"
            if config["enabled"]
            else "Connector 已停用；不得执行任何飞书读取。"
        ),
    }


def enable_lark_connector(
    workspace_id: str,
    *,
    actor: str = "owner_via_cli",
    lookback_days: int = 30,
    max_active_chats: int = 30,
    initial_messages_per_chat: int = 20,
    expanded_messages_per_chat: int = 50,
    minimum_interval_hours: int = 24,
    event_acceleration: bool = False,
    root: Path | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Explicitly enable the Lark-specific baseline and incremental planner."""

    if not actor.strip():
        raise MemoryWorkspaceError("actor 不能为空。")
    _validate_lark_limits(
        lookback_days=lookback_days,
        max_active_chats=max_active_chats,
        initial_messages_per_chat=initial_messages_per_chat,
        expanded_messages_per_chat=expanded_messages_per_chat,
        minimum_interval_hours=minimum_interval_hours,
    )
    workspace, _ = workspace_store.load_manifest(workspace_id, root=root)
    path = _connector_path(workspace, LARK_CONNECTOR_ID)
    existing = _load_document(path, CONNECTOR_SCHEMA, label="lark connector config")
    timestamp = _parse_datetime(now or _now())
    activated_at = (
        existing["activation"]["activated_at"]
        if existing and existing.get("enabled")
        else _format_datetime(timestamp)
    )
    capabilities = [
        "chat_metadata",
        "message_history",
        "drive_search",
        "drive_metadata",
    ]
    if event_acceleration:
        capabilities.append("event_acceleration")
    document = {
        "schema_version": 1,
        "connector_id": LARK_CONNECTOR_ID,
        "provider": "lark",
        "workspace_id": workspace_id,
        "enabled": True,
        "identity_mode": "user",
        "activation": {
            "explicit": True,
            "actor": actor.strip(),
            "activated_at": activated_at,
            "deactivated_at": None,
        },
        "capabilities": capabilities,
        "baseline": {
            "lookback_days": lookback_days,
            "max_active_chats": max_active_chats,
            "initial_messages_per_chat": initial_messages_per_chat,
            "expanded_messages_per_chat": expanded_messages_per_chat,
        },
        "schedule": {
            "trigger": "agent_start_or_idle",
            "minimum_interval_hours": minimum_interval_hours,
            "event_acceleration": bool(event_acceleration),
        },
        "review": {
            "candidate_review_required": True,
            "profile_write_allowed": False,
        },
        "updated_at": _format_datetime(timestamp),
    }
    _validate_document(document, CONNECTOR_SCHEMA, label="lark connector config")
    atomic_write_json(
        path,
        document,
        backup=existing is not None,
        allow_transient=workspace_store.allow_transient(),
    )
    return {
        "workspace_id": workspace_id,
        "provider": "lark",
        "enabled": True,
        "config_path": str(path),
        "external_read_performed": False,
        "next_action": "Run connectors.py plan to review the bounded read plan.",
    }


def disable_connector(
    workspace_id: str,
    *,
    provider: str = "lark",
    actor: str = "owner_via_cli",
    root: Path | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    clean_provider = _validate_provider(provider)
    workspace, _ = workspace_store.load_manifest(workspace_id, root=root)
    path = _connector_path(workspace, clean_provider)
    config = _load_document(path, CONNECTOR_SCHEMA, label=f"{clean_provider} connector config")
    if config is None:
        return {
            "workspace_id": workspace_id,
            "provider": clean_provider,
            "enabled": False,
            "changed": False,
            "external_read_performed": False,
        }
    timestamp = _format_datetime(_parse_datetime(now or _now()))
    config["enabled"] = False
    config["activation"]["actor"] = actor.strip() or "owner_via_cli"
    config["activation"]["deactivated_at"] = timestamp
    config["updated_at"] = timestamp
    _validate_document(config, CONNECTOR_SCHEMA, label=f"{clean_provider} connector config")
    atomic_write_json(
        path,
        config,
        backup=True,
        allow_transient=workspace_store.allow_transient(),
    )
    return {
        "workspace_id": workspace_id,
        "provider": clean_provider,
        "enabled": False,
        "changed": True,
        "external_read_performed": False,
    }


def _chat_inventory_task() -> dict[str, Any]:
    return {
        "task_id": "chat-inventory",
        "command_category": "im.chat-list",
        "risk": "read",
        "argv": [
            "lark-cli",
            "im",
            "+chat-list",
            "--as",
            "user",
            "--types",
            "p2p,group",
            "--sort",
            "active_time",
            "--page-all",
            "--format",
            "json",
        ],
        "select_fields": [
            "chat_id",
            "chat_mode",
            "name",
            "description",
            "owner_id",
            "external",
            "chat_status",
            "p2p_target_type",
            "p2p_target_id",
        ],
        "model_work": False,
        "purpose": "Compare chat metadata locally; discovery alone never creates a Candidate.",
    }


def _message_sample_task(
    *, start: datetime, end: datetime, page_size: int, selector: str
) -> dict[str, Any]:
    return {
        "task_id": "message-sample",
        "command_category": "im.chat-messages-list",
        "risk": "read",
        "foreach": selector,
        "argv_template": [
            "lark-cli",
            "im",
            "+chat-messages-list",
            "--as",
            "user",
            "--chat-id",
            "{chat_id}",
            "--start",
            _format_lark_time(start),
            "--end",
            _format_lark_time(end),
            "--order",
            "desc",
            "--page-size",
            str(page_size),
            "--no-reactions",
            "--format",
            "json",
        ],
        "select_fields": [
            "message_id",
            "msg_type",
            "create_time",
            "update_time",
            "sender",
            "content",
            "mentions",
            "thread_id",
            "deleted",
            "updated",
        ],
        "model_work": False,
        "purpose": "Create immutable evidence snapshots before any semantic analysis.",
    }


def _drive_search_task(
    *, task_id: str, start: datetime, end: datetime, mode: str
) -> dict[str, Any]:
    mode_flags = {
        "edited": ("--edited-since", "--edited-until"),
        "commented": ("--commented-since", "--commented-until"),
        "created": ("--created-since", "--created-until"),
    }
    since_flag, until_flag = mode_flags[mode]
    argv = [
        "lark-cli",
        "drive",
        "+search",
        "--as",
        "user",
        "--query",
        "",
    ]
    if mode == "created":
        argv.append("--created-by-me")
    argv.extend(
        [
            since_flag,
            _format_lark_time(start),
            until_flag,
            _format_lark_time(end),
            "--sort",
            "edit_time" if mode != "created" else "create_time",
            "--page-size",
            "20",
            "--format",
            "json",
        ]
    )
    return {
        "task_id": task_id,
        "command_category": "drive.search",
        "risk": "read",
        "argv": argv,
        "pagination": "Follow page_token until has_more=false; deduplicate by canonical token and type.",
        "select_fields": [
            "title",
            "url",
            "doc_type",
            "edit_time",
            "summary_highlighted",
        ],
        "model_work": False,
        "purpose": f"Discover documents the user {mode} during the bounded window.",
    }


def _known_document_metadata_task() -> dict[str, Any]:
    return {
        "task_id": "known-document-metadata",
        "command_category": "drive.metas.batch_query",
        "risk": "read",
        "foreach_batch": "confirmed_project_document_refs",
        "batch_size_maximum": 200,
        "request_fields": ["doc_token", "doc_type", "with_url=true"],
        "select_fields": [
            "doc_token",
            "doc_type",
            "title",
            "url",
            "owner_id",
            "latest_modify_user",
            "create_time",
            "latest_modify_time",
            "sec_label_name",
        ],
        "model_work": False,
        "purpose": "Compare known project artifacts without searching unrelated documents.",
    }


def _build_plan_tasks(
    config: dict[str, Any], *, phase: str, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    baseline = config["baseline"]
    selector = (
        f"top_{baseline['max_active_chats']}_active_chat_ids"
        if phase == "baseline"
        else "confirmed_or_new_chat_ids"
    )
    tasks = [
        _chat_inventory_task(),
        _message_sample_task(
            start=start,
            end=end,
            page_size=baseline["initial_messages_per_chat"]
            if phase == "baseline"
            else baseline["expanded_messages_per_chat"],
            selector=selector,
        ),
        _drive_search_task(
            task_id="documents-edited", start=start, end=end, mode="edited"
        ),
        _drive_search_task(
            task_id="documents-commented", start=start, end=end, mode="commented"
        ),
        _drive_search_task(
            task_id="documents-created", start=start, end=end, mode="created"
        ),
    ]
    if phase == "daily_incremental":
        tasks.append(_known_document_metadata_task())
    return tasks


def plan_connector_sync(
    workspace_id: str,
    *,
    provider: str = "lark",
    root: Path | None = None,
    now: str | datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Return a bounded read plan only when the provider was explicitly enabled."""

    clean_provider = _validate_provider(provider)
    current = _parse_datetime(now or _now())
    state = connector_status(workspace_id, provider=clean_provider, root=root)
    if not state["enabled"]:
        return {
            "workspace_id": workspace_id,
            "provider": clean_provider,
            "status": "skipped",
            "reason": "connector_not_enabled",
            "lark_cli_required": False,
            "commands": [],
            "external_read_performed": False,
            "boundary": state["boundary"],
        }

    config = state["config"]
    if clean_provider != "lark" or config["provider"] != "lark":
        raise MemoryWorkspaceError("当前计划器只能处理显式启用的 Lark Connector。")
    if config["identity_mode"] != "user":
        raise MemoryWorkspaceError("Lark 个人基线必须使用 user identity，不能改用 bot。")

    checkpoint = state["checkpoint"]
    if checkpoint is None:
        phase = "baseline"
        trigger = "first_enable"
        start = current - timedelta(days=config["baseline"]["lookback_days"])
    else:
        phase = "daily_incremental"
        trigger = "agent_start"
        last_success = _parse_datetime(checkpoint["last_success_at"])
        interval = timedelta(hours=config["schedule"]["minimum_interval_hours"])
        if not force and current - last_success < interval:
            return {
                "workspace_id": workspace_id,
                "provider": clean_provider,
                "status": "not_due",
                "reason": "minimum_interval_not_reached",
                "next_due_at": _format_datetime(last_success + interval),
                "lark_cli_required": False,
                "commands": [],
                "external_read_performed": False,
            }
        start = _parse_datetime(checkpoint["coverage"]["end"])
    if start > current:
        raise MemoryWorkspaceError("checkpoint coverage.end 晚于当前时间，已停止增量计划。")

    tasks = _build_plan_tasks(config, phase=phase, start=start, end=current)
    optional_scopes = (
        ["im:message.p2p_msg:readonly"]
        if config["schedule"]["event_acceleration"]
        else []
    )
    return {
        "workspace_id": workspace_id,
        "provider": "lark",
        "status": "due",
        "phase": phase,
        "trigger": trigger,
        "identity_mode": "user",
        "coverage": {
            "start": _format_datetime(start),
            "end": _format_datetime(current),
        },
        "required_scopes": list(LARK_REQUIRED_SCOPES),
        "optional_event_scopes": optional_scopes,
        "lark_cli_required": True,
        "commands": tasks,
        "external_read_performed": False,
        "execution_boundary": (
            "Plan only. Execute read commands sequentially, save immutable snapshots, stop on auth/scope errors, "
            "and never write Exact Profile from connector evidence."
        ),
    }


def record_sync_success(
    workspace_id: str,
    *,
    provider: str = "lark",
    coverage_start: str | datetime,
    coverage_end: str | datetime,
    snapshot_ref: str,
    trigger: str,
    complete: bool = True,
    high_watermark_external_id: str | None = None,
    root: Path | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Advance a checkpoint only after an external read and snapshot succeeded."""

    clean_provider = _validate_provider(provider)
    state = connector_status(workspace_id, provider=clean_provider, root=root)
    if not state["enabled"]:
        raise MemoryWorkspaceError("Connector 未启用，拒绝记录同步成功。")
    start = _parse_datetime(coverage_start)
    end = _parse_datetime(coverage_end)
    if start > end:
        raise MemoryWorkspaceError("coverage_start 不能晚于 coverage_end。")
    relative = PurePosixPath(snapshot_ref)
    if relative.is_absolute() or ".." in relative.parts or not snapshot_ref.strip():
        raise MemoryWorkspaceError("snapshot_ref 必须是 Workspace 内不含 .. 的相对路径。")
    if trigger not in {"first_enable", "agent_start", "idle", "manual", "event_reconciliation"}:
        raise MemoryWorkspaceError(f"未知 trigger：{trigger}")
    workspace, _ = workspace_store.load_manifest(workspace_id, root=root)
    snapshot_path = (workspace / relative).resolve(strict=False)
    if not is_within(snapshot_path, workspace.resolve(strict=False)):
        raise MemoryWorkspaceError("snapshot_ref 解析后超出 Workspace。")
    if not snapshot_path.is_file():
        raise MemoryWorkspaceError(
            f"尚未找到不可变快照，拒绝推进 checkpoint：{snapshot_ref}"
        )
    path = _checkpoint_path(workspace, clean_provider)
    timestamp = _parse_datetime(now or _now())
    document = {
        "schema_version": 1,
        "connector_id": state["config"]["connector_id"],
        "provider": clean_provider,
        "workspace_id": workspace_id,
        "stream_id": "baseline" if state["checkpoint"] is None else "daily_incremental",
        "last_attempt_at": _format_datetime(timestamp),
        "last_success_at": _format_datetime(timestamp),
        "trigger": trigger,
        "coverage": {
            "start": _format_datetime(start),
            "end": _format_datetime(end),
            "complete": bool(complete),
        },
        "high_watermark": {
            "occurred_at": _format_datetime(end),
            "external_id": high_watermark_external_id,
        },
        "snapshot_ref": snapshot_ref,
        "error_boundary": None,
    }
    _validate_document(document, CHECKPOINT_SCHEMA, label="sync checkpoint")
    atomic_write_json(
        path,
        document,
        backup=path.exists(),
        allow_transient=workspace_store.allow_transient(),
    )
    return {
        "workspace_id": workspace_id,
        "provider": clean_provider,
        "checkpoint_path": str(path),
        "coverage": document["coverage"],
        "next_due_at": _format_datetime(
            timestamp
            + timedelta(hours=state["config"]["schedule"]["minimum_interval_hours"])
        ),
    }


def validate_workspace_connector_files(
    workspace: Path, workspace_id: str
) -> list[str]:
    """Validate optional connector files without making them required for every user."""

    errors: list[str] = []
    config_dir = workspace / "config" / "connectors"
    if config_dir.is_dir():
        for path in sorted(config_dir.glob("*.json")):
            try:
                document = _load_document(
                    path, CONNECTOR_SCHEMA, label=f"connector config {path.name}"
                )
                if document and document["workspace_id"] != workspace_id:
                    errors.append(
                        f"{path.relative_to(workspace)}: workspace_id mismatch"
                    )
                if document and path.stem != document["provider"]:
                    errors.append(f"{path.relative_to(workspace)}: provider/file mismatch")
            except MemoryWorkspaceError as exc:
                errors.append(f"{path.relative_to(workspace)}: {exc}")
    checkpoint_root = workspace / ".llm-wiki" / "connectors"
    if checkpoint_root.is_dir():
        for path in sorted(checkpoint_root.glob("*/checkpoint.json")):
            try:
                document = _load_document(
                    path, CHECKPOINT_SCHEMA, label=f"sync checkpoint {path.parent.name}"
                )
                if document and document["workspace_id"] != workspace_id:
                    errors.append(
                        f"{path.relative_to(workspace)}: workspace_id mismatch"
                    )
                if document and path.parent.name != document["provider"]:
                    errors.append(f"{path.relative_to(workspace)}: provider/path mismatch")
            except MemoryWorkspaceError as exc:
                errors.append(f"{path.relative_to(workspace)}: {exc}")
    return errors
