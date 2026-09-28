# Umsetzung HomeIntent 7.3.1 – 7.6.0

Arbeitsbericht zum Gesamtauftrag „sicherer, nachvollziehbarer, sprachlich
flexibler“ (Phasen 1 bis 9). Basis ist 7.3.0 (`df3ae85`; der Arbeitsbranch
beginnt auf dem gemergten Stand `ef20210`, der zusätzlich nur die Korrektur zur
Außentemperatur-Frage enthält). Jede Phase ist für sich abgeschlossen: eigene
Version, eigene Commits, grüne Tests, Abschnitt in diesem Bericht.

Grundsätze, die in allen Phasen gelten: kein LLM und kein ML-Modell, alles lokal
und deterministisch; Parser führen nie Dienste aus; Validator,
ExecutionPolicy, NEVER_AUTO und der Executor sind die einzigen Instanzen, die
über Ausführung entscheiden; keine zweite Pipeline, keine zweite
Bedeutungsschicht, keine zweite Geräteauflösung.

**Prüfumfang je Phase** (lokaler Nachbau der CI): `pytest` mit hassil 3.11
(Python 3.12) und hassil 3.12 (Python 3.13), Sprachverständnis-Gate
(`scripts/run_language_eval.sh`) mit beiden hassil-Versionen,
V8-Shadow-Baseline, `pyflakes`, `ruff` (klassische Regeln E4/E7/E9/F; das Repo
hat keine eigene ruff-Konfiguration, Maßstab ist „keine neuen Befunde“
gegenüber 7.3.0 = 57, alle E402/E731/E713 in Altcode), vollständiges Pyright
und alle Pyright-Strict-Profile, Latenz-Benchmarks. Live: echtes Home Assistant
2026.9.2 (Python 3.14.2) mit dem Testhaus aus `sim/`.

---

## Phase 1 – Transitive Sicherheit: EffectGraph (7.3.1)

### Umgesetzt

- **`effect_graph.py`** (neu, nur lesend). Zwei Schichten:
  - ein HA-freier Kern `build_effect_graph(root, sources)`, der
    Aktionskonfigurationen über ein kleines `EffectSources`-Protokoll abläuft
    und daher vollständig unit-testbar ist;
  - der Adapter `HassEffectSources`/`build_plan_effects(hass, plan)`, der die
    echte Konfiguration liest: Skripte (`script.sequence`, sonst
    `raw_config`), Automationen (`action_script.sequence`), Szenen
    (`scene_config.states`), Gruppen (Attribut `entity_id`, rekursiv) und
    Registry-UUIDs von Geräte-Aktionen (`entity_registry.async_resolve_entity_id`).
    `device_id`/`area_id`/`floor_id`/`label_id` löst er mit HAs eigenem
    `helpers.target.async_extract_referenced_entity_ids` auf – also mit genau
    der HA-Semantik (versteckte und Konfigurations-/Diagnose-Entitäten
    ausgenommen) – und filtert auf die Domäne der Aktion.
  - Alle Zweige zählen (`if`/`then`/`else`, `choose` inkl. `default`,
    `parallel`, `repeat`, `sequence`), deaktivierte Schritte (`enabled: false`)
    nicht. Verschachtelung über `script.turn_on`, `action: script.x`,
    `scene.turn_on`, `scene:`, `scene.apply` und `automation.trigger` mit
    Zyklenschutz und Tiefe ≤ 8.
  - **Unbekannt** (→ `complete = False`): Vorlagen im Dienstnamen oder Ziel,
    `event:`, `python_script`/`shell_command`/`rest_command`/`mqtt`/
    `conversation`/…, Dienste ohne prüfbares Ziel, Ziele in Dienstdaten fremder
    Dienste (ein Datenwert, der eine existierende Entität nennt), nicht
    lesbare Konfiguration, zu tiefe Verschachtelung. Bekannte zweite Ziele in
    Daten werden als Effekt erfasst (`tts.speak` → `media_player_entity_id`).
  - **Ohne Wirkungseintrag:** `delay`, `wait_*`, `variables`, `condition`,
    `stop`, `notify.*` ohne Entität, `persistent_notification.*`,
    `logbook.log`, `system_log.write`.
  - `possible_followups`: Automationen, die ein Effektziel referenzieren
    (`automations_with_entity`), nur als Hinweis.
