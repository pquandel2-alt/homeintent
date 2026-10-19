from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from homeintent.adaptive_planning import advise_deadline_goal
from homeintent.entities import EntitySnapshot
from homeintent.experience import (
    ExperienceQuality,
    ExperienceRecord,
    extract_goal_run_experiences,
)
from homeintent.experience_store import ExperienceStore
from homeintent.goal_model import GoalKind, GoalModel, GoalScope, TemporalGoal
from homeintent.goal_run import (
    EffectEvidenceState,
    GoalRun,
    GoalRunStatus,
    GoalRunStore,
    StepExecutionRecord,
    VerificationRecord,
)
from homeintent.learning_manager import LearningManager
from homeintent.learning_policy import KnowledgeState, LearningMode, LearningPolicy
from homeintent.model_registry import (
    LearnedKind,
    LearnedModel,
    ModelHealth,
    ModelRegistry,
    SuppressionKind,
)
from homeintent.prediction import PredictionStatus
from homeintent.predictive_house_model import PredictiveHouseModel
from homeintent.service_call import ServiceCallPlan
from homeintent.statistical_models import EffectTimingModel, ReliabilityStatistic
from homeintent.thermal_deadline import (
    PendingThermalCheckpointStore,
    ThermalCheckpointPhase,
    ThermalCheckpointStatus,
    ThermalDeadlineCheckpoint,
    async_process_thermal_checkpoint,
)
from homeintent.thermal_model import (
    ThermalBinding,
    ThermalObservation,
    predict_thermal_duration,
    thermal_model_to_learned,
    train_thermal_model,
)
from homeintent.thermal_tracker import ThermalExperienceTracker


NOW = datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc)


def _policy(**changes: object) -> LearningPolicy:
    return replace(LearningPolicy(
        learning_mode=LearningMode.ASK,
        predictive_models_enabled=True,
        habit_discovery_enabled=True,
    ), **changes)


def _run(
    *, observed_at: datetime | None, accepted_at: datetime | None,
    success: bool = True, status: GoalRunStatus = GoalRunStatus.SUCCESS,
    run_id: str = "run",
) -> GoalRun:
    verification = () if observed_at is None else (VerificationRecord(
        "light.a", "on", "on" if success else "off", success,
        observed_at=observed_at.isoformat(),
    ),)
    return GoalRun(
        run_id, "goal", NOW.isoformat(), NOW.isoformat(), "", "philipp", None,
        GoalModel(GoalKind.ACHIEVE_STATE, goal_id="goal"), "plan",
        ("light.a",), (), True,
        (StepExecutionRecord(
            "step", "LIGHT_TURN_ON", ("light.a",), (), True,
            verification, service_accepted_at=(
                accepted_at.isoformat() if accepted_at is not None else None
            ),
        ),), (), status,
    )


def _thermal_observations(count: int = 20) -> tuple[ThermalObservation, ...]:
    result = []
    for index in range(count):
        delta = 1.5 + (index % 6) * 0.3
        start = 18.5 + (index % 4) * 0.3
        outdoor = 8.0 + (index % 6) * 2.0
        duration = 300.0 + 600.0 * delta + 15.0 * (start - outdoor)
        stamp = NOW - timedelta(days=count - index)
        result.append(ThermalObservation(
            stamp, stamp + timedelta(seconds=duration), "living",
            "sensor.living", "climate.living", start, start + delta,
            duration, True, outdoor,
        ))
    return tuple(result)


def test_negative_effect_timestamp_is_not_zero_latency():
    record = extract_goal_run_experiences(_run(
        accepted_at=NOW + timedelta(seconds=5),
        observed_at=NOW + timedelta(seconds=4),
    ))[0]
    assert record.effect.latency_seconds is None
    assert record.effect.evidence_state is EffectEvidenceState.VERIFIED_SUCCESS


def test_effect_latency_uses_service_acceptance_timestamp():
    run = _run(
        accepted_at=NOW + timedelta(seconds=5),
        observed_at=NOW + timedelta(seconds=9),
    )
    run = replace(run, steps=(replace(
        run.steps[0], executed_at=(NOW - timedelta(minutes=10)).isoformat()
    ),))
    assert extract_goal_run_experiences(run)[0].effect.latency_seconds == 4.0
    legacy = replace(run, steps=(replace(
        run.steps[0], service_accepted_at=None
    ),))
    assert extract_goal_run_experiences(legacy)[0].effect.latency_seconds is None


