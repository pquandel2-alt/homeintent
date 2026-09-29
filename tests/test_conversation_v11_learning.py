from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.conversation_location import AreaSnapshot  # noqa: E402
from homeintent.automation_executor import AutomationExecutor  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.experience_store import ExperienceStore  # noqa: E402
from homeintent.goal_model import GoalKind, GoalModel  # noqa: E402
from homeintent.goal_run import (  # noqa: E402
    GoalRun,
    GoalRunStore,
    GoalRunStatus,
    StepExecutionRecord,
    VerificationRecord,
)
from homeintent.learning_manager import LearningManager  # noqa: E402
from homeintent.learning_policy import LearningMode, LearningPolicy  # noqa: E402
from homeintent.model_registry import (  # noqa: E402
    LearnedKind, LearnedModel, ModelHealth, ModelRegistry,
)
from homeintent.learning_policy import KnowledgeState  # noqa: E402
from homeintent.predictive_house_model import PredictiveHouseModel  # noqa: E402
from homeintent.thermal_model import (  # noqa: E402
    ThermalBinding,
    ThermalObservation,
    train_thermal_model,
)
from homeintent.profiles import ComfortProfile, ProfileStore  # noqa: E402
from homeintent.user_context import UserContextStore  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402


NOW = datetime(2026, 1, 5, 6, 30, tzinfo=timezone.utc)
ENTITIES = [
    EntitySnapshot(
        "light.kitchen", "Küchenlicht", "light", "off",
        area_id="kitchen", capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
    ),
    EntitySnapshot(
        "cover.kitchen", "Küchenrollladen", "cover", "closed",
        area_id="kitchen", capabilities=frozenset({"OPEN", "CLOSE"}),
    ),
    EntitySnapshot(
        "switch.coffee", "Kaffeemaschine", "switch", "off",
        area_id="kitchen", capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
    ),
]


def _agent(tmp_path, monkeypatch):
    policy = LearningPolicy(
        learning_mode=LearningMode.ASK,
        predictive_models_enabled=True,
        habit_discovery_enabled=True,
    )
    registry = ModelRegistry(tmp_path / "models.json", policy)
    experiences = ExperienceStore(tmp_path / "experiences.json", policy)
    house = PredictiveHouseModel(policy)
    learning = LearningManager(experiences, registry, house, policy)
    agent = NluConversationEntity(ConfigEntry())
    agent.hass = HomeAssistant()
    agent._runtime_data.learning_policy = policy
    agent._runtime_data.learned_models = registry
    agent._runtime_data.experiences = experiences
    agent._runtime_data.predictive_house = house
    agent._runtime_data.learning_manager = learning
    agent._runtime_data.profiles = ProfileStore(tmp_path / "profiles.json")
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda *_: ENTITIES
    )
    monkeypatch.setattr(
        ha_conversation, "build_device_snapshots", lambda *_: []
    )
    return agent, learning, registry


def _turn(agent, text: str, conversation_id: str = "v11"):
    return asyncio.run(agent._async_handle_message(
        ConversationInput(
            text=text, conversation_id=conversation_id,
            context=SimpleNamespace(user_id="philipp"),
        ),
        None,
    ))


def test_inferred_preference_requires_dialog_confirmation(tmp_path, monkeypatch):
    agent, learning, registry = _agent(tmp_path, monkeypatch)
    for index in range(10):
        asyncio.run(learning.async_observe_preference_selection(
            user_id="philipp", concept="Lampe", area_id="living_room",
            entity_id="light.floor" if index < 8 else "light.ceiling",
            observed_at=NOW + timedelta(minutes=index),
        ))

    suggestion = _turn(agent, "Was weißt du über meine Lichtpräferenzen?")
    assert "Soll das" in suggestion.response.speech
    before = asyncio.run(registry.async_list(kind=LearnedKind.PREFERENCE))[0]
    assert before.knowledge_state.value == "inferred"
    agent.hass.services.async_call.assert_not_awaited()

    saved = _turn(agent, "Ja")
    after = asyncio.run(registry.async_list(kind=LearnedKind.PREFERENCE))[0]
    assert saved.response.speech.startswith("Gespeichert")
    assert after.knowledge_state.value == "confirmed"
    assert after.confirmed_by == "philipp"
    agent.hass.services.async_call.assert_not_awaited()


