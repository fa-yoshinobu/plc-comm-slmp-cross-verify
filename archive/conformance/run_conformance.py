from __future__ import annotations

import argparse
from pathlib import Path

from conformance.adapters import load_adapter_specs
from conformance.engine import ROOT, run_suite


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="specs/suites/core_sequences.json")
    parser.add_argument("--implementations", default="python,dotnet,cpp,node")
    parser.add_argument("--keep-artifacts", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suite_path = ROOT / args.suite
    adapters = load_adapter_specs(ROOT / "specs" / "implementations.json")
    selected = [item.strip() for item in args.implementations.split(",") if item.strip()]
    report = run_suite(suite_path, adapters, selected, keep_artifacts=args.keep_artifacts)
    failed = report["summary"]["failed_steps"]
    print(f"[suite] {report['suite']}")
    print(f"[summary] cases={report['summary']['cases']} steps={report['summary']['steps']} passed={report['summary']['passed_steps']} failed={failed}")
    print(f"[report] json={ROOT / 'reports' / 'latest_conformance.json'}")
    print(f"[report] md={ROOT / 'reports' / 'latest_conformance.md'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
