# HomeIntent 7.9.7 – EventRuntime: Ränge, gezielte Kategorien, begrenzte Buchführung

Reiner Stabilitäts-, Überlastungs- und Speicher-Bugfix der Ereignisverarbeitung.
Keine neuen Funktionen, keine Änderung an Sprachverständnis, Intent-Auflösung,
Dialog, Bestätigungen, Berechtigungen, Execution Policy, Safety- oder
Datenschutzgrenzen, keine neuen Schreibwege zu Home Assistant.

| | |
|---|---|
| Ausgangsstand | `origin/main` = Tag `v7.9.6` = `fff609def5addf86d63267cb13220a2ad0f653e9` (geprüft mit `git fetch --all --tags --prune`, `git rev-parse origin/main`, `git rev-parse v7.9.6`; keine Abweichung) |
| Branch | `fix/7.9.7-event-priority-memory-cleanup` |
| Produktionscode | `event_priority.py`, `event_interest.py`, `event_runtime.py` (sonst nur `manifest.json`) |
| Reproduktion | `scripts/event_runtime_repro_797.py` (läuft unverändert gegen 7.9.6 und 7.9.7; in der CI über das RuntimeWarning-Gate) |
| Neue Tests | `tests/test_event_runtime_priority_memory_797.py`, `tests_ha/test_event_runtime_priority.py` |

ERGEBNIS_KOPF

## 1. Reproduktion auf 7.9.6 und Root Causes

Alle Fälle synchron (kein `await`, kein Loop-Durchlauf zwischen den
Ereignissen), Stub-Home-Assistant mit echtem `SituationRuntime`; Messwerte
aus `scripts/event_runtime_repro_797.py --json` gegen den Tag `v7.9.6`
(vollständig in `docs/perf/event-runtime-repro-7.9.6.json`).

### 1.1 „LOSSLESS“ bei voller Queue nicht geschützt

Kategorie `safety`, 4096 Schalter `off → on`, danach ExpectedEffect für
`light.flur` und `light.flur: off → on`:

```text
queue_depth 4096, dropped_lossless 1, light_has_pending_event False,
effect_event_processed False, false_effect_timeout True, worker_starts 1
```

Das Licht-Ereignis wurde abgewiesen; nach Ablauf der Frist meldete der
EffectMonitor eine fehlende Wirkung, obwohl das Licht an war. Gleiches mit
echten Kategorie-Ereignissen (4096 Lichter unter `light_unoccupied`) und für
eine Monitor-Person (`home → not_home → home` hinter 4096 Kategorie-
Ereignissen: `dropped_lossless 2`, beide Übergänge verloren).

ExpectedEffect zuerst eingereiht, Queue mit 4095 Kategorie-Ereignissen
gefüllt, dann Rauchmelder: `effect_event_processed False, dropped_lossless 1`
– das kritische Ereignis verdrängte den ältesten Lossless-Eintrag, und das
war der ExpectedEffect. Beim 50 000er-Lauf ebenso: ExpectedEffect verloren.

Root Cause: ein Rang `LOSSLESS` für zwei verschiedene Arten von Ereignissen
(direkt beobachtete – Effect, Monitor, Thermal – und bloße Kategorie-
Ereignisse). Ein Lossless-Ereignis durfte nur Coalescible verdrängen, ein
kritisches den *ältesten* Lossless-Eintrag, ohne die Art zu kennen.

### 1.2 Kategorien nicht zielgerichtet

`_HouseWide.active = bool(categories) or …`: jede nichtleere Kategorienliste
machte jede ausgewählte Entität zum Ereignis. Unter `safety` (6000 Schalter
und Zahlen-Sensoren): `queued 4097, dropped_coalescible 1905,
snapshot_builds 4, worker_starts 1`. Unter `expected_effect_missing` ohne
aktiven Effect: 6000 Ereignisse, `queued 4096, dropped_lossless 1904,
worker_starts 1`.

### 1.3 Proaktiv-Kontext ohne Relevanzgrenze

`is_relevant_event()` entschied nur zwischen `LOSSLESS` und dem Fall-through
auf die Hausweit-Regel: 6000 irrelevante Ereignisse →
`queued 6000, dropped_coalescible 1904, worker_starts 1`.

