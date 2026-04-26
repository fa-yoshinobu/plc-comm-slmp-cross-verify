## Device Consistency Profiles

These profiles drive `device_command_consistency.py`.

Each profile defines:

- transport defaults such as `frame`, `series`, retry counts, and settle delays
- the device matrix to exercise on a live PLC
- which command paths are considered supported for each device kind
- whether a device is volatile and should use best-effort restore verification

Profile files are checked in so the live runner logic stays data-driven rather than
embedding PLC-specific policy in Python code.

Long-device route policy is intentionally explicit in each profile:

- `LTS/LTC/LSTS/LSTC` are decoded through named helpers and the 4-word
  `LTN/LSTN` status block.
- `LCS/LCC` are bit devices: direct bit read is valid, while writes use
  random/named bit helpers.
- `LTN/LSTN`, `LCN`, and `LZ` current/index values are verified through
  random/named dword helpers. Direct/raw word routes are not part of the
  positive consistency matrix.

Current profiles:

- `r120pcpu_tcp1025.json`
