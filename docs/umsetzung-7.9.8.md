# HomeIntent 7.9.8 – korrekte Consumer-Zustellung bei Degradation

Ausgangsstand: `origin/main` = Tag `v7.9.7` =
`ffb06794407655560fefa77141c5244870f71c0c` (geprüft am 11. Oktober 2026;
kein neuerer Commit auf `origin/main`). Arbeitszweig:
`fix/7.9.8-event-degradation-consumer-correctness`.

7.9.8 ist ein Stabilitäts- und Korrektheitsrelease. NLU, Intent-Erkennung,
Ausführungspolicy, Berechtigungen, Bestätigungs- und Safety-Regeln sowie
öffentliche Services bleiben unverändert.

## 1. Vor der Korrektur reproduzierte Fehler

Der neue echte No-Yield-Test füllte eine verkleinerte Kategoriequeue,
überlief die History-Grenze und reihte danach eine Änderung des von einem
aktiven Thermal-Zyklus beobachteten Temperatursensors ein. Auf dem
unveränderten 7.9.7-Stand lautete das Ergebnis:

```text
history_degraded = 1
priority = PROTECTED
dropped_protected = 0
temperature event processed = true
thermal tracker calls = 0
retained_total after drain = 0
```

Zusätzlich belegte die Analyse:

- `processed` wurde auch erhöht, wenn kein vorgesehener Consumer aufgerufen
  wurde;
- ein negativer ausschließlich proaktiver Filter erhöhte zusätzlich
  `filtered_by_category`;
- `worker_restarts` war dokumentiert, aber ohne messbaren Restart-Pfad;
- `SituationEvaluator` unterstützte `long_state_seconds`, die reale Runtime
  übergab jedoch nie einen Schwellenwert. `long_running_state` konnte daher
  über den produktiven Pfad keine Situation erzeugen.

## 2. Ursache und Degradationsmodell

7.9.7 benutzte ein einziges Boolean `degraded` für zwei fachlich
unterschiedliche Zustände. Sowohl eine unvollständige globale Historie als
auch fünf fehlgeschlagene Registry-Snapshot-Versuche setzten denselben Wert.
Thermal und Proactive waren pauschal durch `not degraded` geschützt und
wurden daher auch dann übersprungen, wenn ihre konkrete Historie sicher war.

7.9.8 verwendet `DegradationMode`:

| Modus | Bedeutung | Consumer-Verhalten |
|---|---|---|
| `NORMAL` | vollständiger Snapshot und exakte Queue-Historie | alle aktiven Consumer normal |
| `HISTORY` | Registry vorhanden, globale Historie wegen begrenzter Gap-/Carry-Daten unvollständig | konkrete Watch-Sets erhalten eine begrenzte exakte Ansicht; hausweite Consumer werden unterdrückt |
| `REGISTRY` | Snapshot nach fünf Versuchen weiterhin nicht verfügbar | eventlokale Critical-/Protected-Verarbeitung bleibt möglich; Registry-/Area-abhängige Consumer werden unterdrückt |

Ein Degradationsgrund wird nicht mehr in einen anderen umgedeutet.

## 3. Begrenzte Protected-Consumer-Ansicht

Der Callback bleibt synchron und I/O-frei. `EventInterestIndex` hält die
Watch-Sets über vorhandene Listener aktuell. Bis zu 4096 konkrete IDs werden
gecacht. Ein Überlauf speichert keine unbeschränkte Teilmenge, sondern fällt
konservativ auf Schutz aller selektierten Events zurück; eine
consumer-spezifische Auswertung wird dann unterdrückt.

Im Worker entsteht bei `HISTORY` zusätzlich zur konservativen globalen Sicht
eine Protected-Sicht:

1. Basis sind nur die aktuell konkret beobachteten Entities aus dem bereits
   gebauten Snapshot. Dynamische, bereits im Interest-Index markierte Mengen
   wie alle ausgewählten Haushaltspersonen werden dabei ebenfalls bis zum
   Watch-Limit aufgenommen.
2. Alle Änderungen aus Batch und Restqueue werden nach Sequenz zurückgespult
   und vorwärts angewendet.
3. Eine unveränderte Watch-Entity bleibt aus dem Snapshot vorhanden.
4. Spätere geschützte Änderungen werden vor früheren Aufrufen zurückgespult.
5. Ein verworfenes Protected-Event markiert seine Entity bis zum Drain als
   unsicher. Die Menge ist auf 4096 IDs begrenzt und kollabiert danach in ein
   einzelnes konservatives Overflow-Flag.
