"""Politeness, thanks, reasons and urgency as frames (7.8 B2).

"Sei so lieb und …", "Hättest du die Güte, … anzuschalten", "Magst du …
runterfahren", "Wäre super, wenn du … einschaltest", "…, danke",
"…, ich schlafe gleich", "…, fix": these parts carry no target and no
operation. They are recognised as *frames* around the request, recorded,
and removed from the surface the command pipeline reads. They never change
the target set or the safety shape:

* a polite conditional ("wenn du so nett wärst", "wenn's geht") is never an
  automation trigger - a real condition ("wenn das Fenster aufgeht") has a
  subject of its own and stays a condition,
* an embedded question ("Kannst du mir sagen, ob …") stays a question,
* a reason or comment is only removed when it names nothing of the house
  (no device, no place, no value, no time, no operation).

The rules are over word classes and a small lexicon; no sentence template.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from ..entities import EntitySnapshot, normalize_for_compare
from .language_frontend import LanguageToken, tokenize_language

__all__ = ("PragmaticFrames", "strip_pragmatic_frames")


@dataclass(frozen=True)
class PragmaticFrames:
    text: str
    politeness: tuple[str, ...] = ()
    thanks: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    urgency: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return bool(self.politeness or self.thanks or self.reasons or self.urgency)


_THANKS = frozenset({"danke", "dankeschoen", "merci", "thx", "dank"})
_THANKS_TAIL = frozenset({"dir", "euch", "schoen", "sehr", "vielmals", "vielen", "herzlichen", "lieben"})
_URGENCY = frozenset({
    "schnell", "fix", "zack", "zackig", "zuegig", "flott", "hurtig", "sofort", "gleich",
    "jetzt", "dalli", "bitte", "aber", "mal", "ruckzuck", "subito", "hopp", "los",
})
_URGENCY_CORE = frozenset({"schnell", "fix", "zack", "zackig", "zuegig", "flott", "hurtig", "sofort", "dalli", "ruckzuck", "subito", "hopp"})
_KIND_ADJECTIVES = frozenset({
    "gut", "lieb", "nett", "freundlich", "suess", "so", "bitte", "doch", "mal", "einmal",
})
_PRAISE = frozenset({
    "super", "klasse", "toll", "nett", "schoen", "lieb", "prima", "cool", "genial", "spitze",
    "grossartig", "praktisch", "hilfreich", "fein", "wunderbar", "top",
})
_PLEASE_FRAMES = (
    # "wenn du so nett/lieb/freundlich wärst", "wenn's geht", "falls möglich"
    ("wenn", "du", "so", "*", "waerst"), ("wenn", "du", "so", "*", "bist"),
    ("wenn", "ihr", "so", "*", "waert"), ("wenn", "s", "geht"), ("wenn", "es", "geht"),
    ("wenn", "moeglich"), ("falls", "moeglich"), ("wenn", "du", "kannst"),
    ("wenn", "du", "magst"), ("wenn", "du", "willst"), ("falls", "du", "kannst"),
    ("wenn", "es", "dir", "nichts", "ausmacht"), ("wenn", "s", "dir", "nichts", "ausmacht"),
)
_ZU_PARTICLES = ("an", "aus", "ein", "auf", "zu", "hoch", "runter", "herunter", "rauf", "raus", "rein", "los", "ab")
_IMPERATIVE = {
    "machen": "mach", "schalten": "schalte", "fahren": "fahr", "drehen": "dreh", "stellen": "stell",
    "setzen": "setz", "knipsen": "knips", "oeffnen": "öffne", "schliessen": "schließe",
    "starten": "starte", "stoppen": "stoppe", "dimmen": "dimm", "aktivieren": "aktiviere",
    "deaktivieren": "deaktiviere", "sperren": "sperr", "spielen": "spiel", "schicken": "schick",
    "lassen": "lass", "ziehen": "zieh", "senken": "senke", "erhoehen": "erhöhe",
    "pausieren": "pausiere", "beenden": "beende",
}
_SECOND_PERSON = {
    "machst": "mach", "schaltest": "schalte", "faehrst": "fahr", "drehst": "dreh", "stellst": "stell",
    "setzt": "setz", "oeffnest": "öffne", "schliesst": "schließe", "startest": "starte",
    "stoppst": "stoppe", "dimmst": "dimm", "aktivierst": "aktiviere", "deaktivierst": "deaktiviere",
    "sperrst": "sperr", "spielst": "spiel", "schickst": "schick", "ziehst": "zieh",
    "knipst": "knips", "senkst": "senke", "beendest": "beende",
}


def _key(token: LanguageToken) -> str:
    return token.canonical


def _words(tokens: Sequence[LanguageToken]) -> list[int]:
    return [index for index, token in enumerate(tokens) if token.is_word or token.is_number]


def _verb_phrase(keys: Sequence[str], surface: Sequence[str]) -> tuple[str, str] | None:
    """(imperative, particle) from "anzuschalten", "runterfahren", "einschaltest"."""
    if not keys:
        return None
    last = keys[-1]
    for particle in sorted(_ZU_PARTICLES, key=len, reverse=True):
        if last.startswith(particle + "zu") and last[len(particle) + 2:] in _IMPERATIVE:
            return _IMPERATIVE[last[len(particle) + 2:]], particle
        if last.startswith(particle) and last[len(particle):] in _IMPERATIVE:
            return _IMPERATIVE[last[len(particle):]], particle
        if last.startswith(particle) and last[len(particle):] in _SECOND_PERSON:
            return _SECOND_PERSON[last[len(particle):]], particle
    if last in _IMPERATIVE:
        if len(keys) >= 2 and keys[-2] == "zu":
            return _IMPERATIVE[last], ""
        return _IMPERATIVE[last], ""
    if last in _SECOND_PERSON:
        return _SECOND_PERSON[last], ""
    return None


def _rebuild(object_words: Sequence[str], verb: tuple[str, str]) -> str:
    imperative, particle = verb
    body = " ".join(object_words).strip(" ,")
    return f"{imperative[:1].upper()}{imperative[1:]} {body} {particle}".strip()


def _match_frame(keys: Sequence[str], frame: Sequence[str]) -> bool:
    return len(keys) >= len(frame) and all(
        want == "*" or have == want for have, want in zip(keys, frame)
    )


def _polite_shell(tokens: Sequence[LanguageToken], text: str) -> tuple[str, str] | None:
    """(rest of the request, removed frame) or ``None``."""
    words = _words(tokens)
    keys = [_key(tokens[index]) for index in words]
    surface = [tokens[index].text for index in words]
    if not keys:
        return None

    def after(position: int) -> str:
        if position >= len(words):
            return ""
        return text[tokens[words[position]].start:].strip()

    # "Sei (bitte) so gut/lieb/nett und …", "Seid so lieb und …"
    if keys[0] in {"sei", "seid"} and "und" in keys[:7] and set(keys[1:keys.index("und")]) <= _KIND_ADJECTIVES:
        position = keys.index("und") + 1
        return after(position), text[: tokens[words[position - 1]].end]
    # "Wärst du so lieb und …"
    if keys[0] in {"waerst", "waeret", "waert"} and "und" in keys[:7]:
        position = keys.index("und") + 1
        rest_keys = keys[position:]
        verb = _verb_phrase(rest_keys, surface[position:])
        rest = after(position)
        if rest_keys and rest_keys[0] in _SECOND_PERSON:
            imperative = _SECOND_PERSON[rest_keys[0]]
            rest = f"{imperative} {' '.join(surface[position + 1:])}"
        elif verb is not None and rest_keys and rest_keys[-1] in _SECOND_PERSON:
            rest = _rebuild(surface[position:-1], verb)
        return rest, text[: tokens[words[position - 1]].end]
    # "Hättest du die Güte/Freundlichkeit, … anzumachen" /
    # "Würde es dir etwas ausmachen, … auszuschalten" /
    # "Wäre es möglich, … zu öffnen"
    comma = next((index for index, token in enumerate(tokens) if token.canonical == ","), None)
    head_keys = keys[: next((pos for pos, index in enumerate(words) if comma is not None and index > comma), len(keys))]
    shells = (
        ("haettest", "du"), ("haettet", "ihr"), ("wuerde", "es", "dir"), ("wuerde", "es", "euch"),
        ("waere", "es", "moeglich"), ("waere", "es", "nett"), ("macht", "es", "dir"),
        ("wuerdest", "du", "bitte"),
    )
    if comma is not None and any(_match_frame(head_keys, shell) for shell in shells):
        position = len(head_keys)
        rest_keys = keys[position:]
        verb = _verb_phrase(rest_keys, surface[position:])
        if verb is not None:
            object_words = surface[position:-1]
            if len(rest_keys) >= 2 and rest_keys[-2] == "zu" and verb[1] == "":
                object_words = surface[position:-2]
            return _rebuild(object_words, verb), text[: tokens[comma].end]
    # "Magst du … runterfahren", "Willst du mal … anmachen"
    if keys[0] in {"magst", "moechtest", "willst"} and len(keys) > 2 and keys[1] == "du":
        verb = _verb_phrase(keys[2:], surface[2:])
        if verb is not None:
            object_words = [word for word in surface[2:-1] if normalize_for_compare(word) not in {"mal", "bitte", "eben"}]
            return _rebuild(object_words, verb), " ".join(surface[:2])
    # "Wäre super, wenn du … einschaltest" / "Es wäre toll, wenn du …"
    start = 1 if keys[0] == "es" else 0
    if (
        len(keys) > start + 4 and keys[start] in {"waere", "ist"} and keys[start + 1] in _PRAISE
        and "wenn" in keys[start + 2:start + 5]
    ):
        position = keys.index("wenn", start) + 1
        if position < len(keys) and keys[position] in {"du", "ihr"}:
            verb = _verb_phrase(keys[position + 1:], surface[position + 1:])
            if verb is not None:
                return _rebuild(surface[position + 1:-1], verb), " ".join(surface[:position + 1])
    return None


def _leading_please(tokens: Sequence[LanguageToken], text: str) -> tuple[str, str] | None:
    words = _words(tokens)
    keys = [_key(tokens[index]) for index in words]
    for frame in _PLEASE_FRAMES:
        if _match_frame(keys, frame):
            position = len(frame)
            while position < len(keys) and keys[position] in {"bitte", "dann", "mal"}:
                position += 1
            if position >= len(keys):
                return None
            return text[tokens[words[position]].start:].strip(), " ".join(tokens[index].text for index in words[:len(frame)])
    return None


def _segments(tokens: Sequence[LanguageToken]) -> list[tuple[int, int]]:
    ranges, start = [], 0
    for index, token in enumerate(tokens):
        if token.canonical in {",", ";", "–", "-", "!", "."}:
            if index > start:
                ranges.append((start, index))
            start = index + 1
    if start < len(tokens):
        ranges.append((start, len(tokens)))
    return ranges


def _names_nothing(segment: str, entities: Sequence[EntitySnapshot]) -> bool:
    """A comment names no device, place, value, time or operation."""
    from .self_correction import utterance_fields

    fields, _rest = utterance_fields(segment, entities)
    for item in fields:
        if item.kind in {"reference", "verb"}:
            continue
        if item.kind == "place" and getattr(getattr(item, "place", None), "kind", None) is not None \
                and item.place.kind.name == "HERE":
            continue
        return False
    return True


def strip_pragmatic_frames(text: str, entities: Iterable[EntitySnapshot]) -> PragmaticFrames:
    """The request without its frames, plus the recorded frames."""
    entity_list = entities if isinstance(entities, list) else list(entities)
    politeness: list[str] = []
    thanks: list[str] = []
    reasons: list[str] = []
    urgency: list[str] = []
    for _round in range(3):
        tokens = tokenize_language(text)
        if not tokens:
            break
        shell = _polite_shell(tokens, text) or _leading_please(tokens, text)
        if shell is None:
            break
        text, frame = shell
        politeness.append(frame)
    # leading thanks ("Danke dir, …")
    tokens = tokenize_language(text)
    words = _words(tokens)
    keys = [_key(tokens[index]) for index in words]
    position = 0
    while position < len(keys) and (keys[position] in _THANKS or (position and keys[position] in _THANKS_TAIL)):
        position += 1
    if 0 < position < len(keys) and (keys[0] in _THANKS or keys[:2] == ["vielen", "dank"]):
        thanks.append(" ".join(tokens[index].text for index in words[:position]))
        text = text[tokens[words[position]].start:].strip()
    # trailing parts: thanks, urgency, reasons
    for _round in range(4):
        tokens = tokenize_language(text)
        ranges = _segments(tokens)
        if len(ranges) < 2:
            # "…, danke" without comma from speech recognition
            words = _words(tokens)
            keys = [_key(tokens[index]) for index in words]
            tail = len(keys)
            while tail > 1 and (keys[tail - 1] in _THANKS or keys[tail - 1] in _THANKS_TAIL):
                tail -= 1
            if tail < len(keys) and any(key in _THANKS for key in keys[tail:]) and tail >= 2:
                thanks.append(" ".join(tokens[index].text for index in words[tail:]))
                text = text[: tokens[words[tail - 1]].end]
                continue
            break
        start, end = ranges[-1]
        segment = text[tokens[start].start:tokens[end - 1].end]
        keys = [token.canonical for token in tokens[start:end] if token.is_word]
        head = text[: tokens[ranges[-2][1] - 1].end]
        if keys and all(key in _THANKS or key in _THANKS_TAIL for key in keys) and set(keys) & (_THANKS | {"vielen"}):
            thanks.append(segment)
        elif keys and all(key in _URGENCY for key in keys) and set(keys) & _URGENCY_CORE:
            urgency.append(segment)
        elif keys and (
            keys[0] in {"weil", "denn", "da", "wir", "ich", "es", "is", "der", "die", "das", "er", "sie", "man"}
            and len(keys) >= 2
        ) and _names_nothing(segment, entity_list) and not _is_question_or_condition(keys):
            reasons.append(segment)
        else:
            break
        text = head
    return PragmaticFrames(
        text=text.strip(), politeness=tuple(politeness), thanks=tuple(thanks),
        reasons=tuple(reasons), urgency=tuple(urgency),
    )


def _is_question_or_condition(keys: Sequence[str]) -> bool:
    """A comment that asks, conditions, negates or is modal is kept: it may
    change what the sentence means."""
    return bool(set(keys) & {
        "wenn", "sobald", "falls", "ob", "wann", "wie", "was", "welche", "nicht", "kein",
        "keine", "keinen", "nie", "niemals", "haette", "waere", "wuerde", "soll", "sollte",
        "koennte", "lass", "lieber", "sondern", "aber", "doch", "stattdessen",
    })
