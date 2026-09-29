"""Registry-structure cache for the entity index and the house graph (7.8 B8).

Building the entity index (alias generation) and the house graph took about
80 % of a whole turn at 5000 entities, although both depend only on the
*structure* of the house: entity, device, area and floor registry,
exposure, names, aliases and learned bindings (which reach the snapshots as
aliases), and the configured relations.

``structure_key`` fingerprints exactly these fields. Any change of one of
them yields a new key and a fresh build - so a device withdrawn from
HomeIntent is gone from the very next turn. State values are never cached:
the index stores entity ids and resolves them against the current turn's
snapshots on access, and the graph's entity nodes are refreshed with the
current state before use.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .entities import EntityIndex, EntitySnapshot, build_entity_index

if TYPE_CHECKING:
    from .devices import DeviceSnapshot
    from .house_graph import HouseGraph, RelationSpec
    from .world_model import WorldModel

__all__ = ("StructureCache", "structure_key")


def _entity_structure(entity: EntitySnapshot) -> tuple[object, ...]:
    return (
        entity.entity_id, entity.friendly_name, entity.domain, entity.area_id, entity.area_name,
        entity.floor_id, entity.floor_name, entity.floor_level, tuple(entity.aliases),
        tuple(entity.area_aliases), tuple(entity.floor_aliases), entity.device_class,
        entity.unit, entity.capabilities,
    )


def structure_key(
    entities: Sequence[EntitySnapshot],
    devices: Sequence[DeviceSnapshot] = (),
    relations: Sequence[RelationSpec] = (),
) -> int:
    """Fingerprint of everything the index and the graph are built from -
    and of nothing that changes with a state."""
    return hash((
        tuple(_entity_structure(entity) for entity in entities),
        tuple(
            (device.device_id, device.name, device.area_id, tuple(device.entity_ids))
            for device in devices
        ),
        tuple(relations),
    ))


class _LiveMapping(Mapping[str, tuple[EntitySnapshot, ...]]):
    """Cached id groups resolved against the current turn's snapshots."""

    def __init__(self, ids: Mapping[str, tuple[str, ...]], current: Mapping[str, EntitySnapshot]) -> None:
        self._ids = ids
        self._current = current

    def __getitem__(self, key: str) -> tuple[EntitySnapshot, ...]:
        return tuple(self._current[entity_id] for entity_id in self._ids[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._ids)

    def __len__(self) -> int:
        return len(self._ids)

    def __contains__(self, key: object) -> bool:
        return key in self._ids


@dataclass(frozen=True)
class _IndexIds:
    by_domain: Mapping[str, tuple[str, ...]]
    by_area: Mapping[str, tuple[str, ...]]
    by_normalized_name: Mapping[str, tuple[str, ...]]
    by_normalized_alias: Mapping[str, tuple[str, ...]]
    by_normalized_name_token: Mapping[str, tuple[str, ...]]

    @classmethod
    def of(cls, index: EntityIndex) -> "_IndexIds":
        def ids(mapping: Mapping[str, tuple[EntitySnapshot, ...]]) -> dict[str, tuple[str, ...]]:
            return {key: tuple(entity.entity_id for entity in value) for key, value in mapping.items()}

        return cls(
            ids(index.by_domain), ids(index.by_area), ids(index.by_normalized_name),
            ids(index.by_normalized_alias), ids(index.by_normalized_name_token),
        )

    def live(self, current: Mapping[str, EntitySnapshot]) -> EntityIndex:
        return EntityIndex(
            by_domain=_LiveMapping(self.by_domain, current),
            by_area=_LiveMapping(self.by_area, current),
            by_normalized_name=_LiveMapping(self.by_normalized_name, current),
            by_normalized_alias=_LiveMapping(self.by_normalized_alias, current),
            by_normalized_name_token=_LiveMapping(self.by_normalized_name_token, current),
        )


class StructureCache:
    """One cached structure per key; rebuilt whenever the key changes."""

    def __init__(self) -> None:
        self._index_key: int | None = None
        self._index: _IndexIds | None = None
        self._graph_key: int | None = None
        self._graph: HouseGraph | None = None
        self._world_key: int | None = None
        self._world: tuple | None = None
        self.builds = 0  # for tests and diagnostics

    def entity_index(
        self, entities: Sequence[EntitySnapshot], key: int
    ) -> EntityIndex:
        current = {entity.entity_id: entity for entity in entities}
        if self._index is None or key != self._index_key:
            self._index = _IndexIds.of(build_entity_index(list(entities)))
            self._index_key = key
            self.builds += 1
        return self._index.live(current)

    def world_model(
        self,
        entities: Sequence[EntitySnapshot],
        devices: Sequence[DeviceSnapshot],
        key: int,
    ) -> WorldModel:
        """The world model of this turn: cached groupings, live snapshots."""
        from .world_model import WorldModel, build_world_model

        index = self.entity_index(entities, key)
        current = {entity.entity_id: entity for entity in entities}
        if self._world is None or key != self._world_key:
            built = build_world_model(list(entities), list(devices), index=index)

            def ids(mapping: Mapping[str, tuple[EntitySnapshot, ...]]) -> dict[str, tuple[str, ...]]:
                return {k: tuple(entity.entity_id for entity in v) for k, v in mapping.items()}

            self._world = (
                built.areas, built.floors,
                ids(built.entities_by_domain), ids(built.entities_by_device_class),
                ids(built.entities_by_area_id), ids(built.entities_by_floor_id),
                ids(built.entities_by_capability),
            )
            self._world_key = key
        areas, floors, by_domain, by_class, by_area, by_floor, by_capability = self._world
        devices_by_area: dict[str, list[DeviceSnapshot]] = {}
        for device in devices:
            if device.area_id is not None:
                devices_by_area.setdefault(device.area_id, []).append(device)
        return WorldModel(
            entities=tuple(entities),
            devices=tuple(devices),
            areas=areas,
            floors=floors,
            entity_index=index,
            entities_by_id=current,
            devices_by_id={device.device_id: device for device in devices},
            devices_by_area_id={area: tuple(items) for area, items in devices_by_area.items()},
            device_id_by_entity_id={
                entity_id: device.device_id for device in devices for entity_id in device.entity_ids
            },
            entities_by_domain=_LiveMapping(by_domain, current),
            entities_by_device_class=_LiveMapping(by_class, current),
            entities_by_area_id=_LiveMapping(by_area, current),
            entities_by_floor_id=_LiveMapping(by_floor, current),
            entities_by_capability=_LiveMapping(by_capability, current),
        )

    def house_graph(
        self,
        key: int,
        build: Callable[[], HouseGraph],
        entities: Sequence[EntitySnapshot],
    ) -> HouseGraph:
        if self._graph is None or key != self._graph_key:
            self._graph = build()
            self._graph_key = key
            self.builds += 1
            return self._graph
        refresh_entity_states(self._graph, entities)
        return self._graph


def refresh_entity_states(graph: HouseGraph, entities: Sequence[EntitySnapshot]) -> None:
    """Write the current state into the cached graph's entity nodes."""
    for entity in entities:
        node_id = f"entity:{entity.entity_id}"
        node = graph.node(node_id)
        if node is None or node.attributes.get("state") == entity.state:
            continue
        attributes = dict(node.attributes)
        attributes["state"] = entity.state
        attributes["quality"] = (
            "available" if entity.state not in {"unknown", "unavailable"} else entity.state
        )
        graph.replace_node(replace(node, attributes=attributes))


# A process-wide cache: HomeIntent runs one conversation entity per entry;
# the key includes everything that differs between entries.
SHARED = StructureCache()
