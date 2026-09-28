"""Long-lived deterministic coordinator for proactive HomeIntent situations."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any, Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .agent_delivery import AgentDelivery
from .agent_action_policy import validate_agent_service_plan
from .agent_event import AgentEvent, AgentEventState, AgentMode, StoredServicePlan
from .agent_event_store import AgentEventStore
from .automation_executor import AutomationExecutor
from .const import (
    CONF_AGENT_AUTO_ENABLED,
    CONF_AGENT_AUTO_ENTITY_IDS,
    CONF_AGENT_COOLDOWN_SECONDS,
    CONF_AGENT_ENABLED,
    CONF_AGENT_NOTIFY_TARGETS,
    DEFAULT_AGENT_COOLDOWN_SECONDS,
)
from .hass_entities import build_entity_snapshots
from .execution_policy import PolicyOutcome, evaluate_service_plan
from .nlu.automation_confirmation import ConfirmationReply, classify_confirmation_reply
from .risk import RiskLevel
from .runtime_data import HomeIntentRuntimeData
from .service_call import ServiceCallPlan
from .effect_graph import build_plan_effects
from .plan_origin import PlanOrigin
from .service_executor import async_execute_service_plan

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_ACTION_EVENT = "mobile_app_notification_action"
ACTION_PREFIXES = {
    "HOMEINTENT_EXECUTE_": "execute",
    "HOMEINTENT_IGNORE_": "ignore",
    "HOMEINTENT_SNOOZE_": "snooze",
    # Input-only compatibility for buttons in notifications created before 5.0.2.
    "HA_NLU_EXECUTE_": "execute",
    "HA_NLU_IGNORE_": "ignore",
    "HA_NLU_SNOOZE_": "snooze",
}

class ProactiveAgentRuntime:
    """Connects native HA automation signals to policy, events and delivery."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        runtime_data: HomeIntentRuntimeData,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._runtime_data = runtime_data
        self._store = AgentEventStore(hass)
        self._delivery = AgentDelivery(hass)
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._entry.options.get(CONF_AGENT_ENABLED, True))

    def async_start(self) -> Any:
        """Register the sole incoming event required by proactive delivery."""
        bus = getattr(self._hass, "bus", None)
        async_listen = getattr(bus, "async_listen", None)
        if async_listen is None:
            return lambda: None
        return async_listen(NOTIFICATION_ACTION_EVENT, self.async_handle_notification_action)

    async def async_recover(self) -> None:
        """Expire stale events and safely re-evaluate overdue snoozes."""
        now = dt_util.utcnow()
        for event in (await self._store.async_load_all()).values():
            if event.proposed_action is not None:
                plan_error = validate_agent_service_plan(event.proposed_action.to_plan())
                if plan_error is not None:
                    await self._store.async_save(
                        replace(
                            event,
                            state=AgentEventState.EXPIRED,
                            updated_at=now.isoformat(),
                            last_error=plan_error,
                        )
                    )
                    continue
            if _parse_datetime(event.expires_at) <= now:
                if event.state not in {AgentEventState.RESOLVED, AgentEventState.EXPIRED}:
                    await self._store.async_save(
                        replace(
                            event,
                            state=AgentEventState.EXPIRED,
                            updated_at=now.isoformat(),
                        )
                    )
                continue
            if (
                event.state is AgentEventState.SNOOZED
                and event.snoozed_until is not None
                and _parse_datetime(event.snoozed_until) <= now
            ):
                await self.async_recheck(event.event_id)

    async def async_signal(self, data: Mapping[str, Any]) -> AgentEvent | None:
        """Create/deliver one deduplicated situation from a native automation."""
        if not self.enabled:
            return None
        rule_id = _required_text(data.get("rule_id"), "rule_id", limit=200)
        message = _required_text(data.get("message"), "message", limit=4000)
        title = _optional_text(data.get("title"), limit=200) or "HomeIntent"
        try:
            mode = AgentMode(str(data.get("mode", AgentMode.INFORM.value)).casefold())
        except ValueError as err:
            raise ValueError("mode muss inform, ask oder auto sein") from err
        source_entity_id = _optional_text(data.get("source_entity_id"), limit=255)
        expected_state = _optional_text(data.get("expected_source_state"), limit=255)
        source_comparator = _optional_text(data.get("source_comparator"), limit=20)
        if source_comparator not in {None, "above", "below", "equal"}:
            raise ValueError("source_comparator muss above, below oder equal sein")
        raw_threshold = data.get("source_threshold")
        source_threshold = (
            float(raw_threshold)
            if isinstance(raw_threshold, (int, float)) and not isinstance(raw_threshold, bool)
            else None
        )
        if source_comparator is not None and source_threshold is None:
            raise ValueError("source_threshold fehlt")
        dedupe_suffix = _optional_text(data.get("dedupe_key"), limit=500)
        dedupe_key = f"{rule_id}:{dedupe_suffix or source_entity_id or 'global'}"
        action = self._parse_action(data)
        if mode in {AgentMode.ASK, AgentMode.AUTO} and action is None:
            raise ValueError("ask und auto benötigen eine vollständig validierbare Aktion")
        now = dt_util.utcnow()
        expires_seconds = _bounded_int(data.get("expires_seconds"), 86400, 60, 604800)
        cooldown = _bounded_int(
            data.get("cooldown_seconds"),
            self._configured_cooldown,
            0,
            86400,
        )
        async with self._lock:
            existing = await self._find_duplicate(dedupe_key, now, cooldown)
            if existing is not None:
                return existing
            event = AgentEvent(
                event_id=uuid.uuid4().hex,
                rule_id=rule_id,
                dedupe_key=dedupe_key,
                title=title,
                message=message,
                mode=mode,
                state=AgentEventState.ACTIVE,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
                expires_at=(now + timedelta(seconds=expires_seconds)).isoformat(),
                source_entity_id=source_entity_id,
                expected_source_state=expected_state,
                source_comparator=source_comparator,
                source_threshold=source_threshold,
                proposed_action=(StoredServicePlan.from_plan(action) if action else None),
                notification_device_ids=self._configured_notification_device_ids(),
                safety_critical=bool(data.get("safety_critical", False)),
            )
            await self._store.async_save(event)

        if not self._source_still_matches(event):
            event = replace(
                event, state=AgentEventState.RESOLVED, updated_at=dt_util.utcnow().isoformat()
            )
            await self._store.async_save(event)
            return event

        if mode is AgentMode.AUTO and action is not None:
            entities = build_entity_snapshots(self._hass, self._entry)
            target_ids = (
                (action.entity_id,)
                if isinstance(action.entity_id, str)
                else tuple(action.entity_id)
            )
            raw_allowlist = self._entry.options.get(CONF_AGENT_AUTO_ENTITY_IDS)
            auto_allowlist = (
                set(raw_allowlist)
                if isinstance(raw_allowlist, (list, tuple, set))
                else set()
            )
            # Config entries predating this option retain their explicitly
            # authored AUTO rules. New/updated entries receive an explicit
            # empty allowlist and therefore cannot AUTO until configured.
            if raw_allowlist is None:
                auto_allowlist = set(target_ids)
            auto_decision = evaluate_service_plan(
                action,
                entities,
                self._entry.options,
                is_admin=False,
                user_id=None,
                effects=build_plan_effects(self._hass, action),
                origin=PlanOrigin.PROACTIVE_PROPOSAL,
                attended=False,
            )
            if (
                not bool(self._entry.options.get(CONF_AGENT_AUTO_ENABLED, True))
                or not set(target_ids) <= auto_allowlist
                or action.service not in {
                    "turn_on", "turn_off", "open_cover", "close_cover"
                }
                or auto_decision.outcome is not PolicyOutcome.ALLOW
                or auto_decision.risk is not RiskLevel.LOW
            ):
                # AUTO can only execute a plan that is both policy-ALLOW and LOW.
                event = replace(event, mode=AgentMode.ASK)
            else:
                result = await async_execute_service_plan(
                    self._hass,
                    action,
                    entities,
                    self._entry.options,
                    is_admin=False,
                    user_id=None,
                    confirmed=False,
                    audit_trail=self._runtime_data.audit_trail,
                    audit_actor_id="proactive-agent",
                    effect_monitor=self._runtime_data.effect_monitor,
                    origin=PlanOrigin.PROACTIVE_PROPOSAL,
                    attended=False,
                )
                event = replace(
                    event,
                    state=(
                        AgentEventState.RESOLVED
                        if result.executed
                        else AgentEventState.ACTIVE
                    ),
                    mode=(AgentMode.AUTO if result.executed else AgentMode.ASK),
                    last_error=result.error,
                )
        return await self._deliver_and_store(event)

    async def async_handle_notification_action(self, event: Any) -> None:
        raw = getattr(event, "data", {}).get("action")
        if not self.enabled and not (isinstance(raw, str) and raw.startswith("HOMEINTENT_V12_")):
            return
        raw_action = getattr(event, "data", {}).get("action")
        if not isinstance(raw_action, str):
            return
        proactive = self._runtime_data.proactive_context
        if proactive is not None and raw_action.startswith("HOMEINTENT_V12_"):
            # V12 proposals carry only an opaque reference and a typed choice.
            user_id, _is_admin = await self._async_notification_actor(event)
            device_id = getattr(event, "data", {}).get("device_id")
            await proactive.async_handle_push_action(
                raw_action, user_id=user_id,
                device_id=device_id if isinstance(device_id, str) else None,
            )
            return
        operation = None
        event_id = ""
        for prefix, value in ACTION_PREFIXES.items():
            if raw_action.startswith(prefix):
                operation = value
                event_id = raw_action.removeprefix(prefix)
                break
        if operation is None or len(event_id) != 32 or any(
            char not in "0123456789abcdef" for char in event_id
        ):
            return
        user_id, is_admin = await self._async_notification_actor(event)
        async with self._lock:
            current = (await self._store.async_load_all()).get(event_id)
            if current is None or current.state not in {
                AgentEventState.ACTIVE,
                AgentEventState.NOTIFIED,
                AgentEventState.SNOOZED,
            }:
                return
            event_device_id = getattr(event, "data", {}).get("device_id")
            if (
                current.notification_device_ids
                and isinstance(event_device_id, str)
                and event_device_id not in current.notification_device_ids
            ):
                _LOGGER.warning(
                    "Ignored notification action for agent event %s from an unexpected device",
                    event_id,
                )
                return
            if _parse_datetime(current.expires_at) <= dt_util.utcnow():
                await self._store.async_save(
                    replace(
                        current,
                        state=AgentEventState.EXPIRED,
                        updated_at=dt_util.utcnow().isoformat(),
                    )
                )
                return
            if operation == "ignore":
                await self._store.async_save(
                    replace(
                        current,
                        state=AgentEventState.ACKNOWLEDGED,
                        updated_at=dt_util.utcnow().isoformat(),
                    )
                )
                return
            if operation == "snooze":
                await self._async_snooze_locked(current, 1800)
                return
            if current.mode is not AgentMode.ASK or current.proposed_action is None:
                return
            # Claim the event before physical I/O so parallel clicks are idempotent.
            claimed = replace(
                current,
                state=AgentEventState.ACKNOWLEDGED,
                updated_at=dt_util.utcnow().isoformat(),
            )
            await self._store.async_save(claimed)

        finished = await self._async_execute_claimed(
            claimed, user_id=user_id, is_admin=is_admin
        )
        if finished.state in {AgentEventState.DENIED, AgentEventState.FAILED}:
            await self._async_deliver_failure_feedback(finished)

    async def async_handle_voice_reply(
        self, text: str, *, user_id: str | None, is_admin: bool
    ) -> str | None:
        """Resolve yes/no only against one unique, TTS-delivered ASK event."""
        if not self.enabled:
            return None
        reply = classify_confirmation_reply(text)
        if reply is ConfirmationReply.UNCLEAR:
            return None
        now = dt_util.utcnow()
        async with self._lock:
            candidates = [
                event
                for event in (await self._store.async_load_all()).values()
                if event.mode is AgentMode.ASK
                and event.state is AgentEventState.NOTIFIED
                and "tts" in event.delivered_channels
                and _parse_datetime(event.expires_at) > now
            ]
            if not candidates:
                return None
            if len(candidates) > 1:
                return (
                    "Es sind mehrere Agentenfragen offen. Bitte antworte über die "
                    "zugehörige Push-Nachricht oder nenne die gewünschte Aktion vollständig."
                )
            current = candidates[0]
            claimed = replace(
                current,
                state=AgentEventState.ACKNOWLEDGED,
                updated_at=now.isoformat(),
            )
            await self._store.async_save(claimed)
        if reply is ConfirmationReply.NO:
            return "In Ordnung. Ich führe die vorgeschlagene Aktion nicht aus."
        finished = await self._async_execute_claimed(
            claimed, user_id=user_id, is_admin=is_admin
        )
        if finished.state is AgentEventState.RESOLVED:
            return "Erledigt."
        return (
            "Die Situation ist nicht mehr aktuell oder die Aktion ist nicht mehr erlaubt. "
            "Ich habe nichts ausgeführt."
        )

    async def async_open_voice_question_count(self) -> int:
        """Number of unexpired TTS-delivered ASK events (read-only)."""
        if not self.enabled:
            return 0
        now = dt_util.utcnow()
        return sum(
            1 for event in (await self._store.async_load_all()).values()
            if event.mode is AgentMode.ASK
            and event.state is AgentEventState.NOTIFIED
            and "tts" in event.delivered_channels
            and _parse_datetime(event.expires_at) > now
        )

    async def async_recheck(self, event_id: str) -> None:
        if not self.enabled:
            return
        async with self._lock:
            current = (await self._store.async_load_all()).get(event_id)
            if current is None or current.state is not AgentEventState.SNOOZED:
                return
            if not self._source_still_matches(current):
                await self._store.async_save(
                    replace(
                        current,
                        state=AgentEventState.RESOLVED,
                        updated_at=dt_util.utcnow().isoformat(),
                    )
                )
                return
            automation_entity_id = self._automation_entity_id(current.rule_id)
            if automation_entity_id is not None:
                # Expiring the old instance lets the original automation
                # create a fresh event if and only if all of its current HA
                # conditions still pass. Its trigger is rechecked above.
                expired = replace(
                    current,
                    state=AgentEventState.EXPIRED,
                    updated_at=dt_util.utcnow().isoformat(),
                    snoozed_until=None,
                    reminder_automation_id=None,
                )
                await self._store.async_save(expired)
            else:
                active = replace(
                    current,
                    state=AgentEventState.ACTIVE,
                    updated_at=dt_util.utcnow().isoformat(),
                    snoozed_until=None,
                    reminder_automation_id=None,
                )
        if automation_entity_id is not None:
            try:
                await self._hass.services.async_call(
                    "automation",
                    "trigger",
                    {"entity_id": automation_entity_id, "skip_condition": False},
                    blocking=True,
                )
            except Exception as err:  # noqa: BLE001
                await self._store.async_save(
                    replace(
                        current,
                        state=AgentEventState.SNOOZED,
                        updated_at=dt_util.utcnow().isoformat(),
                        last_error=str(err),
                    )
                )
            return
        await self._deliver_and_store(active)

    async def _async_execute_claimed(
        self, event: AgentEvent, *, user_id: str | None, is_admin: bool
    ) -> AgentEvent:
        action = event.proposed_action
        if action is None or not self._source_still_matches(event):
            updated = replace(
                event,
                state=AgentEventState.RESOLVED,
                updated_at=dt_util.utcnow().isoformat(),
                last_error=(None if action is not None else "Keine Aktion gespeichert"),
            )
            await self._store.async_save(updated)
            return updated
        plan = action.to_plan()
        if plan_error := validate_agent_service_plan(plan):
            updated = replace(
                event,
                state=AgentEventState.DENIED,
                updated_at=dt_util.utcnow().isoformat(),
                last_error=plan_error,
            )
            await self._store.async_save(updated)
            return updated
        entities = build_entity_snapshots(self._hass, self._entry)
        result = await async_execute_service_plan(
            self._hass,
            plan,
            entities,
            self._entry.options,
            is_admin=is_admin,
            user_id=user_id,
            confirmed=True,
            audit_trail=self._runtime_data.audit_trail,
            audit_actor_id=user_id or "proactive-agent",
            effect_monitor=self._runtime_data.effect_monitor,
            origin=PlanOrigin.PROACTIVE_PROPOSAL,
        )
        updated = replace(
            event,
            state=(
                AgentEventState.RESOLVED
                if result.executed
                else (
                    AgentEventState.DENIED
                    if result.decision.outcome is PolicyOutcome.DENY
                    else AgentEventState.FAILED
                )
            ),
            updated_at=dt_util.utcnow().isoformat(),
            last_error=result.error,
        )
        await self._store.async_save(updated)
        return updated

    async def _async_notification_actor(self, event: Any) -> tuple[str | None, bool]:
        """Resolve the authenticated HA actor carried by a Companion event."""
        context = getattr(event, "context", None)
        raw_user_id = getattr(context, "user_id", None)
        user_id = str(raw_user_id) if raw_user_id else None
        if user_id is None:
            return None, False
        auth = getattr(self._hass, "auth", None)
        getter = getattr(auth, "async_get_user", None)
        if getter is None:
            return None, False
        try:
            user = await getter(user_id)
        except Exception:  # noqa: BLE001 - auth backends may fail during shutdown
            _LOGGER.warning("Could not resolve notification actor %s", user_id)
            return None, False
        if user is None:
            return None, False
        return user_id, bool(getattr(user, "is_admin", False))

    async def _async_deliver_failure_feedback(self, event: AgentEvent) -> None:
        """Make a denied/failed push action visible without offering stale buttons."""
        reason = event.last_error or "Die Aktion ist nicht mehr erlaubt."
        feedback = replace(
            event,
            title="HomeIntent: Aktion nicht ausgeführt",
            message=reason,
            mode=AgentMode.INFORM,
            state=AgentEventState.RESOLVED,
            proposed_action=None,
        )
        await self._delivery.async_deliver(feedback, self._entry.options)

    async def _async_snooze_locked(self, event: AgentEvent, seconds: int) -> None:
        # HA time triggers use configured local wall-clock time. Persisted
        # lifecycle timestamps remain UTC elsewhere in this module.
        due = dt_util.now() + timedelta(seconds=seconds)
        automation_id = uuid.uuid4().hex
        config = {
            "alias": f"HomeIntent: {event.title} erneut prüfen",
            "description": "Von HomeIntent verwaltete Agent-Snooze-Prüfung",
            "triggers": [{"trigger": "time", "at": due.strftime("%H:%M:%S")}],
            "conditions": [
                {
                    "condition": "template",
                    "value_template": "{{ now().date().isoformat() == '"
                    + due.date().isoformat()
                    + "' }}",
                }
            ],
            "actions": [
                {
                    "action": "homeintent.recheck_agent_event",
                    "data": {"event_id": event.event_id},
                },
                {
                    "action": "homeintent.delete_automation",
                    "data": {"automation_id": automation_id},
                },
            ],
        }
        await AutomationExecutor(self._hass).async_create_automation(
            config,
            automation_id=automation_id,
            scheduled_for=due,
            once=True,
        )
        await self._store.async_save(
            replace(
                event,
                state=AgentEventState.SNOOZED,
                updated_at=dt_util.utcnow().isoformat(),
                snoozed_until=due.isoformat(),
                reminder_automation_id=automation_id,
            )
        )

    async def _deliver_and_store(self, event: AgentEvent) -> AgentEvent:
        result = await self._delivery.async_deliver(event, self._entry.options)
        now = dt_util.utcnow().isoformat()
        updated = replace(
            event,
            state=(event.state if event.state is AgentEventState.RESOLVED else AgentEventState.NOTIFIED),
            updated_at=now,
            delivery_attempts=event.delivery_attempts + 1,
            delivered_channels=result.delivered_channels,
            last_error="; ".join(result.errors) if result.errors else None,
        )
        await self._store.async_save(updated)
        return updated

    async def _find_duplicate(
        self, dedupe_key: str, now: Any, cooldown_seconds: int
    ) -> AgentEvent | None:
        for event in (await self._store.async_load_all()).values():
            if event.dedupe_key != dedupe_key or event.state is AgentEventState.EXPIRED:
                continue
            if event.state in {
                AgentEventState.ACTIVE,
                AgentEventState.NOTIFIED,
                AgentEventState.SNOOZED,
            }:
                return event
            if cooldown_seconds and (
                now - _parse_datetime(event.updated_at)
            ).total_seconds() < cooldown_seconds:
                return event
        return None

    @property
    def _configured_cooldown(self) -> int:
        value = self._entry.options.get(
            CONF_AGENT_COOLDOWN_SECONDS, DEFAULT_AGENT_COOLDOWN_SECONDS
        )
        return value if isinstance(value, int) else DEFAULT_AGENT_COOLDOWN_SECONDS

    def _source_still_matches(self, event: AgentEvent) -> bool:
        if event.source_entity_id is None:
            return True
        state = self._hass.states.get(event.source_entity_id)
        if state is None:
            return False
        if event.expected_source_state is not None:
            return str(state.state) == event.expected_source_state
        if event.source_comparator is not None and event.source_threshold is not None:
            try:
                value = float(state.state)
            except (TypeError, ValueError):
                return False
            if event.source_comparator == "above":
                return value > event.source_threshold
            if event.source_comparator == "below":
                return value < event.source_threshold
            return value == event.source_threshold
        return True

    def _automation_entity_id(self, automation_id: str) -> str | None:
        try:
            from homeassistant.helpers import entity_registry as er

            return er.async_get(self._hass).async_get_entity_id(
                "automation", "automation", automation_id
            )
        except Exception:  # noqa: BLE001 - registry may be unavailable during startup
            return None

    def _configured_notification_device_ids(self) -> tuple[str, ...]:
        """Resolve configured notify entities to device IDs when HA exposes them."""
        raw_targets = self._entry.options.get(CONF_AGENT_NOTIFY_TARGETS, ())
        if not isinstance(raw_targets, (list, tuple)):
            return ()
        try:
            from homeassistant.helpers import entity_registry as er

            registry = er.async_get(self._hass)
            device_ids = {
                entry.device_id
                for target in raw_targets
                if isinstance(target, str)
                if (entry := registry.async_get(target)) is not None
                if entry.device_id
            }
        except Exception:  # noqa: BLE001 - registry may be unavailable during setup
            return ()
        return tuple(sorted(device_ids))

    def _parse_action(self, data: Mapping[str, Any]) -> ServiceCallPlan | None:
        domain = _optional_text(data.get("action_domain"), limit=100)
        service = _optional_text(data.get("action_service"), limit=100)
        raw_entity_ids = data.get("action_entity_id")
        if domain is None and service is None and raw_entity_ids is None:
            return None
        if domain is None or service is None:
            raise ValueError("action_domain und action_service müssen zusammen gesetzt sein")
        if isinstance(raw_entity_ids, str):
            entity_ids = [raw_entity_ids]
        elif isinstance(raw_entity_ids, (list, tuple)):
            entity_ids = [str(item) for item in raw_entity_ids if isinstance(item, str)]
        else:
            entity_ids = []
        if not entity_ids:
            raise ValueError("action_entity_id fehlt")
        action_data = data.get("action_data", {})
        if isinstance(action_data, str):
            try:
                action_data = json.loads(action_data) if action_data else {}
            except json.JSONDecodeError as err:
                raise ValueError("action_data ist kein gültiges JSON-Objekt") from err
        if not isinstance(action_data, Mapping):
            raise ValueError("action_data muss ein Objekt sein")
        target: str | list[str] = entity_ids[0] if len(entity_ids) == 1 else entity_ids
        plan = ServiceCallPlan(domain, service, target, dict(action_data))
        if error := validate_agent_service_plan(plan):
            raise ValueError(error)
        return plan


def _required_text(value: object, field: str, *, limit: int) -> str:
    result = _optional_text(value, limit=limit)
    if result is None:
        raise ValueError(f"{field} fehlt")
    return result


def _optional_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped and len(stripped) <= limit else None


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return max(minimum, min(maximum, int(value)))
    return default


def _parse_datetime(value: str) -> Any:
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return dt_util.utcnow()
    return parsed
