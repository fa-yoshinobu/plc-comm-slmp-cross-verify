"""Comprehensive SLMP cross-language verification tool.

Runs the tests defined below across the Python, .NET, and C++ wrappers.
Checks:
  1. Status parity (all clients succeed/fail on the same test)
  2. Request packet parity (all clients send identical bytes, except 4E serial)
  3. High-level named result parity for read/poll commands
"""
import subprocess
import json
import math
import time
import os
import sys
from datetime import datetime

# Ensure UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
PREV_RESULTS_FILE = f"{ROOT}/logs/prev_results.json"

CLIENTS = {
    "python": ["python", f"{ROOT}/clients/python/client_wrapper.py"],
    "dotnet": [f"{ROOT}/clients/dotnet/SlmpVerifyClient/bin/Debug/net9.0/SlmpVerifyClient.exe"],
    "cpp":    [f"{ROOT}/clients/cpp/cpp_verify_client.exe"],
    "node-red": ["node", f"{ROOT}/clients/node/client_wrapper.js"],
}

HOST = "127.0.0.1"
PORT = 9000

# Python + .NET only (C++ lacks readBitsModuleBuf support)
CLIENTS_NO_CPP = {k: CLIENTS[k] for k in ("python", "dotnet", "node-red")}


# ---------------------------------------------------------------------------
# Test cases
# Format: (name, command, address, extra_args, flags, clients, expect_error)
#   flags : dict of --key -> value; rendered as ["--key", "value", ...]
#   clients: dict (CLIENTS or CLIENTS_DP)
#   expect_error: if True, expect status="error" from all clients
# ---------------------------------------------------------------------------
def _t(name, cmd, addr, extra=None, flags=None, clients=None, expect_error=False):
    return (name, cmd, addr, extra or [], flags or {}, clients or CLIENTS, expect_error)


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
    # Long Timer
    _t("3E QL LTN Word Write [1000,2000]",   "write", "LTN0",  [1000, 2000]),
    _t("3E QL LTN Word Read  2pts",          "read",  "LTN0",  [2]),
    _t("3E QL LTS Bit  Write [1,0]",         "write", "LTS0",  [1, 0],        {"mode": "bit"}),
    _t("3E QL LTS Bit  Read  2pts",          "read",  "LTS0",  [2],           {"mode": "bit"}),
    _t("3E QL LTC Bit  Write [0,1]",         "write", "LTC0",  [0, 1],        {"mode": "bit"}),
    _t("3E QL LTC Bit  Read  2pts",          "read",  "LTC0",  [2],           {"mode": "bit"}),
    # Retentive Timer
    _t("3E QL STN Word Write [200,300]",     "write", "STN0",  [200, 300]),
    _t("3E QL STN Word Read  2pts",          "read",  "STN0",  [2]),
    _t("3E QL STS Bit  Write [0,1]",         "write", "STS0",  [0, 1],        {"mode": "bit"}),
    _t("3E QL STS Bit  Read  2pts",          "read",  "STS0",  [2],           {"mode": "bit"}),
    _t("3E QL STC Bit  Write [1,0]",         "write", "STC0",  [1, 0],        {"mode": "bit"}),
    _t("3E QL STC Bit  Read  2pts",          "read",  "STC0",  [2],           {"mode": "bit"}),
    # Long Retentive Timer
    _t("3E QL LSTN Word Write [999]",        "write", "LSTN0", [999]),
    _t("3E QL LSTN Word Read  1pt",          "read",  "LSTN0", [1]),
    _t("3E QL LSTS Bit  Write [1,0]",        "write", "LSTS0", [1, 0],        {"mode": "bit"}),
    _t("3E QL LSTS Bit  Read  2pts",         "read",  "LSTS0", [2],           {"mode": "bit"}),
    _t("3E QL LSTC Bit  Write [0,1]",        "write", "LSTC0", [0, 1],        {"mode": "bit"}),
    _t("3E QL LSTC Bit  Read  2pts",         "read",  "LSTC0", [2],           {"mode": "bit"}),
    # Counter
    _t("3E QL CN  Word Write [50,60]",       "write", "CN0",   [50, 60]),
    _t("3E QL CN  Word Read  2pts",          "read",  "CN0",   [2]),
    _t("3E QL CS  Bit  Write [1,0]",         "write", "CS0",   [1, 0],        {"mode": "bit"}),
    _t("3E QL CS  Bit  Read  2pts",          "read",  "CS0",   [2],           {"mode": "bit"}),
    _t("3E QL CC  Bit  Write [0,1]",         "write", "CC0",   [0, 1],        {"mode": "bit"}),
    _t("3E QL CC  Bit  Read  2pts",          "read",  "CC0",   [2],           {"mode": "bit"}),
    # Long Counter
    _t("3E QL LCN Word Write [500,600]",     "write", "LCN0",  [500, 600]),
    _t("3E QL LCN Word Read  2pts",          "read",  "LCN0",  [2]),
    _t("3E QL LCS Bit  Write [1,0]",         "write", "LCS0",  [1, 0],        {"mode": "bit"}),
    _t("3E QL LCS Bit  Read  2pts",          "read",  "LCS0",  [2],           {"mode": "bit"}),
    _t("3E QL LCC Bit  Write [0,1]",         "write", "LCC0",  [0, 1],        {"mode": "bit"}),
    _t("3E QL LCC Bit  Read  2pts",          "read",  "LCC0",  [2],           {"mode": "bit"}),
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
    _t("3E QL LZ  Word Write [20]",          "write", "LZ0",   [20]),
    _t("3E QL LZ  Word Read  1pt",           "read",  "LZ0",   [1]),

    # ===== 3E QL Bit Read/Write =====
    _t("3E QL M   Bit Write [1,0,1,0]",     "write", "M0",    [1, 0, 1, 0], {"mode": "bit"}),
    _t("3E QL M   Bit Read  4pts",           "read",  "M0",    [4],           {"mode": "bit"}),
    _t("3E QL Y   Bit Write [1,1,0,0]",     "write", "Y0",    [1, 1, 0, 0],  {"mode": "bit"}),
    _t("3E QL Y   Bit Read  4pts",           "read",  "Y0",    [4],           {"mode": "bit"}),
    _t("3E QL X   Bit Read  4pts",           "read",  "X0",    [4],           {"mode": "bit"}),
    _t("3E QL B   Bit Write [0,1,0,1]",     "write", "B0",    [0, 1, 0, 1],  {"mode": "bit"}),
    _t("3E QL B   Bit Read  4pts",           "read",  "B0",    [4],           {"mode": "bit"}),
    _t("3E QL SB  Bit Read  4pts",           "read",  "SB0",   [4],           {"mode": "bit"}),
    _t("3E QL SW  Bit Read  4pts",           "read",  "SW0",   [4],           {"mode": "bit"}),

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

    # ===== Routing (Other Station) =====
    _t("Routing NW1-ST2 D  Word Write [5000]", "write", "D700", [5000], {"target": "1,2,1023,0"}),
    _t("Routing NW1-ST2 D  Word Read  1pt",    "read",  "D700", [1],    {"target": "1,2,1023,0"}),
    _t("Routing NW2-ST3 M  Bit Write [1,0]",   "write", "M400", [1, 0], {"target": "2,3,1023,0", "mode": "bit"}),
    _t("Routing NW2-ST3 M  Bit Read  2pts",    "read",  "M400", [2],    {"target": "2,3,1023,0", "mode": "bit"}),

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
    _t("3E QL Block Write D800=10:20:30 / M500=1:0:1",
       "block-write", "", [], {"word-blocks": "D800=10:20:30", "bit-blocks": "M500=1:0:1"}),
    _t("3E QL Block Read  D800x3 / M500x3",
       "block-read",  "", [], {"word-blocks": "D800=3", "bit-blocks": "M500=3"}),
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
    _t("3E QL Named Write Z/LZ/LTN/LSTN/LCN",
       "write-named", "Z100=321,LZ110=654,LTN120=777,LSTN130=888,LCN140=999", []),
    _t("3E QL Named Read  Z/LZ/LTN/LSTN/LCN",
       "read-named", "Z100,LZ110,LTN120,LSTN130,LCN140", []),
    _t("3E QL Poll Once   Z/LZ/LTN/LSTN/LCN",
       "poll-once", "Z100,LZ110,LTN120,LSTN130,LCN140", []),
    _t("3E QL Named Write RD:D",
       "write-named", "RD150:D=305419896", []),
    _t("3E QL Named Read  RD:D",
       "read-named", "RD150:D", []),
    _t("3E QL Poll Once   RD:D",
       "poll-once", "RD150:D", []),
    _t("3E QL Named Write LTS/LTC/LSTS/LSTC/LCS/LCC",
       "write-named", "LTS100=1,LTC110=0,LSTS120=1,LSTC130=0,LCS140=1,LCC150=0", []),
    _t("3E QL Named Read  LTS/LTC/LSTS/LSTC/LCS/LCC",
       "read-named", "LTS100,LTC110,LSTS120,LSTC130,LCS140,LCC150", []),
    _t("3E QL Poll Once   LTS/LTC/LSTS/LSTC/LCS/LCC",
       "poll-once", "LTS100,LTC110,LSTS120,LSTC130,LCS140,LCC150", []),

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
    _t("3E J1\\SW0  Ext Bit Read  3pts",     "read-ext",  "J1\\SW0",  [3],        {"mode": "bit"}),
    _t("3E U3\\G100 Ext Word Write [11,22]",  "write-ext", "U3\\G100", [11, 22]),
    _t("3E U3\\G100 Ext Word Read  2pts",     "read-ext",  "U3\\G100", [2]),
    _t("3E U3\\G100 Ext Bit Write [1,0,1]",  "write-ext", "U3\\G100", [1, 0, 1],  {"mode": "bit"}),
    _t("3E U3\\G100 Ext Bit Read  3pts",     "read-ext",  "U3\\G100", [3],         {"mode": "bit"}),
    _t("3E U1\\HG0  Ext Word Write [33,44]", "write-ext", "U1\\HG0",  [33, 44]),
    _t("3E U1\\HG0  Ext Word Read  2pts",    "read-ext",  "U1\\HG0",  [2]),
    _t("4E J1\\SW0  Ext Word Write [70,80]", "write-ext", "J1\\SW0",  [70, 80],   {"frame": "4e"}),
    _t("4E J1\\SW0  Ext Word Read  2pts",    "read-ext",  "J1\\SW0",  [2],         {"frame": "4e"}),

    # ===== Error / NG Conditions =====
    _t("NG 3E Data Length Over 1001pts",  "read", "D0", [1001], {},                 CLIENTS, True),
    _t("NG 4E Data Length Over 1001pts",  "read", "D0", [1001], {"frame": "4e"},    CLIENTS, True),
]


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
    args = []
    for k, v in flags.items():
        args += [f"--{k}", str(v)]
    args += [str(a) for a in extra]
    return args


