from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_workspace import capture, habits, home, policy, profile, workspace
from memory_workspace.io import MemoryWorkspaceError


class MemoryHomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "memory-home"
        self.environment = os.environ.copy()
        self.environment["MEMORY_HOME"] = str(self.root)
        self.environment["MWORK_ALLOW_TRANSIENT"] = "1"
        self.environment["MEMORY_HOME_ALLOW_TRANSIENT"] = "1"
        for key in ("PMEM_FILE", "PMEM_DIR", "MWORK_WORKSPACES_DIR", "MWORK_CAPTURE_DIR"):
            self.environment.pop(key, None)
        self.patcher = patch.dict(os.environ, self.environment, clear=True)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.temporary.cleanup()

    def test_paths_share_one_memory_home_by_default(self) -> None:
        self.assertEqual(home.home_path(), self.root)
        self.assertEqual(
            profile.store_path(), self.root / "personal" / "profile" / "exact.json"
        )
        self.assertEqual(workspace.workspaces_path(), self.root / "workspaces")
        self.assertEqual(capture.capture_path(), self.root / "system" / "capture")

    def test_init_creates_parallel_personal_workspace_and_system_layers(self) -> None:
        result = home.init_home(root=self.root)
        self.assertTrue(result["created"])
        self.assertEqual(result["status"], "OK")
        self.assertTrue((self.root / "memory-home.json").is_file())
        self.assertTrue((self.root / "personal" / "work" / "overview.md").is_file())
        self.assertTrue((self.root / "workspaces").is_dir())
        self.assertTrue((self.root / "system" / "capture").is_dir())
        self.assertFalse((self.root / "personal" / "profile" / "exact.json").exists())

        again = home.init_home(root=self.root)
        self.assertFalse(again["created"])
        self.assertEqual(again["status"], "OK")

    def test_default_workspace_and_capture_writes_initialize_home(self) -> None:
        workspace.init_workspace("alpha", name="Alpha")
        event = capture.enqueue_event("Keep this as a reviewable observation.")

        self.assertTrue((self.root / "memory-home.json").is_file())
        manifest = json.loads(
            (self.root / "workspaces" / "alpha" / "llm-wiki.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["scope"], "workspace")
        self.assertFalse((self.root / "workspaces" / "alpha" / "personal").exists())
        self.assertTrue(Path(event["event_path"]).is_file())

    def test_habits_and_policies_default_to_personal_learning(self) -> None:
        report = habits.build_baseline_document(
            [],
            coverage_start="2026-08-01T00:00:00Z",
            coverage_end="2026-08-27T00:00:00Z",
            days=27,
        )
        written = habits.write_report(report)
        built = policy.build_policy([])

        self.assertEqual(
            Path(written["markdown_path"]),
            self.root / "personal" / "learning" / "query-habits.md",
        )
        self.assertEqual(
            Path(built["policy_path"]).parent,
            self.root / "personal" / "learning" / "policies",
        )
        self.assertFalse((self.root / "system" / "capture" / "learning").exists())

    def test_component_overrides_remain_compatible(self) -> None:
        environment = dict(self.environment)
        environment["PMEM_DIR"] = str(Path(self.temporary.name) / "profile-override")
        environment["MWORK_WORKSPACES_DIR"] = str(
            Path(self.temporary.name) / "workspace-override"
        )
        environment["MWORK_CAPTURE_DIR"] = str(
            Path(self.temporary.name) / "capture-override"
        )
        self.assertEqual(
            home.exact_profile_path_info(environment)[0],
            Path(self.temporary.name) / "profile-override" / "store.json",
        )
        self.assertEqual(
            home.workspaces_path_info(environment)[0],
            Path(self.temporary.name) / "workspace-override",
        )
        self.assertEqual(
            home.capture_path_info(environment)[0],
            Path(self.temporary.name) / "capture-override",
        )

    def test_migration_copies_and_never_deletes_legacy_data(self) -> None:
        legacy_personal = Path(self.temporary.name) / "legacy-personal"
        legacy_runtime = Path(self.temporary.name) / "legacy-runtime"
        profile_source = legacy_personal / "store.json"
        workspace_source = legacy_personal / "workspaces" / "alpha" / "llm-wiki.json"
        capture_source = legacy_runtime / "capture" / "events" / "evt_example.json"
        habits_source = legacy_runtime / "capture" / "learning" / "query-habits.md"
        profile_source.parent.mkdir(parents=True)
        workspace_source.parent.mkdir(parents=True)
        capture_source.parent.mkdir(parents=True)
        habits_source.parent.mkdir(parents=True)
        profile_source.write_text(
            json.dumps({"schema_version": 1, "updated_at": 1, "items": {}}),
            encoding="utf-8",
        )
        workspace_source.write_text('{"workspace": "legacy"}\n', encoding="utf-8")
        capture_source.write_text('{"event": "legacy"}\n', encoding="utf-8")
        habits_source.write_text("# Legacy habits\n", encoding="utf-8")

        plan = home.migration_plan(
            root=self.root,
            legacy_personal_root=legacy_personal,
            legacy_runtime_root=legacy_runtime,
        )
        self.assertTrue(plan["can_apply"])
        self.assertEqual(plan["counts"]["copy"], 4)
        self.assertFalse(plan["deletes_source"])

        result = home.migrate_legacy(
            root=self.root,
            legacy_personal_root=legacy_personal,
            legacy_runtime_root=legacy_runtime,
        )
        self.assertEqual(result["copied"], 4)
        self.assertFalse(result["source_deleted"])
        self.assertTrue(profile_source.is_file())
        self.assertTrue(workspace_source.is_file())
        self.assertTrue(capture_source.is_file())
        self.assertTrue(habits_source.is_file())
        self.assertEqual(
            (self.root / "personal" / "profile" / "exact.json").read_bytes(),
            profile_source.read_bytes(),
        )
        self.assertTrue(Path(result["receipt_path"]).is_file())
        self.assertTrue(
            (self.root / "personal" / "learning" / "query-habits.md").is_file()
        )
        self.assertFalse(
            (self.root / "system" / "capture" / "learning" / "query-habits.md").exists()
        )

    def test_migration_conflict_stops_before_copy(self) -> None:
        legacy_personal = Path(self.temporary.name) / "legacy-personal"
        legacy_runtime = Path(self.temporary.name) / "legacy-runtime"
        source = legacy_personal / "store.json"
        source.parent.mkdir(parents=True)
        source.write_text('{"old": true}\n', encoding="utf-8")
        target = self.root / "personal" / "profile" / "exact.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"new": true}\n', encoding="utf-8")

        plan = home.migration_plan(
            root=self.root,
            legacy_personal_root=legacy_personal,
            legacy_runtime_root=legacy_runtime,
        )
        self.assertFalse(plan["can_apply"])
        self.assertEqual(plan["counts"]["conflict"], 1)
        with self.assertRaisesRegex(MemoryWorkspaceError, "冲突"):
            home.migrate_legacy(
                root=self.root,
                legacy_personal_root=legacy_personal,
                legacy_runtime_root=legacy_runtime,
            )
        self.assertEqual(target.read_text(encoding="utf-8"), '{"new": true}\n')
        self.assertFalse((self.root / "memory-home.json").exists())

    def test_migration_rejects_a_symlinked_destination(self) -> None:
        legacy_personal = Path(self.temporary.name) / "legacy-personal"
        legacy_runtime = Path(self.temporary.name) / "legacy-runtime"
        source = legacy_personal / "store.json"
        source.parent.mkdir(parents=True)
        source.write_text('{"old": true}\n', encoding="utf-8")
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text('{"old": true}\n', encoding="utf-8")
        target = self.root / "personal" / "profile" / "exact.json"
        target.parent.mkdir(parents=True)
        target.symlink_to(outside)

        plan = home.migration_plan(
            root=self.root,
            legacy_personal_root=legacy_personal,
            legacy_runtime_root=legacy_runtime,
        )
        self.assertFalse(plan["can_apply"])
        self.assertEqual(plan["counts"]["blocked_symlink"], 1)
        with self.assertRaisesRegex(MemoryWorkspaceError, "符号链接"):
            home.migrate_legacy(
                root=self.root,
                legacy_personal_root=legacy_personal,
                legacy_runtime_root=legacy_runtime,
            )
        self.assertEqual(outside.read_text(encoding="utf-8"), '{"old": true}\n')


if __name__ == "__main__":
    unittest.main()
