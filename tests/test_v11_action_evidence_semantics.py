"""Pre-V12 gate: step success != service acceptance != effect success.

These tests drive the real PlanExecutor, the real policy-gated service
executor, GoalRun persistence, Experience extraction and Reliability
training.  A device service call is counted by an instrumented fake
``hass.services`` sink, never by a fixture constant.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import _ha_stub

_ha_stub.install()

from homeintent.effect_monitor import EffectMonitor
from homeintent.entities import EntitySnapshot
from homeintent.experience import extract_goal_run_experiences
from homeintent.experience_store import ExperienceStore
from homeintent.goal_model import GoalKind, GoalModel
from homeintent.goal_run import (
    EffectEvidenceState,
    FailureCode,
    GoalRun,
    GoalRunStatus,
    GoalRunStore,
    StepExecutionRecord,
    VerificationRecord,
)
from homeintent.learning_manager import LearningManager
from homeintent.learning_policy import LearningMode, LearningPolicy
from homeintent.model_registry import ModelRegistry
from homeintent.planner import (
    MaterializedPlan,
    PlanExecutor,
    PlanStatus,
    PlanStep,
    StepKind,
    StepResult,
)
from homeintent.predictive_house_model import PredictiveHouseModel
from homeintent.risk import RiskLevel
from homeintent.service_call import ServiceCallPlan
from homeintent.service_executor import async_execute_service_plan
from homeintent.thermal_tracker import ThermalExperienceTracker


NOW = datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc)


class ServiceSink:
    """Records every physical HA service call that reaches ``hass.services``."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail = fail

    async def async_call(
        self, domain: str, service: str, data: dict[str, Any], blocking: bool = False,
        context: Any = None,
    ) -> None:
        self.calls.append((domain, service, dict(data)))
        if self.fail:
            raise RuntimeError("Dienst abgelehnt")


def _policy() -> LearningPolicy:
    return replace(LearningPolicy(
        learning_mode=LearningMode.ASK,
        predictive_models_enabled=True,
        habit_discovery_enabled=True,
    ), minimum_model_samples=2, usable_model_samples=2)


def _plan(entity_id: str, service: str, expected: str, *, at: str | None = None) -> MaterializedPlan:
    domain = entity_id.partition(".")[0]
    data: dict[str, object] = {"temperature": 23.0} if service == "set_temperature" else {}
    step = PlanStep(
        "step", StepKind.ACTION, "Aktion",
        action=ServiceCallPlan(domain, service, entity_id, data),
        verification={entity_id: expected},
        operator_id=f"{domain}_{service}".upper(),
        execute_at_local_time=at,
    )
    return MaterializedPlan(
        "plan", GoalModel(GoalKind.ACHIEVE_STATE, goal_id="goal"), (step,),
        RiskLevel.LOW, False, "Vorschau",
    )


class Harness:
    """Real PlanExecutor over the real policy-gated service executor."""

    def __init__(
        self, entities: list[EntitySnapshot], *, fail: bool = False,
        effect_applies: bool = True, tracker: ThermalExperienceTracker | None = None,
    ) -> None:
        self.entities = {item.entity_id: item for item in entities}
        self.sink = ServiceSink(fail=fail)
        self.hass = SimpleNamespace(services=self.sink)
        self.effect_applies = effect_applies
        self.monitor = EffectMonitor()
        self.observed_actions: list[ServiceCallPlan] = []
        if tracker is not None:
            def _observe(plan: ServiceCallPlan, occurred_at: datetime) -> None:
                self.observed_actions.append(plan)
                tracker.observe_action(
                    plan, tuple(self.entities.values()), occurred_at=occurred_at
                )
        else:
            def _observe(plan: ServiceCallPlan, occurred_at: datetime) -> None:
                self.observed_actions.append(plan)
        self.monitor.set_action_observer(_observe)
        self.scheduled: list[str] = []

    async def refresh(self) -> list[EntitySnapshot]:
        return list(self.entities.values())

    async def execute(self, action: ServiceCallPlan, fresh: list[EntitySnapshot], confirmed: bool):
        result = await async_execute_service_plan(
            self.hass,  # type: ignore[arg-type]
            action, fresh, {}, is_admin=True, user_id="philipp",
            confirmed=confirmed, effect_monitor=self.monitor,
        )
        if result.executed and self.effect_applies and isinstance(action.entity_id, str):
            state = "on" if action.service == "turn_on" else "off"
            self.entities[action.entity_id] = replace(
                self.entities[action.entity_id], state=state
            )
        return result

    async def verify(self, entity_id: str, expected: str) -> bool:
        return self.entities[entity_id].state == expected

    async def schedule(self, step: PlanStep, _fresh: list[EntitySnapshot], _confirmed: bool):
        self.scheduled.append(step.step_id)
        return SimpleNamespace(executed=True, error=None)

    def run(self, plan: MaterializedPlan) -> StepResult:
        async def _go():
            try:
                return await PlanExecutor(
                    self.refresh, self.execute, self.verify, self.schedule
                ).execute(plan, confirmed=True)
            finally:
                await self.monitor.async_close()
        result = asyncio.run(_go())
        self.plan_status = result.status
        return result.steps[-1]


