"""HA Automation Generator (HomeIntent V5 Teil 7/10, V5.25): the pure
translator from an already-``validate_automation()``-clean ``AutomationModel``
into a Home Assistant-native automation config dict - the exact shape
Home Assistant's own ``config/automation`` HTTP view
(``EditAutomationConfigView._write_value``, upstream ``homeassistant/
components/config/automation.py``) writes into ``automations.yaml``: modern
plural schema keys (``triggers``/``conditions``/``actions``, ``trigger:``/
``condition:``/``action:`` renamed from the legacy ``platform:``/``service:``
inside each item - confirmed against the real upstream source, not guessed
from memory, since getting this wrong risks producing YAML Home Assistant's
own config loader rejects or silently misinterprets).

Pure translation only, no live HA/hassil imports (same ``nlu/`` boundary
``automation_validator.py`` already keeps) - the only "live" piece is
resolving an already-parsed ``TriggerTarget``'s domain/device_class/area
back into concrete ``entity_id``s against the fresh entity list the caller
passes in (``nlu/constraint_resolver.py``'s ``resolve_candidates``, Regel 6 -
the exact same resolution ``automation_action_parser.py``'s own
``_candidates_for_target`` already performs; duplicated here in miniature
rather than imported, since that parser lives outside ``nlu/`` - same
cross-boundary duplication precedent ``automation_model.py``'s/
``action_model.py``'s ``_render_target`` already sets).

Regel 4 ("niemals raten") applied one layer deeper than anywhere else in this
codebase: a handful of ``TriggerType``/``ConditionType``/``ActionType``
values have no faithful, non-invented Home Assistant native-schema
translation given what this project's semantic model actually captures -
``TriggerType.DEVICE``/``ConditionType.DEVICE`` (HA's ``device`` trigger/
condition platforms require an integration-specific ``domain``/``subtype``
this project's ``DeviceSnapshot`` never records), a bare ``TriggerType.WEEKDAY``
(Home Assistant has no standalone "this weekday" trigger platform - only
``condition: time``'s ``weekday`` field, which is why ``ConditionType.WEEKDAY``
*is* translated below). ``ConditionType.DATE`` is emitted as one bounded
``now().month/day`` template. ``ActionType.WAIT`` uses the same private,
bounded condition compiler and never invents integration-specific data.
Each returns a specific ``GenerationError`` member instead of a best-effort
guess - the caller (the not-yet-written Executor/``conversation.py`` wiring)
speaks this back to the user and persists nothing, exactly the same
"refuse, never silently do something wrong" contract
``AutomationValidationError`` already established one stage earlier.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace, dataclass
from enum import Enum, auto
from typing import Any, Callable

from ..entities import EntitySnapshot
from .action_model import ActionGroup, ActionModel, ActionType, ExecutionMode
from .automation_model import (
    AutomationModel,
    NumericComparator,
    PresenceEvent,
    SunEvent,
    TriggerModel,
    TriggerTarget,
    TriggerType,
)
from .condition_model import ConditionModel, ConditionNode, ConditionType, LogicalOperator, TimeComparator
from .constraint_resolver import Constraints, resolve_candidates
from .measurement import (
    arrival_condition,
    comparison_expression,
    direction_condition,
    safe_entity_id,
    spec_for,
)
from .semantic_state import SemanticState


class GenerationError(Enum):
    """Mirrors ``AutomationValidationError``'s "refuse, don't guess" shape,
    one translation stage later (semantic model -> HA-native schema, not
    spoken sentence -> semantic model)."""

    ENTITY_NOT_FOUND = auto()  # target re-resolved against the live entity list yields zero matches
    UNSUPPORTED_STATE = auto()  # a SemanticState this domain/device_class combination cannot express
    UNSUPPORTED_TRIGGER_TYPE = auto()  # DEVICE, bare WEEKDAY - see module docstring
    UNSUPPORTED_CONDITION_TYPE = auto()  # DEVICE, DATE - see module docstring
    UNSUPPORTED_ACTION_TYPE = auto()  # WAIT - see module docstring
    NOTIFY_RECIPIENT_UNRESOLVED = auto()  # "mich"/"uns" not materialized into exact notify targets


@dataclass(frozen=True)
class GenerationResult:
    """``config`` is the full Home Assistant automation dict (minus ``id`` -
    assigning that is the Executor's job, mirroring the real
    ``EditAutomationConfigView`` where the id is decided by the config-view
    caller, not ``_write_value`` itself) when ``error`` is ``None``; ``None``
    otherwise. Same "exactly one of the two is set" contract
    ``AutomationMatchResult``'s ``validation_error`` already follows."""

    config: dict[str, Any] | None
    error: GenerationError | None


# Reverse of ``semantic_state.py``'s ``derive_semantic_state()`` (that
# module's own tables are private/module-local, and this is the only other
# place in the codebase needing the inverse direction - small, stable,
# duplicated rather than imported, same precedent
# ``automation_model.py``'s/``action_model.py``'s ``_render_target`` already
# sets for cross-``nlu/``-module duplication).
_BINARY_SENSOR_OPEN_CLOSE_DEVICE_CLASSES = frozenset({"window", "door", "opening", "garage_door"})
_ON_OFF_DOMAINS = frozenset({"light", "switch", "fan"})


def _raw_state_for_semantic(domain: str | None, device_class: str | None, state: SemanticState) -> str | None:
    if domain == "cover":
        return {SemanticState.OPEN: "open", SemanticState.CLOSED: "closed"}.get(state)
    if domain == "binary_sensor":
        if device_class in _BINARY_SENSOR_OPEN_CLOSE_DEVICE_CLASSES:
            return {SemanticState.OPEN: "on", SemanticState.CLOSED: "off"}.get(state)
        return {SemanticState.ON: "on", SemanticState.OFF: "off"}.get(state)
    if domain in _ON_OFF_DOMAINS:
        return {SemanticState.ON: "on", SemanticState.OFF: "off"}.get(state)
    return None


def _entity_id_field(entities: list[EntitySnapshot]) -> str | list[str]:
    """A bare string for a single entity, a list for multiple - mirrors
    ``service_call.py``'s own ``_entity_id_field`` exactly (same
    cross-boundary duplication reasoning as the module docstring)."""
    ids = [e.entity_id for e in entities]
    return ids[0] if len(ids) == 1 else ids