- **Policy** (`execution_policy.evaluate_service_plan`, bleibt eine reine
  Funktion) erhält `effects`, `origin`, `attended` und `binding_confirmed`:
  1. Nicht freigegebenes wirksames Ziel → **DENY**, mit Nennung der Geräte
     (höchstens 5 Namen, dann „und N weitere“) und des breiten Schritts
     („Der Schritt ‚Rolladen Runterfahren‘ drückt alle Buttons im
     Erdgeschoss.“). Eine Bestätigung kann das nicht überstimmen.
  2. Nur-Lesen und Nur-Admin gelten für die wirksamen Ziele.
  3. CompositeRisk = max(Risiko des äußeren Plans, `classify_service_plan` je
     Effekt) – dieselben Regeln wie für direkte Befehle. Szenenzustände werden
     dafür auf den entsprechenden Dienst abgebildet (`unlocked` → `unlock` →
     CRITICAL).
  4. Fail-closed: unvollständiger Graph → Standard `effect_graph_unknown: deny`
     (DENY mit „Schritt ‚…‘ kann ich nicht prüfen“); Option `confirm` →
     mindestens HIGH und immer Bestätigung mit diesem Hinweis. Ohne anwesenden
     Nutzer (`attended=False`: Daueranweisung, AUTO-Agent, zeitversetzt) immer
     DENY. Ein zusammengesetzter Plan **ohne** Graph wird wie ein
     unvollständiger behandelt – ein Aufrufer kann die Prüfung also nicht durch
     Weglassen umgehen.
  5. `max_action_targets` zählt die wirksamen Ziele.
  6. `possible_followups` ändern das Risiko nicht; sie erscheinen als
     `PolicyDecision.note` in der Rückfrage.
  7. Kein pauschales HIGH für Skripte/Szenen/Gruppen: ein Lichtskript bleibt
     LOW.
- **Executor** (`service_executor.async_execute_service_plan`): baut den
  EffectGraph unmittelbar vor `hass.services.async_call` neu, auch nach „Ja“.
- **Alle Aufrufer** von `evaluate_service_plan` übergeben den Graphen:
  `conversation.py` (Einzelbefehl, Mehrfachbefehl, Gerätesteuerung, Undo,
  zeitversetzte Plan-Schritte mit `attended=False`), `agent_runtime.py`
  (AUTO-Pfad: `origin=PROACTIVE_PROPOSAL`, `attended=False`),
  `proactive_runtime.py` (Daueranweisung: `STANDING_PERMISSION`,
  unbeaufsichtigt; bestätigter Vorschlag: `PROACTIVE_PROPOSAL`).
  `proactive_decision.py` und `planner.py` sind reine Funktionen ohne `hass`;
  dort greift die Fail-closed-Regel (zusammengesetzt ohne Graph → DENY), und
  der Executor prüft ohnehin erneut. `validate_automation_action_targets`
  prüft von HomeIntent angelegte Automationen mit Skript-/Szenenaktion beim
  Anlegen (unbekannte Schritte → nie anlegen); die README dokumentiert, dass
  spätere Änderungen am Skript nicht erneut geprüft werden.
- **Abgeleitete Routinen** (1.4): `need_compiler._routine` schlägt jede
  gefundene Routine vor, auch Szenen (bisher startete eine eindeutige Szene
  ohne Rückfrage). Zusätzlich markiert `engine.understand` jeden Skript- oder
  Szenenplan als `INFERRED_ROUTINE`, wenn der Name (oder ein Alias) nicht
  wörtlich im Satz steht („Starte die Schlafroutine.“ → Skript „Schlafen“ nur
  mit Bestätigung). Die Policy macht daraus CONFIRM, solange keine bestätigte
  Bindung vorliegt (Minimalform von `origin`, vollständig in Phase 3).
- **Neues Modul `plan_origin.py`** mit `PlanOrigin` (`EXPLICIT_COMMAND`,
  `IMPLICIT_NEED`, `INFERRED_ROUTINE`, `PROACTIVE_PROPOSAL`,
  `STANDING_PERMISSION`); `MatchResult`, `CommandPlan`,
  `PendingServiceConfirmation` und `NeedOutcome` tragen es; ausgewertet wird
  es nur in der Policy.
