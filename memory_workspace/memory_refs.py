"""Stable, non-sensitive references for memory-backed Agent responses."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote


def personal_profile_reference(key: str, *, index: int | None = None) -> dict[str, Any]:
    """Describe one exact-profile lookup without exposing the stored value or host path."""

    suffix = f"/{index}" if index is not None else ""
    label = f"Personal · {key}"
    if index is not None:
        label += f" · 第 {index} 条"
    return {
        "schema_version": 1,
        "uri": f"memory://personal/profile/{quote(key, safe='')}{suffix}",
        "scope": "personal",
        "kind": "exact_profile",
        "label": label,
        "path": "personal/profile/exact.json",
        "item_key": key,
        "item_index": index,
    }


def workspace_reference(
    workspace_id: str,
    workspace_name: str,
    *,
    kind: str,
    identifier: str,
    title: str,
    path: str,
) -> dict[str, Any]:
    """Describe one canonical Workspace result without leaking an absolute path."""

    return {
        "schema_version": 1,
        "uri": (
            f"memory://workspace/{quote(workspace_id, safe='')}/"
            f"{quote(path, safe='/')}"
        ),
        "scope": "workspace",
        "kind": kind,
        "label": f"Workspace · {workspace_name} · {title}",
        "workspace_id": workspace_id,
        "item_id": identifier,
        "path": path,
    }
