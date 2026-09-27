"""Deterministic scope corrections for an already understood command."""

from __future__ import annotations

import re

from .areas import AreaResolveStatus, resolve_area_name
from .entities import EntitySnapshot
from .floors import FloorResolveStatus, resolve_floor_name
from .nlu.context import ConversationContext
from .nlu.entity_resolution import ResolutionStatus, resolve_entity_scored
from .nlu.frame import AreaReference, SemanticFrame, TargetReference
from .nlu.normalize import normalize
from .nlu.parse_outcome import ParseFailureReason, UnderstandingFeedback
from .nlu.parser import ClarificationRequest, ParseResult


_CORRECTION_RE = re.compile(
    r"^(?:nein[,.]?\s*)?(?:"
    r"nicht\s+.+?\s+sondern\s+|"
    r"(?:ich\s+)?meinte\s+|"
    r"statt(?:dessen)?\s+|"
    r"nur\s+(?:(?:in\s+der|in\s+dem|im|am)\s+)?"
    r")(?P<location>.+?)\s*[?.!]*$",
    re.IGNORECASE,
)

_SHORT_NEGATIVE_CORRECTION_RE = re.compile(
    r"^nein[,.]?\s+(?P<location>(?!doch\b|nicht\b).+?)\s*[?.!]*$",
    re.IGNORECASE,
)


_MEANT_TAILS = (("meine", "ich"), ("meinte", "ich"), ("ist", "gemeint"), ("war", "gemeint"))


def _canonical_correction(text: str) -> str:
    """"Nicht X, Y meine ich" / "Y meine ich, nicht X" -> "ich meinte Y".

    Word order variants of one correction meaning: the rejected constituent
    is dropped, the meant one is kept.
    """
    parts = [part.strip(" .!?") for part in text.split(",")]
    if len(parts) != 2:
        return text
    first, second = parts
    first_words = first.split()
    second_words = second.split()

    def meant(words: list[str]) -> list[str] | None:
        for tail in _MEANT_TAILS:
            if tuple(word.casefold() for word in words[-2:]) == tail:
                return words[:-2]
        return None

    if first_words[:1] and first_words[0].casefold() == "nicht":
        kept = meant(second_words)
        if kept is None and second_words[:1] and second_words[0].casefold() == "sondern":
            kept = second_words[1:]
        if kept:
            return "ich meinte " + " ".join(kept)
    if second_words[:1] and second_words[0].casefold() == "nicht":
        kept = meant(first_words)
        if kept:
            return "ich meinte " + " ".join(kept)
    return text


class ConversationCorrectionResolver:
    """Retarget the previous action while retaining its validated meaning."""

    def resolve(
        self,
        text: str,
        entities: list[EntitySnapshot],
        context: ConversationContext | None,
    ) -> ParseResult | ClarificationRequest | UnderstandingFeedback | None:
        if context is None or context.last_command is None:
            return None
        previous = context.last_command
        # Query corrections already have richer delta semantics in
        # QueryFollowupParser and must not be reinterpreted as actions here.
        if previous.intent.startswith("HassGet") or "Query" in previous.intent:
            return None
        if not previous.entities:
            return None

        normalized = _canonical_correction(normalize(text))
        match = _CORRECTION_RE.match(normalized)
        if match is None:
            match = _SHORT_NEGATIVE_CORRECTION_RE.match(normalized)
        if match is None:
            return None
        location = match.group("location").strip()
        location = re.sub(
            r"^nur\s+(?:(?:die|der|das|den|dem)\s+)?",
            "",
            location,
            flags=re.IGNORECASE,
        )
        domain = previous.entities[0].domain
        named = resolve_entity_scored(
            location, [entity for entity in entities if entity.domain == domain]
        )
        if named.status is ResolutionStatus.RESOLVED and named.entity is not None:
            entity = named.entity
            frame = SemanticFrame(
                intent=previous.intent,
                target=TargetReference(
                    text=entity.friendly_name,
                    entity_id=entity.entity_id,
                    domain=entity.domain,
                ),
                area=(
                    AreaReference(text=entity.area_name, area_id=entity.area_id)
                    if entity.area_id is not None and entity.area_name is not None
                    else None
                ),
                parameters=dict(previous.parameters),
                source_text=text,
            )
            return ParseResult(frame=frame, resolved_entities=[entity])
        if named.status is ResolutionStatus.AMBIGUOUS:
            return ClarificationRequest(
                pending_intent=previous.intent,
                pending_target=domain,
                candidates=named.candidates,
                pending_parameters=previous.parameters,
            )
        area = resolve_area_name(location, entities)
        floor_id: str | None = None
        area_id: str | None = None
        area_name: str | None = None
        if area.status is AreaResolveStatus.OK:
            area_id, area_name = area.area_id, location
        elif area.status is AreaResolveStatus.AMBIGUOUS:
            return UnderstandingFeedback(
                ParseFailureReason.AMBIGUOUS_TARGET,
                f"Der Ort {location} ist nicht eindeutig.",
            )
        else:
            floor = resolve_floor_name(location, entities)
            if floor.status is not FloorResolveStatus.OK:
                return UnderstandingFeedback(
                    ParseFailureReason.UNKNOWN_LOCATION,
                    f"Ich kenne keinen Raum und keine Etage namens {location}.",
                )
            floor_id = floor.floor_id

        candidates = [
            entity for entity in entities
            if entity.domain == domain
            and (area_id is None or entity.area_id == area_id)
            and (floor_id is None or entity.floor_id == floor_id)
        ]
        if not candidates:
            return UnderstandingFeedback(
                ParseFailureReason.UNKNOWN_ENTITY,
                f"Dort ist kein passendes Gerät der Art {domain} für Assist freigegeben.",
            )
        preserve_group = (
            previous.source_frame.quantifier is not None
            and previous.source_frame.quantifier.kind == "all"
        )
        if len(candidates) > 1 and not preserve_group:
            return ClarificationRequest(
                pending_intent=previous.intent,
                pending_target=domain,
                candidates=tuple(candidates),
                pending_parameters=previous.parameters,
            )

        if preserve_group:
            parameters = dict(previous.parameters)
            parameters["location_kind"] = "area" if area_id is not None else "floor"
            parameters["location_id"] = area_id if area_id is not None else floor_id
            frame = SemanticFrame(
                intent=previous.intent,
                target=TargetReference(text=domain, domain=domain),
                area=(
                    AreaReference(text=area_name, area_id=area_id)
                    if area_id is not None and area_name is not None
                    else None
                ),
                quantifier=previous.source_frame.quantifier,
                parameters=parameters,
                source_text=text,
            )
            return ParseResult(frame=frame, resolved_entities=candidates)

        entity = candidates[0]
        frame = SemanticFrame(
            intent=previous.intent,
            target=TargetReference(
                text=entity.friendly_name,
                entity_id=entity.entity_id,
                domain=entity.domain,
            ),
            area=(
                AreaReference(text=area_name, area_id=area_id)
                if area_id is not None and area_name is not None
                else None
            ),
            parameters=dict(previous.parameters),
            source_text=text,
        )
        return ParseResult(frame=frame, resolved_entities=[entity])
