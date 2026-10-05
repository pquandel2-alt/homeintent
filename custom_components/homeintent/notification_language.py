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
#
# The reader works on tokens and German valency frames: a notification verb
# class (accusative "benachrichtigen", dative "schicken/schreiben", "Bescheid
# sagen/geben"), a recipient in the case the verb demands, an optional message
# object and an optional content complement (":" text, quoted text, "dass"
# clause, "mit dem Text ...").  One tokenizer, no sentence patterns.

_TOKEN_RE = re.compile(r"[„\"“”«»‚‘’']|[\wäöüßÄÖÜ]+(?:-[\wäöüßÄÖÜ]+)*|[:,;.!?]")


@dataclass(frozen=True)
class _Tok:
    surface: str
    key: str
    start: int
    end: int

    @property
    def is_word(self) -> bool:
        return self.key[:1].isalnum()


def _tokenize(text: str) -> list[_Tok]:
    return [
        _Tok(match.group(0), match.group(0).casefold(), match.start(), match.end())
        for match in _TOKEN_RE.finditer(text)
    ]


_MODAL_WRAPPERS = frozenset({"kannst", "könntest", "koenntest", "würdest", "wuerdest", "magst"})
_PARTICLES = frozenset({
    "bitte", "mal", "doch", "kurz", "einfach", "gleich", "sofort", "jetzt", "nochmal",
    "vielleicht", "eben", "eigentlich", "dann",
})
_DEVICES = frozenset({"handy", "iphone", "smartphone", "telefon", "tablet", "ipad"})
_PUSH_WORDS = frozenset({
    "push", "push-nachricht", "pushnachricht", "benachrichtigung", "app", "handy",
    "push-benachrichtigung", "pushbenachrichtigung",
})
_ACCUSATIVE_VERBS = frozenset({
    "benachrichtige", "benachrichtigen", "benachrichtiger", "benachrichtig",
    "informiere", "informieren", "informierer", "informier", "warne", "warn",
})
_DATIVE_VERBS = frozenset({
    "schick", "schicke", "schicken", "schicker", "send", "sende", "senden", "sender",
    "mach", "mache", "schreib", "schreibe", "schreiben", "schreiber",
})
# Verbs whose dative recipient needs no message noun when content follows
# ("Schreib Anna, dass ...", "Schick mir aufs Handy: ...").
_WRITING_VERBS = frozenset({"schreib", "schreibe", "schreiben", "schreiber"})
_BESCHEID_VERBS = frozenset({"sag", "sage", "sagen", "gib", "geb", "gebe", "geben"})
_MESSAGE_NOUNS = frozenset({
    "nachricht", "benachrichtigung", "meldung", "mitteilung", "notification", "info",
    "warnung", "push", "push-nachricht", "pushnachricht", "push-benachrichtigung",
    "pushbenachrichtigung", "sms", "textnachricht", "nachrichten",
})
_ARTICLES = frozenset({"eine", "die", "ne", "kurze", "kleine"})
_WISH_MODALS = frozenset({
    "möchte", "moechte", "will", "würde", "wuerde", "hätte", "haette", "wäre", "waere",
})
_PASSIVE_PARTICIPLES = frozenset({"benachrichtigt", "informiert", "gewarnt", "verständigt"})
_RECEIVE = frozenset({"bekommen", "erhalten", "haben"})

# Words that can follow a verb but are never a recipient.
_NOT_A_RECIPIENT = frozenset({
    "eine", "ein", "die", "das", "der", "den", "dem", "ne", "an", "auf", "aufs",
    "per", "mit", "dass", "ob", "es", "bescheid", "test", "push", "nachricht",
    "benachrichtigung", "meldung", "mitteilung", "info", "wenn", "sobald",
    "falls", "was", "wie", "wo", "wann", "warum", "welche", "welcher", "welches",
    "sie", "ihn", "ihm", "ihr", "dir", "dich", "du", "ich", "euch",
})
_QUOTES = "\"'„“”‚‘’«»"
_EXPLICIT_TEXT_MARKERS: tuple[tuple[str, ...], ...] = (
    ("mit", "dem", "text"), ("mit", "der", "nachricht"), ("mit", "dem", "inhalt"),
    ("mit", "folgendem", "text"), ("mit", "folgender", "nachricht"), ("mit", "text"),
)


