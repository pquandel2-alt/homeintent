"""Integration Wave Migration Step 3/5: direct unit tests for
``NluEngine.match_automation()`` itself - the regex pre-check, the
sentence-split gate, propagation of each sub-parser's own "never guess"
``None``, and the happy-path -> ``validate_automation()`` wiring. Complements
``tests/test_integration_wave_e2e.py``, which exercises the same method
through the real conversation entity instead.

Conditions Wave (2026-08-19): also covers the Trigger/Condition split
fallback (``NluEngine._split_trigger_condition()``) end-to-end through
``match_automation()`` itself - not just ``split_on_top_level_and()`` in
isolation (see ``tests/test_automation_condition_parser.py`` for that), the
same "unit tests plus real pipeline tests" split every other automation
sub-feature in this repo already follows.
"""

from __future__ import annotations

import pytest

from homeintent.engine import (
    AutomationClarificationResult,
    AutomationDraftMatchResult,
    AutomationMatchResult,
)
from homeintent.entities import EntitySnapshot
from homeintent.nlu.automation_validator import AutomationValidationError
from homeintent.nlu.action_model import ActionType
from homeintent.nlu.condition_model import ConditionType, LogicalOperator

KUECHE_FENSTER = EntitySnapshot(
    "binary_sensor.kueche_fenster", "Küchenfenster", "binary_sensor", "off",
    area_id="kueche", area_name="Küche", device_class="window",
)
KUECHE_LICHT = EntitySnapshot(
    "light.kueche_licht", "Küchenlicht", "light", "off",
    area_id="kueche", area_name="Küche",
    capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
)
BUERO_FENSTER_1 = EntitySnapshot(
    "binary_sensor.buero_fenster_1", "Bürofenster", "binary_sensor", "off",
    area_id="buero_1", area_name="Büro 1", device_class="window",
)
BUERO_FENSTER_2 = EntitySnapshot(
    "binary_sensor.buero_fenster_2", "Bürofenster", "binary_sensor", "off",
    area_id="buero_2", area_name="Büro 2", device_class="window",
)
WOHNZIMMER_HEIZUNG = EntitySnapshot(
    "climate.heizung_wohnzimmer", "Heizung Wohnzimmer", "climate", "heat",
    area_id="wohnzimmer", area_name="Wohnzimmer",
    capabilities=frozenset({"TURN_ON", "TURN_OFF", "TEMPERATURE"}),
    attributes={"temperature": 20},
)
TEMP_AUSSEN = EntitySnapshot(
    "sensor.aussentemperatur",
    "Außentemperatur",
    "sensor",
    "21.5",
    unit="°C",
    device_class="temperature",
)
NOTIFY_PHILIPP = EntitySnapshot(
    "notify.mobile_app_philipp", "Philipp Handy", "notify", "unknown"
)

ENTITIES = [KUECHE_FENSTER, KUECHE_LICHT, BUERO_FENSTER_1, BUERO_FENSTER_2, WOHNZIMMER_HEIZUNG, TEMP_AUSSEN, NOTIFY_PHILIPP]


def test_non_automation_sentence_is_rejected_by_the_regex_gate(engine):
    assert engine.match_automation("Schalte das Küchenlicht ein.", ENTITIES) is None


def test_automation_keyword_without_a_comma_uses_unique_grammar_split(engine):
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird schalte das Küchenlicht ein.", ENTITIES
    )
    assert result is not None
    assert result.validation_error is None


def test_valid_trigger_and_action_produces_a_validated_automation_model(engine):
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein.", ENTITIES
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert len(result.model.triggers) == 1
    assert len(result.model.actions) == 1
    assert "AutomationModel" in result.response_text


def test_action_first_numeric_equality_can_create_a_notification(engine):
    for text in (
        "Kannst du mich benachrichtigen wenn die Außentemperatur 28 Grad beträgt?",
        "Kannst du Philipp benachrichtigen wenn die Außentemperatur 28 Grad beträgt?",
    ):
        result = engine.match_automation(text, ENTITIES)

        assert isinstance(result, AutomationMatchResult)
        assert result.validation_error is None
        assert result.model.triggers[0].comparator.name == "EQUAL"
        assert result.model.triggers[0].threshold == 28
        assert result.model.actions[0].type is ActionType.NOTIFY
        assert "Außentemperatur" in result.model.actions[0].message
        if "Philipp" in text:
            assert result.model.actions[0].target.entity_id == "notify.mobile_app_philipp"
        else:
            assert result.model.actions[0].target is None