def _resolve_target_entities(target: TriggerTarget, entities: list[EntitySnapshot]) -> list[EntitySnapshot]:
    """Mirrors ``automation_action_parser.py``'s own ``_candidates_for_target``
    (Regel 6 in spirit - same resolution logic, duplicated in miniature
    because that parser lives outside ``nlu/`` and this module must stay
    inside it, same boundary every other ``nlu/`` module already respects)."""
    if target.entity_id is not None:
        return [e for e in entities if e.entity_id == target.entity_id]
    if target.entity_ids:
        requested = set(target.entity_ids)
        return [e for e in entities if e.entity_id in requested]
    candidates = resolve_candidates(
        entities,
        Constraints(
            domain=target.domain,
            device_class=target.device_class,
            area_id=target.area_id,
            floor_id=target.floor_id,
        ),
    )
    if target.exclude_entity_ids:
        candidates = [e for e in candidates if e.entity_id not in target.exclude_entity_ids]
    return candidates


def resolve_automation_action_entity_ids(
    model: AutomationModel, entities: list[EntitySnapshot]
) -> frozenset[str]:
    """Return every concrete entity an automation action may control.

    Conditions and triggers are intentionally excluded because they only read
    state.  CHOOSE branches are both included: either branch can become the
    active write path at runtime.
    """
    resolved: set[str] = set()

    def visit(step: ActionModel | ActionGroup) -> None:
        if isinstance(step, ActionGroup):
            for child in step.steps:
                visit(child)
            return
        if step.target is not None:
            resolved.update(
                entity.entity_id
                for entity in _resolve_target_entities(step.target, entities)
            )
        if step.type in {ActionType.CHOOSE, ActionType.REPEAT, ActionType.ESCALATE}:
            for child in (*step.then_steps, *step.else_steps):
                visit(child)

    for action in model.actions:
        visit(action)
    return frozenset(resolved)


def _format_offset(minutes: int) -> str:
    """Minutes (already signed - negative = before, positive = after, see
    ``TriggerModel.offset_minutes``' own comment) as the "±HH:MM:SS" string
    Home Assistant's ``cv.time_period``-based offset fields accept."""
    sign = "-" if minutes < 0 else ""
    total_seconds = abs(minutes) * 60
    hours, remainder = divmod(total_seconds, 3600)
    mins, secs = divmod(remainder, 60)
    return f"{sign}{hours:02d}:{mins:02d}:{secs:02d}"


# Relations Home Assistant's numeric_state trigger cannot express exactly:
# equality and inclusive bounds. They become template triggers, which fire on
# the false -> true transition of the comparison.
_TEMPLATE_COMPARATORS: dict[NumericComparator, str] = {
    NumericComparator.EQUAL: "==",
    NumericComparator.AT_LEAST: ">=",
    NumericComparator.AT_MOST: "<=",
}


def _generate_measurement_trigger(
    trigger: TriggerModel,
    candidates: list[EntitySnapshot],
    identified: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any] | None, GenerationError | None]:
    """Attribute-backed percentage (cover position, brightness, fan speed).

    The template is assembled only from the closed ``MeasurementProperty``
    mapping, validated entity ids and a number - never from spoken text.
    """
    assert trigger.measurement is not None and trigger.comparator is not None
    assert trigger.threshold is not None
    spec = spec_for(trigger.measurement)
    if any(entity.domain != spec.domain for entity in candidates):
        return None, GenerationError.UNSUPPORTED_STATE
    entity_ids = [entity.entity_id for entity in candidates]
    if trigger.direction is not None:
        # Direction needs the previous value, which only a state trigger on
        # the attribute provides; the arrival edge and the travel sense are
        # checked by the conditions from ``_trigger_conditions``.
        config: dict[str, Any] = {
            "trigger": "state",
            "entity_id": _entity_id_field(candidates),
            "attribute": spec.attribute,
        }
    else:
        config = {
            "trigger": "template",
            "value_template": comparison_expression(
                trigger.measurement, entity_ids, trigger.comparator.name, trigger.threshold
            ),
        }
    if trigger.for_seconds is not None:
        config["for"] = {"seconds": trigger.for_seconds}
    return identified(config), None


def _trigger_conditions(trigger: TriggerModel) -> list[dict[str, Any]]:
    """Automation conditions a trigger's own meaning requires."""
    if trigger.measurement is None or trigger.direction is None:
        return []
    assert trigger.comparator is not None and trigger.threshold is not None
    return [
        {
            "condition": "template",
            "value_template": arrival_condition(
                trigger.measurement, trigger.comparator.name, trigger.threshold
            ),
        },
        {
            "condition": "template",
            "value_template": direction_condition(trigger.measurement, trigger.direction),
        },
    ]


