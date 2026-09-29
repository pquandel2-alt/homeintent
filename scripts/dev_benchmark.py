#!/usr/bin/env python3
"""Development language benchmark (7.7 B8).

A development tool, not independent evidence: the corpus
(``tests/eval/dev_benchmark_77.txt``) was written by the session that also
changes the code. The independent measurement stays with the test session
and its unpublished corpus.

Every case runs through the real conversation entity against the stub test
house (``tests/_testhaus.py``); nothing reaches a real Home Assistant. Per
turn the observed behaviour is

* ``outcome`` – ``execute`` (a service call), ``confirm`` (a pending safety
  confirmation), ``draft`` (an automation waiting for confirmation),
  ``clarify`` (a question back) or ``answer`` (anything else, no write),
* the executed calls (operation, targets, data, notification recipient),
* the meaning IR of the sentence (speech act, time, condition).

Corpus format, one case per line (``#`` starts a comment)::

    category [heldout] :: turn || turn :: expectation || expectation

An expectation is a space separated list of

* an outcome: ``execute``, ``confirm``, ``draft``, ``clarify``, ``answer``,
  ``nowrite`` (any outcome without a write), ``ask`` (confirm or clarify),
* ``call=<domain>.<service>:<entity>[,<entity>]`` – one expected call;
  ``<domain>`` ``turn`` matches ``homeassistant``/the entity domain,
* ``<key>=<value>`` – expected call data (``brightness_pct=30``),
* ``act=<SPEECH_ACT>``, ``time=<TimeKind>``, ``cond`` (a condition),
  ``to=<entity>`` (notification recipient), ``say=<word>`` (in the reply),
* ``-`` – the turn is not scored (context only), but still safety-checked.

``unsafe_execution_count`` counts turns that wrote although no write was
expected, wrote to other entities or with another operation than expected,
or executed where a confirmation was expected. It must always be 0.

    python scripts/dev_benchmark.py                 # report
    python scripts/dev_benchmark.py --json out.json
    python scripts/dev_benchmark.py --check         # exit 1 on unsafe executions
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))
sys.path.insert(0, str(ROOT / "tests"))
CORPUS = ROOT / "tests" / "eval" / "dev_benchmark_77.txt"
OUTCOMES = {"execute", "confirm", "draft", "clarify", "answer", "nowrite", "ask"}
ERROR_CLASSES = (
    "UNDERSTANDING", "GROUNDING", "AMBIGUITY", "CAPABILITY", "DIALOG",
    "POLICY", "RESPONSE", "TEST_EXPECTATION", "SAFETY",
)
DIMENSIONS = (
    "speech_act", "operation", "target", "place", "quantity", "value", "time",
    "condition", "recipient", "clarification", "confirmation", "no_write", "response",
)


@dataclass
class Expectation:
    scored: bool = True
    outcome: str | None = None
    calls: list[tuple[str, str, frozenset[str]]] = field(default_factory=list)
    data: dict[str, str] = field(default_factory=dict)
    act: str | None = None
    time: str | None = None
    condition: bool = False
    recipient: str | None = None
    say: list[str] = field(default_factory=list)


@dataclass
class Case:
    line: int
    category: str
    heldout: bool
    turns: list[str]
    expectations: list[Expectation]


def parse_expectation(text: str) -> Expectation:
    expectation = Expectation()
    for token in text.split():
        if token == "-":
            expectation.scored = False
        elif token in OUTCOMES:
            expectation.outcome = token
        elif token == "cond":
            expectation.condition = True
        elif token.startswith("call="):
            operation, _, targets = token[5:].partition(":")
            domain, _, service = operation.partition(".")
            expectation.calls.append((domain, service, frozenset(filter(None, targets.split(",")))))
        elif token.startswith("act="):
            expectation.act = token[4:]
        elif token.startswith("time="):
            expectation.time = token[5:]
        elif token.startswith("to="):
            expectation.recipient = token[3:]
        elif token.startswith("say="):
            expectation.say.append(token[4:].replace("_", " "))
        elif "=" in token:
            key, _, value = token.partition("=")
            expectation.data[key] = value
        else:
            raise ValueError(f"unknown expectation token {token!r}")
    if expectation.calls and expectation.outcome is None:
        expectation.outcome = "execute"
    return expectation


def load(path: Path = CORPUS) -> list[Case]:
    cases = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split(" #", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        head, turns, expected = (part.strip() for part in line.split("::"))
        category, *flags = head.split()
        turn_list = [turn.strip() for turn in turns.split("||")]
        expectations = [parse_expectation(part) for part in expected.split("||")]
        if len(turn_list) != len(expectations):
            raise ValueError(f"line {number}: {len(turn_list)} turns, {len(expectations)} expectations")
        cases.append(Case(number, category, "heldout" in flags, turn_list, expectations))
    return cases


@dataclass
class Observed:
    outcome: str
    calls: list[tuple[str, str, dict]]
    speech: str
    act: str
    time: str
    condition: bool


def _is_read(call: tuple[str, str, dict]) -> bool:
    """Service calls that only read (to-do items, calendar events, forecasts)."""
    return call[1].startswith("get_")


def _targets(data: dict) -> set[str]:
    value = data.get("entity_id")
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple)):
        return set(value)
    return set()


def _operation_matches(expected: tuple[str, str, frozenset[str]], call: tuple[str, str, dict]) -> bool:
    domain, service, _targets_expected = expected
    call_domain, call_service, data = call
    if service != call_service:
        return False
    if domain in {"turn", call_domain}:
        return True
    return call_domain == "homeassistant" and all(t.split(".")[0] == domain for t in _targets(data))


class Runner:
    def __init__(self) -> None:
        import tempfile

        import _ha_stub
        import pytest

        _ha_stub.install()
        from _testhaus import HouseConversation

        # With a tmp path the test house has both phones as push targets and
        # each user bound to their own phone, like the live test bed.
        self._tmp = tempfile.TemporaryDirectory()
        self.house = HouseConversation(pytest.MonkeyPatch(), tmp_path=Path(self._tmp.name))
        self.counter = 0

    def run_case(self, case: Case) -> list[Observed]:
        from homeintent.nlu.context import PendingDialogKind, active_pending_dialog
        from homeintent.nlu.language_frontend import analyse_language
        from homeintent.nlu.meaning_ir import ground_meaning

        self.counter += 1
        self.house.conversation_id = f"bench-{self.counter}"
        observed = []
        for text in case.turns:
            turn = self.house.say(text)
            context = self.house.entity._context_store.get(self.house.conversation_id)
            pending = active_pending_dialog(context)
            calls = [call for call in turn.calls if not _is_read(call)]
            if calls:
                outcome = "execute"
            elif pending is not None and pending.kind is PendingDialogKind.SERVICE_CONFIRMATION:
                outcome = "confirm"
            elif pending is not None and pending.kind.name.startswith("AUTOMATION"):
                outcome = "draft"
            elif pending is not None or "?" in turn.speech:
                outcome = "clarify"
            else:
                outcome = "answer"
            entities = self.house.entities
            document = analyse_language(text, entities)
            meaning = ground_meaning(document, entities)
            clause = meaning.clauses[0] if meaning.clauses else None
            observed.append(Observed(
                outcome=outcome,
                calls=calls,
                speech=turn.speech,
                act=document.utterance.speech_act.name,
                time=clause.time.name if clause is not None else "",
                condition=bool(clause is not None and clause.conditions),
            ))
        return observed


def score_turn(expected: Expectation, observed: Observed, area_of: dict[str, str | None]) -> tuple[dict[str, bool], bool]:
    """Per-dimension result (only dimensions the expectation names) and
    whether the turn executed unsafely."""
    result: dict[str, bool] = {}
    wrote = bool(observed.calls)
    expected_targets = set().union(*(targets for _d, _s, targets in expected.calls)) if expected.calls else set()
    observed_targets = set().union(*(_targets(data) for _d, _s, data in observed.calls)) if observed.calls else set()
    unsafe = False
    if wrote:
        allowed = expected.outcome == "execute" or (not expected.scored and expected.outcome is None)
        if not allowed and expected.scored:
            unsafe = True
        if expected.calls and not observed_targets <= expected_targets:
            unsafe = True
        if expected.calls and not all(
            any(_operation_matches(exp, call) for exp in expected.calls) for call in observed.calls
        ):
            unsafe = True
    if not expected.scored:
        return result, unsafe
    outcome = expected.outcome
    if outcome is not None:
        if outcome == "nowrite":
            result["no_write"] = not wrote
        elif outcome == "ask":
            result["clarification"] = observed.outcome in {"clarify", "confirm"}
        elif outcome == "clarify":
            result["clarification"] = observed.outcome == "clarify"
        elif outcome == "confirm":
            result["confirmation"] = observed.outcome == "confirm"
        elif outcome == "draft":
            result["confirmation"] = observed.outcome == "draft"
        elif outcome == "answer":
            result["no_write"] = observed.outcome == "answer"
        elif outcome == "execute":
            result["no_write"] = wrote
    if expected.calls:
        result["operation"] = bool(observed.calls) and all(
            any(_operation_matches(exp, call) for call in observed.calls) for exp in expected.calls
        )
        result["target"] = observed_targets == expected_targets
        result["quantity"] = len(observed_targets) == len(expected_targets)
        result["place"] = {area_of.get(t) for t in observed_targets} == {area_of.get(t) for t in expected_targets}
    if expected.data:
        merged: dict[str, str] = {}
        for _d, _s, data in observed.calls:
            merged.update({key: str(value) for key, value in data.items() if key != "entity_id"})
        result["value"] = all(merged.get(key) == value for key, value in expected.data.items())
    if expected.recipient:
        result["recipient"] = any(expected.recipient in _targets(data) for _d, _s, data in observed.calls)
    if expected.act:
        result["speech_act"] = observed.act == expected.act
    if expected.time:
        result["time"] = observed.time == expected.time
    if expected.condition:
        result["condition"] = observed.condition
    if expected.say:
        folded = observed.speech.casefold()
        result["response"] = all(word.casefold() in folded for word in expected.say)
    return result, unsafe


def error_class(case: Case, index: int, expected: Expectation, observed: Observed,
                failed: set[str], unsafe: bool) -> str:
    if unsafe:
        return "SAFETY"
    if index > 0:
        return "DIALOG"
    if failed <= {"response"}:
        return "RESPONSE"
    if expected.outcome in {"execute", "confirm"} and observed.outcome in {"execute", "confirm"}:
        return "POLICY" if not failed & {"target", "operation", "value"} else "GROUNDING"
    if expected.outcome == "execute" and observed.outcome == "clarify":
        return "AMBIGUITY"
    if expected.outcome in {"clarify", "ask"} and observed.outcome == "execute":
        return "AMBIGUITY"
    speech = observed.speech.casefold()
    if any(word in speech for word in ("unterstützt", "kann ich nicht", "nicht steuern", "keinen für homeintent")):
        return "CAPABILITY"
    if failed & {"target", "place", "quantity"} and not failed & {"operation"}:
        return "GROUNDING"
    return "UNDERSTANDING"


def run(cases: list[Case]) -> dict:
    logging.disable(logging.CRITICAL)
    runner = Runner()
    area_of = {entity.entity_id: entity.area_id for entity in runner.house.entities}
    dimension_totals: dict[str, list[int]] = {name: [0, 0] for name in DIMENSIONS}
    by_category: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    by_split: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    errors: collections.Counter = collections.Counter()
    unsafe_total = 0
    failures = []
    for case in cases:
        observed = runner.run_case(case)
        passed = True
        for index, (expected, seen) in enumerate(zip(case.expectations, observed)):
            result, unsafe = score_turn(expected, seen, area_of)
            unsafe_total += unsafe
            for name, ok in result.items():
                dimension_totals[name][0] += ok
                dimension_totals[name][1] += 1
            failed = {name for name, ok in result.items() if not ok}
            if failed or unsafe:
                passed = False
                kind = error_class(case, index, expected, seen, failed, unsafe)
                errors[kind] += 1
                failures.append({
                    "line": case.line, "category": case.category, "heldout": case.heldout,
                    "turn": case.turns[index], "failed": sorted(failed), "class": kind,
                    "outcome": seen.outcome, "calls": [[d, s, sorted(_targets(data))] for d, s, data in seen.calls],
                    "speech": seen.speech[:160],
                })
        by_category[case.category][0] += passed
        by_category[case.category][1] += 1
        split = "heldout" if case.heldout else "dev"
        by_split[split][0] += passed
        by_split[split][1] += 1
    return {
        "cases": len(cases),
        "turns": sum(len(case.turns) for case in cases),
        "passed": sum(value[0] for value in by_split.values()),
        "unsafe_execution_count": unsafe_total,
        "splits": {name: {"passed": value[0], "total": value[1]} for name, value in sorted(by_split.items())},
        "categories": {name: {"passed": value[0], "total": value[1]} for name, value in sorted(by_category.items())},
        "dimensions": {
            name: {"passed": value[0], "total": value[1]}
            for name, value in dimension_totals.items() if value[1]
        },
        "error_classes": {name: errors.get(name, 0) for name in ERROR_CLASSES},
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--failures", action="store_true")
    parser.add_argument("--split", choices=("dev", "heldout"))
    args = parser.parse_args()
    cases = load(args.corpus)
    if args.split:
        cases = [case for case in cases if case.heldout == (args.split == "heldout")]
    report = run(cases)
    summary = {key: value for key, value in report.items() if key != "failures"}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if args.failures:
        for failure in report["failures"]:
            print(json.dumps(failure, ensure_ascii=False))
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.check and report["unsafe_execution_count"]:
        print(f"unsafe_execution_count = {report['unsafe_execution_count']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    asyncio.set_event_loop_policy(None)
    raise SystemExit(main())
