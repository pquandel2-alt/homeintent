# HomeIntent 7.7 – Bestandsaufnahme (B0)

Stand: 7.6.1 (`b1e89da`, CI grün). Grundlage für die Wellen B1–B9. Alle
Zahlen stammen aus dem Code dieses Stands (AST-Auswertung, `grep`,
`scripts/regex_inventory.py`), nicht aus älteren Berichten.

## 1. Verantwortungskarte `conversation.py`

8485 Zeilen. Eine Klasse `NluConversationEntity` (7403 Zeilen, 76 Methoden,
Basisklassen `DialogLearningMixin` aus `conversation_learning.py`,
`ConversationEntity`, `AbstractConversationAgent`) und 26 Modulfunktionen
(449 Zeilen).

### Fachliche Bereiche und ihre Methoden (Zeilen)

| Bereich | Methoden (Auswahl, Größe) | Summe ca. |
| --- | --- | --- |
| **Turn-Einstieg / Router** | `_async_handle_message` (49), `_async_handle_message_inner` (**1566**) | 1615 |
| **Geräte / direkte Befehle** | `_async_handle_match_result` (199), `_async_handle_command_plan` (178), `_async_handle_device_control_result` (131), `_async_handle_service_confirmation_reply` (99), `_async_handle_pending_semantic_command` (64), `_async_handle_no_match` (111), `_async_clarify`, `_async_handle_bound_result` | ~850 |
| **Automationen** | `_async_handle_automation_management` (325), `_async_handle_automation_confirmation_reply` (171), `_async_handle_automation_management_confirmation` (156), `_async_handle_automation_wizard` (154), `_async_handle_structure_edit_request` (62), `_async_handle_action_edit_request` (56), `_async_handle_automation_deletion_confirmation_reply` (50), `_async_handle_pending_automation_draft` (48), `_handle_automation_match_result` (45), `_async_handle_automation_toggle_result` (34), `_decide_recurrence` (52), `_handle_recurrence_choice` (35), Anlegen (~120) | ~1350 |
| **Ziele / Proaktiv / Komfort** | `_async_handle_goal_turn` (**930**), `_async_handle_comfort_turn` (286), `_async_handle_procedure_turn` (135), `_async_report_background_plan` (44) | ~1400 |
| **Lernen / Gedächtnis** | `_async_handle_learning_turn` (291), `_async_handle_memory_turn` (278), `_async_handle_routine_feedback_turn` (58), `_async_apply_confirmed_preferences` (44), Mixin `conversation_learning.py` (522) | ~670 (+522) |
| **Routinen** | `_async_handle_routine_binding_task` (98), `_async_handle_routine_binding_request` (70) | ~170 |
| **Produktivität / Kalender / Timer** | `_async_execute_todo` (157), `_async_handle_calendar_event_turn` (93), `_async_handle_native_timer_request` (82), `_async_execute_productivity` (69), `_async_handle_pending_timer_reply` (69), `_async_execute_timer` (66), `_async_handle_pending_productivity` (61), `_async_handle_productivity_request` (57) | ~650 |
| **Benachrichtigungen** | `_materialize_notification_recipients` (44), sofortige Push (`_async_handle_immediate_notification`) | ~150 |
| **Abfragen / Erklärungen** | `_handle_explanation_request` (33), `_async_explain_cause` (31), Verlauf, erweiterte Abfragen, Audit | ~250 |
| **Rückgängig** | `_async_handle_undo_request` (79) | 79 |

### Abhängigkeiten der Klasse

`self._context_store` (104 Zugriffe), `self._runtime_data` (91 Zugriffe
auf 22 verschiedene Dienste: `dialog_manager`, `effect_monitor`,
`profiles`, `user_contexts`, `predictive_house`, `bindings`, `memory`,
`proactive_agent`, `native_timer`, `goal_runs`, `shadow`, `trace` …),
`self.hass` (87), `self.entry` (47), `self._engine` (45),
`self._automation_executor` (36), `self._audit_trail` (13). Jeder Bereich
hat heute Zugriff auf alles – das ist das God-Object, das B4 auflösen soll.

