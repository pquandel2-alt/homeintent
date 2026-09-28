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

---

## Phase 4 – Property-Based Safety Suite und Shadow-Infrastruktur (7.3.4)

### Umgesetzt

- **`tests/test_safety_properties.py`**: generative Suite mit `hypothesis`
  (neu in `requirements-dev.txt`, keine Laufzeitabhängigkeit). Satzbausteine
  kommen aus dem Lexikon (`device_ontology.GENERA`: Lemmata, Plural, Genus;
  Orte und Gattungsmitglieder des synthetischen Testhauses;
  `dative_location_phrase`), kombiniert mit Artikeln, Höflichkeit, Negation,
  Frage-, Vergangenheits- und Konjunktivrahmen, Zeitangaben, Ausnahmen und
  Nebensätzen. 17 Tests, je Invariante einer: Negation, Frage, Vergangenheit,
  Kontrafaktisches → nie Write; unbekanntes Zielwort vergrößert nie die
  Zielmenge; Ziele bleiben in der gesprochenen Gattung; unbekannte Ausnahme
  und teilweise verstandener Mehrfachsatz → keine Teilausführung;
  Zeitauftrag → nie sofort; Einzahl bei mehreren Treffern → Rückfrage;
  IMPLICIT_NEED/INFERRED_ROUTINE nie lockerer als EXPLICIT (über Stufe,
  Bestätigungsschwelle, Bindung, Risiko); CompositeRisk ≥ stärkste transitive
  Wirkung (zufällige Skripte über alle Verschachtelungsarten); unvollständiger
  EffectGraph nie LOW/ALLOW; mehrdeutige ausführbare Bedeutung → keine
  Ausführung; nicht freigegebenes wirksames Ziel → nie Write (Executor);
  gelerntes Binding → nie auf nicht freigegebene Ziele. Profile: `ci`
  (derandomisiert, 25 Beispiele) und `nightly` (zufällig, 300 Beispiele,
  eigener Job in `nightly-live.yml`); Gegenbeispiele werden in `REGRESSIONS`
  fest übernommen.
- **Shadow-Infrastruktur** als Erweiterung von `nlu/understanding.py`
  (`compare_outcomes` bleibt): `BehaviorSignature`, `behavior_signature`,
  `classify_drift` mit den Klassen `EQUIVALENT`, `REFINEMENT`,
  `BEHAVIOR_CHANGE`, `SAFETY_DRIFT` (Write statt Non-Write, andere/zusätzliche
  Ziele, Domänen- oder Gattungswechsel, niedrigeres Risiko, weggefallene
  Bestätigung), `ShadowReport` (Zählung, Beispiele, `switch_allowed`).
  - Offline: `scripts/shadow_compare.py --candidate … --check` über alle
    veröffentlichten Korpora (Testbett-Szenarien, NLU-Probe, Dialogfälle,
    Golden-Dateien, Automationskorpus; 2022 eindeutige Sätze) gegen das
    Testhaus; CI-Schritt „Shadow-Vergleich (SAFETY_DRIFT blockiert)“.
    Kandidaten liegen in `shadow_candidates.py`.
  - Live: Option `shadow_mode` (`off` Standard, `log`). `shadow_runtime.py`
    ruft registrierte Kandidaten erst nach der aktiven Pipeline auf und
    protokolliert nur (Satz-Hash, beide Signaturen, Drift-Klasse, begrenzt auf
    500). Ausgeführt wird ausschließlich das aktive Ergebnis. Sichtbar in den
    Diagnosedaten und im Learning Center (`shadow/report`, nur Admin).

### Ergebnisse

- CI-Profil: 17/17 Invarianten; Nightly-Profil lokal (300 Beispiele je
  Invariante, zufällige Seeds): 17/17, **0 verletzte Invarianten**, daher keine
  Regressionseinträge.
