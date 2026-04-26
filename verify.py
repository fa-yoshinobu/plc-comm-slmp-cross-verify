"""Comprehensive SLMP cross-language verification tool.

Runs the tests defined below across the Python, .NET, C++, Node-RED, and Rust wrappers.
Checks:
  1. Status parity (all clients succeed/fail on the same test)
  2. Request packet parity (all clients send identical bytes, except 4E serial)
  3. High-level named result parity for read/poll commands
"""
import argparse
import subprocess
import json
import math
import random
import time
import os
import sys
from datetime import datetime

# Ensure UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
PREV_RESULTS_FILE = f"{ROOT}/logs/prev_results.json"
LIVE_CASES_FILE = f"{ROOT}/logs/latest_live_cases.jsonl"
EXPECTED_RESPONSES_FILE = f"{ROOT}/specs/expected_responses/live_profiles.json"
UNSUPPORTED_PATHS_FILE = f"{ROOT}/specs/shared/unsupported_path_vectors.json"


def _resolve_dotnet_client():
    exe = f"{ROOT}/clients/dotnet/SlmpVerifyClient/bin/Debug/net9.0/SlmpVerifyClient.exe"
    dll = f"{ROOT}/clients/dotnet/SlmpVerifyClient/bin/Debug/net9.0/SlmpVerifyClient.dll"
    return [exe] if os.path.exists(exe) else ["dotnet", dll]


def _resolve_cpp_client():
    exe = f"{ROOT}/clients/cpp/cpp_verify_client.exe"
    native = f"{ROOT}/clients/cpp/cpp_verify_client"
    return [exe] if os.path.exists(exe) else [native]


def _resolve_rust_client():
    native = f"{ROOT}/../plc-comm-slmp-rust/target/debug/slmp_verify_client"
    exe = f"{ROOT}/../plc-comm-slmp-rust/target/debug/slmp_verify_client.exe"
    manifest = f"{ROOT}/../plc-comm-slmp-rust/Cargo.toml"
    if os.path.exists(exe):
        return [exe]
    if os.path.exists(native):
        return [native]
    return ["cargo", "run", "--quiet", "--manifest-path", manifest, "--features", "cli", "--bin", "slmp_verify_client", "--"]

CLIENTS = {
    "python": [sys.executable, f"{ROOT}/clients/python/client_wrapper.py"],
    "dotnet": _resolve_dotnet_client(),
    "cpp":    _resolve_cpp_client(),
    "node-red": ["node", f"{ROOT}/clients/node/client_wrapper.js"],
    "rust": _resolve_rust_client(),
}
CLIENT_ORDER = tuple(CLIENTS.keys())
CLIENT_ALIASES = {
    "python": "python",
    "dotnet": "dotnet",
    "cpp": "cpp",
    "rust": "rust",
    "node": "node-red",
    "node-red": "node-red",
    "nodered": "node-red",
}

HOST = "127.0.0.1"
PORT = 9000

# Python + .NET only (C++ lacks readBitsModuleBuf support)
CLIENTS_NO_CPP = {k: CLIENTS[k] for k in ("python", "dotnet", "node-red", "rust")}


# ---------------------------------------------------------------------------
# Test cases
# Format: (name, command, address, extra_args, flags, clients, expect_error)
#   flags : dict of --key -> value; rendered as ["--key", "value", ...]
#   clients: dict (CLIENTS or CLIENTS_DP)
#   expect_error: if True, expect status="error" from all clients
# ---------------------------------------------------------------------------
def _t(name, cmd, addr, extra=None, flags=None, clients=None, expect_error=False, meta=None):
    return (
        name,
        cmd,
        addr,
        extra or [],
        flags or {},
        clients or CLIENTS,
        expect_error,
        merge_case_meta(name, meta or {}),
    )


def load_expected_case_metadata(path=EXPECTED_RESPONSES_FILE):
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}

    cases = payload.get("cases", {})
    return cases if isinstance(cases, dict) else {}


EXPECTED_CASE_METADATA = load_expected_case_metadata()


def merge_case_meta(name, inline_meta):
    merged = dict(inline_meta or {})
    file_meta = EXPECTED_CASE_METADATA.get(name)
    if isinstance(file_meta, dict):
        merged.update(file_meta)
    return merged


def resolve_case_clients(value):
    if value in (None, "all"):
        return CLIENTS
    if value == "no-cpp":
        return CLIENTS_NO_CPP
    if isinstance(value, list):
        return {name: CLIENTS[name] for name in value}
    raise ValueError(f"unsupported clients selector: {value!r}")


def load_unsupported_path_tests(path=UNSUPPORTED_PATHS_FILE):
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return []

    tests = []
    for item in payload.get("cases", []):
        tests.append(
            _t(
                item["name"],
                item["command"],
                item.get("address", ""),
                item.get("extra") or [],
                item.get("flags") or {},
                resolve_case_clients(item.get("clients")),
                bool(item.get("expect_error", True)),
                meta=item.get("meta"),
            )
        )
    return tests


WALK_FLAGS_4E_IQR = {"frame": "4e", "series": "iqr"}


def _named_updates(assignments):
    return ",".join(f"{address}={value}" for address, value in assignments)


def _seeded_assignments(addresses, seed, low, high):
    rng = random.Random(seed)
    return [(address, rng.randint(low, high)) for address in addresses]


def _build_named_toggle_cases(prefix, addresses, flags):
    read_target = ",".join(addresses)
    updates_on = _named_updates((address, 1) for address in addresses)
    updates_off = _named_updates((address, 0) for address in addresses)
    return [
        _t(f"{prefix} Write ON #1", "write-named", updates_on, [], flags),
        _t(f"{prefix} Read  ON #1", "read-named", read_target, [], flags),
        _t(f"{prefix} Write OFF #1", "write-named", updates_off, [], flags),
        _t(f"{prefix} Read  OFF #1", "read-named", read_target, [], flags),
        _t(f"{prefix} Write ON #2", "write-named", updates_on, [], flags),
        _t(f"{prefix} Read  ON #2", "read-named", read_target, [], flags),
        _t(f"{prefix} Write OFF #2", "write-named", updates_off, [], flags),
        _t(f"{prefix} Read  OFF #2", "read-named", read_target, [], flags),
    ]


def _build_named_dual_write_cases(prefix, addresses, flags, seed, low, high):
    read_target = ",".join(addresses)
    updates_a = _named_updates(_seeded_assignments(addresses, seed, low, high))
    updates_b = _named_updates(_seeded_assignments(addresses, seed + 1, low, high))
    return [
        _t(f"{prefix} Write SET A", "write-named", updates_a, [], flags),
        _t(f"{prefix} Read  SET A", "read-named", read_target, [], flags),
        _t(f"{prefix} Write SET B", "write-named", updates_b, [], flags),
        _t(f"{prefix} Read  SET B", "read-named", read_target, [], flags),
    ]


