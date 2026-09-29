"""Same-turn self correction as sentence structure (7.7.1 A1).

"Schalte das Radio aus, ich meine den Fernseher": a *correction marker*
between two parts turns the utterance into **retraction + replacement**.

* The replacement replaces only the fields it names (target, place, side,
  value, operation, time); every other field comes from the retracted part.
* An empty replacement or a pure abort ("nein, doch nicht", "lass mal",
  "stopp") cancels the command: nothing runs.
* When the structure cannot be formed unambiguously, the turn asks back with
  both readings. The retracted part never runs, and never do both parts.
* Several corrections apply in order; the last one wins.

The analysis is lexical and structural (marker lexicon, place lexicon,
device ontology, registry names); it knows no sentence template. It returns
one effective command surface that the ordinary pipeline then reads, so the
validator, policy and executor stay the only instances that decide about
execution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Iterable, Sequence

from ..entities import EntitySnapshot, normalize_for_compare
from .device_ontology import analyse_word
from .normalize import german_number
from .temporal_semantics import TemporalKind, analyse_temporal_semantics

__all__ = (
    "IMPERATIVE_VERBS",
    "utterance_fields",
    "CorrectionKind",
    "SelfCorrection",
    "analyse_self_correction",
    "render_correction",
)


class CorrectionKind(Enum):
    NONE = auto()
    REPLACED = auto()
    CANCELLED = auto()
    AMBIGUOUS = auto()


@dataclass(frozen=True)
class SelfCorrection:
    kind: CorrectionKind
    text: str
    retracted: str = ""
    replacement: str = ""
    markers: tuple[str, ...] = ()
    readings: tuple[str, ...] = ()


# --------------------------------------------------------------- lexicon
# Markers that retract what precedes them wherever they stand.
_STRONG_MARKERS: tuple[tuple[str, ...], ...] = (
    ("ich", "wollte", "sagen"), ("besser", "gesagt"), ("genauer", "gesagt"),
    ("anders", "gesagt"), ("ich", "meine"), ("ich", "meinte"), ("ich", "mein"),
    ("ach", "nee"), ("ach", "nein"), ("ach", "ne"), ("oder", "nee"), ("oder", "nein"),
    ("oder", "besser"), ("oder", "lieber"), ("oder", "doch"), ("nein", "doch"),
    ("nee", "doch"), ("nein",), ("nee",), ("neee",), ("noe",), ("aeh",), ("aehm",),
    ("oeh",), ("oehm",), ("sorry",), ("entschuldigung",), ("pardon",), ("korrigiere",),
    ("korrektur",), ("quatsch",), ("stopp",), ("stop",),
)
# Markers that are ordinary words elsewhere ("Mach halt das Licht an",
# "Mach doch mal ..."): only after a pause (comma, dash, ellipsis).
_WEAK_MARKERS: tuple[tuple[str, ...], ...] = (
    ("halt",), ("moment",), ("warte",), ("also",), ("sprich",), ("lieber",),
    ("oder",), ("doch",), ("ach",), ("ne",), ("ja",), ("eher",), ("vielmehr",),
    ("lass", "mal"), ("lass", "es"), ("lass", "das"), ("vergiss", "es"), ("vergiss", "das"),
    ("doch", "nicht"), ("besser", "nicht"), ("lieber", "nicht"), ("egal",),
)
# Hesitation only: without a complete first part it is a filler.
_HESITATION = frozenset({"aeh", "aehm", "oeh", "oehm", "also", "ja", "ach"})
# A bare "oder" after a complete command may be a correction or a question
# between two options: ask with both readings.
_OPEN_ALTERNATIVE = frozenset({"oder"})
_PAUSE = frozenset({",", "-", "–", "—", "…", ";", ":", "."})
_ABORT_WORDS = frozenset({
    "doch", "nicht", "lass", "lasse", "mal", "vergiss", "es", "das", "egal",
    "nichts", "stopp", "stop", "abbrechen", "abbruch", "warte", "nein", "nee",
    "moment", "sein", "bleiben", "schon", "gut", "ok", "okay", "lieber", "noch",
    "quatsch", "sorry",
})
_ABORT_KEYS = frozenset({"nicht", "lass", "lasse", "vergiss", "egal", "nichts", "stopp", "stop", "abbrechen", "abbruch"})
_FILLER = frozenset({"bitte", "mal", "eben", "doch", "halt", "ja", "also", "danke", "gleich", "jetzt", "dann", "bitteschoen"})
_ARTICLES = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer",
    "alle", "allen", "beide", "beiden", "mein", "meine", "meinen", "unser", "unsere", "unseren",
})
_REFERENCE = frozenset({"das", "die", "den", "der", "dem", "es", "ihn", "sie", "dies", "diese", "diesen", "dieses", "dort", "da"})
_SIDE = {
    "links": "left", "linke": "left", "linken": "left", "linker": "left", "linkes": "left", "linkem": "left",
    "rechts": "right", "rechte": "right", "rechten": "right", "rechter": "right", "rechtes": "right", "rechtem": "right",
    "obere": "upper", "oberen": "upper", "oberer": "upper", "oberes": "upper",
    "untere": "lower", "unteren": "lower", "unterer": "lower", "unteres": "lower",
    "vordere": "front", "vorderen": "front", "vorderer": "front", "vorderes": "front", "vorne": "front",
    "hintere": "back", "hinteren": "back", "hinterer": "back", "hinteres": "back", "hinten": "back",
    "grosse": "big", "grossen": "big", "grosser": "big", "grosses": "big",
    "kleine": "small", "kleinen": "small", "kleiner": "small", "kleines": "small",
    "mittlere": "middle", "mittleren": "middle", "mittlerer": "middle", "mittleres": "middle",
}
_SIDE_SWAP = {"left": ("links", "linke"), "right": ("rechts", "rechte")}
_PARTICLES = frozenset({
    "an", "aus", "ein", "auf", "zu", "hoch", "runter", "rauf", "raus", "rein", "los", "ab",
    "heller", "dunkler", "lauter", "leiser", "waermer", "kaelter", "hoeher", "tiefer",
    "weiter", "zurueck", "halb",
})
_LIGHT_VERBS = frozenset({
    "mach", "mache", "macht", "schalt", "schalte", "schaltet", "stell", "stelle", "stellt",
    "fahr", "fahre", "fahrt", "dreh", "drehe", "dreht", "setz", "setze", "setzt", "knips", "knipse",
})
_VERBS = _LIGHT_VERBS | frozenset({
    "oeffne", "oeffnet", "schliess", "schliesse", "schliesst", "starte", "startet", "stoppe",
    "stoppt", "beende", "beendet", "aktiviere", "aktiviert", "deaktiviere", "deaktiviert",
    "dimm", "dimme", "dimmt", "spiel", "spiele", "spielt", "pausiere", "pausiert", "sperr",
    "sperre", "entriegle", "verriegle", "erhoehe", "senke", "reduziere", "ziehe", "zieh",
    "lass", "lasse", "schick", "schicke", "sende", "zeig", "zeige",
})
# Imperative operation verbs (shared with ``coordination``).
IMPERATIVE_VERBS = _VERBS
_UNITS = frozenset({"prozent", "%", "grad", "°", "c", "stufe"})
_PREPOSITIONS = frozenset({"im", "in", "am", "an", "beim", "bei", "auf", "vom", "von", "aus", "zum", "zur", "nach"})
# Automations keep their own repair reading (``automation_lexicon``).
_TRIGGER_WORDS = frozenset({"wenn", "sobald", "falls", "sofern"})
_TOKEN_RE = re.compile(r"\d+(?:[,.]\d+)?|[\wäöüß]+|[%°]|[^\w\s]", re.I)


@dataclass(frozen=True)
class _Token:
    text: str
    key: str
    start: int
    end: int
    is_word: bool

    @property
    def canonical(self) -> str:
        return self.key


def _tokens(text: str) -> list[_Token]:
    return [
        _Token(match.group(0), normalize_for_compare(match.group(0)), match.start(), match.end(),
               bool(re.fullmatch(r"[\wäöüß]+|\d+(?:[,.]\d+)?", match.group(0), re.I)))
        for match in _TOKEN_RE.finditer(text)
    ]


# --------------------------------------------------------------- fields
@dataclass(frozen=True)
class _Field:
    kind: str  # target, feature, place, value, verb, particle, time, reference
    start: int  # char offsets in the segment text
    end: int
    text: str
    head: int | None = None  # char offset of the head noun (target)
    registry: EntitySnapshot | None = None
    place_label: str | None = None
    side: str | None = None
    registry_ids: tuple[str, ...] = ()
    genera: tuple[str, ...] = ()
    modifier: str | None = None
    place: object | None = None


class _Names:
    """Registry names and aliases as word tuples (built once per turn)."""

    def __init__(self, entities: Sequence[EntitySnapshot]) -> None:
        self.by_words: dict[tuple[str, ...], list[EntitySnapshot]] = {}
        for entity in entities:
            for name in (entity.friendly_name, *entity.aliases):
                words = tuple(normalize_for_compare(name).replace("-", " ").split())
                if words:
                    self.by_words.setdefault(words, []).append(entity)
        self.max_words = max((len(words) for words in self.by_words), default=1)


def _is_number(token: _Token) -> bool:
    return token.key.replace(",", "").replace(".", "").isdigit() or (
        token.is_word and german_number(token.key) is not None and token.key not in {"ein", "eine", "einen"}
    )


def _fields(text: str, entities: Sequence[EntitySnapshot], names: _Names, places) -> tuple[list[_Field], list[_Token]]:
    """Typed fields of one segment and the words no field explains."""
    tokens = _tokens(text)
    words = [index for index, token in enumerate(tokens) if token.is_word]
    keys = [tokens[index].key for index in words]
    used = [False] * len(words)
    fields: list[_Field] = []

    def span(first: int, last: int) -> tuple[int, int]:
        return tokens[words[first]].start, tokens[words[last]].end

    def take(first: int, last: int) -> None:
        for position in range(first, last + 1):
            used[position] = True

    def left_extend(position: int, allowed: frozenset[str] | set[str]) -> int:
        while position > 0 and not used[position - 1] and keys[position - 1] in allowed:
            position -= 1
        return position

    # registry names, longest first
    for size in range(min(names.max_words, len(keys)), 0, -1):
        for first in range(0, len(keys) - size + 1):
            if any(used[first:first + size]):
                continue
            found = names.by_words.get(tuple(keys[first:first + size]))
            if not found:
                continue
            start_word = left_extend(first, _ARTICLES)
            start, end = span(start_word, first + size - 1)
            fields.append(_Field(
                "target", start, end, text[start:end], head=tokens[words[first + size - 1]].start,
                registry=found[0] if len(found) == 1 else None,
                registry_ids=tuple(entity.entity_id for entity in found),
            ))
            take(start_word, first + size - 1)
    # places
    for mention in places.scan(keys):
        if any(used[mention.token_start:mention.token_end]):
            continue
        first = mention.token_start
        while first > 0 and not used[first - 1] and keys[first - 1] in _ARTICLES | {"der", "dem"}:
            first -= 1
        if first > 0 and not used[first - 1] and keys[first - 1] in _PREPOSITIONS:
            first -= 1
        start, end = span(first, mention.token_end - 1)
        fields.append(_Field(
            "place", start, end, text[start:end], place_label=mention.place.label, place=mention.place,
        ))
        take(first, mention.token_end - 1)
    # time
    for item in analyse_temporal_semantics(tuple(tokens)):
        if item.kind is TemporalKind.NOW:
            continue
        first_word = next((pos for pos, index in enumerate(words) if index >= item.token_start), None)
        last_word = max((pos for pos, index in enumerate(words) if index < item.token_end), default=None)
        if first_word is None or last_word is None or last_word < first_word or any(used[first_word:last_word + 1]):
            continue
        while last_word + 1 < len(keys) and keys[last_word + 1] in {"frueh", "abend", "mittag", "nacht", "morgen", "uhr"}:
            last_word += 1
        start, end = span(first_word, last_word)
        fields.append(_Field("time", start, end, text[start:end]))
        take(first_word, last_word)
    # genus nouns ("das Licht", "den linken Rollladen")
    for position, key in enumerate(keys):
        if used[position] or key in _PARTICLES or key in _SIDE or len(key) < 3:
            continue
        analysis = analyse_word(key)
        if analysis is None or not analysis.genera:
            continue
        first = left_extend(position, _ARTICLES | set(_SIDE))
        first = left_extend(first, _ARTICLES)
        start, end = span(first, position)
        side = next((_SIDE[keys[item]] for item in range(first, position) if keys[item] in _SIDE), None)
        fields.append(_Field(
            "target", start, end, text[start:end], head=tokens[words[position]].start, side=side,
            genera=tuple(analysis.genera), modifier=analysis.modifier,
        ))
        take(first, position)
    # side/feature without noun ("den rechten", "links")
    for position, key in enumerate(keys):
        if used[position] or key not in _SIDE:
            continue
        first = left_extend(position, _ARTICLES)
        start, end = span(first, position)
        fields.append(_Field("feature", start, end, text[start:end], side=_SIDE[key]))
        take(first, position)
    # values ("auf 40 Prozent", "40%", "zwanzig Grad")
    position = 0
    while position < len(keys):
        if used[position] or not _is_number(tokens[words[position]]):
            position += 1
            continue
        first = (
            position - 1
            if position > 0 and keys[position - 1] in {"auf", "in", "um"} and not used[position - 1]
            else position
        )
        last = position
        end_char = tokens[words[position]].end
        following = words[position] + 1
        while following < len(tokens) and tokens[following].key in _UNITS:
            end_char = tokens[following].end
            if tokens[following].is_word:
                last = words.index(following)
            following += 1
        start = tokens[words[first]].start
        fields.append(_Field("value", start, end_char, text[start:end_char]))
        take(first, last)
        position = last + 1
    # operation: leading verb, final particle
    if keys and not used[0] and keys[0] in _VERBS:
        start, end = span(0, 0)
        fields.append(_Field("verb", start, end, text[start:end]))
        used[0] = True
    last = len(keys) - 1
    if keys and not used[last] and keys[last] in _PARTICLES:
        start, end = span(last, last)
        fields.append(_Field("particle", start, end, text[start:end]))
        used[last] = True
    # references ("das im Flur", "den")
    for position, key in enumerate(keys):
        if not used[position] and key in _REFERENCE:
            start, end = span(position, position)
            fields.append(_Field("reference", start, end, text[start:end]))
            used[position] = True
    residue = [
        tokens[words[position]] for position, key in enumerate(keys)
        if not used[position] and key not in _FILLER and key not in _ARTICLES
        and key not in {"und", "wieder", "auch", "nur", "noch", "lieber", "eher", "natuerlich", "genau", "so"}
    ]
    return sorted(fields, key=lambda item: item.start), residue


def utterance_fields(text: str, entities: Sequence[EntitySnapshot]) -> tuple[list[_Field], list[_Token]]:
    """Typed fields (target, side, place, value, operation, time) of a turn.

    Shared by the self correction and the ellipsis contract (7.7.1 A3): both
    ask which fields an utterance names itself.
    """
    from .place_model import build_place_lexicon

    entity_list = list(entities)
    return _fields(text, entity_list, _Names(entity_list), build_place_lexicon(entity_list))


# --------------------------------------------------------------- markers
@dataclass(frozen=True)
class _Marker:
    words: tuple[str, ...]
    char_start: int
    char_end: int
    strong: bool


def _find_markers(tokens: list[_Token]) -> list[_Marker]:
    markers: list[_Marker] = []
    words = [index for index, token in enumerate(tokens) if token.is_word]
    position = 0
    while position < len(words):
        index = words[position]
        found: tuple[tuple[str, ...], bool] | None = None
        for marker, strong in [(item, True) for item in _STRONG_MARKERS] + [(item, False) for item in _WEAK_MARKERS]:
            size = len(marker)
            keys = tuple(tokens[item].key for item in words[position:position + size])
            if keys == marker and (found is None or size > len(found[0])):
                found = (marker, strong)
        if found is None:
            position += 1
            continue
        marker, strong = found
        # A pause, or the end of a complete clause ("Schalte A aus halt B"
        # from speech recognition): the operation particle closes the part.
        paused = index > 0 and (
            tokens[index - 1].key in _PAUSE
            or (tokens[index - 1].key in _PARTICLES and position + len(marker) < len(words))
        )
        after_last = words[position + len(marker) - 1] + 1
        paused_after = after_last < len(tokens) and tokens[after_last].key in _PAUSE
        if position > 0 and (strong or paused or (_is_hesitation(marker) and paused_after)):
            markers.append(_Marker(
                marker, tokens[index].start, tokens[words[position + len(marker) - 1]].end,
                strong or paused,
            ))
        position += len(marker)
    return markers


_TRAILING_MARKERS = (("meine", "ich"), ("meinte", "ich"), ("mein", "ich"), ("sorry",), ("natuerlich",))


def _clean(segment: str) -> str:
    """Strip pauses and a trailing inverted marker ("…, das Gartentor meine ich")."""
    segment = segment.strip(" ,.;:!?-–—…").strip()
    words = segment.split()
    for marker in _TRAILING_MARKERS:
        tail = [normalize_for_compare(word.strip(",.!?")) for word in words[-len(marker):]]
        if len(words) > len(marker) and tuple(tail) == marker:
            segment = " ".join(words[: -len(marker)]).strip(" ,.;:!?-–—…")
            break
    return segment


def _is_hesitation(marker: tuple[str, ...]) -> bool:
    """"äh", "also", "ja" alone hesitate; "ach nee" retracts."""
    return len(marker) == 1 and marker[0] in _HESITATION


def _is_abort(segment: str, marker: tuple[str, ...]) -> bool:
    keys = [token.key for token in _tokens(segment) if token.is_word]
    if not keys:
        return not _is_hesitation(marker)
    return all(key in _ABORT_WORDS for key in keys) and bool(set(keys) & _ABORT_KEYS)


def _has_content(fields: list[_Field]) -> bool:
    return any(item.kind not in {"verb", "reference"} for item in fields)


def _inflect_like(new: str, old: str) -> str:
    """"linke" in place of "rechten" -> "linken" (same adjective ending)."""
    if normalize_for_compare(new) in {"links", "rechts", "vorne", "hinten"}:
        return new
    ending = re.search(r"e[nrsm]?$", old)
    stem = re.sub(r"e[nrsm]?$", "", new)
    return f"{stem}{ending.group(0)}" if ending else new


def _head_noun(field: _Field, text: str) -> str | None:
    """"Küchenlicht" -> "Licht": the genus head of a registry name."""
    name_words = text[field.start:field.end].split()
    last = name_words[-1] if name_words else ""
    analysis = analyse_word(last)
    if analysis is None or not analysis.head:
        return None
    head = analysis.head
    surface = last[-len(head):] if normalize_for_compare(last).endswith(head) else head
    return surface[:1].upper() + surface[1:]


def _merge(base: str, replacement: str, entities, names, places) -> str | None:
    """Apply the fields of ``replacement`` to ``base``; None if not clear."""
    base_fields, _base_rest = _fields(base, entities, names, places)
    new_fields, rest = _fields(replacement, entities, names, places)
    if rest:
        return None
    new_kinds = {item.kind for item in new_fields}
    if not new_kinds - {"reference"}:
        return None
    edits: list[tuple[int, int, str]] = []  # (start, end, text) in base
    appended: list[str] = []
    prefix: list[str] = []

    def of(kind: str, fields: list[_Field]) -> list[_Field]:
        return [item for item in fields if item.kind == kind]

    base_targets = of("target", base_fields)
    new_targets = of("target", new_fields)
    target_insert = ""
    if new_targets:
        if len(new_targets) != 1 or len(base_targets) > 1:
            return None
        if base_targets:
            edits.append((base_targets[0].start, base_targets[0].end, new_targets[0].text))
        else:
            target_insert = new_targets[0].text
        # A registry name carries its own place and side.
        if new_targets[0].registry is not None:
            for item in of("place", base_fields) + of("feature", base_fields):
                if "place" not in new_kinds:
                    edits.append((item.start, item.end, ""))
        for item in of("feature", base_fields):
            if "feature" not in new_kinds and new_targets[0].registry is None:
                pass  # "den linken ..., nein, den Rollladen": keep the side
    if "feature" in new_kinds:
        feature = of("feature", new_fields)[0]
        base_features = of("feature", base_fields)
        if base_features:
            edits.append((base_features[0].start, base_features[0].end, feature.text))
        elif len(base_targets) == 1 and not new_targets:
            target = base_targets[0]
            side_word = next(
                (word for word in re.findall(r"[\wäöüß]+", base[target.start:target.end])
                 if normalize_for_compare(word) in _SIDE),
                None,
            )
            spoken = re.findall(r"[\wäöüß]+", feature.text)[-1]
            if side_word is not None:
                start = base.index(side_word, target.start)
                if target.registry is not None and normalize_for_compare(side_word) in {"links", "rechts"}:
                    swapped = _SIDE_SWAP.get(feature.side or "")
                    spoken = swapped[0] if swapped else spoken
                else:
                    spoken = _inflect_like(spoken, side_word)
                edits.append((start, start + len(side_word), spoken))
            elif target.registry is None and target.head is not None:
                edits.append((target.head, target.head, f"{spoken} "))
            else:
                return None
        else:
            return None
    if "place" in new_kinds:
        place = of("place", new_fields)[0]
        label = place.place_label or place.text
        base_places = of("place", base_fields)
        if base_places:
            edits.append((base_places[0].start, base_places[0].end, label))
        elif len(base_targets) == 1 and not new_targets:
            target = base_targets[0]
            if target.registry is not None:
                head = _head_noun(target, base)
                if head is None:
                    return None
                article = re.match(r"\s*((?:der|die|das|den|dem)\s+)", base[target.start:target.end], re.I)
                edits.append((target.start, target.end, f"{article.group(1) if article else ''}{head} {label}"))
            else:
                edits.append((target.end, target.end, f" {label}"))
        elif new_targets and not base_targets:
            target_insert = f"{target_insert} {label}".strip()
        elif new_targets:
            appended_place = f" {label}"
            edits.append((base_targets[0].end, base_targets[0].end, appended_place))
        else:
            return None
    if "value" in new_kinds:
        value = of("value", new_fields)[0]
        base_values = of("value", base_fields)
        spoken = value.text
        if base_values:
            old = base_values[0].text
            if not re.search(r"(?i)prozent|%|grad|°", spoken):
                unit = re.search(r"(?i)\s*(?:prozent|%|grad|°\s*c?)\s*$", old)
                spoken = f"{spoken}{unit.group(0) if unit else ''}"
            if old.casefold().startswith("auf") and not spoken.casefold().startswith("auf"):
                spoken = f"auf {spoken}"
            edits.append((base_values[0].start, base_values[0].end, spoken))
        else:
            base_times = of("time", base_fields)
            number = re.fullmatch(r"(?:auf\s+|in\s+|um\s+)?(\S+)", spoken, re.I)
            if base_times and number and "time" not in new_kinds:
                time = base_times[0]
                old_number = next(
                    (token for token in _tokens(time.text) if _is_number(token)), None,
                )
                if old_number is None:
                    return None
                start = time.start + old_number.start
                edits.append((start, time.start + old_number.end, number.group(1)))
            else:
                if not spoken.casefold().startswith("auf"):
                    spoken = f"auf {spoken}"
                appended.append(spoken)
    if "time" in new_kinds:
        time = of("time", new_fields)[0]
        base_times = of("time", base_fields)
        if base_times:
            edits.append((base_times[0].start, base_times[0].end, time.text))
        else:
            prefix.append(time.text)
    new_verb = of("verb", new_fields)
    new_particle = of("particle", new_fields)
    base_verb = of("verb", base_fields)
    base_particle = of("particle", base_fields)
    if new_verb:
        if base_verb:
            edits.append((base_verb[0].start, base_verb[0].end, new_verb[0].text))
        else:
            prefix.insert(0, new_verb[0].text)
        if not new_particle and normalize_for_compare(new_verb[0].text) not in _LIGHT_VERBS:
            for item in base_particle:
                edits.append((item.start, item.end, ""))
    if new_particle:
        if base_particle:
            edits.append((base_particle[0].start, base_particle[0].end, new_particle[0].text))
        else:
            appended.append(new_particle[0].text)
    if target_insert:
        if base_verb:
            edits.append((base_verb[0].end, base_verb[0].end, f" {target_insert}"))
        else:
            prefix.append(target_insert)
    merged = base
    for start, end, text in sorted(edits, key=lambda item: (item[0], item[1]), reverse=True):
        merged = merged[:start] + text + merged[end:]
    merged = merged.rstrip(" .!,")
    if appended:
        particle = base_particle[0].text if base_particle and not new_particle else None
        if particle and merged.endswith(particle):
            merged = f"{merged[: -len(particle)].rstrip()} {' '.join(appended)} {particle}"
        else:
            merged = f"{merged} {' '.join(appended)}"
    if prefix:
        merged = f"{' '.join(prefix)} {merged}"
    return re.sub(r"\s+", " ", merged).strip()


# --------------------------------------------------------------- analysis
def analyse_self_correction(text: str, entities: Iterable[EntitySnapshot]) -> SelfCorrection:
    """Detect "retraction + replacement" and return the effective surface."""
    tokens = _tokens(text)
    markers = _find_markers(tokens)
    if not markers or any(token.key in _TRIGGER_WORDS for token in tokens):
        return SelfCorrection(CorrectionKind.NONE, text)
    from .place_model import build_place_lexicon

    entity_list = list(entities)
    names = _Names(entity_list)
    places = build_place_lexicon(entity_list)
    base = _clean(text[: markers[0].char_start])
    base_fields, _rest = _fields(base, entity_list, names, places)
    if not base or not _has_content(base_fields):
        # Nothing to retract: a hesitation ("Mach äh die Stehlampe an").
        if all(_is_hesitation(marker.words) for marker in markers):
            stripped = text
            for marker in reversed(markers):
                stripped = stripped[: marker.char_start] + stripped[marker.char_end:]
            stripped = re.sub(r"\s*,\s*,", ",", re.sub(r"\s+", " ", stripped)).strip(" ,")
            stripped = re.sub(r"^\s*,\s*|\s+,(?=\s|$)", " ", stripped).strip()
            return SelfCorrection(CorrectionKind.REPLACED, stripped, markers=tuple(" ".join(m.words) for m in markers))
        return SelfCorrection(CorrectionKind.NONE, text)
    current = base
    trailing_text = text[len(text.rstrip(" .!?")):]
    marker_words = tuple(" ".join(marker.words) for marker in markers)
    retracted = base
    for number, marker in enumerate(markers):
        end = markers[number + 1].char_start if number + 1 < len(markers) else len(text)
        segment = _clean(text[marker.char_end:end])
        if _is_abort(segment, marker.words):
            if number + 1 == len(markers):
                return SelfCorrection(
                    CorrectionKind.CANCELLED, text, retracted=current, replacement=segment,
                    markers=marker_words,
                )
            continue
        if not segment:
            continue  # hesitation at the end ("…, äh.")
        if _is_hesitation(marker.words) and all(
            token.key in _FILLER for token in _tokens(segment) if token.is_word
        ):
            continue  # "…, äh, bitte": a filler, nothing replaced
        if marker.words in {(word,) for word in _OPEN_ALTERNATIVE}:
            merged = _merge(current, segment, entity_list, names, places)
            return SelfCorrection(
                CorrectionKind.AMBIGUOUS, text, retracted=current, replacement=segment,
                markers=marker_words, readings=(current, merged or segment),
            )
        merged = _merge(current, segment, entity_list, names, places)
        if merged is None:
            return SelfCorrection(
                CorrectionKind.AMBIGUOUS, text, retracted=current, replacement=segment,
                markers=marker_words, readings=(current, segment),
            )
        retracted = current
        current = merged
    if current == base and not all(_is_hesitation(marker.words) for marker in markers):
        return SelfCorrection(CorrectionKind.NONE, text)
    surface = current + (trailing_text.strip() if trailing_text.strip() in {"?", "!", "."} else ".")
    return SelfCorrection(
        CorrectionKind.REPLACED, surface[:1].upper() + surface[1:], retracted=retracted,
        replacement=current, markers=marker_words,
    )


def render_correction(correction: SelfCorrection) -> str:
    """Answer for a cancelled or unclear correction; nothing was executed."""
    if correction.kind is CorrectionKind.CANCELLED:
        return "In Ordnung, ich mache nichts."
    first, second = (correction.readings + ("", ""))[:2]
    return (
        f"Meinst du „{first}“ oder „{second}“? "
        "Ich habe noch nichts ausgeführt. Sag bitte den ganzen Befehl noch einmal."
    )
