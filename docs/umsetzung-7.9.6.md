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

{{ERGEBNISSE}}

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

{{MATRIX}}
