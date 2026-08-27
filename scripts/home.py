#!/usr/bin/env python3
"""Initialize, inspect, and safely migrate a local Memory Home."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_workspace import home  # noqa: E402
from memory_workspace.io import MemoryWorkspaceError  # noqa: E402


def add_shared(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified Personal Memory, Workspaces, and system state"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    command = sub.add_parser("status", help="inspect without creating files")
    add_shared(command)

    command = sub.add_parser("doctor", help="check persistent path readiness")
    add_shared(command)

    command = sub.add_parser("init", help="initialize the Memory Home layout")
    command.add_argument("--id", default="default", dest="home_id")
    command.add_argument("--language", default="zh-CN")
    command.add_argument("--timezone", default="Asia/Shanghai")
    add_shared(command)

    for name, help_text in (
        ("migration-plan", "preview legacy copies without writing"),
        ("migrate", "copy legacy data and keep all source files"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--legacy-personal-root", type=Path)
        command.add_argument("--legacy-runtime-root", type=Path)
        add_shared(command)
    return parser


def run_command(args: argparse.Namespace) -> Any:
    if args.cmd == "status":
        return home.check_home(root=args.home)
    if args.cmd == "doctor":
        return home.doctor(root=args.home)
    if args.cmd == "init":
        return home.init_home(
            root=args.home,
            home_id=args.home_id,
            language=args.language,
            timezone_name=args.timezone,
        )
    keywords = {
        "root": args.home,
        "legacy_personal_root": args.legacy_personal_root,
        "legacy_runtime_root": args.legacy_runtime_root,
    }
    if args.cmd == "migration-plan":
        return home.migration_plan(**keywords)
    if args.cmd == "migrate":
        return home.migrate_legacy(**keywords)
    raise MemoryWorkspaceError(f"未知命令：{args.cmd}")


def render_text(args: argparse.Namespace, result: dict[str, Any]) -> None:
    print(f"home_path: {result['home_path']}")
    if args.cmd in {"status", "init"}:
        print(f"status: {result['status']}")
        print(f"initialized: {'yes' if result['initialized'] else 'no'}")
        if "created" in result:
            print(f"created: {'yes' if result['created'] else 'no'}")
    elif args.cmd == "doctor":
        print(f"status: {result['status']}")
        print(f"writable: {'yes' if result['filesystem_writable'] else 'no'}")
        print(f"transient: {'yes' if result['transient_risk'] else 'no'}")
    elif args.cmd == "migration-plan":
        print(f"can_apply: {'yes' if result['can_apply'] else 'no'}")
        print(f"copy: {result['counts']['copy']}")
        print(f"conflict: {result['counts']['conflict']}")
        print("source_deleted: no")
    elif args.cmd == "migrate":
        print(f"status: {result['status']}")
        print(f"copied: {result['copied']}")
        print("source_deleted: no")
        print(f"receipt_path: {result['receipt_path']}")


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_command(args)
        if args.as_json:
            print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        else:
            render_text(args, result)
        if args.cmd == "status" and result["status"] != "OK":
            return 1
        return 0
    except MemoryWorkspaceError as exc:
        if getattr(args, "as_json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
