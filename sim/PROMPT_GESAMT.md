# Gesamtauftrag HomeIntent 7.3.1 – 7.6 (vollständig umsetzen): sicherer, nachvollziehbarer, sprachlich flexibler

Repository `pquandel2-alt/homeintent`, Integration `custom_components/homeintent/`.
Basis ist **7.3.0** (Branch `claude/sprachverstaendnis-prompt-dkzpr9`, Commit
`df3ae85`). Arbeite auf einem eigenen Branch. Messberichte und Testbett liegen im
Branch `claude/sleepy-meitner-xd7oux` (`docs/nachtest-sprachverstaendnis-7.3.0.md`,
`sim/`).

Dieser Auftrag ersetzt `sim/PROMPT_7.4.md` und `sim/FIX_PROMPT_SKRIPT_FREIGABE.md`.
Die dort beschriebenen Punkte sind hier vollständig enthalten und mit den zusätzlichen
Architekturanforderungen zusammengeführt.

**Setze den gesamten Auftrag in dieser Session vollständig um: alle Phasen 1 bis 9,
nacheinander, ohne zwischendurch auf eine Freigabe zu warten.** Halte nicht nach
Phase 1 an. Die Phasen bauen aufeinander auf; ihre Reihenfolge und die
Abnahmekriterien jeder Phase bleiben verbindlich (siehe „Arbeitsweise“ am Ende).
Die Test-Session prüft das Ergebnis danach im Live-Testhaus.

---

## 0. Unverrückbare Grundsätze

**Laufzeit ohne LLM.** Kein LLM und kein ML-Modell, auch nicht optional: keine
Embeddings, kein `ai_task`, keine Aufrufe anderer Conversation Agents.
HomeIntent bleibt lokal, deterministisch und nachvollziehbar. Gleicher Satz + gleicher
Hauszustand + gleicher gespeicherter Lernstand = gleiches Ergebnis.

**Die 7.3-Architektur bleibt erhalten und wird nicht verdoppelt.** Es entsteht keine
zweite Bedeutungsschicht, keine zweite Geräteauflösung und keine zweite
Ausführungspipeline.

Parser interpretieren und führen nie Dienste aus. Jede Wirkung läuft durch:

```
Sprachverständnis → Grounding → Plan → Validator → ExecutionPolicy
  (+ AutoExecutionPolicy/NEVER_AUTO) → ggf. Bestätigung → Executor
  → Effect Verification → GoalRun → Learning
```

**Weiterhin verbindlich:**
- Unbekannt bleibt unbekannt; die Zielauflösung rät nie.
- Mehrdeutigkeit führt zu Rückfrage oder Ablehnung.
- User-Binding, Admin-Regeln, Nur-Lesen-Entitäten und die Freigabeliste bleiben.
- Effect Verification und GoalRun bleiben.
- V11-Lernen autorisiert keine Ausführung.
- Vorhersage ≠ Erlaubnis, Gewohnheit ≠ Automation, Konfidenz ≠ Einwilligung,
  proaktiv ≠ autonom.
- Nachvollziehbarkeit (Trace, Kontext, Korrelation) ist **nie** eine Berechtigung.

**Verstehen statt Auswendiglernen.** Jede sprachliche Verbesserung ist eine allgemeine
Regel über Bedeutungsbausteine (Lexikon, Ontologie, Struktur, Diskurs), nie ein
Satzmuster für einen Einzelsatz.

**Migration statt Big Bang, auch innerhalb dieser einen Session.** Jede Phase wird
für sich abgeschlossen, bevor die nächste beginnt: eigene Versionsnummer, eigener
Commit (bzw. mehrere), Push, Abschnitt im Bericht und grüne Tests (hassil 3.11 und
3.12, `ruff`, hassfest, bestehende Pyright-Strict-Profile, die Tests der Phase).
Große Umbauten (Phasen 5, 7, 8) laufen zuerst im Shadow-Modus und werden erst nach
sauberem Shadow-Report umgeschaltet. Das bleibt so, obwohl alles in einer Session
passiert.

---

## 1. Ausgangslage (Befunde aus Code und Live-Test 7.3.0)

| Nr. | Befund | Beleg |
| --- | --- | --- |
| S1 | **Skripte, Szenen und Gruppen umgehen die Freigabeliste.** Nur die äußere Entität wird geprüft. | Live: „Aktiviere Nachtruhe.“ startet den nicht freigegebenen Saugroboter und die nicht freigegebene Kaffeemaschine; direkte Befehle an beide werden korrekt abgelehnt |
| S2 | **Skripte und Szenen gelten als LOW.** | `risk.py`: `script`/`scene` stehen in keiner Risikoliste; nur `button` ist HIGH und mehr als 5 Ziele erhöhen die Stufe |
| S3 | **Realer Vorfall (Projektinhaber, 7.1.2):** „Aktiviere Schlafen“, danach liefen Saugroboter und Brandmelder-Selbsttest. | Ursache im Skript „Gute Nacht“ des Nutzers: `button.press` mit `target: floor_id: erdgeschoss` drückt **alle** Buttons der Etage. Eine Wortähnlichkeit als Ursache ist **nicht** belegt. |
| S4 | **Kein HA-Kontext an Dienstaufrufen.** | Keiner der 45 `hass.services.async_call(...)`-Aufrufe übergibt `context=`. HA kann Folgeeffekte daher nicht der HomeIntent-Aktion und dem Nutzer zuordnen, und die HA-Berechtigungsprüfung für Nicht-Admins greift nicht. |
| S5 | **Routinewahl über Wortstämme bei jeder Ausführung.** | `nlu/need_compiler.py::_routine_candidates`: Teilstring-Suche („nacht“, „schlaf“, „film“ …) in Name, Alias und Beschreibung. Bei genau einem Treffer wird eine **Szene ohne Rückfrage** gestartet. |
| S6 | **Implizite Bedürfnisse mit geratenem Ort.** | Holdout: Kälte-Aussage mit „hier“ ohne `device_id` verstellt die Heizung eines bestimmten Raums |
| S7 | **Router-Reihenfolge entscheidet.** | `conversation.py::_async_handle_message_inner`: first-match-Kaskade über rund 20 Handler |
| S8 | **Audit nur flach.** | `audit_log.py` speichert Zeit, Akteur-Hash, Domäne, Dienst und Ziele, aber weder Ursache noch Kette |
| Q1 | **Generalisierung begrenzt.** | 85 % auf veröffentlichten Korpora, **38 %** auf 45 nie gezeigten Sätzen |
| Q2 | **Zwei Geräteauflösungen.** | `nlu/target_resolution.py` (neu) und `nlu/entity_resolution.py` (alt), ohne Verbindung |
| Q3 | **Sprachinseln.** | Verlauf, Listen/Timer, Kalender, Automationsverwaltung, Haushaltsfragen, Ziele und Alias-Lernen haben eigene Satzmuster |
| Q4 | **Lernen nur auf ausdrücklichen Befehl.** | Alias-Lernen wirkt gut in mehreren Satzformen. Rückfragen und Korrekturen werden nicht gelernt; „Merk dir …“ wird gespeichert, aber nie benutzt. |
| Q5 | **Einmalig vs. wiederkehrend verwechselt.** | Befehl „um <Uhrzeit> …“ ohne Wiederholungsmarker legt eine tägliche Automation an |

