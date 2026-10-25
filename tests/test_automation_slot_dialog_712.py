"""Real conversation-pipeline tests for typed automation slot filling."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from _testhaus import HouseConversation
from homeintent.conversation_scope import conversation_scope_key_for_input


def test_solar_heating_draft_fills_over_five_turns_and_supports_correction(
    monkeypatch, tmp_path
):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)

    first = house.say("Morgen nach Sonnenuntergang noch warm.")
    assert "Automation" in first.speech
    assert house.automations() == [] and first.calls == []

    second = house.say("Im Wohnzimmer.")
    assert "Temperatur" in second.speech
    assert house.automations() == [] and second.calls == []

    preview = house.say("Stell die Heizung auf 21 Grad.")
    assert "Soll diese Automation erstellt werden?" in preview.speech
    assert house.automations() == [] and preview.calls == []

    revised = house.say("Nein, lieber 20 Grad.")
    assert "20" in revised.speech
    assert "Soll diese Automation erstellt werden?" in revised.speech
    assert house.automations() == [] and revised.calls == []

    confirmed = house.say("Ja.")
    assert confirmed.speech == "Automation wurde erstellt."
    [created] = house.automations()
    call = next(action for action in created["actions"] if action.get("action") == "climate.set_temperature")
    assert call["target"]["entity_id"] == "climate.heizung_wohnzimmer"
    assert call["data"]["temperature"] == 20.0


def test_clock_light_draft_never_writes_before_final_yes(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)

    question = house.say("Morgen um acht das Licht.")
    assert "Licht" in question.speech
    assert house.automations() == []

    preview = house.say("Im Flur einschalten.")
    assert "Soll diese Automation erstellt werden?" in preview.speech
    assert house.automations() == []

    house.say("Ja.")
    [created] = house.automations()
    assert created["actions"][0]["action"] == "homeassistant.turn_on"
    assert created["actions"][0]["target"]["entity_id"] == "light.flurlicht"


def test_slot_followup_reuses_the_turn_language_document(monkeypatch, tmp_path):
    import homeintent.engine as engine_module

    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    house.say("Morgen um acht das Licht.")
    original = engine_module.analyse_language
    reparses = 0

    def counted(*args, **kwargs):
        nonlocal reparses
        reparses += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(engine_module, "analyse_language", counted)
    preview = house.say("Im Flur einschalten.")

    assert "Soll diese Automation erstellt werden?" in preview.speech
    assert reparses == 0


def test_slot_draft_cancel_discards_everything(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)

    house.say("Morgen nach Sonnenuntergang noch warm.")
    cancelled = house.say("Vergiss es.")

    assert "verworfen" in cancelled.speech
    assert house.automations() == [] and cancelled.calls == []


def test_other_user_and_other_satellite_cannot_continue_draft(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    public_id = "shared-public-id"
    house.say_input(house.conversation_input(
        "Morgen nach Sonnenuntergang noch warm.",
        conversation_id=public_id,
        user="admin",
        satellite_id="satellite.wohnzimmer",
    ))

    foreign_user = house.say_input(house.conversation_input(
        "Ja.", conversation_id=public_id, user="anna",
        satellite_id="satellite.wohnzimmer",
    ))
    other_satellite = house.say_input(house.conversation_input(
        "Im Wohnzimmer.", conversation_id=public_id, user="admin",
        satellite_id="satellite.werkstatt",
    ))

    assert house.automations() == []
    assert foreign_user.calls == [] and other_satellite.calls == []
    resumed = house.say_input(house.conversation_input(
        "Im Wohnzimmer.", conversation_id=public_id, user="admin",
        satellite_id="satellite.wohnzimmer",
    ))
    assert "Temperatur" in resumed.speech
    assert house.automations() == []


@pytest.mark.parametrize("connector", ["nur wenn", "wenn", "sofern"])
def test_explicit_condition_is_filled_but_is_not_required_otherwise(
    monkeypatch, tmp_path, connector
):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)

    first = house.say(f"Morgen um acht das Licht, {connector} Anna zuhause ist.")
    assert "mit dem Licht passieren" in first.speech
    assert house.automations() == []

    preview = house.say("Im Flur einschalten.")
    assert "Anna zuhause" in preview.speech
    assert "Soll diese Automation erstellt werden?" in preview.speech
    assert house.automations() == []


def test_water_event_accepts_two_typed_actions_in_followup(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    existing_switch = next(entity for entity in house.entities if entity.domain == "switch")
    house.entities.append(replace(
        existing_switch,
        entity_id="switch.waschmaschine",
        friendly_name="Waschmaschine",
        area_id="hauswirtschaftsraum",
        area_name="Hauswirtschaftsraum",
        floor_id="keller",
        floor_name="Keller",
        floor_level=-1,
    ))

    first = house.say("Wenn im Keller Wasser erkannt wird.")
    assert first.speech == "Was soll dann passieren?"
    assert house.automations() == []

    preview = house.say(
        "Sag mir sofort Bescheid und schalte die Waschmaschine aus."
    )
    assert "eine Benachrichtigung senden" in preview.speech
    assert "Schalter" in preview.speech and "ausschalten" in preview.speech
    assert house.automations() == []


def test_anonymous_turn_cannot_open_or_inherit_automation_draft(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    anonymous = house.say_input(house.conversation_input(
        "Morgen um acht das Licht.", user=None, conversation_id="anonymous"
    ))

    assert "angemeldet" in anonymous.speech
    assert house.automations() == [] and anonymous.calls == []


def test_expired_slot_draft_is_discarded_without_write(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    opening = house.conversation_input(
        "Morgen nach Sonnenuntergang noch warm.", conversation_id="expires"
    )
    house.say_input(opening)
    scope = conversation_scope_key_for_input(opening)
    context = house.entity._context_store.get(scope)
    assert context is not None and context.pending_automation_slot_draft is not None
    expired = replace(
        context.pending_automation_slot_draft,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    house.entity._context_store.set(
        scope, replace(context, pending_automation_slot_draft=expired)
    )

    answer = house.say_input(house.conversation_input(
        "Im Wohnzimmer.", conversation_id="expires"
    ))
    assert "abgelaufen" in answer.speech
    assert house.automations() == [] and answer.calls == []


def test_explicit_topic_switch_discards_slot_draft(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    house.say("Morgen nach Sonnenuntergang noch warm.")

    switched = house.say("Mach lieber das Flurlicht an.")
    assert switched.calls and switched.calls[-1][1] == "turn_on"
    assert house.automations() == []

    later_yes = house.say("Ja.")
    assert house.automations() == []
    assert later_yes.calls == []


def test_two_conversations_keep_independent_slot_drafts(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    for conversation_id in ("one", "two"):
        house.say_input(house.conversation_input(
            "Morgen nach Sonnenuntergang noch warm.",
            conversation_id=conversation_id,
        ))

    first = house.say_input(house.conversation_input(
        "Im Wohnzimmer.", conversation_id="one"
    ))
    second = house.say_input(house.conversation_input(
        "Im Schlafzimmer.", conversation_id="two"
    ))
    assert "Temperatur" in first.speech and "Temperatur" in second.speech
    assert house.automations() == []


def test_unknown_meaningful_addition_never_reduces_to_write_plan(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    house.say("Morgen um acht das Licht mit Quantennebel.")
    answer = house.say("Im Flur einschalten.")

    assert "Soll diese Automation erstellt werden?" not in answer.speech
    assert "quantennebel" in answer.speech.casefold()
    assert house.automations() == [] and answer.calls == []


def test_generic_device_class_still_requires_a_concrete_target(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    house.say("Morgen um acht das Licht.")

    answer = house.say("Einschalten.")

    assert "Soll diese Automation erstellt werden?" not in answer.speech
    assert any(word in answer.speech for word in ("Gerät", "Raum", "Licht"))
    assert house.automations() == [] and answer.calls == []


def test_ambiguous_room_target_is_selected_only_inside_offered_entities(
    monkeypatch, tmp_path
):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    living = next(
        entity for entity in house.entities
        if entity.entity_id == "climate.heizung_wohnzimmer"
    )
    house.entities[house.entities.index(living)] = replace(
        living, friendly_name="Wohnzimmerheizung links", aliases=("linke",)
    )
    house.entities.append(replace(
        living,
        entity_id="climate.heizung_wohnzimmer_rechts",
        friendly_name="Wohnzimmerheizung rechts",
        aliases=("rechte",),
    ))

    house.say("Morgen nach Sonnenuntergang noch warm.")
    choice = house.say("Im Wohnzimmer.")
    assert "links" in choice.speech and "rechts" in choice.speech
    assert house.automations() == []

    value_question = house.say("Die linke.")
    assert "Temperatur" in value_question.speech
    preview = house.say("Stell die Heizung auf 21 Grad.")
    assert "Soll diese Automation erstellt werden?" in preview.speech
    assert house.automations() == []
    house.say("Ja.")
    [created] = house.automations()
    assert created["actions"][0]["target"]["entity_id"] == "climate.heizung_wohnzimmer"


def test_natural_time_correction_replaces_only_preview_schedule(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    house.say("Morgen um acht das Licht.")
    first_preview = house.say("Im Flur einschalten.")
    assert "08:00" in first_preview.speech

    corrected = house.say("Nein, lieber um neun Uhr.")

    assert "09:00" in corrected.speech
    assert "Soll diese Automation erstellt werden?" in corrected.speech
    assert house.automations() == [] and corrected.calls == []


def test_sun_offset_survives_slot_filling_and_confirmation(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    question = house.say("Morgen 20 Minuten vor Sonnenuntergang das Licht.")
    assert "Licht" in question.speech

    preview = house.say("Im Flur einschalten.")
    assert "20 Minuten" in preview.speech and "Sonnenuntergang" in preview.speech
    assert house.automations() == []
    house.say("Ja.")
    [created] = house.automations()
    assert created["triggers"][0]["event"] == "sunset"
    assert created["triggers"][0]["offset"] == "-00:20:00"


def test_slot_provenance_uses_one_turn_number_per_utterance(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    opening = house.conversation_input(
        "Morgen nach Sonnenuntergang noch warm.", conversation_id="provenance"
    )
    house.say_input(opening)
    house.say_input(house.conversation_input(
        "Im Wohnzimmer.", conversation_id="provenance"
    ))
    scope = conversation_scope_key_for_input(opening)
    context = house.entity._context_store.get(scope)

    assert context is not None and context.pending_automation_slot_draft is not None
    draft = context.pending_automation_slot_draft
    assert draft.turn_count == 2
    assert {item.turn for item in draft.provenance} <= {1, 2}
    assert any(item.slot == "target" and item.turn == 2 for item in draft.provenance)


def test_restart_does_not_persist_or_revive_open_slot_draft(monkeypatch, tmp_path):
    first = HouseConversation(monkeypatch, tmp_path=tmp_path)
    first.say("Morgen nach Sonnenuntergang noch warm.")
    assert first.automations() == []

    restarted = HouseConversation(monkeypatch, tmp_path=tmp_path)
    answer = restarted.say("Ja.")

    assert restarted.automations() == []
    assert answer.calls == []
