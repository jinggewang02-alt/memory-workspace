"""Read-only capability negotiation for unknown agent runtimes."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


MINIMUM_PYTHON = (3, 10)
REQUIRED_RESOURCES = (
    "SKILL.md",
    "scripts/capture.py",
    "scripts/store.py",
    "scripts/workspace.py",
    "schemas/query-habits.schema.json",
    "schemas/onboarding-state.schema.json",
    "schemas/workspace.schema.json",
    "schemas/operation.schema.json",
)


def _is_within(path, root):
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def _transient_reason(path, environ):
    resolved = Path(os.path.realpath(str(path)))
    candidates = {Path("/tmp"), Path("/private/tmp")}
    for value in (
        tempfile.gettempdir(),
        environ.get("TMPDIR"),
        environ.get("TEMP"),
        environ.get("TMP"),
    ):
        if value:
            candidates.add(Path(os.path.realpath(os.path.expanduser(value))))
    for root in sorted(candidates, key=lambda item: len(str(item)), reverse=True):
        normalized = Path(os.path.realpath(str(root)))
        if _is_within(resolved, normalized):
            return "path is inside transient directory {0}".format(normalized)
    return None


def _nearest_existing_parent(path):
    current = Path(os.path.abspath(os.path.expanduser(str(path))))
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def _profile_path(environ):
    explicit_file = environ.get("PMEM_FILE")
    if explicit_file:
        return Path(os.path.abspath(os.path.expanduser(explicit_file))), "PMEM_FILE"
    explicit_dir = environ.get("PMEM_DIR")
    if explicit_dir:
        directory = Path(os.path.abspath(os.path.expanduser(explicit_dir)))
        return directory / "store.json", "PMEM_DIR"
    return Path.home() / ".personal-memory" / "store.json", "default-home"


def _workspaces_root(environ):
    explicit = environ.get("MWORK_WORKSPACES_DIR")
    if explicit:
        return Path(os.path.abspath(os.path.expanduser(explicit))), "MWORK_WORKSPACES_DIR"
    return Path.home() / ".personal-memory" / "workspaces", "default-home"


def _capture_root(environ):
    explicit = environ.get("MWORK_CAPTURE_DIR")
    if explicit:
        return Path(os.path.abspath(os.path.expanduser(explicit))), "MWORK_CAPTURE_DIR"
    return Path.home() / ".memory-workspace" / "capture", "default-home"


def _path_report(path, source, environ, parent_target=False):
    target = path if parent_target else path.parent
    parent = _nearest_existing_parent(target)
    reason = _transient_reason(path, environ)
    return {
        "path": str(path),
        "source": source,
        "exists": path.exists(),
        "nearest_existing_parent": str(parent),
        "filesystem_writable": os.access(str(parent), os.W_OK),
        "transient_risk": reason is not None,
        "transient_reason": reason,
    }


def _python_text(version):
    return ".".join(str(item) for item in version[:3])


def build_report(
    skill_root,
    environ=None,
    python_version=None,
    path_probe=None,
):
    """Describe capabilities without creating or modifying any user files."""

    environment = dict(os.environ if environ is None else environ)
    root = Path(skill_root).resolve()
    version = tuple(sys.version_info[:3] if python_version is None else python_version)
    probe = _path_report if path_probe is None else path_probe

    missing = [relative for relative in REQUIRED_RESOURCES if not (root / relative).is_file()]
    profile_path, profile_source = _profile_path(environment)
    workspace_root, workspace_source = _workspaces_root(environment)
    capture_root, capture_source = _capture_root(environment)
    profile = probe(profile_path, profile_source, environment, False)
    workspaces = probe(workspace_root, workspace_source, environment, True)
    capture = probe(capture_root, capture_source, environment, True)

    configured_history = environment.get("MWORK_HISTORY_FILE")
    history_path = (
        Path(os.path.abspath(os.path.expanduser(configured_history)))
        if configured_history
        else None
    )
    history_access = {
        "contract": "agent-visible-normalized-jsonl",
        "configured": history_path is not None,
        "available": history_path is not None and history_path.is_file(),
        "source_path": str(history_path) if history_path is not None else None,
        "adapter": environment.get("MWORK_HISTORY_ADAPTER")
        if history_path is not None
        else None,
        "window_days_maximum": 30,
    }

    python_ready = version >= MINIMUM_PYTHON
    transient = (
        profile["transient_risk"]
        or workspaces["transient_risk"]
        or capture["transient_risk"]
    )
    writable = (
        profile["filesystem_writable"]
        and workspaces["filesystem_writable"]
        and capture["filesystem_writable"]
    )

    if missing:
        status = "UNSUPPORTED"
        mode = "instruction-only"
        actions = ["Restore or reinstall the missing Skill resources before running commands."]
    elif not python_ready:
        status = "NEEDS_RUNTIME"
        mode = "local-setup-required"
        actions = [
            "Provide Python {0}.{1} or newer, then rerun this read-only probe.".format(
                *MINIMUM_PYTHON
            )
        ]
    elif transient:
        status = "NEEDS_PERSISTENT_PATH"
        mode = "local-setup-required"
        actions = [
            "Point PMEM_FILE or PMEM_DIR, MWORK_WORKSPACES_DIR, and MWORK_CAPTURE_DIR to durable storage, then rerun."
        ]
    elif not writable:
        status = "NEEDS_PERMISSION"
        mode = "local-setup-required"
        actions = [
            "Authorize the exact reported parent directories or select other durable paths, then rerun."
        ]
    else:
        status = "READY"
        mode = "local-full"
        actions = [
            "Run the command-specific doctor before the first write; the host may still request permission."
        ]
        if history_access["available"]:
            actions.append(
                "Run capture.py onboarding run; it will use only the configured 30-day history handoff."
            )
        else:
            actions.append(
                "If first-use learning is desired, let the current Agent provide only its visible history through the normalized JSONL adapter contract."
            )

    return {
        "schema_version": 1,
        "probe": "memory-workspace-capabilities",
        "status": status,
        "runtime_mode": mode,
        "write_ready": status == "READY",
        "capabilities": {
            "command_execution": True,
            "python": {
                "available": True,
                "version": _python_text(version),
                "minimum": "{0}.{1}".format(*MINIMUM_PYTHON),
                "meets_minimum": python_ready,
            },
            "skill_resources": {
                "root": str(root),
                "complete": not missing,
                "missing": missing,
            },
            "persistent_storage": {
                "profile_memory": profile,
                "workspaces": workspaces,
                "capture_learning": capture,
            },
            "history_access": history_access,
        },
        "next_actions": actions,
        "limitations": [
            "This probe is read-only and cannot prove host-level sandbox approval.",
            "A runtime that cannot execute this script must not claim local persistence.",
        ],
    }


def exit_code(report):
    if report["status"] == "READY":
        return 0
    if report["status"] == "UNSUPPORTED":
        return 2
    return 1
