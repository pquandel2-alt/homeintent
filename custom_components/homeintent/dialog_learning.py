"""Learning the household's language from the dialog (7.4.1).

Pure, deterministic building blocks; the conversation agent only glues them
to the dialog and to the ``BindingStore``. Nothing here executes anything or
decides about execution:

* an unknown device noun is asked about ("Was meinst du mit ‚X‘?") and may be
  stored as an alias afterwards;
* the same clarification answered the same way twice offers a default choice;
* activity preferences ("Wenn ich lese, möchte ich die Stehlampe auf 60
  Prozent") are stored as a *command sentence* and replayed through the whole
  pipeline (validator, EffectGraph, policy) when the activity is announced;
* speech macros ("Wenn ich ‚Kinoabend‘ sage, dann …") are stored the same way;
* "Was weißt du über mich?", "Wie hell möchte ich lesen?" and "Vergiss …" are
  answered from the bindings.

Every binding still requires an explicit "Ja" (``BindingStore``) and only
becomes a meaning building block: aliases are lexicon entries of the one
target resolution, macros and preferences are sentences.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .bindings import Binding, BindingKind, BindingScope, BindingState, normalize_key
from .entities import EntitySnapshot, normalize_for_compare

# ---------------------------------------------------------------------------
# Activities ("Ich lese jetzt", "zum Lesen", "beim Fernsehen").


@dataclass(frozen=True)
class Activity:
    key: str
    noun: str  # "Lesen"
    forms: tuple[str, ...]  # normalized surface forms (verb and noun)


ACTIVITIES: tuple[Activity, ...] = (
    Activity("lesen", "Lesen", ("lese", "lesen", "liest")),
    Activity("fernsehen", "Fernsehen", ("fernsehe", "fernsehen", "sehe fern", "schaue fern", "gucke fern", "fernschauen", "fernsehschauen")),
    Activity("arbeiten", "Arbeiten", ("arbeite", "arbeiten")),
    Activity("kochen", "Kochen", ("koche", "kochen")),
    Activity("essen", "Essen", ("esse", "essen")),
    Activity("entspannen", "Entspannen", ("entspanne", "entspannen")),
    Activity("musik", "Musikhören", ("hoere musik", "musik hoeren", "musikhoeren")),
    Activity("zocken", "Zocken", ("zocke", "zocken", "spiele", "spielen")),
)
_ACTIVITY_BY_KEY = {activity.key: activity for activity in ACTIVITIES}


def activity_label(key: str) -> str:
    activity = _ACTIVITY_BY_KEY.get(key)
    return activity.noun if activity is not None else key.capitalize()


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s%]", " ", normalize_for_compare(text)).split())


def _find_activity(text: str) -> Activity | None:
    padded = f" {text} "
    found = [
        activity for activity in ACTIVITIES
        if any(f" {form} " in padded for form in activity.forms)
    ]
    return found[0] if len(found) == 1 else None


def parse_activity_announcement(text: str) -> str | None:
    """"Ich lese jetzt." / "Ich will lesen." / "Ich schaue jetzt fern." -> key.

    Only a first-person statement consisting of the activity (plus modal
    verb and time particles) counts; questions and longer sentences do not.
    """
    if text.rstrip().endswith("?"):
        return None
    normalized = _norm(text)
    words = normalized.split()
    if not words or words[0] != "ich" or len(words) > 6:
        return None
    rest = " ".join(
        word for word in words[1:]
        if word not in {"will", "moechte", "werde", "jetzt", "gleich", "nun", "mal", "ein", "bisschen", "etwas", "gehe", "fange", "an", "zu", "bitte", "dann"}
    )
    activity = _find_activity(rest)
    if activity is None:
        return None
    if rest not in activity.forms:
        return None
    return activity.key


# ---------------------------------------------------------------------------
# Preferences: "Wenn ich lese, möchte ich die Stehlampe auf 60 Prozent."


@dataclass(frozen=True)
class PreferenceDraft:
    activity: str
    entity: EntitySnapshot
    command: str  # the sentence replayed through the pipeline
    brightness: int | None = None
    state: str | None = None  # "on" / "off"


_PREF_RE = re.compile(
    r"^(?:merke?\s+dir\s*,?\s*(?:dass\s+)?)?(?:"
    r"(?:wenn|sobald)\s+ich\s+(?P<act1>.+?)\s*,?\s+(?:moechte|will|mag|haette\s+gern)\s+ich\s+(?P<body1>.+)"
    r"|(?:zum|beim|fuers|fuer\s+das)\s+(?P<act2>\w+)\s+(?:moechte|will|mag)\s+ich\s+(?P<body2>.+)"
    r"|ich\s+(?:moechte|will|mag)\s+(?:zum|beim|fuers|fuer\s+das)\s+(?P<act3>\w+)\s+(?P<body3>.+)"
    r")$"
)
_PERCENT_RE = re.compile(r"\b(?:auf\s+)?(\d{1,3})\s*(?:prozent|%)")
_OFF_RE = re.compile(r"\b(?:aus|ausgeschaltet|dunkel)\s*(?:haben)?$")
_ON_RE = re.compile(r"\b(?:an|ein|eingeschaltet)\s*(?:haben)?$")


def parse_preference_statement(
    text: str, entities: Sequence[EntitySnapshot], resolve: object
) -> PreferenceDraft | None:
    """A remembered setting for an activity (never executed here)."""
    match = _PREF_RE.match(_norm(text))
    if match is None:
        return None
    raw_activity = match.group("act1") or match.group("act2") or match.group("act3") or ""
    body = match.group("body1") or match.group("body2") or match.group("body3") or ""
    activity = _find_activity(raw_activity)
    if activity is None:
        return None
    percent = _PERCENT_RE.search(body)
    state = None
    if percent is None:
        state = "off" if _OFF_RE.search(body) else "on" if _ON_RE.search(body) else None
        if state is None:
            return None
    value = int(percent.group(1)) if percent is not None else None
    if value is not None and not 0 <= value <= 100:
        return None
    phrase = body[: percent.start()] if percent is not None else re.sub(r"\s+(?:an|ein|aus)\s*(?:haben)?$", "", body)
    phrase = re.sub(r"^(?:das|die|den|der|meine?n?)\s+", "", phrase.strip())
    phrase = re.sub(r"\s+(?:haben|stehen|sein)$", "", phrase)
    if not phrase:
        return None
    result = resolve(phrase, list(entities))  # type: ignore[operator]
    entity = getattr(result, "entity", None)
    if entity is None:
        return None
    if value is not None:
        command = f"Stelle {entity.friendly_name} auf {value} Prozent."
    else:
        command = f"Schalte {entity.friendly_name} {'ein' if state == 'on' else 'aus'}."
    return PreferenceDraft(activity.key, entity, command, value, state)


_PREF_QUESTION_RE = re.compile(
    r"^wie\s+(?:hell|dunkel|warm|laut)\s+(?:moechte|will|mag|habe)\s+ich\s+(?:es\s+)?"
    r"(?:(?:beim|zum|fuers|fuer\s+das)\s+)?(?P<act>\w+(?:\s+\w+)?)"
)


def parse_preference_question(text: str) -> str | None:
    """"Wie hell möchte ich lesen?" / "Wie hell mag ich es beim Lesen?"."""
    match = _PREF_QUESTION_RE.match(_norm(text))
    if match is None:
        return None
    activity = _find_activity(match.group("act"))
    return activity.key if activity is not None else None


# ---------------------------------------------------------------------------
# Speech macros: "Wenn ich ‚Kinoabend‘ sage, dann mach … und …".

_MACRO_RE = re.compile(
    r"^\s*wenn\s+ich\s+[„\"'‚»«]?(?P<name>[^„“\"'‚‘»«,]+?)[“\"'‘»«]?\s+sage\s*,?\s*(?:dann\s+)?(?P<body>.+?)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_MACRO_FILLERS = {"bitte", "starte", "start", "los", "aktiviere", "mach", "jetzt", "modus", "zeit", "fuer", "fur", "den", "die", "das"}


@dataclass(frozen=True)
class MacroDraft:
    name: str
    body: str


def parse_macro_definition(text: str) -> MacroDraft | None:
    match = _MACRO_RE.match(text)
    if match is None:
        return None
    name = match.group("name").strip()
    body = match.group("body").strip()
    if not 2 <= len(name) <= 40 or len(name.split()) > 3 or len(body.split()) < 2:
        return None
    body = body[0].upper() + body[1:]
    if not body.endswith((".", "!")):
        body += "."
    return MacroDraft(name, body)


def macro_invoked(text: str, macros: Iterable[Binding]) -> Binding | None:
    """"Kinoabend." / "Kinoabend bitte!" / "Starte Kinoabend." -> macro."""
    words = [word for word in normalize_key(text).split() if word not in _MACRO_FILLERS]
    spoken = " ".join(words)
    if not spoken or len(words) > 3:
        return None
    return next((macro for macro in macros if macro.key == spoken), None)


# ---------------------------------------------------------------------------
# "Was weißt du über mich?", "Vergiss …".

_ABOUT_ME_RE = re.compile(
    r"^(?:was\s+(?:weisst|kennst)\s+du\s+(?:alles\s+)?(?:ueber|von)\s+mich"
    r"|was\s+hast\s+du\s+dir\s+(?:(?:ueber|von)\s+mi(?:ch|r)\s+)?gemerkt"
    r"|welche\s+(?:namen|vorlieben|makros|sprachmakros|standardauswahlen)\s+(?:kennst|hast)\s+du)\b"
)
_LEARNED_RE = re.compile(r"^was\s+hast\s+du\s+(?:(?:ueber|von)\s+mi(?:ch|r)\s+)?gelernt\b")


def is_about_me_question(text: str) -> bool:
    return _ABOUT_ME_RE.match(_norm(text)) is not None


def is_learned_question(text: str) -> bool:
    """"Was hast du gelernt?" - the V11 model overview answers it when the
    long-term learning is set up; otherwise the bindings do."""
    return _LEARNED_RE.match(_norm(text)) is not None


_FORGET_RE = re.compile(
    r"^(?:bitte\s+)?(?:vergiss|loesche|entferne)\s+(?:bitte\s+)?"
    r"(?:(?:den\s+namen|das\s+wort|den\s+alias|das\s+makro|das\s+sprachmakro|die\s+standardauswahl(?:\s+fuer)?|meine\s+vorliebe(?:\s+(?:zum|beim|fuers|fuer\s+das))?)\s+)?"
    r"(?P<rest>.+?)(?:\s+wieder)?$"
)
_FORGET_HOW_RE = re.compile(r"^(?:wie|wann)\s+\w+\s+ich\s+(?:es\s+)?(?:beim\s+|zum\s+)?(?P<act>.+?)(?:\s+(?:moechte|will|mag))?$")


@dataclass(frozen=True)
class ForgetRequest:
    key: str  # normalized binding key or activity key
    kinds: tuple[BindingKind, ...]


def parse_forget(text: str) -> ForgetRequest | None:
    """"Vergiss den Namen Kuschelecke." / "Vergiss, wie hell ich lesen möchte."."""
    normalized = _norm(text)
    match = _FORGET_RE.match(normalized)
    if match is None:
        return None
    rest = match.group("rest").strip()
    how = _FORGET_HOW_RE.match(rest)
    if how is not None:
        activity = _find_activity(how.group("act"))
        return ForgetRequest(activity.key, (BindingKind.PREFERENCE,)) if activity else None
    if "vorliebe" in normalized:
        activity = _find_activity(rest)
        return ForgetRequest(activity.key, (BindingKind.PREFERENCE,)) if activity else None
    if rest in {"alles", "es", "das", "mich"} or len(rest.split()) > 3:
        return None
    # "Vergiss die Sonnenlampe": the article is not part of the name (7.8 B7).
    words = rest.split()
    while len(words) > 1 and words[0] in {"der", "die", "das", "den", "dem", "mein", "meine", "meinen"}:
        words = words[1:]
    rest = " ".join(words)
    return ForgetRequest(normalize_key(rest), (BindingKind.ALIAS, BindingKind.MACRO, BindingKind.DEFAULT_CHOICE))


# ---------------------------------------------------------------------------
# Unknown device nouns: "Schalte den Zauberkasten aus."

_ACTION_WORDS = {
    "schalte", "schalt", "mach", "mache", "dreh", "drehe", "stell", "stelle", "fahr", "fahre",
    "oeffne", "schliesse", "starte", "stoppe", "dimme", "aktiviere", "deaktiviere",
}
_FUNCTION_WORDS = {
    "den", "die", "das", "der", "dem", "des", "ein", "eine", "einen", "mein", "meine", "meinen",
    "bitte", "mal", "an", "aus", "ein", "auf", "zu", "hoch", "runter", "im", "in", "am", "und",
    "jetzt", "doch", "kurz", "wieder", "ganz", "prozent", "grad",
}


def unknown_device_noun(
    text: str,
    entities: Sequence[EntitySnapshot],
    known_word: object,
) -> str | None:
    """The single capitalised word of a command HomeIntent does not know.

    ``known_word(word) -> bool`` answers from the lexicon (genus words, place
    words, grammar); an entity or area name is always known.
    """
    raw = [word.strip(",.;:!?\"„“‚‘'") for word in text.split()]
    if not raw or normalize_for_compare(raw[0]) not in _ACTION_WORDS:
        return None
    lowered = f" {normalize_for_compare(text)} "
    if any(joint in lowered for joint in (" und ", " oder ", " dann ", " ausser ", " sonst ")) or "," in text:
        return None  # one clause only: a multi-command names its unclear part
    known_names = {
        word
        for entity in entities
        for name in (entity.friendly_name, *entity.aliases, entity.area_name or "", *entity.area_aliases)
        for word in normalize_for_compare(name).replace("-", " ").split()
    }
    unknown = [
        word for word in raw[1:]
        if word[:1].isupper() and len(word) >= 3
        and (key := normalize_for_compare(word)) not in known_names
        and key not in _FUNCTION_WORDS
        and not re.fullmatch(r"\d+", key)
        and not known_word(word)  # type: ignore[operator]
    ]
    return unknown[0] if len(unknown) == 1 else None


def replace_word(text: str, word: str, replacement: str) -> str:
    """The sentence with the unknown word replaced by the chosen device name."""
    return re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", replacement, text, count=1)


# ---------------------------------------------------------------------------
# Safety rules for learning an alias.

CRITICAL_DOMAINS = frozenset({"lock", "alarm_control_panel", "siren", "valve"})
CRITICAL_DEVICE_CLASSES = frozenset({"garage", "garage_door", "gate", "door", "lock"})


def is_critical_target(entity: EntitySnapshot) -> bool:
    return entity.domain in CRITICAL_DOMAINS or (entity.device_class or "") in CRITICAL_DEVICE_CLASSES


def alias_rejection(
    alias: str,
    entity: EntitySnapshot,
    entities: Sequence[EntitySnapshot],
    *,
    is_admin: bool,
    genus_word: object,
) -> str | None:
    """Why ``alias`` may not become a name for ``entity`` (``None``: allowed)."""
    key = normalize_for_compare(alias)
    if not 2 <= len(key) <= 40:
        return "Dieses Wort kann ich mir nicht als Namen merken."
    for other in entities:
        names = {normalize_for_compare(name) for name in (other.friendly_name, *other.aliases)}
        if key in names and other.entity_id != entity.entity_id:
            return f"„{alias}“ ist schon der Name von {other.friendly_name}. Ich überschreibe keine Gerätenamen."
        if other.area_name and key == normalize_for_compare(other.area_name):
            return f"„{alias}“ ist schon ein Raumname. Ich überschreibe keine Raumnamen."
    if genus_word(alias):  # type: ignore[operator]
        return f"„{alias}“ ist schon eine Gerätegattung. Ich überschreibe keine Gattungsnamen."
    if is_critical_target(entity) and not is_admin:
        return f"Namen für {entity.friendly_name} dürfen nur Administratoren vergeben."
    return None


# ---------------------------------------------------------------------------
# Default choices: the same clarification answered the same way twice.

DEFAULT_CHOICE_THRESHOLD = 2


def choice_key(candidate_ids: Iterable[str], area_id: str | None) -> str:
    """The same question: the same offered candidates × place (the speaker
    is the binding's user scope)."""
    return f"{'|'.join(sorted(candidate_ids))} @ {area_id or 'ueberall'}"


def choice_label(candidates: Sequence[EntitySnapshot]) -> str:
    names = [entity.friendly_name for entity in candidates]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " oder " + names[-1]


# ---------------------------------------------------------------------------
# German descriptions.

_KIND_ORDER = (BindingKind.ALIAS, BindingKind.DEFAULT_CHOICE, BindingKind.PREFERENCE, BindingKind.MACRO, BindingKind.ROUTINE)


def describe_binding(binding: Binding, names: Mapping[str, str]) -> str:
    target = names.get(binding.target, binding.target)
    if binding.kind is BindingKind.ALIAS:
        return f"„{binding.data.get('spoken', binding.key)}“ heißt {target}"
    if binding.kind is BindingKind.DEFAULT_CHOICE:
        spoken = str(binding.data.get("spoken", ""))
        place = binding.data.get("area_name")
        where = f" im Raum {place}" if place else ""
        return f"bei der Wahl zwischen {spoken}{where} nehme ich {target}"
    if binding.kind is BindingKind.PREFERENCE:
        return f"beim {activity_label(binding.key)}: {str(binding.data.get('command', target)).rstrip('.')}"
    if binding.kind is BindingKind.MACRO:
        return f"Sprachmakro „{binding.data.get('spoken', binding.key)}“: {str(binding.data.get('body', '')).rstrip('.')}"
    return f"Routine „{binding.key}“ ist {target}"


def describe_bindings(
    bindings: Sequence[Binding],
    names: Mapping[str, str],
    states: Mapping[str, BindingState] | None = None,
) -> str:
    """"Ich weiß über dich: …" in German, grouped by kind."""
    if not bindings:
        return (
            "Ich habe mir noch nichts über dich gemerkt. Du kannst mir zum Beispiel "
            "sagen: „Mit Kuschelecke meine ich die Stehlampe.“"
        )
    ordered = sorted(bindings, key=lambda item: (_KIND_ORDER.index(item.kind) if item.kind in _KIND_ORDER else 9, item.key))
    parts = []
    for binding in ordered[:12]:
        text = describe_binding(binding, names)
        state = (states or {}).get(binding.binding_id)
        if state is not None and state is not BindingState.VALID:
            text += " (wirkungslos: Gerät nicht mehr freigegeben)"
        parts.append(text)
    more = f" und {len(ordered) - 12} weitere Einträge" if len(ordered) > 12 else ""
    scope_note = ""
    if any(item.scope is BindingScope.HOUSEHOLD for item in bindings):
        scope_note = " Namen und Sprachmakros gelten für den ganzen Haushalt."
    return "Ich weiß: " + "; ".join(parts) + more + "." + scope_note


MEMORY_DISABLED_TEXT = (
    "Das dauerhafte Gedächtnis ist ausgeschaltet. Einschalten kannst du es in "
    "Home Assistant unter Einstellungen → Geräte & Dienste → HomeIntent → "
    "Konfigurieren (Option „Gedächtnis“). Gelernte Namen, Vorlieben und "
    "Sprachmakros funktionieren auch ohne."
)


__all__ = (
    "ACTIVITIES", "Activity", "CRITICAL_DEVICE_CLASSES", "CRITICAL_DOMAINS", "DEFAULT_CHOICE_THRESHOLD",
    "ForgetRequest", "MEMORY_DISABLED_TEXT", "MacroDraft", "PreferenceDraft", "activity_label",
    "alias_rejection", "choice_key", "choice_label", "describe_binding", "describe_bindings", "is_about_me_question", "is_learned_question",
    "is_critical_target", "macro_invoked", "parse_activity_announcement", "parse_forget",
    "parse_macro_definition", "parse_preference_question", "parse_preference_statement",
    "replace_word", "unknown_device_noun",
)
