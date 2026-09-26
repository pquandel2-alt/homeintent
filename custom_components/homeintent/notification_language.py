"""Shared German notification language (NOTIFY / REMIND / INFORM / TELL).

One compositional reader for the *notification clause* of an utterance -
"schick mir eine Testbenachrichtigung", "kannst du mich benachrichtigen",
"sag mir Bescheid", "erinnere mich daran, den Backofen zu prüfen",
"benachrichtige mich, dass im Wohnzimmer ein Fenster offen ist".  Timing
("in 10 Sekunden") and triggers ("sobald ...") are *not* read here: the
existing relative-time decomposer and the automation clause analysis remove
them first and hand only the notification clause to this module.  Immediate,
delayed, reminder and event-triggered notifications therefore share one
meaning instead of four parsers.

The reader is deliberately structural rather than keyword based:

* a clause needs a notification verb in imperative or modal-infinitive
  position together with a recipient ("mich"/"mir"/"uns"/a name);
* "schicken"/"senden" additionally need a message noun ("Nachricht",
  "Benachrichtigung", ...), "sagen"/"geben" need "Bescheid";
* "sag mir, ob ..." (a query) and "sag mir, dass ..." (no "Bescheid") are
  therefore never notifications.

Message text is inert data.  It is only ever placed into a ``message`` field
by the caller and never interpreted as a command or service.

This module is Home-Assistant-free and fully typed (strict Pyright scope).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .entities import EntitySnapshot
from .nlu.action_model import NotificationRecipientKind
from .nlu.automation_model import (
    NumericComparator,
    PresenceEvent,
    TriggerModel,
    TriggerTarget,
    TriggerType,
)
from .nlu.measurement import MeasurementProperty, TravelDirection
from .nlu.german_morphology import (
    definite_entity_phrase,
    dative_location_phrase,
    sentence_initial,
)
from .nlu.semantic_state import SemanticState

DEFAULT_NOTIFICATION_TITLE = "HomeIntent"
TEST_NOTIFICATION_MESSAGE = "Testbenachrichtigung von HomeIntent."
DEFAULT_NOTIFICATION_MESSAGE = "Benachrichtigung von HomeIntent."
DEFAULT_REMINDER_MESSAGE = "Erinnerung von HomeIntent."
MAX_MESSAGE_LENGTH = 500


@dataclass(frozen=True)
class NotificationClause:
    """The meaning of one notification clause, independent of timing."""

    recipient_kind: NotificationRecipientKind
    recipient_name: str | None = None  # only for EXPLICIT_TARGET
    message: str | None = None  # explicit message, already a main clause
    test: bool = False
    reminder: bool = False

    def resolved_message(self) -> str:
        """Explicit message, else a deterministic default."""
        if self.message is not None:
            return self.message
        if self.test:
            return TEST_NOTIFICATION_MESSAGE
        if self.reminder:
            return DEFAULT_REMINDER_MESSAGE
        return DEFAULT_NOTIFICATION_MESSAGE


# --- lexical layer -----------------------------------------------------------

_MODAL_WRAPPER_RE = re.compile(
    r"^(?:kannst|könntest|koenntest|würdest|wuerdest|magst)\s+du\s+", re.IGNORECASE
)
# Pragmatic particles that never change the meaning of a notification head.
_PARTICLE_RE = re.compile(
    r"\b(?:bitte|mal|doch|kurz|einfach|gleich|sofort|jetzt|nochmal|noch\s+mal|"
    r"vielleicht|eben|eigentlich|dann)\b",
    re.IGNORECASE,
)
_CHANNEL_RE = re.compile(
    r"\b(?:(?:aufs|auf\s+(?:das|mein|dein))\s+(?:handy|iphone|smartphone|telefon)|"
    r"(?:per|über|via)\s+(?:push(?:[\s-]?nachricht)?|benachrichtigung|app|handy)|"
    r"als\s+push(?:[\s-]?(?:nachricht|benachrichtigung))?)\b",
    re.IGNORECASE,
)
_NOUN = r"(?:nachricht|benachrichtigung|meldung|mitteilung|notification|info|warnung)"
_OBJECT = (
    r"(?:(?:eine|die|ne)\s+)?(?P<test>test[\s-]?)?(?:push[\s-]?)?" + _NOUN
)
_RECIPIENT = r"(?P<recipient>[a-zäöüß][\wäöüß-]*)"
_ACCUSATIVE_VERB = r"(?:benachrichtig(?:e|en|er)?|informier(?:e|en|er)?)"
_DATIVE_VERB = r"(?:schick(?:e|en|er)?|send(?:e|en|er)?|mach(?:e)?)"
_BESCHEID_VERB = r"(?:sag(?:e|en)?|gib|geb(?:e|en)?)"

_HEAD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # benachrichtige mich / informiere Philipp
        rf"{_ACCUSATIVE_VERB}\s+{_RECIPIENT}",
        # schick mir eine Testbenachrichtigung
        rf"{_DATIVE_VERB}\s+{_RECIPIENT}\s+{_OBJECT}",
        # schick eine Push-Nachricht an Philipp
        rf"{_DATIVE_VERB}\s+{_OBJECT}\s+an\s+(?:den\s+|die\s+|das\s+)?{_RECIPIENT}",
        # sag mir Bescheid / gib Philipp Bescheid
        rf"{_BESCHEID_VERB}\s+{_RECIPIENT}\s+bescheid",
        # (kannst du) mich benachrichtigen (lassen)
        rf"{_RECIPIENT}\s+(?:benachrichtigen|informieren)(?:\s+lassen)?",
        # (kannst du) mir eine Nachricht schicken
        rf"{_RECIPIENT}\s+{_OBJECT}\s+(?:schicken|senden|zukommen\s+lassen)",
        # (kannst du) mir Bescheid sagen/geben
        rf"{_RECIPIENT}\s+bescheid\s+(?:sagen|geben)",
        # (bitte) eine Nachricht an mich - verbless request
        rf"{_OBJECT}\s+an\s+{_RECIPIENT}",
        # schick mir ne Push / schick mir was
        rf"{_DATIVE_VERB}\s+{_RECIPIENT}\s+(?:(?:eine|ne)\s+)?(?:push|was|etwas)",
        # schick mir (aufs Handy) Bescheid
        rf"{_DATIVE_VERB}\s+{_RECIPIENT}\s+bescheid",
        # two-word addressee, anchored by the message noun/"Bescheid":
        # "schick Onkel Herbert eine Nachricht", "sag Tante Erna Bescheid"
        rf"{_DATIVE_VERB}\s+(?P<recipient>[A-ZÄÖÜ][\wäöüß-]*\s+[A-ZÄÖÜ][\wäöüß-]*)\s+{_OBJECT}",
        rf"{_BESCHEID_VERB}\s+(?P<recipient>[A-ZÄÖÜ][\wäöüß-]*\s+[A-ZÄÖÜ][\wäöüß-]*)\s+bescheid",
        rf"{_ACCUSATIVE_VERB}\s+(?P<recipient>[A-ZÄÖÜ][\wäöüß-]*\s+[A-ZÄÖÜ][\wäöüß-]*)",
    )
)
# First-person wishes: "ich möchte eine Nachricht bekommen", "ich will
# benachrichtigt werden", "ich hätte gern eine Benachrichtigung".  They only
# ever address the speaker.
_SELF_WISH_RE = re.compile(
    r"(?:ich\s+(?:möchte|moechte|will|würde|wuerde|hätte|haette|wäre|waere)"
    r"|(?:möchte|moechte|will|hätte|haette)\s+ich)\s+"
    r"(?:(?:sehr\s+)?(?:gern|gerne)\s+|dankbar\s+für\s+)?"
    r"(?:"
    r"(?:(?:eine|ne|die)\s+)?(?:push[\s-]?)?" + _NOUN + r"(?:\s+(?:bekommen|erhalten|haben))?"
    r"|(?:benachrichtigt|informiert|gewarnt|verständigt)\s+werden"
    r")",
    re.IGNORECASE,
)
# "melde dich (bei mir)", "ping mich (an)", "gib Bescheid"
_SELF_CONTACT_RE = re.compile(
    r"(?:meld(?:e)?\s+dich(?:\s+bei\s+mir)?|ping\s+mich(?:\s+an)?|(?:gib|sag|sage)\s+bescheid)",
    re.IGNORECASE,
)
# "Sag mir" is stripped by ``normalize()`` as a politeness prefix, which
# leaves a bare "Bescheid" that still unambiguously means "tell me".
_BARE_BESCHEID_RE = re.compile(r"bescheid(?:\s+(?:sagen|geben))?", re.IGNORECASE)
_REMINDER_HEAD_RE = re.compile(r"erinner(?:e|n|st)?\s+" + _RECIPIENT, re.IGNORECASE)

# Words that can follow a verb but are never a recipient.
_NOT_A_RECIPIENT = frozenset({
    "eine", "ein", "die", "das", "der", "den", "dem", "ne", "an", "auf", "aufs",
    "per", "mit", "dass", "ob", "es", "bescheid", "test", "push", "nachricht",
    "benachrichtigung", "meldung", "mitteilung", "info", "wenn", "sobald",
    "falls", "was", "wie", "wo", "wann", "warum", "welche", "welcher", "welches",
    "sie", "ihn", "ihm", "ihr", "dir", "dich", "du", "ich", "euch",
})

_EXPLICIT_TEXT_RE = re.compile(
    r"^(?P<head>.+?)\s+mit\s+(?:dem\s+text|der\s+nachricht|dem\s+inhalt|"
    r"folgendem\s+text|folgender\s+nachricht|text)\s*:?\s+(?P<message>.+)$",
    re.IGNORECASE,
)
_COLON_RE = re.compile(r"^(?P<head>[^:]+?)\s*:\s*(?P<message>.+)$")
_QUOTED_RE = re.compile(
    r"^(?P<head>[^„\"“«]+?)\s*,?\s*[„\"“«](?P<message>[^„\"“”«»]+)[“”\"»]$"
)
_DASS_RE = re.compile(r"^(?P<head>.+?)\s*,?\s+dass\s+(?P<content>.+)$", re.IGNORECASE)
_NAMED_MESSAGE_RE = re.compile(
    rf"^(?P<head>{_DATIVE_VERB}\s+{_RECIPIENT})\s+die\s+nachricht\s+(?P<message>.+)$",
    re.IGNORECASE,
)
_REMINDER_TASK_RE = re.compile(
    r"^(?P<head>erinner\w*\s+\S+)\s+daran\s*,?\s+(?P<task>.+?)\s+"
    r"(?:zu\s+(?P<verb>[\wäöüß]+)|(?P<particle>[\wäöüß]+?)zu(?P<stem>[\wäöüß]+en))$",
    re.IGNORECASE,
)
_REMINDER_DARAN_DASS_RE = re.compile(
    r"^(?P<head>erinner\w*\s+\S+)\s+daran\s*,?\s+dass\s+(?P<content>.+)$", re.IGNORECASE
)
_REMINDER_AN_RE = re.compile(
    r"^(?P<head>erinner\w*\s+\S+)\s+an\s+(?P<thing>.+)$", re.IGNORECASE
)
_SELF_TASK_RE = re.compile(
    r"^ich\s+(?P<task>.+?)\s+(?:soll|sollte|muss|müsste|muesste|darf|wollte)$",
    re.IGNORECASE,
)
_QUOTES = "\"'„“”‚‘’«»"


def parse_notification_clause(text: str) -> NotificationClause | None:
    """Read one notification clause; ``None`` if it is not one.

    ``text`` must not contain the timing or trigger part any more.
    """
    clause = _strip_edges(text)
    if not clause:
        return None
    modal = _MODAL_WRAPPER_RE.match(clause)
    if modal is not None:
        clause = clause[modal.end():]

    reminder = _parse_reminder(clause)
    if reminder is not None:
        return reminder

    head = clause
    message: str | None = None
    for pattern in (_EXPLICIT_TEXT_RE, _NAMED_MESSAGE_RE, _COLON_RE, _QUOTED_RE):
        match = pattern.match(clause)
        if match is not None:
            head, message = match.group("head"), _literal_message(match.group("message"))
            if pattern is _NAMED_MESSAGE_RE:
                head = f"{head} eine Nachricht"  # "schick mir die Nachricht X"
            elif pattern is _QUOTED_RE:
                head = re.sub(r"\s+die\s+nachricht$", "", head.strip(), flags=re.IGNORECASE)
                if re.fullmatch(rf"{_DATIVE_VERB}\s+{_RECIPIENT}", _clean_head(head), re.IGNORECASE):
                    head = f"{head} eine Nachricht"  # schick mir „Fenster zu!"
            break
    else:
        dass = _DASS_RE.match(clause)
        if dass is not None:
            head, message = dass.group("head"), message_from_dass_content(dass.group("content"))
    if message is not None and not message:
        return None

    parsed = _parse_head(head)
    if parsed is None:
        return None
    kind, name, test = parsed
    return NotificationClause(kind, name, message, test=test and message is None)


def _parse_head(head: str) -> tuple[NotificationRecipientKind, str | None, bool] | None:
    cleaned = _clean_head(head)
    if (
        _BARE_BESCHEID_RE.fullmatch(cleaned)
        or _SELF_WISH_RE.fullmatch(cleaned)
        or _SELF_CONTACT_RE.fullmatch(cleaned)
    ):
        return NotificationRecipientKind.CURRENT_USER, None, False
    for pattern in _HEAD_PATTERNS:
        match = pattern.fullmatch(cleaned)
        if match is None:
            continue
        recipient = _recipient(match.group("recipient"))
        if recipient is None:
            return None
        kind, name = recipient
        test = "test" in match.groupdict() and match.group("test") is not None
        return kind, name, test
    return None


def _parse_reminder(clause: str) -> NotificationClause | None:
    task = _REMINDER_TASK_RE.match(clause)
    message: str | None
    if task is not None:
        head = task.group("head")
        # "den Kuchen rauszuholen" -> separable verb "rausholen".
        verb = task.group("verb") or f"{task.group('particle')}{task.group('stem')}"
        message = _reminder_message(f"{task.group('task')} {verb}")
    elif (daran := _REMINDER_DARAN_DASS_RE.match(clause)) is not None:
        head = daran.group("head")
        message = message_from_dass_content(daran.group("content"))
    elif (about := _REMINDER_AN_RE.match(clause)) is not None:
        head = about.group("head")
        message = _reminder_message(about.group("thing"))
    elif (dass := _DASS_RE.match(clause)) is not None and _REMINDER_HEAD_RE.fullmatch(
        _clean_head(dass.group("head"))
    ):
        head = dass.group("head")
        message = message_from_dass_content(dass.group("content"))
    else:
        head, message = clause, None
    match = _REMINDER_HEAD_RE.fullmatch(_clean_head(head))
    if match is None:
        return None
    recipient = _recipient(match.group("recipient"))
    if recipient is None or message == "":
        return None
    kind, name = recipient
    return NotificationClause(kind, name, message, reminder=True)


def _recipient(token: str) -> tuple[NotificationRecipientKind, str | None] | None:
    lowered = token.casefold()
    if " " in lowered:
        if any(part in _NOT_A_RECIPIENT or part in {"mir", "mich", "uns"} for part in lowered.split()):
            return None
        return NotificationRecipientKind.EXPLICIT_TARGET, token
    if lowered in {"mich", "mir"}:
        return NotificationRecipientKind.CURRENT_USER, None
    if lowered == "uns":
        return NotificationRecipientKind.HOUSEHOLD, None
    if lowered in _NOT_A_RECIPIENT:
        return None
    return NotificationRecipientKind.EXPLICIT_TARGET, token


def _clean_head(head: str) -> str:
    cleaned = _CHANNEL_RE.sub(" ", head)
    cleaned = _PARTICLE_RE.sub(" ", cleaned)
    cleaned = re.sub(r"[,;]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" .!?")


def _strip_edges(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().strip(" .!?")


def _literal_message(raw: str) -> str:
    """An explicitly dictated text is kept verbatim (only quotes trimmed)."""
    message = raw.strip().strip(_QUOTES).strip()
    if not message or len(message) > MAX_MESSAGE_LENGTH:
        return ""
    return message


def _reminder_message(task: str) -> str:
    cleaned = task.strip().strip(" ,.!?").strip(_QUOTES).strip()
    if not cleaned or len(cleaned) > MAX_MESSAGE_LENGTH:
        return ""
    return f"Erinnerung: {cleaned}."


# --- message realization ------------------------------------------------------

_FINITE_VERBS = frozenset({
    "ist", "sind", "wird", "werden", "war", "waren", "wurde", "wurden", "hat",
    "haben", "bleibt", "bleiben", "läuft", "laufen", "steht", "stehen", "geht",
    "gehen", "kommt", "kommen", "soll", "sollen", "muss", "müssen", "kann",
    "können", "fehlt", "fehlen", "brennt", "brennen",
})
_PREPOSITIONS = frozenset({
    "im", "in", "am", "an", "auf", "beim", "bei", "vom", "von", "zum", "zur",
    "unter", "über", "hinter", "vor", "neben", "aus",
})
_DETERMINERS = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "eines",
    "einem", "einen", "mein", "meine", "dein", "deine", "unser", "unsere",
    "kein", "keine", "alle", "jedes", "jede", "jeder", "irgendein", "irgendeine",
})
_SINGLE_WORD_SUBJECTS = frozenset({
    "es", "er", "sie", "wir", "man", "jemand", "niemand", "dort", "hier",
    "jetzt", "heute", "gerade", "da",
})


def message_from_dass_content(content: str) -> str:
    """Realize a "dass ..." complement as an inert main-clause message.

    "im Wohnzimmer ein Fenster offen ist" -> "Im Wohnzimmer ist ein Fenster
    offen."  A self-directed task ("ich den Backofen prüfen soll") becomes the
    same reminder text "erinnere mich daran, den Backofen zu prüfen" yields.
    Anything the verb-second rule cannot place safely stays verbatim.
    """
    cleaned = content.strip().strip(" ,.!?").strip(_QUOTES).strip()
    if not cleaned or len(cleaned) > MAX_MESSAGE_LENGTH:
        return ""
    task = _SELF_TASK_RE.fullmatch(cleaned)
    if task is not None:
        return _reminder_message(task.group("task"))
    tokens = cleaned.split()
    if len(tokens) >= 3 and tokens[-1].casefold() in _FINITE_VERBS:
        first = _first_constituent_length(tokens)
        if first is not None and first < len(tokens) - 1:
            tokens = [*tokens[:first], tokens[-1], *tokens[first:-1]]
    return _as_sentence(" ".join(tokens))


def _first_constituent_length(tokens: Sequence[str]) -> int | None:
    head = tokens[0].casefold()
    if head in _PREPOSITIONS:
        if len(tokens) > 2 and tokens[1].casefold() in _DETERMINERS:
            return 3
        return 2
    if head in _DETERMINERS:
        return 2
    if head in _SINGLE_WORD_SUBJECTS:
        return 1
    if tokens[0][:1].isupper():
        return 1  # a proper name or compound noun ("Badezimmerfenster")
    return None


def _as_sentence(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped[-1] not in ".!?":
        stripped += "."
    return sentence_initial(stripped)


@dataclass(frozen=True)
class StateEventPhrase:
    """A state-trigger event realized for people, never with entity ids."""

    subordinate: str  # "im Wohnzimmer ein Fenster geöffnet wird"
    sentence: str  # "Im Wohnzimmer wurde ein Fenster geöffnet."


# Indefinite noun phrase per device class / domain for "ein Fenster".
_EVENT_NOUNS: dict[tuple[str, str | None], str] = {
    ("binary_sensor", "window"): "ein Fenster",
    ("binary_sensor", "door"): "eine Tür",
    ("binary_sensor", "garage_door"): "ein Garagentor",
    ("cover", "garage"): "ein Garagentor",
    ("cover", None): "ein Rollladen",
    ("light", None): "ein Licht",
    ("switch", None): "ein Schalter",
    ("fan", None): "ein Ventilator",
    ("lock", None): "ein Schloss",
}
_PARTICIPLES: dict[SemanticState, str] = {
    SemanticState.OPEN: "geöffnet",
    SemanticState.CLOSED: "geschlossen",
    SemanticState.ON: "eingeschaltet",
    SemanticState.OFF: "ausgeschaltet",
}


def describe_state_event(
    trigger: TriggerModel, entities: Sequence[EntitySnapshot]
) -> StateEventPhrase | None:
    """Describe a STATE trigger's event, or ``None`` if it cannot be said safely."""
    target = trigger.target
    if trigger.type is not TriggerType.STATE or target is None or trigger.state is None:
        return None
    if target.domain == "binary_sensor" and target.device_class == "motion":
        if trigger.state is not SemanticState.ON:
            return None
        location = _location(target, entities)
        if location is None:
            return StateEventPhrase("eine Bewegung erkannt wird", "Es wurde eine Bewegung erkannt.")
        return StateEventPhrase(
            f"{location} eine Bewegung erkannt wird",
            f"{sentence_initial(location)} wurde eine Bewegung erkannt.",
        )
    participle = _PARTICIPLES.get(trigger.state)
    if participle is None:
        return None
    single = _single_entity(target, entities)
    if single is not None:
        definite = definite_entity_phrase(single.friendly_name)
        subject = definite[0] if definite is not None else single.friendly_name
        return StateEventPhrase(
            f"{subject} {participle} wird",
            f"{sentence_initial(subject)} wurde {participle}.",
        )
    noun = _EVENT_NOUNS.get((target.domain or "", target.device_class))
    if noun is None:
        return None
    location = _location(target, entities)
    if location is None:
        return StateEventPhrase(
            f"{noun} {participle} wird", f"{sentence_initial(noun)} wurde {participle}."
        )
    return StateEventPhrase(
        f"{location} {noun} {participle} wird",
        f"{sentence_initial(location)} wurde {noun} {participle}.",
    )


