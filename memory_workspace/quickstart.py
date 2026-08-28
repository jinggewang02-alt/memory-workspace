"""Idempotent first-use preparation for a local Memory Workspace."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import capture, home, onboarding, workspace
from .bootstrap import build_report
from .io import MemoryWorkspaceError


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _paths(report: dict[str, Any], environment: dict[str, str]) -> dict[str, Path]:
    storage = report["capabilities"]["persistent_storage"]
    learning_root, _ = home.personal_learning_path_info(environment)
    return {
        "memory_home": Path(storage["memory_home"]["path"]),
        "profile": Path(storage["profile_memory"]["path"]),
        "workspaces": Path(storage["workspaces"]["path"]),
        "capture": Path(storage["capture_learning"]["path"]),
        "learning": learning_root,
    }


def prepare(
    *,
    skill_root: Path = PACKAGE_ROOT,
    environ: dict[str, str] | None = None,
    capability_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check capabilities, initialize durable storage, and return a setup receipt.

    This function never reads external services. History remains optional and is
    represented only by an adapter handoff already present in the capability report.
    """

    environment = dict(os.environ if environ is None else environ)
    report = capability_report or build_report(skill_root, environ=environment)
    if report["status"] != "READY":
        return {
            "schema_version": 2,
            "command": "quickstart",
            "status": report["status"],
            "ready": False,
            "initialized": False,
            "blocker": report["next_actions"][0] if report["next_actions"] else None,
            "next_actions": report["next_actions"],
            "capability_report": report,
        }

    paths = _paths(report, environment)
    initialized = home.init_home(root=paths["memory_home"])
    history_access = report["capabilities"]["history_access"]
    history_file = (
        Path(history_access["source_path"])
        if history_access.get("available") and history_access.get("source_path")
        else None
    )
    try:
        learning = onboarding.get_status(
            root=paths["capture"],
            habits_root=paths["learning"].parent,
            policy_root=paths["learning"],
            history_file=history_file,
            adapter=history_access.get("adapter"),
        )
        if learning["status"] == "ready" and history_file is not None:
            learning = onboarding.run_first_learning(
                root=paths["capture"],
                habits_root=paths["learning"].parent,
                policy_root=paths["learning"],
                history_file=history_file,
                adapter=history_access.get("adapter"),
                days=30,
            )
    except MemoryWorkspaceError as exc:
        learning = {
            "status": "needs_attention",
            "error": str(exc),
        }
    workspaces = workspace.list_workspaces(root=paths["workspaces"])
    capture_check = capture.doctor(root=paths["capture"])

    notices: list[dict[str, str]] = []
    if learning["status"] == "awaiting_review":
        notices.append(
            {
                "kind": "history_review_ready",
                "message": "A Query-habit draft is ready for review.",
            }
        )
    elif learning["status"] == "needs_attention":
        notices.append(
            {
                "kind": "history_source_attention",
                "message": (
                    "The configured history handoff needs attention; core local memory "
                    "is already ready."
                ),
            }
        )

    python = report["capabilities"]["python"]
    ui_command = [
        str(python.get("executable") or "<python-3.10+>"),
        str((skill_root / "scripts" / "ui.py").resolve()),
    ]

    return {
        "schema_version": 2,
        "command": "quickstart",
        "status": "READY",
        "ready": True,
        "initialized": {
            "created": initialized["created"],
            "status": initialized["status"],
            "home_path": initialized["home_path"],
        },
        "paths": {name: str(path) for name, path in paths.items()},
        "workspace_count": len(workspaces),
        "history_learning": {
            "status": learning["status"],
            "optional": True,
            "available": bool(history_access.get("available")),
            "error": learning.get("error"),
        },
        "external_connectors": {"lark": "disabled_by_default"},
        "capture": capture_check,
        "ui": {
            "status": "not_started",
            "url": None,
            "launch_command": ui_command,
            "health_path": "/api/health",
            "loopback_only": True,
            "requires_same_device_browser": True,
            "requires_long_lived_process": True,
        },
        "message": "Memory Workspace is ready. Continue using the Agent normally.",
        "primary_action": {
            "kind": "continue",
            "label": "Continue using the Agent",
        },
        "notices": notices,
        "next_actions": [
            "Continue using the Agent normally; contextual memory workflows activate when needed."
        ],
        "capability_status": report["status"],
    }


def exit_code(result: dict[str, Any]) -> int:
    if result.get("ready"):
        return 0
    if result.get("status") == "UNSUPPORTED":
        return 2
    return 1