def _build_ext_bit_toggle_cases(devices, flags):
    cases = []
    sequence = [("ON #1", 1), ("OFF #1", 0), ("ON #2", 1), ("OFF #2", 0)]
    for device in devices:
        ext_flags = dict(flags, mode="bit")
        for label, value in sequence:
            cases.append(_t(f"4E iQR Walk {device} Ext Bit Write {label}", "write-ext", device, [value], ext_flags))
            cases.append(_t(f"4E iQR Walk {device} Ext Bit Read  {label}", "read-ext", device, [1], ext_flags))
    return cases


def _build_ext_word_dual_write_cases(devices, flags, seed, low, high):
    cases = []
    writes_a = dict(_seeded_assignments(devices, seed, low, high))
    writes_b = dict(_seeded_assignments(devices, seed + 1, low, high))
    for device in devices:
        cases.append(_t(f"4E iQR Walk {device} Ext Word Write SET A", "write-ext", device, [writes_a[device]], flags))
        cases.append(_t(f"4E iQR Walk {device} Ext Word Read  SET A", "read-ext", device, [1], flags))
        cases.append(_t(f"4E iQR Walk {device} Ext Word Write SET B", "write-ext", device, [writes_b[device]], flags))
        cases.append(_t(f"4E iQR Walk {device} Ext Word Read  SET B", "read-ext", device, [1], flags))
    return cases


def build_automated_device_walk_cases():
    common_bits = [
        "STS10", "STC10", "TS10", "TC10", "CS10", "CC10", "SB10", "DX10", "DY10",
        "X10", "Y10", "M10", "L10", "F100", "V10", "B10", "SM10",
    ]
    common_words = [
        "STN10", "TN10", "CN10", "SW10", "ZR10", "D10", "W10", "Z10", "R10", "SD10", "RD10",
    ]
    long_bits = ["LTS10", "LTC10", "LSTS10", "LSTC10"]
    long_counter_bits = ["LCS10", "LCC10"]
    long_dwords = ["LTN10:D", "LSTN10:D", "LCN10:D", "LZ0:D", "LZ1:D"]
    ext_bits = [r"J1\X10", r"J1\Y10", r"J1\B10", r"J1\SB10"]
    ext_words = [r"J1\W10", r"J1\SW10"]

    cases = []
    cases.extend(_build_named_toggle_cases(
        "4E iQR Walk Bits STS10/STC10/TS10/TC10/CS10/CC10/SB10/DX10/DY10/X10/Y10/M10/L10/F100/V10/B10/SM10",
        common_bits,
        WALK_FLAGS_4E_IQR,
    ))
    cases.extend(_build_named_dual_write_cases(
        "4E iQR Walk Words STN10/TN10/CN10/SW10/ZR10/D10/W10/Z10/R10/SD10/RD10",
        common_words,
        WALK_FLAGS_4E_IQR,
        2026041301,
        1000,
        65000,
    ))
    cases.extend(_build_named_toggle_cases(
        "4E iQR Walk LongBits LTS10/LTC10/LSTS10/LSTC10",
        long_bits,
        WALK_FLAGS_4E_IQR,
    ))
    cases.extend(_build_named_toggle_cases(
        "4E iQR Walk LongCounterBits LCS10/LCC10",
        long_counter_bits,
        WALK_FLAGS_4E_IQR,
    ))
    cases.extend(_build_named_dual_write_cases(
        "4E iQR Walk LongDWords LTN10/LSTN10/LCN10/LZ0/LZ1",
        long_dwords,
        WALK_FLAGS_4E_IQR,
        2026041302,
        100000,
        2_000_000_000,
    ))
    cases.extend(_build_ext_bit_toggle_cases(ext_bits, WALK_FLAGS_4E_IQR))
    cases.extend(_build_ext_word_dual_write_cases(
        ext_words,
        WALK_FLAGS_4E_IQR,
        2026041303,
        1000,
        65000,
    ))
    return cases