- **Antworten** (1.5): Ablehnung wie im Auftrag formuliert; nach erfolgreicher
  Ausführung eine Kurzfassung des geprüften Graphen („Schlafen ausgeführt:
  2 Rollläden und 2 Lichter.“).
- **Neue Option** `effect_graph_unknown` (`deny`/`confirm`) im Options-Flow.
- **Testbett:** die 16 eingecheckten Test-Automationen aus
  `sim/config/automations.yaml` sind entfernt (einzige Änderung an `sim/`).

### Prüfung der Umgehungsfreiheit

`grep -rn "evaluate_service_plan\|async_execute_service_plan\|services.async_call"`:

- Jeder Aufruf von `evaluate_service_plan` übergibt einen Graphen oder ist eine
  reine Vorentscheidung, die ohne Graph fail-closed ablehnt.
- Jeder Gerätewrite läuft durch `async_execute_service_plan`, das den Graphen
  selbst baut.
- Die übrigen direkten `services.async_call`-Stellen schreiben keine Geräte
  und aktivieren keine Skripte, Szenen oder Gruppen: `todo.*`, `timer.*`,
  `calendar.*` (Listen, Timer, Kalender des Nutzers), `notify.*`,
  `tts.speak` und `assist_satellite.*` für Ansagen (`agent_delivery.py`),
  `media_player.play_media` nur für den Timer-Signalton (`native_timer.py`),
  `persistent_notification.create`, `automation.reload`/`turn_on`/`turn_off`/
  `trigger` ausschließlich für von HomeIntent selbst angelegte und beim Anlegen
  geprüfte Automationen, `recorder`/`history`-Lesezugriffe.

### Tests

`tests/test_effect_graph.py` (40 Fälle) deckt die 14 geforderten Unit-Fälle ab
(u. a. Etagen-Buttons mit/ohne nicht freigegebene Buttons, UUID-Geräteaktion,
`device_id` nur in der Aktionsdomäne, Zyklen und Tiefenlimit, `choose`-Zweig
mit Schloss, Szene/Lichtgruppe mit fremdem Mitglied, Alarmanlage für
Nicht-Admin, sieben Arten unbekannter Schritte mit `deny`/`confirm`/
unbeaufsichtigt/Daueranweisung, Skriptänderung zwischen Vorschau und „Ja“,
harmloses Skript bleibt LOW/ALLOW, Agent/Proaktiv/Daueranweisung, Automation
mit Skriptaktion beim Anlegen, abgeleitete Routine nur mit Bestätigung) und die
**Schlafen-Regression** mit nachgebautem Haus des Projektinhabers (Skripte
„Schlafen“ und „Gute Nacht“, `button.press` auf `floor_id`, nicht freigegebener
Saugroboter-Button und Brandmelder-Selbsttest):

- „Aktiviere Schlafen.“ → genau `script.schlafen`, Kurzfassung der Wirkung.
- „Ich gehe schlafen.“, „Gute Nacht.“, „Starte die Schlafroutine.“, „Mach alles
  für die Nacht fertig.“ → kein Skript und keine Szene ohne Bestätigung;
  „Ich gehe schlafen.“ fragt „Welche Routine meinst du: Schlafen und Gute
  Nacht?“.
- „Aktiviere Gute Nacht.“ → abgelehnt mit Nennung von Saugroboter und
  Brandmelder; ein folgendes „Ja“ führt nichts aus.

### Live-Prüfung im Testbett

Frisches Home Assistant 2026.9.2 mit dem Testhaus (`sim/fresh_ha.sh`,
`bootstrap.py`, 133 freigegebene Entitäten):

- `runner.py --exclude-category Proaktiv`: **155/155** Funktionsszenarien
  bestanden (die 7 Proaktiv-Szenarien mit minutenlangen Wartezeiten laufen in
  CI nächtlich; sie berühren den geänderten Pfad nur über den bereits
  unit-getesteten Executor).
