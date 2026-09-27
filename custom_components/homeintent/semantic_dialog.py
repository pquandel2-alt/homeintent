"""Dialog completion for commands with one missing semantic component."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .device_result import DeviceControlResult
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.registered_operation_compiler import climate_in_named_area
from .nlu.entity_resolution import (
    ResolutionStatus,
    ResolveStatus,
    resolve_entity,
    resolve_mentioned_target,
)
from .nlu.context import PendingSemanticCommand
from .nlu.domain_operations import DOMAIN_WORDS
from .service_call import ServiceCallPlan


@dataclass(frozen=True)
class SemanticDialogOutcome:
    pending: PendingSemanticCommand | None = None
    question: str | None = None
    result: DeviceControlResult | None = None


_COMMAND_RE = re.compile(r"\b(?:stell\w*|setz\w*|regel\w*|mach\w*|schalt\w*|fahr\w*|öffn\w*|schließ\w*)\b", re.I)
_SOME_RE = re.compile(r"\b(?:ein\s+paar|einige\w*|mehrere\w*)\b", re.I)
_NUMBER_RE = re.compile(r"\b(-?\d+(?:[,.]\d+)?)\s*(grad|prozent|%)?\b", re.I)


def _mentioned_entity(
    text: str, entities: list[EntitySnapshot], domains: frozenset[str]
) -> EntitySnapshot | None:
    result = resolve_mentioned_target(text, entities, domains)
    if result.status is ResolutionStatus.RESOLVED:
        return result.entity
    if "climate" in domains:
        # "die Heizung im Büro" names the only climate entity of that room.
        return climate_in_named_area(text, entities)
    return None

def _spoken_domain(text: str) -> str | None:
    normalized = normalize_for_compare(text)
    matches = [
        domain
        for domain, words in DOMAIN_WORDS.items()
        if domain in {
            "light", "switch", "cover", "fan", "climate", "input_boolean",
            "humidifier", "water_heater", "number", "input_number", "select",
        }
        if any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", normalized) for word in words)
    ]
    return matches[0] if len(matches) == 1 else None


def _action(text: str, domain: str) -> tuple[str, str, str] | None:
    actions = []
    if re.search(r"\b(?:an|ein|einschalt\w*|anmach\w*)\b", text, re.I):
        actions.append(("homeassistant", "turn_on", "eingeschaltet"))
    if re.search(r"\b(?:aus|ausschalt\w*|ausmach\w*)\b", text, re.I):
        actions.append(("homeassistant", "turn_off", "ausgeschaltet"))
    if domain == "cover" and re.search(r"\b(?:hoch|öffn\w*)\b", text, re.I):
        actions.append(("cover", "open_cover", "geöffnet"))
    if domain == "cover" and re.search(r"\b(?:runter|herunter|schließ\w*)\b", text, re.I):
        actions.append(("cover", "close_cover", "geschlossen"))
    return actions[0] if len(actions) == 1 else None


def _temperature_result(
    entity: EntitySnapshot, value: float
) -> DeviceControlResult | None:
    try:
        minimum = float(entity.attributes.get("min_temp", 7))
        maximum = float(entity.attributes.get("max_temp", 35))
    except (TypeError, ValueError):
        return None
    if not minimum <= value <= maximum:
        return None
    return DeviceControlResult(
        ServiceCallPlan("climate", "set_temperature", entity.entity_id, {"temperature": value}),
        f"{entity.friendly_name} auf {value:g} Grad gestellt.",
        entity=entity,
    )


def _value_result(
    entity: EntitySnapshot, service: str, value_kind: str, raw_value: str
) -> DeviceControlResult | None:
    """Validate and materialize a value reply for several HA domains."""
    if value_kind == "option":
        normalized = normalize_for_compare(raw_value.strip(" .!?"))
        options = [
            str(option)
            for option in entity.attributes.get("options", ())
            if normalize_for_compare(str(option)) == normalized
            or normalize_for_compare(str(option)) in normalized
        ]
        if len(options) != 1:
            return None
        return DeviceControlResult(
            ServiceCallPlan(entity.domain, service, entity.entity_id, {"option": options[0]}),
            f"{entity.friendly_name} auf {options[0]} gestellt.",
            entity=entity,
        )

    number = _NUMBER_RE.search(raw_value)
    if number is None:
        return None
    value = float(number.group(1).replace(",", "."))
    if value_kind == "temperature" and entity.domain == "climate":
        return _temperature_result(entity, value)
    bounds = {
        "humidity": ("min_humidity", "max_humidity", 0.0, 100.0, "humidity", "Prozent Luftfeuchtigkeit"),
        "water_temperature": ("min_temp", "max_temp", 20.0, 90.0, "temperature", "Grad"),
        "number": ("min", "max", float("-inf"), float("inf"), "value", ""),
    }.get(value_kind)
    if bounds is None:
        return None
    minimum_key, maximum_key, default_min, default_max, data_key, unit = bounds
    try:
        minimum = float(entity.attributes.get(minimum_key, default_min))
        maximum = float(entity.attributes.get(maximum_key, default_max))
    except (TypeError, ValueError):
        return None
    if not minimum <= value <= maximum:
        return None
    return DeviceControlResult(
        ServiceCallPlan(entity.domain, service, entity.entity_id, {data_key: value}),
        f"{entity.friendly_name} auf {value:g}{(' ' + unit) if unit else ''} gestellt.",
        entity=entity,
    )


def start_semantic_dialog(
    text: str, entities: list[EntitySnapshot]
) -> SemanticDialogOutcome | None:
    """Recognize safe partial commands after all complete parsers declined."""
    if not _COMMAND_RE.search(text) or re.search(
        r"\b(?:nicht|kein\w*|ohne|vielleicht|normalerweise|gestern)\b", text, re.I
    ):
        return None

    if _SOME_RE.search(text):
        domain = _spoken_domain(text)
        if domain is None or (operation := _action(text, domain)) is None:
            return None
        candidates = tuple(entity for entity in entities if entity.domain == domain)
        if len(candidates) < 2:
            return None
        service_domain, service, response_verb = operation
        pending = PendingSemanticCommand(
            "selection", candidates, service_domain, service, {}, response_verb
        )
        return SemanticDialogOutcome(
            pending=pending,
            question="Welche Geräte genau? Bitte nenne ihre Namen.",
        )

    number = _NUMBER_RE.search(text)
    number_unit = number.group(2).casefold() if number is not None and number.group(2) else None
    temperature_value = number is not None and (
        number_unit == "grad" or "temperatur" in text.casefold()
    )
    if temperature_value:
        assert number is not None
        value = float(number.group(1).replace(",", "."))
        candidates = tuple(
            entity for entity in entities
            if entity.domain == "climate" and "TEMPERATURE" in entity.capabilities
        )
        named = _mentioned_entity(text, list(candidates), frozenset({"climate"}))
        if named is not None:
            return None
        if len(candidates) == 1:
            result = _temperature_result(candidates[0], value)
            return SemanticDialogOutcome(result=result) if result is not None else None
        if len(candidates) > 1:
            pending = PendingSemanticCommand(
                "target", candidates, "climate", "set_temperature",
                {"temperature": value}, "eingestellt",
            )
            return SemanticDialogOutcome(pending=pending, question="Welche Heizung meinst du?")

    climate = _mentioned_entity(text, entities, frozenset({"climate"}))
    if climate is not None and re.search(r"\b(?:temperatur|grad|stell\w*|regel\w*)\b", text, re.I):
        pending = PendingSemanticCommand(
            "value", (climate,), "climate", "set_temperature", {}, "eingestellt"
        )
        return SemanticDialogOutcome(
            pending=pending,
            question=f"Auf welche Temperatur soll ich {climate.friendly_name} stellen?",
        )

    specifications = (
        ("humidifier", "set_humidity", "humidity", r"\b(?:luftfeuchtigkeit|feuchtigkeit|stell\w*|regel\w*)\b", "Welche Luftfeuchtigkeit in Prozent möchtest du?"),
        ("water_heater", "set_temperature", "water_temperature", r"\b(?:warmwasser|temperatur|grad|stell\w*)\b", "Auf welche Temperatur soll ich das Warmwasser stellen?"),
        ("number", "set_value", "number", r"\b(?:stell\w*|setz\w*|wert|regel\w*)\b", "Welchen Wert soll ich einstellen?"),
        ("input_number", "set_value", "number", r"\b(?:stell\w*|setz\w*|wert|regel\w*)\b", "Welchen Wert soll ich einstellen?"),
        ("select", "select_option", "option", r"\b(?:wähl\w*|waehl\w*|stell\w*|setz\w*)\b", "Welche Option soll ich auswählen?"),
    )
    for domain, service, value_kind, cue, question in specifications:
        target = _mentioned_entity(text, entities, frozenset({domain}))
        if target is not None and re.search(cue, text, re.I):
            return SemanticDialogOutcome(
                pending=PendingSemanticCommand(
                    "value", (target,), domain, service, {}, "eingestellt",
                    value_kind=value_kind,
                ),
                question=question,
            )
    return None


def continue_semantic_dialog(
    text: str, pending: PendingSemanticCommand
) -> SemanticDialogOutcome:
    """Fill the one missing component without widening the candidate set."""
    if pending.missing == "value":
        value_kind = pending.value_kind or "temperature"
        result = _value_result(
            pending.candidates[0], pending.service, value_kind, text
        )
        if result is None:
            options = pending.candidates[0].attributes.get("options", ())
            hint = (
                " Mögliche Optionen sind: " + ", ".join(str(item) for item in options) + "."
                if value_kind == "option" and options else ""
            )
            return SemanticDialogOutcome(
                pending=pending,
                question="Diesen Wert habe ich nicht eindeutig verstanden oder er wird nicht unterstützt." + hint,
            )
        return SemanticDialogOutcome(result=result)

    if pending.missing == "target":
        resolved = resolve_entity(text.strip(" .!?"), list(pending.candidates))
        if resolved.status is not ResolveStatus.OK or resolved.entity is None:
            return SemanticDialogOutcome(pending=pending, question="Das ist noch nicht eindeutig. Welche Heizung meinst du?")
        value = float(pending.data["temperature"])
        result = _temperature_result(resolved.entity, value)
        return SemanticDialogOutcome(result=result) if result is not None else SemanticDialogOutcome(
            pending=pending, question="Der Wert wird von dieser Heizung nicht unterstützt."
        )

    selected = []
    normalized = normalize_for_compare(text)
    for entity in pending.candidates:
        names = (entity.friendly_name, *entity.aliases)
        if any(
            (candidate := normalize_for_compare(name))
            and re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", normalized)
            for name in names
        ):
            selected.append(entity)
    if not selected:
        return SemanticDialogOutcome(pending=pending, question="Ich konnte daraus keine eindeutigen Gerätenamen erkennen. Welche Geräte genau?")
    plan = ServiceCallPlan(
        pending.service_domain,
        pending.service,
        [entity.entity_id for entity in selected],
        dict(pending.data),
    )
    return SemanticDialogOutcome(
        result=DeviceControlResult(
            plan,
            f"{len(selected)} Geräte {pending.response_verb}.",
        )
    )
