"""Automation Preview (HomeIntent V5 Teil 7/10, V5.23-V5.24): the first
spoken German rendering of an ``AutomationModel`` meant for a human to
actually confirm ("Automation erkannt: Wenn ..., dann ... Soll diese
Automation erstellt werden?") - distinct from ``automation_model.py``'s/
``condition_model.py``'s/``action_model.py``'s ``render_automation_tree()``/
``render_condition_tree()``/``render_action_step()``, which are all
explicitly documented in their own docstrings as debug-only, never a spoken
preview. Follows ``automation_validator.py``'s precedent of a dedicated
module combining all three semantic-model packages (Regel 6, not folded
into any one of them - would reopen the same import-cycle problem those
three modules already avoid via ``TYPE_CHECKING``/function-local imports).

Never invents information the model doesn't actually carry (Regel 4): where
an upstream parser structurally cannot distinguish two outcomes (e.g.
``TriggerType.PRESENCE`` never records arrival vs. departure - see
``automation_trigger_parser.py``'s ``_parse_presence_trigger`` docstring),
this module renders the truthful, direction-neutral phrasing rather than
guessing "Ankunft" or "Verlassen". Every lookup (entity friendly name, area
name, color word) falls back to the raw stored value instead of raising -
same "never crash on an unrecognized value" posture ``service_call.py``'s
own spoken-response helpers already take.
"""

from __future__ import annotations

from dataclasses import replace

from .automation_access import access_openings, describe_access_refusal, irrigation_openings
from .device_ontology import entity_genera, genus
from .semantic_catalog import COLOR_TEMPERATURE_SPOKEN
from ..entities import EntitySnapshot
from .action_model import ActionGroup, ActionModel, ActionType, ExecutionMode
from .automation_model import (
    AutomationModel,
    NumericComparator,
    PresenceEvent,
    SunEvent,
    TriggerModel,
    TriggerTarget,
    TriggerType,
)
from .condition_model import ConditionModel, ConditionNode, ConditionType, LogicalOperator, TimeComparator
from .semantic_state import SemanticState

_STATE_SPOKEN_DE = {
    SemanticState.OPEN: "geöffnet",
    SemanticState.CLOSED: "geschlossen",
    SemanticState.ON: "eingeschaltet",
    SemanticState.OFF: "ausgeschaltet",
    SemanticState.ACTIVE: "aktiv",
    SemanticState.INACTIVE: "inaktiv",
    SemanticState.UNKNOWN: "in unbekanntem Zustand",
}

_DOMAIN_NOUN_DE = {
    "light": "Licht", "switch": "Schalter", "fan": "Ventilator", "cover": "Rollladen",
    "climate": "Heizung", "binary_sensor": "Sensor", "sensor": "Sensor", "person": "Person",
    "media_player": "Medienplayer", "vacuum": "Saugroboter", "humidifier": "Luftbefeuchter",
    "water_heater": "Warmwasserbereiter", "input_boolean": "Helfer",
    "number": "Regler", "input_number": "Zahlenhelfer", "select": "Auswahl",
    "valve": "Ventil", "lawn_mower": "Mähroboter", "scene": "Szene",
    "camera": "Kamera", "notify": "Benachrichtigungsziel",
}
_PLURAL_NOUN_DE = {
    "Licht": "Lichter", "Schalter": "Schalter", "Ventilator": "Ventilatoren",
    "Rollladen": "Rollläden", "Heizung": "Heizungen", "Sensor": "Sensoren",
    "Medienplayer": "Medienplayer", "Ventil": "Ventile", "Fenster": "Fenster",
    "Tür": "Türen", "Gerät": "Geräte", "Steckdose": "Steckdosen",
    "Garagentor": "Garagentore", "Tor": "Tore", "Markise": "Markisen",
    "Jalousie": "Jalousien", "Vorhang": "Vorhänge", "Raffstore": "Raffstores",
}
_DEVICE_CLASS_NOUN_DE = {
    "window": "Fenster", "door": "Tür", "garage_door": "Garagentor", "opening": "Öffnung",
    "motion": "Bewegungsmelder", "occupancy": "Anwesenheitssensor",
    # Cover classes (7.9.1 A1): a garage door is never called "Rollladen".
    "garage": "Garagentor", "gate": "Tor", "awning": "Markise", "shutter": "Rollladen",
    "blind": "Jalousie", "curtain": "Vorhang", "shade": "Rollo",
}
_WEEKDAY_SPOKEN_DE = {
    "mon": "Montag", "tue": "Dienstag", "wed": "Mittwoch", "thu": "Donnerstag",
    "fri": "Freitag", "sat": "Samstag", "sun": "Sonntag",
}
_PRESENCE_RAW_STATE_SPOKEN_DE = {"home": "zuhause", "not_home": "nicht zuhause"}
# Mirrors service_call.py's _COLOR_SPOKEN_DE/_COLOR_TEMP_SPOKEN_DE verbatim
# (Regel 6, same vocabulary) - duplicated rather than imported, same
# nlu/-stays-hass-free layering ``service_call.py`` itself sits outside of.
_COLOR_SPOKEN_DE = {
    "red": "rot", "green": "grün", "blue": "blau", "yellow": "gelb", "orange": "orange",
    "purple": "lila", "white": "weiß", "pink": "pink", "turquoise": "türkis", "cyan": "cyan",
}
_COLOR_TEMP_SPOKEN_DE = COLOR_TEMPERATURE_SPOKEN



