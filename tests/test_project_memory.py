from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_workspace import connector_protocol, project_memory, workspace


class ProviderNeutralProjectMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspaces"
        environment = os.environ.copy()
        environment["MWORK_ALLOW_TRANSIENT"] = "1"
        self.patcher = patch.dict(os.environ, environment, clear=True)
        self.patcher.start()
        workspace.init_workspace("field-notes", name="Field Notes", root=self.root)

    def tearDown(self) -> None:
        self.patcher.stop()
        self.temporary.cleanup()

    def test_core_modules_do_not_import_the_bundled_provider(self) -> None:
        package = Path(__file__).resolve().parents[1] / "memory_workspace"
        for relative in (
            "connector_protocol.py",
            "project_memory.py",
            "home_view.py",
            "ui_server.py",
            "workspace.py",
        ):
            source = (package / relative).read_text(encoding="utf-8")
            self.assertNotIn("providers.lark", source, relative)
            self.assertNotIn("from .connectors", source, relative)
            self.assertNotIn("from .lark_sync", source, relative)

    def test_legacy_single_connector_view_is_upgraded_in_place(self) -> None:
        target = self.root / "field-notes" / ".llm-wiki" / "index" / "project-memory.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "workspace": {"id": "field-notes", "name": "Field Notes"},
                    "generated_at": "2026-08-29T02:00:00Z",
                    "status": "ready",
                    "notice": "legacy derived view",
                    "connector": {
                        "provider": "lark",
                        "configured": True,
                        "enabled": True,
                        "mapped_sources": 1,
                        "last_success_at": "2026-08-29T02:00:00Z",
                        "coverage_end": "2026-08-29T02:00:00Z",
                    },
                    "sections": {
                        key: []
                        for key in (
                            "updates",
                            "decisions",
                            "next_actions",
                            "people",
                            "artifacts",
                        )
                    },
                    "sources": [
                        {
                            "source_id": "lark-chat-example",
                            "kind": "chat",
                            "label": "Example",
                            "external_id": "oc_example",
                            "source_note": "wiki/sources/S-001.md",
                            "latest_snapshot_ref": "connected/lark/chats/lark-chat-example/2026-08-29T020000Z.json",
                            "captured_at": "2026-08-29T02:00:00Z",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        view = project_memory.load_project_memory("field-notes", root=self.root)

        self.assertNotIn("connector", view)
        self.assertEqual(view["connectors"][0]["provider"], "lark")
        self.assertEqual(view["sources"][0]["provider"], "lark")
        persisted = json.loads(target.read_text(encoding="utf-8"))
        self.assertIn("connectors", persisted)

    def test_non_lark_adapter_bundle_uses_the_same_core_contract(self) -> None:
        source = {
            "source_id": "local-notes-project-log",
            "provider": "local-notes",
            "kind": "document",
            "external_id": "project-log",
            "locator": "file:project-log",
            "label": "Project Log",
        }
        connector_protocol.map_source(
            "field-notes",
            provider=source["provider"],
            source_id=source["source_id"],
            kind=source["kind"],
            external_id=source["external_id"],
            locator=source["locator"],
            label=source["label"],
            root=self.root,
            now="2026-08-29T01:00:00Z",
        )
        snapshot_ref = project_memory.snapshot_ref(
            provider=source["provider"],
            kind=source["kind"],
            source_id=source["source_id"],
            captured_at="2026-08-30T02:00:00Z",
        )
        observation = {
            "schema_version": 1,
            "observation_id": "obs_local_notes_project_log_1",
            "event_type": "asset.updated",
            "occurred_at": "2026-08-30T01:30:00Z",
            "observed_at": "2026-08-30T02:00:00Z",
            "source": {
                "provider": "local-notes",
                "connector_id": "local-notes-default",
                "adapter": "local-notes-test",
                "adapter_version": "1.0.0",
                "identity_mode": "user",
                "workspace_id": "field-notes",
            },
            "object": {
                "object_type": "document",
                "external_id": "project-log",
                "parent_external_id": None,
                "canonical_url": None,
                "content_type": "text/markdown",
                "attributes": {"source_id": "local-notes-project-log"},
            },
            "actors": [],
            "content": {
                "title": "Project Log",
                "text_excerpt": "决策：适配器只生成标准数据包，Memory Core 统一落盘。",
                "content_hash": None,
                "attachment_refs": [],
            },
            "provenance": {
                "snapshot_ref": snapshot_ref,
                "command_category": "local_file_read",
                "time_coverage": {
                    "start": "2026-08-29T02:00:00Z",
                    "end": "2026-08-30T02:00:00Z",
                    "complete": True,
                },
            },
            "routing": {
                "project_id": "field-notes",
                "project_confidence": 1.0,
                "reason_codes": ["explicit_mapping"],
                "candidate_eligible": True,
            },
            "privacy": {
                "classification": "local_content",
                "expires_at": "2026-11-28T02:00:00Z",
                "profile_write_allowed": False,
            },
        }
        result = project_memory.persist_sync_bundle(
            "field-notes",
            provider="local-notes",
            connector_id="local-notes-default",
            captured_at="2026-08-30T02:00:00Z",
            trigger="manual",
            coverage={
                "start": "2026-08-29T02:00:00Z",
                "end": "2026-08-30T02:00:00Z",
            },
            snapshots=[
                {
                    "source": source,
                    "snapshot_ref": snapshot_ref,
                    "command_category": "local_file_read",
                    "result_count": 1,
                    "raw": json.dumps({"text": "project evidence"}).encode(),
                }
            ],
            observations=[observation],
            limitations=[],
            root=self.root,
        )

        self.assertEqual(result["provider"], "local-notes")
        view = project_memory.load_project_memory("field-notes", root=self.root)
        self.assertEqual(view["connectors"][0]["provider"], "local-notes")
        self.assertIn("Memory Core", view["sections"]["decisions"][0]["text"])
        self.assertEqual(view["sources"][0]["provider"], "local-notes")
        self.assertTrue((self.root / "field-notes" / snapshot_ref).is_file())
        checked = workspace.check_workspace("field-notes", root=self.root)
        self.assertEqual(checked["status"], "OK", checked["errors"])


if __name__ == "__main__":
    unittest.main()
