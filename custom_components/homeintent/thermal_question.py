"""Honest answers to heating-time questions ("Wann ist das Büro warm?") (F24).

A prediction is spoken only when a confirmed V11 thermal model for exactly
that room answers ``OK``. Every other case (no model, low confidence, stale,
out of distribution, missing measurement) gets the documented no-model
answer instead of a guess or a generic "not understood". Read-only: this
module never plans or executes anything.
"""

from __future__ import annotations

import re
from datetime import datetime

from .entities import EntitySnapshot, format_spoken_number, normalize_for_compare
from .nlu.german_morphology import dative_location_phrase, sentence_initial
from .predictive_house_model import PredictiveHouseModel

_THERMAL_QUESTION_RE = re.compile(
    r"\bwann\s+(?:ist|wird)\b.*\b(?:warm|waermer|aufgeheizt)\b"
    r"|\bwie\s+lange\s+(?:braucht|dauert)\b.*\b(?:bis|auf)\s+\d+(?:[,.]\d+)?\s*grad\b"
    r"|\bwie\s+lange\s+(?:braucht|dauert)\b.*\baufheiz"
)
_TARGET_RE = re.compile(r"\b(\d+(?:[,.]\d+)?)\s*grad\b")


def is_thermal_question(text: str) -> bool:
    return _THERMAL_QUESTION_RE.search(normalize_for_compare(text)) is not None


def _area(text: str, entities: list[EntitySnapshot]) -> tuple[str, str] | None:
    key = normalize_for_compare(text)
    found: dict[str, str] = {}
    for entity in entities:
        if entity.area_id is None or entity.area_name is None:
            continue
        for name in (entity.area_name, *entity.area_aliases):
            if name and re.search(rf"\b{re.escape(normalize_for_compare(name))}\b", key):
                found[entity.area_id] = entity.area_name
    return next(iter(found.items())) if len(found) == 1 else None


def _float(value: object) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _current_temperature(area_id: str, entities: list[EntitySnapshot]) -> float | None:
    for entity in entities:
        if entity.area_id == area_id and entity.domain == "climate":
            value = _float(entity.attributes.get("current_temperature"))
            if value is not None:
                return value
    for entity in entities:
        if (
            entity.area_id == area_id
            and entity.domain == "sensor"
            and entity.device_class == "temperature"
        ):
            value = _float(entity.state)
            if value is not None:
                return value
    return None


def _target_temperature(
    text: str, area_id: str, entities: list[EntitySnapshot]
) -> float | None:
    spoken = _TARGET_RE.search(normalize_for_compare(text))
    if spoken is not None:
        return _float(spoken.group(1))
    for entity in entities:
        if entity.area_id == area_id and entity.domain == "climate":
            value = _float(entity.attributes.get("temperature"))
            if value is not None:
                return value
    return None


def _spoken_minutes(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    return "einer Minute" if minutes == 1 else f"{minutes} Minuten"


def answer_thermal_question(
    text: str,
    entities: list[EntitySnapshot],
    predictive_house: PredictiveHouseModel | None,
    now: datetime,
) -> str | None:
    """Return the spoken answer, or ``None`` if this is no heating-time question."""
    if not is_thermal_question(text):
        return None
    area = _area(text, entities)
    if area is None:
        return (
            "Dafür habe ich noch kein belastbares Aufheizmodell, "
            "deshalb kann ich keine Zeit vorhersagen."
        )
    area_id, area_name = area
    location = dative_location_phrase(area_name)
    current = _current_temperature(area_id, entities)
    target = _target_temperature(text, area_id, entities)
    model = predictive_house.thermal_model(area_id) if predictive_house is not None else None
    if (
        predictive_house is not None
        and model is not None
        and model.binding.confirmed
        and current is not None
        and target is not None
    ):
        outdoor: float | None = None
        outdoor_id = model.binding.outdoor_temperature_entity_id
        if outdoor_id is not None:
            outdoor = next(
                (_float(entity.state) for entity in entities if entity.entity_id == outdoor_id),
                None,
            )
        prediction = predictive_house.predict_thermal(
            area_id, current_celsius=current, target_celsius=target,
            outdoor_celsius=outdoor, now=now,
        )
        if prediction.usable_for_planning and prediction.value is not None:
            return sentence_initial(
                f"{location} sind {format_spoken_number(target)} Grad voraussichtlich "
                f"in etwa {_spoken_minutes(prediction.value)} erreicht."
            )
    answer = (
        f"Für das Aufheizen {location} habe ich noch kein belastbares Modell, "
        "deshalb kann ich keine Zeit vorhersagen."
    )
    if current is not None:
        answer += f" Aktuell sind es {format_spoken_number(round(current, 1))} Grad."
    return answer
