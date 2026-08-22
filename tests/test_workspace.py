from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "workspace.py"


class WorkspaceCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspaces"
        self.environment = os.environ.copy()
        self.environment["MWORK_WORKSPACES_DIR"] = str(self.root)
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
        completed = self.run_cli(*args, expected=expected)
        return json.loads(completed.stdout)

    def init_workspace(self) -> Path:
        payload = self.result_json("init", "research-notes", "--name", "Research Notes", "--json")
        self.assertTrue(payload["ok"])
        return self.root / "research-notes"

    def test_init_list_inspect_and_check(self) -> None:
        workspace = self.init_workspace()
        self.assertTrue((workspace / "llm-wiki.json").is_file())
        self.assertTrue((workspace / "raw" / "inbox").is_dir())

        listing = self.result_json("list", "--json")["result"]
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["workspace_id"], "research-notes")

        inspection = self.result_json("inspect", "research-notes", "--json")["result"]
        self.assertEqual(inspection["manifest"]["workspace"]["name"], "Research Notes")
        self.assertEqual(inspection["counts"], {"sources": 0, "operations": 1})

        check = self.result_json("check", "research-notes", "--json")["result"]
        self.assertEqual(check["status"], "OK")
        self.assertEqual(check["counts"]["operations"], 1)

    def test_ingest_is_hashed_audited_and_idempotent(self) -> None:
        workspace = self.init_workspace()
        source = Path(self.temporary.name) / "source.txt"
        source.write_text("An exact source.\n", encoding="utf-8")
        expected_hash = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()

        first = self.result_json(
            "source",
            "ingest",
            "research-notes",
            str(source),
            "--title",
            "Exact Source",
            "--json",
        )["result"]
        self.assertFalse(first["duplicate"])
        self.assertEqual(first["source_id"], "S-001")
        self.assertEqual(first["content_hash"], expected_hash)
        self.assertTrue((workspace / first["content_path"]).is_file())
        note = (workspace / "wiki" / "sources" / "S-001.md").read_text(encoding="utf-8")
        self.assertIn(expected_hash, note)
        self.assertIn("[[sources/S-001|Exact Source]]", (workspace / "wiki" / "index.md").read_text())

        operation_path = workspace / ".llm-wiki" / "operations" / f"{first['operation_id']}.json"
        operation = json.loads(operation_path.read_text(encoding="utf-8"))
        self.assertEqual(operation["type"], "ingest")
        self.assertEqual(operation["validation"]["status"], "passed")
        self.assertEqual(operation["approval"]["status"], "approved")

        second = self.result_json(
            "source", "ingest", "research-notes", str(source), "--json"
        )["result"]
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["source_id"], "S-001")
        self.assertIsNone(second["operation_id"])
        self.assertEqual(len(list((workspace / "wiki" / "sources").glob("S-*.md"))), 1)
        self.assertEqual(len(list((workspace / ".llm-wiki" / "operations").glob("op_*.json"))), 2)

    def test_check_detects_immutable_source_change(self) -> None:
        workspace = self.init_workspace()
        source = Path(self.temporary.name) / "evidence.md"
        source.write_text("original\n", encoding="utf-8")
        result = self.result_json(
            "source", "ingest", "research-notes", str(source), "--json"
        )["result"]
        captured = workspace / result["content_path"]
        captured.write_text("modified\n", encoding="utf-8")

        check = self.result_json(
            "check", "research-notes", "--json", expected=1
        )["result"]
        self.assertEqual(check["status"], "FAILED")
        self.assertTrue(any("hash mismatch" in error for error in check["errors"]))

    def test_invalid_slug_and_transient_write_are_rejected(self) -> None:
        bad = self.result_json("init", "../escape", "--name", "Bad", "--json", expected=1)
        self.assertFalse(bad["ok"])
        self.assertIn("workspace id", bad["error"])

        environment = self.environment.copy()
        environment.pop("MWORK_ALLOW_TRANSIENT")
        with patch.dict(os.environ, environment, clear=True):
            result = subprocess.run(
                [sys.executable, str(CLI), "init", "blocked", "--name", "Blocked", "--json"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("临时目录", json.loads(result.stdout)["error"])

    def test_private_key_source_is_rejected(self) -> None:
        workspace = self.init_workspace()
        source = Path(self.temporary.name) / "secret.pem"
        source.write_text("-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n", encoding="utf-8")
        result = self.result_json(
            "source", "ingest", "research-notes", str(source), "--json", expected=1
        )
        self.assertFalse(result["ok"])
        self.assertFalse(any((workspace / "raw" / "inbox").iterdir()))


if __name__ == "__main__":
    unittest.main()
