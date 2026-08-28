from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "capture.py"


class AdaptiveCaptureCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.capture_root = self.root / "capture"
        self.history_path = self.root / "history.jsonl"
        self.environment = os.environ.copy()
        self.environment["MEMORY_HOME"] = str(self.root / "memory-home")
        self.environment["MWORK_CAPTURE_DIR"] = str(self.capture_root)
        self.environment["MWORK_WORKSPACES_DIR"] = str(self.root / "workspaces")
        self.environment["MWORK_ALLOW_TRANSIENT"] = "1"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=ROOT,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def result_json(self, *args: str, expected: int = 0) -> dict[str, object]:
        return json.loads(self.run_cli(*args, expected=expected).stdout)

    def write_history(self, rows: list[dict[str, object]]) -> None:
        self.history_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    @staticmethod
    def learning_episode(
        conversation_id: str, day: int, *, direct_save: bool = False
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = [
            {
                "role": "user",
                "conversation_id": conversation_id,
                "message_id": f"{conversation_id}-1",
                "occurred_at": f"2026-08-{day:02d}T01:00:00Z",
                "workspace_id": "memory-workspace",
                "content": "请帮我解读这份产品方案",
            },
            {
                "role": "user",
                "conversation_id": conversation_id,
                "message_id": f"{conversation_id}-2",
                "occurred_at": f"2026-08-{day:02d}T01:05:00Z",
                "workspace_id": "memory-workspace",
                "content": "这部分我没理解，为什么要区分协议和界面？",
            },
            {
                "role": "user",
                "conversation_id": conversation_id,
                "message_id": f"{conversation_id}-3",
                "occurred_at": f"2026-08-{day:02d}T01:10:00Z",
                "workspace_id": "memory-workspace",
                "content": "总结一下核心能学到什么",
            },
        ]
        if direct_save:
            rows.append(
                {
                    "role": "user",
                    "conversation_id": conversation_id,
                    "message_id": f"{conversation_id}-4",
                    "occurred_at": f"2026-08-{day:02d}T01:15:00Z",
                    "workspace_id": "memory-workspace",
                    "content": "把这个结论记入我的 wiki",
                }
            )
        return rows

    def import_rows(self, rows: list[dict[str, object]]) -> dict[str, object]:
        self.write_history(rows)
        return self.result_json(
            "history",
            "import",
            "--file",
            str(self.history_path),
            "--adapter",
            "synthetic-test",
            "--json",
        )["result"]

    def test_history_import_is_bounded_deduplicated_and_secret_safe(self) -> None:
        rows = self.learning_episode("conv-one", 1)
        rows.extend(
            [
                {
                    "role": "assistant",
                    "conversation_id": "conv-one",
                    "occurred_at": "2026-08-01T01:06:00Z",
                    "content": "assistant text must not be imported",
                },
                {
                    "role": "user",
                    "conversation_id": "conv-secret",
                    "occurred_at": "2026-08-01T02:00:00Z",
                    "content": "password: synthetic-do-not-store",
                },
            ]
        )
        imported = self.import_rows(rows)
        self.assertEqual(imported["input_rows"], 5)
        self.assertEqual(imported["imported_count"], 3)
        self.assertEqual(imported["ignored_non_user_count"], 1)
        self.assertEqual(imported["rejected_secret_count"], 1)

        repeated = self.result_json(
            "history",
            "import",
            "--file",
            str(self.history_path),
            "--adapter",
            "synthetic-test",
            "--json",
        )["result"]
        self.assertEqual(repeated["imported_count"], 0)
        self.assertEqual(repeated["duplicate_count"], 3)
        event_files = list((self.capture_root / "events").glob("evt_*.json"))
        self.assertEqual(len(event_files), 3)
        self.assertNotIn(
            "synthetic-do-not-store",
            "".join(path.read_text(encoding="utf-8") for path in event_files),
        )

    def test_policy_worker_episode_candidate_and_feedback_loop(self) -> None:
        rows = (
            self.learning_episode("conv-train-a", 1, direct_save=True)
            + self.learning_episode("conv-train-b", 8, direct_save=True)
            + self.learning_episode("conv-test", 20)
        )
        self.import_rows(rows)

        draft = self.result_json("policy", "build", "--json")["result"]["policy"]
        self.assertTrue(draft["rules"]["learning_synthesis"]["enabled"])
        self.assertEqual(draft["source_summary"]["positive_episode_count"], 2)
        self.assertEqual(
            len(draft["rules"]["learning_synthesis"]["evidence_episode_ids"]), 2
        )
        policy_id = str(draft["policy_id"])
        self.result_json("policy", "activate", policy_id, "--json")

        plans = self.result_json("worker", "plan", "--json")["result"]
        plan = next(item for item in plans if item["conversation_id"] == "conv-test")
        self.assertEqual(
            plan["policy"]["recommendation"], "project_candidate_review"
        )

        resolved = self.result_json(
            "episode",
            "resolve",
            str(plan["episode_id"]),
            "--decision",
            "project",
            "--kind",
            "learning",
            "--content",
            "协议负责稳定数据边界，界面负责呈现和交互，两者应解耦。",
            "--confidence",
            "0.86",
            "--policy-version",
            policy_id,
            "--policy-rule",
            "learning_synthesis",
            "--reason",
            "Episode 从问题澄清进入总结阶段",
            "--json",
        )["result"]
        self.assertEqual(resolved["resolved_event_count"], 3)
        candidate_id = str(resolved["candidate_id"])
        candidate = self.result_json(
            "candidate", "show", candidate_id, "--json"
        )["result"]["candidate"]
        self.assertEqual(candidate["episode_id"], plan["episode_id"])
        self.assertEqual(len(candidate["evidence_event_ids"]), 3)
        self.assertEqual(candidate["policy_version"], policy_id)
        self.assertEqual(candidate["policy_rule"], "learning_synthesis")
        self.assertFalse((self.root / "workspaces").exists())

        rejected = self.result_json(
            "candidate",
            "reject",
            candidate_id,
            "--feedback-reason",
            "not durable",
            "--suppress-similar",
            "--json",
        )["result"]
        self.assertEqual(len(rejected["feedback_paths"]), 2)
        actions = {
            item["action"]
            for item in self.result_json("feedback", "list", "--json")["result"]
        }
        self.assertEqual(actions, {"rejected", "suppress_similar"})
        next_policy = self.result_json("policy", "build", "--json")["result"][
            "policy"
        ]
        self.assertEqual(next_policy["feedback_summary"]["rejected"], 1)
        self.assertEqual(next_policy["feedback_summary"]["suppress_similar"], 1)
        self.assertEqual(
            next_policy["rules"]["learning_synthesis"]["minimum_conversations"],
            2,
        )

    def test_frequent_learning_queries_without_explicit_save_do_not_train_rule(self) -> None:
        self.import_rows(
            self.learning_episode("conv-unlabeled-a", 2)
            + self.learning_episode("conv-unlabeled-b", 3)
        )
        policy = self.result_json("policy", "build", "--json")["result"]["policy"]
        self.assertGreaterEqual(
            policy["signals"]["first_principles"]["conversation_count"], 2
        )
        self.assertEqual(policy["source_summary"]["positive_episode_count"], 0)
        self.assertFalse(policy["rules"]["learning_synthesis"]["enabled"])
        self.assertEqual(
            policy["rules"]["learning_synthesis"]["evidence_episode_ids"], []
        )

    def test_direct_save_is_feedback_only_and_never_becomes_a_candidate(self) -> None:
        self.import_rows(self.learning_episode("conv-direct", 21, direct_save=True))
        policy_id = str(
            self.result_json("policy", "build", "--json")["result"]["policy"][
                "policy_id"
            ]
        )
        self.result_json("policy", "activate", policy_id, "--json")
        plan = self.result_json("worker", "plan", "--json")["result"][0]
        self.assertEqual(plan["policy"]["recommendation"], "feedback_only")
        self.assertEqual(plan["policy"]["resolution_decision"], "session")
        result = self.result_json(
            "episode",
            "resolve",
            str(plan["episode_id"]),
            "--decision",
            "project",
            "--kind",
            "learning",
            "--content",
            "must not be proposed",
            "--confidence",
            "0.9",
            "--reason",
            "incorrect duplicate attempt",
            "--json",
            expected=1,
        )
        self.assertIn("不得重复生成 Candidate", result["error"])
        self.assertEqual(
            self.result_json("candidate", "list", "--json")["result"], []
        )
        closed = self.result_json(
            "episode",
            "resolve",
            str(plan["episode_id"]),
            "--decision",
            "session",
            "--reason",
            "explicit save already handled canonical write",
            "--json",
        )["result"]
        self.assertEqual(closed["resolved_event_count"], 4)
        self.assertEqual(len(closed["feedback_paths"]), 1)
        self.assertEqual(closed["feedback_errors"], [])
        self.assertEqual(
            self.result_json("worker", "plan", "--json")["result"], []
        )
        feedback = self.result_json("feedback", "list", "--json")["result"]
        self.assertEqual(feedback[0]["action"], "explicit_save_followup")

    def test_time_split_replay_reports_volume_not_unlabeled_precision(self) -> None:
        rows = (
            self.learning_episode("conv-train-a", 1, direct_save=True)
            + self.learning_episode("conv-train-b", 8, direct_save=True)
            + self.learning_episode("conv-test", 20)
        )
        self.import_rows(rows)
        replay = self.result_json(
            "eval", "replay", "--split-time", "2026-08-15T00:00:00Z", "--json"
        )["result"]
        self.assertEqual(replay["train_event_count"], 8)
        self.assertEqual(replay["test_event_count"], 3)
        self.assertEqual(replay["candidate_review_hint_count"], 1)
        self.assertIn("not precision", replay["boundary"])

    def test_feedback_must_reference_an_existing_event_or_candidate(self) -> None:
        result = self.result_json(
            "feedback",
            "record",
            "--action",
            "forgotten_complaint",
            "--event-id",
            "evt_missing",
            "--json",
            expected=1,
        )
        self.assertIn("未找到 capture event", result["error"])


if __name__ == "__main__":
    unittest.main()
