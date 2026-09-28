"""Phase 2 (7.3.2): ExecutionContext and ExecutionTrace.

The cause chain uses only Home Assistant's own context chain (VERIFIED);
temporal proximity is at most POSSIBLE and never phrased as a fact.
"""

from __future__ import annotations

import ast
import asyncio
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeassistant.core import Context, HomeAssistant  # noqa: E402

from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.execution_context import begin_turn, current_turn, end_turn  # noqa: E402
from homeintent.execution_trace import (  # noqa: E402
    ContextEvent,
    ContextIndex,
    EffectKind,
    Evidence,
    ExecutionTraceStore,
    TraceEffect,
    TraceRecord,
    explain_change,
    register_trace,
)
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.service_executor import async_execute_service_plan  # noqa: E402

ROOT = Path(__file__).parent.parent / "custom_components" / "homeintent"
NOW = datetime(2026, 9, 28, 22, 13)


def ctx(context_id: str, parent_id: str | None = None, user_id: str | None = None):
    return types.SimpleNamespace(id=context_id, parent_id=parent_id, user_id=user_id)


def record(execution_id: str = "exec1", **changes) -> TraceRecord:
    base = {
        "execution_id": execution_id,
        "created_at": NOW.isoformat(),
        "actor": "abc",
        "user_present": True,
        "utterance": "Aktiviere Schlafen",
        "plans": ("script.turn_on script.schlafen",),
        "targets": ("script.schlafen",),
        "effects": (
            TraceEffect("vacuum", "start", ("vacuum.saugroboter",), "Saugen starten", "script.schlafen"),
        ),
        "names": {"script.schlafen": "Schlafen", "vacuum.saugroboter": "Saugroboter"},
    }
    base.update(changes)
    return TraceRecord(**base)


# ------------------------------------------------------------ static rule
def test_every_service_call_passes_a_context():
    missing = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "async_call"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "services"
                and not any(keyword.arg == "context" for keyword in node.keywords)
            ):
                missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert missing == []


# ------------------------------------------------------------ explanation
def test_direct_command_is_direct_and_verified():
    store = ExecutionTraceStore()
    store.add(record(targets=("light.flur",), effects=(), plans=("light.turn_on light.flur",),
                     utterance="Mach das Flurlicht an"))
    cause = explain_change("light.flur", "Flurlicht", ctx("exec1"), NOW, store, ContextIndex())
    assert (cause.kind, cause.evidence) == (EffectKind.DIRECT_EFFECT, Evidence.VERIFIED)
    assert "22:13" in cause.text and "Mach das Flurlicht an" in cause.text


def test_script_effect_names_the_step():
    store = ExecutionTraceStore()
    store.add(record())
    cause = explain_change("vacuum.saugroboter", "Saugroboter", ctx("exec1"), NOW, store, ContextIndex())
    assert (cause.kind, cause.evidence) == (EffectKind.SCRIPT_EFFECT, Evidence.VERIFIED)
    assert "Skript Schlafen" in cause.text and "‚Saugen starten‘" in cause.text
    assert "Aktiviere Schlafen" in cause.text


def test_automation_triggered_by_state_change_is_verified_through_parent_id():
    store = ExecutionTraceStore()
    store.add(record(targets=("light.flur",), effects=()))
    index = ContextIndex()
    index.add(ContextEvent("run1", "automation", "automation.nachlauf", "Flur Nachlauf",
                           "exec1", None, NOW.isoformat(), "state of light.flur"))
    cause = explain_change("fan.flur", "Flurlüfter", ctx("run1", "exec1"), NOW, store, index)
    assert (cause.kind, cause.evidence) == (EffectKind.SECONDARY_EFFECT, Evidence.VERIFIED)
    assert "Flur Nachlauf" in cause.text


