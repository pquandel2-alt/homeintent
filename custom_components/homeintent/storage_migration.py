"""Lossless local storage-key migration for the HomeIntent rename."""

from __future__ import annotations

import logging
import os
from typing import Protocol

_LOGGER = logging.getLogger(__name__)


class _ConfigPath(Protocol):
    def path(self, *parts: str) -> str: ...


class HassWithConfigPath(Protocol):
    config: _ConfigPath


def resolve_storage_path(
    hass: HassWithConfigPath, current: str, legacy: str
) -> str:
    """Return the canonical path and atomically adopt a legacy file once.

    A failed rename keeps using the legacy file, so an existing installation
    never starts with an empty store merely because filesystem migration was
    unavailable.
    """
    current_path = hass.config.path(*current.split("/"))
    legacy_path = hass.config.path(*legacy.split("/"))
    if os.path.exists(current_path) or not os.path.exists(legacy_path):
        return current_path
    try:
        os.makedirs(os.path.dirname(current_path) or ".", exist_ok=True)
        os.replace(legacy_path, current_path)
    except OSError:
        _LOGGER.warning(
            "HomeIntent could not migrate legacy storage %s; continuing with it",
            legacy,
            exc_info=True,
        )
        return legacy_path
    _LOGGER.info("Migrated legacy HomeIntent storage %s to %s", legacy, current)
    return current_path


__all__ = ("HassWithConfigPath", "resolve_storage_path")
