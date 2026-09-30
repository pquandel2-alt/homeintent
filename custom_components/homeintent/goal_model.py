"""Authoritative typed V10 goal model.

Goals describe outcomes.  They intentionally contain no Home Assistant
service name; the planner is the only component allowed to select a closed
operator and the ServiceMapper/executor remain authoritative for actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Mapping, Sequence, cast


class IntentClass(StrEnum):
    COMMAND = "command"
    QUERY = "query"
    AUTOMATION = "automation"
    GOAL = "goal"


class GoalKind(StrEnum):
    ACHIEVE_STATE = "achieve_state"
    MAINTAIN_STATE = "maintain_state"
    PREPARE_ROUTINE = "prepare_routine"
    MONITOR_AND_NOTIFY = "monitor_and_notify"
    CONDITIONAL = "conditional_goal"
    COMFORT = "comfort_goal"
    SCHEDULED = "scheduled_goal"
    EXPLAIN_FAILURE = "explain_failure"
    VERIFY_STATE = "verify_state"

    # Stable V9 names retained as specializations during migration.
    PREPARE_NIGHT = "prepare_night"
    PREPARE_MOVIE = "prepare_movie"
    SAVE_UNOCCUPIED = "save_unoccupied"
    PREPARE_AWAY = "prepare_away"
    IMPROVE_COMFORT = "improve_comfort"
    INVESTIGATE_STATE = "investigate_state"
    SECURE_HOME = "secure_home"
    QUIET_MEDIA = "quiet_media"


class GoalLifecycle(StrEnum):
    ONE_SHOT = "one_shot"
    RECURRING = "recurring"
    MONITOR = "monitor"


class DeliveryChannel(StrEnum):
    VOICE = "voice"
    PUSH = "push"
    TEXT_APP = "text_app"


class NotificationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class GoalSemanticChoice(StrEnum):
    SETPOINT_AT_TIME = "setpoint_at_time"
    ACHIEVE_BY_DEADLINE = "achieve_by_deadline"


@dataclass(frozen=True)
class GoalScope:
    entity_ids: tuple[str, ...] = ()
    domain: str | None = None
    device_class: str | None = None
    area_id: str | None = None
    floor_id: str | None = None
    person_ids: tuple[str, ...] = ()
    excluded_entity_ids: tuple[str, ...] = ()
    excluded_area_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DesiredState:
    property_name: str
    value: str | float | int | bool
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class TemporalGoal:
    deadline: datetime | None = None
    execute_at: datetime | None = None
    day_part: str | None = None
    must_be_achieved_by_deadline: bool = False


@dataclass(frozen=True)
class GoalTrigger:
    kind: str
    person_entity_id: str | None = None
    zone_id: str | None = None
    from_state: str | None = None
    to_state: str | None = None
    household_person_ids: tuple[str, ...] = ()
    occurrence_id: str | None = None
    # kind "value_change" (7.9 W3, ``rate_monitor.RateRule``): the sensor,
    # the amount in its unit, "fall"/"rise"/"either" and the window.
    entity_id: str | None = None
    delta: float | None = None
    unit: str | None = None
    direction: str | None = None
    window_seconds: int | None = None


@dataclass(frozen=True)
class GoalCondition:
    kind: str
    scope: GoalScope = field(default_factory=GoalScope)
    operator: str = "equals"
    value: object = None
    evaluate_at_trigger: bool = True


@dataclass(frozen=True)
class SuccessCriterion:
    criterion_id: str
    kind: str
    scope: GoalScope = field(default_factory=GoalScope)
    expected: object = None
    required: bool = True


@dataclass(frozen=True)
class GoalProvenance:
    source_utterance: str = ""
    user_id: str | None = None
    conversation_id: str | None = None
    semantic_graph_ref: str | None = None
    confirmed: bool = False


@dataclass(frozen=True)
class GoalModel:
    """Closed, serializable description of an intended result.

    ``parameters`` is retained for V9 procedure compatibility.  New V10
    behavior uses the typed fields; conversion is explicit in ``from_dict``.
    """

    kind: GoalKind
    parameters: Mapping[str, object] = field(default_factory=lambda: _empty_mapping())
    goal_id: str = ""
    scope: GoalScope = field(default_factory=GoalScope)
    desired_states: tuple[DesiredState, ...] = ()
    temporal: TemporalGoal | None = None
    trigger: GoalTrigger | None = None
    conditions: tuple[GoalCondition, ...] = ()
    exclusions: GoalScope = field(default_factory=GoalScope)
    profile_id: str | None = None
    routine_id: str | None = None
    recipient_person_ids: tuple[str, ...] = ()
    delivery_channel: DeliveryChannel | None = None
    notification_severity: NotificationSeverity = NotificationSeverity.INFO
    confirmation_required: bool = False
    success_criteria: tuple[SuccessCriterion, ...] = ()
    failure_handling: str = "report"
    provenance: GoalProvenance = field(default_factory=GoalProvenance)
    lifecycle: GoalLifecycle = GoalLifecycle.ONE_SHOT

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "goal_id": self.goal_id,
            "kind": self.kind.value,
            "parameters": dict(self.parameters),
            "scope": _scope_dict(self.scope),
            "desired_states": [
                {
                    "property_name": item.property_name,
                    "value": item.value,
                    "unit": item.unit,
                    "minimum": item.minimum,
                    "maximum": item.maximum,
                }
                for item in self.desired_states
            ],
            "temporal": None if self.temporal is None else {
                "deadline": _iso(self.temporal.deadline),
                "execute_at": _iso(self.temporal.execute_at),
                "day_part": self.temporal.day_part,
                "must_be_achieved_by_deadline": self.temporal.must_be_achieved_by_deadline,
            },
            "trigger": None if self.trigger is None else {
                "kind": self.trigger.kind,
                "person_entity_id": self.trigger.person_entity_id,
                "zone_id": self.trigger.zone_id,
                "from_state": self.trigger.from_state,
                "to_state": self.trigger.to_state,
                "household_person_ids": list(self.trigger.household_person_ids),
                "occurrence_id": self.trigger.occurrence_id,
                "entity_id": self.trigger.entity_id,
                "delta": self.trigger.delta,
                "unit": self.trigger.unit,
                "direction": self.trigger.direction,
                "window_seconds": self.trigger.window_seconds,
            },
            "conditions": [
                {
                    "kind": item.kind,
                    "scope": _scope_dict(item.scope),
                    "operator": item.operator,
                    "value": item.value,
                    "evaluate_at_trigger": item.evaluate_at_trigger,
                }
                for item in self.conditions
            ],
            "exclusions": _scope_dict(self.exclusions),
            "profile_id": self.profile_id,
            "routine_id": self.routine_id,
            "recipient_person_ids": list(self.recipient_person_ids),
            "delivery_channel": self.delivery_channel.value if self.delivery_channel else None,
            "notification_severity": self.notification_severity.value,
            "confirmation_required": self.confirmation_required,
            "success_criteria": [
                {
                    "criterion_id": item.criterion_id,
                    "kind": item.kind,
                    "scope": _scope_dict(item.scope),
                    "expected": item.expected,
                    "required": item.required,
                }
                for item in self.success_criteria
            ],
            "failure_handling": self.failure_handling,
            "provenance": {
                "source_utterance": self.provenance.source_utterance,
                "user_id": self.provenance.user_id,
                "conversation_id": self.provenance.conversation_id,
                "semantic_graph_ref": self.provenance.semantic_graph_ref,
                "confirmed": self.provenance.confirmed,
            },
            "lifecycle": self.lifecycle.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "GoalModel":
        scope = _scope_from(raw.get("scope"))
        desired_raw = raw.get("desired_states", ())
        desired = tuple(
            DesiredState(
                str(item.get("property_name", "")),
                cast(str | float | int | bool, item.get("value", "")),
                _optional_str(item.get("unit")),
                _optional_float(item.get("minimum")),
                _optional_float(item.get("maximum")),
            )
            for item in _mapping_sequence(desired_raw)
        )
        temporal_raw = raw.get("temporal")
        temporal = None
        if isinstance(temporal_raw, Mapping):
            temporal_mapping = cast(Mapping[str, object], temporal_raw)
            temporal = TemporalGoal(
                _datetime(temporal_mapping.get("deadline")),
                _datetime(temporal_mapping.get("execute_at")),
                _optional_str(temporal_mapping.get("day_part")),
                bool(temporal_mapping.get("must_be_achieved_by_deadline", False)),
            )
        trigger_raw = raw.get("trigger")
        trigger = None
        if isinstance(trigger_raw, Mapping):
            trigger_mapping = cast(Mapping[str, object], trigger_raw)
            trigger = GoalTrigger(
                str(trigger_mapping.get("kind", "")),
                _optional_str(trigger_mapping.get("person_entity_id")),
                _optional_str(trigger_mapping.get("zone_id")),
                _optional_str(trigger_mapping.get("from_state")),
                _optional_str(trigger_mapping.get("to_state")),
                _strings(trigger_mapping.get("household_person_ids")),
                _optional_str(trigger_mapping.get("occurrence_id")),
                _optional_str(trigger_mapping.get("entity_id")),
                _optional_float(trigger_mapping.get("delta")),
                _optional_str(trigger_mapping.get("unit")),
                _optional_str(trigger_mapping.get("direction")),
                _optional_int(trigger_mapping.get("window_seconds")),
            )
        conditions = tuple(
            GoalCondition(
                str(item.get("kind", "")),
                _scope_from(item.get("scope")),
                str(item.get("operator", "equals")),
                item.get("value"),
                bool(item.get("evaluate_at_trigger", True)),
            )
            for item in _mapping_sequence(raw.get("conditions", ()))
        )
        criteria = tuple(
            SuccessCriterion(
                str(item.get("criterion_id", "")),
                str(item.get("kind", "")),
                _scope_from(item.get("scope")),
                item.get("expected"),
                bool(item.get("required", True)),
            )
            for item in _mapping_sequence(raw.get("success_criteria", ()))
        )
        provenance_raw = raw.get("provenance")
        provenance = GoalProvenance()
        if isinstance(provenance_raw, Mapping):
            provenance_mapping = cast(Mapping[str, object], provenance_raw)
            provenance = GoalProvenance(
                str(provenance_mapping.get("source_utterance", "")),
                _optional_str(provenance_mapping.get("user_id")),
                _optional_str(provenance_mapping.get("conversation_id")),
                _optional_str(provenance_mapping.get("semantic_graph_ref")),
                bool(provenance_mapping.get("confirmed", False)),
            )
        params = raw.get("parameters", {})
        return cls(
            GoalKind(str(raw["kind"])),
            dict(cast(Mapping[str, object], params)) if isinstance(params, Mapping) else {},
            str(raw.get("goal_id", "")),
            scope,
            desired,
            temporal,
            trigger,
            conditions,
            _scope_from(raw.get("exclusions")),
            _optional_str(raw.get("profile_id")),
            _optional_str(raw.get("routine_id")),
            _strings(raw.get("recipient_person_ids")),
            DeliveryChannel(str(raw["delivery_channel"])) if raw.get("delivery_channel") else None,
            NotificationSeverity(str(raw.get("notification_severity", "info"))),
            bool(raw.get("confirmation_required", False)),
            criteria,
            str(raw.get("failure_handling", "report")),
            provenance,
            GoalLifecycle(str(raw.get("lifecycle", "one_shot"))),
        )


@dataclass(frozen=True)
class PendingGoalSemanticClarification:
    """Complete authority for one open goal-semantics choice."""

    goal: GoalModel
    requested_by_user_id: str | None
    conversation_id: str
    choices: tuple[GoalSemanticChoice, ...] = (
        GoalSemanticChoice.SETPOINT_AT_TIME,
        GoalSemanticChoice.ACHIEVE_BY_DEADLINE,
    )


def _scope_dict(scope: GoalScope) -> dict[str, object]:
    return {
        "entity_ids": list(scope.entity_ids),
        "domain": scope.domain,
        "device_class": scope.device_class,
        "area_id": scope.area_id,
        "floor_id": scope.floor_id,
        "person_ids": list(scope.person_ids),
        "excluded_entity_ids": list(scope.excluded_entity_ids),
        "excluded_area_ids": list(scope.excluded_area_ids),
    }


def _scope_from(value: object) -> GoalScope:
    if not isinstance(value, Mapping):
        return GoalScope()
    mapping = cast(Mapping[str, object], value)
    return GoalScope(
        _strings(mapping.get("entity_ids")),
        _optional_str(mapping.get("domain")),
        _optional_str(mapping.get("device_class")),
        _optional_str(mapping.get("area_id")),
        _optional_str(mapping.get("floor_id")),
        _strings(mapping.get("person_ids")),
        _strings(mapping.get("excluded_entity_ids")),
        _strings(mapping.get("excluded_area_ids")),
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in cast(Sequence[object], value) if isinstance(item, str))


def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    values = cast(Sequence[object], value)
    return tuple(cast(Mapping[str, object], item) for item in values if isinstance(item, Mapping))


def _empty_mapping() -> dict[str, object]:
    return {}


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value)


__all__ = (
    "DeliveryChannel", "DesiredState", "GoalCondition", "GoalKind",
    "GoalLifecycle", "GoalModel", "GoalProvenance", "GoalScope",
    "GoalSemanticChoice", "PendingGoalSemanticClarification",
    "GoalTrigger", "IntentClass", "NotificationSeverity", "SuccessCriterion",
    "TemporalGoal",
)
