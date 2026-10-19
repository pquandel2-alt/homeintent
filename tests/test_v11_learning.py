from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homeintent.adaptive_planning import advise_deadline_goal, apply_advice
from homeintent.experience import (
    ExperienceAction,
    ExperienceContext,
    ExperienceEffect,
    ExperienceProvenance,
    ExperienceQuality,
    ExperienceRecord,
    extract_goal_run_experiences,
)
from homeintent.experience_store import ExperienceStore
from homeintent.goal_model import (
    DesiredState,
    GoalKind,
    GoalModel,
    GoalScope,
    TemporalGoal,
)
from homeintent.goal_run import (
    FailureCode,
    GoalRun,
    GoalRunStore,
    GoalRunStatus,
    StepExecutionRecord,
    VerificationRecord,
)
from homeintent.habit_discovery import (
    HabitSequenceObservation,
    SuggestionStatus,
    discover_habit,
    routine_from_habit_model,
    update_suggestion,
)
from homeintent.house_graph import (
    ConfidenceClass,
    FactProvenance,
    GraphNode,
    HouseGraph,
    NodeKind,
    RelationKind,
)
from homeintent.learning_policy import KnowledgeState, LearningMode, LearningPolicy
from homeintent.learning_policy import ConfidenceBand
from homeintent.learning_manager import LearningManager
from homeintent.model_registry import (
    LearnedKind,
    LearnedModel,
    ModelHealth,
    ModelRegistry,
    UpsertResult,
)
from homeintent.preferences import (
    ConflictResolution,
    LearnedPreference,
    PreferenceContext,
    confirm_preference,
    infer_preference,
    resolve_preferences,
)
from homeintent.predictive_house_model import PredictiveHouseModel
from homeintent.prediction import PredictionStatus
from homeintent.statistical_models import (
    adaptive_verification_timeout,
    evaluate_latency_anomaly,
    train_effect_timing,
    train_reliability,
)
from homeintent.thermal_model import (
    ThermalBinding,
    ThermalObservation,
    detect_thermal_drift,
    train_thermal_model,
    thermal_model_from_learned,
    thermal_model_to_learned,
    thermal_observation_to_experience,
    thermal_observations_from_experiences,
)
from homeintent.thermal_tracker import ThermalExperienceTracker
from homeintent.thermal_deadline import (
    ThermalCheckpointPhase,
    ThermalDeadlineCheckpoint,
    append_start_checkpoint,
    async_process_thermal_checkpoint,
    checkpoint_automation_config,
)
from homeintent.entities import EntitySnapshot
from homeintent.service_call import ServiceCallPlan


NOW = datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)


def _policy(**changes: object) -> LearningPolicy:
    base = LearningPolicy(
        learning_mode=LearningMode.ASK,
        predictive_models_enabled=True,
        habit_discovery_enabled=True,
    )
    return replace(base, **changes)


def _experience(index: int, *, latency: float = 2.0) -> ExperienceRecord:
    return ExperienceRecord(
        f"exp_{index}", NOW + timedelta(seconds=index), "goal", f"run_{index}",
        ExperienceContext("living_room", "light.a", "light", "philipp"),
        ExperienceAction("light.turn_on", "light.a"), {}, {"state": "on"},
        ExperienceEffect("on", "on", latency, True),
        (f"goal_run:run_{index}",), ExperienceProvenance.GOAL_RUN,
        ExperienceQuality.COMPLETE,
    )


def test_experience_store_is_bounded_deduped_restart_safe_and_corruption_tolerant(tmp_path):
    path = tmp_path / "experiences.json"
    policy = _policy(experience_limit=3, retention_days=3650)
    store = ExperienceStore(path, policy)
    assert asyncio.run(store.async_append(_experience(1)))
    assert not asyncio.run(store.async_append(_experience(1)))
    for index in range(2, 6):
        asyncio.run(store.async_append(_experience(index)))
    assert [item.experience_id for item in asyncio.run(ExperienceStore(path, policy).async_list())] == [
        "exp_3", "exp_4", "exp_5"
    ]
    path.write_text("{broken", encoding="utf-8")
    assert asyncio.run(ExperienceStore(path, policy).async_list()) == ()


def test_goal_run_extractor_uses_verified_features_only():
    goal = GoalModel(GoalKind.ACHIEVE_STATE, goal_id="g", scope=GoalScope(area_id="living_room"))
    run = GoalRun(
        "run", "g", NOW.isoformat(), (NOW + timedelta(seconds=3)).isoformat(),
        "private utterance must not enter features", "philipp", None, goal, "plan",
        ("light.a",), (), True,
        (StepExecutionRecord(
            "step", "light.turn_on", ("light.a",), (), True,
            (VerificationRecord("light.a", "on", "on", True,
                                observed_at=(NOW + timedelta(seconds=3)).isoformat()),),
            executed_at=(NOW + timedelta(seconds=1)).isoformat(),
            service_accepted_at=(NOW + timedelta(seconds=1)).isoformat(),
        ),), (), GoalRunStatus.SUCCESS,
    )
    records = extract_goal_run_experiences(run)
    assert len(records) == 1
    assert records[0].effect.latency_seconds == 2
    assert "private utterance" not in repr(records[0].to_dict())


def test_goal_run_extractor_uses_each_step_execution_time_and_never_run_start():
    goal = GoalModel(GoalKind.ACHIEVE_STATE, goal_id="g")
    run = GoalRun(
        "run", "g", NOW.isoformat(), (NOW + timedelta(seconds=22)).isoformat(),
        "", "u", None, goal, "plan", ("light.a", "light.b"), (), True,
        (
            StepExecutionRecord(
                "first", "light.turn_on", ("light.a",), (), True,
                (VerificationRecord(
                    "light.a", "on", "on", True,
                    observed_at=(NOW + timedelta(seconds=3)).isoformat(),
                ),),
                executed_at=(NOW + timedelta(seconds=2)).isoformat(),
                service_accepted_at=(NOW + timedelta(seconds=2)).isoformat(),
            ),
            StepExecutionRecord(
                "second", "light.turn_on", ("light.b",), (), True,
                (VerificationRecord(
                    "light.b", "on", "on", True,
                    observed_at=(NOW + timedelta(seconds=22)).isoformat(),
                ),),
                executed_at=(NOW + timedelta(seconds=20)).isoformat(),
                service_accepted_at=(NOW + timedelta(seconds=20)).isoformat(),
            ),
        ),
        (), GoalRunStatus.SUCCESS,
    )
    records = extract_goal_run_experiences(run)
    assert [item.effect.latency_seconds for item in records] == [1.0, 2.0]
    restored = GoalRun.from_dict(run.to_dict())
    assert restored.steps[1].executed_at == (NOW + timedelta(seconds=20)).isoformat()

    legacy = replace(
        run,
        steps=(replace(
            run.steps[1], executed_at=None, service_accepted_at=None
        ),),
    )
    assert extract_goal_run_experiences(legacy)[0].effect.latency_seconds is None