def describe_event(
    trigger: TriggerModel, entities: Sequence[EntitySnapshot]
) -> StateEventPhrase | None:
    """Describe a STATE or NUMERIC_STATE trigger's event for people."""
    if trigger.type is TriggerType.STATE:
        return describe_state_event(trigger, entities)
    if trigger.type is TriggerType.NUMERIC_STATE:
        return describe_numeric_event(trigger, entities)
    if trigger.type is TriggerType.PRESENCE:
        return describe_presence_event(trigger, entities)
    return None


def describe_presence_event(
    trigger: TriggerModel, entities: Sequence[EntitySnapshot]
) -> StateEventPhrase | None:
    """"Julia nach Hause kommt" / "Julia ist nach Hause gekommen."."""
    event = trigger.presence_event
    if event is None:
        return None
    if trigger.presence_of_speaker:
        if event is PresenceEvent.ARRIVE:
            return StateEventPhrase("du nach Hause kommst", "Du bist nach Hause gekommen.")
        return StateEventPhrase("du das Haus verlässt", "Du hast das Haus verlassen.")
    target = trigger.target
    person = _single_entity(target, entities) if target is not None else None
    if person is None:
        return None
    name = person.friendly_name
    if event is PresenceEvent.ARRIVE:
        return StateEventPhrase(f"{name} nach Hause kommt", f"{name} ist nach Hause gekommen.")
    return StateEventPhrase(f"{name} das Haus verlässt", f"{name} hat das Haus verlassen.")


