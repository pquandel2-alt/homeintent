"""7.0.1: interactive V12 push actions fail closed.

Every case runs the real inbound path::

    mobile_app_notification_action event
      -> ProactiveAgentRuntime.async_handle_notification_action
      -> ProactiveRuntime.async_handle_push_action
      -> ProactiveContextEngine.async_handle_push_action
      -> (only if authorized) V10 runner -> policy-gated executor

Device calls are counted at the instrumented ``hass.services`` sink.  A
rejection must leave proposals, situations, permissions and devices untouched.
"""

from __future__ import annotations

from _ha_stub import ServiceMock  # noqa: E402
import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from v12_harness import NOW
import test_v12_runtime
from test_v12_runtime import Clock, _registry, _runtime

from homeintent.agent_runtime import ProactiveAgentRuntime
from homeintent.proactive_model import ProposalState
from homeassistant.core import State

scheduled = test_v12_runtime.scheduled  # re-exported pytest fixture
pytestmark = pytest.mark.usefixtures("scheduled")

DEVICE_DOMAINS = {"cover", "light", "lock", "homeassistant", "switch"}
PHILIPP_PHONE = SimpleNamespace(entity_id="notify.mobile_app_philipp", area_id=None, device_id="phone_p")


class Rig:
    def __init__(self, runtime, hass, calls, entities, data):
        self.runtime, self.hass, self.calls, self.entities, self.data = runtime, hass, calls, entities, data
        self.agent = ProactiveAgentRuntime(hass, runtime._entry, data)
        data.proactive_context = runtime

    async def open_garage(self) -> None:
        self.hass.states._states["person.philipp"] = State("person.philipp", "not_home")
        garage = replace(self.entities["cover.garage"], state="open", last_changed=NOW - timedelta(minutes=30))
        self.entities["cover.garage"] = garage
        await self.runtime.async_observe_state(garage, "closed", tuple(self.entities.values()))

    def pushed(self) -> dict:
        return [call for call in self.calls if call[0] == "notify"][-1][2]

    def action(self, index: int = 0) -> str:
        return self.pushed()["data"]["actions"][index]["action"]

    async def tap(self, action: str, *, user: str | None = "philipp", device: str | None = "phone_p") -> None:
        data = {"action": action}
        if device is not None:
            data["device_id"] = device
        await self.agent.async_handle_notification_action(SimpleNamespace(
            data=data, context=SimpleNamespace(user_id=user),
        ))

    def device_calls(self) -> list:
        return [call for call in self.calls if call[0] in DEVICE_DOMAINS]

    def snapshot(self) -> str:
        engine = self.runtime.engine
        return json.dumps({
            "proposals": engine.proposals.to_dict(),
            "situations": engine.situations.to_dict(),
            "permissions": engine.permissions.to_dict(),
            "entities": {key: value.state for key, value in self.entities.items()},
        }, sort_keys=True, default=str)

    def proposal_state(self) -> ProposalState:
        return self.runtime.engine.proposals.all()[-1].state


async def _rig(tmp_path, monkeypatch, registry=(PHILIPP_PHONE,)) -> Rig:
    _registry(monkeypatch, list(registry))
    rig = Rig(*await _runtime(tmp_path, monkeypatch))
    original = rig.hass.services.async_call.side_effect

    async def apply(domain, service, data, blocking=False):
        await original(domain, service, data, blocking)
        if (domain, service) == ("cover", "close_cover"):
            rig.entities["cover.garage"] = replace(rig.entities["cover.garage"], state="closed")

    rig.hass.services.async_call = ServiceMock(side_effect=apply)
    rig.data.effect_monitor.timeout = timedelta(milliseconds=20)
    return rig


def _tokenless(action: str) -> str:
    return action.rsplit("_", 1)[0]


def _run(coro_factory):
    async def wrapper():
        rig = await coro_factory()
        await rig.data.effect_monitor.async_close()
    asyncio.run(wrapper())


@pytest.mark.parametrize(("index", "name"), [(0, "ACCEPT"), (1, "LATER"), (2, "IGNORE")])
def test_v12_push_action_requires_device_identity(tmp_path, monkeypatch, index, name):
    """Covers *_accept/_later/_ignore_requires_device_id: no token -> rejected."""
    async def scenario():
        rig = await _rig(tmp_path, monkeypatch)
        await rig.open_garage()
        action = rig.action(index)
        assert action.startswith(f"HOMEINTENT_V12_{name}_")
        before = rig.snapshot()
        await rig.tap(_tokenless(action))
        await rig.tap(_tokenless(action), device=None)
        assert rig.snapshot() == before
        assert rig.device_calls() == []
        assert rig.proposal_state() is ProposalState.PENDING
        return rig

    _run(scenario)


def test_v12_push_accepts_bound_companion_device(tmp_path, monkeypatch):
    async def scenario():
        rig = await _rig(tmp_path, monkeypatch)
        await rig.open_garage()
        await rig.tap(rig.action(0))
        assert [call[:2] for call in rig.device_calls()] == [("cover", "close_cover")]
        assert rig.proposal_state() is ProposalState.EXECUTED
        return rig

    _run(scenario)


def test_v12_push_without_event_device_id_uses_token_bound_device(tmp_path, monkeypatch):
    """HA core forwards Companion event data unchanged and adds no device id;
    the token delivered only to the bound device is the device identity."""
    async def scenario():
        rig = await _rig(tmp_path, monkeypatch)
        await rig.open_garage()
        await rig.tap(rig.action(0), device=None)
        assert [call[:2] for call in rig.device_calls()] == [("cover", "close_cover")]
        return rig

    _run(scenario)