def test_model_registry_persists_invalidates_deletes_and_tombstones(tmp_path):
    path = tmp_path / "models.json"
    registry = ModelRegistry(path, _policy())
    model = LearnedModel(
        "thermal:living_room", LearnedKind.THERMAL_MODEL, "living_room", {},
        {"mae": 2.0}, KnowledgeState.OBSERVED, 0.9, 20, NOW, NOW,
        ("exp_1",), health=ModelHealth.VALID,
    )
    assert asyncio.run(registry.async_upsert(model)) is UpsertResult.STORED
    assert asyncio.run(ModelRegistry(path, _policy()).async_get(model.model_id)) == model
    assert asyncio.run(registry.async_invalidate(model.model_id, "sensor_removed"))
    assert asyncio.run(registry.async_get(model.model_id)).health is ModelHealth.INVALID
    assert asyncio.run(registry.async_delete(model.model_id))
    assert asyncio.run(registry.async_upsert(model)) is UpsertResult.SUPPRESSED
    assert asyncio.run(registry.async_is_suppressed(model.model_id))
    assert asyncio.run(registry.async_get(model.model_id)) is None


def _thermal_observations(count: int = 20) -> tuple[ThermalObservation, ...]:
    durations = (
        2820, 2880, 2760, 2940, 2850, 2910, 2790, 2870, 2830, 2920,
        2860, 2810, 2890, 2780, 2950, 2840, 2900, 2800, 2930, 2860,
    )
    return tuple(
        ThermalObservation(
            NOW - timedelta(days=index + 1),
            NOW - timedelta(days=index + 1) + timedelta(seconds=duration),
            "living_room", "sensor.living_temperature", "climate.living_room",
            19.0, 21.0, duration, True,
        )
        for index, duration in enumerate(durations[:count])
    )


def test_thermal_e2e_prediction_and_adaptive_start_are_independent_ranges():
    policy = _policy()
    binding = ThermalBinding(
        "living_room", "sensor.living_temperature", "climate.living_room",
        confirmed=True,
    )
    model = train_thermal_model(binding, _thermal_observations(), policy, now=NOW)
    assert model is not None
    house = PredictiveHouseModel(policy)
    house.install_thermal(model)
    prediction = house.predict_thermal(
        "living_room", current_celsius=19.0, target_celsius=21.0,
        outdoor_celsius=None, now=NOW,
    )
    assert prediction.usable_for_planning
    assert 45 * 60 <= prediction.value <= 50 * 60
    assert prediction.uncertainty is not None
    deadline = datetime(2026, 1, 16, 7, 0, tzinfo=timezone.utc)
    goal = GoalModel(
        GoalKind.SCHEDULED, goal_id="warm", scope=GoalScope(area_id="living_room"),
        desired_states=(DesiredState("temperature", 21.0, "°C"),),
        temporal=TemporalGoal(deadline=deadline, must_be_achieved_by_deadline=True),
    )
    advice = advise_deadline_goal(goal, prediction)
    assert advice is not None
    assert datetime(2026, 1, 16, 6, 5, tzinfo=timezone.utc) <= advice.start_at
    assert advice.start_at <= datetime(2026, 1, 16, 6, 12, tzinfo=timezone.utc)
    assert advice.final_verification_at == deadline


def test_thermal_cold_start_bad_data_stale_and_drift():
    policy = _policy()
    binding = ThermalBinding("living_room", "sensor.t", "climate.h", confirmed=True)
    assert train_thermal_model(binding, (), policy, now=NOW) is None
    assert train_thermal_model(binding, _thermal_observations(3), policy, now=NOW) is None
    contaminated = replace(_thermal_observations(1)[0], window_opened=True)
    assert train_thermal_model(binding, (contaminated,) * 20, policy, now=NOW) is None
    model = train_thermal_model(
        ThermalBinding("living_room", "sensor.living_temperature", "climate.living_room", confirmed=True),
        _thermal_observations(), policy, now=NOW,
    )
    assert model is not None
    assert detect_thermal_drift(model, (1000, 1100, 1050, 1200, 1150), policy)


def test_thermal_experience_and_registry_roundtrip_uses_exact_binding():
    observation = _thermal_observations(1)[0]
    record = thermal_observation_to_experience(
        observation, goal_id="warm", run_id="run", user_id="philipp"
    )
    binding = ThermalBinding(
        "living_room", "sensor.living_temperature", "climate.living_room",
        confirmed=True,
    )
    assert thermal_observations_from_experiences((record,), binding) == (observation,)
    wrong = replace(binding, temperature_entity_id="sensor.other")
    assert thermal_observations_from_experiences((record,), wrong) == ()
    model = train_thermal_model(binding, _thermal_observations(), _policy(), now=NOW)
    assert model is not None
    restored = thermal_model_from_learned(thermal_model_to_learned(model))
    assert restored == model


def test_deadline_advice_preserves_dst_aware_instants():
    berlin = ZoneInfo("Europe/Berlin")
    policy = _policy()
    model = train_thermal_model(
        ThermalBinding(
            "living_room", "sensor.living_temperature", "climate.living_room",
            confirmed=True,
        ),
        _thermal_observations(), policy, now=NOW,
    )
    assert model is not None
    house = PredictiveHouseModel(policy)
    house.install_thermal(model)
    prediction = house.predict_thermal(
        "living_room", current_celsius=19, target_celsius=21,
        outdoor_celsius=None, now=NOW,
    )
    for deadline in (
        datetime(2026, 3, 29, 7, 0, tzinfo=berlin),
        datetime(2026, 10, 25, 7, 0, tzinfo=berlin),
    ):
        goal = GoalModel(
            GoalKind.SCHEDULED, scope=GoalScope(area_id="living_room"),
            desired_states=(DesiredState("temperature", 21, "°C"),),
            temporal=TemporalGoal(deadline=deadline, must_be_achieved_by_deadline=True),
        )
        advice = advise_deadline_goal(goal, prediction)
        assert advice is not None
        assert advice.start_at.tzinfo is berlin
        assert advice.final_verification_at == deadline


def test_effect_timing_anomaly_timeout_cap_and_reliability_are_advisory():
    policy = _policy()
    durations = (14.0, 15.0, 16.0, 15.5, 14.5, 15.2, 14.8, 15.1, 16.2, 13.9,
                 15.3, 14.7, 15.4, 14.6, 15.0, 15.8, 14.9, 15.2, 14.8, 15.1)
    timing = train_effect_timing("cover.close", "cover.garage", durations, policy)
    assert timing is not None
    anomaly = evaluate_latency_anomaly(timing, 50.0)
    assert anomaly.anomalous and anomaly.advisory_only
    assert adaptive_verification_timeout(timing, default_seconds=10, absolute_max_seconds=40) <= 40
    reliability = train_reliability("cover.close", "cover.garage", (True,) * 24 + (False,) * 6)
    assert reliability is not None
    assert reliability.success_rate == 0.8
    assert not hasattr(reliability, "service_call")


def test_preference_requires_confirmation_and_stays_contextual():
    context = PreferenceContext("philipp", "lampe", area_id="living_room")
    inferred = infer_preference(
        context, ("light.floor",) * 8 + ("light.ceiling",) * 2, _policy()
    )
    assert inferred is not None and inferred.knowledge_state is KnowledgeState.INFERRED
    assert resolve_preferences((inferred,), present_user_ids=("philipp",)).requires_clarification
    confirmed = confirm_preference(inferred, confirmed_by="philipp")
    resolved = resolve_preferences((confirmed,), present_user_ids=("philipp",))
    assert resolved.value == "light.floor"
    assert confirmed.context.area_id == "living_room"