def test_legacy_effect_evidence_migrates_conservatively():
    complete = extract_goal_run_experiences(_run(
        accepted_at=NOW, observed_at=NOW + timedelta(seconds=1)
    ))[0].to_dict()
    effect = complete["effect"]
    assert isinstance(effect, dict)
    effect.pop("evidence_state")
    assert ExperienceRecord.from_dict(complete).effect.evidence_state is (
        EffectEvidenceState.VERIFIED_SUCCESS
    )
    effect["success"] = False
    assert ExperienceRecord.from_dict(complete).effect.evidence_state is (
        EffectEvidenceState.VERIFIED_FAILURE
    )
    complete["quality"] = ExperienceQuality.PARTIAL.value
    assert ExperienceRecord.from_dict(complete).effect.evidence_state is (
        EffectEvidenceState.UNVERIFIED
    )
    complete["quality"] = ExperienceQuality.INVALID.value
    assert ExperienceRecord.from_dict(complete).effect.evidence_state is (
        EffectEvidenceState.INVALID
    )

    malformed = replace(
        _run(accepted_at=NOW, observed_at=NOW + timedelta(seconds=1)),
        updated_at="not-a-time",
    )
    assert extract_goal_run_experiences(malformed) == ()
    naive = replace(malformed, updated_at=NOW.replace(tzinfo=None).isoformat())
    assert extract_goal_run_experiences(naive) == ()
    no_operator = replace(
        _run(accepted_at=NOW, observed_at=NOW + timedelta(seconds=1)),
        steps=(replace(
            _run(accepted_at=NOW, observed_at=NOW).steps[0], operator_id=None
        ),),
    )
    assert extract_goal_run_experiences(no_operator) == ()
    bad_observed = replace(
        _run(accepted_at=NOW, observed_at=NOW + timedelta(seconds=1)),
        steps=(replace(
            _run(accepted_at=NOW, observed_at=NOW).steps[0],
            verification=(VerificationRecord(
                "light.a", "on", "on", True, observed_at="bad"
            ),),
            service_accepted_at="bad",
        ),),
    )
    assert extract_goal_run_experiences(bad_observed)[0].effect.latency_seconds is None


def test_unverified_effect_does_not_count_as_failure(tmp_path):
    policy = _policy(minimum_model_samples=2, usable_model_samples=2)
    registry = ModelRegistry(tmp_path / "models.json", policy)
    manager = LearningManager(
        ExperienceStore(tmp_path / "experiences.json", policy), registry,
        PredictiveHouseModel(policy), policy,
    )
    for index in range(8):
        asyncio.run(manager.async_observe_goal_run(_run(
            run_id=f"success-{index}", accepted_at=NOW,
            observed_at=NOW + timedelta(seconds=2),
        )))
    asyncio.run(manager.async_observe_goal_run(_run(
        run_id="failure", accepted_at=NOW,
        observed_at=NOW + timedelta(seconds=2), success=False,
        status=GoalRunStatus.FAILURE,
    )))
    asyncio.run(manager.async_observe_goal_run(_run(
        run_id="unknown", accepted_at=NOW, observed_at=None,
        status=GoalRunStatus.PARTIAL_FAILURE,
    )))
    model = asyncio.run(registry.async_get("reliability:LIGHT_TURN_ON:light.a"))
    assert model is not None
    assert model.sample_count == 9
    assert model.parameters["success_rate"] == 8 / 9
    records = asyncio.run(manager.experiences.async_list())
    unknown = next(item for item in records if item.run_id == "unknown")
    assert unknown.effect.evidence_state is EffectEvidenceState.UNVERIFIED


def test_duplicate_goalrun_learning_is_idempotent(tmp_path):
    policy = _policy(minimum_model_samples=2, usable_model_samples=2)
    manager = LearningManager(
        ExperienceStore(tmp_path / "experiences.json", policy),
        ModelRegistry(tmp_path / "models.json", policy),
        PredictiveHouseModel(policy), policy,
    )
    run = _run(
        accepted_at=NOW, observed_at=NOW + timedelta(seconds=2), run_id="same"
    )
    asyncio.run(manager.async_observe_goal_run(run))
    asyncio.run(manager.async_observe_goal_run(run))
    assert len(asyncio.run(manager.experiences.async_list())) == 1
    reliability = asyncio.run(manager.models.async_get(
        "reliability:LIGHT_TURN_ON:light.a"
    ))
    assert reliability is not None and reliability.sample_count == 1


