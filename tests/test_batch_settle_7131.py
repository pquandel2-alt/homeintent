"""7.13.1: batch results are final only after the shared effect wait.

In 7.13.0 an independent light batch stored a target as ``UNVERIFIED`` right
after its service call; ``async_settle_turn`` later confirmed the effect but
only updated the general turn outcomes.  A device that reported 100 ms after
the call was announced as not verified, missing from Undo and audited
wrongly.

Since 7.13.1 the executor builds a ``PendingBatchExecution``, observes every
accepted call together in one bounded, event-driven window and only then
creates the immutable ``BatchExecutionResult``.  Reply, audit and Undo are
derived from that final result alone.

The fake Home Assistant has a real event bus double: a device reports by
writing its state and firing ``state_changed`` after ``delay`` seconds.
"""

from __future__ import annotations

import asyncio
import time
import types
from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

import _ha_stub

_ha_stub.install()

from homeassistant.core import HomeAssistant, State  # noqa: E402

from _testhaus import HouseConversation, house_entities, with_states  # noqa: E402
from homeintent.audit_log import AuditTrail, render_today  # noqa: E402
from homeintent.batch_execution import (  # noqa: E402
    BatchExecutionResult,
    BatchTargetResult,
    BatchTargetStatus,
    PendingBatchExecution,
    batch_execution_speech,
)
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.service_executor import async_execute_service_plan  # noqa: E402
from homeintent.turn_outcome import TurnOutcomeKind, begin_outcomes, end_outcomes  # noqa: E402

WAIT = 0.5


class FakeBus:
    def __init__(self) -> None:
        self.listeners: list[tuple[str, Callable[[Any], None]]] = []

    def async_listen(self, event_type: str, callback: Callable[[Any], None]) -> Callable[[], None]:
        entry = (event_type, callback)
        self.listeners.append(entry)

        def remove() -> None:
            if entry in self.listeners:
                self.listeners.remove(entry)

        return remove

    def fire(self, event_type: str, data: dict[str, Any]) -> None:
        event = types.SimpleNamespace(event_type=event_type, data=data)
        for kind, callback in list(self.listeners):
            if kind == event_type:
                callback(event)


@dataclass
class Behaviour:
    """What one device does after a call: report ``state`` after ``delay``."""

    state: str | None = None  # None: the device does not report at all
    delay: float = 0.0
    raises: bool = False
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Devices:
    hass: Any
    behaviour: dict[str, Behaviour]
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    async def call(self, domain, service, data=None, **_kwargs):
        data = dict(data or {})
        entity_id = data["entity_id"]
        self.calls.append((domain, service, entity_id))
        behaviour = self.behaviour.get(entity_id, Behaviour())
        if behaviour.raises:
            raise RuntimeError("device offline")
        if behaviour.state is None:
            return None
        if behaviour.delay <= 0:
            self.write(entity_id, behaviour)
        else:
            asyncio.get_running_loop().call_later(behaviour.delay, self.write, entity_id, behaviour)
        return None

    def write(self, entity_id: str, behaviour: Behaviour) -> None:
        old = self.hass.states.get(entity_id)
        attributes = {**(getattr(old, "attributes", None) or {}), **behaviour.attributes}
        new = State(entity_id, behaviour.state, attributes)
        self.hass.states._states[entity_id] = new
        if getattr(self.hass, "bus", None) is not None:
            self.hass.bus.fire("state_changed", {"entity_id": entity_id, "old_state": old, "new_state": new})


def _light(entity_id: str, name: str, state: str = "on", capabilities=("TURN_ON", "TURN_OFF")) -> EntitySnapshot:
    return EntitySnapshot(entity_id, name, "light", state, capabilities=frozenset(capabilities))


def _hass(entities, behaviour, *, bus: bool = True) -> tuple[HomeAssistant, Devices]:
    hass = HomeAssistant()
    if bus:
        hass.bus = FakeBus()
    for entity in entities:
        hass.states._states[entity.entity_id] = State(entity.entity_id, entity.state, dict(entity.attributes))
    devices = Devices(hass, behaviour)
    hass.services.async_call.side_effect = devices.call
    return hass, devices


def _run(hass, plan, entities, *, wait: float = WAIT, audit: AuditTrail | None = None):
    async def go():
        outcomes, token = begin_outcomes()
        try:
            started = time.monotonic()
            result = await async_execute_service_plan(
                hass, plan, entities, {"effect_wait_seconds": wait},
                is_admin=True, user_id="admin", confirmed=False, audit_trail=audit,
                audit_actor_id="admin",
            )
            return result, outcomes, time.monotonic() - started
        finally:
            end_outcomes(token)

    return asyncio.run(go())


