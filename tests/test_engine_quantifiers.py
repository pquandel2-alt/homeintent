"""'alle'/'beide' quantifiers over a whole domain, optionally scoped to a room."""

from __future__ import annotations

import pytest

from homeintent.entities import EntitySnapshot

from conftest import POLERAUM_COVERS


def test_beide_rollladen_im_poleraum_hoch(engine, quantifier_entities):
    # Verbatim regression case from the real-world bug report.
    result = engine.match("Fahre beide Rollladen im Poleraum hoch", quantifier_entities)
    assert result is not None
    assert result.plan.domain == "cover"
    assert result.plan.service == "open_cover"
    assert sorted(result.plan.entity_id) == sorted(e.entity_id for e in POLERAUM_COVERS)


def test_alle_rolladen_hoch(engine, quantifier_entities):
    # Verbatim regression case (note: single-l "Rolladen" misspelling).
    result = engine.match("fahre alle Rolladen hoch", quantifier_entities)
    assert result is not None
    assert result.plan.domain == "cover"
    assert result.plan.service == "open_cover"
    expected = sorted(e.entity_id for e in quantifier_entities if e.domain == "cover")
    assert sorted(result.plan.entity_id) == expected


def test_alle_lichter_aus_whole_house(engine, entities):
    result = engine.match("mach alle Lichter aus", entities)
    assert result is not None
    assert result.plan.domain == "homeassistant"
    assert result.plan.service == "turn_off"
    expected = sorted(e.entity_id for e in entities if e.domain == "light")
    assert sorted(result.plan.entity_id) == expected


def test_dreh_alle_lichter_an(engine, entities):
    # V4.1 "Advanced German Language": "dreh"/"lass" verbs, base quantifier
    # form only (see quantifiers/light_switch_all.yaml comment).
    result = engine.match("dreh alle Lichter an", entities)
    assert result is not None
    assert result.plan.service == "turn_on"
    expected = sorted(e.entity_id for e in entities if e.domain == "light")
    assert sorted(result.plan.entity_id) == expected


def test_lass_alle_lichter_aus_keeps_the_state(engine, entities):
    # 7.7.1 A2: "lass … aus" is maintenance, never an operation.
    assert engine.match("lass alle Lichter aus", entities) is None


def test_alle_lichter_im_buro_room_scoped(engine):
    entities = [
        EntitySnapshot("light.a", "Deckenlicht", "light", "off", area_id="buro", area_name="Büro"),
        EntitySnapshot("light.b", "Stehlampe", "light", "off", area_id="buro", area_name="Büro"),
        EntitySnapshot("light.c", "Küchenlicht", "light", "off", area_id="kueche", area_name="Küche"),
    ]
    result = engine.match("mach alle Lichter im Büro aus", entities)
    assert result is not None
    assert sorted(result.plan.entity_id) == ["light.a", "light.b"]


def test_floor_wins_over_same_named_area_for_cover_group(engine):
    """A group location denotes the floor when floor and area names collide."""
    entities = [
        EntitySnapshot(
            "sensor.erdgeschoss", "Temperatur Erdgeschoss", "sensor", "21.6",
            area_id="erdgeschoss", area_name="Erdgeschoss",
            floor_id="erdgeschoss", floor_name="Erdgeschoss",
        ),
        EntitySnapshot(
            "cover.wohnzimmer", "Rollladen Wohnzimmer", "cover", "closed",
            area_id="wohnzimmer", area_name="Wohnzimmer",
            floor_id="erdgeschoss", floor_name="Erdgeschoss",
        ),
        EntitySnapshot(
            "cover.kueche", "Rollladen Küche", "cover", "closed",
            area_id="kueche", area_name="Küche",
            floor_id="erdgeschoss", floor_name="Erdgeschoss",
        ),
    ]

    result = engine.match("Fahre alle Rolladen im Erdgeschoss hoch", entities)

    assert result is not None
    assert result.plan.service == "open_cover"
    assert sorted(result.plan.entity_id) == ["cover.kueche", "cover.wohnzimmer"]


def test_area_scoped_plural_cover_is_implicit_all(engine):
    entities = [
        EntitySnapshot(
            "cover.links", "Rollladen links", "cover", "closed",
            area_id="esszimmer", area_name="Esszimmer",
        ),
        EntitySnapshot(
            "cover.rechts", "Rollladen rechts", "cover", "closed",
            area_id="esszimmer", area_name="Esszimmer",
        ),
    ]

    result = engine.match("Fahre die Rolladen im Esszimmer hoch", entities)

    assert result is not None
    assert result.plan.service == "open_cover"
    assert sorted(result.plan.entity_id) == ["cover.links", "cover.rechts"]