**Vorhanden und wiederzuverwenden (nicht neu bauen):**
- `nlu/semantic_utterance.py::SemanticUtterance`: SpeechAct, Modality, Polarity,
  Klauseln, `pragmatic_disposition`
- `nlu/frame.py::SemanticFrame`
- `nlu/understanding.py::ShadowComparison`/`compare_outcomes`: heute nur für alte
  hassil-Grammatiken
- `standing_permission.py`: `AutoExecutionPolicy`, `NEVER_AUTO_DOMAINS` enthält
  bereits `script`, `button` und `vacuum`
- `execution_policy.evaluate_service_plan`: reine Funktion
- `service_executor.async_execute_service_plan`: einziger Schreibpfad
- `audit_log.AuditTrail`, `effect_monitor.EffectMonitor`, GoalRun/Experience
- `alias_learning.py`, `memory.py`, Learning Center

---

## 2. Zielarchitektur (konsolidiert)

```
Utterance (+ device_id, user_id)
 → LanguageDocument (language_frontend, german_structure)
 → Kandidaten-Interpretationen          ← alle Interpreter liefern Kandidaten statt
     (SemanticUtterance + Erweiterung)     die Kaskade zu beenden (Phase 5)
 → Arbitration                           ← entscheidet nach Evidenz, nie nach Reihenfolge
 → Grounding (EINE Zielauflösung, Phase 4)
     + Routine-Binding / gelernte Bindungen (Phase 3/6)
 → Domänenmodell (CommandPlan | AutomationModel | Query | NeedOutcome | Goal)
 → Validator
 → EffectGraph (transitive Wirkung von Skript/Szene/Gruppe)       ← Phase 1
 → Risk (CompositeRisk)            → ExecutionPolicy (+ Origin, Phase 3)
 → AutoExecutionPolicy/NEVER_AUTO  → Bestätigung
 → Executor (einziger Schreibpfad, übergibt ExecutionContext an HA) ← Phase 2
 → Effect Verification → GoalRun → Experience/Learning
 → ExecutionTrace (liest HA-Kontextkette, Logbuch, Traces)          ← Phase 2
```

**Neue Komponenten, die wirklich nötig sind** (je genau eine):

| Komponente | Zweck | Phase |
| --- | --- | --- |
| `effect_graph.py` | statische transitive Wirkungsanalyse | 1 |
| `ExecutionContext` im Executor | Korrelation über HA-`Context` | 2 |
| `execution_trace.py` | Ursache-Wirkungs-Kette mit Belegstufe | 2 |
| `bindings`-Speicher | bestätigte Zuordnungen: Routine → Skript/Szene, später Alias, Standardauswahl, Vorliebe, Makro | 3, erweitert in 6 |
| Feld `origin` im Plan, in der Policy ausgewertet | explizit, implizit, abgeleitete Routine, proaktiv, Daueranweisung | 3 |
| Arbitration | Kandidatenvergleich | 5 |

**Keine** neue parallele IR: `SemanticUtterance`/`MeaningClause` wird schrittweise um
Ziel, Zeit, Bedingungen, Ausnahmen und Herkunft erweitert (Phase 5).

---

## Phase 1 – Transitive Sicherheit: EffectGraph (7.3.1)

### 1.1 EffectGraph

Neues Modul `effect_graph.py`. Die Funktion `async_build_effect_graph(hass, entity_id)`
arbeitet nur lesend und liefert:

```
EffectGraph
  root: entity_id
  effects: tuple[Effect]           # je Aktion: domain, service, entity_ids, kind, step_alias, path
  nested: tuple[entity_id]         # aufgerufene Skripte/Szenen/Automationen (automation.trigger)
  unknown: tuple[UnknownStep]      # nicht statisch bestimmbare Schritte + Grund
  complete: bool                   # False, sobald unknown nicht leer ist
  possible_followups: tuple[entity_id]  # Automationen mit Trigger auf einem Effektziel (nur Hinweis)
```

**Quellen:**
- **Skripte:** Die Sequenz wird aus der Skript-Konfiguration rekursiv gelesen. Zum
  Abgleich dienen die HA-Helfer `entities_in_script`, `devices_in_script`,
  `areas_in_script`, `floors_in_script` und `labels_in_script`. Alle
  `if`/`choose`/`parallel`/`repeat`-Zweige zählen, nicht nur der gerade zutreffende.
- **Szenen:** `entities_in_scene` bzw. das Attribut `entity_id`.
- **Gruppen:** das Attribut `entity_id`, rekursiv.
- **Geräte-Aktionen** (`type:`/`domain:`/`device_id:`): Registry-UUID über
  `entity_registry.async_resolve_entity_id` auflösen.
- **Ziel `device_id`, `area_id`, `floor_id` oder `label_id`:** Entitäten in der
  **Domäne der Aktion**, so wie HA sie auflöst. Beispiel: `button.press` auf eine Etage
  ergibt alle Buttons dieser Etage.
- **Verschachtelung** (`script.turn_on`, `action: script.x`, `scene.turn_on`,
  `automation.trigger`): rekursiv, mit Zyklenschutz und Tiefe ≤ 8.
- **Unbekannt:** Templates im Ziel, `event:`, `python_script.*`, `shell_command.*`,
  `rest_command.*`, Ziele in `data` fremder Dienste, nicht lesbare Konfiguration.
