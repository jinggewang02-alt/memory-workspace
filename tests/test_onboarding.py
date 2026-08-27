from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from memory_workspace import capture, habits, onboarding, policy
from memory_workspace.history_sources import discover_history_source
from memory_workspace.io import MemoryWorkspaceError


class FirstLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.capture_root = self.base / "capture"
        self.history_path = self.base / "visible-history.jsonl"
        self.previous_allow = os.environ.get("MWORK_ALLOW_TRANSIENT")
        os.environ["MWORK_ALLOW_TRANSIENT"] = "1"

    def tearDown(self) -> None:
        if self.previous_allow is None:
            os.environ.pop("MWORK_ALLOW_TRANSIENT", None)
        else:
            os.environ["MWORK_ALLOW_TRANSIENT"] = self.previous_allow
        self.temporary.cleanup()

    def write_rows(self) -> None:
        rows = [
            {
                "role": "user",
                "conversation_id": "too-old",
                "occurred_at": "2026-07-01T08:00:00Z",
                "content": "这部分我没理解",
            },
            {
                "role": "assistant",
                "conversation_id": "conversation-a",
                "occurred_at": "2026-08-10T08:00:00Z",
                "content": "assistant content",
            },
            {
                "role": "user",
                "conversation_id": "conversation-a",
                "message_id": "a-1",
                "occurred_at": "2026-08-10T08:01:00Z",
                "content": "这部分我没理解，为什么这样设计？",
            },
            {
                "role": "user",
                "conversation_id": "conversation-a",
                "message_id": "a-2",
                "occurred_at": "2026-08-10T08:02:00Z",
                "content": "总结一下核心能学到什么",
            },
            {
                "role": "user",
                "conversation_id": "conversation-b",
                "message_id": "b-1",
                "occurred_at": "2026-08-20T08:01:00Z",
                "content": "我没理解，怎么理解这个调用关系？",
            },
            {
                "role": "user",
                "conversation_id": "conversation-b",
                "message_id": "b-2",
                "occurred_at": "2026-08-20T08:02:00Z",
                "content": "所以总结一下核心结论",
            },
        ]
        self.history_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    def test_source_discovery_does_not_search_without_handoff(self) -> None:
        missing = discover_history_source(environ={})
        self.assertEqual(missing["status"], "needs_history_source")
        self.assertFalse(missing["available"])

        self.write_rows()
        configured = discover_history_source(
            environ={
                "MWORK_HISTORY_FILE": str(self.history_path),
                "MWORK_HISTORY_ADAPTER": "synthetic-host-adapter",
            }
        )
        self.assertTrue(configured["available"])
        self.assertTrue(configured["auto_configured"])
        self.assertEqual(configured["adapter"], "synthetic-host-adapter")

    def test_first_learning_writes_bounded_markdown_and_waits_for_review(self) -> None:
        self.write_rows()
        result = onboarding.run_first_learning(
            root=self.capture_root,
            history_file=self.history_path,
            adapter="synthetic-host-adapter",
            current_time=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "awaiting_review")
        self.assertEqual(result["import"]["outside_window_count"], 1)
        self.assertEqual(result["import"]["ignored_non_user_count"], 1)
        self.assertEqual(result["habits"]["coverage"]["query_count"], 4)
        habit_ids = {item["habit_id"] for item in result["habits"]["habits"]}
        self.assertIn("first-principles", habit_ids)
        self.assertIn("explore-then-synthesize", habit_ids)
        markdown_path = Path(result["run"]["habits_markdown_path"])
        markdown = markdown_path.read_text(encoding="utf-8")
        self.assertIn("# Query Habits", markdown)
        self.assertIn("state: tentative", markdown)
        self.assertNotIn("怎么理解这个调用关系", markdown)
        self.assertIsNone(policy.load_active_policy(root=self.capture_root))

        repeated = onboarding.run_first_learning(
            root=self.capture_root,
            history_file=self.history_path,
            adapter="synthetic-host-adapter",
        )
        self.assertTrue(repeated["deduplicated"])
        self.assertEqual(repeated["run"]["run_id"], result["run"]["run_id"])

    def test_default_onboarding_splits_personal_learning_from_system_state(self) -> None:
        self.write_rows()
        memory_home = self.base / "memory-home"
        environment = os.environ.copy()
        environment["MEMORY_HOME"] = str(memory_home)
        environment["MWORK_ALLOW_TRANSIENT"] = "1"
        environment["MEMORY_HOME_ALLOW_TRANSIENT"] = "1"
        for key in ("MWORK_CAPTURE_DIR", "PMEM_FILE", "PMEM_DIR"):
            environment.pop(key, None)

        with patch.dict(os.environ, environment, clear=True):
            result = onboarding.run_first_learning(
                history_file=self.history_path,
                adapter="synthetic-host-adapter",
                current_time=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(
            Path(result["run"]["habits_markdown_path"]),
            memory_home / "personal" / "learning" / "query-habits.md",
        )
        self.assertTrue(
            (memory_home / "system" / "capture" / "onboarding" / "state.json").is_file()
        )
        self.assertTrue(
            (
                memory_home
                / "personal"
                / "learning"
                / "policies"
                / f"{result['run']['policy_id']}.json"
            ).is_file()
        )
        self.assertFalse((memory_home / "system" / "capture" / "learning").exists())

    def test_confirmation_activates_policy_and_marks_markdown_confirmed(self) -> None:
        self.write_rows()
        started = onboarding.run_first_learning(
            root=self.capture_root,
            history_file=self.history_path,
            adapter="synthetic-host-adapter",
            current_time=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        )
        confirmed = onboarding.confirm_first_learning(
            root=self.capture_root, actor="owner_via_test"
        )
        self.assertEqual(confirmed["status"], "completed")
        self.assertEqual(confirmed["habits"]["state"], "confirmed")
        self.assertTrue(confirmed["policy_active"])
        self.assertEqual(
            policy.load_active_policy(root=self.capture_root)["policy_id"],
            started["run"]["policy_id"],
        )
        self.assertIn(
            "state: confirmed",
            habits.report_markdown_path(self.capture_root).read_text(encoding="utf-8"),
        )

    def test_agent_can_refine_tentative_habits_without_expanding_coverage(self) -> None:
        self.write_rows()
        started = onboarding.run_first_learning(
            root=self.capture_root,
            history_file=self.history_path,
            adapter="synthetic-host-adapter",
            current_time=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        )
        semantic = dict(started["habits"])
        semantic["report_id"] = "habits_20260825T120000Z_semantic"
        semantic["method"] = "agent_semantic_review"
        semantic["habits"] = [
            dict(
                next(
                    item
                    for item in started["habits"]["habits"]
                    if item["habit_id"] == "first-principles"
                ),
                title="先通过追问建立底层模型",
                observation="用户会在不同主题中追问机制和调用方向。",
            )
        ]
        semantic_path = self.base / "semantic-habits.json"
        semantic_path.write_text(
            json.dumps(semantic, ensure_ascii=False), encoding="utf-8"
        )
        refined = onboarding.refine_habits_from_agent(
            semantic_path, root=self.capture_root
        )
        self.assertEqual(refined["habits"]["method"], "agent_semantic_review")
        self.assertEqual(refined["run"]["habits_report_id"], semantic["report_id"])
        self.assertIn(
            "先通过追问建立底层模型",
            habits.report_markdown_path(self.capture_root).read_text(encoding="utf-8"),
        )

    def test_agent_refinement_cannot_cite_another_adapter(self) -> None:
        self.write_rows()
        started = onboarding.run_first_learning(
            root=self.capture_root,
            history_file=self.history_path,
            adapter="synthetic-host-adapter",
            current_time=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        )
        outside = capture.enqueue_event(
            "这部分我没理解",
            conversation_id="other-account-conversation",
            source_adapter="other-adapter",
            source_kind="history_import",
            occurred_at="2026-08-22T08:00:00Z",
            retention_days=30,
            root=self.capture_root,
        )
        semantic = dict(started["habits"])
        semantic["report_id"] = "habits_20260825T120000Z_outside"
        semantic["method"] = "agent_semantic_review"
        source_habit = next(
            item
            for item in started["habits"]["habits"]
            if item["habit_id"] == "first-principles"
        )
        evidence = dict(source_habit["evidence"])
        evidence["event_ids"] = evidence["event_ids"] + [outside["event_id"]]
        evidence["query_count"] += 1
        evidence["conversation_count"] += 1
        semantic["habits"] = [dict(source_habit, evidence=evidence)]
        semantic_path = self.base / "outside-semantic.json"
        semantic_path.write_text(
            json.dumps(semantic, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaises(MemoryWorkspaceError):
            onboarding.refine_habits_from_agent(
                semantic_path, root=self.capture_root
            )


if __name__ == "__main__":
    unittest.main()
