"""7.13.1: independent standard covers really run as a partial batch.

7.13.0 documented partial execution for light *and* cover batches, but the
batch path only ran at ``RiskLevel.LOW``; covers are ``MEDIUM`` and silently
fell back to one shared service call.

Since 7.13.1 batch *eligibility* is modelled separately from risk
(``batch_execution.batch_eligibility``):

* covers stay ``MEDIUM``; garage doors and gates stay ``CRITICAL``;
* only standard covers (shutter, blind, shade, curtain) with a supported
  open/close/position service are eligible - any other or missing class keeps
  the established all-or-nothing, confirmation-gated path;
* the complete preflight of every target finishes before the first call;
* results go through the same settle-able batch structure as lights.
"""

from __future__ import annotations

import asyncio

import pytest

import _ha_stub

_ha_stub.install()

from homeassistant.core import State  # noqa: E402

from _testhaus import HouseConversation, house_entities, with_states  # noqa: E402
from test_batch_settle_7131 import Behaviour, Devices, FakeBus, _hass, _run, _scope  # noqa: E402
from homeintent import service_executor  # noqa: E402
from homeintent.batch_execution import (  # noqa: E402
    BatchEligibility,
    BatchTarget,
    BatchTargetStatus,
    batch_eligibility,
    batch_execution_speech,
)
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.risk import RiskLevel, classify_service_plan  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.service_executor import (  # noqa: E402
    ConfirmedScope,
    async_execute_service_plan,
    confirmed_scope,
)

WAIT = 0.5


def _cover(entity_id: str, name: str, device_class: str | None = "shutter", state: str = "open",
           position: int = 100) -> EntitySnapshot:
    return EntitySnapshot(
        entity_id, name, "cover", state, device_class=device_class,
        attributes={"current_position": position},
        capabilities=frozenset({"POSITION"}),
    )


LEFT = _cover("cover.links", "Rollladen links")
RIGHT = _cover("cover.rechts", "Rollladen rechts")
KITCHEN = _cover("cover.kueche", "Küchenrollladen")
GARAGE = _cover("cover.garagentor", "Garagentor", device_class="garage", state="closed", position=0)
STANDARD = [LEFT, RIGHT, KITCHEN]


def _statuses(result):
    return [item.status for item in result.batch_result.targets]


# ------------------------------------------------------------ classification
def test_covers_stay_medium_and_garage_doors_critical():
    assert classify_service_plan(ServiceCallPlan("cover", "close_cover", LEFT.entity_id), STANDARD) is RiskLevel.MEDIUM
    for service, data in (("open_cover", {}), ("close_cover", {}), ("set_cover_position", {"position": 50})):
        plan = ServiceCallPlan("cover", service, GARAGE.entity_id, data)
        assert classify_service_plan(plan, [GARAGE]) is RiskLevel.CRITICAL, service


@pytest.mark.parametrize("device_class,eligible", [
    ("shutter", True), ("blind", True), ("shade", True), ("curtain", True),
    ("garage", False), ("gate", False), ("door", False), ("window", False),
    ("damper", False), ("awning", False), (None, False),
])
def test_eligibility_depends_on_cover_class_not_on_risk(device_class, eligible):
    targets = (
        BatchTarget("cover.a", "cover", "shutter", True),
        BatchTarget("cover.b", "cover", device_class, True),
    )
    result = batch_eligibility("close_cover", targets)
    assert (result is BatchEligibility.ELIGIBLE) is eligible


def test_eligibility_rejects_unknown_cover_mixed_domains_and_other_services():
    shutter = BatchTarget("cover.a", "cover", "shutter", True)
    assert batch_eligibility("close_cover", (shutter, BatchTarget("cover.x", "cover", None, False))) is (
        BatchEligibility.DEPENDENT_TARGET
    )
    assert batch_eligibility("turn_off", (shutter, BatchTarget("light.a", "light", None, True))) is (
        BatchEligibility.MIXED_DOMAINS
    )
    assert batch_eligibility("stop_cover", (shutter, shutter)) is BatchEligibility.UNSUPPORTED_SERVICE
    assert batch_eligibility("close_cover", (shutter,)) is BatchEligibility.SINGLE_TARGET


