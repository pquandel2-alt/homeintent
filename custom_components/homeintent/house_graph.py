"""Typed, local house graph shared by reasoning, memory and planning.

The graph is deliberately descriptive: it can resolve relationships and
explain evidence, but it cannot create or execute a Home Assistant service
call.  Stable HA ids are retained internally; diagnostics use redacted ids.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, StrEnum, auto
from typing import Iterable, Mapping

from .world_model import WorldModel


class NodeKind(StrEnum):
    PERSON = "person"
    FLOOR = "floor"
    AREA = "area"
    DEVICE = "device"
    ENTITY = "entity"
    GROUP = "group"
    MODE = "mode"
    PROPERTY = "property"
    CAPABILITY = "capability"


class RelationKind(StrEnum):
    ADJACENT_TO = "adjacent_to"
    ABOVE = "above"
    BELOW = "below"
    BELONGS_TO = "belongs_to"
    OBSERVED_BY = "observed_by"
    INFLUENCES = "influences"
    PREFERRED_DEVICE = "preferred_device"
    PREFERRED_MEASUREMENT = "preferred_measurement"
    VOICE_ORIGIN = "voice_origin"
    LOCATED_IN = "located_in"
    CONTAINS = "contains"
    ON_FLOOR = "on_floor"
    MEASURES = "measures"
    HAS_CAPABILITY = "has_capability"
    CONTROLS = "controls"
    RELATED_TO = "related_to"
    SAME_DEVICE = "same_device"


class FactProvenance(StrEnum):
    OBSERVED = "observed"
    CONFIGURED = "configured"
    CONFIRMED_MEMORY = "confirmed_memory"
    DERIVED = "derived"
    STATISTICAL = "statistical"


class ConfidenceClass(StrEnum):
    CERTAIN = "certain"
    CONFIRMED = "confirmed"
    DERIVED = "derived"
    ESTIMATE = "estimate"


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    kind: NodeKind
    label: str
    attributes: Mapping[str, object] = field(default_factory=lambda: _empty_attributes())


@dataclass(frozen=True)
class GraphRelation:
    relation_id: str
    source_id: str
    kind: RelationKind
    target_id: str
    provenance: FactProvenance
    confidence: ConfidenceClass
    observed_at: datetime | None = None

    @property
    def is_asserted_fact(self) -> bool:
        """Statistical evidence is never promoted to an asserted fact."""
        return self.provenance is not FactProvenance.STATISTICAL


@dataclass(frozen=True)
class RelationSpec:
    source_id: str
    kind: RelationKind
    target_id: str


class TraversalDirection(Enum):
    OUTGOING = auto()
    INCOMING = auto()


@dataclass(frozen=True)
class TraversalStep:
    kind: RelationKind
    direction: TraversalDirection = TraversalDirection.OUTGOING


@dataclass(frozen=True)
class TraversalMatch:
    """One deterministic result path, including its explicit evidence."""

    node: GraphNode
    path: tuple[GraphRelation, ...]


class TraversalLimitExceeded(RuntimeError):
    """A complete traversal would exceed a deterministic safety bound."""


class HouseGraph:
    """Immutable-id graph with deterministic traversal and explanations."""

    def __init__(self) -> None:
        self._nodes: dict[str, GraphNode] = {}
        self._relations: dict[str, GraphRelation] = {}
        self._outgoing: dict[tuple[str, RelationKind], set[str]] = {}
        self._incoming: dict[tuple[str, RelationKind], set[str]] = {}
        self._asserted_edges: set[tuple[str, RelationKind, str]] = set()
        self._edge_relations: dict[tuple[str, RelationKind, str], GraphRelation] = {}

    @property
    def nodes(self) -> tuple[GraphNode, ...]:
        return tuple(self._nodes[key] for key in sorted(self._nodes))

    @property
    def relations(self) -> tuple[GraphRelation, ...]:
        return tuple(self._relations[key] for key in sorted(self._relations))

    def add_node(self, node: GraphNode) -> None:
        existing = self._nodes.get(node.node_id)
        if existing is not None and existing.kind is not node.kind:
            raise ValueError(f"Stabile ID {node.node_id!r} hat widersprüchliche Typen")
        self._nodes[node.node_id] = node

    def replace_node(self, node: GraphNode) -> None:
        """Replace an existing node's label/attributes (live state, 7.8 B8)."""
        existing = self._nodes.get(node.node_id)
        if existing is None or existing.kind is not node.kind:
            raise ValueError(f"Knoten {node.node_id!r} kann nicht ersetzt werden")
        self._nodes[node.node_id] = node

    def add_relation(
        self,
        source_id: str,
        kind: RelationKind,
        target_id: str,
        *,
        provenance: FactProvenance,
        confidence: ConfidenceClass,
        observed_at: datetime | None = None,
    ) -> GraphRelation:
        if source_id not in self._nodes or target_id not in self._nodes:
            raise ValueError("Beide Endpunkte einer Hausbeziehung müssen existieren")
        key = f"{source_id}\0{kind.value}\0{target_id}"
        relation_id = "rel_" + hashlib.sha256(key.encode()).hexdigest()[:20]
        relation = GraphRelation(
            relation_id,
            source_id,
            kind,
            target_id,
            provenance,
            confidence,
            observed_at,
        )
        edge = (source_id, kind, target_id)
        self._relations[relation_id] = relation
        self._edge_relations[edge] = relation
        self._outgoing.setdefault((source_id, kind), set()).add(target_id)
        self._incoming.setdefault((target_id, kind), set()).add(source_id)
        if relation.is_asserted_fact:
            self._asserted_edges.add(edge)
        else:
            self._asserted_edges.discard(edge)
        return relation

    def node(self, node_id: str) -> GraphNode | None:
        return self._nodes.get(node_id)

    def related(
        self,
        node_id: str,
        kind: RelationKind | None = None,
        *,
        asserted_only: bool = True,
    ) -> tuple[GraphNode, ...]:
        target_ids = (
            set(self._outgoing.get((node_id, kind), ()))
            if kind is not None
            else {
                target_id
                for (source_id, _), targets in self._outgoing.items()
                if source_id == node_id
                for target_id in targets
            }
        )
        if asserted_only:
            if kind is not None:
                target_ids = {
                    target_id
                    for target_id in target_ids
                    if (node_id, kind, target_id) in self._asserted_edges
                }
            else:
                target_ids = {
                    target_id
                    for (source_id, asserted_kind), targets in self._outgoing.items()
                    if source_id == node_id
                    for target_id in targets
                    if (source_id, asserted_kind, target_id) in self._asserted_edges
                }
        return tuple(self._nodes[item] for item in sorted(target_ids))

    def sources(
        self,
        node_id: str,
        kind: RelationKind,
        *,
        asserted_only: bool = True,
    ) -> tuple[GraphNode, ...]:
        """Return asserted incoming neighbours through the indexed graph."""
        source_ids = set(self._incoming.get((node_id, kind), ()))
        if asserted_only:
            source_ids = {
                source_id
                for source_id in source_ids
                if (source_id, kind, node_id) in self._asserted_edges
            }
        return tuple(self._nodes[item] for item in sorted(source_ids))

    def evidence(
        self, source_id: str, kind: RelationKind, target_id: str
    ) -> GraphRelation | None:
        return self._edge_relations.get((source_id, kind, target_id))

    def traverse(
        self,
        start_ids: Iterable[str],
        steps: tuple[TraversalStep, ...],
        *,
        asserted_only: bool = True,
        max_depth: int = 4,
        max_nodes_visited: int = 20_000,
        max_frontier_size: int = 5_000,
        max_paths: int = 10_000,
    ) -> tuple[TraversalMatch, ...]:
        """Follow an explicit bounded relation path using graph indices.

        This is deliberately not an open-ended graph search.  Every hop is a
        typed ``RelationKind`` and direction supplied by the caller. Paths
        exceeding ``max_depth`` are rejected, repeated nodes in one path are
        ignored, and statistical edges disappear under the safe default.
        """
        if max_depth < 0 or max_depth > 8:
            raise ValueError("max_depth must be between 0 and 8")
        if len(steps) > max_depth:
            raise ValueError("Traversal exceeds its maximum hop depth")
        if min(max_nodes_visited, max_frontier_size, max_paths) < 1:
            raise ValueError("Traversal complexity bounds must be positive")
        frontier: list[tuple[str, tuple[GraphRelation, ...], frozenset[str]]] = [
            (node_id, (), frozenset({node_id}))
            for node_id in sorted(set(start_ids))
            if node_id in self._nodes
        ]
        if len(frontier) > max_frontier_size or len(frontier) > max_paths:
            raise TraversalLimitExceeded("initial frontier exceeds traversal bound")
        nodes_visited = len(frontier)
        for step in steps:
            next_frontier: list[
                tuple[str, tuple[GraphRelation, ...], frozenset[str]]
            ] = []
            for node_id, path, visited in frontier:
                neighbours = (
                    self._outgoing.get((node_id, step.kind), ())
                    if step.direction is TraversalDirection.OUTGOING
                    else self._incoming.get((node_id, step.kind), ())
                )
                for neighbour_id in sorted(neighbours):
                    if neighbour_id in visited:
                        continue
                    edge = (
                        (node_id, step.kind, neighbour_id)
                        if step.direction is TraversalDirection.OUTGOING
                        else (neighbour_id, step.kind, node_id)
                    )
                    relation = self._edge_relations.get(edge)
                    if relation is None or (
                        asserted_only and not relation.is_asserted_fact
                    ):
                        continue
                    next_frontier.append((
                        neighbour_id,
                        (*path, relation),
                        visited | {neighbour_id},
                    ))
                    nodes_visited += 1
                    if nodes_visited > max_nodes_visited:
                        raise TraversalLimitExceeded("visited-node bound exceeded")
                    if len(next_frontier) > max_frontier_size:
                        raise TraversalLimitExceeded("frontier bound exceeded")
                    if len(next_frontier) > max_paths:
                        raise TraversalLimitExceeded("path bound exceeded")
            frontier = next_frontier
            if not frontier:
                break
        # Multiple graph paths may reach the same node. Keep the lexically
        # first evidence path so result and trace ordering are reproducible.
        by_node: dict[str, tuple[GraphRelation, ...]] = {}
        for node_id, path, _ in sorted(
            frontier,
            key=lambda item: (item[0], tuple(edge.relation_id for edge in item[1])),
        ):
            by_node.setdefault(node_id, path)
        return tuple(
            TraversalMatch(self._nodes[node_id], by_node[node_id])
            for node_id in sorted(by_node)
        )

    def redacted_diagnostics(self) -> dict[str, object]:
        counts: dict[str, int] = {}
        for node in self._nodes.values():
            counts[node.kind.value] = counts.get(node.kind.value, 0) + 1
        return {
            "node_counts": counts,
            "relation_count": len(self._relations),
            "statistical_relation_count": sum(
                relation.provenance is FactProvenance.STATISTICAL
                for relation in self._relations.values()
            ),
        }