def test_unrelated_actions_do_not_corrupt_habit_support(tmp_path):
    policy = _policy()
    registry = ModelRegistry(tmp_path / "models.json", policy)
    manager = LearningManager(
        ExperienceStore(tmp_path / "experiences.json", policy), registry,
        PredictiveHouseModel(policy), policy,
    )

    def habit_run(
        run_id: str, occurred_at: datetime,
        sequence: tuple[tuple[str, str, str], ...],
    ) -> GoalRun:
        steps = tuple(
            StepExecutionRecord(
                f"step-{index}", operator, (entity,), (), True,
                (VerificationRecord(
                    entity, expected, expected, True,
                    observed_at=occurred_at.isoformat(),
                ),),
                service_accepted_at=occurred_at.isoformat(),
            )
            for index, (operator, entity, expected) in enumerate(sequence)
        )
        return replace(
            _run(
                run_id=run_id, accepted_at=occurred_at,
                observed_at=occurred_at,
            ),
            updated_at=occurred_at.isoformat(), steps=steps,
            status=GoalRunStatus.SUCCESS,
        )

    morning = (
        ("LIGHT_TURN_ON", "light.kitchen", "on"),
        ("COVER_OPEN", "cover.kitchen", "open"),
        ("SWITCH_TURN_ON", "switch.coffee", "on"),
    )
    for index in range(15):
        asyncio.run(manager.async_observe_goal_run(habit_run(
            f"morning-{index}", NOW + timedelta(days=index), morning
        )))
    original = next(
        item for item in asyncio.run(registry.async_list(kind=LearnedKind.HABIT))
        if "light.kitchen" in str(item.parameters.get("sequence"))
    )
    assert original.sample_count == 15
    assert original.parameters["opportunity_count"] == 15
    assert original.parameters["support"] == 1.0

    single = (("LIGHT_TURN_ON", "light.bathroom", "on"),)
    asyncio.run(manager.async_observe_goal_run(habit_run(
        "unrelated-morning", NOW + timedelta(days=20), single
    )))
    evening = (
        ("MEDIA_PLAY", "media_player.living", "playing"),
        ("LIGHT_TURN_OFF", "light.living", "off"),
    )
    for index in range(10):
        stamp = (NOW + timedelta(days=30 + index)).replace(hour=19)
        asyncio.run(manager.async_observe_goal_run(habit_run(
            f"evening-{index}", stamp, evening
        )))
    unchanged = asyncio.run(registry.async_get(original.model_id))
    assert unchanged is not None
    assert unchanged.sample_count == 15
    assert unchanged.parameters["opportunity_count"] == 15
    assert unchanged.parameters["support"] == 1.0

    changed = (
        ("LIGHT_TURN_ON", "light.dining", "on"),
        ("COVER_OPEN", "cover.dining", "open"),
        ("SWITCH_TURN_ON", "switch.tea", "on"),
    )
    asyncio.run(manager.async_observe_goal_run(habit_run(
        "changed-sequence", NOW + timedelta(days=50), changed
    )))
    family = tuple(
        item for item in asyncio.run(registry.async_list(kind=LearnedKind.HABIT))
        if item.context.get("time_band") == "morning"
        and item.context.get("sequence_family")
        == original.context.get("sequence_family")
    )
    assert len(family) == 2
    original_after = next(item for item in family if item.model_id == original.model_id)
    assert original_after.sample_count == 15
    assert original_after.parameters["opportunity_count"] == 16
    assert original_after.parameters["support"] == 15 / 16