_UNIT_WORDS: dict[str, str] = {"°C": "Grad", "°F": "Grad Fahrenheit", "%": "%"}
_PROPERTY_PHRASES: dict[MeasurementProperty, str] = {
    MeasurementProperty.COVER_POSITION: "",
    MeasurementProperty.LIGHT_BRIGHTNESS: "eine Helligkeit von ",
    MeasurementProperty.FAN_PERCENTAGE: "eine Geschwindigkeit von ",
}
_PROPERTY_SUFFIXES: dict[MeasurementProperty, str] = {
    MeasurementProperty.LIGHT_BRIGHTNESS: "Helligkeit",
    MeasurementProperty.FAN_PERCENTAGE: "Geschwindigkeit",
}
_DIRECTION_PHRASES: dict[TravelDirection, str] = {
    TravelDirection.UP: "beim Hochfahren ",
    TravelDirection.DOWN: "beim Herunterfahren ",
}


def format_german_number(value: float) -> str:
    """50.0 -> "50", 50.5 -> "50,5" (German decimal comma)."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}".replace(".", ",")


def describe_numeric_event(
    trigger: TriggerModel, entities: Sequence[EntitySnapshot]
) -> StateEventPhrase | None:
    """"der Rollladen im Büro 50 % erreicht" / "Der Rollladen im Büro hat 50 % erreicht."

    Realized from the grounded entity - never from the user's own wording -
    so every paraphrase of one meaning yields the same message.
    """
    target = trigger.target
    if (
        trigger.type is not TriggerType.NUMERIC_STATE
        or target is None
        or trigger.threshold is None
        or trigger.comparator is None
    ):
        return None
    subject = _event_subject(target, entities)
    if subject is None:
        return None
    number = format_german_number(trigger.threshold)
    if trigger.measurement is not None:
        unit = "%"
        prefix = _PROPERTY_PHRASES[trigger.measurement]
    else:
        single = _single_entity(target, entities)
        unit_symbol = single.unit if single is not None else None
        unit = _UNIT_WORDS.get(unit_symbol or "", unit_symbol or "")
        prefix = ""
    amount = f"{number} {unit}".strip()
    direction = _DIRECTION_PHRASES[trigger.direction] if trigger.direction is not None else ""
    comparator = trigger.comparator
    if comparator is NumericComparator.EQUAL:
        return StateEventPhrase(
            f"{subject} {direction}{prefix}{amount} erreicht",
            f"{sentence_initial(subject)} hat {direction}{prefix}{amount} erreicht.",
        )
    if comparator is NumericComparator.AT_LEAST:
        return StateEventPhrase(
            f"{subject} {direction}mindestens {prefix}{amount} erreicht",
            f"{sentence_initial(subject)} hat {direction}mindestens {prefix}{amount} erreicht.",
        )
    word = {
        NumericComparator.ABOVE: "über",
        NumericComparator.BELOW: "unter",
        NumericComparator.AT_MOST: "bei höchstens",
    }[comparator]
    suffix = _PROPERTY_SUFFIXES.get(trigger.measurement) if trigger.measurement else None
    measured = f"{word} {amount}" + (f" {suffix}" if suffix else "")
    return StateEventPhrase(
        f"{subject} {direction}{measured} liegt",
        f"{sentence_initial(subject)} liegt {direction}jetzt {measured}.",
    )


def _event_subject(
    target: TriggerTarget, entities: Sequence[EntitySnapshot]
) -> str | None:
    """Nominative noun phrase for a numeric trigger's subject."""
    single = _single_entity(target, entities)
    if single is not None:
        return entity_subject_phrase(single)
    noun = _EVENT_NOUNS.get((target.domain or "", target.device_class)) or _EVENT_NOUNS.get(
        (target.domain or "", None)
    )
    if noun is None:
        return None
    location = _location(target, entities)
    return f"{noun} {location}" if location is not None else noun


