"""Single Writer that applies an approved Candidate to canonical local storage."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from . import capture, operations, profile
from .io import MemoryWorkspaceError
from .workspace import file_hash_or_none, load_manifest


WORKSPACE_TARGET_RE = re.compile(
    r"^workspace:(?P<workspace>[a-z0-9]+(?:-[a-z0-9]+)*)/(?P<path>.+)$"
)
PROFILE_TARGET_RE = re.compile(r"^profile:(?P<key>.+)$")
PROJECT_KIND_LABELS = {
    "decision": "Owner-approved decision",
    "learning": "Owner-approved learning",
    "preference": "Owner-approved preference",
    "fact": "Owner-approved note",
}


def _approved_bundle(candidate_id: str, capture_root: Path | None) -> dict[str, Any]:
    bundle = capture.show_candidate(candidate_id, root=capture_root)
    if bundle["status"] == "applied":
        return bundle
    review = bundle.get("review")
    if not isinstance(review, dict) or review.get("decision") != "approved":
        raise MemoryWorkspaceError("candidate 必须先由用户批准，才能执行正式写入。")
    content = review.get("approved_content")
    if not isinstance(content, str) or not content:
        raise MemoryWorkspaceError("approved Candidate 缺少可写入内容。")
    return bundle


def _result(bundle: dict[str, Any], *, already_applied: bool) -> dict[str, Any]:
    application = bundle.get("application")
    return {
        "candidate_id": bundle["candidate"]["candidate_id"],
        "status": bundle["status"],
        "target_ref": bundle.get("review", {}).get("target_ref"),
        "application": application,
        "already_applied": already_applied,
    }


def _apply_profile(
    bundle: dict[str, Any],
    *,
    actor: str,
    capture_root: Path | None,
    profile_path: Path | None,
) -> dict[str, Any]:
    candidate_id = bundle["candidate"]["candidate_id"]
    review = bundle["review"]
    match = PROFILE_TARGET_RE.fullmatch(review["target_ref"])
    if match is None or not match.group("key").strip():
        raise MemoryWorkspaceError("profile target-ref 无效。")
    key = match.group("key").strip()
    content = review["approved_content"]
    path = profile_path or profile.store_path()
    health = profile.doctor(path)
    if not health["filesystem_writable"]:
        raise MemoryWorkspaceError("Exact Profile 目录不可写；未执行正式写入。")
    if health["transient_risk"] and os.environ.get("PMEM_ALLOW_TRANSIENT") != "1":
        raise MemoryWorkspaceError(
            "Exact Profile 位于临时或工程目录；请先配置持久 MEMORY_HOME，或兼容的 PMEM_FILE/PMEM_DIR。"
        )

    store = profile.load_store(path)
    existing = store["items"].get(key) if store is not None else None
    if existing is not None:
        if existing.get("type") != "single":
            raise MemoryWorkspaceError(
                f"profile:{key} 是结构化 entries；字符串 Candidate 不能自动覆盖。"
            )
        if existing.get("value") != content:
            raise MemoryWorkspaceError(
                f"profile:{key} 已有不同值；为避免静默覆盖，正式写入已停止。"
            )
    else:
        profile.set_single(path, key, content)

    readback = profile.get_item(path, key)
    if readback.get("type") != "single" or readback.get("value") != content:
        raise MemoryWorkspaceError("Exact Profile 逐字读回校验失败；未生成 application receipt。")
    capture.mark_candidate_applied(
        candidate_id,
        actor=actor,
        verification=f"profile:{key} exact readback verified",
        writer_kind="profile_single",
        operation_id=None,
        checks=["candidate-approved", "profile-doctor", "exact-readback"],
        root=capture_root,
    )
    return _result(capture.show_candidate(candidate_id, root=capture_root), already_applied=False)


def _page_title(relative_path: str) -> str:
    stem = Path(relative_path).stem.replace("-", " ").strip()
    return stem[:1].upper() + stem[1:] if stem else "Project memory"


def _candidate_block(candidate: dict[str, Any], content: str) -> tuple[str, str]:
    marker = f"<!-- memory-workspace:candidate:{candidate['candidate_id']} -->"
    label = PROJECT_KIND_LABELS.get(candidate.get("kind"), "Owner-approved memory")
    return marker, f"{marker}\n### {label}\n\n{content}\n"


def _compose_project_page(
    current: str | None,
    *,
    relative_path: str,
    candidate: dict[str, Any],
    content: str,
) -> tuple[str, str, str]:
    marker, block = _candidate_block(candidate, content)
    if current is None:
        title = _page_title(relative_path)
        page = (
            "---\n"
            "schema_version: 1\n"
            "kind: project-memory\n"
            f"title: {json.dumps(title, ensure_ascii=False)}\n"
            "status: current\n"
            "---\n\n"
            f"# {title}\n\n"
            "## Owner-approved memories\n\n"
            f"{block}"
        )
        return page, marker, block
    if marker in current:
        if block.strip() not in current:
            raise MemoryWorkspaceError(
                "目标页面已有同 Candidate marker，但内容不一致；拒绝覆盖或重复写入。"
            )
        return current, marker, block
    separator = "" if current.endswith("\n\n") else "\n" if current.endswith("\n") else "\n\n"
    heading = "" if "## Owner-approved memories" in current else "## Owner-approved memories\n\n"
    return current + separator + heading + block, marker, block


def _matching_applied_operation(
    workspace_id: str,
    relative_path: str,
    *,
    workspaces_root: Path | None,
) -> str | None:
    workspace, _ = load_manifest(workspace_id, root=workspaces_root)
    target_hash = file_hash_or_none(workspace / relative_path)
    for item in operations.list_operations(
        workspace_id, status="applied", root=workspaces_root
    ):
        _, document = operations.load_operation(
            workspace_id, item["operation_id"], root=workspaces_root
        )
        if any(
            change.get("path") == relative_path
            and change.get("after_hash") == target_hash
            for change in document["changes"]
        ):
            return str(document["operation_id"])
    return None


def _apply_project(
    bundle: dict[str, Any],
    *,
    actor: str,
    capture_root: Path | None,
    workspaces_root: Path | None,
) -> dict[str, Any]:
    candidate = bundle["candidate"]
    candidate_id = candidate["candidate_id"]
    review = bundle["review"]
    capture._validate_approval_target(
        candidate, review["target_ref"], workspaces_root=workspaces_root
    )
    match = WORKSPACE_TARGET_RE.fullmatch(review["target_ref"])
    if match is None:
        raise MemoryWorkspaceError("workspace target-ref 无效。")
    workspace_id = match.group("workspace")
    relative_path = match.group("path")
    workspace, _ = load_manifest(workspace_id, root=workspaces_root)
    target = workspace / relative_path
    current = target.read_text(encoding="utf-8") if target.is_file() else None
    next_page, marker, block = _compose_project_page(
        current,
        relative_path=relative_path,
        candidate=candidate,
        content=review["approved_content"],
    )

    operation_id: str | None = None
    if current == next_page:
        operation_id = _matching_applied_operation(
            workspace_id, relative_path, workspaces_root=workspaces_root
        )
        if operation_id is None:
            raise MemoryWorkspaceError(
                "目标页面已有 Candidate 内容，但找不到对应的 applied Operation；未生成回执。"
            )
    else:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".md", delete=False
        ) as handle:
            handle.write(next_page)
            staging_path = Path(handle.name)
        try:
            proposed = operations.propose_file(
                workspace_id,
                relative_path,
                content_file=staging_path,
                actor=actor,
                operation_type="memory_write",
                root=workspaces_root,
            )
            operation_id = str(proposed["operation_id"])
            operations.approve_operation(
                workspace_id, operation_id, actor=actor, root=workspaces_root
            )
            operations.apply_operation(workspace_id, operation_id, root=workspaces_root)
        finally:
            staging_path.unlink(missing_ok=True)

    readback = target.read_text(encoding="utf-8")
    if marker not in readback or block.strip() not in readback:
        raise MemoryWorkspaceError("Workspace target readback 校验失败；未生成 application receipt。")
    capture.mark_candidate_applied(
        candidate_id,
        actor=actor,
        verification=f"Workspace operation {operation_id} applied; workspace check and target readback passed",
        writer_kind="workspace_operation",
        operation_id=operation_id,
        checks=[
            "candidate-approved",
            "workspace-operation-applied",
            "workspace-check",
            "target-readback",
        ],
        root=capture_root,
    )
    return _result(capture.show_candidate(candidate_id, root=capture_root), already_applied=False)


def apply_candidate(
    candidate_id: str,
    *,
    actor: str = "owner_via_agent",
    capture_root: Path | None = None,
    profile_path: Path | None = None,
    workspaces_root: Path | None = None,
) -> dict[str, Any]:
    """Apply one approved Candidate, verify canonical readback, and write its receipt."""

    clean_actor = actor.strip()
    if not clean_actor:
        raise MemoryWorkspaceError("actor 不能为空。")
    bundle = _approved_bundle(candidate_id, capture_root)
    if bundle["status"] == "applied":
        return _result(bundle, already_applied=True)
    scope = bundle["candidate"]["scope"]
    try:
        if scope == "profile":
            return _apply_profile(
                bundle,
                actor=clean_actor,
                capture_root=capture_root,
                profile_path=profile_path,
            )
        if scope == "project":
            return _apply_project(
                bundle,
                actor=clean_actor,
                capture_root=capture_root,
                workspaces_root=workspaces_root,
            )
        raise MemoryWorkspaceError(f"未知的 Candidate scope：{scope}")
    except MemoryWorkspaceError:
        raise
    except (OSError, UnicodeError) as exc:
        raise MemoryWorkspaceError(f"Single Writer 无法读写本地存储：{exc}") from exc
