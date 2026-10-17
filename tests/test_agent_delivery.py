from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeintent.agent_delivery import AgentDelivery  # noqa: E402
from homeintent.agent_event import AgentEvent, AgentEventState, AgentMode  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402


def _event(*, critical: bool) -> AgentEvent:
    now = datetime.now(timezone.utc)
    return AgentEvent(
        "a" * 32,
        "safety" if critical else "comfort",
        "key",
        "HomeIntent",
        "Meldung",
        AgentMode.INFORM,
        AgentEventState.ACTIVE,
        now.isoformat(),
        now.isoformat(),
        (now + timedelta(hours=1)).isoformat(),
        safety_critical=critical,
    )


def test_quiet_hours_suppress_comfort_tts_but_never_critical_tts():
    hass = HomeAssistant()
    delivery = AgentDelivery(hass)
    options = {
        "agent_delivery_channels": ["tts"],
        "agent_tts_entity": "tts.piper",
        "agent_media_players": ["media_player.hall"],
        "agent_quiet_start": "00:00",
        "agent_quiet_end": "00:00",
    }
    comfort = asyncio.run(delivery.async_deliver(_event(critical=False), options))
    critical = asyncio.run(delivery.async_deliver(_event(critical=True), options))
    assert comfort.delivered_channels == ()
    assert critical.delivered_channels == ("tts",)
    hass.services.async_call.assert_awaited_once()


def test_provider_exception_details_never_leave_delivery_boundary(caplog):
    secret = "/config/secrets.yaml token=super-secret api_key=123456"
    hass = HomeAssistant()
    hass.services.async_call.side_effect = RuntimeError(secret)
    result = asyncio.run(
        AgentDelivery(hass).async_deliver(
            _event(critical=False), {"agent_delivery_channels": ["push"]}
        )
    )

    assert result.errors == ("push:RuntimeError",)
    assert secret not in repr(result)
    assert secret not in caplog.text


def test_typed_push_uses_exact_bound_mobile_app_service_without_broadcast():
    hass = HomeAssistant()
    hass.services.async_register("notify", "mobile_app_iphone", lambda _call: None)
    delivery = AgentDelivery(hass)

    delivered = asyncio.run(
        delivery.async_deliver_typed_notification(
            "notify.mobile_app_iphone",
            title="Fenster noch offen",
            message="Küchenfenster ist noch offen.",
            dedupe_key="goal:event:person",
            severity="warning",
            goal_id="goal-1",
            run_id="run-1",
        )
    )

    assert delivered is True
    hass.services.async_call.assert_awaited_once()
    call = hass.services.async_call.await_args
    assert call.args[:2] == ("notify", "mobile_app_iphone")
    assert "entity_id" not in call.args[2]
