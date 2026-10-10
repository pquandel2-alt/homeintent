# HomeIntent 7.9.7 – Event-Prioritäten, Interest-Matrix und begrenzte Retention

Ausgangsstand: `origin/main` = Tag `v7.9.6` =
`fff609def5addf86d63267cb13220a2ad0f653e9` (geprüft am 10. Oktober 2026
mit `git fetch --all --tags --prune`; kein neuerer Commit auf `main`).
Arbeitszweig: `fix/7.9.7-event-priority-memory-cleanup`.

7.9.7 ist ein Stabilitäts- und Korrektheits-Bugfix. NLU, öffentliche
Services, Entity-Namen, Config Flow, Ausführungsregeln und Safety-Grenzen
ändern sich nicht. Die Single-Worker-Architektur, Snapshot-Retry und die
historische Zustandsrekonstruktion aus 7.9.4–7.9.6 bleiben erhalten.

## 1. Reproduzierte Ursachen

### 1.1 Aktive Consumer waren nicht separat geschützt

7.9.6 unterschied nur `CRITICAL`, `LOSSLESS` und `COALESCIBLE`. Sowohl ein
beliebiges Ereignis einer breit aktivierten Kategorie als auch das konkrete
Ereignis eines wartenden ExpectedEffects waren `LOSSLESS`. Bei voller Queue
gewann deterministisch der bereits eingereihte Kategorieeintrag; das neue
Effect-Ereignis wurde verworfen. Der neue No-Yield-Regressionstest war vor
der Änderung rot: `light.flur` wurde nicht ausgewertet und der Effect blieb
offen.

### 1.2 Eine Kategorie machte den Filter praktisch hausweit

`_HouseWide.active` wurde schon durch eine einzige Kategorie gesetzt. Nach
wenigen Sonderfällen fiel `classify()` für fast alle selektierten Entitäten
auf `LOSSLESS` oder für numerische Sensoren auf `COALESCIBLE` zurück. Ein
negatives `ProactiveRuntime.is_relevant_event()` verhinderte diesen Fallback
nicht. Auch `expected_effect_missing` erzeugte damit ohne aktiven Effect
hausweites Interesse.

### 1.3 Erledigte Entries und Gap-Historie blieben erhalten

Der Worker setzte Entries auf `_DONE`, entfernte sie aber nicht aus
`latest` und löste ihre Event-/State-Referenzen nicht. Nach dem letzten Batch
gab es keinen stabilen Cleanup; `gaps` und `carry_previous` konnten deshalb
nach vollständigem Drain gefüllt bleiben. Bei fortlaufend neuen Entity-IDs
waren diese Dictionaries zusätzlich nicht separat begrenzt.

## 2. Prioritätsmodell

Die Runtime behält eine einzige FIFO-Queue in globaler Sequenzreihenfolge.
Priorität ordnet nicht um; sie entscheidet nur bei voller Queue über
Verdrängung:

| Priorität | Bedeutung | Beispiele |
|---|---|---|
| `CRITICAL` | unmittelbar sicherheits-/korrektheitskritisch | Rauch, CO, Gas, Wasser, Safety, Tamper, Problem; beide Flanken |
| `PROTECTED` | ein konkret aktiver Consumer benötigt genau dieses Event | ExpectedEffect, MonitorGoal, beobachtete Person, aktiver Thermal-Zyklus |
| `CATEGORY` | ein Event passt zur Matrix einer aktivierten Funktion | Öffnung, Geräteausfall, Licht bei Abwesenheit, Routinen-Input |
| `COALESCIBLE` | konservativer/future Consumer braucht einen Messwertstrom, der jüngste Wert genügt | reine endliche Zahl→Zahl-Änderung eines Sensors |

`EventPriority.LOSSLESS` bleibt als Quellcode-kompatibler Alias für
`CATEGORY` erhalten. Neue Logik verwendet den Namen nicht mehr.

Verdrängungsreihenfolge:

1. Reine Historieneinträge einer verschobenen Coalescing-Auswertung gehen
   zuerst; dabei geht keine Auswertung verloren.
2. `CATEGORY` darf `COALESCIBLE` verdrängen.
3. `PROTECTED` darf zuerst `COALESCIBLE`, dann `CATEGORY` verdrängen.
4. `CRITICAL` darf zuerst `COALESCIBLE`, dann `CATEGORY`, zuletzt
   `PROTECTED` verdrängen.
