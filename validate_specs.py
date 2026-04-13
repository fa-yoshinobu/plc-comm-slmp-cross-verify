#!/usr/bin/env python3
"""Validate checked-in cross-verify JSON spec files."""

from __future__ import annotations

import json
import os
import sys


ROOT = os.path.dirname(os.path.abspath(__file__))
DEVICE_CONSISTENCY_DIR = os.path.join(ROOT, "specs", "device_consistency")
UNSUPPORTED_PATHS_FILE = os.path.join(ROOT, "specs", "shared", "unsupported_path_vectors.json")


def fail(message: str):
    raise ValueError(message)


def load_json(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def expect_type(value, expected_type, path: str):
    if not isinstance(value, expected_type):
        fail(f"{path}: expected {expected_type.__name__}, got {type(value).__name__}")


def expect_keys(value: dict, required_keys: list[str], path: str):
    missing = [key for key in required_keys if key not in value]
    if missing:
        fail(f"{path}: missing keys {', '.join(missing)}")


def validate_string_list(items, path: str):
    expect_type(items, list, path)
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item:
            fail(f"{path}[{index}]: expected non-empty string")


def validate_device_consistency_profile(path: str):
    payload = load_json(path)
    expect_type(payload, dict, path)
    expect_keys(payload, ["name", "defaults", "groups"], path)
    if not isinstance(payload["name"], str) or not payload["name"]:
        fail(f"{path}.name: expected non-empty string")

    defaults = payload["defaults"]
    expect_type(defaults, dict, f"{path}.defaults")
    expect_keys(
        defaults,
        ["frame", "series", "retries", "retry_delay_ms", "command_delay_ms", "settle_delay_ms", "restore_after"],
        f"{path}.defaults",
    )
    if defaults["frame"] not in {"3e", "4e"}:
        fail(f"{path}.defaults.frame: expected 3e or 4e")
    if defaults["series"] not in {"ql", "iqr"}:
        fail(f"{path}.defaults.series: expected ql or iqr")
    for key in ("retries", "retry_delay_ms", "command_delay_ms", "settle_delay_ms"):
        if not isinstance(defaults[key], int) or defaults[key] < 0:
            fail(f"{path}.defaults.{key}: expected non-negative integer")
    if not isinstance(defaults["restore_after"], bool):
        fail(f"{path}.defaults.restore_after: expected boolean")

    groups = payload["groups"]
    expect_type(groups, list, f"{path}.groups")
    if not groups:
        fail(f"{path}.groups: expected at least one group")

    seen_devices = set()
    for group_index, group in enumerate(groups):
        group_path = f"{path}.groups[{group_index}]"
        expect_type(group, dict, group_path)
        expect_keys(group, ["kind", "read_paths", "write_paths"], group_path)
        if "addresses" not in group and "items" not in group:
            fail(f"{group_path}: expected either addresses or items")
        if "addresses" in group:
            validate_string_list(group["addresses"], f"{group_path}.addresses")
        if "items" in group:
            expect_type(group["items"], list, f"{group_path}.items")
            for item_index, item in enumerate(group["items"]):
                item_path = f"{group_path}.items[{item_index}]"
                expect_type(item, dict, item_path)
                expect_keys(item, ["address"], item_path)
                if not isinstance(item["address"], str) or not item["address"]:
                    fail(f"{item_path}.address: expected non-empty string")
        validate_string_list(group["read_paths"], f"{group_path}.read_paths")
        validate_string_list(group["write_paths"], f"{group_path}.write_paths")
        if "volatile_addresses" in group:
            validate_string_list(group["volatile_addresses"], f"{group_path}.volatile_addresses")
        if "restore_path" in group and group["restore_path"] is not None and not isinstance(group["restore_path"], str):
            fail(f"{group_path}.restore_path: expected string or null")
        for key in ("restore_policy", "volatile_restore_policy"):
            if key in group and group[key] not in {"strict", "best-effort", "skip"}:
                fail(f"{group_path}.{key}: expected strict, best-effort, or skip")
        if "read_only" in group and not isinstance(group["read_only"], bool):
            fail(f"{group_path}.read_only: expected boolean")

        raw_devices = []
        raw_devices.extend(group.get("addresses") or [])
        raw_devices.extend(item["address"] for item in group.get("items") or [])
        for device in raw_devices:
            if device in seen_devices:
                fail(f"{group_path}: duplicate device '{device}'")
            seen_devices.add(device)


def validate_unsupported_paths(path: str):
    payload = load_json(path)
    expect_type(payload, dict, path)
    expect_keys(payload, ["cases"], path)
    cases = payload["cases"]
    expect_type(cases, list, f"{path}.cases")
    if not cases:
        fail(f"{path}.cases: expected at least one case")
    seen_names = set()
    for index, case in enumerate(cases):
        case_path = f"{path}.cases[{index}]"
        expect_type(case, dict, case_path)
        expect_keys(
            case,
            ["name", "device", "command_label", "description", "command", "address", "extra", "flags", "expect_error", "clients"],
            case_path,
        )
        for key in ("name", "device", "command_label", "description", "command", "clients"):
            if not isinstance(case[key], str) or not case[key]:
                fail(f"{case_path}.{key}: expected non-empty string")
        if case["name"] in seen_names:
            fail(f"{case_path}.name: duplicate case name '{case['name']}'")
        seen_names.add(case["name"])
        expect_type(case["extra"], list, f"{case_path}.extra")
        expect_type(case["flags"], dict, f"{case_path}.flags")
        if not isinstance(case["expect_error"], bool):
            fail(f"{case_path}.expect_error: expected boolean")


def iter_profile_files():
    for name in sorted(os.listdir(DEVICE_CONSISTENCY_DIR)):
        if name.endswith(".json"):
            yield os.path.join(DEVICE_CONSISTENCY_DIR, name)


def main():
    for profile_path in iter_profile_files():
        validate_device_consistency_profile(profile_path)
    validate_unsupported_paths(UNSUPPORTED_PATHS_FILE)
    print("spec-validation-ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"spec-validation-failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