def test_multi_user_conflict_never_averages_and_shared_confirmation_resolves():
    philipp = LearnedPreference(
        "p", PreferenceContext("philipp", "comfort", area_id="living_room"),
        "21", KnowledgeState.CONFIRMED, 1.0, 10, 10, "philipp",
    )
    julia = LearnedPreference(
        "j", PreferenceContext("julia", "comfort", area_id="living_room"),
        "23", KnowledgeState.CONFIRMED, 1.0, 10, 10, "julia",
    )
    conflict = resolve_preferences((philipp, julia), present_user_ids=("philipp", "julia"))
    assert conflict.requires_clarification and conflict.value is None
    shared = LearnedPreference(
        "shared",
        PreferenceContext(
                "household",
                "comfort",
                area_id="living_room",
                presence_set=("julia", "philipp"),
        ),
        "22", KnowledgeState.CONFIRMED, 1.0, 1, 1, "philipp",
    )
    assert resolve_preferences(
        (philipp, julia), present_user_ids=("philipp", "julia"),
        shared_preference=shared,
    ).value == "22"


def test_habit_candidate_is_opt_in_deduped_and_never_automation():
    observations = tuple(
        HabitSequenceObservation(
            "philipp", f"2026-01-{index + 1:02d}", index % 5, "weekday_morning",
            ("light.kitchen:on", "cover.kitchen:open", "switch.coffee:on"),
            NOW + timedelta(days=index),
        )
        for index in range(15)
    )
    candidate = discover_habit(observations, opportunity_count=20, policy=_policy())
    assert candidate is not None
    assert candidate.support == 0.75
    assert not candidate.creates_automation
    rejected = update_suggestion(candidate, SuggestionStatus.REJECTED)
    assert rejected.suggestion_status is SuggestionStatus.REJECTED
    assert discover_habit(
        observations, opportunity_count=20, policy=_policy(),
        rejected_signatures=frozenset({candidate.signature}),
    ) is None


def test_habit_discovery_and_routine_decoder_reject_unsafe_or_incomplete_input():
    policy = _policy()
    assert discover_habit((), opportunity_count=0, policy=policy) is None
    sparse = (
        HabitSequenceObservation(
            "philipp", "2026-01-01", 1, "morning", ("light.a:on",), NOW
        ),
    )
    assert discover_habit(sparse, opportunity_count=10, policy=policy) is None

    base = LearnedModel(
        "habit:test", LearnedKind.HABIT, "philipp", {"time_band": "evening"},
        {"sequence": "CLIMATE_SET_TEMPERATURE@climate.living=temperature=21|"
                     "LIGHT_TURN_ON@light.floor=on"},
        KnowledgeState.INFERRED, 0.9, 15, NOW, NOW, ("goal_run:r",),
    )
    routine = routine_from_habit_model(base, "philipp")
    assert routine is not None and routine.name == "Gelernte Routine"
    assert routine.steps[0].desired_state.value == 21.0
    assert routine_from_habit_model(replace(base, kind=LearnedKind.PREFERENCE), "philipp") is None
    assert routine_from_habit_model(replace(base, knowledge_state=KnowledgeState.CONFIRMED), "philipp") is None
    assert routine_from_habit_model(replace(base, parameters={}), "philipp") is None
    assert routine_from_habit_model(
        replace(base, parameters={"sequence": "not-a-typed-step"}), "philipp"
    ) is None
    assert routine_from_habit_model(
        replace(base, parameters={"sequence": "LIGHT_TURN_ON@light.floor=on"}),
        "philipp",
    ) is None


def test_statistical_housegraph_relation_is_not_asserted():
    graph = HouseGraph()
    graph.add_node(GraphNode("area:living", NodeKind.AREA, "Living"))
    graph.add_node(GraphNode("entity:lamp", NodeKind.ENTITY, "Lamp"))
    relation = graph.add_relation(
        "area:living", RelationKind.PREFERRED_DEVICE, "entity:lamp",
        provenance=FactProvenance.STATISTICAL,
        confidence=ConfidenceClass.ESTIMATE,
    )
    assert not relation.is_asserted_fact
    assert graph.related("area:living", RelationKind.PREFERRED_DEVICE) == ()
    assert graph.related(
        "area:living", RelationKind.PREFERRED_DEVICE, asserted_only=False
    )[0].node_id == "entity:lamp"


def test_learning_policy_central_semantics_and_validation():
    policy = _policy()
    assert policy.confidence_band(0.1) is ConfidenceBand.VERY_LOW
    assert policy.confidence_band(0.3) is ConfidenceBand.LOW
    assert policy.confidence_band(0.6) is ConfidenceBand.MEDIUM
    assert policy.confidence_band(0.9) is ConfidenceBand.HIGH
    assert policy.suggestions_enabled
    assert policy.retention == timedelta(days=365)
    assert policy.permits_planning(0.9, 20)
    assert not replace(policy, predictive_models_enabled=False).permits_planning(1, 100)
    for changes in (
        {"minimum_planning_confidence": 2.0},
        {"minimum_suggestion_confidence": -1.0},
        {"minimum_model_samples": 1},
        {"minimum_model_samples": 10, "usable_model_samples": 5},
    ):
        try:
            replace(policy, **changes)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid policy accepted")


def test_store_rejects_bad_shapes_forgets_model_inputs_and_extends(tmp_path):
    path = tmp_path / "experiences.json"
    store = ExperienceStore(path, _policy(retention_days=3650))
    linked = replace(_experience(1), evidence=("model:thermal:living",))
    assert asyncio.run(store.async_extend((linked, _experience(2)))) == 2
    assert asyncio.run(store.async_forget_model_inputs("missing")) == 0
    assert asyncio.run(store.async_forget_model_inputs("thermal:living")) == 1
    naive = replace(_experience(3), timestamp=datetime(2026, 1, 1))
    assert not asyncio.run(store.async_append(naive))
    for payload in ("[]", '{"schema_version":2}', '{"schema_version":1,"experiences":{}}'):
        path.write_text(payload, encoding="utf-8")
        assert asyncio.run(store.async_list()) == ()
    path.write_text(
        '{"schema_version":1,"experiences":[null,{"bad":true}]}', encoding="utf-8"
    )
    assert asyncio.run(store.async_list()) == ()


def test_predictive_house_missing_low_confidence_and_forget_paths():
    policy = _policy()
    house = PredictiveHouseModel(policy)
    assert house.thermal_model("missing") is None
    assert house.predict_effect_latency("x", "y").status is PredictionStatus.INSUFFICIENT_DATA
    assert house.predict_reliability("x", "y").status is PredictionStatus.INSUFFICIENT_DATA
    timing = train_effect_timing("cover.close", "cover.garage", (1, 2, 3, 4, 5), policy)
    reliability = train_reliability("cover.close", "cover.garage", (True, False))
    assert timing is not None and reliability is not None
    house.install_effect_timing(timing)
    house.install_reliability(reliability)
    assert house.effect_timing_model("cover.close", "cover.garage") is timing
    assert house.predict_effect_latency("cover.close", "cover.garage").status is PredictionStatus.LOW_CONFIDENCE
    assert house.predict_reliability("cover.close", "cover.garage").value == 0.5
    assert house.forget(timing.model_id)
    assert house.forget(reliability.model_id)
    assert not house.forget("missing")
    thermal = train_thermal_model(
        ThermalBinding(
            "living_room", "sensor.living_temperature", "climate.living_room",
            confirmed=True,
        ),
        _thermal_observations(), policy, now=NOW,
    )
    assert thermal is not None
    house.install_thermal(thermal)
    assert house.forget(thermal.model_id)
    house.install_thermal(thermal)
    house.install_effect_timing(timing)
    house.install_reliability(reliability)
    house.clear()
    assert house.thermal_model("living_room") is None
    assert house.effect_timing_model("cover.close", "cover.garage") is None