- Phase-1-Nachweis (eigenes, nicht eingechecktes Prüfskript; legt über die
  HA-Konfigurations-API die Skripte „Nachtruhe“, „Etagen Buttons“ und
  „Schlafen“ an und entzieht Saugroboter, Kaffeemaschine und den
  Entkalken-Button die Freigabe): **14/14**.
  - „Aktiviere Nachtruhe.“ → „Das Skript „Nachtruhe“ schaltet auch Geräte, die
    für HomeIntent nicht freigegeben sind: Saugroboter und Kaffeemaschine. Ich
    habe nichts ausgeführt.“ – kein einziger Gerätaufruf, auch nicht nach „Ja.“.
  - Etagen-Button-Fall (`button.press` mit `floor_id` Erdgeschoss): abgelehnt
    mit „Kaffeemaschine entkalken“ und „Der Schritt ‚Rolladen Runterfahren‘
    drückt alle Buttons im Erdgeschoss.“; mit allen Buttons freigegeben
    Rückfrage (HIGH), nach „Ja“ genau die zwei Buttons des Erdgeschosses.
  - „Aktiviere Schlafen.“ → nur Schlafzimmerlicht und -rollladen, Antwort
    „Schlafen ausgeführt: 1 Rollladen und 1 Licht.“
  - „Ich gehe schlafen.“, „Gute Nacht.“, „Starte die Schlafroutine.“, „Mach
    alles für die Nacht fertig.“ → kein Aufruf; Rückfrage „Welche Routine meinst
    du: Gute Nacht, Nachtruhe und Schlafen?“ bzw. „Soll ich wirklich Schlafen
    ausführen?“.
- Beobachtung für Phase 3: Ein „Ja.“ nach einer Ablehnung beantwortet
  HomeIntent mit „Das habe ich nicht verstanden.“ (sicher, aber unschön); die
  Antwort auf die Routine-Rückfrage wird noch nicht als Auswahl verstanden –
  beides ist Gegenstand der Routine-Bindungen.

### Messwerte

| Kennzahl | 7.3.0 | 7.3.1 |
| --- | --- | --- |
| Nicht freigegebene Geräte über Skript/Szene/Gruppe schaltbar | ja | **nein** (Unit + live) |
| Skript/Szene mit unvollständigem EffectGraph als LOW | ja | **nie** |
| Routinewahl über Namensähnlichkeit ohne Bestätigung | ja (Szenen) | **nie** |
| Unit-Tests (hassil 3.11 und 3.12) | 6035 | 6073 |
| Funktionsszenarien live (ohne Proaktiv) | 155/155 | 155/155 |
| V8-Shadow-Report | Baseline 7.3.0 | unverändert (neue Datei `v7-shadow-baseline-7.3.1.json` unterscheidet sich nur in der Versionsnummer) |
| Latenz p95 (5000 Entitäten, `understand`) | < 100 ms | < 10 ms je Pfad, Budgets eingehalten |
| `tests_ha` (echtes HA) | 16 (CI) | lokal 15 + 1 Fehler, auf 7.3.0 identisch (Recorder-Fixture der lokalen Umgebung) |

Angepasste bestehende Tests: Der Test-Stub stellt jetzt lesbare
Skript-/Szenen-/Automationskonfiguration und HAs Zielauflösung nach; das
Testhaus lädt dafür `sim/config/scripts.yaml` und `scenes.yaml`.
`test_movie_need_starts_the_movie_scene` heißt jetzt
`test_movie_need_proposes_the_movie_scene`: „Ich will fernsehen.“ schlägt die
Szene vor, erst „Ja“ startet sie (gewollte Verhaltensänderung, S5).

---

## Phase 2 – ExecutionContext und ExecutionTrace (7.3.2)

### Umgesetzt

- **`execution_context.py`** (neu): Pro Nutzeräußerung öffnet
  `conversation._async_handle_message` einen Turn (`begin_turn`/`end_turn`,
  `ContextVar`). Der Turn trägt genau einen HA-`Context(user_id=<sprechender
  Nutzer>, parent_id=<Kontext der Assist-Anfrage>)`; seine `id` ist die
  `execution_id`. Der Executor nutzt diesen Kontext (oder einen expliziten,
  oder einen neuen mit dem Nutzer) für den Dienstaufruf und gibt die
  `execution_id` in `ExecutionResult` zurück. Agent und Proaktiv laufen
  außerhalb eines Turns und bekommen einen eigenen Kontext ohne Benutzer.
