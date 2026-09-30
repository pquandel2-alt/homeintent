"""Phase 4 (7.3.4): property-based safety invariants.

Sentences are composed from the building blocks of the lexicon - device
genera with their gender, plural and synonyms (``device_ontology.GENERA``),
the places of the synthetic test house, articles, politeness, negation,
modality, tense, time, subordinate clauses and self corrections - never
taken from a fixed sentence list. Every invariant is its own test.

"Write" means any service call that changes something (reads such as
``recorder.get_statistics`` or ``todo.get_items`` are not writes).

The suite runs in CI with a fixed seed set (profile ``ci``); the nightly
workflow runs it with changing seeds (``HOMEINTENT_HYPOTHESIS_PROFILE=nightly``).
Counterexamples found there are added as fixed regression cases below
(``REGRESSIONS``).
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, example, given, settings, strategies as st  # noqa: F401

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _testhaus import HouseConversation, house_entities  # noqa: E402
from homeintent.effect_graph import build_plan_effects_from_sources  # noqa: E402
from homeintent.entities import EntitySnapshot, normalize_for_compare  # noqa: E402
from homeintent.execution_policy import PolicyOutcome, evaluate_service_plan  # noqa: E402
from homeintent.nlu.device_ontology import GENERA, Gender, genus_forms  # noqa: E402
from homeintent.nlu.german_morphology import dative_location_phrase  # noqa: E402
from homeintent.nlu.target_resolution import genus_members  # noqa: E402
from homeintent.plan_origin import PlanOrigin  # noqa: E402
from homeintent.risk import RiskLevel, classify_service_plan  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402

settings.register_profile(
    "ci", derandomize=True, max_examples=25, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
settings.register_profile(
    "nightly", max_examples=300, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
settings.load_profile(os.environ.get("HOMEINTENT_HYPOTHESIS_PROFILE", "ci"))

READ_SERVICES = frozenset({
    ("recorder", "get_statistics"), ("todo", "get_items"), ("calendar", "get_events"),
    ("persistent_notification", "create"), ("automation", "reload"),
})
AUTO = {"implicit_action_level": "low_risk_auto"}

# ------------------------------------------------------------ building blocks
_ENTITIES = house_entities()
_SWITCHABLE = [
    item for item in GENERA
    if item.in_everything and not item.critical and not item.sensor
]
_ARTICLE = {  # nominative/accusative singular definite article
    Gender.MASCULINE: "den", Gender.FEMININE: "die", Gender.NEUTER: "das",
}
_ARTICLE_NOM = {Gender.MASCULINE: "der", Gender.FEMININE: "die", Gender.NEUTER: "das"}


@lru_cache(maxsize=None)
def _places_for(key: str) -> tuple[tuple[str, str, int], ...]:
    """(area_id, spoken place, number of members there) for a genus."""
    members = genus_members(key, _ENTITIES)
    counts: dict[tuple[str, str], int] = {}
    for entity in members:
        if entity.area_id and entity.area_name:
            counts[(entity.area_id, entity.area_name)] = counts.get((entity.area_id, entity.area_name), 0) + 1
    return tuple((area_id, dative_location_phrase(name), count) for (area_id, name), count in sorted(counts.items()))


_GENERA_WITH_PLACES = [item for item in _SWITCHABLE if _places_for(item.key)]


@st.composite
def device_phrase(draw, *, plural: bool | None = None):
    """(genus, accusative phrase, nominative phrase, place, members at place)."""
    genus = draw(st.sampled_from(_GENERA_WITH_PLACES))
    area_id, place, count = draw(st.sampled_from(_places_for(genus.key)))
    use_plural = draw(st.booleans()) if plural is None else plural
    if use_plural:
        noun = genus.plural
        phrase = f"alle {noun}" if draw(st.booleans()) else f"die {noun}"
        nominative = phrase
    else:
        noun = draw(st.sampled_from(genus.lemmas))
        phrase = f"{_ARTICLE[genus.gender]} {noun}"
        nominative = f"{_ARTICLE_NOM[genus.gender]} {noun}"
    assume(noun.casefold() in {form.casefold() for form in genus_forms(genus.key)} or use_plural)
    return genus, phrase, nominative, place, count, use_plural


POLITE = st.sampled_from(["", "bitte ", "doch mal ", "mal eben "])
ON_OFF = st.sampled_from([("an", "ein", "angemacht", "anmachen"), ("aus", "aus", "ausgemacht", "ausmachen")])
UNKNOWN = st.sampled_from(["Quirlomat", "Zappelding", "Blubberfix", "Knorpeltron", "Wusel"])
CLOCK = st.sampled_from(["um 22 Uhr", "um halb sieben", "morgen um 8 Uhr", "in zehn Minuten", "heute Abend um 20 Uhr"])


_HOUSE: HouseConversation | None = None
_COUNTER = [0]


def _house() -> HouseConversation:
    global _HOUSE
    if _HOUSE is None:
        _HOUSE = HouseConversation(pytest.MonkeyPatch(), options=AUTO)
    return _HOUSE


def say(text: str):
    house = _house()
    _COUNTER[0] += 1
    house.conversation_id = f"prop-{_COUNTER[0]}"
    return house.say(text)


def writes(turn) -> list[tuple[str, str]]:
    return [(domain, service) for domain, service, _ in turn.calls if (domain, service) not in READ_SERVICES]


# ------------------------------------------------------------ language invariants
@given(device_phrase(), ON_OFF, POLITE)
def test_negation_never_writes(device, switch, polite):
    _genus, phrase, _nom, place, _count, _plural = device
    particle, verb_particle, _part, _inf = switch
    for text in (
        f"Mach {polite}{phrase} {place} nicht {particle}.",
        f"Schalte {polite}{phrase} {place} nicht {verb_particle}.",
    ):
        assert writes(say(text)) == [], text


@given(device_phrase(), ON_OFF)
def test_question_never_writes(device, switch):
    _genus, _phrase, nominative, place, _count, plural = device
    particle = switch[0]
    verb = "Sind" if plural else "Ist"
    for text in (
        f"{verb} {nominative} {place} {particle}?",
        f"Weißt du, ob {nominative} {place} {particle} {'sind' if plural else 'ist'}?",
    ):
        assert writes(say(text)) == [], text


@given(device_phrase(), ON_OFF)
def test_past_statement_changes_nothing_now(device, switch):
    _genus, phrase, nominative, place, _count, plural = device
    particle, _vp, participle, _inf = switch
    for text in (
        f"Gestern {'waren' if plural else 'war'} {nominative} {place} {particle}.",
        f"Ich habe vorhin {phrase} {place} {participle}.",
    ):
        assert writes(say(text)) == [], text


@given(device_phrase(), ON_OFF)
def test_counterfactual_never_writes(device, switch):
    _genus, phrase, _nom, place, _count, _plural = device
    _particle, _vp, participle, infinitive = switch
    for text in (
        f"Wenn ich wollte, würde ich {phrase} {place} {infinitive}.",
        f"Hätte ich doch {phrase} {place} {participle}.",
        f"Angenommen, du würdest {phrase} {place} {infinitive}.",
    ):
        assert writes(say(text)) == [], text


@given(device_phrase(plural=True), ON_OFF, UNKNOWN)
def test_unknown_target_word_never_widens_the_target_set(device, switch, unknown):
    _genus, phrase, _nom, place, _count, _plural = device
    particle = switch[0]
    base = say(f"Mach {phrase} {place} {particle}.").targets
    narrowed = say(f"Mach {phrase} {unknown} {place} {particle}.").targets
    assert narrowed <= base


@given(device_phrase(), ON_OFF, POLITE)
def test_targets_stay_within_the_spoken_genus(device, switch, polite):
    genus, phrase, _nom, place, _count, _plural = device
    turn = say(f"Mach {polite}{phrase} {place} {switch[0]}.")
    members = {entity.entity_id for entity in genus_members(genus.key, _ENTITIES)}
    assert turn.targets <= members, (genus.key, turn.targets - members)


@given(device_phrase(plural=True), ON_OFF, UNKNOWN)
def test_unknown_exception_never_executes_partially(device, switch, unknown):
    _genus, phrase, _nom, place, _count, _plural = device
    text = f"Mach {phrase} {place} {switch[0]}, außer {unknown}."
    assert writes(say(text)) == [], text


@given(device_phrase(), ON_OFF, UNKNOWN)
def test_partially_understood_multi_command_never_executes_partially(device, switch, unknown):
    _genus, phrase, _nom, place, _count, _plural = device
    text = f"Mach {phrase} {place} {switch[0]} und dann {unknown} den {unknown}."
    assert writes(say(text)) == [], text


@given(device_phrase(), ON_OFF, CLOCK)
def test_timed_command_never_runs_immediately(device, switch, clock):
    _genus, phrase, _nom, place, _count, _plural = device
    text = f"Mach {clock} {phrase} {place} {switch[0]}."
    turn = say(text)
    assert [call for call in writes(turn) if call[0] != "automation"] == [], text


_CROWDED = [
    (genus, place)
    for genus in GENERA
    if not genus.sensor and genus.key != "light"  # "das Licht" is a mass noun (rule F20)
    for _area, place, count in _places_for(genus.key)
    if count > 1
] + [
    (genus, place)
    for genus in GENERA if genus.key == "light"
    for _area, place, count in _places_for(genus.key) if count > 1
]


@given(st.sampled_from(_CROWDED), ON_OFF, st.data())
def test_singular_with_several_matches_asks(crowded, switch, data):
    genus, place = crowded
    lemmas = [lemma for lemma in genus.lemmas if lemma not in genus.mass_lemmas]
    noun = data.draw(st.sampled_from(lemmas))
    turn = say(f"Mach {_ARTICLE[genus.gender]} {noun} {place} {switch[0]}.")
    assert writes(turn) == [], turn.speech


# ------------------------------------------------------------ policy invariants
_SERVICES = {
    "light": ["turn_on", "turn_off"], "switch": ["turn_on", "turn_off"], "cover": ["open_cover", "close_cover"],
    "lock": ["lock", "unlock"], "button": ["press"], "vacuum": ["start"], "climate": ["set_temperature"],
    "alarm_control_panel": ["alarm_disarm"], "fan": ["turn_on"],
}


@st.composite
def plan_and_world(draw):
    domain = draw(st.sampled_from(sorted(_SERVICES)))
    service = draw(st.sampled_from(_SERVICES[domain]))
    count = draw(st.integers(1, 8))
    ids = [f"{domain}.d{index}" for index in range(count)]
    classes = draw(st.sampled_from([None, "garage", "window"]))
    entities = [EntitySnapshot(item, item, domain, "off", device_class=classes) for item in ids]
    return ServiceCallPlan(domain, service, ids if count > 1 else ids[0]), entities


LEVELS = st.sampled_from(["understand_only", "propose", "low_risk_auto", "bound_routines_auto"])
STRICTNESS = {PolicyOutcome.ALLOW: 0, PolicyOutcome.CONFIRM: 1, PolicyOutcome.DENY: 2}


@given(plan_and_world(), LEVELS, st.sampled_from([PlanOrigin.IMPLICIT_NEED, PlanOrigin.INFERRED_ROUTINE]),
       st.booleans(), st.sampled_from(["medium", "high", "critical"]))
def test_implicit_origins_are_never_looser_than_explicit(world, level, origin, bound, threshold):
    plan, entities = world
    options = {"implicit_action_level": level, "confirmation_level": threshold}
    explicit = evaluate_service_plan(plan, entities, options, is_admin=True, user_id="admin")
    implicit = evaluate_service_plan(
        plan, entities, options, is_admin=True, user_id="admin", origin=origin, binding_confirmed=bound,
    )
    assert STRICTNESS[implicit.outcome] >= STRICTNESS[explicit.outcome]


class _Sources:
    def __init__(self, sequence, groups=None):
        self.sequence, self.groups = sequence, groups or {}

    def script_sequence(self, entity_id):
        return self.sequence if entity_id == "script.x" else None

    def automation_sequence(self, entity_id):
        return None

    def scene_states(self, entity_id):
        return None

    def group_members(self, entity_id):
        return self.groups.get(entity_id)

    def resolve_target(self, selector):
        return frozenset()

    def resolve_registry_id(self, value):
        return None

    def entity_exists(self, entity_id):
        return False

    def entities_of_domain(self, domain):
        return ()

    def followups(self, entity_ids):
        return ()

    def name(self, kind, item_id):
        return None


@st.composite
def script_steps(draw):
    steps = []
    for index in range(draw(st.integers(1, 5))):
        domain = draw(st.sampled_from(sorted(_SERVICES)))
        service = draw(st.sampled_from(_SERVICES[domain]))
        ids = [f"{domain}.s{index}_{n}" for n in range(draw(st.integers(1, 3)))]
        step = {"action": f"{domain}.{service}", "target": {"entity_id": ids}}
        wrapper = draw(st.sampled_from(["plain", "choose", "if", "parallel", "repeat"]))
        if wrapper == "choose":
            step = {"choose": [{"conditions": [], "sequence": [step]}]}
        elif wrapper == "if":
            step = {"if": [], "then": [], "else": [step]}
        elif wrapper == "parallel":
            step = {"parallel": [step]}
        elif wrapper == "repeat":
            step = {"repeat": {"count": 2, "sequence": [step]}}
        steps.append(step)
    return steps


def _entities_for(steps, extra=()):
    ids = set(extra)

    def collect(node):
        if isinstance(node, dict):
            target = node.get("target")
            if isinstance(target, dict):
                ids.update(target.get("entity_id", []))
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(steps)
    return [EntitySnapshot(item, item, item.split(".")[0], "off") for item in sorted(ids)]


SCRIPT = ServiceCallPlan("script", "turn_on", "script.x")


@given(script_steps())
def test_composite_risk_is_at_least_the_highest_transitive_effect(steps):
    entities = _entities_for(steps, ["script.x"])
    effects = build_plan_effects_from_sources("script", "turn_on", ["script.x"], _Sources(steps))
    decision = evaluate_service_plan(SCRIPT, entities, {}, is_admin=True, user_id="admin", effects=effects)
    highest = max(
        classify_service_plan(ServiceCallPlan(effect.domain, effect.service, list(effect.entity_ids)), entities)
        for effect in effects.effects
    )
    assert decision.risk >= highest


@given(script_steps(), st.sampled_from([
    {"event": "boom"}, {"action": "shell_command.x"},
    {"action": "light.turn_on", "target": {"entity_id": "{{ states('x') }}"}},
    {"action": "{{ 'light.turn_on' }}"}, {"action": "mqtt.publish", "data": {"topic": "x"}},
]), st.sampled_from(["deny", "confirm"]), st.booleans())
def test_incomplete_effect_graph_is_never_low_or_allowed(steps, unknown_step, mode, attended):
    sequence = [*steps, unknown_step]
    entities = _entities_for(sequence, ["script.x"])
    effects = build_plan_effects_from_sources("script", "turn_on", ["script.x"], _Sources(sequence))
    assert not effects.complete
    decision = evaluate_service_plan(
        SCRIPT, entities, {"effect_graph_unknown": mode}, is_admin=True, user_id="admin",
        effects=effects, attended=attended,
    )
    assert decision.outcome is not PolicyOutcome.ALLOW
    assert decision.outcome is PolicyOutcome.DENY or decision.risk >= RiskLevel.HIGH


_GUARDED_UNEXPOSED = frozenset({"lock", "alarm_control_panel", "cover", "valve"})


@given(
    script_steps(), st.data(), st.sampled_from(["deny", "confirm", "allow"]),
    st.sampled_from(list(PlanOrigin)), st.booleans(),
)
def test_unexposed_effective_target_never_writes(steps, data, mode, origin, attended):
    """A routine that switches a hidden device: never in deny mode; in
    allow/confirm mode (7.8.2) only for an explicit, attended command and
    never for a guarded domain (lock, alarm, cover/gate, valve)."""
    import asyncio

    from homeassistant.core import HomeAssistant
    from homeintent.service_executor import async_execute_service_plan

    entities = _entities_for(steps, ["script.x"])
    hidden = data.draw(st.sampled_from([entity for entity in entities if entity.entity_id != "script.x"]))
    exposed = [entity for entity in entities if entity.entity_id != hidden.entity_id]
    hass = HomeAssistant()
    _ha_stub.register_script(hass, "script.x", steps)
    options = {"effect_graph_unknown": "confirm", "routine_unexposed_effects": mode}
    result = asyncio.run(async_execute_service_plan(
        hass, SCRIPT, exposed, options, is_admin=True, user_id="admin",
        confirmed=True, origin=origin, attended=attended,
    ))
    may_run = (
        mode != "deny" and origin is PlanOrigin.EXPLICIT_COMMAND and attended
        and hidden.domain not in _GUARDED_UNEXPOSED
    )
    if not may_run:
        assert result.executed is False
        hass.services.async_call.assert_not_awaited()


# ------------------------------------------------------------ ambiguity and learned bindings
@given(st.sampled_from(["an", "aus"]))
@example("an")
def test_ambiguous_executable_meaning_never_executes(particle):
    entities = [
        EntitySnapshot("light.a", "Leselampe", "light", "off", area_id="buero", area_name="Büro",
                       capabilities=frozenset({"TURN_ON", "TURN_OFF"})),
        EntitySnapshot("light.b", "Leselampe", "light", "off", area_id="wohnzimmer", area_name="Wohnzimmer",
                       capabilities=frozenset({"TURN_ON", "TURN_OFF"})),
    ]
    patch = pytest.MonkeyPatch()
    try:
        house = HouseConversation(patch, entities=entities, options=AUTO)
        assert writes(house.say(f"Mach die Leselampe {particle}.")) == []
    finally:
        patch.undo()


@given(st.sampled_from(["Ich gehe schlafen.", "Starte die Schlafroutine.", "Gute Nacht."]),
       st.sampled_from(["bound_routines_auto", "low_risk_auto", "propose"]))
def test_learned_binding_never_reaches_unexposed_targets(sentence, level):
    import asyncio
    import types
    from datetime import datetime

    from homeintent.bindings import BindingKind

    entities = [
        EntitySnapshot("script.gute_nacht", "Gute Nacht", "script", "off"),
        EntitySnapshot("light.a", "Licht", "light", "on", capabilities=frozenset({"TURN_ON", "TURN_OFF"})),
    ]
    patch = pytest.MonkeyPatch()
    try:
        house = HouseConversation(patch, entities=entities, options={"implicit_action_level": level})
        hass = house.entity.hass
        hass.data.pop("script", None)
        _ha_stub.register_script(hass, "script.gute_nacht", [
            {"action": "vacuum.start", "target": {"entity_id": "vacuum.robbi"}},
        ])
        hass.states._states["vacuum.robbi"] = types.SimpleNamespace(
            entity_id="vacuum.robbi", state="docked", attributes={"friendly_name": "Robbi"},
        )
        asyncio.run(house.entity._runtime_data.bindings.async_bind(
            BindingKind.ROUTINE, "sleep", "script.gute_nacht", confirmed=True, now=datetime(2026, 9, 28),
        ))
        first = house.say(sentence)
        second = house.say("Ja.")
        assert writes(first) == [] and writes(second) == [], (first.speech, second.speech)
    finally:
        patch.undo()


_ALIAS_WORDS = st.sampled_from(["Kuschelecke", "Zauberkasten", "Omalicht", "Bluna", "Knuffel"])
_ALIAS_FRAMES = st.sampled_from([
    "Mach die {w} an.", "Schalte die {w} aus.", "{w} an.", "Kannst du die {w} einschalten?",
    "Mach die {w} und das Küchenlicht an.", "Mach die {w} nicht an.", "Ist die {w} an?",
    "Schalte in 5 Minuten die {w} aus.",
])


@given(_ALIAS_WORDS, _ALIAS_FRAMES, st.sampled_from([e.entity_id for e in _ENTITIES]), st.booleans())
def test_learned_alias_never_reaches_beyond_its_exposed_target(word, frame, target, exposed):
    """7.4.1: a learned word adds no target but its own, and none that is
    not exposed; negation, questions and time commands stay non-writing."""
    import asyncio
    from datetime import datetime

    from homeintent.bindings import BindingKind

    entities = [e for e in _ENTITIES if exposed or e.entity_id != target]
    patch = pytest.MonkeyPatch()
    try:
        house = HouseConversation(patch, entities=entities, options=AUTO)
        asyncio.run(house.entity._runtime_data.bindings.async_bind(
            BindingKind.ALIAS, word, target, confirmed=True, now=datetime(2026, 9, 28), data={"spoken": word},
        ))
        sentence = frame.format(w=word)
        turn = house.say(sentence)
        written = {
            entity for domain, service, data in turn.calls
            if (domain, service) not in READ_SERVICES for entity in _ids(data)
        }
        allowed = ({target} if exposed else set()) | ({"light.kuechenlicht"} if "Küchenlicht" in sentence else set())
        assert written <= allowed, (sentence, written, turn.speech)
        # "Kannst du … einschalten?" is a polite request, not a question.
        if " nicht " in sentence or sentence.startswith("Ist ") or "Minuten" in sentence:
            assert written == set(), (sentence, turn.speech)
    finally:
        patch.undo()


@given(st.sampled_from(["Kinoabend", "Feierabend"]), st.sampled_from([e.entity_id for e in _ENTITIES if e.domain in {"lock", "cover", "alarm_control_panel"}]))
def test_macro_never_bypasses_confirmation_of_critical_steps(name, target):
    """A confirmed speech macro is a sentence: its critical steps still ask."""
    import asyncio
    from datetime import datetime

    from homeintent.bindings import BindingKind, BindingScope

    entity = next(e for e in _ENTITIES if e.entity_id == target)
    body = {
        "lock": f"Schließe {entity.friendly_name} auf.",
        "cover": f"Öffne {entity.friendly_name}.",
        "alarm_control_panel": f"Schalte {entity.friendly_name} aus.",
    }[entity.domain]
    patch = pytest.MonkeyPatch()
    try:
        house = HouseConversation(patch, options=AUTO)
        asyncio.run(house.entity._runtime_data.bindings.async_bind(
            BindingKind.MACRO, name, "macro", confirmed=True, scope=BindingScope.HOUSEHOLD,
            now=datetime(2026, 9, 28), data={"spoken": name, "body": body, "entity_ids": [target]},
        ))
        direct = HouseConversation(patch, options=AUTO).say(body)
        via_macro = house.say(f"{name}.")
        assert bool(writes(via_macro)) <= bool(writes(direct)), (body, via_macro.speech)
    finally:
        patch.undo()


def _ids(data: dict) -> list[str]:
    raw = data.get("entity_id", [])
    return [raw] if isinstance(raw, str) else list(raw)


# ------------------------------------------------------------ 7.7 B9: STT, self-corrections, dialog state
_ACTION_BY_DOMAIN = {
    "light": ("Schalte {name} ein.", "schalte {name} ein"),
    "fan": ("Schalte {name} ein.", "schalte {name} ein"),
    "input_boolean": ("Schalte {name} ein.", "schalte {name} ein"),
    "cover": ("Öffne {name}.", "öffne {name}"),
    "lock": ("Schließe {name} auf.", "schließe {name} auf"),
}
_COMPOUND_NAMES = sorted(
    (entity.entity_id, entity.friendly_name) for entity in _ENTITIES
    if entity.domain in _ACTION_BY_DOMAIN and " " not in entity.friendly_name
    and "-" not in entity.friendly_name and len(entity.friendly_name) >= 9
)


def _written(turn) -> set[str]:
    return {
        entity for domain, service, data in turn.calls
        if (domain, service) not in READ_SERVICES for entity in _ids(data)
    }


@given(st.sampled_from(_COMPOUND_NAMES), st.integers(min_value=3, max_value=6),
       st.sampled_from(["", "äh ", "ähm "]), st.booleans())
def test_stt_variant_never_changes_the_safety_form(named, cut, filler, split):
    """Lower case, no punctuation, a filler and a split compound - as Home
    Assistant's speech recognition delivers it - never write more than the
    typed sentence, and never write where the typed sentence asks first."""
    entity_id, name = named
    domain = entity_id.split(".")[0]
    typed_frame, spoken_frame = _ACTION_BY_DOMAIN[domain]
    spoken_name = name.casefold()
    if split:
        cut = min(cut, len(spoken_name) - 3)
        spoken_name = f"{spoken_name[:cut]} {spoken_name[cut:]}"
    typed = say(typed_frame.format(name=name))
    spoken = say(filler + spoken_frame.format(name=spoken_name))
    assert _written(spoken) <= _written(typed), (spoken.text, spoken.speech)
    if not _written(typed):
        assert _written(spoken) == set(), (spoken.text, spoken.speech)


_LIGHT_NAMES = sorted(
    (entity.entity_id, entity.friendly_name) for entity in _ENTITIES
    if entity.domain == "light" and " " not in entity.friendly_name and "-" not in entity.friendly_name
)
_SELF_CORRECTIONS = st.sampled_from([
    "Schalte {a} ein, äh nein, {b}.",
    "Mach {a} an, nein, {b}.",
    "Schalte {a} an, ach nein, {b}.",
    "Mach {a}, äh, {b} an.",
    "Schalte {a}, ich meine {b}, ein.",
    "Mach {a} an, nein, doch lieber {b}.",
])


@given(st.sampled_from(_LIGHT_NAMES), st.sampled_from(_LIGHT_NAMES), _SELF_CORRECTIONS)
def test_self_correction_never_executes_the_retracted_part(first, second, frame):
    """"A, äh nein, B": never A, never both; B only when the correction
    analysis carries it, otherwise a question."""
    assume(first != second)
    (first_id, first_name), (second_id, second_name) = first, second
    turn = say(frame.format(a=first_name, b=second_name))
    written = _written(turn)
    assert first_id not in written, (turn.text, turn.speech)
    assert written <= {second_id}, (turn.text, turn.speech)


_DIALOG_OPENERS = st.sampled_from([
    "Mach das Licht an.",  # clarification: which light
    "Mir ist kalt.",  # need: which room
    "Wenn die Haustür aufgeht, schalte das Flurlicht an.",  # automation draft
    "Öffne das Garagentor.",  # pending safety confirmation for another device
    "Schalte die Nachttischlampe ein.",  # candidate question
])
_CRITICAL_COMMANDS = st.sampled_from([
    "Schließe das Haustürschloss auf.",
    "Sperr das Gartentor auf.",
    "Öffne das Garagentor.",
    "Schließ die Haustür auf.",
])


@given(_DIALOG_OPENERS, _CRITICAL_COMMANDS)
def test_open_dialog_never_lowers_the_confirmation_duty(opener, critical):
    """Whatever dialog is open, a critical command on the next turn still
    asks before it writes; the open state is evidence, not authorisation."""
    house = _house()
    _COUNTER[0] += 1
    house.conversation_id = f"prop-dialog-{_COUNTER[0]}"
    house.say(opener)
    turn = house.say(critical)
    assert _written(turn) == set(), (opener, critical, turn.speech)


@given(st.sampled_from(["Schließe das Schloss auf.", "Sperr das Schloss auf.", "Mach das Schloss auf."]))
def test_learned_default_choice_never_bypasses_confirmation(sentence):
    """A learned standard pick between two locks resolves the target, never
    the confirmation duty of unlocking it."""
    import asyncio
    from datetime import datetime

    from homeintent.bindings import BindingKind, BindingScope, normalize_key

    patch = pytest.MonkeyPatch()
    try:
        house = HouseConversation(patch, options=AUTO)
        key = normalize_key("lock.gartentor_schloss|lock.haustuerschloss @ ueberall")
        asyncio.run(house.entity._runtime_data.bindings.async_bind(
            BindingKind.DEFAULT_CHOICE, key, "lock.haustuerschloss",
            confirmed=True, scope=BindingScope.USER, user_id="admin", now=datetime(2026, 9, 28),
        ))
        turn = house.say(sentence)
        assert _written(turn) == set(), (sentence, turn.speech)
    finally:
        patch.undo()


def test_habit_learning_never_learns_what_a_sentence_means():
    """V11 learns habits, times and reaction times - never "this unknown
    sentence probably means X": its model kinds carry no language, and the
    habit learner never imports language understanding."""
    import ast

    from homeintent.model_registry import LearnedKind

    assert {kind.value for kind in LearnedKind} == {
        "fact", "preference", "thermal_model", "effect_timing", "reliability",
        "habit", "duration", "energy", "battery_trend",
    }
    root = Path(__file__).parent.parent / "custom_components" / "homeintent"
    for module in ("learning_manager", "habit_discovery", "experience", "experience_store", "model_registry"):
        tree = ast.parse((root / f"{module}.py").read_text(encoding="utf-8"))
        imported = {
            node.module or "" for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not any(
            name.startswith(("nlu", "engine", "parsers", "conversation", "arbitration"))
            for name in imported
        ), (module, imported)


# ------------------------------------------------------------ 7.7.1: forms the earlier generators never reached
# Why the 7.7 generators missed the independent findings: the self-correction
# frames were six fixed sentences with "nein"/"äh" and "ich meine" only in
# mid-position; no replacement named a place or a side, and none followed a
# complete first clause without a negation word. The counterfactual frame
# used only the participles "angemacht"/"ausgemacht" and no "hätte … sollen";
# deliberation and maintenance had no generator at all. No invariant spoke
# two turns (ellipsis), coordinated with hyphen or shared head, or used the
# speaker's room. The building blocks below add exactly these.
_CORRECTION_MARKERS = st.sampled_from([
    "ich meine", "ich meinte", "halt", "äh", "ähm", "nein", "nee", "Quatsch", "sorry",
    "Moment", "korrigiere", "ach nee", "oder nee", "also", "sprich", "lieber", "besser gesagt",
    "Entschuldigung", "stopp,", "nein, doch lieber",
])
_CORRECTION_FRAMES = st.sampled_from([
    "Schalte {a} ein, {m}, {b}.",
    "Mach {a} an, {m} {b}.",
    "{a} an, {m}, {b}.",
    "Schalte {a} aus {m} {b}",
    "Mach {a} aus – {m}, {b}.",
    "Schalte {a}, {m}, {b} ein.",
])
_SWITCH_LIGHTS = sorted(
    (entity.entity_id, entity.friendly_name) for entity in _ENTITIES if entity.domain == "light"
)


@given(st.sampled_from(_SWITCH_LIGHTS), st.sampled_from(_SWITCH_LIGHTS), _CORRECTION_MARKERS, _CORRECTION_FRAMES)
def test_correction_never_writes_to_a_target_only_retracted(first, second, marker, frame):
    """A1: command × marker × replacement - never a target named only in
    the retracted part, never both."""
    assume(first != second)
    (first_id, first_name), (second_id, second_name) = first, second
    turn = say(frame.format(a=first_name, b=second_name, m=marker))
    written = _written(turn)
    assert first_id not in written, (turn.text, turn.speech)
    assert written <= {second_id}, (turn.text, turn.speech)


_PLACE_LIGHTS = [
    (place, frozenset(entity.entity_id for entity in genus_members("light", _ENTITIES) if entity.area_id == area))
    for area, place, _count in _places_for("light")
]


@given(st.sampled_from(_PLACE_LIGHTS), st.sampled_from(_PLACE_LIGHTS), _CORRECTION_MARKERS, ON_OFF)
def test_place_correction_never_writes_to_the_retracted_place(first, second, marker, switch):
    """A1: "Licht im Kinderzimmer an, halt, im Schlafzimmer"."""
    (first_place, first_ids), (second_place, second_ids) = first, second
    assume(first_place != second_place)
    turn = say(f"Licht {first_place} {switch[0]}, {marker}, {second_place}.")
    assert not _written(turn) & (first_ids - second_ids), (turn.text, turn.speech)


@given(st.sampled_from(_SWITCH_LIGHTS), _CORRECTION_MARKERS, st.sampled_from([
    "doch nicht", "lass mal", "vergiss es", "", "lieber nicht", "doch nicht, egal",
]))
def test_aborted_command_never_writes(light, marker, abort):
    """A1: "…, nein, doch nicht" / "…, stopp." runs nothing."""
    assume(marker not in {"also", "sprich", "äh", "ähm"} or abort)
    text = f"Mach {light[1]} an, {marker}, {abort}".rstrip(", ") + "."
    turn = say(text)
    assert _written(turn) == set(), (turn.text, turn.speech)


_PARTICIPLES = st.sampled_from([
    ("angemacht", "ausgemacht"), ("eingeschaltet", "ausgeschaltet"),
    ("angeschaltet", "abgeschaltet"), ("angelassen", "ausgelassen"),
])
_INFINITIVES = st.sampled_from([("anmachen", "ausmachen"), ("einschalten", "ausschalten")])


@given(device_phrase(), st.integers(0, 1), _PARTICIPLES, _INFINITIVES, st.sampled_from(range(10)))
def test_irrealis_deliberation_and_maintenance_never_write(device, on_off, participle, infinitive, frame):
    """A2: these frames around any valid command never write."""
    _genus, phrase, nominative, place, _count, plural = device
    particle = ("an", "aus")[on_off]
    part = participle[on_off]
    infinitive_form = infinitive[on_off]
    texts = [
        f"Hätte ich doch {phrase} {place} {part}.",
        f"Ich hätte {phrase} {place} {infinitive_form} sollen.",
        f"Wir hätten {phrase} {place} schon längst {part}.",
        f"Wäre {nominative} {place} doch {particle} gewesen.",
        f"Ich überlege, ob ich {phrase} {place} {infinitive_form} soll.",
        f"Ich weiß nicht, ob ich {phrase} {place} {infinitive_form} soll.",
        f"Vielleicht sollte ich {phrase} {place} {infinitive_form}.",
        f"Lass {phrase} {place} bitte {particle}.",
        f"{phrase[:1].upper()}{phrase[1:]} {place} lass bitte {particle}.",
        f"{nominative[:1].upper()}{nominative[1:]} {place} {'sollen' if plural else 'soll'} {particle} bleiben.",
    ]
    text = texts[frame]
    assert writes(say(text)) == [], text


def _dialog(*texts):
    house = _house()
    _COUNTER[0] += 1
    house.conversation_id = f"prop-ellipsis-{_COUNTER[0]}"
    return [house.say(text) for text in texts]


@given(st.sampled_from(_SWITCH_LIGHTS), st.sampled_from(_SWITCH_LIGHTS), ON_OFF)
def test_ellipsis_with_a_new_object_never_writes_to_the_previous_target(first, second, switch):
    """A3: "Mach die Stehlampe an." -> "Und das Deckenlicht aus." """
    assume(first != second)
    (first_id, first_name), (second_id, second_name) = first, second
    _opening, follow = _dialog(f"Mach {first_name} an.", f"Und {second_name} {switch[0]}.")
    assert first_id not in _written(follow) or first_id == second_id, (follow.text, follow.speech)
    assert _written(follow) <= {second_id}, (follow.text, follow.speech)


_UNKNOWN_OBJECTS = st.sampled_from([
    "Deckenfluter", "Blumenkohl", "Wackeldackel", "Kuckucksuhr", "Quastenflosser", "Fliegenpilz",
])


@given(st.sampled_from(_SWITCH_LIGHTS), _UNKNOWN_OBJECTS, ON_OFF, st.sampled_from(["Und ", "", "Jetzt "]))
def test_ellipsis_with_an_unknown_object_never_writes_to_the_previous_target(first, word, switch, lead):
    """7.8.1: "Mach das Flurlicht an." -> "Und Deckenfluter aus." with a
    Deckenfluter HomeIntent does not know (not exposed): the unknown new
    object is never replaced by the previous target."""
    first_id, first_name = first
    entities = [entity for entity in _ENTITIES if word.casefold() not in entity.friendly_name.casefold()]
    assume(any(entity.entity_id == first_id for entity in entities))
    patch = pytest.MonkeyPatch()
    try:
        house = HouseConversation(patch, entities=entities, options=AUTO)
        _COUNTER[0] += 1
        house.conversation_id = f"prop-unknown-{_COUNTER[0]}"
        house.say(f"Mach {first_name} an.")
        follow = house.say(f"{lead}{word} {switch[0]}.")
    finally:
        patch.undo()
    assert _written(follow) == set(), (follow.text, follow.speech)


_SIDED = [
    (entity.entity_id, entity.friendly_name)
    for entity in _ENTITIES
    if entity.friendly_name.endswith((" links", " rechts"))
]


@given(st.sampled_from(_SIDED), st.sampled_from(["runter", "hoch", "an", "aus", "zu", "auf"]),
       st.sampled_from(["", "Und ", "Jetzt "]))
def test_ellipsis_with_another_side_never_writes_to_the_previous_side(sided, particle, lead):
    """A3: "…linken … hoch." -> "Den rechten runter." """
    entity_id, name = sided
    other = "rechten" if name.endswith("links") else "linken"
    _opening, follow = _dialog(f"Schalte {name} ein.", f"{lead}den {other} {particle}.")
    assert entity_id not in _written(follow), (follow.text, follow.speech)


@given(st.sampled_from(_SWITCH_LIGHTS), CLOCK, ON_OFF)
def test_ellipsis_with_a_time_never_runs_now(light, clock, switch):
    """A3: "Mach das Licht im Flur aus." -> "Morgen früh wieder an." """
    _opening, follow = _dialog(f"Mach {light[1]} aus.", f"{clock[:1].upper()}{clock[1:]} wieder {switch[0]}.")
    assert [call for call in writes(follow) if call[0] != "automation"] == [], (follow.text, follow.speech)


@given(st.sampled_from(_SWITCH_LIGHTS), st.sampled_from([place for place, _ids in _PLACE_LIGHTS] + ["oben", "unten", "im Keller"]),
       st.sampled_from(["{p} auch.", "Und {p} auch.", "{p} ebenfalls."]))
def test_ellipsis_never_widens_the_target_set(light, place, frame):
    """A3: "Mach das Licht im Flur an." -> "Oben auch." is at most one light."""
    _opening, follow = _dialog(f"Schalte {light[1]} ein.", frame.format(p=place[:1].upper() + place[1:]))
    assert len(_written(follow)) <= 1, (follow.text, follow.speech)


@given(st.sampled_from(_SWITCH_LIGHTS), st.sampled_from(_SWITCH_LIGHTS), st.one_of(st.none(), UNKNOWN),
       st.sampled_from(["Schalte {a} und {b} ein.", "Mach {a} an, dann {b}.", "{a} und {b} an."]))
def test_executed_parts_equal_named_parts_or_nothing(first, second, unknown, frame):
    """A4: every named part runs, or none does."""
    assume(first != second)
    names = (first[1], unknown or second[1])
    turn = say(frame.format(a=names[0], b=names[1]))
    written = _written(turn)
    expected = {first[0]} if unknown else {first[0], second[0]}
    if unknown:
        assert written == set(), (turn.text, turn.speech)
    else:
        assert written in (set(), expected), (turn.text, turn.speech)


@given(st.sampled_from([place for place, _ids in _PLACE_LIGHTS]), ON_OFF,
       st.sampled_from(["fahr die Rollläden runter", "mach die Heizung aus", "schalte den Fernseher aus"]))
def test_first_place_bounds_later_parts(place, switch, second):
    """A4 (SC-10): "Im Wohnzimmer das Licht aus und die Rollläden runter"
    never reaches devices of other rooms."""
    turn = say(f"Mach {place} das Licht {switch[0]} und {second}.")
    area = next(area for area, spoken, _count in _places_for("light") if spoken == place)
    by_id = {entity.entity_id: entity for entity in _ENTITIES}
    assert all(by_id[entity].area_id == area for entity in _written(turn) if entity in by_id), (turn.text, turn.speech)


_SATELLITE_CASES = [
    (genus, area)
    for genus in _SWITCHABLE
    if genus_members(genus.key, _ENTITIES)
    for area in sorted({entity.area_id for entity in _ENTITIES if entity.area_id})
    if not any(entity.area_id == area for entity in genus_members(genus.key, _ENTITIES))
]


@settings(max_examples=12)
@given(st.sampled_from(_SATELLITE_CASES), ON_OFF)
def test_satellite_room_is_never_left_silently(case, switch):
    """A5: without a place, a device of another room is only offered."""
    genus, area = case
    noun = genus.lemmas[0]
    # A device whose registry name is the kind word itself ("Luftbefeuchter")
    # is named, not described; a name is no description of the room.
    assume(not any(
        normalize_for_compare(name) == normalize_for_compare(noun)
        for entity in genus_members(genus.key, _ENTITIES)
        for name in (entity.friendly_name, *entity.aliases)
    ))
    patch = pytest.MonkeyPatch()
    try:
        house = HouseConversation(patch, area=area, options=AUTO)
        turn = house.say(f"Mach {_ARTICLE[genus.gender]} {noun} {switch[0]}.")
    finally:
        # The satellite room is patched module-wide; never leak it.
        patch.undo()
    assert _written(turn) == set(), (area, turn.text, turn.speech)


_HIGH_STEPS = [
    ({"action": "lock.unlock", "target": {"entity_id": "lock.haustuerschloss"}}, "Haustürschloss"),
    ({"action": "lock.lock", "target": {"entity_id": "lock.gartentor_schloss"}}, "Gartentor"),
    ({"action": "cover.open_cover", "target": {"entity_id": "cover.garagentor"}}, "Garagentor"),
]
_LOW_STEPS = [
    {"action": "light.turn_on", "target": {"entity_id": "light.stehlampe"}},
    {"action": "light.turn_off", "target": {"entity_id": "light.flurlicht"}},
]


@settings(max_examples=10)
@given(st.lists(st.sampled_from(_HIGH_STEPS), min_size=1, max_size=2, unique_by=lambda item: item[1]),
       st.lists(st.sampled_from(_LOW_STEPS), max_size=2), st.booleans())
def test_confirmation_names_every_high_effect(high, low, script_first):
    """A6: the question about a script names each HIGH/CRITICAL effect."""
    from homeintent.entities import EntitySnapshot

    entities = _ENTITIES + [EntitySnapshot("script.abendlauf", "Abendlauf", "script", "off")]
    patch = pytest.MonkeyPatch()
    try:
        house = HouseConversation(patch, entities=entities, options=AUTO)
        steps = [step for step, _name in high] + list(low)
        if not script_first:
            steps.reverse()
        _ha_stub.register_script(house.entity.hass, "script.abendlauf", steps)
        turn = house.say("Starte das Skript Abendlauf.")
    finally:
        patch.undo()
    assert _written(turn) == set(), turn.speech
    for step, name in high:
        target = next(entity for entity in _ENTITIES if entity.entity_id == step["target"]["entity_id"])
        assert target.friendly_name in turn.speech, (turn.speech, name)


# ------------------------------------------------------------ 7.8 B2/B3: frames and short forms
_FRAMES = st.sampled_from([
    "Sei so lieb und {c}", "Sei bitte so gut und {c}", "{C}, danke", "Danke dir, {c}",
    "{C}, ich muss arbeiten", "{C}, wir essen gleich", "{C}, schnell", "{C}, aber zügig",
    "Wenn du so nett wärst, {c}", "Wenn's geht, {c}", "Wäre super, wenn du {c}",
])


def _frame(frame: str, command: str) -> str:
    return frame.format(c=command[:1].lower() + command[1:], C=command)


@given(device_phrase(), ON_OFF, _FRAMES, st.booleans())
def test_frames_never_change_target_set_or_safety_form(device, switch, frame, negated):
    """B2: politeness, thanks, reasons and urgency change neither the
    targets nor the safety shape (a negation stays a negation)."""
    _genus, phrase, _nom, place, _count, _plural = device
    command = f"Mach {phrase} {place} {'nicht ' if negated else ''}{switch[0]}"
    plain = say(command + ".")
    framed = say(_frame(frame, command) + ".")
    assert _written(framed) <= _written(plain), (framed.text, framed.speech)
    if negated:
        assert _written(framed) == set(), (framed.text, framed.speech)


@given(device_phrase(), ON_OFF, st.sampled_from([
    "Kannst du mir sagen, ob {n} {p} {s} ist?", "Weißt du, ob {n} {p} {s} ist?",
    "Sag mir bitte, ob {n} {p} {s} ist.", "Ich frage mich, ob {n} {p} {s} ist.",
]))
def test_embedded_question_stays_a_question(device, switch, frame):
    _genus, _phrase, nominative, place, _count, _plural = device
    text = frame.format(n=nominative, p=place, s=switch[0])
    assert writes(say(text)) == [], text


@given(device_phrase(plural=False), ON_OFF)
def test_short_command_never_writes_more_than_the_full_command(device, switch):
    """B3: "<Gerät> <Ort> an" writes nothing the full command would not."""
    genus, _phrase, _nom, place, _count, _plural = device
    noun = genus.lemmas[0]
    short = say(f"{noun} {place} {switch[0]}")
    full = say(f"Schalte {_ARTICLE[genus.gender]} {noun} {place} {switch[1]}.")
    assert _written(short) <= _written(full), (short.text, short.speech)


# ------------------------------------------------------------ fixed regressions
# Counterexamples from nightly runs are pinned here (sentence, forbidden writes).
REGRESSIONS: list[str] = []


@pytest.mark.parametrize("sentence", REGRESSIONS or ["Mach das Licht im Büro nicht an."])
def test_pinned_counterexamples_never_write(sentence):
    assert writes(say(sentence)) == []
