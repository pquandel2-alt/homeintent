# HomeIntent 7.9.6 – EventRuntime unter Last: Prioritäten, Verbraucher, Snapshot-Retry

Ausgangsstand: `origin/main` = Tag `v7.9.5` = `dbb42d92e680c41ef252461c92a2282dddbb11df`
(geprüft mit `git fetch --all --tags --prune`; Arbeitsverzeichnis sauber; kein
neuerer Commit auf `main`). Arbeitszweig `fix/7.9.6-event-runtime-hardening`.

Reiner Stabilitäts-, Safety- und Performance-Bugfix. Unverändert: Sprach-
verständnis, Intent-Auflösung, Dialog, Bestätigungen, Berechtigungen,
Execution Policy, Safety- und Datenschutzgrenzen. Kein neuer Schreibweg zu
Home Assistant; `service_executor` bleibt der einzige Weg, Geräte zu schalten.
Alle Verbesserungen aus 7.9.4 und 7.9.5 bleiben erhalten (kein Task je
Ereignis, höchstens ein Worker, keine Datei-I/O im Bus-Callback, jedes
Ereignis mit eigenem Zustand und dem Haus seines Zeitpunkts, feste Auswahl als
Mengenabfrage, alter Worker nach Reload ohne Zugriff, kein Thermal-Schreiben
ohne aktiven Zyklus); ihre Regressionstests laufen mit.

## 1. Ausgangsprobleme und Root Causes

Reproduziert mit `scripts/event_runtime_repro_796.py` (Stub-HA, nutzt nur
APIs, die 7.9.5 schon hatte; dasselbe Skript gegen beide Stände):

```text
7.9.5 (dbb42d9)
critical: {'queued_before_drain': 4096, 'dropped': 1, 'processed': 4096, 'smoke_processed': False}
snapshot: {'events': 3, 'processed': [], 'lost': 3}
no_consumers: {'no_consumers_queued': 1, 'no_consumers_workers': 1}
monitor_reads: {'person_transitions': 50, 'store_reads_after_first_load': 50}

7.9.6
critical: {'queued_before_drain': 4096, 'dropped': 1, 'processed': 4096, 'smoke_processed': True}
snapshot: {'events': 3, 'processed': ['on', 'off', 'on'], 'lost': 0}
no_consumers: {'no_consumers_queued': 0, 'no_consumers_workers': 0}
monitor_reads: {'person_transitions': 50, 'store_reads_after_first_load': 0}
```

(7.9.6 „dropped: 1“ ist die älteste gewöhnliche Messwertänderung, die der
Rauchmelder verdrängt hat – gezählt als `dropped_coalescible`.)

| # | Problem | Root Cause |
|---|---|---|
| 3.1 | Rauchmelder hinter 4096 Sensorereignissen verworfen | `async_enqueue_state_changed()` behandelte alle ausgewählten Ereignisse gleich: bei `len(_pending) >= 4096` wurde *das neue* Ereignis verworfen, gleich welcher Art. Es gab keine Rangfolge und nur einen Zähler `_dropped`. |
| 3.2 | Snapshot-Fehler verlor den Batch | `_async_drain()` entnahm zuerst bis zu 256 Ereignisse (`popleft`) und baute erst danach den Snapshot; bei einer Ausnahme waren die Ereignisse schon aus der Queue und wurden nur protokolliert. |
| 3.3 | Kein Verbraucher-Filter | Der Callback prüfte nur „ausgewählt/freigegeben“. Ob überhaupt ein Verbraucher aktiv ist, wurde erst im Worker entschieden – nach Queue, Worker-Start und vollständigem Snapshot. Vorhandene, aber inaktive Runtime-Objekte (V12-Kontext aus, kein Thermal-Zyklus, keine Monitorziele, kein ExpectedEffect) wurden trotzdem aufgerufen. |
| 3.4 | MonitorGoalStore las pro Ereignis | `async_load()` las bei jedem Aufruf die JSON-Datei. Der 7.9.4-Single-Flight schützte nur das abgeleitete `_watched`, nicht die Records; `async_process_person_transition()` und `async_process_value_change()` riefen `async_load()` je Ereignis. |
| 3.5 | RuntimeWarning in grüner CI | `tests/_ha_stub.ServiceMock` (Double für `hass.services.async_call`) ist ein `AsyncMock`; ohne konfigurierten Rückgabewert liefert `await async_call(...)` wieder ein `AsyncMock`. `flatten_calendar_response()` rief darauf `.items()` auf (`calendar_management.py:354`) – das erzeugt eine Coroutine, die nie awaited wird. Die Warnung entsteht erst beim Garbage Collect im `__del__`; Python kann sie dort nur als „Exception ignored“ ausgeben, der Prozess endet mit 0 – auch mit `-W error::RuntimeWarning`. |

Zu 3.5: Das echte Home Assistant liefert für `async_call(..., blocking=True,
return_response=True)` einen `ServiceResponse` (ein Mapping, z. B.
`{"calendar.x": {"events": [...]}}`), ohne `return_response` `None`. Der
Produktionscode war korrekt; das Test-Double hatte einen anderen Vertrag.

## 2. Gewählte Architektur

```
HA-Event-Bus ──@callback──► async_enqueue_state_changed()   (synchron, billig)
                              │ 1. ausgewählt? (Mengenabfrage, 7.9.5)
                              │ 2. EventInterestIndex.classify() → CRITICAL / LOSSLESS / COALESCIBLE / None
                              │ 3. None → filtered_no_interest (kein Queue-Eintrag, kein Worker)
                              ▼
                    _Generation.queue (eine deque in globaler Reihenfolge, Sequenznummern)
                              │  Überlast: Verdrängung nach Rang, Gap-Records für die Hausansicht
                              ▼
                    ein Worker (_async_drain) je Generation
                              │ Snapshot bauen → erst dann Batch entnehmen (≤ 1024)
                              │ Fehler: Batch bleibt, Backoff 50/100/200/400 ms …
                              ▼
                    _HouseView (7.9.5) + Gaps → jedes Ereignis mit dem Haus seines Zeitpunkts
                              ▼
                    _async_process_state_changed (Verbraucher, inaktive übersprungen)
```

