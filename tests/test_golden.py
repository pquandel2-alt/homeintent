"""Replays tests/golden/*.json (v2 plan Phase 20, "Golden Tests") against
the real engine. Fixtures are generated (not hand-written) by
scripts/generate_golden_tests.py - see that script's module docstring for
why. Uses the same GOLDEN_ENTITIES as the generator so name/area resolution
picks identical candidates on every run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from golden_fixtures import GOLDEN_ENTITIES

GOLDEN_DIR = Path(__file__).parent / "golden"


def _load_cases() -> list[tuple[str, dict]]:
    cases = []
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        for case in json.loads(path.read_text(encoding="utf-8")):
            cases.append((path.stem, case))
    return cases


CASES = _load_cases()
CASE_IDS = [f"{category}:{case['text']}" for category, case in CASES]


@pytest.mark.parametrize("category,case", CASES, ids=CASE_IDS)
def test_golden_case(engine, category, case):
    result = engine.match(case["text"], GOLDEN_ENTITIES)

    if not case["expect_match"]:
        assert result is None
        return

    # "multi_step" cases (V6 plan, Golden-Benchmark) hold a CommandPlan - one
    # MatchResult per "und"-joined segment - instead of a single MatchResult,
    # so they're replayed against ``result.commands`` rather than the plain
    # ``result.plan``/``result.response_text`` shape below.
    if category == "multi_step":
        assert result is not None
        assert len(result.commands) == len(case["commands"])
        for command, expected in zip(result.commands, case["commands"]):
            assert command.response_text == expected["response_text"]
            if expected["plan"] is None:
                assert command.plan is None
            else:
                assert command.plan is not None
                assert command.plan.domain == expected["plan"]["domain"]
                assert command.plan.service == expected["plan"]["service"]
                assert command.plan.entity_id == expected["plan"]["entity_id"]
                assert command.plan.data == expected["plan"]["data"]
        return

    assert result is not None
    assert result.response_text == case["response_text"]

    if case["plan"] is None:
        assert result.plan is None
    else:
        assert result.plan is not None
        assert result.plan.domain == case["plan"]["domain"]
        assert result.plan.service == case["plan"]["service"]
        assert result.plan.entity_id == case["plan"]["entity_id"]
        assert result.plan.data == case["plan"]["data"]