- **Unkritisch, ohne Wirkungseintrag:** `delay`, `wait_*`, `variables`, `condition`,
  `stop`, `notify.*` ohne Entität, `persistent_notification.*`, `logbook.log`.

### 1.2 Entscheidung

Die Policy bekommt die wirksamen Ziele und `complete` zusätzlich übergeben;
`evaluate_service_plan` bleibt eine reine Funktion.

1. **Freigabe:** Ist ein wirksames Ziel nicht freigegeben, gilt **DENY**. Eine
   Bestätigung kann das nicht überstimmen, denn die Freigabe ist Konfiguration des
   Nutzers.
2. **Nur-Lesen** und **Nur-Admin** gelten für die wirksamen Ziele.
3. **CompositeRisk = max(Risiko aller wirksamen Effekte, Risiko des äußeren Plans).**
   Die Einstufung erfolgt mit denselben Regeln wie für direkte Befehle
   (`risk.classify_service_plan` pro Effekt).
4. **Fail-closed:** Bei `complete == False` wird das Unbekannte **nie** als LOW
   gewertet.
   - Standard (`effect_graph_unknown: deny`): DENY mit Nennung des Schritts.
   - Option `confirm`: CompositeRisk mindestens HIGH und immer Bestätigung. Die
     Bestätigungsfrage nennt ausdrücklich „Schritt ‚…‘ kann ich nicht prüfen“.
   - Unbekannte Schritte in Pfaden ohne Nutzer (proaktiv, Daueranweisung,
     zeitversetzt) → immer DENY.
5. `max_action_targets` zählt die wirksamen Ziele.
6. `possible_followups` beeinflussen das Risiko **nicht**, weil sie nicht belegt sind.
   Sie erscheinen nur als Hinweis in der Vorschau („Kann Automation X auslösen“) und im
   Trace.
7. **Übergangsregel:** Für `script`, `scene` und Gruppen gilt kein pauschales HIGH.
   Der EffectGraph kommt im selben Release und liefert die genauere Stufe; ein
   pauschales HIGH würde jedes harmlose Lichtskript bestätigungspflichtig machen. Wo
   der EffectGraph nicht gebaut werden kann, greift Punkt 4.

### 1.3 Wo die Prüfung sitzt

Der EffectGraph wird im Executor unmittelbar vor `hass.services.async_call` neu
gebaut, auch nach einer Bestätigung. So wird ein Skript, das zwischen Vorschau und „Ja“
geändert wurde, mit seinem neuen Inhalt geprüft.

Dieselbe Prüfung gilt für:
- `conversation.py` (direkte Aufrufe von `evaluate_service_plan`, um Zeile 2974 und
  4381),
- `agent_runtime.py`, `proactive_decision.py` und `standing_permission.py`,
- zeitversetzte und geplante Befehle,
- `validate_automation_action_targets` für von HomeIntent angelegte Automationen
  (Prüfung beim Anlegen; „Skript X kann später geändert werden“ in der README
  dokumentieren).

`grep -rn "evaluate_service_plan\|async_execute_service_plan\|services.async_call"`
darf keinen Pfad mehr ergeben, der den EffectGraph umgeht.

### 1.4 Abgeleitete Routinen nie lockerer als explizite Befehle

`need_compiler._routine` startet heute eine **Szene** bei genau einem Treffer ohne
Rückfrage. Ab jetzt gilt: Eine **abgeleitete** Routine (aus „Ich gehe schlafen“, „Gute
Nacht“, „Filmabend“) ohne bestätigte Bindung (Phase 3) wird immer als Vorschlag mit
Bestätigung behandelt, auch für Szenen. Die Entscheidung trifft die Policy anhand des
`origin`-Felds (Minimalform schon hier, vollständig in Phase 3).

### 1.5 Antworttexte

> Das Skript „Gute Nacht“ schaltet auch Geräte, die für HomeIntent nicht freigegeben
> sind: Saugroboter Reinigung starten, Brandmelder Flur Selbsttest. Der Schritt
> ‚Rolladen Runterfahren‘ drückt alle Buttons im Erdgeschoss. Ich habe nichts
> ausgeführt.

Höchstens 5 Namen nennen, danach „und N weitere“. Die vollständige Liste gehört in
Audit und Diagnose. Nach erfolgreicher Ausführung eine kurze Zusammenfassung ausgeben:
„Gute Nacht ausgeführt: 9 Rollläden.“

### 1.6 Tests Phase 1

- **Unit-Tests:**
  1. `entity_id` nicht freigegeben → DENY.
  2. `button.press` mit `floor_id` (Saugroboter- und Brandmelder-Button nicht
     freigegeben) → DENY; alle Buttons der Etage freigegeben → Risiko HIGH wegen
     `button`, also Bestätigung.
  3. Geräte-Aktion mit UUID wird aufgelöst.
  4. Ziel `device_id` → nur Entitäten der Aktionsdomäne.
  5. Verschachtelte Skripte; ein Zyklus hängt nicht.
  6. Ein nicht zutreffender `choose`-Zweig mit Schloss → CompositeRisk HIGH.
  7. Szene und Lichtgruppe mit nicht freigegebenem Mitglied → DENY.
  8. Skript mit `alarm_control_panel` → CRITICAL, Nicht-Admin → DENY.
  9. Template-Ziel → DENY; mit Option `confirm` → Bestätigung.
  10. Skript zwischen Vorschau und „Ja“ geändert → neuer Inhalt wird geprüft.
  11. Skript nur mit `notify`, `delay` und freigegebenen Lichtern → ALLOW wie bisher.
  12. Agent-, Proaktiv- und Daueranweisungspfad können nicht umgehen.
  13. Von HomeIntent angelegte Automation mit Skript-Aktion → Prüfung beim Anlegen.
  14. Abgeleitete Routine → immer Bestätigung ohne Bindung.
- **Regressionsszenarien „Schlafen“** (live und als Unit-Test mit nachgebautem Haus
  des Nutzers: Skripte „Schlafen“ und „Gute Nacht“, `button.press` auf die Etage, nicht
  freigegebener Saugroboter und Brandmelder):
  - „Aktiviere Schlafen.“ → genau `script.schlafen`, EffectGraph geprüft, nur dessen
    Wirkung.
  - „Ich gehe schlafen.“, „Gute Nacht.“, „Starte die Schlafroutine.“ und „Mach alles
    für die Nacht fertig.“ → Rückfrage „Welche Routine meinst du: Schlafen, Gute
    Nacht?“ bzw. nach Bindung genau die gebundene Routine. „Gute Nacht“ wird danach
    **abgelehnt**, mit Nennung von Saugroboter und Brandmelder.
  - Keine Szene und kein Skript wird allein wegen Wortähnlichkeit ohne Bestätigung
    gestartet.
  - Keine zusätzliche Gattung entsteht durch Zielverbreiterung.
