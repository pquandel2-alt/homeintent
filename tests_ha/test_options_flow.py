"""Real Home Assistant regression tests for the HomeIntent options flow.

Settings -> Devices & Services -> HomeIntent -> Configure POSTs to
``/api/config/config_entries/options/flow``. HomeIntent 7.1.0 answered that
with ``400: Bad Request`` on Home Assistant 2026.9 because the entity
selectors were built with ``domain=<tuple>``: HA validates selector configs
with ``cv.ensure_list``, which wraps a tuple instead of treating it as a
list of domains. These tests drive the real HTTP endpoints with the real
selectors, so the exact failure path is covered.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import EntitySelector
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homeintent.const import (
    CONF_ALLOW_NON_ADMIN_AUTOMATIONS,
    CONF_ADMIN_ONLY_ENTITIES,
    CONF_AGENT_AUTO_ENTITY_IDS,
    CONF_AGENT_MEDIA_PLAYERS,
    CONF_AGENT_NOTIFY_TARGETS,
    CONF_AGENT_TTS_ENTITY,
    CONF_ATTENTION_BUDGET_ENABLED,
    CONF_CONFIRMATION_LEVEL,
    CONF_CONTROL_USER_IDS,
    CONF_CRITICAL_MULTI_CHANNEL_ENABLED,
    CONF_EXPERIENCE_LEARNING_ENABLED,
    CONF_HABIT_DISCOVERY_ENABLED,
    CONF_LEARNING_RETENTION_COUNT,
    CONF_MEMORY_RETENTION_DAYS,
    CONF_MINIMUM_PREDICTION_CONFIDENCE,
    CONF_PREDICTIVE_MODELS_ENABLED,
    CONF_PROACTIVE_APPLIANCE_ENTITIES,
    CONF_PROACTIVE_CONTEXT_ENABLED,
    CONF_PROACTIVE_ENTRY_OPEN_MINUTES,
    CONF_PROACTIVE_PERSON_ROOM_SENSORS,
    CONF_PROACTIVE_SATELLITE_AREAS,
    CONF_PROACTIVE_SUGGESTIONS_ENABLED,
    CONF_PROACTIVE_USER_QUIET_HOURS,
    CONF_PUSH_PROACTIVE_ENABLED,
    CONF_QUIET_HOURS_ENABLED,
    CONF_READ_ONLY_ENTITIES,
    CONF_ROOM_AWARE_VOICE_ENABLED,
    CONF_SELECTED_ENTITIES,
    CONF_STANDING_PERMISSIONS_ENABLED,
    CONF_VOICE_PROACTIVE_ENABLED,
    DOMAIN,
    SELECTABLE_DOMAINS,
)

OPTIONS_FLOW_URL = "/api/config/config_entries/options/flow"

LEARNING_CONTROLS = (
    CONF_EXPERIENCE_LEARNING_ENABLED,
    CONF_PREDICTIVE_MODELS_ENABLED,
    CONF_HABIT_DISCOVERY_ENABLED,
    CONF_PROACTIVE_SUGGESTIONS_ENABLED,
    CONF_LEARNING_RETENTION_COUNT,
    CONF_MINIMUM_PREDICTION_CONFIDENCE,
)

V12_CONTROLS = (
    CONF_PROACTIVE_CONTEXT_ENABLED,
    CONF_VOICE_PROACTIVE_ENABLED,
    CONF_PUSH_PROACTIVE_ENABLED,
    CONF_ROOM_AWARE_VOICE_ENABLED,
    CONF_ATTENTION_BUDGET_ENABLED,
    CONF_QUIET_HOURS_ENABLED,
    CONF_STANDING_PERMISSIONS_ENABLED,
    CONF_CRITICAL_MULTI_CHANNEL_ENABLED,
    CONF_PROACTIVE_ENTRY_OPEN_MINUTES,
    CONF_PROACTIVE_APPLIANCE_ENTITIES,
    CONF_PROACTIVE_PERSON_ROOM_SENSORS,
    CONF_PROACTIVE_SATELLITE_AREAS,
    CONF_PROACTIVE_USER_QUIET_HOURS,
)

# The defaults HomeIntentConfigFlow.async_step_user writes on setup.
SETUP_OPTIONS: dict[str, Any] = {
    "memory_enabled": False,
    CONF_EXPERIENCE_LEARNING_ENABLED: False,
    CONF_PREDICTIVE_MODELS_ENABLED: False,
    CONF_HABIT_DISCOVERY_ENABLED: False,
    CONF_PROACTIVE_SUGGESTIONS_ENABLED: False,
    "routine_detection_enabled": False,
    "agent_auto_enabled": False,
    CONF_AGENT_AUTO_ENTITY_IDS: [],
    "documents_enabled": False,
    CONF_ALLOW_NON_ADMIN_AUTOMATIONS: False,
}


async def _entry(hass: HomeAssistant, options: dict[str, Any] | None = None) -> MockConfigEntry:
    # Same base a real Home Assistant boot provides before any UI request.
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "config", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="HomeIntent",
        data={},
        options=dict(SETUP_OPTIONS if options is None else options),
    )
    entry.add_to_hass(hass)
    return entry


async def _open_configure(hass: HomeAssistant, client: Any, entry: MockConfigEntry) -> dict[str, Any]:
    """Exactly what the frontend's Configure button does."""
    resp = await client.post(OPTIONS_FLOW_URL, json={"handler": entry.entry_id})
    body = await resp.text()
    assert resp.status == HTTPStatus.OK, f"Configure returned {resp.status}: {body}"
    data = await resp.json()
    assert data["type"] == "form", data
    assert data["step_id"] == "init"
    assert data["errors"] is None
    return data