def parse_notification_clause(text: str) -> NotificationClause | None:
    """Read one notification clause; ``None`` if it is not one.

    ``text`` must not contain the timing or trigger part any more.
    """
    clause = _strip_edges(text)
    if not clause:
        return None
    tokens = _tokenize(clause)
    if len(tokens) >= 2 and tokens[0].key in _MODAL_WRAPPERS and tokens[1].key == "du":
        clause = clause[tokens[2].start:] if len(tokens) > 2 else ""
        tokens = _tokenize(clause)
    if not tokens:
        return None

    reminder = _parse_reminder(clause, tokens)
    if reminder is not None:
        return reminder

    head, message, implied_object = _split_content(clause, tokens)
    if message is not None and not message:
        return None
    parsed = _parse_head(head, content=message is not None or implied_object)
    if parsed is None:
        return None
    kind, name, test = parsed
    return NotificationClause(kind, name, message, test=test and message is None)


def _split_content(
    clause: str, tokens: list[_Tok]
) -> tuple[list[_Tok], str | None, bool]:
    """Separate the notification head from dictated content."""
    keys = [token.key for token in tokens]
    for marker in _EXPLICIT_TEXT_MARKERS:
        for index in range(len(keys) - len(marker)):
            if tuple(keys[index:index + len(marker)]) == marker:
                rest = index + len(marker)
                if rest < len(tokens) and tokens[rest].key == ":":
                    rest += 1
                if rest < len(tokens):
                    return tokens[:index], _literal_message(clause[tokens[rest].start:]), False
    # "schick mir die Nachricht X": the message follows its own noun.
    for index in range(1, len(keys) - 2):
        if keys[index] == "die" and keys[index + 1] == "nachricht" and keys[0] in _DATIVE_VERBS:
            if tokens[index + 2].key != ":" and tokens[index + 2].surface[:1] not in _QUOTES:
                return tokens[:index], _literal_message(clause[tokens[index + 2].start:]), True
    for index, token in enumerate(tokens):
        if token.key == ":" and index + 1 < len(tokens):
            return tokens[:index], _literal_message(clause[tokens[index + 1].start:]), False
    for index, token in enumerate(tokens):
        if token.surface in _QUOTES and index + 1 < len(tokens):
            closing = next(
                (item for item in tokens[index + 1:] if item.surface in _QUOTES), None
            )
            end = closing.start if closing is not None else len(clause)
            head = tokens[:index]
            if len(head) >= 2 and [item.key for item in head[-2:]] == ["die", "nachricht"]:
                head = head[:-2]
            return head, _literal_message(clause[tokens[index + 1].start:end]), True
    for index, token in enumerate(tokens):
        if token.key == "dass" and index > 0 and index + 1 < len(tokens):
            head = tokens[:index]
            return head, message_from_dass_content(clause[tokens[index + 1].start:]), False
    return tokens, None, False


def _clean(tokens: list[_Tok]) -> list[_Tok]:
    """Remove channel phrases ("aufs Handy", "per Push"), particles, commas."""
    cleaned: list[_Tok] = []
    index = 0
    while index < len(tokens):
        key = tokens[index].key
        following = tokens[index + 1].key if index + 1 < len(tokens) else ""
        after = tokens[index + 2].key if index + 2 < len(tokens) else ""
        if key == "aufs" and following in _DEVICES:
            index += 2
            continue
        if key == "auf" and following in {"das", "mein", "dein"} and after in _DEVICES:
            index += 3
            continue
        if key in {"per", "über", "via"} and following in _PUSH_WORDS:
            index += 2
            continue
        if key == "als" and following.startswith("push"):
            index += 2
            continue
        if key in _PARTICLES or not tokens[index].is_word:
            index += 1
            continue
        cleaned.append(tokens[index])
        index += 1
    return cleaned


