from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "scripts" / "store.py"


class ProfileCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store_dir = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_store(self, *arguments: str, allow_transient: bool = True) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PMEM_DIR"] = str(self.store_dir)
        if allow_transient:
            environment["PMEM_ALLOW_TRANSIENT"] = "1"
        else:
            environment.pop("PMEM_ALLOW_TRANSIENT", None)
        return subprocess.run(
            [sys.executable, str(STORE), *arguments],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_exact_single_value_round_trip_and_backup(self) -> None:
        original = "A&B（原样） 001"
        first = self.run_store("set", "测试字段", original)
        self.assertEqual(first.returncode, 0, first.stderr)
        recalled = self.run_store("get", "测试字段")
        self.assertEqual(recalled.stdout, original + "\n")

        second = self.run_store("set", "测试字段", "新值")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue((self.store_dir / "store.json.bak").is_file())
        backup = json.loads((self.store_dir / "store.json.bak").read_text(encoding="utf-8"))
        self.assertEqual(backup["items"]["测试字段"]["value"], original)

    def test_entries_and_json_output(self) -> None:
        added = self.run_store(
            "add",
            "经历",
            "--field",
            "机构=Example Org",
            "--field",
            "岗位=Example Role",
            "--json",
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        payload = json.loads(added.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["index"], 1)

        field = self.run_store("get", "经历", "--index", "1", "--field", "岗位")
        self.assertEqual(field.stdout, "Example Role\n")

        listing = self.run_store("list", "--json")
        listed = json.loads(listing.stdout)
        self.assertEqual(listed["result"], [{"key": "经历", "type": "entries", "count": 1}])

    def test_json_get_returns_non_sensitive_memory_reference(self) -> None:
        self.run_store("set", "学号", "12345")
        recalled = self.run_store("get", "学号", "--json")
        payload = json.loads(recalled.stdout)
        reference = payload["result"]["memory_reference"]

        self.assertEqual(reference["schema_version"], 1)
        self.assertEqual(reference["uri"], "memory://personal/profile/%E5%AD%A6%E5%8F%B7")
        self.assertEqual(reference["label"], "Personal · 学号")
        self.assertEqual(reference["path"], "personal/profile/exact.json")
        self.assertNotIn("12345", json.dumps(reference, ensure_ascii=False))
        self.assertFalse(reference["path"].startswith("/"))

    def test_transient_store_is_rejected_without_test_override(self) -> None:
        result = self.run_store("set", "字段", "值", allow_transient=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("临时目录", result.stderr)
        self.assertFalse((self.store_dir / "store.json").exists())

    def test_doctor_json_is_machine_readable(self) -> None:
        result = self.run_store("doctor", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "doctor")
        self.assertEqual(
            Path(payload["result"]["store_path"]),
            self.store_dir / "store.json",
        )

    def test_existing_v1_store_is_read_without_migration(self) -> None:
        legacy = {
            "schema_version": 1,
            "updated_at": 1721600000,
            "items": {"原字段": {"type": "single", "value": "逐字值（001）"}},
        }
        (self.store_dir / "store.json").write_text(
            json.dumps(legacy, ensure_ascii=False),
            encoding="utf-8",
        )
        result = self.run_store("get", "原字段")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "逐字值（001）\n")

    def test_empty_search_keeps_legacy_message(self) -> None:
        result = self.run_store("search", "不存在")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "[pmem] （空档案）\n")


if __name__ == "__main__":
    unittest.main()
