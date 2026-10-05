"""What a turn actually did, as types (7.9.1 Teil B).

The response style ("spoken" or "tone") is decided once, at the end of the
turn, from these typed facts - never from the reply text:

* ``EXECUTED``    - a write ran and its effect is confirmed (or, for scenes,
  scripts, automations and monitors, the write itself is the effect);
* ``UNCONFIRMED`` - a write ran, but the device has not reached the target
  state yet (a cover still moving, a device that did not follow);
* ``NOT_DONE``    - a planned write did not run (refused, failed, needs a
  confirmation);
* ``LEARNED``     - something was learned or remembered.

Writers report through ``report_outcome``; the single physical write path
(``service_executor``) reports every device write itself. A turn without any
report never earns the tone: "Im Zweifel spricht HomeIntent."

Home-Assistant-free.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import Enum

__all__ = (
    "TurnOutcomeKind",
    "TurnOutcomes",
    "begin_outcomes",
    "end_outcomes",
    "report_outcome",
)


class TurnOutcomeKind(Enum):
    EXECUTED = "executed"
    UNCONFIRMED = "unconfirmed"
    NOT_DONE = "not_done"
    LEARNED = "learned"


@dataclass
class TurnOutcomes:
    """Every typed fact reported during one turn, in order."""

    kinds: list[TurnOutcomeKind] = field(default_factory=list)

    @property
    def fully_executed(self) -> bool:
        """At least one write, every write ran, every effect confirmed."""
        return bool(self.kinds) and all(kind is TurnOutcomeKind.EXECUTED for kind in self.kinds)

    @property
    def partial(self) -> bool:
        return TurnOutcomeKind.EXECUTED in self.kinds and TurnOutcomeKind.NOT_DONE in self.kinds


_OUTCOMES: ContextVar[TurnOutcomes | None] = ContextVar("homeintent_turn_outcomes", default=None)


def begin_outcomes() -> tuple[TurnOutcomes, Token[TurnOutcomes | None]]:
    outcomes = TurnOutcomes()
    return outcomes, _OUTCOMES.set(outcomes)


def end_outcomes(token: Token[TurnOutcomes | None]) -> None:
    _OUTCOMES.reset(token)


def report_outcome(kind: TurnOutcomeKind) -> None:
    """Record one fact for the running turn; outside a turn it is dropped
    (proactive and agent writes have no reply to shape)."""
    outcomes = _OUTCOMES.get()
    if outcomes is not None:
        outcomes.kinds.append(kind)