def _recipient_at(
    tokens: list[_Tok], index: int, *, two_words: bool = False
) -> tuple[tuple[NotificationRecipientKind, str | None], int] | None:
    if index >= len(tokens):
        return None
    if (
        two_words
        and index + 1 < len(tokens)
        and tokens[index].surface[:1].isupper()
        and tokens[index + 1].surface[:1].isupper()
    ):
        pair = _recipient(f"{tokens[index].surface} {tokens[index + 1].surface}")
        if pair is not None:
            return pair, 2
    single = _recipient(tokens[index].surface)
    return (single, 1) if single is not None else None


def _object_length(keys: Sequence[str], index: int) -> tuple[int, bool] | None:
    """Length of "[eine] [Test-][Push-]Nachricht" at ``index`` and its test flag."""
    position = index
    while position < len(keys) and keys[position] in _ARTICLES:
        position += 1
    test = False
    if position < len(keys) and keys[position] == "test":
        test, position = True, position + 1
    if position < len(keys) and keys[position] == "push" and position + 1 < len(keys) and (
        keys[position + 1] in _MESSAGE_NOUNS
    ):
        position += 1
    if position >= len(keys):
        return None
    noun = keys[position]
    base = noun.removeprefix("test-").removeprefix("test")
    if noun in _MESSAGE_NOUNS or base in _MESSAGE_NOUNS:
        return position + 1 - index, test or noun != base
    return None


def _parse_head(
    raw: list[_Tok], *, content: bool = False
) -> tuple[NotificationRecipientKind, str | None, bool] | None:
    tokens = _clean(raw)
    keys = [token.key for token in tokens]
    current = NotificationRecipientKind.CURRENT_USER
    if not keys:
        return None
    # Bare or self-directed forms: "Bescheid", "melde dich (bei mir)",
    # "ping mich (an)", "gib Bescheid", "ich möchte eine Nachricht bekommen".
    if keys in (["bescheid"], ["bescheid", "sagen"], ["bescheid", "geben"]):
        return current, None, False
    if keys[:2] in (["melde", "dich"], ["meld", "dich"]) and keys[2:] in ([], ["bei", "mir"]):
        return current, None, False
    if keys[:2] == ["ping", "mich"] and keys[2:] in ([], ["an"]):
        return current, None, False
    if keys in (["gib", "bescheid"], ["sag", "bescheid"], ["sage", "bescheid"]):
        return current, None, False
    wish = _self_wish(keys)
    if wish is not None:
        return current, None, wish
    verb = keys[0]
    if verb in _ACCUSATIVE_VERBS:
        found = _recipient_at(tokens, 1, two_words=True)
        if found is not None and 1 + found[1] == len(keys):
            return found[0][0], found[0][1], False
        return None
    if verb in _DATIVE_VERBS:
        found = _recipient_at(tokens, 1, two_words=True)
        if found is not None:
            (kind, name), used = found
            rest = keys[1 + used:]
            obj = _object_length(keys, 1 + used)
            if obj is not None and 1 + used + obj[0] == len(keys):
                return kind, name, obj[1]
            if used == 1 and rest in (["push"], ["ne", "push"], ["eine", "push"], ["was"], ["etwas"], ["bescheid"]):
                return kind, name, False
            if used == 1 and not rest and (content or verb in _WRITING_VERBS):
                return kind, name, False
        obj = _object_length(keys, 1)
        if obj is not None and 1 + obj[0] < len(keys) and keys[1 + obj[0]] == "an":
            position = 2 + obj[0]
            if position < len(keys) and keys[position] in {"den", "die", "das"}:
                position += 1
            found = _recipient_at(tokens, position)
            if found is not None and position + found[1] == len(keys):
                return found[0][0], found[0][1], obj[1]
        return None
    if verb in _BESCHEID_VERBS:
        found = _recipient_at(tokens, 1, two_words=True)
        if found is not None and keys[1 + found[1]:] == ["bescheid"]:
            return found[0][0], found[0][1], False
        return None
    # Recipient first (modal infinitive): "mich benachrichtigen (lassen)",
    # "mir eine Nachricht schicken", "mir Bescheid sagen".
    found = _recipient_at(tokens, 0)
    if found is not None:
        (kind, name), used = found
        rest = keys[used:]
        if rest in (["benachrichtigen"], ["informieren"], ["benachrichtigen", "lassen"], ["informieren", "lassen"]):
            return kind, name, False
        if rest in (["bescheid", "sagen"], ["bescheid", "geben"]):
            return kind, name, False
        obj = _object_length(keys, used)
        if obj is not None:
            tail = keys[used + obj[0]:]
            if tail in (["schicken"], ["senden"], ["schreiben"], ["zukommen", "lassen"]):
                return kind, name, obj[1]
    # Verbless: "(eine) Nachricht an mich", "Push an Anna".
    obj = _object_length(keys, 0)
    if obj is not None and obj[0] < len(keys) and keys[obj[0]] == "an":
        found = _recipient_at(tokens, obj[0] + 1)
        if found is not None and obj[0] + 1 + found[1] == len(keys):
            return found[0][0], found[0][1], obj[1]
    return None


