"""Deterministic bounded planner over closed HomeIntent operators."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Awaitable, Callable, Iterable, Mapping, Protocol, Sequence, cast

from .adaptive_planning import AdaptivePlanningAdvice, apply_advice

from .agent_action_policy import validate_agent_service_plan
from .entities import EntitySnapshot, normalize_for_compare
from .execution_policy import PolicyOutcome, evaluate_service_plan
from .goal_model import DesiredState, GoalKind, GoalModel, GoalScope
from .goal_run import (
    FailureCode,
    GoalRun,
    GoalRunStatus,
    StepExecutionRecord,
    VerificationRecord,
)
from .profiles import ComfortProfile, RoutineDefinition, RoutineStepDefinition
from .risk import RiskLevel, classify_service_plan
from .service_call import ServiceCallPlan

Goal = GoalModel


class StepKind(StrEnum):
    CHECK = "check"
    QUERY = "query"
    ACTION = "action"
    WAIT_FOR_EVENT = "wait_for_event"
    WAIT_UNTIL = "wait_until"
    CONDITION = "condition"
    NOTIFY = "notify"
    VERIFY = "verify"
    BRANCH = "branch"
    SUBPLAN = "subplan"


class PlanStatus(StrEnum):
    PREVIEW = "preview"
    NEEDS_CONFIRMATION = "needs_confirmation"
    COMPLETED = "completed"
    SCHEDULED = "scheduled"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"
    STOPPED = "stopped"  # V9 compatibility; equivalent to safe failure.


@dataclass(frozen=True)
class PlanningLimits:
    max_depth: int = 16
    max_candidates: int = 8
    max_expansions: int = 64
    max_steps: int = 32


@dataclass(frozen=True)
class PlanOperator:
    operator_id: str
    supported_goal_types: frozenset[GoalKind]
    required_capabilities: tuple[str, ...]
    effects: tuple[str, ...]
    risk: RiskLevel
    idempotent: bool
    verification_strategy: str


@dataclass(frozen=True)
class PlanningTrace:
    goal_kind: str
    facts: tuple[str, ...] = ()
    selected_operators: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    bounds: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    kind: StepKind
    description: str
    preconditions: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    invariants: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    action: ServiceCallPlan | None = None
    risk: RiskLevel = RiskLevel.LOW
    reversible: bool = False
    verification: Mapping[str, str] = field(default_factory=lambda: _empty_verification())
    compensation: ServiceCallPlan | None = None
    operator_id: str | None = None
    idempotent: bool = False
    execute_at_local_time: str | None = None
    scheduled_for: datetime | None = None


@dataclass(frozen=True)
class MaterializedPlan:
    plan_id: str
    goal: GoalModel
    steps: tuple[PlanStep, ...]
    aggregate_risk: RiskLevel
    requires_confirmation: bool
    summary: str
    trace: PlanningTrace | None = None
    adaptive_advice: AdaptivePlanningAdvice | None = None


@dataclass(frozen=True)
class StepResult:
    step_id: str
    success: bool
    message: str
    executed_at: str | None = None
    verified_at: str | None = None
    attempted_at: str | None = None
    service_accepted_at: str | None = None
    # Authoritative device-service acceptance, independent of ``success``:
    # None = no device service call happened (no-op, check, or only scheduled),
    # False = a call was attempted and rejected/failed, True = HA accepted it.
    service_accepted: bool | None = None


@dataclass(frozen=True)
class PlanResult:
    plan_id: str
    status: PlanStatus
    steps: tuple[StepResult, ...]
    compensated_steps: tuple[str, ...] = ()


def materialize_goal(
    goal: GoalModel,
    entities: Iterable[EntitySnapshot],
    *,
    options: Mapping[str, object],
    is_admin: bool,
    user_id: str | None,
    limits: PlanningLimits = PlanningLimits(),
    adaptive_advice: AdaptivePlanningAdvice | None = None,
) -> MaterializedPlan:
    """Materialize legacy bounded goals through the central policy path."""
    goal = apply_advice(goal, adaptive_advice)
    snapshots = tuple(entities)
    if goal.kind is GoalKind.SCHEDULED:
        return _materialize_scheduled_setpoint(
            goal, snapshots, options, is_admin, user_id, limits,
            adaptive_advice=adaptive_advice,
        )
    by_id = {entity.entity_id: entity for entity in snapshots}
    requested = goal.parameters.get("entity_ids", ())
    requested_items = cast(Sequence[object], requested) if isinstance(requested, (list, tuple)) else ()
    if any(not isinstance(item, str) for item in requested_items):
        raise ValueError("Das Ziel enthält ungültige Entitäts-IDs")
    entity_ids = tuple(cast(str, item) for item in requested_items) or goal.scope.entity_ids
    if not entity_ids and goal.kind in {GoalKind.PREPARE_NIGHT, GoalKind.PREPARE_AWAY}:
        entity_ids = tuple(
            entity.entity_id for entity in snapshots
            if "TURN_OFF" in entity.capabilities
            and entity.state in {"on", "playing", "heating", "cooling"}
        )
    if not entity_ids and goal.kind is GoalKind.SAVE_UNOCCUPIED:
        unoccupied = {
            entity.area_id for entity in snapshots
            if entity.area_id is not None and entity.domain == "binary_sensor"
            and entity.device_class in {"occupancy", "presence"}
            and entity.state in {"off", "clear", "not_home"}
        }
        entity_ids = tuple(
            entity.entity_id for entity in snapshots
            if entity.area_id in unoccupied and "TURN_OFF" in entity.capabilities
            and entity.state in {"on", "playing", "heating", "cooling"}
        )
    if not entity_ids and goal.kind is GoalKind.SECURE_HOME:
        entity_ids = tuple(
            entity.entity_id for entity in snapshots
            if (entity.domain == "lock" and entity.state in {"unlocked", "unlocking"})
            or (
                entity.domain == "cover" and entity.state in {"open", "opening"}
                and "POSITION" in entity.capabilities
            )
        )
    if not entity_ids and goal.kind is GoalKind.QUIET_MEDIA:
        entity_ids = tuple(
            entity.entity_id for entity in snapshots
            if entity.domain == "media_player" and entity.state in {"playing", "buffering"}
        )
    if any(item not in by_id for item in entity_ids):
        raise ValueError("Das Ziel enthält unbekannte oder veraltete Entitäten")
    entity_ids = _apply_exclusions(goal, entity_ids, by_id)
    if len(entity_ids) + 1 > limits.max_steps:
        raise ValueError("Der sichere Plan überschreitet die konfigurierte Schrittgrenze")
    check = PlanStep(
        "check-fresh-state", StepKind.CHECK,
        "Aktuelle Zustände, Verfügbarkeit und Fähigkeiten prüfen",
        invariants=("fresh_snapshot", "capability_checked", "policy_checked"),
    )
    if goal.kind is GoalKind.INVESTIGATE_STATE:
        return MaterializedPlan(
            f"plan_{uuid.uuid4().hex}", goal, (check,), RiskLevel.LOW, False,
            "Ich prüfe ausschließlich aktuelle Zustände; es wird nichts geschaltet.",
            _trace(goal, snapshots, (), (), limits),
        )
    if not entity_ids:
        raise ValueError("Für dieses Ziel wurden keine eindeutigen Geräte festgelegt")
    steps: list[PlanStep] = [check]
    dependency = check.step_id
    selected: list[str] = []
    skipped: list[str] = []
    for entity_id in entity_ids:
        entity = by_id[entity_id]
        action, expected, capability = _legacy_action(goal, entity)
        if capability is not None and capability not in entity.capabilities:
            raise ValueError(f"{entity.friendly_name} unterstützt {capability} nicht")
        brightness = goal.parameters.get("brightness_percent")
        if action.service == "turn_on" and entity.domain == "light" and isinstance(brightness, int):
            if not 1 <= brightness <= 100 or "BRIGHTNESS" not in entity.capabilities:
                raise ValueError(f"{entity.friendly_name} unterstützt die gewünschte Helligkeit nicht")
            action = ServiceCallPlan("light", "turn_on", entity_id, {"brightness_pct": brightness})
        elif entity.state == expected:
            skipped.append(f"{entity_id}:already_{expected}")
            continue
        _validate_action(action, snapshots, options, is_admin, user_id)
        inverse = _compensation(goal, entity, action)
        step_id = f"action-{len(steps)}"
        operator = _operator_id(action.domain, action.service)
        steps.append(
            PlanStep(
                step_id, StepKind.ACTION, f"{entity.friendly_name} {expected}",
                preconditions=("entity_available", (capability or "domain_service_available").casefold()),
                effects=(f"state={expected}",),
                invariants=("same_stable_entity_id", "policy_allow_or_confirmed"),
                dependencies=(dependency,), action=action,
                risk=classify_service_plan(action, snapshots), reversible=inverse is not None,
                verification={entity_id: expected}, compensation=inverse,
                operator_id=operator, idempotent=_is_idempotent(action),
            )
        )
        selected.append(operator)
        dependency = step_id
    aggregate = max((step.risk for step in steps), default=RiskLevel.LOW)
    plan = MaterializedPlan(
        f"plan_{uuid.uuid4().hex}", goal, tuple(steps), aggregate, True,
        f"{len(steps) - 1} geprüfte Aktion(en), Gesamtrisiko {_RISK_WORDS_DE[aggregate.name]}.",
        _trace(goal, snapshots, tuple(selected), tuple(skipped), limits),
    )
    validate_plan_graph(plan, limits=limits)
    return plan


def _materialize_scheduled_setpoint(
    goal: GoalModel,
    snapshots: tuple[EntitySnapshot, ...],
    options: Mapping[str, object],
    is_admin: bool,
    user_id: str | None,
    limits: PlanningLimits,
    *,
    adaptive_advice: AdaptivePlanningAdvice | None = None,
) -> MaterializedPlan:
    """Build one scheduled setpoint through the normal policy boundary."""
    if (
        goal.temporal is None
        or goal.temporal.must_be_achieved_by_deadline
        or goal.temporal.execute_at is None
    ):
        raise ValueError("Die Semantik des Temperaturziels ist noch nicht geklärt")
    if len(goal.desired_states) != 1:
        raise ValueError("Das terminierte Ziel benötigt genau einen Zielzustand")
    area = normalize_for_compare(goal.scope.area_id or "")
    candidates = tuple(
        entity
        for entity in snapshots
        if entity.domain == (goal.scope.domain or "climate")
        and (
            not area
            or area == normalize_for_compare(entity.area_id or "")
            or area == normalize_for_compare(entity.area_name or "")
        )
    )
    if len(candidates) != 1:
        raise ValueError("Für den Raum ist keine eindeutige Heizung festgelegt")
    entity = candidates[0]
    desired = goal.desired_states[0]
    action, effect = _action_for_desired_state(entity, desired)
    _validate_action(action, snapshots, options, is_admin, user_id)
    check = PlanStep(
        "check-fresh-state",
        StepKind.CHECK,
        "Aktuelle Zustände, Verfügbarkeit und Fähigkeiten prüfen",
        invariants=("fresh_snapshot", "capability_checked", "policy_checked"),
    )
    target = goal.temporal.execute_at
    step = PlanStep(
        "scheduled-setpoint",
        StepKind.ACTION,
        f"{entity.friendly_name} zum geplanten Zeitpunkt auf {desired.value:g} Grad setzen"
        if isinstance(desired.value, (int, float))
        else f"{entity.friendly_name} zum geplanten Zeitpunkt anpassen",
        preconditions=("entity_available", "capability_checked"),
        effects=(effect,),
        dependencies=(check.step_id,),
        action=action,
        risk=classify_service_plan(action, snapshots),
        verification={entity.entity_id: _verification_value(desired)},
        operator_id=_operator_id(action.domain, action.service),
        idempotent=_is_idempotent(action),
        execute_at_local_time=target.strftime("%H:%M"),
        scheduled_for=target,
    )
    plan = MaterializedPlan(
        f"plan_{uuid.uuid4().hex}",
        goal,
        (check, step),
        step.risk,
        True,
        f"Der Sollwert wird einmalig am {target.date().isoformat()} um {target:%H:%M} Uhr gesetzt.",
        _trace(goal, snapshots, (step.operator_id or "",), (), limits),
        adaptive_advice,
    )
    validate_plan_graph(plan, limits=limits)
    return plan


def materialize_routine(
    goal: GoalModel,
    routine: RoutineDefinition,
    entities: Iterable[EntitySnapshot],
    *,
    options: Mapping[str, object],
    is_admin: bool,
    user_id: str | None,
    limits: PlanningLimits = PlanningLimits(),
) -> MaterializedPlan:
    """Materialize a confirmed routine; exclusions affect every step."""
    if not routine.confirmed:
        raise ValueError("Die Routine ist nicht ausdrücklich bestätigt")
    snapshots = tuple(entities)
    by_id = {item.entity_id: item for item in snapshots}
    steps: list[PlanStep] = [
        PlanStep(
            "check-fresh-state", StepKind.CHECK,
            "Aktuelle Zustände, Verfügbarkeit und Fähigkeiten prüfen",
            invariants=("fresh_snapshot", "capability_checked", "policy_checked"),
        )
    ]
    dependency = steps[0].step_id
    selected: list[str] = []
    skipped: list[str] = []
    expansions = 0
    for definition in routine.steps:
        if not definition.scope.entity_ids:
            raise ValueError("Routinen benötigen stabil aufgelöste Entitäts-IDs")
        for entity_id in definition.scope.entity_ids:
            expansions += 1
            if expansions > limits.max_expansions or len(steps) >= limits.max_steps:
                raise ValueError("Die Routine überschreitet das begrenzte Planungsbudget")
            entity = by_id.get(entity_id)
            if entity is None:
                raise ValueError(f"Routine-Ziel {entity_id} ist nicht mehr vorhanden")
            if not _apply_exclusions(goal, (entity_id,), by_id):
                skipped.append(f"{entity_id}:excluded")
                continue
            if _desired_already_satisfied(entity, definition.desired_state):
                skipped.append(f"{entity_id}:already_satisfied")
                continue
            action, effect = _action_for_desired_state(entity, definition.desired_state)
            _validate_action(action, snapshots, options, is_admin, user_id)
            operator = _operator_id(action.domain, action.service)
            step_id = f"routine-{len(steps)}"
            steps.append(
                PlanStep(
                    step_id, StepKind.ACTION,
                    definition.description or f"{entity.friendly_name} anpassen",
                    preconditions=("entity_available", "capability_checked"),
                    effects=(effect,), dependencies=(dependency,), action=action,
                    risk=classify_service_plan(action, snapshots), reversible=False,
                    verification={entity_id: _verification_value(definition.desired_state)},
                    operator_id=operator, idempotent=_is_idempotent(action),
                )
            )
            selected.append(operator)
            dependency = step_id
    aggregate = max((item.risk for item in steps), default=RiskLevel.LOW)
    plan = MaterializedPlan(
        f"plan_{uuid.uuid4().hex}", goal, tuple(steps), aggregate, True,
        f"{len(steps) - 1} notwendige Routine-Aktion(en), {len(skipped)} Schritt(e) übersprungen.",
        PlanningTrace(
            goal.kind.value,
            facts=(f"routine={routine.routine_id}", f"entities={len(snapshots)}"),
            selected_operators=tuple(selected), skipped=tuple(skipped),
            bounds=_bounds(limits),
        ),
    )
    validate_plan_graph(plan, limits=limits)
    return plan


def materialize_target_states(
    goal: GoalModel,
    targets: Sequence[tuple[str, DesiredState, str]],
    entities: Iterable[EntitySnapshot],
    *,
    options: Mapping[str, object],
    is_admin: bool,
    user_id: str | None,
    limits: PlanningLimits = PlanningLimits(),
) -> MaterializedPlan:
    """Materialize explicit (entity, desired state) targets through V10.

    Used for proactive proposals: the caller supplies only desired states;
    this planner selects the closed operator, runs the Validator and the
    central ExecutionPolicy, skips already-satisfied targets and adds the
    mandatory verification for every action step.
    """
    snapshots = tuple(entities)
    by_id = {item.entity_id: item for item in snapshots}
    if not targets:
        raise ValueError("Der Vorschlag enthält kein Ziel")
    steps: list[PlanStep] = [
        PlanStep(
            "check-fresh-state", StepKind.CHECK,
            "Aktuelle Zustände, Verfügbarkeit und Fähigkeiten prüfen",
            invariants=("fresh_snapshot", "capability_checked", "policy_checked"),
        )
    ]
    dependency = steps[0].step_id
    selected: list[str] = []
    skipped: list[str] = []
    for entity_id, desired, description in targets:
        if len(steps) >= limits.max_steps:
            raise ValueError("Der Vorschlag überschreitet das begrenzte Planungsbudget")
        entity = by_id.get(entity_id)
        if entity is None:
            raise ValueError(f"Ziel {entity_id} ist nicht mehr vorhanden")
        if _desired_already_satisfied(entity, desired):
            skipped.append(f"{entity_id}:already_satisfied")
            continue
        action, effect = _action_for_desired_state(entity, desired)
        _validate_action(action, snapshots, options, is_admin, user_id)
        operator = _operator_id(action.domain, action.service)
        step_id = f"target-{len(steps)}"
        steps.append(
            PlanStep(
                step_id, StepKind.ACTION, description or f"{entity.friendly_name} anpassen",
                preconditions=("entity_available", "capability_checked"),
                effects=(effect,), dependencies=(dependency,), action=action,
                risk=classify_service_plan(action, snapshots), reversible=False,
                verification={entity_id: _verification_value(desired)},
                operator_id=operator, idempotent=_is_idempotent(action),
            )
        )
        selected.append(operator)
        dependency = step_id
    aggregate = max((item.risk for item in steps), default=RiskLevel.LOW)
    plan = MaterializedPlan(
        f"plan_{uuid.uuid4().hex}", goal, tuple(steps), aggregate, True,
        f"{len(steps) - 1} geprüfte Aktion(en), Gesamtrisiko {_RISK_WORDS_DE[aggregate.name]}.",
        _trace(goal, snapshots, tuple(selected), tuple(skipped), limits),
    )
    validate_plan_graph(plan, limits=limits)
    return plan


def materialize_comfort_profile(
    goal: GoalModel,
    profile: ComfortProfile,
    entities: Iterable[EntitySnapshot],
    *,
    options: Mapping[str, object],
    is_admin: bool,
    user_id: str | None,
) -> MaterializedPlan:
    """Plan only deviations from an explicitly confirmed area profile."""
    if not profile.confirmed or goal.scope.area_id != profile.area_id:
        raise ValueError("Für diesen Bereich ist kein bestätigtes Komfortprofil vorhanden")
    snapshots = tuple(entities)
    definitions: list[RoutineStepDefinition] = []
    if profile.brightness_min is not None and profile.brightness_max is not None:
        target = round((profile.brightness_min + profile.brightness_max) / 2)
        for entity in snapshots:
            if entity.area_id != profile.area_id or entity.domain != "light" or "BRIGHTNESS" not in entity.capabilities:
                continue
            current = entity.attributes.get("brightness")
            percent = round(float(current) * 100 / 255) if isinstance(current, (int, float)) else None
            if percent is None or not profile.brightness_min <= percent <= profile.brightness_max:
                definitions.append(
                    RoutineStepDefinition(
                        f"comfort-brightness-{entity.entity_id}",
                        GoalScope(entity_ids=(entity.entity_id,)),
                        DesiredState("brightness", target, "%"),
                        f"{entity.friendly_name} auf den bestätigten Helligkeitsbereich setzen",
                    )
                )
    if profile.temperature_min is not None and profile.temperature_max is not None:
        target_temperature = (profile.temperature_min + profile.temperature_max) / 2
        for entity in snapshots:
            if entity.area_id != profile.area_id or entity.domain != "climate":
                continue
            current = entity.attributes.get("current_temperature")
            if not isinstance(current, (int, float)) or not profile.temperature_min <= float(current) <= profile.temperature_max:
                definitions.append(
                    RoutineStepDefinition(
                        f"comfort-temperature-{entity.entity_id}",
                        GoalScope(entity_ids=(entity.entity_id,)),
                        DesiredState("temperature", target_temperature, "°C"),
                        f"{entity.friendly_name} auf den bestätigten Temperaturbereich setzen",
                    )
                )
    routine = RoutineDefinition(
        f"comfort:{profile.profile_id}", "Komfortprofil", profile.owner_user_id,
        tuple(definitions), True,
    )
    return materialize_routine(
        goal, routine, snapshots, options=options, is_admin=is_admin, user_id=user_id
    )


RefreshEntities = Callable[[], Awaitable[list[EntitySnapshot]]]


class ExecutionOutcome(Protocol):
    @property
    def executed(self) -> bool: ...

    @property
    def error(self) -> str | None: ...


ExecutePlan = Callable[[ServiceCallPlan, list[EntitySnapshot], bool], Awaitable[ExecutionOutcome]]
VerifyState = Callable[[str, str], Awaitable[bool]]
SchedulePlan = Callable[[PlanStep, list[EntitySnapshot], bool], Awaitable[ExecutionOutcome]]


class PlanExecutor:
    """Runs a materialized plan only through the shared policy executor."""

    def __init__(
        self, refresh_entities: RefreshEntities, execute_plan: ExecutePlan,
        verify_state: VerifyState, schedule_plan: SchedulePlan | None = None,
    ) -> None:
        self._refresh_entities = refresh_entities
        self._execute_plan = execute_plan
        self._verify_state = verify_state
        self._schedule_plan = schedule_plan

    async def execute(self, plan: MaterializedPlan, *, confirmed: bool) -> PlanResult:
        validate_plan_graph(plan)
        if plan.requires_confirmation and not confirmed:
            return PlanResult(plan.plan_id, PlanStatus.NEEDS_CONFIRMATION, ())
        results: list[StepResult] = []
        executed: list[PlanStep] = []
        completed_ids: set[str] = set()
        scheduled = False
        for step in plan.steps:
            fresh = await self._refresh_entities()
            if step.kind is StepKind.CHECK:
                results.append(StepResult(step.step_id, True, "Frischer Snapshot geladen."))
                completed_ids.add(step.step_id)
                continue
            if any(dependency not in completed_ids for dependency in step.dependencies):
                results.append(StepResult(step.step_id, False, "Eine Planabhängigkeit ist nicht erfüllt."))
                return PlanResult(plan.plan_id, _failed_status(executed), tuple(results))
            if step.kind not in {StepKind.ACTION, StepKind.NOTIFY}:
                results.append(StepResult(step.step_id, True, "Typisierter Kontrollschritt erfüllt."))
                completed_ids.add(step.step_id)
                continue
            action = step.action
            if action is None:
                results.append(StepResult(step.step_id, False, "Aktionsschritt ohne Aktion."))
                return PlanResult(plan.plan_id, _failed_status(executed), tuple(results))
            current = {item.entity_id: item for item in fresh}
            target_ids = (action.entity_id,) if isinstance(action.entity_id, str) else tuple(action.entity_id)
            if any(item not in current or current[item].state == "unavailable" for item in target_ids):
                results.append(StepResult(step.step_id, False, "Ziel unmittelbar vor Ausführung nicht verfügbar."))
                return PlanResult(plan.plan_id, _failed_status(executed), tuple(results))
            # Replanning is bounded to the same semantics: if every expected
            # state is already true, the action is skipped safely.
            if step.verification and all(
                entity_id in current and current[entity_id].state == expected
                for entity_id, expected in step.verification.items()
            ):
                results.append(StepResult(step.step_id, True, "Bereits erfüllt; keine Aktion nötig."))
                completed_ids.add(step.step_id)
                continue
            if error := validate_agent_service_plan(action):
                results.append(StepResult(step.step_id, False, error))
                return PlanResult(plan.plan_id, _failed_status(executed), tuple(results))
            if step.execute_at_local_time is not None:
                if self._schedule_plan is None:
                    results.append(StepResult(step.step_id, False, "Für den terminierten Schritt ist keine persistente Planung verfügbar."))
                    return PlanResult(plan.plan_id, _failed_status(executed), tuple(results))
                result = await self._schedule_plan(step, fresh, confirmed)
                if not result.executed:
                    results.append(StepResult(step.step_id, False, result.error or "Nicht geplant."))
                    return PlanResult(plan.plan_id, _failed_status(executed), tuple(results))
                scheduled = True
                completed_ids.add(step.step_id)
                results.append(StepResult(step.step_id, True, f"Persistent für {step.execute_at_local_time} Uhr geplant."))
                continue
            attempted_at = datetime.now(timezone.utc).isoformat()
            result = await self._execute_plan(action, fresh, confirmed)
            if not result.executed:
                results.append(StepResult(
                    step.step_id, False, result.error or "Nicht ausgeführt.",
                    attempted_at, None, attempted_at, None, False,
                ))
                compensated = await self._compensate(executed, confirmed)
                return PlanResult(
                    plan.plan_id, _failed_status(executed, compensated),
                    tuple(results), compensated,
                )
            service_accepted_at = datetime.now(timezone.utc).isoformat()
            verified = True
            for entity_id, state in step.verification.items():
                if not await self._verify_state(entity_id, state):
                    verified = False
                    break
            verified_at = datetime.now(timezone.utc).isoformat()
            if not verified:
                results.append(StepResult(
                    step.step_id, False, "Erwartete Wirkung nicht beobachtet.",
                    service_accepted_at, verified_at, attempted_at,
                    service_accepted_at, True,
                ))
                compensated = await self._compensate((*executed, step), confirmed)
                return PlanResult(
                    plan.plan_id, _failed_status(executed, compensated),
                    tuple(results), compensated,
                )
            executed.append(step)
            completed_ids.add(step.step_id)
            results.append(StepResult(
                step.step_id, True, "Ausgeführt und verifiziert.",
                service_accepted_at, verified_at, attempted_at,
                service_accepted_at, True,
            ))
        return PlanResult(
            plan.plan_id,
            PlanStatus.SCHEDULED if scheduled else PlanStatus.COMPLETED,
            tuple(results),
        )

    async def _compensate(self, executed: Iterable[PlanStep], confirmed: bool) -> tuple[str, ...]:
        compensated: list[str] = []
        for step in reversed(tuple(executed)):
            if not step.reversible or step.compensation is None:
                continue
            fresh = await self._refresh_entities()
            result = await self._execute_plan(step.compensation, fresh, confirmed)
            if result.executed:
                compensated.append(step.step_id)
        return tuple(compensated)


def _failed_status(
    successfully_verified: Sequence[PlanStep],
    compensated: Sequence[str] = (),
) -> PlanStatus:
    remaining_successes = len(successfully_verified) - len(compensated)
    return PlanStatus.PARTIAL_FAILURE if remaining_successes > 0 else PlanStatus.FAILED


def validate_plan_graph(plan: MaterializedPlan, *, limits: PlanningLimits = PlanningLimits()) -> None:
    if len(plan.steps) > limits.max_steps:
        raise ValueError("Plan exceeds the configured step bound")
    by_id = {step.step_id: step for step in plan.steps}
    if len(by_id) != len(plan.steps):
        raise ValueError("Plan step identifiers must be unique")
    if any(dep not in by_id for step in plan.steps for dep in step.dependencies):
        raise ValueError("Plan contains a dangling dependency")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str, depth: int) -> None:
        if depth > limits.max_depth:
            raise ValueError("Plan exceeds the configured depth bound")
        if step_id in visiting:
            raise ValueError("Plan contains a dependency cycle")
        if step_id in visited:
            return
        visiting.add(step_id)
        for dependency in by_id[step_id].dependencies:
            visit(dependency, depth + 1)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in by_id:
        visit(step_id, 1)


def _legacy_action(goal: GoalModel, entity: EntitySnapshot) -> tuple[ServiceCallPlan, str, str | None]:
    if goal.kind is GoalKind.SECURE_HOME and entity.domain == "lock":
        return ServiceCallPlan("lock", "lock", entity.entity_id, {}), "locked", None
    if goal.kind is GoalKind.SECURE_HOME and entity.domain == "cover":
        return ServiceCallPlan("cover", "close_cover", entity.entity_id, {}), "closed", "POSITION"
    if goal.kind is GoalKind.QUIET_MEDIA and entity.domain == "media_player":
        return ServiceCallPlan("media_player", "media_pause", entity.entity_id, {}), "paused", None
    off = goal.kind in {GoalKind.PREPARE_NIGHT, GoalKind.SAVE_UNOCCUPIED, GoalKind.PREPARE_AWAY}
    service = "turn_off" if off else "turn_on"
    return ServiceCallPlan("homeassistant", service, entity.entity_id, {}), ("off" if off else "on"), ("TURN_OFF" if off else "TURN_ON")


def _action_for_desired_state(entity: EntitySnapshot, desired: DesiredState) -> tuple[ServiceCallPlan, str]:
    value = desired.value
    if desired.property_name == "state" and value in {"on", "off"}:
        service = "turn_on" if value == "on" else "turn_off"
        capability = service.upper()
        if capability not in entity.capabilities:
            raise ValueError(f"{entity.friendly_name} unterstützt {capability} nicht")
        return ServiceCallPlan("homeassistant", service, entity.entity_id, {}), f"state={value}"
    if desired.property_name == "state" and value == "closed" and entity.domain == "cover":
        return ServiceCallPlan("cover", "close_cover", entity.entity_id, {}), "state=closed"
    if desired.property_name == "state" and value == "open" and entity.domain == "cover":
        return ServiceCallPlan("cover", "open_cover", entity.entity_id, {}), "state=open"
    if desired.property_name == "state" and value == "locked" and entity.domain == "lock":
        return ServiceCallPlan("lock", "lock", entity.entity_id, {}), "state=locked"
    if desired.property_name == "brightness" and entity.domain == "light" and isinstance(value, (int, float)):
        if not 0 <= float(value) <= 100 or "BRIGHTNESS" not in entity.capabilities:
            raise ValueError("Ungültige oder nicht unterstützte Helligkeit")
        return ServiceCallPlan("light", "turn_on", entity.entity_id, {"brightness_pct": round(float(value))}), f"brightness={value}"
    if desired.property_name == "temperature" and entity.domain == "climate" and isinstance(value, (int, float)):
        return ServiceCallPlan("climate", "set_temperature", entity.entity_id, {"temperature": float(value)}), f"temperature={value}"
    raise ValueError(f"Kein geschlossener Operator für {desired.property_name}={value}")


def _desired_already_satisfied(entity: EntitySnapshot, desired: DesiredState) -> bool:
    if desired.property_name == "state":
        return entity.state == str(desired.value)
    attribute = {
        "temperature": "temperature", "brightness": "brightness_pct",
        "color_temperature": "color_temp_kelvin", "humidity": "humidity",
        "position": "current_position",
    }.get(desired.property_name)
    current = entity.attributes.get(attribute) if attribute else None
    if desired.property_name == "brightness" and current is None:
        raw = entity.attributes.get("brightness")
        current = round(float(raw) * 100 / 255) if isinstance(raw, (int, float)) else None
    return isinstance(current, (int, float)) and isinstance(desired.value, (int, float)) and abs(float(current) - float(desired.value)) < 0.01


def _verification_value(desired: DesiredState) -> str:
    return str(desired.value) if desired.property_name == "state" else f"{desired.property_name}={desired.value}"


def observed_effect(entity: EntitySnapshot, expected: str) -> str | None:
    """Return the freshly observed value for a closed verification expression."""
    if "=" not in expected:
        return entity.state
    property_name, _separator, _value = expected.partition("=")
    attribute = {
        "temperature": "temperature",
        "brightness": "brightness_pct",
        "color_temperature": "color_temp_kelvin",
        "humidity": "humidity",
        "position": "current_position",
    }.get(property_name)
    current = entity.attributes.get(attribute) if attribute else None
    if property_name == "brightness" and current is None:
        raw = entity.attributes.get("brightness")
        current = round(float(raw) * 100 / 255) if isinstance(raw, (int, float)) else None
    return None if current is None else f"{property_name}={current}"


def effect_satisfied(entity: EntitySnapshot, expected: str) -> bool:
    """Compare state or numeric attributes without treating service acceptance as success."""
    observed = observed_effect(entity, expected)
    if observed is None:
        return False
    if "=" not in expected:
        return entity.state == expected
    expected_name, _separator, expected_value = expected.partition("=")
    observed_name, _separator, observed_value = observed.partition("=")
    if expected_name != observed_name:
        return False
    try:
        return abs(float(observed_value) - float(expected_value)) < 0.01
    except ValueError:
        return observed_value == expected_value


def _validate_action(
    action: ServiceCallPlan, entities: Sequence[EntitySnapshot], options: Mapping[str, object],
    is_admin: bool, user_id: str | None,
) -> None:
    if error := validate_agent_service_plan(action):
        raise ValueError(error)
    decision = evaluate_service_plan(action, tuple(entities), options, is_admin=is_admin, user_id=user_id)
    if decision.outcome is PolicyOutcome.DENY:
        raise PermissionError(decision.reason or "Aktion ist nicht erlaubt")


def _compensation(goal: GoalModel, entity: EntitySnapshot, action: ServiceCallPlan) -> ServiceCallPlan | None:
    if goal.kind in {GoalKind.SECURE_HOME, GoalKind.QUIET_MEDIA}:
        return None
    if action.service == "turn_off":
        return ServiceCallPlan("homeassistant", "turn_on", entity.entity_id, {})
    if action.service == "turn_on" and entity.state == "off":
        return ServiceCallPlan("homeassistant", "turn_off", entity.entity_id, {})
    raw = entity.attributes.get("brightness")
    if action.domain == "light" and isinstance(raw, (int, float)):
        prior = max(1, min(100, round(float(raw) * 100 / 255)))
        return ServiceCallPlan("light", "turn_on", entity.entity_id, {"brightness_pct": prior})
    return None


def _apply_exclusions(
    goal: GoalModel, entity_ids: Sequence[str], by_id: Mapping[str, EntitySnapshot]
) -> tuple[str, ...]:
    excluded_entities = set(goal.exclusions.entity_ids) | set(goal.exclusions.excluded_entity_ids) | set(goal.scope.excluded_entity_ids)
    excluded_areas = set(goal.exclusions.excluded_area_ids) | set(goal.scope.excluded_area_ids)
    return tuple(
        item for item in entity_ids
        if item not in excluded_entities and by_id[item].area_id not in excluded_areas
    )


def _operator_id(domain: str, service: str) -> str:
    return f"{domain}.{service}".upper().replace(".", "_")


def _is_idempotent(action: ServiceCallPlan) -> bool:
    return action.service in {
        "turn_on", "turn_off", "close_cover", "lock", "media_pause",
        "set_temperature", "set_cover_position",
    }


def _bounds(limits: PlanningLimits) -> tuple[tuple[str, int], ...]:
    return (
        ("max_depth", limits.max_depth), ("max_candidates", limits.max_candidates),
        ("max_expansions", limits.max_expansions), ("max_steps", limits.max_steps),
    )


def _trace(
    goal: GoalModel, entities: Sequence[EntitySnapshot], selected: tuple[str, ...],
    skipped: tuple[str, ...], limits: PlanningLimits,
) -> PlanningTrace:
    return PlanningTrace(
        goal.kind.value, (f"entities={len(entities)}",), selected, skipped, _bounds(limits)
    )


def _empty_verification() -> dict[str, str]:
    return {}


# Spoken German risk levels (F16): never the English enum name.
_RISK_WORDS_DE = {"LOW": "niedrig", "MEDIUM": "mittel", "HIGH": "hoch", "CRITICAL": "kritisch"}


__all__ = (
    "Goal", "GoalKind", "MaterializedPlan", "PlanExecutor", "PlanOperator",
    "PlanningLimits", "PlanningTrace", "PlanResult", "PlanStatus", "PlanStep",
    "StepKind", "StepResult", "materialize_comfort_profile", "materialize_goal",
    "materialize_routine", "validate_plan_graph", "effect_satisfied",
    "observed_effect",
)


def goal_run_from_plan_result(
    plan: MaterializedPlan,
    result: PlanResult,
    current_entities: Mapping[str, EntitySnapshot],
    *,
    run_id: str,
    user_id: str | None,
    person_entity_id: str | None,
    updated_at: str,
    evidence: tuple[str, ...] = (),
) -> GoalRun:
    """Build the authoritative GoalRun record for one executed V10 plan.

    Service acceptance is copied from ``StepResult.service_accepted`` and is
    never inferred from step success, timestamps or verification.  Effect
    evidence is recorded only for an accepted call or a satisfied no-op; a
    rejected or unreached step carries no device observation.
    """
    by_result = {item.step_id: item for item in result.steps}
    step_records: list[StepExecutionRecord] = []
    failures: list[FailureCode] = []
    for step in plan.steps:
        if step.kind is not StepKind.ACTION:
            continue
        outcome = by_result.get(step.step_id)
        verification_records: list[VerificationRecord] = []
        verification_items = (
            ()
            if step.execute_at_local_time is not None
            or outcome is None
            or outcome.service_accepted is False
            else step.verification.items()
        )
        for entity_id, expected in verification_items:
            observed_entity = current_entities.get(entity_id)
            observed = (
                observed_effect(observed_entity, expected)
                if observed_entity is not None
                else None
            )
            success = (
                observed_entity is not None
                and effect_satisfied(observed_entity, expected)
            )
            code = None
            if not success:
                code = (
                    FailureCode.TARGET_UNAVAILABLE
                    if observed in {None, "unavailable", "unknown"}
                    else FailureCode.WRONG_STATE
                )
                failures.append(code)
            verification_records.append(
                VerificationRecord(
                    entity_id, expected, observed, success, code,
                    outcome.verified_at if outcome is not None else None,
                )
            )
        rejected = outcome is not None and outcome.service_accepted is False
        if rejected:
            failures.append(FailureCode.SERVICE_ERROR)
        step_records.append(
            StepExecutionRecord(
                step.step_id, step.operator_id,
                tuple(step.verification), step.preconditions,
                outcome.service_accepted if outcome is not None else None,
                tuple(verification_records),
                (
                    FailureCode.SERVICE_ERROR
                    if rejected
                    else failures[-1]
                    if failures and verification_records
                    and not verification_records[-1].success
                    else None
                ),
                outcome.message if outcome is not None else "Nicht ausgeführt.",
                outcome.executed_at if outcome is not None else None,
                outcome.attempted_at if outcome is not None else None,
                outcome.service_accepted_at if outcome is not None else None,
            )
        )
    run = GoalRun.start(
        plan.goal, user_id=user_id, person_entity_id=person_entity_id,
        plan_id=plan.plan_id,
    )
    status = {
        PlanStatus.COMPLETED: GoalRunStatus.SUCCESS,
        PlanStatus.SCHEDULED: GoalRunStatus.SCHEDULED,
        PlanStatus.PARTIAL_FAILURE: GoalRunStatus.PARTIAL_FAILURE,
    }.get(result.status, GoalRunStatus.FAILURE)
    return GoalRun(
        run_id, run.goal_id, run.created_at, updated_at, run.source_utterance,
        user_id, person_entity_id, plan.goal, plan.plan_id,
        tuple(
            entity_id for step in plan.steps for entity_id in step.verification
        ),
        (), True, tuple(step_records), (), status,
        failures=tuple(dict.fromkeys(failures)) or (
            ()
            if result.status in {PlanStatus.COMPLETED, PlanStatus.SCHEDULED}
            else (FailureCode.SERVICE_ERROR,)
        ),
        evidence=(f"plan_status={result.status.value}", *evidence),
    )

