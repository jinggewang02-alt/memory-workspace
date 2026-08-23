"""Reviewable proposal lifecycle for canonical Wiki changes."""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from .io import (
    MemoryWorkspaceError,
    atomic_write_json,
    atomic_write_text,
    ensure_safe_write_path,
    is_within,
    write_bytes_once,
)
from .schema import load_json_object, validate
from .workspace import (
    OPERATION_SCHEMA,
    allow_transient,
    check_workspace,
    file_hash_or_none,
    load_manifest,
    now_iso,
    operation_id,
    sha256_bytes,
)


OPERATION_ID_PATTERN = re.compile(r"^op_[A-Za-z0-9_-]+$")
ALLOWED_WIKI_SECTIONS = {"projects", "topics", "entities", "syntheses"}
PROTECTED_WIKI_FILES = {"wiki/index.md", "wiki/log.md"}


def _operations_root(workspace: Path) -> Path:
    return workspace / ".llm-wiki" / "operations"


def _operation_path(workspace: Path, identifier: str) -> Path:
    if OPERATION_ID_PATTERN.fullmatch(identifier) is None:
        raise MemoryWorkspaceError(f"operation id 无效：{identifier}")
    return _operations_root(workspace) / f"{identifier}.json"


def _artifact_paths(workspace: Path, identifier: str, index: int) -> dict[str, Path]:
    directory = _operations_root(workspace) / identifier
    prefix = f"{index:03d}"
    return {
        "directory": directory,
        "before": directory / f"{prefix}.before",
        "after": directory / f"{prefix}.after",
        "diff": directory / f"{prefix}.diff",
    }


def _resolve_target(workspace: Path, relative_path: str, *, deleting: bool = False) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise MemoryWorkspaceError(f"Proposal 目标必须是 Workspace 内相对路径：{relative_path}")
    normalized = relative.as_posix()
    target = (workspace / relative).resolve(strict=False)
    wiki_root = (workspace / "wiki").resolve(strict=False)
    if not is_within(target, wiki_root) or target.suffix.lower() != ".md":
        raise MemoryWorkspaceError("Proposal 目前只允许修改 wiki/ 下的 Markdown 文件。")
    parts = relative.parts
    allowed = normalized in PROTECTED_WIKI_FILES or (
        len(parts) >= 3 and parts[0] == "wiki" and parts[1] in ALLOWED_WIKI_SECTIONS
    )
    if not allowed or (len(parts) >= 2 and parts[1] == "sources"):
        raise MemoryWorkspaceError(
            "Proposal 只允许修改 projects/topics/entities/syntheses 页面；来源说明不可改。"
        )
    if deleting and normalized in PROTECTED_WIKI_FILES:
        raise MemoryWorkspaceError(f"受保护页面不可删除：{normalized}")
    return target


def validate_operation_document(document: dict[str, Any]) -> None:
    errors = validate(document, load_json_object(OPERATION_SCHEMA))
    status = document.get("status")
    approval = document.get("approval")
    if status in {"draft", "proposed"} and approval is not None:
        errors.append(f"$.approval: {status} Operation must not have a decision")
    if status in {"approved", "applied"} and (
        not isinstance(approval, dict) or approval.get("status") != "approved"
    ):
        errors.append(f"$.approval: {status} Operation requires approved decision")
    if status == "rejected" and (
        not isinstance(approval, dict) or approval.get("status") != "rejected"
    ):
        errors.append("$.approval: rejected Operation requires rejected decision")
    if errors:
        raise MemoryWorkspaceError("操作清单校验失败：" + "; ".join(errors))


def _save_operation(workspace: Path, document: dict[str, Any]) -> Path:
    validate_operation_document(document)
    path = _operation_path(workspace, str(document["operation_id"]))
    atomic_write_json(path, document, backup=False, allow_transient=allow_transient())
    return path


def _refresh_index(slug: str, root: Path | None) -> str | None:
    try:
        from .index import rebuild_index

        rebuild_index(slug, root=root)
    except MemoryWorkspaceError as exc:
        return str(exc)
    return None


