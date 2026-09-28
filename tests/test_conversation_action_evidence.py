"""Live conversation.py plan path: GoalRun acceptance comes from StepResult."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.dialog_manager import DialogPriority, DialogTaskKind  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.experience import extract_goal_run_experiences  # noqa: E402
from homeintent.goal_model import GoalKind, GoalModel  # noqa: E402
from homeintent.goal_run import FailureCode, GoalRunStore  # noqa: E402
from homeintent.planner import MaterializedPlan, PlanStep, StepKind  # noqa: E402
from homeintent.risk import RiskLevel  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402


def _entities() -> dict[str, EntitySnapshot]:
    return {
        "light.kitchen": EntitySnapshot(
            "light.kitchen", "Küchenlicht", "light", "on",
            capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
        ),
        "light.hall": EntitySnapshot(
            "light.hall", "Flurlicht", "light", "off",
            capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
        ),
    }


def _plan() -> MaterializedPlan:
    return MaterializedPlan(
        "plan-evidence", GoalModel(GoalKind.PREPARE_ROUTINE, goal_id="goal-evidence"),
        (
            PlanStep(
                "light", StepKind.ACTION, "Küchenlicht an",
                action=ServiceCallPlan("light", "turn_on", "light.kitchen", {}),
                verification={"light.kitchen": "on"}, operator_id="LIGHT_TURN_ON",
            ),
            PlanStep(
                "hall", StepKind.ACTION, "Flurlicht an",
                action=ServiceCallPlan("light", "turn_on", "light.hall", {}),
                verification={"light.hall": "on"}, operator_id="LIGHT_TURN_ON",
            ),
        ),
        RiskLevel.LOW, True, "Vorschau",
    )


def _agent(tmp_path, monkeypatch, *, fail: bool):
    agent = NluConversationEntity(ConfigEntry())
    agent.hass = HomeAssistant()
    agent._runtime_data.goal_runs = GoalRunStore(tmp_path / "runs.json")
    agent._runtime_data.effect_monitor.timeout = timedelta(milliseconds=50)
    states = _entities()

    async def _call(domain, service, data, blocking=False, context=None):
        if fail:
            raise RuntimeError("abgelehnt")
        entity_id = data["entity_id"]
        states[entity_id] = replace(states[entity_id], state="on")

    agent.hass.services.async_call.side_effect = _call
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: list(states.values()))
    monkeypatch.setattr(ha_conversation, "build_device_snapshots", lambda *_: [])
    agent._runtime_data.dialog_manager.create(
        "evidence", "goal-plan", DialogTaskKind.PLAN_CONFIRMATION,
        DialogPriority.CONFIRMATION, slots={"plan": _plan()},
        reason="test", requested_by_user_id="philipp",
    )
    return agent


def _confirm(agent):
    return asyncio.run(agent._async_handle_message(
        ConversationInput(
            text="Ja", conversation_id="evidence",
            context=SimpleNamespace(user_id="philipp"),
        ),
        None,
    ))


def test_live_goalrun_noop_and_accepted_steps_are_distinct(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, fail=False)
    result = _confirm(agent)
    assert "vollständig ausgeführt" in result.response.speech
    calls = [call.args[:2] for call in agent.hass.services.async_call.await_args_list]
    assert calls == [("light", "turn_on")]  # the satisfied light was not called
    run = asyncio.run(agent._runtime_data.goal_runs.async_list())[-1]
    by_id = {step.step_id: step for step in run.steps}
    assert by_id["light"].service_accepted is None
    assert by_id["light"].attempted_at is None
    assert by_id["hall"].service_accepted is True
    assert by_id["hall"].service_accepted_at is not None
    experiences = extract_goal_run_experiences(run)
    assert [item.action.target_id for item in experiences] == ["light.hall"]


def test_live_goalrun_rejected_service_is_service_error_not_effect(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, fail=True)
    _confirm(agent)
    run = asyncio.run(agent._runtime_data.goal_runs.async_list())[-1]
    hall = next(step for step in run.steps if step.step_id == "hall")
    assert hall.service_accepted is False
    assert hall.service_accepted_at is None
    assert hall.verification == ()
    assert hall.failure_code is FailureCode.SERVICE_ERROR
    assert FailureCode.WRONG_STATE not in run.failures
    assert extract_goal_run_experiences(run) == ()