def test_multi_user_preference_conflict_live_path_clarifies_and_shared_profile(
    tmp_path, monkeypatch,
):
    agent, _learning, _registry = _agent(tmp_path, monkeypatch)
    profiles = agent._runtime_data.profiles
    assert profiles is not None
    asyncio.run(profiles.async_save_comfort_profile(ComfortProfile(
        "comfort:philipp:living", "philipp", "living", 21, 21,
        confirmed=True,
    ), confirmed=True))
    asyncio.run(profiles.async_save_comfort_profile(ComfortProfile(
        "comfort:julia:living", "julia", "living", 23, 23,
        confirmed=True,
    ), confirmed=True))
    users = UserContextStore(tmp_path / "users.json")
    asyncio.run(users.async_set_user(
        "philipp", person_entity_id="person.philipp", confirmed=True
    ))
    asyncio.run(users.async_set_user(
        "julia", person_entity_id="person.julia", confirmed=True
    ))
    agent._runtime_data.user_contexts = users
    climate = EntitySnapshot(
        "climate.living", "Wohnzimmerheizung", "climate", "heat",
        area_id="living", area_name="Wohnzimmer",
        capabilities=frozenset({"SET_TEMPERATURE"}),
        attributes={"temperature": 19.0},
    )
    people = [
        EntitySnapshot("person.philipp", "Philipp", "person", "home"),
        EntitySnapshot("person.julia", "Julia", "person", "not_home"),
    ]
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda *_: [climate, *people]
    )
    monkeypatch.setattr(
        ha_conversation, "resolve_conversation_area",
        lambda *_: AreaSnapshot("living", "Wohnzimmer"),
    )
    philipp_only = _turn(agent, "Mach es hier gemütlicher.", "philipp-comfort")
    assert "Planvorschau" in philipp_only.response.speech
    philipp_plan = agent._runtime_data.dialog_manager.active("philipp-comfort").slots["plan"]
    assert philipp_plan.steps[-1].action.data["temperature"] == 21
    people[:] = [
        replace(people[0], state="not_home"), replace(people[1], state="home")
    ]
    julia_only = _turn(agent, "Mach es hier gemütlicher.", "julia-comfort")
    assert "Planvorschau" in julia_only.response.speech
    julia_plan = agent._runtime_data.dialog_manager.active("julia-comfort").slots["plan"]
    assert julia_plan.steps[-1].action.data["temperature"] == 23
    people[:] = [replace(people[0], state="home"), people[1]]
    conflict = _turn(agent, "Mach es hier gemütlicher.", "multi-comfort")
    assert "unterschiedliche" in conflict.response.speech
    agent.hass.services.async_call.assert_not_awaited()

    shared = _turn(
        agent, "Wenn wir beide da sind, nimm 22 Grad", "multi-comfort"
    )
    assert shared.response.speech.startswith("Gespeichert")
    selected = profiles.shared_comfort(
        area_id="living", user_ids=("philipp", "julia")
    )
    assert selected is not None
    assert selected.temperature_min == selected.temperature_max == 22
    agent.hass.services.async_call.assert_not_awaited()

    later = _turn(agent, "Mach es hier gemütlicher.", "multi-comfort-later")
    assert "Planvorschau" in later.response.speech
    agent.hass.services.async_call.assert_not_awaited()


def test_confirmed_preference_stays_user_area_and_live_entity_scoped(
    tmp_path, monkeypatch,
):
    agent, _learning, registry = _agent(tmp_path, monkeypatch)
    model = LearnedModel(
        "preference:lamp", LearnedKind.PREFERENCE, "Lampe",
        {"user_id": "philipp", "area_id": "living"},
        {"entity_id": "light.floor"}, KnowledgeState.CONFIRMED, 1.0, 10,
        NOW, NOW, ("explicit_user_feedback",), health=ModelHealth.VALID,
        confirmed_by="philipp",
    )
    asyncio.run(registry.async_upsert(model))
    floor = EntitySnapshot(
        "light.floor", "Stehlampe", "light", "on", area_id="living",
        capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
    )
    same = asyncio.run(agent._learning.async_apply_confirmed_preferences(
        [floor], area_id="living", user_id="philipp"
    ))
    assert "Lampe" in same[0].aliases
    other_user = asyncio.run(agent._learning.async_apply_confirmed_preferences(
        [floor], area_id="living", user_id="julia"
    ))
    assert "Lampe" not in other_user[0].aliases
    other_area = asyncio.run(agent._learning.async_apply_confirmed_preferences(
        [floor], area_id="kitchen", user_id="philipp"
    ))
    assert "Lampe" not in other_area[0].aliases
    moved = asyncio.run(agent._learning.async_apply_confirmed_preferences(
        [replace(floor, area_id="kitchen")],
        area_id="living", user_id="philipp",
    ))
    assert "Lampe" not in moved[0].aliases
    removed = asyncio.run(agent._learning.async_apply_confirmed_preferences(
        [], area_id="living", user_id="philipp"
    ))
    assert removed == []

