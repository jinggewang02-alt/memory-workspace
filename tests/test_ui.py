from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from memory_workspace import capture, home, profile, workspace
from memory_workspace.io import MemoryWorkspaceError
from memory_workspace.ui_server import create_server


class LocalReviewInboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.memory_home = Path(self.temporary.name) / "memory-home"
        self.capture_root = self.memory_home / "system" / "capture"
        self.learning_root = self.memory_home / "personal" / "learning"
        self.workspaces_root = self.memory_home / "workspaces"
        self.profile_path = self.memory_home / "personal" / "profile" / "exact.json"
        self.previous_environment = {
            key: os.environ.get(key)
            for key in (
                "MEMORY_HOME",
                "PMEM_ALLOW_TRANSIENT",
                "MWORK_ALLOW_TRANSIENT",
                "MEMORY_HOME_ALLOW_TRANSIENT",
                "MWORK_HISTORY_FILE",
                "MWORK_HISTORY_ADAPTER",
            )
        }
        os.environ["MEMORY_HOME"] = str(self.memory_home)
        os.environ["PMEM_ALLOW_TRANSIENT"] = "1"
        os.environ["MWORK_ALLOW_TRANSIENT"] = "1"
        os.environ["MEMORY_HOME_ALLOW_TRANSIENT"] = "1"
        os.environ.pop("MWORK_HISTORY_FILE", None)
        os.environ.pop("MWORK_HISTORY_ADAPTER", None)
        home.init_home(root=self.memory_home)
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=self.capture_root,
            learning_root=self.learning_root,
            memory_home=self.memory_home,
            token="synthetic-test-token",
            profile_path=self.profile_path,
            workspaces_root=self.workspaces_root,
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
        for key, value in self.previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
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

    def write_history(self) -> Path:
        path = Path(self.temporary.name) / "visible-history.jsonl"
        now = datetime.now(timezone.utc)
        rows = []
        for index, days_ago in enumerate((8, 3), start=1):
            occurred = (now - timedelta(days=days_ago)).isoformat()
            rows.append(
                {
                    "role": "user",
                    "conversation_id": f"history-{index}",
                    "message_id": f"history-{index}-1",
                    "occurred_at": occurred,
                    "content": "这部分我没理解，为什么这么设计？",
                }
            )
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        return path

    def test_static_page_and_overview_are_local_and_not_cached(self) -> None:
        with urlopen(self.base_url + "/", timeout=3) as response:
            html = response.read().decode()
            headers = dict(response.headers.items())
        self.assertIn("你的记忆，放在一个地方", html)
        self.assertIn("关于你的长期记忆", html)
        self.assertIn("以项目为中心的知识", html)
        self.assertIn("先理解你的提问方式", html)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        with urlopen(self.base_url + "/app.css", timeout=3) as response:
            css = response.read().decode()
        self.assertIn("[hidden]", css)

        status, payload, _ = self.get("/api/overview")
        self.assertEqual(status, 200)
        overview = payload["overview"]
        self.assertEqual(overview["counts"]["proposed"], 0)
        self.assertEqual(overview["pending_episode_count"], 0)
        self.assertTrue(overview["readiness"]["ready_to_use"])
        self.assertTrue(overview["readiness"]["history_learning"]["optional"])
        self.assertEqual(overview["readiness"]["workspaces"]["count"], 0)

        status, payload, _ = self.get("/api/home")
        self.assertEqual(status, 200)
        memory_home = payload["memory_home"]
        self.assertEqual(memory_home["schema_version"], 1)
        self.assertEqual(memory_home["home"]["status"], "ready")
        self.assertEqual(memory_home["personal"]["profile"]["count"], 0)
        self.assertTrue(memory_home["personal"]["profile"]["values_masked"])
        self.assertEqual(memory_home["workspaces"], [])

    def test_health_endpoint_only_claims_ready_after_server_is_listening(self) -> None:
        status, payload, headers = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["service"], "memory-workspace-home-ui")
        self.assertTrue(payload["loopback_only"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_home_view_combines_personal_and_workspace_memory_without_profile_values(self) -> None:
        profile.set_single(self.profile_path, "联系邮箱", "test@example.com")
        profile.add_entry(
            self.profile_path,
            "经历",
            {"机构": "Example Org", "角色": "Product"},
        )
        overview_path = self.memory_home / "personal" / "work" / "overview.md"
        overview_path.write_text(
            "# Work overview\n\n当前负责 Memory Home 的产品设计。\n",
            encoding="utf-8",
        )
        workspace.init_workspace(
            "memory-workspace",
            name="Memory Workspace",
            root=self.workspaces_root,
        )

        _, payload, _ = self.get("/api/home")
        memory_home = payload["memory_home"]
        serialized = json.dumps(memory_home, ensure_ascii=False)

        self.assertNotIn("test@example.com", serialized)
        self.assertNotIn("Example Org", serialized)
        self.assertEqual(memory_home["summary"]["personal_profile_items"], 2)
        self.assertEqual(memory_home["summary"]["personal_documents"], 1)
        self.assertEqual(memory_home["summary"]["workspaces"], 1)
        self.assertEqual(
            [item["key"] for item in memory_home["personal"]["profile"]["items"]],
            ["联系邮箱", "经历"],
        )
        self.assertIn(
            "当前负责 Memory Home",
            memory_home["personal"]["documents"][0]["content"],
        )
        self.assertEqual(
            memory_home["workspaces"][0]["workspace_id"],
            "memory-workspace",
        )
        self.assertNotIn("workspace_path", memory_home["workspaces"][0])

    def test_profile_reveal_and_single_update_require_token_and_read_back(self) -> None:
        profile.set_single(self.profile_path, "联系邮箱", "old@example.com")

        with self.assertRaises(HTTPError) as denied:
            self.post("/api/personal/profile/reveal", {"key": "联系邮箱"})
        self.assertEqual(denied.exception.code, 403)

        status, revealed = self.post(
            "/api/personal/profile/reveal",
            {"key": "联系邮箱"},
            token="synthetic-test-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(revealed["result"]["value"], "old@example.com")
        self.assertEqual(
            revealed["result"]["memory_reference"]["label"],
            "Personal · 联系邮箱",
        )

        status, updated = self.post(
            "/api/personal/profile/set",
            {"key": "联系邮箱", "value": "new@example.com"},
            token="synthetic-test-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["result"]["status"], "applied")
        self.assertEqual(updated["result"]["verification"]["status"], "passed")
        self.assertEqual(
            profile.get_item(self.profile_path, "联系邮箱")["value"],
            "new@example.com",
        )
        _, home_payload, _ = self.get("/api/home")
        self.assertNotIn(
            "new@example.com",
            json.dumps(home_payload["memory_home"], ensure_ascii=False),
        )

    def test_profile_ui_rejects_secrets_and_structured_overwrite(self) -> None:
        with self.assertRaises(HTTPError) as secret:
            self.post(
                "/api/personal/profile/set",
                {"key": "账号", "value": "password: dont-store-this"},
                token="synthetic-test-token",
            )
        self.assertEqual(secret.exception.code, 400)

        profile.add_entry(self.profile_path, "经历", {"机构": "Example Org"})
        with self.assertRaises(HTTPError) as structured:
            self.post(
                "/api/personal/profile/set",
                {"key": "经历", "value": "不要覆盖"},
                token="synthetic-test-token",
            )
        self.assertEqual(structured.exception.code, 400)

    def test_first_learning_report_round_trip(self) -> None:
        _, before, _ = self.get("/api/onboarding")
        self.assertEqual(before["onboarding"]["status"], "needs_history_source")

        history_path = self.write_history()
        status, started = self.post(
            "/api/onboarding/run",
            {
                "history_file": str(history_path),
                "adapter": "synthetic-ui-adapter",
                "days": 30,
            },
            token="synthetic-test-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(started["onboarding"]["status"], "awaiting_review")
        self.assertEqual(
            started["onboarding"]["habits"]["coverage"]["conversation_count"], 2
        )

        _, report, _ = self.get("/api/habits")
        self.assertTrue(report["markdown_path"].endswith("query-habits.md"))
        self.assertEqual(report["report"]["state"], "tentative")

        status, confirmed = self.post(
            "/api/onboarding/confirm",
            {},
            token="synthetic-test-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(confirmed["onboarding"]["status"], "completed")
        self.assertTrue(confirmed["onboarding"]["policy_active"])

    def test_invalid_optional_history_does_not_break_ready_ui(self) -> None:
        invalid = Path(self.temporary.name) / "invalid-history.txt"
        invalid.write_text("not jsonl\n", encoding="utf-8")
        os.environ["MWORK_HISTORY_FILE"] = str(invalid)
        os.environ["MWORK_HISTORY_ADAPTER"] = "synthetic-ui-adapter"

        _, overview, _ = self.get("/api/overview")
        self.assertTrue(overview["overview"]["readiness"]["ready_to_use"])
        self.assertEqual(
            overview["overview"]["readiness"]["history_learning"]["status"],
            "needs_attention",
        )
        _, onboarding_status, _ = self.get("/api/onboarding")
        self.assertEqual(onboarding_status["onboarding"]["status"], "needs_attention")

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