def quoted_message(action: ActionModel) -> str:
    """The spoken message; a message filled in at run time is spoken as a
    real example from the house, never with placeholders (7.9.3 A5)."""
    if action.message_template and action.message_template != action.message:
        return f"zum Beispiel „{action.message}“"
    return f"„{action.message}“"

def _entity_lookup(entities: list[EntitySnapshot]) -> dict[str, EntitySnapshot]:
    return {e.entity_id: e for e in entities}


def _area_name_lookup(entities: list[EntitySnapshot]) -> dict[str, str]:
    return {e.area_id: e.area_name for e in entities if e.area_id and e.area_name}


def _format_number(value: float | None) -> str:
    if value is None:
        return "?"
    return str(int(value)) if float(value).is_integer() else str(value)


def _format_delay(seconds: int | None) -> str:
    if seconds is None:
        return "?"
    if seconds % 3600 == 0 and seconds != 0:
        hours = seconds // 3600
        return "1 Stunde" if hours == 1 else f"{hours} Stunden"
    if seconds > 3600 and seconds % 60 == 0:
        hours, minutes = divmod(seconds // 60, 60)
        hour_text = "1 Stunde" if hours == 1 else f"{hours} Stunden"
        minute_text = "1 Minute" if minutes == 1 else f"{minutes} Minuten"
        return f"{hour_text} und {minute_text}"
    if seconds % 60 == 0 and seconds != 0:
        minutes = seconds // 60
        return "1 Minute" if minutes == 1 else f"{minutes} Minuten"
    return f"{seconds} Sekunden"


def _every(seconds: int | None) -> str:
    """"jede Minute", "jede Stunde", "alle 10 Minuten"."""
    from ..notification_language import spoken_duration

    if seconds == 60:
        return "jede Minute"
    if seconds == 3600:
        return "jede Stunde"
    return f"alle {spoken_duration(seconds or 0)}"


def _cover_noun(members: list[EntitySnapshot]) -> str | None:
    """The most specific device kind every member shares, from the
    ontology (device class first, then name), or ``None``."""
    classes = {member.device_class for member in members}
    if len(classes) == 1 and None not in classes:
        named = _DEVICE_CLASS_NOUN_DE.get(next(iter(classes)) or "")
        if named is not None:
            return named
    if not members:
        return None
    shared = frozenset.intersection(*(entity_genera(member) for member in members)) - {"device"}
    label = min(
        (genus(key) for key in shared),
        key=lambda item: (item.parent is None, item.key),
        default=None,
    )
    return label.singular if label is not None else None


def _speak_target(
    target: TriggerTarget | None,
    entity_by_id: dict[str, EntitySnapshot],
    area_name_by_id: dict[str, str],
    *,
    prefer_name: bool = False,
) -> str:
    if target is None:
        return "unbekanntes Gerät"
    if target.entity_id is not None:
        entity = entity_by_id.get(target.entity_id)
        name = entity.friendly_name if entity is not None else target.entity_id
    elif target.entity_ids:
        names = [
            entity_by_id[entity_id].friendly_name
            if entity_id in entity_by_id else entity_id
            for entity_id in target.entity_ids
        ]
        if len(names) > 2:
            members = [entity_by_id[item] for item in target.entity_ids if item in entity_by_id]
            shared = (
                frozenset.intersection(*(entity_genera(member) for member in members)) - {"device"}
                if members else frozenset()
            )
            label = min(
                (genus(key) for key in shared),
                key=lambda item: (item.parent is None, item.key),
                default=None,
            )
            noun = f" {label.plural}" if label is not None else ""
            name = f"alle {len(names)}{noun} ({', '.join(names[:-1])} und {names[-1]})"
        else:
            name = " und ".join(names)
    else:
        noun = (
            _DEVICE_CLASS_NOUN_DE.get(target.device_class)
            if target.device_class is not None
            else None
        )
        if noun is None:
            noun = (
                _DOMAIN_NOUN_DE.get(target.domain, "Gerät")
                if target.domain is not None
                else "Gerät"
            )
        members = [
            entity for entity in entity_by_id.values()
            if (target.domain is None or entity.domain == target.domain)
            and (target.device_class is None or entity.device_class == target.device_class)
            and target.area_id is not None and entity.area_id == target.area_id
        ]
        if target.device_class is None and target.domain == "cover":
            # The domain alone says "Rollladen"; the devices it resolves to
            # say what they are (Garagentor, Markise, Tor) - 7.9.1 A1.
            noun = _cover_noun(members) or _cover_noun([
                entity for entity in entity_by_id.values()
                if entity.domain == "cover" and target.floor_id is not None
                and entity.floor_id == target.floor_id
            ]) or noun
        if (
            prefer_name and target.quantifier is None and len(members) == 1
            and not target.exclude_entity_ids
        ):
            # One concrete device in the room: say its name, not its class.
            return members[0].friendly_name
        if target.quantifier == "all":
            name = f"alle {_PLURAL_NOUN_DE.get(noun, noun)}"
        elif target.quantifier == "both":
            name = f"beide {noun}"
        elif target.quantifier == "count" and target.quantifier_count is not None:
            name = f"die {target.quantifier_count} {noun}"
        else:
            name = noun
        if target.area_id is not None:
            area_name = area_name_by_id.get(target.area_id, target.area_id)
            name = f"{name} im Bereich {area_name}"
        elif target.floor_id is not None:
            floor_name = next(
                (
                    entity.floor_name
                    for entity in entity_by_id.values()
                    if entity.floor_id == target.floor_id and entity.floor_name
                ),
                target.floor_id,
            )
            name = f"{name} auf der Etage {floor_name}"
    if target.exclude_entity_ids:
        excluded = ", ".join(
            entity_by_id[eid].friendly_name if eid in entity_by_id else eid for eid in target.exclude_entity_ids
        )
        name = f"{name} (außer {excluded})"
    return name


def _speak_trigger(
    trigger: TriggerModel, entity_by_id: dict[str, EntitySnapshot], area_name_by_id: dict[str, str]
) -> str:
    if trigger.type is TriggerType.STATE:
        target = _speak_target(trigger.target, entity_by_id, area_name_by_id)
        state = _STATE_SPOKEN_DE.get(trigger.state, "verändert") if trigger.state is not None else "verändert"
        text = f"{target} wird {state}"
    elif trigger.type is TriggerType.NUMERIC_STATE:
        target = _speak_target(trigger.target, entity_by_id, area_name_by_id)
        comparator = {
            NumericComparator.ABOVE: "über",
            NumericComparator.BELOW: "unter",
            NumericComparator.AT_LEAST: "mindestens",
            NumericComparator.AT_MOST: "höchstens",
        }.get(trigger.comparator, "genau") if trigger.comparator is not None else "genau"
        text = f"{target} {comparator} {_format_number(trigger.threshold)} liegt"
    elif trigger.type is TriggerType.DEVICE:
        text = f"das Geräteereignis „{trigger.device_trigger_type}“ eintritt"
    elif trigger.type is TriggerType.PRESENCE:
        # Deliberately direction-neutral - TriggerModel never records
        # arrive/leave (see module docstring), so this must not guess it.
        who = _speak_target(trigger.target, entity_by_id, area_name_by_id) if trigger.target is not None else "jemand"
        if trigger.presence_event is PresenceEvent.ARRIVE:
            text = "du nach Hause kommst" if trigger.presence_of_speaker else f"{who} nach Hause kommt"
        elif trigger.presence_event is PresenceEvent.LEAVE:
            text = "du das Haus verlässt" if trigger.presence_of_speaker else f"{who} das Haus verlässt"
        else:
            text = f"sich der Anwesenheitsstatus von {who} ändert"
    elif trigger.type is TriggerType.SUN:
        event = "Sonnenaufgang" if trigger.sun_event is SunEvent.SUNRISE else "Sonnenuntergang"
        if trigger.offset_minutes:
            direction = "nach" if trigger.offset_minutes > 0 else "vor"
            text = f"es {abs(trigger.offset_minutes)} Minuten {direction} dem {event} ist"
        else:
            text = f"es {event} ist"
    elif trigger.type is TriggerType.TIME:
        clock = f"{trigger.time_hour:02d}:{(trigger.time_minute or 0):02d}"
        if trigger.time_second is not None:
            clock = f"{clock}:{trigger.time_second:02d}"
        text = f"es {clock} Uhr ist"
    elif trigger.type is TriggerType.RELATIVE_TIME:
        text = f"{_format_delay(trigger.relative_offset_seconds)} vergangen sind"
    elif trigger.type is TriggerType.CALENDAR_TIME:
        text = "der bestätigte Kalenderzeitpunkt erreicht ist"
    elif trigger.type is TriggerType.WEEKDAY:
        text = " oder ".join(_WEEKDAY_SPOKEN_DE.get(d, d) for d in trigger.weekdays) + " ist"
    elif trigger.type is TriggerType.CALENDAR:
        calendar = entity_by_id.get(trigger.calendar_entity_id or "")
        name = calendar.friendly_name if calendar is not None else trigger.calendar_entity_id
        event = "beginnt" if trigger.calendar_event == "start" else "endet"
        text = f"ein Termin im Kalender {name} {event}"
        if trigger.offset_minutes:
            direction = "nach" if trigger.offset_minutes > 0 else "vor"
            text = f"es {abs(trigger.offset_minutes)} Minuten {direction} diesem Termin ist"
    else:
        text = "ein unbekannter Auslöser eintritt"
    if trigger.for_seconds:
        text = f"{text} und dieser Zustand {_format_delay(trigger.for_seconds)} bestehen bleibt"
    if trigger.repeat_within_seconds:
        text = (
            f"{text} und innerhalb von {_format_delay(trigger.repeat_within_seconds)} "
            "ein zweites Mal eintritt"
        )
    if trigger.delay_seconds:
        text = f"{text}, {_format_delay(trigger.delay_seconds)} lang gewartet wird"
    return text


def _speak_condition_leaf(
    condition: ConditionModel, entity_by_id: dict[str, EntitySnapshot], area_name_by_id: dict[str, str]
) -> str:
    if condition.type is ConditionType.STATE:
        target = _speak_target(condition.target, entity_by_id, area_name_by_id)
        state = _STATE_SPOKEN_DE.get(condition.state, "verändert") if condition.state is not None else "verändert"
        return f"{target} {state} ist"
    if condition.type is ConditionType.NUMERIC:
        target = _speak_target(condition.target, entity_by_id, area_name_by_id)
        comparator = "über" if condition.comparator is NumericComparator.ABOVE else "unter"
        unit = f" {condition.unit}" if condition.unit else ""
        return f"{target} {comparator} {_format_number(condition.threshold)}{unit} liegt"
    if condition.type is ConditionType.TIME:
        comparator = "vor" if condition.time_comparator is TimeComparator.BEFORE else "nach"
        return f"es {comparator} {condition.time_hour:02d}:{(condition.time_minute or 0):02d} Uhr ist"
    if condition.type is ConditionType.DATE:
        return f"heute der {(condition.date_day or 0):02d}.{(condition.date_month or 0):02d}. ist"
    if condition.type is ConditionType.WEEKDAY:
        return " oder ".join(_WEEKDAY_SPOKEN_DE.get(d, d) for d in condition.weekdays) + " ist"
    if condition.type is ConditionType.SUN:
        event = "Sonnenaufgang" if condition.sun_event is SunEvent.SUNRISE else "Sonnenuntergang"
        comparator = "vor" if condition.sun_comparator is TimeComparator.BEFORE else "nach"
        if condition.offset_minutes:
            return f"es {abs(condition.offset_minutes)} Minuten {comparator} dem {event} ist"
        return f"es {comparator} dem {event} ist"
    if condition.type is ConditionType.PRESENCE:
        if condition.target is not None:
            who = _speak_target(condition.target, entity_by_id, area_name_by_id)
        elif len(condition.person_entity_ids) == 1:
            who = _people_spoken(condition, entity_by_id)
        elif condition.person_entity_ids:
            who = f"jemand von {_people_spoken(condition, entity_by_id)}"
        else:
            who = "jemand"
        raw_state = condition.raw_state or "unbekannt"
        state = _PRESENCE_RAW_STATE_SPOKEN_DE.get(raw_state, raw_state)
        return f"{who} {state} ist"
    if condition.type is ConditionType.DEVICE:
        return f"die Gerätebedingung „{condition.device_condition_type}“ erfüllt ist"
    if condition.type is ConditionType.ENTITY:
        target = _speak_target(condition.target, entity_by_id, area_name_by_id)
        return f"{target} den Zustand „{condition.raw_state}“ hat"
    if condition.type is ConditionType.UNCHANGED_TODAY and condition.target is not None:
        from ..notification_language import describe_unchanged_today

        assert condition.state is not None
        phrase = describe_unchanged_today(
            condition.target, condition.state, None, list(entity_by_id.values())
        )
        if phrase is not None:
            return phrase.subordinate
    if condition.type is ConditionType.CALENDAR_EVENT:
        return f"der Kalendertitel „{condition.raw_state}“ enthält"
    if condition.type is ConditionType.TEMPLATE:
        return "der angegebene Kalenderzeitraum erfüllt ist"
    return "eine unbekannte Bedingung erfüllt ist"


def _people_spoken(condition: ConditionModel, entity_by_id: dict[str, EntitySnapshot]) -> str:
    names = [
        entity_by_id[person].friendly_name if person in entity_by_id else person
        for person in condition.person_entity_ids
    ]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} und {names[-1]}"