### 1.4 Speicherretention nach vollständigem Drain

6000 eindeutige Entitäten, vollständig abgearbeitet, ohne Unload:

```text
live 0, queue 0, latest 4096, gaps 1904        (zehn Bursts: nach jedem Burst identisch 6000 Referenzen)
5000 eindeutige Zahlen-Sensoren: latest 4096, gaps 904, carry_previous 904
```

`latest` hielt abgeschlossene `_Entry`-Objekte samt Raw Event, alten und
neuen States, Attributen und Context; `gaps`/`carry_previous` wurden nur beim
Unload geleert.

### 1.5 Gap-Buchführung nicht begrenzt

50 000 eindeutige Ereignisse ohne laufenden Worker: `gaps 45 905`,
`latest 4097`, Spitze 54 099 zurückgehaltene Referenzen (Queue 4096). Begrenzt
war nur `gen.live`.

## 2. Ränge (`event_priority.py`)

| Rang | Inhalt | darf verdrängen | Coalescing |
|---|---|---|---|
| `CRITICAL` | Zustandsänderung eines Sicherheitsgeräts: Vereinigung der bestehenden Listen `situation.SAFETY_CLASSES`, `situation_detection.SAFETY_CLASSES`, `event_summary.ALARM_DEVICE_CLASSES` (Rauch, Gas, CO, Feuchtigkeit/Wasser, Safety, Tamper, Problem); keine neue Liste | alle niedrigeren, Protected nur als letzte Grenze | nie |
| `PROTECTED` | direkt beobachtet: Entity eines aktiven ExpectedEffects, Sensoren und Personen aktiver Monitorziele, Personen von Nobody-Home-Zielen, alle Entitäten eines aktiven Thermal-Zyklus (Klima, Temperatur, Außentemperatur, Fenster des Raums) | Routine, Category, Coalescible | nie |
| `ROUTINE` | breite, aber flankenabhängige Verbraucher: Routinenstatistik (Routinen-Erkennung mit `routine_anomaly` bzw. `device_unavailable`), vom Proaktiv-Kontext als relevant gemeldete Ereignisse (inkl. Habit-Trigger) | Category, Coalescible | nie |
| `CATEGORY` | nur wegen einer konfigurierten Agent-Kategorie benötigt (Abschnitt 4) | Coalescible | nie |
| `COALESCIBLE` | reine Zahl → Zahl eines `sensor.*`, nur von der Routinenstatistik gelesen | nichts außer History-only-Einträgen | ja (aufeinanderfolgende Änderungen derselben Entität, solange noch eingereiht) |

Der Rang eines Ereignisses ist der höchste, den ein interessierter Verbraucher
verlangt. Es wird nie ein Ereignis gleichen Rangs verdrängt; innerhalb eines
Rangs fällt das älteste (deterministisch); verarbeitet wird weiterhin in
globaler zeitlicher Reihenfolge (eine Queue, je Rang eine Hilfs-Deque mit
Verweisen).

Verdrängungsreihenfolge für ein eingehendes Ereignis bei voller Queue
(`_make_room`):

1. History-only-Einträge (zusammengeführte Auswertungen, nur für die
   Hausansicht) – kein Auswertungsverlust,
2. Coalescible,
3. Category,
4. Routine,
5. Protected – nur für ein kritisches Ereignis und nur, wenn nichts
   Niedrigeres mehr wartet (gezählt als `dropped_protected`, Fehler-Log).

Ein kritisches Ereignis verdrängt also nie einen ExpectedEffect, solange
gewöhnliche Kategorie-Ereignisse in der Queue stehen. `MAX_PENDING_EVENTS`
bleibt 4096.

`LOSSLESS` gibt es nicht mehr; die Metrik `dropped_lossless` bleibt als Summe
von `dropped_protected + dropped_routine + dropped_category` erhalten (alle
7.9.6-Zusicherungen `dropped_lossless == 0` gelten damit unverändert und
strenger).