5. Ein Event verdrängt nie die eigene Priorität. Bei Gleichstand bleibt das
   bereits wartende FIFO-Event; der neue Eingang wird gezählt verworfen.
6. Nichts verdrängt ein bereits wartendes `CRITICAL`-Event. Ist die Queue
   vollständig kritisch, kann ein weiteres kritisches Event an der harten
   Grenze verworfen werden; das wird separat gezählt und rate-limitiert als
   `SAFETY`-Fehler protokolliert.

Damit können gewöhnliche Kategorien geschützte Events weder beim Eintritt
noch später verdrängen. Kritische Events nehmen vorhandenen niedrigeren
Rängen Platz ab, bevor ein geschütztes Event betroffen sein kann.

## 3. Kategoriespezifische Interest-Matrix

Die Entscheidung im Eventcallback liest ausschließlich Entity-ID, Domain,
`old_state`, `new_state`, `device_class`, gecachte Optionen und Consumer-
Sets. Es gibt dort keine Registry-, Datei- oder Snapshot-I/O und keinen Task.

| Kategorie | Eingereihte Kategorieevents |
|---|---|
| `safety`, `safety_alarm` | nur die bereits zentral definierten kritischen Geräteklassen/-flanken (`CRITICAL`) |
| `device_unavailable` | echte Transition nach `unavailable` |
| `opening_while_away` | Öffnungsflanken von `door`, `window`, `garage_door`, `opening`; Personentransitionen als historische Anwesenheitsinformation |
| `window_heating` | dieselben Öffnungsflanken; Climate-Transitionen als historische Heizungsinformation |
| `light_unoccupied` | Aktivierungsflanken von `light.*`; Personentransitionen als historische Anwesenheitsinformation |
| `long_running_state` | zustandsbehaftete Langläufer-Domänen (`climate`, `cover`, `fan`, `humidifier`, `light`, `media_player`, `switch`, `vacuum`, `water_heater`) |
| `routine_anomaly` | bewusst breit, aber nur wenn genau diese Kategorie und `routine_detection_enabled` aktiv sind |
| `expected_effect_missing` | keine eigene State-Subscription; nur Entities derzeit aktiver ExpectedEffects kommen über den Consumer-Index als `PROTECTED` hinein |

Mehrere Kategorien bilden die Vereinigung ihrer Zeilen. Ein positives
`ProactiveRuntime.is_relevant_event()` ergibt `CATEGORY`; ein negatives
Ergebnis ergibt ohne anderen Interessenten `None`. Konkrete Consumer werden
vor Kategorie/V12 geprüft und bleiben deshalb unabhängig davon `PROTECTED`.
Ein nicht deklarierendes künftiges Consumer-Objekt bleibt aus
Kompatibilitätsgründen konservativ: nicht numerische Events sind `CATEGORY`,
reine Messwertänderungen `COALESCIBLE`.

Person-/Climate-Supportevents sind nötig, obwohl sie allein keine Situation
auslösen: Bei `Person weg → Licht an → Person zurück` in demselben synchronen
Burst darf die Lichtauswertung nicht den späteren Anwesenheitszustand sehen.

## 4. Retention, Cleanup und harte Grenzen

Produktionsgrenzen:

- `MAX_PENDING_EVENTS = 4096` lebende Queue-Einträge;
- `MAX_HISTORY_ENTITIES = 4096` Einträge je Hilfsmap (`gaps` und
  `carry_previous`);
- `MAX_TRANSIENT_RECORDS = 9 × 4096 + 1024 = 37888` konservative Obergrenze
  der gezählten Referenz-Records aus physischer Queue/Tombstones,
  Priority-Deques, `latest`, Pending-Map und Historienmaps.

Die Zahl ist eine Obergrenze für Referenz-/Bookkeeping-Records, nicht für
Bytes und nicht die Summe mehrfach referenzierter Python-Objekte. Der
50.000er-Test erreichte `max_retained_total = 24576`.

Beim Abschluss eines Entry:

- wird `latest[entity_id]` entfernt, falls es noch exakt auf diesen Entry
  zeigt;
- werden `event`, `old_state`, `new_state` und `previous` gelöst;
- bleiben neuere Entries derselben Entity unberührt.

