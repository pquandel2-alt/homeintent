"""Validate HomeIntent's critical one-shot YAML against real HA schemas."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone

from homeassistant.components.automation.config import PLATFORM_SCHEMA
from homeassistant.components.calendar import CREATE_EVENT_SCHEMA
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

# Imported after ``homeassistant``: newer HA installs its own validator as
# ``voluptuous`` at import time, and schema markers must come from the same
# implementation ``cv`` uses.
import voluptuous as vol  # noqa: E402

from homeintent.calendar_event import CalendarEventDraft, build_calendar_event_service_call
from homeintent.entities import EntitySnapshot
from homeintent.nlu.action_model import (
    ActionModel,
    ActionType,
    NotificationRecipient,
    NotificationRecipientKind,
)
from homeintent.nlu.automation_model import AutomationModel, TriggerModel, TriggerTarget, TriggerType
from homeintent.nlu.ha_automation_generator import generate_ha_automation_config


entity = EntitySnapshot(
    "cover.buero_rollladen",
    "Rollladen Büro",
    "cover",
    "closed",
    capabilities=frozenset({"POSITION"}),
)
model = AutomationModel(
    triggers=(
        TriggerModel(
            type=TriggerType.TIME,
            time_hour=12,
            time_minute=5,
            time_second=30,
        ),
    ),
    actions=(
        ActionModel(
            type=ActionType.SET_POSITION,
            target=TriggerTarget(entity_id=entity.entity_id),
            value=50,
        ),
    ),
    source_text="Fahre in 30 Sekunden die Rolllade im Büro auf 50 Prozent",
    once=True,
    scheduled_for=datetime(2026, 8, 21, 12, 5, 30, tzinfo=timezone.utc),
)
result = generate_ha_automation_config(
    model, [entity], automation_id="homeintent-schema-smoke"
)
if result.error is not None or result.config is None:
    raise RuntimeError(f"HomeIntent generation failed: {result.error}")

calendar = EntitySnapshot(
    "calendar.privat",
    "Privat",
    "calendar",
    "off",
    capabilities=frozenset({"CREATE_EVENT"}),
)
calendar_call = build_calendar_event_service_call(
    CalendarEventDraft(
        title="Schema-Prüfung",
        event_date=date(2026, 8, 25),
        start_time=time(10, 0),
        duration_minutes=60,
        calendar_entity_id=calendar.entity_id,
    ),
    (calendar,),
    datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc),
)


# 7.1.2: "Kannst du mir in 10 Sekunden eine Test Benachrichtigung schicken?"
# after "mich" was materialized into the configured iPhone notify entity.
phone = EntitySnapshot(
    "notify.mobile_app_iphone_von_philipp", "iPhone von Philipp", "notify", "unknown"
)
push_model = AutomationModel(
    triggers=(TriggerModel(type=TriggerType.TIME, time_hour=12, time_minute=0, time_second=10),),
    actions=(
        ActionModel(
            type=ActionType.NOTIFY,
            message="Testbenachrichtigung von HomeIntent.",
            recipient=NotificationRecipient(
                NotificationRecipientKind.CURRENT_USER,
                entity_ids=(phone.entity_id,),
                label=phone.friendly_name,
            ),
        ),
    ),
    source_text="Kannst du mir in 10 Sekunden eine Test Benachrichtigung schicken?",
    once=True,
    scheduled_for=datetime(2026, 9, 26, 12, 0, 10, tzinfo=timezone.utc),
)
push_result = generate_ha_automation_config(
    push_model, [phone], automation_id="homeintent-push-schema-smoke"
)
if push_result.error is not None or push_result.config is None:
    raise RuntimeError(f"HomeIntent push generation failed: {push_result.error}")
push_action = push_result.config["actions"][0]
if push_action["action"] != "notify.send_message" or any(
    item.get("action") == "persistent_notification.create"
    for item in push_result.config["actions"]
):
    raise RuntimeError(f"Push automation is not a real notify.send_message: {push_action}")
# The exact field schema notify registers for ``send_message`` in HA
# 2026.9 (``notify/__init__.py``), built with HA's own entity-service helper
# so extra keys are rejected exactly as in production.
SEND_MESSAGE_SCHEMA = cv.make_entity_service_schema(
    {vol.Required("message"): cv.string, vol.Optional("title"): cv.string}
)
# The immediate spoken push (AgentDelivery.async_send_notification) payload.
DIRECT_PUSH_PAYLOAD = {
    "entity_id": [phone.entity_id],
    "title": "HomeIntent",
    "message": "Testbenachrichtigung von HomeIntent.",
}

# The legacy per-device notify services (``notify.mobile_app_<device>``) use
# notify's own NOTIFY_SERVICE_SCHEMA; it is the only path that carries
# ``data`` (tag, actionable buttons).
from homeassistant.components.notify.const import NOTIFY_SERVICE_SCHEMA  # noqa: E402

from homeintent.agent_delivery import AgentDelivery  # noqa: E402
from homeintent.agent_event import AgentEvent, AgentEventState, AgentMode  # noqa: E402
from homeintent.user_context import NotificationTarget, NotificationTargetKind  # noqa: E402


class _RecordingServices:
    """Captures the exact payloads HomeIntent hands to Home Assistant."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in {("notify", "send_message"), ("notify", "mobile_app_iphone")}

    async def async_call(self, domain, service, data, blocking=False, context=None) -> None:
        self.calls.append((domain, service, dict(data)))


