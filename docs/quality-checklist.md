# HomeIntent – Qualitätscheckliste

HomeIntent ist eine HACS-Custom-Integration und hat deshalb keinen offiziell
von Home Assistant vergebenen Bronze-/Silver-/Gold-/Platinum-Status. Diese
Checkliste orientiert sich trotzdem an der aktuellen Integration Quality Scale
und trennt nachweisbar Erledigtes von offenen Arbeiten. Sie ist keine
Selbstzertifizierung.

Stand: 27. Oktober 2026 (7.13.1)

## Release Candidate 7.13.1

- [x] vollständige Satzverbrauchsprüfung für schreibende Medienbefehle
  (`media_coverage`); Koordinationen, Rest und Konflikte übergeben den ganzen
  Satz an die atomare Mehrbefehls-Pipeline
- [x] keine leere koordinierte Klausel, kein koordinierter Ort und kein
  koordiniertes Ziel ohne geplantes Ziel (`nlu/clause_completeness`)
- [x] Nightly-Gegenbeispiel von 7.13.0 und weitere gefundene Fälle fest in
  `REGRESSIONS` (normale CI) sowie Property-/Metamorphietests
- [x] settle-fähige Batch-Struktur (`PendingBatchExecution` → finales
  `BatchExecutionResult`); Antwort, Audit pro Ziel und Undo aus demselben
  Ergebnis
- [x] Batch-Eignung getrennt von der Risikoklasse; Standard-Rollläden pro Ziel,
  Garagentore/Tore/unklassifizierte Cover atomar; Garagentor-Position `CRITICAL`
- [x] Release-Gate verlangt grüne `ci.yml` **und** `nightly-live.yml` für den
  exakten SHA; fehlgeschlagener Sicherheitsjob blockiert dauerhaft
- [ ] reale Sonos-/Music-Assistant-/Licht-/Rollladen-Hardware – bewusst nicht
  behauptet; manuelle Matrix in [Umsetzung 7.13.1](umsetzung-7.13.1.md)
