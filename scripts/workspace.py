#!/usr/bin/env python3
"""CLI for initializing, inspecting, checking, and ingesting Memory Workspaces."""

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
from memory_workspace import workspace  # noqa: E402


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
    return parser


def command_name(args: argparse.Namespace) -> str:
    return f"source.{args.source_cmd}" if args.cmd == "source" else args.cmd


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
