"""F12: read-only questions from the live test house, through conversation.py."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import pytest  # noqa: E402

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.nlu.capabilities import derive_capabilities  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402

FLOORS = {
    "Wohnzimmer": ("erdgeschoss", "Erdgeschoss", 0), "Küche": ("erdgeschoss", "Erdgeschoss", 0),
    "Esszimmer": ("erdgeschoss", "Erdgeschoss", 0), "Flur": ("erdgeschoss", "Erdgeschoss", 0),
    "Büro": ("erdgeschoss", "Erdgeschoss", 0), "Badezimmer": ("obergeschoss", "Obergeschoss", 1),
    "Schlafzimmer": ("obergeschoss", "Obergeschoss", 1), "Garten": ("aussen", "Außenbereich", None),
    "Hauswirtschaftsraum": ("keller", "Keller", -1),
}


def _entity(entity_id, name, state, area=None, **attributes):
    domain = entity_id.split(".", 1)[0]
    attributes = {"friendly_name": name, **attributes}
    floor = FLOORS.get(area or "")
    return EntitySnapshot(
        entity_id, name, domain, state,
        area_id=area.casefold() if area else None, area_name=area,
        floor_id=floor[0] if floor else None, floor_name=floor[1] if floor else None,
        floor_level=floor[2] if floor else None,
        unit=attributes.get("unit_of_measurement"),
        device_class=attributes.get("device_class"), attributes=attributes,
        capabilities=frozenset(
            c.name for c in derive_capabilities(domain, attributes.get("device_class"), attributes)
        ),
    )


def _house(persons: bool = True) -> list[EntitySnapshot]:
    house = [
        _entity("light.badezimmerlicht", "Badezimmerlicht", "on", "Badezimmer"),
        _entity("switch.heizluefter_bad", "Heizlüfter", "on", "Badezimmer"),
        _entity("light.spiegelschrank", "Spiegelschrank", "off", "Badezimmer"),
        _entity("light.kuechenlicht", "Küchenlicht", "on", "Küche"),
        _entity("light.flurlicht", "Flurlicht", "on", "Flur"),
        _entity("light.buerolicht", "Bürolicht", "on", "Büro"),
        _entity("light.schlafzimmerlicht", "Schlafzimmerlicht", "on", "Schlafzimmer"),
        _entity("cover.kuechenrollladen", "Küchenrollladen", "open", "Küche", device_class="shutter", current_position=100),
        _entity("cover.esszimmer_rollladen", "Esszimmer Rollladen", "open", "Esszimmer", device_class="shutter", current_position=100),
        _entity("sensor.temperatur_wohnzimmer", "Temperatur Wohnzimmer", "20.64", "Wohnzimmer", device_class="temperature", unit_of_measurement="°C"),
        _entity("sensor.temperatur_schlafzimmer", "Temperatur Schlafzimmer", "18.1", "Schlafzimmer", device_class="temperature", unit_of_measurement="°C"),
        _entity("sensor.aussentemperatur", "Außentemperatur", "12.3", "Garten", device_class="temperature", unit_of_measurement="°C"),
        _entity("sensor.co2_wohnzimmer", "CO2 Wohnzimmer", "812", "Wohnzimmer", device_class="carbon_dioxide", unit_of_measurement="ppm"),
        _entity("sensor.stromverbrauch_haus", "Stromverbrauch Haus", "432", None, device_class="power", unit_of_measurement="W"),
        _entity("sensor.leistung_waschmaschine", "Leistung Waschmaschine", "1840", "Hauswirtschaftsraum", device_class="power", unit_of_measurement="W"),
        _entity("sensor.energiezaehler", "Energiezähler", "18234.7", "Hauswirtschaftsraum", device_class="energy", unit_of_measurement="kWh"),
        _entity(
            "climate.heizung_buero", "Heizung Büro", "heat", "Büro", hvac_modes=["heat", "off"],
            temperature=20.5, current_temperature=19.4, hvac_action="heating", supported_features=401,
        ),
        _entity(
            "climate.heizung_wohnzimmer", "Heizung Wohnzimmer", "heat", "Wohnzimmer", hvac_modes=["heat", "off"],
            temperature=21.0, current_temperature=20.6, hvac_action="idle", supported_features=401,
        ),
    ]
    if persons:
        house += [
            _entity("person.philipp", "Philipp", "home"),
            _entity("person.anna", "Anna", "home"),
        ]
    return house


def _agent(monkeypatch, house):
    agent = NluConversationEntity(ConfigEntry())
    agent.hass = HomeAssistant()
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda hass, entry: house)
    return agent


def _say(agent, text, cid="queries"):
    return asyncio.run(agent._async_handle_message(
        ConversationInput(text=text, conversation_id=cid), chat_log=None
    )).response.speech


@pytest.mark.parametrize(
    ("question", "expected", "forbidden"),
    [
        ("Was ist im Badezimmer eingeschaltet?", ("Badezimmerlicht", "Heizlüfter"), ("Spiegelschrank",)),
        ("Welche Rollläden gibt es im Erdgeschoss?", ("Küchenrollladen", "Esszimmer Rollladen"), ("Ja,",)),
        ("Welches Gerät verbraucht gerade am meisten Strom?", ("Waschmaschine", "1840 Watt"), ("Energiezähler",)),
        ("Wie hoch ist der CO2-Wert im Wohnzimmer?", ("812 ppm",), ("Grad", "unknown")),
        ("Wie warm ist es draußen?", ("12,3 Grad",), ("Wetter-Entität",)),
        ("Wie viel Strom verbraucht das Haus gerade?", ("432 Watt",), ()),
        ("Wie hoch ist die durchschnittliche Temperatur im Haus?", ("19,4 Grad",), ()),
        ("Auf wie viel Grad ist die Heizung im Wohnzimmer gestellt?", ("21 Grad",), ()),
        ("Heizt die Heizung im Büro gerade?", ("Ja", "heizt"), ("heat",)),
        ("Wie warm ist es im Wohnzimmer?", ("20,6 Grad",), ()),
    ],
)
def test_live_house_question(monkeypatch, question, expected, forbidden):
    speech = _say(_agent(monkeypatch, _house()), question, cid=question)
    for text in expected:
        assert text in speech, (question, speech)
    for text in forbidden:
        assert text not in speech, (question, speech)


def test_who_is_home_without_exposed_persons_is_honest(monkeypatch):
    speech = _say(_agent(monkeypatch, _house(persons=False)), "Wer ist zu Hause?")
    assert "niemand" not in speech
    assert "keine Personen freigegeben" in speech


def test_count_then_davon_then_demonstrative_command(monkeypatch):
    agent = _agent(monkeypatch, _house())
    assert "5 Lichter" in _say(agent, "Wie viele Lichter sind an?")
    listed = _say(agent, "Welche davon sind im Erdgeschoss?")
    assert all(name in listed for name in ("Küchenlicht", "Flurlicht", "Bürolicht"))
    assert "Schlafzimmerlicht" not in listed
    _say(agent, "Schalte die aus.")
    call = agent.hass.services.async_call.await_args_list[-1]
    assert sorted(call.args[2]["entity_id"]) == ["light.buerolicht", "light.flurlicht", "light.kuechenlicht"]