6. Unsicherheit führt zu gezählter Unterdrückung, niemals zur Erfindung eines
   Zustands.

Die Sicht wächst nicht mit irrelevanten Kategorieentities. Temporäre
Batch-/View-Listen leben nur während eines Batches und werden nicht als
dauerhafte Generation-Retention ausgewiesen.

## 4. Verhalten der geschützten Consumer

| Consumer | Benötigte Daten | `HISTORY` | `REGISTRY` |
|---|---|---|---|
| `EffectMonitor` | Event-Entity und deren exakter Zustand | zugestellt | zugestellt |
| Personenübergang | alter/neuer Personenstate; ggf. Monitor-Kontext | mit exakter Protected-Sicht; Bedingungen außerhalb des Watch-Sets werden unterdrückt | einfache Übergänge möglich; Nobody-home/hausabhängige Bedingungen unterdrückt |
| Werteänderungsmonitor | exakter Eventwert plus Recorder-Historie | zugestellt | zugestellt; Anzeigename fällt bei Bedarf auf Entity-ID zurück |
| Nobody-home | alle konkret beobachteten Haushalts-Personen | zugestellt, wenn deren Protected-Historie sicher ist | unterdrückt, weil unveränderte Personen ohne Snapshot fehlen können |
| `ThermalExperienceTracker` | Climate, Temperatur, Außenwert und Fenster des aktiven Zyklus | nur für Änderungen aus seinem vollständigen sicheren Watch-Set; sonst unterdrückt | unterdrückt, weil Area-/Binding-Snapshotdaten fehlen |
| echter `ProactiveRuntime` | vollständige historische Hausansicht und Registry-/Area-Daten | unterdrückt | unterdrückt |
| deklarierter eventlokaler zukünftiger Proactive-Consumer | Event-Sicht; `requires_complete_house_view = False` | zugestellt | nur zusätzlich mit `requires_registry_data = False` |

`MonitorGoalRuntime` kann die vom EventWorker rekonstruierte Entity-Sicht
entgegennehmen und baut dann keinen späteren frischen Hauszustand in eine
frühere Personenauswertung ein.

## 5. Zustellung, Unterdrückung und Drops

`processed` bedeutet weiterhin ausschließlich: Der eventbezogene
Runtime-Dispatch ist beendet, auch wenn darin ein Consumer bewusst
unterdrückt wurde. Registry-bedingt verworfene niedrigere Ränge werden nicht
als `processed`, sondern nur als `discarded_registry` gezählt. `processed`
ist kein Synonym für Consumer-Zustellung. Die Diagnose unterscheidet:

- `dequeued`, `processed`, `evaluation_failures`;
- `consumer_delivered` sowie `delivered_effect`, `delivered_monitor`,
  `delivered_thermal`, `delivered_proactive` (Übergabe an den Consumer, nicht
  die Behauptung, dass dessen interne Verarbeitung erfolgreich war);
- `consumer_suppressed_history`, `consumer_suppressed_registry`;
- `suppressed_protected`, `suppressed_category`, `suppressed_effect`,
  `suppressed_monitor`, `suppressed_thermal`, `suppressed_proactive`;
- `discarded_registry` für niedrigere Ränge, die nach endgültigem
  Snapshot-Ausfall bewusst nicht fachlich ausgewertet werden;
- Queue-Grenze: `rejected_queue_full`, `evicted_queue` und die bisherigen
  prioritätsspezifischen `dropped_*`-Zähler.

Damit wird Registry-Unterdrückung nicht mehr als Queue-Drop ausgegeben. Alle
Zähler sind skalare, niedrig-kardinale Felder; sie verwenden weder Entity-ID
noch dynamische Consumer-Namen als Labels. Suppressionslogs nennen Modus und
Priorität und sind auf ein Log je 60 Sekunden begrenzt.

## 6. Filtermetriken

`filtered_no_interest` und `filtered_by_interest` bleiben Aggregate. Genau
einer der neuen/geschärften Gründe steigt pro negativ gefiltertem selektierten
Event:

- `filtered_no_consumer`: keine Kategorie und kein Consumer aktiv;
- `filtered_by_category`: mindestens eine konfigurierte Kategorie lehnt das
  Event ab;
- `filtered_by_proactive`: ausschließlich der proaktive Relevanzfilter lehnt
  es ab.

