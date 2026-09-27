"""AutomationActionParser (HomeIntent V5 plan, Teil 4/10, V5.7-V5.12): turns
a spoken automation-action sentence ("mach das Flurlicht an, fahr die
Rollläden hoch und stelle die Heizung auf 21 Grad") into a tuple of
``nlu.action_model.ActionModel``/``ActionGroup`` steps.

Top-level, hassil-aware - sibling of ``automation_trigger_parser.py``/
``automation_condition_parser.py``, not part of ``nlu/`` (see
``nlu/frame.py``'s docstring). Its own separately-compiled ``Intents``
grammar lives under ``intents/de/automation_action/`` (9 YAML files/9
hassil intents for the 11 ``ActionType`` values - SET_BRIGHTNESS/
SET_POSITION/SET_FAN_SPEED share one grammar intent,
``HassSetPercentageAction``/set_percentage_action.yaml, since their
sentences are structurally identical and only the *resolved target's
domain* actually distinguishes them - see that file's own comment and
``_parse_set_percentage``/``_PERCENTAGE_ACTION_TYPE_BY_DOMAIN`` below). No
caller in ``engine.py``/``conversation.py`` in this wave either - same
"HA-Schreibzugriff: erst nach explizitem Go" scope decision Wave 1/2
already made.

Two layers, deliberately simpler than ``AutomationConditionParser``'s
boolean-expression layer since actions have no AND/OR/NOT nesting to parse -
only sequencing and (optionally) an explicit parallel grouping:

1. A pure-Python chunk-splitting layer (no hassil) - ``_split_chunks()``
   splits the raw text on "," and "und" (both structural, comma- and
   \\b-word-boundary-anchored so "Grund"/"Sekunde" are never mis-split, same
   discipline ``automation_condition_parser.py``'s own ``_tokenize()``
   documents). If the text names "gleichzeitig"/"parallel" anywhere (V5.9),
   *every* chunk in that sentence becomes one ``ActionGroup(PARALLEL, ...)``
   instead of a flat sequential tuple - the plan's own example only ever
   scopes "gleichzeitig" sentence-wide, so no per-chunk parallel/sequential
   mixing is attempted here. A leading "dann"/"danach" per chunk (V5.10's own
   example, "... und schalte dann alle Lichter aus") is stripped as a
   connector word, not captured as data.
2. Leaf parsing via hassil: each chunk goes to ``_parse_leaf()``, the same
   "one ``Intents`` object, one dispatch dict on ``result.intent.name``"
   shape every other parser in this project already established.

Named targets may share one action predicate: "schalte Küchenlicht und
Flurlicht aus" is projected into two independently validated leaves. This
is deliberately narrower than unrestricted cross-clause ellipsis: every
target must be explicitly named, exactly one command marker must govern the
sentence, and every projected action must resolve. Otherwise the complete
sentence is refused, so no partial automation is ever created.

``ActionType.WAIT``'s condition delegates straight to
``AutomationConditionParser`` (composition, not a second condition grammar,
Regel 6): "warte [maximal N Minuten] bis <condition>" rewrites to "wenn
<condition>" and re-runs it through the caller-supplied condition parser
instance, so it must be phrased exactly like the 9 existing condition
grammars already expect (e.g. "warte bis es nach Sonnenuntergang ist", not
the plan's own more casual "warte bis die Sonne untergegangen ist" - noted
here, not hidden, same as every other grammar-phrasing boundary this V5
plan's waves have already documented).

TURN_ON/TURN_OFF boundary decision: reused across every actionable domain
this project models (light/switch/fan/climate on/off *and* cover open/
close) rather than adding separate OPEN/CLOSE action types beyond the 11
V5.7 names - mirrors ``ConditionType.STATE``'s own domain-agnostic
OPEN/CLOSED/ON/OFF conflation (one leaf type, the concrete HA service call
is later HA-generator work, V5.25). "hoch"/"auf" -> TURN_ON, "runter"/"zu"
-> TURN_OFF for covers, alongside "an"/"aus" for the rest.
"""

from __future__ import annotations

import re
from pathlib import Path

from hassil import Intents, RangeSlotList, RangeType, WildcardSlotList

