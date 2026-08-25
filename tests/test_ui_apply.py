from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from memory_workspace import capture, profile, workspace
from memory_workspace.ui_server import create_server


class LocalReviewInboxApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.capture_root = base / "capture"
        self.workspaces_root = base / "workspaces"
        self.profile_path = base / "profile" / "store.json"
        self.previous_workspace_override = os.environ.get("MWORK_ALLOW_TRANSIENT")
        self.previous_profile_override = os.environ.get("PMEM_ALLOW_TRANSIENT")
        os.environ["MWORK_ALLOW_TRANSIENT"] = "1"
        os.environ["PMEM_ALLOW_TRANSIENT"] = "1"
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=self.capture_root,
            token="apply-test-token",
            profile_path=self.profile_path,
            workspaces_root=self.workspaces_root,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        for key, previous in (
            ("MWORK_ALLOW_TRANSIENT", self.previous_workspace_override),
            ("PMEM_ALLOW_TRANSIENT", self.previous_profile_override),
        ):
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        self.temporary.cleanup()

    def post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        request = Request(
            self.base_url + path,
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Memory-Workspace-Token": "apply-test-token",
            },
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read())

    def get(self, path: str) -> dict[str, object]:
        with urlopen(self.base_url + path, timeout=3) as response:
            return json.loads(response.read())

    def seed(self, *, scope: str, content: str, key: str | None = None) -> str:
        event = capture.enqueue_event(content, root=self.capture_root)
        result = capture.resolve_event(
            event["event_id"],
            decision=scope,
            reason="Synthetic UI application test.",
            content=content,
            confidence=0.93,
            workspace_id="memory-workspace" if scope == "project" else None,
            profile_key=key,
            kind="decision" if scope == "project" else "fact",
            sensitivity="sensitive" if scope == "profile" else "normal",
            root=self.capture_root,
        )
        return str(result["candidate_id"])

    def test_profile_approve_to_apply_round_trip(self) -> None:
        candidate_id = self.seed(
            scope="profile", content="联系邮箱 test@example.com", key="联系邮箱"
        )
        self.post(
            f"/api/candidates/{candidate_id}/approve",
            {"target_ref": "profile:联系邮箱"},
        )
        applied = self.post(f"/api/candidates/{candidate_id}/apply", {})["result"]
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(
            profile.get_item(self.profile_path, "联系邮箱")["value"],
            "联系邮箱 test@example.com",
        )
        detail = self.get(f"/api/candidates/{candidate_id}")["detail"]
        self.assertIsNone(detail["review"]["approved_content"])
        self.assertEqual(detail["application"]["writer"]["kind"], "profile_single")

    def test_project_approve_to_operation_apply_round_trip(self) -> None:
        workspace.init_workspace(
            "memory-workspace", name="Memory Workspace", root=self.workspaces_root
        )
        candidate_id = self.seed(
            scope="project",
            content="环境准备应基于能力探测，不写死平台名称。",
        )
        self.post(
            f"/api/candidates/{candidate_id}/approve",
            {
                "target_ref": (
                    "workspace:memory-workspace/"
                    "wiki/projects/memory-workspace/decisions.md"
                )
            },
        )
        applied = self.post(f"/api/candidates/{candidate_id}/apply", {})["result"]
        receipt = applied["application"]
        self.assertEqual(receipt["writer"]["kind"], "workspace_operation")
        self.assertTrue(receipt["writer"]["operation_id"].startswith("op_"))
        page = (
            self.workspaces_root
            / "memory-workspace/wiki/projects/memory-workspace/decisions.md"
        ).read_text(encoding="utf-8")
        self.assertIn(candidate_id, page)


if __name__ == "__main__":
    unittest.main()
