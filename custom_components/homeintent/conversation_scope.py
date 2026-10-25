"""Private identity boundary for per-conversation in-memory state."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.components import conversation

from .security_control import conversation_user_id

__all__ = (
    "conversation_scope_key",
    "conversation_scope_key_for_input",
    "reusable_conversation_scope_key",
    "scope_conversation_input",
)


def conversation_scope_key(
    conversation_id: str | None,
    *,
    user_id: str | None = None,
    satellite_id: str | None = None,
    device_id: str | None = None,
    nonce: str | None = None,
) -> str:
    """Return an opaque key for one session and authenticated endpoint."""
    session_id = conversation_id or f"unbound:{nonce or uuid.uuid4().hex}"
    # Every available boundary participates.  Selecting only the first
    # principal let the same authenticated user and public conversation id
    # share pending state across two voice satellites.
    material = "\0".join(
        (
            "session", session_id,
            "user", user_id or "",
            "satellite", satellite_id or "",
            "device", device_id or "",
            "anonymous", "0" if user_id else "1",
        )
    ).encode()
    return "homeintent:" + hashlib.sha256(material).hexdigest()


def conversation_scope_key_for_input(
    user_input: conversation.ConversationInput,
    *,
    nonce: str | None = None,
) -> str:
    """Return the production scope key for a Home Assistant input.

    This is the single public boundary for code which must inspect the same
    conversation scope as the entity (notably deterministic test harnesses).
    Callers must not reconstruct the principal or hash material themselves.

    An input without a public conversation id is intentionally unbound.  Its
    key contains a fresh nonce unless the caller that *creates* the turn
    supplies one; computing the key again later therefore cannot recover or
    reuse that anonymous scope.
    """
    return conversation_scope_key(
        user_input.conversation_id,
        user_id=conversation_user_id(user_input),
        satellite_id=getattr(user_input, "satellite_id", None),
        device_id=getattr(user_input, "device_id", None),
        nonce=nonce,
    )


def reusable_conversation_scope_key(
    user_input: conversation.ConversationInput,
) -> str | None:
    """Return a reusable scope key, or ``None`` for an unbound turn.

    A public conversation id is the session anchor.  Identity information
    selects its principal, but must never make a turn without a session id
    reusable by itself.
    """
    if not user_input.conversation_id:
        return None
    return conversation_scope_key_for_input(user_input)


def scope_conversation_input(
    user_input: conversation.ConversationInput,
) -> tuple[conversation.ConversationInput, str | None]:
    """Scope an input while retaining the id that Home Assistant must see."""
    public_id = user_input.conversation_id
    scoped_id = conversation_scope_key_for_input(user_input)
    return replace(user_input, conversation_id=scoped_id), public_id
