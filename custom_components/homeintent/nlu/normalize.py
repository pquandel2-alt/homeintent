"""Text normalization: runs once, before any parser sees the sentence (see
v2 plan Phase 3). Kept intentionally small - hassil's ``recognize()``
already tolerates mixed case, extra whitespace, trailing punctuation and
spelled-out German numbers natively (verified empirically against
hassil==3.11.0), so this module only covers what hassil does *not* handle:
the "%" and "°" symbols, which have no built-in meaning for
``RangeSlotList``, and (HomeIntent plan V4.1, "Advanced German Language")
most of a fixed set of German filler particles that carry no command
meaning of their own.

Must never make a semantic decision (e.g. never resolve a spoken name like
"Wohnzimmerlampe" towards an entity_id here) - that is Entity Resolution's
job, further down the pipeline. Pure text-to-text, no HA/hassil imports.
Filler-word removal stays within that rule: it never picks between
candidate intents/entities, it just deletes particles that are already
proven meaningless to this grammar (see context_followup/brightness.yaml's
pre-existing "etwas heller" == "heller" pair) - stripping them once here
replaces adding "[mal|doch|kurz|etwas|ein bisschen]" to every single
sentence across all 8 grammar groups (the plan's own "keine YAML-
Kombinatorik" goal), a fully general fix instead of a per-file one.

"bitte" is deliberately NOT stripped here even though the plan lists it as
a filler word: several sentences (e.g. light_switch.yaml's "bitte {name}
an") use it as their only imperative marker with no separate verb - it is
already handled per-sentence via existing "[bitte]"/"[kannst du|bitte]"
grammar branches, and blanket-stripping it here breaks those verb-less
imperatives instead of just being redundant.
"""

from __future__ import annotations

import re

# Real users type "30%"/"30 %" in the Assist chat UI, not the word
# "Prozent" - normalizing it to the word form up front keeps hassil's
# grammar (and RangeSlotList, which has no built-in "%"-symbol parsing -
# RangeType.PERCENTAGE is just a label) the single source of truth for the
# sentence shape.
_PERCENT_SYMBOL_RE = re.compile(r"(\d)\s*%")

# Same reasoning as the "%" case above (HomeIntent plan V4.2, "Number
# Normalization": "21 Grad", "21°" must normalize to the same value) -
# climate_extended/temperature.yaml's grammar only knows the literal word
# "Grad", never the "°" symbol.
_DEGREE_SYMBOL_RE = re.compile(r"(\d)\s*°")

# "ein bisschen" listed before the single-word particles only for
# readability - alternation order doesn't matter here since none of the
# other words are prefixes of each other's tokens. "bitte" excluded, see
# module docstring.
_FILLER_RE = re.compile(r"\b(mal|ma|doch|kurz)\b", re.IGNORECASE)
_HESITATION_RE = re.compile(r"(?<!\w)(?:äh+m*|eh+m+)(?!\w)[,;:]?", re.I)
_LEADING_DISCOURSE_FILLER_RE = re.compile(
    r"^\s*(?:(?:also|okay|ok|gut|nun|na\s+gut)\s*[,;:]?\s+)+", re.I
)
_TRAILING_DISCOURSE_FILLER_RE = re.compile(
    r"(?:[,;:]\s*|\s+)(?:(?:also|okay|ok|gut|nun|na\s+gut)\s*)+[.!]*$", re.I
)
_NON_DEGREE_FILLER_RE = re.compile(
    r"\b(?:ein\s+bisschen|etwas)\b"
    r"(?!\s+(?:heller|dunkler|wärmer|kälter|lauter|leiser|schneller|langsamer)\b)",
    re.IGNORECASE,
)

