"""Unified, local-first Memory Home layout and non-destructive migration."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .io import (
    MemoryWorkspaceError,
    atomic_write_json,
    atomic_write_text,
    ensure_safe_write_path,
    nearest_existing_parent,
    transient_reason,
)
from .schema import load_json_object, validate


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
HOME_SCHEMA = PACKAGE_ROOT / "schemas" / "memory-home.schema.json"
HOME_MANIFEST = "memory-home.json"

REQUIRED_DIRECTORIES = (
    "personal/profile",
    "personal/work/people",
    "personal/work/themes",
    "personal/work/timeline",
    "personal/preferences",
    "personal/learning/policies",
    "personal/learning/policy-activations",
    "personal/captures",
    "workspaces",
    "system/capture",
    "system/operations",
    "system/index",
    "system/connectors",
    "system/migrations",
)

INITIAL_FILES = {
    "personal/index.md": (
        "# Personal Memory\n\n"
        "This is the user-centered entry point across all Workspaces.\n\n"
        "- [[work/overview|Work overview]]\n"
        "- [[work/portfolio|Workspace portfolio]]\n"
        "- [[preferences/confirmed|Confirmed preferences]]\n"
        "- [[preferences/tentative|Tentative patterns]]\n"
    ),
    "personal/work/overview.md": (
        "# Work overview\n\n"
        "A maintained, user-centered synthesis of current responsibilities, priorities, "
        "collaborators, and open threads. Material claims should link to Workspace evidence "
        "or an owner capture.\n"
    ),
    "personal/work/portfolio.md": (
        "# Workspace portfolio\n\n"
        "A cross-Workspace view of the user's role, status, next action, and attention level.\n"
    ),
    "personal/preferences/confirmed.md": (
        "# Confirmed preferences\n\n"
        "Only preferences explicitly confirmed by the user belong here.\n"
    ),
    "personal/preferences/tentative.md": (
        "# Tentative patterns\n\n"
        "Observed patterns remain tentative until the user confirms them.\n"
    ),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    return dict(os.environ if environ is None else environ)


def _user_home(environ: dict[str, str]) -> Path:
    configured = environ.get("HOME")
    home = Path(os.path.abspath(os.path.expanduser(configured))) if configured else Path.home()
    if str(home) in {"", "."}:
        raise MemoryWorkspaceError("无法解析用户目录；请用 MEMORY_HOME 指定持久目录。")
    return home


def home_path_info(environ: dict[str, str] | None = None) -> tuple[Path, str]:
    environment = _environment(environ)
    explicit = environment.get("MEMORY_HOME")
    if explicit:
        return Path(os.path.abspath(os.path.expanduser(explicit))), "MEMORY_HOME"
    return _user_home(environment) / ".memory-home", "default-home"


def home_path() -> Path:
    return home_path_info()[0]


def exact_profile_path_info(environ: dict[str, str] | None = None) -> tuple[Path, str]:
    environment = _environment(environ)
    explicit_file = environment.get("PMEM_FILE")
    if explicit_file:
        return Path(os.path.abspath(os.path.expanduser(explicit_file))), "PMEM_FILE"
    explicit_dir = environment.get("PMEM_DIR")
    if explicit_dir:
        directory = Path(os.path.abspath(os.path.expanduser(explicit_dir)))
        return directory / "store.json", "PMEM_DIR"
    root, _ = home_path_info(environment)
    return root / "personal" / "profile" / "exact.json", "memory-home"


def workspaces_path_info(environ: dict[str, str] | None = None) -> tuple[Path, str]:
    environment = _environment(environ)
    explicit = environment.get("MWORK_WORKSPACES_DIR")
    if explicit:
        return Path(os.path.abspath(os.path.expanduser(explicit))), "MWORK_WORKSPACES_DIR"
    root, _ = home_path_info(environment)
    return root / "workspaces", "memory-home"


def capture_path_info(environ: dict[str, str] | None = None) -> tuple[Path, str]:
    environment = _environment(environ)
    explicit = environment.get("MWORK_CAPTURE_DIR")
    if explicit:
        return Path(os.path.abspath(os.path.expanduser(explicit))), "MWORK_CAPTURE_DIR"
    root, _ = home_path_info(environment)
    return root / "system" / "capture", "memory-home"


def personal_learning_path_info(
    environ: dict[str, str] | None = None,
) -> tuple[Path, str]:
    environment = _environment(environ)
    root, _ = home_path_info(environment)
    return root / "personal" / "learning", "memory-home"


def _allow_transient() -> bool:
    return (
        os.environ.get("MEMORY_HOME_ALLOW_TRANSIENT") == "1"
        or os.environ.get("MWORK_ALLOW_TRANSIENT") == "1"
    )


def _validate_manifest(document: dict[str, Any]) -> None:
    errors = validate(document, load_json_object(HOME_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("Memory Home 配置校验失败：" + "; ".join(errors))


def _manifest(home_id: str, language: str, timezone_name: str) -> dict[str, Any]:
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-")
    if not home_id or any(character not in allowed for character in home_id):
        raise MemoryWorkspaceError("Memory Home id 只能使用小写字母、数字和连字符。")
    document = {
        "schema_version": 1,
        "home": {"id": home_id, "created_at": now_iso()},
        "layout": {"personal": "personal", "workspaces": "workspaces", "system": "system"},
        "defaults": {
            "language": language,
            "timezone": timezone_name,
            "review_mode": "proposed_changes",
        },
    }
    _validate_manifest(document)
    return document


def init_home(
    *,
    root: Path | None = None,
    home_id: str = "default",
    language: str = "zh-CN",
    timezone_name: str = "Asia/Shanghai",
) -> dict[str, Any]:
    target = (root or home_path()).resolve(strict=False)
    ensure_safe_write_path(target, allow_transient=_allow_transient())
    manifest_path = target / HOME_MANIFEST
    created = not manifest_path.exists()
    if manifest_path.exists():
        try:
            _validate_manifest(load_json_object(manifest_path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise MemoryWorkspaceError(f"无法读取 Memory Home 配置 {manifest_path}：{exc}") from exc
    else:
        atomic_write_json(
            manifest_path,
            _manifest(home_id, language, timezone_name),
            backup=False,
            allow_transient=_allow_transient(),
        )
    try:
        for relative in REQUIRED_DIRECTORIES:
            (target / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise MemoryWorkspaceError(f"无法创建 Memory Home 目录 {target}：{exc}") from exc
    for relative, content in INITIAL_FILES.items():
        path = target / relative
        if not path.exists():
            atomic_write_text(path, content, backup=False, allow_transient=_allow_transient())
    result = check_home(root=target)
    if result["status"] != "OK":
        raise MemoryWorkspaceError("Memory Home 初始化后校验失败：" + "; ".join(result["errors"]))
    return {**result, "created": created}


def _legacy_roots(
    *,
    environ: dict[str, str] | None = None,
    legacy_personal_root: Path | None = None,
    legacy_runtime_root: Path | None = None,
) -> tuple[Path, Path]:
    environment = _environment(environ)
    user_home = _user_home(environment)
    return (
        legacy_personal_root or user_home / ".personal-memory",
        legacy_runtime_root or user_home / ".memory-workspace",
    )


def check_home(
    *, root: Path | None = None, environ: dict[str, str] | None = None
) -> dict[str, Any]:
    target, source = home_path_info(environ) if root is None else (root, "argument")
    target = target.resolve(strict=False)
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = target / HOME_MANIFEST
    if not manifest_path.is_file():
        errors.append(f"missing file: {HOME_MANIFEST}")
    else:
        try:
            _validate_manifest(load_json_object(manifest_path))
        except (OSError, ValueError, json.JSONDecodeError, MemoryWorkspaceError) as exc:
            errors.append(f"manifest: {exc}")
    for relative in REQUIRED_DIRECTORIES:
        if not (target / relative).is_dir():
            errors.append(f"missing directory: {relative}")
    for relative in INITIAL_FILES:
        if not (target / relative).is_file():
            errors.append(f"missing file: {relative}")
    legacy_personal, legacy_runtime = _legacy_roots(environ=environ)
    legacy = [
        str(path)
        for path in (legacy_personal, legacy_runtime)
        if path.exists() and path.resolve(strict=False) != target
    ]
    if legacy:
        warnings.append("发现旧记忆目录；可先运行 migration-plan，再显式执行 migrate。")
    reason = transient_reason(target)
    if reason:
        warnings.append(reason + "；正式写入仅允许显式测试覆盖。")
    return {
        "home_path": str(target),
        "source": source,
        "initialized": manifest_path.is_file(),
        "status": "FAILED" if errors else "OK",
        "errors": errors,
        "warnings": warnings,
        "legacy_roots": legacy,
        "paths": {
            "personal": str(target / "personal"),
            "exact_profile": str(target / "personal" / "profile" / "exact.json"),
            "workspaces": str(target / "workspaces"),
            "system": str(target / "system"),
        },
        "counts": {
            "workspaces": len(list((target / "workspaces").glob("*/llm-wiki.json")))
            if (target / "workspaces").is_dir()
            else 0,
            "personal_pages": len(list((target / "personal").rglob("*.md")))
            if (target / "personal").is_dir()
            else 0,
        },
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _tree_entries(
    source: Path,
    destination: Path,
    category: str,
    *,
    exclude_roots: tuple[str, ...] = (),
) -> list[tuple[Path, Path, str]]:
    if not source.is_dir():
        return []
    return [
        (path, destination / path.relative_to(source), category)
        for path in sorted(source.rglob("*"))
        if (path.is_file() or path.is_symlink())
        and not any(
            path.relative_to(source).parts
            and path.relative_to(source).parts[0] == excluded
            for excluded in exclude_roots
        )
    ]


def _has_symlink_component(path: Path, root: Path) -> bool:
    current = path
    while current != root:
        if current.is_symlink():
            return True
        if current.parent == current:
            return True
        current = current.parent
    return root.is_symlink()


def migration_plan(
    *,
    root: Path | None = None,
    environ: dict[str, str] | None = None,
    legacy_personal_root: Path | None = None,
    legacy_runtime_root: Path | None = None,
) -> dict[str, Any]:
    target = (root or home_path_info(environ)[0]).resolve(strict=False)
    legacy_personal, legacy_runtime = _legacy_roots(
        environ=environ,
        legacy_personal_root=legacy_personal_root,
        legacy_runtime_root=legacy_runtime_root,
    )
    entries: list[tuple[Path, Path, str]] = []
    legacy_profile = legacy_personal / "store.json"
    if legacy_profile.is_file() or legacy_profile.is_symlink():
        entries.append(
            (
                legacy_profile,
                target / "personal" / "profile" / "exact.json",
                "exact_profile",
            )
        )
    entries.extend(
        _tree_entries(
            legacy_personal / "workspaces", target / "workspaces", "workspaces"
        )
    )
    entries.extend(
        _tree_entries(
            legacy_runtime / "capture",
            target / "system" / "capture",
            "system_capture",
            exclude_roots=("learning", "policies", "policy-activations"),
        )
    )
    entries.extend(
        _tree_entries(
            legacy_runtime / "capture" / "learning",
            target / "personal" / "learning",
            "personal_learning",
        )
    )
    entries.extend(
        _tree_entries(
            legacy_runtime / "capture" / "policies",
            target / "personal" / "learning" / "policies",
            "personal_policies",
        )
    )
    entries.extend(
        _tree_entries(
            legacy_runtime / "capture" / "policy-activations",
            target / "personal" / "learning" / "policy-activations",
            "personal_policy_activations",
        )
    )
    planned: list[dict[str, Any]] = []
    for source_path, destination, category in entries:
        if source_path.is_symlink() or _has_symlink_component(destination, target):
            status = "blocked_symlink"
            content_hash = None
        else:
            content_hash = _sha256(source_path)
            if not destination.exists():
                status = "copy"
            elif destination.is_file() and _sha256(destination) == content_hash:
                status = "already_present"
            else:
                status = "conflict"
        planned.append(
            {
                "category": category,
                "source": str(source_path),
                "target": str(destination),
                "status": status,
                "content_hash": content_hash,
            }
        )
    counts = {
        name: sum(item["status"] == name for item in planned)
        for name in ("copy", "already_present", "conflict", "blocked_symlink")
    }
    return {
        "home_path": str(target),
        "legacy_personal_root": str(legacy_personal),
        "legacy_runtime_root": str(legacy_runtime),
        "mode": "copy_only",
        "deletes_source": False,
        "entries": planned,
        "counts": counts,
        "can_apply": counts["conflict"] == 0 and counts["blocked_symlink"] == 0,
    }


def _copy_once(source: Path, target: Path) -> None:
    ensure_safe_write_path(target, allow_transient=_allow_transient())
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with source.open("rb") as input_handle, target.open("xb") as output_handle:
            for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
                output_handle.write(chunk)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        try:
            target.chmod(0o600)
        except OSError:
            pass
    except FileExistsError as exc:
        raise MemoryWorkspaceError(f"迁移目标已存在，拒绝覆盖：{target}") from exc
    except OSError as exc:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise MemoryWorkspaceError(f"无法迁移 {source} 到 {target}：{exc}") from exc


def migrate_legacy(
    *,
    root: Path | None = None,
    environ: dict[str, str] | None = None,
    legacy_personal_root: Path | None = None,
    legacy_runtime_root: Path | None = None,
) -> dict[str, Any]:
    plan = migration_plan(
        root=root,
        environ=environ,
        legacy_personal_root=legacy_personal_root,
        legacy_runtime_root=legacy_runtime_root,
    )
    if not plan["can_apply"]:
        raise MemoryWorkspaceError("迁移计划包含冲突或符号链接；未复制任何文件。")
    target = Path(plan["home_path"])
    init_home(root=target)
    copied: list[dict[str, Any]] = []
    for item in plan["entries"]:
        if item["status"] != "copy":
            continue
        source_path = Path(item["source"])
        target_path = Path(item["target"])
        _copy_once(source_path, target_path)
        if _sha256(target_path) != item["content_hash"]:
            raise MemoryWorkspaceError(f"迁移后哈希校验失败：{target_path}")
        copied.append(item)
    receipt = {
        "schema_version": 1,
        "migration_id": f"migration_{uuid4().hex}",
        "applied_at": now_iso(),
        "mode": "copy_only",
        "source_deleted": False,
        "copied": copied,
        "already_present": [
            item for item in plan["entries"] if item["status"] == "already_present"
        ],
    }
    receipt_path = (
        target / "system" / "migrations" / f"{receipt['migration_id']}.json"
    )
    atomic_write_json(
        receipt_path,
        receipt,
        backup=False,
        allow_transient=_allow_transient(),
    )
    return {
        "home_path": str(target),
        "status": "OK",
        "copied": len(copied),
        "already_present": len(receipt["already_present"]),
        "source_deleted": False,
        "receipt_path": str(receipt_path),
        "check": check_home(root=target),
    }


def doctor(*, root: Path | None = None) -> dict[str, Any]:
    target, source = home_path_info() if root is None else (root, "argument")
    reason = transient_reason(target)
    parent = nearest_existing_parent(target)
    writable = os.access(parent, os.W_OK)
    warnings: list[str] = []
    if reason:
        warnings.append(reason + "；正式写入将被拒绝。")
    if not writable:
        warnings.append(f"现有父目录不可写：{parent}")
    return {
        "home_path": str(target),
        "source": source,
        "exists": target.exists(),
        "nearest_existing_parent": str(parent),
        "filesystem_writable": writable,
        "transient_risk": reason is not None,
        "status": "WARNING" if warnings else "OK",
        "warnings": warnings,
        "note": "沙箱可能仍需对上述确切目录单独授权。",
    }
