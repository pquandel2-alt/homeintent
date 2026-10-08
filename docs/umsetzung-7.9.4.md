# HomeIntent 7.9.4 – P0: Home Assistant nach Neustart nicht erreichbar

Ausgangsstand 7.9.3 auf `main` (`3f72611`). Arbeitszweig
`fix/7.9.4-event-runtime-startup`. Reiner Stabilitäts-Fix: keine neue
Funktion, keine Änderung an Sprachverständnis, Bestätigung, Execution Policy,
Berechtigungen, Datenschutz oder Schreibwegen (`service_executor` bleibt der
einzige Schreibweg für Geräte).

## Befund aus einem echten System

HomeIntent 7.9.3 frisch installiert, danach Neustart: die Home-Assistant-
Oberfläche war nicht mehr erreichbar. Im Log sehr viele offene bzw. wartende
`state_changed`-Tasks aus `custom_components/homeintent/event_runtime.py`.
Mit deaktivierter HomeIntent-Integration lief Home Assistant normal.

## Ursache

`SituationRuntime` (`event_runtime.py`) hörte mit einer **Coroutine** auf
**jedes** `state_changed` im ganzen Haus. Für jede Coroutine legt Home
Assistant je Ereignis einen eigenen Task an. Jeder dieser Tasks

1. baute die Snapshots **aller** ausgewählten Entitäten neu auf (ohne feste
   Auswahl: einmal durch die gesamte Zustandsmaschine samt
   `async_should_expose` je Entität) – auch für Entitäten, die HomeIntent gar
   nicht sieht; erst danach wurde geprüft, ob das Ereignis überhaupt eine
   ausgewählte Entität betrifft;
2. rief `ThermalExperienceTracker.async_observe_states()` auf, das **immer**
   – auch ohne aktiven Heizzyklus, also praktisch immer – die Datei
   `homeintent_thermal_cycles.json` per `asyncio.to_thread` schrieb, mit
   `fsync`, serialisiert über einen Thread-Lock;
3. bei Sensoren: fragte `MonitorGoalStore.async_watched_entities()`, das vor
   dem ersten Laden je Aufrufer eine eigene Datei-Lesung im Executor
   startete.

Beim Start setzt Home Assistant Tausende Zustände (jede Entität mindestens
einmal, viele zweimal: `unavailable` → Wert). Ergebnis: Tausende gleichzeitige
HomeIntent-Tasks, quadratischer Snapshot-Aufwand im Event-Loop und je
Ereignis ein `fsync`-Schreibvorgang, die alle Threads des gemeinsamen
Executors an einem Lock blockierten. Home Assistant braucht genau diesen
Executor zum Starten (Integrationen laden, HTTP/Frontend) – die Oberfläche
kam nicht mehr hoch.

Warum die CI das nicht sah: Die Stub-Tests rufen den Handler direkt auf, das
Live-Testbett (rund 125 Geräte) startet Home Assistant nach dem Einrichten
von HomeIntent nicht neu, und der kleine Ereignisstrom dort fällt nicht auf.

## Änderung

- **`event_runtime.py`:** Der Listener ist jetzt ein `@callback` (läuft im
  Event-Loop, kein Task je Ereignis). Er prüft in O(1), ob die Entität zu
  den ausgewählten gehört (`hass_entities.is_selected_entity()` – dieselbe
  Regel wie `get_selected_entity_ids()`, nur für eine Entität), und reiht nur
  diese Ereignisse in eine Warteschlange ein. **Ein** Hintergrund-Worker
  (`async_create_background_task`, nicht eager, blockiert den Start nicht)
  arbeitet sie der Reihe nach ab: Snapshots einmal je Stapel (höchstens
  256 Ereignisse; auch die bisherigen Tasks lasen den Zustand erst, wenn sie
  liefen, also nach dem Schwall), Nachschlagen per Dict statt linearer Suche,
  nach je 20 ms Arbeit gibt der Worker den Loop frei. Ein fehlerhaftes
  Ereignis beendet den Worker nicht. Die Warteschlange ist auf 4096 Ereignisse
  begrenzt; läuft sie über, wird das gezählt und höchstens einmal je Minute
  gewarnt (im Live-Test nie erreicht). Beim Entladen wird der Worker
  abgebrochen. Die Auswertung je Ereignis (Effekt-Monitor, Monitore,
  V12-Kontext, Situationen, Routinen, Entscheidungs-Engine, Dedupe,
  `_signal`) ist unverändert; `async_handle_state_changed()` bleibt als
  direkter Einstieg erhalten.
