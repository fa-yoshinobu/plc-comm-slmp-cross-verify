from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .adapters import AdapterSpec, adapter_available, prepare_adapter, run_adapter


ROOT = Path(__file__).resolve().parents[1]


def load_suite(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_port(port: int, timeout_sec: float = 5.0) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            try:
                sock.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.05)
    raise TimeoutError(f"mock PLC did not start on port {port}")


def count_log_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def get_new_requests(path: Path, line_count_before: int) -> list[str]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        lines = handle.readlines()[line_count_before:]
    requests: list[str] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("direction") == "REQ":
            requests.append(entry["data"])
    return requests


def normalize_packet(packet_hex: str, frame: str) -> str:
    raw = bytes.fromhex(packet_hex)
    if frame == "4e" and len(raw) >= 6 and raw[:2] == bytes.fromhex("5400"):
        raw = raw[:2] + b"\x00\x00\x00\x00" + raw[6:]
    return raw.hex()


def normalize_result(command: str, result: dict[str, Any]) -> Any:
    if result.get("status") != "success":
        return {"status": result.get("status"), "message": result.get("message", "")}
    if command in {"write", "write-named", "remote-run", "remote-stop", "remote-pause", "remote-latch-clear", "remote-reset", "random-write-words", "random-write-bits", "block-write", "memory-write", "extend-unit-write"}:
        return {"status": "success"}
    if command in {"read", "memory-read", "extend-unit-read", "read-ext"}:
        return {"values": result.get("values", [])}
    if command == "read-type":
        return {"model": result.get("model"), "model_code": result.get("model_code")}
    if command == "random-read":
        return {
            "word_values": result.get("word_values", []),
            "dword_values": result.get("dword_values", []),
        }
    if command == "block-read":
        if "word_values" in result or "bit_values" in result:
            return {
                "word_values": result.get("word_values", []),
                "bit_values": result.get("bit_values", []),
            }
        word_blocks = result.get("word_blocks", [])
        bit_blocks = result.get("bit_blocks", [])
        return {
            "word_values": [value for _, values in word_blocks for value in values],
            "bit_values": [value for _, values in bit_blocks for value in values],
        }
    if command in {"read-named", "poll-once"}:
        return {
            "addresses": result.get("addresses", []),
            "values": result.get("values", []),
        }
    if command == "self-test":
        return {"echo": result.get("echo", "")}
    return result


def values_equivalent(left: Any, right: Any, tolerance: float = 1e-6) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= tolerance
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(values_equivalent(a, b, tolerance) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        if left.keys() != right.keys():
            return False
        return all(values_equivalent(left[key], right[key], tolerance) for key in left)
    return left == right


def contains_expected(actual: Any, expected: Any, tolerance: float = 1e-6) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(key in actual and contains_expected(actual[key], value, tolerance) for key, value in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(contains_expected(a, b, tolerance) for a, b in zip(actual, expected))
    return values_equivalent(actual, expected, tolerance)


def compare_expected(command: str, result: dict[str, Any], expected: dict[str, Any] | None) -> tuple[bool, str | None]:
    if not expected:
        return True, None
    status = expected.get("status")
    if status is not None and result.get("status") != status:
        return False, f"expected status={status} actual={result.get('status')}"
    if "result" not in expected:
        return True, None
    normalized = normalize_result(command, result)
    if not values_equivalent(normalized, expected["result"]):
        return False, f"expected result={json.dumps(expected['result'], ensure_ascii=False)} actual={json.dumps(normalized, ensure_ascii=False)}"
    return True, None


def start_server(port: int, case_server_config: dict[str, Any], log_path: Path, snapshot_path: Path) -> tuple[subprocess.Popen[str], Path]:
    temp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".json")
    try:
        json.dump(case_server_config, temp, ensure_ascii=False, indent=2)
        temp.close()
        case_path = Path(temp.name)
    except Exception:
        temp.close()
        raise

    command = [
        sys.executable,
        "-u",
        str(ROOT / "server" / "stateful_mock_plc.py"),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--case-file",
        str(case_path),
        "--log-json",
        str(log_path),
        "--snapshot-out",
        str(snapshot_path),
    ]
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    wait_for_port(port)
    return process, case_path


def stop_server(process: subprocess.Popen[str], case_path: Path) -> tuple[str, str]:
    process.terminate()
    try:
        stdout, stderr = process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=3)
    try:
        case_path.unlink(missing_ok=True)
    except OSError:
        pass
    return stdout, stderr