FOUR = [
    _light("light.a", "Licht A"),
    _light("light.b", "Licht B"),
    _light("light.c", "Licht C"),
    _light("light.d", "Licht D"),
]
PLAN_OFF = ServiceCallPlan("light", "turn_off", [entity.entity_id for entity in FOUR])


def _statuses(result) -> list[BatchTargetStatus]:
    return [item.status for item in result.batch_result.targets]


# ------------------------------------------------------------ the example of the task
def test_mixed_batch_is_final_only_after_the_shared_wait():
    hass, devices = _hass(FOUR, {
        "light.a": Behaviour("off"),
        "light.b": Behaviour("off", delay=0.1),
        "light.c": Behaviour(raises=True),
        "light.d": Behaviour(None),
    })
    audit = AuditTrail()
    result, outcomes, elapsed = _run(hass, PLAN_OFF, FOUR, audit=audit)

    assert _statuses(result) == [
        BatchTargetStatus.SUCCESS,
        BatchTargetStatus.SUCCESS,
        BatchTargetStatus.FAILED,
        BatchTargetStatus.TIMEOUT,
    ]
    assert result.batch_result.succeeded_entity_ids == {"light.a", "light.b"}
    assert batch_execution_speech(result.batch_result) == (
        "Zwei Lichter wurden ausgeschaltet. Licht C ist fehlgeschlagen. "
        "Licht D hat sich nicht rechtzeitig zurückgemeldet."
    )
    # One bounded window for all targets, not one per device.
    assert WAIT * 0.9 <= elapsed < WAIT + 0.4
    # Turn outcomes come from the final result; nothing is left to settle.
    assert outcomes.pending == []
    assert outcomes.kinds.count(TurnOutcomeKind.EXECUTED) == 2
    assert TurnOutcomeKind.NOT_DONE in outcomes.kinds
    assert TurnOutcomeKind.UNCONFIRMED in outcomes.kinds
    # The audit holds every target's final status.
    (entry,) = audit.today(entry_now(audit))
    assert entry.entity_ids == ("light.a", "light.b", "light.d")
    assert entry.outcomes == (
        ("light.a", "success"), ("light.b", "success"),
        ("light.c", "failed"), ("light.d", "timeout"),
    )
    assert "nicht bestätigt: light.c, light.d" in render_today((entry,))


def entry_now(audit: AuditTrail):
    return audit._entries[-1].timestamp  # noqa: SLF001 - test reads its own trail


def test_all_targets_confirm_immediately_without_waiting():
    hass, _devices = _hass(FOUR, {entity.entity_id: Behaviour("off") for entity in FOUR})
    result, _outcomes, elapsed = _run(hass, PLAN_OFF, FOUR)
    assert result.batch_result.fully_successful
    assert elapsed < 0.2


def test_all_targets_confirm_late_inside_one_window():
    hass, _devices = _hass(FOUR, {
        entity.entity_id: Behaviour("off", delay=0.15) for entity in FOUR
    })
    result, _outcomes, elapsed = _run(hass, PLAN_OFF, FOUR, wait=2.0)
    assert result.batch_result.fully_successful
    # Four late devices still need one shared window (~0.15 s), never 4 x.
    assert elapsed < 0.5


def test_mix_of_immediate_and_late_confirmations_is_fully_successful():
    hass, _devices = _hass(FOUR, {
        "light.a": Behaviour("off"),
        "light.b": Behaviour("off", delay=0.05),
        "light.c": Behaviour("off"),
        "light.d": Behaviour("off", delay=0.2),
    })
    result, _outcomes, _elapsed = _run(hass, PLAN_OFF, FOUR)
    assert result.batch_result.fully_successful


def test_report_just_before_the_timeout_counts():
    hass, _devices = _hass(FOUR[:2], {
        "light.a": Behaviour("off"),
        "light.b": Behaviour("off", delay=WAIT * 0.8),
    })
    plan = ServiceCallPlan("light", "turn_off", ["light.a", "light.b"])
    result, _outcomes, _elapsed = _run(hass, plan, FOUR[:2])
    assert _statuses(result) == [BatchTargetStatus.SUCCESS, BatchTargetStatus.SUCCESS]


def test_report_after_the_timeout_is_a_timeout():
    hass, _devices = _hass(FOUR[:2], {
        "light.a": Behaviour("off"),
        "light.b": Behaviour("off", delay=WAIT + 0.3),
    })
    plan = ServiceCallPlan("light", "turn_off", ["light.a", "light.b"])
    result, _outcomes, _elapsed = _run(hass, plan, FOUR[:2])
    assert _statuses(result) == [BatchTargetStatus.SUCCESS, BatchTargetStatus.TIMEOUT]
    assert result.batch_result.succeeded_entity_ids == {"light.a"}


