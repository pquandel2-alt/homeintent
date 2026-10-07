"""7.9.3 A1: a monitoring whose condition is already true when it is set up.

Home Assistant fires a ``state``/``numeric_state`` trigger only on a
*transition*: a battery that is already at 9 % when "irgendeine Batterie
unter 25 Prozent" is set up never crosses 25 % again and the monitoring
stays silent for it.  The same holds for every state or threshold monitor
("Fenster offen", "Leistung über …", "nicht erreichbar", combinations).

``already_met()`` evaluates the *generated* Home Assistant configuration
against the current states exactly like Home Assistant would evaluate it
for each trigger entity (trigger target reached, every condition holding)
and returns the entities for which the condition is already true.  The
preview names them with their value and offers to send the message right
away: "Ja" sets up the monitoring and sends one message about them, "Nein"
only sets it up, "Abbrechen" does nothing.  The message is delivered by the
existing push boundary (``AgentDelivery.async_send_notification``) to the
very notify targets the generated action addresses - no new write path.

Only notification monitors are considered (every action a
``notify.send_message`` or HomeIntent's own bookkeeping).  Event-like
sensors (motion, presence, sound) and transition-only triggers (``from``
without ``to``) are no lasting state and are never "already met".  A
condition this module cannot evaluate (templates) makes it say nothing,
never guess.

Home-Assistant-free and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .entities import EntitySnapshot, normalize_for_compare, spoken_state

__all__ = (
    "AlreadyMet",
    "MetReply",
    "already_met",
    "classify_met_reply",
    "met_message",
    "met_question",
)

# Actions a notification monitor may contain besides its message.
_BOOKKEEPING = frozenset({
    "notify.send_message", "homeintent.record_automation_run", "homeintent.delete_automation",
    "homeintent.enable_automation",
})
_LISTED = 4  # at most this many devices are named one by one
# Sensors that report an event, not a lasting state.
_EVENT_CLASSES = frozenset({"motion", "occupancy", "presence", "sound", "vibration", "tamper", "moving"})
_EVENT_DOMAINS = frozenset({"event", "button", "input_button", "scene", "script", "automation"})

_BINARY_WORDS: dict[str, tuple[str, str]] = {
    "door": ("offen", "geschlossen"), "window": ("offen", "geschlossen"),
    "garage_door": ("offen", "geschlossen"), "opening": ("offen", "geschlossen"),
    "moisture": ("nass", "trocken"), "smoke": ("Rauch erkannt", "kein Rauch"),
    "gas": ("Gas erkannt", "kein Gas"), "battery": ("schwach", "in Ordnung"),
    "problem": ("Problem", "in Ordnung"), "lock": ("entriegelt", "verriegelt"),
    "connectivity": ("verbunden", "getrennt"), "power": ("an", "aus"), "plug": ("an", "aus"),
    "running": ("läuft", "aus"), "light": ("hell", "dunkel"), "cold": ("kalt", "normal"),
    "heat": ("heiß", "normal"),
}


@dataclass(frozen=True)
class MetItem:
    entity_id: str
    name: str
    shown: str  # "14 %", "offen", "nicht erreichbar"


@dataclass(frozen=True)
class AlreadyMet:
    """Entities whose monitored condition is already true right now."""

    items: tuple[MetItem, ...]
    direction: str  # "below" | "above" | "state"
    targets: tuple[str, ...]  # notify entities of the generated action
    title: str = "HomeIntent"

    @property
    def all_names(self) -> str:
        """Every device with its value (the message itself)."""
        parts = [f"{item.name} ({item.shown})" for item in self.items]
        return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " und " + parts[-1]

    @property
    def names(self) -> str:
        """For speech: a whole group is named short."""
        parts = [f"{item.name} ({item.shown})" for item in self.items]
        if len(parts) > _LISTED:
            # A whole group ("alle 24 Lichter aus") is named short.
            return ", ".join(parts[:_LISTED - 1]) + f" und {len(parts) - _LISTED + 1} weitere"
        return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " und " + parts[-1]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _steps(steps: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for step in _as_list(steps):
        if not isinstance(step, dict):
            continue
        if isinstance(step.get("action"), str):
            found.append(step)
        for value in step.values():
            if isinstance(value, (list, dict)):
                found.extend(_steps(value if isinstance(value, list) else [value]))
    return found


def _value(entity: EntitySnapshot, attribute: Any) -> Any:
    if isinstance(attribute, str):
        return entity.attributes.get(attribute)
    return entity.state


def _in_range(number: float | None, item: Mapping[str, Any]) -> bool:
    if number is None:
        return False
    above = _number(item.get("above"))
    below = _number(item.get("below"))
    if "above" in item and (above is None or not number > above):
        return False
    return not ("below" in item and (below is None or not number < below))


class _Unknown(Exception):
    """A condition this module does not evaluate (never guessed)."""


def _holds(condition: Mapping[str, Any], by_id: Mapping[str, EntitySnapshot]) -> bool:
    kind = condition.get("condition")
    if kind == "state":
        wanted = [str(item) for item in _as_list(condition.get("state"))]
        ids = _as_list(condition.get("entity_id"))
        if any(entity_id not in by_id for entity_id in ids):
            raise _Unknown()
        attribute = condition.get("attribute")
        match = any if condition.get("match") == "any" else all
        return match(str(_value(by_id[entity_id], attribute)) in wanted for entity_id in ids)
    if kind == "numeric_state":
        ids = _as_list(condition.get("entity_id"))
        if any(entity_id not in by_id for entity_id in ids):
            raise _Unknown()
        return all(
            _in_range(_number(_value(by_id[entity_id], condition.get("attribute"))), condition)
            for entity_id in ids
        )
    children = [item for item in _as_list(condition.get("conditions")) if isinstance(item, dict)]
    if kind == "and":
        return all(_holds(child, by_id) for child in children)
    if kind == "or":
        return any(_holds(child, by_id) for child in children)
    if kind == "not":
        return not any(_holds(child, by_id) for child in children)
    raise _Unknown()


def _shown(entity: EntitySnapshot, value: Any, numeric: bool) -> str:
    if str(value) in {"unavailable", "unknown"}:
        return "nicht erreichbar" if str(value) == "unavailable" else "unbekannt"
    if numeric:
        number = _number(value)
        text = f"{number:g}".replace(".", ",") if number is not None else str(value)
        return f"{text} {entity.unit or ''}".strip()
    if entity.domain == "binary_sensor" and str(value) in {"on", "off"}:
        words = _BINARY_WORDS.get(entity.device_class or "", ("an", "aus"))
        return words[0] if str(value) == "on" else words[1]
    return spoken_state(str(value))


def _seconds(value: Any) -> float:
    if isinstance(value, Mapping):
        return float(value.get("days", 0)) * 86400 + float(value.get("hours", 0)) * 3600 + float(
            value.get("minutes", 0)) * 60 + float(value.get("seconds", 0))
    if isinstance(value, str) and value.count(":") == 2:
        hours, minutes, seconds = (float(part) for part in value.split(":"))
        return hours * 3600 + minutes * 60 + seconds
    number = _number(value)
    return number if number is not None else 0.0


def already_met(
    config: Mapping[str, Any],
    entities: Sequence[EntitySnapshot],
    held_seconds: Mapping[str, float] | None = None,
) -> AlreadyMet | None:
    """The trigger entities whose monitored condition is already true now.

    ``None`` when nothing is met, when the automation is not a pure
    notification monitor or when a condition cannot be evaluated.

    A trigger with a duration ("2 Tage lang nicht geöffnet", "länger als
    20 Minuten offen") is met only when the entity has held the state that
    long (``held_seconds`` from Home Assistant's ``last_changed``); unknown
    means not met.  Unavailability is the device's state itself: a device
    that is already unreachable is met whatever the debounce time.
    """
    held = held_seconds or {}
    actions = _steps(config.get("actions"))
    notify = [step for step in actions if step.get("action") == "notify.send_message"]
    if not notify or any(step.get("action") not in _BOOKKEEPING for step in actions):
        return None
    targets: list[str] = []
    for step in notify:
        target = step.get("target")
        for entity_id in _as_list(target.get("entity_id") if isinstance(target, dict) else None):
            if isinstance(entity_id, str) and entity_id not in targets:
                targets.append(entity_id)
    if not targets:
        return None
    by_id = {entity.entity_id: entity for entity in entities}
    try:
        conditions_hold = all(
            _holds(item, by_id) for item in _as_list(config.get("conditions")) if isinstance(item, dict)
        )
    except _Unknown:
        return None
    if not conditions_hold:
        return None
    items: list[MetItem] = []
    direction = "state"
    for trigger in _as_list(config.get("triggers")):
        if not isinstance(trigger, dict):
            continue
        kind = trigger.get("trigger")
        if kind not in {"state", "numeric_state"}:
            continue
        if kind == "state" and "to" not in trigger:
            continue  # a transition ("nicht mehr zuhause"), no lasting state
        for entity_id in _as_list(trigger.get("entity_id")):
            entity = by_id.get(entity_id) if isinstance(entity_id, str) else None
            if entity is None or entity.domain in _EVENT_DOMAINS:
                continue
            wanted_states = {str(item) for item in _as_list(trigger.get("to"))}
            if (
                entity.domain == "binary_sensor" and entity.device_class in _EVENT_CLASSES
                and not wanted_states & {"unavailable", "unknown"}
            ):
                continue  # an event, unless the monitor is about the device failing
            value = _value(entity, trigger.get("attribute"))
            needed = _seconds(trigger.get("for")) if "for" in trigger else 0.0
            if needed and not wanted_states & {"unavailable"} and held.get(entity.entity_id, -1.0) < needed:
                continue  # the state has not lasted long enough (or unknown)
            if kind == "numeric_state":
                met = _in_range(_number(value), trigger)
                direction = "below" if "below" in trigger and "above" not in trigger else (
                    "above" if "above" in trigger and "below" not in trigger else "state"
                )
            else:
                wanted = [str(item) for item in _as_list(trigger.get("to"))]
                met = str(value) in wanted
                if "from" in trigger and met:
                    met = False  # a transition from a given state cannot be "already"
            if met and all(item.entity_id != entity.entity_id for item in items):
                items.append(MetItem(
                    entity.entity_id, entity.friendly_name, _shown(entity, value, kind == "numeric_state")
                ))
    if not items:
        return None
    items.sort(key=lambda item: normalize_for_compare(item.name))
    return AlreadyMet(tuple(items), direction, tuple(targets))


def met_question(met: AlreadyMet) -> str:
    """The sentence added to the preview, ending in the three answers."""
    many = len(met.items) > 1
    if met.direction == "below":
        state = "liegen schon darunter" if many else "liegt schon darunter"
    elif met.direction == "above":
        state = "liegen schon darüber" if many else "liegt schon darüber"
    else:
        state = "sind schon jetzt so" if many else "ist schon jetzt so"
    return (
        f"{met.names} {state}; das meldet die Überwachung erst, wenn es sich einmal ändert und wieder eintritt. "
        "Soll ich dir das jetzt gleich schicken? Sag „Ja“ (einrichten und jetzt schicken), "
        "„Nein“ (nur einrichten) oder „Abbrechen“."
    )


def met_message(met: AlreadyMet) -> str:
    """The message sent right away about the already met entities."""
    return f"Schon beim Einrichten der Überwachung erfüllt: {met.all_names}."


class MetReply:
    SEND = "send"
    CREATE = "create"
    CANCEL = "cancel"


_SEND = frozenset({"ja", "ja bitte", "ja schick", "ja schicken", "schick", "schicken", "ja gerne", "gerne",
                   "ja mach", "ja mach das", "mach das", "ok", "okay", "genau", "ja genau", "jawohl", "klar",
                   "ja klar", "passt", "beides", "ja beides", "einrichten und schicken"})
_CREATE = frozenset({"nein", "nein danke", "nee", "nur einrichten", "nur anlegen", "nein nur einrichten",
                     "nein nur anlegen", "einrichten", "anlegen", "nicht schicken", "nein nicht schicken",
                     "nur die überwachung", "nicht"})
_CANCEL = frozenset({"abbrechen", "abbruch", "stopp", "stop", "vergiss es", "doch nicht", "lieber nicht",
                     "gar nicht", "nichts", "lass es", "nein abbrechen", "gar nichts"})


def classify_met_reply(text: str) -> str | None:
    """"Ja" / "Nein" (nur einrichten) / "Abbrechen" - closed vocabulary,
    anything else is unclear (``None``) and asked again."""
    canonical = " ".join(
        "".join(char for char in text.casefold() if char.isalnum() or char.isspace() or char in "äöüß").split()
    )
    for reply, phrases in ((MetReply.CANCEL, _CANCEL), (MetReply.CREATE, _CREATE), (MetReply.SEND, _SEND)):
        if canonical in phrases:
            return reply
    return None
