"""Monitoring requests as automations (7.8.3).

"Überwache das Garagentor und melde dich, wenn es länger als 10 Minuten
offen ist." names the watched object first and the event last, and the
event refers back to the object with a pronoun.  This module recognizes the
reusable German *constructions* of such a request - never whole sentences -
and hands the established reader exactly what it already understands:

``MONITOR_HEAD``
    a durative monitoring verb with its object noun phrase:
    "überwache/beobachte NP", "behalte/hab NP im Auge|im Blick",
    "hab/halte ein Auge auf NP", "achte/pass auf NP (auf)",
    "kannst du NP überwachen/beobachten/im Auge behalten".
``CLAUSE_HEAD``
    the same verbs without an object, followed by a clause:
    "achte darauf, ob|dass|wenn ...", "pass auf, dass ...", "überwache, ob ...".

Frames (``segment_monitoring``):

1. ``MONITOR_HEAD (und|,) REST``: REST is an ordinary event/action request
   ("melde dich, wenn es ...", "wenn es ..., sag mir Bescheid"), segmented
   by ``segment_event_automation``; the head's noun phrase becomes the
   antecedent for "es/sie/er", "eins/eines davon" or a missing subject.
2. ``MONITOR_HEAD|CLAUSE_HEAD [,] ob|wenn|sobald|falls EVENT``: the
   monitoring verb itself asks to be told - an implicit notification of the
   speaker.
3. ``CLAUSE_HEAD [,] dass PROHIBITION``: "dass kein Fenster offen bleibt",
   "dass das Tor nicht länger als 10 Minuten offen ist" - the event is the
   violation ("kein" = any member).  A positive goal ("dass das Tor zu ist")
   has no violation event that could be read without inverting a state and
   is left to the caller (no frame).

One-off checks ("prüfe/kontrolliere, ob ...") are queries, not monitoring,
and are deliberately not monitoring verbs.  Grounding, validation, preview
and the write path are unchanged: this module only returns source spans and
the typed reference.

Home-Assistant-free and strictly typed.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .automation_language import (
    ActionCheck,
    ClauseOrder,
    ConditionSpan,
    EventActionFrame,
    EventReference,
    segment_event_automation,
)
from .nlu.automation_lexicon import noun_class, split_compound
from .nlu.german_morphology import GrammaticalGender, entity_name_gender

_LEAD = r"^(?:(?:bitte|kannst\s+du|könntest\s+du|würdest\s+du|du\s+sollst)\s+)?"
_WATCH = r"(?:überwache|überwach|beobachte|beobacht)"
_KEEP = r"(?:behalte|behalt|hab|habe|halte|halt)"
_ATTEND = r"(?:achte|acht|passe|pass)"
# A noun phrase ends at a clause boundary; it never contains a connector.
_NP = r"(?P<np>(?:(?!\b(?:und|wenn|sobald|falls|ob|dass)\b)[^,:])+?)"
_CONNECTOR = r"(?P<conn>ob|dass|wenn|sobald|falls)"

# MONITOR_HEAD alternatives, each with the object noun phrase ``np``.
_OBJECT_HEADS = (
    rf"{_WATCH}\s+{_NP}",
    rf"{_KEEP}\s+{_NP}\s+(?:im|in\s+dem)\s+(?:auge|blick)",
    rf"(?:hab|habe|halte|halt)\s+(?:ein|mal\s+ein)\s+auge\s+auf\s+{_NP}",
    rf"{_ATTEND}\s+auf\s+{_NP}(?:\s+auf)?",
    # Modal request, verb last: "Kannst du das Garagentor überwachen und ...".
    rf"(?:kannst|könntest|würdest)\s+du\s+(?:bitte\s+)?(?:mal\s+)?{_NP}\s+"
    rf"(?:überwachen|beobachten|(?:im|in\s+dem)\s+(?:auge|blick)\s+behalten)",
)
_OBJECT_REST_RES = tuple(
    re.compile(_LEAD + head + r"\s*(?:,\s*(?:und\s+)?|\s+und\s+)(?P<rest>.+)$", re.IGNORECASE)
    for head in _OBJECT_HEADS
)
_OPEN_RES = tuple(re.compile(_LEAD + head + r"$", re.IGNORECASE) for head in _OBJECT_HEADS)
_OBJECT_CLAUSE_RES = tuple(
    re.compile(_LEAD + head + rf"\s*,?\s*{_CONNECTOR}\s+(?P<event>.+)$", re.IGNORECASE)
    for head in _OBJECT_HEADS
)
_CLAUSE_HEAD_RE = re.compile(
    _LEAD
    + rf"(?:{_ATTEND}(?:\s+(?:darauf|drauf))?(?:\s+auf)?|{_WATCH})"
    + rf"\s*,?\s*{_CONNECTOR}\s+(?P<event>.+)$",
    re.IGNORECASE,
)
# Conditions spoken after the event clause ("..., wenn niemand zuhause ist").
_TRAILING_CONDITION_RE = re.compile(
    r"\s*,\s*(?:aber\s+nur\s+)?(?:wenn|falls|solange|während)\s+", re.IGNORECASE
)
_NEGATIVE_DETERMINERS = frozenset({"kein", "keine", "keiner", "keines", "keinen", "keinem"})
_NOT_AN_OBJECT = frozenset({
    "es", "das", "dies", "alles", "mich", "mir", "dich", "dir", "ihn", "sie", "uns",
    "darauf", "drauf", "auf", "mal", "bitte",
})


def _object_words(noun_phrase: str) -> tuple[str, ...] | None:
    words = tuple(word.strip(",.;:!?") for word in noun_phrase.split())
    words = tuple(word for word in words if word)
    if not words or all(word.casefold() in _NOT_AN_OBJECT for word in words):
        return None
    return words


def _split_trailing_condition(event: str) -> tuple[str, tuple[ConditionSpan, ...]]:
    match = _TRAILING_CONDITION_RE.search(event)
    if match is None:
        return event, ()
    left = event[:match.start()].strip(" ,")
    right = event[match.end():].strip(" ,.!?")
    if not left or not right:
        return event, ()
    return left, (ConditionSpan(right),)


def _violation(event: str) -> tuple[str, bool] | None:
    """The event that violates a prohibition, and whether "kein" quantified it."""
    words = event.split()
    keys = [word.casefold().strip(",.;:!?") for word in words]
    for index, key in enumerate(keys):
        if key in _NEGATIVE_DETERMINERS:
            return " ".join(words[:index] + words[index + 1:]), True
    if "nicht" in keys:
        index = keys.index("nicht")
        return " ".join(words[:index] + words[index + 1:]), False
    return None


def _implicit_frame(
    connector: str, event: str, reference: EventReference | None
) -> EventActionFrame | None:
    event = event.strip(" ,.!?")
    member = False
    if connector == "dass":
        violated = _violation(event)
        if violated is None:
            return None
        event, member = violated
    event, conditions = _split_trailing_condition(event)
    if not event:
        return None
    if member:
        reference = EventReference(reference.words if reference else (), member=True)
    return EventActionFrame(
        action_text="",
        event_text=event,
        # "ob"/"dass" introduce the watched event exactly like "wenn".
        connector="wenn" if connector in {"ob", "dass"} else connector,
        order=ClauseOrder.ACTION_FIRST,
        reference=reference,
        implicit_notification=True,
        extra_conditions=conditions,
    )


_OBJECT_PRONOUNS = {
    "sie": (GrammaticalGender.FEMININE,), "es": (GrammaticalGender.NEUTER,),
    "ihn": (GrammaticalGender.MASCULINE,),
}
_CLAUSE_CONNECTOR_RE = re.compile(r"\s*,?\s*\b(?:wenn|sobald|falls)\b", re.IGNORECASE)


def _np_gender(words: tuple[str, ...]) -> GrammaticalGender | None:
    head = words[-1]
    noun = noun_class(head)
    if noun is None:
        compound = split_compound(head)
        noun = compound[1] if compound is not None else None
    if noun is not None:
        return noun.gender
    return entity_name_gender(head)


def bind_action_pronoun(rest: str, words: tuple[str, ...]) -> str:
    """"schließ sie ab, wenn …" -> "schließ die Haustür ab, wenn …" (7.9 W6).

    Only an object pronoun of the action part (before the connector) is
    bound, only to the monitored noun phrase and only when its gender
    agrees - the same rule as for the event's subject pronoun.
    """
    connector = _CLAUSE_CONNECTOR_RE.search(rest)
    action = rest[:connector.start()] if connector is not None else rest
    tail = rest[len(action):]
    tokens = action.split()
    gender = _np_gender(words)
    for index, token in enumerate(tokens[1:], start=1):
        key = token.casefold().strip(",.;:!?")
        # Without a typed noun there is nothing to check against - the same
        # rule as for the event's subject pronoun (``_reference_agrees``).
        if key in _OBJECT_PRONOUNS and (gender is None or gender in _OBJECT_PRONOUNS[key]):
            bound = [*tokens[:index], " ".join(words), *tokens[index + 1:]]
            return " ".join(bound) + tail
    return rest


def open_monitoring_object(text: str) -> tuple[str, ...] | None:
    """"Überwache das Garagentor." - a monitored object without an event
    (7.9 W6): the words of the object, or ``None``."""
    stripped = text.strip().rstrip(".!?").strip()
    for pattern in _OPEN_RES:
        match = pattern.match(stripped)
        if match is not None:
            return _object_words(match.group("np"))
    return None


def segment_monitoring(text: str, action_ok: ActionCheck) -> EventActionFrame | None:
    """Read a monitoring request into one event/action frame, or ``None``."""
    stripped = text.strip()
    for pattern in _OBJECT_REST_RES:
        match = pattern.match(stripped)
        if match is None:
            continue
        words = _object_words(match.group("np"))
        rest = match.group("rest").strip()
        if words is None or not rest:
            continue
        rest = bind_action_pronoun(rest, words)
        inner = segment_event_automation(rest, action_ok)
        if inner is None:
            continue
        return replace(inner, reference=EventReference(words))
    for pattern in _OBJECT_CLAUSE_RES:
        match = pattern.match(stripped)
        if match is None:
            continue
        words = _object_words(match.group("np"))
        if words is None:
            continue
        frame = _implicit_frame(
            match.group("conn").casefold(), match.group("event"), EventReference(words)
        )
        if frame is not None:
            return frame
    match = _CLAUSE_HEAD_RE.match(stripped)
    if match is not None:
        return _implicit_frame(match.group("conn").casefold(), match.group("event"), None)
    return None


__all__ = ("bind_action_pronoun", "open_monitoring_object", "segment_monitoring")
