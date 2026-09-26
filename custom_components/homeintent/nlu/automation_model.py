"""Automation Semantic Model (HomeIntent V5 plan, Teil 1/10 + Teil 2/10,
V5.1-V5.3): ``AutomationModel`` is the semantic representation of a spoken
automation request ("Wenn das Küchenfenster geöffnet wird, ...") - it *is*
the AST itself (no separate AST class, mirrors ``nlu/query_command.py``'s
``QueryCommand`` being both the resolved command and the type the executor
consumes).

Kept free of Home Assistant/hassil imports, same boundary as
``nlu/frame.py``/``nlu/query_command.py`` - only ``automation_trigger_parser.py``
(top-level, hassil-aware) constructs these types from spoken text.

V5 Wave 1 scope: only ``triggers`` is ever populated. ``conditions``/
``actions`` exist as empty ``tuple()`` fields now so later Condition-/
Action-Engine waves (V5 Teil 3-4/10) can extend ``AutomationModel`` without
breaking this shape - not used, not guessed, just reserved.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum, auto
from typing import TYPE_CHECKING

from .measurement import MeasurementProperty, TravelDirection
from .semantic_state import SemanticState

if TYPE_CHECKING:
    # V5 Wave 2 (V5.4-V5.6): condition_model.py imports TriggerTarget/
    # NumericComparator/SunEvent from *this* module, so a real (non-
    # TYPE_CHECKING) import here would cycle. `from __future__ import
    # annotations` above means this annotation is never evaluated at
    # runtime, so the cycle only exists for type checkers, not at import
    # time.
    from .condition_model import ConditionNode

    # V5 Wave 3 (V5.7-V5.12): action_model.py imports TriggerTarget from
    # *this* module (and ConditionNode from condition_model.py, for WAIT) -
    # same TYPE_CHECKING-only cycle-avoidance as ConditionNode above.
    from .action_model import ActionGroup, ActionModel


class TriggerType(Enum):
    """The HomeIntent trigger types, including preview-only relative time.

    ``STATE`` vs. ``NUMERIC_STATE`` deliberately mirrors Home Assistant's own
    split between its ``state`` and ``numeric_state`` trigger platforms
    (equality/transition vs. threshold comparison) rather than merging them -
    the later HA generator (V5.25, a separate future wave) needs exactly
    this distinction to emit the right trigger platform.
    """

    STATE = auto()
    NUMERIC_STATE = auto()
    DEVICE = auto()
    PRESENCE = auto()
    SUN = auto()
    TIME = auto()
    RELATIVE_TIME = auto()
    CALENDAR_TIME = auto()
    WEEKDAY = auto()
    CALENDAR = auto()


class CalendarReference(Enum):
    TODAY = auto()
    TOMORROW = auto()
    DAY_AFTER_TOMORROW = auto()
    WEEKDAY = auto()
    DATE = auto()
    NEXT_WORKDAY = auto()


@dataclass(frozen=True)
class CalendarSchedule:
    """Unresolved local calendar date/time held until confirmation."""

    reference: CalendarReference
    hour: int
    minute: int = 0
    weekday: int | None = None
    day: int | None = None
    month: int | None = None
    year: int | None = None
    spoken: str = ""


class NumericComparator(Enum):
    """Numeric relation requested by the user.

    ``EQUAL`` is generated as an HA template trigger because
    ``numeric_state`` deliberately only supports above/below thresholds.
    """

    ABOVE = auto()
    BELOW = auto()
    EQUAL = auto()
    # Inclusive bounds ("mindestens"/"höchstens"). HA's numeric_state only
    # knows strict above/below, so these are generated as template triggers
    # and are never silently narrowed to ABOVE/BELOW (7.2.0 spec §15).
    AT_LEAST = auto()
    AT_MOST = auto()


class PresenceEvent(Enum):
    """Which presence transition a PRESENCE trigger means."""

    ARRIVE = auto()  # "nach Hause kommt" - to: home
    LEAVE = auto()  # "das Haus verlässt" - from: home


class SunEvent(Enum):
    SUNRISE = auto()
    SUNSET = auto()


@dataclass(frozen=True)
class TriggerTarget:
    """What a trigger watches, before any entity is necessarily resolved.

    Mirrors ``nlu/query_command.py``'s ``QueryTarget`` domain/device_class/
    area/entity_id split on purpose: "ein Fenster im Wohnzimmer" stays
    representable as domain+device_class+area alone - ``entity_id`` is only
    set when the spoken sentence named one specific entity unambiguously.
    Binding to a concrete entity earlier than the sentence itself warrants
    would make the model less reusable for later waves (Reasoning/
    Validation still need to pick *which* entity at runtime, not this
    parse-time model).
    """

    domain: str | None = None
    device_class: str | None = None
    area_id: str | None = None
    floor_id: str | None = None
    entity_id: str | None = None
    # Exact, already-resolved multi-targets used when a validated direct
    # service plan is lifted into a delayed or triggered action. This avoids
    # widening an arbitrary concrete list to an entire room or house.
    entity_ids: tuple[str, ...] = ()
    # V5 Wave 4 (V5.17, "Natural Language Quantifiers in Automations") -
    # reuses parsers.py's QuantifierParser vocabulary/values verbatim
    # (Regel 6): "all"/"both"/"count" (same 3 kinds, same "beide"==exactly-2/
    # "die drei X"==exactly-3 validation rules), populated only by
    # automation_action_parser.py's quantified TURN_ON/TURN_OFF sentences -
    # every other target (triggers, conditions, single-entity actions) keeps
    # these at their default and is completely unaffected.
    quantifier: str | None = None
    quantifier_count: int | None = None  # only set when quantifier == "count"
    exclude_entity_ids: tuple[str, ...] = ()  # entities named via "... außer [dem|der|den] {name}"


@dataclass(frozen=True)
class TriggerModel:
    """One trigger, discriminated by ``type`` rather than 7 subclasses - a
    single dataclass with typed-but-optional fields is easier to inspect,
    compare in tests, and eventually serialize for the future HA generator
    (V5.25) than a class hierarchy would be. Only the fields relevant to
    ``type`` are ever populated; the rest stay at their ``None``/``()``
    default.
    """

    type: TriggerType
    target: TriggerTarget | None = None  # STATE / NUMERIC_STATE / PRESENCE / DEVICE
    state: SemanticState | None = None  # STATE - reuses the existing enum, no new state vocabulary
    comparator: NumericComparator | None = None  # NUMERIC_STATE
    threshold: float | None = None  # NUMERIC_STATE
    # NUMERIC_STATE over a closed, attribute-backed property (cover position,
    # light brightness, fan percentage) instead of the entity state. ``None``
    # keeps the classic "numeric entity state" meaning.
    measurement: MeasurementProperty | None = None
    # Explicit cover travel direction; only ever set with ``measurement``.
    direction: TravelDirection | None = None
    device_id: str | None = None  # DEVICE
    device_trigger_type: str | None = None  # DEVICE
    zone_id: str | None = None  # PRESENCE
    # PRESENCE direction. ``None`` keeps the historical direction-neutral
    # "any presence change" meaning.
    presence_event: PresenceEvent | None = None
    # "wenn ich nach Hause komme": the speaker's own person entity, bound to
    # the authenticated user when the preview is created.
    presence_of_speaker: bool = False
    sun_event: SunEvent | None = None  # SUN
    offset_minutes: int | None = None  # SUN
    time_hour: int | None = None  # TIME
    time_minute: int | None = None  # TIME
    # Wave 13 ("Relative-Zeit-Automationen") - only ever set when a TIME
    # trigger was computed from "jetzt + Offset" (see
    # relative_time_command_parser.py/engine.py's
    # match_relative_time_automation()): a near-minute-boundary delay ("in
    # 30 Sekunden") snapped to ":00" could already be in the past within
    # the current minute, pushing HA's `time` trigger a full day late -
    # carrying the real second closes that gap. Every spoken-absolute-
    # clock-time sentence ("um 20 Uhr") never sets this (nobody says "um 20
    # Uhr 15 Minuten und 30 Sekunden"), so it stays ``None`` there and the
    # generator keeps emitting ":00", unchanged.
    time_second: int | None = None  # TIME
    # Preview-time representation. The absolute target is intentionally not
    # calculated until the user confirms the automation.
    relative_offset_seconds: int | None = None  # RELATIVE_TIME
    weekdays: tuple[str, ...] = ()  # WEEKDAY - HA weekday abbreviations, e.g. ("sat", "sun")
    # V5 Wave 4 (V5.14, "Relative Time" - "10 Minuten nachdem ich nach Hause
    # komme") - only ever set on an event-based trigger type (STATE/
    # NUMERIC_STATE/PRESENCE/DEVICE, see automation_trigger_parser.py's
    # _parse_delay_trigger); TIME/SUN/WEEKDAY triggers never carry a delay -
    # they already express an absolute/recurring point in time, "X Minuten
    # nachdem es 20 Uhr ist" is not a sentence any of the 7 trigger grammars
    # accept.
    delay_seconds: int | None = None
    # Minimum time for which the triggering state must remain true. Unlike
    # ``delay_seconds`` this maps to Home Assistant's native trigger ``for``
    # field and therefore cancels when the state changes back early.
    for_seconds: int | None = None
    # Require one further equivalent trigger event within this window.
    # This models "zweimal innerhalb von ..." using HA wait_for_trigger.
    repeat_within_seconds: int | None = None
    trigger_id: str | None = None
    calendar_entity_id: str | None = None  # CALENDAR
    calendar_event: str | None = None  # start/end


@dataclass(frozen=True)
class AutomationModel:
    """The semantic model/AST for one automation request. ``conditions``
    is populated from V5 Wave 2 onward (``ConditionNode`` trees, see
    ``condition_model.py``); ``actions`` is populated from V5 Wave 3 onward
    (``ActionModel``/``ActionGroup``, see ``action_model.py``) - a flat,
    top-level-SEQUENTIAL tuple whose elements may themselves be a nested
    ``ActionGroup`` for an explicit parallel branch (V5.9). No redundant
    top-level ``ActionGroup(mode=SEQUENTIAL)`` wrapper - HA's own action
    lists are sequential by default, so that's this tuple's default shape
    too, same as it always was before this wave.
    """

    triggers: tuple[TriggerModel, ...] = ()
    conditions: tuple["ConditionNode", ...] = ()
    actions: tuple["ActionModel | ActionGroup", ...] = ()
    source_text: str = ""
    # New feature (Wave 12, "Einmalige Automation"): "wenn ... dann ... -
    # einmalig"/"nur einmal" - the generated HA automation deletes itself via
    # a trailing homeintent.delete_automation action step after it fires once,
    # instead of persisting forever like every other automation. Set by
    # engine.py's _AUTOMATION_ONCE_RE detection; read by
    # ha_automation_generator.py to append the self-delete action.
    once: bool = False
    # Delete after this many successful executions. ``None`` means unlimited;
    # ``once`` remains the explicit one-run shorthand.
    max_runs: int | None = None
    # Full target date/time of a confirmed relative one-shot. The generator
    # uses the date as an additional guard around HA's clock-only trigger.
    scheduled_for: datetime | None = None
    calendar_schedule: CalendarSchedule | None = None
    quiet_start_hour: int | None = None
    quiet_end_hour: int | None = None


def resolve_relative_schedule(model: AutomationModel, now: datetime) -> AutomationModel:
    """Anchor one relative trigger to ``now`` after user confirmation."""
    relative_triggers = [
        trigger
        for trigger in model.triggers
        if trigger.type is TriggerType.RELATIVE_TIME
    ]
    if not relative_triggers:
        return model
    if len(relative_triggers) != 1 or len(model.triggers) != 1:
        raise ValueError("Relative one-shot automations require exactly one trigger")
    offset = relative_triggers[0].relative_offset_seconds
    if offset is None or offset <= 0:
        raise ValueError("Relative one-shot automation has no positive offset")
    if now.tzinfo is not None and now.utcoffset() is not None:
        target = (
            now.astimezone(timezone.utc) + timedelta(seconds=offset)
        ).astimezone(now.tzinfo)
    else:
        target = now + timedelta(seconds=offset)
    trigger = TriggerModel(
        type=TriggerType.TIME,
        time_hour=target.hour,
        time_minute=target.minute,
        time_second=target.second,
    )
    return replace(model, triggers=(trigger,), scheduled_for=target)


def resolve_calendar_schedule(model: AutomationModel, now: datetime) -> AutomationModel:
    """Resolve one spoken calendar schedule against confirmation-local time."""
    schedule = model.calendar_schedule
    if schedule is None:
        return model
    if len(model.triggers) != 1 or model.triggers[0].type is not TriggerType.CALENDAR_TIME:
        raise ValueError("Calendar one-shot automations require exactly one trigger")

    today = now.date()
    if schedule.reference is CalendarReference.TODAY:
        target_date = today
    elif schedule.reference is CalendarReference.TOMORROW:
        target_date = today + timedelta(days=1)
    elif schedule.reference is CalendarReference.DAY_AFTER_TOMORROW:
        target_date = today + timedelta(days=2)
    elif schedule.reference is CalendarReference.NEXT_WORKDAY:
        target_date = today + timedelta(days=1)
        while target_date.weekday() >= 5:
            target_date += timedelta(days=1)
    elif schedule.reference is CalendarReference.WEEKDAY:
        if schedule.weekday is None:
            raise ValueError("Calendar weekday is missing")
        days = (schedule.weekday - today.weekday()) % 7
        target_date = today + timedelta(days=days)
    else:
        if schedule.day is None or schedule.month is None:
            raise ValueError("Calendar date is incomplete")
        year = schedule.year or today.year
        try:
            target_date = date(year, schedule.month, schedule.day)
        except ValueError as err:
            raise ValueError("Ungültiges Kalenderdatum") from err
        if schedule.year is None and target_date < today:
            target_date = date(year + 1, schedule.month, schedule.day)

    target = datetime.combine(
        target_date,
        time(schedule.hour, schedule.minute),
        tzinfo=now.tzinfo,
    )
    if schedule.reference is CalendarReference.WEEKDAY and target <= now:
        target += timedelta(days=7)
    elif target <= now:
        raise ValueError("Der gewünschte Zeitpunkt liegt bereits in der Vergangenheit")

    if target.tzinfo is not None:
        round_trip = target.astimezone(timezone.utc).astimezone(target.tzinfo)
        if (round_trip.date(), round_trip.hour, round_trip.minute) != (
            target.date(),
            target.hour,
            target.minute,
        ):
            raise ValueError("Der gewünschte Zeitpunkt existiert wegen der Zeitumstellung nicht")

    trigger = TriggerModel(
        type=TriggerType.TIME,
        time_hour=target.hour,
        time_minute=target.minute,
        time_second=0,
    )
    return replace(
        model,
        triggers=(trigger,),
        scheduled_for=target,
        calendar_schedule=None,
    )


def resolve_pending_schedule(model: AutomationModel, now: datetime) -> AutomationModel:
    """Resolve either supported preview-time schedule representation."""
    resolved = resolve_calendar_schedule(resolve_relative_schedule(model, now), now)
    if (
        resolved.scheduled_for is None
        or resolved.quiet_start_hour is None
        or resolved.quiet_end_hour is None
    ):
        return resolved
    target = resolved.scheduled_for
    start, end = resolved.quiet_start_hour, resolved.quiet_end_hour
    in_quiet = (
        start <= target.hour < end
        if start < end
        else target.hour >= start or target.hour < end
    )
    if not in_quiet:
        return resolved
    if start > end and target.hour >= start:
        target = target + timedelta(days=1)
    target = target.replace(hour=end, minute=0, second=0, microsecond=0)
    trigger = TriggerModel(
        type=TriggerType.TIME,
        time_hour=target.hour,
        time_minute=target.minute,
        time_second=target.second,
    )
    return replace(resolved, scheduled_for=target, triggers=(trigger,))


def _render_target(target: TriggerTarget) -> str:
    parts = [
        f"{field}={value}"
        for field, value in (
            ("domain", target.domain),
            ("device_class", target.device_class),
            ("area_id", target.area_id),
            ("floor_id", target.floor_id),
            ("entity_id", target.entity_id),
            ("quantifier", target.quantifier),
            ("quantifier_count", target.quantifier_count),
        )
        if value is not None
    ]
    if target.exclude_entity_ids:
        parts.append(f"exclude_entity_ids={','.join(target.exclude_entity_ids)}")
    if target.entity_ids:
        parts.append(f"entity_ids={','.join(target.entity_ids)}")
    return "{" + ", ".join(parts) + "}"


def _render_trigger(trigger: TriggerModel) -> str:
    parts = []
    if trigger.target is not None:
        parts.append(f"target={_render_target(trigger.target)}")
    if trigger.state is not None:
        parts.append(f"state={trigger.state.name}")
    if trigger.comparator is not None:
        parts.append(f"comparator={trigger.comparator.name}")
    if trigger.threshold is not None:
        parts.append(f"threshold={trigger.threshold}")
    if trigger.measurement is not None:
        parts.append(f"measurement={trigger.measurement.name}")
    if trigger.direction is not None:
        parts.append(f"direction={trigger.direction.name}")
    if trigger.device_id is not None:
        parts.append(f"device_id={trigger.device_id}")
    if trigger.device_trigger_type is not None:
        parts.append(f"device_trigger_type={trigger.device_trigger_type}")
    if trigger.zone_id is not None:
        parts.append(f"zone_id={trigger.zone_id}")
    if trigger.presence_event is not None:
        parts.append(f"presence_event={trigger.presence_event.name}")
    if trigger.sun_event is not None:
        parts.append(f"sun_event={trigger.sun_event.name}")
    if trigger.offset_minutes is not None:
        parts.append(f"offset_minutes={trigger.offset_minutes}")
    if trigger.time_hour is not None:
        parts.append(f"time={trigger.time_hour:02d}:{(trigger.time_minute or 0):02d}")
    if trigger.time_second is not None:
        parts.append(f"time_second={trigger.time_second}")
    if trigger.relative_offset_seconds is not None:
        parts.append(f"relative_offset_seconds={trigger.relative_offset_seconds}")
    if trigger.type is TriggerType.CALENDAR_TIME:
        parts.append("calendar_schedule=pending")
    if trigger.weekdays:
        parts.append(f"weekdays={','.join(trigger.weekdays)}")
    if trigger.delay_seconds is not None:
        parts.append(f"delay_seconds={trigger.delay_seconds}")
    return f"{trigger.type.name}(" + ", ".join(parts) + ")"


def render_automation_tree(model: AutomationModel) -> str:
    """Plain-text debug tree, not a spoken preview (V5.23's Dry-Run text is
    a later, separate wave with its own German wording) - only for logs/
    tests/manual inspection."""
    lines = [
        f"AutomationModel(source_text={model.source_text!r}, once={model.once}, "
        f"max_runs={model.max_runs})"
    ]
    if not model.triggers:
        lines.append("  triggers: (none)")
    else:
        lines.append(f"  triggers ({len(model.triggers)}):")
        for trigger in model.triggers:
            lines.append(f"    - {_render_trigger(trigger)}")
    if not model.conditions:
        lines.append("  conditions: 0")
    else:
        # Function-local import (V5 Wave 2): avoids a module-level import
        # cycle back to condition_model.py, which itself imports
        # TriggerTarget/NumericComparator/SunEvent from this module.
        from .condition_model import render_condition_tree

        lines.append(f"  conditions ({len(model.conditions)}):")
        for condition in model.conditions:
            for line in render_condition_tree(condition, indent=2).splitlines():
                lines.append(line)
    if not model.actions:
        lines.append("  actions: (none)")
    else:
        # Function-local import (V5 Wave 3): avoids a module-level import
        # cycle back to action_model.py, which itself imports TriggerTarget
        # from this module - same reasoning as render_condition_tree above.
        from .action_model import render_action_step

        lines.append(f"  actions ({len(model.actions)}):")
        for action in model.actions:
            for line in render_action_step(action, indent=2).splitlines():
                lines.append(line)
    return "\n".join(lines)