### Dialogzustand

Zwei Speicher, die dieselbe Frage „was ist offen?“ beantworten:

- `ConversationContext` (`nlu/context.py`, im `_context_store`) mit
  `PendingDialogKind`: `ALIAS_LEARNING`, `AUTOMATION_WIZARD`,
  `PRODUCTIVITY`, `CALENDAR_EVENT`, `CALENDAR_MUTATION`,
  `AUTOMATION_CONFIRMATION`, `AUTOMATION_ACTION_EDIT`,
  `AUTOMATION_STRUCTURE_EDIT`, `AUTOMATION_MANAGEMENT`,
  `AUTOMATION_DELETION`, `SERVICE_CONFIRMATION`, `SEMANTIC_COMMAND`,
  `AUTOMATION_DRAFT`, `AUTOMATION_EVENT_CLARIFICATION`, `CLARIFICATION`.
- `DialogManager` (`dialog_manager.py`) mit 24 `DialogTaskKind`s, u. a.
  `ROUTINE_BINDING`, `RECURRENCE_CHOICE`, `LEARNING_OFFER`,
  `UNKNOWN_WORD`, `PLAN_CONFIRMATION`, `ALIAS_CONFIRMATION`,
  `PROACTIVE_CLARIFICATION`. `synchronize_context_task` spiegelt den
  ersten Speicher in den zweiten.

## 2. Verantwortungskarte `engine.py`

5335 Zeilen. `NluEngine` (4397 Zeilen, 69 Methoden), dazu `MatchResult`,
`CommandPlan` und 13 Modulfunktionen.

| Bereich | Methoden | Bemerkung |
| --- | --- | --- |
| **Kanonisches Verstehen** | `understand` → `_understand` (153), `_interpreted_match_result` (68), `_direct_understanding_outcome` (236), `_ontology_understanding` (150), `_build_match_result` (192), `understand_need` (88), `understand_release` (32), `understand_discourse` (49), `_semantic_multi_result` (47) | V8-Pfad; `_ontology_understanding` **überschreibt** das Ergebnis des semantischen Interpreten (`ontology_result` ersetzt `result`, `_REFUSED` verwirft es) – die verbliebene Override-Logik |
| **Kontext-Anschlüsse (first match)** | `match_followup` (73), `match_reference` (268), `match_query_followup` (335), `match_command_followup` (104), `match_correction_followup`, `match_contextual_property_followup`, `_media_followup` (34), `resolve_clarification` (110) | je eigener Einstieg, Reihenfolge in `conversation.py` |
| **Automationen** | `match_automation` (279), `match_relative_time_automation` (83), `match_recurring_time_automation` (75), `match_calendar_event_automation` (66), `match_calendar_time_automation` (62), `match_automation_delete` (61), `match_persistent_state_automation` (37), `match_repeated_event_automation` (37), `match_automation_query` (39), `_match_automation_toggle` (43), `understand_automation` (45), `revise_pending_automation` (107), `match_automation_draft_start`, `complete_automation_draft`, `_parse_action_or_notification` (45), `_try_extract_notification_from_trigger_clause` (56), `match_immediate_notification` (30) | Automations-/Benachrichtigungsdomäne in der Engine |
| **Legacy** | `match` (öffentlich, jetzt nur `understand().payload`), `_legacy_shadow_match` (157), `_select_shadow_parser` (29), `compare_understanding_pipelines` (56), `debug` (104) | `_legacy_shadow_match` wird **produktiv** noch in `_match_multi` (Segmente) und `_match_coordinated_named_targets` gerufen; `match(..., _compatibility_first=True)` nur vom Regressionsbericht; `engine.match` wird sonst nur von Tests (≈ 400 Aufrufe) benutzt. `_parse_action_semantically` (Automationsaktion) ruft `self.match` als Kompatibilitätsstufe |
| **Antworttexte** | `understanding_feedback` (121), `failure_feedback`, `_clarification_question`, `_ontology_preview_text`, `_ambiguous_kind_question` | Antwortplanung in der Engine |