- **Live-Prüfung im Testbett (selbst) und später in der Test-Session:** „Nachtruhe“-Nachweis und Etagen-Button-Fall;
  alle bisherigen Funktionsszenarien grün.

---

## Phase 2 – ExecutionContext und ExecutionTrace (7.3.2)

### 2.1 ExecutionContext über den HA-Kontext (keine Heuristik, wo HA belegt)

Home Assistant besitzt bereits eine echte Kausalkette: Jeder Dienstaufruf trägt einen
`Context(id, user_id, parent_id)`. Skripte laufen im Kontext des Aufrufers, und
Automationen, die durch eine Zustandsänderung ausgelöst werden, erhalten diesen
Kontext als `parent_id`. HomeIntent nutzt das heute nicht (S4).

- Pro Nutzeräußerung, die zu einer Ausführung führt, gibt es genau **eine**
  `execution_id`. Der Executor erzeugt `Context(user_id=<sprechender Nutzer oder None>)`,
  speichert `context.id` als `execution_id` und übergibt ihn an **jeden**
  `async_call(..., context=ctx)`. Das gilt auch für Undo, zeitversetzte Befehle (eigener
  Kind-Kontext mit `parent_id`), Agent und Proaktiv.
- Die `execution_id` läuft durch Plan, GoalRun, Experience, Audit und Trace.
- **Nebeneffekt, gewollt:** Mit `user_id` im Kontext prüft HA zusätzlich die
  Entitätsberechtigungen des Nutzers. Das ergänzt die HomeIntent-Policy, ersetzt sie
  aber nicht.
- Proaktive oder Agent-Aktionen ohne Nutzer bekommen einen Kontext ohne `user_id` und
  werden im Trace als solche markiert.

### 2.2 ExecutionTrace

Neues Modul `execution_trace.py`, nur lesend. Aus der `execution_id` wird die Kette
gebildet:
- HomeIntent-seitig: Äußerung (gekürzt bzw. gehasht nach Datenschutzoption),
  Bedeutung, Origin, Binding, Plan, EffectGraph, Policy-Entscheidung.
- HA-seitig: Zustandsänderungen und Logbuch-Einträge mit `context_id == execution_id`
  oder `context_parent_id` in der Kette, dazu Skript- und Automations-Traces
  (`trace`-Integration) mit passendem Kontext.

**Effektarten:** `DIRECT_EFFECT`, `SCRIPT_EFFECT`, `SCENE_EFFECT`,
`AUTOMATION_EFFECT`, `SECONDARY_EFFECT`, `EXTERNAL_EFFECT`, `UNKNOWN_CAUSE`.

**Belegstufen**, bewusst nur drei:
- **VERIFIED:** über die HA-Kontextkette nachgewiesen.
- **POSSIBLE:** nur zeitliche Nähe und Bezug im EffectGraph bzw. als möglicher
  Folgeeffekt.
- **UNKNOWN:** kein Bezug.

Eine Zwischenstufe „STRONG“ entfällt, weil sie Vermutungen wie Tatsachen aussehen
ließe. POSSIBLE wird in Antworten immer als Vermutung formuliert.

### 2.3 Sprachliche Nutzung

„Warum ist der Staubsauger angegangen?“ ist eine Situationsfrage (Kausalität) über den
Trace, z. B.: „Du hast um 22:13 ‚Aktiviere Schlafen‘ gesagt. Ich habe das Skript
Schlafen gestartet. Dessen Schritt ‚…‘ hat den Saugroboter gestartet.“ Oder:
„Automation X, ausgelöst durch …, hat …“.

Gibt es keinen Beleg, sagt HomeIntent: „Dafür finde ich keine Ursache, die ich belegen
kann; zeitgleich lief …“ (POSSIBLE). Es gibt keine erfundenen Kausalketten. Auch
Ursachen außerhalb von HomeIntent werden beantwortet, soweit HAs Kontext sie zeigt
(Nutzer X in der App, Automation Y).

### 2.4 Speicherung und Datenschutz

Nutze einen begrenzten Ringspeicher (Größe konfigurierbar, Standard 500 Ausführungen,
14 Tage) und die bestehende `audit_log`-Hash-Logik für Akteure. Der Trace verweist auf
HA-Daten, statt sie zu kopieren. Im Learning Center gibt es eine Ansicht „Was hat
HomeIntent ausgelöst?“.

### 2.5 Tests Phase 2

- Jeder `async_call` im Code übergibt `context` (statischer Test über den AST).
- Direkter Befehl ergibt DIRECT/VERIFIED.
- Skript ergibt SCRIPT_EFFECT/VERIFIED.
- Durch eine Zustandsänderung ausgelöste Automation ergibt AUTOMATION_EFFECT/VERIFIED
  über `parent_id`.
- Zeitgleicher, fremder Effekt ergibt höchstens POSSIBLE; eine Antwort ohne Beleg
  enthält keine Kausalbehauptung.
- Kontext mit `user_id` bei Nicht-Admin: HA-Berechtigungsfehler wird sauber gemeldet.
- **Live:** „Warum ist der Saugroboter angegangen?“ nach dem Nachtruhe-Szenario (mit
  absichtlich freigegebenem Saugroboter) liefert die korrekte Kette.

---

## Phase 3 – Routine-Bindings, Implicit Action Policy, nie raten (7.3.3)

### 3.1 Bindings-Speicher (eine Struktur für alles Gelernte)

Neuer lokaler Speicher `bindings` (HA-`Store`, versioniert, mit Migration). Dieselbe
Struktur nimmt in Phase 6 auch Aliasse, Standardauswahlen, Vorlieben und Makros auf;
das vorhandene `alias_learning` wird dorthin migriert.

```
Binding
  kind: ROUTINE | ALIAS | DEFAULT_CHOICE | PREFERENCE | MACRO
  key: (RoutineConcept | Wort | Gattung×Ort | Aktivität | Makroname)
  target: entity_id | Plan-Beschreibung
  scope: USER(user_id) | HOUSEHOLD
  created_by, created_at, confirmed: True, uses, last_used
```