Neue Module: `event_priority.py` (Klassifizierung), `event_interest.py`
(Interest-Index). Geändert: `event_runtime.py`, `effect_monitor.py`,
`thermal_tracker.py`, `monitor_goal.py`, `proactive_runtime.py`
(`is_relevant_event`), `situation.py` (nur Performance, s. u.),
`hass_entities.py` (`snapshot_from_state`), `event_summary.py`
(öffentlicher Name der bestehenden Alarmklassen), `__init__.py` (ein
initiales Laden der Monitorziele).

Es gibt **eine** Queue, keine Priority-Lanes. Die Verarbeitung erfolgt immer
in globaler Ereignisreihenfolge (Sequenznummer); die Priorität entscheidet nur,
was bei voller Queue weichen muss und was zusammengeführt werden darf. Damit
gibt es keine Starvation, keine Umordnung und die `_HouseView`-Rekonstruktion
bleibt exakt. Kritische Ereignisse werden „bevorzugt“, indem sie gewöhnliche
Ereignisse verdrängen und nie von ihnen verdrängt werden; ihre Latenz ist die
Abarbeitungszeit der Ereignisse vor ihnen (gemessen: s. Abschnitt 9).

### 2.1 Event-Prioritätsregeln (`event_priority.py`, `event_interest.py`)

**CRITICAL** – Zustandsänderung eines Sicherheitsgeräts, das HomeIntent bereits
als Alarm behandelt. Gemeinsame Quelle, keine neue Liste: die Vereinigung von
`situation.SAFETY_CLASSES` (SituationEvaluator), `situation_detection.SAFETY_CLASSES`
(V12-Detektor) und `event_summary.ALARM_DEVICE_CLASSES` (Zusammenfassung):
`smoke`, `carbon_monoxide`, `gas`, `moisture` (Wasserleck), `safety`, `tamper`,
`problem`. Ein `binary_sensor` dieser Klassen zählt immer (beide Flanken, also
auch „Alarm vorbei“); andere Domänen nur, wenn eine Seite ein Alarmzustand
(`on`/`detected`/`alarm`) ist – ein Gaszähler (`sensor`, Klasse `gas`,
numerisch) ist kein Gasmelder. Die Geräteklasse kommt aus den Attributen des
Ereignis-`State` (kein Registry-Zugriff). Kritische Ereignisse werden auch
ohne jeden aktiven Verbraucher eingereiht (Safety nie durch den Filter
entfernt).

**LOSSLESS** – alles, was ein Verbraucher exakt braucht:
- Entitäten mit aktivem `ExpectedEffect`,
- Wert-Sensoren, Trigger-Personen und Nobody-Home-Personen aktiver
  Monitorziele (folgt ein Nobody-Home-Ziel dem konfigurierten Haushalt, gilt
  jede `person.*`; solange der Store nicht geladen ist, gelten alle
  `person.*` und `sensor.*`),
- die Entitäten eines aktiven Thermal-Zyklus (Klima, Temperatursensor,
  Außensensor, Fenster des Bereichs),
- bei hausweiten Verbrauchern: jede `person.*`, jede für den V12-Detektor
  relevante Entität (`SituationDetector.is_relevant` + Habit-Trigger),
  jedes Ereignis bei aktivierter Routinen-Erkennung, und jedes Ereignis, das
  keine reine Zahl→Zahl-Änderung eines `sensor` ist (Tür-/Fensterflanken,
  `unavailable`, Licht an/aus, neue Entitäten …).

**COALESCIBLE** – nur: reine numerische Wertänderung (`21.3 → 21.4`, beide
Seiten endliche Zahlen) eines `sensor.*`, den kein Verbraucher einzeln
beobachtet, während nur hausweite Verbraucher aktiv sind und die
Routinen-Erkennung aus ist. Begründung: Der SituationEvaluator hat für eine
Zahl→Zahl-Änderung keine Regel (kein `SAFETY_ALARM`, `UNAVAILABLE`, `OPENED`,
`ACTIVATED`), der V12-Detektor ignoriert Sensoren außerhalb von Geräte-/
Habit-Listen; die Auswertung eines solchen Zwischenwerts kann keine Meldung
auslösen. Nie coalescible: Safety-, Personen-, Tür-/Fenster-, Effect-,
Monitor- und Thermal-Ereignisse, `binary_sensor` mit Zahlen-Labels, Wechsel
von/zu `unavailable`/`unknown`.

### 2.2 Consumer-/Interest-Index (`EventInterestIndex`)

Der Callback ruft `classify(entity_id, old_state, new_state)` auf: Attribut-
zugriffe auf die eigenen `State`-Objekte und Mengenabfragen – keine Datei,
keine Registry, kein Snapshot, keine Coroutine, kein Task, keine lineare
Suche. Verbraucher und ihr Interesse:

| Verbraucher | aktiv, wenn | Interesse |
|---|---|---|
| Safety | immer | CRITICAL-Klassen |
| `EffectMonitor` | ein Effect wartet | `watched_entity_ids` (neue read-only Property) |
| `ThermalExperienceTracker` | ein Zyklus läuft | `watched_entity_ids` (neue read-only Property) |
| `MonitorGoalStore`/`MonitorGoalRuntime` | ein aktiviertes Ziel existiert | `watched_value_entity_ids`, `watched_person_entity_ids`, `watched_nobody_home_person_ids`, `nobody_home_uses_household` |
| SituationEvaluator / Routinen | Ereigniskategorien konfiguriert | alle ausgewählten (konservativ), Rang wie oben |
| V12-Kontext | `ProactiveRuntime.enabled` | alle ausgewählten, Rang über `is_relevant_event` |

Eine existierende, aber deaktivierte Runtime ist kein Verbraucher
(`proactive_context.enabled is False`, Tracker ohne Zyklus, Store ohne
aktivierte Ziele, EffectMonitor ohne Effect). Ein Verbraucherobjekt, das sein
Interesse nicht deklariert (Test-Double, künftiger Verbraucher), gilt als an
allem interessiert (konservativ).

**Aktualisierung:** Die spezifischen Mengen werden gepusht. `EffectMonitor`
(Registrierung, Erfüllung, Timeout, Schließen), `ThermalExperienceTracker`
(Start, Ende, Abbruch, Restore) und `MonitorGoalStore` (Laden, Reload,
Speichern, Ändern, Aktivieren/Deaktivieren, Löschen – jeweils erst nach
erfolgreichem Schreiben) rufen ihre Listener; `refresh()` baut daraus eine
zusammengeführte Menge. Die hausweiten Flags sind an die Optionen und das
V12-Objekt gebunden (Schlüssel aus Identität der Options-Mapping, Kategorien,
Routinen-Option, V12-Objekt und `enabled`) und werden bei jeder Änderung neu
berechnet – auch ohne Reload. Ein Options-Update lädt die Integration ohnehin
neu (`_async_update_listener`), dabei entsteht ein neuer Index.

