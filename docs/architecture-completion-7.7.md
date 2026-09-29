# Architekturabschluss 7.7

Stand: 7.7.0 auf `claude/homeintent-sprachverstaendnis-phases-6feab4`.
Grundlage: `docs/architektur-7.7-bestandsaufnahme.md` (B0, Stand 7.6.1).
Alles bleibt lokal und deterministisch; kein Sprachmodell, kein ML-Modell.

## 1. Die sechs Fragen – je eine Stelle

| Frage | Stelle | Absicherung |
| --- | --- | --- |
| Wo entsteht die Bedeutung? | `nlu/language_frontend.analyse_language` (Sprachdokument) → `NluEngine.understand` (Lesarten und Pläne); die gemeinsame Bedeutungsdarstellung ist `nlu/meaning_ir.ground_meaning` auf `nlu/clause_reading` | `test_architecture_77.py`: IR importiert keinen Compiler und keine privaten Namen |
| Wo wird das Ziel aufgelöst? | `nlu/target_resolution.resolve_phrase` | `test_one_target_resolution.py`: nur `entities.py`/`target_resolution.py` ranken Namen; der historische Resolver ist gelöscht |
| Wer entscheidet zwischen zwei Deutungen? | `arbitration.arbitrate` / `arbitrate_dialog` (Kandidaten und Dialogevidenz aus `arbitration_candidates`) | Arbiter-Shadow; Architekturtest: der Arbiter importiert weder Policy noch Executor |
| Wer autorisiert? | `execution_policy.evaluate_service_plan` (+ NEVER_AUTO), Bestätigung gebunden über `service_executor.ConfirmedScope` | Property-Suite; `test_effect_graph_77.py` |
| Wo wird geschaltet? | `service_executor.async_execute_service_plan` | `test_architecture_layers_77.py`: nur gelistete Module rufen `hass.services.async_call`, Gerätewrites nur hier |
| Warum wurde etwas ausgeführt? | `execution_trace.record_execution` / `explain_change` | Trace je Ausführung, Erklärung per Sprache |

**Ehrliche Einschränkung zur ersten Frage:** Die Meaning IR ist die gemeinsame
Darstellung für den Arbiter (Zeitbezug, Rest, Bedingungen), aber noch nicht der
einzige Erzeuger von Plänen. Die Compiler der Engine (`SemanticInterpreter`,
Gattungs-, Diskurs- und Registrierte-Operationen-Compiler) lesen dieselben
öffentlichen Primitive (`clause_reading`, `degree_semantics`, Namensindex),
erzeugen ihre Pläne aber selbst. „Die IR ist die einzige Quelle aller Pläne“
bleibt der nächste Schritt (Abschnitt 9).

## 2. Ausgangslage (7.6.1)

- `conversation.py` 8485 Zeilen, Router mit first-match-Kaskade in 15 Stufen;
  `engine.py` 5335 Zeilen mit dem alten Erster-Treffer-Matcher als
  „Shadow“-Pfad; `parsers.py` 2603 Zeilen hassil-Parser.
- Meaning IR griff auf private Teile des Gattungscompilers zu.
- Dialogzustand (offene Rückfragen) wurde vor dem Arbiter in eigenen
  Zweigen entschieden.
- Historischer Resolver als In-Code-Vergleich, Legacy/V7-Report in CI.
- 212 Satzmuster (SEMANTIC_SENTENCE_PATTERN).

## 3. Wellen

### A (7.6.1)
Acht Befunde des Nachtests 7.6.0, je ein Commit mit Test (A1–A8), Version
7.6.1, CI grün.

### B0 Bestandsaufnahme
`docs/architektur-7.7-bestandsaufnahme.md`: Verantwortungskarten, Kaskade,
IR-Abhängigkeiten, Regex-Hotspots, CI-Lücken, Wellenplan.

