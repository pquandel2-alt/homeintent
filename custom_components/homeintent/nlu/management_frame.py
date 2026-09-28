"""Language island "Automationsverwaltung" (7.5.2): a frame table, not sentences.

Every management request is one row of ``FRAMES``: the cue lexemes that must
be spoken (in any order), the object words, and where the automation's name
begins and ends (the words left and right of the name slot). Parameters
(clock time, count, state, day) come from small value readers. The result is
the historic canonical frame ``AutomationManagementRequest``; resolving the
name to automations stays in ``automation_management``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..entities import normalize_for_compare

_COUNT_WORDS = {"zwei": 2, "drei": 3, "vier": 4, "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10}
_PREPOSITIONS = frozenset({"von", "fuer", "fur", "zu"})
_ARTICLES = frozenset({"die", "den", "der", "das"})


@dataclass(frozen=True)
class Token:
    surface: str
    key: str


def tokens_of(text: str) -> list[Token]:
    source = re.sub(r"[?.!,]", " ", text)
    return [Token(word, normalize_for_compare(word)) for word in source.split()]


def _is(token: Token, lexemes: frozenset[str] | set[str], stems: tuple[str, ...] = ()) -> bool:
    return token.key in lexemes or any(token.key.startswith(stem) for stem in stems)


def _find(tokens: Sequence[Token], lexemes: frozenset[str] | set[str], stems: tuple[str, ...] = (), start: int = 0) -> int | None:
    return next((index for index in range(start, len(tokens)) if _is(tokens[index], lexemes, stems)), None)


def _span(tokens: Sequence[Token], start: int, end: int) -> str | None:
    words = [token.surface for token in tokens[start:end]]
    return " ".join(words) if words else None


def _clock(tokens: Sequence[Token], start: int) -> tuple[int, int] | None:
    """"22 Uhr", "22:30", "7" after ``start``."""
    for token in tokens[start:]:
        match = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?", token.key)
        if match:
            return int(match.group(1)), int(match.group(2) or 0)
    return None


def _run_count(tokens: Sequence[Token], start: int) -> int | None:
    """"dreimal", "drei mal", "3 mal" after ``start``."""
    for index in range(start, len(tokens)):
        key = tokens[index].key
        joined = key[:-3] if key.endswith("mal") and key != "mal" else None
        spoken = joined if joined is not None else (
            key if index + 1 < len(tokens) and tokens[index + 1].key == "mal" else None
        )
        if spoken is None:
            continue
        if spoken.isdigit() and 2 <= int(spoken) <= 10:
            return int(spoken)
        if spoken in _COUNT_WORDS:
            return _COUNT_WORDS[spoken]
    return None


@dataclass(frozen=True)
class Frame:
    kind: str
    parse: Callable[[list[Token]], dict[str, object] | None]
    read_only: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


def _object(tokens: Sequence[Token], start: int, objects: frozenset[str]) -> int | None:
    """The object word right after ``start`` (an article may stand between)."""
    index = start
    if index < len(tokens) and tokens[index].key in _ARTICLES:
        index += 1
    return index if index < len(tokens) and tokens[index].key in objects else None


def _name_after_object(tokens: Sequence[Token], index: int) -> int:
    """Skip an optional preposition after the object word."""
    return index + 1 if index < len(tokens) and tokens[index].key in _PREPOSITIONS else index


def _simulate(tokens):
    cue = _find(tokens, set(), ("simulier",))
    if cue is None:
        was = _find(tokens, {"was"})
        cue = was + 1 if was is not None and was + 1 < len(tokens) and tokens[was + 1].key == "wuerde" else None
    if cue is None:
        return None
    obj = _object(tokens, cue + 1, frozenset({"automation"}))
    if obj is None:
        return None
    start = _name_after_object(tokens, obj + 1)
    end = len(tokens)
    if end - start >= 2 and [token.key for token in tokens[end - 2:end]] == ["jetzt", "tun"]:
        end -= 2
    name = _span(tokens, start, end)
    return {"entity_name": name} if name else None


def _duplicate(tokens):
    cue = _find(tokens, set(), ("duplizier", "kopier"))
    obj = _object(tokens, cue + 1, frozenset({"automation"})) if cue is not None else None
    if obj is None:
        return None
    name = _span(tokens, _name_after_object(tokens, obj + 1), len(tokens))
    return {"entity_name": name} if name else None


def _diagnose(tokens):
    warum = _find(tokens, {"warum"})
    if warum is None or warum + 1 >= len(tokens) or tokens[warum + 1].key != "wurde":
        return None
    obj = _object(tokens, warum + 2, frozenset({"automation"}))
    if obj is None:
        return None
    start = _name_after_object(tokens, obj + 1)
    nicht = _find(tokens, {"nicht"}, start=start + 1)
    if nicht is None or nicht + 1 >= len(tokens) or tokens[nicht + 1].key not in {"ausgeloest", "ausgefuehrt"}:
        return None
    name = _span(tokens, start, nicht)
    return {"entity_name": name} if name else None


def _pause(tokens):
    cue = _find(tokens, set(), ("pausier",))
    obj = _object(tokens, cue + 1, frozenset({"automation"})) if cue is not None else None
    if obj is None:
        return None
    bis = _find(tokens, {"bis"}, start=obj + 1)
    if bis is None:
        return None
    name = None
    if obj + 1 < bis and tokens[obj + 1].key in _PREPOSITIONS:
        name = _span(tokens, obj + 2, bis)
    elif obj + 1 != bis:
        return None
    index = bis + 1
    day = 0
    if index < len(tokens) and tokens[index].key in {"morgen", "uebermorgen"}:
        day = 2 if tokens[index].key == "uebermorgen" else 1
        index += 1
    if index >= len(tokens) or not re.fullmatch(r"\d{1,2}(?::\d{1,2})?", tokens[index].key):
        return None
    hour, minute = _clock(tokens, index) or (0, 0)
    if hour > 23 or minute > 59:
        return {"invalid": True}
    return {"entity_name": name, "hour": hour, "minute": minute, "day_offset": day}


def _count(tokens):
    keys = [token.key for token in tokens]
    for index in range(len(keys) - 1):
        if keys[index] == "wie" and keys[index + 1] == "viele":
            rest = keys[index + 2:]
            if rest[:1] == ["homeintent-automationen"]:
                rest = rest[1:]
            elif rest[:2] == ["homeintent", "automationen"]:
                rest = rest[2:]
            else:
                return None
            if len(rest) >= 2 and rest[0] == "sind" and rest[1] in {"aktiv", "eingeschaltet", "deaktiviert", "ausgeschaltet"}:
                return {"state": "active" if rest[1] in {"aktiv", "eingeschaltet"} else "disabled"}
    return None


def _clean_expired(tokens):
    cue = _find(tokens, set(), ("loesch",))
    if cue is None or cue + 2 >= len(tokens):
        return None
    return {} if tokens[cue + 1].key == "alle" and tokens[cue + 2].key == "abgelaufenen" else None


def _rollback(tokens):
    verb = _find(tokens, set(), ("mach", "nimm", "setz"))
    last = _find(tokens, {"letzte", "letzten"}, start=(verb or 0) + 1) if verb is not None else None
    change = _find(tokens, set(), ("homeintent-automationaenderung", "homeintent-automationsaenderung", "automationaenderung", "automationsaenderung"), start=(last or 0) + 1) if last is not None else None
    back = _find(tokens, {"rueckgaengig", "zurueck"}, start=(change or 0) + 1) if change is not None else None
    return {} if back is not None else None


def _set_max_runs(tokens):
    cue = _find(tokens, set(), ("wiederhol",))
    obj = _object(tokens, cue + 1, frozenset({"automation"})) if cue is not None else None
    if obj is None:
        return None
    nur = _find(tokens, {"nur"}, start=obj + 1)
    if nur is None:
        return None
    count = _run_count(tokens, nur + 1)
    if count is None or not (nur + 1 < len(tokens) and (tokens[nur + 1].key.endswith("mal") or (nur + 2 < len(tokens) and tokens[nur + 2].key == "mal"))):
        return None
    name = None
    if obj + 1 < nur:
        if tokens[obj + 1].key not in {"fuer", "fur"}:
            return None
        name = _span(tokens, obj + 2, nur)
    return {"entity_name": name, "max_runs": count}


def _reschedule(tokens):
    cue = _find(tokens, set(), ("verschieb",))
    if cue is None or cue + 2 >= len(tokens) or tokens[cue + 1].key not in {"den", "die"}:
        return None
    if tokens[cue + 2].key not in {"auftrag", "automation"}:
        return None
    index = cue + 3
    name_start = None
    if index < len(tokens) and tokens[index].key in {"fuer", "fur"}:
        name_start = index + 1
    auf = _find(tokens, {"auf"}, start=(name_start or index) + (1 if name_start else 0))
    if auf is None or name_start is None and auf != index:
        return None
    if auf + 1 >= len(tokens) or not re.fullmatch(r"\d{1,2}(?::\d{1,2})?", tokens[auf + 1].key):
        return None
    if auf + 2 >= len(tokens) or tokens[auf + 2].key != "uhr":
        return None
    hour, minute = _clock(tokens, auf + 1) or (0, 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return {"invalid": True}
    return {"entity_name": _span(tokens, name_start, auf) if name_start else None, "hour": hour, "minute": minute}


_WHEN_PARTICIPLES = frozenset({"gefahren", "geschaltet", "eingeschaltet", "ausgeschaltet", "gestartet", "ausgefuehrt"})


def _when(tokens):
    wann = _find(tokens, {"wann"})
    if wann is None or wann + 1 >= len(tokens) or tokens[wann + 1].key != "wird":
        return None
    end = _find(tokens, _WHEN_PARTICIPLES, start=wann + 3)
    name = _span(tokens, wann + 2, end) if end is not None else None
    return {"entity_name": name} if name else None


def _controls(tokens):
    for index, token in enumerate(tokens):
        if token.key in {"steuert", "schaltet"}:
            before = [item.key for item in tokens[max(0, index - 2):index]]
            welche = before == ["welche", "automation"]
            was = token.key == "steuert" and before[-1:] == ["was"]
            if welche or was:
                name = _span(tokens, index + 1, len(tokens))
                return {"entity_name": name} if name else None
    return None


def _detail(tokens):
    cue = _find(tokens, set(), ("zeig", "erklaer"))
    if cue is None:
        return None
    index = cue + 1
    if index < len(tokens) and tokens[index].key == "mir":
        index += 1
    if index < len(tokens) and tokens[index].key == "die":
        index += 1
    if index < len(tokens) and tokens[index].key == "details":
        index += 1
        if index + 1 < len(tokens) and tokens[index].key == "der" and tokens[index + 1].key == "automation":
            index += 2
    elif index < len(tokens) and tokens[index].key == "automation":
        index += 1
    else:
        return None
    if index >= len(tokens) or tokens[index].key not in _PREPOSITIONS:
        return None
    name = _span(tokens, index + 1, len(tokens))
    return {"entity_name": name} if name else None


_EXPLAIN_PARTICIPLES = frozenset({
    "geoeffnet", "geschlossen", "an", "aus", "erkannt", "ausgeloest", "gemeldet",
    "eingeschaltet", "ausgeschaltet", "aktiv",
})


def _explain(tokens):
    keys = [token.key for token in tokens]
    start = None
    for index in range(len(keys) - 1):
        if keys[index] == "was" and keys[index + 1] == "passiert":
            start = index + 2
            break
        if keys[index] == "welche" and keys[index + 1] in {"automation", "automationen"} and index + 2 < len(keys) and (
            keys[index + 2].startswith("reagier") or keys[index + 2].startswith("start")
        ):
            start = index + 3
            break
    if start is None:
        return None
    if start < len(keys) and keys[start] in {"wenn", "sobald"}:
        start += 1
    end = len(tokens)
    if end - start >= 3 and keys[end - 1] == "wird" and keys[end - 2] in _EXPLAIN_PARTICIPLES:
        end -= 2
    name = _span(tokens, start, end)
    return {"entity_name": name} if name else None


def _list_homeintent(tokens):
    keys = [token.key for token in tokens]
    listed = (
        any(key in {"welche", "zeige"} for key in keys)
        and ("homeintent-automationen" in keys or any(
            a == "homeintent" and b == "automationen" for a, b in zip(keys, keys[1:])
        ))
    )
    if not listed:
        return None
    scope = None
    for index, key in enumerate(keys):
        if key == "im":
            scope = _span(tokens, index + 1, len(tokens))
            break
        if key == "in" and index + 1 < len(keys) and keys[index + 1] in {"der", "den"}:
            scope = _span(tokens, index + 2, len(tokens))
            break
    return {"scope_name": scope}


def _list_scheduled(tokens):
    keys = [token.key for token in tokens]
    welche = _find(tokens, {"welche"})
    if welche is None:
        return None
    index = welche + 1
    if index < len(keys) and keys[index] == "einmaligen":
        index += 1
    if index >= len(keys) or keys[index] not in {"auftraege", "automationen"}:
        return None
    index += 1
    if index >= len(keys) or keys[index] != "sind":
        return None
    index += 1
    if index < len(keys) and keys[index] == "noch":
        index += 1
    return {} if index < len(keys) and keys[index] == "geplant" else None


# Order is precedence (a DETAIL request can mention "steuert" in the name).
FRAMES: tuple[Frame, ...] = (
    Frame("SIMULATE", _simulate, True),
    Frame("DUPLICATE", _duplicate),
    Frame("DIAGNOSE", _diagnose, True),
    Frame("PAUSE_UNTIL", _pause),
    Frame("COUNT", _count, True),
    Frame("CLEAN_EXPIRED", _clean_expired),
    Frame("ROLLBACK", _rollback),
    Frame("SET_MAX_RUNS", _set_max_runs),
    Frame("RESCHEDULE", _reschedule),
    Frame("WHEN", _when, True),
    Frame("CONTROLS_ENTITY", _controls, True),
    Frame("DETAIL", _detail, True),
    Frame("EXPLAIN_TRIGGER", _explain, True),
    Frame("LIST_HOMEINTENT", _list_homeintent, True),
    Frame("LIST_SCHEDULED", _list_scheduled, True),
)


def management_frame(text: str) -> tuple[str, dict[str, object]] | None:
    """(kind, slots) of the first frame whose cues are spoken, or ``None``."""
    tokens = tokens_of(text)
    for frame in FRAMES:
        slots = frame.parse(tokens)
        if slots is not None:
            return frame.kind, slots
    return None


__all__ = ("FRAMES", "Frame", "management_frame", "tokens_of")