def test_habit_acceptance_enters_v10_routine_confirmation_only(tmp_path, monkeypatch):
    agent, learning, registry = _agent(tmp_path, monkeypatch)
    goal = GoalModel(GoalKind.ACHIEVE_STATE, goal_id="morning")
    for index in range(15):
        stamp = NOW + timedelta(days=index)
        steps = tuple(
            StepExecutionRecord(
                f"s{step_index}", operator, (target,), (), True,
                (VerificationRecord(
                    target, expected, expected, True,
                    observed_at=stamp.isoformat(),
                ),),
            )
            for step_index, (operator, target, expected) in enumerate((
                ("LIGHT_TURN_ON", "light.kitchen", "on"),
                ("COVER_OPEN_COVER", "cover.kitchen", "open"),
                ("SWITCH_TURN_ON", "switch.coffee", "on"),
            ))
        )
        run = GoalRun(
            f"habit-{index}", "morning", stamp.isoformat(), stamp.isoformat(),
            "", "philipp", None, goal, "plan", (), (), True, steps, (),
            GoalRunStatus.SUCCESS,
        )
        asyncio.run(learning.async_observe_goal_run(run))

    suggestion = _turn(agent, "Welche Gewohnheiten hast du erkannt?", "habit")
    assert "Soll ich daraus eine Routine" in suggestion.response.speech
    preview = _turn(agent, "Ja", "habit")
    assert "Routinenvorschau" in preview.response.speech
    assert agent._runtime_data.profiles.routine(
        "Morgenroutine", user_id="philipp"
    ) is None
    saved = _turn(agent, "Ja", "habit")
    assert saved.response.speech.startswith("Gespeichert")
    assert agent._runtime_data.profiles.routine(
        "Morgenroutine", user_id="philipp"
    ) is not None
    habit = asyncio.run(registry.async_list(kind=LearnedKind.HABIT))[0]
    assert habit.parameters["creates_automation"] is False
    agent.hass.services.async_call.assert_not_awaited()


def test_thermal_deadline_creates_start_intermediate_and_final_one_shots(
    tmp_path, monkeypatch,
):
    policy = LearningPolicy(
        learning_mode=LearningMode.ASK,
        predictive_models_enabled=True,
        habit_discovery_enabled=False,
    )
    house = PredictiveHouseModel(policy)
    durations = (
        2820, 2880, 2760, 2940, 2850, 2910, 2790, 2870, 2830, 2920,
        2860, 2810, 2890, 2780, 2950, 2840, 2900, 2800, 2930, 2860,
    )
    observations = tuple(
        ThermalObservation(
            NOW - timedelta(days=index + 1),
            NOW - timedelta(days=index + 1) + timedelta(seconds=duration),
            "wohnzimmer", "sensor.living_temperature", "climate.living",
            19.0, 21.0, duration, True,
        )
        for index, duration in enumerate(durations)
    )
    model = train_thermal_model(
        ThermalBinding(
            "wohnzimmer", "sensor.living_temperature", "climate.living",
            confirmed=True,
        ),
        observations,
        policy,
        now=NOW,
    )
    assert model is not None
    house.install_thermal(model)
    climate = EntitySnapshot(
        "climate.living", "Wohnzimmerheizung", "climate", "heat",
        area_id="wohnzimmer", area_name="Wohnzimmer",
        attributes={"temperature": 19.0},
    )
    sensor = EntitySnapshot(
        "sensor.living_temperature", "Wohnzimmertemperatur", "sensor", "19",
        area_id="wohnzimmer", area_name="Wohnzimmer", unit="°C",
        device_class="temperature",
    )
    agent = NluConversationEntity(ConfigEntry())
    agent.hass = HomeAssistant()
    agent.hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    agent._runtime_data.predictive_house = house
    agent._runtime_data.learning_policy = policy
    agent._runtime_data.goal_runs = GoalRunStore(tmp_path / "runs.json")
    monkeypatch.setattr(
        AutomationExecutor, "_assign_homeintent_category", lambda *_args: None
    )
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda *_: [climate, sensor]
    )
    monkeypatch.setattr(ha_conversation, "build_device_snapshots", lambda *_: [])
    monkeypatch.setattr(ha_conversation.dt_util, "now", lambda: NOW)

    preview = _turn(
        agent,
        "Sorge dafür, dass es morgen um 7 Uhr im Wohnzimmer 21 Grad hat.",
        "thermal-deadline",
    )
    assert "Planvorschau" in preview.response.speech
    active = agent._runtime_data.dialog_manager.active("thermal-deadline")
    assert active is not None
    plan = active.slots["plan"]
    assert plan.adaptive_advice is not None
    assert plan.adaptive_advice.final_verification_at == NOW.replace(
        day=NOW.day + 1, hour=7, minute=0, second=0, microsecond=0
    )

    confirmed = _turn(agent, "Ja", "thermal-deadline")
    assert "persistent" in confirmed.response.speech.casefold()
    persisted = (tmp_path / "automations.yaml").read_text(encoding="utf-8")
    assert persisted.count("homeintent.thermal_deadline_checkpoint") == 3
    assert "phase: start" in persisted
    assert "phase: intermediate" in persisted
    assert "phase: final" in persisted
    runs = asyncio.run(agent._runtime_data.goal_runs.async_list())
    assert len(runs) == 1 and runs[0].status is GoalRunStatus.SCHEDULED
    assert agent.hass.services.async_call.await_count == 3
    assert all(
        call.args[:2] == ("automation", "reload")
        for call in agent.hass.services.async_call.await_args_list
    )


