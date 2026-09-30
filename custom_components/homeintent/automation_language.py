"""Compositional German reading of event-triggered automations (7.2.0).

Two deterministic, dictionary-driven stages - no sentence list, no LLM:

1. **Clause segmentation** (:func:`segment_event_automation`).  An
   event-notification request consists of a *notification main clause*
   ("benachrichtige mich", "schick mir eine Nachricht", "ich möchte
   informiert werden", ...) and a *subordinate event clause* introduced by
   ``wenn``/``sobald``/``falls``/``sofern`` (``immer wenn``, ``jedes Mal
   wenn`` are normalized to these).  Both orders are accepted, and the
   boundary never depends on a comma: in trigger-first sentences it is the
   word position where the remainder is a complete notification clause.
   Explicitly dictated message text is protected - a connector inside it is
   inert data, never a second clause.

2. **Semantic role extraction** (:func:`read_event_roles`).  The event
   clause is decomposed into SUBJECT / LOCATION / COMPARATOR / VALUE / UNIT /
   STATE / DIRECTION / DURATION / CONDITION roles by word function, not by
   word position.  German verb-second, verb-final and perfect
   ("erreicht hat") orders therefore produce the same roles: the verbal
   material is recognized and removed, whatever position it has.

Grounding of the SUBJECT against the house, and projection into a
``TriggerModel``, happen in ``automation_grounding.py``.  The notification
clause itself is read by ``notification_language.py`` - the single
authority for notification meaning.

Home-Assistant-free and strictly typed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import Callable

from .nlu.automation_lexicon import rejoin_stt, resolve_repairs
from .nlu.automation_model import NumericComparator, PresenceEvent, SunEvent
from .nlu.lexicon import weekday_vocabulary
from .nlu.measurement import TravelDirection
from .nlu.normalize import german_number, normalize
from .nlu.semantic_state import SemanticState
from .notification_language import NotificationClause, parse_notification_clause


# --- preprocessing --------------------------------------------------------------

_JEDES_MAL_RE = re.compile(r"\bjedes\s*mal\b\s*,?", re.IGNORECASE)
_IMMER_DANN_RE = re.compile(r"\bimmer\s+dann\b", re.IGNORECASE)
_WISH_GERN_RE = re.compile(
    r"\b(ich\s+(?:hätte|haette|möchte|moechte|würde|wuerde))\s+(?:sehr\s+)?(?:gern|gerne)\b",
    re.IGNORECASE,
)
_SAY_SHELL_RE = re.compile(r"^\s*(?:sag|sage)\s+(?:(?:mir|uns)\s+)?", re.IGNORECASE)
_LEADING_NOISE_RE = re.compile(
    r"^(?:(?:also|ähm?|äh|hm+|okay|ok|so|und|hey|hallo|homeintent)\b[\s,]*)+",
    re.IGNORECASE,
)
_TRAILING_NOISE_RE = re.compile(r"\s+(?:punkt|bitte|danke)\s*[.!?]*$", re.IGNORECASE)


@dataclass(frozen=True)
class PreparedText:
    text: str
    repaired: bool


# Verbs saying a detector fired ("auslöst", "anschlägt", "etwas meldet").
_DETECTOR_EVENT_VERBS = frozenset({
    "auslöst", "ausgelöst", "anschlägt", "angeschlagen", "reagiert", "meldet",
    "gemeldet", "alarmiert", "anspringt", "angesprungen", "piept", "losgeht",
    "alarm", "schlägt", "geschlagen", "anschlagen",
})


def prepare_automation_text(raw: str) -> PreparedText:
    """Repairs first (they need the hesitation markers), then shared normalization."""
    repair = resolve_repairs(raw)
    text = _JEDES_MAL_RE.sub("immer ", repair.text)
    text = _IMMER_DANN_RE.sub("immer", text)
    text = rejoin_stt(text)
    # "ich hätte gern eine Nachricht" is a wish addressed to the speaker;
    # normalize() would reduce it to a bare "bitte".
    text = _WISH_GERN_RE.sub(r"\1", text)
    shell = _SAY_SHELL_RE.match(text)
    if shell is not None:
        # normalize() drops "sag (mir)" as a politeness shell; here it is
        # the notification verb itself ("Sag Julia Bescheid, wenn ...").
        text = shell.group(0) + _normalize_outside_messages(text[shell.end():])
    else:
        text = _normalize_outside_messages(text)
    text = _LEADING_NOISE_RE.sub("", text)
    text = _TRAILING_NOISE_RE.sub("", text)
    return PreparedText(re.sub(r"\s+", " ", text).strip(), repair.repaired)


def _normalize_outside_messages(text: str) -> str:
    """Shared normalization for the request, never for dictated message text."""
    spans = protected_message_spans(text)
    if not spans:
        return normalize(text)
    pieces: list[str] = []
    last = 0
    for start, end in sorted(spans):
        if start < last:
            continue
        pieces.append(normalize(text[last:start]))
        pieces.append(text[start:end].strip())
        last = end
    pieces.append(normalize(text[last:]))
    return " ".join(piece for piece in pieces if piece)


# --- clause segmentation -----------------------------------------------------------


class ClauseOrder(Enum):
    ACTION_FIRST = auto()  # "Benachrichtige mich, wenn X."
    EVENT_FIRST = auto()  # "Wenn X, benachrichtige mich."


@dataclass(frozen=True)
class TemporalEvent:
    """A sun or recurring clock event spoken as a phrase, not a clause
    ("bei Sonnenuntergang", "jeden Tag um 6:45 Uhr")."""

    sun_event: SunEvent | None = None
    offset_minutes: int | None = None
    hour: int | None = None
    minute: int | None = None
    weekdays: tuple[str, ...] = ()


ActionCheck = Callable[[str], bool]


@dataclass(frozen=True)
class EventActionFrame:
    """Source spans of one event clause and one action clause.

    The action clause is anything the caller's ``ActionCheck`` accepts - a
    notification clause or device actions; this module never decides what
    an action *means*.
    """

    action_text: str
    event_text: str  # without its connector
    connector: str
    order: ClauseOrder
    temporal: TemporalEvent | None = None
    # "Benachrichtige mich, sobald X, mit dem Text: Y" - text dictated after
    # the event clause still belongs to the notification.
    trailing_message: str | None = None
    # Monitoring frames (7.8.3, ``automation_monitoring``): the monitored
    # object an anaphor in the event clause refers to ("Überwache das
    # Garagentor und melde dich, wenn *es* ..."), a notification implied by
    # the monitoring verb itself ("Achte darauf, ob ..."), conditions spoken
    # after the event ("..., dass kein Fenster offen bleibt, wenn niemand
    # zuhause ist") and a prohibition whose violation is the event
    # ("kein Fenster" -> any window).
    reference: "EventReference | None" = None
    implicit_notification: bool = False
    extra_conditions: tuple["ConditionSpan", ...] = ()


@dataclass(frozen=True)
class _Word:
    start: int
    end: int
    text: str

    @property
    def key(self) -> str:
        return self.text.strip(",.;:!?\"'„“”»«").casefold()


_CONNECTORS = frozenset({"wenn", "sobald", "falls", "sofern"})
# Head words that only restate "every time" / hesitation around a connector.
_HEAD_FILLERS_RE = re.compile(r"\b(?:immer|dann|jedesmal)\b", re.IGNORECASE)
_TRAILING_TEXT_RE = re.compile(
    r"^(?P<event>.+?)\s*,?\s+mit\s+(?:dem|der|folgendem|folgender)\s+"
    r"(?:text|nachricht|inhalt)\s*:?\s+(?P<message>.+)$",
    re.IGNORECASE,
)
_MESSAGE_MARKER_RE = re.compile(
    r"(?::\s)|(?:\bmit\s+(?:dem|der|folgendem|folgender)\s+(?:text|nachricht|inhalt)\b)"
    r"|(?:\bdie\s+nachricht\b)|[„\"“«]",
    re.IGNORECASE,
)
_QUOTE_OPEN = "„\"“«"
_QUOTE_CLOSE = "“\"”»"


def _words(text: str) -> list[_Word]:
    return [_Word(match.start(), match.end(), match.group(0)) for match in re.finditer(r"\S+", text)]


def protected_message_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of dictated message text.

    A colon message runs to the end of the utterance (it is the last thing
    said).  A quoted message runs to its closing quote.  "mit dem Text X" and
    "die Nachricht X" run until a following ", wenn/sobald/..." whose
    remainder is again an event clause, otherwise to the end.
    """
    spans: list[tuple[int, int]] = []
    for marker in _MESSAGE_MARKER_RE.finditer(text):
        start = marker.start()
        if any(begin <= start < end for begin, end in spans):
            continue
        token = marker.group(0)
        if token.startswith(":"):
            if re.search(r"\d:\s?\d", text[max(0, start - 1):start + 3]):
                continue  # a clock time "20:30"
            spans.append((start, len(text)))
            continue
        if token in _QUOTE_OPEN:
            close = min(
                (index for index in (text.find(char, start + 1) for char in _QUOTE_CLOSE) if index > start),
                default=len(text) - 1,
            )
            spans.append((start, close + 1))
            continue
        end = len(text)
        tail = re.search(r",\s*(?:wenn|sobald|falls|sofern)\b", text[marker.end():], re.IGNORECASE)
        if tail is not None:
            end = marker.end() + tail.start()
        spans.append((start, end))
    return spans