- Offline-Shadow „identity“: 2022/2022 EQUIVALENT (Selbsttest der Werkzeugkette).
- `tests/test_shadow.py` (7): alle Drift-Gründe; ein absichtlich fehlerhafter
  Kandidat (Küchenlicht → Saugroboter) wird als SAFETY_DRIFT erkannt
  (`domain_change`, `more_or_other_targets`) und blockiert das Umschalten; im
  Live-Shadow-Modus wird der Kandidat aufgerufen, schreibt aber nachweislich
  nichts (nur das Küchenlicht wird geschaltet); das Protokoll enthält keinen
  Satztext; im Modus `off` werden Kandidaten nicht aufgerufen.

---

## Phase 5 – Eine Zielauflösung (7.4.0)

### Umgesetzt

- **`entities.resolve_entity_scored`** in zwei Teile geteilt, das Verhalten
  bleibt unverändert: `rank_name_candidates` (Namensstufe mit Punkten) und
  `assemble_name_resolution` (Status aus der Rangfolge). Beide Teile nutzt
  jetzt auch die neue Auflösung.
- **`nlu/target_resolution.resolve_phrase`** ist der einzige Einstieg für
  „welches Gerät meint dieser Name?“:
  - Namensstufe (exakt, Alias, Teilname, begrenzte Tippfehler-Korrektur).
  - Kandidaten sind nur die übergebenen, also freigegebenen Entitäten.
  - **Klassengrenze**: Nennt die Phrase eine Gerätegattung, fallen Teilnamen-
    und Tippfehler-Kandidaten fremder Gattung weg. Maßgeblich ist die Domäne
    oder die Gattung aus Name und Geräteklasse der Entität, so bleibt „Licht
    Sportraum“ als Schalter ein Licht. Exakte Registry-Namen bleiben
    maßgeblich. Befehlswörter (`automation`, `script`, `scene`) sind keine
    Gerätegattung.
  - **Mehrdeutigkeit bleibt Mehrdeutigkeit.** Ein gesprochener Ort engt ein,
    sonst nichts.
  - `numbered_question` liefert die nummerierte Rückfrage.
  - Die Stelle, an der ab 7.4.1 gelernte Bindungen angewandt werden, ist im
    Modulkommentar festgelegt.
- **Shadow alt gegen neu** (`scripts/resolver_shadow.py`, Hook in
  `tests/conftest.py` über `HOMEINTENT_RESOLVER_SHADOW`):
  - Bei jedem Aufruf liefen beide Resolver. Die Abweichungen wurden je
    Aufrufer klassifiziert (`compare_resolutions`):
    - neue Ziele → SAFETY_DRIFT
    - weggefallene Bestätigung → SAFETY_DRIFT
    - alt gelöst, neu nicht → „alt besser“, außer der alte Resolver hat die
      genannte Gattung überschritten → REFINEMENT/`class_boundary`
    - Teilmenge → REFINEMENT
  - **Umschalten je Aufrufer:** Alle 19 Aufrufstellen in 10 Modulen rufen
    jetzt `resolve_phrase` auf, darunter
    - `parsers`
    - `semantic_compiler`, `semantic_projection`
    - `entity_resolution.rank_semantic_targets`/`resolve_named_target`
    - Automations-Auslöser, -Bedingungen und -Ziele
    - `conversation_correction`, `alias_learning`
    - `entities.resolve_entity`
  - `resolve_entity_scored` bleibt als historische Namensstufe für den
    Vergleich.
  - Ein Architekturtest verbietet neue direkte Aufrufe.
  - Das Skript hüllt jetzt `resolve_phrase` ein und läuft als CI-Schritt
    „Zielauflösung gegen historischen Resolver (SAFETY_DRIFT blockiert)“.
- **Eigene Suchen, die bleiben** (dokumentiert, nicht umgestellt):
  - `entity_scope.resolve_entity_scope` (registrierte Operationen, Timer,
    erweiterte Abfragen) ist eine Bereichssuche nach „ein Gerät / eine
    homogene Raum-, Etagen- oder Alles-Gruppe“. Sie arbeitet über
    Teilnamen im Satz und lehnt mehrere Namenstreffer und gemischte
    Domänen ab. Sie rät also nie, und ihre Aufgabe (Gruppen) ist eine andere
    als die Namensauflösung.
  - `target_resolution.resolve_description` (Gattung + Ort + Merkmal) ist die
    Beschreibungsauflösung desselben Moduls und nutzt dieselben Regeln.

