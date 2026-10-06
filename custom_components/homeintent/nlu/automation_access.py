"""Access policy for automation actions (7.9.1 A1): access never opens by itself.

7.9.2 A2 (owner decision "Variante C"): a valve that clearly feeds water to
a garden, lawn or bed - an irrigation valve - is not an access. It may open
by itself, but never without its end: an automation that opens it must
close it again within the same automation (``open_ended_irrigation``).
Gas valves, main/supply valves ("Hauptwasserventil", "Zuleitung",
"Haupthahn") and unknown valves without class and without hint stay
accesses, as do gates, doors and locks. ``irrigation_valve`` is that one
rule, used by ``access_kind`` and therefore by validator, preview and the
write path alike.

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
from .device_ontology import entity_has_genus, entity_name_mentions

__all__ = (
    "ACCESS_COVER_CLASSES",
    "AccessKind",
    "AccessOpening",
    "access_kind",
    "access_noun",
    "access_openings",
    "describe_access_refusal",
    "irrigation_openings",
    "irrigation_valve",
    "open_ended_irrigation",
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


# Name/area words that say a *water* valve feeds a garden (data, stems).
_GARDEN_WATER_HINTS = ("garten", "rasen", "beet", "tropf", "bewässer", "bewaesser", "bereg", "spreng", "gieß", "giess")


def _words(*texts: str | None) -> tuple[str, ...]:
    found: list[str] = []
    for text in texts:
        if text:
            found.extend(text.casefold().replace("-", " ").split())
    return tuple(found)


def irrigation_valve(entity: EntitySnapshot | None) -> bool:
    """A valve that clearly feeds garden water - the one valve kind that may
    open by itself (7.9.2 A2). Gas, main/supply valves and unknown valves
    never are."""
    if entity is None or entity.domain != "valve":
        return False
    if entity.device_class == "gas":
        return False
    if entity_name_mentions(entity, "main_valve"):
        return False
    if entity_has_genus(entity, "irrigation"):
        return True
    if entity.device_class != "water":
        return False
    words = _words(entity.friendly_name, *entity.aliases, entity.area_name)
    return any(hint in word for word in words for hint in _GARDEN_WATER_HINTS)


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
        return None if irrigation_valve(entity) else AccessKind.VALVE
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
        f"{listed}{how} öffne ich nicht automatisch: Tore, Türen, Schlösser, Gas- und "
        "Hauptventile öffnen sich bei mir nur, wenn du es in dem Moment selbst sagst."
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


@dataclass(frozen=True)
class IrrigationOpening:
    """An irrigation valve an automation opens, and when it closes again."""

    entity_id: str
    name: str
    closes_after_seconds: int | None  # None: no end in this automation


def _closes(action: ActionModel, entity_id: str, entities: Sequence[EntitySnapshot]) -> bool:
    if action.target is None:
        return False
    if entity_id not in {item.entity_id for item in _members(action.target, entities)}:
        return False
    if action.type is ActionType.TURN_OFF:
        return True
    if action.type is ActionType.REGISTERED_SERVICE:
        if action.service_name == "close_valve":
            return True
        if action.service_name == "set_valve_position":
            return action.service_data.get("position") == 0
    return False


def irrigation_openings(
    actions: Sequence[ActionModel | ActionGroup], entities: Sequence[EntitySnapshot]
) -> tuple[IrrigationOpening, ...]:
    """Every irrigation valve the actions open, with its end (if any).

    The end is either the step's own duration ("für 20 Minuten") or a
    later step of the same automation that closes the same valve.
    """
    leaves = list(_leaves(actions))
    found: dict[str, IrrigationOpening] = {}
    for index, action in enumerate(leaves):
        if action.target is None:
            continue
        for entity in _members(action.target, entities):
            if not irrigation_valve(entity) or not _opens(action, entity):
                continue
            ends = action.duration_seconds
            if ends is None and any(_closes(later, entity.entity_id, entities) for later in leaves[index + 1:]):
                ends = 0  # closed by a later step; when is the sequence's matter
            previous = found.get(entity.entity_id)
            if previous is None or previous.closes_after_seconds is not None:
                found[entity.entity_id] = IrrigationOpening(entity.entity_id, entity.friendly_name, ends)
    return tuple(found[key] for key in sorted(found))


def open_ended_irrigation(
    actions: Sequence[ActionModel | ActionGroup], entities: Sequence[EntitySnapshot]
) -> tuple[IrrigationOpening, ...]:
    """Irrigation valves this automation would open without ever closing
    them - never written (7.9.2 A2: "Wie lange?")."""
    return tuple(item for item in irrigation_openings(actions, entities) if item.closes_after_seconds is None)