def _goal_run(outcome: StepResult, *, run_id: str, observed: str | None,
              expected: str = "on", status: GoalRunStatus | None = None) -> GoalRun:
    """Mirror conversation.py GoalRun construction from a real StepResult."""
    verification: tuple[VerificationRecord, ...] = ()
    if outcome.service_accepted is not False and observed is not None:
        verification = (VerificationRecord(
            "light.a", expected, observed, observed == expected,
            None if observed == expected else FailureCode.WRONG_STATE,
            outcome.verified_at or NOW.isoformat(),
        ),)
    return GoalRun(
        run_id, "goal", NOW.isoformat(), NOW.isoformat(), "", "philipp", None,
        GoalModel(GoalKind.ACHIEVE_STATE, goal_id="goal"), "plan",
        ("light.a",), (), True,
        (StepExecutionRecord(
            outcome.step_id, "LIGHT_TURN_ON", ("light.a",), (),
            outcome.service_accepted, verification,
            FailureCode.SERVICE_ERROR if outcome.service_accepted is False else None,
            outcome.message, outcome.executed_at, outcome.attempted_at,
            outcome.service_accepted_at,
        ),), (),
        status or (GoalRunStatus.SUCCESS if outcome.success else GoalRunStatus.FAILURE),
    )


def _light(state: str) -> EntitySnapshot:
    return EntitySnapshot(
        "light.a", "Licht", "light", state,
        capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
    )


# --- A) no action required -------------------------------------------------

def test_already_satisfied_step_does_not_claim_service_acceptance():
    harness = Harness([_light("on")])
    outcome = harness.run(_plan("light.a", "turn_on", "on"))
    assert outcome.success is True
    assert outcome.service_accepted is None
    assert outcome.attempted_at is None
    assert outcome.service_accepted_at is None
    assert harness.sink.calls == []


def test_already_satisfied_step_creates_no_action_experience():
    harness = Harness([_light("on")])
    outcome = harness.run(_plan("light.a", "turn_on", "on"))
    run = _goal_run(outcome, run_id="noop", observed="on")
    # The GoalRun keeps the no-op for explainability ...
    assert run.steps[0].service_accepted is None
    assert run.steps[0].verification[0].success is True
    # ... but a satisfied goal state is not a device action success.
    assert extract_goal_run_experiences(run) == ()


def test_noop_does_not_change_reliability(tmp_path):
    policy = _policy()
    manager = LearningManager(
        ExperienceStore(tmp_path / "e.json", policy),
        ModelRegistry(tmp_path / "m.json", policy),
        PredictiveHouseModel(policy), policy,
    )
    for index in range(8):
        harness = Harness([_light("off")])
        asyncio.run(manager.async_observe_goal_run(_goal_run(
            harness.run(_plan("light.a", "turn_on", "on")),
            run_id=f"ok-{index}", observed="on",
        )))
    failing = Harness([_light("off")], effect_applies=False)
    asyncio.run(manager.async_observe_goal_run(_goal_run(
        failing.run(_plan("light.a", "turn_on", "on")),
        run_id="effect-failed", observed="off",
    )))
    for index in range(10):
        noop = Harness([_light("on")])
        asyncio.run(manager.async_observe_goal_run(_goal_run(
            noop.run(_plan("light.a", "turn_on", "on")),
            run_id=f"noop-{index}", observed="on",
        )))
    for index in range(3):
        accepted = Harness([_light("off")])
        outcome = accepted.run(_plan("light.a", "turn_on", "on"))
        asyncio.run(manager.async_observe_goal_run(_goal_run(
            outcome, run_id=f"unverified-{index}", observed=None,
            status=GoalRunStatus.PARTIAL_FAILURE,
        )))
    model = asyncio.run(manager.models.async_get("reliability:LIGHT_TURN_ON:light.a"))
    assert model is not None
    assert model.sample_count == 9
    assert model.parameters["success_rate"] == 8 / 9
    records = asyncio.run(manager.experiences.async_list())
    assert not any(item.run_id.startswith("noop-") for item in records)


