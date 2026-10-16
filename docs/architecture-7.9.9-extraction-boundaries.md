# Schrittweise Grenzen für Engine und Conversation nach v7.9.9

Stand: Stabilitätscommit `34b08373b1130f6f67991caa15136412a4a5a0df`.
Dieses Dokument ist eine Bestandsaufnahme und Migrationsreihenfolge, keine
Freigabe für einen Big-Bang-Umbau. Es verändert kein Laufzeitverhalten.

Die ältere Bestandsaufnahme `architektur-7.7-bestandsaufnahme.md` bleibt als
historischer Ausgangspunkt nützlich. Ihre Größenangaben beschreiben aber
nicht mehr den heutigen Stand: Die damaligen Controller-Extraktionen sind
bereits umgesetzt und müssen weiterverwendet werden.

## Gemessener Ist-Stand

Die Zahlen stammen aus einer AST-Auswertung des oben genannten Commits.

| Datei / Bereich | Zeilen | Beobachtung |
| --- | ---: | --- |
| `engine.py` | 4.917 | `NluEngine` umfasst 4.086 Zeilen; der direkte Kern `_understand` ist mit 156 Zeilen bereits vergleichsweise geschlossen. |
| `conversation.py` | 2.500 | `NluConversationEntity` umfasst 2.117 Zeilen. |
| `_async_handle_message_inner` | 1.782 | 111 Returns, 83 Await-Ausdrücke, 194 Verzweigungen und 505 Aufrufe; hier liegt der aktuelle Orchestrierungsengpass. |
| `controllers/` | 12 Fachcontroller | Automation, Automation-Management, Comfort, Devices, Goals, Insights, Learning, Monitoring, Notifications, Productivity, Queries und Routines existieren bereits. |

Innerhalb der langen Turn-Methode werden unter anderem `_engine` 34-mal,
`_world_model` 23-mal, `_context_store` 19-mal, `_devices` 17-mal und
`_automations` 13-mal referenziert. Das Problem ist damit nicht mehr ein
Fehlen von Fachmodulen, sondern deren weiterhin reihenfolgenabhängige
Verdrahtung in einer einzigen Methode.

Die bestehende Architektur besitzt bereits die richtigen Zielbausteine:

- `LanguageDocument`, `GermanStructuralAnalysis`, `SemanticGraph` und
  `MeaningCandidate` für Sprache und Bedeutungs-IR,
- `UnderstandingOutcome` als HA- und parserfreie Routergrenze,
- `arbitration.py`, `arbitration_candidates.py` und `dialog_manager.py` für
  Konkurrenz und offenen Dialog,
- die zwölf Fachcontroller statt einer neu zu erfindenden Controllerfamilie,
- `execution_policy.py` und `service_executor.py` als unveränderliche letzte
  Schreibgrenzen.

## Unveränderliche Grenzen

Jede Extraktion muss folgende Regeln erhalten:

1. NLU- und IR-Module importieren weder Home Assistant noch Controller,
   Conversation, ExecutionPolicy oder ServiceExecutor.
2. Fachrouter liefern langfristig ein typisiertes `UnderstandingOutcome`
   mit einem bestehenden Domainmodell als Payload. Ein `None` darf nicht
   gleichzeitig „nicht zuständig“, „unsicher“, „unvollständig“ und
   „Providerfehler“ bedeuten.
3. Ein sprachlicher Router ruft keinen HA-Service auf. Schreibpläne werden
   erst nach Grounding und Capability Validation erzeugt.
4. Jede Ausführung läuft weiterhin durch `ExecutionPolicy`, gegebenenfalls
   Bestätigung und `service_executor.async_execute_service_plan`.
5. AutomationModel, AutomationValidator, Preview/Bestätigung und
   AutomationExecutor bleiben autoritativ; eine Extraktion baut keinen
   zweiten Automationsstack.
6. Routerreihenfolge ist beobachtbares Sicherheitsverhalten. Eine
   mechanisch elegantere Reihenfolge ist ohne Shadow-Beleg keine reine
   Refaktorierung.
7. Queries, Negationen, Hypothesen, unsichere Referenzen und unvollständige
   Bedeutungen tragen niemals einen ausführbaren Plan.

Die bestehenden statischen Regeln in `tests/test_architecture_layers_77.py`
werden erweitert, nicht ersetzt.

## Geeignete Grenze in `conversation.py`