Wo bleibt Coalescing? Mit gezielten Kategorien braucht keine Situationsregel
eine reine Zahlenänderung mehr – sie wird ohne Routinenstatistik gar nicht
eingereiht. Mit Routinenstatistik ist sie `COALESCIBLE`: die Statistik
zählt Zeitpunkte; zusammengeführt wird nur, solange die frühere Änderung
derselben Entität noch wartet (also nur unter Last), der Zeitpunkt der
letzten bleibt. 7.9.6 hielt sie unter Routinenstatistik verlustfrei; das ist
die einzige bewusste Rangsenkung (Abschnitt 9).

Nie zusammengeführt: Safety, ExpectedEffect, Monitorziel, Person, Tür/Fenster,
Thermal, Wechsel mit `unknown`/`unavailable`, alles Nichtnumerische.

## 3. Interest-Index (`event_interest.py`)

Im Event-Bus-Callback, synchron: Entity-ID, Domain, alter/neuer State,
`device_class` aus den Event-States, gecachte Verbraucher-Sets (von den
Providern gepusht) und gecachte Optionen (neu berechnet nur bei geänderten
Optionen oder V12-Objekt). Kein Datei-, kein Registry-Zugriff, kein Snapshot,
keine Coroutine, keine Task, keine Suche über ausgewählte Entitäten.

```text
CRITICAL      is_critical_change(...)
PROTECTED     entity_id in specific_entity_ids (Effect, Thermal, Monitor) | Personen/Sensoren vor dem ersten Store-Load
ROUTINE       V12 ohne deklarierte Relevanz | proactive.is_relevant_event(entity_id, device_class)
ROUTINE       Routinenstatistik hausweit und kein reiner Zahlenwechsel
CATEGORY      _Consumers.category_wants(entity_id, old, new)
COALESCIBLE   Routinenstatistik hausweit und reiner Zahlenwechsel eines sensor.*
None          sonst  → filtered_no_interest (+ filtered_by_category, wenn Kategorien konfiguriert sind)
```

## 4. Kategoriespezifische Interest-Matrix

Abgeleitet aus `normalize_state_change()` und `SituationEvaluator.evaluate()`
so, wie `SituationRuntime` sie aufruft (keine zweite Semantik:
`OPEN_CLASSES`, `ACTIVE_STATES` werden aus `situation.py` importiert).

| Kategorie | Benötigt (CATEGORY) | Begründung aus der bestehenden Regel |
|---|---|---|
| `safety` | nichts zusätzlich | einzige kritische Situation ist `safety_alarm` (`SAFETY_ALARM` = Klasse aus `SAFETY_CLASSES` im Alarmzustand); jedes solche Ereignis ist bereits `CRITICAL` |
| `safety_alarm` | nichts zusätzlich | wie oben |
| `device_unavailable` | neuer Zustand `unavailable` | `EventQuality.UNAVAILABLE`; `unknown` ergibt im Evaluator keine Situation |
| `opening_while_away` | Flanken von `OPEN_CLASSES` (Tür, Fenster, Garagentor, Öffnung – jede Domain, also auch Covers); `person.*` | Regel: `OPENED` nachts bei `occupied is False`; `occupied` stammt aus den Personen der Hausansicht *zum Ereigniszeitpunkt* – ohne ihre Änderungen in der Queue sähe eine Öffnung den späteren Personenzustand (Future-Leak, 7.9.5) |
| `window_heating` | Flanken von `OPEN_CLASSES`; `climate.*` | Regel: `OPENED` mit heizendem Klima im selben Bereich; der Heizzustand wird aus der Hausansicht zum Zeitpunkt der Öffnung gelesen (gleiche Begründung); Klimaänderungen lösen die Regel selbst nie aus |
| `light_unoccupied` | `light.*` mit aktivem neuen Zustand (`ACTIVE_STATES`, auch reine Attributänderung eines eingeschalteten Lichts – sie wird als Aktivierung ausgewertet); `person.*`; mit Routinen-Erkennung jede `light.*`-Flanke | Regel: `ACTIVATED` eines `light.*` bei `occupied is False`; die Routinenstatistik des Lichts erklärt seine Situation („Historie“) |
| `long_running_state` | nichts | `SituationRuntime` ruft den Evaluator ohne `long_state_seconds` auf – die Regel kann aus einer Zustandsänderung nicht feuern |
| `routine_anomaly` | nichts ohne Routinen-Erkennung; mit ihr jede Entität (`ROUTINE`) | die Routinenstatistik läuft nur mit Routinen-Erkennung und bewertet jedes ausgewertete Ereignis |
| `expected_effect_missing` | nichts | nur Entitäten aktiver ExpectedEffects (ohnehin `PROTECTED`); der Timeout läuft im EffectMonitor ohne Zustandsänderung |

