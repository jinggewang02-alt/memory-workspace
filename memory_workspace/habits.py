"""Build and render a human-readable draft of recurring Query habits."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import episodes, policy
from .capture import (
    allow_transient,
    all_event_documents,
    format_datetime,
    now_utc,
    parse_datetime,
)
from .io import MemoryWorkspaceError, atomic_write_json, atomic_write_text
from .home import init_home as init_memory_home, personal_learning_path_info
from .schema import load_json_object, validate


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
HABITS_SCHEMA = PACKAGE_ROOT / "schemas" / "query-habits.schema.json"


def _learning_root(root: Path | None = None) -> Path:
    if root is not None:
        return root / "learning"
    return personal_learning_path_info()[0]


def report_json_path(root: Path | None = None) -> Path:
    return _learning_root(root) / "query-habits.json"


def report_markdown_path(root: Path | None = None) -> Path:
    return _learning_root(root) / "query-habits.md"


def _conversation_id(event: dict[str, Any]) -> str:
    return str(event["source"].get("conversation_id") or event["event_id"])


def _confidence(conversations: int, queries: int) -> float:
    return round(
        min(0.92, 0.56 + 0.07 * min(conversations, 4) + 0.02 * min(queries, 5)),
        2,
    )


def _matched_events(
    events: list[dict[str, Any]], patterns: tuple[Any, ...]
) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if any(pattern.search(event["payload"]["user_message"]) for pattern in patterns)
    ]


def _habit(
    *,
    habit_id: str,
    title: str,
    observation: str,
    guidance: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    conversation_count = len({_conversation_id(event) for event in evidence})
    return {
        "habit_id": habit_id,
        "title": title,
        "observation": observation,
        "agent_guidance": guidance,
        "confidence": _confidence(conversation_count, len(evidence)),
        "evidence": {
            "query_count": len(evidence),
            "conversation_count": conversation_count,
            "event_ids": [event["event_id"] for event in evidence],
        },
    }


def build_baseline_document(
    events: list[dict[str, Any]],
    *,
    coverage_start: str,
    coverage_end: str,
    days: int = 30,
) -> dict[str, Any]:
    """Describe repeated self-patterns; never treat them as capture labels."""

    if days < 1 or days > 30:
        raise MemoryWorkspaceError("query habits coverage days 必须在 1–30 之间。")
    habits: list[dict[str, Any]] = []
    definitions = {
        "brevity": (
            "倾向主动压缩表达",
            "用户会在多个对话中要求精简、简化或只保留核心。",
            "回答复杂问题时先给短结论，再按需展开；不要把单次精简要求直接当作永久偏好。",
        ),
        "first_principles": (
            "倾向追问底层原理",
            "用户会跨多个对话继续追问为什么、怎么理解以及核心能学到什么。",
            "解释时补充责任边界、因果关系和端到端流程，并允许先短后深。",
        ),
        "project_judgment": (
            "习惯用明确判断推进方案",
            "用户经常用“我觉得”“我希望”或“不要”表达取舍并继续迭代。",
            "把用户判断和外部事实分开，复述关键约束后再推进可逆的下一步。",
        ),
    }
    for name, patterns in policy.SIGNAL_PATTERNS.items():
        matched = _matched_events(events, patterns)
        if len({_conversation_id(event) for event in matched}) < 2:
            continue
        title, observation, guidance = definitions[name]
        habits.append(
            _habit(
                habit_id=name.replace("_", "-"),
                title=title,
                observation=observation,
                guidance=guidance,
                evidence=matched,
            )
        )

    grouped = episodes.group_events(events)
    refinement_events = [
        event
        for episode in grouped
        if episode["phase_counts"].get("revise", 0) >= 1
        and episode["event_count"] >= 2
        for event in episode["events"]
    ]
    if len({_conversation_id(event) for event in refinement_events}) >= 2:
        habits.append(
            _habit(
                habit_id="iterative-refinement",
                title="通过连续反馈迭代结果",
                observation="用户往往先查看一版结果，再通过连续追问或修改意见逐步收敛。",
                guidance="保留当前方案的上下文与已确认约束，修改时说明这次改变了什么。",
                evidence=refinement_events,
            )
        )

    synthesis_events = [
        event
        for episode in grouped
        if episode["phase_counts"].get("synthesize", 0) >= 1
        and len(episode["phase_counts"]) >= 2
        for event in episode["events"]
    ]
    if len({_conversation_id(event) for event in synthesis_events}) >= 2:
        habits.append(
            _habit(
                habit_id="explore-then-synthesize",
                title="探索之后希望收束成结论",
                observation="用户会先展开讨论，再要求总结核心、结论或下一步。",
                guidance="探索阶段保留分歧；进入收束阶段后给出明确结论、行动和仍待验证的边界。",
                evidence=synthesis_events,
            )
        )

    created = now_utc()
    return {
        "schema_version": 1,
        "report_id": f"habits_{created.strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:10]}",
        "created_at": format_datetime(created),
        "state": "tentative",
        "coverage": {
            "start": coverage_start,
            "end": coverage_end,
            "days": days,
            "query_count": len(events),
            "conversation_count": len({_conversation_id(event) for event in events}),
            "source_adapters": sorted(
                {str(event["source"]["adapter"]) for event in events}
            ),
        },
        "method": "local_signal_baseline",
        "habits": habits,
        "limitations": [
            "独特表示相对于该用户自身反复出现的模式，不表示与其他用户进行比较。",
            "普通高频问法不是记忆触发正样本；触发 Policy 仍只学习后来明确保存的 Episode。",
            "当前报告是本地低成本基线，Agent 可在相同证据边界内做语义复核。",
        ],
    }


def render_markdown(document: dict[str, Any]) -> str:
    coverage = document["coverage"]
    lines = [
        "---",
        f"schema_version: {document['schema_version']}",
        f"report_id: {document['report_id']}",
        f"state: {document['state']}",
        f"coverage_start: {coverage['start']}",
        f"coverage_end: {coverage['end']}",
        f"query_count: {coverage['query_count']}",
        f"conversation_count: {coverage['conversation_count']}",
        f"generated_at: {document['created_at']}",
        "---",
        "",
        "# Query Habits",
        "",
        "这些是从最近对话中观察到的重复模式。未经确认的内容只是推断，不是永久偏好。",
        "",
    ]
    if not document["habits"]:
        lines.extend(
            [
                "目前没有发现跨多个对话重复出现、足以形成草稿的 Query 习惯。",
                "",
            ]
        )
    for habit in document["habits"]:
        evidence = habit["evidence"]
        state_label = "已确认" if document["state"] == "confirmed" else "待确认"
        lines.extend(
            [
                f"## {habit['title']}",
                "",
                f"- 观察：{habit['observation']}",
                f"- 证据：{evidence['conversation_count']} 个对话 / {evidence['query_count']} 条 Query",
                f"- 置信度：{round(habit['confidence'] * 100)}%",
                f"- Agent 建议：{habit['agent_guidance']}",
                f"- 状态：{state_label}",
                "",
            ]
        )
    lines.extend(["## 边界", ""])
    lines.extend(f"- {item}" for item in document["limitations"])
    lines.append("")
    return "\n".join(lines)


def write_report(
    document: dict[str, Any], *, root: Path | None = None
) -> dict[str, Any]:
    errors = validate(document, load_json_object(HABITS_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("query habits 校验失败：" + "; ".join(errors))
    if root is None:
        learning_root = personal_learning_path_info()[0]
        init_memory_home(root=learning_root.parents[1])
    atomic_write_json(
        report_json_path(root),
        document,
        allow_transient=allow_transient(),
    )
    atomic_write_text(
        report_markdown_path(root),
        render_markdown(document),
        allow_transient=allow_transient(),
    )
    return {
        "report": document,
        "json_path": str(report_json_path(root)),
        "markdown_path": str(report_markdown_path(root)),
    }


def load_report(*, root: Path | None = None) -> dict[str, Any] | None:
    path = report_json_path(root)
    if not path.is_file():
        return None
    try:
        document = load_json_object(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MemoryWorkspaceError(f"无法读取 query habits：{exc}") from exc
    errors = validate(document, load_json_object(HABITS_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("query habits 校验失败：" + "; ".join(errors))
    return document


def load_agent_semantic_report(
    path: Path,
    *,
    root: Path,
    expected_coverage: dict[str, Any],
) -> dict[str, Any]:
    try:
        document = load_json_object(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MemoryWorkspaceError(f"无法读取 Agent Query habits 报告：{exc}") from exc
    errors = validate(document, load_json_object(HABITS_SCHEMA))
    if errors:
        raise MemoryWorkspaceError("Agent Query habits 校验失败：" + "; ".join(errors))
    if document["method"] != "agent_semantic_review" or document["state"] != "tentative":
        raise MemoryWorkspaceError("Agent 语义报告必须是 agent_semantic_review/tentative。")
    if document["coverage"] != expected_coverage:
        raise MemoryWorkspaceError("Agent 语义报告不得扩大或改变首次学习的证据范围。")

    coverage_start = parse_datetime(expected_coverage["start"])
    coverage_end = parse_datetime(expected_coverage["end"])
    coverage_adapters = set(expected_coverage["source_adapters"])
    available = {
        event["event_id"]: event
        for event in all_event_documents(root=root)
        if event.get("source_kind") == "history_import"
        and event["source"].get("adapter") in coverage_adapters
        and coverage_start
        <= parse_datetime(event.get("occurred_at", event["created_at"]))
        <= coverage_end
    }
    seen_ids: set[str] = set()
    for habit in document["habits"]:
        habit_id = habit["habit_id"]
        if habit_id in seen_ids:
            raise MemoryWorkspaceError(f"habit id 重复：{habit_id}")
        seen_ids.add(habit_id)
        confidence = habit["confidence"]
        if confidence < 0 or confidence > 1:
            raise MemoryWorkspaceError(f"habit confidence 必须在 0–1：{habit_id}")
        evidence_ids = habit["evidence"]["event_ids"]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise MemoryWorkspaceError(f"habit evidence event 重复：{habit_id}")
        try:
            evidence_events = [available[event_id] for event_id in evidence_ids]
        except KeyError as exc:
            raise MemoryWorkspaceError(
                f"habit evidence 不属于本地历史事件：{exc.args[0]}"
            ) from exc
        conversations = {_conversation_id(event) for event in evidence_events}
        if len(conversations) < 2:
            raise MemoryWorkspaceError(
                f"habit 必须由至少两个独立对话支持：{habit_id}"
            )
        if habit["evidence"]["query_count"] != len(evidence_events) or habit[
            "evidence"
        ]["conversation_count"] != len(conversations):
            raise MemoryWorkspaceError(f"habit evidence 计数不一致：{habit_id}")
    return document


def confirm_report(*, root: Path | None = None) -> dict[str, Any]:
    document = load_report(root=root)
    if document is None:
        raise MemoryWorkspaceError("还没有可确认的 query habits 报告。")
    confirmed = copy.deepcopy(document)
    confirmed["state"] = "confirmed"
    return write_report(confirmed, root=root)