### Shadow-Ergebnis vor dem Umschalten

| Lauf | Aufrufe | EQUIVALENT | REFINEMENT | alt besser | SAFETY_DRIFT |
|---|---|---|---|---|---|
| Korpus (2022 Sätze) | 438 | 436 | 2 | 0 | 0 |
| Testsuite, erster Stand | 1743 | 1729 | 9 | 5 | 0 |
| Testsuite nach Nachbesserung | 1743 | 1730 | 13 | 0 | 0 |

**Die fünf Fälle „alt besser“:**
- Drei waren Fehlgriffe des alten Resolvers über die Gattungsgrenze
  („Rolllade Büro“, „Rollladen Büro“ → `light.buero`; „Rollladen Küche“ →
  `light.kueche`). Der neue lehnt sie ab; sie zählen jetzt als
  REFINEMENT/`class_boundary`.
- Einer kam aus demselben Grund aus einer Paraphrase („der Rollladen im Büro
  50 Prozent erreicht“ → `light.buero`).
- Einer war ein echter Rückschritt: „…eine Automation für die Haustür…“.
  Dort wurde „Automation“ als Gattung gelesen. Behoben, Befehlswörter sind
  keine Gerätegattung.

**Weitere Nachbesserung:** Bei „Licht“ fielen zunächst Schalter weg, die
„Licht“ im Namen tragen. Das ist behoben, sie bleiben Kandidaten.

**Verbleibende REFINEMENTs:**
- „Heizung Wohnzimmer“ wird statt mehrdeutig über Klima, Licht und Medien
  jetzt aufgelöst.
- „Schlafzimmer Fenster“ wird zum Fensterkontakt statt mehrdeutig mit dem
  Ventilator.
- „Tür“ nennt nur noch Türkontakte statt zusätzlich Schloss und Klingeltaste.
- „Rolllade Wohnzimmer“ ohne Rollladen im Haus: nicht gefunden statt
  mehrdeutig über fremde Geräte.

### Ergebnisse nach dem Umschalten

- Ende-zu-Ende über den Shadow-Korpus: Signaturen vor dem Umschalten (Commit
  658bba2) gegen danach, **2022/2022 EQUIVALENT**, 0 SAFETY_DRIFT.
- V8-Shadow-Baseline unverändert (`docs/perf/v7-shadow-baseline-7.4.0.json`,
  bis auf die Versionsnummer identisch mit 7.3.4).
- Neue Tests `tests/test_one_target_resolution.py` (14):
  - Architekturregel
  - Klassengrenze, auch Ende-zu-Ende: „Fahre den Rollladen Büro hoch“
    schaltet kein Licht
  - Gattung im eigenen Namen, Befehlswörter
  - exakte Namen
  - Ortseinengung
  - Bestätigung bei einzelner Tippfehler-Korrektur
  - nur freigegebene Kandidaten
  - nummerierte Rückfrage
  - Drift-Klassen

### Live-Prüfung (Testbett, frisch aufgesetzt)

- `sim/runner.py`: **158/162**. Die vier Abweichungen sind die gewollten aus
  Phase 3: Bedürfnisse im Standard `propose` (s73-s6-too-bright,
  s73-2-freezing, s73-2-stale-air) und die Sonnenuntergangs-Rückfrage in
  auto-manage. Keine neue Abweichung durch die Zielauflösung.
- Schlafen-Regressionen und Phase-3-Prüfungen: **15/15**.
- Zielauflösung live: **8/8**.
  - „Fahre den Rollladen Büro hoch“ fährt den Raffstore und kein Licht.
  - Mehrdeutig („Wohnzimmer Rollladen“, „Nachttischlampe“) gibt eine
    nummerierte Rückfrage; Antwort per Seite oder Nummer.
  - Ein exakter Name wird ausgeführt.
  - Ein Tippfehler führt zur Bestätigung statt zur Ausführung.
  - In einer Automation wird „Rollladen im Büro“ zum Rollladen-Auslöser und
    nie zum Bürolicht.