def _connector_positions(words: list[_Word], protected: list[tuple[int, int]]) -> list[int]:
    return [
        index for index, word in enumerate(words)
        if word.key in _CONNECTORS
        and not any(begin <= word.start < end for begin, end in protected)
    ]


def _clean_notification_head(head: str) -> str:
    cleaned = _HEAD_FILLERS_RE.sub(" ", head)
    return re.sub(r"\s+", " ", cleaned).strip(" ,")


def only_quoted_connectors(text: str) -> bool:
    """Every trigger connector sits inside dictated/quoted text."""
    words = _words(text)
    all_connectors = [word for word in words if word.key in _CONNECTORS]
    return bool(all_connectors) and not _connector_positions(words, protected_message_spans(text))


def is_notification_text(text: str) -> bool:
    return _notification(text) is not None


def _notification(text: str) -> NotificationClause | None:
    candidate = _strip_then(text)
    if not candidate:
        return None
    return parse_notification_clause(candidate)


def _strip_then(text: str) -> str:
    return re.sub(r"^\s*,?\s*(?:(?:dann|immer|bitte)\s+)+", "", text.strip(" ,"), flags=re.IGNORECASE).strip(" ,")


# Imperative/modal openings of a device action clause.  Only used to decide
# *where* an action clause may start in comma-less speech; the action's
# meaning is always read by the established action parsers.
ACTION_OPENERS = frozenset({
    "schalte", "schalt", "mach", "mache", "fahre", "fahr", "öffne", "schließe", "schliess",
    "schliesse", "stelle", "stell", "setze", "setz", "starte", "stoppe", "aktiviere",
    "deaktiviere", "dimme", "dimm", "drehe", "dreh", "spiele", "spiel", "kannst", "könntest",
    "bitte", "dann", "sperre", "entsperre", "lass", "lasse",
})


_LEADING_POLITENESS = frozenset({"bitte", "dann", "auch", "und", "du", "doch", "noch"})


def looks_like_device_action(text: str) -> bool:
    """Cheap pre-selection before the (expensive) action parsers run.

    A device action clause opens with an imperative or a modal request and
    is one clause - an inner comma that does not coordinate ("Stell dir vor,
    du würdest ...") is never an action clause.
    """
    stripped = text.strip(" ,.!?")
    if re.search(r",(?!\s*(?:und|dann|danach)\b)", stripped):
        return False
    words = [word.strip(",.;:!?").casefold() for word in stripped.split()]
    words = [word for word in words if word not in _LEADING_POLITENESS]
    return bool(words) and words[0] in ACTION_OPENERS


def segment_event_automation(text: str, action_ok: ActionCheck) -> EventActionFrame | None:
    """Find the one EVENT clause + ACTION clause decomposition, if any."""
    words = _words(text)
    if len(words) < 3:
        return None
    protected = protected_message_spans(text)
    connectors = _connector_positions(words, protected)
    if not connectors:
        return _segment_implicit_then(text, action_ok) or _segment_temporal(text, action_ok)
    if connectors[0] == 0:
        return _segment_event_first(text, words, protected, action_ok)
    for index in connectors:
        head = _clean_notification_head(text[:words[index].start])
        if not head or not action_ok(head):
            continue
        event_text = text[words[index].end:].strip(" ,")
        trailing_message: str | None = None
        trailing = _TRAILING_TEXT_RE.match(event_text)
        if trailing is not None and is_notification_text(head):
            trailing_message = (
                trailing.group("message").strip().strip(_QUOTE_OPEN + _QUOTE_CLOSE).strip() or None
            )
            event_text = trailing.group("event").strip(" ,")
        elif is_notification_text(head):
            event_text, trailing_message = _split_dictated_content(event_text)
        if not event_text:
            return None
        return EventActionFrame(
            head, event_text, words[index].key, ClauseOrder.ACTION_FIRST,
            trailing_message=trailing_message,
        )
    return None


def _split_dictated_content(event_text: str) -> tuple[str, str | None]:
    """"das Küchenfenster geöffnet wird, dass ich lüften soll" -> event + text.

    Dictated content is inert message data; it is separated before the event
    is grounded so its words are never read as a device or a command.
    """
    from .notification_language import message_from_dass_content

    words = event_text.split()
    for index, word in enumerate(words[1:], start=1):
        if word.casefold().strip(",") == "dass" and words[index - 1].endswith(","):
            event = " ".join(words[:index]).strip(" ,")
            message = message_from_dass_content(" ".join(words[index + 1:]))
            if event and message:
                return event, message
    return event_text, None


def _segment_event_first(
    text: str, words: list[_Word], protected: list[tuple[int, int]], action_ok: ActionCheck
) -> EventActionFrame | None:
    connector = words[0]
    body_start = connector.end
    # The written clause boundary first; then the word positions where an
    # action clause can begin - speech recognition rarely emits commas.
    commas = [
        match.start() + 1
        for match in re.finditer(",", text)
        if match.start() > body_start
        and not any(begin <= match.start() < end for begin, end in protected)
    ]
    for position in commas:
        event_text = text[body_start:position].strip(" ,")
        rest = _strip_then(text[position:])
        if event_text and rest and action_ok(rest):
            return EventActionFrame(rest, event_text, connector.key, ClauseOrder.EVENT_FIRST)
    for word in words[2:]:
        if any(begin <= word.start < end for begin, end in protected):
            break
        event_text = text[body_start:word.start].strip(" ,")
        rest = _strip_then(text[word.start:])
        if not event_text or not rest:
            continue
        if is_notification_text(rest) or (word.key in ACTION_OPENERS and action_ok(rest)):
            return EventActionFrame(rest, event_text, connector.key, ClauseOrder.EVENT_FIRST)
    return None


