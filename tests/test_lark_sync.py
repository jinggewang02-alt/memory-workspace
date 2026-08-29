from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from memory_workspace import connectors, lark_sync, workspace
from memory_workspace.io import MemoryWorkspaceError


class SyntheticLarkProjectSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspaces"
        self.environment = os.environ.copy()
        self.environment["MWORK_ALLOW_TRANSIENT"] = "1"
        self.patcher = patch.dict(os.environ, self.environment, clear=True)
        self.patcher.start()
        workspace.init_workspace("launch", name="Launch", root=self.root)
        self.workspace = self.root / "launch"
        connectors.enable_lark_connector(
            "launch", root=self.root, now="2026-08-01T02:00:00Z"
        )

    def tearDown(self) -> None:
        self.patcher.stop()
        self.temporary.cleanup()

    def map_sources(self) -> None:
        connectors.map_lark_source(
            "launch",
            kind="chat",
            external_id="oc_launch",
            label="Launch 项目群",
            root=self.root,
            now="2026-08-01T02:01:00Z",
        )
        connectors.map_lark_source(
            "launch",
            kind="document",
            external_id="doc_launch_prd",
            locator="https://example.feishu.cn/docx/doc_launch_prd",
            label="Launch PRD",
            root=self.root,
            now="2026-08-01T02:02:00Z",
        )

    def synthetic_runner(self, argv: list[str]) -> SimpleNamespace:
        if "+chat-messages-list" in argv:
            data = {
                "messages": [
                    {
                        "message_id": "m1",
                        "msg_type": "text",
                        "create_time": "2026-08-28T09:00:00+00:00",
                        "sender": {"id": "ou_alice", "type": "user", "name": "Alice"},
                        "content": {"text": "决策：首版只覆盖一个项目的核心群和 PRD。"},
                    },
                    {
                        "message_id": "m2",
                        "msg_type": "text",
                        "create_time": "2026-08-28T10:00:00+00:00",
                        "sender": {"id": "ou_bob", "type": "user", "name": "Bob"},
                        "content": {"text": "下一步：补齐项目详情页并验证来源回链。"},
                    },
                ]
            }
        else:
            data = {
                "document": {
                    "document_id": "doc_launch_prd",
                    "title": "Launch PRD",
                    "content": "# Launch PRD\n\n首版目标是把项目证据变成可回溯的项目脉络。",
                    "updated_at": "2026-08-28T08:00:00+00:00",
                }
            }
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"ok": True, "identity": "user", "data": data}),
            stderr="",
        )

    def test_mapping_is_explicit_deduplicated_and_performs_no_read(self) -> None:
        first = connectors.map_lark_source(
            "launch", kind="chat", external_id="oc_launch", label="Launch 群",
            root=self.root, now="2026-08-01T02:01:00Z",
        )
        second = connectors.map_lark_source(
            "launch", kind="chat", external_id="oc_launch", label="Launch 项目群",
            root=self.root, now="2026-08-01T03:01:00Z",
        )
        self.assertFalse(first["external_read_performed"])
        self.assertFalse(second["changed"])
        sources = connectors.list_connector_sources("launch", root=self.root)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["label"], "Launch 项目群")
        self.assertEqual(sources[0]["sync_mode"], "direct_execution")

    def test_sync_runs_only_mapped_sources_and_builds_project_memory(self) -> None:
        self.map_sources()
        calls: list[list[str]] = []

        def runner(argv: list[str]) -> SimpleNamespace:
            calls.append(argv)
            return self.synthetic_runner(argv)

        result = lark_sync.sync_lark_workspace(
            "launch", root=self.root, now="2026-08-29T02:00:00Z", runner=runner
        )
        self.assertEqual(result["status"], "synced")
        self.assertEqual(result["mapped_sources"], 2)
        self.assertEqual(result["observations"], 3)
        self.assertEqual(len(calls), 2)
        self.assertIn("oc_launch", calls[0])
        self.assertIn("https://example.feishu.cn/docx/doc_launch_prd", calls[1])
        self.assertTrue((self.workspace / result["manifest_ref"]).is_file())
        self.assertTrue((self.workspace / result["observation_ref"]).is_file())

        project = lark_sync.load_project_memory("launch", root=self.root)
        self.assertEqual(project["status"], "ready")
        self.assertEqual(project["connector"]["mapped_sources"], 2)
        self.assertIn("决策", project["sections"]["decisions"][0]["text"])
        self.assertIn("下一步", project["sections"]["next_actions"][0]["text"])
        self.assertEqual(
            {item["text"] for item in project["sections"]["people"]},
            {"Alice", "Bob"},
        )
        self.assertEqual(len(project["sources"]), 2)
        self.assertTrue(all(item["source_note"].startswith("wiki/sources/S-") for item in project["sources"]))
        checked = workspace.check_workspace("launch", root=self.root)
        self.assertEqual(checked["status"], "OK", checked["errors"])

    def test_command_failure_leaves_no_snapshot_or_checkpoint(self) -> None:
        self.map_sources()

        def failing(argv: list[str]) -> SimpleNamespace:
            if "+fetch" in argv:
                return SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr=json.dumps({"ok": False, "error": {"message": "missing scope docs:doc:readonly"}}),
                )
            return self.synthetic_runner(argv)

        with self.assertRaisesRegex(MemoryWorkspaceError, "明确映射"):
            lark_sync.sync_lark_workspace(
                "launch", root=self.root, now="2026-08-29T02:00:00Z", runner=failing
            )
        self.assertFalse((self.workspace / "connected" / "lark").exists())
        self.assertIsNone(connectors.connector_status("launch", root=self.root)["checkpoint"])

    def test_incremental_sync_rebuilds_from_all_observations(self) -> None:
        self.map_sources()
        lark_sync.sync_lark_workspace(
            "launch", root=self.root, now="2026-08-29T02:00:00Z", runner=self.synthetic_runner
        )

        def later_runner(argv: list[str]) -> SimpleNamespace:
            if "+chat-messages-list" in argv:
                data = {
                    "messages": [
                        {
                            "message_id": "m3",
                            "msg_type": "text",
                            "create_time": "2026-08-29T09:00:00+00:00",
                            "sender": {"id": "ou_alice", "type": "user", "name": "Alice"},
                            "content": {"text": "今天完成了项目详情接口。"},
                        }
                    ]
                }
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"ok": True, "identity": "user", "data": data}),
                    stderr="",
                )
            return self.synthetic_runner(argv)

        lark_sync.sync_lark_workspace(
            "launch", root=self.root, now="2026-08-30T03:00:00Z", force=True, runner=later_runner
        )
        project = lark_sync.load_project_memory("launch", root=self.root)
        self.assertTrue(any("首版只覆盖" in item["text"] for item in project["sections"]["decisions"]))
        self.assertTrue(any("今天完成" in item["text"] for item in project["sections"]["updates"]))
        self.assertEqual([item["text"] for item in project["sections"]["people"]].count("Alice"), 1)

    def test_sync_refuses_to_discover_or_guess_when_no_source_is_mapped(self) -> None:
        with self.assertRaisesRegex(MemoryWorkspaceError, "拒绝扩大"):
            lark_sync.sync_lark_workspace(
                "launch", root=self.root, now="2026-08-29T02:00:00Z", runner=self.synthetic_runner
            )


if __name__ == "__main__":
    unittest.main()
