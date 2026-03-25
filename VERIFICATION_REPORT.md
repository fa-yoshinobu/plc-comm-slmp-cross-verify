# SLMP Cross-Language Verification Report (2026-03-24)

Using this verification tool, the following bugs and implementation differences were identified as a result of verifying the parity (equivalence) between the Python, .NET, and C++ libraries.

## 1. Critical Bugs

### ~~Python Version: Incorrect Bit Packing Order~~ 竊・**2026-03-24 Determined as False Alarm**
- **Actual Device Verification Result**: Write M0=ON, M1=OFF 竊・Response `0x10` (upper nibble=1, lower nibble=0) 竊・Normal operation
- **Correct Specification**: Upper 4 bits = Point N, lower 4 bits = Point N+1 (Both Python and .NET implementations match)
- **Conclusion**: No bug. The specification description in the report was incorrect.

### ~~.NET Version: Insufficient Validation in RemoteRunAsync~~ 竊・**2026-03-24 Determined as False Alarm**
- **Verification Result**: The current implementation does not perform any device parsing and assembles the payload directly. Confirmed normal operation on both mock server and actual device.
- **Conclusion**: No bug. Presumed false detection due to verification with an old binary.

## 2. Implementation Gaps 窶・All resolved as of 2026-03-24

### ~~C++ Version: Extended Device Not Supported~~ 竊・**2026-03-24 Resolved**
- Implemented `readBitsModuleBuf` / `writeBitsModuleBuf` in `slmp_minimal`.
- Expanded `parseDevice()` in `clients/cpp/main.cpp` to support all device families.
- C++ now passes cross-verify tests including `U3\G100 bit`.

### ~~C++ Version: F Device Code Undefined~~ 竊・**2026-03-24 Resolved**
- Added `F` (Annunciator, 0x0093) to `slmp_minimal`.
- Updated `parseDevice()` in `clients/cpp/main.cpp` to recognize F.

### ~~Python Version: Incorrect Subcommand Switching for Extended Addresses~~ 竊・**2026-03-24 Resolved**
- Fixed DM auto-configuration logic in `resolve_ExtendedDevice_device_and_extension`.
- `U笆｡\G` 竊・DM=0xF8, `U笆｡\HG` 竊・DM=0xFA are now set correctly, matching .NET.
- Fix details: Do not overwrite if explicit `direct_memory_specification` is provided (auto-configure only when `DIRECT_MEMORY_NORMAL=0x00`).

## 3. Verified Features (2026-03-24 140/140 ALL PASS)

| Category | Content |
|---|---|
| Frame | 3E / 4E Binary Header Generation |
| Basic Devices | D, W, R, ZR, M, X, Y, B, SM, SD, SB, SW, L, F, V, DX, DY, Z, LZ |
| Timers | TN/TS/TC, LTN/LTS/LTC, STN/STS/STC, LSTN/LSTS/LSTC (12 types) |
| Counters | CN/CS/CC, LCN/LCS/LCC (6 types) |
| Extended Addresses | J笆｡\SW (link direct), U笆｡\G word, U笆｡\HG word, U笆｡\G bit |
| Memory / ExtUnit | Memory word (4E), ExtUnit word (4E) |
| Routing | Network No, Station No, Module I/O Multidrop |
| Error Scenarios | PLC error codes such as 0xC056 properly propagated in all languages |


