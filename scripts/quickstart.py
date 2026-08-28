#!/usr/bin/env python3
"""Prepare Memory Home and launch its loopback-only local UI."""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_workspace.io import MemoryWorkspaceError  # noqa: E402
from memory_workspace.quickstart import exit_code, prepare  # noqa: E402
from memory_workspace.ui_server import create_server  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Initialize Memory Home and launch its local management UI"
    )
    parser.add_argument(
        "--home",
        type=Path,
        help="durable Memory Home path (default: ~/.memory-home)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8741)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="initialize and print a machine-readable receipt without starting the UI",
    )
    return parser


def _failure(exc: Exception) -> dict[str, object]:
    return {
        "schema_version": 2,
        "command": "quickstart",
        "status": "SETUP_FAILED",
        "ready": False,
        "initialized": False,
        "blocker": str(exc),
        "next_actions": ["Resolve the reported local path or permission issue, then rerun."],
    }


def _render_text(result: dict[str, object]) -> None:
    print("[memory-workspace] status: {0}".format(result["status"]))
    if not result.get("ready"):
        if result.get("blocker"):
            print("[memory-workspace] blocker: {0}".format(result["blocker"]))
        return
    initialized = result["initialized"]
    assert isinstance(initialized, dict)
    created = "created" if initialized["created"] else "already ready"
    print(
        "[memory-workspace] Memory Home {0}: {1}".format(
            created, initialized["home_path"]
        )
    )
    print("[memory-workspace] 已准备好。你可以选择：")
    menu = result.get("menu")
    assert isinstance(menu, list)
    for index, item in enumerate(menu, 1):
        assert isinstance(item, dict)
        recommended = "（推荐）" if item.get("recommended") else ""
        print(
            "  {0}. {1}{2} — {3}".format(
                index,
                item["label"],
                recommended,
                item["description"],
            )
        )
    learning = result["history_learning"]
    assert isinstance(learning, dict)
    if learning["status"] == "awaiting_review":
        print("[memory-workspace] 有一份 Query 习惯草稿待审阅。")
    elif learning["status"] == "needs_attention":
        print(
            "[memory-workspace] 历史学习配置需要检查；基础能力仍可使用：{0}".format(
                learning.get("error") or "未知历史来源错误"
            )
        )


def main() -> int:
    args = build_parser().parse_args()
    if args.home is not None:
        os.environ["MEMORY_HOME"] = str(args.home.expanduser().resolve(strict=False))
    try:
        result = prepare(skill_root=ROOT)
    except (MemoryWorkspaceError, OSError, ValueError) as exc:
        result = _failure(exc)

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False))
        return exit_code(result)

    _render_text(result)
    if not result.get("ready"):
        return exit_code(result)

    paths = result["paths"]
    assert isinstance(paths, dict)
    try:
        server = create_server(
            host=args.host,
            port=args.port,
            root=Path(str(paths["capture"])),
            learning_root=Path(str(paths["learning"])),
            memory_home=Path(str(paths["memory_home"])),
            profile_path=Path(str(paths["profile"])),
            workspaces_root=Path(str(paths["workspaces"])),
        )
    except (MemoryWorkspaceError, OSError) as exc:
        print("[memory-workspace] 无法启动本地 UI：{0}".format(exc), file=sys.stderr)
        return 1

    host, port = server.server_address[:2]
    url = "http://{0}:{1}/".format(host, port)
    print("[memory-workspace] Memory Home 本地管理台已启动：{0}".format(url), flush=True)
    print("[memory-workspace] 按 Ctrl+C 停止；数据不会离开本机。", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[memory-workspace] 已停止。", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
