"""Deterministic reminder language mapped onto one-shot automations.

Only the *timing* and optional quiet hours are separated here.  The reminder
clause itself ("erinnere mich daran, den Backofen zu prüfen", "sag mir
Bescheid, dass ...", "benachrichtige mich, dass ...") is handed back to the
shared notification language (``notification_language.py``), so reminders,
spoken notifications and notification automations share one meaning.
"""

from __future__ import annotations

import re

from .nlu.phrases import Span, Word, words


_REMINDER_RE = re.compile(
    r"^\s*(?:bitte\s+)?(?P<verb>erinner(?:e|st)?)\s+(?P<recipient>mich|uns|[A-Za-zÄÖÜäöüß-]+)\s+"
    r"(?P<when>.+?)\s*,?\s+(?P<rest>(?:daran|an|dass)\b.+?)\s*[.!?]*$",
    re.IGNORECASE,
)
# Language island "Erinnerung" (7.5.2): "Sag X … Bescheid", "Benachrichtige
# X …, dass" and the quiet-hours suffix as word sequences with char offsets.
_RECIPIENT = re.compile(r"[A-Za-zÄÖÜäöüß-]+")


def _lead(tokens: list[Word], verbs: frozenset[str]) -> int | None:
    """Index of the verb after an optional leading "bitte"."""
    index = 1 if tokens and tokens[0].text.casefold() == "bitte" else 0
    return index if index < len(tokens) and tokens[index].text.casefold() in verbs else None


def _message(text: str, start: int) -> str:
    return re.sub(r"\s*[.!?]*$", "", text[start:])


def _tell(text: str) -> Span | None:
    """"(bitte) sag X <wann> Bescheid(,) dass|über <Nachricht>"."""
    tokens = words(text)
    verb = _lead(tokens, frozenset({"sag"}))
    if verb is None or verb + 2 >= len(tokens):
        return None
    recipient = tokens[verb + 1]
    if not _RECIPIENT.fullmatch(recipient.text) or text[recipient.end:recipient.end + 1] not in (" ", "\t"):
        return None
    for index in range(verb + 3, len(tokens) - 2):
        if tokens[index].key != "bescheid" or not text[tokens[index].start - 1].isspace():
            continue
        link = tokens[index + 1]
        if link.text.casefold() not in {"dass", "über", "ueber"}:
            continue
        between = text[tokens[index].end:link.start]
        if between.replace(",", "", 1).strip():
            continue
        if not text[link.end:link.end + 1].isspace() or index + 2 >= len(tokens):
            continue
        when = text[tokens[verb + 2].start:tokens[index].start].rstrip()
        return Span(0, len(text), named={
            "verb": tokens[verb].text, "recipient": recipient.text, "when": when,
            "message": _message(text, tokens[index + 2].start),
        })
    return None


def _notify(text: str) -> Span | None:
    """"(bitte) benachrichtige|informiere X <wann>(,) dass <Nachricht>"."""
    tokens = words(text)
    verb = _lead(tokens, frozenset({"benachrichtige", "informiere"}))
    if verb is None or verb + 2 >= len(tokens):
        return None
    recipient = tokens[verb + 1]
    if not _RECIPIENT.fullmatch(recipient.text) or not text[recipient.end:recipient.end + 1].isspace():
        return None
    for index in range(verb + 3, len(tokens) - 1):
        dass = tokens[index]
        if dass.text.casefold() != "dass" or not text[dass.start - 1].isspace():
            continue
        if not text[dass.end:dass.end + 1].isspace():
            continue
        when = text[tokens[verb + 2].start:dass.start].rstrip()
        if when.endswith(","):
            when = when[:-1].rstrip()
        if not when:
            continue
        return Span(0, len(text), named={
            "verb": tokens[verb].text, "recipient": recipient.text, "when": when,
            "message": _message(text, tokens[index + 1].start),
        })
    return None


def _quiet(text: str) -> tuple[int, int, int] | None:
    """"…, (aber) nicht zwischen 22 und 7 Uhr" / "außerhalb der Ruhezeit von
    23 bis 6 Uhr" at the end: (cut position, start hour, end hour)."""
    tokens = words(text)
    if not tokens or text[tokens[-1].end:].strip():
        return None
    end = len(tokens)
    if tokens[end - 1].key == "uhr":
        end -= 1
    clock = re.compile(r"(\d{1,2})(?::\d{2})?")
    last_match = clock.fullmatch(tokens[end - 1].key) if end >= 4 else None
    if last_match is None:
        return None
    last = int(last_match.group(1))
    link = end - 2
    if tokens[link].key not in {"und", "bis"}:
        return None
    first_index = link - 1
    if tokens[first_index].key == "uhr":
        first_index -= 1
    first_match = clock.fullmatch(tokens[first_index].key) if first_index >= 1 else None
    if first_match is None:
        return None
    first = int(first_match.group(1))
    cue = first_index - 1
    if tokens[cue].key == "zwischen" and cue >= 1 and tokens[cue - 1].key == "nicht":
        start = cue - 1
        if start >= 1 and tokens[start - 1].key == "aber":
            start -= 1
    elif (
        tokens[cue].key == "von" and cue >= 3 and tokens[cue - 1].key == "ruhezeit"
        and tokens[cue - 2].key == "der" and tokens[cue - 3].text.casefold() == "außerhalb"
    ):
        start = cue - 3
    else:
        return None
    prefix = text[:tokens[start].start].rstrip()
    if prefix.endswith(","):
        prefix = prefix[:-1].rstrip()
    return len(prefix), first, last


def _match(text: str) -> re.Match[str] | Span | None:
    quiet = _quiet(text)
    cleaned = text[:quiet[0]] if quiet is not None else text
    return _REMINDER_RE.match(cleaned) or _tell(cleaned) or _notify(cleaned)


def reminder_automation_text(text: str) -> str | None:
    """Return the same request with its timing moved to the front.

    Timing and action parsing deliberately stay in the existing automation
    parsers and the shared notification language; this adapter only removes
    the quiet-hours suffix and restores a word order the one-shot parsers
    read ("in fünf Minuten erinnere mich an die Waschmaschine").
    """
    match = _match(text)
    if match is None:
        return None
    when = match.group("when").strip(" ,")
    verb = match.group("verb").casefold()
    recipient = match.group("recipient")
    if "rest" in match.groupdict() and match.group("rest") is not None:
        body = match.group("rest").strip(" ,.!?")
        clause = f"erinnere {recipient} {body}"
    else:
        message = match.group("message").strip(" ,.!?")
        if not message:
            return None
        clause = (
            f"sag {recipient} Bescheid, dass {message}" if verb == "sag"
            else f"{verb} {recipient}, dass {message}"
        )
    if not when or len(clause) > 520:
        return None
    return f"{when} {clause}"


def reminder_recipient(text: str) -> str | None:
    match = _match(text)
    if match is None:
        return None
    recipient = match.group("recipient")
    return None if recipient.casefold() in {"mich", "mir", "uns"} else recipient


def reminder_quiet_hours(text: str) -> tuple[int, int] | None:
    quiet = _quiet(text)
    if quiet is None:
        return None
    _cut, start, end = quiet
    return (start, end) if 0 <= start <= 23 and 0 <= end <= 23 and start != end else None