def test_unchanged_state_is_a_timeout_and_never_success():
    hass, _devices = _hass(FOUR[:2], {})
    plan = ServiceCallPlan("light", "turn_off", ["light.a", "light.b"])
    result, _outcomes, _elapsed = _run(hass, plan, FOUR[:2], wait=0.2)
    assert _statuses(result) == [BatchTargetStatus.TIMEOUT, BatchTargetStatus.TIMEOUT]
    assert result.batch_result.succeeded_entity_ids == frozenset()


def test_device_becoming_unavailable_after_the_call_is_reported_so():
    hass, _devices = _hass(FOUR[:2], {
        "light.a": Behaviour("off"),
        "light.b": Behaviour("unavailable", delay=0.05),
    })
    plan = ServiceCallPlan("light", "turn_off", ["light.a", "light.b"])
    result, _outcomes, _elapsed = _run(hass, plan, FOUR[:2])
    assert _statuses(result) == [BatchTargetStatus.SUCCESS, BatchTargetStatus.UNAVAILABLE]


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
def test_unreliable_state_before_the_call_is_never_called(state):
    entities = [_light("light.a", "Licht A"), _light("light.b", "Licht B", state=state)]
    hass, devices = _hass(entities, {"light.a": Behaviour("off")})
    plan = ServiceCallPlan("light", "turn_off", ["light.a", "light.b"])
    result, _outcomes, _elapsed = _run(hass, plan, entities)
    assert _statuses(result) == [BatchTargetStatus.SUCCESS, BatchTargetStatus.UNAVAILABLE]
    assert devices.calls == [("light", "turn_off", "light.a")]


def test_unsupported_target_is_skipped_and_others_settle():
    entities = [_light("light.a", "Licht A"), _light("light.oven", "Backofen", capabilities=("TURN_ON",))]
    hass, devices = _hass(entities, {"light.a": Behaviour("off", delay=0.05)})
    plan = ServiceCallPlan("light", "turn_off", ["light.a", "light.oven"])
    result, _outcomes, _elapsed = _run(hass, plan, entities)
    assert _statuses(result) == [BatchTargetStatus.SUCCESS, BatchTargetStatus.UNSUPPORTED]
    assert devices.calls == [("light", "turn_off", "light.a")]


def test_without_event_bus_only_the_visible_state_decides():
    hass, _devices = _hass(FOUR[:2], {
        "light.a": Behaviour("off"),
        "light.b": Behaviour("off", delay=0.05),
    }, bus=False)
    plan = ServiceCallPlan("light", "turn_off", ["light.a", "light.b"])
    result, _outcomes, elapsed = _run(hass, plan, FOUR[:2])
    # Nothing can be observed: no wait, the late device is unverified.
    assert _statuses(result) == [BatchTargetStatus.SUCCESS, BatchTargetStatus.UNVERIFIED]
    assert elapsed < 0.2


def test_contrary_cover_movement_is_contrary_not_success():
    covers = [
        EntitySnapshot("cover.a", "Rollladen A", "cover", "open", device_class="shutter"),
        EntitySnapshot("cover.b", "Rollladen B", "cover", "open", device_class="shutter"),
    ]
    hass, _devices = _hass(covers, {
        "cover.a": Behaviour("closing", delay=0.05),
        "cover.b": Behaviour("opening", delay=0.05),
    })
    plan = ServiceCallPlan("cover", "close_cover", ["cover.a", "cover.b"])
    result, _outcomes, _elapsed = _run(hass, plan, covers)
    assert _statuses(result) == [BatchTargetStatus.SUCCESS, BatchTargetStatus.CONTRARY]
    assert "Rollladen B meldet einen anderen Zustand." in batch_execution_speech(result.batch_result)


# ------------------------------------------------------------ result types
def test_final_result_never_contains_a_pending_target():
    plan = ServiceCallPlan("light", "turn_off", ["light.a"])
    with pytest.raises(ValueError):
        BatchExecutionResult(plan, (BatchTargetResult("light.a", "A", BatchTargetStatus.PENDING),))


