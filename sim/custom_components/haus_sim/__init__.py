"""Realistic house simulation used as a live HomeIntent test bed.

Services:
- ``haus_sim.set``: drive a sensor/binary_sensor/tracker (``entity_id``, ``value``)
- ``haus_sim.get_log``: returns received device calls, notifications, TTS
  output and played media (response only)
- ``haus_sim.clear_log``: reset those logs
- ``haus_sim.configure``: fault injection for one entity or a whole domain
  (``target`` = entity id or domain; ``report_delay`` seconds, ``reverse``
  for covers, ``unavailable``); ``haus_sim.reset`` clears it (7.9.2 A1)
- ``haus_sim.reset``: restore every device; with ``full: true`` also empty
  the to-do lists, cancel running Assist and helper timers and remove every
  automation that is not part of the versioned ``automations.yaml``, so
  measurement series in one Home Assistant stay independent (R11)
"""

from __future__ import annotations

from datetime import timedelta
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers.event import async_track_time_interval

from .entities import CLASSES, DOMAIN
from .house import AREAS, FLOORS, HOUSE

_LOGGER = logging.getLogger(__name__)
PLATFORMS = sorted(CLASSES)
LOG_KEYS = ("calls", "notifications", "spoken", "played_media", "announcements")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data.setdefault(DOMAIN, {})
    data["entities"] = {}
    data["faults"] = {}
    for key in LOG_KEYS:
        data[key] = []
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _wire_registries(hass)

    @callback
    def _tick(_now) -> None:
        for ent in list(data["entities"].values()):
            if hasattr(ent, "tick") and ent.hass is not None and ent.entity_id in hass.states.async_entity_ids():
                ent.tick()

    entry.async_on_unload(async_track_time_interval(hass, _tick, timedelta(seconds=10)))

    async def _set(call: ServiceCall) -> None:
        ent = data["entities"][call.data["entity_id"]]
        ent.set_value(call.data["value"])

    async def _get_log(call: ServiceCall):
        return {key: list(data[key]) for key in LOG_KEYS}

    async def _clear(call: ServiceCall) -> None:
        for key in LOG_KEYS:
            data[key].clear()

    hass.services.async_register(
        DOMAIN, "set", _set,
        schema=vol.Schema({vol.Required("entity_id"): str, vol.Required("value"): object}),
    )
    hass.services.async_register(DOMAIN, "get_log", _get_log, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "clear_log", _clear)

    async def _configure(call: ServiceCall) -> None:
        target = call.data["target"]
        fault = {key: call.data[key] for key in ("report_delay", "reverse", "unavailable") if key in call.data}
        data["faults"].setdefault(target, {}).update(fault)
        for ent in list(data["entities"].values()):
            if ent.entity_id == target or ent.entity_id.split(".", 1)[0] == target:
                ent.async_write_ha_state()

    hass.services.async_register(
        DOMAIN, "configure", _configure,
        schema=vol.Schema({
            vol.Required("target"): str,
            vol.Optional("report_delay"): vol.All(vol.Coerce(float), vol.Range(min=0, max=30)),
            vol.Optional("reverse"): bool,
            vol.Optional("unavailable"): bool,
        }),
    )

    async def _reset(call: ServiceCall) -> None:
        """Restore every simulated device to its initial state."""
        data["faults"].clear()
        for domain, key, name, _area, opts in HOUSE:
            ent = data["entities"].get(f"{domain}.{key}")
            if ent is None:
                continue
            task = getattr(ent, "_task", None)
            if task is not None and not task.done():
                task.cancel()
            type(ent).__init__(ent, hass, key, name, opts)
            ent.async_write_ha_state()
        # Mirrored sensors (room temperatures) would read "unknown" until the
        # next 10 s tick after re-initialisation; derive them right away.
        for ent in list(data["entities"].values()):
            if getattr(ent, "_opts", {}).get("mirror") and hasattr(ent, "tick"):
                ent.tick()
        for key in LOG_KEYS:
            data[key].clear()
        if call.data.get("full"):
            await _reset_household_state(hass)

    hass.services.async_register(
        DOMAIN, "reset", _reset, schema=vol.Schema({vol.Optional("full", default=False): bool})
    )
    return True


VERSIONED_AUTOMATION_IDS = frozenset({"1700000000001", "1700000000002"})


async def _reset_household_state(hass: HomeAssistant) -> None:
    """Lists, timers and test automations back to the bootstrap state."""
    try:
        from homeassistant.components.intent.timers import TIMER_DATA

        manager = hass.data.get(TIMER_DATA)
        if manager is not None:
            for timer_id in list(getattr(manager, "timers", {})):
                manager.cancel_timer(timer_id)
    except Exception:  # noqa: BLE001 - best effort for the test bed
        _LOGGER.warning("Could not cancel Assist timers", exc_info=True)
    helper_timers = [
        state.entity_id for state in hass.states.async_all("timer") if state.state != "idle"
    ]
    if helper_timers:
        await hass.services.async_call("timer", "cancel", {"entity_id": helper_timers}, blocking=True)
    for state in hass.states.async_all("todo"):
        response = await hass.services.async_call(
            "todo", "get_items", {"entity_id": state.entity_id},
            blocking=True, return_response=True,
        )
        items = (response or {}).get(state.entity_id, {}).get("items", [])
        uids = [item.get("uid") or item.get("summary") for item in items]
        if uids:
            await hass.services.async_call(
                "todo", "remove_item", {"entity_id": state.entity_id, "item": uids}, blocking=True
            )
    path = hass.config.path("automations.yaml")

    def _prune() -> bool:
        import yaml

        with open(path, encoding="utf-8") as handle:
            content = yaml.safe_load(handle) or []
        kept = [item for item in content if str(item.get("id")) in VERSIONED_AUTOMATION_IDS]
        if len(kept) == len(content):
            return False
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(kept, handle, allow_unicode=True, sort_keys=False)
        return True

    if await hass.async_add_executor_job(_prune):
        await hass.services.async_call("automation", "reload", {}, blocking=True)


def _wire_registries(hass: HomeAssistant) -> None:
    floors = fr.async_get(hass)
    areas = ar.async_get(hass)
    ents = er.async_get(hass)
    devs = dr.async_get(hass)
    floor_ids: dict[str, str] = {}
    for name, spec in FLOORS.items():
        floor = floors.async_get_floor_by_name(name) or floors.async_create(
            name, aliases=set(spec["aliases"]), level=spec["level"]
        )
        floor_ids[name] = floor.floor_id
    area_ids: dict[str, str] = {}
    for name, spec in AREAS.items():
        area = areas.async_get_area_by_name(name)
        if area is None:
            area = areas.async_create(name, floor_id=floor_ids[spec["floor"]], aliases=set(spec["aliases"]))
        elif area.floor_id != floor_ids[spec["floor"]] or not set(spec["aliases"]) <= set(area.aliases):
            area = areas.async_update(
                area.id,
                floor_id=floor_ids[spec["floor"]],
                aliases=set(area.aliases) | set(spec["aliases"]),
            )
        area_ids[name] = area.id
    for domain, key, _name, area, opts in HOUSE:
        entity_id = f"{domain}.{key}"
        entry = ents.async_get(entity_id)
        if entry is None:
            _LOGGER.warning("Sim entity %s missing from registry", entity_id)
            continue
        if area and entry.device_id:
            devs.async_update_device(entry.device_id, area_id=area_ids[area])
        aliases = opts.get("aliases")
        if aliases:
            ents.async_update_entity(entity_id, aliases=[er.COMPUTED_NAME, *aliases])


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
