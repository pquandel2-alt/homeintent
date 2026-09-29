"""Phase 6 (v2 plan): Entity Resolver 2.0 - resolve_phrase(). Pins the
scoring tiers from the plan (exact friendly name/alias, normalized exact,
entity ID, contains/reverse contains, area/domain/device_class bonuses) and
ambiguity detection on near-tied scores. resolve_entity() itself (the
context-free wrapper) stays covered by test_entity_resolution.py/
test_aliases.py - those must keep passing unchanged as the regression net
for this rewrite."""

from __future__ import annotations

from homeintent.entities import EntitySnapshot, ResolutionStatus
from homeintent.nlu.target_resolution import resolve_phrase


def test_exact_friendly_name_outscores_contains():
    entities = [
        EntitySnapshot("light.a", "Licht", "light", "off"),
        EntitySnapshot("light.b", "Küchenlicht", "light", "off"),
    ]
    result = resolve_phrase("Licht", entities)
    assert result.status is ResolutionStatus.RESOLVED
    assert result.entity.entity_id == "light.a"
    assert result.score == 100


def test_exact_alias_scores_below_exact_friendly_name():
    entities = [EntitySnapshot("light.a", "Wohnzimmer Deckenlicht", "light", "off", aliases=("Deckenlampe",))]
    result = resolve_phrase("Deckenlampe", entities)
    assert result.status is ResolutionStatus.RESOLVED
    assert result.score == 90


def test_normalized_exact_scores_below_literal_exact():
    entities = [EntitySnapshot("light.a", "Garagentor", "light", "off")]
    result = resolve_phrase("garagentor", entities)
    assert result.status is ResolutionStatus.RESOLVED
    assert result.score == 85


def test_entity_id_match_scores_below_normalized_exact():
    entities = [EntitySnapshot("switch.garagentor", "Tor", "switch", "off")]
    result = resolve_phrase("switch.garagentor", entities)
    assert result.status is ResolutionStatus.RESOLVED
    assert result.score == 70


def test_contains_outscores_reverse_contains():
    entities = [EntitySnapshot("cover.a", "Rolllade Büro", "cover", "closed")]
    result = resolve_phrase("Büro", entities)
    assert result.status is ResolutionStatus.RESOLVED
    assert result.score == 50


def test_reverse_contains_scores_forty():
    entities = [EntitySnapshot("cover.a", "Wohnzimmer", "cover", "closed")]
    result = resolve_phrase("Rollladen Wohnzimmer", entities)
    assert result.status is ResolutionStatus.RESOLVED
    assert result.score == 40


def test_area_bonus_breaks_a_tie():
    entities = [
        EntitySnapshot("light.a", "Lampe", "light", "off", area_id="wohnzimmer"),
        EntitySnapshot("light.b", "Lampe", "light", "off", area_id="buro"),
    ]
    # Without context: identical exact-name score -> ambiguous.
    assert resolve_phrase("Lampe", entities).status is ResolutionStatus.AMBIGUOUS
    # With an area hint: the matching-area candidate wins outright.
    result = resolve_phrase("Lampe", entities, area_id="wohnzimmer")
    assert result.status is ResolutionStatus.RESOLVED
    assert result.entity.entity_id == "light.a"
    assert result.score == 130


def test_domain_and_device_class_bonus_add_up():
    entities = [EntitySnapshot("sensor.a", "Temperatur", "sensor", "18", device_class="temperature")]
    result = resolve_phrase("Temperatur", entities, domain="sensor", device_class="temperature")
    assert result.status is ResolutionStatus.RESOLVED
    assert result.score == 100 + 20 + 15


def test_near_tied_scores_are_ambiguous_not_guessed():
    # Both contains-tier (+50), genuinely tied - must not silently pick one.
    entities = [
        EntitySnapshot("light.a", "Buntes Licht", "light", "off"),
        EntitySnapshot("light.b", "Helles Licht", "light", "off"),
    ]
    result = resolve_phrase("Licht", entities)
    assert result.status is ResolutionStatus.AMBIGUOUS
    assert len(result.candidates) == 2


def test_no_match_is_not_found():
    entities = [EntitySnapshot("light.a", "Küchenlicht", "light", "off")]
    result = resolve_phrase("Kellerlicht", entities)
    assert result.status is ResolutionStatus.NOT_FOUND


def test_empty_name_is_not_found():
    entities = [EntitySnapshot("light.a", "Küchenlicht", "light", "off")]
    assert resolve_phrase("", entities).status is ResolutionStatus.NOT_FOUND
