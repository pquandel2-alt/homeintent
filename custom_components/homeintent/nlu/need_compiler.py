"""Ground a recognised need in concrete, policy-checked operations.

``need_semantics`` says *what* a person needs ("warmer", "less glare",
"ventilate", routine "sleep"); this module decides *which devices* at the
relevant place can provide it and returns ordinary ``ParseResult`` objects
with a short German reason.  The choice follows the capability that serves
the effect (heating setpoint for warmth, lights for brightness, shading
for glare, a fan for stale air, the playing player for loudness), never a
device name.  Unclear or broad effects become a proposal that needs "Ja";
nothing is executed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..entities import EntitySnapshot, format_spoken_number, normalize_for_compare
from .device_ontology import entity_genera
from .frame import Quantifier, SemanticFrame, TargetReference
from .german_morphology import dative_location_phrase
from .grounded_answer import join_german
from .need_semantics import NeedKind, NeedMeaning, RoutineConcept
from .parser import ParseResult
from .place_model import Place
from .primitives import SemanticAction, SemanticDirection, SemanticProperty
from ..service_call import REGISTERED_OPERATION_INTENT

__all__ = ("NeedOutcome", "compile_need")

_MAX_DIRECT_LIGHTS = 4
_LIGHT_STEP_PERCENT = 20


@dataclass(frozen=True)
class NeedOutcome:
    """Operations serving a need, plus how to talk about them.

    ``results`` run directly when ``confirm`` is ``None``; otherwise
    ``confirm`` is the proposal question and they run after "Ja".
    ``message`` alone is an answer without any action (a hint or a
    question for the missing place).
    """

    results: tuple[ParseResult, ...] = ()
    reason: str | None = None
    confirm: str | None = None
    message: str | None = None


def _frame(
    intent: str,
    targets: Sequence[EntitySnapshot],
    source_text: str,
    *,
    action: SemanticAction,
    parameters: dict[str, object] | None = None,
    prop: SemanticProperty | None = None,
    direction: SemanticDirection | None = None,
) -> ParseResult:
    single = len(targets) == 1
    first = targets[0]
    return ParseResult(
        frame=SemanticFrame(
            intent=intent,
            target=(
                TargetReference(first.friendly_name, first.entity_id, first.domain)
                if single else TargetReference(first.domain, domain=first.domain)
            ),
            area=None,
            quantifier=None if single else Quantifier("all"),
            parameters=parameters or {},
            source_text=source_text,
            action=action,
            property=prop,
            direction=direction,
        ),
        resolved_entities=list(targets),
    )


def _registered(
    targets: Sequence[EntitySnapshot], domain: str, service: str, source_text: str,
    action: SemanticAction = SemanticAction.SET,
) -> ParseResult:
    return _frame(
        REGISTERED_OPERATION_INTENT, targets, source_text, action=action,
        parameters={"service_domain": domain, "service_name": service, "service_data": {}},
    )


def _at(place: Place | None, entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    return [entity for entity in entities if place is None or place.contains(entity)]


def _where(place: Place | None) -> str:
    return place.label if place is not None else "im Haus"


def _names(entities: Sequence[EntitySnapshot]) -> str:
    return join_german([entity.friendly_name for entity in entities])


def _heating(entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    return [
        entity for entity in entities
        if entity.domain == "climate" and "TEMPERATURE" in entity.capabilities
    ]


def _needs_place(kind: NeedKind) -> bool:
    return kind is not NeedKind.ROUTINE


def compile_need(
    meaning: NeedMeaning,
    entities: Sequence[EntitySnapshot],
    place: Place | None,
    source_text: str,
) -> NeedOutcome:
    """Choose operations serving ``meaning`` at ``place``."""
    if meaning.kind is NeedKind.ROUTINE and meaning.routine is not None:
        return _routine(meaning.routine, entities, source_text)
    local = _at(place, entities)
    kind = meaning.kind
    if kind in {NeedKind.WARMER, NeedKind.COOLER}:
        heating = _heating(local)
        if place is None and len(heating) > 1:
            return NeedOutcome(message="In welchem Raum soll ich die Temperatur ändern?")
        if kind is NeedKind.COOLER and not heating:
            fans = [entity for entity in local if entity.domain == "fan"]
            if len(fans) == 1:
                return NeedOutcome(
                    results=(_frame("HassTurnOn", fans, source_text, action=SemanticAction.TURN_ON),),
                    reason=f"Ich habe {_names(fans)} {_where(place)} eingeschaltet.",
                )
        if not heating:
            return NeedOutcome(
                message=f"{_where(place)[:1].upper()}{_where(place)[1:]} gibt es keine Heizung, die ich regeln kann."
            )
        warmer = kind is NeedKind.WARMER
        results = tuple(
            _frame(
                "HassClimateIncreaseTemperature" if warmer else "HassClimateDecreaseTemperature",
                (entity,), source_text, action=SemanticAction.ADJUST,
                prop=SemanticProperty.TEMPERATURE,
                direction=SemanticDirection.INCREASE if warmer else SemanticDirection.DECREASE,
            )
            for entity in heating
        )
        setpoints = [
            entity.attributes.get("temperature") for entity in heating
            if isinstance(entity.attributes.get("temperature"), (int, float))
        ]
        target = (
            f" auf {format_spoken_number(float(setpoints[0]) + (1 if warmer else -1))} Grad"
            if len(setpoints) == 1 else ""
        )
        verb = "erhöht" if warmer else "gesenkt"
        noun = "die Heizung" if len(heating) == 1 else "die Heizungen"
        return NeedOutcome(
            results=results,
            reason=f"Ich habe {noun} {_where(place)} um ein Grad{target} {verb}.",
        )
    if kind is NeedKind.BRIGHTER:
        lights = [entity for entity in local if entity.domain == "light"]
        if place is None and len({entity.area_id for entity in lights}) > 1:
            return NeedOutcome(message="In welchem Raum soll ich es heller machen?")
        on = [entity for entity in lights if entity.state == "on" and "BRIGHTNESS" in entity.capabilities]
        if on:
            return NeedOutcome(
                results=(_frame(
                    "HassLightBrighten", on, source_text, action=SemanticAction.ADJUST,
                    parameters={"step_percent": _LIGHT_STEP_PERCENT},
                    prop=SemanticProperty.BRIGHTNESS, direction=SemanticDirection.INCREASE,
                ),),
                reason=f"Ich habe {_names(on)} heller gestellt.",
            )
        off = [entity for entity in lights if entity.state != "on"]
        if not off:
            return NeedOutcome(message=f"{_where(place)[:1].upper()}{_where(place)[1:]} gibt es kein Licht, das ich einschalten kann.")
        result = _frame("HassTurnOn", off, source_text, action=SemanticAction.TURN_ON)
        if len(off) > _MAX_DIRECT_LIGHTS:
            return NeedOutcome(results=(result,), confirm=f"Soll ich {_names(off)} einschalten?")
        return NeedOutcome(
            results=(result,),
            reason=f"Ich habe {_where(place)} das Licht eingeschaltet ({_names(off)}).",
        )
    if kind in {NeedKind.DIMMER, NeedKind.GLARE}:
        lights_on = [entity for entity in local if entity.domain == "light" and entity.state == "on"]
        shades = [
            entity for entity in local
            if entity.domain == "cover" and entity_genera(entity) & {"shutter", "awning"}
            and entity.state != "closed"
        ]
        if kind is NeedKind.GLARE or not lights_on:
            if shades:
                result = _frame("HassCloseCover", shades, source_text, action=SemanticAction.CLOSE)
                if kind is NeedKind.GLARE and len(shades) <= 2:
                    return NeedOutcome(
                        results=(result,),
                        reason=f"Ich habe {_names(shades)} heruntergefahren, damit es {_where(place)} nicht mehr blendet.",
                    )
                return NeedOutcome(
                    results=(result,),
                    confirm=f"Soll ich {_names(shades)} herunterfahren?",
                )
            if not lights_on:
                return NeedOutcome(message=f"{_where(place)[:1].upper()}{_where(place)[1:]} ist kein Licht an und ich finde keine Beschattung.")
        if place is None and len({entity.area_id for entity in lights_on}) > 1:
            return NeedOutcome(message="In welchem Raum soll ich das Licht dimmen?")
        dimmable = [entity for entity in lights_on if "BRIGHTNESS" in entity.capabilities]
        if dimmable:
            return NeedOutcome(
                results=(_frame(
                    "HassLightDim", dimmable, source_text, action=SemanticAction.ADJUST,
                    parameters={"step_percent": _LIGHT_STEP_PERCENT},
                    prop=SemanticProperty.BRIGHTNESS, direction=SemanticDirection.DECREASE,
                ),),
                reason=f"Ich habe {_names(dimmable)} gedimmt.",
            )
        return NeedOutcome(
            results=(_frame("HassTurnOff", lights_on, source_text, action=SemanticAction.TURN_OFF),),
            confirm=f"Soll ich {_names(lights_on)} ausschalten?",
        )
    if kind is NeedKind.VENTILATE:
        fans = [entity for entity in local if entity.domain == "fan" and entity.state != "on"]
        windows = [
            entity for entity in local
            if entity.domain == "binary_sensor" and entity.device_class == "window"
            and entity.state == "off"
        ]
        hint = f" Öffne am besten kurz {'das Fenster' if len(windows) == 1 else 'die Fenster'}." if windows else ""
        if place is None:
            return NeedOutcome(message="In welchem Raum soll ich lüften?")
        if fans:
            return NeedOutcome(
                results=(_frame("HassTurnOn", fans, source_text, action=SemanticAction.TURN_ON),),
                reason=f"Ich habe {_names(fans)} eingeschaltet.{hint}",
            )
        return NeedOutcome(
            message=(
                f"{_where(place)[:1].upper()}{_where(place)[1:]} gibt es keinen Lüfter, den ich einschalten kann."
                + (hint or " Lüfte am besten kurz.")
            )
        )
    if kind in {NeedKind.QUIETER, NeedKind.LOUDER}:
        players = [entity for entity in local if entity.domain == "media_player"]
        playing = [entity for entity in players if entity.state == "playing"] or [
            entity for entity in players if entity.state not in {"off", "standby", "unavailable"}
        ]
        if not playing:
            return NeedOutcome(message=f"{_where(place)[:1].upper()}{_where(place)[1:]} läuft gerade nichts, das ich leiser oder lauter stellen kann.")
        if place is None and len(playing) > 1:
            return NeedOutcome(message=f"Welches Gerät meinst du: {_names(playing)}?")
        quieter = kind is NeedKind.QUIETER
        return NeedOutcome(
            results=(_registered(playing, "media_player", "volume_down" if quieter else "volume_up", source_text),),
            reason=f"Ich habe {_names(playing)} {'leiser' if quieter else 'lauter'} gestellt.",
        )
    return NeedOutcome()


def _routine_candidates(
    concept: RoutineConcept, entities: Sequence[EntitySnapshot]
) -> list[EntitySnapshot]:
    found: list[EntitySnapshot] = []
    for entity in entities:
        if entity.domain not in {"scene", "script"}:
            continue
        texts = [entity.friendly_name, *entity.aliases, str(entity.attributes.get("description") or "")]
        haystack = " ".join(normalize_for_compare(text) for text in texts)
        if any(stem in haystack for stem in concept.names):
            found.append(entity)
    return found


def _routine(
    concept: RoutineConcept, entities: Sequence[EntitySnapshot], source_text: str
) -> NeedOutcome:
    candidates = _routine_candidates(concept, entities)
    if not candidates:
        return NeedOutcome(
            message=f"Für „{concept.label}“ finde ich keine passende Szene oder Routine."
        )
    if len(candidates) > 1:
        return NeedOutcome(message=f"Welche Routine meinst du: {_names(candidates)}?")
    entity = candidates[0]
    kind_word = "die Szene" if entity.domain == "scene" else "das Skript"
    intent = "HassActivateScene" if entity.domain == "scene" else "HassRunScript"
    result = _frame(intent, (entity,), source_text, action=SemanticAction.TURN_ON)
    if concept.confirm or entity.domain == "script":
        return NeedOutcome(
            results=(result,),
            confirm=f"Soll ich {kind_word} {entity.friendly_name} starten?",
        )
    return NeedOutcome(
        results=(result,),
        reason=f"Ich habe {kind_word} {entity.friendly_name} gestartet.",
    )


def need_place_phrase(place: Place | None) -> str:
    return dative_location_phrase(place.name) if place is not None else "im Haus"