### 2.3 Queue- und Backpressure-Semantik

- Grenze `MAX_PENDING_EVENTS = 4096` lebende Einträge (unverändert, nicht
  vergrößert). Kein Task je Ereignis, höchstens ein Worker je Generation.
- Volle Queue, eingehendes Ereignis: zuerst weicht ein reiner Historien-
  eintrag (zusammengeführte Auswertung, s. 2.4 – dabei geht keine Auswertung
  verloren, `view_evictions`), dann – für LOSSLESS/CRITICAL – die älteste
  noch ausstehende COALESCIBLE-Auswertung (`dropped_coalescible`), dann – nur
  für CRITICAL – das älteste LOSSLESS-Ereignis (`dropped_lossless`). Nichts
  verdrängt ein CRITICAL-Ereignis. Findet sich kein Platz, wird das
  eingehende Ereignis abgewiesen und unter seinem Rang gezählt.
- Harte Systemgrenze (ehrlich): Sind 4096 kritische Ereignisse gleichzeitig
  ausstehend, wird ein weiteres kritisches Ereignis abgewiesen
  (`dropped_critical`, Error-Log mit Präfix `SAFETY:`, höchstens einmal je
  Minute). In allen Lasttests unten: `dropped_critical == 0`.
- Ein verdrängtes oder abgewiesenes Ereignis hinterlässt einen **Gap-Record**
  je Entität (erster `old_state`, letzter `new_state`, Sequenzbereich). Die
  Hausansicht zeigt die Entität vor dem Bereich im alten, ab seinem Ende im
  letzten Zustand: Ein späterer Zustand leakt nie in ein früheres Ereignis;
  innerhalb des Bereichs kann sie höchstens den älteren Zustand zeigen.
- Warnungen: gewöhnliche Verluste „HomeIntent is behind on state changes …“
  höchstens einmal je Minute; kritische separat als Error mit `SAFETY:`.

### 2.4 Coalescing-Semantik

- Ein neues COALESCIBLE-Ereignis einer Entität, deren letztes ausstehendes
  Ereignis ebenfalls COALESCIBLE ist und noch nicht im Batch steckt, wird
  zusammengeführt: die Auswertung erfolgt einmal, mit dem `old_state` des
  ersten und dem `new_state`, den Attributen, dem Zeitstempel und dem
  Kontext des letzten Ereignisses (`_MergedEvent`).
- Steht das frühere Ereignis direkt am Ende der Queue (nichts dazwischen),
  wird der Eintrag in place erweitert – exakt. Liegen andere Ereignisse
  dazwischen, bleibt der frühere Eintrag als **reiner Historieneintrag** in
  der Queue (keine Auswertung, aber die Hausansicht wendet ihn an), damit die
  Ereignisse dazwischen das Haus exakt sehen. Coalescing verändert die
  historische Hausansicht damit nicht.
- Geht eine COALESCIBLE-Auswertung durch Verdrängung verloren, übernimmt die
  nächste COALESCIBLE-Auswertung derselben Entität deren `old_state`.

### 2.5 Historische Zustandsrekonstruktion

`_HouseView` aus 7.9.5, verallgemeinert auf Änderungen
`(rewind_seq, apply_seq, entity, old, new)`: Queue-Einträge (auch Historien-
einträge) mit ihrer Sequenznummer, Gap-Records mit Anfang/Ende. Je Entität
wird auf den `old_state` der frühesten Änderung zurückgespult, dann wird in
Sequenzreihenfolge vorgespult. `off → on → off`, Interleaving, Attribute und
Einheit je Ereignis, neue Entitäten vor ihrem ersten Ereignis, direkter
Einstieg `async_handle_state_changed()` – unverändert wie 7.9.5 (Tests
laufen mit).

Gefilterte Ereignisse (kein Interesse) werden nicht protokolliert: Eine
gefilterte Entität wird von keinem aktiven Verbraucher aus der Hausansicht
gelesen (hausweite Verbraucher machen alle ausgewählten Entitäten
interessant; spezifische Verbraucher lesen nur ihre eigenen, beobachteten
Entitäten bzw. – Monitorziele – einen eigenen frischen Snapshot).

### 2.6 Snapshot-Fehler, Retry und Backoff

- Der Worker baut den Snapshot, **bevor** er Ereignisse entnimmt. Scheitert der
  Aufbau, bleibt die Queue unverändert (gleiche Reihenfolge, keine Doppel-
  verarbeitung).
- Retry mit exponentiellem Backoff `50 ms · 2^(n-1)` (max. 5 s), jeweils mit
  `await asyncio.sleep` (Event-Loop-Yield, kein Hotloop), weiterhin ein
  Worker. Metriken `snapshot_failures`, `snapshot_retries`; Error-Log
  höchstens einmal je Minute mit Anzahl der wartenden Ereignisse.
- Nach `SNAPSHOT_MAX_ATTEMPTS = 5` Fehlschlägen in Folge (Pausen 50+100+200+400+800 ms ≈ 1,55 s) wird der
  vorderste Batch ohne Registry-Daten ausgewertet (`snapshot_from_state`):
  CRITICAL und LOSSLESS erreichen ihre Verbraucher (der Thermal-Tracker wird
  dabei übersprungen, er würde fehlende Sensoren als entfernte Messquelle
  werten); COALESCIBLE werden als `dropped_coalescible` gezählt; Error-Log
  (mit `SAFETY:`, falls kritische dabei waren) und Metrik
  `snapshot_degraded_batches`. Danach beginnt für den nächsten Batch eine neue
  Serie – keine hängende Queue, kein toter Worker.
- Stop/Unload während des Backoffs bricht den Worker ab (Cancel); überlebt er
  den Abbruch, endet er beim nächsten Generationstest. Ein alter Worker
  greift nach Reload weder auf die neue Queue noch auf die neuen Metriken zu.

### 2.7 Stop-/Unload-Verhalten

