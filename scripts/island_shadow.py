#!/usr/bin/env python3
"""Shadow comparison per language island (7.5.x).

The frames of one island are computed for the same sentences - every
published corpus sentence, every sentence-like literal of the test suite and
a generated island corpus (combinations of the island's building blocks on
the test house) - once with the historic code (a git worktree, ``--root``)
and once with the new derivation. Only an island without any difference is
switched; the historic parser is deleted in the same change.

    python scripts/island_shadow.py kalender --root /tmp/old --dump old.pkl
    python scripts/island_shadow.py kalender --dump new.pkl
    python scripts/island_shadow.py kalender --compare old.pkl new.pkl
"""

from __future__ import annotations

import argparse
import ast
import itertools
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc)


def _setup(root: Path) -> None:
    sys.path.insert(0, str(root / "custom_components"))
    sys.path.insert(0, str(SCRIPT_ROOT / "tests"))
    sys.path.insert(0, str(SCRIPT_ROOT / "scripts"))
    import _ha_stub

    _ha_stub.install()


# -- generated corpora ------------------------------------------------------
def _history_corpus(entities) -> list[str]:
    sensors = [e for e in entities if e.domain == "sensor" and e.device_class in {"temperature", "humidity", "energy", "power"}]
    states = [e for e in entities if e.domain in {"binary_sensor", "cover", "light", "media_player", "vacuum"}]
    periods = ["gestern", "vorgestern", "heute", "diese Woche", "letzte Woche", "diesen Monat",
               "in den letzten 24 Stunden", "in den letzten sieben Tagen", "zuletzt"]
    statistics = ["Durchschnitt", "Mittelwert", "Minimum", "Maximum", "niedrigste Wert", "höchste Wert",
                  "Verbrauch", "Veränderung", "Durchschnittstemperatur"]
    sentences: list[str] = []
    for sensor, period, statistic in itertools.product(sensors[:6], periods, statistics):
        sentences.append(f"Was war der {statistic} von {sensor.friendly_name} {period}?")
    for entity, period in itertools.product(states[:10], periods):
        sentences += [
            f"Wie oft war {entity.friendly_name} {period} offen?",
            f"Wie lange lief {entity.friendly_name} {period}?",
            f"Wann war {entity.friendly_name} zuletzt an?",
            f"War {entity.friendly_name} {period} geöffnet?",
            f"Wie lange war {entity.friendly_name} {period} an?",
        ]
    for sensor in sensors[:6]:
        sentences += [
            f"War {sensor.friendly_name} heute höher als gestern?",
            f"Wie war {sensor.friendly_name} gestern im Vergleich zu vorgestern?",
            f"Wurde diese Woche mehr verbraucht als letzte Woche bei {sensor.friendly_name}?",
        ]
    return sentences + [
        "Wie warm war es gestern im Durchschnitt im Wohnzimmer?",
        "Wie kalt war es draußen letzte Nacht im Minimum?",
        "Wie viel Strom wurde heute verbraucht?",
        "Wie hoch war die Luftfeuchtigkeit im Bad im Schnitt diese Woche?",
    ]


def _management_corpus(entities) -> list[str]:
    names = [e.friendly_name for e in entities if e.domain in {"cover", "light", "binary_sensor"}][:8] + ["Küche dauerhaft", "Büro"]
    sentences: list[str] = []
    for name in names:
        sentences += [
            f"Simuliere die Automation für {name}.", f"Was würde die Automation {name} jetzt tun?",
            f"Dupliziere die Automation von {name}.", f"Kopiere Automation {name}",
            f"Warum wurde die Automation für {name} nicht ausgelöst?",
            f"Pausiere die Automation für {name} bis morgen 7 Uhr.",
            f"Wiederhole die Automation für {name} nur dreimal.",
            f"Verschiebe den Auftrag für {name} auf 21:15 Uhr.",
            f"Wann wird {name} geschaltet?", f"Welche Automation steuert {name}?", f"Was steuert {name}?",
            f"Zeige die Details der Automation zu {name}", f"Erkläre mir die Automation für {name}",
            f"Was passiert, wenn {name} geöffnet wird?", f"Welche Automationen reagieren, sobald {name} aus wird?",
            f"Zeige HomeIntent-Automationen im {name}",
        ]
    return sentences + [
        "Pausiere die Automation bis 22:30", "Wiederhole die Automation nur 3 mal", "Verschiebe die Automation auf 7 Uhr",
        "Wie viele HomeIntent-Automationen sind aktiv?", "Wie viele HomeIntent Automationen sind deaktiviert?",
        "Lösche alle abgelaufenen Aufträge.", "Mach die letzte HomeIntent-Automationsänderung rückgängig.",
        "Nimm die letzte Automationsänderung zurück", "Welche einmaligen Aufträge sind noch geplant?",
        "Welche Automationen sind geplant?", "Zeige nur HomeIntent-Automationen",
        "Welche HomeIntent-Automationen gibt es in der Küche?",
        "Pausiere die Automation bis 25 Uhr", "Verschiebe den Auftrag auf 25 Uhr",
        "Was passiert, wenn die Bewegung im Flur erkannt wird?",
    ]


