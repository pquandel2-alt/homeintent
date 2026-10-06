# HomeIntent 7.9.2 – Befunde aus dem Nachtest 7.9.1 und neue Fähigkeiten

Auftrag: `sim/PROMPT_7.9.2.md`, Grundlage `docs/nachtest-7.9.1.md` (beide im
Zweig `claude/sleepy-meitner-xd7oux`). Ausgangsstand 7.9.1
(`claude/homeintent-7.9.1` = `3988ac8`; `main` stand noch auf 7.9.0).
Arbeitszweig `claude/homeintent-7.9.2`.

Entscheidungen des Projekteigentümers: **T1** – ein Gerät, das in die
verlangte Richtung fährt, ist ein Erfolg mit Ton; **T2 Variante C** – nur
Wasser-/Bewässerungsventile öffnen sich automatisch. Der Test im echten Haus
ist nicht Teil dieses Auftrags.

Alles bleibt lokal und deterministisch, ohne Satzlisten. Die
Sicherheitsgrenze ist unverändert: Grounding → Validator → gesprochene
Vorschau → ausdrückliches „Ja“ → Schreiben; `service_executor` bleibt der
einzige Schreibweg für Geräte, die Negations-Sperre in `conversation.py` ist
unberührt, Zugangsregel und Eigentümerrechte aus 7.9.1 gelten weiter.

„Vorher rot“ heißt: dieselbe Testdatei mit denselben Test-Helfern
(`tests/_ha_sim.py`, `tests/_testhaus.py`, Fixture) gegen den unveränderten
Stand 7.9.1 (Worktree von `3988ac8`) ausgeführt.

| Testdatei | Fälle | vorher rot | nachher |
|---|---:|---:|---|
| `test_effect_wait_792.py` (A1) | 72 | 58 | grün |
| `test_irrigation_792.py` (A2) | 166 | 126 | grün |
| `test_household_voice_792.py` (A3) | 40 | 30 | grün |
| `test_floor_coverage_792.py` (A4) | 29 | 25 | grün |
| `test_exact_registry_name_792.py` (A5) | 20 | 18 | grün |
| `test_gaps_792.py` (A6) | 93 | 87 | grün |
| `test_device_health_792.py` (B3) | 41 | 41 | grün |
| `test_energy_792.py` (B5) | 18 | 18 | grün |
| `test_event_summary_792.py` (B1) | 15 | 15 | grün |
| `test_habit_suggestions_792.py` (B2) | 15 | 15 | grün |
| `test_vacation_792.py` (B4) | 31 | 31 | grün |
| **Summe** | **540** | **464** | |

Die grünen Fälle „vorher“ sind Gegenproben, die schon 7.9.1 richtig
behandelte (z. B. Gas- und Hauptventil gesperrt, Bewertungsmatrix ohne
Konversation, ohne Option bleibt alles wie in 7.9.1). Nach der Messung kamen
17 Fälle hinzu (Abschnitt „Nachträge aus Live-Lauf und Prüfsätzen“); der
Gegenrichtungs-Fall war gegen den Stand vor seiner Korrektur rot, die
übrigen fehlen in 7.9.1 schon mangels Modul.

## Teil A – Befunde

### A1 (T1): „fährt“ ist ein Erfolg, auf Rückmeldung wird kurz gewartet

- **Ursache:** `service_executor` prüfte den Zielzustand sofort nach dem
  Aufruf. Echte Geräte melden 0,1–2 s später; Rollläden melden während der
  Fahrt `opening`/`closing`. Folge: selten ein Ton, „Rollladen runter“ immer
  gesprochen.