def _self_wish(keys: Sequence[str]) -> bool | None:
    """"ich möchte eine Nachricht (bekommen)" / "ich will benachrichtigt werden"."""
    if len(keys) < 3:
        return None
    if keys[0] == "ich" and keys[1] in _WISH_MODALS:
        rest = list(keys[2:])
    elif keys[0] in _WISH_MODALS and keys[1] == "ich":
        rest = list(keys[2:])
    else:
        return None
    while rest and rest[0] in {"sehr", "gern", "gerne"}:
        rest.pop(0)
    if rest[:2] == ["dankbar", "für"]:
        rest = rest[2:]
    if len(rest) == 2 and rest[0] in _PASSIVE_PARTICIPLES and rest[1] == "werden":
        return False
    obj = _object_length(rest, 0)
    if obj is not None and (
        not rest[obj[0]:] or (len(rest[obj[0]:]) == 1 and rest[obj[0]] in _RECEIVE)
    ):
        return obj[1]
    return None


def _parse_reminder(clause: str, tokens: list[_Tok]) -> NotificationClause | None:
    words = [token for token in tokens if token.is_word]
    if len(words) < 2 or not words[0].key.startswith("erinner"):
        return None
    recipient = _recipient(words[1].surface)
    if recipient is None:
        return None
    kind, name = recipient
    rest_tokens = [token for token in tokens if token.start >= words[1].end]
    rest_words = [token for token in rest_tokens if token.is_word]
    message: str | None = None
    if rest_words and rest_words[0].key == "daran":
        after = [token for token in rest_words[1:]]
        if after and after[0].key == "dass":
            message = message_from_dass_content(clause[after[0].end:])
        elif len(after) >= 2 and after[-2].key == "zu":
            task = clause[after[0].start:after[-2].start].strip()
            message = _reminder_message(f"{task} {after[-1].surface}")
        elif after and "zu" in after[-1].key[2:-2] and after[-1].key.endswith("en"):
            particle, _, stem = after[-1].surface.partition("zu")
            task = clause[after[0].start:after[-1].start].strip()
            message = _reminder_message(f"{task} {particle}{stem}")
        else:
            return None
    elif rest_words and rest_words[0].key == "an":
        message = _reminder_message(clause[rest_words[0].end:])
    elif rest_words and rest_words[0].key == "dass":
        message = message_from_dass_content(clause[rest_words[0].end:])
    elif rest_words:
        return None
    if message == "":
        return None
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
    if lowered in _NOT_A_RECIPIENT or not lowered[:1].isalpha():
        return None
    return NotificationRecipientKind.EXPLICIT_TARGET, token