def load_operation(
    slug: str,
    identifier: str,
    *,
    root: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    workspace, _ = load_manifest(slug, root=root)
    path = _operation_path(workspace, identifier)
    if not path.is_file():
        raise MemoryWorkspaceError(f"未找到 Operation：{identifier}")
    try:
        document = load_json_object(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MemoryWorkspaceError(f"无法读取 Operation {path}：{exc}") from exc
    validate_operation_document(document)
    if document.get("operation_id") != identifier:
        raise MemoryWorkspaceError(f"Operation 文件名与内部 ID 不一致：{path}")
    return workspace, document


def _unified_diff(relative_path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
        )
    )


def _validate_input_refs(workspace: Path, input_refs: list[str]) -> None:
    for reference in input_refs:
        relative = Path(reference)
        if relative.is_absolute() or ".." in relative.parts:
            raise MemoryWorkspaceError(f"input-ref 必须是 Workspace 内相对路径：{reference}")
        resolved = (workspace / relative).resolve(strict=False)
        if not is_within(resolved, workspace.resolve(strict=False)) or not resolved.is_file():
            raise MemoryWorkspaceError(f"input-ref 不存在或超出 Workspace：{reference}")


def propose_file(
    slug: str,
    target_path: str,
    *,
    content_file: Path | None = None,
    delete: bool = False,
    input_refs: list[str] | None = None,
    actor: str = "owner_via_agent",
    operation_type: str = "synthesis",
    root: Path | None = None,
) -> dict[str, Any]:
    if delete == (content_file is not None):
        raise MemoryWorkspaceError("必须在 --content-file 和 --delete 中选择一个。")
    workspace, _ = load_manifest(slug, root=root)
    normalized_input_refs = list(input_refs or [])
    _validate_input_refs(workspace, normalized_input_refs)
    target = _resolve_target(workspace, target_path, deleting=delete)
    if target.exists() and not target.is_file():
        raise MemoryWorkspaceError(f"Proposal 目标不是文件：{target_path}")

    before_data = target.read_bytes() if target.is_file() else None
    if delete:
        if before_data is None:
            raise MemoryWorkspaceError(f"无法删除不存在的页面：{target_path}")
        after_data = None
        action = "delete"
    else:
        assert content_file is not None
        candidate = content_file.expanduser().resolve()
        if not candidate.is_file():
            raise MemoryWorkspaceError(f"候选内容文件不存在：{candidate}")
        try:
            after_data = candidate.read_bytes()
        except OSError as exc:
            raise MemoryWorkspaceError(f"无法读取候选内容 {candidate}：{exc}") from exc
        action = "update" if before_data is not None else "create"

    for label, data in (("原页面", before_data), ("候选页面", after_data)):
        if data is None:
            continue
        if b"-----BEGIN PRIVATE KEY-----" in data or b"-----BEGIN RSA PRIVATE KEY-----" in data:
            raise MemoryWorkspaceError(f"{label}包含私钥标记；Memory Workspace 不保存高风险秘密。")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MemoryWorkspaceError(f"{label}必须是 UTF-8 Markdown：{target_path}") from exc
    if before_data == after_data:
        raise MemoryWorkspaceError("候选内容与当前页面完全相同，无需创建 Proposal。")

    identifier = operation_id()
    artifacts = _artifact_paths(workspace, identifier, 0)
    before_text = before_data.decode("utf-8") if before_data is not None else ""
    after_text = after_data.decode("utf-8") if after_data is not None else ""
    diff_text = _unified_diff(target_path, before_text, after_text)
    document = {
        "schema_version": 1,
        "operation_id": identifier,
        "type": operation_type,
        "status": "proposed",
        "requested_at": now_iso(),
        "actor": actor,
        "input_refs": normalized_input_refs,
        "changes": [
            {
                "path": target_path,
                "action": action,
                "before_hash": sha256_bytes(before_data) if before_data is not None else None,
                "after_hash": sha256_bytes(after_data) if after_data is not None else None,
                "diff_path": artifacts["diff"].relative_to(workspace).as_posix(),
            }
        ],
        "validation": {
            "status": "passed",
            "checks": ["target-scope", "utf8-markdown", "operation-schema"],
            "errors": [],
        },
        "approval": None,
    }
    validate_operation_document(document)
    ensure_safe_write_path(artifacts["directory"], allow_transient=allow_transient())
    try:
        artifacts["directory"].mkdir(mode=0o700, parents=True)
    except OSError as exc:
        raise MemoryWorkspaceError(f"无法创建 Proposal 暂存目录：{exc}") from exc
    if before_data is not None:
        write_bytes_once(artifacts["before"], before_data, allow_transient=allow_transient())
    if after_data is not None:
        write_bytes_once(artifacts["after"], after_data, allow_transient=allow_transient())
    atomic_write_text(
        artifacts["diff"], diff_text, backup=False, allow_transient=allow_transient()
    )
    path = _save_operation(workspace, document)
    index_warning = _refresh_index(slug, root)
    return {
        "workspace_id": slug,
        "operation_id": identifier,
        "operation_path": str(path),
        "status": "proposed",
        "change": document["changes"][0],
        "diff": diff_text,
        "index_warning": index_warning,
    }


def list_operations(
    slug: str,
    *,
    status: str | None = None,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    workspace, _ = load_manifest(slug, root=root)
    result: list[dict[str, Any]] = []
    for path in sorted(_operations_root(workspace).glob("op_*.json"), reverse=True):
        try:
            document = load_json_object(path)
            validate_operation_document(document)
        except (OSError, ValueError, json.JSONDecodeError, MemoryWorkspaceError) as exc:
            raise MemoryWorkspaceError(f"Operation 审计记录损坏 {path}：{exc}") from exc
        if status and document.get("status") != status:
            continue
        result.append(
            {
                "operation_id": document["operation_id"],
                "type": document["type"],
                "status": document["status"],
                "requested_at": document["requested_at"],
                "actor": document["actor"],
                "changes_count": len(document["changes"]),
            }
        )
    return result


def show_operation(
    slug: str,
    identifier: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    workspace, document = load_operation(slug, identifier, root=root)
    diffs: list[dict[str, str]] = []
    for index, change in enumerate(document["changes"]):
        _, _, diff_text = _verified_artifacts(workspace, document, index, change)
        diffs.append({"path": change["path"], "diff": diff_text})
    return {"workspace_id": slug, "operation": document, "diffs": diffs}


def _verified_artifacts(
    workspace: Path,
    document: dict[str, Any],
    index: int,
    change: dict[str, Any],
) -> tuple[bytes | None, bytes | None, str]:
    artifacts = _artifact_paths(workspace, document["operation_id"], index)
    expected_diff_path = artifacts["diff"].relative_to(workspace).as_posix()
    if change.get("diff_path") != expected_diff_path:
        raise MemoryWorkspaceError(f"Operation diff 路径异常：{change.get('diff_path')}")
    before_data = artifacts["before"].read_bytes() if artifacts["before"].is_file() else None
    after_data = artifacts["after"].read_bytes() if artifacts["after"].is_file() else None
    if change.get("before_hash") is not None:
        if before_data is None or sha256_bytes(before_data) != change.get("before_hash"):
            raise MemoryWorkspaceError(f"Proposal 暂存的 before 内容哈希异常：{change['path']}")
    elif before_data is not None:
        raise MemoryWorkspaceError(f"新建 Proposal 不应包含 before 内容：{change['path']}")
    if change["action"] in {"create", "update"}:
        if after_data is None or sha256_bytes(after_data) != change.get("after_hash"):
            raise MemoryWorkspaceError(f"Proposal 暂存的 after 内容哈希异常：{change['path']}")
    elif after_data is not None or change.get("after_hash") is not None:
        raise MemoryWorkspaceError(f"删除 Proposal 不应包含 after 内容：{change['path']}")
    try:
        before_text = before_data.decode("utf-8") if before_data is not None else ""
        after_text = after_data.decode("utf-8") if after_data is not None else ""
        stored_diff = artifacts["diff"].read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise MemoryWorkspaceError(f"无法读取 Proposal 暂存内容：{change['path']}：{exc}") from exc
    expected_diff = _unified_diff(change["path"], before_text, after_text)
    if stored_diff != expected_diff:
        raise MemoryWorkspaceError(f"Proposal Diff 与暂存内容不一致：{change['path']}")
    return before_data, after_data, expected_diff


def approve_operation(
    slug: str,
    identifier: str,
    *,
    actor: str = "owner_via_cli",
    root: Path | None = None,
) -> dict[str, Any]:
    workspace, document = load_operation(slug, identifier, root=root)
    if document["status"] != "proposed":
        raise MemoryWorkspaceError(
            f"只有 proposed Operation 可以批准；当前状态是 {document['status']}。"
        )
    decided_at = now_iso()
    document["status"] = "approved"
    document["approval"] = {"status": "approved", "decided_at": decided_at, "actor": actor}
    _save_operation(workspace, document)
    return {
        "workspace_id": slug,
        "operation_id": identifier,
        "status": "approved",
        "index_warning": _refresh_index(slug, root),
    }


def reject_operation(
    slug: str,
    identifier: str,
    *,
    actor: str = "owner_via_cli",
    root: Path | None = None,
) -> dict[str, Any]:
    workspace, document = load_operation(slug, identifier, root=root)
    if document["status"] != "proposed":
        raise MemoryWorkspaceError(
            f"只有 proposed Operation 可以拒绝；当前状态是 {document['status']}。"
        )
    decided_at = now_iso()
    document["status"] = "rejected"
    document["approval"] = {"status": "rejected", "decided_at": decided_at, "actor": actor}
    _save_operation(workspace, document)
    return {
        "workspace_id": slug,
        "operation_id": identifier,
        "status": "rejected",
        "index_warning": _refresh_index(slug, root),
    }


def _preflight(
    workspace: Path,
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    _validate_input_refs(workspace, list(document.get("input_refs", [])))
    for index, change in enumerate(document["changes"]):
        action = change["action"]
        target = _resolve_target(workspace, change["path"], deleting=action == "delete")
        current_hash = file_hash_or_none(target)
        if current_hash != change.get("before_hash"):
            raise MemoryWorkspaceError(
                f"Proposal 已过期：{change['path']} 当前哈希与 before_hash 不一致，请重新生成。"
            )
        if action not in {"create", "update", "delete"}:
            raise MemoryWorkspaceError(f"不支持的变更类型：{action}")
        before_data, after_data, _ = _verified_artifacts(
            workspace, document, index, change
        )
        plan.append(
            {
                "target": target,
                "action": action,
                "before_data": before_data,
                "after_data": after_data,
            }
        )
    return plan


def _rollback(applied: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for item in reversed(applied):
        target: Path = item["target"]
        before_data: bytes | None = item["before_data"]
        try:
            if before_data is None:
                target.unlink(missing_ok=True)
            else:
                atomic_write_text(
                    target,
                    before_data.decode("utf-8"),
                    backup=False,
                    allow_transient=allow_transient(),
                )
        except (OSError, UnicodeDecodeError, MemoryWorkspaceError) as exc:
            errors.append(f"rollback {target}: {exc}")
    return errors


def apply_operation(
    slug: str,
    identifier: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    workspace, document = load_operation(slug, identifier, root=root)
    if document["status"] != "approved":
        raise MemoryWorkspaceError(
            f"只有 approved Operation 可以应用；当前状态是 {document['status']}。"
        )
    plan = _preflight(workspace, document)
    applied: list[dict[str, Any]] = []
    try:
        for item in plan:
            target: Path = item["target"]
            if item["action"] == "delete":
                target.unlink()
            else:
                atomic_write_text(
                    target,
                    item["after_data"].decode("utf-8"),
                    backup=False,
                    allow_transient=allow_transient(),
                )
            applied.append(item)
        report = check_workspace(slug, root=root)
        if report["status"] != "OK":
            raise MemoryWorkspaceError("Workspace 校验失败：" + "; ".join(report["errors"]))
    except (OSError, UnicodeDecodeError, MemoryWorkspaceError) as exc:
        rollback_errors = _rollback(applied)
        errors = [str(exc), *rollback_errors]
        document["status"] = "failed"
        document["validation"] = {
            "status": "failed",
            "checks": list(document["validation"].get("checks", [])) + ["workspace-check"],
            "errors": errors,
        }
        _save_operation(workspace, document)
        _refresh_index(slug, root)
        raise MemoryWorkspaceError("Apply 失败，已尝试回滚：" + "; ".join(errors)) from exc

    document["status"] = "applied"
    checks = list(document["validation"].get("checks", []))
    if "workspace-check" not in checks:
        checks.append("workspace-check")
    document["validation"] = {"status": "passed", "checks": checks, "errors": []}
    _save_operation(workspace, document)

    index_warning = _refresh_index(slug, root)
    return {
        "workspace_id": slug,
        "operation_id": identifier,
        "status": "applied",
        "changed_paths": [change["path"] for change in document["changes"]],
        "index_warning": index_warning,
    }