### B1 Release-Basis
Vollständiger Live-Lauf inkl. Proaktiv per `workflow_dispatch`
(`nightly-live.yml`) auf dem B0-Stand: grün. Baseline für die
Vergleichswerkzeuge in `/tmp`-Worktrees (7.6.1, B0).

### B2 Meaning IR ohne Legacy-Interna
- Neu/öffentlich: `nlu/clause_reading.py` (`ClauseMeaning`, `read_clauses`,
  `read_operation`), absolute Werte in `nlu/degree_semantics.py`,
  `name_index`, `compile_clauses`.
- Alt: IR importierte `_read_clauses` u. a. aus `ontology_compiler`.
- Shadow: `scripts/corpus_shadow.py` (Engine- und IR-Signatur je Satz, alter
  Stand per Worktree) – 3341 Sätze, 0 Abweichungen.
- Test: `test_architecture_77.py`.

### B3 Arbitration autoritativ, Dialogzustand als Evidenz
- `arbitration.DialogEvidence`, `arbitrate_dialog`: neuer Satz beendet
  Sicherheitsfrage, vollständiger Befehl ersetzt offenen Dialog, Ja/Nein,
  Fortsetzung – eine Entscheidung statt drei Kaskadenzweige.
- Bedürfnis/Routine, Situationsfrage, Direktbefehl, Diskurs, Freigabe und die
  fünf Kontext-Anschlüsse sind Kandidaten desselben Arbiter-Schritts;
  Autorität PARSER > BINDING > NEED > DISCOURSE; zeitgebundene/bedingte
  Bedeutung ist DEFER (`meaning_ir.is_deferred`).
- Shadow: `scripts/dialog_shadow.py` (226 Dialoge, 441 Turns, eingefrorene
  Zeit) 0 Abweichungen; Arbiter-Shadow 2045 gleichwertig; 0 SAFETY_DRIFT.
- Sicherheit: Der Arbiter verwaltet keine Dialoge und kann keine
  Bestätigung senken (seit B9 Property-Invariante).

### B7 EffectGraph-Härtung (vor B4 gezogen)
- **Sicherheitsbefund:** Ein „Ja“ zu einem Licht-Skript führte nach einer
  Änderung des Skripts auf `lock.unlock` die Entriegelung aus. Jetzt trägt
  jede offene Bestätigung `ConfirmedScope` (Risiko, wirksame Ziele); der
  Executor verweigert bei höherem Risiko oder neuem Ziel.
- Dienstdaten in beliebiger Tiefe, `entities` nur bei `scene.apply`,
  `scene.create` → spätere Aktivierung nutzt die erzeugten Zustände.
- Tests: `test_effect_graph_77.py` (Kern und echte Konversation).

### B4 Zerlegung
- `conversation.py` 8485 → 2195 Zeilen (Router, Verdrahtung, Trace).
- `controllers/`: devices 1074, queries 167, goals 1420, routines 287,
  comfort 603, learning 897, notifications 171, automations 845,
  automation_management 1148, productivity 1293, replies 80 (alle < 1500).
- Abhängigkeiten ausdrücklich (Getter für `hass`/Weltmodell/Entitäten,
  Protokoll für Laufzeitdienste) statt `self._runtime_data` überall.
- `management_dialogs.py` aufgelöst.
- Test: `test_architecture_layers_77.py` (Importrichtung, erlaubte
  Dienstaufrufer, Größen).
- Shadow: Dialog 0/441 nach jedem Teilschritt.

### B5 Alte Pfade gelöscht
- Engine: `_legacy_shadow_match`, `_match_multi`,
  `compare_understanding_pipelines`, Schatten-Parser (5335 → 4744 Zeilen).
- `parsers.py` 2603 → 325 Zeilen (nur noch Automations-Parser-Helfer);
  19 hassil-Grammatikdateien der alten Parser gelöscht.
- `resolve_entity_scored` samt `compare_resolutions`,
  `scripts/resolver_shadow.py` und Sitzungshaken der Tests gelöscht.
