"""Scope rules from the 7.1.2 live test (F20, F21 and the garage query).

- F20: "das Licht im <Raum>" switches every light of a room with several
  lights; a named device or a single light keeps its own meaning.
- F21: "im Haus", "überall", "in allen Räumen" are whole-home scopes. The
  word "Haus" must not select a device such as "Stromverbrauch Haus".
- A singular question naming a device ("Ist das Garagentor offen?") answers
  for that device even if the class word maps to another domain.
"""

from __future__ import annotations

import pytest

from homeintent.entities import EntitySnapshot

_LIGHT = frozenset({"TURN_ON", "TURN_OFF"})


def _light(entity_id: str, name: str, area_id: str, area_name: str) -> EntitySnapshot:
    return EntitySnapshot(
        entity_id, name, "light", "on",
        area_id=area_id, area_name=area_name, capabilities=_LIGHT,
    )


HOUSE = [
    _light("light.bad_decke", "Badezimmerlicht", "bad", "Bad"),
    _light("light.spiegelschrank", "Spiegelschrank", "bad", "Bad"),
    _light("light.kuechenlicht", "Küchenlicht", "kueche", "Küche"),
    _light("light.stehlampe", "Stehlampe", "wohnzimmer", "Wohnzimmer"),
    EntitySnapshot(
        "sensor.stromverbrauch_haus", "Stromverbrauch Haus", "sensor", "1834.2",
        unit="kWh", device_class="energy",
    ),
    EntitySnapshot(
        "cover.garagentor", "Garagentor", "cover", "open",
        area_id="garage", area_name="Garage", device_class="garage",
        capabilities=frozenset({"OPEN", "CLOSE"}),
    ),
]
ALL_LIGHTS = {entity.entity_id for entity in HOUSE if entity.domain == "light"}


def _targets(result) -> set[str]:
    entity_id = result.plan.entity_id
    return set(entity_id) if isinstance(entity_id, list) else {entity_id}


def test_f20_room_light_means_every_light_of_a_multi_light_room(engine):
    result = engine.match("Schalte das Licht im Bad aus", HOUSE)
    assert result is not None and result.plan is not None
    assert _targets(result) == {"light.bad_decke", "light.spiegelschrank"}


def test_f20_single_light_room_and_named_device_keep_their_meaning(engine):
    single = engine.match("Schalte das Licht in der Küche aus", HOUSE)
    assert single is not None and _targets(single) == {"light.kuechenlicht"}
    named = engine.match("Schalte den Spiegelschrank im Bad aus", HOUSE)
    assert named is not None and _targets(named) == {"light.spiegelschrank"}


@pytest.mark.parametrize(
    "sentence",
    [
        "Schalte alle Lichter im Haus aus",
        "Mach überall das Licht aus",
        "Schalte im ganzen Haus das Licht aus",
        "Mach in allen Räumen das Licht aus",
    ],
)
def test_f21_whole_home_scope_switches_every_light(engine, sentence):
    result = engine.match(sentence, HOUSE)
    assert result is not None and result.plan is not None, sentence
    assert result.plan.service == "turn_off"
    assert _targets(result) == ALL_LIGHTS


def test_f21_named_device_inside_whole_home_scope_is_not_widened(engine):
    result = engine.match("Schalte im ganzen Haus die Stehlampe aus", HOUSE)
    assert result is not None and _targets(result) == {"light.stehlampe"}


def test_f21_real_area_called_haus_keeps_its_meaning(engine):
    house = [
        _light("light.gartenhaus", "Gartenhauslicht", "haus", "Haus"),
        _light("light.flur", "Flurlicht", "flur", "Flur"),
    ]
    result = engine.match("Schalte alle Lichter im Haus aus", house)
    assert result is not None and _targets(result) == {"light.gartenhaus"}


def test_singular_named_query_answers_for_the_named_device(engine):
    result = engine.match("Ist das Garagentor offen?", HOUSE)
    assert result is not None and result.plan is None
    assert result.response_text.startswith("Ja")
    assert "Garagentor" in result.response_text


def test_f21_whole_home_measurement_excludes_outdoor_sensors(engine):
    def _temperature(entity_id: str, name: str, state: str, area: str) -> EntitySnapshot:
        return EntitySnapshot(
            entity_id, name, "sensor", state, area_id=area.casefold(), area_name=area,
            unit="°C", device_class="temperature",
        )

    house = [
        _temperature("sensor.t_wohnzimmer", "Temperatur Wohnzimmer", "21", "Wohnzimmer"),
        _temperature("sensor.t_schlafzimmer", "Temperatur Schlafzimmer", "18", "Schlafzimmer"),
        _temperature("sensor.aussentemperatur", "Außentemperatur", "6", "Garten"),
    ]
    result = engine.match("Wie hoch ist die durchschnittliche Temperatur im Haus?", house)
    assert result is not None and result.response_text is not None
    assert "19,5" in result.response_text
