"""Bridge from live Home Assistant state to the hass-free ``EntitySnapshot``
model the engine matches against.

Selection logic ported from xiaozhi_entity_mcp's ``default_exposed_entities()``/
``get_selected_entity_ids()`` (https://github.com/pquandel2-alt/xiaozhi_entity_mcp):
explicit per-entity selection in config entry options wins; otherwise fall
back to whatever is exposed to Assist (``async_should_expose``).
"""

from __future__ import annotations

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import (
    async_should_expose,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
)

from .const import CONF_CUSTOM_ALIASES, CONF_SELECTED_ENTITIES, SELECTABLE_DOMAINS
from .customization import parse_custom_aliases
from .devices import DeviceSnapshot
from .entities import EntitySnapshot
from .nlu.capabilities import derive_capabilities
from .world_model import WorldModel, build_world_model as _build_world_model


def default_exposed_entities(hass: HomeAssistant) -> list[str]:
    """Selectable entities currently exposed to the conversation assistant."""
    return [
        state.entity_id
        for state in hass.states.async_all()
        if state.entity_id.split(".", 1)[0] in SELECTABLE_DOMAINS
        and async_should_expose(hass, conversation.DOMAIN, state.entity_id)
    ]


def fixed_selection_missing_exposed(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Assist-exposed entities that a stored fixed selection leaves out.

    Empty when HomeIntent follows the dynamic Assist exposure (no stored
    selection). A non-empty result means newly exposed entities are
    invisible to HomeIntent until the selection is cleared or extended.
    """
    selected = entry.options.get(CONF_SELECTED_ENTITIES) or entry.data.get(CONF_SELECTED_ENTITIES)
    if not selected:
        return []
    chosen = set(selected)
    return sorted(
        entity_id for entity_id in default_exposed_entities(hass) if entity_id not in chosen
    )


def get_selected_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Return the selected entity_ids from options, falling back to Assist-exposed."""
    selected = entry.options.get(CONF_SELECTED_ENTITIES)
    if selected is None:
        selected = entry.data.get(CONF_SELECTED_ENTITIES)
    if selected:
        return list(selected)
    return default_exposed_entities(hass)


def _friendly(state: State) -> str:
    """Friendly name of a state, falling back to the entity_id."""
    return state.attributes.get("friendly_name") or state.entity_id


def _area_info(
    hass: HomeAssistant, entity_id: str, registry_entry: er.RegistryEntry | None = None
) -> tuple[str | None, str | None, tuple[str, ...]]:
    """(area_id, area_name, area_aliases) via the standard entity -> device ->
    area fallback chain, or (None, None, ()) if the entity has no area
    assigned. ``area_aliases`` mirrors ``_entity_aliases`` below but reads
    from the Area Registry's own ``aliases`` field (e.g. "draußen" configured
    on a "Garten" area) rather than the entity registry."""
    entry = registry_entry if registry_entry is not None else er.async_get(hass).async_get(entity_id)
    area_id = entry.area_id if entry else None
    if area_id is None and entry is not None and entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        area_id = device.area_id if device else None
    if area_id is None:
        return None, None, ()
    area = ar.async_get(hass).async_get_area(area_id)
    if area is None:
        return area_id, None, ()
    return area_id, area.name, tuple(sorted(area.aliases))


def _floor_info(
    hass: HomeAssistant, area_id: str | None
) -> tuple[str | None, str | None, int | None]:
    """(floor_id, floor_name, floor_level) via the area's assigned floor
    (Phase 28, "Hierarchische Orte"), or (None, None, None) if the entity
    has no area or the area has no floor assigned."""
    if area_id is None:
        return None, None, None
    area = ar.async_get(hass).async_get_area(area_id)
    floor_id = area.floor_id if area else None
    if floor_id is None:
        return None, None, None
    floor = fr.async_get(hass).async_get_floor(floor_id)
    return floor_id, (floor.name if floor else None), (floor.level if floor else None)


def _entity_aliases(
    hass: HomeAssistant, registry_entry: er.RegistryEntry | None
) -> tuple[str, ...]:
    """Aliases the user configured in HA's entity registry (Assist feature).

    ``registry_entry.aliases`` entries can be the ``ComputedNameType``
    sentinel (meaning "use the entity's computed full name here") rather
    than a plain string, so raw entries aren't safe to use directly -
    ``async_get_entity_aliases`` is HA's own resolver for that sentinel.
    """
    if registry_entry is None:
        return ()
    return tuple(sorted(er.async_get_entity_aliases(hass, registry_entry)))


def build_entity_snapshots(
    hass: HomeAssistant, entry: ConfigEntry
) -> list[EntitySnapshot]:
    """Snapshot the selected/exposed entities for a single match() call."""
    snapshots: list[EntitySnapshot] = []
    custom_aliases = parse_custom_aliases(entry.options.get(CONF_CUSTOM_ALIASES))
    for entity_id in get_selected_entity_ids(hass, entry):
        state = hass.states.get(entity_id)
        if state is None:
            continue
        registry_entry = er.async_get(hass).async_get(entity_id)
        area_id, area_name, area_aliases = _area_info(hass, entity_id, registry_entry=registry_entry)
        floor_id, floor_name, floor_level = _floor_info(hass, area_id)
        domain = entity_id.split(".", 1)[0]
        device_class = state.attributes.get("device_class")
        capabilities = derive_capabilities(domain, device_class, state.attributes)
        snapshots.append(
            EntitySnapshot(
                entity_id=entity_id,
                friendly_name=_friendly(state),
                domain=domain,
                state=state.state,
                area_id=area_id,
                area_name=area_name,
                floor_id=floor_id,
                floor_name=floor_name,
                floor_level=floor_level,
                unit=state.attributes.get("unit_of_measurement"),
                device_class=device_class,
                state_class=state.attributes.get("state_class"),
                aliases=tuple(dict.fromkeys((
                    *_entity_aliases(hass, registry_entry),
                    *custom_aliases.get(entity_id, ()),
                ))),
                area_aliases=area_aliases,
                attributes=state.attributes,
                capabilities=frozenset(c.name for c in capabilities),
                last_changed=getattr(state, "last_changed", None),
                last_updated=getattr(state, "last_updated", None),
            )
        )
    return snapshots


def build_device_snapshots(hass: HomeAssistant, entry: ConfigEntry) -> list[DeviceSnapshot]:
    """Devices that own at least one selected/exposed entity (World Model
    Wave, 2026-08-13). A device with zero selected entities is invisible
    here on purpose - same "only what's actually reachable this turn"
    scoping ``build_entity_snapshots()`` already applies.

    ``DeviceSnapshot.area_id`` is the device's own Area Registry area, which
    can legitimately differ from the effective area of an individual entity
    under it (per-entity area overrides exist - see ``_area_info()`` above);
    that's real HA behaviour, not something to reconcile here.
    """
    selected = set(get_selected_entity_ids(hass, entry))
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    area_registry = ar.async_get(hass)
    floor_registry = fr.async_get(hass)

    entity_ids_by_device: dict[str, list[str]] = {}
    for entity_id in selected:
        registry_entry = entity_registry.async_get(entity_id)
        if registry_entry is None or registry_entry.device_id is None:
            continue
        entity_ids_by_device.setdefault(registry_entry.device_id, []).append(entity_id)

    snapshots: list[DeviceSnapshot] = []
    for device_id, entity_ids in entity_ids_by_device.items():
        device = device_registry.async_get(device_id)
        if device is None:
            continue
        area_id = device.area_id
        area_name: str | None = None
        floor_id: str | None = None
        floor_name: str | None = None
        floor_level: int | None = None
        if area_id is not None:
            area = area_registry.async_get_area(area_id)
            if area is not None:
                area_name = area.name
                floor_id = area.floor_id
                if floor_id is not None:
                    floor = floor_registry.async_get_floor(floor_id)
                    if floor is not None:
                        floor_name = floor.name
                        floor_level = floor.level
        snapshots.append(
            DeviceSnapshot(
                device_id=device_id,
                name=device.name_by_user or device.name or device_id,
                manufacturer=device.manufacturer,
                model=device.model,
                area_id=area_id,
                area_name=area_name,
                floor_id=floor_id,
                floor_name=floor_name,
                floor_level=floor_level,
                entity_ids=tuple(sorted(entity_ids)),
            )
        )
    return snapshots


def build_world_model(hass: HomeAssistant, entry: ConfigEntry) -> WorldModel:
    """Single per-turn World Model constructor (World Model Wave, 2026-08-13):
    bundles ``build_entity_snapshots()`` + ``build_device_snapshots()`` via
    ``world_model.build_world_model()``.

    Not called from ``conversation.py`` yet - see docs/architecture-v7.md's
    World Model Wave section for why (mirrors ``nlu/reasoning.py``'s
    ``ReasoningEngine`` sitting built-and-tested before being wired into the
    live pipeline, to avoid a second parallel entity list flowing through
    the engine next to the one ``build_entity_snapshots()`` already
    provides).
    """
    entities = build_entity_snapshots(hass, entry)
    devices = build_device_snapshots(hass, entry)
    return _build_world_model(entities, devices)
