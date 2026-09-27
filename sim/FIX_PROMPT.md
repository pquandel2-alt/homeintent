# Auftrag: Alle Befunde aus dem HomeIntent-Live-Test beheben

Du arbeitest im Repository `pquandel2-alt/homeintent` (HomeIntent 7.1.2, deutsche
lokale Sprachsteuerung für Home Assistant, Integration unter
`custom_components/homeintent/`). In einer separaten Test-Session wurde
HomeIntent gegen ein **echtes Home Assistant 2026.9.2** (Python 3.14, hassil
3.12.0) in einem simulierten Einfamilienhaus getestet. Dabei wurden 28 Befunde
gefunden. **Deine Aufgabe: alle Befunde beheben, mit Tests absichern und
nachweisen, dass sie im echten Home Assistant behoben sind.**

## Ausgangslage

- Der vollständige Testbericht und das Testbett liegen auf dem Branch
  `claude/sleepy-meitner-xd7oux`:
  - `docs/testbericht-live-simulation-7.1.2.md` – Bericht mit allen Befunden (F1–F28)
  - `sim/` – Testbett: eigene HA-Integration `haus_sim` (simuliertes Haus),
    `bootstrap.py`, `runner.py` (126 Szenarien), `say.py`, `dump.py`, `lc_check.py`
  - `sim/results/final.json` – Rohtranskripte des Ausgangsstands (87/126 bestanden)
- Lies zuerst den Bericht und `sim/README.md` vollständig. Arbeite auf einem
  neuen Branch, der von `claude/sleepy-meitner-xd7oux` abzweigt (damit `sim/`
  und der Bericht vorhanden sind).
- Die Stub-Testsuite (`tests/`, `python -m pytest -q`) ist aktuell grün mit
  hassil 3.11.0 – das täuscht: Die meisten Befunde entstehen genau dort, wo der
  Stub (`tests/_ha_stub.py`) vom echten HA abweicht (hassil-Version,
  Thread-Modell, Antwortformate, Dienstschemata).

## Umgebung einrichten

```bash
# Stub-Suite (wie CI)
uv venv --python 3.12 ../devvenv && uv pip install --python ../devvenv/bin/python -r requirements-dev.txt
# Stub-Suite zusätzlich mit der hassil-Version, die HA 2026.9.2 mitbringt
uv venv --python 3.12 ../devvenv312h && grep -v hassil requirements-dev.txt > /tmp/req.txt \
  && uv pip install --python ../devvenv312h/bin/python -r /tmp/req.txt "hassil==3.12.0"
# Echtes Home Assistant (Python >= 3.14.2; `uv python install 3.14` liefert eine passende Version)
uv venv --python 3.14 ../havenv && uv pip install --python ../havenv/bin/python -r requirements-ha-test.txt
# Testhaus starten
cd sim && HASS=../../havenv/bin/hass ./run_ha.sh && ../../havenv/bin/python bootstrap.py
```

(Docker-Images sind in manchen Umgebungen gesperrt; HA aus PyPI genügt. Pfade
an deine Umgebung anpassen. `run_ha.sh` startet HA neu – danach wird geänderter
HomeIntent-Code geladen, weil `sim/config/custom_components/homeintent` ein
Symlink auf das Repo ist.)

## Befunde, die zu beheben sind

Die Details, Originalsätze und Beobachtungen stehen im Bericht unter der
jeweiligen Nummer. Für jede Nummer: Ursache bestätigen, beheben, Regressionstest
schreiben.

### P0 – Kernfunktionen defekt

- **F1 – hassil 3.12 zerstört „in N Minuten …“** (`relative_time_command_parser.py`,
  Grammatik `intents/de/relative_time/`, ggf. auch `scheduled_time_command_parser.py`).
  `decompose("in 30 Minuten erinnere mich an die Waschmaschine")` liefert mit hassil
  3.12 `("n erinnere mich …", 1800)`: Die Einheitenliste matcht „Minute“ und das „n“
  landet im Wildcard-Slot. Betrifft Sekunden, Minuten und Stunden, also verzögerte
  Push-Nachrichten, relative Erinnerungen und „In 5 Minuten schalte …“. Die Lösung
  muss mit hassil 3.11 **und** 3.12 funktionieren, etwa `command_text` nur an
  Wortgrenzen akzeptieren, Pluralformen bevorzugen oder die Zeitangabe robust
  vorab per Regex abtrennen. Abnahme: Die 46 Tests, die unter hassil 3.12
  fehlschlagen (u. a. `tests/test_notification_language_corpus.py`,
  `test_push_notifications_e2e.py`, `test_recurring_and_reminders.py`,
  `test_relative_time_command_parser.py`, `test_scheduled_time_command_parser.py`,
  `test_conversation_automation_query.py::test_targetless_reschedule_…`), laufen
  mit beiden Versionen grün.
