"""Grammar parsers of the automation management island (hassil).

What remains of the historic hassil parser family (7.7 B5): the automation
query, delete and on/off grammars and two helpers shared by the automation
trigger, condition and action parsers. Direct device and query language is
compiled by the V8 semantic path; its historic parsers and grammars are
deleted (compare old states through git worktrees, ``scripts/corpus_shadow.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hassil import Intents, SlotList, WildcardSlotList

from .hassil_compat import recognize_aligned
from .automation_summary import AutomationSummary, automations_named
from .entities import (
    EntitySnapshot,
)
from .nlu.entity_resolution import ResolutionStatus, resolve_phrase
from .nlu.frame import SemanticFrame, TargetReference
from .nlu.lexicon import _STATE_SLOT_LIST
from .nlu.parser import ParseContext, ParseResult
from .nlu.query_command import (
    QueryCommand,
    QueryFilter,
    QueryScope,
    QueryTarget,
    QueryTargetKind,
)
from .nlu.query_executor import QueryExecutor
from .nlu.semantic_state import SemanticState


# The {name} wildcard captures everything between the fixed template words,
# so "fahre die Rollade im Büro hoch" captures "Rollade im Büro" verbatim -
# including the preposition. Real HA friendly names are almost never a
# grammatically exact match ("Rolllade Büro", not "Rolllade im Büro"), so
# without stripping these the captured text falls through to a fragile
# contains-match (or misses an exact match it should have hit). Stripping
# is safe: none of these words plausibly appear as part of a real entity
# name, only as connective glue in a spoken sentence.
_LOCATIVE_PREPOSITIONS = re.compile(r"\b(in der|in dem|im|am|beim)\b", re.IGNORECASE)


def strip_locative_prepositions(name: str) -> str:
    without_prepositions = _LOCATIVE_PREPOSITIONS.sub(" ", name)
    return re.sub(r"\s+", " ", without_prepositions).strip()


# _DEVICE_CLASS_SLOT_LIST/_STATE_SLOT_LIST/_STATE_ADJ_SLOT_LIST now live in
# nlu/lexicon.py (V6.3, "Semantic Lexicon") - imported above. Predicate-
# position {state} values are the SemanticState member *name* (a plain
# string, cast back to the enum member via STATE_NAME_TO_SEMANTIC below).
STATE_NAME_TO_SEMANTIC: dict[str, SemanticState] = {
    "OPEN": SemanticState.OPEN,
    "CLOSED": SemanticState.CLOSED,
    "ON": SemanticState.ON,
    "OFF": SemanticState.OFF,
    "ACTIVE": SemanticState.ACTIVE,
    "INACTIVE": SemanticState.INACTIVE,
}


# Shared, stateless (HomeIntent v4.2.1 plan, Phase 7): StateQueryParser now
# runs its own candidate filtering/ambiguity classification through the same
# QueryExecutor a future V5 consumer would use, instead of a private ad hoc
# list comprehension - see StateQueryParser's own docstring for the "why now,
# but response text unchanged" scope decision.
_QUERY_EXECUTOR = QueryExecutor()


class AutomationQueryParser:
    """Wraps the {name}/{state} grammar ("welche Automationen gibt es",
    "was schaltet das Küchenlicht", "warum ist das Küchenlicht an") -
    HomeIntent V5 Teil 9/10, V5.29 "Automation Query", the 16th separately-
    compiled hassil grammar.

    Unlike every other query parser here, its ``QueryResult`` is built from
    ``automations.yaml``/the metadata sidecar (``AutomationSummary``), not
    live entity state - data this class has no way to fetch itself (``nlu``-
    adjacent parsers stay hass-free, same boundary ``StateQueryParser``
    already documents). So ``automations`` is an explicit parameter to
    ``parse()``, supplied by the caller, rather than folded into
    ``ParseContext`` like ``world_model`` - ``ParseContext`` otherwise holds
    only cheap, always-available per-turn data every parser can rely on;
    this one field would need an async file read on every single turn to
    keep that promise, which is exactly the per-turn I/O cost this design
    avoids (see ``engine.py``'s ``_AUTOMATION_QUERY_RE`` pre-check, mirroring
    ``match_automation()``'s own ``_AUTOMATION_TRIGGER_RE`` gate).

    No {area}/{device_class} slots - see this grammar's own YAML docstring
    for why area-scoping automations is out of scope. An unresolved or
    ambiguous {name} is refused (``None``, no clarification round-trip),
    same "never guess" precedent ``StateQueryParser._parse_check_state``
    already sets for its own singular {name} resolution.
    """

    def __init__(self, intents: Intents) -> None:
        self._intents = intents

    def parse(
        self,
        text: str,
        context: ParseContext,
        automations: tuple[AutomationSummary, ...],
    ) -> ParseResult | None:
        slot_lists = {
            "state": _STATE_SLOT_LIST,
            "name": WildcardSlotList(name="name"),
        }
        result = recognize_aligned(text, self._intents, slot_lists=slot_lists, language="de")
        if result is None or result.intent is None:
            return None

        if result.intent.name == "HassAutomationQuery":
            return self._parse_query(text, result.entities, context, automations, causal=False)
        if result.intent.name == "HassAutomationWhyQuery":
            return self._parse_query(text, result.entities, context, automations, causal=True)
        return None

    @staticmethod
    def _parse_query(
        text: str,
        slots: dict,
        context: ParseContext,
        automations: tuple[AutomationSummary, ...],
        causal: bool,
    ) -> ParseResult | None:
        intent_name = "HassAutomationWhyQuery" if causal else "HassAutomationQuery"
        name_slot = slots.get("name")

        if name_slot is None:
            # "welche Automationen gibt es" - no entity filter, list every
            # automation the caller read.
            query_command = QueryCommand(
                intent=intent_name,
                scope=QueryScope.LIST,
                target=QueryTarget(kind=QueryTargetKind.AUTOMATION),
                filter=QueryFilter(),
            )
            query_result = _QUERY_EXECUTOR.execute(query_command, candidates=[], automations=automations)
            frame = SemanticFrame(
                intent=intent_name,
                target=None,
                area=None,
                parameters={"query_command": query_command, "query_result": query_result},
                source_text=text,
            )
            return ParseResult(frame=frame, resolved_entities=[])

        name = strip_locative_prepositions(str(name_slot.value))
        resolved = resolve_phrase(name, context.entities, index=context.index)
        if resolved.status is not ResolutionStatus.RESOLVED or resolved.entity is None:
            return None  # not found or ambiguous - never guess, no clarification round-trip

        query_command = QueryCommand(
            intent=intent_name,
            scope=QueryScope.LIST,
            target=QueryTarget(
                domain=resolved.entity.domain,
                device_class=resolved.entity.device_class,
                entity_id=resolved.entity.entity_id,
                kind=QueryTargetKind.AUTOMATION,
            ),
            filter=QueryFilter(),
        )
        query_result = _QUERY_EXECUTOR.execute(
            query_command, candidates=[resolved.entity], automations=automations
        )

        frame = SemanticFrame(
            intent=intent_name,
            target=TargetReference(text=name, entity_id=resolved.entity.entity_id, domain=resolved.entity.domain),
            area=None,
            parameters={"query_command": query_command, "query_result": query_result},
            source_text=text,
        )
        return ParseResult(frame=frame, resolved_entities=list(query_result.entities))


@dataclass(frozen=True)
class AutomationDeleteMatch:
    """Result of ``AutomationDeleteParser.parse()`` - the entity the {name}
    slot resolved to, plus every automation referencing it (0, 1, or more).
    Cardinality is deliberately not decided here: turning a count into a
    confirmation question vs. one of two different refusal messages is
    ``engine.py``'s ``match_automation_delete()`` job, the same split
    ``QueryExecutor``/``ResponseGenerator`` already keep for query results.
    """

    # None when {name} named the automation itself (its alias), not a device.
    entity: EntitySnapshot | None
    matched: tuple[AutomationSummary, ...]


_AUTOMATION_NAME_FRAME_RE = re.compile(
    r"^(?:(?:für|fuer)\s+(?P<for>.+)|die\s+(?P<rel>.+?)\s+(?:schaltet|steuert))$",
    re.IGNORECASE,
)


def _automation_name_slot(value: str) -> str:
    """The bare name from "für X", "die X steuert" or "X" (grammar variants)."""
    stripped = value.strip()
    match = _AUTOMATION_NAME_FRAME_RE.match(stripped)
    if match is None:
        return stripped
    return (match.group("for") or match.group("rel") or stripped).strip()


class AutomationDeleteParser:
    """Wraps the {name} grammar ("lösche die Automation für X") - HomeIntent
    V5 Teil 8/10, V5.28 "Automation Deletion", the 17th separately-compiled
    hassil grammar.

    Deliberately reuses ``AutomationQueryParser``'s own "entity-filtered"
    resolution shape (resolve {name} -> filter automations by
    ``referenced_entity_ids``) rather than inventing a second targeting
    mechanism (Regel 6). Unlike the query parser, {name} is mandatory here
    (see this grammar's own YAML docstring for why a target-less "lösche die
    Automation" is refused, not treated as "delete every automation" -
    niemals raten).

    Same ``automations`` calling shape ``AutomationQueryParser.parse()``
    already establishes: an explicit parameter, not folded into
    ``ParseContext``, for the same "no per-turn automations.yaml read
    unless the sentence plausibly needs it" reason (see ``engine.py``'s
    ``_AUTOMATION_DELETE_RE`` pre-check).
    """

    def __init__(self, intents: Intents) -> None:
        self._intents = intents

    def parse(
        self,
        text: str,
        context: ParseContext,
        automations: tuple[AutomationSummary, ...],
    ) -> AutomationDeleteMatch | None:
        slot_lists: dict[str, SlotList] = {"name": WildcardSlotList(name="name")}
        result = recognize_aligned(text, self._intents, slot_lists=slot_lists, language="de")
        if result is None or result.intent is None or result.intent.name != "HassAutomationDelete":
            return None

        name_slot = result.entities.get("name")
        if name_slot is None:
            return None  # grammar requires {name} - structurally unreachable, defense only

        spoken = _automation_name_slot(str(name_slot.value))
        named = automations_named(spoken, automations)
        if named:
            return AutomationDeleteMatch(entity=None, matched=named)
        name = strip_locative_prepositions(spoken)
        resolved = resolve_phrase(name, context.entities, index=context.index)
        if resolved.status is not ResolutionStatus.RESOLVED or resolved.entity is None:
            return None  # entity itself not found or ambiguous - never guess (Regel 4)

        matched = tuple(
            a for a in automations if resolved.entity.entity_id in a.referenced_entity_ids
        )
        return AutomationDeleteMatch(entity=resolved.entity, matched=matched)


@dataclass(frozen=True)
class AutomationToggleMatch:
    """Result of ``AutomationToggleParser.parse()`` - mirrors
    ``AutomationDeleteMatch`` exactly (entity resolved, every automation
    referencing it, cardinality decided by the caller), plus one extra
    field: ``enable`` tells ``engine.py``'s ``match_automation_disable()``/
    ``match_automation_enable()`` which of the two intents matched, since
    both share this one parser/match type rather than two near-identical
    copies (Regel 6)."""

    # None when {name} named the automation itself (its alias), not a device.
    entity: EntitySnapshot | None
    matched: tuple[AutomationSummary, ...]
    enable: bool


class AutomationToggleParser:
    """Wraps the {name} grammar ("deaktiviere/aktiviere die Automation für
    X") - HomeIntent V5 Teil 8/10 (Wave 11, "Automation Disable/Enable"),
    the 18th separately-compiled hassil grammar.

    One parser for both ``HassAutomationDisable`` and ``HassAutomationEnable``
    (compiled from the same YAML file, see intents/de/automation_toggle/
    automation_toggle.yaml) - same "entity-filtered" resolution
    ``AutomationDeleteParser`` already establishes, reused verbatim rather
    than a third targeting mechanism (Regel 6). {name} is mandatory, same
    "never guess a target-less bulk operation" reasoning.
    """

    def __init__(self, intents: Intents) -> None:
        self._intents = intents

    def parse(
        self,
        text: str,
        context: ParseContext,
        automations: tuple[AutomationSummary, ...],
    ) -> AutomationToggleMatch | None:
        slot_lists: dict[str, SlotList] = {"name": WildcardSlotList(name="name")}
        result = recognize_aligned(text, self._intents, slot_lists=slot_lists, language="de")
        if result is None or result.intent is None:
            return None
        if result.intent.name == "HassAutomationDisable":
            enable = False
        elif result.intent.name == "HassAutomationEnable":
            enable = True
        else:
            return None

        name_slot = result.entities.get("name")
        if name_slot is None:
            return None  # grammar requires {name} - structurally unreachable, defense only

        spoken = _automation_name_slot(str(name_slot.value))
        named = automations_named(spoken, automations)
        if named:
            return AutomationToggleMatch(entity=None, matched=named, enable=enable)
        name = strip_locative_prepositions(spoken)
        resolved = resolve_phrase(name, context.entities, index=context.index)
        if resolved.status is not ResolutionStatus.RESOLVED or resolved.entity is None:
            return None  # entity itself not found or ambiguous - never guess (Regel 4)

        matched = tuple(
            a for a in automations if resolved.entity.entity_id in a.referenced_entity_ids
        )
        return AutomationToggleMatch(entity=resolved.entity, matched=matched, enable=enable)
