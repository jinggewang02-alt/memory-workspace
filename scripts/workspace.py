#!/usr/bin/env python3
"""CLI for capturing, reviewing, indexing, and querying Memory Workspaces."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_workspace.io import MemoryWorkspaceError  # noqa: E402
from memory_workspace import index as workspace_index  # noqa: E402
from memory_workspace import operations, workspace  # noqa: E402


def add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Memory Workspace local-first CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    command = sub.add_parser("doctor", help="show the resolved durable workspace root")
    add_json_flag(command)

    command = sub.add_parser("init", help="create a new project workspace")
    command.add_argument("workspace_id")
    command.add_argument("--name", required=True)
    command.add_argument("--language", default="zh-CN")
    command.add_argument("--timezone", default="Asia/Shanghai")
    command.add_argument(
        "--review-mode",
        choices=("direct", "proposed_changes"),
        default="proposed_changes",
    )
    add_json_flag(command)

    command = sub.add_parser("list", help="list initialized workspaces")
    add_json_flag(command)

    command = sub.add_parser("inspect", help="show one workspace manifest and counts")
    command.add_argument("workspace_id")
    add_json_flag(command)

    command = sub.add_parser("check", help="validate layout, integrity, links, and operations")
    command.add_argument("workspace_id")
    add_json_flag(command)

    source = sub.add_parser("source", help="manage immutable workspace sources")
    source_sub = source.add_subparsers(dest="source_cmd", required=True)
    command = source_sub.add_parser("ingest", help="copy a file and create its source note")
    command.add_argument("workspace_id")
    command.add_argument("file", type=Path)
    command.add_argument("--title")
    add_json_flag(command)

    operation = sub.add_parser("operation", help="propose, review, and apply Wiki changes")
    operation_sub = operation.add_subparsers(dest="operation_cmd", required=True)

    command = operation_sub.add_parser("propose-file", help="stage one Markdown change")
    command.add_argument("workspace_id")
    command.add_argument("target_path")
    candidate = command.add_mutually_exclusive_group(required=True)
    candidate.add_argument("--content-file", type=Path)
    candidate.add_argument("--delete", action="store_true")
    command.add_argument("--input-ref", action="append")
    command.add_argument("--actor", default="owner_via_agent")
    command.add_argument(
        "--type",
        dest="operation_type",
        choices=("capture", "synthesis", "lint"),
        default="synthesis",
    )
    add_json_flag(command)

    command = operation_sub.add_parser("list", help="list operation summaries")
    command.add_argument("workspace_id")
    command.add_argument(
        "--status",
        choices=("draft", "proposed", "approved", "applied", "rejected", "failed"),
    )
    add_json_flag(command)

    command = operation_sub.add_parser("show", help="show an operation and its diffs")
    command.add_argument("workspace_id")
    command.add_argument("operation_id")
    add_json_flag(command)

    for action in ("approve", "reject"):
        command = operation_sub.add_parser(action, help=f"{action} a proposed operation")
        command.add_argument("workspace_id")
        command.add_argument("operation_id")
        command.add_argument("--actor", default="owner_via_cli")
        add_json_flag(command)

    command = operation_sub.add_parser("apply", help="apply an approved operation")
    command.add_argument("workspace_id")
    command.add_argument("operation_id")
    add_json_flag(command)

    index = sub.add_parser("index", help="manage the disposable UI/search index")
    index_sub = index.add_subparsers(dest="index_cmd", required=True)
    command = index_sub.add_parser("rebuild", help="rebuild the workspace read model")
    command.add_argument("workspace_id")
    add_json_flag(command)

    command = sub.add_parser("query", help="query the rebuilt local index")
    command.add_argument("workspace_id")
    command.add_argument("keyword")
    command.add_argument("--limit", type=int, default=20)
    command.add_argument("--no-rebuild", action="store_true")
    add_json_flag(command)
    return parser


def command_name(args: argparse.Namespace) -> str:
    if args.cmd == "source":
        return f"source.{args.source_cmd}"
    if args.cmd == "operation":
        return f"operation.{args.operation_cmd}"
    if args.cmd == "index":
        return f"index.{args.index_cmd}"
    return args.cmd


def run_command(args: argparse.Namespace) -> Any:
    if args.cmd == "doctor":
        return workspace.doctor()
    if args.cmd == "init":
        return workspace.init_workspace(
            args.workspace_id,
            name=args.name,
            language=args.language,
            timezone_name=args.timezone,
            review_mode=args.review_mode,
        )
    if args.cmd == "list":
        return workspace.list_workspaces()
    if args.cmd == "inspect":
        return workspace.inspect_workspace(args.workspace_id)
    if args.cmd == "check":
        return workspace.check_workspace(args.workspace_id)
    if args.cmd == "source" and args.source_cmd == "ingest":
        return workspace.ingest_source(args.workspace_id, args.file, title=args.title)
    if args.cmd == "operation" and args.operation_cmd == "propose-file":
        return operations.propose_file(
            args.workspace_id,
            args.target_path,
            content_file=args.content_file,
            delete=args.delete,
            input_refs=args.input_ref,
            actor=args.actor,
            operation_type=args.operation_type,
        )
    if args.cmd == "operation" and args.operation_cmd == "list":
        return operations.list_operations(args.workspace_id, status=args.status)
    if args.cmd == "operation" and args.operation_cmd == "show":
        return operations.show_operation(args.workspace_id, args.operation_id)
    if args.cmd == "operation" and args.operation_cmd == "approve":
        return operations.approve_operation(
            args.workspace_id, args.operation_id, actor=args.actor
        )
    if args.cmd == "operation" and args.operation_cmd == "reject":
        return operations.reject_operation(
            args.workspace_id, args.operation_id, actor=args.actor
        )
    if args.cmd == "operation" and args.operation_cmd == "apply":
        return operations.apply_operation(args.workspace_id, args.operation_id)
    if args.cmd == "index" and args.index_cmd == "rebuild":
        return workspace_index.rebuild_index(args.workspace_id)
    if args.cmd == "query":
        return workspace_index.query_index(
            args.workspace_id,
            args.keyword,
            limit=args.limit,
            rebuild=not args.no_rebuild,
        )
    raise MemoryWorkspaceError(f"未知命令：{command_name(args)}")


def render_doctor(result: dict[str, Any]) -> None:
    for key in (
        "workspaces_root",
        "source",
        "exists",
        "nearest_existing_parent",
        "filesystem_writable",
        "transient_risk",
        "status",
    ):
        value = result[key]
        if isinstance(value, bool):
            value = "yes" if value else "no"
        print(f"{key}: {value}")
    for warning in result["warnings"]:
        print(f"warning: {warning}")
    print(f"note: {result['note']}")


def render_text(args: argparse.Namespace, result: Any) -> None:
    name = command_name(args)
    if name == "doctor":
        render_doctor(result)
    elif name == "init":
        print(f"[mwork] 已创建 {result['workspace_id']}：{result['workspace_path']}")
        print(f"operation: {result['operation_id']}")
    elif name == "list":
        if not result:
            print("[mwork] （暂无 workspace）")
        for item in result:
            suffix = f" — {item['error']}" if "error" in item else ""
            print(f"{item['workspace_id']}\t{item['name']}\t{item['workspace_path']}{suffix}")
    elif name == "inspect":
        manifest = result["manifest"]
        print(f"workspace_id: {result['workspace_id']}")
        print(f"name: {manifest['workspace']['name']}")
        print(f"path: {result['workspace_path']}")
        print(f"sources: {result['counts']['sources']}")
        print(f"operations: {result['counts']['operations']}")
        print(f"latest_operation: {result['latest_operation'] or '-'}")
    elif name == "check":
        print(f"status: {result['status']}")
        for error in result["errors"]:
            print(f"error: {error}")
        for warning in result["warnings"]:
            print(f"warning: {warning}")
        print(
            "counts: "
            f"sources={result['counts']['source_notes']} "
            f"pages={result['counts']['wiki_pages']} "
            f"operations={result['counts']['operations']}"
        )
    elif name == "source.ingest":
        if result["duplicate"]:
            print(
                f"[mwork] 内容已收录为 {result['source_id']}，未创建重复来源："
                f"{result['content_path']}"
            )
        else:
            print(f"[mwork] 已收录 {result['source_id']}：{result['content_path']}")
            print(f"operation: {result['operation_id']}")
    elif name == "operation.propose-file":
        print(f"[mwork] 已创建 Proposal：{result['operation_id']}")
        print(f"change: {result['change']['action']} {result['change']['path']}")
        if result["diff"]:
            sys.stdout.write(result["diff"])
    elif name == "operation.list":
        if not result:
            print("[mwork] （没有匹配的 Operation）")
        for item in result:
            print(
                f"{item['operation_id']}\t{item['status']}\t"
                f"{item['type']}\t{item['changes_count']} change(s)"
            )
    elif name == "operation.show":
        operation = result["operation"]
        print(f"operation_id: {operation['operation_id']}")
        print(f"status: {operation['status']}")
        print(f"type: {operation['type']}")
        for item in result["diffs"]:
            print(f"\n# {item['path']}")
            sys.stdout.write(item["diff"])
    elif name in {"operation.approve", "operation.reject", "operation.apply"}:
        print(f"[mwork] {result['operation_id']} → {result['status']}")
        if result.get("index_warning"):
            print(f"warning: {result['index_warning']}")
    elif name == "index.rebuild":
        print(f"[mwork] 已重建索引：{result['index_path']}")
        print(
            "counts: "
            f"projects={result['counts']['projects']} "
            f"sources={result['counts']['sources']} "
            f"pages={result['counts']['pages']} "
            f"operations={result['counts']['operations']}"
        )
    elif name == "query":
        print(f"matches: {result['count']} / {result['total_matches']}")
        for item in result["results"]:
            print(f"[{item['type']}] {item['title']} — {item['path']}")
            if item["snippet"]:
                print(f"  {item['snippet']}")


def main() -> int:
    args = build_parser().parse_args()
    name = command_name(args)
    try:
        result = run_command(args)
    except MemoryWorkspaceError as exc:
        if getattr(args, "as_json", False):
            print(json.dumps({"ok": False, "command": name, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"[mwork] 错误：{exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps({"ok": True, "command": name, "result": result}, ensure_ascii=False))
    else:
        render_text(args, result)
    if name == "check" and result["status"] != "OK":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