Zusätzlich mit Routinen-Erkennung (und beliebiger Kategorie): `person.*` als
Kontext jeder Routinenbeobachtung (Anwesenheit und Modus); mit
`device_unavailable` jede Entität (`ROUTINE`), weil die Statistik die Historie
jeder unverfügbar werdenden Entität erklärt.

Kritische Ereignisse werden unabhängig von jeder Kategorie konservativ
zugelassen. Direkte Protected-Interessen überschreiben den Kategorienfilter.
Mehrere Kategorien bilden die Vereinigung.

## 5. Proaktiv-Relevanz

Aktivierter Proaktiv-Kontext ist kein Hausweit-Verbraucher mehr:
`is_relevant_event(entity_id, device_class)` (Detektor-Vorfilter und
Habit-Trigger) entscheidet. `False` und kein anderer Verbraucher →
`classify() is None`, `filtered_no_interest`, keine Queue, kein Snapshot, kein
Worker. Ausnahmen bleiben: Critical, ExpectedEffect, Monitor, Thermal,
relevante Kategorie, Routinenstatistik. Ein V12-Objekt ohne deklarierte
Relevanz bleibt konservativ hausweit (`ROUTINE`).

Folge: `ProactiveEngine.counters.events_seen` zählt nur noch eingereihte
Ereignisse (vorher jedes ausgewählte). Der Detektor verwarf irrelevante
Ereignisse ohnehin als erstes; Erkennungen ändern sich nicht.

## 6. Speicherbereinigung

- **`latest`**: Ein Eintrag wird entfernt, sobald sein Ereignis ausgewertet
  (`_Generation.finish`), verdrängt (`_evict`) oder abgewiesen ist. `latest`
  hält damit nur noch wartende oder gerade ausgewertete Einträge.
- **Hilfs-Deques** (`by_priority`, `view_only`): veraltete Köpfe beim Entnehmen,
  vollständige Bereinigung bei `compact()` (jetzt fest ab 1024 Tombstones statt
  mitwachsend) und nach jedem vollständigen Drain.
- **Vollständiger Drain** (`release_drained`): Sieht der Worker seiner
  Generation `live == 0`, gibt er synchron – ohne `await` dazwischen, also ohne
  dass ein Ereignis dazwischen eintreffen kann – Queue, Deques, `latest`,
  `gaps`, `carry_previous`, `unprocessed_by_entity` und Tombstones frei
  (`cleanup_runs` +1). Gaps und übertragene Vorzustände beschreiben dann nur
  noch Änderungen vor jedem künftigen Ereignis; der nächste Worker baut
  ohnehin einen neuen Snapshot. Ein Worker einer gestoppten oder ersetzten
  Generation gibt nichts frei (`_owns`). Metriken bleiben.
- **Abgewiesene/verdrängte Einträge** lassen Event- und State-Referenzen sofort
  los (`_Entry.release`).

## 7. Harte Grenzen der Buchführung

| Struktur | Grenze |
|---|---|
| Queue (live) | `MAX_PENDING_EVENTS` = 4096 |
| Tombstones in der Queue (ohne Event/State) | `MAX_TOMBSTONES` = 1024 |
| `latest` | wartende + eine Charge in Auswertung ≤ 4096 + `MAX_BATCH_EVENTS` (1024) |
| `gaps` | `MAX_GAPS` = 4096 Entitäten |
| `carry_previous` | `MAX_CARRIED` = 4096 Entitäten |
| Hilfs-Deques | nur Einträge der Queue (bereinigt bei `compact()`/Drain) |
| Summe Referenzen | `MAX_RETAINED_ENTRIES` = 18 432 |

Diagnose: `SituationRuntime.retention()` liefert `retained_entries`,
`retained_index`, `retained_latest`, `retained_gaps`, `retained_carry`,
`retained_tombstones`, `retained_unprocessed`, `history_degraded_active`,
`max_retained_entries`, `retained_limit`. Metriken: `history_degraded`,
`history_degraded_batches`, `max_retained_entries`, `cleanup_runs`.

