"""User-facing, read-only audit of HomeIntent's selected HA entities."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .device_result import DeviceControlResult
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.language_frontend import LanguageDocument
from .nlu.semantic_utterance import SpeechAct
from .query_target import mentioned_entities
from .nlu.phrases import has, words


CONTROL_DOMAINS = frozenset({
    "alarm_control_panel", "button", "camera", "climate", "cover", "fan",
    "group", "humidifier", "input_boolean", "input_number", "lawn_mower",
    "light", "lock", "media_player", "notify", "number", "scene", "script",
    "select", "switch", "valve", "vacuum", "water_heater",
})
READ_ONLY_DOMAINS = frozenset({
    "binary_sensor", "calendar", "device_tracker", "person", "sensor", "sun",
    "weather",
})
MANAGED_DOMAINS = frozenset({"calendar", "timer", "todo"})
_NON_DEVICE_DOMAINS = frozenset({
    "automation", "conversation", "event", "image", "persistent_notification",
    "remote", "stt", "tts", "update", "zone",
})
_AREA_RELEVANT_DOMAINS = CONTROL_DOMAINS - frozenset({
    "group", "input_boolean", "input_number", "notify", "number", "scene",
    "script", "select",
})
_DOMAIN_LABELS_DE = {
    "alarm_control_panel": "Alarmanlagen", "button": "Tasten", "camera": "Kameras",
    "climate": "Heizungen", "cover": "Rollläden", "fan": "Ventilatoren",
    "humidifier": "Luftbefeuchter", "input_boolean": "Schalter-Helfer",
    "input_number": "Zahlenhelfer", "lawn_mower": "Mähroboter", "light": "Lichter",
    "lock": "Schlösser", "media_player": "Medienplayer", "number": "Regler",
    "scene": "Szenen", "script": "Skripte", "select": "Auswahl-Helfer",
    "switch": "Schalter", "valve": "Ventile", "vacuum": "Saugroboter",
    "water_heater": "Warmwasserbereiter",
}


@dataclass(frozen=True)
class CapabilityAudit:
    selected_count: int
    controllable: tuple[EntitySnapshot, ...]
    readable: tuple[EntitySnapshot, ...]
    managed: tuple[EntitySnapshot, ...]
    unsupported: tuple[EntitySnapshot, ...]
    missing_area: tuple[EntitySnapshot, ...]


def audit_capabilities(entities: list[EntitySnapshot]) -> CapabilityAudit:
    """Classify only the entities already visible to this conversation turn."""
    controllable = tuple(e for e in entities if e.domain in CONTROL_DOMAINS)
    readable = tuple(e for e in entities if e.domain in READ_ONLY_DOMAINS)
    managed = tuple(e for e in entities if e.domain in MANAGED_DOMAINS)
    unsupported = tuple(
        e for e in entities
        if e.domain not in CONTROL_DOMAINS | READ_ONLY_DOMAINS | MANAGED_DOMAINS | _NON_DEVICE_DOMAINS
    )
    missing_area = tuple(
        e for e in controllable
        if e.domain in _AREA_RELEVANT_DOMAINS and e.area_id is None
    )
    return CapabilityAudit(
        selected_count=len(entities),
        controllable=controllable,
        readable=readable,
        managed=managed,
        unsupported=unsupported,
        missing_area=missing_area,
    )


def _answer(text: str) -> DeviceControlResult:
    return DeviceControlResult(None, text, is_query=True)


def _names(entities: tuple[EntitySnapshot, ...], maximum: int = 12) -> str:
    names = sorted(entity.friendly_name for entity in entities)
    if not names:
        return "keine"
    rendered = ", ".join(names[:maximum])
    if len(names) > maximum:
        rendered += f" und {len(names) - maximum} weitere"
    return rendered


def match_capability_audit_query(
    text: str,
    entities: list[EntitySnapshot],
    document: LanguageDocument | None = None,
) -> DeviceControlResult | None:
    """Answer setup/capability questions without exposing hidden HA state."""
    if document is not None and document.utterance.speech_act is not SpeechAct.QUERY:
        return None
    key = normalize_for_compare(
        document.normalized_text if document is not None else text
    )
    audit = audit_capabilities(entities)

    tokens = words(key)
    if has(tokens, "ist homeintent bereit", "pruefe homeintent", "homeintent status"):
        return _answer(
            f"HomeIntent sieht {audit.selected_count} freigegebene Entities: "
            f"{len(audit.controllable)} steuerbar, {len(audit.readable)} lesbar und "
            f"{len(audit.managed)} verwaltbar. {len(audit.missing_area)} steuerbare "
            f"Geräte haben keinen Bereich; {len(audit.unsupported)} Entities gehören "
            "zu noch nicht unterstützten Domänen."
        )

    if has(tokens, "welche geraete haben keinen bereich|raum"):
        return _answer("Ohne Bereich: " + _names(audit.missing_area) + ".")

    if has(
        tokens,
        "welche geraete kannst du steuern|bedienen",
        "welche geraete kann homeintent steuern|bedienen",
    ):
        counts = Counter(entity.domain for entity in audit.controllable)
        groups = [
            f"{count} {_DOMAIN_LABELS_DE.get(domain, domain)}"
            for domain, count in sorted(counts.items())
        ]
        return _answer(
            "Steuerbar: " + (", ".join(groups) if groups else "keine Geräte") + "."
        )

    if has(
        tokens,
        "welche geraete kannst du nicht steuern|bedienen|unterstuetzen",
        "welche geraete kann homeintent nicht steuern|bedienen|unterstuetzen",
        "welche geraete werden nicht unterstuetzt",
    ):
        return _answer("Noch nicht steuerbar: " + _names(audit.unsupported) + ".")

    if has(tokens, "warum kannst du", "warum kann homeintent"):
        targets = mentioned_entities(text, entities)
        if len(targets) > 1:
            return _answer("Welches Gerät meinst du?")
        if len(targets) == 1:
            target = targets[0]
            if target.domain in CONTROL_DOMAINS:
                suffix = (
                    " Es hat allerdings keinen Bereich."
                    if target in audit.missing_area else ""
                )
                return _answer(
                    f"{target.friendly_name} gehört zur unterstützten Domäne "
                    f"{target.domain}.{suffix}"
                )
            if target.domain in READ_ONLY_DOMAINS:
                return _answer(
                    f"{target.friendly_name} ist eine lesende {target.domain}-Entity "
                    "und wird deshalb nicht wie ein Gerät geschaltet."
                )
            if target.domain in MANAGED_DOMAINS:
                return _answer(
                    f"{target.friendly_name} wird über die eigene {target.domain}-Verwaltung bedient."
                )
            return _answer(
                f"Für die Home-Assistant-Domäne {target.domain} gibt es noch keinen "
                "sicheren HomeIntent-Adapter."
            )

    return None
