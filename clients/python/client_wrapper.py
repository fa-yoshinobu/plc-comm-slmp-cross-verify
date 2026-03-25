import json
import sys
import os
import argparse

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../plc-comm-slmp-python")))

from slmp.client import SlmpClient
from slmp.constants import PLCSeries, FrameType
from slmp.core import SlmpTarget, ExtensionSpec

def _parse_kv_pairs(items_str):
    """Parse 'DEV=VAL,DEV2=VAL2' into list of (device_str, int_value)."""
    result = []
    for item in items_str.split(","):
        k, v = item.split("=", 1)
        result.append((k.strip(), int(v.strip())))
    return result

def _parse_dev_count_pairs(blocks_str):
    """Parse 'D100=3,D200=2' into list of (device_str, count)."""
    result = []
    for item in blocks_str.split(","):
        k, v = item.split("=", 1)
        result.append((k.strip(), int(v.strip())))
    return result

def _parse_dev_values_pairs(blocks_str):
    """Parse 'D100=10:20:30,D200=40:50' into list of (device_str, [values])."""
    result = []
    for item in blocks_str.split(","):
        k, v = item.split("=", 1)
        result.append((k.strip(), [int(x) for x in v.split(":")]))
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("port", type=int)
    parser.add_argument("command", choices=[
        "read", "write", "read-type",
        "remote-run", "remote-stop", "remote-pause", "remote-latch-clear", "remote-reset",
        "random-read", "random-write-words", "random-write-bits",
        "block-read", "block-write",
        "self-test",
        "memory-read", "memory-write",
        "extend-unit-read", "extend-unit-write",
        "read-ext", "write-ext",
    ])
    parser.add_argument("address", nargs="?", default="")
    parser.add_argument("count_or_values", nargs="*")
    parser.add_argument("--frame", choices=["3e", "4e"], default="3e")
    parser.add_argument("--series", choices=["ql", "iqr"], default="ql")
    parser.add_argument("--target", help="NW,ST,MIO,MD")
    parser.add_argument("--mode", choices=["word", "bit", "dword", "float"], default="word")
    # Random access
    parser.add_argument("--word-devs", default="")
    parser.add_argument("--dword-devs", default="")
    parser.add_argument("--words", default="")
    parser.add_argument("--dwords", default="")
    parser.add_argument("--bits", default="")
    # Block access
    parser.add_argument("--word-blocks", default="")
    parser.add_argument("--bit-blocks", default="")

    args = parser.parse_args()

    target = None
    if args.target:
        p = args.target.split(",")
        target = SlmpTarget(network=int(p[0], 0), station=int(p[1], 0), module_io=int(p[2], 0), multidrop=int(p[3], 0))

    frame = FrameType.FRAME_3E if args.frame == "3e" else FrameType.FRAME_4E
    series = PLCSeries.QL if args.series == "ql" else PLCSeries.IQR

    with SlmpClient(args.host, args.port, frame_type=frame, plc_series=series, default_target=target) as client:
        result = {}
        try:
            cmd = args.command

            # --- Basic device read/write ---
            if cmd == "read":
                count = int(args.count_or_values[0]) if args.count_or_values else 1
                if args.mode == "bit":
                    vals = client.read_devices(args.address, count, bit_unit=True)
                    result = {"status": "success", "values": [1 if v else 0 for v in vals]}
                elif args.mode == "dword":
                    result = {"status": "success", "values": client.read_dwords(args.address, count)}
                elif args.mode == "float":
                    result = {"status": "success", "values": client.read_float32s(args.address, count)}
                else:
                    result = {"status": "success", "values": client.read_devices(args.address, count)}

            elif cmd == "write":
                if args.mode == "bit":
                    client.write_devices(args.address, [bool(int(v)) for v in args.count_or_values], bit_unit=True)
                elif args.mode == "dword":
                    client.write_dwords(args.address, [int(v) for v in args.count_or_values])
                elif args.mode == "float":
                    client.write_float32s(args.address, [float(v) for v in args.count_or_values])
                else:
                    client.write_devices(args.address, [int(v) for v in args.count_or_values])
                result = {"status": "success"}

            # --- Type name ---
            elif cmd == "read-type":
                info = client.read_type_name()
                result = {"status": "success", "model": info.model, "model_code": hex(info.model_code) if info.model_code is not None else None}

            # --- Remote operations ---
            elif cmd == "remote-run":
                client.remote_run()
                result = {"status": "success"}
            elif cmd == "remote-stop":
                client.remote_stop()
                result = {"status": "success"}
            elif cmd == "remote-pause":
                client.remote_pause()
                result = {"status": "success"}
            elif cmd == "remote-latch-clear":
                client.remote_latch_clear()
                result = {"status": "success"}
            elif cmd == "remote-reset":
                client.remote_reset(expect_response=False)
                result = {"status": "success"}

            # --- Random access ---
            elif cmd == "random-read":
                word_devs = [d.strip() for d in args.word_devs.split(",") if d.strip()] if args.word_devs else []
                dword_devs = [d.strip() for d in args.dword_devs.split(",") if d.strip()] if args.dword_devs else []
                r = client.read_random(word_devices=word_devs, dword_devices=dword_devs)
                result = {"status": "success", "word_values": list(r.word.values()), "dword_values": list(r.dword.values())}

            elif cmd == "random-write-words":
                word_items = _parse_kv_pairs(args.words) if args.words else []
                dword_items = _parse_kv_pairs(args.dwords) if args.dwords else []
                client.write_random_words(word_values=word_items, dword_values=dword_items)
                result = {"status": "success"}

            elif cmd == "random-write-bits":
                bit_items = [(d.strip(), bool(int(v))) for d, v in _parse_kv_pairs(args.bits)] if args.bits else []
                client.write_random_bits(bit_values=bit_items)
                result = {"status": "success"}

            # --- Block access ---
            elif cmd == "block-read":
                word_blocks = _parse_dev_count_pairs(args.word_blocks) if args.word_blocks else []
                bit_blocks = _parse_dev_count_pairs(args.bit_blocks) if args.bit_blocks else []
                r = client.read_block(word_blocks=word_blocks, bit_blocks=bit_blocks)
                result = {"status": "success",
                          "word_blocks": [[b.device, b.values] for b in r.word_blocks],
                          "bit_blocks": [[b.device, b.values] for b in r.bit_blocks]}

            elif cmd == "block-write":
                word_blocks = _parse_dev_values_pairs(args.word_blocks) if args.word_blocks else []
                bit_blocks = _parse_dev_values_pairs(args.bit_blocks) if args.bit_blocks else []
                client.write_block(word_blocks=word_blocks, bit_blocks=bit_blocks)
                result = {"status": "success"}

            # --- Self test ---
            elif cmd == "self-test":
                data = args.address or "TEST"
                echoed = client.self_test_loopback(data)
                result = {"status": "success", "echo": echoed.decode("ascii", errors="replace")}

            # --- Memory read/write ---
            elif cmd == "memory-read":
                head = int(args.address, 0)
                word_count = int(args.count_or_values[0]) if args.count_or_values else 1
                vals = client.memory_read_words(head, word_count)
                result = {"status": "success", "values": vals}

            elif cmd == "memory-write":
                head = int(args.address, 0)
                vals = [int(v) for v in args.count_or_values]
                client.memory_write_words(head, vals)
                result = {"status": "success"}

            # --- Extend unit read/write ---
            elif cmd == "extend-unit-read":
                parts = args.address.split(":")
                module_no = int(parts[0], 0)
                head = int(parts[1], 0) if len(parts) > 1 else 0
                word_count = int(args.count_or_values[0]) if args.count_or_values else 1
                vals = client.extend_unit_read_words(head, word_count, module_no)
                result = {"status": "success", "values": vals}

            elif cmd == "extend-unit-write":
                parts = args.address.split(":")
                module_no = int(parts[0], 0)
                head = int(parts[1], 0) if len(parts) > 1 else 0
                vals = [int(v) for v in args.count_or_values]
                client.extend_unit_write_words(head, module_no, vals)
                result = {"status": "success"}

            # --- Extended address (Extended Device) ---
            elif cmd == "read-ext":
                count = int(args.count_or_values[0]) if args.count_or_values else 1
                ext = ExtensionSpec()
                if args.mode == "bit":
                    vals = client.read_devices_ext(args.address, count, extension=ext, bit_unit=True)
                    result = {"status": "success", "values": [1 if v else 0 for v in vals]}
                elif args.mode == "dword":
                    # read as 2 words and combine
                    vals = client.read_devices_ext(args.address, count * 2, extension=ext)
                    result = {"status": "success", "values": [vals[i] | (vals[i+1] << 16) for i in range(0, len(vals), 2)]}
                else:
                    vals = client.read_devices_ext(args.address, count, extension=ext)
                    result = {"status": "success", "values": list(vals)}

            elif cmd == "write-ext":
                ext = ExtensionSpec()
                if args.mode == "bit":
                    vals = [bool(int(v)) for v in args.count_or_values]
                    client.write_devices_ext(args.address, vals, extension=ext, bit_unit=True)
                else:
                    vals = [int(v) for v in args.count_or_values]
                    client.write_devices_ext(args.address, vals, extension=ext)
                result = {"status": "success"}

        except Exception as e:
            result = {"status": "error", "message": str(e)}

        print(json.dumps(result))

if __name__ == "__main__":
    main()

