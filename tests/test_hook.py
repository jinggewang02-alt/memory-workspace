from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "adapters" / "codex" / "on_user_prompt.py"


class CodexHookTests(unittest.TestCase):
    def run_hook(self, prompt: str) -> str:
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            cwd=ROOT,
            input=json.dumps({"prompt": prompt}, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_workspace_intent_uses_workspace_route(self) -> None:
        output = self.run_hook("把这个文件收录进我的 wiki")
        self.assertIn("PROJECT_WIKI", output)
        self.assertIn("workspace.py", output)
        self.assertNotIn("IMPORT_FILE", output)

    def test_personal_fact_keeps_profile_route(self) -> None:
        output = self.run_hook("请记住我的学号是 12345")
        self.assertIn("REMEMBER", output)
        self.assertIn("store.py", output)

    def test_unrelated_prompt_is_silent(self) -> None:
        self.assertEqual(self.run_hook("解释一下二分查找"), "")


if __name__ == "__main__":
    unittest.main()
