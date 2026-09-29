"""Push notifications and their recipients (7.7, B4: from ``conversation.py``).

Own contract: a notification's meaning (recipient, content, trigger) is
independent of device control; it writes only ``notify`` services.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Protocol

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from ..agent_delivery import AgentDelivery
from ..entities import EntitySnapshot
from ..nlu.action_model import (
    ActionGroup,
    ActionModel,
    ActionType,
    NotificationRecipient,
)
from ..nlu.automation_model import AutomationModel, TriggerTarget
from ..nlu.context import ConversationContextStore
from ..notification_request import async_deliver_notification_request, NotificationRequest
from ..notification_target import (
    named_notification_targets,
    NotificationTargetResolver,
    resolution_failure_text,
)
from ..security_control import conversation_user_id


class NotificationRuntime(Protocol):
    """The one runtime service this controller uses."""

    user_contexts: Any | None


class NotificationController:
    """Resolves notification recipients and sends immediate pushes."""

    def __init__(
        self,
        *,
        hass: Callable[[], HomeAssistant],
        entry: ConfigEntry,
        context_store: ConversationContextStore,
        runtime: NotificationRuntime,
    ) -> None:
        self._hass = hass
        self.entry = entry
        self._context_store = context_store
        self._runtime = runtime

    @property
    def hass(self) -> HomeAssistant:
        return self._hass()

    def _notification_target_resolver(
        self, entities: list[EntitySnapshot]
    ) -> NotificationTargetResolver:
        labels = {item.entity_id: item.friendly_name for item in entities}
        return NotificationTargetResolver.from_options(
            self.entry.options,
            self._runtime.user_contexts,
            label_for=lambda target_id: labels.get(target_id, ""),
            named_targets=named_notification_targets(
                entities, self._runtime.user_contexts
            ),
        )

    def materialize_presence_speaker(
        self,
        model: AutomationModel,
        user_input: conversation.ConversationInput,
    ) -> tuple[AutomationModel, str | None]:
        """Bind "ich komme nach Hause" to the speaker's own person entity.

        Only an explicit, confirmed user/person binding is used - never a
        person guessed from a similar name.
        """
        if not any(trigger.presence_of_speaker for trigger in model.triggers):
            return model, None
        store = self._runtime.user_contexts
        binding = (
            store.resolve_current_person(conversation_user_id(user_input))
            if store is not None else None
        )
        if binding is None or binding.person_entity_id is None:
            return model, (
                "Ich weiß noch nicht, welche Person du bist. Bitte ordne deinem "
                "HomeIntent-Benutzer eine Person zu."
            )
        triggers = tuple(
            replace(trigger, target=TriggerTarget(domain="person", entity_id=binding.person_entity_id))
            if trigger.presence_of_speaker else trigger
            for trigger in model.triggers
        )
        return replace(model, triggers=triggers), None

    def materialize_recipients(
        self,
        model: AutomationModel,
        user_input: conversation.ConversationInput,
        entities: list[EntitySnapshot],
    ) -> tuple[AutomationModel, str | None]:
        """Turn "mich"/"uns" into the exact authorized notify targets.

        The automation runs later without a live conversation user, so the
        semantic recipient is resolved now - through the single
        ``NotificationTargetResolver`` - and the exact targets are persisted.
        """
        resolver: NotificationTargetResolver | None = None
        failure: str | None = None

        def materialize(step: ActionModel | ActionGroup) -> ActionModel | ActionGroup:
            nonlocal resolver, failure
            if isinstance(step, ActionGroup):
                return replace(step, steps=tuple(materialize(child) for child in step.steps))
            recipient = step.recipient
            if (
                step.type is not ActionType.NOTIFY
                or recipient is None
                or recipient.materialized
                or failure is not None
            ):
                return step
            if resolver is None:
                resolver = self._notification_target_resolver(entities)
            resolution = resolver.resolve(recipient.kind, conversation_user_id(user_input))
            if not resolution.resolved:
                failure = resolution_failure_text(resolution)
                return step
            return replace(step, recipient=NotificationRecipient(
                recipient.kind,
                entity_ids=resolution.entity_ids,
                service_ids=resolution.service_ids,
                label=resolution.label,
            ))

        actions = tuple(materialize(step) for step in model.actions)
        if failure is not None:
            return model, failure
        return replace(model, actions=actions), None

    async def async_handle_immediate(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: NotificationRequest,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Explicit push the user asked for right now.

        Independent of proactive situation detection and V12 opportunity
        policies; only the push channel and a safely resolved target matter.
        """
        self._context_store.clear(user_input.conversation_id)
        outcome = await async_deliver_notification_request(
            request,
            resolver=self._notification_target_resolver(entities),
            delivery=AgentDelivery(self.hass),
            user_id=conversation_user_id(user_input),
        )
        response.async_set_speech(outcome.spoken())
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