# ------------------------------------------------------------ executor
@pytest.mark.parametrize("service,data,report", [
    ("close_cover", {}, {"state": "closed", "attributes": {"current_position": 0}}),
    ("open_cover", {}, {"state": "open", "attributes": {"current_position": 100}}),
    ("set_cover_position", {"position": 40}, {"state": "open", "attributes": {"current_position": 40}}),
])
def test_every_standard_cover_runs_on_its_own(service, data, report):
    entities = [_cover("cover.a", "A", position=60, state="open"), _cover("cover.b", "B", position=60, state="open")]
    hass, devices = _hass(entities, {
        entity.entity_id: Behaviour(report["state"], attributes=report["attributes"]) for entity in entities
    })
    plan = ServiceCallPlan("cover", service, ["cover.a", "cover.b"], data)
    result, _outcomes, _elapsed = _run(hass, plan, entities, wait=WAIT)
    assert result.executed
    assert _statuses(result) == [BatchTargetStatus.SUCCESS, BatchTargetStatus.SUCCESS]
    assert devices.calls == [("cover", service, "cover.a"), ("cover", service, "cover.b")]


def test_one_unavailable_cover_does_not_stop_the_others():
    unavailable = _cover("cover.rechts", "Rollladen rechts", state="unavailable")
    entities = [LEFT, unavailable, KITCHEN]
    hass, devices = _hass(entities, {
        LEFT.entity_id: Behaviour("closed", attributes={"current_position": 0}),
        KITCHEN.entity_id: Behaviour("closed", attributes={"current_position": 0}),
    })
    plan = ServiceCallPlan("cover", "close_cover", [entity.entity_id for entity in entities])
    result, _outcomes, _elapsed = _run(hass, plan, entities)
    assert _statuses(result) == [
        BatchTargetStatus.SUCCESS, BatchTargetStatus.UNAVAILABLE, BatchTargetStatus.SUCCESS,
    ]
    assert [entity_id for _d, _s, entity_id in devices.calls] == ["cover.links", "cover.kueche"]
    assert batch_execution_speech(result.batch_result) == (
        "Zwei Rollläden wurden geschlossen. Rollladen rechts ist nicht verfügbar."
    )


def test_a_service_error_of_one_cover_does_not_block_the_others():
    hass, devices = _hass(STANDARD, {
        LEFT.entity_id: Behaviour(raises=True),
        RIGHT.entity_id: Behaviour("closed", attributes={"current_position": 0}),
        KITCHEN.entity_id: Behaviour("closed", attributes={"current_position": 0}),
    })
    plan = ServiceCallPlan("cover", "close_cover", [entity.entity_id for entity in STANDARD])
    result, _outcomes, _elapsed = _run(hass, plan, STANDARD)
    assert _statuses(result) == [
        BatchTargetStatus.FAILED, BatchTargetStatus.SUCCESS, BatchTargetStatus.SUCCESS,
    ]
    assert len(devices.calls) == 3
    assert result.batch_result.succeeded_entity_ids == {"cover.rechts", "cover.kueche"}


def test_late_cover_positions_and_movement_confirm_inside_the_window():
    entities = [_cover("cover.a", "A", position=100), _cover("cover.b", "B", position=100)]
    hass, _devices = _hass(entities, {
        # Travelling in the requested direction is a success ("es fährt").
        "cover.a": Behaviour("closing", delay=0.1, attributes={"current_position": 80}),
        "cover.b": Behaviour("open", delay=0.2, attributes={"current_position": 30}),
    })
    plan = ServiceCallPlan("cover", "set_cover_position", ["cover.a", "cover.b"], {"position": 30})
    result, _outcomes, elapsed = _run(hass, plan, entities)
    assert _statuses(result) == [BatchTargetStatus.SUCCESS, BatchTargetStatus.SUCCESS]
    assert elapsed < WAIT


