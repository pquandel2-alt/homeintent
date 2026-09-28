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
