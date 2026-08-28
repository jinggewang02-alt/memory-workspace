"""Loopback-only HTTP API and static server for the local review inbox."""

from __future__ import annotations

import copy
import json
import re
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import capture, episodes, habits, home, onboarding, policy, workspace, writer
from .io import MemoryWorkspaceError


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = PACKAGE_ROOT / "ui"
ALLOWED_BIND_HOSTS = {"127.0.0.1", "localhost"}
ALLOWED_STATUS = {"proposed", "approved", "rejected", "applied", "all"}
MAX_BODY_BYTES = 64 * 1024
CANDIDATE_ACTION_RE = re.compile(
    r"^/api/candidates/(?P<candidate_id>cand_[A-Za-z0-9_-]+)/(?P<action>approve|reject|reveal|apply)$"
)
CANDIDATE_DETAIL_RE = re.compile(
    r"^/api/candidates/(?P<candidate_id>cand_[A-Za-z0-9_-]+)$"
)
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def _validate_bind_host(host: str) -> str:
    normalized = host.strip().lower()
    if normalized not in ALLOWED_BIND_HOSTS:
        raise MemoryWorkspaceError(
            "本地 UI 只能监听 localhost 或 127.0.0.1；拒绝暴露到局域网或公网。"
        )
    return normalized


def _host_header_is_loopback(value: str | None) -> bool:
    if not value:
        return False
    host = value.strip().lower()
    if host.startswith("["):
        return host == "[::1]" or host.startswith("[::1]:")
    name = host.rsplit(":", 1)[0] if ":" in host else host
    return name in ALLOWED_BIND_HOSTS


def _learning_roots(
    root: Path | None, learning_root: Path | None
) -> tuple[Path | None, Path | None]:
    if learning_root is None:
        return root, root
    return learning_root.parent, learning_root


def _readiness(
    *,
    root: Path | None,
    learning_root: Path | None,
    memory_home: Path | None,
    workspaces_root: Path | None,
) -> dict[str, Any]:
    home_check = (
        home.check_home(root=memory_home)
        if memory_home is not None
        else (home.check_home() if root is None else None)
    )
    capture_check = capture.doctor(root=root)
    workspace_check = (
        workspace.doctor(root=workspaces_root)
        if workspaces_root is not None or root is None
        else None
    )
    workspace_items = (
        workspace.list_workspaces(root=workspaces_root)
        if workspaces_root is not None or root is None
        else []
    )
    learning = _safe_onboarding_status(root=root, learning_root=learning_root)
    home_ready = (
        home_check["status"] == "OK"
        if home_check is not None
        else bool(capture_check["filesystem_writable"])
    )
    ready = (
        home_ready
        and bool(capture_check["filesystem_writable"])
        and (
            workspace_check is None
            or bool(workspace_check["filesystem_writable"])
        )
    )
    return {
        "status": "ready" if ready else "needs_setup",
        "ready_to_use": ready,
        "memory": {
            "status": "ready" if ready else "needs_setup",
            "path": (
                home_check["home_path"]
                if home_check is not None
                else capture_check["capture_root"]
            ),
        },
        "history_learning": {
            "status": learning["status"],
            "optional": True,
        },
        "workspaces": {
            "count": len(workspace_items),
            "optional_until_first_project": True,
        },
    }


def _safe_onboarding_status(
    *, root: Path | None, learning_root: Path | None
) -> dict[str, Any]:
    habits_root, policy_root = _learning_roots(root, learning_root)
    try:
        return onboarding.get_status(
            root=root,
            habits_root=habits_root,
            policy_root=policy_root,
        )
    except MemoryWorkspaceError as exc:
        return {
            "status": "needs_attention",
            "run": None,
            "habits": None,
            "policy_active": False,
            "can_confirm": False,
            "history_source": None,
            "error": str(exc),
        }


def _overview(
    root: Path | None,
    *,
    learning_root: Path | None,
    memory_home: Path | None,
    workspaces_root: Path | None,
) -> dict[str, Any]:
    candidates = capture.list_candidates(status="all", limit=500, root=root)
    counts = {name: 0 for name in ("proposed", "approved", "rejected", "applied")}
    for item in candidates:
        counts[item["status"]] += 1
    pending = episodes.list_episodes(status="pending", limit=500, root=root)
    policies = policy.list_policies(
        root=learning_root if learning_root is not None else root
    )
    active_policy = next((item for item in policies if item["active"]), None)
    return {
        "counts": counts,
        "pending_episode_count": len(pending),
        "active_policy": active_policy,
        "capture": capture.doctor(root=root),
        "readiness": _readiness(
            root=root,
            learning_root=learning_root,
            memory_home=memory_home,
            workspaces_root=workspaces_root,
        ),
    }