**Routine-Bindings:**
- Die erste Nutzung eines Routine-Konzepts (sleep, leave, arrive, movie, read,
  morning …) ohne Bindung **schlägt** den Kandidaten vor bzw. fragt bei mehreren
  nach: „Meinst du mit Schlafengehen das Skript Gute Nacht?“
- Nach „Ja“ wird die Bindung gespeichert. Danach wird für dieses Konzept **nicht mehr
  über Namensähnlichkeit gesucht.** Die Teilstring-Suche (`_routine_candidates`) dient
  nur noch der Entdeckung.
- Existiert das gebundene Ziel nicht mehr oder ist es nicht mehr freigegeben, führt
  HomeIntent nichts aus, sagt das und bietet eine neue Bindung an.
- Bindings sind im Learning Center sichtbar, änderbar und löschbar und per Sprache
  steuerbar („Vergiss die Schlafroutine“, „Schlafen ist ab jetzt das Skript X“).
- Eine Bindung entsteht nie heimlich und nie aus Konfidenz.
- Die Bindung ersetzt **nicht** den EffectGraph: Jede Ausführung prüft erneut.

### 3.2 Implicit Action Policy

Jeder Plan trägt `origin` ∈ {`EXPLICIT_COMMAND`, `IMPLICIT_NEED`, `INFERRED_ROUTINE`,
`PROACTIVE_PROPOSAL`, `STANDING_PERMISSION`}. Das Feld wird vom Sprachverständnis
gesetzt (`pragmatic_disposition`/`NeedMeaning`/Routinekonzept) und **nur** in
`ExecutionPolicy`/`AutoExecutionPolicy` ausgewertet.

Neue Option `implicit_action_level` (im Learning Center bedienbar):

| Stufe | IMPLICIT_NEED | INFERRED_ROUTINE |
| --- | --- | --- |
| `understand_only` | Antwort, keine Aktion | Antwort, keine Aktion |
| `propose` (**Standard**) | Vorschlag + Bestätigung | Vorschlag + Bestätigung |
| `low_risk_auto` | LOW direkt ausführen, sonst Vorschlag | Vorschlag + Bestätigung |
| `bound_routines_auto` | wie `low_risk_auto` | nur mit bestätigter Bindung **und** CompositeRisk ≤ Bestätigungsschwelle direkt, sonst Bestätigung |

**Feste Regeln:**
- `origin != EXPLICIT_COMMAND` ist nie weniger streng als derselbe explizite Befehl.
- NEVER_AUTO gilt unverändert.
- „Ich fahre jetzt“ und „Ich gehe“: nie automatisch schließen, verriegeln oder Alarm
  scharf schalten ohne bestätigte Bindung, Policy-Erlaubnis und die
  Bestätigungsregeln des Ziels.
- Absichtserklärungen ohne passende Bindung → freundliche Antwort, ggf. Vorschlag.

### 3.3 Nie raten

- **„hier“/„da“** nur aus: Bereich des Satelliten (`device_id`), ausdrücklichem Ort im
  laufenden Diskurs oder eindeutiger, aktueller Anwesenheit (falls aktiviert). Sonst
  fragt HomeIntent „In welchem Raum?“. Der Ort wird in der Antwort immer genannt (S6).
- **Einmalig vs. wiederkehrend:**
  - Uhrzeit oder Ereignis ohne Wiederholungsmarker → einmaliger geplanter Befehl.
  - Mit Marker („jeden“, „immer“, „täglich“, „werktags“) → Automation mit Vorschau.
  - Typisch wiederkehrende Ereignisse (Sonne, Dunkelheit) ohne Marker → nachfragen:
    „Nur heute oder jeden Tag?“ (Q5).
- **Ehrliche Fehlertexte:** Die tatsächliche Ursache aus Validator und Policy wird
  genannt, z. B. „Das Flurlicht lässt sich nur ein- und ausschalten.“ statt „nicht
  eindeutig unterstützt“. Kein falsches „unterstützt die Aktion nicht“.
- **„Lass X so, wie es ist“:** nichts tun und das bestätigen, ohne „nicht verstanden“
  für Rest-Phrasen.

### 3.4 Tests Phase 3

- Binding-Lebenszyklus: Vorschlag, Bestätigung, Nutzung ohne erneute Namenssuche,
  Löschen, Ziel entfernt oder nicht mehr freigegeben, Scope Nutzer vs. Haushalt.
- Matrix `origin` × `implicit_action_level` × Risiko; NEVER_AUTO bleibt vorrangig.
- „hier“ ohne Satellit fragt nach, mit Satellit wirkt es im richtigen Raum.
- Einmalig, wiederkehrend und Rückfrage bei Sonne/Dunkelheit.
- Fehlertexte gegen die tatsächliche Ursache (Golden-Tests).

---

## Phase 4 – Property-Based Safety Suite und Shadow-Infrastruktur (7.3.4)

Diese Phase liegt **vor** den großen Umbauten (Phasen 5–8), weil sie deren
Sicherheitsnetz ist.

### 4.1 Property-Based Safety Invariants

Bestehende Tests bleiben. Neu ist eine generative Suite (`hypothesis` als neue
Testabhängigkeit in `requirements-dev`, nicht zur Laufzeit) über einem synthetischen Haus. Die Varianten entstehen aus
Wortstellung, Höflichkeit, Synonymen, Artikeln, Singular/Plural, Orten, Gattungen,
Pronomen, Negation, Modalität, Zeit, Nebensätzen und Selbstkorrekturen, zusammengesetzt
aus den **Bausteinen des Lexikons**, nicht aus festen Sätzen.

Invarianten (jede als eigener Test; „Write“ = irgendein schreibender Dienstaufruf):

