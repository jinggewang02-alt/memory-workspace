"""Read model for the unified local Memory Home UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import connectors, home, profile, workspace
from .io import MemoryWorkspaceError
from .memory_refs import personal_profile_reference
from .schema import load_json_object, validate


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
HOME_VIEW_SCHEMA = PACKAGE_ROOT / "schemas" / "memory-home-view.schema.json"
MAX_DOCUMENT_CHARS = 32_000
PERSONAL_DOCUMENTS = (
    ("work_overview", "工作总览", "personal/work/overview.md", "work"),
    ("work_portfolio", "项目组合", "personal/work/portfolio.md", "work"),
    (
        "preferences_confirmed",
        "已确认偏好",
        "personal/preferences/confirmed.md",
        "preferences",
    ),
    (
        "preferences_tentative",
        "待确认模式",
        "personal/preferences/tentative.md",
        "preferences",
    ),
)
PERSONAL_COLLECTIONS = {
    "people": "personal/work/people",
    "themes": "personal/work/themes",
    "timeline": "personal/work/timeline",
    "captures": "personal/captures",
}


def _resolved_home(memory_home: Path | None) -> Path:
    return (memory_home or home.home_path()).resolve(strict=False)


def _resolved_profile(memory_home: Path, profile_path: Path | None) -> Path:
    return (
        profile_path
        if profile_path is not None
        else memory_home / "personal" / "profile" / "exact.json"
    ).resolve(strict=False)


def _resolved_workspaces_root(
    memory_home: Path, workspaces_root: Path | None
) -> Path:
    return (workspaces_root or memory_home / "workspaces").resolve(strict=False)


def _home_id(root: Path) -> str:
    manifest = root / home.HOME_MANIFEST
    if not manifest.is_file():
        return "default"
    try:
        document = load_json_object(manifest)
    except (OSError, ValueError, json.JSONDecodeError):
        return "default"
    home_meta = document.get("home")
    if not isinstance(home_meta, dict):
        return "default"
    value = home_meta.get("id")
    return str(value or "default")


def _profile_snapshot(path: Path) -> dict[str, Any]:
    document = profile.load_store(path)
    updated_at = document.get("updated_at") if document is not None else None
    items = []
    for item in profile.list_items(path):
        items.append(
            {
                **item,
                "masked": True,
                "memory_reference": personal_profile_reference(item["key"]),
            }
        )
    return {
        "count": len(items),
        "updated_at": updated_at,
        "items": items,
        "values_masked": True,
        "supports_single_value_edit": True,
        "supports_structured_entry_edit": False,
    }


def _document_snapshot(root: Path, identifier: str, label: str, relative: str, kind: str) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        return {
            "id": identifier,
            "label": label,
            "kind": kind,
            "path": relative,
            "exists": False,
            "empty": True,
            "content": "",
            "truncated": False,
            "editable_in_ui": False,
        }
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise MemoryWorkspaceError(f"无法读取 Personal Memory 页面 {relative}：{exc}") from exc
    starter = home.INITIAL_FILES.get(relative)
    truncated = len(content) > MAX_DOCUMENT_CHARS
    return {
        "id": identifier,
        "label": label,
        "kind": kind,
        "path": relative,
        "exists": True,
        "empty": content == starter or not content.strip(),
        "content": content[:MAX_DOCUMENT_CHARS],
        "truncated": truncated,
        "editable_in_ui": False,
    }


def _collection_counts(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, relative in PERSONAL_COLLECTIONS.items():
        directory = root / relative
        counts[name] = (
            sum(1 for path in directory.rglob("*") if path.is_file())
            if directory.is_dir()
            else 0
        )
    return counts


def _workspace_snapshot(root: Path | None) -> list[dict[str, Any]]:
    result = []
    for item in workspace.list_workspaces(root=root):
        workspace_id = str(item["workspace_id"])
        summary = {
            "workspace_id": workspace_id,
            "name": str(item.get("name") or workspace_id),
            "created_at": item.get("created_at"),
            "status": "needs_attention" if item.get("error") else "ready",
            "error": item.get("error"),
            "counts": {"sources": 0, "operations": 0},
        }
        if not item.get("error"):
            inspection = workspace.inspect_workspace(workspace_id, root=root)
            summary["counts"] = inspection["counts"]
            connector = connectors.connector_status(workspace_id, root=root)
            summary["project_memory"] = {
                "status": (
                    "ready"
                    if inspection["index"]["exists"]
                    and (Path(inspection["index"]["path"]).parent / "project-memory.json").is_file()
                    else "empty"
                ),
                "lark_enabled": connector["enabled"],
                "mapped_sources": connector["mapped_sources"],
                "last_sync_at": connector["checkpoint"]["last_success_at"] if connector["checkpoint"] else None,
            }
        else:
            summary["project_memory"] = {
                "status": "empty",
                "lark_enabled": False,
                "mapped_sources": 0,
                "last_sync_at": None,
            }
        result.append(summary)
    return result


def build_home_view(
    *,
    memory_home: Path | None = None,
    profile_path: Path | None = None,
    workspaces_root: Path | None = None,
    review_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build one safe Home-level view without exposing exact profile values."""

    root = _resolved_home(memory_home)
    check = home.check_home(root=root)
    exact_profile = _profile_snapshot(_resolved_profile(root, profile_path))
    documents = [
        _document_snapshot(root, identifier, label, relative, kind)
        for identifier, label, relative, kind in PERSONAL_DOCUMENTS
    ]
    workspaces = _workspace_snapshot(_resolved_workspaces_root(root, workspaces_root))
    counts = review_counts or {
        "proposed": 0,
        "approved": 0,
        "rejected": 0,
        "applied": 0,
    }
    document = {
        "schema_version": 1,
        "home": {
            "id": _home_id(root),
            "path": str(root),
            "status": "ready" if check["status"] == "OK" else "needs_setup",
        },
        "summary": {
            "personal_profile_items": exact_profile["count"],
            "personal_documents": sum(not item["empty"] for item in documents),
            "workspaces": len(workspaces),
            "review_candidates": counts["proposed"],
        },
        "personal": {
            "profile": exact_profile,
            "documents": documents,
            "collections": _collection_counts(root),
        },
        "workspaces": workspaces,
        "review": {"counts": counts},
    }
    errors = validate(document, load_json_object(HOME_VIEW_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("Memory Home UI 数据协议校验失败：" + "; ".join(errors))
    return document
