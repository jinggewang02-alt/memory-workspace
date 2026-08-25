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
WORKSPACE_CLI = ROOT / "scripts" / "workspace.py"


class CaptureQueueCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.capture_root = Path(self.temporary.name) / "capture"
        self.workspace_root = Path(self.temporary.name) / "workspaces"
        self.environment = os.environ.copy()
        self.environment["MWORK_CAPTURE_DIR"] = str(self.capture_root)
        self.environment["MWORK_WORKSPACES_DIR"] = str(self.workspace_root)
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

    def enqueue(self, message: str = "以后统一使用跨 Agent 协议") -> dict[str, object]:
        payload = self.result_json(
            "event",
            "enqueue",
            "--message",
            message,
            "--conversation-id",
            "conv-1",
            "--workspace-id",
            "memory-workspace",
            "--source-agent",
            "test-agent",
            "--json",
        )
        self.assertTrue(payload["ok"])
        return payload["result"]

    def init_workspace(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(WORKSPACE_CLI),
                "init",
                "memory-workspace",
                "--name",
                "Memory Workspace",
                "--json",
            ],
            cwd=ROOT,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_doctor_calls_transient_path_an_explicit_test_override(self) -> None:
        result = self.result_json("doctor", "--json")["result"]
        self.assertEqual(result["status"], "WARNING")
        self.assertTrue(result["transient_risk"])
        self.assertIn("测试 override 已启用", result["warnings"][0])
        self.assertNotIn("正式入队将被拒绝", result["warnings"][0])

    def test_event_is_immutable_pending_and_resolved_once(self) -> None:
        event = self.enqueue()
        event_id = str(event["event_id"])
        event_path = Path(str(event["event_path"]))
        self.assertTrue(event_path.is_file())

        pending = self.result_json("event", "list", "--status", "pending", "--json")
        self.assertEqual([item["event_id"] for item in pending["result"]], [event_id])
        shown = self.result_json("event", "show", event_id, "--json")["result"]
        self.assertEqual(shown["event"]["payload"]["user_message"], "以后统一使用跨 Agent 协议")

        resolved = self.result_json(
            "event",
            "resolve",
            event_id,
            "--decision",
            "session",
            "--reason",
            "Only relevant to the active task",
            "--json",
        )
        self.assertIsNone(resolved["result"]["candidate_id"])
        duplicate = self.result_json(
            "event",
            "resolve",
            event_id,
            "--decision",
            "ignore",
            "--reason",
            "duplicate",
            "--json",
            expected=1,
        )
        self.assertFalse(duplicate["ok"])
        self.assertIn("拒绝重复解析", duplicate["error"])
        self.assertEqual(self.result_json("candidate", "list", "--json")["result"], [])

    def test_candidate_requires_review_and_external_write_receipt(self) -> None:
        event_id = str(self.enqueue()["event_id"])
        resolution = self.result_json(
            "event",
            "resolve",
            event_id,
            "--decision",
            "project",
            "--content",
            "Compatibility must not hard-code Agent platform names.",
            "--confidence",
            "0.88",
            "--workspace-id",
            "memory-workspace",
            "--reason",
            "Stable project constraint",
            "--json",
        )["result"]
        candidate_id = str(resolution["candidate_id"])

        proposed = self.result_json("candidate", "list", "--status", "proposed", "--json")
        self.assertEqual([item["candidate_id"] for item in proposed["result"]], [candidate_id])
        missing_target = self.result_json(
            "candidate", "approve", candidate_id, "--json", expected=1
        )
        self.assertIn("target-ref", missing_target["error"])

        missing_workspace = self.result_json(
            "candidate",
            "approve",
            candidate_id,
            "--target-ref",
            "workspace:memory-workspace/wiki/projects/memory-workspace/decisions.md",
            "--json",
            expected=1,
        )
        self.assertIn("未找到 workspace", missing_workspace["error"])
        self.init_workspace()

        approved = self.result_json(
            "candidate",
            "approve",
            candidate_id,
            "--target-ref",
            "workspace:memory-workspace/wiki/projects/memory-workspace/decisions.md",
            "--edited-content",
            "Compatibility should use capability detection instead of hard-coded platform names.",
            "--feedback-reason",
            "owner clarified wording",
            "--json",
        )["result"]
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(len(approved["feedback_paths"]), 2)
        self.assertEqual(
            self.result_json("candidate", "list", "--status", "approved", "--json")[
                "result"
            ][0]["candidate_id"],
            candidate_id,
        )

        applied = self.result_json(
            "candidate",
            "mark-applied",
            candidate_id,
            "--verification",
            "Workspace operation op_test applied; workspace check OK",
            "--json",
        )["result"]
        self.assertEqual(applied["status"], "applied")
        bundle = self.result_json("candidate", "show", candidate_id, "--json")["result"]
        self.assertEqual(bundle["status"], "applied")
        self.assertIn("capability detection", bundle["review"]["approved_content"])
        self.assertIn("workspace check OK", bundle["application"]["verification"])

    def test_secret_is_rejected_before_any_event_file_is_written(self) -> None:
        result = self.result_json(
            "event",
            "enqueue",
            "--message",
            "password: do-not-store-this",
            "--json",
            expected=1,
        )
        self.assertFalse(result["ok"])
        self.assertIn("password", result["error"])
        events = self.capture_root / "events"
        self.assertFalse(events.exists() and any(events.iterdir()))

    def test_sensitive_candidate_is_redacted_in_list_but_available_in_show(self) -> None:
        event_id = str(self.enqueue("我的企业账号 UID 是 123456789012345")["event_id"])
        candidate_id = str(
            self.result_json(
                "event",
                "resolve",
                event_id,
                "--decision",
                "profile",
                "--content",
                "企业账号 UID：123456789012345",
                "--confidence",
                "0.98",
                "--sensitivity",
                "sensitive",
                "--profile-key",
                "企业账号 UID",
                "--reason",
                "Owner-provided exact identifier",
                "--json",
            )["result"]["candidate_id"]
        )
        listing = self.result_json("candidate", "list", "--json")["result"][0]
        self.assertEqual(listing["candidate_id"], candidate_id)
        self.assertNotIn("123456789012345", listing["content_preview"])
        shown = self.result_json("candidate", "show", candidate_id, "--json")["result"]
        self.assertIn("123456789012345", shown["candidate"]["content"])
        mismatch = self.result_json(
            "candidate",
            "approve",
            candidate_id,
            "--target-ref",
            "profile:个人账号 UID",
            "--json",
            expected=1,
        )
        self.assertIn("不一致", mismatch["error"])
        approved = self.result_json(
            "candidate",
            "approve",
            candidate_id,
            "--target-ref",
            "profile:企业账号 UID",
            "--json",
        )
        self.assertEqual(approved["result"]["status"], "approved")

    def test_cleanup_is_dry_run_before_explicit_apply(self) -> None:
        event = self.enqueue()
        path = Path(str(event["event_path"]))
        document = json.loads(path.read_text(encoding="utf-8"))
        document["expires_at"] = "2020-01-01T00:00:00Z"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

        expired = self.result_json(
            "event",
            "resolve",
            str(event["event_id"]),
            "--decision",
            "ignore",
            "--reason",
            "too old",
            "--json",
            expected=1,
        )
        self.assertIn("已过期", expired["error"])

        preview = self.result_json("cleanup", "expired", "--json")["result"]
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["expired_count"], 1)
        self.assertTrue(path.exists())

        removed = self.result_json("cleanup", "expired", "--apply", "--json")["result"]
        self.assertFalse(removed["dry_run"])
        self.assertEqual(removed["removed_count"], 1)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