TESTS = [
    # ===== 3E QL Word Read/Write =====
    _t("3E QL D   Word Write [10,20,30]",   "write", "D100",  [10, 20, 30]),
    _t("3E QL D   Word Read  3pts",          "read",  "D100",  [3]),
    _t("3E QL W   Word Write [100,200]",    "write", "W10",   [100, 200]),
    _t("3E QL W   Word Read  2pts",          "read",  "W10",   [2]),
    _t("3E QL R   Word Write [500]",         "write", "R50",   [500]),
    _t("3E QL R   Word Read  1pt",           "read",  "R50",   [1]),
    _t("3E QL ZR  Word Write [999,1000]",    "write", "ZR0",   [999, 1000]),
    _t("3E QL ZR  Word Read  2pts",          "read",  "ZR0",   [2]),
    _t("3E QL SM  Word Read  2pts",          "read",  "SM0",   [2]),
    _t("3E QL SD  Word Read  2pts",          "read",  "SD0",   [2]),
    # Timer
    _t("3E QL TN  Word Write [100,200]",     "write", "TN0",   [100, 200]),
    _t("3E QL TN  Word Read  2pts",          "read",  "TN0",   [2]),
    _t("3E QL TS  Bit  Write [1,0,1,0]",    "write", "TS0",   [1, 0, 1, 0], {"mode": "bit"}),
    _t("3E QL TS  Bit  Read  4pts",          "read",  "TS0",   [4],           {"mode": "bit"}),
    _t("3E QL TC  Bit  Write [0,1,0,1]",    "write", "TC0",   [0, 1, 0, 1], {"mode": "bit"}),
    _t("3E QL TC  Bit  Read  4pts",          "read",  "TC0",   [4],           {"mode": "bit"}),
    # Retentive Timer
    _t("3E QL STN Word Write [200,300]",     "write", "STN0",  [200, 300]),
    _t("3E QL STN Word Read  2pts",          "read",  "STN0",  [2]),
    _t("3E QL STS Bit  Write [0,1]",         "write", "STS0",  [0, 1],        {"mode": "bit"}),
    _t("3E QL STS Bit  Read  2pts",          "read",  "STS0",  [2],           {"mode": "bit"}),
    _t("3E QL STC Bit  Write [1,0]",         "write", "STC0",  [1, 0],        {"mode": "bit"}),
    _t("3E QL STC Bit  Read  2pts",          "read",  "STC0",  [2],           {"mode": "bit"}),
    # Counter
    _t("3E QL CN  Word Write [50,60]",       "write", "CN0",   [50, 60]),
    _t("3E QL CN  Word Read  2pts",          "read",  "CN0",   [2]),
    _t("3E QL CS  Bit  Write [1,0]",         "write", "CS0",   [1, 0],        {"mode": "bit"}),
    _t("3E QL CS  Bit  Read  2pts",          "read",  "CS0",   [2],           {"mode": "bit"}),
    _t("3E QL CC  Bit  Write [0,1]",         "write", "CC0",   [0, 1],        {"mode": "bit"}),
    _t("3E QL CC  Bit  Read  2pts",          "read",  "CC0",   [2],           {"mode": "bit"}),
    # Other bit devices
    _t("3E QL L   Bit  Write [1,0,1,0]",    "write", "L0",    [1, 0, 1, 0],  {"mode": "bit"}),
    _t("3E QL L   Bit  Read  4pts",          "read",  "L0",    [4],            {"mode": "bit"}),
    _t("3E QL F   Bit  Write [1,0,1,0]",    "write", "F0",    [1, 0, 1, 0],  {"mode": "bit"}),
    _t("3E QL F   Bit  Read  4pts",          "read",  "F0",    [4],            {"mode": "bit"}),
    _t("3E QL V   Bit  Write [0,1,0,1]",    "write", "V0",    [0, 1, 0, 1],  {"mode": "bit"}),
    _t("3E QL V   Bit  Read  4pts",          "read",  "V0",    [4],            {"mode": "bit"}),
    _t("3E QL DX  Bit  Read  4pts",          "read",  "DX0",   [4],            {"mode": "bit"}),
    _t("3E QL DY  Bit  Write [1,0,1,0]",    "write", "DY0",   [1, 0, 1, 0],  {"mode": "bit"}),
    _t("3E QL DY  Bit  Read  4pts",          "read",  "DY0",   [4],            {"mode": "bit"}),
    # Index registers
    _t("3E QL Z   Word Write [10]",          "write", "Z0",    [10]),
    _t("3E QL Z   Word Read  1pt",           "read",  "Z0",    [1]),
    _t("3E iQR LZ  Random Write DWord",      "random-write-words", "", [],    {"series": "iqr", "dwords": "LZ0=20"}),
    _t("3E iQR LZ  Random Read  DWord",      "random-read", "", [],           {"series": "iqr", "dword-devs": "LZ0"}),

    # ===== 3E QL Bit Read/Write =====
    _t("3E QL M   Bit Write [1,0,1,0]",     "write", "M0",    [1, 0, 1, 0], {"mode": "bit"}),
    _t("3E QL M   Bit Read  4pts",           "read",  "M0",    [4],           {"mode": "bit"}),
    _t("3E QL Y   Bit Write [1,1,0,0]",     "write", "Y0",    [1, 1, 0, 0],  {"mode": "bit"}),
    _t("3E QL Y   Bit Read  4pts",           "read",  "Y0",    [4],           {"mode": "bit"}),
    _t("3E QL X   Bit Read  4pts",           "read",  "X0",    [4],           {"mode": "bit"}),
    _t("3E QL B   Bit Write [0,1,0,1]",     "write", "B0",    [0, 1, 0, 1],  {"mode": "bit"}),
    _t("3E QL B   Bit Read  4pts",           "read",  "B0",    [4],           {"mode": "bit"}),
    _t("3E QL SB  Bit Read  4pts",           "read",  "SB0",   [4],           {"mode": "bit"}),
    _t("3E iQR SW  Bit Read  4pts",          "read",  "SW0",   [4],           {"series": "iqr", "mode": "bit"}),

    # ===== 3E QL DWord =====
    _t("3E QL D   DWord Write [100000,200000]", "write", "D200", [100000, 200000], {"mode": "dword"}),
    _t("3E QL D   DWord Read  2pts",             "read",  "D200", [2],              {"mode": "dword"}),
    _t("3E QL W   DWord Write [300000]",         "write", "W20",  [300000],         {"mode": "dword"}),
    _t("3E QL W   DWord Read  1pt",              "read",  "W20",  [1],              {"mode": "dword"}),

    # ===== 3E QL Float32 =====
    _t("3E QL D   Float32 Write [3.14,2.71]",  "write", "D300", [3.14, 2.71], {"mode": "float"}),
    _t("3E QL D   Float32 Read  2pts",          "read",  "D300", [2],           {"mode": "float"}),

    # ===== 4E Frame =====
    _t("4E QL D   Word Write [1000,2000]",   "write", "D400", [1000, 2000], {"frame": "4e"}),
    _t("4E QL D   Word Read  2pts",           "read",  "D400", [2],          {"frame": "4e"}),
    _t("4E QL M   Bit Write [1,0,1]",        "write", "M100", [1, 0, 1],    {"frame": "4e", "mode": "bit"}),
    _t("4E QL M   Bit Read  3pts",            "read",  "M100", [3],          {"frame": "4e", "mode": "bit"}),
    _t("4E QL D   DWord Write [999999]",      "write", "D450", [999999],     {"frame": "4e", "mode": "dword"}),
    _t("4E QL D   DWord Read  1pt",           "read",  "D450", [1],          {"frame": "4e", "mode": "dword"}),

    # ===== iQR Series =====
    _t("3E iQR D  Word Write [111,222]",      "write", "D500", [111, 222],   {"series": "iqr"}),
    _t("3E iQR D  Word Read  2pts",            "read",  "D500", [2],          {"series": "iqr"}),
    _t("3E iQR M  Bit Write [0,1,0,1]",       "write", "M200", [0, 1, 0, 1], {"series": "iqr", "mode": "bit"}),
    _t("3E iQR M  Bit Read  4pts",             "read",  "M200", [4],          {"series": "iqr", "mode": "bit"}),
    _t("3E iQR D  DWord Write [70000]",        "write", "D550", [70000],      {"series": "iqr", "mode": "dword"}),
    _t("3E iQR D  DWord Read  1pt",            "read",  "D550", [1],          {"series": "iqr", "mode": "dword"}),
    _t("4E iQR D  Word Write [333,444]",       "write", "D600", [333, 444],   {"frame": "4e", "series": "iqr"}),
    _t("4E iQR D  Word Read  2pts",            "read",  "D600", [2],          {"frame": "4e", "series": "iqr"}),
    _t("4E iQR M  Bit Write [1,1,0]",         "write", "M300", [1, 1, 0],    {"frame": "4e", "series": "iqr", "mode": "bit"}),
    _t("4E iQR M  Bit Read  3pts",             "read",  "M300", [3],          {"frame": "4e", "series": "iqr", "mode": "bit"}),
    _t(
        "3E iQR LTN/LSTN/LCN/LZ Random Write DWords",
        "random-write-words",
        "",
        [],
        {"series": "iqr", "dwords": "LTN10=123456,LSTN20=234567,LCN30=999,LZ0=1234"},
    ),
    _t(
        "3E iQR LTN/LSTN/LCN/LZ Random Read  DWords",
        "random-read",
        "",
        [],
        {"series": "iqr", "dword-devs": "LTN10,LSTN20,LCN30,LZ0"},
    ),
    _t("3E iQR LCS Random Bit Write [1,0]",  "random-write-bits", "", [],     {"series": "iqr", "bits": "LCS30=1,LCS31=0"}),
    _t("3E iQR LCS Bit  Read  2pts",          "read",  "LCS30", [2],          {"series": "iqr", "mode": "bit"}),
    _t("3E iQR LCC Random Bit Write [0,1]",  "random-write-bits", "", [],     {"series": "iqr", "bits": "LCC40=0,LCC41=1"}),
    _t("3E iQR LCC Bit  Read  2pts",          "read",  "LCC40", [2],          {"series": "iqr", "mode": "bit"}),

    # ===== Routing (Other Station) =====
    _t("Routing NW1-ST2 D  Word Write [5000]", "write", "D700", [5000], {"target": "1,2,1023,0"}),
    _t("Routing NW1-ST2 D  Word Read  1pt",    "read",  "D700", [1],    {"target": "1,2,1023,0"}),
    _t(
        "Routing NW2-ST3 M  Bit Write [1,0]",
        "write",
        "M400",
        [1, 0],
        {"target": "2,3,1023,0", "mode": "bit"},
    ),
    _t(
        "Routing NW2-ST3 M  Bit Read  2pts",
        "read",
        "M400",
        [2],
        {"target": "2,3,1023,0", "mode": "bit"},
    ),

    # ===== Random Access =====
    _t("3E QL Random Write Words D130=10,D131=20 DW:D230=65537",
       "random-write-words", "", [], {"words": "D130=10,D131=20", "dwords": "D230=65537"}),
    _t("3E QL Random Read  Words D130+D131,DW:D230",
       "random-read",        "", [], {"word-devs": "D130,D131", "dword-devs": "D230"}),
    _t("3E QL Random Write Bits  M10=1,M11=0,M12=1",
       "random-write-bits",  "", [], {"bits": "M10=1,M11=0,M12=1"}),
    _t("4E QL Random Write Words D150=100,D151=200",
       "random-write-words", "", [], {"words": "D150=100,D151=200", "frame": "4e"}),
    _t("4E QL Random Read  Words D150+D151",
       "random-read",        "", [], {"word-devs": "D150,D151", "frame": "4e"}),

    # ===== Block Access =====
    _t(
        "3E QL Block Write D800=10:20:30 / M500=1:0:1",
        "block-write",
        "",
        [],
        {"word-blocks": "D800=10:20:30", "bit-blocks": "M500=1:0:1"},
    ),
    _t(
        "3E QL Block Read  D800x3 / M500x3",
        "block-read",
        "",
        [],
        {"word-blocks": "D800=3", "bit-blocks": "M500=3"},
    ),
    _t("4E QL Block Write D850=5:6",
       "block-write", "", [], {"word-blocks": "D850=5:6", "frame": "4e"}),
    _t("4E QL Block Read  D850x2",
       "block-read",  "", [], {"word-blocks": "D850=2", "frame": "4e"}),

    # ===== Remote Operations =====
    _t("3E Remote RUN",         "remote-run",         "", []),
    _t("3E Remote STOP",        "remote-stop",        "", []),
    _t("3E Remote PAUSE",       "remote-pause",       "", []),
    _t("3E Remote LATCH CLEAR", "remote-latch-clear", "", []),
    _t("4E Remote RUN",         "remote-run",         "", [], {"frame": "4e"}),
    _t("4E Remote STOP",        "remote-stop",        "", [], {"frame": "4e"}),
    _t("4E iQR Remote RUN",     "remote-run",         "", [], {"frame": "4e", "series": "iqr"}),

    # ===== Self Test =====
    _t("3E Self Test HELLO",           "self-test", "HELLO",         []),
    _t("4E Self Test LOOPBACK_4E",     "self-test", "LOOPBACK_4E",   [], {"frame": "4e"}),

    # ===== Type Name =====
    _t("3E Read Type Name", "read-type", "", []),
    _t("4E Read Type Name", "read-type", "", [], {"frame": "4e"}),

    # ===== High-Level Named Snapshot =====
    _t("3E QL Named Write D910",
       "write-named", "D910=321", []),
    _t("3E QL Named Read  D910 / D920:F / D930.3",
       "read-named", "D910,D920:F,D930.3", []),
    _t("3E QL Poll Once   D910 / D920:F / D930.3",
       "poll-once", "D910,D920:F,D930.3", []),
    _t(
        "3E iQR Named Write Z/LZ:D/LTN:D/LSTN:D/LCN:D",
        "write-named",
        "Z10=321,LZ0:D=654,LTN10:D=777,LSTN20:D=888,LCN30:D=999",
        [],
        {"series": "iqr"},
    ),
    _t(
        "3E iQR Named Read  Z/LZ:D/LTN:D/LSTN:D/LCN:D",
        "read-named",
        "Z10,LZ0:D,LTN10:D,LSTN20:D,LCN30:D",
        [],
        {"series": "iqr"},
    ),
    _t(
        "3E iQR Poll Once   Z/LZ:D/LTN:D/LSTN:D/LCN:D",
        "poll-once",
        "Z10,LZ0:D,LTN10:D,LSTN20:D,LCN30:D",
        [],
        {"series": "iqr"},
    ),
    _t("3E iQR Named Write RD:D",
       "write-named", "RD10:D=305419896", [], {"series": "iqr"}),
    _t("3E iQR Named Read  RD:D",
       "read-named", "RD10:D", [], {"series": "iqr"}),
    _t("3E iQR Poll Once   RD:D",
       "poll-once", "RD10:D", [], {"series": "iqr"}),
    _t(
        "3E iQR Named Write LTS/LTC/LSTS/LSTC",
        "write-named",
        "LTS10=1,LTC11=0,LSTS20=1,LSTC21=0",
        [],
        {"series": "iqr"},
    ),
    _t(
        "3E iQR Named Read  LTS/LTC/LSTS/LSTC",
        "read-named",
        "LTS10,LTC11,LSTS20,LSTC21",
        [],
        {"series": "iqr"},
    ),
    _t(
        "3E iQR Poll Once   LTS/LTC/LSTS/LSTC",
        "poll-once",
        "LTS10,LTC11,LSTS20,LSTC21",
        [],
        {"series": "iqr"},
    ),

    # ===== Memory Read/Write =====
    _t("Memory Write 0x100 [100,200,300]",    "memory-write", "0x100", [100, 200, 300]),
    _t("Memory Read  0x100 3pts",             "memory-read",  "0x100", [3]),
    _t("Memory Write 0x200 [500]",            "memory-write", "0x200", [500]),
    _t("Memory Read  0x200 1pt",              "memory-read",  "0x200", [1]),
    _t("4E Memory Write 0x300 [11,22]",       "memory-write", "0x300", [11, 22],   {"frame": "4e"}),
    _t("4E Memory Read  0x300 2pts",          "memory-read",  "0x300", [2],         {"frame": "4e"}),

    # ===== Extend Unit Read/Write =====
    _t("ExtUnit Write 0x3E0:0x0 [111,222]",      "extend-unit-write", "0x3E0:0x0",  [111, 222]),
    _t("ExtUnit Read  0x3E0:0x0 2pts",           "extend-unit-read",  "0x3E0:0x0",  [2]),
    _t("ExtUnit Write 0x3E1:0x10 [999]",         "extend-unit-write", "0x3E1:0x10", [999]),
    _t("ExtUnit Read  0x3E1:0x10 1pt",           "extend-unit-read",  "0x3E1:0x10", [1]),
    _t("4E ExtUnit Write 0x3E0:0x0 [555,666]",   "extend-unit-write", "0x3E0:0x0",  [555, 666], {"frame": "4e"}),
    _t("4E ExtUnit Read  0x3E0:0x0 2pts",        "extend-unit-read",  "0x3E0:0x0",  [2],         {"frame": "4e"}),

    # ===== Extended Address Extended Device (all 3 clients) =====
    _t("3E J1\\SW0  Ext Word Write [50,60]", "write-ext", "J1\\SW0",  [50, 60]),
    _t("3E J1\\SW0  Ext Word Read  2pts",    "read-ext",  "J1\\SW0",  [2]),
    _t("3E J1\\SW0  Ext Bit Write [1,0,1]",  "write-ext", "J1\\SW0",  [1, 0, 1], {"mode": "bit"}),
    _t("3E J1\\SW0  Ext Bit Read  3pts",     "read-ext",  "J1\\SW0",  [3],       {"mode": "bit"}),
    _t("3E U3\\G100 Ext Word Write [11,22]",  "write-ext", "U3\\G100", [11, 22]),
    _t("3E U3\\G100 Ext Word Read  2pts",     "read-ext",  "U3\\G100", [2]),
    _t("3E U3\\G100 Ext Bit Write [1,0,1]",   "write-ext", "U3\\G100", [1, 0, 1], {"mode": "bit"}),
    _t("3E U3\\G100 Ext Bit Read  3pts",      "read-ext",  "U3\\G100", [3],       {"mode": "bit"}),
    _t("3E U1\\HG0  Ext Word Write [33,44]",  "write-ext", "U1\\HG0",  [33, 44]),
    _t("3E U1\\HG0  Ext Word Read  2pts",     "read-ext",  "U1\\HG0",  [2]),
    _t("4E J1\\SW0  Ext Word Write [70,80]", "write-ext", "J1\\SW0",  [70, 80],   {"frame": "4e"}),
    _t("4E J1\\SW0  Ext Word Read  2pts",    "read-ext",  "J1\\SW0",  [2],         {"frame": "4e"}),

    # ===== Error / NG Conditions =====
    _t("NG 3E Data Length Over 1001pts",      "read", "D0", [1001], {},           CLIENTS, True),
    _t("NG 4E Data Length Over 1001pts",      "read", "D0", [1001], {"frame": "4e"}, CLIENTS, True),
]