@pytest.mark.parametrize("noun", ["Rollläden", "Rollladen"])
def test_compound_spoken_area_matches_spaced_registry_name(engine, noun):
    """Natural German compounds remain grounded in the exact HA area."""
    entities = [
        EntitySnapshot(
            "cover.left", "Rolllade Poleraum links", "cover", "open",
            area_id="pole_raum", area_name="Pole Raum",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.right", "Rolllade Poleraum rechts", "cover", "open",
            area_id="pole_raum", area_name="Pole Raum",
            capabilities=frozenset({"POSITION"}),
        ),
    ]

    result = engine.match(
        f"Fahre die {noun} im Poleraum auf 30 Prozent", entities
    )

    assert result is not None
    assert result.plan.service == "set_cover_position"
    assert result.plan.data == {"position": 30}
    assert sorted(result.plan.entity_id) == ["cover.left", "cover.right"]


def test_singular_article_does_not_turn_one_cover_into_area_group(engine):
    entities = [
        EntitySnapshot(
            "cover.left", "Rolllade Poleraum links", "cover", "open",
            area_id="pole_raum", area_name="Pole Raum",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.right", "Rolllade Poleraum rechts", "cover", "open",
            area_id="pole_raum", area_name="Pole Raum",
            capabilities=frozenset({"POSITION"}),
        ),
    ]

    result = engine.match(
        "Fahre den Rollladen im Poleraum auf 30 Prozent", entities
    )

    assert result is None or result.plan is None


def test_area_scoped_plural_lights_are_implicit_all(engine):
    entities = [
        EntitySnapshot(
            "light.decke", "Deckenlicht", "light", "on",
            area_id="esszimmer", area_name="Esszimmer",
        ),
        EntitySnapshot(
            "light.tisch", "Tischlampe", "light", "on",
            area_id="esszimmer", area_name="Esszimmer",
        ),
    ]

    result = engine.match("Mach die Lichter im Esszimmer aus", entities)

    assert result is not None
    assert result.plan.service == "turn_off"
    assert sorted(result.plan.entity_id) == ["light.decke", "light.tisch"]


def test_beide_rejects_single_match_in_room(engine):
    entities = [
        EntitySnapshot("cover.a", "Rolllade", "cover", "closed", area_id="gaeste", area_name="Gästezimmer"),
    ]
    result = engine.match("fahre beide Rollladen im Gästezimmer hoch", entities)
    assert result is None


def test_beide_rejects_triple_match_in_room(engine):
    entities = [
        EntitySnapshot("cover.a", "Rolllade 1", "cover", "closed", area_id="wohnzimmer", area_name="Wohnzimmer"),
        EntitySnapshot("cover.b", "Rolllade 2", "cover", "closed", area_id="wohnzimmer", area_name="Wohnzimmer"),
        EntitySnapshot("cover.c", "Rolllade 3", "cover", "closed", area_id="wohnzimmer", area_name="Wohnzimmer"),
    ]
    result = engine.match("fahre beide Rollladen im Wohnzimmer hoch", entities)
    assert result is None


def test_unknown_room_returns_none(engine, quantifier_entities):
    result = engine.match("mach alle Lichter im Bunker aus", quantifier_entities)
    assert result is None


def test_domain_with_no_matches_in_room_returns_none(engine):
    entities = [
        EntitySnapshot("light.a", "Deckenlicht", "light", "off", area_id="keller", area_name="Keller"),
    ]
    result = engine.match("fahre alle Rollläden im Keller hoch", entities)
    assert result is None


# Phase 29 ("Natural Quantifiers") - plan examples: "die beiden Lampen", "die
# drei Lichter", "alle außer der Küchenlampe", "nur die Wohnzimmerlampen".
# The last one is tested via "nur die Lampen im Wohnzimmer" instead of the
# literal compound word "Wohnzimmerlampen": splitting a German compound noun
# into a room + domain pair is a deliberate, documented scope cut (see
# QuantifierParser's docstring) - "nur" maps to the same "all" quantifier
# value as "alle", so an area-scoped "nur"-sentence exercises the same
# resolution path without needing compound-word parsing.


def test_die_beiden_lampen_both_with_die_prefix(engine):
    entities = [
        EntitySnapshot("light.deckenlicht", "Deckenlicht", "light", "off"),
        EntitySnapshot("light.stehlampe", "Stehlampe", "light", "off"),
    ]
    result = engine.match("mach die beiden Lampen an", entities)
    assert result is not None
    assert sorted(result.plan.entity_id) == ["light.deckenlicht", "light.stehlampe"]


