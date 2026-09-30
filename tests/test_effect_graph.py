"""Phase 1 (7.3.1): transitive safety of scripts, scenes and groups.

Covers the EffectGraph core (hass-free, through a fake ``EffectSources``),
the policy on effective targets, the executor re-check directly before the
write, the agent/proactive/standing paths and the "Schlafen" regression
with a reproduction of the project owner's house (real incident 7.1.2:
``button.press`` with ``floor_id`` started the vacuum and the smoke
detector self test).
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any, Mapping

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeassistant.core import HomeAssistant  # noqa: E402

from homeintent.effect_graph import (  # noqa: E402
    PlanEffects,
    build_effect_graph,
    build_plan_effects,
    build_plan_effects_from_sources,
)
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.execution_policy import (  # noqa: E402
    PolicyOutcome,
    evaluate_service_plan,
    validate_automation_action_targets,
)
from homeintent.plan_origin import PlanOrigin  # noqa: E402
from homeintent.risk import RiskLevel  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.service_executor import async_execute_service_plan  # noqa: E402


class FakeSources:
    def __init__(
        self,
        *,
        scripts: Mapping[str, list[Any]] | None = None,
        scenes: Mapping[str, Mapping[str, Any]] | None = None,
        groups: Mapping[str, tuple[str, ...]] | None = None,
        automations: Mapping[str, list[Any]] | None = None,
        targets: Mapping[tuple[str, str], set[str]] | None = None,
        uuids: Mapping[str, str] | None = None,
        names: Mapping[str, str] | None = None,
        followups: Mapping[str, tuple[str, ...]] | None = None,
        existing: set[str] | None = None,
    ) -> None:
        self.scripts = dict(scripts or {})
        self.scenes = dict(scenes or {})
        self.groups = dict(groups or {})
        self.automations = dict(automations or {})
        self.targets = dict(targets or {})
        self.uuids = dict(uuids or {})
        self.names = dict(names or {})
        self._followups = dict(followups or {})
        self.existing = set(existing or set())

    def script_sequence(self, entity_id):
        return self.scripts.get(entity_id)

    def automation_sequence(self, entity_id):
        return self.automations.get(entity_id)

    def scene_states(self, entity_id):
        return self.scenes.get(entity_id)

    def group_members(self, entity_id):
        return self.groups.get(entity_id)

    def resolve_target(self, selector):
        found: set[str] = set()
        for key, values in selector.items():
            for value in values if isinstance(values, (list, tuple)) else [values]:
                if (key, value) not in self.targets:
                    return None
                found |= self.targets[(key, value)]
        return frozenset(found)

    def resolve_registry_id(self, value):
        return self.uuids.get(value)

    def entity_exists(self, entity_id):
        return entity_id in self.existing

    def entities_of_domain(self, domain):
        return tuple(item for item in self.existing if item.startswith(f"{domain}."))

    def followups(self, entity_ids):
        return tuple(item for entity_id in sorted(entity_ids) for item in self._followups.get(entity_id, ()))

    def name(self, kind, item_id):
        return self.names.get(item_id)


def snap(entity_id: str, name: str | None = None, **kwargs: Any) -> EntitySnapshot:
    domain = entity_id.split(".", 1)[0]
    return EntitySnapshot(entity_id, name or entity_id, domain, kwargs.pop("state", "off"), **kwargs)


# The exposure-deny mode these tests were written for; "allow" (default since
# 7.8.2) is covered in tests/test_routine_unexposed_782.py.
DENY = {"routine_unexposed_effects": "deny"}


def decide(plan: ServiceCallPlan, entities, sources: FakeSources, options=None, **kwargs):
    targets = (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
    effects = build_plan_effects_from_sources(plan.domain, plan.service, targets, sources)
    return evaluate_service_plan(
        plan, entities, options or DENY, is_admin=kwargs.pop("is_admin", True),
        user_id=kwargs.pop("user_id", "admin"), effects=effects, **kwargs,
    )


SCRIPT = ServiceCallPlan("script", "turn_on", "script.routine")


# ------------------------------------------------------------------ 1
def test_unexposed_entity_in_script_is_denied():
    sources = FakeSources(
        scripts={"script.routine": [{"action": "vacuum.start", "target": {"entity_id": "vacuum.robbi"}}]},
        names={"vacuum.robbi": "Saugroboter", "script.routine": "Routine"},
    )
    decision = decide(SCRIPT, [snap("script.routine")], sources)
    assert decision.outcome is PolicyOutcome.DENY
    assert "Saugroboter" in (decision.reason or "")
    assert "nichts ausgeführt" in (decision.reason or "")


def test_confirmation_cannot_override_exposure():
    sources = FakeSources(
        scripts={"script.routine": [{"action": "vacuum.start", "target": {"entity_id": "vacuum.robbi"}}]},
    )
    hass = HomeAssistant()
    _ha_stub.register_script(hass, "script.routine", sources.scripts["script.routine"])
    result = asyncio.run(async_execute_service_plan(
        hass, SCRIPT, [snap("script.routine")], DENY, is_admin=True, user_id="admin", confirmed=True,
    ))
    assert result.executed is False
    hass.services.async_call.assert_not_awaited()


# ------------------------------------------------------------------ 2
FLOOR_BUTTONS = {
    "button.saugroboter_start": "Saugroboter Reinigung starten",
    "button.brandmelder_test": "Brandmelder Flur Selbsttest",
    "button.rollladen_runter": "Rollläden runter",
}


def _floor_script() -> FakeSources:
    return FakeSources(
        scripts={"script.gute_nacht": [
            {"alias": "Rolladen Runterfahren", "action": "button.press",
             "target": {"floor_id": "erdgeschoss"}},
        ]},
        targets={("floor_id", "erdgeschoss"): {*FLOOR_BUTTONS, "light.kueche"}},
        names={**FLOOR_BUTTONS, "script.gute_nacht": "Gute Nacht", "erdgeschoss": "Erdgeschoss"},
    )


def test_floor_button_press_with_unexposed_buttons_is_denied_and_named():
    plan = ServiceCallPlan("script", "turn_on", "script.gute_nacht")
    decision = decide(plan, [snap("script.gute_nacht"), snap("button.rollladen_runter")], _floor_script())
    assert decision.outcome is PolicyOutcome.DENY
    reason = decision.reason or ""
    assert "Saugroboter Reinigung starten" in reason and "Brandmelder Flur Selbsttest" in reason
    assert "Rollläden runter" not in reason.split(":")[1].split(".")[0]
    assert "‚Rolladen Runterfahren‘ drückt alle Buttons im Erdgeschoss" in reason
    # Only the action domain is affected: the kitchen light is not an effect.
    assert "light.kueche" not in (decision.effects.effective_targets if decision.effects else set())


def test_floor_button_press_all_exposed_is_high_and_needs_confirmation():
    plan = ServiceCallPlan("script", "turn_on", "script.gute_nacht")
    entities = [snap("script.gute_nacht"), *(snap(item) for item in FLOOR_BUTTONS)]
    decision = decide(plan, entities, _floor_script())
    assert decision.outcome is PolicyOutcome.CONFIRM
    assert decision.risk is RiskLevel.HIGH


# ------------------------------------------------------------------ 3/4
def test_device_action_uuid_is_resolved():
    sources = FakeSources(
        scripts={"script.routine": [
            {"type": "turn_on", "device_id": "dev1", "entity_id": "0123456789abcdef0123456789abcdef",
             "domain": "switch"},
        ]},
        uuids={"0123456789abcdef0123456789abcdef": "switch.kaffeemaschine"},
    )
    graph = build_effect_graph("script.routine", sources)
    assert graph.complete
    assert graph.effective_targets == {"switch.kaffeemaschine"}


def test_device_id_target_only_affects_the_action_domain():
    sources = FakeSources(
        scripts={"script.routine": [
            {"action": "light.turn_on", "target": {"device_id": "dev1"}},
        ]},
        targets={("device_id", "dev1"): {"light.lampe", "sensor.lampe_power", "switch.lampe_kindersicherung"}},
    )
    graph = build_effect_graph("script.routine", sources)
    assert graph.effective_targets == {"light.lampe"}


# ------------------------------------------------------------------ 5
def test_nested_scripts_are_followed_and_cycles_terminate():
    sources = FakeSources(scripts={
        "script.a": [{"action": "script.turn_on", "target": {"entity_id": "script.b"}}],
        "script.b": [
            {"action": "script.a"},
            {"action": "light.turn_off", "target": {"entity_id": "light.flur"}},
            {"action": "script.c"},
        ],
        "script.c": [{"sequence": [{"action": "switch.turn_off", "entity_id": "switch.pumpe"}]}],
    })
    graph = build_effect_graph("script.a", sources)
    assert graph.effective_targets == {"light.flur", "switch.pumpe"}
    assert set(graph.nested) >= {"script.a", "script.b", "script.c"}
    assert graph.complete


def test_depth_limit_is_unknown_not_low():
    scripts = {
        f"script.s{index}": [{"action": f"script.s{index + 1}"}] for index in range(12)
    }
    scripts["script.s12"] = [{"action": "light.turn_on", "target": {"entity_id": "light.x"}}]
    graph = build_effect_graph("script.s0", FakeSources(scripts=scripts))
    assert not graph.complete


# ------------------------------------------------------------------ 6
def test_untaken_choose_branch_with_lock_counts():
    sources = FakeSources(scripts={"script.routine": [
        {"choose": [
            {"conditions": [{"condition": "state", "entity_id": "sun.sun", "state": "never"}],
             "sequence": [{"action": "lock.lock", "target": {"entity_id": "lock.haustuer"}}]},
        ], "default": [{"action": "light.turn_on", "target": {"entity_id": "light.flur"}}]},
        {"if": [{"condition": "template", "value_template": "{{ false }}"}],
         "then": [{"action": "light.turn_off", "target": {"entity_id": "light.flur"}}]},
        {"parallel": [{"action": "switch.turn_on", "target": {"entity_id": "switch.a"}}]},
        {"repeat": {"count": 2, "sequence": [{"action": "switch.turn_off", "target": {"entity_id": "switch.b"}}]}},
    ]})
    entities = [snap("script.routine"), snap("lock.haustuer"), snap("light.flur"), snap("switch.a"), snap("switch.b")]
    decision = decide(SCRIPT, entities, sources)
    assert decision.risk is RiskLevel.HIGH
    assert decision.outcome is PolicyOutcome.CONFIRM
    assert decision.effects is not None
    assert decision.effects.effective_targets == {"lock.haustuer", "light.flur", "switch.a", "switch.b"}


# ------------------------------------------------------------------ 7
def test_scene_with_unexposed_member_is_denied():
    sources = FakeSources(scenes={"scene.abend": {"light.a": "on", "switch.kaffeemaschine": {"state": "on"}}})
    plan = ServiceCallPlan("scene", "turn_on", "scene.abend")
    decision = decide(plan, [snap("scene.abend"), snap("light.a")], sources)
    assert decision.outcome is PolicyOutcome.DENY


def test_light_group_with_unexposed_member_is_denied():
    sources = FakeSources(groups={"light.alle": ("light.a", "light.geheim")})
    plan = ServiceCallPlan("homeassistant", "turn_on", "light.alle")
    entities = [snap("light.alle", attributes={"entity_id": ["light.a", "light.geheim"]}), snap("light.a")]
    decision = decide(plan, entities, sources)
    assert decision.outcome is PolicyOutcome.DENY


def test_scene_that_unlocks_is_critical():
    sources = FakeSources(scenes={"scene.heim": {"lock.haustuer": "unlocked"}})
    plan = ServiceCallPlan("scene", "turn_on", "scene.heim")
    decision = decide(plan, [snap("scene.heim"), snap("lock.haustuer")], sources)
    assert decision.risk is RiskLevel.CRITICAL


# ------------------------------------------------------------------ 8
def test_script_with_alarm_panel_is_critical_and_denied_for_non_admin():
    sources = FakeSources(scripts={"script.routine": [
        {"action": "alarm_control_panel.alarm_disarm", "target": {"entity_id": "alarm_control_panel.haus"}},
    ]})
    entities = [snap("script.routine"), snap("alarm_control_panel.haus")]
    decision = decide(SCRIPT, entities, sources, is_admin=False, user_id="anna")
    assert decision.risk is RiskLevel.CRITICAL
    assert decision.outcome is PolicyOutcome.DENY


# ------------------------------------------------------------------ 9
@pytest.mark.parametrize("step", [
    {"alias": "Dynamisch", "action": "light.turn_on", "target": {"entity_id": "{{ states('input_text.x') }}"}},
    {"alias": "Dynamisch", "action": "{{ 'light.turn_on' }}", "target": {"entity_id": "light.a"}},
    {"alias": "Dynamisch", "event": "custom_event"},
    {"alias": "Dynamisch", "action": "python_script.do"},
    {"alias": "Dynamisch", "action": "shell_command.run"},
    {"alias": "Dynamisch", "action": "rest_command.call"},
    {"alias": "Dynamisch", "action": "foo.bar", "data": {"device": "light.a"}},
])
def test_unknown_step_is_denied_by_default_and_confirmable_by_option(step):
    sources = FakeSources(scripts={"script.routine": [step]}, existing={"light.a"})
    entities = [snap("script.routine"), snap("light.a")]
    denied = decide(SCRIPT, entities, sources)
    assert denied.outcome is PolicyOutcome.DENY
    assert "‚Dynamisch‘ kann ich nicht prüfen" in (denied.reason or "")
    confirm = decide(SCRIPT, entities, sources, options={"effect_graph_unknown": "confirm"})
    assert confirm.outcome is PolicyOutcome.CONFIRM
    assert confirm.risk >= RiskLevel.HIGH
    assert "‚Dynamisch‘ kann ich nicht prüfen" in (confirm.note or "")
    # Paths without a person answering always refuse unknown steps.
    unattended = decide(
        SCRIPT, entities, sources, options={"effect_graph_unknown": "confirm"}, attended=False
    )
    assert unattended.outcome is PolicyOutcome.DENY
    standing = decide(
        SCRIPT, entities, sources, options={"effect_graph_unknown": "confirm"},
        origin=PlanOrigin.STANDING_PERMISSION,
    )
    assert standing.outcome is PolicyOutcome.DENY


def test_composite_plan_without_graph_fails_closed():
    decision = evaluate_service_plan(SCRIPT, [snap("script.routine")], {}, is_admin=True, user_id="admin")
    assert decision.outcome is PolicyOutcome.DENY


# ------------------------------------------------------------------ 10
def test_script_changed_between_preview_and_yes_is_checked_with_new_content():
    hass = HomeAssistant()
    entities = [snap("script.routine"), snap("light.a", state="off")]
    _ha_stub.register_script(hass, "script.routine", [
        {"action": "light.turn_on", "target": {"entity_id": "light.a"}},
    ])
    preview = evaluate_service_plan(
        SCRIPT, entities, DENY, is_admin=True, user_id="admin", effects=build_plan_effects(hass, SCRIPT)
    )
    assert preview.outcome is PolicyOutcome.ALLOW
    _ha_stub.register_script(hass, "script.routine", [
        {"action": "light.turn_on", "target": {"entity_id": "light.a"}},
        {"action": "vacuum.start", "target": {"entity_id": "vacuum.robbi"}},
    ])
    result = asyncio.run(async_execute_service_plan(
        hass, SCRIPT, entities, DENY, is_admin=True, user_id="admin", confirmed=True,
    ))
    assert result.executed is False
    hass.services.async_call.assert_not_awaited()


# ------------------------------------------------------------------ 11
def test_script_with_notify_delay_and_exposed_lights_is_allowed():
    sources = FakeSources(scripts={"script.routine": [
        {"action": "notify.mobile_app_handy", "data": {"message": "Hallo"}},
        {"delay": {"seconds": 5}},
        {"wait_template": "{{ true }}"},
        {"variables": {"x": 1}},
        {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"},
        {"action": "persistent_notification.create", "data": {"message": "x"}},
        {"action": "logbook.log", "data": {"name": "a", "message": "b"}},
        {"action": "light.turn_on", "target": {"entity_id": ["light.a", "light.b"]}, "data": {"brightness_pct": "{{ 50 }}"}},
    ]})
    entities = [snap("script.routine"), snap("light.a"), snap("light.b")]
    decision = decide(SCRIPT, entities, sources)
    assert decision.outcome is PolicyOutcome.ALLOW
    assert decision.risk is RiskLevel.LOW


def test_script_that_only_touches_many_targets_counts_effective_targets():
    lights = [f"light.l{index}" for index in range(8)]
    sources = FakeSources(scripts={"script.routine": [
        {"action": "light.turn_off", "target": {"entity_id": lights}},
    ]})
    entities = [snap("script.routine"), *(snap(item) for item in lights)]
    assert decide(SCRIPT, entities, sources, options={"max_action_targets": 5}).outcome is PolicyOutcome.DENY
    assert decide(SCRIPT, entities, sources).risk is RiskLevel.MEDIUM


def test_read_only_and_admin_only_apply_to_effective_targets():
    sources = FakeSources(scripts={"script.routine": [
        {"action": "switch.turn_on", "target": {"entity_id": "switch.pumpe"}},
    ]})
    entities = [snap("script.routine"), snap("switch.pumpe")]
    assert decide(SCRIPT, entities, sources, options={"read_only_entities": ["switch.pumpe"]}).outcome is PolicyOutcome.DENY
    assert decide(
        SCRIPT, entities, sources, options={"admin_only_entities": ["switch.pumpe"]},
        is_admin=False, user_id="anna",
    ).outcome is PolicyOutcome.DENY


def test_possible_followups_are_a_hint_only():
    sources = FakeSources(
        scripts={"script.routine": [{"action": "light.turn_on", "target": {"entity_id": "light.a"}}]},
        followups={"light.a": ("automation.flur",)},
        names={"automation.flur": "Flur nachführen"},
    )
    decision = decide(SCRIPT, [snap("script.routine"), snap("light.a")], sources)
    assert decision.outcome is PolicyOutcome.ALLOW
    assert decision.risk is RiskLevel.LOW
    assert "Flur nachführen" in (decision.note or "")


def test_media_player_target_in_tts_data_is_an_effect():
    sources = FakeSources(scripts={"script.routine": [
        {"action": "tts.speak", "target": {"entity_id": "tts.piper"},
         "data": {"media_player_entity_id": "media_player.kueche", "message": "Hallo"}},
    ]})
    graph = build_effect_graph("script.routine", sources)
    assert graph.effective_targets == {"tts.piper", "media_player.kueche"}


# ------------------------------------------------------------------ 12
def test_agent_proactive_and_standing_paths_cannot_bypass():
    from homeintent.agent_event import AgentMode

    hass = HomeAssistant()
    _ha_stub.register_script(hass, "script.routine", [
        {"action": "vacuum.start", "target": {"entity_id": "vacuum.robbi"}},
    ])
    entities = [snap("script.routine")]
    for origin, attended in (
        (PlanOrigin.PROACTIVE_PROPOSAL, False),
        (PlanOrigin.STANDING_PERMISSION, False),
        (PlanOrigin.PROACTIVE_PROPOSAL, True),
    ):
        result = asyncio.run(async_execute_service_plan(
            hass, SCRIPT, entities, {}, is_admin=False, user_id=None, confirmed=True,
            origin=origin, attended=attended,
        ))
        assert result.executed is False, origin
    hass.services.async_call.assert_not_awaited()
    assert AgentMode.AUTO  # the pure decision path fails closed without a graph
    decision = evaluate_service_plan(
        SCRIPT, entities, {}, is_admin=False, user_id=None,
        origin=PlanOrigin.PROACTIVE_PROPOSAL, attended=False,
    )
    assert decision.outcome is PolicyOutcome.DENY


# ------------------------------------------------------------------ 13
def test_automation_with_script_action_is_checked_on_creation():
    sources = FakeSources(
        scripts={"script.routine": [{"action": "vacuum.start", "target": {"entity_id": "vacuum.robbi"}}]},
        names={"vacuum.robbi": "Saugroboter"},
    )
    effects = build_plan_effects_from_sources("homeassistant", "turn_on", ["script.routine"], sources)
    error = validate_automation_action_targets(
        frozenset({"script.routine"}), {}, effects=effects, exposed_ids={"script.routine"},
    )
    assert error is not None and "Saugroboter" in error
    unknown = build_plan_effects_from_sources(
        "homeassistant", "turn_on", ["script.x"], FakeSources(scripts={"script.x": [{"event": "e"}]}),
    )
    error = validate_automation_action_targets(
        frozenset({"script.x"}), {}, effects=unknown, exposed_ids={"script.x"},
    )
    assert error is not None and "nicht angelegt" in error


# ------------------------------------------------------------------ 14
def test_inferred_routine_always_needs_confirmation_without_binding():
    sources = FakeSources(scenes={"scene.film": {"light.a": "off"}})
    plan = ServiceCallPlan("scene", "turn_on", "scene.film")
    entities = [snap("scene.film"), snap("light.a")]
    explicit = decide(plan, entities, sources)
    inferred = decide(plan, entities, sources, origin=PlanOrigin.INFERRED_ROUTINE)
    bound = decide(plan, entities, sources, origin=PlanOrigin.INFERRED_ROUTINE, binding_confirmed=True)
    bound_auto = decide(
        plan, entities, sources, origin=PlanOrigin.INFERRED_ROUTINE, binding_confirmed=True,
        options={"implicit_action_level": "bound_routines_auto"},
    )
    assert explicit.outcome is PolicyOutcome.ALLOW
    assert inferred.outcome is PolicyOutcome.CONFIRM
    # 7.3.3: a confirmed binding runs directly only with bound_routines_auto.
    assert bound.outcome is PolicyOutcome.CONFIRM
    assert bound_auto.outcome is PolicyOutcome.ALLOW


def test_unchecked_placeholder_is_incomplete():
    assert not PlanEffects.unchecked(["script.x"]).complete


# ============================================ Regression "Schlafen" (house)
OWNER_HOUSE = [
    ("script.schlafen", "Schlafen", None, None),
    ("script.gute_nacht", "Gute Nacht", None, None),
    ("light.wohnzimmer", "Wohnzimmerlicht", "wohnzimmer", "erdgeschoss"),
    ("light.kueche", "Küchenlicht", "kueche", "erdgeschoss"),
    ("cover.wohnzimmer", "Rollladen Wohnzimmer", "wohnzimmer", "erdgeschoss"),
    ("cover.kueche", "Rollladen Küche", "kueche", "erdgeschoss"),
    ("button.rollladen_eg_runter", "Rollläden EG runter", "wohnzimmer", "erdgeschoss"),
]
NOT_EXPOSED = {
    "button.saugroboter_reinigung": ("Saugroboter Reinigung starten", "wohnzimmer"),
    "button.brandmelder_flur_selbsttest": ("Brandmelder Flur Selbsttest", "flur"),
    "vacuum.saugroboter": ("Saugroboter", "wohnzimmer"),
}


def _owner_house(monkeypatch):
    from _testhaus import HouseConversation

    entities = [
        EntitySnapshot(
            entity_id, name, entity_id.split(".", 1)[0], "off" if not entity_id.startswith("cover") else "open",
            area_id=area, area_name=area.title() if area else None,
            floor_id=floor, floor_name="Erdgeschoss" if floor else None,
            capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
        )
        for entity_id, name, area, floor in OWNER_HOUSE
    ]
    house = HouseConversation(monkeypatch, entities=entities, options=DENY)
    hass = house.entity.hass
    hass.data.pop("script", None)
    hass.data.pop("scene", None)
    hass.data["_stub_target_index"] = {}
    _ha_stub.register_script(hass, "script.schlafen", [
        {"action": "light.turn_off", "target": {"entity_id": ["light.wohnzimmer", "light.kueche"]}},
        {"action": "cover.close_cover", "target": {"entity_id": ["cover.wohnzimmer", "cover.kueche"]}},
    ])
    _ha_stub.register_script(hass, "script.gute_nacht", [
        {"action": "light.turn_off", "target": {"area_id": ["wohnzimmer", "kueche"]}},
        {"alias": "Rolladen Runterfahren", "action": "button.press", "target": {"floor_id": "erdgeschoss"}},
    ])
    index = hass.data["_stub_target_index"]
    index[("area_id", "wohnzimmer")] = {"light.wohnzimmer", "cover.wohnzimmer", "button.rollladen_eg_runter",
                                        "button.saugroboter_reinigung", "vacuum.saugroboter"}
    index[("area_id", "kueche")] = {"light.kueche", "cover.kueche"}
    index[("floor_id", "erdgeschoss")] = {
        *index[("area_id", "wohnzimmer")], *index[("area_id", "kueche")], "button.brandmelder_flur_selbsttest",
    }
    for entity_id, (name, _area) in NOT_EXPOSED.items():
        hass.states._states[entity_id] = types.SimpleNamespace(
            entity_id=entity_id, state="unknown", attributes={"friendly_name": name}
        )
    hass.states._states["floor"] = None
    hass.states._states.pop("floor")
    return house


def _started(turn) -> set[str]:
    return {target for target in turn.targets if target.startswith(("script.", "scene."))}


def test_schlafen_explicit_runs_exactly_that_script(monkeypatch):
    house = _owner_house(monkeypatch)
    turn = house.say("Aktiviere Schlafen.")
    assert turn.targets == {"script.schlafen"}, turn.speech
    assert turn.calls == [("script", "turn_on", {"entity_id": "script.schlafen"})]
    # Short summary of the verified effect graph after the run (1.5).
    assert "2 Rollläden" in turn.speech and "2 Lichter" in turn.speech, turn.speech


@pytest.mark.parametrize("sentence", [
    "Ich gehe schlafen.", "Gute Nacht.", "Starte die Schlafroutine.", "Mach alles für die Nacht fertig.",
])
def test_schlafen_inferred_never_starts_without_confirmation(monkeypatch, sentence):
    house = _owner_house(monkeypatch)
    turn = house.say(sentence)
    assert _started(turn) == set(), (sentence, turn.speech)
    assert not any(target.startswith(("button.", "vacuum.")) for target in turn.targets)


def test_ich_gehe_schlafen_asks_which_routine(monkeypatch):
    house = _owner_house(monkeypatch)
    turn = house.say("Ich gehe schlafen.")
    assert turn.calls == []
    assert "Schlafen" in turn.speech and "Gute Nacht" in turn.speech and "?" in turn.speech, turn.speech


def test_gute_nacht_script_is_refused_naming_the_foreign_devices(monkeypatch):
    house = _owner_house(monkeypatch)
    turn = house.say("Aktiviere Gute Nacht.")
    assert turn.calls == [], turn.speech
    assert "Saugroboter Reinigung starten" in turn.speech
    assert "Brandmelder Flur Selbsttest" in turn.speech
    assert "nichts ausgeführt" in turn.speech
    # A "Ja" afterwards cannot run it either.
    assert house.say("Ja.").calls == []
