"""Central safety classification for Home Assistant service plans."""

from __future__ import annotations

from enum import IntEnum

from .entities import EntitySnapshot
from .service_call import ServiceCallPlan


class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


_HIGH_RISK_DOMAINS = frozenset({"lock", "valve", "button", "alarm_control_panel"})
_MEDIUM_RISK_DOMAINS = frozenset({"cover", "vacuum", "lawn_mower", "notify"})


def classify_service_plan(
    plan: ServiceCallPlan, entities: list[EntitySnapshot] | tuple[EntitySnapshot, ...]
) -> RiskLevel:
    """Classify from concrete domain, service, targets and device classes."""
    target_ids = {plan.entity_id} if isinstance(plan.entity_id, str) else set(plan.entity_id)
    targets = [entity for entity in entities if entity.entity_id in target_ids]
    domains = {entity.domain for entity in targets} or {plan.domain}
    device_classes = {(entity.device_class or "").casefold() for entity in targets}

    if "alarm_control_panel" in domains:
        level = RiskLevel.CRITICAL
    elif "lock" in domains and plan.service == "unlock":
        level = RiskLevel.CRITICAL
    elif device_classes & {"garage", "garage_door", "gate"} and (
        plan.service.startswith(("open", "close")) or plan.service == "set_cover_position"
    ):
        # Moving an entrance to a position is as critical as opening it.
        level = RiskLevel.CRITICAL
    elif domains & _HIGH_RISK_DOMAINS:
        level = RiskLevel.HIGH
    elif domains & _MEDIUM_RISK_DOMAINS:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW

    # A broad physical action deserves one extra safety tier. This never
    # lowers an already critical action and never affects read-only queries.
    if len(target_ids) > 5 and level < RiskLevel.CRITICAL:
        level = RiskLevel(level + 1)
    return level


def requires_confirmation(
    plan: ServiceCallPlan, entities: list[EntitySnapshot] | tuple[EntitySnapshot, ...]
) -> bool:
    return classify_service_plan(plan, entities) >= RiskLevel.HIGH
