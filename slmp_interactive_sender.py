import socket
import json
import os
import sys

# UTF-8 output for Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
PACKETS_LOG       = f"{ROOT}/logs/latest_packets.jsonl"
MARKERS_LOG       = f"{ROOT}/logs/latest_markers.jsonl"
RESPONSE_HIST_FILE = f"{ROOT}/logs/response_history.json"
LIVE_CASES_FILE    = f"{ROOT}/logs/latest_live_cases.jsonl"

COMMAND_NAMES = {
    0x0401: "DeviceRead",   0x1401: "DeviceWrite",
    0x0403: "ReadRandom",   0x1402: "WriteRandom",
    0x0406: "BlockRead",    0x1406: "BlockWrite",
    0x0101: "ReadType",
    0x1001: "RemoteRun",    0x1002: "RemoteStop",
    0x1003: "RemotePause",  0x1005: "LatchClear",   0x1006: "RemoteReset",
    0x0613: "MemoryRead",   0x1613: "MemoryWrite",
    0x0601: "ExtUnitRead",  0x1601: "ExtUnitWrite",
    0x0619: "SelfTest",
}

# QL 1-byte device code → name
QL_DEV = {
    0x9C: "X",  0x9D: "Y",  0x90: "M",  0xA0: "B",
    0xA8: "D",  0xB4: "W",  0xAF: "R",  0xB0: "ZR",
    0x91: "SM", 0xA9: "SD", 0xA1: "SB", 0xB5: "SW",
    0xAB: "G",  0x2E: "HG", 0xC4: "T",  0xC6: "C",
}

# iQR 2-byte device code → name
IQR_DEV = {
    0x0009: "X",  0x000A: "Y",  0x0008: "M",  0x00A0: "B",
    0x00A8: "D",  0x00B4: "W",  0x000C: "R",
    0x0091: "SM", 0x00A9: "SD",
}


# ---------------------------------------------------------------------------
# Packet byte parser
# ---------------------------------------------------------------------------
def parse_packet_info(hex_str):
    """Return (frame, cmd_name, detail_str) from raw hex."""
    try:
        b = bytes.fromhex(hex_str)
        if b[0:2] == b"\x50\x00":
            frame, cmd_off, pay_off = "3E", 11, 15
        elif b[0:2] == b"\x54\x00":
            frame, cmd_off, pay_off = "4E", 15, 19
        else:
            return "??", "??", ""

        cmd = int.from_bytes(b[cmd_off:cmd_off+2], "little")
        sub = int.from_bytes(b[cmd_off+2:cmd_off+4], "little")
        cmd_name = COMMAND_NAMES.get(cmd, f"0x{cmd:04X}")

        detail = _parse_device_detail(b, pay_off, cmd, sub)
        return frame, cmd_name, detail
    except Exception:
        return "??", "??", ""