def build_house_graph(
    world: WorldModel,
    configured_relations: Iterable[RelationSpec] = (),
) -> HouseGraph:
    """Build one graph from the same snapshots used by the language turn."""
    graph = HouseGraph()
    for floor in world.floors:
        graph.add_node(GraphNode(f"floor:{floor.floor_id}", NodeKind.FLOOR, floor.name))
    for area in world.areas:
        graph.add_node(GraphNode(f"area:{area.area_id}", NodeKind.AREA, area.name))
        floor_ids = {
            entity.floor_id
            for entity in world.entities_by_area_id.get(area.area_id, ())
            if entity.floor_id is not None
        }
        if len(floor_ids) == 1:
            floor_id = next(iter(floor_ids))
            graph.add_relation(
                f"area:{area.area_id}",
                RelationKind.LOCATED_IN,
                f"floor:{floor_id}",
                provenance=FactProvenance.OBSERVED,
                confidence=ConfidenceClass.CERTAIN,
            )
    for device in world.devices:
        graph.add_node(GraphNode(f"device:{device.device_id}", NodeKind.DEVICE, device.name))
        if device.area_id is not None and graph.node(f"area:{device.area_id}") is not None:
            graph.add_relation(
                f"device:{device.device_id}",
                RelationKind.LOCATED_IN,
                f"area:{device.area_id}",
                provenance=FactProvenance.OBSERVED,
                confidence=ConfidenceClass.CERTAIN,
            )
    now = datetime.now(timezone.utc)
    property_nodes: set[str] = set()
    capability_nodes: set[str] = set()
    for entity in world.entities:
        kind = NodeKind.PERSON if entity.domain == "person" else NodeKind.ENTITY
        graph.add_node(
            GraphNode(
                f"entity:{entity.entity_id}",
                kind,
                entity.friendly_name,
                {
                    "domain": entity.domain,
                    "state": entity.state,
                    "capabilities": tuple(sorted(entity.capabilities)),
                    "quality": "available" if entity.state not in {"unknown", "unavailable"} else entity.state,
                },
            )
        )
        area_id = entity.area_id
        if area_id is not None and graph.node(f"area:{area_id}") is not None:
            graph.add_relation(
                f"entity:{entity.entity_id}",
                RelationKind.LOCATED_IN,
                f"area:{area_id}",
                provenance=FactProvenance.OBSERVED,
                confidence=ConfidenceClass.CERTAIN,
                observed_at=entity.last_updated or now,
            )
        if entity.floor_id is not None and graph.node(f"floor:{entity.floor_id}") is not None:
            graph.add_relation(
                f"entity:{entity.entity_id}",
                RelationKind.ON_FLOOR,
                f"floor:{entity.floor_id}",
                provenance=FactProvenance.OBSERVED,
                confidence=ConfidenceClass.CERTAIN,
                observed_at=entity.last_updated or now,
            )
        measured_property = {
            "temperature": "temperature",
            "humidity": "humidity",
            "power": "power",
            "energy": "energy",
            "battery": "battery",
        }.get(entity.device_class or "")
        if measured_property is not None:
            property_id = f"property:{measured_property}"
            if property_id not in property_nodes:
                graph.add_node(GraphNode(property_id, NodeKind.PROPERTY, measured_property))
                property_nodes.add(property_id)
            graph.add_relation(
                f"entity:{entity.entity_id}",
                RelationKind.MEASURES,
                property_id,
                provenance=FactProvenance.OBSERVED,
                confidence=ConfidenceClass.CERTAIN,
                observed_at=entity.last_updated or now,
            )
        for capability in sorted(entity.capabilities):
            capability_id = f"capability:{capability}"
            if capability_id not in capability_nodes:
                graph.add_node(GraphNode(capability_id, NodeKind.CAPABILITY, capability))
                capability_nodes.add(capability_id)
            graph.add_relation(
                f"entity:{entity.entity_id}",
                RelationKind.HAS_CAPABILITY,
                capability_id,
                provenance=FactProvenance.OBSERVED,
                confidence=ConfidenceClass.CERTAIN,
                observed_at=entity.last_updated or now,
            )
        device = world.device_for_entity(entity.entity_id)
        if device is not None:
            graph.add_relation(
                f"entity:{entity.entity_id}",
                RelationKind.BELONGS_TO,
                f"device:{device.device_id}",
                provenance=FactProvenance.OBSERVED,
                confidence=ConfidenceClass.CERTAIN,
                observed_at=entity.last_updated or now,
            )
    for spec in configured_relations:
        graph.add_relation(
            spec.source_id,
            spec.kind,
            spec.target_id,
            provenance=FactProvenance.CONFIGURED,
            confidence=ConfidenceClass.CONFIRMED,
        )
        if spec.kind is RelationKind.ADJACENT_TO:
            graph.add_relation(
                spec.target_id,
                spec.kind,
                spec.source_id,
                provenance=FactProvenance.DERIVED,
                confidence=ConfidenceClass.DERIVED,
            )
    return graph


def _empty_attributes() -> dict[str, object]:
    return {}


def parse_relation_specs(raw: object) -> tuple[RelationSpec, ...]:
    """Parse configured ``source | relation | target`` lines."""
    if raw is None or raw == "":
        return ()
    if not isinstance(raw, str):
        raise ValueError("Hausbeziehungen müssen Textzeilen sein")
    specs: list[RelationSpec] = []
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = tuple(part.strip() for part in line.split("|"))
        if len(parts) != 3 or not parts[0] or not parts[2]:
            raise ValueError(f"Ungültige Hausbeziehung in Zeile {line_number}")
        try:
            kind = RelationKind(parts[1])
        except ValueError as err:
            raise ValueError(
                f"Unbekannter Beziehungstyp in Zeile {line_number}"
            ) from err
        specs.append(RelationSpec(parts[0], kind, parts[2]))
        if len(specs) > 500:
            raise ValueError("Es sind höchstens 500 Hausbeziehungen erlaubt")
    return tuple(specs)
