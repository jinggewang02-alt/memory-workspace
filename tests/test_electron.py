from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ElectronDesktopContractTests(unittest.TestCase):
    def test_package_exposes_desktop_entry_and_bundles_core_resources(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["main"], "desktop/main.cjs")
        self.assertEqual(package["scripts"]["desktop"], "electron .")
        destinations = {
            item["to"] for item in package["build"]["extraResources"]
        }
        self.assertIn("memory-home/memory_workspace", destinations)
        self.assertIn("memory-home/scripts", destinations)
        self.assertIn("memory-home/ui", destinations)

    def test_main_process_keeps_core_loopback_only_and_renderer_restricted(self) -> None:
        source = (ROOT / "desktop" / "main.cjs").read_text(encoding="utf-8")
        self.assertIn('path.join(root, "scripts", "quickstart.py")', source)
        self.assertIn('"/opt/homebrew/bin/python3.12"', source)
        self.assertIn('"127.0.0.1"', source)
        self.assertIn('"--port", "0"', source)
        self.assertIn('"--no-open"', source)
        self.assertIn("contextIsolation: true", source)
        self.assertIn("nodeIntegration: false", source)
        self.assertIn("sandbox: true", source)
        self.assertIn("setPermissionRequestHandler", source)
        self.assertIn('app.setName("Memory Home")', source)

    def test_preload_exposes_only_runtime_metadata(self) -> None:
        source = (ROOT / "desktop" / "preload.cjs").read_text(encoding="utf-8")
        self.assertIn('exposeInMainWorld("memoryHomeDesktop"', source)
        self.assertNotIn("require: ", source)
        self.assertNotIn("readFile", source)


if __name__ == "__main__":
    unittest.main()
