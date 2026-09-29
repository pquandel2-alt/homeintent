"""Questions about an open dialog itself (7.7 B6).

"Was hast du verstanden?", "Warum fragst du?" and "abbrechen / vergiss es /
lass das" are asked inside any open question. The fixed wordings live here
once, read word by word, instead of as a copy of the same sentence pattern
in every controller.
"""

from __future__ import annotations

from enum import Enum

from .phrases import has, words


class MetaQuestion(Enum):
    UNDERSTOOD = "understood"  # was hast du verstanden
    WHY_ASKING = "why_asking"  # warum fragst du
    CANCEL = "cancel"  # abbrechen, vergiss es, lass das


_PHRASES: dict[MetaQuestion, tuple[str, ...]] = {
    MetaQuestion.UNDERSTOOD: ("was hast du verstanden",),
    MetaQuestion.WHY_ASKING: ("warum fragst du",),
    MetaQuestion.CANCEL: ("abbrechen", "vergiss es", "lass das"),
}


def meta_questions(text: str) -> frozenset[MetaQuestion]:
    """Every dialog meta question the utterance asks."""
    tokens = words(text)
    return frozenset(kind for kind, phrases in _PHRASES.items() if has(tokens, *phrases))
