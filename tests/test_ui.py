from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from memory_workspace import capture
from memory_workspace.io import MemoryWorkspaceError
from memory_workspace.ui_server import create_server


class LocalReviewInboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.capture_root = Path(self.temporary.name) / "capture"
        self.previous_override = os.environ.get("MWORK_ALLOW_TRANSIENT")
        os.environ["MWORK_ALLOW_TRANSIENT"] = "1"
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=self.capture_root,
            token="synthetic-test-token",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        self.port = port

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        if self.previous_override is None:
            os.environ.pop("MWORK_ALLOW_TRANSIENT", None)
        else:
            os.environ["MWORK_ALLOW_TRANSIENT"] = self.previous_override
        self.temporary.cleanup()

    def get(self, path: str) -> tuple[int, dict[str, object], dict[str, str]]:
        with urlopen(self.base_url + path, timeout=3) as response:
            payload = json.loads(response.read())
            return response.status, payload, dict(response.headers.items())

    def post(
        self, path: str, payload: dict[str, object], *, token: str | None = None
    ) -> tuple[int, dict[str, object]]:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Memory-Workspace-Token"] = token
        request = Request(
            self.base_url + path,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    def seed_candidate(self, *, sensitive: bool = False) -> str:
        event = capture.enqueue_event(
            "我希望所有 Agent 都通过能力探测准备环境，联系邮箱 test@example.com",
            conversation_id="conv-ui-test",
            message_id="message-1",
            workspace_id="memory-workspace",
            source_agent="synthetic-agent",
            root=self.capture_root,
        )
        resolution = capture.resolve_event(
            event["event_id"],
            decision="profile" if sensitive else "project",
            reason="这是跨会话仍有价值的稳定约束。",
            content=(
                "联系邮箱 test@example.com"
                if sensitive
                else "Agent 应根据能力探测结果准备运行环境，不写死平台名称。"
            ),
            confidence=0.91,
            sensitivity="sensitive" if sensitive else "normal",
            profile_key="联系邮箱" if sensitive else None,
            workspace_id=None if sensitive else "memory-workspace",
            kind="fact" if sensitive else "decision",
            policy_version="policy_synthetic",
            policy_rule="learning_synthesis",
            trigger_phase="synthesize",
            root=self.capture_root,
        )
        return str(resolution["candidate_id"])

    def test_static_page_and_overview_are_local_and_not_cached(self) -> None:
        with urlopen(self.base_url + "/", timeout=3) as response:
            html = response.read().decode()
            headers = dict(response.headers.items())
        self.assertIn("让重要的内容留下", html)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

        status, payload, _ = self.get("/api/overview")
        self.assertEqual(status, 200)
        overview = payload["overview"]
        self.assertEqual(overview["counts"]["proposed"], 0)
        self.assertEqual(overview["pending_episode_count"], 0)

    def test_bad_host_and_non_loopback_bind_are_rejected(self) -> None:
        with self.assertRaises(MemoryWorkspaceError):
            create_server(host="0.0.0.0", port=0, root=self.capture_root)

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.putrequest("GET", "/api/session", skip_host=True)
        connection.putheader("Host", "attacker.example")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        connection.close()

    def test_candidate_detail_redacts_evidence_and_reveal_requires_token(self) -> None:
        candidate_id = self.seed_candidate(sensitive=True)
        _, listing, _ = self.get("/api/candidates?status=proposed")
        self.assertNotIn("test@example.com", listing["candidates"][0]["content_preview"])

        _, detail, _ = self.get(f"/api/candidates/{candidate_id}")
        self.assertTrue(detail["detail"]["content_redacted"])
        self.assertIsNone(detail["detail"]["candidate"]["content"])
        self.assertEqual(
            detail["detail"]["evidence"][0]["message_preview"],
            "敏感证据 · 内容已隐藏",
        )

        with self.assertRaises(HTTPError) as denied:
            self.post(f"/api/candidates/{candidate_id}/reveal", {})
        self.assertEqual(denied.exception.code, 403)

        status, revealed = self.post(
            f"/api/candidates/{candidate_id}/reveal",
            {},
            token="synthetic-test-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(revealed["content"], "联系邮箱 test@example.com")

    def test_reject_uses_existing_review_and_feedback_contract(self) -> None:
        candidate_id = self.seed_candidate()
        status, payload = self.post(
            f"/api/candidates/{candidate_id}/reject",
            {"feedback_reason": "not_durable", "suppress_similar": True},
            token="synthetic-test-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["status"], "rejected")
        self.assertEqual(len(payload["result"]["feedback_paths"]), 2)

        bundle = capture.show_candidate(candidate_id, root=self.capture_root)
        self.assertEqual(bundle["status"], "rejected")
        _, listing, _ = self.get("/api/candidates?status=rejected")
        self.assertEqual(listing["candidates"][0]["candidate_id"], candidate_id)

    def test_approve_uses_exact_target_and_keeps_sensitive_content_masked(self) -> None:
        candidate_id = self.seed_candidate(sensitive=True)
        status, payload = self.post(
            f"/api/candidates/{candidate_id}/approve",
            {
                "target_ref": "profile:联系邮箱",
                "edited_content": None,
                "feedback_reason": "approved_via_ui_test",
            },
            token="synthetic-test-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["status"], "approved")

        _, detail, _ = self.get(f"/api/candidates/{candidate_id}")
        self.assertEqual(detail["detail"]["status"], "approved")
        self.assertIsNone(detail["detail"]["review"]["approved_content"])
        self.assertEqual(
            capture.show_candidate(candidate_id, root=self.capture_root)["review"][
                "approved_content"
            ],
            "联系邮箱 test@example.com",
        )


if __name__ == "__main__":
    unittest.main()