def _nobody_home_spoken(condition: ConditionModel, entity_by_id: dict[str, EntitySnapshot]) -> str:
    """"niemand zuhause ist" - naming the people it is about once they are
    bound (``presence_scope``, 7.8.3), so the "Ja" is given knowingly."""
    if not condition.person_entity_ids:
        return "niemand zuhause ist"
    if len(condition.person_entity_ids) == 1:
        return f"{_people_spoken(condition, entity_by_id)} nicht zuhause ist"
    return f"keiner von {_people_spoken(condition, entity_by_id)} zuhause ist"


def _bound_nobody_home(model: AutomationModel) -> ConditionModel | None:
    for node in model.conditions:
        if node.operator is LogicalOperator.NOT and len(node.children) == 1:
            leaf = node.children[0].condition
            if (
                leaf is not None and leaf.type is ConditionType.PRESENCE
                and leaf.target is None and leaf.raw_state == "home" and leaf.person_entity_ids
            ):
                return leaf
    return None


def _speak_condition_node(
    node: ConditionNode, entity_by_id: dict[str, EntitySnapshot], area_name_by_id: dict[str, str]
) -> str:
    if node.operator is None:
        assert node.condition is not None
        return _speak_condition_leaf(node.condition, entity_by_id, area_name_by_id)
    if node.operator is LogicalOperator.NOT:
        child = node.children[0]
        if (
            child.operator is None
            and child.condition is not None
            and child.condition.type is ConditionType.PRESENCE
            and child.condition.target is None
            and child.condition.raw_state == "home"
        ):
            # AutomationConditionParser's own lexical-negation special case
            # ("niemand [mehr] zuhause ist" - see its docstring) - rendered
            # back the same idiomatic way, not "nicht (jemand zuhause ist)".
            return _nobody_home_spoken(child.condition, entity_by_id)
        return f"nicht ({_speak_condition_node(child, entity_by_id, area_name_by_id)})"
    if node.operator is LogicalOperator.OR:
        existential = _speak_existential(node, entity_by_id)
        if existential is not None:
            return existential
    joiner = " und " if node.operator is LogicalOperator.AND else " oder "
    return joiner.join(f"({_speak_condition_node(c, entity_by_id, area_name_by_id)})" for c in node.children)


