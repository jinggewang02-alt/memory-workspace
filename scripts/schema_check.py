#!/usr/bin/env python3
"""Validate Memory Workspace JSON documents without third-party packages."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_workspace.schema import validate_file  # noqa: E402


SCHEMA_DIR = ROOT / "schemas"
FIXTURE_DIR = ROOT / "examples" / "schema"


def fixture_schema(path: Path) -> Path:
    stem = path.name.split(".", 1)[0]
    return SCHEMA_DIR / f"{stem}.schema.json"


def check_fixtures() -> int:
    failures: list[str] = []
    fixtures = sorted(FIXTURE_DIR.glob("*.json"))
    for fixture in fixtures:
        schema_path = fixture_schema(fixture)
        if not schema_path.is_file():
            failures.append(f"{fixture.name}: missing schema {schema_path.name}")
            continue
        errors = validate_file(schema_path, fixture)
        should_pass = ".valid." in fixture.name
        if should_pass and errors:
            failures.append(f"{fixture.name}: expected valid; " + "; ".join(errors))
        if not should_pass and not errors:
            failures.append(f"{fixture.name}: expected invalid but validation passed")

    print(f"Checked {len(fixtures)} schema fixture(s).")
    for failure in failures:
        print(f"ERROR: {failure}")
    if failures:
        print(f"FAILED: {len(failures)} fixture error(s).")
        return 1
    print("OK: all schema fixtures matched their expected result.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Memory Workspace JSON documents")
    parser.add_argument("schema", nargs="?", type=Path)
    parser.add_argument("document", nargs="?", type=Path)
    parser.add_argument("--fixtures", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.fixtures:
        return check_fixtures()
    if args.schema is None or args.document is None:
        print("schema and document are required unless --fixtures is used", file=sys.stderr)
        return 2
    errors = validate_file(args.schema, args.document)
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print("OK: document is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