- **Änderung:**
  - Neues Modul `effect_wait.py`. `expectations_for()` leitet aus jedem
    geschriebenen Plan die erwartete Wirkung je Ziel ab; `judge()` bewertet
    einen Zustand als `REACHED`, `MOVING` (verlangte Richtung bzw. Bewegung
    zur Zielposition), `PENDING`, `CONTRARY` (Gegenrichtung oder Gegenteil)
    oder `UNAVAILABLE` (`unavailable`/`unknown`). Erfolg sind `REACHED` und
    `MOVING`. Klima: der neue Sollwert im Attribut (die Raumtemperatur muss
    nicht erreicht sein); Mediaplayer: Zustand bzw. Lautstärke.
  - `service_executor` bewertet direkt nach dem Schreiben. Ist die Wirkung
    schon da, meldet er `EXECUTED`; sonst legt er die Wirkung als
    `PendingEffect` in den Turn (`turn_outcome.defer_outcome`).
  - Am Turn-Ende entscheidet `async_settle_turn()` einmal für alle offenen
    Ziele: **ein** `state_changed`-Listener
    (`async_track_state_change_event`) über alle Ziele, gewartet wird auf ein
    `asyncio.Event` mit Zeitlimit – ereignisgesteuert, kein Polling, alle
    Ziele parallel. Erst danach wird zwischen Ton und Sprache entschieden.
    Wartezeit: Option `effect_wait_seconds` (Optionsdialog, 0–5 s,
    Standard 2 s). Ohne offene Wirkung wird nicht gewartet, auch im Stil
    `spoken`.
  - Nicht bestätigte Ziele werden `UNCONFIRMED` und ehrlich angesagt:
    „X hat sich noch nicht zurückgemeldet.“, „X fährt in die Gegenrichtung.“,
    „X ist nicht erreichbar.“ Teilerfolg bleibt Teilerfolg (ein Ziel
    bestätigt, ein anderes nicht → gesprochen).
  - Eine beobachtete Gegenrichtung bleibt der Befund, auch wenn das Gerät vor
    dem Ende der Wartezeit schon am falschen Ende steht (Befund aus dem
    Live-Lauf, siehe Nachträge). Nach einer Gegenrichtung wird bis zum
    Zeitlimit auf eine Korrektur gewartet; erreicht das Gerät doch das Ziel,
    ist es ein Erfolg.
  - Statistik: p50/p95 der zusätzlichen Wartezeit je wartendem Turn in den
    Diagnosedaten (`effect_wait`); der Live-Runner gibt sie aus. Die
    Wartezeit zählt nicht zum Sprachverständnis-Budget.
- **Testbett:** `haus_sim.configure` (je Entity oder Domain):
  `report_delay` (s), `reverse` (Cover fährt falsch herum), `unavailable`;
  `haus_sim.reset` hebt alles auf.
- **Test:** `test_effect_wait_792.py` – Matrix aus Gerätearten (Licht,
  Schalter, Cover auf/zu/Position, Ventil, Klima, Mediaplayer, Schloss) ×
  Verzögerung × Bericht (Ziel, Bewegung, Gegenrichtung, unerreichbar, zu
  spät); Wartezeit begrenzt, auch im Stil `spoken`; ohne offene Wirkung
  keine Wartezeit; Wartezeit 0 entscheidet sofort; mehrere Ziele parallel
  (Gesamtzeit ≈ eine Wartezeit); Teilerfolg; Listener wird entfernt;
  Optionsbereich; Statistik.

### A2 (T2, Variante C): Bewässerungsventile öffnen sich automatisch

- **Ursache:** `access_openings()` zählte jedes Ventil zu den Zugängen; die
  Ontologie kannte keine Bewässerung, „öffne die Bewässerung“ fand darum
  „kein eindeutig passendes Gerät“; „für 20 Minuten“ und „bewässere den
  Garten“ wurden nicht verstanden.
- **Änderung:**
  - `nlu/device_ontology.py`: Gattungen `irrigation`
    (Bewässerung/Beregnung/Rasen/Beet/Tropf/Garten …) und `main_valve`
    (Hauptwasser/Zuleitung/Haupthahn), beide nur über den Namen erkannt.
  - `nlu/automation_access.py`: `irrigation_valve()` ist die eine Regel –
    `device_class: water` **und** Bewässerungs-Hinweis, oder Gattung
    `irrigation`; nie bei `device_class: gas`, Haupt-/Zuleitungsbezug oder
    unbekanntem Ventil ohne Klasse und Hinweis. `access_kind()` nimmt nur
    diese Ventile aus; Validator, Vorschau und Schreibweg teilen die
    Funktion. Die Ablehnung nennt „Tore, Türen, Schlösser, Gas- und
    Hauptventile“.
  - `nlu/action_duration.py` (Konstruktionen über Wörter, keine Satzmuster):
    „für N Minuten“, „N Minuten lang“, „N Minuten“ am Satzende (nicht nach
    „in/nach/alle/seit …“); Bewässerungsverben (bewässern, beregnen,
    sprengen, gießen) werden zu „öffne die Bewässerung [im Ort]“.
    `inverse_of()` kennt das Gegenstück (öffnen → schließen, ein → aus …).
  - Generator: Aktion mit Dauer → Sequenz Öffnen, `delay`, Schließen in
    derselben Automation. Validator `IRRIGATION_WITHOUT_END`: eine
    automatisch öffnende Bewässerung ohne Ende wird nie geschrieben (auch
    im Schreibweg hart geprüft). Ohne Dauer fragt HomeIntent „Wie lange soll
    „Bewässerung Garten“ jeweils laufen?“ (Rückfrage-Dialog, Teil
    `DURATION`).
  - Vorschau: „… Bewässerung Garten öffnen und nach 20 Minuten wieder
    schließen. Achtung: „Bewässerung Garten“ öffnet sich dabei automatisch
    und schließt nach 20 Minuten wieder.“
  - Mehrdeutigkeit (zwei Bewässerungen) → Rückfrage.