def build_markdown(report: dict[str, Any], selected_impls: list[str]) -> str:
    lines = [
        "# SLMP Conformance Report",
        "",
        f"- Suite: `{report['suite']}`",
        f"- Cases: `{report['summary']['cases']}`",
        f"- Steps: `{report['summary']['steps']}`",
        f"- Passed: `{report['summary']['passed_steps']}`",
        f"- Failed: `{report['summary']['failed_steps']}`",
        "",
        "## Implementation Coverage",
        "",
        "| Implementation | Passed | Failed | Not Implemented | Unavailable | Skipped |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in selected_impls:
        counts = report["coverage"].get(name, {})
        lines.append(
            f"| {name} | {counts.get('passed', 0)} | {counts.get('failed', 0)} | {counts.get('not_implemented', 0)} | {counts.get('unavailable', 0)} | {counts.get('skipped', 0)} |"
        )

    lines.extend(["", "## Scenario Matrix", "", f"| Scenario | {' | '.join(selected_impls)} |", f"| --- | {' | '.join(['---'] * len(selected_impls))} |"])
    for case in report["cases"]:
        cells = []
        for name in selected_impls:
            cells.append(case["implementation_status"].get(name, "n/a"))
        lines.append(f"| {case['id']} | {' | '.join(cells)} |")
    return "\n".join(lines) + "\n"


def run_suite(
    suite_path: Path,
    adapter_specs: dict[str, AdapterSpec],
    selected_impls: list[str],
    keep_artifacts: bool = False,
) -> dict[str, Any]:
    suite = load_suite(suite_path)
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)

    for name in selected_impls:
        spec = adapter_specs[name]
        prepare_adapter(spec, ROOT)

    report: dict[str, Any] = {
        "suite": suite.get("suite", suite_path.stem),
        "cases": [],
        "coverage": {
            name: {"passed": 0, "failed": 0, "not_implemented": 0, "skipped": 0, "unavailable": 0}
            for name in selected_impls
        },
        "summary": {"cases": 0, "steps": 0, "passed_steps": 0, "failed_steps": 0},
    }

    for case in suite["cases"]:
        report["summary"]["cases"] += 1
        case_impls = [name for name in case.get("implementations", selected_impls) if name in selected_impls]
        port = allocate_port()
        log_path = ROOT / "tmp" / f"{case['id']}_packets.jsonl"
        snapshot_path = ROOT / "tmp" / f"{case['id']}_snapshot.json"
        log_path.parent.mkdir(exist_ok=True)
        if log_path.exists():
            log_path.unlink()
        if snapshot_path.exists():
            snapshot_path.unlink()

        process, case_path = start_server(port, case.get("server", {}), log_path, snapshot_path)
        participant_impls: set[str] = set()
        case_result: dict[str, Any] = {
            "id": case["id"],
            "description": case.get("description", ""),
            "steps": [],
            "implementation_status": {name: "skip" for name in selected_impls},
        }

        try:
            for step in case["steps"]:
                report["summary"]["steps"] += 1
                step_result: dict[str, Any] = {"id": step["id"], "results": {}, "checks": []}
                active_impls: list[str] = []

                for name in case_impls:
                    spec = adapter_specs[name]
                    required = set(step.get("require_capabilities", []))
                    if not required.issubset(spec.capabilities):
                        step_result["results"][name] = {"status": "not_implemented", "reason": "capability"}
                        report["coverage"][name]["not_implemented"] += 1
                        if case_result["implementation_status"][name] == "skip":
                            case_result["implementation_status"][name] = "not_implemented"
                        continue
                    if not adapter_available(spec):
                        step_result["results"][name] = {"status": "unavailable", "reason": "adapter"}
                        report["coverage"][name]["unavailable"] += 1
                        if case_result["implementation_status"][name] == "skip":
                            case_result["implementation_status"][name] = "unavailable"
                        continue

                    before = count_log_lines(log_path)
                    result = run_adapter(spec, ROOT, "127.0.0.1", port, step, timeout_sec=float(step.get("timeout_sec", 6.0)))
                    result["_requests"] = get_new_requests(log_path, before)
                    step_result["results"][name] = result
                    active_impls.append(name)
                    participant_impls.add(name)

                expected = step.get("expect", {})
                for name in active_impls:
                    ok, message = compare_expected(step["command"], step_result["results"][name], expected)
                    if not ok:
                        step_result["checks"].append({"kind": "expected", "implementation": name, "ok": False, "message": message})

                checks = step.get("checks", {})
                if checks.get("status_parity", True) and active_impls:
                    statuses = {name: step_result["results"][name].get("status") for name in active_impls}
                    unique = set(statuses.values())
                    if len(unique) > 1:
                        step_result["checks"].append({"kind": "status_parity", "ok": False, "message": json.dumps(statuses, ensure_ascii=False)})

                if checks.get("result_parity", False) and active_impls:
                    baseline = active_impls[0]
                    left = normalize_result(step["command"], step_result["results"][baseline])
                    for name in active_impls[1:]:
                        right = normalize_result(step["command"], step_result["results"][name])
                        if not values_equivalent(left, right):
                            step_result["checks"].append(
                                {
                                    "kind": "result_parity",
                                    "ok": False,
                                    "message": f"{baseline} vs {name}: {json.dumps(left, ensure_ascii=False)} != {json.dumps(right, ensure_ascii=False)}",
                                }
                            )
                            break

                if checks.get("packet_parity", False) and active_impls:
                    frame = step.get("flags", {}).get("frame", "3e")
                    baseline = active_impls[0]
                    left = [normalize_packet(packet, frame) for packet in step_result["results"][baseline].get("_requests", [])]
                    for name in active_impls[1:]:
                        right = [normalize_packet(packet, frame) for packet in step_result["results"][name].get("_requests", [])]
                        if left != right:
                            step_result["checks"].append(
                                {
                                    "kind": "packet_parity",
                                    "ok": False,
                                    "message": f"{baseline}={left} {name}={right}",
                                }
                            )
                            break
                    expected_packets = expected.get("packets")
                    if expected_packets:
                        normalized_expected = [normalize_packet(packet, frame) for packet in expected_packets]
                        for name in active_impls:
                            actual = [normalize_packet(packet, frame) for packet in step_result["results"][name].get("_requests", [])]
                            if actual != normalized_expected:
                                step_result["checks"].append(
                                    {
                                        "kind": "expected_packets",
                                        "ok": False,
                                        "message": f"{name} expected={normalized_expected} actual={actual}",
                                    }
                                )

                step_failed = any(not check.get("ok", False) for check in step_result["checks"])
                if step_failed:
                    report["summary"]["failed_steps"] += 1
                else:
                    report["summary"]["passed_steps"] += 1

                for name in selected_impls:
                    entry = step_result["results"].get(name)
                    if not entry:
                        continue
                    if entry.get("status") == "not_implemented" or entry.get("status") == "unavailable":
                        continue
                    if step_failed:
                        report["coverage"][name]["failed"] += 1
                        case_result["implementation_status"][name] = "fail"
                    else:
                        report["coverage"][name]["passed"] += 1
                        if case_result["implementation_status"][name] != "fail":
                            case_result["implementation_status"][name] = "pass"

                case_result["steps"].append(step_result)
        finally:
            stdout, stderr = stop_server(process, case_path)
            case_result["server_stdout"] = stdout.strip()
            case_result["server_stderr"] = stderr.strip()
            if snapshot_path.exists():
                case_result["final_snapshot"] = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if "expect_snapshot" in case:
                report["summary"]["steps"] += 1
                snapshot_step: dict[str, Any] = {
                    "id": "final_snapshot",
                    "results": {"server": {"status": "success", "snapshot": case_result.get("final_snapshot")}},
                    "checks": [],
                }
                actual_snapshot = case_result.get("final_snapshot")
                if actual_snapshot is None:
                    snapshot_step["checks"].append({"kind": "snapshot", "ok": False, "message": "snapshot not captured"})
                elif not contains_expected(actual_snapshot, case["expect_snapshot"]):
                    snapshot_step["checks"].append(
                        {
                            "kind": "snapshot",
                            "ok": False,
                            "message": (
                                f"expected snapshot={json.dumps(case['expect_snapshot'], ensure_ascii=False)} "
                                f"actual={json.dumps(actual_snapshot, ensure_ascii=False)}"
                            ),
                        }
                    )
                snapshot_failed = any(not check.get("ok", False) for check in snapshot_step["checks"])
                if snapshot_failed:
                    report["summary"]["failed_steps"] += 1
                else:
                    report["summary"]["passed_steps"] += 1
                for name in sorted(participant_impls):
                    if snapshot_failed:
                        report["coverage"][name]["failed"] += 1
                        case_result["implementation_status"][name] = "fail"
                    else:
                        report["coverage"][name]["passed"] += 1
                        if case_result["implementation_status"][name] != "fail":
                            case_result["implementation_status"][name] = "pass"
                case_result["steps"].append(snapshot_step)
            if not keep_artifacts:
                log_path.unlink(missing_ok=True)
                snapshot_path.unlink(missing_ok=True)

        report["cases"].append(case_result)

    markdown = build_markdown(report, selected_impls)
    (reports_dir / "latest_conformance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (reports_dir / "latest_conformance.md").write_text(markdown, encoding="utf-8")
    return report
