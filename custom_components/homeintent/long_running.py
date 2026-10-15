"""Conservative activity semantics for long-running-state observations.

An entity being configured for an operating mode is not proof that it is
currently doing work.  Climate therefore uses ``hvac_action`` and not its
state (the HVAC mode).  Water heaters are intentionally unsupported: Home
Assistant's standard state/current-operation values describe the selected
mode, not a reliable active-heating signal.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


LONG_RUNNING_DOMAINS = frozenset(
    {
        "climate",
        "cover",
        "fan",
        "humidifier",
        "light",
        "media_player",
        "switch",
        "vacuum",
    }
)

_CLIMATE_ACTIONS = frozenset(
    {"heating", "cooling", "drying", "fan", "preheating", "defrosting"}
)
_STATE_ACTIVITY: Mapping[str, frozenset[str]] = {
    "cover": frozenset({"opening", "closing"}),
    "fan": frozenset({"on"}),
    "humidifier": frozenset({"on"}),
    "light": frozenset({"on"}),
    "media_player": frozenset({"playing"}),
    "switch": frozenset({"on"}),
    "vacuum": frozenset({"cleaning", "returning"}),
}
_UNRELIABLE_STATES = frozenset({"", "unknown", "unavailable"})


def activity_marker(entity: Any) -> str | None:
    """Return the concrete active operation, or ``None`` when not proven."""
    entity_id = getattr(entity, "entity_id", None)
    raw_state = getattr(entity, "state", None)
    if not isinstance(entity_id, str) or not isinstance(raw_state, str):
        return None
    state = raw_state.casefold()
    if state in _UNRELIABLE_STATES:
        return None
    domain = getattr(entity, "domain", None)
    if not isinstance(domain, str):
        domain = entity_id.partition(".")[0]
    if domain == "climate":
        attributes = getattr(entity, "attributes", None)
        if not isinstance(attributes, Mapping):
            return None
        action = attributes.get("hvac_action")
        if not isinstance(action, str):
            return None
        normalized = action.casefold()
        return f"hvac_action:{normalized}" if normalized in _CLIMATE_ACTIONS else None
    states = _STATE_ACTIVITY.get(domain)
    return f"state:{state}" if states is not None and state in states else None


def has_reliable_last_changed(entity: Any) -> bool:
    """Whether HA's ``last_changed`` timestamps this activity interval.

    Attribute-driven climate actions do not change ``State.state`` and hence
    do not reliably update ``last_changed``.  All other supported predicates
    above are state based.
    """
    entity_id = getattr(entity, "entity_id", None)
    return isinstance(entity_id, str) and not entity_id.startswith("climate.")


__all__ = ("LONG_RUNNING_DOMAINS", "activity_marker", "has_reliable_last_changed")