- Hinweis zum Ablauf: Ein erster Runner-Lauf wurde durch ein Zeitlimit
  abgebrochen und hinterließ geänderte Optionen. Das Testbett wurde neu
  aufgesetzt; gezählt wird nur der saubere Lauf.

---

## Phase 6 – Selbstlernen aus dem Dialog (7.4.1)

### Umgesetzt

- **`dialog_learning.py`** (reine, deterministische Regeln) und
  **`conversation_learning.py`** (Mixin der Konversation, verbindet Regeln,
  Dialog und `BindingStore`). Gelernte Sätze (Makro, Vorliebe, Befehl nach
  einem unbekannten Wort) laufen durch `_async_handle_message_inner`, also
  durch dieselbe Pipeline wie ein gesprochener Befehl. Es gibt keine zweite
  Ausführungs- oder Bedeutungsschicht.
- **Ein Speicher:** Neue Aliasse, Standardauswahlen, Vorlieben und Makros sind
  Bindungen (`bindings.py`, Schema 2). Neu in Schema 2 sind die beobachteten
  Antworten auf Rückfragen (`choices`); sie sind Beobachtungen und berechtigen
  zu nichts. Das Alias-Lernen („Mit X meine ich Y“) schreibt nicht mehr in die
  Optionen und nicht mehr in das V11-Modellregister. Alte Einträge dort
  wirken weiter; `custom_aliases` bleibt Konfiguration.
- **Anwendung nur in der Zielauflösung:** `target_resolution` besitzt beide
  Stellen, an denen Bindungen angewandt werden.
  - `apply_alias_bindings` hängt Namen als Aliasse an die freigegebenen
    Snapshots. Damit sind sie Lexikon, kein Satz, und wirken in allen
    Satzformen.
  - `default_choice_for` wählt nur unter den Kandidaten, die die Rückfrage
    selbst anbietet (Schlüssel: Kandidatenmenge × Ort, Sprecher =
    Geltungsbereich).
- **Unbekanntes Geräte-Nomen:**
  - Erkennung durch `unknown_device_noun`: Ein Befehlsverb am Anfang, genau
    ein großgeschriebenes Wort, das weder Gerät, Raum, Gattung noch
    Funktionswort ist, und nur ein Teilsatz.
  - Rückfrage mit nummerierten Optionen aus Aktion (Domänen) und Ort
    (gesprochener Ort, sonst Satellit).
  - Nach der Wahl wird der Satz mit dem Gerätenamen ausgeführt, danach kommt
    das Alias-Angebot.
  - Spezifische Erklärungen (unbekannte Etage, Fähigkeit) haben Vorrang.
  - Neu: Ein Befehl mit unbekanntem Wort wird nie über Kontext-Anschlüsse
    (`match_followup`, `match_reference`, `match_query_followup` u. a.)
    ergänzt. Vorher beantwortete „Mach den Zauberkasten an“ nach einer Frage
    zur Stehlampe die Stehlampen-Frage erneut.
- **Rückfragen und Korrekturen:**
  - Zweimal dieselbe Wahl führt einmal zum Angebot einer Standardauswahl.
  - Korrekturen („Nein, ich meinte X“, „Nein, X“) zählen als Wahl zwischen
    dem vorigen und dem gemeinten Gerät. Nach einer Standardauswahl zählen
    sie zu deren Kandidaten.
  - Neu versteht die Korrektur die bloße Seitenangabe („die rechte“ nach
    „Nachttischlampe links“) als Geschwistergerät, und zwar nur, wenn der
    Name selbst eine Seite trägt.
- **Vorlieben und Makros:**
  - Vorlieben werden gemerkt, beantwortet, beim ersten Mal mit Vorschau
    angewandt und gezielt vergessen.
  - Makros sind bestätigte Sätze und keine HA-Automationen.
  - Der Makroname darf kein Geräte-, Raum- oder Gattungsname sein.
  - Der Makrotext muss als schreibender Befehl verstanden werden, sonst wird
    nichts gespeichert.
