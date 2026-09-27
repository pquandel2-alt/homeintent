"""The homeintent integration: a deterministic, LLM-free Assist conversation agent.

Setup only forwards to the ``conversation`` platform - all matching logic
lives in ``engine.py``/``entities.py``/``service_call.py`` and is loaded
lazily by ``NluConversationEntity`` itself.

Generated one-shot and run-limited automations use the internal services
``homeintent.delete_automation`` and ``homeintent.record_automation_run``. They exist
so an automation can complete its own lifecycle; users manage automations
through the conversation agent rather than raw automation ids.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .const import (
    CONF_CONTEXT_TTL_SECONDS,
    CONF_DOCUMENTS_DIRECTORY,
    CONF_DOCUMENTS_ENABLED,
    CONF_MEMORY_ENABLED,
    CONF_MEMORY_RETENTION_DAYS,
    CONF_EXPERIENCE_LEARNING_ENABLED,
    CONF_PREDICTIVE_MODELS_ENABLED,
    CONF_HABIT_DISCOVERY_ENABLED,
    CONF_PROACTIVE_SUGGESTIONS_ENABLED,
    CONF_LEARNING_RETENTION_COUNT,
    CONF_MINIMUM_PREDICTION_CONFIDENCE,
    DOMAIN,
)
from .memory import MemoryKind, MemoryStore
from .goal_run import GoalRun, GoalRunStatus, GoalRunStore
from .monitor_goal import MonitorGoalRuntime, MonitorGoalStore
from .nlu.context import ConversationContextStore
from .profiles import ProfileStore
from .engine import NluEngine
from .runtime_data import HomeIntentRuntimeData
from .storage_migration import resolve_storage_path
from .user_context import UserContextStore
from .experience_store import ExperienceStore
from .learning_manager import LearningManager
from .learning_policy import LearningMode, LearningPolicy
from .model_registry import ModelRegistry
from .predictive_house_model import PredictiveHouseModel
from .prediction import PredictionStatus
from .service_call import ServiceCallPlan
from .thermal_tracker import ThermalExperienceTracker
from .statistical_models import evaluate_latency_anomaly
from .thermal_deadline import (
    PendingThermalCheckpointStore,
    ThermalCheckpointResult,
    ThermalCheckpointStatus,
    ThermalDeadlineCheckpoint,
    async_process_thermal_checkpoint,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall

PLATFORMS = ["conversation"]

_LOGGER = logging.getLogger(__name__)

SERVICE_DELETE_AUTOMATION = "delete_automation"
SERVICE_RECORD_AUTOMATION_RUN = "record_automation_run"
SERVICE_ENABLE_AUTOMATION = "enable_automation"
SERVICE_PROACTIVE_MESSAGE = "proactive_message"
SERVICE_RECHECK_AGENT_EVENT = "recheck_agent_event"
SERVICE_BIND_USER_CONTEXT = "bind_user_context"
SERVICE_SET_HOUSEHOLD = "set_household"
SERVICE_SAVE_ROUTINE = "save_routine"
SERVICE_SAVE_COMFORT_PROFILE = "save_comfort_profile"
SERVICE_DELETE_MONITOR_GOAL = "delete_monitor_goal"
SERVICE_THERMAL_DEADLINE_CHECKPOINT = "thermal_deadline_checkpoint"
LEGACY_DOMAIN = "ha_nlu"

# A self-deleting automation must finish the service action that requested
# its deletion before ``automation.reload`` removes/unloads that automation.
# Without this grace period the reload waits for the running action script,
# while that script waits for this service handler: a circular wait.
SELF_DELETE_GRACE_SECONDS = 0.1
SELF_DELETE_RETRY_SECONDS = (1.0, 5.0)


def _build_engine() -> NluEngine:
    """Load all grammar files off the event loop and warm hassil's number rules."""
    engine = NluEngine()
    engine.warm_up()
    return engine


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    configured_ttl = entry.options.get(CONF_CONTEXT_TTL_SECONDS, 30)
    context_ttl = (
        float(configured_ttl)
        if isinstance(configured_ttl, (int, float)) and configured_ttl >= 10
        else 30.0
    )
    configured_retention = entry.options.get(CONF_MEMORY_RETENTION_DAYS, 90)
    retention_days = (
        configured_retention
        if isinstance(configured_retention, int) and configured_retention >= 1
        else 90
    )
    memory = MemoryStore(
        _storage_path(hass, "homeintent_memory.sqlite3", "ha_nlu_memory.sqlite3"),
        enabled=bool(entry.options.get(CONF_MEMORY_ENABLED, False)),
        retention_days=retention_days,
    )
    user_contexts = UserContextStore(
        Path(_storage_path(hass, "homeintent_users.json", "ha_nlu_users.json"))
    )
    profile_store = ProfileStore(
        _storage_path(hass, "homeintent_profiles.json", "ha_nlu_profiles.json")
    )
    goal_runs = GoalRunStore(
        _storage_path(hass, "homeintent_goal_runs.json", "ha_nlu_goal_runs.json")
    )
    monitor_goals = MonitorGoalStore(
        _storage_path(hass, "homeintent_monitor_goals.json", "ha_nlu_monitor_goals.json")
    )
    learning_enabled = bool(entry.options.get(CONF_EXPERIENCE_LEARNING_ENABLED, False))
    suggestions_enabled = bool(entry.options.get(CONF_PROACTIVE_SUGGESTIONS_ENABLED, False))
    configured_confidence = entry.options.get(CONF_MINIMUM_PREDICTION_CONFIDENCE, 0.75)
    minimum_confidence = (
        float(configured_confidence)
        if isinstance(configured_confidence, (int, float))
        and 0.5 <= configured_confidence <= 1.0 else 0.75
    )
    configured_count = entry.options.get(CONF_LEARNING_RETENTION_COUNT, 5_000)
    retention_count = (
        configured_count if isinstance(configured_count, int)
        and 100 <= configured_count <= 20_000 else 5_000
    )
    learning_policy = LearningPolicy(
        experience_limit=retention_count,
        retention_days=retention_days,
        minimum_planning_confidence=minimum_confidence,
        learning_mode=(LearningMode.ASK if learning_enabled and suggestions_enabled
                       else LearningMode.SILENT_LEARN if learning_enabled
                       else LearningMode.OFF),
        predictive_models_enabled=bool(
            entry.options.get(CONF_PREDICTIVE_MODELS_ENABLED, False)
        ),
        habit_discovery_enabled=bool(
            entry.options.get(CONF_HABIT_DISCOVERY_ENABLED, False)
        ),
    )
    experiences = ExperienceStore(
        _storage_path(hass, "homeintent_experiences.json", "ha_nlu_experiences.json"),
        learning_policy,
    )
    learned_models = ModelRegistry(
        _storage_path(hass, "homeintent_models.json", "ha_nlu_models.json"),
        learning_policy,
    )
    predictive_house = PredictiveHouseModel(learning_policy)
    learning_manager = LearningManager(
        experiences, learned_models, predictive_house, learning_policy
    )
    thermal_tracker = ThermalExperienceTracker(
        learning_manager,
        state_path=_storage_path(
            hass, "homeintent_thermal_cycles.json", "ha_nlu_thermal_cycles.json"
        ),
    )
    thermal_checkpoints = PendingThermalCheckpointStore(
        _storage_path(
            hass, "homeintent_thermal_checkpoints.json",
            "ha_nlu_thermal_checkpoints.json",
        )
    )
    engine = await hass.async_add_executor_job(_build_engine)
    entry.runtime_data = HomeIntentRuntimeData(
        engine=engine,
        context_store=ConversationContextStore(ttl_seconds=context_ttl),
        memory=memory,
        user_contexts=user_contexts,
        profiles=profile_store,
        goal_runs=goal_runs,
        monitor_goals=monitor_goals,
        learning_policy=learning_policy,
        experiences=experiences,
        learned_models=learned_models,
        predictive_house=predictive_house,
        learning_manager=learning_manager,
        thermal_tracker=thermal_tracker,
        thermal_checkpoints=thermal_checkpoints,
    )
    def _learned_effect_timeout(plan: ServiceCallPlan) -> timedelta | None:
        entity_ids = (
            (plan.entity_id,) if isinstance(plan.entity_id, str)
            else tuple(plan.entity_id)
        )
        operator_id = f"{plan.domain}.{plan.service}".upper().replace(".", "_")
        upper_bounds: list[float] = []
        for entity_id in entity_ids:
            prediction = predictive_house.predict_effect_latency(operator_id, entity_id)
            if (
                prediction.status is PredictionStatus.OK
                and prediction.uncertainty is not None
            ):
                timing = predictive_house.effect_timing_model(operator_id, entity_id)
                if timing is not None:
                    upper_bounds.append(
                        evaluate_latency_anomaly(timing, float("inf")).threshold_seconds
                    )
        return timedelta(seconds=max(upper_bounds)) if upper_bounds else None

    entry.runtime_data.effect_monitor.set_timeout_resolver(_learned_effect_timeout)
    def _observe_accepted_action(plan: ServiceCallPlan, occurred_at) -> None:
        from .hass_entities import build_entity_snapshots

        thermal_tracker.observe_action(
            plan, tuple(build_entity_snapshots(hass, entry)), occurred_at=occurred_at
        )

    entry.runtime_data.effect_monitor.set_action_observer(_observe_accepted_action)
    async def _queue_learning(run: GoalRun) -> None:
        thermal_tracker.associate_goal_run(run)
        task = hass.async_create_task(
            learning_manager.async_observe_goal_run(run),
            name=f"HomeIntent learn GoalRun {run.run_id}",
        )
        entry.runtime_data.learning_tasks.add(task)
        def _learning_done(done: asyncio.Task[None]) -> None:
            entry.runtime_data.learning_tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                error = done.exception()
                assert error is not None
                _LOGGER.error(
                    "HomeIntent GoalRun learning failed for %s",
                    run.run_id,
                    exc_info=(type(error), error, error.__traceback__),
                )
        task.add_done_callback(_learning_done)

    entry.runtime_data.remove_learning_listener = goal_runs.add_append_listener(
        _queue_learning
    )
    await memory.async_initialize()
    await user_contexts.async_load()
    await profile_store.async_load()
    await learning_manager.async_restore_models()
    from .hass_entities import build_entity_snapshots
    await thermal_tracker.async_restore(
        tuple(build_entity_snapshots(hass, entry)), goal_runs,
        now=datetime.now(timezone.utc),
    )
    if memory.enabled:
        await memory.async_apply_retention(
            {kind: retention_days for kind in MemoryKind}
        )
    if bool(entry.options.get(CONF_DOCUMENTS_ENABLED, False)):
        from .adapters import LocalDocumentIndex

        configured_directory = entry.options.get(
            CONF_DOCUMENTS_DIRECTORY, "homeintent_documents"
        )
        relative = (
            Path(configured_directory)
            if isinstance(configured_directory, str)
            else Path("homeintent_documents")
        )
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
            or str(relative) in {"", "."}
        ):
            _LOGGER.warning("HomeIntent document directory must stay below the HA config directory")
        else:
            document_index = LocalDocumentIndex(
                hass.config.path(*relative.parts),
                _storage_path(hass, "homeintent_documents.sqlite3", "ha_nlu_documents.sqlite3"),
                enabled=True,
            )
            entry.runtime_data.document_index = document_index
            indexing_task = hass.async_create_task(
                document_index.async_reindex(),
                name="HomeIntent local document indexing",
            )
            entry.async_on_unload(indexing_task.cancel)
    from .agent_runtime import ProactiveAgentRuntime
    from .agent_delivery import AgentDelivery

    from .native_timer import NativeTimerRuntime

    native_timer = NativeTimerRuntime(hass, entry)
    entry.runtime_data.native_timer = native_timer
    entry.async_on_unload(native_timer.async_start())
    await native_timer.async_load_journal()

    proactive_agent = ProactiveAgentRuntime(hass, entry, entry.runtime_data)
    entry.runtime_data.proactive_agent = proactive_agent
    entry.async_on_unload(proactive_agent.async_start())
    delivery = AgentDelivery(hass)

    async def _deliver_monitor(model, rendered) -> bool:
        return await delivery.async_deliver_typed_notification(
            model.target_id,
            target_kind=model.target_kind,
            title=rendered.title,
            message=rendered.message,
            dedupe_key=model.dedupe_key,
            severity=model.severity.value,
            goal_id=model.goal_id,
            run_id=model.run_id,
        )

    async def _fresh_entities():
        from .hass_entities import build_entity_snapshots

        return build_entity_snapshots(hass, entry)

    entry.runtime_data.monitor_runtime = MonitorGoalRuntime(
        monitor_goals, goal_runs, user_contexts, _fresh_entities, _deliver_monitor
    )
    from .event_runtime import SituationRuntime

    situation_runtime = SituationRuntime(hass, entry, entry.runtime_data)
    entry.runtime_data.situation_runtime = situation_runtime
    entry.async_on_unload(situation_runtime.async_start())
    from .proactive_runtime import ProactiveRuntime

    # V12 is an additive decision layer: it observes through the existing
    # SituationRuntime and acts only through the V10 pipeline.
    proactive_context = ProactiveRuntime(
        hass, entry, entry.runtime_data,
        _storage_path(hass, "homeintent_proactive.json", "ha_nlu_proactive.json"),
    )
    entry.runtime_data.proactive_context = proactive_context
    entry.async_on_unload(await proactive_context.async_start())

    async def _proactive_goal_failure(run: GoalRun) -> None:
        # Only a failure nobody was watching (scheduled / monitor goals) needs
        # a proactive notice; interactive and V12-originated runs report inline.
        if run.status not in {GoalRunStatus.FAILURE, GoalRunStatus.PARTIAL_FAILURE}:
            return
        if any(item.startswith("proactive:") for item in run.evidence):
            return
        unattended = run.goal.lifecycle.value == "monitor" or any(
            item.startswith("thermal") or item == "plan_status=scheduled"
            for item in run.evidence
        )
        if not unattended:
            return
        label = run.source_utterance or "Geplantes Ziel"
        await proactive_context.async_report_goal_failure(
            run_id=run.run_id, goal_label=label[:80], owner_user_id=run.user_id,
        )

    entry.runtime_data.remove_proactive_listener = goal_runs.add_append_listener(
        _proactive_goal_failure
    )
    from .adapters import StructuredAdapterRuntime

    adapter_runtime = StructuredAdapterRuntime(
        hass, entry, entry.runtime_data.adapter_evidence.append
    )
    entry.runtime_data.adapter_runtime = adapter_runtime
    entry.async_on_unload(await adapter_runtime.async_start())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _async_check_fixed_selection(hass, entry)
    from homeassistant.core import callback
    from homeassistant.helpers.start import async_at_started

    # Entities are still being added while HA starts; check again once it
    # runs so a frozen selection is reported against the complete exposure.
    @callback
    def _check_when_started(_hass: HomeAssistant) -> None:
        _async_check_fixed_selection(hass, entry)

    entry.async_on_unload(async_at_started(hass, _check_when_started))
    try:
        from .learning_center_ws import async_setup_learning_center

        # 7.1 Learning Center: one sidebar panel + authenticated WebSocket API
        # over the existing V11/V12 authorities.  A panel failure must never
        # take the conversation agent down with it.
        await async_setup_learning_center(hass, entry)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("HomeIntent Learning Center could not be registered")

    # Single-instance-only integration (config_flow.py's own
    # ``_async_abort_entries_match({})``) - guarded anyway so a second
    # ``async_setup_entry`` call (e.g. re-added after a full removal) never
    # raises on an already-registered service.
    if not hass.services.has_service(DOMAIN, SERVICE_DELETE_AUTOMATION):

        async def _handle_delete_automation(call: ServiceCall) -> None:
            automation_id = _automation_id_from_call(call)
            if automation_id is None:
                return
            # Do not await the delete here. This handler is itself the last
            # action of the automation being deleted. Returning lets that
            # action script finish before the background task reloads HA's
            # automation component and unloads it.
            hass.async_create_task(
                _async_delete_automation_after_action(hass, automation_id),
                name=f"HomeIntent self-delete automation {automation_id}",
            )

        hass.services.async_register(DOMAIN, SERVICE_DELETE_AUTOMATION, _handle_delete_automation)
        _register_legacy_service(hass, SERVICE_DELETE_AUTOMATION, _handle_delete_automation)

    if not hass.services.has_service(DOMAIN, SERVICE_RECORD_AUTOMATION_RUN):

        async def _handle_record_automation_run(call: ServiceCall) -> None:
            automation_id = _automation_id_from_call(call)
            if automation_id is None:
                return
            hass.async_create_task(
                _async_record_automation_run_after_action(hass, automation_id),
                name=f"HomeIntent record automation run {automation_id}",
            )

        hass.services.async_register(
            DOMAIN, SERVICE_RECORD_AUTOMATION_RUN, _handle_record_automation_run
        )
        _register_legacy_service(
            hass, SERVICE_RECORD_AUTOMATION_RUN, _handle_record_automation_run
        )

    if not hass.services.has_service(DOMAIN, SERVICE_ENABLE_AUTOMATION):

        async def _handle_enable_automation(call: ServiceCall) -> None:
            automation_id = _automation_id_from_call(call)
            if automation_id is None:
                return
            hass.async_create_task(
                _async_enable_automation_after_action(hass, automation_id),
                name=f"HomeIntent resume automation {automation_id}",
            )

        hass.services.async_register(
            DOMAIN, SERVICE_ENABLE_AUTOMATION, _handle_enable_automation
        )
        _register_legacy_service(hass, SERVICE_ENABLE_AUTOMATION, _handle_enable_automation)

    if not hass.services.has_service(DOMAIN, SERVICE_PROACTIVE_MESSAGE):

        async def _handle_proactive_message(call: ServiceCall) -> None:
            try:
                await proactive_agent.async_signal(call.data)
            except ValueError as err:
                _LOGGER.warning("Invalid proactive message request: %s", err)

        hass.services.async_register(
            DOMAIN, SERVICE_PROACTIVE_MESSAGE, _handle_proactive_message
        )
        _register_legacy_service(hass, SERVICE_PROACTIVE_MESSAGE, _handle_proactive_message)

    if not hass.services.has_service(DOMAIN, SERVICE_RECHECK_AGENT_EVENT):

        async def _handle_recheck_agent_event(call: ServiceCall) -> None:
            event_id = call.data.get("event_id")
            if isinstance(event_id, str):
                await proactive_agent.async_recheck(event_id)

        hass.services.async_register(
            DOMAIN, SERVICE_RECHECK_AGENT_EVENT, _handle_recheck_agent_event
        )
        _register_legacy_service(
            hass, SERVICE_RECHECK_AGENT_EVENT, _handle_recheck_agent_event
        )

    if not hass.services.has_service(DOMAIN, SERVICE_THERMAL_DEADLINE_CHECKPOINT):

        async def _handle_thermal_deadline_checkpoint(call: ServiceCall) -> None:
            checkpoint = ThermalDeadlineCheckpoint.from_service_data(call.data)
            if checkpoint is None:
                _LOGGER.warning("Rejected invalid thermal deadline checkpoint")
                return
            from .hass_entities import build_entity_snapshots

            snapshots = tuple(build_entity_snapshots(hass, entry))
            now = datetime.now(timezone.utc)
            if not await thermal_checkpoints.async_consume(checkpoint, now=now):
                _LOGGER.warning("Rejected unauthenticated thermal deadline checkpoint")
                return
            result = await async_process_thermal_checkpoint(
                checkpoint, snapshots, thermal_tracker, goal_runs, now=now
            )
            proactive = entry.runtime_data.proactive_context
            if (
                proactive is not None
                and isinstance(result, ThermalCheckpointResult)
                and result.status is ThermalCheckpointStatus.LIKELY_LATE
            ):
                area_name = next(
                    (item.area_name for item in snapshots
                     if item.area_id == checkpoint.area_id and item.area_name),
                    checkpoint.area_id,
                )
                owner = next(
                    (run.user_id for run in await goal_runs.async_list()
                     if run.run_id == checkpoint.run_id),
                    None,
                )
                await proactive.async_report_thermal_risk(
                    area_id=checkpoint.area_id, area_name=area_name or checkpoint.area_id,
                    goal_id=checkpoint.goal_id, current=result.measured_celsius,
                    target=result.target_celsius, owner_user_id=owner,
                )
            if result is None and checkpoint.phase.value == "final":
                _LOGGER.warning(
                    "Thermal final checkpoint has no GoalRun for %s",
                    checkpoint.goal_id,
                )

        hass.services.async_register(
            DOMAIN,
            SERVICE_THERMAL_DEADLINE_CHECKPOINT,
            _handle_thermal_deadline_checkpoint,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_BIND_USER_CONTEXT):

        async def _handle_bind_user_context(call: ServiceCall) -> None:
            await _require_admin_service_call(hass, call)
            user_id = call.data.get("user_id")
            person_entity_id = call.data.get("person_entity_id")
            targets = call.data.get("notification_targets", ())
            service_targets = call.data.get("notification_services", ())
            preferred_target = call.data.get("preferred_notification_target")
            preferred_service = call.data.get("preferred_notification_service")
            if not isinstance(user_id, str) or not isinstance(person_entity_id, str):
                raise ValueError("user_id and person_entity_id are required")
            if not isinstance(targets, (list, tuple)) or any(
                not isinstance(item, str) for item in targets
            ):
                raise ValueError("notification_targets must be a list of notify.* ids")
            if not isinstance(service_targets, (list, tuple)) or any(
                not isinstance(item, str) for item in service_targets
            ):
                raise ValueError("notification_services must be a list of notify.* service ids")
            if hass.states.get(person_entity_id) is None or not person_entity_id.startswith("person."):
                raise ValueError("person_entity_id must reference an existing person.* entity")
            from .user_context import NotificationTarget, NotificationTargetKind

            entity_notification_targets = tuple(
                NotificationTarget(
                    item,
                    NotificationTargetKind.ENTITY,
                    preferred=(item == preferred_target),
                )
                for item in targets
                if item.startswith("notify.") and hass.states.get(item) is not None
            )
            if len(entity_notification_targets) != len(targets):
                raise ValueError("Every notification target must be an existing notify.* entity")
            service_notification_targets = tuple(
                NotificationTarget(
                    item,
                    NotificationTargetKind.SERVICE,
                    preferred=(item == preferred_service),
                )
                for item in service_targets
                if item.startswith("notify.")
                and hass.services.has_service("notify", item.partition(".")[2])
            )
            if len(service_notification_targets) != len(service_targets):
                raise ValueError("Every notification service must be an existing notify.* service")
            notification_targets = (
                *entity_notification_targets,
                *service_notification_targets,
            )
            preferred_values = tuple(
                item for item in (preferred_target, preferred_service) if item is not None
            )
            if any(
                not isinstance(item, str)
                or item not in {target.target_id for target in notification_targets}
                for item in preferred_values
            ) or len(preferred_values) > 1:
                raise ValueError("The preferred notification target must identify exactly one bound target")
            await user_contexts.async_set_user(
                user_id,
                person_entity_id=person_entity_id,
                notification_targets=notification_targets,
                confirmed=bool(call.data.get("confirmed", False)),
                allow_shared_person=bool(call.data.get("allow_shared_person", False)),
            )

        hass.services.async_register(
            DOMAIN, SERVICE_BIND_USER_CONTEXT, _handle_bind_user_context
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SET_HOUSEHOLD):

        async def _handle_set_household(call: ServiceCall) -> None:
            await _require_admin_service_call(hass, call)
            persons = call.data.get("person_entity_ids", ())
            if not isinstance(persons, (list, tuple)) or any(
                not isinstance(item, str) for item in persons
            ):
                raise ValueError("person_entity_ids must be a list")
            if any(hass.states.get(item) is None for item in persons):
                raise ValueError("Every household person must exist")
            await user_contexts.async_set_household(
                persons, confirmed=bool(call.data.get("confirmed", False))
            )

        hass.services.async_register(DOMAIN, SERVICE_SET_HOUSEHOLD, _handle_set_household)

    if not hass.services.has_service(DOMAIN, SERVICE_SAVE_ROUTINE):

        async def _handle_save_routine(call: ServiceCall) -> None:
            await _require_admin_service_call(hass, call)
            from .goal_model import DesiredState, GoalScope
            from .profiles import RoutineDefinition, RoutineStepDefinition

            routine_id = call.data.get("routine_id")
            name = call.data.get("name")
            owner_user_id = call.data.get("owner_user_id")
            raw_steps = call.data.get("steps", ())
            confirmed = bool(call.data.get("confirmed", False))
            if not all(isinstance(item, str) and item for item in (routine_id, name, owner_user_id)):
                raise ValueError("routine_id, name and owner_user_id are required")
            if not isinstance(raw_steps, (list, tuple)):
                raise ValueError("steps must be a list of typed routine steps")
            steps: list[RoutineStepDefinition] = []
            for index, raw in enumerate(raw_steps):
                if not isinstance(raw, dict):
                    raise ValueError("Each routine step must be an object")
                entity_id = raw.get("entity_id")
                property_name = raw.get("property")
                value = raw.get("value")
                if (
                    not isinstance(entity_id, str)
                    or hass.states.get(entity_id) is None
                    or property_name not in {"state", "brightness", "temperature"}
                    or not isinstance(value, (str, int, float, bool))
                ):
                    raise ValueError("Routine step target, property or value is invalid")
                steps.append(
                    RoutineStepDefinition(
                        str(raw.get("step_id") or f"step-{index + 1}"),
                        GoalScope(entity_ids=(entity_id,)),
                        DesiredState(str(property_name), value, _optional_text(raw.get("unit"))),
                        str(raw.get("description", "")),
                    )
                )
            await profile_store.async_save_routine(
                RoutineDefinition(
                    str(routine_id), str(name), str(owner_user_id), tuple(steps), confirmed
                ),
                confirmed=confirmed,
            )

        hass.services.async_register(DOMAIN, SERVICE_SAVE_ROUTINE, _handle_save_routine)

    if not hass.services.has_service(DOMAIN, SERVICE_SAVE_COMFORT_PROFILE):

        async def _handle_save_comfort_profile(call: ServiceCall) -> None:
            await _require_admin_service_call(hass, call)
            from .profiles import ComfortProfile

            profile_id = call.data.get("profile_id")
            owner_user_id = call.data.get("owner_user_id")
            area_id = call.data.get("area_id")
            confirmed = bool(call.data.get("confirmed", False))
            if not all(isinstance(item, str) and item for item in (profile_id, owner_user_id, area_id)):
                raise ValueError("profile_id, owner_user_id and area_id are required")
            await profile_store.async_save_comfort_profile(
                ComfortProfile(
                    str(profile_id), str(owner_user_id), str(area_id),
                    _optional_number(call.data.get("temperature_min")),
                    _optional_number(call.data.get("temperature_max")),
                    _optional_integer(call.data.get("brightness_min")),
                    _optional_integer(call.data.get("brightness_max")),
                    _optional_integer(call.data.get("color_temperature_kelvin")),
                    _optional_number(call.data.get("humidity_min")),
                    _optional_number(call.data.get("humidity_max")),
                    _optional_integer(call.data.get("cover_position")),
                    confirmed,
                ),
                confirmed=confirmed,
            )

        hass.services.async_register(
            DOMAIN, SERVICE_SAVE_COMFORT_PROFILE, _handle_save_comfort_profile
        )

    if not hass.services.has_service(DOMAIN, SERVICE_DELETE_MONITOR_GOAL):

        async def _handle_delete_monitor_goal(call: ServiceCall) -> None:
            await _require_admin_service_call(hass, call)
            goal_id = call.data.get("goal_id")
            if not isinstance(goal_id, str) or not goal_id:
                raise ValueError("goal_id is required")
            await monitor_goals.async_delete(goal_id)

        hass.services.async_register(
            DOMAIN, SERVICE_DELETE_MONITOR_GOAL, _handle_delete_monitor_goal
        )

    # Reconcile persistence before expiring missed one-shots. This recovers
    # a crash-interrupted transaction, removes orphan sidecar metadata and
    # repairs category assignments for every known HomeIntent automation.
    if hass.services.has_service("automation", "reload"):
        hass.async_create_task(
            _async_reconcile_and_cleanup_automations(hass),
            name="HomeIntent reconcile automation persistence",
        )
    # Recovery is a bounded atomic sidecar read and must finish before an
    # incoming notification action can address a persisted event.
    await proactive_agent.async_recover()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        try:
            from .learning_center_ws import async_unload_learning_center

            async_unload_learning_center(hass, entry)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("HomeIntent Learning Center could not be unregistered")
        if entry.runtime_data.remove_learning_listener is not None:
            entry.runtime_data.remove_learning_listener()
            entry.runtime_data.remove_learning_listener = None
        if entry.runtime_data.remove_proactive_listener is not None:
            entry.runtime_data.remove_proactive_listener()
            entry.runtime_data.remove_proactive_listener = None
        for task in tuple(entry.runtime_data.learning_tasks):
            task.cancel()
        if entry.runtime_data.learning_tasks:
            await asyncio.gather(
                *entry.runtime_data.learning_tasks, return_exceptions=True
            )
        entry.runtime_data.learning_tasks.clear()
        await entry.runtime_data.effect_monitor.async_close()
    if unloaded and hass.services.has_service(DOMAIN, SERVICE_DELETE_AUTOMATION):
        hass.services.async_remove(DOMAIN, SERVICE_DELETE_AUTOMATION)
    if unloaded and hass.services.has_service(DOMAIN, SERVICE_RECORD_AUTOMATION_RUN):
        hass.services.async_remove(DOMAIN, SERVICE_RECORD_AUTOMATION_RUN)
    if unloaded and hass.services.has_service(DOMAIN, SERVICE_ENABLE_AUTOMATION):
        hass.services.async_remove(DOMAIN, SERVICE_ENABLE_AUTOMATION)
    if unloaded and hass.services.has_service(DOMAIN, SERVICE_PROACTIVE_MESSAGE):
        hass.services.async_remove(DOMAIN, SERVICE_PROACTIVE_MESSAGE)
    if unloaded and hass.services.has_service(DOMAIN, SERVICE_RECHECK_AGENT_EVENT):
        hass.services.async_remove(DOMAIN, SERVICE_RECHECK_AGENT_EVENT)
    if unloaded and hass.services.has_service(
        DOMAIN, SERVICE_THERMAL_DEADLINE_CHECKPOINT
    ):
        hass.services.async_remove(DOMAIN, SERVICE_THERMAL_DEADLINE_CHECKPOINT)
    if unloaded:
        for service in (
            SERVICE_BIND_USER_CONTEXT,
            SERVICE_SET_HOUSEHOLD,
            SERVICE_SAVE_ROUTINE,
            SERVICE_SAVE_COMFORT_PROFILE,
            SERVICE_DELETE_MONITOR_GOAL,
        ):
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
    if unloaded:
        for service in (
            SERVICE_DELETE_AUTOMATION,
            SERVICE_RECORD_AUTOMATION_RUN,
            SERVICE_ENABLE_AUTOMATION,
            SERVICE_PROACTIVE_MESSAGE,
            SERVICE_RECHECK_AGENT_EVENT,
            SERVICE_BIND_USER_CONTEXT,
            SERVICE_SET_HOUSEHOLD,
        ):
            if hass.services.has_service(LEGACY_DOMAIN, service):
                hass.services.async_remove(LEGACY_DOMAIN, service)
    return unloaded