- **Test:** `test_irrigation_792.py` – Ventilklassen (water/gas/keine) ×
  Namen (Bewässerung, Garten, Rasen, Tropf, Hauptwasser, Zuleitung, Gas,
  neutral) × Auslöser (Uhrzeit, Sonnenaufgang, alle weg, Ankunft) × Formen
  (öffne, schalte ein, bewässere, für/lang/Satzende). Abwesenheit bleibt für
  Bewässerung erlaubt; Gas und Hauptventil immer gesperrt; HA-Nachbildung
  (`_ha_sim`) prüft Öffnen → Verzögerung → Schließen.

### A3 (T3): Sprachgeräte ohne angemeldeten Benutzer

- **Ziel:** Ein Wand-Satellit ohne Benutzer konnte nichts verwalten.
- **Änderung:**
  - Option „Sprachgeräte im Haus sprechen für den Haushalt“
    (`household_voice_devices`, Standard aus).
  - `automation_ownership.py`: Eigentümer `household`;
    `may_manage(..., household_voice=)` bleibt die eine Regel. Mit Option
    darf eine Sprachquelle ohne Benutzer, deren Satellit bzw. Gerät zum Haus
    gehört (`household_voice()`), gemeinsame Überwachungen verwalten;
    anlegen nur bei `allow_non_admin_automations`. Persönliche bleiben
    gesperrt. Text-Chat ohne Gerät zählt nie als Haushalt.
  - Markieren: „Mach die Garagen-Meldung für alle / gemeinsam“ (nur
    Eigentümer oder Admin, `may_share`), beim Anlegen „… für uns alle“.
    Erkannt über Wortfolgen (`shared_request`), der Marker wird aus dem Satz
    entfernt, bevor er verstanden wird. Die Liste zeigt „(gemeinsam)“.
  - Empfänger: Gemeinsame Überwachungen benachrichtigen den bestätigten
    Haushalt (`notify`-Ziele werden beim Teilen umgeschrieben); ohne
    bestätigten Haushalt die ehrliche Antwort wie bei „uns“.
  - Rückmeldungen beim Pausieren und Stoppen in Kurzform (A6).
- **Test:** `test_household_voice_792.py` – Option an/aus × Eigentümer
  (Haushalt, Admin, ohne) × Verwaltungsformen; Teilen durch Eigentümer, Admin,
  Fremde; Anlegen „für uns alle“; Empfänger.
- **Testbett:** neuer Dienst `haus_sim.voice` – ein Turn am Satelliten ohne
  angemeldeten Benutzer (wie ein echter Satellit); Runner-Schritt
  `household`.

### A4 (T4): Eine Etage mit nur einem Melder wird ehrlich benannt

- **Änderung:** `automation_grounding._unwatched_rooms_note()` leitet aus der
  Registry ab, welche Räume der Etage keinen passenden Melder haben; die
  Vorschau sagt es: „Im Obergeschoss gibt es nur im Schlafzimmer einen
  Melder; Badezimmer, Kinderzimmer … kann ich nicht beobachten.“
- **Test:** `test_floor_coverage_792.py` – Etagen („im Obergeschoss“,
  „oben“, „im Erdgeschoss“, „unten“) × Formen × Dauern; ein einzelner Raum
  bekommt keinen Hinweis.

### A5 (T5): Ein exakter Registry-Name gewinnt vor der Rückfrage

