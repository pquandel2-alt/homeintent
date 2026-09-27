"""Intent -> Home Assistant service-call mapping.

Domain/service pairs mirror the already-verified control logic in
xiaozhi_entity_mcp's tool handlers (turn_on -> homeassistant.turn_on,
open_cover -> cover.open_cover, ...), so behaviour matches an existing,
working integration rather than inventing new semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .entities import SPOKEN_UNITS_DE, EntitySnapshot, format_spoken_number, spoken_state
from .nlu.capabilities import Capability
from .nlu.automation_operations import validate_registered_operation
from .nlu.query import QueryType, derive_query_type


@dataclass(frozen=True)
class ServiceCallPlan:
    domain: str
    service: str
    entity_id: str | list[str]
    data: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class IntentSpec:
    allowed_domains: frozenset[str]
    build: Callable[[list[EntitySnapshot]], ServiceCallPlan]
    response: Callable[[list[EntitySnapshot]], str]
    required_capability: str | None = None


def _entity_id_field(entities: list[EntitySnapshot]) -> str | list[str]:
    """A bare string for a single entity (keeps existing single-match
    service calls/tests unchanged), a list for multiple (quantifiers)."""
    ids = [e.entity_id for e in entities]
    return ids[0] if len(ids) == 1 else ids


_DOMAIN_PLURAL_DE = {
    "light": "Lichter", "switch": "Schalter", "fan": "Ventilatoren", "cover": "Rollläden",
    "script": "Skripte", "media_player": "Medienplayer", "vacuum": "Saugroboter",
    "humidifier": "Luftbefeuchter", "input_boolean": "Helfer", "valve": "Ventile",
    "scene": "Szenen",
    "lock": "Schlösser",
}


def _plural_response(entities: list[EntitySnapshot], singular_suffix: str, plural_suffix: str) -> str:
    """'{Name} {singular_suffix}' for one entity, '{count} {Domain-Plural} {plural_suffix}' for many -
    short and TTS-friendly rather than listing every name."""
    if len(entities) == 1:
        return f"{entities[0].friendly_name} {singular_suffix}"
    noun = _DOMAIN_PLURAL_DE.get(entities[0].domain, "Geräte")
    return f"{len(entities)} {noun} {plural_suffix}"


INTENTS: dict[str, IntentSpec] = {
    "HassTurnOn": IntentSpec(
        allowed_domains=frozenset({"light", "switch", "fan", "climate", "humidifier", "input_boolean"}),
        build=lambda es: ServiceCallPlan("homeassistant", "turn_on", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "eingeschaltet.", "eingeschaltet."),
    ),
    "HassTurnOff": IntentSpec(
        allowed_domains=frozenset({"light", "switch", "fan", "climate", "humidifier", "input_boolean"}),
        build=lambda es: ServiceCallPlan("homeassistant", "turn_off", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "ausgeschaltet.", "ausgeschaltet."),
    ),
    "HassToggle": IntentSpec(
        allowed_domains=frozenset({"light", "switch", "fan", "climate", "humidifier", "input_boolean"}),
        build=lambda es: ServiceCallPlan("homeassistant", "toggle", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "umgeschaltet.", "umgeschaltet."),
    ),
    "HassOpenCover": IntentSpec(
        allowed_domains=frozenset({"cover"}),
        build=lambda es: ServiceCallPlan("cover", "open_cover", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "wird geöffnet.", "geöffnet."),
    ),
    "HassCloseCover": IntentSpec(
        allowed_domains=frozenset({"cover"}),
        build=lambda es: ServiceCallPlan("cover", "close_cover", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "wird geschlossen.", "geschlossen."),
    ),
    # Skript-Aktivierung (2026-08-20): a dedicated intent rather than adding
    # "script" to HassTurnOn's allowed_domains - mirrors xiaozhi_entity_mcp's
    # own separation of a generic "turn_on" tool (light/switch/fan/cover)
    # from its own distinct "run_script" tool (script.turn_on), the same
    # precedent this module's docstring already cites. "script.turn_on" is
    # HA's documented, stable way to run a script (see
    # https://www.home-assistant.io/integrations/script/).
    "HassRunScript": IntentSpec(
        allowed_domains=frozenset({"script"}),
        build=lambda es: ServiceCallPlan("script", "turn_on", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "ausgeführt.", "ausgeführt."),
    ),
    "HassActivateScene": IntentSpec(
        allowed_domains=frozenset({"scene"}),
        build=lambda es: ServiceCallPlan("scene", "turn_on", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "aktiviert.", "aktiviert."),
    ),
    "HassMediaPlay": IntentSpec(
        allowed_domains=frozenset({"media_player"}),
        build=lambda es: ServiceCallPlan("media_player", "media_play", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "spielt weiter.", "gestartet."),
    ),
    "HassMediaPause": IntentSpec(
        allowed_domains=frozenset({"media_player"}),
        build=lambda es: ServiceCallPlan("media_player", "media_pause", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "pausiert.", "pausiert."),
    ),
    "HassMediaStop": IntentSpec(
        allowed_domains=frozenset({"media_player"}),
        build=lambda es: ServiceCallPlan("media_player", "media_stop", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "gestoppt.", "gestoppt."),
    ),
    "HassMediaMute": IntentSpec(
        allowed_domains=frozenset({"media_player"}),
        build=lambda es: ServiceCallPlan(
            "media_player", "volume_mute", _entity_id_field(es),
            {"is_volume_muted": True},
        ),
        response=lambda es: _plural_response(es, "stummgeschaltet.", "stummgeschaltet."),
    ),
    "HassVacuumStart": IntentSpec(
        allowed_domains=frozenset({"vacuum"}),
        build=lambda es: ServiceCallPlan("vacuum", "start", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "gestartet.", "gestartet."),
    ),
    "HassVacuumStop": IntentSpec(
        allowed_domains=frozenset({"vacuum"}),
        build=lambda es: ServiceCallPlan("vacuum", "stop", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "gestoppt.", "gestoppt."),
    ),
    "HassVacuumLocate": IntentSpec(
        allowed_domains=frozenset({"vacuum"}),
        build=lambda es: ServiceCallPlan("vacuum", "locate", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "wird geortet.", "werden geortet."),
    ),
    "HassPressButton": IntentSpec(
        allowed_domains=frozenset({"button"}),
        build=lambda es: ServiceCallPlan("button", "press", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "gedrückt.", "gedrückt."),
    ),
    "HassOpenValve": IntentSpec(
        allowed_domains=frozenset({"valve"}),
        build=lambda es: ServiceCallPlan("valve", "open_valve", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "wird geöffnet.", "geöffnet."),
        required_capability="OPEN",
    ),
    "HassCloseValve": IntentSpec(
        allowed_domains=frozenset({"valve"}),
        build=lambda es: ServiceCallPlan("valve", "close_valve", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "wird geschlossen.", "geschlossen."),
        required_capability="CLOSE",
    ),
    "HassLock": IntentSpec(
        allowed_domains=frozenset({"lock"}),
        build=lambda es: ServiceCallPlan("lock", "lock", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "abgeschlossen.", "abgeschlossen."),
    ),
    "HassUnlock": IntentSpec(
        allowed_domains=frozenset({"lock"}),
        build=lambda es: ServiceCallPlan("lock", "unlock", _entity_id_field(es)),
        response=lambda es: _plural_response(es, "aufgeschlossen.", "aufgeschlossen."),
    ),
}


REGISTERED_OPERATION_INTENT = "HassRegisteredOperation"


def build_registered_operation(
    entities: list[EntitySnapshot], parameters: Mapping[str, Any]
) -> ServiceCallPlan | None:
    """Materialize one typed operation from the closed local allow-list."""
    domain = parameters.get("service_domain")
    service = parameters.get("service_name")
    data = parameters.get("service_data", {})
    if not isinstance(domain, str) or not isinstance(service, str) or not isinstance(data, Mapping):
        return None
    normalized_data = dict(data)
    target_domains = frozenset(entity.domain for entity in entities)
    if not validate_registered_operation(domain, service, normalized_data, target_domains):
        return None
    return ServiceCallPlan(domain, service, _entity_id_field(entities), normalized_data)


# Central, deterministic action-inversion table (live conversation-context
# follow-up fix, 2026-08-18): "Und jetzt wieder aus." after "Schalte das
# Studio ein." must resolve to the *opposite* of the previous command's
# intent, not fail as an unrelated fresh sentence (see
# ``parsers.CommandFollowupParser``). Only intents with one semantically
# unambiguous opposite are listed here - ``HassToggle`` is deliberately
# absent: its own effect already depends on current state, so "the
# opposite of toggle" has no single fixed answer. A follow-up whose
# previous command isn't in this table falls through to the normal "nicht
# verstanden" response instead of guessing (Regel 4).
ACTION_OPPOSITES: dict[str, str] = {
    "HassTurnOn": "HassTurnOff",
    "HassTurnOff": "HassTurnOn",
    "HassOpenCover": "HassCloseCover",
    "HassCloseCover": "HassOpenCover",
    "HassMediaPlay": "HassMediaPause",
    "HassMediaPause": "HassMediaPlay",
    "HassVacuumStart": "HassVacuumStop",
    "HassVacuumStop": "HassVacuumStart",
    "HassOpenValve": "HassCloseValve",
    "HassCloseValve": "HassOpenValve",
    "HassLock": "HassUnlock",
    "HassUnlock": "HassLock",
}


@dataclass(frozen=True)
class PercentIntentSpec:
    """Separate from IntentSpec: build/response take an extra percent value,
    a different callable signature than the 5 action intents above - kept
    apart deliberately rather than bolting an optional argument onto those."""

    build: Callable[[list[EntitySnapshot], int], ServiceCallPlan]
    response: Callable[[list[EntitySnapshot], int], str]


# Keyed by the *resolved entity's domain*, not by a grammar-level intent
# name: German speakers use the same generic verbs ("stelle", "setze") for
# both cover position and light brightness, so the sentence template alone
# cannot tell them apart (verified empirically - a verb-based split either
# rejects valid phrasings or silently mis-routes them). The single
# HassSetPercentage grammar only captures {name}/{percent}; which service
# call applies is decided here, from the entity that {name} actually
# resolved to. A domain with no entry here (e.g. sensor) falls through to
# "not understood" via the dict.get(...) is None check in _match_percentage.
PERCENT_INTENTS: dict[str, PercentIntentSpec] = {
    "cover": PercentIntentSpec(
        build=lambda es, pct: ServiceCallPlan("cover", "set_cover_position", _entity_id_field(es), {"position": pct}),
        response=lambda es, pct: _plural_response(
            es, f"auf {pct} Prozent gefahren.", f"auf {pct} Prozent gefahren."
        ),
    ),
    "light": PercentIntentSpec(
        build=lambda es, pct: ServiceCallPlan("light", "turn_on", _entity_id_field(es), {"brightness_pct": pct}),
        response=lambda es, pct: _plural_response(
            es,
            f"auf {pct} Prozent Helligkeit gestellt.",
            f"auf {pct} Prozent Helligkeit gestellt.",
        ),
    ),
}


@dataclass(frozen=True)
class QueryIntentSpec:
    """A query never produces a ServiceCallPlan - it only speaks the
    current state (see MatchResult.plan: ServiceCallPlan | None).

    ``allows_empty``: most queries expect at least one resolved entity (an
    empty result means "not understood", same as any other parser miss).
    The state-query operations (V4.2, LIST_ENTITIES/COUNT_ENTITIES/
    EXISTS) are a deliberate exception - "0 Fenster offen" is itself a
    valid, correct answer, not an error - so they set this ``True`` to
    skip nlu/validator.py's check #2 (ENTITY_NOT_FOUND). Defaults ``False``
    so every pre-existing query intent (HassGetState/HassQueryComparison)
    keeps its current "empty = not understood" behaviour unchanged.
    """

    allowed_domains: frozenset[str]
    # Widened to also take ``frame.parameters`` (mirrors the exact widening
    # already used for LightExtendedIntentSpec/FanExtendedIntentSpec/
    # ClimateExtendedIntentSpec) - the V4.2 state-query responses need to
    # read the resolved SemanticState/count_only/device_class the parser
    # stashed there (StateQueryParser doesn't fit its result into a single
    # entity attribute the way HassGetState/HassQueryComparison could).
    # HassGetState/HassQueryComparison's existing lambdas just ignore it.
    response: Callable[[list[EntitySnapshot], Mapping[str, Any]], str]
    allows_empty: bool = False


# Shared with every other spoken measurement (entities.SPOKEN_UNITS_DE).
_UNIT_SPOKEN_DE = SPOKEN_UNITS_DE


def _measurement_value(entity: EntitySnapshot) -> str | None:
    """Spoken number of a measurement, or None when HA reports no number.

    Temperatures are spoken with one decimal ("21,6 Grad"), everything else
    with at most two; "unknown"/"unavailable" never become a number (F12).
    """
    try:
        number = float(str(entity.state).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if entity.unit in {"°C", "°F"}:
        number = round(number, 1)
    return format_spoken_number(number)


def _speak_state(entity: EntitySnapshot) -> str:
    unit = _UNIT_SPOKEN_DE.get(entity.unit, entity.unit) if entity.unit else None
    if unit:
        value = _measurement_value(entity)
        if value is None:
            return f"{entity.friendly_name} meldet gerade keinen Messwert."
        return f"{value} {unit}."
    return f"{spoken_state(entity.state)}."


# A light's ``state`` is "on"/"off", not a number - its brightness lives in
# the raw HA "brightness" attribute (0-255, HA's own scale). Converted to a
# percentage here (not stored pre-converted anywhere) since nothing else in
# this project needs a light's brightness as a fraction of 255.
def _speak_brightness(entity: EntitySnapshot) -> str:
    if entity.state == "off":
        return "aus."
    brightness = entity.attributes.get("brightness")
    if brightness is None:
        return "an."  # on, but the light doesn't report a brightness level (e.g. onoff-only)
    return f"{round(brightness / 255 * 100)} Prozent."


def _speak_query(entity: EntitySnapshot) -> str:
    """Dispatches on nlu/query.py's QueryType - today only BRIGHTNESS needs
    its own phrasing, every other type (including STATE) is spoken via the
    same raw state+unit logic ``_speak_state`` already provided."""
    if derive_query_type(entity) is QueryType.BRIGHTNESS:
        return _speak_brightness(entity)
    return _speak_state(entity)


def _format_measurement_number(raw: str) -> str:
    """Speak numeric HA states with a German decimal separator."""
    return format_spoken_number(raw)


def _speak_location_measurements(
    entities: list[EntitySnapshot], params: Mapping[str, Any]
) -> str:
    """Speak every matching measurement; average only when requested."""
    property_label = str(params.get("property_label", "Messwert"))
    if params.get("average"):
        numeric: list[float] = []
        for entity in entities:
            try:
                numeric.append(float(entity.state))
            except (TypeError, ValueError):
                return f"Für {property_label} kann ich keinen Durchschnitt berechnen."
        unit = entities[0].unit if entities else None
        if not numeric or any(entity.unit != unit for entity in entities):
            return f"Für {property_label} kann ich keinen eindeutigen Durchschnitt berechnen."
        spoken_unit = _UNIT_SPOKEN_DE.get(unit, unit) if unit else ""
        value = _format_measurement_number(str(round(sum(numeric) / len(numeric), 1)))
        suffix = f" {spoken_unit}" if spoken_unit else ""
        return f"Der Durchschnitt für {property_label} beträgt {value}{suffix}."

    readings: list[str] = []
    if len(entities) == 1:
        return _speak_query(entities[0])
    for entity in entities:
        if derive_query_type(entity) is QueryType.BRIGHTNESS:
            readings.append(
                f"{entity.friendly_name}: {_speak_brightness(entity).removesuffix('.')}"
            )
            continue
        unit = _UNIT_SPOKEN_DE.get(entity.unit, entity.unit) if entity.unit else None
        measured = _measurement_value(entity)
        if measured is None:
            readings.append(f"{entity.friendly_name}: kein Messwert")
            continue
        suffix = f" {unit}" if unit else ""
        readings.append(f"{entity.friendly_name}: {measured}{suffix}")
    if len(readings) == 1:
        return readings[0] + "."
    return "; ".join(readings) + "."


# Which raw HA attribute a comparison-query match's current value is spoken
# from, per domain (HomeIntent plan V4.6, "Comparisons", query-filter half -
# user decision 2026-08-11). Deliberately a separate small read here rather
# than importing parsers.py's ``_current_percent``/``_current_temperature``:
# parsers.py already imports from this module (QUERY_INTENTS itself), so the
# reverse import would be circular - same reasoning kept everywhere else in
# this project for the parsers.py/service_call.py boundary.
def _speak_comparison_value(entity: EntitySnapshot) -> str:
    if entity.domain == "light":
        brightness = entity.attributes.get("brightness")
        return f"{round(brightness / 255 * 100)} Prozent" if brightness is not None else "unbekannt"
    if entity.domain == "cover":
        position = entity.attributes.get("current_position")
        return f"{round(position)} Prozent" if position is not None else "unbekannt"
    if entity.domain == "climate":
        current = entity.attributes.get("current_temperature")
        return f"{round(current)} Grad" if current is not None else "unbekannt"
    return "unbekannt"


def _speak_comparison_matches(entities: list[EntitySnapshot]) -> str:
    """ComparisonQueryParser already filtered ``entities`` down to the ones
    satisfying the comparison (see its docstring) - this only speaks the
    result, same "no matches" bailout as everywhere else in this engine
    doesn't apply here since an empty list never reaches this function
    (parsers.py returns ``None`` instead)."""
    if len(entities) == 1:
        entity = entities[0]
        return f"{entity.friendly_name}: {_speak_comparison_value(entity)}."
    return ", ".join(f"{e.friendly_name} ({_speak_comparison_value(e)})" for e in entities) + "."


# The union of every domain this file already treats as valid somewhere
# else (INTENTS/PERCENT_INTENTS/the other QUERY_INTENTS entries) - not a
# new, separately-invented "all domains" list (Regel 4: never guess). Used
# only by the two AUTOMATION-kind query intents below, see their comment.
_AUTOMATION_QUERY_ALLOWED_DOMAINS = frozenset(
    {"light", "switch", "fan", "climate", "cover", "sensor", "binary_sensor"}
)


QUERY_INTENTS: dict[str, QueryIntentSpec] = {
    "HassGetState": QueryIntentSpec(
        allowed_domains=frozenset({"sensor", "light"}),
        response=lambda es, params: _speak_query(es[0]),
    ),
    "HassLocationPropertyQuery": QueryIntentSpec(
        allowed_domains=frozenset({"sensor", "light"}),
        response=_speak_location_measurements,
    ),
    "HassQueryComparison": QueryIntentSpec(
        allowed_domains=frozenset({"light", "cover", "climate"}),
        response=lambda es, params: _speak_comparison_matches(es),
    ),
    # Graph-projected comparison of two explicitly grounded live operands.
    # ResponseGenerator consumes the typed QueryResult; this registration is
    # solely the read-only allow-list used by the validator and speech-act
    # boundary. It has no service mapping.
    "HassRelationalComparison": QueryIntentSpec(
        allowed_domains=frozenset({"sensor", "light", "cover", "climate"}),
        response=lambda es, params: "Das habe ich nicht verstanden.",
        allows_empty=False,
    ),
    "HassRelationshipQuery": QueryIntentSpec(
        allowed_domains=frozenset({"sensor", "binary_sensor", "light", "switch", "cover", "climate"}),
        response=lambda es, params: "Das habe ich nicht verstanden.",
        allows_empty=True,
    ),
    # WorldModelQuery wave: dead-code responses (engine.py::_build_match_result's
    # "query_result" branch intercepts these four intents before the
    # placeholder lambda below is ever reached - every parser that produces
    # them now stashes frame.parameters["query_result"]). Kept only because
    # QueryIntentSpec.response has no default and allows_empty/dict
    # membership are still load-bearing for validate_command() and
    # conversation.py's QUERY_INTENT_NAMES.
    "HassStateQuery": QueryIntentSpec(
        allowed_domains=frozenset({
            "binary_sensor", "cover", "light", "switch", "fan", "climate",
            "media_player", "vacuum", "humidifier", "input_boolean", "valve",
            "lawn_mower",
        }),
        response=lambda es, params: "Das habe ich nicht verstanden.",
        allows_empty=True,
    ),
    "HassCheckState": QueryIntentSpec(
        allowed_domains=frozenset({
            "binary_sensor", "cover", "light", "switch", "fan", "climate",
            "media_player", "vacuum", "humidifier", "input_boolean", "valve",
            "lawn_mower",
        }),
        response=lambda es, params: "Das habe ich nicht verstanden.",
        allows_empty=False,
    ),
    "HassEntityStateQuery": QueryIntentSpec(
        allowed_domains=frozenset({
            "binary_sensor", "cover", "light", "switch", "fan", "climate",
            "media_player", "vacuum", "humidifier", "input_boolean", "valve",
            "lawn_mower",
        }),
        response=lambda es, params: "Das habe ich nicht verstanden.",
        allows_empty=False,
    ),
    "HassExistsQuery": QueryIntentSpec(
        allowed_domains=frozenset({
            "binary_sensor", "cover", "light", "switch", "fan", "climate",
            "media_player", "vacuum", "humidifier", "input_boolean", "valve",
            "lawn_mower",
        }),
        response=lambda es, params: "Das habe ich nicht verstanden.",
        allows_empty=True,
    ),
    "HassDeviceQuery": QueryIntentSpec(
        allowed_domains=frozenset(),
        response=lambda es, params: "Das habe ich nicht verstanden.",
        allows_empty=True,
    ),
    # V5.29 "Automation Query": unlike every other QUERY_INTENTS entry, the
    # resolved entity's domain has no bearing on this query's behaviour at
    # all - "was schaltet {name}" only checks entity_id membership in an
    # automation's referenced_entity_ids (see QueryExecutor._execute_automation),
    # the same way HassDeviceQuery's domain check is inert by construction.
    # But unlike HassDeviceQuery, the entity-filtered shape here *does*
    # populate command.entities with the resolved target (so its response
    # can name it) - an empty allowed_domains would make step 5 of
    # validate_command() reject every entity-filtered match. So this uses
    # _AUTOMATION_QUERY_ALLOWED_DOMAINS (every domain already treated as
    # valid somewhere else in this file) rather than an empty set.
    "HassAutomationQuery": QueryIntentSpec(
        allowed_domains=_AUTOMATION_QUERY_ALLOWED_DOMAINS,
        response=lambda es, params: "Das habe ich nicht verstanden.",
        allows_empty=True,
    ),
    "HassAutomationWhyQuery": QueryIntentSpec(
        allowed_domains=_AUTOMATION_QUERY_ALLOWED_DOMAINS,
        response=lambda es, params: "Das habe ich nicht verstanden.",
        allows_empty=True,
    ),
}


@dataclass(frozen=True)
class LightExtendedIntentSpec:
    """Separate from IntentSpec/PercentIntentSpec: these 4 intents (v2 plan
    Phase 14, "Licht-NLU erweitern") only ever target the ``light`` domain
    but need different capabilities per intent, and build/response need the
    full parameters mapping (step_percent, or color_name, or
    color_temp_kelvin - one differently-named parameter per intent, unlike
    the single uniform ``percent`` PercentIntentSpec carries). ``capability``
    is the single source of truth nlu/validator.py reads for its
    UNSUPPORTED_CAPABILITY check - no separate intent-to-capability dict
    duplicated there.
    """

    capability: Capability
    build: Callable[[list[EntitySnapshot], Mapping[str, Any]], ServiceCallPlan]
    response: Callable[[list[EntitySnapshot], Mapping[str, Any]], str]


# German word spoken back in a response is not necessarily the one the user
# said (e.g. both "lila" and "violett" resolve to "purple") - same
# not-an-exact-echo precedent as _plural_response's domain-plural wording.
_COLOR_SPOKEN_DE = {
    "red": "rot", "green": "grün", "blue": "blau", "yellow": "gelb", "orange": "orange",
    "purple": "lila", "white": "weiß", "pink": "pink", "turquoise": "türkis", "cyan": "cyan",
}
_COLOR_TEMP_SPOKEN_DE = {2700: "warmweiß", 6500: "kaltweiß"}


@dataclass(frozen=True)
class FanExtendedIntentSpec:
    """Separate from LightExtendedIntentSpec: different domain (fan) and a
    different parameter shape (``level``, not step_percent/color_name) -
    same "one dataclass per domain extension" precedent LightExtendedIntentSpec's
    own docstring explains (v2 plan Phase 16, "Fan-NLU erweitern")."""

    capability: Capability
    build: Callable[[list[EntitySnapshot], Mapping[str, Any]], ServiceCallPlan]
    response: Callable[[list[EntitySnapshot], Mapping[str, Any]], str]


def _fan_set_speed_plan(es: list[EntitySnapshot], params: Mapping[str, Any]) -> ServiceCallPlan:
    """"Stufe N" -> a percentage, via the resolved fan's own percentage_step
    attribute (HA's FanEntity base class always exposes it, = 100 /
    speed_count - defaults to 1.0 for a continuous-percentage fan). Clamped
    to 100 so an unusually high level on a coarse-stepped fan can't produce
    an out-of-range service call."""
    percentage_step = es[0].attributes.get("percentage_step") or 1.0
    percentage = min(round(params["level"] * percentage_step), 100)
    return ServiceCallPlan("fan", "turn_on", _entity_id_field(es), {"percentage": percentage})


FAN_EXTENDED_INTENTS: dict[str, FanExtendedIntentSpec] = {
    "HassFanSetSpeed": FanExtendedIntentSpec(
        capability=Capability.FAN_SPEED,
        build=_fan_set_speed_plan,
        response=lambda es, params: f"{es[0].friendly_name} auf Stufe {params['level']} gestellt.",
    ),
    # "schneller"/"langsamer" call HA's native fan.increase_speed/
    # decrease_speed directly, with no extra data - unlike light brightness
    # (Phase 14) there is no invented step size to decide on here; HA/the
    # device own the step.
    "HassFanIncreaseSpeed": FanExtendedIntentSpec(
        capability=Capability.FAN_SPEED,
        build=lambda es, params: ServiceCallPlan("fan", "increase_speed", _entity_id_field(es)),
        response=lambda es, params: f"{es[0].friendly_name} schneller gestellt.",
    ),
    "HassFanDecreaseSpeed": FanExtendedIntentSpec(
        capability=Capability.FAN_SPEED,
        build=lambda es, params: ServiceCallPlan("fan", "decrease_speed", _entity_id_field(es)),
        response=lambda es, params: f"{es[0].friendly_name} langsamer gestellt.",
    ),
}


@dataclass(frozen=True)
class ClimateExtendedIntentSpec:
    """Separate from FanExtendedIntentSpec: different domain (climate) and a
    different parameter shape (an absolute ``temperature`` target, not
    ``level``/step_percent) - same "one dataclass per domain extension"
    precedent FanExtendedIntentSpec's own docstring explains (v2 plan Phase
    17, "Climate-NLU")."""

    capability: Capability
    build: Callable[[list[EntitySnapshot], Mapping[str, Any]], ServiceCallPlan | None]
    response: Callable[[list[EntitySnapshot], Mapping[str, Any]], str]


# "wärmer"/"kälter" have no user-specified amount (unlike light brightness in
# Phase 14) - a fixed default step, same design choice FanExtendedIntentSpec's
# docstring explains for why *that* phase didn't need one (climate has no
# native increase/decrease service to defer to, unlike fan.increase_speed).
_CLIMATE_TEMPERATURE_STEP = 1


def _climate_step_plan(es: list[EntitySnapshot], step: float) -> ServiceCallPlan | None:
    """New absolute target = the resolved entity's current setpoint (its
    ``temperature`` attribute - the same signal nlu/capabilities.py already
    reads to derive Capability.TEMPERATURE) plus/minus the fixed step.

    The Validator already requires Capability.TEMPERATURE (derived from
    ``"temperature" in attributes`` at snapshot time) before this runs, so
    the attribute should always be present - but the snapshot is a single
    point-in-time read, so this stays defensive rather than trusting that
    invariant with a raw ``[...]`` access (KeyError would otherwise
    propagate as HA's generic "Unexpected error during intent
    recognition"). ``None`` here means "fall through to not understood",
    same as any other unsupported-capability case.
    """
    current = es[0].attributes.get("temperature")
    if current is None:
        return None
    return ServiceCallPlan("climate", "set_temperature", _entity_id_field(es), {"temperature": current + step})


CLIMATE_EXTENDED_INTENTS: dict[str, ClimateExtendedIntentSpec] = {
    "HassClimateSetTemperature": ClimateExtendedIntentSpec(
        capability=Capability.TEMPERATURE,
        build=lambda es, params: ServiceCallPlan(
            "climate", "set_temperature", _entity_id_field(es), {"temperature": params["temperature"]}
        ),
        response=lambda es, params: f"{es[0].friendly_name} auf {float(params['temperature']):g} Grad gestellt.",
    ),
    "HassClimateIncreaseTemperature": ClimateExtendedIntentSpec(
        capability=Capability.TEMPERATURE,
        build=lambda es, params: _climate_step_plan(es, params.get("step", _CLIMATE_TEMPERATURE_STEP)),
        response=lambda es, params: f"{es[0].friendly_name} wärmer gestellt.",
    ),
    "HassClimateDecreaseTemperature": ClimateExtendedIntentSpec(
        capability=Capability.TEMPERATURE,
        build=lambda es, params: _climate_step_plan(es, -params.get("step", _CLIMATE_TEMPERATURE_STEP)),
        response=lambda es, params: f"{es[0].friendly_name} kälter gestellt.",
    ),
}


LIGHT_EXTENDED_INTENTS: dict[str, LightExtendedIntentSpec] = {
    "HassLightBrighten": LightExtendedIntentSpec(
        capability=Capability.BRIGHTNESS,
        build=lambda es, params: ServiceCallPlan(
            "light", "turn_on", _entity_id_field(es), {"brightness_step_pct": params["step_percent"]}
        ),
        response=lambda es, params: f"{es[0].friendly_name} um {params['step_percent']} Prozent heller gestellt.",
    ),
    "HassLightDim": LightExtendedIntentSpec(
        capability=Capability.BRIGHTNESS,
        build=lambda es, params: ServiceCallPlan(
            "light", "turn_on", _entity_id_field(es), {"brightness_step_pct": -params["step_percent"]}
        ),
        response=lambda es, params: f"{es[0].friendly_name} um {params['step_percent']} Prozent dunkler gestellt.",
    ),
    "HassLightSetColor": LightExtendedIntentSpec(
        capability=Capability.COLOR,
        build=lambda es, params: ServiceCallPlan(
            "light", "turn_on", _entity_id_field(es), {"color_name": params["color_name"]}
        ),
        response=lambda es, params: (
            f"{es[0].friendly_name} auf {_COLOR_SPOKEN_DE.get(params['color_name'], params['color_name'])} gestellt."
        ),
    ),
    "HassLightSetColorTemp": LightExtendedIntentSpec(
        capability=Capability.COLOR_TEMPERATURE,
        build=lambda es, params: ServiceCallPlan(
            "light", "turn_on", _entity_id_field(es), {"color_temp_kelvin": params["color_temp_kelvin"]}
        ),
        response=lambda es, params: (
            f"{es[0].friendly_name} auf "
            f"{_COLOR_TEMP_SPOKEN_DE.get(params['color_temp_kelvin'], str(params['color_temp_kelvin']) + ' Kelvin')} "
            "gestellt."
        ),
    ),
}