def entity_subject_phrase(entity: EntitySnapshot) -> str:
    """"Büro Rollladen" in area "Büro" -> "der Rollladen im Büro".

    The area word is only moved into a locative when the rest of the name is
    a single known device noun; any other name is kept verbatim.
    """
    name = entity.friendly_name.strip()
    area = (entity.area_name or "").strip()
    if area:
        remainder = _without_area(name, area)
        if remainder is not None and " " not in remainder:
            gender_phrase = definite_entity_phrase(remainder)
            if gender_phrase is not None:
                return f"{gender_phrase[0]} {dative_location_phrase(area)}"
    definite = definite_entity_phrase(name)
    return definite[0] if definite is not None else name


def _without_area(name: str, area: str) -> str | None:
    lowered, area_key = name.casefold(), area.casefold()
    if lowered.startswith(area_key + " "):
        return name[len(area) + 1:].strip()
    if lowered.endswith(" " + area_key):
        return name[: -len(area) - 1].strip()
    return None


def _matching_entities(
    target: TriggerTarget, entities: Sequence[EntitySnapshot]
) -> list[EntitySnapshot]:
    if target.entity_id is not None:
        return [item for item in entities if item.entity_id == target.entity_id]
    if target.entity_ids:
        return [item for item in entities if item.entity_id in target.entity_ids]
    return [
        item for item in entities
        if item.domain == target.domain
        and (target.device_class is None or item.device_class == target.device_class)
        and (target.area_id is None or item.area_id == target.area_id)
        and (target.floor_id is None or item.floor_id == target.floor_id)
    ]


