"""Canonical, non-executable meaning graph for one language turn.

``SemanticGraph`` represents relations in the utterance.  It contains no
Home Assistant service names and performs no registry lookup; grounding is
added by the authoritative resolver through stable entity identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Iterable, Mapping, Sequence

from .german_structure import (
    ClauseKind,
    GermanStructuralAnalysis,
    StructuralRelationKind,
    StructuralToken,
)
from .semantic_lexicon import SemanticAnalysis, SemanticKind
from .semantic_utterance import SpeechAct
from .temporal_semantics import analyse_temporal_semantics


class SemanticNodeKind(Enum):
    UTTERANCE = auto()
    CLAUSE = auto()
    ACTION = auto()
    ENTITY = auto()
    ENTITY_CLASS = auto()
    AREA = auto()
    FLOOR = auto()
    PROPERTY = auto()
    STATE = auto()
    VALUE = auto()
    COMPARISON = auto()
    QUANTIFIER = auto()
    REFERENCE = auto()
    NEGATION = auto()
    TEMPORAL = auto()
    CONDITION = auto()
    TRIGGER = auto()
    UNIT = auto()
    REPAIR = auto()
    RELATION = auto()
    LOGICAL = auto()


class SemanticEdgeKind(Enum):
    CONTAINS = auto()
    TARGET = auto()
    FILTER = auto()
    EXCLUDE = auto()
    CONDITION = auto()
    TRIGGER = auto()
    THEN = auto()
    AND = auto()
    OR = auto()
    BEFORE = auto()
    AFTER = auto()
    UNTIL = auto()
    WHILE = auto()
    SINCE = auto()
    MODIFIES = auto()
    REPLACES = auto()
    RESOLVES_TO = auto()
    LOCATED_IN = auto()
    ON_FLOOR = auto()
    NOT = auto()
    VALUE_OF = auto()
    PROPERTY_OF = auto()
    COMPARE = auto()
    DEPENDS_ON = auto()
    REFERENCE_TO = auto()


@dataclass(frozen=True)
class SemanticNode:
    node_id: str
    kind: SemanticNodeKind
    value: str
    start: int | None = None
    end: int | None = None
    attributes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SemanticEdge:
    source: str
    kind: SemanticEdgeKind
    target: str
    attributes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SemanticGraph:
    """Immutable turn meaning with stable deterministic node identifiers."""

    source_text: str
    nodes: tuple[SemanticNode, ...]
    edges: tuple[SemanticEdge, ...]
    root_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        identifiers = tuple(node.node_id for node in self.nodes)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("SemanticGraph node identifiers must be unique")
        known = set(identifiers)
        if any(edge.source not in known or edge.target not in known for edge in self.edges):
            raise ValueError("SemanticGraph edge references an unknown node")
        if any(root not in known for root in self.root_ids):
            raise ValueError("SemanticGraph root references an unknown node")

    def nodes_of_kind(self, kind: SemanticNodeKind) -> tuple[SemanticNode, ...]:
        return tuple(node for node in self.nodes if node.kind is kind)

    def canonical_snapshot(self) -> Mapping[str, object]:
        """Return a stable JSON-compatible form for regression snapshots."""
        return {
            "source": self.source_text,
            "roots": self.root_ids,
            "nodes": tuple(
                {
                    "id": node.node_id,
                    "kind": node.kind.name.lower(),
                    "value": node.value,
                    "span": (node.start, node.end),
                    "attributes": node.attributes,
                }
                for node in self.nodes
            ),
            "edges": tuple(
                {
                    "source": edge.source,
                    "relation": edge.kind.name.lower(),
                    "target": edge.target,
                    "attributes": edge.attributes,
                }
                for edge in self.edges
            ),
        }

    def with_grounded_entities(self, entity_ids: Iterable[str]) -> SemanticGraph:
        """Attach resolver results without reproducing entity resolution."""
        existing = {node.value for node in self.nodes_of_kind(SemanticNodeKind.ENTITY)}
        additions = tuple(sorted(set(entity_ids) - existing))
        if not additions:
            return self
        nodes = list(self.nodes)
        edges = list(self.edges)
        target_nodes = self.nodes_of_kind(SemanticNodeKind.ENTITY_CLASS)
        for offset, entity_id in enumerate(additions):
            node_id = f"entity:{len(existing) + offset}"
            nodes.append(SemanticNode(node_id, SemanticNodeKind.ENTITY, entity_id))
            for target in target_nodes:
                edges.append(SemanticEdge(target.node_id, SemanticEdgeKind.RESOLVES_TO, node_id))
        return replace(self, nodes=tuple(nodes), edges=tuple(edges))

    def with_grounded_locations(
        self,
        locations: Iterable[tuple[str, str | None, str | None]],
    ) -> SemanticGraph:
        """Attach only locations proven by the existing area/floor resolver."""
        nodes = list(self.nodes)
        edges = list(self.edges)
        target_nodes = self.nodes_of_kind(SemanticNodeKind.ENTITY_CLASS)
        known = {(node.kind, node.value): node for node in nodes}
        for spoken, area_id, floor_id in locations:
            if area_id is not None:
                key = (SemanticNodeKind.AREA, area_id)
                node = known.get(key)
                if node is None:
                    node = SemanticNode(
                        f"area:{len([item for item in nodes if item.kind is SemanticNodeKind.AREA])}",
                        SemanticNodeKind.AREA,
                        area_id,
                        attributes=(("surface", spoken),),
                    )
                    known[key] = node
                    nodes.append(node)
                for target in target_nodes:
                    edges.append(SemanticEdge(target.node_id, SemanticEdgeKind.LOCATED_IN, node.node_id))
            if floor_id is not None:
                key = (SemanticNodeKind.FLOOR, floor_id)
                node = known.get(key)
                if node is None:
                    node = SemanticNode(
                        f"floor:{len([item for item in nodes if item.kind is SemanticNodeKind.FLOOR])}",
                        SemanticNodeKind.FLOOR,
                        floor_id,
                        attributes=(("surface", spoken),),
                    )
                    known[key] = node
                    nodes.append(node)
                for target in target_nodes:
                    edges.append(SemanticEdge(target.node_id, SemanticEdgeKind.ON_FLOOR, node.node_id))
        return replace(self, nodes=tuple(nodes), edges=tuple(edges))

    def with_excluded_entities(self, entity_ids: Iterable[str]) -> SemanticGraph:
        """Attach exclusions already proven by the authoritative resolver."""
        nodes = list(self.nodes)
        edges = list(self.edges)
        existing = {
            node.value: node
            for node in nodes
            if node.kind is SemanticNodeKind.ENTITY
        }
        actions = tuple(sorted(
            self.nodes_of_kind(SemanticNodeKind.ACTION),
            key=lambda item: item.start if item.start is not None else len(self.source_text),
        )[:1])
        for entity_id in sorted(set(entity_ids)):
            node = existing.get(entity_id)
            if node is None:
                node = SemanticNode(
                    f"excluded-entity:{len(existing)}",
                    SemanticNodeKind.ENTITY,
                    entity_id,
                    attributes=(("role", "excluded"),),
                )
                existing[entity_id] = node
                nodes.append(node)
            for action in actions:
                edges.append(SemanticEdge(action.node_id, SemanticEdgeKind.EXCLUDE, node.node_id))
        return replace(self, nodes=tuple(nodes), edges=tuple(edges))


_NODE_KIND = {
    SemanticKind.ACTION: SemanticNodeKind.ACTION,
    SemanticKind.DOMAIN: SemanticNodeKind.ENTITY_CLASS,
    SemanticKind.DEVICE_CLASS: SemanticNodeKind.ENTITY_CLASS,
    SemanticKind.PROPERTY: SemanticNodeKind.PROPERTY,
    SemanticKind.STATE: SemanticNodeKind.STATE,
    SemanticKind.COMPARATOR: SemanticNodeKind.COMPARISON,
    SemanticKind.RELATION: SemanticNodeKind.RELATION,
    SemanticKind.QUANTIFIER: SemanticNodeKind.QUANTIFIER,
}

_RELATION_KIND = {
    StructuralRelationKind.IF: SemanticEdgeKind.CONDITION,
    StructuralRelationKind.THEN: SemanticEdgeKind.THEN,
    StructuralRelationKind.AND: SemanticEdgeKind.AND,
    StructuralRelationKind.OR: SemanticEdgeKind.OR,
    StructuralRelationKind.EXCEPT: SemanticEdgeKind.EXCLUDE,
    StructuralRelationKind.BEFORE: SemanticEdgeKind.BEFORE,
    StructuralRelationKind.AFTER: SemanticEdgeKind.AFTER,
    StructuralRelationKind.UNTIL: SemanticEdgeKind.UNTIL,
    StructuralRelationKind.WHILE: SemanticEdgeKind.WHILE,
    StructuralRelationKind.MODIFIES: SemanticEdgeKind.MODIFIES,
    StructuralRelationKind.REPLACES: SemanticEdgeKind.REPLACES,
    StructuralRelationKind.SEQUENCE: SemanticEdgeKind.THEN,
}


def build_semantic_graph(
    source_text: str,
    tokens: Sequence[StructuralToken],
    structure: GermanStructuralAnalysis,
    semantics: SemanticAnalysis,
    speech_act: SpeechAct | None = None,
) -> SemanticGraph:
    """Compose structural and lexical evidence into one relation graph."""
    nodes: list[SemanticNode] = [
        SemanticNode("utterance", SemanticNodeKind.UTTERANCE, "turn", 0, len(source_text))
    ]
    edges: list[SemanticEdge] = []
    clause_nodes: dict[str, str] = {}
    for clause in structure.clauses:
        node_id = f"clause:{clause.clause_id}"
        clause_nodes[clause.clause_id] = node_id
        nodes.append(SemanticNode(
            node_id,
            SemanticNodeKind.CLAUSE,
            clause.kind.name.lower(),
            clause.char_start,
            clause.char_end,
            (("connector", clause.connector),) if clause.connector else (),
        ))
        edges.append(SemanticEdge("utterance", SemanticEdgeKind.CONTAINS, node_id))

        if clause.kind in {ClauseKind.CONDITION, ClauseKind.TEMPORAL}:
            scoped_kind = (
                SemanticNodeKind.CONDITION
                if clause.kind is ClauseKind.CONDITION
                else SemanticNodeKind.TEMPORAL
            )
            scope_id = f"scope:{clause.clause_id}"
            nodes.append(SemanticNode(
                scope_id,
                scoped_kind,
                clause.connector or clause.kind.name.lower(),
                clause.char_start,
                clause.char_end,
            ))
            edges.append(SemanticEdge(node_id, SemanticEdgeKind.CONTAINS, scope_id))
            if (
                clause.kind is ClauseKind.CONDITION
                and speech_act is SpeechAct.AUTOMATION
                and clause.connector != "nur wenn"
            ):
                trigger_id = f"trigger:{clause.clause_id}"
                nodes.append(SemanticNode(
                    trigger_id,
                    SemanticNodeKind.TRIGGER,
                    clause.connector or "trigger",
                    clause.char_start,
                    clause.char_end,
                ))
                edges.append(SemanticEdge(
                    node_id, SemanticEdgeKind.CONTAINS, trigger_id
                ))

    for relation in structure.relations:
        source = clause_nodes.get(relation.source_clause)
        target = clause_nodes.get(relation.target_clause)
        if source is not None and target is not None:
            edge_kind = _RELATION_KIND[relation.kind]
            if (
                relation.kind is StructuralRelationKind.IF
                and speech_act is SpeechAct.AUTOMATION
                and next(
                    (
                        clause.connector
                        for clause in structure.clauses
                        if clause.clause_id == relation.source_clause
                    ),
                    None,
                ) != "nur wenn"
            ):
                edges.append(SemanticEdge(
                    source, SemanticEdgeKind.TRIGGER, target
                ))
            edges.append(SemanticEdge(source, edge_kind, target))

    semantic_by_clause: dict[str, list[SemanticNode]] = {}
    for offset, span in enumerate(semantics.spans):
        node_kind = _NODE_KIND.get(span.kind)
        if node_kind is None:
            continue
        clause = structure.clause_for_char(span.start)
        if (
            speech_act is SpeechAct.COMMAND
            and span.kind is SemanticKind.ACTION
            and clause is not None
            and clause.kind in {ClauseKind.RELATIVE, ClauseKind.CONDITION}
            and any(
                state.kind is SemanticKind.STATE
                and state.start == span.start
                and state.end == span.end
                for state in semantics.spans
            )
        ):
            # In a relative/condition predicate, homonyms such as ``an`` and
            # ``aus`` describe state. They must not become competing command
            # actions in the canonical graph.
            continue
        if (
            speech_act is SpeechAct.COMMAND
            and span.kind is SemanticKind.STATE
            and clause is not None
            and clause.kind not in {ClauseKind.RELATIVE, ClauseKind.CONDITION}
            and any(
                action.kind is SemanticKind.ACTION
                and action.start == span.start
                and action.end == span.end
                for action in semantics.spans
            )
        ):
            # German particles such as ``aus`` are both state vocabulary and
            # command predicates. In an action clause the action owns the
            # shared span; relative/condition clauses retain state meaning.
            continue
        node_id = f"meaning:{offset}"
        node = SemanticNode(
            node_id,
            node_kind,
            str(span.value),
            span.start,
            span.end,
            (("surface", span.text),),
        )
        nodes.append(node)
        if clause is not None:
            clause_id = clause_nodes[clause.clause_id]
            edges.append(SemanticEdge(clause_id, SemanticEdgeKind.CONTAINS, node_id))
            semantic_by_clause.setdefault(clause.clause_id, []).append(node)

    # Numeric values and reference expressions are structural meanings even
    # when the domain lexicon deliberately has no entry for their surface.
    for index, (token, feature) in enumerate(zip(tokens, structure.token_features)):
        if token.canonical.replace(",", "").isdigit():
            node = SemanticNode(
                f"value:{index}", SemanticNodeKind.VALUE, token.canonical,
                token.start, token.end,
            )
        elif (
            feature.word_class.name == "PRONOUN"
            and token.canonical not in {"ich", "du", "wir", "ihr", "mir", "mich", "dir", "dich"}
        ):
            node = SemanticNode(
                f"reference:{index}", SemanticNodeKind.REFERENCE, token.canonical,
                token.start, token.end,
            )
        else:
            continue
        nodes.append(node)
        clause = structure.clause_for_char(token.start)
        if clause is not None:
            edges.append(SemanticEdge(
                clause_nodes[clause.clause_id], SemanticEdgeKind.CONTAINS, node.node_id
            ))
            semantic_by_clause.setdefault(clause.clause_id, []).append(node)

    for offset, temporal in enumerate(analyse_temporal_semantics(tokens)):
        first = tokens[temporal.token_start]
        last = tokens[temporal.token_end - 1]
        node = SemanticNode(
            f"temporal:{offset}",
            SemanticNodeKind.TEMPORAL,
            temporal.value,
            first.start,
            last.end,
            tuple(
                item for item in (
                    ("kind", temporal.kind.name.lower()),
                    ("seconds", str(temporal.seconds)) if temporal.seconds is not None else None,
                )
                if item is not None
            ),
        )
        nodes.append(node)
        clause = structure.clause_for_char(first.start)
        if clause is not None:
            edges.append(SemanticEdge(
                clause_nodes[clause.clause_id], SemanticEdgeKind.CONTAINS, node.node_id
            ))
            semantic_by_clause.setdefault(clause.clause_id, []).append(node)
            temporal_edge = {
                "before": SemanticEdgeKind.BEFORE,
                "after": SemanticEdgeKind.AFTER,
                "until": SemanticEdgeKind.UNTIL,
                "while": SemanticEdgeKind.WHILE,
                "since": SemanticEdgeKind.SINCE,
            }.get(temporal.kind.name.lower(), SemanticEdgeKind.MODIFIES)
            edges.append(SemanticEdge(
                node.node_id, temporal_edge, clause_nodes[clause.clause_id]
            ))

    # Negation is a first-class scoped operator.  It points to every semantic
    # constituent inside its conservative scope; if none is known it points
    # to the clause, ensuring the negation can never disappear silently.
    for offset, negation in enumerate(structure.negations):
        token = tokens[negation.token_index]
        node = SemanticNode(
            f"negation:{offset}",
            SemanticNodeKind.NEGATION,
            token.canonical,
            token.start,
            token.end,
            (("kind", negation.kind.name.lower()),),
        )
        nodes.append(node)
        scope_nodes = tuple(
            item
            for item in semantic_by_clause.get(negation.clause_id, ())
            if item.start is not None
            and item.end is not None
            and tokens[negation.scope_start].start <= item.start
            and item.end <= tokens[negation.scope_end - 1].end
        )
        targets = scope_nodes or (
            next(item for item in nodes if item.node_id == clause_nodes[negation.clause_id]),
        )
        for target in targets:
            edges.append(SemanticEdge(node.node_id, SemanticEdgeKind.NOT, target.node_id))

    # Repair relations are mirrored between compatible semantic values, so
    # downstream consumers can suppress the original without inspecting text.
    for relation in structure.relations:
        if relation.kind is not StructuralRelationKind.REPLACES:
            continue
        original = semantic_by_clause.get(relation.source_clause, ())
        replacement = semantic_by_clause.get(relation.target_clause, ())
        for target in replacement:
            compatible = tuple(item for item in original if item.kind is target.kind)
            if compatible:
                edges.append(SemanticEdge(
                    compatible[-1].node_id, SemanticEdgeKind.REPLACES, target.node_id
                ))

    clauses_by_id = {clause.clause_id: clause for clause in structure.clauses}
    for clause_id, meanings in semantic_by_clause.items():
        clause = clauses_by_id[clause_id]
        actions = tuple(node for node in meanings if node.kind is SemanticNodeKind.ACTION)
        targets = tuple(node for node in meanings if node.kind is SemanticNodeKind.ENTITY_CLASS)
        states = tuple(node for node in meanings if node.kind is SemanticNodeKind.STATE)
        properties = tuple(
            node for node in meanings if node.kind is SemanticNodeKind.PROPERTY
        )
        values = tuple(node for node in meanings if node.kind is SemanticNodeKind.VALUE)
        comparisons = tuple(
            node for node in meanings if node.kind is SemanticNodeKind.COMPARISON
        )
        for action in actions:
            for target in targets:
                edges.append(SemanticEdge(action.node_id, SemanticEdgeKind.TARGET, target.node_id))
            if clause.kind is ClauseKind.RELATIVE:
                for state in states:
                    edges.append(SemanticEdge(action.node_id, SemanticEdgeKind.FILTER, state.node_id))
        for prop in properties:
            for target in targets:
                edges.append(SemanticEdge(prop.node_id, SemanticEdgeKind.PROPERTY_OF, target.node_id))
            for value in values:
                edges.append(SemanticEdge(value.node_id, SemanticEdgeKind.VALUE_OF, prop.node_id))
        for comparison in comparisons:
            for operand in (*properties, *values, *targets):
                edges.append(SemanticEdge(
                    comparison.node_id, SemanticEdgeKind.COMPARE, operand.node_id
                ))

    # Relative filters and exclusions often omit the repeated target. Link
    # their meaning to the immediately preceding clause, preserving scope.
    for index, clause in enumerate(structure.clauses):
        if index == 0 or clause.kind not in {ClauseKind.RELATIVE, ClauseKind.EXCLUSION}:
            continue
        prior_meanings = semantic_by_clause.get(structure.clauses[index - 1].clause_id, [])
        scoped = semantic_by_clause.get(clause.clause_id, [])
        prior_actions = [node for node in prior_meanings if node.kind is SemanticNodeKind.ACTION]
        relation_kind = (
            SemanticEdgeKind.FILTER
            if clause.kind is ClauseKind.RELATIVE
            else SemanticEdgeKind.EXCLUDE
        )
        for action in prior_actions:
            for node in scoped:
                if node.kind in {SemanticNodeKind.STATE, SemanticNodeKind.ENTITY_CLASS}:
                    edges.append(SemanticEdge(action.node_id, relation_kind, node.node_id))

    # German permits the finite/action predicate after a relative or
    # exclusion clause. When exactly one active command action exists, bind
    # the surrounding target/filter/exclusion constituents to that predicate
    # rather than relying on textual adjacency.
    if speech_act is SpeechAct.COMMAND:
        clause_kind_by_id = {
            clause.clause_id: clause.kind for clause in structure.clauses
        }
        active_actions = tuple(
            node
            for clause_id, meanings in semantic_by_clause.items()
            if clause_kind_by_id[clause_id]
            not in {ClauseKind.RELATIVE, ClauseKind.CONDITION, ClauseKind.TEMPORAL, ClauseKind.REPAIR}
            for node in meanings
            if node.kind is SemanticNodeKind.ACTION
        )
        if len(active_actions) == 1:
            action = active_actions[0]
            existing = {(edge.source, edge.kind, edge.target) for edge in edges}
            for clause_id, meanings in semantic_by_clause.items():
                clause_kind = clause_kind_by_id[clause_id]
                relation_kind = (
                    SemanticEdgeKind.FILTER
                    if clause_kind is ClauseKind.RELATIVE
                    else SemanticEdgeKind.EXCLUDE
                    if clause_kind is ClauseKind.EXCLUSION
                    else SemanticEdgeKind.TARGET
                )
                accepted_kinds = (
                    {SemanticNodeKind.STATE}
                    if relation_kind is SemanticEdgeKind.FILTER
                    else {SemanticNodeKind.ENTITY_CLASS}
                )
                for node in meanings:
                    key = (action.node_id, relation_kind, node.node_id)
                    if node.kind in accepted_kinds and key not in existing:
                        edges.append(SemanticEdge(*key))
                        existing.add(key)

    roots = tuple(clause_nodes[root] for root in structure.root_clause_ids if root in clause_nodes)
    return SemanticGraph(source_text, tuple(nodes), tuple(edges), roots or ("utterance",))