def _parse_device_detail(b, pay_off, cmd, sub):
    """Parse device / value info from DeviceRead/Write payload."""
    if cmd not in (0x0401, 0x1401):
        return ""
    is_iqr = sub in (0x0002, 0x0003)
    is_bit = sub in (0x0001, 0x0003)
    mode_jp = "ビット" if is_bit else "ワード"

    try:
        if is_iqr:
            dev_no = int.from_bytes(b[pay_off:pay_off+4], "little")
            dev_code = int.from_bytes(b[pay_off+4:pay_off+6], "little")
            dev_str = IQR_DEV.get(dev_code, f"0x{dev_code:04X}")
            spec_len = 6
        else:
            dev_no = int.from_bytes(b[pay_off:pay_off+3], "little")
            dev_code = b[pay_off+3]
            dev_str = QL_DEV.get(dev_code, f"0x{dev_code:02X}")
            spec_len = 4

        count = int.from_bytes(b[pay_off+spec_len:pay_off+spec_len+2], "little")

        if cmd == 0x1401:  # write — extract values
            val_off = pay_off + spec_len + 2
            vals = []
            if is_bit:
                for j in range(min((count + 1) // 2, 4)):
                    byte = b[val_off + j] if val_off + j < len(b) else 0
                    vals.append(byte & 0x0F)
                    if len(vals) < count:
                        vals.append((byte >> 4) & 0x0F)
            else:
                for j in range(min(count, 4)):
                    if val_off + j * 2 + 2 <= len(b):
                        vals.append(int.from_bytes(b[val_off+j*2:val_off+j*2+2], "little"))
            sfx = "..." if count > len(vals) else ""
            return f"{dev_str}{dev_no} ← {mode_jp}{vals}{sfx}"
        else:
            return f"{dev_str}{dev_no} → {count}点{mode_jp}"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Loader — pairs REQ packets with test markers from separate files
# ---------------------------------------------------------------------------
def load_tests():
    """Return list of test dicts: {packets, name, result, desc}."""
    if os.path.exists(LIVE_CASES_FILE):
        tests = []
        with open(LIVE_CASES_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    if e.get("type") != "LIVE_CASE":
                        continue
                    packets = e.get("baseline_requests") or []
                    tests.append({
                        "packets": packets,
                        "name": e.get("name"),
                        "result": None,
                        "desc": e.get("desc", ""),
                    })
                except Exception:
                    pass
        if tests:
            return tests

    # Load REQ packets from packet log
    reqs = []
    if os.path.exists(PACKETS_LOG):
        with open(PACKETS_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    if e.get("direction") == "REQ":
                        reqs.append(e["data"])
                except Exception:
                    pass

    # Load test metadata from separate markers file (written by verify.py)
    markers = []
    if os.path.exists(MARKERS_LOG):
        with open(MARKERS_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    if e.get("type") == "TEST_RESULT":
                        markers.append(e)
                except Exception:
                    pass

    # Pair markers with their REQ packets using n_clients stride
    tests = []
    req_offset = 0
    for m in markers:
        n = m.get("n_clients", 1)
        pkts = reqs[req_offset: req_offset + n]
        req_offset += n
        tests.append({
            "packets": pkts if pkts else [],
            "name":    m["name"],
            "result":  m["result"],
            "desc":    m.get("desc", ""),
        })

    # Any remaining ungrouped REQ packets (manual sends / no marker)
    for pkt in reqs[req_offset:]:
        tests.append({"packets": [pkt], "name": None, "result": None, "desc": None})

    return tests


# ---------------------------------------------------------------------------
# Network send
# ---------------------------------------------------------------------------
def recv_exact(s, n):
    """Receive exactly n bytes, raising on short read."""
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"接続が閉じられました ({len(buf)}/{n} bytes)")
        buf += chunk
    return buf


def send_hex(ip, port, hex_str):
    """Send hex packet and return (response_hex, error_str)."""
    try:
        data = bytes.fromhex(hex_str)
        req_is_4e = data[0:2] == b"\x54\x00"

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5.0)
            s.connect((ip, port))
            s.sendall(data)

            # 3E response header: D0 00 + NW ST MIO(2) MD + LEN(2) = 9 bytes
            # 4E response header: D4 00 + serial(2) + res(2) + NW ST MIO(2) MD + LEN(2) = 13 bytes
            if req_is_4e:
                header = recv_exact(s, 13)
                data_len = int.from_bytes(header[11:13], "little")
            else:
                header = recv_exact(s, 9)
                data_len = int.from_bytes(header[7:9], "little")

            body = recv_exact(s, data_len)
            return (header + body).hex(), None

    except socket.timeout:
        return None, "タイムアウト (PLCが応答しないか、到達不可)"
    except ConnectionRefusedError:
        return None, "接続拒否 (IP/ポートが間違っているか、PLCがTCP待受していない)"
    except OSError as e:
        return None, f"ネットワークエラー: {e}"
    except Exception as e:
        return None, str(e)


def parse_end_code(resp_hex):
    """Extract end code from 3E/4E response. Returns (code_int, ok_bool)."""
    try:
        b = bytes.fromhex(resp_hex)
        # 3E: D0 00 + routing(5) + LEN(2) → body starts at offset 9, end_code at [9:11]
        # 4E: D4 00 + serial(2) + res(2) + routing(5) + LEN(2) → body at offset 13, end_code at [13:15]
        if b[0:2] == b"\xD0\x00":
            offset = 9
        elif b[0:2] == b"\xD4\x00":
            offset = 13
        else:
            return None, False
        code = int.from_bytes(b[offset:offset + 2], "little")
        return code, code == 0x0000
    except Exception:
        return None, False


# ---------------------------------------------------------------------------
# Response history (compare current vs previous send)
# ---------------------------------------------------------------------------
def load_response_history():
    """Return dict keyed by test name → last normalized response hex."""
    if os.path.exists(RESPONSE_HIST_FILE):
        try:
            with open(RESPONSE_HIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_response_history(history):
    try:
        os.makedirs(os.path.dirname(RESPONSE_HIST_FILE), exist_ok=True)
        with open(RESPONSE_HIST_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def normalize_response(resp_hex):
    """Zero out 4E serial/reserved bytes (offsets 2-5) for stable comparison."""
    if isinstance(resp_hex, list):
        return [normalize_response(item) for item in resp_hex]
    try:
        b = bytearray(bytes.fromhex(resp_hex))
        if b[0:2] == b"\xD4\x00":
            b[2:6] = b"\x00\x00\x00\x00"
        return b.hex()
    except Exception:
        return resp_hex


def compare_and_record(key, resp_hex, history):
    """Compare resp against history[key]. Update history in-place.
    Returns (same: bool, prev_norm: str|None).
    None prev means first time — treated as same."""
    norm = normalize_response(resp_hex)
    prev = history.get(key)
    history[key] = norm
    if prev is None:
        return True, None
    return norm == prev, prev


def send_case_packets(packets, ip, port):
    responses = []
    for pkt in packets:
        resp, err = send_hex(ip, port, pkt)
        if err:
            return None, err
        responses.append(resp)
    return responses, None


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
PREV_MARK = {"OK": "✓OK  ", "OK(NG)": "✓OK  ", "NG": "✗NG  ", None: "  -  "}

def show_tests(tests):
    if not tests:
        print("  (パケット履歴なし — verify.py を先に実行してください)")
        return
    print(f"  {'ID':>3}  {'前回':<6} {'Frame':<4} {'Command':<14}  説明")
    print("  " + "─" * 88)
    for i, t in enumerate(tests):
        pkt = t["packets"][0]
        frame, cmd_name, pkt_detail = parse_packet_info(pkt)
        prev = PREV_MARK.get(t["result"], "  ?  ")
        # Prefer test desc (rich), fall back to parsed bytes
        desc = t["desc"] or pkt_detail or t["name"] or pkt[:24] + "..."
        n = len(t["packets"])
        note = f" ×{n}" if n > 1 else ""
        print(f"  [{i:3}] {prev} {frame:<4} {cmd_name:<14}  {desc}{note}")
    print()


# ---------------------------------------------------------------------------
# Batch send
# ---------------------------------------------------------------------------
def batch_send(tests, indices, ip, port, history):
    total = len(indices)
    errors = 0
    ng_items = []  # (idx, name_str, desc_str, reason_str)

    print(f"\n{'─'*70}")
    print(f"  一括送信開始: {total}件  →  {ip}:{port}")
    print(f"{'─'*70}")
    for seq, idx in enumerate(indices, 1):
        t = tests[idx]
        pkt = t["packets"][0]
        frame, cmd_name, pkt_detail = parse_packet_info(pkt)
        name_str = t["name"] or f"[{idx}]"
        desc_str = t["desc"] or pkt_detail or ""

        print(f"\n  [{seq}/{total}] {name_str}")
        if desc_str:
            print(f"         {desc_str}")
        for packet_index, packet in enumerate(t["packets"], 1):
            print(f"    Send[{packet_index}/{len(t['packets'])}]: {packet}")

        responses, err = send_case_packets(t["packets"], ip, port)
        if err:
            print(f"    Recv: ERROR — {err}")
            errors += 1
            ng_items.append((idx, name_str, desc_str, f"通信エラー: {err}"))
        else:
            last_resp = responses[-1]
            end_code, ok = parse_end_code(last_resp)
            ec_str = f"  EndCode=0x{end_code:04X} {'(正常)' if ok else '(エラー)'}" if end_code is not None else ""
            same, prev = compare_and_record(name_str, responses, history)
            diff_str = "  [初回]" if prev is None else ("  応答:同じ" if same else "  応答:変化")
            print(f"    Recv: {normalize_response(responses)}{ec_str}{diff_str}")

            reasons = []
            if end_code is not None and not ok:
                reasons.append(f"EndCode=0x{end_code:04X}")
            if not same:
                reasons.append(f"応答変化 前回:{str(prev)[:24]}... 今回:{str(normalize_response(responses))[:24]}...")
            if reasons:
                ng_items.append((idx, name_str, desc_str, " / ".join(reasons)))

    print(f"\n{'─'*70}")
    print(f"  完了: {total}件送信, {errors}件通信エラー")
    print(f"  ※ PLCの応答内容は人間が評価してください")
    print(f"{'─'*70}")

    if ng_items:
        print(f"\n  NG一覧 ({len(ng_items)}件):")
        print(f"  {'─'*66}")
        for idx, name_str, desc_str, reason in ng_items:
            print(f"  [{idx:3}] {name_str}")
            if desc_str:
                print(f"         {desc_str}")
            print(f"         → {reason}")
        print(f"  {'─'*66}")
    else:
        print("  全件 EndCode正常 / 応答変化なし")

    print()
    save_response_history(history)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=== SLMP Interactive Raw Sender ===")
    ip   = input("PLC IP   [127.0.0.1]: ").strip() or "127.0.0.1"
    port = int(input("PLC Port [9000]     : ").strip() or "9000")
    history = load_response_history()

    while True:
        tests = load_tests()
        print()
        show_tests(tests)

        print("  操作:")
        print("    番号        — 単体送信 (例: 5)")
        print("    a           — 全件一括送信")
        print("    a:開始-終了 — 範囲一括送信 (例: a:0-9)")
        print("    m           — 手動 Hex 入力送信")
        print("    r           — 履歴リロード")
        print("    q           — 終了")
        choice = input("\n選択: ").strip().lower()

        if choice == "q":
            break

        elif choice == "r":
            continue

        elif choice == "m":
            raw = input("Hex packet: ").strip().replace(" ", "")
            if raw:
                resp, err = send_hex(ip, port, raw)
                if err:
                    print(f"  ERROR: {err}")
                else:
                    ec, ok = parse_end_code(resp)
                    print(f"  Recv: {resp}")
                    if ec is not None:
                        print(f"  EndCode: 0x{ec:04X} {'(正常)' if ok else '(エラー)'}")
                input("Enter で続行...")

        elif choice == "a":
            if not tests:
                print("  パケットがありません。")
            else:
                confirm = input(f"  全{len(tests)}件を {ip}:{port} に送信します。よろしいですか？ [y/N]: ").strip().lower()
                if confirm == "y":
                    batch_send(tests, list(range(len(tests))), ip, port, history)
                    input("Enter で続行...")

        elif choice.startswith("a:"):
            try:
                rng = choice[2:].split("-")
                start, end = int(rng[0]), int(rng[1])
                indices = [i for i in range(start, end + 1) if 0 <= i < len(tests)]
                confirm = input(f"  {len(indices)}件 ({start}〜{end}) を {ip}:{port} に送信します。よろしいですか？ [y/N]: ").strip().lower()
                if confirm == "y":
                    batch_send(tests, indices, ip, port, history)
                    input("Enter で続行...")
            except (IndexError, ValueError):
                print("  書式: a:開始-終了 (例: a:0-9)")

        elif choice.isdigit():
            idx = int(choice)
            if 0 <= idx < len(tests):
                t = tests[idx]
                if t.get("name"):
                    print(f"\n  テスト: {t['name']}")
                    print(f"  説明  : {t['desc']}")
                for packet_index, pkt in enumerate(t["packets"], 1):
                    print(f"  Send[{packet_index}/{len(t['packets'])}] : {pkt}")
                responses, err = send_case_packets(t["packets"], ip, port)
                if err:
                    print(f"  ERROR : {err}")
                else:
                    last_resp = responses[-1]
                    ec, ok = parse_end_code(last_resp)
                    name_key = t["name"] or f"[{idx}]"
                    same, prev = compare_and_record(name_key, responses, history)
                    save_response_history(history)
                    print(f"  Recv  : {normalize_response(responses)}")
                    if ec is not None:
                        print(f"  EndCode: 0x{ec:04X} {'(正常)' if ok else '(エラー)'}")
                    if prev is None:
                        print(f"  応答比較: [初回]")
                    elif same:
                        print(f"  応答比較: 前回と同じ")
                    else:
                        print(f"  応答比較: 変化あり")
                        print(f"    前回: {prev}")
                        print(f"    今回: {normalize_response(responses)}")
                input("Enter で続行...")
            else:
                print("  番号が範囲外です。")

        else:
            print("  不明な入力です。")


if __name__ == "__main__":
    main()
