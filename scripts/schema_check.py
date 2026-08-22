#!/usr/bin/env python3
"""Validate Memory Workspace JSON documents without third-party packages.

The bundled schemas are standard JSON Schema Draft 2020-12 documents. This
validator intentionally implements only the keywords used by those schemas so
the Skill stays dependency-free. Full JSON Schema implementations can validate
the same files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
FIXTURE_DIR = ROOT / "examples" / "schema"


def json_type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"Only local JSON Schema refs are supported: {ref}")
    current: Any = root_schema
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        current = current[token]
    if not isinstance(current, dict):
        raise ValueError(f"Schema ref does not resolve to an object: {ref}")
    return current


def is_datetime(value: str) -> bool:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate(
    value: Any,
    schema: dict[str, Any],
    *,
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
) -> list[str]:
    root = root_schema or schema
    if "$ref" in schema:
        return validate(value, resolve_ref(root, schema["$ref"]), root_schema=root, path=path)

    if "oneOf" in schema:
        variants = [validate(value, candidate, root_schema=root, path=path) for candidate in schema["oneOf"]]
        matches = sum(not errors for errors in variants)
        if matches != 1:
            return [f"{path}: expected exactly one schema variant to match, got {matches}"]
        return []

    errors: list[str] = []
    expected = schema.get("type")
    if expected is not None:
        expected_types = expected if isinstance(expected, list) else [expected]
        if not any(json_type_matches(value, item) for item in expected_types):
            return [f"{path}: expected type {expected_types}, got {type(value).__name__}"]

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']!r}")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than minLength")
        pattern = schema.get("pattern")
        if pattern and re.search(pattern, value) is None:
            errors.append(f"{path}: string does not match {pattern!r}")
        if schema.get("format") == "date-time" and not is_datetime(value):
            errors.append(f"{path}: expected an ISO 8601 date-time with timezone")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: array is shorter than minItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate(item, item_schema, root_schema=root, path=f"{path}[{index}]"))

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")

        properties = schema.get("properties", {})
        pattern_properties = schema.get("patternProperties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                errors.extend(validate(item, properties[key], root_schema=root, path=f"{path}.{key}"))
                continue
            matched = False
            for pattern, candidate in pattern_properties.items():
                if re.search(pattern, key):
                    matched = True
                    errors.extend(validate(item, candidate, root_schema=root, path=f"{path}.{key}"))
            if matched:
                continue
            if additional is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(additional, dict):
                errors.extend(validate(item, additional, root_schema=root, path=f"{path}.{key}"))

    return errors


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def validate_file(schema_path: Path, document_path: Path) -> list[str]:
    schema = load_json(schema_path)
    document = load_json(document_path)
    return validate(document, schema)


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
