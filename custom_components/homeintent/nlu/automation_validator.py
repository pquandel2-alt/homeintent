"""Automation Validator (HomeIntent V5 plan, Teil 6/10, V5.21-V5.22):
checks a fully-built ``AutomationModel`` against the plan's 10 named error
states before it may ever reach ``Automation -> Home Assistant`` (V5.21's
own pipeline: "Syntax -> Semantic -> Entity -> Area -> Capability ->
Parameter -> Temporal -> Logical -> Safety Validation").

Mirrors ``nlu/validator.py``'s own rationale for existing anyway even though
today's three automation parsers (trigger/condition/action) already
self-police most of this at parse time - they return ``None`` before a
``TriggerModel``/``ConditionNode``/``ActionModel`` is ever constructed
whenever a name doesn't resolve, resolves ambiguously, or lacks a required
capability (V5.20). Stated plainly, not hidden: because of that upstream
self-policing, a well-formed ``AutomationModel`` built by today's three
parsers structurally cannot exhibit AMBIGUOUS_ENTITY, ENTITY_NOT_FOUND,
UNKNOWN_TRIGGER/CONDITION/ACTION or UNSUPPORTED_CAPABILITY - the checks
below for them are defense-in-depth against a *future* caller that builds
an ``AutomationModel`` directly (the plan's own later LLM Adapter phase,
same reasoning ``nlu/validator.py``'s docstring gives; LLM output is always
untrusted input and will not self-police the way hassil-based parsers do).
The one check with no current upstream enforcement at all - and therefore
the most immediately valuable part of this module today - is
``INCOMPLETE_AUTOMATION``: nothing before this validator ever checks that a
constructed ``AutomationModel`` actually has at least one trigger and one
action.

UNSUPPORTED_CAPABILITY specifically can never be *returned* here (kept in
the enum only because V5.22 names it as one of the 10 states an automation
must never bypass): checking it for real requires live ``EntitySnapshot``
capability data this module - kept free of Home Assistant/hassil imports,
same boundary as ``automation_model.py``/``condition_model.py``/
``action_model.py`` - structurally cannot have. That check is already owned
by ``automation_action_parser.py``'s V5.20 capability gate (Regel 6, not
duplicated here).

Kept free of Home Assistant/hassil imports for the same reason its sibling
semantic-model modules are.
"""

from __future__ import annotations

from enum import Enum, auto

from .action_model import ActionGroup, ActionModel, ActionType
from .automation_operations import validate_registered_operation
from .automation_model import AutomationModel, NumericComparator, TriggerModel, TriggerTarget, TriggerType
from .condition_model import ConditionModel, ConditionNode, ConditionType, LogicalOperator
from .measurement import MeasurementProperty, is_valid_value, spec_for

# Reused verbatim from nlu/validator.py's own _CLIMATE_TEMPERATURE_MIN/MAX
# (Regel 6, no new bound invented) - already enforced at parse time by
# automation_action_parser.py's RangeSlotList(start=5, stop=30, ...), this
# is the same defense-in-depth re-check the module docstring explains.
_CLIMATE_TEMPERATURE_MIN = 5
_CLIMATE_TEMPERATURE_MAX = 30
_PERCENT_MIN = 0
_PERCENT_MAX = 100
# lexicon.py's _COLOR_TEMP_SLOT_LIST only ever maps "warmweiß"/"kaltweiß" to
# these two kelvin values - no third value the grammar can ever produce.
_COLOR_TEMP_KELVIN_VALUES = frozenset({2700, 6500})
# Home Assistant's own weekday trigger/condition abbreviations
# (automation_trigger_parser.py's/automation_condition_parser.py's weekday
# slot lists already only ever emit these).
_VALID_WEEKDAYS = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})
# Mirrors AutomationConditionParser's own default max_depth (its
# constructor's own docstring/argument) - re-checked here as the "Logical"
# pipeline stage's defense-in-depth equivalent.
_MAX_CONDITION_TREE_DEPTH = 3


