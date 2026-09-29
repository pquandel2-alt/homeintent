"""Goals, plans and routines of the V10 goal model (7.7, B4: from ``conversation.py``).

A goal ("Mach das Wohnzimmer bis 18 Uhr warm", "Bereite den Filmabend
vor") becomes a bounded, previewed plan; nothing runs without the plan's
confirmation, and every step passes the execution policy.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import (
    Any,
    Callable,
    Protocol,
    Sequence,
)

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from ..adaptive_planning import AdaptivePlanningAdvice, advise_deadline_goal
from ..areas import AreaSnapshot
from ..audit_log import AuditTrail
from ..automation_executor import AutomationExecutor
from ..automation_management import parse_automation_management
from ..dialog_manager import DialogPriority, DialogTaskKind
from ..effect_graph import build_plan_effects
from ..engine import CommandPlan, MatchResult, NluEngine
from ..entities import EntitySnapshot, normalize_for_compare
from ..execution_context import call_context
from ..execution_policy import evaluate_service_plan, PolicyOutcome
from ..goal_intent import interpret_goal
from ..goal_model import (
    DesiredState,
    GoalKind as V10GoalKind,
    GoalScope,
    GoalSemanticChoice,
    PendingGoalSemanticClarification,
    TemporalGoal,
)
from ..goal_run import (
    explain_goal_run,
    GoalRun,
    GoalRunClarification,
    GoalRunQuery,
)
from ..history_query import async_get_transition_evidence
from ..memory import MemoryKind
from ..monitor_goal import MonitorRecord
from ..nlu.action_model import ActionModel, ActionType
from ..nlu.automation_confirmation import classify_confirmation_reply, ConfirmationReply
from ..nlu.automation_model import (
    AutomationModel,
    TriggerModel,
    TriggerTarget,
    TriggerType,
)
from ..nlu.automation_validator import validate_automation
from ..nlu.ha_automation_generator import generate_ha_automation_config
from ..nlu.language_frontend import analyse_language, LanguageDocument
from ..nlu.primitives import SemanticProperty
from ..nlu.temporal_semantics import resolve_history_window, resolve_scheduled_datetime
from ..nlu.unit_reasoning import normalize_measurement
from ..plan_modification import apply_plan_modification
from ..planner import (
    effect_satisfied,
    Goal,
    goal_run_from_plan_result,
    GoalKind,
    materialize_comfort_profile,
    materialize_goal,
    materialize_routine,
    MaterializedPlan,
    PlanExecutor,
    PlanResult,
    PlanStatus,
    StepKind,
)
from ..predictive_house_model import PredictiveHouseModel
from ..profiles import RoutineDefinition, RoutineStepDefinition
from ..security_control import conversation_user_id, user_is_admin
from ..service_call import ServiceCallPlan
from ..service_executor import async_execute_service_plan
from ..thermal_deadline import (
    append_start_checkpoint,
    checkpoint_automation_config,
    PendingThermalCheckpointStore,
    ThermalCheckpointPhase,
    ThermalDeadlineCheckpoint,
)
from ..user_context import BindingStatus
from ..world_model import WorldModel

_LOGGER = logging.getLogger(__name__)


class GoalRuntime(Protocol):
    """Runtime services of goals, plans and their reports."""

    dialog_manager: Any
    effect_monitor: Any
    execution_coordinator: Any
    goal_runs: Any | None
    memory: Any | None
    monitor_goals: Any | None
    predictive_house: Any | None
    proactive_context: Any | None
    profiles: Any | None
    thermal_checkpoints: Any | None
    user_contexts: Any | None


# How long a spoken plan confirmation may wait for the plan's verified
# result before replying and continuing in the background (F17).
_PLAN_REPLY_BUDGET_SECONDS = 2.0


def _desired_state_from_service_plan(plan: ServiceCallPlan) -> DesiredState | None:
    if plan.service in {"turn_on", "turn_off"}:
        brightness = plan.data.get("brightness_pct")
        if plan.service == "turn_on" and isinstance(brightness, (int, float)):
            return DesiredState("brightness", float(brightness), "%")
        return DesiredState("state", "on" if plan.service == "turn_on" else "off")
    if plan.service == "close_cover":
        return DesiredState("state", "closed")
    if plan.service == "lock":
        return DesiredState("state", "locked")
    if plan.service == "set_temperature":
        temperature = plan.data.get("temperature")
        if isinstance(temperature, (int, float)):
            return DesiredState("temperature", float(temperature), "°C")
    if plan.service == "set_cover_position":
        position = plan.data.get("position")
        if isinstance(position, (int, float)):
            return DesiredState("position", float(position), "%")
    return None



@dataclass(frozen=True)
class _ScheduledOutcome:
    executed: bool
    error: str | None = None


def _german_count(value: int) -> str:
    return {2: "zwei", 3: "drei"}.get(value, str(value))


def _german_goal_labels(labels: Sequence[str]) -> str:
    values = tuple(dict.fromkeys(labels))
    if not values:
        return "keine benannten Ziele"
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + " und " + values[-1]


def _goal_run_label(run: GoalRun) -> str:
    if run.goal.routine_id:
        return run.goal.routine_id.replace("_", " ").capitalize()
    labels = {
        V10GoalKind.MONITOR_AND_NOTIFY: "Monitor-Ziel",
        V10GoalKind.SCHEDULED: "terminiertes Ziel",
        V10GoalKind.COMFORT: "Komfortziel",
    }
    return labels.get(run.goal.kind, run.source_utterance.strip() or run.goal.kind.value)


def _goal_semantic_choice(document: LanguageDocument) -> GoalSemanticChoice | None:
    words = frozenset(token.canonical for token in document.tokens if token.is_word)
    normalized = document.normalized_text.casefold()
    if words & {"ersteres", "erstes"}:
        return GoalSemanticChoice.SETPOINT_AT_TIME
    if words & {"zweiteres", "zweites"}:
        return GoalSemanticChoice.ACHIEVE_BY_DEADLINE
    setpoint = bool(words & {"sollwert", "einstellen", "setz", "setzen", "stell"}) and bool(
        words & {"dann", "zeitpunkt", "uhr", "einfach"}
    )
    achieved = bool(words & {"erreicht", "warm", "sein", "haben"}) and bool(
        words & {"bis", "dahin", "schon"}
    )
    if setpoint and not achieved:
        return GoalSemanticChoice.SETPOINT_AT_TIME
    if achieved and not setpoint:
        return GoalSemanticChoice.ACHIEVE_BY_DEADLINE
    if "zum zeitpunkt" in normalized:
        return GoalSemanticChoice.SETPOINT_AT_TIME
    return None


def _mentioned_goal_run_entity(
    document: LanguageDocument, entities: Sequence[EntitySnapshot]
) -> str | None:
    normalized = normalize_for_compare(document.source_text)
    matches = {
        entity.entity_id
        for entity in entities
        if any(
            candidate and candidate in normalized
            for candidate in (
                normalize_for_compare(entity.friendly_name),
                normalize_for_compare(entity.entity_id.partition(".")[2]),
            )
        )
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _occasion_phrase(routine_id: str) -> str:
    """"beim Schlafengehen" - the occasion with its preposition (7.6.1)."""
    return _OCCASION_PHRASES.get(routine_id, f"bei „{routine_id}“")


_OCCASION_PHRASES = {
    "schlafengehen": "beim Schlafengehen",
    "filmabend": "beim Filmabend",
    "abwesenheit": "bei Abwesenheit",
}


def _routine_definition_from_payload(
    routine_id: str,
    owner_user_id: str,
    payload: object,
    entities: list[EntitySnapshot],
) -> RoutineDefinition | None:
    """Convert already validated V8 actions into a non-executable routine draft."""
    matches = (
        (payload,)
        if isinstance(payload, MatchResult)
        else payload.commands
        if isinstance(payload, CommandPlan)
        else ()
    )
    plans = [item.plan for item in matches if item.plan is not None]
    if not plans or len(plans) != len(matches):
        return None
    by_id = {item.entity_id: item for item in entities}
    steps: list[RoutineStepDefinition] = []
    for plan in plans:
        desired = _desired_state_from_service_plan(plan)
        if desired is None:
            return None
        raw_targets = (
            (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
        )
        for entity_id in raw_targets:
            entity = by_id.get(entity_id)
            if entity is None:
                return None
            steps.append(
                RoutineStepDefinition(
                    f"definition-{len(steps) + 1}",
                    GoalScope(entity_ids=(entity_id,)),
                    desired,
                    f"{entity.friendly_name}: {desired.property_name} = {desired.value}",
                )
            )
    return RoutineDefinition(
        routine_id,
        routine_id.replace("_", " ").capitalize(),
        owner_user_id,
        tuple(steps),
        False,
    )


def _scheduled_action_model(plan: ServiceCallPlan) -> ActionModel | None:
    """Lift a closed service plan into the existing typed automation model."""
    entity_ids = (
        (plan.entity_id,)
        if isinstance(plan.entity_id, str)
        else tuple(plan.entity_id)
    )
    if not entity_ids:
        return None
    target = TriggerTarget(
        domain=entity_ids[0].partition(".")[0],
        entity_id=entity_ids[0] if len(entity_ids) == 1 else None,
        entity_ids=entity_ids if len(entity_ids) > 1 else (),
    )
    if plan.service == "set_temperature" and plan.domain == "climate":
        value = plan.data.get("temperature")
        if isinstance(value, (int, float)):
            return ActionModel(ActionType.SET_TEMPERATURE, target=target, value=float(value))
    if plan.service in {"turn_on", "turn_off"} and plan.domain == "homeassistant":
        return ActionModel(
            ActionType.TURN_ON if plan.service == "turn_on" else ActionType.TURN_OFF,
            target=target,
        )
    return None


def _select_goal_run_reply(
    document: LanguageDocument,
    run_ids: Sequence[str],
    labels: Sequence[str],
) -> str | None:
    words = frozenset(token.canonical for token in document.tokens if token.is_word)
    if words & {"erstes", "ersteres", "erste"} and run_ids:
        return run_ids[0]
    if words & {"zweites", "zweiteres", "zweite"} and len(run_ids) >= 2:
        return run_ids[1]
    normalized = normalize_for_compare(document.source_text)
    matches = {
        run_id
        for run_id, label in zip(run_ids, labels, strict=True)
        if normalize_for_compare(label) in normalized
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _thermal_advice_for_goal(
    goal: Goal,
    entities: Sequence[EntitySnapshot],
    predictive_house: PredictiveHouseModel | None,
) -> AdaptivePlanningAdvice | None:
    """Use only an installed model's exact confirmed measurement binding."""
    if predictive_house is None or goal.scope.area_id is None or not goal.desired_states:
        return None
    model = predictive_house.thermal_model(goal.scope.area_id)
    if model is None or not model.binding.confirmed:
        return None
    by_id = {item.entity_id: item for item in entities}
    temperature = by_id.get(model.binding.temperature_entity_id)
    if temperature is None or temperature.state in {"unknown", "unavailable"}:
        return None
    try:
        raw_current = float(temperature.state)
    except ValueError:
        return None
    normalized_current = normalize_measurement(
        raw_current, temperature.unit, SemanticProperty.TEMPERATURE
    )
    if normalized_current is None:
        return None
    desired = goal.desired_states[0]
    if desired.property_name != "temperature" or not isinstance(desired.value, (int, float)):
        return None
    normalized_target = normalize_measurement(
        float(desired.value), desired.unit, SemanticProperty.TEMPERATURE
    )
    if normalized_target is None:
        return None
    outdoor_value: float | None = None
    outdoor_id = model.binding.outdoor_temperature_entity_id
    if outdoor_id is not None:
        outdoor = by_id.get(outdoor_id)
        if outdoor is None or outdoor.state in {"unknown", "unavailable"}:
            return None
        try:
            raw_outdoor = float(outdoor.state)
        except ValueError:
            return None
        normalized_outdoor = normalize_measurement(
            raw_outdoor, outdoor.unit, SemanticProperty.TEMPERATURE
        )
        if normalized_outdoor is None:
            return None
        outdoor_value = normalized_outdoor.value
    prediction = predictive_house.predict_thermal(
        goal.scope.area_id, current_celsius=normalized_current.value,
        target_celsius=normalized_target.value, outdoor_celsius=outdoor_value,
        now=dt_util.now(),
    )
    return advise_deadline_goal(goal, prediction)


