# HomeIntent 7.9.5 – EventRuntime: jedes Ereignis mit seinem eigenen Zustand

Ausgangsstand 7.9.4 auf `main` (`517d76c`, Tag `v7.9.4`). Arbeitszweig
`fix/7.9.5-event-runtime-correctness`. Reiner Bugfix: keine neue Funktion,
keine Änderung an Sprachverständnis, Bestätigung, Execution Policy,
Berechtigungen, Datenschutz oder Schreibwegen (`service_executor` bleibt der
einzige Schreibweg für Geräte).

Die P0-Verbesserung aus 7.9.4 bleibt vollständig erhalten: `@callback`-
Listener ohne Task je Ereignis, höchstens ein Worker, Snapshot-Aufbau je
Stapel, kein Thermal-`fsync` ohne aktiven Zyklus, Monitor-Store als Single
Flight. Die 7.9.4-Regressionstests (Stub und echtes HA) laufen unverändert
mit; angepasst ist nur der Zugriff auf die interne Warteschlange
(`deque` statt `asyncio.Queue`).

## Befunde aus dem Review von 7.9.4

### 1. Alle Ereignisse eines Stapels sahen denselben späteren Snapshot

`_async_drain()` baute je Stapel (bis 256 Ereignisse) **einen** Snapshot und
wertete jedes Ereignis dagegen aus. Der Snapshot ist das Haus *nach* dem
ganzen Stapel – und sogar nach allen noch wartenden Ereignissen. Innerhalb
eines Stapels führte das zu falschen Auswertungen:

- Eine Entität, die sich zweimal ändert (Tür auf → zu), wurde beide Male im
  Endzustand gesehen. Effekt-Monitor, Situationen und Routinestatistik
  bekamen „zu, zu“ statt „auf, zu“; eine nächtliche Öffnung, die im selben
  Stapel wieder geschlossen wurde, wurde nicht gemeldet.
- Eine Person, die geht und zurückkommt (`home → not_home → home`), erzeugte
  keinen „gegangen“-Übergang: das erste Ereignis sah `home → home`.
- Wertänderungs-Monitore bekamen für jedes Ereignis den letzten Wert
  (`unavailable → 25 → 21` wurde als „21, 21“ gemeldet).
- Der Thermal-Tracker sah zu jedem Zeitpunkt die *späteren* Fenster- und
  Temperaturzustände (Zyklus zu früh als erreicht oder Fenster falsch).
- Ereignisse, die schon hinter dem Stapel warteten, schlugen in ihn durch.
- Attribute (z. B. Helligkeit, Einheit) und daraus abgeleitete Felder kamen
  aus dem Endzustand.

Der direkte Einstieg `async_handle_state_changed()` hatte denselben Fehler:
er las den aktuellen Zustand statt des `new_state` des Ereignisses.

### 2. Ein fehlgeschlagener Snapshot-Aufbau beendete den Worker

`build_entity_snapshots()` lag außerhalb jeder Fehlerbehandlung. Eine
Ausnahme (z. B. Registry während des Starts) beendete den Worker; der
Stapel ging still verloren, und die restliche Warteschlange blieb liegen,
bis zufällig ein weiteres ausgewähltes Ereignis den Worker neu startete.

### 3. Ein Worker, der seinen Abbruch überlebt, leerte die neue Warteschlange

`stop()` bricht den Worker ab, wartet aber nicht auf ihn. Verschluckt eine
gerade laufende Auswertung den Abbruch (z. B. ein abgeschirmter Aufruf),
lief der alte Worker weiter. Nach einem erneuten `async_start()` war
`_stopped` wieder `False`: der alte Worker verarbeitete noch Ereignisse aus
der Zeit vor dem Stopp und griff danach auf die **neue** Warteschlange zu –
zwei Worker nebeneinander, Reihenfolge nicht mehr garantiert.

### 4. Der Auswahlfilter war bei fester Geräteauswahl linear

7.9.4 beschreibt den Filter als O(1); bei fester Auswahl war es
`entity_id in <Liste>` – für **jedes** Ereignis im ganzen Haus ein Durchlauf
über die gespeicherte Liste (bei 3000 ausgewählten Entitäten und einem
Startschwall von Tausenden Ereignissen spürbar im Event-Loop). Bekannte
Grenze in 7.9.4, jetzt behoben.

## Änderung