def test_thermal_all_prediction_health_statuses_and_outdoor_feature():
    policy = _policy()
    base = train_thermal_model(
        ThermalBinding("living_room", "sensor.living_temperature", "climate.living_room", confirmed=True),
        _thermal_observations(), policy, now=NOW,
    )
    assert base is not None
    cases = (
        (replace(base, invalidation_reason="removed"), PredictionStatus.MODEL_INVALID),
        (replace(base, drift_detected=True), PredictionStatus.DRIFT_DETECTED),
        (replace(base, updated_at=NOW - timedelta(days=181)), PredictionStatus.STALE_MODEL),
        (replace(base, sample_count=10), PredictionStatus.LOW_CONFIDENCE),
        (replace(base, validation_mae_seconds=901), PredictionStatus.MODEL_UNRELIABLE),
        (replace(base, confidence=0.5), PredictionStatus.LOW_CONFIDENCE),
    )
    for model, expected in cases:
        result = PredictiveHouseModel(policy)
        result.install_thermal(model)
        assert result.predict_thermal(
            "living_room", current_celsius=19, target_celsius=21,
            outdoor_celsius=None, now=NOW,
        ).status is expected
    house = PredictiveHouseModel(policy)
    house.install_thermal(base)
    assert house.predict_thermal(
        "living_room", current_celsius=21, target_celsius=20,
        outdoor_celsius=None, now=NOW,
    ).status is PredictionStatus.INCOMPATIBLE_CONTEXT
    outside = replace(base, outside_gap_coefficient=30.0)
    house.install_thermal(outside)
    assert house.predict_thermal(
        "living_room", current_celsius=19, target_celsius=21,
        outdoor_celsius=None, now=NOW,
    ).status is PredictionStatus.INCOMPATIBLE_CONTEXT
    assert house.predict_thermal(
        "living_room", current_celsius=19, target_celsius=21,
        outdoor_celsius=7, now=NOW,
    ).status is PredictionStatus.OK
    assert not detect_thermal_drift(base, (1, 2), policy)
    assert not detect_thermal_drift(base, (1000, -1000, 1000, -1000, 1000), policy)


def test_apply_advice_guards_and_materializes_timing():
    policy = _policy()
    model = train_thermal_model(
        ThermalBinding("living_room", "sensor.living_temperature", "climate.living_room", confirmed=True),
        _thermal_observations(), policy, now=NOW,
    )
    assert model is not None
    house = PredictiveHouseModel(policy)
    house.install_thermal(model)
    prediction = house.predict_thermal(
        "living_room", current_celsius=19, target_celsius=21,
        outdoor_celsius=None, now=NOW,
    )
    deadline = NOW + timedelta(days=1)
    goal = GoalModel(
        GoalKind.SCHEDULED, scope=GoalScope(area_id="living_room"),
        temporal=TemporalGoal(deadline=deadline, must_be_achieved_by_deadline=True),
    )
    advice = advise_deadline_goal(goal, prediction)
    assert advice is not None
    applied = apply_advice(goal, advice)
    assert applied.temporal is not None and not applied.temporal.must_be_achieved_by_deadline
    assert applied.temporal.execute_at == advice.start_at
    assert apply_advice(goal, None) is goal
    try:
        apply_advice(replace(goal, temporal=replace(goal.temporal, deadline=deadline + timedelta(hours=1))), advice)
    except ValueError:
        pass
    else:
        raise AssertionError("mismatched advice accepted")


def test_thermal_checkpoint_payload_and_persistent_automations_are_closed():
    checkpoint = ThermalDeadlineCheckpoint(
        ThermalCheckpointPhase.START, "goal", "living_room", "climate.living",
        "sensor.living_temperature", 21.0, "thermal:living_room", 2_820.0,
        480.0,
    )
    assert ThermalDeadlineCheckpoint.from_service_data(
        checkpoint.to_service_data()
    ) == checkpoint
    assert ThermalDeadlineCheckpoint.from_service_data({}) is None
    assert ThermalDeadlineCheckpoint.from_service_data({
        **checkpoint.to_service_data(), "climate_entity_id": "lock.front"
    }) is None
    base = {
        "actions": [
            {"action": "climate.set_temperature"},
            {"action": "homeintent.delete_automation", "data": {"automation_id": "a"}},
        ]
    }
    enriched = append_start_checkpoint(base, checkpoint)
    assert enriched is not None
    actions = enriched["actions"]
    assert isinstance(actions, list)
    assert actions[1]["action"] == "homeintent.thermal_deadline_checkpoint"
    assert actions[-1]["action"] == "homeintent.delete_automation"
    assert append_start_checkpoint(
        base, replace(checkpoint, phase=ThermalCheckpointPhase.FINAL)
    ) is None

    final = replace(checkpoint, phase=ThermalCheckpointPhase.FINAL)
    config = checkpoint_automation_config(
        final, scheduled_for=NOW + timedelta(days=1), automation_id="final-id"
    )
    assert config is not None
    assert config["triggers"] == [{"trigger": "time", "at": "07:00:00"}]
    assert config["actions"][-1]["data"]["automation_id"] == "final-id"
    assert checkpoint_automation_config(
        checkpoint, scheduled_for=NOW, automation_id="start"
    ) is None
    assert checkpoint_automation_config(
        final, scheduled_for=datetime(2026, 1, 1), automation_id="naive"
    ) is None


def test_learning_manager_goalrun_models_thermal_update_and_restart(tmp_path):
    policy = _policy(retention_days=3650)
    experiences = ExperienceStore(tmp_path / "experiences.json", policy)
    registry = ModelRegistry(tmp_path / "models.json", policy)
    house = PredictiveHouseModel(policy)
    manager = LearningManager(experiences, registry, house, policy)
    goal = GoalModel(GoalKind.ACHIEVE_STATE, goal_id="g")
    for index in range(15):
        started = NOW + timedelta(minutes=index)
        run = GoalRun(
            f"run{index}", "g", started.isoformat(),
            (started + timedelta(seconds=1 + index % 3)).isoformat(), "", "u", None,
            goal, "p", ("light.a",), (), True,
            (StepExecutionRecord(
                "s", "light.turn_on", ("light.a",), (), True,
                (VerificationRecord(
                    "light.a", "on", "on", True,
                    observed_at=(started + timedelta(seconds=1 + index % 3)).isoformat(),
                ),),
                executed_at=started.isoformat(),
                service_accepted_at=started.isoformat(),
            ),), (), GoalRunStatus.SUCCESS,
        )
        asyncio.run(manager.async_observe_goal_run(run))
    assert house.predict_effect_latency(
        "light.turn_on", "light.a", now=NOW + timedelta(minutes=15)
    ).status is PredictionStatus.OK
    assert house.predict_reliability(
        "light.turn_on", "light.a", now=NOW + timedelta(minutes=2)
    ).value == 1.0
    binding = ThermalBinding(
        "living_room", "sensor.living_temperature", "climate.living_room", confirmed=True
    )
    thermal_records = tuple(
        thermal_observation_to_experience(item, goal_id="warm", run_id=f"thermal{index}")
        for index, item in enumerate(_thermal_observations())
    )
    asyncio.run(experiences.async_extend(thermal_records))
    assert asyncio.run(manager.async_update_thermal_model(binding)) is not None
    restarted_house = PredictiveHouseModel(policy)
    restarted = LearningManager(experiences, registry, restarted_house, policy)
    asyncio.run(restarted.async_restore_models())
    assert restarted_house.thermal_model("living_room") is not None
    assert restarted_house.predict_effect_latency(
        "light.turn_on", "light.a", now=NOW + timedelta(minutes=15)
    ).value is not None
    assert restarted_house.predict_reliability(
        "light.turn_on", "light.a", now=NOW + timedelta(minutes=15)
    ).value == 1.0
    disabled = LearningManager(
        experiences, registry, PredictiveHouseModel(replace(policy, predictive_models_enabled=False)),
        replace(policy, predictive_models_enabled=False),
    )
    assert asyncio.run(disabled.async_update_thermal_model(binding)) is None


