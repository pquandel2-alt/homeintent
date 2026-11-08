"""Independent v7.13.3 review: real conversation code, HA service doubles only."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
import _ha_stub

_ha_stub.install()

from homeassistant.exceptions import HomeAssistantError
from _testhaus import HouseConversation
from test_clause_binding_7132 import source_entities, RADIO, SPEAKER, TV
from homeintent.nlu.clause_binding import analyse_clause_bindings
from homeintent.nlu.language_frontend import analyse_language
from homeintent.service_call import ServiceCallPlan

JOINERS = ["und", "und dann", ",", ";", ".", "danach", "sowie", "anschließend", "außerdem"]


def house(monkeypatch, entities=None):
    return HouseConversation(
        monkeypatch, entities=entities if entities is not None else source_entities(),
        options={"effect_wait_seconds": 0.01},
    )


def both_sources():
    return [
        replace(e, attributes={**e.attributes, "source_list": ["WDR 2"]})
        if e.entity_id == SPEAKER else e
        for e in source_entities()
    ]


@pytest.mark.parametrize("joiner", JOINERS)
def test_known_source_and_pause_have_exact_targets_and_order(monkeypatch, joiner):
    h = house(monkeypatch)
    t = h.say(f"Spiel WDR 2 in der Küche {joiner} pausiere die Musik im Schlafzimmer.")
    assert t.calls == [
        ("media_player", "select_source", {"source": "WDR 2", "entity_id": RADIO}),
        ("media_player", "media_pause", {"entity_id": SPEAKER}),
    ]


@pytest.mark.parametrize("joiner", JOINERS)
def test_unknown_source_refuses_whole_turn_and_later_yes(monkeypatch, joiner):
    h = house(monkeypatch)
    t = h.say(f"Spiel Quirlomat in der Küche {joiner} pausiere die Musik im Schlafzimmer.")
    assert t.calls == []
    assert "Planvorschau" not in t.speech
    assert h.say("Ja").calls == []


@pytest.mark.parametrize("reply", [
    "Nicht in der Küche.",
    "In der Küche, aber nicht jetzt.",
    "In der Küche, aber erst in zehn Minuten.",
    "In der Küche und Quirlomat.",
])
def test_player_selection_does_not_ignore_negative_temporal_or_unknown_content(monkeypatch, reply):
    h = house(monkeypatch)
    assert h.say("Spiel WDR 2.").calls == []
    t = h.say(reply)
    assert t.calls == [], (t.speech, t.calls)


@pytest.mark.parametrize("reply", [
    "Doch im Schlafzimmer, aber nicht jetzt.",
    "Doch im Schlafzimmer, aber erst in zehn Minuten.",
    "Doch im Schlafzimmer und Quirlomat.",
])
def test_location_correction_validates_the_complete_amendment(monkeypatch, reply):
    h = house(monkeypatch, both_sources())
    assert h.say("Spiel WDR 2 in der Küche.").targets == {RADIO}
    t = h.say(reply)
    assert t.calls == [], (t.speech, t.calls)


@pytest.mark.parametrize("unavailable_id,tail", [
    (SPEAKER, "pausiere die Musik im Schlafzimmer"),
    ("light.buerolicht", "mach das Bürolicht aus"),
])
def test_all_clauses_preflight_availability_before_the_first_write(monkeypatch, unavailable_id, tail):
    entities = [replace(e, state="unavailable") if e.entity_id == unavailable_id else e for e in source_entities()]
    h = house(monkeypatch, entities)
    t = h.say(f"Spiel WDR 2 in der Küche und {tail}.")
    assert t.calls == [], (t.speech, t.calls)


def inject_one_lamp_failure(h):
    original = h.entity.hass.services.async_call.side_effect

    async def effect(domain, service, data=None, **kwargs):
        if (data or {}).get("entity_id") == "light.deckenfluter_buero":
            raise HomeAssistantError("Injected lamp failure")
        return await original(domain, service, data, **kwargs)

    h.entity.hass.services.async_call.side_effect = effect


def test_failed_batch_member_is_reported_and_stops_later_clause(monkeypatch):
    h = house(monkeypatch)
    inject_one_lamp_failure(h)
    t = h.say("Spiel WDR 2 in der Küche und mach das Licht im Büro aus und pausiere die Musik im Schlafzimmer.")
    assert not any(service == "media_pause" for _, service, _ in t.calls), (t.speech, t.calls)
    assert "3 Lichter ausgeschaltet" not in t.speech


def test_failed_first_playback_is_not_remembered_as_started(monkeypatch):
    h = house(monkeypatch, both_sources())
    h.entity.hass.services.async_call.side_effect = HomeAssistantError("Injected player failure")
    first = h.say("Spiel WDR 2 in der Küche.")
    assert "Fehler" in first.speech
    h.entity.hass.services.async_call.side_effect = None
    corrected = h.say("Doch im Schlafzimmer.")
    assert "erste Wiedergabe war bereits gestartet" not in corrected.speech


def test_clause_boundary_independently_rejects_wrong_room_plan():
    entities = source_entities()
    document = analyse_language("Spiel WDR 2 in der Küche und pausiere die Musik im Schlafzimmer.", entities)
    payload = SimpleNamespace(commands=(
        SimpleNamespace(plan=ServiceCallPlan("media_player", "select_source", RADIO, {"source": "WDR 2"})),
        SimpleNamespace(plan=ServiceCallPlan("media_player", "media_pause", TV)),
    ))
    binding = analyse_clause_bindings(document, entities, payload)
    assert not binding.complete, binding


def test_exact_music_assistant_source_compound_uses_existing_contract(monkeypatch):
    from test_music_assistant_710 import _response
    entities = [
        replace(e, integration="music_assistant", config_entry_id="mass-entry")
        if e.entity_id == RADIO else e for e in source_entities()
    ]
    h = house(monkeypatch, entities)
    h.entity.hass.services.async_register("music_assistant", "search", lambda call: None)
    h.entity.hass.services.async_register("music_assistant", "play_media", lambda call: None)
    h.entity.hass.services.async_call.return_value = _response(
        playlists=[{"name": "Entspannen", "uri": "library://playlist/7"}],
    )
    t = h.say("Spiel meine Playlist Entspannen in der Küche und pausiere die Musik im Schlafzimmer.")
    writes = [(domain, service, data) for domain, service, data in t.calls if service != "search"]
    assert writes == [
        ("music_assistant", "play_media", {
            "media_id": "library://playlist/7", "media_type": "playlist", "enqueue": "replace", "entity_id": RADIO,
        }),
        ("media_player", "media_pause", {"entity_id": SPEAKER}),
    ], (t.speech, t.calls)