Nach stabilem vollständigem Drain werden ohne weiteren Await Queue,
Priority-Deques, `latest`, `gaps`, `carry_previous`, Pending-Map, Tombstones,
Snapshot-Retry-Zustand und der bounded Dedupe-Set geleert. Ein während einer
Consumer-Auswertung eintreffendes Event erhöht vorher synchron `live`; der
laufende Worker sieht es in der nächsten Schleifenrunde. Ein späteres Event
sieht den beendeten Task und startet genau einen neuen Worker. Beim Unload
markiert die Generation sich zuerst als gestoppt, meldet Listener ab, bricht
den Worker ab und leert dieselben Strukturen; die alte Generation kann eine
neue nicht drainieren.

## 5. Sicherer Historien-Degradationsmodus

Wenn für mehr als 4096 verschiedene überlastbedingt verlorene Übergänge
exakte Gap-Historie nötig wäre, werden keine weiteren Entity-IDs oder
State-Objekte behalten. Stattdessen:

1. `gaps` und `carry_previous` werden freigegeben;
2. ein skalarer Sequenzmarker bezeichnet den unsicheren Queue-Zeitraum;
3. betroffene Batches verwenden nur Entities, deren eigene old/new-States
   aus Batch oder Restqueue rekonstruierbar sind;
4. Thermal- und V12-Beobachtung sowie hausabhängige Situationsregeln werden
   in diesem Batch übersprungen; eventlokale Safety- und
   `device_unavailable`-Regeln sowie ExpectedEffect/Monitor-Verbraucher
   bleiben möglich;
5. niemals wird ein aktueller/future State als historischer State erfunden;
6. nach dem Drain wird der Marker entfernt.

Der Modus zählt Episoden (`history_degraded`), betroffene Übergänge
(`history_degraded_events`) und Batches (`history_degraded_batches`). Die
Warnung ist auf einmal je 60 Sekunden begrenzt. Die Integration bleibt aktiv
und die Queue bleibt begrenzt; fachlich abhängige Meldungen können in diesem
extremen Zustand bewusst ausbleiben, statt mit falscher Historie ausgelöst zu
werden.

## 6. Metriken

Bestehende Namen bleiben erhalten. `dropped_lossless` ist der
Kompatibilitäts-Aggregatzähler aus `dropped_category + dropped_protected`.
Neu beziehungsweise verfeinert:

- Drops: `dropped_critical`, `dropped_protected`, `dropped_category`,
  `dropped_coalescible`;
- Filter: `filtered_by_interest`, `filtered_by_category`;
- Historie: `history_degraded`, `history_degraded_events`,
  `history_degraded_batches`;
- Retention-Gauges: `retained_live`, `retained_latest`, `retained_gaps`,
  `retained_carry_previous`, `retained_priority_refs`,
  `retained_unprocessed`, `retained_tombstones`, `retained_total` und
  `max_retained_total`;
- Worker: `worker_starts`, `worker_restarts`.

Keine Metrik verwendet Entity-IDs als Label oder hält Event-/State-Objekte.
Normales Filtern wird nicht geloggt. Drop-, Critical-, Snapshot- und
Historienwarnungen sind getrennt rate-limitiert.

## 7. Snapshot-Retry und Responsiveness

Der Snapshot wird weiterhin vor dem `take()` gebaut; ein Fehler verliert
keinen Batch. Nach fünf Fehlern werden nur `CRITICAL` und `PROTECTED` ohne
Registry-Daten ausgewertet, niedrigere Ränge gezählt verworfen. Ein
fehlerhaftes Event beendet den Worker nicht.

Der Callback bleibt amortisiert O(1): Mengen-/Dictionary-Abfragen, wenige
Domain-/State-Vergleiche, Deque-Anhängen. Die Opfersuche kann nur im
Überlastpfad eine Priority-Deque linear durchlaufen; sie ist durch die
4096er-Queue hart begrenzt und vermeidet im Normalpfad zusätzlichen Aufwand.
Es gibt weiterhin keinen Task je Event, keine synchrone Batchauswertung im
Callback und genau einen Worker je Generation.

## 8. Reproduktion und verifizierte Messwerte

`scripts/event_runtime_repro_797.py` führt acht Szenarien mit der realen
`SituationRuntime` und dem HA-Teststub aus und schreibt ein JSON-Objekt.
Lokaler Lauf (Python 3.12.3):

