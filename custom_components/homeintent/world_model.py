"""World Model: a single per-turn object bundling Entities, Devices, Areas,
Floors and their relationships (V6 architecture plan, World Model Wave,
2026-08-13 - see docs/architecture-v7.md).

HA stays the sole source of truth; ``WorldModel`` is a semantic view computed
fresh per conversation turn from already-fetched snapshots, never a persisted
second database (same principle ``EntitySnapshot``/``AreaSnapshot``/
``FloorSnapshot`` already follow). This module does not recompute anything
those snapshot modules already do: it reuses ``entities.build_entity_index()``,
``areas.area_snapshots()`` and ``floors.floor_snapshots()`` rather than
re-deriving "distinct areas/floors referenced by these entities" a third time.

``conversation.py`` builds this model once for every live turn and passes it
to the canonical language entry.  The same object is also the source for the
typed house graph; no second entity or location truth is constructed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import cached_property
from typing import Iterable, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .house_graph import HouseGraph, RelationSpec

from .areas import AreaSnapshot, area_snapshots
from .devices import DeviceSnapshot
from .entities import EntityIndex, EntitySnapshot, build_entity_index
from .floors import FloorSnapshot, floor_snapshots


@dataclass(frozen=True)
class WorldModel:
    """Bundled semantic view over one conversation turn's entities/devices/
    areas/floors, plus precomputed lookups and relationship accessors.

    The ``*_by_*`` fields are plain public ``Mapping``s (same style
    ``EntityIndex`` already uses) for callers that just want ``.get()``; the
    methods below are thin convenience wrappers over those same fields.
    """

    entities: tuple[EntitySnapshot, ...]
    devices: tuple[DeviceSnapshot, ...]
    areas: tuple[AreaSnapshot, ...]
    floors: tuple[FloorSnapshot, ...]
    entity_index: EntityIndex
    entities_by_id: Mapping[str, EntitySnapshot]
    devices_by_id: Mapping[str, DeviceSnapshot]
    devices_by_area_id: Mapping[str, tuple[DeviceSnapshot, ...]]
    device_id_by_entity_id: Mapping[str, str]
    entities_by_domain: Mapping[str, tuple[EntitySnapshot, ...]]
    entities_by_device_class: Mapping[str, tuple[EntitySnapshot, ...]]
    entities_by_area_id: Mapping[str, tuple[EntitySnapshot, ...]]
    entities_by_floor_id: Mapping[str, tuple[EntitySnapshot, ...]]
    entities_by_capability: Mapping[str, tuple[EntitySnapshot, ...]]
    configured_house_graph: "HouseGraph | None" = None

    def device_for_entity(self, entity_id: str) -> DeviceSnapshot | None:
        """The device owning ``entity_id``, or ``None`` if it has no device
        (or its device isn't part of this WorldModel's entity set)."""
        device_id = self.device_id_by_entity_id.get(entity_id)
        return self.devices_by_id.get(device_id) if device_id is not None else None

    def entities_for_device(self, device_id: str) -> tuple[EntitySnapshot, ...]:
        """Entities of ``device_id`` that are actually present in this
        WorldModel's ``entities`` - a device can own entities that aren't
        selected/exposed this turn, so ``device.entity_ids`` alone is never
        trusted blindly; only entities this WorldModel was actually handed
        are ever returned."""
        device = self.devices_by_id.get(device_id)
        if device is None:
            return ()
        return tuple(
            entity
            for entity_id in device.entity_ids
            if (entity := self.entities_by_id.get(entity_id)) is not None
        )

    def devices_in_area(self, area_id: str) -> tuple[DeviceSnapshot, ...]:
        """Devices whose own registry area is ``area_id``. A device with
        ``area_id=None`` never appears here, though it's still reachable via
        ``devices_by_id``."""
        return self.devices_by_area_id.get(area_id, ())

    def select_entities(
        self,
        *,
        domain: str | None = None,
        device_class: str | None = None,
        area_id: str | None = None,
        floor_id: str | None = None,
        capability: str | None = None,
    ) -> tuple[EntitySnapshot, ...]:
        """Select entities through the smallest available precomputed index.

        All supplied constraints are hard filters.  The method never widens
        an unknown area/floor/device class to the whole house.
        """
        pools: list[tuple[EntitySnapshot, ...]] = []
        if domain is not None:
            pools.append(self.entities_by_domain.get(domain, ()))
        if device_class is not None:
            pools.append(self.entities_by_device_class.get(device_class, ()))
        if area_id is not None:
            pools.append(self.entities_by_area_id.get(area_id, ()))
        if floor_id is not None:
            pools.append(self.entities_by_floor_id.get(floor_id, ()))
        if capability is not None:
            pools.append(self.entities_by_capability.get(capability, ()))
        candidates = min(pools, key=len) if pools else self.entities
        return tuple(
            entity for entity in candidates
            if (domain is None or entity.domain == domain)
            and (device_class is None or entity.device_class == device_class)
            and (area_id is None or entity.area_id == area_id)
            and (floor_id is None or entity.floor_id == floor_id)
            and (capability is None or capability in entity.capabilities)
        )

    def build_house_graph(
        self, configured_relations: Iterable["RelationSpec"] = ()
    ) -> "HouseGraph":
        """Project this exact turn snapshot into the shared typed graph."""
        from .house_graph import build_house_graph

        return build_house_graph(self, configured_relations)

    def with_house_graph(self, graph: "HouseGraph") -> "WorldModel":
        """Attach the configured graph to this same immutable turn snapshot."""
        return replace(self, configured_house_graph=graph)

    @cached_property
    def house_graph(self) -> "HouseGraph":
        """Indexed registry graph for this immutable per-turn snapshot.

        Relational clauses in one turn must not rebuild the same O(n)
        projection for every candidate. Explicit configured relations still
        go through ``build_house_graph`` because their input differs.
        """
        return self.configured_house_graph or self.build_house_graph()


def build_world_model(
    entities: list[EntitySnapshot],
    devices: list[DeviceSnapshot],
    *,
    index: EntityIndex | None = None,
) -> WorldModel:
    """Construct a ``WorldModel`` from already-fetched entity/device
    snapshots - hass-free, reuses ``build_entity_index()``/
    ``area_snapshots()``/``floor_snapshots()`` rather than reimplementing any
    of them (Regel 6: no parallel implementation of something that already
    exists)."""
    entities_by_id = {e.entity_id: e for e in entities}
    devices_by_id = {d.device_id: d for d in devices}

    devices_by_area_id: dict[str, list[DeviceSnapshot]] = {}
    for device in devices:
        if device.area_id is not None:
            devices_by_area_id.setdefault(device.area_id, []).append(device)

    device_id_by_entity_id: dict[str, str] = {
        entity_id: device.device_id for device in devices for entity_id in device.entity_ids
    }

    def group(attribute: str) -> dict[str, tuple[EntitySnapshot, ...]]:
        grouped: dict[str, list[EntitySnapshot]] = {}
        for entity in entities:
            value = getattr(entity, attribute)
            if value is not None:
                grouped.setdefault(value, []).append(entity)
        return {value: tuple(items) for value, items in grouped.items()}

    by_capability: dict[str, list[EntitySnapshot]] = {}
    for entity in entities:
        for capability in entity.capabilities:
            by_capability.setdefault(capability, []).append(entity)

    return WorldModel(
        entities=tuple(entities),
        devices=tuple(devices),
        areas=area_snapshots(entities),
        floors=floor_snapshots(entities),
        entity_index=index if index is not None else build_entity_index(entities),
        entities_by_id=entities_by_id,
        devices_by_id=devices_by_id,
        devices_by_area_id={area_id: tuple(ds) for area_id, ds in devices_by_area_id.items()},
        device_id_by_entity_id=device_id_by_entity_id,
        entities_by_domain=group("domain"),
        entities_by_device_class=group("device_class"),
        entities_by_area_id=group("area_id"),
        entities_by_floor_id=group("floor_id"),
        entities_by_capability={key: tuple(items) for key, items in by_capability.items()},
    )