`conversation.py` soll HA-Adapter und expliziter Orchestrator bleiben. Es
soll nicht selbst die fachliche Bedeutung eines Satzes bestimmen. Die
Zerlegung erfolgt in vier kleinen Schnitten.

### C1: Turn-Vorbereitung

Der zusammenhängende Anfang von `_async_handle_message_inner` baut heute
Entity-/Device-Snapshot, WorldModel, HouseGraph, lokalen Text,
`PreparedSurface`, `LanguageDocument` und `UnderstandingContext` auf. Diese
Arbeit kann in einen kleinen Turn-Preparation-Baustein verschoben werden.

Der Rückgabetyp hält nur Referenzen auf die bereits bestehenden Objekte; er
kopiert weder Registry noch WorldModel und führt keine Entity Resolution
ein. Registryzugriffe bleiben einmalig vor dem Routing. Der Wake-Sonderfall
wird erst nach eigenen Charakterisierungstests verschoben, da er derzeit
eine sichere Rückfrage und eine Textprojektion kombiniert.

### C2: Dialogaufnahme und Ownership

Synchronisation von `ConversationContext`, `DialogManager`, Benutzerbindung,
Abbruch, Meta-Fragen und Supersede-Entscheidung bilden eine zweite
zusammenhängende Phase. Sie soll die vorhandenen Typen aus
`dialog_manager.py` und `arbitration.py` verwenden und eine typisierte
Entscheidung zurückgeben: weiter routen, offene Aufgabe beantworten,
verwerfen oder sicher ablehnen.

Dabei entsteht kein dritter Dialogspeicher. `ConversationContext` und
`DialogManager` werden über die bereits vorhandene Synchronisation
schrittweise konsolidiert. Ownership-Prüfungen dürfen nicht in einzelne
Antwortcontroller zerstreut werden.

### C3: Explizite Routingphasen

Die verbleibende Kaskade wird zunächst nur in private, benannte Phasen mit
unveränderter Reihenfolge geteilt, beispielsweise:

1. nicht ausführbare Modalität und Maintain,
2. offene Dialog-/Task-Antworten,
3. read-only Insights und Queries,
4. Need/Query/Command-Arbitration,
5. vorhandene Fachcontroller,
6. Kontextanschlüsse,
7. Automation und zeitgebundene Projektionen,
8. direkter semantischer Befehl und ehrlicher Fallback.

Erst wenn diese Phasen durch Turn-Charakterisierungstests eingefroren sind,
kann eine Phase in einen vorhandenen Fachcontroller wandern. Eine generische
Plugin- oder „first handler wins“-Registry wäre hier ein Rückschritt: Sie
würde die sicherheitsrelevante Reihenfolge nur verstecken.

### C4: Typisierte Fachantwort vor Rendering

Mehrere Controller interpretieren, mutieren Dialogzustand, rendern Sprache
und führen in derselben Methode aus. Die Migration trennt pro Fähigkeit
zuerst nur den Interpretationsschritt ab. Dieser liefert
`UnderstandingOutcome[DomainPayload]`; der bestehende Controller rendert und
behandelt den Payload vorerst weiter.

Nachgewiesene Schreibpläne dürfen Controller weiterhin ausschließlich über
den zentralen ServiceExecutor ausführen. Das Ziel ist nicht ein einzelner
Mega-Executor, sondern genau eine Policy-/Service-Grenze für jeden
Schreibpfad.

## Geeignete Grenzen in `engine.py`

Der 156-zeilige direkte `_understand`-Pfad ist der kanonische semantische
Kern und bleibt zunächst in `NluEngine`. Die sicheren Extraktionskandidaten
liegen um ihn herum.

### E1: Kontext- und Follow-up-Verstehen

Die Methoden `match_followup`, `_media_followup`,
`match_contextual_property_followup`, `match_reference`,
`match_query_followup` und `match_command_followup` bilden einen Block von
rund 840 Zeilen. Sie greifen bereits auf `nlu.discourse`,
`query_followup_compiler`, `ContextualPropertyResolver` und
`ConversationCorrectionResolver` zurück.

Ein gemeinsamer Context-Understanding-Baustein soll diese vorhandenen Leser
koordinieren und typisierte Outcomes liefern. `NluEngine` behält während der
Migration dünne kompatible Methoden. Die fünf Leser werden nicht durch eine
zweite Referenzauflösung ersetzt.

