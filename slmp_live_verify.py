import argparse
import json
import os
import time
from datetime import UTC, datetime

from slmp_interactive_sender import LIVE_CASES_FILE, ROOT, normalize_response, parse_end_code, send_hex


LATEST_JSON = f"{ROOT}/logs/latest_live_verify.json"
LATEST_MD = f"{ROOT}/logs/latest_live_verify.md"


def response_data_length(resp_hex):
    try:
        raw = bytes.fromhex(resp_hex)
        if raw[:2] == bytes.fromhex("d000"):
            return int.from_bytes(raw[7:9], "little") - 2
        if raw[:2] == bytes.fromhex("d400"):
            return int.from_bytes(raw[11:13], "little") - 2
    except Exception:
        pass
    return None


def load_cases(path):
    cases = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("type") == "LIVE_CASE":
                cases.append(entry)
    return cases


def compare_response(actual_hex, expected_hex, expected_end_code, expected_length, mode):
    actual_norm = normalize_response(actual_hex)
    parsed_end_code = parse_end_code(actual_hex)
    actual_end_code = parsed_end_code[0] if isinstance(parsed_end_code, tuple) else parsed_end_code
    actual_length = response_data_length(actual_hex)
    if mode == "exact":
        ok = actual_norm == expected_hex
        reason = None if ok else f"expected={expected_hex} actual={actual_norm}"
        return ok, reason
    if mode == "shape":
        ok = actual_end_code == expected_end_code and actual_length == expected_length
        reason = None if ok else (
            f"expected_end_code={expected_end_code} actual_end_code={actual_end_code} "
            f"expected_len={expected_length} actual_len={actual_length}"
        )
        return ok, reason
    ok = actual_end_code == expected_end_code
    reason = None if ok else f"expected_end_code={expected_end_code} actual_end_code={actual_end_code}"
    return ok, reason


def resolve_expectations(case, profile):
    mode = case.get("comparison_mode", "exact")
    responses = case.get("baseline_responses", [])
    end_codes = case.get("baseline_response_end_codes", [])
    lengths = case.get("baseline_response_data_lengths", [])
    note = None
    if not profile:
        return mode, responses, end_codes, lengths, note

    override = (case.get("live_profiles") or {}).get(profile)
    if not override:
        return mode, responses, end_codes, lengths, note

    return (
        override.get("comparison_mode", mode),
        override.get("responses", responses),
        override.get("end_codes", end_codes),
        override.get("lengths", lengths),
        override.get("note"),
    )


def should_run_case(case, include_stateful, include_remote, pattern):
    if pattern and pattern not in case.get("name", ""):
        return False
    replay_class = case.get("replay_class", "stateful")
    if replay_class == "remote_control" and not include_remote:
        return False
    if replay_class == "stateful" and not include_stateful:
        return False
    return True


def iter_variants(case, mode):
    if mode == "all-clients":
        for client_name, payload in case.get("clients", {}).items():
            yield client_name, payload.get("requests", []), payload.get("responses", []), payload.get("response_end_codes", []), payload.get("response_data_lengths", [])
        return
    baseline_client = case.get("baseline_client")
    yield (
        baseline_client or "baseline",
        case.get("baseline_requests", []),
        case.get("baseline_responses", []),
        case.get("baseline_response_end_codes", []),
        case.get("baseline_response_data_lengths", []),
    )


def build_markdown(report):
    lines = [
        "# SLMP Live Verification Report",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Target: `{report['target']['ip']}:{report['target']['port']}`",
        f"- Profile: `{report['profile'] or 'baseline'}`",
        f"- Mode: `{report['mode']}`",
        f"- Cases run: `{report['summary']['run_cases']}`",
        f"- Passed: `{report['summary']['passed']}`",
        f"- Failed: `{report['summary']['failed']}`",
        f"- Skipped: `{report['summary']['skipped']}`",
        "",
    ]
    if report["failures"]:
        lines.extend(["## Failures", ""])
        for failure in report["failures"]:
            lines.append(f"- `{failure['name']}` [{failure['variant']}] {failure['reason']}")
    else:
        lines.append("All checked cases matched the mock expectations.")
    return "\n".join(lines) + "\n"


