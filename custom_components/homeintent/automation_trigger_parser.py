"""AutomationTriggerParser (HomeIntent V5 plan, Teil 2/10, V5.3): turns a
spoken automation-trigger sentence ("wenn das Küchenfenster geöffnet wird",
"um 20 Uhr", "bei Sonnenuntergang") into a ``nlu.automation_model.TriggerModel``.

Top-level, hassil-aware - sibling of ``parsers.py``, not part of ``nlu/``
(which stays hassil-/HA-import-free, see ``nlu/frame.py``'s docstring). Its
own separately-compiled ``Intents`` grammar lives under
``intents/de/automation_trigger/`` (7 YAML files, one per ``TriggerType``),
loaded the same way every other grammar directory is (see
``engine.py``'s ``STATE_QUERY_DIR``/``Intents.from_files()`` pattern) - just
not yet from ``engine.py`` itself: this parser has no caller in
``engine.py``/``conversation.py`` in this wave (the "HA-Schreibzugriff: erst
nach explizitem Go" scope decision - parsing and modeling automation
sentences is in scope, wiring a live turn to actually build one is not).

Reuses existing resolvers/vocabulary throughout (Regel 6, "no parallel
system") rather than inventing new ones:

- ``resolve_area_scored``/``resolve_entity_scored``/``constraint_resolver``
  - the exact same entity/area resolution every other parser uses.
- ``_STATE_NAME_TO_SEMANTIC`` plus ``automation_target_resolver.py`` - State
  Trigger und State Condition share one target-resolution implementation.
- ``WorldModel.device_for_entity()`` - the Device Trigger resolves a spoken
  *entity* name (``devices.py`` deliberately has no spoken-device-name
  resolver, see its own docstring) and looks up its owning device from
  there, never a new name-based device lookup.

Compound-name/two-slot target equivalence (architecture addendum): "wenn das
Küchenfenster geöffnet wird" (a compound proper name) and "wenn das Fenster
in der Küche geöffnet wird" (device_class + area) must produce an
*identical* ``TriggerTarget`` when the name did no disambiguation beyond
domain/device_class/area - see ``automation_target_resolver.build_named_target()``'s constraint
recheck below. ``entity_id`` is only kept set when the spoken name picked
one specific entity out of several otherwise-identical candidates (real
disambiguation, e.g. "die Stehlampe" among several lights in one room).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from hassil import Intents, RangeSlotList, RangeType, WildcardSlotList

from .hassil_compat import recognize_aligned
from .nlu.entity_resolution import ResolutionStatus, resolve_phrase
from .automation_target_resolver import build_device_class_target, build_named_target
from .nlu.automation_model import (
    NumericComparator,
    PresenceEvent,
    SunEvent,
    TriggerModel,
    TriggerTarget,
    TriggerType,
)
from .nlu.lexicon import (
    _ACTION_TEMPORAL_UNIT_SLOT_LIST,
    _COMPARATOR_SLOT_LIST,
    _DAY_PART_HOUR_SLOT_LIST,
    _DEVICE_CLASS_SLOT_LIST,
    _DEVICE_PRESS_TYPE_SLOT_LIST,
    _PRESENCE_EVENT_SLOT_LIST,
    _STATE_SLOT_LIST,
    _STATE_VERB_SLOT_LIST,
    _SUN_EVENT_SLOT_LIST,
    _TIME_OFFSET_DIRECTION_SLOT_LIST,
    _WEEKDAY_SLOT_LIST,
)
from .nlu.parser import ParseContext
from .nlu.semantic_automation import compile_state_predicate
from .parsers import _STATE_NAME_TO_SEMANTIC, _strip_locative_prepositions

AUTOMATION_TRIGGER_DIR = Path(__file__).parent / "intents" / "de" / "automation_trigger"

# Regex pre-selection scaffold for a future ``engine.py::_select_parser()``
# wiring wave (not called from anywhere in this wave, see module docstring) -
# mirrors the trigger-keyword/bare-time-phrase vocabulary the 7 grammars
# above actually use.
import re  # noqa: E402
from types import SimpleNamespace  # noqa: E402

_AUTOMATION_TRIGGER_RE = re.compile(
    r"\b(wenn|sobald|falls|bei sonnenaufgang|bei sonnenuntergang|^um \d)\b", re.IGNORECASE
)


_COPULA_STATE_RE = re.compile(
    r"(?P<head>(?:wenn|sobald|falls)\s+(?:der|die|das)\s+\S+)\s+"
    r"(?P<state>offen|auf|zu|geöffnet|geschlossen|an|aus|eingeschaltet|ausgeschaltet)"
    r"\s+(?:ist|sind)[?.!]*",
    re.IGNORECASE,
)
_CLAUSE_JOIN_RE = re.compile(r",|\b(?:und|oder)\b", re.IGNORECASE)


class AutomationTriggerParser:
    """Wraps the 7 ``intents/de/automation_trigger/*.yaml`` grammars (one
    ``Intents`` object, 7 intents - same "one parser per grammar directory,
    dispatch on intent name" shape ``StateQueryParser`` already established
    for its own multi-intent grammar).

    ``parse()`` returns a bare ``TriggerModel``, not a ``ParseResult``/
    ``SemanticFrame`` - this parser isn't (yet) part of the
    ``IntentParser`` protocol/pipeline ``engine.py`` drives; it produces the
    semantic model directly, for the Condition/Action/Reasoning waves that
    build on top of it later.
    """

    def __init__(self, intents: Intents) -> None:
        self._intents = intents

    def parse(self, text: str, context: ParseContext) -> TriggerModel | None:
        # A wildcard immediately followed by a number can otherwise consume
        # the first digit ("Außentemperatur 28" -> name ending in 2,
        # threshold 8). The terminal verb gives this equality shape an
        # unambiguous, compositional boundary before Hassil is consulted.
        equality = re.fullmatch(
            r"(?:wenn|sobald|falls)\s+(?:der|die|das)?\s*"
            r"(?P<name>.+?)\s+(?P<threshold>\d+(?:[.,]\d+)?)\s*"
            r"(?:Grad|Watt|Prozent|kWh)?\s*(?:beträgt|erreicht|hat)[?.!]*",
            text.strip(),
            re.IGNORECASE,
        )
        if equality is not None:
            target = build_named_target(
                SimpleNamespace(value=equality.group("name")), context
            )
            if target is not None:
                return TriggerModel(
                    type=TriggerType.NUMERIC_STATE,
                    target=target,
                    comparator=NumericComparator.EQUAL,
                    threshold=float(equality.group("threshold").replace(",", ".")),
                )
        # Everyday sun wording. Checked before Hassil because a free name slot
        # would otherwise read "die Sonne aufgeht" as an opening entity.
        colloquial_sun = re.fullmatch(
            r"(?:wenn|sobald|falls)\s+(?:es\s+(?P<light>dunkel|hell)\s+wird|"
            r"die\s+sonne\s+(?P<sun>unter|auf)geht)[?.!]*",
            text.strip(),
            re.IGNORECASE,
        )
        if colloquial_sun is not None:
            sets = (colloquial_sun.group("light") or "").casefold() == "dunkel" or (
                (colloquial_sun.group("sun") or "").casefold() == "unter"
            )
            return TriggerModel(
                type=TriggerType.SUN,
                sun_event=SunEvent.SUNSET if sets else SunEvent.SUNRISE,
            )
        slot_lists = {
            "device_class": _DEVICE_CLASS_SLOT_LIST,
            "area": WildcardSlotList(name="area"),
            "name": WildcardSlotList(name="name"),
            "state": _STATE_SLOT_LIST,
            "state_verb": _STATE_VERB_SLOT_LIST,
            "comparator": _COMPARATOR_SLOT_LIST,
            # words=False (hassil default is True): 0-5000 is far too wide a
            # range to also spell out in words, same combinatorial-explosion
            # concern the plan's own research flagged - existing
            # RangeSlotList calls elsewhere only ever cover small ranges
            # (0-100, 5-30) and never needed this.
            "threshold": RangeSlotList(name="threshold", start=0, stop=5000, step=1, type=RangeType.NUMBER, words=False),
            "presence_event": _PRESENCE_EVENT_SLOT_LIST,
            "device_press_type": _DEVICE_PRESS_TYPE_SLOT_LIST,
            "sun_event": _SUN_EVENT_SLOT_LIST,
            "offset_direction": _TIME_OFFSET_DIRECTION_SLOT_LIST,
            "offset": RangeSlotList(name="offset", start=0, stop=120, step=1, type=RangeType.NUMBER),
            "hour": RangeSlotList(name="hour", start=0, stop=23, step=1, type=RangeType.NUMBER),
            "minute": RangeSlotList(name="minute", start=0, stop=59, step=1, type=RangeType.NUMBER),
            "weekday": _WEEKDAY_SLOT_LIST,
            # V5 Wave 4 (V5.13) - {weekday2} is a second, independent
            # instance of the exact same _WEEKDAY_SLOT_LIST vocabulary, only
            # ever used together with {weekday} in weekday_trigger.yaml's
            # 2-weekday combination sentence ("jeden Montag und Mittwoch") -
            # hassil needs two distinct slot names to fill two separate
            # positions in one sentence with the same underlying vocabulary.
            "weekday2": _WEEKDAY_SLOT_LIST,
            "day_part": _DAY_PART_HOUR_SLOT_LIST,
            # V5 Wave 4 (V5.14, "nachdem"-Delay-Wrapper) - {amount}/
            # {action_temporal_unit} reuse the exact vocabulary
            # automation_action_parser.py's DELAY action already established
            # (Regel 6); {trigger_text} is a WildcardSlotList capturing the
            # remainder after "nachdem", re-parsed via a recursive self.parse()
            # call - same text-rewrite-and-reparse composition Wave 3's WAIT
            # action already established (see _parse_delay_trigger below).
            "amount": RangeSlotList(name="amount", start=1, stop=59, step=1, type=RangeType.NUMBER),
            "action_temporal_unit": _ACTION_TEMPORAL_UNIT_SLOT_LIST,
            "trigger_text": WildcardSlotList(name="trigger_text"),
        }
        result = recognize_aligned(text, self._intents, slot_lists=slot_lists, language="de")
        if result is None or result.intent is None:
            return self._parse_semantic_state_trigger(
                text, context
            ) or self._parse_copula_state_trigger(text, context)

        dispatch = {
            "HassStateTrigger": self._parse_state_trigger,
            "HassNumericStateTrigger": self._parse_numeric_state_trigger,
            "HassNumericEqualTrigger": self._parse_numeric_equal_trigger,
            "HassDeviceTrigger": self._parse_device_trigger,
            "HassPresenceTrigger": self._parse_presence_trigger,
            "HassSunTrigger": self._parse_sun_trigger,
            "HassTimeTrigger": self._parse_time_trigger,
            "HassWeekdayTrigger": self._parse_weekday_trigger,
            "HassDelayTrigger": self._parse_delay_trigger,
        }
        handler = dispatch.get(result.intent.name)
        if handler is None:
            return None
        parsed = handler(result.entities, context)
        return parsed if parsed is not None else self._parse_semantic_state_trigger(text, context)

    def _parse_copula_state_trigger(self, text: str, context: ParseContext) -> TriggerModel | None:
        """"wenn das Badezimmerfenster offen/auf ist" for a *named* entity.

        The predicate form is the same state event as "... geöffnet wird".
        Only a single-predicate clause qualifies: with "und"/"oder"/a comma
        the name wildcard could swallow a second predicate
        ("... geöffnet wird und das Kellerlicht an ist").
        """
        match = _COPULA_STATE_RE.fullmatch(text.strip())
        if match is None or _CLAUSE_JOIN_RE.search(match.group("head")):
            return None
        state = match.group("state").casefold()
        passive = {"auf": "geöffnet", "offen": "geöffnet", "zu": "geschlossen",
                   "an": "eingeschaltet", "aus": "ausgeschaltet"}.get(state, state)
        return self.parse(f"{match.group('head')} {passive} wird", context)

    @staticmethod
    def _parse_semantic_state_trigger(text: str, context: ParseContext) -> TriggerModel | None:
        compiled = compile_state_predicate(text, context)
        if compiled is None:
            return None
        target, state = compiled
        return TriggerModel(type=TriggerType.STATE, target=target, state=state)

    # -- per-intent parsing ---------------------------------------------------

    def _parse_state_trigger(self, slots: dict, context: ParseContext) -> TriggerModel | None:
        state_slot = slots.get("state")
        state_verb_slot = slots.get("state_verb")
        if state_slot is not None:
            semantic_state = _STATE_NAME_TO_SEMANTIC[str(state_slot.value)]
        elif state_verb_slot is not None:
            semantic_state = _STATE_NAME_TO_SEMANTIC[str(state_verb_slot.value)]
        else:
            return None  # structurally unreachable - HassStateTrigger's grammar always captures one of the two

        device_class_slot = slots.get("device_class")
        if device_class_slot is not None:
            target = build_device_class_target(str(device_class_slot.value), slots, context)
        else:
            name_slot = slots.get("name")
            if name_slot is None:
                return None
            target = build_named_target(name_slot, context)
        if target is None:
            return None
        return TriggerModel(type=TriggerType.STATE, target=target, state=semantic_state)

    def _parse_numeric_state_trigger(self, slots: dict, context: ParseContext) -> TriggerModel | None:
        name_slot = slots.get("name")
        comparator_slot = slots.get("comparator")
        threshold_slot = slots.get("threshold")
        if name_slot is None or comparator_slot is None or threshold_slot is None:
            return None

        comparator_value = str(comparator_slot.value)
        if comparator_value == "gt":
            comparator = NumericComparator.ABOVE
        elif comparator_value == "lt":
            comparator = NumericComparator.BELOW
        else:
            # "gte"/"lte" ("mindestens"/"höchstens"/"nicht höher als") still
            # match {comparator} (shared _COMPARATOR_SLOT_LIST, see
            # lexicon.py's comment) but HA's numeric_state trigger has no
            # inclusive comparator to map them onto - refuse, never guess an
            # approximation.
            return None

        target = build_named_target(name_slot, context)
        if target is None:
            return None
        return TriggerModel(
            type=TriggerType.NUMERIC_STATE, target=target, comparator=comparator, threshold=float(threshold_slot.value)
        )

    @staticmethod
    def _parse_numeric_equal_trigger(
        slots: dict, context: ParseContext
    ) -> TriggerModel | None:
        name_slot = slots.get("name")
        threshold_slot = slots.get("threshold")
        if name_slot is None or threshold_slot is None:
            return None
        target = build_named_target(name_slot, context)
        if target is None:
            return None
        return TriggerModel(
            type=TriggerType.NUMERIC_STATE,
            target=target,
            comparator=NumericComparator.EQUAL,
            threshold=float(threshold_slot.value),
        )

    @staticmethod
    def _parse_device_trigger(slots: dict, context: ParseContext) -> TriggerModel | None:
        name_slot = slots.get("name")
        press_slot = slots.get("device_press_type")
        if name_slot is None or press_slot is None:
            return None
        if context.world_model is None:
            return None  # no entity->device mapping available - never guess a device_id

        name = _strip_locative_prepositions(str(name_slot.value))
        resolved = resolve_phrase(name, context.entities, index=context.index)
        if resolved.status is not ResolutionStatus.RESOLVED or resolved.entity is None:
            return None
        device = context.world_model.device_for_entity(resolved.entity.entity_id)
        if device is None:
            return None
        return TriggerModel(type=TriggerType.DEVICE, device_id=device.device_id, device_trigger_type=str(press_slot.value))

    @staticmethod
    def _parse_presence_trigger(slots: dict, context: ParseContext) -> TriggerModel | None:
        name_slot = slots.get("name")
        event_slot = slots.get("presence_event")
        if name_slot is None or event_slot is None:
            return None

        name = _strip_locative_prepositions(str(name_slot.value))
        resolved = resolve_phrase(name, context.entities, domain="person", index=context.index)
        if resolved.status is not ResolutionStatus.RESOLVED or resolved.entity is None:
            return None
        if resolved.entity.domain != "person":
            return None  # named entity resolved, but isn't a person - never guess

        target = TriggerTarget(domain="person", entity_id=resolved.entity.entity_id)
        # Both {presence_event} outcomes (arrive/leave) watch the same "home"
        # zone - lexicon.py's own comment explains why a third, zone-count-
        # based outcome doesn't fit this slot (out of scope this wave).
        event = PresenceEvent.ARRIVE if str(event_slot.value) == "arrive" else PresenceEvent.LEAVE
        return TriggerModel(
            type=TriggerType.PRESENCE, target=target, zone_id="home", presence_event=event
        )

    @staticmethod
    def _parse_sun_trigger(slots: dict, context: ParseContext) -> TriggerModel | None:
        event_slot = slots.get("sun_event")
        if event_slot is None:
            return None
        sun_event = SunEvent.SUNRISE if str(event_slot.value) == "sunrise" else SunEvent.SUNSET

        offset_slot = slots.get("offset")
        direction_slot = slots.get("offset_direction")
        offset_minutes = None
        if offset_slot is not None and direction_slot is not None:
            magnitude = int(offset_slot.value)
            # HA's own `sun` trigger offset convention: negative = before the
            # event, positive = after.
            offset_minutes = -magnitude if str(direction_slot.value) == "before" else magnitude

        return TriggerModel(type=TriggerType.SUN, sun_event=sun_event, offset_minutes=offset_minutes)

    @staticmethod
    def _parse_time_trigger(slots: dict, context: ParseContext) -> TriggerModel | None:
        day_part_slot = slots.get("day_part")
        if day_part_slot is not None:
            # V5 Wave 4 (V5.13) - canonical-hour day-part word ("morgens"/
            # "abends"/...), minute always 0 - see lexicon.py's
            # _DAY_PART_HOUR_SLOT_LIST docstring for the fixed mapping.
            return TriggerModel(type=TriggerType.TIME, time_hour=int(day_part_slot.value), time_minute=0)
        hour_slot = slots.get("hour")
        if hour_slot is None:
            return None
        minute_slot = slots.get("minute")
        minute = int(minute_slot.value) if minute_slot is not None else 0
        return TriggerModel(type=TriggerType.TIME, time_hour=int(hour_slot.value), time_minute=minute)

    @staticmethod
    def _parse_weekday_trigger(slots: dict, context: ParseContext) -> TriggerModel | None:
        weekday_slot = slots.get("weekday")
        if weekday_slot is None:
            return None
        weekdays = list(str(weekday_slot.value).split(","))
        # V5 Wave 4 (V5.16, "jeden Montag und Mittwoch") - {weekday2} is only
        # ever present on the 2-weekday combination sentence; deduplicated
        # and order-normalized (mon..sun) so "Montag und Mittwoch"/"Mittwoch
        # und Montag" produce the identical TriggerModel. Bounded to exactly
        # 2 weekdays per sentence (not arbitrary N) - same "bounded, not
        # fully general" scope decision this project's other closed-
        # vocabulary combinations already make (stated plainly, not hidden).
        weekday2_slot = slots.get("weekday2")
        if weekday2_slot is not None:
            weekdays.extend(str(weekday2_slot.value).split(","))
        canonical_order = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        unique_weekdays = sorted(set(weekdays), key=canonical_order.index)
        return TriggerModel(type=TriggerType.WEEKDAY, weekdays=tuple(unique_weekdays))

    def _parse_delay_trigger(self, slots: dict, context: ParseContext) -> TriggerModel | None:
        """V5 Wave 4 (V5.14, "Relative Time") - "{amount} {unit} nachdem
        <trigger-clause>" rewrites to just "<trigger-clause>" and recurses
        into ``self.parse()`` (same text-rewrite-and-reparse composition
        ``AutomationActionParser._parse_wait`` already established for
        "warte bis <condition>", Regel 6) - never a second trigger grammar.

        ``delay_seconds`` is only meaningful on an event-based trigger (it
        already fires the moment its own event happens; the delay pushes
        the actual automation start later) - TIME/SUN/WEEKDAY triggers are
        refused here (Regel 4, never guess what "10 Minuten nachdem es 20
        Uhr ist" would even mean for a trigger that's already a fixed clock
        time).
        """
        amount_slot = slots.get("amount")
        unit_slot = slots.get("action_temporal_unit")
        trigger_text_slot = slots.get("trigger_text")
        if amount_slot is None or unit_slot is None or trigger_text_slot is None:
            return None
        inner_text = str(trigger_text_slot.value).strip()
        if not inner_text:
            return None
        inner_trigger = self.parse(inner_text, context)
        if inner_trigger is None:
            return None
        if inner_trigger.type not in (
            TriggerType.STATE,
            TriggerType.NUMERIC_STATE,
            TriggerType.PRESENCE,
            TriggerType.DEVICE,
        ):
            return None
        multiplier = {"seconds": 1, "minutes": 60, "hours": 3600}[str(unit_slot.value)]
        delay_seconds = int(amount_slot.value) * multiplier
        return replace(inner_trigger, delay_seconds=delay_seconds)
