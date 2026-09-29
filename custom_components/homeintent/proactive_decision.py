"""Pure decision layer for INFORM/ASK/low-risk AUTO."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Mapping, Sequence, cast

from .agent_action_policy import validate_agent_service_plan
from .agent_event import AgentMode
from .entities import EntitySnapshot
from .execution_policy import PolicyOutcome, evaluate_service_plan
from .plan_origin import PlanOrigin
from .risk import RiskLevel
from .service_call import ServiceCallPlan
from .situation import EventQuality, NormalizedEvent, Situation, SituationSeverity


@dataclass(frozen=True)
class ProactiveDecision:
    deliver: bool
    mode: AgentMode
    channels: tuple[str, ...]
    reason: str
    action: ServiceCallPlan | None = None


class ProactiveDecisionEngine:
    """Order-sensitive safety decision; never performs delivery or actions."""

    def decide(
        self,
        event: NormalizedEvent,
        situation: Situation,
        *,
        entities: list[EntitySnapshot],
        options: Mapping[str, object],
        proposed_action: ServiceCallPlan | None = None,
        requested_mode: AgentMode = AgentMode.INFORM,
        now: datetime,
        deduplicated: bool = False,
        in_cooldown: bool = False,
        reachable: bool = True,
    ) -> ProactiveDecision:
        if event.quality is not EventQuality.GOOD:
            return ProactiveDecision(False, AgentMode.INFORM, (), "Beobachtung ist nicht verlässlich.")
        configured_max_age = options.get("agent_max_event_age_seconds", 300)
        max_age = configured_max_age if isinstance(configured_max_age, int) else 300
        if max_age < 1 or abs((now - event.occurred_at).total_seconds()) > max_age:
            return ProactiveDecision(False, AgentMode.INFORM, (), "Beobachtung ist veraltet.")
        if deduplicated or in_cooldown:
            return ProactiveDecision(False, AgentMode.INFORM, (), "Situation ist dedupliziert oder im Cooldown.")
        if not situation.relevant:
            return ProactiveDecision(False, AgentMode.INFORM, (), "Situation ist im aktuellen Modus nicht relevant.")
        if not reachable and situation.severity is not SituationSeverity.CRITICAL:
            return ProactiveDecision(False, AgentMode.INFORM, (), "Niemand ist erreichbar.")
        channels = _channels(options, now, critical=situation.severity is SituationSeverity.CRITICAL)
        if not channels:
            return ProactiveDecision(False, AgentMode.INFORM, (), "Kein erlaubter Ausgabekanal ist verfügbar.")
        if proposed_action is None or requested_mode is AgentMode.INFORM:
            return ProactiveDecision(True, AgentMode.INFORM, channels, "Eine Information reicht aus.")
        if error := validate_agent_service_plan(proposed_action):
            return ProactiveDecision(True, AgentMode.INFORM, channels, error)
        # Pure decision: without an effect graph a script, scene or group
        # fails closed here; the executor re-checks with the real graph.
        policy = evaluate_service_plan(
            proposed_action, entities, options, is_admin=False, user_id=None,
            origin=PlanOrigin.PROACTIVE_PROPOSAL,
            attended=requested_mode is not AgentMode.AUTO,
        )
        if policy.outcome is PolicyOutcome.DENY:
            return ProactiveDecision(True, AgentMode.INFORM, channels, policy.reason or "Aktion nicht erlaubt.")
        if requested_mode is AgentMode.AUTO:
            auto_enabled = bool(options.get("agent_auto_enabled", False))
            auto_allowlist = options.get("agent_auto_entity_ids", ())
            allowlist: set[str] = (
                {
                    item
                    for item in cast(Sequence[object] | set[object], auto_allowlist)
                    if isinstance(item, str)
                }
                if isinstance(auto_allowlist, (list, tuple, set))
                else set()
            )
            targets = (
                (proposed_action.entity_id,)
                if isinstance(proposed_action.entity_id, str)
                else tuple(proposed_action.entity_id)
            )
            reversible = proposed_action.service in {
                "turn_on", "turn_off", "open_cover", "close_cover", "pause", "media_pause"
            }
            if (
                auto_enabled
                and policy.outcome is PolicyOutcome.ALLOW
                and policy.risk is RiskLevel.LOW
                and reversible
                and set(targets) <= allowlist
            ):
                return ProactiveDecision(True, AgentMode.AUTO, channels, "Explizit freigegebenes reversibles LOW-AUTO.", proposed_action)
        return ProactiveDecision(True, AgentMode.ASK, channels, "Aktion wird nur als geprüfte Rückfrage angeboten.", proposed_action)


def _channels(
    options: Mapping[str, object], now: datetime, *, critical: bool
) -> tuple[str, ...]:
    configured = options.get("agent_delivery_channels", ("push",))
    channels: tuple[str, ...] = (
        tuple(
            item
            for item in cast(Sequence[object] | set[object], configured)
            if isinstance(item, str) and item in {"push", "tts", "persistent"}
        )
        if isinstance(configured, (list, tuple, set))
        else ("push",)
    )
    if critical:
        return channels
    quiet_start = _parse_time(options.get("agent_quiet_start"))
    quiet_end = _parse_time(options.get("agent_quiet_end"))
    if quiet_start is not None and quiet_end is not None:
        current = now.timetz().replace(tzinfo=None)
        quiet = (
            quiet_start <= current < quiet_end
            if quiet_start < quiet_end
            else current >= quiet_start or current < quiet_end
        )
        if quiet:
            return tuple(item for item in channels if item != "tts")
    return channels


def _parse_time(value: object) -> time | None:
    if not isinstance(value, str):
        return None
    try:
        hour, minute = value.split(":", 1)
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return None
