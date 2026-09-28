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
from homeintent.entities import EntitySnapshot  # noqa: E402
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


@given(script_steps(), st.data())
def test_unexposed_effective_target_never_writes(steps, data):
    import asyncio

    from homeassistant.core import HomeAssistant
    from homeintent.service_executor import async_execute_service_plan

    entities = _entities_for(steps, ["script.x"])
    hidden = data.draw(st.sampled_from([entity for entity in entities if entity.entity_id != "script.x"]))
    exposed = [entity for entity in entities if entity.entity_id != hidden.entity_id]
    hass = HomeAssistant()
    _ha_stub.register_script(hass, "script.x", steps)
    result = asyncio.run(async_execute_service_plan(
        hass, SCRIPT, exposed, {"effect_graph_unknown": "confirm"}, is_admin=True, user_id="admin",
        confirmed=True,
    ))
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
    house = HouseConversation(pytest.MonkeyPatch(), entities=entities, options=AUTO)
    assert writes(house.say(f"Mach die Leselampe {particle}.")) == []


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
    house = HouseConversation(pytest.MonkeyPatch(), entities=entities, options={"implicit_action_level": level})
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
    house = HouseConversation(pytest.MonkeyPatch(), entities=entities, options=AUTO)
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
    house = HouseConversation(pytest.MonkeyPatch(), options=AUTO)
    asyncio.run(house.entity._runtime_data.bindings.async_bind(
        BindingKind.MACRO, name, "macro", confirmed=True, scope=BindingScope.HOUSEHOLD,
        now=datetime(2026, 9, 28), data={"spoken": name, "body": body, "entity_ids": [target]},
    ))
    direct = HouseConversation(pytest.MonkeyPatch(), options=AUTO).say(body)
    via_macro = house.say(f"{name}.")
    assert bool(writes(via_macro)) <= bool(writes(direct)), (body, via_macro.speech)


def _ids(data: dict) -> list[str]:
    raw = data.get("entity_id", [])
    return [raw] if isinstance(raw, str) else list(raw)


# ------------------------------------------------------------ fixed regressions
# Counterexamples from nightly runs are pinned here (sentence, forbidden writes).
REGRESSIONS: list[str] = []


@pytest.mark.parametrize("sentence", REGRESSIONS or ["Mach das Licht im Büro nicht an."])
def test_pinned_counterexamples_never_write(sentence):
    assert writes(say(sentence)) == []