def _candidate_detail(candidate_id: str, root: Path | None) -> dict[str, Any]:
    bundle = copy.deepcopy(capture.show_candidate(candidate_id, root=root))
    candidate = bundle["candidate"]
    sensitive = candidate.get("sensitivity") == "sensitive"
    if sensitive:
        candidate["content"] = None
        if bundle.get("review") is not None:
            bundle["review"]["approved_content"] = None
    evidence = []
    for event_id in candidate.get("evidence_event_ids", [candidate["trigger_event_id"]]):
        event_bundle = capture.show_event(event_id, root=root)
        event = event_bundle["event"]
        evidence.append(
            {
                "event_id": event_id,
                "occurred_at": event.get("occurred_at", event["created_at"]),
                "source_kind": event.get("source_kind", "live"),
                "message_preview": (
                    "敏感证据 · 内容已隐藏"
                    if sensitive
                    else capture._safe_preview(event["payload"]["user_message"], limit=220)
                ),
            }
        )
    bundle["content_redacted"] = sensitive
    bundle["evidence"] = evidence
    return bundle


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    raw_length = handler.headers.get("Content-Length", "")
    try:
        length = int(raw_length)
    except ValueError as exc:
        raise MemoryWorkspaceError("Content-Length 无效。") from exc
    if length < 0 or length > MAX_BODY_BYTES:
        raise MemoryWorkspaceError("请求内容过大。")
    if handler.headers.get_content_type() != "application/json":
        raise MemoryWorkspaceError("请求必须使用 application/json。")
    try:
        value = json.loads(handler.rfile.read(length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MemoryWorkspaceError("请求 JSON 无效。") from exc
    if not isinstance(value, dict):
        raise MemoryWorkspaceError("请求 JSON 必须是对象。")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MemoryWorkspaceError(f"{key} 必须是字符串。")
    return value


def build_handler(
    *,
    root: Path | None,
    learning_root: Path | None,
    memory_home: Path | None,
    token: str,
    profile_path: Path | None,
    workspaces_root: Path | None,
) -> type[BaseHTTPRequestHandler]:
    configured_habits_root, configured_policy_root = _learning_roots(
        root, learning_root
    )

    class ReviewInboxHandler(BaseHTTPRequestHandler):
        server_version = "MemoryWorkspaceUI/0.9"

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
                "form-action 'self'; frame-ancestors 'none'",
            )
            super().end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_bytes(
            self, body: bytes, *, content_type: str, status: HTTPStatus = HTTPStatus.OK
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(
            self, payload: dict[str, Any] | list[Any], status: HTTPStatus = HTTPStatus.OK
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send_bytes(body, content_type="application/json; charset=utf-8", status=status)

        def _reject_bad_host(self) -> bool:
            if _host_header_is_loopback(self.headers.get("Host")):
                return False
            self._send_json(
                {"ok": False, "error": "仅接受本机 Host。"}, HTTPStatus.FORBIDDEN
            )
            return True

        def _mutation_authorized(self) -> bool:
            supplied = self.headers.get("X-Memory-Workspace-Token", "")
            if secrets.compare_digest(supplied, token):
                return True
            self._send_json(
                {"ok": False, "error": "会话已失效，请刷新页面。"},
                HTTPStatus.FORBIDDEN,
            )
            return False

        def do_GET(self) -> None:  # noqa: N802
            if self._reject_bad_host():
                return
            target = urlsplit(self.path)
            try:
                if target.path in STATIC_FILES:
                    filename, content_type = STATIC_FILES[target.path]
                    self._send_bytes((UI_ROOT / filename).read_bytes(), content_type=content_type)
                    return
                if target.path == "/api/session":
                    self._send_json({"ok": True, "token": token})
                    return
                if target.path == "/api/overview":
                    self._send_json(
                        {
                            "ok": True,
                            "overview": _overview(
                                root,
                                learning_root=learning_root,
                                memory_home=memory_home,
                                workspaces_root=workspaces_root,
                            ),
                        }
                    )
                    return
                if target.path == "/api/onboarding":
                    self._send_json(
                        {
                            "ok": True,
                            "onboarding": _safe_onboarding_status(
                                root=root, learning_root=learning_root
                            ),
                        }
                    )
                    return
                if target.path == "/api/habits":
                    report = habits.load_report(root=configured_habits_root)
                    self._send_json(
                        {
                            "ok": True,
                            "report": report,
                            "markdown_path": (
                                str(habits.report_markdown_path(configured_habits_root))
                                if report is not None
                                else None
                            ),
                        }
                    )
                    return
                if target.path == "/api/candidates":
                    status = parse_qs(target.query).get("status", ["proposed"])[0]
                    if status not in ALLOWED_STATUS:
                        raise MemoryWorkspaceError("未知的候选状态。")
                    items = capture.list_candidates(status=status, limit=500, root=root)
                    for item in items:
                        if item["sensitivity"] == "sensitive":
                            item["content_preview"] = "敏感内容 · 点击后查看"
                    self._send_json({"ok": True, "candidates": list(reversed(items))})
                    return
                match = CANDIDATE_DETAIL_RE.fullmatch(target.path)
                if match:
                    detail = _candidate_detail(match.group("candidate_id"), root)
                    self._send_json({"ok": True, "detail": detail})
                    return
                self._send_json({"ok": False, "error": "未找到页面。"}, HTTPStatus.NOT_FOUND)
            except FileNotFoundError:
                self._send_json({"ok": False, "error": "UI 静态文件缺失。"}, HTTPStatus.NOT_FOUND)
            except MemoryWorkspaceError as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def do_POST(self) -> None:  # noqa: N802
            if self._reject_bad_host() or not self._mutation_authorized():
                return
            target = urlsplit(self.path)
            if target.path in {"/api/onboarding/run", "/api/onboarding/confirm"}:
                try:
                    payload = _read_json(self)
                    if target.path == "/api/onboarding/confirm":
                        result = onboarding.confirm_first_learning(
                            root=root,
                            habits_root=configured_habits_root,
                            policy_root=configured_policy_root,
                        )
                    else:
                        history_file_value = _optional_string(payload, "history_file")
                        adapter = _optional_string(payload, "adapter")
                        days = payload.get("days", 30)
                        if not isinstance(days, int) or isinstance(days, bool):
                            raise MemoryWorkspaceError("days 必须是整数。")
                        result = onboarding.run_first_learning(
                            root=root,
                            habits_root=configured_habits_root,
                            policy_root=configured_policy_root,
                            history_file=(
                                Path(history_file_value)
                                if history_file_value is not None
                                else None
                            ),
                            adapter=adapter,
                            days=days,
                        )
                    self._send_json({"ok": True, "onboarding": result})
                except MemoryWorkspaceError as exc:
                    self._send_json(
                        {"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST
                    )
                return
            match = CANDIDATE_ACTION_RE.fullmatch(target.path)
            if not match:
                self._send_json({"ok": False, "error": "未找到操作。"}, HTTPStatus.NOT_FOUND)
                return
            try:
                payload = _read_json(self)
                candidate_id = match.group("candidate_id")
                action = match.group("action")
                if action == "reveal":
                    bundle = capture.show_candidate(candidate_id, root=root)
                    self._send_json(
                        {"ok": True, "content": bundle["candidate"]["content"]}
                    )
                    return
                if action == "apply":
                    result = writer.apply_candidate(
                        candidate_id,
                        actor="owner_via_local_ui",
                        capture_root=root,
                        profile_path=profile_path,
                        workspaces_root=workspaces_root,
                    )
                    self._send_json({"ok": True, "result": result})
                    return
                if action == "approve":
                    result = capture.decide_candidate(
                        candidate_id,
                        decision="approved",
                        actor="owner_via_local_ui",
                        target_ref=_optional_string(payload, "target_ref"),
                        edited_content=_optional_string(payload, "edited_content"),
                        feedback_reason=_optional_string(payload, "feedback_reason"),
                        root=root,
                        workspaces_root=workspaces_root,
                    )
                else:
                    suppress_similar = payload.get("suppress_similar", False)
                    if not isinstance(suppress_similar, bool):
                        raise MemoryWorkspaceError("suppress_similar 必须是布尔值。")
                    result = capture.decide_candidate(
                        candidate_id,
                        decision="rejected",
                        actor="owner_via_local_ui",
                        feedback_reason=_optional_string(payload, "feedback_reason"),
                        suppress_similar=suppress_similar,
                        root=root,
                    )
                self._send_json({"ok": True, "result": result})
            except MemoryWorkspaceError as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    return ReviewInboxHandler


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8741,
    root: Path | None = None,
    learning_root: Path | None = None,
    memory_home: Path | None = None,
    token: str | None = None,
    profile_path: Path | None = None,
    workspaces_root: Path | None = None,
) -> ThreadingHTTPServer:
    bind_host = _validate_bind_host(host)
    if port < 0 or port > 65535:
        raise MemoryWorkspaceError("端口必须在 0–65535 之间。")
    session_token = token or secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(
        (bind_host, port),
        build_handler(
            root=root,
            learning_root=learning_root,
            memory_home=memory_home,
            token=session_token,
            profile_path=profile_path,
            workspaces_root=workspaces_root,
        ),
    )
    server.daemon_threads = True
    return server
