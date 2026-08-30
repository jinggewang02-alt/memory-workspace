from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from memory_workspace import onboarding
from memory_workspace.bootstrap import build_report
from memory_workspace.quickstart import exit_code, prepare


ROOT = Path(__file__).resolve().parents[1]


def path_probe(path, source, environ, parent_target=False):
    return {
        "path": str(path),
        "source": source,
        "exists": path.exists(),
        "nearest_existing_parent": str(path.parent),
        "filesystem_writable": True,
        "transient_risk": False,
        "transient_reason": None,
    }


class QuickstartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.memory_home = Path(self.temporary.name) / "memory-home"
        self.environment = os.environ.copy()
        self.environment["MEMORY_HOME"] = str(self.memory_home)
        self.environment["MWORK_ALLOW_TRANSIENT"] = "1"
        self.environment["MEMORY_HOME_ALLOW_TRANSIENT"] = "1"
        for key in (
            "PMEM_FILE",
            "PMEM_DIR",
            "MWORK_WORKSPACES_DIR",
            "MWORK_CAPTURE_DIR",
            "MWORK_HISTORY_FILE",
            "MWORK_HISTORY_ADAPTER",
        ):
            self.environment.pop(key, None)
        self.patcher = patch.dict(os.environ, self.environment, clear=True)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.temporary.cleanup()

    def capability_report(self, *, python_version=(3, 12, 1)):
        return build_report(
            ROOT,
            environ=self.environment,
            python_version=python_version,
            path_probe=path_probe,
        )

    def test_prepare_initializes_one_home_and_is_idempotent(self) -> None:
        report = self.capability_report()
        first = prepare(environ=self.environment, capability_report=report)
        second = prepare(environ=self.environment, capability_report=report)

        self.assertTrue(first["ready"])
        self.assertTrue(first["initialized"]["created"])
        self.assertFalse(second["initialized"]["created"])
        self.assertEqual(first["paths"]["memory_home"], str(self.memory_home))
        self.assertEqual(
            first["paths"]["capture"], str(self.memory_home / "system" / "capture")
        )
        self.assertEqual(
            first["paths"]["learning"], str(self.memory_home / "personal" / "learning")
        )
        self.assertEqual(first["history_learning"]["status"], "needs_history_source")
        self.assertTrue(first["history_learning"]["optional"])
        self.assertEqual(first["external_connectors"]["status"], "optional")
        self.assertEqual(first["external_connectors"]["enabled"], [])
        self.assertEqual(first["schema_version"], 2)
        self.assertEqual(first["primary_action"]["kind"], "continue")
        self.assertEqual(first["primary_action"]["label"], "直接开始")
        self.assertEqual(
            [item["id"] for item in first["menu"]],
            ["continue", "import", "review"],
        )
        self.assertTrue(first["menu"][0]["recommended"])
        self.assertEqual(len(first["next_actions"]), 3)
        self.assertEqual(
            first["memory_behavior"]["explicit_save"], "direct_with_readback"
        )
        self.assertEqual(
            first["memory_behavior"]["processing"], "nightly_or_next_startup"
        )
        self.assertEqual(
            first["memory_behavior"]["scheduling_status"],
            "host_integration_required",
        )
        self.assertEqual(
            first["memory_behavior"]["canonical_write_policy"],
            "candidate_only_without_explicit_save",
        )
        self.assertEqual(first["notices"], [])
        self.assertEqual(first["ui"]["status"], "not_started")
        self.assertIsNone(first["ui"]["url"])
        self.assertNotIn("default_url", first["ui"])
        self.assertEqual(
            first["ui"]["launch_command"][0], str(Path(sys.executable).resolve())
        )
        self.assertTrue(first["ui"]["requires_same_device_browser"])
        self.assertTrue(first["ui"]["requires_long_lived_process"])
        self.assertEqual(exit_code(first), 0)

    def test_history_and_capture_keep_separate_roots(self) -> None:
        result = prepare(
            environ=self.environment,
            capability_report=self.capability_report(),
        )
        status = onboarding.get_status(
            root=Path(result["paths"]["capture"]),
            habits_root=Path(result["paths"]["learning"]).parent,
            policy_root=Path(result["paths"]["learning"]),
        )
        self.assertEqual(status["status"], "needs_history_source")
        self.assertFalse(
            (self.memory_home / "system" / "capture" / "learning").exists()
        )

    def test_authorized_history_handoff_runs_first_learning_once(self) -> None:
        history = Path(self.temporary.name) / "visible-history.jsonl"
        history.write_text(
            json.dumps(
                {
                    "role": "user",
                    "conversation_id": "quickstart-history",
                    "message_id": "quickstart-history-1",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "content": "这部分我没理解，请用一个具体例子解释。",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        self.environment["MWORK_HISTORY_FILE"] = str(history)
        self.environment["MWORK_HISTORY_ADAPTER"] = "synthetic-agent-history"

        result = prepare(
            environ=self.environment,
            capability_report=self.capability_report(),
        )

        self.assertEqual(result["history_learning"]["status"], "awaiting_review")
        self.assertEqual(result["notices"][0]["kind"], "history_review_ready")
        self.assertTrue(
            (self.memory_home / "personal" / "learning" / "query-habits.md").is_file()
        )
        self.assertTrue(
            (
                self.memory_home
                / "system"
                / "capture"
                / "onboarding"
                / "state.json"
            ).is_file()
        )
        repeated = prepare(
            environ=self.environment,
            capability_report=self.capability_report(),
        )
        self.assertEqual(repeated["history_learning"]["status"], "awaiting_review")
        self.assertFalse(repeated["initialized"]["created"])

    def test_runtime_blocker_does_not_create_memory_home(self) -> None:
        blocked = prepare(
            environ=self.environment,
            capability_report=self.capability_report(python_version=(3, 9, 18)),
        )
        self.assertFalse(blocked["ready"])
        self.assertEqual(blocked["schema_version"], 2)
        self.assertEqual(blocked["status"], "NEEDS_RUNTIME")
        self.assertFalse(self.memory_home.exists())
        self.assertEqual(exit_code(blocked), 1)

    def test_invalid_optional_history_does_not_block_core_setup(self) -> None:
        history = Path(self.temporary.name) / "invalid-history.txt"
        history.write_text("not jsonl\n", encoding="utf-8")
        self.environment["MWORK_HISTORY_FILE"] = str(history)
        self.environment["MWORK_HISTORY_ADAPTER"] = "synthetic-agent-history"

        result = prepare(
            environ=self.environment,
            capability_report=self.capability_report(),
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["history_learning"]["status"], "needs_attention")
        self.assertIn(".jsonl", result["history_learning"]["error"])
        self.assertEqual(result["notices"][0]["kind"], "history_source_attention")
        self.assertTrue((self.memory_home / "memory-home.json").is_file())
