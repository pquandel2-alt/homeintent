"""Compile closed extended HA operations into typed semantic frames.

The compiler consumes the same normalized language variant and registry
snapshot as the main V7 interpreter.  It never creates or executes a service
plan: it emits ``HassRegisteredOperation`` plus structured parameters which
the validator and service mapper re-check against the closed operation
registry.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..entities import EntityIndex, EntitySnapshot, normalize_for_compare
from ..entity_scope import resolve_entity_scope
from ..service_call import REGISTERED_OPERATION_INTENT
from .entity_resolution import ResolutionStatus, resolve_mentioned_target
from .frame import Quantifier, SemanticFrame, TargetReference
from .parser import ParseResult
from .primitives import SemanticAction, SemanticProperty


_SET_CUE = re.compile(r"\b(?:stell\w*|setz\w*|regel\w*|änder\w*|aender\w*|(?:aus)?wähl\w*|(?:aus)?waehl\w*)\b", re.I)
_PERCENT = re.compile(r"\b(100|[1-9]?\d)\s*(?:prozent|%)\b", re.I)
_NUMBER = re.compile(r"\bauf\s+(-?\d+(?:[,.]\d+)?)\b", re.I)
_REGISTERED_CUE = re.compile(
    r"\b(?:heizbetrieb|kühlbetrieb|kuehlbetrieb|automatik|entfeuchten|"
    r"lüften|lueften|preset|modus|betrieb|lautstärke|lautstaerke|quelle|"
    r"ladestation|piep\w*|suchsignal|saugstärke|saugstaerke|saugstufe|saugleistung|"
    r"luftfeuchtigkeit|feuchtigkeit|\w*befeuchter|\w*warmwasser\w*|\w*heizung|\w*profil|"
    r"\w*programm|lamellen|drehzahl|"
    r"neigung|kippposition|\w*ventil|mähroboter|maehroboter|kamera|"
    r"nachricht|meldung|oszillier\w*|oszillation|schwenk\w*|richtung|unmute|"
    r"stummschaltung|stumm|ton|laut|(?:aus)?wähl\w*|(?:aus)?waehl\w*|timer|bescheid)\b"
    # "Stelle/Schalte X auf <Option|Zahl>": the option vocabulary lives in the
    # entity attributes (source_list, options, preset_modes ...), so the
    # generic "auf <Wert>" shape must reach the per-domain handlers (F11).
    r"|\bauf\s+\S+",
    re.I,
)


def has_registered_operation_cue(text: str) -> bool:
    """Whether the utterance explicitly names a registered operation slot."""
    return _REGISTERED_CUE.search(text) is not None


def _entity(
    text: str,
    entities: list[EntitySnapshot],
    domains: Iterable[str],
    index: EntityIndex | None,
) -> EntitySnapshot | None:
    resolution = resolve_mentioned_target(
        text, entities, frozenset(domains), index=index
    )
    return (
        resolution.entity
        if resolution.status is ResolutionStatus.RESOLVED
        else None
    )


def climate_in_named_area(text: str, entities: list[EntitySnapshot]) -> EntitySnapshot | None:
    """ "die Heizung in der Küche" -> the only climate entity of that room."""
    key = normalize_for_compare(text)
    if not re.search(r"\b(?:\w*heizung|thermostat|klima\w*|heizkoerper)\b", key):
        return None
    candidates = [
        entity for entity in entities
        if entity.domain == "climate"
        and any(
            name and re.search(
                rf"\b(?:im|in\s+der|in\s+dem|am|beim)\s+{re.escape(normalize_for_compare(name))}\b", key
            )
            for name in (entity.area_name, *entity.area_aliases)
        )
    ]
    return candidates[0] if len(candidates) == 1 else None


def _option(text: str, values: object) -> str | None:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return None
    normalized = normalize_for_compare(text)
    matches = [
        str(value)
        for value in values
        if (key := normalize_for_compare(str(value)))
        and re.search(rf"(?<!\w){re.escape(key)}(?!\w)", normalized)
    ]
    return matches[0] if len(matches) == 1 else None


def _bounded(entity: EntitySnapshot, value: float, low: str, high: str, defaults: tuple[float, float]) -> bool:
    try:
        return float(entity.attributes.get(low, defaults[0])) <= value <= float(
            entity.attributes.get(high, defaults[1])
        )
    except (TypeError, ValueError):
        return False


def _result(
    text: str,
    entity: EntitySnapshot,
    service_domain: str,
    service_name: str,
    data: dict[str, object] | None = None,
    *,
    action: SemanticAction = SemanticAction.SET,
    property_: SemanticProperty | None = None,
) -> ParseResult:
    return ParseResult(
        frame=SemanticFrame(
            intent=REGISTERED_OPERATION_INTENT,
            target=TargetReference(entity.friendly_name, entity.entity_id, entity.domain),
            area=None,
            parameters={
                "service_domain": service_domain,
                "service_name": service_name,
                "service_data": dict(data or {}),
            },
            source_text=text,
            action=action,
            property=property_,
        ),
        resolved_entities=[entity],
    )


def _multi_result(
    text: str,
    entities: tuple[EntitySnapshot, ...],
    service_domain: str,
    service_name: str,
    data: dict[str, object],
) -> ParseResult:
    return ParseResult(
        frame=SemanticFrame(
            intent=REGISTERED_OPERATION_INTENT,
            target=TargetReference(service_domain, domain=entities[0].domain),
            area=None,
            quantifier=Quantifier("all"),
            parameters={
                "service_domain": service_domain,
                "service_name": service_name,
                "service_data": data,
            },
            source_text=text,
            action=SemanticAction.SET,
        ),
        resolved_entities=list(entities),
    )


def compile_registered_operation(
    text: str,
    entities: list[EntitySnapshot],
    *,
    index: EntityIndex | None = None,
) -> ParseResult | None:
    """Return one fully typed, allow-listed extended operation."""
    if not has_registered_operation_cue(text):
        return None
    scope = resolve_entity_scope(
        text,
        entities,
        frozenset({"humidifier", "water_heater", "number", "input_number", "select"}),
    )
    if scope is not None and len(scope.entities) > 1:
        domain = scope.entities[0].domain
        percent = _PERCENT.search(text)
        if domain == "humidifier" and percent is not None and re.search(
            r"\b(?:luftfeuchtigkeit|feuchtigkeit)\b", text, re.I
        ):
            value = int(percent.group(1))
            if all(
                _bounded(entity, value, "min_humidity", "max_humidity", (0, 100))
                for entity in scope.entities
            ):
                return _multi_result(
                    text, scope.entities, "humidifier", "set_humidity", {"humidity": value}
                )

    climate = _entity(text, entities, {"climate"}, index) or climate_in_named_area(text, entities)
    if climate is not None:
        mode_words = {
            "heizbetrieb": "heat", "kühlbetrieb": "cool", "kuehlbetrieb": "cool",
            "automatik": "auto", "entfeuchten": "dry", "lüften": "fan_only",
            "lueften": "fan_only",
        }
        modes = [value for word, value in mode_words.items() if re.search(rf"\b{word}\b", text, re.I)]
        if len(modes) == 1 and _SET_CUE.search(text):
            if modes[0] not in (climate.attributes.get("hvac_modes") or ()):
                return None
            return _result(text, climate, "climate", "set_hvac_mode", {"hvac_mode": modes[0]})
        for attribute, service, key in (
            ("preset_modes", "set_preset_mode", "preset_mode"),
            ("fan_modes", "set_fan_mode", "fan_mode"),
            ("swing_modes", "set_swing_mode", "swing_mode"),
        ):
            if (choice := _option(text, climate.attributes.get(attribute))) is not None and _SET_CUE.search(text):
                return _result(text, climate, "climate", service, {key: choice})
        # A named climate entity is authoritative for this utterance.  If no
        # allow-listed mode/preset matched, the standard semantic compiler
        # may still handle temperature/on/off, but scanning every unrelated
        # registered-operation domain cannot produce a sound alternative.
        return None

    player = _entity(text, entities, {"media_player"}, index)
    if player is not None:
        unmute = re.search(
            r"\b(?:unmute|nicht\s+mehr\s+stumm|wieder\s+laut|laut\s+schalt\w*)\b|"
            r"\bstummschaltung\b.*\b(?:aus|aufheb\w*|auf)\b|"
            r"\bton\b.*\bwieder\b.*\b(?:an|ein|anmach\w*|einschalt\w*)\b",
            text,
            re.I,
        )
        if unmute is not None:
            return _result(
                text,
                player,
                "media_player",
                "volume_mute",
                {"is_volume_muted": False},
                action=SemanticAction.MUTE,
            )
        percent = _PERCENT.search(text)
        if percent is not None and re.search(r"\blautstärke\b", text, re.I) and _SET_CUE.search(text):
            return _result(text, player, "media_player", "volume_set", {"volume_level": int(percent.group(1)) / 100}, property_=SemanticProperty.VOLUME)
        if re.search(r"\bquelle\b", text, re.I) and _SET_CUE.search(text):
            if (source := _option(text, player.attributes.get("source_list"))) is not None:
                return _result(text, player, "media_player", "select_source", {"source": source})
        # "Schalte den Wohnzimmer TV auf Netflix": an offered source named
        # after "auf" selects it (F11).
        if re.search(r"\b(?:schalt\w*|stell\w*|wechsl\w*|wechsel\w*|umschalt\w*)\b", text, re.I):
            target = re.search(r"\bauf\s+(?P<value>.+?)\s*(?:um)?\s*[.!?]*$", text, re.I)
            if target is not None and (
                source := _option(target.group("value"), player.attributes.get("source_list"))
            ) is not None:
                return _result(text, player, "media_player", "select_source", {"source": source})

    vacuum = _entity(text, entities, {"vacuum"}, index)
    if vacuum is not None:
        if re.search(r"\b(?:piep\w*|suchsignal)\b", text, re.I):
            return _result(
                text,
                vacuum,
                "vacuum",
                "locate",
                action=SemanticAction.LOCATE,
            )
        if re.search(r"\b(?:ladestation|zurück\s+zur\s+station)\b", text, re.I) and re.search(r"\b(?:schick\w*|fahr\w*|soll\w*)\b", text, re.I):
            return _result(text, vacuum, "vacuum", "return_to_base", action=SemanticAction.START)
        if re.search(r"\bpaus\w*\b", text, re.I):
            return _result(text, vacuum, "vacuum", "pause", action=SemanticAction.PAUSE)
        if (speed := _option(text, vacuum.attributes.get("fan_speed_list"))) is not None and _SET_CUE.search(text):
            return _result(text, vacuum, "vacuum", "set_fan_speed", {"fan_speed": speed})

    fan = _entity(text, entities, {"fan"}, index)
    if fan is not None:
        if (preset := _option(text, fan.attributes.get("preset_modes"))) is not None and re.search(
            r"\b(?:preset|modus|stell\w*|wähl\w*|waehl\w*)\b", text, re.I
        ):
            return _result(text, fan, "fan", "set_preset_mode", {"preset_mode": preset})
        oscillating = re.search(r"\b(?:oszillier\w*|oszillation|schwenk\w*)\b", text, re.I)
        if oscillating is not None:
            on = re.search(
                r"\b(?:an|ein|aktivier\w*|einschalt\w*)\b|^\s*lass\b(?!.*\bnicht\b)", text, re.I
            )
            off = re.search(r"\b(?:aus|deaktivier\w*|ausschalt\w*|stopp\w*|nicht\s+mehr)\b", text, re.I)
            if bool(on) != bool(off):
                return _result(text, fan, "fan", "oscillate", {"oscillating": bool(on)})
        direction = re.search(r"\b(?:vorwärts|vorwaerts|rückwärts|rueckwaerts|forward|reverse)\b", text, re.I)
        if direction is not None and re.search(r"\b(?:richtung|stell\w*|setz\w*)\b", text, re.I):
            forward = normalize_for_compare(direction.group(0)) in {"vorwaerts", "forward"}
            return _result(text, fan, "fan", "set_direction", {"direction": "forward" if forward else "reverse"})

    humidifier = _entity(text, entities, {"humidifier"}, index)
    if humidifier is not None:
        percent = _PERCENT.search(text)
        # For a humidifier "auf 50 Prozent" can only mean its target humidity.
        if percent is not None and (
            re.search(r"\b(?:luftfeuchtigkeit|feuchtigkeit)\b", text, re.I) or _SET_CUE.search(text)
        ):
            value = int(percent.group(1))
            if not _bounded(humidifier, value, "min_humidity", "max_humidity", (0, 100)):
                return None
            return _result(text, humidifier, "humidifier", "set_humidity", {"humidity": value})
        if (mode := _option(text, humidifier.attributes.get("available_modes"))) is not None and _SET_CUE.search(text):
            return _result(text, humidifier, "humidifier", "set_mode", {"mode": mode})

    heater = _entity(text, entities, {"water_heater"}, index)
    if heater is not None:
        number = _NUMBER.search(text)
        if number is not None and re.search(r"\b(?:grad|temperatur|warmwasser)\b", text, re.I):
            value = float(number.group(1).replace(",", "."))
            if not _bounded(heater, value, "min_temp", "max_temp", (20, 90)):
                return None
            return _result(text, heater, "water_heater", "set_temperature", {"temperature": value})
        if (mode := _option(text, heater.attributes.get("operation_list"))) is not None and _SET_CUE.search(text):
            return _result(text, heater, "water_heater", "set_operation_mode", {"operation_mode": mode})

    numeric = _entity(text, entities, {"number", "input_number"}, index)
    if numeric is not None and (number := _NUMBER.search(text)) is not None and _SET_CUE.search(text):
        value = float(number.group(1).replace(",", "."))
        if not _bounded(numeric, value, "min", "max", (float("-inf"), float("inf"))):
            return None
        return _result(text, numeric, numeric.domain, "set_value", {"value": value})

    select = _entity(text, entities, {"select"}, index)
    if select is not None and _SET_CUE.search(text):
        if (option := _option(text, select.attributes.get("options"))) is not None:
            return _result(text, select, "select", "select_option", {"option": option})

    cover = _entity(text, entities, {"cover"}, index)
    if cover is not None and (percent := _PERCENT.search(text)) is not None and re.search(
        r"\b(?:lamellen|neigung|winkel|kippposition)\b", text, re.I
    ):
        return _result(
            text,
            cover,
            "cover",
            "set_cover_tilt_position",
            {"tilt_position": int(percent.group(1))},
        )

    valve = _entity(text, entities, {"valve"}, index)
    if valve is not None:
        percent = _PERCENT.search(text)
        if percent is not None and re.search(r"\b(?:position|stell\w*|setz\w*|öffn\w*|oeffn\w*)\b", text, re.I):
            return _result(text, valve, "valve", "set_valve_position", {"position": int(percent.group(1))}, action=SemanticAction.OPEN)
        opening = re.search(r"\b(?:öffn\w*|oeffn\w*)\b", text, re.I)
        closing = re.search(r"\b(?:schließ\w*|schliess\w*)\b", text, re.I)
        if bool(opening) != bool(closing):
            try:
                features = int(valve.attributes.get("supported_features", 0))
            except (TypeError, ValueError):
                return None
            required = 1 if opening else 2
            if not features & required:
                return None
            return _result(text, valve, "valve", "open_valve" if opening else "close_valve", action=SemanticAction.OPEN if opening else SemanticAction.CLOSE)

    mower = _entity(text, entities, {"lawn_mower"}, index)
    if mower is not None:
        operations = [
            (service, feature, action)
            for pattern, service, feature, action in (
                (r"\b(?:mähe|mähen|maehe|maehen|start\w*)\b", "start_mowing", 1, SemanticAction.START),
                (r"\bpaus\w*\b", "pause", 2, SemanticAction.PAUSE),
                (r"\b(?:ladestation|dock)\b", "dock", 4, SemanticAction.START),
            )
            if re.search(pattern, text, re.I)
        ]
        if len(operations) == 1:
            service, feature, action = operations[0]
            try:
                supported = int(mower.attributes.get("supported_features", 0))
            except (TypeError, ValueError):
                return None
            if supported & feature:
                return _result(text, mower, "lawn_mower", service, action=action)

    helper_timer = _entity(text, entities, {"timer"}, index)
    if helper_timer is not None:
        operations = [
            (service, action)
            for pattern, service, action in (
                (r"\b(?:start\w*|beginn\w*)\b", "start", SemanticAction.START),
                (r"\b(?:pausier\w*|anhalt\w*|halt\w*\s+an)\b", "pause", SemanticAction.PAUSE),
                (r"\b(?:abbrech\w*|brich\w*|stopp\w*|beend\w*)\b", "cancel", SemanticAction.STOP),
            )
            if re.search(pattern, text, re.I)
        ]
        if len(operations) == 1:
            service, action = operations[0]
            return _result(text, helper_timer, "timer", service, action=action)

    camera = _entity(text, entities, {"camera"}, index)
    if camera is not None and player is not None and re.search(r"\b(?:zeig\w*|stream\w*|übertrag\w*|uebertrag\w*)\b", text, re.I):
        return _result(text, camera, "camera", "play_stream", {"media_player": player.entity_id}, action=SemanticAction.START)

    notify = _entity(text, entities, {"notify"}, index)
    clause = None
    if re.search(r"\b(?:send\w*|schick\w*|sag\w*|gib)\b", text, re.I):
        # "Schick Anna eine Nachricht, dass das Essen fertig ist" (F13): the
        # shared notification grammar extracts recipient and message; a
        # named recipient must match exactly one notify entity.
        from ..automation_notification import resolve_notify_target
        from ..notification_language import NotificationRecipientKind, parse_notification_clause

        clause = parse_notification_clause(text)
        if (
            notify is None
            and clause is not None
            and clause.recipient_kind is NotificationRecipientKind.EXPLICIT_TARGET
            and (target := resolve_notify_target(clause.recipient_name or "", entities)) is not None
        ):
            notify = next((item for item in entities if item.entity_id == target.entity_id), None)
    if notify is not None and re.search(r"\b(?:send\w*|schick\w*|sag\w*|gib)\b", text, re.I):
        if clause is not None and clause.message:
            return _result(
                text, notify, "notify", "send_message", {"message": clause.message},
                action=SemanticAction.START,
            )
        message = re.search(r"\b(?:nachricht|meldung)\s+(.+)$", text, re.I)
        if message is not None and (body := message.group(1).strip(" .!?")):
            return _result(text, notify, "notify", "send_message", {"message": body}, action=SemanticAction.START)
    return None