class AutomationValidationError(Enum):
    """The 10 states V5.22 ("Automation Safety") names as the ones that must
    block ``Automation -> Home Assistant`` creation."""

    AMBIGUOUS_ENTITY = auto()
    ENTITY_NOT_FOUND = auto()
    UNKNOWN_TRIGGER = auto()
    UNKNOWN_CONDITION = auto()
    UNKNOWN_ACTION = auto()
    UNSUPPORTED_CAPABILITY = auto()
    INVALID_PARAMETER = auto()
    INVALID_TIME = auto()
    INVALID_LOGIC = auto()
    INCOMPLETE_AUTOMATION = auto()


def validate_automation(model: AutomationModel) -> AutomationValidationError | None:
    """The V5.21 pipeline itself: returns the first violated
    ``AutomationValidationError``, or ``None`` if ``model`` is safe to hand
    to a future HA generator (V5.25). There is no separate "Safety
    Validation" stage function - running this whole ordered pipeline to
    completion *is* the safety gate V5.22 asks for; "Syntax" has no check of
    its own either, since hassil's grammar-level ``recognize()`` already
    rejects anything not shaped like a sentence before a parser ever calls
    into these semantic-model constructors.
    """
    error = _validate_semantic(model)
    if error is not None:
        return error
    for trigger in model.triggers:
        error = _validate_trigger(trigger)
        if error is not None:
            return error
    for condition in model.conditions:
        error = _validate_condition_node(condition, depth=1)
        if error is not None:
            return error
    for action in model.actions:
        error = _validate_action_step(action)
        if error is not None:
            return error
    return None


def _validate_semantic(model: AutomationModel) -> AutomationValidationError | None:
    """INCOMPLETE_AUTOMATION - the one check nothing upstream performs today
    (see module docstring): a trigger-less or action-less ``AutomationModel``
    is a perfectly valid dataclass value, but never a usable automation."""
    if not model.triggers or not model.actions:
        return AutomationValidationError.INCOMPLETE_AUTOMATION
    if model.max_runs is not None and not (2 <= model.max_runs <= 10):
        return AutomationValidationError.INVALID_PARAMETER
    if model.once and model.max_runs is not None:
        return AutomationValidationError.INVALID_PARAMETER
    if (model.quiet_start_hour is None) != (model.quiet_end_hour is None):
        return AutomationValidationError.INVALID_PARAMETER
    if (
        model.quiet_start_hour is not None
        and model.quiet_end_hour is not None
        and (
        not 0 <= model.quiet_start_hour <= 23
        or not 0 <= model.quiet_end_hour <= 23
        or model.quiet_start_hour == model.quiet_end_hour
        or not model.once
        )
    ):
        return AutomationValidationError.INVALID_PARAMETER
    if any(trigger.type is TriggerType.CALENDAR_TIME for trigger in model.triggers):
        schedule = model.calendar_schedule
        if schedule is None:
            return AutomationValidationError.INVALID_TIME
        if not (0 <= schedule.hour <= 23 and 0 <= schedule.minute <= 59):
            return AutomationValidationError.INVALID_TIME
    return None


def _field_missing(obj: object, name: str) -> bool:
    value = getattr(obj, name)
    if isinstance(value, tuple):
        return not value
    return value is None


# Entity/Area stage: which fields a TriggerTarget-bearing leaf's *type* says
# it needs - a leaf whose declared type requires a field the payload never
# set is structurally malformed (UNKNOWN_TRIGGER/CONDITION/ACTION), the
# defense-in-depth equivalent of "grammar named this a STATE trigger but no
# constructor ever populated `state`".
_TRIGGER_REQUIRED_FIELDS: dict[TriggerType, tuple[str, ...]] = {
    TriggerType.STATE: ("target", "state"),
    TriggerType.NUMERIC_STATE: ("target", "comparator", "threshold"),
    TriggerType.DEVICE: ("device_id", "device_trigger_type"),
    TriggerType.PRESENCE: ("target", "zone_id"),
    TriggerType.SUN: ("sun_event",),
    TriggerType.TIME: ("time_hour",),
    TriggerType.RELATIVE_TIME: ("relative_offset_seconds",),
    TriggerType.CALENDAR_TIME: (),
    TriggerType.WEEKDAY: ("weekdays",),
    TriggerType.CALENDAR: ("calendar_entity_id", "calendar_event"),
}