TESTS.extend(load_unsupported_path_tests())
TESTS.extend(build_automated_device_walk_cases())


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run the SLMP verification harness in full parity mode or in a "
            "filtered single-client debug mode."
        )
    )
    parser.add_argument("--host", default=HOST, help=f"Mock server host (default: {HOST})")
    parser.add_argument("--port", type=int, default=PORT, help=f"Mock server port (default: {PORT})")
    parser.add_argument(
        "--clients",
        default="all",
        help="Comma-separated clients to run: all, python, dotnet, cpp, node-red, rust",
    )
    parser.add_argument(
        "--case-pattern",
        default="",
        help="Case-insensitive substring filter for test names",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="List selected cases and exit without starting the mock server",
    )
    parser.add_argument(
        "--write-latest",
        action="store_true",
        help=(
            "Allow filtered runs to overwrite latest_* artifacts. By default, "
            "filtered runs write timestamped files only."
        ),
    )
    return parser.parse_args()


def parse_selected_clients(raw_value):
    raw_value = (raw_value or "all").strip().lower()
    if raw_value in {"", "all"}:
        return list(CLIENT_ORDER)

    selected = []
    seen = set()
    for item in raw_value.split(","):
        key = CLIENT_ALIASES.get(item.strip().lower())
        if key is None:
            valid = ", ".join(CLIENT_ORDER)
            raise SystemExit(f"unknown client '{item.strip()}'; valid values: {valid}, all")
        if key not in seen:
            selected.append(key)
            seen.add(key)
    return selected


