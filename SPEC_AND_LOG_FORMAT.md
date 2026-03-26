# SLMP Verification Specs and Log Files

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

The mock server deliberately returns common SLMP errors for a few stable cases:

- `0xC050` for out-of-range addresses such as `999999`
- `0xC056` for oversized reads above the suite limit
- `0xC051` for intentionally invalid device-code payloads

These cases are used to confirm consistent error propagation across languages.

## Generated Log Files

`verify.py` writes several files under `logs/`:

- `packet_log_YYYYMMDD_HHMMSS.log`
  Human-readable console transcript for one run.
- `latest_packets.jsonl`
  Raw REQ/RES packets streamed from the mock server.
- `latest_markers.jsonl`
  One marker per test case, written by `verify.py`.
- `prev_results.json`
  Previous normalized test outcomes used for regression comparison.
- `response_history.json`
  Interactive replay history used by `slmp_interactive_sender.py`.

## JSONL Entry Shapes

### `latest_packets.jsonl`

Each line is one mock-server packet event:

```json
{
  "session_id": 1,
  "direction": "REQ",
  "routing": "NW:0,ST:255,MIO:03FF,MD:0",
  "data": "500000ffff03000c0010000104010000640000a80300"
}
```

### `latest_markers.jsonl`

Each line is one per-test result marker:

```json
{
  "type": "TEST_RESULT",
  "name": "3E QL D Word Read 3pts",
  "result": "pass",
  "desc": "read D100 3pts",
  "n_clients": 3
}
```

`slmp_interactive_sender.py` uses `n_clients` to group the corresponding REQ
packets from `latest_packets.jsonl`.

## Interactive Replay Tool

`slmp_interactive_sender.py`:

- loads `latest_packets.jsonl` and `latest_markers.jsonl`
- groups request packets by test case
- sends one selected request or a full batch to a real PLC
- normalizes 4E response serial bytes before history comparison
- reports response changes against `response_history.json`