def test_tombstoned_effect_and_reliability_models_cannot_reactivate_or_restore(tmp_path):
    policy = _policy(
        retention_days=3650, minimum_model_samples=2, usable_model_samples=2
    )
    experiences = ExperienceStore(tmp_path / "experiences.json", policy)
    registry_path = tmp_path / "models.json"
    registry = ModelRegistry(registry_path, policy)
    house = PredictiveHouseModel(policy)
    manager = LearningManager(experiences, registry, house, policy)
    goal = GoalModel(GoalKind.ACHIEVE_STATE, goal_id="g")

    def observed_run(index: int) -> GoalRun:
        started = NOW + timedelta(minutes=index)
        return GoalRun(
            f"run{index}", "g", (started - timedelta(seconds=30)).isoformat(),
            (started + timedelta(seconds=2)).isoformat(), "", "u", None,
            goal, "p", ("light.a",), (), True,
            (StepExecutionRecord(
                "s", "light.turn_on", ("light.a",), (), True,
                (VerificationRecord(
                    "light.a", "on", "on", True,
                    observed_at=(started + timedelta(seconds=2)).isoformat(),
                ),),
                executed_at=started.isoformat(),
                service_accepted_at=started.isoformat(),
            ),), (), GoalRunStatus.SUCCESS,
        )

    asyncio.run(manager.async_observe_goal_run(observed_run(0)))
    asyncio.run(manager.async_observe_goal_run(observed_run(1)))
    timing_id = "effect_latency:light.turn_on:light.a"
    reliability_id = "reliability:light.turn_on:light.a"
    assert house.effect_timing_model("light.turn_on", "light.a") is not None
    assert house.predict_reliability(
        "light.turn_on", "light.a", now=NOW + timedelta(minutes=2)
    ).value == 1.0

    assert asyncio.run(registry.async_delete(timing_id, suppress=True))
    assert asyncio.run(registry.async_delete(reliability_id, suppress=True))
    assert house.effect_timing_model("light.turn_on", "light.a") is None
    assert house.predict_reliability(
        "light.turn_on", "light.a"
    ).status is PredictionStatus.INSUFFICIENT_DATA
    asyncio.run(manager.async_observe_goal_run(observed_run(2)))

    assert asyncio.run(registry.async_get(timing_id)) is None
    assert asyncio.run(registry.async_get(reliability_id)) is None
    assert house.predict_effect_latency(
        "light.turn_on", "light.a"
    ).status is PredictionStatus.INSUFFICIENT_DATA
    assert house.predict_reliability(
        "light.turn_on", "light.a"
    ).status is PredictionStatus.INSUFFICIENT_DATA

    restarted_house = PredictiveHouseModel(policy)
    restarted = LearningManager(
        experiences, ModelRegistry(registry_path, policy), restarted_house, policy
    )
    asyncio.run(restarted.async_restore_models())
    asyncio.run(restarted.async_observe_goal_run(observed_run(3)))
    assert restarted_house.predict_effect_latency(
        "light.turn_on", "light.a"
    ).status is PredictionStatus.INSUFFICIENT_DATA
    assert restarted_house.predict_reliability(
        "light.turn_on", "light.a"
    ).status is PredictionStatus.INSUFFICIENT_DATA


def test_tombstoned_thermal_model_cannot_reactivate_or_restore(tmp_path):
    policy = _policy(
        retention_days=3650, minimum_model_samples=2, usable_model_samples=2
    )
    experiences = ExperienceStore(tmp_path / "experiences.json", policy)
    registry_path = tmp_path / "models.json"
    registry = ModelRegistry(registry_path, policy)
    house = PredictiveHouseModel(policy)
    manager = LearningManager(experiences, registry, house, policy)
    binding = ThermalBinding(
        "living_room", "sensor.living_temperature", "climate.living_room",
        confirmed=True,
    )
    records = tuple(
        thermal_observation_to_experience(
            observation, goal_id="warm", run_id=f"thermal{index}"
        )
        for index, observation in enumerate(_thermal_observations())
    )
    asyncio.run(experiences.async_extend(records))
    assert asyncio.run(manager.async_update_thermal_model(binding)) is not None
    model_id = "thermal:living_room"
    assert asyncio.run(registry.async_delete(model_id, suppress=True))
    assert house.thermal_model("living_room") is None

    extra = replace(
        _thermal_observations(1)[0],
        started_at=NOW + timedelta(days=2),
        ended_at=NOW + timedelta(days=2, minutes=30),
    )
    asyncio.run(experiences.async_append(thermal_observation_to_experience(
        extra, goal_id="warm", run_id="thermal-new"
    )))
    assert asyncio.run(manager.async_update_thermal_model(binding)) is None
    assert asyncio.run(registry.async_get(model_id)) is None
    assert house.thermal_model("living_room") is None
    assert house.predict_thermal(
        "living_room", current_celsius=19, target_celsius=21,
        outdoor_celsius=None, now=NOW,
    ).status is PredictionStatus.INSUFFICIENT_DATA

    restarted_house = PredictiveHouseModel(policy)
    restarted = LearningManager(
        experiences, ModelRegistry(registry_path, policy), restarted_house, policy
    )
    asyncio.run(restarted.async_restore_models())
    assert asyncio.run(restarted.async_update_thermal_model(binding)) is None
    assert restarted_house.thermal_model("living_room") is None


def test_reset_tombstones_block_retraining_from_retained_old_evidence(tmp_path):
    policy = _policy(
        retention_days=3650, minimum_model_samples=2, usable_model_samples=2
    )
    experiences = ExperienceStore(tmp_path / "experiences.json", policy)
    registry_path = tmp_path / "models.json"
    registry = ModelRegistry(registry_path, policy)
    house = PredictiveHouseModel(policy)
    manager = LearningManager(experiences, registry, house, policy)
    goal = GoalModel(GoalKind.ACHIEVE_STATE, goal_id="g")
    for index in range(2):
        started = NOW + timedelta(minutes=index)
        run = GoalRun(
            f"run{index}", "g", started.isoformat(),
            (started + timedelta(seconds=1)).isoformat(), "", "u", None,
            goal, "p", ("light.a",), (), True,
            (StepExecutionRecord(
                "s", "light.turn_on", ("light.a",), (), True,
                (VerificationRecord(
                    "light.a", "on", "on", True,
                    observed_at=(started + timedelta(seconds=1)).isoformat(),
                ),), executed_at=started.isoformat(),
                service_accepted_at=started.isoformat(),
            ),), (), GoalRunStatus.SUCCESS,
        )
        asyncio.run(manager.async_observe_goal_run(run))
    assert asyncio.run(registry.async_reset()) == 2
    house.clear()

    restarted_registry = ModelRegistry(registry_path, policy)
    restarted_house = PredictiveHouseModel(policy)
    restarted = LearningManager(
        experiences, restarted_registry, restarted_house, policy
    )
    asyncio.run(restarted.async_observe_goal_run(run))
    assert asyncio.run(restarted_registry.async_list()) == ()
    assert restarted_house.predict_effect_latency(
        "light.turn_on", "light.a"
    ).status is PredictionStatus.INSUFFICIENT_DATA
    assert restarted_house.predict_reliability(
        "light.turn_on", "light.a"
    ).status is PredictionStatus.INSUFFICIENT_DATA