_CONDITION_REQUIRED_FIELDS: dict[ConditionType, tuple[str, ...]] = {
    ConditionType.STATE: ("target", "state"),
    ConditionType.NUMERIC: ("target", "comparator", "threshold"),
    ConditionType.TIME: ("time_hour", "time_comparator"),
    ConditionType.DATE: ("date_month", "date_day"),
    ConditionType.WEEKDAY: ("weekdays",),
    ConditionType.SUN: ("sun_event", "sun_comparator"),
    ConditionType.PRESENCE: ("raw_state",),  # target is optional (None = whole-house "niemand zuhause")
    ConditionType.DEVICE: ("device_id", "device_condition_type"),
    ConditionType.ENTITY: ("target", "raw_state"),
    ConditionType.CALENDAR_EVENT: ("raw_state",),
    ConditionType.TEMPLATE: ("raw_state",),
}

_ACTION_REQUIRED_FIELDS: dict[ActionType, tuple[str, ...]] = {
    ActionType.TURN_ON: ("target",),
    ActionType.TURN_OFF: ("target",),
    ActionType.SET_BRIGHTNESS: ("target", "value"),
    ActionType.SET_COLOR: ("target", "color_name"),
    ActionType.SET_COLOR_TEMPERATURE: ("target", "color_temp_kelvin"),
    ActionType.SET_POSITION: ("target", "value"),
    ActionType.SET_TEMPERATURE: ("target", "value"),
    ActionType.SET_FAN_SPEED: ("target", "value"),
    ActionType.NOTIFY: ("message",),
    ActionType.DELAY: ("delay_seconds",),
    ActionType.WAIT: ("wait_condition",),
    ActionType.CHOOSE: ("if_condition", "then_steps"),
    ActionType.REGISTERED_SERVICE: ("target", "service_domain", "service_name"),
}

_PERCENT_ACTION_TYPES = frozenset({ActionType.SET_BRIGHTNESS, ActionType.SET_POSITION, ActionType.SET_FAN_SPEED})


def _validate_target(target: TriggerTarget | None) -> AutomationValidationError | None:
    """Entity/Area stage for any leaf that carries a ``TriggerTarget`` -
    structurally empty (neither domain nor entity_id set) can never come
    from today's parsers (Regel 4, they refuse rather than build one), so
    this is defense-in-depth, same as the module docstring explains."""
    if target is None:
        return None  # caller's _*_REQUIRED_FIELDS check already covers "target required but missing"
    if target.domain is None and target.entity_id is None and not target.entity_ids:
        return AutomationValidationError.ENTITY_NOT_FOUND
    if target.entity_id is not None and target.entity_ids:
        return AutomationValidationError.INVALID_PARAMETER
    if len(set(target.entity_ids)) != len(target.entity_ids):
        return AutomationValidationError.INVALID_PARAMETER
    if target.quantifier == "count" and (target.quantifier_count is None or target.quantifier_count < 1):
        return AutomationValidationError.INVALID_PARAMETER
    return None


