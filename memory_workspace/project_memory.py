"""Provider-neutral evidence persistence and rebuildable project memory views."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from . import connector_protocol, index, workspace as workspace_store
from .io import MemoryWorkspaceError, atomic_write_json, atomic_write_text, write_bytes_once
from .schema import load_json_object, validate


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OBSERVATION_SCHEMA = PACKAGE_ROOT / "schemas" / "external-observation.schema.json"
MANIFEST_SCHEMA = PACKAGE_ROOT / "schemas" / "sync-manifest.schema.json"
PROJECT_VIEW_SCHEMA = PACKAGE_ROOT / "schemas" / "project-memory-view.schema.json"


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def snapshot_ref(
    *, provider: str, kind: str, source_id: str, captured_at: str | datetime
) -> str:
    clean_provider = connector_protocol.validate_provider(provider)
    workspace_store.validate_slug(source_id)
    clean_kind = kind.strip().lower().replace("_", "-")
    workspace_store.validate_slug(clean_kind)
    stamp = _stamp(connector_protocol.parse_datetime(captured_at))
    return f"connected/{clean_provider}/snapshots/{clean_kind}/{source_id}/{stamp}.json"


def project_memory_path(workspace: Path) -> Path:
    return workspace / ".llm-wiki" / "index" / "project-memory.json"


def _upgrade_project_view(document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Upgrade the disposable pre-v0.11 read model without touching evidence."""

    upgraded = False
    if "connectors" not in document and isinstance(document.get("connector"), dict):
        document = dict(document)
        document["connectors"] = [document.pop("connector")]
        upgraded = True
    connectors = document.get("connectors")
    default_provider = None
    if isinstance(connectors, list) and len(connectors) == 1:
        connector = connectors[0]
        if isinstance(connector, dict) and isinstance(connector.get("provider"), str):
            default_provider = connector["provider"]
    sources = document.get("sources")
    if isinstance(sources, list):
        revised_sources = []
        for source in sources:
            if not isinstance(source, dict) or "provider" in source:
                revised_sources.append(source)
                continue
            revised = dict(source)
            ref = str(revised.get("latest_snapshot_ref") or "")
            parts = PurePosixPath(ref).parts
            provider = (
                parts[1]
                if len(parts) > 2 and parts[0] == "connected"
                else default_provider
            )
            if provider:
                revised["provider"] = provider
                upgraded = True
            revised_sources.append(revised)
        if upgraded:
            document = dict(document)
            document["sources"] = revised_sources
    return document, upgraded