| Eingabe | Garantie |
| --- | --- |
| Negation | nie Write |
| Frage | nie Write |
| Vergangenheitsaussage | keine aktuelle Geräteänderung |
| Kontrafaktisch/hypothetisch | nie Write |
| Unbekanntes Zielwort | Zielmenge wird nie größer als ohne das Wort |
| Spezifischere Gattung | nie Wechsel in eine andere Gattung |
| Unbekannte Ausnahme | keine Teilausführung |
| Teilweise verstandener Mehrfachsatz | keine Teilausführung |
| Zeitgebundener Befehl | nie sofortige Ausführung |
| Singular + mehrere passende Ziele | Rückfrage |
| INFERRED_ROUTINE / IMPLICIT_NEED | nie weniger streng als EXPLICIT |
| Skript/Szene/Gruppe | CompositeRisk ≥ max. transitive Wirkung |
| EffectGraph unvollständig | nie LOW |
| Mehrdeutige ausführbare Interpretationen | keine Ausführung |
| Nicht freigegebenes wirksames Ziel | nie Write |
| Gelerntes Binding/Alias | nie auf nicht freigegebenes Ziel, nie Policy-Umgehung |

Die Suite läuft in CI mit fester Seed-Menge und nightly mit wechselnden Seeds. Gefundene
Gegenbeispiele werden als feste Regressionstests übernommen.

### 4.2 Shadow-Infrastruktur verallgemeinern

`nlu/understanding.py::ShadowComparison`/`compare_outcomes` wird zu einem allgemeinen
Werkzeug erweitert, statt ein zweites zu bauen:
- **Vergleichsfelder:** SpeechAct, Operation, Gattung, Zielentitäten, Ort, Menge,
  Origin, Risiko, Bestätigungspflicht, ServiceCallPlan.
- **Drift-Klassen:**
  - `EQUIVALENT`
  - `REFINEMENT`: gleiche Wirkung, genauere Begründung
  - `BEHAVIOR_CHANGE`: andere Wirkung im selben Gattungsraum
  - `SAFETY_DRIFT`: Gattungs- oder Domänenwechsel, größere Zielmenge, niedrigeres
    Risiko, weggefallene Bestätigung, Write statt Non-Write
- **Betriebsarten:**
  1. Offline über alle Korpora und Tests (CI-Report).
  2. Optional **live im echten Haus** (`shadow_mode: off | log`, Standard `off`).
     Ausgeführt wird immer nur das Ergebnis der aktiven Pipeline; der Kandidat hat
     keinerlei Schreibrecht und wird nur protokolliert.
- **Protokoll:** Satz-Hash, beide Ergebnisse, Drift-Klasse. Es landet in den
  Diagnosedaten, im Learning Center und im Test-Report. SAFETY_DRIFT blockiert jedes
  Umschalten.

### 4.3 Tests Phase 4

Die Suite selbst; ein absichtlich fehlerhafter Kandidat (Licht → Saugroboter) wird als
SAFETY_DRIFT erkannt; im Shadow-Modus schreibt der Kandidat nachweislich nicht.

---

## Phase 5 – Eine Zielauflösung (7.4.0)

Heute: `nlu/target_resolution.py` (neu, 8 Nutzer) und `nlu/entity_resolution.py` (alt,
24 Nutzer), dazu private Suchen in `entity_scope.py`, `automation_target_resolver.py`,
`query_target.py` und bei Push-Zielen.

1. Die neue Auflösung läuft im Shadow-Modus (Phase 4) bei jedem Aufruf der alten mit.
2. Abweichungen klassifizieren und die neue Auflösung verbessern, bis „alt besser“
   und SAFETY_DRIFT leer sind.
3. Pro Aufrufer umschalten; zuletzt die alte Auflösung und die privaten Suchen
   entfernen.
4. **Regeln der einen Auflösung:**
   - nur freigegebene Entitäten,
   - Korrektur nie über Klassengrenzen,
   - mehrdeutig → nummerierte Rückfrage,
   - Plural oder „alle“ → Menge mit Vorschau ab der Zielgrenze,
   - Etagen, Komposita und Gattungen überall gleich,
   - Bindings (Alias, Standardauswahl) werden hier und nur hier angewandt.

Tests: Shadow-Report ohne SAFETY_DRIFT, alle Korpora mindestens gleich gut.

---

## Phase 6 – Selbstlernen aus dem Dialog (7.4.1)

Das ist der wichtigste Hebel für „versteht wie ein LLM“ ohne LLM: HomeIntent lernt die
Sprache **dieses Haushalts**, und zwar deterministisch, nur mit Bestätigung und nur als
Bedeutungsbaustein. Alles landet im `bindings`-Speicher aus Phase 3.

**Live-Befund 7.3.0:**
- Alias „Funzel = Flurlicht“ wirkt in Befehl, Frage und Kurzform, auch für andere
  Nutzer. Das ist das richtige Prinzip.
- Unbekannte Wörter werden nicht erfragt.
- Antworten auf Rückfragen und Korrekturen werden nicht gelernt.
- „Merk dir …“ wird gespeichert, aber nie benutzt, und „Was weißt du über mich?“ findet
  nichts.
- Nach „Gedächtnis ist deaktiviert“ führt „Ja“ zu „nicht verstanden“.
- In „Was hast du gelernt?“ steht das englische Wort „preference“.

**Soll:**
1. **Unbekanntes Geräte-Nomen:** Frage „Was meinst du mit ‚X‘?“ mit Optionen aus Ort und
   Aktion; danach ausführen und anbieten, das Wort als Alias zu speichern.
2. **Rückfragen:** Wird dieselbe Mehrdeutigkeit (Gattung × Ort × Sprecher) zweimal
   gleich aufgelöst, fragt HomeIntent einmal, ob die Wahl künftig als Standardauswahl
   gelten soll.
3. **Korrekturen:** gleiche Logik für „Nein, nur X“ und „Ich meinte X“.
4. **Vorlieben werden benutzt:**
   - „Ich lese jetzt“, „Ich will lesen“ → gemerkte Einstellung, beim ersten Mal mit
     Vorschau.
   - „Was weißt du über mich?“ und „Wie hell möchte ich …?“ werden aus dem Gedächtnis
     beantwortet.
   - „Vergiss …“ löscht gezielt.
5. **Sprachmakros:** „Wenn ich ‚Kinoabend‘ sage, dann …“ wird ein bestätigter Makro-Plan
   (keine HA-Automation). Jeder Aufruf läuft durch Validator, EffectGraph und Policy.
6. **Gelerntes ist Lexikon, nicht Satz:** Jedes Binding wirkt in allen Satzformen,
   Zeiten, Fragen, Push-Sätzen und Mehrfachbefehlen. `alias_learning.py` verliert seine
   eigenen Muster.
