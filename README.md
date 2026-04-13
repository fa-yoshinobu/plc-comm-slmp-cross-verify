# SLMP Cross-Verify

`plc-comm-slmp-cross-verify` is the canonical cross-library verification repo
for the Python, .NET, C++, Node-RED, and Rust SLMP implementations.

This repo has three jobs:

- full parity verification across libraries
- single-client debug runs inside the same harness
- live PLC replay against a validated target profile

The tools are layered:

- `verify.py`
  Canonical mock-baseline parity gate.
- `slmp_live_verify.py`
  Real-PLC replay against the saved mock baseline plus checked-in live profile overrides.
- `device_command_consistency.py`
  Real-PLC consistency runner that compares multiple supported command paths on the same address.
- `validate_specs.py`
  Fast schema check for checked-in JSON specs before CI or live runs.

## Quick Guide

| Goal | Command |
| --- | --- |
| Mock parity across all libraries | `python validate_specs.py && python verify.py` |
| One library only | `python verify.py --clients python --case-pattern "D Word"` |
| Real PLC vs saved baseline/profile | `python slmp_live_verify.py --ip ... --port ... --profile r120pcpu_tcp1025 --include-stateful --include-remote` |
| Real PLC multi-command consistency | `python device_command_consistency.py --host ... --port ... --clients all` |
| Check JSON specs only | `python validate_specs.py` |

## Use This Repo For

- **Library unit tests**
  Keep protocol/helper correctness in each library repo.
- **Cross-library parity**
  Use `verify.py` to compare status parity, packet parity, and selected
  high-level helper results.
- **Automated device walk**
  The parity suite also contains the validated 4E/iQR device-walk pattern used
  for real-PLC smoke checks: bit devices repeat `on -> off -> on -> off`,
  word devices perform two deterministic pseudo-random writes, and the J1
  extended devices are covered with the same automated sequence.
- **Single-client debug**
  Use `verify.py --clients ...` when one wrapper or one library needs isolated
  verification before comparing it with the others.
- **Real PLC validation**
  Use `slmp_live_verify.py` after a mock-baseline run to compare a real PLC
  with the saved mock expectations or a known target profile.

## Main Commands

### Build wrappers

- .NET
  `dotnet build clients/dotnet/SlmpVerifyClient/SlmpVerifyClient.csproj -c Debug`
- C++
  `g++ -I ../plc-comm-slmp-cpp-minimal/src clients/cpp/main.cpp ../plc-comm-slmp-cpp-minimal/src/slmp_minimal.cpp ../plc-comm-slmp-cpp-minimal/src/slmp_high_level.cpp -o clients/cpp/cpp_verify_client.exe -lws2_32`
- Rust
  `cargo build --manifest-path ../plc-comm-slmp-rust/Cargo.toml --bin slmp_verify_client`

### List runnable cases

```bash
python verify.py --list-cases
python verify.py --clients python --case-pattern "LTN" --list-cases
```

### Run the full parity suite

```bash
python validate_specs.py
python verify.py
```

This is the canonical baseline run. It updates:

- `logs/latest_packets.jsonl`
- `logs/latest_markers.jsonl`
- `logs/latest_live_cases.jsonl`
- `logs/prev_results.json`

### Run a single-client debug pass

```bash
python verify.py --clients python --case-pattern "Read Type Name"
python verify.py --clients cpp --case-pattern "LTS/LTC"
python verify.py --clients node-red --case-pattern "Named Read"
```

This is not a separate tool. It runs the same harness with a filtered client
set. By default, filtered runs write timestamped artifacts only and do **not**
replace `latest_*` or `prev_results.json`.

If you intentionally want a filtered run to become the current baseline:

```bash
python verify.py --clients python --case-pattern "Type Name" --write-latest
```

### Replay saved requests against a PLC

```bash
python slmp_interactive_sender.py
```

### Compare a real PLC against saved expectations

Run `verify.py` first so replay cases exist, then:

```bash
python slmp_live_verify.py --ip 192.168.0.10 --port 5000 --include-stateful --include-remote
```

For the validated `R120PCPU` TCP path:

```bash
python slmp_live_verify.py --ip 192.168.250.100 --port 1025 --profile r120pcpu_tcp1025 --include-stateful --include-remote
```

### Run the live device consistency sweep

```bash
python device_command_consistency.py --host 192.168.250.100 --port 1025 --clients all
```

Useful flags:

- `--devices D10,J1\W10`
  Limit the run to a device subset.
- `--summary-only`
  Suppress per-device PASS lines and print only failures plus the final summary.
- `--fail-fast`
  Stop after the first failure while still writing reports.
- `--no-restore-after`
  Skip restore writes when you only want to observe the forward path.

### Guarded paths

These paths are intentionally treated as unsupported and are checked by the
negative parity cases in `specs/shared/unsupported_path_vectors.json`.

- `LTS/LTC/LSTS/LSTC`
  Direct bit read is blocked. Use named helpers or the 4-word base block decode.
- `LTN/LSTN`
  Direct read is blocked. Use named dword helpers or the 4-word base block decode.
- `LCS/LCC`
  `0403 Read Random`, `0406 Read Block`, `1406 Write Block`, and `0801 Entry Monitor Device` are blocked.

## Repository Layout

- `verify.py`
  Main harness for full parity runs and single-client debug runs.
- `slmp_live_verify.py`
  Automated real-PLC replay verifier.
- `device_command_consistency.py`
  Multi-command live PLC consistency runner with restore/report support.
- `validate_specs.py`
  Schema validator for checked-in device-consistency and unsupported-path specs.
- `slmp_interactive_sender.py`
  Manual replay/debug tool for saved request packets.
- `server/mock_server.py`
  Mock SLMP 3E/4E binary server used by `verify.py`.
- `clients/`
  Wrapper CLIs for Python, .NET, C++, Node-RED, and Rust.
- `specs/shared/`
  Canonical shared JSON vectors for device encoding, address parsing, address
  normalization, and golden request frames.
- `specs/expected_responses/`
  Checked-in live-profile response overrides keyed by verification case name.
- `specs/device_consistency/`
  Checked-in live device matrix profiles and supported-path policy.

## Artifact Rules

- A full unfiltered `python verify.py` run refreshes the `latest_*` artifacts.
- A filtered run writes timestamped `logs/packets_*.jsonl`,
  `logs/markers_*.jsonl`, and `logs/live_cases_*.jsonl`.
- `slmp_live_verify.py` should normally consume the canonical `latest_*`
  artifacts from a full baseline run.

## CI

- `.github/workflows/ci.yml`
  Required mock-baseline parity gate.
- `.github/workflows/live-r120-profile.yml`
  Manual real-PLC job for the validated `r120pcpu_tcp1025` profile on a
  reachable self-hosted runner.
- `.github/workflows/live-device-consistency.yml`
  Manual real-PLC multi-command consistency job with job-summary output.

## Document Map

- `README.md`
  Entry point and workflow guide.
- `SPEC_AND_LOG_FORMAT.md`
  Packet/log/live-case file shapes and mock-fixture scope.
- `specs/expected_responses/README.md`
  Checked-in target-profile response expectations.
- `CHANGELOG.md`
  Repository release history.