_WEEKDAYS = weekday_vocabulary()
_SUN_SPAN_RE = re.compile(
    r"\b(?:(?P<amount>\d+|[a-zäöüß]+)\s+minuten?\s+(?P<rel>vor|nach)\s+(?:dem\s+)?"
    r"|(?:bei|zum|mit\s+dem|ab)\s+)?(?P<sun>sonnenuntergang|sonnenaufgang)\b",
    re.IGNORECASE,
)
_RECURRING_RE = re.compile(
    r"\b(?:jeden\s+(?:tag|morgen|abend|mittag|nachmittag)|jede\s+nacht|täglich)\b",
    re.IGNORECASE,
)
_WEEKDAY_RECUR_RE = re.compile(
    r"\b(?:jeden\s+(?P<one>montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)"
    r"|(?P<plural>montags|dienstags|mittwochs|donnerstags|freitags|samstags|sonntags|werktags)"
    r"|(?:am|an\s+den|an)\s+(?P<group>wochenende|wochenenden|werktagen))\b",
    re.IGNORECASE,
)
_CLOCK_RE = re.compile(
    # "um drei Grad", "um 20 Prozent" are the size of a change (7.6.1).
    r"\bum\s+(?P<hour>\d{1,2}|[a-zäöüß]+)(?::(?P<minute>\d{2}))?\b(?!\s*(?:grad|prozent|%|°))"
    r"(?:\s+uhr)?\b",
    re.IGNORECASE,
)
# One-shot dates belong to the calendar/reminder routes.
_ONE_SHOT_RE = re.compile(
    r"\b(?:heute|übermorgen|(?<!jeden\s)morgen(?!s)|in\s+\S+\s+(?:sekunden?|minuten?|stunden?|tagen?))\b",
    re.IGNORECASE,
)


def _weekdays_for(match: re.Match[str]) -> tuple[str, ...]:
    word = (match.group("one") or match.group("plural") or match.group("group") or "").casefold()
    if word in {"wochenende", "wochenenden"}:
        word = "wochenende"
    elif word in {"werktagen", "werktags"}:
        word = "werktags"
    return _WEEKDAYS.get(word, ())


def _segment_temporal(text: str, action_ok: ActionCheck) -> EventActionFrame | None:
    """"Benachrichtige mich bei Sonnenuntergang", "Um 22 Uhr schließe den
    Rollladen": a temporal phrase is the event, the rest the action.

    A bare clock time ("um 22 Uhr") is a recurring trigger for device
    actions (the established meaning); for a notification it additionally
    needs an explicit recurrence ("jeden Tag"), otherwise it is a one-shot
    reminder handled by the reminder route.
    """
    if _ONE_SHOT_RE.search(text):
        return None
    spans: list[tuple[int, int]] = []
    temporal: TemporalEvent | None = None
    sun = _SUN_SPAN_RE.search(text)
    weekday = _WEEKDAY_RECUR_RE.search(text)
    weekdays = _weekdays_for(weekday) if weekday is not None else ()
    if weekday is not None:
        spans.append((weekday.start(), weekday.end()))
    recurring_given = weekday is not None
    if sun is not None:
        offset: int | None = None
        if sun.group("amount") is not None:
            raw = sun.group("amount")
            amount = int(raw) if raw.isdigit() else german_number(raw)
            if amount is None:
                return None
            offset = -amount if sun.group("rel").casefold() == "vor" else amount
        event = SunEvent.SUNSET if sun.group("sun").casefold() == "sonnenuntergang" else SunEvent.SUNRISE
        temporal = TemporalEvent(sun_event=event, offset_minutes=offset, weekdays=weekdays)
        spans.append((sun.start(), sun.end()))
        recurring_given = True
    else:
        clock = _CLOCK_RE.search(text)
        if clock is None:
            return None
        recurring = _RECURRING_RE.search(text)
        raw_hour = clock.group("hour")
        hour = int(raw_hour) if raw_hour.isdigit() else german_number(raw_hour)
        minute = int(clock.group("minute") or 0)
        if hour is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        if recurring is not None:
            recurring_given = True
            spans.append((recurring.start(), recurring.end()))
            if recurring.group(0).casefold() == "jeden abend" and hour < 12:
                hour += 12
        spans.append((clock.start(), clock.end()))
        temporal = TemporalEvent(hour=hour, minute=minute, weekdays=weekdays)
    remainder = text
    for start, end in sorted(spans, reverse=True):
        remainder = remainder[:start] + " " + remainder[end:]
    remainder = re.sub(r"\s+", " ", remainder).strip(" ,")
    if not remainder:
        return None
    notification = is_notification_text(remainder)
    if notification and not recurring_given:
        return None
    if not notification and not action_ok(remainder):
        return None
    event_text = " ".join(text[start:end] for start, end in sorted(spans))
    return EventActionFrame(remainder, event_text, "", ClauseOrder.ACTION_FIRST, temporal)


def _segment_implicit_then(text: str, action_ok: ActionCheck) -> EventActionFrame | None:
    """"Terrassentür auf, dann Nachricht an mich." - "dann" marks the consequence."""
    match = re.search(r",\s*dann\s+", text, re.IGNORECASE)
    if match is None:
        return None
    event_text = text[:match.start()].strip(" ,")
    rest = text[match.end():].strip(" ,")
    if not event_text or not rest or not action_ok(rest):
        return None
    return EventActionFrame(rest, event_text, "wenn", ClauseOrder.EVENT_FIRST)


# --- semantic roles of the event clause ------------------------------------------


class ValueUnit(Enum):
    PERCENT = auto()
    DEGREE = auto()
    NONE = auto()
    # Power and energy (7.9 W4): kept apart - "10 kWh" is never "10 kW".
    WATT = auto()
    KILOWATT = auto()
    WATT_HOUR = auto()
    KILOWATT_HOUR = auto()


@dataclass(frozen=True)
class ConditionSpan:
    """A source span the established condition parser must read."""

    text: str
    negated: bool = False
    # A recurring weekday phrase ("an Werktagen") is read structurally.
    weekdays: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventRoles:
    """Word-order independent roles of one event clause."""

    source: str
    subject_words: tuple[str, ...] = ()
    comparator: NumericComparator | None = None
    value: float | None = None
    unit: ValueUnit | None = None
    half: bool = False
    state: SemanticState | None = None
    motion: bool = False
    # "jemand ist im Büro" / "niemand mehr im Schlafzimmer" (7.8 B5): room
    # presence, read by the room's presence or motion detector.
    occupancy: bool = False
    full_travel: bool = False  # "ganz/komplett offen" -> a state, not a percentage
    direction: TravelDirection | None = None
    for_seconds: int | None = None
    unsupported: str | None = None  # relative change / rate - understood, not buildable
    presence: PresenceEvent | None = None
    conditions: tuple[ConditionSpan, ...] = field(default_factory=tuple)
    # How the subject was obtained from a monitored object (7.8.3):
    # "es"/"er"/"sie" (agreement is checked in grounding), "member" for
    # "eins/eines davon" and a prohibition's "kein" (any member of the set).
    reference: str | None = None
    # "offen ist/steht/bleibt" names a lasting state, "geöffnet wird" or
    # "aufgeht" a moment.  A state joined with another state ("und niemand
    # zuhause ist") holds whenever both are true (7.8.3).
    stative: bool = False
    # Inactivity (7.9 W2): "wenn sich im Flur 12 Stunden nichts bewegt",
    # "wenn die Haustür zwei Tage nicht geöffnet wurde".  ``absent`` is the
    # state that did *not* occur (motion ON, door OPEN); ``state`` is then
    # its rest state and ``for_seconds`` the spoken span.  ``until`` is a
    # check time instead of a span ("bis 10 Uhr keine Bewegung im Bad"),
    # ``agent`` a person named as the one who should have moved (only
    # motion is observable, the preview says so).
    absent: SemanticState | None = None
    until: tuple[int, int] | None = None
    agent: str | None = None
    # A change by an amount ("um 3 Grad innerhalb einer Stunde", 7.9 W3).
    change: "RelativeChange | None" = None

    @property
    def numeric(self) -> bool:
        return self.value is not None


