# SLMP Conformance Platform

This repository rebuilds the SLMP cross-language verifier as a spec-driven
conformance engine.

Instead of keeping the test matrix only inside Python code, this project makes
the scenario suite machine-readable and adds a stateful mock PLC with
fault-injection support.

## Design Goals

- Keep conformance cases as JSON specs, not hard-coded orchestration logic.
- Compare more than request packets:
  - logical results
  - expected success/error surface
  - packet parity when a case requires it
  - final mock-PLC snapshot for stateful scenarios
  - capability coverage per implementation
- Run multi-step stateful scenarios against a shared mock PLC state.
- Inject PLC end codes and transport faults from the scenario definition.
- Distinguish `pass`, `fail`, `not_implemented`, and `unavailable` in the
  generated matrix.

## Repository Layout

- `specs/implementations.json`
  Implementation adapter registry and capability declarations.
- `specs/suites/*.json`
  Conformance scenarios.
- `server/stateful_mock_plc.py`
  Stateful SLMP 3E/4E mock server with per-case fault injection.
- `conformance/adapters.py`
  Adapter preparation and subprocess execution.
- `conformance/engine.py`
  Scenario runner, packet/result comparison, and report generation.
- `run_conformance.py`
  CLI entry point.

## Current Scope

The seed suite covers:

- golden 4E/iQ-R frame checks
- stateful write/read parity
- random write/read parity
- block write/read parity
- high-level named read/write/poll parity
- memory and extend-unit parity
- oversized-read PLC boundary errors
- PLC end-code fault injection
- final PLC-state assertions for stateful cases

The runner reuses the existing SLMP wrapper clients from
`../plc-comm-slmp-cross-verify/clients` for now. That keeps the rebuild focused
on the conformance engine, not wrapper duplication.

## Usage

```bash
python run_conformance.py
```

Useful options:

```bash
python run_conformance.py --implementations python,node
python run_conformance.py --suite specs/suites/core_sequences.json
python run_conformance.py --keep-artifacts
```

Reports are written to `reports/latest_conformance.json` and
`reports/latest_conformance.md`.
