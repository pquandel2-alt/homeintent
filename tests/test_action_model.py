"""V5 Wave 3 (V5.7-V5.12): nlu/action_model.py's ActionModel/ActionGroup type
system. Hass-free, no parser involved (mirrors tests/test_condition_model.py's
style).

Pure construction/equality/render tests only - nothing here is wired into
automation_action_parser.py's caller graph beyond direct construction. These
tests pin the dataclass shapes/defaults, the leaf-vs-group AST structure, and
the render_action_step() debug output.
"""

from __future__ import annotations

from homeintent.nlu.action_model import ActionGroup, ActionModel, ActionType, ExecutionMode, render_action_step
from homeintent.nlu.automation_model import TriggerTarget
from homeintent.nlu.condition_model import ConditionModel, ConditionNode, ConditionType
from homeintent.nlu.semantic_state import SemanticState


# ============================================================================
# Section 1: Enums (2 Tests)
# ============================================================================


def test_action_type_covers_all_documented_types():
    """ActionType includes the original leaves plus native IF/THEN/ELSE."""
    assert {t.name for t in ActionType} == {
        "TURN_ON",
        "TURN_OFF",
        "SET_BRIGHTNESS",
        "SET_COLOR",
        "SET_COLOR_TEMPERATURE",
        "SET_POSITION",
        "SET_TEMPERATURE",
        "SET_FAN_SPEED",
        "NOTIFY",
        "DELAY",
        "WAIT",
        "CHOOSE",
        "REGISTERED_SERVICE",
        # 7.9 W5: bounded repetition and escalation - new typed actions,
        # generated to HA repeat/wait_template only from these fields.
        "REPEAT",
        "ESCALATE",
    }


def test_execution_mode_covers_sequential_and_parallel():
    """ExecutionMode has SEQUENTIAL, PARALLEL (V5.9)."""
    assert {m.name for m in ExecutionMode} == {"SEQUENTIAL", "PARALLEL"}


# ============================================================================
# Section 2: ActionModel Field Abdeckung (11 Tests, one per type)
# ============================================================================


def test_action_model_turn_on_carries_type_target_and_optional_duration():
    """TURN_ON: type, target, duration_seconds (Duration Engine, V5.11)."""
    target = TriggerTarget(domain="light", area_id="wohnzimmer")
    action = ActionModel(type=ActionType.TURN_ON, target=target, duration_seconds=1800)
    assert action.type is ActionType.TURN_ON
    assert action.target is target
    assert action.duration_seconds == 1800
    # Other fields stay at defaults
    assert action.value is None
    assert action.color_name is None


def test_action_model_turn_off_carries_type_and_target():
    """TURN_OFF: type, target - no duration (revert-after only makes sense from on/open)."""
    target = TriggerTarget(domain="cover", area_id="wohnzimmer")
    action = ActionModel(type=ActionType.TURN_OFF, target=target)
    assert action.type is ActionType.TURN_OFF
    assert action.target is target
    assert action.duration_seconds is None


def test_action_model_set_brightness_carries_type_target_and_value():
    """SET_BRIGHTNESS: type, target, value (percent 0-100)."""
    target = TriggerTarget(domain="light", entity_id="light.flur")
    action = ActionModel(type=ActionType.SET_BRIGHTNESS, target=target, value=50.0)
    assert action.type is ActionType.SET_BRIGHTNESS
    assert action.target is target
    assert action.value == 50.0
    assert action.color_name is None


def test_action_model_set_color_carries_type_target_and_color_name():
    """SET_COLOR: type, target, color_name (_COLOR_SLOT_LIST vocabulary)."""
    target = TriggerTarget(domain="light", area_id="wohnzimmer")
    action = ActionModel(type=ActionType.SET_COLOR, target=target, color_name="red")
    assert action.type is ActionType.SET_COLOR
    assert action.target is target
    assert action.color_name == "red"
    assert action.value is None


def test_action_model_set_color_temperature_carries_type_target_and_kelvin():
    """SET_COLOR_TEMPERATURE: type, target, color_temp_kelvin."""
    target = TriggerTarget(domain="light", area_id="wohnzimmer")
    action = ActionModel(type=ActionType.SET_COLOR_TEMPERATURE, target=target, color_temp_kelvin=2700)
    assert action.type is ActionType.SET_COLOR_TEMPERATURE
    assert action.target is target
    assert action.color_temp_kelvin == 2700
    assert action.color_name is None