- **Alle 45 `hass.services.async_call`-Aufrufe** übergeben `context=`:
  Gerätewrites über den Executor, alle übrigen (Listen, Timer, Kalender,
  Benachrichtigungen, Automations-Reload, Recorder-Statistik) über
  `call_context()`. Ein statischer AST-Test (`test_every_service_call_passes_a_context`)
  hält das dauerhaft ein.
- **HA-Berechtigung:** Lehnt Home Assistant einen Aufruf wegen der
  Benutzerrechte ab (`Unauthorized`), meldet HomeIntent „Home Assistant
  erlaubt diesem Benutzer diese Aktion nicht.“ – ergänzend zur
  HomeIntent-Policy, nicht als Ersatz.
- **`execution_trace.py`** (neu, nur lesend): Ringspeicher
  `ExecutionTraceStore` (Standard 500 Ausführungen / 14 Tage, Optionen
  `trace_limit`, `trace_days`), ein Eintrag je `execution_id` mit Satz
  (gekürzt oder mit `trace_store_text: false` nur als Hash), gehashtem Akteur
  wie im Audit, Herkunft, Plänen, Zielen, geprüften Effekten (aus dem
  EffectGraph), möglichen Folge-Automationen, Policy-Ergebnis und Risiko.
  Persistenz als `.storage/homeintent_trace.json` (minütlich bei Änderung und
  beim Entladen). Ein `ContextIndex` folgt den HA-Ereignissen
  `automation_triggered` und `script_started` (Kontext-ID → Lauf, Name,
  Auslöser, `parent_id`) – das sind die Glieder der HA-Kette; Zustände selbst
  liest HomeIntent live aus Home Assistant und kopiert sie nicht.
- **`explain_change`**: geht vom aktuellen Zustandskontext des Geräts über
  HAs Kette (`id` → Automations-/Skriptlauf → `parent_id` …, höchstens 8
  Glieder) zurück. Effektarten `DIRECT_EFFECT`, `SCRIPT_EFFECT`,
  `SCENE_EFFECT`, `AUTOMATION_EFFECT`, `SECONDARY_EFFECT`, `EXTERNAL_EFFECT`,
  `UNKNOWN_CAUSE`; Belegstufen nur `VERIFIED` (Kette), `POSSIBLE` (zeitliche
  Nähe ± 2 min und Bezug im EffectGraph, immer als Vermutung: „Dafür finde ich
  keine Ursache, die ich belegen kann; zeitgleich lief … Das könnte
  zusammenhängen.“) und `UNKNOWN`.
- **Sprache:** `nlu/causal_question.py` erkennt Ursachenfragen zu einem
  benannten Gerät („Warum/Wieso ist … an/angegangen?“, „Wer hat … eingeschaltet?“)
  über die gemeinsame Zielauflösung. Fragen an HomeIntent selbst („Warum hast
  du …“), über Automationen und über die Vergangenheit („gestern“) bleiben bei
  ihren bestehenden Antworten. Findet der Trace **keinen** Beleg, antwortet
  weiter der bisherige, vorsichtig formulierte Automationsbezug („könnte …
  beeinflusst werden“) – es entsteht keine erfundene Kausalkette.
- **Learning Center:** WebSocket-Befehl `traces/list` und Abschnitt „Was hat
  HomeIntent ausgelöst?“ im Tab Aktivität (Admins: Haushalt; sonst nur eigene
  Ausführungen). Mobile-Prüfung (390×844, hell/dunkel) bestanden.

### Tests