Wenn Kategorie und Proactive gleichzeitig aktiv sind, hat die
Kategorieursache für den einen disjunkten Diagnosezähler Vorrang. Es gibt
keine Doppelzählung. Es gilt:

```text
received = filtered_unselected + filtered_no_interest
         + queued + rejected_queue_full
```

`queued` zählt auch akzeptierte Coalescing-Eingänge; ein später verdrängter
bereits akzeptierter Eintrag wird über `evicted_queue` sichtbar.

## 7. Worker-Semantik

`worker_starts` zählt jeden tatsächlich angelegten Worker-Task. Ein neuer
Worker nach einem vollständig beendeten Burst ist ein normaler Start.
`worker_restarts` ist die Teilmenge ungeplanter automatischer Neustarts: Der
Worker-Task endete oder scheiterte, obwohl seine Generation noch Live-Arbeit
enthält. Ein Done-Callback holt die Ausnahme ab, protokolliert sie und startet
genau einen Ersatzworker. Stop/Unload setzt die Generation vorher auf
`stopped`; eine Cancellation beim Unload löst daher keinen Restart aus.

## 8. `long_running_state`

Es existierte keine autoritative Schwellenwertquelle. 7.9.8 führt deshalb die
kleine rückwärtskompatible Option `agent_long_running_minutes` ein:

- ganze Minuten, validiert auf 0 bis 10080 (sieben Tage);
- Standard `0`, also deaktiviert und ohne neue Meldungsflut;
- im Options Flow auf Deutsch und Englisch beschriftet;
- nur wirksam, wenn zusätzlich `long_running_state` aktiviert ist;
- die Runtime übergibt den Wert in Sekunden für genau die Event-Entity an
  `SituationEvaluator`.

Nur ein bekannter aktiver Zustand der dokumentierten Domains kann melden.
`unknown`, `unavailable`, inaktive und nicht unterstützte Entities melden
nicht. `last_changed` aus dem historischen Eventzustand bestimmt die Dauer.
Mehrere Attributupdates desselben `(state, last_changed)`-Intervalls werden
durch ein auf 4096 Entities begrenztes Runtime-Dedupe genau einmal bewertet.
Eine echte Zustandsänderung erzeugt einen neuen Token.

## 9. Grenzen und Retention

- Queue: 4096 Live-Einträge.
- Worker-Batch: 1024 Einträge.
- Gap-/Carry-Historie: je 4096 Entities.
- Consumer-Watch-Index: 4096 konkrete IDs, danach konservativer Overflow.
- unsichere Protected-Historie: 4096 IDs plus ein Overflow-Flag.
- konservative Obergrenze der dauerhaft von einer aktiven Generation
  gezählten Referenz-Records: `MAX_TRANSIENT_RECORDS = 10 × 4096 + 1024 =
  41984` (keine Byte-Grenze; Strukturen referenzieren teils dieselben
  Objekte).
- `retained_total`: physische Queue, Priority-Referenzen, Latest-, Gap-,
  Carry-, Pending- und unsichere-Protected-Records der Generation.
- Zusätzlich temporär: ein auf 1024 begrenzter Batch, ein Registry-Snapshot,
  die globale Batchsicht und die auf das Watch-Limit begrenzte Protected-Sicht.

`retained_total` beschreibt damit alle von der `_Generation` gehaltenen
Referenz-Records, nicht aber bewusst außerhalb der Generation liegende
Runtime-Caches: `_seen` ist auf 4096 begrenzt und wird nach stabilem Drain
geleert; das Long-running-Intervall-Dedupe ist ebenfalls auf 4096 Entities
begrenzt und bleibt über einzelne Bursts erhalten, bis Runtime-Neustart oder
ein neues `(state, last_changed)`-Intervall es ersetzt. Registry-Snapshot,
Batch und Views sind nur während der Verarbeitung lokal und werden deshalb
nicht als dauerhafte Retention oder als Byte-Obergrenze dargestellt.

Nach stabilem Drain und nach Unload sind die Generationstrukturen und
`retained_total` null. Der Callback erzeugt weiterhin keinen Task pro Event,
liest keine Registry/Datei und baut keinen Snapshot.

## 10. Reproduktion und Tests

`scripts/event_runtime_repro_798.py` verwendet die echte `SituationRuntime`
für neun Szenarien: sichere und unsichere Thermal-Historie, ExpectedEffect,
Monitor, Proactive, dauerhaften Registry-Fehler, Long-running-state, 50000
eindeutige Events und Unload unter Last. Die Ausgabe ist kompaktes JSON.

