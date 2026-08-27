from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "connectors.py"

from memory_workspace import connectors, workspace  # noqa: E402
from memory_workspace.io import MemoryWorkspaceError  # noqa: E402


class ExplicitLarkConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspaces"
        self.environment = os.environ.copy()
        self.environment["MWORK_WORKSPACES_DIR"] = str(self.root)
        self.environment["MWORK_ALLOW_TRANSIENT"] = "1"
        self.patcher = patch.dict(os.environ, self.environment, clear=True)
        self.patcher.start()
        workspace.init_workspace(
            "product-memory", name="Product Memory", root=self.root
        )
        self.workspace = self.root / "product-memory"

    def tearDown(self) -> None:
        self.patcher.stop()
        self.temporary.cleanup()

    def test_missing_connector_is_a_hard_no_read_boundary(self) -> None:
        state = connectors.connector_status("product-memory", root=self.root)
        self.assertFalse(state["configured"])
        self.assertFalse(state["enabled"])
        self.assertFalse(state["lark_cli_required"])
        self.assertFalse((self.workspace / "config" / "connectors").exists())

        plan = connectors.plan_connector_sync(
            "product-memory", root=self.root, now="2026-08-27T02:00:00Z"
        )
        self.assertEqual(plan["status"], "skipped")
        self.assertEqual(plan["reason"], "connector_not_enabled")
        self.assertEqual(plan["commands"], [])
        self.assertFalse(plan["lark_cli_required"])
        self.assertFalse(plan["external_read_performed"])

    def test_explicit_enable_creates_a_lark_only_baseline_plan(self) -> None:
        enabled = connectors.enable_lark_connector(
            "product-memory", root=self.root, now="2026-08-27T02:00:00Z"
        )
        self.assertTrue(enabled["enabled"])
        self.assertFalse(enabled["external_read_performed"])

        state = connectors.connector_status("product-memory", root=self.root)
        self.assertTrue(state["config"]["activation"]["explicit"])
        self.assertEqual(state["config"]["provider"], "lark")
        self.assertEqual(state["config"]["identity_mode"], "user")
        self.assertFalse(state["config"]["review"]["profile_write_allowed"])

        plan = connectors.plan_connector_sync(
            "product-memory", root=self.root, now="2026-08-27T02:00:00Z"
        )
        self.assertEqual(plan["status"], "due")
        self.assertEqual(plan["phase"], "baseline")
        self.assertEqual(plan["trigger"], "first_enable")
        self.assertEqual(plan["identity_mode"], "user")
        self.assertTrue(plan["lark_cli_required"])
        self.assertFalse(plan["external_read_performed"])
        self.assertEqual(len(plan["commands"]), 5)
        self.assertEqual(plan["commands"][0]["argv"][:3], ["lark-cli", "im", "+chat-list"])
        self.assertEqual(
            plan["commands"][1]["foreach"], "top_30_active_chat_ids"
        )
        self.assertIn("--no-reactions", plan["commands"][1]["argv_template"])
        self.assertNotIn("im:message.p2p_msg:readonly", plan["required_scopes"])

    def test_checkpoint_changes_the_trigger_to_bounded_daily_incremental(self) -> None:
        connectors.enable_lark_connector(
            "product-memory", root=self.root, now="2026-08-27T02:00:00Z"
        )
        snapshot = (
            self.workspace
            / "connected"
            / "lark"
            / "manifests"
            / "2026-08-27T020000Z.json"
        )
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text('{"complete": true}\n', encoding="utf-8")
        connectors.record_sync_success(
            "product-memory",
            root=self.root,
            coverage_start="2026-07-28T02:00:00Z",
            coverage_end="2026-08-27T02:00:00Z",
            snapshot_ref="connected/lark/manifests/2026-08-27T020000Z.json",
            trigger="first_enable",
            now="2026-08-27T02:05:00Z",
        )

        early = connectors.plan_connector_sync(
            "product-memory", root=self.root, now="2026-08-27T12:00:00Z"
        )
        self.assertEqual(early["status"], "not_due")
        self.assertEqual(early["commands"], [])

        due = connectors.plan_connector_sync(
            "product-memory", root=self.root, now="2026-08-28T03:00:00Z"
        )
        self.assertEqual(due["status"], "due")
        self.assertEqual(due["phase"], "daily_incremental")
        self.assertEqual(due["coverage"]["start"], "2026-08-27T02:00:00Z")
        self.assertEqual(due["commands"][1]["foreach"], "confirmed_or_new_chat_ids")
        self.assertEqual(due["commands"][-1]["task_id"], "known-document-metadata")

    def test_disable_stops_future_lark_plans_without_deleting_state(self) -> None:
        connectors.enable_lark_connector("product-memory", root=self.root)
        disabled = connectors.disable_connector("product-memory", root=self.root)
        self.assertFalse(disabled["enabled"])
        self.assertTrue(disabled["changed"])

        plan = connectors.plan_connector_sync("product-memory", root=self.root)
        self.assertEqual(plan["status"], "skipped")
        self.assertEqual(plan["commands"], [])
        self.assertTrue(
            (self.workspace / "config" / "connectors" / "lark.json").is_file()
        )

    def test_checkpoint_requires_a_real_workspace_snapshot(self) -> None:
        connectors.enable_lark_connector("product-memory", root=self.root)
        with self.assertRaisesRegex(MemoryWorkspaceError, "不可变快照"):
            connectors.record_sync_success(
                "product-memory",
                root=self.root,
                coverage_start="2026-08-26T02:00:00Z",
                coverage_end="2026-08-27T02:00:00Z",
                snapshot_ref="connected/lark/missing.json",
                trigger="manual",
            )

    def test_workspace_check_validates_optional_connector_files(self) -> None:
        connectors.enable_lark_connector("product-memory", root=self.root)
        result = workspace.check_workspace("product-memory", root=self.root)
        self.assertEqual(result["status"], "OK", result["errors"])
        self.assertEqual(result["counts"]["connectors"], 1)

        path = self.workspace / "config" / "connectors" / "lark.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["activation"]["explicit"] = False
        path.write_text(json.dumps(document), encoding="utf-8")
        broken = workspace.check_workspace("product-memory", root=self.root)
        self.assertEqual(broken["status"], "FAILED")
        self.assertTrue(any("connector" in error for error in broken["errors"]))

    def test_cli_plan_is_skipped_until_enable_lark_is_called(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "plan",
                "product-memory",
                "--at",
                "2026-08-27T02:00:00Z",
                "--json",
            ],
            cwd=ROOT,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)["result"]
        self.assertEqual(payload["status"], "skipped")
        self.assertEqual(payload["commands"], [])

    def test_lark_limits_are_bounded(self) -> None:
        with self.assertRaisesRegex(MemoryWorkspaceError, "lookback_days"):
            connectors.enable_lark_connector(
                "product-memory", root=self.root, lookback_days=365
            )


if __name__ == "__main__":
    unittest.main()
