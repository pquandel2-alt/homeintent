"""Read-only state and capability queries for extended device domains."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .entities import EntitySnapshot, format_spoken_number, spoken_state
from .entity_scope import DOMAIN_WORDS, resolve_entity_scope
from .device_result import DeviceControlResult
from .nlu.language_frontend import LanguageDocument
from .nlu.normalize import normalize
from .nlu.semantic_utterance import SpeechAct
from .productivity import format_duration


_DOMAINS = frozenset(DOMAIN_WORDS)
_QUERY_CUE = re.compile(
    r"\b(?:welch\w*|wie\s+hoch|wie\s+viel|ist|sind|laeuft|laufen|steht|status|zustand)\b",
    re.I,
)


def _state_text(entity: EntitySnapshot) -> str:
    if entity.domain == "humidifier":
        humidity = entity.attributes.get("humidity")
        return f"{entity.friendly_name}: {format_spoken_number(humidity)} Prozent" if humidity is not None else f"{entity.friendly_name}: {spoken_state(entity.state)}"
    if entity.domain in {"number", "input_number"}:
        unit = entity.unit or entity.attributes.get("unit_of_measurement") or ""
        return f"{entity.friendly_name}: {format_spoken_number(entity.state)} {unit}".strip()
    if entity.domain == "water_heater":
        value = entity.attributes.get("temperature")
        return f"{entity.friendly_name}: {format_spoken_number(value)} Grad" if value is not None else f"{entity.friendly_name}: {spoken_state(entity.state)}"
    return f"{entity.friendly_name}: {spoken_state(entity.state)}"


_TIMER_REMAINING_RE = re.compile(
    r"\bwie\s+(?:lange|viel\s+zeit)\s+(?:laeuft|läuft|dauert|braucht|hat)\b.*\bnoch\b", re.I
)


def _timer_remaining(entity: EntitySnapshot, now: datetime) -> int | None:
    """Remaining seconds of a Home Assistant timer helper, if it runs."""
    finishes_at = entity.attributes.get("finishes_at")
    if entity.state == "active" and isinstance(finishes_at, str):
        try:
            finish = datetime.fromisoformat(finishes_at.replace("Z", "+00:00"))
        except ValueError:
            finish = None
        if finish is not None:
            if finish.tzinfo is None:
                finish = finish.replace(tzinfo=timezone.utc)
            return max(0, round((finish - now).total_seconds()))
    remaining = entity.attributes.get("remaining")
    if isinstance(remaining, str) and (match := re.fullmatch(r"(\d+):(\d{2}):(\d{2})", remaining)):
        hours, minutes, seconds = (int(part) for part in match.groups())
        return hours * 3600 + minutes * 60 + seconds
    return None


def match_timer_helper_remaining(
    text: str, entities: list[EntitySnapshot], now: datetime | None = None
) -> DeviceControlResult | None:
    """ "Wie lange läuft der Waschgang noch?" for a ``timer.*`` helper (F11)."""
    if _TIMER_REMAINING_RE.search(text) is None:
        return None
    scope = resolve_entity_scope(text, [item for item in entities if item.domain == "timer"], frozenset({"timer"}))
    if scope is None or len(scope.entities) != 1:
        return None
    entity = scope.entities[0]
    if entity.state == "idle":
        return DeviceControlResult(
            None, f"Der Timer {entity.friendly_name} läuft gerade nicht.", entity=entity, is_query=True
        )
    seconds = _timer_remaining(entity, now or datetime.now(timezone.utc))
    if seconds is None:
        return None
    state = " (pausiert)" if entity.state == "paused" else ""
    return DeviceControlResult(
        None,
        f"Der Timer {entity.friendly_name}{state} läuft noch {format_duration(seconds)}.",
        entity=entity,
        is_query=True,
    )


def match_extended_device_query(
    text: str,
    entities: list[EntitySnapshot],
    document: LanguageDocument | None = None,
) -> DeviceControlResult | None:
    if document is not None and document.utterance.speech_act is not SpeechAct.QUERY:
        return None
    timer_answer = match_timer_helper_remaining(
        document.source_text if document is not None else text, entities
    )
    if timer_answer is not None:
        return timer_answer
    text = document.normalized_text if document is not None else normalize(text)
    if _QUERY_CUE.search(text) is None:
        return None
    scope = resolve_entity_scope(text, entities, _DOMAINS)
    if scope is None or not scope.entities:
        return None
    rendered = "; ".join(_state_text(entity) for entity in scope.entities[:8])
    suffix = "" if len(scope.entities) <= 8 else f"; und {len(scope.entities) - 8} weitere"
    return DeviceControlResult(
        None,
        rendered + suffix + ".",
        entity=(scope.entities[0] if len(scope.entities) == 1 else None),
        entities=tuple(scope.entities),
        is_query=True,
    )