def _lists_corpus(entities) -> list[str]:
    lists = [e.friendly_name for e in entities if e.domain == "todo"] or ["Einkaufsliste"]
    items = ["Milch", "Brot und Butter", "Äpfel, Birnen und Bananen"]
    sentences: list[str] = []
    for name, item in itertools.product(lists, items):
        sentences += [
            f"Setze {item} auf die {name}.", f"Füge {item} zur {name} hinzu.", f"Schreib {item} auf meine {name}.",
            f"Hake {item} auf der {name} ab.", f"Markiere {item} als erledigt.", f"Entferne {item} von der {name}.",
            f"Was steht auf der {name}?", f"Was fehlt auf meiner {name}?", f"Zeig mir die {name}.",
            f"Welche Sachen stehen auf der {name}?", f"Lösche alle erledigten Einträge der {name}.",
            f"Setze {item} mit der Beschreibung für Sonntag auf die {name}.",
            f"Setze {item} mit Priorität hoch bis morgen auf die {name}.",
        ]
    return sentences + [
        "Stelle einen Timer auf 5 Minuten.", "Stelle einen Timer für 10 Minuten mit dem Namen Nudeln.",
        "Starte einen Eiertimer für 7 Minuten.", "Timer 3 Minuten für Tee.", "Welche Timer laufen?",
        "Zeige alle Timer.", "Brich den Nudeltimer ab.", "Wie lange läuft der Timer noch?",
        "Pausiere den Timer.", "Setze den Timer fort.", "Verlängere den Timer um 2 Minuten.",
        "Stoppe alle Timer.", "Lösche den Timer Nudeln.", "Er soll Nudeln heißen.", "Der Timer heißt Tee.",
        "Ohne Namen.", "Egal.", "Nenne ihn Pizza.",
    ]


def _calendar_corpus(entities) -> list[str]:
    titles = ["Zahnarzt", "Elternabend", "Fußballtraining"]
    sentences: list[str] = []
    for title in titles:
        sentences += [
            f"Wann ist mein {title}?", f"Wann ist mein {title}termin?", f"Benenne den Termin {title} in Arzt um.",
            f"Ändere die Dauer vom Termin {title} auf 2 Stunden.", f"Verschiebe den Termin {title} auf morgen 15 Uhr.",
            f"Verlege den Kalendertermin {title} auf Freitag.",
        ]
    return sentences + [
        "Was steht morgen in meinem Kalender?", "Welche Termine habe ich heute?", "Was habe ich übermorgen vor?",
        "Zeig mir meine Termine für diese Woche.", "Habe ich morgen zwischen 14 und 16 Uhr Zeit?",
        "Bin ich am Freitag frei?", "Hab ich heute von 9 bis 11 Uhr Zeit?",
        "Der Titel ist Zahnarzt.", "Der Name soll Elternabend sein.", "Nenne ihn Training.",
        "Trag morgen um 10 Uhr ganztägig Urlaub ein.", "Termin morgen für eine halbe Stunde Arzt.",
    ]


def _household_corpus(entities) -> list[str]:
    places = ["im Wohnzimmer", "in der Küche", "im Büro", "oben", "im Erdgeschoss"]
    sentences = [f"Was ist gerade {place} an?" for place in places] + [f"Und was läuft noch {place}?" for place in places]
    return sentences + [
        "Wie spät ist es?", "Wieviel Uhr ist es?", "Welche Uhrzeit haben wir?", "Welches Datum ist heute?",
        "Welcher Tag ist heute?", "Was haben wir heute für einen Tag?", "Was kannst du?", "Wer ist zu Hause?",
        "Wer ist daheim?", "Ist Anna zu Hause?", "Ist Philipp daheim?",
        "Wie viel Strom verbraucht das Haus gerade?", "Wie viel Energie zieht der Haushalt?",
        "Wie ist die durchschnittliche Temperatur im ganzen Haus?", "Auf wie viel Grad ist die Heizung im Bad eingestellt?",
        "Welche Solltemperatur hat das Büro?", "Gibt es Probleme im Haus?", "Gibt es Störungen zu Hause?",
        "Welche Batterien sind unter 20 Prozent?", "Zeige Batterien mit weniger als 30 Prozent.",
        "Wann geht die Sonne auf?", "Wann geht die Sonne unter?", "Wie ist das Wetter?",
        "Wie warm ist es draußen?", "Wie wird das Wetter morgen?", "Welche Szenen gibt es?",
        "Welche Skripte sind verfügbar?", "Wann ist die Waschmaschine fertig?", "Wann wird der Trockner fertig?",
    ]


