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

_AMOUNT = r"(?P<amount>\d+(?:[.,]5)?|[a-zäöüß]+|eine\s+halbe|einer\s+halben|anderthalb|eineinhalb)"
_UNIT = r"(?P<unit>sekunden?|minuten?|stunden?)"
_FOR_RE = re.compile(rf"\b(?:für|fuer)\s+(?:die\s+(?:nächsten|naechsten)\s+)?{_AMOUNT}\s+{_UNIT}\b(?:\s+lang)?", re.IGNORECASE)
_LONG_RE = re.compile(rf"\b{_AMOUNT}\s+{_UNIT}\s+lang\b", re.IGNORECASE)
_BARE_RE = re.compile(rf"(?<!\S){_AMOUNT}\s+{_UNIT}\s*[.!]?\s*$", re.IGNORECASE)
# Prepositions that make "N Minuten" a delay, interval, window or age.
_TEMPORAL_LEAD = frozenset({
    "in", "nach", "alle", "seit", "vor", "bis", "innerhalb", "binnen", "als", "von", "um",
    "jede", "jeden", "jedes", "spätestens", "frühestens", "über", "unter", "mindestens",
})


def _seconds(amount: str, unit: str) -> int | None:
    key = re.sub(r"\s+", " ", amount.casefold())
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


def split_action_duration(text: str) -> tuple[int | None, str]:
    """The spoken duration of an action clause and the clause without it."""
    for pattern in (_FOR_RE, _LONG_RE, _BARE_RE):
        match = pattern.search(text)
        if match is None:
            continue
        before = text[:match.start()].split()
        if pattern is not _FOR_RE and before and before[-1].casefold().strip(",") in _TEMPORAL_LEAD:
            continue
        if pattern is _BARE_RE and len(before) < 2:
            continue  # "20 Minuten." alone is no action with a duration
        seconds = _seconds(match.group("amount"), _unit_key(match.group("unit")))
        if seconds is None:
            continue
        rest = re.sub(r"\s+", " ", f"{text[:match.start()]} {text[match.end():]}").strip(" ,")
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
