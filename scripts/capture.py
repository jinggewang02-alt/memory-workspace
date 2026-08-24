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

from memory_workspace import capture, episodes, evaluation, feedback, history, policy  # noqa: E402
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
    command.add_argument("--source-adapter", default="generic-cli")
    command.add_argument("--source-kind", choices=("live", "history_import"), default="live")
    command.add_argument("--occurred-at")
    command.add_argument(
        "--direct-route", choices=("none", "workspace", "remember", "recall"), default="none"
    )
    command.add_argument("--observation-only", action="store_true")
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
    command.add_argument("--kind", choices=("fact", "preference", "decision", "learning"))
    command.add_argument("--episode-id")
    command.add_argument("--evidence-event-id", action="append", dest="evidence_event_ids")
    command.add_argument("--policy-version")
    command.add_argument("--policy-rule")
    command.add_argument("--trigger-phase")
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
        command.add_argument("--edited-content")
        command.add_argument("--feedback-reason")
        command.add_argument("--suppress-similar", action="store_true")
        add_json_flag(command)

    command = candidate_sub.add_parser(
        "mark-applied", help="record a verified canonical write performed by the single writer"
    )
    command.add_argument("candidate_id")
    command.add_argument("--actor", default="owner_via_agent")
    command.add_argument("--verification", required=True)
    add_json_flag(command)

    history_parser = sub.add_parser("history", help="import bounded, platform-neutral query history")
    history_sub = history_parser.add_subparsers(dest="history_cmd", required=True)
    command = history_sub.add_parser("import", help="import normalized JSONL user queries")
    command.add_argument("--file", type=Path, required=True)
    command.add_argument("--adapter", default="generic-jsonl")
    command.add_argument("--retention-days", type=int, default=30)
    command.add_argument("--limit", type=int, default=1000)
    add_json_flag(command)

    episode_parser = sub.add_parser("episode", help="group events into conversation stages")
    episode_sub = episode_parser.add_subparsers(dest="episode_cmd", required=True)
    command = episode_sub.add_parser("list", help="list grouped episodes")
    command.add_argument("--status", choices=("pending", "resolved", "all"), default="pending")
    command.add_argument("--idle-minutes", type=int, default=30)
    command.add_argument("--limit", type=int, default=50)
    add_json_flag(command)
    command = episode_sub.add_parser("show", help="show one episode with its evidence events")
    command.add_argument("episode_id")
    command.add_argument("--idle-minutes", type=int, default=30)
    add_json_flag(command)
    command = episode_sub.add_parser("resolve", help="resolve all pending events in one episode")
    command.add_argument("episode_id")
    command.add_argument("--decision", choices=("ignore", "session", "project", "profile"), required=True)
    command.add_argument("--reason", required=True)
    command.add_argument("--resolved-by", default="async_worker")
    command.add_argument("--trigger-event-id")
    command.add_argument("--content")
    command.add_argument("--confidence", type=float)
    command.add_argument("--sensitivity", choices=("normal", "sensitive"), default="normal")
    command.add_argument("--workspace-id")
    command.add_argument("--profile-key")
    command.add_argument("--kind", choices=("fact", "preference", "decision", "learning"))
    command.add_argument("--policy-version")
    command.add_argument("--policy-rule")
    command.add_argument("--idle-minutes", type=int, default=30)
    add_json_flag(command)

    policy_parser = sub.add_parser("policy", help="build and activate a personal trigger policy")
    policy_sub = policy_parser.add_subparsers(dest="policy_cmd", required=True)
    command = policy_sub.add_parser("build", help="build a draft from current local events")
    add_json_flag(command)
    command = policy_sub.add_parser("list", help="list policy drafts and activation state")
    add_json_flag(command)
    command = policy_sub.add_parser("show", help="show one policy")
    command.add_argument("policy_id")
    add_json_flag(command)
    command = policy_sub.add_parser("activate", help="activate a reviewed policy draft")
    command.add_argument("policy_id")
    command.add_argument("--actor", default="owner_via_cli")
    add_json_flag(command)

    worker_parser = sub.add_parser("worker", help="plan pending episode batches without model work")
    worker_sub = worker_parser.add_subparsers(dest="worker_cmd", required=True)
    command = worker_sub.add_parser("plan", help="show policy-aware work for one background Worker")
    command.add_argument("--idle-minutes", type=int, default=30)
    command.add_argument("--limit", type=int, default=20)
    add_json_flag(command)

    feedback_parser = sub.add_parser("feedback", help="append outcomes that improve future policies")
    feedback_sub = feedback_parser.add_subparsers(dest="feedback_cmd", required=True)
    command = feedback_sub.add_parser("record", help="record an explicit outcome")
    command.add_argument(
        "--action",
        choices=("approved", "rejected", "edited", "suppress_similar", "explicit_save_followup", "recalled", "forgotten_complaint"),
        required=True,
    )
    command.add_argument("--actor", default="owner_via_cli")
    command.add_argument("--candidate-id")
    command.add_argument("--event-id")
    command.add_argument("--fingerprint")
    command.add_argument("--reason-code")
    add_json_flag(command)
    command = feedback_sub.add_parser("list", help="list append-only feedback")
    add_json_flag(command)

    eval_parser = sub.add_parser("eval", help="offline replay without canonical writes")
    eval_sub = eval_parser.add_subparsers(dest="eval_cmd", required=True)
    command = eval_sub.add_parser("replay", help="time-split history replay")
    command.add_argument("--split-time", required=True)
    command.add_argument("--idle-minutes", type=int, default=30)
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
    if args.cmd == "history":
        return f"history.{args.history_cmd}"
    if args.cmd == "episode":
        return f"episode.{args.episode_cmd}"
    if args.cmd == "policy":
        return f"policy.{args.policy_cmd}"
    if args.cmd == "worker":
        return f"worker.{args.worker_cmd}"
    if args.cmd == "feedback":
        return f"feedback.{args.feedback_cmd}"
    if args.cmd == "eval":
        return f"eval.{args.eval_cmd}"
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
            source_adapter=args.source_adapter,
            source_kind=args.source_kind,
            occurred_at=args.occurred_at,
            direct_route=args.direct_route,
            capture_eligible=not args.observation_only,
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
            kind=args.kind,
            episode_id=args.episode_id,
            evidence_event_ids=args.evidence_event_ids,
            policy_version=args.policy_version,
            policy_rule=args.policy_rule,
            trigger_phase=args.trigger_phase,
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
            edited_content=args.edited_content,
            feedback_reason=args.feedback_reason,
            suppress_similar=args.suppress_similar,
        )
    if args.cmd == "candidate" and args.candidate_cmd == "mark-applied":
        return capture.mark_candidate_applied(
            args.candidate_id,
            actor=args.actor,
            verification=args.verification,
        )
    if args.cmd == "history" and args.history_cmd == "import":
        return history.import_jsonl(
            args.file,
            adapter=args.adapter,
            retention_days=args.retention_days,
            limit=args.limit,
        )
    if args.cmd == "episode" and args.episode_cmd == "list":
        return episodes.list_episodes(
            status=args.status, idle_minutes=args.idle_minutes, limit=args.limit
        )
    if args.cmd == "episode" and args.episode_cmd == "show":
        return episodes.show_episode(args.episode_id, idle_minutes=args.idle_minutes)
    if args.cmd == "episode" and args.episode_cmd == "resolve":
        return episodes.resolve_episode(
            args.episode_id,
            decision=args.decision,
            reason=args.reason,
            resolved_by=args.resolved_by,
            trigger_event_id=args.trigger_event_id,
            content=args.content,
            confidence=args.confidence,
            sensitivity=args.sensitivity,
            workspace_id=args.workspace_id,
            profile_key=args.profile_key,
            kind=args.kind,
            policy_version=args.policy_version,
            policy_rule=args.policy_rule,
            idle_minutes=args.idle_minutes,
        )
    if args.cmd == "policy" and args.policy_cmd == "build":
        return policy.build_policy(capture.all_event_documents())
    if args.cmd == "policy" and args.policy_cmd == "list":
        return policy.list_policies()
    if args.cmd == "policy" and args.policy_cmd == "show":
        return policy.show_policy(args.policy_id)
    if args.cmd == "policy" and args.policy_cmd == "activate":
        return policy.activate_policy(args.policy_id, actor=args.actor)
    if args.cmd == "worker" and args.worker_cmd == "plan":
        return episodes.plan_pending(idle_minutes=args.idle_minutes, limit=args.limit)
    if args.cmd == "feedback" and args.feedback_cmd == "record":
        return feedback.record_feedback(
            action=args.action,
            actor=args.actor,
            candidate_id=args.candidate_id,
            event_id=args.event_id,
            fingerprint=args.fingerprint,
            reason_code=args.reason_code,
        )
    if args.cmd == "feedback" and args.feedback_cmd == "list":
        return feedback.list_feedback()
    if args.cmd == "eval" and args.eval_cmd == "replay":
        return evaluation.replay(args.split_time, idle_minutes=args.idle_minutes)
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
    elif name == "history.import":
        print(
            f"[capture] imported={result['imported_count']} duplicate={result['duplicate_count']} "
            f"rejected_secret={result['rejected_secret_count']}"
        )
    elif name == "episode.list":
        if not result:
            print("[capture] （没有匹配 Episode）")
        for item in result:
            print(
                f"{item['episode_id']}\t{item['status']}\t{item['current_phase']}\t"
                f"{item['message_preview']}"
            )
    elif name in {"episode.show", "episode.resolve", "worker.plan", "policy.show", "eval.replay"}:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif name == "policy.build":
        print(f"[capture] {result['policy']['policy_id']} → draft")
    elif name == "policy.list":
        if not result:
            print("[capture] （没有 Policy）")
        for item in result:
            print(f"{item['policy_id']}\tactive={item['active']}\t{','.join(item['enabled_rules'])}")
    elif name == "policy.activate":
        print(f"[capture] {result['policy_id']} → active")
    elif name == "feedback.record":
        print(f"[capture] feedback {result['feedback']['feedback_id']} recorded")
    elif name == "feedback.list":
        if not result:
            print("[capture] （没有 Feedback）")
        for item in result:
            print(f"{item['feedback_id']}\t{item['action']}")
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