def test_thermal_prediction_domain_holdout_and_bad_slope():
    policy = _policy()
    binding = ThermalBinding(
        "living", "sensor.living", "climate.living", "sensor.outdoor", True
    )
    model = train_thermal_model(binding, _thermal_observations(), policy, now=NOW)
    assert model is not None
    assert model.validation_sample_count == 4
    assert model.validation_mae_seconds is not None
    assert model.validation_p90_absolute_error_seconds is not None
    assert predict_thermal_duration(
        model, current_celsius=19, target_celsius=21.2,
        outdoor_celsius=9, policy=policy, now=NOW,
    ).status is PredictionStatus.OK
    assert predict_thermal_duration(
        model, current_celsius=12, target_celsius=28,
        outdoor_celsius=9, policy=policy, now=NOW,
    ).status is PredictionStatus.OUT_OF_DISTRIBUTION
    assert predict_thermal_duration(
        model, current_celsius=19, target_celsius=21.2,
        outdoor_celsius=-30, policy=policy, now=NOW,
    ).status is PredictionStatus.OUT_OF_DISTRIBUTION
    bad = tuple(replace(
        item,
        duration_seconds=5000 - 1000 * (item.target_celsius - item.start_celsius),
    ) for item in _thermal_observations())
    invalid = train_thermal_model(binding, bad, policy, now=NOW)
    assert invalid is not None
    assert invalid.invalidation_reason is not None
    assert predict_thermal_duration(
        invalid, current_celsius=19, target_celsius=21,
        outdoor_celsius=9, policy=policy, now=NOW,
    ).status is PredictionStatus.MODEL_INVALID


def test_held_out_thermal_validation_controls_model_health():
    policy = _policy()
    # Static synthetic oracle: the oldest 12 samples follow 300 + 600*delta;
    # the newest three are manually offset by one hour and form the holdout.
    rows = (
        (1.0, 900.0), (1.2, 1020.0), (1.4, 1140.0),
        (1.6, 1260.0), (1.8, 1380.0), (2.0, 1500.0),
        (2.2, 1620.0), (2.4, 1740.0), (2.6, 1860.0),
        (2.8, 1980.0), (3.0, 2100.0), (3.2, 2220.0),
        (1.5, 4800.0), (2.0, 5100.0), (2.5, 5400.0),
    )
    observations = tuple(
        ThermalObservation(
            NOW + timedelta(days=index),
            NOW + timedelta(days=index, seconds=duration),
            "living", "sensor.living", "climate.living", 19.0,
            19.0 + delta, duration, True,
        )
        for index, (delta, duration) in enumerate(rows)
    )
    model = train_thermal_model(
        ThermalBinding(
            "living", "sensor.living", "climate.living", confirmed=True
        ),
        observations, policy, now=NOW + timedelta(days=20),
    )
    assert model is not None
    assert model.validation_sample_count == 3
    assert model.mae_seconds < 0.001
    assert model.validation_mae_seconds is not None
    assert abs(model.validation_mae_seconds - 3600.0) < 0.001
    assert model.validation_p90_absolute_error_seconds is not None
    assert abs(model.validation_p90_absolute_error_seconds - 3600.0) < 0.001
    assert thermal_model_to_learned(model, policy).health is ModelHealth.UNRELIABLE


def test_ood_prediction_prevents_adaptive_planning():
    policy = _policy()
    model = train_thermal_model(
        ThermalBinding("living", "sensor.living", "climate.living", confirmed=True),
        tuple(replace(item, outdoor_celsius=None) for item in _thermal_observations()),
        policy, now=NOW,
    )
    assert model is not None
    prediction = predict_thermal_duration(
        model, current_celsius=12, target_celsius=28,
        outdoor_celsius=None, policy=policy, now=NOW,
    )
    goal = GoalModel(
        GoalKind.SCHEDULED, scope=GoalScope(area_id="living"),
        temporal=TemporalGoal(
            deadline=NOW + timedelta(hours=2),
            must_be_achieved_by_deadline=True,
        ),
    )
    assert prediction.status is PredictionStatus.OUT_OF_DISTRIBUTION
    assert advise_deadline_goal(goal, prediction) is None


