"""Typed attribute measurements (spec §9-§16, §33, §35): model, grounding,
generation and the closed attribute mapping."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _automation_world import WORLD  # noqa: E402
from homeintent.engine import AutomationClarificationResult, AutomationMatchResult, NluEngine  # noqa: E402
from homeintent.nlu.action_model import (  # noqa: E402
    ActionModel,
    ActionType,
    NotificationRecipient,
    NotificationRecipientKind,
)
from homeintent.nlu.semantic_state import SemanticState  # noqa: E402
from homeintent.nlu.automation_model import (  # noqa: E402
    AutomationModel,
    NumericComparator,
    TriggerModel,
    TriggerTarget,
    TriggerType,
)
from homeintent.nlu.automation_validator import AutomationValidationError, validate_automation  # noqa: E402
from homeintent.nlu.ha_automation_generator import GenerationError, generate_ha_automation_config  # noqa: E402
from homeintent.nlu.measurement import (  # noqa: E402
    MEASUREMENTS,
    MeasurementProperty,
    TravelDirection,
    comparison_expression,
    percent_property_for_domain,
)

ENGINE = NluEngine()
ENTITIES = list(WORLD)
COVER_TARGET = TriggerTarget(domain="cover", area_id="buero")


def _trigger(sentence: str) -> TriggerModel:
    result = ENGINE.match_automation(sentence, ENTITIES)
    assert isinstance(result, AutomationMatchResult), result
    assert result.validation_error is None
    [trigger] = result.model.triggers
    return trigger


def test_the_attribute_mapping_is_closed_and_explicit():
    assert {prop: spec.attribute for prop, spec in MEASUREMENTS.items()} == {
        MeasurementProperty.COVER_POSITION: "current_position",
        MeasurementProperty.LIGHT_BRIGHTNESS: "brightness",
        MeasurementProperty.FAN_PERCENTAGE: "percentage",
    }
    assert percent_property_for_domain("cover") is MeasurementProperty.COVER_POSITION
    assert percent_property_for_domain("media_player") is None
    assert percent_property_for_domain("sensor") is None


@pytest.mark.parametrize(("sentence", "domain", "prop"), (
    ("Benachrichtige mich, wenn die Rolllade im Büro 50 Prozent erreicht.", "cover",
     MeasurementProperty.COVER_POSITION),
    ("Benachrichtige mich, wenn das Wohnzimmerlicht auf 50 Prozent gedimmt ist.", "light",
     MeasurementProperty.LIGHT_BRIGHTNESS),
    ("Benachrichtige mich, wenn der Ventilator im Schlafzimmer auf 50 Prozent läuft.", "fan",
     MeasurementProperty.FAN_PERCENTAGE),
    ("Benachrichtige mich, wenn die Luftfeuchtigkeit im Büro 50 Prozent erreicht.", "sensor", None),
    ("Benachrichtige mich, wenn der Handy Akku 50 Prozent erreicht.", "sensor", None),
))
def test_the_grounded_domain_decides_what_percent_means(sentence, domain, prop):
    trigger = _trigger(sentence)
    assert trigger.type is TriggerType.NUMERIC_STATE
    assert trigger.target is not None and trigger.target.domain == domain
    assert trigger.measurement is prop
    assert trigger.threshold == 50


def test_percent_on_a_device_without_a_safe_property_is_unsupported():
    result = ENGINE.match_automation(
        "Benachrichtige mich, wenn der Lautsprecher im Wohnzimmer 50 Prozent erreicht.", ENTITIES
    )
    assert isinstance(result, AutomationClarificationResult)
    assert "Prozent" in result.response_text


@pytest.mark.parametrize(("sentence", "comparator", "value"), (
    ("Sag mir Bescheid, wenn der Rollladen im Büro 50 Prozent erreicht.", NumericComparator.EQUAL, 50),
    ("Sag mir Bescheid, wenn der Rollladen im Büro über 50 Prozent steht.", NumericComparator.ABOVE, 50),
    ("Sag mir Bescheid, wenn der Rollladen im Büro mehr als 50 Prozent offen ist.", NumericComparator.ABOVE, 50),
    ("Sag mir Bescheid, wenn der Rollladen im Büro unter 50 Prozent fällt.", NumericComparator.BELOW, 50),
    ("Sag mir Bescheid, wenn der Rollladen im Büro weniger als 50 Prozent offen ist.", NumericComparator.BELOW, 50),
    ("Sag mir Bescheid, wenn der Rollladen im Büro mindestens 50 Prozent erreicht.", NumericComparator.AT_LEAST, 50),
    ("Sag mir Bescheid, wenn der Rollladen im Büro höchstens 50 Prozent offen ist.", NumericComparator.AT_MOST, 50),
    ("Sag mir Bescheid, wenn der Rollladen im Büro 50,5 Prozent erreicht.", NumericComparator.EQUAL, 50.5),
    ("Sag mir Bescheid, wenn der Rollladen im Büro fünfzig Prozent erreicht.", NumericComparator.EQUAL, 50),
    ("Sag mir Bescheid, wenn der Rollladen im Büro halb offen ist.", NumericComparator.EQUAL, 50),
    ("Sag mir Bescheid, wenn der Rollladen im Büro zur Hälfte geöffnet ist.", NumericComparator.EQUAL, 50),
))
def test_position_language(sentence, comparator, value):
    trigger = _trigger(sentence)
    assert trigger.measurement is MeasurementProperty.COVER_POSITION
    assert trigger.comparator is comparator
    assert trigger.threshold == value


@pytest.mark.parametrize(("sentence", "direction"), (
    ("Sag mir Bescheid, wenn der Rollladen im Büro auf 50 Prozent heruntergefahren ist.", TravelDirection.DOWN),
    ("Sag mir Bescheid, wenn der Rollladen im Büro beim Hochfahren 50 Prozent erreicht.", TravelDirection.UP),
))
def test_explicit_direction_is_kept(sentence, direction):
    assert _trigger(sentence).direction is direction


def test_equality_generates_an_internal_template_trigger():
    trigger = TriggerModel(
        TriggerType.NUMERIC_STATE, target=COVER_TARGET, comparator=NumericComparator.EQUAL,
        threshold=50, measurement=MeasurementProperty.COVER_POSITION,
    )
    config = generate_ha_automation_config(AutomationModel(triggers=(trigger,)), ENTITIES).config
    assert config is not None
    assert config["triggers"] == [{
        "trigger": "template",
        "value_template": (
            "{{ (state_attr('cover.buero_rollladen', 'current_position') is number and "
            "state_attr('cover.buero_rollladen', 'current_position') == 50) }}"
        ),
    }]


def test_direction_uses_an_attribute_state_trigger_with_edge_and_sense_conditions():
    trigger = TriggerModel(
        TriggerType.NUMERIC_STATE, target=COVER_TARGET, comparator=NumericComparator.EQUAL,
        threshold=50, measurement=MeasurementProperty.COVER_POSITION, direction=TravelDirection.DOWN,
    )
    config = generate_ha_automation_config(AutomationModel(triggers=(trigger,)), ENTITIES).config
    assert config is not None
    assert config["triggers"] == [
        {"trigger": "state", "entity_id": "cover.buero_rollladen", "attribute": "current_position"}
    ]
    assert len(config["conditions"]) == 2
    assert "from_state" in config["conditions"][1]["value_template"]


def test_template_builder_refuses_anything_but_validated_entity_ids():
    with pytest.raises(ValueError):
        comparison_expression(MeasurementProperty.COVER_POSITION, ["cover.x') }}{{ 1"], "EQUAL", 50)
    with pytest.raises(ValueError):
        comparison_expression(MeasurementProperty.COVER_POSITION, ["cover.x"], "RAW", 50)


def test_spoken_attribute_names_never_reach_a_template():
    result = ENGINE.match_automation(
        "Benachrichtige mich, wenn im Büro state_attr current_position der Rolllade 50 Prozent erreicht.",
        ENTITIES,
    )
    assert not isinstance(result, AutomationMatchResult)


@pytest.mark.parametrize(("trigger", "error"), (
    (TriggerModel(TriggerType.STATE, target=COVER_TARGET, state=SemanticState.OPEN,
                  measurement=MeasurementProperty.COVER_POSITION),
     AutomationValidationError.INVALID_PARAMETER),
    (TriggerModel(TriggerType.NUMERIC_STATE, target=TriggerTarget(domain="light"), comparator=NumericComparator.EQUAL,
                  threshold=50, measurement=MeasurementProperty.COVER_POSITION),
     AutomationValidationError.INVALID_PARAMETER),
    (TriggerModel(TriggerType.NUMERIC_STATE, target=COVER_TARGET, comparator=NumericComparator.EQUAL,
                  threshold=150, measurement=MeasurementProperty.COVER_POSITION),
     AutomationValidationError.INVALID_PARAMETER),
    (TriggerModel(TriggerType.NUMERIC_STATE, target=TriggerTarget(domain="light"), comparator=NumericComparator.EQUAL,
                  threshold=50, measurement=MeasurementProperty.LIGHT_BRIGHTNESS, direction=TravelDirection.UP),
     AutomationValidationError.INVALID_PARAMETER),
))
def test_validator_rejects_inconsistent_measurements(trigger, error):
    action = ActionModel(ActionType.NOTIFY, message="x", recipient=NotificationRecipient(
        NotificationRecipientKind.CURRENT_USER))
    valid = replace(trigger, type=TriggerType.NUMERIC_STATE, target=COVER_TARGET,
                    comparator=NumericComparator.EQUAL, threshold=50,
                    measurement=MeasurementProperty.COVER_POSITION, direction=None)
    assert validate_automation(AutomationModel(triggers=(valid,), actions=(action,))) is None
    assert validate_automation(AutomationModel(triggers=(trigger,), actions=(action,))) is error


def test_generator_refuses_a_measurement_on_the_wrong_domain():
    trigger = TriggerModel(
        TriggerType.NUMERIC_STATE, target=TriggerTarget(domain="light", area_id="buero"),
        comparator=NumericComparator.EQUAL, threshold=50, measurement=MeasurementProperty.COVER_POSITION,
    )
    assert generate_ha_automation_config(AutomationModel(triggers=(trigger,)), ENTITIES).error is (
        GenerationError.UNSUPPORTED_STATE
    )
