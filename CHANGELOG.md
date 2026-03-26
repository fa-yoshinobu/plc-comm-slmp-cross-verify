# Changelog

## [Unreleased]

### Changed
- Cleaned up repository documentation so README, spec notes, and verification report match the current 140-test harness layout and JSONL-based log files.
- Clarified that active follow-up items belong in the language-library repositories unless the issue is specific to the parity harness itself.
- Removed Step Relay `S` from the C++ wrapper parser to match the current SLMP library scope. `TS/LTS/STS/LSTS/CS/LCS` remain supported.

## 2026-03-24

### Added
- `verify.py`: expand test suite from 83 to 140 cases; add timer (TN/TS/TC, LTN/LTS/LTC, STN/STS/STC, LSTN/LSTS/LSTC), counter (CN/CS/CC, LCN/LCS/LCC), bit devices (L/F/V/DX/DY), index registers (Z/LZ), buffer memory bit (`U3\G100` bit), Memory 4E, ExtUnit 4E, `U1\HG0` word tests 窶・**140/140 ALL PASS** across Python / .NET / C++
- `slmp_interactive_sender.py`: response history comparison across sessions (`logs/response_history.json`); marks each send as `[蛻晏屓]` / `蠢懃ｭ・蜷後§` / `蠢懃ｭ・螟牙喧`; prints NG-only summary after batch
- `clients/cpp/main.cpp`: extend `parseDevice()` to cover all SLMP device families; add bit-mode path for read-ext/write-ext using `readBitsModuleBuf` / `writeBitsModuleBuf`
- `clients/dotnet/SlmpVerifyClient/Program.cs`: add test cases for timers, counters, extended devices
- `clients/python/client_wrapper.py`: add test cases to match new suite
- `server/mock_server.py`: extend mock state to support timer/counter device families and buffer memory bit access
- `run.bat`: add batch runner for all three clients in sequence

### Fixed
- `VERIFICATION_REPORT.md`: mark all previously reported gaps as resolved (C++ ExtendedDevice, C++ F device, Python extended address DM handling)

## 2026-03-22 (initial)

### Added
- Initial cross-language verification tool with Python, .NET, C++ clients
- `verify.py`: 83 test cases covering 3E/4E frames, basic devices, extended address (J/U), routing, remote RUN/STOP, error responses
- `server/mock_server.py`: SLMP 3E/4E binary mock server with ExtendedDevice and error injection support
- `slmp_interactive_sender.py`: interactive PLC re-sender from verification logs
- `VERIFICATION_REPORT.md`: initial gap analysis report
- `SPEC_AND_LOG_FORMAT.md`: log format specification

