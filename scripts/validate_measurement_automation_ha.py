"""Run HomeIntent 7.2.0 attribute automations inside a real Home Assistant core.

The exact screenshot sentence is parsed by the HomeIntent engine, the preview
model's recipient is bound the way the conversation binds it, the generated
automation is loaded by Home Assistant's own ``automation`` integration, and
cover attribute changes are driven through the real state machine.  The only
stub is the service handler at the ``notify.send_message`` boundary, which
counts deliveries.  Also checks inclusive bounds and a travel direction.

    PYTHONPATH=custom_components python scripts/validate_measurement_automation_ha.py
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import replace
from typing import Any

from homeassistant import bootstrap, config_entries, loader
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component

from homeintent.engine import AutomationMatchResult, NluEngine
from homeintent.entities import EntitySnapshot
from homeintent.nlu.action_model import ActionModel, NotificationRecipient
from homeintent.nlu.automation_model import AutomationModel
from homeintent.nlu.ha_automation_generator import generate_ha_automation_config

IPHONE = "notify.mobile_app_iphone_von_philipp"
COVER = "cover.buero_rollladen"
SCREENSHOT = "Schicke mir eine Benachrichtigung wenn im Büro die Rolllade 50% erreicht hat"

ENTITIES = [
    EntitySnapshot(COVER, "Büro Rollladen", "cover", "open", area_id="buero", area_name="Büro",
                   device_class="shutter", attributes={"current_position": 20}),
    EntitySnapshot("cover.bad_rollladen", "Bad Rollladen", "cover", "open", area_id="bad",
                   area_name="Bad", device_class="shutter", attributes={"current_position": 0}),
    EntitySnapshot(IPHONE, "iPhone von Philipp", "notify", "unknown"),
]


def _bind_recipient(model: AutomationModel) -> AutomationModel:
    """What the conversation's authoritative resolver does for "mich"."""
    actions = []
    for action in model.actions:
        assert isinstance(action, ActionModel) and action.recipient is not None
        actions.append(replace(action, recipient=NotificationRecipient(
            action.recipient.kind, entity_ids=(IPHONE,), label="iPhone von Philipp",
        )))
    return replace(model, actions=tuple(actions))


def _config(sentence: str) -> dict[str, Any]:
    result = NluEngine().match_automation(sentence, ENTITIES)
    assert isinstance(result, AutomationMatchResult), result
    assert result.validation_error is None, result.validation_error
    generated = generate_ha_automation_config(_bind_recipient(result.model), ENTITIES)
    assert generated.error is None and generated.config is not None, generated.error
    return {"id": "homeintent-test", **generated.config}


async def _core(config_dir: str) -> HomeAssistant:
    """A bare core bootstrapped by Home Assistant's own base loader, so the
    registries are set up the way the running HA version expects."""
    hass = HomeAssistant(config_dir)
    loader.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await bootstrap.async_load_base_functionality(hass)
    await hass.async_start()
    return hass


async def _run(sentence: str, positions: list[int], start: int = 20) -> list[dict[str, Any]]:
    config = _config(sentence)
    calls: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as config_dir:
        hass = await _core(config_dir)

        async def send_message(call: ServiceCall) -> None:
            calls.append(dict(call.data))

        hass.services.async_register("notify", "send_message", send_message)
        hass.states.async_set(COVER, "open", {"current_position": start})
        hass.states.async_set(IPHONE, "unknown")
        assert await async_setup_component(hass, "automation", {"automation": [config]})
        await hass.async_block_till_done()
        for position in positions:
            hass.states.async_set(
                COVER, "open" if position else "closed", {"current_position": position}
            )
            await hass.async_block_till_done()
        await hass.async_stop(force=True)
    return calls


async def main() -> None:
    calls = await _run(SCREENSHOT, [35, 50])
    assert len(calls) == 1, calls
    assert calls[0]["entity_id"] == [IPHONE], calls
    assert "Büro" in calls[0]["message"] and "50 %" in calls[0]["message"], calls
    # Staying at 50 (another attribute update) and leaving/returning.
    calls = await _run(SCREENSHOT, [35, 50, 50, 60, 50])
    assert len(calls) == 2, calls
    # Inclusive bound: ">= 50" must fire at exactly 50, not only above.
    calls = await _run(
        "Benachrichtige mich, wenn der Rollladen im Büro mindestens 50 Prozent erreicht.", [40, 50, 70]
    )
    assert len(calls) == 1, calls
    # Direction: only arriving at 50 while moving down.
    down = "Benachrichtige mich, wenn der Rollladen im Büro auf 50 Prozent heruntergefahren ist."
    assert len(await _run(down, [30, 50], start=20)) == 0
    assert len(await _run(down, [80, 50], start=90)) == 1
    print("HomeIntent attribute automations validated in a real Home Assistant core.")


asyncio.run(main())