Die Engine enthält keine Dienstaufrufe und keine Policy (geprüft:
`services.async_call` nur im Docstring), aber Dialog-nahe Logik
(`revise_pending_automation`, `resolve_clarification`) und
Domänen-Sonderlogik (Automationen, Benachrichtigungen, Medien-Anschlüsse).

## 3. Verbleibende first-match-Kaskade

`_async_handle_message_inner` entscheidet in dieser Reihenfolge; der erste
Treffer gewinnt (Zeilen aus 7.6.1):

1. Vorverarbeitung: Ort des Satelliten, Vorlieben als Alias, Klitika,
   Uhrzeiten, „hier“, Weckwunsch („Weck mich …“) wird umgeschrieben.
2. Offene Bestätigung + neuer vollständiger Satz → Frage verwerfen.
3. Offener Dialog + vollständiger Direktbefehl → Dialog ersetzen
   (`understand` zweimal möglich).
4. Meta-Fragen („Was hast du verstanden?“, „Warum fragst du?“).
5. `RECURRENCE_CHOICE`, Lernaufgaben, Dialog-Lernen, `ROUTINE_BINDING`.
6. Ursachenfrage (`interpret_cause_question`).
7. **Arbiter: Bedürfnis ↔ Situationsfrage** (einziger umgeschalteter Teil).
8. Diskurs-Anschluss (`understand_discourse`), Freigabe („kann aus“).
9. Alias-Bestätigung, Prozeduren, Routine-Rückmeldung, „unten alles aus“,
   Komfort, Dokumente, Wärmefragen, Lernen, **Ziele** (`interpret_goal`),
   Gedächtnis, Proaktiv-Antworten.
10. Undo, Erklärung „warum“, Assistent, offene Produktivität, Kalender,
    Automationsbestätigung, Verwaltung, Löschen, Dienstbestätigung,
    offener semantischer Befehl, Ereignis-Rückfrage, Automationsentwurf.
11. Sicherheitsgatter (unsicherer/verneinter Befehl), **nicht
    freigegebene Geräte (7.6.1)**, Routinen-Bindung per Sprache, Alias-Lehre,
    Assistent-Start, Audit, Verlauf, erweiterte Abfragen, Alarmanlage,
    Verwaltung (`understand_management`), Produktivität, Kalender,
    Automations-Bearbeitung/-Struktur/-Verwaltung,
    Automation löschen/deaktivieren/aktivieren.
12. `understand` (Direktbefehl), Fähigkeits-, Haushalts-, Geräteabfragen.
13. Offene Rückfrage (`resolve_candidate_reply`, `resolve_clarification`),
    Korrektur, sofortige Benachrichtigung, **fünf Kontext-Anschlüsse**
    (`match_followup`, `match_contextual_property_followup`,
    `match_reference`, `match_query_followup`, `match_command_followup`).
14. **Neun Automationsmatcher** in fester Reihenfolge (Abfrage,
    Wiederholung, Dauerzustand, wiederkehrende Zeit, `understand_automation`,
    Kalenderereignis, Kalenderzeit, Entwurf, relative Zeit).
15. Fallback `understand`, `_async_handle_no_match`.

**Konflikte, die die Reihenfolge heute entscheidet** (jeweils: wer gewinnt):

