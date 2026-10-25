"""First typed NLU phase-4 matrix: incomplete future/solar automations."""

from __future__ import annotations

import pytest

from homeintent.entities import EntitySnapshot
from homeintent.nlu.automation_turn_router import (
    AutomationMeaningSlot as Slot,
    AutomationRouteKind,
    project_automation_meaning,
    route_automation_turn,
)
from homeintent.nlu.language_frontend import analyse_language


HEATING = EntitySnapshot(
    "climate.wohnzimmer",
    "Heizung",
    "climate",
    "heat",
    area_id="living",
    area_name="Wohnzimmer",
)


@pytest.mark.parametrize(
    "text",
    [
        "Morgen nach Sonnenuntergang noch warm.",
        "Nach Sonnenuntergang morgen bitte noch warm.",
        "Bitte morgen nach Sonnenuntergang weiterhin warm.",
    ],
)
def test_incomplete_future_state_metamorphs_request_the_same_slots(text):
    decision = route_automation_turn(analyse_language(text, [HEATING]), [HEATING])

    assert decision.kind is AutomationRouteKind.INCOMPLETE
    assert decision.meaning.missing_slots == (Slot.TARGET, Slot.ACTION)
    assert decision.question is not None
    assert "Bedingung" not in decision.question


@pytest.mark.parametrize(
    "text",
    [
        "Morgen nach Sonnenuntergang schalte die Heizung ein.",
        "Schalte bitte morgen nach Sonnenuntergang die Heizung ein.",
        "Morgen nach Sonnenuntergang möchte ich die Heizung einschalten.",
    ],
)
def test_complete_action_and_target_stay_behind_compatibility_adapter(text):
    decision = route_automation_turn(analyse_language(text, [HEATING]), [HEATING])

    assert decision.kind is AutomationRouteKind.COMPATIBILITY
    assert {Slot.ACTION, Slot.TARGET, Slot.DATE, Slot.SUN_EVENT} <= decision.meaning.slots


@pytest.mark.parametrize(
    "text",
    [
        "Sag mir Bescheid, wenn der Stromverbrauch heute über 10 kWh liegt.",
        "Melde dich, wenn der Energieverbrauch heute über 10 Kilowattstunden liegt.",
        "Warne mich, wenn heute mehr als 10000 Wh Strom verbraucht wurden.",
    ],
)
def test_complete_monitoring_reactions_never_open_an_incomplete_slot_dialog(text):
    decision = route_automation_turn(analyse_language(text, [HEATING]), [HEATING])

    assert decision.kind is AutomationRouteKind.COMPATIBILITY
    assert Slot.ACTION in decision.meaning.slots


@pytest.mark.parametrize(
    "text",
    [
        "20 Minuten vor Sonnenuntergang schalte die Heizung ein.",
        "Zwanzig Minuten vor Sonnenuntergang schalte die Heizung ein.",
    ],
)
def test_digit_and_number_word_sun_offsets_have_the_same_slots(text):
    meaning = project_automation_meaning(analyse_language(text, [HEATING]), [HEATING])

    assert {Slot.SUN_EVENT, Slot.SUN_OFFSET, Slot.DURATION, Slot.ACTION, Slot.TARGET} <= meaning.slots


def test_date_time_repeat_exception_and_validity_are_compositional_slots():
    text = (
        "Jeden Montag morgen um 8 Uhr schalte alle Heizungen außer der Heizung ein."
    )
    meaning = project_automation_meaning(analyse_language(text, [HEATING]), [HEATING])

    assert {Slot.DATE, Slot.CLOCK, Slot.REPETITION, Slot.VALIDITY, Slot.EXCEPTION} <= meaning.slots


def test_existing_goal_clause_stays_behind_compatibility_adapter():
    text = "Sorge dafür, dass es morgen um 7 Uhr im Wohnzimmer 21 Grad hat."

    decision = route_automation_turn(analyse_language(text, [HEATING]), [HEATING])

    assert decision.kind is AutomationRouteKind.COMPATIBILITY


@pytest.mark.parametrize(
    "text",
    [
        "Morgen nach Sonnenuntergang nicht warm.",
        "Wenn es morgen nach Sonnenuntergang warm wäre.",
        "Ich überlege, ob es morgen nach Sonnenuntergang warm sein soll.",
    ],
)
def test_negative_and_hypothetical_fragments_are_never_actionable(text):
    decision = route_automation_turn(analyse_language(text, [HEATING]), [HEATING])

    assert decision.kind is AutomationRouteKind.UNSAFE


def test_incomplete_fragment_end_to_end_never_writes(monkeypatch):
    from _testhaus import HouseConversation

    house = HouseConversation(monkeypatch)

    turn = house.say("Morgen nach Sonnenuntergang noch warm.")

    assert "Automation" in turn.speech
    assert "Bedingung" not in turn.speech
    assert turn.calls == []