def test_free_order_state_trigger_runs_through_complete_automation_pipeline(engine):
    result = engine.match_automation(
        "Wenn in der Küche offen die Fenster sind, schalte das Küchenlicht ein.",
        ENTITIES,
    )

    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert result.model.triggers[0].target.area_id == "kueche"
    assert result.model.triggers[0].state.name == "OPEN"
    assert result.model.actions[0].type is ActionType.TURN_ON


def test_state_trigger_reuses_semantic_half_position_action(engine):
    cover = EntitySnapshot(
        "cover.kueche", "Rollade Küche", "cover", "open",
        area_id="kueche", area_name="Küche",
        capabilities=frozenset({"POSITION"}),
    )

    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird, fahr die Rollade Küche halb runter.",
        [*ENTITIES, cover],
    )

    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert result.model.actions[0].type is ActionType.SET_POSITION
    assert result.model.actions[0].target.area_id == "kueche"
    assert result.model.actions[0].value == 50


def test_trigger_only_starts_a_conversational_automation_draft(engine):
    draft = engine.match_automation_draft_start(
        "Wenn das Küchenfenster geöffnet wird", ENTITIES
    )

    assert isinstance(draft, AutomationDraftMatchResult)
    assert draft.response_text == "Was soll dann passieren?"


def test_next_turn_action_completes_the_automation_draft(engine):
    draft = engine.match_automation_draft_start(
        "Wenn das Küchenfenster geöffnet wird", ENTITIES
    )
    completed = engine.complete_automation_draft(
        "Schalte das Küchenlicht ein", draft.trigger, draft.source_text, ENTITIES
    )

    assert isinstance(completed, AutomationMatchResult)
    assert completed.validation_error is None
    assert len(completed.model.triggers) == 1
    assert len(completed.model.actions) == 1


def test_unconfirmed_automation_can_be_revised_before_confirmation(engine):
    original = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein.", ENTITIES
    )
    repeated = engine.revise_pending_automation(
        "Nur dreimal", original.model, ENTITIES
    )
    extended = engine.revise_pending_automation(
        "Füge als weitere Aktion schalte das Küchenlicht aus hinzu",
        repeated.model,
        ENTITIES,
    )

    assert repeated.model.max_runs == 3
    assert len(extended.model.actions) == 2
    assert extended.validation_error is None


def test_ambiguous_trigger_target_returns_none_never_guesses(engine):
    # Two entities named "Bürofenster" in different areas, no area named in
    # the sentence - AutomationTriggerParser._build_named_target() refuses.
    result = engine.match_automation(
        "Wenn das Bürofenster geöffnet wird, schalte das Küchenlicht ein.", ENTITIES
    )
    assert result is None


def test_unresolvable_action_target_returns_none_never_guesses(engine):
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird, schalte das Kellerlicht ein.", ENTITIES
    )
    assert result is None


def test_structurally_invalid_automation_surfaces_its_validation_error_not_silently(engine, monkeypatch):
    # Every value match_automation()'s two sub-parsers can currently produce
    # is already grammar-constrained to be valid (temperature 5-30, percent
    # 0-100, weekdays/times from fixed slot lists, targets refused rather
    # than built invalid - Regel 4) - so validate_automation() can't
    # actually be made to fail through a real sentence today. This monkey-
    # patches it to prove match_automation()'s *formatting* of a non-None
    # validation_error onto response_text works, independent of whether
    # today's grammars can reach that branch.
    monkeypatch.setattr(
        "homeintent.engine.validate_automation",
        lambda model: AutomationValidationError.INVALID_PARAMETER,
    )
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein.", ENTITIES
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is AutomationValidationError.INVALID_PARAMETER
    assert "validation_error: INVALID_PARAMETER" in result.response_text


def test_never_produces_a_service_call_plan():
    """AutomationMatchResult has no plan/ServiceCallPlan field at all -
    structurally impossible to reach hass.services.async_call() from this
    path (see AutomationMatchResult's own docstring)."""
    assert not hasattr(AutomationMatchResult, "plan")


# ============================================================================
# Conditions Wave (2026-08-19): "wenn Trigger und Condition, Action" through
# the real match_automation() Trigger/Condition split fallback.
# ============================================================================

WOHNZIMMER_LICHT = EntitySnapshot(
    "light.wohnzimmer_licht", "Wohnzimmerlicht", "light", "off",
    area_id="wohnzimmer", area_name="Wohnzimmer",
    capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
)

CONDITIONS_ENTITIES = ENTITIES + [WOHNZIMMER_LICHT]


