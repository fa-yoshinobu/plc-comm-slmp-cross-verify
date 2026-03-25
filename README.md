# SLMP Cross-Language Verification Tool

This tool is an environment for strictly verifying the compatibility and specification compliance of SLMP client implementations among three languages: Python, .NET, and C++.

## Structure
- `server/mock_server.py`: Mock server compliant with SLMP 3E/4E Binary specifications. Supports intentional error responses (NG patterns) and Extended Device (Extended Addresses).
- `clients/`: Wrapper programs using the libraries of each language.
- `verify.py`: Orchestrator that automatically runs all test cases and detects mismatches in "execution results" and "sent packets" between languages.
- `slmp_interactive_sender.py`: Debug tool to select specific packets from verification logs and retransmit them to an actual PLC in an interactive format.
- `logs/`: Directory where verification logs with timestamps (JSON format) are saved.

## Features Verified (140 Test Cases / 2026-03-24 ALL PASS)

- **Frame**: 3E Binary / 4E Binary
- **Devices (Basic)**: D, W, R, ZR, M, X, Y, B, SM, SD, SB, SW, L, F, V, DX, DY, Z, LZ
- **Devices (Timer)**: TN/TS/TC, LTN/LTS/LTC, STN/STS/STC, LSTN/LSTS/LSTC
- **Devices (Counter)**: CN/CS/CC, LCN/LCS/LCC
- **Extended Address**: Link Direct (`J笆｡\SW`), Buffer Memory word (`U笆｡\G`, `U笆｡\HG`), Buffer Memory bit (`U笆｡\G` bit)
- **Routing**: Other Station (Network, Station, Module I/O)
- **Memory / ExtUnit**: Memory word read/write (4E), ExtUnit word read/write (4E)
- **Special Operations**: Remote RUN / STOP
- **Error Scenarios**: Error response processing for address out of range, non-existent device, data length too large, etc.

## Execution Procedure

### 1. Client Build
- **.NET**: `dotnet build clients/dotnet/SlmpVerifyClient/SlmpVerifyClient.csproj`
- **C++**: `g++ -I ../plc-comm-slmp-cpp-minimal/src clients/cpp/main.cpp ../plc-comm-slmp-cpp-minimal/src/slmp_minimal.cpp -o clients/cpp/cpp_verify_client.exe -lws2_32`

### 2. Run Verification
```bash
python verify.py
```
After execution, `logs/packet_log_YYYYMMDD_HHMMSS.json` will be generated.

### 3. Retransmission Verification to Actual PLC
```bash
python slmp_interactive_sender.py
```
Select a test case from the menu and send the generated message to the actual PLC to check its behavior.

Response history comparison function: Accumulates response differences from previous sessions in `logs/response_history.json`, and notifies as `Response: Changed` when the response to the same command changes. Displays a summary of only NG items after batch execution.