def _speak_existential(node: ConditionNode, entity_by_id: dict[str, EntitySnapshot]) -> str | None:
    """"ein Fenster offen ist" - an OR of one state per device (the reading
    of "ein/irgendein X", 7.8.3) is spoken as the phrase it came from."""
    # Function-local: these modules live outside nlu/ and import it.
    from ..automation_grounding import target_for
    from ..notification_language import describe_holding_state
    from .automation_model import TriggerModel, TriggerType

    leaves = [child.condition for child in node.children if child.operator is None]
    if len(leaves) != len(node.children) or len(leaves) < 2:
        return None
    from .ha_automation_generator import resolve_target_entities

    states = {leaf.state for leaf in leaves if leaf is not None and leaf.type is ConditionType.STATE}
    everything = list(entity_by_id.values())
    members: list[EntitySnapshot] = []
    for leaf in leaves:
        resolved = (
            resolve_target_entities(leaf.target, everything)
            if leaf is not None and leaf.target is not None else []
        )
        if len(resolved) != 1:
            return None
        members.append(resolved[0])
    if len(states) != 1:
        return None
    state = next(iter(states))
    phrase = describe_holding_state(
        TriggerModel(TriggerType.STATE, target=target_for(members, everything), state=state),
        everything,
    )
    return phrase.subordinate if phrase is not None else None