def _validate_observation(observation: dict[str, Any]) -> None:
    errors = validate(observation, load_json_object(OBSERVATION_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("External Observation 校验失败：" + "; ".join(errors))


def _find_or_create_source_note(
    workspace: Path,
    source: dict[str, Any],
    *,
    snapshot_ref_value: str,
    captured_at: str,
    coverage: dict[str, str],
    content_hash: str,
) -> tuple[str, bool]:
    source_dir = workspace / "wiki" / "sources"
    external_ref = (
        f"{source['provider']}:{source['kind']}:{source['external_id']}"
    )
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
    source_note_id = note_path.stem
    scalar = lambda value: json.dumps(value, ensure_ascii=False)
    text = (
        "---\n"
        "schema_version: 1\n"
        f"id: {source_note_id}\n"
        f"kind: connector_{source['provider']}_{source['kind']}\n"
        f"title: {scalar(source['label'])}\n"
        f"content_path: {scalar(snapshot_ref_value)}\n"
        f"external_ref: {scalar(external_ref)}\n"
        f"captured_at: {scalar(captured_at)}\n"
        f"coverage: {scalar(coverage['start'] + ' to ' + coverage['end'])}\n"
        f"content_hash: {scalar(content_hash)}\n"
        "status: captured\n"
        "limitations: []\n"
        "privacy: workspace_private\n"
        "---\n\n"
        f"# {source['label']}\n\n"
        f"由 `{source['provider']}` Connector 从已确认的项目来源只读获取。"
        "原始返回保存在不可变快照中；项目视图中的条目必须回链到本来源。\n"
    )
    atomic_write_text(
        note_path,
        text,
        backup=not created,
        allow_transient=workspace_store.allow_transient(),
    )
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


def _load_observation_history(workspace: Path) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    connected = workspace / "connected"
    if not connected.is_dir():
        return []
    for path in sorted(connected.glob("*/observations/*.jsonl")):
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
            _validate_observation(observation)
            by_id[observation["observation_id"]] = observation
    return list(by_id.values())


def _merge_source_rows(
    workspace: Path, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    path = project_memory_path(workspace)
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    if path.is_file():
        current, _ = _upgrade_project_view(load_json_object(path))
        for source in current.get("sources", []):
            if (
                isinstance(source, dict)
                and isinstance(source.get("provider"), str)
                and isinstance(source.get("source_id"), str)
            ):
                merged[(source["provider"], source["source_id"])] = source
    for source in rows:
        merged[(source["provider"], source["source_id"])] = source
    return sorted(merged.values(), key=lambda source: (source["provider"], source["source_id"]))


def _compile_project_view(
    *,
    workspace_id: str,
    workspace_name: str,
    captured_at: str,
    source_rows: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    connector_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    sections: dict[str, list[dict[str, Any]]] = {
        key: []
        for key in ("updates", "decisions", "next_actions", "people", "artifacts")
    }
    source_by_id = {
        (row["provider"], row["source_id"]): row for row in source_rows
    }
    people_seen: set[tuple[str, str]] = set()
    for observation in sorted(
        observations, key=lambda item: item["occurred_at"], reverse=True
    ):
        source_id = observation["object"]["attributes"].get("source_id")
        provider = observation["source"]["provider"]
        source_key = (provider, source_id)
        if source_key not in source_by_id:
            continue
        source_row = source_by_id[source_key]
        base = {
            "occurred_at": observation["occurred_at"],
            "source_id": source_id,
            "source_note": source_row["source_note"],
            "snapshot_ref": observation["provenance"]["snapshot_ref"],
        }
        excerpt = observation["content"]["text_excerpt"]
        if observation["object"]["object_type"] == "document":
            sections["artifacts"].append(
                {
                    **base,
                    "text": observation["content"]["title"] or source_row["label"],
                    "classification": "artifact_metadata",
                }
            )
            if excerpt:
                bucket, value = _classified(excerpt[:500])
                sections[bucket].append(
                    {
                        **base,
                        "text": value,
                        "classification": (
                            "explicit_marker" if bucket != "updates" else "source_excerpt"
                        ),
                    }
                )
        elif excerpt:
            bucket, value = _classified(excerpt)
            sections[bucket].append(
                {
                    **base,
                    "text": value,
                    "classification": (
                        "explicit_marker" if bucket != "updates" else "source_excerpt"
                    ),
                }
            )
        for actor in observation["actors"]:
            person_key = (provider, actor["external_id"])
            if person_key in people_seen:
                continue
            people_seen.add(person_key)
            sections["people"].append(
                {
                    **base,
                    "text": actor["display_name"] or actor["external_id"],
                    "classification": "observed_actor",
                }
            )
    for key in sections:
        sections[key] = sections[key][:20]
    document = {
        "schema_version": 1,
        "workspace": {"id": workspace_id, "name": workspace_name},
        "generated_at": captured_at,
        "status": "ready" if source_rows else "empty",
        "notice": "这是由已保存来源证据生成的可重建项目视图；显式标记不等同于已确认的正式决策。",
        "connectors": connector_summaries,
        "sections": sections,
        "sources": source_rows,
    }
    errors = validate(document, load_json_object(PROJECT_VIEW_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("Project Memory 视图协议校验失败：" + "; ".join(errors))
    return document


def load_project_memory(
    workspace_id: str, *, root: Path | None = None
) -> dict[str, Any]:
    workspace, manifest = workspace_store.load_manifest(workspace_id, root=root)
    path = project_memory_path(workspace)
    if path.is_file():
        document, upgraded = _upgrade_project_view(load_json_object(path))
        errors = validate(document, load_json_object(PROJECT_VIEW_SCHEMA))
        if errors:
            raise MemoryWorkspaceError("Project Memory 视图协议校验失败：" + "; ".join(errors))
        if upgraded:
            atomic_write_json(
                path,
                document,
                backup=True,
                allow_transient=workspace_store.allow_transient(),
            )
        return document
    summaries = connector_protocol.list_connector_summaries(workspace_id, root=root)
    document = {
        "schema_version": 1,
        "workspace": {"id": workspace_id, "name": manifest["workspace"]["name"]},
        "generated_at": None,
        "status": "empty",
        "notice": "尚无已同步的项目证据。可以选择一个外部连接器，并明确映射这个项目的来源。",
        "connectors": summaries,
        "sections": {
            key: []
            for key in ("updates", "decisions", "next_actions", "people", "artifacts")
        },
        "sources": [],
    }
    errors = validate(document, load_json_object(PROJECT_VIEW_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("Project Memory 视图协议校验失败：" + "; ".join(errors))
    return document


def persist_sync_bundle(
    workspace_id: str,
    *,
    provider: str,
    connector_id: str,
    captured_at: str,
    trigger: str,
    coverage: dict[str, str],
    snapshots: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    limitations: list[str],
    root: Path | None = None,
) -> dict[str, Any]:
    """Persist one adapter bundle; adapters never write canonical memory directly."""

    clean_provider = connector_protocol.validate_provider(provider)
    captured = connector_protocol.parse_datetime(captured_at)
    stamp = _stamp(captured)
    if not snapshots:
        raise MemoryWorkspaceError("Sync Bundle 至少需要一个已确认来源快照。")
    workspace, manifest_meta = workspace_store.load_manifest(workspace_id, root=root)
    expected_refs: set[str] = set()
    snapshot_rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        source = snapshot.get("source")
        raw = snapshot.get("raw")
        ref = snapshot.get("snapshot_ref")
        if not isinstance(source, dict) or not isinstance(raw, bytes) or not isinstance(ref, str):
            raise MemoryWorkspaceError("Sync Bundle snapshot 缺少 source、raw bytes 或 snapshot_ref。")
        if source.get("provider") != clean_provider:
            raise MemoryWorkspaceError("Sync Bundle source provider 与批次 provider 不一致。")
        expected_prefix = f"connected/{clean_provider}/"
        if not ref.startswith(expected_prefix):
            raise MemoryWorkspaceError("Sync Bundle snapshot_ref 超出当前 provider 目录。")
        expected_refs.add(ref)
        snapshot_rows.append(
            {
                "source_id": source["source_id"],
                "kind": source["kind"],
                "external_id": source["external_id"],
                "label": source["label"],
                "command_category": snapshot["command_category"],
                "snapshot_ref": ref,
                "result_count": int(snapshot["result_count"]),
                "content_hash": "sha256:" + hashlib.sha256(raw).hexdigest(),
            }
        )
    for observation in observations:
        _validate_observation(observation)
        if observation["source"]["provider"] != clean_provider:
            raise MemoryWorkspaceError("Observation provider 与 Sync Bundle 不一致。")
        if observation["source"]["workspace_id"] != workspace_id:
            raise MemoryWorkspaceError("Observation Workspace 与 Sync Bundle 不一致。")
        if observation["provenance"]["snapshot_ref"] not in expected_refs:
            raise MemoryWorkspaceError("Observation 引用了本批次之外的 snapshot_ref。")
    manifest_document = {
        "schema_version": 1,
        "provider": clean_provider,
        "workspace_id": workspace_id,
        "captured_at": connector_protocol.format_datetime(captured),
        "trigger": trigger,
        "time_coverage": coverage,
        "complete": True,
        "snapshots": snapshot_rows,
        "limitations": limitations,
    }
    errors = validate(manifest_document, load_json_object(MANIFEST_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("Sync Manifest 校验失败：" + "; ".join(errors))

    source_rows: list[dict[str, Any]] = []
    for snapshot, snapshot_row in zip(snapshots, snapshot_rows):
        write_bytes_once(
            workspace / snapshot_row["snapshot_ref"],
            snapshot["raw"],
            allow_transient=workspace_store.allow_transient(),
        )
        note_ref, created = _find_or_create_source_note(
            workspace,
            snapshot["source"],
            snapshot_ref_value=snapshot_row["snapshot_ref"],
            captured_at=manifest_document["captured_at"],
            coverage=coverage,
            content_hash=snapshot_row["content_hash"],
        )
        if created:
            index_path = workspace / "wiki" / "index.md"
            current_index = index_path.read_text(encoding="utf-8")
            atomic_write_text(
                index_path,
                workspace_store._append_source_to_index(
                    current_index, Path(note_ref).stem, snapshot["source"]["label"]
                ),
                backup=False,
                allow_transient=workspace_store.allow_transient(),
            )
        source_rows.append(
            {
                "source_id": snapshot["source"]["source_id"],
                "provider": clean_provider,
                "kind": snapshot["source"]["kind"],
                "label": snapshot["source"]["label"],
                "external_id": snapshot["source"]["external_id"],
                "source_note": note_ref,
                "latest_snapshot_ref": snapshot_row["snapshot_ref"],
                "captured_at": manifest_document["captured_at"],
            }
        )

    observation_ref = (
        f"connected/{clean_provider}/observations/{stamp}-{workspace_id}.jsonl"
    )
    observation_bytes = "".join(
        json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
        for item in observations
    ).encode("utf-8")
    write_bytes_once(
        workspace / observation_ref,
        observation_bytes,
        allow_transient=workspace_store.allow_transient(),
    )
    manifest_ref = f"connected/{clean_provider}/manifests/{stamp}-{workspace_id}.json"
    write_bytes_once(
        workspace / manifest_ref,
        (json.dumps(manifest_document, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        ),
        allow_transient=workspace_store.allow_transient(),
    )
    checkpoint = connector_protocol.record_checkpoint(
        workspace_id,
        provider=clean_provider,
        connector_id=connector_id,
        coverage_start=coverage["start"],
        coverage_end=coverage["end"],
        snapshot_ref=manifest_ref,
        trigger=trigger,
        root=root,
        now=manifest_document["captured_at"],
    )
    merged_sources = _merge_source_rows(workspace, source_rows)
    view = _compile_project_view(
        workspace_id=workspace_id,
        workspace_name=manifest_meta["workspace"]["name"],
        captured_at=manifest_document["captured_at"],
        source_rows=merged_sources,
        observations=_load_observation_history(workspace),
        connector_summaries=connector_protocol.list_connector_summaries(
            workspace_id, root=root
        ),
    )
    atomic_write_json(
        project_memory_path(workspace),
        view,
        backup=True,
        allow_transient=workspace_store.allow_transient(),
    )
    log_path = workspace / "wiki" / "log.md"
    log_text = log_path.read_text(encoding="utf-8")
    atomic_write_text(
        log_path,
        log_text.rstrip()
        + f"\n\n## {manifest_document['captured_at']} — Connector project sync\n\n"
        + f"Captured {len(snapshot_rows)} mapped `{clean_provider}` sources in `{manifest_ref}`.\n",
        backup=False,
        allow_transient=workspace_store.allow_transient(),
    )
    index.rebuild_index(workspace_id, root=root)
    return {
        "workspace_id": workspace_id,
        "provider": clean_provider,
        "status": "synced",
        "mapped_sources": len(snapshot_rows),
        "observations": len(observations),
        "manifest_ref": manifest_ref,
        "observation_ref": observation_ref,
        "project_memory_path": str(project_memory_path(workspace)),
        "checkpoint": checkpoint,
        "external_read_performed": True,
    }
