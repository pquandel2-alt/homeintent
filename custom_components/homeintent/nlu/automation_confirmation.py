"""Automation Confirmation Reply Classifier (HomeIntent V5 Teil 7/10,
V5.23, "Dry Run"): decides whether a reply to "... Soll diese Automation
erstellt werden?" means yes, no, or neither - deterministically, with no
hassil grammar involved (a yes/no gate is a fixed, tiny closed vocabulary,
not worth a twelfth intent-YAML grammar).

Regel 4 ("niemals raten") applied for the first time to the confirmation
dialog itself, not just entity/area resolution: an unrecognized or
contradictory reply (neither list matches, or - degenerate but checked
anyway - somehow both do) must resolve to ``UNCLEAR``, never be guessed as
``YES`` or ``NO``. Persisting a real Home Assistant automation is the single
most hard-to-reverse action anywhere in this engine (see
``nlu/context.py``'s ``PendingAutomationConfirmation``) - silently guessing
wrong here is strictly worse than asking again.
"""

from __future__ import annotations

import re
from enum import Enum, auto


class ConfirmationReply(Enum):
    YES = auto()
    NO = auto()
    UNCLEAR = auto()


_PUNCTUATION_RE = re.compile(r"[.!?,]")
_WHITESPACE_RE = re.compile(r"\s+")

# Small, closed, exact-phrase vocabulary (Regel 4) - deliberately not a
# "contains a yes-word" substring check, which could misfire on a longer,
# unrelated reply that merely happens to contain "ja" or "nein" as part of
# another word/sentence.
_YES_PHRASES = frozenset({
    "ja", "ja bitte", "jawohl", "ja klar", "klar", "genau", "korrekt", "richtig",
    "ok", "okay", "in ordnung", "mach das", "erstelle sie", "erstellen",
    "bestätigt", "bestätige", "ja genau", "passt",
    # 7.8 B7: everyday assent
    "ja mach", "ja mach das", "ja mach mal", "ja mach es", "mach mal", "mach es", "los",
    "ja los", "leg los", "los gehts", "los geht's", "tu das", "ja tu das", "ja gerne", "gerne",
    "gern", "sicher", "ja sicher", "unbedingt", "auf jeden fall", "jep", "jo", "jawoll",
    "passt so", "ja passt", "ja bitte mach das", "ja genau so", "genau so", "mach nur",
})
_NO_PHRASES = frozenset({
    "nein", "nein danke", "nicht", "abbrechen", "stopp", "stop", "falsch",
    "lieber nicht", "vergiss es", "doch nicht", "nee",
})


def _canonicalize(text: str) -> str:
    canonical = text.strip().lower()
    canonical = _PUNCTUATION_RE.sub("", canonical)
    canonical = _WHITESPACE_RE.sub(" ", canonical).strip()
    return canonical


def classify_confirmation_reply(text: str) -> ConfirmationReply:
    """Exact-phrase match only, against the closed vocabularies above -
    the same "never guess" discipline every entity/area resolver in this
    engine already follows, just applied to a yes/no decision instead of a
    name. Anything not in either list - including a reply that tries to
    say something else entirely, e.g. a new sentence - resolves to
    ``UNCLEAR``; the caller decides how to re-ask (same "caller decides how
    to respond" split ``NluEngine.resolve_clarification()`` already uses
    for its own ``None`` case)."""
    canonical = _canonicalize(text)
    is_yes = canonical in _YES_PHRASES
    is_no = canonical in _NO_PHRASES
    if is_yes and not is_no:
        return ConfirmationReply.YES
    if is_no and not is_yes:
        return ConfirmationReply.NO
    return ConfirmationReply.UNCLEAR
