from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_workspace import workspace


class ConnectorCliWorkspaceRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspaces"
        environment = os.environ.copy()
        environment["MWORK_ALLOW_TRANSIENT"] = "1"
        self.patcher = patch.dict(os.environ, environment, clear=True)
        self.patcher.start()
        workspace.init_workspace("existing-wiki", name="Existing Wiki", root=self.root)

    def tearDown(self) -> None:
        self.patcher.stop()
        self.temporary.cleanup()

    def test_status_reads_an_explicit_existing_workspace_root(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "connectors.py"
        environment = os.environ.copy()
        environment["MWORK_ALLOW_TRANSIENT"] = "1"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "status",
                "existing-wiki",
                "--workspaces-root",
                str(self.root),
                "--json",
            ],
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertTrue(document["ok"])
        self.assertEqual(document["result"]["workspace_id"], "existing-wiki")
        self.assertFalse(document["result"]["enabled"])


if __name__ == "__main__":
    unittest.main()
