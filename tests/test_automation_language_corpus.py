"""Release gates for the 7.2.0 automation language corpora (spec §41-§43, §76, §91-§93).

Every line runs through the real conversation entity with an instrumented
service sink; a stray "Ja" follows every non-automation line.  The held-out
corpus was frozen before implementation (see its README); its thresholds are
the honestly measured values in docs/perf/automation-language-7.2.0-*.json
and only guard against regressions - they were never tuned against.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _automation_eval import CORPUS_DIR, failures, load_cases, run_cases, summarize  # noqa: E402


@pytest.fixture(scope="module")
def dev():
    results = run_cases(load_cases(sorted(CORPUS_DIR.glob("dev_*.txt"))))
    return results, summarize(results)


@pytest.fixture(scope="module")
def heldout():
    results = run_cases(load_cases(sorted(CORPUS_DIR.glob("heldout_*.txt"))))
    return results, summarize(results)


def test_corpus_sizes():
    dev_cases = load_cases(sorted(CORPUS_DIR.glob("dev_*.txt")))
    heldout_cases = load_cases(sorted(CORPUS_DIR.glob("heldout_*.txt")))
    automation = [case for case in (*dev_cases, *heldout_cases) if case.kind != "reject"]
    negatives = [case for case in heldout_cases if case.kind == "reject"]
    assert len(automation) >= 1000
    assert len(negatives) >= 300


def test_zero_unsafe_execution_anywhere(dev, heldout):
    for results, summary in (dev, heldout):
        assert summary["unsafe_wrong_execution"] == 0, "\n".join(
            line for line in failures(results) if line.startswith("[unsafe]")
        )


def test_every_negative_is_rejected(dev, heldout):
    for _, summary in (dev, heldout):
        assert summary["negative_correct"] == summary["negative_cases"]


def test_development_corpus_quality(dev):
    results, summary = dev
    assert summary["unsafe_false_positive_previews"] == 0, "\n".join(failures(results))
    assert summary["automation_accuracy"] >= 0.99, "\n".join(failures(results))


def test_heldout_generalization_does_not_regress(heldout):
    results, summary = heldout
    # Measured 7.2.0 values: 316/332 correct, 2 disputed false previews
    # ("das Fenster ... und so", "der Akku" with exactly one battery sensor).
    assert summary["automation_accuracy"] >= 0.95, "\n".join(failures(results))
    assert summary["wrong_semantic_interpretation"] <= 1, "\n".join(failures(results))
    assert summary["unsafe_false_positive_previews"] <= 2, "\n".join(failures(results))
    assert summary["unsupported_correctly_rejected"] == summary["unsupported_cases"]
