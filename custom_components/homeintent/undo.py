"""Bounded inverse plans for recently executed, safely reversible actions."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
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
    # Context identities of the writes whose observed effects made this undo
    # available.  They are evidence/trace data, never authorization.
    source_execution_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompletedUndoStep:
    """One inverse whose exact, causally linked effect was observed."""

    plan: ServiceCallPlan
    execution_id: str
    observed_states: tuple[tuple[str, tuple[object, ...]], ...]


@dataclass(frozen=True)
class UndoRemainder:
    """Immutable execution state for one consumed undo request.

    ``original_observed_states`` is never changed.  ``observed_states`` may
    advance only for a target that has just been affected by a causally proven
    inverse and still has another inverse step.  Thus an unrelated target can
    never acquire fresh evidence merely because an earlier step succeeded.
    """

    remaining_plans: tuple[ServiceCallPlan, ...]
    original_observed_states: tuple[tuple[str, tuple[object, ...]], ...]
    observed_states: tuple[tuple[str, tuple[object, ...]], ...]
    completed_steps: tuple[CompletedUndoStep, ...] = ()
    aborted_plans: tuple[ServiceCallPlan, ...] = ()
    skipped_plans: tuple[ServiceCallPlan, ...] = ()
    requested_by_user_id: str | None = None
    source_execution_ids: tuple[str, ...] = ()
    undo_execution_ids: tuple[str, ...] = ()

    @classmethod
    def from_undo(cls, undo: UndoPlan) -> "UndoRemainder":
        """Consume a fully evidenced stored plan into an execution remainder."""

        return cls(
            remaining_plans=undo.plans,
            original_observed_states=undo.observed_states,
            observed_states=undo.observed_states,
            requested_by_user_id=undo.requested_by_user_id,
            source_execution_ids=undo.source_execution_ids,
        )


def _freeze_state_value(value: object) -> object:
    """Recursively freeze HA state attributes without losing container type."""

    if isinstance(value, Mapping):
        return (
            "mapping",
            tuple(
                sorted(
                    (
                        (_freeze_state_value(key), _freeze_state_value(item))
                        for key, item in value.items()
                    ),
                    key=repr,
                )
            ),
        )
    if isinstance(value, list):
        return ("list", tuple(_freeze_state_value(item) for item in value))
    if isinstance(value, tuple):
        return ("tuple", tuple(_freeze_state_value(item) for item in value))
    if isinstance(value, set):
        return (
            "set",
            tuple(sorted((_freeze_state_value(item) for item in value), key=repr)),
        )
    if isinstance(value, frozenset):
        return (
            "frozenset",
            tuple(sorted((_freeze_state_value(item) for item in value), key=repr)),
        )
    return deepcopy(value)


def _state_signature(state: Any) -> tuple[object, ...] | None:
    if state is None:
        return None
    return (
        state.state, _freeze_state_value(dict(state.attributes)),
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
    execution_id: str | None = None,
) -> UndoPlan | None:
    """Bind a safely observed partial inverse; missing evidence offers no Undo."""
    if undo is None or not undo.plans:
        return None
    target_ids = _target_ids(undo)
    if executed is not None and not _observed_effect_matches(
        executed, state_lookup, target_ids, undo
    ):
        return None
    if execution_id is not None and not all(
        _state_belongs_to_execution(state_lookup(entity_id), execution_id)
        for entity_id in target_ids
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
    source_execution_ids = undo.source_execution_ids
    if execution_id is not None and execution_id not in source_execution_ids:
        source_execution_ids += (execution_id,)
    return replace(
        undo,
        observed_states=tuple(observed),
        source_execution_ids=source_execution_ids,
    )


def _state_belongs_to_execution(state: Any, execution_id: str) -> bool:
    """Whether HA causally linked this state report to the original write."""
    # Home-Assistant-free service doubles have no Context at all.  They can
    # still prove state/attributes/time; a real HA State always has Context.
    if state is not None and not hasattr(state, "context"):
        return True
    context = getattr(state, "context", None)
    return context is not None and execution_id in {
        getattr(context, "id", None),
        getattr(context, "parent_id", None),
    }


async def async_bind_undo_when_observed(
    hass: Any,
    undo: UndoPlan,
    executed: ServiceCallPlan,
    execution_id: str,
    *,
    timeout: float,
) -> UndoPlan | None:
    """Wait in the background for exact, causally bound post-state evidence.

    A moving cover is a confirmed *movement* for the spoken response, but it
    is not yet the requested set point that may safely anchor a later inverse.
    This observer therefore waits for the exact typed effect without delaying
    the response.  A foreign state event aborts the observation fail-closed.
    """
    target_ids = _target_ids(undo)
    if not target_ids or timeout <= 0:
        return None

    def bind_if_proven() -> UndoPlan | None:
        if not all(
            _state_belongs_to_execution(hass.states.get(entity_id), execution_id)
            for entity_id in target_ids
        ):
            return None
        return bind_undo_to_observed_states(
            undo,
            hass.states.get,
            executed=executed,
            execution_id=execution_id,
        )

    if (bound := bind_if_proven()) is not None:
        return bound
    if any(
        not _state_belongs_to_execution(
            hass.states.get(entity_id), execution_id
        )
        for entity_id in target_ids
    ):
        return None

    changed = asyncio.Event()
    foreign_change = False

    def on_change(event: Any) -> None:
        nonlocal foreign_change
        data = getattr(event, "data", None) or {}
        entity_id = data.get("entity_id")
        if entity_id not in target_ids:
            return
        new_state = data.get("new_state")
        if not _state_belongs_to_execution(new_state, execution_id):
            foreign_change = True
        changed.set()

    bus = getattr(hass, "bus", None)
    if bus is None:
        return None
    unsubscribe = bus.async_listen("state_changed", on_change)
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while not foreign_change:
            changed.clear()
            if (bound := bind_if_proven()) is not None:
                return bound
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(changed.wait(), remaining)
            except asyncio.TimeoutError:
                return None
        return None
    finally:
        unsubscribe()


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


def next_undo_guard(remainder: UndoRemainder) -> UndoPlan | None:
    """Return the exact evidence guard for only the next inverse step."""

    if not remainder.remaining_plans:
        return None
    plan = remainder.remaining_plans[0]
    target_ids = _plan_target_ids(plan)
    observed = tuple(
        item for item in remainder.observed_states if item[0] in target_ids
    )
    guard = UndoPlan(
        (plan,),
        remainder.requested_by_user_id,
        observed,
        remainder.source_execution_ids,
    )
    return guard if frozenset(item[0] for item in observed) == target_ids else None


def advance_undo_remainder(
    remainder: UndoRemainder,
    plan: ServiceCallPlan,
    observed: UndoPlan,
    execution_id: str,
) -> UndoRemainder:
    """Remove one proven step and mutate evidence only for its own targets."""

    if not remainder.remaining_plans or remainder.remaining_plans[0] != plan:
        raise ValueError("undo step is not the next remaining plan")
    target_ids = _plan_target_ids(plan)
    observed_by_id = dict(observed.observed_states)
    if frozenset(observed_by_id) != target_ids:
        raise ValueError("completed undo step lacks exact target evidence")

    remaining = remainder.remaining_plans[1:]
    remaining_target_ids = frozenset(
        entity_id
        for pending in remaining
        for entity_id in _plan_target_ids(pending)
    )
    evidence_by_id = dict(remainder.observed_states)
    for entity_id in target_ids:
        if entity_id in remaining_target_ids:
            evidence_by_id[entity_id] = observed_by_id[entity_id]
        else:
            evidence_by_id.pop(entity_id, None)
    ordered_evidence = tuple(sorted(evidence_by_id.items()))
    return replace(
        remainder,
        remaining_plans=remaining,
        observed_states=ordered_evidence,
        completed_steps=remainder.completed_steps
        + (CompletedUndoStep(plan, execution_id, observed.observed_states),),
        undo_execution_ids=remainder.undo_execution_ids + (execution_id,),
    )


def stop_undo_remainder(
    remainder: UndoRemainder,
    *,
    aborted_plan: ServiceCallPlan | None,
) -> UndoRemainder:
    """Consume every remaining step while retaining an honest typed outcome."""

    aborted_index = next(
        (
            index
            for index, plan in enumerate(remainder.remaining_plans)
            if plan is aborted_plan
        ),
        None,
    )
    if aborted_index is None and aborted_plan is not None:
        aborted_index = next(
            (
                index
                for index, plan in enumerate(remainder.remaining_plans)
                if plan == aborted_plan
            ),
            None,
        )
    aborted = (
        (remainder.remaining_plans[aborted_index],)
        if aborted_index is not None
        else ()
    )
    skipped = (
        remainder.remaining_plans[:aborted_index]
        + remainder.remaining_plans[aborted_index + 1:]
        if aborted_index is not None
        else remainder.remaining_plans
    )
    return replace(
        remainder,
        remaining_plans=(),
        aborted_plans=remainder.aborted_plans + aborted,
        skipped_plans=remainder.skipped_plans + skipped,
    )


def _plan_target_ids(plan: ServiceCallPlan) -> frozenset[str]:
    return frozenset(
        (plan.entity_id,) if isinstance(plan.entity_id, str) else plan.entity_id
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