- **`event_runtime.py`:**
  - Neue interne Klasse `_HouseView`: aus dem Snapshot je Stapel (das Haus
    *jetzt*) wird jede Entität mit wartendem Ereignis auf den `old_state`
    ihres frühesten wartenden Ereignisses zurückgesetzt (Stapel **und**
    dahinter wartende Ereignisse); danach wird je Ereignis dessen
    `new_state` angewandt. Jedes Ereignis sieht so seine Entität exakt mit
    Zustand und Attributen des Ereignisses und das übrige Haus so, wie es in
    diesem Moment war. Entitäten, die erst im Stapel entstehen
    (`old_state` ist `None`), fehlen vor ihrem Ereignis. Weiterhin nur
    Entitäten, die beim Snapshot ausgewählt sind; ein Ereignis einer nicht
    mehr ausgewählten oder entfernten Entität wird wie bisher nicht
    ausgewertet.
  - Der Snapshot wird weiterhin **einmal je Stapel** gebaut; je Ereignis
    kommt nur das Ersetzen eines Eintrags und eine Listenkopie hinzu (wie
    7.9.4 sie mit `tuple(entities)` ohnehin machte).
  - Snapshot-Fehler: protokolliert mit Anzahl der nicht ausgewerteten
    Ereignisse, der Worker läuft mit dem nächsten Stapel weiter.
  - Der Worker gehört genau einer Warteschlange; ist sie nicht mehr die
    aktuelle (Stopp/Neustart), endet er sofort. Die Warteschlange ist eine
    `deque` (gleiche Grenze 4096, gleiche Überlaufwarnung höchstens einmal je
    Minute), damit wartende Ereignisse für das Zurücksetzen lesbar sind.
  - `async_handle_state_changed()` nutzt dieselbe Ansicht.
  - Die Auswertung je Ereignis (`_async_process_state_changed`: Effekt-
    Monitor, Monitore, V12-Kontext, Situationen, Routinen,
    Entscheidungs-Engine, Dedupe, `_signal`) ist unverändert.
- **`hass_entities.py`:**
  - `snapshot_at_state()`: ein Snapshot mit den Feldern eines bestimmten
    `State` (Zustand, Attribute, Name, Einheit, Geräteklasse,
    Zustandsklasse, Fähigkeiten, Zeitstempel); Registry-Daten (Bereich,
    Etage, Aliase) bleiben. `build_entity_snapshots()` nutzt dieselbe
    Feldableitung (`_state_fields()`), das Ergebnis ist unverändert.
  - `SelectedEntityFilter`: dieselbe Regel wie `is_selected_entity()`, bei
    fester Auswahl als Mengenabfrage; die Menge wird neu gebaut, sobald sich
    die gespeicherte Auswahl ändert. Ohne feste Auswahl unverändert eine
    einzelne `async_should_expose`-Abfrage.

Nicht geändert: Thermal-Tracker, Monitor-Store, Proaktiv-Engine,
Entscheidungslogik, Kategorien, Dedupe-Schlüssel und alle Safety-Grenzen.

## Tests

„Vorher rot“ heißt: dieselbe Testdatei gegen den unveränderten Stand 7.9.4
(Worktree von `517d76c`).

| Testdatei | Fälle | vorher rot | nachher |
|---|---:|---:|---|
| `tests/test_event_runtime_correctness_795.py` (Stub) | 15 | 15 | grün |
| `tests_ha/test_event_runtime_correctness.py` (echtes HA 2026.9.2) | 1 | 1 | grün |

Stub-Fälle: jedes Ereignis sieht seinen eigenen Zustand und das Haus zu
seinem Zeitpunkt (4 Ereignisse, ein Stapel); dahinter wartende Ereignisse
schlagen nicht in den Stapel durch (Stapelgröße 2); Person geht und kommt
zurück → zwei Übergänge in Reihenfolge; Wertänderungen mit dem eigenen Wert
(`unavailable` übersprungen); Effekt-Monitor sieht jeden Zwischenzustand;
Thermal-Tracker sieht Fenster und Temperatur zum Ereigniszeitpunkt;
Attribute folgen dem Ereignis; im Stapel neu entstandene Entität fehlt
vorher; direkter Einstieg nutzt den Ereigniszustand; nächtliche Öffnung, die
im selben Stapel wieder schließt, wird gemeldet (Meldung unverändert);
Snapshot-Fehler beendet den Worker nicht und wird mit Anzahl protokolliert;
ein Worker, der seinen Abbruch überlebt, verarbeitet weder alte noch neue
Ereignisse; Mengenfilter entspricht `is_selected_entity()` und folgt
Optionsänderungen; die Runtime ruft für 5000 fremde Ereignisse keine lineare
Prüfung auf; `snapshot_at_state()` behält Registry-Daten.