## 8. Degradierte Hausansicht

Passt ein aufgegebener Wechsel nicht mehr in `MAX_GAPS`, wird er nicht
gespeichert (`history_degraded` +1, Warnung höchstens einmal pro Minute) und
der Bereich `history_degraded_from … history_degraded_until` gemerkt. Für
Ereignisse davor gilt:

- Sie werden in eigenen Chargen ausgewertet (`take(until_seq=…)`,
  `history_degraded_batches`).
- Die Hausansicht enthält nur Entitäten, deren Zustand bekannt ist: mit
  Änderung in Charge, Queue oder Gap; alle anderen fehlen, statt einen
  späteren Zustand zu zeigen.
- Eine Entität, deren früheste bekannte Änderung *nach* dem ersten
  unprotokollierten Wechsel liegt, fehlt bis zu ihrer eigenen Änderung: ihr
  `old_state` kann den aufgegebenen späteren Zustand bereits enthalten
  (Regressionstest `test_degraded_history_never_rewinds_to_a_given_up_later_state`,
  ohne diese Regel rot).
- Ausgewertet wird mit `degraded=True`: der Thermal-Tracker wird übersprungen
  (wie schon bei Registry-losen Chargen), Anwesenheit ist `unknown`
  (`occupied=None`, `operating_mode="unknown"`) statt „niemand zu Hause“ aus
  bloß fehlenden Personen – sonst könnte eine Öffnung fälschlich
  `opening_while_away` melden (Test
  `test_degraded_history_reports_presence_unknown_not_nobody_home`). Das gilt
  nun auch für Chargen ohne Registry-Daten nach Snapshot-Fehlern.
- Critical- und Protected-Ereignisse werden weiter aus ihrem eigenen
  Event-State ausgewertet (EffectMonitor, Monitorziele, Safety-Situation).
- Ereignisse nach dem letzten unprotokollierten Wechsel sehen wieder das
  vollständige Haus.

## 9. Bewusste Verhaltensänderungen und angepasste Tests

Keine Tests entfernt, keine Safety-Assertion gelockert. Angepasst wurden nur
Vorbedingungen, die genau auf den behobenen Fehlern beruhten:

- 7.9.6-Tests (`test_event_runtime_hardening_796.py`), 7.9.5/7.9.4-Tests und
  drei `tests_ha`-Tests nutzten `safety`, `device_unavailable` oder
  `opening_while_away` nur, um *alle* Entitäten zu Verbrauchern zu machen. Sie
  laufen jetzt mit der in 7.9.7 weiterhin hausweiten Konfiguration
  (Routinen-Erkennung + `routine_anomaly`, ggf. zusätzlich `safety`); ihre
  Assertions sind unverändert.
- `EventPriority.LOSSLESS` → `PROTECTED` (spezifische Verbraucher) bzw.
  `ROUTINE` (hausweit) in den Klassifikationstests; zusätzlich
  `dropped_category == 4` bzw. `dropped_protected == 0` geprüft.
- `test_classification_of_house_wide_consumers`: Unter Routinenstatistik ist
  eine reine Zahlenänderung jetzt `COALESCIBLE` statt verlustfrei (Abschnitt 2);
  zusätzlich geprüft: ohne Routinen-Erkennung ist die Kategorie kein
  Verbraucher.
- `test_enabled_proactive_context_is_a_consumer`: eine vom Proaktiv-Kontext
  als irrelevant gemeldete Leistungsänderung ist jetzt `None` (Fehler 3.3).
- 7.9.4-Stubs: Fenster-/Tür-States tragen jetzt wie in Home Assistant ihre
  `device_class` (der Kategorienfilter liest sie aus dem Ereignis; der
  Snapshot liest sie aus demselben Attribut); ein Licht-Ereignis unter
  `opening_while_away` wurde durch ein Fensterereignis ersetzt, ein
  Sensor-Ereignis unter `device_unavailable` wird `unavailable`.

## 10. Ergebnisse

ERGEBNISSE_797

## 11. Verbleibende ehrliche Systemgrenzen

GRENZEN_797

## 12. Umsetzungsmatrix

MATRIX_797