- **F2 – V12-Timer-Callback läuft im falschen Thread** (`proactive_runtime.py`,
  Methode `schedule`, innere Funktion `_fire`). `_fire` braucht `@callback`
  (aus `homeassistant.core`), sonst läuft er im Executor und
  `hass.async_create_task` wirft `RuntimeError`. Prüfe die gesamte Integration auf
  weitere Callbacks mit demselben Muster.
- **F3 – Recorder-Statistik** (`history_query.py`, `render_history_result` und
  `_async_execute_comparative_history_query`). `recorder.get_statistics` antwortet
  mit `{"statistics": {entity_id: [...]}}`. Beide Formen akzeptieren. Außerdem muss
  „Wie hoch war die durchschnittliche Temperatur im Wohnzimmer gestern?“ den Sensor
  „Temperatur Wohnzimmer“ finden (Auflösung über Bereich + Messgröße bzw.
  `device_class`, nicht nur über den Namen), ebenso „Wie hat sich der Energiezähler
  diese Woche verändert?“ und „Wie kalt war es gestern draußen minimal?“.
- **F4 – `kelvin` ist in HA 2026.9 ungültig** (`service_call.py`, ca. Zeile 659;
  `nlu/ha_automation_generator.py`, ca. Zeile 542). Stattdessen `color_temp_kelvin`
  verwenden. „Stelle das Bürolicht auf warmweiß/kaltweiß/4000 Kelvin“ muss direkt
  und in Automationen funktionieren.

### P1 – falsches Verhalten, dokumentierte Funktion fehlt

- **F5 – Ausschlussliste mit „und“** (`nlu/semantic_projection.py`,
  `_exclusion_phrases`/`_resolve_exclusions`). „außer der Stehlampe und dem
  Nachtlicht“ muss beide ausschließen. Ist ein Ausschluss nicht eindeutig
  auflösbar, wird **abgebrochen oder nachgefragt, nie ausgeführt**.
- **F6 – Optionsfluss friert die Entitätsauswahl ein** (`config_flow.py`,
  `async_step_init`). Ohne ausdrückliche Wahl des Nutzers bleibt HomeIntent
  dynamisch (leer = alle für Assist freigegebenen Entitäten). Das Formular darf
  nicht mit `default_exposed_entities()` vorbelegt werden. Bestehende
  Installationen, die so versehentlich eingefroren wurden, sind zu
  berücksichtigen (sinnvolle Migration oder klarer Hinweis im
  Bereitschaftscheck/Learning Center). `tests_ha/` anpassen bzw. erweitern.
- **F7 – Doppelte Daueranweisungen** (`standing_permission.py`, `matching()`; der
  Speicherpfad in conversation/proactive). Identische Anweisung (gleicher Besitzer,
  gleiche Situation, gleicher Bereich, gleiche Entitäten) nicht erneut anlegen,
  sondern verlängern bzw. bestätigen. Bestehende Duplikate beim Laden
  zusammenführen. „Was darfst du ohne Rückfrage?“ soll die Anweisungen auflisten
  („Welche Daueranweisungen gibt es?“ funktioniert bereits).
- **F8 – Notify-Entity im Zustand `unknown`** (`service_executor.py`,
  `_state_is_unreliable`). Zustandslose Domänen (`notify`, `button`, `scene`,
  `script`) dürfen wegen `unknown` nicht blockiert werden.
- **F9 – Automationsverwaltung per Name.** „Deaktiviere/Aktiviere die Automation
  Flurlicht bei Bewegung“ (ohne „für“) muss die Automation treffen, nicht das Gerät
  „Flurlicht“. „Füge der Automation für Außenbeleuchtung … hinzu“ darf nicht alle
  Automationen als mehrdeutig auflisten, wenn nur eine passt. Eine offene
  „nicht eindeutig“-Rückfrage darf neue vollständige Befehle nicht verschlucken.
  „Was passiert, wenn die Bewegung im Flur erkannt wird?“ soll die Benutzer-Automation
  „Flurlicht bei Bewegung“ erklären.
- **F10 – Dialogfallen.** Ein universelles „Abbrechen/Stopp/Vergiss es/Egal“ beendet
  jede offene Rückfrage. Ein vollständiger neuer Befehl beendet optionale
  Rückfragen (z. B. Komfortkonflikt „gemeinsamer Temperaturwert“). Im geführten
  Automationsdialog gelten „keine Bedingung/ohne Bedingung/keine“ als Nein.
