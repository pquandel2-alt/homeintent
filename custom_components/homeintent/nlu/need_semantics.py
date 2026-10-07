"""Need semantics: statements about how a person feels -> desired effect.

"Mir ist kalt", "Hier ist es zu hell", "Im Bad ist es stickig", "Die Sonne
blendet", "Ich gehe schlafen" are not commands.  They state a need.  This
module maps such statements, through a typed table (data, below), to a
desired *effect* on a property ("temperature up", "brightness down",
"ventilate", "volume down") or to a *routine concept* ("sleep", "leave",
"movie").  The place comes from the sentence, else from the speaking
satellite, else from the conversation context.

The rules are stated over word meaning and sentence shape (statement, not
question; not negated; experiencer "mir"/"ich" or copula "es ist"), never
over complete sentences.  Nothing here executes; ``need_compiler`` in the
engine grounds effects to concrete operations that run through the normal
validator, policy and confirmation path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Mapping, Sequence

from ..entities import normalize_for_compare

__all__ = (
    "NeedKind",
    "NeedMeaning",
    "ROUTINE_CONCEPTS",
    "routine_concept_by_key",
    "routine_concept_of_compound",
    "RoutineConcept",
    "interpret_need",
)


class NeedKind(Enum):
    WARMER = auto()
    COOLER = auto()
    BRIGHTER = auto()
    DIMMER = auto()
    GLARE = auto()
    VENTILATE = auto()
    QUIETER = auto()
    LOUDER = auto()
    ROUTINE = auto()


# Adjectives/verbs of sensation -> need.  "zu" before an adjective only
# intensifies; the adjective carries the meaning.  Keys are compare
# normalized (ä -> ae ...).
NEED_WORDS: Mapping[str, NeedKind] = {
    # cold
    "kalt": NeedKind.WARMER, "kaelter": NeedKind.WARMER, "eisig": NeedKind.WARMER,
    "frisch": NeedKind.WARMER, "kuehl": NeedKind.WARMER, "frostig": NeedKind.WARMER,
    "friere": NeedKind.WARMER, "frieren": NeedKind.WARMER, "friert": NeedKind.WARMER,
    "frier": NeedKind.WARMER, "froestelt": NeedKind.WARMER, "froestele": NeedKind.WARMER,
    "bibbere": NeedKind.WARMER, "zittere": NeedKind.WARMER,
    "waerme": NeedKind.WARMER,
    # warm
    "warm": NeedKind.COOLER, "heiss": NeedKind.COOLER, "schwuel": NeedKind.COOLER,
    "ueberhitzt": NeedKind.COOLER, "schwitze": NeedKind.COOLER,
    "schwitzen": NeedKind.COOLER, "schwitzt": NeedKind.COOLER, "backofen": NeedKind.COOLER,
    # dark
    "dunkel": NeedKind.BRIGHTER, "finster": NeedKind.BRIGHTER, "duester": NeedKind.BRIGHTER,
    "zappenduster": NeedKind.BRIGHTER,
    # bright
    "hell": NeedKind.DIMMER, "grell": NeedKind.DIMMER, "gleissend": NeedKind.DIMMER,
    "blendet": NeedKind.GLARE, "blenden": NeedKind.GLARE, "blendend": NeedKind.GLARE,
    # air
    "stickig": NeedKind.VENTILATE, "muffig": NeedKind.VENTILATE, "mief": NeedKind.VENTILATE,
    "miefig": NeedKind.VENTILATE, "feucht": NeedKind.VENTILATE, "verbraucht": NeedKind.VENTILATE,
    "dunstig": NeedKind.VENTILATE, "beschlagen": NeedKind.VENTILATE,
    # sound
    "laut": NeedKind.QUIETER, "droehnt": NeedKind.QUIETER,
    # A device that disturbs (7.6.0): "Das Radio nervt" -> quieter or off, asked.
    "nervt": NeedKind.QUIETER, "stoert": NeedKind.QUIETER, "nerven": NeedKind.QUIETER,
    "leise": NeedKind.LOUDER,
}
# "Ich sehe/höre nichts": perception verb + negative object -> need.
_PERCEPTION_NEEDS: Mapping[str, NeedKind] = {
    "sehe": NeedKind.BRIGHTER, "seh": NeedKind.BRIGHTER, "sieht": NeedKind.BRIGHTER,
    "hoere": NeedKind.LOUDER, "hoer": NeedKind.LOUDER, "versteh": NeedKind.LOUDER,
    "verstehe": NeedKind.LOUDER,
    # "Ich kann kaum lesen / nichts erkennen" (7.6.0).
    "lesen": NeedKind.BRIGHTER, "erkennen": NeedKind.BRIGHTER, "sehen": NeedKind.BRIGHTER,
    "hoeren": NeedKind.LOUDER, "verstehen": NeedKind.LOUDER,
}
# Adverbs that make a following perception verb a need ("kaum lesen").
_HARDLY = frozenset({"kaum", "nichts", "nix", "schlecht", "schwer"})
_NOTHING = frozenset({"nichts", "nix", "kaum", "wenig"})
# A wish for more of a sensation: "etwas mehr Wärme", "mehr Licht".
_MORE_WORDS = frozenset({"mehr"})
_MORE_OBJECTS: Mapping[str, NeedKind] = {
    "waerme": NeedKind.WARMER, "licht": NeedKind.BRIGHTER, "helligkeit": NeedKind.BRIGHTER,
    "luft": NeedKind.VENTILATE, "frischluft": NeedKind.VENTILATE, "ruhe": NeedKind.QUIETER,
}
_NON_ASSERTIVE = frozenset({
    "obwohl", "obgleich", "trotzdem", "wenn", "falls", "sobald", "ob", "dass",
    "merk", "merke", "merken", "notiere", "speichere", "erinnere", "vergiss",
    # Past and counterfactual states are reports, not present needs.
    "war", "waren", "warst", "gewesen", "gestern", "vorhin", "damals", "letzte", "letztes",
    "waere", "waeren", "wuerde", "wuerden",
})
_EXPERIENCERS = frozenset({"mir", "mich", "ich", "uns", "wir"})
_COPULAS = frozenset({"ist", "sind", "wird", "wirds", "isses"})
_NEGATIONS = frozenset({"nicht", "kein", "keine", "nie", "niemals"})
_QUESTION_WORDS = frozenset({
    "wie", "wo", "was", "wann", "warum", "wieso", "weshalb", "welche", "welcher",
    "welches", "ob",
})


@dataclass(frozen=True)
class RoutineConcept:
    """A situation the house has routines for ("schlafen", "Film").

    ``cues`` are words in the utterance that evoke the concept; ``names``
    are word stems that identify a matching scene or script by name,
    alias or description.  ``confirm`` concepts are proposed, never run
    silently (leaving the house or going to bed can lock, arm or switch
    many devices).
    """

    key: str
    label: str
    cues: frozenset[str]
    names: tuple[str, ...]
    confirm: bool


ROUTINE_CONCEPTS: tuple[RoutineConcept, ...] = (
    RoutineConcept(
        "sleep", "schlafen gehen",
        frozenset({"schlafen", "bett", "hinlegen", "nachtruhe", "pennen", "heia", "schlafenszeit"}),
        ("nacht", "schlaf", "bett", "night"),
        True,
    ),
    RoutineConcept(
        "leave", "das Haus verlassen",
        frozenset({"verlasse", "verlassen", "weg", "los", "tschuess", "fahre", "gehe"}),
        ("abwesend", "verlassen", "weg", "away", "tschuess", "abfahrt"),
        True,
    ),
    RoutineConcept(
        "arrive", "nach Hause kommen",
        frozenset({"zurueck", "heim", "zuhause", "daheim", "angekommen", "heimgekommen", "wieder"}),
        ("willkommen", "heim", "zuhause", "anwesend", "zurueck", "ankunft"),
        True,
    ),
    RoutineConcept(
        "movie", "einen Film schauen",
        frozenset({"film", "filme", "fernsehen", "fernsehschauen", "kino", "serie", "netflix", "glotzen"}),
        ("film", "kino", "tv", "fernseh", "movie"),
        False,
    ),
    RoutineConcept(
        "read", "lesen",
        frozenset({"lesen", "buch", "schmoekern"}),
        ("lesen", "lese", "read"),
        False,
    ),
    RoutineConcept(
        "morning", "aufstehen",
        frozenset({"aufstehen", "aufgestanden", "wach", "morgen"}),
        ("morgen", "aufsteh", "morning", "wecken"),
        False,
    ),
)
# Words of intention/announcement that make a first-person sentence about a
# situation a request for its routine.
_INTENTION = frozenset({
    "gehe", "geh", "gehen", "will", "moechte", "wollen", "werde", "mache",
    "verlasse", "bin", "sind", "fahre", "lege", "leg", "schaue", "schauen",
    "gucke", "gucken", "sehen", "gute", "guten",
})


_PREPARE_WORDS = frozenset({"mach", "mache", "machst", "bereite", "bereit", "vorbereiten"})
# Occasion nouns a preparation names ("für die Nacht", "fürs Schlafengehen").
_OCCASION_WORDS: dict[str, frozenset[str]] = {
    "sleep": frozenset({"nacht", "schlafengehen", "schlafen", "bett"}),
    "movie": frozenset({"filmabend", "kinoabend", "film"}),
    "leave": frozenset({"abwesenheit", "abfahrt", "urlaub"}),
}


@dataclass(frozen=True)
class NeedMeaning:
    kind: NeedKind
    cue: str
    routine: RoutineConcept | None = None
    preparation: bool = False


def routine_concept_of_compound(word: str) -> RoutineConcept | None:
    """„Schlafroutine“, „Nachtroutine“, „Filmroutine“ name a routine concept."""
    folded = normalize_for_compare(word)
    if not folded.endswith("routine") or len(folded) <= len("routine"):
        return None
    prefix = folded[: -len("routine")].rstrip("s")
    for concept in ROUTINE_CONCEPTS:
        if any(prefix.startswith(stem) for stem in concept.names) or prefix in concept.cues:
            return concept
    return None


def routine_concept_by_key(key: str) -> RoutineConcept | None:
    return next((concept for concept in ROUTINE_CONCEPTS if concept.key == key), None)


def interpret_need(words: Sequence[str], *, question: bool = False) -> NeedMeaning | None:
    """Recognise a need statement in normalized words, or ``None``."""
    normalized = [normalize_for_compare(word) for word in words]
    word_set = set(normalized)
    if question or word_set & _QUESTION_WORDS and normalized and normalized[0] in _QUESTION_WORDS:
        return None
    if word_set & _NEGATIONS or word_set & _NON_ASSERTIVE:
        # Negated, concessive, conditional or reported sensations and
        # memory instructions do not state a present need.
        return None
    # A routine named by its concept ("Starte die Schlafroutine").
    for word in normalized:
        concept = routine_concept_of_compound(word)
        if concept is not None:
            return NeedMeaning(NeedKind.ROUTINE, word, concept)
    # Routine concepts: first-person announcement + concept cue.
    first_person = bool(word_set & {"ich", "wir", "bin", "sind"}) or (
        len(normalized) >= 2 and normalized[:2] in (["gute", "nacht"],)
    )
    if first_person and word_set & _INTENTION:
        for concept in ROUTINE_CONCEPTS:
            cues = word_set & concept.cues
            if not cues:
                continue
            if concept.key == "leave" and not (
                word_set & {"haus", "wohnung", "weg", "los", "jetzt", "tschuess", "verlasse", "verlassen"}
            ):
                continue
            if concept.key == "arrive" and not (
                word_set & {"zurueck", "heim", "zuhause", "daheim", "angekommen", "heimgekommen"}
                or {"wieder", "da"} <= word_set  # "Wir sind wieder da" (7.9.3 A3)
            ):
                continue
            if concept.key == "morning" and not word_set & {"aufstehen", "aufgestanden", "wach"}:
                continue
            return NeedMeaning(NeedKind.ROUTINE, sorted(cues)[0], concept)
    # Preparing an occasion ("Mach alles für die Nacht fertig", "Bereite
    # den Filmabend vor"): the same routine concept as announcing it
    # (7.6.1). Without any bound or discoverable routine the goal dialog
    # takes over (``NeedMeaning.preparation``).
    if word_set & _PREPARE_WORDS and word_set & {"fertig", "vorbereiten", "vor", "bereit"}:
        for concept in ROUTINE_CONCEPTS:
            occasion = word_set & _OCCASION_WORDS.get(concept.key, frozenset())
            if occasion:
                return NeedMeaning(NeedKind.ROUTINE, sorted(occasion)[0], concept, preparation=True)
    if normalized[:2] == ["gute", "nacht"] and len(normalized) <= 3:
        sleep = next(concept for concept in ROUTINE_CONCEPTS if concept.key == "sleep")
        return NeedMeaning(NeedKind.ROUTINE, "nacht", sleep)
    # Perception: "ich sehe nichts", "man hört kaum was".
    for index, word in enumerate(normalized):
        if word in _PERCEPTION_NEEDS and any(item in _NOTHING for item in normalized[index + 1:index + 4]):
            return NeedMeaning(_PERCEPTION_NEEDS[word], word)
        if word in _PERCEPTION_NEEDS and any(item in _HARDLY for item in normalized[max(0, index - 2):index]):
            return NeedMeaning(_PERCEPTION_NEEDS[word], word)
    # Wish for more: "ich hätte gern etwas mehr Wärme im Bad".
    for index, word in enumerate(normalized):
        if word in _MORE_WORDS:
            for item in normalized[index + 1:index + 3]:
                if item in _MORE_OBJECTS:
                    return NeedMeaning(_MORE_OBJECTS[item], item)
    # Sensation adjective/verb with experiencer, copula or intensifier.
    has_frame = bool(word_set & _EXPERIENCERS or word_set & _COPULAS or "zu" in word_set)
    for index, word in enumerate(normalized):
        kind = NEED_WORDS.get(word)
        if kind is None:
            continue
        if kind in {NeedKind.GLARE} or word.endswith(("e", "t", "en")) and word in {
            "friere", "frieren", "friert", "froestelt", "froestele", "bibbere", "zittere",
            "schwitze", "schwitzen", "schwitzt", "droehnt", "nervt", "stoert", "nerven",
        }:
            return NeedMeaning(kind, word)
        if not has_frame:
            continue
        # "zu" must be the intensifier before this adjective, never the
        # closing particle ("Mach das Fenster zu").
        return NeedMeaning(kind, word)
    return None
