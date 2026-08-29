"""Execute an explicitly mapped, read-only Lark project sync.

Discovery remains a plan-only concern in :mod:`memory_workspace.connectors`.
This module only reads sources that the owner already mapped to one Workspace.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import connectors, index, workspace as workspace_store
from .io import MemoryWorkspaceError, atomic_write_json, atomic_write_text, write_bytes_once
from .schema import load_json_object, validate


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OBSERVATION_SCHEMA = PACKAGE_ROOT / "schemas" / "external-observation.schema.json"
MANIFEST_SCHEMA = PACKAGE_ROOT / "schemas" / "lark-sync-manifest.schema.json"
PROJECT_VIEW_SCHEMA = PACKAGE_ROOT / "schemas" / "project-memory-view.schema.json"
CommandRunner = Callable[[list[str]], Any]


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        raise MemoryWorkspaceError(f"Lark 时间缺少时区：{value}")
    return parsed.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, check=False)


def _read_envelope(result: Any, *, label: str) -> tuple[dict[str, Any], bytes]:
    returncode = int(getattr(result, "returncode", 1))
    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    try:
        document = json.loads(stdout) if stdout.strip() else None
    except json.JSONDecodeError as exc:
        raise MemoryWorkspaceError(f"{label} 返回了无效 JSON，已停止且未推进 checkpoint。") from exc
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


def _source_command(source: dict[str, Any], *, start: str, end: str) -> tuple[str, list[str]]:
    if source["kind"] == "chat":
        return (
            "im.chat-messages-list",
            [
                "lark-cli", "im", "+chat-messages-list", "--as", "user",
                "--chat-id", source["external_id"], "--start", start, "--end", end,
                "--order", "asc", "--page-all", "--no-reactions", "--format", "json",
            ],
        )
    return (
        "docs.fetch",
        [
            "lark-cli", "docs", "+fetch", "--as", "user", "--doc", source["locator"],
            "--doc-format", "markdown", "--detail", "simple", "--format", "json",
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
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        number = int(value)
        if number > 10_000_000_000:
            number //= 1000
        return _format_time(datetime.fromtimestamp(number, timezone.utc))
    if isinstance(value, str) and value.strip():
        try:
            return _format_time(_parse_time(value.strip()))
        except (ValueError, MemoryWorkspaceError):
            pass
    return fallback


def _actor(sender: Any) -> list[dict[str, Any]]:
    if not isinstance(sender, dict):
        return []
    external_id = sender.get("id") or sender.get("open_id") or sender.get("sender_id")
    if not external_id:
        return []
    sender_type = str(sender.get("sender_type") or sender.get("type") or "unknown").lower()
    actor_type = "bot" if "bot" in sender_type else "user" if sender_type in {"user", "person"} else "unknown"
    display = sender.get("name") or sender.get("display_name")
    return [{"external_id": str(external_id), "actor_type": actor_type, "display_name": str(display) if display else None}]


def _observation(
    *, workspace_id: str, source: dict[str, Any], item: dict[str, Any], snapshot_ref: str,
    coverage: dict[str, str], captured_at: str, command_category: str, document_mode: bool,
) -> dict[str, Any]:
    if document_mode:
        external_id = str(item.get("document_id") or item.get("doc_token") or source["external_id"])
        body = _text(item.get("content") or item.get("markdown"))
        title = _text(item.get("title")) or source["label"]
        occurred_at = _event_time(item.get("edit_time") or item.get("updated_at"), captured_at)
        object_type = "document"
        event_type = "asset.updated"
        actors: list[dict[str, Any]] = []
        parent = None
        content_type = "text/markdown"
    else:
        external_id = str(item.get("message_id") or hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:20])
        body = _text(item.get("content"))
        title = source["label"]
        occurred_at = _event_time(item.get("create_time") or item.get("occurred_at"), captured_at)
        object_type = "message"
        event_type = "message.created"
        actors = _actor(item.get("sender"))
        parent = source["external_id"]
        content_type = str(item.get("msg_type") or "text")
    content_hash = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest() if body else None
    stable = hashlib.sha256(f"{source['source_id']}:{external_id}".encode()).hexdigest()[:24]
    observation = {
        "schema_version": 1,
        "observation_id": f"obs_{stable}",
        "event_type": event_type,
        "occurred_at": occurred_at,
        "observed_at": captured_at,
        "source": {
            "provider": "lark", "connector_id": "lark", "adapter": "lark-cli",
            "adapter_version": "1", "identity_mode": "user", "workspace_id": workspace_id,
        },
        "object": {
            "object_type": object_type, "external_id": external_id,
            "parent_external_id": parent,
            "canonical_url": source["locator"] if document_mode and source["locator"].startswith("http") else None,
            "content_type": content_type, "attributes": {"source_id": source["source_id"], "source_label": source["label"]},
        },
        "actors": actors,
        "content": {"title": title, "text_excerpt": body[:1000] or None, "content_hash": content_hash, "attachment_refs": []},
        "provenance": {
            "snapshot_ref": snapshot_ref, "command_category": command_category,
            "time_coverage": {"start": coverage["start"], "end": coverage["end"], "complete": True},
        },
        "routing": {"project_id": workspace_id, "project_confidence": 1.0, "reason_codes": ["confirmed_source_mapping"], "candidate_eligible": True},
        "privacy": {"classification": "local_content", "expires_at": _format_time(_parse_time(captured_at) + timedelta(days=3650)), "profile_write_allowed": False},
    }
    errors = validate(observation, load_json_object(OBSERVATION_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("Lark observation 协议校验失败：" + "; ".join(errors))
    return observation


def _find_or_create_source_note(
    workspace: Path, source: dict[str, Any], snapshot_ref: str, captured_at: str,
    coverage: dict[str, str], content_hash: str,
) -> tuple[str, bool]:
    source_dir = workspace / "wiki" / "sources"
    external_ref = f"lark:{source['kind']}:{source['external_id']}"
    note_path: Path | None = None
    for candidate in sorted(source_dir.glob("S-*.md")):
        try:
            metadata = workspace_store._parse_frontmatter(candidate)
        except (OSError, UnicodeDecodeError, MemoryWorkspaceError):
            continue
        if metadata.get("external_ref") == external_ref:
            note_path = candidate
            break
    created = note_path is None
    if note_path is None:
        note_path = source_dir / f"{workspace_store._next_source_id(source_dir)}.md"
    source_id = note_path.stem
    scalar = lambda value: json.dumps(value, ensure_ascii=False)
    text = (
        "---\n"
        "schema_version: 1\n"
        f"id: {source_id}\n"
        f"kind: lark_{source['kind']}\n"
        f"title: {scalar(source['label'])}\n"
        f"content_path: {scalar(snapshot_ref)}\n"
        f"external_ref: {scalar(external_ref)}\n"
        f"captured_at: {scalar(captured_at)}\n"
        f"coverage: {scalar(coverage['start'] + ' to ' + coverage['end'])}\n"
        f"content_hash: {scalar(content_hash)}\n"
        "status: captured\n"
        "limitations: []\n"
        "privacy: workspace_private\n"
        "---\n\n"
        f"# {source['label']}\n\n"
        f"通过只读 lark-cli 从已确认的项目{('群聊' if source['kind'] == 'chat' else '文档')}获取。"
        "原始返回保存在不可变快照中；项目视图中的条目必须回链到本来源。\n"
    )
    atomic_write_text(note_path, text, backup=not created, allow_transient=workspace_store.allow_transient())
    return note_path.relative_to(workspace).as_posix(), created


def _classified(text: str) -> tuple[str, str]:
    compact = " ".join(text.split())
    lower = compact.casefold()
    for marker in ("决定：", "决策：", "decision:"):
        if marker in lower:
            return "decisions", compact
    for marker in ("下一步：", "待办：", "todo:", "action:"):
        if marker in lower:
            return "next_actions", compact
    return "updates", compact


def _compile_project_view(
    *, workspace_id: str, workspace_name: str, captured_at: str,
    source_rows: list[dict[str, Any]], observations: list[dict[str, Any]], connector_state: dict[str, Any],
) -> dict[str, Any]:
    sections: dict[str, list[dict[str, Any]]] = {key: [] for key in ("updates", "decisions", "next_actions", "people", "artifacts")}
    source_by_id = {row["source_id"]: row for row in source_rows}
    people_seen: set[str] = set()
    for observation in sorted(observations, key=lambda item: item["occurred_at"], reverse=True):
        source_id = observation["object"]["attributes"]["source_id"]
        if source_id not in source_by_id:
            # A formerly mapped source can be disabled without deleting its evidence.
            # Keep the raw observations; omit them from the active project view until
            # the source is mapped again.
            continue
        source_row = source_by_id[source_id]
        base = {
            "occurred_at": observation["occurred_at"], "source_id": source_id,
            "source_note": source_row["source_note"], "snapshot_ref": observation["provenance"]["snapshot_ref"],
        }
        excerpt = observation["content"]["text_excerpt"]
        if observation["object"]["object_type"] == "document":
            sections["artifacts"].append({**base, "text": observation["content"]["title"] or source_row["label"], "classification": "artifact_metadata"})
            if excerpt:
                bucket, value = _classified(excerpt[:500])
                sections[bucket].append({**base, "text": value, "classification": "explicit_marker" if bucket != "updates" else "source_excerpt"})
        elif excerpt:
            bucket, value = _classified(excerpt)
            sections[bucket].append({**base, "text": value, "classification": "explicit_marker" if bucket != "updates" else "source_excerpt"})
        for actor in observation["actors"]:
            key = actor["external_id"]
            if key in people_seen:
                continue
            people_seen.add(key)
            sections["people"].append({**base, "text": actor["display_name"] or key, "classification": "observed_actor"})
    for key in sections:
        sections[key] = sections[key][:20]
    checkpoint = connector_state.get("checkpoint") or {}
    coverage = checkpoint.get("coverage") or {}
    document = {
        "schema_version": 1,
        "workspace": {"id": workspace_id, "name": workspace_name},
        "generated_at": captured_at,
        "status": "ready" if source_rows else "empty",
        "notice": "这是由最新来源证据生成的可重建项目视图；显式标记不等同于已确认的正式决策。",
        "connector": {
            "provider": "lark", "configured": connector_state["configured"], "enabled": connector_state["enabled"],
            "mapped_sources": len(source_rows), "last_success_at": connector_state.get("checkpoint", {}).get("last_success_at") if connector_state.get("checkpoint") else captured_at,
            "coverage_end": coverage.get("end") or captured_at,
        },
        "sections": sections,
        "sources": source_rows,
    }
    errors = validate(document, load_json_object(PROJECT_VIEW_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("Project Memory 视图协议校验失败：" + "; ".join(errors))
    return document


def project_memory_path(workspace: Path) -> Path:
    return workspace / ".llm-wiki" / "index" / "project-memory.json"


def _load_observation_history(workspace: Path) -> list[dict[str, Any]]:
    """Rebuild the project stream from immutable JSONL, deduplicated by stable id."""

    by_id: dict[str, dict[str, Any]] = {}
    schema = load_json_object(OBSERVATION_SCHEMA)
    root = workspace / "connected" / "lark" / "observations"
    if not root.is_dir():
        return []
    for path in sorted(root.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise MemoryWorkspaceError(f"无法读取 Observation 历史 {path}：{exc}") from exc
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                observation = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MemoryWorkspaceError(
                    f"Observation 历史损坏：{path}:{line_number}"
                ) from exc
            if not isinstance(observation, dict):
                raise MemoryWorkspaceError(
                    f"Observation 历史必须是对象：{path}:{line_number}"
                )
            errors = validate(observation, schema)
            if errors:
                raise MemoryWorkspaceError(
                    f"Observation 历史协议错误：{path}:{line_number}：" + "; ".join(errors)
                )
            by_id[observation["observation_id"]] = observation
    return list(by_id.values())


def load_project_memory(workspace_id: str, *, root: Path | None = None) -> dict[str, Any]:
    workspace, manifest = workspace_store.load_manifest(workspace_id, root=root)
    path = project_memory_path(workspace)
    if path.is_file():
        document = load_json_object(path)
        errors = validate(document, load_json_object(PROJECT_VIEW_SCHEMA))
        if errors:
            raise MemoryWorkspaceError("Project Memory 视图协议校验失败：" + "; ".join(errors))
        return document
    state = connectors.connector_status(workspace_id, root=root)
    return {
        "schema_version": 1,
        "workspace": {"id": workspace_id, "name": manifest["workspace"]["name"]},
        "generated_at": None,
        "status": "empty",
        "notice": "尚无已同步的项目证据。先启用 Lark Connector，并明确映射项目群聊或文档。",
        "connector": {
            "provider": "lark", "configured": state["configured"], "enabled": state["enabled"],
            "mapped_sources": state["mapped_sources"], "last_success_at": state["checkpoint"]["last_success_at"] if state["checkpoint"] else None,
            "coverage_end": state["checkpoint"]["coverage"]["end"] if state["checkpoint"] else None,
        },
        "sections": {key: [] for key in ("updates", "decisions", "next_actions", "people", "artifacts")},
        "sources": [],
    }


def sync_lark_workspace(
    workspace_id: str, *, root: Path | None = None, now: str | datetime | None = None,
    force: bool = False, trigger: str | None = None, runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Read exactly the mapped sources, persist evidence, then advance checkpoint."""

    sources = [item for item in connectors.list_connector_sources(workspace_id, root=root) if item["enabled"] and item["sync_mode"] == "direct_execution"]
    if not sources:
        raise MemoryWorkspaceError("没有已确认的 direct_execution 项目来源；拒绝扩大到其他飞书资源。")
    plan = connectors.plan_connector_sync(workspace_id, root=root, now=now, force=force)
    if plan["status"] != "due":
        return {**plan, "mapped_sources": len(sources)}
    selected_trigger = trigger or plan["trigger"]
    if selected_trigger not in {"first_enable", "agent_start", "idle", "manual", "event_reconciliation"}:
        raise MemoryWorkspaceError(f"未知 trigger：{selected_trigger}")
    current = _parse_time(plan["coverage"]["end"])
    captured_at = _format_time(current)
    stamp = _stamp(current)
    execute = runner or _run
    pending: list[dict[str, Any]] = []
    for source in sources:
        category, argv = _source_command(source, start=plan["coverage"]["start"], end=plan["coverage"]["end"])
        envelope, raw = _read_envelope(execute(argv), label=source["label"])
        pending.append({"source": source, "category": category, "argv": argv, "envelope": envelope, "raw": raw})

    workspace, manifest_meta = workspace_store.load_manifest(workspace_id, root=root)
    snapshot_rows: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    for item in pending:
        source = item["source"]
        folder = "chats" if source["kind"] == "chat" else "docs"
        relative = f"connected/lark/{folder}/{source['source_id']}/{stamp}.json"
        write_bytes_once(workspace / relative, item["raw"], allow_transient=workspace_store.allow_transient())
        content_hash = "sha256:" + hashlib.sha256(item["raw"]).hexdigest()
        data = item["envelope"]["data"]
        records = _messages(data) if source["kind"] == "chat" else [_document(data)]
        snapshot_rows.append({
            "source_id": source["source_id"], "kind": source["kind"], "external_id": source["external_id"],
            "label": source["label"], "command_category": item["category"], "snapshot_ref": relative,
            "result_count": len(records), "content_hash": content_hash,
        })
        note_ref, created = _find_or_create_source_note(
            workspace, source, relative, captured_at, plan["coverage"], content_hash
        )
        if created:
            index_path = workspace / "wiki" / "index.md"
            current_index = index_path.read_text(encoding="utf-8")
            atomic_write_text(
                index_path,
                workspace_store._append_source_to_index(current_index, Path(note_ref).stem, source["label"]),
                backup=False,
                allow_transient=workspace_store.allow_transient(),
            )
        source_rows.append({
            "source_id": source["source_id"], "kind": source["kind"], "label": source["label"],
            "external_id": source["external_id"], "source_note": note_ref,
            "latest_snapshot_ref": relative, "captured_at": captured_at,
        })
        for record in records:
            observations.append(_observation(
                workspace_id=workspace_id, source=source, item=record, snapshot_ref=relative,
                coverage=plan["coverage"], captured_at=captured_at, command_category=item["category"],
                document_mode=source["kind"] == "document",
            ))

    observation_ref = f"connected/lark/observations/{stamp}-{workspace_id}.jsonl"
    observation_bytes = "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in observations).encode("utf-8")
    write_bytes_once(workspace / observation_ref, observation_bytes, allow_transient=workspace_store.allow_transient())
    manifest_document = {
        "schema_version": 1, "provider": "lark", "workspace_id": workspace_id,
        "captured_at": captured_at, "trigger": selected_trigger, "time_coverage": plan["coverage"],
        "complete": True, "snapshots": snapshot_rows,
        "limitations": ["只覆盖已显式映射的 direct_execution 来源。", "缺失内容不能证明事件没有发生。"],
    }
    errors = validate(manifest_document, load_json_object(MANIFEST_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("Lark sync manifest 协议校验失败：" + "; ".join(errors))
    manifest_ref = f"connected/lark/manifests/{stamp}-{workspace_id}.json"
    write_bytes_once(
        workspace / manifest_ref,
        (json.dumps(manifest_document, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        allow_transient=workspace_store.allow_transient(),
    )
    checkpoint = connectors.record_sync_success(
        workspace_id, root=root, coverage_start=plan["coverage"]["start"], coverage_end=plan["coverage"]["end"],
        snapshot_ref=manifest_ref, trigger=selected_trigger, complete=True, now=captured_at,
    )
    state = connectors.connector_status(workspace_id, root=root)
    view = _compile_project_view(
        workspace_id=workspace_id, workspace_name=manifest_meta["workspace"]["name"], captured_at=captured_at,
        source_rows=source_rows, observations=_load_observation_history(workspace), connector_state=state,
    )
    atomic_write_json(project_memory_path(workspace), view, backup=True, allow_transient=workspace_store.allow_transient())
    log_path = workspace / "wiki" / "log.md"
    log_text = log_path.read_text(encoding="utf-8")
    atomic_write_text(
        log_path,
        log_text.rstrip() + f"\n\n## {captured_at} — Lark project sync\n\nCaptured {len(snapshot_rows)} mapped sources in `{manifest_ref}`.\n",
        backup=False,
        allow_transient=workspace_store.allow_transient(),
    )
    index.rebuild_index(workspace_id, root=root)
    return {
        "workspace_id": workspace_id, "status": "synced", "phase": plan["phase"],
        "mapped_sources": len(sources), "observations": len(observations),
        "manifest_ref": manifest_ref, "observation_ref": observation_ref,
        "project_memory_path": str(project_memory_path(workspace)), "checkpoint": checkpoint,
        "external_read_performed": True,
    }