def _strip_edges(text: str) -> str:
    return " ".join(text.split()).strip().strip(" .!?")


def _literal_message(raw: str) -> str:
    """An explicitly dictated text is kept verbatim (only quotes trimmed)."""
    message = raw.strip().strip(_QUOTES).strip().rstrip(_QUOTES).strip()
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
    "ich", "du", "es", "er", "sie", "wir", "man", "jemand", "niemand", "dort", "hier",
    "jetzt", "heute", "gerade", "da",
})


_SELF_TASK_MODALS = frozenset({"soll", "sollte", "muss", "müsste", "muesste", "darf", "wollte"})
_FINITE_ENDINGS = ("e", "t", "st", "en")


def _looks_finite(word: str) -> bool:
    """Verb-final position of a subordinate clause: a lower-case verb form."""
    lowered = word.casefold()
    if lowered in _FINITE_VERBS:
        return True
    return word[:1].islower() and lowered.endswith(_FINITE_ENDINGS) and len(lowered) > 3


def message_from_dass_content(content: str) -> str:
    """Realize a "dass ..." complement as an inert main-clause message.

    "im Wohnzimmer ein Fenster offen ist" -> "Im Wohnzimmer ist ein Fenster
    offen."  A self-directed task ("ich den Backofen prüfen soll") becomes the
    same reminder text "erinnere mich daran, den Backofen zu prüfen" yields.
    The finite verb of the subordinate clause moves to second position
    ("ich gleich komme" -> "Ich komme gleich."); anything the verb-second
    rule cannot place safely stays verbatim.
    """
    cleaned = content.strip().strip(" ,.!?").strip(_QUOTES).strip()
    if not cleaned or len(cleaned) > MAX_MESSAGE_LENGTH:
        return ""
    tokens = cleaned.split()
    if (
        len(tokens) >= 3
        and tokens[0].casefold() == "ich"
        and tokens[-1].casefold() in _SELF_TASK_MODALS
    ):
        return _reminder_message(" ".join(tokens[1:-1]))
    if len(tokens) >= 3 and _looks_finite(tokens[-1]):
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
    if (
        target.domain == "binary_sensor"
        and trigger.state is SemanticState.ON
        and target.device_class in _ALARM_REPORTS
    ):
        report = _ALARM_REPORTS[target.device_class]
        single = _single_entity(target, entities)
        subject = (
            single.friendly_name if single is not None
            else _group_subject(target, entities, _ALARM_NOUNS[target.device_class])
        )
        return StateEventPhrase(
            f"{subject} {report} meldet",
            f"{sentence_initial(subject)} meldet {report}.",
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
    matches = _matching_entities(target, entities)
    if noun is None and matches:
        classes = {(item.domain, item.device_class) for item in matches}
        if len(classes) == 1:
            noun = _EVENT_NOUNS.get(next(iter(classes))) or _EVENT_NOUNS.get(
                (next(iter(classes))[0], None)
            )
    if noun is None:
        return None
    location = _location(target, entities) or _shared_location(matches)
    if location is None:
        return StateEventPhrase(
            f"{noun} {participle} wird", f"{sentence_initial(noun)} wurde {participle}."
        )
    return StateEventPhrase(
        f"{location} {noun} {participle} wird",
        f"{sentence_initial(location)} wurde {noun} {participle}.",
    )


# Detector-style binary sensors report something; they are not "switched on".
_ALARM_REPORTS: dict[str | None, str] = {
    "moisture": "Wasser",
    "smoke": "Rauch",
    "gas": "Gas",
    "carbon_monoxide": "Kohlenmonoxid",
    "problem": "ein Problem",
    "tamper": "eine Manipulation",
}
_ALARM_NOUNS: dict[str | None, str] = {
    "moisture": "ein Wassermelder",
    "smoke": "ein Rauchmelder",
    "gas": "ein Gasmelder",
    "carbon_monoxide": "ein CO-Melder",
    "problem": "ein Sensor",
    "tamper": "ein Sensor",
}


def _shared_location(matches: Sequence[EntitySnapshot]) -> str | None:
    """Common area or floor of several trigger entities ("im Obergeschoss")."""
    if not matches:
        return None
    areas = {item.area_name for item in matches}
    if len(areas) == 1 and None not in areas:
        return dative_location_phrase(next(iter(areas)) or "")
    floors = {item.floor_name for item in matches}
    if len(floors) == 1 and None not in floors:
        return dative_location_phrase(next(iter(floors)) or "")
    return None


def _group_subject(
    target: TriggerTarget, entities: Sequence[EntitySnapshot], noun: str
) -> str:
    location = _location(target, entities) or _shared_location(_matching_entities(target, entities))
    return f"{noun} {location}" if location else noun


def describe_event(
    trigger: TriggerModel, entities: Sequence[EntitySnapshot]
) -> StateEventPhrase | None:
    """Describe a STATE or NUMERIC_STATE trigger's event for people."""
    if trigger.appliance_label:
        subject = trigger.appliance_label
        return StateEventPhrase(f"{subject} fertig ist", f"{sentence_initial(subject)} ist fertig.")
    if trigger.type is TriggerType.STATE and trigger.absent_state is not None:
        return describe_inactivity(trigger, entities)
    if trigger.type is TriggerType.STATE:
        phrase = describe_state_event(trigger, entities)
        if phrase is not None and trigger.for_seconds and trigger.state in _STATE_ADJECTIVES:
            return _with_duration(phrase, trigger)
        return phrase
    if trigger.type is TriggerType.NUMERIC_STATE:
        return describe_numeric_event(trigger, entities)
    if trigger.type is TriggerType.PRESENCE:
        return describe_presence_event(trigger, entities)
    return None


_STATE_ADJECTIVES: dict[SemanticState, str] = {
    SemanticState.OPEN: "offen",
    SemanticState.CLOSED: "geschlossen",
    SemanticState.ON: "an",
    SemanticState.OFF: "aus",
}


def spoken_duration(seconds: int) -> str:
    """600 -> "10 Minuten", 3600 -> "1 Stunde", 90 -> "90 Sekunden"."""
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} Tag" if days == 1 else f"{days} Tage"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} Stunde" if hours == 1 else f"{hours} Stunden"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} Minute" if minutes == 1 else f"{minutes} Minuten"
    return f"{seconds} Sekunden"


