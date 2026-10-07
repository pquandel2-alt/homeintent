"""How long an automation action lasts (7.9.2 A2).

"… schalte die Bewässerung für 20 Minuten ein", "… öffne das Ventil
15 Minuten lang", "… bewässere den Garten 15 Minuten": a duration on an
action means *do it, wait, undo it* - one automation with the end as a
mandatory part. Only actions with a defined opposite carry a duration
(``inverse_of``); any other action with a spoken duration is not
understood rather than silently shortened to "do it" (Regel 4: a spoken
restriction is never dropped).

Constructions, not sentences:

* duration phrase: ``für`` + amount + unit, amount + unit + ``lang``, or a
  bare amount + unit at the end of the clause that no temporal preposition
  claims ("in/nach/alle/seit/vor/bis/innerhalb …" stay delays, intervals
  and windows);
* irrigation verb (``bewässern``, ``beregnen``, ``sprengen``, ``gießen``)
  with an optional place object: the act of opening the irrigation
  ("die Bewässerung") at that place. The verb lexicon is data.

Home-Assistant-free and deterministic.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Sequence

from .action_model import ActionGroup, ActionModel, ActionType
from .normalize import german_number

__all__ = (
    "ACTION_DURATION_MAX_SECONDS",
    "inverse_of",
    "irrigation_clause",
    "split_action_duration",
    "with_duration",
)

# A bounded watering/run: never longer than a day.
ACTION_DURATION_MAX_SECONDS = 86400

_UNIT_SECONDS = {"sekunde": 1, "minute": 60, "stunde": 3600}

# Prepositions that make "N Minuten" a delay, interval, window or age.
_TEMPORAL_LEAD = frozenset({
    "in", "nach", "alle", "seit", "vor", "bis", "innerhalb", "binnen", "als", "von", "um",
    "jede", "jeden", "jedes", "spätestens", "frühestens", "über", "unter", "mindestens",
})


def _seconds(amount: str, unit: str) -> int | None:
    key = " ".join(amount.casefold().split())
    base = _UNIT_SECONDS[unit]
    if key in {"eine halbe", "einer halben"}:
        value = 0.5
    elif key in {"anderthalb", "eineinhalb"}:
        value = 1.5
    elif key in {"ein", "eine", "einen", "einer"}:
        value = 1.0
    elif re.fullmatch(r"\d+(?:[.,]5)?", key):
        value = float(key.replace(",", "."))
    else:
        number = german_number(key)
        if number is None:
            return None
        value = float(number)
    seconds = int(value * base)
    return seconds if 0 < seconds <= ACTION_DURATION_MAX_SECONDS else None


def _unit_key(unit: str) -> str:
    key = unit.casefold()
    for stem in _UNIT_SECONDS:
        if key.startswith(stem):
            return stem
    return key


_HALF = {("eine", "halbe"): 0.5, ("einer", "halben"): 0.5}
_FILLERS = frozenset({"die", "nächsten", "naechsten"})


def _amount_at(words: list[str], index: int) -> tuple[str, int] | None:
    """(amount text, words used) of an amount starting at ``index``."""
    if index + 1 < len(words) and (words[index], words[index + 1]) in _HALF:
        return f"{words[index]} {words[index + 1]}", 2
    if index < len(words):
        return words[index], 1
    return None


def split_action_duration(text: str) -> tuple[int | None, str]:
    """The spoken duration of an action clause and the clause without it.

    Constructions over words: "für [die nächsten] <Menge> <Einheit> [lang]",
    "<Menge> <Einheit> lang", or a bare "<Menge> <Einheit>" closing the
    clause that no temporal preposition claims.
    """
    raw = text.split()
    words = [word.casefold().strip(",.!?") for word in raw]
    for start in range(len(words)):
        lead_for = words[start] in {"für", "fuer"}
        index = start + 1 if lead_for else start
        while lead_for and index < len(words) and words[index] in _FILLERS:
            index += 1
        found = _amount_at(words, index)
        if found is None:
            continue
        amount, used = found
        unit_index = index + used
        if unit_index >= len(words):
            continue
        unit = _unit_key(words[unit_index])
        if unit not in _UNIT_SECONDS:
            continue
        end = unit_index + 1
        long_form = end < len(words) and words[end] == "lang"
        if long_form:
            end += 1
        bare = not lead_for and not long_form
        if not lead_for and start > 0 and words[start - 1] in _TEMPORAL_LEAD:
            continue
        # A bare duration must close the clause after an action - an
        # irrigation verb is a whole action by itself ("Bewässere 20 Minuten").
        verb_alone = start == 1 and _IRRIGATION_VERB_RE.match(words[0]) is not None
        if bare and (end != len(words) or (start < 2 and not verb_alone)):
            continue
        seconds = _seconds(amount, unit)
        if seconds is None:
            continue
        rest = " ".join([*raw[:start], *raw[end:]]).strip(" ,")
        return seconds, rest
    return None, text


# Irrigation verbs (data): stem -> the act of opening the irrigation.
_IRRIGATION_VERB_RE = re.compile(
    r"^(?:bitte\s+)?(?:bewässer|bewaesser|beregn|spreng|gieß|giess)\w*\b"
    r"(?:\s+(?P<object>.*?))?\s*[.!]?$",
    re.IGNORECASE,
)
# Object nouns that name what is watered, not where: no place filter.
_WATERED_THINGS = frozenset({
    "rasen", "beete", "beet", "hochbeet", "hochbeete", "pflanzen", "blumen", "hecke", "gemüse",
})
_ARTICLES = frozenset({"den", "die", "das", "dem", "der", "im", "in", "bitte"})


def irrigation_clause(text: str) -> str | None:
    """"bewässere den Garten" -> "öffne die Bewässerung im Garten"."""
    match = _IRRIGATION_VERB_RE.match(text.strip())
    if match is None:
        return None
    words = [word for word in (match.group("object") or "").split() if word.casefold() not in _ARTICLES]
    if not words or all(word.casefold().strip(",") in _WATERED_THINGS for word in words):
        return "öffne die Bewässerung"
    return f"öffne die Bewässerung im {' '.join(words)}"


_INVERSE_SERVICES: dict[tuple[str, str], tuple[str, str]] = {
    ("valve", "open_valve"): ("valve", "close_valve"),
    ("valve", "set_valve_position"): ("valve", "close_valve"),
    ("media_player", "turn_on"): ("media_player", "turn_off"),
    ("media_player", "media_play"): ("media_player", "media_pause"),
    ("vacuum", "start"): ("vacuum", "return_to_base"),
    ("lawn_mower", "start_mowing"): ("lawn_mower", "dock"),
}


def inverse_of(action: ActionModel) -> ActionModel | None:
    """The step that ends ``action`` - ``None`` when it has no opposite."""
    if action.target is None:
        return None
    if action.type is ActionType.TURN_ON:
        return ActionModel(type=ActionType.TURN_OFF, target=action.target)
    if action.type is ActionType.REGISTERED_SERVICE and action.service_domain and action.service_name:
        inverse = _INVERSE_SERVICES.get((action.service_domain, action.service_name))
        if inverse is None:
            return None
        return ActionModel(
            type=ActionType.REGISTERED_SERVICE, target=action.target,
            service_domain=inverse[0], service_name=inverse[1],
        )
    return None


def with_duration(
    actions: Sequence[ActionModel | ActionGroup], seconds: int
) -> tuple[ActionModel | ActionGroup, ...] | None:
    """Every leaf gets the duration; ``None`` if one of them cannot end."""
    timed: list[ActionModel | ActionGroup] = []
    for action in actions:
        if isinstance(action, ActionGroup) or inverse_of(action) is None or action.duration_seconds:
            return None
        timed.append(replace(action, duration_seconds=seconds))
    return tuple(timed) if timed else None