class GoalController:
    """Handles goal turns, plan confirmations and background plan reports."""

    def __init__(
        self,
        *,
        hass: Callable[[], HomeAssistant],
        entry: ConfigEntry,
        engine: NluEngine,
        world_model: Callable[[], WorldModel | None],
        executor: Callable[[], AutomationExecutor],
        audit_trail: AuditTrail,
        runtime: GoalRuntime,
        entities: Callable[[], list[EntitySnapshot]],
        conversation_area: Callable[[Any], AreaSnapshot | None],
    ) -> None:
        self._hass = hass
        self.entry = entry
        self._engine = engine
        self._world_model_of = world_model
        self._executor = executor
        self._audit_trail = audit_trail
        self._runtime = runtime
        # The exposed entities and the speaker's area, read fresh per call.
        self._entities = entities
        self._conversation_area = conversation_area

    @property
    def hass(self) -> HomeAssistant:
        return self._hass()

    @property
    def _world_model(self) -> WorldModel | None:
        return self._world_model_of()

    def _automation_store(self) -> AutomationExecutor:
        return self._executor()

    async def async_handle_goal_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        language_document: LanguageDocument,
        entities: list[EntitySnapshot],
        direct_understanding: object | None = None,
    ) -> conversation.ConversationResult | None:
        manager = self._runtime.dialog_manager
        conversation_id = user_input.conversation_id
        active = manager.active(conversation_id)
        if (
            active is not None
            and active.kind is DialogTaskKind.GOAL_RUN_CLARIFICATION
            and isinstance(active.payload, GoalRunClarification)
        ):
            actor_id = conversation_user_id(user_input)
            pending_runs = active.payload
            if pending_runs.requested_by_user_id != actor_id:
                response.async_set_speech("Diese Verlaufs-Rückfrage gehört zu einem anderen Benutzer.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            selected_id = _select_goal_run_reply(
                language_document, pending_runs.run_ids, pending_runs.labels
            )
            if selected_id is None:
                response.async_set_speech(
                    "Bitte nenne eines der Ziele: " + _german_goal_labels(pending_runs.labels) + "."
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            store = self._runtime.goal_runs
            matches = (
                await store.async_query(GoalRunQuery(user_id=actor_id, run_id=selected_id))
                if store is not None
                else ()
            )
            manager.cancel(conversation_id, active.task_id)
            response.async_set_speech(explain_goal_run(matches[-1] if matches else None).message)
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        if (
            active is not None
            and active.kind is DialogTaskKind.GOAL_SEMANTIC_CLARIFICATION
            and isinstance(active.payload, PendingGoalSemanticClarification)
        ):
            actor_id = conversation_user_id(user_input)
            pending_goal = active.payload
            if pending_goal.requested_by_user_id != actor_id:
                response.async_set_speech("Diese Ziel-Rückfrage gehört zu einem anderen Benutzer.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            choice = _goal_semantic_choice(language_document)
            if choice is None or choice not in pending_goal.choices:
                response.async_set_speech(
                    "Bitte wähle eindeutig: Sollwert zum Zeitpunkt setzen oder bis dahin erreichen."
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            manager.cancel(conversation_id, active.task_id)
            if choice is GoalSemanticChoice.ACHIEVE_BY_DEADLINE:
                original_document = analyse_language(
                    pending_goal.goal.provenance.source_utterance, entities
                )
                deadline = resolve_scheduled_datetime(
                    original_document.temporal, dt_util.now()
                )
                goal_for_advice = (
                    replace(
                        pending_goal.goal,
                        temporal=replace(pending_goal.goal.temporal, deadline=deadline),
                    )
                    if pending_goal.goal.temporal is not None and deadline is not None
                    else pending_goal.goal
                )
                advice = _thermal_advice_for_goal(
                    goal_for_advice, entities, self._runtime.predictive_house
                )
                if advice is None:
                    response.async_set_speech(
                        "Für dieses Ergebnisziel fehlt mir ein ausreichend validiertes thermisches Modell. "
                        "Ich kann den Sollwert zu einem festen Zeitpunkt setzen oder du sammelst weitere belegte Heizvorgänge."
                    )
                    return conversation.ConversationResult(response=response, conversation_id=conversation_id)
                try:
                    plan = materialize_goal(
                        goal_for_advice, entities, options=self.entry.options,
                        is_admin=await user_is_admin(self.hass, user_input),
                        user_id=actor_id, adaptive_advice=advice,
                    )
                except (PermissionError, ValueError) as err:
                    response.async_set_speech(f"Ich kann dafür keinen sicheren Plan erstellen: {err}")
                    return conversation.ConversationResult(response=response, conversation_id=conversation_id)
                return self.stage_plan(user_input, response, plan, actor_id)
            original_document = analyse_language(
                pending_goal.goal.provenance.source_utterance, entities
            )
            scheduled_for = resolve_scheduled_datetime(
                original_document.temporal, dt_util.now()
            )
            if scheduled_for is None:
                response.async_set_speech(
                    "Der geplante Zeitpunkt ist nicht mehr vollständig. Ich habe nichts geplant."
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            clarified = replace(
                pending_goal.goal,
                temporal=TemporalGoal(
                    execute_at=scheduled_for,
                    day_part=scheduled_for.strftime("%Y-%m-%d %H:%M"),
                    must_be_achieved_by_deadline=False,
                ),
                failure_handling="report",
            )
            try:
                plan = materialize_goal(
                    clarified,
                    entities,
                    options=self.entry.options,
                    is_admin=await user_is_admin(self.hass, user_input),
                    user_id=actor_id,
                )
            except (PermissionError, ValueError) as err:
                response.async_set_speech(f"Ich kann dafür keinen sicheren Plan erstellen: {err}")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            return self.stage_plan(user_input, response, plan, actor_id)

        if active is not None and active.kind is DialogTaskKind.ROUTINE_DEFINITION:
            actor_id = conversation_user_id(user_input)
            if active.requested_by_user_id != actor_id:
                response.async_set_speech("Diese Routinen-Definition gehört zu einem anderen Benutzer.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            draft = active.slots.get("routine")
            if (
                not isinstance(draft, RoutineDefinition)
                and classify_confirmation_reply(language_document.source_text)
                is ConfirmationReply.NO
            ):
                manager.cancel(conversation_id)
                response.async_set_speech("In Ordnung. Ich lege keine Routine an.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            if isinstance(draft, RoutineDefinition):
                reply = classify_confirmation_reply(language_document.source_text)
                if reply is ConfirmationReply.NO:
                    manager.cancel(conversation_id)
                    response.async_set_speech("In Ordnung. Die Routine wurde nicht gespeichert.")
                elif reply is not ConfirmationReply.YES:
                    response.async_set_speech("Bitte bestätige die gezeigte Routine eindeutig mit Ja oder Nein.")
                elif self._runtime.profiles is None:
                    manager.cancel(conversation_id)
                    response.async_set_speech("Die lokale Profil-Persistenz ist nicht verfügbar.")
                else:
                    confirmed_routine = replace(draft, confirmed=True)
                    await self._runtime.profiles.async_save_routine(
                        confirmed_routine, confirmed=True
                    )
                    manager.cancel(conversation_id)
                    response.async_set_speech(
                        f"Gespeichert. Die Routine {confirmed_routine.name} enthält "
                        f"{len(confirmed_routine.steps)} bestätigte Schritte."
                    )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)

            understood = direct_understanding
            if understood is None:
                understood = self._engine.understand(
                    user_input.text,
                    entities,
                    self._world_model,
                    language_document,
                )
            payload = getattr(understood, "payload", None)
            routine_id = active.slots.get("routine_id")
            if not isinstance(routine_id, str) or actor_id is None:
                manager.cancel(conversation_id)
                response.async_set_speech("Die Routinen-Definition ist nicht mehr vollständig.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            routine = _routine_definition_from_payload(
                routine_id, actor_id, payload, entities
            )
            if routine is None:
                response.async_set_speech(
                    "Ich konnte daraus keine vollständige Folge unterstützter Gerätezustände bilden. Bitte nenne konkrete Geräte und Zielzustände."
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            manager.create(
                conversation_id,
                active.task_id,
                DialogTaskKind.ROUTINE_DEFINITION,
                DialogPriority.CONFIRMATION,
                slots={"routine_id": routine_id, "routine": routine},
                reason="Eine typisierte Routinen-Definition wartet auf ausdrückliche Bestätigung.",
                requested_by_user_id=actor_id,
            )
            preview = "; ".join(step.description for step in routine.steps)
            response.async_set_speech(
                f"Als Routine {routine.name} habe ich verstanden: {preview}. Soll ich diese Definition lokal speichern?"
            )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        if active is not None and active.kind is DialogTaskKind.PLAN_CONFIRMATION:
            actor_id = conversation_user_id(user_input)
            if active.requested_by_user_id is not None and active.requested_by_user_id != actor_id:
                response.async_set_speech("Diese Planbestätigung gehört zu einem anderen Benutzer.")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            pending_plan = active.slots.get("plan")
            if isinstance(pending_plan, MaterializedPlan):
                modified = apply_plan_modification(
                    pending_plan, language_document, entities
                )
                if modified is not None:
                    manager.create(
                        conversation_id,
                        active.task_id,
                        DialogTaskKind.PLAN_CONFIRMATION,
                        DialogPriority.CONFIRMATION,
                        slots={"plan": modified},
                        reason="Der strukturierte Plan wurde geändert und wartet erneut auf Bestätigung.",
                        requested_by_user_id=actor_id,
                    )
                    actions = [
                        step.description for step in modified.steps
                        if step.kind is StepKind.ACTION
                    ]
                    response.async_set_speech(
                        f"Plan angepasst: {'; '.join(actions[:3]) or 'keine sofortige Aktion'}. Soll ich diesen Plan ausführen?"
                    )
                    return conversation.ConversationResult(
                        response=response, conversation_id=conversation_id
                    )
            reply = classify_confirmation_reply(language_document.source_text)
            if reply is ConfirmationReply.NO:
                manager.cancel(conversation_id)
                response.async_set_speech("In Ordnung. Der Plan wurde nicht ausgeführt.")
            elif reply is ConfirmationReply.UNCLEAR:
                if len(language_document.tokens) > 3:
                    manager.cancel(conversation_id)
                    return None
                response.async_set_speech("Bitte bestätige den gesamten Plan eindeutig mit Ja oder Nein.")
            else:
                stored_plan = active.slots.get("plan")
                if not isinstance(stored_plan, MaterializedPlan):
                    manager.cancel(conversation_id)
                    response.async_set_speech("Der Plan ist nicht mehr vollständig. Ich habe nichts ausgeführt.")
                else:
                    is_admin = await user_is_admin(self.hass, user_input)

                    async def refresh() -> list[EntitySnapshot]:
                        return self._entities()

                    async def execute(plan, fresh, confirmed):
                        return await async_execute_service_plan(
                            self.hass,
                            plan,
                            fresh,
                            self.entry.options,
                            is_admin=is_admin,
                            user_id=actor_id,
                            confirmed=confirmed,
                            audit_trail=self._audit_trail,
                            audit_actor_id=actor_id or "voice",
                            effect_monitor=self._runtime.effect_monitor,
                        )

                    async def verify(entity_id: str, expected: str) -> bool:
                        loop = asyncio.get_running_loop()
                        deadline = loop.time() + min(
                            10.0,
                            self._runtime.effect_monitor.timeout.total_seconds(),
                        )
                        while True:
                            current = next(
                                (
                                    item
                                    for item in self._entities()
                                    if item.entity_id == entity_id
                                ),
                                None,
                            )
                            if current is not None and effect_satisfied(current, expected):
                                return True
                            remaining = deadline - loop.time()
                            if remaining <= 0:
                                return False
                            await asyncio.sleep(min(0.1, remaining))

                    async def schedule(step, fresh, confirmed):
                        action = step.action
                        if action is None or step.execute_at_local_time is None:
                            return _ScheduledOutcome(False, "Der terminierte Schritt ist unvollständig.")
                        decision = evaluate_service_plan(
                            action,
                            fresh,
                            self.entry.options,
                            is_admin=is_admin,
                            user_id=actor_id,
                            effects=build_plan_effects(self.hass, action),
                            attended=False,
                        )
                        if decision.outcome is PolicyOutcome.DENY:
                            return _ScheduledOutcome(False, decision.reason)
                        if decision.outcome is PolicyOutcome.CONFIRM and not confirmed:
                            return _ScheduledOutcome(False, "Die Aktion benötigt eine Bestätigung.")
                        automation_action = _scheduled_action_model(action)
                        if automation_action is None:
                            return _ScheduledOutcome(False, "Für diese Aktion gibt es keinen geschlossenen Zeitplan-Operator.")
                        try:
                            hour_text, minute_text = step.execute_at_local_time.split(":", 1)
                            hour, minute = int(hour_text), int(minute_text)
                        except (TypeError, ValueError):
                            return _ScheduledOutcome(False, "Der geplante Zeitpunkt ist ungültig.")
                        now = dt_util.now()
                        scheduled_for = step.scheduled_for
                        if scheduled_for is None:
                            scheduled_for = now.replace(
                                hour=hour, minute=minute, second=0, microsecond=0
                            )
                            if scheduled_for <= now:
                                scheduled_for += timedelta(days=1)
                        elif scheduled_for <= now:
                            return _ScheduledOutcome(False, "Der geplante Zeitpunkt liegt bereits in der Vergangenheit.")
                        automation_id = uuid.uuid4().hex
                        model = AutomationModel(
                            triggers=(TriggerModel(
                                TriggerType.TIME,
                                time_hour=hour,
                                time_minute=minute,
                                time_second=0,
                            ),),
                            actions=(automation_action,),
                            source_text=(
                                f"HomeIntent: {step.description} um "
                                f"{step.execute_at_local_time} Uhr"
                            ),
                            once=True,
                            scheduled_for=scheduled_for,
                        )
                        validation_error = validate_automation(model)
                        if validation_error is not None:
                            return _ScheduledOutcome(
                                False,
                                f"Der terminierte Schritt ist nicht sicher: {validation_error.name}",
                            )
                        generation = generate_ha_automation_config(
                            model, fresh, automation_id=automation_id
                        )
                        if generation.error is not None or generation.config is None:
                            return _ScheduledOutcome(
                                False,
                                "Der terminierte Schritt konnte nicht sicher erzeugt werden.",
                            )
                        automation_configs: list[tuple[str, dict[str, object], datetime]] = [
                            (automation_id, generation.config, scheduled_for)
                        ]
                        checkpoint_records: list[
                            tuple[ThermalDeadlineCheckpoint, datetime]
                        ] = []
                        advice = stored_plan.adaptive_advice
                        if advice is not None:
                            area_id = stored_plan.goal.scope.area_id
                            thermal_model = (
                                self._runtime.predictive_house.thermal_model(area_id)
                                if self._runtime.predictive_house is not None
                                and area_id is not None else None
                            )
                            target_value = action.data.get("temperature")
                            climate_id = (
                                action.entity_id
                                if isinstance(action.entity_id, str) else None
                            )
                            if (
                                thermal_model is None or climate_id is None
                                or not isinstance(target_value, (int, float))
                                or thermal_model.model_id != advice.model_id
                            ):
                                return _ScheduledOutcome(
                                    False,
                                    "Die thermische Modellbindung ist nicht mehr eindeutig.",
                                )
                            base_checkpoint = ThermalDeadlineCheckpoint(
                                ThermalCheckpointPhase.START,
                                stored_plan.goal.goal_id,
                                thermal_model.binding.area_id,
                                climate_id,
                                thermal_model.binding.temperature_entity_id,
                                float(target_value),
                                advice.model_id,
                                advice.predicted_duration.total_seconds(),
                                advice.uncertainty_buffer.total_seconds(),
                                checkpoint_id=f"thermal-checkpoint-{uuid.uuid4().hex}",
                                token=secrets.token_urlsafe(32),
                                run_id=execution_run_id,
                                scheduled_for=scheduled_for.isoformat(),
                                deadline=advice.final_verification_at.isoformat(),
                            )
                            enriched = append_start_checkpoint(
                                generation.config, base_checkpoint
                            )
                            if enriched is None:
                                return _ScheduledOutcome(
                                    False, "Die thermische Startprüfung konnte nicht geplant werden."
                                )
                            automation_configs[0] = (automation_id, enriched, scheduled_for)
                            checkpoint_records = [(base_checkpoint, scheduled_for)]
                            for phase, checkpoint_at in (
                                (ThermalCheckpointPhase.INTERMEDIATE,
                                 advice.intermediate_check_at),
                                (ThermalCheckpointPhase.FINAL,
                                 advice.final_verification_at),
                            ):
                                checkpoint_id = uuid.uuid4().hex
                                phase_checkpoint = replace(
                                    base_checkpoint, phase=phase,
                                    checkpoint_id=f"thermal-checkpoint-{uuid.uuid4().hex}",
                                    token=secrets.token_urlsafe(32),
                                    scheduled_for=checkpoint_at.isoformat(),
                                )
                                checkpoint_config = checkpoint_automation_config(
                                    phase_checkpoint,
                                    scheduled_for=checkpoint_at,
                                    automation_id=checkpoint_id,
                                )
                                if checkpoint_config is None:
                                    return _ScheduledOutcome(
                                        False, "Ein thermischer Prüfzeitpunkt ist ungültig."
                                    )
                                automation_configs.append(
                                    (checkpoint_id, checkpoint_config, checkpoint_at)
                                )
                                checkpoint_records.append((phase_checkpoint, checkpoint_at))
                            checkpoint_store = self._runtime.thermal_checkpoints
                            if checkpoint_store is None:
                                checkpoint_store = PendingThermalCheckpointStore(
                                    self.hass.config.path(
                                        ".storage/homeintent_thermal_checkpoints.json"
                                    )
                                )
                                self._runtime.thermal_checkpoints = checkpoint_store
                            for checkpoint_record, checkpoint_at in checkpoint_records:
                                registered = await checkpoint_store.async_register(
                                    checkpoint_record, scheduled_for=checkpoint_at,
                                    deadline=advice.final_verification_at,
                                )
                                if not registered:
                                    return _ScheduledOutcome(
                                        False, "Ein thermischer Prüfpunkt konnte nicht authentifiziert werden."
                                    )
                        created_ids: list[str] = []
                        try:
                            for created_id, config, execute_at in automation_configs:
                                await self._automation_store().async_create_automation(
                                    config,
                                    automation_id=created_id,
                                    scheduled_for=execute_at,
                                    once=True,
                                )
                                created_ids.append(created_id)
                        except Exception as err:  # noqa: BLE001 - transactional executor reports heterogeneous HA/I/O failures
                            for created_id in reversed(created_ids):
                                try:
                                    await self._automation_store().async_delete_automation(
                                        created_id
                                    )
                                except Exception:  # noqa: BLE001 - best-effort multi-object rollback
                                    _LOGGER.exception(
                                        "Could not roll back thermal checkpoint %s",
                                        created_id,
                                    )
                            if advice is not None and self._runtime.thermal_checkpoints is not None:
                                for checkpoint_record, _checkpoint_at in checkpoint_records:
                                    await self._runtime.thermal_checkpoints.async_delete(
                                        checkpoint_record.checkpoint_id
                                    )
                            return _ScheduledOutcome(False, str(err))
                        return _ScheduledOutcome(True)

                    execution_run_id = f"run_{uuid.uuid4().hex}"
                    reservation = await self._runtime.execution_coordinator.async_acquire(
                        execution_run_id, stored_plan
                    )
                    if reservation.outcome.value == "conflict":
                        manager.cancel(conversation_id)
                        response.async_set_speech(
                            "Ein anderes Ziel verändert gerade mindestens dasselbe Gerät. Ich habe diesen Plan nicht nondeterministisch parallel ausgeführt."
                        )
                        return conversation.ConversationResult(
                            response=response, conversation_id=conversation_id
                        )
                    goal_runs = self._runtime.goal_runs
                    user_contexts = self._runtime.user_contexts

                    async def run_plan() -> PlanResult:
                        try:
                            plan_result = await PlanExecutor(
                                refresh, execute, verify, schedule
                            ).execute(
                                stored_plan, confirmed=True
                            )
                        finally:
                            await self._runtime.execution_coordinator.async_release(
                                execution_run_id
                            )
                        if goal_runs is not None:
                            current_entities = {
                                item.entity_id: item
                                for item in self._entities()
                            }
                            run = goal_run_from_plan_result(
                                stored_plan, plan_result, current_entities,
                                run_id=execution_run_id,
                                user_id=actor_id,
                                person_entity_id=(
                                    user_contexts.resolve_current_person(actor_id).person_entity_id
                                    if user_contexts is not None
                                    else None
                                ),
                                updated_at=dt_util.utcnow().isoformat(),
                            )
                            await goal_runs.async_append(run)
                        return plan_result

                    # Service calls are accepted within milliseconds, but
                    # verifying slow effects (a garage door, several locks)
                    # can take much longer than a voice satellite waits (F17).
                    # Answer with the final result when it is quick; otherwise
                    # confirm immediately, keep verifying in the background and
                    # report only a failure. The GoalRun keeps every piece of
                    # evidence for "Warum?".
                    plan_task = self.hass.async_create_task(
                        run_plan(), name=f"HomeIntent plan {execution_run_id}"
                    )
                    done, _pending = await asyncio.wait(
                        {plan_task}, timeout=_PLAN_REPLY_BUDGET_SECONDS
                    )
                    manager.cancel(conversation_id)
                    if plan_task not in done:
                        label = (stored_plan.goal.provenance.source_utterance if stored_plan.goal.provenance else "") or "Der bestätigte Plan"
                        plan_task.add_done_callback(
                            lambda task: self.hass.async_create_task(
                                self.async_report_background_plan(
                                    task, execution_run_id, label[:80], actor_id
                                ),
                                name=f"HomeIntent plan report {execution_run_id}",
                            )
                        )
                        steps = sum(
                            1 for step in stored_plan.steps
                            if step.kind in {StepKind.ACTION, StepKind.NOTIFY}
                        )
                        response.async_set_speech(
                            f"In Ordnung, ich führe den Plan jetzt aus ({steps} "
                            f"{'Schritt' if steps == 1 else 'Schritte'}) und prüfe die Wirkung. "
                            "Ich melde mich nur, falls etwas nicht klappt."
                        )
                        return conversation.ConversationResult(
                            response=response, conversation_id=conversation_id
                        )
                    result = plan_task.result()
                    if result.status is PlanStatus.COMPLETED:
                        response.async_set_speech("Der Plan wurde vollständig ausgeführt und verifiziert.")
                    elif result.status is PlanStatus.SCHEDULED:
                        response.async_set_speech(
                            "Die sofortigen Schritte wurden verifiziert; die verschobenen Schritte sind als persistente einmalige Home-Assistant-Automation geplant."
                        )
                    elif result.status is PlanStatus.PARTIAL_FAILURE:
                        response.async_set_speech(
                            "Der Plan wurde nur teilweise erfüllt. Mindestens ein früherer Schritt ist verifiziert, aber ein weiterer Schritt ist fehlgeschlagen."
                        )
                    else:
                        response.async_set_speech("Der Plan wurde sicher gestoppt, weil ein Schritt fehlschlug oder nicht verifiziert werden konnte.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        actor_id = conversation_user_id(user_input)
        person_id = None
        user_contexts = self._runtime.user_contexts
        if user_contexts is not None:
            person_binding = user_contexts.resolve_current_person(actor_id)
            if person_binding.status is BindingStatus.RESOLVED:
                person_id = person_binding.person_entity_id
        area = self._conversation_area(user_input)
        household = (
            user_contexts.household.person_entity_ids if user_contexts is not None else ()
        )
        person_names: dict[str, list[str]] = {}
        for entity in entities:
            if entity.domain != "person":
                continue
            aliases = {
                normalize_for_compare(entity.friendly_name),
                normalize_for_compare(entity.entity_id.partition(".")[2]),
            }
            for alias in aliases:
                if alias:
                    person_names.setdefault(alias, []).append(entity.entity_id)
        profiles = self._runtime.profiles
        routine_names: dict[str, str] = {}
        if profiles is not None and actor_id is not None:
            for routine in profiles.routines_for(actor_id):
                for spoken in (routine.name, routine.routine_id.replace("_", " ")):
                    if key := normalize_for_compare(spoken).strip():
                        routine_names[key] = routine.routine_id
        area_names = {
            key: entity.area_id
            for entity in entities
            if entity.area_id is not None
            for name in (entity.area_name or "", *entity.area_aliases)
            if (key := normalize_for_compare(name).strip())
        }
        goal = interpret_goal(
            language_document,
            current_user_id=actor_id,
            conversation_id=conversation_id,
            current_person_entity_id=person_id,
            voice_area_id=area.area_id if area is not None else None,
            household_person_ids=household,
            person_name_bindings={
                name: tuple(dict.fromkeys(entity_ids))
                for name, entity_ids in person_names.items()
            },
            routine_names=routine_names,
            area_names=area_names,
        )
        if goal is None:
            return None

        if goal.kind is V10GoalKind.EXPLAIN_FAILURE:
            # Explicit automation diagnostics belong to the existing
            # authoritative automation-management path.  V10 run-history
            # explanations handle anaphoric/general failure questions only.
            if parse_automation_management(user_input.text) is not None:
                return None
            if actor_id is None:
                response.async_set_speech(
                    "Ohne eindeutige Home-Assistant-Benutzerzuordnung kann ich keinen "
                    "persönlichen Zielverlauf erklären."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=conversation_id
                )
            store = self._runtime.goal_runs
            window = resolve_history_window(language_document.tokens, dt_util.now())
            goal_kind_value = goal.parameters.get("goal_kind")
            goal_kind = None
            if isinstance(goal_kind_value, str):
                try:
                    goal_kind = V10GoalKind(goal_kind_value)
                except ValueError:
                    goal_kind = None
            entity_id = _mentioned_goal_run_entity(language_document, entities)
            query = GoalRunQuery(
                user_id=actor_id,
                start_time=window.start if window is not None else None,
                end_time=window.end if window is not None else None,
                failed_only=goal.parameters.get("failed_only") is not False,
                goal_kind=goal_kind,
                routine_id=goal.routine_id,
                entity_id=entity_id,
            )
            matches = await store.async_query(query) if store else ()
            if window is not None and len(matches) > 1:
                labels = tuple(_goal_run_label(item) for item in matches)
                manager.create(
                    conversation_id,
                    "goal-run-clarification",
                    DialogTaskKind.GOAL_RUN_CLARIFICATION,
                    DialogPriority.SELECTION,
                    candidates=tuple(item.run_id for item in matches),
                    reason="Mehrere historische Ziele passen zum genannten Zeitraum.",
                    requested_by_user_id=actor_id,
                    payload=GoalRunClarification(
                        tuple(item.run_id for item in matches), labels, actor_id
                    ),
                )
                response.async_set_speech(
                    f"{window.label.capitalize()} sind {_german_count(len(matches))} passende Ziele fehlgeschlagen: "
                    + _german_goal_labels(labels)
                    + ". Welches meinst du?"
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            run = matches[-1] if matches else None
            explanation = explain_goal_run(run)
            if run is None and window is not None and window.label.startswith("gestern"):
                records = (
                    await self._runtime.monitor_goals.async_load()
                    if self._runtime.monitor_goals is not None
                    else ()
                )
                candidates = [
                    item.goal
                    for item in records
                    if item.enabled
                    and item.goal.provenance.user_id == actor_id
                    and item.goal.trigger is not None
                    and item.goal.trigger.person_entity_id is not None
                ]
                if len(candidates) == 1:
                    trigger = candidates[0].trigger
                    assert trigger is not None and trigger.person_entity_id is not None
                    evidence = await async_get_transition_evidence(
                        self.hass,
                        trigger.person_entity_id,
                        start=window.start,
                        end=window.end,
                        from_state=trigger.from_state or "home",
                        to_state=trigger.to_state or "not_home",
                    )
                    if evidence.available and evidence.occurred is False:
                        explanation = replace(
                            explanation,
                            message=(
                                f"Die Erinnerung wurde gestern nicht ausgelöst, weil "
                                f"{trigger.person_entity_id} laut Home-Assistant-Verlauf "
                                "nicht vom Ausgangszustand in den erwarteten Zielzustand wechselte."
                            ),
                        )
            response.async_set_speech(explanation.message)
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        if goal.kind is V10GoalKind.MONITOR_AND_NOTIFY:
            if goal.parameters.get("ambiguous_person_name"):
                response.async_set_speech(
                    "Die genannte Person ist nicht eindeutig. Bitte wähle eine konkrete person.*-Entität."
                )
            elif goal.trigger is None:
                response.async_set_speech("Der Auslöser ist nicht eindeutig. Es wurde nichts gespeichert.")
            elif goal.trigger.kind.startswith("person_") and goal.trigger.person_entity_id is None:
                response.async_set_speech(
                    "Ich kann „ich“ noch keinem Home-Assistant-Benutzer und keiner person.*-Entität eindeutig zuordnen. Bitte konfiguriere diese Zuordnung einmalig."
                )
            elif goal.trigger.kind == "nobody_home" and not household:
                response.async_set_speech(
                    "Für „niemand zuhause“ ist noch kein bestätigter Haushalt aus person.*-Entitäten konfiguriert."
                )
            elif not goal.recipient_person_ids:
                response.async_set_speech(
                    "Für den Empfänger fehlt eine eindeutige Personenzuordnung. Es wurde nichts gespeichert."
                )
            elif user_contexts is None or self._runtime.monitor_goals is None:
                response.async_set_speech("Die lokale Goal-Persistenz ist nicht verfügbar.")
            else:
                unresolved = [
                    user_contexts.resolve_notification_targets(recipient)
                    for recipient in goal.recipient_person_ids
                    if user_contexts.resolve_notification_targets(recipient).status
                    is not BindingStatus.RESOLVED
                ]
                if unresolved:
                    ambiguous = any(item.status is BindingStatus.AMBIGUOUS for item in unresolved)
                    response.async_set_speech(
                        "Welches bestätigte Gerät soll ich für diese Push-Benachrichtigung verwenden?"
                        if ambiguous
                        else "Für diese Person ist noch kein bestätigtes Push-Ziel konfiguriert."
                    )
                else:
                    confirmed_goal = replace(
                        goal, provenance=replace(goal.provenance, confirmed=True)
                    )
                    await self._runtime.monitor_goals.async_save(
                        MonitorRecord(confirmed_goal)
                    )
                    response.async_set_speech(
                        "Das Monitor-Ziel ist lokal gespeichert. Die Bedingung wird beim tatsächlichen Auslöser mit einem frischen Hauszustand geprüft."
                    )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        if goal.kind is V10GoalKind.SCHEDULED and goal.temporal is not None and goal.temporal.must_be_achieved_by_deadline:
            resolved_deadline = resolve_scheduled_datetime(
                language_document.temporal, dt_util.now()
            )
            goal_for_advice = (
                replace(goal, temporal=replace(goal.temporal, deadline=resolved_deadline))
                if resolved_deadline is not None else goal
            )
            advice = _thermal_advice_for_goal(
                goal_for_advice, entities, self._runtime.predictive_house
            )
            if advice is not None:
                try:
                    plan = materialize_goal(
                        goal_for_advice, entities, options=self.entry.options,
                        is_admin=await user_is_admin(self.hass, user_input),
                        user_id=actor_id, adaptive_advice=advice,
                    )
                except (PermissionError, ValueError) as err:
                    response.async_set_speech(f"Ich kann dafür keinen sicheren Plan erstellen: {err}")
                    return conversation.ConversationResult(response=response, conversation_id=conversation_id)
                return self.stage_plan(user_input, response, plan, actor_id)
            manager.create(
                conversation_id,
                "goal-semantic-clarification",
                DialogTaskKind.GOAL_SEMANTIC_CLARIFICATION,
                DialogPriority.SELECTION,
                slots={
                    "goal_id": goal.goal_id,
                    "area_id": goal.scope.area_id,
                    "desired_states": goal.desired_states,
                    "temporal": goal.temporal,
                },
                missing_slots=("goal_semantic_choice",),
                candidates=tuple(item.value for item in GoalSemanticChoice),
                reason="Ein Temperatur-Ergebnisziel muss semantisch geklärt werden.",
                requested_by_user_id=actor_id,
                payload=PendingGoalSemanticClarification(
                    goal, actor_id, conversation_id
                ),
            )
            response.async_set_speech(
                "Soll die Heizung zu diesem Zeitpunkt auf den Zielwert gestellt werden oder soll der Raum ihn dann bereits erreicht haben? Ohne bestätigtes thermisches Modell kann ich keine Vorheizzeit garantieren."
            )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)

        if goal.kind in {V10GoalKind.COMFORT, V10GoalKind.IMPROVE_COMFORT}:
            if actor_id is None or goal.scope.area_id is None:
                response.async_set_speech(
                    "Was bedeutet angenehm für dich hier? Soll ich Temperatur, Licht oder beides berücksichtigen?"
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            profiles = self._runtime.profiles
            profile = profiles.comfort(area_id=goal.scope.area_id, user_id=actor_id) if profiles else None
            if profile is None:
                response.async_set_speech(
                    "Was bedeutet angenehm für dich hier? Soll ich Temperatur, Licht oder beides berücksichtigen?"
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            try:
                plan = materialize_comfort_profile(
                    goal, profile, entities, options=self.entry.options,
                    is_admin=await user_is_admin(self.hass, user_input), user_id=actor_id,
                )
            except (PermissionError, ValueError) as err:
                response.async_set_speech(f"Ich kann dafür keinen sicheren Plan erstellen: {err}")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            return self.stage_plan(user_input, response, plan, actor_id)

        if goal.routine_id is not None:
            profiles = self._runtime.profiles
            routine = profiles.routine(goal.routine_id, user_id=actor_id) if profiles and actor_id else None
            if routine is None:
                manager.create(
                    conversation_id,
                    "routine-definition",
                    DialogTaskKind.ROUTINE_DEFINITION,
                    DialogPriority.FOLLOWUP,
                    slots={"routine_id": goal.routine_id},
                    missing_slots=("typisierte Routinen-Schritte",),
                    reason="Der Begriff hat noch keine ausdrücklich bestätigte lokale Bedeutung.",
                    requested_by_user_id=actor_id,
                )
                response.async_set_speech(
                    f"Was soll ich {_occasion_phrase(goal.routine_id)} erledigen? Ich speichere die Routine erst nach deiner ausdrücklichen Bestätigung."
                )
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            excluded_names = goal.parameters.get("excluded_area_names", ())
            if isinstance(excluded_names, (list, tuple)):
                excluded = {
                    entity.area_id for entity in entities
                    if entity.area_id is not None and any(
                        normalize_for_compare(str(name)) in {
                            normalize_for_compare(entity.area_id),
                            normalize_for_compare(entity.area_name or ""),
                        }
                        for name in excluded_names
                    )
                }
                if excluded:
                    goal = replace(goal, exclusions=GoalScope(excluded_area_ids=tuple(sorted(excluded))))
            try:
                plan = materialize_routine(
                    goal, routine, entities, options=self.entry.options,
                    is_admin=await user_is_admin(self.hass, user_input), user_id=actor_id,
                )
            except (PermissionError, ValueError) as err:
                response.async_set_speech(f"Ich kann dafür keinen sicheren Plan erstellen: {err}")
                return conversation.ConversationResult(response=response, conversation_id=conversation_id)
            return self.stage_plan(user_input, response, plan, actor_id)

        if goal.kind is GoalKind.PREPARE_MOVIE:
            store = self._runtime.memory
            preferences = (
                await store.async_list(
                    person_id=actor_id, kinds=(MemoryKind.PREFERENCE,)
                )
                if store is not None and store.enabled and actor_id is not None
                else ()
            )
            matching_preferences = [
                record
                for record in preferences
                if record.content.get("activity") == "television"
                and isinstance(record.content.get("entity_id"), str)
                and isinstance(record.content.get("brightness_percent"), int)
            ]
            if len(matching_preferences) == 1:
                preference = matching_preferences[0].content
                goal = Goal(
                    GoalKind.PREPARE_MOVIE,
                    {
                        "entity_ids": [preference["entity_id"]],
                        "brightness_percent": preference["brightness_percent"],
                    },
                )
        try:
            plan = materialize_goal(
                goal,
                entities,
                options=self.entry.options,
                is_admin=await user_is_admin(self.hass, user_input),
                user_id=actor_id,
            )
        except (PermissionError, ValueError) as err:
            response.async_set_speech(f"Ich kann dafür keinen sicheren Plan erstellen: {err}")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        actions = [step.description for step in plan.steps if step.kind is StepKind.ACTION]
        details = "; ".join(actions[:3])
        if len(actions) > 3:
            details += f"; sowie {len(actions) - 3} weitere geprüfte Schritte"
        if not plan.requires_confirmation:
            response.async_set_speech(plan.summary)
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        manager.create(
            conversation_id,
            "goal-plan",
            DialogTaskKind.PLAN_CONFIRMATION,
            DialogPriority.CONFIRMATION,
            slots={"plan": plan},
            reason="Ein vollständiger Mehrschrittplan wartet auf Bestätigung.",
            requested_by_user_id=actor_id,
        )
        response.async_set_speech(
            f"Planvorschau: {details}. {plan.summary} Soll ich den gesamten Plan ausführen?"
        )
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)

    async def async_report_background_plan(
        self,
        task: "asyncio.Task[PlanResult]",
        run_id: str,
        label: str,
        actor_id: str | None,
    ) -> None:
        """Report a plan that finished after the spoken reply - only if it failed."""
        try:
            result = task.result()
        except asyncio.CancelledError:
            failed = True
        except Exception:  # noqa: BLE001 - reported below, never swallowed silently
            _LOGGER.exception("HomeIntent background plan %s failed", run_id)
            failed = True
        else:
            failed = result.status not in {PlanStatus.COMPLETED, PlanStatus.SCHEDULED}
        if not failed:
            return
        proactive = self._runtime.proactive_context
        if proactive is not None and proactive.enabled and actor_id is not None:
            await proactive.async_report_goal_failure(
                run_id=run_id, goal_label=label, owner_user_id=actor_id,
            )
            return
        # Without the proactive layer the failure still has to reach the
        # user: a Home Assistant notification names what did not work.
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "HomeIntent",
                    "message": (
                        f"„{label}“ wurde nicht vollständig ausgeführt. "
                        "Frag „Warum hat das nicht funktioniert?“ für die Details."
                    ),
                    "notification_id": f"homeintent_plan_{run_id}",
                },
                blocking=False,
                context=call_context(),
            )
        except Exception:  # noqa: BLE001 - reporting must never raise
            _LOGGER.warning("HomeIntent could not report a failed plan", exc_info=True)

    def stage_plan(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        plan: MaterializedPlan,
        actor_id: str | None,
    ) -> conversation.ConversationResult:
        actions = [step.description for step in plan.steps if step.kind is StepKind.ACTION]
        details = "; ".join(actions[:3]) or "keine Änderung nötig"
        if len(actions) > 3:
            details += f"; sowie {len(actions) - 3} weitere geprüfte Schritte"
        if not plan.requires_confirmation:
            response.async_set_speech(plan.summary)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        self._runtime.dialog_manager.create(
            user_input.conversation_id,
            "goal-plan",
            DialogTaskKind.PLAN_CONFIRMATION,
            DialogPriority.CONFIRMATION,
            slots={"plan": plan},
            reason="Ein vollständiger Mehrschrittplan wartet auf Bestätigung.",
            requested_by_user_id=actor_id,
        )
        response.async_set_speech(
            f"Planvorschau: {details}. {plan.summary} Soll ich den gesamten Plan ausführen?"
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
