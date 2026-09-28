"""V5 Wave 5 (V5.21-V5.22): nlu/automation_validator.py's validate_automation() -
a fully-built AutomationModel -> the first violated AutomationValidationError,
or None if safe to hand to a future HA generator.

This module is hass-free (see its own module docstring) - tests build
AutomationModel/TriggerModel/ConditionNode/ActionModel values directly,
mirroring tests/test_condition_model.py's/tests/test_action_model.py's
"exercise the dataclasses/pure functions directly" style rather than going
through a hassil parser.
"""

from __future__ import annotations

from homeintent.nlu.action_model import ActionGroup, ActionModel, ActionType, ExecutionMode
from homeintent.nlu.automation_model import (
    AutomationModel,
    NumericComparator,
    TriggerModel,
    TriggerTarget,
    TriggerType,
)
from homeintent.nlu.automation_validator import AutomationValidationError, validate_automation
from homeintent.nlu.condition_model import ConditionModel, ConditionNode, ConditionType, LogicalOperator
from homeintent.nlu.semantic_state import SemanticState

KUECHE_FENSTER_TARGET = TriggerTarget(domain="binary_sensor", device_class="window", area_id="kueche")
WOHNZIMMER_LICHT_TARGET = TriggerTarget(domain="light", area_id="wohnzimmer")

VALID_TRIGGER = TriggerModel(type=TriggerType.STATE, target=KUECHE_FENSTER_TARGET, state=SemanticState.OPEN)
VALID_ACTION = ActionModel(type=ActionType.TURN_ON, target=WOHNZIMMER_LICHT_TARGET)


# ============================================================================
# Section 1: Happy path (2 Tests)
# ============================================================================


def test_a_well_formed_automation_validates_clean():
    model = AutomationModel(triggers=(VALID_TRIGGER,), actions=(VALID_ACTION,), source_text="test")
    assert validate_automation(model) is None


def test_a_well_formed_automation_with_conditions_validates_clean():
    condition = ConditionNode(
        condition=ConditionModel(type=ConditionType.STATE, target=WOHNZIMMER_LICHT_TARGET, state=SemanticState.ON)
    )
    model = AutomationModel(triggers=(VALID_TRIGGER,), conditions=(condition,), actions=(VALID_ACTION,))
    assert validate_automation(model) is None


# ============================================================================
# Section 2: INCOMPLETE_AUTOMATION (V5.21/V5.22's one genuinely new,
# currently-reachable check - see module docstring) (3 Tests)
# ============================================================================


def test_no_triggers_is_incomplete():
    model = AutomationModel(triggers=(), actions=(VALID_ACTION,))
    assert validate_automation(model) is AutomationValidationError.INCOMPLETE_AUTOMATION


def test_no_actions_is_incomplete():
    model = AutomationModel(triggers=(VALID_TRIGGER,), actions=())
    assert validate_automation(model) is AutomationValidationError.INCOMPLETE_AUTOMATION


def test_empty_action_group_is_incomplete():
    empty_group = ActionGroup(mode=ExecutionMode.PARALLEL, steps=())
    model = AutomationModel(triggers=(VALID_TRIGGER,), actions=(empty_group,))
    assert validate_automation(model) is AutomationValidationError.INCOMPLETE_AUTOMATION


# ============================================================================
# Section 3: UNKNOWN_TRIGGER / UNKNOWN_CONDITION / UNKNOWN_ACTION -
# defense-in-depth against a directly-constructed (not parser-built) model
# missing a field its own type declares required (3 Tests)
# ============================================================================


def test_state_trigger_missing_state_is_unknown_trigger():
    bad_trigger = TriggerModel(type=TriggerType.STATE, target=KUECHE_FENSTER_TARGET)  # no state
    model = AutomationModel(triggers=(bad_trigger,), actions=(VALID_ACTION,))
    assert validate_automation(model) is AutomationValidationError.UNKNOWN_TRIGGER


def test_numeric_condition_missing_threshold_is_unknown_condition():
    bad_condition = ConditionNode(
        condition=ConditionModel(type=ConditionType.NUMERIC, target=WOHNZIMMER_LICHT_TARGET, comparator=NumericComparator.ABOVE)
    )
    model = AutomationModel(triggers=(VALID_TRIGGER,), conditions=(bad_condition,), actions=(VALID_ACTION,))
    assert validate_automation(model) is AutomationValidationError.UNKNOWN_CONDITION


def test_set_brightness_action_missing_value_is_unknown_action():
    bad_action = ActionModel(type=ActionType.SET_BRIGHTNESS, target=WOHNZIMMER_LICHT_TARGET)  # no value
    model = AutomationModel(triggers=(VALID_TRIGGER,), actions=(bad_action,))
    assert validate_automation(model) is AutomationValidationError.UNKNOWN_ACTION


# ============================================================================
# Section 4: ENTITY_NOT_FOUND - structurally empty target (1 Test)
# ============================================================================


def test_structurally_empty_target_is_entity_not_found():
    bad_trigger = TriggerModel(type=TriggerType.STATE, target=TriggerTarget(), state=SemanticState.OPEN)
    model = AutomationModel(triggers=(bad_trigger,), actions=(VALID_ACTION,))
    assert validate_automation(model) is AutomationValidationError.ENTITY_NOT_FOUND


# ============================================================================
# Section 5: INVALID_TIME (3 Tests)
# ============================================================================