def test_mixed_target_set_with_a_garage_door_keeps_the_atomic_confirmation_path():
    entities = [LEFT, GARAGE]
    hass, devices = _hass(entities, {})
    plan = ServiceCallPlan("cover", "open_cover", [LEFT.entity_id, GARAGE.entity_id])
    result, _outcomes, _elapsed = _run(hass, plan, entities)
    assert not result.executed
    assert result.batch_result is None  # never split into per-target calls
    assert devices.calls == []
    assert result.decision.risk is RiskLevel.CRITICAL


def test_a_cover_without_device_class_keeps_the_atomic_path():
    unclassified = _cover("cover.x", "Unbekannt", device_class=None)
    entities = [LEFT, unclassified]
    hass, devices = _hass(entities, {
        LEFT.entity_id: Behaviour("closed"), unclassified.entity_id: Behaviour("closed"),
    })
    plan = ServiceCallPlan("cover", "close_cover", [LEFT.entity_id, unclassified.entity_id])
    result, _outcomes, _elapsed = _run(hass, plan, entities)
    assert result.batch_result is None
    # One shared call, exactly like before 7.13.1.
    assert len(hass.services.async_call.await_args_list) == 1


def test_a_not_exposed_cover_is_unauthorized_and_never_called():
    hass, devices = _hass([LEFT], {LEFT.entity_id: Behaviour("closed", attributes={"current_position": 0})})
    plan = ServiceCallPlan("cover", "close_cover", [LEFT.entity_id, "cover.versteckt"])
    result, _outcomes, _elapsed = _run(hass, plan, [LEFT])
    # A target outside the exposed snapshot has no known class: atomic path,
    # which refuses the whole plan without any call.
    assert devices.calls == []
    assert not result.executed


def test_confirmation_never_covers_a_changed_cover_set():
    hass, devices = _hass(STANDARD, {})
    plan = ServiceCallPlan("cover", "close_cover", [entity.entity_id for entity in STANDARD])
    result = asyncio.run(async_execute_service_plan(
        hass, plan, STANDARD, {}, is_admin=True, user_id="admin", confirmed=True,
        scope=ConfirmedScope(RiskLevel.MEDIUM, frozenset({LEFT.entity_id, RIGHT.entity_id})),
    ))
    assert not result.executed
    assert {item.status for item in result.batch_result.targets} == {BatchTargetStatus.NOT_CONFIRMED}
    assert devices.calls == []


def test_broad_cover_batch_needs_confirmation_then_runs_per_target():
    covers = [_cover(f"cover.r{index}", f"Rollladen {index}") for index in range(6)]
    plan = ServiceCallPlan("cover", "close_cover", [entity.entity_id for entity in covers])
    # Six targets: the broad-action tier makes the batch HIGH -> confirmation.
    assert classify_service_plan(plan, covers) is RiskLevel.HIGH
    hass, devices = _hass(covers, {
        entity.entity_id: Behaviour("closed", attributes={"current_position": 0}) for entity in covers
    })
    unconfirmed = asyncio.run(async_execute_service_plan(
        hass, plan, covers, {}, is_admin=True, user_id="admin", confirmed=False,
    ))
    assert not unconfirmed.executed and devices.calls == []
    assert {item.status for item in unconfirmed.batch_result.targets} == {BatchTargetStatus.NOT_CONFIRMED}
    scope = confirmed_scope(hass, (plan,), covers, {})
    confirmed = asyncio.run(async_execute_service_plan(
        hass, plan, covers, {}, is_admin=True, user_id="admin", confirmed=True, scope=scope,
    ))
    assert confirmed.executed and len(devices.calls) == 6
    assert confirmed.batch_result.fully_successful


