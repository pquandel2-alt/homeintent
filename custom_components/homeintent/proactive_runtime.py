"""Home Assistant glue for V12: implements ``ProactivePorts`` with real HA APIs.

No second event engine: state changes arrive through the existing
``SituationRuntime`` listener, push actions through the existing
``ProactiveAgentRuntime`` notification listener, and effect expiries through
the existing ``EffectMonitor`` handler.  Scheduling uses one
``async_track_point_in_utc_time`` callback per stable key (no polling loop).
Transport stays with ``AgentDelivery``; device writes stay with
``service_executor.async_execute_service_plan`` via ``V10ProposalRunner``.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback as ha_callback
from homeassistant.util import dt as dt_util

from .agent_delivery import AgentDelivery
from .attention_policy import AttentionConfig
from .communication_router import RouterConfig
from .const import (
    CONF_AGENT_MEDIA_PLAYERS,
    CONF_AGENT_QUIET_END,
    CONF_AGENT_QUIET_START,
    CONF_AGENT_TTS_ENTITY,
    CONF_ATTENTION_BUDGET_ENABLED,
    CONF_CRITICAL_MULTI_CHANNEL_ENABLED,
    CONF_PROACTIVE_APPLIANCE_ENTITIES,
    CONF_PROACTIVE_CONTEXT_ENABLED,
    CONF_PROACTIVE_ENTRY_OPEN_MINUTES,
    CONF_PROACTIVE_PERSON_ROOM_SENSORS,
    CONF_PROACTIVE_SATELLITE_AREAS,
    CONF_PROACTIVE_USER_QUIET_HOURS,
    CONF_PUSH_PROACTIVE_ENABLED,
    CONF_QUIET_HOURS_ENABLED,
    CONF_ROOM_AWARE_VOICE_ENABLED,
    CONF_STANDING_PERMISSIONS_ENABLED,
    CONF_VOICE_PROACTIVE_ENABLED,
)
from .context_forecast import HabitEvidence
from .entities import EntitySnapshot
from .hass_entities import build_entity_snapshots
from .learning_manager import time_band
from .model_registry import LearnedKind, ModelHealth
from .planner import effect_satisfied
from .proactive_engine import (
    DeliveryReceipt,
    OutgoingMessage,
    PUSH_ACTION_PREFIX,
    ProactiveConfig,
    ProactiveContextEngine,
    ReplyResult,
)
from .proactive_execution import V10ProposalRunner
from .proactive_model import (
    CommunicationChannel,
    ProactiveSituation,
    PushActionBinding,
    QuietHoursWindow,
    RecipientContext,
    RoomPresenceResult,
    SatelliteRecord,
    SituationEvidence,
    SituationKind,
)
from .proactive_policy import QuietHoursPolicy, parse_quiet_window
from .proactive_store import GenerationalJsonFile
from .room_presence import (
    RoomPresenceConfig,
    RoomPresenceResolver,
    SatelliteRegistry,
    build_area_lookup,
    parse_mapping_lines,
)
from .runtime_data import HomeIntentRuntimeData
from .service_call import ServiceCallPlan
from .service_executor import async_execute_service_plan
from .situation_detection import (
    DetectionSignal,
    DetectorConfig,
    HabitCandidate,
    SituationDetector,
    habit_signal,
    parse_habit_sequence,
)
from .user_context import BindingStatus


_LOGGER = logging.getLogger(__name__)
MAX_SCHEDULED = 600


def build_config(options: Mapping[str, object]) -> ProactiveConfig:
    """Map config-entry options to the typed V12 configuration."""
    quiet_enabled = bool(options.get(CONF_QUIET_HOURS_ENABLED, True))
    default_window: QuietHoursWindow | None = None
    user_windows: dict[str, QuietHoursWindow] = {}
    if quiet_enabled:
        start = options.get(CONF_AGENT_QUIET_START, "22:00")
        end = options.get(CONF_AGENT_QUIET_END, "07:00")
        default_window = parse_quiet_window(f"{start}-{end}")
        for user_id, values in parse_mapping_lines(
            options.get(CONF_PROACTIVE_USER_QUIET_HOURS), key_prefix="",
        ).items():
            window = parse_quiet_window(values[0])
            if window is not None:
                user_windows[user_id] = window
    minutes = options.get(CONF_PROACTIVE_ENTRY_OPEN_MINUTES, 15)
    tts = options.get(CONF_AGENT_TTS_ENTITY)
    players = options.get(CONF_AGENT_MEDIA_PLAYERS)
    return ProactiveConfig(
        enabled=bool(options.get(CONF_PROACTIVE_CONTEXT_ENABLED, False)),
        standing_permissions_enabled=bool(options.get(CONF_STANDING_PERMISSIONS_ENABLED, False)),
        entry_open_minutes=(
            max(1, min(240, minutes)) if isinstance(minutes, int) and not isinstance(minutes, bool) else 15
        ),
        attention=AttentionConfig(enabled=bool(options.get(CONF_ATTENTION_BUDGET_ENABLED, True))),
        router=RouterConfig(
            voice_enabled=bool(options.get(CONF_VOICE_PROACTIVE_ENABLED, True)),
            push_enabled=bool(options.get(CONF_PUSH_PROACTIVE_ENABLED, True)),
            room_aware_voice_enabled=bool(options.get(CONF_ROOM_AWARE_VOICE_ENABLED, True)),
            critical_multi_channel_enabled=bool(options.get(CONF_CRITICAL_MULTI_CHANNEL_ENABLED, True)),
            house_speakers_configured=(
                isinstance(tts, str) and bool(tts)
                and isinstance(players, (list, tuple)) and bool(players)
            ),
        ),
        quiet=QuietHoursPolicy(default_window, user_windows),
    )


class ProactiveRuntime:
    """Owns the V12 engine for one config entry and adapts it to HA."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        runtime_data: HomeIntentRuntimeData,
        storage_path: str,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._data = runtime_data
        options: Mapping[str, object] = entry.options
        appliances = options.get(CONF_PROACTIVE_APPLIANCE_ENTITIES, ())
        self._detector = SituationDetector(DetectorConfig(
            entry_open_minutes=build_config(options).entry_open_minutes,
            appliance_entity_ids=frozenset(
                item for item in (appliances if isinstance(appliances, (list, tuple)) else ())
                if isinstance(item, str)
            ),
        ))
        self._room = RoomPresenceResolver(RoomPresenceConfig(
            person_room_sensors=parse_mapping_lines(
                options.get(CONF_PROACTIVE_PERSON_ROOM_SENSORS), key_prefix="person.",
            ),
        ))
        self._configured_satellites = {
            key: values[0]
            for key, values in parse_mapping_lines(
                options.get(CONF_PROACTIVE_SATELLITE_AREAS), key_prefix="assist_satellite.",
            ).items()
        }
        self._delivery = AgentDelivery(hass)
        self._timers: dict[str, Callable[[], None]] = {}
        self._habits: dict[str, HabitEvidence] = {}
        self._habit_candidates: tuple[HabitCandidate, ...] = ()
        self._habit_triggers: frozenset[str] = frozenset()
        self._entities_cache: tuple[datetime, dict[str, EntitySnapshot]] | None = None
        runner = V10ProposalRunner(
            refresh=self._async_refresh,
            execute_service=self._async_execute_service,
            verify=self._async_verify,
            coordinator=runtime_data.execution_coordinator,
            goal_runs=runtime_data.goal_runs,
            options=options,
        )
        self.engine: ProactiveContextEngine = ProactiveContextEngine(
            self, runner, config=build_config(options),
            predictive_house=runtime_data.predictive_house,
            detector=self._detector,
            storage=GenerationalJsonFile(storage_path),
        )

        from .proactive_dialog import ProactiveDialogHandler

        self.dialogs = ProactiveDialogHandler(self.engine, runtime_data.dialog_manager)

    @property
    def enabled(self) -> bool:
        return self.engine.config.enabled

    # ------------------------------------------------------------ lifecycle
    async def async_start(self) -> Callable[[], None]:
        if self.enabled:
            try:
                await self.engine.async_restore()
            except Exception:  # noqa: BLE001 - corrupt state must never block setup
                _LOGGER.exception("HomeIntent V12 state restore failed; starting empty")
            await self.async_refresh_habits()

        def stop() -> None:
            for cancel in tuple(self._timers.values()):
                cancel()
            self._timers.clear()

        return stop

    # --------------------------------------------------------------- hooks
    async def async_observe_state(
        self, entity: EntitySnapshot, previous: str | None, entities: tuple[EntitySnapshot, ...],
    ) -> None:
        if not self.enabled:
            return
        self._entities_cache = (dt_util.utcnow(), {item.entity_id: item for item in entities})
        nobody = self.nobody_home()
        await self.engine.async_observe_state(entity, previous, entities, nobody_home=nobody)
        if entity.entity_id in self._habit_triggers:
            signal = habit_signal(
                entity, self._habit_candidates, now=dt_util.utcnow(), local_now=dt_util.now(),
                band=time_band(dt_util.now().hour),
                home_user_ids=frozenset(self._home_user_ids()),
                entities=self._entities_cache[1],
            )
            if signal is not None:
                await self.engine.async_process_signals((signal,), now=dt_util.utcnow())

    async def async_report_effect_anomaly(self, entity_id: str, operator_id: str | None, name: str) -> None:
        if not self.enabled:
            return
        now = dt_util.utcnow()
        await self.engine.async_report_situation(DetectionSignal(
            SituationKind.DEVICE_EFFECT_ANOMALY,
            f"{SituationKind.DEVICE_EFFECT_ANOMALY.value}:{entity_id}",
            (entity_id,), None, True, now,
            (SituationEvidence("operator_id", operator_id or ""),),
            name,
        ))

    async def async_report_thermal_risk(
        self, *, area_id: str, area_name: str, goal_id: str, current: float | None,
        target: float, owner_user_id: str | None,
    ) -> None:
        if not self.enabled:
            return
        now = dt_util.utcnow()
        evidence = [
            SituationEvidence("goal_id", goal_id),
            SituationEvidence("target_celsius", f"{target:g}"),
            SituationEvidence("area_name", area_name),
        ]
        if current is not None:
            evidence.append(SituationEvidence("current_celsius", f"{current:g}"))
        await self.engine.async_report_situation(DetectionSignal(
            SituationKind.THERMAL_GOAL_AT_RISK,
            f"{SituationKind.THERMAL_GOAL_AT_RISK.value}:{goal_id}",
            (), area_id, True, now, tuple(evidence), area_name,
            owner_user_id=owner_user_id,
        ))

    async def async_report_goal_failure(self, *, run_id: str, goal_label: str, owner_user_id: str | None) -> None:
        if not self.enabled or owner_user_id is None:
            return
        now = dt_util.utcnow()
        await self.engine.async_report_situation(DetectionSignal(
            SituationKind.PENDING_GOAL_REQUIRES_ATTENTION,
            f"{SituationKind.PENDING_GOAL_REQUIRES_ATTENTION.value}:{run_id}",
            (), None, True, now, (SituationEvidence("run_id", run_id),),
            goal_label, owner_user_id=owner_user_id,
        ))

    def record_timer_finished(self, label: str) -> None:
        """NativeTimer already announced it; V12 only notes history."""
        if self.enabled:
            self.engine.record_external_communication(
                SituationKind.TIMER_FINISHED, label, owner="native_timer",
            )

    def record_authenticated_turn(self, user_id: str | None, device_id: str | None) -> None:
        """A known HA user spoke on a known satellite: short-lived room evidence."""
        if not self.enabled or user_id is None or device_id is None:
            return
        person = self.person_for(user_id)
        satellite = self.satellites().by_device(device_id)
        if person is not None and satellite is not None and satellite.area_id is not None:
            self._room.record_interaction(person, satellite.area_id, dt_util.utcnow())

    async def async_handle_push_action(self, action: str, *, user_id: str | None, device_id: str | None) -> bool:
        if not action.startswith(PUSH_ACTION_PREFIX):
            return False
        if not self.enabled:
            return True
        result = await self.engine.async_handle_push_action(
            action, user_id=user_id, device_id=device_id,
        )
        if result is not None and result.handled and user_id is not None:
            await self._async_push_feedback(user_id, result)
        return True

    def _companion_device(self, user_id: str | None, target_id: str) -> str | None:
        """HA device behind a notify *entity* bound to this user, or None.

        Authoritative path only: UserContext notification binding -> HA
        entity registry.  A legacy ``notify.<service>`` target has no registry
        entry and is never mapped by name, so it gets no action buttons.
        """
        contexts = self._data.user_contexts
        if contexts is None or user_id is None:
            return None
        user = next((item for item in contexts.bound_users() if item.ha_user_id == user_id), None)
        if user is None or not any(item.target_id == target_id for item in user.notification_targets):
            return None
        try:
            from homeassistant.helpers import entity_registry as er

            entry = er.async_get(self._hass).async_get(target_id)
        except Exception:  # noqa: BLE001 - registry may be unavailable
            return None
        device_id = getattr(entry, "device_id", None) if entry is not None else None
        return str(device_id) if device_id else None

    async def async_handle_reply(
        self, text: str, *, user_id: str | None, device_id: str | None, is_admin: bool,
        other_open_questions: int,
    ) -> ReplyResult | None:
        if not self.enabled:
            return None
        return await self.engine.async_handle_reply(
            text, user_id=user_id, device_id=device_id, is_admin=is_admin,
            other_open_questions=other_open_questions,
        )

    async def async_refresh_habits(self) -> None:
        registry = self._data.learned_models
        policy = self._data.learning_policy
        if registry is None or policy is None or not policy.suggestions_enabled:
            self._habit_candidates = ()
            self._habit_triggers = frozenset()
            return
        now = dt_util.utcnow()
        candidates: list[HabitCandidate] = []
        habits: dict[str, HabitEvidence] = {}
        for model in await registry.async_list(kind=LearnedKind.HABIT):
            sequence = model.parameters.get("sequence")
            steps = parse_habit_sequence(sequence) if isinstance(sequence, str) else ()
            band = model.context.get("time_band")
            if len(steps) < 2 or not isinstance(band, str):
                continue
            status = str(model.parameters.get("suggestion_status", "new"))
            weekday = model.context.get("weekday")
            candidates.append(HabitCandidate(
                model.model_id, model.subject, band,
                weekday if isinstance(weekday, int) else None, steps,
            ))
            habits[model.model_id] = HabitEvidence(
                model.model_id, model.subject,
                model.health is ModelHealth.VALID,
                model.health is ModelHealth.VALID,
                status in {"dismissed", "rejected", "never"},
                model.expires_at is not None and model.expires_at <= now,
                float(model.parameters.get("support", 0.0) or 0.0)
                if isinstance(model.parameters.get("support", 0.0), (int, float)) else 0.0,
            )
        self._habit_candidates = tuple(candidates[:64])
        self._habit_triggers = frozenset(item.steps[0][0] for item in self._habit_candidates)
        self._habits = habits

    # --------------------------------------------------------------- ports
    def now(self) -> datetime:
        return dt_util.utcnow()

    def local_now(self) -> datetime:
        return dt_util.now()

    def fresh_entities(self) -> Mapping[str, EntitySnapshot]:
        return {item.entity_id: item for item in build_entity_snapshots(self._hass, self._entry)}

    def _person_states(self) -> dict[str, str]:
        states: dict[str, str] = {}
        contexts = self._data.user_contexts
        if contexts is None:
            return states
        persons = set(contexts.household.person_entity_ids) | {
            item.person_entity_id for item in contexts.bound_users() if item.person_entity_id
        }
        for person in persons:
            state = self._hass.states.get(person)
            if state is not None:
                states[person] = str(state.state)
        return states

    def nobody_home(self) -> bool | None:
        contexts = self._data.user_contexts
        return contexts.nobody_home(self._person_states()) if contexts is not None else None

    def _home_user_ids(self) -> tuple[str, ...]:
        contexts = self._data.user_contexts
        return contexts.present_user_ids(self._person_states()) if contexts is not None else ()

    def recipients_for(self, situation: ProactiveSituation) -> tuple[RecipientContext, ...]:
        contexts = self._data.user_contexts
        if contexts is None:
            return ()
        states = self._person_states()
        recipients: list[RecipientContext] = []
        for user in contexts.bound_users():
            if user.person_entity_id is None:
                continue
            if situation.owner_user_id is not None and user.ha_user_id != situation.owner_user_id:
                continue
            binding = contexts.resolve_notification_targets(user.person_entity_id, channel="push")
            targets = tuple(item.target_id for item in binding.targets) if binding.status is BindingStatus.RESOLVED else ()
            state = states.get(user.person_entity_id)
            recipients.append(RecipientContext(
                user.ha_user_id, user.person_entity_id,
                None if state is None else state == "home",
                targets, binding.status is BindingStatus.AMBIGUOUS,
            ))
        home = [item for item in recipients if item.home]
        return tuple(home or recipients)

    def room_for(self, person_id: str | None) -> RoomPresenceResult | None:
        if person_id is None:
            return None
        entities = self.fresh_entities()
        states = self._person_states()
        return self._room.resolve(
            person_id, person_home=(states.get(person_id) == "home") if person_id in states else None,
            entities=entities, area_lookup=build_area_lookup(entities.values()),
            home_person_ids=tuple(key for key, value in states.items() if value == "home"),
            now=dt_util.utcnow(),
        )

    def others_home(self, person_id: str | None) -> bool:
        return any(
            state == "home" for person, state in self._person_states().items() if person != person_id
        )

    def satellites(self) -> SatelliteRegistry:
        records: list[SatelliteRecord] = []
        try:
            from homeassistant.helpers import device_registry as dr
            from homeassistant.helpers import entity_registry as er

            entity_registry = er.async_get(self._hass)
            device_registry = dr.async_get(self._hass)
            for item in tuple(entity_registry.entities.values()):
                if not item.entity_id.startswith("assist_satellite."):
                    continue
                area_id = item.area_id
                if area_id is None and item.device_id:
                    device = device_registry.async_get(item.device_id)
                    area_id = getattr(device, "area_id", None)
                records.append(SatelliteRecord(item.entity_id, area_id, item.device_id))
        except Exception:  # noqa: BLE001 - registries may be unavailable during startup
            _LOGGER.debug("Satellite registry unavailable", exc_info=True)
        return SatelliteRegistry(records, self._configured_satellites)

    def owner_known(self, user_id: str) -> bool:
        contexts = self._data.user_contexts
        return contexts is not None and contexts.resolve_current_person(user_id).status is BindingStatus.RESOLVED

    def person_for(self, user_id: str | None) -> str | None:
        contexts = self._data.user_contexts
        if contexts is None:
            return None
        binding = contexts.resolve_current_person(user_id)
        return binding.person_entity_id if binding.status is BindingStatus.RESOLVED else None

    def active_goal_subjects(self) -> frozenset[str]:
        return self._data.execution_coordinator.reserved_entity_ids()

    def habit_evidence(self, situation: ProactiveSituation) -> HabitEvidence | None:
        model_id = situation.evidence_value("habit_model")
        return self._habits.get(model_id or "")

    async def async_is_admin(self, user_id: str | None) -> bool:
        if not user_id:
            return False
        auth = getattr(self._hass, "auth", None)
        getter = getattr(auth, "async_get_user", None)
        if getter is None:
            return False
        try:
            user = await getter(user_id)
        except Exception:  # noqa: BLE001
            return False
        return bool(user and getattr(user, "is_admin", False))

    async def async_deliver(self, message: OutgoingMessage) -> DeliveryReceipt:
        decision = message.decision
        delivered: list[CommunicationChannel] = []
        errors: list[str] = []
        origin: str | None = None
        if CommunicationChannel.VOICE in decision.channels:
            try:
                if decision.satellite_entity_id is not None:
                    await self._delivery.async_satellite_message(
                        decision.satellite_entity_id, message.text,
                        ask=message.proposal_id is not None,
                    )
                    record = self.satellites().by_entity(decision.satellite_entity_id)
                    origin = record.device_id if record is not None else None
                else:
                    await self._delivery.async_speak(message.text, self._entry.options)
                delivered.append(CommunicationChannel.VOICE)
            except Exception as err:  # noqa: BLE001 - provider failures vary
                errors.append(f"voice:{type(err).__name__}")
        if (
            decision.recipient_user_id is None
            and CommunicationChannel.PUSH in decision.channels
            and not decision.push_target_ids
        ):
            # Critical household fallback: visible in the HA UI for everyone.
            try:
                await self._hass.services.async_call(
                    "persistent_notification", "create",
                    {"title": message.title, "message": message.text,
                     "notification_id": "homeintent_v12_critical"},
                    blocking=True,
                )
                delivered.append(CommunicationChannel.PUSH)
            except Exception as err:  # noqa: BLE001
                errors.append(f"notification:{type(err).__name__}")
        bindings: list[PushActionBinding] = []
        if decision.push_target_ids:
            contexts = self._data.user_contexts
            for target in decision.push_target_ids:
                kind = None
                if contexts is not None:
                    for user in contexts.bound_users():
                        for item in user.notification_targets:
                            if item.target_id == target:
                                kind = item.kind
                actions: tuple[tuple[str, str], ...] = ()
                device_id = (
                    self._companion_device(decision.recipient_user_id, target)
                    if message.proposal_id is not None else None
                )
                if (
                    message.proposal_id is not None
                    and device_id is not None
                    and decision.recipient_user_id
                    # Buttons exist only on a legacy mobile_app notify
                    # service; a notify entity (``notify.send_message``)
                    # gets a plain push and the proposal stays answerable by
                    # voice or dashboard - no token is minted for it.
                    and self._delivery.supports_actions(target, kind)
                ):
                    # Buttons only for an authoritatively bound Companion
                    # device; the token reaches only that device.
                    token = f"t{secrets.token_hex(8)}"
                    suffix = f"{message.proposal_id}_{token}"
                    actions = (
                        (f"{PUSH_ACTION_PREFIX}ACCEPT_{suffix}", message.accept_label or "Ausführen"),
                        (f"{PUSH_ACTION_PREFIX}LATER_{suffix}", "Später"),
                        (f"{PUSH_ACTION_PREFIX}IGNORE_{suffix}", "Ignorieren"),
                    )
                    bindings.append(PushActionBinding(token, decision.recipient_user_id, device_id))
                try:
                    await self._delivery.async_deliver_typed_notification(
                        target, target_kind=kind, title=message.title, message=message.text,
                        dedupe_key=f"homeintent_v12_{message.proposal_id or 'info'}",
                        severity="critical" if message.priority.value >= 4 else "info",
                        goal_id="proactive", run_id=message.proposal_id or "",
                        actions=actions,
                    )
                    kind_channel = (
                        CommunicationChannel.INTERACTIVE_PUSH if actions else CommunicationChannel.PUSH
                    )
                    if kind_channel not in delivered:
                        delivered.append(kind_channel)
                except Exception as err:  # noqa: BLE001
                    errors.append(f"push:{type(err).__name__}")
                    if actions:
                        bindings.pop()
        return DeliveryReceipt(tuple(delivered), origin, tuple(errors), tuple(bindings))

    async def _async_push_feedback(self, user_id: str, result: ReplyResult) -> None:
        contexts = self._data.user_contexts
        person = self.person_for(user_id)
        if contexts is None or person is None:
            return
        binding = contexts.resolve_notification_targets(person, channel="push")
        if binding.status is not BindingStatus.RESOLVED:
            return
        for target in binding.targets:
            try:
                await self._delivery.async_deliver_typed_notification(
                    target.target_id, target_kind=target.kind, title="HomeIntent",
                    message=result.speech, dedupe_key="homeintent_v12_feedback",
                    severity="info", goal_id="proactive", run_id="",
                )
            except Exception:  # noqa: BLE001
                _LOGGER.warning("HomeIntent V12 push feedback failed", exc_info=True)

    def schedule(self, key: str, at: datetime, callback: Callable[[], Awaitable[None]]) -> None:
        from homeassistant.helpers.event import async_track_point_in_utc_time

        self.cancel(key)
        if len(self._timers) >= MAX_SCHEDULED:
            _LOGGER.warning("HomeIntent V12 scheduler bound reached; dropping %s", key)
            return

        # Must run in the event loop: without @callback HA dispatches a plain
        # function to the executor, where hass.async_create_task raises.
        @ha_callback
        def _fire(_now: Any) -> None:
            self._timers.pop(key, None)
            self._hass.async_create_task(callback(), name=f"HomeIntent V12 {key}")

        self._timers[key] = async_track_point_in_utc_time(self._hass, _fire, at)

    def cancel(self, key: str) -> None:
        cancel = self._timers.pop(key, None)
        if cancel is not None:
            cancel()

    # ------------------------------------------------------ V10 adapters
    async def _async_refresh(self) -> list[EntitySnapshot]:
        return build_entity_snapshots(self._hass, self._entry)

    async def _async_execute_service(
        self, plan: ServiceCallPlan, fresh: list[EntitySnapshot], confirmed: bool,
        user_id: str | None, is_admin: bool,
    ) -> Any:
        return await async_execute_service_plan(
            self._hass, plan, fresh, self._entry.options,
            is_admin=is_admin, user_id=user_id, confirmed=confirmed,
            audit_trail=self._data.audit_trail,
            audit_actor_id=f"proactive:{user_id or 'permission'}",
            effect_monitor=self._data.effect_monitor,
        )

    async def _async_verify(self, entity_id: str, expected: str) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + min(10.0, self._data.effect_monitor.timeout.total_seconds())
        while True:
            current = next(
                (item for item in build_entity_snapshots(self._hass, self._entry)
                 if item.entity_id == entity_id),
                None,
            )
            if current is not None and effect_satisfied(current, expected):
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.1, remaining))


__all__ = ("ProactiveRuntime", "build_config")
