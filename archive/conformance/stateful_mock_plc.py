from __future__ import annotations

import argparse
import asyncio
import json
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class Command:
    DEVICE_READ = 0x0401
    DEVICE_WRITE = 0x1401
    DEVICE_READ_RANDOM = 0x0403
    DEVICE_WRITE_RANDOM = 0x1402
    DEVICE_READ_BLOCK = 0x0406
    DEVICE_WRITE_BLOCK = 0x1406
    READ_TYPE_NAME = 0x0101
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


SUBCOMMAND_QL_WORD = 0x0000
SUBCOMMAND_QL_BIT = 0x0001
SUBCOMMAND_IQR_WORD = 0x0002
SUBCOMMAND_IQR_BIT = 0x0003
SUBCOMMAND_QL_WORD_EXT = 0x0080
SUBCOMMAND_QL_BIT_EXT = 0x0081
SUBCOMMAND_IQR_WORD_EXT = 0x0082
SUBCOMMAND_IQR_BIT_EXT = 0x0083


ERR_DEVICE_NOT_FOUND = 0xC051
ERR_DATA_LENGTH = 0xC056


@dataclass
class FaultAction:
    end_code: int | None = None
    payload_hex: str | None = None
    delay_ms: int = 0
    close_without_response: bool = False
    malformed_length_delta: int = 0


@dataclass
class FaultRule:
    command: int
    subcommand: int | None = None
    hit: int = 1
    per_session: bool = True
    action: FaultAction = field(default_factory=FaultAction)
    seen: int = 0
    seen_by_session: dict[int, int] = field(default_factory=dict)

    def matches(self, command: int, subcommand: int, session_id: int) -> bool:
        if self.command != command:
            return False
        if self.subcommand is not None and self.subcommand != subcommand:
            return False
        if self.per_session:
            seen = self.seen_by_session.get(session_id, 0) + 1
            self.seen_by_session[session_id] = seen
            return seen == self.hit
        self.seen += 1
        return self.seen == self.hit