# --- reference to a monitored object (7.8.3) -----------------------------------------


@dataclass(frozen=True)
class EventReference:
    """The antecedent noun phrase a monitoring frame introduced.

    ``words`` is the unchanged source noun phrase ("die Fenster"); ``member``
    says the event is about any one of it even without a partitive word (a
    prohibition "dass kein Fenster ..." is violated by any window).
    """

    words: tuple[str, ...]
    member: bool = False


# Personal pronouns in subject position.  Agreement with the antecedent's
# grammatical gender is checked where the noun is known (grounding).
PERSONAL_ANAPHORS = frozenset({"es", "er", "sie"})
_DEMONSTRATIVE_ANAPHORS = frozenset({"dieses", "diese", "dieser", "dies", "das"})
# "eins", "eines davon", "irgendeins von ihnen", "welches": one member.
_MEMBER_HEADS = frozenset({
    "eins", "eines", "einer", "eine", "irgendeins", "irgendeines", "irgendeiner",
    "irgendeine", "welches", "welcher", "welche", "jedes", "jeder", "jede",
})
_MEMBER_TAILS = frozenset({"davon", "von", "ihnen", "denen", "den", "der", "dieser", "diesen"})


def reference_kind(subject_words: tuple[str, ...]) -> str | None:
    """How a subject refers back, or ``None`` when it names something itself.

    Only pure reference material counts: "es", "eins davon", or no subject
    at all.  A subject with a noun of its own ("das Tor") is never replaced.
    """
    keys = [word.casefold().strip(",.;:!?") for word in subject_words]
    keys = [key for key in keys if key]
    if not keys:
        return "empty"
    if len(keys) == 1 and keys[0] in PERSONAL_ANAPHORS:
        return keys[0]
    if len(keys) == 1 and keys[0] in _DEMONSTRATIVE_ANAPHORS:
        return "demonstrative"
    if keys[0] in _MEMBER_HEADS and all(key in _MEMBER_TAILS for key in keys[1:]):
        return "member"
    return None


def resolve_reference(roles: EventRoles, reference: EventReference | None) -> EventRoles:
    """Bind a pronoun, partitive or missing subject to the monitored object.

    Deterministic and structural: the antecedent's own source words become
    the subject; nothing is guessed when there is no antecedent or when the
    event names a subject of its own.
    """
    if reference is None or roles.presence is not None:
        return roles
    kind = reference_kind(roles.subject_words)
    if kind is None:
        # "dass kein Fenster offen bleibt": the prohibition's own noun, any member.
        return replace(roles, reference="member") if reference.member else roles
    if not reference.words:
        return roles
    if reference.member and kind in {"empty", "demonstrative"}:
        kind = "member"
    return replace(roles, subject_words=reference.words, reference=kind)


_ABOVE_WORDS = (
    "auf über", "mehr als", "höher als", "heller als", "wärmer als", "größer als",
    "oberhalb von", "über",
)
_BELOW_WORDS = (
    "auf unter", "weniger als", "niedriger als", "dunkler als", "kälter als",
    "kleiner als", "unterhalb von", "unter",
)
_AT_LEAST_WORDS = ("mindestens", "wenigstens")
_AT_MOST_WORDS = ("höchstens", "maximal", "nicht mehr als")

_NUMBER_TOKEN_RE = re.compile(r"^[-−]?\d+(?:[.,]\d+)?$")
_UNIT_WORDS = {
    "prozent": ValueUnit.PERCENT, "%": ValueUnit.PERCENT, "grad": ValueUnit.DEGREE,
    "watt": ValueUnit.WATT, "w": ValueUnit.WATT,
    "kilowatt": ValueUnit.KILOWATT, "kw": ValueUnit.KILOWATT,
    "wattstunden": ValueUnit.WATT_HOUR, "wattstunde": ValueUnit.WATT_HOUR, "wh": ValueUnit.WATT_HOUR,
    "kilowattstunden": ValueUnit.KILOWATT_HOUR, "kilowattstunde": ValueUnit.KILOWATT_HOUR,
    "kwh": ValueUnit.KILOWATT_HOUR,
}
# A counting period for energy ("heute", "diese Woche", 7.9 W4): only a meter
# that restarts with that period can answer it.
METER_PERIOD_WORDS = {"heute": "daily", "täglich": "daily", "woche": "weekly", "monat": "monthly"}
_ARTICLE_NUMBERS = frozenset({"ein", "eine", "eins", "einer", "einen", "einem"})

_RELATIVE_CHANGE_RE = re.compile(
    r"\bum\s+\S+\s+(?:grad|prozent|%)\b"
    r"|\b\S+\s+(?:grad|prozent)\s+(?:wärmer|kälter|mehr|weniger|heller|dunkler)\b"
    r"|\bweitere\s+\S+\s+(?:prozent|grad)\b"
    r"|\binnerhalb\s+(?:von|einer|eines|der)\b"
    r"|\b(?:schneller|langsamer)\s+als\b",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    # Duration modifiers stack: "seit mehr als", "schon länger als" (7.8.3).
    r"(?:\b(?:seit|länger\s+als|mehr\s+als|über|mindestens|für|schon)\s+)*"
    r"(?:(?P<number>-?\d+|[a-zäöüß]+)\s+|(?P<half>eine\s+halbe|einer\s+halben)\s+)"
    r"(?P<unit>sekunden?|minuten?|stunden?|tagen?|tage|tag)\b(?:\s+lang)?",
    re.IGNORECASE,
)
_DOWN_RE = re.compile(
    r"\b(?:beim\s+)?(?:herunter|runter|hinunter|nach\s+unten\s+)"
    r"(?:fahren|gefahren|fährt|fahrt)\b",
    re.IGNORECASE,
)
_UP_RE = re.compile(
    r"\b(?:beim\s+)?(?:hoch|herauf|rauf|hinauf|nach\s+oben\s+)(?:fahren|gefahren|fährt|fahrt)\b",
    re.IGNORECASE,
)
_TIME_CONDITION_RE = re.compile(
    r"\b(?P<rel>nach|vor)\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s+uhr\b", re.IGNORECASE
)
_CONDITION_SPLITS: tuple[tuple[re.Pattern[str], bool], ...] = (
    (re.compile(r"\s*,?\s+aber\s+nur\s+(?:wenn|falls|sofern)\s+", re.IGNORECASE), False),
    (re.compile(r"\s*,?\s+(?:und\s+)?nur\s+(?:wenn|falls|sofern)\s+", re.IGNORECASE), False),
    (re.compile(r"\s*,?\s+außer\s+(?:wenn|falls)\s+", re.IGNORECASE), True),
    (re.compile(r"\s*,?\s+während\s+", re.IGNORECASE), False),
    (re.compile(r"\s*,?\s+solange\s+", re.IGNORECASE), False),
    (re.compile(r"\s+und\s+", re.IGNORECASE), False),
)