_OPEN_CLOSE_DOMAINS = frozenset({"cover"})


def _action_domain(action: ActionModel, entity_by_id: dict[str, EntitySnapshot]) -> str | None:
    target = action.target
    if target is None:
        return None
    if target.domain is not None:
        return target.domain
    ids = (target.entity_id,) if target.entity_id is not None else target.entity_ids
    domains = {entity_by_id[item].domain for item in ids if item in entity_by_id}
    return next(iter(domains)) if len(domains) == 1 else None


def _speak_action_leaf(
    action: ActionModel, entity_by_id: dict[str, EntitySnapshot], area_name_by_id: dict[str, str]
) -> str:
    target = _speak_target(action.target, entity_by_id, area_name_by_id) if action.target is not None else None
    domain = _action_domain(action, entity_by_id)
    if action.duration_seconds:
        # 7.9.2 A2: the end is spoken as part of the action.
        from .action_duration import inverse_of

        inverse = inverse_of(replace(action, duration_seconds=None))
        start = _speak_action_leaf(replace(action, duration_seconds=None), entity_by_id, area_name_by_id)
        end = _speak_action_leaf(inverse, entity_by_id, area_name_by_id) if inverse is not None else ""
        end_verb = end.rsplit(" ", 1)[-1] if end else "beenden"
        return f"{start} und nach {_format_delay(action.duration_seconds)} wieder {end_verb}"
    if action.type is ActionType.TURN_ON and domain in _OPEN_CLOSE_DOMAINS:
        return f"{target} öffnen"
    if action.type is ActionType.TURN_OFF and domain in _OPEN_CLOSE_DOMAINS:
        return f"{target} schließen"
    if action.type is ActionType.TURN_ON:
        return f"{target} einschalten"
    if action.type is ActionType.TURN_OFF:
        return f"{target} ausschalten"
    if action.type is ActionType.SET_BRIGHTNESS:
        return f"{target} auf {_format_number(action.value)} Prozent Helligkeit stellen"
    if action.type is ActionType.SET_COLOR:
        color_name = action.color_name or "unbekannt"
        color = _COLOR_SPOKEN_DE.get(color_name, color_name)
        return f"{target} auf die Farbe {color} stellen"
    if action.type is ActionType.SET_COLOR_TEMPERATURE:
        kelvin = action.color_temp_kelvin or 0
        temp = _COLOR_TEMP_SPOKEN_DE.get(kelvin, f"{kelvin} Kelvin")
        return f"{target} auf {temp} stellen"
    if action.type is ActionType.SET_POSITION:
        return f"{target} auf {_format_number(action.value)} Prozent Position fahren"
    if action.type is ActionType.SET_TEMPERATURE:
        return f"{target} auf {_format_number(action.value)} Grad stellen"
    if action.type is ActionType.SET_FAN_SPEED:
        return f"{target} auf {_format_number(action.value)} Prozent Stufe stellen"
    if action.type is ActionType.REGISTERED_SERVICE:
        from .automation_operations import describe_registered_operation

        described = describe_registered_operation(action.service_domain, action.service_name, action.service_data)
        if action.service_domain == "valve" and action.service_name in {"open_valve", "close_valve"}:
            # "Bewässerung Garten öffnen", not "bei Ventil im Bereich Garten
            # das Ventil öffnen" (7.9.2 A2).
            named = _speak_target(action.target, entity_by_id, area_name_by_id, prefer_name=True)
            return f"{named} {'öffnen' if action.service_name == 'open_valve' else 'schließen'}"
        if " " not in described:
            # A plain verb names the one device: "Küchenradio einschalten".
            named = _speak_target(action.target, entity_by_id, area_name_by_id, prefer_name=True)
            return f"{named} {described}"
        return f"bei {target} {described}"
    if action.type is ActionType.NOTIFY:
        if action.recipient is not None and action.recipient.label:
            return (
                f"eine Push-Benachrichtigung an „{action.recipient.label}“ senden: "
                f"{quoted_message(action)}"
            )
        return f"eine Benachrichtigung senden: {quoted_message(action)}"
    if action.type is ActionType.DELAY:
        return f"{_format_delay(action.delay_seconds)} warten"
    if action.type is ActionType.WAIT:
        condition_text = (
            _speak_condition_node(action.wait_condition, entity_by_id, area_name_by_id)
            if action.wait_condition is not None
            else "eine unbekannte Bedingung erfüllt ist"
        )
        text = f"warten, bis {condition_text}"
        if action.timeout_seconds:
            text = f"{text} (maximal {_format_delay(action.timeout_seconds)})"
        return text
    return "eine unbekannte Aktion ausführen"


