"""One policy-gated HA write path shared by conversation and the agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from homeassistant.core import HomeAssistant

from .agent_action_policy import RESERVED_TARGET_DATA_KEYS
from .audit_log import AuditTrail
from .entities import STATELESS_ACTION_DOMAINS, EntitySnapshot
from .effect_monitor import EffectMonitor
from .execution_policy import PolicyDecision, PolicyOutcome, evaluate_service_plan
from .service_call import ServiceCallPlan


@dataclass(frozen=True)
class ExecutionResult:
    executed: bool
    decision: PolicyDecision
    error: str | None = None


async def async_execute_service_plan(
    hass: HomeAssistant,
    plan: ServiceCallPlan,
    entities: list[EntitySnapshot] | tuple[EntitySnapshot, ...],
    options: Mapping[str, object],
    *,
    is_admin: bool,
    user_id: str | None,
    confirmed: bool,
    audit_trail: AuditTrail | None = None,
    audit_actor_id: str | None = None,
    effect_monitor: EffectMonitor | None = None,
) -> ExecutionResult:
    """Re-evaluate policy immediately before the only physical write."""
    decision = evaluate_service_plan(
        plan, entities, options, is_admin=is_admin, user_id=user_id
    )
    if decision.outcome is PolicyOutcome.DENY:
        return ExecutionResult(False, decision, decision.reason)
    if decision.outcome is PolicyOutcome.CONFIRM and not confirmed:
        return ExecutionResult(False, decision, "Die Aktion benötigt eine Bestätigung.")
    if RESERVED_TARGET_DATA_KEYS & frozenset(plan.data):
        return ExecutionResult(
            False, decision, "Aktionsdaten dürfen das validierte Ziel nicht ersetzen."
        )
    target_ids = (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
    available_ids = {entity.entity_id for entity in entities}
    if not target_ids or any(entity_id not in available_ids for entity_id in target_ids):
        return ExecutionResult(
            False, decision, "Mindestens ein Ziel ist nicht mehr verfügbar oder freigegeben."
        )
    fresh_targets = [entity for entity in entities if entity.entity_id in target_ids]
    if any(_state_is_unreliable(entity) for entity in fresh_targets):
        return ExecutionResult(
            False, decision, "Mindestens ein Ziel meldet keinen verlässlichen aktuellen Zustand."
        )
    required = _required_capabilities(plan)
    if required and any(
        entity.capabilities and not required <= entity.capabilities
        for entity in fresh_targets
    ):
        return ExecutionResult(
            False, decision, "Mindestens ein Ziel unterstützt die Aktion nicht mehr."
        )
    try:
        await hass.services.async_call(
            plan.domain,
            plan.service,
            {**plan.data, "entity_id": plan.entity_id},
            blocking=True,
        )
    except Exception as err:  # noqa: BLE001 - HA service failures are heterogeneous
        return ExecutionResult(False, decision, str(err))
    if audit_trail is not None:
        from homeassistant.util import dt as dt_util

        audit_trail.record(dt_util.now(), audit_actor_id, plan)
    if effect_monitor is not None:
        effect_monitor.register(plan)
    return ExecutionResult(True, decision)


def _state_is_unreliable(entity: EntitySnapshot) -> bool:
    if entity.state == "unavailable":
        return True
    return entity.state == "unknown" and entity.domain not in STATELESS_ACTION_DOMAINS


def _required_capabilities(plan: ServiceCallPlan) -> frozenset[str]:
    if plan.service == "turn_on":
        return frozenset(
            {"TURN_ON", "BRIGHTNESS"}
            if "brightness_pct" in plan.data
            else {"TURN_ON"}
        )
    if plan.service == "turn_off":
        return frozenset({"TURN_OFF"})
    # Only valves report OPEN/CLOSE (nlu/capabilities.py); for covers open and
    # close are the baseline contract and derive_capabilities() models POSITION
    # alone, so requiring OPEN/CLOSE there rejected every positionable cover.
    if plan.service == "open_valve":
        return frozenset({"OPEN"})
    if plan.service == "close_valve":
        return frozenset({"CLOSE"})
    if plan.service == "set_cover_position":
        return frozenset({"POSITION"})
    return frozenset()