_STATE_WORDS: dict[str, SemanticState] = {
    **dict.fromkeys(
        ("aufgeht", "aufgegangen", "geöffnet", "öffnet", "offen", "aufgemacht", "aufmacht",
         "aufgeht?"),
        SemanticState.OPEN,
    ),
    **dict.fromkeys(
        ("zugeht", "zugegangen", "geschlossen", "schließt", "zugemacht", "zumacht"),
        SemanticState.CLOSED,
    ),
    **dict.fromkeys(
        ("angeht", "angegangen", "eingeschaltet", "einschaltet", "angeschaltet", "anschaltet",
         "angemacht", "anmacht", "anspringt", "angesprungen"),
        SemanticState.ON,
    ),
    **dict.fromkeys(
        ("ausgeht", "ausgegangen", "ausgeschaltet", "ausschaltet", "ausgemacht", "ausmacht"),
        SemanticState.OFF,
    ),
}
# Particles that are a state only in predicate position ("auf ist", "an bleibt").
_PARTICLE_STATES: dict[str, SemanticState] = {
    "auf": SemanticState.OPEN, "zu": SemanticState.CLOSED,
    "an": SemanticState.ON, "aus": SemanticState.OFF, "ein": SemanticState.ON,
}
_COPULAS = frozenset({
    "ist", "sind", "steht", "stehen", "wird", "werden", "geht", "gehen", "bleibt",
    "war", "hat", "ist?",
})
_MOTION_VERBS = frozenset({
    "erkannt", "registriert", "gemeldet", "erfasst", "festgestellt", "erkennt",
    "registriert", "meldet", "erfasst", "feststellt", "bemerkt",
})
_FULL_TRAVEL = frozenset({"ganz", "komplett", "vollständig", "voll"})
_UP_POSITION_WORDS = frozenset({"oben"})
_DOWN_POSITION_WORDS = frozenset({"unten"})
_HALF_WORDS = frozenset({"halb", "hälfte", "halber", "halbe"})
# Verbal and function material that carries no subject meaning once the
# predicate role has been read.
_PREDICATE_WORDS = frozenset({
    "erreicht", "erreichen", "erreichte", "erreichst", "hat", "haben", "ist", "sind",
    "steht", "stehen", "liegt", "liegen", "beträgt", "betragen", "steigt", "steigen",
    "fällt", "fallen", "sinkt", "sinken", "gefahren", "fährt", "läuft", "laufen",
    "gedimmt", "gestellt", "eingestellt", "wird", "werden", "worden", "angekommen",
    "ankommt", "geht", "gehen", "zeigt", "misst", "bei", "auf", "mit", "noch", "schon",
    "jetzt", "gerade", "irgendwann", "dann", "bitte", "wieder", "wirklich", "mal",
    "jemand", "einmal", "helligkeit", "position", "gesunken", "gestiegen", "gefallen",
    "geworden", "dreht", "rotiert", "hochgefahren", "heruntergefahren", "runtergefahren",
    "offen", "geöffnet", "zu", "geschlossen", "prozent", "grad", "%", "die", "der",
    "das", "hälfte", "halb", "unten", "oben", "bleibt", "war", "kommt", "etwa",
    "ungefähr", "circa", "genau", "so", "etwa", "hochfahren", "herunterfahren", "runterfahren",
    "beim", "angelangt", "gelangt", "mehr", "als", "über", "unter", "hell",
    "gedimmt", "läuft", "erkannt", "höhe", "warm", "kalt", "heiß", "heiss", "irgendwo", "klettert", "rutscht", "wandert",
})
# Kept as subject material even though listed above.
_SUBJECT_KEEP = frozenset({"die", "der", "das"})


def _numeric_value(word: str) -> float | None:
    cleaned = word.replace("−", "-").rstrip("%")
    if _NUMBER_TOKEN_RE.match(cleaned):
        return float(cleaned.replace(",", "."))
    if cleaned.casefold() in _ARTICLE_NUMBERS:
        return None
    number = german_number(cleaned)
    return float(number) if number is not None else None


def _duration_seconds(match: re.Match[str]) -> int | None:
    unit = match.group("unit").casefold()
    multiplier = (
        1 if unit.startswith("sekunde") else 60 if unit.startswith("minute")
        else 86400 if unit.startswith("tag") else 3600
    )
    if match.group("half") is not None:
        return multiplier // 2 if multiplier >= 60 else None
    raw = match.group("number")
    if raw is None:
        return None
    if raw.casefold() in {"eine", "einer", "einem", "ein"}:
        amount = 1
    elif raw.lstrip("-").isdigit():
        amount = int(raw)
    else:
        number = german_number(raw)
        if number is None:
            return None
        amount = number
    return amount * multiplier if amount > 0 else None


# A time window qualifying the event ("wenn sie nachts geöffnet wird",
# "zwischen 22 und 6 Uhr") is a condition read by the established condition
# grammar, never part of the subject (7.8.3).
_TIME_WINDOW_CONDITION_RE = re.compile(
    r"\b(?:nur\s+)?(?:nachts|abends|morgens|vormittags|mittags|nachmittags)\b"
    r"|\bzwischen\s+\d{1,2}(?::\d{2})?\s+(?:uhr\s+)?und\s+\d{1,2}(?::\d{2})?\s+uhr\b",
    re.IGNORECASE,
)


def _extract_conditions(text: str) -> tuple[str, tuple[ConditionSpan, ...]]:
    """Pull time/weekday prepositional conditions out of the event clause."""
    spans: list[ConditionSpan] = []
    window = _TIME_WINDOW_CONDITION_RE.search(text)
    if window is not None:
        spans.append(ConditionSpan(window.group(0)))
        text = (text[:window.start()] + " " + text[window.end():]).strip()
    time_match = _TIME_CONDITION_RE.search(text)
    if time_match is not None and not re.match(r"\s*(?:um)\b", text[: time_match.start()][-4:]):
        spans.append(ConditionSpan(time_match.group(0)))
        text = (text[:time_match.start()] + text[time_match.end():]).strip()
    weekday = _WEEKDAY_RECUR_RE.search(text)
    if weekday is not None and _weekdays_for(weekday):
        spans.append(ConditionSpan(weekday.group(0), weekdays=_weekdays_for(weekday)))
        text = (text[:weekday.start()] + text[weekday.end():]).strip()
    return re.sub(r"\s+", " ", text), tuple(spans)


def condition_split_candidates(text: str) -> tuple[tuple[str, ConditionSpan], ...]:
    """Every "<event> <marker> <condition>" decomposition, first marker first."""
    candidates: list[tuple[str, ConditionSpan]] = []
    for pattern, negated in _CONDITION_SPLITS:
        for match in pattern.finditer(text):
            left = text[:match.start()].strip(" ,")
            right = text[match.end():].strip(" ,.")
            if left and right:
                candidates.append((left, ConditionSpan(right, negated)))
    return tuple(candidates)


_PLAIN_AND_RE = re.compile(r"\s+und\s+", re.IGNORECASE)


def and_reversed_candidates(text: str) -> tuple[tuple[str, str], ...]:
    """Every "<condition> und <event>" decomposition (condition spoken first)."""
    candidates: list[tuple[str, str]] = []
    for match in _PLAIN_AND_RE.finditer(text):
        left = text[:match.start()].strip(" ,")
        right = text[match.end():].strip(" ,.")
        if left and right:
            candidates.append((left, right))
    return tuple(candidates)