def _speak_action_step(
    step: "ActionModel | ActionGroup", entity_by_id: dict[str, EntitySnapshot], area_name_by_id: dict[str, str]
) -> str:
    if isinstance(step, ActionGroup):
        parts = [_speak_action_step(child, entity_by_id, area_name_by_id) for child in step.steps]
        if step.mode is ExecutionMode.PARALLEL:
            return "gleichzeitig " + " und ".join(parts)
        return ", dann ".join(parts)
    return _speak_action_leaf(step, entity_by_id, area_name_by_id)


def _is_notify(step: object) -> bool:
    return (
        isinstance(step, ActionModel) and step.type is ActionType.NOTIFY and step.recipient is not None
    )


def _notification_actions(model: AutomationModel) -> tuple[ActionModel, ...] | None:
    """The first notifications, if the automation does nothing but addressed
    notifications - also repeated or escalated ones (7.9 W5)."""
    leaves: list[ActionModel] = []
    for step in model.actions:
        if not isinstance(step, ActionModel):
            return None
        if _is_notify(step):
            leaves.append(step)
        elif step.type is ActionType.REPEAT and step.then_steps and all(_is_notify(s) for s in step.then_steps):
            leaves.extend(s for s in step.then_steps if isinstance(s, ActionModel))
        elif step.type is ActionType.ESCALATE and step.then_steps and all(_is_notify(s) for s in step.then_steps):
            continue
        else:
            return None
    return tuple(leaves) if leaves else None


def _recipient_phrase(action: ActionModel) -> str:
    from .action_model import NotificationRecipientKind

    recipient = action.recipient
    assert recipient is not None
    if recipient.kind is NotificationRecipientKind.CURRENT_USER:
        return "dir" + (f" an dein Gerät „{recipient.label}“" if recipient.label else "")
    if recipient.kind is NotificationRecipientKind.HOUSEHOLD:
        return "euch"
    return f"an „{recipient.label or 'das gewählte Gerät'}“"


def _speak_follow_ups(model: AutomationModel, entities: list[EntitySnapshot]) -> str:
    """Repetition and escalation, said exactly (7.9 W5): how often, how long,
    up to which bound, and who gets the second message."""
    from ..notification_language import describe_holding_state, spoken_duration
    from .automation_model import TriggerModel, TriggerType

    def holding(node: ConditionNode | None) -> str:
        leaf = node.condition if node is not None else None
        if leaf is None or leaf.target is None or leaf.state is None:
            return "der Zustand anhält"
        phrase = describe_holding_state(
            TriggerModel(TriggerType.STATE, target=leaf.target, state=leaf.state), entities
        )
        return phrase.subordinate if phrase is not None else "der Zustand anhält"

    complement = {
        SemanticState.ON: SemanticState.OFF, SemanticState.OFF: SemanticState.ON,
        SemanticState.OPEN: SemanticState.CLOSED, SemanticState.CLOSED: SemanticState.OPEN,
    }
    parts: list[str] = []
    for step in model.actions:
        if not isinstance(step, ActionModel):
            continue
        if step.type is ActionType.REPEAT and step.delay_seconds and step.max_repeats:
            total = spoken_duration(step.delay_seconds * step.max_repeats)
            parts.append(
                f"Danach wiederhole ich sie {_every(step.delay_seconds)}, solange "
                f"{holding(step.if_condition)} – höchstens {step.max_repeats}-mal, also längstens {total}."
            )
        if step.type is ActionType.ESCALATE and step.timeout_seconds:
            leaf = step.wait_condition.condition if step.wait_condition is not None else None
            still = (
                replace(step.wait_condition, condition=replace(leaf, state=complement[leaf.state]))
                if step.wait_condition is not None and leaf is not None and leaf.state in complement
                else None
            )
            situation = holding(still)
            words = situation.rsplit(" ", 2)
            question = (
                f"Ist {words[0]} nach {spoken_duration(step.timeout_seconds)} immer noch {words[1]}"
                if len(words) == 3 and words[2] == "ist"
                else f"Wenn nach {spoken_duration(step.timeout_seconds)} immer noch {situation}"
            )
            for second in step.then_steps:
                if not isinstance(second, ActionModel) or second.recipient is None:
                    continue
                parts.append(
                    f"{question}, sende ich eine Push-Benachrichtigung {_recipient_phrase(second)}: "
                    f"{quoted_message(second)}"
                )
    kinds = {step.type for step in model.actions if isinstance(step, ActionModel)}
    if parts:
        # A running repetition or wait lives in Home Assistant's memory.
        what = " und ".join(
            word for kind, word in (
                (ActionType.REPEAT, "die Wiederholung"), (ActionType.ESCALATE, "das Warten"),
            ) if kind in kinds
        )
        parts.append(f"Startet Home Assistant währenddessen neu, bricht {what} ab.")
    return " ".join(parts)