def test_action_model_set_position_carries_type_target_and_value():
    """SET_POSITION: type, target, value (cover position percent)."""
    target = TriggerTarget(domain="cover", area_id="wohnzimmer")
    action = ActionModel(type=ActionType.SET_POSITION, target=target, value=30.0)
    assert action.type is ActionType.SET_POSITION
    assert action.target is target
    assert action.value == 30.0


def test_action_model_set_temperature_carries_type_target_and_value():
    """SET_TEMPERATURE: type, target, value (degrees)."""
    target = TriggerTarget(domain="climate", area_id="wohnzimmer")
    action = ActionModel(type=ActionType.SET_TEMPERATURE, target=target, value=21.0)
    assert action.type is ActionType.SET_TEMPERATURE
    assert action.target is target
    assert action.value == 21.0


def test_action_model_set_fan_speed_carries_type_target_and_value():
    """SET_FAN_SPEED: type, target, value (percent)."""
    target = TriggerTarget(domain="fan", area_id="wohnzimmer")
    action = ActionModel(type=ActionType.SET_FAN_SPEED, target=target, value=70.0)
    assert action.type is ActionType.SET_FAN_SPEED
    assert action.target is target
    assert action.value == 70.0


def test_action_model_notify_carries_type_and_message_no_target():
    """NOTIFY: type, message - no target (standalone, not domain-bound)."""
    action = ActionModel(type=ActionType.NOTIFY, message="die Waschmaschine ist fertig")
    assert action.type is ActionType.NOTIFY
    assert action.message == "die Waschmaschine ist fertig"
    assert action.target is None


def test_action_model_delay_carries_type_and_delay_seconds_no_target():
    """DELAY: type, delay_seconds - no target (Delay Engine, V5.10)."""
    action = ActionModel(type=ActionType.DELAY, delay_seconds=300)
    assert action.type is ActionType.DELAY
    assert action.delay_seconds == 300
    assert action.target is None


def test_action_model_wait_carries_type_wait_condition_and_optional_timeout():
    """WAIT: type, wait_condition (reused ConditionNode), timeout_seconds (V5.12)."""
    condition = ConditionNode(condition=ConditionModel(type=ConditionType.STATE, state=SemanticState.ON))
    action = ActionModel(type=ActionType.WAIT, wait_condition=condition, timeout_seconds=1800)
    assert action.type is ActionType.WAIT
    assert action.wait_condition is condition
    assert action.timeout_seconds == 1800
    assert action.target is None


def test_action_model_only_requires_type_everything_else_defaults():
    """ActionModel requires only type, rest default to None."""
    action = ActionModel(type=ActionType.DELAY)
    assert action.type is ActionType.DELAY
    assert action.target is None
    assert action.value is None
    assert action.color_name is None
    assert action.color_temp_kelvin is None
    assert action.duration_seconds is None
    assert action.message is None
    assert action.delay_seconds is None
    assert action.wait_condition is None
    assert action.timeout_seconds is None


# ============================================================================
# Section 3: ActionGroup Leaf vs Branch (3 Tests)
# ============================================================================


def test_action_group_sequential_holds_ordered_steps():
    """SEQUENTIAL group: mode, steps tuple - default shape (never wraps the
    top-level AutomationModel.actions tuple, only nested/explicit groups)."""
    step1 = ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(domain="light"))
    step2 = ActionModel(type=ActionType.TURN_OFF, target=TriggerTarget(domain="cover"))
    group = ActionGroup(mode=ExecutionMode.SEQUENTIAL, steps=(step1, step2))
    assert group.mode is ExecutionMode.SEQUENTIAL
    assert group.steps == (step1, step2)


def test_action_group_parallel_holds_steps_that_run_together():
    """PARALLEL group (V5.9) - explicit "gleichzeitig" branch."""
    step1 = ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(domain="light"))
    step2 = ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(domain="cover"))
    group = ActionGroup(mode=ExecutionMode.PARALLEL, steps=(step1, step2))
    assert group.mode is ExecutionMode.PARALLEL
    assert len(group.steps) == 2