| Konflikt | Heute entschieden durch |
| --- | --- |
| Automation ↔ zeitversetzter Befehl | Schritt 14 vor 15; `_decide_recurrence`; `document.temporal` sperrt `understand` |
| Routine ↔ Szenenname | Schritt 7 (Bedürfnis/Routine) vor 12 (Direktbefehl); `_mark_inferred_routines` |
| Diskurs ↔ neuer Befehl | Schritt 3 vs. 8 vs. 13 (Kontext-Anschlüsse nur, wenn kein unbekanntes Wort) |
| gelernte Bindung ↔ Namenssuche | `need_compiler._routine` (Bindung zuerst) |
| Ziel ↔ Routine ↔ Direktbefehl | Schritt 9 (Ziele) vor 12; `classify_intent` |
| Verwaltung ↔ Gerät („Aktiviere die Automation Flurlicht“) | Schritt 11 vor 12 |
| Rückfrage-Antwort ↔ neuer Satz | Schritte 2/3 vs. 13 |

## 4. Abhängigkeiten der Meaning IR auf Legacy-Interna

| Stelle | Import | Art |
| --- | --- | --- |
| `nlu/meaning_ir.ground_meaning` | `ontology_compiler._clause_meanings` | privater Klausel-Leser des Gattungscompilers; die IR rekonstruiert Bedeutung aus dessen `ClauseMeaning` |
| `nlu/meaning_ir` | `ontology_compiler.ClauseMeaning` (indirekt, Felder `actions/degree/percent/temperature/descriptions/residue`) | die IR kopiert Feld für Feld |
| `nlu/ontology_compiler`, `nlu/discourse_compiler` | `semantic_compiler._percent`, `_temperature` | Mengen-/Wertlesung privat im alten Compiler |
| `nlu/discourse_compiler` | `target_resolution._name_index` | privater Namensindex |
| `nlu/target_resolution` | `device_ontology._form_index` | privater Formindex |
| `arbitration_candidates` | `engine.understand`, `understand_need`, `understand_release`, `security_control.match_alarm_control` | Kandidaten entstehen aus fertigen Compiler-Ergebnissen, nicht aus der IR |

Weitere private Querimporte außerhalb der IR (für B5/B6 relevant):
`parsers._strip_locative_prepositions` (4 Module),
`parsers._STATE_NAME_TO_SEMANTIC` (2), `response_generator._automation_label`
(3), `automation_action_parser._seconds_from_amount_unit`.

## 5. Sonderwege der Zielauflösung

`resolve_phrase` ist seit 7.4.0 die einzige **Namens**auflösung
(Architekturtest). Daneben existieren:

- `entity_scope.resolve_entity_scope` – Bereichssuche (ein Gerät oder eine
  homogene Raum-/Etagen-/Alles-Gruppe); Nutzer:
  `registered_operation_compiler`, `entity_resolution`,
  `extended_device_query`.
- `mentioned_entities` – Vorfilter „welche Namen stehen im Satz“:
  33 Aufrufe in 9 Modulen (`semantic_compiler` 13, `semantic_projection` 5,
  `household_query` 4, `entity_resolution` 3, …).
- `entities.resolve_entity_scored` – historische Namensstufe, noch in
  `areas.py`, `entities.resolve_entity` (Hülle), `constraint_resolver`,
  `entity_resolution`, `parser`, `query_executor`, `parsers`
  (Vergleichsresolver für `scripts/resolver_shadow.py`).
- Friendly-Name-Vergleiche (`normalize_for_compare(… friendly_name)`)
  außerhalb von `target_resolution`: 27 Stellen in 15 Modulen
  (`situation_views` 4, `conversation` 4, `semantic_compiler` 2,
  `discourse_compiler` 2, `household_query` 2, `engine` 2, …).
- Alias-Auflösung außerhalb: `alias_learning`, `conversation_learning`
  (Anwendung aber zentral über `target_resolution.apply_alias_bindings`).
- Neu in 7.6.1: `hidden_name_mentions` (nur Antworttext, nie Ziel).

## 6. Regex-Hotspots (`SEMANTIC_SENTENCE_PATTERN`, 212)