- [x] Release-Commit `0f1e63d02fb97e23d626e924c62e9b3491147650`: CI
  [Lauf 40605416517](https://github.com/pquandel2-alt/homeintent/actions/runs/40605416517)
  und Nightly-Sicherheitslauf
  [Lauf 40605390788](https://github.com/pquandel2-alt/homeintent/actions/runs/40605390788)
  mit allen Pflichtjobs grün; Release-Gate im
  [Release-Workflow 40610331432](https://github.com/pquandel2-alt/homeintent/actions/runs/40610331432)
  bestanden; [Tag und GitHub-Release v7.13.1](https://github.com/pquandel2-alt/homeintent/releases/tag/v7.13.1)

## Release Candidate 7.13.0

- [x] einmalige, executor-gebundene Legacy-/HomeIntent-Pfadauflösung; keine
  Dateisystemprüfung in Store-Konstruktoren oder beanstandeten Requestpfaden
- [x] `proactive_message` mit Admin-Grenze, gebundenem internem Nachweis,
  unveränderlicher Aktion/Zielmenge, Ablauf-/Replay-Schutz und sichtbarer Aktion
- [x] gemeinsames Ortsmodell für Haus, innen/außen, Bereiche, Etagen, Aliase,
  Satellitenbezug und harte ortsgebundene Zielmengen
- [x] kompositionelle Komposita-, Skript-, LIST- und elliptische Gerätesprache
- [x] typisierte Skriptausgänge und sichere partielle Licht-Batches
- [ ] partielle Cover-Batches – in 7.13.0 **nicht** aktiv (Cover `MEDIUM`
  fielen auf den gemeinsamen Call zurück); umgesetzt in 7.13.1
- [x] capability- und quellengebundene Medien-/Sonos-/Music-Assistant-Semantik
- [x] Regressionen und Invarianten zu Issue #38, Event-Loop-I/O,
  Proaktivautorisierung, Skriptausgang, Teilfehlern und Medienquellen
- [x] normale CI für den exakten Release-Commit `324cfb8` grün:
  [Lauf 40457654872](https://github.com/pquandel2-alt/homeintent/actions/runs/40457654872);
  Tag und Release `v7.13.0` erstellt
- [ ] Nightly-Sicherheitslauf desselben Commits **fehlgeschlagen**
  ([Lauf 40470216752](https://github.com/pquandel2-alt/homeintent/actions/runs/40470216752));
  das damalige Gate prüfte ihn nicht – behoben in 7.13.1

## Release Candidate 7.12.0

- [x] typisierter Automations-Slotdialog mit Scope, TTL, Korrektur, Abbruch,
  Themenwechsel, expliziten Bedingungen und No-Write-Zwischenständen
- [x] History Schema 3 enthält keine Vollsnapshots für neue Single- oder
  Multi-Target-Einträge
- [x] atomarer Pause-/Resume-Undo prüft und restauriert Ziel und Helper
  gemeinsam; fremde Objekte bleiben erhalten
- [x] Music-Assistant-Multiinstanzauflösung und benutzergebundene Rückfrage
- [x] echter HA-Komponenten-/Schemavertrag automatisiert geprüft
- [ ] echter Music-Assistant-Server mit Audioplayer automatisiert geprüft –
  bewusst nicht behauptet; dokumentierte manuelle Matrix vorhanden
- [x] Entwicklungsbenchmarks: 471/507 und 102/107, sämtliche Safety-Zähler 0
- [x] Korpus-Shadow: 4.153 gemeinsame Signaturen, 0 geändert; 39 neue
- [x] Code-RC `18a286d42bd88a174aea2ca748ad2924d7bc50e8`:
  Gesamtsuite 10.033 bestanden/12 übersprungen, Coverage 90 Prozent,
  Pyright samt Strict-Profilen 0/0, Pyflakes, Hassfest, HACS, HA-Matrix und
  Performance grün in [CI 40173229191](https://github.com/pquandel2-alt/homeintent/actions/runs/40173229191)
- [ ] CI für exakt den finalen Dokumentations-Commit, Tag und GitHub Release –
  durch den Release-Workflow vor Veröffentlichung zu erzwingen

## V10 Completion / 5.0.2

- [x] offizielle Projektidentität und kanonische Integration sind `HomeIntent` / `homeintent`
- [x] `legacy/ha_nlu` enthält ausschließlich den dokumentierten
  Config-Entry-Kompatibilitätsshim; unter `custom_components/` ist nur HomeIntent
- [x] zentrales typisiertes GoalModel trennt GOAL, COMMAND, QUERY und AUTOMATION
- [x] ein zentraler bounded Planner erzeugt typisierte Abhängigkeitsgraphen und PlanningTrace
- [x] bestätigte Routinen und Komfortprofile werden lokal persistiert; es gibt kein stilles Lernen
- [x] Home-Assistant-`person.*`, explizite User↔Person-/Push-Bindings und Household-Scope
- [x] persistente Monitor Goals mit frischer Triggerzeit-Abfrage, Dedupe, Cooldown und Idempotency-Key
- [x] erwartete Effekte werden aus frischen Zuständen geprüft; Serviceannahme allein gilt nicht als Erfolg
- [x] begrenzte GoalRun-Historie und belegbasierte Fehlererklärung ohne erfundene Kausalität
- [x] typisierte historische GoalRun-Selektion nach lokaler HA-Zeit, User,
  Status, Routine, GoalKind und Entity; mehrere Treffer führen zur Rückfrage
- [x] typisierter Temperatur-Semantikdialog bewahrt das ursprüngliche GoalModel;
  Setpoint nutzt den persistenten One-Shot-Pfad, Deadline ohne Modell stoppt
- [x] exakte Push-Bindings unterscheiden Notify-Entity und klassischen
  Notify-Service; Missing/Ambiguous besitzen keinen Broadcast-Fallback
- [x] neue Notification Actions verwenden nur `HOMEINTENT_*`; `HA_NLU_*`
  bleibt ausschließlich eingehende Legacy-Kompatibilität
- [x] ExecutionCoordinator-, Monitor-, UserContext- und Profile-Grenzen sind
  mit gezielten Konflikt-, Restart-, Dedupe-, Binding- und Persistenztests gehärtet
- [x] strukturierte Planänderungen einschließlich planweiter Exclusions und persistenter Terminierung
- [x] 160 handgeschriebene V10-Goal-/Planning-OOD-Fälle sowie Metamorphie- und Safety-Tests

## Abschlussvalidierung 5.0.2

- [x] Gesamtsuite: 3.137 bestanden, 12 übersprungen, 0 fehlgeschlagen
- [x] Coverage: 86,98 Prozent (19.280/22.167 Statements; Terminalanzeige 87 Prozent)
- [x] Modul-Coverage: ExecutionCoordinator 100,00 %, MonitorGoal 88,28 %,
  UserContext 90,67 %, Profiles 92,81 %, GoalRun 89,26 %, GoalIntent 86,77 %,
  Planner 86,88 %
- [x] Language Safety: 252 bestanden
- [x] Shadow: 3.772 Turns, Baseline unverändert, 0 Action-Leakage
- [x] Full Pyright einschließlich konfiguriertem Strict-Scope: 0 Fehler, 0 Warnungen
- [x] Pyflakes und `git diff --check`: grün
- [x] V10-5k-Performance-Gate lokal grün; höchster gemessener p95 45,820 ms
- [x] Vollständiger 5.0.2-CI-Lauf mit Hassfest, HACS, Home Assistant Stable
  Smoke, Python 3.12/3.13 und separatem CI-Strict-Befehl: grün in
  [CI-Lauf 35565156452](https://github.com/pquandel2-alt/homeintent/actions/runs/35565156452)

Der lokale V9-5k-Lauf wurde bei einer Hostlast von etwa 54 auf vier CPUs
ausgeführt und überschritt dadurch mehrere unveränderte 100-ms-Grenzen. Diese
Messung ist kein grünes Release-Gate und die Budgets wurden nicht angehoben.
Maßgeblich ist der identische Lauf auf dem unbelasteten GitHub-CI-Runner.

## V9 Completion / 4.77.0

- [x] Query-Algebra bleibt einziger Reasoning-Evaluator
- [x] relationale Klassen, Kardinalität, Difference und Discourse-Literalsets
- [x] State Duration nur mit vollständigem `last_changed`-Beleg
- [x] Event-History ohne Recorder niemals aus aktuellem Zustand abgeleitet
- [x] Value-/Property-/relative Temporal-Repair behält nur finalen Slot
- [x] Target Repair bewahrt `REPLACES`-Evidenz und führt nur das neue Ziel aus
- [x] absolute und relative One-shot-Zeitbefehle nutzen die persistente Automation
- [x] 159 unabhängige handgeschriebene V9-OOD-Fälle samt Ergebnis-/Safety-Orakel
- [x] HouseGraph: depth-, visited-node-, frontier- und path-bound
- [x] Query/Ambiguous/Unsupported bleiben ohne ServicePlan
- [x] explizite Recorder-Queries nutzen den vorhandenen History-Adapter;
  nicht belegbare Mengen-History bleibt unsupported

## Abschlussvalidierung 4.77.0

- [x] Gesamtsuite: 3.054 bestanden, 12 übersprungen, 0 fehlgeschlagen
- [x] Coverage: 87,76 Prozent (17.475/19.913 Zeilen)
- [x] Language Safety: 252 bestanden
- [x] Shadow: 3.772 Turns, 0 Divergenzen, 0 Action-Leakage
- [x] vollständiges Pyright: 0 Fehler; CI-identischer Strict-Scope: 0 Fehler
- [x] lokaler Schema-Smoke mit echtem Home Assistant 2025.1.4:
  `HOME_ASSISTANT_AUTOMATION_CALENDAR_AND_PUSH_SCHEMA_OK`
- [x] unverändertes 5k-Performance-Gate in GitHub Actions: grün; höchster
  gemessener V9-p95 21,55 ms (verschachtelter Filter), produktiver
  Discourse-Follow-up 2,19 ms
- [x] HA-Stable-Container-Smoke, Hassfest und HACS: grün in
  [CI-Lauf 35453737883](https://github.com/pquandel2-alt/homeintent/actions/runs/35453737883)

Der zusätzliche lokale 5k-Lauf unter einer Host-Last von etwa 61 auf vier
sichtbaren CPUs war nicht vollständig grün; seine Messwerte werden deshalb
nicht als Release-Gate gewertet. Die Grenzen wurden nicht verändert. Details
und die maßgebliche CI-Messung stehen im Performanceprotokoll.

## Bereits erfüllt oder für HomeIntent nicht anwendbar

- Einrichtung und nachträgliche Konfiguration erfolgen über Config Flow und
  Options Flow; eine zweite Installation wird verhindert.
- Entitäten werden dynamisch aus der Assist-Freigabe oder einer bewusst
  gespeicherten Auswahl gelesen; HomeIntent pollt keine Geräte und benötigt
  keine externe Verbindung.
- Eigene Dienste werden beim Laden registriert und beim Entladen entfernt.
- Config Flow, Options Flow, Laden, Entladen, Conversation Agent,
  Automationspersistenz und Dialoge besitzen automatisierte Tests.
- Die README beschreibt Zweck, Installation, Optionen, unterstützte Funktionen,
  Sicherheitsregeln, Beispiele und bekannte Grenzen.
- Es gibt einen benannten Code Owner, ein öffentliches Issue-Tracking und eine
  deklarierte MIT-Lizenz.
- Die Integration verwendet ausschließlich lokale Home-Assistant-Daten und hat
  keine versteckte Laufzeitabhängigkeit oder Cloud-Anmeldung.
- Fehler bei Dateiänderungen werden transaktional zurückgerollt; gleichzeitige
  Änderungen werden über Fingerabdrücke erkannt und nicht überschrieben.
- Diagnoseinformationen sind vorhanden und enthalten keine Äußerungen,
  Entity-IDs oder Zustände.
- Lesende und steuernde Freigaben können getrennt werden; zentrale Richtlinien
  begrenzen Zielanzahl, Bestätigungsstufe, zugelassene Benutzer,
  Administratorziele sowie Nicht-Administrator-Aktionen.
- Offene Service- und Automationserstellungsbestätigungen sind an die bekannte
  Home-Assistant-Benutzer-ID gebunden.
- Gemeinsame Engine und Dialogzustände werden typisiert über
  `ConfigEntry.runtime_data` gehalten; direkte Testkonstruktion besitzt einen
  kompatiblen Fallback.
- Config-/Options-Flow und interne Dienste besitzen deutsche und englische
  UI-Übersetzungen.
- Eine eigene Fehlerdiagnose-Anleitung deckt Auswahl, Ortsbezug, Richtlinien,
  Recorder, Benachrichtigungen und Automationspersistenz ab.

## Noch offen vor einer möglichen Aufnahme in Home Assistant Core

- Offizielle Brand-Assets im Home-Assistant-Brands-Repository fehlen.
- Eine belastbare, modulweise gemessene Testabdeckung von mehr als 95 Prozent
  ist noch nicht nachgewiesen. Das ist ausdrücklich keine Aussage über die
  Anzahl der bestehenden Tests.
- Die vielen dynamisch erzeugten Conversation-Antworten sind noch nicht
  vollständig in Home Assistants Übersetzungssystem überführt.
- Strikte Typprüfung ist für die sicherheits- und semantikkritischen Kernmodule
  konfiguriert; der vollständige Repository-Scope läuft zusätzlich im normalen
  Pyright-Modus.
- Die offiziellen Regeln zu Discovery, Reauthentication, Firmware-Updates,
  Geräteentitäten und Offline-Verfügbarkeit sind für einen lokalen Conversation
  Agent überwiegend nicht anwendbar; eventuelle Ausnahmen müssten in einem
  Core-Review durch Home Assistant bestätigt werden.
- Eine offizielle Einstufung ist erst im Rahmen einer Aufnahme und Prüfung durch
  das Home-Assistant-Core-Team möglich.

## Architekturfortschritt

Die vormals stark gebündelte Logik wird schrittweise nach Zuständigkeit
getrennt. Eigene Module kapseln inzwischen unter anderem Gradsemantik,
semantische Rückfragedialoge, zusätzliche Gerätedomänen, Kalenderabfragen,
Kalender-Laufzeitzugriffe, Automations-Aktionsänderungen, Metadaten und
Transaktionen sowie nun Ortskontext, Rücknahme, lokale Anpassungen,
Benachrichtigungsautomation und Recorder-Verlauf. Weitere Aufteilungen sollen
jeweils verhaltensneutral und mit Regressionstests erfolgen.
