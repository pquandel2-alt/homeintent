from homeintent.entities import EntitySnapshot
from homeintent.nlu.parse_outcome import ParseFailureReason
from homeintent.nlu.understanding import (
    UnderstandingAuthority,
    UnderstandingKind,
    UnderstandingOutcome,
    compare_outcomes,
)
from homeintent.nlu.semantic_utterance import SpeechAct


def test_understand_returns_explicit_command_with_validated_payload(engine, entities):
    outcome = engine.understand("Mach das Flurlicht an", entities)

    assert outcome.kind is UnderstandingKind.COMMAND
    assert outcome.actionable
    assert outcome.payload is not None
    assert outcome.payload.plan.service == "turn_on"
    assert outcome.route == "HassTurnOn"


def test_light_power_is_v7_authoritative_after_shadow_gate(engine, entities):
    outcome = engine.understand("Mach das Flurlicht an", entities)

    assert outcome.authority is UnderstandingAuthority.V7_MIGRATED
    assert outcome.payload is not None
    assert outcome.payload.plan.entity_id == "light.flur_licht"


def test_cover_capability_is_v7_authoritative_after_shadow_gate(engine, entities):
    outcome = engine.understand("Fahre Rollladen Büro hoch", entities)

    assert outcome.authority is UnderstandingAuthority.V7_MIGRATED
    assert outcome.payload is not None
    assert outcome.payload.plan.service == "open_cover"


def test_percentage_capability_is_v7_authoritative_after_shadow_gate(engine, entities):
    outcome = engine.understand("Fahre Rollladen Büro auf 40 Prozent", entities)

    assert outcome.authority is UnderstandingAuthority.V7_MIGRATED
    assert outcome.payload is not None
    assert outcome.payload.plan.service == "set_cover_position"


def test_named_state_query_is_v7_authoritative(engine):
    entities = [EntitySnapshot("light.kueche", "Küchenlicht", "light", "off")]

    outcome = engine.understand("Ist das Küchenlicht an?", entities)

    assert outcome.authority is UnderstandingAuthority.V7_MIGRATED
    assert outcome.kind is UnderstandingKind.QUERY
    assert outcome.payload is not None
    assert outcome.payload.plan is None


def test_verb_state_query_is_v7_authoritative(engine):
    entities = [
        EntitySnapshot("media_player.atlas", "Radio Atlas", "media_player", "playing")
    ]

    outcome = engine.understand("Spielt das Radio Atlas?", entities)

    assert outcome.authority is UnderstandingAuthority.V7_MIGRATED
    assert outcome.kind is UnderstandingKind.QUERY
    assert outcome.speech is not None and outcome.speech.startswith("Ja,")


def test_measurement_query_is_v7_authoritative(engine):
    entities = [
        EntitySnapshot(
            "sensor.wohnzimmer_temp",
            "Wohnzimmer Temperatur",
            "sensor",
            "21.5",
            area_id="wohnzimmer",
            area_name="Wohnzimmer",
            device_class="temperature",
            unit="°C",
        )
    ]

    outcome = engine.understand("Wie warm ist es im Wohnzimmer?", entities)

    assert outcome.authority is UnderstandingAuthority.V7_MIGRATED
    assert outcome.kind is UnderstandingKind.QUERY
    assert outcome.speech == "21,5 Grad."


def test_understand_returns_explicit_unsupported_reason(engine, entities):
    outcome = engine.understand("Erzähle mir eine Geschichte", entities)

    assert outcome.kind is UnderstandingKind.UNSUPPORTED
    assert outcome.reason is ParseFailureReason.NO_GRAMMAR
    assert outcome.payload is None


def test_understand_never_enters_legacy_matcher(engine, entities, monkeypatch):
    """The historical parser may only run through the shadow API."""

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("production understand() called the legacy matcher")

    monkeypatch.setattr(engine, "match", fail_if_called)

    supported = engine.understand("Mach das Flurlicht an", entities)
    unsupported = engine.understand("Erzähle mir eine Geschichte", entities)

    assert supported.authority is UnderstandingAuthority.V7_MIGRATED
    assert supported.actionable
    assert unsupported.authority is UnderstandingAuthority.NONE
    assert not unsupported.actionable


def test_understand_marks_negated_command_unsafe(engine, entities):
    outcome = engine.understand("Mach das Flurlicht nicht an", entities)

    assert outcome.kind is UnderstandingKind.UNSAFE
    assert outcome.reason is ParseFailureReason.UNSAFE_INFERENCE
    assert not outcome.actionable


def test_understand_uses_unique_bounded_action_spelling_variant(engine, entities):
    outcome = engine.understand("Bitte Flurlicht einschallten", entities)

    assert outcome.kind is UnderstandingKind.COMMAND
    assert outcome.payload is not None
    assert outcome.payload.plan.entity_id == "light.flur_licht"
    assert outcome.payload.plan.service == "turn_on"
    assert outcome.corrections == ("lexical_spelling",)


def test_misspelled_unknown_negation_cannot_become_actionable(engine, entities):
    outcome = engine.understand("Mach Flurlicht nciht an", entities)

    assert not outcome.actionable
    assert outcome.payload is None


def test_shadow_comparison_reports_semantic_differences():
    authoritative = UnderstandingOutcome(
        UnderstandingKind.COMMAND,
        "Licht an",
        "Licht an",
        SpeechAct.COMMAND,
        payload="plan",
    )
    candidate = UnderstandingOutcome(
        UnderstandingKind.QUERY,
        "Licht an",
        "Licht an",
        SpeechAct.QUERY,
    )

    comparison = compare_outcomes(authoritative, candidate)

    assert not comparison.equivalent
    assert any(item.startswith("kind:") for item in comparison.differences)
    assert "payload" in comparison.differences


def test_understand_surfaces_ambiguous_candidates_explicitly(engine):
    entities = [
        EntitySnapshot("light.a", "Leselicht", "light", "off"),
        EntitySnapshot("light.b", "Leselicht", "light", "off"),
    ]

    outcome = engine.understand("Mach das Leselicht an", entities)

    assert outcome.kind is UnderstandingKind.AMBIGUOUS
    assert outcome.payload is not None
    assert outcome.payload.clarification is not None
    assert len(outcome.payload.clarification.candidates) == 2


def test_partial_shared_name_lists_similarly_named_candidates(engine):
    entities = [
        EntitySnapshot("light.links", "Deckenlicht links", "light", "off"),
        EntitySnapshot("light.rechts", "Deckenlicht rechts", "light", "off"),
    ]

    outcome = engine.understand("Mach das Deckenlicht an", entities)

    assert outcome.kind is UnderstandingKind.AMBIGUOUS
    assert not outcome.actionable
    assert outcome.payload is not None
    assert outcome.payload.clarification is not None
    assert {entity.entity_id for entity in outcome.payload.clarification.candidates} == {
        "light.links",
        "light.rechts",
    }
    assert "Deckenlicht links" in outcome.speech
    assert "Deckenlicht rechts" in outcome.speech


def test_single_fuzzy_name_requires_confirmation_instead_of_execution(engine):
    entities = [
        EntitySnapshot("light.links", "Deckenlicht links", "light", "off"),
    ]

    outcome = engine.understand("Mach Deckenlicth links an", entities)

    assert outcome.kind is UnderstandingKind.CLARIFICATION
    assert not outcome.actionable
    assert outcome.payload is not None
    assert outcome.payload.clarification is not None
    assert [entity.entity_id for entity in outcome.payload.clarification.candidates] == [
        "light.links"
    ]
