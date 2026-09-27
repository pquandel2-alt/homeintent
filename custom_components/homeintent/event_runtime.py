"""Home Assistant state-change bridge into normalized local situations."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AGENT_EVENT_CATEGORIES,
    CONF_ANOMALY_THRESHOLD_PERCENT,
    CONF_BANTER_LEVEL,
    CONF_PERSONA_STYLE,
    CONF_ROUTINE_DETECTION_ENABLED,
    CONF_ROUTINE_MIN_OBSERVATIONS,
)
from .agent_config_validation import parse_event_categories
from .hass_entities import build_entity_snapshots
from .entities import spoken_state
from .agent_event import AgentMode
from .effect_monitor import ExpectedEffect
from .proactive_decision import ProactiveDecisionEngine
from .runtime_data import HomeIntentRuntimeData
from .service_call import ServiceCallPlan
from .statistical_models import evaluate_latency_anomaly
from .response_planner import (
    DialogAct,
    GermanResponseRealizer,
    PersonaStyle,
    ResponsePlan,
    Urgency,
)
from .situation import (
    RoutineStatistics,
    Situation,
    SituationSeverity,
    normalize_state_change,
)


_LOGGER = logging.getLogger(__name__)


class SituationRuntime:
    """Normalizes selected HA events and emits bounded AgentEvents."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, runtime_data: HomeIntentRuntimeData
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._runtime_data = runtime_data
        self._seen: set[str] = set()
        self._decision_engine = ProactiveDecisionEngine()

    def async_start(self) -> Any:
        self._runtime_data.effect_monitor.set_expired_handler(
            self.async_handle_expected_effect_expired
        )
        bus = getattr(self._hass, "bus", None)
        listen = getattr(bus, "async_listen", None)
        if listen is None:
            unlisten = lambda: None
        else:
            unlisten = listen("state_changed", self.async_handle_state_changed)

        def stop() -> None:
            unlisten()
            self._runtime_data.effect_monitor.set_expired_handler(None)

        return stop

    async def async_handle_state_changed(self, raw_event: Any) -> None:
        data = getattr(raw_event, "data", {})
        if not isinstance(data, dict):
            return
        entity_id = data.get("entity_id")
        new_state = data.get("new_state")
        old_state = data.get("old_state")
        if not isinstance(entity_id, str) or new_state is None:
            return
        entities = build_entity_snapshots(self._hass, self._entry)
        thermal_tracker = self._runtime_data.thermal_tracker
        if thermal_tracker is not None:
            try:
                await thermal_tracker.async_observe_states(
                    tuple(entities),
                    occurred_at=getattr(raw_event, "time_fired", None) or dt_util.utcnow(),
                )
            except Exception:  # noqa: BLE001 - learning must not break HA event flow
                _LOGGER.exception("HomeIntent thermal experience update failed")
        entity = next((item for item in entities if item.entity_id == entity_id), None)
        if entity is None:
            return
        self._runtime_data.effect_monitor.observe(entity_id, entity.state)
        previous = getattr(old_state, "state", None)
        monitor_runtime = self._runtime_data.monitor_runtime
        if (
            monitor_runtime is not None
            and entity.domain == "person"
            and isinstance(previous, str)
            and previous != entity.state
        ):
            try:
                event_context = getattr(raw_event, "context", None)
                context_id = getattr(event_context, "id", None)
                await monitor_runtime.async_process_person_transition(
                    entity.entity_id,
                    previous,
                    entity.state,
                    occurred_at=getattr(raw_event, "time_fired", None) or dt_util.utcnow(),
                    occurrence_id=context_id if isinstance(context_id, str) else None,
                )
            except Exception:  # noqa: BLE001 - monitor failure must not break HA event flow
                _LOGGER.exception("HomeIntent monitor goal evaluation failed")
        proactive = self._runtime_data.proactive_context
        if proactive is not None:
            try:
                await proactive.async_observe_state(
                    entity,
                    previous if isinstance(previous, str) else None,
                    tuple(entities),
                )
            except Exception:  # noqa: BLE001 - V12 must never break HA event flow
                _LOGGER.exception("HomeIntent proactive context evaluation failed")
        categories = self._configured_categories()
        if not categories:
            return
        people = tuple(
            item.entity_id for item in entities if item.domain == "person" and item.state == "home"
        )
        event = normalize_state_change(
            entity,
            previous if isinstance(previous, str) else None,
            occurred_at=getattr(raw_event, "time_fired", None) or dt_util.utcnow(),
            person_ids=people,
            occupied=bool(people),
            operating_mode="home" if people else "away",
        )
        if event.event_id in self._seen:
            return
        self._seen.add(event.event_id)
        if len(self._seen) > 4096:
            self._seen = {event.event_id}
        situations = self._runtime_data.situation_evaluator.evaluate(event, entities)
        routine_assessment = None
        if bool(self._entry.options.get(CONF_ROUTINE_DETECTION_ENABLED, False)):
            stats = self._runtime_data.routine_statistics.setdefault(
                entity_id,
                RoutineStatistics(
                    minimum_observations=int(
                        self._entry.options.get(CONF_ROUTINE_MIN_OBSERVATIONS, 10)
                    ),
                    anomaly_threshold=float(
                        self._entry.options.get(CONF_ANOMALY_THRESHOLD_PERCENT, 5)
                    ) / 100.0,
                ),
            )
            assessment = stats.assess(event)
            routine_assessment = assessment
            stats.observe(event)
            if assessment.unusual and "routine_anomaly" in categories:
                routine_situation = Situation(
                    f"routine:{event.event_id}",
                    "routine_anomaly",
                    SituationSeverity.NOTICE,
                    "Ein lokales Zeitmuster weicht ab.",
                    (),
                    event.event_id,
                )
                decision = self._decision_engine.decide(
                    event,
                    routine_situation,
                    entities=entities,
                    options=self._entry.options,
                    now=event.occurred_at,
                )
                if decision.deliver:
                    await self._signal(
                        event.entity_id,
                        "routine_anomaly",
                        "Ein lokales Zeitmuster weicht ab.",
                        (
                            assessment.observed,
                            assessment.normal,
                            f"{assessment.observation_count} Beobachtungen",
                            assessment.uncertainty,
                        ),
                        event.state,
                        critical=False,
                        mode=decision.mode,
                        action=decision.action,
                    )
        for situation in situations:
            if situation.category not in categories and not (
                "safety" in categories and situation.severity.value == "critical"
            ):
                continue
            facts = situation.facts
            if routine_assessment is not None:
                facts = (
                    *facts,
                    "Historie: "
                    f"{routine_assessment.normal}; "
                    f"{routine_assessment.observation_count} Beobachtungen; "
                    f"{routine_assessment.uncertainty}",
                )
            action = (
                ServiceCallPlan(
                    "homeassistant",
                    situation.suggested_operation,
                    situation.suggested_entity_id,
                    {},
                )
                if situation.suggested_entity_id is not None
                and situation.suggested_operation == "turn_off"
                else None
            )
            decision = self._decision_engine.decide(
                event,
                situation,
                entities=entities,
                options=self._entry.options,
                proposed_action=action,
                requested_mode=AgentMode.ASK if action is not None else AgentMode.INFORM,
                now=event.occurred_at,
            )
            if not decision.deliver:
                continue
            await self._signal(
                event.entity_id,
                situation.category,
                situation.summary,
                facts,
                event.state,
                critical=situation.severity.value == "critical",
                mode=decision.mode,
                action=decision.action,
            )

    async def async_handle_expected_effect_expired(
        self, effect: ExpectedEffect
    ) -> None:
        """Report a missing observable effect without retrying the action."""
        proactive = self._runtime_data.proactive_context
        if proactive is not None:
            try:
                name = next(
                    (item.friendly_name for item in build_entity_snapshots(self._hass, self._entry)
                     if item.entity_id == effect.entity_id),
                    effect.entity_id,
                )
                await proactive.async_report_effect_anomaly(
                    effect.entity_id, effect.operator_id, name
                )
            except Exception:  # noqa: BLE001 - V12 must never break HA event flow
                _LOGGER.exception("HomeIntent proactive effect report failed")
        if "expected_effect_missing" not in self._configured_categories():
            return
        entities = build_entity_snapshots(self._hass, self._entry)
        entity = next(
            (item for item in entities if item.entity_id == effect.entity_id), None
        )
        if entity is None or entity.state in {"unknown", "unavailable"}:
            return
        facts = [
            f"Erwarteter Zustand: {effect.expected_state}",
            f"Beobachteter Zustand: {spoken_state(entity.state)}",
            "Die Aktion wird nicht automatisch wiederholt.",
        ]
        summary = "Die erwartete Gerätewirkung wurde nicht beobachtet."
        predictive = self._runtime_data.predictive_house
        if predictive is not None and effect.operator_id is not None:
            timing = predictive.effect_timing_model(
                effect.operator_id, effect.entity_id
            )
            if timing is not None:
                elapsed = max(
                    0.0, (dt_util.utcnow() - effect.registered_at).total_seconds()
                )
                anomaly = evaluate_latency_anomaly(timing, elapsed)
                if anomaly.anomalous:
                    summary = "Das Gerät reagiert ungewöhnlich langsam."
                    facts.insert(
                        0,
                        f"Normaler Median: {timing.median_seconds:.1f} s; "
                        f"robuste Anomaliegrenze: {anomaly.threshold_seconds:.1f} s",
                    )
        await self._signal(
            effect.entity_id,
            "expected_effect_missing",
            summary,
            tuple(facts),
            entity.state,
            critical=False,
            mode=AgentMode.INFORM,
        )

    async def _signal(
        self,
        entity_id: str,
        category: str,
        summary: str,
        facts: tuple[str, ...],
        expected_state: str,
        *,
        critical: bool,
        mode: AgentMode = AgentMode.INFORM,
        action: ServiceCallPlan | None = None,
    ) -> None:
        agent = self._runtime_data.proactive_agent
        if agent is None:
            return
        factual_result = summary
        if facts:
            factual_result += " " + "; ".join(facts[:4]) + "."
        raw_style = self._entry.options.get(CONF_PERSONA_STYLE, "neutral")
        try:
            style = PersonaStyle(str(raw_style))
        except ValueError:
            style = PersonaStyle.NEUTRAL
        raw_banter = self._entry.options.get(CONF_BANTER_LEVEL, 0)
        banter = raw_banter if isinstance(raw_banter, int) else 0
        message = GermanResponseRealizer().realize(
            ResponsePlan(
                DialogAct.WARN if critical else DialogAct.INFORM,
                factual_result,
                urgency=Urgency.CRITICAL if critical else Urgency.NORMAL,
                safety_level="critical" if critical else "low",
                banter_level=max(0, min(3, banter)),
                category=category,
            ),
            style=style,
        )
        try:
            payload: dict[str, object] = {
                "rule_id": f"situation:{category}",
                "message": message,
                "mode": mode.value,
                "source_entity_id": entity_id,
                "expected_source_state": expected_state,
                "dedupe_key": entity_id,
                "safety_critical": critical,
            }
            if action is not None:
                payload.update(
                    {
                        "action_domain": action.domain,
                        "action_service": action.service,
                        "action_entity_id": action.entity_id,
                        "action_data": action.data,
                    }
                )
            await agent.async_signal(payload)
        except (TypeError, ValueError):
            _LOGGER.warning("Invalid normalized situation %s", category, exc_info=True)

    def _configured_categories(self) -> frozenset[str]:
        raw = self._entry.options.get(CONF_AGENT_EVENT_CATEGORIES, "")
        try:
            return parse_event_categories(raw)
        except ValueError:
            _LOGGER.warning("Invalid configured HomeIntent event categories")
            return frozenset()
