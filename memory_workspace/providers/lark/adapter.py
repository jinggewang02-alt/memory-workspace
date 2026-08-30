"""Lark-specific fetching and conversion into provider-neutral Sync Bundles."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ... import connector_protocol, project_memory
from ...io import MemoryWorkspaceError
from ...schema import load_json_object, validate
from . import config


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
OBSERVATION_SCHEMA = PACKAGE_ROOT / "schemas" / "external-observation.schema.json"
CommandRunner = Callable[[list[str]], Any]


def _format_time(value: datetime) -> str:
    return connector_protocol.format_datetime(value)


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, check=False)


def _read_envelope(result: Any, *, label: str) -> tuple[dict[str, Any], bytes]:
    returncode = int(getattr(result, "returncode", 1))
    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    try:
        document = json.loads(stdout) if stdout.strip() else None
    except json.JSONDecodeError as exc:
        raise MemoryWorkspaceError(
            f"{label} 返回了无效 JSON，已停止且未推进 checkpoint。"
        ) from exc
    if returncode != 0 or not isinstance(document, dict) or document.get("ok") is not True:
        detail = stderr.strip()
        if isinstance(document, dict):
            error = document.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("hint") or detail)
        raise MemoryWorkspaceError(
            f"{label} 读取失败，已停在明确映射的来源边界"
            + (f"：{detail[:240]}" if detail else "。")
        )
    if not isinstance(document.get("data"), dict):
        raise MemoryWorkspaceError(f"{label} 缺少 data 对象，已停止且未推进 checkpoint。")
    return document, stdout.encode("utf-8")


def _source_command(
    source: dict[str, Any], *, start: str, end: str
) -> tuple[str, list[str]]:
    if source["kind"] == "chat":
        return (
            "im.chat-messages-list",
            [
                "lark-cli",
                "im",
                "+chat-messages-list",
                "--as",
                "user",
                "--chat-id",
                source["external_id"],
                "--start",
                start,
                "--end",
                end,
                "--order",
                "asc",
                "--page-all",
                "--no-reactions",
                "--format",
                "json",
            ],
        )
    return (
        "docs.fetch",
        [
            "lark-cli",
            "docs",
            "+fetch",
            "--as",
            "user",
            "--doc",
            source["locator"],
            "--doc-format",
            "markdown",
            "--detail",
            "simple",
            "--format",
            "json",
        ],
    )


def _messages(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("messages", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _document(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("document")
    return value if isinstance(value, dict) else data


def _text(value: Any) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{"):
            try:
                decoded = json.loads(stripped)
                if isinstance(decoded, dict):
                    for key in ("text", "content"):
                        if isinstance(decoded.get(key), str):
                            return " ".join(decoded[key].split())
            except json.JSONDecodeError:
                pass
        return " ".join(stripped.split())
    if isinstance(value, dict):
        for key in ("text", "content", "markdown"):
            if isinstance(value.get(key), str):
                return " ".join(value[key].split())
    return ""


def _event_time(value: Any, fallback: str) -> str:
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.isdigit()
    ):
        number = int(value)
        if number > 10_000_000_000:
            number //= 1000
        return _format_time(datetime.fromtimestamp(number, timezone.utc))
    if isinstance(value, str) and value.strip():
        try:
            return _format_time(connector_protocol.parse_datetime(value.strip()))
        except MemoryWorkspaceError:
            pass
    return fallback


def _actor(sender: Any) -> list[dict[str, Any]]:
    if not isinstance(sender, dict):
        return []
    external_id = sender.get("id") or sender.get("open_id") or sender.get("sender_id")
    if not external_id:
        return []
    sender_type = str(
        sender.get("sender_type") or sender.get("type") or "unknown"
    ).lower()
    actor_type = (
        "bot"
        if "bot" in sender_type
        else "user"
        if sender_type in {"user", "person"}
        else "unknown"
    )
    display = sender.get("name") or sender.get("display_name")
    return [
        {
            "external_id": str(external_id),
            "actor_type": actor_type,
            "display_name": str(display) if display else None,
        }
    ]


def _observation(
    *,
    workspace_id: str,
    source: dict[str, Any],
    item: dict[str, Any],
    snapshot_ref_value: str,
    coverage: dict[str, str],
    captured_at: str,
    command_category: str,
    document_mode: bool,
) -> dict[str, Any]:
    if document_mode:
        external_id = str(
            item.get("document_id")
            or item.get("doc_token")
            or source["external_id"]
        )
        body = _text(item.get("content") or item.get("markdown"))
        title = _text(item.get("title")) or source["label"]
        occurred_at = _event_time(
            item.get("edit_time") or item.get("updated_at"), captured_at
        )
        object_type = "document"
        event_type = "asset.updated"
        actors: list[dict[str, Any]] = []
        parent = None
        content_type = "text/markdown"
    else:
        external_id = str(
            item.get("message_id")
            or hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:20]
        )
        body = _text(item.get("content"))
        title = source["label"]
        occurred_at = _event_time(
            item.get("create_time") or item.get("occurred_at"), captured_at
        )
        object_type = "message"
        event_type = "message.created"
        actors = _actor(item.get("sender"))
        parent = source["external_id"]
        content_type = str(item.get("msg_type") or "text")
    content_hash = (
        "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest() if body else None
    )
    stable = hashlib.sha256(
        f"{source['source_id']}:{external_id}".encode()
    ).hexdigest()[:24]
    observation = {
        "schema_version": 1,
        "observation_id": f"obs_{stable}",
        "event_type": event_type,
        "occurred_at": occurred_at,
        "observed_at": captured_at,
        "source": {
            "provider": "lark",
            "connector_id": "lark",
            "adapter": "lark-cli",
            "adapter_version": "1",
            "identity_mode": "user",
            "workspace_id": workspace_id,
        },
        "object": {
            "object_type": object_type,
            "external_id": external_id,
            "parent_external_id": parent,
            "canonical_url": (
                source["locator"]
                if document_mode and source["locator"].startswith("http")
                else None
            ),
            "content_type": content_type,
            "attributes": {
                "source_id": source["source_id"],
                "source_label": source["label"],
            },
        },
        "actors": actors,
        "content": {
            "title": title,
            "text_excerpt": body[:1000] or None,
            "content_hash": content_hash,
            "attachment_refs": [],
        },
        "provenance": {
            "snapshot_ref": snapshot_ref_value,
            "command_category": command_category,
            "time_coverage": {
                "start": coverage["start"],
                "end": coverage["end"],
                "complete": True,
            },
        },
        "routing": {
            "project_id": workspace_id,
            "project_confidence": 1.0,
            "reason_codes": ["confirmed_source_mapping"],
            "candidate_eligible": True,
        },
        "privacy": {
            "classification": "local_content",
            "expires_at": _format_time(
                connector_protocol.parse_datetime(captured_at) + timedelta(days=3650)
            ),
            "profile_write_allowed": False,
        },
    }
    errors = validate(observation, load_json_object(OBSERVATION_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("Lark Observation 校验失败：" + "; ".join(errors))
    return observation


def sync_lark_workspace(
    workspace_id: str,
    *,
    root: Path | None = None,
    now: str | datetime | None = None,
    force: bool = False,
    trigger: str | None = None,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Fetch mapped Lark sources and hand one standard bundle to Memory Core."""

    mapped = connector_protocol.list_sources(
        workspace_id, provider="lark", root=root
    )
    sources = [
        {**item, "provider": "lark"}
        for item in mapped
        if item["enabled"] and item["sync_mode"] == "direct_execution"
    ]
    if not sources:
        raise MemoryWorkspaceError(
            "没有已确认的 direct_execution 项目来源；拒绝扩大到其他飞书资源。"
        )
    plan = config.plan_connector_sync(
        workspace_id, root=root, now=now, force=force
    )
    if plan["status"] != "due":
        return {**plan, "mapped_sources": len(sources)}
    selected_trigger = trigger or plan["trigger"]
    execute = runner or _run
    captured_at = plan["coverage"]["end"]
    snapshots: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for source in sources:
        category, argv = _source_command(
            source, start=plan["coverage"]["start"], end=plan["coverage"]["end"]
        )
        envelope, raw = _read_envelope(execute(argv), label=source["label"])
        pending.append(
            {
                "source": source,
                "category": category,
                "envelope": envelope,
                "raw": raw,
            }
        )
    for item in pending:
        source = item["source"]
        data = item["envelope"]["data"]
        records = _messages(data) if source["kind"] == "chat" else [_document(data)]
        ref = project_memory.snapshot_ref(
            provider="lark",
            kind=source["kind"],
            source_id=source["source_id"],
            captured_at=captured_at,
        )
        snapshots.append(
            {
                "source": source,
                "snapshot_ref": ref,
                "command_category": item["category"],
                "result_count": len(records),
                "raw": item["raw"],
            }
        )
        for record in records:
            observations.append(
                _observation(
                    workspace_id=workspace_id,
                    source=source,
                    item=record,
                    snapshot_ref_value=ref,
                    coverage=plan["coverage"],
                    captured_at=captured_at,
                    command_category=item["category"],
                    document_mode=source["kind"] == "document",
                )
            )
    result = project_memory.persist_sync_bundle(
        workspace_id,
        provider="lark",
        connector_id="lark",
        captured_at=captured_at,
        trigger=selected_trigger,
        coverage=plan["coverage"],
        snapshots=snapshots,
        observations=observations,
        limitations=[
            "只覆盖已显式映射的 direct_execution 来源。",
            "缺失内容不能证明事件没有发生。",
        ],
        root=root,
    )
    result["phase"] = plan["phase"]
    return result
