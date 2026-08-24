from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "adapters" / "codex" / "on_user_prompt.py"


class CodexHookTests(unittest.TestCase):
    def run_hook(self, prompt: str, *, environment: dict[str, str] | None = None) -> str:
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            cwd=ROOT,
            env=environment,
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

    def test_async_capture_is_opt_in_and_only_creates_candidate_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["MWORK_ASYNC_CAPTURE"] = "signals"
            environment["MWORK_CAPTURE_DIR"] = str(Path(directory) / "capture")
            environment["MWORK_ALLOW_TRANSIENT"] = "1"
            output = self.run_hook(
                "以后统一不要写死具体 Agent 平台名称", environment=environment
            )
            self.assertIn("ASYNC_CAPTURE", output)
            self.assertIn("不得等待记忆判断", output)
            events = list((Path(directory) / "capture" / "events").glob("evt_*.json"))
            self.assertEqual(len(events), 1)
            document = json.loads(events[0].read_text(encoding="utf-8"))
            self.assertEqual(
                document["payload"]["user_message"],
                "以后统一不要写死具体 Agent 平台名称",
            )
            self.assertFalse((Path(directory) / "capture" / "resolutions").exists())

    def test_explicit_remember_is_not_duplicated_into_async_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["MWORK_ASYNC_CAPTURE"] = "all"
            environment["MWORK_CAPTURE_DIR"] = str(Path(directory) / "capture")
            environment["MWORK_ALLOW_TRANSIENT"] = "1"
            output = self.run_hook("请记住我的学号是 12345", environment=environment)
            self.assertIn("REMEMBER", output)
            self.assertNotIn("ASYNC_CAPTURE", output)
            events = Path(directory) / "capture" / "events"
            self.assertFalse(events.exists() and any(events.iterdir()))


if __name__ == "__main__":
    unittest.main()
