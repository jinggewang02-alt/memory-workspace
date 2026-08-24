#!/usr/bin/env python3
"""CLI for the durable asynchronous capture queue and review inbox."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_workspace import capture  # noqa: E402
from memory_workspace.io import MemoryWorkspaceError  # noqa: E402


def add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Memory Workspace asynchronous capture CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    command = sub.add_parser("doctor", help="show the durable capture queue path")
    add_json_flag(command)

    event = sub.add_parser("event", help="enqueue and resolve immutable capture events")
    event_sub = event.add_subparsers(dest="event_cmd", required=True)

    command = event_sub.add_parser("enqueue", help="append one local event without model work")
    source = command.add_mutually_exclusive_group(required=True)
    source.add_argument("--message")
    source.add_argument("--message-file", type=Path)
    command.add_argument("--assistant-summary")
    command.add_argument("--conversation-id")
    command.add_argument("--message-id")
    command.add_argument("--workspace-id")
    command.add_argument("--source-agent", default="manual_cli")
    command.add_argument("--retention-days", type=int, default=7)
    add_json_flag(command)

    command = event_sub.add_parser("list", help="list pending or resolved events")
    command.add_argument(
        "--status", choices=("pending", "resolved", "expired", "all"), default="pending"
    )
    command.add_argument("--limit", type=int, default=50)
    add_json_flag(command)

    command = event_sub.add_parser("show", help="show one event and its resolution")
    command.add_argument("event_id")
    add_json_flag(command)

    command = event_sub.add_parser("resolve", help="resolve once; project/profile become candidates")
    command.add_argument("event_id")
    command.add_argument("--decision", choices=("ignore", "session", "project", "profile"), required=True)
    command.add_argument("--reason", required=True)
    command.add_argument("--resolved-by", default="async_worker")
    command.add_argument("--content")
    command.add_argument("--confidence", type=float)
    command.add_argument("--sensitivity", choices=("normal", "sensitive"), default="normal")
    command.add_argument("--workspace-id")
    command.add_argument("--profile-key")
    add_json_flag(command)

    candidate = sub.add_parser("candidate", help="review candidates without direct canonical writes")
    candidate_sub = candidate.add_subparsers(dest="candidate_cmd", required=True)

    command = candidate_sub.add_parser("list", help="list candidate inbox entries")
    command.add_argument(
        "--status",
        choices=("proposed", "approved", "rejected", "applied", "all"),
        default="proposed",
    )
    command.add_argument("--limit", type=int, default=50)
    add_json_flag(command)

    command = candidate_sub.add_parser("show", help="show one candidate audit bundle")
    command.add_argument("candidate_id")
    add_json_flag(command)

    for action in ("approve", "reject"):
        command = candidate_sub.add_parser(action, help=f"{action} one candidate")
        command.add_argument("candidate_id")
        command.add_argument("--actor", default="owner_via_cli")
        command.add_argument("--target-ref")
        add_json_flag(command)

    command = candidate_sub.add_parser(
        "mark-applied", help="record a verified canonical write performed by the single writer"
    )
    command.add_argument("candidate_id")
    command.add_argument("--actor", default="owner_via_agent")
    command.add_argument("--verification", required=True)
    add_json_flag(command)

    cleanup = sub.add_parser("cleanup", help="inspect or remove expired raw event payloads")
    cleanup_sub = cleanup.add_subparsers(dest="cleanup_cmd", required=True)
    command = cleanup_sub.add_parser("expired", help="dry-run by default")
    command.add_argument("--apply", action="store_true")
    add_json_flag(command)
    return parser


def command_name(args: argparse.Namespace) -> str:
    if args.cmd == "event":
        return f"event.{args.event_cmd}"
    if args.cmd == "candidate":
        return f"candidate.{args.candidate_cmd}"
    if args.cmd == "cleanup":
        return f"cleanup.{args.cleanup_cmd}"
    return args.cmd


def _read_message(args: argparse.Namespace) -> str:
    if args.message is not None:
        return args.message
    try:
        return args.message_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise MemoryWorkspaceError(f"无法读取 message file {args.message_file}：{exc}") from exc


def run_command(args: argparse.Namespace) -> Any:
    if args.cmd == "doctor":
        return capture.doctor()
    if args.cmd == "event" and args.event_cmd == "enqueue":
        return capture.enqueue_event(
            _read_message(args),
            assistant_summary=args.assistant_summary,
            conversation_id=args.conversation_id,
            message_id=args.message_id,
            workspace_id=args.workspace_id,
            source_agent=args.source_agent,
            retention_days=args.retention_days,
        )
    if args.cmd == "event" and args.event_cmd == "list":
        return capture.list_events(status=args.status, limit=args.limit)
    if args.cmd == "event" and args.event_cmd == "show":
        return capture.show_event(args.event_id)
    if args.cmd == "event" and args.event_cmd == "resolve":
        return capture.resolve_event(
            args.event_id,
            decision=args.decision,
            reason=args.reason,
            resolved_by=args.resolved_by,
            content=args.content,
            confidence=args.confidence,
            sensitivity=args.sensitivity,
            workspace_id=args.workspace_id,
            profile_key=args.profile_key,
        )
    if args.cmd == "candidate" and args.candidate_cmd == "list":
        return capture.list_candidates(status=args.status, limit=args.limit)
    if args.cmd == "candidate" and args.candidate_cmd == "show":
        return capture.show_candidate(args.candidate_id)
    if args.cmd == "candidate" and args.candidate_cmd in {"approve", "reject"}:
        return capture.decide_candidate(
            args.candidate_id,
            decision="approved" if args.candidate_cmd == "approve" else "rejected",
            actor=args.actor,
            target_ref=args.target_ref,
        )
    if args.cmd == "candidate" and args.candidate_cmd == "mark-applied":
        return capture.mark_candidate_applied(
            args.candidate_id,
            actor=args.actor,
            verification=args.verification,
        )
    if args.cmd == "cleanup" and args.cleanup_cmd == "expired":
        return capture.cleanup_expired(apply=args.apply)
    raise MemoryWorkspaceError(f"未知命令：{command_name(args)}")


def render_doctor(result: dict[str, Any]) -> None:
    for key in (
        "capture_root",
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
    elif name == "event.enqueue":
        print(f"[capture] 已入队 {result['event_id']}（pending）")
        print(f"expires_at: {result['expires_at']}")
    elif name == "event.list":
        if not result:
            print("[capture] （没有匹配事件）")
        for item in result:
            print(f"{item['event_id']}\t{item['status']}\t{item['message_preview']}")
    elif name == "event.show":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif name == "event.resolve":
        print(f"[capture] {result['event_id']} → {result['decision']}")
        if result["candidate_id"]:
            print(f"candidate: {result['candidate_id']}")
    elif name == "candidate.list":
        if not result:
            print("[capture] （没有匹配候选）")
        for item in result:
            print(
                f"{item['candidate_id']}\t{item['status']}\t"
                f"{item['scope']}\t{item['content_preview']}"
            )
    elif name == "candidate.show":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif name in {"candidate.approve", "candidate.reject", "candidate.mark-applied"}:
        print(f"[capture] {result['candidate_id']} → {result['status']}")
    elif name == "cleanup.expired":
        mode = "removed" if not result["dry_run"] else "would_remove"
        print(f"[capture] {mode}: {result['expired_count']}")


def main() -> int:
    args = build_parser().parse_args()
    name = command_name(args)
    try:
        result = run_command(args)
    except MemoryWorkspaceError as exc:
        if getattr(args, "as_json", False):
            print(json.dumps({"ok": False, "command": name, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"[capture] 错误：{exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps({"ok": True, "command": name, "result": result}, ensure_ascii=False))
    else:
        render_text(args, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
