"""Closed natural-language meanings for explicit routine feedback."""

from __future__ import annotations

from dataclasses import dataclass

from .entities import normalize_for_compare
from .nlu.language_frontend import LanguageDocument
from .situation import RoutineFeedback


@dataclass(frozen=True)
class RoutineFeedbackIntent:
    feedback: RoutineFeedback


# "<Bezug> <Aussage>": the reference to the last hint, then one closed
# feedback predicate. Only the whole utterance counts, never bare praise.
def phrase(text: str) -> tuple[str, ...]:
    return tuple(text.split())


_REFERENCES = tuple(phrase(item) for item in (
    "das", "dies", "dieser hinweis", "diese routine", "diese meldung",
))
_PREDICATES: dict[tuple[str, ...], RoutineFeedback] = {
    **{phrase(item): RoutineFeedback.IGNORE for item in (
        "ignorieren", "kuenftig ignorieren", "nie wieder", "kuenftig nie wieder",
        "ignorieren bitte", "kuenftig ignorieren bitte", "nie wieder bitte",
        "kuenftig nie wieder bitte",
    )},
    **{phrase(item): RoutineFeedback.LATER for item in (
        "spaeter", "noch einmal spaeter", "spaeter bitte", "noch einmal spaeter bitte",
    )},
    **{phrase(f"{verb} {item}"): feedback for verb in ("war", "ist") for item, feedback in (
        ("hilfreich", RoutineFeedback.HELPFUL),
        ("unnoetig", RoutineFeedback.UNNECESSARY),
        ("nicht hilfreich", RoutineFeedback.UNNECESSARY),
        ("falsch", RoutineFeedback.WRONG),
    )},
}


def interpret_routine_feedback(
    document: LanguageDocument,
) -> RoutineFeedbackIntent | None:
    """Interpret an explicit referential feedback sentence, never bare praise."""
    text = normalize_for_compare(document.normalized_text)
    words = tuple(text.split())
    if not all(word.isalnum() for word in words):
        return None
    asks_again = bool(words) and words[0].startswith("frag")
    for start in ((0, 1) if asks_again else (0,)):
        for reference in _REFERENCES:
            if words[start:start + len(reference)] != reference:
                continue
            feedback = _PREDICATES.get(words[start + len(reference):])
            if feedback is not None and (start == 0 or feedback is RoutineFeedback.LATER):
                return RoutineFeedbackIntent(feedback)
    return None


__all__ = ("RoutineFeedbackIntent", "interpret_routine_feedback")