def _validate_trigger(trigger: TriggerModel) -> AutomationValidationError | None:
    required = _TRIGGER_REQUIRED_FIELDS.get(trigger.type)
    if required is None or any(_field_missing(trigger, name) for name in required):
        return AutomationValidationError.UNKNOWN_TRIGGER
    error = _validate_target(trigger.target)
    if error is not None:
        return error
    if trigger.time_hour is not None and not (0 <= trigger.time_hour <= 23):
        return AutomationValidationError.INVALID_TIME
    if trigger.time_minute is not None and not (0 <= trigger.time_minute <= 59):
        return AutomationValidationError.INVALID_TIME
    if trigger.time_second is not None and not (0 <= trigger.time_second <= 59):
        return AutomationValidationError.INVALID_TIME
    if trigger.relative_offset_seconds is not None and trigger.relative_offset_seconds <= 0:
        return AutomationValidationError.INVALID_TIME
    if trigger.weekdays and not set(trigger.weekdays) <= _VALID_WEEKDAYS:
        return AutomationValidationError.INVALID_PARAMETER
    if trigger.delay_seconds is not None and trigger.delay_seconds < 0:
        return AutomationValidationError.INVALID_PARAMETER
    if trigger.for_seconds is not None and trigger.for_seconds <= 0:
        return AutomationValidationError.INVALID_PARAMETER
    if trigger.for_seconds is not None and trigger.type not in {
        TriggerType.STATE, TriggerType.NUMERIC_STATE
    }:
        return AutomationValidationError.INVALID_PARAMETER
    if trigger.repeat_within_seconds is not None and (
        trigger.repeat_within_seconds <= 0
        or trigger.type not in {TriggerType.STATE, TriggerType.NUMERIC_STATE}
        or trigger.for_seconds is not None
    ):
        return AutomationValidationError.INVALID_PARAMETER
    if trigger.trigger_id is not None and not trigger.trigger_id.strip():
        return AutomationValidationError.INVALID_PARAMETER
    if trigger.type is TriggerType.CALENDAR and trigger.calendar_event not in {"start", "end"}:
        return AutomationValidationError.INVALID_PARAMETER
    return _validate_measurement(trigger)


def _validate_measurement(trigger: TriggerModel) -> AutomationValidationError | None:
    """Attribute measurements are closed, typed and domain-bound."""
    if trigger.measurement is None:
        return (
            AutomationValidationError.INVALID_PARAMETER
            if trigger.direction is not None
            else None
        )
    if trigger.type is not TriggerType.NUMERIC_STATE or trigger.threshold is None:
        return AutomationValidationError.INVALID_PARAMETER
    spec = spec_for(trigger.measurement)
    if trigger.target is not None and trigger.target.domain not in {None, spec.domain}:
        return AutomationValidationError.INVALID_PARAMETER
    if not is_valid_value(trigger.measurement, trigger.threshold):
        return AutomationValidationError.INVALID_PARAMETER
    if trigger.direction is not None and trigger.measurement is not MeasurementProperty.COVER_POSITION:
        return AutomationValidationError.INVALID_PARAMETER
    return None


def _validate_condition_leaf(condition: ConditionModel) -> AutomationValidationError | None:
    required = _CONDITION_REQUIRED_FIELDS.get(condition.type)
    if required is None or any(_field_missing(condition, name) for name in required):
        return AutomationValidationError.UNKNOWN_CONDITION
    error = _validate_target(condition.target)
    if error is not None:
        return error
    if condition.comparator is not None and condition.comparator not in {
        NumericComparator.ABOVE, NumericComparator.BELOW
    }:
        # Conditions are generated as native numeric_state (strict bounds).
        return AutomationValidationError.INVALID_PARAMETER
    if condition.type is ConditionType.CALENDAR_EVENT and (
        len(condition.raw_state or "") > 100 or not (condition.raw_state or "").strip()
    ):
        return AutomationValidationError.INVALID_PARAMETER
    if condition.type is ConditionType.TEMPLATE and (
        len(condition.raw_state or "") > 500 or not (condition.raw_state or "").strip()
    ):
        return AutomationValidationError.INVALID_PARAMETER
    if condition.time_hour is not None and not (0 <= condition.time_hour <= 23):
        return AutomationValidationError.INVALID_TIME
    if condition.time_minute is not None and not (0 <= condition.time_minute <= 59):
        return AutomationValidationError.INVALID_TIME
    if condition.date_month is not None and not (1 <= condition.date_month <= 12):
        return AutomationValidationError.INVALID_TIME
    if condition.date_day is not None and not (1 <= condition.date_day <= 31):
        return AutomationValidationError.INVALID_TIME
    if condition.weekdays and not set(condition.weekdays) <= _VALID_WEEKDAYS:
        return AutomationValidationError.INVALID_PARAMETER
    return None


