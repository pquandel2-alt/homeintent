"""The only V12 path to a device: hand an accepted goal to the V10 pipeline.

V12 never builds a ``ServiceCallPlan``.  It passes explicit desired target
states to ``planner.materialize_target_states`` (operator selection,
Validator, ExecutionPolicy), reserves the ``ExecutionCoordinator``, runs the
existing ``PlanExecutor`` whose every write goes through the shared
policy-gated service executor, verifies each effect, records the GoalRun
via ``planner.goal_run_from_plan_result`` and appends it to the existing
``GoalRunStore`` (whose listener feeds V11 Experience).

``interactive_confirmed`` is True only for an explicit user answer.  For a
StandingPermission it is False, so the service executor refuses anything
the ExecutionPolicy would merely *confirm*: a permission cannot upgrade a
CONFIRM decision into an unattended action.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Awaitable, Callable, Mapping, Protocol

from .entities import EntitySnapshot
from .execution_coordinator import ConflictOutcome, ExecutionCoordinator
from .goal_model import DesiredState, GoalKind, GoalModel, GoalProvenance, GoalScope
from .goal_run import GoalRun, GoalRunStore
from .planner import (
    PlanExecutor,
    PlanStatus,
    StepKind,
    goal_run_from_plan_result,
    materialize_target_states,
)
from .proactive_model import ProposedGoal
from .service_call import ServiceCallPlan


class _Outcome(Protocol):
    @property
    def executed(self) -> bool: ...

    @property
    def error(self) -> str | None: ...


RefreshEntities = Callable[[], Awaitable[list[EntitySnapshot]]]
ExecuteService = Callable[[ServiceCallPlan, list[EntitySnapshot], bool, str | None, bool], Awaitable[_Outcome]]
VerifyState = Callable[[str, str], Awaitable[bool]]


class ProactiveExecutionStatus(StrEnum):
    EXECUTED = "executed"
    FAILED = "failed"
    BLOCKED = "blocked"
    STALE = "stale"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class ProactiveExecutionResult:
    status: ProactiveExecutionStatus
    run: GoalRun | None
    plan_status: PlanStatus | None
    reason: str


class V10ProposalRunner:
    def __init__(
        self,
        *,
        refresh: RefreshEntities,
        execute_service: ExecuteService,
        verify: VerifyState,
        coordinator: ExecutionCoordinator,
        goal_runs: GoalRunStore | None,
        options: Mapping[str, object],
    ) -> None:
        self._refresh = refresh
        self._execute_service = execute_service
        self._verify = verify
        self._coordinator = coordinator
        self._goal_runs = goal_runs
        self._options = options

    async def async_execute(
        self,
        goal: ProposedGoal,
        *,
        user_id: str | None,
        is_admin: bool,
        person_entity_id: str | None,
        interactive_confirmed: bool,
        provenance: str,
        now: datetime,
    ) -> ProactiveExecutionResult:
        fresh = await self._refresh()
        goal_model = GoalModel(
            GoalKind.ACHIEVE_STATE,
            goal_id=f"goal_{uuid.uuid4().hex}",
            scope=GoalScope(entity_ids=tuple(item.entity_id for item in goal.targets)),
            desired_states=tuple(
                DesiredState("state", item.desired_state) for item in goal.targets
            ),
            provenance=GoalProvenance(provenance, user_id, confirmed=True),
        )
        try:
            plan = materialize_target_states(
                goal_model,
                tuple(
                    (item.entity_id, DesiredState("state", item.desired_state),
                     item.name or item.entity_id)
                    for item in goal.targets
                ),
                fresh,
                options=self._options,
                is_admin=is_admin,
                user_id=user_id,
            )
        except PermissionError:
            return ProactiveExecutionResult(
                ProactiveExecutionStatus.BLOCKED, None, None, "policy_denied"
            )
        except ValueError:
            return ProactiveExecutionResult(
                ProactiveExecutionStatus.STALE, None, None, "plan_stale"
            )
        if not any(step.kind is StepKind.ACTION for step in plan.steps):
            # Desired state already holds: a no-op is not a device action.
            return ProactiveExecutionResult(
                ProactiveExecutionStatus.STALE, None, None, "already_satisfied",
            )
        run_id = f"run_{uuid.uuid4().hex}"
        reservation = await self._coordinator.async_acquire(run_id, plan)
        if reservation.outcome is ConflictOutcome.CONFLICT:
            return ProactiveExecutionResult(
                ProactiveExecutionStatus.CONFLICT, None, None, "execution_conflict",
            )

        async def execute(action: ServiceCallPlan, current: list[EntitySnapshot], _plan_confirmed: bool):
            return await self._execute_service(
                action, current, interactive_confirmed, user_id, is_admin,
            )

        try:
            result = await PlanExecutor(self._refresh, execute, self._verify).execute(
                plan, confirmed=True,
            )
        finally:
            await self._coordinator.async_release(run_id)
        after = {item.entity_id: item for item in await self._refresh()}
        run = goal_run_from_plan_result(
            plan, result, after, run_id=run_id, user_id=user_id,
            person_entity_id=person_entity_id, updated_at=now.isoformat(),
            evidence=(f"proactive:{provenance}",),
        )
        if self._goal_runs is not None:
            await self._goal_runs.async_append(run)
        if result.status is PlanStatus.COMPLETED:
            return ProactiveExecutionResult(ProactiveExecutionStatus.EXECUTED, run, result.status, "verified")
        rejected = any(step.service_accepted is False for step in result.steps)
        return ProactiveExecutionResult(
            ProactiveExecutionStatus.BLOCKED if rejected and not any(
                step.service_accepted for step in result.steps
            ) else ProactiveExecutionStatus.FAILED,
            run, result.status,
            next((step.message for step in result.steps if not step.success), "failed"),
        )


__all__ = (
    "ProactiveExecutionResult",
    "ProactiveExecutionStatus",
    "V10ProposalRunner",
)