- **Sicherheit beim Lernen:**
  - Gespeichert wird nur nach „Ja“. Ein neuer Befehl statt einer Antwort
    lässt das Angebot verfallen.
  - Es gibt nur freigegebene Ziele; ein entzogenes Ziel ist wirkungslos und
    wird in „Was weißt du über mich?“ und im Learning Center markiert.
  - Geräte-, Raum- und Gattungsnamen (exakte Gattungswörter) werden nie
    überschrieben.
  - Kritische Ziele (Schloss, Alarm, Sirene, Ventil, Tor, Tür) dürfen nur
    Administratoren benennen.
  - Löschen fremder Haushaltseinträge per Sprache ist nur für Administratoren
    möglich.
- **Kleinigkeiten:**
  - Deutsche Bezeichnungen für V11-Modelle (Art, Status, Tageszeit,
    Dezimalkomma).
  - Das Learning Center zeigt Vorlieben und Makros mit Tätigkeit bzw. Satz.
  - Der Text bei ausgeschaltetem Gedächtnis erklärt, wo man es einschaltet.
  - „Was hast du gelernt?“ bleibt die V11-Übersicht; ohne V11 antworten die
    Bindungen.

### Bewusst so belassen

- `alias_learning.py` behält seine drei Lehr-Satzrahmen („Mit X meine ich Y“,
  „Nenne X künftig Y“, „X bedeutet Y“). Sie erkennen die Sprechhandlung
  „Lehren“. Die Wirkung eines Namens ist nicht mehr an Satzmuster gebunden,
  sondern ein Lexikoneintrag der Zielauflösung (10+ Satzformen ohne eigene
  Programmierung, s. Tests).
- Der Zeitraffer läuft in der Testsuite und nicht im Live-Testbett. Die
  Gewohnheitserkennung nimmt ihre Zeit aus den Zeitstempeln der
  Ausführungsläufe; das ist bereits eine testbare Zeitquelle, eine
  zusätzliche war nicht nötig.
- „Nein, nur X“ nach einem Befehl an mehrere Geräte wird korrigiert, aber
  nicht als Standardauswahl gezählt. Es gab keine Rückfrage, deren Antwort
  künftig ersetzt werden könnte.

### Tests und Messwerte

- `tests/test_dialog_learning.py` (45):
  - ein gelerntes Wort in 10 Befehlsformen plus Frage, Zeitauftrag, „lass
    an“ und Verneinung; das Wort ist nirgends eigens programmiert
  - Lehrdialog, unbekanntes Wort (Ja/Nein, kein Kontextersatz, kein Einsatz
    in Mehrfachsätzen)
  - Sicherheitsregeln (Namen, Räume, Gattung, kritische Ziele, entzogene
    Ziele, nur nach Ja)
  - Standardauswahl (persönlich, explizit gewinnt, andere Person wird
    gefragt)
  - Vorlieben, Makros (auch: kritischer Schritt fragt bei jedem Aufruf),
    deutsche Ausgaben, Parser
- `tests/test_safety_properties.py` (+2 Invarianten):
  - Ein gelernter Name erreicht nie mehr als sein freigegebenes Ziel;
    Verneinung, Frage und Zeitauftrag schreiben nie.
  - Ein Makro umgeht nie die Bestätigung kritischer Schritte.
  - Nightly-Profil (300 Beispiele): grün. Ein zunächst gefundenes
    „Gegenbeispiel“ war eine falsche Testannahme („Kannst du … einschalten?“
    ist eine Bitte).
- Zeitraffer-Test (`test_conversation_v11_learning.py`), 14 simulierte Tage:
  - Gewohnheit ab 10 Belegen nur als Vorschlag
  - „Nein“ ist dauerhaft; nach weiteren Tagen kein erneuter Vorschlag
  - keine Routine, keine Automation, kein Dienstaufruf
- Korpusvergleich 7.4.0 gegen 7.4.1 auf Engine-Ebene: 2022/2022 EQUIVALENT.
  Die Konversationsschicht ist durch die Testsuite abgedeckt.
  Resolver-Shadow unverändert (0 SAFETY_DRIFT, 0 „alt besser“).
