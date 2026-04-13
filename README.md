# SLMP Cross-Verify

`plc-comm-slmp-cross-verify` is the canonical cross-library verification repo
for the Python, .NET, C++, Node-RED, and Rust SLMP implementations.

This repo has three jobs:

- full parity verification across libraries
- single-client debug runs inside the same harness
- live PLC replay against a validated target profile

## Use This Repo For

- **Library unit tests**
  Keep protocol/helper correctness in each library repo.
- **Cross-library parity**
  Use `verify.py` to compare status parity, packet parity, and selected
  high-level helper results.
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

## Repository Layout

- `verify.py`
  Main harness for full parity runs and single-client debug runs.
- `slmp_live_verify.py`
  Automated real-PLC replay verifier.
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

## Document Map

- `README.md`
  Entry point and workflow guide.
- `SPEC_AND_LOG_FORMAT.md`
  Packet/log/live-case file shapes and mock-fixture scope.
- `specs/expected_responses/README.md`
  Checked-in target-profile response expectations.
- `CHANGELOG.md`
  Repository release history.
