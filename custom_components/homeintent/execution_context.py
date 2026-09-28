"""Home Assistant ``Context`` for every HomeIntent service call.

Home Assistant already has a real causal chain: every service call carries a
``Context(id, user_id, parent_id)``; scripts run in the caller's context and
automations triggered by a state change get that change's context as their
``parent_id``. HomeIntent passes one context per user turn that leads to an
execution, so HA can attribute follow-up effects to the spoken command and
the speaking user (and, with ``user_id`` set, applies the user's own entity
permissions in addition to the HomeIntent policy).

``context.id`` is the ``execution_id`` that runs through plan, GoalRun,
experience, audit and trace. Traceability is never an authorization.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from homeassistant.core import Context


def new_execution_context(
    user_id: str | None = None, parent: Any | None = None
) -> Context:
    """A fresh context; ``parent`` (a Context) makes it a child in HA's chain."""
    parent_id = getattr(parent, "id", None) if parent is not None else None
    return Context(user_id=user_id, parent_id=parent_id)


def turn_context(user_input: Any, user_id: str | None = None) -> Context:
    """The execution context of one conversation turn.

    A child of the Assist request's own context when there is one, so HA's
    chain continues from the pipeline run to the device.
    """
    request = getattr(user_input, "context", None)
    if user_id is None:
        user_id = getattr(request, "user_id", None)
    return new_execution_context(user_id, request if getattr(request, "id", None) else None)


def system_context() -> Context:
    """Context for HomeIntent's own bookkeeping calls (no user)."""
    return Context()


def execution_id(context: Any) -> str | None:
    value = getattr(context, "id", None)
    return str(value) if value else None


@dataclass(frozen=True)
class ExecutionTurn:
    """One user turn: its single execution context and trace inputs."""

    context: Context
    user_id: str | None
    utterance: str | None


_TURN: ContextVar[ExecutionTurn | None] = ContextVar("homeintent_turn", default=None)


def begin_turn(user_input: Any, user_id: str | None, utterance: str | None) -> Token[ExecutionTurn | None]:
    """Open the turn: every execution in it shares one ``execution_id``."""
    return _TURN.set(ExecutionTurn(turn_context(user_input, user_id), user_id, utterance))


def end_turn(token: Token[ExecutionTurn | None]) -> None:
    _TURN.reset(token)


def current_turn() -> ExecutionTurn | None:
    return _TURN.get()


def call_context(user_id: str | None = None) -> Context:
    """The turn's context inside a turn, otherwise a fresh one."""
    turn = _TURN.get()
    if turn is not None:
        return turn.context
    return new_execution_context(user_id)
