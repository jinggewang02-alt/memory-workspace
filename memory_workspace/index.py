"""Disposable read model and local query adapter for a Memory Workspace."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .io import MemoryWorkspaceError, atomic_write_json
from .operations import validate_operation_document
from .schema import load_json_object, validate
from .workspace import (
    PACKAGE_ROOT,
    WIKI_LINK_PATTERN,
    _parse_frontmatter,
    allow_transient,
    load_manifest,
    now_iso,
)


INDEX_SCHEMA = PACKAGE_ROOT / "schemas" / "workspace-index.schema.json"
SOURCE_REF_PATTERN = re.compile(r"^sources/(S-\d+)$")


def index_path(workspace: Path) -> Path:
    return workspace / ".llm-wiki" / "index" / "workspace-index.json"


def _metadata_or_empty(path: Path) -> dict[str, Any]:
    try:
        return _parse_frontmatter(path)
    except (OSError, UnicodeDecodeError, MemoryWorkspaceError):
        return {}


def _page_title(path: Path, text: str, metadata: dict[str, Any]) -> str:
    title = metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for line in text.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return path.stem.replace("-", " ").strip() or path.name


def _source_refs(text: str) -> list[str]:
    refs: set[str] = set()
    for raw_target in WIKI_LINK_PATTERN.findall(text):
        target = raw_target.split("|", 1)[0].split("#", 1)[0].strip()
        match = SOURCE_REF_PATTERN.fullmatch(target)
        if match:
            refs.add(match.group(1))
    return sorted(refs)


def _collect_sources(workspace: Path) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for path in sorted((workspace / "wiki" / "sources").glob("S-*.md")):
        metadata = _metadata_or_empty(path)
        text = path.read_text(encoding="utf-8")
        sources.append(
            {
                "id": str(metadata.get("id") or path.stem),
                "title": _page_title(path, text, metadata),
                "kind": str(metadata.get("kind") or "source"),
                "path": path.relative_to(workspace).as_posix(),
                "content_path": metadata.get("content_path")
                if isinstance(metadata.get("content_path"), str)
                else None,
                "captured_at": metadata.get("captured_at")
                if isinstance(metadata.get("captured_at"), str)
                else None,
                "status": metadata.get("status")
                if isinstance(metadata.get("status"), str)
                else None,
                "content_hash": metadata.get("content_hash")
                if isinstance(metadata.get("content_hash"), str)
                else None,
            }
        )
    return sources


def _collect_pages(workspace: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    source_root = workspace / "wiki" / "sources"
    for path in sorted((workspace / "wiki").rglob("*.md")):
        if path.parent == source_root:
            continue
        text = path.read_text(encoding="utf-8")
        metadata = _metadata_or_empty(path)
        relative = path.relative_to(workspace).as_posix()
        pages.append(
            {
                "path": relative,
                "title": _page_title(path, text, metadata),
                "kind": str(metadata.get("kind") or path.parent.name or "page"),
                "source_refs": _source_refs(text),
                "content": text,
            }
        )
    return pages


def _collect_projects(workspace: Path, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    root = workspace / "wiki" / "projects"
    if not root.is_dir():
        return projects
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        prefix = directory.relative_to(workspace).as_posix() + "/"
        project_pages = [page for page in pages if page["path"].startswith(prefix)]
        overview = next(
            (page for page in project_pages if page["path"].endswith("/overview.md")),
            None,
        )
        projects.append(
            {
                "id": directory.name,
                "title": overview["title"] if overview else directory.name.replace("-", " "),
                "path": directory.relative_to(workspace).as_posix(),
                "page_count": len(project_pages),
            }
        )
    return projects


def _collect_operations(workspace: Path) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    root = workspace / ".llm-wiki" / "operations"
    for path in sorted(root.glob("op_*.json"), reverse=True):
        try:
            document = load_json_object(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise MemoryWorkspaceError(f"无法索引 Operation {path}：{exc}") from exc
        try:
            validate_operation_document(document)
        except MemoryWorkspaceError as exc:
            raise MemoryWorkspaceError(f"无法索引损坏的 Operation {path}：{exc}") from exc
        changes = document.get("changes")
        operations.append(
            {
                "operation_id": str(document.get("operation_id") or path.stem),
                "type": str(document.get("type") or "unknown"),
                "status": str(document.get("status") or "unknown"),
                "requested_at": str(document.get("requested_at") or now_iso()),
                "actor": str(document.get("actor") or "unknown"),
                "changes_count": len(changes) if isinstance(changes, list) else 0,
            }
        )
    return operations


def build_index_document(slug: str, *, root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    workspace, manifest = load_manifest(slug, root=root)
    pages = _collect_pages(workspace)
    document = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "workspace": {
            "id": str(manifest["workspace"]["id"]),
            "name": str(manifest["workspace"]["name"]),
        },
        "projects": _collect_projects(workspace, pages),
        "sources": _collect_sources(workspace),
        "pages": pages,
        "operations": _collect_operations(workspace),
    }
    errors = validate(document, load_json_object(INDEX_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("索引校验失败：" + "; ".join(errors))
    return workspace, document


def rebuild_index(slug: str, *, root: Path | None = None) -> dict[str, Any]:
    workspace, document = build_index_document(slug, root=root)
    path = index_path(workspace)
    atomic_write_json(path, document, backup=False, allow_transient=allow_transient())
    return {
        "workspace_id": slug,
        "index_path": str(path),
        "generated_at": document["generated_at"],
        "counts": {
            "projects": len(document["projects"]),
            "sources": len(document["sources"]),
            "pages": len(document["pages"]),
            "operations": len(document["operations"]),
        },
    }


def load_index(slug: str, *, root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    workspace, _ = load_manifest(slug, root=root)
    path = index_path(workspace)
    if not path.is_file():
        raise MemoryWorkspaceError(f"索引不存在，请先运行 index rebuild：{path}")
    try:
        document = load_json_object(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MemoryWorkspaceError(f"无法读取索引 {path}：{exc}") from exc
    errors = validate(document, load_json_object(INDEX_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("索引校验失败：" + "; ".join(errors))
    return workspace, document


def _snippet(text: str, keyword: str, *, radius: int = 70) -> str:
    compact = " ".join(text.split())
    position = compact.casefold().find(keyword.casefold())
    if position < 0:
        return compact[: radius * 2]
    start = max(0, position - radius)
    end = min(len(compact), position + len(keyword) + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(compact) else ""
    return prefix + compact[start:end] + suffix


def query_index(
    slug: str,
    keyword: str,
    *,
    limit: int = 20,
    root: Path | None = None,
    rebuild: bool = True,
) -> dict[str, Any]:
    if not keyword.strip():
        raise MemoryWorkspaceError("查询关键词不能为空。")
    if limit < 1 or limit > 100:
        raise MemoryWorkspaceError("limit 必须在 1 到 100 之间。")
    if rebuild:
        rebuild_index(slug, root=root)
    _, document = load_index(slug, root=root)
    needle = keyword.casefold()
    results: list[dict[str, Any]] = []

    def add_result(kind: str, identifier: str, title: str, path: str, content: str) -> None:
        title_hits = title.casefold().count(needle)
        path_hits = path.casefold().count(needle)
        content_hits = content.casefold().count(needle)
        score = title_hits * 5 + path_hits * 2 + min(content_hits, 5)
        if score:
            results.append(
                {
                    "type": kind,
                    "id": identifier,
                    "title": title,
                    "path": path,
                    "score": score,
                    "snippet": _snippet(content or title, keyword),
                }
            )

    for project in document["projects"]:
        add_result("project", project["id"], project["title"], project["path"], project["title"])
    for source in document["sources"]:
        content = " ".join(
            str(source.get(key) or "") for key in ("id", "title", "kind", "content_path", "status")
        )
        add_result("source", source["id"], source["title"], source["path"], content)
    for page in document["pages"]:
        add_result("page", page["path"], page["title"], page["path"], page["content"])
    for operation in document["operations"]:
        content = " ".join(
            str(operation.get(key) or "")
            for key in ("operation_id", "type", "status", "actor")
        )
        add_result(
            "operation",
            operation["operation_id"],
            f"{operation['type']} · {operation['status']}",
            f".llm-wiki/operations/{operation['operation_id']}.json",
            content,
        )

    results.sort(key=lambda item: (-item["score"], item["type"], item["path"]))
    limited = results[:limit]
    return {
        "workspace_id": slug,
        "keyword": keyword,
        "index_generated_at": document["generated_at"],
        "count": len(limited),
        "total_matches": len(results),
        "results": limited,
    }