def case_matches_pattern(name, pattern):
    return not pattern or pattern.lower() in name.lower()


def list_selected_cases(selected_clients, case_pattern):
    print("Selected cases:")
    matched = 0
    runnable = 0
    for test in TESTS:
        name, cmd, addr, extra, flags, clients, expect_error, _meta = test
        if not case_matches_pattern(name, case_pattern):
            continue
        matched += 1
        resolved = resolve_clients(cmd, addr, extra, flags, clients, selected_clients)
        scope = ",".join(resolved.keys()) if resolved else "skip(out-of-scope)"
        if resolved:
            runnable += 1
        print(f"- [{scope}] {name}")
    print(f"\nTotal matched cases: {matched}")
    print(f"Runnable cases: {runnable}")


def should_write_latest(selected_clients, case_pattern, force_write_latest):
    return force_write_latest or (selected_clients == list(CLIENT_ORDER) and not case_pattern)


def count_selected_cases(selected_clients, case_pattern):
    matched = 0
    runnable = 0
    for test in TESTS:
        name, cmd, addr, extra, flags, clients, expect_error, _meta = test
        if not case_matches_pattern(name, case_pattern):
            continue
        matched += 1
        if resolve_clients(cmd, addr, extra, flags, clients, selected_clients):
            runnable += 1
    return matched, runnable


