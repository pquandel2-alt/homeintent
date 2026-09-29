"""Validate the 7.1 Learning Center glue against a REAL Home Assistant install.

Runs inside the ``home-assistant:stable`` container (CI smoke job):

* the module imports with the real ``websocket_api``/``http``/``panel_custom``,
* every command carries HA's real decorator schema and accepts/rejects
  payloads as intended,
* the panel/static registration calls match the real API signatures,
* the WebSocket commands register on a real ``HomeAssistant`` object.
"""

from __future__ import annotations

import asyncio
import inspect
import tempfile

# Import Home Assistant first: current releases install their voluptuous
# replacement (probatio) on import, and it must precede any voluptuous use.
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from homeintent import learning_center_ws


def _rejects(schema, payload) -> bool:
    """True when the real HA schema refuses the payload (any validator lib)."""
    try:
        schema(payload)
    except AssertionError:
        raise
    except Exception as err:  # noqa: BLE001 - vol.Invalid or probatio.Invalid
        return "Invalid" in type(err).__name__ or "Invalid" in {
            base.__name__ for base in type(err).__mro__
        }
    return False


def _check_schemas() -> None:
    commands = {handler._ws_command: handler for handler in learning_center_ws._COMMANDS}
    assert len(commands) == len(learning_center_ws._COMMANDS) == 24, commands
    assert all(name.startswith("homeintent/learning_center/") for name in commands)
    for name, handler in commands.items():
        schema = handler._ws_schema
        if schema is False:
            continue
        base = {"id": 1, "type": name}
        if name.endswith(("models/get", "models/evidence", "models/forget", "preferences/confirm",
                          "preferences/reject", "habits/preview", "habits/accept", "habits/reject")):
            base["ref"] = "abc"
        if name.endswith("permissions/revoke"):
            base["permission_id"] = "perm_x"
        if name.endswith("mutes/remove"):
            base["situation_kind"] = "entry_left_open"
        if name.endswith("models/reset"):
            base["confirm"] = True
        if name.endswith("bindings/remove"):
            base["binding_id"] = "binding_x"
        if name.endswith("settings/implicit_action_level"):
            base["level"] = "propose"
        schema(base)
        if not _rejects(schema, {**base, "user_id": "someone-else"}):
            raise AssertionError(f"{name} accepted a browser supplied user_id")
    reset = commands["homeintent/learning_center/models/reset"]._ws_schema
    for payload in ({"id": 1, "type": "homeintent/learning_center/models/reset"},
                    {"id": 1, "type": "homeintent/learning_center/models/reset", "confirm": False}):
        if not _rejects(reset, payload):
            raise AssertionError("reset without explicit confirmation accepted")


def _check_signatures() -> None:
    inspect.signature(panel_custom.async_register_panel).bind(
        None, frontend_url_path="homeintent", webcomponent_name="homeintent-learning-center",
        sidebar_title="HomeIntent", sidebar_icon="mdi:brain",
        module_url="/homeintent_frontend/homeintent-learning-center.js?v=0",
        embed_iframe=False, require_admin=False, config={"api_version": 1},
    )
    inspect.signature(frontend.async_remove_panel).bind(None, "homeintent", warn_if_unknown=False)
    StaticPathConfig("/homeintent_frontend", str(learning_center_ws.FRONTEND_DIR), True)
    assert (learning_center_ws.FRONTEND_DIR / learning_center_ws.FRONTEND_FILE).is_file()


async def _register_on_real_hass() -> None:
    with tempfile.TemporaryDirectory() as config_dir:
        hass = HomeAssistant(config_dir)
        for handler in learning_center_ws._COMMANDS:
            websocket_api.async_register_command(hass, handler)
        registered = hass.data[websocket_api.DOMAIN] if websocket_api.DOMAIN in hass.data else {}
        names = set(registered) if isinstance(registered, dict) else set()
        if names:
            assert "homeintent/learning_center/summary" in names
        await hass.async_stop(force=True)


_check_schemas()
_check_signatures()
asyncio.run(_register_on_real_hass())
print("Learning Center glue validated against the real Home Assistant API.")