def _since(duration: str) -> str:
    """"seit 2 Tagen" - the dative plural of a spoken duration."""
    return f"seit {duration}n" if duration.endswith("Tage") else f"seit {duration}"


_MOTION_CLASSES = frozenset({"motion", "occupancy", "presence"})


def describe_inactivity(
    trigger: TriggerModel, entities: Sequence[EntitySnapshot]
) -> StateEventPhrase | None:
    """"im Flur 12 Stunden lang keine Bewegung erkannt wurde" / "Im Flur wurde
    seit 12 Stunden keine Bewegung erkannt." (7.9 W2)."""
    target = trigger.target
    if target is None or trigger.absent_state is None:
        return None
    duration = spoken_duration(int(trigger.for_seconds)) if trigger.for_seconds else None
    span = f" {duration} lang" if duration else ""
    since = f" {_since(duration)}" if duration else ""
    matches = _matching_entities(target, entities)
    if matches and all(item.device_class in _MOTION_CLASSES for item in matches):
        location = _location(target, entities) or _shared_location(matches)
        where = f"{location} " if location else ""
        head = location or "Es"
        return StateEventPhrase(
            f"{where}{span.strip()} keine Bewegung erkannt wurde".replace("  ", " ").strip(),
            f"{sentence_initial(head)} wurde{since} keine Bewegung erkannt.",
        )
    participle = _PARTICIPLES.get(trigger.absent_state)
    single = _single_entity(target, entities)
    if participle is None or single is None:
        return None
    definite = definite_entity_phrase(single.friendly_name)
    subject = definite[0] if definite is not None else single.friendly_name
    return StateEventPhrase(
        f"{subject}{span} nicht {participle} wurde",
        f"{sentence_initial(subject)} wurde{since} nicht {participle}.",
    )


