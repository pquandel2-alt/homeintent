"""Reports pushed by an automation (7.9.3 A6 and B6).

* A6 – "Schick mir beim Nachhausekommen eine Zusammenfassung": when the
  speaker's ``person`` arrives home, the summary of their absence
  (``event_summary``) is built *at run time* and pushed.
* B6 – "Schick mir jeden Sonntag um 18 Uhr einen Haus-Bericht": a weekly
  report – weak batteries, unreachable devices, the week's consumption,
  the week's warnings and monitors that fired, findings of the monitor
  runtime – short in the push, in full on request ("Zeig mir den
  Haus-Bericht", "Was stand im Haus-Bericht?").

Both are ordinary Home Assistant automations created after preview and
"Ja" (owner or household, like monitors).  Their only action is
``homeintent.send_report`` with nothing but a ``report_id``.  That service
looks the report up in HomeIntent's own store (owner, kind, person),
resolves the recipients *at run time* through the same resolver and
binding as every monitor (the owner's own devices, or the confirmed
household for a shared report) and sends one push through the existing
delivery boundary (``AgentDelivery.async_send_notification``).  It never
switches a device and cannot be pointed at another recipient: a report id
that is unknown, or whose owner has no bound device, sends nothing.

The language is constructions over closed word classes; the config is
built from validated entity ids and a generated id only.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .entities import EntitySnapshot, normalize_for_compare

__all__ = (
    "REPORT_ACTION",
    "ReportRecord",
    "ReportRequest",
    "ReportStore",
    "build_config",
    "describe_request",
    "parse_report_request",
    "parse_report_show",
    "render_weekly",
    "validate_report_config",
)

REPORT_ACTION = "homeintent.send_report"
_WEEKDAY_STEMS = {
    "montag": "mon", "dienstag": "tue", "mittwoch": "wed", "donnerstag": "thu", "freitag": "fri",
    "samstag": "sat", "sonntag": "sun",
}
_WEEKDAY_NAMES = {value: key.capitalize() for key, value in _WEEKDAY_STEMS.items()}
_CLOCK_RE = re.compile(r"\bum\s+(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?\s*(?:uhr)?\b", re.IGNORECASE)
_SEND_VERBS = frozenset({
    "schick", "schicke", "schicken", "sende", "send", "senden", "push", "pushe", "gib", "melde", "berichte",
    "benachrichtige", "informiere", "sag", "sage",
})
_SUMMARY_WORDS = frozenset({"zusammenfassung", "ueberblick", "rueckblick"})
_REPORT_WORDS = frozenset({"hausbericht", "wochenbericht", "statusbericht", "hausstatus", "hauszustand"})
_ARRIVAL_WORDS = frozenset({
    "heimkomme", "heimkommen", "nachhausekomme", "nachhausekommen", "heimkehr", "ankunft", "ankomme",
    "ankommen", "heimgekommen", "zurueckkomme", "zurueckkommen", "heimkunft",
})
_SHOW_VERBS = frozenset({"zeig", "zeige", "lies", "lese", "vorlesen", "stand", "steht", "stehen", "sag", "sage",
                         "wiederhole", "nochmal", "details", "genauer", "ausfuehrlich"})


def _words(text: str) -> list[str]:
    folded = normalize_for_compare(text).replace("haus-bericht", "hausbericht").replace("wochen-bericht",
                                                                                         "wochenbericht")
    return "".join(char if char.isalnum() else " " for char in folded).split()


def _glued(words: Sequence[str]) -> set[str]:
    """Words plus two- and three-word compounds ("nach hause komme")."""
    found = set(words)
    for size in (2, 3):
        found.update("".join(words[index:index + size]) for index in range(len(words) - size + 1))
    return found


@dataclass(frozen=True)
class ReportRequest:
    kind: str  # "arrival" | "weekly"
    weekdays: tuple[str, ...] = ()
    hour: int | None = None
    minute: int = 0


def _report_named(words: Sequence[str]) -> bool:
    present = _glued(words)
    return bool(present & _REPORT_WORDS) or ("bericht" in present and bool(present & {"haus", "woche", "wochen"}))


def _arrival(words: Sequence[str]) -> bool:
    present = _glued(words)
    if present & _ARRIVAL_WORDS:
        return True
    return ("ich" in present or "wir" in present) and bool(present & {"zurueck", "heim", "wiederda"}) and bool(
        present & {"wenn", "sobald", "beim", "bei"}
    )


def parse_report_request(text: str) -> ReportRequest | None:
    """A push report to set up, else ``None``.

    Arrival summary: a send verb + a summary noun (or "was los war/was
    passiert ist") + the speaker's arrival.  Weekly report: a send verb +
    "Haus-Bericht/Wochenbericht" + a schedule (a weekday, "wöchentlich").
    """
    words = _words(text)
    present = set(words)
    if not words or not present & _SEND_VERBS or present & {"nicht", "kein", "keinen", "keine"}:
        return None
    summary = bool(present & _SUMMARY_WORDS) or (
        "was" in present and bool(present & {"los", "passiert", "verpasst", "geschehen"})
    )
    if summary and _arrival(words) and not _report_named(words):
        return ReportRequest("arrival")
    if not _report_named(words):
        return None
    weekdays: tuple[str, ...] = ()
    for index, word in enumerate(words):
        stem = word.removesuffix("s")
        previous = words[index - 1] if index else ""
        if stem in _WEEKDAY_STEMS and (word.endswith("s") or previous in {"jeden", "jedem", "am"}):
            weekdays = (_WEEKDAY_STEMS[stem],)
            break
    clock = _CLOCK_RE.search(normalize_for_compare(text))
    hour = int(clock.group("hour")) if clock is not None else None
    minute = int(clock.group("minute") or 0) if clock is not None else 0
    if hour is not None and not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if not weekdays and not present & {"woechentlich", "jede", "jeden", "einmal"}:
        return None
    return ReportRequest("weekly", weekdays or ("sun",), hour, minute)


def parse_report_show(text: str) -> bool:
    """"Zeig mir den Haus-Bericht", "Was stand im Haus-Bericht?"."""
    words = _words(text)
    present = set(words)
    if not _report_named(words) or present & {"jeden", "jede", "woechentlich", "sonntags", "montags", "um"}:
        return False
    return bool(present & _SHOW_VERBS) or (bool(present & {"was", "wie"}) and bool(present & {"im", "der", "den"}))


def describe_request(request: ReportRequest, recipient: str, example: str) -> str:
    """The preview: when, what, to whom, a real example of the push."""
    if request.kind == "arrival":
        return (
            f"Wenn du nach Hause kommst, schicke ich {recipient} eine Zusammenfassung deiner Abwesenheit: "
            "die wichtigsten Ereignisse mit Uhrzeit, erstellt erst in dem Moment, in dem du ankommst. "
            "Ich schalte dabei nichts. Soll ich das so einrichten?"
        )
    day = _WEEKDAY_NAMES.get(request.weekdays[0], "Sonntag") if request.weekdays else "Sonntag"
    clock = f"{request.hour}:{request.minute:02d}" if request.hour is not None else "?"
    return (
        f"Jeden {day} um {clock} Uhr schicke ich {recipient} einen Haus-Bericht: schwache Batterien, "
        "nicht erreichbare Geräte, Verbrauch der Woche, ausgelöste Warnungen und auffällige Werte – "
        f"kurz in der Nachricht, nach heutigem Stand zum Beispiel „{example}“. Die Details sage ich dir "
        "auf „Zeig mir den Haus-Bericht“. Ich schalte dabei nichts. Soll ich das so einrichten?"
    )


def build_config(report_id: str, request: ReportRequest, person_entity_id: str | None) -> dict[str, Any]:
    """The automation: arrival of the person, or the weekly time."""
    action = {"action": REPORT_ACTION, "data": {"report_id": report_id}}
    if request.kind == "arrival":
        assert person_entity_id is not None
        return {
            "alias": "Zusammenfassung beim Heimkommen",
            "description": "HomeIntent: beim Heimkommen eine Zusammenfassung der Abwesenheit per Push.",
            "triggers": [{"trigger": "state", "entity_id": person_entity_id, "to": "home",
                          "not_from": ["unknown", "unavailable"]}],
            "actions": [action],
            "mode": "single",
        }
    assert request.hour is not None
    return {
        "alias": "Haus-Bericht",
        "description": "HomeIntent: wöchentlicher Haus-Bericht per Push.",
        "triggers": [{"trigger": "time", "at": f"{request.hour:02d}:{request.minute:02d}:00"}],
        "conditions": [{"condition": "time", "weekday": list(request.weekdays)}],
        "actions": [action],
        "mode": "single",
    }


def validate_report_config(config: Mapping[str, Any], entities: Sequence[EntitySnapshot]) -> str | None:
    """The validator for report automations: exactly one action,
    ``homeintent.send_report`` with only a report id; a person trigger
    only on a known ``person``; a time trigger only with a weekday."""
    actions = config.get("actions")
    if not isinstance(actions, list) or len(actions) != 1:
        return "genau eine Aktion erwartet"
    action = actions[0]
    if not isinstance(action, Mapping) or action.get("action") != REPORT_ACTION:
        return "nicht erlaubte Aktion"
    data = action.get("data")
    if set(action) - {"action", "data"} or not isinstance(data, Mapping) or set(data) != {"report_id"}:
        return "die Aktion darf nur die Bericht-ID tragen"
    if not re.fullmatch(r"[0-9a-f]{32}", str(data.get("report_id"))):
        return "ungültige Bericht-ID"
    known = {entity.entity_id for entity in entities}
    for trigger in config.get("triggers") or ():
        kind = trigger.get("trigger") if isinstance(trigger, Mapping) else None
        if kind == "state":
            entity_id = trigger.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id.startswith("person.") or entity_id not in known:
                return "Auslöser nur über eine bekannte Person"
        elif kind != "time":
            return "nicht erlaubter Auslöser"
    return None


# --- the weekly report ---------------------------------------------------------

@dataclass(frozen=True)
class WeeklyFacts:
    batteries: tuple[tuple[str, float], ...] = ()
    unavailable: tuple[str, ...] = ()
    energy_kwh: float | None = None
    energy_source: str | None = None
    fired: tuple[tuple[str, int], ...] = ()  # monitor/automation name, runs
    findings: tuple[str, ...] = ()  # monitor runtime (warnings, notable values)
    recorder: bool = True


def _number(value: float) -> str:
    rounded = round(value, 1)
    return (f"{rounded:.1f}".rstrip("0").rstrip(".")).replace(".", ",")


def render_weekly(facts: WeeklyFacts) -> tuple[str, str]:
    """(short push text, full text for "Zeig mir den Haus-Bericht")."""
    short: list[str] = []
    full: list[str] = []
    if facts.batteries:
        short.append(f"{len(facts.batteries)} schwache Batterie{'n' if len(facts.batteries) > 1 else ''}")
        full.append("Schwache Batterien: " + ", ".join(f"{name} ({round(value)} %)" for name, value in facts.batteries))
    else:
        full.append("Keine Batterie ist schwach")
    if facts.unavailable:
        count = len(facts.unavailable)
        short.append(f"{count} Gerät{'e' if count > 1 else ''} nicht erreichbar")
        full.append("Nicht erreichbar: " + ", ".join(facts.unavailable))
    else:
        full.append("Alle Geräte sind erreichbar")
    if facts.energy_kwh is not None:
        short.append(f"{_number(facts.energy_kwh)} kWh verbraucht")
        full.append(f"Verbrauch der letzten 7 Tage: {_number(facts.energy_kwh)} kWh ({facts.energy_source})")
    elif not facts.recorder:
        full.append("Den Verbrauch kenne ich nicht (kein Verlauf)")
    runs = sum(count for _, count in facts.fired)
    if facts.fired:
        short.append(f"{runs} Meldung{'en' if runs > 1 else ''}")
        full.append("Ausgelöst: " + ", ".join(f"„{name}“ {count}-mal" for name, count in facts.fired))
    else:
        full.append("Keine Überwachung hat ausgelöst" if facts.recorder else "Ausgelöste Meldungen kenne ich nicht (kein Verlauf)")
    if facts.findings:
        short.append(f"{len(facts.findings)} Auffälligkeit{'en' if len(facts.findings) > 1 else ''}")
        full.append("Auffällig: " + "; ".join(facts.findings))
    head = "Haus-Bericht: " + (", ".join(short) if short else "alles in Ordnung") + "."
    return head + " Details: frag mich „Zeig mir den Haus-Bericht“.", "Haus-Bericht: " + ". ".join(full) + "."


# --- store ---------------------------------------------------------------------

@dataclass
class ReportRecord:
    report_id: str
    kind: str
    owner_user_id: str | None
    shared: bool = False
    person_entity_id: str | None = None
    automation_id: str | None = None
    last_short: str = ""
    last_full: str = ""
    last_sent: str = ""


@dataclass
class ReportStore:
    """HomeIntent's report records (JSON file, read/written in the executor)."""

    path: str
    records: dict[str, ReportRecord] = field(default_factory=dict)
    loaded: bool = False

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (FileNotFoundError, ValueError):
            raw = {}
        self.records = {
            key: ReportRecord(**{name: value[name] for name in ReportRecord.__dataclass_fields__ if name in value})
            for key, value in (raw.items() if isinstance(raw, dict) else ())
            if isinstance(value, dict) and "report_id" in value and "kind" in value
        }
        self.loaded = True

    def save(self) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=directory, prefix=".reports-")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({key: asdict(value) for key, value in self.records.items()}, stream, ensure_ascii=False)
        os.replace(temp, self.path)


def new_report_id() -> str:
    return uuid.uuid4().hex
