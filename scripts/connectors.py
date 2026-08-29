#!/usr/bin/env python3
"""Configure and plan explicit, provider-gated Memory Workspace connectors."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_workspace import connectors  # noqa: E402
from memory_workspace import lark_sync  # noqa: E402
from memory_workspace.io import MemoryWorkspaceError  # noqa: E402


def add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explicit domain connector configuration and read planning"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    command = sub.add_parser("status", help="show state without probing the provider")
    command.add_argument("workspace_id")
    command.add_argument("--provider", default="lark")
    add_json_flag(command)

    command = sub.add_parser(
        "enable-lark", help="explicitly enable the Lark-only baseline and daily planner"
    )
    command.add_argument("workspace_id")
    command.add_argument("--actor", default="owner_via_cli")
    command.add_argument("--lookback-days", type=int, default=30)
    command.add_argument("--max-active-chats", type=int, default=30)
    command.add_argument("--initial-messages-per-chat", type=int, default=20)
    command.add_argument("--expanded-messages-per-chat", type=int, default=50)
    command.add_argument("--minimum-interval-hours", type=int, default=24)
    command.add_argument("--event-acceleration", action="store_true")
    add_json_flag(command)

    command = sub.add_parser("disable", help="disable a connector without deleting audit state")
    command.add_argument("workspace_id")
    command.add_argument("--provider", default="lark")
    command.add_argument("--actor", default="owner_via_cli")
    add_json_flag(command)

    command = sub.add_parser("plan", help="return a bounded read plan; never executes lark-cli")
    command.add_argument("workspace_id")
    command.add_argument("--provider", default="lark")
    command.add_argument("--at")
    command.add_argument("--force", action="store_true")
    add_json_flag(command)

    command = sub.add_parser("map-chat", help="map one confirmed Lark chat to this Workspace")
    command.add_argument("workspace_id")
    command.add_argument("--chat-id", required=True)
    command.add_argument("--label", required=True)
    add_json_flag(command)

    command = sub.add_parser("map-document", help="map one confirmed Lark document to this Workspace")
    command.add_argument("workspace_id")
    command.add_argument("--document-id", required=True)
    command.add_argument("--doc", required=True, help="document URL or token used by lark-cli docs +fetch")
    command.add_argument("--label", required=True)
    add_json_flag(command)

    command = sub.add_parser("sources", help="list explicitly mapped project sources")
    command.add_argument("workspace_id")
    add_json_flag(command)

    command = sub.add_parser("sync", help="read only explicitly mapped Lark sources")
    command.add_argument("workspace_id")
    command.add_argument("--at")
    command.add_argument("--force", action="store_true")
    command.add_argument("--trigger", choices=("first_enable", "agent_start", "idle", "manual", "event_reconciliation"))
    add_json_flag(command)

    command = sub.add_parser("project-view", help="show the rebuildable project memory view without refreshing Lark")
    command.add_argument("workspace_id")
    add_json_flag(command)

    command = sub.add_parser(
        "checkpoint", help="record success only after immutable snapshots were saved"
    )
    command.add_argument("workspace_id")
    command.add_argument("--provider", default="lark")
    command.add_argument("--coverage-start", required=True)
    command.add_argument("--coverage-end", required=True)
    command.add_argument("--snapshot-ref", required=True)
    command.add_argument(
        "--trigger",
        choices=("first_enable", "agent_start", "idle", "manual", "event_reconciliation"),
        required=True,
    )
    command.add_argument("--incomplete", action="store_true")
    command.add_argument("--high-watermark-external-id")
    add_json_flag(command)
    return parser


def run_command(args: argparse.Namespace) -> Any:
    if args.cmd == "status":
        return connectors.connector_status(args.workspace_id, provider=args.provider)
    if args.cmd == "enable-lark":
        return connectors.enable_lark_connector(
            args.workspace_id,
            actor=args.actor,
            lookback_days=args.lookback_days,
            max_active_chats=args.max_active_chats,
            initial_messages_per_chat=args.initial_messages_per_chat,
            expanded_messages_per_chat=args.expanded_messages_per_chat,
            minimum_interval_hours=args.minimum_interval_hours,
            event_acceleration=args.event_acceleration,
        )
    if args.cmd == "disable":
        return connectors.disable_connector(
            args.workspace_id, provider=args.provider, actor=args.actor
        )
    if args.cmd == "plan":
        return connectors.plan_connector_sync(
            args.workspace_id,
            provider=args.provider,
            now=args.at,
            force=args.force,
        )
    if args.cmd == "map-chat":
        return connectors.map_lark_source(
            args.workspace_id,
            kind="chat",
            external_id=args.chat_id,
            label=args.label,
        )
    if args.cmd == "map-document":
        return connectors.map_lark_source(
            args.workspace_id,
            kind="document",
            external_id=args.document_id,
            locator=args.doc,
            label=args.label,
        )
    if args.cmd == "sources":
        return {
            "workspace_id": args.workspace_id,
            "provider": "lark",
            "sources": connectors.list_connector_sources(args.workspace_id),
            "external_read_performed": False,
        }
    if args.cmd == "sync":
        return lark_sync.sync_lark_workspace(
            args.workspace_id,
            now=args.at,
            force=args.force,
            trigger=args.trigger,
        )
    if args.cmd == "project-view":
        return lark_sync.load_project_memory(args.workspace_id)
    if args.cmd == "checkpoint":
        return connectors.record_sync_success(
            args.workspace_id,
            provider=args.provider,
            coverage_start=args.coverage_start,
            coverage_end=args.coverage_end,
            snapshot_ref=args.snapshot_ref,
            trigger=args.trigger,
            complete=not args.incomplete,
            high_watermark_external_id=args.high_watermark_external_id,
        )
    raise MemoryWorkspaceError(f"未知命令：{args.cmd}")


def render_text(args: argparse.Namespace, result: Any) -> None:
    if args.cmd == "status":
        print(f"provider: {result['provider']}")
        print(f"configured: {'yes' if result['configured'] else 'no'}")
        print(f"enabled: {'yes' if result['enabled'] else 'no'}")
        print(f"boundary: {result['boundary']}")
    elif args.cmd == "enable-lark":
        print(f"[connector] lark enabled for {result['workspace_id']}")
        print("external_read_performed: no")
        print(f"next_action: {result['next_action']}")
    elif args.cmd == "disable":
        print(f"[connector] {result['provider']} disabled for {result['workspace_id']}")
    elif args.cmd == "plan":
        print(f"status: {result['status']}")
        if result.get("phase"):
            print(f"phase: {result['phase']}")
        print(f"commands: {len(result['commands'])}")
        if result.get("reason"):
            print(f"reason: {result['reason']}")
    elif args.cmd in {"map-chat", "map-document"}:
        source = result["source"]
        print(f"[connector] mapped {source['kind']}: {source['label']}")
        print("external_read_performed: no")
    elif args.cmd == "sources":
        print(f"mapped_sources: {len(result['sources'])}")
        for source in result["sources"]:
            print(f"- {source['kind']}: {source['label']} ({source['external_id']})")
    elif args.cmd == "sync":
        print(f"status: {result['status']}")
        print(f"mapped_sources: {result['mapped_sources']}")
        if result.get("manifest_ref"):
            print(f"manifest: {result['manifest_ref']}")
    elif args.cmd == "project-view":
        print(f"status: {result['status']}")
        print(f"mapped_sources: {result['connector']['mapped_sources']}")
        for section in ("updates", "decisions", "next_actions", "people", "artifacts"):
            print(f"{section}: {len(result['sections'][section])}")
    elif args.cmd == "checkpoint":
        print(f"[connector] checkpoint updated: {result['checkpoint_path']}")
        print(f"next_due_at: {result['next_due_at']}")


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_command(args)
        if args.as_json:
            print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        else:
            render_text(args, result)
        return 0
    except MemoryWorkspaceError as exc:
        if getattr(args, "as_json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