def _generate_trigger(trigger: TriggerModel, entities: list[EntitySnapshot]) -> tuple[dict[str, Any] | None, GenerationError | None]:
    def identified(config: dict[str, Any]) -> dict[str, Any]:
        if trigger.trigger_id is not None:
            config["id"] = trigger.trigger_id
        return config

    if trigger.type is TriggerType.STATE:
        assert trigger.target is not None and trigger.state is not None
        candidates = _resolve_target_entities(trigger.target, entities)
        if not candidates:
            return None, GenerationError.ENTITY_NOT_FOUND
        domain = trigger.target.domain or candidates[0].domain
        device_class = trigger.target.device_class or candidates[0].device_class
        if trigger.raw_to:
            config = {
                "trigger": "state",
                "entity_id": _entity_id_field(candidates),
                "to": list(trigger.raw_to),
            }
            if trigger.for_seconds is not None:
                config["for"] = {"seconds": trigger.for_seconds}
            return identified(config), None
        to_raw = _raw_state_for_semantic(domain, device_class, trigger.state)
        if to_raw is None:
            return None, GenerationError.UNSUPPORTED_STATE
        config = {"trigger": "state", "entity_id": _entity_id_field(candidates), "to": to_raw}
        if trigger.for_seconds is not None:
            config["for"] = {"seconds": trigger.for_seconds}
        return identified(config), None

    if trigger.type is TriggerType.NUMERIC_STATE:
        assert trigger.target is not None and trigger.comparator is not None and trigger.threshold is not None
        candidates = _resolve_target_entities(trigger.target, entities)
        if not candidates:
            return None, GenerationError.ENTITY_NOT_FOUND
        entity_ids = [entity.entity_id for entity in candidates]
        if not all(safe_entity_id(entity_id) for entity_id in entity_ids):
            return None, GenerationError.ENTITY_NOT_FOUND
        if trigger.measurement is not None:
            return _generate_measurement_trigger(trigger, candidates, identified)
        if trigger.comparator in _TEMPLATE_COMPARATORS:
            operator = _TEMPLATE_COMPARATORS[trigger.comparator]
            if trigger.comparator is NumericComparator.EQUAL:
                # ``None == 50`` is simply false for a non-numeric state.
                comparisons = [
                    f"states('{entity_id}') | float(none) == {trigger.threshold:g}"
                    for entity_id in entity_ids
                ]
            else:
                # Ordering against ``None`` would raise; guard with is_number.
                comparisons = [
                    f"(states('{entity_id}') | is_number and "
                    f"states('{entity_id}') | float {operator} {trigger.threshold:g})"
                    for entity_id in entity_ids
                ]
            config = {
                "trigger": "template",
                "value_template": "{{ " + " or ".join(comparisons) + " }}",
            }
            if trigger.for_seconds is not None:
                config["for"] = {"seconds": trigger.for_seconds}
            return identified(config), None
        key = "above" if trigger.comparator is NumericComparator.ABOVE else "below"
        config = {"trigger": "numeric_state", "entity_id": _entity_id_field(candidates), key: trigger.threshold}
        if trigger.for_seconds is not None:
            config["for"] = {"seconds": trigger.for_seconds}
        return identified(config), None

    if trigger.type is TriggerType.PRESENCE:
        assert trigger.target is not None
        if trigger.target.entity_id is None or not safe_entity_id(trigger.target.entity_id):
            # "ich" not yet bound to the speaker's person entity - never guess.
            return None, GenerationError.ENTITY_NOT_FOUND
        config = {"trigger": "state", "entity_id": trigger.target.entity_id}
        if trigger.presence_event is PresenceEvent.ARRIVE:
            config["to"] = "home"
        elif trigger.presence_event is PresenceEvent.LEAVE:
            config["from"] = "home"
        # Without a spoken direction the trigger stays direction-neutral, the
        # historical meaning (see automation_preview.py's PRESENCE wording).
        return identified(config), None

    if trigger.type is TriggerType.WEATHER:
        # 7.9.3 B1: the forecast is read in the automation itself (see
        # ``weather.guard_steps``); the trigger only looks regularly.
        from ..weather import CHECK_MINUTES

        if trigger.target is None or trigger.target.entity_id is None or not safe_entity_id(
            trigger.target.entity_id
        ) or not trigger.target.entity_id.startswith("weather."):
            return None, GenerationError.ENTITY_NOT_FOUND
        return identified({"trigger": "time_pattern", "minutes": f"/{CHECK_MINUTES}"}), None

    if trigger.type is TriggerType.SUN:
        assert trigger.sun_event is not None
        event = "sunrise" if trigger.sun_event is SunEvent.SUNRISE else "sunset"
        config: dict[str, Any] = {"trigger": "sun", "event": event}
        if trigger.offset_minutes is not None:
            config["offset"] = _format_offset(trigger.offset_minutes)
        return identified(config), None

    if trigger.type is TriggerType.TIME:
        assert trigger.time_hour is not None
        minute = trigger.time_minute or 0
        second = trigger.time_second or 0
        return identified({"trigger": "time", "at": f"{trigger.time_hour:02d}:{minute:02d}:{second:02d}"}), None

    if trigger.type is TriggerType.CALENDAR:
        assert trigger.calendar_entity_id is not None and trigger.calendar_event is not None
        if not any(
            entity.entity_id == trigger.calendar_entity_id and entity.domain == "calendar"
            for entity in entities
        ):
            return None, GenerationError.ENTITY_NOT_FOUND
        config = {
            "trigger": "calendar",
            "entity_id": trigger.calendar_entity_id,
            "event": trigger.calendar_event,
        }
        if trigger.offset_minutes is not None:
            config["offset"] = _format_offset(trigger.offset_minutes)
        return identified(config), None

    if trigger.type is TriggerType.RELATIVE_TIME:
        # Preview-only. conversation.py must anchor the offset to the
        # confirmation time before generation.
        return None, GenerationError.UNSUPPORTED_TRIGGER_TYPE

    # TriggerType.DEVICE / TriggerType.WEEKDAY - see module docstring.
    return None, GenerationError.UNSUPPORTED_TRIGGER_TYPE


_ENTITY_ID_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def unchanged_today_template(
    condition: ConditionModel, entities: list[EntitySnapshot]
) -> tuple[str | None, GenerationError | None]:
    """"bis 10 Uhr keine Bewegung im Bad" (7.9 W2): every entity is in its
    rest state and has not changed since local midnight.  Built only from
    validated entity ids and the closed raw-state vocabulary - never from
    user text.  Home Assistant resets ``last_changed`` on a restart, which
    the preview says."""
    if condition.target is None or condition.state is None:
        return None, GenerationError.UNSUPPORTED_CONDITION_TYPE
    candidates = _resolve_target_entities(condition.target, entities)
    if not candidates:
        return None, GenerationError.ENTITY_NOT_FOUND
    parts: list[str] = []
    for entity in candidates:
        if not _ENTITY_ID_RE.match(entity.entity_id):
            return None, GenerationError.UNSUPPORTED_CONDITION_TYPE
        unchanged = f"states.{entity.entity_id}.last_changed < today_at('00:00')"
        if entity.domain == "sensor" and condition.state is SemanticState.INACTIVE:
            # A program status sensor: any run changes it.
            parts.append(unchanged)
            continue
        raw = _raw_state_for_semantic(entity.domain, entity.device_class, condition.state)
        if raw is None:
            return None, GenerationError.UNSUPPORTED_CONDITION_TYPE
        parts.append(f"is_state('{entity.entity_id}', '{raw}') and {unchanged}")
    return " and ".join(parts), None


def resolve_target_entities(target: TriggerTarget, entities: list[EntitySnapshot]) -> list[EntitySnapshot]:
    """Public name of the generator's own target resolution (preview reuse)."""
    return _resolve_target_entities(target, entities)


