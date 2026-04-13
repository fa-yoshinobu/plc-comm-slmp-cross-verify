## Device Consistency Profiles

These profiles drive `device_command_consistency.py`.

Each profile defines:

- transport defaults such as `frame`, `series`, retry counts, and settle delays
- the device matrix to exercise on a live PLC
- which command paths are considered supported for each device kind
- whether a device is volatile and should use best-effort restore verification

Profile files are checked in so the live runner logic stays data-driven rather than
embedding PLC-specific policy in Python code.

Current profiles:

- `r120pcpu_tcp1025.json`