class SlmpMockServer:
    def __init__(self, host: str, port: int, case_file: Path, log_json: Path | None, snapshot_out: Path | None) -> None:
        self.host = host
        self.port = port
        self.log_json = log_json
        self.snapshot_out = snapshot_out
        self.server: asyncio.AbstractServer | None = None
        self._log_fp = None
        self.case_data = json.loads(case_file.read_text(encoding="utf-8")) if case_file.exists() else {}
        self.faults = self._load_faults(self.case_data.get("faults", []))
        self.type_name_model = self.case_data.get("type_name", {}).get("model", "MOCK-PLC")
        self.type_name_code = int(self.case_data.get("type_name", {}).get("model_code", 0x1234))
        self.context_memory: dict[tuple[int, int, int, int, int], dict[int, int]] = {}
        self.memory_store: dict[int, int] = {}
        self.extend_unit_store: dict[tuple[int, int], int] = {}
        self.request_count = 0
        self.session_counter = 0
        self._stop_event = asyncio.Event()

    def _load_faults(self, items: list[dict[str, Any]]) -> list[FaultRule]:
        rules: list[FaultRule] = []
        for item in items:
            action = FaultAction(
                end_code=int(item.get("action", {}).get("end_code")) if item.get("action", {}).get("end_code") is not None else None,
                payload_hex=item.get("action", {}).get("payload_hex"),
                delay_ms=int(item.get("action", {}).get("delay_ms", 0)),
                close_without_response=bool(item.get("action", {}).get("close_without_response", False)),
                malformed_length_delta=int(item.get("action", {}).get("malformed_length_delta", 0)),
            )
            rules.append(
                FaultRule(
                    command=int(item["command"]),
                    subcommand=int(item["subcommand"]) if item.get("subcommand") is not None else None,
                    hit=int(item.get("hit", 1)),
                    per_session=bool(item.get("per_session", True)),
                    action=action,
                )
            )
        return rules

    async def start(self) -> None:
        if self.log_json:
            self.log_json.parent.mkdir(parents=True, exist_ok=True)
            self._log_fp = self.log_json.open("w", encoding="utf-8")
        try:
            self.server = await asyncio.start_server(self.handle_client, self.host, self.port)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, self.request_shutdown)
                except NotImplementedError:
                    pass
            async with self.server:
                await self._stop_event.wait()
        finally:
            if self._log_fp:
                self._log_fp.close()
            if self.snapshot_out:
                self.snapshot_out.parent.mkdir(parents=True, exist_ok=True)
                self.snapshot_out.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")

    def request_shutdown(self) -> None:
        self._stop_event.set()
        if self.server is not None:
            self.server.close()

    def snapshot(self) -> dict[str, Any]:
        contexts = {}
        for key, values in self.context_memory.items():
            contexts[str(key)] = {str(addr): value for addr, value in sorted(values.items())}
        return {
            "request_count": self.request_count,
            "contexts": contexts,
            "memory_store": {str(addr): value for addr, value in sorted(self.memory_store.items())},
            "extend_unit_store": {f"{module}:{head}": value for (module, head), value in sorted(self.extend_unit_store.items())},
        }

    def log(self, session_id: int, direction: str, data: bytes, routing: bytes) -> None:
        if not self._log_fp:
            return
        entry = {
            "session_id": session_id,
            "direction": direction,
            "routing": {
                "network": routing[0],
                "station": routing[1],
                "module_io": int.from_bytes(routing[2:4], "little"),
                "multidrop": routing[4],
            },
            "data": data.hex(),
        }
        self._log_fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._log_fp.flush()

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.session_counter += 1
        session_id = self.session_counter
        try:
            while True:
                subheader = await reader.readexactly(2)
                is_3e = subheader == b"\x50\x00"
                is_4e = subheader == b"\x54\x00"
                if not is_3e and not is_4e:
                    return

                routing = await reader.readexactly(5 if is_3e else 9)
                routing_only = routing if is_3e else routing[4:9]
                data_len = int.from_bytes(await reader.readexactly(2), "little")
                body = await reader.readexactly(data_len)
                request = subheader + routing + data_len.to_bytes(2, "little") + body
                self.request_count += 1
                self.log(session_id, "REQ", request, routing_only)

                command = int.from_bytes(body[2:4], "little")
                subcommand = int.from_bytes(body[4:6], "little")
                fault = self.find_fault(command, subcommand, session_id)
                if fault and fault.delay_ms > 0:
                    await asyncio.sleep(fault.delay_ms / 1000.0)
                if fault and fault.close_without_response:
                    writer.close()
                    await writer.wait_closed()
                    return

                end_code, payload = self.process_command(command, subcommand, body[6:], routing_only)
                if fault and fault.end_code is not None:
                    end_code = fault.end_code
                if fault and fault.payload_hex is not None:
                    payload = bytes.fromhex(fault.payload_hex)

                response = self.build_response(is_3e, request, routing, end_code, payload, fault.malformed_length_delta if fault else 0)
                writer.write(response)
                await writer.drain()
                self.log(session_id, "RES", response, routing_only)
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def find_fault(self, command: int, subcommand: int, session_id: int) -> FaultAction | None:
        for rule in self.faults:
            if rule.matches(command, subcommand, session_id):
                return rule.action
        return None

    @staticmethod
    def build_response(is_3e: bool, request: bytes, routing: bytes, end_code: int, payload: bytes, malformed_length_delta: int) -> bytes:
        response_body = end_code.to_bytes(2, "little") + payload
        header = bytearray()
        header += b"\xD0\x00" if is_3e else b"\xD4\x00"
        header += routing
        header += max(0, len(response_body) + malformed_length_delta).to_bytes(2, "little")
        return bytes(header + response_body)

    def _context(self, routing: bytes, device_code: int) -> dict[int, int]:
        key = (
            routing[0],
            routing[1],
            int.from_bytes(routing[2:4], "little"),
            routing[4],
            device_code,
        )
        return self.context_memory.setdefault(key, {})

    def _read_bit_block_word(self, routing: bytes, device_code: int, start: int) -> int:
        memory = self._context(routing, device_code)
        value = 0
        for bit_index in range(16):
            if memory.get(start + bit_index, 0):
                value |= 1 << bit_index
        return value

    def _write_bit_block_word(self, routing: bytes, device_code: int, start: int, value: int) -> None:
        memory = self._context(routing, device_code)
        for bit_index in range(16):
            memory[start + bit_index] = 1 if value & (1 << bit_index) else 0

    def process_command(self, command: int, subcommand: int, payload: bytes, routing: bytes) -> tuple[int, bytes]:
        is_ext = subcommand in (SUBCOMMAND_QL_WORD_EXT, SUBCOMMAND_QL_BIT_EXT, SUBCOMMAND_IQR_WORD_EXT, SUBCOMMAND_IQR_BIT_EXT)
        is_iqr = subcommand in (SUBCOMMAND_IQR_WORD, SUBCOMMAND_IQR_BIT, SUBCOMMAND_IQR_WORD_EXT, SUBCOMMAND_IQR_BIT_EXT)
        is_bit = subcommand in (SUBCOMMAND_QL_BIT, SUBCOMMAND_IQR_BIT, SUBCOMMAND_QL_BIT_EXT, SUBCOMMAND_IQR_BIT_EXT)
        addr_len = 4 if is_iqr else 3
        code_len = 2 if is_iqr else 1
        spec_len = addr_len + code_len

        if command == Command.READ_TYPE_NAME:
            model = self.type_name_model.encode("ascii", errors="replace")[:16].ljust(16, b" ")
            return 0, model + self.type_name_code.to_bytes(2, "little")

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

        if command == Command.DEVICE_READ:
            start = int.from_bytes(payload[0:addr_len], "little")
            code = int.from_bytes(payload[addr_len:spec_len], "little")
            count = int.from_bytes(payload[spec_len:spec_len + 2], "little")
            if code == 0xEE:
                return ERR_DEVICE_NOT_FOUND, b""
            if count > 1000:
                return ERR_DATA_LENGTH, b""
            memory = self._context(routing, code)
            if is_bit:
                result = bytearray()
                for index in range(0, count, 2):
                    first = 1 if memory.get(start + index, 0) else 0
                    second = 1 if memory.get(start + index + 1, 0) else 0
                    result.append((first << 4) | second)
                return 0, bytes(result)
            result = bytearray()
            for index in range(count):
                result += int(memory.get(start + index, 0)).to_bytes(2, "little")
            return 0, bytes(result)

        if command == Command.DEVICE_WRITE:
            start = int.from_bytes(payload[0:addr_len], "little")
            code = int.from_bytes(payload[addr_len:spec_len], "little")
            count = int.from_bytes(payload[spec_len:spec_len + 2], "little")
            if code == 0xEE:
                return ERR_DEVICE_NOT_FOUND, b""
            memory = self._context(routing, code)
            cursor = spec_len + 2
            if is_bit:
                for index in range(0, count, 2):
                    packed = payload[cursor + index // 2]
                    memory[start + index] = (packed >> 4) & 0x1
                    if index + 1 < count:
                        memory[start + index + 1] = packed & 0x1
            else:
                for index in range(count):
                    memory[start + index] = int.from_bytes(payload[cursor + index * 2 : cursor + index * 2 + 2], "little")
            return 0, b""

        if command == Command.DEVICE_READ_RANDOM:
            word_count = payload[0]
            dword_count = payload[1]
            cursor = 2
            response = bytearray()
            for _ in range(word_count):
                start = int.from_bytes(payload[cursor : cursor + addr_len], "little")
                code = int.from_bytes(payload[cursor + addr_len : cursor + spec_len], "little")
                memory = self._context(routing, code)
                response += int(memory.get(start, 0)).to_bytes(2, "little")
                cursor += spec_len
            for _ in range(dword_count):
                start = int.from_bytes(payload[cursor : cursor + addr_len], "little")
                code = int.from_bytes(payload[cursor + addr_len : cursor + spec_len], "little")
                memory = self._context(routing, code)
                low = int(memory.get(start, 0))
                high = int(memory.get(start + 1, 0))
                response += ((high << 16) | low).to_bytes(4, "little")
                cursor += spec_len
            return 0, bytes(response)

        if command == Command.DEVICE_WRITE_RANDOM:
            word_count = payload[0]
            dword_count = payload[1]
            cursor = 2
            if is_bit:
                value_size = 2 if is_iqr else 1
                for _ in range(word_count):
                    start = int.from_bytes(payload[cursor : cursor + addr_len], "little")
                    code = int.from_bytes(payload[cursor + addr_len : cursor + spec_len], "little")
                    value = int.from_bytes(payload[cursor + spec_len : cursor + spec_len + value_size], "little")
                    self._context(routing, code)[start] = 1 if value else 0
                    cursor += spec_len + value_size
                return 0, b""

            for _ in range(word_count):
                start = int.from_bytes(payload[cursor : cursor + addr_len], "little")
                code = int.from_bytes(payload[cursor + addr_len : cursor + spec_len], "little")
                value = int.from_bytes(payload[cursor + spec_len : cursor + spec_len + 2], "little")
                self._context(routing, code)[start] = value
                cursor += spec_len + 2
            for _ in range(dword_count):
                start = int.from_bytes(payload[cursor : cursor + addr_len], "little")
                code = int.from_bytes(payload[cursor + addr_len : cursor + spec_len], "little")
                value = int.from_bytes(payload[cursor + spec_len : cursor + spec_len + 4], "little")
                memory = self._context(routing, code)
                memory[start] = value & 0xFFFF
                memory[start + 1] = (value >> 16) & 0xFFFF
                cursor += spec_len + 4
            return 0, b""

        if command == Command.DEVICE_READ_BLOCK:
            word_blocks = payload[0]
            bit_blocks = payload[1]
            cursor = 2
            response = bytearray()
            word_specs: list[tuple[int, int, int]] = []
            bit_specs: list[tuple[int, int, int]] = []
            for _ in range(word_blocks):
                start = int.from_bytes(payload[cursor : cursor + addr_len], "little")
                code = int.from_bytes(payload[cursor + addr_len : cursor + spec_len], "little")
                points = int.from_bytes(payload[cursor + spec_len : cursor + spec_len + 2], "little")
                cursor += spec_len + 2
                word_specs.append((start, code, points))
            for _ in range(bit_blocks):
                start = int.from_bytes(payload[cursor : cursor + addr_len], "little")
                code = int.from_bytes(payload[cursor + addr_len : cursor + spec_len], "little")
                points = int.from_bytes(payload[cursor + spec_len : cursor + spec_len + 2], "little")
                cursor += spec_len + 2
                bit_specs.append((start, code, points))
            for start, code, points in word_specs:
                memory = self._context(routing, code)
                for index in range(points):
                    response += int(memory.get(start + index, 0)).to_bytes(2, "little")
            for start, code, points in bit_specs:
                for index in range(points):
                    response += self._read_bit_block_word(routing, code, start + index * 16).to_bytes(2, "little")
            return 0, bytes(response)

        if command == Command.DEVICE_WRITE_BLOCK:
            word_blocks = payload[0]
            bit_blocks = payload[1]
            cursor = 2
            word_specs: list[tuple[int, int, int]] = []
            bit_specs: list[tuple[int, int, int]] = []
            for _ in range(word_blocks):
                start = int.from_bytes(payload[cursor : cursor + addr_len], "little")
                code = int.from_bytes(payload[cursor + addr_len : cursor + spec_len], "little")
                points = int.from_bytes(payload[cursor + spec_len : cursor + spec_len + 2], "little")
                cursor += spec_len + 2
                word_specs.append((start, code, points))
            for _ in range(bit_blocks):
                start = int.from_bytes(payload[cursor : cursor + addr_len], "little")
                code = int.from_bytes(payload[cursor + addr_len : cursor + spec_len], "little")
                points = int.from_bytes(payload[cursor + spec_len : cursor + spec_len + 2], "little")
                cursor += spec_len + 2
                bit_specs.append((start, code, points))
            for start, code, points in word_specs:
                memory = self._context(routing, code)
                for index in range(points):
                    memory[start + index] = int.from_bytes(payload[cursor : cursor + 2], "little")
                    cursor += 2
            for start, code, points in bit_specs:
                for index in range(points):
                    value = int.from_bytes(payload[cursor : cursor + 2], "little")
                    self._write_bit_block_word(routing, code, start + index * 16, value)
                    cursor += 2
            return 0, b""

        if command == Command.SELF_TEST:
            count = int.from_bytes(payload[:2], "little")
            loopback = payload[2 : 2 + count]
            return 0, count.to_bytes(2, "little") + loopback

        if command == Command.MEMORY_READ:
            head = int.from_bytes(payload[0:4], "little")
            word_count = int.from_bytes(payload[4:6], "little")
            response = bytearray()
            for index in range(word_count):
                response += int(self.memory_store.get(head + index, 0)).to_bytes(2, "little")
            return 0, bytes(response)

        if command == Command.MEMORY_WRITE:
            head = int.from_bytes(payload[0:4], "little")
            word_count = int.from_bytes(payload[4:6], "little")
            cursor = 6
            for index in range(word_count):
                self.memory_store[head + index] = int.from_bytes(payload[cursor : cursor + 2], "little")
                cursor += 2
            return 0, b""

        if command == Command.EXTEND_UNIT_READ:
            head = int.from_bytes(payload[0:4], "little")
            byte_count = int.from_bytes(payload[4:6], "little")
            module_no = int.from_bytes(payload[6:8], "little")
            response = bytearray()
            for index in range(byte_count // 2):
                response += int(self.extend_unit_store.get((module_no, head + index), 0)).to_bytes(2, "little")
            if byte_count % 2:
                response += b"\x00"
            return 0, bytes(response)

        if command == Command.EXTEND_UNIT_WRITE:
            head = int.from_bytes(payload[0:4], "little")
            byte_count = int.from_bytes(payload[4:6], "little")
            module_no = int.from_bytes(payload[6:8], "little")
            cursor = 8
            for index in range(byte_count // 2):
                self.extend_unit_store[(module_no, head + index)] = int.from_bytes(payload[cursor : cursor + 2], "little")
                cursor += 2
            return 0, b""

        if command in {Command.REMOTE_RUN, Command.REMOTE_STOP, Command.REMOTE_PAUSE, Command.REMOTE_LATCH_CLEAR, Command.REMOTE_RESET}:
            return 0, b""

        return 0, b""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--case-file", type=Path, required=True)
    parser.add_argument("--log-json", type=Path)
    parser.add_argument("--snapshot-out", type=Path)
    args = parser.parse_args()
    server = SlmpMockServer(args.host, args.port, args.case_file, args.log_json, args.snapshot_out)
    asyncio.run(server.start())


if __name__ == "__main__":
    main()
