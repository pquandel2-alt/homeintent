"""Hass-free lifecycle model for one concrete proactive situation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence, cast

from .service_call import ServiceCallPlan


class AgentMode(StrEnum):
    INFORM = "inform"
    ASK = "ask"
    AUTO = "auto"


class AgentEventState(StrEnum):
    ACTIVE = "active"
    NOTIFIED = "notified"
    ACKNOWLEDGED = "acknowledged"
    SNOOZED = "snoozed"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    DENIED = "denied"
    FAILED = "failed"


@dataclass(frozen=True)
class StoredServicePlan:
    domain: str
    service: str
    entity_ids: tuple[str, ...]
    data: Mapping[str, object] = field(default_factory=lambda: _empty_data())

    @classmethod
    def from_plan(cls, plan: ServiceCallPlan) -> "StoredServicePlan":
        raw_ids = plan.entity_id
        entity_ids = (raw_ids,) if isinstance(raw_ids, str) else tuple(raw_ids)
        plan_data = cast(
            Mapping[str, object], plan.data  # pyright: ignore[reportUnknownMemberType]
        )
        return cls(plan.domain, plan.service, entity_ids, dict(plan_data))

    def to_plan(self) -> ServiceCallPlan:
        target: str | list[str] = (
            self.entity_ids[0] if len(self.entity_ids) == 1 else list(self.entity_ids)
        )
        return ServiceCallPlan(self.domain, self.service, target, dict(self.data))


@dataclass(frozen=True)
class AgentEvent:
    event_id: str
    rule_id: str
    dedupe_key: str
    title: str
    message: str
    mode: AgentMode
    state: AgentEventState
    created_at: str
    updated_at: str
    expires_at: str
    source_entity_id: str | None = None
    expected_source_state: str | None = None
    source_comparator: str | None = None
    source_threshold: float | None = None
    proposed_action: StoredServicePlan | None = None
    snoozed_until: str | None = None
    reminder_automation_id: str | None = None
    delivery_attempts: int = 0
    delivered_channels: tuple[str, ...] = ()
    notification_device_ids: tuple[str, ...] = ()
    last_error: str | None = None
    safety_critical: bool = False
    origin: str = "internal_runtime"
    created_by_user_id: str | None = None
    creator_was_admin: bool = False
    provenance: str = "entry_owned_runtime"
    canonical_action: str | None = None
    canonical_target_ids: tuple[str, ...] = ()
    canonical_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["mode"] = self.mode.value
        result["state"] = self.state.value
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AgentEvent":
        action_raw = raw.get("proposed_action")
        action = None
        if isinstance(action_raw, Mapping):
            action_values = cast(Mapping[str, object], action_raw)
            entity_ids = action_values.get("entity_ids", ())
            if isinstance(entity_ids, (list, tuple)):
                raw_data = action_values.get("data", {})
                action = StoredServicePlan(
                    domain=str(action_values.get("domain", "")),
                    service=str(action_values.get("service", "")),
                    entity_ids=tuple(str(item) for item in cast(Sequence[object], entity_ids)),
                    data=(
                        dict(cast(Mapping[str, object], raw_data))
                        if isinstance(raw_data, Mapping)
                        else {}
                    ),
                )
        delivered = raw.get("delivered_channels", ())
        notification_devices = raw.get("notification_device_ids", ())
        canonical_targets = raw.get("canonical_target_ids", ())
        return cls(
            event_id=str(raw["event_id"]),
            rule_id=str(raw["rule_id"]),
            dedupe_key=str(raw["dedupe_key"]),
            title=str(raw.get("title", "HomeIntent")),
            message=str(raw.get("message", "")),
            mode=AgentMode(str(raw.get("mode", AgentMode.INFORM.value))),
            state=AgentEventState(str(raw.get("state", AgentEventState.ACTIVE.value))),
            created_at=str(raw["created_at"]),
            updated_at=str(raw["updated_at"]),
            expires_at=str(raw["expires_at"]),
            source_entity_id=_optional_str(raw.get("source_entity_id")),
            expected_source_state=_optional_str(raw.get("expected_source_state")),
            source_comparator=_optional_str(raw.get("source_comparator")),
            source_threshold=(
                float(raw["source_threshold"])
                if isinstance(raw.get("source_threshold"), (int, float))
                else None
            ),
            proposed_action=action,
            snoozed_until=_optional_str(raw.get("snoozed_until")),
            reminder_automation_id=_optional_str(raw.get("reminder_automation_id")),
            delivery_attempts=int(raw.get("delivery_attempts", 0)),
            delivered_channels=(
                tuple(str(item) for item in cast(Sequence[object], delivered))
                if isinstance(delivered, (list, tuple))
                else ()
            ),
            notification_device_ids=(
                tuple(str(item) for item in cast(Sequence[object], notification_devices))
                if isinstance(notification_devices, (list, tuple))
                else ()
            ),
            last_error=_optional_str(raw.get("last_error")),
            safety_critical=bool(raw.get("safety_critical", False)),
            origin=str(raw.get("origin", "internal_runtime")),
            created_by_user_id=_optional_str(raw.get("created_by_user_id")),
            creator_was_admin=bool(raw.get("creator_was_admin", False)),
            provenance=str(raw.get("provenance", "legacy_event")),
            canonical_action=_optional_str(raw.get("canonical_action")),
            canonical_target_ids=(
                tuple(str(item) for item in cast(Sequence[object], canonical_targets))
                if isinstance(canonical_targets, (list, tuple))
                else ()
            ),
            canonical_message=_optional_str(raw.get("canonical_message")),
        )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _empty_data() -> dict[str, object]:
    return {}