def test_manual_setpoint_and_hvac_changes_contaminate_cycle(tmp_path):
    policy = _policy(retention_days=3650)
    manager = LearningManager(
        ExperienceStore(tmp_path / "experiences.json", policy),
        ModelRegistry(tmp_path / "models.json", policy),
        PredictiveHouseModel(policy), policy,
    )
    tracker = ThermalExperienceTracker(manager)
    climate = EntitySnapshot(
        "climate.living", "Heat", "climate", "heat", area_id="living",
        attributes={"temperature": 21.0},
    )
    sensor = EntitySnapshot(
        "sensor.living", "Temp", "sensor", "19", area_id="living",
        unit="°C", device_class="temperature",
    )
    action = ServiceCallPlan(
        "climate", "set_temperature", "climate.living", {"temperature": 21}
    )
    tracker.observe_action(action, (climate, sensor), occurred_at=NOW)
    asyncio.run(tracker.async_observe_states(
        (replace(climate, attributes={"temperature": 24.0}), sensor),
        occurred_at=NOW + timedelta(minutes=5),
    ))
    first = asyncio.run(manager.experiences.async_list())[-1]
    assert first.quality is ExperienceQuality.INVALID
    tracker.observe_action(action, (climate, sensor), occurred_at=NOW)
    asyncio.run(tracker.async_observe_states(
        (replace(climate, state="off"), sensor),
        occurred_at=NOW + timedelta(minutes=5),
    ))
    second = asyncio.run(manager.experiences.async_list())[-1]
    assert second.quality is ExperienceQuality.INVALID


def test_outdoor_sensor_is_not_guessed(tmp_path):
    policy = _policy()
    house = PredictiveHouseModel(policy)
    manager = LearningManager(
        ExperienceStore(tmp_path / "experiences.json", policy),
        ModelRegistry(tmp_path / "models.json", policy),
        house, policy,
    )
    tracker = ThermalExperienceTracker(manager)
    climate = EntitySnapshot("climate.living", "Heat", "climate", "heat", area_id="living")
    room = EntitySnapshot("sensor.room", "Room", "sensor", "19", area_id="living", unit="°C", device_class="temperature")
    outside_a = EntitySnapshot("sensor.out_a", "Outside", "sensor", "8", area_id="garden", unit="°C", device_class="temperature")
    outside_b = replace(outside_a, entity_id="sensor.out_b")
    tracker.observe_action(ServiceCallPlan(
        "climate", "set_temperature", "climate.living", {"temperature": 21}
    ), (climate, room, outside_a, outside_b), occurred_at=NOW)
    assert tracker.active[0].binding.outdoor_temperature_entity_id is None
    assert tracker.active[0].outdoor_celsius is None

    configured_model = train_thermal_model(
        ThermalBinding(
            "living", "sensor.room", "climate.living", "sensor.out_a", True
        ),
        tuple(replace(
            item, temperature_entity_id="sensor.room"
        ) for item in _thermal_observations()),
        policy, now=NOW,
    )
    assert configured_model is not None
    house.install_thermal(configured_model)
    tracker.observe_action(ServiceCallPlan(
        "climate", "set_temperature", "climate.living", {"temperature": 21}
    ), (climate, room, outside_a, outside_b), occurred_at=NOW)
    assert tracker.active[0].binding.outdoor_temperature_entity_id == "sensor.out_a"
    assert tracker.active[0].outdoor_celsius == 8.0


def test_intermediate_thermal_checkpoint_is_observation_only_and_likely_late(tmp_path):
    policy = _policy()
    house = PredictiveHouseModel(policy)
    model = train_thermal_model(
        ThermalBinding("living", "sensor.living", "climate.living", confirmed=True),
        tuple(replace(item, outdoor_celsius=None) for item in _thermal_observations()),
        policy, now=NOW,
    )
    assert model is not None
    house.install_thermal(model)
    manager = LearningManager(
        ExperienceStore(tmp_path / "experiences.json", policy),
        ModelRegistry(tmp_path / "models.json", policy), house, policy,
    )
    tracker = ThermalExperienceTracker(manager)
    runs = GoalRunStore(tmp_path / "runs.json")
    goal = GoalModel(GoalKind.SCHEDULED, goal_id="warm")
    run = replace(
        GoalRun.start(goal, user_id="philipp", person_entity_id=None, now=NOW),
        run_id="run-warm", status=GoalRunStatus.RUNNING,
    )
    asyncio.run(runs.async_append(run))
    checkpoint = ThermalDeadlineCheckpoint(
        ThermalCheckpointPhase.INTERMEDIATE, "warm", "living",
        "climate.living", "sensor.living", 21.0, model.model_id,
        1800, 300, run_id="run-warm",
        deadline=(NOW + timedelta(minutes=10)).isoformat(),
    )
    snapshots = (
        EntitySnapshot("climate.living", "Heat", "climate", "heat", area_id="living"),
        EntitySnapshot("sensor.living", "Temp", "sensor", "19.0", area_id="living", unit="°C", device_class="temperature"),
    )
    result = asyncio.run(async_process_thermal_checkpoint(
        checkpoint, snapshots, tracker, runs, now=NOW
    ))
    assert result is not None and not isinstance(result, bool)
    assert result.status is ThermalCheckpointStatus.LIKELY_LATE
    assert result.device_service_calls == 0