- Legacy/V7-Report und 34 `v7-shadow-baseline-*.json` ersetzt durch
  `docs/perf/corpus-signatures-7.7.0.json` (Digest je Korpussatz) und
  `corpus_shadow.py --check` in CI.
- Die einzige verbliebene Aufgabe einer alten Grammatik (Erklärung für
  Gruppen-Prozentbefehle ohne Ziel) liegt grammatikfrei in
  `nlu/group_feedback.py`.
- Vergleich gegen den Stand vor B5: 3341 Sätze 0 Abweichungen, Dialoge 0/441.

### B6 Satzmuster
212 → 173, ohne Umklassifizierung (jede Zählung sinkt durch gelöschten oder
zusammengeführten Code):
- tote Muster nach B5 (fünf Engine-Konstanten, `nlu/group_semantics.py`),
- Lokativ „im/in der/in dem/am/beim“ aus zehn Kopien → `nlu/locative.py`,
- Dialog-Metafragen aus drei Controllern → `nlu/dialog_meta.py`,
- Routine-Rückmeldung (5 Muster) → Bezug + Aussagelexikon; 3648 erzeugte
  Varianten gleich dem alten Stand,
- Fähigkeitsfragen (6) → bestehendes Phrasenlexikon `nlu/phrases.py`,
- geteilte Duplikate („ich meinte“, „wie hoch ist“).
Test: `test_regex_reduction_77.py`. Korpus/Dialoge/Arbiter unverändert.

### B8 Entwicklungs-Benchmark und STT
Siehe `docs/perf/dev-benchmark-7.7.md`. 503 Fälle, 18 Kategorien, 113
zurückgehalten. Held-out erster Lauf (Code unverändert) 88/113 mit zwei
unsicheren Ausführungen; nach der Welle 97/113, gesamt 442/503,
`unsafe_execution_count` 0. Behoben: STT-Komposita (`nlu/stt_repair.py`),
still weggelassene Wiederholungszahl. **Entwicklungswerkzeug, kein
unabhängiger Nachweis.**

### B9 Sicherheit und Release-Gate
Neue Invarianten (Property-Suite 19 → 24, Nightly-Profil ohne Verletzung):
STT-Variante nie unsicherer als getippt; Selbstkorrektur führt den
zurückgenommenen Teil nie aus; offener Dialog senkt nie die
Bestätigungspflicht; gelernte Standardauswahl umgeht keine Bestätigung;
V11 lernt nie Satzbedeutungen. Statischer Gate-Test
`test_release_gate_77.py`.

## 4. Release-Gate 7.7

| Gate | Anforderung | Ergebnis |
| --- | --- | --- |
| `unsafe_execution_count` | 0 | 0 (503 Fälle) |
| SAFETY_DRIFT je Umschaltung | 0 | 0 (Korpus, Dialoge, Arbiter, Shadow-Vergleich) |
| Property-Suite | 0 Verletzungen | 24/24, Nightly-Profil grün |
| Pyright voll und strict | 0 | 0 |
| HACS, hassfest, echte HA-Tests, Live-Testbett | grün | in CI; vollständiger Live-Lauf inkl. Proaktiv für den Release-Commit per `workflow_dispatch` angestoßen |
| Latenz p95 bei 5000 Entitäten | < 100 ms, nicht schlechter als 7.6 | `understand` alle Formen < 100 ms; Aufrufzählung gleich 7.6.1 (3.519.981 / 3.521.958) |
| Satzmuster | < 180 | 173 |
| Entwicklungs-Benchmark | ≥ 500 | 503 |
| Unabhängiger Nachtest | nicht schlechter als 7.6.0 | **offen** – macht die Test-Session |

Zeitmessungen in dieser Umgebung schwanken um bis zu 40 %; für den Vergleich
mit 7.6.1 wurde deshalb zusätzlich die deterministische Aufrufzählung je
Benchmark-Form verwendet. Ein ganzer Turn durch die Conversation bei 5000
Entitäten liegt unverändert bei p50 ≈ 370 ms, p95 ≈ 630 ms (7.6.1 gleich) –
bestehende Schuld, siehe Abschnitt 9.

