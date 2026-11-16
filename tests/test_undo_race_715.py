"""P0 regressions for immutable evidence during a running device undo."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from hypothesis import HealthCheck, given, settings, strategies as st
import pytest

import _ha_stub

_ha_stub.install()

from homeassistant.core import State
from test_clause_binding_7132 import source_entities
from test_review_regressions_7133 import house
from homeintent.service_call import ServiceCallPlan
from homeintent.controllers.device_undo import partial_undo_failure_text
from homeintent.execution_trace import ContextIndex, ExecutionTraceStore, register_trace
from homeintent.undo import (
    CompletedUndoStep,
    UndoPlan,
    UndoRemainder,
    advance_undo_remainder,
    bind_undo_to_observed_states,
    next_undo_guard,
    undo_matches_observed_states,
)


OFFICE = "light.buerolicht"
HALL = "light.flurlicht"


def _compound_house(monkeypatch):
    entities = [
        replace(entity, state="off")
        if entity.entity_id in {OFFICE, HALL}
        else entity
        for entity in source_entities()
    ]
    h = house(monkeypatch, entities)
    first = h.say(
        "Schalte das Bürolicht ein und schalte das Flurlicht ein."
    )
    assert first.targets == {OFFICE, HALL}
    return h


def _changed_office(h, kind: str) -> State:
    current = h.entity.hass.states.get(OFFICE)
    attributes = dict(current.attributes)
    if kind == "attributes":
        attributes["brightness"] = 30
    changed = State(OFFICE, current.state, attributes)
    if kind in {"last_updated", "same_state_new_version"}:
        changed.last_updated = datetime(2026, 11, 15, tzinfo=timezone.utc)
    if kind == "context":
        changed.context = SimpleNamespace(
            id="external-version", parent_id=None
        )
    return changed


@pytest.mark.parametrize(
    "change",
    ["attributes", "last_updated", "context", "same_state_new_version"],
)
def test_external_version_during_previous_inverse_is_never_rebound(
    monkeypatch, change
):
    h = _compound_house(monkeypatch)
    original = h.entity.hass.services.async_call.side_effect

    async def mutate_after_first_inverse(domain, service, data=None, **kwargs):
        result = await original(domain, service, data, **kwargs)
        if service == "turn_off" and (data or {}).get("entity_id") == HALL:
            h.entity.hass.states._states[OFFICE] = _changed_office(h, change)
        return result

    h.entity.hass.services.async_call.side_effect = mutate_after_first_inverse

    undone = h.say("Mach das rückgängig.")

    assert undone.targets == {HALL}, (undone.speech, undone.calls)
    assert "Extern verändert" in undone.speech
    assert "Flurlicht" in undone.speech
    assert "Bürolicht" in undone.speech
    assert h.say("Mach das rückgängig.").calls == []


def test_confirmed_compound_undo_keeps_original_evidence(monkeypatch):
    h = _compound_house_for_confirmation(monkeypatch)
    original = h.entity.hass.services.async_call.side_effect

    async def mutate_after_first_inverse(domain, service, data=None, **kwargs):
        result = await original(domain, service, data, **kwargs)
        if service == "turn_off" and (data or {}).get("entity_id") == HALL:
            h.entity.hass.states._states[OFFICE] = State(
                OFFICE, "on", {"brightness": 73}
            )
        return result

    h.entity.hass.services.async_call.side_effect = mutate_after_first_inverse

    undone = h.say("Mach das rückgängig.")

    assert undone.targets == {HALL}, (undone.speech, undone.calls)
    assert "Extern verändert" in undone.speech
    assert h.say("Mach das rückgängig.").calls == []


def _compound_house_for_confirmation(monkeypatch):
    entities = [
        replace(entity, state="off")
        if entity.entity_id in {OFFICE, HALL}
        else entity
        for entity in source_entities()
    ]
    h = house(monkeypatch, entities)
    controller_type = type(h.entity._devices)
    original_handler = controller_type.async_handle_command_plan

    async def force_confirmation(self, user_input, response, result, live_entities):
        return await original_handler(
            self,
            user_input,
            response,
            replace(result, confirmation_text="Soll ich das ausführen?"),
            live_entities,
        )

    monkeypatch.setattr(
        controller_type, "async_handle_command_plan", force_confirmation
    )
    preview = h.say(
        "Schalte das Bürolicht ein und schalte das Flurlicht ein."
    )
    assert preview.calls == []
    assert h.say("Ja.").targets == {OFFICE, HALL}
    return h


def test_inverse_steps_use_distinct_child_contexts(monkeypatch):
    h = _compound_house(monkeypatch)
    traces = ExecutionTraceStore()
    register_trace(h.entity.hass, traces, ContextIndex(), store_text=True)
    before = len(h.entity.hass.services.async_call.contexts)

    undone = h.say("Mach das rückgängig.")
    contexts = h.entity.hass.services.async_call.contexts[before:]

    assert undone.targets == {OFFICE, HALL}
    assert len(contexts) == 2
    assert all(context is not None for context in contexts)
    assert contexts[0].id != contexts[1].id
    assert contexts[0].parent_id == contexts[1].parent_id
    assert contexts[0].parent_id is not None
    assert all(
        (record := traces.get(str(context.id))) is not None
        and record.utterance == "Mach das rückgängig."
        for context in contexts
    )


def test_permission_change_between_inverse_steps_stops_remaining_write(
    monkeypatch,
):
    h = _compound_house(monkeypatch)
    permission = {"office": True}

    class Permissions:
        def check_entity(self, entity_id, permission_name):
            assert permission_name == "control"
            return entity_id != OFFICE or permission["office"]

    user = SimpleNamespace(
        id="admin", name="Philipp", is_admin=True, permissions=Permissions()
    )

    async def get_user(_user_id):
        return user

    h.entity.hass.auth = SimpleNamespace(async_get_user=get_user)
    original = h.entity.hass.services.async_call.side_effect

    async def revoke_after_first_inverse(domain, service, data=None, **kwargs):
        result = await original(domain, service, data, **kwargs)
        if service == "turn_off" and (data or {}).get("entity_id") == HALL:
            permission["office"] = False
        return result

    h.entity.hass.services.async_call.side_effect = revoke_after_first_inverse

    undone = h.say("Mach das rückgängig.")

    assert undone.targets == {HALL}, (undone.speech, undone.calls)
    assert "Bürolicht" in undone.speech
    assert h.say("Mach das rückgängig.").calls == []


def test_evidence_change_inside_repeated_preflight_is_caught_at_write_boundary(
    monkeypatch,
):
    h = _compound_house(monkeypatch)
    phase = {"after_first_inverse": False, "auth_reads": 0}
    user = SimpleNamespace(
        id="admin", name="Philipp", is_admin=True, permissions=None
    )

    async def get_user(_user_id):
        if phase["after_first_inverse"]:
            phase["auth_reads"] += 1
            if phase["auth_reads"] == 2:
                h.entity.hass.states._states[OFFICE] = State(
                    OFFICE, "on", {"brightness": 41}
                )
        return user

    h.entity.hass.auth = SimpleNamespace(async_get_user=get_user)
    original = h.entity.hass.services.async_call.side_effect

    async def mark_first_inverse(domain, service, data=None, **kwargs):
        result = await original(domain, service, data, **kwargs)
        if service == "turn_off" and (data or {}).get("entity_id") == HALL:
            phase["after_first_inverse"] = True
        return result

    h.entity.hass.services.async_call.side_effect = mark_first_inverse

    undone = h.say("Mach das rückgängig.")

    assert undone.targets == {HALL}, (undone.speech, undone.calls)
    assert "Extern verändert" in undone.speech
    assert "Bürolicht" in undone.speech
    assert h.say("Mach das rückgängig.").calls == []


def test_service_disappearing_between_inverse_steps_stops_remaining_write(
    monkeypatch,
):
    h = _compound_house(monkeypatch)
    available = {"turn_off": True}

    def services_for_domain(domain):
        if domain != "light" or not available["turn_off"]:
            return {}
        return {"turn_off": SimpleNamespace(schema=None)}

    h.entity.hass.services.async_services_for_domain = services_for_domain
    original = h.entity.hass.services.async_call.side_effect

    async def remove_after_first_inverse(domain, service, data=None, **kwargs):
        result = await original(domain, service, data, **kwargs)
        if service == "turn_off" and (data or {}).get("entity_id") == HALL:
            available["turn_off"] = False
        return result

    h.entity.hass.services.async_call.side_effect = remove_after_first_inverse

    undone = h.say("Mach das rückgängig.")

    assert undone.targets == {HALL}, (undone.speech, undone.calls)
    assert "Dienst" in undone.speech
    assert h.say("Mach das rückgängig.").calls == []


def test_target_becoming_unavailable_between_steps_is_not_written(
    monkeypatch,
):
    h = _compound_house(monkeypatch)
    original = h.entity.hass.services.async_call.side_effect

    async def make_unavailable(domain, service, data=None, **kwargs):
        result = await original(domain, service, data, **kwargs)
        if service == "turn_off" and (data or {}).get("entity_id") == HALL:
            h.entity.hass.states._states[OFFICE] = State(
                OFFICE, "unavailable", {}
            )
        return result

    h.entity.hass.services.async_call.side_effect = make_unavailable

    undone = h.say("Mach das rückgängig.")

    assert undone.targets == {HALL}, (undone.speech, undone.calls)
    assert "Bürolicht" in undone.speech


def test_same_target_evidence_advances_only_after_causal_inverse():
    target = "climate.office"
    first = ServiceCallPlan(
        "climate", "set_hvac_mode", target, {"hvac_mode": "heat"}
    )
    second = ServiceCallPlan(
        "climate", "set_temperature", target, {"temperature": 20}
    )
    original = State(target, "off", {"temperature": 25})
    original.context = SimpleNamespace(id="source-execution", parent_id=None)
    states = {target: original}
    stored = bind_undo_to_observed_states(
        UndoPlan((first, second), "admin"), states.get
    )
    assert stored is not None
    remainder = UndoRemainder.from_undo(stored)

    foreign = State(target, "heat", {"temperature": 25})
    foreign.context = SimpleNamespace(id="foreign", parent_id=None)
    states[target] = foreign
    assert bind_undo_to_observed_states(
        UndoPlan((first,), "admin"),
        states.get,
        executed=first,
        execution_id="inverse-1",
    ) is None

    causal = State(target, "heat", {"temperature": 25})
    causal.context = SimpleNamespace(id="reported", parent_id="inverse-1")
    states[target] = causal
    observed = bind_undo_to_observed_states(
        UndoPlan((first,), "admin"),
        states.get,
        executed=first,
        execution_id="inverse-1",
    )
    assert observed is not None
    advanced = advance_undo_remainder(
        remainder, first, observed, "inverse-1"
    )

    assert advanced.original_observed_states == stored.observed_states
    assert advanced.original_observed_states is remainder.original_observed_states
    assert advanced.observed_states == observed.observed_states
    assert advanced.remaining_plans == (second,)
    assert advanced.completed_steps[0].execution_id == "inverse-1"


def test_observed_state_signature_is_deeply_immutable():
    entity_id = "light.office"
    state = State(
        entity_id,
        "on",
        {"effects": ["reading", {"colors": [255, 120, 0]}]},
    )
    stored = bind_undo_to_observed_states(
        UndoPlan((ServiceCallPlan("light", "turn_off", entity_id),), "admin"),
        {entity_id: state}.get,
    )

    assert stored is not None
    attribute_signature = stored.observed_states[0][1][1]

    def assert_frozen(value):
        assert not isinstance(value, (dict, list, set))
        if isinstance(value, tuple):
            for item in value:
                assert_frozen(item)

    assert_frozen(attribute_signature)


def test_partial_result_distinguishes_same_target_operations():
    target = "climate.office"
    mode = ServiceCallPlan(
        "climate", "set_hvac_mode", target, {"hvac_mode": "heat"}
    )
    temperature = ServiceCallPlan(
        "climate", "set_temperature", target, {"temperature": 20}
    )
    remainder = UndoRemainder(
        remaining_plans=(),
        original_observed_states=(),
        observed_states=(),
        completed_steps=(CompletedUndoStep(mode, "inverse-1", ()),),
        aborted_plans=(temperature,),
    )

    text = partial_undo_failure_text(
        remainder,
        {target: "Büroheizung"},
        "Die Temperatur wurde extern verändert.",
    )

    assert "Büroheizung (Betriebsmodus)" in text
    assert "Büroheizung (Temperatur)" in text


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    count=st.integers(min_value=2, max_value=8),
    data=st.data(),
    brightness=st.integers(min_value=1, max_value=255),
)
def test_property_external_change_between_any_inverse_pair_is_never_evidence(
    count, data, brightness
):
    changed_index = data.draw(st.integers(min_value=1, max_value=count - 1))
    plans = tuple(
        ServiceCallPlan("light", "turn_off", f"light.target_{index}")
        for index in range(count)
    )
    states = {
        f"light.target_{index}": State(f"light.target_{index}", "on", {})
        for index in range(count)
    }
    stored = bind_undo_to_observed_states(UndoPlan(plans, "admin"), states.get)
    assert stored is not None
    remainder = UndoRemainder.from_undo(stored)

    for index in range(changed_index):
        plan = plans[index]
        states[plan.entity_id] = State(plan.entity_id, "off", {})
        observed = bind_undo_to_observed_states(
            UndoPlan((plan,), "admin"),
            states.get,
            executed=plan,
            execution_id=f"inverse-{index}",
        )
        assert observed is not None
        remainder = advance_undo_remainder(
            remainder, plan, observed, f"inverse-{index}"
        )

    untouched_original = remainder.original_observed_states
    changed_plan = plans[changed_index]
    states[changed_plan.entity_id] = State(
        changed_plan.entity_id, "on", {"brightness": brightness}
    )
    guard = next_undo_guard(remainder)

    assert guard is not None
    assert not undo_matches_observed_states(guard, states.get)
    assert remainder.original_observed_states is untouched_original
    assert dict(remainder.observed_states)[changed_plan.entity_id] == dict(
        stored.observed_states
    )[changed_plan.entity_id]