def _render_notification_preview(
    model: AutomationModel,
    actions: tuple[ActionModel, ...],
    entities: list[EntitySnapshot],
    entity_by_id: dict[str, EntitySnapshot],
    area_name_by_id: dict[str, str],
) -> str:
    """Natural preview for push-only automations - no service or entity ids.

    "Wenn im Wohnzimmer ein Fenster geöffnet wird, sende ich dir eine
    Push-Benachrichtigung an dein Gerät „iPhone“: „...“ Soll ich das so
    einrichten?"
    """
    # Function-local: notification_language lives outside nlu/ and imports it.
    from ..notification_language import TEST_NOTIFICATION_MESSAGE, describe_event
    from .action_model import NotificationRecipientKind

    clauses: list[str] = []
    ends_with_message = False
    for action in actions:
        recipient = action.recipient
        assert recipient is not None
        test = action.message == TEST_NOTIFICATION_MESSAGE
        reminder = (action.message or "").startswith("Erinnerung")
        noun = (
            "eine Testbenachrichtigung" if test
            else "eine Erinnerung" if reminder
            else "eine Push-Benachrichtigung"
        )
        if recipient.kind is NotificationRecipientKind.CURRENT_USER:
            text = f"sende ich dir {noun}"
            if recipient.label:
                text += f" an dein Gerät „{recipient.label}“"
        elif recipient.kind is NotificationRecipientKind.HOUSEHOLD:
            text = f"sende ich euch {noun}"
        else:
            text = f"sende ich {noun} an „{recipient.label or 'das gewählte Gerät'}“"
        ends_with_message = (
            not test and bool(action.message) and action.message[-1] in ".!?"
        )
        if not test and action.message:
            text += f": {quoted_message(action)}"
        clauses.append(text)
    action_text = " und ".join(clauses)

    trigger = model.triggers[0] if len(model.triggers) == 1 else None
    one_shot_time = trigger is not None and trigger.type in (
        TriggerType.RELATIVE_TIME, TriggerType.CALENDAR_TIME
    )
    if model.calendar_schedule is not None:
        sentence = f"Zum Zeitpunkt „{model.calendar_schedule.spoken}“ {action_text}"
    elif trigger is not None and trigger.type is TriggerType.RELATIVE_TIME:
        sentence = f"In {_format_delay(trigger.relative_offset_seconds)} {action_text}"
    elif model.situation is not None:
        # One combined situation, whichever part of it begins last (7.8.3).
        extra_conditions = [
            _speak_condition_node(c, entity_by_id, area_name_by_id)
            for c in model.conditions
            if _is_time_window(c)
        ]
        situation = model.situation
        nobody = _bound_nobody_home(model)
        if nobody is not None:
            situation = situation.replace(
                "niemand zuhause ist", _nobody_home_spoken(nobody, entity_by_id)
            )
        when = situation + "".join(f" und {text}" for text in extra_conditions)
        # Several parts: whichever begins last.  A whole set ("alle Fenster
        # zu", 7.9 W1) is one part with one trigger.
        sentence = (
            f"Sobald {when}, egal was davon zuletzt eintritt, {action_text}"
            if model.situation_parts > 1
            else f"Sobald {when}, {action_text}"
        )
    else:
        described = [
            describe_event(item, entities) for item in model.triggers
        ]
        trigger_text = " oder ".join(
            phrase.subordinate if phrase is not None
            else _speak_trigger(item, entity_by_id, area_name_by_id)
            for item, phrase in zip(model.triggers, described)
        )
        if model.conditions:
            trigger_text += " und " + " und ".join(
                _speak_condition_node(c, entity_by_id, area_name_by_id) for c in model.conditions
            )
        sentence = f"Wenn {trigger_text}, {action_text}"
    if not (ends_with_message and sentence.endswith("“")):
        sentence += "."  # a quoted message already carries its own full stop
    follow_ups = _speak_follow_ups(model, entities)
    if follow_ups:
        sentence += f" {follow_ups}"
    if model.once and not one_shot_time:
        sentence += " Diese Automation wird nach der ersten Ausführung automatisch gelöscht."
    elif model.max_runs == 1:
        sentence += " Diese Automation läuft nur einmal und wird danach automatisch gelöscht."
    elif model.max_runs is not None:
        sentence += (
            f" Diese Automation wird nach {model.max_runs} Ausführungen automatisch gelöscht."
        )
    if model.quiet_start_hour is not None:
        sentence += (
            f" Fällt der Zeitpunkt in die Ruhezeit von {model.quiet_start_hour:02d}:00 "
            f"bis {model.quiet_end_hour:02d}:00 Uhr, wird die Erinnerung auf deren Ende verschoben."
        )
    for note in model.notes:
        sentence += f" {note}"
    return f"{sentence} Soll ich das so einrichten?"