FIXED_SELECTION_ISSUE = "fixed_entity_selection"


def _async_check_fixed_selection(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Report a fixed entity selection that hides Assist-exposed entities (F6).

    HomeIntent 7.1.2 pre-filled the options form with the current exposure,
    so saving any option silently froze it. That cannot be told apart from a
    deliberate choice, so it is not rewritten; a repair issue names the
    missing entities and how to return to the dynamic selection instead.
    """
    from homeassistant.helpers import issue_registry as ir

    from .hass_entities import fixed_selection_missing_exposed

    missing = fixed_selection_missing_exposed(hass, entry)
    if not missing:
        ir.async_delete_issue(hass, DOMAIN, FIXED_SELECTION_ISSUE)
        return
    _LOGGER.warning(
        "HomeIntent uses a fixed entity selection; %d entities exposed to Assist "
        "are not visible to HomeIntent (e.g. %s)", len(missing), ", ".join(missing[:5]),
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        FIXED_SELECTION_ISSUE,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=FIXED_SELECTION_ISSUE,
        translation_placeholders={
            "count": str(len(missing)),
            "examples": ", ".join(missing[:5]),
        },
    )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_delete_automation(hass: HomeAssistant, call: ServiceCall) -> None:
    """Deletes the automation named by ``call.data["automation_id"]`` -
    reuses ``AutomationExecutor.async_delete_automation()`` (Wave 10)
    verbatim (Regel 6), rather than a second, parallel deletion mechanism.

    Manual validation (no voluptuous schema): ``vol``/``config_validation``
    aren't exercised anywhere else in this codebase's test suite (only
    imported, untested, by ``config_flow.py``) - keeping this handler pure
    Python keeps it directly testable the same way the rest of this
    integration already is.

    A fresh ``AutomationExecutor`` is created per call rather than sharing
    the conversation entity's own instance. Executor instances belonging to
    the same Home Assistant instance share their mutation lock and additionally
    protect the file with optimistic fingerprint checks.

    Swallows the documented ``ValueError`` race (see
    ``async_delete_automation``'s own docstring): if the automation was
    already deleted by something else between generation and this call
    firing, there is nothing left to do.
    """
    automation_id = _automation_id_from_call(call)
    if automation_id is None:
        return
    await _async_delete_automation_by_id(hass, automation_id)


def _automation_id_from_call(call: ServiceCall) -> str | None:
    """Return a validated automation id from the custom service call."""
    automation_id = call.data.get("automation_id")
    if not isinstance(automation_id, str) or not automation_id:
        _LOGGER.warning("homeintent.delete_automation called without a valid automation_id: %r", automation_id)
        return None
    return automation_id


async def _async_delete_automation_after_action(
    hass: HomeAssistant, automation_id: str
) -> None:
    """Delete after the calling automation has left its final action."""
    delays = (SELF_DELETE_GRACE_SECONDS, *SELF_DELETE_RETRY_SECONDS)
    for attempt, delay in enumerate(delays, start=1):
        await asyncio.sleep(delay)
        try:
            await _async_delete_automation_by_id(hass, automation_id)
            return
        except Exception:  # noqa: BLE001 - HA reload/file failures are heterogeneous
            if attempt == len(delays):
                _LOGGER.exception(
                    "Self-delete of automation %s failed after %d attempts",
                    automation_id,
                    attempt,
                )
            else:
                _LOGGER.warning(
                    "Self-delete attempt %d for automation %s failed; retrying",
                    attempt,
                    automation_id,
                    exc_info=True,
                )


async def _async_record_automation_run_after_action(
    hass: HomeAssistant, automation_id: str
) -> None:
    """Record a successful run after its service action has returned."""
    delays = (SELF_DELETE_GRACE_SECONDS, *SELF_DELETE_RETRY_SECONDS)
    for attempt, delay in enumerate(delays, start=1):
        await asyncio.sleep(delay)
        try:
            from .automation_executor import AutomationExecutor

            await AutomationExecutor(hass).async_record_automation_run(automation_id)
            return
        except Exception:  # noqa: BLE001 - HA reload/file failures are heterogeneous
            if attempt == len(delays):
                _LOGGER.exception(
                    "Recording run of automation %s failed after %d attempts",
                    automation_id,
                    attempt,
                )
            else:
                _LOGGER.warning(
                    "Recording run attempt %d for automation %s failed; retrying",
                    attempt,
                    automation_id,
                    exc_info=True,
                )


async def _async_enable_automation_after_action(
    hass: HomeAssistant, automation_id: str
) -> None:
    """Enable after the calling automation action has returned."""
    await asyncio.sleep(SELF_DELETE_GRACE_SECONDS)
    try:
        from .automation_executor import AutomationExecutor

        await AutomationExecutor(hass).async_enable_automation(automation_id)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Resuming automation %s failed", automation_id)


async def _async_cleanup_expired_scheduled_automations(
    hass: HomeAssistant,
) -> None:
    """Reconcile date-bound one-shots after Home Assistant startup."""
    from homeassistant.util import dt as dt_util

    from .automation_executor import AutomationExecutor

    try:
        expired = await AutomationExecutor(
            hass
        ).async_cleanup_expired_scheduled_automations(dt_util.now())
    except Exception:  # noqa: BLE001 - startup cleanup must not unload HomeIntent
        _LOGGER.exception("Cleanup of expired HomeIntent automations failed")
        return
    if expired:
        _LOGGER.info("Removed %d expired HomeIntent automations", len(expired))


async def _async_reconcile_and_cleanup_automations(hass: HomeAssistant) -> None:
    """Repair durable state first, then apply the missed-schedule policy."""
    from .automation_executor import AutomationExecutor

    executor = AutomationExecutor(hass)
    try:
        recovered = await executor.async_recover_incomplete_transaction()
        orphan_metadata = await executor.async_reconcile_homeintent_state()
    except Exception:  # noqa: BLE001 - startup recovery must not unload HomeIntent
        _LOGGER.exception("HomeIntent automation persistence reconciliation failed")
        return
    if recovered:
        _LOGGER.warning("Recovered an interrupted HomeIntent automation transaction")
    if orphan_metadata:
        _LOGGER.info(
            "Removed %d orphan HomeIntent automation metadata entries",
            len(orphan_metadata),
        )
    await _async_cleanup_expired_scheduled_automations(hass)


async def _async_delete_automation_by_id(
    hass: HomeAssistant, automation_id: str
) -> None:
    """Delete one automation immediately; callers decide when it is safe."""
    # Function-local import: automation_executor.py imports the real
    # ``homeassistant.core``/``homeassistant.util`` at module level (no
    # HA-free path exists there, see its own module docstring) - keeping
    # that import out of this module's top level is what lets every
    # existing ``homeintent.engine``-only unit test keep importing bare
    # ``homeintent.*`` (which always runs this package's ``__init__.py`` first)
    # without the real ``homeassistant`` package installed, exactly as
    # before this wave.
    from .automation_executor import AutomationExecutor

    executor = AutomationExecutor(hass)
    try:
        await executor.async_delete_automation(automation_id)
    except ValueError:
        _LOGGER.debug("homeintent.delete_automation: automation %s already gone", automation_id)


def _storage_path(hass: HomeAssistant, current: str, legacy: str) -> str:
    """Resolve a canonical .storage path with one lossless legacy adoption."""
    return resolve_storage_path(hass, f".storage/{current}", f".storage/{legacy}")


def _register_legacy_service(hass: HomeAssistant, service: str, handler) -> None:
    """Forward old service calls while emitting a removable deprecation path."""
    if hass.services.has_service(LEGACY_DOMAIN, service):
        return

    async def _deprecated(call: ServiceCall) -> None:
        _LOGGER.warning(
            "Service %s.%s is deprecated; migrate this automation to %s.%s",
            LEGACY_DOMAIN,
            service,
            DOMAIN,
            service,
        )
        await handler(call)

    hass.services.async_register(LEGACY_DOMAIN, service, _deprecated)


async def _require_admin_service_call(hass: HomeAssistant, call: ServiceCall) -> None:
    user_id = getattr(getattr(call, "context", None), "user_id", None)
    auth = getattr(hass, "auth", None)
    get_user = getattr(auth, "async_get_user", None)
    user = await get_user(user_id) if get_user is not None and user_id is not None else None
    if user is None or not bool(getattr(user, "is_admin", False)):
        raise PermissionError("HomeIntent binding services require an authenticated administrator")


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _optional_integer(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None