def describe_unchanged_today(
    target: TriggerTarget,
    rest_state: SemanticState,
    until: tuple[int, int] | None,
    entities: Sequence[EntitySnapshot],
) -> StateEventPhrase | None:
    """"im Bad seit Mitternacht keine Bewegung erkannt wurde" / "Im Bad wurde
    heute bis 10:00 Uhr keine Bewegung erkannt." (7.9 W2)."""
    absent = _COMPLEMENT.get(rest_state, SemanticState.ACTIVE if rest_state is SemanticState.INACTIVE else None)
    if absent is None:
        return None
    clock = f" bis {until[0]:02d}:{until[1]:02d} Uhr" if until is not None else ""
    matches = _matching_entities(target, entities)
    if matches and all(item.device_class in _MOTION_CLASSES for item in matches):
        location = _location(target, entities) or _shared_location(matches) or "im Haus"
        return StateEventPhrase(
            f"{location} seit Mitternacht keine Bewegung erkannt wurde",
            f"{sentence_initial(location)} wurde heute{clock} keine Bewegung erkannt.",
        )
    single = _single_entity(target, entities)
    if single is None:
        return None
    if single.domain == "sensor" and rest_state is SemanticState.INACTIVE:
        # A program status sensor ("Waschmaschine Status"): the appliance.
        name = " ".join(
            word for word in single.friendly_name.split()
            if word.casefold() not in {"status", "zustand", "programm", "betrieb"}
        ) or single.friendly_name
        definite = definite_entity_phrase(name)
        subject = definite[0] if definite is not None else name
        return StateEventPhrase(
            f"{subject} seit Mitternacht nicht gelaufen ist",
            f"{sentence_initial(subject)} ist heute{clock} nicht gelaufen.",
        )
    participle = _PARTICIPLES.get(absent)
    if participle is None:
        return None
    definite = definite_entity_phrase(single.friendly_name)
    subject = definite[0] if definite is not None else single.friendly_name
    return StateEventPhrase(
        f"{subject} seit Mitternacht nicht {participle} wurde",
        f"{sentence_initial(subject)} wurde heute{clock} nicht {participle}.",
    )


_COMPLEMENT: dict[SemanticState, SemanticState] = {
    SemanticState.ON: SemanticState.OFF, SemanticState.OFF: SemanticState.ON,
    SemanticState.OPEN: SemanticState.CLOSED, SemanticState.CLOSED: SemanticState.OPEN,
}


def _with_duration(phrase: StateEventPhrase, trigger: TriggerModel) -> StateEventPhrase:
    """"das Küchenfenster länger als 10 Minuten offen ist"."""
    assert trigger.state is not None and trigger.for_seconds is not None
    participle = _PARTICIPLES.get(trigger.state, "")
    adjective = _STATE_ADJECTIVES[trigger.state]
    duration = spoken_duration(int(trigger.for_seconds))
    suffix = f" {participle} wird"
    if not phrase.subordinate.endswith(suffix):
        return phrase
    subject = phrase.subordinate[: -len(suffix)]
    return StateEventPhrase(
        f"{subject} länger als {duration} {adjective} ist",
        f"{sentence_initial(subject)} ist {_since(duration)} {adjective}.",
    )