- **Änderung:** `nlu/entity_resolution.registry_name_hits()` /
  `exact_registry_name()`: Enthält genau ein Registry-Name das gesprochene
  Nomen als ganzes Wort und passt die Messgröße, gewinnt er
  („Stromverbrauch“ → „Stromverbrauch Haus“). Mehrere solche Namen →
  weiterhin Rückfrage. Dieselbe Regel in Automationen (Grounding) und
  Abfragen (`semantic_compiler`). Befehle geprüft: Die Zielauflösung wählte
  schon in 7.9.1 den eindeutigen schaltbaren Namen („Schalte die
  Kaffeemaschine ein“ trotz „Leistung Kaffeemaschine“, „Mach das Radio aus“
  → Küchenradio); dort war keine Änderung nötig.
- **Test:** `test_exact_registry_name_792.py` – Automationen und Abfragen;
  zwei passende Namen → Rückfrage; ein Name mit Raum gewinnt nicht; die
  Regelfunktion direkt.

### A6 (T6): verbleibende Lücken

| Formulierung | jetzt |
|---|---|
| „Wenn die Sonne scheint, fahre die Markise aus.“ | ehrlich auf den Helligkeitssensor abgebildet, Rückfrage „Ab welcher Helligkeit scheint für dich die Sonne? „Helligkeit außen“ misst gerade 5400 lx.“ – nie ein geratener Wert |
| „Wenn es draußen heller als 30000 Lux ist, öffne die Markise.“ | Lux-Grenzwert verstanden |
| „Bei Wind über 40 km/h fahr die Markise ein.“ | km/h-Grenzwert am Windsensor; ohne Windsensor ehrliche Antwort |
| „Erinnere mich jede Minute, bis die Markise eingefahren ist.“ | dieselbe Frage „Nur jetzt oder jedes Mal …?“ wie bei der Haustür |
| „Sag mir Bescheid, wenn die Außentemperatur schnell fällt.“ | Rückfrage „Um wie viel und in welchem Zeitraum?“ (Teil `RATE`) |
| „Wenn ich gehe und noch Licht an ist, sag mir Bescheid.“ | Nachricht nennt die Räume, zur Laufzeit per Template aus Entity-IDs |
| „… wenn irgendeine Batterie unter 20 Prozent fällt.“ | alle Batterie-Sensoren, Vorschau „eine der 4 Batterien“, Nachricht „Batterie Fenstersensor Küche: 15 %“ |
| Pausieren/Stoppen | Kurzform wie in der Liste |

- **Test:** `test_gaps_792.py` (kombinatorisch, mit HA-Nachbildung der
  Templates).

## Teil B – Neue Fähigkeiten

Nur lesende Antworten kommen ohne „Ja“; alles, was schreibt oder dauerhaft
anlegt, geht über Vorschau und „Ja“. Fehlen Daten, antwortet HomeIntent
ehrlich.

### B1 – „Was war los, während ich weg war?“

- `event_summary.py`: Zeitraum aus „während ich weg war“ (letzte Abwesenheit
  aus dem Verlauf der eigenen `person.*`), „heute“, „seit heute Morgen“,
  „letzte Nacht“, „seit 14 Uhr“, „gestern“. Ereignisse nach Wichtigkeit:
  Melder-Alarme, ausgelöste Überwachungen/Automationen, Türen/Tore (Fenster
  und Bewegung nur bei Abwesenheit), Geräte fertig, HomeIntent-Ausführungen
  (Execution-Trace), Kommen und Gehen. Höchstens 5 gesprochen, der Rest auf
  „Was noch?“.
- Quellen: Recorder über den einen Adapter in `history_query.py`
  (`async_read_state_rows`), Trace; keine eigene Datenhaltung. Ohne Recorder
  ehrliche Antwort.
- Datenschutz: Nicht-Admins hören andere Personen nur zusammengefasst
  („jemand ist um 15:02 heimgekommen“).
- **Test:** `test_event_summary_792.py` mit synthetischen Verläufen.
- **Nicht umgesetzt:** der optionale Push beim Heimkommen (siehe Grenzen).

### B2 – Vorschläge aus Gewohnheiten