def test_die_drei_lichter_count_quantifier(engine):
    entities = [
        EntitySnapshot("light.deckenlicht", "Deckenlicht", "light", "off"),
        EntitySnapshot("light.stehlampe", "Stehlampe", "light", "off"),
        EntitySnapshot("light.wandlicht", "Wandlicht", "light", "off"),
    ]
    result = engine.match("mach die drei Lichter an", entities)
    assert result is not None
    assert sorted(result.plan.entity_id) == ["light.deckenlicht", "light.stehlampe", "light.wandlicht"]


def test_count_rejects_wrong_number_of_matches(engine):
    entities = [
        EntitySnapshot("light.deckenlicht", "Deckenlicht", "light", "off"),
        EntitySnapshot("light.stehlampe", "Stehlampe", "light", "off"),
    ]
    result = engine.match("mach die drei Lichter an", entities)
    assert result is None


def test_alle_ausser_exclusion(engine):
    entities = [
        EntitySnapshot("light.wohnzimmerlampe", "Wohnzimmerlampe", "light", "off"),
        EntitySnapshot("light.schlafzimmerlampe", "Schlafzimmerlampe", "light", "off"),
        EntitySnapshot("light.kuechenlampe", "Küchenlampe", "light", "off"),
    ]
    result = engine.match("mach alle Lichter an außer der Küchenlampe", entities)
    assert result is not None
    assert sorted(result.plan.entity_id) == ["light.schlafzimmerlampe", "light.wohnzimmerlampe"]


def test_exclusion_target_outside_match_set_returns_none(engine):
    # "Küchenrollladen" resolves, but to a cover, not a light - the
    # domain-filtered match set never contained it, so excluding it must
    # refuse the whole command rather than silently matching everyone.
    entities = [
        EntitySnapshot("light.wohnzimmerlampe", "Wohnzimmerlampe", "light", "off"),
        EntitySnapshot("cover.kuechenrollladen", "Küchenrollladen", "cover", "closed"),
    ]
    result = engine.match("mach alle Lichter an außer der Küchenrollladen", entities)
    assert result is None


def test_exclusion_target_unresolvable_returns_none(engine):
    entities = [
        EntitySnapshot("light.wohnzimmerlampe", "Wohnzimmerlampe", "light", "off"),
        EntitySnapshot("light.schlafzimmerlampe", "Schlafzimmerlampe", "light", "off"),
    ]
    result = engine.match("mach alle Lichter an außer der Küchenlampe", entities)
    assert result is None


def test_nur_quantifier_area_scoped(engine):
    entities = [
        EntitySnapshot("light.decke", "Deckenlicht", "light", "off", area_id="wohnzimmer", area_name="Wohnzimmer"),
        EntitySnapshot("light.steh", "Stehlampe", "light", "off", area_id="wohnzimmer", area_name="Wohnzimmer"),
        EntitySnapshot("light.kueche", "Küchenlicht", "light", "off", area_id="kueche", area_name="Küche"),
    ]
    result = engine.match("mach nur die Lampen im Wohnzimmer an", entities)
    assert result is not None
    assert sorted(result.plan.entity_id) == ["light.decke", "light.steh"]


def test_die_ersten_beiden_returns_none_no_defined_ordering(engine):
    # HomeIntent plan V4.5 ("Advanced Quantifiers", "später"-Teil): "die
    # ersten beiden"/"die letzten zwei" braucht eine Ordnung ueber die
    # Treffermenge ("erste" wovon?). EntitySnapshot traegt kein Ordnungsfeld
    # (keine Reihenfolge-/Positionsangabe) - jede gewaehlte Sortierung
    # (alphabetisch, HA-interne Registrierungsreihenfolge, ...) waere eine
    # erfundene Bedeutung, kein Fakt aus den Daten - genau die Art Raten, die
    # der Plan verbietet. "beiden" ist zwar Teil von _QUANTIFIER_SLOT_LIST,
    # aber "ersten" passt in keine Satzschablone, daher kein Fehltreffer -
    # der Satz bleibt sauber unverstanden statt eine falsche Teilmenge zu
    # waehlen.
    entities = [
        EntitySnapshot("light.a", "Wohnzimmerlampe", "light", "off"),
        EntitySnapshot("light.b", "Kuechenlampe", "light", "off"),
        EntitySnapshot("light.c", "Buerolampe", "light", "off"),
    ]
    assert engine.match("mach die ersten beiden Lichter an", entities) is None
    assert engine.match("mach die letzten zwei Lichter an", entities) is None
