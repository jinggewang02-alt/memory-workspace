"""Build and activate explainable, local memory-trigger policies."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from .capture import capture_path, format_datetime, now_utc
from .episodes import explicit_save, group_events
from .io import MemoryWorkspaceError, write_bytes_once
from .schema import load_json_object, validate


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
POLICY_SCHEMA = PACKAGE_ROOT / "schemas" / "memory-policy.schema.json"
ACTIVATION_SCHEMA = PACKAGE_ROOT / "schemas" / "policy-activation.schema.json"
POLICY_ID_RE = re.compile(r"^policy_[A-Za-z0-9_-]+$")

SIGNAL_PATTERNS = {
    "brevity": tuple(
        re.compile(pattern, re.I)
        for pattern in (r"太长|略长|精简|简化|缩减|不要太复杂|核心总结", r"\b(?:shorter|concise|too long)\b")
    ),
    "first_principles": tuple(
        re.compile(pattern, re.I)
        for pattern in (r"没懂|没理解|不理解|想学习|核心能学到什么|怎么理解|解读", r"\b(?:first principles|help me understand|takeaways?)\b")
    ),
    "project_judgment": tuple(
        re.compile(pattern, re.I)
        for pattern in (r"我觉得|我希望|不应该|不要|最好|核心是|最终方案|北极星指标", r"\b(?:I prefer|should not|final decision)\b")
    ),
}


def _root(root: Path | None) -> Path:
    return root or capture_path()


def _policy_path(policy_id: str, root: Path | None = None) -> Path:
    if POLICY_ID_RE.fullmatch(policy_id) is None:
        raise MemoryWorkspaceError(f"policy id 格式无效：{policy_id}")
    return _root(root) / "policies" / f"{policy_id}.json"


def _activation_path(policy_id: str, root: Path | None = None) -> Path:
    if POLICY_ID_RE.fullmatch(policy_id) is None:
        raise MemoryWorkspaceError(f"policy id 格式无效：{policy_id}")
    return _root(root) / "policy-activations" / f"{policy_id}.json"


def _write_once(path: Path, document: dict[str, Any], schema_path: Path, label: str) -> None:
    errors = validate(document, load_json_object(schema_path))
    if errors:
        raise MemoryWorkspaceError(f"{label} 校验失败：" + "; ".join(errors))
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
    from .capture import allow_transient

    write_bytes_once(path, payload, allow_transient=allow_transient())


def _load(path: Path, schema_path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise MemoryWorkspaceError(f"未找到 {label}：{path.stem}")
    document = load_json_object(path)
    errors = validate(document, load_json_object(schema_path))
    if errors:
        raise MemoryWorkspaceError(f"{label} 校验失败：" + "; ".join(errors))
    return document


def _conversation_id(event: dict[str, Any]) -> str:
    return str(event["source"].get("conversation_id") or event["event_id"])


def _signal_summary(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for name, patterns in SIGNAL_PATTERNS.items():
        matches = [
            event
            for event in events
            if any(pattern.search(event["payload"]["user_message"]) for pattern in patterns)
        ]
        summary[name] = {
            "query_count": len(matches),
            "conversation_count": len({_conversation_id(event) for event in matches}),
        }
    explicit = [event for event in events if explicit_save(event["payload"]["user_message"])]
    summary["explicit_save"] = {
        "query_count": len(explicit),
        "conversation_count": len({_conversation_id(event) for event in explicit}),
    }
    return summary


def build_policy_document(
    events: list[dict[str, Any]], *, feedback_summary: dict[str, int] | None = None
) -> dict[str, Any]:
    feedback_counts = feedback_summary or {}
    signals = _signal_summary(events)
    conversations = {_conversation_id(event) for event in events}
    grouped = group_events(events)
    positive_episodes = [episode for episode in grouped if episode["has_direct_write"]]
    learning_evidence = [
        episode
        for episode in positive_episodes
        if episode["phase_counts"].get("clarify", 0) >= 1
        and episode["phase_counts"].get("synthesize", 0) >= 1
        and any(
            (event.get("routing") or {}).get("direct_route") == "workspace"
            for event in episode["events"]
        )
    ]
    brevity_evidence = [
        episode
        for episode in positive_episodes
        if episode["phase_counts"].get("revise", 0) >= 1
        and any(
            (event.get("routing") or {}).get("direct_route") == "remember"
            for event in episode["events"]
        )
    ]
    positive_feedback = feedback_counts.get("approved", 0) + feedback_counts.get(
        "explicit_save_followup", 0
    )
    negative_feedback = feedback_counts.get("rejected", 0) + feedback_counts.get(
        "suppress_similar", 0
    )
    learning_support = 1 + int(negative_feedback > positive_feedback)
    brevity_support = 1 + int(negative_feedback > positive_feedback)
    created = now_utc()
    return {
        "schema_version": 1,
        "policy_id": f"policy_{created.strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:10]}",
        "created_at": format_datetime(created),
        "state": "draft",
        "source_summary": {
            "event_count": len(events),
            "conversation_count": len(conversations),
            "history_event_count": sum(event.get("source_kind") == "history_import" for event in events),
            "live_event_count": sum(event.get("source_kind", "live") == "live" for event in events),
            "positive_episode_count": len(positive_episodes),
            "unlabeled_episode_count": len(grouped) - len(positive_episodes),
        },
        "signals": signals,
        "rules": {
            "learning_synthesis": {
                "enabled": len(learning_evidence) >= learning_support,
                "kind": "learning",
                "scope": "project",
                "minimum_conversations": learning_support,
                "minimum_episode_events": 3,
                "evidence_episode_ids": [
                    episode["episode_id"] for episode in learning_evidence
                ],
                "explanation": "只学习后来明确写入 Workspace 的历史 Episode；相似会话仅在进入总结阶段后建议送审。",
            },
            "repeated_brevity": {
                "enabled": len(brevity_evidence) >= brevity_support,
                "kind": "preference",
                "scope": "profile",
                "minimum_conversations": brevity_support,
                "minimum_episode_events": 1,
                "evidence_episode_ids": [
                    episode["episode_id"] for episode in brevity_evidence
                ],
                "explanation": "只学习后来被明确保存为个人记忆的表达反馈；普通的单次精简要求不是持久偏好。",
            },
            "explicit_save_feedback": {
                "enabled": True,
                "kind": "decision",
                "scope": "project",
                "minimum_conversations": 1,
                "minimum_episode_events": 1,
                "evidence_episode_ids": [
                    episode["episode_id"] for episode in positive_episodes
                ],
                "explanation": "明确保存请求作为正反馈，不重复创建已由直接路径写入的候选。",
            },
        },
        "feedback_summary": dict(sorted(feedback_counts.items())),
    }


def build_policy(events: list[dict[str, Any]], *, root: Path | None = None) -> dict[str, Any]:
    from .feedback import summarize_feedback

    document = build_policy_document(events, feedback_summary=summarize_feedback(root=root))
    path = _policy_path(document["policy_id"], root)
    _write_once(path, document, POLICY_SCHEMA, "memory policy")
    return {"policy": document, "policy_path": str(path), "status": "draft"}


def list_policies(*, root: Path | None = None) -> list[dict[str, Any]]:
    directory = _root(root) / "policies"
    if not directory.exists():
        return []
    active = load_active_policy(root=root)
    active_id = active["policy_id"] if active else None
    result = []
    for path in sorted(directory.glob("policy_*.json")):
        item = _load(path, POLICY_SCHEMA, "memory policy")
        result.append(
            {
                "policy_id": item["policy_id"],
                "created_at": item["created_at"],
                "active": item["policy_id"] == active_id,
                "effective_state": "active"
                if item["policy_id"] == active_id
                else "draft_or_superseded",
                "source_summary": item["source_summary"],
                "enabled_rules": [name for name, rule in item["rules"].items() if rule["enabled"]],
            }
        )
    return result


def show_policy(policy_id: str, *, root: Path | None = None) -> dict[str, Any]:
    policy = _load(_policy_path(policy_id, root), POLICY_SCHEMA, "memory policy")
    activation_path = _activation_path(policy_id, root)
    activation = (
        _load(activation_path, ACTIVATION_SCHEMA, "policy activation")
        if activation_path.is_file()
        else None
    )
    active_policy = load_active_policy(root=root)
    return {
        "policy": policy,
        "activation": activation,
        "active": active_policy is not None
        and active_policy["policy_id"] == policy["policy_id"],
        "effective_state": (
            "active"
            if active_policy is not None
            and active_policy["policy_id"] == policy["policy_id"]
            else "draft_or_superseded"
        ),
    }


def activate_policy(
    policy_id: str, *, actor: str = "owner_via_cli", root: Path | None = None
) -> dict[str, Any]:
    _load(_policy_path(policy_id, root), POLICY_SCHEMA, "memory policy")
    clean_actor = actor.strip()
    if not clean_actor:
        raise MemoryWorkspaceError("actor 不能为空。")
    document = {
        "schema_version": 1,
        "policy_id": policy_id,
        "activated_at": format_datetime(now_utc()),
        "actor": clean_actor,
    }
    path = _activation_path(policy_id, root)
    _write_once(path, document, ACTIVATION_SCHEMA, "policy activation")
    return {"policy_id": policy_id, "status": "active", "activation_path": str(path)}


def load_active_policy(*, root: Path | None = None) -> dict[str, Any] | None:
    directory = _root(root) / "policy-activations"
    if not directory.exists():
        return None
    activations = []
    for path in directory.glob("policy_*.json"):
        activations.append(_load(path, ACTIVATION_SCHEMA, "policy activation"))
    if not activations:
        return None
    latest = max(activations, key=lambda item: (item["activated_at"], item["policy_id"]))
    return _load(_policy_path(latest["policy_id"], root), POLICY_SCHEMA, "memory policy")


def evaluate_episode(
    episode: dict[str, Any], policy: dict[str, Any] | None
) -> dict[str, Any]:
    if episode["has_direct_route"]:
        return {
            "recommendation": "feedback_only",
            "matched_rules": ["explicit_save_feedback"],
            "resolution_decision": "session",
            "feedback_action": "explicit_save_followup"
            if episode["has_direct_write"]
            else "recalled",
            "reason": "该 Episode 已走明确写入或召回路径，只学习反馈，不重复生成候选。",
        }
    if policy is None:
        return {
            "recommendation": "semantic_review",
            "matched_rules": [],
            "resolution_decision": None,
            "feedback_action": None,
            "reason": "尚未激活个人 Policy；由 Worker 保守判断为 session 或候选。",
        }
    learning = policy["rules"]["learning_synthesis"]
    clarification_count = episode["phase_counts"].get("clarify", 0)
    synthesis_count = episode["phase_counts"].get("synthesize", 0)
    if (
        learning["enabled"]
        and episode["event_count"] >= learning["minimum_episode_events"]
        and clarification_count >= 1
        and synthesis_count >= 1
    ):
        return {
            "recommendation": "project_candidate_review",
            "matched_rules": ["learning_synthesis"],
            "resolution_decision": "project",
            "feedback_action": None,
            "reason": "会话已从探索/澄清进入总结阶段，需由语义 Worker 提炼候选内容。",
        }
    brevity = policy["rules"]["repeated_brevity"]
    if brevity["enabled"] and episode["phase_counts"].get("revise", 0) >= 1:
        return {
            "recommendation": "profile_candidate_review",
            "matched_rules": ["repeated_brevity"],
            "resolution_decision": "profile",
            "feedback_action": None,
            "reason": "会话出现了与已明确保存偏好相同的表达修订阶段，需由语义 Worker 判断是否值得送审。",
        }
    return {
        "recommendation": "semantic_review",
        "matched_rules": [],
        "resolution_decision": None,
        "feedback_action": None,
        "reason": "没有命中已激活的阶段策略；不得仅凭关键词持久化。",
    }
