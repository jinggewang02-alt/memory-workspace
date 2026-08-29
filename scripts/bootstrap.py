#!/usr/bin/env python3
"""Probe an agent runtime before Memory Home reads or writes local data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_workspace.bootstrap import build_report, exit_code  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(description="Memory Home capability probe")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def render_text(report):
    print("status: {0}".format(report["status"]))
    print("runtime_mode: {0}".format(report["runtime_mode"]))
    print("python: {0}".format(report["capabilities"]["python"]["version"]))
    for name, item in report["capabilities"]["persistent_storage"].items():
        print("{0}_path: {1}".format(name, item["path"]))
        print("{0}_writable: {1}".format(name, "yes" if item["filesystem_writable"] else "no"))
        print("{0}_transient: {1}".format(name, "yes" if item["transient_risk"] else "no"))
    for action in report["next_actions"]:
        print("next_action: {0}".format(action))


def main():
    args = build_parser().parse_args()
    report = build_report(ROOT)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        render_text(report)
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