## 5. Meaning IR und Arbitration vorher/nachher

| | 7.6.1 | 7.7 |
| --- | --- | --- |
| IR-Quelle | private Compiler-Teile | öffentliche Primitive (`clause_reading`, `degree_semantics`) |
| Dialogzustand | eigene Kaskadenzweige vor dem Arbiter | typisierte Evidenz im Arbiter |
| Kontext-Anschlüsse | first-match in fester Reihenfolge | Kandidaten; widersprechende Schreib-Lesarten → Rückfrage |
| Zeitbezug | je Pfad | eine Regel (`is_deferred`) |
| Bestätigung | pauschal für den Plan | gebunden an Risiko und Ziele der Vorschau |

## 6. Gelöschte Legacy-Pfade
`_legacy_shadow_match`, `_match_multi`, `_match_coordinated_named_targets`
(Legacy-Teil), `compare_understanding_pipelines`, Schatten-Parser und ihre
Grammatiken (`intents/de/{quantifiers,percentage,light_extended,fan_extended,
climate_extended,context_followup,reference,comparison_query,temporal,
area_query,query_followup,state_query,command_followup}`, `cover.yaml`,
`light_switch.yaml`, `query.yaml`, `script.yaml`), `resolve_entity_scored`,
`compare_resolutions`, `scripts/resolver_shadow.py`,
`scripts/v7_shadow_report.py`, alle `v7-shadow-baseline-*.json`,
`management_dialogs.py`, `nlu/group_semantics.py`. Insgesamt seit 7.6.1:
−12015/+9613 Zeilen im Integrationscode, dazu rund 25 000 Zeilen
Baselines/Grammatiken/Tests.

## 7. Regex-Verlauf
SEMANTIC_SENTENCE_PATTERN: 7.5.0 272 → 7.5.1 258 → 7.5.2 212 → 7.6.0 212 →
**7.7.0 173** (`docs/regex-klassifikation.json`, Test hält das Maximum).

## 8. CI
Neu bzw. geändert: Korpus-Signaturen gegen Baseline (statt V8-Report),
Entwicklungs-Benchmark `--check`, Arbiter-Shadow zeichnet die Policy-Übergabe
in den Controllern auf, Architekturtests, Release-Gate-Test. Entfernt:
Resolver-Shadow. Ein zeitabhängiger E2E-Test (Garage) wartet jetzt auf den
Zustand statt auf eine feste Zeit.

## 9. Verbleibende Schulden und offene Sprachlücken
- **IR als einzige Planquelle:** Die Engine-Compiler erzeugen Pläne noch
  selbst; die IR ist Arbiter-Evidenz, nicht der alleinige Planerzeuger.
- **`engine.py` 4744 Zeilen:** Nach B5 kleiner, aber noch kein Modul pro
  Compiler; die Zerlegung analog zu `controllers/` steht aus.
- **Latenz eines ganzen Turns** bei 5000 Entitäten (p50 ≈ 370 ms) ist
  unverändert hoch; `understand` selbst ist < 100 ms, die Kosten liegen im
  Router (Weltmodell, Weltkontext je Turn).
- **Arbiter-Shadow:** 7 Sätze nicht messbar (Kalenderlesungen im Stub).
- **Sprachlücken** (aus dem Benchmark, bewusst nicht mit Einzelregeln
  beantwortet): höfliche Nachsätze („…, danke“, „Sei bitte so nett und …“),
  Automationen mit Präsenzende/Abwesenheitsdauer/Alarm-/Leistungsauslösern,
  Ellipsen mit neuem Ort und Wert („Und im Wohnzimmer auf 22“),
  „Kannst du bitte die Haustür abschließen?“ (nennt den Kontakt statt des
  Schlosses), Mehrfachbefehle mit kritischem Teil werden abgelehnt statt
  rückgefragt.
- **Unabhängiger Nachtest** für 7.7 steht aus.
