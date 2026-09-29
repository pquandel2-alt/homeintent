"""Unit tests for floors.resolve_floor_name/resolve_floor_by_level_keyword -
hass-free, no engine/hassil involved. Mirrors test_area_resolution.py."""

from __future__ import annotations

from homeintent.entities import EntitySnapshot
from homeintent.floors import FloorResolveStatus, resolve_floor_by_level_keyword, resolve_floor_name

TWO_FLOOR_HOUSE = [
    EntitySnapshot(
        "light.wohnzimmer", "Wohnzimmerlicht", "light", "off",
        area_id="wohnzimmer", area_name="Wohnzimmer",
        floor_id="erdgeschoss", floor_name="Erdgeschoss", floor_level=0,
    ),
    EntitySnapshot(
        "light.kueche", "Küchenlicht", "light", "off",
        area_id="kueche", area_name="Küche",
        floor_id="erdgeschoss", floor_name="Erdgeschoss", floor_level=0,
    ),
    EntitySnapshot(
        "light.buero", "Bürolicht", "light", "off",
        area_id="buero", area_name="Büro",
        floor_id="obergeschoss", floor_name="Obergeschoss", floor_level=1,
    ),
    EntitySnapshot(
        "light.schlafzimmer", "Schlafzimmerlicht", "light", "off",
        area_id="schlafzimmer", area_name="Schlafzimmer",
        floor_id="obergeschoss", floor_name="Obergeschoss", floor_level=1,
    ),
]


def test_exact_match():
    result = resolve_floor_name("Obergeschoss", TWO_FLOOR_HOUSE)
    assert result.status is FloorResolveStatus.OK
    assert result.floor_id == "obergeschoss"


def test_exact_match_case_insensitive():
    result = resolve_floor_name("erdgeschoss", TWO_FLOOR_HOUSE)
    assert result.status is FloorResolveStatus.OK
    assert result.floor_id == "erdgeschoss"


def test_unambiguous_contains_match():
    result = resolve_floor_name("im Erdgeschoss", TWO_FLOOR_HOUSE)
    assert result.status is FloorResolveStatus.OK
    assert result.floor_id == "erdgeschoss"


def test_not_found():
    result = resolve_floor_name("Keller", TWO_FLOOR_HOUSE)
    assert result.status is FloorResolveStatus.NOT_FOUND


def test_empty_name_not_found():
    result = resolve_floor_name("", TWO_FLOOR_HOUSE)
    assert result.status is FloorResolveStatus.NOT_FOUND


def test_entities_without_floor_are_ignored():
    entities = [EntitySnapshot("light.a", "Licht", "light", "off")]
    result = resolve_floor_name("Erdgeschoss", entities)
    assert result.status is FloorResolveStatus.NOT_FOUND


def test_ambiguous_floor_name():
    entities = [
        EntitySnapshot("light.a", "A", "light", "off", floor_id="og_west", floor_name="Obergeschoss", floor_level=1),
        EntitySnapshot("light.b", "B", "light", "off", floor_id="og_ost", floor_name="Obergeschoss", floor_level=1),
    ]
    result = resolve_floor_name("Obergeschoss", entities)
    assert result.status is FloorResolveStatus.AMBIGUOUS
    assert len(result.candidates) == 2


def test_level_keyword_up_resolves_highest_level():
    result = resolve_floor_by_level_keyword("up", TWO_FLOOR_HOUSE)
    assert result.status is FloorResolveStatus.OK
    assert result.floor_id == "obergeschoss"


def test_level_keyword_down_resolves_lowest_level():
    result = resolve_floor_by_level_keyword("down", TWO_FLOOR_HOUSE)
    assert result.status is FloorResolveStatus.OK
    assert result.floor_id == "erdgeschoss"


def test_level_keyword_without_any_floor_data_not_found():
    entities = [EntitySnapshot("light.a", "Licht", "light", "off")]
    result = resolve_floor_by_level_keyword("up", entities)
    assert result.status is FloorResolveStatus.NOT_FOUND


def test_level_keyword_single_floor_has_no_upper_floor():
    # 7.6.1: one rule for "oben"/"unten" (place_model.level_for_keyword):
    # "oben" is a level >= 1, "unten" the ground floor. A one-storey house
    # has no "oben"; it is never widened to the only floor.
    entities = [
        EntitySnapshot("light.a", "A", "light", "off", floor_id="eg", floor_name="Erdgeschoss", floor_level=0),
    ]
    assert resolve_floor_by_level_keyword("up", entities).status is FloorResolveStatus.NOT_FOUND
    result = resolve_floor_by_level_keyword("down", entities)
    assert result.status is FloorResolveStatus.OK
    assert result.floor_id == "eg"


def test_level_keyword_tie_is_ambiguous():
    entities = [
        EntitySnapshot("light.a", "A", "light", "off", floor_id="og_west", floor_name="Obergeschoss West", floor_level=1),
        EntitySnapshot("light.b", "B", "light", "off", floor_id="og_ost", floor_name="Obergeschoss Ost", floor_level=1),
    ]
    result = resolve_floor_by_level_keyword("up", entities)
    assert result.status is FloorResolveStatus.AMBIGUOUS
    assert len(result.candidates) == 2


def test_level_keyword_ignores_floors_missing_level():
    entities = [
        EntitySnapshot("light.a", "A", "light", "off", floor_id="eg", floor_name="Erdgeschoss", floor_level=0),
        EntitySnapshot("light.b", "B", "light", "off", floor_id="unbekannt", floor_name="Unbekannt", floor_level=None),
    ]
    result = resolve_floor_by_level_keyword("down", entities)
    assert result.status is FloorResolveStatus.OK
    assert result.floor_id == "eg"