Lokal bereits verifiziert:

```text
7.9.7 Baseline vor Änderung: 19 passed
7.9.8 neue Regressionen: 14 passed
7.9.8 Reproduktion: 9/9 Szenarien, RuntimeWarning 0,
  pending_after_drain 0, retained_total 0
  Thermal sicher: delivered 1, suppressed 0, Temperatur 20.0,
    dropped_protected 0
  Thermal unsicher: delivered 0, suppressed 3, dropped_protected 2
  ExpectedEffect delivered 1; Monitor delivered 2; Registry-Modus:
    Effect delivered 1, Thermal suppressed 1, Proactive suppressed 3
  50000 unique: queue_peak 4096, max_retained_total 24576
  Unload: pending_before_unload 4096, unfinished_tasks 0
Historische Event-/Startup-Gruppe einschließlich 7.9.8: 101 passed;
  RuntimeWarning und PytestUnraisableExceptionWarning als Fehler
Korpus-Signaturen: 4107/4107 mit Baseline, 0 geändert;
  gegenüber 7.9.7 ein neuer Test-Docstring, 0 gemeinsame Signaturen geändert
Vollständige Stub-Suite (Python 3.12.3):
  9778 passed, 12 skipped, 0 failed in 52:40;
  RuntimeWarning und PytestUnraisableExceptionWarning als Fehler
Pyright vollständig sowie V11-, V12- und Learning-Center-Strict:
  jeweils 0 Fehler, 0 Warnungen
Sprachverständnis: 463 passed
Shadow: 2131/2131 äquivalent, SAFETY_DRIFT 0
Arbiter: 2161 Fälle, 2157 äquivalent, 4 erwartete Verhaltensänderungen,
  SAFETY_DRIFT 0, RuntimeWarning-Gate 0 Findings
Entwicklungs-Benchmarks: unsafe_execution_count 0, Safety-Fehlerklasse 0
Nightly-Safety-Profil: 40 passed in 46:28, 0 Counterexamples
Echte HA-Suite lokal (Python 3.14.2, HA 2026.9.2): 21 passed, 1
  zeitabhängiger 6000er-Kategorie-Burst fehlgeschlagen. Der identische Test
  verliert auf unverändertem 7.9.7 im selben Host exakt dieselben 378
  Kategorieevents; Critical/Protected sind nicht betroffen. Options Flow:
  11 passed; echter Recorder: 1 passed. Der CI-Runner entscheidet den
  unveränderten Durchsatz-Gate.
```

`ruff` gehört nicht zu den Repository-Abhängigkeiten und es existiert keine
Ruff-Konfiguration. Ein zusätzlicher Lauf mit Ruff 0.17.0 fand repositoryweit
2192 bereits bestehende Check-Funde (1969 automatisch behebbar) und 623 nach
Ruff-Standard anders formatierte Dateien; diese themenfremde Massenänderung
ist nicht Teil des Bugfix-Releases. `compileall` und Pyflakes waren fehlerfrei.

Lokale Latenz-Gates sind auf diesem ausgelasteten Host nicht vollständig
repräsentativ: V10, V11, V12 und Automation-Registry-5000 bestanden; V9,
Learning-Evidence und Event-Summary überschritten ihre Wallclock-Grenze.
Unverändertes 7.9.7 scheiterte im identischen Host ebenfalls und war bei den
gemessenen Vergleichsfällen gleich langsam oder langsamer. Maßgeblich bleiben
die sauberen GitHub-CI-Runner; deren Ergebnis wird nicht vor dem tatsächlichen
Lauf als grün behauptet.

## 11. Restrisiken

- Die harte Queue-Grenze bleibt absichtlich bestehen. Unter extremer Last
  können Events gemäß Priorität abgewiesen oder verdrängt werden.
- Eine selbst konkret beobachtete Entity mit verlorener Protected-Historie
  wird bis zum Drain nicht geraten, sondern ihr Consumer wird unterdrückt.
- Ein vollständiger Registry-Ausfall erlaubt keine korrekten Area-/Device-
  Aussagen; entsprechende Consumer bleiben unterdrückt.
- `long_running_state` ist eventgetrieben: Ohne späteres `state_changed`
  entsteht kein eigener Timer allein für das Überschreiten der Schwelle.
