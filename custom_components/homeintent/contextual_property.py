"""Target inference for short, property-centric conversation follow-ups.

This is deliberately deterministic: the previous turn may contribute a
property and a spatial scope, but never an arbitrary device guess. A single
eligible actuator is selected, multiple actuators produce clarification and
read-only properties never become actions.
"""

from __future__ import annotations

import re

from .entities import EntitySnapshot
from .nlu.context import ConversationContext
from .nlu.frame import AreaReference, SemanticFrame, TargetReference
from .nlu.normalize import normalize
from .nlu.parse_outcome import ParseFailureReason, UnderstandingFeedback
from .nlu.parser import ClarificationRequest, ParseResult
from .nlu.primitives import SemanticProperty


class ContextualPropertyResolver:
    """Resolve omitted actuator targets using the previous semantic focus."""

    _NUMBERS = {
        "null": 0,
        "ein": 1,
        "eine": 1,
        "einen": 1,
        "eins": 1,
        "zwei": 2,
        "drei": 3,
        "vier": 4,
        "fünf": 5,
        "sechs": 6,
        "sieben": 7,
        "acht": 8,
        "neun": 9,
        "zehn": 10,
        "zwanzig": 20,
        "dreißig": 30,
        "vierzig": 40,
        "fünfzig": 50,
        "sechzig": 60,
        "siebzig": 70,
        "achtzig": 80,
        "neunzig": 90,
        "hundert": 100,
    }

    @classmethod
    def _number(cls, raw: str) -> float | None:
        normalized = raw.casefold().strip()
        if normalized in cls._NUMBERS:
            return float(cls._NUMBERS[normalized])
        try:
            return float(normalized.replace(",", "."))
        except ValueError:
            return None

    def resolve(
        self,
        text: str,
        entities: list[EntitySnapshot],
        context: ConversationContext | None,
    ) -> ParseResult | ClarificationRequest | UnderstandingFeedback | None:
        if context is None:
            return None
        normalized = normalize(text)

        previous = self._previous_value(normalized, text, entities, context)
        if previous is not None:
            return previous

        temperature = self._temperature(normalized, text, entities, context)
        if temperature is not None:
            return temperature

        percentage = self._percentage(normalized, text, entities, context)
        if percentage is not None:
            return percentage

        return self._fan_level(normalized, text, entities, context)

    def _previous_value(self, normalized, source, entities, context):
        words = {word.casefold() for word in re.findall(r"[\wäöüß]+", normalized, re.IGNORECASE)}
        asks_previous = bool(words & {"vorher", "zuvor", "vorherigen", "alten", "früheren"}) and bool(
            words & {"wert", "temperatur", "einstellung", "zurück"}
        )
        if not asks_previous:
            return None
        if self._property(context) is not SemanticProperty.TEMPERATURE:
            return UnderstandingFeedback(
                ParseFailureReason.UNSAFE_INFERENCE,
                "Ich habe keinen belegten vorherigen Stellwert für dieses Gerät und ändere nichts.",
            )
        value = context.previous_value
        if value is None or not 5 <= value <= 30:
            return UnderstandingFeedback(
                ParseFailureReason.UNSAFE_INFERENCE,
                "Ich habe keinen sicheren vorherigen Temperaturwert und ändere nichts.",
            )
        candidates = self.candidates("climate", entities, context, capability="TEMPERATURE")
        if candidates is None or len(candidates) != 1:
            return UnderstandingFeedback(
                ParseFailureReason.UNSAFE_INFERENCE,
                "Die Heizung zum vorherigen Wert ist nicht mehr eindeutig; ich ändere nichts.",
            )
        return self._setpoint_result(
            source, "HassClimateSetTemperature", "Temperatur", "climate",
            {"temperature": value}, candidates,
        )

    @staticmethod
    def _property(context: ConversationContext) -> SemanticProperty | None:
        if context.focus is not None and context.focus.property is not None:
            return context.focus.property
        previous = context.last_command
        raw = previous.parameters.get("property") if previous is not None else None
        if raw == "temperatur":
            return SemanticProperty.TEMPERATURE
        if raw == "helligkeit":
            return SemanticProperty.BRIGHTNESS
        if raw == "position":
            return SemanticProperty.POSITION
        if raw in ("geschwindigkeit", "stufe"):
            return SemanticProperty.FAN_SPEED
        if raw == "batterie":
            return SemanticProperty.BATTERY
        if raw == "luftfeuchtigkeit":
            return SemanticProperty.HUMIDITY
        if raw == "leistung":
            return SemanticProperty.POWER
        if raw == "energie":
            return SemanticProperty.ENERGY
        return None

    @staticmethod
    def candidates(
        domain: str,
        entities: list[EntitySnapshot],
        context: ConversationContext,
        *,
        capability: str | None = None,
    ) -> list[EntitySnapshot] | None:
        def eligible(entity: EntitySnapshot) -> bool:
            return entity.domain == domain and (
                capability is None or capability in entity.capabilities
            )

        focus = context.focus
        if focus is not None:
            if focus.scope_kind == "entity" and focus.scope_id:
                direct = [
                    entity for entity in entities
                    if entity.entity_id == focus.scope_id and eligible(entity)
                ]
                if direct:
                    return direct
            if focus.scope_kind == "area" and focus.scope_id:
                return [
                    entity for entity in entities
                    if entity.area_id == focus.scope_id and eligible(entity)
                ]
            if focus.scope_kind == "floor" and focus.scope_id:
                return [
                    entity for entity in entities
                    if entity.floor_id == focus.scope_id and eligible(entity)
                ]

        direct_ids = {
            entity.entity_id for entity in context.last_entities if eligible(entity)
        }
        if direct_ids:
            return [entity for entity in entities if entity.entity_id in direct_ids]

        previous = context.last_command
        parameters = previous.parameters if previous is not None else {}
        location_kind = parameters.get("location_kind")
        location_id = parameters.get("location_id")
        if location_kind == "area" and location_id:
            return [e for e in entities if eligible(e) and e.area_id == location_id]
        if location_kind == "floor" and location_id:
            return [e for e in entities if eligible(e) and e.floor_id == location_id]

        area_ids = {e.area_id for e in context.last_entities if e.area_id is not None}
        floor_ids = {e.floor_id for e in context.last_entities if e.floor_id is not None}
        if len(area_ids) == 1:
            area_id = next(iter(area_ids))
            return [e for e in entities if eligible(e) and e.area_id == area_id]
        if len(floor_ids) == 1:
            floor_id = next(iter(floor_ids))
            return [e for e in entities if eligible(e) and e.floor_id == floor_id]
        return None

    def _temperature(self, normalized, source, entities, context):
        prop = self._property(context)
        has_measurement = any(
            entity.domain == "sensor" and entity.device_class == "temperature"
            for entity in context.last_entities
        )
        if prop is not SemanticProperty.TEMPERATURE and not has_measurement:
            return None

        absolute = re.match(
            r"^(?:kannst du\s+)?(?:bitte\s+)?(?:"
            r"(?:(?:die\s+)?temperatur\s+)?auf\s+(?P<a>.+?)\s+grad(?:\s+"
            r"(?:erhöhen|anheben|stellen|setzen|ändern|regeln))?"
            r"|(?:erhöhe|erhöh|stelle|stell|setze|setz|ändere|änder|regel)\s+"
            r"(?:die\s+)?temperatur\s+auf\s+(?P<b>.+?)\s+grad"
            r"|(?:stelle|stell|setze|setz|mach)\s+(?:sie|es|die\s+temperatur)\s+"
            r"auf\s+(?P<c>.+?)\s+grad)\s*[?.!]*$",
            normalized,
            re.IGNORECASE,
        )
        relative = re.match(
            r"^(?:jetzt\s+)?(?:noch\s+)?"
            r"(?:mach(?:e)?(?:\s+es|\s+die\s+temperatur)?|temperatur)?\s*"
            r"(?P<amount>.+?)\s+grad\s+(?P<direction>wärmer|kälter)\s*[?.!]*$",
            normalized,
            re.IGNORECASE,
        )
        if absolute is None and relative is None:
            return None

        candidates = self.candidates("climate", entities, context, capability="TEMPERATURE")
        if candidates is None:
            return UnderstandingFeedback(
                ParseFailureReason.UNSAFE_INFERENCE,
                "Ich kann aus der vorherigen Abfrage keinen eindeutigen Bereich ableiten.",
            )
        if not candidates:
            return UnderstandingFeedback(
                ParseFailureReason.UNSUPPORTED_CAPABILITY,
                "Dort ist keine steuerbare Heizung oder kein Thermostat für Assist freigegeben.",
            )

        if absolute is not None:
            raw_value = next(value for value in absolute.groups() if value is not None)
        else:
            assert relative is not None
            raw_amount = relative.group("amount")
            amount = self._number(raw_amount) or 0
            current = candidates[0].attributes.get("temperature")
            if current is None or amount <= 0:
                return UnderstandingFeedback(
                    ParseFailureReason.INVALID_VALUE,
                    "Ich konnte die gewünschte Temperaturänderung nicht bestimmen.",
                )
            target = current + amount if relative.group("direction") == "wärmer" else current - amount
            # Hassil's temperature slot accepts an integer spelling here.
            # German number normalization can make a relative amount a
            # float ("zwei" -> "2" -> 2.0), so avoid emitting "23.0" for a
            # mathematically integral target.
            raw_value = f"{target:g}" if isinstance(target, float) else str(target)

        temperature = self._number(raw_value)
        if temperature is None or not 5 <= temperature <= 30:
            return UnderstandingFeedback(
                ParseFailureReason.INVALID_VALUE,
                "Die gewünschte Temperatur muss zwischen 5 und 30 Grad liegen.",
            )
        return self._setpoint_result(
            source, "HassClimateSetTemperature", "Temperatur", "climate",
            {"temperature": temperature}, candidates,
        )

    def _percentage(self, normalized, source, entities, context):
        match = re.match(
            r"^(?:kannst du\s+)?(?:bitte\s+)?(?:"
            r"(?:die\s+)?(?P<property>helligkeit|position|rollläden?|rollos?|jalousien?)\s+"
            r"auf\s+(?P<a>.+?)\s*(?:prozent)?(?:\s+"
            r"(?:erhöhen|anheben|stellen|setzen|ändern|dimmen|fahren))?"
            r"|(?:stelle|stell|setze|setz|mach|fahre|fahr)\s+"
            r"(?:sie|es|die\s+helligkeit|die\s+position)\s+auf\s+"
            r"(?P<b>.+?)\s+prozent"
            r"|auf\s+(?P<c>.+?)\s+prozent)\s*[?.!]*$",
            normalized,
            re.IGNORECASE,
        )
        if match is None:
            return None
        property_word = (match.group("property") or "").casefold()
        prop = self._property(context)
        contextual_domains = {entity.domain for entity in context.last_entities}
        if property_word == "helligkeit" or (not property_word and prop is SemanticProperty.BRIGHTNESS):
            domain = "light"
        elif property_word or (not property_word and prop is SemanticProperty.POSITION):
            domain = "cover"
        elif contextual_domains == {"light"}:
            domain = "light"
        elif contextual_domains == {"cover"}:
            domain = "cover"
        else:
            return None
        raw = match.group("a") or match.group("b") or match.group("c")
        candidates = self.candidates(
            domain, entities, context,
            capability="BRIGHTNESS" if domain == "light" else "POSITION",
        )
        label = "Licht" if domain == "light" else "Rollladen"
        if not candidates:
            return UnderstandingFeedback(
                ParseFailureReason.UNSUPPORTED_CAPABILITY,
                f"Dort ist kein steuerbares {label} für Assist freigegeben.",
            )
        percent = self._number(raw)
        if percent is None or not percent.is_integer() or not 0 <= percent <= 100:
            return UnderstandingFeedback(
                ParseFailureReason.INVALID_VALUE,
                "Der Prozentwert muss zwischen 0 und 100 liegen.",
            )
        return self._setpoint_result(
            source, "HassSetPercentage", label, domain,
            {"percent": int(percent)}, candidates,
        )

    def _fan_level(self, normalized, source, entities, context):
        match = re.match(
            r"^(?:kannst du\s+)?(?:bitte\s+)?(?:"
            r"(?:die\s+)?(?:geschwindigkeit|stufe)\s+auf\s+(?:stufe\s+)?(?P<a>.+?)"
            r"(?:\s+(?:erhöhen|stellen|setzen|ändern))?"
            r"|(?:stelle|stell|setze|setz|mach)\s+(?:sie|ihn|es|die\s+stufe)\s+"
            r"auf\s+stufe\s+(?P<b>.+?)"
            r"|auf\s+stufe\s+(?P<c>.+?))\s*[?.!]*$",
            normalized,
            re.IGNORECASE,
        )
        if match is None:
            return None
        raw = match.group("a") or match.group("b") or match.group("c")
        candidates = self.candidates("fan", entities, context, capability="FAN_SPEED")
        if not candidates:
            return UnderstandingFeedback(
                ParseFailureReason.UNSUPPORTED_CAPABILITY,
                "Dort ist kein Ventilator für Assist freigegeben.",
            )
        level = self._number(raw)
        if level is None or not level.is_integer() or not 1 <= level <= 10:
            return UnderstandingFeedback(
                ParseFailureReason.INVALID_VALUE,
                "Die Ventilatorstufe muss zwischen 1 und 10 liegen.",
            )
        return self._setpoint_result(
            source, "HassFanSetSpeed", "Ventilator", "fan",
            {"level": int(level)}, candidates,
        )

    @staticmethod
    def _setpoint_result(source, intent, label, domain, parameters, candidates):
        if len(candidates) > 1:
            return ClarificationRequest(
                pending_intent=intent,
                pending_target=label,
                candidates=tuple(candidates),
                pending_parameters=parameters,
            )
        entity = candidates[0]
        return ParseResult(
            frame=SemanticFrame(
                intent=intent,
                target=TargetReference(text=label, entity_id=entity.entity_id, domain=domain),
                area=(
                    AreaReference(text=entity.area_name, area_id=entity.area_id)
                    if entity.area_id is not None and entity.area_name is not None
                    else None
                ),
                parameters=parameters,
                source_text=source,
            ),
            resolved_entities=[entity],
        )
