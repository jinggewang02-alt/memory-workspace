"""Safe filesystem primitives shared by profile and workspace storage."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


class MemoryWorkspaceError(Exception):
    """An error that is safe to display to a local user."""


def is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def transient_reason(path: Path) -> str | None:
    resolved = Path(os.path.realpath(path))
    candidates = {Path("/tmp"), Path("/private/tmp")}
    for value in (
        tempfile.gettempdir(),
        os.environ.get("TMPDIR"),
        os.environ.get("TEMP"),
        os.environ.get("TMP"),
    ):
        if value:
            candidates.add(Path(os.path.realpath(os.path.expanduser(value))))
    for root in sorted(candidates, key=lambda item: len(str(item)), reverse=True):
        normalized = Path(os.path.realpath(root))
        if is_within(resolved, normalized):
            return f"路径位于临时目录 {normalized}"
    return None


def ensure_safe_write_path(path: Path, *, allow_transient: bool = False) -> None:
    reason = transient_reason(path)
    if reason and not allow_transient:
        raise MemoryWorkspaceError(
            f"{reason}，已拒绝写入。请显式指定本机持久目录；"
            "仅测试时可允许临时目录。"
        )


def nearest_existing_parent(path: Path) -> Path:
    current = path.resolve(strict=False)
    while not current.exists():
        if current.parent == current:
            break
        current = current.parent
    return current


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except json.JSONDecodeError as exc:
        raise MemoryWorkspaceError(
            f"JSON 已损坏（{path}:{exc.lineno}:{exc.colno}），为避免覆盖已停止。"
        ) from exc
    except OSError as exc:
        raise MemoryWorkspaceError(f"无法读取 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise MemoryWorkspaceError(f"JSON 根节点必须是对象（{path}）。")
    return value


def atomic_write_json(
    path: Path,
    data: dict[str, Any],
    *,
    backup: bool = True,
    allow_transient: bool = False,
) -> None:
    ensure_safe_write_path(path, allow_transient=allow_transient)
    directory = path.parent
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
    except OSError as exc:
        raise MemoryWorkspaceError(
            f"无法创建本机目录 {directory}：{exc}。没有回退到临时目录。"
        ) from exc

    temporary = path.with_name(path.name + ".tmp")
    backup_path = path.with_name(path.name + ".bak")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        if backup and path.exists():
            shutil.copy2(path, backup_path)
            try:
                backup_path.chmod(0o600)
            except OSError:
                pass
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise MemoryWorkspaceError(
            f"无法持久化到 {path}：{exc}。没有回退到临时目录。"
        ) from exc
