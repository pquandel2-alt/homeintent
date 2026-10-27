"""Capability-grounded media transport semantics.

The integer values are Home Assistant's stable ``MediaPlayerEntityFeature``
wire values.  This module stays Home-Assistant-free so the resolver remains
deterministic and can be exercised without importing an integration.
"""

from __future__ import annotations

from enum import StrEnum

from .entities import EntitySnapshot


class MediaCapability(StrEnum):
    PAUSE = "PAUSE"
    VOLUME_SET = "VOLUME_SET"
    VOLUME_MUTE = "VOLUME_MUTE"
    PREVIOUS_TRACK = "PREVIOUS_TRACK"
    NEXT_TRACK = "NEXT_TRACK"
    TURN_ON = "TURN_ON"
    TURN_OFF = "TURN_OFF"
    VOLUME_STEP = "VOLUME_STEP"
    SELECT_SOURCE = "SELECT_SOURCE"
    STOP = "STOP"
    PLAY = "PLAY"


_FEATURE_BITS = {
    MediaCapability.PAUSE: 1,
    MediaCapability.VOLUME_SET: 4,
    MediaCapability.VOLUME_MUTE: 8,
    MediaCapability.PREVIOUS_TRACK: 16,
    MediaCapability.NEXT_TRACK: 32,
    MediaCapability.TURN_ON: 128,
    MediaCapability.TURN_OFF: 256,
    MediaCapability.VOLUME_STEP: 1024,
    MediaCapability.SELECT_SOURCE: 2048,
    MediaCapability.STOP: 4096,
    MediaCapability.PLAY: 16384,
}


def media_capabilities(entity: EntitySnapshot) -> frozenset[MediaCapability]:
    """Return only capabilities evidenced by the entity's current snapshot."""
    try:
        features = int(entity.attributes.get("supported_features", 0))
    except (TypeError, ValueError):
        features = 0
    named = set(entity.capabilities)
    return frozenset(
        capability
        for capability, bit in _FEATURE_BITS.items()
        if features & bit or capability.value in named
    )


def supports_media(entity: EntitySnapshot, capability: MediaCapability) -> bool:
    return capability in media_capabilities(entity)


__all__ = ("MediaCapability", "media_capabilities", "supports_media")