def _fields(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {field["name"]: field for field in data["data_schema"]}


def _default(field: dict[str, Any]) -> Any:
    return field.get("default", field.get("description", {}).get("suggested_value"))


def _form_data(data: dict[str, Any]) -> dict[str, Any]:
    """What the frontend form holds before the user touches anything."""
    values = {name: _default(field) for name, field in _fields(data).items()}
    return {name: value for name, value in values.items() if value is not None}


async def _submit(client: Any, data: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """Press Save: the frontend posts the whole form, including the changes."""
    user_input = {**_form_data(data), **changes}
    resp = await client.post(f"{OPTIONS_FLOW_URL}/{data['flow_id']}", json=user_input)
    body = await resp.text()
    assert resp.status == HTTPStatus.OK, f"Save returned {resp.status}: {body}"
    return await resp.json()


async def test_homeintent_options_flow_opens_on_ha_2026_9(hass: HomeAssistant, hass_client) -> None:
    entry = await _entry(hass)
    client = await hass_client()
    data = await _open_configure(hass, client, entry)
    fields = _fields(data)
    for key in (
        CONF_SELECTED_ENTITIES, CONF_READ_ONLY_ENTITIES, CONF_ADMIN_ONLY_ENTITIES,
        CONF_CONTROL_USER_IDS, CONF_CONFIRMATION_LEVEL, CONF_AGENT_NOTIFY_TARGETS,
        CONF_AGENT_TTS_ENTITY, CONF_AGENT_MEDIA_PLAYERS, CONF_AGENT_AUTO_ENTITY_IDS,
        *LEARNING_CONTROLS, *V12_CONTROLS,
    ):
        assert key in fields, key


async def test_options_flow_entity_selectors_use_valid_domain_lists(
    hass: HomeAssistant, hass_client
) -> None:
    entry = await _entry(hass)
    # Real selector objects, as built by the flow.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"]
    selectors = {
        str(key): value for key, value in schema.schema.items()
        if isinstance(value, EntitySelector)
    }
    assert selectors
    for name, selector in selectors.items():
        domain = selector.config.get("domain")
        assert isinstance(domain, list), (name, domain)
        assert all(isinstance(item, str) for item in domain), (name, domain)
    for key in (CONF_SELECTED_ENTITIES, CONF_READ_ONLY_ENTITIES,
                CONF_ADMIN_ONLY_ENTITIES, CONF_AGENT_AUTO_ENTITY_IDS):
        assert selectors[key].config["domain"] == list(SELECTABLE_DOMAINS)
        assert selectors[key].config["multiple"] is True
    assert selectors[CONF_AGENT_NOTIFY_TARGETS].config["domain"] == ["notify"]
    assert selectors[CONF_AGENT_TTS_ENTITY].config["domain"] == ["tts"]
    assert selectors[CONF_AGENT_MEDIA_PLAYERS].config["domain"] == ["media_player"]
    hass.config_entries.options.async_abort(result["flow_id"])

    # And their serialized form, as sent to the frontend.
    fields = _fields(await _open_configure(hass, await hass_client(), entry))
    serialized = fields[CONF_SELECTED_ENTITIES]["selector"]["entity"]
    assert serialized["domain"] == list(SELECTABLE_DOMAINS)
    assert serialized["multiple"] is True


async def test_options_flow_contains_learning_controls(hass: HomeAssistant, hass_client) -> None:
    entry = await _entry(hass)
    fields = _fields(await _open_configure(hass, await hass_client(), entry))
    for key in LEARNING_CONTROLS:
        assert key in fields, key
    for key in LEARNING_CONTROLS[:4]:
        assert fields[key]["type"] == "boolean"
        assert _default(fields[key]) is False


async def test_options_flow_contains_v12_controls(hass: HomeAssistant, hass_client) -> None:
    entry = await _entry(hass)
    fields = _fields(await _open_configure(hass, await hass_client(), entry))
    for key in V12_CONTROLS:
        assert key in fields, key
    assert fields[CONF_PROACTIVE_CONTEXT_ENABLED]["type"] == "boolean"
    assert _default(fields[CONF_PROACTIVE_CONTEXT_ENABLED]) is False
    assert fields[CONF_PROACTIVE_APPLIANCE_ENTITIES]["selector"]["entity"]["domain"] == [
        "sensor", "select", "binary_sensor",
    ]


async def test_options_flow_preserves_existing_options(hass: HomeAssistant, hass_client) -> None:
    hass.states.async_set("light.kueche", "on")
    existing = {
        **SETUP_OPTIONS,
        CONF_SELECTED_ENTITIES: ["light.kueche"],
        CONF_EXPERIENCE_LEARNING_ENABLED: True,
        CONF_MEMORY_RETENTION_DAYS: 30,
        CONF_PROACTIVE_CONTEXT_ENABLED: True,
        CONF_PROACTIVE_PERSON_ROOM_SENSORS: "person.anna = sensor.anna_room",
    }
    entry = await _entry(hass, existing)
    client = await hass_client()
    data = await _open_configure(hass, client, entry)
    fields = _fields(data)
    assert _default(fields[CONF_SELECTED_ENTITIES]) == ["light.kueche"]
    assert _default(fields[CONF_EXPERIENCE_LEARNING_ENABLED]) is True
    assert _default(fields[CONF_MEMORY_RETENTION_DAYS]) == 30
    assert _default(fields[CONF_PROACTIVE_CONTEXT_ENABLED]) is True
    assert _default(fields[CONF_PROACTIVE_PERSON_ROOM_SENSORS]) == "person.anna = sensor.anna_room"

    # Saving without touching anything keeps every existing value.
    result = await _submit(client, data, {})
    assert result["type"] == "create_entry", result
    for key, value in existing.items():
        assert entry.options[key] == value, key


async def test_options_flow_can_save_learning_enabled(hass: HomeAssistant, hass_client) -> None:
    entry = await _entry(hass)
    client = await hass_client()
    data = await _open_configure(hass, client, entry)
    result = await _submit(client, data, {
        CONF_EXPERIENCE_LEARNING_ENABLED: True,
        CONF_PREDICTIVE_MODELS_ENABLED: True,
        CONF_HABIT_DISCOVERY_ENABLED: True,
        CONF_PROACTIVE_SUGGESTIONS_ENABLED: True,
    })
    assert result["type"] == "create_entry", result
    assert entry.options[CONF_EXPERIENCE_LEARNING_ENABLED] is True
    assert entry.options[CONF_PREDICTIVE_MODELS_ENABLED] is True
    assert entry.options[CONF_HABIT_DISCOVERY_ENABLED] is True
    assert entry.options[CONF_PROACTIVE_SUGGESTIONS_ENABLED] is True


async def test_options_flow_can_save_proactive_enabled(hass: HomeAssistant, hass_client) -> None:
    entry = await _entry(hass)
    client = await hass_client()
    data = await _open_configure(hass, client, entry)
    result = await _submit(client, data, {CONF_PROACTIVE_CONTEXT_ENABLED: True})
    assert result["type"] == "create_entry", result
    assert entry.options[CONF_PROACTIVE_CONTEXT_ENABLED] is True
    # Unrelated learning switches stay at their saved (off) values.
    assert entry.options[CONF_EXPERIENCE_LEARNING_ENABLED] is False


async def test_saving_options_keeps_the_entity_selection_dynamic(
    hass: HomeAssistant, hass_client
) -> None:
    """F6: saving any other option must not freeze today's Assist exposure."""
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity

    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("light.kueche", "on")
    async_expose_entity(hass, "conversation", "light.kueche", True)
    entry = await _entry(hass)
    client = await hass_client()
    data = await _open_configure(hass, client, entry)
    assert _default(_fields(data)[CONF_SELECTED_ENTITIES]) in (None, [])

    result = await _submit(client, data, {CONF_PROACTIVE_CONTEXT_ENABLED: True})

    assert result["type"] == "create_entry", result
    assert not entry.options.get(CONF_SELECTED_ENTITIES)
    # A later exposure is visible without touching the options again.
    from custom_components.homeintent.hass_entities import get_selected_entity_ids

    hass.states.async_set("person.anna", "home")
    async_expose_entity(hass, "conversation", "person.anna", True)
    assert "person.anna" in get_selected_entity_ids(hass, entry)


async def test_fixed_selection_hiding_exposed_entities_raises_a_repair_issue(
    hass: HomeAssistant,
) -> None:
    """F6: installations frozen by 7.1.2 are told how to become dynamic again."""
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
    from homeassistant.helpers import issue_registry as ir

    assert await async_setup_component(hass, "homeassistant", {})
    for entity_id in ("light.kueche", "person.anna"):
        hass.states.async_set(entity_id, "on")
        async_expose_entity(hass, "conversation", entity_id, True)
    entry = await _entry(hass, {**SETUP_OPTIONS, CONF_SELECTED_ENTITIES: ["light.kueche"]})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    issue = ir.async_get(hass).async_get_issue(DOMAIN, "fixed_entity_selection")
    assert issue is not None
    assert issue.translation_placeholders == {"count": "1", "examples": "person.anna"}
    # The deliberate selection itself is never rewritten.
    assert entry.options[CONF_SELECTED_ENTITIES] == ["light.kueche"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_new_entry_reserves_automation_creation_for_admins(hass: HomeAssistant) -> None:
    """F26: a fresh installation stores the safer default explicitly."""
    assert await async_setup_component(hass, "homeassistant", {})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "create_entry"
    assert result["options"][CONF_ALLOW_NON_ADMIN_AUTOMATIONS] is False
    assert result["options"] == SETUP_OPTIONS
    await hass.async_block_till_done()