`tests/test_execution_trace.py` (14 Fälle): AST-Regel für `context=`, direkter
Befehl → DIRECT/VERIFIED, Skript → SCRIPT/VERIFIED mit Schrittname, durch
Zustandsänderung ausgelöste Automation → SECONDARY bzw. AUTOMATION/VERIFIED
über `parent_id`, Person in der App → EXTERNAL/VERIFIED, zeitgleicher fremder
Effekt höchstens POSSIBLE ohne Kausalbehauptung, kein Bezug → UNKNOWN,
Ringspeicher mit Alters- und Mengengrenze, Hash-Datenschutz, ein Kontext pro
Turn über mehrere Pläne, Kontext ohne Benutzer außerhalb eines Turns,
HA-`Unauthorized` sauber gemeldet, Gespräch „Aktiviere Nachtruhe.“ → „Warum ist
der Saugroboter angegangen?“. Dazu ein Learning-Center-Test für die
Sichtbarkeit von `traces/list`.

Test-Attrappen: `tests/_ha_stub.ServiceMock` zeichnet den Kontext getrennt auf
(`.contexts`), damit die bestehenden Assertions auf Domäne/Dienst/Ziel
unverändert bleiben; der Kontext selbst wird in den neuen Tests geprüft.

### Live-Prüfung

Frisches Testhaus, eigenes Prüfskript: **8/8**. Funktionsszenarien
(`runner.py`, ohne Proaktiv) gegen genau diesen Stand: **155/155**.
- „Aktiviere Nachtruhe.“ (Skript mit `vacuum.start`, Saugroboter absichtlich
  freigegeben) → der HA-Zustand des Saugroboters trägt den Kontext mit der
  Benutzer-ID; „Warum ist der Saugroboter angegangen?“ → „Du hast um 11:36
  „Aktiviere Nachtruhe.“ gesagt. Ich habe das Skript Nachtruhe gestartet.
  Dessen Schritt ‚Saugen starten‘ hat Saugroboter gestartet.“
- Direkter Befehl → „… Ich habe daraufhin Küchenlicht eingeschaltet.“
- Anna schaltet das Bürolicht über die HA-API → „Anna hat Bürolicht um … selbst
  geschaltet, zum Beispiel in der App.“
- Bewegungsmelder löst die Benutzer-Automation „Flurlicht bei Bewegung“ aus →
  „Automation „Flurlicht bei Bewegung“, ausgelöst durch eine Zustandsänderung
  von Bewegungsmelder Flur, hat … Flurlicht geschaltet.“
- `traces/list`: Admin sieht die Ausführungen, Anna keine fremden.
- Im ersten Durchlauf fielen zwei Formulierungsfehler auf und wurden behoben:
  Uhrzeiten aus HA-Zuständen kamen in UTC, HAs englische Auslöserbeschreibung
  („state of binary_sensor…“) wird jetzt deutsch mit Gerätenamen genannt.

### Bewusst offen

- Einmalige, zeitversetzte Befehle führt Home Assistant als von HomeIntent
  angelegte Automation aus; deren spätere Wirkung erscheint im Trace als
  AUTOMATION_EFFECT dieser Automation, nicht als Glied der ursprünglichen
  Äußerung (HA verknüpft den Zeit-Auslöser nicht mit dem Anlegekontext).

---

## Phase 3 – Routine-Bindings, Implicit Action Policy, nie raten (7.3.3)

### Umgesetzt

- **`bindings.py`** (neu): der eine Speicher für alles Gelernte
  (`ROUTINE`, `ALIAS`, `DEFAULT_CHOICE`, `PREFERENCE`, `MACRO`), Geltung
  `USER`/`HOUSEHOLD`, `created_by`, `created_at`, `uses`, `last_used`.
  JSON-Datei mit Schemaversion und Migration (`.storage/homeintent_bindings.json`,
  gleiche Mechanik wie die übrigen HomeIntent-Speicher). `async_bind` verlangt
  `confirmed=True`; unbestätigte Einträge werden beim Laden verworfen.
  `binding_state` meldet `target_missing`/`not_exposed` – solche Bindungen
  wirken nicht.
- **Routine-Bindungen** (`need_compiler._routine`): Mit Bindung wird genau das
  gebundene Ziel verwendet, ohne Namenssuche; ohne Bindung dient
  `_routine_candidates` nur der Entdeckung: ein Kandidat → „Meinst du mit
  schlafen gehen das Skript X? Soll ich das jetzt starten und mir die Zuordnung
  merken?“, mehrere → „Welche Routine meinst du: …?“ (Dialogaufgabe
  `ROUTINE_BINDING`; Antwort per Name oder Ordinalzahl). Gespeichert wird erst
  nach „Ja“ bzw. Wahl **und** erfolgreicher Ausführung; lehnt die Richtlinie ab
  (z. B. nicht freigegebener Saugroboter im Skript), wird nichts gespeichert.
  Konzeptwörter wie „Schlafroutine“, „Nachtroutine“, „Filmroutine“ benennen das
  Konzept (Lexikonregel über `RoutineConcept.names`).
