"""Home Assistant cover classes and HomeIntent's orthogonal safety sets."""

from __future__ import annotations

from importlib import import_module
from typing import Sequence

from .entities import EntitySnapshot
from .service_call import ServiceCallPlan

# CoverDeviceClass values in the supported Home Assistant line (2026.9).
# The integration keeps these values HA-free so its deterministic safety
# classifier remains usable in the lightweight test process. ``garage_door``
# is accepted only as a legacy registry value; current HA calls it ``garage``.
_SUPPORTED_HA_COVER_DEVICE_CLASSES = frozenset({
    "awning",
    "blind",
    "curtain",
    "damper",
    "door",
    "garage",
    "gate",
    "shade",
    "shutter",
    "window",
})


def _home_assistant_cover_classes() -> frozenset[str]:
    """Read the central HA enum when HA is installed.

    Lightweight deterministic tests intentionally run with a minimal HA
    stub, so the verified 2026.9 values remain the exact fallback there.
    """
    try:
        module = import_module("homeassistant.components.cover")
        device_class = getattr(module, "CoverDeviceClass")
        values = frozenset(str(item.value) for item in device_class)
    except (AttributeError, ImportError, TypeError):
        return _SUPPORTED_HA_COVER_DEVICE_CLASSES
    return values


HA_COVER_DEVICE_CLASSES = _home_assistant_cover_classes()
LEGACY_COVER_DEVICE_CLASSES = frozenset({"garage_door"})

INDEPENDENT_COVER_CLASSES = frozenset({"shutter", "blind", "shade", "curtain"})
PHYSICAL_ACCESS_COVER_CLASSES = frozenset({
    "garage", "garage_door", "gate", "door", "window",
})
CRITICAL_COVER_CLASSES = frozenset({"garage", "garage_door", "gate"})
COVER_MOVEMENT_SERVICES = frozenset({
    "open_cover", "close_cover", "set_cover_position",
    # Home Assistant's generic helpers and group/script expansion can expose
    # the same physical movement under these effective cover-domain names.
    "turn_on", "turn_off", "toggle",
})


def cover_movement_requires_confirmation(
    plan: ServiceCallPlan,
    entities: Sequence[EntitySnapshot],
) -> bool:
    """Whether physical movement is confirmation-mandatory by device class.

    This is deliberately independent of the configurable risk threshold.
    Only the established shading classes may use their ordinary threshold;
    missing, unknown and every other cover class fail closed.  A mixed set
    inherits the mandatory confirmation of its non-independent member.
    """
    if plan.service not in COVER_MOVEMENT_SERVICES:
        return False
    target_ids = (
        (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
    )
    if not target_ids:
        return plan.domain == "cover"
    by_id = {entity.entity_id: entity for entity in entities}
    cover_targets = [
        by_id.get(entity_id)
        for entity_id in target_ids
        if entity_id.startswith("cover.")
        or getattr(by_id.get(entity_id), "domain", None) == "cover"
    ]
    if plan.domain != "cover" and not cover_targets:
        return False
    for entity_id in target_ids:
        entity = by_id.get(entity_id)
        if entity is None:
            if plan.domain == "cover" or entity_id.startswith("cover."):
                return True
            continue
        if entity.domain != "cover":
            if plan.domain == "cover":
                return True
            continue
        device_class = (entity.device_class or "").casefold()
        if device_class not in INDEPENDENT_COVER_CLASSES:
            return True
    return False

__all__ = (
    "COVER_MOVEMENT_SERVICES",
    "cover_movement_requires_confirmation",
    "CRITICAL_COVER_CLASSES",
    "HA_COVER_DEVICE_CLASSES",
    "INDEPENDENT_COVER_CLASSES",
    "LEGACY_COVER_DEVICE_CLASSES",
    "PHYSICAL_ACCESS_COVER_CLASSES",
)
