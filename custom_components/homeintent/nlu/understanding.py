"""Canonical outcomes and trace data for one HomeIntent understanding turn.

This module is deliberately Home-Assistant- and parser-free.  It is the
stable boundary between language interpretation and the domain executors:
callers no longer need to infer why a matcher returned ``None`` or whether a
plan-less result is a query, a clarification, or a rejection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Generic, Mapping, Sequence, TypeVar, cast

from .parse_outcome import ParseFailureReason
from .semantic_utterance import SpeechAct
from .semantic_graph import SemanticGraph


class UnderstandingKind(Enum):
    """Exhaustive public result classes for a language turn."""

    QUERY = auto()
    COMMAND = auto()
    AUTOMATION = auto()
    MANAGEMENT = auto()
    CLARIFICATION = auto()
    AMBIGUOUS = auto()
    UNSUPPORTED = auto()
    UNSAFE = auto()


class UnderstandingAuthority(Enum):
    """Pipeline that supplied the payload for one understanding outcome."""

    NONE = auto()
    LEGACY = auto()
    LEGACY_SHADOW = auto()
    V8_COMPATIBILITY = auto()
    V8_SEMANTIC = auto()

    # Compatibility aliases for integrations and persisted diagnostics from
    # releases up to 4.73.  Enum aliases retain identity and therefore do not
    # break callers comparing the historical members with ``is``.
    V7_FALLBACK = LEGACY_SHADOW
    V7_MIGRATED = V8_SEMANTIC


class EvidenceKind(Enum):
    """Where a piece of interpretation evidence originated."""

    ORIGINAL = auto()
    NORMALIZED = auto()
    LEXICON = auto()
    LEXICAL = LEXICON
    REGISTRY = auto()
    ENTITY = auto()
    AREA = auto()
    FLOOR = auto()
    CONTEXT = auto()
    DISCOURSE = auto()
    WORLD_MODEL = auto()
    CAPABILITY = auto()
    PROPERTY = auto()
    UNIT = auto()
    TEMPORAL = auto()
    NEGATIVE_EVIDENCE = auto()
    CORRECTION = auto()
    SPELLING = auto()
    PHONETIC = auto()
    LEGACY = auto()
    STRUCTURE = auto()


class EvidencePolarity(Enum):
    """Whether evidence supports or contradicts a semantic claim."""

    POSITIVE = auto()
    NEGATIVE = auto()


@dataclass(frozen=True)
class UnderstandingEvidence:
    """One source-spanned, scored reason for an interpretation."""

    kind: EvidenceKind
    value: str
    start: int | None = None
    end: int | None = None
    score: float = 0.0
    detail: str | None = None
    polarity: EvidencePolarity = EvidencePolarity.POSITIVE
    claim: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class MeaningCandidate:
    """A ranked but not yet executable semantic interpretation."""

    key: str
    score: float
    complete: bool
    slots: Mapping[str, object] = field(default_factory=dict[str, object])
    missing_slots: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    evidence: tuple[UnderstandingEvidence, ...] = ()
    graph: SemanticGraph | None = None
    rejection_reason: str | None = None

    @property
    def raw_score(self) -> float:
        """Lossless score used by ranking and ambiguity decisions."""
        return self.score

    @property
    def display_score(self) -> float:
        """Bounded diagnostic presentation without affecting ranking."""
        return max(0.0, min(100.0, self.score))


T = TypeVar("T")


@dataclass(frozen=True)
class UnderstandingOutcome(Generic[T]):
    """The one canonical result of understanding a complete user turn.

    ``payload`` is an already validated domain object (for example a legacy
    ``MatchResult`` during migration).  Language modules only construct
    candidates; the orchestration boundary attaches an executable payload
    after resolution and safety validation.
    """

    kind: UnderstandingKind
    source_text: str
    normalized_text: str
    speech_act: SpeechAct
    payload: T | None = None
    reason: ParseFailureReason | None = None
    speech: str | None = None
    candidates: tuple[MeaningCandidate, ...] = ()
    evidence: tuple[UnderstandingEvidence, ...] = ()
    unexplained_tokens: tuple[str, ...] = ()
    corrections: tuple[str, ...] = ()
    route: str | None = None
    margin: float | None = None
    authority: UnderstandingAuthority = UnderstandingAuthority.NONE

    @property
    def actionable(self) -> bool:
        return self.kind in {
            UnderstandingKind.COMMAND,
            UnderstandingKind.AUTOMATION,
            UnderstandingKind.MANAGEMENT,
        } and self.payload is not None

    @property
    def safe_non_action(self) -> bool:
        return self.kind in {
            UnderstandingKind.QUERY,
            UnderstandingKind.CLARIFICATION,
            UnderstandingKind.AMBIGUOUS,
            UnderstandingKind.UNSUPPORTED,
            UnderstandingKind.UNSAFE,
        }


@dataclass(frozen=True)
class ShadowComparison:
    """Read-only comparison between candidate and authoritative pipelines."""

    source_text: str
    authoritative: UnderstandingOutcome[object]
    candidate: UnderstandingOutcome[object]
    equivalent: bool
    differences: tuple[str, ...] = ()
    stage_differences: tuple[str, ...] = ()


def compare_outcomes(
    authoritative: UnderstandingOutcome[object],
    candidate: UnderstandingOutcome[object],
) -> ShadowComparison:
    """Compare two outcomes without executing either payload."""
    differences: list[str] = []
    if authoritative.kind is not candidate.kind:
        differences.append(
            f"kind:{authoritative.kind.name}!={candidate.kind.name}"
        )
    if authoritative.speech_act is not candidate.speech_act:
        differences.append(
            "speech_act:"
            f"{authoritative.speech_act.name}!={candidate.speech_act.name}"
        )
    if authoritative.reason is not candidate.reason:
        left = authoritative.reason.name if authoritative.reason else "-"
        right = candidate.reason.name if candidate.reason else "-"
        differences.append(f"reason:{left}!={right}")
    if authoritative.actionable != candidate.actionable:
        differences.append(
            f"actionable:{authoritative.actionable}!={candidate.actionable}"
        )
    if _payload_signature(authoritative.payload) != _payload_signature(
        candidate.payload
    ):
        differences.append("payload")
    stage_differences: list[str] = []
    if _candidate_signature(authoritative.candidates) != _candidate_signature(
        candidate.candidates
    ):
        stage_differences.append("meaning_candidates")
    if _graph_signature(authoritative) != _graph_signature(candidate):
        stage_differences.append("semantic_graph")
    return ShadowComparison(
        source_text=authoritative.source_text,
        authoritative=authoritative,
        candidate=candidate,
        equivalent=not differences,
        differences=tuple(differences),
        stage_differences=tuple(stage_differences),
    )


def _candidate_signature(candidates: tuple[MeaningCandidate, ...]) -> object:
    return tuple(
        (
            item.key,
            round(item.score, 6),
            item.complete,
            _freeze(item.slots),
            item.missing_slots,
            item.conflicts,
            item.rejection_reason,
        )
        for item in candidates
    )


def _graph_signature(outcome: UnderstandingOutcome[object]) -> object | None:
    graphs = tuple(
        _freeze(candidate.graph.canonical_snapshot())
        for candidate in outcome.candidates
        if candidate.graph is not None
    )
    return graphs or None


def _freeze(value: object) -> object:
    """Return a deterministic comparison form for plan parameters."""
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return tuple(
            sorted((str(key), _freeze(item)) for key, item in mapping.items())
        )
    if isinstance(value, (list, tuple)):
        sequence = cast(Sequence[object], value)
        return tuple(_freeze(item) for item in sequence)
    if isinstance(value, (set, frozenset)):
        items = cast(set[object] | frozenset[object], value)
        return tuple(sorted((_freeze(item) for item in items), key=repr))
    return value


def _payload_signature(payload: object | None) -> object | None:
    """Compare observable meaning, not parser-specific bookkeeping.

    Legacy and V8 intentionally build independent frame and reasoning
    objects.  A shadow comparison must flag a changed service, target,
    parameter, query answer, or clarification set, but not object-internal
    provenance that cannot affect the user-visible result.
    """
    if payload is None:
        return None
    commands = getattr(payload, "commands", None)
    if commands is not None:
        return ("commands", tuple(_payload_signature(item) for item in commands))
    plan = getattr(payload, "plan", None)
    if plan is not None:
        return (
            "plan",
            getattr(plan, "domain", None),
            getattr(plan, "service", None),
            _freeze(getattr(plan, "entity_id", None)),
            _freeze(getattr(plan, "data", {})),
        )
    clarification = getattr(payload, "clarification", None)
    if clarification is not None:
        return (
            "clarification",
            getattr(clarification, "pending_intent", None),
            tuple(
                getattr(entity, "entity_id", None)
                for entity in getattr(clarification, "candidates", ())
            ),
            _freeze(getattr(clarification, "pending_parameters", {})),
        )
    frame = getattr(payload, "frame", None)
    command = getattr(payload, "command", None)
    entities = (
        getattr(command, "entities", ())
        if command is not None
        else getattr(payload, "context_entities", ())
    )
    return (
        "non_action",
        getattr(frame, "intent", None),
        tuple(getattr(entity, "entity_id", None) for entity in entities),
        getattr(payload, "response_text", None),
    )


# ---------------------------------------------------------------------------
# General shadow infrastructure (7.3.4): behaviour signatures and drift.
#
# ``compare_outcomes`` above compares two understanding outcomes structurally.
# The functions below compare the *behaviour* two pipelines would cause, so
# every migration (target resolution, arbitration, language islands) is
# judged by one rule set.  A candidate never executes anything: it only
# produces a payload whose signature is logged.


class DriftClass(Enum):
    """Result of comparing an active and a candidate behaviour."""

    EQUIVALENT = auto()
    REFINEMENT = auto()  # same effect, different reasoning/wording
    BEHAVIOR_CHANGE = auto()  # different effect within the same kind of device
    SAFETY_DRIFT = auto()  # other kind/domain, more targets, lower risk,
    # dropped confirmation, or a write where the active pipeline wrote nothing


@dataclass(frozen=True)
class BehaviorSignature:
    """What a payload would do; no parser internals, no Home Assistant."""

    speech_act: str = ""
    writes: bool = False
    operations: frozenset[str] = frozenset()
    domains: frozenset[str] = frozenset()
    genera: frozenset[str] = frozenset()
    targets: frozenset[str] = frozenset()
    place: str | None = None
    quantity: int = 0
    origin: str = "explicit_command"
    risk: int = 0
    confirmation: bool = False
    plan: tuple[object, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "speech_act": self.speech_act,
            "writes": self.writes,
            "operations": sorted(self.operations),
            "domains": sorted(self.domains),
            "genera": sorted(self.genera),
            "targets": sorted(self.targets),
            "place": self.place,
            "quantity": self.quantity,
            "origin": self.origin,
            "risk": self.risk,
            "confirmation": self.confirmation,
        }


def _plans_of(payload: object | None) -> list[object]:
    if payload is None:
        return []
    commands = getattr(payload, "commands", None)
    if commands is not None:
        return [plan for item in commands if (plan := getattr(item, "plan", None)) is not None]
    plan = getattr(payload, "plan", None)
    return [plan] if plan is not None else []


def behavior_signature(
    payload: object | None,
    *,
    speech_act: str = "",
    place: str | None = None,
    genera: frozenset[str] = frozenset(),
    risk_of: "Callable[[object], int] | None" = None,
    confirmation_of: "Callable[[object], bool] | None" = None,
    detail: str = "",
) -> BehaviorSignature:
    """Behaviour of a ``MatchResult``/``CommandPlan``-like payload.

    ``risk_of``/``confirmation_of`` are injected by the caller (they need the
    risk classification and policy, which this module must not import).
    """
    plans = _plans_of(payload)
    targets: set[str] = set()
    operations: set[str] = set()
    domains: set[str] = set()
    frozen: list[object] = []
    for plan in plans:
        raw = getattr(plan, "entity_id", ())
        ids = (raw,) if isinstance(raw, str) else tuple(cast(Sequence[str], raw))
        targets.update(ids)
        domains.update(item.split(".", 1)[0] for item in ids)
        operations.add(f"{getattr(plan, 'domain', '')}.{getattr(plan, 'service', '')}")
        frozen.append(_payload_signature_plan(plan))
    origin = getattr(payload, "origin", None)
    origin_value = str(getattr(origin, "value", origin or "explicit_command"))
    return BehaviorSignature(
        speech_act=speech_act,
        writes=bool(plans),
        operations=frozenset(operations),
        domains=frozenset(domains),
        genera=genera,
        targets=frozenset(targets),
        place=place,
        quantity=len(targets),
        origin=origin_value,
        risk=max((risk_of(plan) for plan in plans), default=0) if risk_of else 0,
        confirmation=(
            any(confirmation_of(plan) for plan in plans) if confirmation_of else False
        ) or bool(getattr(payload, "confirmation_text", None)),
        plan=tuple(frozen),
        detail=detail,
    )


def _payload_signature_plan(plan: object) -> object:
    return (
        getattr(plan, "domain", None),
        getattr(plan, "service", None),
        _freeze(getattr(plan, "entity_id", None)),
        _freeze(getattr(plan, "data", {})),
    )


def classify_drift(
    active: BehaviorSignature, candidate: BehaviorSignature
) -> tuple[DriftClass, tuple[str, ...]]:
    """Classify how the candidate's behaviour differs from the active one."""
    reasons: list[str] = []
    if candidate.writes and not active.writes:
        reasons.append("write_instead_of_non_write")
    if candidate.writes and active.writes:
        if not candidate.targets <= active.targets:
            reasons.append("more_or_other_targets")
        if not candidate.domains <= active.domains:
            reasons.append("domain_change")
        if active.genera and candidate.genera and not candidate.genera & active.genera:
            reasons.append("genus_change")
        if candidate.risk < active.risk:
            reasons.append("lower_risk")
        if active.confirmation and not candidate.confirmation:
            reasons.append("confirmation_dropped")
    if reasons:
        return DriftClass.SAFETY_DRIFT, tuple(reasons)
    same_effect = (
        candidate.writes == active.writes
        and candidate.plan == active.plan
        and candidate.confirmation == active.confirmation
        and candidate.risk == active.risk
    )
    if same_effect and candidate.speech_act == active.speech_act and candidate.detail == active.detail:
        return DriftClass.EQUIVALENT, ()
    if same_effect:
        return DriftClass.REFINEMENT, ("same_effect_different_reasoning",)
    changes: list[str] = []
    if candidate.writes != active.writes:
        changes.append("non_write_instead_of_write")
    if candidate.plan != active.plan:
        changes.append("different_plan")
    if candidate.confirmation != active.confirmation:
        changes.append("confirmation_added")
    if candidate.risk != active.risk:
        changes.append("higher_risk")
    return DriftClass.BEHAVIOR_CHANGE, tuple(changes)


