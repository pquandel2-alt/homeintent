"""Typed persistent checkpoints for an advised thermal deadline goal."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Mapping, cast

from .entities import EntitySnapshot
from .goal_run import (
    FailureCode,
    GoalRun,
    GoalRunStatus,
    GoalRunStore,
    StepExecutionRecord,
    VerificationRecord,
)
from .nlu.primitives import SemanticProperty
from .nlu.unit_reasoning import normalize_measurement
from .service_call import ServiceCallPlan
from .thermal_tracker import ThermalExperienceTracker
from .prediction import PredictionStatus


class ThermalCheckpointPhase(StrEnum):
    START = "start"
    INTERMEDIATE = "intermediate"
    FINAL = "final"


class ThermalCheckpointStatus(StrEnum):
    ON_TRACK = "on_track"
    LIKELY_LATE = "likely_late"
    TARGET_ALREADY_REACHED = "target_already_reached"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    MODEL_INVALID = "model_invalid"


@dataclass(frozen=True)
class ThermalCheckpointResult:
    status: ThermalCheckpointStatus
    measured_celsius: float | None
    target_celsius: float
    predicted_completion: datetime | None
    model_id: str
    device_service_calls: int = 0


@dataclass(frozen=True)
class ThermalDeadlineCheckpoint:
    """Narrow evidence payload carried by a HomeIntent-owned one-shot."""

    phase: ThermalCheckpointPhase
    goal_id: str
    area_id: str
    climate_entity_id: str
    measurement_entity_id: str
    target_value: float
    model_id: str
    predicted_duration_seconds: float
    uncertainty_buffer_seconds: float
    checkpoint_id: str = ""
    token: str = ""
    run_id: str = ""
    scheduled_for: str | None = None
    deadline: str | None = None

    def to_service_data(self) -> dict[str, str | float]:
        return {
            "phase": self.phase.value,
            "goal_id": self.goal_id,
            "area_id": self.area_id,
            "climate_entity_id": self.climate_entity_id,
            "measurement_entity_id": self.measurement_entity_id,
            "target_value": self.target_value,
            "model_id": self.model_id,
            "predicted_duration_seconds": self.predicted_duration_seconds,
            "uncertainty_buffer_seconds": self.uncertainty_buffer_seconds,
            "checkpoint_id": self.checkpoint_id,
            "token": self.token,
            "run_id": self.run_id,
            "scheduled_for": self.scheduled_for or "",
            "deadline": self.deadline or "",
        }

    @classmethod
    def from_service_data(
        cls, raw: Mapping[str, object]
    ) -> ThermalDeadlineCheckpoint | None:
        try:
            phase = ThermalCheckpointPhase(str(raw["phase"]))
            texts = tuple(
                str(raw[key])
                for key in (
                    "goal_id", "area_id", "climate_entity_id",
                    "measurement_entity_id", "model_id",
                )
            )
            target = _finite_number(raw["target_value"])
            duration = _finite_number(raw["predicted_duration_seconds"])
            buffer = _finite_number(raw["uncertainty_buffer_seconds"])
        except (KeyError, TypeError, ValueError):
            return None
        if (
            any(not value or len(value) > 255 for value in texts)
            or not texts[2].startswith("climate.")
            or "." not in texts[3]
            or target is None or not -100.0 <= target <= 150.0
            or duration is None or duration <= 0.0
            or buffer is None or buffer < 0.0
        ):
            return None
        return cls(
            phase=phase,
            goal_id=texts[0],
            area_id=texts[1],
            climate_entity_id=texts[2],
            measurement_entity_id=texts[3],
            target_value=target,
            model_id=texts[4],
            predicted_duration_seconds=duration,
            uncertainty_buffer_seconds=buffer,
            checkpoint_id=str(raw.get("checkpoint_id", "")),
            token=str(raw.get("token", "")),
            run_id=str(raw.get("run_id", "")),
            scheduled_for=_optional_text(raw.get("scheduled_for")),
            deadline=_optional_text(raw.get("deadline")),
        )


@dataclass(frozen=True)
class PendingThermalCheckpoint:
    checkpoint_id: str
    token_hash: str
    goal_id: str
    run_id: str
    phase: ThermalCheckpointPhase
    model_id: str
    climate_entity_id: str
    measurement_entity_id: str
    scheduled_for: datetime
    deadline: datetime
    consumed: bool = False


class PendingThermalCheckpointStore:
    """Bounded authority for one-shot internal checkpoint calls."""

    def __init__(self, path: str | Path, *, limit: int = 100) -> None:
        self.path = Path(path)
        self.limit = max(1, min(limit, 1000))
        self._lock = asyncio.Lock()

    async def async_register(
        self, checkpoint: ThermalDeadlineCheckpoint, *, scheduled_for: datetime,
        deadline: datetime,
    ) -> bool:
        if (
            not checkpoint.checkpoint_id or not checkpoint.token
            or not checkpoint.run_id or scheduled_for.tzinfo is None
            or deadline.tzinfo is None
        ):
            return False
        record = PendingThermalCheckpoint(
            checkpoint.checkpoint_id, _token_hash(checkpoint.token),
            checkpoint.goal_id, checkpoint.run_id, checkpoint.phase,
            checkpoint.model_id, checkpoint.climate_entity_id,
            checkpoint.measurement_entity_id, scheduled_for, deadline,
        )
        async with self._lock:
            records = await asyncio.to_thread(self._read)
            records[record.checkpoint_id] = record
            bounded = dict(sorted(
                records.items(), key=lambda item: item[1].scheduled_for
            )[-self.limit:])
            await asyncio.to_thread(self._write, bounded)
        return True

    async def async_consume(
        self, checkpoint: ThermalDeadlineCheckpoint, *, now: datetime
    ) -> bool:
        if now.tzinfo is None:
            return False
        requested_schedule = _aware_datetime(checkpoint.scheduled_for)
        requested_deadline = _aware_datetime(checkpoint.deadline)
        async with self._lock:
            records = await asyncio.to_thread(self._read)
            record = records.get(checkpoint.checkpoint_id)
            valid = (
                record is not None and not record.consumed
                and hmac.compare_digest(record.token_hash, _token_hash(checkpoint.token))
                and record.goal_id == checkpoint.goal_id
                and record.run_id == checkpoint.run_id
                and record.phase is checkpoint.phase
                and record.model_id == checkpoint.model_id
                and record.climate_entity_id == checkpoint.climate_entity_id
                and record.measurement_entity_id == checkpoint.measurement_entity_id
                and requested_schedule == record.scheduled_for
                and requested_deadline == record.deadline
                and now <= record.deadline + timedelta(minutes=10)
            )
            if not valid or record is None:
                return False
            records[record.checkpoint_id] = replace(record, consumed=True)
            await asyncio.to_thread(self._write, records)
            return True

    async def async_delete(self, checkpoint_id: str) -> None:
        async with self._lock:
            records = await asyncio.to_thread(self._read)
            if records.pop(checkpoint_id, None) is not None:
                await asyncio.to_thread(self._write, records)

    def _read(self) -> dict[str, PendingThermalCheckpoint]:
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        if not isinstance(raw, dict):
            return {}
        document = cast(Mapping[str, object], raw)
        if document.get("schema_version") != 1:
            return {}
        result: dict[str, PendingThermalCheckpoint] = {}
        values = document.get("checkpoints")
        if not isinstance(values, list):
            return result
        for value in cast(list[object], values):
            if not isinstance(value, dict):
                continue
            document_item = cast(Mapping[str, object], value)
            try:
                checkpoint_item = PendingThermalCheckpoint(
                    str(document_item["checkpoint_id"]),
                    str(document_item["token_hash"]),
                    str(document_item["goal_id"]), str(document_item["run_id"]),
                    ThermalCheckpointPhase(str(document_item["phase"])),
                    str(document_item["model_id"]),
                    str(document_item["climate_entity_id"]),
                    str(document_item["measurement_entity_id"]),
                    datetime.fromisoformat(str(document_item["scheduled_for"])),
                    datetime.fromisoformat(str(document_item["deadline"])),
                    bool(document_item.get("consumed", False)),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if (
                checkpoint_item.scheduled_for.tzinfo is not None
                and checkpoint_item.deadline.tzinfo is not None
            ):
                result[checkpoint_item.checkpoint_id] = checkpoint_item
        return result

    def _write(self, records: Mapping[str, PendingThermalCheckpoint]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".homeintent_checkpoints_", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "checkpoints": [{
                    "checkpoint_id": item.checkpoint_id,
                    "token_hash": item.token_hash,
                    "goal_id": item.goal_id,
                    "run_id": item.run_id,
                    "phase": item.phase.value,
                    "model_id": item.model_id,
                    "climate_entity_id": item.climate_entity_id,
                    "measurement_entity_id": item.measurement_entity_id,
                    "scheduled_for": item.scheduled_for.isoformat(),
                    "deadline": item.deadline.isoformat(),
                    "consumed": item.consumed,
                } for item in records.values()]}, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


def checkpoint_service_action(
    checkpoint: ThermalDeadlineCheckpoint,
) -> dict[str, object]:
    """Render the sole internal, non-actuating checkpoint service action."""
    return {
        "action": "homeintent.thermal_deadline_checkpoint",
        "data": checkpoint.to_service_data(),
    }


def append_start_checkpoint(
    config: Mapping[str, object], checkpoint: ThermalDeadlineCheckpoint
) -> dict[str, object] | None:
    """Insert START before a one-shot's self-delete without changing its action."""
    if checkpoint.phase is not ThermalCheckpointPhase.START:
        return None
    raw_actions = config.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        return None
    actions = list(cast(list[object], raw_actions))
    insertion = len(actions)
    last = actions[-1]
    if (
        isinstance(last, Mapping)
        and cast(Mapping[str, object], last).get("action")
        == "homeintent.delete_automation"
    ):
        insertion -= 1
    actions.insert(insertion, checkpoint_service_action(checkpoint))
    return {**config, "actions": actions}


