"""7.8.2: a script, scene or group the user names runs even when it switches
devices that are not exposed (owner decision: "Schlafen" switches everything
off, "Ambiente" is a light group).

``routine_unexposed_effects``: allow (default) runs, confirm asks first and
names the devices, deny refuses as before. In every mode: locks, alarm
panels, covers/gates and valves inside a routine are never switched unless
exposed, and implicit, inferred or unattended plans are refused.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeassistant.core import HomeAssistant  # noqa: E402

from homeintent.effect_graph import build_plan_effects  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.execution_policy import (  # noqa: E402
    PolicyOutcome,
    evaluate_service_plan,
    validate_automation_action_targets,
)
from homeintent.plan_origin import PlanOrigin  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402

SCHLAFEN = ServiceCallPlan("script", "turn_on", "script.schlafen")
AMBIENTE = ServiceCallPlan("light", "turn_on", "light.ambiente")


def _state(hass, entity_id: str, name: str, members: list[str] | None = None) -> None:
    attributes: dict = {"friendly_name": name}
    if members:
        attributes["entity_id"] = members
    hass.states._states[entity_id] = types.SimpleNamespace(
        entity_id=entity_id, state="off", attributes=attributes,
    )


def _snap(entity_id: str, name: str, **kwargs) -> EntitySnapshot:
    return EntitySnapshot(entity_id, name, entity_id.split(".", 1)[0], "off", **kwargs)


def _schlafen(extra_step: dict | None = None):
    """All lights off, TV and LED bed light on; Poleraum is not exposed."""
    hass = HomeAssistant()
    steps = [
        {"action": "light.turn_off", "target": {"entity_id": ["light.wohnzimmer", "light.poleraum_leuchtschrift"]}},
        {"action": "media_player.turn_on", "target": {"entity_id": "media_player.samsung"}},
        {"action": "light.turn_on", "target": {"entity_id": "light.led_bett"}},
    ]
    if extra_step:
        steps.append(extra_step)
    _ha_stub.register_script(hass, "script.schlafen", steps)
    _state(hass, "script.schlafen", "Schlafen")
    _state(hass, "light.wohnzimmer", "Wohnzimmer")
    _state(hass, "light.poleraum_leuchtschrift", "Poleraum Leuchtschrift")
    _state(hass, "media_player.samsung", "Samsung 8 Series (43)")
    _state(hass, "light.led_bett", "LED Bett")
    entities = [
        _snap("script.schlafen", "Schlafen"),
        _snap("light.wohnzimmer", "Wohnzimmer"),
        _snap("media_player.samsung", "Samsung 8 Series (43)"),
        _snap("light.led_bett", "LED Bett"),
    ]
    return hass, entities


def _decide(hass, entities, plan=SCHLAFEN, options=None, **kwargs):
    return evaluate_service_plan(
        plan, entities, options or {}, is_admin=True, user_id="admin",
        effects=build_plan_effects(hass, plan), **kwargs,
    )


def test_a_named_script_with_a_hidden_light_runs_by_default():
    hass, entities = _schlafen()
    decision = _decide(hass, entities)
    assert decision.outcome is PolicyOutcome.ALLOW, decision.reason


def test_a_named_light_group_with_a_hidden_member_runs_by_default():
    hass = HomeAssistant()
    _state(hass, "light.ambiente", "Ambiente", ["light.stehlampe", "light.kuecheninsel"])
    _state(hass, "light.stehlampe", "Stehlampe")
    _state(hass, "light.kuecheninsel", "Kücheninsel")
    entities = [
        _snap("light.ambiente", "Ambiente", attributes={"entity_id": ["light.stehlampe", "light.kuecheninsel"]}),
        _snap("light.stehlampe", "Stehlampe"),
    ]
    decision = _decide(hass, entities, plan=AMBIENTE)
    assert decision.outcome is PolicyOutcome.ALLOW, decision.reason


def test_confirm_mode_asks_and_names_the_hidden_devices():
    hass, entities = _schlafen()
    decision = _decide(hass, entities, options={"routine_unexposed_effects": "confirm"})
    assert decision.outcome is PolicyOutcome.CONFIRM
    assert "Poleraum Leuchtschrift" in (decision.note or "")
    assert "nichts ausgeführt" not in (decision.note or "")


def test_deny_mode_refuses_as_before():
    hass, entities = _schlafen()
    decision = _decide(hass, entities, options={"routine_unexposed_effects": "deny"})
    assert decision.outcome is PolicyOutcome.DENY
    assert "Poleraum Leuchtschrift" in (decision.reason or "")


@pytest.mark.parametrize("step", [
    {"action": "lock.unlock", "target": {"entity_id": "lock.haustuer"}},
    {"action": "cover.open_cover", "target": {"entity_id": "cover.garagentor"}},
    {"action": "alarm_control_panel.alarm_disarm", "target": {"entity_id": "alarm_control_panel.haus"}},
    {"action": "valve.open_valve", "target": {"entity_id": "valve.wasser"}},
])
@pytest.mark.parametrize("mode", ["allow", "confirm"])
def test_a_hidden_guarded_device_is_never_allowed(step, mode):
    hass, entities = _schlafen(step)
    decision = _decide(hass, entities, options={"routine_unexposed_effects": mode})
    assert decision.outcome is PolicyOutcome.DENY


@pytest.mark.parametrize("origin", [
    PlanOrigin.IMPLICIT_NEED, PlanOrigin.INFERRED_ROUTINE, PlanOrigin.PROACTIVE_PROPOSAL,
    PlanOrigin.STANDING_PERMISSION,
])
def test_only_an_explicit_command_may_reach_hidden_devices(origin):
    hass, entities = _schlafen()
    assert _decide(hass, entities, origin=origin).outcome is PolicyOutcome.DENY


def test_an_unattended_plan_never_reaches_hidden_devices():
    hass, entities = _schlafen()
    assert _decide(hass, entities, attended=False).outcome is PolicyOutcome.DENY


def test_an_automation_still_refuses_hidden_devices():
    hass, entities = _schlafen()
    reason = validate_automation_action_targets(
        frozenset({"script.schlafen"}), {}, effects=build_plan_effects(hass, SCHLAFEN),
        exposed_ids={entity.entity_id for entity in entities},
    )
    assert reason is not None and "Poleraum Leuchtschrift" in reason


def test_a_question_the_plan_gets_anyway_names_the_hidden_devices():
    # A button in the routine is HIGH risk: the confirmation names both.
    hass, entities = _schlafen({"action": "button.press", "target": {"entity_id": "button.kaffee"}})
    _state(hass, "button.kaffee", "Kaffee")
    entities.append(_snap("button.kaffee", "Kaffee"))
    decision = _decide(hass, entities)
    assert decision.outcome is PolicyOutcome.CONFIRM
    assert "Poleraum Leuchtschrift" in (decision.note or "")


def test_the_conversation_runs_the_script(monkeypatch):
    from _testhaus import HouseConversation

    house = HouseConversation(monkeypatch, entities=[
        _snap("script.schlafen", "Schlafen"),
        _snap("light.wohnzimmer", "Wohnzimmer", capabilities=frozenset({"TURN_ON", "TURN_OFF"})),
    ])
    hass = house.entity.hass
    hass.data.pop("script", None)
    _ha_stub.register_script(hass, "script.schlafen", [
        {"action": "light.turn_off", "target": {"entity_id": ["light.wohnzimmer", "light.poleraum_leuchtschrift"]}},
    ])
    _state(hass, "light.poleraum_leuchtschrift", "Poleraum Leuchtschrift")
    turn = house.say("Aktiviere Schlafen.")
    assert ("script", "turn_on", {"entity_id": "script.schlafen"}) in turn.calls, turn.speech
