"""Bounded inverse plans for recently executed, safely reversible actions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .entities import EntitySnapshot
from .service_call import ServiceCallPlan


_UNDO_RE = re.compile(
    r"^\s*(?:(?:mach|mache|setz|setze|nimm)\s+(?:das|es|den\s+befehl)\s+"
    r"(?:rückgängig|rueckgaengig|zurück|zurueck)"
    r"|(?:rückgängig|rueckgaengig)(?:\s+bitte)?)\s*[.!?]*\s*$",
    re.I,
)


@dataclass(frozen=True)
class UndoPlan:
    plans: tuple[ServiceCallPlan, ...]
    requested_by_user_id: str | None = None


def is_undo_request(text: str) -> bool:
    return _UNDO_RE.fullmatch(text) is not None


def _on_off_plan(entity: EntitySnapshot) -> ServiceCallPlan | None:
    if entity.state not in {"on", "off"}:
        return None
    return ServiceCallPlan(
        "homeassistant",
        "turn_on" if entity.state == "on" else "turn_off",
        entity.entity_id,
    )


def _entity_undo_plans(entity: EntitySnapshot) -> tuple[ServiceCallPlan, ...]:
    """Return exact inverse calls, or no calls when restoration is unsafe."""
    if entity.domain in {"switch", "input_boolean"}:
        plan = _on_off_plan(entity)
        return (plan,) if plan is not None else ()

    if entity.domain == "light":
        if entity.state == "off":
            return (ServiceCallPlan("light", "turn_off", entity.entity_id),)
        if entity.state != "on":
            return ()
        data = {}
        for key in ("brightness", "color_temp_kelvin", "rgb_color"):
            value = entity.attributes.get(key)
            if value is not None:
                data[key] = value
        return (ServiceCallPlan("light", "turn_on", entity.entity_id, data),)

    if entity.domain == "fan":
        if entity.state == "off":
            return (ServiceCallPlan("fan", "turn_off", entity.entity_id),)
        if entity.state != "on":
            return ()
        data = {}
        if isinstance(entity.attributes.get("percentage"), (int, float)):
            data["percentage"] = entity.attributes["percentage"]
        if isinstance(entity.attributes.get("preset_mode"), str):
            data["preset_mode"] = entity.attributes["preset_mode"]
        return (ServiceCallPlan("fan", "turn_on", entity.entity_id, data),)

    if entity.domain == "cover":
        position = entity.attributes.get("current_position")
        if isinstance(position, (int, float)):
            return (
                ServiceCallPlan(
                    "cover", "set_cover_position", entity.entity_id,
                    {"position": round(position)},
                ),
            )
        if entity.state in {"open", "closed"}:
            return (
                ServiceCallPlan(
                    "cover",
                    "open_cover" if entity.state == "open" else "close_cover",
                    entity.entity_id,
                ),
            )
        return ()

    if entity.domain == "climate":
        if entity.state == "off":
            return (ServiceCallPlan("climate", "turn_off", entity.entity_id),)
        if not entity.state or entity.state in {"unknown", "unavailable"}:
            return ()
        plans = [
            ServiceCallPlan(
                "climate", "set_hvac_mode", entity.entity_id,
                {"hvac_mode": entity.state},
            )
        ]
        temperature = entity.attributes.get("temperature")
        if isinstance(temperature, (int, float)):
            plans.append(
                ServiceCallPlan(
                    "climate", "set_temperature", entity.entity_id,
                    {"temperature": temperature},
                )
            )
        return tuple(plans)

    if entity.domain == "humidifier":
        plans = () if entity.state not in {"on", "off"} else (
            ServiceCallPlan(
                "humidifier",
                "turn_on" if entity.state == "on" else "turn_off",
                entity.entity_id,
            ),
        )
        humidity = entity.attributes.get("humidity")
        if entity.state == "on" and isinstance(humidity, (int, float)):
            plans += (
                ServiceCallPlan(
                    "humidifier", "set_humidity", entity.entity_id,
                    {"humidity": humidity},
                ),
            )
        return plans

    if entity.domain in {"number", "input_number"}:
        try:
            value = float(entity.state)
        except (TypeError, ValueError):
            return ()
        return (
            ServiceCallPlan(
                entity.domain, "set_value", entity.entity_id, {"value": value}
            ),
        )

    if entity.domain == "select" and entity.state not in {
        "unknown", "unavailable", ""
    }:
        return (
            ServiceCallPlan(
                "select", "select_option", entity.entity_id,
                {"option": entity.state},
            ),
        )
    return ()


def build_undo_plan(
    executed: ServiceCallPlan,
    entities: list[EntitySnapshot] | tuple[EntitySnapshot, ...],
    *,
    requested_by_user_id: str | None = None,
) -> UndoPlan | None:
    """Snapshot exact pre-action states for all targets or refuse entirely."""
    target_ids = (
        {executed.entity_id}
        if isinstance(executed.entity_id, str)
        else set(executed.entity_id)
    )
    targets = [entity for entity in entities if entity.entity_id in target_ids]
    if len(targets) != len(target_ids):
        return None
    plans: list[ServiceCallPlan] = []
    for entity in targets:
        inverse = _entity_undo_plans(entity)
        if not inverse:
            return None
        plans.extend(inverse)
    return UndoPlan(tuple(plans), requested_by_user_id) if plans else None
