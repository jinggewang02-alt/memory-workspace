from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SchemaFixtureTests(unittest.TestCase):
    def test_bundled_fixtures(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "schema_check.py"), "--fixtures"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_workspace_is_rejected(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "schema_check.py"),
                str(ROOT / "schemas" / "workspace.schema.json"),
                str(ROOT / "examples" / "schema" / "workspace.invalid.json"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR:", result.stdout)


if __name__ == "__main__":
    unittest.main()