def test_v12_push_rejects_other_companion_device(tmp_path, monkeypatch):
    async def scenario():
        rig = await _rig(tmp_path, monkeypatch)
        await rig.open_garage()
        before = rig.snapshot()
        await rig.tap(rig.action(0), device="phone_anna")
        assert rig.snapshot() == before and rig.device_calls() == []
        return rig

    _run(scenario)


def test_v12_push_correct_user_and_wrong_device_is_rejected(tmp_path, monkeypatch):
    async def scenario():
        rig = await _rig(tmp_path, monkeypatch)
        await rig.open_garage()
        before = rig.snapshot()
        await rig.tap(rig.action(0), user="philipp", device="stolen_phone")
        assert rig.snapshot() == before and rig.device_calls() == []
        return rig

    _run(scenario)


def test_v12_push_wrong_user_and_correct_device_is_rejected(tmp_path, monkeypatch):
    async def scenario():
        rig = await _rig(tmp_path, monkeypatch)
        await rig.open_garage()
        before = rig.snapshot()
        for user in ("anna", "mallory", None):
            await rig.tap(rig.action(0), user=user, device="phone_p")
        assert rig.snapshot() == before and rig.device_calls() == []
        return rig

    _run(scenario)


def test_v12_push_missing_device_causes_zero_service_calls(tmp_path, monkeypatch):
    async def scenario():
        rig = await _rig(tmp_path, monkeypatch)
        await rig.open_garage()
        for index in range(3):
            await rig.tap(_tokenless(rig.action(index)), device=None)
        assert rig.device_calls() == []
        assert rig.proposal_state() is ProposalState.PENDING
        return rig

    _run(scenario)


def test_v12_push_unbound_device_causes_zero_service_calls(tmp_path, monkeypatch):
    async def scenario():
        rig = await _rig(tmp_path, monkeypatch)
        await rig.open_garage()
        forged = _tokenless(rig.action(0)) + "_t0123456789abcdef"
        before = rig.snapshot()
        await rig.tap(forged)
        await rig.tap(forged, device=None)
        assert rig.snapshot() == before and rig.device_calls() == []
        return rig

    _run(scenario)


def test_v12_push_replay_causes_zero_additional_service_calls(tmp_path, monkeypatch):
    async def scenario():
        rig = await _rig(tmp_path, monkeypatch)
        await rig.open_garage()
        action, ignore = rig.action(0), rig.action(2)
        await rig.tap(action)
        assert len(rig.device_calls()) == 1
        after_first = rig.snapshot()
        for _ in range(3):
            await rig.tap(action)
            await rig.tap(ignore)
        assert len(rig.device_calls()) == 1
        assert rig.snapshot() == after_first
        return rig

    _run(scenario)


def test_v12_push_expired_proposal_is_rejected_without_mutation(tmp_path, monkeypatch):
    async def scenario():
        rig = await _rig(tmp_path, monkeypatch)
        await rig.open_garage()
        action = rig.action(0)
        Clock.now = NOW + timedelta(hours=2)
        try:
            before = rig.snapshot()
            await rig.tap(action)
            assert rig.snapshot() == before and rig.device_calls() == []
        finally:
            Clock.now = NOW
        return rig

    _run(scenario)


def test_v12_push_rejects_when_no_authoritative_device_binding_exists(tmp_path, monkeypatch):
    async def scenario():
        rig = await _rig(tmp_path, monkeypatch, registry=())
        await rig.open_garage()
        proposal = rig.runtime.engine.proposals.all()[-1]
        assert proposal.push_bindings == ()
        before = rig.snapshot()
        base = f"HOMEINTENT_V12_ACCEPT_{proposal.proposal_id}"
        for action in (base, base + "_t0123456789abcdef"):
            for device in ("phone_p", None):
                await rig.tap(action, device=device)
        assert rig.snapshot() == before and rig.device_calls() == []
        return rig

    _run(scenario)


def test_v12_unbound_push_target_does_not_offer_executable_action_buttons(tmp_path, monkeypatch):
    async def scenario():
        rig = await _rig(tmp_path, monkeypatch, registry=())
        await rig.open_garage()
        payload = rig.pushed()
        assert "actions" not in payload["data"]
        assert payload["message"] == "Die Garage ist noch offen. Soll ich sie schließen?"
        # The question can still be answered through authenticated Assist.
        reply = await rig.runtime.async_handle_reply(
            "Ja", user_id="philipp", device_id=None, is_admin=True, other_open_questions=0,
        )
        assert reply is not None and [call[:2] for call in rig.device_calls()] == [("cover", "close_cover")]
        return rig

    _run(scenario)


def test_informational_push_needs_no_device_binding(tmp_path, monkeypatch):
    async def scenario():
        rig = await _rig(tmp_path, monkeypatch, registry=())
        rig.hass.states._states["person.philipp"] = State("person.philipp", "not_home")
        await rig.runtime.async_report_goal_failure(run_id="r", goal_label="Heizplan", owner_user_id="philipp")
        payload = rig.pushed()
        assert payload["message"] == "„Heizplan“ konnte nicht wie geplant erreicht werden."
        assert "actions" not in payload["data"]
        return rig

    _run(scenario)