@dataclass(frozen=True)
class ShadowRecord:
    text_hash: str
    source: str
    drift: DriftClass
    reasons: tuple[str, ...]
    active: BehaviorSignature
    candidate: BehaviorSignature

    def to_dict(self) -> dict[str, object]:
        return {
            "text_hash": self.text_hash,
            "source": self.source,
            "drift": self.drift.name,
            "reasons": list(self.reasons),
            "active": self.active.to_dict(),
            "candidate": self.candidate.to_dict(),
        }


def text_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(" ".join(text.split()).casefold().encode()).hexdigest()[:12]


@dataclass
class ShadowReport:
    """Offline (CI) or live comparison of one candidate against the active pipeline."""

    name: str
    records: list[ShadowRecord] = field(default_factory=lambda: cast(list[ShadowRecord], []))
    limit: int | None = None

    def add(
        self, text: str, active: BehaviorSignature, candidate: BehaviorSignature, *, source: str = ""
    ) -> ShadowRecord:
        drift, reasons = classify_drift(active, candidate)
        record = ShadowRecord(text_hash(text), source, drift, reasons, active, candidate)
        self.records.append(record)
        if self.limit is not None and len(self.records) > self.limit:
            del self.records[: len(self.records) - self.limit]
        return record

    def counts(self) -> dict[str, int]:
        result = {item.name: 0 for item in DriftClass}
        for record in self.records:
            result[record.drift.name] += 1
        return result

    @property
    def safety_drift(self) -> tuple[ShadowRecord, ...]:
        return tuple(record for record in self.records if record.drift is DriftClass.SAFETY_DRIFT)

    @property
    def switch_allowed(self) -> bool:
        """SAFETY_DRIFT blocks every switch-over."""
        return not self.safety_drift

    def to_dict(self, *, examples: int = 20) -> dict[str, object]:
        return {
            "name": self.name,
            "total": len(self.records),
            "counts": self.counts(),
            "switch_allowed": self.switch_allowed,
            "safety_drift": [record.to_dict() for record in self.safety_drift[:examples]],
            "behavior_changes": [
                record.to_dict() for record in self.records
                if record.drift is DriftClass.BEHAVIOR_CHANGE
            ][:examples],
        }