# --- B) accepted + verified -------------------------------------------------

def test_service_accepted_and_verified_success():
    harness = Harness([_light("off")])
    outcome = harness.run(_plan("light.a", "turn_on", "on"))
    assert outcome.success is True
    assert outcome.service_accepted is True
    assert outcome.attempted_at is not None
    assert outcome.service_accepted_at is not None
    assert outcome.verified_at is not None
    assert harness.sink.calls == [("light", "turn_on", {"entity_id": "light.a"})]
    record = extract_goal_run_experiences(_goal_run(outcome, run_id="ok", observed="on"))[0]
    assert record.effect.evidence_state is EffectEvidenceState.VERIFIED_SUCCESS


# --- C) accepted + effect failed -------------------------------------------

def test_service_accepted_but_effect_failed_preserves_acceptance():
    harness = Harness([_light("off")], effect_applies=False)
    outcome = harness.run(_plan("light.a", "turn_on", "on"))
    assert outcome.success is False
    assert outcome.service_accepted is True
    assert outcome.service_accepted_at is not None
    assert len(harness.sink.calls) == 1
    record = extract_goal_run_experiences(
        _goal_run(outcome, run_id="bad", observed="off")
    )[0]
    assert record.effect.evidence_state is EffectEvidenceState.VERIFIED_FAILURE


# --- D) service rejected ----------------------------------------------------

def test_service_execution_failure_is_not_effect_failure():
    harness = Harness([_light("off")], fail=True)
    outcome = harness.run(_plan("light.a", "turn_on", "on"))
    assert outcome.success is False
    assert outcome.service_accepted is False
    assert outcome.attempted_at is not None
    assert outcome.service_accepted_at is None
    assert len(harness.sink.calls) == 1  # attempted, then raised
    run = _goal_run(outcome, run_id="rejected", observed="off")
    assert run.steps[0].failure_code is FailureCode.SERVICE_ERROR
    assert run.steps[0].verification == ()
    assert extract_goal_run_experiences(run) == ()


# --- E) future scheduled action ----------------------------------------------

def test_scheduled_future_action_has_no_device_acceptance_yet():
    harness = Harness([_light("off")])
    outcome = harness.run(_plan("light.a", "turn_on", "on", at="22:00"))
    assert harness.plan_status is PlanStatus.SCHEDULED
    assert outcome.success is True
    assert outcome.service_accepted is None
    assert outcome.service_accepted_at is None
    assert harness.sink.calls == []
    assert harness.scheduled == ["step"]
    assert extract_goal_run_experiences(
        _goal_run(outcome, run_id="later", observed=None,
                  status=GoalRunStatus.SUCCESS)
    ) == ()


def test_effect_latency_requires_real_accepted_service():
    base = _goal_run(
        StepResult("step", True, "ok", NOW.isoformat(), NOW.isoformat(),
                   NOW.isoformat(), NOW.isoformat(), True),
        run_id="latency", observed="on",
    )
    observed_at = (NOW + timedelta(seconds=3)).isoformat()
    accepted = replace(base, steps=(replace(
        base.steps[0],
        verification=(replace(base.steps[0].verification[0], observed_at=observed_at),),
    ),))
    assert extract_goal_run_experiences(accepted)[0].effect.latency_seconds == 3.0
    # A timestamp alone is never acceptance: without authoritative acceptance
    # there is no experience and therefore no latency sample at all.
    for flag in (None, False):
        forged = replace(accepted, steps=(replace(accepted.steps[0], service_accepted=flag),))
        assert extract_goal_run_experiences(forged) == ()


