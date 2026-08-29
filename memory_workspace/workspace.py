"""Local-first project workspace operations shared by the CLI and future UI."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .home import (
    init_home as init_memory_home,
    workspaces_path_info as memory_home_workspaces_path_info,
)
from .io import (
    MemoryWorkspaceError,
    atomic_write_json,
    atomic_write_text,
    ensure_safe_write_path,
    is_within,
    nearest_existing_parent,
    transient_reason,
    write_bytes_once,
)
from .schema import load_json_object, validate


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PACKAGE_ROOT / "schemas"
WORKSPACE_SCHEMA = SCHEMA_DIR / "workspace.schema.json"
OPERATION_SCHEMA = SCHEMA_DIR / "operation.schema.json"
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SOURCE_NOTE_PATTERN = re.compile(r"^S-(\d{3,})\.md$")
WIKI_LINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")

REQUIRED_DIRECTORIES = (
    "raw/inbox",
    "connected",
    "config/projects",
    "wiki/sources",
    "wiki/projects",
    "wiki/topics",
    "wiki/entities",
    "wiki/syntheses",
    ".llm-wiki/index",
    ".llm-wiki/operations",
)
LEGACY_PERSONAL_DIRECTORIES = (
    "personal/captures",
    "personal/reflections",
    "personal/preferences",
)
REQUIRED_FILES = ("llm-wiki.json", "wiki/index.md", "wiki/log.md")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def workspaces_path_info() -> tuple[Path, str]:
    return memory_home_workspaces_path_info()


def workspaces_path() -> Path:
    return workspaces_path_info()[0]


def allow_transient() -> bool:
    return os.environ.get("MWORK_ALLOW_TRANSIENT") == "1"


def validate_slug(slug: str) -> None:
    if SLUG_PATTERN.fullmatch(slug) is None:
        raise MemoryWorkspaceError(
            "workspace id 只能使用小写字母、数字和单个连字符，例如 product-memory。"
        )


def workspace_path(slug: str, root: Path | None = None) -> Path:
    validate_slug(slug)
    return (root or workspaces_path()) / slug


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def file_hash_or_none(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def operation_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"op_{stamp}_{uuid4().hex[:10]}"


def _change(
    workspace: Path,
    path: Path,
    *,
    before_hash: str | None,
) -> dict[str, Any]:
    return {
        "path": path.relative_to(workspace).as_posix(),
        "action": "update" if before_hash else "create",
        "before_hash": before_hash,
        "after_hash": file_hash_or_none(path),
        "diff_path": None,
    }


def _write_operation(
    workspace: Path,
    *,
    operation_type: str,
    input_refs: list[str],
    changes: list[dict[str, Any]],
    checks: list[str],
) -> dict[str, Any]:
    requested_at = now_iso()
    document = {
        "schema_version": 1,
        "operation_id": operation_id(),
        "type": operation_type,
        "status": "applied",
        "requested_at": requested_at,
        "actor": "owner_via_cli",
        "input_refs": input_refs,
        "changes": changes,
        "validation": {"status": "passed", "checks": checks, "errors": []},
        "approval": {
            "status": "approved",
            "decided_at": requested_at,
            "actor": "owner_via_cli",
        },
    }
    errors = validate(document, load_json_object(OPERATION_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("操作清单校验失败：" + "; ".join(errors))
    path = workspace / ".llm-wiki" / "operations" / f"{document['operation_id']}.json"
    atomic_write_json(path, document, backup=False, allow_transient=allow_transient())
    return {"operation_id": document["operation_id"], "operation_path": str(path)}


def _initial_index(name: str) -> str:
    return (
        f"# {name}\n\n"
        "This is the maintained entry point for the workspace.\n\n"
        "## Sources\n\n"
        "No sources captured yet.\n"
    )


def _initial_log(name: str, created_at: str) -> str:
    return (
        "# Activity log\n\n"
        f"## {created_at} — workspace initialized\n\n"
        f"Created workspace `{name}` with the standard local-first layout.\n"
    )


def init_workspace(
    slug: str,
    *,
    name: str,
    language: str = "zh-CN",
    timezone_name: str = "Asia/Shanghai",
    review_mode: str = "proposed_changes",
    root: Path | None = None,
) -> dict[str, Any]:
    if not name.strip():
        raise MemoryWorkspaceError("workspace name 不能为空。")
    if review_mode not in {"direct", "proposed_changes"}:
        raise MemoryWorkspaceError("review mode 必须是 direct 或 proposed_changes。")
    if root is None:
        default_root, source = workspaces_path_info()
        if source == "memory-home":
            init_memory_home(root=default_root.parent)
    workspace = workspace_path(slug, root)
    if workspace.exists():
        raise MemoryWorkspaceError(f"workspace 已存在，拒绝覆盖：{workspace}")
    transient_ok = allow_transient()
    created_at = now_iso()
    manifest = {
        "schema_version": 2,
        "scope": "workspace",
        "workspace": {"id": slug, "name": name.strip(), "created_at": created_at},
        "defaults": {
            "language": language,
            "timezone": timezone_name,
            "review_mode": review_mode,
        },
    }
    errors = validate(manifest, load_json_object(WORKSPACE_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("workspace 配置校验失败：" + "; ".join(errors))

    ensure_safe_write_path(workspace, allow_transient=transient_ok)
    try:
        workspace.mkdir(mode=0o700, parents=True)
        for relative in REQUIRED_DIRECTORIES:
            (workspace / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise MemoryWorkspaceError(f"无法创建 workspace {workspace}：{exc}") from exc

    manifest_path = workspace / "llm-wiki.json"
    index_path = workspace / "wiki" / "index.md"
    log_path = workspace / "wiki" / "log.md"
    atomic_write_json(manifest_path, manifest, backup=False, allow_transient=transient_ok)
    atomic_write_text(index_path, _initial_index(name.strip()), backup=False, allow_transient=transient_ok)
    atomic_write_text(log_path, _initial_log(name.strip(), created_at), backup=False, allow_transient=transient_ok)
    changes = [
        _change(workspace, manifest_path, before_hash=None),
        _change(workspace, index_path, before_hash=None),
        _change(workspace, log_path, before_hash=None),
    ]
    operation = _write_operation(
        workspace,
        operation_type="init",
        input_refs=[],
        changes=changes,
        checks=["workspace-schema", "required-layout"],
    )
    from .index import rebuild_index

    index_result = rebuild_index(slug, root=root)
    return {
        "workspace_id": slug,
        "name": name.strip(),
        "workspace_path": str(workspace),
        "manifest_path": str(manifest_path),
        "index_path": index_result["index_path"],
        **operation,
    }


def load_manifest(slug: str, *, root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    workspace = workspace_path(slug, root)
    manifest_path = workspace / "llm-wiki.json"
    if not manifest_path.is_file():
        raise MemoryWorkspaceError(f"未找到 workspace：{slug}（缺少 {manifest_path}）")
    try:
        manifest = load_json_object(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MemoryWorkspaceError(f"无法读取 workspace 配置 {manifest_path}：{exc}") from exc
    return workspace, manifest


def list_workspaces(*, root: Path | None = None) -> list[dict[str, Any]]:
    directory = root or workspaces_path()
    if not directory.exists():
        return []
    result: list[dict[str, Any]] = []
    try:
        candidates = sorted(path for path in directory.iterdir() if path.is_dir())
    except OSError as exc:
        raise MemoryWorkspaceError(f"无法读取 workspace 目录 {directory}：{exc}") from exc
    for candidate in candidates:
        manifest_path = candidate / "llm-wiki.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = load_json_object(manifest_path)
            meta = manifest.get("workspace", {})
            result.append(
                {
                    "workspace_id": meta.get("id", candidate.name),
                    "name": meta.get("name", candidate.name),
                    "created_at": meta.get("created_at"),
                    "workspace_path": str(candidate),
                }
            )
        except (OSError, ValueError, json.JSONDecodeError):
            result.append(
                {
                    "workspace_id": candidate.name,
                    "name": candidate.name,
                    "workspace_path": str(candidate),
                    "error": "invalid manifest",
                }
            )
    return result


def inspect_workspace(slug: str, *, root: Path | None = None) -> dict[str, Any]:
    workspace, manifest = load_manifest(slug, root=root)
    source_notes = sorted((workspace / "wiki" / "sources").glob("S-*.md"))
    operation_files = sorted((workspace / ".llm-wiki" / "operations").glob("op_*.json"))
    derived_index = workspace / ".llm-wiki" / "index" / "workspace-index.json"
    return {
        "workspace_id": slug,
        "workspace_path": str(workspace),
        "manifest": manifest,
        "counts": {"sources": len(source_notes), "operations": len(operation_files)},
        "latest_operation": operation_files[-1].stem if operation_files else None,
        "index": {"exists": derived_index.is_file(), "path": str(derived_index)},
    }


def _parse_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        raise MemoryWorkspaceError(f"缺少 YAML frontmatter：{path}")
    result: dict[str, Any] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return result
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        value = raw.strip()
        try:
            result[key.strip()] = json.loads(value)
        except json.JSONDecodeError:
            result[key.strip()] = value
    raise MemoryWorkspaceError(f"frontmatter 未闭合：{path}")


def _next_source_id(source_dir: Path) -> str:
    highest = 0
    for path in source_dir.glob("S-*.md"):
        match = SOURCE_NOTE_PATTERN.fullmatch(path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"S-{highest + 1:03d}"


def _existing_source_for_hash(source_dir: Path, content_hash: str) -> tuple[Path, dict[str, Any]] | None:
    for path in sorted(source_dir.glob("S-*.md")):
        try:
            metadata = _parse_frontmatter(path)
        except (OSError, UnicodeDecodeError, MemoryWorkspaceError):
            continue
        if metadata.get("content_hash") == content_hash:
            return path, metadata
    return None


def _source_target(inbox: Path, original_name: str, content_hash: str) -> Path:
    safe_name = Path(original_name).name
    if not safe_name:
        safe_name = "source"
    target = inbox / safe_name
    if not target.exists():
        return target
    if target.is_file() and sha256_file(target) == content_hash:
        return target
    stem = Path(safe_name).stem or "source"
    suffix = Path(safe_name).suffix
    return inbox / f"{stem}-{content_hash.removeprefix('sha256:')[:8]}{suffix}"


def _source_note(
    *,
    source_id: str,
    title: str,
    content_path: str,
    captured_at: str,
    content_hash: str,
) -> str:
    scalar = lambda value: json.dumps(value, ensure_ascii=False)
    return (
        "---\n"
        "schema_version: 1\n"
        f"id: {source_id}\n"
        "kind: manual_file\n"
        f"title: {scalar(title)}\n"
        f"content_path: {scalar(content_path)}\n"
        "external_ref: null\n"
        f"captured_at: {scalar(captured_at)}\n"
        f"coverage: {scalar('version available at capture time')}\n"
        f"content_hash: {scalar(content_hash)}\n"
        "status: captured\n"
        "limitations: []\n"
        "privacy: workspace_private\n"
        "---\n\n"
        f"# {title}\n\n"
        "This source was captured without interpretation. Claim extraction and synthesis "
        "remain pending so that no unsupported facts are introduced.\n"
    )


def _append_source_to_index(text: str, source_id: str, title: str) -> str:
    display_title = title.replace("|", "-").replace("[", "").replace("]", "")
    line = f"- [[sources/{source_id}|{display_title}]] — captured\n"
    placeholder = "No sources captured yet.\n"
    if placeholder in text:
        return text.replace(placeholder, line, 1)
    if not text.endswith("\n"):
        text += "\n"
    return text + line


def ingest_source(
    slug: str,
    source: Path,
    *,
    title: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    workspace, _ = load_manifest(slug, root=root)
    source = source.expanduser().resolve()
    if not source.is_file():
        raise MemoryWorkspaceError(f"来源文件不存在：{source}")
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise MemoryWorkspaceError(f"无法读取来源文件 {source}：{exc}") from exc
    if b"-----BEGIN PRIVATE KEY-----" in data or b"-----BEGIN RSA PRIVATE KEY-----" in data:
        raise MemoryWorkspaceError("来源包含私钥标记；Memory Workspace 不保存高风险秘密。")
    content_hash = sha256_bytes(data)
    source_dir = workspace / "wiki" / "sources"
    existing = _existing_source_for_hash(source_dir, content_hash)
    if existing is not None:
        note_path, metadata = existing
        return {
            "workspace_id": slug,
            "source_id": metadata.get("id", note_path.stem),
            "source_note": str(note_path),
            "content_path": metadata.get("content_path"),
            "content_hash": content_hash,
            "duplicate": True,
            "operation_id": None,
        }

    inbox = workspace / "raw" / "inbox"
    raw_target = _source_target(inbox, source.name, content_hash)
    if raw_target.exists():
        if not raw_target.is_file() or sha256_file(raw_target) != content_hash:
            raise MemoryWorkspaceError(f"来源目标冲突，拒绝覆盖：{raw_target}")
        raw_before = file_hash_or_none(raw_target)
    else:
        raw_before = None
        write_bytes_once(raw_target, data, allow_transient=allow_transient())

    source_id = _next_source_id(source_dir)
    note_path = source_dir / f"{source_id}.md"
    clean_title = " ".join((title or source.stem or source_id).split())
    relative_raw = raw_target.relative_to(workspace).as_posix()
    captured_at = now_iso()
    note_text = _source_note(
        source_id=source_id,
        title=clean_title,
        content_path=relative_raw,
        captured_at=captured_at,
        content_hash=content_hash,
    )
    atomic_write_text(note_path, note_text, backup=False, allow_transient=allow_transient())

    index_path = workspace / "wiki" / "index.md"
    log_path = workspace / "wiki" / "log.md"
    index_before = file_hash_or_none(index_path)
    log_before = file_hash_or_none(log_path)
    index_text = index_path.read_text(encoding="utf-8")
    log_text = log_path.read_text(encoding="utf-8")
    atomic_write_text(
        index_path,
        _append_source_to_index(index_text, source_id, clean_title),
        backup=False,
        allow_transient=allow_transient(),
    )
    log_entry = (
        f"\n## {captured_at} — source ingested\n\n"
        f"Captured [[sources/{source_id}]] from `{relative_raw}`.\n"
    )
    atomic_write_text(
        log_path,
        log_text.rstrip() + "\n" + log_entry,
        backup=False,
        allow_transient=allow_transient(),
    )
    changes = [
        _change(workspace, note_path, before_hash=None),
        _change(workspace, index_path, before_hash=index_before),
        _change(workspace, log_path, before_hash=log_before),
    ]
    if raw_before is None:
        changes.insert(0, _change(workspace, raw_target, before_hash=None))
    operation = _write_operation(
        workspace,
        operation_type="ingest",
        input_refs=[str(source)],
        changes=changes,
        checks=["content-hash", "source-note", "wiki-links"],
    )
    from .index import rebuild_index

    index_result = rebuild_index(slug, root=root)
    return {
        "workspace_id": slug,
        "source_id": source_id,
        "source_note": str(note_path),
        "content_path": relative_raw,
        "content_hash": content_hash,
        "duplicate": False,
        "index_path": index_result["index_path"],
        **operation,
    }


def _resolve_wiki_link(workspace: Path, target: str) -> Path:
    clean = target.split("|", 1)[0].split("#", 1)[0].strip()
    candidate = workspace / "wiki" / clean
    return candidate if candidate.suffix else candidate.with_suffix(".md")


def check_workspace(slug: str, *, root: Path | None = None) -> dict[str, Any]:
    workspace = workspace_path(slug, root)
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[str] = []

    manifest_path = workspace / "llm-wiki.json"
    required_directories = REQUIRED_DIRECTORIES
    if manifest_path.is_file():
        try:
            if load_json_object(manifest_path).get("schema_version") == 1:
                required_directories += LEGACY_PERSONAL_DIRECTORIES
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    for relative in required_directories:
        if not (workspace / relative).is_dir():
            errors.append(f"missing directory: {relative}")
    for relative in REQUIRED_FILES:
        if not (workspace / relative).is_file():
            errors.append(f"missing file: {relative}")
    checks.append("required-layout")

    if manifest_path.is_file():
        try:
            manifest_errors = validate(
                load_json_object(manifest_path), load_json_object(WORKSPACE_SCHEMA)
            )
            errors.extend(f"manifest: {error}" for error in manifest_errors)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"manifest: {exc}")
    checks.append("workspace-schema")

    source_notes = sorted((workspace / "wiki" / "sources").glob("S-*.md"))
    for note in source_notes:
        try:
            metadata = _parse_frontmatter(note)
        except (OSError, UnicodeDecodeError, MemoryWorkspaceError) as exc:
            errors.append(f"{note.relative_to(workspace)}: {exc}")
            continue
        for key in ("id", "kind", "content_path", "captured_at", "content_hash"):
            if key not in metadata:
                errors.append(f"{note.relative_to(workspace)}: missing {key}")
        content_path = metadata.get("content_path")
        if isinstance(content_path, str):
            raw_path = (workspace / content_path).resolve(strict=False)
            kind = str(metadata.get("kind") or "")
            allowed_root = workspace / ("connected/lark" if kind.startswith("lark_") else "raw")
            if not is_within(raw_path, allowed_root.resolve(strict=False)):
                errors.append(
                    f"{note.relative_to(workspace)}: source path escapes {allowed_root.relative_to(workspace)}/: {content_path}"
                )
                continue
            if not raw_path.is_file():
                errors.append(f"{note.relative_to(workspace)}: missing source {content_path}")
            else:
                actual_hash = sha256_file(raw_path)
                if actual_hash != metadata.get("content_hash"):
                    errors.append(
                        f"{note.relative_to(workspace)}: source hash mismatch for {content_path}"
                    )
    checks.append("source-integrity")

    markdown_files = sorted((workspace / "wiki").rglob("*.md"))
    for page in markdown_files:
        try:
            text = page.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{page.relative_to(workspace)}: cannot read: {exc}")
            continue
        for target in WIKI_LINK_PATTERN.findall(text):
            resolved = _resolve_wiki_link(workspace, target).resolve(strict=False)
            wiki_root = (workspace / "wiki").resolve(strict=False)
            if not is_within(resolved, wiki_root):
                errors.append(
                    f"{page.relative_to(workspace)}: wiki link escapes wiki/ [[{target}]]"
                )
            elif not resolved.is_file():
                errors.append(
                    f"{page.relative_to(workspace)}: broken wiki link [[{target}]]"
                )
    checks.append("wiki-links")

    operation_files = sorted((workspace / ".llm-wiki" / "operations").glob("op_*.json"))
    from .operations import validate_operation_document

    for path in operation_files:
        try:
            validate_operation_document(load_json_object(path))
        except (OSError, ValueError, json.JSONDecodeError, MemoryWorkspaceError) as exc:
            errors.append(f"{path.relative_to(workspace)}: {exc}")
    checks.append("operation-schema")

    derived_index = workspace / ".llm-wiki" / "index" / "workspace-index.json"
    if derived_index.is_file():
        try:
            from .index import INDEX_SCHEMA

            index_errors = validate(load_json_object(derived_index), load_json_object(INDEX_SCHEMA))
            errors.extend(f"index: {error}" for error in index_errors)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"index: {exc}")
    checks.append("derived-index-schema")

    project_memory = workspace / ".llm-wiki" / "index" / "project-memory.json"
    if project_memory.is_file():
        try:
            from .lark_sync import PROJECT_VIEW_SCHEMA

            view_errors = validate(load_json_object(project_memory), load_json_object(PROJECT_VIEW_SCHEMA))
            errors.extend(f"project-memory: {error}" for error in view_errors)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"project-memory: {exc}")
    checks.append("project-memory-schema")

    from .connectors import validate_workspace_connector_files

    connector_errors = validate_workspace_connector_files(workspace, slug)
    errors.extend(f"connector: {error}" for error in connector_errors)
    checks.append("optional-connector-schemas")
    connector_configs = sorted(
        path
        for path in (workspace / "config" / "connectors").glob("*.json")
        if not path.name.endswith("-sources.json")
    )

    if transient_reason(workspace):
        warnings.append("workspace is under a transient directory (allowed only for explicit tests)")
    return {
        "workspace_id": slug,
        "workspace_path": str(workspace),
        "status": "FAILED" if errors else "OK",
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "source_notes": len(source_notes),
            "wiki_pages": len(markdown_files),
            "operations": len(operation_files),
            "connectors": len(connector_configs),
        },
    }


def doctor(*, root: Path | None = None) -> dict[str, Any]:
    directory, source = workspaces_path_info() if root is None else (root, "argument")
    reason = transient_reason(directory)
    parent = nearest_existing_parent(directory)
    writable = os.access(parent, os.W_OK)
    warnings: list[str] = []
    if reason:
        warnings.append(reason + "；正式写入将被拒绝。")
    if source == "MWORK_WORKSPACES_DIR":
        warnings.append("正在使用组件级旧路径覆盖；新安装建议统一配置 MEMORY_HOME。")
    if not writable:
        warnings.append(f"现有父目录不可写：{parent}")
    return {
        "workspaces_root": str(directory),
        "source": source,
        "exists": directory.exists(),
        "nearest_existing_parent": str(parent),
        "filesystem_writable": writable,
        "transient_risk": reason is not None,
        "status": "WARNING" if warnings else "OK",
        "warnings": warnings,
        "note": "沙箱可能仍需对上述确切目录单独授权。",
    }
