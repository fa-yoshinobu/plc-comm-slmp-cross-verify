# SLMP Cross-Language Verification Tool

This repository verifies semantic parity and request-packet parity across the
Python, .NET, and C++ SLMP libraries.

## Scope

The current suite in `verify.py` contains 143 tests. It covers:

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
  Python, .NET, and C++ wrapper programs that expose comparable CLI commands.
- `verify.py`
  Test orchestrator. Compares status parity and request-packet parity across
  clients.
- `slmp_interactive_sender.py`
  Interactive replay tool for sending captured request packets to a real PLC.
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

## Response History

`slmp_interactive_sender.py` stores normalized responses in
`logs/response_history.json`.

- If a response matches the previous run, it is reported as unchanged.
- If a response differs, it is reported as `Response: Changed`.
- The sender prints an NG-only summary after batch replay so that dynamic or
  unexpected PLC responses are easy to review.