- **F11 – Erweiterte Geräteoperationen.** Alle Sätze aus der Tabelle F11 im
  Bericht müssen funktionieren (Heizung mit „im/in der <Raum>“ + relativ/Preset,
  „Wähle beim Heizprogramm Eco“, Number mit Zahl, Warmwasser Temperatur/Modus,
  Luftbefeuchter „auf 50 Prozent“, Saugstufe (`_REGISTERED_CUE` in
  `nlu/registered_operation_compiler.py` fehlt „saugstufe“), Media-Quelle
  „auf Netflix“, Oszillation, Teilnamen wie „LED-Streifen“, `timer.*`-Helfer
  starten, „Verriegle die Haustür“ → Schloss „Haustürschloss“, „Stelle die Heizung
  im Büro ein“ fragt nach der Temperatur). Außerdem die irreführende Meldung „kein
  eindeutig passendes, für HomeIntent freigegebenes Gerät gefunden“ nur ausgeben,
  wenn tatsächlich kein Gerät gefunden wurde.
- **F12 – Abfragen.** Alle Zeilen der Tabelle F12, insbesondere: „Was ist im
  Badezimmer eingeschaltet?“, W-Fragen nie mit „Ja,“ beantworten und Namen nennen,
  Energie (kWh) nicht als Leistung (W) behandeln, CO2 mit ppm statt „unknown Grad“,
  „Wie warm ist es draußen?“ über einen Außentemperatursensor, wenn keine
  Wetter-Entität existiert, aktueller Hausverbrauch, Haus-Durchschnittstemperatur,
  eingestellter Sollwert der Heizung, Folgefragen mit „davon“ und „Schalte die aus“,
  „Wer ist zu Hause?“ ohne freigegebene Personen ehrlich beantworten, Temperaturen
  mit einer Nachkommastelle, kurze Dauern in Sekunden statt „0 Minuten“.
- **F13 – Folgeäußerungen:** „Bitte weiterspielen“ nach „Pausiere das Küchenradio“,
  „Kannst du es pausieren?“ nach „Starte das Küchenradio“, sofortige Nachricht an
  eine Person („Schick Anna eine Nachricht, dass das Essen fertig ist“).
- **F14 – Fest verdrahtetes Vokabular** (`goal_intent.py`, `_routine_name`,
  `_spoken_area`). Routinennamen aus dem Routinen-Store, Bereiche aus der
  Area-Registry (inkl. Aliasen). Eine per `homeintent.save_routine` gespeicherte
  Routine „Lesezeit“ muss per „Bereite die Lesezeit vor“/„Starte die Routine
  Lesezeit“ erreichbar sein.
- **F15 – Gerät-fertig-Erkennung** (`situation_detection.py`). Leistungssensoren
  unterstützen (unter einer Schwelle für eine Mindestdauer nach vorherigem
  Betrieb) und `binary_sensor` (on→off). Alternativ die Auswahl im Optionsfluss
  einschränken und klar warnen – bevorzugt ist die Unterstützung.
- **F27 – Listen:** „Markiere Milch und Brot als erledigt“ und „Lösche alle
  erledigten Einträge“ (landet derzeit im Kalenderpfad) müssen auf der Liste
  wirken.

### P2 – Qualität, UX, Robustheit

- **F16** – Keine englischen Wörter in Sprachausgaben (`closed`, `paused`,
  `Gesamtrisiko high`, `Antwort: accepted`, `1 preference`). Durchsuche alle
  Antwortpfade systematisch nach Rohwerten aus Enums bzw. HA-Zuständen.
- **F17** – Lange Planausführungen („Sichere das Haus“ 25 s, Garagen-„Ja“ 6 s)
  blockieren die Antwort. Sofort antworten, Wirkung asynchron prüfen, nur bei
  Fehlschlag nachmelden (die Belege für „Warum?“ und GoalRun erhalten).
- **F18** – Kein blockierendes Datei-I/O im Event-Loop: `engine.py` (`_get_shadow_parser`,
  ca. Zeile 820) lädt YAML lazy während eines Gesprächszugs. Beim Setup im Executor
  vorladen oder im Fehlerpfad nicht verwenden.
- **F19** – Nach einem HA-Neustart verschwundene Timer: benannte Timer
  persistieren oder beim nächsten „Welche Timer laufen?“ auf den Verlust hinweisen.
- **F20** – „Licht in der Küche an“ (zwei Lichter) und „Licht im Bad an“ verhalten
  sich uneinheitlich. Eine konsistente, dokumentierte Regel festlegen (Empfehlung:
  „das Licht im <Raum>“ = alle Lichter des Raums, wie beim HA-Standardagenten,
  sofern die Zielgrenze nicht greift).
- **F21** – „Schalte alle Lichter im Haus aus“ und „Mach überall das Licht aus“
  verstehen.
- **F22** – Abfragen im Kalender- und Automationsbereich als `query_answer`
  zurückgeben.
- **F23** – „Was hast du dir über mich gemerkt?“ nennt den Inhalt.
- **F24** – Vorhersagefragen ohne Modell bekommen die dokumentierte ehrliche
  Kein-Modell-Antwort.