def test_trigger_and_action_without_a_condition_still_matches_unchanged(engine):
    # Same sentence test_valid_trigger_and_action_produces_a_validated_automation_model
    # already covers - repeated here to pin that adding the Condition
    # fallback did not change the primary (no "und") path at all.
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein.", CONDITIONS_ENTITIES
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.model.conditions == ()
    assert result.validation_error is None


def test_trigger_and_action_with_a_multi_entity_und_still_matches_as_a_single_trigger(engine):
    # "Tür und Fenster" is a single Trigger, not a Trigger+Condition split -
    # the full-text Trigger parse must succeed first and win, so the split
    # fallback never even runs (see match_automation()'s own docstring).
    result = engine.match_automation(
        "Wenn das Küchenfenster und das Bürofenster geöffnet werden, schalte das Küchenlicht ein.",
        CONDITIONS_ENTITIES,
    )
    assert result is None  # Bürofenster is ambiguous across two areas - never guessed, not a regression


def test_trigger_and_numeric_condition_and_action(engine):
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird und die Heizung Wohnzimmer über 22 Grad liegt, "
        "schalte das Küchenlicht ein.",
        CONDITIONS_ENTITIES,
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert len(result.model.conditions) == 1
    assert result.model.conditions[0].condition.type is ConditionType.NUMERIC


def test_trigger_and_time_condition_and_action(engine):
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird und es nach 18 Uhr ist, schalte das Küchenlicht ein.",
        CONDITIONS_ENTITIES,
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert len(result.model.conditions) == 1
    assert result.model.conditions[0].condition.type is ConditionType.TIME


def test_trigger_and_presence_condition_and_action(engine):
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird und niemand zuhause ist, schalte das Küchenlicht ein.",
        CONDITIONS_ENTITIES,
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert len(result.model.conditions) == 1
    assert result.model.conditions[0].operator is LogicalOperator.NOT


def test_trigger_and_weekday_condition_and_action(engine):
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird und Samstag ist, schalte das Küchenlicht ein.",
        CONDITIONS_ENTITIES,
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert len(result.model.conditions) == 1
    assert result.model.conditions[0].condition.type is ConditionType.WEEKDAY


def test_ambiguous_condition_target_returns_none_never_guesses(engine):
    # Two entities named "Bürofenster" in different areas - the Condition
    # half must refuse exactly like the Trigger half already does elsewhere
    # in this file, not fall back to a guess.
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird und das Bürofenster offen ist, schalte das Küchenlicht ein.",
        CONDITIONS_ENTITIES,
    )
    assert result is None


def test_unresolvable_condition_target_returns_none_never_guesses(engine):
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird und das Kellerlicht an ist, schalte das Küchenlicht ein.",
        CONDITIONS_ENTITIES,
    )
    assert result is None


def test_condition_result_is_validated_the_same_way_as_triggers_and_actions(engine, monkeypatch):
    monkeypatch.setattr(
        "homeintent.engine.validate_automation",
        lambda model: AutomationValidationError.INVALID_LOGIC,
    )
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird und es nach 18 Uhr ist, schalte das Küchenlicht ein.",
        CONDITIONS_ENTITIES,
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is AutomationValidationError.INVALID_LOGIC
    assert "validation_error: INVALID_LOGIC" in result.response_text


def test_plain_command_is_unaffected_by_the_condition_fallback(engine):
    assert engine.match_automation("Schalte das Wohnzimmerlicht ein.", CONDITIONS_ENTITIES) is None


# --- Wave 12, "Einmalige Automation" (fire-once, then self-delete) ---------


def test_einmalig_qualifier_sets_once_true_and_is_stripped_from_the_sentence(engine):
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht einmalig ein.", ENTITIES
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert result.model.once is True
    assert len(result.model.actions) == 1


def test_nur_einmal_qualifier_also_sets_once_true(engine):
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht nur einmal ein.", ENTITIES
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert result.model.once is True


def test_without_the_qualifier_once_defaults_to_false(engine):
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein.", ENTITIES
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.model.once is False


def test_einmalig_qualifier_works_on_a_state_based_trigger_not_just_time(engine):
    # The user's own explicit scope for Wave 12: "nicht nur zeitbasiert
    # sondern auch wenn sich der Zustand von einem Sensor ändert" - any
    # trigger type already supported by match_automation() must support
    # "einmalig" too, since the qualifier is stripped independently of the
    # trigger clause's own grammar.
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird, schalte einmalig das Küchenlicht ein.", ENTITIES
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.model.once is True
    assert len(result.model.triggers) == 1


