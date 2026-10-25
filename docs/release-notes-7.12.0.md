# HomeIntent 7.12.0

## Automationsdialog

- Eigener typisierter, flüchtiger Slot-Draft für unvollständige zeitliche und
  solare Automationswünsche; Isolation nach Benutzer, Conversation,
  Satellit/Gerät und TTL.
- Schrittweises Füllen von Trigger, Ziel, Aktion und Wert; explizite
  Bedingungen sind typisiert, aber nicht pauschal Pflicht.
- Ziel-, Zeit- und Wertkorrekturen erzeugen eine neue Vorschau. Echte
  Entity-Mehrdeutigkeiten bleiben auf die angebotenen Kandidaten begrenzt;
  unbekannte bedeutungstragende Wörter bleiben bis zur Klärung erhalten.
  Abbruch, Ablauf, Themenwechsel, fremde oder anonyme Antworten schreiben
  nichts.
- Vollständige Entwürfe verwenden unverändert Grounding, Capability-Prüfung,
  Validator, Execution Policy, Vorschau, Bestätigung und AutomationExecutor.

## Delta-History und Undo

- Schema 3 speichert bei neuen Einträgen ausschließlich Zielautomation und
  Zielmetadaten vor/nach der Änderung samt Positionen und kanonischen Digests.
- Pause/Fortsetzen verwenden atomare geordnete Mehrziel-Deltas für Ziel und
  Resume-Helper. Eine Abweichung blockiert den gesamten Undo-Vorgang.
- Fremde YAML-/Metadatenänderungen bleiben erhalten. Schema 1/2 wird nur
  beweiskräftig migriert oder fail-closed abgelehnt.
- Alte nicht unterstützte zusammengesetzte LIFO-Köpfe können ausschließlich
  von einem angemeldeten Administrator nach eigener Bestätigung verworfen
  werden; Automationen und Metadaten bleiben dabei unverändert.

## Music Assistant

- Mehrere Instanzen werden über expliziten Player/Gruppenplayer, eindeutigen
  Raum, Satellitenbereich oder genau eine verfügbare Instanz aufgelöst.
- Bei Mehrdeutigkeit folgt eine benutzergebundene Player-/Raumrückfrage; Suche,
  Bestätigung und Wiedergabe bleiben auf derselben `config_entry_id`.
- Echte HA-Komponenten- und Service-Schemas sind automatisiert geprüft. Ein
  echter MA-Server und Audioplayer wurden nicht automatisiert gestartet; reale
  Wiedergabe wird nicht behauptet. Die manuelle Matrix steht in
  [music-assistant.md](music-assistant.md).

## Qualität vor Veröffentlichung

- Entwicklungsbenchmark: 471/507; Dev 365/393; Held-out 106/114; Automation
  30/38; Mehrturn 33/35; Confirmation 65/77; Clarification 26/31; No-write
  440/453. Alle Safety-Zähler sind 0.
- Zweites Korpus: 102/107; Dev 70/71; Held-out 32/36; Automation 12/14;
  Dialog 12/12; Confirmation 28/30; No-write 94/96; Safety 0.
- Korpus: 4.192 Signaturen; 4.153 gemeinsame Einträge gegenüber 7.11.0,
  0 geändert, 39 neu.
- Gesamtsuite auf Python 3.12: 10.033 bestanden, 12 übersprungen,
  0 fehlgeschlagen; 90 Prozent Coverage (53.808 Statements, 5.192 nicht
  abgedeckt). Python 3.13 ist ebenfalls grün. RuntimeWarning- und
  UnraisableException-Gates meldeten 0 Befunde.
- Vollständiges Pyright und alle vorhandenen Strict-Profile: 0 Fehler,
  0 Warnungen. Pyflakes, Exception-Inventar, Hassfest und HACS sind grün.
- Home-Assistant-Options-Flow, Stable-Smoke und Live-Testbett sowie der echte
  HA-Komponenten-/MA-Servicevertrag gegen HA 2026.4.0 und 2026.9.2 sind grün.
- Identity-Shadow: 2.131/2.131 äquivalent, Safety Drift 0. Arbiter-Shadow:
  2.157 äquivalent und vier versionierte Verhaltensänderungen, kein Safety
  Drift. Alle Runtime-/Event-Supervisionszähler blieben bei 0 Fehlern.
- Unveränderte 5.000-Entity-Budgets sind im isolierten CI-Runner grün:
  Automation-NLU 20,45 ms p95, MA-Instanzauflösung 0,296 ms p95,
  Engine-Konstruktion 37,71 ms p95 und Event-Zusammenfassung 153,5 ms p95.
  Ein lokaler Lauf unter einer Hostlast von etwa 54 auf vier CPUs war nicht
  als Performance-Gate verwertbar; die Grenzen wurden nicht angehoben.
- Der vollständige Code-RC `18a286d42bd88a174aea2ca748ad2924d7bc50e8`
  ist in [CI-Lauf 40173229191](https://github.com/pquandel2-alt/homeintent/actions/runs/40173229191)
  grün. Der Release-Workflow prüft zusätzlich denselben finalen SHA, den er
  taggt und veröffentlicht.

## Verbleibende Grenzen

- Offene Automations-Slotentwürfe werden nicht persistiert und sind nach einem
  Neustart bewusst verworfen.
- Unvollständige wiederkehrende oder tiefer verschachtelte Formulierungen, die
  nicht sicher in den neuen Slottyp projiziert werden können, verbleiben im
  dokumentierten Compatibility-Pfad oder werden ohne Schreibplan abgelehnt.
- `engine.py` und `event_runtime.py` bleiben große Fassaden. Ihre bereits
  vorhandenen semantischen beziehungsweise Queue-/Supervisor-Module wurden
  beibehalten; in 7.12.0 wurden Dialog-Slotprojektion und Undo-Planung als neue
  eigenständige Fachgrenzen extrahiert.
- Ein echter Music-Assistant-Server und reale Audiowiedergabe sind nicht Teil
  der automatischen CI und werden nicht als getestet behauptet.

Echte Audiowiedergabe gehört ausdrücklich nicht zum automatisierten Nachweis.
Der endgültige Tag, Release-SHA und der dazu gehörige CI-Lauf werden im
GitHub Release ausgewiesen, weil diese Belege erst nach diesem versionierten
Dokument entstehen können.