def test_time_trigger_hour_out_of_range_is_invalid_time():
    bad_trigger = TriggerModel(type=TriggerType.TIME, time_hour=24, time_minute=0)
    model = AutomationModel(triggers=(bad_trigger,), actions=(VALID_ACTION,))
    assert validate_automation(model) is AutomationValidationError.INVALID_TIME


def test_time_trigger_second_out_of_range_is_invalid_time():
    bad_trigger = TriggerModel(
        type=TriggerType.TIME, time_hour=20, time_minute=0, time_second=60
    )
    model = AutomationModel(
        triggers=(bad_trigger,),
        actions=(ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(entity_id="light.kueche")),),
    )
    assert validate_automation(model) is AutomationValidationError.INVALID_TIME


def test_time_condition_minute_out_of_range_is_invalid_time():
    from homeintent.nlu.condition_model import TimeComparator

    bad_condition = ConditionNode(
        condition=ConditionModel(type=ConditionType.TIME, time_hour=10, time_minute=60, time_comparator=TimeComparator.AFTER)
    )
    model = AutomationModel(triggers=(VALID_TRIGGER,), conditions=(bad_condition,), actions=(VALID_ACTION,))
    assert validate_automation(model) is AutomationValidationError.INVALID_TIME


def test_date_condition_month_out_of_range_is_invalid_time():
    bad_condition = ConditionNode(condition=ConditionModel(type=ConditionType.DATE, date_month=13, date_day=1))
    model = AutomationModel(triggers=(VALID_TRIGGER,), conditions=(bad_condition,), actions=(VALID_ACTION,))
    assert validate_automation(model) is AutomationValidationError.INVALID_TIME


# ============================================================================
# Section 6: INVALID_PARAMETER (3 Tests)
# ============================================================================


def test_brightness_value_above_100_is_invalid_parameter():
    bad_action = ActionModel(type=ActionType.SET_BRIGHTNESS, target=WOHNZIMMER_LICHT_TARGET, value=150.0)
    model = AutomationModel(triggers=(VALID_TRIGGER,), actions=(bad_action,))
    assert validate_automation(model) is AutomationValidationError.INVALID_PARAMETER


def test_climate_temperature_out_of_bounds_is_invalid_parameter():
    bad_action = ActionModel(type=ActionType.SET_TEMPERATURE, target=TriggerTarget(domain="climate"), value=40.0)
    model = AutomationModel(triggers=(VALID_TRIGGER,), actions=(bad_action,))
    assert validate_automation(model) is AutomationValidationError.INVALID_PARAMETER


def test_color_temperature_kelvin_outside_the_closed_vocabulary_is_invalid_parameter():
    bad_action = ActionModel(
        type=ActionType.SET_COLOR_TEMPERATURE, target=WOHNZIMMER_LICHT_TARGET, color_temp_kelvin=3000
    )
    model = AutomationModel(triggers=(VALID_TRIGGER,), actions=(bad_action,))
    assert validate_automation(model) is AutomationValidationError.INVALID_PARAMETER


# ============================================================================
# Section 7: INVALID_LOGIC (3 Tests)
# ============================================================================


def test_and_node_with_no_children_is_invalid_logic():
    bad_condition = ConditionNode(operator=LogicalOperator.AND, children=())
    model = AutomationModel(triggers=(VALID_TRIGGER,), conditions=(bad_condition,), actions=(VALID_ACTION,))
    assert validate_automation(model) is AutomationValidationError.INVALID_LOGIC


def test_not_node_with_two_children_is_invalid_logic():
    leaf = ConditionNode(condition=ConditionModel(type=ConditionType.STATE, target=WOHNZIMMER_LICHT_TARGET, state=SemanticState.ON))
    bad_condition = ConditionNode(operator=LogicalOperator.NOT, children=(leaf, leaf))
    model = AutomationModel(triggers=(VALID_TRIGGER,), conditions=(bad_condition,), actions=(VALID_ACTION,))
    assert validate_automation(model) is AutomationValidationError.INVALID_LOGIC


def test_condition_tree_deeper_than_max_depth_is_invalid_logic():
    leaf = ConditionNode(condition=ConditionModel(type=ConditionType.STATE, target=WOHNZIMMER_LICHT_TARGET, state=SemanticState.ON))
    depth_2 = ConditionNode(operator=LogicalOperator.NOT, children=(leaf,))
    depth_3 = ConditionNode(operator=LogicalOperator.NOT, children=(depth_2,))
    depth_4 = ConditionNode(operator=LogicalOperator.NOT, children=(depth_3,))  # exceeds _MAX_CONDITION_TREE_DEPTH=3
    model = AutomationModel(triggers=(VALID_TRIGGER,), conditions=(depth_4,), actions=(VALID_ACTION,))
    assert validate_automation(model) is AutomationValidationError.INVALID_LOGIC


# ============================================================================
# Section 8: Pipeline ordering - first violation wins (1 Test)
# ============================================================================


def test_semantic_stage_runs_before_any_trigger_or_action_is_inspected():
    """An automation that's both incomplete (no actions) AND has a malformed
    trigger must report INCOMPLETE_AUTOMATION, not UNKNOWN_TRIGGER - Semantic
    (V5.21's pipeline order) runs first."""
    bad_trigger = TriggerModel(type=TriggerType.STATE, target=KUECHE_FENSTER_TARGET)  # no state
    model = AutomationModel(triggers=(bad_trigger,), actions=())
    assert validate_automation(model) is AutomationValidationError.INCOMPLETE_AUTOMATION
