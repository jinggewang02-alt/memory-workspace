"""Small durable preferences owned by the local Memory Home UI."""

from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import home
from .io import MemoryWorkspaceError, atomic_write_json, load_json
from .schema import load_json_object, validate


SCHEMA_VERSION = 1
ONBOARDING_PATHS = {"direct", "history", "provider"}
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
STATE_SCHEMA = PACKAGE_ROOT / "schemas" / "ui-onboarding-state.schema.json"


def state_path(memory_home: Path | None = None) -> Path:
    root = (memory_home or home.home_path()).resolve(strict=False)
    return root / "system" / "ui" / "onboarding.json"


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "completed": False,
        "selected_path": None,
        "completed_at": None,
    }


def _validate(document: dict[str, Any]) -> dict[str, Any]:
    errors = validate(document, load_json_object(STATE_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("UI 引导状态校验失败：" + "; ".join(errors))
    return document


def load_state(memory_home: Path | None = None) -> dict[str, Any]:
    document = load_json(state_path(memory_home))
    if document is None:
        return _default_state()
    return copy.deepcopy(_validate(document))


def complete_onboarding(
    selected_path: str, *, memory_home: Path | None = None
) -> dict[str, Any]:
    if selected_path not in ONBOARDING_PATHS:
        raise MemoryWorkspaceError("请选择直接开始、导入历史或连接工作平台。")
    document = {
        "schema_version": SCHEMA_VERSION,
        "completed": True,
        "selected_path": selected_path,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    _validate(document)
    allow_transient = (
        os.environ.get("MEMORY_HOME_ALLOW_TRANSIENT") == "1"
        or os.environ.get("MWORK_ALLOW_TRANSIENT") == "1"
    )
    atomic_write_json(
        state_path(memory_home),
        document,
        allow_transient=allow_transient,
    )
    return copy.deepcopy(document)