def read_event_roles(event_text: str) -> EventRoles:
    """Decompose one event clause into semantic roles.

    Unknown material is never discarded silently: whatever is not
    recognized as predicate, value or filler stays in ``subject_words`` and
    grounding has to explain it, or the reading fails.
    """
    source = re.sub(
        r"^(?:(?:immer|nur|aber)\s+)*(?:wenn|sobald|falls|sofern)\s+", "",
        event_text.strip(" ,.!?"), flags=re.IGNORECASE,
    )
    text, conditions = _extract_conditions(source)
    if _RELATIVE_CHANGE_RE.search(text):
        change = read_relative_change(text)
        if change is not None:
            reading, subject_words = change
            return EventRoles(
                source, subject_words=subject_words, unit=reading.unit, change=reading,
                conditions=conditions,
            )
        return EventRoles(source, unsupported="relative_change", conditions=conditions)

    presence = _presence(text)
    if presence is not None:
        event, span = presence
        rest = (text[:span[0]] + " " + text[span[1]:]).split()
        subject_words = tuple(
            word.strip(",.;:!?") for word in rest
            if word.strip(",.;:!?").casefold() not in _PRESENCE_FILLERS
        )
        return EventRoles(source, subject_words=subject_words, presence=event, conditions=conditions)

    for_seconds: int | None = None
    duration = _DURATION_RE.search(text)
    if duration is not None:
        seconds = _duration_seconds(duration)
        if seconds is not None:
            for_seconds = seconds
            text = (text[:duration.start()] + " " + text[duration.end():]).strip()

    direction: TravelDirection | None = None
    if _DOWN_RE.search(text):
        direction = TravelDirection.DOWN
    elif _UP_RE.search(text):
        direction = TravelDirection.UP

    words = [word.strip(",.;:!?") for word in text.split()]
    keys = [word.casefold() for word in words]
    consumed: set[int] = set()

    # VALUE / UNIT / COMPARATOR ------------------------------------------------
    value: float | None = None
    unit: ValueUnit | None = None
    comparator: NumericComparator | None = None
    half = False
    for index, key in enumerate(keys):
        number = _numeric_value(words[index])
        start = index
        if number is None and key == "minus" and index + 1 < len(words):
            follow = _numeric_value(words[index + 1])
            if follow is not None:
                consumed.add(index)
                index += 1
                number = -follow
        if number is None:
            continue
        next_key = keys[index + 1] if index + 1 < len(keys) else ""
        next_unit = _UNIT_WORDS.get(next_key)
        preceded = " ".join(keys[max(0, start - 3):start])
        if next_unit is None and words[index].endswith("%"):
            next_unit = ValueUnit.PERCENT
        # A bare number word is a value only in a value slot ("bei fünfzig").
        if next_unit is None and not re.search(
            r"\b(?:bei|auf|über|unter|als|mindestens|höchstens|maximal|die)\s*$", preceded
        ) and not re.search(r"(?:erreicht|beträgt|hat)", " ".join(keys[index + 1:index + 3])):
            continue
        value = number
        unit = next_unit or ValueUnit.NONE
        consumed.add(index)
        if next_unit is not None and not words[index].endswith("%"):
            consumed.add(index + 1)
        comparator = _comparator(preceded)
        break
    if value is None:
        for index, key in enumerate(keys):
            if key in _HALF_WORDS:
                value, unit, half = 50.0, ValueUnit.PERCENT, True
                comparator = _comparator(" ".join(keys[max(0, index - 3):index]))
                consumed.add(index)
                break

    # DIRECTION words are verbal material, as are comparator words.
    # STATE ---------------------------------------------------------------------
    state: SemanticState | None = None
    motion = False
    occupancy = False
    full_travel = False
    if value is None:
        for index, key in enumerate(keys):
            if key in _STATE_WORDS:
                state = _STATE_WORDS[key]
                consumed.add(index)
                break
            follows = keys[index + 1] if index + 1 < len(keys) else None
            if key in _PARTICLE_STATES and (follows is None or follows in _COPULAS):
                state = _PARTICLE_STATES[key]
                consumed.add(index)
                break
        if state is None:
            if any(key in _UP_POSITION_WORDS for key in keys) and any(k in _FULL_TRAVEL for k in keys):
                state = SemanticState.OPEN
            elif any(key in _DOWN_POSITION_WORDS for key in keys) and any(k in _FULL_TRAVEL for k in keys):
                state = SemanticState.CLOSED
        if state is None and len(keys) >= 2 and keys[-1] in _STATIVE_COPULAS and keys[-2] in (
            _UP_POSITION_WORDS | _DOWN_POSITION_WORDS
        ):
            # "wenn alle Rollläden unten sind": the position word as the
            # predicate right before the copula is the end position (7.9 W1);
            # elsewhere ("die Fenster oben") it stays a place.
            state = SemanticState.OPEN if keys[-2] in _UP_POSITION_WORDS else SemanticState.CLOSED
            consumed.add(len(keys) - 2)
        full_travel = state is not None and any(key in _FULL_TRAVEL for key in keys)
        if "bewegung" in keys and any(
            key in _MOTION_VERBS for key in keys
        ):
            motion, state = True, SemanticState.ON
        elif "bewegt" in keys and "sich" in keys:
            motion, state = True, SemanticState.ON
        elif any(key in _DETECTOR_EVENT_VERBS for key in keys):
            motion, state = True, SemanticState.ON
        elif any(key.startswith(("bewegungsmelder", "bewegungssensor")) for key in keys) and any(
            key in _MOTION_VERBS for key in keys
        ):
            motion, state = True, SemanticState.ON
        if state is None and "jemand" in keys and set(keys) & _PRESENT_VERBS:
            motion, occupancy, state = True, True, SemanticState.ON
        elif state is None and set(keys) & {"niemand", "keiner"} and set(keys) & _PRESENT_VERBS:
            motion, occupancy, state = True, True, SemanticState.OFF

    subject: list[str] = []
    for index, word in enumerate(words):
        key = keys[index]
        if index in consumed or not word:
            continue
        if key in _FULL_TRAVEL or key in _MOTION_VERBS or key in {
            "sich", "etwas", "bewegt", "auslöst", "ausgelöst", "anschlägt", "reagiert",
        } or key in _DETECTOR_EVENT_VERBS or (motion and key == "bewegung") or (
            occupancy and key in _PRESENT_VERBS | {"jemand", "niemand", "keiner", "mehr"}
        ):
            if key == "etwas":
                subject.append(word)
            continue
        if _is_comparator_word(key, keys, index):
            continue
        if (
            key in _UP_POSITION_WORDS | _DOWN_POSITION_WORDS
            and state is not None and value is None and not full_travel and not motion
        ):
            # "wenn oben kein Fenster mehr offen ist": the state comes from
            # another word, so "oben/unten" is the place (7.9 W1).
            subject.append(word)
            continue
        if key in _PREDICATE_WORDS and key not in _SUBJECT_KEEP:
            continue
        if key in _STATE_WORDS:
            continue
        subject.append(word)
    subject_words = _trim_articles(tuple(subject))
    absent: SemanticState | None = None
    until: tuple[int, int] | None = None
    agent: str | None = None
    negated = any(key in _ABSENCE_WORDS for key in keys)
    if negated and value is None:
        until = _until_time(text)
        if "bewegung" in keys and state is None:
            motion, state = True, SemanticState.ON
        running = state is None and any(key in _RUN_VERBS for key in keys)
        if running:
            # "wenn die Waschmaschine heute nicht lief": an appliance run
            # that did not happen - grounding asks "bis wann" if unsaid.
            state = SemanticState.ON
        if (for_seconds is not None or until is not None or running) and state in _COMPLEMENT_STATES:
            # Something did not happen for a span / until a time: the
            # entity stayed in the opposite (rest) state (7.9 W2).
            absent, state = state, _COMPLEMENT_STATES[state]
            subject_words, agent = _absence_subject(subject_words)
    if state is not None and value is None and not motion and absent is None:
        subject_words, state = _aggregate_reading(subject_words, state)
    return EventRoles(
        source=source,
        subject_words=subject_words,
        comparator=comparator if value is not None else None,
        value=value,
        unit=unit,
        half=half,
        state=state,
        motion=motion,
        occupancy=occupancy,
        full_travel=full_travel,
        direction=direction,
        for_seconds=for_seconds,
        conditions=conditions,
        stative=(
            state is not None and value is None and not motion and absent is None
            and _is_stative(keys)
        ),
        absent=absent,
        until=until,
        agent=agent,
    )