from .hassil_compat import recognize_aligned
from .areas import AreaResolutionStatus, resolve_area_scored
from .automation_condition_parser import AutomationConditionParser
from .entities import EntitySnapshot
from .nlu.entity_resolution import (
    ResolutionStatus,
    resolve_named_target,
)
from .nlu.composition import build_compositional_plan, project_target
from .nlu.action_model import ActionGroup, ActionModel, ActionType, ExecutionMode
from .nlu.automation_model import TriggerTarget
from .nlu.capabilities import Capability
from .nlu.constraint_resolver import Constraints, resolve_candidates
from .nlu.lexicon import (
    _ACTION_TEMPORAL_UNIT_SLOT_LIST,
    _COLOR_SLOT_LIST,
    _COLOR_TEMP_SLOT_LIST,
    _COUNT_SLOT_LIST,
    _QUANTIFIER_SLOT_LIST,
)
from .nlu.semantic_compiler import canonicalize_exclusion_clause
from .nlu.meaning import analyse_turn
from .nlu.parser import ParseContext
from .parsers import _strip_locative_prepositions

AUTOMATION_ACTION_DIR = Path(__file__).parent / "intents" / "de" / "automation_action"

# V5 Wave 5 (V5.18, "Context in Automations") - same closed pronoun set
# automation_trigger_parser.py/automation_condition_parser.py establish
# (Regel 6, not a second vocabulary).
_PRONOUN_WORDS = frozenset({"es"})

# {action_domain} vocabulary for TURN_ON/TURN_OFF/SET_* target resolution -
# a union of _DOMAIN_SLOT_LIST's light/switch/cover/fan and
# _COMPARISON_DOMAIN_SLOT_LIST's climate synonyms (Heizung/Thermostat),
# deliberately its own list (not either of those two) rather than a
# module-level import combining them: mirrors _ACTION_TEMPORAL_UNIT_SLOT_LIST's
# own "don't leak a widened vocabulary into an unrelated existing grammar"
# reasoning in lexicon.py - this project's other consumers of
# _DOMAIN_SLOT_LIST (QuantifierParser/ReferenceParser) must not suddenly
# accept "Heizung" as a domain word, and _COMPARISON_DOMAIN_SLOT_LIST's own
# "Raum"/"Räume" synonym is deliberately excluded here (too generic a word
# to safely target for a write action - a query-only judgment call, not
# reused for actions).
from hassil import TextSlotList  # noqa: E402

_ACTION_DOMAIN_SLOT_LIST = TextSlotList.from_tuples(
    [
        ("Lichter", "light"), ("Licht", "light"), ("Lampen", "light"), ("Lampe", "light"),
        ("Schalter", "switch"), ("Steckdosen", "switch"), ("Steckdose", "switch"),
        ("Rollläden", "cover"), ("Rollladen", "cover"), ("Rolladen", "cover"), ("Rolläden", "cover"),
        ("Rollos", "cover"), ("Rollo", "cover"),
        ("Ventilatoren", "fan"), ("Ventilator", "fan"),
        ("Heizungen", "climate"), ("Heizung", "climate"),
        ("Thermostate", "climate"), ("Thermostat", "climate"),
    ],
    name="action_domain",
)