- **Bestand geprüft:** `habit_discovery.py` und die V11/V12-Laufzeit
  erkennen Routinen aus Goal-Läufen (Schwelle 10 Vorkommen) und schlagen
  daraus Routinen vor. Darauf aufbauend erkennt `habit_suggestions.py`
  zeitgebundene eigene Handlungen aus dem Execution-Trace: gleiche Wirkung,
  Uhrzeit ±45 Minuten, an mindestens `MIN_DAYS = 4` von `WINDOW_DAYS = 7`
  Tagen, Werktag/täglich unterschieden – rein zählend, deterministisch.
  Die V11-Antwort auf „Welche Gewohnheiten …?“ bleibt erhalten, wenn das
  Lernmodul Routinen kennt.
- Vorschlag höchstens einmal je Muster, nie während einer anderen Rückfrage,
  nur dem Benutzer, der gehandelt hat, auf Wunsch per Push; „Ja“ führt zur
  normalen Automationsvorschau mit erneutem „Ja“. Zugänge, Schlösser,
  Alarmanlagen und kritische Geräte werden nie vorgeschlagen.
- Abfragen: „Welche Gewohnheiten hast du erkannt?“, „Hast du Vorschläge für
  Automationen?“, „Schlag mir nichts mehr vor“, „Schick mir Vorschläge per
  Push“.
- **Test:** `test_habit_suggestions_792.py`.

### B3 – Batterien und Ausfälle

- `device_health.py`: „Welche Batterien sind schwach?“ (Standard unter 20 %,
  gesprochene Grenze gilt), „Welche Geräte sind nicht erreichbar?“ (nur
  freigegebene Sensoren und Aktoren), Bericht „Sag mir jeden Sonntag um
  10 Uhr, welche Batterien unter 30 % sind“ (ohne Uhrzeit Rückfrage;
  Nachricht zur Laufzeit per Template).
- „Melde dich, wenn ein Gerät nicht mehr erreichbar ist / der
  Bewegungsmelder im Flur ausfällt“: Auslöser `unavailable` mit `for`
  (Standard 10 Minuten, in der Vorschau genannt), Vorschau mit Anzahl,
  Nachricht nennt das Gerät. Nach einem Neustart zählt die Mindestdauer ab
  dem Neustart – keine Flut (Test).
- **Test:** `test_device_health_792.py`.

### B4 – Urlaubsmodus

- `vacation.py`: „Ich bin bis Sonntag weg“, „Wir fahren bis zum 20. in den
  Urlaub“, „Urlaubsmodus bis Freitag“; ohne Ende „Bis wann seid ihr weg?“.
  Vorschau zählt einzeln auf: 1. jede Tür-/Fensteröffnung und Bewegung
  sofort als Push an den Haushalt (vorhandener Überwachungstyp, bis zum
  Ende); 2. optional Anwesenheitssimulation nur mit Lichtern zu den
  gewohnten Zeiten (B2-Daten) mit deterministischer Streuung aus Datum und
  Entity-ID – ohne genug Verlauf ehrlich, mit Angebot fester Zeiten; 3. ein
  eindeutiger Urlaubs-Helfer wird mitgeschaltet. Heizung, Tore, Türen und
  Schlösser bleiben unberührt (`validate_plan`).
- Ende: „Urlaub vorbei“, „Wir sind zurück“ (mit „Ja“) oder automatisch zum
  Enddatum; alles Angelegte wird vollständig zurückgenommen (Test).
  „Was macht der Urlaubsmodus gerade?“ antwortet mit dem Stand.
- „Schalte den Urlaubsmodus ein“ ohne Enddatum und ohne Reisewort bleibt der
  Gerätebefehl für den Helfer (wie bisher, Entwicklungs-Benchmark).
- **Test:** `test_vacation_792.py`.

### B5 – Verbrauch

- `energy_query.py`: Energie in einem Zeitraum (heute, gestern, diese Woche,
  diesen Monat, letzte Nacht) aus Zählern (Differenz, Zählerreset
  berücksichtigt) oder – nur mit Leistungssensor – als Integral über den
  Verlauf, dann „geschätzt aus der Leistung“. „Was hat heute am meisten
  verbraucht?“ nennt die drei größten in kWh. „… gerade?“ bleibt Leistung in
  W. Kosten nur mit Strompreis: Option `energy_price` (€/kWh) oder ein
  eindeutiger fester Netzpreis aus dem HA-Energie-Dashboard; mehrere Tarife
  werden nicht geraten. Ohne Recorder ehrliche Antwort.