def test_intermediate_checkpoint_status_matrix_has_zero_device_actions(tmp_path):
    policy = _policy()
    base = train_thermal_model(
        ThermalBinding("living", "sensor.living", "climate.living", confirmed=True),
        tuple(replace(item, outdoor_celsius=None) for item in _thermal_observations()),
        policy, now=NOW,
    )
    assert base is not None
    goal = GoalModel(GoalKind.SCHEDULED, goal_id="warm")
    checkpoint = ThermalDeadlineCheckpoint(
        ThermalCheckpointPhase.INTERMEDIATE, "warm", "living",
        "climate.living", "sensor.living", 21.0, base.model_id,
        1800, 300, deadline=(NOW + timedelta(hours=3)).isoformat(),
    )
    climate = EntitySnapshot(
        "climate.living", "Heat", "climate", "heat", area_id="living"
    )

    def evaluate(model, sensor_state: str) -> ThermalCheckpointStatus:
        house = PredictiveHouseModel(policy)
        if model is not None:
            house.install_thermal(model)
        manager = LearningManager(
            ExperienceStore(tmp_path / f"exp-{sensor_state}-{id(model)}.json", policy),
            ModelRegistry(tmp_path / f"models-{sensor_state}-{id(model)}.json", policy),
            house, policy,
        )
        tracker = ThermalExperienceTracker(manager)
        runs = GoalRunStore(tmp_path / f"runs-{sensor_state}-{id(model)}.json")
        asyncio.run(runs.async_append(replace(
            GoalRun.start(goal, user_id="p", person_entity_id=None, now=NOW),
            status=GoalRunStatus.RUNNING,
        )))
        sensor = EntitySnapshot(
            "sensor.living", "Temp", "sensor", sensor_state, area_id="living",
            unit="°C", device_class="temperature",
        )
        result = asyncio.run(async_process_thermal_checkpoint(
            checkpoint, (climate, sensor), tracker, runs, now=NOW
        ))
        assert result is not None and not isinstance(result, bool)
        assert result.device_service_calls == 0
        return result.status

    assert evaluate(base, "19") is ThermalCheckpointStatus.ON_TRACK
    assert evaluate(base, "21") is ThermalCheckpointStatus.TARGET_ALREADY_REACHED
    assert evaluate(None, "19") is ThermalCheckpointStatus.INSUFFICIENT_EVIDENCE
    assert evaluate(base, "unavailable") is ThermalCheckpointStatus.INSUFFICIENT_EVIDENCE
    assert evaluate(replace(
        base, updated_at=NOW - policy.stale_model_age - timedelta(days=1)
    ), "19") is ThermalCheckpointStatus.MODEL_INVALID
    assert evaluate(replace(base, drift_detected=True), "19") is ThermalCheckpointStatus.MODEL_INVALID
    assert evaluate(base, "12") is ThermalCheckpointStatus.MODEL_INVALID


