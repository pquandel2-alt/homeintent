"""Phase 7 (7.5.0): shared meaning layer and arbitration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import _ha_stub

_ha_stub.install()

from _testhaus import HouseConversation, house_entities  # noqa: E402
from homeintent.arbitration import (  # noqa: E402
    Authority,
    Candidate,
    DecisionKind,
    Effect,
    arbitrate,
)
from homeintent.arbitration_candidates import collect_candidates  # noqa: E402
from homeintent.engine import NluEngine  # noqa: E402
from homeintent.nlu.meaning_ir import ground_meaning  # noqa: E402
from homeintent.nlu.semantic_utterance import SpeechAct, TimeKind  # noqa: E402

HOUSE = house_entities()
ENGINE = NluEngine()
COLLISIONS = json.loads((Path(__file__).parent / "eval" / "collisions_v75.json").read_text(encoding="utf-8"))


def cand(source="parser", effect=Effect.WRITE, authority=Authority.PARSER, targets=("light.a",), ops=("light.turn_on",), **kw):
    return Candidate(source, "COMMAND", authority, effect, frozenset(targets), frozenset(ops), **kw)


# ------------------------------------------------------------ rules
def test_single_executable_without_residue_runs():
    assert arbitrate([cand()]).kind is DecisionKind.EXECUTE


def test_executable_with_residue_never_runs():
    decision = arbitrate([cand(residue=("flausche",))])
    assert decision.kind is DecisionKind.NOTHING and not decision.writes


def test_same_effect_is_merged_and_the_strongest_authority_kept():
    decision = arbitrate([cand(authority=Authority.NEED), cand(authority=Authority.PARSER)])
    assert decision.kind is DecisionKind.MERGE and decision.chosen[0].authority is Authority.PARSER


def test_explicit_device_beats_a_need():
    explicit = cand(targets=("light.stehlampe",), explicit_device=True)
    need = cand(authority=Authority.NEED, targets=("cover.x",), ops=("cover.close_cover",))
    decision = arbitrate([need, explicit])
    assert decision.kind is DecisionKind.EXECUTE and decision.chosen == (explicit,)


def test_contradicting_executables_without_evidence_ask():
    a = cand(targets=("light.a",))
    b = cand(targets=("light.b",))
    decision = arbitrate([a, b])
    assert decision.kind is DecisionKind.ASK and not decision.writes


def test_explicit_question_beats_a_command_reading():
    reader = cand("situation", Effect.READ, targets=())
    decision = arbitrate([cand(), reader], explicit_question=True)
    assert decision.kind is DecisionKind.ANSWER and decision.chosen == (reader,)


def test_read_only_candidates_are_all_answered_without_executables():
    readers = [cand("a", Effect.READ, targets=()), cand("b", Effect.READ, targets=())]
    assert arbitrate(readers).chosen == tuple(readers)


def test_time_bound_meaning_is_deferred():
    assert arbitrate([cand(), cand("automation", Effect.DEFER, targets=())]).kind is DecisionKind.DEFER


def test_nothing_without_candidates():
    assert arbitrate([]).kind is DecisionKind.NOTHING


# ------------------------------------------------------------ meaning layer
def test_ir_fills_operation_targets_value_time_exceptions_and_origin():
    meaning = ground_meaning("Mach alle Lichter außer der Stehlampe aus.", HOUSE)
    clause = meaning.clauses[0]
    assert clause.operation == frozenset({"turn_off"})
    assert clause.targets[0].genera == ("light",) and clause.targets[0].quantity == "ALL"
    assert clause.exceptions == ("Stehlampe",)
    assert clause.origin == "explicit_command" and clause.time is TimeKind.NOW
    value = ground_meaning("Stell die Heizung im Bad auf 22 Grad.", HOUSE).clauses[0]
    assert value.value == {"temperature": 22.0} and value.targets[0].place


@pytest.mark.parametrize("text,time", [
    ("Mach jetzt das Flurlicht an.", TimeKind.NOW),
    ("Mach das Küchenlicht für eine Stunde an.", TimeKind.NOW),
    ("Schalte jeden Tag um 22 Uhr das Flurlicht aus.", TimeKind.RECURRING),
    ("Schalte heute um 22 Uhr das Flurlicht aus.", TimeKind.ONCE),
    ("Mach in 10 Minuten das Küchenlicht aus.", TimeKind.UNSPECIFIED),
])
def test_ir_time(text, time):
    assert ground_meaning(text, HOUSE).clauses[0].time is time


def test_ir_origin_references_and_residue():
    assert ground_meaning("Mir ist kalt.", HOUSE).clauses[0].origin == "implicit_need"
    assert ground_meaning("Ist das Küchenlicht an?", HOUSE).clauses[0].origin is None
    assert ground_meaning("Mach es aus.", HOUSE).clauses[0].targets[0].reference == "es"
    assert "zauberkasten" in ground_meaning("Mach den Zauberkasten an.", HOUSE).clauses[0].residue


def test_ir_never_touches_home_assistant():
    import homeintent.nlu.meaning_ir as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "async_call" not in source and "hass" not in source


def test_arbiter_modules_never_execute():
    import homeintent.arbitration as arbitration
    import homeintent.arbitration_candidates as candidates

    for module in (arbitration, candidates):
        assert "async_call" not in Path(module.__file__).read_text(encoding="utf-8")


# ------------------------------------------------------------ collision corpus
@pytest.mark.parametrize("item", COLLISIONS, ids=[item["text"] for item in COLLISIONS])
def test_collision_corpus(monkeypatch, item):
    candidates, question = collect_candidates(ENGINE, item["text"], HOUSE)
    decision = arbitrate(candidates, explicit_question=question)
    expected = item["expected"]
    if expected == "ANSWER_OR_NOTHING":
        assert not decision.writes, decision
    elif expected == "DEFER":
        assert decision.kind in {DecisionKind.DEFER, DecisionKind.NOTHING}, decision
    elif expected == "EXECUTE":
        assert decision.writes, decision
    if expected in {"ANSWER_OR_NOTHING", "NO_WRITE_WITHOUT_YES", "DEFER"}:
        # The conversation (with the arbiter for need ↔ question) never
        # writes on these without an explicit "Ja".
        turn = HouseConversation(monkeypatch).say(item["text"])
        assert turn.calls == [], (item["text"], turn.speech)


def test_speech_acts_of_the_collision_pairs():
    assert ground_meaning("Ist es im Büro zu dunkel?", HOUSE).speech_act is SpeechAct.QUERY
    assert ground_meaning("Im Büro ist es zu dunkel.", HOUSE).clauses[0].origin == "implicit_need"