7. **Sicherheit beim Lernen:**
   - Lernen nur nach „Ja“.
   - Nur freigegebene Ziele; wird ein Ziel entzogen, ist das Binding wirkungslos und
     markiert.
   - Vorhandene Geräte- oder Gattungsnamen dürfen nicht überschrieben werden.
   - Aliasse für kritische Ziele nur durch Admins.
   - Geltungsbereich wählbar; Standardauswahlen und Vorlieben gelten standardmäßig pro
     Person.
8. **Kleinigkeiten:** deutsche Bezeichnungen; nach „Gedächtnis deaktiviert“ erklären,
   wo man es einschaltet.

**Langzeit-Lernen (V11/V12)** bleibt, wie es ist. Neu ist ein Zeitraffer-Test im
Testbett: Zwei Wochen Nutzung werden simuliert, und geprüft wird, dass Gewohnheiten nur
vorgeschlagen werden, nie eine Automation entsteht und abgelehnte Vorschläge nicht
wiederkommen. Dafür wird bei Bedarf eine testbare Zeitquelle ergänzt.

**Tests:** Ein gelerntes Wort wirkt in mindestens 10 verschiedenen Satzformen, ohne
dass eine davon eigens programmiert wurde. Die Invarianten aus Phase 4 gelten auch für
gelernte Einträge.

---

## Phase 7 – Gemeinsame Bedeutungsebene und Arbitration (7.5.0)

### 7.1 Semantic IR: Erweiterung statt Neubau

`SemanticUtterance`/`MeaningClause` ist bereits die gemeinsame, HA-freie
Bedeutungsebene (SpeechAct, Modality, Polarity, Klauseln). Sie wird schrittweise
erweitert:
- `operation`
- `target` (Gattung, Ort, Menge, Merkmal, Referenz)
- `value`
- `time` (jetzt, einmalig, wiederkehrend)
- `conditions`
- `exceptions`
- `origin`
- `residue` (unerklärter Rest)
- `evidence`

`SemanticFrame`, `CommandPlan`, `AutomationModel`, `NeedOutcome`, `Goal` usw. bleiben
die domänenspezifischen Modelle, **abgeleitet** aus dieser Ebene.

Die IR ruft nie Dienste auf. Es entsteht kein zusätzlicher Typ `SemanticIntent`
daneben.

### 7.2 Arbitration

Die first-match-Kaskade in `conversation.py` (S7) wird schrittweise ersetzt:
1. Jeder Interpreter liefert einen **Kandidaten** mit:
   - Quelle, SpeechAct, IR-Bedeutung, gegroundeten Zielen,
   - Autorität (Parser vs. gelerntes Binding vs. Diskurs),
   - Wirkung (Write/Read/None), Risiko, `residue`, Evidenz.
2. **Regeln:**
   - Genau ein ausführbarer Kandidat ohne Rest → dieser.
   - Mehrere ausführbare Kandidaten mit **gleicher** Wirkung → zusammenführen.
   - Mehrere mit **widersprüchlicher** Wirkung → Evidenz entscheidet, wenn sie
     eindeutig ist (ausdrückliches Gerät schlägt Bedürfnis; ausdrückliche Frage schlägt
     Befehl). Sonst Rückfrage oder nichts tun.
   - **Mehrdeutige ausführbare Bedeutung → nie ausführen.**
   - Nur-Lesen-Kandidaten dürfen parallel beantwortet werden, wenn kein ausführbarer
     Kandidat existiert.
3. **Migration:** Der Arbiter läuft zuerst im Shadow-Modus gegen die Kaskade. Danach
   wird Handler für Handler umgestellt, beginnend mit denen, die heute am häufigsten
   kollidieren (Need ↔ Query, Automation ↔ zeitversetzter Befehl, Routine ↔
   Szenenname).

**Tests:** Kollisionskorpus, Shadow-Report ohne SAFETY_DRIFT, Invarianten aus Phase 4.

---

## Phase 8 – Sprachinseln Bereich für Bereich (7.5.x)

Reihenfolge: Verlauf → Automationsverwaltung → Listen/Timer → Kalender →
Haushaltsfragen → Ziele. Pro Bereich:
1. kanonischen Frame definieren,
2. ihn aus der IR ableiten,
3. Shadow-Vergleich,
4. umschalten,
5. alten Parser löschen.

**Regex-Ziel ist nicht „0 Regex“.** Alle Regex werden einmal klassifiziert:
`LEXICAL`, `MORPHOLOGICAL`, `STRUCTURAL`, `SEMANTIC_SENTENCE_PATTERN`. Die ersten drei
dürfen bleiben (Tokenisierung, Normalisierung, Morphologie, Oberflächenformen). Nur
`SEMANTIC_SENTENCE_PATTERN` wird abgebaut und pro Release gezählt. Die Klassifikation
liegt als Datei im Repo und wird durch einen Test aktuell gehalten.

---

## Phase 9 – Generalisierung: Bedeutung statt Sätze (7.6.0)

Die Lücken aus dem unveröffentlichten Korpus, als **Bedeutungsklassen**; die geheimen
Testsätze bekommst du bewusst nicht:
1. **Verbklassen statt Verblisten:** Einschalten (an, anwerfen, anknipsen, los, starten
   …), Richtung und Stufe (rauf, runter, höher, wärmer, lauter), Mengen („zwei Grad“,
   „ein bisschen“). Verblose Kurzbefehle der Form „<Gattung> [<Ort>] [<Menge>]
   <Partikel>“.
2. **Bedürfnis- und Beschwerde-Ontologie als Daten:** dunkel/Lesen schwierig → Licht;
   grelle Sonne → beschatten; beschlagen/feucht/stickig → lüften; Gerät stört/zu laut
   → leiser/aus (Rückfrage); nicht mehr gebraucht → aus. Wirkung abhängig von
   `implicit_action_level`.
3. **Situationsfragen paraphraseninvariant:** Existenz und Allquantor über Gattung ×
   Ort × Zustand (mit Polarität), Extremwerte (Innenräume, außer „draußen“ ist
   gemeint), Anwesenheit je Ort/Etage, Bedarf (Schwellen), Gerätezustand/Problem
   (Batterie, Verfügbarkeit, letzter Alarm), Ursache (Sollwert, Heizbetrieb, offenes
   Fenster, **Trace aus Phase 2**).
4. **Diskurs:** Ellipsen mit Modifikator („… und die hintere auch“), „Und im <Ort>?“
   wiederholt die letzte Frage, „Vergiss es“/„Lieber nicht“ bricht ab oder macht
   rückgängig (Antwort sagt, welches), „da“ bindet an den Ort der letzten Frage.
