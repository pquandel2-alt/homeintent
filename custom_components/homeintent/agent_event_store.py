"""Atomic persistence for proactive AgentEvent instances."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file_atomic

from .agent_event import AgentEvent
from .storage_paths import storage_paths_for_hass

AGENT_EVENTS_FILENAME = "homeintent_agent_events.json"
LEGACY_AGENT_EVENTS_FILENAME = "ha_nlu_agent_events.json"


class AgentEventStore:
    """Small config-entry collaborator; no database or second rule store."""

    def __init__(self, hass: HomeAssistant, path: str | None = None) -> None:
        self._hass = hass
        self._path = path or storage_paths_for_hass(hass).agent_events
        self._lock = asyncio.Lock()

    async def async_load_all(self) -> dict[str, AgentEvent]:
        path = self._path
        raw = await self._hass.async_add_executor_job(self._read, path)
        events: dict[str, AgentEvent] = {}
        for event_id, value in raw.items():
            if not isinstance(value, dict):
                continue
            try:
                event = AgentEvent.from_dict(value)
            except (KeyError, TypeError, ValueError):
                continue
            if event.event_id == event_id:
                events[event_id] = event
        return events

    async def async_save(self, event: AgentEvent) -> None:
        path = self._path
        async with self._lock:
            raw = await self._hass.async_add_executor_job(self._read, path)
            raw[event.event_id] = event.to_dict()
            await self._hass.async_add_executor_job(self._write, path, raw)

    async def async_delete(self, event_id: str) -> None:
        path = self._path
        async with self._lock:
            raw = await self._hass.async_add_executor_job(self._read, path)
            if event_id not in raw:
                return
            del raw[event_id]
            await self._hass.async_add_executor_job(self._write, path, raw)

    @staticmethod
    def _read(path: str) -> dict[str, Any]:
        try:
            with open(path, encoding="utf-8") as handle:
                value = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _write(path: str, values: dict[str, Any]) -> None:
        write_utf8_file_atomic(
            path, json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True)
        )