def test_active_thermal_cycle_survives_restart_without_action(tmp_path):
    policy = _policy()
    manager = LearningManager(
        ExperienceStore(tmp_path / "experiences.json", policy),
        ModelRegistry(tmp_path / "models.json", policy),
        PredictiveHouseModel(policy), policy,
    )
    path = tmp_path / "cycles.json"
    tracker = ThermalExperienceTracker(manager, state_path=path)
    climate = EntitySnapshot(
        "climate.living", "Heat", "climate", "heat", area_id="living",
        attributes={"temperature": 21.0},
    )
    sensor = EntitySnapshot("sensor.living", "Temp", "sensor", "19", area_id="living", unit="°C", device_class="temperature")
    tracker.observe_action(ServiceCallPlan(
        "climate", "set_temperature", "climate.living", {"temperature": 21}
    ), (climate, sensor), occurred_at=NOW)
    goal = GoalModel(GoalKind.ACHIEVE_STATE, goal_id="warm")
    run = replace(
        GoalRun.start(goal, user_id="philipp", person_entity_id=None, now=NOW),
        run_id="run-warm", status=GoalRunStatus.RUNNING,
        steps=(StepExecutionRecord("s", "CLIMATE_SET_TEMPERATURE", ("climate.living",), (), True),),
    )
    asyncio.run(tracker.async_associate_goal_run(run))
    runs = GoalRunStore(tmp_path / "runs.json")
    asyncio.run(runs.async_append(run))
    persisted = path.read_text(encoding="utf-8")
    restarted = ThermalExperienceTracker(manager, state_path=path)
    assert asyncio.run(restarted.async_restore(
        (climate, sensor), runs, now=NOW + timedelta(minutes=5)
    )) == 1
    assert len(restarted.active) == 1
    assert asyncio.run(manager.experiences.async_list()) == ()

    for changed_entities, restore_now in (
        ((sensor,), NOW + timedelta(minutes=5)),
        ((replace(climate, attributes={"temperature": 24.0}), sensor),
         NOW + timedelta(minutes=5)),
        ((climate, replace(sensor, area_id="kitchen")),
         NOW + timedelta(minutes=5)),
        ((climate, sensor),
         NOW + policy.maximum_thermal_cycle_duration + timedelta(seconds=1)),
    ):
        path.write_text(persisted, encoding="utf-8")
        discarded = ThermalExperienceTracker(manager, state_path=path)
        assert asyncio.run(discarded.async_restore(
            changed_entities, runs, now=restore_now
        )) == 0
        assert discarded.active == ()
    path.write_text("not-json", encoding="utf-8")
    corrupt = ThermalExperienceTracker(manager, state_path=path)
    assert asyncio.run(corrupt.async_restore(
        (climate, sensor), runs, now=NOW + timedelta(minutes=5)
    )) == 0


def test_stale_effect_timing_and_reliability_are_not_used():
    policy = _policy(minimum_model_samples=2, usable_model_samples=2)
    house = PredictiveHouseModel(policy)
    old = NOW - policy.stale_model_age - timedelta(seconds=1)
    house.install_effect_timing(EffectTimingModel(
        "effect", "LIGHT_TURN_ON", "light.a", 20, 2, 3, 4, 1, 1.0,
        old, old, old + policy.stale_model_age,
    ))
    house.install_reliability(ReliabilityStatistic(
        "reliability", "LIGHT_TURN_ON", "light.a", 19, 1, 20, .95, .9,
        old, old, old + policy.stale_model_age,
    ))
    assert house.predict_effect_latency(
        "LIGHT_TURN_ON", "light.a", now=NOW
    ).status is PredictionStatus.STALE_MODEL
    assert house.predict_reliability(
        "LIGHT_TURN_ON", "light.a", now=NOW
    ).status is PredictionStatus.STALE_MODEL


def test_tombstone_gc_cannot_resurrect_retained_evidence(tmp_path):
    policy = _policy()
    registry = ModelRegistry(tmp_path / "models.json", policy)
    model = LearnedModel(
        "habit:x", LearnedKind.HABIT, "philipp", {}, {},
        knowledge_state=KnowledgeState.INFERRED,
        confidence=.8, sample_count=20, first_observed=NOW - timedelta(days=30),
        last_observed=NOW, provenance=("goal_run:x",), health=ModelHealth.VALID,
    )
    asyncio.run(registry.async_upsert(model))
    asyncio.run(registry.async_delete_with_reason(
        model.model_id, deleted_at=NOW, reason="user_forget"
    ))
    assert asyncio.run(registry.async_gc_tombstones(
        oldest_retained_evidence_at=NOW - timedelta(days=1)
    )) == 0
    assert asyncio.run(registry.async_upsert(model)).value == "suppressed"
    assert asyncio.run(registry.async_gc_tombstones(
        oldest_retained_evidence_at=NOW + timedelta(seconds=1)
    )) == 1
    asyncio.run(registry.async_upsert(model))
    asyncio.run(registry.async_delete_with_reason(
        model.model_id, deleted_at=NOW, reason="user_rejected_habit",
        suppression_kind=SuppressionKind.REJECTED_HABIT,
    ))
    assert asyncio.run(registry.async_gc_tombstones(
        oldest_retained_evidence_at=NOW + timedelta(days=500)
    )) == 0


