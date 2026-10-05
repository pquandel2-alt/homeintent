"""Access policy for automation actions (7.9.1 A1): access never opens by itself.

An automation runs later, without anyone answering. Opening an *access*
(garage door, gate, door drive, valve, lock) unattended is the one write a
household cannot undo in time: "Wenn alle weg sind, öffne das Garagentor"
leaves the house open. HomeIntent therefore never writes an automation
whose action would open an access - whatever the trigger is - and offers a
notification instead. Closing stays allowed (with the usual confirmation).

The rule lives here, once. ``automation_validator.validate_automation`` and
``automation_preview.render_automation_preview`` both call
``access_openings``; the conversation controller additionally re-checks it
right before writing. Scripts and scenes are checked through their static
effect graph (``effect_graph.Effect``): a script that opens the garage door
is an opening, too.

Hass-free and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable, Sequence

from ..effect_graph import Effect
from ..entities import EntitySnapshot
from .action_model import (
    ActionGroup,
    ActionModel,
    ActionType,
    NotificationRecipient,
    NotificationRecipientKind,
)
from .automation_model import AutomationModel, TriggerTarget
from .device_ontology import entity_has_genus

__all__ = (
    "ACCESS_COVER_CLASSES",
    "AccessKind",
    "AccessOpening",
    "access_kind",
    "access_noun",
    "access_openings",
    "describe_access_refusal",
    "notice_instead_of_opening",
)

# Cover classes that close an entrance rather than shade a window.
ACCESS_COVER_CLASSES = frozenset({"garage", "gate", "door"})


class AccessKind(Enum):
    GARAGE_DOOR = "garage_door"
    GATE = "gate"
    DOOR = "door"
    VALVE = "valve"
    LOCK = "lock"


_NOUN = {
    AccessKind.GARAGE_DOOR: "das Garagentor",
    AccessKind.GATE: "das Tor",
    AccessKind.DOOR: "die Tür",
    AccessKind.VALVE: "das Ventil",
    AccessKind.LOCK: "das Schloss",
}

# Services (also derived from scene states) that open an access.
_OPENING_SERVICES = {
    "cover": frozenset({
        "open_cover", "set_cover_position", "toggle", "turn_on", "set_state",
        "stop_cover",  # stopping a closing gate leaves it open
    }),
    "valve": frozenset({"open_valve", "set_valve_position", "toggle", "turn_on", "set_state"}),
    "lock": frozenset({"unlock", "open", "toggle", "set_state"}),
}


@dataclass(frozen=True)
class AccessOpening:
    """One access an automation would open: the device and how."""

    entity_id: str
    name: str
    kind: AccessKind
    via: str | None = None  # friendly name of the script/scene, if indirect


def access_kind(entity: EntitySnapshot | None, entity_id: str | None = None) -> AccessKind | None:
    """The access kind of one device, ``None`` for everything else.

    An entity Home Assistant does not expose to HomeIntent is judged by its
    domain alone: a cover of unknown class fails closed as an access.
    """
    if entity is None:
        domain = (entity_id or "").split(".", 1)[0]
        if domain == "valve":
            return AccessKind.VALVE
        if domain == "lock":
            return AccessKind.LOCK
        if domain == "cover":
            return AccessKind.GATE
        return None
    if entity.domain == "lock":
        return AccessKind.LOCK
    if entity.domain == "valve":
        return AccessKind.VALVE
    if entity.domain != "cover":
        return None
    if entity.device_class == "garage":
        return AccessKind.GARAGE_DOOR
    if entity.device_class == "gate":
        return AccessKind.GATE
    if entity.device_class == "door":
        return AccessKind.DOOR
    if entity.device_class is None and entity_has_genus(entity, "garage_door"):
        # A class-less drive named "Garagentor"/"Hoftor": the name decides.
        return AccessKind.GARAGE_DOOR
    return None


def access_noun(kind: AccessKind) -> str:
    return _NOUN[kind]


def _members(target: TriggerTarget, entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    # Function-local: the generator imports the preview, which imports us.
    from .ha_automation_generator import resolve_target_entities

    return resolve_target_entities(target, list(entities))


def _opens(action: ActionModel, entity: EntitySnapshot) -> bool:
    """Whether this leaf action, applied to ``entity``, opens it."""
    if action.type is ActionType.TURN_ON:
        return True
    if action.type is ActionType.SET_POSITION:
        return action.value is None or action.value > 0
    if action.type is ActionType.REGISTERED_SERVICE:
        if entity.domain == "valve" and action.service_name == "set_valve_position":
            position = action.service_data.get("position")
            return not isinstance(position, (int, float)) or position > 0
        return action.service_name in _OPENING_SERVICES.get(entity.domain, frozenset())
    return False


def _leaves(steps: Iterable[ActionModel | ActionGroup]) -> Iterable[ActionModel]:
    for step in steps:
        if isinstance(step, ActionGroup):
            yield from _leaves(step.steps)
            continue
        yield step
        if step.type in {ActionType.CHOOSE, ActionType.REPEAT, ActionType.ESCALATE}:
            yield from _leaves((*step.then_steps, *step.else_steps))


def access_openings(
    actions: Iterable[ActionModel | ActionGroup],
    entities: Sequence[EntitySnapshot],
    effects: Iterable[Effect] = (),
    effect_roots: dict[str, str] | None = None,
) -> tuple[AccessOpening, ...]:
    """Every access the given automation actions would open.

    ``effects`` are the statically known writes of the scripts and scenes
    the actions run (``effect_graph``); ``effect_roots`` maps an effect's
    entity to the friendly name of the script/scene it came from.
    """
    by_id = {entity.entity_id: entity for entity in entities}
    found: dict[str, AccessOpening] = {}
    for action in _leaves(actions):
        if action.target is None:
            continue
        for entity in _members(action.target, entities):
            kind = access_kind(entity)
            if kind is not None and _opens(action, entity):
                found.setdefault(entity.entity_id, AccessOpening(entity.entity_id, entity.friendly_name, kind))
    for effect in effects:
        opening_services = _OPENING_SERVICES.get(effect.domain)
        for entity_id in effect.entity_ids:
            entity = by_id.get(entity_id)
            domain = entity.domain if entity is not None else entity_id.split(".", 1)[0]
            services = _OPENING_SERVICES.get(domain, opening_services or frozenset())
            if effect.service not in services:
                continue
            kind = access_kind(entity, entity_id)
            if kind is None:
                continue
            found.setdefault(entity_id, AccessOpening(
                entity_id,
                entity.friendly_name if entity is not None else entity_id,
                kind,
                via=(effect_roots or {}).get(entity_id),
            ))
    return tuple(found[key] for key in sorted(found))


def describe_access_refusal(openings: Sequence[AccessOpening]) -> str:
    """The honest refusal, naming each access and the reason once."""
    names = [f"„{item.name}“" for item in openings]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " und " + names[-1]
    via = next((item.via for item in openings if item.via), None)
    how = f" (über „{via}“)" if via else ""
    return (
        f"{listed}{how} öffne ich nicht automatisch: Tore, Türen, Ventile und Schlösser "
        "öffnen sich bei mir nur, wenn du es in dem Moment selbst sagst."
    )


def notice_instead_of_opening(
    model: AutomationModel, entities: Sequence[EntitySnapshot]
) -> AutomationModel | None:
    """The offer that replaces a refused opening: the same trigger and
    conditions, but every opening step becomes a push to the speaker
    ("Garagentor jetzt öffnen? Das entscheidest du selbst.").

    ``None`` when nothing would remain to offer (e.g. a script whose
    effects open an access - the script itself cannot be split).
    """
    replaced = False

    def swap(step: ActionModel | ActionGroup) -> ActionModel | ActionGroup:
        nonlocal replaced
        if isinstance(step, ActionGroup):
            return replace(step, steps=tuple(swap(child) for child in step.steps))
        if step.then_steps or step.else_steps:
            step = replace(
                step,
                then_steps=tuple(swap(child) for child in step.then_steps),
                else_steps=tuple(swap(child) for child in step.else_steps),
            )
        openings = access_openings((step,), entities) if step.target is not None else ()
        if not openings or step.type in {ActionType.CHOOSE, ActionType.REPEAT, ActionType.ESCALATE}:
            return step
        replaced = True
        names = [item.name for item in openings]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " und " + names[-1]
        return ActionModel(
            type=ActionType.NOTIFY,
            message=f"{listed} jetzt öffnen? Das entscheidest du selbst.",
            recipient=NotificationRecipient(NotificationRecipientKind.CURRENT_USER),
        )

    actions = tuple(swap(step) for step in model.actions)
    if not replaced or access_openings(actions, entities):
        return None
    # The alias in Home Assistant must not read "öffne das Garagentor".
    return replace(
        model, actions=actions,
        source_text=f"{model.source_text.strip()} (nur Benachrichtigung, kein automatisches Öffnen)",
    )