def _presence_people(
    condition: ConditionModel, entities: list[EntitySnapshot]
) -> list[EntitySnapshot]:
    """The people of a whole-house presence condition (``presence_scope``):
    exactly the bound ones - all of them must still exist - or, unbound,
    every ``person.*`` entity."""
    if not condition.person_entity_ids:
        return resolve_candidates(entities, Constraints(domain="person"))
    by_id = {entity.entity_id: entity for entity in entities if entity.domain == "person"}
    if any(person not in by_id for person in condition.person_entity_ids):
        return []
    return [by_id[person] for person in condition.person_entity_ids]


def _generate_condition_leaf(condition: ConditionModel, entities: list[EntitySnapshot]) -> tuple[dict[str, Any] | None, GenerationError | None]:
    if condition.type is ConditionType.STATE:
        assert condition.target is not None and condition.state is not None
        candidates = _resolve_target_entities(condition.target, entities)
        if not candidates:
            return None, GenerationError.ENTITY_NOT_FOUND
        domain = condition.target.domain or candidates[0].domain
        device_class = condition.target.device_class or candidates[0].device_class
        raw_state = _raw_state_for_semantic(domain, device_class, condition.state)
        if raw_state is None:
            return None, GenerationError.UNSUPPORTED_STATE
        return {"condition": "state", "entity_id": _entity_id_field(candidates), "state": raw_state}, None

    if condition.type is ConditionType.NUMERIC:
        assert condition.target is not None and condition.comparator is not None and condition.threshold is not None
        candidates = _resolve_target_entities(condition.target, entities)
        if not candidates:
            return None, GenerationError.ENTITY_NOT_FOUND
        key = "above" if condition.comparator is NumericComparator.ABOVE else "below"
        return {"condition": "numeric_state", "entity_id": _entity_id_field(candidates), key: condition.threshold}, None

    if condition.type is ConditionType.TIME:
        assert condition.time_hour is not None and condition.time_comparator is not None
        minute = condition.time_minute or 0
        key = "before" if condition.time_comparator is TimeComparator.BEFORE else "after"
        return {"condition": "time", key: f"{condition.time_hour:02d}:{minute:02d}:00"}, None

    if condition.type is ConditionType.DATE:
        assert condition.date_month is not None and condition.date_day is not None
        return {
            "condition": "template",
            "value_template": (
                "{{ now().month == " + str(condition.date_month)
                + " and now().day == " + str(condition.date_day) + " }}"
            ),
        }, None

    if condition.type is ConditionType.WEEKDAY:
        assert condition.weekdays
        # HA's ``time`` condition platform natively supports a bare
        # ``weekday`` filter with no before/after bound - unlike
        # ``TriggerType.WEEKDAY`` (no such standalone trigger platform
        # exists, see module docstring), this one has a faithful, direct
        # translation.
        return {"condition": "time", "weekday": list(condition.weekdays)}, None

    if condition.type is ConditionType.SUN:
        assert condition.sun_event is not None and condition.sun_comparator is not None
        event = "sunrise" if condition.sun_event is SunEvent.SUNRISE else "sunset"
        key = "before" if condition.sun_comparator is TimeComparator.BEFORE else "after"
        config: dict[str, Any] = {"condition": "sun", key: event}
        if condition.offset_minutes is not None:
            config[f"{key}_offset"] = _format_offset(condition.offset_minutes)
        return config, None

    if condition.type is ConditionType.PRESENCE:
        assert condition.raw_state is not None
        if condition.target is None:
            # Whole-house "[irgend]jemand ist zuhause" leaf (see
            # ``automation_condition_parser.py``'s ``_parse_presence_condition``
            # docstring - the NOT-wrapper caller negates this into "niemand
            # zuhause") - OR across every known person entity, since HA's
            # own multi-entity ``state`` condition uses AND semantics, the
            # opposite of what "jemand" (someone, i.e. at least one) means.
            person_entities = _presence_people(condition, entities)
            if not person_entities:
                return None, GenerationError.ENTITY_NOT_FOUND
            return {
                "condition": "or",
                "conditions": [
                    {"condition": "state", "entity_id": e.entity_id, "state": condition.raw_state} for e in person_entities
                ],
            }, None
        candidates = _resolve_target_entities(condition.target, entities)
        if not candidates:
            return None, GenerationError.ENTITY_NOT_FOUND
        return {"condition": "state", "entity_id": _entity_id_field(candidates), "state": condition.raw_state}, None

    if condition.type is ConditionType.ENTITY:
        assert condition.target is not None and condition.raw_state is not None
        candidates = _resolve_target_entities(condition.target, entities)
        if not candidates:
            return None, GenerationError.ENTITY_NOT_FOUND
        return {"condition": "state", "entity_id": _entity_id_field(candidates), "state": condition.raw_state}, None

    if condition.type is ConditionType.CALENDAR_EVENT:
        assert condition.raw_state is not None
        needle = json.dumps(condition.raw_state.casefold(), ensure_ascii=False)
        return {
            "condition": "template",
            "value_template": (
                "{{ " + needle
                + " in (trigger.calendar_event.summary | default('', true) | lower) }}"
            ),
        }, None

    if condition.type is ConditionType.TEMPLATE:
        assert condition.raw_state is not None
        return {"condition": "template", "value_template": condition.raw_state}, None

    if condition.type is ConditionType.UNCHANGED_TODAY:
        template, error = unchanged_today_template(condition, entities)
        if error is not None or template is None:
            return None, error or GenerationError.UNSUPPORTED_CONDITION_TYPE
        return {"condition": "template", "value_template": "{{ " + template + " }}"}, None

    # ConditionType.DEVICE lacks its integration-specific subtype schema.
    return None, GenerationError.UNSUPPORTED_CONDITION_TYPE


def _generate_condition_node(node: ConditionNode, entities: list[EntitySnapshot]) -> tuple[dict[str, Any] | None, GenerationError | None]:
    if node.operator is None:
        assert node.condition is not None
        return _generate_condition_leaf(node.condition, entities)

    translated: list[dict[str, Any]] = []
    for child in node.children:
        child_config, error = _generate_condition_node(child, entities)
        if error is not None:
            return None, error
        assert child_config is not None
        translated.append(child_config)

    if node.operator is LogicalOperator.AND:
        return {"condition": "and", "conditions": translated}, None
    if node.operator is LogicalOperator.OR:
        return {"condition": "or", "conditions": translated}, None
    return {"condition": "not", "conditions": translated}, None  # LogicalOperator.NOT


