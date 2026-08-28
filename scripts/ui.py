#!/usr/bin/env python3
"""Launch the loopback-only Memory Home UI."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_workspace.io import MemoryWorkspaceError  # noqa: E402
from memory_workspace.ui_server import create_server  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the local Memory Home UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8741)
    parser.add_argument("--no-open", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        server = create_server(host=args.host, port=args.port)
    except (MemoryWorkspaceError, OSError) as exc:
        print(f"[memory-workspace] 无法启动本地 UI：{exc}", file=sys.stderr)
        return 1
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"[memory-workspace] Memory Home 本地管理台已启动：{url}", flush=True)
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