def _is_time_window(node: "ConditionNode") -> bool:
    from .condition_model import ConditionType

    leaves = [node.condition] if node.condition is not None else [
        child.condition for child in node.children
    ]
    return bool(leaves) and all(
        leaf is not None and leaf.type in {ConditionType.TIME, ConditionType.WEEKDAY}
        for leaf in leaves
    )


def _speak_measured_trigger(trigger: TriggerModel, entities: list[EntitySnapshot]) -> str | None:
    """Natural wording for attribute measurements and inclusive bounds.

    Classic numeric state triggers keep their established rendering.
    """
    if trigger.type is not TriggerType.NUMERIC_STATE or (
        trigger.measurement is None
        and trigger.comparator not in {NumericComparator.AT_LEAST, NumericComparator.AT_MOST}
        # A sensor is named with its unit (7.9.2 A6), never "Sensor im Bereich …".
        and (trigger.target is None or trigger.target.domain not in {"sensor", None})
    ):
        return None
    from ..notification_language import describe_event

    described = describe_event(trigger, entities)
    return described.subordinate if described is not None else None


def render_automation_preview(model: AutomationModel, entities: list[EntitySnapshot]) -> str:
    """The V5.23/V5.24 spoken Dry-Run/Preview text: "Automation erkannt:
    Wenn ..., [und ...,] dann ... Soll diese Automation erstellt werden?" -
    the exact sentence ``conversation.py`` speaks before storing a
    ``pending_automation_confirmation`` and waiting for the user's reply
    (V5.23). Callers only ever pass an already-``validate_automation()``-
    clean ``model`` (the confirmation flow never offers to create an
    automation ``validate_automation()`` rejected) - this function itself
    does not re-validate, purely renders whatever it is given, same
    division of labor ``render_automation_tree()`` already has.

    ``entities`` is the same live entity list the caller already resolved
    the sentence against - used only to look up friendly/area names for
    already-resolved ``entity_id``s, never to re-resolve or guess anything
    new.
    """
    openings = access_openings(model.actions, entities)
    if openings:
        # Same rule as the validator (7.9.1 A1): never offered for a "Ja".
        return f"{describe_access_refusal(openings)} Ich habe nichts angelegt."
    entity_by_id = _entity_lookup(entities)
    area_name_by_id = _area_name_lookup(entities)
    notification_actions = _notification_actions(model)
    if notification_actions is not None:
        return _render_notification_preview(
            model, notification_actions, entities, entity_by_id, area_name_by_id
        )
    if model.calendar_schedule is not None:
        trigger_text = f"der Zeitpunkt „{model.calendar_schedule.spoken}“ erreicht ist"
    else:
        # Home Assistant fires on any one of several triggers.
        trigger_text = " oder ".join(
            _speak_measured_trigger(t, entities)
            or _speak_trigger(t, entity_by_id, area_name_by_id)
            for t in model.triggers
        )
    action_text = ", dann ".join(_speak_action_step(a, entity_by_id, area_name_by_id) for a in model.actions)
    sentence = f"Wenn {trigger_text}"
    if model.conditions:
        condition_text = " und ".join(_speak_condition_node(c, entity_by_id, area_name_by_id) for c in model.conditions)
        sentence = f"{sentence} und {condition_text}"
    sentence = f"{sentence}, dann {action_text}."
    if model.once:
        sentence = f"{sentence} Diese Automation wird nach der ersten Ausführung automatisch gelöscht."
    elif model.max_runs == 1:
        sentence = f"{sentence} Diese Automation läuft nur einmal und wird danach automatisch gelöscht."
    elif model.max_runs is not None:
        sentence = (
            f"{sentence} Diese Automation wird nach {model.max_runs} "
            "Ausführungen automatisch gelöscht."
        )
    if model.quiet_start_hour is not None:
        sentence = (
            f"{sentence} Fällt der Zeitpunkt in die Ruhezeit von "
            f"{model.quiet_start_hour:02d}:00 bis {model.quiet_end_hour:02d}:00 Uhr, "
            "wird die Erinnerung auf deren Ende verschoben."
        )
    for note in model.notes:
        sentence = f"{sentence} {note}"
    for watering in irrigation_openings(model.actions, entities):
        # 7.9.2 A2: an automatically opening valve is said explicitly.
        if watering.closes_after_seconds is None:
            when = "und schließt nicht von selbst"
        elif watering.closes_after_seconds:
            when = f"und schließt nach {_format_delay(watering.closes_after_seconds)} wieder"
        else:
            when = "und wird im selben Ablauf wieder geschlossen"
        sentence = f"{sentence} Achtung: „{watering.name}“ öffnet sich dabei automatisch {when}."
    return f"Automation erkannt: {sentence} Soll diese Automation erstellt werden?"