`stop()` meldet den Listener ab, markiert die Generation als gestoppt,
bricht den Worker ab, löst die Interest-Listener, entfernt Expiry-Handler und
Pending-Probe des EffectMonitors und gibt Queue, Indizes und Gap-Records
frei. Die Metriken der Generation bleiben lesbar; ein Neustart beginnt mit
neuen Metriken und genau einem neuen Worker beim ersten Ereignis.

### 2.8 ExpectedEffect unter Last

`EffectMonitor.watched_entity_ids` (read-only, bei jeder Änderung neu
berechnet, Listener). Zusätzlich: Läuft die Frist eines Effects ab, während
eine Zustandsänderung seiner Entität noch in der Queue wartet
(`SituationRuntime.has_pending`, O(1)), wartet der Ablauf auf diese
Auswertung (Prüfung alle 100 ms, höchstens 60 s). Ein Effect läuft so nicht
wegen Queue-Überlast fälschlich ab; ohne wartendes Ereignis läuft er wie bisher
zur Frist ab.

### 2.9 MonitorGoalStore-Cache

- `_records: tuple[MonitorRecord, ...] | None`, `_load_lock`, `_write_lock`,
  `read_count`.
- Erster Ladevorgang Single-Flight (100 parallele Aufrufe → 1 Read); danach
  kein Dateizugriff durch `async_load()`. `async_reload()` liest bewusst neu.
- Ein Read mit I/O-Fehler (außer „Datei fehlt“) wird nicht gecacht:
  `async_load()` liefert wie bisher `()` und versucht es beim nächsten Aufruf
  erneut; `async_save()`/`async_delete()` brechen dann mit dem Fehler ab,
  statt die gespeicherten Ziele mit dem neuen allein zu überschreiben (vorher
  möglicher Datenverlust).
- `async_save()`/`async_delete()` unter einem Schreib-Lock auf Basis des
  Caches; atomisches Schreiben (`mkstemp` + `fsync` + `os.replace`, wie
  bisher); Cache und Interest-Sets erst nach erfolgreichem Schreiben; bei
  Schreibfehler bleibt beides unverändert. Löschen einer unbekannten ID
  schreibt nicht. Speichern einer vorhandenen ID ersetzt genau diesen Record.
- Abgeleitete Mengen synchron und ohne Dateizugriff lesbar (s. 2.2).
- `async_setup_entry()` lädt die Ziele einmal vor dem Start der EventRuntime.

### 2.10 Performance

Bei aktiven hausweiten Verbrauchern kostete 7.9.5 je ausgewertetem Ereignis
O(Entitäten) dreifach: `SituationEvaluator.evaluate()` baute ein Dict über das
ganze Haus (gebraucht nur von der Regel „Fenster offen beim Heizen“), die
Anwesenheit wurde durch Scannen aller Entitäten ermittelt, und jede 256er-
Charge spulte die ganze Queue zurück. 7.9.6: das Dict nur noch in dieser
Regel (gleiches Ergebnis), Personen-IDs in der Hausansicht (`_ViewDict`),
Charge 1024 (Ansicht für jede Chargengröße exakt; weiterhin ein Snapshot je
Charge, Yield nach 20 ms Arbeit). Stub, 6000 Entitäten, 4096 ausstehende
Auswertungen: 5,2 s → 1,2 s.

## 3. Metriken (`SituationRuntime.metrics`, `EventRuntimeMetrics`)

`received`, `filtered_unselected`, `filtered_no_interest`, `queued`,
`processed`, `coalesced`, `dropped_coalescible`, `dropped_lossless`,
`dropped_critical`, `view_evictions`, `snapshot_builds`, `snapshot_failures`,
`snapshot_retries`, `snapshot_degraded_batches`, `worker_starts`,
`max_queue_depth`. Je Runtime-Start (Generation) neu; ein alter Worker zählt
nur in seine eigene Generation. `received = filtered_unselected +
filtered_no_interest + queued + abgewiesene Ereignisse`. Die Datei-Reads der
Monitorziele zählt `MonitorGoalStore.read_count`.

## 4. Ergebnisse

### 4.1 Synchroner 6000-Ereignisse-Test gegen echtes Home Assistant

`tests_ha/test_event_runtime_hardening.py` (HA 2026.9.2, Python 3.14): 6000
ausgewählte Sensoren, alle Änderungen in einer synchronen Schleife ohne
`await`, danach eine Leuchte mit ExpectedEffect `off → on → off`, Rauch- und
Wassermelder `off → on`; Kategorie `safety` aktiv (hausweiter Verbraucher).
Zähler nur des Bursts (HA ändert beim Setup eigene Zustände):

| Metrik | Wert | Gate |
|---|---:|---|
| received | 6004 | |
| filtered_unselected | 0 | |
| filtered_no_interest | 0 | |
| queued | 4100 | |
| processed | 4096 | |
| coalesced | 0 | |
| dropped_coalescible | 1908 | gewöhnliche Messwerte jenseits der Grenze 4096, gezählt |
| dropped_lossless | 0 | == 0 ✔ |
| dropped_critical | 0 | == 0 ✔ |
| snapshot_builds | 4 | |
| snapshot_failures | 0 | |
| snapshot_retries | 0 | |
| monitor_store_reads | 0 | ≤ 1 ✔ |
| worker_starts | 1 | ≤ 1 ✔ |
| max_queue_depth | 4096 | ≤ 4096 ✔ |
| elapsed_seconds | 0,712 | |
| pending_tasks | 0 | == 0 ✔ |

Zusätzlich: 1 Task durch den Burst erzeugt (der Worker), größte Event-Loop-
Lücke während des Abarbeitens 0,148 s (Probe alle 5 ms), Rauch und Wasser
ausgewertet, Leuchte `on` und `off` ausgewertet, Effect erfüllt statt
abgelaufen, 0 Thermal-Schreibvorgänge, Integration danach sauber entladen
(kein HomeIntent-Worker mehr).

Standardkonfiguration ohne aktive Verbraucher (V12 existiert, ist aber aus),
6000 Ereignisse: `received 6000`, `filtered_no_interest 6000`, `queued 0`,
`processed 0`, `snapshot_builds 0`, `worker_starts 0`, 0 Monitor-Reads,
0 Thermal-Schreibvorgänge.