def test_remaining_time_question_is_not_hijacked_by_the_no_model_answer(tmp_path, monkeypatch):
    agent, _learning, _registry = _agent(tmp_path, monkeypatch)
    timer = EntitySnapshot(
        "timer.kueche", "Küchentimer", "timer", "active",
        attributes={"remaining": "00:04:15"},
    )
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda *_: [*ENTITIES, timer]
    )

    remaining = _turn(agent, "Wie lange läuft der Küchentimer noch?")
    predictive = _turn(agent, "Wie lange läuft die Kaffeemaschine normalerweise?")

    assert "Verbleibende Zeit: 4 Minuten und 15 Sekunden" in remaining.response.speech
    assert "kein belastbares Dauermodell" in predictive.response.speech
    agent.hass.services.async_call.assert_not_awaited()


def _morning_run(index: int, stamp: datetime) -> GoalRun:
    steps = tuple(
        StepExecutionRecord(
            f"s{step_index}", operator, (target,), (), True,
            (VerificationRecord(target, expected, expected, True, observed_at=stamp.isoformat()),),
        )
        for step_index, (operator, target, expected) in enumerate((
            ("LIGHT_TURN_ON", "light.kitchen", "on"),
            ("COVER_OPEN_COVER", "cover.kitchen", "open"),
            ("SWITCH_TURN_ON", "switch.coffee", "on"),
        ))
    )
    return GoalRun(
        f"lapse-{index}", "morning", stamp.isoformat(), stamp.isoformat(),
        "", "philipp", None, GoalModel(GoalKind.ACHIEVE_STATE, goal_id="morning"),
        "plan", (), (), True, steps, (), GoalRunStatus.SUCCESS,
    )


def test_two_weeks_time_lapse_habits_are_only_proposed_and_rejection_sticks(tmp_path, monkeypatch):
    """7.4.1: fourteen simulated days of use (the runs carry their own time).

    Habits are only ever proposed, no automation or routine is created
    without a "Ja", and a rejected proposal does not come back - neither on
    the next question nor after another week of the same behaviour.
    """
    agent, learning, registry = _agent(tmp_path, monkeypatch)
    for day in range(10):  # the proposal threshold is ten occurrences
        asyncio.run(learning.async_observe_goal_run(_morning_run(day, NOW + timedelta(days=day))))
    agent.hass.services.async_call.assert_not_awaited()
    first = _turn(agent, "Welche Gewohnheiten hast du erkannt?", "lapse")
    assert "Soll ich daraus eine Routine" in first.response.speech
    rejected = _turn(agent, "Nein", "lapse")
    assert "Routine" not in rejected.response.speech or "nicht" in rejected.response.speech
    for day in range(10, 14):
        asyncio.run(learning.async_observe_goal_run(_morning_run(day, NOW + timedelta(days=day))))
    again = _turn(agent, "Welche Gewohnheiten hast du erkannt?", "lapse-2")
    assert "Soll ich daraus eine Routine" not in again.response.speech
    assert agent._runtime_data.profiles.routine("Morgenroutine", user_id="philipp") is None
    for habit in asyncio.run(registry.async_list(kind=LearnedKind.HABIT)):
        assert habit.parameters.get("creates_automation") is False
    agent.hass.services.async_call.assert_not_awaited()
