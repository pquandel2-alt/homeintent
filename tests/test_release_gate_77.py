"""7.7 B9: the statically checkable part of the release gate.

Dynamic gates run as their own CI steps: shadow comparisons (SAFETY_DRIFT),
arbiter shadow, corpus signatures, the development benchmark with
``--check`` (unsafe executions), the latency budget and pyright. HACS,
hassfest, the real Home Assistant tests, the live test bed and the
independent retest of the test session are outside this repository's CI.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_sentence_patterns_stay_below_180():
    data = json.loads((ROOT / "docs/regex-klassifikation.json").read_text(encoding="utf-8"))
    assert data["anzahl"]["SEMANTIC_SENTENCE_PATTERN"] < 180


def test_recorded_benchmark_has_500_cases_and_no_unsafe_execution():
    report = json.loads((ROOT / "docs/perf/dev-benchmark-7.7.json").read_text(encoding="utf-8"))
    assert report["cases"] >= 500
    assert report["unsafe_execution_count"] == 0
    first_run = json.loads(
        (ROOT / "docs/perf/dev-benchmark-7.7-heldout-first-run.json").read_text(encoding="utf-8")
    )
    assert first_run["splits"] == {"heldout": {"passed": 88, "total": 113}}


def test_property_suite_carries_the_77_invariants():
    tree = ast.parse((ROOT / "tests/test_safety_properties.py").read_text(encoding="utf-8"))
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert {
        "test_stt_variant_never_changes_the_safety_form",
        "test_self_correction_never_executes_the_retracted_part",
        "test_open_dialog_never_lowers_the_confirmation_duty",
        "test_learned_default_choice_never_bypasses_confirmation",
        "test_learned_alias_never_reaches_beyond_its_exposed_target",
        "test_learned_binding_never_reaches_unexposed_targets",
        "test_macro_never_bypasses_confirmation_of_critical_steps",
        "test_habit_learning_never_learns_what_a_sentence_means",
    } <= names


def test_ci_runs_every_dynamic_gate():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for step in (
        "scripts/corpus_shadow.py",
        "scripts/shadow_compare.py --candidate identity --check",
        "scripts/arbiter_shadow.py --check",
        "scripts/dev_benchmark.py --check",
        "--max-p95-ms 100",
        "python -m pyright",
    ):
        assert step in workflow, step