def checkpoint_automation_config(
    checkpoint: ThermalDeadlineCheckpoint,
    *,
    scheduled_for: datetime,
    automation_id: str,
) -> dict[str, object] | None:
    """Build a date-guarded one-shot which can only report a checkpoint."""
    if (
        checkpoint.phase is ThermalCheckpointPhase.START
        or scheduled_for.tzinfo is None
        or not automation_id
    ):
        return None
    return {
        "alias": f"HomeIntent thermal {checkpoint.phase.value}: {checkpoint.goal_id}",
        "triggers": [{
            "trigger": "time",
            "at": scheduled_for.strftime("%H:%M:%S"),
        }],
        "conditions": [{
            "condition": "template",
            "value_template": (
                "{{ now().date().isoformat() == '"
                + scheduled_for.date().isoformat()
                + "' }}"
            ),
        }],
        "actions": [
            checkpoint_service_action(checkpoint),
            {
                "action": "homeintent.delete_automation",
                "data": {"automation_id": automation_id},
            },
        ],
    }


async def async_process_thermal_checkpoint(
    checkpoint: ThermalDeadlineCheckpoint,
    snapshots: tuple[EntitySnapshot, ...],
    tracker: ThermalExperienceTracker,
    goal_runs: GoalRunStore,
    *,
    now: datetime,
) -> bool | ThermalCheckpointResult | None:
    """Observe one checkpoint and finalize the existing scheduled GoalRun."""
    if now.tzinfo is None:
        raise ValueError("Thermal checkpoint timestamps require a timezone")
    if checkpoint.phase is ThermalCheckpointPhase.START:
        tracker.observe_action(
            ServiceCallPlan(
                "climate", "set_temperature", checkpoint.climate_entity_id,
                {"temperature": checkpoint.target_value},
            ),
            snapshots,
            occurred_at=now,
        )
        scheduled = await _checkpoint_run(goal_runs, checkpoint)
        if scheduled is not None:
            await tracker.async_associate_goal_run(scheduled)
            await goal_runs.async_append(replace(
                scheduled,
                updated_at=now.isoformat(),
                status=GoalRunStatus.RUNNING,
                evidence=(*scheduled.evidence, "thermal_start_observed"),
            ))
        return None
    if checkpoint.phase is ThermalCheckpointPhase.INTERMEDIATE:
        sensor = next((item for item in snapshots
                       if item.entity_id == checkpoint.measurement_entity_id), None)
        climate = next((item for item in snapshots
                        if item.entity_id == checkpoint.climate_entity_id), None)
        measured = _snapshot_temperature(sensor)
        target = checkpoint.target_value
        status = ThermalCheckpointStatus.INSUFFICIENT_EVIDENCE
        eta: datetime | None = None
        model = tracker.predictive_house.thermal_model(checkpoint.area_id)
        current_setpoint = (
            _finite_number(climate.attributes.get("temperature"))
            if climate is not None else None
        )
        if measured is not None and measured >= target - 0.1:
            status = ThermalCheckpointStatus.TARGET_ALREADY_REACHED
            eta = now
        elif (
            current_setpoint is not None
            and abs(current_setpoint - target) > 0.1
        ):
            status = ThermalCheckpointStatus.MODEL_INVALID
        elif model is None or model.model_id != checkpoint.model_id:
            status = ThermalCheckpointStatus.INSUFFICIENT_EVIDENCE
        elif measured is not None:
            outdoor = (
                next((item for item in snapshots
                      if item.entity_id
                      == model.binding.outdoor_temperature_entity_id), None)
                if model.binding.outdoor_temperature_entity_id is not None else None
            )
            prediction = tracker.predictive_house.predict_thermal(
                checkpoint.area_id, current_celsius=measured,
                target_celsius=target,
                outdoor_celsius=_snapshot_temperature(outdoor), now=now,
            )
            if prediction.status is PredictionStatus.OK and prediction.value is not None:
                eta = now + timedelta(seconds=prediction.value)
                deadline = _aware_datetime(checkpoint.deadline)
                status = (
                    ThermalCheckpointStatus.LIKELY_LATE
                    if deadline is not None and eta > deadline
                    else ThermalCheckpointStatus.ON_TRACK
                )
            elif prediction.status in {
                PredictionStatus.MODEL_INVALID,
                PredictionStatus.MODEL_UNRELIABLE,
                PredictionStatus.DRIFT_DETECTED,
                PredictionStatus.STALE_MODEL,
                PredictionStatus.OUT_OF_DISTRIBUTION,
            }:
                status = ThermalCheckpointStatus.MODEL_INVALID
        scheduled = await _checkpoint_run(goal_runs, checkpoint)
        if scheduled is not None:
            evidence = (
                "thermal_checkpoint:intermediate",
                f"thermal_measured_celsius={measured:g}" if measured is not None else "thermal_measured_celsius=unavailable",
                f"thermal_target_celsius={target:g}",
                f"thermal_checkpoint_status={status.value}",
                f"thermal_checkpoint_model={checkpoint.model_id}",
                f"thermal_checkpoint_eta={eta.isoformat()}" if eta is not None else "thermal_checkpoint_eta=unavailable",
            )
            await goal_runs.async_append(replace(
                scheduled, updated_at=now.isoformat(),
                evidence=(*scheduled.evidence, *evidence)[-100:],
            ))
        return ThermalCheckpointResult(
            status, measured, target, eta, checkpoint.model_id
        )

    await tracker.async_finalize(
        checkpoint.climate_entity_id, snapshots, occurred_at=now
    )
    scheduled = await _checkpoint_run(goal_runs, checkpoint)
    if scheduled is None:
        return None
    sensor = next(
        (item for item in snapshots
         if item.entity_id == checkpoint.measurement_entity_id),
        None,
    )
    observed: str | None = None
    current_celsius: float | None = None
    target_celsius: float | None = None
    if sensor is not None and sensor.state not in {"unknown", "unavailable"}:
        observed = sensor.state
        try:
            current = normalize_measurement(
                float(sensor.state), sensor.unit, SemanticProperty.TEMPERATURE
            )
            target = normalize_measurement(
                checkpoint.target_value, sensor.unit, SemanticProperty.TEMPERATURE
            )
        except ValueError:
            current = target = None
        current_celsius = current.value if current is not None else None
        target_celsius = target.value if target is not None else None
    success = (
        current_celsius is not None and target_celsius is not None
        and current_celsius >= target_celsius - 0.1
    )
    failure = None if success else (
        FailureCode.TARGET_UNAVAILABLE if observed is None else FailureCode.WRONG_STATE
    )
    start_observed = "thermal_start_observed" in scheduled.evidence
    verification = VerificationRecord(
        checkpoint.measurement_entity_id,
        f"temperature={target_celsius:g}"
        if target_celsius is not None else "temperature=unknown",
        observed,
        success,
        failure,
        now.isoformat(),
    )
    final_run = replace(
        scheduled,
        updated_at=now.isoformat(),
        selected_targets=(checkpoint.climate_entity_id,),
        steps=(*(
            step for step in scheduled.steps
            if step.step_id != "scheduled-setpoint"
        ), StepExecutionRecord(
            "thermal-final-verification",
            "CLIMATE_SET_TEMPERATURE" if start_observed else None,
            (checkpoint.climate_entity_id,), ("confirmed_measurement_binding",),
            True if start_observed else None, (verification,),
            failure if start_observed else FailureCode.SERVICE_ERROR,
            "Thermisches Ergebnis zum bestätigten Zieltermin geprüft.",
        )),
        status=GoalRunStatus.SUCCESS if success else GoalRunStatus.FAILURE,
        failures=tuple(dict.fromkeys((
            *((FailureCode.SERVICE_ERROR,) if not start_observed else ()),
            *((failure,) if failure is not None else ()),
        ))),
        evidence=(*scheduled.evidence,
                  f"prediction_model={checkpoint.model_id}",
                  f"prediction_seconds={checkpoint.predicted_duration_seconds:g}",
                  f"uncertainty_buffer_seconds={checkpoint.uncertainty_buffer_seconds:g}"),
    )
    await goal_runs.async_append(final_run)
    return success


def _finite_number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    result = float(value)
    return result if result == result and abs(result) != float("inf") else None


async def _checkpoint_run(
    goal_runs: GoalRunStore, checkpoint: ThermalDeadlineCheckpoint
) -> GoalRun | None:
    """Resolve the exact authenticated run; never fall back to another run."""
    return next((
        item for item in reversed(await goal_runs.async_list())
        if item.run_id == checkpoint.run_id and item.goal_id == checkpoint.goal_id
    ), None)


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _aware_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result if result.tzinfo is not None else None


def _snapshot_temperature(entity: EntitySnapshot | None) -> float | None:
    if entity is None or entity.state in {"unknown", "unavailable"}:
        return None
    try:
        normalized = normalize_measurement(
            float(entity.state), entity.unit, SemanticProperty.TEMPERATURE
        )
    except ValueError:
        return None
    return normalized.value if normalized is not None else None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = (
    "PendingThermalCheckpoint", "PendingThermalCheckpointStore",
    "ThermalCheckpointPhase", "ThermalCheckpointResult",
    "ThermalCheckpointStatus", "ThermalDeadlineCheckpoint",
    "async_process_thermal_checkpoint",
    "append_start_checkpoint", "checkpoint_automation_config",
    "checkpoint_service_action",
)