def test_learning_manager_discovers_but_never_automates_habit_and_respects_rejection(tmp_path):
    policy = _policy(retention_days=3650, learning_mode=LearningMode.ASK)
    experiences = ExperienceStore(tmp_path / "experiences.json", policy)
    registry = ModelRegistry(tmp_path / "models.json", policy)
    manager = LearningManager(
        experiences, registry, PredictiveHouseModel(policy), policy
    )
    goal = GoalModel(GoalKind.ACHIEVE_STATE, goal_id="morning")
    for index in range(15):
        stamp = NOW + timedelta(days=index)
        run = GoalRun(
            f"habit{index}", "morning", stamp.isoformat(), stamp.isoformat(),
            "", "philipp", None, goal, "p", (), (), True,
            tuple(
                StepExecutionRecord(
                    f"s{step_index}", operator, (target,), (), True,
                    (VerificationRecord(target, "on", "on", True,
                                        observed_at=stamp.isoformat()),),
                )
                for step_index, (operator, target) in enumerate((
                    ("LIGHT_TURN_ON", "light.kitchen"),
                    ("COVER_OPEN_COVER", "cover.kitchen"),
                    ("SWITCH_TURN_ON", "switch.coffee"),
                ))
            ),
            (), GoalRunStatus.SUCCESS,
        )
        asyncio.run(manager.async_observe_goal_run(run))
    habits = asyncio.run(registry.async_list(kind=LearnedKind.HABIT))
    assert len(habits) == 1
    habit = habits[0]
    assert habit.health is ModelHealth.VALID
    assert habit.knowledge_state is KnowledgeState.INFERRED
    assert habit.parameters["suggestion_status"] == "new"
    assert habit.parameters["creates_automation"] is False
    routine = routine_from_habit_model(habit, "philipp")
    assert routine is not None and not routine.confirmed
    assert len(routine.steps) == 3
    assert routine.steps[0].scope.entity_ids == ("light.kitchen",)
    assert asyncio.run(registry.async_delete(habit.model_id, suppress=True))

    later = replace(run, run_id="habit-later",
                    updated_at=(NOW + timedelta(days=20)).isoformat())
    asyncio.run(manager.async_observe_goal_run(later))
    assert asyncio.run(registry.async_list(kind=LearnedKind.HABIT)) == ()


def test_learning_manager_infers_contextual_preference_but_never_confirms_it(tmp_path):
    policy = _policy(retention_days=3650)
    registry = ModelRegistry(tmp_path / "models.json", policy)
    manager = LearningManager(
        ExperienceStore(tmp_path / "experiences.json", policy), registry,
        PredictiveHouseModel(policy), policy,
    )
    for index in range(10):
        selected = "light.floor" if index < 8 else "light.ceiling"
        asyncio.run(manager.async_observe_preference_selection(
            user_id="philipp", concept="Lampe", area_id="living_room",
            entity_id=selected, observed_at=NOW + timedelta(minutes=index),
        ))
    preferences = asyncio.run(registry.async_list(kind=LearnedKind.PREFERENCE))
    assert len(preferences) == 1
    preference = preferences[0]
    assert preference.knowledge_state is KnowledgeState.INFERRED
    assert preference.confidence == 0.8
    assert preference.parameters["entity_id"] == "light.floor"
    assert preference.context["area_id"] == "living_room"
    assert preference.confirmed_by is None


def test_tombstoned_preference_cannot_be_recreated_after_restart(tmp_path):
    policy = _policy(retention_days=3650)
    path = tmp_path / "models.json"
    registry = ModelRegistry(path, policy)
    manager = LearningManager(
        ExperienceStore(tmp_path / "experiences.json", policy), registry,
        PredictiveHouseModel(policy), policy,
    )
    created = asyncio.run(manager.async_observe_preference_selection(
        user_id="philipp", concept="Lampe", area_id="living_room",
        entity_id="light.floor", observed_at=NOW,
    ))
    assert created is not None
    assert asyncio.run(registry.async_delete(created.model_id, suppress=True))

    restarted_registry = ModelRegistry(path, policy)
    restarted = LearningManager(
        ExperienceStore(tmp_path / "experiences.json", policy),
        restarted_registry, PredictiveHouseModel(policy), policy,
    )
    assert asyncio.run(restarted.async_observe_preference_selection(
        user_id="philipp", concept="Lampe", area_id="living_room",
        entity_id="light.floor", observed_at=NOW + timedelta(minutes=1),
    )) is None
    assert asyncio.run(restarted_registry.async_list(
        kind=LearnedKind.PREFERENCE
    )) == ()


def test_learning_manager_disabled_and_empty_inputs_are_noops(tmp_path):
    off = _policy(learning_mode=LearningMode.OFF, retention_days=3650)
    registry = ModelRegistry(tmp_path / "models.json", off)
    manager = LearningManager(
        ExperienceStore(tmp_path / "experiences.json", off), registry,
        PredictiveHouseModel(off), off,
    )
    empty_goal = GoalModel(GoalKind.ACHIEVE_STATE, goal_id="empty")
    empty_run = GoalRun(
        "empty-run", "empty", NOW.isoformat(), NOW.isoformat(), "", "philipp",
        None, empty_goal, "p", (), (), True, (), (), GoalRunStatus.SUCCESS,
    )
    asyncio.run(manager.async_observe_goal_run(empty_run))
    assert asyncio.run(manager.async_observe_preference_selection(
        user_id="philipp", concept="Lampe", area_id="living_room",
        entity_id="light.floor", observed_at=NOW,
    )) is None
    binding = ThermalBinding(
        "living_room", "sensor.living_temperature", "climate.living_room",
        confirmed=True,
    )
    assert asyncio.run(manager.async_record_thermal_observation(
        binding, _thermal_observations(1)[0], goal_id="g", run_id="r",
        user_id="philipp",
    )) is None
    assert asyncio.run(registry.async_list()) == ()

    ask = _policy(retention_days=3650)
    active = LearningManager(
        ExperienceStore(tmp_path / "ask-experiences.json", ask),
        ModelRegistry(tmp_path / "ask-models.json", ask),
        PredictiveHouseModel(ask), ask,
    )
    asyncio.run(active.async_observe_goal_run(empty_run))
    assert asyncio.run(active.models.async_list()) == ()
    scheduled_run = replace(
        empty_run,
        status=GoalRunStatus.SCHEDULED,
        steps=(StepExecutionRecord(
            "scheduled-setpoint", "CLIMATE_SET_TEMPERATURE",
            ("climate.living",), (), True,
        ),),
    )
    asyncio.run(active.async_observe_goal_run(scheduled_run))
    assert asyncio.run(active.experiences.async_list()) == ()