# --- changes by an amount (7.9 W3) ---------------------------------------------------


class ChangeSense(Enum):
    FALL = auto()
    RISE = auto()
    EITHER = auto()


@dataclass(frozen=True)
class RelativeChange:
    """"um 3 Grad innerhalb einer Stunde fällt": amount, unit, sense and the
    window (``None`` when unsaid - never assumed, grounding asks)."""

    delta: float
    unit: ValueUnit
    sense: ChangeSense
    window_seconds: int | None


_CHANGE_AMOUNT_RE = re.compile(
    r"\bum\s+(?:(?:mindestens|mehr\s+als|über)\s+)?(?P<amount>\d+(?:[.,]\d+)?|[a-zäöüß]+)\s+"
    r"(?P<unit>grad|prozent|%)\b",
    re.IGNORECASE,
)
_CHANGE_WINDOW_RE = re.compile(
    r"\b(?:innerhalb|binnen)\s+(?:von\s+)?(?P<count>\d+|[a-zäöüß]+)\s+(?P<unit>minuten?|stunden?)\b"
    r"|\bin\s+(?:(?:weniger\s+als|unter)\s+)?(?P<count2>\d+|[a-zäöüß]+)\s+(?P<unit2>minuten?|stunden?)\b",
    re.IGNORECASE,
)
_FALL_VERBS = frozenset({"fällt", "sinkt", "abfällt", "absinkt", "runtergeht", "abnimmt", "fallen", "sinken"})
_RISE_VERBS = frozenset({"steigt", "ansteigt", "zunimmt", "hochgeht", "steigen", "klettert"})
_EITHER_VERBS = frozenset({"ändert", "verändert", "schwankt", "springt"})
_CHANGE_FILLERS = frozenset({"sich", "um", "mindestens", "mehr", "als", "über", "plötzlich", "schnell", "stark"})


def _amount(raw: str) -> float | None:
    if raw.replace(",", "").replace(".", "").isdigit():
        return float(raw.replace(",", "."))
    if raw.casefold() in {"ein", "eine", "einen", "einem", "einer"}:
        return 1.0
    number = german_number(raw)
    return float(number) if number is not None else None


def read_relative_change(text: str) -> tuple[RelativeChange, tuple[str, ...]] | None:
    """The change and the remaining subject words, or ``None``."""
    amount = _CHANGE_AMOUNT_RE.search(text)
    if amount is None:
        return None
    delta = _amount(amount.group("amount"))
    if delta is None or delta <= 0:
        return None
    unit = _UNIT_WORDS[amount.group("unit").casefold()]
    window_seconds: int | None = None
    rest = text[:amount.start()] + " " + text[amount.end():]
    window = _CHANGE_WINDOW_RE.search(rest)
    if window is not None:
        count_raw = window.group("count") or window.group("count2")
        unit_raw = (window.group("unit") or window.group("unit2")).casefold()
        count = 1.0 if count_raw.casefold() in {"einer", "einem", "eine", "ein"} else _amount(count_raw)
        if count is None or count <= 0:
            return None
        window_seconds = int(count * (3600 if unit_raw.startswith("stunde") else 60))
        rest = rest[:window.start()] + " " + rest[window.end():]
    words = [word.strip(",.;:!?") for word in rest.split()]
    keys = {word.casefold() for word in words}
    if keys & _FALL_VERBS:
        sense = ChangeSense.FALL
    elif keys & _RISE_VERBS:
        sense = ChangeSense.RISE
    elif keys & _EITHER_VERBS:
        sense = ChangeSense.EITHER
    else:
        return None
    subject = tuple(
        word for word in words
        if word and word.casefold() not in _FALL_VERBS | _RISE_VERBS | _EITHER_VERBS | _CHANGE_FILLERS
    )
    return RelativeChange(delta, unit, sense, window_seconds), _trim_articles(subject)


# --- inactivity (7.9 W2) ------------------------------------------------------------

_ABSENCE_WORDS = frozenset({"nicht", "nichts", "keine", "kein", "keinerlei"})
_RUN_VERBS = frozenset({"lief", "läuft", "gelaufen", "lief?", "lief.", "lief,"})
_ABSENCE_FILLERS = frozenset({
    "nicht", "nichts", "keine", "kein", "keinerlei", "lang", "lange", "hatte", "hat",
    "gab", "gibt", "war", "wurde", "worden", "mehr", "bis", "uhr", "sich", "bewegung",
    "lief", "läuft", "gelaufen", "ist", "heute",
})
_UNTIL_RE = re.compile(r"\bbis\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*uhr\b", re.IGNORECASE)


def _until_time(text: str) -> tuple[int, int] | None:
    match = _UNTIL_RE.search(text)
    if match is None:
        return None
    hour, minute = int(match.group("hour")), int(match.group("minute") or 0)
    return (hour, minute) if hour < 24 and minute < 60 else None


def _absence_subject(words: tuple[str, ...]) -> tuple[tuple[str, ...], str | None]:
    """Drop the negation and its verbal material; a leading name before the
    place ("Oma ... im Bad") is the agent, not a device."""
    kept: list[str] = []
    for word in words:
        key = word.casefold()
        if key in _ABSENCE_FILLERS or _UNTIL_RE.fullmatch(key) or key.isdigit():
            continue
        kept.append(word)
    agent: str | None = None
    if (
        len(kept) >= 2 and kept[0][:1].isupper()
        and kept[1].casefold() in {"im", "in", "am", "auf"}
    ):
        agent, kept = kept[0], kept[1:]
    return _trim_articles(tuple(kept)), agent


# --- the whole household away (7.9 W1) ------------------------------------------------

# "niemand (mehr) zuhause ist", "keiner daheim ist", "alle weg sind", "alle
# aus dem Haus sind": a negated or universal quantifier over the people of
# the house plus an absence predicate - one meaning, "niemand zuhause".
# Read from closed word classes, never from whole sentences.
_NOBODY_QUANTIFIERS = frozenset({"niemand", "keiner"})
_EVERYBODY_QUANTIFIERS = frozenset({"alle"})
_HOME_PREDICATES = frozenset({"zuhause", "zu hause", "daheim", "im haus"})
_AWAY_PREDICATES = frozenset({
    "weg", "fort", "unterwegs", "aus dem haus", "außer haus", "nicht zuhause",
    "nicht zu hause", "nicht daheim", "das haus verlassen", "verlassen",
    "weggegangen", "gegangen",
})
_PEOPLE_FILLERS = frozenset({"noch", "schon", "dann", "gerade", "mehr", "wir", "jetzt"})
_PRESENCE_COPULAS = frozenset({"ist", "sind", "haben", "hat"})