- **Test:** `test_energy_792.py` mit synthetischen Verläufen.

## Nachträge aus Live-Lauf und Prüfsätzen

- **Gegenrichtung im Live-Testbett:** Ein Rollladen, der aus halber Höhe
  falsch herum fährt, erreicht das falsche Ende innerhalb der 2 s; der
  letzte Zustand „open“ las sich als „noch nicht zurückgemeldet“. Jetzt
  bleibt die beobachtete Gegenrichtung der Befund
  (`test_a_wrong_way_movement_that_already_stopped_stays_contrary`, vorher
  rot).
- **„Welche Gewohnheiten hast du erkannt?“** ohne Lernmodul und ohne
  Gewohnheit: vorher „… Eigenschaft oder das Ziel nicht gefunden“, jetzt „Ich
  habe noch keine Gewohnheit erkannt. Dafür brauche ich dieselbe Handlung um
  eine ähnliche Uhrzeit an mindestens 4 von 7 Tagen.“ (Test, vorher rot).
- **Entwicklungs-Benchmark 7.7:** „Schalte den Urlaubsmodus ein“ wurde
  zwischenzeitlich zur Urlaubs-Rückfrage (461 → 458). Behoben (s. B4), Test
  `test_the_helper_command_stays_a_device_command` (4 Formen).
- **Strompreis** aus dem Energie-Dashboard und als Option im Optionsdialog
  (Test `test_energy_dashboard_price`, 6 Fälle).
- **Blockierender Dateizugriff (check_log.py, 2 Befunde im ersten
  Live-Lauf):** Gewohnheits- und Urlaubsspeicher öffneten ihre JSON-Datei in
  der Ereignisschleife. Jetzt lädt und schreibt nur der Executor; im Turn
  wird im Speicher gelesen und geändert (`HabitStore.load/save`, Test
  `test_store_touches_the_disk_only_in_load_and_save`). Das Angebot nach
  einem Befehl ist dafür asynchron (`async_offer_after_turn`).
- **Live-Szenario `stat-mean`:** „Was war gestern der höchste Wert vom
  Stromverbrauch Haus?“ ging an die Verbrauchsfrage (B5) statt an die
  Verlaufsstatistik. Statistikwörter (Wert, Mittel, Durchschnitt, höchste,
  niedrigste, Minimum, Maximum, verändert) schließen die Verbrauchsfrage
  aus (Test `test_statistics_questions_stay_history_statistics`, 4 Fälle).

## Geänderte Test-Erwartungen

| Test | vorher | jetzt | Begründung |
|---|---|---|---|
| `tests/test_access_policy_791.py` | „nie automatisch öffnen“ geprüft an `valve.bewaesserung` | geprüft an `valve.hauptwasserventil` | T2 Variante C: Bewässerungsventile dürfen sich automatisch öffnen (mit Pflicht-Ende). Die Strenge des Tests ist unverändert; geprüft wird das Ventil, das nie automatisch öffnen darf. Kommentar im Test. |
| Live `n791-b-partial` | „Fahre den Küchenrollladen runter“ → gesprochen (Teilerfolg) | ein echter Teilerfolg: Stehlampe nicht erreichbar, Flurlicht an → gesprochen | T1: Ein fahrender Rollladen ist ein Erfolg mit Ton (jetzt `n792-a1-cover-moving`). Der Teilerfolg-Fall bleibt erhalten. Kommentar im Szenario. |
| `tests/_ha_sim.py`, `tests/data/testhaus.json` | – | Nachbildung um `numeric_state`, `to`-Listen und Templates (`is_state`, `states`, `trigger.to_state`, `float`) erweitert; beide Ventile `device_class: water` | Werkzeug für die neuen Automationen; Fixture entspricht dem Live-Testhaus. Keine Erwartung abgeschwächt. |
| Korpus-Signaturen | Baseline 7.9.1 | Baseline 7.9.2 | eine IR-Änderung, Engine unverändert: `docs/perf/corpus-signatures-7.9.2-begruendung.md` |

## Gates

Alle Gates aus `.github/workflows/ci.yml`, lokal auf dem Endstand:

