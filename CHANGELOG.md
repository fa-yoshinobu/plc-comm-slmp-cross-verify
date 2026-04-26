# Changelog

## [Unreleased]

### Changed
- Aligned cross-library verification specs with the current long-device route policy: `LCN` and `LZ` now use random/named dword paths, `LCS/LCC` use direct/named reads plus random/named writes, and invalid direct/raw/block routes are covered by shared negative vectors.
- Updated the live device consistency runner to honor profile `read_paths`, `write_paths`, and `restore_path` for dword devices instead of always exercising direct/raw word routes.
- Added parity coverage for label array/random read/write commands across Python, .NET, C++, Node-RED, and Rust.
- Expanded the Node-RED wrapper scope to remote control, memory, extend-unit, and label commands now supported by its packaged helper layer.

## [0.1.6] - 2026-04-13

### Added
- `device_command_consistency.py`: added a live multi-command consistency runner that exercises the same PLC address through multiple supported command paths across Python, .NET, C++, Node-RED, and Rust.
- `specs/device_consistency/r120pcpu_tcp1025.json`: added a checked-in live device-matrix profile covering supported paths, volatile devices, and restore policy for the validated R120 target.
- `specs/shared/unsupported_path_vectors.json`: added a checked-in shared spec for intentionally blocked paths such as long timer direct reads and `LCS/LCC` random/block/monitor commands.
- `.github/workflows/live-device-consistency.yml`: added a manual self-hosted GitHub Actions workflow for the live device consistency sweep.
- `validate_specs.py`: added a fast schema validator for device-consistency profiles, unsupported-path vectors, and live profile overrides.

### Changed
- `verify.py` now loads unsupported-path negative cases from checked-in JSON instead of hardcoding them in the test list.
- The Node verification wrapper now supports `read-ext` and `write-ext` for link-direct and buffer-memory extended devices and uses the correct `Jn\\...` subcommands.
- The .NET verification wrapper now handles larger dword values safely during consistency and random-write runs.
- The live consistency runner now writes richer reports with client summary, kind summary, restore summary, failure classes, and the effective device matrix.
- The manual live workflows now publish their markdown reports to the GitHub Actions job summary.
- `README.md` now documents the tool layering, quick-start commands, guarded paths, and the intended use of the mock parity suite versus live replay versus device consistency.

### Validated
- Mock parity suite now passes at `228/228`.
- Live PLC profile verification against `192.168.250.100:1025` now passes at `228/228`.
- Live device consistency across all five client implementations now passes at `225/225`.

## [0.1.5] - 2026-04-13

### Changed
- Simplified the documentation set so `README.md` is the entry point, `SPEC_AND_LOG_FORMAT.md` keeps artifact details, and duplicate status/TODO documents were removed.
- `verify.py` now supports filtered single-client debug runs through `--clients`, `--case-pattern`, and `--list-cases` without requiring a separate verification tool.
- Filtered `verify.py` runs now write timestamped packet/marker/live-case artifacts by default so they do not overwrite the canonical `latest_*` baseline.
- Live PLC response overrides moved out of `verify.py` into `specs/expected_responses/live_profiles.json`.

## [0.1.4] - 2026-04-01

### Changed
- Updated the .NET verification wrapper to open connections through `SlmpConnectionOptions`, `SlmpClientFactory`, and the queued client path.
- Added Node-RED-backed wrapper coverage for the scope-aligned commands that overlap with the current package surface.
- Added high-level named helper parity cases and linked the optional C++ high-level facade into the automated suite.

### Added
- `clients/node/client_wrapper.js`: added a Node-RED-backed wrapper around `node-red-contrib-plc-comm-slmp` so the parity suite can compare the Node implementation too.

### Changed
- Cleaned up repository documentation so README, spec notes, and verification report match the current 140-test harness layout and JSONL-based log files.
- Clarified that active follow-up items belong in the language-library repositories unless the issue is specific to the parity harness itself.
- Removed Step Relay `S` from the C++ wrapper parser to match the current SLMP library scope. `TS/LTS/STS/LSTS/CS/LCS` remain supported.
- `run.bat` now builds both the .NET and C++ wrapper before `Build + Verify` and `Build only`.
- `verify.py` now resolves repository root dynamically and reports a clear `missing executable` error when the C++ client has not been built.
- Added parity coverage for the high-level named snapshot helpers: `write-named`, `read-named`, and `poll-once`.
- The C++ wrapper build now links `slmp_high_level.cpp` so the optional high-level facade is part of the automated suite.
- `verify.py` now adds the Node-RED wrapper to scope-aligned commands (`read`, `write`, `read-type`, `random-read`, `random-write-words`, `read-named`, `write-named`, `poll-once`) and keeps it out of commands that are outside the current Node-RED package scope.

## 2026-03-24

### Added
- `verify.py`: expanded the test suite from 83 to 140 cases; added timer (TN/TS/TC, LTN/LTS/LTC, STN/STS/STC, LSTN/LSTS/LSTC), counter (CN/CS/CC, LCN/LCS/LCC), bit devices (L/F/V/DX/DY), index registers (Z/LZ), buffer memory bit (`U3\G100` bit), Memory 4E, ExtUnit 4E, and `U1\HG0` word tests. Result: **140/140 ALL PASS** across Python / .NET / C++.
- `slmp_interactive_sender.py`: added response history comparison across sessions (`logs/response_history.json`), per-send first/unchanged/changed markers, and an NG-only summary after batch replay.
- `clients/cpp/main.cpp`: extended `parseDevice()` to cover all SLMP device families and added bit-mode support for `read-ext` / `write-ext` via `readBitsModuleBuf` / `writeBitsModuleBuf`.
- `clients/dotnet/SlmpVerifyClient/Program.cs`: added test coverage for timers, counters, and extended devices.
- `clients/python/client_wrapper.py`: added test coverage to match the expanded suite.
- `server/mock_server.py`: extended mock state to support timer/counter device families and buffer-memory bit access.
- `run.bat`: added batch runner support for all three clients.

### Fixed
- `VERIFICATION_REPORT.md`: marked all previously reported gaps as resolved (C++ ExtendedDevice, C++ F device, Python extended-address DM handling).

## 2026-03-22 (initial)

### Added
- Initial cross-language verification tool with Python, .NET, and C++ clients.
- `verify.py`: 83 test cases covering 3E/4E frames, basic devices, extended address (`J`/`U`), routing, remote RUN/STOP, and error responses.
- `server/mock_server.py`: SLMP 3E/4E binary mock server with ExtendedDevice and error injection support.
- `slmp_interactive_sender.py`: interactive PLC re-sender from verification logs.
- `VERIFICATION_REPORT.md`: initial gap analysis report.
- `SPEC_AND_LOG_FORMAT.md`: log format specification.