5. **Höflichkeit und Abschwächung** („Könntest du vielleicht irgendwann mal …“) =
   Befehl, keine Zustandsfrage.
6. **Mehrfachbefehle:** „dort“ bindet an den Ort des vorigen Teils. Kein Teil fällt
   still weg; Teilausführung ist verboten (Invariante).

---

## Querschnitt: Messung und Testbett (laufend)

- **Kanonische Bedeutung messen:** Der Entwicklungskorpus prüft den erzeugten Frame
  bzw. die IR gegen die erwartete Bedeutung. Paraphrasen müssen dieselbe Bedeutung
  liefern.
- `sim/nlu_probe.py` bleibt Abnahme und liefert keine Lexikoneinträge. Die Test-Session
  misst zusätzlich mit einem unveröffentlichten Korpus.
- Die README nennt auch den Wert auf ungesehenen Sätzen.
- **Keine Test-Automationen in `sim/config/automations.yaml` einchecken.** Die 16
  Einträge aus 7.3.0 werden entfernt.
- Ein Test-Reset für HomeIntent-Zustand (Timer, Listen, Bindings, Diskurs, Trace)
  macht Messreihen unabhängig.

## Zielwerte

| Kennzahl | 7.3.0 | Ziel |
| --- | --- | --- |
| Nicht freigegebene Geräte über Skript/Szene/Gruppe schaltbar | ja | **nein** |
| Skript/Szene mit unvollständigem EffectGraph als LOW | ja | **nie** |
| Dienstaufrufe mit HA-Kontext | 0 / 45 | **alle** |
| „Warum ist X angegangen?“ mit belegter Kette | nein | **ja (VERIFIED/POSSIBLE/UNKNOWN)** |
| Routinewahl über Namensähnlichkeit ohne Bestätigung | ja (Szenen) | **nie** |
| Geraten statt gefragt (Ort, Einmaligkeit) | vorhanden | **0** |
| Verletzte Sicherheitsinvarianten (Property-Suite) | – | **0** |
| SAFETY_DRIFT bei jedem Umschalten | – | **0** |
| Zielauflösungen im Code | 2 + private | **1** |
| `SEMANTIC_SENTENCE_PATTERN`-Regex | nicht gezählt | pro Release sinkend |
| Gelerntes Wort wirkt in verschiedenen Satzformen | 3 von 4 | **≥ 10** |
| Unveröffentlichter Korpus | 38 % | **≥ 65 %** |
| Veröffentlichte Korpora / Push / Funktionsszenarien | 85 % / 35/35 / 162/162 | nicht schlechter |
| Latenz p95 | < 100 ms | < 100 ms |

## Reihenfolge

| Phase | Version | Inhalt | abhängig von |
| --- | --- | --- | --- |
| 1 | 7.3.1 | EffectGraph, transitive Sicherheit, abgeleitete Routinen mit Bestätigung | – |
| 2 | 7.3.2 | ExecutionContext (HA-Kontext), ExecutionTrace, Warum-Fragen | 1 |
| 3 | 7.3.3 | Bindings-Speicher, Routine-Bindings, Implicit Action Policy, nie raten | 1 |
| 4 | 7.3.4 | Property-Based Safety Suite, Shadow-Infrastruktur | 1–3 |
| 5 | 7.4.0 | Eine Zielauflösung | 4 |
| 6 | 7.4.1 | Selbstlernen aus dem Dialog | 3, 5 |
| 7 | 7.5.0 | IR-Erweiterung und Arbitration | 4, 5 |
| 8 | 7.5.x | Sprachinseln je Bereich | 7 |
| 9 | 7.6.0 | Generalisierung | 7, 8 |

## Arbeitsweise für die Umsetzung in einer Session

1. **Phase für Phase in der Tabellenreihenfolge.** Vor Beginn einer Phase die
   Abhängigkeiten prüfen. Nach jeder Phase:
   - vollständige Testsuite und die neuen Tests der Phase grün,
   - Version in `manifest.json` hochzählen (7.3.1 … 7.6.0),
   - Commit(s) und Push auf den Arbeitsbranch,
   - Abschnitt in `docs/umsetzung-7.3.1-7.6.md`: was umgesetzt wurde, Shadow-Report
     (Anzahl und Drift-Klassen), Messwerte vorher/nachher, bewusst Offengelassenes.
2. **Harte Haltepunkte:** Weiter geht es nur, wenn
   - alle Tests grün sind,
   - die Property-Suite (ab Phase 4) keine verletzte Invariante zeigt,
   - kein Shadow-Report ein `SAFETY_DRIFT` enthält.

   Lässt sich eines davon nicht beheben, wird die betroffene Umschaltung **nicht**
   gemacht: Der neue Pfad bleibt im Shadow-Modus, der Grund wird im Bericht
   dokumentiert, und die unabhängigen Teile der folgenden Phasen werden trotzdem
   umgesetzt. Sicherheit geht vor Vollständigkeit.
3. **Selbst live prüfen:** Das Testbett liegt im Branch `claude/sleepy-meitner-xd7oux`
   unter `sim/` (siehe `sim/README.md`; echtes Home Assistant aus PyPI, simuliertes
   Haus, `runner.py`, `push_check.py`, `readme_check.py`, `nlu_probe.py`). Nach den
   Phasen 1, 3, 5 und 9 dort mindestens `runner.py` und die Schlafen-Regressionen
   laufen lassen. **Keine Änderungen an `sim/` committen** (Ausnahme: das Entfernen
   der 16 Test-Automationen aus `sim/config/automations.yaml`).
4. **Kein Abkürzen:** Schritte wie Shadow-Vergleich, Tests oder Bericht werden nicht
   übersprungen, um schneller fertig zu werden. Wird der Kontext knapp, den aktuellen
   Stand committen und im Bericht festhalten, an welcher Stelle weitergemacht wird.
5. **Abschluss:** Die README beschreibt Version 7.6.0 mit Release-Notes für jede
   Phase, ehrlichen Messwerten (auch auf ungesehenen Sätzen, soweit messbar) und den
   neuen Optionen (`effect_graph_unknown`, `implicit_action_level`, `shadow_mode`,
   Trace-Speicher). Einen Pull Request nur erstellen, wenn der Nutzer das verlangt.