| Gate | Ergebnis |
|---|---|
| `pytest -q` (Stub-Suite) | 8492 passed, 12 skipped, 0 failed |
| `pytest -q tests_ha` (echtes HA 2026.9.2) | 16 passed |
| Sprachverständnis-Gate (`run_language_eval.sh`) | 463 passed |
| Korpus-Signaturen (`corpus_shadow.py --check …-7.9.2.json`) | 3827 Sätze, 0 geänderte Signaturen; gegenüber 7.9.1: 1 begründete IR-Änderung, 0 Engine-Änderungen |
| Shadow-Vergleich (`--candidate identity --check`) | 2108 EQUIVALENT, 0 SAFETY_DRIFT |
| Arbiter gegen Kaskade | 2131 EQUIVALENT, 7 NOT_MEASURABLE (wie 7.9.1), 0 SAFETY_DRIFT |
| Entwicklungs-Benchmark 7.7 | 461/503 (wie 7.9.1), unsafe_execution_count 0 |
| Entwicklungs-Benchmark 7.8 | 102/107 (wie 7.9.1), unsafe_execution_count 0 |
| V9-Latenz (5000 Entitäten, p95) | Licht an 4,4 ms, Bereichsquantor 20,1 ms (Budget 100 ms) |
| Automationssprache (5000 Entitäten, p95) | 20,3 ms (Haus 8,1 ms; Budget 100 ms) |
| V10 Goal/Planung (p95) | Zehn-Schritt-Plan 8,4 ms (Budget 100 ms) |
| V11 Lernen (p95) | ≤ 0,03 ms |
| V12 Kontext (p95) | Situationsbewertung 13,3 ms (Budget 100 ms) |
| Learning Center (p95) | Übersicht 6,6 ms, Evidenz 147 ms (bestanden) |
| `regex_inventory.py --write` | SEMANTIC_SENTENCE_PATTERN 171 (7.9.1: 173) |
| Pyright voll / Strict-Scope / V11 / V12 / Learning Center | je 0 Fehler; neue Module `effect_wait`, `device_health`, `energy_query`, `event_summary`, `habit_suggestions`, `vacation`, `controllers/insights`, `nlu/action_duration` im Strict-Scope |
| Pyflakes | 0 |

Die zusätzliche Wartezeit aus A1 zählt nicht zu diesen Budgets; sie ist
unten getrennt gemessen.

## Live-Testbett

LIVE_PLACEHOLDER

## Bekannte Grenzen

- **B1 Push beim Heimkommen** („Schick mir beim Nachhausekommen eine
  Zusammenfassung“) ist nicht umgesetzt: Die Zusammenfassung entsteht zur
  Laufzeit aus Recorder und Trace; eine Automation müsste dafür einen neuen
  HomeIntent-Dienst aufrufen, der Benachrichtigungen schreibt. Das wäre ein
  zweiter Schreibweg neben `service_executor`/`agent_delivery` und braucht
  eine eigene Sicherheitsprüfung. Heute antwortet HomeIntent auf die Frage.
- **A1:** Die Wartezeit gilt je Turn, nicht je Gerät; ein Gerät, das sich
  erst nach mehr als `effect_wait_seconds` meldet, wird ehrlich als „noch
  nicht zurückgemeldet“ angesagt. Geräte ohne Zustandsrückmeldung (z. B.
  Szenen, Skripte, Tasten) werden nicht abgewartet.
- **A2:** Ein Ventil ohne Klasse und ohne Bewässerungs-Hinweis im Namen bleibt
  vorsichtshalber ein Zugang; der Nutzer kann es umbenennen oder die Klasse
  `water` setzen.
- **A3:** Ob eine Sprachquelle „zum Haus gehört“, entscheidet die
  Device-Registry bzw. ein vorhandener Satellit; eine Unterscheidung
  einzelner Satelliten (z. B. nur Flur) gibt es nicht.
- **B2:** Gewohnheiten entstehen nur aus Handlungen über HomeIntent
  (Execution-Trace), nicht aus Schaltern an der Wand oder aus der HA-App.
- **B4:** Die Anwesenheitssimulation braucht mindestens 4 Tage Verlauf je
  Licht; sonst bietet HomeIntent feste Zeiten an.
- **B5:** Langzeitstatistiken (`recorder.statistics`) werden nicht gelesen;
  die Berechnung nutzt den Zustandsverlauf. Bei kurzem Recorder-Aufbewahrungs-
  zeitraum (`purge_keep_days`) sind „diesen Monat“ und „diese Woche“ ehrlich
  unvollständig („keine Verlaufsdaten“).