_POLITE_MODAL_RE = re.compile(
    r"\b(?:könntest|koenntest|würdest|wuerdest)\s+du\b", re.IGNORECASE
)
_TRIGGER_DISCOURSE_RE = re.compile(r"\b(?:jedes\s+mal|immer)\s+wenn\b", re.IGNORECASE)
_SHORT_DRIVE_IMPERATIVE_RE = re.compile(
    r"^(?P<prefix>(?:(?:kannst\s+du|bitte)\s+)?)fahr\b", re.IGNORECASE
)
_PROPERTY_WISH_RE = re.compile(
    r"^\s*ich\s+(?:hätte|haette|möchte|moechte)\s+(?:gern|gerne)\s+"
    r"(?P<body>(?:die\s+)?(?:lautstärke|lautstaerke|temperatur|helligkeit|"
    r"position|luftfeuchtigkeit)\b.+\bauf\s+.+)$",
    re.I,
)
_SENSOR_READING_QUERY_RE = re.compile(
    r"^\s*(?:was|welchen\s+wert)\s+zeig\w*\s+(?:der|die|das)\s+"
    r"(?P<property>temperatur|luftfeuchtigkeit|feuchtigkeit|leistung|energie|batterie)"
    r"(?:s?sensor)?\s+(?P<location>(?:im|in\s+der|in\s+dem)\s+.+?)"
    r"\s+an\s*[?.!]*$",
    re.I,
)

_NATURAL_SHELL_REWRITES = (
    (
        re.compile(r"^\s*(?:wäre|waere)\s+es\s+(?:dir\s+)?möglich[,:]?\s*", re.I),
        "bitte ",
    ),
    (
        re.compile(r"^\s*ich\s+(?:hätte|haette|möchte|moechte)\s+(?:gern|gerne)\s+", re.I),
        "bitte ",
    ),
    (
        re.compile(r"^\s*sorg(?:e|en)?\s+(?:bitte\s+)?dafür[,:]?\s+dass\s+", re.I),
        "bitte ",
    ),
    (
        re.compile(
            r"^\s*(?:sag|sage|nenn|nenne)\s+(?:mir\s+)?"
            r"(?:bitte\s*)?[,:]?\s*",
            re.I,
        ),
        "",
    ),
    (re.compile(r"^\s*wie\s+steht\s+es\s+um\s+(?:die|den|das)?\s*", re.I), "wie ist "),
)

_SEPARABLE_INFINITIVE_REWRITES = (
    (re.compile(r"\bein(?:(?:\s+)?zu)?(?:\s+)?schalten\b", re.I), "einschalten"),
    (re.compile(r"\baus(?:(?:\s+)?zu)?(?:\s+)?schalten\b", re.I), "ausschalten"),
    (re.compile(r"\ban(?:(?:\s+)?zu)?(?:\s+)?machen\b", re.I), "anmachen"),
    (re.compile(r"\baus(?:(?:\s+)?zu)?(?:\s+)?machen\b", re.I), "ausmachen"),
    (re.compile(r"\bhoch(?:(?:\s+)?zu)?(?:\s+)?fahren\b", re.I), "hochfahren"),
    (re.compile(r"\bherunter(?:(?:\s+)?zu)?(?:\s+)?fahren\b", re.I), "herunterfahren"),
)

_FRACTION_REWRITES = (
    (re.compile(r"\b(?:zu|auf)\s+drei\s+vierteln?\b", re.I), "auf 75 Prozent"),
    (re.compile(r"\b(?:zu|auf)\s+(?:einem|ein)\s+viertel\b", re.I), "auf 25 Prozent"),
    (re.compile(r"\b(?:zu|auf)\s+(?:der\s+)?hälfte\b", re.I), "auf 50 Prozent"),
    (re.compile(r"\bzur\s+hälfte\b", re.I), "auf 50 Prozent"),
)

_NUMBER_UNITS = {
    "null": 0,
    "ein": 1,
    "eins": 1,
    "eine": 1,
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fünf": 5,
    "fuenf": 5,
    "sechs": 6,
    "sieben": 7,
    "acht": 8,
    "neun": 9,
    "zehn": 10,
    "elf": 11,
    "zwölf": 12,
    "zwoelf": 12,
}
_NUMBER_TENS = {
    "zwanzig": 20,
    "dreißig": 30,
    "dreissig": 30,
    "vierzig": 40,
    "fünfzig": 50,
    "fuenfzig": 50,
    "sechzig": 60,
    "siebzig": 70,
    "achtzig": 80,
    "neunzig": 90,
}
_NUMBER_TEENS = {
    "dreizehn": 13,
    "vierzehn": 14,
    "fünfzehn": 15,
    "fuenfzehn": 15,
    "sechzehn": 16,
    "siebzehn": 17,
    "achtzehn": 18,
    "neunzehn": 19,
}
_NUMBER_WITH_UNIT_RE = re.compile(
    r"\b(?P<number>[a-zäöüß]+)\s+(?P<unit>grad|prozent)\b", re.I
)


