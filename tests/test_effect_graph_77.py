"""7.7 B7: EffectGraph hardening beyond the 40 cases of 7.3.1.

Principle: what cannot be determined statically yields ``complete = False``
and is handled fail-closed. No heuristic makes something unknown LOW.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeassistant.core import HomeAssistant  # noqa: E402

from homeintent.effect_graph import build_effect_graph, build_plan_effects  # noqa: E402
from homeintent.execution_policy import PolicyOutcome, evaluate_service_plan  # noqa: E402
from homeintent.risk import RiskLevel  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.service_executor import (  # noqa: E402
    CHANGED_SINCE_CONFIRMATION,
    async_execute_service_plan,
    confirmed_scope,
)
from test_effect_graph import FakeSources, decide, snap  # noqa: E402

SCRIPT = ServiceCallPlan("script", "turn_on", "script.routine")
EXISTING = {"light.a", "light.b", "lock.haustuer", "vacuum.robbi", "switch.x"}


def _graph(sequence, **kwargs):
    sources = FakeSources(scripts={"script.routine": sequence, **kwargs.pop("scripts", {})},
                          existing=EXISTING, **kwargs)
    return build_effect_graph("script.routine", sources)


# ------------------------------------------------------ script parameters
def test_script_fields_used_as_target_are_incomplete():
    graph = _graph(
        [{"action": "light.turn_on", "target": {"entity_id": "{{ target }}"}}],
    )
    assert graph.complete is False


def test_script_turn_on_with_entity_in_variables_is_incomplete():
    graph = _graph([{
        "action": "script.turn_on",
        "target": {"entity_id": "script.inner"},
        "data": {"variables": {"target": "lock.haustuer"}},
    }], scripts={"script.inner": [{"action": "light.turn_on", "target": {"entity_id": "light.a"}}]})
    assert graph.complete is False
    assert "variables" in graph.unknown[0].reason


def test_variables_without_entities_keep_a_static_script_complete():
    graph = _graph([{
        "action": "script.turn_on",
        "target": {"entity_id": "script.inner"},
        "data": {"variables": {"brightness": 40}},
    }], scripts={"script.inner": [{"action": "light.turn_on", "target": {"entity_id": "light.a"}}]})
    assert graph.complete is True
    assert graph.effective_targets == {"light.a"}


def test_nested_list_of_mappings_in_data_is_scanned():
    graph = _graph([{
        "action": "light.turn_on",
        "target": {"entity_id": "light.a"},
        "data": {"extra": [{"deep": {"also": "vacuum.robbi"}}]},
    }])
    assert graph.complete is False


# ------------------------------------------------------ generic services
@pytest.mark.parametrize("service", ["turn_on", "turn_off", "toggle"])
def test_homeassistant_services_expand_groups_recursively(service):
    sources = FakeSources(
        scripts={"script.routine": [{"action": f"homeassistant.{service}",
                                     "target": {"entity_id": "group.aussen"}}]},
        groups={"group.aussen": ("group.tuer", "light.a"), "group.tuer": ("lock.haustuer",)},
        existing=EXISTING,
    )
    graph = build_effect_graph("script.routine", sources)
    assert graph.complete is True
    assert graph.effective_targets == {"light.a", "lock.haustuer"}
    assert {"group.aussen", "group.tuer"} <= set(graph.nested)


def test_homeassistant_turn_on_on_a_script_runs_its_content():
    graph = _graph(
        [{"action": "homeassistant.turn_on", "target": {"entity_id": "script.inner"}}],
        scripts={"script.inner": [{"action": "lock.unlock", "target": {"entity_id": "lock.haustuer"}}]},
    )
    assert graph.effective_targets == {"lock.haustuer"}


def test_group_target_in_a_plan_is_checked_like_its_members():
    sources = FakeSources(groups={"group.alles": ("light.a", "lock.haustuer")}, existing=EXISTING)
    plan = ServiceCallPlan("homeassistant", "turn_off", "group.alles")
    entities = [snap("group.alles"), snap("light.a"), snap("lock.haustuer")]
    decision = decide(plan, entities, sources)
    assert decision.effects is not None and decision.effects.effective_targets == {"light.a", "lock.haustuer"}


# ------------------------------------------- scene and automation services
def test_scene_create_then_activate_uses_the_created_states():
    graph = _graph([
        {"action": "scene.create", "data": {"scene_id": "vorher",
                                            "entities": {"lock.haustuer": "unlocked"}}},
        {"action": "scene.turn_on", "target": {"entity_id": "scene.vorher"}},
    ])
    assert graph.complete is True
    assert [(effect.domain, effect.service) for effect in graph.effects] == [("lock", "unlock")]


def test_scene_create_snapshot_then_activate_is_incomplete():
    graph = _graph([
        {"action": "scene.create", "data": {"scene_id": "vorher", "snapshot_entities": ["light.a"]}},
        {"scene": "scene.vorher"},
    ])
    assert graph.complete is False


def test_scene_create_alone_changes_nothing():
    graph = _graph([
        {"action": "scene.create", "data": {"scene_id": "vorher", "entities": {"light.a": "on"}}},
    ])
    assert graph.complete is True and graph.effects == ()


def test_scene_apply_with_entities_in_data_is_an_effect():
    graph = _graph([{"action": "scene.apply", "data": {"entities": {"lock.haustuer": "unlocked"}}}])
    assert [(effect.domain, effect.service) for effect in graph.effects] == [("lock", "unlock")]


def test_scene_apply_with_template_states_is_incomplete():
    graph = _graph([{"action": "scene.apply", "data": {"entities": "{{ states }}"}}])
    assert graph.complete is False


@pytest.mark.parametrize("skip", [True, False])
def test_automation_trigger_counts_the_automation_actions_regardless_of_skip_condition(skip):
    graph = _graph(
        [{"action": "automation.trigger", "target": {"entity_id": "automation.tuer"},
          "data": {"skip_condition": skip}}],
        automations={"automation.tuer": [{"action": "lock.unlock", "target": {"entity_id": "lock.haustuer"}}]},
    )
    assert graph.complete is True
    assert graph.effective_targets == {"lock.haustuer"}


# ------------------------------------------------- further targets in data
def test_entities_key_of_a_foreign_service_is_a_further_target():
    graph = _graph([{"action": "group.set", "data": {"object_id": "x", "entities": ["lock.haustuer"]}}])
    assert graph.complete is False


def test_foreign_entity_id_field_in_data_is_a_further_target():
    graph = _graph([{"action": "media_player.join", "target": {"entity_id": "light.a"},
                     "data": {"group_members": ["vacuum.robbi"]}}])
    assert graph.complete is False


def test_unknown_is_never_low():
    sources = FakeSources(
        scripts={"script.routine": [{"action": "light.turn_on", "target": {"entity_id": "light.a"},
                                     "data": {"variables": {"x": "lock.haustuer"}}}]},
        existing=EXISTING,
    )
    entities = [snap("script.routine"), snap("light.a"), snap("lock.haustuer")]
    decision = decide(SCRIPT, entities, sources, options={"effect_graph_unknown": "confirm"})
    assert decision.risk >= RiskLevel.HIGH
    assert decide(SCRIPT, entities, sources).outcome is PolicyOutcome.DENY


# ------------------------------------------ mutation between question and "Ja"
def test_mutation_regression_light_script_changed_to_unlock_needs_a_new_confirmation():
    """Architecture rule (kept as explicit regression):

    1. the script only switches a light, HomeIntent asks (inferred routine),
    2. the script is changed to ``lock.unlock``,
    3. the user says "Ja",
    4. a new policy decision (CRITICAL) - nothing runs on the old "Ja".
    """
    hass = HomeAssistant()
    entities = [snap("script.routine"), snap("light.a"), snap("lock.haustuer")]
    _ha_stub.register_script(hass, "script.routine", [
        {"action": "light.turn_on", "target": {"entity_id": "light.a"}},
    ])
    scope = confirmed_scope(hass, (SCRIPT,), entities, {})
    assert scope.risk is RiskLevel.LOW and scope.targets == {"script.routine", "light.a"}
    _ha_stub.register_script(hass, "script.routine", [
        {"action": "lock.unlock", "target": {"entity_id": "lock.haustuer"}},
    ])
    fresh = evaluate_service_plan(
        SCRIPT, entities, {}, is_admin=True, user_id="admin", effects=build_plan_effects(hass, SCRIPT),
    )
    assert fresh.risk is RiskLevel.CRITICAL
    result = asyncio.run(async_execute_service_plan(
        hass, SCRIPT, entities, {}, is_admin=True, user_id="admin", confirmed=True, scope=scope,
    ))
    assert result.executed is False
    assert result.error == CHANGED_SINCE_CONFIRMATION
    hass.services.async_call.assert_not_awaited()


def test_unchanged_script_runs_on_its_confirmation():
    hass = HomeAssistant()
    entities = [snap("script.routine"), snap("light.a")]
    _ha_stub.register_script(hass, "script.routine", [
        {"action": "light.turn_on", "target": {"entity_id": "light.a"}},
    ])
    scope = confirmed_scope(hass, (SCRIPT,), entities, {})
    result = asyncio.run(async_execute_service_plan(
        hass, SCRIPT, entities, {}, is_admin=True, user_id="admin", confirmed=True, scope=scope,
    ))
    assert result.executed is True


def test_conversation_ja_after_mutation_executes_nothing(monkeypatch):
    """The same regression through the real conversation: question, edit, "Ja".

    In 7.6.1 this "Ja" unlocked the front door (found while hardening).
    """
    from _testhaus import HouseConversation, house_entities

    entities = house_entities() + [snap("script.leselicht", "Leselicht")]
    house = HouseConversation(monkeypatch, entities=entities)
    hass = house.entity.hass
    _ha_stub.register_script(hass, "script.leselicht", [
        {"action": "light.turn_on", "target": {"entity_id": "light.stehlampe"}},
    ])
    first = house.say("Ich will lesen.")
    assert "Leselicht" in first.speech and first.calls == []
    _ha_stub.register_script(hass, "script.leselicht", [
        {"action": "lock.unlock", "target": {"entity_id": "lock.haustuerschloss"}},
    ])
    second = house.say("Ja.")
    assert second.calls == []
    assert CHANGED_SINCE_CONFIRMATION in second.speech