# \b-anchored, same discipline _STRUCTURAL_TOKEN_RE (automation_condition_parser.py)
# documents - neither side of the embedded "und" substring in "Grund"/
# "Sekunde"/"München" is itself a word boundary, so this never mis-splits them.
_CHUNK_SPLIT_RE = re.compile(r",|\bund\b", re.IGNORECASE)
_PARALLEL_MARKER_RE = re.compile(r"\b(gleichzeitig|parallel)\b", re.IGNORECASE)
_LEADING_CONNECTOR_RE = re.compile(r"^(dann|danach)\b\s*", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_CHOOSE_RE = re.compile(
    r"^wenn\s+(?P<condition>.+?)\s*,?\s+dann\s+(?P<then>.+?)"
    r"(?:\s*,?\s+sonst\s+(?P<else>.+))?$",
    re.I,
)
_TURN_ACTION_DOMAINS = frozenset({
    "light", "switch", "fan", "climate", "humidifier", "input_boolean", "cover",
})


def _split_chunks(text: str) -> tuple[list[str], bool]:
    """Splits ``text`` into independently-parseable clause chunks, and
    reports whether "gleichzeitig"/"parallel" (V5.9) appeared anywhere -
    see the module docstring for why that flag scopes the *whole* sentence
    rather than an individual chunk."""
    is_parallel = bool(_PARALLEL_MARKER_RE.search(text))
    normalized = _WHITESPACE_RE.sub(" ", _PARALLEL_MARKER_RE.sub(" ", text)).strip()
    raw_chunks = [c.strip() for c in _CHUNK_SPLIT_RE.split(normalized) if c.strip()]
    chunks = [_LEADING_CONNECTOR_RE.sub("", c).strip() for c in raw_chunks]
    return [c for c in chunks if c], is_parallel


def _seconds_from_amount_unit(amount: int, unit: str) -> int:
    multiplier = {"seconds": 1, "minutes": 60, "hours": 3600}[unit]
    return amount * multiplier


class AutomationActionParser:
    """Wraps the 11 ``intents/de/automation_action/*.yaml`` grammars (one
    ``Intents`` object, 11 intents) plus the chunk-splitting layer around
    them. ``parse()`` is the sole public entry point - a bare single action
    (no "und"/","/"gleichzeitig") still comes back as a 1-tuple, never a
    separate union type.
    """

    def __init__(self, intents: Intents, condition_parser: AutomationConditionParser, max_depth: int = 3) -> None:
        self._intents = intents
        # WAIT (V5.12) delegates to an already-constructed condition parser
        # instance rather than building its own - composition, not a second
        # condition grammar (Regel 6, see module docstring).
        self._condition_parser = condition_parser
        self._max_depth = max_depth
        self._slot_lists = {
            "action_domain": _ACTION_DOMAIN_SLOT_LIST,
            "area": WildcardSlotList(name="area"),
            "name": WildcardSlotList(name="name"),
            "percent": RangeSlotList(name="percent", start=0, stop=100, step=1, type=RangeType.PERCENTAGE),
            "temperature": RangeSlotList(name="temperature", start=5, stop=30, step=1, type=RangeType.NUMBER),
            "color": _COLOR_SLOT_LIST,
            "color_temp": _COLOR_TEMP_SLOT_LIST,
            "message": WildcardSlotList(name="message"),
            "amount": RangeSlotList(name="amount", start=1, stop=59, step=1, type=RangeType.NUMBER),
            "action_temporal_unit": _ACTION_TEMPORAL_UNIT_SLOT_LIST,
            "condition_text": WildcardSlotList(name="condition_text"),
            # V5 Wave 4 (V5.17, "Natural Language Quantifiers in
            # Automations") - {quantifier}/{count} reuse parsers.py's
            # QuantifierParser vocabulary verbatim (Regel 6); {name} here
            # doubles as the exclusion target ("... außer der Flurlampe"),
            # same WildcardSlotList already used above for the singular
            # named-target sentences - _build_target() tells the two apart
            # by whether {action_domain} is also present (see its own
            # comment).
            "quantifier": _QUANTIFIER_SLOT_LIST,
            "count": _COUNT_SLOT_LIST,
        }
        self._dispatch = {
            "HassTurnOnAction": self._parse_turn_on,
            "HassTurnOffAction": self._parse_turn_off,
            "HassSetPercentageAction": self._parse_set_percentage,
            "HassSetColorAction": self._parse_set_color,
            "HassSetColorTemperatureAction": self._parse_set_color_temperature,
            "HassSetTemperatureAction": self._parse_set_temperature,
            "HassNotifyAction": self._parse_notify,
            "HassDelayAction": self._parse_delay,
            "HassWaitAction": self._parse_wait,
        }

    def parse(self, text: str, context: ParseContext) -> tuple["ActionModel | ActionGroup", ...] | None:
        choose = _CHOOSE_RE.fullmatch(text.strip())
        if choose is not None:
            condition = self._condition_parser.parse(
                "wenn " + choose.group("condition"), context
            )
            then_steps = self.parse(choose.group("then"), context)
            else_text = choose.group("else")
            else_steps = self.parse(else_text, context) if else_text else ()
            if condition is None or not then_steps or (else_text and not else_steps):
                return None
            return (
                ActionModel(
                    type=ActionType.CHOOSE,
                    if_condition=condition,
                    then_steps=then_steps,
                    else_steps=else_steps or (),
                ),
            )
        text = canonicalize_exclusion_clause(text)
        chunks, is_parallel = _split_chunks(text)
        if not chunks:
            return None
        actions: list[ActionModel] = []
        for chunk in chunks:
            action = self._parse_leaf(chunk, context)
            if action is None:
                # One predicate may govern several explicitly named targets:
                # ``Schalte Küchenlicht und Flurlicht aus``.  Projection is
                # allowed only with exactly one command marker and succeeds
                # atomically for every target; genuine multi-step commands
                # continue through the established independent-clause path.
                return self._parse_shared_named_action(text, context, is_parallel)
            actions.append(action)
        if is_parallel:
            return (ActionGroup(mode=ExecutionMode.PARALLEL, steps=tuple(actions)),)
        return tuple(actions)

    def _parse_shared_named_action(
        self, text: str, context: ParseContext, is_parallel: bool
    ) -> tuple["ActionModel | ActionGroup", ...] | None:
        turn = analyse_turn(text)
        plan = build_compositional_plan(
            turn, context.entities, index=context.index
        )
        if plan is None:
            return None
        actions: list[ActionModel] = []
        for selected in plan.targets:
            candidate = project_target(plan, selected)
            action = self._parse_leaf(candidate, context)
            if action is None:
                return None
            target = action.target
            if (
                target is None
                or target.domain != selected.domain
                or target.entity_id is not None and target.entity_id != selected.entity_id
                or target.area_id is not None and target.area_id != selected.area_id
            ):
                return None
            actions.append(action)
        if is_parallel:
            return (ActionGroup(mode=ExecutionMode.PARALLEL, steps=tuple(actions)),)
        return tuple(actions)

    # -- leaf parsing via hassil -----------------------------------------------

    def _parse_leaf(self, text: str, context: ParseContext) -> ActionModel | None:
        result = recognize_aligned(text, self._intents, slot_lists=self._slot_lists, language="de")
        if result is None or result.intent is None:
            return None
        handler = self._dispatch.get(result.intent.name)
        if handler is None:
            return None
        return handler(result.entities, context)

    # -- shared helpers (architectural precedent: AutomationConditionParser) --

    @staticmethod
    def _resolve_area(slots: dict, entities: list[EntitySnapshot]) -> tuple[str | None, str | None] | None:
        area_slot = slots.get("area")
        if area_slot is None:
            return None, None
        area_text = _strip_locative_prepositions(str(area_slot.value))
        if not area_text:
            return None, None
        resolved = resolve_area_scored(area_text, entities)
        if resolved.status is not AreaResolutionStatus.RESOLVED or resolved.area is None:
            return None  # named but not a known/unambiguous room - never guess
        return resolved.area.area_id, resolved.area.name

    def _build_domain_area_target(self, domain_value: str, slots: dict, context: ParseContext) -> TriggerTarget | None:
        resolved_area = self._resolve_area(slots, context.entities)
        if resolved_area is None:
            return None
        area_id, _area_name = resolved_area
        return TriggerTarget(domain=domain_value, area_id=area_id)

    @staticmethod
    def _build_pronoun_target(context: ParseContext) -> TriggerTarget | None:
        """V5 Wave 5 (V5.18) - mirrors AutomationTriggerParser's/
        AutomationConditionParser's own ``_build_pronoun_target``
        byte-for-byte (Regel 6). "es" resolves against
        ``context.last_entities``; refuses on nothing remembered or a mixed
        remembered set."""
        last_entities = context.last_entities
        if not last_entities:
            return None
        if len(last_entities) == 1:
            entity = last_entities[0]
            candidates = resolve_candidates(
                context.entities,
                Constraints(domain=entity.domain, device_class=entity.device_class, area_id=entity.area_id),
            )
            if len(candidates) == 1:
                return TriggerTarget(domain=entity.domain, device_class=entity.device_class, area_id=entity.area_id)
            return TriggerTarget(
                domain=entity.domain,
                device_class=entity.device_class,
                area_id=entity.area_id,
                entity_id=entity.entity_id,
            )
        domains = {e.domain for e in last_entities}
        device_classes = {e.device_class for e in last_entities}
        area_ids = {e.area_id for e in last_entities}
        if len(domains) != 1 or len(device_classes) != 1 or len(area_ids) != 1:
            return None
        return TriggerTarget(domain=next(iter(domains)), device_class=next(iter(device_classes)), area_id=next(iter(area_ids)))

    @staticmethod
    def _build_named_target(name_slot, context: ParseContext) -> TriggerTarget | None:
        name = _strip_locative_prepositions(str(name_slot.value))
        if name.strip().lower() in _PRONOUN_WORDS:
            return AutomationActionParser._build_pronoun_target(context)
        resolved = resolve_named_target(name, context.entities)
        if resolved.status is not ResolutionStatus.RESOLVED or resolved.entity is None:
            return None  # not found or ambiguous - never guess (Regel 4)
        entity = resolved.entity

        # Same semantic-equivalence recheck AutomationTriggerParser/
        # AutomationConditionParser's own _build_named_target documents.
        candidates = resolve_candidates(
            context.entities,
            Constraints(domain=entity.domain, device_class=entity.device_class, area_id=entity.area_id),
        )
        if len(candidates) == 1:
            return TriggerTarget(domain=entity.domain, device_class=entity.device_class, area_id=entity.area_id)
        return TriggerTarget(
            domain=entity.domain,
            device_class=entity.device_class,
            area_id=entity.area_id,
            entity_id=entity.entity_id,
        )

    def _build_target(self, slots: dict, context: ParseContext) -> TriggerTarget | None:
        """{action_domain}+{area} takes precedence over {name} when both are
        structurally present - mirrors AutomationConditionParser's own
        device_class-before-name dispatch order in e.g. _parse_state_condition.

        V5 Wave 4 (V5.17) - {quantifier}/{count}/{name} alongside
        {action_domain} routes to _build_quantified_target() instead of the
        plain domain+area path ({name} here is the exclusion target, "...
        außer der Flurlampe" - only reachable together with
        {action_domain}, see turn_on_action.yaml/turn_off_action.yaml's own
        sentences; a bare {name} with no {action_domain} is still the
        original singular-target sentence, unchanged below).
        """
        domain_slot = slots.get("action_domain")
        if domain_slot is not None:
            if slots.get("quantifier") is not None or slots.get("count") is not None or slots.get("name") is not None:
                return self._build_quantified_target(str(domain_slot.value), slots, context)
            return self._build_domain_area_target(str(domain_slot.value), slots, context)
        name_slot = slots.get("name")
        if name_slot is None:
            return None
        return self._build_named_target(name_slot, context)

    def _build_quantified_target(self, domain_value: str, slots: dict, context: ParseContext) -> TriggerTarget | None:
        """Ports QuantifierParser's exact validation rules (parsers.py,
        Regel 6 - no second quantifier-resolution system): "both" requires
        exactly 2 matches, "count" requires exactly N matches, an exclusion
        name must resolve unambiguously and be part of the filtered match
        set before being removed from it. Matches are only checked at parse
        (authoring) time as a sanity check, same as QuantifierParser's own -
        re-validation against live HA state at execution time is a future
        HA-generator wave's job (V5.25), not this one's (stated plainly, not
        hidden).
        """
        resolved_area = self._resolve_area(slots, context.entities)
        if resolved_area is None:
            return None
        area_id, _area_name = resolved_area

        quantifier_slot = slots.get("quantifier")
        count_slot = slots.get("count")
        quantifier_value: int | None = None
        if quantifier_slot is not None:
            quantifier = str(quantifier_slot.value)
        elif count_slot is not None:
            quantifier = "count"
            quantifier_value = int(count_slot.value)
        else:
            # Exclusion sentences hardcode the literal "alle" instead of
            # using {quantifier} (see turn_on_action.yaml's own comment) -
            # same precedent QuantifierParser's own docstring documents.
            quantifier = "all"

        matches = resolve_candidates(context.entities, Constraints(domain=domain_value, area_id=area_id))

        exclude_entity_ids: tuple[str, ...] = ()
        name_slot = slots.get("name")
        if name_slot is not None:
            exclude_name = _strip_locative_prepositions(str(name_slot.value))
            resolved = resolve_named_target(exclude_name, context.entities)
            if resolved.status is not ResolutionStatus.RESOLVED or resolved.entity is None:
                return None  # exclusion target doesn't resolve unambiguously - never guess
            if resolved.entity not in matches:
                return None  # not part of the filtered match set - refuse, don't silently no-op
            matches = [e for e in matches if e.entity_id != resolved.entity.entity_id]
            exclude_entity_ids = (resolved.entity.entity_id,)

        if not matches:
            return None
        if quantifier == "both" and len(matches) != 2:
            return None  # "beide" requires exactly 2 hits - otherwise not understood
        if quantifier == "count" and len(matches) != quantifier_value:
            return None  # "die drei Lichter" requires exactly 3 hits - same rule, generalized

        return TriggerTarget(
            domain=domain_value,
            area_id=area_id,
            quantifier=quantifier,
            quantifier_count=quantifier_value,
            exclude_entity_ids=exclude_entity_ids,
        )

    # V5 Wave 5 (V5.20, "Capability Validation in Automations") - which
    # Capability a SET_* action requires on every one of its target's
    # concrete entities, same enum/precedent nlu/validator.py's own
    # _PERCENT_CAPABILITY_BY_DOMAIN checks (Regel 6). Deliberately does NOT
    # cover TURN_ON/TURN_OFF: capabilities.derive_capabilities() only ever
    # grants Capability.TURN_ON/TURN_OFF for domain in
    # ("light", "switch", "fan", "climate") - never "cover" - even though
    # this parser's own TURN_ON/TURN_OFF reuses those two ActionTypes for
    # cover open/close (see module docstring, "TURN_ON/TURN_OFF boundary
    # decision"). Gating TURN_ON/TURN_OFF here would reject every legitimate
    # cover automation ("fahr die Rollläden hoch"), so those two ActionTypes
    # are intentionally absent from this map.
    _CAPABILITY_BY_ACTION_TYPE = {
        ActionType.SET_BRIGHTNESS: Capability.BRIGHTNESS,
        ActionType.SET_POSITION: Capability.POSITION,
        ActionType.SET_FAN_SPEED: Capability.FAN_SPEED,
        ActionType.SET_COLOR: Capability.COLOR,
        ActionType.SET_COLOR_TEMPERATURE: Capability.COLOR_TEMPERATURE,
        ActionType.SET_TEMPERATURE: Capability.TEMPERATURE,
    }

    @staticmethod
    def _candidates_for_target(target: TriggerTarget, context: ParseContext) -> list[EntitySnapshot]:
        """The concrete entities ``target`` actually refers to - a single
        already-resolved entity when ``entity_id`` is set (the common case,
        every singular named/pronoun target), otherwise every entity
        matching its domain/device_class/area (quantified "alle Lichter im
        Wohnzimmer" targets), minus any "... außer {name}" exclusions.
        Mirrors ``_build_quantified_target``'s own ``resolve_candidates()``
        call, not a new search system (Regel 6)."""
        if target.entity_id is not None:
            return [e for e in context.entities if e.entity_id == target.entity_id]
        candidates = resolve_candidates(
            context.entities,
            Constraints(
                domain=target.domain,
                device_class=target.device_class,
                area_id=target.area_id,
                floor_id=target.floor_id,
            ),
        )
        if target.exclude_entity_ids:
            candidates = [e for e in candidates if e.entity_id not in target.exclude_entity_ids]
        return candidates

    def _check_capability(self, action_type: ActionType, target: TriggerTarget, context: ParseContext) -> bool:
        """True iff every concrete entity ``target`` resolves to already has
        the ``Capability`` ``action_type`` requires (V5.20) - an empty
        candidate set (e.g. a quantified target with zero matches, already
        refused earlier by ``_build_quantified_target``) is never silently
        treated as "capable"."""
        required = self._CAPABILITY_BY_ACTION_TYPE.get(action_type)
        if required is None:
            return True  # no capability requirement for this action type
        candidates = self._candidates_for_target(target, context)
        return bool(candidates) and all(required.name in e.capabilities for e in candidates)

    @staticmethod
    def _optional_duration_seconds(slots: dict) -> int | None:
        amount_slot = slots.get("amount")
        unit_slot = slots.get("action_temporal_unit")
        if amount_slot is None or unit_slot is None:
            return None
        return _seconds_from_amount_unit(int(amount_slot.value), str(unit_slot.value))

    # -- per-intent leaf parsing ------------------------------------------------

    def _parse_turn_on(self, slots: dict, context: ParseContext) -> ActionModel | None:
        target = self._build_target(slots, context)
        if target is None or target.domain not in _TURN_ACTION_DOMAINS:
            return None
        return ActionModel(type=ActionType.TURN_ON, target=target, duration_seconds=self._optional_duration_seconds(slots))

    def _parse_turn_off(self, slots: dict, context: ParseContext) -> ActionModel | None:
        target = self._build_target(slots, context)
        if target is None or target.domain not in _TURN_ACTION_DOMAINS:
            return None
        return ActionModel(type=ActionType.TURN_OFF, target=target)

    # Domain -> ActionType for the single percent grammar (set_percentage_action.yaml's
    # own comment explains why one grammar covers all three types) - mirrors
    # service_call.py's PERCENT_INTENTS dict verbatim for cover/light, plus fan
    # (not a PERCENT_INTENTS member - that dict is query-engine scope, this is
    # the action engine's own V5.7 vocabulary). switch/climate/anything else
    # has no percent semantics and is refused (Regel 4, never guess).
    _PERCENTAGE_ACTION_TYPE_BY_DOMAIN = {
        "light": ActionType.SET_BRIGHTNESS,
        "cover": ActionType.SET_POSITION,
        "fan": ActionType.SET_FAN_SPEED,
    }

    def _parse_set_percentage(self, slots: dict, context: ParseContext) -> ActionModel | None:
        target = self._build_target(slots, context)
        percent_slot = slots.get("percent")
        if target is None or percent_slot is None:
            return None
        action_type = self._PERCENTAGE_ACTION_TYPE_BY_DOMAIN.get(target.domain or "")
        if action_type is None:
            return None  # domain has no percent semantics - refuse, don't guess (Regel 4)
        if not self._check_capability(action_type, target, context):
            return None  # V5.20 - entity structurally can't do this, refuse rather than build a no-op automation
        return ActionModel(type=action_type, target=target, value=float(percent_slot.value))

    def _parse_set_temperature(self, slots: dict, context: ParseContext) -> ActionModel | None:
        target = self._build_target(slots, context)
        temperature_slot = slots.get("temperature")
        if target is None or temperature_slot is None:
            return None
        if not self._check_capability(ActionType.SET_TEMPERATURE, target, context):
            return None  # V5.20
        return ActionModel(type=ActionType.SET_TEMPERATURE, target=target, value=float(temperature_slot.value))

    def _parse_set_color(self, slots: dict, context: ParseContext) -> ActionModel | None:
        target = self._build_target(slots, context)
        color_slot = slots.get("color")
        if target is None or color_slot is None:
            return None
        if not self._check_capability(ActionType.SET_COLOR, target, context):
            return None  # V5.20
        return ActionModel(type=ActionType.SET_COLOR, target=target, color_name=str(color_slot.value))

    def _parse_set_color_temperature(self, slots: dict, context: ParseContext) -> ActionModel | None:
        target = self._build_target(slots, context)
        color_temp_slot = slots.get("color_temp")
        if target is None or color_temp_slot is None:
            return None
        if not self._check_capability(ActionType.SET_COLOR_TEMPERATURE, target, context):
            return None  # V5.20
        return ActionModel(type=ActionType.SET_COLOR_TEMPERATURE, target=target, color_temp_kelvin=int(color_temp_slot.value))

    @staticmethod
    def _parse_notify(slots: dict, context: ParseContext) -> ActionModel | None:
        message_slot = slots.get("message")
        if message_slot is None:
            return None
        message = str(message_slot.value).strip()
        if not message:
            return None
        return ActionModel(type=ActionType.NOTIFY, message=message)

    def _parse_delay(self, slots: dict, context: ParseContext) -> ActionModel | None:
        delay_seconds = self._optional_duration_seconds(slots)
        if delay_seconds is None:
            return None
        return ActionModel(type=ActionType.DELAY, delay_seconds=delay_seconds)

    def _parse_wait(self, slots: dict, context: ParseContext) -> ActionModel | None:
        condition_text_slot = slots.get("condition_text")
        if condition_text_slot is None:
            return None
        condition_text = str(condition_text_slot.value).strip()
        if not condition_text:
            return None
        # Rewrite "warte [maximal N Einheit] bis <condition>" -> "wenn
        # <condition>" and delegate straight to the shared condition parser
        # (see module docstring) - never a second condition grammar.
        condition_node = self._condition_parser.parse(f"wenn {condition_text}", context)
        if condition_node is None:
            return None
        return ActionModel(
            type=ActionType.WAIT, wait_condition=condition_node, timeout_seconds=self._optional_duration_seconds(slots)
        )
