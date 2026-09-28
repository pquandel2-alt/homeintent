"""HA-facing multi-channel delivery for deterministic agent messages."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from typing import Mapping

from homeassistant.core import HomeAssistant

from .agent_event import AgentEvent
from .const import (
    AGENT_CHANNEL_PUSH,
    AGENT_CHANNEL_TTS,
    CONF_AGENT_DELIVERY_CHANNELS,
    CONF_AGENT_MEDIA_PLAYERS,
    CONF_AGENT_NOTIFY_TARGETS,
    CONF_AGENT_TTS_ENTITY,
    CONF_AGENT_QUIET_END,
    CONF_AGENT_QUIET_START,
)
from .user_context import NotificationTarget, NotificationTargetKind
from .execution_context import call_context

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    delivered_channels: tuple[str, ...]
    errors: tuple[str, ...]


class NotificationDeliveryStatus(StrEnum):
    DELIVERED = "delivered"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True)
class NotificationDeliveryResult:
    """Outcome of one explicit, already-addressed push delivery.

    ``reason`` is an internal diagnostic code, never a service name meant
    for the user.
    """

    status: NotificationDeliveryStatus
    delivered_target_ids: tuple[str, ...] = ()
    unavailable_target_ids: tuple[str, ...] = ()
    reason: str = ""

    @property
    def delivered(self) -> bool:
        return self.status is NotificationDeliveryStatus.DELIVERED


class AgentDelivery:
    """Routes already-reasoned messages; it never evaluates rules or actions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def async_deliver(
        self, event: AgentEvent, options: Mapping[str, object]
    ) -> DeliveryResult:
        raw_channels = options.get(CONF_AGENT_DELIVERY_CHANNELS, (AGENT_CHANNEL_PUSH,))
        channels = (
            {str(item) for item in raw_channels}
            if isinstance(raw_channels, (list, tuple, set))
            else {AGENT_CHANNEL_PUSH}
        )
        delivered: list[str] = []
        errors: list[str] = []
        if AGENT_CHANNEL_PUSH in channels:
            try:
                await self._async_push(event, options)
                delivered.append(AGENT_CHANNEL_PUSH)
            except Exception as err:  # noqa: BLE001 - HA services fail heterogeneously
                _LOGGER.warning("Agent push delivery failed: %s", err)
                errors.append(f"push: {err}")
        if AGENT_CHANNEL_TTS in channels and not _quiet_tts(event, options):
            try:
                await self._async_tts(event, options)
                delivered.append(AGENT_CHANNEL_TTS)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Agent TTS delivery failed: %s", err)
                errors.append(f"tts: {err}")
        return DeliveryResult(tuple(delivered), tuple(errors))

    async def _async_push(
        self, event: AgentEvent, options: Mapping[str, object]
    ) -> None:
        raw_targets = options.get(CONF_AGENT_NOTIFY_TARGETS, ())
        targets = (
            [str(item) for item in raw_targets if isinstance(item, str)]
            if isinstance(raw_targets, (list, tuple))
            else []
        )
        if targets:
            # Configured targets are notify *entities*.  Home Assistant's
            # ``notify.send_message`` entity service accepts only ``message``
            # and ``title`` (extra keys are rejected), so this rule push
            # carries no tag and no buttons; the event stays answerable in
            # the HomeIntent dashboard and by voice.
            await self._hass.services.async_call(
                "notify",
                "send_message",
                {"entity_id": targets, "title": event.title, "message": event.message},
                blocking=True,
                context=call_context(),
            )
            return
        await self._hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": event.title,
                "message": event.message,
                "notification_id": f"homeintent_{event.event_id}",
            },
            blocking=True,
            context=call_context(),
        )

    async def async_deliver_typed_notification(
        self,
        target_id: str,
        *,
        target_kind: NotificationTargetKind | None = None,
        title: str,
        message: str,
        dedupe_key: str,
        severity: str,
        goal_id: str,
        run_id: str,
        actions: tuple[tuple[str, str], ...] = (),
    ) -> bool:
        """Deliver an already-rendered V10 notification to one bound target.

        Recipient resolution and content construction happen before this
        boundary.  No broadcast fallback is permitted for typed goals.
        ``actions`` are (opaque action id, title) pairs; an action id never
        carries a domain, service or entity.

        Only a legacy ``notify.<service>`` binding accepts the ``data``
        payload (tag, severity, actionable buttons).  A notify *entity* is
        addressed through ``notify.send_message``, whose Home Assistant
        schema accepts nothing but ``message`` and ``title``; buttons for an
        entity target are therefore refused instead of silently dropped -
        callers check ``supports_actions()`` first.
        """
        if not target_id.startswith("notify."):
            raise ValueError("Typed push delivery requires one explicit notify.* target")
        service = target_id.partition(".")[2]
        effective = self.effective_target_kind(target_id, target_kind)
        if effective is None:
            raise ValueError("Legacy notify target is missing or ambiguous; bind its kind explicitly")
        if effective is NotificationTargetKind.ENTITY:
            if actions:
                raise ValueError("Actionable push buttons require a legacy notify service binding")
            await self._hass.services.async_call(
                "notify",
                "send_message",
                {"entity_id": [target_id], "title": title, "message": message},
                blocking=True,
                context=call_context(),
            )
            return True
        data: dict[str, object] = {
            "tag": dedupe_key,
            "severity": severity,
            "goal_id": goal_id,
            "run_id": run_id,
        }
        if actions:
            data["actions"] = [
                {"action": action_id, "title": label} for action_id, label in actions
            ]
        payload = {
            "title": title,
            "message": message,
            "data": data,
        }
        if not self._hass.services.has_service("notify", service):
            raise ValueError("The explicitly bound notify service is unavailable")
        await self._hass.services.async_call("notify", service, payload, blocking=True, context=call_context())
        return True

    def effective_target_kind(
        self, target_id: str, target_kind: NotificationTargetKind | None
    ) -> NotificationTargetKind | None:
        """The kind a target is actually addressed as.

        Explicit bindings are authoritative.  ``None`` is limited to
        schema-v1 records and is resolved only from the live entity/service
        registries (never from a name); missing or ambiguous yields ``None``.
        """
        if target_kind is not None:
            return target_kind
        service = target_id.partition(".")[2]
        has_service = self._hass.services.has_service("notify", service)
        has_entity = self._hass.states.get(target_id) is not None
        if has_service == has_entity:
            return None
        return NotificationTargetKind.SERVICE if has_service else NotificationTargetKind.ENTITY

    def supports_actions(
        self, target_id: str, target_kind: NotificationTargetKind | None
    ) -> bool:
        """Only legacy mobile_app services render actionable buttons."""
        return self.effective_target_kind(target_id, target_kind) is NotificationTargetKind.SERVICE

    def notification_target_available(self, target: NotificationTarget) -> bool:
        """Whether an exact target can currently receive a push."""
        if target.kind is NotificationTargetKind.SERVICE:
            service = target.target_id.partition(".")[2]
            return bool(service) and self._hass.services.has_service("notify", service)
        state = self._hass.states.get(target.target_id)
        return (
            state is not None
            and getattr(state, "state", None) != "unavailable"
            and self._hass.services.has_service("notify", "send_message")
        )

    async def async_send_notification(
        self,
        targets: tuple[NotificationTarget, ...],
        *,
        title: str,
        message: str,
    ) -> NotificationDeliveryResult:
        """Send one plain push to already-resolved, exact targets.

        This is the shared primitive for explicit user-requested pushes.
        Recipient resolution happens before this boundary; there is no
        broadcast and no ``persistent_notification`` fallback.  Entity
        targets use Home Assistant's ``notify.send_message`` entity service,
        whose schema accepts only ``message`` and ``title``; confirmed legacy
        service bindings use their own ``notify.<service>``.  ``title`` and
        ``message`` are inert text and never interpreted.
        """
        if not targets or any(not item.target_id.startswith("notify.") for item in targets):
            return NotificationDeliveryResult(
                NotificationDeliveryStatus.FAILED, reason="no_exact_notify_target"
            )
        available = tuple(item for item in targets if self.notification_target_available(item))
        unavailable = tuple(
            item.target_id for item in targets if item not in available
        )
        if not available:
            return NotificationDeliveryResult(
                NotificationDeliveryStatus.UNAVAILABLE,
                unavailable_target_ids=unavailable,
                reason="notify_target_unavailable",
            )
        entity_ids = [
            item.target_id for item in available
            if item.kind is not NotificationTargetKind.SERVICE
        ]
        delivered: list[str] = []
        try:
            if entity_ids:
                await self._hass.services.async_call(
                    "notify",
                    "send_message",
                    {"entity_id": entity_ids, "title": title, "message": message},
                    blocking=True,
                    context=call_context(),
                )
                delivered.extend(entity_ids)
            for item in available:
                if item.kind is NotificationTargetKind.SERVICE:
                    await self._hass.services.async_call(
                        "notify",
                        item.target_id.partition(".")[2],
                        {"title": title, "message": message},
                        blocking=True,
                        context=call_context(),
                    )
                    delivered.append(item.target_id)
        except Exception as err:  # noqa: BLE001 - HA services fail heterogeneously
            _LOGGER.warning("Push delivery failed: %s", err)
            return NotificationDeliveryResult(
                NotificationDeliveryStatus.FAILED,
                tuple(delivered),
                unavailable,
                f"service_call_failed:{type(err).__name__}",
            )
        return NotificationDeliveryResult(
            NotificationDeliveryStatus.DELIVERED, tuple(delivered), unavailable, "accepted"
        )

    async def async_satellite_message(
        self, satellite_entity_id: str, message: str, *, ask: bool
    ) -> None:
        """Speak on one explicitly resolved Assist satellite.

        ``ask`` uses ``assist_satellite.start_conversation`` so the satellite
        keeps listening for the answer (no extra wake word); otherwise
        ``assist_satellite.announce``.  Never a broadcast.
        """
        if not satellite_entity_id.startswith("assist_satellite."):
            raise ValueError("Voice delivery requires one explicit assist_satellite.* target")
        service = "start_conversation" if ask else "announce"
        key = "start_message" if ask else "message"
        if not self._hass.services.has_service("assist_satellite", service):
            raise ValueError(f"assist_satellite.{service} is not available")
        await self._hass.services.async_call(
            "assist_satellite",
            service,
            {"entity_id": satellite_entity_id, key: message},
            blocking=True,
            context=call_context(),
        )

    async def _async_tts(
        self, event: AgentEvent, options: Mapping[str, object]
    ) -> None:
        await self.async_speak(event.message, options)

    async def async_speak(
        self, message: str, options: Mapping[str, object]
    ) -> None:
        """Speak a pre-rendered local message through configured HA TTS targets."""
        tts_entity = options.get(CONF_AGENT_TTS_ENTITY)
        raw_players = options.get(CONF_AGENT_MEDIA_PLAYERS, ())
        players = (
            [str(item) for item in raw_players if isinstance(item, str)]
            if isinstance(raw_players, (list, tuple))
            else []
        )
        if not isinstance(tts_entity, str) or not tts_entity or not players:
            raise ValueError("TTS-Engine und mindestens ein TTS-Ziel müssen konfiguriert sein")
        await self._hass.services.async_call(
            "tts",
            "speak",
            {
                "entity_id": tts_entity,
                "media_player_entity_id": players,
                "message": message,
            },
            blocking=True,
            context=call_context(),
        )


def _quiet_tts(event: AgentEvent, options: Mapping[str, object]) -> bool:
    if event.safety_critical:
        return False
    start = _parse_time(options.get(CONF_AGENT_QUIET_START))
    end = _parse_time(options.get(CONF_AGENT_QUIET_END))
    if start is None or end is None:
        return False
    from homeassistant.util import dt as dt_util

    current = dt_util.now().timetz().replace(tzinfo=None)
    return start <= current < end if start < end else current >= start or current < end


def _parse_time(value: object) -> time | None:
    if not isinstance(value, str):
        return None
    try:
        hour, minute = value.split(":", 1)
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return None