def test_goalrun_preserves_acceptance_independent_of_success(tmp_path):
    store = GoalRunStore(tmp_path / "runs.json")
    failing = Harness([_light("off")], effect_applies=False)
    outcome = failing.run(_plan("light.a", "turn_on", "on"))
    run = _goal_run(outcome, run_id="persisted", observed="off")
    asyncio.run(store.async_append(run))
    loaded = asyncio.run(GoalRunStore(tmp_path / "runs.json").async_list())
    assert loaded[-1].steps[0].service_accepted is True
    assert loaded[-1].status is GoalRunStatus.FAILURE
    noop = _goal_run(Harness([_light("on")]).run(_plan("light.a", "turn_on", "on")),
                     run_id="persisted-noop", observed="on")
    asyncio.run(store.async_append(noop))
    loaded = asyncio.run(GoalRunStore(tmp_path / "runs.json").async_list())
    assert loaded[-1].steps[0].service_accepted is None
    assert loaded[-1].status is GoalRunStatus.SUCCESS


def test_legacy_goalrun_without_new_fields_loads_safely(tmp_path):
    store = GoalRunStore(tmp_path / "runs.json")
    asyncio.run(store.async_append(
        _goal_run(StepResult("step", True, "ok", service_accepted=True),
                  run_id="legacy", observed="on")
    ))
    path = tmp_path / "runs.json"
    document = json.loads(path.read_text())
    runs = document["runs"] if isinstance(document, dict) else document
    for raw in runs:
        for step in raw["steps"]:
            for key in ("service_accepted", "attempted_at", "service_accepted_at"):
                step.pop(key, None)
    path.write_text(json.dumps(document))
    loaded = asyncio.run(GoalRunStore(path).async_list())
    assert loaded[-1].steps[0].service_accepted is None
    assert extract_goal_run_experiences(loaded[-1]) == ()
    assert StepResult("x", True, "legacy").service_accepted is None


# --- thermal tracker start gating --------------------------------------------

def _thermal_entities(state: str = "heat", temperature: float = 19.0) -> list[EntitySnapshot]:
    return [
        EntitySnapshot(
            "climate.living", "Heizung", "climate", state, area_id="living",
            attributes={"temperature": temperature, "current_temperature": 19.0},
        ),
        EntitySnapshot(
            "sensor.living", "Temperatur", "sensor", "19.0", area_id="living",
            device_class="temperature", unit="°C",
        ),
    ]


def _tracker(tmp_path) -> ThermalExperienceTracker:
    policy = _policy()
    return ThermalExperienceTracker(LearningManager(
        ExperienceStore(tmp_path / "e.json", policy),
        ModelRegistry(tmp_path / "m.json", policy),
        PredictiveHouseModel(policy), policy,
    ))


def _climate_plan() -> MaterializedPlan:
    step = PlanStep(
        "step", StepKind.ACTION, "Heizung",
        action=ServiceCallPlan("climate", "set_temperature", "climate.living",
                               {"temperature": 23.0}),
        verification={"climate.living": "heat"},
        operator_id="CLIMATE_SET_TEMPERATURE",
    )
    return MaterializedPlan(
        "plan", GoalModel(GoalKind.ACHIEVE_STATE, goal_id="goal"), (step,),
        RiskLevel.LOW, False, "Vorschau",
    )


def test_thermal_tracker_does_not_start_for_noop(tmp_path):
    tracker = _tracker(tmp_path)
    harness = Harness(_thermal_entities(), tracker=tracker)
    outcome = harness.run(_climate_plan())
    assert outcome.service_accepted is None
    assert harness.sink.calls == []
    assert harness.observed_actions == []
    assert tracker.active == ()


def test_thermal_tracker_does_not_start_for_rejected_action(tmp_path):
    tracker = _tracker(tmp_path)
    harness = Harness(_thermal_entities(state="off"), tracker=tracker, fail=True)
    outcome = harness.run(_climate_plan())
    assert outcome.service_accepted is False
    assert len(harness.sink.calls) == 1
    assert harness.observed_actions == []
    assert tracker.active == ()


def test_thermal_tracker_positive_control_starts_for_accepted_action(tmp_path):
    tracker = _tracker(tmp_path)
    harness = Harness(_thermal_entities(state="off"), tracker=tracker)
    harness.run(_climate_plan())
    assert len(harness.sink.calls) == 1
    assert len(harness.observed_actions) == 1
    assert [cycle.binding.climate_entity_id for cycle in tracker.active] == ["climate.living"]
