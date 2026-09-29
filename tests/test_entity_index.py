"""Unit tests for entities.build_entity_index (v2 plan Phase 23,
"Performance/Indexing") - hass-free, no engine/hassil involved.

The second half of this file (World Model Wave 1) covers
``resolve_phrase``'s optional ``index`` parameter: the candidate
prefilter built on top of ``EntityIndex``. Each test asserts the indexed
path returns the *exact same* ``ResolutionResult`` as the index-less full
scan for the same inputs - scoring equivalence, not just "still passes"."""

from __future__ import annotations

from homeintent.entities import (
    EntitySnapshot,
    ResolutionStatus,
    build_entity_index,
)
from homeintent.nlu.target_resolution import resolve_phrase
from homeintent.nlu.semantic_location import resolve_semantic_location
from homeintent.world_model import build_world_model

LIGHT_WOHNZIMMER = EntitySnapshot(
    "light.wohnzimmer", "Wohnzimmerlicht", "light", "on", area_id="wohnzimmer", area_name="Wohnzimmer",
)
LIGHT_KUECHE = EntitySnapshot(
    "light.kueche", "Küchenlicht", "light", "on", area_id="kueche", area_name="Küche",
    aliases=("Deckenlicht",),
)
SWITCH_NO_AREA = EntitySnapshot("switch.steckdose", "Steckdose", "switch", "off")


def test_by_domain_groups_all_entities_of_that_domain():
    index = build_entity_index([LIGHT_WOHNZIMMER, LIGHT_KUECHE, SWITCH_NO_AREA])
    assert sorted(e.entity_id for e in index.by_domain["light"]) == ["light.kueche", "light.wohnzimmer"]
    assert index.by_domain["switch"] == (SWITCH_NO_AREA,)
    assert "cover" not in index.by_domain


def test_by_area_excludes_entities_without_area_id():
    index = build_entity_index([LIGHT_WOHNZIMMER, LIGHT_KUECHE, SWITCH_NO_AREA])
    assert index.by_area["wohnzimmer"] == (LIGHT_WOHNZIMMER,)
    assert index.by_area["kueche"] == (LIGHT_KUECHE,)
    assert sum(len(v) for v in index.by_area.values()) == 2


def test_by_normalized_name_is_case_and_whitespace_insensitive():
    index = build_entity_index([LIGHT_WOHNZIMMER])
    assert index.by_normalized_name["wohnzimmerlicht"] == (LIGHT_WOHNZIMMER,)
    assert "  Wohnzimmerlicht  " not in index.by_normalized_name


def test_by_normalized_alias_covers_entity_id_and_configured_alias():
    index = build_entity_index([LIGHT_KUECHE])
    assert LIGHT_KUECHE in index.by_normalized_alias["light.kueche"]
    assert LIGHT_KUECHE in index.by_normalized_alias["deckenlicht"]
    # Humanized entity_id form ("Kueche" from "light.kueche").
    assert LIGHT_KUECHE in index.by_normalized_alias["kueche"]


def test_name_token_index_covers_names_and_generated_aliases():
    upstairs = EntitySnapshot(
        "light.badlicht_oben", "Badlicht oben", "light", "off",
        floor_id="og", floor_name="Obergeschoss", floor_level=1,
    )
    index = build_entity_index([upstairs])

    assert index.by_normalized_name_token["oben"] == (upstairs,)
    assert index.by_normalized_name_token["badlicht"] == (upstairs,)


def test_indexed_level_cue_still_distinguishes_entity_name_from_floor():
    downstairs = EntitySnapshot(
        "light.kueche", "Küchenlicht", "light", "off",
        floor_id="eg", floor_name="Erdgeschoss", floor_level=0,
    )
    upstairs = EntitySnapshot(
        "light.badlicht_oben", "Badlicht oben", "light", "off",
        floor_id="og", floor_name="Obergeschoss", floor_level=1,
    )
    entities = [downstairs, upstairs]
    world = build_world_model(entities, [])

    assert resolve_semantic_location("Ist Badlicht oben an?", entities, world) is None
    assert resolve_semantic_location("Welche davon sind oben?", entities, world) == (
        "oben", None, "og",
    )


def test_empty_entities_yields_empty_index():
    index = build_entity_index([])
    assert index.by_domain == {}
    assert index.by_area == {}
    assert index.by_normalized_name == {}
    assert index.by_normalized_alias == {}
    assert index.by_normalized_name_token == {}


# --- resolve_phrase(index=...) - candidate prefilter (World Model Wave 1) ---


def test_resolve_phrase_without_index_is_unaffected_by_the_new_parameter():
    """Backward compatibility: omitting ``index`` (the default) must behave
    exactly like before the parameter existed."""
    entities = [
        EntitySnapshot("light.a", "Lampe", "light", "off", area_id="wohnzimmer"),
        EntitySnapshot("light.b", "Lampe", "light", "off", area_id="buro"),
    ]
    result = resolve_phrase("Lampe", entities, area_id="wohnzimmer")
    assert result.status is ResolutionStatus.RESOLVED
    assert result.entity.entity_id == "light.a"
    assert result.score == 130