- **`thermal_tracker.py`:** `async_observe_states()` kehrt ohne aktiven
  Zyklus sofort zurück und schreibt nur noch, wenn sich ein Zyklus wirklich
  ändert (abgeschlossen bzw. Fenster erstmals offen). Aufzeichnung und
  Invalidierung der Lernbeobachtungen sind unverändert.
- **`monitor_goal.py`:** Das erste Laden der beobachteten Entitäten läuft
  genau einmal (Single Flight), auch wenn viele Sensor-Ereignisse
  gleichzeitig fragen.

Bewusste Verhaltensgrenze: Ereignisse nicht ausgewählter Entitäten lösen
keine Thermal-Beobachtung mehr aus. Sie lieferten dem Tracker dieselben
Snapshots wie das nächste Ereignis einer ausgewählten Entität; der
Temperatursensor eines Zyklus ist selbst ausgewählt.

## Tests

„Vorher rot“ heißt: dieselbe Testdatei gegen den unveränderten Stand 7.9.3
(Worktree von `3f72611`).

| Testdatei | Fälle | vorher rot | nachher |
|---|---:|---:|---|
| `tests/test_startup_event_storm_794.py` (Stub) | 11 | 10 | grün |
| `tests_ha/test_startup_event_storm.py` (echtes HA 2026.9.2) | 2 | 2 | grün |

Die eine vorher grüne Stub-Probe ist die Gegenprobe: dieselbe Nacht-Öffnung
erzeugt durch den echten Listener genau eine Meldung (Verhalten unverändert).

Stub-Tests: Listener ist `@callback`, keine Coroutine; 5000 Ereignisse fremder
Entitäten erzeugen keinen Task, keinen Snapshot, keine Thermal-Beobachtung;
dynamische Assist-Freigabe als Filter; 1000 Ereignisse einer ausgewählten
Entität → genau ein Worker, alle verarbeitet, höchstens
⌈1000/256⌉ Snapshot-Aufbauten; Reihenfolge erhalten, ein Fehler stoppt den
Worker nicht, ein beendeter Worker startet neu; Stop bricht ab und ignoriert
spätere Ereignisse; Überlauf gezählt, eine Warnung; Thermal ohne Zyklus
schreibt nichts (500 Beobachtungen), mit Zyklus nur bei Änderung (Fenster
auf: 1, Ziel erreicht: 1); 500 gleichzeitige Monitor-Abfragen → eine
Lesung.

Echtes Home Assistant (`tests_ha`): HomeIntent einrichten, dann 3000 Sensoren
je `unavailable` → Wert (6000 Ereignisse), einmal nicht ausgewählt, einmal
alle ausgewählt:

| Messung | 7.9.3 nicht ausgewählt | 7.9.3 ausgewählt | 7.9.4 nicht ausgewählt | 7.9.4 ausgewählt |
|---|---:|---:|---:|---:|
| Thermal-Schreibvorgänge (`fsync`) | 5998 | 6000 | 0 | 0 |
| Dauer des Tests | 26,0 s | 138,3 s | < 0,5 s | < 0,5 s |
| verarbeitete Ereignisse | – | – | 0 | 6000 (in Reihenfolge) |
| Snapshot-Aufbauten | – | – | 0 | 25 |

Ohne Zwischen-Freigabe des Loops (alle 6000 Ereignisse synchron) hielt 7.9.3
6000 offene HomeIntent-Tasks gleichzeitig, 7.9.4 null.

Bewusste Grenze des seriellen Workers: Ereignisse werden nacheinander statt
gleichzeitig ausgewertet. Wartet eine Auswertung (z. B. ein Serviceaufruf
eines bestätigten Agent-Ereignisses), verschieben sich die folgenden um diese
Zeit; die Reihenfolge bleibt dafür erhalten. Die Proaktiv-Engine führt
Automatik-Aktionen weiterhin im Hintergrund aus.

## Gates