def describe_holding_state(
    trigger: TriggerModel, entities: Sequence[EntitySnapshot]
) -> StateEventPhrase | None:
    """"ein Fenster offen ist" / "Ein Fenster ist offen." - the state itself,
    not the moment it began."""
    phrase = describe_state_event(trigger, entities)
    if phrase is None or trigger.state not in _STATE_ADJECTIVES:
        return None
    suffix = f" {_PARTICIPLES.get(trigger.state, '')} wird"
    if not phrase.subordinate.endswith(suffix):
        return None
    subject = phrase.subordinate[: -len(suffix)]
    adjective = _STATE_ADJECTIVES[trigger.state]
    return StateEventPhrase(
        f"{subject} {adjective} ist", f"{sentence_initial(subject)} ist {adjective}."
    )


# Plural noun per device class / domain for a whole set ("alle Fenster", 7.9 W1).
_PLURAL_NOUNS: dict[tuple[str, str | None], str] = {
    ("binary_sensor", "window"): "Fenster",
    ("binary_sensor", "door"): "Türen",
    ("binary_sensor", "garage_door"): "Garagentore",
    ("cover", "garage"): "Garagentore",
    ("cover", None): "Rollläden",
    ("light", None): "Lichter",
    ("switch", None): "Schalter",
    ("fan", None): "Ventilatoren",
    ("lock", None): "Schlösser",
}


def describe_whole_set_state(
    trigger: TriggerModel, entities: Sequence[EntitySnapshot]
) -> StateEventPhrase | None:
    """"alle 6 Fenster im Obergeschoss geschlossen sind" / "Alle 6 Fenster im
    Obergeschoss sind geschlossen." - the count is always spoken, so a set
    larger than expected is visible before the "Ja"."""
    target = trigger.target
    if target is None or trigger.state not in _STATE_ADJECTIVES:
        return None
    matches = _matching_entities(target, entities)
    if len(matches) < 2:
        return None
    classes = {(item.domain, item.device_class) for item in matches}
    domains = {domain for domain, _ in classes}
    noun = (
        _PLURAL_NOUNS.get(next(iter(classes))) if len(classes) == 1 else None
    ) or (_PLURAL_NOUNS.get((next(iter(domains)), None)) if len(domains) == 1 else None) or "Geräte"
    location = _location(target, entities) or _shared_location(matches)
    subject = f"alle {len(matches)} {noun}" + (f" {location}" if location else "")
    adjective = _STATE_ADJECTIVES[trigger.state]
    return StateEventPhrase(
        f"{subject} {adjective} sind", f"{sentence_initial(subject)} sind {adjective}."
    )


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
    if trigger.for_seconds:
        # "länger als 5 Minuten über 3000 W" - the duration is part of the
        # meaning and is always said (7.9 W4).
        duration = spoken_duration(int(trigger.for_seconds))
        return StateEventPhrase(
            f"{subject} {direction}länger als {duration} {measured} liegt",
            f"{sentence_initial(subject)} liegt {direction}seit {duration} {measured}."
            if not duration.endswith("Tage")
            else f"{sentence_initial(subject)} liegt {direction}seit {duration}n {measured}.",
        )
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


_TRIGGER_CONNECTORS = frozenset({"wenn", "sobald", "falls"})


def message_from_trigger_text(trigger_text: str) -> str:
    """Grammatical fallback when only the spoken trigger clause is known."""
    words = trigger_text.strip().strip(" ,.!?").split()
    if words and words[0].casefold() in _TRIGGER_CONNECTORS:
        words = words[1:]
    content = " ".join(words).strip(" ,.!?")
    if not content:
        return "Auslöser eingetreten."
    words = content.split()
    if len(words) >= 3 and words[-1].casefold() in {"wird", "werden"}:
        aux = "wurde" if words[-1].casefold() == "wird" else "wurden"
        return _as_sentence(f"{' '.join(words[:-2])} {aux} {words[-2]}")
    return message_from_dass_content(content) or "Auslöser eingetreten."


__all__ = (
    "DEFAULT_NOTIFICATION_MESSAGE",
    "describe_holding_state",
    "describe_inactivity",
    "describe_unchanged_today",
    "describe_whole_set_state",
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