def _generate_addressed_notification(
    action: ActionModel, entities: list[EntitySnapshot]
) -> tuple[dict[str, Any] | None, GenerationError | None]:
    """A typed recipient always becomes a real push to its exact targets.

    A semantic recipient ("mich"/"uns") that was not materialized before
    persistence is refused - it must never degrade into a
    ``persistent_notification`` or a broadcast.  Entity targets use Home
    Assistant's ``notify.send_message`` entity action (``message``/``title``
    only); confirmed legacy service bindings use ``notify.<service>``.
    """
    recipient = action.recipient
    assert recipient is not None and action.message is not None
    if not recipient.materialized:
        return None, GenerationError.NOTIFY_RECIPIENT_UNRESOLVED
    data = {"message": action.message_template or action.message, "title": "HomeIntent"}
    steps: list[dict[str, Any]] = []
    if recipient.entity_ids:
        known = {entity.entity_id for entity in entities if entity.domain == "notify"}
        if any(entity_id not in known for entity_id in recipient.entity_ids):
            return None, GenerationError.ENTITY_NOT_FOUND
        steps.append({
            "action": "notify.send_message",
            "target": {"entity_id": list(recipient.entity_ids)},
            "data": dict(data),
        })
    for service_id in recipient.service_ids:
        domain, _, service = service_id.partition(".")
        if domain != "notify" or not service:
            return None, GenerationError.NOTIFY_RECIPIENT_UNRESOLVED
        steps.append({"action": service_id, "data": dict(data)})
    if len(steps) == 1:
        return steps[0], None
    return {"sequence": steps}, None


def _generate_action_leaf(action: ActionModel, entities: list[EntitySnapshot]) -> tuple[dict[str, Any] | None, GenerationError | None]:
    if action.duration_seconds:
        # 7.9.2 A2: "für 20 Minuten" - the end is part of the same
        # automation: do it, wait, undo it. Never just "do it".
        from .action_duration import inverse_of

        inverse = inverse_of(action)
        if inverse is None:
            return None, GenerationError.UNSUPPORTED_ACTION_TYPE
        start, error = _generate_action_leaf(replace(action, duration_seconds=None), entities)
        if error is not None:
            return None, error
        end, error = _generate_action_leaf(inverse, entities)
        if error is not None:
            return None, error
        assert start is not None and end is not None
        return {"sequence": [start, {"delay": {"seconds": action.duration_seconds}}, end]}, None
    if action.type is ActionType.DELAY:
        assert action.delay_seconds is not None
        return {"delay": {"seconds": action.delay_seconds}}, None

    if action.type is ActionType.NOTIFY:
        assert action.message is not None
        if action.recipient is not None:
            return _generate_addressed_notification(action, entities)
        if action.target is not None:
            candidates = _resolve_target_entities(action.target, entities)
            if not candidates or any(entity.domain != "notify" for entity in candidates):
                return None, GenerationError.ENTITY_NOT_FOUND
            return {
                "action": "notify.send_message",
                "target": {"entity_id": _entity_id_field(candidates)},
                "data": {"message": action.message},
            }, None
        # No fixed notify target exists in this project's model (no
        # notify-service/entity vocabulary anywhere upstream) - Home
        # Assistant's always-available, no-configuration-required
        # ``persistent_notification.create`` is the only notify mechanism
        # that needs no invented target (Regel 4).
        return {"action": "persistent_notification.create", "data": {"message": action.message}}, None

    if action.type is ActionType.WAIT:
        assert action.wait_condition is not None
        template, error = _condition_template(action.wait_condition, entities)
        if error is not None:
            return None, (
                GenerationError.UNSUPPORTED_ACTION_TYPE
                if error is GenerationError.UNSUPPORTED_CONDITION_TYPE
                else error
            )
        assert template is not None
        config: dict[str, Any] = {
            "wait_template": "{{ " + template + " }}",
            "continue_on_timeout": False,
        }
        if action.timeout_seconds is not None:
            config["timeout"] = {"seconds": action.timeout_seconds}
        return config, None

    if action.type is ActionType.REPEAT:
        # 7.9 W5: bounded repetition - notify, wait the interval, and only
        # repeat while the situation holds and the upper bound is not reached.
        assert action.if_condition is not None and action.delay_seconds is not None
        assert action.max_repeats is not None
        condition, error = _generate_condition_node(action.if_condition, entities)
        if error is not None:
            return None, error
        sequence, error = generate_ha_action_configs(action.then_steps, entities)
        if error is not None or sequence is None:
            return None, error or GenerationError.UNSUPPORTED_ACTION_TYPE
        return {"repeat": {
            "while": [
                condition,
                {"condition": "template", "value_template": f"{{{{ repeat.index <= {int(action.max_repeats)} }}}}"},
            ],
            "sequence": [*sequence, {"delay": {"seconds": int(action.delay_seconds)}}],
        }}, None

    if action.type is ActionType.ESCALATE:
        # 7.9 W5: wait for the end of the situation; only a timeout escalates.
        assert action.wait_condition is not None and action.timeout_seconds is not None
        template, error = _condition_template(action.wait_condition, entities)
        if error is not None or template is None:
            return None, GenerationError.UNSUPPORTED_ACTION_TYPE
        escalation, error = generate_ha_action_configs(action.then_steps, entities)
        if error is not None:
            return None, error
        return {"sequence": [
            {
                "wait_template": "{{ " + template + " }}",
                "timeout": {"seconds": int(action.timeout_seconds)},
                "continue_on_timeout": True,
            },
            {
                "if": [{"condition": "template", "value_template": "{{ not wait.completed }}"}],
                "then": escalation,
            },
        ]}, None

    if action.type is ActionType.CHOOSE:
        assert action.if_condition is not None
        condition, error = _generate_condition_node(action.if_condition, entities)
        if error is not None:
            return None, error
        then_steps, error = generate_ha_action_configs(action.then_steps, entities)
        if error is not None:
            return None, error
        else_steps, error = generate_ha_action_configs(action.else_steps, entities)
        if error is not None:
            return None, error
        config = {"if": [condition], "then": then_steps}
        if else_steps:
            config["else"] = else_steps
        return config, None

    assert action.target is not None
    candidates = _resolve_target_entities(action.target, entities)
    if not candidates:
        return None, GenerationError.ENTITY_NOT_FOUND
    if action.target.entity_ids and len(candidates) != len(action.target.entity_ids):
        return None, GenerationError.ENTITY_NOT_FOUND
    domain = action.target.domain or candidates[0].domain

    if action.type is ActionType.REGISTERED_SERVICE:
        from .automation_operations import validate_registered_operation

        if not validate_registered_operation(
            action.service_domain,
            action.service_name,
            action.service_data,
            frozenset(entity.domain for entity in candidates),
        ):
            return None, GenerationError.UNSUPPORTED_ACTION_TYPE
        config: dict[str, Any] = {
            "action": f"{action.service_domain}.{action.service_name}",
            "target": {"entity_id": _entity_id_field(candidates)},
        }
        if action.service_data:
            config["data"] = dict(action.service_data)
        return config, None

    if action.type is ActionType.TURN_ON:
        service = (
            "cover.open_cover" if domain == "cover"
            else "valve.open_valve" if domain == "valve"
            else "homeassistant.turn_on"
        )
        return {"action": service, "target": {"entity_id": _entity_id_field(candidates)}}, None
    if action.type is ActionType.TURN_OFF:
        service = (
            "cover.close_cover" if domain == "cover"
            else "valve.close_valve" if domain == "valve"
            else "homeassistant.turn_off"
        )
        return {"action": service, "target": {"entity_id": _entity_id_field(candidates)}}, None
    if action.type is ActionType.SET_BRIGHTNESS:
        assert action.value is not None
        return {
            "action": "light.turn_on",
            "target": {"entity_id": _entity_id_field(candidates)},
            "data": {"brightness_pct": action.value},
        }, None
    if action.type is ActionType.SET_COLOR:
        assert action.color_name is not None
        return {
            "action": "light.turn_on",
            "target": {"entity_id": _entity_id_field(candidates)},
            "data": {"color_name": action.color_name},
        }, None
    if action.type is ActionType.SET_COLOR_TEMPERATURE:
        assert action.color_temp_kelvin is not None
        return {
            "action": "light.turn_on",
            "target": {"entity_id": _entity_id_field(candidates)},
            "data": {"color_temp_kelvin": action.color_temp_kelvin},
        }, None
    if action.type is ActionType.SET_POSITION:
        assert action.value is not None
        return {
            "action": "cover.set_cover_position",
            "target": {"entity_id": _entity_id_field(candidates)},
            "data": {"position": action.value},
        }, None
    if action.type is ActionType.SET_TEMPERATURE:
        assert action.value is not None
        return {
            "action": "climate.set_temperature",
            "target": {"entity_id": _entity_id_field(candidates)},
            "data": {"temperature": action.value},
        }, None
    assert action.type is ActionType.SET_FAN_SPEED and action.value is not None
    return {
        "action": "fan.turn_on",
        "target": {"entity_id": _entity_id_field(candidates)},
        "data": {"percentage": action.value},
    }, None


