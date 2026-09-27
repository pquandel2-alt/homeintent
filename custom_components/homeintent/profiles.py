"""Confirmed local routine and comfort profile definitions."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, cast

from .goal_model import DesiredState, GoalScope


@dataclass(frozen=True)
class RoutineStepDefinition:
    step_id: str
    scope: GoalScope
    desired_state: DesiredState
    description: str = ""


@dataclass(frozen=True)
class RoutineDefinition:
    routine_id: str
    name: str
    owner_user_id: str
    steps: tuple[RoutineStepDefinition, ...]
    confirmed: bool


@dataclass(frozen=True)
class ComfortProfile:
    profile_id: str
    owner_user_id: str
    area_id: str
    temperature_min: float | None = None
    temperature_max: float | None = None
    brightness_min: int | None = None
    brightness_max: int | None = None
    color_temperature_kelvin: int | None = None
    humidity_min: float | None = None
    humidity_max: float | None = None
    cover_position: int | None = None
    confirmed: bool = False
    household_user_ids: tuple[str, ...] = ()


class ProfileStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._routines: dict[str, RoutineDefinition] = {}
        self._comfort: dict[str, ComfortProfile] = {}
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        raw = await asyncio.to_thread(self._read)
        self._routines = {
            item.routine_id: item
            for value in _mapping_sequence(raw.get("routines", ()))
            if (item := _routine_from(value)) is not None
        }
        self._comfort = {
            item.profile_id: item
            for value in _mapping_sequence(raw.get("comfort_profiles", ()))
            if (item := _comfort_from(value)) is not None
        }

    async def async_save_routine(
        self, routine: RoutineDefinition, *, confirmed: bool
    ) -> None:
        if not confirmed or not routine.confirmed:
            raise ValueError("Routines require explicit confirmation")
        if not routine.steps:
            raise ValueError("A routine must contain at least one typed step")
        async with self._lock:
            self._routines[routine.routine_id] = routine
            await asyncio.to_thread(self._write)

    async def async_save_comfort_profile(
        self, profile: ComfortProfile, *, confirmed: bool
    ) -> None:
        if not confirmed or not profile.confirmed:
            raise ValueError("Comfort profiles require explicit confirmation")
        _validate_comfort(profile)
        async with self._lock:
            self._comfort[profile.profile_id] = profile
            await asyncio.to_thread(self._write)

    def routines_for(self, user_id: str) -> tuple[RoutineDefinition, ...]:
        """Every confirmed routine the user owns (for spoken names, F14)."""
        return tuple(
            item for item in self._routines.values()
            if item.confirmed and item.owner_user_id == user_id
        )

    def routine(self, name_or_id: str, *, user_id: str) -> RoutineDefinition | None:
        key = name_or_id.casefold().strip()
        matches = [
            item for item in self._routines.values()
            if item.confirmed and item.owner_user_id == user_id
            and (item.routine_id == name_or_id or item.name.casefold().strip() == key)
        ]
        return matches[0] if len(matches) == 1 else None

    def comfort(self, *, area_id: str, user_id: str) -> ComfortProfile | None:
        matches = [
            item for item in self._comfort.values()
            if item.confirmed and item.area_id == area_id and item.owner_user_id == user_id
        ]
        return matches[0] if len(matches) == 1 else None

    def comfort_profiles(
        self, *, area_id: str, user_ids: tuple[str, ...]
    ) -> tuple[ComfortProfile, ...]:
        users = set(user_ids)
        return tuple(
            item for item in self._comfort.values()
            if item.confirmed and item.area_id == area_id
            and not item.household_user_ids and item.owner_user_id in users
        )

    def shared_comfort(
        self, *, area_id: str, user_ids: tuple[str, ...]
    ) -> ComfortProfile | None:
        exact = tuple(sorted(set(user_ids)))
        matches = tuple(
            item for item in self._comfort.values()
            if item.confirmed and item.area_id == area_id
            and tuple(sorted(item.household_user_ids)) == exact
            and len(exact) > 1
        )
        return matches[0] if len(matches) == 1 else None

    def _read(self) -> dict[str, object]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return dict(cast(Mapping[str, object], value)) if isinstance(value, Mapping) else {}

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "routines": [_routine_dict(item) for item in self._routines.values()],
            "comfort_profiles": [_comfort_dict(item) for item in self._comfort.values()],
        }
        descriptor, temporary = tempfile.mkstemp(
            prefix=".homeintent_profiles_", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


def _validate_comfort(profile: ComfortProfile) -> None:
    if profile.temperature_min is not None and not 5 <= profile.temperature_min <= 35:
        raise ValueError("Invalid temperature range")
    if profile.temperature_max is not None and not 5 <= profile.temperature_max <= 35:
        raise ValueError("Invalid temperature range")
    if profile.temperature_min is not None and profile.temperature_max is not None:
        if profile.temperature_min > profile.temperature_max:
            raise ValueError("Invalid temperature range")
    if profile.brightness_min is not None and not 0 <= profile.brightness_min <= 100:
        raise ValueError("Invalid brightness range")
    if profile.brightness_max is not None and not 0 <= profile.brightness_max <= 100:
        raise ValueError("Invalid brightness range")
    if profile.brightness_min is not None and profile.brightness_max is not None:
        if profile.brightness_min > profile.brightness_max:
            raise ValueError("Invalid brightness range")
    if profile.cover_position is not None and not 0 <= profile.cover_position <= 100:
        raise ValueError("Invalid cover position")


def _routine_dict(value: RoutineDefinition) -> dict[str, object]:
    return {
        "routine_id": value.routine_id,
        "name": value.name,
        "owner_user_id": value.owner_user_id,
        "confirmed": value.confirmed,
        "steps": [
            {
                "step_id": step.step_id,
                "description": step.description,
                "scope": _scope_dict(step.scope),
                "desired_state": {
                    "property_name": step.desired_state.property_name,
                    "value": step.desired_state.value,
                    "unit": step.desired_state.unit,
                    "minimum": step.desired_state.minimum,
                    "maximum": step.desired_state.maximum,
                },
            }
            for step in value.steps
        ],
    }


def _routine_from(raw: Mapping[str, object]) -> RoutineDefinition | None:
    try:
        steps = tuple(
            RoutineStepDefinition(
                str(item["step_id"]),
                _scope_from(item.get("scope")),
                _desired_from(item.get("desired_state")),
                str(item.get("description", "")),
            )
            for item in _mapping_sequence(raw.get("steps", ()))
        )
        return RoutineDefinition(
            str(raw["routine_id"]), str(raw["name"]), str(raw["owner_user_id"]),
            steps, bool(raw.get("confirmed", False)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _comfort_dict(value: ComfortProfile) -> dict[str, object]:
    return {
        "profile_id": value.profile_id,
        "owner_user_id": value.owner_user_id,
        "area_id": value.area_id,
        "temperature_min": value.temperature_min,
        "temperature_max": value.temperature_max,
        "brightness_min": value.brightness_min,
        "brightness_max": value.brightness_max,
        "color_temperature_kelvin": value.color_temperature_kelvin,
        "humidity_min": value.humidity_min,
        "humidity_max": value.humidity_max,
        "cover_position": value.cover_position,
        "confirmed": value.confirmed,
        "household_user_ids": list(value.household_user_ids),
    }


def _comfort_from(raw: Mapping[str, object]) -> ComfortProfile | None:
    try:
        value = ComfortProfile(
            str(raw["profile_id"]), str(raw["owner_user_id"]), str(raw["area_id"]),
            _number(raw.get("temperature_min")), _number(raw.get("temperature_max")),
            _integer(raw.get("brightness_min")), _integer(raw.get("brightness_max")),
            _integer(raw.get("color_temperature_kelvin")),
            _number(raw.get("humidity_min")), _number(raw.get("humidity_max")),
            _integer(raw.get("cover_position")), bool(raw.get("confirmed", False)),
            _strings(raw.get("household_user_ids")),
        )
        _validate_comfort(value)
        return value
    except (KeyError, TypeError, ValueError):
        return None


def _scope_dict(scope: GoalScope) -> dict[str, object]:
    return {
        "entity_ids": list(scope.entity_ids), "domain": scope.domain,
        "device_class": scope.device_class, "area_id": scope.area_id,
        "floor_id": scope.floor_id, "person_ids": list(scope.person_ids),
        "excluded_entity_ids": list(scope.excluded_entity_ids),
        "excluded_area_ids": list(scope.excluded_area_ids),
    }


def _scope_from(value: object) -> GoalScope:
    if not isinstance(value, Mapping):
        return GoalScope()
    mapping = cast(Mapping[str, object], value)
    return GoalScope(
        _strings(mapping.get("entity_ids")), _text(mapping.get("domain")),
        _text(mapping.get("device_class")), _text(mapping.get("area_id")),
        _text(mapping.get("floor_id")), _strings(mapping.get("person_ids")),
        _strings(mapping.get("excluded_entity_ids")),
        _strings(mapping.get("excluded_area_ids")),
    )


def _desired_from(value: object) -> DesiredState:
    if not isinstance(value, Mapping):
        raise ValueError("Missing desired state")
    mapping = cast(Mapping[str, object], value)
    raw_value = mapping.get("value")
    if not isinstance(raw_value, (str, int, float, bool)):
        raise ValueError("Invalid desired value")
    return DesiredState(
        str(mapping.get("property_name", "state")), raw_value,
        _text(mapping.get("unit")), _number(mapping.get("minimum")),
        _number(mapping.get("maximum")),
    )


def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    values = cast(Sequence[object], value)
    return tuple(cast(Mapping[str, object], item) for item in values if isinstance(item, Mapping))


def _strings(value: object) -> tuple[str, ...]:
    return tuple(item for item in cast(Sequence[object], value) if isinstance(item, str)) if isinstance(value, (list, tuple)) else ()


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _integer(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None


__all__ = (
    "ComfortProfile", "ProfileStore", "RoutineDefinition", "RoutineStepDefinition",
)