def test_index_area_prefilter_matches_full_scan_result():
    """Mirrors test_resolver.py::test_area_bonus_breaks_a_tie: with an area
    hint the matching-area candidate wins outright, same score, whether or
    not an index is supplied."""
    entities = [
        EntitySnapshot("light.a", "Lampe", "light", "off", area_id="wohnzimmer"),
        EntitySnapshot("light.b", "Lampe", "light", "off", area_id="buro"),
    ]
    index = build_entity_index(entities)

    without_index = resolve_phrase("Lampe", entities, area_id="wohnzimmer")
    with_index = resolve_phrase("Lampe", entities, area_id="wohnzimmer", index=index)

    assert with_index == without_index
    assert with_index.status is ResolutionStatus.RESOLVED
    assert with_index.entity.entity_id == "light.a"
    assert with_index.score == 130


def test_index_domain_prefilter_matches_full_scan_result():
    """Mirrors test_resolver.py::test_domain_and_device_class_bonus_add_up."""
    entities = [EntitySnapshot("sensor.a", "Temperatur", "sensor", "18", device_class="temperature")]
    index = build_entity_index(entities)

    without_index = resolve_phrase("Temperatur", entities, domain="sensor", device_class="temperature")
    with_index = resolve_phrase(
        "Temperatur", entities, domain="sensor", device_class="temperature", index=index
    )

    assert with_index == without_index
    assert with_index.status is ResolutionStatus.RESOLVED
    assert with_index.score == 100 + 20 + 15


def test_index_domain_and_area_prefilter_matches_full_scan_result():
    """A realistic scenario where domain+area prefiltering genuinely shrinks
    the candidate set (3 entities -> 1) while still landing on the same
    winner and score the full scan would have picked - two same-named
    lights in different areas, plus an unrelated switch."""
    light_wohnzimmer = EntitySnapshot(
        "light.wohnzimmer_decke", "Deckenlicht", "light", "off", area_id="wohnzimmer",
    )
    light_kueche = EntitySnapshot(
        "light.kueche_decke", "Deckenlicht", "light", "off", area_id="kueche",
    )
    switch_wohnzimmer = EntitySnapshot(
        "switch.wohnzimmer_steckdose", "Steckdose", "switch", "off", area_id="wohnzimmer",
    )
    entities = [light_wohnzimmer, light_kueche, switch_wohnzimmer]
    index = build_entity_index(entities)

    without_index = resolve_phrase("Deckenlicht", entities, domain="light", area_id="wohnzimmer")
    with_index = resolve_phrase(
        "Deckenlicht", entities, domain="light", area_id="wohnzimmer", index=index
    )

    assert with_index == without_index
    assert with_index.status is ResolutionStatus.RESOLVED
    assert with_index.entity.entity_id == "light.wohnzimmer_decke"
    assert with_index.score == 100 + 30 + 20


def test_index_without_domain_or_area_does_not_change_ambiguity():
    """Mirrors test_resolver.py::test_near_tied_scores_are_ambiguous_not_guessed:
    with neither ``domain`` nor ``area_id`` given, an index must not narrow
    the candidate set at all - the prefilter only ever triggers off those
    two hints."""
    entities = [
        EntitySnapshot("light.a", "Buntes Licht", "light", "off"),
        EntitySnapshot("light.b", "Helles Licht", "light", "off"),
    ]
    index = build_entity_index(entities)

    without_index = resolve_phrase("Licht", entities)
    with_index = resolve_phrase("Licht", entities, index=index)

    assert with_index == without_index
    assert with_index.status is ResolutionStatus.AMBIGUOUS
    assert len(with_index.candidates) == 2


def test_index_exact_tier_matches_full_scan_without_scope():
    """The fast exact-name path preserves duplicate-name ambiguity."""
    entities = [
        EntitySnapshot("light.a", "Stehlampe", "light", "off"),
        EntitySnapshot("light.b", "Stehlampe", "light", "off"),
        EntitySnapshot("light.c", "Stehlampe Ecke", "light", "off"),
    ]
    index = build_entity_index(entities)

    without_index = resolve_phrase("Stehlampe", entities)
    with_index = resolve_phrase("Stehlampe", entities, index=index)

    assert with_index == without_index
    assert with_index.status is ResolutionStatus.AMBIGUOUS
    assert {item.entity_id for item in with_index.candidates} == {
        "light.a",
        "light.b",
    }


def test_index_domain_prefilter_can_diverge_from_full_scan_by_design():
    """Documents the caveat spelled out in resolve_phrase's docstring:
    ``domain``/``area_id`` are additive *bonuses* in the index-less path (an
    entity outside the domain can still win on name match alone), but a
    *hard filter* once an ``index`` is supplied. This is the one case where
    the two paths intentionally disagree - captured here as a named,
    understood trade-off rather than a silent surprise."""
    entities = [EntitySnapshot("light.a", "Lampe", "light", "off")]
    index = build_entity_index(entities)

    # Index-less: no entity is in domain "switch", but "Lampe" still wins on
    # an exact name match alone (domain is only a bonus, never a gate here).
    without_index = resolve_phrase("Lampe", entities, domain="switch")
    assert without_index.status is ResolutionStatus.RESOLVED
    assert without_index.entity.entity_id == "light.a"
    assert without_index.score == 100

    # Indexed: the domain prefilter drops light.a before scoring even runs,
    # since it isn't in index.by_domain["switch"] - NOT_FOUND instead.
    with_index = resolve_phrase("Lampe", entities, domain="switch", index=index)
    assert with_index.status is ResolutionStatus.NOT_FOUND