def node_red_supports(command, _address, _extra, _flags):
    return command in {
        "read",
        "write",
        "read-type",
        "random-read",
        "random-write-words",
        "read-named",
        "write-named",
        "poll-once",
    }


def resolve_clients(command, address, extra, flags, clients):
    resolved = dict(clients)
    if node_red_supports(command, address, extra, flags):
        resolved["node-red"] = CLIENTS["node-red"]
    else:
        resolved.pop("node-red", None)
    return resolved


def run_client(client_name, command, address, extra, flags):
    cmd_prefix = CLIENTS[client_name]
    exe_path = cmd_prefix[0]
    if exe_path.lower().endswith(".exe") and not os.path.exists(exe_path):
        return {"status": "error", "message": f"missing executable: {exe_path}"}
    extra_args = build_cmd_args(command, address, extra, flags)
    cmd = cmd_prefix + [HOST, str(PORT), command, address] + extra_args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
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


def normalize_packet(hex_str, frame):
    """Normalize 4E serial bytes (bytes 2-5) to zeros for comparison."""
    b = bytes.fromhex(hex_str)
    if frame == "4e" and len(b) >= 6 and b[:2] == bytes.fromhex("5400"):
        b = b[:2] + b"\x00\x00\x00\x00" + b[6:]
    return b.hex()


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


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------
def test_case(name, command, address, extra, flags, clients, expect_error, packets_json, log_fp, prev_result=None):
    clients = resolve_clients(command, address, extra, flags, clients)
    prev_tag = f"  [Prev:{prev_result}]" if prev_result else ""
    log_print(f"Running: {name}{prev_tag}", log_fp)
    desc = generate_desc(command, address, extra, flags, clients, expect_error)
    log_print(f"  {desc}", log_fp)

    results = {}
    reqs_by_client = {}
    for client_name in clients:
        line_before = count_log_lines(packets_json)
        results[client_name] = run_client(client_name, command, address, extra, flags)
        reqs_by_client[client_name] = get_new_reqs(packets_json, line_before)

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
    markers_json = packets_json.replace("latest_packets.jsonl", "latest_markers.jsonl")
    try:
        with open(markers_json, "a", encoding="utf-8") as mf:
            mf.write(marker + "\n")
    except Exception:
        pass

    if all_ok:
        status_tag = "OK (NG)" if expect_error else "OK"
        log_print(f"  {status_tag}", log_fp)
    return all_ok