def test_foreign_automation_is_verified_without_homeintent():
    index = ContextIndex()
    index.add(ContextEvent("run1", "automation", "automation.bewegung", "Flurlicht bei Bewegung",
                           "sensorctx", None, NOW.isoformat(), "state of binary_sensor.bewegung_flur"))
    cause = explain_change("light.flur", "Flurlicht", ctx("run1", "sensorctx"), NOW,
                           ExecutionTraceStore(), index)
    assert (cause.kind, cause.evidence) == (EffectKind.AUTOMATION_EFFECT, Evidence.VERIFIED)
    assert "Flurlicht bei Bewegung" in cause.text and "binary_sensor.bewegung_flur" in cause.text


def test_person_in_the_app_is_external_and_verified():
    cause = explain_change("light.flur", "Flurlicht", ctx("x", None, "anna"), NOW,
                           ExecutionTraceStore(), ContextIndex(), {"anna": "Anna"})
    assert (cause.kind, cause.evidence) == (EffectKind.EXTERNAL_EFFECT, Evidence.VERIFIED)
    assert cause.text.startswith("Anna hat")


def test_coincident_foreign_effect_is_at_most_possible_and_no_claim():
    store = ExecutionTraceStore()
    store.add(record(created_at=(NOW - timedelta(seconds=30)).isoformat()))
    cause = explain_change("light.garten", "Gartenlicht", ctx("unrelated"), NOW, store, ContextIndex())
    assert cause.evidence is Evidence.POSSIBLE
    assert cause.text.startswith("Dafür finde ich keine Ursache, die ich belegen kann")
    assert "hat daraufhin" not in cause.text and "Ich habe" not in cause.text


def test_no_relation_at_all_is_unknown():
    cause = explain_change("light.garten", "Gartenlicht", ctx("unrelated"), NOW,
                           ExecutionTraceStore(), ContextIndex())
    assert cause.evidence is Evidence.UNKNOWN
    assert cause.text == "Dafür finde ich keine Ursache, die ich belegen kann."


def test_store_is_a_bounded_ring_with_age_limit():
    store = ExecutionTraceStore(limit=3, days=14)
    for index in range(5):
        store.add(record(f"e{index}"))
    assert [item.execution_id for item in store.recent()] == ["e4", "e3", "e2"]
    store.add(record("old", created_at=(NOW - timedelta(days=20)).isoformat()))
    store.prune(NOW)
    assert store.get("old") is None
    reloaded = ExecutionTraceStore()
    reloaded.load(store.to_list())
    assert reloaded.get("e4") == store.get("e4")


def test_utterance_can_be_hashed_for_privacy():
    from homeintent.execution_trace import utterance_for_trace

    assert utterance_for_trace("Mach das Licht an", store_text=False).startswith("#")
    store = ExecutionTraceStore()
    store.add(record(utterance=utterance_for_trace("Mach das Licht an", store_text=False),
                     targets=("light.flur",), effects=()))
    cause = explain_change("light.flur", "Flurlicht", ctx("exec1"), NOW, store, ContextIndex())
    assert "Mach das Licht an" not in cause.text and cause.evidence is Evidence.VERIFIED


# ------------------------------------------------------------ executor
def _execute(hass, plan, entities, **kwargs):
    return asyncio.run(async_execute_service_plan(
        hass, plan, entities, {}, is_admin=True, user_id=kwargs.pop("user_id", "admin"),
        confirmed=True, **kwargs,
    ))


def test_executor_passes_one_context_per_turn_and_records_the_trace():
    hass = HomeAssistant()
    store, index = ExecutionTraceStore(), ContextIndex()
    register_trace(hass, store, index, store_text=True)
    lamp = EntitySnapshot("light.flur", "Flurlicht", "light", "off")
    fan = EntitySnapshot("fan.bad", "Badlüfter", "fan", "off")

    async def turn():
        token = begin_turn(types.SimpleNamespace(context=None), "admin", "Flurlicht und Lüfter an")
        try:
            first = await async_execute_service_plan(
                hass, ServiceCallPlan("homeassistant", "turn_on", "light.flur"), [lamp, fan], {},
                is_admin=True, user_id="admin", confirmed=True,
            )
            second = await async_execute_service_plan(
                hass, ServiceCallPlan("homeassistant", "turn_on", "fan.bad"), [lamp, fan], {},
                is_admin=True, user_id="admin", confirmed=True,
            )
            return first, second, current_turn()
        finally:
            end_turn(token)

    first, second, turn_info = asyncio.run(turn())
    contexts = hass.services.async_call.contexts
    assert len({item.id for item in contexts}) == 1
    assert contexts[0].user_id == "admin"
    assert first.execution_id == second.execution_id == contexts[0].id == turn_info.context.id
    trace = store.get(first.execution_id)
    assert trace is not None and set(trace.targets) == {"light.flur", "fan.bad"}
    assert trace.utterance == "Flurlicht und Lüfter an"


