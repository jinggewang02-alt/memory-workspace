"""Group immutable capture events into stable conversation episodes."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import timedelta
from typing import Any

from .capture import parse_datetime
from .io import MemoryWorkspaceError


EXPLICIT_SAVE_PATTERNS = (
    re.compile(pattern, re.I)
    for pattern in (
        r"记入.{0,12}(wiki|知识库)",
        r"帮我记(?:住|一下|下来)?",
        r"记(?:住|一下|下来)",
        r"保存到.{0,12}(记忆|wiki|知识库)",
        r"\bremember\b",
        r"\bsave (?:this|that|it)\b",
    )
)
EXPLICIT_SAVE_PATTERNS = tuple(EXPLICIT_SAVE_PATTERNS)

SYNTHESIS_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"核心能学到什么",
        r"(?:总结|概述|归纳).{0,12}(一下|下|核心|结论)",
        r"(?:最终|正式|已经确认|决定|定稿)",
        r"所以.{0,20}(结论|核心|意味着|应该)",
        r"\b(?:takeaways?|final decision|summari[sz]e)\b",
    )
)

CLARIFY_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"没懂|没理解|不理解|为什么|究竟|怎么理解|什么意思|干啥",
        r"是不是|能不能|可以吗|对吗|有什么区别",
        r"\b(?:why|how|what does|difference|clarify)\b",
    )
)

REVISION_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"太长|略长|精简|简化|缩减|不要太复杂",
        r"这句话|这一段|这部分.{0,16}(修改|调整|润色|合并)",
        r"我觉得.{0,24}(不对|不好|不能这么|应该)",
        r"\b(?:shorter|too long|revise|rewrite)\b",
    )
)


def event_occurred_at(event: dict[str, Any]) -> str:
    return str(event.get("occurred_at") or event["created_at"])


def explicit_save(text: str) -> bool:
    return any(pattern.search(text) for pattern in EXPLICIT_SAVE_PATTERNS)


def classify_phase(event: dict[str, Any]) -> str:
    """Return a low-cost feature, not a persistence decision."""

    routing = event.get("routing") or {}
    if routing.get("direct_route") in {"remember", "workspace"}:
        return "commit"
    text = event["payload"]["user_message"]
    if explicit_save(text):
        return "commit"
    if any(pattern.search(text) for pattern in SYNTHESIS_PATTERNS):
        return "synthesize"
    if any(pattern.search(text) for pattern in REVISION_PATTERNS):
        return "revise"
    if any(pattern.search(text) for pattern in CLARIFY_PATTERNS):
        return "clarify"
    return "explore"


def _conversation_key(event: dict[str, Any]) -> str:
    source = event["source"]
    conversation_id = source.get("conversation_id")
    if conversation_id:
        return str(conversation_id)
    return event["event_id"]


def _episode_id(conversation_key: str, first_event_id: str) -> str:
    digest = hashlib.sha256(f"{conversation_key}\n{first_event_id}".encode()).hexdigest()
    return f"ep_{digest[:20]}"


def _build_episode(conversation_key: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    phases = [classify_phase(item) for item in items]
    counts = Counter(phases)
    first = items[0]
    last = items[-1]
    return {
        "episode_id": _episode_id(conversation_key, first["event_id"]),
        "conversation_id": first["source"].get("conversation_id"),
        "workspace_id": next(
            (item["source"].get("workspace_id") for item in reversed(items) if item["source"].get("workspace_id")),
            None,
        ),
        "started_at": event_occurred_at(first),
        "ended_at": event_occurred_at(last),
        "event_ids": [item["event_id"] for item in items],
        "pending_event_ids": [],
        "event_count": len(items),
        "phases": phases,
        "phase_counts": dict(sorted(counts.items())),
        "current_phase": phases[-1],
        "has_explicit_save": any(phase == "commit" for phase in phases),
        "has_direct_write": any(
            (item.get("routing") or {}).get("direct_route") in {"remember", "workspace"}
            for item in items
        ),
        "has_direct_route": any(
            (item.get("routing") or {}).get("direct_route")
            in {"remember", "workspace", "recall"}
            for item in items
        ),
        "events": items,
    }


def group_events(
    events: list[dict[str, Any]], *, idle_minutes: int = 30
) -> list[dict[str, Any]]:
    if idle_minutes < 1 or idle_minutes > 24 * 60:
        raise MemoryWorkspaceError("idle minutes 必须在 1–1440 之间。")
    by_conversation: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_conversation.setdefault(_conversation_key(event), []).append(event)

    result: list[dict[str, Any]] = []
    gap = timedelta(minutes=idle_minutes)
    for key, items in sorted(by_conversation.items()):
        ordered = sorted(items, key=lambda item: (parse_datetime(event_occurred_at(item)), item["event_id"]))
        current: list[dict[str, Any]] = []
        previous = None
        for event in ordered:
            occurred = parse_datetime(event_occurred_at(event))
            if current and previous is not None and occurred - previous > gap:
                result.append(_build_episode(key, current))
                current = []
            current.append(event)
            previous = occurred
        if current:
            result.append(_build_episode(key, current))
    return sorted(result, key=lambda item: (item["started_at"], item["episode_id"]))


def find_episode(
    episodes: list[dict[str, Any]], episode_id: str
) -> dict[str, Any]:
    for episode in episodes:
        if episode["episode_id"] == episode_id:
            return episode
    raise MemoryWorkspaceError(f"未找到 episode：{episode_id}")


def _episodes_with_status(
    *, root=None, idle_minutes: int = 30
) -> list[dict[str, Any]]:
    from . import capture

    episodes = group_events(capture.all_event_documents(root=root), idle_minutes=idle_minutes)
    for episode in episodes:
        episode["pending_event_ids"] = [
            event["event_id"]
            for event in episode["events"]
            if capture._event_status(event, root=root) == "pending"
        ]
    return episodes


def list_episodes(
    *, status: str = "pending", idle_minutes: int = 30, limit: int = 50, root=None
) -> list[dict[str, Any]]:
    from . import capture

    if status not in {"pending", "resolved", "all"}:
        raise MemoryWorkspaceError("episode status 必须是 pending/resolved/all。")
    if limit < 1 or limit > 500:
        raise MemoryWorkspaceError("limit 必须在 1–500 之间。")
    result = []
    for episode in _episodes_with_status(root=root, idle_minutes=idle_minutes):
        derived = "pending" if episode["pending_event_ids"] else "resolved"
        if status != "all" and derived != status:
            continue
        latest_message = episode["events"][-1]["payload"]["user_message"]
        result.append(
            {
                key: value
                for key, value in episode.items()
                if key != "events"
            }
            | {
                "status": derived,
                "message_preview": capture._safe_preview(latest_message),
            }
        )
        if len(result) >= limit:
            break
    return result


def show_episode(episode_id: str, *, idle_minutes: int = 30, root=None) -> dict[str, Any]:
    return find_episode(
        _episodes_with_status(root=root, idle_minutes=idle_minutes), episode_id
    )


def plan_pending(
    *, idle_minutes: int = 30, limit: int = 20, root=None
) -> list[dict[str, Any]]:
    from .policy import evaluate_episode, load_active_policy

    active = load_active_policy(root=root)
    result = []
    for summary in list_episodes(
        status="pending", idle_minutes=idle_minutes, limit=limit, root=root
    ):
        episode = show_episode(summary["episode_id"], idle_minutes=idle_minutes, root=root)
        result.append(summary | {"policy": evaluate_episode(episode, active)})
    return result


def resolve_episode(
    episode_id: str,
    *,
    decision: str,
    reason: str,
    resolved_by: str = "async_worker",
    trigger_event_id: str | None = None,
    content: str | None = None,
    confidence: float | None = None,
    sensitivity: str = "normal",
    workspace_id: str | None = None,
    profile_key: str | None = None,
    kind: str | None = None,
    policy_version: str | None = None,
    policy_rule: str | None = None,
    idle_minutes: int = 30,
    root=None,
) -> dict[str, Any]:
    from . import capture

    episode = show_episode(episode_id, idle_minutes=idle_minutes, root=root)
    pending_ids = episode["pending_event_ids"]
    if not pending_ids:
        raise MemoryWorkspaceError(f"episode 已经没有 pending event：{episode_id}")
    if decision in capture.CANDIDATE_SCOPES and episode["has_direct_route"]:
        raise MemoryWorkspaceError("Episode 已包含明确写入/召回路径，只能记录反馈，不得重复生成 Candidate。")
    if decision == "ignore" and episode["has_direct_route"]:
        raise MemoryWorkspaceError("feedback_only Episode 请用 session 关闭并记录正反馈，不得标记为 ignore。")
    trigger = trigger_event_id or pending_ids[-1]
    if trigger not in pending_ids:
        raise MemoryWorkspaceError("trigger event 必须属于该 Episode 且仍为 pending。")

    creates_candidate = decision in capture.CANDIDATE_SCOPES

    result = capture.resolve_event(
        trigger,
        decision=decision,
        reason=reason,
        resolved_by=resolved_by,
        content=content if creates_candidate else None,
        confidence=confidence if creates_candidate else None,
        sensitivity=sensitivity,
        workspace_id=(workspace_id or episode["workspace_id"])
        if creates_candidate
        else None,
        profile_key=profile_key if creates_candidate else None,
        kind=kind if creates_candidate else None,
        episode_id=episode_id,
        evidence_event_ids=episode["event_ids"],
        policy_version=policy_version,
        policy_rule=policy_rule,
        trigger_phase=classify_phase(next(item for item in episode["events"] if item["event_id"] == trigger)),
        root=root,
    )
    companion_results = []
    for event_id in pending_ids:
        if event_id == trigger:
            continue
        try:
            companion_results.append(
                capture.resolve_event(
                    event_id,
                    decision="session" if decision in capture.CANDIDATE_SCOPES else decision,
                    reason=f"Episode {episode_id} 已整体处理；该事件作为上下文证据。",
                    resolved_by=resolved_by,
                    episode_id=episode_id,
                    evidence_event_ids=[event_id],
                    policy_version=policy_version,
                    trigger_phase=classify_phase(
                        next(item for item in episode["events"] if item["event_id"] == event_id)
                    ),
                    root=root,
                )
            )
        except MemoryWorkspaceError as exc:
            if "拒绝重复解析" not in str(exc):
                raise
    feedback_paths = []
    feedback_errors = []
    if decision == "session" and episode["has_direct_route"]:
        from .feedback import record_feedback

        feedback_events: dict[str, str] = {}
        for event in episode["events"]:
            route = (event.get("routing") or {}).get("direct_route")
            if route in {"workspace", "remember"}:
                feedback_events["explicit_save_followup"] = event["event_id"]
            elif route == "recall":
                feedback_events["recalled"] = event["event_id"]
        for action, event_id in feedback_events.items():
            try:
                recorded = record_feedback(
                    action=action,
                    actor=resolved_by,
                    event_id=event_id,
                    reason_code=reason,
                    root=root,
                )
                feedback_paths.append(recorded["feedback_path"])
            except MemoryWorkspaceError as exc:
                # Resolution is primary. Report feedback failure separately so
                # callers do not retry an already closed Episode.
                feedback_errors.append(str(exc))
    return {
        "episode_id": episode_id,
        "decision": decision,
        "candidate_id": result["candidate_id"],
        "trigger_event_id": trigger,
        "evidence_event_ids": episode["event_ids"],
        "resolved_event_count": 1 + len(companion_results),
        "deduplicated": result["deduplicated"],
        "feedback_paths": feedback_paths,
        "feedback_errors": feedback_errors,
    }