def is_nobody_home_phrase(text: str) -> bool:
    keys = [
        word.casefold() for word in text.strip(" ,.!?").split()
        if word.casefold() not in _PEOPLE_FILLERS
    ]
    if len(keys) < 3 or keys[-1] not in _PRESENCE_COPULAS:
        return False
    quantifier, predicate = keys[0], " ".join(keys[1:-1])
    if quantifier in _NOBODY_QUANTIFIERS:
        return predicate in _HOME_PREDICATES
    return quantifier in _EVERYBODY_QUANTIFIERS and predicate in _AWAY_PREDICATES


# --- whole-set states (7.9 W1) ------------------------------------------------------

_NEGATIVE_DETERMINERS = frozenset({"kein", "keine", "keiner", "keines", "keinen", "keinem"})
_LAST_WORDS = frozenset({"letzte", "letzter", "letztes", "letzten"})
_COMPLEMENT_STATES = {
    SemanticState.ON: SemanticState.OFF, SemanticState.OFF: SemanticState.ON,
    SemanticState.OPEN: SemanticState.CLOSED, SemanticState.CLOSED: SemanticState.OPEN,
}


def _aggregate_reading(
    words: tuple[str, ...], state: SemanticState
) -> tuple[tuple[str, ...], SemanticState]:
    """"kein Licht (mehr) an" = "alle Lichter aus"; "das letzte Fenster zu" =
    "alle Fenster zu".  The negation belongs to the quantifier and inverts
    the state - it never becomes a negated command.  The result carries
    "alle", which grounding reads as the whole-set quantifier."""
    keys = [word.casefold() for word in words]
    if any(key in _NEGATIVE_DETERMINERS for key in keys) and state in _COMPLEMENT_STATES:
        rest = tuple(
            word for word, key in zip(words, keys)
            if key not in _NEGATIVE_DETERMINERS and key != "mehr"
        )
        return ("alle", *rest), _COMPLEMENT_STATES[state]
    if any(key in _LAST_WORDS for key in keys):
        rest = tuple(
            word for index, (word, key) in enumerate(zip(words, keys))
            if key not in _LAST_WORDS
            and not (key in {"der", "die", "das"} and index + 1 < len(keys) and keys[index + 1] in _LAST_WORDS)
        )
        return ("alle", *rest), state
    return words, state


# A predicate adjective or participle with a stative copula ("offen ist",
# "geöffnet sind", "an bleibt"); "wird/werden" and event verbs are moments.
_STATIVE_COPULAS = frozenset({"ist", "sind", "steht", "stehen", "bleibt", "bleiben", "ist?"})
_EVENT_AUXILIARIES = frozenset({"wird", "werden", "worden", "wurde", "wurden"})
_STATIVE_PREDICATES = frozenset({
    "offen", "geöffnet", "auf", "zu", "geschlossen", "an", "aus", "ein", "eingeschaltet", "oben", "unten",
    "ausgeschaltet", "angeschaltet",
})


def _is_stative(keys: list[str]) -> bool:
    return (
        bool(set(keys) & _STATIVE_COPULAS)
        and not set(keys) & _EVENT_AUXILIARIES
        and bool(set(keys) & _STATIVE_PREDICATES)
    )


_ARRIVE_RE = re.compile(
    r"\b(?:nach\s+hause|heim|zuhause)\s*(?:komm\w*|gekommen|kehr\w*|ankomm\w*|angekommen|eintreff\w*|eintrifft)\b",
    re.IGNORECASE,
)
_LEAVE_RE = re.compile(
    r"\b(?:das\s+haus|die\s+wohnung)\s+verl(?:ässt|asse|assen|ässt)\b"
    r"|\bweg(?:geh\w*|gegangen|fähr\w*|fahr\w*|gefahren)\b"
    r"|\baus\s+dem\s+haus\s+geh\w*\b"
    r"|\blos(?:fähr\w*|fahr\w*|gefahren)\b"
    # "wenn ich gehe", "wenn Anna geht": intransitive "gehen" closing the
    # clause (verb-final) means leaving; with a separated particle ("auf
    # geht", "aus geht") it is a device state, never presence (7.8.3).
    r"|(?<!\bauf\s)(?<!\bzu\s)(?<!\baus\s)(?<!\ban\s)(?<!\bvor\s)(?<!\bein\s)"
    r"\bgeh(?:e|st|t|en)\s*$",
    re.IGNORECASE,
)
_PRESENT_VERBS = frozenset({
    "ist", "sind", "da", "kommt", "betritt", "reinkommt", "hereinkommt", "rein", "herein",
    "anwesend", "drin", "befindet",
})
_PRESENCE_FILLERS = frozenset({
    "hat", "habe", "hast", "ist", "bin", "bist", "sind", "wieder", "gerade", "dann",
    "irgendwann", "endlich",
})


def _presence(text: str) -> tuple[PresenceEvent, tuple[int, int]] | None:
    arrive = _ARRIVE_RE.search(text)
    leave = _LEAVE_RE.search(text)
    if (arrive is None) == (leave is None):
        return None
    match = arrive or leave
    assert match is not None
    return (PresenceEvent.ARRIVE if arrive else PresenceEvent.LEAVE), (match.start(), match.end())


def _comparator(preceding: str) -> NumericComparator:
    tail = preceding.strip()
    for words, comparator in (
        (_AT_MOST_WORDS, NumericComparator.AT_MOST),
        (_AT_LEAST_WORDS, NumericComparator.AT_LEAST),
        (_ABOVE_WORDS, NumericComparator.ABOVE),
        (_BELOW_WORDS, NumericComparator.BELOW),
    ):
        if any(re.search(rf"\b{re.escape(word)}(?:\s+(?:noch|die|der|das))?$", tail) for word in words):
            return comparator
    return NumericComparator.EQUAL


_COMPARATOR_TOKENS = frozenset({
    "über", "unter", "mehr", "weniger", "höher", "niedriger", "heller", "dunkler",
    "wärmer", "kälter", "größer", "kleiner", "als", "mindestens", "wenigstens",
    "höchstens", "maximal", "oberhalb", "unterhalb", "von",
})


def _is_comparator_word(key: str, keys: list[str], index: int) -> bool:
    if key not in _COMPARATOR_TOKENS:
        return False
    if key == "von":
        return index > 0 and keys[index - 1] in {"oberhalb", "unterhalb"}
    return True


def _trim_articles(words: tuple[str, ...]) -> tuple[str, ...]:
    """Articles left dangling at the end once predicates were removed."""
    trimmed = list(words)
    while trimmed and trimmed[-1].casefold() in {"die", "der", "das", "den", "dem"}:
        trimmed.pop()
    return tuple(trimmed)


__all__ = (
    "ClauseOrder",
    "ConditionSpan",
    "ChangeSense",
    "RelativeChange",
    "read_relative_change",
    "and_reversed_candidates",
    "is_nobody_home_phrase",
    "EventReference",
    "EventRoles",
    "PERSONAL_ANAPHORS",
    "reference_kind",
    "resolve_reference",
    "ActionCheck",
    "EventActionFrame",
    "PreparedText",
    "TemporalEvent",
    "ValueUnit",
    "condition_split_candidates",
    "prepare_automation_text",
    "read_event_roles",
    "ACTION_OPENERS",
    "is_notification_text",
    "only_quoted_connectors",
    "looks_like_device_action",
    "protected_message_spans",
    "segment_event_automation",
)
