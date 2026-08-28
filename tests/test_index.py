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


class WorkspaceIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspaces"
        self.environment = os.environ.copy()
        self.environment["MWORK_WORKSPACES_DIR"] = str(self.root)
        self.environment["MWORK_ALLOW_TRANSIENT"] = "1"
        self.result_json("init", "search-lab", "--name", "Search Lab", "--json")
        self.workspace = self.root / "search-lab"

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

    def test_init_creates_valid_disposable_index(self) -> None:
        path = self.workspace / ".llm-wiki" / "index" / "workspace-index.json"
        self.assertTrue(path.is_file())
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["workspace"], {"id": "search-lab", "name": "Search Lab"})
        self.assertEqual(len(document["operations"]), 1)
        inspection = self.result_json("inspect", "search-lab", "--json")["result"]
        self.assertTrue(inspection["index"]["exists"])

    def test_ingested_source_is_queryable(self) -> None:
        source = Path(self.temporary.name) / "roadmap.txt"
        source.write_text("UI review roadmap\n", encoding="utf-8")
        self.result_json(
            "source",
            "ingest",
            "search-lab",
            str(source),
            "--title",
            "Review Roadmap",
            "--json",
        )
        query = self.result_json("query", "search-lab", "Roadmap", "--json")["result"]
        self.assertGreaterEqual(query["count"], 1)
        self.assertTrue(
            any(item["type"] == "source" and item["id"] == "S-001" for item in query["results"])
        )
        source_result = next(item for item in query["results"] if item["type"] == "source")
        reference = source_result["memory_reference"]
        self.assertEqual(reference["schema_version"], 1)
        self.assertEqual(reference["scope"], "workspace")
        self.assertEqual(reference["workspace_id"], "search-lab")
        self.assertEqual(reference["label"], "Workspace · Search Lab · Review Roadmap")
        self.assertFalse(reference["path"].startswith("/"))
        self.assertIn(reference, query["memory_references"])

    def test_rebuild_indexes_project_pages_and_full_text(self) -> None:
        project = self.workspace / "wiki" / "projects" / "memory-ui"
        project.mkdir(parents=True)
        (project / "overview.md").write_text(
            "---\nkind: project_overview\ntitle: \"Memory UI\"\n---\n\n# Memory UI\n\nApproval cockpit.\n",
            encoding="utf-8",
        )
        rebuilt = self.result_json("index", "rebuild", "search-lab", "--json")["result"]
        self.assertEqual(rebuilt["counts"]["projects"], 1)
        query = self.result_json(
            "query", "search-lab", "cockpit", "--no-rebuild", "--json"
        )["result"]
        self.assertTrue(
            any(item["type"] == "page" and item["title"] == "Memory UI" for item in query["results"])
        )
        self.assertTrue(query["memory_references"])

    def test_query_rejects_invalid_limit(self) -> None:
        result = self.result_json(
            "query", "search-lab", "anything", "--limit", "0", "--json", expected=1
        )
        self.assertFalse(result["ok"])
        self.assertIn("limit", result["error"])

    def test_operation_state_changes_refresh_index(self) -> None:
        candidate = Path(self.temporary.name) / "state.md"
        candidate.write_text("# Review state\n", encoding="utf-8")
        proposal = self.result_json(
            "operation",
            "propose-file",
            "search-lab",
            "wiki/topics/state.md",
            "--content-file",
            str(candidate),
            "--json",
        )["result"]
        index_path = self.workspace / ".llm-wiki" / "index" / "workspace-index.json"

        def indexed_status() -> str:
            document = json.loads(index_path.read_text(encoding="utf-8"))
            match = next(
                item
                for item in document["operations"]
                if item["operation_id"] == proposal["operation_id"]
            )
            return match["status"]

        self.assertEqual(indexed_status(), "proposed")
        self.result_json(
            "operation", "approve", "search-lab", proposal["operation_id"], "--json"
        )
        self.assertEqual(indexed_status(), "approved")


if __name__ == "__main__":
    unittest.main()
