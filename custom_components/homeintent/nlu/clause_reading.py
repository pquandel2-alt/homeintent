"""Reading coordinated clauses of a language document (7.7, public).

One reader for what a clause *says*: its operation (lexical ACTION, a
comparative degree, a percentage or a temperature) and its target
descriptions (genus × place × feature × quantity). The genus compiler, the
discourse compiler and the meaning IR (``meaning_ir.ground_meaning``) all
read clauses here; none of them reconstructs meaning from another
compiler's result. Nothing here resolves a single device or executes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..entities import EntitySnapshot, normalize_for_compare
from .degree_semantics import percent_value, relative_amount, temperature_value
from .device_ontology import analyse_word
from .german_structure import ClauseKind
from .language_frontend import tokenize_language
from .normalize import normalize
from .place_model import build_place_lexicon
from .semantic_catalog import DEGREE_WORDS
from .semantic_lexicon import SemanticKind, analyse_semantics
from .target_resolution import TargetDescription, describe_with_residue, name_index
from .utterance_meaning import segment_clauses

__all__ = (
    "ClauseMeaning", "FILLER_WORDS", "directional_as_degree", "read_operation", "read_clauses",
)

FILLER_WORDS = frozenset({
    "etwas", "bisschen", "ein", "wenig", "bitte", "mal", "prozent", "grad",
    "noch", "mehr", "viel", "deutlich", "ganz", "wieder", "sofort", "jetzt",
    "gleich", "kurz", "schnell", "auch", "dann", "danach", "anschliessend",
})


@dataclass(frozen=True)
class ClauseMeaning:
    """Operation and targets of one coordinated clause."""

    text: str
    actions: frozenset[str]
    degree: tuple[str, int] | None
    percent: int | None
    temperature: float | None
    descriptions: tuple[TargetDescription, ...]
    residue: tuple[str, ...] = ()

    @property
    def has_operation(self) -> bool:
        return bool(
            self.actions or self.degree or self.percent is not None
            or self.temperature is not None
        )



def read_operation(analysis_text: str) -> tuple[frozenset[str], frozenset[str]]:
    """ACTION values and the words that expressed an operation."""
    analysis = analyse_semantics(analysis_text)
    actions: set[str] = set()
    words: set[str] = set()
    for span in analysis.spans:
        if span.kind not in {SemanticKind.ACTION, SemanticKind.COMMAND_MARKER}:
            continue
        parts = normalize_for_compare(span.text).split()
        if span.kind is SemanticKind.ACTION:
            actions.add(str(span.value))
        # Discontinuous verb frames ("mach ... auf") only own their first
        # and last word; the object in between stays a target.
        words.update({parts[0], parts[-1]} if len(parts) > 1 else set(parts))
    return frozenset(actions), frozenset(words)


_PLACE_GLUE_WORDS = frozenset({
    "im", "in", "der", "dem", "den", "die", "das", "am", "an", "aus", "ein", "zu",
    "auf", "hoch", "runter", "bitte", "auch",
})
_SUBORDINATING_OR_EXCEPTING = frozenset({
    "dass", "ob", "wo", "wohin", "woher", "weil", "damit", "obwohl", "nachdem",
    "bevor", "welche", "welcher", "welches", "dessen", "deren", "ausser",
    "ausnahme", "ausgenommen", "sondern", "stattdessen", "nachricht",
    "benachrichtigung", "nachrichten",
})
_NON_COORDINATE_CLAUSES = frozenset({
    ClauseKind.RELATIVE, ClauseKind.REPAIR, ClauseKind.EXCLUSION,
    ClauseKind.CONDITION, ClauseKind.TEMPORAL,
})


def read_clauses(
    document: object, entities: Sequence[EntitySnapshot]
) -> tuple[ClauseMeaning, ...]:
    structure = getattr(document, "structure")
    if any(clause.kind in _NON_COORDINATE_CLAUSES for clause in structure.clauses):
        # Repairs ("äh nein, das Wohnzimmerlicht"), relative restrictions
        # (", die noch an sind"), exclusions and conditions have their own
        # dedicated semantics; this compiler only composes coordination.
        return ()
    # The normalized surface has shells and fillers removed ("Wäre es
    # möglich, ..." -> "bitte ...") while keeping every meaning word.
    source = getattr(getattr(document, "utterance"), "normalized_text")
    tokens = tokenize_language(source)
    words = [token.canonical for token in tokens if token.is_word]
    if any(word in _SUBORDINATING_OR_EXCEPTING for word in words):
        # Subordinate content ("…, dass das Essen fertig ist"), relational
        # clauses ("…, wo ein Fenster offen steht") and exceptions ("mit
        # Ausnahme von") belong to their dedicated compilers.
        return ()
    lexicon = build_place_lexicon(entities)
    names = name_index(entities)
    meanings: list[ClauseMeaning] = []
    ranges: list[tuple[int, int]] = []
    for start, end in segment_clauses(tokens):
        words_here = [token.canonical for token in tokens[start:end] if token.is_word]
        place_words = {
            word for mention in lexicon.scan(words_here)
            for word in words_here[mention.token_start:mention.token_end]
        }
        if ranges and words_here and all(
            word in place_words or word in _PLACE_GLUE_WORDS for word in words_here
        ):
            # "in Küche und Flur aus": a coordinated place, not a clause.
            ranges[-1] = (ranges[-1][0], end)
            continue
        ranges.append((start, end))
    # "Schalte in Küche | und Flur alle Lichter aus": a leading segment that
    # ends in a place and holds no device word coordinates its place with
    # the next segment.
    joined: list[tuple[int, int]] = []
    for start, end in ranges:
        if joined:
            previous_start, previous_end = joined[-1]
            previous_words = [
                token.canonical for token in tokens[previous_start:previous_end] if token.is_word
            ]
            mentions = lexicon.scan(previous_words)
            if (
                mentions
                and mentions[-1].token_end == len(previous_words)
                and not any(analyse_word(word) is not None for word in previous_words)
                and not read_operation(normalize(" ".join(previous_words)))[0]
            ):
                joined[-1] = (previous_start, end)
                continue
        joined.append((start, end))
    ranges = joined
    for start, end in ranges:
        clause_tokens = tokens[start:end]
        if not any(token.is_word for token in clause_tokens):
            continue
        text = source[clause_tokens[0].start:clause_tokens[-1].end]
        normalized = normalize(text)
        actions, operation_words = read_operation(normalized)
        degree_words = [
            DEGREE_WORDS[token.canonical]
            for token in clause_tokens
            if token.canonical in DEGREE_WORDS
        ]
        degree = degree_words[0] if len(set(degree_words)) == 1 else None
        temperature = temperature_value(normalized)
        percent = None if temperature is not None else percent_value(normalized)
        spoken_values = [
            int(clause_tokens[index].canonical)
            for index in range(len(clause_tokens) - 1)
            if clause_tokens[index].is_number
            and clause_tokens[index].canonical.isdigit()
            and clause_tokens[index + 1].canonical in {"prozent", "%"}
        ]
        if any(value > 100 for value in spoken_values):
            return ()
        if degree is not None and percent is not None and "%" not in text and "prozent" not in normalized.casefold():
            percent = None
        words_here = [token.canonical for token in clause_tokens if token.is_word]
        if (
            "ein" in words_here
            and any(word.startswith("stell") for word in words_here)
            and percent is None and temperature is None and degree is None
        ):
            # "Stelle die Heizung ein" asks to configure a value, it does not
            # mean "switch on" (existing contract F11).
            return ()
        spoken_step = relative_amount(normalized)
        ignore = frozenset(operation_words | FILLER_WORDS | frozenset(DEGREE_WORDS))
        if spoken_step is not None:
            ignore |= {"um"}
        descriptions, residue = describe_with_residue(
            clause_tokens, entities, lexicon=lexicon, ignore=ignore, names=names
        )
        if residue:
            # An unexplained content word may change the meaning entirely;
            # never execute around it.  The clause is kept (with its
            # residue) so a multi-clause turn can name the part it did not
            # understand instead of silently dropping it (finding S3).
            meanings.append(ClauseMeaning(
                text=text, actions=actions, degree=degree, percent=percent,
                temperature=temperature, descriptions=descriptions,
                residue=residue,
            ))
            continue
        genera_here = {key for item in descriptions for key in item.genera}
        actions, degree = directional_as_degree(actions, degree, words_here, genera_here)
        if spoken_step is not None and degree is None and actions in (
            frozenset({"open"}), frozenset({"close"})
        ):
            # "um 20 Prozent runter", "drei Grad hoch": a direction particle
            # with an amount is a step, for every genus (7.6.1).
            sign = 1 if actions == {"open"} else -1
            if genera_here and set(genera_here) <= _MOVABLE_GENERA:
                degree = ("level", sign)
            else:
                properties = {_GENUS_PROPERTY.get(key) for key in genera_here}
                if len(properties) == 1 and None not in properties:
                    degree = (next(iter(properties)) or "", sign)
            if degree is not None:
                actions = frozenset()
        if degree is not None and spoken_step is not None:
            # "zwei Grad wärmer", "30 Prozent höher": the number is the size
            # of the step, never an absolute target (7.6.1).
            percent = None
            temperature = None
        if (
            not actions and degree is None and percent is None and temperature is None
            and any(word.startswith("mach") for word in words_here)
            and any(item.mass and "light" in item.genera for item in descriptions)
            and not any(
                words_here[index + 1] in {"licht", "beleuchtung"}
                for index, word in enumerate(words_here[:-1])
                if word in {"das", "die", "dem", "den"}
            )
        ):
            # Only the article-less collocation "Licht machen" means switching
            # on; "Mach das Licht" lacks its particle and stays unclear.
            # "Mach (mal) Licht im Flur": making light is switching it on.
            actions = frozenset({"turn_on"})
        meanings.append(ClauseMeaning(
            text=text,
            actions=actions,
            degree=degree,
            percent=percent,
            temperature=temperature,
            descriptions=descriptions,
        ))
    # Coordinated objects share the following predicate:
    # "Mach das Licht und die Heizung aus" -> both clauses operate "aus".
    merged: list[ClauseMeaning] = []
    carried: list[TargetDescription] = []
    for meaning in meanings:
        if meaning.residue and not meaning.has_operation:
            merged.append(meaning)
            continue
        if not meaning.has_operation:
            if meaning.descriptions:
                carried.extend(meaning.descriptions)
                continue
            continue
        if carried:
            meaning = ClauseMeaning(
                meaning.text, meaning.actions, meaning.degree, meaning.percent,
                meaning.temperature, (*carried, *meaning.descriptions),
                meaning.residue,
            )
            carried = []
        merged.append(meaning)
    if carried and merged:
        last = merged[-1]
        merged[-1] = ClauseMeaning(
            last.text, last.actions, last.degree, last.percent, last.temperature,
            (*last.descriptions, *carried), last.residue,
        )
    return tuple(merged)


# Genera that physically move ("hoch" opens them); every other genus turned
# "hoch"/"runter" changes its scalar property instead.
_MOVABLE_GENERA = frozenset({"shutter", "raffstore", "awning", "curtain", "garage_door", "window", "door", "valve"})
_GENUS_PROPERTY = {
    "heating": "temperature", "light": "brightness", "media": "volume", "tv": "volume",
    "radio": "volume", "music": "volume", "fan": "speed",
}


def directional_as_degree(
    actions: frozenset[str],
    degree: tuple[str, int] | None,
    words: Sequence[str],
    genera: set[str] | frozenset[str],
) -> tuple[frozenset[str], tuple[str, int] | None]:
    """"Dreh die Heizung hoch", "Dreh das Radio runter": turning a control
    up or down changes the device's scalar property, not its position.

    Shared by the direct and the discourse compiler (one lexical rule).
    """
    if (
        actions in (frozenset({"open"}), frozenset({"close"}))
        and degree is None
        and any(word.startswith("dreh") for word in words)
        and genera and not set(genera) & _MOVABLE_GENERA
    ):
        properties = {_GENUS_PROPERTY.get(key) for key in genera}
        if len(properties) == 1 and None not in properties:
            return frozenset(), (next(iter(properties)) or "", 1 if actions == {"open"} else -1)
    return actions, degree