**Vergleich mit 7.9.5** (gleicher Ablauf, echtes HA, Messskript gegen beide
Stände): 7.9.5 3,61 s, Rauch **nicht**, Wasser **nicht**, Leuchte **nicht**
ausgewertet, Effect blieb offen (wäre fälschlich abgelaufen), 1908 verworfen.
7.9.6 0,74 s, alles ausgewertet, ebenfalls 1908 gewöhnliche Messwerte
verworfen (Queue-Grenze bewusst nicht vergrößert). Der 7.9.4-Sturmtest
(`tests_ha/test_startup_event_storm.py`, mit Yields) läuft unverändert grün.

### 4.2 Datei-Read-Zahlen MonitorGoalStore

| Fall | 7.9.5 | 7.9.6 |
|---|---:|---:|
| 100 parallele erste `async_load()` | – | 1 |
| 50 Personenwechsel ohne Ziele (nach erstem Laden) | 50 | 0 |
| 50 Personenwechsel mit Ziel | 50 | 0 |
| 50 Wertänderungen beobachteter Sensor | ≥ 50 | 0 |
| 50 Wertänderungen nicht beobachteter Sensor | 0 | 0 |
| Echtes HA, 6000 Ereignisse | – | 0 (ein Read beim Setup) |

### 4.3 Testzahlen

| Suite | Ergebnis |
|---|---|
| Stub-Suite Python 3.12 (`-W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning`) | 9745 passed, 12 skipped, 0 failed (7.9.5: 9703 passed, 12 skipped) |
| Stub-Suite Python 3.13 (gleiche Flags) | 9745 passed, 12 skipped, 0 failed |
| Stub-Suite Python 3.13 + hassil 3.12 (HA-Version) | 9745 passed, 12 skipped, 0 failed |
| `tests_ha` (HA 2026.9.2, Python 3.14, RuntimeWarning = Fehler) | 21 passed (19 + 2 neu) |
| Neu: `tests/test_event_runtime_hardening_796.py` | 42 Fälle |

Angepasste bestehende Tests (keine entfernt, keine Assertion gelockert):
sieben 7.9.4/7.9.5-Tests (Stub) und zwei HA-Tests liefen ohne aktiven
Verbraucher und bekamen eine Ereigniskategorie bzw. einen ExpectedEffect in
die Konfiguration, weil 7.9.6 genau diesen Fall nicht mehr einreiht; ihre
Assertions sind unverändert. `test_failing_snapshot_build_neither_ends_the_worker_nor_hides_it`
schrieb den Batch-Verlust von 7.9.5 fest („2 state changes not evaluated“) –
er erwartet jetzt, dass alle drei Ereignisse ausgewertet werden.

### 4.4 Gates