def _condition_template(
    node: ConditionNode, entities: list[EntitySnapshot]
) -> tuple[str | None, GenerationError | None]:
    """Compile a bounded condition tree into a deterministic wait template."""
    if node.operator is not None:
        parts: list[str] = []
        for child in node.children:
            value, error = _condition_template(child, entities)
            if error is not None:
                return None, error
            assert value is not None
            parts.append(f"({value})")
        if node.operator is LogicalOperator.NOT:
            return f"not {parts[0]}", None
        joiner = " and " if node.operator is LogicalOperator.AND else " or "
        return joiner.join(parts), None

    assert node.condition is not None
    condition = node.condition
    if condition.type in {ConditionType.STATE, ConditionType.ENTITY, ConditionType.PRESENCE}:
        if condition.target is None:
            if condition.type is not ConditionType.PRESENCE or condition.raw_state is None:
                return None, GenerationError.UNSUPPORTED_CONDITION_TYPE
            people = _presence_people(condition, entities)
            if not people:
                return None, GenerationError.ENTITY_NOT_FOUND
            raw = json.dumps(condition.raw_state)
            return " or ".join(
                f"is_state('{entity.entity_id}', {raw})"
                for entity in people
            ), None
        candidates = _resolve_target_entities(condition.target, entities)
        if not candidates:
            return None, GenerationError.ENTITY_NOT_FOUND
        if condition.type is ConditionType.STATE:
            assert condition.state is not None
            raw = _raw_state_for_semantic(
                condition.target.domain or candidates[0].domain,
                condition.target.device_class or candidates[0].device_class,
                condition.state,
            )
        else:
            raw = condition.raw_state
        if raw is None:
            return None, GenerationError.UNSUPPORTED_STATE
        return " and ".join(
            f"is_state('{entity.entity_id}', '{raw}')"
            for entity in candidates
        ), None
    if condition.type is ConditionType.NUMERIC:
        assert condition.target is not None and condition.threshold is not None and condition.comparator is not None
        candidates = _resolve_target_entities(condition.target, entities)
        if not candidates:
            return None, GenerationError.ENTITY_NOT_FOUND
        operator = ">" if condition.comparator is NumericComparator.ABOVE else "<"
        return " and ".join(
            f"states('{entity.entity_id}') | float(0) {operator} {condition.threshold:g}"
            for entity in candidates
        ), None
    if condition.type is ConditionType.TIME:
        assert condition.time_hour is not None and condition.time_comparator is not None
        value = f"{condition.time_hour:02d}:{(condition.time_minute or 0):02d}:00"
        operator = "<" if condition.time_comparator is TimeComparator.BEFORE else ">"
        return f"now().strftime('%H:%M:%S') {operator} {json.dumps(value)}", None
    if condition.type is ConditionType.DATE:
        assert condition.date_month is not None and condition.date_day is not None
        return (
            f"now().month == {condition.date_month} and now().day == {condition.date_day}",
            None,
        )
    if condition.type is ConditionType.WEEKDAY:
        weekday_numbers = {
            "mon": 0,
            "tue": 1,
            "wed": 2,
            "thu": 3,
            "fri": 4,
            "sat": 5,
            "sun": 6,
        }
        if not condition.weekdays or any(day not in weekday_numbers for day in condition.weekdays):
            return None, GenerationError.UNSUPPORTED_CONDITION_TYPE
        values = ", ".join(str(weekday_numbers[day]) for day in condition.weekdays)
        return f"now().weekday() in [{values}]", None
    if condition.type is ConditionType.SUN:
        assert condition.sun_event is not None and condition.sun_comparator is not None
        if condition.offset_minutes not in {None, 0}:
            return None, GenerationError.UNSUPPORTED_CONDITION_TYPE
        above_horizon = (
            condition.sun_event is SunEvent.SUNRISE
            and condition.sun_comparator is TimeComparator.AFTER
        ) or (
            condition.sun_event is SunEvent.SUNSET
            and condition.sun_comparator is TimeComparator.BEFORE
        )
        state = "above_horizon" if above_horizon else "below_horizon"
        return f"is_state('sun.sun', '{state}')", None
    if condition.type is ConditionType.TEMPLATE and condition.raw_state is not None:
        return condition.raw_state.removeprefix("{{").removesuffix("}}").strip(), None
    if condition.type is ConditionType.UNCHANGED_TODAY:
        return unchanged_today_template(condition, entities)
    return None, GenerationError.UNSUPPORTED_CONDITION_TYPE