def test_thermal_tracker_requires_unique_binding_and_records_goalrun_cycle(tmp_path):
    policy = _policy(retention_days=3650, minimum_model_samples=2,
                     usable_model_samples=2)
    experiences = ExperienceStore(tmp_path / "experiences.json", policy)
    registry = ModelRegistry(tmp_path / "models.json", policy)
    manager = LearningManager(
        experiences, registry, PredictiveHouseModel(policy), policy
    )
    tracker = ThermalExperienceTracker(manager)
    climate = EntitySnapshot(
        "climate.living", "Heizung", "climate", "heat",
        area_id="living_room", area_name="Wohnzimmer",
    )
    sensor = EntitySnapshot(
        "sensor.living_temperature", "Temperatur", "sensor", "19.0",
        area_id="living_room", area_name="Wohnzimmer", unit="°C",
        device_class="temperature",
    )
    plan = ServiceCallPlan(
        "climate", "set_temperature", "climate.living", {"temperature": 21.0}
    )
    tracker.observe_action(plan, (climate, sensor), occurred_at=NOW)
    assert len(tracker.active) == 1
    goal = GoalModel(GoalKind.ACHIEVE_STATE, goal_id="warm")
    run = GoalRun(
        "run-warm", "warm", NOW.isoformat(), NOW.isoformat(), "", "philipp",
        None, goal, "p", (), (), True,
        (StepExecutionRecord(
            "s", "CLIMATE_SET_TEMPERATURE", ("climate.living",), (), True,
        ),), (), GoalRunStatus.SUCCESS,
    )
    asyncio.run(tracker.async_associate_goal_run(run))
    reached = replace(sensor, state="21.0")
    asyncio.run(tracker.async_observe_states(
        (climate, reached), occurred_at=NOW + timedelta(minutes=45)
    ))
    records = asyncio.run(experiences.async_list())
    assert len(records) == 1
    assert records[0].run_id == "run-warm"
    assert records[0].quality is ExperienceQuality.COMPLETE

    tracker.observe_action(
        plan, (climate, sensor, replace(sensor, entity_id="sensor.second")),
        occurred_at=NOW + timedelta(hours=1),
    )
    assert tracker.active == ()


def test_thermal_tracker_safe_guards_and_contamination_paths(tmp_path):
    policy = _policy(retention_days=3650, minimum_model_samples=2,
                     usable_model_samples=2)
    experiences = ExperienceStore(tmp_path / "experiences.json", policy)
    registry = ModelRegistry(tmp_path / "models.json", policy)
    manager = LearningManager(experiences, registry, PredictiveHouseModel(policy), policy)
    tracker = ThermalExperienceTracker(manager)
    climate = EntitySnapshot(
        "climate.living", "Heizung", "climate", "heat", area_id="living_room"
    )
    sensor = EntitySnapshot(
        "sensor.temp", "Temperatur", "sensor", "19", area_id="living_room",
        unit="°C", device_class="temperature",
    )
    good = ServiceCallPlan(
        "climate", "set_temperature", "climate.living", {"temperature": 21}
    )
    for plan, entities in (
        (ServiceCallPlan("light", "turn_on", "light.a", {}), (climate, sensor)),
        (ServiceCallPlan("climate", "set_temperature", "climate.living", {}),
         (climate, sensor)),
        (good, (replace(climate, area_id=None), sensor)),
        (good, (climate, replace(sensor, state="not-a-number"))),
        (ServiceCallPlan("climate", "set_temperature", "climate.living",
                         {"temperature": 18}), (climate, sensor)),
    ):
        tracker.observe_action(plan, entities, occurred_at=NOW)
        assert tracker.active == ()

    tracker.observe_action(good, (climate, sensor), occurred_at=NOW)
    tracker.observe_action(good, (climate, sensor), occurred_at=NOW + timedelta(seconds=1))
    assert not tracker.active[0].concurrent_action
    window = EntitySnapshot(
        "binary_sensor.window", "Fenster", "binary_sensor", "on",
        area_id="living_room", device_class="window",
    )
    asyncio.run(tracker.async_observe_states(
        (climate, replace(sensor, state="unknown"), window),
        occurred_at=NOW + timedelta(minutes=1),
    ))
    stored = asyncio.run(experiences.async_list())
    assert stored[-1].quality is ExperienceQuality.INVALID

    tracker.observe_action(good, (climate, sensor), occurred_at=NOW + timedelta(hours=1))
    asyncio.run(tracker.async_observe_states(
        (), occurred_at=NOW + timedelta(hours=1, minutes=1)
    ))
    assert tracker.active == ()
    assert asyncio.run(registry.async_get("thermal:living_room")) is None


def test_thermal_tracker_final_checkpoint_records_missed_deadline(tmp_path):
    policy = _policy(retention_days=3650)
    experiences = ExperienceStore(tmp_path / "experiences.json", policy)
    manager = LearningManager(
        experiences, ModelRegistry(tmp_path / "models.json", policy),
        PredictiveHouseModel(policy), policy,
    )
    tracker = ThermalExperienceTracker(manager)
    climate = EntitySnapshot(
        "climate.living", "Heizung", "climate", "heat", area_id="living_room"
    )
    sensor = EntitySnapshot(
        "sensor.temp", "Temperatur", "sensor", "19", area_id="living_room",
        unit="°C", device_class="temperature",
    )
    tracker.observe_action(
        ServiceCallPlan(
            "climate", "set_temperature", "climate.living", {"temperature": 21}
        ),
        (climate, sensor),
        occurred_at=NOW,
    )
    assert asyncio.run(tracker.async_finalize(
        "climate.living", (climate, replace(sensor, state="20")),
        occurred_at=NOW + timedelta(minutes=50),
    )) is False
    assert tracker.active == ()
    records = asyncio.run(experiences.async_list())
    assert len(records) == 1
    assert not records[0].effect.success
    assert asyncio.run(tracker.async_finalize(
        "climate.living", (climate, sensor), occurred_at=NOW + timedelta(hours=1)
    )) is None


