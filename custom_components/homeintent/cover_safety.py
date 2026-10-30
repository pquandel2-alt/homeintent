"""Home Assistant cover classes and HomeIntent's orthogonal safety sets."""

from __future__ import annotations

from importlib import import_module

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
})

__all__ = (
    "COVER_MOVEMENT_SERVICES",
    "CRITICAL_COVER_CLASSES",
    "HA_COVER_DEVICE_CLASSES",
    "INDEPENDENT_COVER_CLASSES",
    "LEGACY_COVER_DEVICE_CLASSES",
    "PHYSICAL_ACCESS_COVER_CLASSES",
)
