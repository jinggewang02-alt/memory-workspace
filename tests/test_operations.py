from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "workspace.py"


class OperationLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspaces"
        self.environment = os.environ.copy()
        self.environment["MWORK_WORKSPACES_DIR"] = str(self.root)
        self.environment["MWORK_ALLOW_TRANSIENT"] = "1"
        self.result_json("init", "review-lab", "--name", "Review Lab", "--json")
        self.workspace = self.root / "review-lab"

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

    def candidate(self, name: str, content: str) -> Path:
        path = Path(self.temporary.name) / name
        path.write_text(content, encoding="utf-8")
        return path

    def propose(self, target: str, content: str) -> dict[str, object]:
        candidate = self.candidate("candidate.md", content)
        return self.result_json(
            "operation",
            "propose-file",
            "review-lab",
            target,
            "--content-file",
            str(candidate),
            "--json",
        )["result"]

    def approve_and_apply(self, operation_id: str) -> None:
        approved = self.result_json(
            "operation", "approve", "review-lab", operation_id, "--json"
        )["result"]
        self.assertEqual(approved["status"], "approved")
        applied = self.result_json(
            "operation", "apply", "review-lab", operation_id, "--json"
        )["result"]
        self.assertEqual(applied["status"], "applied")

    def test_proposal_does_not_write_before_approval_then_applies(self) -> None:
        target = "wiki/topics/research.md"
        proposal = self.propose(target, "# Research\n\nDraft finding.\n")
        operation_id = proposal["operation_id"]
        self.assertEqual(proposal["status"], "proposed")
        self.assertIn("+++ b/wiki/topics/research.md", proposal["diff"])
        self.assertFalse((self.workspace / target).exists())

        listing = self.result_json(
            "operation", "list", "review-lab", "--status", "proposed", "--json"
        )["result"]
        self.assertEqual([item["operation_id"] for item in listing], [operation_id])
        shown = self.result_json(
            "operation", "show", "review-lab", operation_id, "--json"
        )["result"]
        self.assertEqual(shown["operation"]["status"], "proposed")
        self.assertIn("Draft finding", shown["diffs"][0]["diff"])

        self.approve_and_apply(operation_id)
        self.assertEqual(
            (self.workspace / target).read_text(encoding="utf-8"),
            "# Research\n\nDraft finding.\n",
        )
        shown = self.result_json(
            "operation", "show", "review-lab", operation_id, "--json"
        )["result"]
        self.assertEqual(shown["operation"]["status"], "applied")
        self.assertIn("workspace-check", shown["operation"]["validation"]["checks"])

    def test_rejected_proposal_cannot_apply(self) -> None:
        target = "wiki/topics/rejected.md"
        proposal = self.propose(target, "# Rejected\n")
        operation_id = proposal["operation_id"]
        rejected = self.result_json(
            "operation", "reject", "review-lab", operation_id, "--json"
        )["result"]
        self.assertEqual(rejected["status"], "rejected")
        failed = self.result_json(
            "operation", "apply", "review-lab", operation_id, "--json", expected=1
        )
        self.assertFalse(failed["ok"])
        self.assertFalse((self.workspace / target).exists())

    def test_stale_before_hash_blocks_apply_without_overwrite(self) -> None:
        target = "wiki/topics/stale.md"
        first = self.propose(target, "# Stale\n\nVersion one.\n")
        self.approve_and_apply(first["operation_id"])

        update = self.propose(target, "# Stale\n\nAgent version.\n")
        target_path = self.workspace / target
        target_path.write_text("# Stale\n\nOwner version.\n", encoding="utf-8")
        self.result_json(
            "operation", "approve", "review-lab", update["operation_id"], "--json"
        )
        failed = self.result_json(
            "operation", "apply", "review-lab", update["operation_id"], "--json", expected=1
        )
        self.assertIn("Proposal 已过期", failed["error"])
        self.assertEqual(target_path.read_text(encoding="utf-8"), "# Stale\n\nOwner version.\n")
        shown = self.result_json(
            "operation", "show", "review-lab", update["operation_id"], "--json"
        )["result"]
        self.assertEqual(shown["operation"]["status"], "approved")

    def test_missing_input_ref_is_rejected_before_staging(self) -> None:
        candidate = self.candidate("missing-ref.md", "# Missing ref\n")
        result = self.result_json(
            "operation",
            "propose-file",
            "review-lab",
            "wiki/topics/missing-ref.md",
            "--content-file",
            str(candidate),
            "--input-ref",
            "wiki/sources/S-999.md",
            "--json",
            expected=1,
        )
        self.assertIn("input-ref", result["error"])
        artifacts = [
            path
            for path in (self.workspace / ".llm-wiki" / "operations").iterdir()
            if path.is_dir()
        ]
        self.assertEqual(artifacts, [])

    def test_missing_before_artifact_blocks_update(self) -> None:
        target = "wiki/topics/artifact.md"
        created = self.propose(target, "# Artifact\n\nOriginal.\n")
        self.approve_and_apply(created["operation_id"])
        update = self.propose(target, "# Artifact\n\nUpdated.\n")
        before = (
            self.workspace
            / ".llm-wiki"
            / "operations"
            / update["operation_id"]
            / "000.before"
        )
        before.unlink()
        self.result_json(
            "operation", "approve", "review-lab", update["operation_id"], "--json"
        )
        failed = self.result_json(
            "operation", "apply", "review-lab", update["operation_id"], "--json", expected=1
        )
        self.assertIn("before 内容哈希异常", failed["error"])
        self.assertEqual(
            (self.workspace / target).read_text(encoding="utf-8"),
            "# Artifact\n\nOriginal.\n",
        )

    def test_tampered_diff_is_rejected_before_review(self) -> None:
        proposal = self.propose("wiki/topics/tampered.md", "# Tampered\n")
        diff = (
            self.workspace
            / ".llm-wiki"
            / "operations"
            / proposal["operation_id"]
            / "000.diff"
        )
        diff.write_text("misleading diff\n", encoding="utf-8")
        result = self.result_json(
            "operation",
            "show",
            "review-lab",
            proposal["operation_id"],
            "--json",
            expected=1,
        )
        self.assertIn("Diff 与暂存内容不一致", result["error"])

    def test_inconsistent_status_and_approval_fail_workspace_check(self) -> None:
        proposal = self.propose("wiki/topics/state.md", "# State\n")
        manifest = (
            self.workspace
            / ".llm-wiki"
            / "operations"
            / f"{proposal['operation_id']}.json"
        )
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["approval"] = {
            "status": "approved",
            "decided_at": document["requested_at"],
            "actor": "tampered",
        }
        manifest.write_text(json.dumps(document), encoding="utf-8")
        check = self.result_json("check", "review-lab", "--json", expected=1)["result"]
        self.assertEqual(check["status"], "FAILED")
        self.assertTrue(any("must not have a decision" in error for error in check["errors"]))

    def test_failed_workspace_check_rolls_back_and_marks_failed(self) -> None:
        target = "wiki/topics/broken.md"
        proposal = self.propose(target, "# Broken\n\n[[topics/does-not-exist]]\n")
        operation_id = proposal["operation_id"]
        self.result_json(
            "operation", "approve", "review-lab", operation_id, "--json"
        )
        failed = self.result_json(
            "operation", "apply", "review-lab", operation_id, "--json", expected=1
        )
        self.assertIn("已尝试回滚", failed["error"])
        self.assertFalse((self.workspace / target).exists())
        shown = self.result_json(
            "operation", "show", "review-lab", operation_id, "--json"
        )["result"]
        self.assertEqual(shown["operation"]["status"], "failed")
        self.assertEqual(shown["operation"]["validation"]["status"], "failed")

    def test_approved_delete_removes_page_and_keeps_before_artifact(self) -> None:
        target = "wiki/topics/delete-me.md"
        created = self.propose(target, "# Delete me\n")
        self.approve_and_apply(created["operation_id"])
        deletion = self.result_json(
            "operation",
            "propose-file",
            "review-lab",
            target,
            "--delete",
            "--json",
        )["result"]
        self.approve_and_apply(deletion["operation_id"])
        self.assertFalse((self.workspace / target).exists())
        before = (
            self.workspace
            / ".llm-wiki"
            / "operations"
            / deletion["operation_id"]
            / "000.before"
        )
        self.assertEqual(before.read_text(encoding="utf-8"), "# Delete me\n")


if __name__ == "__main__":
    unittest.main()