- **F25** – Der V12-Verlauf speichert Hinweise nicht pro Empfänger doppelt (oder
  zeigt sie zusammengefasst). Die Erklärung erwähnt Antwortknöpfe nur, wenn es
  tatsächlich welche gab.
- **F26** – Standard für `allow_non_admin_automations` prüfen (Empfehlung: aus).
  Die Änderung im README/Changelog dokumentieren.
- **F28** – Gruppierte Hinweise dürfen nicht verloren gehen. Eine gruppierte
  Meldung wird zusammengefasst zugestellt, und kritische Meldungen verbrauchen
  nicht das Budget normaler Hinweise.

Falls ein Befund eine Produktentscheidung erfordert (F20, F26, Umfang von F15/F19),
triff eine begründete Entscheidung, dokumentiere sie im PR und setze sie um –
nicht offen lassen.

## Regeln

- Parser führen weiterhin keine Dienste aus. Validator → ExecutionPolicy →
  gemeinsamer Executor bleiben autoritativ. Keine Sicherheitsgrenze aufweichen
  (Bestätigungen, NEVER_AUTO, Benutzerbindung, Nur-Lesen/Nur-Admin).
- Keine Tests löschen, überspringen oder abschwächen, und Szenarioerwartungen in
  `sim/scenarios.py` nicht an das Verhalten anpassen, damit sie grün werden.
  Offensichtlich falsche Erwartungen dürfen korrigiert werden, mit Begründung im PR.
- Stil, Typisierung und Kommentardichte des umgebenden Codes beibehalten. Die
  Pyright-Strict-Scopes aus `.github/workflows/ci.yml` müssen grün bleiben.
- Keine Modellbezeichnungen in Commits, Code oder PR-Text.

## CI-Verbesserungen (Teil des Auftrags)

1. In `.github/workflows/ci.yml` einen Job bzw. eine Matrixzeile ergänzen, der die
   Stub-Suite mit der hassil-Version aus `requirements-ha-test.txt` (3.12.0)
   ausführt. Beide Versionen müssen grün sein.
2. Einen Job ergänzen, der das Live-Testbett ausführt (HA aus
   `requirements-ha-test.txt` auf Python 3.14, `sim/run_ha.sh`, `bootstrap.py`,
   `runner.py`) und bei Fehlschlägen rot wird. Szenarien mit langen Wartezeiten
   (Kategorie „Proaktiv“) dürfen in einen separaten, nächtlichen Job.
3. Für F2, F3, F4 und F8 Regressionstests in `tests_ha/` (echtes HA) ergänzen,
   denn diese Fehler sind im Stub unsichtbar.

## Abnahme

1. `python -m pytest -q` grün mit hassil 3.11.0 **und** 3.12.0.
2. `python -m pytest -q tests_ha` grün (echtes HA 2026.9.2).
3. Pyflakes und alle Pyright-Prüfungen aus der CI grün, `scripts/run_language_eval.sh`
   grün, Latenzbudgets (`scripts/benchmark_*.py` wie in der CI) eingehalten.
4. Frisches Testbett (`sim/config/.storage` und `home-assistant_v2.db*` löschen,
   `automations.yaml` aus git), dann `python sim/runner.py --out
   sim/results/nach-fix.json`: Jede Szenario-ID, die in `sim/results/final.json`
   rot war, ist grün, und keine vorher grüne ist rot. Das HA-Log
   (`sim/config/home-assistant.run.log`) enthält keine HomeIntent-Tracebacks,
   keine Thread-Warnungen und keine „blocking call“-Warnungen mehr.
5. Zusätzlich manuell im Testbett nachweisen (`sim/say.py`):
   - Garage 1 min offen → Push → „Ja.“ → Tor zu
   - Daueranweisung zweimal bestätigen → nur eine gespeichert → bei Abwesenheit
     nach 5 min Licht aus
   - „Erinnere mich in 30 Sekunden an die Waschmaschine.“ → „Ja.“ → Push kommt an
   - Durchschnittstemperatur gestern → Zahl mit °C
6. Im Bericht `docs/testbericht-live-simulation-7.1.2.md` einen Abschnitt
   „Nachtest“ mit dem neuen Ergebnis ergänzen. README-Abschnitt „Was ist neu“ plus
   Version/Changelog (Patch- oder Minor-Release nach Ermessen, z. B. 7.1.3)
   aktualisieren und eine neue Shadow-Baseline in `docs/perf/` erzeugen, falls die
   CI sie verlangt.

Arbeite die Befunde in dieser Reihenfolge ab: P0 → F5 → F6 → F7 → F8 → restliches
P1 → P2 → CI. Committe in logisch getrennten Schritten, pushe den Branch und
erstelle am Ende einen Pull Request gegen `main`. Führe im PR für jeden Befund auf,
wie er behoben und womit er getestet wurde.