def _single_entity(
    target: TriggerTarget, entities: Sequence[EntitySnapshot]
) -> EntitySnapshot | None:
    matches = _matching_entities(target, entities)
    return matches[0] if len(matches) == 1 else None


def _location(target: TriggerTarget, entities: Sequence[EntitySnapshot]) -> str | None:
    if target.area_id is None:
        return None
    name = next(
        (item.area_name for item in entities if item.area_id == target.area_id and item.area_name),
        None,
    )
    return dative_location_phrase(name) if name else None


def trigger_message(
    trigger: TriggerModel | None,
    trigger_text: str,
    entities: Sequence[EntitySnapshot],
) -> str:
    """Deterministic message for a notification without explicit text."""
    if trigger is not None:
        described = describe_event(trigger, entities)
        if described is not None:
            return described.sentence
    return message_from_trigger_text(trigger_text)


def message_from_trigger_text(trigger_text: str) -> str:
    """Grammatical fallback when only the spoken trigger clause is known."""
    content = re.sub(
        r"^(?:wenn|sobald|falls)\s+", "", trigger_text.strip(), flags=re.IGNORECASE
    ).strip(" ,.!?")
    if not content:
        return "Auslöser eingetreten."
    passive = re.fullmatch(
        r"(?P<subject>.+?)\s+(?P<participle>\S+)\s+(?P<aux>wird|werden)", content, re.IGNORECASE
    )
    if passive is not None:
        aux = "wurde" if passive.group("aux").casefold() == "wird" else "wurden"
        return _as_sentence(
            f"{passive.group('subject')} {aux} {passive.group('participle')}"
        )
    return message_from_dass_content(content) or "Auslöser eingetreten."


__all__ = (
    "DEFAULT_NOTIFICATION_MESSAGE",
    "DEFAULT_NOTIFICATION_TITLE",
    "DEFAULT_REMINDER_MESSAGE",
    "NotificationClause",
    "StateEventPhrase",
    "TEST_NOTIFICATION_MESSAGE",
    "describe_event",
    "describe_numeric_event",
    "describe_state_event",
    "entity_subject_phrase",
    "format_german_number",
    "message_from_dass_content",
    "message_from_trigger_text",
    "parse_notification_clause",
    "trigger_message",
)