def test_executor_without_turn_creates_its_own_context_without_user():
    hass = HomeAssistant()
    lamp = EntitySnapshot("light.flur", "Flurlicht", "light", "off")
    result = _execute(hass, ServiceCallPlan("homeassistant", "turn_on", "light.flur"), [lamp], user_id=None)
    context = hass.services.async_call.contexts[-1]
    assert isinstance(context, Context) and context.user_id is None
    assert result.execution_id == context.id


def test_home_assistant_permission_error_is_reported_cleanly():
    hass = HomeAssistant()

    class Unauthorized(Exception):
        pass

    hass.services.async_call.side_effect = Unauthorized("Unauthorized")
    lamp = EntitySnapshot("light.flur", "Flurlicht", "light", "off")
    result = _execute(hass, ServiceCallPlan("homeassistant", "turn_on", "light.flur"), [lamp], user_id="anna")
    assert result.executed is False
    assert result.error == "Home Assistant erlaubt diesem Benutzer diese Aktion nicht."


# ------------------------------------------------------------ conversation
def test_why_question_after_script_names_the_verified_chain(monkeypatch):
    from _testhaus import HouseConversation

    entities = [
        EntitySnapshot("script.nachtruhe", "Nachtruhe", "script", "off"),
        EntitySnapshot("vacuum.saugroboter", "Saugroboter", "vacuum", "docked",
                       area_id="wohnzimmer", area_name="Wohnzimmer"),
        EntitySnapshot("light.flur", "Flurlicht", "light", "on", area_id="flur", area_name="Flur"),
    ]
    house = HouseConversation(monkeypatch, entities=entities)
    hass = house.entity.hass
    store, index = ExecutionTraceStore(), ContextIndex()
    house.entity._runtime_data.trace = register_trace(hass, store, index, store_text=True)
    _ha_stub.register_script(hass, "script.nachtruhe", [
        {"alias": "Saugen starten", "action": "vacuum.start", "target": {"entity_id": "vacuum.saugroboter"}},
    ])
    turn = house.say("Aktiviere Nachtruhe.")
    assert turn.targets == {"script.nachtruhe"}, turn.speech
    context = hass.services.async_call.contexts[-1]
    assert context.user_id == "admin"
    # Home Assistant writes the vacuum's new state with the script's context.
    hass.states._states["vacuum.saugroboter"] = types.SimpleNamespace(
        entity_id="vacuum.saugroboter", state="cleaning",
        attributes={"friendly_name": "Saugroboter"}, context=context,
        last_changed=datetime.now(),
    )
    answer = house.say("Warum ist der Saugroboter angegangen?")
    assert answer.calls == []
    assert "Aktiviere Nachtruhe" in answer.speech
    assert "Skript Nachtruhe" in answer.speech and "‚Saugen starten‘" in answer.speech

    # A change without any evidence gets no invented cause.
    hass.states._states["light.flur"] = types.SimpleNamespace(
        entity_id="light.flur", state="on", attributes={"friendly_name": "Flurlicht"},
        context=types.SimpleNamespace(id="fremd", parent_id=None, user_id=None),
        last_changed=datetime.now() + timedelta(hours=3),
    )
    unknown = house.say("Warum ist das Flurlicht an?")
    assert unknown.calls == []
    assert "Nachtruhe" not in unknown.speech
