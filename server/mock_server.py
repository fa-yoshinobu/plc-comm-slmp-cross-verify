import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, IO, List, Optional

class Command(IntEnum):
    DEVICE_READ = 0x0401
    DEVICE_WRITE = 0x1401
    DEVICE_READ_RANDOM = 0x0403
    DEVICE_WRITE_RANDOM = 0x1402
    DEVICE_ENTRY_MONITOR = 0x0801
    DEVICE_EXECUTE_MONITOR = 0x0802
    DEVICE_READ_BLOCK = 0x0406
    DEVICE_WRITE_BLOCK = 0x1406
    READ_TYPE_NAME = 0x0101
    LABEL_ARRAY_READ = 0x041A
    LABEL_ARRAY_WRITE = 0x141A
    LABEL_READ_RANDOM = 0x041C
    LABEL_WRITE_RANDOM = 0x141B
    REMOTE_RUN = 0x1001
    REMOTE_STOP = 0x1002
    REMOTE_PAUSE = 0x1003
    REMOTE_LATCH_CLEAR = 0x1005
    REMOTE_RESET = 0x1006
    SELF_TEST = 0x0619
    MEMORY_READ = 0x0613
    MEMORY_WRITE = 0x1613
    EXTEND_UNIT_READ = 0x0601
    EXTEND_UNIT_WRITE = 0x1601

# Subcommands
SUBCOMMAND_QL_WORD     = 0x0000
SUBCOMMAND_QL_BIT      = 0x0001
SUBCOMMAND_IQR_WORD    = 0x0002
SUBCOMMAND_IQR_BIT     = 0x0003
SUBCOMMAND_QL_WORD_EXT  = 0x0080
SUBCOMMAND_QL_BIT_EXT   = 0x0081
SUBCOMMAND_IQR_WORD_EXT = 0x0082
SUBCOMMAND_IQR_BIT_EXT  = 0x0083

# SLMP Error Codes
ERR_ADDRESS_RANGE    = 0xC050
ERR_DEVICE_NOT_FOUND = 0xC051
ERR_DATA_LENGTH      = 0xC056

@dataclass
class LogEntry:
    session_id: int
    client_addr: str
    direction: str
    data: str
    routing: str = ""

class SlmpMockServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 5000, log_json: Optional[str] = None) -> None:
        self.host = host
        self.port = port
        self.server: Optional[asyncio.AbstractServer] = None
        self.context_memory: Dict[tuple, Dict[int, int]] = {}
        self.memory_store: Dict[int, int] = {}
        self.extend_unit_store: Dict[tuple, int] = {}
        self.label_store: Dict[str, bytes] = {}
        self.logs: List[LogEntry] = []
        self.log_json = log_json
        self._log_fp: Optional[IO] = None
        self._session_counter = 0

    def log(self, addr: str, direction: str, data: bytes, session_id: int, routing: bytes = None):
        r_str = ""
        if routing:
            nw, st, mio, md = routing[0], routing[1], int.from_bytes(routing[2:4], "little"), routing[4]
            r_str = f"NW:{nw},ST:{st},MIO:{mio:04X},MD:{md}"
        self.logs.append(LogEntry(session_id, addr, direction, data.hex(), r_str))
        print(f"[sess={session_id}][{addr}] {direction} ({r_str}): {data.hex()}")
        if self._log_fp:
            entry = {"session_id": session_id, "direction": direction, "routing": r_str, "data": data.hex()}
            self._log_fp.write(json.dumps(entry) + "\n")
            self._log_fp.flush()

    async def start(self):
        if self.log_json:
            os.makedirs(os.path.dirname(self.log_json), exist_ok=True)
            self._log_fp = open(self.log_json, "w", encoding="utf-8")
        try:
            self.server = await asyncio.start_server(self.handle_client, self.host, self.port)
            addr = self.server.sockets[0].getsockname()
            print(f"Serving on {addr}")
            async with self.server:
                await self.server.serve_forever()
        finally:
            if self._log_fp:
                self._log_fp.close()

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._session_counter += 1
        session_id = self._session_counter
        addr = writer.get_extra_info('peername')[0]
        try:
            while True:
                subheader = await reader.readexactly(2)
                is_3e, is_4e = (subheader == b"\x50\x00"), (subheader == b"\x54\x00")
                routing = await reader.readexactly(5 if is_3e else 9)
                routing_only = routing[4:9] if is_4e else routing
                serial_fixed = routing[0:4] if is_4e else b""

                data_len = int.from_bytes(await reader.readexactly(2), "little")
                body = await reader.readexactly(data_len)
                full_req = subheader + routing + data_len.to_bytes(2, "little") + body
                self.log(addr, "REQ", full_req, session_id, routing_only)

                cmd, sub = int.from_bytes(body[2:4], "little"), int.from_bytes(body[4:6], "little")
                end_code, res_payload = self.process_command(cmd, sub, body[6:], routing_only)

                res_body = end_code.to_bytes(2, "little") + res_payload
                res_subheader = b"\xd0\x00" if is_3e else b"\xd4\x00"
                full_res = res_subheader + routing + len(res_body).to_bytes(2, "little") + res_body
                writer.write(full_res)
                await writer.drain()
                self.log(addr, "RES", full_res, session_id, routing_only)
        except Exception: pass
        finally: writer.close()

    def process_command(self, command: int, subcommand: int, payload: bytes, routing: bytes) -> tuple[int, bytes]:
        is_ext = subcommand in (SUBCOMMAND_QL_WORD_EXT, SUBCOMMAND_QL_BIT_EXT, SUBCOMMAND_IQR_WORD_EXT, SUBCOMMAND_IQR_BIT_EXT)
        is_iqr = subcommand in (SUBCOMMAND_IQR_WORD, SUBCOMMAND_IQR_BIT, SUBCOMMAND_IQR_WORD_EXT, SUBCOMMAND_IQR_BIT_EXT)
        is_bit = subcommand in (SUBCOMMAND_QL_BIT, SUBCOMMAND_IQR_BIT, SUBCOMMAND_QL_BIT_EXT, SUBCOMMAND_IQR_BIT_EXT)

        # --- Extended Device extended reads/writes ---
        # Extended device spec is 10-11 bytes (various formats); count is always the last 2 bytes of payload.
        if command == Command.DEVICE_READ and is_ext:
            if len(payload) < 2:
                return ERR_DATA_LENGTH, b""
            count = int.from_bytes(payload[-2:], "little")
            if count > 1000:
                return ERR_DATA_LENGTH, b""
            if is_bit:
                return 0, bytes((count + 1) // 2)
            return 0, bytes(count * 2)

        if command == Command.DEVICE_WRITE and is_ext:
            return 0, b""

        # --- Standard device read/write ---
        ptr = 0
        addr_len = 4 if is_iqr else 3
        dev_code_len = 2 if is_iqr else 1

        if command in (Command.DEVICE_READ, Command.DEVICE_WRITE):
            start_addr = int.from_bytes(payload[ptr:ptr+addr_len], "little")
            dev_ptr = ptr + addr_len
            dev_code = int.from_bytes(payload[dev_ptr:dev_ptr+dev_code_len], "little")
            if dev_code == 0xEE: return ERR_DEVICE_NOT_FOUND, b""

        if command == Command.DEVICE_READ:
            count_off = ptr + addr_len + dev_code_len
            count = int.from_bytes(payload[count_off:count_off+2], "little")
            if count > 1000: return ERR_DATA_LENGTH, b""

            nw, st, mio, md = routing[0], routing[1], int.from_bytes(routing[2:4], "little"), routing[4]
            mem = self.context_memory.get((nw, st, mio, md, dev_code, 0, 0), {})

            if is_bit:
                res = b""
                for i in range(0, count, 2):
                    v1 = mem.get(start_addr + i, 0)
                    v2 = mem.get(start_addr + i + 1, 0)
                    res += (((1 if v1 else 0) << 4) | (1 if v2 else 0)).to_bytes(1, "little")
                return 0, res
            else:
                res = b""
                for i in range(count):
                    res += mem.get(start_addr + i, 0).to_bytes(2, "little")
                return 0, res

        elif command == Command.DEVICE_WRITE:
            nw, st, mio, md = routing[0], routing[1], int.from_bytes(routing[2:4], "little"), routing[4]
            key = (nw, st, mio, md, dev_code, 0, 0)
            if key not in self.context_memory:
                self.context_memory[key] = {}
            mem = self.context_memory[key]
            count_off = ptr + addr_len + dev_code_len
            count = int.from_bytes(payload[count_off:count_off+2], "little")
            data_start = count_off + 2
            if is_bit:
                for i in range(0, count, 2):
                    byte = payload[data_start + i // 2]
                    mem[start_addr + i] = (byte >> 4) & 0x1
                    if i + 1 < count:
                        mem[start_addr + i + 1] = byte & 0x1
            else:
                for i in range(count):
                    mem[start_addr + i] = int.from_bytes(payload[data_start + i*2:data_start + i*2+2], "little")
            return 0, b""

        elif command == Command.DEVICE_READ_RANDOM:
            if len(payload) < 2:
                return ERR_DATA_LENGTH, b""
            w_pts, dw_pts = payload[0], payload[1]
            spec = addr_len + dev_code_len  # 4 (QL) or 6 (iQR) bytes per device spec
            nw, st, mio, md = routing[0], routing[1], int.from_bytes(routing[2:4], "little"), routing[4]
            ptr2 = 2
            res = b""
            # word reads
            for _ in range(w_pts):
                if ptr2 + spec > len(payload): break
                start = int.from_bytes(payload[ptr2:ptr2+addr_len], "little")
                dc = int.from_bytes(payload[ptr2+addr_len:ptr2+spec], "little")
                mem = self.context_memory.get((nw, st, mio, md, dc, 0, 0), {})
                res += mem.get(start, 0).to_bytes(2, "little")
                ptr2 += spec
            # dword reads
            for _ in range(dw_pts):
                if ptr2 + spec > len(payload): break
                start = int.from_bytes(payload[ptr2:ptr2+addr_len], "little")
                dc = int.from_bytes(payload[ptr2+addr_len:ptr2+spec], "little")
                mem = self.context_memory.get((nw, st, mio, md, dc, 0, 0), {})
                lo = mem.get(start, 0)
                hi = mem.get(start + 1, 0)
                val = (hi << 16) | lo
                res += val.to_bytes(4, "little")
                ptr2 += spec
            return 0, res

        elif command == Command.DEVICE_WRITE_RANDOM:
            if len(payload) < 2:
                return 0, b""
            w_pts, dw_pts = payload[0], payload[1]
            spec = addr_len + dev_code_len
            nw, st, mio, md = routing[0], routing[1], int.from_bytes(routing[2:4], "little"), routing[4]
            ptr2 = 2
            val_size_word = 2 if is_iqr else 1  # iQR bit write uses 2 bytes for value; but for word random it's always 2
            # For word random write subcommand is 0x0000 or 0x0002 (not bit), value is always 2 bytes
            # For bit random write subcommand is 0x0001 or 0x0003, value is 1 byte (QL) or 2 bytes (iQR)
            if is_bit:
                val_size = 2 if is_iqr else 1
                for _ in range(w_pts):  # w_pts is actually bit_pts in DEVICE_WRITE_RANDOM for bit
                    if ptr2 + spec + val_size > len(payload): break
                    start = int.from_bytes(payload[ptr2:ptr2+addr_len], "little")
                    dc = int.from_bytes(payload[ptr2+addr_len:ptr2+spec], "little")
                    val = int.from_bytes(payload[ptr2+spec:ptr2+spec+val_size], "little")
                    key = (nw, st, mio, md, dc, 0, 0)
                    if key not in self.context_memory: self.context_memory[key] = {}
                    self.context_memory[key][start] = 1 if val else 0
                    ptr2 += spec + val_size
            else:
                for _ in range(w_pts):
                    if ptr2 + spec + 2 > len(payload): break
                    start = int.from_bytes(payload[ptr2:ptr2+addr_len], "little")
                    dc = int.from_bytes(payload[ptr2+addr_len:ptr2+spec], "little")
                    val = int.from_bytes(payload[ptr2+spec:ptr2+spec+2], "little")
                    key = (nw, st, mio, md, dc, 0, 0)
                    if key not in self.context_memory: self.context_memory[key] = {}
                    self.context_memory[key][start] = val
                    ptr2 += spec + 2
                for _ in range(dw_pts):
                    if ptr2 + spec + 4 > len(payload): break
                    start = int.from_bytes(payload[ptr2:ptr2+addr_len], "little")
                    dc = int.from_bytes(payload[ptr2+addr_len:ptr2+spec], "little")
                    val = int.from_bytes(payload[ptr2+spec:ptr2+spec+4], "little")
                    key = (nw, st, mio, md, dc, 0, 0)
                    if key not in self.context_memory: self.context_memory[key] = {}
                    self.context_memory[key][start] = val & 0xFFFF
                    self.context_memory[key][start + 1] = (val >> 16) & 0xFFFF
                    ptr2 += spec + 4
            return 0, b""

        elif command == Command.DEVICE_READ_BLOCK:
            if len(payload) < 2:
                return ERR_DATA_LENGTH, b""
            w_blocks = payload[0]
            b_blocks = payload[1]
            spec = addr_len + dev_code_len
            nw, st, mio, md = routing[0], routing[1], int.from_bytes(routing[2:4], "little"), routing[4]
            ptr2 = 2
            res = b""
            for _ in range(w_blocks + b_blocks):
                if ptr2 + spec + 2 > len(payload): break
                start = int.from_bytes(payload[ptr2:ptr2+addr_len], "little")
                dc = int.from_bytes(payload[ptr2+addr_len:ptr2+spec], "little")
                points = int.from_bytes(payload[ptr2+spec:ptr2+spec+2], "little")
                ptr2 += spec + 2
                mem = self.context_memory.get((nw, st, mio, md, dc, 0, 0), {})
                for i in range(points):
                    res += mem.get(start + i, 0).to_bytes(2, "little")
            return 0, res

        elif command == Command.DEVICE_WRITE_BLOCK:
            if len(payload) < 2:
                return 0, b""
            w_blocks = payload[0]
            b_blocks = payload[1]
            spec = addr_len + dev_code_len
            nw, st, mio, md = routing[0], routing[1], int.from_bytes(routing[2:4], "little"), routing[4]
            ptr2 = 2
            for _ in range(w_blocks + b_blocks):
                if ptr2 + spec + 2 > len(payload): break
                start = int.from_bytes(payload[ptr2:ptr2+addr_len], "little")
                dc = int.from_bytes(payload[ptr2+addr_len:ptr2+spec], "little")
                points = int.from_bytes(payload[ptr2+spec:ptr2+spec+2], "little")
                ptr2 += spec + 2
                key = (nw, st, mio, md, dc, 0, 0)
                if key not in self.context_memory: self.context_memory[key] = {}
                mem = self.context_memory[key]
                for i in range(points):
                    if ptr2 + 2 > len(payload): break
                    mem[start + i] = int.from_bytes(payload[ptr2:ptr2+2], "little")
                    ptr2 += 2
            return 0, b""

        elif command == Command.SELF_TEST:
            if len(payload) < 2:
                return 0, b"\x00\x00"
            count = int.from_bytes(payload[:2], "little")
            data = payload[2:2+count]
            return 0, count.to_bytes(2, "little") + data

        elif command == Command.MEMORY_READ:
            # payload: head_addr(4) + word_count(2)
            if len(payload) < 6:
                return ERR_DATA_LENGTH, b""
            head = int.from_bytes(payload[0:4], "little")
            word_count = int.from_bytes(payload[4:6], "little")
            res = b""
            for i in range(word_count):
                res += self.memory_store.get(head + i, 0).to_bytes(2, "little")
            return 0, res

        elif command == Command.MEMORY_WRITE:
            # payload: head_addr(4) + word_count(2) + data(word_count*2)
            if len(payload) < 6:
                return 0, b""
            head = int.from_bytes(payload[0:4], "little")
            word_count = int.from_bytes(payload[4:6], "little")
            for i in range(word_count):
                if 6 + i*2 + 2 > len(payload): break
                self.memory_store[head + i] = int.from_bytes(payload[6+i*2:8+i*2], "little")
            return 0, b""

        elif command == Command.EXTEND_UNIT_READ:
            # payload: head_addr(4) + byte_count(2) + module_no(2)
            if len(payload) < 8:
                return ERR_DATA_LENGTH, b""
            head = int.from_bytes(payload[0:4], "little")
            byte_count = int.from_bytes(payload[4:6], "little")
            module_no = int.from_bytes(payload[6:8], "little")
            res = b""
            word_count = byte_count // 2
            for i in range(word_count):
                v = self.extend_unit_store.get((module_no, head + i), 0)
                res += v.to_bytes(2, "little")
            if byte_count % 2:
                res += b"\x00"
            return 0, res

        elif command == Command.EXTEND_UNIT_WRITE:
            # payload: head_addr(4) + byte_count(2) + module_no(2) + data
            if len(payload) < 8:
                return 0, b""
            head = int.from_bytes(payload[0:4], "little")
            byte_count = int.from_bytes(payload[4:6], "little")
            module_no = int.from_bytes(payload[6:8], "little")
            word_count = byte_count // 2
            for i in range(word_count):
                if 8 + i*2 + 2 > len(payload): break
                self.extend_unit_store[(module_no, head + i)] = int.from_bytes(payload[8+i*2:10+i*2], "little")
            return 0, b""

        elif command == Command.LABEL_READ_RANDOM:
            labels = self._parse_random_label_read_payload(payload)
            res = len(labels).to_bytes(2, "little")
            for label in labels:
                data = self.label_store.get(label, b"\x00\x00")
                res += b"\x09\x00" + len(data).to_bytes(2, "little") + data
            return 0, res

        elif command == Command.LABEL_WRITE_RANDOM:
            for label, data in self._parse_random_label_write_payload(payload):
                self.label_store[label] = data
            return 0, b""

        elif command == Command.LABEL_ARRAY_READ:
            points = self._parse_array_label_read_payload(payload)
            res = len(points).to_bytes(2, "little")
            for label, unit_spec, array_length in points:
                data_size = self._label_array_data_size(unit_spec, array_length)
                data = self.label_store.get(label, bytes(data_size))
                data = data[:data_size].ljust(data_size, b"\x00")
                res += b"\x09" + unit_spec.to_bytes(1, "little") + array_length.to_bytes(2, "little") + data
            return 0, res

        elif command == Command.LABEL_ARRAY_WRITE:
            for label, _unit_spec, _array_length, data in self._parse_array_label_write_payload(payload):
                self.label_store[label] = data
            return 0, b""

        elif command in (Command.REMOTE_RUN, Command.REMOTE_STOP, Command.REMOTE_PAUSE, Command.REMOTE_LATCH_CLEAR, Command.REMOTE_RESET):
            return 0, b""

        elif command == Command.READ_TYPE_NAME:
            return 0, b"MOCK-PLC".ljust(16, b"\x00") + b"\x34\x12"

        return 0, b""

    @staticmethod
    def _label_array_data_size(unit_spec: int, array_length: int) -> int:
        return array_length * 2 if unit_spec == 0 else array_length

    @staticmethod
    def _read_label_name(payload: bytes, offset: int) -> tuple[str, int]:
        if offset + 2 > len(payload):
            return "", len(payload)
        char_count = int.from_bytes(payload[offset:offset+2], "little")
        offset += 2
        byte_count = char_count * 2
        raw = payload[offset:offset+byte_count]
        offset += byte_count
        return raw.decode("utf-16-le", errors="replace"), offset

    def _skip_abbreviation_labels(self, payload: bytes, offset: int, count: int) -> int:
        for _ in range(count):
            _, offset = self._read_label_name(payload, offset)
        return offset

    def _parse_random_label_read_payload(self, payload: bytes) -> list[str]:
        if len(payload) < 4:
            return []
        count = int.from_bytes(payload[0:2], "little")
        abbrev_count = int.from_bytes(payload[2:4], "little")
        offset = self._skip_abbreviation_labels(payload, 4, abbrev_count)
        labels = []
        for _ in range(count):
            label, offset = self._read_label_name(payload, offset)
            labels.append(label)
        return labels

    def _parse_random_label_write_payload(self, payload: bytes) -> list[tuple[str, bytes]]:
        if len(payload) < 4:
            return []
        count = int.from_bytes(payload[0:2], "little")
        abbrev_count = int.from_bytes(payload[2:4], "little")
        offset = self._skip_abbreviation_labels(payload, 4, abbrev_count)
        points = []
        for _ in range(count):
            label, offset = self._read_label_name(payload, offset)
            if offset + 2 > len(payload):
                break
            data_len = int.from_bytes(payload[offset:offset+2], "little")
            offset += 2
            points.append((label, payload[offset:offset+data_len]))
            offset += data_len
        return points

    def _parse_array_label_read_payload(self, payload: bytes) -> list[tuple[str, int, int]]:
        if len(payload) < 4:
            return []
        count = int.from_bytes(payload[0:2], "little")
        abbrev_count = int.from_bytes(payload[2:4], "little")
        offset = self._skip_abbreviation_labels(payload, 4, abbrev_count)
        points = []
        for _ in range(count):
            label, offset = self._read_label_name(payload, offset)
            if offset + 4 > len(payload):
                break
            unit_spec = payload[offset]
            array_length = int.from_bytes(payload[offset+2:offset+4], "little")
            offset += 4
            points.append((label, unit_spec, array_length))
        return points

    def _parse_array_label_write_payload(self, payload: bytes) -> list[tuple[str, int, int, bytes]]:
        if len(payload) < 4:
            return []
        count = int.from_bytes(payload[0:2], "little")
        abbrev_count = int.from_bytes(payload[2:4], "little")
        offset = self._skip_abbreviation_labels(payload, 4, abbrev_count)
        points = []
        for _ in range(count):
            label, offset = self._read_label_name(payload, offset)
            if offset + 4 > len(payload):
                break
            unit_spec = payload[offset]
            array_length = int.from_bytes(payload[offset+2:offset+4], "little")
            offset += 4
            data_size = self._label_array_data_size(unit_spec, array_length)
            points.append((label, unit_spec, array_length, payload[offset:offset+data_size]))
            offset += data_size
        return points

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--log-json", default=None)
    args = parser.parse_args()
    asyncio.run(SlmpMockServer(args.host, args.port, args.log_json).start())

