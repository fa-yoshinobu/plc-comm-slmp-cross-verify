#!/usr/bin/env python3
"""Verify per-library command consistency across the same device set.

Runs live checks against a real PLC using the existing wrapper clients.
For each library, it writes the same address through multiple supported commands
and confirms the subsequent reads agree across the available command paths.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import UTC, datetime

import verify

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT_JSON = os.path.join(ROOT, "logs", "latest_device_consistency.json")
REPORT_MD = os.path.join(ROOT, "logs", "latest_device_consistency.md")
FLAGS_4E_IQR = {"frame": "4e", "series": "iqr"}
VOLATILE_WORD_DEVICES = {"TN10", "STN10", "CN10", "SD10", "RD10"}
VOLATILE_DWORD_DEVICES = {"LCN10", "LZ0", "LZ1"}
VOLATILE_EXT_BIT_DEVICES = {r"J1\X10"}

COMMON_BITS = [
    "STS10", "STC10", "TS10", "TC10", "CS10", "CC10", "SB10", "DX10", "DY10",
    "X10", "Y10", "M10", "L10", "F100", "V10", "B10", "SM10",
]
COMMON_WORDS = [
    "STN10", "TN10", "CN10", "SW10", "ZR10", "D10", "W10", "Z10", "R10", "SD10", "RD10",
]
LONG_STATE_BITS = ["LTS10", "LTC10", "LSTS10", "LSTC10"]
LONG_COUNTER_BITS = ["LCS10", "LCC10"]
LONG_DWORDS = [
    ("LTN10", "LTN10:D"),
    ("LSTN10", "LSTN10:D"),
    ("LCN10", "LCN10:D"),
    ("LZ0", "LZ0:D"),
    ("LZ1", "LZ1:D"),
]
EXT_BITS = [r"J1\X10", r"J1\Y10", r"J1\B10", r"J1\SB10"]
EXT_WORDS = [r"J1\W10", r"J1\SW10"]

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
    parser.add_argument("--clients", default="all")
    parser.add_argument("--devices", default="")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-delay-ms", type=int, default=250)
    parser.add_argument("--command-delay-ms", type=int, default=100)
    parser.add_argument("--settle-delay-ms", type=int, default=200)
    return parser.parse_args()


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


def wants_device(selection: set[str] | None, device: str):
    return selection is None or device in selection


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
    flags = {**FLAGS_4E_IQR, **(flags or {})}
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


def settle(**kwargs):
    delay_ms = kwargs.get("settle_delay_ms", 200)
    if delay_ms:
        time.sleep(delay_ms / 1000.0)


def named_scalar(client_name: str, address: str, **kwargs):
    result = run(client_name, "read-named", address, **kwargs)
    if not result.get("values"):
        make_error(f"missing named value for {address}")
    return result["values"][0]


def read_bit_direct(client_name: str, address: str, **kwargs):
    return bool_value(run(client_name, "read", address, [1], {"mode": "bit"}, **kwargs)["values"][0])


def read_bit_named(client_name: str, address: str, **kwargs):
    return bool_value(named_scalar(client_name, address, **kwargs))


def write_bit_direct(client_name: str, address: str, value: bool, **kwargs):
    run(client_name, "write", address, [1 if value else 0], {"mode": "bit"}, **kwargs)


def write_bit_random(client_name: str, address: str, value: bool, **kwargs):
    run(client_name, "random-write-bits", "", [], {"bits": f"{address}={1 if value else 0}"}, **kwargs)


def write_bit_named(client_name: str, address: str, value: bool, **kwargs):
    run(client_name, "write-named", f"{address}={1 if value else 0}", **kwargs)


def read_word_direct(client_name: str, address: str, count=1, **kwargs):
    result = run(client_name, "read", address, [count], **kwargs)
    return [int_value(item) for item in result["values"]]


def read_word_named(client_name: str, address: str, **kwargs):
    return int_value(named_scalar(client_name, address, **kwargs))


def read_word_random(client_name: str, address: str, **kwargs):
    result = run(client_name, "random-read", "", [], {"word-devs": address}, **kwargs)
    return int_value(result["word_values"][0])


def write_word_direct(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "write", address, [value], **kwargs)


def write_word_random(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "random-write-words", "", [], {"words": f"{address}={value}"}, **kwargs)


def write_word_named(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "write-named", f"{address}={value}", **kwargs)


def read_dword_direct(client_name: str, address: str, **kwargs):
    result = run(client_name, "read", address, [1], {"mode": "dword"}, **kwargs)
    return int_value(result["values"][0]) & 0xFFFFFFFF


def write_dword_direct(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "write", address, [value], {"mode": "dword"}, **kwargs)


def read_dword_named(client_name: str, address: str, **kwargs):
    return int_value(named_scalar(client_name, address, **kwargs)) & 0xFFFFFFFF


def read_dword_random(client_name: str, address: str, **kwargs):
    result = run(client_name, "random-read", "", [], {"dword-devs": address}, **kwargs)
    return int_value(result["dword_values"][0]) & 0xFFFFFFFF


def write_dword_random(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "random-write-words", "", [], {"dwords": f"{address}={value}"}, **kwargs)


def write_dword_named(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "write-named", f"{address}={value}", **kwargs)


def read_ext_bit(client_name: str, address: str, **kwargs):
    result = run(client_name, "read-ext", address, [1], {"mode": "bit"}, **kwargs)
    return bool_value(result["values"][0])


def write_ext_bit(client_name: str, address: str, value: bool, **kwargs):
    run(client_name, "write-ext", address, [1 if value else 0], {"mode": "bit"}, **kwargs)


def read_ext_word(client_name: str, address: str, **kwargs):
    result = run(client_name, "read-ext", address, [1], **kwargs)
    return int_value(result["values"][0])


def write_ext_word(client_name: str, address: str, value: int, **kwargs):
    run(client_name, "write-ext", address, [value], **kwargs)


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


def assert_long_state_reads(client_name: str, address: str, expected: bool | None, **kwargs):
    base_address, contact = state_base_address(address)
    observed = {
        "named": read_bit_named(client_name, address, **kwargs),
        "base_block4": decode_long_state(read_word_direct(client_name, base_address, 4, **kwargs), contact),
    }
    if expected is not None:
        observed["expected"] = expected
    ensure_equal(f"{client_name} {address}", observed)


def assert_common_word_reads(client_name: str, address: str, expected: int | None, **kwargs):
    observed = {
        "direct": read_word_direct(client_name, address, **kwargs)[0],
        "named": read_word_named(client_name, address, **kwargs),
        "random": read_word_random(client_name, address, **kwargs),
    }
    if expected is not None:
        observed["expected"] = expected
    ensure_equal(f"{client_name} {address}", observed)


def assert_long_current_reads(client_name: str, address: str, named_address: str, expected: int | None, **kwargs):
    observed = {
        "named": read_dword_named(client_name, named_address, **kwargs),
        "block4": decode_long_current(read_word_direct(client_name, address, 4, **kwargs)),
    }
    if expected is not None:
        observed["expected"] = expected
    ensure_equal(f"{client_name} {address}", observed)


def assert_common_dword_reads(client_name: str, address: str, named_address: str, expected: int | None, word_count: int, **kwargs):
    observed = {
        "direct": read_dword_direct(client_name, address, **kwargs),
        "named": read_dword_named(client_name, named_address, **kwargs),
        "random": read_dword_random(client_name, address, **kwargs),
        "raw_words": decode_dword(read_word_direct(client_name, address, word_count, **kwargs)[:2]),
    }
    if expected is not None:
        observed["expected"] = expected
    ensure_equal(f"{client_name} {address}", observed)


def compare_common_bit(client_name: str, address: str, **kwargs):
    original = read_bit_direct(client_name, address, **kwargs)
    try:
        assert_common_bit_reads(client_name, address, original, **kwargs)
        write_bit_direct(client_name, address, True, **kwargs)
        settle(**kwargs)
        assert_common_bit_reads(client_name, address, True, **kwargs)
        write_bit_random(client_name, address, False, **kwargs)
        settle(**kwargs)
        assert_common_bit_reads(client_name, address, False, **kwargs)
        write_bit_named(client_name, address, True, **kwargs)
        settle(**kwargs)
        assert_common_bit_reads(client_name, address, True, **kwargs)
        write_bit_direct(client_name, address, False, **kwargs)
        settle(**kwargs)
        assert_common_bit_reads(client_name, address, False, **kwargs)
    finally:
        write_bit_direct(client_name, address, original, **kwargs)


def compare_long_state_bit(client_name: str, address: str, **kwargs):
    original = read_bit_named(client_name, address, **kwargs)
    try:
        assert_long_state_reads(client_name, address, original, **kwargs)
        write_bit_named(client_name, address, True, **kwargs)
        settle(**kwargs)
        assert_long_state_reads(client_name, address, True, **kwargs)
        write_bit_named(client_name, address, False, **kwargs)
        settle(**kwargs)
        assert_long_state_reads(client_name, address, False, **kwargs)
        write_bit_named(client_name, address, True, **kwargs)
        settle(**kwargs)
        assert_long_state_reads(client_name, address, True, **kwargs)
        write_bit_named(client_name, address, False, **kwargs)
        settle(**kwargs)
        assert_long_state_reads(client_name, address, False, **kwargs)
    finally:
        write_bit_named(client_name, address, original, **kwargs)


def compare_long_counter_read_only(client_name: str, address: str, **kwargs):
    original = read_bit_named(client_name, address, **kwargs)
    assert_long_state_reads(client_name, address, original, **kwargs)


def compare_common_word(client_name: str, address: str, **kwargs):
    original = read_word_direct(client_name, address, **kwargs)[0]
    value_a = seeded_u16(address, 0x11)
    value_b = seeded_u16(address, 0x22)
    strict_expected = address not in VOLATILE_WORD_DEVICES
    try:
        assert_common_word_reads(client_name, address, original, **kwargs)
        write_word_direct(client_name, address, value_a, **kwargs)
        settle(**kwargs)
        assert_common_word_reads(client_name, address, value_a if strict_expected else None, **kwargs)
        write_word_random(client_name, address, value_b, **kwargs)
        settle(**kwargs)
        assert_common_word_reads(client_name, address, value_b if strict_expected else None, **kwargs)
        write_word_named(client_name, address, value_a, **kwargs)
        settle(**kwargs)
        assert_common_word_reads(client_name, address, value_a if strict_expected else None, **kwargs)
    finally:
        write_word_direct(client_name, address, original, **kwargs)


def compare_long_current(client_name: str, address: str, named_address: str, **kwargs):
    original = read_dword_named(client_name, named_address, **kwargs)
    value_a = seeded_u32(address, 0x33)
    value_b = seeded_u32(address, 0x44)
    try:
        assert_long_current_reads(client_name, address, named_address, original, **kwargs)
        write_dword_random(client_name, address, value_a, **kwargs)
        settle(**kwargs)
        assert_long_current_reads(client_name, address, named_address, value_a, **kwargs)
        write_dword_named(client_name, named_address, value_b, **kwargs)
        settle(**kwargs)
        assert_long_current_reads(client_name, address, named_address, value_b, **kwargs)
    finally:
        write_dword_named(client_name, named_address, original, **kwargs)


def compare_common_dword(client_name: str, address: str, named_address: str, word_count: int, **kwargs):
    original = read_dword_direct(client_name, address, **kwargs)
    value_a = seeded_u32(address, 0x55)
    value_b = seeded_u32(address, 0x66)
    strict_expected = address not in VOLATILE_DWORD_DEVICES
    try:
        assert_common_dword_reads(client_name, address, named_address, original, word_count, **kwargs)
        write_dword_direct(client_name, address, value_a, **kwargs)
        settle(**kwargs)
        assert_common_dword_reads(client_name, address, named_address, value_a if strict_expected else None, word_count, **kwargs)
        write_dword_random(client_name, address, value_b, **kwargs)
        settle(**kwargs)
        assert_common_dword_reads(client_name, address, named_address, value_b if strict_expected else None, word_count, **kwargs)
        write_dword_named(client_name, named_address, value_a, **kwargs)
        settle(**kwargs)
        assert_common_dword_reads(client_name, address, named_address, value_a if strict_expected else None, word_count, **kwargs)
    finally:
        write_dword_direct(client_name, address, original, **kwargs)


def compare_ext_bit(client_name: str, address: str, **kwargs):
    original = read_ext_bit(client_name, address, **kwargs)
    strict_expected = address not in VOLATILE_EXT_BIT_DEVICES
    try:
        for value in (True, False, True, False):
            write_ext_bit(client_name, address, value, **kwargs)
            settle(**kwargs)
            observed = {
                "read_ext": read_ext_bit(client_name, address, **kwargs),
            }
            if strict_expected:
                observed["expected"] = value
            ensure_equal(f"{client_name} {address}", observed)
    finally:
        write_ext_bit(client_name, address, original, **kwargs)


def compare_ext_word(client_name: str, address: str, **kwargs):
    original = read_ext_word(client_name, address, **kwargs)
    value_a = seeded_u16(address, 0x77)
    value_b = seeded_u16(address, 0x88)
    try:
        for value in (value_a, value_b, value_a):
            write_ext_word(client_name, address, value, **kwargs)
            settle(**kwargs)
            observed = {
                "read_ext": read_ext_word(client_name, address, **kwargs),
                "expected": value,
            }
            ensure_equal(f"{client_name} {address}", observed)
    finally:
        write_ext_word(client_name, address, original, **kwargs)


def markdown_report(payload):
    lines = [
        "# Device Command Consistency",
        "",
        f"- Generated: {payload['generated_at']}",
        f"- Host: `{payload['host']}:{payload['port']}`",
        f"- Clients: `{','.join(payload['clients'])}`",
        f"- Passed: `{payload['passed']}`",
        f"- Failed: `{payload['failed']}`",
        f"- Total: `{payload['total']}`",
        "",
    ]
    if payload["failures"]:
        lines.append("## Failures")
        lines.append("")
        for failure in payload["failures"]:
            lines.append(f"- `{failure['client']}` `{failure['device']}`: {failure['message']}")
        lines.append("")
    lines.append("## Results")
    lines.append("")
    for item in payload["results"]:
        lines.append(f"- `{item['client']}` `{item['device']}` `{item['kind']}`: {item['status']}")
    lines.append("")
    return "\n".join(lines)


def main():
    args = parse_args()
    verify.HOST = args.host
    verify.PORT = args.port
    clients = resolve_clients(args.clients)
    device_filter = selected_devices(args.devices)

    results = []
    failures = []

    def record(client, device, kind, fn):
        if not wants_device(device_filter, device):
            return
        try:
            fn(
                client,
                retries=args.retries,
                retry_delay_ms=args.retry_delay_ms,
                command_delay_ms=args.command_delay_ms,
                settle_delay_ms=args.settle_delay_ms,
            )
            results.append({"client": client, "device": device, "kind": kind, "status": "pass"})
            print(f"PASS {client:<8} {kind:<14} {device}")
        except Exception as exc:  # noqa: BLE001
            failures.append({"client": client, "device": device, "kind": kind, "message": str(exc)})
            results.append({"client": client, "device": device, "kind": kind, "status": "fail"})
            print(f"FAIL {client:<8} {kind:<14} {device} :: {exc}")

    for client in clients:
        for device in COMMON_BITS:
            record(client, device, "bit", lambda c, **kw: compare_common_bit(c, device, **kw))
        for device in LONG_STATE_BITS:
            record(client, device, "long-bit", lambda c, **kw: compare_long_state_bit(c, device, **kw))
        for device in LONG_COUNTER_BITS:
            record(client, device, "long-counter-ro", lambda c, **kw: compare_long_counter_read_only(c, device, **kw))
        for device in COMMON_WORDS:
            record(client, device, "word", lambda c, **kw: compare_common_word(c, device, **kw))
        for address, named in LONG_DWORDS:
            if address in {"LTN10", "LSTN10"}:
                record(client, address, "long-dword", lambda c, addr=address, nm=named, **kw: compare_long_current(c, addr, nm, **kw))
            else:
                word_count = 4 if address == "LCN10" else 2
                record(client, address, "dword", lambda c, addr=address, nm=named, wc=word_count, **kw: compare_common_dword(c, addr, nm, wc, **kw))
        for device in EXT_BITS:
            record(client, device, "ext-bit", lambda c, **kw: compare_ext_bit(c, device, **kw))
        for device in EXT_WORDS:
            record(client, device, "ext-word", lambda c, **kw: compare_ext_word(c, device, **kw))

    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "host": args.host,
        "port": args.port,
        "clients": clients,
        "passed": sum(1 for item in results if item["status"] == "pass"),
        "failed": sum(1 for item in results if item["status"] == "fail"),
        "total": len(results),
        "results": results,
        "failures": failures,
    }

    os.makedirs(os.path.dirname(REPORT_JSON), exist_ok=True)
    with open(REPORT_JSON, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    with open(REPORT_MD, "w", encoding="utf-8") as handle:
        handle.write(markdown_report(payload))

    print(f"summary: passed={payload['passed']} failed={payload['failed']} total={payload['total']}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
