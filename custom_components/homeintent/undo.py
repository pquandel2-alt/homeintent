"""Bounded inverse plans for recently executed, safely reversible actions."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from copy import deepcopy
from typing import Any, Callable

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
    # Exact post-execution observations; a later update invalidates the inverse.
    observed_states: tuple[tuple[str, tuple[object, ...]], ...] = ()


def _state_signature(state: Any) -> tuple[object, ...] | None:
    if state is None:
        return None
    return (
        state.state, deepcopy(dict(state.attributes)),
        getattr(state, "last_updated", None),
        getattr(getattr(state, "context", None), "id", None),
    )


def _target_ids(undo: UndoPlan) -> frozenset[str]:
    return frozenset(
        entity_id
        for plan in undo.plans
        for entity_id in (
            (plan.entity_id,) if isinstance(plan.entity_id, str) else plan.entity_id
        )
    )


def _observed_effect_matches(
    executed: ServiceCallPlan,
    state_lookup: Callable[[str], Any],
    evidenced_target_ids: frozenset[str],
    undo: UndoPlan,
) -> bool:
    """Require the requested post-state before an inverse becomes available."""

    executed_target_ids = (
        (executed.entity_id,)
        if isinstance(executed.entity_id, str)
        else tuple(executed.entity_id)
    )
    target_ids = tuple(
        entity_id
        for entity_id in executed_target_ids
        if entity_id in evidenced_target_ids
    )
    if not target_ids or frozenset(target_ids) != evidenced_target_ids:
        return False
    for entity_id in target_ids:
        state = state_lookup(entity_id)
        if state is None:
            return False
        value = getattr(state, "state", None)
        attributes = dict(getattr(state, "attributes", {}) or {})
        service = executed.service
        if service == "turn_on" and value != "on":
            return False
        if service == "turn_off" and value != "off":
            return False
        if service == "open_cover" and value != "open":
            return False
        if service == "close_cover" and value != "closed":
            return False
        if service == "toggle":
            inverse = next(
                (
                    plan
                    for plan in undo.plans
                    if plan.entity_id == entity_id
                    and plan.service in {"turn_on", "turn_off"}
                ),
                None,
            )
            expected = (
                "off"
                if inverse is not None and inverse.service == "turn_on"
                else "on"
                if inverse is not None and inverse.service == "turn_off"
                else None
            )
            if expected is None or value != expected:
                return False
        expected_attribute = {
            "set_cover_position": ("current_position", "position"),
            "set_temperature": ("temperature", "temperature"),
            "set_humidity": ("humidity", "humidity"),
            "set_value": (None, "value"),
            "select_option": (None, "option"),
            "set_hvac_mode": (None, "hvac_mode"),
        }.get(service)
        if expected_attribute is not None:
            attribute, data_key = expected_attribute
            expected = executed.data.get(data_key)
            observed = value if attribute is None else attributes.get(attribute)
            if service == "set_value":
                matches = False
                if isinstance(observed, (str, int, float)) and isinstance(
                    expected, (str, int, float)
                ):
                    try:
                        matches = float(observed) == float(expected)
                    except ValueError:
                        pass
            else:
                matches = observed == expected
            if expected is None or not matches:
                return False
        elif service not in {
            "turn_on", "turn_off", "open_cover", "close_cover", "toggle"
        }:
            # Undo is intentionally unavailable for an effect whose resulting
            # state this closed adapter cannot prove.
            return False
    return True


def bind_undo_to_observed_states(
    undo: UndoPlan | None,
    state_lookup: Callable[[str], Any],
    *,
    executed: ServiceCallPlan | None = None,
) -> UndoPlan | None:
    """Bind a safely observed partial inverse; missing evidence offers no Undo."""
    if undo is None or not undo.plans:
        return None
    target_ids = _target_ids(undo)
    if executed is not None and not _observed_effect_matches(
        executed, state_lookup, target_ids, undo
    ):
        return None
    if not target_ids:
        return None
    observed = []
    for entity_id in sorted(target_ids):
        signature = _state_signature(state_lookup(entity_id))
        if signature is None:
            return None
        observed.append((entity_id, signature))
    return replace(undo, observed_states=tuple(observed))


def undo_matches_observed_states(undo: UndoPlan, state_lookup: Callable[[str], Any]) -> bool:
    """Do not overwrite a later device change, including same-value updates."""
    target_ids = _target_ids(undo)
    observed_ids = tuple(entity_id for entity_id, _signature in undo.observed_states)
    if (
        not target_ids
        or len(observed_ids) != len(target_ids)
        or frozenset(observed_ids) != target_ids
    ):
        return False
    return all(
        _state_signature(state_lookup(entity_id)) == signature
        for entity_id, signature in undo.observed_states
    )


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


def restrict_undo_plan(
    undo: UndoPlan | None, entity_ids: frozenset[str]
) -> UndoPlan | None:
    """Keep inverse calls only for targets confirmed as successfully changed."""
    if undo is None or not entity_ids:
        return None
    plans = tuple(
        plan
        for plan in undo.plans
        if (
            (plan.entity_id in entity_ids)
            if isinstance(plan.entity_id, str)
            else bool(plan.entity_id) and set(plan.entity_id) <= entity_ids
        )
    )
    return replace(undo, plans=plans, observed_states=tuple(
        item for item in undo.observed_states if item[0] in entity_ids
    )) if plans else None