def _validate_condition_node(node: ConditionNode, depth: int) -> AutomationValidationError | None:
    """Logical stage (V5.21) - recurses ``ConditionNode``'s AND/OR/NOT tree.
    Mirrors ``condition_tree_depth()``'s own leaf/branch shape (Regel 6, not
    a second traversal)."""
    if depth > _MAX_CONDITION_TREE_DEPTH:
        return AutomationValidationError.INVALID_LOGIC
    if node.operator is None:
        if node.condition is None:
            return AutomationValidationError.INVALID_LOGIC  # leaf node carrying no condition - malformed
        return _validate_condition_leaf(node.condition)
    if node.operator is LogicalOperator.NOT:
        if len(node.children) != 1:
            return AutomationValidationError.INVALID_LOGIC  # NOT always has exactly one child
    elif not node.children:
        return AutomationValidationError.INVALID_LOGIC  # AND/OR with no children
    for child in node.children:
        error = _validate_condition_node(child, depth + 1)
        if error is not None:
            return error
    return None


def _validate_action_leaf(action: ActionModel) -> AutomationValidationError | None:
    required = _ACTION_REQUIRED_FIELDS.get(action.type)
    if required is None or any(_field_missing(action, name) for name in required):
        return AutomationValidationError.UNKNOWN_ACTION
    error = _validate_target(action.target)
    if error is not None:
        return error
    if action.type in _PERCENT_ACTION_TYPES and action.value is not None:
        if not (_PERCENT_MIN <= action.value <= _PERCENT_MAX):
            return AutomationValidationError.INVALID_PARAMETER
    if action.type is ActionType.SET_TEMPERATURE and action.value is not None:
        if not (_CLIMATE_TEMPERATURE_MIN <= action.value <= _CLIMATE_TEMPERATURE_MAX):
            return AutomationValidationError.INVALID_PARAMETER
    if action.type is ActionType.SET_COLOR_TEMPERATURE and action.color_temp_kelvin is not None:
        if action.color_temp_kelvin not in _COLOR_TEMP_KELVIN_VALUES:
            return AutomationValidationError.INVALID_PARAMETER
    if action.type is ActionType.REGISTERED_SERVICE and not validate_registered_operation(
        action.service_domain,
        action.service_name,
        action.service_data,
        (
            frozenset({action.target.domain})
            if action.target is not None and action.target.domain is not None
            else None
        ),
    ):
        return AutomationValidationError.INVALID_PARAMETER
    if action.delay_seconds is not None and action.delay_seconds < 0:
        return AutomationValidationError.INVALID_PARAMETER
    if action.timeout_seconds is not None and action.timeout_seconds < 0:
        return AutomationValidationError.INVALID_PARAMETER
    if action.duration_seconds is not None and action.duration_seconds < 0:
        return AutomationValidationError.INVALID_PARAMETER
    if action.type is ActionType.WAIT and action.wait_condition is not None:
        return _validate_condition_node(action.wait_condition, depth=1)
    if action.type is ActionType.CHOOSE:
        assert action.if_condition is not None
        error = _validate_condition_node(action.if_condition, depth=1)
        if error is not None:
            return error
        for step in (*action.then_steps, *action.else_steps):
            error = _validate_action_step(step)
            if error is not None:
                return error
    return None


def _validate_action_step(step: "ActionModel | ActionGroup") -> AutomationValidationError | None:
    if isinstance(step, ActionGroup):
        if not step.steps:
            return AutomationValidationError.INCOMPLETE_AUTOMATION  # an empty action group is malformed
        for child in step.steps:
            error = _validate_action_step(child)
            if error is not None:
                return error
        return None
    return _validate_action_leaf(step)