def _generate_action_step(
    step: "ActionModel | ActionGroup", entities: list[EntitySnapshot]
) -> tuple[dict[str, Any] | None, GenerationError | None]:
    if isinstance(step, ActionGroup):
        children: list[dict[str, Any]] = []
        for child in step.steps:
            child_config, error = _generate_action_step(child, entities)
            if error is not None:
                return None, error
            assert child_config is not None
            children.append(child_config)
        key = "parallel" if step.mode is ExecutionMode.PARALLEL else "sequence"
        return {key: children}, None
    return _generate_action_leaf(step, entities)


def generate_ha_action_configs(
    actions: tuple["ActionModel | ActionGroup", ...],
    entities: list[EntitySnapshot],
) -> tuple[list[dict[str, Any]] | None, GenerationError | None]:
    """Public action-only renderer used by safe automation action editing."""
    rendered: list[dict[str, Any]] = []
    for action in actions:
        config, error = _generate_action_step(action, entities)
        if error is not None:
            return None, error
        assert config is not None
        rendered.append(config)
    return rendered, None


def generate_ha_trigger_configs(
    triggers: tuple[TriggerModel, ...], entities: list[EntitySnapshot]
) -> tuple[list[dict[str, Any]] | None, GenerationError | None]:
    """Public trigger-only renderer used by safe automation editing."""
    rendered: list[dict[str, Any]] = []
    for trigger in triggers:
        config, error = _generate_trigger(trigger, entities)
        if error is not None:
            return None, error
        assert config is not None
        rendered.append(config)
    return rendered, None


def generate_ha_condition_configs(
    conditions: tuple[ConditionNode, ...], entities: list[EntitySnapshot]
) -> tuple[list[dict[str, Any]] | None, GenerationError | None]:
    """Public condition-only renderer used by safe automation editing."""
    rendered: list[dict[str, Any]] = []
    for condition in conditions:
        config, error = _generate_condition_node(condition, entities)
        if error is not None:
            return None, error
        assert config is not None
        rendered.append(config)
    return rendered, None


def _bind_proactive_delivery(
    config: dict[str, Any],
    *,
    automation_id: str,
    source_entity_id: str | None,
    expected_source_state: str | None,
    source_comparator: str | None,
    source_threshold: float | None,
) -> dict[str, Any]:
    """Route targetless notifications through the entry-owned agent runtime."""
    action = config.get("action")
    data = config.get("data")
    if (
        action == "persistent_notification.create"
        and isinstance(data, dict)
        and isinstance(data.get("message"), str)
    ):
        proactive_data: dict[str, Any] = {
            "rule_id": automation_id,
            "title": "HomeIntent",
            "message": data["message"],
            "mode": "inform",
        }
        if source_entity_id is not None:
            proactive_data["source_entity_id"] = source_entity_id
        if expected_source_state is not None:
            proactive_data["expected_source_state"] = expected_source_state
        if source_comparator is not None and source_threshold is not None:
            proactive_data["source_comparator"] = source_comparator
            proactive_data["source_threshold"] = source_threshold
        return {"action": "homeintent.proactive_message", "data": proactive_data}
    for key in ("sequence", "parallel"):
        children = config.get(key)
        if isinstance(children, list):
            return {
                **config,
                key: [
                    _bind_proactive_delivery(
                        child,
                        automation_id=automation_id,
                        source_entity_id=source_entity_id,
                        expected_source_state=expected_source_state,
                        source_comparator=source_comparator,
                        source_threshold=source_threshold,
                    )
                    if isinstance(child, dict)
                    else child
                    for child in children
                ],
            }
    return config


