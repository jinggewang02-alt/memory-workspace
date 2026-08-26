from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from memory_workspace import capture, operations, profile, workspace, writer
from memory_workspace.io import MemoryWorkspaceError


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_CLI = ROOT / "scripts" / "capture.py"


class CandidateSingleWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.capture_root = base / "capture"
        self.workspaces_root = base / "workspaces"
        self.profile_path = base / "profile" / "store.json"
        self.previous_workspace_override = os.environ.get("MWORK_ALLOW_TRANSIENT")
        self.previous_profile_override = os.environ.get("PMEM_ALLOW_TRANSIENT")
        os.environ["MWORK_ALLOW_TRANSIENT"] = "1"
        os.environ["PMEM_ALLOW_TRANSIENT"] = "1"

    def tearDown(self) -> None:
        for key, previous in (
            ("MWORK_ALLOW_TRANSIENT", self.previous_workspace_override),
            ("PMEM_ALLOW_TRANSIENT", self.previous_profile_override),
        ):
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        self.temporary.cleanup()

    def candidate(self, *, scope: str, content: str, key: str = "表达偏好") -> str:
        event = capture.enqueue_event(
            content,
            conversation_id="conv-writer-test",
            workspace_id="memory-workspace" if scope == "project" else None,
            root=self.capture_root,
        )
        resolved = capture.resolve_event(
            event["event_id"],
            decision=scope,
            reason="Synthetic durable memory for Writer tests.",
            content=content,
            confidence=0.94,
            workspace_id="memory-workspace" if scope == "project" else None,
            profile_key=key if scope == "profile" else None,
            kind="decision" if scope == "project" else "preference",
            root=self.capture_root,
        )
        return str(resolved["candidate_id"])

    def test_profile_candidate_writes_reads_back_and_is_idempotent(self) -> None:
        candidate_id = self.candidate(
            scope="profile", content="先给结论，再说明关键依据。"
        )
        capture.decide_candidate(
            candidate_id,
            decision="approved",
            actor="owner",
            target_ref="profile:表达偏好",
            root=self.capture_root,
        )

        applied = writer.apply_candidate(
            candidate_id,
            actor="owner_via_test",
            capture_root=self.capture_root,
            profile_path=self.profile_path,
        )
        self.assertEqual(applied["status"], "applied")
        self.assertFalse(applied["already_applied"])
        self.assertEqual(
            profile.get_item(self.profile_path, "表达偏好")["value"],
            "先给结论，再说明关键依据。",
        )
        receipt = applied["application"]
        self.assertEqual(receipt["schema_version"], 2)
        self.assertEqual(receipt["writer"]["kind"], "profile_single")
        self.assertEqual(receipt["verification"]["status"], "passed")

        repeated = writer.apply_candidate(
            candidate_id,
            capture_root=self.capture_root,
            profile_path=self.profile_path,
        )
        self.assertTrue(repeated["already_applied"])

    def test_profile_conflict_stops_without_overwriting_or_receipt(self) -> None:
        profile.set_single(self.profile_path, "表达偏好", "保留原值")
        candidate_id = self.candidate(scope="profile", content="新的不同值")
        capture.decide_candidate(
            candidate_id,
            decision="approved",
            actor="owner",
            target_ref="profile:表达偏好",
            root=self.capture_root,
        )

        with self.assertRaisesRegex(MemoryWorkspaceError, "已有不同值"):
            writer.apply_candidate(
                candidate_id,
                capture_root=self.capture_root,
                profile_path=self.profile_path,
            )
        self.assertEqual(profile.get_item(self.profile_path, "表达偏好")["value"], "保留原值")
        self.assertEqual(
            capture.show_candidate(candidate_id, root=self.capture_root)["status"],
            "approved",
        )

    def test_project_candidate_runs_operation_check_and_target_readback(self) -> None:
        workspace.init_workspace(
            "memory-workspace",
            name="Memory Workspace",
            root=self.workspaces_root,
        )
        content = "Agent 环境准备应基于能力探测，不写死平台名称。"
        candidate_id = self.candidate(scope="project", content=content)
        target_ref = (
            "workspace:memory-workspace/"
            "wiki/projects/memory-workspace/decisions.md"
        )
        capture.decide_candidate(
            candidate_id,
            decision="approved",
            actor="owner",
            target_ref=target_ref,
            root=self.capture_root,
            workspaces_root=self.workspaces_root,
        )

        applied = writer.apply_candidate(
            candidate_id,
            actor="owner_via_test",
            capture_root=self.capture_root,
            workspaces_root=self.workspaces_root,
        )
        receipt = applied["application"]
        operation_id = receipt["writer"]["operation_id"]
        target = (
            self.workspaces_root
            / "memory-workspace/wiki/projects/memory-workspace/decisions.md"
        )
        page = target.read_text(encoding="utf-8")
        self.assertIn(f"memory-workspace:candidate:{candidate_id}", page)
        self.assertIn(content, page)
        self.assertEqual(
            operations.show_operation(
                "memory-workspace", operation_id, root=self.workspaces_root
            )["operation"]["status"],
            "applied",
        )
        self.assertEqual(
            workspace.check_workspace("memory-workspace", root=self.workspaces_root)[
                "status"
            ],
            "OK",
        )
        self.assertIn("target-readback", receipt["verification"]["checks"])

    def test_writer_rejects_candidate_without_owner_approval(self) -> None:
        candidate_id = self.candidate(scope="profile", content="尚未批准")
        with self.assertRaisesRegex(MemoryWorkspaceError, "先由用户批准"):
            writer.apply_candidate(
                candidate_id,
                capture_root=self.capture_root,
                profile_path=self.profile_path,
            )

    def test_candidate_apply_cli_uses_the_same_writer(self) -> None:
        candidate_id = self.candidate(
            scope="profile", content="CLI 也必须走统一 Writer。", key="CLI 写入约束"
        )
        capture.decide_candidate(
            candidate_id,
            decision="approved",
            actor="owner",
            target_ref="profile:CLI 写入约束",
            root=self.capture_root,
        )
        environment = os.environ.copy()
        environment["MWORK_CAPTURE_DIR"] = str(self.capture_root)
        environment["PMEM_FILE"] = str(self.profile_path)
        result = subprocess.run(
            [
                sys.executable,
                str(CAPTURE_CLI),
                "candidate",
                "apply",
                candidate_id,
                "--json",
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["result"]["status"], "applied")
        self.assertEqual(
            profile.get_item(self.profile_path, "CLI 写入约束")["value"],
            "CLI 也必须走统一 Writer。",
        )


if __name__ == "__main__":
    unittest.main()