def test_forged_and_replayed_thermal_checkpoints_are_rejected(tmp_path):
    store = PendingThermalCheckpointStore(tmp_path / "checkpoints.json")
    checkpoint = ThermalDeadlineCheckpoint(
        ThermalCheckpointPhase.INTERMEDIATE, "goal", "living",
        "climate.living", "sensor.living", 21, "thermal:living", 1000, 100,
        "checkpoint", "secret-token", "run", NOW.isoformat(),
        (NOW + timedelta(hours=1)).isoformat(),
    )
    assert asyncio.run(store.async_register(
        checkpoint, scheduled_for=NOW, deadline=NOW + timedelta(hours=1)
    ))
    assert not asyncio.run(store.async_consume(
        replace(checkpoint, token="forged"), now=NOW
    ))
    assert not asyncio.run(store.async_consume(
        replace(checkpoint, goal_id="wrong-goal"), now=NOW
    ))
    assert not asyncio.run(store.async_consume(
        replace(checkpoint, climate_entity_id="climate.other"), now=NOW
    ))
    assert not asyncio.run(store.async_consume(
        replace(checkpoint, phase=ThermalCheckpointPhase.FINAL), now=NOW
    ))
    assert not asyncio.run(store.async_consume(
        replace(
            checkpoint,
            scheduled_for=(NOW + timedelta(seconds=1)).isoformat(),
        ),
        now=NOW,
    ))
    assert not asyncio.run(store.async_consume(
        replace(
            checkpoint,
            deadline=(NOW + timedelta(hours=2)).isoformat(),
        ),
        now=NOW,
    ))
    assert not asyncio.run(store.async_consume(
        checkpoint, now=NOW + timedelta(hours=2)
    ))
    assert asyncio.run(store.async_consume(checkpoint, now=NOW))
    assert not asyncio.run(store.async_consume(checkpoint, now=NOW))


def test_checkpoint_authority_store_fails_closed_and_deletes(tmp_path):
    path = tmp_path / "checkpoints.json"
    store = PendingThermalCheckpointStore(path)
    checkpoint = ThermalDeadlineCheckpoint(
        ThermalCheckpointPhase.INTERMEDIATE, "goal", "living",
        "climate.living", "sensor.living", 21, "thermal:living", 1000, 100,
        "checkpoint", "token", "run", NOW.isoformat(),
        (NOW + timedelta(hours=1)).isoformat(),
    )
    assert not asyncio.run(store.async_register(
        replace(checkpoint, token=""), scheduled_for=NOW,
        deadline=NOW + timedelta(hours=1),
    ))
    assert asyncio.run(store.async_register(
        checkpoint, scheduled_for=NOW, deadline=NOW + timedelta(hours=1)
    ))
    assert not asyncio.run(store.async_consume(
        checkpoint, now=NOW.replace(tzinfo=None)
    ))
    asyncio.run(store.async_delete(checkpoint.checkpoint_id))
    assert not asyncio.run(store.async_consume(checkpoint, now=NOW))
    path.write_text("not-json", encoding="utf-8")
    assert not asyncio.run(store.async_consume(checkpoint, now=NOW))


def test_restored_stale_models_do_not_gain_planning_authority(tmp_path):
    policy = _policy()
    binding = ThermalBinding("living", "sensor.living", "climate.living", confirmed=True)
    thermal = train_thermal_model(
        binding, tuple(replace(item, outdoor_celsius=None) for item in _thermal_observations()),
        policy, now=NOW - policy.stale_model_age - timedelta(days=1),
    )
    assert thermal is not None
    registry = ModelRegistry(tmp_path / "models.json", policy)
    asyncio.run(registry.async_upsert(thermal_model_to_learned(thermal, policy)))
    house = PredictiveHouseModel(policy)
    manager = LearningManager(
        ExperienceStore(tmp_path / "experiences.json", policy), registry,
        house, policy,
    )
    asyncio.run(manager.async_restore_models())
    prediction = house.predict_thermal(
        "living", current_celsius=19, target_celsius=21,
        outdoor_celsius=None, now=NOW,
    )
    assert prediction.status is PredictionStatus.STALE_MODEL
    assert not prediction.usable_for_planning