def test_persistent_thermal_checkpoint_finalizes_goalrun_and_experience(tmp_path):
    policy = _policy(retention_days=3650)
    experiences = ExperienceStore(tmp_path / "experiences.json", policy)
    manager = LearningManager(
        experiences, ModelRegistry(tmp_path / "models.json", policy),
        PredictiveHouseModel(policy), policy,
    )
    tracker = ThermalExperienceTracker(manager)
    runs = GoalRunStore(tmp_path / "runs.json")
    goal = GoalModel(GoalKind.SCHEDULED, goal_id="warm")
    scheduled = replace(
        GoalRun.start(goal, user_id="philipp", person_entity_id=None, now=NOW),
        status=GoalRunStatus.SCHEDULED,
        confirmed=True,
        steps=(StepExecutionRecord(
            "scheduled-setpoint", "CLIMATE_SET_TEMPERATURE",
            ("climate.living",), (), True,
        ),),
    )
    asyncio.run(runs.async_append(scheduled))
    climate = EntitySnapshot(
        "climate.living", "Heizung", "climate", "heat", area_id="living_room"
    )
    sensor = EntitySnapshot(
        "sensor.temp", "Temperatur", "sensor", "19", area_id="living_room",
        unit="°C", device_class="temperature",
    )
    start = ThermalDeadlineCheckpoint(
        ThermalCheckpointPhase.START, "warm", "living_room", "climate.living",
        "sensor.temp", 21, "thermal:living_room", 2_820, 480,
        run_id=scheduled.run_id,
    )
    asyncio.run(async_process_thermal_checkpoint(
        start, (climate, sensor), tracker, runs, now=NOW
    ))
    assert tracker.active[0].run_id == scheduled.run_id
    result = asyncio.run(async_process_thermal_checkpoint(
        replace(start, phase=ThermalCheckpointPhase.FINAL),
        (climate, replace(sensor, state="21")), tracker, runs,
        now=NOW + timedelta(minutes=55),
    ))
    assert result is True
    final = asyncio.run(runs.async_latest(goal_id="warm"))
    assert final is not None and final.status is GoalRunStatus.SUCCESS
    assert final.steps[-1].verification[0].success
    assert "prediction_model=thermal:living_room" in final.evidence
    learned = asyncio.run(experiences.async_list())
    assert len(learned) == 1 and learned[0].effect.success

    missing_start_goal = GoalModel(GoalKind.SCHEDULED, goal_id="warm-no-start")
    missing_start = replace(
        GoalRun.start(
            missing_start_goal, user_id="philipp", person_entity_id=None,
            now=NOW,
        ),
        status=GoalRunStatus.SCHEDULED,
        confirmed=True,
    )
    asyncio.run(runs.async_append(missing_start))
    no_start_result = asyncio.run(async_process_thermal_checkpoint(
        replace(
            start, phase=ThermalCheckpointPhase.FINAL,
            goal_id="warm-no-start", run_id=missing_start.run_id,
        ),
        (climate, replace(sensor, state="21")), tracker, runs,
        now=NOW + timedelta(hours=2),
    ))
    assert no_start_result is True
    no_start_run = asyncio.run(runs.async_latest(goal_id="warm-no-start"))
    assert no_start_run is not None
    assert no_start_run.steps[-1].operator_id is None
    assert FailureCode.SERVICE_ERROR in no_start_run.failures


def test_learning_manager_marks_drift_then_retrains_from_new_regime(tmp_path):
    policy = _policy(retention_days=3650)
    experiences = ExperienceStore(tmp_path / "experiences.json", policy)
    registry = ModelRegistry(tmp_path / "models.json", policy)
    house = PredictiveHouseModel(policy)
    manager = LearningManager(experiences, registry, house, policy)
    binding = ThermalBinding(
        "living_room", "sensor.living_temperature", "climate.living_room",
        confirmed=True,
    )
    baseline = tuple(
        thermal_observation_to_experience(item, goal_id="warm", run_id=f"b{index}")
        for index, item in enumerate(_thermal_observations())
    )
    asyncio.run(experiences.async_extend(baseline))
    first = asyncio.run(manager.async_update_thermal_model(binding))
    assert first is not None and not first.drift_detected

    shifted = tuple(
        ThermalObservation(
            NOW + timedelta(days=index),
            NOW + timedelta(days=index, seconds=4700),
            binding.area_id, binding.temperature_entity_id,
            binding.climate_entity_id, 19.0, 21.0, 4700, True,
        )
        for index in range(1, 6)
    )
    asyncio.run(experiences.async_extend(tuple(
        thermal_observation_to_experience(item, goal_id="warm", run_id=f"d{index}")
        for index, item in enumerate(shifted)
    )))
    drifted = asyncio.run(manager.async_update_thermal_model(binding))
    assert drifted is not None and drifted.drift_detected
    assert house.predict_thermal(
        "living_room", current_celsius=19, target_celsius=21,
        outdoor_celsius=None, now=drifted.updated_at,
    ).status is PredictionStatus.DRIFT_DETECTED

    new_regime = tuple(
        ThermalObservation(
            NOW + timedelta(days=10 + index),
            NOW + timedelta(days=10 + index, seconds=4700 + index % 3 * 30),
            binding.area_id, binding.temperature_entity_id,
            binding.climate_entity_id, 19.0, 21.0,
            4700 + index % 3 * 30, True,
        )
        for index in range(15)
    )
    asyncio.run(experiences.async_extend(tuple(
        thermal_observation_to_experience(item, goal_id="warm", run_id=f"n{index}")
        for index, item in enumerate(new_regime)
    )))
    retrained = asyncio.run(manager.async_update_thermal_model(binding))
    assert retrained is not None and not retrained.drift_detected
    assert retrained.sample_count == 15


def test_model_registry_filters_summary_reset_bad_data_and_model_validation(tmp_path):
    path = tmp_path / "models.json"
    registry = ModelRegistry(path, _policy(model_limit=2))
    models = tuple(
        LearnedModel(
            f"m{index}", LearnedKind.RELIABILITY, f"entity.{index}", {}, {},
            KnowledgeState.OBSERVED, 0.5, index + 1,
            NOW + timedelta(minutes=index), NOW + timedelta(minutes=index), (),
            health=ModelHealth.VALID,
        )
        for index in range(3)
    )
    for model in models:
        asyncio.run(registry.async_upsert(model))
    assert asyncio.run(registry.async_get("m0")) is None
    assert len(asyncio.run(registry.async_list(kind=LearnedKind.RELIABILITY))) == 2
    assert len(asyncio.run(registry.async_list(subject="entity.2"))) == 1
    summary = asyncio.run(registry.async_redacted_summary())
    assert summary["model_count"] == 2
    assert not summary["personal_values_included"]
    assert not asyncio.run(registry.async_invalidate("missing", "gone"))
    assert asyncio.run(registry.async_reset()) == 2
    assert asyncio.run(registry.async_list()) == ()
    for bad in (
        "[]", '{"schema_version":2}',
        '{"schema_version":1,"models":[null,{"bad":true}]}',
    ):
        path.write_text(bad, encoding="utf-8")
        assert asyncio.run(ModelRegistry(path, _policy()).async_list()) == ()
    try:
        replace(models[0], confidence=1.1)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid confidence accepted")
    try:
        replace(models[0], first_observed=datetime(2026, 1, 1))
    except ValueError:
        pass
    else:
        raise AssertionError("naive model timestamp accepted")


def test_preference_negative_threshold_confirmation_and_owner_conflict_paths():
    context = PreferenceContext("philipp", "lamp", area_id="living")
    assert infer_preference(context, ("a",) * 7, _policy()) is None
    assert infer_preference(context, ("a",) * 5 + ("b",) * 3, _policy()) is None
    inferred = infer_preference(context, ("a",) * 8, _policy())
    assert inferred is not None
    try:
        confirm_preference(inferred, confirmed_by="")
    except ValueError:
        pass
    else:
        raise AssertionError("empty confirmation actor accepted")
    philipp = confirm_preference(inferred, confirmed_by="philipp")
    julia = LearnedPreference(
        "j", replace(context, user_id="julia"), "b", KnowledgeState.CONFIRMED,
        1.0, 8, 8, "julia",
    )
    owner = resolve_preferences(
        (philipp, julia), present_user_ids=("philipp", "julia"),
        conflict_policy=ConflictResolution.OWNER_PRIORITY,
        owner_user_id="julia",
    )
    assert owner.value == "b"
    same = replace(julia, preferred_value="a")
    assert resolve_preferences(
        (philipp, same), present_user_ids=("philipp", "julia")
    ).value == "a"
