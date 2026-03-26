# SLMP Cross-Language Verification Report

This file records the current repository-level status of the SLMP parity
harness.

## Current Harness Role

`plc-comm-slmp-cross-verify` exists to detect:

- status mismatches between Python, .NET, and C++
- request-packet mismatches for equivalent public operations
- regressions in wrapper behavior after library changes

The current suite in `verify.py` contains 140 tests.

## Covered Areas

- 3E and 4E binary frames
- basic word and bit devices
- timers, counters, long timers/counters, and index registers
- dword and float helpers
- routing / other-station access
- random access
- block access
- self-test and type-name
- remote control
- memory read/write
- extension-unit read/write
- Extended Specification qualified devices such as `Jx\SWy`, `Ux\G`, and `Ux\HG`
- representative negative tests for oversized reads

## Historical Gaps Closed by the 2026-03-24 Suite Expansion

The last major suite expansion in this repository closed the following
cross-language gaps:

- C++ Extended Specification device coverage was expanded, including bit access
- C++ device parsing was extended to include `F`
- Python qualified `G/HG` handling was aligned with the verified direct-memory
  override behavior

Those items are now treated as resolved historical issues, not active harness
bugs.

## Current Interpretation

- No active harness-specific mismatch is documented in this repository.
- Environment-dependent SLMP behavior still belongs in the language-library
  repositories and their validation reports.
- Update this file only with evidence from an actual `verify.py` run or a
  reproducible live replay result.
