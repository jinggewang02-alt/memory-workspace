"""Provider-neutral connector registration, source mapping, and checkpoints.

The Memory Home core knows only this protocol. Provider adapters own external
authentication, commands, pagination, and field conversion.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from . import workspace as workspace_store
from .io import MemoryWorkspaceError, atomic_write_json, is_within
from .schema import load_json_object, validate


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_MAP_SCHEMA = PACKAGE_ROOT / "schemas" / "connector-source-map.schema.json"
CONNECTOR_SCHEMA = PACKAGE_ROOT / "schemas" / "connector-config.schema.json"
CHECKPOINT_SCHEMA = PACKAGE_ROOT / "schemas" / "sync-checkpoint.schema.json"


def format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_datetime(value: str | datetime) -> datetime:
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


def validate_provider(provider: str) -> str:
    clean = provider.strip().lower()
    workspace_store.validate_slug(clean)
    return clean


def config_path(workspace: Path, provider: str) -> Path:
    return workspace / "config" / "connectors" / f"{provider}.json"


def source_map_path(workspace: Path, provider: str) -> Path:
    return workspace / "config" / "connectors" / f"{provider}-sources.json"


def checkpoint_path(workspace: Path, provider: str) -> Path:
    return workspace / ".llm-wiki" / "connectors" / provider / "checkpoint.json"


def _load_json(path: Path, *, label: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return load_json_object(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MemoryWorkspaceError(f"无法读取 {label} {path}：{exc}") from exc


def _load_source_map(path: Path) -> dict[str, Any] | None:
    document = _load_json(path, label="connector source map")
    if document is None:
        return None
    errors = validate(document, load_json_object(SOURCE_MAP_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("Connector source map 校验失败：" + "; ".join(errors))
    return document


def list_sources(
    workspace_id: str,
    *,
    provider: str,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    clean_provider = validate_provider(provider)
    workspace, _ = workspace_store.load_manifest(workspace_id, root=root)
    document = _load_source_map(source_map_path(workspace, clean_provider))
    if document is None:
        return []
    if document["workspace_id"] != workspace_id or document["provider"] != clean_provider:
        raise MemoryWorkspaceError("Connector source map 与 Workspace/provider 不匹配。")
    return list(document["sources"])


def map_source(
    workspace_id: str,
    *,
    provider: str,
    source_id: str,
    kind: str,
    external_id: str,
    locator: str,
    label: str,
    root: Path | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    clean_provider = validate_provider(provider)
    workspace_store.validate_slug(source_id)
    clean_kind = kind.strip().lower()
    clean_external_id = external_id.strip()
    clean_locator = locator.strip()
    clean_label = " ".join(label.split())
    if not clean_kind or not clean_external_id or not clean_locator or not clean_label:
        raise MemoryWorkspaceError("kind、external_id、locator 和 label 均不能为空。")
    workspace, _ = workspace_store.load_manifest(workspace_id, root=root)
    path = source_map_path(workspace, clean_provider)
    existing = _load_source_map(path)
    timestamp = format_datetime(parse_datetime(now or datetime.now(timezone.utc)))
    document = existing or {
        "schema_version": 1,
        "provider": clean_provider,
        "workspace_id": workspace_id,
        "sources": [],
        "updated_at": timestamp,
    }
    if document["workspace_id"] != workspace_id or document["provider"] != clean_provider:
        raise MemoryWorkspaceError("Connector source map 与目标 Workspace/provider 不匹配。")
    source = {
        "source_id": source_id,
        "kind": clean_kind,
        "external_id": clean_external_id,
        "locator": clean_locator,
        "label": clean_label,
        "enabled": True,
        "sync_mode": "direct_execution",
        "added_at": timestamp,
    }
    created = True
    updated = False
    for index, item in enumerate(document["sources"]):
        if item["source_id"] == source_id:
            source["added_at"] = item["added_at"]
            created = False
            updated = item != source
            document["sources"][index] = source
            break
    else:
        document["sources"].append(source)
    document["sources"].sort(key=lambda item: item["source_id"])
    document["updated_at"] = timestamp
    errors = validate(document, load_json_object(SOURCE_MAP_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("Connector source map 校验失败：" + "; ".join(errors))
    atomic_write_json(
        path,
        document,
        backup=existing is not None,
        allow_transient=workspace_store.allow_transient(),
    )
    return {
        "workspace_id": workspace_id,
        "provider": clean_provider,
        "source": source,
        # Keep the historical meaning of `changed`: a new mapping was created.
        # `updated` makes replacement of an existing mapping explicit.
        "changed": created,
        "updated": updated,
        "source_map_path": str(path),
        "external_read_performed": False,
    }


def connector_summary(
    workspace_id: str,
    *,
    provider: str,
    root: Path | None = None,
) -> dict[str, Any]:
    clean_provider = validate_provider(provider)
    workspace, _ = workspace_store.load_manifest(workspace_id, root=root)
    config = _load_json(config_path(workspace, clean_provider), label="connector config")
    checkpoint = _load_json(
        checkpoint_path(workspace, clean_provider), label="connector checkpoint"
    )
    sources = list_sources(workspace_id, provider=clean_provider, root=root)
    coverage = checkpoint.get("coverage", {}) if checkpoint else {}
    return {
        "provider": clean_provider,
        "configured": config is not None,
        "enabled": bool(config and config.get("enabled")),
        "mapped_sources": sum(
            bool(item.get("enabled")) and item.get("sync_mode") == "direct_execution"
            for item in sources
        ),
        "last_success_at": checkpoint.get("last_success_at") if checkpoint else None,
        "coverage_end": coverage.get("end"),
    }


def list_connector_summaries(
    workspace_id: str, *, root: Path | None = None
) -> list[dict[str, Any]]:
    workspace, _ = workspace_store.load_manifest(workspace_id, root=root)
    directory = workspace / "config" / "connectors"
    providers: set[str] = set()
    if directory.is_dir():
        for path in directory.glob("*.json"):
            name = path.name
            providers.add(name[: -len("-sources.json")] if name.endswith("-sources.json") else path.stem)
    checkpoint_root = workspace / ".llm-wiki" / "connectors"
    if checkpoint_root.is_dir():
        providers.update(path.name for path in checkpoint_root.iterdir() if path.is_dir())
    return [
        connector_summary(workspace_id, provider=provider, root=root)
        for provider in sorted(providers)
    ]


def record_checkpoint(
    workspace_id: str,
    *,
    provider: str,
    connector_id: str,
    coverage_start: str | datetime,
    coverage_end: str | datetime,
    snapshot_ref: str,
    trigger: str,
    root: Path | None = None,
    now: str | datetime | None = None,
    high_watermark_external_id: str | None = None,
) -> dict[str, Any]:
    clean_provider = validate_provider(provider)
    start = parse_datetime(coverage_start)
    end = parse_datetime(coverage_end)
    if start > end:
        raise MemoryWorkspaceError("coverage_start 不能晚于 coverage_end。")
    if trigger not in {
        "first_enable",
        "agent_start",
        "idle",
        "manual",
        "event_reconciliation",
    }:
        raise MemoryWorkspaceError(f"未知 trigger：{trigger}")
    relative = PurePosixPath(snapshot_ref)
    if relative.is_absolute() or ".." in relative.parts or not snapshot_ref.strip():
        raise MemoryWorkspaceError("snapshot_ref 必须是 Workspace 内不含 .. 的相对路径。")
    workspace, _ = workspace_store.load_manifest(workspace_id, root=root)
    resolved = (workspace / relative).resolve(strict=False)
    if not is_within(resolved, workspace.resolve(strict=False)) or not resolved.is_file():
        raise MemoryWorkspaceError(f"尚未找到不可变同步清单：{snapshot_ref}")
    path = checkpoint_path(workspace, clean_provider)
    previous = _load_json(path, label="connector checkpoint")
    timestamp = parse_datetime(now or datetime.now(timezone.utc))
    document = {
        "schema_version": 1,
        "connector_id": connector_id,
        "provider": clean_provider,
        "workspace_id": workspace_id,
        "stream_id": "baseline" if previous is None else "daily_incremental",
        "last_attempt_at": format_datetime(timestamp),
        "last_success_at": format_datetime(timestamp),
        "trigger": trigger,
        "coverage": {
            "start": format_datetime(start),
            "end": format_datetime(end),
            "complete": True,
        },
        "high_watermark": {
            "occurred_at": format_datetime(end),
            "external_id": high_watermark_external_id,
        },
        "snapshot_ref": snapshot_ref,
        "error_boundary": None,
    }
    errors = validate(document, load_json_object(CHECKPOINT_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("Connector checkpoint 校验失败：" + "; ".join(errors))
    atomic_write_json(
        path,
        document,
        backup=previous is not None,
        allow_transient=workspace_store.allow_transient(),
    )
    return {
        "workspace_id": workspace_id,
        "provider": clean_provider,
        "checkpoint_path": str(path),
        "coverage": document["coverage"],
    }


def validate_source_maps(workspace: Path, workspace_id: str) -> list[str]:
    errors: list[str] = []
    directory = workspace / "config" / "connectors"
    if not directory.is_dir():
        return errors
    for path in sorted(directory.glob("*-sources.json")):
        try:
            document = _load_source_map(path)
            provider = path.name[: -len("-sources.json")]
            if document and document["workspace_id"] != workspace_id:
                errors.append(f"{path.relative_to(workspace)}: workspace_id mismatch")
            if document and document["provider"] != provider:
                errors.append(f"{path.relative_to(workspace)}: provider/path mismatch")
        except MemoryWorkspaceError as exc:
            errors.append(f"{path.relative_to(workspace)}: {exc}")
    return errors


def validate_connector_files(workspace: Path, workspace_id: str) -> list[str]:
    """Validate provider-neutral connector records without importing adapters."""

    errors = validate_source_maps(workspace, workspace_id)
    config_dir = workspace / "config" / "connectors"
    if config_dir.is_dir():
        for path in sorted(
            item
            for item in config_dir.glob("*.json")
            if not item.name.endswith("-sources.json")
        ):
            try:
                document = _load_json(path, label=f"connector config {path.name}")
                document_errors = (
                    validate(document, load_json_object(CONNECTOR_SCHEMA))
                    if document
                    else []
                )
                errors.extend(
                    f"{path.relative_to(workspace)}: {error}"
                    for error in document_errors
                )
                if document and document.get("workspace_id") != workspace_id:
                    errors.append(
                        f"{path.relative_to(workspace)}: workspace_id mismatch"
                    )
                if document and path.stem != document.get("provider"):
                    errors.append(f"{path.relative_to(workspace)}: provider/file mismatch")
            except MemoryWorkspaceError as exc:
                errors.append(f"{path.relative_to(workspace)}: {exc}")
    checkpoint_root = workspace / ".llm-wiki" / "connectors"
    if checkpoint_root.is_dir():
        for path in sorted(checkpoint_root.glob("*/checkpoint.json")):
            try:
                document = _load_json(
                    path, label=f"sync checkpoint {path.parent.name}"
                )
                document_errors = (
                    validate(document, load_json_object(CHECKPOINT_SCHEMA))
                    if document
                    else []
                )
                errors.extend(
                    f"{path.relative_to(workspace)}: {error}"
                    for error in document_errors
                )
                if document and document.get("workspace_id") != workspace_id:
                    errors.append(
                        f"{path.relative_to(workspace)}: workspace_id mismatch"
                    )
                if document and path.parent.name != document.get("provider"):
                    errors.append(f"{path.relative_to(workspace)}: provider/path mismatch")
            except MemoryWorkspaceError as exc:
                errors.append(f"{path.relative_to(workspace)}: {exc}")
    return errors
