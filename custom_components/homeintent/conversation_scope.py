"""Private identity boundary for per-conversation in-memory state."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.components import conversation

from .security_control import conversation_user_id

__all__ = ("conversation_scope_key", "scope_conversation_input")


def conversation_scope_key(
    conversation_id: str | None,
    *,
    user_id: str | None = None,
    satellite_id: str | None = None,
    device_id: str | None = None,
    nonce: str | None = None,
) -> str:
    """Return an opaque key for one session and authenticated endpoint."""
    if user_id:
        principal_kind, principal_id = "user", user_id
    elif satellite_id:
        principal_kind, principal_id = "satellite", satellite_id
    elif device_id:
        principal_kind, principal_id = "device", device_id
    else:
        principal_kind, principal_id = "anonymous", ""
    session_id = conversation_id or f"unbound:{nonce or uuid.uuid4().hex}"
    material = "\0".join((principal_kind, principal_id, session_id)).encode()
    return "homeintent:" + hashlib.sha256(material).hexdigest()


def scope_conversation_input(
    user_input: conversation.ConversationInput,
) -> tuple[conversation.ConversationInput, str | None]:
    """Scope an input while retaining the id that Home Assistant must see."""
    public_id = user_input.conversation_id
    scoped_id = conversation_scope_key(
        public_id,
        user_id=conversation_user_id(user_input),
        satellite_id=getattr(user_input, "satellite_id", None),
        device_id=getattr(user_input, "device_id", None),
    )
    return replace(user_input, conversation_id=scoped_id), public_id