def test_no_call_before_the_complete_preflight(monkeypatch):
    events: list[str] = []
    real_policy = service_executor.evaluate_service_plan

    def recording_policy(plan, *args, **kwargs):
        ids = plan.entity_id if isinstance(plan.entity_id, str) else "batch"
        events.append(f"check:{ids}")
        return real_policy(plan, *args, **kwargs)

    monkeypatch.setattr(service_executor, "evaluate_service_plan", recording_policy)
    hass, devices = _hass(STANDARD, {})

    async def recording_call(domain, service, data=None, **_kwargs):
        events.append(f"call:{data['entity_id']}")

    hass.services.async_call.side_effect = recording_call
    plan = ServiceCallPlan("cover", "close_cover", [entity.entity_id for entity in STANDARD])
    _run(hass, plan, STANDARD, wait=0.0)
    first_call = next(index for index, event in enumerate(events) if event.startswith("call:"))
    preflight = [event for event in events[:first_call] if event.startswith("check:cover.")]
    # Every target was checked before the first call (the executor checks the
    # first target once more immediately before its own call).
    assert set(preflight) == {f"check:{entity.entity_id}" for entity in STANDARD}


# ------------------------------------------------------------ whole conversation pipeline
LIVING = ("cover.wohnzimmer_rollladen_links", "cover.wohnzimmer_rollladen_rechts")


def _living_house(monkeypatch, behaviour, **states):
    entities = with_states(house_entities(), **states)
    house = HouseConversation(
        monkeypatch, entities=entities,
        options={"effect_wait_seconds": WAIT, "response_style": "spoken"},
    )
    hass = house.entity.hass
    hass.bus = FakeBus()
    for entity in entities:
        attributes = dict(entity.attributes)
        if entity.domain == "cover":
            attributes["current_position"] = 100 if entity.state == "open" else 0
        hass.states._states[entity.entity_id] = State(entity.entity_id, entity.state, attributes)
    devices = Devices(hass, behaviour)
    hass.services.async_call.side_effect = devices.call
    return house, devices


def test_delayed_cover_batch_through_the_conversation(monkeypatch):
    house, devices = _living_house(monkeypatch, {
        LIVING[0]: Behaviour("closing", delay=0.05, attributes={"current_position": 70}),
        LIVING[1]: Behaviour("closed", delay=0.2, attributes={"current_position": 0}),
    })
    turn = house.say("Fahr die Rollläden im Wohnzimmer runter.")
    assert sorted(entity_id for _d, _s, entity_id in devices.calls) == sorted(LIVING), turn.speech
    assert {service for _d, service, _e in devices.calls} == {"close_cover"}
    assert "zurückgemeldet" not in turn.speech and "verifiziert" not in turn.speech, turn.speech
    entry = house.entity._audit_trail._entries[-1]  # noqa: SLF001
    assert dict(entry.outcomes) == {LIVING[0]: "success", LIVING[1]: "success"}
    stored = house.entity._context_store.get(_scope(house))  # noqa: SLF001
    assert {
        entity_id
        for plan in stored.pending_undo.plans
        for entity_id in ([plan.entity_id] if isinstance(plan.entity_id, str) else plan.entity_id)
    } == set(LIVING)


def test_cover_batch_with_one_unreachable_cover_and_undo(monkeypatch):
    house, devices = _living_house(monkeypatch, {
        LIVING[0]: Behaviour("closed", delay=0.1, attributes={"current_position": 0}),
        LIVING[1]: Behaviour(raises=True),
    })
    turn = house.say("Fahr die Rollläden im Wohnzimmer runter.")
    assert turn.speech == (
        "Ein Rollladen wurde geschlossen. Rollladen Wohnzimmer rechts ist fehlgeschlagen."
    ) or turn.speech.startswith("Ein Rollladen wurde geschlossen."), turn.speech
    assert "fehlgeschlagen" in turn.speech
    entry = house.entity._audit_trail._entries[-1]  # noqa: SLF001
    assert dict(entry.outcomes) == {LIVING[0]: "success", LIVING[1]: "failed"}
    devices.calls.clear()
    devices.behaviour = {LIVING[0]: Behaviour("open", attributes={"current_position": 100})}
    house.say("Mach das rückgängig.")
    assert [entity_id for _d, _s, entity_id in devices.calls] == [LIVING[0]]