def send_with_retry(ip, port, request_hex, retries, retry_delay_ms):
    last_resp = None
    last_error = None
    for attempt in range(retries + 1):
        last_resp, last_error = send_hex(ip, port, request_hex)
        if not last_error:
            return last_resp, None, attempt
        if attempt < retries:
            time.sleep(retry_delay_ms / 1000.0)
    return last_resp, last_error, retries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", default="192.168.250.100")
    parser.add_argument("--port", type=int, default=1025)
    parser.add_argument("--cases-file", default=LIVE_CASES_FILE)
    parser.add_argument("--mode", choices=["baseline", "all-clients"], default="baseline")
    parser.add_argument("--profile", default="")
    parser.add_argument("--include-stateful", action="store_true")
    parser.add_argument("--include-remote", action="store_true")
    parser.add_argument("--case-pattern", default="")
    parser.add_argument("--report-json", default=LATEST_JSON)
    parser.add_argument("--report-md", default=LATEST_MD)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-delay-ms", type=int, default=300)
    parser.add_argument("--step-delay-ms", type=int, default=150)
    args = parser.parse_args()

    cases = load_cases(args.cases_file)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "target": {"ip": args.ip, "port": args.port},
        "profile": args.profile,
        "mode": args.mode,
        "summary": {"run_cases": 0, "passed": 0, "failed": 0, "skipped": 0},
        "results": [],
        "failures": [],
    }

    for case in cases:
        if not should_run_case(case, args.include_stateful, args.include_remote, args.case_pattern):
            report["summary"]["skipped"] += 1
            report["results"].append({"name": case.get("name"), "status": "skipped", "reason": "filter_or_safety"})
            continue

        report["summary"]["run_cases"] += 1
        case_failed = False
        case_result = {"name": case.get("name"), "status": "pass", "variants": []}
        compare_mode, case_responses, case_end_codes, case_lengths, case_note = resolve_expectations(case, args.profile)
        if case_note:
            case_result["note"] = case_note

        for variant, requests, expected_responses, expected_end_codes, expected_lengths in iter_variants(case, args.mode):
            variant_result = {"variant": variant, "status": "pass", "steps": []}
            effective_responses = case_responses if args.mode == "baseline" else expected_responses
            effective_end_codes = case_end_codes if args.mode == "baseline" else expected_end_codes
            effective_lengths = case_lengths if args.mode == "baseline" else expected_lengths

            if len(requests) != len(effective_responses):
                case_failed = True
                variant_result["status"] = "fail"
                variant_result["reason"] = f"request_count={len(requests)} response_count={len(effective_responses)}"
                report["failures"].append({"name": case.get("name"), "variant": variant, "reason": variant_result["reason"]})
                case_result["variants"].append(variant_result)
                continue

            for index, request_hex in enumerate(requests):
                actual_resp, error, attempts = send_with_retry(
                    args.ip,
                    args.port,
                    request_hex,
                    max(0, args.retries),
                    max(0, args.retry_delay_ms),
                )
                step = {"index": index, "request": request_hex}
                if error:
                    case_failed = True
                    variant_result["status"] = "fail"
                    step["status"] = "fail"
                    step["reason"] = error
                    step["attempts"] = attempts + 1
                    report["failures"].append({"name": case.get("name"), "variant": variant, "reason": f"step={index} attempts={attempts + 1} {error}"})
                    variant_result["steps"].append(step)
                    break

                ok, reason = compare_response(
                    actual_resp,
                    effective_responses[index],
                    effective_end_codes[index] if index < len(effective_end_codes) else None,
                    effective_lengths[index] if index < len(effective_lengths) else None,
                    compare_mode,
                )
                step["actual_response"] = normalize_response(actual_resp)
                step["attempts"] = attempts + 1
                step["status"] = "pass" if ok else "fail"
                if not ok:
                    case_failed = True
                    variant_result["status"] = "fail"
                    step["reason"] = reason
                    report["failures"].append({"name": case.get("name"), "variant": variant, "reason": f"step={index} {reason}"})
                    variant_result["steps"].append(step)
                    break
                variant_result["steps"].append(step)
                if args.step_delay_ms > 0:
                    time.sleep(args.step_delay_ms / 1000.0)

            case_result["variants"].append(variant_result)

        if case_failed:
            case_result["status"] = "fail"
            report["summary"]["failed"] += 1
        else:
            report["summary"]["passed"] += 1
        report["results"].append(case_result)

    os.makedirs(os.path.dirname(args.report_json), exist_ok=True)
    with open(args.report_json, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    with open(args.report_md, "w", encoding="utf-8") as handle:
        handle.write(build_markdown(report))

    print(f"[summary] run_cases={report['summary']['run_cases']} passed={report['summary']['passed']} failed={report['summary']['failed']} skipped={report['summary']['skipped']}")
    print(f"[report] json={args.report_json}")
    print(f"[report] md={args.report_md}")
    raise SystemExit(1 if report["summary"]["failed"] else 0)


if __name__ == "__main__":
    main()
