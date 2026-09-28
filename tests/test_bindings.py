"""Phase 3 (7.3.3): bindings store, routine bindings, implicit action policy."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeintent.bindings import (  # noqa: E402
    BindingKind,
    BindingScope,
    BindingState,
    BindingStore,
    binding_state,
)
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.execution_policy import PolicyOutcome, evaluate_service_plan  # noqa: E402
from homeintent.plan_origin import PlanOrigin  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402

NOW = datetime(2026, 9, 28, 22, 0)


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ store
def test_binding_requires_confirmation(tmp_path):
    store = BindingStore(tmp_path / "b.json")
    with pytest.raises(ValueError):
        run(store.async_bind(BindingKind.ROUTINE, "sleep", "script.x", confirmed=False, now=NOW))


def test_binding_lifecycle_and_scope(tmp_path):
    path = tmp_path / "b.json"
    store = BindingStore(path)
    household = run(store.async_bind(BindingKind.ROUTINE, "sleep", "script.gute_nacht", confirmed=True, now=NOW))
    personal = run(store.async_bind(
        BindingKind.ROUTINE, "sleep", "script.schlafen", confirmed=True, now=NOW,
        scope=BindingScope.USER, user_id="anna",
    ))
    assert store.find(BindingKind.ROUTINE, "sleep", "anna").binding_id == personal.binding_id
    assert store.find(BindingKind.ROUTINE, "sleep", "philipp").binding_id == household.binding_id
    reloaded = BindingStore(path)
    run(reloaded.async_load())
    assert {item.target for item in reloaded.all()} == {"script.gute_nacht", "script.schlafen"}
    run(reloaded.async_record_use(household.binding_id, NOW))
    assert reloaded.get(household.binding_id).uses == 1
    run(reloaded.async_remove_key(BindingKind.ROUTINE, "sleep", "anna"))
    assert reloaded.find(BindingKind.ROUTINE, "sleep", "anna").target == "script.gute_nacht"


def test_unconfirmed_entries_are_never_loaded_and_v0_is_migrated(tmp_path):
    path = tmp_path / "b.json"
    path.write_text(json.dumps([
        {"key": "sleep", "target": "script.a", "confirmed": True},
        {"key": "movie", "target": "scene.b", "confirmed": False},
    ]), encoding="utf-8")
    store = BindingStore(path)
    run(store.async_load())
    assert [(item.kind, item.target) for item in store.all()] == [(BindingKind.ROUTINE, "script.a")]


def test_binding_state_reports_missing_and_unexposed_targets(tmp_path):
    store = BindingStore(tmp_path / "b.json")
    binding = run(store.async_bind(BindingKind.ROUTINE, "sleep", "script.a", confirmed=True, now=NOW))
    assert binding_state(binding, {"script.a"}, {"script.a"}) is BindingState.VALID
    assert binding_state(binding, {"script.a"}, set()) is BindingState.NOT_EXPOSED
    assert binding_state(binding, set(), set()) is BindingState.TARGET_MISSING


# ------------------------------------------------------------ policy matrix
LIGHT = EntitySnapshot("light.a", "Licht", "light", "off")
LOCK = EntitySnapshot("lock.tuer", "Tür", "lock", "unlocked")
LOW = ServiceCallPlan("homeassistant", "turn_on", "light.a")
HIGH = ServiceCallPlan("lock", "lock", "lock.tuer")


@pytest.mark.parametrize("level", ["understand_only", "propose", "low_risk_auto", "bound_routines_auto"])
@pytest.mark.parametrize("origin", [PlanOrigin.IMPLICIT_NEED, PlanOrigin.INFERRED_ROUTINE])
@pytest.mark.parametrize("plan", [LOW, HIGH])
@pytest.mark.parametrize("bound", [False, True])
def test_non_explicit_origin_is_never_looser_than_explicit(level, origin, plan, bound):
    options = {"implicit_action_level": level}
    explicit = evaluate_service_plan(plan, [LIGHT, LOCK], options, is_admin=True, user_id="admin")
    implicit = evaluate_service_plan(
        plan, [LIGHT, LOCK], options, is_admin=True, user_id="admin", origin=origin,
        binding_confirmed=bound,
    )
    strictness = {PolicyOutcome.ALLOW: 0, PolicyOutcome.CONFIRM: 1, PolicyOutcome.DENY: 2}
    assert strictness[implicit.outcome] >= strictness[explicit.outcome]
    if level == "understand_only":
        assert implicit.outcome is PolicyOutcome.DENY
    if level == "propose":
        assert implicit.outcome is not PolicyOutcome.ALLOW
    if origin is PlanOrigin.INFERRED_ROUTINE and not (level == "bound_routines_auto" and bound):
        assert implicit.outcome is not PolicyOutcome.ALLOW
    if origin is PlanOrigin.IMPLICIT_NEED and level in {"low_risk_auto", "bound_routines_auto"} and plan is LOW:
        assert implicit.outcome is PolicyOutcome.ALLOW
    if plan is HIGH:
        assert implicit.outcome is not PolicyOutcome.ALLOW


# ------------------------------------------------------------ conversation
def _house(monkeypatch, options=None):
    import types

    from _testhaus import HouseConversation

    entities = [
        EntitySnapshot("script.schlafen", "Schlafen", "script", "off"),
        EntitySnapshot("script.gute_nacht", "Gute Nacht", "script", "off"),
        EntitySnapshot("scene.filmabend", "Filmabend", "scene", "unknown"),
        EntitySnapshot("light.wohnzimmer", "Wohnzimmerlicht", "light", "on",
                       area_id="wohnzimmer", area_name="Wohnzimmer",
                       capabilities=frozenset({"TURN_ON", "TURN_OFF"})),
    ]
    house = HouseConversation(monkeypatch, entities=entities, options=options)
    hass = house.entity.hass
    hass.data.pop("script", None)
    hass.data.pop("scene", None)
    _ha_stub.register_script(hass, "script.schlafen", [
        {"action": "light.turn_off", "target": {"entity_id": "light.wohnzimmer"}},
    ])
    _ha_stub.register_script(hass, "script.gute_nacht", [
        {"action": "vacuum.start", "target": {"entity_id": "vacuum.saugroboter"}},
    ])
    _ha_stub.register_scene(hass, "scene.filmabend", {"light.wohnzimmer": "off"})
    hass.states._states["vacuum.saugroboter"] = types.SimpleNamespace(
        entity_id="vacuum.saugroboter", state="docked", attributes={"friendly_name": "Saugroboter"},
    )
    return house


def _bindings(house):
    return house.entity._runtime_data.bindings.all(BindingKind.ROUTINE)


def test_choice_binds_and_bound_routine_is_used_without_name_search(monkeypatch):
    house = _house(monkeypatch)
    ask = house.say("Ich gehe schlafen.")
    assert ask.calls == [] and "Schlafen" in ask.speech and "Gute Nacht" in ask.speech
    chosen = house.say("Schlafen.")
    assert chosen.targets == {"script.schlafen"}, chosen.speech
    assert "merke ich mir" in chosen.speech
    assert [(item.key, item.target) for item in _bindings(house)] == [("sleep", "script.schlafen")]
    # Default "propose": the bound routine is proposed, no other candidate named.
    again = house.say("Ich gehe schlafen.")
    assert again.calls == [] and "Schlafen" in again.speech and "Gute Nacht" not in again.speech
    assert house.say("Ja.").targets == {"script.schlafen"}
    assert _bindings(house)[0].uses >= 0


def test_bound_routines_auto_runs_the_bound_routine_directly(monkeypatch):
    house = _house(monkeypatch, {"implicit_action_level": "bound_routines_auto"})
    house.say("Ich gehe schlafen.")
    house.say("Schlafen.")
    direct = house.say("Ich gehe schlafen.")
    assert direct.targets == {"script.schlafen"}, direct.speech
    concept = house.say("Starte die Schlafroutine.")
    assert concept.targets == {"script.schlafen"}, concept.speech


def test_choosing_a_routine_with_foreign_devices_is_refused_and_not_bound(monkeypatch):
    house = _house(monkeypatch)
    house.say("Ich gehe schlafen.")
    refused = house.say("Gute Nacht.")
    assert refused.calls == [] and "Saugroboter" in refused.speech, refused.speech
    assert "nichts gespeichert" in refused.speech
    assert _bindings(house) == ()


def test_single_candidate_yes_runs_and_binds(monkeypatch):
    house = _house(monkeypatch)
    ask = house.say("Ich will fernsehen.")
    assert ask.calls == [] and "Filmabend" in ask.speech and ask.speech.endswith("?"), ask.speech
    yes = house.say("Ja.")
    assert yes.targets == {"scene.filmabend"}
    assert "merke ich mir" in yes.speech
    assert [(item.key, item.target) for item in _bindings(house)] == [("movie", "scene.filmabend")]


def test_spoken_binding_management(monkeypatch):
    house = _house(monkeypatch)
    ask = house.say("Schlafen ist ab jetzt das Skript Schlafen.")
    assert ask.calls == [] and "künftig" in ask.speech, ask.speech
    assert _bindings(house) == ()
    house.say("Ja.")
    assert [(item.key, item.target) for item in _bindings(house)] == [("sleep", "script.schlafen")]
    shown = house.say("Welche Routine nutzt du für das Schlafen?")
    assert "Schlafen" in shown.speech
    forgotten = house.say("Vergiss die Schlafroutine.")
    assert "keine Routine mehr" in forgotten.speech and _bindings(house) == ()
    asked_again = house.say("Ich gehe schlafen.")
    assert "Gute Nacht" in asked_again.speech


def test_bound_target_no_longer_exposed_does_nothing_and_offers_rebinding(monkeypatch):
    house = _house(monkeypatch)
    house.say("Ich gehe schlafen.")
    house.say("Schlafen.")
    house.entities = [item for item in house.entities if item.entity_id != "script.schlafen"]
    turn = house.say("Ich gehe schlafen.")
    assert turn.calls == [], turn.speech
    assert "nicht mehr freigegeben" in turn.speech and "ab jetzt" in turn.speech


def test_personal_binding_wins_over_household(tmp_path):
    store = BindingStore(tmp_path / "b.json")
    run(store.async_bind(BindingKind.ROUTINE, "movie", "scene.a", confirmed=True, now=NOW))
    run(store.async_bind(BindingKind.ROUTINE, "movie", "scene.b", confirmed=True, now=NOW,
                         scope=BindingScope.USER, user_id="anna"))
    assert store.find(BindingKind.ROUTINE, "movie", "anna").target == "scene.b"
    assert store.find(BindingKind.ROUTINE, "movie", "admin").target == "scene.a"
    assert store.find(BindingKind.ROUTINE, "movie", None).target == "scene.a"