def test_action_group_can_nest_another_action_group():
    """A PARALLEL group's steps may contain a nested ActionGroup (e.g. a
    short sequential sub-sequence inside a parallel branch)."""
    inner_step1 = ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(domain="light"))
    inner_step2 = ActionModel(type=ActionType.DELAY, delay_seconds=60)
    inner_group = ActionGroup(mode=ExecutionMode.SEQUENTIAL, steps=(inner_step1, inner_step2))
    outer_step = ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(domain="cover"))
    outer_group = ActionGroup(mode=ExecutionMode.PARALLEL, steps=(inner_group, outer_step))
    assert outer_group.steps[0] is inner_group
    assert isinstance(outer_group.steps[0], ActionGroup)
    assert outer_group.steps[1] is outer_step


# ============================================================================
# Section 4: render_action_step() (4 Tests)
# ============================================================================


def test_render_action_step_single_turn_on_leaf():
    """Single TURN_ON leaf renders as "TURN_ON(...)"."""
    target = TriggerTarget(domain="light", area_id="wohnzimmer")
    action = ActionModel(type=ActionType.TURN_ON, target=target, duration_seconds=1800)
    output = render_action_step(action)
    assert "TURN_ON(" in output
    assert "domain=light" in output
    assert "area_id=wohnzimmer" in output
    assert "duration_seconds=1800" in output


def test_render_action_step_wait_embeds_rendered_condition():
    """WAIT's rendered output embeds the wrapped condition's own rendering
    (reuses render_condition_tree(), not a second renderer, Regel 6)."""
    condition = ConditionNode(condition=ConditionModel(type=ConditionType.STATE, state=SemanticState.ON))
    action = ActionModel(type=ActionType.WAIT, wait_condition=condition, timeout_seconds=600)
    output = render_action_step(action)
    assert "WAIT(" in output
    assert "wait_condition=(" in output
    assert "STATE(" in output
    assert "timeout_seconds=600" in output


def test_render_action_step_sequential_group_shows_indentation():
    """SEQUENTIAL group renders with a header and indented children,
    mirrors render_condition_tree()'s AND/OR rendering."""
    step1 = ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(domain="light"))
    step2 = ActionModel(type=ActionType.TURN_OFF, target=TriggerTarget(domain="cover"))
    group = ActionGroup(mode=ExecutionMode.SEQUENTIAL, steps=(step1, step2))

    output = render_action_step(group)
    lines = output.split("\n")
    assert lines[0] == "SEQUENTIAL"
    assert lines[1].startswith("  ") and "TURN_ON(" in lines[1]
    assert lines[2].startswith("  ") and "TURN_OFF(" in lines[2]


def test_render_action_step_nested_parallel_shows_nested_indentation():
    """Nested group renders with proper nesting levels."""
    inner_step1 = ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(domain="light"))
    inner_step2 = ActionModel(type=ActionType.DELAY, delay_seconds=60)
    inner_group = ActionGroup(mode=ExecutionMode.SEQUENTIAL, steps=(inner_step1, inner_step2))
    outer_step = ActionModel(type=ActionType.TURN_ON, target=TriggerTarget(domain="cover"))
    outer_group = ActionGroup(mode=ExecutionMode.PARALLEL, steps=(inner_group, outer_step))

    output = render_action_step(outer_group)
    lines = output.split("\n")
    assert lines[0] == "PARALLEL"
    assert lines[1] == "  SEQUENTIAL"
    assert lines[2].startswith("    ") and "TURN_ON(" in lines[2]
    assert lines[3].startswith("    ") and "DELAY(" in lines[3]
    assert lines[4].startswith("  ") and "TURN_ON(" in lines[4]


# ============================================================================
# Section 5: Frozen & Immutability (1 Test)
# ============================================================================


def test_action_model_and_action_group_are_frozen():
    """Both ActionModel and ActionGroup are frozen=True dataclasses."""
    action = ActionModel(type=ActionType.TURN_ON)
    try:
        action.type = ActionType.TURN_OFF  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("ActionModel must be frozen")

    group = ActionGroup(mode=ExecutionMode.SEQUENTIAL, steps=(action,))
    try:
        group.mode = ExecutionMode.PARALLEL  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("ActionGroup must be frozen")