def test_finalize_maps_verdicts_and_observation():
    plan = ServiceCallPlan("light", "turn_off", ["light.a", "light.b", "light.c", "light.d"])
    pending = PendingBatchExecution(plan, (
        BatchTargetResult("light.a", "A", BatchTargetStatus.PENDING),
        BatchTargetResult("light.b", "B", BatchTargetStatus.PENDING),
        BatchTargetResult("light.c", "C", BatchTargetStatus.FAILED),
        BatchTargetResult("light.d", "D", BatchTargetStatus.PENDING),
    ))
    assert pending.pending_entity_ids == ("light.a", "light.b", "light.d")
    observed = pending.finalize({"light.a": "reached", "light.b": "contrary"}, observed=True)
    assert [item.status for item in observed.targets] == [
        BatchTargetStatus.SUCCESS, BatchTargetStatus.CONTRARY,
        BatchTargetStatus.FAILED, BatchTargetStatus.TIMEOUT,
    ]
    unobserved = pending.finalize({"light.a": "moving"}, observed=False)
    assert unobserved.targets[3].status is BatchTargetStatus.UNVERIFIED
    assert unobserved.targets[0].status is BatchTargetStatus.SUCCESS


# ------------------------------------------------------------ whole conversation pipeline
OFFICE = ("light.buerolicht", "light.deckenfluter_buero", "light.schreibtischlampe")


def _scope(house) -> str:
    from homeintent.conversation_scope import conversation_scope_key_for_input

    return conversation_scope_key_for_input(house.conversation_input("scope"))


def _office_house(monkeypatch, tmp_path, behaviour):
    entities = with_states(
        house_entities(),
        light__buerolicht="on", light__deckenfluter_buero="on", light__schreibtischlampe="on",
    )
    house = HouseConversation(
        monkeypatch, entities=entities, tmp_path=None,
        options={"effect_wait_seconds": WAIT, "response_style": "spoken"},
    )
    hass = house.entity.hass
    hass.bus = FakeBus()
    for entity in entities:
        hass.states._states[entity.entity_id] = State(entity.entity_id, entity.state, dict(entity.attributes))
    devices = Devices(hass, behaviour)
    hass.services.async_call.side_effect = devices.call
    return house, devices


def test_delayed_light_batch_speech_undo_and_audit_agree(monkeypatch, tmp_path):
    house, devices = _office_house(monkeypatch, tmp_path, {
        "light.buerolicht": Behaviour("off"),
        "light.deckenfluter_buero": Behaviour("off", delay=0.1),
        "light.schreibtischlampe": Behaviour(None),
    })
    turn = house.say("Mach das Licht im Büro aus.")
    assert sorted(entity_id for _d, _s, entity_id in devices.calls) == sorted(OFFICE)
    # One final statement; no later settle note contradicts it.
    assert turn.speech == (
        "Zwei Lichter wurden ausgeschaltet. "
        "Schreibtischlampe hat sich nicht rechtzeitig zurückgemeldet."
    ), turn.speech
    assert "noch nicht zurückgemeldet" not in turn.speech
    stored = house.entity._context_store.get(_scope(house))  # noqa: SLF001
    undo_ids = {
        entity_id
        for plan in stored.pending_undo.plans
        for entity_id in ([plan.entity_id] if isinstance(plan.entity_id, str) else plan.entity_id)
    }
    assert undo_ids == {"light.buerolicht", "light.deckenfluter_buero"}
    entry = house.entity._audit_trail._entries[-1]  # noqa: SLF001
    assert dict(entry.outcomes) == {
        "light.buerolicht": "success",
        "light.deckenfluter_buero": "success",
        "light.schreibtischlampe": "timeout",
    }
    # Undo restores exactly the confirmed targets.
    devices.calls.clear()
    devices.behaviour = {entity_id: Behaviour("on") for entity_id in OFFICE}
    house.say("Mach das rückgängig.")
    assert sorted(entity_id for _d, _s, entity_id in devices.calls) == [
        "light.buerolicht", "light.deckenfluter_buero",
    ]


def test_fully_delayed_light_batch_is_a_plain_success(monkeypatch, tmp_path):
    house, _devices = _office_house(monkeypatch, tmp_path, {
        entity_id: Behaviour("off", delay=0.1) for entity_id in OFFICE
    })
    turn = house.say("Mach das Licht im Büro aus.")
    assert "zurückgemeldet" not in turn.speech and "verifiziert" not in turn.speech, turn.speech
    stored = house.entity._context_store.get(_scope(house))  # noqa: SLF001
    assert {
        entity_id
        for plan in stored.pending_undo.plans
        for entity_id in ([plan.entity_id] if isinstance(plan.entity_id, str) else plan.entity_id)
    } == set(OFFICE)
    entry = house.entity._audit_trail._entries[-1]  # noqa: SLF001
    assert set(dict(entry.outcomes).values()) == {"success"}
