#!/usr/bin/env python3
"""Offline shadow comparison of a candidate pipeline (7.3.4).

Runs the active pipeline and a named candidate over every published corpus
(testbed scenarios, NLU probe, dialog cases, golden files, automation
corpus) against the test house and classifies each difference
(EQUIVALENT / REFINEMENT / BEHAVIOR_CHANGE / SAFETY_DRIFT). Nothing is
executed. ``--check`` exits 1 on any SAFETY_DRIFT: that blocks switching.

    python scripts/shadow_compare.py --candidate identity --check
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))
sys.path.insert(0, str(ROOT / "tests"))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _testhaus import house_entities  # noqa: E402
from homeintent.engine import NluEngine  # noqa: E402
from homeintent.nlu.understanding import ShadowReport  # noqa: E402
from homeintent.shadow_candidates import CANDIDATES, active_pipeline  # noqa: E402
from homeintent.shadow_runtime import signature_for  # noqa: E402


def _scenario_sentences() -> list[str]:
    """``say("…")`` texts of sim/scenarios.py, read without importing sim."""
    tree = ast.parse((ROOT / "sim" / "scenarios.py").read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "say"
            and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
        ):
            found.append(node.args[0].value)
    return found


def _probe_sentences() -> list[str]:
    tree = ast.parse((ROOT / "sim" / "nlu_probe.py").read_text(encoding="utf-8"))
    return [
        node.args[1].value for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "case"
        and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)
    ]


def corpus() -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    items += [("scenarios", text) for text in _scenario_sentences()]
    items += [("nlu_probe", text) for text in _probe_sentences()]
    for case in json.loads((ROOT / "tests/eval/dialog_cases.json").read_text(encoding="utf-8")):
        items += [("dialog_cases", turn) for turn in case.get("turns", []) if isinstance(turn, str)]
    for path in sorted((ROOT / "tests/golden").glob("*.json")):
        items += [("golden", case["text"]) for case in json.loads(path.read_text(encoding="utf-8")) if "text" in case]
    for path in sorted((ROOT / "tests/eval/automation_v72").glob("*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if " :: " in line and not line.startswith("#"):
                items += [("automation_v72", turn.strip()) for turn in line.split(" :: ", 1)[1].split(">>")]
    seen: set[str] = set()
    unique = []
    for source, text in items:
        if text and text not in seen:
            seen.add(text)
            unique.append((source, text))
    return unique


def run(candidate: str) -> ShadowReport:
    engine = NluEngine()
    entities = house_entities()
    factory = CANDIDATES[candidate]
    report = ShadowReport(candidate)
    for source, text in corpus():
        active = active_pipeline(engine, text, entities)
        other = factory(engine, text, entities)
        report.add(text, signature_for(active, entities), signature_for(other, entities), source=source)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default="identity", choices=sorted(CANDIDATES))
    parser.add_argument("--output")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = run(args.candidate)
    summary = report.to_dict(examples=50)
    if args.output:
        Path(args.output).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{args.candidate}: {summary['total']} Sätze, {summary['counts']}")
    for record in report.safety_drift[:10]:
        print("  SAFETY_DRIFT", record.text_hash, record.source, record.reasons)
    return 1 if args.check and not report.switch_allowed else 0


if __name__ == "__main__":
    raise SystemExit(main())
