#!/usr/bin/env python3
"""Validate integration metadata and shared JSON files for CI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FILES_TO_VALIDATE = [
    PROJECT_ROOT / "custom_components/tesla_fleet_telemetry/manifest.json",
    PROJECT_ROOT / "custom_components/tesla_fleet_telemetry/strings.json",
    PROJECT_ROOT / "custom_components/tesla_fleet_telemetry/icons.json",
    PROJECT_ROOT / "hacs.json",
]
MANIFEST_PATH = PROJECT_ROOT / "custom_components/tesla_fleet_telemetry/manifest.json"
STRINGS_PATH = PROJECT_ROOT / "custom_components/tesla_fleet_telemetry/strings.json"
REQUIRED_MANIFEST_FIELDS = [
    "domain",
    "name",
    "codeowners",
    "config_flow",
    "documentation",
    "issue_tracker",
]


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def validate_json_files(errors: list[str]) -> None:
    for path in FILES_TO_VALIDATE:
        try:
            load_json(path)
            print(f"OK {path.relative_to(PROJECT_ROOT)}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path.relative_to(PROJECT_ROOT)}: {exc}")


def validate_manifest(errors: list[str]) -> None:
    manifest = load_json(MANIFEST_PATH)

    for field in REQUIRED_MANIFEST_FIELDS:
        if field not in manifest:
            errors.append(f"manifest.json missing required field: {field}")


def validate_strings(errors: list[str]) -> None:
    strings = load_json(STRINGS_PATH)
    if "config" not in strings:
        errors.append("strings.json missing required config section")


def validate_version(expected_version: str, errors: list[str]) -> None:
    if not expected_version:
        return

    manifest = load_json(MANIFEST_PATH)
    manifest_version = str(manifest.get("version", "")).lstrip("v")
    normalized_expected = expected_version.lstrip("v")

    if manifest_version != normalized_expected:
        errors.append(
            f"Version mismatch: manifest={manifest_version}, expected={normalized_expected}"
        )
    else:
        print(f"OK Version validation passed: {manifest_version}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--expected-version",
        default="",
        help="Optional version that manifest.json must match.",
    )
    args = parser.parse_args()

    errors: list[str] = []

    validate_json_files(errors)
    validate_manifest(errors)
    validate_strings(errors)
    validate_version(args.expected_version, errors)

    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 1

    print("\nOK All manifest and JSON validations passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
