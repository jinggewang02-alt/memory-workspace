from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from memory_workspace.bootstrap import build_report, exit_code


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "bootstrap.py"


def probe(*, writable: bool = True, transient: bool = False):
    def inner(path, source, environ, parent_target=False):
        return {
            "path": str(path),
            "source": source,
            "exists": False,
            "nearest_existing_parent": str(path.parent),
            "filesystem_writable": writable,
            "transient_risk": transient,
            "transient_reason": "test transient path" if transient else None,
        }

    return inner


class BootstrapTests(unittest.TestCase):
    def test_ready_report_is_capability_based(self) -> None:
        report = build_report(
            ROOT,
            environ={},
            python_version=(3, 12, 1),
            path_probe=probe(),
        )
        self.assertEqual(report["status"], "READY")
        self.assertEqual(report["runtime_mode"], "local-full")
        self.assertTrue(report["write_ready"])
        self.assertEqual(exit_code(report), 0)
        serialized = json.dumps(report)
        self.assertNotIn('"platform"', serialized)
        self.assertNotIn('"host"', serialized)

    def test_old_python_needs_runtime(self) -> None:
        report = build_report(
            ROOT,
            environ={},
            python_version=(3, 9, 18),
            path_probe=probe(),
        )
        self.assertEqual(report["status"], "NEEDS_RUNTIME")
        self.assertFalse(report["write_ready"])
        self.assertEqual(exit_code(report), 1)

    def test_transient_and_permission_states_are_distinct(self) -> None:
        transient = build_report(
            ROOT,
            environ={},
            python_version=(3, 10, 0),
            path_probe=probe(transient=True),
        )
        self.assertEqual(transient["status"], "NEEDS_PERSISTENT_PATH")

        permission = build_report(
            ROOT,
            environ={},
            python_version=(3, 10, 0),
            path_probe=probe(writable=False),
        )
        self.assertEqual(permission["status"], "NEEDS_PERMISSION")

    def test_missing_skill_resources_are_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = build_report(
                Path(directory),
                environ={},
                path_probe=probe(),
            )
        self.assertEqual(report["status"], "UNSUPPORTED")
        self.assertIn("SKILL.md", report["capabilities"]["skill_resources"]["missing"])
        self.assertEqual(exit_code(report), 2)

    def test_cli_probe_is_json_and_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = os.environ.copy()
            environment["PMEM_FILE"] = str(root / "profile" / "store.json")
            environment["MWORK_WORKSPACES_DIR"] = str(root / "workspaces")
            before = sorted(path.relative_to(root) for path in root.rglob("*"))
            result = subprocess.run(
                [sys.executable, str(CLI), "--json"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            after = sorted(path.relative_to(root) for path in root.rglob("*"))

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        expected_status = (
            "NEEDS_PERSISTENT_PATH"
            if sys.version_info[:2] >= (3, 10)
            else "NEEDS_RUNTIME"
        )
        self.assertEqual(report["status"], expected_status)
        storage = report["capabilities"]["persistent_storage"]
        self.assertTrue(storage["profile_memory"]["transient_risk"])
        self.assertTrue(storage["workspaces"]["transient_risk"])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