def test_dreimal_qualifier_sets_a_bounded_run_count(engine):
    result = engine.match_automation(
        "Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht nur dreimal ein.",
        ENTITIES,
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert result.model.once is False
    assert result.model.max_runs == 3


# ============================================================================
# Notification-specific features: action-first, action-trailing, automation shells
# ============================================================================

WOHNZIMMER_FENSTER = EntitySnapshot(
    "binary_sensor.wohnzimmer_fenster", "Wohnzimmerfenster", "binary_sensor", "off",
    area_id="wohnzimmer", area_name="Wohnzimmer", device_class="window",
)
# NOTIFY_PHILIPP is already part of ENTITIES (defined above) - appending it
# again here would put two distinct EntitySnapshot objects with the same
# entity_id in NOTIFY_ENTITIES, making every recipient resolution ambiguous
# (2 matches instead of 1) and silently forcing "no automation" everywhere.
NOTIFY_ENTITIES = ENTITIES + [WOHNZIMMER_FENSTER]


def test_action_first_notification_with_recipient_resolves_notify_target(engine):
    """Benachrichtige Philipp, wenn..."""
    result = engine.match_automation(
        "Benachrichtige Philipp, wenn das Wohnzimmer Fenster geöffnet wird", NOTIFY_ENTITIES
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert result.model.actions[0].type is ActionType.NOTIFY
    assert result.model.actions[0].target.entity_id == "notify.mobile_app_philipp"
    # 7.1.2: the message describes the event grammatically instead of
    # echoing the subordinate trigger clause ("... geöffnet wird.").
    assert result.model.actions[0].message == "Das Wohnzimmerfenster wurde geöffnet."


def test_action_trailing_notification_with_recipient_to_preposition(engine):
    """...eine Push-Nachricht an Philipp schickt"""
    result = engine.match_automation(
        "Wenn das Wohnzimmer Fenster geöffnet wird, schicke eine Push-Nachricht an Philipp",
        NOTIFY_ENTITIES,
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert result.model.actions[0].type is ActionType.NOTIFY
    assert result.model.actions[0].target.entity_id == "notify.mobile_app_philipp"
    # 7.1.2: the message describes the event grammatically instead of
    # echoing the subordinate trigger clause ("... geöffnet wird.").
    assert result.model.actions[0].message == "Das Wohnzimmerfenster wurde geöffnet."


def test_automation_shell_with_notification_and_recipient(engine):
    """Erstelle eine Automation die sobald das Wohnzimmer Fenster geöffnet wird
    eine Push Nachricht an Philipp schickt"""
    result = engine.match_automation(
        "Erstelle eine Automation die sobald das Wohnzimmer Fenster geöffnet wird eine Push-Nachricht an Philipp schickt",
        NOTIFY_ENTITIES,
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert result.model.actions[0].type is ActionType.NOTIFY
    assert result.model.actions[0].target.entity_id == "notify.mobile_app_philipp"
    # 7.1.2: the message describes the event grammatically instead of
    # echoing the subordinate trigger clause ("... geöffnet wird.").
    assert result.model.actions[0].message == "Das Wohnzimmerfenster wurde geöffnet."
    # Original text with shell is preserved for preview
    assert "Erstelle eine Automation" in result.model.source_text


def test_automation_shell_variants_all_recognized(engine):
    """Multiple shells are recognized: Erstelle, Lege an, Ich möchte"""
    for shell_text in [
        "Erstelle eine Automation die ",
        "Lege eine Regel an, die ",
        "Ich möchte eine Automation, die ",
    ]:
        text = f"{shell_text}wenn das Wohnzimmer Fenster geöffnet wird, benachrichtige mich"
        result = engine.match_automation(text, NOTIFY_ENTITIES)
        assert isinstance(result, AutomationMatchResult)
        assert result.model.actions[0].type is ActionType.NOTIFY


def test_notification_variant_keywords_recognized(engine):
    """Push-Nachricht, Nachricht, Benachrichtigung, Push Nachricht"""
    base = "Wenn das Wohnzimmer Fenster geöffnet wird, "
    for variant in [
        "schicke eine Push-Nachricht an Philipp",
        "schicke eine Benachrichtigung an Philipp",
        "sende eine Nachricht an Philipp",
        "schicke Philipp eine Mitteilung",
    ]:
        result = engine.match_automation(
            f"{base}{variant}", NOTIFY_ENTITIES
        )
        assert isinstance(result, AutomationMatchResult), f"Failed: {variant}"
        assert result.model.actions[0].type is ActionType.NOTIFY
        assert result.model.actions[0].target.entity_id == "notify.mobile_app_philipp"


def test_notification_with_generated_message_from_trigger(engine):
    """Message is derived from trigger when not explicitly stated"""
    result = engine.match_automation(
        "Wenn das Wohnzimmer Fenster geöffnet wird, benachrichtige mich",
        NOTIFY_ENTITIES,
    )
    assert isinstance(result, AutomationMatchResult)
    # 7.1.2: the message describes the event grammatically instead of
    # echoing the subordinate trigger clause ("... geöffnet wird.").
    assert result.model.actions[0].message == "Das Wohnzimmerfenster wurde geöffnet."
    assert "geöffnet" in result.model.actions[0].message


def test_notification_without_shell_trigger_first_form_still_works(engine):
    """Sobald X, sende Nachricht an Y"""
    result = engine.match_automation(
        "Sobald das Wohnzimmer Fenster geöffnet wird, sende eine Nachricht an Philipp",
        NOTIFY_ENTITIES,
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.model.actions[0].type is ActionType.NOTIFY
    assert result.model.actions[0].target.entity_id == "notify.mobile_app_philipp"


def test_notification_without_shell_action_first_form_still_works(engine):
    """Benachrichtige mich, wenn X"""
    result = engine.match_automation(
        "Benachrichtige mich, wenn das Wohnzimmer Fenster geöffnet wird",
        NOTIFY_ENTITIES,
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.model.actions[0].type is ActionType.NOTIFY
    assert result.model.actions[0].target is None  # mich -> proactive channel


def test_notification_to_recipient_without_explicit_verb(engine):
    """Eine Push-Nachricht an Philipp (implicit schicke/sende)"""
    result = engine.match_automation(
        "Wenn das Wohnzimmer Fenster geöffnet wird, eine Push-Nachricht an Philipp",
        NOTIFY_ENTITIES,
    )
    assert isinstance(result, AutomationMatchResult)
    assert result.model.actions[0].type is ActionType.NOTIFY
    assert result.model.actions[0].target.entity_id == "notify.mobile_app_philipp"


def test_notification_with_unknown_recipient_returns_none(engine):
    """Unknown recipient name → no automation created"""
    result = engine.match_automation(
        "Wenn das Wohnzimmer Fenster geöffnet wird, schicke eine Nachricht an Klaus",
        NOTIFY_ENTITIES,
    )
    # No Klaus in the notify entities, so no valid automation. Since 7.2.0
    # the request is recognized and answered with a recipient question.
    assert not isinstance(result, AutomationMatchResult)
    if result is not None:
        assert isinstance(result, AutomationClarificationResult)
        assert "Klaus" in result.response_text
        assert result.clarification is None


def test_notification_trigger_first_with_window_variants(engine):
    """Wohnzimmer Fenster and Wohnzimmerfenster both work"""
    for fenster_text in ["Wohnzimmer Fenster", "Wohnzimmerfenster"]:
        result = engine.match_automation(
            f"Wenn das {fenster_text} geöffnet wird, benachrichtige mich",
            NOTIFY_ENTITIES,
        )
        assert isinstance(result, AutomationMatchResult)
        assert result.model.triggers[0].target.area_id == "wohnzimmer"


def test_trigger_with_unreadable_action_is_not_mistaken_for_a_trigger_only_draft(engine):
    # The whole sentence must not be read as "when the light turns on".
    draft = engine.match_automation_draft_start(
        "Wenn das Küchenfenster geöffnet wird, mach irgendwas Unbekanntes.", ENTITIES
    )

    assert draft is None


def test_trigger_with_trailing_dann_still_starts_a_draft(engine):
    draft = engine.match_automation_draft_start(
        "Wenn das Küchenfenster geöffnet wird, dann", ENTITIES
    )

    assert isinstance(draft, AutomationDraftMatchResult)


@pytest.mark.parametrize(
    ("sentence", "event"),
    (
        ("Wenn es dunkel wird, schalte das Küchenlicht ein.", "SUNSET"),
        ("Wenn die Sonne untergeht, mach das Küchenlicht an.", "SUNSET"),
        ("Sobald es hell wird, schalte das Küchenlicht aus.", "SUNRISE"),
        ("Wenn die Sonne aufgeht, schalte das Küchenlicht aus.", "SUNRISE"),
    ),
)
def test_everyday_sun_wording_becomes_a_sun_trigger(engine, sentence, event):
    result = engine.match_automation(sentence, ENTITIES)

    assert isinstance(result, AutomationMatchResult)
    assert result.validation_error is None
    assert result.model.triggers[0].sun_event.name == event
    assert len(result.model.actions) == 1
