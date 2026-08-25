"""Offline time-split replay for adaptive capture policies."""

from __future__ import annotations

from typing import Any

from .capture import all_event_documents, parse_datetime
from .episodes import event_occurred_at, group_events
from .policy import build_policy_document, evaluate_episode


def replay(
    split_time: str, *, idle_minutes: int = 30, root=None
) -> dict[str, Any]:
    split = parse_datetime(split_time)
    events = [
        event
        for event in all_event_documents(root=root)
        if parse_datetime(event["expires_at"]) > split
    ]
    train = [event for event in events if parse_datetime(event_occurred_at(event)) < split]
    test = [event for event in events if parse_datetime(event_occurred_at(event)) >= split]
    policy = build_policy_document(train)
    episodes = group_events(test, idle_minutes=idle_minutes)
    plans = [evaluate_episode(episode, policy) for episode in episodes]
    candidate_hints = sum(plan["recommendation"] == "project_candidate_review" for plan in plans)
    feedback_only = sum(plan["recommendation"] == "feedback_only" for plan in plans)
    per_100 = (candidate_hints * 100 / len(test)) if test else 0.0
    return {
        "split_time": split_time,
        "train_event_count": len(train),
        "train_conversation_count": policy["source_summary"]["conversation_count"],
        "test_event_count": len(test),
        "test_episode_count": len(episodes),
        "candidate_review_hint_count": candidate_hints,
        "feedback_only_episode_count": feedback_only,
        "candidate_hints_per_100_queries": round(per_100, 2),
        "policy_preview": {
            "signals": policy["signals"],
            "enabled_rules": [name for name, rule in policy["rules"].items() if rule["enabled"]],
        },
        "episode_plans": [
            {
                "episode_id": episode["episode_id"],
                "event_count": episode["event_count"],
                "current_phase": episode["current_phase"],
                "recommendation": plan["recommendation"],
                "matched_rules": plan["matched_rules"],
            }
            for episode, plan in zip(episodes, plans)
        ],
        "boundary": "This replay measures trigger volume, not precision; user labels are required for precision/recall.",
    }