| Datei | Anzahl |
| --- | --- |
| `engine.py` | 30 |
| `nlu/semantic_utterance.py` | 18 |
| `nlu/semantic_compiler.py` | 16 |
| `conversation.py` | 13 |
| `nlu/normalize.py` | 13 |
| `automation_language.py` | 11 |
| `dialog_learning.py` | 11 |
| `location_property_query.py` | 10 |
| `appliance_lifecycle.py`, `capability_audit.py` | je 6 |
| `proactive_dialog.py`, `routine_intent.py` | je 5 |
| 33 weitere Dateien | 1–4 |

Die sechs größten Dateien halten 101 der 212 Muster.

## 7. Lücken in CI und Live-Tests

- **Proaktiv-Szenarien** laufen nur nächtlich (`nightly-live.yml`, Cron
  02:17), nicht bei `push`. Ein Release-Commit ist damit nicht vollständig
  live geprüft; der letzte Nightly-Lauf gehört zu einem anderen Commit.
- `nightly-live.yml` hat einen `workflow_dispatch`-Auslöser, läuft aber
  nicht bei `push`; für jeden Release-Commit muss der vollständige Lauf
  ausdrücklich angestoßen werden (B1).
- Der Arbiter-Shadow blockiert nur bei SAFETY_DRIFT; eine
  `BEHAVIOR_CHANGE` bleibt grün (in 7.6.1 kurz aufgetreten, behoben).
- Kein Architekturtest für die Abhängigkeitsrichtung (NLU → Conversation,
  Policy → Parser usw.); nur Einzelregeln (`resolve_phrase`, `context=`,
  Systemkontext).
- Latenzbenchmarks messen `understand`, nicht den ganzen Turn durch
  `conversation.py` (die Kaskade selbst ist ungemessen).
- Der Engine-Korpusvergleich gegen alte Stände (Git-Worktree) ist ein
  Skript, kein CI-Schritt.

## 8. Wellen

Reihenfolge wie im Auftrag, mit zwei Präzisierungen:

1. **B1 Release-Basis** – vollständigen Live-Lauf (inkl. Proaktiv) für den
   Release-Commit anstoßen und grün halten.
2. **B2 IR ohne Legacy-Interna** – Klausellesung (`ClauseMeaning`,
   `read_clauses`), Mengen (`percent_value`, `temperature_value`) und der
   Namensindex werden öffentliche Primitive in `nlu/`; Gattungscompiler,
   Diskurscompiler und IR nutzen dieselben. Shadow: Engine-Korpus 0
   Abweichung.
3. **B3 Arbitration mit Dialogzustand** – Dialogzustand als typisierte
   Evidenz; umschalten, wo der Shadow 0 SAFETY_DRIFT zeigt. Der größte
   Hebel ist die Kaskade in Schritt 13–15 (Kontext-Anschlüsse,
   Automationsmatcher, Direktbefehl).
4. **B7 EffectGraph-Härtung vor B4** (Umstellung gegenüber dem Vorschlag):
   unabhängig vom Umbau, sicherheitsrelevant, klein; so steht die
   Härtung auch dann, wenn die große Zerlegung nicht vollständig gelingt.
5. **B4 Zerlegung** – Controller entlang der Bereiche aus Abschnitt 1, jeder
   mit expliziten Abhängigkeiten statt `self._runtime_data`; Architekturtest
   für die Importrichtung.
6. **B5 Alte Pfade löschen** – `_legacy_shadow_match` aus Produktivpfaden
   (`_match_multi`, `_match_coordinated_named_targets`,
   `_parse_action_semantically`), `resolve_entity_scored` als
   In-Code-Vergleich, Override `_ontology_understanding`.
7. **B6 Satzmuster** – Hotspots aus Abschnitt 6.
8. **B8 Benchmark/STT**, **B9 Invarianten und Release-Gate**, **B10 Bericht**.
