"""HomeIntent 7.1 Learning Center: read-only presentation over V11/V12.

The Learning Center is a TRANSPARENCY + CONTROL layer, never a second
learning authority.  This module only

* reads the existing authorities (``ModelRegistry``, ``ExperienceStore``,
  ``StandingPermissionStore``, ``AttentionStateStore``,
  ``ProactiveHistoryStore``, ``LearningPolicy``),
* applies the server-side visibility policy (PERSONAL / HOUSEHOLD / SYSTEM),
* maps the result to explicit, language-neutral view models.

It owns no persistent state.  Every mutation goes through
``learning_control`` or the V12 store's own method; the only runtime state
here is a content-free revision counter used for live refresh.

Everything returned is language neutral (stable keys, numbers, resolved
friendly names); the bundled panel localizes it.  ``LearnedModel.to_dict()``
is never exposed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from .attention_policy import AttentionStateStore
from .experience_store import ExperienceStore
from .learning_control import habit_decidable, model_owner, preference_confirmable
from .learning_policy import KnowledgeState, LearningPolicy
from .model_registry import LearnedKind, LearnedModel, ModelHealth, ModelRegistry
from .proactive_model import (
    HistoryRecord,
    PrivacyLevel,
    QuietHoursWindow,
    SituationKind,
    StandingPermission,
)
from .proactive_store import ProactiveHistoryStore
from .standing_permission import StandingPermissionStore


API_VERSION = 1
DEFAULT_MODEL_PAGE = 100
MAX_MODEL_PAGE = 250
DEFAULT_EVIDENCE = 10
MAX_EVIDENCE = 50
DEFAULT_HISTORY_PAGE = 40
MAX_HISTORY_PAGE = 100
PERMISSION_EXPIRY_WARNING = timedelta(days=14)
RECENT_ACTIVITY_WINDOW = timedelta(hours=24)
# Situation kinds that can never be muted (see ProactiveDialogHandler).
NON_MUTABLE_SITUATIONS = (SituationKind.CRITICAL_SAFETY_EVENT,)

JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonDict = dict[str, JsonValue]


class Visibility(StrEnum):
    PERSONAL = "personal"
    HOUSEHOLD = "household"
    SYSTEM = "system"


class ModelStatus(StrEnum):
    """Primary user-facing status; distinct from health and knowledge."""

    VALID = "valid"
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    LEARNING = "learning"
    STALE = "stale"
    DRIFT = "drift"
    UNRELIABLE = "unreliable"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class ModelCategory(StrEnum):
    HABITS = "habits"
    PREFERENCES = "preferences"
    HEATING = "heating"
    DEVICES = "devices"
    OTHER = "other"


class ModelAction(StrEnum):
    FORGET = "forget"
    CONFIRM_PREFERENCE = "confirm_preference"
    REJECT_PREFERENCE = "reject_preference"
    ACCEPT_HABIT = "accept_habit"
    REJECT_HABIT = "reject_habit"


class FactUnit(StrEnum):
    COUNT = "count"
    RATIO = "ratio"
    SECONDS = "seconds"
    CELSIUS = "celsius"
    DATETIME = "datetime"
    TEXT = "text"


class AttentionKind(StrEnum):
    PREFERENCE_PENDING = "preference_pending"
    HABIT_PENDING = "habit_pending"
    MODEL_DRIFT = "model_drift"
    MODEL_STALE = "model_stale"
    MODEL_INVALID = "model_invalid"
    MODEL_UNRELIABLE = "model_unreliable"
    ENTITY_MISSING = "entity_missing"
    PERMISSION_EXPIRING = "permission_expiring"


class AttentionSeverity(StrEnum):
    """UI relevance only - NOT the V12 safety PriorityPolicy."""

    INFO = "info"
    ACTION = "action"
    WARNING = "warning"


# -- viewer / sources ---------------------------------------------------------

@dataclass(frozen=True)
class Viewer:
    """The authenticated connection user; never taken from the browser."""

    user_id: str
    is_admin: bool


class LabelSource(Protocol):
    """Friendly labels resolved from live HA registries; never persisted."""

    def entity_name(self, entity_id: str) -> str | None: ...

    def area_name(self, area_id: str) -> str | None: ...

    def user_name(self, user_id: str) -> str | None: ...


@dataclass(frozen=True)
class StaticLabels:
    """Plain mapping implementation of ``LabelSource``."""

    entities: dict[str, str]
    areas: dict[str, str]
    users: dict[str, str]

    def entity_name(self, entity_id: str) -> str | None:
        return self.entities.get(entity_id)

    def area_name(self, area_id: str) -> str | None:
        return self.areas.get(area_id)

    def user_name(self, user_id: str) -> str | None:
        return self.users.get(user_id)


class ProactiveSource(Protocol):
    """The existing V12 authorities, read (and revoked/unmuted) in place."""

    @property
    def permissions(self) -> StandingPermissionStore: ...

    @property
    def attention_state(self) -> AttentionStateStore: ...

    @property
    def history(self) -> ProactiveHistoryStore: ...


@dataclass(frozen=True)
class ProactiveStatus:
    enabled: bool
    standing_permissions_enabled: bool
    quiet_default: QuietHoursWindow | None
    quiet_users: dict[str, QuietHoursWindow]


@dataclass(frozen=True)
class LearningCenterSources:
    registry: ModelRegistry | None
    policy: LearningPolicy | None
    experiences: ExperienceStore | None
    proactive: ProactiveSource | None
    proactive_status: ProactiveStatus | None


class LearningCenterError(Exception):
    """Machine-stable refusal, mapped to a WebSocket error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# -- revision -------------------------------------------------------------------