def main():
    logs_dir = f"{ROOT}/logs"
    os.makedirs(logs_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = f"{logs_dir}/packet_log_{ts}.log"
    packets_json = f"{logs_dir}/latest_packets.jsonl"

    markers_json = f"{logs_dir}/latest_markers.jsonl"
    # Clear both logs so line counting and marker pairing are fresh
    open(packets_json, "w").close()
    open(markers_json, "w").close()

    server_log_path = f"{logs_dir}/server_{ts}.log"
    server_log_fp = open(server_log_path, "w", encoding="utf-8")
    server_proc = subprocess.Popen([
        "python", "-u", f"{ROOT}/server/mock_server.py",
        "--port", str(PORT), "--log-json", packets_json
    ], stdout=server_log_fp, stderr=server_log_fp)
    time.sleep(2)

    passed = 0
    failed = 0
    fail_names = []
    prev_results = load_prev_results()
    current_results = {}

    with open(log_path, "w", encoding="utf-8") as log_fp:
        log_fp.write(f"Verification run: {datetime.now().isoformat()}\n\n")
        try:
            log_print(f"Starting comprehensive verification ({len(TESTS)} tests)...\n", log_fp)

            for t in TESTS:
                name, cmd, addr, extra, flags, clients, expect_error = t
                prev = prev_results.get(name)
                ok = test_case(name, cmd, addr, extra, flags, clients, expect_error, packets_json, log_fp, prev_result=prev)
                if ok:
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
            summary = f"\n{'ALL PASSED' if failed == 0 else 'SOME FAILED'}: {passed}/{passed+failed}"
            log_print(summary, log_fp)

        finally:
            server_proc.terminate()
            server_proc.wait()
            server_log_fp.close()
            save_results(current_results)

    print(f"\nLog: {log_path}")


if __name__ == "__main__":
    main()


