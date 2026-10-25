# HomeIntent 7.11.0

## Music Assistant

- Angemeldete Benutzer können einen einzelnen, exakt gefundenen
  Music-Assistant-Bibliothekseintrag als persönlichen Medienalias vormerken.
  Gespeichert wird erst nach Vorschau und ausdrücklichem Ja.
- Der Eintrag enthält Besitzer, `config_entry_id`, Medientyp, kanonischen Namen
  und eine sichere, nicht webbasierte Media-ID. HTTP-, HTTPS-, FTP-, Datei- und
  Daten-URLs sind ausgeschlossen.
- Vor jeder Wiedergabe werden Existenz, Eindeutigkeit, MA-Instanz und Media-ID
  erneut geprüft. Veraltete oder mehrdeutige Aliase starten nichts.
- Anlegen und Verwenden sind benutzergebunden; Löschen ist nur dem Besitzer
  oder einem Administrator erlaubt. Konflikte mit Geräten, Räumen, Personen,
  HA-Benutzern und vorhandenen Aliasen werden abgelehnt.
- Bereits vorhandene, eindeutig benannte und freigegebene MA-Gruppen-Player
  werden wie normale Ziele verwendet. Dynamische Gruppenbildung ist bewusst
  nicht implementiert, weil für die unterstützten HA-Versionen kein eigener
  stabiler nativer MA-Aktionsvertrag belegt ist.

## NLU-Roadmap Phase 4

- Ein erster enger Router-Ausschnitt konsumiert das bereits einmal erzeugte
  `LanguageDocument` und projiziert typisierte Automationsslots.
- Autoritativ migriert ist nur die fail-closed Erkennung unvollständiger
  zukünftiger beziehungsweise solarer Automationsfragmente. Sie darf lediglich
  eine gezielte Rückfrage öffnen und besitzt keinen Ausführungspfad.
- Datum, Uhrzeit, Sonnenereignis/-offset, Bedingung, Dauer, Zustand, Aktion,
  Ziel, Ausnahme, Wiederholung und Gültigkeitszeitraum sind in dieser Matrix
  typisiert. Negation, Hypothese und unvollständige Äußerungen erzeugen keinen
  reduzierten Plan.
- Der vollständige Automationscompiler, Management, Monitoring, Routinen,
  Queries und Gerätecontroller bleiben Compatibility-/Legacy-Pfade. Phase 4
  ist ausdrücklich nicht vollständig abgeschlossen.

## Qualitätsstand vor dem Release

- Standardbenchmark: 467/503, Dev 362/390, Held-out 105/113,
  Automation 30/38, Mehrturn 29/31, Confirmation 55/67,
  Clarification 26/31 und No-write 440/453.
- Zweites 7.8-Korpus: 102/107, Dev 70/71, Held-out 32/36,
  Automation 12/14, Dialog 12/12, Confirmation 28/30 und No-write 94/96.
- Wrong Target, falsche Operation, unerwartetes Schreiben, Ausführung ohne
  Bestätigung, Safety-Fehler und `unsafe_execution_count`: jeweils exakt null.
- 4.173 Korpussätze wurden gegen 7.10.1 geprüft; es gab keine geänderte
  Signatur und keine abgesenkte Baseline.

Details: [Music Assistant](music-assistant.md),
[NLU-Migrationsstatus](nlu-phase4-migration-7.11.md) und
[Benchmark-Qualitätsgrenzen](perf/dev-benchmark-quality-gates.md).