class LearningCenterRevision:
    """Runtime-only change counter; listeners receive only the number."""

    def __init__(self) -> None:
        self._value = 0
        self._listeners: list[Callable[[int], None]] = []

    @property
    def value(self) -> int:
        return self._value

    def bump(self) -> int:
        self._value += 1
        for listener in tuple(self._listeners):
            listener(self._value)
        return self._value

    def subscribe(self, listener: Callable[[int], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @property
    def listener_count(self) -> int:
        return len(self._listeners)


# -- view models -------------------------------------------------------------------

@dataclass(frozen=True)
class ModelFact:
    key: str
    value: float | int | str
    unit: FactUnit

    def to_dict(self) -> JsonDict:
        return {"key": self.key, "value": self.value, "unit": self.unit.value}


@dataclass(frozen=True)
class LearningModelListItem:
    ref: str
    kind: str
    category: ModelCategory
    visibility: Visibility
    owned_by_viewer: bool
    owner_label: str | None
    redacted: bool
    subject_label: str
    subject_missing: bool
    area_label: str | None
    action_key: str | None
    status: ModelStatus
    health: str
    knowledge_state: str
    sample_count: int
    headline: ModelFact | None
    last_observed: str
    needs_attention: bool
    actions: tuple[ModelAction, ...]

    def to_dict(self) -> JsonDict:
        return {
            "ref": self.ref,
            "kind": self.kind,
            "category": self.category.value,
            "visibility": self.visibility.value,
            "owned_by_viewer": self.owned_by_viewer,
            "owner_label": self.owner_label,
            "redacted": self.redacted,
            "subject_label": self.subject_label,
            "subject_missing": self.subject_missing,
            "area_label": self.area_label,
            "action_key": self.action_key,
            "status": self.status.value,
            "health": self.health,
            "knowledge_state": self.knowledge_state,
            "sample_count": self.sample_count,
            "headline": self.headline.to_dict() if self.headline else None,
            "last_observed": self.last_observed,
            "needs_attention": self.needs_attention,
            "actions": [item.value for item in self.actions],
        }


@dataclass(frozen=True)
class HabitStepView:
    entity_label: str
    entity_missing: bool
    action_key: str
    expected: str

    def to_dict(self) -> JsonDict:
        return {
            "entity_label": self.entity_label, "entity_missing": self.entity_missing,
            "action_key": self.action_key, "expected": self.expected,
        }


@dataclass(frozen=True)
class PreferenceChoiceView:
    entity_label: str
    entity_missing: bool
    count: int
    preferred: bool

    def to_dict(self) -> JsonDict:
        return {
            "entity_label": self.entity_label, "entity_missing": self.entity_missing,
            "count": self.count, "preferred": self.preferred,
        }


@dataclass(frozen=True)
class TechnicalDetails:
    model_id: str | None
    model_version: int
    kind: str
    health: str
    knowledge_state: str
    quality_score: float
    first_observed: str
    last_observed: str
    expires_at: str | None
    provenance_count: int
    validation_metrics: tuple[ModelFact, ...]
    invalidation_reason: str | None

    def to_dict(self) -> JsonDict:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "kind": self.kind,
            "health": self.health,
            "knowledge_state": self.knowledge_state,
            "quality_score": self.quality_score,
            "first_observed": self.first_observed,
            "last_observed": self.last_observed,
            "expires_at": self.expires_at,
            "provenance_count": self.provenance_count,
            "validation_metrics": [item.to_dict() for item in self.validation_metrics],
            "invalidation_reason": self.invalidation_reason,
        }


@dataclass(frozen=True)
class LearningModelDetail:
    item: LearningModelListItem
    quality_band: str
    first_observed: str
    expires_at: str | None
    confirmed_by_label: str | None
    confirmed_by_viewer: bool
    invalidation_reason: str | None
    facts: tuple[ModelFact, ...]
    habit_steps: tuple[HabitStepView, ...]
    preference_choices: tuple[PreferenceChoiceView, ...]
    evidence_available: bool
    technical: TechnicalDetails

    def to_dict(self) -> JsonDict:
        return {
            **self.item.to_dict(),
            "quality_band": self.quality_band,
            "first_observed": self.first_observed,
            "expires_at": self.expires_at,
            "confirmed_by_label": self.confirmed_by_label,
            "confirmed_by_viewer": self.confirmed_by_viewer,
            "invalidation_reason": self.invalidation_reason,
            "facts": [item.to_dict() for item in self.facts],
            "habit_steps": [item.to_dict() for item in self.habit_steps],
            "preference_choices": [item.to_dict() for item in self.preference_choices],
            "evidence_available": self.evidence_available,
            "technical": self.technical.to_dict(),
        }


@dataclass(frozen=True)
class EvidenceRow:
    timestamp: str
    entity_label: str
    action_key: str
    evidence_state: str
    latency_seconds: float | None

    def to_dict(self) -> JsonDict:
        return {
            "timestamp": self.timestamp, "entity_label": self.entity_label,
            "action_key": self.action_key, "evidence_state": self.evidence_state,
            "latency_seconds": self.latency_seconds,
        }


@dataclass(frozen=True)
class LearningEvidenceSummary:
    ref: str
    basis: str
    facts: tuple[ModelFact, ...]
    rows: tuple[EvidenceRow, ...]
    provenance_count: int
    truncated: bool

    def to_dict(self) -> JsonDict:
        return {
            "ref": self.ref, "basis": self.basis,
            "facts": [item.to_dict() for item in self.facts],
            "rows": [item.to_dict() for item in self.rows],
            "provenance_count": self.provenance_count,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class LearningCenterAttentionItem:
    id: str
    kind: AttentionKind
    severity: AttentionSeverity
    subject_label: str
    model_ref: str | None
    permission_id: str | None
    action_available: bool
    visibility: Visibility

    def to_dict(self) -> JsonDict:
        return {
            "id": self.id, "kind": self.kind.value, "severity": self.severity.value,
            "subject_label": self.subject_label, "model_ref": self.model_ref,
            "permission_id": self.permission_id,
            "action_available": self.action_available,
            "visibility": self.visibility.value,
        }


@dataclass(frozen=True)
class StandingPermissionView:
    permission_id: str
    owner_label: str
    owned_by_viewer: bool
    description: str
    situation_kind: str
    operator: str
    entities: tuple[tuple[str, bool], ...]
    area_label: str | None
    conditions: tuple[str, ...]
    created_at: str
    expires_at: str
    revoked: bool
    expired: bool
    attempts_today: int
    verified_today: int
    max_per_day: int
    can_revoke: bool

    def to_dict(self) -> JsonDict:
        return {
            "permission_id": self.permission_id,
            "owner_label": self.owner_label,
            "owned_by_viewer": self.owned_by_viewer,
            "description": self.description,
            "situation_kind": self.situation_kind,
            "operator": self.operator,
            "entities": [
                {"label": label, "missing": missing} for label, missing in self.entities
            ],
            "area_label": self.area_label,
            "conditions": list(self.conditions),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
            "expired": self.expired,
            "attempts_today": self.attempts_today,
            "verified_today": self.verified_today,
            "max_per_day": self.max_per_day,
            "can_revoke": self.can_revoke,
        }


@dataclass(frozen=True)
class MuteView:
    situation_kind: str
    confirmed_at: str
    can_remove: bool

    def to_dict(self) -> JsonDict:
        return {
            "situation_kind": self.situation_kind,
            "confirmed_at": self.confirmed_at,
            "can_remove": self.can_remove,
        }


@dataclass(frozen=True)
class HistoryView:
    record_id: str
    timestamp: str
    situation_kind: str
    subject_label: str
    decision: str
    channel: str
    result: str
    reasons: tuple[str, ...]
    recipient_is_viewer: bool
    recipient_label: str | None
    acknowledgement: str | None
    visibility: Visibility

    def to_dict(self) -> JsonDict:
        return {
            "record_id": self.record_id, "timestamp": self.timestamp,
            "situation_kind": self.situation_kind,
            "subject_label": self.subject_label, "decision": self.decision,
            "channel": self.channel, "result": self.result,
            "reasons": list(self.reasons),
            "recipient_is_viewer": self.recipient_is_viewer,
            "recipient_label": self.recipient_label,
            "acknowledgement": self.acknowledgement,
            "visibility": self.visibility.value,
        }


@dataclass(frozen=True)
class TombstoneView:
    model_id: str
    deleted_at: str
    reason: str
    suppression_kind: str
    source_cutoff: str
    durable: bool

    def to_dict(self) -> JsonDict:
        return {
            "model_id": self.model_id, "deleted_at": self.deleted_at,
            "reason": self.reason, "suppression_kind": self.suppression_kind,
            "source_cutoff": self.source_cutoff, "durable": self.durable,
        }


@dataclass(frozen=True)
class FeatureStatus:
    learning_mode: str
    learning_enabled: bool
    predictive_models_enabled: bool
    habit_discovery_enabled: bool
    suggestions_enabled: bool
    proactive_enabled: bool
    standing_permissions_enabled: bool
    quiet_hours: str | None
    quiet_hours_personal: bool

    def to_dict(self) -> JsonDict:
        return {
            "learning_mode": self.learning_mode,
            "learning_enabled": self.learning_enabled,
            "predictive_models_enabled": self.predictive_models_enabled,
            "habit_discovery_enabled": self.habit_discovery_enabled,
            "suggestions_enabled": self.suggestions_enabled,
            "proactive_enabled": self.proactive_enabled,
            "standing_permissions_enabled": self.standing_permissions_enabled,
            "quiet_hours": self.quiet_hours,
            "quiet_hours_personal": self.quiet_hours_personal,
        }


@dataclass(frozen=True)
class LearningCenterSummary:
    entry_id: str
    revision: int
    is_admin: bool
    total_models: int
    valid_models: int
    learning_models: int
    stale_models: int
    invalid_models: int
    needs_attention: int
    category_counts: dict[str, int]
    habit_count: int
    preference_count: int
    thermal_count: int
    effect_timing_count: int
    reliability_count: int
    standing_permission_count: int
    mute_count: int
    recent_activity_count: int
    features: FeatureStatus
    attention: tuple[LearningCenterAttentionItem, ...]
    non_mutable_situations: tuple[str, ...]

    def to_dict(self) -> JsonDict:
        return {
            "api_version": API_VERSION,
            "entry_id": self.entry_id,
            "revision": self.revision,
            "is_admin": self.is_admin,
            "total_models": self.total_models,
            "valid_models": self.valid_models,
            "learning_models": self.learning_models,
            "stale_models": self.stale_models,
            "invalid_models": self.invalid_models,
            "needs_attention": self.needs_attention,
            "category_counts": dict[str, JsonValue](self.category_counts),
            "habit_count": self.habit_count,
            "preference_count": self.preference_count,
            "thermal_count": self.thermal_count,
            "effect_timing_count": self.effect_timing_count,
            "reliability_count": self.reliability_count,
            "standing_permission_count": self.standing_permission_count,
            "mute_count": self.mute_count,
            "recent_activity_count": self.recent_activity_count,
            "features": self.features.to_dict(),
            "attention": [item.to_dict() for item in self.attention],
            "non_mutable_situations": list(self.non_mutable_situations),
        }


# -- pure policy / mapping helpers ---------------------------------------------------

HOUSEHOLD_KINDS = frozenset({
    LearnedKind.THERMAL_MODEL, LearnedKind.EFFECT_TIMING, LearnedKind.RELIABILITY,
})

_ACTION_SUFFIXES = (
    "set_cover_position", "open_cover", "close_cover", "stop_cover",
    "set_temperature", "set_hvac_mode", "set_percentage", "select_option",
    "set_value", "volume_set", "media_play", "media_pause", "return_to_base",
    "turn_on", "turn_off", "toggle", "unlock", "lock", "open", "close",
    "press", "start",
)


def model_ref(model_id: str) -> str:
    """Opaque, URL-safe reference; raw model ids stay a technical detail."""
    return hashlib.sha256(model_id.encode()).hexdigest()[:24]


def model_visibility(model: LearnedModel) -> Visibility:
    if model_owner(model) is not None:
        return Visibility.PERSONAL
    if model.kind in HOUSEHOLD_KINDS:
        return Visibility.HOUSEHOLD
    return Visibility.SYSTEM


def can_view_model(model: LearnedModel, viewer: Viewer) -> bool:
    """Server-side read authorization (filtering never happens in the browser)."""
    visibility = model_visibility(model)
    if visibility is Visibility.PERSONAL:
        return model_owner(model) == viewer.user_id or viewer.is_admin
    if visibility is Visibility.HOUSEHOLD:
        return True
    return viewer.is_admin


def is_redacted(model: LearnedModel, viewer: Viewer) -> bool:
    """An admin managing somebody else's personal model sees no behaviour."""
    owner = model_owner(model)
    return owner is not None and owner != viewer.user_id


def can_forget_model(model: LearnedModel, viewer: Viewer) -> bool:
    owner = model_owner(model)
    if owner is not None:
        return owner == viewer.user_id or viewer.is_admin
    return viewer.is_admin


def model_status(model: LearnedModel, now: datetime) -> ModelStatus:
    """Deterministic presentation of explicit model state (never confidence)."""
    health = model.health
    if health is ModelHealth.INVALID or model.invalidation_reason:
        return ModelStatus.INVALID
    if health is ModelHealth.DRIFT_DETECTED:
        return ModelStatus.DRIFT
    if health is ModelHealth.STALE or (
        model.expires_at is not None and now >= model.expires_at
    ):
        return ModelStatus.STALE
    if health is ModelHealth.UNRELIABLE:
        return ModelStatus.UNRELIABLE
    if model.knowledge_state is KnowledgeState.CONFIRMED:
        return ModelStatus.CONFIRMED
    if health is ModelHealth.LOW_CONFIDENCE:
        return ModelStatus.LEARNING
    if health is ModelHealth.VALID:
        if model.knowledge_state is KnowledgeState.INFERRED:
            return ModelStatus.INFERRED
        return ModelStatus.VALID
    return ModelStatus.UNKNOWN


def model_category(kind: LearnedKind) -> ModelCategory:
    if kind is LearnedKind.HABIT:
        return ModelCategory.HABITS
    if kind is LearnedKind.PREFERENCE:
        return ModelCategory.PREFERENCES
    if kind is LearnedKind.THERMAL_MODEL:
        return ModelCategory.HEATING
    if kind in {LearnedKind.EFFECT_TIMING, LearnedKind.RELIABILITY}:
        return ModelCategory.DEVICES
    return ModelCategory.OTHER


def action_key(operator_id: str | None) -> str:
    """Closed action vocabulary from a V10 operator id ("LIGHT_TURN_ON")."""
    if not operator_id:
        return "other"
    normalized = operator_id.casefold().replace(".", "_")
    for suffix in _ACTION_SUFFIXES:
        if normalized == suffix or normalized.endswith("_" + suffix):
            return suffix
    return "other"


def reliability_counts(model: LearnedModel) -> tuple[int, int] | None:
    """(verified successes, verified failures) under V11 evidence semantics.

    ``sample_count`` of a RELIABILITY model counts only service-accepted
    actions with VERIFIED_SUCCESS/VERIFIED_FAILURE effects; no-ops and
    unverified runs never enter it (see ``LearningManager``).
    """
    rate = model.parameters.get("success_rate")
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        return None
    successes = round(float(rate) * model.sample_count)
    return successes, model.sample_count - successes


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _window_text(window: QuietHoursWindow | None) -> str | None:
    if window is None or window.start_minute == window.end_minute:
        return None
    return (
        f"{window.start_minute // 60:02d}:{window.start_minute % 60:02d}"
        f"–{window.end_minute // 60:02d}:{window.end_minute % 60:02d}"
    )


def _habit_steps(model: LearnedModel) -> tuple[tuple[str, str, str], ...]:
    """(operator, entity_id, expected) from the persisted closed encoding."""
    raw = model.parameters.get("sequence")
    if not isinstance(raw, str) or not raw:
        return ()
    steps: list[tuple[str, str, str]] = []
    for encoded in raw.split("|")[:20]:
        operator, separator, remainder = encoded.partition("@")
        entity_id, value_separator, expected = remainder.partition("=")
        if not separator or not value_separator or "." not in entity_id:
            return ()
        steps.append((operator, entity_id, expected))
    return tuple(steps)


def _preference_counts(model: LearnedModel) -> tuple[tuple[str, int], ...]:
    counts: list[tuple[str, int]] = []
    for key, value in model.parameters.items():
        if key.startswith("count:") and isinstance(value, int) and not isinstance(value, bool):
            counts.append((key[6:], value))
    counts.sort(key=lambda item: (-item[1], item[0]))
    return tuple(counts[:20])


# -- service -------------------------------------------------------------------------

class LearningCenterService:
    """Read-oriented orchestration for one config entry and one viewer."""

    def __init__(
        self,
        entry_id: str,
        sources: LearningCenterSources,
        labels: LabelSource,
        *,
        now: datetime,
        revision: int = 0,
    ) -> None:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        self.entry_id = entry_id
        self.sources = sources
        self.labels = labels
        self.now = now
        self.revision = revision

    # ---- labels
    def _entity(self, entity_id: str) -> tuple[str, bool]:
        name = self.labels.entity_name(entity_id)
        return (name, False) if name else (entity_id, True)

    def _user(self, user_id: str | None) -> str | None:
        if user_id is None:
            return None
        return self.labels.user_name(user_id) or None

    def _recipient_label(self, record: HistoryRecord, viewer: Viewer) -> str | None:
        # A household entry names every recipient; a personal one only ever
        # names the viewer themself.
        if record.privacy >= PrivacyLevel.PERSONAL:
            return self._user(viewer.user_id) if record.addressed_to(viewer.user_id) else None
        names = [name for user_id in record.recipient_user_ids if (name := self._user(user_id))]
        return ", ".join(names) or None

    def _area(self, area_id: object) -> str | None:
        if not isinstance(area_id, str) or not area_id:
            return None
        return self.labels.area_name(area_id) or area_id

    def _subject(self, model: LearnedModel) -> tuple[str, bool, str | None]:
        """(label, entity_missing, area_label) for a model."""
        if model.kind in {LearnedKind.RELIABILITY, LearnedKind.EFFECT_TIMING}:
            label, missing = self._entity(model.subject)
            return label, missing, None
        if model.kind is LearnedKind.THERMAL_MODEL:
            area = self._area(model.context.get("area_id") or model.subject) or model.subject
            climate = model.parameters.get("climate_entity_id")
            missing = isinstance(climate, str) and self._entity(climate)[1]
            return area, missing, area
        if model.kind is LearnedKind.PREFERENCE:
            entity_id = model.parameters.get("entity_id")
            missing = isinstance(entity_id, str) and self._entity(entity_id)[1]
            return model.subject, missing, self._area(model.context.get("area_id"))
        if model.kind is LearnedKind.HABIT:
            band = model.context.get("time_band")
            missing = any(self._entity(entity_id)[1] for _op, entity_id, _exp in _habit_steps(model))
            return (band if isinstance(band, str) else "habit"), missing, None
        return model.subject, False, None

    # ---- registry access
    async def _models(self) -> tuple[LearnedModel, ...]:
        registry = self.sources.registry
        if registry is None:
            return ()
        return await registry.async_list()

    async def async_visible_models(self, viewer: Viewer) -> tuple[LearnedModel, ...]:
        return tuple(item for item in await self._models() if can_view_model(item, viewer))

    async def async_resolve(self, ref: str, viewer: Viewer) -> LearnedModel:
        """Find a model by opaque ref; invisible models are simply not found."""
        for model in await self._models():
            if model_ref(model.model_id) == ref:
                if not can_view_model(model, viewer):
                    raise LearningCenterError("not_found")
                return model
        raise LearningCenterError("not_found")

    # ---- mapping
    def _actions(self, model: LearnedModel, viewer: Viewer) -> tuple[ModelAction, ...]:
        actions: list[ModelAction] = []
        owner = model_owner(model)
        if owner == viewer.user_id:
            if (
                preference_confirmable(model)
                and model.parameters.get("suggestion_status") != "rejected"
            ):
                actions.append(ModelAction.CONFIRM_PREFERENCE)
            if (
                model.kind is LearnedKind.PREFERENCE
                and model.knowledge_state is not KnowledgeState.CONFIRMED
                and model.parameters.get("suggestion_status") != "rejected"
            ):
                actions.append(ModelAction.REJECT_PREFERENCE)
            if habit_decidable(model):
                if _habit_steps(model):
                    actions.append(ModelAction.ACCEPT_HABIT)
                actions.append(ModelAction.REJECT_HABIT)
        if can_forget_model(model, viewer):
            actions.append(ModelAction.FORGET)
        return tuple(actions)

    def _headline(self, model: LearnedModel, redacted: bool) -> ModelFact | None:
        if redacted:
            return None
        if model.kind is LearnedKind.RELIABILITY:
            rate = _number(model.parameters.get("success_rate"))
            return ModelFact("success_rate", rate, FactUnit.RATIO) if rate is not None else None
        if model.kind is LearnedKind.EFFECT_TIMING:
            median = _number(model.parameters.get("median_seconds"))
            return ModelFact("median_seconds", median, FactUnit.SECONDS) if median is not None else None
        if model.kind is LearnedKind.THERMAL_MODEL:
            holdout = model.validation_metrics.get("holdout_mae_seconds")
            if holdout is not None:
                return ModelFact("holdout_mae_seconds", holdout, FactUnit.SECONDS)
            return None
        if model.kind is LearnedKind.HABIT:
            support = _number(model.parameters.get("support"))
            return ModelFact("support", support, FactUnit.RATIO) if support is not None else None
        if model.kind is LearnedKind.PREFERENCE:
            entity_id = model.parameters.get("entity_id")
            if isinstance(entity_id, str):
                return ModelFact("preferred_entity", self._entity(entity_id)[0], FactUnit.TEXT)
        return None

    def _needs_attention(self, model: LearnedModel, viewer: Viewer, status: ModelStatus, missing: bool) -> bool:
        return bool(self._model_attention(model, viewer, status, missing))

    def list_item(self, model: LearnedModel, viewer: Viewer) -> LearningModelListItem:
        status = model_status(model, self.now)
        redacted = is_redacted(model, viewer)
        label, missing, area = self._subject(model)
        if redacted and model.kind is LearnedKind.PREFERENCE:
            label = ""
        owner = model_owner(model)
        operator = model.context.get("operator_id")
        return LearningModelListItem(
            model_ref(model.model_id), model.kind.value, model_category(model.kind),
            model_visibility(model), owner is not None and owner == viewer.user_id,
            self._user(owner) or (owner if owner is not None and viewer.is_admin and redacted else None),
            redacted, label, missing, area,
            action_key(operator) if isinstance(operator, str) else None,
            status, model.health.value, model.knowledge_state.value,
            model.sample_count, self._headline(model, redacted),
            model.last_observed.isoformat(),
            self._needs_attention(model, viewer, status, missing),
            self._actions(model, viewer),
        )

    def _facts(self, model: LearnedModel) -> tuple[ModelFact, ...]:
        facts: list[ModelFact] = []
        params = model.parameters
        if model.kind is LearnedKind.RELIABILITY:
            counts = reliability_counts(model)
            facts.append(ModelFact("observed_actions", model.sample_count, FactUnit.COUNT))
            if counts is not None:
                facts.append(ModelFact("verified_successes", counts[0], FactUnit.COUNT))
                facts.append(ModelFact("verified_failures", counts[1], FactUnit.COUNT))
                rate = _number(params.get("success_rate"))
                if rate is not None:
                    facts.append(ModelFact("success_rate", rate, FactUnit.RATIO))
        elif model.kind is LearnedKind.EFFECT_TIMING:
            facts.append(ModelFact("observations", model.sample_count, FactUnit.COUNT))
            for key in ("median_seconds", "p90_seconds", "p95_seconds", "mad_seconds"):
                value = _number(params.get(key))
                if value is not None:
                    facts.append(ModelFact(key, value, FactUnit.SECONDS))
        elif model.kind is LearnedKind.THERMAL_MODEL:
            facts.append(ModelFact("heating_cycles", model.sample_count, FactUnit.COUNT))
            for key in ("holdout_mae_seconds", "holdout_median_absolute_error_seconds",
                        "holdout_p90_absolute_error_seconds"):
                value = model.validation_metrics.get(key)
                if value is not None:
                    facts.append(ModelFact(key, value, FactUnit.SECONDS))
            validation_count = params.get("validation_sample_count")
            if isinstance(validation_count, int) and not isinstance(validation_count, bool) and validation_count > 0:
                facts.append(ModelFact("validation_sample_count", validation_count, FactUnit.COUNT))
            for key in ("min_start_temperature", "max_start_temperature"):
                value = _number(params.get(key))
                if value is not None:
                    facts.append(ModelFact(key, value, FactUnit.CELSIUS))
            for key in ("min_temperature_delta", "max_temperature_delta",
                        "min_outdoor_gap", "max_outdoor_gap"):
                value = _number(params.get(key))
                if value is not None:
                    facts.append(ModelFact(key, value, FactUnit.CELSIUS))
        elif model.kind is LearnedKind.HABIT:
            facts.append(ModelFact("occurrences", model.sample_count, FactUnit.COUNT))
            opportunities = params.get("opportunity_count")
            if isinstance(opportunities, int) and not isinstance(opportunities, bool) and opportunities > 0:
                facts.append(ModelFact("opportunities", opportunities, FactUnit.COUNT))
            support = _number(params.get("support"))
            if support is not None:
                facts.append(ModelFact("support", support, FactUnit.RATIO))
            band = model.context.get("time_band")
            if isinstance(band, str):
                facts.append(ModelFact("time_band", band, FactUnit.TEXT))
            status = params.get("suggestion_status")
            if isinstance(status, str):
                facts.append(ModelFact("suggestion_status", status, FactUnit.TEXT))
        elif model.kind is LearnedKind.PREFERENCE:
            facts.append(ModelFact("selections", model.sample_count, FactUnit.COUNT))
            support = params.get("support_count")
            if isinstance(support, int) and not isinstance(support, bool):
                facts.append(ModelFact("support_count", support, FactUnit.COUNT))
            status = params.get("suggestion_status")
            if isinstance(status, str):
                facts.append(ModelFact("suggestion_status", status, FactUnit.TEXT))
        else:
            facts.append(ModelFact("samples", model.sample_count, FactUnit.COUNT))
        return tuple(facts)

    def detail(self, model: LearnedModel, viewer: Viewer) -> LearningModelDetail:
        item = self.list_item(model, viewer)
        redacted = item.redacted
        steps: tuple[HabitStepView, ...] = ()
        choices: tuple[PreferenceChoiceView, ...] = ()
        if not redacted and model.kind is LearnedKind.HABIT:
            steps = tuple(
                HabitStepView(*self._entity(entity_id), action_key(operator), expected)
                for operator, entity_id, expected in _habit_steps(model)
            )
        if not redacted and model.kind is LearnedKind.PREFERENCE:
            preferred = model.parameters.get("entity_id")
            counts = _preference_counts(model)
            if not counts and isinstance(preferred, str):
                counts = ((preferred, model.sample_count),)
            choices = tuple(
                PreferenceChoiceView(*self._entity(entity_id), count, entity_id == preferred)
                for entity_id, count in counts
            )
        policy = self.sources.policy
        band = policy.confidence_band(model.confidence).value if policy is not None else "unknown"
        technical = TechnicalDetails(
            model.model_id if viewer.is_admin else None,
            model.model_version, model.kind.value, model.health.value,
            model.knowledge_state.value, round(model.confidence, 4),
            model.first_observed.isoformat(), model.last_observed.isoformat(),
            _iso(model.expires_at), len(model.provenance),
            () if redacted else tuple(
                ModelFact(key, value, FactUnit.SECONDS if key.endswith("_seconds") else FactUnit.COUNT)
                for key, value in sorted(model.validation_metrics.items())
            ),
            model.invalidation_reason,
        )
        confirmed_by = model.confirmed_by
        return LearningModelDetail(
            item, band, model.first_observed.isoformat(), _iso(model.expires_at),
            self._user(confirmed_by), confirmed_by is not None and confirmed_by == viewer.user_id,
            model.invalidation_reason,
            () if redacted else self._facts(model), steps, choices,
            not redacted, technical,
        )

    # ---- evidence
    async def async_evidence(
        self, model: LearnedModel, viewer: Viewer, *, limit: int = DEFAULT_EVIDENCE
    ) -> LearningEvidenceSummary:
        if is_redacted(model, viewer):
            raise LearningCenterError("not_authorized")
        bound = max(1, min(MAX_EVIDENCE, limit))
        ref = model_ref(model.model_id)
        facts = self._facts(model)
        rows: tuple[EvidenceRow, ...] = ()
        truncated = False
        if model.kind in {LearnedKind.RELIABILITY, LearnedKind.EFFECT_TIMING}:
            basis = "verified_actions" if model.kind is LearnedKind.RELIABILITY else "verified_timings"
            experiences = self.sources.experiences
            if experiences is not None and model.provenance:
                records = await experiences.async_get_many(model.provenance, limit=bound)
                rows = tuple(
                    EvidenceRow(
                        record.timestamp.isoformat(),
                        self._entity(record.action.target_id)[0],
                        action_key(record.action.operator_id),
                        record.effect.evidence_state.value,
                        record.effect.latency_seconds,
                    )
                    for record in records
                )
                truncated = len(model.provenance) > len(rows)
        elif model.kind is LearnedKind.THERMAL_MODEL:
            basis = "heating_cycles"
        elif model.kind is LearnedKind.HABIT:
            basis = "goal_runs"
        elif model.kind is LearnedKind.PREFERENCE:
            basis = (
                "explicit_feedback"
                if "explicit_user_feedback" in model.provenance else "clarification_selections"
            )
        else:
            basis = "samples"
        return LearningEvidenceSummary(ref, basis, facts, rows, len(model.provenance), truncated)

    # ---- attention
    def _model_attention(
        self, model: LearnedModel, viewer: Viewer, status: ModelStatus, missing: bool
    ) -> tuple[tuple[AttentionKind, AttentionSeverity, bool], ...]:
        if is_redacted(model, viewer):
            return ()
        items: list[tuple[AttentionKind, AttentionSeverity, bool]] = []
        owner = model_owner(model)
        if (
            owner == viewer.user_id
            and preference_confirmable(model)
            and model.health is ModelHealth.VALID
            and model.parameters.get("suggestion_status") not in {"accepted", "rejected"}
            and not missing
        ):
            items.append((AttentionKind.PREFERENCE_PENDING, AttentionSeverity.ACTION, True))
        if (
            owner == viewer.user_id
            and habit_decidable(model)
            and model.health is ModelHealth.VALID
        ):
            items.append((AttentionKind.HABIT_PENDING, AttentionSeverity.ACTION, True))
        shared_manageable = owner is not None or viewer.is_admin
        if status is ModelStatus.DRIFT:
            items.append((AttentionKind.MODEL_DRIFT, AttentionSeverity.WARNING, shared_manageable))
        elif status is ModelStatus.INVALID:
            items.append((AttentionKind.MODEL_INVALID, AttentionSeverity.WARNING, shared_manageable))
        elif status is ModelStatus.STALE:
            items.append((AttentionKind.MODEL_STALE, AttentionSeverity.INFO, shared_manageable))
        elif status is ModelStatus.UNRELIABLE:
            items.append((AttentionKind.MODEL_UNRELIABLE, AttentionSeverity.INFO, shared_manageable))
        if missing:
            items.append((AttentionKind.ENTITY_MISSING, AttentionSeverity.WARNING, shared_manageable))
        return tuple(items)

    def _attention_for_models(
        self, models: tuple[LearnedModel, ...], viewer: Viewer
    ) -> list[LearningCenterAttentionItem]:
        result: list[LearningCenterAttentionItem] = []
        for model in models:
            try:
                status = model_status(model, self.now)
                label, missing, _area = self._subject(model)
                entries = self._model_attention(model, viewer, status, missing)
            except (TypeError, ValueError):
                continue
            ref = model_ref(model.model_id)
            for kind, severity, actionable in entries:
                result.append(LearningCenterAttentionItem(
                    f"{kind.value}:{ref}", kind, severity, label, ref, None,
                    actionable, model_visibility(model),
                ))
        return result

    # ---- V12 views
    def _visible_permissions(self, viewer: Viewer) -> tuple[StandingPermission, ...]:
        proactive = self.sources.proactive
        if proactive is None:
            return ()
        return tuple(
            item for item in proactive.permissions.all()
            if item.owner_user_id == viewer.user_id or viewer.is_admin
        )

    def permission_view(self, permission: StandingPermission, viewer: Viewer) -> StandingPermissionView:
        proactive = self.sources.proactive
        attempts = verified = 0
        if proactive is not None:
            attempts = proactive.permissions.attempts_today(permission.permission_id, self.now)
            verified = proactive.permissions.verified_executions_today(permission.permission_id, self.now)
        owned = permission.owner_user_id == viewer.user_id
        expired = self.now >= permission.expires_at
        return StandingPermissionView(
            permission.permission_id,
            self._user(permission.owner_user_id) or ("" if owned else "?"),
            owned, permission.description, permission.situation_kind.value,
            permission.operator.value,
            tuple(self._entity(entity_id) for entity_id in permission.entity_ids),
            self._area(permission.area_id),
            tuple(item.value for item in permission.conditions),
            permission.created_at.isoformat(), permission.expires_at.isoformat(),
            permission.revoked, expired, attempts, verified,
            permission.max_executions_per_day,
            not permission.revoked and (owned or viewer.is_admin),
        )

    def permissions(self, viewer: Viewer) -> tuple[StandingPermissionView, ...]:
        views = [self.permission_view(item, viewer) for item in self._visible_permissions(viewer)]
        views.sort(key=lambda item: (item.revoked or item.expired, item.created_at), reverse=False)
        return tuple(views)

    def mutes(self, viewer: Viewer) -> tuple[MuteView, ...]:
        """A mute is a personal communication preference: owner only."""
        proactive = self.sources.proactive
        if proactive is None:
            return ()
        return tuple(
            MuteView(kind.value, confirmed_at.isoformat(), True)
            for user_id, kind, confirmed_at in proactive.attention_state.mutes()
            if user_id == viewer.user_id
        )

    def _history_visible(self, record: HistoryRecord, viewer: Viewer) -> bool:
        # Same rule as ProactiveContextEngine.history_summary/explain_latest:
        # PERSONAL (and stricter) history belongs to its recipient only; an
        # administrator gets no exception.
        if record.privacy >= PrivacyLevel.PERSONAL:
            return record.addressed_to(viewer.user_id)
        return True

    def history(
        self, viewer: Viewer, *, cursor: int = 0, limit: int = DEFAULT_HISTORY_PAGE
    ) -> tuple[tuple[HistoryView, ...], int | None]:
        proactive = self.sources.proactive
        if proactive is None:
            return (), None
        bound = max(1, min(MAX_HISTORY_PAGE, limit))
        start = max(0, cursor)
        visible = [
            item for item in reversed(proactive.history.records())
            if self._history_visible(item, viewer)
        ]
        page = visible[start:start + bound]
        views = tuple(
            HistoryView(
                item.record_id, item.timestamp.isoformat(), item.situation_kind.value,
                item.subject_label, item.decision.value, item.channel.value,
                item.result, item.reasons[:6],
                item.addressed_to(viewer.user_id),
                self._recipient_label(item, viewer),
                item.acknowledgement,
                Visibility.PERSONAL if item.privacy >= PrivacyLevel.PERSONAL else Visibility.HOUSEHOLD,
            )
            for item in page
        )
        next_cursor = start + bound if start + bound < len(visible) else None
        return views, next_cursor

    async def async_tombstones(self, viewer: Viewer) -> tuple[TombstoneView, ...]:
        if not viewer.is_admin:
            raise LearningCenterError("admin_required")
        registry = self.sources.registry
        if registry is None:
            return ()
        records = await registry.async_list_tombstones()
        return tuple(
            TombstoneView(
                item.model_id, item.deleted_at.isoformat(), item.reason,
                item.suppression_kind.value, item.source_cutoff.isoformat(),
                item.durable,
            )
            for item in sorted(records, key=lambda value: value.deleted_at, reverse=True)[:MAX_MODEL_PAGE]
        )

    # ---- list / summary
    async def async_list(
        self, viewer: Viewer, *, offset: int = 0, limit: int = DEFAULT_MODEL_PAGE
    ) -> tuple[tuple[LearningModelListItem, ...], int, int | None]:
        bound = max(1, min(MAX_MODEL_PAGE, limit))
        start = max(0, offset)
        visible = sorted(
            await self.async_visible_models(viewer),
            key=lambda item: (item.last_observed, item.model_id), reverse=True,
        )
        items: list[LearningModelListItem] = []
        for model in visible[start:start + bound]:
            try:
                items.append(self.list_item(model, viewer))
            except (TypeError, ValueError, KeyError):
                # One malformed model must never break the whole panel.
                continue
        next_offset = start + bound if start + bound < len(visible) else None
        return tuple(items), len(visible), next_offset

    def features(self, viewer: Viewer) -> FeatureStatus:
        policy = self.sources.policy
        status = self.sources.proactive_status
        personal_window = status.quiet_users.get(viewer.user_id) if status is not None else None
        window = personal_window or (status.quiet_default if status is not None else None)
        return FeatureStatus(
            policy.learning_mode.value if policy is not None else "off",
            policy is not None and policy.learning_mode.value != "off",
            policy is not None and policy.predictive_models_enabled,
            policy is not None and policy.habit_discovery_enabled,
            policy is not None and policy.suggestions_enabled,
            status is not None and status.enabled,
            status is not None and status.standing_permissions_enabled,
            _window_text(window), personal_window is not None,
        )

    async def async_summary(self, viewer: Viewer) -> LearningCenterSummary:
        models = await self.async_visible_models(viewer)
        statuses = [model_status(item, self.now) for item in models]
        kinds = [item.kind for item in models]
        category_counts: dict[str, int] = {}
        for kind in kinds:
            key = model_category(kind).value
            category_counts[key] = category_counts.get(key, 0) + 1
        attention = self._attention_for_models(models, viewer)
        permissions = self._visible_permissions(viewer)
        for permission in permissions:
            if (
                not permission.revoked
                and self.now < permission.expires_at <= self.now + PERMISSION_EXPIRY_WARNING
            ):
                attention.append(LearningCenterAttentionItem(
                    f"permission_expiring:{permission.permission_id}",
                    AttentionKind.PERMISSION_EXPIRING, AttentionSeverity.INFO,
                    permission.description, None, permission.permission_id,
                    permission.owner_user_id == viewer.user_id or viewer.is_admin,
                    Visibility.PERSONAL,
                ))
        proactive = self.sources.proactive
        recent = 0
        if proactive is not None:
            recent = sum(
                1 for item in proactive.history.since(self.now - RECENT_ACTIVITY_WINDOW)
                if self._history_visible(item, viewer)
            )
        severity_rank = {AttentionSeverity.WARNING: 0, AttentionSeverity.ACTION: 1, AttentionSeverity.INFO: 2}
        attention.sort(key=lambda item: (severity_rank[item.severity], item.id))
        return LearningCenterSummary(
            self.entry_id, self.revision, viewer.is_admin, len(models),
            sum(1 for item in statuses if item in {
                ModelStatus.VALID, ModelStatus.CONFIRMED, ModelStatus.INFERRED,
            }),
            sum(1 for item in statuses if item is ModelStatus.LEARNING),
            sum(1 for item in statuses if item is ModelStatus.STALE),
            sum(1 for item in statuses if item in {
                ModelStatus.INVALID, ModelStatus.UNRELIABLE, ModelStatus.DRIFT,
            }),
            len(attention), category_counts,
            kinds.count(LearnedKind.HABIT), kinds.count(LearnedKind.PREFERENCE),
            kinds.count(LearnedKind.THERMAL_MODEL), kinds.count(LearnedKind.EFFECT_TIMING),
            kinds.count(LearnedKind.RELIABILITY),
            sum(1 for item in permissions if not item.revoked and self.now < item.expires_at),
            len(self.mutes(viewer)), recent, self.features(viewer),
            tuple(attention[:50]),
            tuple(item.value for item in NON_MUTABLE_SITUATIONS),
        )


__all__ = (
    "API_VERSION",
    "AttentionKind",
    "AttentionSeverity",
    "DEFAULT_EVIDENCE",
    "DEFAULT_HISTORY_PAGE",
    "DEFAULT_MODEL_PAGE",
    "FactUnit",
    "HistoryView",
    "LabelSource",
    "LearningCenterAttentionItem",
    "LearningCenterError",
    "LearningCenterRevision",
    "LearningCenterService",
    "LearningCenterSources",
    "LearningCenterSummary",
    "LearningEvidenceSummary",
    "LearningModelDetail",
    "LearningModelListItem",
    "MAX_EVIDENCE",
    "MAX_HISTORY_PAGE",
    "MAX_MODEL_PAGE",
    "ModelAction",
    "ModelCategory",
    "ModelStatus",
    "MuteView",
    "ProactiveSource",
    "ProactiveStatus",
    "StandingPermissionView",
    "StaticLabels",
    "TombstoneView",
    "Viewer",
    "Visibility",
    "action_key",
    "can_forget_model",
    "can_view_model",
    "is_redacted",
    "model_category",
    "model_ref",
    "model_status",
    "model_visibility",
    "reliability_counts",
)
