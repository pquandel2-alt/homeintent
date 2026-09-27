"""Typed explicit notification requests and their staged outcome.

An explicit request ("Schick mir eine Testbenachrichtigung") is executed as

    NotificationRequest -> NotificationTargetResolver -> AgentDelivery
    -> notify.send_message

It deliberately bypasses V12 opportunity/attention policies: the user asked
for exactly this push.  It never touches a device service, and it never
reports success unless Home Assistant accepted the notify call.

The outcome keeps the stages apart so the spoken answer matches what really
happened: recipient missing, target unavailable, delivery failed, delivered.
The same outcome doubles as the test-push diagnostic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from .agent_delivery import AgentDelivery, NotificationDeliveryResult, NotificationDeliveryStatus
from .nlu.action_model import NotificationRecipientKind
from .notification_language import (
    DEFAULT_NOTIFICATION_TITLE,
    TEST_NOTIFICATION_MESSAGE,
    NotificationClause,
)
from .notification_target import (
    NotificationResolutionStatus,
    NotificationTargetResolution,
    NotificationTargetResolver,
    resolution_failure_text,
)

_LOGGER = logging.getLogger(__name__)

TEST_NOTIFICATION_SENT_TEXT = "Die Testbenachrichtigung wurde gesendet."
NOTIFICATION_SENT_TEXT = "Die Benachrichtigung wurde gesendet."
NOTIFICATION_TARGET_UNAVAILABLE_TEXT = "Dein Push-Ziel ist momentan nicht verfügbar."
NOTIFICATION_FAILED_TEXT = "Die Benachrichtigung konnte nicht gesendet werden."


class NotificationStage(StrEnum):
    RECIPIENT_UNRESOLVED = "recipient_unresolved"
    TARGET_UNAVAILABLE = "target_unavailable"
    DELIVERY_FAILED = "delivery_failed"
    DELIVERED = "delivered"


@dataclass(frozen=True)
class NotificationRequest:
    """One explicit, immediate push the authenticated user asked for."""

    recipient: NotificationRecipientKind
    message: str
    title: str = DEFAULT_NOTIFICATION_TITLE
    test: bool = False
    # Spoken person name for an EXPLICIT_TARGET recipient ("Schreib Anna").
    recipient_name: str | None = None

    @classmethod
    def from_clause(cls, clause: NotificationClause) -> NotificationRequest:
        return cls(
            clause.recipient_kind,
            clause.resolved_message(),
            test=clause.test,
            recipient_name=clause.recipient_name,
        )

    @classmethod
    def test_push(cls) -> NotificationRequest:
        return cls(NotificationRecipientKind.CURRENT_USER, TEST_NOTIFICATION_MESSAGE, test=True)


@dataclass(frozen=True)
class NotificationOutcome:
    """Staged result; also the internal test-push diagnostic."""

    stage: NotificationStage
    request: NotificationRequest
    resolution: NotificationTargetResolution
    delivery: NotificationDeliveryResult | None = None

    @property
    def push_channel_enabled(self) -> bool:
        return self.resolution.status is not NotificationResolutionStatus.PUSH_DISABLED

    @property
    def target_resolved(self) -> bool:
        return self.resolution.resolved

    @property
    def target_available(self) -> bool:
        return (
            self.delivery is not None
            and self.delivery.status is not NotificationDeliveryStatus.UNAVAILABLE
        )

    @property
    def service_accepted(self) -> bool:
        return self.delivery is not None and self.delivery.delivered

    @property
    def reason(self) -> str:
        if self.delivery is not None:
            return self.delivery.reason
        return self.resolution.reason

    def spoken(self) -> str:
        name = self.request.recipient_name
        if self.stage is NotificationStage.DELIVERED:
            if name and not self.request.test:
                return f"Die Nachricht an {name} wurde gesendet."
            return TEST_NOTIFICATION_SENT_TEXT if self.request.test else NOTIFICATION_SENT_TEXT
        if self.stage is NotificationStage.RECIPIENT_UNRESOLVED:
            if name and self.resolution.reason == "named_recipient_unbound":
                return (
                    f"Für {name} finde ich kein zugeordnetes Handy. "
                    "Ich habe nichts gesendet."
                )
            return resolution_failure_text(self.resolution)
        if self.stage is NotificationStage.TARGET_UNAVAILABLE:
            return NOTIFICATION_TARGET_UNAVAILABLE_TEXT
        return NOTIFICATION_FAILED_TEXT

    def diagnostic(self) -> dict[str, object]:
        """Privacy-safe summary (no ids, no message text)."""
        return {
            "stage": self.stage.value,
            "push_channel_enabled": self.push_channel_enabled,
            "target_resolved": self.target_resolved,
            "resolution": self.resolution.status.value,
            "target_available": self.target_available,
            "service_accepted": self.service_accepted,
            "reason": self.reason,
        }


async def async_deliver_notification_request(
    request: NotificationRequest,
    *,
    resolver: NotificationTargetResolver,
    delivery: AgentDelivery,
    user_id: str | None,
) -> NotificationOutcome:
    resolution = resolver.resolve(request.recipient, user_id, name=request.recipient_name)
    if not resolution.resolved:
        _LOGGER.info("Push request not delivered: %s", resolution.reason)
        return NotificationOutcome(NotificationStage.RECIPIENT_UNRESOLVED, request, resolution)
    result = await delivery.async_send_notification(
        resolution.targets, title=request.title, message=request.message
    )
    if result.status is NotificationDeliveryStatus.DELIVERED:
        stage = NotificationStage.DELIVERED
    elif result.status is NotificationDeliveryStatus.UNAVAILABLE:
        stage = NotificationStage.TARGET_UNAVAILABLE
    else:
        stage = NotificationStage.DELIVERY_FAILED
    if stage is not NotificationStage.DELIVERED:
        _LOGGER.warning("Push request not delivered: %s", result.reason)
    return NotificationOutcome(stage, request, resolution, result)


__all__ = (
    "NOTIFICATION_FAILED_TEXT",
    "NOTIFICATION_SENT_TEXT",
    "NOTIFICATION_TARGET_UNAVAILABLE_TEXT",
    "NotificationOutcome",
    "NotificationRequest",
    "NotificationStage",
    "TEST_NOTIFICATION_SENT_TEXT",
    "async_deliver_notification_request",
)
