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
# "Mach jetzt das Licht an": "jetzt"/"sofort" only say "now", which every
# plain command means anyway (7.5.0). "ab jetzt", "bis jetzt", "von jetzt
# an" carry meaning and stay.
_NOW_FILLER_RE = re.compile(
    r"(?<!\bab\s)(?<!\bbis\s)(?<!\bvon\s)\b(?:jetzt|sofort)\b(?!\s+an\b(?=.*\bvon\b))",
    re.IGNORECASE,
)
_HESITATION_RE = re.compile(r"(?<!\w)(?:äh+m*|eh+m+)(?!\w)[,;:]?", re.I)
_LEADING_DISCOURSE_FILLER_RE = re.compile(
    r"^\s*(?:(?:also|okay|ok|gut|nun|na\s+gut)\s*[,;:]?\s+)+", re.I
)
_TRAILING_DISCOURSE_FILLER_RE = re.compile(
    r"(?:[,;:]\s*|\s+)(?:(?:also|okay|ok|gut|nun|na\s+gut)\s*)+[.!]*$", re.I
)
_NON_DEGREE_FILLER_RE = re.compile(
    r"\b(?:ein\s+bisschen|etwas)\b"
    r"(?!\s+(?:heller|dunkler|wärmer|kälter|lauter|leiser|schneller|langsamer|höher|hoeher|niedriger|tiefer)\b)",
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

# Spoken clitics: the pronoun is fused onto the verb ("kannste", "mach's").
# One table, expanded word by word before any meaning is read.
_CLITICS = {
    "kannste": "kannst du", "kannstes": "kannst du es", "haste": "hast du",
    "willste": "willst du", "machste": "machst du", "biste": "bist du",
    "mach's": "mach das", "machs": "mach das", "mach’s": "mach das",
    "dreh's": "dreh das", "drehs": "dreh das", "schalt's": "schalt das",
    "stell's": "stell das", "gibt's": "gibt es", "gibts": "gibt es",
}
_CLITIC_RE = re.compile(
    r"(?<![\wäöüß'’])(" + "|".join(re.escape(key) for key in sorted(_CLITICS, key=len, reverse=True)) + r")(?![\wäöüß'’])",
    re.IGNORECASE,
)


def _expand_clitic(match: re.Match[str]) -> str:
    return _CLITICS[match.group(1).casefold()]


def expand_clitics(text: str) -> str:
    """"Kannste die Rollos …" -> "kannst du die Rollos …"."""
    return _CLITIC_RE.sub(_expand_clitic, text)


_WHITESPACE_RE = re.compile(r"\s+")


_EMBEDDING_SHELLS = (
    ("ich", "frage", "mich"), ("ich", "frag", "mich"), ("ich", "wüsste", "gern"),
    ("ich", "wüsste", "gerne"), ("ich", "wuesste", "gern"), ("ich", "möchte", "wissen"),
    ("ich", "moechte", "wissen"), ("ich", "will", "wissen"), ("weißt", "du"),
    ("weisst", "du"), ("kannst", "du", "mir", "sagen"), ("sag", "mir", "mal"),
    ("mich", "würde", "interessieren"), ("mich", "interessiert"),
)
_EMBEDDED_WH = frozenset({"wie", "wo", "was", "wann", "warum", "wieso", "welche", "welcher", "welches", "wieviel", "wer"})
_WH_PHRASE_WORDS = frozenset({"warm", "kalt", "hell", "viel", "viele", "lange", "spät", "spaet", "hoch", "feucht", "laut"})


def _unembed_question(text: str) -> str:
    """"Ich frage mich, ob das Fenster offen ist" -> "ist das Fenster offen?".

    An embedded question keeps its verb last; the question it embeds has the
    finite verb first (yes/no) or after the wh-phrase.  Pure word order, no
    meaning is added or removed.
    """
    words = text.strip().rstrip(".!?").replace(",", " ").split()
    lowered = [word.casefold() for word in words]
    for shell in _EMBEDDING_SHELLS:
        if tuple(lowered[:len(shell)]) != shell:
            continue
        rest, keys = words[len(shell):], lowered[len(shell):]
        if len(rest) < 3:
            return text
        finite = rest[-1]
        if keys[0] == "ob":
            body = rest[1:-1]
            return f"{finite} {' '.join(body)}?"
        if keys[0] in _EMBEDDED_WH:
            head = 2 if len(keys) > 3 and keys[1] in _WH_PHRASE_WORDS else 1
            return f"{' '.join(rest[:head])} {finite} {' '.join(rest[head:-1])}?".replace("  ", " ")
        return text
    return text


_OPEN_STATE_WORDS = frozenset({"offen", "auf", "geöffnet", "geoeffnet", "zu", "geschlossen"})


def _standing_state(text: str) -> str:
    """"Steht noch ein Fenster offen?" -> "Ist noch ein Fenster offen?" (7.6.0).

    "stehen" + an open/closed state within the next six words is the copula
    of that state; anything else ("Die Heizung steht auf 22 Grad") stays.
    """
    parts = re.split(r"(\s+)", text)
    words = [part for part in parts if part and not part.isspace()]
    keys = [word.strip("?.!,").casefold() for word in words]
    for index, key in enumerate(keys):
        if key in {"steht", "stehen"} and _OPEN_STATE_WORDS & set(keys[index + 1:index + 7]):
            later = keys[index + 1:index + 7]
            if any(word.isdigit() for word in later):
                return text
            replacement = "ist" if key == "steht" else "sind"
            if words[index][:1].isupper():
                replacement = replacement.capitalize()
            count = 0
            for position, part in enumerate(parts):
                if part and not part.isspace():
                    if count == index:
                        parts[position] = replacement + part[len(words[index].rstrip("?.!,")):]
                        return "".join(parts)
                    count += 1
    return text


_KIND_WORDS = frozenset({"lieb", "nett", "gut", "freundlich", "so", "toll", "super", "schoen", "schön", "klasse"})
_PARTICLES = ("aus", "an", "ein", "auf", "zu", "hoch", "runter", "herunter")
_SEPARABLE_STEMS = ("mach", "schalt", "dreh", "fahr", "knips", "lass")


def _imperative(verb: str) -> tuple[str, str] | None:
    """"ausmachst" -> ("mach", "aus"), "anschaltest" -> ("schalte", "an")."""
    low = verb.casefold()
    for particle in _PARTICLES:
        if not low.startswith(particle):
            continue
        rest = low[len(particle):]
        for stem in _SEPARABLE_STEMS:
            if rest in {stem + "st", stem + "est"}:
                return ("schalte" if stem == "schalt" else stem), particle
    return None


def _polite_requests(text: str) -> str:
    """Politeness shells that are requests (7.6.0).

    "Wärst du so lieb und machst X an" / "Es wäre nett, wenn du X ausmachst"
    / "Kannst du X an?" are commands, not state questions.
    """
    stripped = text.strip()
    words = stripped.rstrip("?.!").split()
    keys = [word.strip(",").casefold() for word in words]
    # "Wärst/Wärest du so lieb und machst …"
    if len(keys) > 4 and keys[0] in {"wärst", "waerst", "wärest", "wärt", "bist"} and keys[1] == "du":
        if "und" in keys[2:6] and set(keys[2:keys.index("und", 2)]) <= _KIND_WORDS:
            rest = words[keys.index("und", 2) + 1:]
            if rest and rest[0].casefold().endswith("st"):
                rest = [rest[0][:-2] if not rest[0].casefold().endswith("est") else rest[0][:-3], *rest[1:]]
            return "bitte " + " ".join(rest) + "."
    # "Es wäre nett, wenn du X ausmachst"
    if len(keys) > 5 and keys[:2] == ["es", "wäre"] and keys[2] in _KIND_WORDS and "wenn" in keys[3:5]:
        start = keys.index("wenn", 3)
        if start + 2 < len(keys) and keys[start + 1] == "du":
            verb = _imperative(keys[-1])
            if verb is not None:
                middle = words[start + 2:-1]
                return f"bitte {verb[0]} {' '.join(middle)} {verb[1]}."
    # "Kannst du X an?" without a verb
    if (
        stripped.endswith("?") and len(keys) >= 4 and keys[0] in {"kannst", "könntest", "koenntest"}
        and keys[1] == "du" and keys[-1] in _PARTICLES
        and not any(key.endswith(("en", "st")) and key not in {"den", "einen", "meinen", "deinen"} for key in keys[2:-1])
    ):
        return "bitte " + " ".join(words[2:]) + "."
    return text


def is_polite_request(text: str) -> bool:
    """Whether ``text`` is one of the politeness shells above."""
    return _polite_requests(text) != text


def normalize(text: str) -> str:
    text = _polite_requests(text)
    text = _unembed_question(text)
    text = _CLITIC_RE.sub(_expand_clitic, text)
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
    text = re.sub(r"\bmal\s+eben\b", " ", text, flags=re.IGNORECASE)
    text = _FILLER_RE.sub(" ", text)
    text = _NOW_FILLER_RE.sub(" ", text)
    # "irgendwo" asks without a place restriction (7.6.0).
    text = re.sub(r"\birgendwo\b", " ", text, flags=re.IGNORECASE)
    text = _standing_state(text)
    text = _NON_DEGREE_FILLER_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()
