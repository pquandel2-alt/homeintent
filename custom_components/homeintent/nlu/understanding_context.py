"""Immutable per-turn context for canonical semantic understanding."""

from __future__ import annotations

from dataclasses import dataclass

from ..areas import AreaSnapshot


@dataclass(frozen=True)
class UnderstandingContext:
    """Facts about the current input that are not part of its spoken text.

    ``source_area`` is the physical area of the current Assist input device.
    It is deliberately separate from ``ConversationContext.last_area``, which
    describes a previously grounded conversational location.
    """

    source_area: AreaSnapshot | None = None
    script_verbs: tuple[tuple[str, ...], ...] = ()
