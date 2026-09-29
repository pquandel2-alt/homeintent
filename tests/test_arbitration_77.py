"""7.7 B3: the arbiter with dialog state as evidence and more readings.

Pure rules (no conversation) plus the conversation-level guarantees: an
open dialog never lowers a confirmation, a superseded question never runs,
time-bound meaning never writes now.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _testhaus import HouseConversation, house_entities  # noqa: E402
from homeintent.arbitration import (  # noqa: E402
    Authority,
    Candidate,
    DecisionKind,
    DialogEvidence,
    DialogReply,
    Effect,
    arbitrate,
    arbitrate_dialog,
    needs_command_reading,
)
from homeintent.arbitration_candidates import (  # noqa: E402
    context_candidates,
    dialog_evidence,
    need_query_candidates,
)
from homeintent.engine import MatchResult  # noqa: E402
from homeintent.nlu.language_frontend import analyse_language  # noqa: E402
from homeintent.nlu.meaning_ir import is_deferred  # noqa: E402
from homeintent.plan_origin import PlanOrigin  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402

ENTITIES = house_entities()


def _writer(source: str, target: str, authority=Authority.PARSER, **kwargs) -> Candidate:
    return Candidate(source, "COMMAND", authority, Effect.WRITE, frozenset({target}),
                     frozenset({"light.turn_on"}), **kwargs)


def _evidence(text: str, reply=DialogReply.UNCLEAR, **kwargs) -> DialogEvidence:
    return dialog_evidence(
        "SERVICE_CONFIRMATION", analyse_language(text, ENTITIES), reply,
        contextual_followup=False, **kwargs,
    )


# ----------------------------------------------------------- dialog rules
def test_yes_and_no_answer_the_open_question():
    for reply in (DialogReply.YES, DialogReply.NO):
        evidence = _evidence("Ja.", reply, supersedable=True, drops_on_new_sentence=True)
        assert arbitrate_dialog(evidence).kind is DecisionKind.CONTINUE_DIALOG


def test_a_full_new_sentence_drops_a_safety_question_and_runs_nothing_of_it():
    evidence = _evidence("Wie spät ist es eigentlich?", supersedable=False, drops_on_new_sentence=True)
    assert arbitrate_dialog(evidence).kind is DecisionKind.CONTINUE_DIALOG  # a question
    evidence = _evidence("Schalte das Küchenlicht ein.", supersedable=False, drops_on_new_sentence=True)
    decision = arbitrate_dialog(evidence)
    assert decision.kind is DecisionKind.SUPERSEDE_DIALOG and decision.chosen == ()


def test_a_complete_new_command_supersedes_a_supersedable_question():
    evidence = _evidence("Schalte das Küchenlicht ein.", supersedable=True)
    assert needs_command_reading(evidence)
    command = _writer("parser", "light.kuechenlicht")
    decision = arbitrate_dialog(evidence, command)
    assert decision.kind is DecisionKind.SUPERSEDE_DIALOG and decision.chosen == (command,)


def test_an_incomplete_command_or_a_composing_dialog_keeps_the_question():
    evidence = _evidence("Schalte das Küchenlicht ein.", supersedable=True)
    half = _writer("parser", "light.kuechenlicht", residue=("zauberkasten",))
    assert arbitrate_dialog(evidence, half).kind is DecisionKind.CONTINUE_DIALOG
    composing = _evidence("Schalte das Küchenlicht ein.", supersedable=False)
    assert not needs_command_reading(composing)
    assert arbitrate_dialog(composing, _writer("parser", "x")).kind is DecisionKind.CONTINUE_DIALOG


def test_dialog_decisions_never_carry_a_confirmation_away():
    """The arbiter only says continue/supersede; the chosen payload is the
    unchanged command, which still passes validator, EffectGraph and policy."""
    command = _writer("parser", "lock.haustuerschloss")
    decision = arbitrate_dialog(_evidence("Schließ die Haustür auf.", supersedable=True), command)
    assert decision.chosen[0] is command
    assert not decision.writes  # never an execution decision of its own


# ------------------------------------------------ evidence of the readings
def _payload(entity_id: str, **kwargs) -> MatchResult:
    domain = entity_id.split(".", 1)[0]
    return MatchResult(plan=ServiceCallPlan(domain, "turn_on", entity_id), response_text="", **kwargs)


def test_a_confirmed_binding_beats_an_inferred_routine_with_the_same_target():
    need = _payload("script.schlafen", origin=PlanOrigin.INFERRED_ROUTINE, binding_confirmed=True)
    parser = _payload("script.schlafen", origin=PlanOrigin.INFERRED_ROUTINE)
    candidates = need_query_candidates(None, need, "COMMAND", parser=parser,
                                       text="Starte die Schlafroutine.", entities=ENTITIES)
    authorities = {item.source: item.authority for item in candidates}
    assert authorities == {"need": Authority.BINDING, "parser": Authority.NEED}
    decision = arbitrate(candidates)
    assert decision.chosen[0].source == "need"


def test_an_explicitly_named_scene_beats_a_need():
    need = _payload("script.gute_nacht")
    parser = _payload("scene.filmabend")
    candidates = need_query_candidates(None, need, "COMMAND", parser=parser,
                                       text="Aktiviere Filmabend.", entities=ENTITIES)
    decision = arbitrate(candidates)
    assert decision.kind is DecisionKind.EXECUTE and decision.chosen[0].source == "parser"


def test_time_bound_meaning_defers_every_writing_reading():
    for text in ("Schalte in zehn Minuten das Flurlicht ein.",
                 "Wenn die Haustür aufgeht, mach das Flurlicht an.",
                 "Morgen früh die Heizung im Bad auf 22 Grad."):
        assert is_deferred(analyse_language(text, ENTITIES)), text
    assert not is_deferred(analyse_language("Schalte jetzt das Flurlicht ein.", ENTITIES))
    candidates = need_query_candidates(None, _payload("light.flurlicht"), "COMMAND", deferred=True)
    assert arbitrate(candidates).kind is DecisionKind.DEFER


def test_context_readings_with_different_effects_ask():
    candidates = context_candidates([
        ("followup", _payload("light.stehlampe")), ("reference", _payload("light.flurlicht")),
    ])
    assert arbitrate(candidates).kind is DecisionKind.ASK
    same = context_candidates([
        ("followup", _payload("light.stehlampe")), ("reference", _payload("light.stehlampe")),
    ])
    assert arbitrate(same).writes


# ------------------------------------------------------- conversation level
@pytest.mark.parametrize("sentence", [
    "Schalte in zehn Minuten das Küchenlicht ein.",
    "Wenn es dunkel wird, mach das Küchenlicht an.",
])
def test_time_bound_sentences_never_write_now(monkeypatch, sentence):
    house = HouseConversation(monkeypatch)
    assert house.say(sentence).calls == []


def test_open_safety_question_is_not_answered_by_a_new_sentence(monkeypatch):
    house = HouseConversation(monkeypatch)
    first = house.say("Schließ die Haustür auf.")
    assert first.calls == []
    house.say("Schalte das Küchenlicht ein.")
    assert house.say("Ja.").calls == []
