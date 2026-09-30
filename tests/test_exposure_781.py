"""7.8.1: scripts and groups against the real exposure.

Two owner reports: "Aktiviere Schlafen" was refused because the script
names ``light.musikanlage``, an entity Home Assistant no longer knows; and
"Schalte Ambiente ein" was refused for "Kücheninsel" although a device of
that name is exposed. A deleted entity switches nothing, so it is not an
unexposed device; a hidden entity that shares its name with an exposed one
is named with its entity id, and the answer says where it is released.
Everything that exists and is hidden is still refused.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeassistant.core import HomeAssistant  # noqa: E402

from homeintent.effect_graph import build_plan_effects, summarize_effects  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.execution_policy import (  # noqa: E402
    PolicyOutcome,
    evaluate_service_plan,
    validate_automation_action_targets,
)
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.service_executor import async_execute_service_plan  # noqa: E402

SCRIPT = ServiceCallPlan("script", "turn_on", "script.schlafen")
GROUP = ServiceCallPlan("light", "turn_on", "light.ambiente")


def _state(hass, entity_id: str, name: str, members: list[str] | None = None) -> None:
    attributes: dict = {"friendly_name": name}
    if members:
        attributes["entity_id"] = members
    hass.states._states[entity_id] = types.SimpleNamespace(
        entity_id=entity_id, state="off", attributes=attributes,
    )


def _snap(entity_id: str, name: str, **kwargs) -> EntitySnapshot:
    return EntitySnapshot(entity_id, name, entity_id.split(".", 1)[0], "off", **kwargs)


def _script_house() -> tuple[HomeAssistant, list[EntitySnapshot]]:
    hass = HomeAssistant()
    _ha_stub.register_script(hass, "script.schlafen", [
        {"action": "light.turn_off", "target": {"entity_id": ["light.stehlampe", "light.musikanlage"]}},
    ])
    # The script names light.musikanlage, which Home Assistant does not know.
    hass.states._states.pop("light.musikanlage")
    _state(hass, "script.schlafen", "Schlafen")
    _state(hass, "light.stehlampe", "Stehlampe")
    return hass, [_snap("script.schlafen", "Schlafen"), _snap("light.stehlampe", "Stehlampe")]


def test_a_deleted_entity_in_a_script_is_not_an_unexposed_device():
    hass, entities = _script_house()
    effects = build_plan_effects(hass, SCRIPT)
    assert effects is not None
    assert effects.missing == {"light.musikanlage"}
    assert effects.effective_targets == {"light.stehlampe"}
    decision = evaluate_service_plan(SCRIPT, entities, {}, is_admin=True, user_id="admin", effects=effects)
    assert decision.outcome is PolicyOutcome.ALLOW, decision.reason
    assert summarize_effects(effects) == "1 Licht"


def test_the_script_with_a_deleted_entity_runs():
    hass, entities = _script_house()
    result = asyncio.run(async_execute_service_plan(
        hass, SCRIPT, entities, {}, is_admin=True, user_id="admin", confirmed=False,
    ))
    assert result.executed, result.error
    hass.services.async_call.assert_awaited_once()
    assert hass.services.async_call.await_args.args[:2] == ("script", "turn_on")


def test_an_existing_hidden_device_is_still_refused():
    hass, entities = _script_house()
    _state(hass, "light.musikanlage", "Musikanlage")
    result = asyncio.run(async_execute_service_plan(
        hass, SCRIPT, entities, {}, is_admin=True, user_id="admin", confirmed=True,
    ))
    assert result.executed is False
    assert "Musikanlage" in (result.error or "")
    hass.services.async_call.assert_not_awaited()


def _group_house(options: dict) -> tuple[HomeAssistant, list[EntitySnapshot], dict]:
    hass = HomeAssistant()
    _state(hass, "light.ambiente", "Ambiente", ["light.kuecheninsel", "light.kuecheninsel_2"])
    _state(hass, "light.kuecheninsel", "Kücheninsel")
    _state(hass, "light.kuecheninsel_2", "Kücheninsel")
    entities = [
        _snap("light.ambiente", "Ambiente", attributes={"entity_id": ["light.kuecheninsel", "light.kuecheninsel_2"]}),
        _snap("light.kuecheninsel", "Kücheninsel"),
    ]
    return hass, entities, options


def test_a_hidden_namesake_is_named_with_its_entity_id():
    hass, entities, options = _group_house({})
    decision = evaluate_service_plan(
        GROUP, entities, options, is_admin=True, user_id="admin", effects=build_plan_effects(hass, GROUP),
    )
    assert decision.outcome is PolicyOutcome.DENY
    reason = decision.reason or ""
    assert "Kücheninsel (light.kuecheninsel_2)" in reason
    assert "Einstellungen, Sprachassistenten, Entitäten freigeben" in reason


def test_a_fixed_selection_is_named_as_the_cause():
    hass, entities, options = _group_house({"selected_entities": ["light.ambiente", "light.kuecheninsel"]})
    decision = evaluate_service_plan(
        GROUP, entities, options, is_admin=True, user_id="admin", effects=build_plan_effects(hass, GROUP),
    )
    assert decision.outcome is PolicyOutcome.DENY
    reason = decision.reason or ""
    assert "feste Geräteauswahl" in reason and "Optionen von HomeIntent" in reason


def test_an_automation_still_counts_an_entity_missing_today():
    # An automation runs later, when the entity may exist again.
    hass, _entities = _script_house()
    effects = build_plan_effects(hass, SCRIPT)
    reason = validate_automation_action_targets(
        frozenset({"script.schlafen"}), {}, effects=effects,
        exposed_ids={"script.schlafen", "light.stehlampe"},
    )
    assert reason is not None and "light.musikanlage" in reason