# ---------------------------------------------------------------------------
# Previous results persistence
# ---------------------------------------------------------------------------
def load_prev_results():
    try:
        with open(PREV_RESULTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_results(results_dict):
    try:
        os.makedirs(os.path.dirname(PREV_RESULTS_FILE), exist_ok=True)
        with open(PREV_RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(results_dict, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Human-readable test description generator
# ---------------------------------------------------------------------------
def generate_desc(cmd, addr, extra, flags, clients, expect_error=False):
    mode = flags.get("mode", "word")
    frame = flags.get("frame", "3e").upper()
    series = "iQR" if flags.get("series", "ql") == "iqr" else "QL"
    target = flags.get("target", "")

    ctx = f"{frame}/{series}"
    if target:
        p = target.split(",")
        ctx += f" via NW{p[0]}-ST{p[1]}"
    ctx += f" {len(clients)}client"

    mode_jp = {"word": "Word", "bit": "Bit", "dword": "DWord", "float": "Float32"}.get(mode, mode)
    err_sfx = " <- Verify error response" if expect_error else ""

    if cmd == "write":
        return f"{addr} <- {mode_jp}{list(extra)}  [{ctx}]{err_sfx}"
    elif cmd == "read":
        return f"{addr} -> {extra[0] if extra else 1}pts {mode_jp}  [{ctx}]{err_sfx}"
    elif cmd == "random-write-words":
        parts = ([f"W:{flags['words']}"] if flags.get("words") else []) + \
                ([f"DW:{flags['dwords']}"] if flags.get("dwords") else [])
        return f"Random word write {' / '.join(parts)}  [{ctx}]"
    elif cmd == "random-read":
        parts = ([f"W:{flags['word-devs']}"] if flags.get("word-devs") else []) + \
                ([f"DW:{flags['dword-devs']}"] if flags.get("dword-devs") else [])
        return f"Random read {' / '.join(parts)}  [{ctx}]"
    elif cmd == "random-write-bits":
        return f"Random bit write {flags.get('bits', '')}  [{ctx}]"
    elif cmd == "block-write":
        parts = ([f"W:{flags['word-blocks']}"] if flags.get("word-blocks") else []) + \
                ([f"B:{flags['bit-blocks']}"] if flags.get("bit-blocks") else [])
        return f"Block write {' / '.join(parts)}  [{ctx}]"
    elif cmd == "block-read":
        parts = ([f"W:{flags['word-blocks']}"] if flags.get("word-blocks") else []) + \
                ([f"B:{flags['bit-blocks']}"] if flags.get("bit-blocks") else [])
        return f"Block read {' / '.join(parts)}  [{ctx}]"
    elif cmd in ("remote-run", "remote-stop", "remote-pause", "remote-latch-clear", "remote-reset"):
        op = cmd.replace("remote-", "").upper().replace("-", " ")
        return f"Remote {op} command sent  [{ctx}]"
    elif cmd == "self-test":
        return f"Self-test: '{addr}' echo back check  [{ctx}]"
    elif cmd == "read-type":
        return f"Read model name (model name + code)  [{ctx}]"
    elif cmd == "write-named":
        return f"Write named snapshot {addr}  [{ctx}]"
    elif cmd == "read-named":
        return f"Read named snapshot {addr}  [{ctx}]"
    elif cmd == "poll-once":
        return f"Poll one named snapshot {addr}  [{ctx}]"
    elif cmd == "memory-write":
        return f"Memory {addr} <- {list(extra)}  [{ctx}]"
    elif cmd == "memory-read":
        return f"Memory {addr} -> {extra[0] if extra else 1} word read  [{ctx}]"
    elif cmd == "extend-unit-write":
        mod, head = (addr.split(":") + ["0"])[:2]
        return f"Extension unit mod={mod} head={head} <- {list(extra)}  [{ctx}]"
    elif cmd == "extend-unit-read":
        mod, head = (addr.split(":") + ["0"])[:2]
        return f"Extension unit mod={mod} head={head} -> {extra[0] if extra else 1} words  [{ctx}]"
    elif cmd == "write-ext":
        kind = "Link Direct" if addr.upper().startswith("J") else "Buffer Memory"
        return f"ExtendedDevice({kind}) {addr} <- {mode_jp}{list(extra)}  [{ctx}]"
    elif cmd == "read-ext":
        kind = "Link Direct" if addr.upper().startswith("J") else "Buffer Memory"
        return f"ExtendedDevice({kind}) {addr} -> {extra[0] if extra else 1}pts {mode_jp}  [{ctx}]"
    else:
        return f"{cmd} {addr} {list(extra)}  [{ctx}]{err_sfx}"


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------
def build_cmd_args(command, address, extra, flags):
    """Build the argument list appended to [HOST, PORT, command, address]."""
    flag_args = []
    for k, v in flags.items():
        flag_args += [f"--{k}", str(v)]
    extra_args = [str(a) for a in extra]
    return flag_args + extra_args


def node_red_supports(command, _address, _extra, _flags):
    return command in {
        "read",
        "write",
        "read-ext",
        "write-ext",
        "read-type",
        "random-read",
        "random-write-words",
        "random-write-bits",
        "monitor-register",
        "block-read",
        "block-write",
        "read-named",
        "write-named",
        "poll-once",
    }


def resolve_clients(command, address, extra, flags, clients, selected_clients=None):
    resolved = dict(clients)
    if node_red_supports(command, address, extra, flags):
        resolved["node-red"] = CLIENTS["node-red"]
    else:
        resolved.pop("node-red", None)
    if selected_clients is not None:
        resolved = {name: resolved[name] for name in selected_clients if name in resolved}
    return resolved


def run_client(client_name, command, address, extra, flags):
    cmd_prefix = CLIENTS[client_name]
    exe_path = cmd_prefix[0]
    if exe_path not in {"python", "python3", "node", "dotnet"} and not os.path.exists(exe_path):
        return {"status": "error", "message": f"missing executable: {exe_path}"}
    if exe_path == "dotnet" and (len(cmd_prefix) < 2 or not os.path.exists(cmd_prefix[1])):
        return {"status": "error", "message": f"missing executable: {cmd_prefix[1] if len(cmd_prefix) > 1 else 'dotnet target'}"}
    extra_args = build_cmd_args(command, address, extra, flags)
    if client_name == "python":
        extra_args = [str(a) for a in extra] + [item for kv in flags.items() for item in (f"--{kv[0]}", str(kv[1]))]
    cmd = cmd_prefix + [HOST, str(PORT), command, address] + extra_args
    env = None
    if client_name == "cpp":
        env = os.environ.copy()
        msys_bin = r"C:\msys64\ucrt64\bin"
        if os.path.isdir(msys_bin):
            env["PATH"] = msys_bin + os.pathsep + env.get("PATH", "")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=6, env=env)
        if result.returncode != 0:
            return {"status": "error", "message": f"Exit {result.returncode}: {result.stderr.strip()[:200]}"}
        stdout = result.stdout.strip()
        if not stdout:
            return {"status": "error", "message": f"empty stdout; stderr={result.stderr.strip()[:200]}"}
        return json.loads(stdout)
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "timeout"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def count_log_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def get_new_reqs(path, line_count_before):
    """Return list of (client_session_order, hex_data) for REQ entries added since line_count_before."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        new_lines = lines[line_count_before:]
        reqs = []
        for line in new_lines:
            try:
                e = json.loads(line)
                if e.get("direction") == "REQ":
                    reqs.append(e["data"])
            except Exception:
                pass
        return reqs
    except Exception:
        return []


def get_new_packet_entries(path, line_count_before):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[line_count_before:]
        entries = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
        return entries
    except Exception:
        return []


def normalize_packet(hex_str, frame):
    """Normalize 4E serial bytes (bytes 2-5) to zeros for comparison."""
    b = bytes.fromhex(hex_str)
    if frame == "4e" and len(b) >= 6 and b[:2] == bytes.fromhex("5400"):
        b = b[:2] + b"\x00\x00\x00\x00" + b[6:]
    return b.hex()


def normalize_response(hex_str):
    b = bytes.fromhex(hex_str)
    if len(b) >= 6 and b[:2] == bytes.fromhex("d400"):
        b = b[:2] + b"\x00\x00\x00\x00" + b[6:]
    return b.hex()


def parse_response_end_code(hex_str):
    try:
        b = bytes.fromhex(hex_str)
        if b[:2] == bytes.fromhex("d000"):
            return int.from_bytes(b[9:11], "little")
        if b[:2] == bytes.fromhex("d400"):
            return int.from_bytes(b[13:15], "little")
    except Exception:
        pass
    return None


def response_data_length(hex_str):
    try:
        b = bytes.fromhex(hex_str)
        if b[:2] == bytes.fromhex("d000"):
            return int.from_bytes(b[7:9], "little") - 2
        if b[:2] == bytes.fromhex("d400"):
            return int.from_bytes(b[11:13], "little") - 2
    except Exception:
        pass
    return None


def requires_packet_parity(command):
    return command not in {"read-named", "write-named", "poll-once"}


def log_print(msg, fp=None):
    print(msg)
    if fp:
        fp.write(msg + "\n")
        fp.flush()


def comparable_success_result(command, result):
    if result.get("status") != "success":
        return None
    if command in ("read-named", "poll-once"):
        return {
            "status": "success",
            "addresses": result.get("addresses", []),
            "values": result.get("values", []),
        }
    return None


def results_equivalent(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-6)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(results_equivalent(lv, rv) for lv, rv in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        if left.keys() != right.keys():
            return False
        return all(results_equivalent(left[key], right[key]) for key in left.keys())
    return left == right


def _extract_address_prefixes(address):
    prefixes = []
    for item in str(address or "").split(","):
        base = item.strip()
        if not base:
            continue
        if "=" in base:
            base = base.split("=", 1)[0].strip()
        if "\\" in base:
            base = base.split("\\", 1)[1]
        if "/" in base:
            base = base.split("/", 1)[1]
        if "." in base:
            base = base.split(".", 1)[0]
        if ":" in base and "\\" not in item and "/" not in item:
            base = base.rsplit(":", 1)[0]
        prefix = []
        for ch in base:
            if ch.isalpha():
                prefix.append(ch.upper())
            else:
                break
        if prefix:
            prefixes.append("".join(prefix))
    return prefixes


def determine_live_compare_mode(command, address, flags, expect_error):
    if expect_error:
        return "end_code"
    if command == "read-type":
        return "shape"
    if command in {"remote-run", "remote-stop", "remote-pause", "remote-latch-clear", "remote-reset"}:
        return "end_code"
    dynamic_prefixes = {"SM", "SD", "SB", "SW", "X", "DX"}
    if command in {"read", "read-ext", "read-named", "poll-once"}:
        prefixes = _extract_address_prefixes(address)
        if prefixes and any(prefix in dynamic_prefixes for prefix in prefixes):
            return "shape"
    return "exact"


def determine_live_replay_class(command, address, flags, expect_error):
    if command in {"remote-run", "remote-stop", "remote-pause", "remote-latch-clear", "remote-reset"}:
        return "remote_control"
    if expect_error or command in {"read-type", "self-test"}:
        return "safe"
    if command in {"read", "read-ext"}:
        prefixes = _extract_address_prefixes(address)
        if prefixes and all(prefix in {"SM", "SD", "SB", "SW", "X", "DX"} for prefix in prefixes):
            return "safe"
    return "stateful"


def write_live_case(path, case_payload):
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(case_payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------
def test_case(
    name,
    command,
    address,
    extra,
    flags,
    clients,
    expect_error,
    meta,
    packets_json,
    markers_json,
    live_cases_file,
    log_fp,
    prev_result=None,
    selected_clients=None,
):
    clients = resolve_clients(command, address, extra, flags, clients, selected_clients)
    prev_tag = f"  [Prev:{prev_result}]" if prev_result else ""
    log_print(f"Running: {name}{prev_tag}", log_fp)
    if not clients:
        log_print("  SKIP (no selected clients in scope)", log_fp)
        return "skip"
    desc = generate_desc(command, address, extra, flags, clients, expect_error)
    log_print(f"  {desc}", log_fp)

    results = {}
    reqs_by_client = {}
    resps_by_client = {}
    traces_by_client = {}
    for client_name in clients:
        line_before = count_log_lines(packets_json)
        results[client_name] = run_client(client_name, command, address, extra, flags)
        traces_by_client[client_name] = get_new_packet_entries(packets_json, line_before)
        reqs_by_client[client_name] = [entry["data"] for entry in traces_by_client[client_name] if entry.get("direction") == "REQ"]
        resps_by_client[client_name] = [entry["data"] for entry in traces_by_client[client_name] if entry.get("direction") == "RES"]

    frame = flags.get("frame", "3e")
    client_names = list(clients.keys())

    # --- Status parity check ---
    all_ok = True
    if expect_error:
        for cn, res in results.items():
            if res.get("status") != "error":
                log_print(f"  !!! {cn}: expected ERROR but got SUCCESS: {res}", log_fp)
                all_ok = False
    else:
        ref_name = client_names[0]
        ref_status = results[ref_name].get("status")
        for cn in client_names[1:]:
            if results[cn].get("status") != ref_status:
                log_print(f"  !!! STATUS MISMATCH {ref_name}={ref_status} vs {cn}={results[cn].get('status')}", log_fp)
                log_print(f"      {ref_name}: {results[ref_name].get('message','')}", log_fp)
                log_print(f"      {cn}: {results[cn].get('message','')}", log_fp)
                all_ok = False
        if ref_status == "error" and not expect_error:
            log_print(f"  !!! ALL CLIENTS ERRORED: {results[ref_name].get('message','')}", log_fp)
            all_ok = False
        elif ref_status == "success":
            ref_payload = comparable_success_result(command, results[ref_name])
            if ref_payload is not None:
                ref_json = json.dumps(ref_payload, sort_keys=True, ensure_ascii=False)
                for cn in client_names[1:]:
                    cur_payload = comparable_success_result(command, results[cn])
                    cur_json = json.dumps(cur_payload, sort_keys=True, ensure_ascii=False)
                    if not results_equivalent(ref_payload, cur_payload):
                        log_print(f"  !!! RESULT MISMATCH {ref_name} vs {cn}", log_fp)
                        log_print(f"      {ref_name}: {ref_json}", log_fp)
                        log_print(f"      {cn}: {cur_json}", log_fp)
                        all_ok = False

    # --- Packet parity check ---
    if client_names and requires_packet_parity(command):
        ref_name = client_names[0]
        ref_packets = [normalize_packet(pkt, frame) for pkt in reqs_by_client.get(ref_name, [])]
        for cn in client_names[1:]:
            cur_packets = [normalize_packet(pkt, frame) for pkt in reqs_by_client.get(cn, [])]
            if cur_packets != ref_packets:
                log_print(f"  !!! PACKET MISMATCH:", log_fp)
                log_print(f"      {ref_name}: {ref_packets}", log_fp)
                log_print(f"      {cn}: {cur_packets}", log_fp)
                all_ok = False
                break
        else:
            pass  # packet sequences match
    elif any(reqs_by_client.values()):
        # Some clients may not have sent (e.g., connection failed before request)
        pass

    # Write test result marker to SEPARATE markers file
    # (cannot share packets_json: server holds that file open in "w" mode and would overwrite appended data)
    result_str = "OK(NG)" if (all_ok and expect_error) else ("OK" if all_ok else "NG")
    marker = json.dumps({
        "type": "TEST_RESULT", "name": name, "result": result_str,
        "desc": desc, "n_clients": len(clients),
    }, ensure_ascii=False)
    try:
        with open(markers_json, "a", encoding="utf-8") as mf:
            mf.write(marker + "\n")
    except Exception:
        pass

    baseline_client = client_names[0] if client_names else None
    live_case = {
        "type": "LIVE_CASE",
        "name": name,
        "desc": desc,
        "command": command,
        "address": address,
        "extra": [str(item) for item in extra],
        "flags": {key: str(value) for key, value in flags.items()},
        "expect_error": expect_error,
        "replay_class": determine_live_replay_class(command, address, flags, expect_error),
        "comparison_mode": determine_live_compare_mode(command, address, flags, expect_error),
        "baseline_client": baseline_client,
        "clients": {
            client_name: {
                "status": results[client_name].get("status"),
                "message": results[client_name].get("message"),
                "requests": [normalize_packet(pkt, frame) for pkt in reqs_by_client.get(client_name, [])],
                "responses": [normalize_response(pkt) for pkt in resps_by_client.get(client_name, [])],
                "response_end_codes": [parse_response_end_code(pkt) for pkt in resps_by_client.get(client_name, [])],
                "response_data_lengths": [response_data_length(pkt) for pkt in resps_by_client.get(client_name, [])],
            }
            for client_name in client_names
        },
    }
    if baseline_client:
        live_case["baseline_requests"] = live_case["clients"][baseline_client]["requests"]
        live_case["baseline_responses"] = live_case["clients"][baseline_client]["responses"]
        live_case["baseline_response_end_codes"] = live_case["clients"][baseline_client]["response_end_codes"]
        live_case["baseline_response_data_lengths"] = live_case["clients"][baseline_client]["response_data_lengths"]
    if meta.get("live_profiles"):
        live_case["live_profiles"] = meta["live_profiles"]
    write_live_case(live_cases_file, live_case)

    if all_ok:
        status_tag = "OK (NG)" if expect_error else "OK"
        log_print(f"  {status_tag}", log_fp)
        return "pass"
    return "fail"


def main():
    args = parse_args()
    selected_clients = parse_selected_clients(args.clients)
    if args.list_cases:
        list_selected_cases(selected_clients, args.case_pattern)
        return

    global HOST, PORT
    HOST = args.host
    PORT = args.port

    logs_dir = f"{ROOT}/logs"
    os.makedirs(logs_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"{logs_dir}/packet_log_{ts}.log"
    update_latest = should_write_latest(selected_clients, args.case_pattern, args.write_latest)
    selected_test_count, runnable_test_count = count_selected_cases(selected_clients, args.case_pattern)
    if selected_test_count == 0:
        print("No cases matched the current filter.")
        return
    if runnable_test_count == 0:
        print("Matched cases exist, but none are in scope for the selected clients.")
        return
    if update_latest:
        packets_json = f"{logs_dir}/latest_packets.jsonl"
        markers_json = f"{logs_dir}/latest_markers.jsonl"
        live_cases_file = LIVE_CASES_FILE
    else:
        packets_json = f"{logs_dir}/packets_{ts}.jsonl"
        markers_json = f"{logs_dir}/markers_{ts}.jsonl"
        live_cases_file = f"{logs_dir}/live_cases_{ts}.jsonl"

    open(packets_json, "w").close()
    open(markers_json, "w").close()
    open(live_cases_file, "w").close()

    server_log_path = f"{logs_dir}/server_{ts}.log"
    server_log_fp = open(server_log_path, "w", encoding="utf-8")
    server_proc = subprocess.Popen([
        "python", "-u", f"{ROOT}/server/mock_server.py",
        "--port", str(PORT), "--log-json", packets_json
    ], stdout=server_log_fp, stderr=server_log_fp)
    time.sleep(2)

    passed = 0
    failed = 0
    skipped = 0
    fail_names = []
    prev_results = load_prev_results()
    current_results = {}
    mode = "single-client" if len(selected_clients) == 1 else "parity"

    with open(log_path, "w", encoding="utf-8") as log_fp:
        log_fp.write(f"Verification run: {datetime.now().isoformat()}\n\n")
        try:
            log_print(
                (
                    f"Starting {mode} verification "
                    f"(matched={selected_test_count}, runnable={runnable_test_count}, "
                    f"clients={','.join(selected_clients)}, "
                    f"write_latest={'yes' if update_latest else 'no'})...\n"
                ),
                log_fp,
            )

            for t in TESTS:
                name, cmd, addr, extra, flags, clients, expect_error, meta = t
                if not case_matches_pattern(name, args.case_pattern):
                    continue
                prev = prev_results.get(name)
                result = test_case(
                    name,
                    cmd,
                    addr,
                    extra,
                    flags,
                    clients,
                    expect_error,
                    meta,
                    packets_json,
                    markers_json,
                    live_cases_file,
                    log_fp,
                    prev_result=prev,
                    selected_clients=selected_clients,
                )
                if result == "skip":
                    skipped += 1
                    continue
                if result == "pass":
                    passed += 1
                    current_results[name] = "OK(NG)" if expect_error else "OK"
                else:
                    failed += 1
                    fail_names.append(name)
                    current_results[name] = "NG"

            log_fp.write("\n")
            if fail_names:
                log_print(f"\nFAILED ({failed}):", log_fp)
                for fn in fail_names:
                    log_print(f"  - {fn}", log_fp)
            executed = passed + failed
            summary = (
                f"\n{'ALL PASSED' if failed == 0 else 'SOME FAILED'}: "
                f"passed={passed} failed={failed} skipped={skipped} executed={executed}"
            )
            log_print(summary, log_fp)
            if not update_latest:
                log_print("Filtered run: latest_* artifacts and prev_results.json were left unchanged.", log_fp)

        finally:
            server_proc.terminate()
            server_proc.wait()
            server_log_fp.close()
            if update_latest:
                save_results(current_results)

    print(f"\nLog: {log_path}")
    print(f"Packets JSON: {packets_json}")
    print(f"Markers JSON: {markers_json}")
    print(f"Live cases JSONL: {live_cases_file}")


if __name__ == "__main__":
    main()
