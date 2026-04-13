#!/usr/bin/env python3
"""Verify per-library command consistency across the same device set.

Runs live checks against a real PLC using the existing wrapper clients.
For each library, it exercises the same address through the supported command
paths defined by a checked-in device-consistency profile and confirms the reads
and read-backs agree.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime

import verify

ROOT = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(ROOT, "specs", "device_consistency")
UNSUPPORTED_PATHS_FILE = os.path.join(ROOT, "specs", "shared", "unsupported_path_vectors.json")
DEFAULT_PROFILE = "r120pcpu_tcp1025"
DEFAULT_REPORT_JSON = os.path.join(ROOT, "logs", "latest_device_consistency.json")
DEFAULT_REPORT_MD = os.path.join(ROOT, "logs", "latest_device_consistency.md")

LONG_STATE_BASE = {
    "LTS": ("LTN", True),
    "LTC": ("LTN", False),
    "LSTS": ("LSTN", True),
    "LSTC": ("LSTN", False),
    "LCS": ("LCN", True),
    "LCC": ("LCN", False),
}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.168.250.100")
    parser.add_argument("--port", type=int, default=1025)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--clients", default="all")
    parser.add_argument("--devices", default="")
    parser.add_argument("--retries", type=int)
    parser.add_argument("--retry-delay-ms", type=int)
    parser.add_argument("--command-delay-ms", type=int)
    parser.add_argument("--settle-delay-ms", type=int)
    parser.add_argument("--report-json", default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", default=DEFAULT_REPORT_MD)
    restore_group = parser.add_mutually_exclusive_group()
    restore_group.add_argument("--restore-after", dest="restore_after", action="store_true")
    restore_group.add_argument("--no-restore-after", dest="restore_after", action="store_false")
    parser.set_defaults(restore_after=None)
    return parser.parse_args()


def load_json(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def resolve_profile_path(value: str):
    if os.path.isfile(value):
        return value
    if value.endswith(".json"):
        path = os.path.join(PROFILES_DIR, value)
    else:
        path = os.path.join(PROFILES_DIR, f"{value}.json")
    if not os.path.isfile(path):
        raise SystemExit(f"unknown profile '{value}'")
    return path


def expand_profile_devices(profile_payload: dict):
    devices = []
    for group in profile_payload.get("groups", []):
        volatile_addresses = set(group.get("volatile_addresses") or [])
        base_restore_policy = group.get("restore_policy", "skip")
        volatile_restore_policy = group.get("volatile_restore_policy", base_restore_policy)
        if group.get("items"):
            raw_items = group["items"]
        else:
            raw_items = [{"address": address} for address in group.get("addresses", [])]
        for raw_item in raw_items:
            item = dict(raw_item)
            address = item["address"]
            volatile = bool(item.get("volatile", address in volatile_addresses))
            spec = {
                "address": address,
                "named_address": item.get("named_address"),
                "kind": group["kind"],
                "word_count": item.get("word_count", group.get("word_count")),
                "read_paths": list(group.get("read_paths") or []),
                "write_paths": list(group.get("write_paths") or []),
                "restore_path": item.get("restore_path", group.get("restore_path")),
                "restore_policy": item.get(
                    "restore_policy",
                    volatile_restore_policy if volatile else base_restore_policy,
                ),
                "read_only": bool(item.get("read_only", group.get("read_only", False))),
                "volatile": volatile,
                "notes": item.get("notes", group.get("notes", "")),
            }
            if spec["kind"] in {"long-state-bit", "long-counter-ro"}:
                spec["base_address"], spec["contact"] = state_base_address(address)
            devices.append(spec)
    return devices


def load_profile(profile_name: str):
    profile_path = resolve_profile_path(profile_name)
    payload = load_json(profile_path)
    payload["_path"] = profile_path
    payload["_devices"] = expand_profile_devices(payload)
    return payload


def load_unsupported_command_map(path: str = UNSUPPORTED_PATHS_FILE):
    try:
        payload = load_json(path)
    except Exception:
        return {}

    mapping = defaultdict(list)
    for item in payload.get("cases", []):
        device = item.get("device")
        if not device:
            continue
        mapping[device].append(
            {
                "label": item.get("command_label", item.get("command", "")),
                "name": item.get("name", ""),
                "description": item.get("description", ""),
            }
        )
    return dict(mapping)


def runtime_options(profile_payload: dict, args):
    defaults = profile_payload.get("defaults") or {}

    def choose(name, fallback):
        cli_value = getattr(args, name)
        if cli_value is not None:
            return cli_value
        if name in defaults:
            return defaults[name]
        return fallback

    return {
        "frame": defaults.get("frame", "4e"),
        "series": defaults.get("series", "iqr"),
        "retries": choose("retries", 6),
        "retry_delay_ms": choose("retry_delay_ms", 500),
        "command_delay_ms": choose("command_delay_ms", 100),
        "settle_delay_ms": choose("settle_delay_ms", 200),
        "restore_after": choose("restore_after", True),
    }


def resolve_clients(raw_value: str):
    if raw_value.strip().lower() == "all":
        return list(verify.CLIENT_ORDER)
    selected = []
    seen = set()
    for item in raw_value.split(","):
        key = verify.CLIENT_ALIASES.get(item.strip().lower())
        if key is None:
            raise SystemExit(f"unknown client '{item.strip()}'")
        if key not in seen:
            seen.add(key)
            selected.append(key)
    return selected


def selected_devices(raw_value: str):
    if not raw_value.strip():
        return None
    return {item.strip() for item in raw_value.split(",") if item.strip()}


def wants_device(selection: set[str] | None, spec: dict):
    if selection is None:
        return True
    if spec["address"] in selection:
        return True
    named_address = spec.get("named_address")
    return bool(named_address and named_address in selection)


def make_error(message: str):
    raise RuntimeError(message)


def split_plain_device(address: str):
    token = address.split(":", 1)[0]
    match = re.match(r"^([A-Z]+)([0-9A-F]+)$", token)
    if not match:
        make_error(f"unsupported device format: {address}")
    return match.group(1), match.group(2)


def state_base_address(address: str):
    code, number = split_plain_device(address)
    base_code, contact = LONG_STATE_BASE[code]
    return f"{base_code}{number}", contact


def seeded_u16(label: str, salt: int):
    value = 0x811C9DC5 ^ salt
    for byte in label.encode("ascii", "ignore"):
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    result = (value & 0xFFFF) | 1
    return result if result else 1


def seeded_u32(label: str, salt: int):
    high = seeded_u16(label, salt)
    low = seeded_u16(label, salt ^ 0xA5A55A5A)
    return (((high << 16) | low) % 2_000_000_000) + 1


def bool_value(value):
    if isinstance(value, bool):
        return value
    return bool(int(value))


def int_value(value):
    return int(value)


def run(
    client_name: str,
    command: str,
    address: str = "",
    extra=None,
    flags=None,
    retries=6,
    retry_delay_ms=500,
    command_delay_ms=100,
    **_ignored,
):
    extra = extra or []
    flags = dict(flags or {})
    last = None
    for attempt in range(retries):
        result = verify.run_client(client_name, command, address, extra, flags)
        if command_delay_ms:
            time.sleep(command_delay_ms / 1000.0)
        if result.get("status") == "success":
            return result
        last = result
        if attempt + 1 < retries:
            time.sleep(retry_delay_ms / 1000.0)
    raise RuntimeError(f"{client_name} {command} {address}: {last}")


def protocol_flags(kwargs, extra=None):
    flags = {
        "frame": kwargs.get("frame", "4e"),
        "series": kwargs.get("series", "iqr"),
    }
    if extra:
        flags.update(extra)
    return flags


def settle(**kwargs):
    delay_ms = kwargs.get("settle_delay_ms", 200)
    if delay_ms:
        time.sleep(delay_ms / 1000.0)


def named_scalar(client_name: str, address: str, flags=None, **kwargs):
    result = run(client_name, "read-named", address, [], flags, **kwargs)
    if not result.get("values"):
        make_error(f"missing named value for {address}")
    return result["values"][0]


def read_bit_direct(client_name: str, address: str, **kwargs):
    return bool_value(run(client_name, "read", address, [1], protocol_flags(kwargs, {"mode": "bit"}), **kwargs)["values"][0])


def read_bit_named(client_name: str, address: str, **kwargs):
    return bool_value(named_scalar(client_name, address, protocol_flags(kwargs), **kwargs))


def write_bit_direct(client_name: str, address: str, value: bool, **kwargs):
    run(client_name, "write", address, [1 if value else 0], protocol_flags(kwargs, {"mode": "bit"}), **kwargs)


def write_bit_random(client_name: str, address: str, value: bool, **kwargs):
    run(client_name, "random-write-bits", "", [], protocol_flags(kwargs, {"bits": f"{address}={1 if value else 0}"}), **kwargs)


def write_bit_named(client_name: str, address: str, value: bool, **kwargs):
    run(client_name, "write-named", f"{address}={1 if value else 0}", [], protocol_flags(kwargs), **kwargs)


def read_word_direct(client_name: str, address: str, count=1, **kwargs):
    result = run(client_name, "read", address, [count], protocol_flags(kwargs), **kwargs)
    return [int_value(item) for item in result["values"]]


def read_word_named(client_name: str, address: str, **kwargs):
    return int_value(named_scalar(client_name, address, protocol_flags(kwargs), **kwargs))


def read_word_random(client_name: str, address: str, **kwargs):
    result = run(client_name, "random-read", "", [], protocol_flags(kwargs, {"word-devs": address}), **kwargs)
    return int_value(result["word_values"][0])


def write_word_direct(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "write", address, [value], protocol_flags(kwargs), **kwargs)


def write_word_random(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "random-write-words", "", [], protocol_flags(kwargs, {"words": f"{address}={value}"}), **kwargs)


def write_word_named(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "write-named", f"{address}={value}", [], protocol_flags(kwargs), **kwargs)


def read_dword_direct(client_name: str, address: str, **kwargs):
    result = run(client_name, "read", address, [1], protocol_flags(kwargs, {"mode": "dword"}), **kwargs)
    return int_value(result["values"][0]) & 0xFFFFFFFF


def write_dword_direct(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "write", address, [value], protocol_flags(kwargs, {"mode": "dword"}), **kwargs)


def read_dword_named(client_name: str, address: str, **kwargs):
    return int_value(named_scalar(client_name, address, protocol_flags(kwargs), **kwargs)) & 0xFFFFFFFF


def read_dword_random(client_name: str, address: str, **kwargs):
    result = run(client_name, "random-read", "", [], protocol_flags(kwargs, {"dword-devs": address}), **kwargs)
    return int_value(result["dword_values"][0]) & 0xFFFFFFFF


def write_dword_random(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "random-write-words", "", [], protocol_flags(kwargs, {"dwords": f"{address}={value}"}), **kwargs)


def write_dword_named(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "write-named", f"{address}={value}", [], protocol_flags(kwargs), **kwargs)


def read_ext_bit(client_name: str, address: str, **kwargs):
    result = run(client_name, "read-ext", address, [1], protocol_flags(kwargs, {"mode": "bit"}), **kwargs)
    return bool_value(result["values"][0])


def write_ext_bit(client_name: str, address: str, value: bool, **kwargs):
    run(client_name, "write-ext", address, [1 if value else 0], protocol_flags(kwargs, {"mode": "bit"}), **kwargs)


def read_ext_word(client_name: str, address: str, **kwargs):
    result = run(client_name, "read-ext", address, [1], protocol_flags(kwargs), **kwargs)
    return int_value(result["values"][0])


def write_ext_word(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "write-ext", address, [value], protocol_flags(kwargs), **kwargs)


def decode_dword(words):
    return (int(words[0]) & 0xFFFF) | ((int(words[1]) & 0xFFFF) << 16)


def decode_long_state(words, contact):
    if len(words) != 4:
        make_error(f"expected 4 words, got {words!r}")
    status = int(words[2]) & 0xFFFF
    return bool(status & (0x0002 if contact else 0x0001))


def decode_long_current(words):
    if len(words) != 4:
        make_error(f"expected 4 words, got {words!r}")
    return decode_dword(words[:2])


def ensure_equal(label: str, observed: dict[str, object]):
    values = list(observed.items())
    if not values:
        return
    expected = values[0][1]
    for name, value in values[1:]:
        if value != expected:
            details = ", ".join(f"{key}={val!r}" for key, val in observed.items())
            make_error(f"{label} mismatch: {details}; diverged at {name}")


def assert_common_bit_reads(client_name: str, address: str, expected: bool | None, **kwargs):
    observed = {
        "direct": read_bit_direct(client_name, address, **kwargs),
        "named": read_bit_named(client_name, address, **kwargs),
    }
    if expected is not None:
        observed["expected"] = expected
    ensure_equal(f"{client_name} {address}", observed)


def assert_long_state_reads(client_name: str, spec: dict, expected: bool | None, **kwargs):
    observed = {
        "named": read_bit_named(client_name, spec["address"], **kwargs),
        "base_block4": decode_long_state(read_word_direct(client_name, spec["base_address"], 4, **kwargs), spec["contact"]),
    }
    if expected is not None:
        observed["expected"] = expected
    ensure_equal(f"{client_name} {spec['address']}", observed)


def assert_common_word_reads(client_name: str, address: str, expected: int | None, **kwargs):
    observed = {
        "direct": read_word_direct(client_name, address, **kwargs)[0],
        "named": read_word_named(client_name, address, **kwargs),
        "random": read_word_random(client_name, address, **kwargs),
    }
    if expected is not None:
        observed["expected"] = expected
    ensure_equal(f"{client_name} {address}", observed)


def assert_long_current_reads(client_name: str, spec: dict, expected: int | None, **kwargs):
    observed = {
        "named": read_dword_named(client_name, spec["named_address"], **kwargs),
        "base_block4": decode_long_current(read_word_direct(client_name, spec["address"], 4, **kwargs)),
    }
    if expected is not None:
        observed["expected"] = expected
    ensure_equal(f"{client_name} {spec['address']}", observed)


def assert_common_dword_reads(client_name: str, spec: dict, expected: int | None, **kwargs):
    observed = {
        "direct": read_dword_direct(client_name, spec["address"], **kwargs),
        "named": read_dword_named(client_name, spec["named_address"], **kwargs),
        "random": read_dword_random(client_name, spec["address"], **kwargs),
        "raw_words_low_dword": decode_dword(read_word_direct(client_name, spec["address"], spec["word_count"], **kwargs)[:2]),
    }
    if expected is not None:
        observed["expected"] = expected
    ensure_equal(f"{client_name} {spec['address']}", observed)


def assert_ext_bit_reads(client_name: str, address: str, expected: bool | None, **kwargs):
    observed = {"read_ext": read_ext_bit(client_name, address, **kwargs)}
    if expected is not None:
        observed["expected"] = expected
    ensure_equal(f"{client_name} {address}", observed)


def assert_ext_word_reads(client_name: str, address: str, expected: int | None, **kwargs):
    observed = {"read_ext": read_ext_word(client_name, address, **kwargs)}
    if expected is not None:
        observed["expected"] = expected
    ensure_equal(f"{client_name} {address}", observed)


def assert_consistent_reads(spec: dict, client_name: str, expected, **kwargs):
    kind = spec["kind"]
    if kind == "bit":
        assert_common_bit_reads(client_name, spec["address"], expected, **kwargs)
    elif kind in {"long-state-bit", "long-counter-ro"}:
        assert_long_state_reads(client_name, spec, expected, **kwargs)
    elif kind == "word":
        assert_common_word_reads(client_name, spec["address"], expected, **kwargs)
    elif kind == "long-current":
        assert_long_current_reads(client_name, spec, expected, **kwargs)
    elif kind == "dword":
        assert_common_dword_reads(client_name, spec, expected, **kwargs)
    elif kind == "ext-bit":
        assert_ext_bit_reads(client_name, spec["address"], expected, **kwargs)
    elif kind == "ext-word":
        assert_ext_word_reads(client_name, spec["address"], expected, **kwargs)
    else:
        make_error(f"unknown device kind: {kind}")


def primary_read(spec: dict, client_name: str, **kwargs):
    kind = spec["kind"]
    if kind == "bit":
        return read_bit_direct(client_name, spec["address"], **kwargs)
    if kind in {"long-state-bit", "long-counter-ro"}:
        return read_bit_named(client_name, spec["address"], **kwargs)
    if kind == "word":
        return read_word_direct(client_name, spec["address"], **kwargs)[0]
    if kind == "long-current":
        return read_dword_named(client_name, spec["named_address"], **kwargs)
    if kind == "dword":
        return read_dword_direct(client_name, spec["address"], **kwargs)
    if kind == "ext-bit":
        return read_ext_bit(client_name, spec["address"], **kwargs)
    if kind == "ext-word":
        return read_ext_word(client_name, spec["address"], **kwargs)
    make_error(f"unknown device kind: {kind}")


def restore_original(spec: dict, client_name: str, original, **kwargs):
    restore_after = kwargs.get("restore_after", True)
    policy = spec.get("restore_policy", "skip")
    if not restore_after:
        return {"attempted": False, "status": "disabled", "policy": policy}
    if policy == "skip" or spec.get("read_only") or not spec.get("restore_path"):
        return {"attempted": False, "status": "skipped", "policy": policy}

    kind = spec["kind"]
    if kind == "bit":
        write_bit_direct(client_name, spec["address"], original, **kwargs)
    elif kind == "long-state-bit":
        write_bit_named(client_name, spec["address"], original, **kwargs)
    elif kind == "word":
        write_word_direct(client_name, spec["address"], original, **kwargs)
    elif kind == "long-current":
        write_dword_named(client_name, spec["named_address"], original, **kwargs)
    elif kind == "dword":
        write_dword_direct(client_name, spec["address"], original, **kwargs)
    elif kind == "ext-bit":
        write_ext_bit(client_name, spec["address"], original, **kwargs)
    elif kind == "ext-word":
        write_ext_word(client_name, spec["address"], original, **kwargs)
    else:
        make_error(f"unsupported restore kind: {kind}")

    settle(**kwargs)
    info = {
        "attempted": True,
        "path": spec["restore_path"],
        "policy": policy,
    }
    if policy == "strict":
        assert_consistent_reads(spec, client_name, original, **kwargs)
        info["status"] = "verified"
        info["observed"] = primary_read(spec, client_name, **kwargs)
    else:
        try:
            info["observed"] = primary_read(spec, client_name, **kwargs)
            info["status"] = "best-effort"
        except Exception as exc:  # noqa: BLE001
            info["status"] = "best-effort-read-failed"
            info["message"] = str(exc)
    return info


def compare_common_bit(client_name: str, spec: dict, **kwargs):
    original = read_bit_direct(client_name, spec["address"], **kwargs)
    details = {
        "read_before": original,
        "write_sequence": [
            {"path": "direct-bit", "value": True},
            {"path": "random-bit", "value": False},
            {"path": "named", "value": True},
            {"path": "direct-bit", "value": False},
        ],
    }
    try:
        assert_common_bit_reads(client_name, spec["address"], original, **kwargs)
        write_bit_direct(client_name, spec["address"], True, **kwargs)
        settle(**kwargs)
        assert_common_bit_reads(client_name, spec["address"], True, **kwargs)
        write_bit_random(client_name, spec["address"], False, **kwargs)
        settle(**kwargs)
        assert_common_bit_reads(client_name, spec["address"], False, **kwargs)
        write_bit_named(client_name, spec["address"], True, **kwargs)
        settle(**kwargs)
        assert_common_bit_reads(client_name, spec["address"], True, **kwargs)
        write_bit_direct(client_name, spec["address"], False, **kwargs)
        settle(**kwargs)
        assert_common_bit_reads(client_name, spec["address"], False, **kwargs)
    finally:
        details["restore"] = restore_original(spec, client_name, original, **kwargs)
    return details


def compare_long_state_bit(client_name: str, spec: dict, **kwargs):
    original = read_bit_named(client_name, spec["address"], **kwargs)
    details = {
        "read_before": original,
        "write_sequence": [
            {"path": "named", "value": True},
            {"path": "named", "value": False},
            {"path": "named", "value": True},
            {"path": "named", "value": False},
        ],
    }
    try:
        assert_long_state_reads(client_name, spec, original, **kwargs)
        write_bit_named(client_name, spec["address"], True, **kwargs)
        settle(**kwargs)
        assert_long_state_reads(client_name, spec, True, **kwargs)
        write_bit_named(client_name, spec["address"], False, **kwargs)
        settle(**kwargs)
        assert_long_state_reads(client_name, spec, False, **kwargs)
        write_bit_named(client_name, spec["address"], True, **kwargs)
        settle(**kwargs)
        assert_long_state_reads(client_name, spec, True, **kwargs)
        write_bit_named(client_name, spec["address"], False, **kwargs)
        settle(**kwargs)
        assert_long_state_reads(client_name, spec, False, **kwargs)
    finally:
        details["restore"] = restore_original(spec, client_name, original, **kwargs)
    return details


def compare_long_counter_read_only(client_name: str, spec: dict, **kwargs):
    original = read_bit_named(client_name, spec["address"], **kwargs)
    assert_long_state_reads(client_name, spec, original, **kwargs)
    return {
        "read_before": original,
        "write_sequence": [],
        "restore": restore_original(spec, client_name, original, **kwargs),
    }


def compare_common_word(client_name: str, spec: dict, **kwargs):
    original = read_word_direct(client_name, spec["address"], **kwargs)[0]
    value_a = seeded_u16(spec["address"], 0x11)
    value_b = seeded_u16(spec["address"], 0x22)
    strict_expected = not spec.get("volatile", False)
    details = {
        "read_before": original,
        "write_sequence": [
            {"path": "direct-word", "value": value_a},
            {"path": "random-word", "value": value_b},
            {"path": "named-word", "value": value_a},
        ],
    }
    try:
        assert_common_word_reads(client_name, spec["address"], original, **kwargs)
        write_word_direct(client_name, spec["address"], value_a, **kwargs)
        settle(**kwargs)
        assert_common_word_reads(client_name, spec["address"], value_a if strict_expected else None, **kwargs)
        write_word_random(client_name, spec["address"], value_b, **kwargs)
        settle(**kwargs)
        assert_common_word_reads(client_name, spec["address"], value_b if strict_expected else None, **kwargs)
        write_word_named(client_name, spec["address"], value_a, **kwargs)
        settle(**kwargs)
        assert_common_word_reads(client_name, spec["address"], value_a if strict_expected else None, **kwargs)
    finally:
        details["restore"] = restore_original(spec, client_name, original, **kwargs)
    return details


def compare_long_current(client_name: str, spec: dict, **kwargs):
    original = read_dword_named(client_name, spec["named_address"], **kwargs)
    value_a = seeded_u32(spec["address"], 0x33)
    value_b = seeded_u32(spec["address"], 0x44)
    details = {
        "read_before": original,
        "write_sequence": [
            {"path": "random-dword", "value": value_a},
            {"path": "named-dword", "value": value_b},
        ],
    }
    try:
        assert_long_current_reads(client_name, spec, original, **kwargs)
        write_dword_random(client_name, spec["address"], value_a, **kwargs)
        settle(**kwargs)
        assert_long_current_reads(client_name, spec, value_a, **kwargs)
        write_dword_named(client_name, spec["named_address"], value_b, **kwargs)
        settle(**kwargs)
        assert_long_current_reads(client_name, spec, value_b, **kwargs)
    finally:
        details["restore"] = restore_original(spec, client_name, original, **kwargs)
    return details


def compare_common_dword(client_name: str, spec: dict, **kwargs):
    original = read_dword_direct(client_name, spec["address"], **kwargs)
    value_a = seeded_u32(spec["address"], 0x55)
    value_b = seeded_u32(spec["address"], 0x66)
    strict_expected = not spec.get("volatile", False)
    details = {
        "read_before": original,
        "write_sequence": [
            {"path": "direct-dword", "value": value_a},
            {"path": "random-dword", "value": value_b},
            {"path": "named-dword", "value": value_a},
        ],
    }
    try:
        assert_common_dword_reads(client_name, spec, original, **kwargs)
        write_dword_direct(client_name, spec["address"], value_a, **kwargs)
        settle(**kwargs)
        assert_common_dword_reads(client_name, spec, value_a if strict_expected else None, **kwargs)
        write_dword_random(client_name, spec["address"], value_b, **kwargs)
        settle(**kwargs)
        assert_common_dword_reads(client_name, spec, value_b if strict_expected else None, **kwargs)
        write_dword_named(client_name, spec["named_address"], value_a, **kwargs)
        settle(**kwargs)
        assert_common_dword_reads(client_name, spec, value_a if strict_expected else None, **kwargs)
    finally:
        details["restore"] = restore_original(spec, client_name, original, **kwargs)
    return details


def compare_ext_bit(client_name: str, spec: dict, **kwargs):
    original = read_ext_bit(client_name, spec["address"], **kwargs)
    strict_expected = not spec.get("volatile", False)
    details = {
        "read_before": original,
        "write_sequence": [
            {"path": "write-ext", "value": True},
            {"path": "write-ext", "value": False},
            {"path": "write-ext", "value": True},
            {"path": "write-ext", "value": False},
        ],
    }
    try:
        for value in (True, False, True, False):
            write_ext_bit(client_name, spec["address"], value, **kwargs)
            settle(**kwargs)
            assert_ext_bit_reads(client_name, spec["address"], value if strict_expected else None, **kwargs)
    finally:
        details["restore"] = restore_original(spec, client_name, original, **kwargs)
    return details


def compare_ext_word(client_name: str, spec: dict, **kwargs):
    original = read_ext_word(client_name, spec["address"], **kwargs)
    value_a = seeded_u16(spec["address"], 0x77)
    value_b = seeded_u16(spec["address"], 0x88)
    details = {
        "read_before": original,
        "write_sequence": [
            {"path": "write-ext", "value": value_a},
            {"path": "write-ext", "value": value_b},
            {"path": "write-ext", "value": value_a},
        ],
    }
    try:
        for value in (value_a, value_b, value_a):
            write_ext_word(client_name, spec["address"], value, **kwargs)
            settle(**kwargs)
            assert_ext_word_reads(client_name, spec["address"], value, **kwargs)
    finally:
        details["restore"] = restore_original(spec, client_name, original, **kwargs)
    return details


COMPARATORS = {
    "bit": compare_common_bit,
    "word": compare_common_word,
    "long-state-bit": compare_long_state_bit,
    "long-counter-ro": compare_long_counter_read_only,
    "long-current": compare_long_current,
    "dword": compare_common_dword,
    "ext-bit": compare_ext_bit,
    "ext-word": compare_ext_word,
}


def classify_failure(message: str):
    lowered = message.lower()
    if "mismatch:" in lowered:
        return "consistency_mismatch"
    if "connection refused" in lowered or "connect refused" in lowered:
        return "transport_connect_refused"
    if "timed out" in lowered or "timeout" in lowered:
        return "transport_timeout"
    if "unsupported" in lowered or "does not support" in lowered:
        return "unsupported_path"
    if "missing named value" in lowered or "expected 4 words" in lowered:
        return "response_shape"
    if lowered.startswith("exit "):
        return "wrapper_process_error"
    return "other"


def build_device_matrix(device_specs: list[dict]):
    matrix = []
    for spec in device_specs:
        matrix.append(
            {
                "device": spec["address"],
                "kind": spec["kind"],
                "named_address": spec.get("named_address"),
                "volatile": spec.get("volatile", False),
                "read_only": spec.get("read_only", False),
                "read_paths": list(spec.get("read_paths") or []),
                "write_paths": list(spec.get("write_paths") or []),
                "restore_path": spec.get("restore_path"),
                "restore_policy": spec.get("restore_policy"),
                "unsupported_commands": list(spec.get("unsupported_commands") or []),
                "notes": spec.get("notes", ""),
            }
        )
    return matrix


def summarize_results(results: list[dict], failures: list[dict]):
    client_summary = {}
    kind_summary = {}
    restore_counter = Counter()

    by_client = defaultdict(list)
    by_kind = defaultdict(list)
    for item in results:
        by_client[item["client"]].append(item)
        by_kind[item["kind"]].append(item)
        restore_status = item.get("restore", {}).get("status")
        if restore_status:
            restore_counter[restore_status] += 1

    for client, items in by_client.items():
        client_summary[client] = {
            "passed": sum(1 for item in items if item["status"] == "pass"),
            "failed": sum(1 for item in items if item["status"] == "fail"),
            "total": len(items),
            "duration_ms": sum(item.get("duration_ms", 0) for item in items),
        }

    for kind, items in by_kind.items():
        kind_summary[kind] = {
            "passed": sum(1 for item in items if item["status"] == "pass"),
            "failed": sum(1 for item in items if item["status"] == "fail"),
            "total": len(items),
            "duration_ms": sum(item.get("duration_ms", 0) for item in items),
        }

    failure_classes = Counter(item["failure_class"] for item in failures)
    return {
        "client_summary": client_summary,
        "kind_summary": kind_summary,
        "restore_summary": dict(restore_counter),
        "failure_classes": [
            {
                "class": name,
                "count": count,
                "clients": sorted({item["client"] for item in failures if item["failure_class"] == name}),
                "devices": sorted({item["device"] for item in failures if item["failure_class"] == name}),
            }
            for name, count in sorted(failure_classes.items())
        ],
    }


def markdown_table(headers, rows):
    if not rows:
        return "_none_"
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def format_paths(paths):
    return "<br>".join(paths) if paths else "none"


def markdown_report(payload):
    client_rows = [
        [
            client,
            str(summary["passed"]),
            str(summary["failed"]),
            str(summary["total"]),
            f"{summary['duration_ms'] / 1000:.2f}",
        ]
        for client, summary in sorted(payload["client_summary"].items())
    ]
    kind_rows = [
        [
            kind,
            str(summary["passed"]),
            str(summary["failed"]),
            str(summary["total"]),
            f"{summary['duration_ms'] / 1000:.2f}",
        ]
        for kind, summary in sorted(payload["kind_summary"].items())
    ]
    failure_rows = [
        [
            item["class"],
            str(item["count"]),
            ", ".join(item["clients"]),
            ", ".join(item["devices"]),
        ]
        for item in payload["failure_classes"]
    ]
    matrix_rows = [
        [
            item["device"],
            item["kind"],
            "yes" if item["volatile"] else "no",
            "yes" if item["read_only"] else "no",
            format_paths(item["read_paths"]),
            format_paths(item["write_paths"]),
            item["restore_path"] or "none",
            item["restore_policy"] or "none",
            "<br>".join(item["unsupported_commands"]) if item["unsupported_commands"] else "none",
        ]
        for item in payload["device_matrix"]
    ]

    lines = [
        "# Device Command Consistency",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Started: `{payload['started_at']}`",
        f"- Finished: `{payload['finished_at']}`",
        f"- Duration: `{payload['duration_seconds']:.2f}s`",
        f"- Profile: `{payload['profile']}`",
        f"- Host: `{payload['host']}:{payload['port']}`",
        f"- Clients: `{','.join(payload['clients'])}`",
        f"- Passed: `{payload['passed']}`",
        f"- Failed: `{payload['failed']}`",
        f"- Total: `{payload['total']}`",
        "",
        "## Client Summary",
        "",
        markdown_table(["Client", "Pass", "Fail", "Total", "Duration (s)"], client_rows),
        "",
        "## Kind Summary",
        "",
        markdown_table(["Kind", "Pass", "Fail", "Total", "Duration (s)"], kind_rows),
        "",
        "## Failure Classes",
        "",
        markdown_table(["Class", "Count", "Clients", "Devices"], failure_rows),
        "",
        "## Restore Summary",
        "",
    ]

    if payload["restore_summary"]:
        for key, value in sorted(payload["restore_summary"].items()):
            lines.append(f"- `{key}`: `{value}`")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Device Matrix",
            "",
            markdown_table(
                ["Device", "Kind", "Volatile", "Read-only", "Read Paths", "Write Paths", "Restore Path", "Restore Policy", "Unsupported"],
                matrix_rows,
            ),
            "",
        ]
    )

    if payload["failures"]:
        lines.extend(["## Failures", ""])
        for failure in payload["failures"]:
            lines.append(
                f"- `{failure['client']}` `{failure['device']}` `{failure['kind']}` `{failure['failure_class']}`: {failure['message']}"
            )
        lines.append("")

    return "\n".join(lines)


def main():
    args = parse_args()
    profile_payload = load_profile(args.profile)
    unsupported_map = load_unsupported_command_map()
    options = runtime_options(profile_payload, args)

    verify.HOST = args.host
    verify.PORT = args.port
    clients = resolve_clients(args.clients)
    device_filter = selected_devices(args.devices)
    device_specs = [
        dict(spec, unsupported_commands=[entry["label"] for entry in unsupported_map.get(spec["address"], [])])
        for spec in profile_payload["_devices"]
        if wants_device(device_filter, spec)
    ]

    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    results = []
    failures = []

    for client in clients:
        for spec in device_specs:
            started_step = time.monotonic()
            base_result = {
                "client": client,
                "device": spec["address"],
                "kind": spec["kind"],
                "volatile": spec.get("volatile", False),
                "read_only": spec.get("read_only", False),
                "read_paths": list(spec.get("read_paths") or []),
                "write_paths": list(spec.get("write_paths") or []),
                "restore_path": spec.get("restore_path"),
                "restore_policy": spec.get("restore_policy"),
                "unsupported_commands": list(spec.get("unsupported_commands") or []),
            }
            try:
                details = COMPARATORS[spec["kind"]](client, spec, **options)
                duration_ms = round((time.monotonic() - started_step) * 1000, 2)
                result = {
                    **base_result,
                    "status": "pass",
                    "duration_ms": duration_ms,
                    **details,
                }
                results.append(result)
                print(f"PASS {client:<8} {spec['kind']:<16} {spec['address']}")
            except Exception as exc:  # noqa: BLE001
                duration_ms = round((time.monotonic() - started_step) * 1000, 2)
                failure_class = classify_failure(str(exc))
                failure = {
                    **base_result,
                    "status": "fail",
                    "duration_ms": duration_ms,
                    "failure_class": failure_class,
                    "message": str(exc),
                }
                failures.append(failure)
                results.append(failure)
                print(f"FAIL {client:<8} {spec['kind']:<16} {spec['address']} :: {exc}")

    finished_at = datetime.now(UTC)
    summary = summarize_results(results, failures)
    payload = {
        "generated_at": finished_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "started_at": started_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "finished_at": finished_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "duration_seconds": round(time.monotonic() - started_monotonic, 3),
        "profile": profile_payload.get("name", args.profile),
        "profile_path": profile_payload["_path"],
        "host": args.host,
        "port": args.port,
        "clients": clients,
        "selected_devices": sorted(device_filter) if device_filter else [],
        "defaults": profile_payload.get("defaults") or {},
        "options": options,
        "passed": sum(1 for item in results if item["status"] == "pass"),
        "failed": sum(1 for item in results if item["status"] == "fail"),
        "total": len(results),
        "results": results,
        "failures": failures,
        "device_matrix": build_device_matrix(device_specs),
        **summary,
    }

    report_json = args.report_json
    report_md = args.report_md
    os.makedirs(os.path.dirname(report_json), exist_ok=True)
    os.makedirs(os.path.dirname(report_md), exist_ok=True)
    with open(report_json, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    with open(report_md, "w", encoding="utf-8") as handle:
        handle.write(markdown_report(payload))

    print(
        "summary: "
        f"passed={payload['passed']} failed={payload['failed']} total={payload['total']} "
        f"profile={payload['profile']} duration={payload['duration_seconds']:.2f}s"
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