- Sprachbefehle (`routine_binding_intent.py`): „Vergiss die Schlafroutine“,
  „Schlafen ist ab jetzt das Skript X“ (mit Rückfrage), „Welche Routine nutzt
  du für …?“. Haushaltsbindungen löscht nur, wer sie angelegt hat, oder ein
  Admin.
- **Implicit Action Policy** in `evaluate_service_plan` (einzige Stelle, die
  `origin` auswertet), Option `implicit_action_level` mit Standard `propose`,
  im Options-Flow und im Learning Center (nur Admin) einstellbar. Regeln wie im
  Auftrag; zusätzlich gilt: Risiko ≥ Bestätigungsschwelle → immer Bestätigung,
  unabhängig von der Stufe. Die Bestätigungsfrage eines Bedürfnisses wird aus
  der Begründung abgeleitet („Soll ich die Heizung im Büro um ein Grad auf 21,5
  Grad erhöhen?“), nach „Ja“ folgt die Begründung als Erfolgstext.
- **Nie raten:**
  - `engine.understand_need`: „hier“/„da“ ohne Satellit und ohne Ort im
    Gespräch → „In welchem Raum? …“, auch wenn nur ein Gerät in Frage käme.
  - `nlu/recurrence.py` + `conversation._decide_recurrence`: Uhrzeit ohne
    Wiederholungsmarker → einmaliger Auftrag zum nächsten Vorkommen (neue
    `CalendarReference.NEXT_OCCURRENCE`); mit Marker → wiederkehrende
    Automation; Sonne/Dunkelheit ohne Marker → „… Nur heute oder jeden Tag?“
    (Dialogaufgabe `RECURRENCE_CHOICE`; „nur heute“ → `max_runs=1`). Zustands-
    ereignisse mit „wenn“ („Wenn die Haustür aufgeht …“) bleiben Regeln.
  - Ehrliche Fehlertexte: `nlu/capabilities.describe_abilities` nennt, was ein
    Gerät wirklich kann („Flurlicht lässt sich nur ein- und ausschalten.“), in
    der Zielauflösung und im Rückmeldepfad; „Dreh da die Heizung hoch“ im
    Diskurs nutzt dieselbe Lexikonregel wie der direkte Pfad
    (`directional_as_degree`) und erhöht den Sollwert.
  - „Lass X so, wie es ist“ / „Lass alles so“ → nichts tun und bestätigen
    (Vergleichssatz „wie es ist“ ist kein Rest mehr); ein bloßes „Ja“/„Nein“ ohne
    offene Frage wird ehrlich beantwortet.
- **Learning Center:** `bindings/list`, `bindings/remove`,
  `settings/implicit_action_level`; Abschnitt „Gelernte Zuordnungen“ mit Stufe,
  Geltung, Nutzungen, Zustand und „Vergessen“ im Tab Autonomie. Mobile-Prüfung
  bestanden.

### Bewusste Verhaltensänderungen (und angepasste Tests)

- Implizite Bedürfnisse werden standardmäßig vorgeschlagen statt ausgeführt
  (`propose`). `tests/test_sprache73_needs.py` prüft die Bedürfnissemantik
  deshalb mit `low_risk_auto`; neue Tests sichern den Standard (`propose`) und
  `understand_only`.
- Automationskorpus 7.2.0 (`tests/eval/automation_v72`, Entwicklungs- und
  Held-out-Teil): 53 Zeilen nach der neuen Bedeutung migriert – Uhrzeit ohne
  Marker erwartet jetzt `at(next_occurrence HH:MM)`, Sonnenereignisse ohne
  Marker bekommen „>> Jeden Tag.“ als zweiten Turn (die erwartete Automation
  bleibt damit vollständig geprüft). Die Migration ist mechanisch aus der
  Regel abgeleitet, nicht auf einzelne Sätze abgestimmt.
