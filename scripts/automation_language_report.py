"""HomeIntent 7.2.0 automation language quality report.

Runs a corpus (``dev`` or ``heldout``) through the real conversation path with
an instrumented service sink and prints the metrics of spec §92.

    python scripts/automation_language_report.py --corpus heldout
    python scripts/automation_language_report.py --corpus dev --failures
    python scripts/automation_language_report.py --corpus heldout --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))
sys.path.insert(0, str(ROOT / "tests"))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _automation_eval import CORPUS_DIR, failures, load_cases, run_cases, summarize  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", choices=("dev", "heldout"), required=True)
    parser.add_argument("--failures", action="store_true")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--category")
    args = parser.parse_args()
    paths = sorted(CORPUS_DIR.glob(f"{args.corpus}_*.txt"))
    cases = load_cases(paths)
    if args.category:
        cases = [case for case in cases if case.category == args.category]
    started = time.perf_counter()
    results = run_cases(cases)
    elapsed = time.perf_counter() - started
    summary = summarize(results)
    summary["corpus"] = args.corpus
    summary["seconds"] = round(elapsed, 2)
    if args.failures:
        for line in failures(results):
            print(line)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.json is not None:
        args.json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