```text
full_queue_expected_effect: effect_processed=true, effect_pending=0,
  dropped_category=1, dropped_protected=0, queue_peak=64
category_vs_protected: protected_processed=true, critical_processed=true,
  dropped_category=4, dropped_protected=0, dropped_critical=0
negative_relevance_6000: filtered=6000, processed=0, queue_peak=0,
  worker_starts=0
drain_6000: queue_peak=4096, dropped_coalescible=1904,
  retained_before_drain=20192, retained_total=0
ten_bursts: retained_after_each_burst=[0,0,0,0,0,0,0,0,0,0]
unique_50000: queue_peak=4096, dropped_coalescible=45904,
  history_degraded=1, max_retained_total=24576, retained_total=0
snapshot_retry: snapshot_attempts=2, effect_processed=true
unload_pending: pending_before_unload=4096, pending_after_drain=0,
  unfinished_tasks=0
Gesamt: dropped_critical=0, dropped_protected=0, runtime_warnings=0,
  retained_total=0, pending_after_drain=0
```

Ausgeführte lokale Prüfungen zum Zeitpunkt dieser Dokumentation:

- vollständige Stub-Suite auf Python 3.12.3, mit `RuntimeWarning` und
  `PytestUnraisableExceptionWarning` als Fehler: `9764 passed, 12 skipped`
  in 54:23;
- Event-/Startup-/History-/Effect-Gates einschließlich Warning-as-error:
  `170 passed`;
- neuer 50.000er-Test einzeln: `1 passed in 14.64s`, maximaler RSS des
  pytest-Prozesses 367224 KiB;
- Reproduktionsprogramm im CI-Warning-Gate: Exit 0, 0 RuntimeWarnings,
  45.14 s; maximaler Prozess-RSS 1117860 KiB. Dieser Wert enthält die vom
  Testharness gleichzeitig gehaltenen 50.000 simulierten Hauszustände; die
  Runtime-eigene Retention ist separat mit maximal 24576 Records gemessen;
- Sprachverständnis: `463 passed`;
- Korpus-Signaturen: 4106/4106, 0 geändert;
- Shadow: 2131/2131 equivalent, `SAFETY_DRIFT = 0`;
- Arbiter: 2161 Sätze, 4 bekannte Behavior-Changes, kein Safety-Drift;
  zusätzlicher Warning-Gate-Lauf mit 0 Findings;
- Entwicklungsbenchmarks 7.7 und 7.8: `unsafe_execution_count = 0`,
  Safety-Fehlerklasse 0;
- Safety-Properties im Nightly-Profil mit 300 Beispielen je Property:
  `40 passed` in 43:39, keine Counterexamples;
- V10-, V11-, V12- und Automationssprach-Budgets bestanden; die
  Event-Sturm-Messung für 1000 Events lag bei 8.47 ms;
- `compileall`: Exit 0;
- Pyflakes repositoryweit: 0 Befunde;
- Pyright vollständig sowie V11-, V12- und Learning-Center-Strict-Profil:
  jeweils 0 Fehler, 0 Warnungen (der fokussierte Check für
  `event_priority.py`, `event_interest.py`, `event_runtime.py` ebenfalls 0/0).

Zwei unveränderte Latenzgates überschritten auf der geteilten lokalen
Maschine ihre Budgets (`Learning Center evidence` p95 630.62 ms statt 400 ms,
Event-Summary p95 816.1 ms statt 500 ms); gleichzeitig beanspruchten fremde
Headless-Chrome-Prozesse einen erheblichen CPU-Anteil. Schwellen wurden nicht
gelockert. Maßgeblich ist die Wiederholung auf dem sauberen CI-Runner.

Echtes-HA-Tests, Live-/Proactive-Testbett und CI werden im Abschlussbericht nur
dann als bestanden bezeichnet, wenn sie tatsächlich gelaufen sind. Python
3.14 und damit die lokale `tests_ha`-Umgebung standen auf dem
Entwicklungsrechner nicht zur Verfügung.

## 9. Upgrade-Auswirkungen und Restgrenzen

- Keine Konfigurationsmigration und keine neue öffentliche API.
- Übliche Kategorieevents können bei Überlast bewusst verworfen werden;
  7.9.7 verspricht nicht, dass nie ein Event verloren geht.
- Eine vollständig mit `CRITICAL`/`PROTECTED` belegte Queue kann weitere
  Events derselben oder niedrigerer Klasse abweisen. Jeder Verlust ist
  gezählt; Critical wird separat als Error protokolliert.
- Im expliziten Historien-Degradationsmodus können hausabhängige
  Benachrichtigungen ausbleiben. Falsche Future-Historie wird nicht erzeugt.
- Die 4096er-Queue und der Single Worker bleiben absichtlich unverändert,
  damit ein Startup-Sturm Home Assistant nicht blockiert.