- V12-OOD-Dialogfälle `dlg-au-01..03` entsprechend (Sonne → Rückfrage).
- `test_inferred_routine_always_needs_confirmation_without_binding`: eine
  bestätigte Bindung führt nur mit `bound_routines_auto` direkt aus.

### Tests

- `tests/test_bindings.py` (43): Speicher (Bestätigungspflicht, Lebenszyklus,
  Geltung Nutzer vor Haushalt, Migration, Zustand), vollständige Matrix
  `origin` × `implicit_action_level` × Risiko × Bindung („nie lockerer als
  explizit“, HIGH nie ohne Bestätigung), Gespräch: Wahl bindet, gebundene
  Routine ohne Namenssuche, `bound_routines_auto`, abgelehnte Wahl bindet nicht,
  Einzelkandidat + „Ja“, Sprachverwaltung, entzogenes Ziel.
- `tests/test_never_guess.py` (31): „hier“ ohne/mit Satellit/mit Diskursort,
  Wiederholungsmarker und Antworten, einmalig vs. wiederkehrend, Rückfrage bei
  Sonne, ehrliche Fähigkeitstexte, Heizung im Diskurs, „so wie es ist“, „Ja“
  ohne Frage.
- Learning Center: Sichtbarkeit/Löschrechte der Bindungen, Stufe nur für Admins.

### Live-Prüfung

- Eigenes Prüfskript: **15/15** – Routine-Wahl bei mehreren Kandidaten;
  gewählte Routine „Nachtruhe“ mit nicht freigegebenem Saugroboter wird
  abgelehnt und **nicht** gebunden; Wahl „Schlafen“ bindet und führt aus;
  Learning Center zeigt die Bindung; danach Vorschlag ohne Namenssuche, „Ja“
  führt aus; mit `bound_routines_auto` (über das Learning Center gesetzt) läuft
  „Starte die Schlafroutine.“ direkt; „Vergiss die Schlafroutine.“; „hier“ ohne
  Satellit; Bedürfnis als Vorschlag + „Ja“; Uhrzeit einmalig; Sonnenuntergang
  mit Rückfrage; ehrliche Fähigkeitsbegründung; „Ja“ ohne offene Frage.
- `runner.py` (ohne Proaktiv): **151/155**. Die vier Abweichungen sind die
  gewollten Verhaltensänderungen dieser Phase:
  `s73-2-freezing`, `s73-2-stale-air`, `s73-s6-too-bright` erwarten die
  sofortige Ausführung eines impliziten Bedürfnisses (jetzt Vorschlag im
  Standard `propose`), `auto-manage` baut auf der Automation aus `auto-sun` auf,
  die jetzt erst nach „Nur heute oder jeden Tag?“ entsteht (das Szenario
  antwortet mit „Ja“). Mit der neuen Dialogform (Vorschlag + „Ja“ bzw. „Jeden
  Tag.“) bestehen alle fünf Szenarien in einer lokalen, nicht eingecheckten
  Kopie des Testbetts. `sim/` bleibt unverändert; die Test-Session sollte diese
  vier Szenarien an die Standardstufe `propose` anpassen oder mit
  `implicit_action_level: low_risk_auto` laufen lassen.

### Messwerte

| Kennzahl | 7.3.2 | 7.3.3 |
| --- | --- | --- |
| Routinewahl über Namensähnlichkeit ohne Bestätigung | nie (seit 7.3.1) | nie; nach Bindung keine Namenssuche mehr |
| Geraten statt gefragt: „hier“ ohne Satellit | Rückfrage nur bei mehreren Geräten | immer Rückfrage |
| Uhrzeit ohne Wiederholungsmarker | tägliche Automation | einmaliger Auftrag |
| Sonne/Dunkelheit ohne Marker | tägliche Automation | Rückfrage |
| Unit-Tests (hassil 3.11 und 3.12) | 6088 | 6165 |
| Sprach-Gate | 465 | 465 |
| Automationskorpus 7.2.0 (dev/held-out) | Schwellen erfüllt | Schwellen erfüllt (nach Migration von 53 Erwartungen, s. o.) |