### E2: Automation-Understanding

Von `match_automation_query` bis zu den Zeit-/Kalenderprojektionen und der
Clarification liegen mehr als 1.700 zusammenhängende Zeilen. Die eigentlichen
Parser und Modelle existieren bereits in Automationmodulen. Ein
Automation-Understanding-Baustein erhält diese Parser als Abhängigkeiten und
liefert `UnderstandingOutcome` mit den bestehenden
`AutomationMatchResult`-/Draft-/Clarification-Payloads als Übergangsvertrag.

Die Kompatibilitätsmethoden auf `NluEngine` delegieren zunächst nur. Erst ein
späterer, separat belegter Commit darf Conversation direkt gegen den neuen
typisierten Einstieg verdrahten. Validator, Preview, Bestätigung und
Executor werden dabei nicht verschoben.

### E3: Feedback und Diagnose

`failure_feedback`, `understanding_feedback`, `respond` und `debug` sind
Ausgabe-/Diagnoseprojektionen, nicht Bedeutungserkennung. Sie werden erst
nach E1/E2 extrahiert, damit Fehlertext und Debugsignaturen nicht gleichzeitig
mit Routing verändert werden. Benutzertexte bleiben stabil und sanitisiert;
interne Providerdetails bleiben ausschließlich im Log.

### E4: Kompatibilitätsabbau zuletzt

`MatchResult`, `CommandPlan` und die `match_*`-Methoden bleiben so lange als
Adapter bestehen, bis alle produktiven Aufrufer den typisierten Outcome-Pfad
verwenden. Entfernt wird nur, was `rg`, Pyright und Shadow als unbenutzt und
verhaltensgleich belegen. Zeilenreduktion allein ist kein Erfolgskriterium.

## Reihenfolge der Implementierungs-PRs

Jeder Punkt ist ein eigener, reviewbarer PR auf einem grünen Vorgänger:

1. Charakterisierung von Turn-Vorbereitung, Dialog-Supersede und heutiger
   Routerreihenfolge; noch keine Extraktion.
2. C1 Turn-Vorbereitung, reine Bewegung mit identischem Snapshot-/Cache-
   Verhalten.
3. C2 Dialogaufnahme, ohne Änderung der gespeicherten Dialogtypen.
4. C3 Aufteilung in private Phasen innerhalb `conversation.py`.
5. E1 Context-Understanding mit delegierenden Kompatibilitätsmethoden.
6. E2 Automation-Understanding mit unveränderten Domainmodellen.
7. Je Fachfähigkeit C4: Interpretation als `UnderstandingOutcome`, danach
   separat Rendering/Handling.
8. E3 Diagnoseprojektionen und erst zuletzt E4 unbenutzte Adapter.

Ein PR darf höchstens eine dieser Autoritätsgrenzen verändern. Reine
Dateibewegung wird nicht mit NLU-Erweiterung, Antwortänderung oder
ExecutionPolicy-Änderung vermischt.

## Pflichtbelege je Extraktion

Vorher und nachher werden mindestens ausgeführt und als Artefakte verglichen:

- betroffene Unit- und Conversation-Integrationstests,
- `tests/test_architecture_77.py` und
  `tests/test_architecture_layers_77.py`,
- das offizielle Sprach-Eval `scripts/run_language_eval.sh`,
- `scripts/corpus_shadow.py --check
  docs/perf/corpus-signatures-7.9.9.json`,
- `scripts/shadow_compare.py --candidate identity --check`,
- `scripts/arbiter_shadow.py --check` mit RuntimeWarning als Fehler,
- beide Entwicklungsbenchmarks mit `unsafe_execution_count = 0`,
- Pyflakes und vollständiges Pyright,
- die 5.000-Entity-Latenzbudgets bei Änderungen am Turn- oder
  Understanding-Pfad,
- die vollständige Suite unter Python 3.12/3.13 und das echte HA-Testbett
  vor einer Zusammenführung.

Zusätzliche Charakterisierung muss insbesondere dieselbe Routerwahl,
Outcome-Art, Payloadsignatur, Antwort, Dialogmutation, Servicepläne und
Ausführungsreihenfolge festhalten. Safety-Drift, neue Schreibpfade,
zusätzliche Registryzugriffe pro Turn oder eine Änderung von NEVER_AUTO und
Bestätigung sind Abbruchkriterien, keine akzeptierte Refactoringabweichung.