Echtes Home Assistant: 600 ausgewählte Sensoren je `unavailable` → Wert mit
Attributen, dazwischen eine Tür, die ihren Zustand wechselt – 1206
Ereignisse in einem synchronen Schwall. Jedes ausgewertete Ereignis muss
Zustand, Attribut, Einheit und Türzustand dieses Moments sehen.
7.9.4: 903 Abweichungen, 7.9.5: 0. Weiterhin kein Task je Ereignis.

Laufzeit des 7.9.4-Sturmtests (echtes HA, 6000 Ereignisse, 3000 Sensoren
ausgewählt): 7.9.4 0,44 s, 7.9.5 0,49 s (ganzer Test inklusive 6000
`async_set`).

## Gates

Lokal, Python 3.12 (CI-Hauptjob), zusätzlich 3.13 und 3.13 mit hassil 3.12:

| Gate | Ergebnis |
|---|---|
| Stub-Suite 3.12 / 3.13 / 3.13 + hassil 3.12 | je 9703 passed, 12 skipped, 0 failed (7.9.4: 9688 + 15 neue) |
| `tests_ha` (HA 2026.9.2, Python 3.14) | 19 passed (18 + 1 neuer) |
| Sprachverständnis-Gate (hassil 3.11 und 3.12) | je 463 passed |
| Korpus-Signaturen gegen `corpus-signatures-7.9.4.json` | 4098 Sätze, 0 geändert; neue Baseline `corpus-signatures-7.9.5.json` mit 3 neuen Einträgen (Test-Docstrings, keine Benutzersätze; `corpus-signatures-7.9.5-begruendung.md`), CI-Gate umgestellt |
| Shadow-Vergleich | 2131 EQUIVALENT, 0 SAFETY_DRIFT |
| Entwicklungs-Benchmark 7.7 / 7.8 | `unsafe_execution_count` 0, `SAFETY` 0 |
| Arbiter-Shadow | 2150 gleichwertig, 7 nicht messbar, 4 BEHAVIOR_CHANGE (Musik, wie 7.9.4), 0 SAFETY_DRIFT |
| V9-Latenz 5000 Entitäten | bestanden (Budget p95 100 ms; z. B. `v9_group_reference_shape` p95 6,0 ms) |
| Automationssprache 5000 | p95 12,2 ms (Budget 100 ms) |
| V10 / V11 / V12 / Learning Center | alle Budgets eingehalten (V12 `event_storm_1000` 2,2 ms) |
| Zusammenfassung 5000 Entitäten, 7 Tage | p95 93,5 ms (Budget 500 ms) |
| Pyright voll, CI-Strict-Liste, V11-, V12-, Learning-Center-Profil | je 0 Fehler |
| Pyflakes, `git diff --check` | 0 |
| HA-Smoke-Skripte (`validate_*`), lokal mit HA 2026.9.2 statt Docker-Image | alle 4 OK |

## Live-Testbett

`sim/fresh_ha.sh` (frisches HA 2026.9.2, 136 freigegebene Entitäten), danach
**Neustart** von Home Assistant mit installiertem HomeIntent, dann
`runner.py --strict` über alle Kategorien inklusive Proaktiv: **230/230**.
Der Gesamtlauf wurde lokal nach 30 Minuten von der Laufzeitgrenze der
Umgebung beendet (223 bestanden, 0 fehlgeschlagen); die 7 restlichen
Szenarien (`n793-b1-awning-rain` … `n793-b7-monitor-edit`) liefen danach
einzeln auf demselben System, alle bestanden.

- Nach einem weiteren Neustart: HTTP nach 2,0 s erreichbar, keine Meldung
  „Something is blocking Home Assistant“.
- `check_log.py`: kein HomeIntent-Traceback. Der einzige Treffer ist wie in
  7.9.4 der `libturbojpeg`-Traceback der HA-Kamera-Komponente; er zählt
  lokal nur mit, weil der Pfad der lokalen venv „homeintent“ enthält.

## Bekannte Grenzen

- Läuft die Warteschlange über (mehr als 4096 ausgewählte Zustandsänderungen,
  ohne dass der Loop dazwischen frei wird), werden weitere Ereignisse wie in
  7.9.4 verworfen und gemeldet. Für eine Entität mit verworfenem Ereignis
  kann die Ansicht früherer Ereignisse dann deren späteren Zustand zeigen
  (Verhalten wie 7.9.4). Im Live-Test und in `tests_ha` nie erreicht.
- Der serielle Worker bleibt: Ereignisse werden nacheinander ausgewertet;
  wartet eine Auswertung, verschieben sich die folgenden (wie 7.9.4).
- Registry-Daten (Bereich, Etage, Aliase) stammen aus dem Snapshot des
  Stapels, nicht aus dem Ereigniszeitpunkt; sie ändern sich nicht durch
  Zustandsänderungen.
