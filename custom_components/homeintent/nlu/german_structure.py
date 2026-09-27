"""Lightweight deterministic structural analysis for German utterances.

The analyser deliberately stops before domain grounding.  It records clause
and scope relations over source-token indices so later semantic stages can
distinguish, for example, an action from a state in a relative clause.  It is
small, dependency-free and has no access to Home Assistant services.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Protocol, Sequence


class StructuralToken(Protocol):
    """Minimum token contract required by the structural analyser."""

    @property
    def canonical(self) -> str: ...

    @property
    def text(self) -> str: ...

    @property
    def start(self) -> int: ...

    @property
    def end(self) -> int: ...

    @property
    def is_word(self) -> bool: ...


class ClauseKind(Enum):
    MAIN = auto()
    CONDITION = auto()
    TEMPORAL = auto()
    RELATIVE = auto()
    COORDINATE = auto()
    EXCLUSION = auto()
    REPAIR = auto()


class StructuralRelationKind(Enum):
    IF = auto()
    THEN = auto()
    AND = auto()
    OR = auto()
    EXCEPT = auto()
    BEFORE = auto()
    AFTER = auto()
    UNTIL = auto()
    WHILE = auto()
    MODIFIES = auto()
    REPLACES = auto()


class NegationKind(Enum):
    PREDICATE = auto()
    CONSTITUENT = auto()
    QUANTIFIER = auto()


class WordClass(Enum):
    PUNCTUATION = auto()
    NUMBER = auto()
    VERB = auto()
    MODAL = auto()
    AUXILIARY = auto()
    PRONOUN = auto()
    ARTICLE = auto()
    PREPOSITION = auto()
    CONJUNCTION = auto()
    PARTICLE = auto()
    NOUN_OR_NAME = auto()
    ADJECTIVE_OR_ADVERB = auto()
    UNKNOWN = auto()


class ArgumentRole(Enum):
    SUBJECT = auto()
    OBJECT = auto()
    INDIRECT_OBJECT = auto()
    LOCATIVE = auto()
    TEMPORAL = auto()
    REFERENCE = auto()
    COMPLEMENT = auto()


@dataclass(frozen=True)
class TokenFeatures:
    token_index: int
    word_class: WordClass
    lemma: str
    grammatical_gender: str | None = None
    grammatical_number: str | None = None
    grammatical_case: str | None = None
    safe_filler: bool = False


@dataclass(frozen=True)
class PredicateArgument:
    role: ArgumentRole
    token_start: int
    token_end: int
    head_token: int


@dataclass(frozen=True)
class ParticleLink:
    predicate_token: int
    particle_token: int
    combined_lemma: str


@dataclass(frozen=True)
class StructuralClause:
    """One source-backed clause or coordinated constituent."""

    clause_id: str
    kind: ClauseKind
    token_start: int
    token_end: int
    char_start: int
    char_end: int
    connector: str | None = None
    predicate_tokens: tuple[int, ...] = ()
    arguments: tuple[PredicateArgument, ...] = ()
    parent_clause_id: str | None = None


@dataclass(frozen=True)
class StructuralRelation:
    """Directed relation between two structural clauses."""

    kind: StructuralRelationKind
    source_clause: str
    target_clause: str
    connector_start: int
    connector_end: int


@dataclass(frozen=True)
class NegationScope:
    """Conservative token scope for one explicit negation."""

    token_index: int
    clause_id: str
    kind: NegationKind
    scope_start: int
    scope_end: int


@dataclass(frozen=True)
class GermanStructuralAnalysis:
    """Immutable structural view attached to a ``LanguageDocument``."""

    clauses: tuple[StructuralClause, ...]
    relations: tuple[StructuralRelation, ...]
    negations: tuple[NegationScope, ...]
    root_clause_ids: tuple[str, ...]
    token_features: tuple[TokenFeatures, ...] = ()
    particle_links: tuple[ParticleLink, ...] = ()

    def clause_for_char(self, position: int) -> StructuralClause | None:
        matches = tuple(
            clause
            for clause in self.clauses
            if clause.char_start <= position < clause.char_end
        )
        if not matches:
            return None
        return min(matches, key=lambda clause: clause.char_end - clause.char_start)


@dataclass(frozen=True)
class _Connector:
    words: tuple[str, ...]
    relation: StructuralRelationKind
    right_kind: ClauseKind


_CONNECTORS = tuple(sorted((
    _Connector(("nur", "wenn"), StructuralRelationKind.IF, ClauseKind.CONDITION),
    _Connector(("immer", "wenn"), StructuralRelationKind.IF, ClauseKind.CONDITION),
    _Connector(("jedes", "mal", "wenn"), StructuralRelationKind.IF, ClauseKind.CONDITION),
    _Connector(("wenn",), StructuralRelationKind.IF, ClauseKind.CONDITION),
    _Connector(("falls",), StructuralRelationKind.IF, ClauseKind.CONDITION),
    _Connector(("sofern",), StructuralRelationKind.IF, ClauseKind.CONDITION),
    _Connector(("bevor",), StructuralRelationKind.BEFORE, ClauseKind.TEMPORAL),
    _Connector(("nachdem",), StructuralRelationKind.AFTER, ClauseKind.TEMPORAL),
    _Connector(("bis",), StructuralRelationKind.UNTIL, ClauseKind.TEMPORAL),
    _Connector(("waehrend",), StructuralRelationKind.WHILE, ClauseKind.TEMPORAL),
    _Connector(("solange",), StructuralRelationKind.WHILE, ClauseKind.TEMPORAL),
    _Connector(("ausser",), StructuralRelationKind.EXCEPT, ClauseKind.EXCLUSION),
    _Connector(("bis", "auf"), StructuralRelationKind.EXCEPT, ClauseKind.EXCLUSION),
    _Connector(("ausgenommen",), StructuralRelationKind.EXCEPT, ClauseKind.EXCLUSION),
    _Connector(("nur",), StructuralRelationKind.EXCEPT, ClauseKind.EXCLUSION),
    _Connector(("aber",), StructuralRelationKind.AND, ClauseKind.COORDINATE),
    _Connector(("oder",), StructuralRelationKind.OR, ClauseKind.COORDINATE),
    _Connector(("und",), StructuralRelationKind.AND, ClauseKind.COORDINATE),
), key=lambda item: len(item.words), reverse=True))

_RELATIVE_WORDS = frozenset({"der", "die", "das", "welcher", "welche", "welches"})
_REPAIR_WORDS = frozenset({"nein", "sondern", "stattdessen", "aeh"})
_NEGATION_WORDS = frozenset({"nicht", "nie", "niemals", "keinesfalls"})
_NEGATIVE_QUANTIFIERS = frozenset({"kein", "keine", "keinen", "keinem", "keiner", "keines", "niemand"})
_FINITE_OR_ACTION_SUFFIXES = ("en", "st", "t", "e")
_MODALS = frozenset({"kann", "kannst", "koennte", "koenntest", "muss", "musst", "soll", "sollst", "sollen", "will", "willst", "moechte", "moechtest", "darf", "darfst"})
_AUXILIARIES = frozenset({"bin", "bist", "ist", "sind", "war", "waren", "hat", "haben", "wird", "werden", "wurde", "wurden"})
_PRONOUNS = frozenset({"ich", "du", "er", "sie", "es", "wir", "ihr", "das", "dies", "diese", "dieser", "jenes", "jene", "derjenige", "diejenigen", "davon", "dort"})
_ARTICLES = frozenset({"der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "alle", "beide"})
_PREPOSITIONS = frozenset({"in", "im", "an", "am", "auf", "bei", "beim", "mit", "nach", "vor", "hinter", "neben", "unter", "ueber", "von", "zu", "zum", "zur", "fuer", "seit"})
_LOCATIVE_PREPOSITIONS = frozenset({"in", "im", "an", "am", "bei", "beim", "hinter", "neben", "unter", "ueber"})
_TEMPORAL_PREPOSITIONS = frozenset({"nach", "vor", "bis", "seit", "waehrend", "fuer"})
_DATIVE_ARTICLES = frozenset({"dem", "einem", "einer"})
_ACCUSATIVE_ARTICLES = frozenset({"den", "einen"})
_PARTICLES = frozenset({"an", "aus", "auf", "zu", "ein", "hoch", "runter", "ab", "weiter"})
_SAFE_FILLERS = frozenset({
    "also", "aeh", "eh", "ach", "mal", "eben", "bitte", "halt"
})
_CONJUNCTIONS = frozenset(word for connector in _CONNECTORS for word in connector.words)


def _word_positions(tokens: Sequence[StructuralToken]) -> tuple[int, ...]:
    return tuple(index for index, token in enumerate(tokens) if token.is_word)


_VALUE_WORDS = frozenset({"halb", "die", "ein", "einen", "eine", "null", "voll"})


def _value_follows(tokens: Sequence[StructuralToken], index: int) -> bool:
    following = index + 1
    while following < len(tokens) and not (tokens[following].is_word or tokens[following].is_number):
        following += 1
    if following >= len(tokens):
        return False
    token = tokens[following]
    if token.is_number:
        return True
    if token.canonical in {"halb", "null", "voll"}:
        return True
    return token.canonical in _VALUE_WORDS and following + 1 < len(tokens) and tokens[following + 1].canonical in {
        "haelfte", "viertel", "drittel", "prozent",
    }


def _connector_at(
    tokens: Sequence[StructuralToken], word_positions: tuple[int, ...], word_offset: int
) -> tuple[_Connector, tuple[int, ...]] | None:
    for connector in _CONNECTORS:
        positions = word_positions[word_offset:word_offset + len(connector.words)]
        if len(positions) != len(connector.words):
            continue
        if tuple(tokens[index].canonical for index in positions) == connector.words:
            if connector.words == ("bis", "auf") and _value_follows(tokens, positions[-1]):
                continue  # "bis auf 50 Prozent" is a value, not an exception
            return connector, positions
    return None


def _is_relative_start(tokens: Sequence[StructuralToken], token_index: int) -> bool:
    if tokens[token_index].canonical not in _RELATIVE_WORDS:
        return False
    following = token_index + 1
    if (
        tokens[token_index].canonical in {"der", "die", "das"}
        and following < len(tokens)
        and tokens[following].is_word
        and tokens[following].text[:1].isupper()
    ):
        # ", das Küchenlicht aus": an article before a capitalized noun opens
        # a coordinated object, not a relative clause (", die noch an sind").
        return False
    previous = token_index - 1
    while previous >= 0 and tokens[previous].is_word is False:
        if tokens[previous].canonical == ",":
            return True
        previous -= 1
    return False


def _predicate_tokens(
    tokens: Sequence[StructuralToken], start: int, end: int
) -> tuple[int, ...]:
    """Return conservative verb candidates, keeping separable particles visible."""
    candidates: list[int] = []
    for index in range(start, end):
        token = tokens[index]
        word = token.canonical
        if not token.is_word:
            continue
        if word in {
            "ist", "sind", "war", "waren", "wird", "werden", "soll", "sollen",
            "mach", "mache", "macht", "schalt", "schalte", "stell", "stelle",
            "fahr", "fahre", "oeffne", "schliess", "schliesse", "starte", "stoppe",
        } or (len(word) >= 5 and word.endswith(_FINITE_OR_ACTION_SUFFIXES)):
            candidates.append(index)
    return tuple(candidates)


def _classify_tokens(tokens: Sequence[StructuralToken]) -> tuple[TokenFeatures, ...]:
    features: list[TokenFeatures] = []
    for index, token in enumerate(tokens):
        word = token.canonical
        if not token.is_word:
            word_class = WordClass.NUMBER if word.replace(",", "").isdigit() else WordClass.PUNCTUATION
        elif word in _MODALS:
            word_class = WordClass.MODAL
        elif word in _AUXILIARIES:
            word_class = WordClass.AUXILIARY
        elif word in _PRONOUNS:
            word_class = WordClass.PRONOUN
        elif word in _ARTICLES:
            word_class = WordClass.ARTICLE
        elif word in _CONJUNCTIONS:
            word_class = WordClass.CONJUNCTION
        elif word in _PREPOSITIONS:
            word_class = WordClass.PREPOSITION
        elif word in _PARTICLES:
            word_class = WordClass.PARTICLE
        elif len(word) >= 4 and word.endswith(_FINITE_OR_ACTION_SUFFIXES):
            word_class = WordClass.VERB
        elif token.text[:1].isupper():
            word_class = WordClass.NOUN_OR_NAME
        elif word.endswith(("ig", "lich", "isch", "er", "e", "en")):
            word_class = WordClass.ADJECTIVE_OR_ADVERB
        else:
            word_class = WordClass.UNKNOWN
        grammatical_case = (
            "dative" if word in _DATIVE_ARTICLES
            else "accusative" if word in _ACCUSATIVE_ARTICLES
            else None
        )
        grammatical_number = "plural" if word in {"alle", "beide", "sie", "diejenigen"} else None
        features.append(TokenFeatures(
            token_index=index,
            word_class=word_class,
            lemma=word,
            grammatical_number=grammatical_number,
            grammatical_case=grammatical_case,
            safe_filler=word in _SAFE_FILLERS,
        ))
    return tuple(features)


def _arguments(
    tokens: Sequence[StructuralToken],
    features: tuple[TokenFeatures, ...],
    start: int,
    end: int,
    predicates: tuple[int, ...],
) -> tuple[PredicateArgument, ...]:
    """Extract bounded phrase candidates without inventing dependency labels."""
    arguments: list[PredicateArgument] = []
    predicate_set = set(predicates)
    index = start
    while index < end:
        feature = features[index]
        if not tokens[index].is_word or index in predicate_set or feature.safe_filler:
            index += 1
            continue
        phrase_start = index
        if feature.word_class is WordClass.PREPOSITION:
            preposition = feature.lemma
            index += 1
            while index < end and (
                tokens[index].is_word
                and index not in predicate_set
                and features[index].word_class is not WordClass.CONJUNCTION
            ):
                index += 1
            role = (
                ArgumentRole.LOCATIVE if preposition in _LOCATIVE_PREPOSITIONS
                else ArgumentRole.TEMPORAL if preposition in _TEMPORAL_PREPOSITIONS
                else ArgumentRole.INDIRECT_OBJECT if preposition in {"mit", "von", "zu", "zum", "zur"}
                else ArgumentRole.COMPLEMENT
            )
        else:
            while index < end and (
                tokens[index].is_word
                and index not in predicate_set
                and features[index].word_class
                not in {WordClass.PREPOSITION, WordClass.CONJUNCTION}
            ):
                index += 1
            phrase_features = features[phrase_start:index]
            if any(item.word_class is WordClass.PRONOUN for item in phrase_features):
                role = ArgumentRole.REFERENCE
            elif any(item.grammatical_case == "dative" for item in phrase_features):
                role = ArgumentRole.INDIRECT_OBJECT
            elif predicates and phrase_start < predicates[0]:
                role = ArgumentRole.SUBJECT
            else:
                role = ArgumentRole.OBJECT
        if index <= phrase_start:
            index += 1
            continue
        head = next(
            (
                position
                for position in range(index - 1, phrase_start - 1, -1)
                if tokens[position].is_word
            ),
            phrase_start,
        )
        arguments.append(PredicateArgument(role, phrase_start, index, head))
    return tuple(arguments)


def _particle_links(
    tokens: Sequence[StructuralToken], clauses: Sequence[StructuralClause]
) -> tuple[ParticleLink, ...]:
    links: list[ParticleLink] = []
    for clause in clauses:
        predicates = tuple(
            index for index in clause.predicate_tokens
            if tokens[index].canonical not in _AUXILIARIES
        )
        particles = tuple(
            index for index in range(clause.token_start, clause.token_end)
            if tokens[index].canonical in _PARTICLES
        )
        if not predicates or not particles:
            continue
        predicate = predicates[0]
        particle = next((item for item in reversed(particles) if item > predicate), None)
        if particle is not None:
            links.append(ParticleLink(
                predicate,
                particle,
                tokens[particle].canonical + tokens[predicate].canonical,
            ))
    return tuple(links)


def _trim(tokens: Sequence[StructuralToken], start: int, end: int) -> tuple[int, int]:
    while start < end and not tokens[start].is_word:
        start += 1
    while end > start and not tokens[end - 1].is_word:
        end -= 1
    return start, end


def analyse_german_structure(
    tokens: Sequence[StructuralToken],
) -> GermanStructuralAnalysis:
    """Build a bounded token-level clause and scope analysis.

    Ambiguous coordinators remain explicit relations; this layer does not
    guess whether ``und`` joins targets, predicates, or complete clauses.
    """
    if not tokens:
        return GermanStructuralAnalysis((), (), (), ())

    token_features = _classify_tokens(tokens)

    word_positions = _word_positions(tokens)
    boundaries: list[tuple[int, int, _Connector | None, tuple[int, ...]]] = []
    last = 0
    word_offset = 0
    while word_offset < len(word_positions):
        token_index = word_positions[word_offset]
        connector_match = _connector_at(tokens, word_positions, word_offset)
        if (
            connector_match is not None
            and connector_match[0].words == ("nur",)
            and not any(
                token.canonical == "," for token in tokens[last:token_index]
            )
        ):
            # Sentence-initial ``nur`` restricts the following target/filter;
            # only comma-delimited ``..., nur X nicht`` is an exclusion.
            connector_match = None
        repair_is_pending = bool(
            boundaries
            and boundaries[-1][2] is not None
            and boundaries[-1][2].right_kind is ClauseKind.REPAIR
        )
        # ``nein, das Wohnzimmer`` is an elliptical replacement, not a
        # relative clause. Once a repair marker has opened a replacement,
        # an article after its comma belongs to that replacement span.
        is_relative = _is_relative_start(tokens, token_index) and not repair_is_pending
        is_repair = tokens[token_index].canonical in _REPAIR_WORDS
        if connector_match is None and not is_relative and not is_repair:
            word_offset += 1
            continue
        if connector_match is not None:
            connector, positions = connector_match
        elif is_relative:
            connector = _Connector(
                (tokens[token_index].canonical,),
                StructuralRelationKind.MODIFIES,
                ClauseKind.RELATIVE,
            )
            positions = (token_index,)
        else:
            connector = _Connector(
                (tokens[token_index].canonical,),
                StructuralRelationKind.REPLACES,
                ClauseKind.REPAIR,
            )
            positions = (token_index,)
        left_start, left_end = _trim(tokens, last, positions[0])
        if left_start < left_end:
            boundaries.append((left_start, left_end, None, ()))
        last = positions[-1] + 1
        boundaries.append((last, last, connector, positions))
        word_offset += len(positions)
    tail_start, tail_end = _trim(tokens, last, len(tokens))
    if tail_start < tail_end:
        boundaries.append((tail_start, tail_end, None, ()))

    if not boundaries:
        start, end = _trim(tokens, 0, len(tokens))
        boundaries.append((start, end, None, ()))

    clauses: list[StructuralClause] = []
    pending: tuple[_Connector, tuple[int, ...]] | None = None
    connector_for_clause: dict[str, tuple[_Connector, tuple[int, ...]]] = {}
    for start, end, connector, positions in boundaries:
        if connector is not None:
            pending = (connector, positions)
            continue
        if start >= end:
            continue
        # A comma closes a dependent German clause.  Commas immediately
        # before a connector were already consumed by ``_trim``; remaining
        # commas therefore delimit the action/main clause that follows.
        segment_starts = [start]
        segment_ends: list[int] = []
        for index in range(start, end):
            if tokens[index].canonical == ",":
                segment_ends.append(index)
                segment_starts.append(index + 1)
        segment_ends.append(end)
        segment_connector = pending
        for segment_start, segment_end in zip(segment_starts, segment_ends):
            segment_start, segment_end = _trim(tokens, segment_start, segment_end)
            if segment_start >= segment_end:
                continue
            kind = (
                segment_connector[0].right_kind
                if segment_connector is not None
                else ClauseKind.MAIN
            )
            clause_id = f"c{len(clauses)}"
            predicates = _predicate_tokens(tokens, segment_start, segment_end)
            clause = StructuralClause(
                clause_id=clause_id,
                kind=kind,
                token_start=segment_start,
                token_end=segment_end,
                char_start=tokens[segment_start].start,
                char_end=tokens[segment_end - 1].end,
                connector=(
                    " ".join(segment_connector[0].words)
                    if segment_connector is not None
                    else None
                ),
                predicate_tokens=predicates,
                arguments=_arguments(
                    tokens, token_features, segment_start, segment_end, predicates
                ),
            )
            clauses.append(clause)
            if segment_connector is not None:
                connector_for_clause[clause_id] = segment_connector
            segment_connector = None
        pending = None

    relations: list[StructuralRelation] = []
    for index, clause in enumerate(clauses):
        connection = connector_for_clause.get(clause.clause_id)
        if connection is None:
            continue
        connector, positions = connection
        if index == 0:
            # In a leading subordinate clause (``Wenn X, tue Y``) the
            # connector relates that condition to the following main clause.
            target = next(
                (item for item in clauses[1:] if item.kind is ClauseKind.MAIN),
                None,
            )
            if target is None:
                continue
            source_clause = clause.clause_id
            target_clause = target.clause_id
        else:
            previous_index = index - 1
            if connector.relation is StructuralRelationKind.REPLACES:
                # Hesitations such as ``äh``/``ach`` between the original and
                # ``nein`` carry no replaceable meaning. Link REPLACES to the
                # nearest substantive clause instead of the filler fragment.
                while previous_index >= 0 and all(
                    not tokens[token_index].is_word
                    or token_features[token_index].safe_filler
                    for token_index in range(
                        clauses[previous_index].token_start,
                        clauses[previous_index].token_end,
                    )
                ):
                    previous_index -= 1
            if previous_index < 0:
                continue
            source_clause = clauses[previous_index].clause_id
            target_clause = clause.clause_id
        relations.append(StructuralRelation(
            kind=connector.relation,
            source_clause=source_clause,
            target_clause=target_clause,
            connector_start=tokens[positions[0]].start,
            connector_end=tokens[positions[-1]].end,
        ))

    negations: list[NegationScope] = []
    for clause in clauses:
        for index in range(clause.token_start, clause.token_end):
            word = tokens[index].canonical
            if word not in _NEGATION_WORDS and word not in _NEGATIVE_QUANTIFIERS:
                continue
            kind = (
                NegationKind.QUANTIFIER
                if word in _NEGATIVE_QUANTIFIERS
                else NegationKind.PREDICATE
            )
            scope_start = index + 1
            scope_end = clause.token_end
            if scope_start >= scope_end:
                scope_start = clause.token_start
            negations.append(NegationScope(
                token_index=index,
                clause_id=clause.clause_id,
                kind=kind,
                scope_start=scope_start,
                scope_end=scope_end,
            ))

    targeted = {relation.target_clause for relation in relations}
    roots = tuple(clause.clause_id for clause in clauses if clause.clause_id not in targeted)
    if not roots and clauses:
        roots = (clauses[0].clause_id,)
    parent_by_clause: dict[str, str] = {}
    for relation in relations:
        if relation.kind in {
            StructuralRelationKind.IF,
            StructuralRelationKind.MODIFIES,
            StructuralRelationKind.BEFORE,
            StructuralRelationKind.AFTER,
            StructuralRelationKind.UNTIL,
            StructuralRelationKind.WHILE,
        }:
            parent_by_clause[relation.target_clause] = relation.source_clause
    clauses = [
        replace(clause, parent_clause_id=parent_by_clause.get(clause.clause_id))
        for clause in clauses
    ]
    return GermanStructuralAnalysis(
        clauses=tuple(clauses),
        relations=tuple(relations),
        negations=tuple(negations),
        root_clause_ids=roots,
        token_features=token_features,
        particle_links=_particle_links(tokens, clauses),
    )
