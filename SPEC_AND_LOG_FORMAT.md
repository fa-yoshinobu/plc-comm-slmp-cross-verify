# SLMP Verification Specs And Log Formats

## Purpose

This file describes the machine-readable artifacts produced and consumed by the
cross-verify harness.

Use `README.md` for workflow and command examples. Use this file only for
artifact semantics.

Checked-in target-specific response overrides live under
`specs/expected_responses/`.

## Mock Server Scope

`server/mock_server.py` is the protocol fixture used by `verify.py`.

It supports the command families exercised by the current suite:

- device read/write
- random read/write
- block read/write
- read type name
- remote control
- self-test
- memory read/write
- extension-unit read/write
- Extended Specification read/write for qualified devices

Supported subcommands include:

- `0x0000 / 0x0001`: Q/L word / bit
- `0x0002 / 0x0003`: iQ-R word / bit
- `0x0080 / 0x0081`: Q/L Extended Specification word / bit
- `0x0082 / 0x0083`: iQ-R Extended Specification word / bit

## Intentional Error Injection

The mock server deliberately returns stable SLMP errors for selected negative
cases:

- `0xC050` for out-of-range addresses such as `999999`
- `0xC056` for oversized reads above the suite limit
- `0xC051` for intentionally invalid device-code payloads

## Artifact Policy

### Full baseline run

`python verify.py` refreshes the canonical artifacts:

- `logs/latest_packets.jsonl`
- `logs/latest_markers.jsonl`
- `logs/latest_live_cases.jsonl`
- `logs/prev_results.json`

### Filtered or single-client run

`python verify.py --clients ...` or `python verify.py --case-pattern ...`
writes timestamped artifacts instead:

- `logs/packets_<timestamp>.jsonl`
- `logs/markers_<timestamp>.jsonl`
- `logs/live_cases_<timestamp>.jsonl`

This avoids overwriting the canonical baseline unless `--write-latest` is used.

## Checked-In Expected Responses

`specs/expected_responses/live_profiles.json` contains target-specific live
comparison overrides keyed by verification case name.

Typical contents:

- `comparison_mode`
- `responses`
- `end_codes`
- `lengths`
- `note`

`verify.py` merges those checked-in expectations into the generated live-case
records written under `logs/`.

## Generated Files

`verify.py` writes:

- `packet_log_YYYYMMDD_HHMMSS.log`
  Human-readable console transcript for one run.
- `latest_packets.jsonl` or `packets_<timestamp>.jsonl`
  Raw mock-server REQ/RES packet stream for that run.
- `latest_markers.jsonl` or `markers_<timestamp>.jsonl`
  One executed test marker per run.
- `prev_results.json`
  Previous canonical full-suite results used for regression comparison.
- `latest_live_cases.jsonl` or `live_cases_<timestamp>.jsonl`
  Replayable case records for `slmp_live_verify.py`.

`slmp_interactive_sender.py` writes:

- `response_history.json`
  Normalized replay-history store for manual PLC sends.

`slmp_live_verify.py` writes:

- `latest_live_verify.json`
- `latest_live_verify.md`

## JSONL Entry Shapes

### Packet stream

Each line of `latest_packets.jsonl` or `packets_<timestamp>.jsonl` is one
mock-server packet event:

```json
{
  "session_id": 1,
  "direction": "REQ",
  "routing": "NW:0,ST:255,MIO:03FF,MD:0",
  "data": "500000ffff03000c0010000104010000640000a80300"
}
```

### Test markers

Each line of `latest_markers.jsonl` or `markers_<timestamp>.jsonl` is one
executed test marker:

```json
{
  "type": "TEST_RESULT",
  "name": "3E QL D Word Read 3pts",
  "result": "OK",
  "desc": "D100 -> 3pts Word  [3E/QL 4client]",
  "n_clients": 4
}
```

Notes:

- `result` is `OK`, `OK(NG)`, or `NG`
- filtered-out or out-of-scope cases are not emitted
- `n_clients` is used by `slmp_interactive_sender.py` to regroup saved REQ
  packets

### Live cases

Each line of `latest_live_cases.jsonl` or `live_cases_<timestamp>.jsonl`
contains one replayable verification case:

```json
{
  "type": "LIVE_CASE",
  "name": "3E QL D Word Read 3pts",
  "comparison_mode": "exact",
  "replay_class": "stateful",
  "baseline_client": "python",
  "baseline_requests": ["5000..."],
  "baseline_responses": ["d000..."],
  "live_profiles": {
    "r120pcpu_tcp1025": {
      "comparison_mode": "end_code",
      "end_codes": [49244],
      "note": "target-specific override"
    }
  },
  "clients": {
    "python": {
      "requests": ["5000..."],
      "responses": ["d000..."]
    }
  }
}
```

`slmp_live_verify.py` replays these requests against a real PLC and compares
the normalized responses with either:

- the baseline mock expectations
- a profile override such as `r120pcpu_tcp1025`

## Replay Tools

### `slmp_interactive_sender.py`

- loads replayable requests from `latest_live_cases.jsonl` first
- falls back to packet + marker pairing if needed
- normalizes 4E serial bytes before response-history comparison

### `slmp_live_verify.py`

- loads `latest_live_cases.jsonl` by default
- replays either the baseline request sequence or all client traces
- compares live responses using `exact`, `shape`, or `end_code`
