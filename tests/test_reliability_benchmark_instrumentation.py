"""The shadow probe must observe the shared preflight without changing it."""
from pathlib import Path
import importlib
import sys

import pytest
import _ha_stub

_ha_stub.install()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from arbiter_shadow import Cascade


@pytest.fixture
def cascade(monkeypatch):
    # Cascade installs instrumentation; restore every observed boundary after use.
    for name in ("controllers.devices", "controllers.goals", "controllers.routines"):
        module = importlib.import_module("homeintent." + name)
        monkeypatch.setattr(module, "evaluate_service_plan", module.evaluate_service_plan)
    module = importlib.import_module("homeintent.controllers.devices")
    monkeypatch.setattr(module, "async_preflight_service_plan", module.async_preflight_service_plan)
    return Cascade()


@pytest.mark.parametrize("text,expected_count", [
    ("Schalte das Küchenlicht aus.", 1),
    ("Schalte das Küchenlicht aus und schalte das Flurlicht ein.", 2),
])
def test_shadow_probe_records_each_logical_plan_once(cascade, text, expected_count):
    turn, payload = cascade.run(text)
    assert len(turn.calls) == expected_count
    assert payload is not None
    assert len(payload.commands) == expected_count
    observed = {target for command in payload.commands
                for target in ((command.plan.entity_id,) if isinstance(command.plan.entity_id, str)
                               else command.plan.entity_id)}
    assert observed == turn.targets


def test_notification_test_adapter_retains_device_effect_reports(monkeypatch, tmp_path):
    from _testhaus import HouseConversation
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, simulate_device_effects=True)
    turn = house.say("Mach die Nachttischlampen aus und das Nachtlicht an")
    assert turn.targets == {"light.nachttischlampe_links", "light.nachttischlampe_rechts", "light.nachtlicht"}
    assert house.entity.hass.states.get("light.nachttischlampe_links").state == "off"
    assert house.entity.hass.states.get("light.nachtlicht").state == "on"


def test_notification_adapter_without_reports_stops_later_clause(monkeypatch, tmp_path):
    from _testhaus import HouseConversation
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, simulate_device_effects=False)
    turn = house.say("Mach die Nachttischlampen aus und das Nachtlicht an")
    assert turn.targets == {"light.nachttischlampe_links", "light.nachttischlampe_rechts"}
    assert "nicht mehr ausgeführt" in turn.speech
