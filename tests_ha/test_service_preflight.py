"""Use the real HA registry/schema; no hardware or network playback."""
import voluptuous as vol
from homeassistant.core import HomeAssistant

from custom_components.homeintent.entities import EntitySnapshot
from custom_components.homeintent.service_call import ServiceCallPlan
from custom_components.homeintent.service_executor import async_preflight_service_plan, async_execute_service_plan


async def test_registry_schema_preflight_is_read_only_and_rechecked(hass: HomeAssistant):
    calls = []

    async def record(call):
        calls.append(call)

    hass.states.async_set("media_player.test", "playing")
    entities = [EntitySnapshot("media_player.test", "Testplayer", "media_player", "playing")]
    plan = ServiceCallPlan("media_player", "volume_set", "media_player.test", {"volume_level": 0.3})
    args = dict(is_admin=True, user_id=None)
    missing = await async_preflight_service_plan(hass, plan, entities, {}, **args)
    assert missing.error and not calls
    hass.services.async_register("media_player", "volume_set", record, schema=vol.Schema({
        vol.Required("entity_id"): str,
        vol.Required("volume_level"): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
    }))
    valid = await async_preflight_service_plan(hass, plan, entities, {}, **args)
    assert valid.error is None and not calls
    invalid = await async_preflight_service_plan(hass,
        ServiceCallPlan("media_player", "volume_set", "media_player.test", {"volume_level": 3}),
        entities, {}, **args)
    assert invalid.error and not calls
    hass.services.async_remove("media_player", "volume_set")
    result = await async_execute_service_plan(hass, plan, entities, {}, confirmed=False, **args)
    assert not result.executed and not calls


async def test_real_state_change_is_checked_again_before_execution(hass: HomeAssistant):
    calls = []

    async def record(call):
        calls.append(call)

    hass.services.async_register("media_player", "media_pause", record)
    hass.states.async_set("media_player.test", "playing")
    entities = [EntitySnapshot("media_player.test", "Testplayer", "media_player", "playing")]
    plan = ServiceCallPlan("media_player", "media_pause", "media_player.test")
    args = dict(is_admin=True, user_id=None)
    assert (await async_preflight_service_plan(hass, plan, entities, {}, **args)).error is None
    hass.states.async_set("media_player.test", "unavailable")
    result = await async_execute_service_plan(hass, plan, entities, {}, confirmed=False, **args)
    assert not result.executed and not calls
