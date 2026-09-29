"""Canonical HomeIntent understanding orchestrator.

The engine consumes the loss-aware ``LanguageDocument`` and its
``GermanStructuralAnalysis``/``SemanticGraph``, ranks ``MeaningCandidate``
objects with explicit evidence, grounds them against the per-turn
``WorldModel`` and then projects them onto the established query, automation
or validated command path.  Hassil grammars remain compatibility inputs for
closed domains; they are not a parallel understanding authority.

No LLM or cloud fallback is used.  Queries stay read-only and commands only
become ``ServiceCallPlan`` objects after the existing validator, execution
policy and service mapper have accepted the grounded semantic result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from hassil import Intents

from .areas import AreaResolveStatus, AreaSnapshot, resolve_area_name
from .automation_summary import AutomationSummary
from .automation_composition import (
    CompositionOutcome,
    EventClarification,
    OutcomeKind,
    Readers,
    compose_event_automation,
    log_composition_trace,
    resolve_event_clarification,
    unsupported_text,
)
from .automation_grounding import GroundingStatus, ground_event
from .automation_language import only_quoted_connectors, read_event_roles
from .automation_results import (
    AutomationClarificationResult,
    AutomationDraftMatchResult,
    AutomationDeletionMatchResult,
    AutomationMatchResult,
    AutomationToggleMatchResult,
)
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.semantic_compiler import empty_comparison_answer
from .nlu.target_resolution import Quantity
from .nlu.clock_language import split_relative_delay
from .nlu.entity_resolution import (
    ResolutionStatus,
    ResolveStatus,
    all_mentioned_entities,
    resolve_entity,
    resolve_mentioned_target,
)
from .nlu.entity_clarification import render_candidate_question
from .nlu.composition import independent_predicate_clauses
from .nlu.command import SemanticCommand, build_semantic_command
from .nlu.context import ConversationContext
from .nlu.discourse import ReferenceStatus, current_discourse_group, resolve_reference
from .nlu.debug import DebugTrace, format_command
from .nlu.degree_semantics import extract_degree
from .nlu.frame import AreaReference, Quantifier, SemanticFrame, TargetReference
from .nlu.primitives import SemanticAction, SemanticDirection, SemanticProperty
from .nlu.normalize import normalize
from .nlu.group_feedback import group_percentage_feedback
from .nlu.device_ontology import analyse_word
from .nlu.ontology_compiler import compile_ontology_command, compile_release
from .nlu.discourse_compiler import compile_discourse
from .nlu.need_compiler import compile_need, proposal_from_reason, routine_named_explicitly
from .nlu.capabilities import describe_abilities
from .plan_origin import PlanOrigin
from .nlu.need_semantics import interpret_need
from .nlu.place_model import PlaceKind, build_place_lexicon
from .nlu.language_frontend import LanguageDocument, analyse_language, tokenize_language
from .nlu.parser import (
    ClarificationRequest,
    ParseContext,
    ParseResult,
    create_parse_context,
)
from .nlu.reasoning import ReasoningEngine, ResolvedSemanticIntent
from .nlu.response import NluError, NluResponse
from .nlu.parse_outcome import ParseFailureReason, UnderstandingFeedback
from .nlu.response_generator import ResponseGenerator, _automation_label
from .nlu.query_command import (
    LiteralSetExpression,
    QueryCommand,
    QueryFilter,
    QueryResult,
    QueryResultStatus,
    QueryScope,
    QueryTarget,
    QueryTargetKind,
    QueryTraversal,
    RelationFilterExpression,
    SetExpression,
    SetOperator,
    SourceExpression,
    StateFilterExpression,
)
from .nlu.query_executor import QueryExecutor
from .house_graph import RelationKind, TraversalDirection
from .nlu.service_mapper import map_to_service_call
from .nlu.semantic_compiler import (
    SemanticCommandCompiler,
    SemanticQueryCompiler,
)
from .nlu.registered_operation_compiler import climate_in_named_area
from .nlu.grounded_answer import join_german
from .nlu.german_morphology import dative_location_phrase, sentence_initial
from .nlu.semantic_exclusion import has_exclusion_clause, split_exclusion
from .nlu.semantic_lexicon import SemanticKind, analyse_semantics
from .nlu.semantic_state import SemanticState
from .nlu.semantic_catalog import INTENT_BY_DOMAIN_ACTION
from .nlu.semantic_interpreter import InterpreterResult, SemanticInterpreter
from .nlu.repair_semantics import repaired_temporal_command
from .nlu.temporal_semantics import TemporalKind
from .nlu.semantic_projection import project_independent_predicates
from .nlu.semantic_utterance import (
    ClauseRole,
    Modality,
    NON_EXECUTABLE_MODALITIES,
    Polarity,
    PragmaticDisposition,
    SpeechAct,
    analyse_utterance,
    is_contextual_followup,
)
from .nlu.understanding import (
    UnderstandingAuthority,
    UnderstandingEvidence,
    UnderstandingKind,
    UnderstandingOutcome,
)
from .nlu.understanding_context import UnderstandingContext
from .nlu.verb_state_query import match_contextual_verb_state_query
from .nlu.semantic_location import resolve_semantic_location
from .nlu.query_followup_compiler import compile_query_followup
from .nlu.validator import validate_command
from .automation_action_parser import AutomationActionParser
from .automation_notification import (
    is_notification_shaped,
    notification_action,
    notification_action_from_clause,
    notification_action_from_request,
)
from .notification_language import NotificationClause, parse_notification_clause
from .nlu.action_model import NotificationRecipientKind
from .automation_condition_parser import AutomationConditionParser, split_on_top_level_and
from .nlu.locative import has_locative_cue
from .nlu.automation_shell_normalization import strip_automation_shell
from .calendar_automation import parse_calendar_automation_draft
from .automation_trigger_parser import _AUTOMATION_TRIGGER_RE, AutomationTriggerParser
from .location_property_query import LocationPropertyQueryParser, LocationQueryFeedback
from .contextual_property import ContextualPropertyResolver
from .conversation_correction import ConversationCorrectionResolver
from .relative_time_command_parser import RelativeTimeCommandParser
from .productivity import parse_duration_seconds
from .scheduled_time_command_parser import ScheduledTimeCommandParser
from .nlu.automation_model import (
    AutomationModel,
    CalendarReference,
    CalendarSchedule,
    TriggerModel,
    TriggerType,
    render_automation_tree,
)
from .nlu.automation_model import TriggerTarget
from .nlu.action_model import ActionGroup, ActionModel, ActionType
from .nlu.automation_sentence_split import (
    split_automation_document,
    split_trigger_action,
    structured_automation_condition_clauses,
)
from .nlu.automation_validator import validate_automation
from .nlu.automation_operations import validate_registered_operation
from .nlu.condition_model import (
    ConditionModel,
    ConditionNode,
    ConditionType,
    LogicalOperator,
)
from .parsers import (
    AutomationDeleteMatch,
    AutomationDeleteParser,
    AutomationQueryParser,
    AutomationToggleMatch,
    AutomationToggleParser,
)
from .service_call import (
    ACTION_OPPOSITES,
    CLIMATE_EXTENDED_INTENTS,
    FAN_EXTENDED_INTENTS,
    INTENTS,
    LIGHT_EXTENDED_INTENTS,
    PERCENT_INTENTS,
    QUERY_INTENTS,
    REGISTERED_OPERATION_INTENT,
    ServiceCallPlan,
)
from .nlu.automation_operations import describe_registered_operation, describe_registered_result
from .world_model import WorldModel
from .nlu.word_cues import has_word
from .nlu.phrases import words as phrase_words

_RESPONSE_GENERATOR = ResponseGenerator()

_OUTCOME_ERROR_MAP = {
    ParseFailureReason.UNKNOWN_ENTITY: NluError.ENTITY_NOT_FOUND,
    ParseFailureReason.UNKNOWN_LOCATION: NluError.AREA_NOT_FOUND,
    ParseFailureReason.AMBIGUOUS_TARGET: NluError.AMBIGUOUS_ENTITY,
    ParseFailureReason.UNSUPPORTED_PROPERTY: NluError.INVALID_PARAMETER,
    ParseFailureReason.UNSUPPORTED_CAPABILITY: NluError.UNSUPPORTED_CAPABILITY,
    ParseFailureReason.INVALID_VALUE: NluError.INVALID_PARAMETER,
    ParseFailureReason.INCOMPLETE_REQUEST: NluError.MISSING_PARAMETER,
}


def _nlu_error_for_outcome(outcome: UnderstandingOutcome[Any]) -> NluError:
    """Map a canonical non-action outcome onto the compact public error API."""
    if outcome.kind is UnderstandingKind.AMBIGUOUS:
        return NluError.AMBIGUOUS_ENTITY
    return (
        _OUTCOME_ERROR_MAP.get(outcome.reason, NluError.NO_MATCH)
        if outcome.reason is not None
        else NluError.NO_MATCH
    )

INTENTS_DIR = Path(__file__).parent / "intents" / "de"
AUTOMATION_TRIGGER_DIR = INTENTS_DIR / "automation_trigger"
AUTOMATION_CONDITION_DIR = INTENTS_DIR / "automation_condition"
AUTOMATION_ACTION_DIR = INTENTS_DIR / "automation_action"
AUTOMATION_QUERY_DIR = INTENTS_DIR / "automation_query"
AUTOMATION_DELETE_DIR = INTENTS_DIR / "automation_delete"
AUTOMATION_TOGGLE_DIR = INTENTS_DIR / "automation_toggle"
RELATIVE_TIME_DIR = INTENTS_DIR / "relative_time"


# Intents NluEngine.match_query_followup() will continue with a fresh
# area/floor ("Und in der Küche?", "Und oben?") - HassGetState (original,
# v4.9) plus the three StateQueryParser intents (HomeIntent v4.2.1 plan,
# Phase 8). Deliberately excludes HassQueryComparison - see that method's
# docstring.
_QUERY_FOLLOWUP_INTENTS = frozenset(
    {"HassGetState", "HassLocationPropertyQuery", "HassStateQuery", "HassCheckState", "HassEntityStateQuery", "HassExistsQuery", "HassDeviceQuery"}
)

# Sentences containing "Prozent" are routed to PercentageParser's separately-
# compiled grammar for the same reason as the quantifier routing below: the
# existing {name}-wildcard close-cover sentence ("fahre {name} runter")
# would otherwise structurally swallow "Rolllade im Büro auf 30 Prozent" as
# {name}. Checked after normalize() so a "%" symbol (normalized to the word
# "Prozent" - see nlu/normalize.py) is routed correctly too.
_PERCENT_RE = re.compile(r"\bprozent\b", re.IGNORECASE)

# Bare "auf 50" with no unit word at all (HomeIntent plan V4.2, "Number
# Normalization" - "50" must mean the same as "50 Prozent") also needs
# routing here, since without a "Prozent"/"%" anywhere in the sentence
# _PERCENT_RE above never fires. Checked *after* the climate/fan regexes
# below (never before this point in _select_parser) so "auf 21 Grad"/"auf
# Stufe 3" keep going to their own grammars first - this only catches a
# digit sitting directly after "auf" with nothing else in between, which
# "auf Stufe 3" and "auf 21 Grad" don't (there's always a word between "auf"
# and the digit, or a unit word after it, in those two).
_BARE_PERCENT_RE = re.compile(r"\bauf\s+\d{1,3}\b")

# Fixed natural-language cover position: "halb (runter/hoch)" and "zur
# Hälfte" mean an absolute 50 percent position. Routed to PercentageParser
# so single and quantified room/floor targets share the same safe resolver.
_HALF_POSITION_RE = re.compile(r"\b(halb|hälfte)\b", re.IGNORECASE)

# Wave 13 ("Relative-Zeit-Automationen") - the same cheap, collision-free
# pre-check scaffold ``_AUTOMATION_TRIGGER_RE`` establishes for
# ``match_automation()``, gating ``match_relative_time_automation()`` so an
# ordinary command/query sentence never pays for a third hassil pass. Matches
# "in <word> Minuten/Sekunden/Stunden" anywhere in the sentence - <word> is
# deliberately a bare ``\S+`` (not ``\d+``) so spelled-out amounts ("in fünf
# Minuten") aren't missed; the full grammar (relative_time_command.yaml)
# still does the real, exhaustive matching, this only decides whether it's
# worth trying.
_RELATIVE_TIME_RE = re.compile(r"\bin\s+\S+\s+(sekunden?|minuten?|stunden?)\b", re.IGNORECASE)
_CALENDAR_TIME_RE = re.compile(
    r"\b(heute|morgen|übermorgen)\b|\b(?:am\s+)?(?:nächsten?|kommenden?)\s+werktag\b|"
    r"\b(?:am|nächsten?|kommenden?)\s+"
    r"(montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonnabend|sonntag)\b|"
    r"\bam\s+\d{1,2}\.(?=\s)",
    re.IGNORECASE,
)

# Anything that schedules or conditions a notification keeps it out of the
# immediate-delivery path.
_IMMEDIATE_NOTIFICATION_BLOCKER_RE = re.compile(
    r"\b(?:wenn|sobald|falls|nachdem|bevor|sofern|um\s+\d|später|spaeter|"
    r"nachher|jeden|jede|jedes|täglich|taeglich|abends|morgens|mittags|nachts|"
    r"sonnenaufgang|sonnenuntergang)\b",
    re.IGNORECASE,
)

_RECURRING_TIME_RE = re.compile(
    r"\b(?:jede[nr]?|an\s+jedem)\s+"
    r"(?:(?P<interval>zweiten?)\s+)?"
    r"(?P<day>werktag|montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)"
    r"(?:s|en)?\s+(?:um\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{1,2}))?\s*(?:uhr)?\b",
    re.IGNORECASE,
)
_PERSISTENT_STATE_RE = re.compile(
    r"\b(?P<amount>\d+|ein(?:e|en)?|zwei|drei|vier|fünf|fuenf|sechs|sieben|acht|neun|zehn)\s*"
    r"(?P<unit>sekunden?|minuten?|stunden?)\s+"
    r"(?P<state>offen|geöffnet|geschlossen|an|aus)\s+bleib(?:t|en)\b",
    re.IGNORECASE,
)
_REPEATED_EVENT_RE = re.compile(r"\b(?:zweimal|zwei\s*mal|2\s*mal)\b", re.IGNORECASE)
_WITHIN_WINDOW_RE = re.compile(
    r"\binnerhalb\s+von\s+"
    r"(?:\d+|ein(?:e|en|er)?|zwei|drei|vier|fünf|fuenf|sechs|sieben|acht|neun|zehn)\s*"
    r"(?:sekunden?|minuten?|stunden?)\b",
    re.IGNORECASE,
)
_RECURRING_WEEKDAYS = {
    "montag": ("mon",), "dienstag": ("tue",), "mittwoch": ("wed",),
    "donnerstag": ("thu",), "freitag": ("fri",), "samstag": ("sat",),
    "sonntag": ("sun",),
    "werktag": ("mon", "tue", "wed", "thu", "fri"),
}
_SMALL_NUMBERS = {
    "ein": 1, "eine": 1, "einen": 1, "zwei": 2, "drei": 3, "vier": 4,
    "fünf": 5, "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8,
    "neun": 9, "zehn": 10,
}


def _weekday_number(day: str) -> int:
    return {
        "montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3,
        "freitag": 4, "samstag": 5, "sonntag": 6,
    }.get(day, 0)


# "automation(en)"/"was schaltet"/"was steuert"/"warum ist"/"warum geht"
# routes to AutomationQueryParser's separately-compiled grammar (HomeIntent
# plan V5.29, "Automation Query") - checked as its own cheap pre-check
# (mirroring ``_AUTOMATION_TRIGGER_RE``'s own role one wave earlier) since
# this is what gates the real, blocking ``automations.yaml``/metadata-
# sidecar read in ``conversation.py`` before ``match_automation_query()``
# is even called - an ordinary command/query turn must never pay that I/O
# cost. "warum ist"/"warum geht" are deliberately narrow (not a bare
# "warum") - grepped empirically against every existing live grammar, no
# collision found.
_AUTOMATION_QUERY_RE = re.compile(
    r"\bautomation(en)?\b|\bwas schaltet\b|\bwas steuert\b|\bwarum ist\b|\bwarum geht\b", re.IGNORECASE
)

# "lösch(e)"/"entfern(e)" + "automation" routes to AutomationDeleteParser's
# separately-compiled grammar (HomeIntent plan V5.28, "Automation Deletion")
# - same cheap-pre-check role as ``_AUTOMATION_QUERY_RE`` one wave earlier,
# gating the real, blocking ``automations.yaml``/metadata-sidecar read in
# ``conversation.py`` before ``match_automation_delete()`` is even called.
# Grepped empirically against every existing intent YAML (see this module's
# git history) - no other grammar uses "lösch"/"entfern" for anything else.
_AUTOMATION_DELETE_RE = re.compile(r"\blösch\w*\b|\bentfern\w*\b", re.IGNORECASE)

# "deaktivier(e)"/"aktivier(e)" + "automation" route to AutomationToggleParser's
# separately-compiled grammar (HomeIntent plan V5.28 rest, Wave 11
# "Automation Disable/Enable") - same cheap-pre-check role as
# ``_AUTOMATION_DELETE_RE`` above. Two separate regexes (not one with an
# optional "de" prefix) because each gates a different one of
# ``match_automation_disable()``/``match_automation_enable()`` - the German
# word boundary (``\b``) already keeps "deaktiviere" from matching
# ``_AUTOMATION_ENABLE_RE`` (see AutomationToggleParser's own docstring).
# Grepped empirically against every existing intent YAML - no other grammar
# uses "aktivier"/"deaktivier" for anything else.
_AUTOMATION_DISABLE_RE = re.compile(r"\bdeaktivier\w*\b", re.IGNORECASE)
_AUTOMATION_ENABLE_RE = re.compile(r"\baktivier\w*\b", re.IGNORECASE)

# "einmalig(e)"/"nur einmal" marks a fire-once automation (new feature, Wave
# 12 "Einmalige Automation") - matched and *stripped* from the normalized
# text inside ``match_automation()`` itself (not a separate pre-check gate
# like the regexes above, since this qualifier can appear anywhere in an
# otherwise ordinary trigger+action sentence, not just at the very start).
# Verified collision-free: "einmal"/"einmalig" has zero existing usages
# anywhere in intents/lexicon/parsers/engine, and ``normalize()``'s filler-
# word stripping (``\b(ein bisschen|mal|doch|kurz|etwas)\b``) has no word
# boundary at "mal" inside "einmal"/"einmalig", so it never fires on this
# qualifier by accident.
_AUTOMATION_ONCE_RE = re.compile(r"\beinmalig\w*\b|\bnur einmal\b", re.IGNORECASE)
_AUTOMATION_REPEAT_RE = re.compile(
    r"\bnur\s+(?P<separate>zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|[2-9]|10)\s*mal\b"
    r"|\b(?P<joined>zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn)mal\b",
    re.IGNORECASE,
)
_REPEAT_COUNTS = {
    "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9, "zehn": 10,
}


def _clarification_question(clarification: ClarificationRequest) -> str:
    return render_candidate_question(clarification.candidates)


@dataclass(frozen=True)
class MatchResult:
    plan: ServiceCallPlan | None
    response_text: str
    # SemanticFrame representation of the same match, built by the parser
    # in parsers.py and used here to look up the ServiceCall spec (see
    # _build_match_result) - see Phase 1/2 of the v2 plan.
    frame: SemanticFrame | None = None
    # Fully-resolved counterpart to ``frame`` (real EntitySnapshot/AreaSnapshot
    # objects instead of text references) - see Phase 10 of the v2 plan.
    # Purely additive like ``frame`` was in Phase 1/2: plan/response_text
    # keep driving behaviour, no consumer reads this yet (Phase 11 Validator).
    command: SemanticCommand | None = None
    # Set instead of ``plan``/``command`` when the name resolved to more than
    # one tied candidate (v2 plan Phase 25, "Clarification") - ``plan`` is
    # ``None`` here too, but unlike a successful query (also ``plan=None``)
    # this means "ask the user, don't execute anything yet". Callers must
    # check ``clarification`` before falling back to the ``plan is None``
    # query-vs-action distinction ``conversation.py`` already made in Phase
    # 19/Query-Engine (see its module docstring).
    clarification: ClarificationRequest | None = None
    # ReasoningEngine's composed view of the same match (V6.25/V6.26).
    # ``_build_match_result`` uses it as a consistency gate for explicit,
    # singular targets before a service plan can be returned.
    # For a current-turn floor reference ("oben"/"unten") this can diverge
    # from ``command`` - ``SemanticFrame`` has no floor field, so
    # ``ReasoningEngine.resolve()`` can only pick up a floor from
    # ``ConversationContext.last_floor`` (a remembered turn), never from the
    # sentence just parsed. Known, documented, not a behaviour regression
    # since nothing reads this field yet.
    resolved_intent: ResolvedSemanticIntent | None = None
    # Read-only answers that do not originate from a SemanticCommand still
    # need conversational focus for pronouns and elliptical follow-ups.
    context_entities: tuple[EntitySnapshot, ...] = ()
    context_predicate: str | None = None
    explanation_text: str | None = None
    # An understood command that cannot be grounded honestly ("Im Büro gibt
    # es keinen Ventilator."). Never executable; spoken instead of the
    # generic "nicht verstanden".
    failure_text: str | None = None
    # Where the plan came from (explicit command, need, inferred routine);
    # read only by the execution policy.
    origin: PlanOrigin = PlanOrigin.EXPLICIT_COMMAND
    # Question form of an implicit need's action ("Soll ich …?"), used when
    # the policy turns it into a proposal (implicit_action_level).
    proposal_text: str | None = None
    # Routine binding (7.3.3): concept key, whether the target comes from a
    # confirmed binding, and the candidates a choice or "Ja" would bind.
    routine_key: str | None = None
    binding_confirmed: bool = False
    routine_candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandPlan:
    """An "und"-joined multi-step sentence ("Mach das Licht an und fahr die
    Rollläden hoch."), split and matched as N independent sub-commands
    (HomeIntent plan V4.8, "Multi-Step Commands").

    ``commands`` holds one fully-built ``MatchResult`` per segment, each
    already individually validated through the normal single-command
    pipeline ("Jeder Command einzeln validieren"). ``NluEngine.match()``
    only ever returns a ``CommandPlan`` once *every* segment has
    successfully matched, resolved, and validated - the plan's own
    "Standardmäßig für sicherheitskritische Aktionen: validate entire plan
    first -> execute only if complete plan is valid" default, applied by
    never constructing a partial ``CommandPlan``: one failing/ambiguous/
    non-actionable segment fails the whole sentence (``match()`` returns
    ``None``), same "never guess, no partial execution" precedent the rest
    of this engine already follows.

    Read-only query segments may be mixed with actions. Every segment is
    still resolved and validated before execution starts, and action/query
    segments that refer to the same entity are refused because the query
    would otherwise describe the pre-execution snapshot.
    """

    commands: tuple[MatchResult, ...]
    # Group operations over several device kinds or many targets are
    # previewed first; the plan runs only after an explicit "Ja".
    confirmation_text: str | None = None
    origin: PlanOrigin = PlanOrigin.EXPLICIT_COMMAND
    proposal_text: str | None = None
    routine_key: str | None = None
    binding_confirmed: bool = False
    routine_candidates: tuple[str, ...] = ()


def _mark_inferred_routines(
    payload: "MatchResult | CommandPlan | None",
    text: str,
    entities: list[EntitySnapshot],
) -> "MatchResult | CommandPlan | None":
    """Scripts/scenes found by name similarity become inferred routines."""
    by_id = {entity.entity_id: entity for entity in entities}

    def mark(result: MatchResult) -> MatchResult:
        plan = result.plan
        if plan is None or result.origin is not PlanOrigin.EXPLICIT_COMMAND:
            return result
        target_ids = (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
        routines = [
            by_id[entity_id] for entity_id in target_ids
            if entity_id.split(".", 1)[0] in {"script", "scene"} and entity_id in by_id
        ]
        if routines and not all(routine_named_explicitly(text, entity) for entity in routines):
            return replace(result, origin=PlanOrigin.INFERRED_ROUTINE)
        return result

    if isinstance(payload, MatchResult):
        return mark(payload)
    if isinstance(payload, CommandPlan):
        commands = tuple(mark(item) for item in payload.commands)
        if all(new is old for new, old in zip(commands, payload.commands)):
            return payload
        return replace(payload, commands=commands, origin=PlanOrigin.INFERRED_ROUTINE)
    return payload


# Sentinel: the genus model proved the legacy reading incomplete.
_REFUSED: Any = object()

_PREVIEW_VERBS = {
    "HassTurnOn": "einschalten",
    "HassTurnOff": "ausschalten",
    "HassToggle": "umschalten",
    "HassOpenCover": "öffnen",
    "HassCloseCover": "schließen",
    "HassOpenValve": "öffnen",
    "HassCloseValve": "schließen",
    "HassMediaPlay": "abspielen",
    "HassMediaPause": "pausieren",
    "HassMediaStop": "stoppen",
    "HassVacuumStart": "starten",
    "HassVacuumStop": "stoppen",
    "HassLightBrighten": "heller stellen",
    "HassLightDim": "dunkler stellen",
    "HassClimateIncreaseTemperature": "wärmer stellen",
    "HassClimateDecreaseTemperature": "kühler stellen",
}


def _preview_verb(item: MatchResult) -> str:
    frame = item.frame
    if frame is None:
        return "schalten"
    if frame.intent == REGISTERED_OPERATION_INTENT:
        return describe_registered_operation(
            frame.parameters.get("service_domain"),
            frame.parameters.get("service_name"),
            frame.parameters.get("service_data") or {},
        )
    if frame.intent == "HassSetPercentage":
        return f"auf {frame.parameters.get('percent')} Prozent stellen"
    if frame.intent == "HassClimateSetTemperature":
        return f"auf {frame.parameters.get('temperature'):g} Grad stellen"
    return _PREVIEW_VERBS.get(frame.intent, "schalten")


def _ontology_preview_text(results: Sequence[MatchResult], targets: str) -> str:
    """"Soll ich A, B und C ausschalten?" for a previewed group operation."""
    verbs: list[str] = []
    for item in results:
        verb = _preview_verb(item)
        if verb not in verbs:
            verbs.append(verb)
    return f"Soll ich {targets} {' bzw. '.join(verbs)}?"


def _no_automation_text(entity: EntitySnapshot | None) -> str:
    if entity is None:
        return "Ich habe keine passende Automation gefunden."
    return f"Ich habe keine Automation gefunden, die {entity.friendly_name} steuert."


def _several_automations_text(entity: EntitySnapshot | None) -> str:
    if entity is None:
        return "Es gibt mehrere Automationen mit diesem Namen."
    return f"Es gibt mehrere Automationen, die {entity.friendly_name} steuern."


def _unknown_exclusions(
    names: tuple[str, ...], entities: list[EntitySnapshot]
) -> tuple[str, ...]:
    """Excluded names that match no registry name or alias at all (R7)."""
    known = {
        normalize_for_compare(name).removeprefix("der ").removeprefix("die ").removeprefix("das ")
        for entity in entities
        for name in (entity.friendly_name, *entity.aliases)
    }
    unknown: list[str] = []
    for name in names:
        words = normalize_for_compare(name).split()
        while words and words[0] in {"der", "die", "das", "den", "dem"}:
            words.pop(0)
        key = " ".join(words)
        if key and not any(key == item or key in item.split() or item in key for item in known):
            spoken = " ".join(name.split()[len(name.split()) - len(words):])
            unknown.append(spoken[:1].upper() + spoken[1:])
    return tuple(unknown)


def _exclusions_outside_places(
    positive: str, names: tuple[str, ...], entities: list[EntitySnapshot]
) -> str | None:
    """"… in Küche und Flur, außer dem Nachtlicht" with the Nachtlicht elsewhere."""
    from .nlu.place_model import build_place_lexicon

    places = [
        mention.place
        for mention in build_place_lexicon(entities).scan(
            [token.canonical for token in tokenize_language(positive) if token.is_word]
        )
    ]
    if not places:
        return None
    outside: list[str] = []
    for name in names:
        key = normalize_for_compare(name)
        matches = [
            entity for entity in entities
            if normalize_for_compare(entity.friendly_name) == key
            or key in {normalize_for_compare(alias) for alias in entity.aliases}
        ]
        if len(matches) == 1 and not any(place.contains(matches[0]) for place in places):
            where = matches[0].area_name
            outside.append(f"„{matches[0].friendly_name}“" + (f" (im Bereich {where})" if where else ""))
    if not outside:
        return None
    return (
        f"Die Ausnahme {' und '.join(outside)} liegt nicht an den genannten Orten. "
        "Ich habe nichts ausgeführt."
    )


def _unresolved_exclusion_text(names: tuple[str, ...]) -> str:
    quoted = [f"„{name}“" for name in names]
    if len(quoted) == 1:
        subject = f"die Ausnahme {quoted[0]} nicht eindeutig"
    else:
        listed = f"{', '.join(quoted[:-1])} und {quoted[-1]}"
        subject = f"nicht alle Ausnahmen ({listed}) eindeutig"
    return (
        f"Ich konnte {subject} zuordnen und habe deshalb nichts geschaltet. "
        "Bitte nenne die Geräte genauer."
    )


# "Sag mir ... Bescheid" is a request, although "sag mir" also opens
# embedded questions ("Sag mir, ob ..."), which stay queries.
_SAY_REQUEST_RE = re.compile(
    r"^\s*(?:sag|sage|gib)\s+(?!.*\b(?:ob|wie|was|warum|wann|welche[rsmn]?|wer|wo|wieviel)\b)"
    r"(?=.*\bbescheid\b)[^?]*$",
    re.IGNORECASE,
)


# A polite modal request ("..., kannst du mir dann Bescheid sagen?") is a
# request even though it is phrased as a question; wh-questions never are.
_REPETITION_WORDS = frozenset({"mal", "male"})
_COUNT_WORDS = frozenset({
    "zwei", "drei", "vier", "fuenf", "sechs", "sieben", "acht", "neun", "zehn",
    "zwanzig", "hundert", "tausend",
})


def _repetition_count(text: str) -> str | None:
    """The spoken repetition count ("1000 Mal", "drei Mal"), if any."""
    tokens = phrase_words(text)
    for number, following in zip(tokens, tokens[1:]):
        if following.key in _REPETITION_WORDS and (
            number.key.isdigit() or number.key in _COUNT_WORDS
        ):
            return text[number.start:following.end]
    return None


def _writes(payload: object) -> bool:
    commands = getattr(payload, "commands", None)
    if commands is not None:
        return any(command.plan is not None for command in commands)
    return getattr(payload, "plan", None) is not None


# "Nein, ich meinte …" / "gemeint war …" before a corrected name or place.
_MEANT_PREFIX_RE = re.compile(r"^(?:nein[, ]+)?(?:ich\s+meinte|gemeint\s+war)\s+", re.IGNORECASE)
_MODAL_REQUEST_RE = re.compile(
    r"\b(?:kannst|könntest|koenntest|würdest|wuerdest)\s+du\s+(?:\S+\s+){0,4}?"
    r"(?:bescheid\s+(?:sagen|geben)|benachrichtigen|informieren|schicken|senden)\b",
    re.IGNORECASE,
)
_WH_QUESTION_RE = re.compile(
    r"^\s*(?:wie|was|warum|wieso|weshalb|wann|wer|wo|welche[rsmn]?|wohin|womit|ob)\b",
    re.IGNORECASE,
)
_NEGATED_NOTIFICATION_RE = re.compile(
    r"\b(?:benachrichtig\w*|informier\w*|schick\w*|send\w*|sag\w*|gib|meld\w*)\s+"
    r"(?:\S+\s+){0,2}?(?:nicht|nie|niemals|keine?[nmrs]?|bloß\s+nicht)\b"
    r"|\b(?:keine|kein)\s+(?:push[\s-]?)?(?:nachricht|benachrichtigung|meldung)\w*\b"
    r"|\bnicht\s+(?:mehr\s+)?(?:benachrichtigt|informiert)\b",
    re.IGNORECASE,
)


_NOTIFICATION_REQUEST_VERB_RE = re.compile(
    r"\b(?:benachrichtig\w*|informier\w*|schick\w*|send\w*|schreib\w*|sag\w*|gib|geb\w*|meld\w*|"
    r"ping\w*|mach\w*|kannst|könntest|koenntest|würdest|wuerdest|möchte|moechte|will|hätte|"
    r"haette|erinner\w*)\b",
    re.IGNORECASE,
)


def _ambiguous_kind_question(
    document: LanguageDocument, entities: list[EntitySnapshot]
) -> str | None:
    """"Stelle den Ventilator auf Stufe 3" with two fans: ask, naming them.

    Used only after every compiler failed, so it never replaces a result;
    it turns "nicht eindeutig" into the concrete question.
    """
    from .nlu.target_resolution import ResolutionOutcome, describe_with_residue, resolve_description

    descriptions, _residue = describe_with_residue(document.tokens, entities)
    for description in descriptions:
        if description.explicit or description.universal or description.quantity is not Quantity.ONE:
            continue
        resolution = resolve_description(description, entities)
        if resolution.outcome is ResolutionOutcome.AMBIGUOUS and 1 < len(resolution.entities) <= 8:
            from .nlu.entity_clarification import which_question

            return f"{which_question(resolution.entities)} Ich habe nichts ausgeführt."
    return None


def _names_its_target(
    document: LanguageDocument, entity_id: str, entities: list[EntitySnapshot]
) -> bool:
    """Whether the utterance names this device (name or alias), not a kind."""
    entity = next((item for item in entities if item.entity_id == entity_id), None)
    if entity is None:
        return True
    spoken = " " + " ".join(token.canonical for token in document.tokens if token.is_word) + " "
    names = (entity.friendly_name, *entity.aliases)
    return any(
        f" {normalize_for_compare(name)} " in spoken
        or normalize_for_compare(name).replace(" ", "") in spoken.replace(" ", "")
        for name in names
        if name
    )


class NluEngine:
    """Loads all intent YAML files once at construction; ``match()`` is
    stateless per call (entities are passed in fresh each time since HA
    entity state changes between conversation turns)."""

    def __init__(
        self,
        automation_trigger_dir: Path = AUTOMATION_TRIGGER_DIR,
        automation_condition_dir: Path = AUTOMATION_CONDITION_DIR,
        automation_action_dir: Path = AUTOMATION_ACTION_DIR,
        automation_query_dir: Path = AUTOMATION_QUERY_DIR,
        automation_delete_dir: Path = AUTOMATION_DELETE_DIR,
        automation_toggle_dir: Path = AUTOMATION_TOGGLE_DIR,
        relative_time_dir: Path = RELATIVE_TIME_DIR,
    ) -> None:

        automation_trigger_yaml_files = sorted(automation_trigger_dir.glob("*.yaml"))
        if not automation_trigger_yaml_files:
            raise FileNotFoundError(f"No intent YAML files found in {automation_trigger_dir}")
        automation_trigger_intents: Intents = Intents.from_files(automation_trigger_yaml_files)

        automation_condition_yaml_files = sorted(automation_condition_dir.glob("*.yaml"))
        if not automation_condition_yaml_files:
            raise FileNotFoundError(f"No intent YAML files found in {automation_condition_dir}")
        automation_condition_intents: Intents = Intents.from_files(automation_condition_yaml_files)

        automation_action_yaml_files = sorted(automation_action_dir.glob("*.yaml"))
        if not automation_action_yaml_files:
            raise FileNotFoundError(f"No intent YAML files found in {automation_action_dir}")
        automation_action_intents: Intents = Intents.from_files(automation_action_yaml_files)

        automation_query_yaml_files = sorted(automation_query_dir.glob("*.yaml"))
        if not automation_query_yaml_files:
            raise FileNotFoundError(f"No intent YAML files found in {automation_query_dir}")
        automation_query_intents: Intents = Intents.from_files(automation_query_yaml_files)

        automation_delete_yaml_files = sorted(automation_delete_dir.glob("*.yaml"))
        if not automation_delete_yaml_files:
            raise FileNotFoundError(f"No intent YAML files found in {automation_delete_dir}")
        automation_delete_intents: Intents = Intents.from_files(automation_delete_yaml_files)

        automation_toggle_yaml_files = sorted(automation_toggle_dir.glob("*.yaml"))
        if not automation_toggle_yaml_files:
            raise FileNotFoundError(f"No intent YAML files found in {automation_toggle_dir}")
        automation_toggle_intents: Intents = Intents.from_files(automation_toggle_yaml_files)

        relative_time_yaml_files = sorted(relative_time_dir.glob("*.yaml"))
        if not relative_time_yaml_files:
            raise FileNotFoundError(f"No intent YAML files found in {relative_time_dir}")
        relative_time_intents: Intents = Intents.from_files(relative_time_yaml_files)

        # Automation-Grammatiken/Parser (Integration Wave Migrationsschritt 1):
        # nur geladen und instanziiert, noch nicht an _select_parser()/match()
        # angeschlossen - reines Laden ohne Routing, Regressionsrisiko ≈ 0.
        self._automation_trigger_parser = AutomationTriggerParser(automation_trigger_intents)
        self._automation_condition_parser = AutomationConditionParser(automation_condition_intents)
        self._automation_action_parser = AutomationActionParser(
            automation_action_intents, self._automation_condition_parser
        )
        self._automation_query_parser = AutomationQueryParser(automation_query_intents)
        self._automation_delete_parser = AutomationDeleteParser(automation_delete_intents)
        self._automation_toggle_parser = AutomationToggleParser(automation_toggle_intents)
        self._relative_time_command_parser = RelativeTimeCommandParser(
            relative_time_intents, self._automation_action_parser
        )
        self._scheduled_time_command_parser = ScheduledTimeCommandParser(
            self._automation_action_parser
        )
        self._location_property_query_parser = LocationPropertyQueryParser()
        self._contextual_property_resolver = ContextualPropertyResolver()
        self._conversation_correction_resolver = ConversationCorrectionResolver()

    def match_correction_followup(
        self,
        text: str,
        entities: list[EntitySnapshot],
        context: ConversationContext | None,
    ) -> MatchResult | None:
        """Apply an explicit correction to the previous actionable turn."""
        outcome = self._conversation_correction_resolver.resolve(text, entities, context)
        if outcome is None:
            return None
        if isinstance(outcome, UnderstandingFeedback):
            return MatchResult(plan=None, response_text=outcome.speech)
        if isinstance(outcome, ClarificationRequest):
            return MatchResult(
                plan=None,
                response_text=_clarification_question(outcome),
                clarification=outcome,
            )
        return self._build_match_result(outcome, entities, context)


    def match(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
    ) -> MatchResult | CommandPlan | None:
        """Compatibility API: the payload of the canonical ``understand``."""
        return self.understand(text, entities, world_model).payload


    def understand(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        document: LanguageDocument | None = None,
        *,
        context: UnderstandingContext | None = None,
    ) -> UnderstandingOutcome[MatchResult | CommandPlan]:
        """Canonical result of one direct turn, with the plan's origin set."""
        outcome = self._understand(
            text, entities, world_model, document, context=context
        )
        payload = outcome.payload
        repeated = _repetition_count(text) if _writes(payload) else None
        if repeated is not None:
            # "Schalte das Licht 1000 Mal ein": the count is an instruction
            # the plan would silently drop. Never execute a reduced command.
            return replace(outcome, payload=MatchResult(
                plan=None,
                response_text=(
                    f"Wiederholtes Schalten („{repeated}“) führe ich nicht aus. "
                    "Ich habe nichts ausgeführt."
                ),
            ))
        marked = _mark_inferred_routines(payload, text, entities)
        if marked is payload:
            return outcome
        return replace(outcome, payload=marked)

    def _understand(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        document: LanguageDocument | None = None,
        *,
        context: UnderstandingContext | None = None,
    ) -> UnderstandingOutcome[MatchResult | CommandPlan]:
        """Return a canonical, reason-carrying result for one direct turn.

        The V8 language document and semantic interpreter are the sole
        production authority.  ``match()`` remains available only to the
        explicit, read-only shadow comparison so historical grammars can be
        measured without being able to create a production service plan.
        """
        supplied_document = document is not None
        document = document or analyse_language(
            text, entities, include_registry_compounds=False
        )
        interpreted = SemanticInterpreter.interpret(
            document,
            entities,
            world_model,
            compile_result=True,
            # The compiler below performs authoritative target resolution.
            # A second all-mentions scan only enriches shadow evidence and is
            # prohibitively expensive for large registries, so production
            # keeps it disabled while ``compare_understanding_pipelines()``
            # explicitly enables it.
            resolve_registry=False,
            context=context,
        )
        v7_result = self._interpreted_match_result(interpreted, entities)
        if v7_result is None and not supplied_document and has_locative_cue(text):
            expanded_document = analyse_language(text, entities)
            if any(
                variant.source == "registry_compound"
                for variant in expanded_document.variants
            ):
                document = expanded_document
                interpreted = SemanticInterpreter.interpret(
                    document,
                    entities,
                    world_model,
                    compile_result=True,
                    resolve_registry=False,
                    context=context,
                )
                v7_result = self._interpreted_match_result(interpreted, entities)
        # A shared-predicate compositional plan already proves that ``und``
        # joins targets. Re-running the independent-clause compiler would
        # rescan the registry for every segment and can become quadratic at
        # large registry sizes without changing the selected meaning.
        has_typed_query = (
            isinstance(interpreted.parse_result, ParseResult)
            and isinstance(
                interpreted.parse_result.frame.parameters.get("query_result"),
                QueryResult,
            )
        )
        predicate_clauses = (
            () if has_typed_query else independent_predicate_clauses(document)
        )
        selected_graph = next(
            (
                candidate.graph
                for candidate in interpreted.candidates
                if candidate.complete and candidate.graph is not None
            ),
            None,
        )
        projected_predicates = (
            project_independent_predicates(
                document, selected_graph, entities, world_model
            )
            if interpreted.compositional_plan is None
            and selected_graph is not None
            and predicate_clauses
            else ()
        )
        multi_result: CommandPlan | None = None
        if projected_predicates:
            rendered_items: list[MatchResult] = []
            for parsed in projected_predicates:
                rendered_item = self._build_match_result(parsed, entities)
                if rendered_item is not None and rendered_item.plan is not None:
                    rendered_items.append(rendered_item)
            rendered = tuple(rendered_items)
            if len(rendered) == len(projected_predicates):
                multi_result = CommandPlan(rendered)
        elif interpreted.compositional_plan is None:
            multi_result = self._semantic_multi_result(
                predicate_clauses, entities, world_model, context
            )
        if multi_result is not None:
            v7_result = multi_result
        elif (
            interpreted.compositional_plan is None and predicate_clauses
        ):
            # Never execute only the first half of an independently shaped
            # conjunction when another clause failed compilation.
            v7_result = None
        result: MatchResult | CommandPlan | None = v7_result
        authority = (
            UnderstandingAuthority.V8_SEMANTIC
            if v7_result is not None
            else UnderstandingAuthority.NONE
        )
        # The loss-aware frontend recognizes bounded safety-critical spelling
        # variants (especially a mistyped negation) that legacy grammars may
        # otherwise absorb into a wildcard entity name.  The canonical V8
        # boundary is authoritative for non-executability.
        if (
            document.utterance.speech_act is SpeechAct.COMMAND
            and not document.utterance.safe_to_execute_directly
        ):
            result = None
            authority = UnderstandingAuthority.NONE
        if document.utterance.modality is Modality.HYPOTHETICAL:
            # A counterfactual may contain executable-looking predicates, but
            # they describe a simulated world. Do not let ordinary state or
            # command compilers answer a different, literal question.
            result = None
            authority = UnderstandingAuthority.NONE
        if (
            document.utterance.speech_act is SpeechAct.COMMAND
            and document.temporal
            and (
                isinstance(result, CommandPlan)
                or isinstance(result, MatchResult) and result.plan is not None
            )
        ):
            # Scheduling/duration is a different domain projection from an
            # immediate service call. Conversation routes supported temporal
            # forms into AutomationModel first; this generic boundary must
            # never discard the time semantics and execute the remainder.
            result = None
            authority = UnderstandingAuthority.NONE
        ontology_result = self._ontology_understanding(
            document, entities, context, result
        )
        if ontology_result is _REFUSED:
            result = None
            authority = UnderstandingAuthority.NONE
        elif ontology_result is not None:
            result = ontology_result
            authority = UnderstandingAuthority.V8_SEMANTIC
        # The honest failure sentence knows the speaker's room (7.7.1 A5).
        self._feedback_source_area = context.source_area if context is not None else None
        try:
            return self._direct_understanding_outcome(
                text, document, interpreted, result, entities, authority
            )
        finally:
            self._feedback_source_area = None

    def understand_need(
        self,
        document: LanguageDocument,
        entities: list[EntitySnapshot],
        *,
        source_area_id: str | None = None,
        context_area_id: str | None = None,
        routine_bindings: Mapping[str, str] | None = None,
    ) -> MatchResult | CommandPlan | None:
        """Ground a need statement ("Mir ist kalt") in operations.

        Returns ``None`` when the turn states no need.  A ``MatchResult``
        without plan carries a spoken hint or question; a ``CommandPlan``
        with ``confirmation_text`` is a proposal.
        """
        utterance = document.utterance
        if utterance.speech_act in {
            SpeechAct.AUTOMATION, SpeechAct.CONFIRMATION, SpeechAct.CORRECTION,
        } or utterance.modality is Modality.HYPOTHETICAL:
            return None
        words = [token.canonical for token in document.tokens if token.is_word]
        meaning = interpret_need(
            words, question=document.source_text.rstrip().endswith("?")
        )
        if meaning is None:
            return None
        lexicon = build_place_lexicon(entities)
        mentions = lexicon.scan(words)
        place = next(
            (mention.place for mention in mentions if mention.place.kind is not PlaceKind.HERE),
            None,
        )
        if place is None:
            for area_id in (source_area_id, context_area_id):
                if area_id is not None:
                    place = lexicon.place_for_area(area_id)
                    if place is not None:
                        break
        if place is None and any(mention.place.kind is PlaceKind.HERE for mention in mentions):
            # "hier"/"da" only from the satellite's area or a place named in
            # the conversation; never guessed from the house (7.3.3, S6).
            return MatchResult(
                plan=None,
                response_text="In welchem Raum? Ohne Sprachsatellit weiß ich nicht, wo „hier“ ist.",
            )
        outcome = compile_need(
            meaning, entities, place, document.source_text, routine_bindings=routine_bindings
        )
        routine = {
            "routine_key": outcome.routine_key,
            "binding_confirmed": outcome.bound,
            "routine_candidates": tuple(item.entity_id for item in outcome.candidates),
        }
        if outcome.message is not None and not outcome.results:
            return MatchResult(plan=None, response_text=outcome.message, **routine)
        rendered: list[MatchResult] = []
        for parsed in outcome.results:
            item = self._build_match_result(parsed, entities)
            if item is None or item.plan is None:
                return None
            rendered.append(item)
        if not rendered:
            return None
        proposal = proposal_from_reason(outcome.reason)
        rendered = [replace(item, origin=outcome.origin, **routine) for item in rendered]
        if outcome.confirm is not None:
            return CommandPlan(
                tuple(rendered), confirmation_text=outcome.confirm, origin=outcome.origin,
                **routine,
            )
        if len(rendered) == 1:
            return replace(
                rendered[0],
                response_text=outcome.reason or rendered[0].response_text,
                proposal_text=proposal,
            )
        first, *rest = rendered
        return CommandPlan(
            (replace(first, response_text=outcome.reason or first.response_text),
             *(replace(item, response_text="") for item in rest)),
            origin=outcome.origin,
            proposal_text=proposal,
            **routine,
        )

    def understand_release(
        self,
        document: LanguageDocument,
        entities: list[EntitySnapshot],
        *,
        context: UnderstandingContext | None = None,
    ) -> MatchResult | CommandPlan | None:
        """"Die Stehlampe muss nicht an sein" -> switch the Stehlampe off."""
        frame = document.release
        if frame is None:
            return None
        compiled = compile_release(
            document, frame.object_text, frame.action, entities,
            source_area=context.source_area if context is not None else None,
        )
        if compiled is None:
            return None
        if compiled.message is not None:
            return MatchResult(plan=None, response_text=compiled.message, failure_text=compiled.message)
        if compiled.clarification is not None:
            return MatchResult(
                plan=None,
                response_text=_clarification_question(compiled.clarification),
                clarification=compiled.clarification,
            )
        rendered = [self._build_match_result(parsed, entities) for parsed in compiled.results]
        if not rendered or any(item is None or item.plan is None for item in rendered):
            return None
        items = tuple(item for item in rendered if item is not None)
        if compiled.preview is not None:
            return CommandPlan(items, confirmation_text=_ontology_preview_text(items, compiled.preview))
        return items[0] if len(items) == 1 else CommandPlan(items)

    def understand_discourse(
        self,
        document: LanguageDocument,
        entities: list[EntitySnapshot],
        context: ConversationContext | None,
        *,
        understanding: UnderstandingContext | None = None,
    ) -> MatchResult | CommandPlan | None:
        """Elliptical/referential command bound to the conversation context."""
        utterance = document.utterance
        if (
            utterance.speech_act in {SpeechAct.AUTOMATION, SpeechAct.CONFIRMATION}
            or utterance.modality in NON_EXECUTABLE_MODALITIES
            or utterance.polarity is not Polarity.POSITIVE
            or (
                document.source_text.rstrip().endswith("?")
                and not document.source_text.strip().casefold().startswith("und ")
            )
        ):
            return None
        compiled = compile_discourse(
            document, entities, context,
            source_area=understanding.source_area if understanding is not None else None,
        )
        if compiled is None:
            return None
        if compiled.message is not None:
            return MatchResult(plan=None, response_text=compiled.message, failure_text=compiled.message)
        if compiled.clarification is not None:
            return MatchResult(
                plan=None,
                response_text=_clarification_question(compiled.clarification),
                clarification=compiled.clarification,
            )
        # "Und im Bad?" after an action repeats that action; its question
        # mark is prosody of the ellipsis, not a request for information.
        results = [
            replace(parsed, frame=replace(
                parsed.frame, source_text=parsed.frame.source_text.rstrip(" ?") + "."
            ))
            for parsed in compiled.results
        ]
        rendered = [self._build_match_result(parsed, entities) for parsed in results]
        if not rendered or any(item is None or item.plan is None for item in rendered):
            return None
        items = tuple(item for item in rendered if item is not None)
        if compiled.preview is not None:
            return CommandPlan(items, confirmation_text=_ontology_preview_text(items, compiled.preview))
        return items[0] if len(items) == 1 else CommandPlan(items)

    def _ontology_failure(
        self, text: str, entities: list[EntitySnapshot]
    ) -> str | None:
        """Honest sentence for a command the genus model cannot ground."""
        document = analyse_language(text, entities, include_registry_compounds=False)
        if document.utterance.speech_act is not SpeechAct.COMMAND:
            return None
        if not document.utterance.safe_to_execute_directly:
            # Asking which device is meant is safe for any command shape.
            return _ambiguous_kind_question(document, entities) if document.temporal else None
        compiled = compile_ontology_command(
            document, entities, source_area=getattr(self, "_feedback_source_area", None)
        )
        if compiled is not None and compiled.message is not None:
            return compiled.message
        return _ambiguous_kind_question(document, entities)

    def _ontology_understanding(
        self,
        document: LanguageDocument,
        entities: list[EntitySnapshot],
        context: UnderstandingContext | None,
        legacy: MatchResult | CommandPlan | None,
    ) -> MatchResult | CommandPlan | None:
        """Genus/place/quantity understanding in the shared direct path.

        The established compilers keep authority for everything they
        resolve.  The ontology compiler answers when they found nothing,
        and takes precedence where their result is provably incomplete:
        an unspecific "alles" (only the ontology knows which kinds belong
        to it) and coordinated clauses of which the legacy result dropped
        some (finding S3: never lose a clause silently).
        """
        utterance = document.utterance
        if (
            utterance.speech_act is not SpeechAct.COMMAND
            or not utterance.safe_to_execute_directly
            or document.temporal
        ):
            return None
        universal = any(
            (analysis := analyse_word(token.canonical)) is not None
            and analysis.genera == ("device",)
            and not analysis.indefinite
            for token in document.tokens
            if token.is_word
        )
        coordinated = any(
            token.canonical in {",", "und", "sowie"} for token in document.tokens
        )
        if (
            isinstance(legacy, MatchResult)
            and legacy.plan is not None
            and isinstance(legacy.plan.entity_id, str)
            and not universal
            and not coordinated
            and _names_its_target(document, legacy.plan.entity_id, entities)
        ):
            # One grounded single-target command that names its device
            # cannot be too broad and cannot have dropped a clause.
            return None
        compiled = compile_ontology_command(
            document,
            entities,
            source_area=context.source_area if context is not None else None,
        )
        if compiled is None:
            return None
        if (
            compiled.clarification is not None
            and isinstance(legacy, MatchResult)
            and legacy.plan is not None
            and isinstance(legacy.plan.entity_id, str)
            and not coordinated
        ):
            # A singular kind word ("die Lampe im Büro") with several
            # members at the place is ambiguous; the legacy pick of one of
            # them would be a guess.  Ask instead.
            return MatchResult(
                plan=None,
                response_text=_clarification_question(compiled.clarification),
                clarification=compiled.clarification,
            )
        if legacy is not None and not universal:
            legacy_commands = (
                legacy.commands if isinstance(legacy, CommandPlan) else (legacy,)
            )
            legacy_targets = {
                entity_id
                for command in legacy_commands
                if command.plan is not None
                for entity_id in (
                    (command.plan.entity_id,)
                    if isinstance(command.plan.entity_id, str)
                    else tuple(command.plan.entity_id)
                )
            }
            compiled_targets = {
                entity.entity_id
                for parsed in compiled.results
                for entity in parsed.resolved_entities
            }
            drops_clause = (
                compiled.clauses > 1
                and len(compiled.results) > sum(
                    1 for command in legacy_commands if command.plan is not None
                )
            )
            # A legacy domain word ("Rollos" -> every cover) may reach
            # devices outside the spoken genus (garage door, awning).  The
            # genus reading is then strictly narrower and wins.
            too_broad = bool(
                compiled_targets
                and legacy_targets
                and compiled_targets < legacy_targets
            )
            # Disjoint readings ("die Lichter unten": legacy basement, the
            # house's own floor alias says ground floor) cannot both be
            # right; the registry-grounded place model decides.
            disagrees = bool(
                compiled_targets and legacy_targets
                and not compiled_targets & legacy_targets
            )
            too_broad = too_broad or disagrees
            if (
                compiled.message is not None
                and compiled.clauses > 1
                and len(legacy_commands) < compiled.clauses
            ):
                # The legacy reading covers fewer clauses than were spoken:
                # refuse instead of executing a subset (finding S3).  The
                # honest sentence is spoken via understanding_feedback().
                return _REFUSED
            if not (compiled.executable and (drops_clause or too_broad)):
                return None
        if compiled.message is not None:
            # Honest non-results are spoken through understanding_feedback();
            # the direct outcome itself stays "no payload".
            return None
        if compiled.clarification is not None:
            return MatchResult(
                plan=None,
                response_text=_clarification_question(compiled.clarification),
                clarification=compiled.clarification,
            )
        rendered: list[MatchResult] = []
        for parsed in compiled.results:
            item = self._build_match_result(parsed, entities)
            if item is None or item.plan is None:
                return None
            rendered.append(item)
        if not rendered:
            return None
        if compiled.preview is not None:
            kept = (
                " Unverändert bleiben: "
                + join_german([entity.friendly_name for entity in compiled.kept])
                + "."
                if compiled.kept else ""
            )
            return CommandPlan(
                tuple(rendered),
                confirmation_text=_ontology_preview_text(rendered, compiled.preview) + kept,
            )
        if len(rendered) == 1:
            return rendered[0]
        return CommandPlan(tuple(rendered))

    def _semantic_multi_result(
        self,
        segments: tuple[str, ...],
        entities: list[EntitySnapshot],
        world_model: WorldModel | None,
        context: UnderstandingContext | None = None,
    ) -> CommandPlan | None:
        """Compile structurally proven independent conjunction clauses."""
        if len(segments) < 2:
            return None
        results: list[MatchResult] = []
        for segment in segments:
            document = analyse_language(segment, entities)
            if document.utterance.speech_act not in {SpeechAct.COMMAND, SpeechAct.QUERY}:
                return None
            if (
                document.utterance.speech_act is SpeechAct.COMMAND
                and not document.utterance.safe_to_execute_directly
            ):
                return None
            interpreted = SemanticInterpreter.interpret(
                document,
                entities,
                world_model,
                compile_result=True,
                resolve_registry=False,
                context=context,
            )
            result = self._interpreted_match_result(interpreted, entities)
            if not isinstance(result, MatchResult) or result.command is None or result.clarification is not None:
                return None
            if result.plan is None and document.utterance.speech_act is not SpeechAct.QUERY:
                return None
            if result.plan is not None and document.utterance.speech_act is SpeechAct.QUERY:
                return None
            results.append(result)
        changed_ids: set[str] = set()
        for result in results:
            command = result.command
            if command is None:
                return None
            ids = {entity.entity_id for entity in command.entities}
            if result.plan is None and changed_ids & ids:
                return None
            if result.plan is not None:
                changed_ids.update(ids)
        return CommandPlan(tuple(results))


    def _interpreted_match_result(
        self,
        interpreted: InterpreterResult,
        entities: list[EntitySnapshot],
    ) -> MatchResult | CommandPlan | None:
        """Turn the interpreter parse into the same validated payload shape."""
        if interpreted.compositional_plan is not None:
            if not isinstance(interpreted.parse_result, ParseResult):
                return None
            template = interpreted.parse_result
            rendered: list[MatchResult] = []
            for selected in interpreted.compositional_plan.targets:
                # The interpreter already compiled the shared predicate and
                # all targets were resolved by the canonical compositional
                # matcher. Project the validated frame mechanically instead
                # of rescanning the complete registry once per target.
                parsed = replace(
                    template,
                    frame=replace(
                        template.frame,
                        target=TargetReference(
                            selected.friendly_name,
                            selected.entity_id,
                            selected.domain,
                            selected.device_class,
                        ),
                        source_text=interpreted.compositional_plan.source_text,
                    ),
                    resolved_entities=[selected],
                )
                result = self._build_match_result(parsed, entities)
                if result is None or result.plan is None:
                    return None
                rendered.append(result)
            return CommandPlan(tuple(rendered))
        if isinstance(interpreted.parse_result, ParseResult):
            if interpreted.parse_result.response_text is not None:
                if isinstance(
                    interpreted.parse_result.frame.parameters.get("query_result"),
                    QueryResult,
                ):
                    built = NluEngine._build_match_result(
                        interpreted.parse_result, entities
                    )
                    if built is not None:
                        return replace(
                            built,
                            response_text=interpreted.parse_result.response_text,
                            explanation_text=(
                                interpreted.parse_result.explanation_text
                                or built.explanation_text
                            ),
                        )
                return MatchResult(
                    plan=None,
                    response_text=interpreted.parse_result.response_text,
                    context_entities=tuple(interpreted.parse_result.resolved_entities),
                    context_predicate=interpreted.parse_result.context_predicate,
                    explanation_text=interpreted.parse_result.explanation_text,
                )
            return NluEngine._build_match_result(interpreted.parse_result, entities)
        if isinstance(interpreted.parse_result, ClarificationRequest):
            return MatchResult(
                plan=None,
                response_text=_clarification_question(interpreted.parse_result),
                clarification=interpreted.parse_result,
            )
        return None

    @staticmethod
    def _candidate_margin(interpreted: InterpreterResult) -> float | None:
        complete = tuple(
            candidate for candidate in interpreted.candidates if candidate.complete
        )
        if len(complete) < 2:
            return None
        return complete[0].score - complete[1].score

    @staticmethod
    def _selected_evidence(
        interpreted: InterpreterResult,
    ) -> tuple[UnderstandingEvidence, ...]:
        """Evidence for the best complete hypothesis, or best failed one."""
        selected = next(
            (candidate for candidate in interpreted.candidates if candidate.complete),
            interpreted.candidates[0] if interpreted.candidates else None,
        )
        return selected.evidence if selected is not None else ()

    def _direct_understanding_outcome(
        self,
        text: str,
        document: LanguageDocument,
        interpreted: InterpreterResult,
        result: MatchResult | CommandPlan | None,
        entities: list[EntitySnapshot],
        authority: UnderstandingAuthority,
    ) -> UnderstandingOutcome[MatchResult | CommandPlan]:
        """Render one direct pipeline result as the canonical V8 outcome."""
        route = type(result).__name__ if result is not None else "no_match"
        margin = self._candidate_margin(interpreted)
        corrections = (
            (interpreted.selected_variant.source,)
            if interpreted.selected_variant is not None
            and interpreted.selected_variant.source != "original"
            else ()
        )
        evidence = self._selected_evidence(interpreted)

        if (
            document.utterance.pragmatic_disposition
            is PragmaticDisposition.ASK_BEFORE_ACTION
        ):
            return UnderstandingOutcome(
                kind=UnderstandingKind.CLARIFICATION,
                source_text=text,
                normalized_text=document.utterance.normalized_text,
                speech_act=document.utterance.speech_act,
                reason=ParseFailureReason.INCOMPLETE_REQUEST,
                speech="Möchtest du, dass ich daraus eine konkrete Änderung ableite?",
                candidates=interpreted.candidates,
                evidence=evidence,
                unexplained_tokens=document.semantics.unexplained_tokens,
                route="pragmatic_clarification",
                authority=UnderstandingAuthority.V8_SEMANTIC,
                margin=margin,
            )
        if (
            result is None
            and document.utterance.pragmatic_disposition
            is PragmaticDisposition.READ_ONLY
            and document.utterance.modality is Modality.HYPOTHETICAL
        ):
            return UnderstandingOutcome(
                kind=UnderstandingKind.QUERY,
                source_text=text,
                normalized_text=document.utterance.normalized_text,
                speech_act=document.utterance.speech_act,
                reason=ParseFailureReason.UNSUPPORTED_PROPERTY,
                speech="Die hypothetische Aussage wurde nur lesend verstanden; daraus wird keine Aktion ausgeführt.",
                candidates=interpreted.candidates,
                evidence=evidence,
                unexplained_tokens=document.semantics.unexplained_tokens,
                route="read_only_hypothetical",
                authority=UnderstandingAuthority.V8_SEMANTIC,
                margin=margin,
            )

        if isinstance(result, CommandPlan):
            has_action = any(command.plan is not None for command in result.commands)
            return UnderstandingOutcome(
                kind=(UnderstandingKind.COMMAND if has_action else UnderstandingKind.QUERY),
                source_text=text,
                normalized_text=document.utterance.normalized_text,
                speech_act=document.utterance.speech_act,
                payload=result,
                candidates=interpreted.candidates,
                evidence=evidence,
                unexplained_tokens=document.semantics.unexplained_tokens,
                corrections=corrections,
                route=route,
                authority=authority,
                margin=margin,
            )

        if isinstance(result, MatchResult):
            if result.clarification is not None:
                return UnderstandingOutcome(
                    kind=(
                        UnderstandingKind.AMBIGUOUS
                        if len(result.clarification.candidates) > 1
                        else UnderstandingKind.CLARIFICATION
                    ),
                    source_text=text,
                    normalized_text=document.utterance.normalized_text,
                    speech_act=document.utterance.speech_act,
                    payload=result,
                    candidates=interpreted.candidates,
                    evidence=evidence,
                    reason=ParseFailureReason.AMBIGUOUS_TARGET,
                    speech=result.response_text,
                    unexplained_tokens=document.semantics.unexplained_tokens,
                    corrections=corrections,
                    route=route,
                    authority=authority,
                    margin=margin,
                )
            reasoning_result = (
                result.frame.parameters.get("query_result")
                if result.frame is not None
                else None
            )
            if (
                isinstance(reasoning_result, QueryResult)
                and reasoning_result.status is QueryResultStatus.AMBIGUOUS
            ):
                return UnderstandingOutcome(
                    kind=UnderstandingKind.AMBIGUOUS,
                    source_text=text,
                    normalized_text=document.utterance.normalized_text,
                    speech_act=document.utterance.speech_act,
                    payload=result,
                    candidates=interpreted.candidates,
                    evidence=evidence,
                    reason=ParseFailureReason.AMBIGUOUS_TARGET,
                    speech=result.response_text,
                    unexplained_tokens=document.semantics.unexplained_tokens,
                    corrections=corrections,
                    route=result.frame.intent if result.frame is not None else None,
                    authority=authority,
                    margin=margin,
                )
            if (
                isinstance(reasoning_result, QueryResult)
                and reasoning_result.status is QueryResultStatus.UNSUPPORTED
            ):
                return UnderstandingOutcome(
                    kind=UnderstandingKind.UNSUPPORTED,
                    source_text=text,
                    normalized_text=document.utterance.normalized_text,
                    speech_act=document.utterance.speech_act,
                    payload=result,
                    candidates=interpreted.candidates,
                    evidence=evidence,
                    reason=ParseFailureReason.UNSUPPORTED_PROPERTY,
                    speech="Dafür fehlen mir vollständige Zustands- oder Verlaufsdaten.",
                    unexplained_tokens=document.semantics.unexplained_tokens,
                    corrections=corrections,
                    route=result.frame.intent if result.frame is not None else None,
                    authority=authority,
                    margin=margin,
                )
            return UnderstandingOutcome(
                kind=(
                    UnderstandingKind.COMMAND
                    if result.plan is not None
                    else UnderstandingKind.QUERY
                ),
                source_text=text,
                normalized_text=document.utterance.normalized_text,
                speech_act=document.utterance.speech_act,
                payload=result,
                candidates=interpreted.candidates,
                evidence=evidence,
                speech=result.response_text,
                unexplained_tokens=document.semantics.unexplained_tokens,
                corrections=corrections,
                route=(result.frame.intent if result.frame is not None else route),
                authority=authority,
                margin=margin,
            )

        unsafe = (
            document.utterance.speech_act is SpeechAct.COMMAND
            and not document.utterance.safe_to_execute_directly
        )
        unbound_semantic_query = (
            document.utterance.speech_act is SpeechAct.QUERY
            and len(document.structure.clauses) > 1
            and not document.structure.relations
        )
        if unsafe:
            # Safety classification is already authoritative and does not
            # benefit from fuzzy target lookup. In particular, a repair or
            # negated command must not pay for (or be reinterpreted by) a
            # legacy registry feedback scan.
            feedback = None
        elif document.temporal and document.utterance.speech_act is SpeechAct.COMMAND:
            feedback = UnderstandingFeedback(
                ParseFailureReason.UNSUPPORTED_PROPERTY,
                "Die Zeitangabe wurde erkannt, ist in diesem direkten Pfad aber nicht sicher ausführbar.",
            )
        elif (
            document.utterance.speech_act is SpeechAct.COMMAND
            and has_exclusion_clause(document.utterance.normalized_text)
            and split_exclusion(document.utterance.normalized_text)[1]
        ):
            # An exception that cannot be resolved stops the whole command
            # with a clear reason - never a broader execution (F5).
            feedback = UnderstandingFeedback(
                ParseFailureReason.UNKNOWN_ENTITY,
                _unresolved_exclusion_text(
                    split_exclusion(document.utterance.normalized_text)[1]
                ),
            )
        elif unbound_semantic_query:
            feedback = UnderstandingFeedback(
                ParseFailureReason.UNSUPPORTED_PROPERTY,
                "Ich habe die Frage erkannt, kann die Beziehungen zwischen "
                "ihren Teilen aber noch nicht sicher auswerten.",
            )
        elif is_contextual_followup(text):
            # ConversationContext owns contextual grounding. A context-free
            # miss must not trigger an exhaustive fuzzy registry scan.
            feedback = None
        else:
            feedback = self.understanding_feedback(text, entities)
        return UnderstandingOutcome(
            kind=(
                UnderstandingKind.UNSAFE
                if unsafe
                else UnderstandingKind.AMBIGUOUS
                if interpreted.semantic_ambiguity
                else UnderstandingKind.UNSUPPORTED
            ),
            source_text=text,
            normalized_text=document.utterance.normalized_text,
            speech_act=document.utterance.speech_act,
            reason=(
                ParseFailureReason.UNSAFE_INFERENCE
                if unsafe
                else ParseFailureReason.AMBIGUOUS_MEANING
                if interpreted.semantic_ambiguity
                else feedback.reason if feedback is not None
                else ParseFailureReason.NO_GRAMMAR
            ),
            speech=feedback.speech if feedback is not None else None,
            candidates=interpreted.candidates,
            evidence=evidence,
            unexplained_tokens=document.semantics.unexplained_tokens,
            corrections=corrections,
            route=route,
            authority=authority,
            margin=margin,
        )

    def understand_automation(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        context: ConversationContext | None = None,
        document: LanguageDocument | None = None,
    ) -> UnderstandingOutcome[AutomationMatchResult | AutomationClarificationResult]:
        """Canonical V8 boundary for a trigger/condition/action turn."""
        document = document or analyse_language(text, entities)
        result = self.match_automation(text, entities, world_model, context)
        interpreted = SemanticInterpreter.interpret(
            document,
            entities,
            world_model,
            compile_result=False,
            resolve_registry=result is None,
        )
        if result is not None:
            return UnderstandingOutcome(
                kind=UnderstandingKind.AUTOMATION,
                source_text=text,
                normalized_text=document.utterance.normalized_text,
                speech_act=document.utterance.speech_act,
                payload=result,
                candidates=interpreted.candidates,
                unexplained_tokens=document.semantics.unexplained_tokens,
                route="automation",
            )
        return UnderstandingOutcome(
            kind=(
                UnderstandingKind.UNSAFE
                if document.utterance.speech_act is SpeechAct.AUTOMATION
                and not document.utterance.safe_to_execute_directly
                and document.utterance.polarity.name == "NEGATIVE"
                else UnderstandingKind.UNSUPPORTED
            ),
            source_text=text,
            normalized_text=document.utterance.normalized_text,
            speech_act=document.utterance.speech_act,
            reason=ParseFailureReason.INCOMPLETE_REQUEST,
            candidates=interpreted.candidates,
            unexplained_tokens=document.semantics.unexplained_tokens,
            route="automation_no_match",
        )


    def failure_feedback(
        self,
        text: str,
        entities: list[EntitySnapshot] | None = None,
        *,
        source_area: AreaSnapshot | None = None,
    ) -> str | None:
        """Best-effort explanation after every deterministic parser failed."""
        self._feedback_source_area = source_area
        try:
            feedback = self.understanding_feedback(text, entities)
        finally:
            self._feedback_source_area = None
        return feedback.speech if feedback is not None else None

    def understanding_feedback(
        self, text: str, entities: list[EntitySnapshot] | None = None
    ) -> UnderstandingFeedback | None:
        """Return the structured counterpart of the spoken failure text."""
        if entities is not None and has_exclusion_clause(normalize(text)):
            positive, excluded_names = split_exclusion(normalize(text))
            unknown = _unknown_exclusions(excluded_names, entities)
            if unknown:
                names = " und ".join(f"„{name}“" for name in unknown)
                return UnderstandingFeedback(
                    ParseFailureReason.UNKNOWN_ENTITY,
                    f"Ich finde kein Gerät {names}. Ich habe nichts ausgeführt.",
                )
            elsewhere = _exclusions_outside_places(positive, excluded_names, entities)
            if elsewhere:
                return UnderstandingFeedback(ParseFailureReason.UNKNOWN_ENTITY, elsewhere)
        if entities is not None:
            honest = self._ontology_failure(text, entities)
            if honest is not None:
                return UnderstandingFeedback(ParseFailureReason.UNKNOWN_ENTITY, honest)
        if re.search(
            r"\b(?:heute|morgen|übermorgen|am\s+\S+)\s+"
            r"(?:gegen|irgendwann)\s+(?:früh|morgens|abends?|nachts)\b",
            text,
            re.IGNORECASE,
        ):
            return UnderstandingFeedback(
                ParseFailureReason.INVALID_VALUE,
                "Bitte nenne für diesen Auftrag eine genaue Uhrzeit.",
            )
        if entities is not None:
            location = self._location_property_query_parser.parse(normalize(text), entities)
            if isinstance(location, LocationQueryFeedback):
                return UnderstandingFeedback(location.reason, location.response_text)
            normalized = normalize(text)
            if (
                _PERCENT_RE.search(normalized)
                or _BARE_PERCENT_RE.search(normalized)
                or _HALF_POSITION_RE.search(normalized)
            ):
                percentage_feedback = group_percentage_feedback(normalized, entities)
                if percentage_feedback is not None:
                    return percentage_feedback
        if _AUTOMATION_TRIGGER_RE.search(text):
            return UnderstandingFeedback(
                ParseFailureReason.INCOMPLETE_REQUEST,
                "Ich konnte Trigger und Aktion der Automation nicht eindeutig erkennen.",
            )
        if re.search(
            r"\b(schalte|mach|fahre?|öffne|schließe|stelle?|setze|starte|aktiviere|drehe?|dimme?)\b",
            text,
            re.IGNORECASE,
        ):
            mentioned = all_mentioned_entities(text, entities or [])
            if not mentioned and entities:
                # A distinctive part of a registry name ("den LED-Streifen")
                # still names a found device: never claim that nothing was
                # found (or suggest checking the exposure) in that case (F11).
                partial = resolve_mentioned_target(
                    text, entities, frozenset(entity.domain for entity in entities)
                )
                candidates: tuple[EntitySnapshot, ...] = ()
                in_area = climate_in_named_area(text, entities)
                if partial.status is ResolutionStatus.RESOLVED and partial.entity is not None:
                    candidates = (partial.entity,)
                elif in_area is not None:
                    candidates = (in_area,)
                else:
                    area_words = {
                        word
                        for entity in entities
                        for name in (entity.area_name or "", *entity.area_aliases)
                        for word in normalize_for_compare(name).split()
                    }
                    spoken = {
                        normalize_for_compare(token)
                        for token in analyse_semantics(text).unexplained_tokens
                        if len(token) >= 3
                    } - area_words
                    candidates = tuple(
                        entity for entity in entities
                        if spoken
                        and spoken <= set(normalize_for_compare(entity.friendly_name).replace("-", " ").split())
                    )
                if len(candidates) == 1:
                    mentioned = list(candidates)
                elif 1 < len(candidates) <= 5:
                    names = ", ".join(entity.friendly_name for entity in candidates)
                    return UnderstandingFeedback(
                        ParseFailureReason.AMBIGUOUS_TARGET,
                        f"Ich habe mehrere passende Geräte gefunden ({names}), "
                        "aber die gewünschte Funktion nicht eindeutig zuordnen können. "
                        "Bitte nenne das Gerät genauer.",
                        {"entity_ids": tuple(entity.entity_id for entity in candidates)},
                    )
            if mentioned:
                return UnderstandingFeedback(
                    ParseFailureReason.UNSUPPORTED_CAPABILITY,
                    " ".join(
                        describe_abilities(entity.friendly_name, entity.capabilities)
                        for entity in mentioned[:3]
                    ),
                    {"entity_ids": tuple(entity.entity_id for entity in mentioned)},
                )
            return UnderstandingFeedback(
                ParseFailureReason.UNKNOWN_ENTITY,
                "Ich habe die Aktion erkannt, aber kein eindeutig passendes, "
                "für HomeIntent freigegebenes Gerät gefunden.",
            )
        if analyse_utterance(text).speech_act is SpeechAct.QUERY:
            if entities is not None and (empty := empty_comparison_answer(text, entities)) is not None:
                return UnderstandingFeedback(ParseFailureReason.UNSUPPORTED_PROPERTY, empty)
            return UnderstandingFeedback(
                ParseFailureReason.UNSUPPORTED_PROPERTY,
                "Ich habe die Frage erkannt, aber die gewünschte Eigenschaft oder das Ziel nicht gefunden.",
            )
        return None


    def match_followup(self, text: str, context: ConversationContext | None) -> MatchResult | None:
        """Complete an elliptical follow-up sentence that omits its own
        target (v2 plan Phase 26, "Context"; cross-property extension
        HomeIntent plan V6.23, "Natural Context") - e.g. "Etwas heller."
        right after "Mach das Wohnzimmerlicht an.", or "Wärmer." right after
        a climate command. Tried by ``conversation.py`` *before* a fresh
        ``match()``, since these sentences have no {name} of their own for
        any other parser to resolve.

        The target domain (light vs climate) is derived from which spec
        dict the parsed intent name falls into - ``LIGHT_EXTENDED_INTENTS``'
        ``HassLightBrighten``/``HassLightDim`` for brightness,
        ``CLIMATE_EXTENDED_INTENTS``' ``HassClimateIncreaseTemperature``/
        ``HassClimateDecreaseTemperature`` for "wärmer"/"kälter" (V6.23:
        reuses that already-established vocabulary and those already-wired
        intents rather than inventing a color-temperature equivalent for
        which no grammar/lexicon vocabulary exists yet - "niemals raten").

        Scope deliberately narrowed to exactly one matching entity in
        ``context.last_entities``: both spec dicts' response lambdas only
        ever read ``entities[0].friendly_name`` (see service_call.py), so
        passing through 2+ entities would silently name only one of several
        entities actually changed. 0 or 2+ matches therefore both return
        ``None`` here - same "never guess" rule as the rest of the engine,
        not a bug: the plan's own example is single-entity anyway. Falls
        through to the normal "not understood" response, same as any other
        non-match.
        """
        if context is None:
            return None

        normalized = normalize(text).strip(" .!?\t\r\n")
        media = self._media_followup(normalized, context)
        if media is not None:
            return media
        adjustment = extract_degree(normalized)
        meaning = adjustment.text.casefold().strip(" .!?")
        intent_by_meaning = {
            "heller": "HassLightBrighten",
            "dunkler": "HassLightDim",
            "wärmer": "HassClimateIncreaseTemperature",
            "waermer": "HassClimateIncreaseTemperature",
            "kälter": "HassClimateDecreaseTemperature",
            "kaelter": "HassClimateDecreaseTemperature",
        }
        intent = intent_by_meaning.get(meaning)
        if intent is None:
            return None

        if intent in LIGHT_EXTENDED_INTENTS:
            domain = "light"
            parameters = {"step_percent": adjustment.light_percent}
        elif intent in CLIMATE_EXTENDED_INTENTS:
            domain = "climate"
            parameters = {"step": adjustment.climate_degrees}
        else:
            return None

        matched = [entity for entity in context.last_entities if entity.domain == domain]
        if len(matched) != 1:
            return None

        entity = matched[0]
        frame = SemanticFrame(
            intent=intent,
            target=TargetReference(text=text, entity_id=entity.entity_id, domain=entity.domain),
            area=None,
            parameters=parameters,
            source_text=text,
        )
        return self._build_match_result(
            ParseResult(frame=frame, resolved_entities=[entity]), list(context.last_entities), context
        )
    def _media_followup(
        self, normalized: str, context: ConversationContext
    ) -> MatchResult | None:
        """ "Bitte weiterspielen." / "Kannst du es pausieren?" for the one
        media player of the previous turn (F13) - elliptical or with "es"."""
        key = normalize_for_compare(normalized)
        key = re.sub(r"^(?:bitte|kannst\s+du|koenntest\s+du|wuerdest\s+du|jetzt|und|dann)\s+", "", key)
        key = re.sub(r"^(?:bitte|jetzt|dann)\s+", "", key)
        key = re.sub(r"\s+(?:bitte|mal|jetzt|wieder|doch)\b", "", key).strip()
        key = re.sub(r"^(?:es|das|ihn|sie)\s+", "", key)
        intent = {
            "weiterspielen": "HassMediaPlay", "weiter spielen": "HassMediaPlay",
            "fortsetzen": "HassMediaPlay", "weiter": "HassMediaPlay",
            "abspielen": "HassMediaPlay", "spiel weiter": "HassMediaPlay",
            "mach weiter": "HassMediaPlay", "pausieren": "HassMediaPause",
            "pausiere": "HassMediaPause", "pause": "HassMediaPause",
            "anhalten": "HassMediaPause", "halt an": "HassMediaPause",
            "stoppen": "HassMediaStop", "stopp": "HassMediaStop",
        }.get(key)
        if intent is None:
            return None
        players = [entity for entity in context.last_entities if entity.domain == "media_player"]
        if len(players) != 1:
            return None
        entity = players[0]
        frame = SemanticFrame(
            intent=intent,
            target=TargetReference(entity.friendly_name, entity.entity_id, entity.domain),
            area=None,
            source_text=normalized,
        )
        return self._build_match_result(
            ParseResult(frame=frame, resolved_entities=[entity]), list(context.last_entities), context
        )

    def match_contextual_property_followup(
        self,
        text: str,
        entities: list[EntitySnapshot],
        context: ConversationContext | None,
    ) -> MatchResult | None:
        """Infer omitted targets for existing, validated setpoint commands.

        The mapping is intentionally limited to real controllable properties:
        temperature→climate, brightness→light, position→cover and
        speed/level→fan. Battery, humidity, power and energy stay read-only.
        """
        outcome = self._contextual_property_resolver.resolve(text, entities, context)
        if outcome is None:
            return None
        if isinstance(outcome, UnderstandingFeedback):
            return MatchResult(plan=None, response_text=outcome.speech)
        if isinstance(outcome, ClarificationRequest):
            return MatchResult(
                plan=None,
                response_text=_clarification_question(outcome),
                clarification=outcome,
            )
        return self._build_match_result(outcome, entities, context)
    def match_reference(
        self,
        text: str,
        entities: list[EntitySnapshot],
        context: ConversationContext | None,
        world_model: WorldModel | None = None,
    ) -> MatchResult | None:
        """Resolve a pronoun or relative reference ("Mach es aus.", "Mach die
        auch an.", "Die Rollläden dort runter.", "Die andere.") against the
        stored ``ConversationContext`` (v2 plan Phase 27, "Pronomen und
        Referenzen"). Tried by ``conversation.py`` alongside
        the two grammars can't collide with each other or with any other
        grammar (disjoint, fixed sentence sets, verified empirically against
        hassil==3.11.0).

        Both a structural non-match and a recognized-but-unresolvable
        relative reference (for example "Die andere.", the plan's own
        ``AMBIGUOUS_REFERENCE`` case, "nie raten") collapse to ``None``
        here, same as the rest of this engine's
        ``match_followup()``/``match()``/``resolve_clarification()``:
        ``conversation.py`` shows the same fixed "not understood" text
        regardless of the specific miss cause.
        """
        if context is None:
            return None

        normalized = re.sub(
            r"^(?:dann|danach|also)\s+", "", normalize(text), flags=re.IGNORECASE
        )
        # "Schalte die aus": a bare demonstrative directly before the verb
        # particle refers back like "sie" (F12).
        normalized = re.sub(
            r"^(\s*(?:bitte\s+)?\w+\s+)(?:die|diese|jene)(\s+(?:auch\s+)?(?:aus|an|ein|zu|auf|hoch|runter|ab)\b)",
            r"\1sie\2", normalized, flags=re.IGNORECASE,
        )
        remembered_entities = (
            context.memory.entities if context.memory is not None else context.last_entities
        )
        if re.fullmatch(r"(?:die|der|das)\s+(?:andere|daneben)\.?", normalized, re.I):
            return None
        is_others = re.search(r"\bdie\s+anderen\b", normalized, re.I) is not None
        is_area_reference = re.search(
            r"\b(?:hier|dort|im\s+selben\s+raum)\b", normalized, re.I
        ) is not None
        is_pronoun = re.search(r"\b(?:es|sie|ihn|die\s+auch)\b", normalized, re.I) is not None
        is_exclusion_reference = re.search(
            r"\b(?:außer|ausser)\b", normalized, re.I
        ) is not None
        exclusion_candidates: list[EntitySnapshot] = []
        if is_exclusion_reference and context.discourse is not None:
            group = current_discourse_group(
                context.discourse, semantic_type="entity"
            )
            excluded = all_mentioned_entities(normalized, entities)
            if group is not None and len(excluded) == 1:
                live_by_id = {entity.entity_id: entity for entity in entities}
                group_ids = {
                    member_id.removeprefix("entity:")
                    for member_id in group.member_ids
                    if member_id.startswith("entity:")
                }
                if excluded[0].entity_id in group_ids:
                    exclusion_candidates = [
                        live_by_id[entity_id]
                        for entity_id in sorted(group_ids - {excluded[0].entity_id})
                        if entity_id in live_by_id
                    ]
        if not (
            is_others or is_area_reference or is_pronoun
            or exclusion_candidates
        ):
            return None

        discourse_resolution = (
            resolve_reference(normalized, context.discourse, entities)
            if is_pronoun and context.discourse is not None
            else None
        )
        if (
            discourse_resolution is not None
            and discourse_resolution.status in {
                ReferenceStatus.RESOLVED,
                ReferenceStatus.AMBIGUOUS,
            }
        ):
            remembered_entities = discourse_resolution.entities
        remembered_ids = {entity.entity_id for entity in remembered_entities}
        fresh_remembered = [
            entity for entity in entities if entity.entity_id in remembered_ids
        ]
        # Conversation context contains the snapshot that was validated for
        # the preceding turn.  Keep it usable for resolving the reference
        # even when a small caller-provided test/preview inventory omits it;
        # execution still performs the mandatory live-snapshot validation.
        resolved_remembered = fresh_remembered or list(remembered_entities)
        if exclusion_candidates:
            candidates = exclusion_candidates
        elif is_others:
            if context.last_area is None or not resolved_remembered:
                return None
            domains = {entity.domain for entity in resolved_remembered}
            if len(domains) != 1:
                return None
            domain = next(iter(domains))
            candidates = [
                entity
                for entity in entities
                if entity.domain == domain
                and entity.area_id == context.last_area.area_id
                and entity.entity_id not in remembered_ids
            ]
        elif is_area_reference:
            analysis = analyse_semantics(normalized)
            domains = {
                value
                for value in analysis.values(SemanticKind.DOMAIN)
                if isinstance(value, str)
            }
            if len(domains) != 1:
                return None
            domain = next(iter(domains))
            group = current_discourse_group(
                context.discourse, semantic_type="area"
            )
            if group is not None and world_model is not None:
                selection = QueryCommand(
                    "HassStateQuery",
                    QueryScope.LIST,
                    QueryTarget(domain=domain),
                    QueryFilter(),
                    RelationFilterExpression(
                        SourceExpression(QueryTarget(domain=domain)),
                        QueryTraversal(((
                            RelationKind.LOCATED_IN,
                            TraversalDirection.OUTGOING,
                        ),)),
                        LiteralSetExpression(
                            QueryTargetKind.AREA, group.member_ids
                        ),
                    ),
                )
                selected = QueryExecutor().execute(selection, [], world_model)
                if selected.status is not QueryResultStatus.MATCHED:
                    return None
                candidates = list(selected.entities)
            elif context.last_area is not None:
                candidates = [
                    entity
                    for entity in entities
                    if entity.domain == domain
                    and entity.area_id == context.last_area.area_id
                ]
            else:
                return None
        else:
            candidates = resolved_remembered
        if not candidates:
            return None

        direction = re.search(
            r"\b(heller|dunkler|an|ein|aus|hoch|runter|herunter|auf|zu)\b(?=[.!?]*$)",
            normalized,
            re.I,
        )
        if direction is None:
            return None
        if direction.group(1).casefold() in {"heller", "dunkler"} and len(candidates) != 1:
            return None
        probe = candidates[0]
        direction_word = direction.group(1).casefold()
        action_value = {
            "an": "turn_on",
            "ein": "turn_on",
            "aus": "turn_off",
            "hoch": "open",
            "auf": "open",
            "runter": "close",
            "herunter": "close",
            "zu": "close",
        }.get(direction_word)
        intent: str | None
        parameters: dict[str, object] = {}
        property_: SemanticProperty | None = None
        semantic_direction: SemanticDirection | None = None
        degree = None
        if direction_word in {"heller", "dunkler"}:
            if probe.domain != "light":
                return None
            adjustment = extract_degree(text)
            intent = (
                "HassLightBrighten"
                if direction_word == "heller"
                else "HassLightDim"
            )
            parameters["step_percent"] = adjustment.light_percent
            semantic_action = SemanticAction.ADJUST
            property_ = SemanticProperty.BRIGHTNESS
            semantic_direction = (
                SemanticDirection.INCREASE
                if direction_word == "heller"
                else SemanticDirection.DECREASE
            )
            degree = adjustment.degree
        else:
            if action_value is None:
                return None
            intents = {
                INTENT_BY_DOMAIN_ACTION.get((entity.domain, action_value))
                for entity in candidates
            }
            if None in intents or len(intents) != 1:
                return None
            intent = next(iter(intents))
            semantic_action = {
                "turn_on": SemanticAction.TURN_ON,
                "turn_off": SemanticAction.TURN_OFF,
                "open": SemanticAction.OPEN,
                "close": SemanticAction.CLOSE,
            }[action_value]
        if intent is None:
            return None
        frame = SemanticFrame(
            intent=intent,
            target=TargetReference(
                text=text,
                entity_id=(probe.entity_id if len(candidates) == 1 else None),
                domain=probe.domain,
            ),
            area=(
                AreaReference(
                    text=context.last_area.name,
                    area_id=context.last_area.area_id,
                    area_name=context.last_area.name,
                )
                if is_area_reference and context.last_area is not None
                else None
            ),
            quantifier=(Quantifier("all") if len(candidates) > 1 else None),
            parameters=parameters,
            source_text=text,
            action=semantic_action,
            property=property_,
            direction=semantic_direction,
            degree=degree,
            semantic_graph=(
                context.discourse.current_graph
                if context.discourse is not None
                else None
            ),
        )
        if (
            discourse_resolution is not None
            and discourse_resolution.status is ReferenceStatus.AMBIGUOUS
        ):
            clarification = ClarificationRequest(
                pending_intent=frame.intent,
                pending_target=text,
                candidates=tuple(candidates),
                pending_parameters=frame.parameters,
            )
            return MatchResult(
                plan=None,
                response_text=_clarification_question(clarification),
                clarification=clarification,
            )
        return self._build_match_result(
            ParseResult(frame=frame, resolved_entities=candidates), entities, context
        )

    def match_query_followup(
        self,
        text: str,
        entities: list[EntitySnapshot],
        context: ConversationContext | None,
        world_model: WorldModel | None = None,
    ) -> MatchResult | None:
        """Continue a previous state query with a new room/floor ("Und in
        der Küche?", "Und oben?" after "Wie warm ist es im Wohnzimmer?") -
        HomeIntent plan V4.9, "Cross-Sentence References". Tried by
        ``conversation.py`` alongside ``match_followup()``/
        ``match_reference()``, before the normal ``match()``: a leading
        "Und ..." is never touched by ``match()``'s own multi-step
        ``_AND_SPLIT_RE`` (that pattern requires whitespace on *both* sides
        of "und", so it never fires on a sentence that only *starts* with
        the word - see its own comment), and no existing grammar has a
        sentence shape for a bare "und {area}"/"und {level}" anyway, so
        without this method such a follow-up would just fail to match
        anything and fall through to "not understood".

        Continues a plain ``HassGetState`` query, or (HomeIntent v4.2.1
        plan, Phase 8) one of the three ``StateQueryParser`` intents -
        ``HassStateQuery``/``HassCheckState``/``HassExistsQuery`` - gated
        here (not in ``QueryFollowupParser`` itself) for the same reason
        ``match_followup()`` does its own light-only filtering before
        calling into ``ContextFollowupParser``: keeps the parser itself
        free of ``ConversationContext``/``SemanticCommand`` knowledge, it
        only sees the plain ``QueryCommand`` this method extracts from
        ``context.last_command.parameters["query_command"]`` (populated by
        ``StateQueryParser`` since Phase 7). ``HassQueryComparison``
        (``QUERY_INTENTS``' other entry, its own multi-entity "welche..."
        shape) is out of scope - same narrow-scoping precedent
        ``TemporalParser``/``ComparisonQueryParser`` already set for
        themselves. A missing/non-query previous turn, or a structural/
        resolution miss in the parser itself, both collapse to ``None``
        here, same as every other "never guess" miss in this engine.
        """
        if context is None:
            return None

        normalized_reference = normalize(text)
        discourse_group = current_discourse_group(context.discourse)
        discourse_location = resolve_semantic_location(
            normalized_reference, entities, world_model
        )
        excludes_location = re.search(
            r"\b(?:außer|ausser|ohne)\b", normalized_reference, re.I
        ) is not None
        if (
            discourse_group is not None
            and discourse_group.semantic_type == "entity"
            and re.search(
                r"\b(?:davon|diese|jene|die|beide|alle)\b",
                normalized_reference,
                re.I,
            )
            and world_model is not None
        ):
            analysis = analyse_semantics(normalized_reference)
            states = tuple({
                span.value
                for span in analysis.matching(SemanticKind.STATE)
                if isinstance(span.value, SemanticState)
                and span.text.casefold()
                not in {"ein", "eine", "einen", "einem", "einer", "steht"}
            })
            if len(states) == 1:
                expression = StateFilterExpression(
                    LiteralSetExpression(
                        QueryTargetKind.ENTITY, discourse_group.member_ids
                    ),
                    states[0],
                )
                command = QueryCommand(
                    "HassStateQuery",
                    QueryScope.LIST,
                    QueryTarget(kind=QueryTargetKind.ENTITY),
                    QueryFilter(),
                    expression,
                )
                query_result = QueryExecutor().execute(
                    command, entities, world_model
                )
                parsed = ParseResult(
                    frame=SemanticFrame(
                        intent=command.intent,
                        target=TargetReference(text),
                        area=None,
                        quantifier=Quantifier("all"),
                        parameters={
                            "query_command": command,
                            "query_result": query_result,
                        },
                        source_text=text,
                        action=SemanticAction.QUERY,
                    ),
                    resolved_entities=list(query_result.entities),
                )
                return self._build_match_result(parsed, entities, context)
        if (
            discourse_group is not None
            and discourse_group.semantic_type == "entity"
            and has_word(normalized_reference, "davon", "diese", "jene")
            and discourse_location is not None
            and (discourse_location[1] is not None or discourse_location[2] is not None)
            and world_model is not None
        ):
            # "Wie viele Lichter sind an?" -> "Welche davon sind im
            # Erdgeschoss?": the previous result set, narrowed to the
            # location and listed by name (F12).
            members = {
                member.removeprefix("entity:") for member in discourse_group.member_ids
            }
            located = [
                entity for entity in entities
                if entity.entity_id in members
                and (
                    (discourse_location[1] is not None and entity.area_id == discourse_location[1])
                    or (discourse_location[2] is not None and entity.floor_id == discourse_location[2])
                )
            ]
            if excludes_location:
                located = [
                    entity for entity in entities
                    if entity.entity_id in members and entity not in located
                ]
            names = [entity.friendly_name for entity in located]
            where = sentence_initial(
                dative_location_phrase(discourse_location[0].strip())
                if not re.match(r"(?:im|in|am|auf|beim)\b", discourse_location[0].strip(), re.I)
                else discourse_location[0].strip()
            )
            speech = (
                f"{where} ist davon keines." if not names
                else f"{where} {'ist' if len(names) == 1 else 'sind'} davon: {join_german(tuple(names))}."
            )
            return MatchResult(
                plan=None,
                response_text=speech,
                context_entities=tuple(located),
            )
        if (
            discourse_group is not None
            and discourse_group.semantic_type == "area"
            and (
                excludes_location
                or has_word(normalized_reference, "davon", "diese", "jene", "welche")
            )
            and discourse_location is not None
            and world_model is not None
        ):
            expression = SetExpression(
                LiteralSetExpression(QueryTargetKind.AREA, discourse_group.member_ids),
                (
                    SetOperator.DIFFERENCE
                    if excludes_location else SetOperator.INTERSECTION
                ),
                SourceExpression(QueryTarget(
                    kind=QueryTargetKind.AREA,
                    area=(
                        next(
                            (area for area in world_model.areas if area.area_id == discourse_location[1]),
                            None,
                        )
                        if discourse_location[1] is not None
                        else None
                    ),
                    floor_id=discourse_location[2],
                )),
            )
            command = QueryCommand(
                "HassStateQuery", QueryScope.LIST,
                QueryTarget(kind=QueryTargetKind.AREA), QueryFilter(), expression,
            )
            query_result = QueryExecutor().execute(command, [], world_model)
            parsed = ParseResult(
                frame=SemanticFrame(
                    intent=command.intent,
                    target=TargetReference(text),
                    area=None,
                    quantifier=Quantifier("all"),
                    parameters={"query_command": command, "query_result": query_result},
                    source_text=text,
                    action=SemanticAction.QUERY,
                ),
                resolved_entities=[],
            )
            return self._build_match_result(parsed, entities, context)

        if (
            discourse_group is not None
            and discourse_group.semantic_type == "area"
            and has_word(normalized_reference, "davon", "diese", "jene", "welche")
            and world_model is not None
        ):
            analysis = analyse_semantics(normalized_reference)
            classes = tuple(
                value
                for value in analysis.values(SemanticKind.DEVICE_CLASS)
                if isinstance(value, tuple) and len(value) == 2
            )
            states = tuple(
                span.value
                for span in analysis.matching(SemanticKind.STATE)
                if isinstance(span.value, SemanticState)
                and span.text.casefold()
                not in {"ein", "eine", "einen", "einem", "einer", "steht"}
            )
            if len(classes) == 1 and len(set(states)) == 1:
                related_domain, related_class = classes[0]
                expression = RelationFilterExpression(
                    LiteralSetExpression(
                        QueryTargetKind.AREA, discourse_group.member_ids
                    ),
                    QueryTraversal(((
                        RelationKind.LOCATED_IN,
                        TraversalDirection.INCOMING,
                    ),)),
                    StateFilterExpression(
                        SourceExpression(QueryTarget(
                            domain=str(related_domain),
                            device_class=str(related_class),
                        )),
                        states[0],
                    ),
                )
                command = QueryCommand(
                    "HassStateQuery", QueryScope.LIST,
                    QueryTarget(kind=QueryTargetKind.AREA), QueryFilter(), expression,
                )
                query_result = QueryExecutor().execute(command, [], world_model)
                parsed = ParseResult(
                    frame=SemanticFrame(
                        intent=command.intent,
                        target=TargetReference(text),
                        area=None,
                        quantifier=Quantifier("all"),
                        parameters={
                            "query_command": command,
                            "query_result": query_result,
                        },
                        source_text=text,
                        action=SemanticAction.QUERY,
                    ),
                    resolved_entities=[],
                )
                return self._build_match_result(parsed, entities, context)

        remembered_entities = (
            context.memory.entities if context.memory is not None else context.last_entities
        )
        remembered_predicate = (
            context.memory.predicate
            if context.memory is not None and context.memory.predicate is not None
            else context.last_query_predicate
        )
        contextual_answer = match_contextual_verb_state_query(
            text,
            entities,
            remembered_entities,
            remembered_predicate,
        )
        if contextual_answer is not None:
            return MatchResult(
                plan=None,
                response_text=contextual_answer.response_text,
                context_entities=contextual_answer.entities,
                context_predicate=contextual_answer.predicate,
                explanation_text=contextual_answer.explanation_text,
            )

        if (
            context.last_command is None
            or context.last_command.intent not in _QUERY_FOLLOWUP_INTENTS
        ):
            return None

        normalized = normalize(text)
        meant = _MEANT_PREFIX_RE.match(normalized)
        meant_rest = normalized[meant.end():] if meant is not None else ""
        if meant_rest:
            location = meant_rest.rstrip("?.!") or meant_rest[:1]
            normalized = f"Und im {location.strip()}?"
        else:
            natural = re.match(
                r"^wie\s+sieht\s+es\s+(?P<location>oben|unten|(?:im|in der|in dem)\s+.+?)\s+aus[?.!]*$",
                normalized,
                re.IGNORECASE,
            )
            if natural is not None:
                normalized = f"Und {natural.group('location')}?"
        if re.match(
            r"^(?:welche\b|(?:in der|in dem|im|am|beim)\b|oben\b|unten\b)",
            normalized,
            re.IGNORECASE,
        ):
            # The follow-up grammar stores deltas, not discourse particles.
            # "Und" is optional in natural dialogue; canonicalize both forms
            # to the same grammar instead of duplicating every sentence.
            normalized = f"Und {normalized}"
        previous_query_command = context.last_command.parameters.get("query_command")
        if (
            context.last_command.parameters.get("property")
            and context.last_command.parameters.get("location_kind") in {"area", "floor"}
            and (
                context.last_command.intent == "HassLocationPropertyQuery"
                or context.focus is not None
            )
        ):
            location_match = re.match(
                r"^und\s+(?:(?:im|in der|in dem)\s+)?(?P<location>.+?)[?.!]*$",
                normalized,
                re.IGNORECASE,
            )
            property_name = context.last_command.parameters.get("property")
            if location_match is not None and property_name:
                query = SemanticQueryCompiler.compile(
                    f"{property_name} im {location_match.group('location').strip()}",
                    entities,
                    world_model,
                )
                if query is not None:
                    return self._build_match_result(query, entities, context)
        result = compile_query_followup(
            normalized,
            entities,
            context.last_entities,
            previous_query_command,
            world_model,
        )
        if result is None:
            return None
        return self._build_match_result(result, entities, context)

    def match_command_followup(
        self, text: str, entities: list[EntitySnapshot], context: ConversationContext | None
    ) -> MatchResult | None:
        """Continue a previous *command* (not query) with a generic
        follow-up ("Und jetzt wieder aus." after "Schalte das Studio ein.",
        "Und im Schlafzimmer auch." after "Mach das Licht im Wohnzimmer
        an.") - live conversation-context bug fix 2026-08-18. Tried by
        ``conversation.py`` alongside ``match_followup()``/
        ``match_reference()``/``match_query_followup()``, before the normal
        ``match()`` - same "a leading 'und ...' never matches ``match()``'s
        own grammars anyway" reasoning ``match_query_followup()`` already
        documents for itself.

        Gated on ``context.last_command.intent`` being one of the 5 basic
        command intents (``INTENTS``) - the only ones ``CommandFollowupParser``
        can meaningfully continue (either by inverting via
        ``service_call.ACTION_OPPOSITES``, or by keeping the same intent for
        a new area). Query-only previous turns (``QUERY_INTENTS``) are left
        to ``match_query_followup()`` instead - strict command/query context
        separation, never mixed.
        """
        if context is None or context.last_command is None or context.last_command.intent not in INTENTS:
            return None

        normalized = normalize(text).strip()
        previous = context.last_command
        previous_entities = tuple(
            entity
            for remembered in previous.entities
            for entity in entities
            if entity.entity_id == remembered.entity_id
        )
        if len(previous_entities) != len(previous.entities) or not previous_entities:
            return None
        domains = {entity.domain for entity in previous_entities}
        if len(domains) != 1:
            return None

        if has_word(normalized, "auch"):
            location = resolve_semantic_location(normalized, entities)
            if location is None:
                return None
            candidates = [
                entity
                for entity in entities
                if entity.domain in domains
                and (location[1] is None or entity.area_id == location[1])
                and (location[2] is None or entity.floor_id == location[2])
            ]
            if not candidates:
                return None
            frame = SemanticFrame(
                intent=previous.intent,
                target=TargetReference(
                    text=next(iter(domains)), domain=next(iter(domains))
                ),
                area=(
                    AreaReference(location[0], location[1], location[0])
                    if location[1] is not None
                    else None
                ),
                quantifier=Quantifier("all") if len(candidates) > 1 else None,
                parameters=dict(previous.parameters),
                source_text=text,
                action=previous.source_frame.action,
            )
            return self._build_match_result(
                ParseResult(frame=frame, resolved_entities=candidates),
                entities,
                context,
            )

        opposite = ACTION_OPPOSITES.get(previous.intent)
        if opposite is None:
            return None
        action_word = re.search(
            r"\b(an|ein|aus|auf|zu|hoch|runter|herunter)\b(?=[.!?]*$)",
            normalized,
            re.I,
        )
        if action_word is None:
            return None
        probe = previous_entities[0]
        parsed = SemanticCommandCompiler.compile(
            f"mach {probe.friendly_name} {action_word.group(1)}",
            [probe],
        )
        if not isinstance(parsed, ParseResult) or parsed.frame.intent != opposite:
            return None
        frame = replace(
            parsed.frame,
            target=TargetReference(
                text=probe.friendly_name,
                domain=probe.domain,
                entity_id=(probe.entity_id if len(previous_entities) == 1 else None),
            ),
            quantifier=(Quantifier("all") if len(previous_entities) > 1 else None),
            source_text=text,
        )
        return self._build_match_result(
            ParseResult(frame=frame, resolved_entities=list(previous_entities)),
            entities,
            context,
        )

    def match_automation_query(
        self,
        text: str,
        entities: list[EntitySnapshot],
        context: ConversationContext | None,
        automations: tuple[AutomationSummary, ...],
    ) -> MatchResult | None:
        """Match "Welche Automationen gibt es?"/"Was schaltet/steuert X?"/
        "Warum ist/geht X ...?" (HomeIntent plan V5.29, "Automation Query")
        into a ``QueryResult`` built from ``automations`` - a read-only
        survey of ``automations.yaml`` plus its metadata sidecar
        (``AutomationExecutor.async_list_automations()``), same
        ``AutomationSummary``-tuple shape ``AutomationQueryParser``/
        ``QueryExecutor._execute_automation()`` already consume.

        Gated by ``_AUTOMATION_QUERY_RE`` first, same cheap-pre-check role
        ``_AUTOMATION_TRIGGER_RE`` already plays for ``match_automation()``
        - but here the gate matters even more: it is what lets
        ``conversation.py`` decide whether to await the real, blocking
        ``automations.yaml``/metadata-sidecar read at all *before* calling
        this method, so ``automations`` is only ever non-empty-by-actual-
        content on a turn that plausibly needs it. An ordinary command/query
        turn never pays that I/O cost.

        Called from ``conversation.py`` alongside ``match_automation()`` -
        both are independent entry points ``_select_parser()`` never routes
        to internally, same "new grammar, new top-level method" precedent
        every other separately-compiled grammar in this module already
        follows.
        """
        if not _AUTOMATION_QUERY_RE.search(text):
            return None

        normalized = normalize(text)
        parse_context = create_parse_context(entities)
        result = self._automation_query_parser.parse(normalized, parse_context, automations)
        if result is None:
            return None
        return self._build_match_result(result, entities, context)

    def match_automation_delete(
        self,
        text: str,
        entities: list[EntitySnapshot],
        automations: tuple[AutomationSummary, ...],
    ) -> AutomationDeletionMatchResult | None:
        """Match "Lösche/Entferne die Automation für X" (HomeIntent plan
        V5.28, "Automation Deletion") into an ``AutomationDeletionMatchResult``
        - never a direct deletion on this turn, only ever a spoken
        confirmation prompt or refusal (see that type's own docstring for
        why). ``conversation.py`` stores a successful resolution as a
        ``PendingAutomationDeletion`` and only calls
        ``AutomationExecutor.async_delete_automation()`` once the user
        confirms with "ja" - same two-step gate ``match_automation()``'s own
        creation path already established, and for the identical reason
        (V5.36's "no half-created/half-deleted automations" applies to
        deletion just as much as creation).

        Gated by ``_AUTOMATION_DELETE_RE`` first, the same role
        ``_AUTOMATION_QUERY_RE`` plays for ``match_automation_query()`` -
        letting ``conversation.py`` skip the real, blocking
        ``automations.yaml``/metadata-sidecar read on a turn that plausibly
        can't need it.

        Returns ``None`` only when the sentence doesn't match this grammar
        at all, or when the named entity itself doesn't resolve (unknown or
        ambiguous {name} - never guessed, same as ``AutomationQueryParser``).
        A resolved entity with zero or 2+ referencing automations still
        returns a result (a spoken refusal), not ``None`` - the sentence was
        understood, it just can't be acted on.
        """
        if not _AUTOMATION_DELETE_RE.search(text):
            return None

        normalized = normalize(text)
        parse_context = create_parse_context(entities)
        match: AutomationDeleteMatch | None = self._automation_delete_parser.parse(
            normalized, parse_context, automations
        )
        if match is None:
            return None

        if not match.matched:
            return AutomationDeletionMatchResult(
                automation=None,
                response_text=_no_automation_text(match.entity),
            )
        if len(match.matched) > 1:
            return AutomationDeletionMatchResult(
                automation=None,
                response_text=(
                    f"{_several_automations_text(match.entity)} "
                    "Das kann ich nicht eindeutig löschen."
                ),
            )

        automation = next(iter(match.matched))
        return AutomationDeletionMatchResult(
            automation=automation,
            response_text=f'Soll die Automation "{_automation_label(automation)}" gelöscht werden?',
        )

    def match_automation_disable(
        self,
        text: str,
        entities: list[EntitySnapshot],
        automations: tuple[AutomationSummary, ...],
    ) -> AutomationToggleMatchResult | None:
        """Match "Deaktiviere die Automation für X" (HomeIntent plan V5.28
        rest, Wave 11 "Automation Disable/Enable") into an
        ``AutomationToggleMatchResult`` with ``enable=False`` - unlike
        ``match_automation_delete()``, a resolved single match here is acted
        on immediately by ``conversation.py`` on this same turn (see that
        result type's own docstring for why no confirmation gate is needed).

        Gated by ``_AUTOMATION_DISABLE_RE`` first, same role
        ``_AUTOMATION_DELETE_RE`` plays for ``match_automation_delete()``.
        """
        return self._match_automation_toggle(text, entities, automations, gate=_AUTOMATION_DISABLE_RE)

    def match_automation_enable(
        self,
        text: str,
        entities: list[EntitySnapshot],
        automations: tuple[AutomationSummary, ...],
    ) -> AutomationToggleMatchResult | None:
        """The ``match_automation_disable()`` counterpart - ``enable=True``,
        gated by ``_AUTOMATION_ENABLE_RE`` instead."""
        return self._match_automation_toggle(text, entities, automations, gate=_AUTOMATION_ENABLE_RE)

    def _match_automation_toggle(
        self,
        text: str,
        entities: list[EntitySnapshot],
        automations: tuple[AutomationSummary, ...],
        gate: re.Pattern[str],
    ) -> AutomationToggleMatchResult | None:
        if not gate.search(text):
            return None

        normalized = normalize(text)
        parse_context = create_parse_context(entities)
        match: AutomationToggleMatch | None = self._automation_toggle_parser.parse(
            normalized, parse_context, automations
        )
        if match is None:
            return None

        verb = "aktivieren" if match.enable else "deaktivieren"
        if not match.matched:
            return AutomationToggleMatchResult(
                automation=None,
                enable=match.enable,
                response_text=_no_automation_text(match.entity),
            )
        if len(match.matched) > 1:
            return AutomationToggleMatchResult(
                automation=None,
                enable=match.enable,
                response_text=(
                    f"{_several_automations_text(match.entity)} "
                    f"Das kann ich nicht eindeutig {verb}."
                ),
            )

        automation = next(iter(match.matched))
        return AutomationToggleMatchResult(
            automation=automation,
            enable=match.enable,
            response_text=(
                f"Automation wurde {'aktiviert' if match.enable else 'deaktiviert'}."
            ),
        )

    def _try_extract_notification_from_trigger_clause(
        self,
        text: str,
        parse_context: ParseContext,
        entities: list[EntitySnapshot] | tuple[EntitySnapshot, ...],
    ) -> tuple[str, str] | None:
        """Extract a trailing notification action from a trigger-first clause
        with no comma at all, e.g. "sobald das Fenster geöffnet wird eine
        Push-Nachricht an Philipp schickt" - the shape left behind once
        ``strip_automation_shell()`` removes a meta-shell that had no comma
        of its own before "die" either.

        Returns (trigger_text, action_text) only once both halves
        independently validate through their own dedicated parser
        (``AutomationTriggerParser``/``notification_action_from_request()``)
        - never a guess. Returns ``None`` if no trigger connector is found,
        no trailing notification pattern is detected, or either half fails
        to parse.
        """
        notification_pattern_re = re.compile(
            r"\b(?:eine\s+)?(?:push[\s-]nachricht|benachrichtigung|nachricht|meldung|mitteilung)\s+"
            r"(?:an\s+den|an\s+die|an\s+dem|an|dem|der)\s+"
            r"[a-zäöüß][a-z0-9äöüß_-]*"
            r"(?:\s+(?:schick\w*|send\w*|geb\w*|sag\w*))?",
            re.IGNORECASE,
        )
        # ``(?<!\S)`` (not preceded by a non-whitespace char) instead of a
        # literal leading ``\s+`` so the connector is also recognized right
        # at the start of the string - the common case once an automation
        # shell has been stripped off the front of the sentence.
        for marker in re.finditer(r"(?<!\S)(wenn|sobald|falls)\b", text, re.IGNORECASE):
            trigger_with_action = text[marker.end():].strip(" ,")
            if not trigger_with_action:
                continue

            notification_match = notification_pattern_re.search(trigger_with_action)
            if notification_match is None:
                continue

            trigger_only = trigger_with_action[: notification_match.start()].strip(" ,")
            if not trigger_only:
                continue

            trigger_text = f"{marker.group(1)} {trigger_only}"
            parsed_trigger = self._automation_trigger_parser.parse(trigger_text, parse_context)
            if parsed_trigger is None:
                continue

            action_text = trigger_with_action[notification_match.start():].strip()
            notification = notification_action_from_request(action_text, trigger_text, entities)
            if notification is None:
                continue

            return (trigger_text, action_text)

        return None

    def _parse_action_or_notification(
        self,
        action_text: str,
        trigger_text: str,
        entities: list[EntitySnapshot] | tuple[EntitySnapshot, ...],
        parse_context: ParseContext,
        trigger: TriggerModel | None = None,
    ) -> tuple[ActionModel | ActionGroup, ...] | None:
        """Prefer the dedicated notification-vocabulary reader in
        ``automation_notification.py`` over the general action parser for a
        recipient-shaped clause; fall back to the general parser otherwise.

        ``notify_action.yaml``'s pre-existing grammar branches ("benachrichtige
        [mich|uns] [dass] {message}", "[schick|sende] ... eine Nachricht [dass]
        {message}") all end in a free-form wildcard ``{message}`` slot. Given a
        bare recipient clause with no "dass ..." content of its own (e.g.
        "benachrichtige mich" or "sende eine Nachricht an Philipp"), that
        wildcard can still match by swallowing the recipient token/prepositional
        phrase itself, producing a nonsensical raw message and never resolving
        a named recipient to its notify.* target.
        ``notification_action_from_request()``'s three patterns all
        ``fullmatch`` a tightly scoped verb+recipient / message+recipient
        shape with no trailing free text, so they never match a genuine
        explicit-content dictation ("... dass der Akku leer ist") - trying
        them first only ever intercepts the recipient-shaped clauses this
        module is meant to own, and falls through to the general parser
        (unchanged) for everything else.

        If the clause matches a notification shape but the recipient itself
        could not be resolved (unknown or ambiguous), that is a hard failure
        - falling through to the general parser here would let the same
        wildcard ``{message}`` slot swallow the unresolved recipient token
        as free-text message content, silently producing a wrong automation
        instead of refusing (never guess).
        """
        notification = notification_action_from_clause(
            action_text, trigger_text, entities, trigger
        ) or notification_action_from_request(
            action_text, trigger_text, entities, trigger
        )
        if notification is not None:
            return (notification,)
        if is_notification_shaped(action_text):
            return None
        return self._parse_action_semantically(action_text, parse_context)

    def _parse_action_semantically(
        self, text: str, context: ParseContext
    ) -> tuple[ActionModel | ActionGroup, ...] | None:
        """Prefer the shared direct-command semantics for automation actions.

        This keeps percentage, natural-position and flexible-word-order
        understanding identical for immediate, delayed and state-triggered
        commands. Automation-only actions remain available through the
        established dedicated parser as the fallback.
        """
        clause = parse_notification_clause(text)
        if clause is not None:
            # One notification meaning for immediate, delayed, reminder and
            # scheduled requests; the recipient stays semantic here and is
            # materialized into exact targets before persistence.
            notification = notification_action(
                clause, clause.resolved_message(), context.entities
            )
            return (notification,) if notification is not None else None
        document = analyse_language(text, context.entities)
        interpreted = SemanticInterpreter.interpret(
            document,
            context.entities,
            context.world_model,
            compile_result=True,
            resolve_registry=False,
        )
        direct = self._interpreted_match_result(interpreted, context.entities)
        # The genus reading applies to delayed and scheduled actions exactly
        # as to immediate ones: "die Rollläden" never reaches the garage door.
        narrowed = self._ontology_understanding(document, context.entities, None, direct)
        if narrowed is _REFUSED:
            return None
        if narrowed is not None:
            commands = narrowed.commands if isinstance(narrowed, CommandPlan) else (narrowed,)
            lifted = tuple(
                self._action_from_direct_match(command, context.entities) for command in commands
            )
            if lifted and all(item is not None for item in lifted):
                return tuple(item for item in lifted if item is not None)
            return None
        action = self._action_from_direct_match(direct, context.entities)
        if action is not None:
            return (action,)
        compatibility = self.match(text, context.entities, context.world_model)
        action = self._action_from_direct_match(
            compatibility, context.entities
        )
        if action is not None:
            return (action,)
        return self._automation_action_parser.parse(text, context)

    def parse_automation_actions(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
    ) -> tuple[ActionModel | ActionGroup, ...] | None:
        """Parse one replacement action through the shared semantic pipeline."""
        return self._parse_action_semantically(
            text, create_parse_context(entities, world_model=world_model)
        )

    def parse_automation_trigger(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
    ) -> TriggerModel | None:
        normalized = normalize(text)
        normalized = re.sub(
            r"^(?:wenn\s+)?(?:die\s+)?sonne\s+(?:untergeht|untergegangen\s+ist)$",
            "bei sonnenuntergang",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"^(?:wenn\s+)?(?:die\s+)?sonne\s+(?:aufgeht|aufgegangen\s+ist)$",
            "bei sonnenaufgang",
            normalized,
            flags=re.IGNORECASE,
        )
        if not _AUTOMATION_TRIGGER_RE.search(normalized):
            normalized = "wenn " + normalized
        return self._automation_trigger_parser.parse(
            normalized, create_parse_context(entities, world_model=world_model)
        )

    def parse_automation_condition(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
    ) -> ConditionNode | None:
        normalized = normalize(text)
        normalized = re.sub(r"^nur\s+", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"^dass\s+", "wenn ", normalized, flags=re.IGNORECASE)
        if not has_word(normalized, "wenn", "falls", "sofern"):
            normalized = "wenn " + normalized
        return self._automation_condition_parser.parse(
            normalized, create_parse_context(entities, world_model=world_model)
        )

    def match_automation(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        context: ConversationContext | None = None,
    ) -> AutomationMatchResult | AutomationClarificationResult | None:
        """Match a combined spoken automation sentence ("Wenn das
        Küchenfenster geöffnet wird, schalte das Küchenlicht ein.") into an
        ``AutomationModel`` (Integration Wave Migration Step 3). Called live
        from ``conversation.py`` since Migration Step 4, after
        ``match_command_followup()`` and before the plain ``match()``
        fallback (see ``NluConversationEntity._async_handle_message()``).

        Scope boundary (Integration Plan Section 9, stated plainly): this
        method only ever produces and validates an ``AutomationModel`` - it
        never builds HA Automation YAML and never calls
        ``hass.services.async_call()``. Existing HA write access is
        completely unchanged by this method's existence.

        Gated by ``_AUTOMATION_TRIGGER_RE`` first - the same cheap,
        collision-free (verified empirically against all 14 existing live
        grammars) regex scaffold ``automation_trigger_parser.py`` already
        defined - so an ordinary command/query sentence never pays the cost
        of ``split_trigger_action()``/two extra parser passes below it.
        Deliberately checked on the raw (not yet ``normalize()``-d) text,
        same as every other pre-check regex in this module runs on already-
        normalized text for ``_select_parser()`` - here there is no shared
        normalization step yet to hook into (this is a new, independent
        entry point, not a branch inside ``_select_parser()`` itself; see
        the Integration Plan's Section 4/Variante D).

        Trigger+action is still the primary shape ``split_trigger_action()``
        splits for - a sentence with no comma at all still returns ``None``
        here, same "never guess" rule as the rest of this engine. Since the
        Conditions Wave (2026-08-19), the trigger-clause additionally allows
        an embedded "und"-joined condition ("Wenn X und Y, tu Z."): if the
        whole trigger-clause doesn't parse as a single Trigger on its own,
        ``_split_trigger_condition()`` searches every top-level "und" split
        point for exactly one point where the left half parses as a Trigger
        and the right half as a Condition - grammar-driven (try-parse, not
        keyword-based), and refusing (``None``) on zero or more than one
        such point, same ambiguity discipline every entity resolution in
        this engine already follows. No second ambiguity gate is introduced
        anywhere else - this fallback only ever runs inside this method, for
        this trigger-clause.
        """
        # Strip optional automation meta-shells ("Erstelle eine Automation, die...")
        # before clause analysis, so trigger/action boundaries are recognized correctly.
        automation_shell_stripped_text, shell_was_present = strip_automation_shell(text)

        utterance = analyse_utterance(automation_shell_stripped_text)
        if utterance.speech_act is SpeechAct.QUERY and (
            _WH_QUESTION_RE.match(automation_shell_stripped_text)
            or not (
                _SAY_REQUEST_RE.match(automation_shell_stripped_text)
                or _MODAL_REQUEST_RE.search(automation_shell_stripped_text)
            )
        ):
            return None
        if _NEGATED_NOTIFICATION_RE.search(automation_shell_stripped_text):
            # "Benachrichtige mich nicht, wenn ..." asks for the opposite of
            # an automation; no reading may turn it into one.
            return None
        composed = self._compose_with_run_limits(
            automation_shell_stripped_text, text, entities, world_model, context
        )
        if composed is not None:
            return composed
        if utterance.speech_act is SpeechAct.QUERY:
            return None
        if not _AUTOMATION_TRIGGER_RE.search(automation_shell_stripped_text):
            return None
        if only_quoted_connectors(automation_shell_stripped_text):
            # "Auf dem Zettel steht „... wenn ...“": reported, inert text.
            return None

        normalized = normalize(automation_shell_stripped_text)
        repeat_match = _AUTOMATION_REPEAT_RE.search(normalized)
        max_runs = None
        if repeat_match is not None:
            raw_count = (repeat_match.group("separate") or repeat_match.group("joined")).casefold()
            max_runs = int(raw_count) if raw_count.isdigit() else _REPEAT_COUNTS[raw_count]
            normalized = _AUTOMATION_REPEAT_RE.sub(" ", normalized)
            normalized = re.sub(r"\s+", " ", normalized).strip()
        once = bool(_AUTOMATION_ONCE_RE.search(normalized))
        if once:
            # Strip the qualifier itself before trigger/action splitting -
            # it is not part of either clause's own grammar (mirrors
            # ``normalize()``'s own filler-word-then-whitespace-squeeze
            # pattern, see nlu/normalize.py).
            normalized = _AUTOMATION_ONCE_RE.sub(" ", normalized)
            normalized = re.sub(r"\s+", " ", normalized).strip()

        last_entities = context.last_entities if context is not None else ()
        last_area = context.last_area if context is not None else None
        parse_context = create_parse_context(
            entities,
            world_model=world_model,
            last_entities=tuple(last_entities),
            last_area=last_area,
        )

        # GermanStructuralAnalysis owns the clause boundary. The legacy comma
        # splitter remains a compatibility fallback only for shapes the
        # structural layer deliberately cannot classify yet.
        automation_document = analyse_language(
            normalized, entities, include_registry_compounds=False
        )
        split = split_automation_document(automation_document)
        if split is None:
            split = split_trigger_action(normalized)
            if split is not None and re.match(r"dass\b", split[1], re.IGNORECASE):
                # A comma before "dass" opens the complement of the preceding
                # verb ("schick mir eine Nachricht, dass ..."); it is never
                # the trigger/action boundary.
                split = None
        if split is not None:
            # ``split_trigger_action`` assumes trigger-then-action order
            # around the first comma. When the action actually comes first
            # ("Benachrichtige Philipp, wenn ..."), the first half won't
            # parse as a Trigger while the second half will - and the first
            # half parses as a valid Action on its own. Swap the halves in
            # that one specific, verified case rather than guessing.
            first_half, second_half = split
            if self._automation_trigger_parser.parse(first_half, parse_context) is None:
                second_as_trigger = self._automation_trigger_parser.parse(
                    second_half, parse_context
                )
                if second_as_trigger is not None:
                    first_as_action = self._parse_action_semantically(
                        first_half, parse_context
                    )
                    if not first_as_action:
                        notification = notification_action_from_request(
                            first_half, second_half, entities
                        )
                        first_as_action = (notification,) if notification is not None else None
                    if first_as_action:
                        split = (second_half, first_half)
        if split is None:
            candidates: list[tuple[str, str]] = []
            # The common utterance model already knows an action-first
            # clause followed by a trigger clause, independent of the
            # concrete action vocabulary. Let the established trigger and
            # action compilers validate the two meanings; no service call is
            # built here.
            action_clause = next(
                (clause for clause in utterance.clauses if clause.role is ClauseRole.ACTION),
                None,
            )
            trigger_clause = next(
                (clause for clause in utterance.clauses if clause.role is ClauseRole.TRIGGER),
                None,
            )
            if action_clause is not None and trigger_clause is not None:
                semantic_trigger = (
                    f"{trigger_clause.connector or 'wenn'} {trigger_clause.text}"
                )
                if (
                    self._automation_trigger_parser.parse(semantic_trigger, parse_context)
                    is not None
                    and self._parse_action_semantically(action_clause.text, parse_context)
                ):
                    candidates.append((semantic_trigger, action_clause.text))
            for marker in re.finditer(
                r"\b(schalte|mach|fahre|öffne|schließe|stelle|setze|starte|aktiviere|führe|drehe)\b",
                normalized,
                re.IGNORECASE,
            ):
                trigger_candidate = normalized[: marker.start()].strip(" ,")
                action_candidate = normalized[marker.start() :].strip()
                if (
                    trigger_candidate
                    and self._automation_trigger_parser.parse(trigger_candidate, parse_context) is not None
                    and self._parse_action_semantically(action_candidate, parse_context)
                ):
                    candidates.append((trigger_candidate, action_candidate))
            for marker in re.finditer(r"\s+(wenn|sobald|falls)\s+", normalized, re.IGNORECASE):
                action_candidate = normalized[: marker.start()].strip(" ,")
                trigger_candidate = normalized[marker.start() :].strip(" ,")
                parsed_trigger = self._automation_trigger_parser.parse(
                    trigger_candidate, parse_context
                )
                parsed_actions = self._parse_action_semantically(
                    action_candidate, parse_context
                )
                if not parsed_actions:
                    notification = notification_action_from_request(
                        action_candidate, trigger_candidate, entities
                    )
                    parsed_actions = (notification,) if notification is not None else None
                if (
                    action_candidate
                    and parsed_trigger is not None
                    and parsed_actions
                ):
                    candidates.append((trigger_candidate, action_candidate))
            if re.match(r"(?:wenn|sobald|falls)\b", normalized, re.IGNORECASE):
                # Trigger-first without a comma ("Wenn im Wohnzimmer ein
                # Fenster aufgeht benachrichtige mich"): the clause boundary
                # is the one word position where the remainder is a complete
                # notification clause *and* the prefix a complete trigger.
                for word in list(re.finditer(r"\S+", normalized))[2:]:
                    action_candidate = normalized[word.start():].strip(" ,")
                    if parse_notification_clause(action_candidate) is None:
                        continue
                    trigger_candidate = normalized[: word.start()].strip(" ,")
                    if self._automation_trigger_parser.parse(
                        trigger_candidate, parse_context
                    ) is not None:
                        candidates.append((trigger_candidate, action_candidate))
            candidates = list(dict.fromkeys(candidates))
            if len(candidates) != 1:
                # Fallback for notification patterns trailing in trigger-first clauses:
                # "sobald das Fenster geöffnet wird eine Push-Nachricht an Philipp schicken"
                # Extract trailing notification action, then try to parse the trigger alone.
                split_candidate = self._try_extract_notification_from_trigger_clause(
                    normalized, parse_context, entities
                )
                if split_candidate is not None:
                    candidates.append(split_candidate)
                candidates = list(dict.fromkeys(candidates))
            if len(candidates) != 1:
                return None
            split = candidates[0]
        trigger_text, action_text = split

        condition_node: ConditionNode | None = None
        trigger = self._typed_trigger(trigger_text, entities) or self._automation_trigger_parser.parse(
            trigger_text, parse_context
        )
        triggers: tuple[TriggerModel, ...] = ()
        if trigger is not None:
            triggers = (trigger,)
        else:
            parts = re.split(
                r"\s+oder\s+(?=(?:wenn|sobald|falls)\b)",
                trigger_text,
                flags=re.IGNORECASE,
            )
            if len(parts) > 1:
                parsed = tuple(
                    candidate
                    for part in parts
                    if (candidate := self._automation_trigger_parser.parse(part, parse_context))
                    is not None
                )
                if len(parsed) == len(parts):
                    triggers = tuple(
                        replace(candidate, trigger_id=f"ausloeser_{index}")
                        for index, candidate in enumerate(parsed, start=1)
                    )
        if not triggers:
            split_result = self._split_structured_trigger_condition(
                automation_document, parse_context
            ) or self._split_trigger_condition(trigger_text, parse_context)
            if split_result is None:
                return None
            trigger, condition_node = split_result
            triggers = (trigger,)

        actions = self._parse_action_or_notification(
            action_text, trigger_text, entities, parse_context,
            triggers[0] if len(triggers) == 1 else None,
        )
        if not actions:
            return None

        conditions = (condition_node,) if condition_node is not None else ()
        model = AutomationModel(
            triggers=triggers, conditions=conditions, actions=actions, source_text=text,
            once=once, max_runs=max_runs,
        )
        validation_error = validate_automation(model)
        response_text = render_automation_tree(model)
        if validation_error is not None:
            response_text = f"{response_text}\nvalidation_error: {validation_error.name}"
        return AutomationMatchResult(model=model, response_text=response_text, validation_error=validation_error)

    def _typed_trigger(
        self, trigger_text: str, entities: list[EntitySnapshot]
    ) -> TriggerModel | None:
        """Typed (class + area) grounding of an entity event, if it resolves.

        Preferred over the grammar parser's free-name slot, which resolves
        names fuzzily ("Schlafzimmerfenster" must never become a fan).
        """
        roles = read_event_roles(trigger_text)
        if roles.conditions:
            return None
        grounded = ground_event(roles, entities)
        if grounded.status is GroundingStatus.RESOLVED:
            return grounded.trigger
        return None

    def _composition_readers(
        self,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None,
        context: ConversationContext | None,
    ) -> Readers:
        parse_context = create_parse_context(
            entities,
            world_model=world_model,
            last_entities=tuple(context.last_entities) if context is not None else (),
            last_area=context.last_area if context is not None else None,
        )
        plain: list[ParseContext] = []

        def read_action(span: str) -> tuple[ActionModel | ActionGroup, ...] | None:
            # The same established action parsers; the registry-free context
            # is a second reading for spans the world-model index rejects.
            parsed = self._parse_action_semantically(span, parse_context)
            if parsed or world_model is None:
                return parsed
            if not plain:
                plain.append(create_parse_context(entities))
            return self._parse_action_semantically(span, plain[0])

        return Readers(
            trigger=lambda span: self._automation_trigger_parser.parse(span, parse_context),
            condition=lambda span: self._automation_condition_parser.parse(span, parse_context),
            action=read_action,
        )

    def _compose_with_run_limits(
        self,
        text: str,
        source_text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None,
        context: ConversationContext | None,
    ) -> AutomationMatchResult | AutomationClarificationResult | None:
        """"einmalig"/"dreimal" qualify the whole automation, not a clause."""
        repeat_match = _AUTOMATION_REPEAT_RE.search(text)
        max_runs: int | None = None
        if repeat_match is not None:
            raw_count = (repeat_match.group("separate") or repeat_match.group("joined")).casefold()
            max_runs = int(raw_count) if raw_count.isdigit() else _REPEAT_COUNTS[raw_count]
            text = re.sub(r"\s+", " ", _AUTOMATION_REPEAT_RE.sub(" ", text)).strip()
        once = bool(_AUTOMATION_ONCE_RE.search(text))
        if once:
            text = re.sub(r"\s+", " ", _AUTOMATION_ONCE_RE.sub(" ", text)).strip()
        result = self.compose_event_automation(text, entities, world_model, context)
        if isinstance(result, AutomationMatchResult) and (once or max_runs is not None):
            model = replace(result.model, once=once, max_runs=max_runs, source_text=source_text)
            validation_error = validate_automation(model)
            response_text = render_automation_tree(model)
            if validation_error is not None:
                response_text = f"{response_text}\nvalidation_error: {validation_error.name}"
            return AutomationMatchResult(model, response_text, validation_error)
        return result

    def compose_event_automation(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        context: ConversationContext | None = None,
    ) -> AutomationMatchResult | AutomationClarificationResult | None:
        """7.2.0 compositional EVENT clause + ACTION clause reading (both orders)."""
        outcome = compose_event_automation(
            text, entities, self._composition_readers(entities, world_model, context)
        )
        return self._composition_result(outcome)

    @staticmethod
    def _composition_result(
        outcome: CompositionOutcome | None,
    ) -> AutomationMatchResult | AutomationClarificationResult | None:
        if outcome is None:
            return None
        log_composition_trace(outcome.trace)
        if outcome.kind is OutcomeKind.AUTOMATION and outcome.model is not None:
            # The engine's validator stays the single validation authority.
            validation_error = validate_automation(outcome.model)
            response_text = render_automation_tree(outcome.model)
            if validation_error is not None:
                response_text = f"{response_text}\nvalidation_error: {validation_error.name}"
            return AutomationMatchResult(
                model=outcome.model,
                response_text=response_text,
                validation_error=validation_error,
            )
        return AutomationClarificationResult(
            response_text=outcome.speech or unsupported_text(None),
            clarification=outcome.clarification,
            trace=outcome.trace,
        )

    def resolve_event_clarification(
        self,
        text: str,
        pending: EventClarification,
        entities: list[EntitySnapshot],
    ) -> AutomationMatchResult | AutomationClarificationResult | None:
        """Continue a clarified event-notification draft ("Die linke.")."""
        return self._composition_result(
            resolve_event_clarification(
                text, pending, entities, self._composition_readers(entities, None, None)
            )
        )

    def match_automation_draft_start(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        context: ConversationContext | None = None,
    ) -> AutomationDraftMatchResult | None:
        """Recognize a complete trigger whose action was left for a reply."""
        if not _AUTOMATION_TRIGGER_RE.search(text):
            return None
        parse_context = create_parse_context(
            entities,
            world_model=world_model,
            last_entities=context.last_entities if context is not None else (),
            last_area=context.last_area if context is not None else None,
        )
        normalized = normalize(text).strip(" ,")
        # Only a trigger on its own starts a draft. Anything after the first
        # comma is the action the full automation match could not read; taking
        # the whole sentence as a trigger would silently change its meaning.
        _, separator, remainder = normalized.partition(",")
        if separator and remainder.strip(" .!?").casefold() not in {"", "dann"}:
            return None
        trigger = self._automation_trigger_parser.parse(
            normalized.split(",", 1)[0].strip(), parse_context
        )
        if trigger is None:
            return None
        return AutomationDraftMatchResult(trigger=trigger, source_text=text)

    def complete_automation_draft(
        self,
        text: str,
        trigger: TriggerModel,
        source_text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        context: ConversationContext | None = None,
    ) -> AutomationMatchResult | None:
        """Add a spoken action to a stored trigger and validate the model."""
        parse_context = create_parse_context(
            entities,
            world_model=world_model,
            last_entities=context.last_entities if context is not None else (),
            last_area=context.last_area if context is not None else None,
        )
        actions = self._parse_action_semantically(normalize(text), parse_context)
        if not actions:
            return None
        model = AutomationModel(
            triggers=(trigger,),
            actions=actions,
            source_text=f"{source_text}, {text}",
        )
        validation_error = validate_automation(model)
        response_text = render_automation_tree(model)
        if validation_error is not None:
            response_text = f"{response_text}\nvalidation_error: {validation_error.name}"
        return AutomationMatchResult(model, response_text, validation_error)

    def revise_pending_automation(
        self,
        text: str,
        model: AutomationModel,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
    ) -> AutomationMatchResult | None:
        """Apply a bounded spoken edit to an unconfirmed automation preview."""
        normalized = normalize(text)
        updated: AutomationModel | None = None

        time_match = re.fullmatch(
            r"(?:ändere|änder|verschiebe|setz(?:e)?)\s+(?:die\s+)?(?:uhrzeit|zeit)?\s*"
            r"(?:auf\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{1,2}))?\s*uhr[?.!]*",
            normalized,
            re.IGNORECASE,
        )
        if time_match is not None:
            hour = int(time_match.group("hour"))
            minute = int(time_match.group("minute") or 0)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return None
            if model.calendar_schedule is not None:
                updated = replace(
                    model,
                    calendar_schedule=replace(
                        model.calendar_schedule, hour=hour, minute=minute
                    ),
                )
            elif len(model.triggers) == 1 and model.triggers[0].type is TriggerType.TIME:
                updated = replace(
                    model,
                    triggers=(
                        replace(
                            model.triggers[0],
                            time_hour=hour,
                            time_minute=minute,
                            time_second=0,
                        ),
                    ),
                )
            else:
                return None

        repeat_match = re.fullmatch(
            r"(?:führe\s+(?:sie|die\s+automation)\s+)?(?:doch\s+)?nur\s+"
            r"(?P<count>\d+|einmal|zweimal|dreimal|viermal|fünfmal)"
            r"(?:\s+(?:aus|ausführen))?[?.!]*",
            normalized,
            re.IGNORECASE,
        )
        if repeat_match is not None:
            raw = repeat_match.group("count").casefold()
            counts = {"einmal": 1, "zweimal": 2, "dreimal": 3, "viermal": 4, "fünfmal": 5}
            count = int(raw) if raw.isdigit() else counts[raw]
            if count <= 0:
                return None
            updated = replace(
                model,
                once=count == 1,
                max_runs=None if count == 1 else count,
            )

        if re.fullmatch(
            r"(?:entferne|lösch(?:e)?)\s+(?:die\s+)?bedingung[?.!]*",
            normalized,
            re.IGNORECASE,
        ):
            if not model.conditions:
                return None
            updated = replace(model, conditions=())

        parse_context = create_parse_context(entities, world_model=world_model)
        action_match = re.fullmatch(
            r"füge\s+(?:als\s+)?(?:weitere\s+|zweite\s+)?aktion\s+(.+?)\s+hinzu[?.!]*",
            normalized,
            re.IGNORECASE,
        )
        if action_match is not None:
            actions = self._parse_action_semantically(
                action_match.group(1), parse_context
            )
            if not actions:
                return None
            updated = replace(model, actions=(*model.actions, *actions))

        condition_match = re.fullmatch(
            r"füge\s+(?:die\s+)?bedingung\s+(.+?)\s+hinzu[?.!]*",
            normalized,
            re.IGNORECASE,
        )
        if condition_match is not None:
            condition = self._automation_condition_parser.parse(
                condition_match.group(1), parse_context
            )
            if condition is None:
                return None
            updated = replace(model, conditions=(*model.conditions, condition))

        if updated is None:
            return None
        validation_error = validate_automation(updated)
        return AutomationMatchResult(
            updated,
            render_automation_tree(updated),
            validation_error,
        )

    def warm_up(self) -> None:
        """Trigger hassil's lazily loaded, file-backed number rules once.

        Call from an executor thread: the first recognition of a spoken number
        reads unicode_rbnf's language XML, which must not happen inside Home
        Assistant's event loop during a conversation turn.
        """
        self._relative_time_command_parser.decompose(
            "in fünf Minuten schalte das Licht ein"
        )

    def match_immediate_notification(self, text: str) -> NotificationClause | None:
        """An explicit notification to be sent *now* ("Schick mir eine
        Testbenachrichtigung").

        Only a complete notification clause qualifies; any trigger connector
        or time phrase belongs to the automation/one-shot paths instead, so
        "Benachrichtige mich, wenn ..." or "... in 10 Sekunden ..." can never
        be delivered immediately.
        """
        if (
            _IMMEDIATE_NOTIFICATION_BLOCKER_RE.search(text)
            or _RELATIVE_TIME_RE.search(text)
            or _CALENDAR_TIME_RE.search(text)
        ):
            return None
        clause = parse_notification_clause(text)
        if clause is None:
            return None
        if clause.message is None and not _NOTIFICATION_REQUEST_VERB_RE.search(text):
            # "Bescheid." / "Nachricht an mich." alone are fragments, not a
            # request to send something now; with dictated content ("Nachricht
            # an Anna: Bin gleich da.") the request is complete.
            return None
        if (
            clause.recipient_kind is NotificationRecipientKind.EXPLICIT_TARGET
            and clause.message is None
        ):
            # A message to another person needs its content.
            return None
        return clause

    def match_relative_time_automation(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        context: ConversationContext | None = None,
    ) -> AutomationMatchResult | None:
        """Match a single-clause spoken command carrying a relative-time
        offset ("Fahre in 5 Minuten die Wohnzimmer Rolllade auf 30 Prozent")
        into an ``AutomationModel`` with one semantic
        ``TriggerType.RELATIVE_TIME`` trigger. The confirmation handler
        anchors it to an absolute, date-guarded ``TIME`` trigger. Called live from
        ``conversation.py`` after ``match_automation()`` and before the plain
        ``match()`` fallback: this sentence shape has no comma, so
        ``split_trigger_action()`` can never split it into trigger+action
        clauses (see ``relative_time_command.yaml``'s own comment) - a
        structurally new, single-clause entry point, not a branch inside
        ``match_automation()``.

        Always ``once=True``: a relative "in N Minuten" offset is inherently
        a one-shot request - there is no spoken way to ask for a *recurring*
        "in 5 Minuten" automation - so this reuses Wave 12's self-delete
        mechanism unconditionally (Regel 6, see
        ``ha_automation_generator.py``'s ``once``-handling) rather than
        detecting/stripping an "einmalig" qualifier like ``match_automation()``
        does.

        Gated by ``_RELATIVE_TIME_RE`` first, same cheap pre-check reasoning
        ``_AUTOMATION_TRIGGER_RE`` documents for ``match_automation()``.
        """
        repair_document = analyse_language(text, entities)
        repaired = repaired_temporal_command(repair_document)
        if repaired is not None and repaired != text:
            projected = self.match_relative_time_automation(
                repaired, entities, world_model, context
            )
            if projected is None:
                return None
            return replace(
                projected,
                model=replace(projected.model, source_text=text),
            )
        if not _RELATIVE_TIME_RE.search(text) and split_relative_delay(text) is None:
            return None

        normalized = normalize(text)

        last_entities = context.last_entities if context is not None else ()
        last_area = context.last_area if context is not None else None
        parse_context = create_parse_context(
            entities,
            world_model=world_model,
            last_entities=tuple(last_entities),
            last_area=last_area,
        )

        parsed = None
        decomposed = self._relative_time_command_parser.decompose(normalized)
        if decomposed is None:
            # Any word order: "Schließe in 150 Minuten die Rollläden".
            decomposed = split_relative_delay(normalized)
        if decomposed is not None:
            command_text, offset_seconds = decomposed
            actions = self._parse_action_semantically(command_text, parse_context)
            if actions is not None:
                parsed = (actions, offset_seconds)
        if parsed is None:
            parsed = self._relative_time_command_parser.parse(normalized, parse_context)
        if parsed is None:
            return None
        actions, offset_seconds = parsed

        trigger = TriggerModel(
            type=TriggerType.RELATIVE_TIME,
            relative_offset_seconds=offset_seconds,
        )

        model = AutomationModel(triggers=(trigger,), actions=actions, source_text=text, once=True)
        validation_error = validate_automation(model)
        response_text = render_automation_tree(model)
        if validation_error is not None:
            response_text = f"{response_text}\nvalidation_error: {validation_error.name}"
        return AutomationMatchResult(model=model, response_text=response_text, validation_error=validation_error)

    def match_recurring_time_automation(
        self,
        text: str,
        entities: list[EntitySnapshot],
        now: datetime,
        world_model: WorldModel | None = None,
        context: ConversationContext | None = None,
    ) -> AutomationMatchResult | None:
        """Build native time+weekday rules without enumerating word orders."""
        schedule = _RECURRING_TIME_RE.search(text)
        if schedule is None:
            return None
        hour = int(schedule.group("hour"))
        minute = int(schedule.group("minute") or 0)
        if hour > 23 or minute > 59:
            return None
        action_text = (text[:schedule.start()] + " " + text[schedule.end():]).strip(" ,.")
        action_text = re.sub(
            r"\bnur\s+zwischen\s+\w+\s+und\s+\w+\b", " ", action_text,
            flags=re.IGNORECASE,
        )
        action_text = re.sub(r"^(?:dann\s+)", "", action_text, flags=re.IGNORECASE)
        parse_context = create_parse_context(
            entities,
            world_model=world_model,
            last_entities=tuple(context.last_entities) if context is not None else (),
            last_area=context.last_area if context is not None else None,
        )
        actions = self._parse_action_semantically(action_text, parse_context)
        if not actions:
            return None
        day = schedule.group("day").casefold()
        conditions = [
            ConditionNode(condition=ConditionModel(
                type=ConditionType.WEEKDAY,
                weekdays=_RECURRING_WEEKDAYS[day],
            ))
        ]
        if schedule.group("interval"):
            # "Jeden zweiten Samstag" starts with the next occurrence and
            # then follows ISO-week parity. The anchor is fixed at creation.
            delta = (_weekday_number(day) - now.weekday()) % 7
            anchor = (now + timedelta(days=delta)).isocalendar().week % 2
            conditions.append(ConditionNode(condition=ConditionModel(
                type=ConditionType.TEMPLATE,
                raw_state=f"{{{{ (now().isocalendar().week % 2) == {anchor} }}}}",
            )))
        month_range = re.search(
            r"\bnur\s+zwischen\s+(?P<start>januar|februar|märz|maerz|april|mai|juni|"
            r"juli|august|september|oktober|november|dezember)\s+und\s+"
            r"(?P<end>januar|februar|märz|maerz|april|mai|juni|juli|august|september|"
            r"oktober|november|dezember)\b",
            text, re.IGNORECASE,
        )
        if month_range:
            months = {"januar": 1, "februar": 2, "märz": 3, "maerz": 3,
                      "april": 4, "mai": 5, "juni": 6, "juli": 7,
                      "august": 8, "september": 9, "oktober": 10,
                      "november": 11, "dezember": 12}
            start = months[month_range.group("start").casefold()]
            end = months[month_range.group("end").casefold()]
            expression = (
                f"{start} <= now().month <= {end}" if start <= end
                else f"now().month >= {start} or now().month <= {end}"
            )
            conditions.append(ConditionNode(condition=ConditionModel(
                type=ConditionType.TEMPLATE,
                raw_state="{{ " + expression + " }}",
            )))
        model = AutomationModel(
            triggers=(TriggerModel(type=TriggerType.TIME, time_hour=hour, time_minute=minute),),
            conditions=tuple(conditions), actions=actions, source_text=text,
        )
        error = validate_automation(model)
        return AutomationMatchResult(model, render_automation_tree(model), error)

    def match_persistent_state_automation(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        context: ConversationContext | None = None,
    ) -> AutomationMatchResult | None:
        """Understand "offen bleibt" as HA trigger ``for``, not an action delay."""
        persistent = _PERSISTENT_STATE_RE.search(text)
        if persistent is None:
            return None
        raw_amount = persistent.group("amount").casefold()
        amount = int(raw_amount) if raw_amount.isdigit() else _SMALL_NUMBERS.get(raw_amount)
        if amount is None:
            return None
        unit = persistent.group("unit").casefold()
        seconds = amount * (3600 if unit.startswith("st") else 60 if unit.startswith("min") else 1)
        state = persistent.group("state")
        replacement = f"{state} wird"
        rewritten = text[:persistent.start()] + replacement + text[persistent.end():]
        base = self.match_automation(rewritten, entities, world_model, context)
        if (
            not isinstance(base, AutomationMatchResult)
            or base.validation_error is not None
            or len(base.model.triggers) != 1
        ):
            return None
        trigger = base.model.triggers[0]
        if trigger.type not in {TriggerType.STATE, TriggerType.NUMERIC_STATE}:
            return None
        model = replace(
            base.model,
            triggers=(replace(trigger, for_seconds=seconds),),
            source_text=text,
        )
        error = validate_automation(model)
        return AutomationMatchResult(model, render_automation_tree(model), error)

    def match_repeated_event_automation(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        context: ConversationContext | None = None,
    ) -> AutomationMatchResult | None:
        """Require the same state transition twice inside a bounded window."""
        trigger_clause = text.split(",", 1)[0]
        repeated = _REPEATED_EVENT_RE.search(trigger_clause)
        window = _WITHIN_WINDOW_RE.search(trigger_clause)
        if repeated is None or window is None:
            return None
        seconds = parse_duration_seconds(window.group())
        if seconds is None:
            return None
        rewritten = text
        for match in sorted((repeated, window), key=lambda item: item.start(), reverse=True):
            rewritten = rewritten[:match.start()] + " " + rewritten[match.end():]
        rewritten = re.sub(r"\s+", " ", rewritten)
        base = self.match_automation(rewritten, entities, world_model, context)
        if (
            not isinstance(base, AutomationMatchResult)
            or base.validation_error is not None
            or len(base.model.triggers) != 1
        ):
            return None
        trigger = base.model.triggers[0]
        if trigger.type not in {TriggerType.STATE, TriggerType.NUMERIC_STATE}:
            return None
        model = replace(
            base.model,
            triggers=(replace(trigger, repeat_within_seconds=seconds),),
            source_text=text,
        )
        error = validate_automation(model)
        return AutomationMatchResult(model, render_automation_tree(model), error)

    @staticmethod
    def _action_from_direct_match(
        result: MatchResult | CommandPlan | None,
        available_entities: list[EntitySnapshot],
    ) -> ActionModel | None:
        """Translate one validated direct command into an automation action.

        This is the semantic bridge used only after a relative-time phrase
        has been removed. It reuses the direct-command understanding instead
        of maintaining a second list of delayed-action sentences.
        """
        if not isinstance(result, MatchResult) or result.plan is None or result.command is None:
            return None
        entities = result.command.entities
        if not entities or len({entity.domain for entity in entities}) != 1:
            return None

        domain = entities[0].domain
        frame = result.command.source_frame
        if len(entities) == 1:
            entity = entities[0]
            equivalent = [
                candidate
                for candidate in available_entities
                if candidate.domain == entity.domain
                and candidate.device_class == entity.device_class
                and candidate.area_id == entity.area_id
            ]
            if entity.area_id is not None and len(equivalent) == 1:
                target = TriggerTarget(
                    domain=domain,
                    device_class=entity.device_class,
                    area_id=entity.area_id,
                )
            else:
                target = TriggerTarget(domain=domain, entity_id=entity.entity_id)
        elif (
            result.command.area is not None and frame.quantifier is not None
            and {entity.entity_id for entity in entities} == {
                candidate.entity_id for candidate in available_entities
                if candidate.domain == domain
                and candidate.area_id == result.command.area.area_id
            }
        ):
            target = TriggerTarget(
                domain=domain,
                area_id=result.command.area.area_id,
                quantifier=frame.quantifier.kind,
                quantifier_count=frame.quantifier.value,
            )
        elif frame.quantifier is not None:
            floor_ids = {entity.floor_id for entity in entities}
            if len(floor_ids) != 1 or None in floor_ids or {
                entity.entity_id for entity in entities
            } != {
                candidate.entity_id for candidate in available_entities
                if candidate.domain == domain and candidate.floor_id in floor_ids
            }:
                # TriggerTarget must retain a real scope; never turn an
                # arbitrary concrete list into an all-house automation.
                # The exact, validated list keeps it ("die Rollläden" ->
                # the eight shutters, not garage door and awning).
                target = TriggerTarget(
                    domain=domain,
                    entity_ids=tuple(entity.entity_id for entity in entities),
                )
            else:
                target = TriggerTarget(
                    domain=domain,
                    floor_id=next(iter(floor_ids)),
                    quantifier=frame.quantifier.kind,
                    quantifier_count=frame.quantifier.value,
                )
        else:
            # An exact concrete list stays exact; it is never broadened to
            # every entity of the domain.
            target = TriggerTarget(
                domain=domain,
                entity_ids=tuple(entity.entity_id for entity in entities),
            )

        plan = result.plan
        if plan.domain == "cover" and plan.service == "open_cover":
            return ActionModel(type=ActionType.TURN_ON, target=target)
        if plan.domain == "cover" and plan.service == "close_cover":
            return ActionModel(type=ActionType.TURN_OFF, target=target)
        if plan.domain == "cover" and plan.service == "set_cover_position":
            position = plan.data.get("position")
            return (
                ActionModel(type=ActionType.SET_POSITION, target=target, value=float(position))
                if isinstance(position, (int, float))
                else None
            )
        if plan.domain == "light" and plan.service == "turn_on":
            brightness = plan.data.get("brightness_pct")
            if isinstance(brightness, (int, float)):
                return ActionModel(
                    type=ActionType.SET_BRIGHTNESS,
                    target=target,
                    value=float(brightness),
                )
        if plan.domain == "climate" and plan.service == "set_temperature":
            temperature = plan.data.get("temperature")
            if isinstance(temperature, (int, float)):
                return ActionModel(
                    type=ActionType.SET_TEMPERATURE,
                    target=target,
                    value=float(temperature),
                )
        if validate_registered_operation(
            plan.domain,
            plan.service,
            plan.data,
            frozenset({domain}),
        ):
            return ActionModel(
                type=ActionType.REGISTERED_SERVICE,
                target=target,
                service_domain=plan.domain,
                service_name=plan.service,
                service_data=dict(plan.data),
            )
        if plan.service == "turn_on":
            return ActionModel(type=ActionType.TURN_ON, target=target)
        if plan.service == "turn_off":
            return ActionModel(type=ActionType.TURN_OFF, target=target)
        return None

    def match_calendar_event_automation(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        context: ConversationContext | None = None,
    ) -> AutomationMatchResult | None:
        """Create an automation driven by a real HA calendar event."""
        draft = parse_calendar_automation_draft(text, entities)
        if draft is None:
            return None
        parse_context = create_parse_context(
            entities,
            world_model=world_model,
            last_entities=tuple(context.last_entities) if context is not None else (),
            last_area=context.last_area if context is not None else None,
        )
        event_message = (
            f"Kalendertermin {draft.summary_contains} {draft.event == 'start' and 'beginnt' or 'endet'}."
            if draft.summary_contains
            else f"Ein Kalendertermin {draft.event == 'start' and 'beginnt' or 'endet'}."
        )
        clause = parse_notification_clause(draft.action_text)
        if clause is not None and clause.message is None:
            # "erinnere mich" / "benachrichtige mich": the event is the message.
            notification = notification_action(clause, event_message, entities)
            actions = (notification,) if notification is not None else None
        else:
            actions = self._parse_action_semantically(draft.action_text, parse_context)
        if not actions and re.search(r"\berinner(?:e|n|t)?\s+(?:mich|uns)\b", draft.action_text, re.IGNORECASE):
            actions = (ActionModel(type=ActionType.NOTIFY, message=event_message),)
        if not actions:
            return None
        conditions = (
            (
                ConditionNode(
                    condition=ConditionModel(
                        type=ConditionType.CALENDAR_EVENT,
                        raw_state=draft.summary_contains,
                    )
                ),
            )
            if draft.summary_contains
            else ()
        )
        model = AutomationModel(
            triggers=tuple(
                TriggerModel(
                    type=TriggerType.CALENDAR,
                    calendar_entity_id=calendar_entity_id,
                    calendar_event=draft.event,
                    offset_minutes=draft.offset_minutes,
                )
                for calendar_entity_id in (
                    draft.calendar_entity_ids or (draft.calendar_entity_id,)
                )
            ),
            conditions=conditions,
            actions=actions,
            source_text=text,
        )
        validation_error = validate_automation(model)
        response_text = render_automation_tree(model)
        if validation_error is not None:
            response_text = f"{response_text}\nvalidation_error: {validation_error.name}"
        return AutomationMatchResult(model, response_text, validation_error)

    def match_calendar_time_automation(
        self,
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        context: ConversationContext | None = None,
    ) -> AutomationMatchResult | None:
        """Match a date-bound command such as "morgen um 8 Uhr ..."."""
        source_text = text
        repaired = repaired_temporal_command(analyse_language(text, entities))
        if repaired is not None:
            text = repaired
        normalized = normalize(text)
        parse_context = create_parse_context(
            entities,
            world_model=world_model,
            last_entities=tuple(context.last_entities) if context is not None else (),
            last_area=context.last_area if context is not None else None,
        )
        decomposed = self._scheduled_time_command_parser.decompose(normalized)
        if decomposed is not None:
            command_text, schedule = decomposed
        else:
            temporal_document = analyse_language(normalized, entities)
            absolute = tuple(
                item for item in temporal_document.temporal
                if item.kind is TemporalKind.ABSOLUTE_TIME
            )
            if len(absolute) != 1:
                return None
            expression = absolute[0]
            first = temporal_document.tokens[expression.token_start]
            last = temporal_document.tokens[expression.token_end - 1]
            command_text = (
                normalized[:first.start] + " " + normalized[last.end:]
            ).strip(" ,.")
            hour, minute = (int(part) for part in expression.value.split(":"))
            schedule = CalendarSchedule(
                reference=CalendarReference.TODAY,
                hour=hour,
                minute=minute,
                spoken=normalized[first.start:last.end],
            )
        actions = self._parse_action_semantically(command_text, parse_context)
        if actions is None:
            return None
        model = AutomationModel(
            triggers=(TriggerModel(type=TriggerType.CALENDAR_TIME),),
            actions=actions,
            source_text=source_text,
            once=True,
            calendar_schedule=schedule,
        )
        validation_error = validate_automation(model)
        response_text = render_automation_tree(model)
        if validation_error is not None:
            response_text = f"{response_text}\nvalidation_error: {validation_error.name}"
        return AutomationMatchResult(
            model=model,
            response_text=response_text,
            validation_error=validation_error,
        )

    def _split_trigger_condition(
        self, trigger_text: str, parse_context: ParseContext
    ) -> tuple[TriggerModel, ConditionNode] | None:
        """Fallback for ``match_automation()`` when ``trigger_text`` alone
        doesn't parse as a single Trigger: search every top-level "und"
        split point (``split_on_top_level_and()``, already time-window-safe)
        for exactly one point where the left half parses as a Trigger
        (``AutomationTriggerParser``) and the right half as a Condition
        (``AutomationConditionParser``) - both already-constructed instances
        this engine uses everywhere else, no parallel parser or resolver.
        Zero or more than one successful split both refuse (``None``) -
        "niemals raten" applied to sentence structure, the same way every
        other ambiguity in this engine already refuses rather than guesses.
        """
        matches: list[tuple[TriggerModel, ConditionNode]] = []
        for left, right in split_on_top_level_and(trigger_text):
            candidate_trigger = self._automation_trigger_parser.parse(left, parse_context)
            if candidate_trigger is None:
                continue
            candidate_condition = self._automation_condition_parser.parse(right, parse_context)
            if candidate_condition is None:
                continue
            matches.append((candidate_trigger, candidate_condition))
        if len(matches) != 1:
            return None
        return matches[0]

    def _split_structured_trigger_condition(
        self, document: LanguageDocument, parse_context: ParseContext
    ) -> tuple[TriggerModel, ConditionNode] | None:
        """Project a structurally delimited IF-side into domain models.

        GermanStructuralAnalysis alone decides the clause boundaries.  The
        existing trigger and condition parsers only classify each already
        bounded clause.  Exactly one trigger assignment must succeed; an
        ambiguous assignment is refused rather than selected by order.
        """
        clause_texts = structured_automation_condition_clauses(document)
        if len(clause_texts) < 2:
            return None
        matches: list[tuple[TriggerModel, ConditionNode]] = []
        for trigger_index, trigger_text in enumerate(clause_texts):
            trigger = self.parse_automation_trigger(trigger_text, list(parse_context.entities), parse_context.world_model)
            if trigger is None:
                continue
            conditions: list[ConditionNode] = []
            for index, condition_text in enumerate(clause_texts):
                if index == trigger_index:
                    continue
                condition = self.parse_automation_condition(
                    condition_text,
                    list(parse_context.entities),
                    parse_context.world_model,
                )
                if condition is None:
                    break
                conditions.append(condition)
            else:
                condition_node = (
                    conditions[0]
                    if len(conditions) == 1
                    else ConditionNode(
                        operator=LogicalOperator.AND,
                        children=tuple(conditions),
                    )
                )
                matches.append((trigger, condition_node))
        return matches[0] if len(matches) == 1 else None

    def resolve_clarification(
        self, reply_text: str, clarification: ClarificationRequest, entities: list[EntitySnapshot]
    ) -> MatchResult | None:
        """Complete a pending clarification (v2 plan Phase 25) with the
        user's follow-up reply - e.g. "Das im Wohnzimmer." after "Welches
        Licht meinst du?". Deterministic, same "never guess" rule as the
        rest of the engine: resolves only if the reply narrows
        ``clarification.candidates`` down to exactly one entity, either by
        name (tried first, against just the candidates - not the full
        ``entities`` list, so a reply like "Küche" can't accidentally match
        some unrelated entity outside the original candidate set) or by area
        (tried second, against the full ``entities`` list since area
        resolution needs to see every entity to judge unambiguity - then
        filtered down to the candidates). Anything else - reply resolves to
        zero or more than one candidate - returns ``None``, same as any
        other non-match; the caller decides how to respond (today: the
        fixed "not understood" text, same as everywhere else in this
        engine).

        Besides the basic ``INTENTS`` this also restores already-parsed
        parameters for query and contextual setpoint clarifications. That is
        required when "22 Grad" was understood before several thermostats
        were found: selecting one thermostat must retain the 22-degree value.
        """
        normalized = normalize(reply_text)
        normalized = _MEANT_PREFIX_RE.sub("", normalized).strip()

        name_resolved = resolve_entity(normalized, list(clarification.candidates))
        entity = name_resolved.entity if name_resolved.status is ResolveStatus.OK else None

        if entity is None:
            area_resolved = resolve_area_name(normalized, entities)
            if area_resolved.status is AreaResolveStatus.OK:
                area_matches = [c for c in clarification.candidates if c.area_id == area_resolved.area_id]
                if len(area_matches) == 1:
                    entity = area_matches[0]

        if entity is None:
            return None

        matched = [entity]
        pending_parameters = dict(clarification.pending_parameters)
        if "relative_step" in pending_parameters:
            # "um 20 Prozent runter" asked which cover: the step applies to
            # the chosen device's current position (7.6.1).
            from .nlu.ontology_compiler import apply_relative_step

            stepped = apply_relative_step(entity, float(str(pending_parameters["relative_step"])))
            if stepped is None or stepped[0] != (clarification.pending_intent,):
                return None
            pending_parameters = dict(stepped[1])
        spec = INTENTS.get(clarification.pending_intent)
        if spec is not None:
            area = (
                AreaReference(text=entity.area_name, area_id=entity.area_id)
                if entity.area_id is not None and entity.area_name is not None
                else None
            )
            return self._build_match_result(
                ParseResult(
                    frame=SemanticFrame(
                        intent=clarification.pending_intent,
                        target=TargetReference(
                            text=entity.friendly_name,
                            entity_id=entity.entity_id,
                            domain=entity.domain,
                        ),
                        area=area,
                        parameters=pending_parameters,
                        source_text=reply_text,
                    ),
                    resolved_entities=matched,
                ),
                entities,
            )
        if (
            clarification.pending_intent in QUERY_INTENTS
            or clarification.pending_intent in CLIMATE_EXTENDED_INTENTS
            or clarification.pending_intent in FAN_EXTENDED_INTENTS
            or clarification.pending_intent in LIGHT_EXTENDED_INTENTS
            or clarification.pending_intent == "HassSetPercentage"
        ):
            area = (
                AreaReference(text=entity.area_name, area_id=entity.area_id)
                if entity.area_id is not None and entity.area_name is not None
                else None
            )
            return self._build_match_result(
                ParseResult(
                    frame=SemanticFrame(
                        intent=clarification.pending_intent,
                        target=TargetReference(
                            text=entity.friendly_name,
                            entity_id=entity.entity_id,
                            domain=entity.domain,
                        ),
                        area=area,
                        parameters=pending_parameters,
                        source_text=reply_text,
                    ),
                    resolved_entities=matched,
                ),
                entities,
            )
        return None

    def respond(self, text: str, entities: list[EntitySnapshot]) -> NluResponse:
        """Render the canonical V8 outcome through the compact legacy API."""
        outcome = self.understand(text, entities)
        match_result = outcome.payload
        if not isinstance(match_result, MatchResult):
            return NluResponse(
                success=False,
                speech=None,
                command=None,
                error=_nlu_error_for_outcome(outcome),
            )
        if match_result.clarification is not None:
            return NluResponse(
                success=False,
                speech=None,
                command=None,
                error=NluError.AMBIGUOUS_ENTITY,
            )
        if match_result.command is None:
            return NluResponse(
                success=False, speech=None, command=None, error=NluError.NO_MATCH
            )
        return NluResponse(
            success=True, speech=match_result.response_text, command=match_result.command, error=None
        )

    def debug(self, text: str, entities: list[EntitySnapshot]) -> DebugTrace:
        """Return a read-only trace of the canonical V8 interpretation."""
        outcome = self.understand(text, entities)
        normalized = outcome.normalized_text
        parser_name = "SemanticInterpreter"
        result = outcome.payload
        if not isinstance(result, MatchResult):
            return DebugTrace(
                input=text,
                normalized=normalized,
                parser=parser_name,
                intent=None,
                target=None,
                area=None,
                candidates=(),
                resolution=None,
                capabilities=(),
                command=None,
                validation=_nlu_error_for_outcome(outcome).name,
                service=None,
                data=None,
                result_kind="no_match",
            )
        if result.clarification is not None:
            clarification = result.clarification
            target = clarification.pending_target
            friendly_names = {
                entity.friendly_name for entity in clarification.candidates
            }
            if len(friendly_names) == 1:
                target = next(iter(friendly_names))
            return DebugTrace(
                input=text,
                normalized=normalized,
                parser=parser_name,
                intent=clarification.pending_intent,
                target=target,
                area=None,
                candidates=tuple(entity.entity_id for entity in clarification.candidates),
                resolution=None,
                capabilities=(),
                command=None,
                validation="AMBIGUOUS_ENTITY",
                service=None,
                data=None,
                result_kind="ambiguous",
            )

        frame = result.frame
        command = result.command
        if frame is None or command is None:
            return DebugTrace(
                input=text,
                normalized=normalized,
                parser=parser_name,
                intent=None,
                target=None,
                area=None,
                candidates=(),
                resolution=None,
                capabilities=(),
                command=None,
                validation="NO_MATCH",
                service=None,
                data=None,
                result_kind="no_match",
                response_text=result.response_text,
            )
        matched = command.entities
        validation_error = validate_command(command)
        capabilities = tuple(sorted({capability for entity in matched for capability in entity.capabilities}))
        candidates = tuple(entity.entity_id for entity in matched)

        service = None
        data = None
        response_text = None
        validation = "OK"
        if validation_error is not None:
            validation = validation_error.name
        else:
            response_text = result.response_text
            if result.plan is not None:
                service = f"{result.plan.domain}.{result.plan.service}"
                data = result.plan.data

        result_kind = "matched" if candidates else "empty"

        return DebugTrace(
            input=text,
            normalized=normalized,
            parser=parser_name,
            intent=frame.intent,
            target=frame.target.text if frame.target is not None else None,
            area=frame.area.text if frame.area is not None else None,
            candidates=candidates,
            resolution=", ".join(candidates) or None,
            capabilities=capabilities,
            command=format_command(command),
            validation=validation,
            service=service,
            data=data,
            result_kind=result_kind,
            response_text=response_text,
        )

    @staticmethod
    def _build_match_result(
        result: ParseResult,
        entities: list[EntitySnapshot],
        context: ConversationContext | None = None,
    ) -> MatchResult | None:
        frame = result.frame
        matched = result.resolved_entities
        command = build_semantic_command(result)
        typed_result = frame.parameters.get("query_result")

        if (
            isinstance(typed_result, QueryResult)
            and typed_result.status is QueryResultStatus.AMBIGUOUS
            and frame.action is SemanticAction.QUERY
            and frame.intent in QUERY_INTENTS
        ):
            resolved_intent = ResolvedSemanticIntent(
                action=frame.action,
                entities=tuple(matched),
                property=frame.property,
                direction=frame.direction,
                degree=frame.degree,
                area=frame.area,
            )
            return MatchResult(
                plan=None,
                response_text=ResponseGenerator().respond(typed_result),
                frame=frame,
                command=command,
                resolved_intent=resolved_intent,
            )

        # Defense in depth for every caller of this builder: a syntactic
        # question can only use registered read-only query intents. Never
        # return a service plan with a success sentence for a question.
        if (
            analyse_utterance(frame.source_text).speech_act is SpeechAct.QUERY
            and frame.intent not in QUERY_INTENTS
        ):
            return None

        # Gate on the Command Validator (v2 plan Phase 11) before building
        # anything. Today's parsers already self-police everything this
        # checks, so this never actually rejects a real match (see the 5
        # "real matches validate clean" regression tests in
        # tests/test_validator.py) - it is wired in now so a future,
        # non-self-policing parser (LLM Adapter) gets a real gatekeeper
        # instead of a silent no-op.
        if validate_command(command) is not None:
            return None

        # A typed QueryResult has already been grounded by the authoritative
        # QueryExecutor. Re-running the generic fuzzy constraint resolver over
        # the complete registry would be redundant and can dominate aggregate
        # latency when an answer is empty. Commands and legacy queries retain
        # the normal central reasoning gate.
        if isinstance(typed_result, QueryResult):
            resolved_intent = ResolvedSemanticIntent(
                action=frame.action,
                entities=tuple(matched),
                property=frame.property,
                direction=frame.direction,
                degree=frame.degree,
                area=frame.area,
            )
        else:
            resolved_intent = ReasoningEngine.resolve(frame, entities, context)
        if (
            context is None
            and frame.target is not None
            and frame.target.entity_id is not None
            and frame.parameters.get("query_result") is None
            and (
                len(matched) != 1
                or tuple(entity.entity_id for entity in resolved_intent.entities)
                != (matched[0].entity_id,)
            )
        ):
            return None

        percent = frame.parameters.get("percent")
        if percent is not None:
            spec = PERCENT_INTENTS.get(matched[0].domain)
            if spec is None:
                return None
            return MatchResult(
                plan=map_to_service_call(command),
                response_text=spec.response(matched, percent),
                frame=frame,
                command=command,
                resolved_intent=resolved_intent,
            )

        light_extended_spec = LIGHT_EXTENDED_INTENTS.get(frame.intent)
        if light_extended_spec is not None:
            return MatchResult(
                plan=map_to_service_call(command),
                response_text=light_extended_spec.response(matched, frame.parameters),
                frame=frame,
                command=command,
                resolved_intent=resolved_intent,
            )

        fan_extended_spec = FAN_EXTENDED_INTENTS.get(frame.intent)
        if fan_extended_spec is not None:
            return MatchResult(
                plan=map_to_service_call(command),
                response_text=fan_extended_spec.response(matched, frame.parameters),
                frame=frame,
                command=command,
                resolved_intent=resolved_intent,
            )

        climate_extended_spec = CLIMATE_EXTENDED_INTENTS.get(frame.intent)
        if climate_extended_spec is not None:
            climate_plan = map_to_service_call(command)
            if climate_plan is None:
                # build() declined (e.g. missing "temperature" attribute) -
                # fall through to "not understood" rather than speaking a
                # success response with nothing actually executed.
                return None
            return MatchResult(
                plan=climate_plan,
                response_text=climate_extended_spec.response(matched, frame.parameters),
                frame=frame,
                command=command,
                resolved_intent=resolved_intent,
            )

        if frame.intent == REGISTERED_OPERATION_INTENT:
            registered_plan = map_to_service_call(command)
            if registered_plan is None:
                return None
            done_text = describe_registered_result(
                join_german([entity.friendly_name for entity in matched]),
                registered_plan.domain,
                registered_plan.service,
            )
            return MatchResult(
                plan=registered_plan,
                response_text=done_text or (
                    f"{matched[0].friendly_name}: "
                    f"{describe_registered_operation(registered_plan.domain, registered_plan.service, registered_plan.data, {entity.entity_id: entity.friendly_name for entity in entities})}."
                ),
                frame=frame,
                command=command,
                resolved_intent=resolved_intent,
            )

        # WorldModelQuery wave: StateQueryParser's four intents (HassStateQuery/
        # HassCheckState/HassExistsQuery/HassDeviceQuery) now stash the full
        # QueryResult (status included, not just the matched entities) - when
        # present, ResponseGenerator speaks it directly instead of the
        # QUERY_INTENTS response lambda below, which loses QueryResultStatus.
        query_result = typed_result
        if query_result is not None:
            return MatchResult(
                plan=None,
                response_text=_RESPONSE_GENERATOR.respond(
                    query_result,
                    context=context,
                    allow_reference=(
                        context is not None
                        and frame.source_text.lstrip().casefold().startswith("und ")
                    ),
                ),
                frame=frame,
                command=command,
                resolved_intent=resolved_intent,
                explanation_text=result.explanation_text,
            )

        query_spec = QUERY_INTENTS.get(frame.intent)
        if query_spec is not None:
            return MatchResult(
                plan=None,
                response_text=query_spec.response(matched, frame.parameters),
                frame=frame,
                command=command,
                resolved_intent=resolved_intent,
            )

        spec = INTENTS.get(frame.intent)
        if spec is None:
            return None
        return MatchResult(
            plan=map_to_service_call(command),
            response_text=spec.response(matched),
            frame=frame,
            command=command,
            resolved_intent=resolved_intent,
        )