class _RecordingStates:
    def get(self, entity_id: str):
        return object() if entity_id == phone.entity_id else None


class _RecordingHass:
    def __init__(self) -> None:
        self.services = _RecordingServices()
        self.states = _RecordingStates()


async def _agent_push_payloads() -> list[tuple[str, str, dict]]:
    """Real V9/V10/V12 delivery code paths, recorded instead of sent."""
    hass = _RecordingHass()
    delivery = AgentDelivery(hass)  # type: ignore[arg-type]
    stamp = datetime(2026, 9, 26, tzinfo=timezone.utc).isoformat()
    event = AgentEvent(
        "event-id", "rule", "key", "HomeIntent", "Die Garage ist offen.", AgentMode.ASK,
        AgentEventState.ACTIVE, stamp, stamp, stamp, proposed_action=None,
    )
    # V9 rule push to the configured notify entity.
    await delivery.async_deliver(event, {
        "agent_delivery_channels": ["push"], "agent_notify_targets": [phone.entity_id],
    })
    # V10 monitor goal / V12 plain push to a bound notify entity.
    await delivery.async_deliver_typed_notification(
        phone.entity_id, target_kind=NotificationTargetKind.ENTITY, title="HomeIntent",
        message="Die Garage ist offen.", dedupe_key="homeintent_v12_info",
        severity="info", goal_id="proactive", run_id="",
    )
    # V12 interactive proposal to a bound legacy mobile_app service.
    await delivery.async_deliver_typed_notification(
        "notify.mobile_app_iphone", target_kind=NotificationTargetKind.SERVICE,
        title="HomeIntent", message="Soll ich die Garage schließen?",
        dedupe_key="homeintent_v12_p1", severity="info", goal_id="proactive",
        run_id="p1", actions=(
            ("HOMEINTENT_V12_ACCEPT_p1_t0123456789abcdef", "Schließen"),
            ("HOMEINTENT_V12_LATER_p1_t0123456789abcdef", "Später"),
            ("HOMEINTENT_V12_IGNORE_p1_t0123456789abcdef", "Ignorieren"),
        ),
    )
    # The shared 7.1.2 explicit push primitive.
    await delivery.async_send_notification(
        (NotificationTarget(phone.entity_id, NotificationTargetKind.ENTITY),),
        title="HomeIntent", message="Testbenachrichtigung von HomeIntent.",
    )
    return hass.services.calls


async def _validate() -> None:
    """Run schema validation in the event-loop context required by HA templates."""
    # HA's template validator resolves the active instance from loop-local state.
    HomeAssistant("/tmp/homeintent-schema-smoke")
    PLATFORM_SCHEMA({"id": "homeintent-schema-smoke", **result.config})
    # The live service call passes the entity through Home Assistant's
    # separate ``target`` argument. CREATE_EVENT_SCHEMA is the lower-level
    # entity-service schema and therefore validates the already merged shape.
    CREATE_EVENT_SCHEMA({"entity_id": calendar_call.entity_id, **calendar_call.data})
    PLATFORM_SCHEMA({"id": "homeintent-push-schema-smoke", **push_result.config})
    SEND_MESSAGE_SCHEMA({**push_action["target"], **push_action["data"]})
    SEND_MESSAGE_SCHEMA(DIRECT_PUSH_PAYLOAD)
    agent_calls = await _agent_push_payloads()
    if len(agent_calls) != 4:
        raise RuntimeError(f"Unexpected agent push calls: {agent_calls}")
    for domain, service, payload in agent_calls:
        if domain != "notify":
            raise RuntimeError(f"Agent push left the notify domain: {domain}.{service}")
        if service == "send_message":
            SEND_MESSAGE_SCHEMA(payload)
        else:
            NOTIFY_SERVICE_SCHEMA(payload)
    if "actions" not in agent_calls[2][2]["data"]:
        raise RuntimeError("Interactive V12 push lost its buttons on the legacy service path")


asyncio.run(_validate())
print("HOME_ASSISTANT_AUTOMATION_CALENDAR_AND_PUSH_SCHEMA_OK")
