"""Arbitration between interpreter candidates (7.5.0).

Every interpreter (parser, learned binding, discourse, need, situation view)
proposes a ``Candidate``; ``arbitrate`` decides with fixed, explainable rules
instead of "whoever matches first wins":

* exactly one executable candidate without residue -> that one;
* several executable candidates with the same effect -> merged (one runs);
* contradicting executable candidates -> evidence decides when it is
  unambiguous (an explicit question beats a command, an explicitly named
  device beats a need, a parser beats discourse); otherwise ask or do
  nothing;
* an executable candidate with unexplained residue never executes;
* read-only candidates are answered (all of them) when no executable
  candidate exists.

The arbiter never executes and never authorises: the chosen payload still
goes through validator, EffectGraph, execution policy and NEVER_AUTO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto
from typing import Any, Sequence


class Authority(IntEnum):
    """How directly the candidate follows from what was said (higher wins)."""

    DISCOURSE = 1  # completed from the conversation context
    NEED = 2  # an implicit need ("Mir ist kalt")
    BINDING = 3  # a confirmed learned binding
    PARSER = 4  # the words of this sentence


class Effect(Enum):
    WRITE = auto()  # would change the house now
    DEFER = auto()  # creates something that asks first (automation, timer)
    READ = auto()  # answers
    NONE = auto()  # asks back or declines


class DecisionKind(Enum):
    EXECUTE = auto()
    MERGE = auto()
    ANSWER = auto()
    DEFER = auto()
    ASK = auto()
    NOTHING = auto()
    # Open dialog (7.7): the turn continues it, or a new turn supersedes it.
    CONTINUE_DIALOG = auto()
    SUPERSEDE_DIALOG = auto()


class DialogReply(Enum):
    YES = auto()
    NO = auto()
    UNCLEAR = auto()


@dataclass(frozen=True)
class DialogEvidence:
    """An open question of the conversation as typed evidence (7.7, B3).

    The arbiter does not manage dialogs; it only learns what is open and how
    the turn relates to it. ``supersedable``: a complete new command may
    replace the question (not while an automation is being composed).
    ``drops_on_new_sentence``: a yes/no safety question that any full new
    sentence ends - nothing of it runs.
    """

    kind: str
    reply: DialogReply
    new_sentence: bool
    command_shaped: bool
    supersedable: bool
    drops_on_new_sentence: bool = False


@dataclass(frozen=True)
class Candidate:
    source: str
    speech_act: str
    authority: Authority
    effect: Effect
    targets: frozenset[str] = frozenset()
    operations: frozenset[str] = frozenset()
    risk: int = 0
    residue: tuple[str, ...] = ()
    explicit_device: bool = False
    evidence: tuple[str, ...] = ()
    payload: Any = field(default=None, compare=False, repr=False)

    @property
    def executable(self) -> bool:
        return self.effect is Effect.WRITE

    @property
    def same_effect_key(self) -> tuple[frozenset[str], frozenset[str]]:
        return self.targets, self.operations


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    chosen: tuple[Candidate, ...] = ()
    reason: str = ""

    @property
    def writes(self) -> bool:
        return self.kind in {DecisionKind.EXECUTE, DecisionKind.MERGE}


def arbitrate(candidates: Sequence[Candidate], *, explicit_question: bool = False) -> Decision:
    """Decide between candidates; pure and deterministic."""
    writers = [item for item in candidates if item.executable]
    with_residue = [item for item in writers if item.residue]
    clean = [item for item in writers if not item.residue]
    readers = [item for item in candidates if item.effect is Effect.READ]
    deferred = [item for item in candidates if item.effect is Effect.DEFER]

    if explicit_question and readers:
        # An explicit question beats a command reading of the same words.
        return Decision(DecisionKind.ANSWER, tuple(readers), "explicit_question")
    if with_residue and not clean:
        # Half understood: never execute.
        return Decision(DecisionKind.NOTHING, tuple(with_residue), "residue")
    if clean:
        if len(clean) == 1 and not deferred:
            return Decision(DecisionKind.EXECUTE, (clean[0],), "single_executable")
        keys = {item.same_effect_key for item in clean}
        if len(keys) == 1 and not deferred:
            best = max(clean, key=lambda item: item.authority)
            return Decision(DecisionKind.MERGE, (best,), "same_effect")
        # Contradicting candidates: unambiguous evidence decides.
        top = max(item.authority for item in clean)
        strongest = [item for item in clean if item.authority == top]
        explicit = [item for item in strongest if item.explicit_device]
        if deferred and top >= Authority.PARSER:
            # "In zehn Minuten …": time-bound meaning is never run now.
            return Decision(DecisionKind.DEFER, tuple(deferred), "time_bound")
        if len(explicit) == 1:
            return Decision(DecisionKind.EXECUTE, (explicit[0],), "explicit_device")
        if len({item.same_effect_key for item in strongest}) == 1:
            return Decision(DecisionKind.MERGE, (strongest[0],), "strongest_authority")
        return Decision(DecisionKind.ASK, tuple(strongest), "ambiguous_executable")
    if deferred:
        return Decision(DecisionKind.DEFER, tuple(deferred), "deferred")
    if readers:
        return Decision(DecisionKind.ANSWER, tuple(readers), "read_only")
    return Decision(DecisionKind.NOTHING, (), "no_candidate")


def arbitrate_dialog(evidence: DialogEvidence, new_command: Candidate | None = None) -> Decision:
    """Continue the open question or let the new turn supersede it.

    Pure and deterministic. A superseded question is discarded, never
    executed; the new turn still passes validator, EffectGraph and policy,
    so an open dialog can never lower a confirmation.
    """
    if evidence.drops_on_new_sentence and evidence.new_sentence:
        return Decision(DecisionKind.SUPERSEDE_DIALOG, (), "new_sentence_drops_question")
    if (
        new_command is not None
        and evidence.supersedable
        and evidence.command_shaped
        and new_command.executable
        and not new_command.residue
    ):
        return Decision(DecisionKind.SUPERSEDE_DIALOG, (new_command,), "complete_new_command")
    if evidence.reply in {DialogReply.YES, DialogReply.NO}:
        return Decision(DecisionKind.CONTINUE_DIALOG, (), "answer")
    return Decision(DecisionKind.CONTINUE_DIALOG, (), "dialog_continues")


def needs_command_reading(evidence: DialogEvidence) -> bool:
    """Whether the caller has to read the turn as a command for the arbiter."""
    return (
        evidence.supersedable
        and evidence.command_shaped
        and not (evidence.drops_on_new_sentence and evidence.new_sentence)
    )


__all__ = (
    "Authority", "Candidate", "Decision", "DecisionKind", "DialogEvidence", "DialogReply", "Effect",
    "arbitrate", "arbitrate_dialog", "needs_command_reading",
)
