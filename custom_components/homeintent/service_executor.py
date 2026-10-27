"""One policy-gated HA write path shared by conversation and the agent."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Mapping, Sequence

from homeassistant.core import Context, HomeAssistant

from .agent_action_policy import RESERVED_TARGET_DATA_KEYS
from .audit_log import AuditTrail
from .entities import STATELESS_ACTION_DOMAINS, EntitySnapshot
from .effect_graph import build_plan_effects
from .effect_monitor import EffectMonitor
from .execution_context import (
    TECHNICAL_FAILURE_TEXT,
    UNAUTHORIZED_TEXT,
    current_turn,
    is_unauthorized,
    new_execution_context,
)
from .execution_trace import record_execution
from .execution_policy import PolicyDecision, PolicyOutcome, evaluate_service_plan
from .plan_origin import PlanOrigin
from .risk import RiskLevel, classify_service_plan
from .service_call import ServiceCallPlan
from .effect_wait import (
    EffectVerdict,
    PendingEffect,
    async_settle,
    expectations_for,
    judge_now,
    wait_seconds,
)
from .turn_outcome import TurnOutcomeKind, defer_outcome, report_outcome
from .const import CONF_EFFECT_WAIT_SECONDS, DEFAULT_EFFECT_WAIT_SECONDS
from .script_execution_observer import (
    ScriptExecutionResult,
    ScriptExecutionStatus,
    async_run_and_observe_script,
    script_execution_speech,
)
from .batch_execution import (
    BatchEligibility,
    BatchTarget,
    batch_eligibility,
    BatchExecutionResult,
    BatchTargetResult,
    BatchTargetStatus,
    PendingBatchExecution,
)


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConfirmedPlanScope:
    """Exact effective target set and risk shown for one proposed plan."""

    risk: RiskLevel
    targets: frozenset[str]


@dataclass(frozen=True)
class ConfirmedScope:
    """What a "Ja" agreed to: the risk and effective targets of the preview.

    A script, scene or group edited between the question and the answer is
    evaluated again; a higher risk or a new target is not covered by the
    earlier "Ja" (7.7, B7).
    """

    risk: RiskLevel
    targets: frozenset[str]
    plans: tuple[ConfirmedPlanScope, ...] = ()


CHANGED_SINCE_CONFIRMATION = (
    "Die Aktion hat sich seit meiner Rückfrage geändert und wirkt jetzt anders. "
    "Ich habe nichts ausgeführt. Bitte frag noch einmal."
)


def _effective_targets(plan: ServiceCallPlan, effects: object) -> frozenset[str]:
    direct = (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
    found = set(direct)
    targets = getattr(effects, "effective_targets", None)
    if targets is not None:
        found |= set(targets)
    return frozenset(found)


def confirmed_scope(
    hass: HomeAssistant,
    plans: Sequence[ServiceCallPlan],
    entities: Sequence[EntitySnapshot],
    options: Mapping[str, object],
    *,
    origin: PlanOrigin = PlanOrigin.EXPLICIT_COMMAND,
    binding_confirmed: bool = False,
) -> ConfirmedScope:
    """The scope a pending confirmation covers, taken when the question is asked."""
    risk = RiskLevel.LOW
    targets: set[str] = set()
    bindings: list[ConfirmedPlanScope] = []
    for plan in plans:
        effects = build_plan_effects(hass, plan)
        decision = evaluate_service_plan(
            plan, list(entities), options, is_admin=True, user_id=None, effects=effects,
            origin=origin, binding_confirmed=binding_confirmed,
        )
        risk = max(risk, decision.risk)
        effective = _effective_targets(plan, effects)
        targets |= effective
        bindings.append(ConfirmedPlanScope(decision.risk, effective))
    return ConfirmedScope(risk, frozenset(targets), tuple(bindings))


def _confirmation_scope_changed(
    decision: PolicyDecision,
    effective_targets: frozenset[str],
    scope: ConfirmedScope,
) -> bool:
    """Return whether this is no longer one exact plan shown to the user."""
    if scope.plans:
        return not any(
            effective_targets == item.targets and decision.risk <= item.risk
            for item in scope.plans
        )
    # Compatibility for persisted/tests scopes created before per-plan
    # bindings existed. They remain fail-closed for additions and risk.
    return decision.risk > scope.risk or not effective_targets <= scope.targets


@dataclass(frozen=True)
class ExecutionResult:
    executed: bool
    decision: PolicyDecision
    error: str | None = None
    # The HA ``Context.id`` of the call (= execution id, 7.3.2).
    execution_id: str | None = None
    script_outcome: ScriptExecutionResult | None = None
    batch_result: BatchExecutionResult | None = None


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
    origin: PlanOrigin = PlanOrigin.EXPLICIT_COMMAND,
    attended: bool = True,
    binding_confirmed: bool = False,
    context: Context | None = None,
    scope: ConfirmedScope | None = None,
) -> ExecutionResult:
    """The single physical write path; reports what the turn did (7.9.1 B).

    ``EXECUTED`` when the write ran and every target shows the requested
    state or moves in the requested direction (7.9.2 A1); a target that has
    not reported yet is handed to the turn, which waits for it (bounded,
    event-driven) before the reply is decided. A write that did not run is
    ``NOT_DONE``.

    An independent batch (``batch_execution.batch_eligibility``) settles all
    accepted targets itself in one bounded window and returns its final
    result; nothing of it is left for the turn to settle.
    """
    if _batch_eligibility(plan, entities) is BatchEligibility.ELIGIBLE:
        return await _async_execute_independent_batch(
            hass,
            plan,
            tuple(entities),
            options,
            is_admin=is_admin,
            user_id=user_id,
            confirmed=confirmed,
            audit_trail=audit_trail,
            audit_actor_id=audit_actor_id,
            effect_monitor=effect_monitor,
            origin=origin,
            attended=attended,
            binding_confirmed=binding_confirmed,
            context=context,
            scope=scope,
        )
    names = {entity.entity_id: entity.friendly_name for entity in entities}
    expectations = expectations_for(plan, lambda entity_id: _snapshot(hass, entity_id), names)
    result = await _async_execute_service_plan(
        hass, plan, entities, options, is_admin=is_admin, user_id=user_id, confirmed=confirmed,
        audit_trail=audit_trail, audit_actor_id=audit_actor_id, effect_monitor=effect_monitor,
        origin=origin, attended=attended, binding_confirmed=binding_confirmed, context=context,
        scope=scope,
    )
    if not result.executed:
        report_outcome(TurnOutcomeKind.NOT_DONE)
        return result
    if result.script_outcome is not None:
        report_outcome(
            TurnOutcomeKind.EXECUTED
            if result.script_outcome.status is ScriptExecutionStatus.COMPLETED
            else TurnOutcomeKind.UNCONFIRMED
        )
        return result
    pending = PendingEffect(expectations)
    judge_now(hass, pending)
    if pending.confirmed:
        report_outcome(TurnOutcomeKind.EXECUTED)
    elif not defer_outcome(pending):
        report_outcome(TurnOutcomeKind.UNCONFIRMED)
    return result


async def _async_execute_independent_batch(
    hass: HomeAssistant,
    plan: ServiceCallPlan,
    entities: Sequence[EntitySnapshot],
    options: Mapping[str, object],
    *,
    is_admin: bool,
    user_id: str | None,
    confirmed: bool,
    audit_trail: AuditTrail | None,
    audit_actor_id: str | None,
    effect_monitor: EffectMonitor | None,
    origin: PlanOrigin,
    attended: bool,
    binding_confirmed: bool,
    context: Context | None,
    scope: ConfirmedScope | None,
) -> ExecutionResult:
    """Preflight every target, then run only independent validated targets."""

    target_ids = tuple(plan.entity_id) if not isinstance(plan.entity_id, str) else (plan.entity_id,)
    by_id = {entity.entity_id: entity for entity in entities}
    names = {
        entity_id: by_id[entity_id].friendly_name
        if entity_id in by_id
        else entity_id
        for entity_id in target_ids
    }
    overall_effects = build_plan_effects(hass, plan)
    overall_decision = evaluate_service_plan(
        plan,
        list(entities),
        options,
        is_admin=is_admin,
        user_id=user_id,
        effects=overall_effects,
        origin=origin,
        attended=attended,
        binding_confirmed=binding_confirmed,
    )
    if any(
        classify_service_plan(
            ServiceCallPlan(plan.domain, plan.service, entity_id, dict(plan.data)),
            list(entities),
        ) >= RiskLevel.HIGH
        for entity_id in target_ids
    ):
        # Eligibility is decided separately from risk, but a target that is
        # itself high or critical risk keeps the established all-or-nothing
        # path. The overall risk (including the broad-action tier) still
        # decides confirmation for the whole batch below.
        return await _async_execute_service_plan(
            hass,
            plan,
            tuple(entities),
            options,
            is_admin=is_admin,
            user_id=user_id,
            confirmed=confirmed,
            audit_trail=audit_trail,
            audit_actor_id=audit_actor_id,
            effect_monitor=effect_monitor,
            origin=origin,
            attended=attended,
            binding_confirmed=binding_confirmed,
            context=context,
            scope=scope,
        )
    if overall_decision.outcome is PolicyOutcome.DENY or (
        overall_decision.outcome is PolicyOutcome.CONFIRM and not confirmed
    ):
        status = (
            BatchTargetStatus.UNAUTHORIZED
            if overall_decision.outcome is PolicyOutcome.DENY
            else BatchTargetStatus.NOT_CONFIRMED
        )
        batch = BatchExecutionResult(
            plan,
            tuple(
                BatchTargetResult(
                    entity_id,
                    names[entity_id],
                    status,
                    overall_decision.reason,
                )
                for entity_id in target_ids
            ),
        )
        for _entity_id in target_ids:
            report_outcome(TurnOutcomeKind.NOT_DONE)
        return ExecutionResult(
            False,
            overall_decision,
            overall_decision.reason,
            batch_result=batch,
        )

    preflight: dict[str, BatchTargetResult] = {}
    runnable: list[tuple[EntitySnapshot, ServiceCallPlan]] = []
    required = _required_capabilities(plan)
    confirmed_targets_changed = (
        confirmed
        and scope is not None
        and _confirmation_scope_changed(
            overall_decision, _effective_targets(plan, overall_effects), scope
        )
    )
    for entity_id in target_ids:
        entity = by_id.get(entity_id)
        if entity is None:
            # ``entities`` is the exposed HomeIntent snapshot.  A plan target
            # absent from it has no authority merely because its entity id was
            # present in caller data.
            preflight[entity_id] = BatchTargetResult(
                entity_id, names[entity_id], BatchTargetStatus.UNAUTHORIZED
            )
            continue
        if _state_is_unreliable(entity):
            preflight[entity_id] = BatchTargetResult(
                entity_id, names[entity_id], BatchTargetStatus.UNAVAILABLE
            )
            continue
        if required and entity.capabilities and not required <= entity.capabilities:
            preflight[entity_id] = BatchTargetResult(
                entity_id, entity.friendly_name, BatchTargetStatus.UNSUPPORTED
            )
            continue
        child = ServiceCallPlan(plan.domain, plan.service, entity_id, dict(plan.data))
        decision = evaluate_service_plan(
            child,
            list(entities),
            options,
            is_admin=is_admin,
            user_id=user_id,
            effects=build_plan_effects(hass, child),
            origin=origin,
            attended=attended,
            binding_confirmed=binding_confirmed,
        )
        if decision.outcome is PolicyOutcome.DENY:
            preflight[entity_id] = BatchTargetResult(
                entity_id,
                entity.friendly_name,
                BatchTargetStatus.UNAUTHORIZED,
                decision.reason,
            )
        elif confirmed_targets_changed or (
            decision.outcome is PolicyOutcome.CONFIRM and not confirmed
        ):
            preflight[entity_id] = BatchTargetResult(
                entity_id, entity.friendly_name, BatchTargetStatus.NOT_CONFIRMED
            )
        else:
            runnable.append((entity, child))

    # The complete preflight above finishes before this first write.
    outcomes: dict[str, BatchTargetResult] = dict(preflight)
    pending_effects: dict[str, PendingEffect] = {}
    called: list[str] = []
    execution_id: str | None = None
    for entity, child in runnable:
        expectations = expectations_for(
            child,
            lambda entity_id: _snapshot(hass, entity_id),
            names,
        )
        result = await _async_execute_service_plan(
            hass,
            child,
            tuple(entities),
            options,
            is_admin=is_admin,
            user_id=user_id,
            confirmed=confirmed,
            # The batch is audited once, after the effect wait, with every
            # target's final status (``AuditTrail.record_batch``).
            audit_trail=None,
            audit_actor_id=audit_actor_id,
            effect_monitor=effect_monitor,
            origin=origin,
            attended=attended,
            binding_confirmed=binding_confirmed,
            context=context,
            # The complete batch was bound and checked above before its first
            # write. A child is an execution unit, not a separately previewed
            # plan; policy is still re-evaluated immediately here.
            scope=None,
        )
        execution_id = execution_id or result.execution_id
        if not result.executed:
            if result.error == UNAUTHORIZED_TEXT:
                status = BatchTargetStatus.UNAUTHORIZED
            elif result.execution_id is None:
                # Refused by the re-evaluation directly before the call.
                status = BatchTargetStatus.REJECTED
            else:
                status = BatchTargetStatus.FAILED
            outcomes[entity.entity_id] = BatchTargetResult(
                entity.entity_id, entity.friendly_name, status, result.error
            )
            continue
        called.append(entity.entity_id)
        pending_effects[entity.entity_id] = PendingEffect(expectations)
        # A service accepted by Home Assistant is not proof that the device
        # reached the requested state: pending until the wait below.
        outcomes[entity.entity_id] = BatchTargetResult(
            entity.entity_id, entity.friendly_name, BatchTargetStatus.PENDING
        )

    pending_batch = PendingBatchExecution(
        plan, tuple(outcomes[entity_id] for entity_id in target_ids)
    )
    # Every accepted call is observed together: one listener, one bounded
    # window of ``effect_wait_seconds`` - never one full wait per device.
    settled = await async_settle(
        hass, list(pending_effects.values()), wait_seconds(options)
    )
    verdicts = {
        entity_id: (
            "reached" if not pending.expectations
            else pending.verdicts.get(entity_id, EffectVerdict.PENDING).value
        )
        for entity_id, pending in pending_effects.items()
    }
    batch = pending_batch.finalize(verdicts, observed=settled.observed)
    for item in batch.targets:
        if item.status is BatchTargetStatus.SUCCESS:
            report_outcome(TurnOutcomeKind.EXECUTED)
        elif item.entity_id in pending_effects:
            report_outcome(TurnOutcomeKind.UNCONFIRMED)
        else:
            report_outcome(TurnOutcomeKind.NOT_DONE)
    if audit_trail is not None and called:
        from homeassistant.util import dt as dt_util

        audit_trail.record_batch(dt_util.now(), audit_actor_id, batch, tuple(called))
    any_executed = bool(called)
    return ExecutionResult(
        any_executed,
        overall_decision,
        None if any_executed else "Kein Ziel hat die vollständige Vorprüfung bestanden.",
        execution_id,
        batch_result=batch,
    )


def _snapshot(hass: HomeAssistant, entity_id: str) -> tuple[str | None, Mapping[str, object] | None]:
    states = getattr(hass, "states", None)
    state = states.get(entity_id) if states is not None else None
    if state is None:
        return None, None
    return getattr(state, "state", None), getattr(state, "attributes", None)


async def _async_execute_service_plan(
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
    origin: PlanOrigin = PlanOrigin.EXPLICIT_COMMAND,
    attended: bool = True,
    binding_confirmed: bool = False,
    context: Context | None = None,
    scope: ConfirmedScope | None = None,
) -> ExecutionResult:
    """Re-evaluate policy immediately before the only physical write.

    The transitive effect graph of scripts, scenes and groups is rebuilt
    here, after any confirmation, so a script edited between the preview and
    the "Ja" is checked with its new content.

    Every call carries a Home Assistant ``Context``: the one of the current
    user turn (one execution id per utterance), an explicit ``context``
    (undo, agent, proactive), or a fresh one with the speaking user. With a
    ``user_id`` HA additionally applies that user's entity permissions.
    """
    names = {entity.entity_id: entity.friendly_name for entity in entities}
    effects = build_plan_effects(hass, plan)
    decision = evaluate_service_plan(
        plan,
        entities,
        options,
        is_admin=is_admin,
        user_id=user_id,
        effects=effects,
        origin=origin,
        attended=attended,
        binding_confirmed=binding_confirmed,
    )
    if decision.outcome is PolicyOutcome.DENY:
        return ExecutionResult(False, decision, decision.reason)
    if decision.outcome is PolicyOutcome.CONFIRM and not confirmed:
        return ExecutionResult(False, decision, "Die Aktion benötigt eine Bestätigung.")
    if confirmed and scope is not None and _confirmation_scope_changed(
        decision, _effective_targets(plan, effects), scope
    ):
        # The "Ja" answered a different question (7.7, B7).
        return ExecutionResult(False, decision, CHANGED_SINCE_CONFIRMATION)
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
    turn = current_turn()
    call_context = context or (turn.context if turn is not None else None) or (
        new_execution_context(user_id)
    )
    execution = str(call_context.id)
    script_outcome: ScriptExecutionResult | None = None
    try:
        if (
            plan.domain == "script"
            and plan.service == "turn_on"
            and isinstance(plan.entity_id, str)
        ):
            configured_wait = options.get(
                CONF_EFFECT_WAIT_SECONDS, DEFAULT_EFFECT_WAIT_SECONDS
            )
            wait_seconds = (
                float(configured_wait)
                if isinstance(configured_wait, (int, float))
                and not isinstance(configured_wait, bool)
                else DEFAULT_EFFECT_WAIT_SECONDS
            )
            script_outcome = await async_run_and_observe_script(
                hass,
                plan.entity_id,
                plan.data,
                context=call_context,
                wait_seconds=wait_seconds,
                service_runner=lambda domain, service, data, script_context: (
                    hass.services.async_call(
                        domain,
                        service,
                        dict(data),
                        blocking=True,
                        context=script_context,
                    )
                ),
            )
            if not script_outcome.accepted:
                name = names.get(plan.entity_id, plan.entity_id)
                error = script_execution_speech(name, script_outcome)
                return ExecutionResult(
                    False, decision, error, execution, script_outcome
                )
        else:
            await hass.services.async_call(
                plan.domain,
                plan.service,
                {**plan.data, "entity_id": plan.entity_id},
                blocking=True,
                context=call_context,
            )
    except Exception as err:  # noqa: BLE001 - HA service failures are heterogeneous
        if is_unauthorized(err):
            return ExecutionResult(False, decision, UNAUTHORIZED_TEXT, execution)
        _LOGGER.exception(
            "HomeIntent service call %s.%s failed for %s",
            plan.domain,
            plan.service,
            plan.entity_id,
        )
        return ExecutionResult(False, decision, TECHNICAL_FAILURE_TEXT, execution)
    from homeassistant.util import dt as dt_util

    now = dt_util.now()
    if audit_trail is not None:
        audit_trail.record(now, audit_actor_id, plan)
    if effect_monitor is not None and (
        script_outcome is None
        or script_outcome.status is ScriptExecutionStatus.COMPLETED
    ):
        effect_monitor.register(plan)
    record_execution(
        hass,
        context=call_context,
        plan=plan,
        decision=decision,
        user_id=user_id,
        utterance=turn.utterance if turn is not None and context is None else None,
        origin=origin.value,
        attended=attended,
        now=now,
        entity_names={entity.entity_id: entity.friendly_name for entity in entities},
    )
    return ExecutionResult(
        True, decision, None, execution, script_outcome
    )


def _state_is_unreliable(entity: EntitySnapshot) -> bool:
    if entity.state == "unavailable":
        return True
    return entity.state == "unknown" and entity.domain not in STATELESS_ACTION_DOMAINS


def _required_capabilities(plan: ServiceCallPlan) -> frozenset[str]:
    media = {
        "media_play": "PLAY",
        "media_pause": "PAUSE",
        "media_stop": "STOP",
        "volume_set": "VOLUME_SET",
        "volume_mute": "VOLUME_MUTE",
        "volume_up": "VOLUME_STEP",
        "volume_down": "VOLUME_STEP",
        "select_source": "SELECT_SOURCE",
        "media_next_track": "NEXT_TRACK",
        "media_previous_track": "PREVIOUS_TRACK",
    }
    if plan.domain == "media_player" and plan.service in media:
        return frozenset({media[plan.service]})
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


def _batch_eligibility(
    plan: ServiceCallPlan, entities: Sequence[EntitySnapshot]
) -> BatchEligibility:
    """Eligibility of the plan's targets, including HA turn helpers.

    The domain comes from each resolved target (``homeassistant.turn_off``
    on lights is a light batch); a target outside the exposed snapshot keeps
    the domain of its entity id and is refused per target in the preflight.
    """
    target_ids = (
        (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
    )
    by_id = {entity.entity_id: entity for entity in entities}
    targets = tuple(
        BatchTarget(
            entity_id,
            by_id[entity_id].domain if entity_id in by_id else entity_id.partition(".")[0],
            by_id[entity_id].device_class if entity_id in by_id else None,
            entity_id in by_id,
        )
        for entity_id in target_ids
    )
    return batch_eligibility(plan.service, targets)