Lokal, Python 3.12 (CI-Hauptjob), zusätzlich 3.13 und 3.13 mit hassil 3.12:

| Gate | Ergebnis |
|---|---|
| Stub-Suite 3.12 / 3.13 / 3.13 + hassil 3.12 | je 9688 passed, 12 skipped, 0 failed (7.9.3: 9677 + 11 neue) |
| `tests_ha` (HA 2026.9.2, Python 3.14) | 18 passed (16 + 2 neue) |
| Sprachverständnis-Gate (hassil 3.11 und 3.12) | je 463 passed |
| Korpus-Signaturen gegen `corpus-signatures-7.9.3.json` | 4098 Sätze, 0 geändert; neue Baseline `corpus-signatures-7.9.4.json` byte-identisch (CI-Gate umgestellt) |
| Shadow-Vergleich | 2131 EQUIVALENT, 0 SAFETY_DRIFT |
| Entwicklungs-Benchmark 7.7 / 7.8 | `unsafe_execution_count` 0, `SAFETY` 0 |
| Arbiter-Shadow | 2150 gleichwertig, 7 nicht messbar, 4 BEHAVIOR_CHANGE (Musik, wie 7.9.3), 0 SAFETY_DRIFT |
| V9-Latenz 5000 Entitäten | bestanden (Budget p95 100 ms; z. B. `v9_group_reference_shape` p95 11,4 ms) |
| Automationssprache 5000 | p95 17,1 ms (Budget 100 ms) |
| V10 / V11 / V12 / Learning Center | alle Budgets eingehalten (V12 `event_storm_1000` 2,9 ms) |
| Zusammenfassung 5000 Entitäten, 7 Tage | p95 134,9 ms (Budget 500 ms) |
| Pyright voll, CI-Strict-Liste, V11-, V12-, Learning-Center-Profil | je 0 Fehler |
| Pyflakes, `git diff --check` | 0 |
| HA-Smoke-Skripte (`validate_*`), lokal mit HA 2026.9.2 statt Docker-Image | alle 4 OK |

## Live-Testbett

`sim/fresh_ha.sh` (frisches HA 2026.9.2, 136 freigegebene Entitäten), danach
**Neustart** von Home Assistant mit installiertem HomeIntent – der gemeldete
Ablauf. HTTP nach 2,0 s erreichbar, keine Meldung „Something is blocking Home
Assistant“. Danach `runner.py --strict` über alle Kategorien inklusive
Proaktiv auf dem neu gestarteten System: **229/230**.

- `n793-b1-weather` („Regnet es heute noch?“ nach „Regen ab Stunde +2“) lief
  gegen 22:25 Uhr Ortszeit; der Regen beginnt damit erst nach Mitternacht,
  HomeIntents Antwort „heute kein Regen mehr“ ist richtig. Gegenprobe: frisches
  7.9.3-Testbett, dasselbe Szenario zur selben Zeit – identische Antwort,
  ebenfalls rot. Ein tageszeitabhängiges Szenario, kein Befund dieses Fixes;
  bewusst nicht angepasst (außerhalb des Auftrags).
- `check_log.py`: kein HomeIntent-Traceback. Der einzige Treffer ist der
  `libturbojpeg`-Traceback der HA-Kamera-Komponente; er zählt lokal nur mit,
  weil der Pfad der lokalen venv „homeintent“ enthält (in der CI liegt sie
  unter `RUNNER_TEMP`).

Das Testbett-Haus ist zu klein, um den Ausfall selbst zu zeigen (7.9.3 startet
dort ebenfalls in 1,5 s); die Reproduktion liefert `tests_ha` mit 3000
Sensoren.

## Bekannte Grenzen

- Läuft die Warteschlange über (mehr als 4096 ausgewählte Zustandsänderungen,
  ohne dass der Loop dazwischen frei wird), werden weitere Ereignisse
  verworfen und mit einer Warnung je Minute gemeldet. Im Live-Test und in
  `tests_ha` (6000 Ereignisse) nie erreicht.
- Bei fester Geräteauswahl prüft der Filter die Mitgliedschaft in der
  gespeicherten Liste (linear); bei der Standard-Freigabe über Assist ist es
  eine einzelne `async_should_expose`-Abfrage.