def generate_ha_automation_config(
    model: AutomationModel, entities: list[EntitySnapshot], automation_id: str | None = None
) -> GenerationResult:
    """Translates an already-validated ``AutomationModel`` into a Home
    Assistant-native automation config dict (no ``id`` - the Executor's job,
    see ``GenerationResult``'s own docstring). Re-resolves every target
    against ``entities`` (the confirming turn's fresh entity list, per
    ``PendingAutomationConfirmation``'s own docstring) rather than trusting
    anything cached from parse time - the exact "future HA-generator wave"
    deferral ``automation_action_parser.py``'s ``_build_quantified_target``
    already names.

    Exactly one ``TriggerModel`` is ever present today (``match_automation()``
    never builds more than one) - iterated as a list regardless, so this
    function keeps working unchanged if that ever stops being true.

    ``automation_id`` (new feature, Wave 12 "Einmalige Automation"): when
    ``model.once`` is set, the generated config's own actions end with a
    call to the custom ``homeintent.delete_automation`` service, reusing the
    already-existing ``AutomationExecutor.async_delete_automation()`` (Wave
    10) rather than a parallel deletion mechanism (Regel 6). That service
    call needs to know the automation's own future ``automation_id`` - a
    chicken-and-egg problem, since ``AutomationExecutor.async_create_automation()``
    normally only generates that id at persist time, *after* this config
    already exists. The caller (``conversation.py``) resolves this by
    pre-generating the id and passing it in here *and* into
    ``async_create_automation()`` for the same automation, so both sides
    agree. ``automation_id`` is ``None``/ignored for every other (non-once)
    automation - fully backward compatible with every existing caller.
    """
    triggers: list[dict[str, Any]] = []
    triggers_delay_action: dict[str, Any] | None = None
    repeat_wait_action: dict[str, Any] | None = None
    for trigger in model.triggers:
        trigger_config, error = _generate_trigger(trigger, entities)
        if error is not None:
            return GenerationResult(config=None, error=error)
        assert trigger_config is not None
        triggers.append(trigger_config)
        if trigger.repeat_within_seconds is not None:
            repeated_trigger = dict(trigger_config)
            repeated_trigger.pop("id", None)
            repeat_wait_action = {
                "wait_for_trigger": [repeated_trigger],
                "timeout": {"seconds": trigger.repeat_within_seconds},
                "continue_on_timeout": False,
            }
        # A trigger's own post-fire delay (V5.14, "10 Minuten nachdem...")
        # has no Home Assistant trigger-level field - it becomes the first
        # action, run before anything the automation was actually asked to
        # do (see TriggerModel.delay_seconds' own comment: "the delay
        # pushes the actual automation start later").
        if trigger.delay_seconds is not None:
            triggers_delay_action = {"delay": {"seconds": trigger.delay_seconds}}

    conditions: list[dict[str, Any]] = []
    trigger_conditions = [
        condition for trigger in model.triggers for condition in _trigger_conditions(trigger)
    ]
    if trigger_conditions and len(model.triggers) != 1:
        # A direction guard is trigger-scoped; with several triggers it would
        # silently constrain the others as well.
        return GenerationResult(config=None, error=GenerationError.UNSUPPORTED_TRIGGER_TYPE)
    conditions.extend(trigger_conditions)
    for condition_node in model.conditions:
        condition_config, error = _generate_condition_node(condition_node, entities)
        if error is not None:
            return GenerationResult(config=None, error=error)
        assert condition_config is not None
        conditions.append(condition_config)

    if model.scheduled_for is not None:
        scheduled_date = model.scheduled_for.date().isoformat()
        conditions.append(
            {
                "condition": "template",
                "value_template": (
                    "{{ now().date().isoformat() == '" + scheduled_date + "' }}"
                ),
            }
        )

    actions: list[dict[str, Any]] = []
    if repeat_wait_action is not None:
        actions.append(repeat_wait_action)
    if triggers_delay_action is not None:
        actions.append(triggers_delay_action)
    main_actions: list[dict[str, Any]] = []
    for step in model.actions:
        step_config, error = _generate_action_step(step, entities)
        if error is not None:
            return GenerationResult(config=None, error=error)
        assert step_config is not None
        main_actions.append(step_config)
    if model.weather_guard is not None:
        # 7.9.3 B1/B4: fetch the forecast in the automation, then one closed
        # template condition; under "Regen angesagt" a device action runs
        # only while it is not already done.
        from ..weather import guard_steps, not_done_condition

        actions.extend(guard_steps(model.weather_guard))
        if any(trigger.type is TriggerType.WEATHER for trigger in model.triggers):
            not_done = not_done_condition(main_actions)
            if not_done is not None:
                actions.append(not_done)
    actions.extend(main_actions)

    if automation_id is not None:
        source_entity_id: str | None = None
        expected_source_state: str | None = None
        source_comparator: str | None = None
        source_threshold: float | None = None
        if len(triggers) == 1 and triggers[0].get("trigger") == "state":
            raw_entity_id = triggers[0].get("entity_id")
            if isinstance(raw_entity_id, str):
                source_entity_id = raw_entity_id
            raw_state = triggers[0].get("to")
            if isinstance(raw_state, str):
                expected_source_state = raw_state
        if (
            len(model.triggers) == 1
            and model.triggers[0].type is TriggerType.NUMERIC_STATE
            and model.triggers[0].measurement is None
            and model.triggers[0].comparator
            in {NumericComparator.ABOVE, NumericComparator.BELOW, NumericComparator.EQUAL}
        ):
            numeric_trigger = model.triggers[0]
            if numeric_trigger.target is not None:
                numeric_candidates = _resolve_target_entities(
                    numeric_trigger.target, entities
                )
                if len(numeric_candidates) == 1:
                    source_entity_id = numeric_candidates[0].entity_id
            if numeric_trigger.comparator is not None:
                source_comparator = numeric_trigger.comparator.name.casefold()
            source_threshold = numeric_trigger.threshold
        actions = [
            _bind_proactive_delivery(
                action,
                automation_id=automation_id,
                source_entity_id=source_entity_id,
                expected_source_state=expected_source_state,
                source_comparator=source_comparator,
                source_threshold=source_threshold,
            )
            for action in actions
        ]

    if model.once:
        assert automation_id is not None
        actions.append({"action": "homeintent.delete_automation", "data": {"automation_id": automation_id}})
    elif model.max_runs is not None:
        assert automation_id is not None
        actions.append(
            {
                "action": "homeintent.record_automation_run",
                "data": {
                    "automation_id": automation_id,
                    "max_runs": model.max_runs,
                },
            }
        )

    config: dict[str, Any] = {"alias": model.source_text, "triggers": triggers, "actions": actions}
    if any(trigger.type is TriggerType.CALENDAR for trigger in model.triggers):
        # Calendar events can overlap; queued prevents a second event from
        # being discarded while the first action sequence is still active.
        config["mode"] = "queued"
    elif any(
        isinstance(action, ActionModel) and action.type in {ActionType.REPEAT, ActionType.ESCALATE}
        for action in model.actions
    ):
        # A new start of the situation restarts the reminder / escalation
        # instead of being dropped with a warning (7.9 W5).
        config["mode"] = "restart"
    elif any(
        isinstance(trigger.get("entity_id"), list) and len(trigger["entity_id"]) > 1 for trigger in triggers
    ) and all(action.get("action") == "notify.send_message" for action in actions):
        # A monitor over several devices reports each device (7.9.3 A1): two
        # devices reaching the state at once are both sent, never dropped.
        config["mode"] = "queued"
    if conditions:
        config["conditions"] = conditions
    return GenerationResult(config=config, error=None)