| Gate | Ergebnis |
|---|---|
| Sprachverständnis-Gate (hassil 3.11 und 3.12) | je 463 passed |
| Korpus-Signaturen gegen 7.9.5 | 4101 Sätze, 0 geändert; neue Baseline 7.9.6 mit 3 begründeten Einträgen (`corpus-signatures-7.9.6-begruendung.md`) |
| Shadow-Vergleich | 2131 EQUIVALENT, SAFETY_DRIFT 0 |
| Entwicklungs-Benchmark 7.7 / 7.8 | `unsafe_execution_count` 0 / 0, SAFETY 0 |
| Arbiter-Shadow | 2157 EQUIVALENT, 4 BEHAVIOR_CHANGE (Musik, wie seit 7.9.4), 0 SAFETY_DRIFT; 7.9.5: 7 NOT_MEASURABLE, jetzt 0 (die Kalender-Lesefragen sind durch das realistische Double messbar) |
| Arbiter-Shadow mit `-W error::RuntimeWarning` über `scripts/warning_gate.py` | 0 Funde (7.9.5: 7 Funde, Exit 1) |
| V9-Latenz 5000 | bestanden, z. B. `v9_group_reference_shape` p95 7,7 ms (Budget 100) |
| Automationssprache 5000 | p95 16,4 ms (Budget 100) |
| V10 / V11 / V12 / Learning Center | alle Budgets eingehalten (V12 `event_storm_1000` 3,4 ms) |
| Event-Summary 5000/7 Tage | p95 132 ms (Budget 500) |
| Pyright voll, CI-Strict-Liste (+ `event_priority.py`, `event_interest.py`), V11, V12, Learning Center | je 0 Fehler |
| Pyflakes, `git diff --check` | 0 |
| HA-Smoke-Skripte (lokal mit HA 2026.9.2) | 4/4 OK |
| Hassfest, HACS, Docker-Smoke | CI grün (Branch-Lauf #444) |

### 4.5 CI

Branch-Lauf #444 (`f35fe8a`): alle 9 Jobs grün, darunter Tests 3.12/3.13
mit RuntimeWarning als Fehler, hassil-Job, Live-Testbett, Options Flow
(`tests_ha`), Hassfest, HACS, HA-Smoke, Learning-Center-UI. Merge-Commit und
Nightly: siehe Release-Notes und PR.

### 4.6 Live-Testbett nach Neustart

Lokal, HA 2026.9.2: `sim/fresh_ha.sh` (frische Installation, 136 freigegebene
Entitäten), danach **Neustart** von Home Assistant mit installiertem HomeIntent
(HTTP `/` 200 nach 14 s inklusive 8 s Einschwingzeit), dann `runner.py --strict`
über **alle** Kategorien inklusive Proaktiv: **230/230**.
`check_log.py` (seit 7.9.6 zusätzlich RuntimeWarning, „never awaited“, „Task was
destroyed but it is pending“, „Something is blocking Home Assistant“, global):
0 Befunde. Einziger Traceback im Log: `libturbojpeg` der HA-Kamerakomponente
(wie 7.9.5, kein HomeIntent-Code). Keine „behind on state changes“- oder
`SAFETY:`-Meldung.

## 5. Verbleibende ehrliche Systemgrenzen

- Mehr als 4096 gleichzeitig ausstehende **kritische** Ereignisse: weitere
  kritische werden abgewiesen (gezählt, `SAFETY:`-Log). Nicht erreicht in
  allen Tests; ein Haus erzeugt so viele Alarmflanken praktisch nicht ohne
  Fehlfunktion des Senders.
- Kritische Ereignisse werden in globaler Reihenfolge verarbeitet: ihre
  Latenz ist die Abarbeitungszeit der vor ihnen wartenden Ereignisse (echtes
  HA, volle Queue, 6000 Entitäten: < 1 s; s. Abschnitt 4).
- Nach einer Verdrängung zeigt die Hausansicht für die betroffene Entität
  zwischen erstem und letztem verlorenen Ereignis höchstens den älteren,
  nie einen späteren Zustand.
- Dauerhaft fehlschlagender Snapshot: Auswertung ohne Registry-Daten (keine
  Bereiche) und ohne Thermal-Tracker; COALESCIBLE-Ereignisse dieser Chargen
  werden gezählt verworfen.
- Fenster, die erst während eines laufenden Thermal-Zyklus einem Bereich
  zugeordnet werden, gehören nicht zum Interesse dieses Zyklus (erst ab dem
  nächsten).
- Eine beschädigte (nicht parsebare) Monitorziel-Datei gilt wie bisher als
  leer und wird beim nächsten Speichern ersetzt (Verhalten wie 7.9.5).
- Hassfest, HACS-Validierung und der Docker-Smoke laufen nur in der CI (in
  der lokalen Umgebung gibt es keinen Docker-Daemon).

## 6. Umsetzungsmatrix

Abkürzungen: **H** = `tests/test_event_runtime_hardening_796.py`, **C** =
`tests/test_event_runtime_correctness_795.py`, **S** =
`tests/test_startup_event_storm_794.py`, **HA** =
`tests_ha/test_event_runtime_hardening.py`, **HAs** =
`tests_ha/test_startup_event_storm.py`, **HAc** =
`tests_ha/test_event_runtime_correctness.py`, **ER** = `event_runtime.py`,
**EI** = `event_interest.py`, **EP** = `event_priority.py`, **MG** =
`monitor_goal.py`, **EM** = `effect_monitor.py`, **TT** = `thermal_tracker.py`.

| Anforderung | Produktionscode | Regressionstest | Ergebnis |
|---|---|---|---|
| 1 Ausgangsbasis geprüft (origin/main = v7.9.5 = dbb42d9, sauber), Branch angelegt | – | – | erfüllt, keine Abweichung |
| 2 Kein Task je `state_changed` | ER `async_enqueue_state_changed` (@callback, unverändert) | H `test_critical_events_behind_a_full_queue…` (1 Task), S `test_burst_of_selected_events…`, HA, HAs | grün |
| 2 Höchstens ein Worker | ER `_enqueue` (Worker nur, wenn keiner läuft) | H (`worker_starts <= 1` in allen Lasttests), HA | grün |
| 2 Keine blockierenden Dateizugriffe im Callback | ER/EI (nur Attribut-/Mengenzugriffe) | H `test_callback_does_no_snapshot_registry_or_file_work`, `test_no_active_consumer…` (0 `read_text`) | grün |
| 2 Zwischenzustände `off → on → off` korrekt | ER `_HouseView` | C `test_each_event_sees_its_own_new_state…`, H `test_critical_intermediate_states…` | grün |
| 2 Jedes Ereignis sieht eigenen Zustand und Attribute | ER `_HouseView`, `_MergedEvent` | C `test_attributes_and_derived_fields…`, H `test_interleaved_value_changes…` | grün |
| 2 Übriges Haus zum Ereigniszeitpunkt | ER `_HouseView.for_batch` + Gaps | C `test_events_still_queued_behind…`, H `test_a_dropped_later_change_never_leaks…`, HAc | grün |
| 2 Feste Auswahl O(1) | `hass_entities.SelectedEntityFilter` (unverändert) | C `test_fixed_selection_filter…`, `test_runtime_uses_the_set_filter…` | grün |
| 2 Alter Worker verarbeitet nach Reload keine neue Queue | ER `_Generation`, `_owns` | C `test_a_worker_surviving…`, H `test_unload_during_snapshot_retries…` | grün |
| 2 ThermalTracker schreibt ohne Zyklus nicht | TT (unverändert) + ER überspringt ohne Zyklus | S `test_thermal_tracker_without_active_cycle…`, H `test_no_active_consumer…`, HA | grün |
| 3.1 Critical bei voller Queue nicht verworfen | ER `_make_room`, `_evict`; EP | H `test_critical_events_behind_a_full_queue…`, HA; Repro `critical` | grün (7.9.5: `smoke_processed False`) |
| 3.2 Snapshot-Fehler verliert keinen Batch | ER `_async_drain` (Snapshot vor Entnahme), `_async_snapshot_failed` | H `test_one_failed_snapshot…`, C `test_failing_snapshot_build…`; Repro `snapshot` | grün (7.9.5: 3 von 3 verloren) |
| 3.3 Consumer-/Interest-Filter | EI `EventInterestIndex` | H `test_no_active_consumer…`, HA `test_default_configuration…`; Repro `no_consumers` | grün (7.9.5: 1 Queue-Eintrag, 1 Worker) |
| 3.3 Existierende, deaktivierte Runtime ist kein Verbraucher | EI `_HouseWide`, ER `_async_process_state_changed` | H `test_no_active_consumer…` (echte `ProactiveRuntime` aus), `test_enabled_proactive_context_is_a_consumer` | grün |
| 3.4 MonitorGoalStore liest nicht je Ereignis | MG Record-Cache | H `test_monitor_store_events_read_nothing…`; Repro `monitor_reads` | grün (7.9.5: 50 Reads) |
| 3.5 RuntimeWarning behoben (Ursache) | `tests/_ha_stub.ServiceMock` liefert `ServiceResponse` | H `test_service_stub_answers_like_home_assistant`, `test_calendar_read_awaits_everything` | grün (Gate 7.9.5: 7 Funde, 7.9.6: 0) |
| 4 Zentrale Klassifizierung `EventPriority` | EP | H `test_critical_classes_are_the_existing_safety_lists…`, `test_classification_of_house_wide_consumers` | grün |
| 4.1 CRITICAL aus bestehenden Safety-Listen, keine zweite Liste | EP `CRITICAL_DEVICE_CLASSES` = Vereinigung | H `test_critical_classes…` | grün |
| 4.1 Critical nie wegen gewöhnlicher Ereignisse verworfen | ER `_make_room` | H Lasttests, HA (`dropped_critical == 0`) | grün |
| 4.2 LOSSLESS: ExpectedEffect | EM `watched_entity_ids`, EI | H `test_expected_effect_sees_its_intermediate_state…`, `test_interest_follows_effect…` | grün |
| 4.2 LOSSLESS: Monitorziel-Entitäten/Wertsensoren | MG `watched_value_entity_ids`, EI | H `test_value_monitor_gets_each…`, `test_specific_consumers…` | grün |
| 4.2 LOSSLESS: Personen der Monitorziele | MG `watched_person_entity_ids`, EI | H `test_person_leaving_and_returning_under_load[monitor_only]` | grün |
| 4.2 LOSSLESS: Nobody-Home-Personen (auch Haushalt) | MG `watched_nobody_home_person_ids`, `nobody_home_uses_household` | H `test_interest_follows_monitor_goal_saves_and_deletes` | grün |
| 4.2 LOSSLESS: Fenster-/Türflanken bei aktiver Kategorie | EI (nicht Zahl→Zahl ⇒ LOSSLESS) | H `test_classification…`, `test_safety_person_effect_and_monitor_edges…` | grün |
| 4.2 LOSSLESS: aktiver Thermal-Zyklus | TT `watched_entity_ids`, EI | H `test_interest_follows_thermal_cycles` | grün |
| 4.2 LOSSLESS: aktivierte Routinen-Erkennung | EI `_HouseWide.routine` | H `test_classification…` (Routine ⇒ LOSSLESS) | grün |
| 4.2 LOSSLESS: entscheidungsrelevante Zwischenzustände | EI (nur Zahl→Zahl-Sensoren coalescible) | H `test_value_monitor…` (`unavailable`), `test_safety_person…` | grün |
| 4.3 COALESCIBLE nur sichere Ereignisse | EI `classify` | H `test_classification…`, `test_safety_person_effect_and_monitor_edges_are_never_merged` | grün |
| 5 Weiterhin ein Worker, keine Task-pro-Event-Architektur | ER | H, HA | grün |
| 5 Keine unbegrenzte Queue (4096 unverändert, nicht vergrößert) | ER `MAX_PENDING_EVENTS` | H (`max_queue_depth <= 4096`), HA (`max_queue_depth 4096`) | grün |
| 5 Critical verdrängt coalescible/entfernbare Einträge | ER `_make_room` | H `test_critical_events_behind…`, `test_critical_displaces_lossless_only…` | grün |
| 5 Lossless im Lasttest nicht verworfen | ER `_make_room` (Coalescible weicht zuerst) | H Lasttests, HA (`dropped_lossless == 0`) | grün |
| 5 Deterministische Reihenfolge, eine Queue, Sequenznummern | ER `_Entry.seq`, eine deque | H `test_one_failed_snapshot…` (Reihenfolge), `test_person_leaving…` | grün |
| 5 Coalescing verfälscht Hausansicht nicht | ER Historieneinträge, `_HouseView` | H `test_interleaved_value_changes_merge_but_keep_the_exact_history` | grün |
| 5 Verworfenes späteres Ereignis leakt nicht | ER `_Gap`, `record_gap` | H `test_a_dropped_later_change_never_leaks_into_an_earlier_event` | grün |
| 5 Keine Starvation, Critical zügig | eine Queue in Sequenzordnung | H (alle Ereignisse in Reihenfolge), HA (Drain 0,7 s) | grün |
| 5 Stop/Reload sicher | ER `stop()` | H `test_unload_during_a_burst…`, S `test_stop_cancels_the_worker…` | grün |
| 6 Metriken (alle 14 + `view_evictions`, `snapshot_degraded_batches`) | ER `EventRuntimeMetrics` | H (alle Lasttests), HA | grün |
| 6 Metriken je Start neu, nicht zwischen Workern vermischt | ER `_Generation.metrics` | H `test_unload_during_a_burst…` (neue Metriken), `test_unload_during_snapshot_retries…` | grün |
| 6 Kritische Verluste separat, Safety-Warnung, rate-limited | ER `_count_drop` (`SAFETY:`-Error) | H `test_critical_displaces_lossless_only…` (1 Log bei 2 Verlusten) | grün |
| 7 Begrenzter Retry mit Backoff, kein Hotloop | ER `_async_snapshot_failed` | H `test_repeated_snapshot_failures_back_off…` | grün |
| 7 Reihenfolge, keine Doppelverarbeitung, `snapshot_failures == 1`, `snapshot_retries >= 1` | ER `_async_drain` | H `test_one_failed_snapshot_loses_nothing…` | grün |
| 7 Stop/Unload beendet Retry, alter Worker ohne Zugriff | ER `_owns`, `stop()` | H `test_unload_during_snapshot_retries…` | grün |
| 7 Critical bleibt während des Fehlers erhalten | ER | H `test_critical_event_survives_snapshot_failures…` | grün |
| 7 Endgültiger Abbruch: Critical/Lossless nicht still verworfen, Metrik, Log | ER `_async_degraded_batch` | H `test_persistent_snapshot_failure_evaluates_critical…` | grün |
| 8 Interest-Index im Speicher, O(1), Callback billig | EI | H `test_callback_does_no_snapshot…`, `test_no_active_consumer…` | grün |
| 8 Aktualisierung bei Effect registriert/erfüllt/abgelaufen | EM Listener, EI `refresh` | H `test_interest_follows_effect_registration_fulfilment_and_timeout` | grün |
| 8 Aktualisierung bei Monitorziel speichern/ändern/aktivieren/deaktivieren/löschen | MG `_replace_cache` + Listener | H `test_interest_follows_monitor_goal_saves_and_deletes`, `test_failed_monitor_write…` | grün |
| 8 Aktualisierung bei Thermal-Start/Ende | TT `_update_watched` | H `test_interest_follows_thermal_cycles` | grün |
| 8 Aktualisierung bei Optionsänderung/Reload | EI `_current_house_wide` (Schlüssel), Reload erzeugt neuen Index | H `test_house_wide_option_change_is_picked_up_without_restart` | grün |
| 8 Safety nie durch Filter entfernt | EI (CRITICAL vor allen Filtern) | H `test_critical_events_are_kept_even_without_any_consumer` | grün |
| 8 6000 Ereignisse ohne Verbraucher: 0 Queue/Processed/Snapshot/Worker | EI, ER | H `test_no_active_consumer…`, HA `test_default_configuration…` | grün |
| 9 EffectMonitor read-only `watched_entity_ids`, mehrere Effects | EM | H `test_interest_follows_effect…` | grün |
| 9 Effect läuft nicht wegen Queue-Überlast ab | EM `set_pending_probe`, ER `has_pending` | H `test_effect_deadline_waits_for_its_still_queued_state_change`, `test_effect_still_expires_when_nothing_is_queued` | grün |
| 10 Thermal-Interest nur mit aktivem Zyklus (Klima, Sensor, Außen, Fenster) | TT `watched_entity_ids` | H `test_interest_follows_thermal_cycles` | grün |
| 10 Keine Dateioperation je Zustandsänderung | TT (unverändert), ER | S, H, HA (`writes == []`) | grün |
| 11.1 100 parallele erste Loads → 1 Read; danach keine | MG `_async_ensure_loaded` | H `test_monitor_store_hundred_parallel_first_loads_read_once` | grün |
| 11.1 `async_reload()` liest bewusst neu | MG `async_reload` | H `test_monitor_store_reload_reads_again_on_purpose` | grün |
| 11.1 Fehlgeschlagener Read: kein Cache, Retry möglich | MG `async_load` | H `test_monitor_store_failed_read_is_not_cached…` | grün |
| 11.2 Save unter Lock, atomar, Cache erst nach Erfolg | MG `async_save`, `_write` | H `test_monitor_store_concurrent_saves…`, `test_monitor_store_failed_write_keeps_previous_cache` | grün |
| 11.2 Zwei konkurrierende Saves; vorhandene ID ersetzt | MG | H `test_monitor_store_concurrent_saves_and_save_with_delete_lose_nothing` | grün |
| 11.3 Delete konsistent, unbekannte ID ohne Write | MG `async_delete` | H `test_monitor_store_delete_of_a_missing_id_writes_nothing` | grün |
| 11.3 Save/Delete während Load | MG | H `test_monitor_store_save_and_delete_during_the_first_load` | grün |
| 11.4 Abgeleitete Interest-Sets synchron | MG `_MonitorInterest` | H `test_interest_follows_monitor_goal…`, `test_failed_monitor_write…` | grün |
| 11.5 Read-Zahlen (50 Personen/Wertänderungen, mit/ohne Ziel: 0) | MG, `__init__.py` (ein initiales Laden) | H `test_monitor_store_events_read_nothing_after_the_first_load`, HA (`monitor_store_reads 0`) | grün |
| 12 Calendar-RuntimeWarning: Ursache behoben, nichts unterdrückt | `tests/_ha_stub.py` | H `test_calendar_read_awaits_everything` | grün |
| 12/17 CI-Gate RuntimeWarning (auch bei GC), „Task was destroyed“ | `scripts/warning_gate.py`, `ci.yml` (Arbiter, Repro, alle pytest-Läufe mit `-W error`) | Gate gegen 7.9.5: Exit 1 | grün |
| 13.1 Critical hinter 6000 Ereignissen (synchron, ohne Yield) | ER | H `test_critical_events_behind_a_full_queue_of_ordinary_events` | grün |
| 13.2 Kritische Zwischenzustände | ER | H `test_critical_intermediate_states_reach_the_safety_evaluation` | grün |
| 13.3 ExpectedEffect unter Last | ER, EM | H `test_expected_effect_sees_its_intermediate_state_under_load` | grün |
| 13.4 Personenwechsel unter Last | ER, EI | H `test_person_leaving_and_returning_under_load` (2 Varianten) | grün |
| 13.5 Wertmonitor unter Last | ER, EI | H `test_value_monitor_gets_each_evaluable_value_in_order_under_load` | grün |
| 13.6 Snapshot-Retry (alle Unterpunkte) | ER | H 5 Snapshot-Tests | grün |
| 13.7 Kein aktiver Verbraucher | EI | H `test_no_active_consumer…` | grün |
| 13.8 Monitor-Cache (alle Unterpunkte) | MG | H 8 Monitor-Store-Tests + 2 Interest-Tests | grün |
| 13.9 Historische 7.9.5-Semantik | ER `_HouseView` | C (15 Fälle, 4 mit ergänzter Verbraucher-Konfiguration/neuer Snapshot-Erwartung) | grün |
| 13.10 Coalescing-Semantik | ER, EI | H `test_adjacent_value_changes…`, `test_interleaved…`, `test_safety_person_effect…`, `test_a_merge_after_a_critical_event…` | grün |
| 13.11 Unload während Last, Reload genau ein Worker | ER `stop()` | H `test_unload_during_a_burst_stops_everything…` | grün |
| 14 Bestehender HA-Sturmtest erhalten | – | HAs (Verbraucher ergänzt) | grün |
| 14 Echter HA-Test 6000 synchron ohne Yield | – | HA `test_six_thousand_changes_without_a_yield…` | grün |
| 14 Echter HA-Test Standardkonfiguration ohne Verbraucher | – | HA `test_default_configuration_without_consumers_queues_nothing` | grün |
| 15 Callback ohne Datei/Snapshot/Registry/Coroutine/Task/lineare Suche | ER, EI | H `test_callback_does_no_snapshot…` | grün |
| 15 Harte Gates (`worker_starts ≤ 1`, `dropped_critical/lossless == 0`, `pending_tasks == 0`, `monitor_store_reads ≤ 1`) | ER | HA | grün |
| 15 Laufzeit im Vergleich zu 7.9.5 dokumentiert | ER, `situation.py` (Performance) | HA-Vergleich (Abschnitt 4) | 0,74 s statt 3,61 s |
| 16 Keine Scheinlösungen (Queue nicht vergrößert, keine Unterdrückung, keine entfernten Tests) | – | Review dieses Berichts | erfüllt |
| 17 Arbiter-Shadow mit RuntimeWarnings als Fehler | `scripts/warning_gate.py` | CI-Schritt | 0 Funde |
| 18 Vollständige Validierung | – | Abschnitt 4 | s. dort |
| 19 Dokumentation | `docs/umsetzung-7.9.6.md` | – | dieser Bericht |
| 20 README / Release Notes | `README.md` | `tests/test_release_version_consistency.py` | grün |
| 21 Version 7.9.6, Korpus-Baseline begründet | `manifest.json`, `README.md`, `ci.yml`, `docs/perf/corpus-signatures-7.9.6*` | `tests/test_release_version_consistency.py`, Korpus-Gate | grün |
| 22 PR, CI, Merge, Tag, Release | – | – | s. Abschnitt 4 / Abschlussbericht |
