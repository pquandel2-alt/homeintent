"""Typed V12 proactive-context boundary models.

Every V12 decision crosses a module boundary as one of these frozen types.
None of them can carry a ``ServiceCallPlan``: a proactive proposal describes
a desired *state* on explicit entity ids, and only the V10 planner may turn
that into an operator and a service call.

Invariants encoded here:

* PREDICTION != EXECUTION  - ``AnticipationResult`` has no action field.
* OPPORTUNITY != PERMISSION - ``OpportunityDecision`` only says whether to
  interrupt; it never authorizes an action.
* COMMUNICATION != DEVICE EXECUTION - ``CommunicationDecision`` names a
  channel and a target, never a device service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, StrEnum


class SituationKind(StrEnum):
    ENTRY_LEFT_OPEN = "entry_left_open"
    APPLIANCE_FINISHED = "appliance_finished"
    DEVICE_LEFT_ON_WHEN_LEAVING = "device_left_on_when_leaving"
    THERMAL_GOAL_AT_RISK = "thermal_goal_at_risk"
    DEVICE_EFFECT_ANOMALY = "device_effect_anomaly"
    HABIT_OPPORTUNITY = "habit_opportunity"
    PENDING_GOAL_REQUIRES_ATTENTION = "pending_goal_requires_attention"
    CRITICAL_SAFETY_EVENT = "critical_safety_event"
    # Owned and announced by NativeTimer.  V12 may record it in history but
    # never communicates it (one timer expiry -> one announcement).
    TIMER_FINISHED = "timer_finished"


class SituationState(StrEnum):
    DETECTED = "detected"
    ACTIVE = "active"
    COMMUNICATED = "communicated"
    ACKNOWLEDGED = "acknowledged"
    SNOOZED = "snoozed"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    SUPPRESSED = "suppressed"


TERMINAL_SITUATION_STATES = frozenset({
    SituationState.RESOLVED, SituationState.EXPIRED,
})
# States in which the condition is still ongoing but must not prompt again.
QUIET_SITUATION_STATES = frozenset({
    SituationState.ACKNOWLEDGED, SituationState.SUPPRESSED,
})


class PriorityLevel(IntEnum):
    INFO = 1
    SUGGESTION = 2
    IMPORTANT = 3
    URGENT = 4
    CRITICAL = 5


class PrivacyLevel(IntEnum):
    PUBLIC = 1
    HOUSEHOLD = 2
    PERSONAL = 3
    SENSITIVE = 4


class OpportunityOutcome(StrEnum):
    SUPPRESS = "suppress"
    HISTORY_ONLY = "history_only"
    COMMUNICATE = "communicate"
    ESCALATE = "escalate"


class AttentionOutcome(StrEnum):
    DELIVER = "deliver"
    GROUP = "group"
    DEFER = "defer"
    SUPPRESS = "suppress"
    BYPASS = "bypass"


class CommunicationChannel(StrEnum):
    VOICE = "voice"
    PUSH = "push"
    INTERACTIVE_PUSH = "interactive_push"
    HISTORY_ONLY = "history_only"
    MULTI_CHANNEL = "multi_channel"
    SUPPRESS = "suppress"


class RoomEvidenceClass(StrEnum):
    EXACT = "exact"
    STRONG = "strong"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


class AnticipationKind(StrEnum):
    NEXT_ACTION_LIKELY = "next_action_likely"
    GOAL_AT_RISK = "goal_at_risk"
    ROUTINE_OPPORTUNITY = "routine_opportunity"
    DEVICE_ANOMALY_RELEVANT = "device_anomaly_relevant"
    ATTENTION_MAY_BE_REQUIRED = "attention_may_be_required"


class ModelEvidenceStatus(StrEnum):
    """V11 prediction status as seen by V12; anything but OK is advisory."""

    OK = "ok"
    NOT_USED = "not_used"
    INSUFFICIENT = "insufficient"
    LOW_CONFIDENCE = "low_confidence"
    STALE = "stale"
    OUT_OF_DISTRIBUTION = "out_of_distribution"
    INVALID = "invalid"


class ProposalState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SNOOZED = "snoozed"
    DISMISSED = "dismissed"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


OPEN_PROPOSAL_STATES = frozenset({ProposalState.PENDING})


class SessionState(StrEnum):
    OPEN = "open"
    WAITING_FOR_REPLY = "waiting_for_reply"
    CONFIRMED = "confirmed"
    EXECUTING = "executing"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ProposalChoice(StrEnum):
    """Closed set of typed replies; push payloads carry only these names."""

    ACCEPT = "accept"
    LATER = "later"
    IGNORE = "ignore"
    REJECT = "reject"


class AutoOperator(StrEnum):
    """Closed operators a StandingPermission may name. No wildcard exists."""

    LIGHT_TURN_OFF = "light_turn_off"
    SWITCH_TURN_OFF = "switch_turn_off"
    FAN_TURN_OFF = "fan_turn_off"


class PermissionCondition(StrEnum):
    NOBODY_HOME = "nobody_home"


@dataclass(frozen=True)
class SituationEvidence:
    """One bounded, content-free fact; never raw HA attributes."""

    code: str
    value: str


@dataclass(frozen=True)
class ProactiveSituation:
    situation_id: str
    kind: SituationKind
    subject_ids: tuple[str, ...]
    area_id: str | None
    started_at: datetime
    updated_at: datetime
    state: SituationState
    evidence: tuple[SituationEvidence, ...]
    persons: tuple[str, ...]
    related_goal_ids: tuple[str, ...]
    priority_hint: PriorityLevel
    privacy_level: PrivacyLevel
    dedupe_key: str
    snooze_until: datetime | None = None
    communicated_at: datetime | None = None
    communication_count: int = 0
    owner_user_id: str | None = None
    subject_name: str = ""
    anticipation_ref: str | None = None

    def evidence_value(self, code: str) -> str | None:
        return next((item.value for item in self.evidence if item.code == code), None)


@dataclass(frozen=True)
class TargetState:
    """One explicit entity with one closed desired state."""

    entity_id: str
    desired_state: str
    name: str = ""


@dataclass(frozen=True)
class ProposedGoal:
    """A desired outcome only. The V10 planner chooses operator and service."""

    targets: tuple[TargetState, ...]
    description: str


@dataclass(frozen=True)
class RoomPresenceResult:
    person_id: str
    area_id: str | None
    evidence_class: RoomEvidenceClass
    observed_at: datetime | None
    valid_until: datetime | None
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class SatelliteRecord:
    entity_id: str
    area_id: str | None
    device_id: str | None = None
    source: str = "area_registry"


@dataclass(frozen=True)
class SatelliteResolution:
    area_id: str | None
    satellite: SatelliteRecord | None
    reason: str


@dataclass(frozen=True)
class RecipientContext:
    """Home-presence authority stays person.*; this only carries its result."""

    user_id: str
    person_id: str | None
    home: bool | None
    push_target_ids: tuple[str, ...]
    push_ambiguous: bool = False


@dataclass(frozen=True)
class ModelReference:
    model_id: str | None
    status: ModelEvidenceStatus
    explanation: str = ""


@dataclass(frozen=True)
class AnticipationResult:
    """Contextual anticipation. Deliberately has no action/service field."""

    kind: AnticipationKind
    situation_id: str
    model: ModelReference
    summary: str
    usable: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class PriorityDecision:
    level: PriorityLevel
    rule: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PrivacyDecision:
    level: PrivacyLevel
    rule: str


@dataclass(frozen=True)
class OpportunityDecision:
    outcome: OpportunityOutcome
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AttentionDecision:
    outcome: AttentionOutcome
    reasons: tuple[str, ...]
    group_key: str | None = None


@dataclass(frozen=True)
class CommunicationDecision:
    """Where to say something. It can never target a device action."""

    channel: CommunicationChannel
    channels: tuple[CommunicationChannel, ...]
    recipient_user_id: str | None
    satellite_entity_id: str | None
    push_target_ids: tuple[str, ...]
    requires_response: bool
    continue_conversation: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class QuietHoursWindow:
    start_minute: int
    end_minute: int

    def contains(self, minute_of_day: int) -> bool:
        if self.start_minute == self.end_minute:
            return False
        if self.start_minute < self.end_minute:
            return self.start_minute <= minute_of_day < self.end_minute
        return minute_of_day >= self.start_minute or minute_of_day < self.end_minute


@dataclass(frozen=True)
class PushActionBinding:
    """One interactive push delivery: an opaque token that was sent only to
    one Companion device, authoritatively resolved via UserContext ->
    notify entity -> HA device registry, for one authenticated HA user."""

    token: str
    user_id: str
    device_id: str


@dataclass(frozen=True)
class PendingProposal:
    proposal_id: str
    situation_id: str
    recipient_user_ids: tuple[str, ...]
    proposed_goal: ProposedGoal
    created_at: datetime
    expires_at: datetime
    channel: CommunicationChannel
    requires_confirmation: bool
    state: ProposalState
    privacy_level: PrivacyLevel
    subject_label: str
    origin_device_id: str | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    run_id: str | None = None
    result: str | None = None
    push_bindings: tuple[PushActionBinding, ...] = ()


@dataclass(frozen=True)
class ActiveGoalSession:
    session_id: str
    originating_situation_id: str
    proposal_id: str
    recipient_user_id: str | None
    recipient_person_id: str | None
    originating_channel: CommunicationChannel
    created_at: datetime
    expires_at: datetime
    state: SessionState
    pending_question: str
    goal_id: str | None = None
    context_refs: tuple[str, ...] = ()
    required_confirmation: bool = True


@dataclass(frozen=True)
class StandingPermission:
    permission_id: str
    owner_user_id: str
    situation_kind: SituationKind
    operator: AutoOperator
    entity_ids: tuple[str, ...]
    area_id: str | None
    conditions: tuple[PermissionCondition, ...]
    created_at: datetime
    expires_at: datetime
    confirmed: bool
    revoked: bool = False
    description: str = ""
    max_executions_per_day: int = 6


@dataclass(frozen=True)
class AutoExecutionDecision:
    allowed: bool
    reasons: tuple[str, ...]
    permission_id: str | None = None


@dataclass(frozen=True)
class HistoryRecord:
    record_id: str
    situation_id: str
    situation_kind: SituationKind
    subject_label: str
    decision: OpportunityOutcome
    recipient_user_id: str | None
    channel: CommunicationChannel
    timestamp: datetime
    priority: PriorityLevel
    privacy: PrivacyLevel
    result: str
    reasons: tuple[str, ...] = ()
    acknowledgement: str | None = None
    related_run_id: str | None = None
    evidence: tuple[SituationEvidence, ...] = ()
    model_ref: str | None = None
    # Further recipients of the identical decision (F25): one notice to a
    # household is one history entry, not one copy per person.
    other_recipient_user_ids: tuple[str, ...] = ()

    @property
    def recipient_user_ids(self) -> tuple[str, ...]:
        first = (self.recipient_user_id,) if self.recipient_user_id is not None else ()
        return (*first, *self.other_recipient_user_ids)

    def addressed_to(self, user_id: str | None) -> bool:
        return user_id is not None and user_id in self.recipient_user_ids


@dataclass(frozen=True)
class ContextSnapshot:
    """Bounded context for one evaluation. Never a copy of HA's state machine."""

    timestamp: datetime
    active_situations: tuple[ProactiveSituation, ...]
    recipients: tuple[RecipientContext, ...]
    nobody_home: bool | None
    room_presence: tuple[RoomPresenceResult, ...]
    active_goal_subjects: frozenset[str]
    pending_proposal_ids: tuple[str, ...]
    recent_communications: int
    quiet_user_ids: frozenset[str]
    prediction_refs: tuple[ModelReference, ...] = ()
    household_person_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def recipient(self, user_id: str) -> RecipientContext | None:
        return next((item for item in self.recipients if item.user_id == user_id), None)

    def room(self, person_id: str | None) -> RoomPresenceResult | None:
        if person_id is None:
            return None
        return next((item for item in self.room_presence if item.person_id == person_id), None)


__all__ = (
    "ActiveGoalSession",
    "AnticipationKind",
    "AnticipationResult",
    "AttentionDecision",
    "AttentionOutcome",
    "AutoExecutionDecision",
    "AutoOperator",
    "CommunicationChannel",
    "CommunicationDecision",
    "ContextSnapshot",
    "HistoryRecord",
    "ModelEvidenceStatus",
    "ModelReference",
    "OPEN_PROPOSAL_STATES",
    "OpportunityDecision",
    "OpportunityOutcome",
    "PendingProposal",
    "PermissionCondition",
    "PriorityDecision",
    "PriorityLevel",
    "PrivacyDecision",
    "PrivacyLevel",
    "ProactiveSituation",
    "ProposalChoice",
    "ProposalState",
    "ProposedGoal",
    "PushActionBinding",
    "QUIET_SITUATION_STATES",
    "QuietHoursWindow",
    "RecipientContext",
    "RoomEvidenceClass",
    "RoomPresenceResult",
    "SatelliteRecord",
    "SatelliteResolution",
    "SessionState",
    "SituationEvidence",
    "SituationKind",
    "SituationState",
    "StandingPermission",
    "TERMINAL_SITUATION_STATES",
    "TargetState",
)