def _procedure_corpus(entities) -> list[str]:
    return [
        "Speichere diesen Plan als Feierabend.", "Merk dir den Plan als Morgens.",
        "Führe die Prozedur Feierabend aus.", "Starte die Routine Morgens.", "Starte das Szenario Urlaub.",
        "Vergiss die Prozedur Feierabend.", "Lösche die Routine Morgens.",
        "Welche Prozeduren kennst du?", "Was für Szenarien hast du?", "Welche benannten Routinen kennst du?",
    ]


def _reminder_corpus(entities) -> list[str]:
    return [
        "Sag Anna morgen um 8 Bescheid, dass der Müll raus muss.",
        "Sag mir heute Abend Bescheid über den Termin.",
        "Benachrichtige mich morgen früh, dass die Pflanzen Wasser brauchen.",
        "Informiere uns am Freitag, dass die Heizung gewartet wird.",
        "Benachrichtige mich, wenn die Waschmaschine fertig ist, aber nicht zwischen 22 und 7 Uhr.",
        "Sag Philipp Bescheid, wenn das Fenster offen ist, außerhalb der Ruhezeit von 23 bis 6 Uhr.",
    ]


# -- island functions -------------------------------------------------------
def _island(name: str, entities):
    if name == "verlauf":
        from homeintent.history_query import parse_history_query

        return (lambda text: parse_history_query(text, entities, NOW)), _history_corpus(entities)
    if name == "automationsverwaltung":
        from homeintent.automation_management import parse_automation_management

        return parse_automation_management, _management_corpus(entities)
    if name == "listen_timer":
        from homeintent.productivity import parse_productivity_request, timer_name_reply

        return (lambda text: (parse_productivity_request(text, entities, NOW), timer_name_reply(text))), _lists_corpus(entities)
    if name == "kalender":
        from homeintent.calendar_event import parse_calendar_date
        from homeintent.calendar_management import parse_calendar_management

        calendars = tuple(e for e in entities if e.domain == "calendar")
        return (
            lambda text: (parse_calendar_management(text, calendars, NOW), parse_calendar_date(text, NOW))
        ), _calendar_corpus(entities)
    if name == "haushalt":
        from homeintent.household_query import match_household_query

        return (lambda text: match_household_query(text, entities, NOW)), _household_corpus(entities)
    if name == "ziele":
        from homeintent.goal_intent import interpret_goal
        from homeintent.nlu.language_frontend import analyse_language
        from homeintent.procedure_intent import interpret_procedure_intent

        def goals(text):
            document = analyse_language(text, entities)
            return (interpret_procedure_intent(document), interpret_goal(document))

        return goals, _procedure_corpus(entities)
    if name == "erinnerung":
        from homeintent.reminder import reminder_automation_text, reminder_quiet_hours, reminder_recipient

        return (
            lambda text: (reminder_automation_text(text), reminder_recipient(text), reminder_quiet_hours(text))
        ), _reminder_corpus(entities)
    raise KeyError(name)


ISLANDS = ("verlauf", "automationsverwaltung", "listen_timer", "kalender", "haushalt", "ziele", "erinnerung")


def test_strings() -> list[str]:
    """Every sentence-like string literal of the test suite."""
    found: list[str] = []
    for path in sorted((SCRIPT_ROOT / "tests").glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value.strip()
                if 1 <= len(value.split()) <= 25 and value[:1].isupper() and "\n" not in value:
                    found.append(value)
    return found


def dump(name: str) -> dict[str, str]:
    import shadow_compare
    from _testhaus import house_entities

    entities = house_entities()
    function, generated = _island(name, entities)
    sentences = [text for _source, text in shadow_compare.corpus()] + generated + test_strings()
    results: dict[str, str] = {}
    for text in dict.fromkeys(sentences):
        try:
            results[text] = repr(function(text))
        except Exception as err:  # noqa: BLE001
            results[text] = f"ERROR {type(err).__name__}: {err}"
    return results


def compare(old: dict[str, str], new: dict[str, str]) -> list[str]:
    return [
        f"{text!r}\n   alt={old[text][:300]}\n   neu={new.get(text, '<fehlt>')[:300]}"
        for text in old if old[text] != new.get(text)
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("island", choices=ISLANDS)
    parser.add_argument("--root", type=Path, default=SCRIPT_ROOT)
    parser.add_argument("--dump", type=Path)
    parser.add_argument("--compare", nargs=2, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.compare:
        old = pickle.loads(args.compare[0].read_bytes())
        new = pickle.loads(args.compare[1].read_bytes())
        differences = compare(old, new)
        empty = {"None", "(None, None)", "(None, None, None)"}
        active = sum(1 for value in new.values() if value not in empty)
        print(f"{args.island}: {len(old)} Sätze, {active} mit Frame, {len(differences)} Abweichungen")
        for line in differences[:30]:
            print("  ", line)
        return 1 if args.check and differences else 0
    _setup(args.root)
    results = dump(args.island)
    if args.dump:
        args.dump.write_bytes(pickle.dumps(results))
    print(f"{args.island}: {len(results)} Sätze")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
