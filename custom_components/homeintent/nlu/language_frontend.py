"""Loss-aware German language frontend.

Unlike the historical ``normalize()`` contract this frontend never replaces
the user's utterance.  It retains source spans and records canonical variants
beside the original, allowing later stages to see negation, modality and
clause boundaries even when an orthographic form is normalized.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Iterable, Sequence

from ..entities import EntitySnapshot, normalize_for_compare
from ..name_similarity import edit_distance
from ..phonetic_correction import phonetic_suggestions
from .german_structure import ClauseKind, GermanStructuralAnalysis, analyse_german_structure
from .normalize import normalize
from .semantic_lexicon import SemanticAnalysis, SemanticKind, analyse_semantics
from .semantic_catalog import CANONICAL_SPELLING_FORMS, DEGREE_WORDS
from .device_ontology import analyse_word
from .semantic_utterance import (
    Polarity,
    PragmaticDisposition,
    SemanticUtterance,
    Modality,
    SpeechAct,
    analyse_utterance,
)
from .temporal_semantics import TemporalKind, TemporalExpression, analyse_temporal_semantics
from .utterance_meaning import MaintainFrame, ReleaseFrame, maintain_frames, release_frame
from .word_cues import has_word


_TOKEN_RE = re.compile(r"\d+(?:[,.]\d+)?|[\wäöüß]+|[%°]|[^\w\s]", re.I)
_SPACE_RE = re.compile(r"\s+")
_ELLIPTICAL_DIRECTIVE_RE = re.compile(
    r"\b(?:bitte|soll(?:st|en|t)?|möchte|moechte|will|gern|gerne)\b", re.I
)
_COPULA_RE = re.compile(r"\b(?:ist|sind|war|waren|bleibt|bleiben)\b", re.I)
_LOCATION_CUE_RE = re.compile(r"\b(?:im|in\s+der|in\s+dem|am|beim)\b", re.I)
_SPELLING_FORMS = {
    normalize_for_compare(item): item
    for item in sorted(CANONICAL_SPELLING_FORMS, key=lambda value: ("ae" in value or "oe" in value or "ue" in value, value))
}
_SPELLING_PROTECTED = frozenset(
    normalize_for_compare(word)
    for word in (
        "nicht kein keine keinen ohne wenn sobald falls sofern vielleicht "
        "bitte kannst könnte koennte soll sollen möchte moechte würde wuerde "
        "heute morgen gestern immer normalerweise welche welcher welches"
    ).split()
)
_NEGATION_FORMS = frozenset({"nicht", "kein", "keine", "keinen", "niemals"})
_NEGATION_TYPO_PROTECTED = frozenset({
    "sein", "fein", "klein", "eine", "einen", "einer", "einem", "eines",
    "meine", "deine", "keinerlei", "nein",
    # Frequent command/temporal vocabulary one edit away from "nicht".
    # These are real words, while a transposition such as "nciht" remains
    # safety-critical below.
    "licht", "dicht", "nacht",
})


@dataclass(frozen=True)
class LanguageToken:
    text: str
    canonical: str
    start: int
    end: int
    is_word: bool
    is_number: bool


@dataclass(frozen=True)
class TextVariant:
    text: str
    source: str
    cost: float = 0.0


@dataclass(frozen=True)
class LanguageDocument:
    source_text: str
    tokens: tuple[LanguageToken, ...]
    variants: tuple[TextVariant, ...]
    utterance: SemanticUtterance
    semantics: SemanticAnalysis
    structure: GermanStructuralAnalysis
    temporal: tuple[TemporalExpression, ...]
    # One optional maintenance frame per coordinated clause ("lass X an").
    maintain: tuple[MaintainFrame | None, ...] = ()
    # "X muss nicht an sein" / "X kann aus": the state is no longer needed.
    release: ReleaseFrame | None = None

    @property
    def maintained(self) -> tuple[MaintainFrame, ...]:
        return tuple(frame for frame in self.maintain if frame is not None)

    @property
    def normalized_text(self) -> str:
        return self.variants[1].text if len(self.variants) > 1 else self.source_text


def _has_place_mention(text: str, entities: tuple[EntitySnapshot, ...]) -> bool:
    """"Im Schlafzimmer bitte etwas kühler": a place carries the target."""
    from .place_model import PlaceKind, build_place_lexicon

    words = [normalize_for_compare(word) for word in re.findall(r"[\wäöüß]+", text)]
    return any(
        mention.place.kind is PlaceKind.AREA or mention.place.kind is PlaceKind.FLOOR
        for mention in build_place_lexicon(entities).scan(words)
    )


_SEPARABLE_PARTICLES = frozenset({"an", "aus", "auf", "zu", "ein", "hoch", "runter"})
_SUBJECT_PRONOUNS = frozenset({"ich", "du", "wir", "ihr", "er", "man"})
_INTERROGATIVES = frozenset({
    "wer", "was", "wie", "wo", "wann", "warum", "wieso", "weshalb", "welche", "welcher",
    "welches", "welchen", "ob", "ist", "sind", "hat", "haben",
})


def tokenize_language(text: str) -> tuple[LanguageToken, ...]:
    """Tokenize one surface while retaining stable source offsets."""
    return tuple(
        LanguageToken(
            text=match.group(0),
            canonical=normalize_for_compare(match.group(0)),
            start=match.start(),
            end=match.end(),
            is_word=bool(re.fullmatch(r"[\wäöüß]+", match.group(0), re.I)),
            is_number=bool(re.fullmatch(r"\d+(?:[,.]\d+)?", match.group(0))),
        )
        for match in _TOKEN_RE.finditer(text)
    )


def _orthographic_variant(text: str) -> str:
    """Return a comparison-only ASCII/umlaut canonical form."""
    return _SPACE_RE.sub(" ", normalize_for_compare(text)).strip()


def _registry_compound_variants(
    text: str, entities: tuple[EntitySnapshot, ...]
) -> tuple[str, ...]:
    """Expand joined/split registry spellings without fuzzy guessing."""
    normalized_text = normalize_for_compare(text)
    collapsed_text = normalized_text.replace(" ", "")
    variants: set[str] = set()
    registry_terms = {
        term
        for entity in entities
        for term in (
            entity.area_name or "",
            *entity.area_aliases,
            entity.floor_name or "",
        )
        if term and " " in term.strip()
    }
    for term in registry_terms:
        canonical = normalize_for_compare(term)
        collapsed = canonical.replace(" ", "")
        if collapsed not in collapsed_text or canonical in normalized_text:
            continue
        # Only replace a single joined word with a registry spelling whose
        # letters are exactly equal after whitespace removal.
        pattern = re.compile(rf"(?<!\w){re.escape(collapsed)}(?!\w)", re.I)
        candidate = pattern.sub(canonical, normalized_text)
        if candidate != normalized_text:
            variants.add(candidate)
    return tuple(sorted(variants))


def _has_registry_mention(text: str, entities: tuple[EntitySnapshot, ...]) -> bool:
    normalized = normalize_for_compare(text)
    padded = f" {normalized} "
    return any(
        key
        and f" {key} " in padded
        for entity in entities
        for name in (entity.friendly_name, *entity.aliases)
        if (key := normalize_for_compare(name))
    )


def registry_name_spans(
    tokens: Sequence[LanguageToken], entities: Iterable[EntitySnapshot]
) -> tuple[tuple[int, int], ...]:
    """Token ranges that spell an exposed multi-word name or alias exactly.

    Words inside such a name belong to the name ("Guten Morgen", "Gute
    Nacht"): they are neither greeting nor time. Single-word names never
    shadow a word, so "morgen" before a device named "Morgen" stays time.
    """
    words = [(index, token.canonical) for index, token in enumerate(tokens) if token.is_word]
    if len(words) < 2:
        return ()
    keys = [key for _index, key in words]
    present = set(keys)
    spans: set[tuple[int, int]] = set()
    for entity in entities:
        for name in (entity.friendly_name, *entity.aliases):
            parts = normalize_for_compare(name).split()
            if len(parts) < 2 or parts[0] not in present:
                continue
            width = len(parts)
            for start in range(len(keys) - width + 1):
                if keys[start:start + width] == parts:
                    spans.add((words[start][0], words[start + width - 1][0] + 1))
    return tuple(sorted(spans))


def _inside_name(item: TemporalExpression, spans: Sequence[tuple[int, int]]) -> bool:
    return any(start <= item.token_start and item.token_end <= end for start, end in spans)


@lru_cache(maxsize=2048)
def _unique_spelling_correction(spoken: str) -> str | None:
    """Return the sole one-edit vocabulary match for a normalized token.

    The closed semantic vocabulary is process-stable. Caching this bounded
    calculation avoids rebuilding the same edit-distance matrices on every
    repeated voice command without caching any entity or household data.
    """
    scored = sorted(
        (edit_distance(spoken, candidate), candidate)
        for candidate in _SPELLING_FORMS
        if abs(len(spoken) - len(candidate)) <= 1
    )
    if not scored or scored[0][0] > 1:
        return None
    best_distance = scored[0][0]
    best = [candidate for distance, candidate in scored if distance == best_distance]
    return best[0] if len(best) == 1 else None


def _lexical_spelling_variants(
    text: str, entities: tuple[EntitySnapshot, ...]
) -> tuple[str, ...]:
    """Correct one uniquely close semantic word; never registry-name words."""
    variants: set[str] = set()
    for match in re.finditer(r"[A-Za-zÄÖÜäöüß]{5,}", text):
        spoken = normalize_for_compare(match.group(0))
        if analyse_semantics(match.group(0)).spans:
            continue
        if (
            spoken in _SPELLING_FORMS
            or spoken in _SPELLING_PROTECTED
        ):
            continue
        correction = _unique_spelling_correction(spoken)
        if correction is None:
            continue
        if any(
            spoken in normalize_for_compare(name).split()
            for entity in entities
            for name in (entity.friendly_name, *entity.aliases)
        ):
            continue
        variants.add(
            text[:match.start()] + _SPELLING_FORMS[correction] + text[match.end():]
        )
    return tuple(sorted(variants))[:8]


def _has_near_negation(
    text: str, entities: tuple[EntitySnapshot, ...] = ()
) -> bool:
    """Treat a one-edit negation typo as negative, never as ignorable noise."""
    text = re.sub(
        r"\bnicht\s+(?:höher|hoeher|niedriger|mehr|weniger)\s+als\b",
        "",
        text,
        flags=re.I,
    )
    for raw in re.findall(r"[A-Za-zÄÖÜäöüß]{4,}", text):
        word = normalize_for_compare(raw)
        if word in _NEGATION_TYPO_PROTECTED:
            continue
        if word in _NEGATION_FORMS:
            return True
        near_negation = any(
            abs(len(word) - len(negation)) <= 1
            and (
                edit_distance(word, negation) == 1
                or (
                    len(word) == len(negation)
                    and sum(left != right for left, right in zip(word, negation)) == 2
                    and any(
                        word[index] == negation[index + 1]
                        and word[index + 1] == negation[index]
                        and word[:index] == negation[:index]
                        and word[index + 2:] == negation[index + 2:]
                        for index in range(len(word) - 1)
                    )
                )
            )
            for negation in _NEGATION_FORMS
        )
        if not near_negation:
            continue
        # Registry names are user data, not grammar.  Only pay the O(n)
        # registry scan after a token has passed the bounded edit-distance
        # gate; ordinary turns therefore remain independent of registry
        # size.  A name such as "Gute Nacht" still cannot become negation.
        if any(
            word in normalize_for_compare(name).split()
            for entity in entities
            for name in (entity.friendly_name, *entity.aliases)
        ):
            continue
        return True
    return False


def bind_coordinated_deixis(text: str, entities: Iterable[EntitySnapshot]) -> str:
    """"Licht im Bad an und den Lüfter dort auch": "dort" is the earlier place (7.6.0).

    Pure wording: only when the part before "und" names exactly one place
    with a preposition, its spoken phrase replaces the deictic word in a
    later conjunct. Nothing is resolved or guessed here.
    """
    lowered = text.casefold()
    if "dort" not in lowered or " und " not in lowered:
        return text
    from .place_model import PlaceKind, build_place_lexicon

    tokens = [token for token in tokenize_language(text) if token.is_word]
    keys = [token.canonical for token in tokens]
    if "und" not in keys:
        return text
    deictic = next(
        (index for index in range(keys.index("und") + 1, len(keys)) if keys[index] == "dort"),
        None,
    )
    if deictic is None:
        return text
    conjunction = max(index for index in range(deictic) if keys[index] == "und")
    mentions = [
        mention
        for mention in build_place_lexicon(tuple(entities)).scan(keys[:conjunction])
        if mention.explicit_preposition and mention.place.kind in {PlaceKind.AREA, PlaceKind.FLOOR}
    ]
    if len(mentions) != 1:
        return text
    mention = mentions[0]
    first = mention.token_start
    while first > 0 and keys[first - 1] in {"der", "dem", "den", "die", "das"}:
        first -= 1
    first -= 1  # the preposition itself
    phrase = text[tokens[first].start:tokens[mention.token_end - 1].end]
    target = tokens[deictic]
    return text[:target.start] + phrase + text[target.end:]


def analyse_language(
    text: str,
    entities: Iterable[EntitySnapshot] = (),
    *,
    include_phonetic: bool = False,
    include_registry_compounds: bool = True,
) -> LanguageDocument:
    """Build one immutable language document without discarding input."""
    entity_tuple = tuple(entities)
    text = bind_coordinated_deixis(text, entity_tuple)
    normalized = normalize(text)
    variants: list[TextVariant] = [TextVariant(text, "original")]
    if normalized != text:
        variants.append(TextVariant(normalized, "surface_normalization", 0.1))
    canonical = _orthographic_variant(text)
    if canonical not in {variant.text for variant in variants}:
        variants.append(TextVariant(canonical, "orthographic", 0.2))
    if include_registry_compounds and _LOCATION_CUE_RE.search(text):
        for candidate in _registry_compound_variants(text, entity_tuple):
            if candidate not in {variant.text for variant in variants}:
                variants.append(TextVariant(candidate, "registry_compound", 0.15))
    for candidate in _lexical_spelling_variants(text, entity_tuple):
        if candidate not in {variant.text for variant in variants}:
            variants.append(TextVariant(candidate, "lexical_spelling", 0.35))
    if include_phonetic:
        for suggestion in phonetic_suggestions(text, list(entity_tuple)):
            if suggestion.corrected_text not in {variant.text for variant in variants}:
                variants.append(TextVariant(
                    suggestion.corrected_text,
                    f"phonetic:{suggestion.corrected_term}",
                    0.75,
                ))
    # Semantics consumes the normalized surface for compatibility while the
    # document keeps the original and every applied alternative.
    utterance = analyse_utterance(text)
    semantics = analyse_semantics(utterance.normalized_text)
    if (
        utterance.speech_act is SpeechAct.COMMAND
        and utterance.normalized_text.rstrip().endswith("?")
        and "locations" in semantics.values(SemanticKind.QUERY_SCOPE)
        and semantics.values(SemanticKind.DEVICE_CLASS)
    ):
        # Interrogatives such as "In welchen Zimmern kann man ... finden?"
        # may contain lexical command homonyms (notably the article "ein").
        # A typed location scope plus question punctuation is read-only query
        # evidence and cannot authorize a service call.
        utterance = replace(utterance, speech_act=SpeechAct.QUERY)
    if (
        utterance.speech_act is SpeechAct.STATEMENT
        and utterance.pragmatic_disposition
        is not PragmaticDisposition.ASK_BEFORE_ACTION
        and semantics.values(SemanticKind.PROPERTY)
        and not semantics.values(SemanticKind.COMMAND_MARKER)
        and not any(
            normalize_for_compare(word) in DEGREE_WORDS
            for word in re.findall(r"[\wäöüß]+", utterance.normalized_text)
        )
    ):
        # Compact dashboard/voice noun phrases such as ``Temperatur Küche``
        # are read requests. The query compiler still requires one typed
        # property and a resolvable registry target/location, so a bare
        # descriptive statement cannot turn into an invented answer.
        utterance = replace(utterance, speech_act=SpeechAct.QUERY)
    # A unique one-edit operation correction may reveal an otherwise hidden
    # imperative. It changes only the discourse classification; compilation
    # still has to succeed on that recorded variant below the frontend.
    if utterance.speech_act is SpeechAct.STATEMENT:
        corrected_command = next(
            (
                analyse_utterance(variant.text)
                for variant in variants
                if variant.source == "lexical_spelling"
                and analyse_utterance(variant.text).speech_act is SpeechAct.COMMAND
            ),
            None,
        )
        if corrected_command is not None:
            utterance = replace(
                utterance,
                speech_act=SpeechAct.COMMAND,
                modality=corrected_command.modality,
            )
    # Dynamic registry names can contain the device noun as a compound
    # ("Gartenschalter", "Turmventilator"). Static discourse regexes cannot
    # safely enumerate those names.  Promote only a positive statement with
    # one explicit registry mention, one operation and directive/elliptical
    # evidence; plain state descriptions ("... ist an") remain statements.
    if (
        utterance.speech_act is SpeechAct.STATEMENT
        and len(semantics.values(SemanticKind.ACTION)) == 1
        and not utterance.normalized_text.rstrip().endswith("?")
        and (
            _ELLIPTICAL_DIRECTIVE_RE.search(utterance.normalized_text)
            or _COPULA_RE.search(utterance.normalized_text) is None
        )
        and _has_registry_mention(utterance.normalized_text, entity_tuple)
    ):
        utterance = replace(utterance, speech_act=SpeechAct.COMMAND)
    if (
        utterance.speech_act is SpeechAct.STATEMENT
        and not utterance.normalized_text.rstrip().endswith("?")
        and has_word(text, "nein", "sondern", "stattdessen", "aeh")
        and re.search(r"\d+(?:[,.]\d+)?", text)
        and semantics.values(SemanticKind.DOMAIN)
        and _has_registry_mention(utterance.normalized_text, entity_tuple)
    ):
        # A compact spoken setpoint repair ("Rollladen auf 70, nein 40%")
        # is directive only when the target is a real unique registry name.
        # The repair projector still checks slot/unit compatibility and the
        # normal validator/capability/policy pipeline remains authoritative.
        utterance = replace(utterance, speech_act=SpeechAct.COMMAND)
    if (
        utterance.speech_act is SpeechAct.STATEMENT
        and not utterance.normalized_text.rstrip().endswith("?")
        and _COPULA_RE.search(utterance.normalized_text) is None
        and any(
            normalize_for_compare(word) in DEGREE_WORDS
            for word in re.findall(r"[\wäöüß]+", utterance.normalized_text)
        )
        and (
            _has_registry_mention(utterance.normalized_text, entity_tuple)
            or any(
                analyse_word(word) is not None
                for word in re.findall(r"[\wäöüß]+", utterance.normalized_text)
            )
            or _has_place_mention(utterance.normalized_text, entity_tuple)
        )
    ):
        # Verbless comparative requests ("Das Radio bitte etwas lauter",
        # "Die Stehlampe heller") are directives: a degree word plus a
        # target without a copula.  "Hier ist es zu hell" keeps its copula
        # and stays a statement.
        utterance = replace(utterance, speech_act=SpeechAct.COMMAND)
    if (
        utterance.speech_act in {SpeechAct.STATEMENT, SpeechAct.QUERY}
        and not utterance.normalized_text.rstrip().endswith("?")
        and _COPULA_RE.search(utterance.normalized_text) is None
        and not re.match(
            r"\s*(?:wer|was|wie|wo|wann|warum|wieso|welch\w*|ob)\b",
            utterance.normalized_text,
            re.I,
        )
    ):
        words = [
            normalize_for_compare(word)
            for word in re.findall(r"[\wäöüß]+", utterance.normalized_text)
        ]
        after_auf = words[words.index("auf") + 1:] if "auf" in words else []
        # "Heizung Wohnzimmer auf 22 Grad": a number with its unit closing
        # the sentence is the value of a verbless setting (7.6.0).
        unit_value = (
            len(after_auf) == 2 and after_auf[0].isdigit() and after_auf[1] in {"grad", "prozent"}
        )
        if "auf" in words and after_auf and (
            _ELLIPTICAL_DIRECTIVE_RE.search(utterance.normalized_text)
            or not any(word.isdigit() for word in after_auf)
            or unit_value
        ) and (
            _has_registry_mention(" ".join(words[:words.index("auf")]), entity_tuple)
            or any(analyse_word(word) is not None for word in words[:words.index("auf")])
        ):
            # Verbless settings ("Saugroboter bitte auf leise", "Rollladen
            # auf 40"): a target followed by "auf <value>" is a directive.
            utterance = replace(utterance, speech_act=SpeechAct.COMMAND)
    if (
        utterance.speech_act in {SpeechAct.STATEMENT, SpeechAct.QUERY}
        and not utterance.normalized_text.rstrip().endswith("?")
        and _COPULA_RE.search(utterance.normalized_text) is None
    ):
        words = [
            normalize_for_compare(word)
            for word in re.findall(r"[\wäöüß]+", utterance.normalized_text)
        ]
        if (
            len(words) > 1
            and words[0] not in _INTERROGATIVES
            and words[-1] in _SEPARABLE_PARTICLES
            and ("bitte" in words or len(words) <= 4)
            and not set(words) & _SUBJECT_PRONOUNS
            and (
                _has_registry_mention(" ".join(words[:-1]), entity_tuple)
                or any(analyse_word(word) is not None for word in words[:-1])
            )
        ):
            # Verbless particle requests ("Bitte das Radio in der Küche
            # an", "Den Rollladen auf"): a target plus a final separable
            # particle is a directive; a copula ("ist an") is a statement.
            utterance = replace(utterance, speech_act=SpeechAct.COMMAND)
    explicit_unmute = re.search(r"\bnicht\s+mehr\s+stumm\b", text, re.I) is not None
    if explicit_unmute:
        utterance = replace(utterance, polarity=Polarity.POSITIVE)
    if _has_near_negation(text, entity_tuple) and not explicit_unmute:
        utterance = replace(utterance, polarity=Polarity.NEGATIVE)
    tokens = tokenize_language(text)
    name_spans = registry_name_spans(tokens, entity_tuple)
    structure = analyse_german_structure(tokens)
    maintain, _clause_ranges = maintain_frames(text, tokens)
    if maintain and all(frame is not None for frame in maintain):
        # Keeping a state is never an operation, whatever particle
        # ("an", "zu", "auf") the clause ends with.
        utterance = replace(utterance, modality=Modality.MAINTAIN)
    if (
        utterance.polarity is Polarity.NEGATIVE
        and structure.negations
        and all(
            (clause := next(
                (item for item in structure.clauses if item.clause_id == scope.clause_id),
                None,
            )) is not None
            and clause.kind is ClauseKind.EXCLUSION
            for scope in structure.negations
        )
    ):
        # Scoped exception negation leaves the main command positive. The
        # projection still has to resolve the excluded target unambiguously.
        utterance = replace(utterance, polarity=Polarity.POSITIVE)
    return LanguageDocument(
        source_text=text,
        tokens=tokens,
        variants=tuple(variants),
        utterance=utterance,
        semantics=semantics,
        structure=structure,
        # "jetzt"/"sofort" say what every plain command means; they are no
        # scheduling and must not block the direct path (7.5.0).
        temporal=tuple(
            item for item in analyse_temporal_semantics(tokens)
            if item.kind is not TemporalKind.NOW and not _inside_name(item, name_spans)
        ),
        maintain=maintain,
        release=(
            release_frame(text, tokens)
            if utterance.speech_act is not SpeechAct.AUTOMATION
            and not text.rstrip().endswith("?")
            else None
        ),
    )