def _german_number(word: str) -> int | None:
    value = word.casefold()
    if value in _NUMBER_UNITS:
        return _NUMBER_UNITS[value]
    if value in _NUMBER_TENS:
        return _NUMBER_TENS[value]
    if value in _NUMBER_TEENS:
        return _NUMBER_TEENS[value]
    if value == "hundert":
        return 100
    if "und" in value:
        one, ten = value.split("und", 1)
        if one in _NUMBER_UNITS and ten in _NUMBER_TENS and 1 <= _NUMBER_UNITS[one] <= 9:
            return _NUMBER_UNITS[one] + _NUMBER_TENS[ten]
    return None


def german_number(word: str) -> int | None:
    """Public view of the one shared German number-word reader (0-100)."""
    return _german_number(word)


def _replace_number_with_unit(match: re.Match[str]) -> str:
    value = _german_number(match.group("number"))
    return match.group(0) if value is None else f"{value} {match.group('unit')}"

# Frequent speech-to-text tokenizations. These are orthographic rewrites,
# not semantic guesses: both sides are the same German device/unit/verb.
_STT_REWRITES = (
    (re.compile(r"\bkönnteste\b", re.IGNORECASE), "kannst du"),
    (re.compile(r"\bkoennteste\b", re.IGNORECASE), "kannst du"),
    (re.compile(r"\bham\s+wir\b", re.IGNORECASE), "haben wir"),
    (re.compile(r"\bisses\b", re.IGNORECASE), "ist es"),
    (re.compile(r"\bpro\s+cent\b", re.IGNORECASE), "Prozent"),
    (re.compile(r"\broll[\s-]+laden\b", re.IGNORECASE), "Rollladen"),
    (re.compile(r"\bgrad\s+(?:c|celsius)\b", re.IGNORECASE), "Grad"),
    (re.compile(r"\b(an|aus)\s+machen\b", re.IGNORECASE), r"\1machen"),
    (re.compile(r"\b(hoch|runter|herunter)\s+fahren\b", re.IGNORECASE), r"\1fahren"),
)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = _LEADING_DISCOURSE_FILLER_RE.sub("", text)
    text = _TRAILING_DISCOURSE_FILLER_RE.sub("", text)
    text = _HESITATION_RE.sub(" ", text)
    text = _PERCENT_SYMBOL_RE.sub(r"\1 Prozent", text)
    text = _DEGREE_SYMBOL_RE.sub(r"\1 Grad", text)
    text = _POLITE_MODAL_RE.sub("kannst du", text)
    text = _TRIGGER_DISCOURSE_RE.sub("wenn", text)
    text = _SHORT_DRIVE_IMPERATIVE_RE.sub(r"\g<prefix>fahre", text)
    text = _SENSOR_READING_QUERY_RE.sub(r"\g<property> \g<location>", text)
    text = _PROPERTY_WISH_RE.sub(r"bitte stelle \g<body>", text)
    for pattern, replacement in _NATURAL_SHELL_REWRITES:
        text = pattern.sub(replacement, text)
    for pattern, replacement in _SEPARABLE_INFINITIVE_REWRITES:
        text = pattern.sub(replacement, text)
    for pattern, replacement in _FRACTION_REWRITES:
        text = pattern.sub(replacement, text)
    text = _NUMBER_WITH_UNIT_RE.sub(_replace_number_with_unit, text)
    text = re.sub(r"\bregl(?:e|en|st|t)?\b", "stelle", text, flags=re.I)
    for pattern, replacement in _STT_REWRITES:
        text = pattern.sub(replacement, text)
    text = _FILLER_RE.sub(" ", text)
    text = _NON_DEGREE_FILLER_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()
