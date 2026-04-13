# SLMP Cross-Language Verification Tool

This repository verifies semantic parity and request-packet parity across the
Python, .NET, C++, and Node-RED SLMP libraries.

## Scope

The current suite in `verify.py` contains 140 tests. It covers:

- binary 3E and 4E frames
- Q/L and iQ-R word and bit access
- timers, counters, long timers/counters, and index registers
- dword and float helpers
- routing / other-station access
- random access
- block access
- remote control operations
- self-test and type-name reads
- high-level named snapshot read/write/poll helpers
- memory read/write and extension-unit read/write
- Extended Specification qualified devices such as `Jx\SWy`, `Ux\G`, and `Ux\HG`
- negative tests for oversized reads

## Repository Layout

- `server/mock_server.py`
  Mock SLMP 3E/4E binary server used by the automated suite.
- `clients/`
  Python, .NET, C++, and Node-RED wrapper programs that expose comparable CLI commands.
  The .NET wrapper opens the session through `SlmpConnectionOptions` plus `SlmpClientFactory` before running the shared command set.
- `verify.py`
  Test orchestrator. Compares status parity and request-packet parity across
  clients. The Node-RED wrapper participates in the commands that are within
  the current `node-red-contrib-plc-comm-slmp` scope.
- `slmp_interactive_sender.py`
  Interactive replay tool for sending captured request packets to a real PLC.
- `slmp_live_verify.py`
  Automated live replay checker that compares real-PLC responses against the
  latest mock-run expectations.
- `logs/`
  Packet logs, test markers, console logs, and replay history.

## Typical Workflow

### 1. Build client wrappers

- .NET:
  `dotnet build clients/dotnet/SlmpVerifyClient/SlmpVerifyClient.csproj -c Debug`
- C++:
  `g++ -I ../plc-comm-slmp-cpp-minimal/src clients/cpp/main.cpp ../plc-comm-slmp-cpp-minimal/src/slmp_minimal.cpp ../plc-comm-slmp-cpp-minimal/src/slmp_high_level.cpp -o clients/cpp/cpp_verify_client.exe -lws2_32`

`run.bat` option `1` and option `3` build both wrappers. If the C++ executable is
missing, `verify.py` now reports `missing executable: ...` instead of a raw
Windows launcher error.

### 2. Run the parity suite

```bash
python verify.py
```

The run produces:

- `logs/packet_log_YYYYMMDD_HHMMSS.log`
- `logs/latest_packets.jsonl`
- `logs/latest_markers.jsonl`
- `logs/prev_results.json`

### 3. Replay packets against a real PLC

```bash
python slmp_interactive_sender.py
```

The replay tool loads `latest_packets.jsonl` and `latest_markers.jsonl`,
groups packets by test case, and can resend one test or a full batch to a PLC.

### 4. Verify live PLC responses against mock expectations

Run `verify.py` first so `logs/latest_live_cases.jsonl` exists, then:

```bash
python slmp_live_verify.py --ip 192.168.0.10 --port 5000 --include-stateful --include-remote
```

For the validated `R120PCPU` TCP target that has known target-dependent
responses for `SW`, `J1\\SW`, mixed `1406`, `1005`, and oversize NG codes:

```bash
python slmp_live_verify.py --ip 192.168.250.100 --port 1025 --profile r120pcpu_tcp1025 --include-stateful --include-remote
```

Useful options:

- `--mode baseline`
  Replay one canonical request sequence per test case.
- `--mode all-clients`
  Replay each client trace separately.
- `--profile r120pcpu_tcp1025`
  Apply the live-profile overrides encoded in `verify.py` for the validated
  `R120PCPU` TCP path while keeping mock parity expectations unchanged.
- `--include-stateful`
  Include write/read sequences that depend on suite ordering.
- `--include-remote`
  Include remote RUN/STOP/PAUSE/LATCH-CLEAR/RESET commands.
- `--case-pattern "Type Name"`
  Run only matching cases.

The live verifier writes:

- `logs/latest_live_verify.json`
- `logs/latest_live_verify.md`

## CI

- `.github/workflows/ci.yml`
  Runs the mock baseline suite on `push`, `pull_request`, and manual dispatch.
  This is the gate that should remain required for merges.
- `.github/workflows/live-r120-profile.yml`
  Manual `workflow_dispatch` job for the validated `R120PCPU` TCP path.
  This job is intended for a `self-hosted` Linux runner that can reach the PLC
  network and runs `slmp_live_verify.py --profile r120pcpu_tcp1025`.

## Response History

`slmp_interactive_sender.py` stores normalized responses in
`logs/response_history.json`.

- If a response matches the previous run, it is reported as unchanged.
- If a response differs, it is reported as `Response: Changed`.
- The sender prints an NG-only summary after batch replay so that dynamic or
  unexpected PLC responses are easy to review.
