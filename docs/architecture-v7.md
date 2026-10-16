# HomeIntent V7 – aktive Understanding-Architektur

Stand: 5. September 2026

Die gemessenen, schrittweisen Extraktionsgrenzen für den v7.9.9-Stand sind
in [`architecture-7.9.9-extraction-boundaries.md`](architecture-7.9.9-extraction-boundaries.md)
dokumentiert.

## Produktgrenze

HomeIntent V7 versteht Bedeutung innerhalb der unterstützten Smart-Home-
Operationen. Es verspricht nicht, jede beliebige deutsche Äußerung der Welt
auszuführen. Bei unvollständiger, widersprüchlicher oder mehrdeutiger
Bedeutung ist eine Rückfrage oder sichere Ablehnung das korrekte Ergebnis.
Eine nur ähnlich klingende Formulierung darf niemals ungeprüft eine Aktion
auslösen.

## Aktiver Pfad

```text
Originaltext + Registry + Dialogkontext
                 |
                 v
      loss-aware LanguageDocument
      - Originaltokens und Quellspannen
      - Normalisierungsvarianten mit Kosten/Herkunft
      - Sprechakt, Modalität, Polarität und Klauseln
                 |
                 v
        SemanticInterpreter
      - wiederverwendbare Katalogeinträge
      - vollständige MeaningCandidates
      - Konflikte, fehlende Slots, ungeklärte Tokens
                 |
                 v
       gemeinsamer Target Resolver
      - Exact/Alias/Token/Ort/Etage/Domain
      - nachvollziehbare Scores
      - Gleichstand => Rückfrage
                 |
                 v
        UnderstandingOutcome
      QUERY | COMMAND | AUTOMATION | CLARIFICATION
      AMBIGUOUS | UNSUPPORTED | UNSAFE
                 |
                 v
      Capability/Policy/Bestätigung/Executor
```

`NluEngine.understand()` ist die kanonische Grenze für direkte Turns,
`NluEngine.understand_automation()` für Automationsformulierungen. Die
Conversation-Integration erstellt pro frischem Turn genau ein
`LanguageDocument` und reicht es an diese Grenzen sowie an die lesenden
Haushalts-, Kalender- und Produktivitätsrouter weiter. Kalender und
Produktivität liefern gemeinsam ein typisiertes `UnderstandingOutcome`;
gleichzeitige vollständige Treffer werden anhand expliziter Domänenevidenz
aufgelöst oder ohne Payload als `AMBIGUOUS` zurückgegeben.

Geräte-Fertigstellungsfragen sind ein strikt lesender Spezialfall dieser
Grenze. Sie verwenden nur im aktuellen Assist-Snapshot enthaltene
`sensor.*`-Entitäten mit `device_class: timestamp`, eine semantisch passende
Fertigstellungsbezeichnung und – sofern vorhanden – die Gerätebeziehung des
gemeinsamen World Models. Kein Treffer wird erraten; mehrere Treffer ergeben
eine Rückfrage. Der Ergebnisvertrag enthält grundsätzlich keinen Serviceplan.

Timer ohne `timer.*`-Helper werden nicht durch einen eigenen Scheduler
implementiert. `NativeTimerRuntime` übersetzt das typisierte `TimerRequest` in
Home Assistants registrierte Timer-Intents und übergibt Dauer, Namen und das
aufrufende Assist-Gerät an dessen `TimerManager`. Native Sprachsatelliten
erhalten das HA-Ablaufereignis. Für nicht timerfähige Clients registriert
HomeIntent genau einen synthetischen Timer-Endpunkt, dessen `FINISHED`-Ereignis
über die konfigurierte TTS-Engine und Medienplayer realisiert wird. Der
Endpunkt wird beim Config-Entry-Unload abgemeldet.

Direkte Live-Turns werden in `understand()` semantisch zuerst interpretiert.
Der aktive Schnitt umfasst Ein/Aus für Licht, Schalter, Ventilator, Heizung,
Luftbefeuchter und Input-Boolean, Öffnen/Schließen für Cover und Ventile,
Media Play/Pause, Saugroboter Start/Stopp, Szenenaktivierung, Cover-/Licht-
Prozentwerte, Heizungs-Solltemperatur, Lock/Unlock, Button-Press sowie
registrierte Klima-, Medien-, Befeuchter-, Warmwasser-, Select-, Number-,
Lamellen-, Fan-, Ventil-, Mähroboter-, Kamera- und Notify-Operationen. Der
frühere `device_control`-Fachparser und sein Erweiterungsparser wurden
gelöscht. Direkte Core-Sprache besitzt keinen produktiven Parser-Registry-
Fallback mehr; historische Grammatiken sind nur über den expliziten
read-only Shadow-Audit erreichbar. Management-, Kalender-, Produktivitäts-
und Automationsparser bleiben begrenzte Fachkomponenten hinter derselben
Verständnis- und Sicherheitsgrenze. `UnderstandingOutcome.authority` macht
die getroffene Entscheidung beobachtbar. Ausführbare V7-Autorität setzt
einen vollständigen Kandidaten und bei konkurrierenden vollständigen Lesarten
mindestens zehn Scorepunkte Abstand voraus; echte Target-Mehrdeutigkeit bleibt
unabhängig davon eine nicht ausführbare Klärung.

## Sicherheitsinvarianten

- Der Originaltext bleibt erhalten; Varianten überschreiben ihn nicht.
- Query, Hypothese, Unsicherheit und Negation sind nicht direkt ausführbar.
- Eine eindeutig erkannte, leicht vertippte Negation bleibt negativ.
- Phonetische Entity-Korrekturen werden nie still ausgeführt.
- Registry-Namen werden nicht als Grammatik interpretiert. Beispielsweise
  bleibt ein Script namens „Gute Nacht“ trotz der Nähe von „Nacht“ zu
  „nicht“ ein positives Ziel.
- Ein gleichwertiger Target-Gleichstand erzeugt eine Klärung, keinen
  First-Match.
- Klärungen nennen bis zu fünf unterscheidbare Kandidaten und akzeptieren
  Name, Alias, Bereich, Etage, Merkmal oder Ordinalzahl. Ein einzelner nur
  ungefähr passender Name verlangt ein explizites Ja; Nein bricht ab.
- Eine unklare Folgeantwort hält den Dialog offen. Abbruch, Timeout oder ein
  vollständiger neuer Befehl beenden ihn definiert.
- Die ausgewählte stabile Entity-ID wird vor Ausführung gegen den aktuellen
  Registry-Snapshot und die aktuelle Capability erneut validiert.
- Erst ein validierter `ServiceCallPlan` darf den Executor erreichen.

## Entity-Kandidatenvertrag

`EntityCandidateSet` enthält Score, Matchquelle, passenden Registry-Namen,
Edit-Distanz, Margin und die stabile Entity-ID. Exakte Namen und Aliase
liegen über Teiltreffern; fuzzy Evidenz liegt darunter. Ein einzelner
fuzzy-only Treffer ist `CONFIRMATION_REQUIRED`, mehrere Treffer innerhalb
der Sicherheitsmargin sind `AMBIGUOUS`.

Der Vertrag wird von direkten Befehlen, Singular-Queries und den bestehenden
Kalender-, Listen- und Timer-Auswahldialogen konsumiert. Automationsparser
nutzen denselben Resolver und dürfen ein mehrdeutiges Ziel nicht in ein
`AutomationModel` übernehmen.

## Migration und Kompatibilität

Die öffentliche V7-Grenze ist für direkte Capabilities autoritativ.
Der frühere generische Geräte-, Erweiterungs- und Geräte-Folgeparser ist aus
der Conversation-Route und dem Quellbaum entfernt. Seine sicheren Operationen
werden als `HassRegisteredOperation` in einem typisierten Frame dargestellt;
Validator und Service Mapper prüfen Domain, Service und Daten erneut gegen die
geschlossene lokale Allowlist. Auch `match()` delegiert ausschließlich an
`understand()`; ein Regex-gewählter Altparser kann keinen produktiven Plan mehr
erzeugen. Der explizite read-only Shadow-Audit verwendet intern weiterhin
`compatibility_first`, damit seine unabhängige Altseite messbar bleibt. Die
zugehörigen historischen Grammatiken werden erst bei einem solchen Audit lazy
geladen und erhöhen weder produktive Startzeit noch Autorität. Redundante
Gruppen-Semantik wurde auf den Semantic Compiler delegiert.

Der Dialogmanager übernimmt bei allen in `ConversationContext` gespeicherten
Dialogarten den vollständigen typisierten Payload, Besitzer, Priorität,
Kandidaten und die Begründung in die zentrale Task-Queue. Sämtliche
Dialoghandler dispatchen auf dieser Manager-Payload und lesen nicht erneut das
jeweilige `pending_*`-Feld. `ConversationContext` bleibt als kompatibler
TTL-/Diskursspeicher bestehen; eine spätere Schema-Migration kann die alten
benannten Felder entfernen, ohne laufende Config Entries zu verlieren.

Der read-only Shadow-Vergleich ist über
`NluEngine.compare_understanding_pipelines()` reproduzierbar. Das Skript
`scripts/v7_shadow_report.py` führt ihn über Dialogkorpus, 1.024
Lichtparaphrasen und 2.688 domänenübergreifende Direktbefehle aus. Der
versionierte Bericht enthält 3.772 Turns: 3.752 semantisch identisch, keinen
nur-Legacy- oder nur-V7-Treffer, keine Divergenz und 20 beidseitige sichere
Fehlschläge. Diese Fehlschläge sind kontextabhängige Folgeturns oder bewusst
nicht ausführbare Hypothesen, unbekannte/ungültige Ziele und Werte. Der Shadow-Pfad erzeugt
nur unveränderliche Pläne und Antworten und besitzt keinen HA-Executor.
Im selben Lauf sind Query-to-Action-, Unsafe-Action- und
Ambiguous-to-Action-Leakage jeweils null.

Das ist bewusst keine Behauptung, sämtliche historischen Parser seien schon
gelöscht. Kalender und Produktivität besitzen weiterhin fachspezifische
Parser, liegen am Hauptrouter aber hinter derselben `LanguageDocument`- und
`UnderstandingOutcome`-Grenze. Einzelne Automation- und Managementfunktionen
werden noch schrittweise auf diesen Vertrag migriert; die zentrale
Sicherheitsklassifikation schützt sie bereits heute.

Dialogzustände werden im zentralen `DialogManager` priorisiert,
benutzergebunden und über ihren typisierten Payload an den zuständigen Handler
gegeben. Das gilt für Alias-, Automations-, Kalender-, Produktivitäts-,
Servicebestätigungs-, semantische Ergänzungs- und Entity-Auswahldialoge.

## Qualitätsgates

Der Sprach-Eval-Lauf bündelt das unabhängige Dialogkorpus, metamorphe
Invarianzprüfungen, die bestehende 1.024er-Paraphrasenmatrix und die
domänenübergreifenden V7-Semantikverträge:

```bash
bash scripts/run_language_eval.sh
```

Der vollständige Projektlauf sowie Pyflakes und der blockierende
Pyright-Strict-Scope bleiben zusätzlich verpflichtend. CI prüft auf Python
3.12 außerdem den versionierten Shadow-Bericht und ein 100-ms-p95-Budget bei
5.000 synthetischen Entities. Performancewerte und die noch erforderliche
Messung auf echter Pi-Hardware stehen in
[`perf/v7-understanding.md`](perf/v7-understanding.md).

`conversation.py` erreicht im vollständigen Lauf vom 5. September 2026 77 %
Coverage; das Gesamtpaket erreicht 87 %. Der Ausbau konzentriert sich auf
offene Dialogzustände und risikoreiche Übergänge: Entity-Auswahl,
Bestätigung/Abbruch, Wizard-Retries, Todo-Mutationen, Mehrfachbefehle mit
Undo, Automationsauswahl sowie Laufzeitfehler. Ein End-to-End-Test schlägt
weiterhin fehl, sobald ein migrierter V7-Pfad wieder den alten Device-Router
erreicht.

## Nachgelagerter lokaler Agentenkern

V7 bleibt der einzige Spracheinstieg. Dahinter teilen sich Dialogmanager,
Hausgraph, Gedächtnis, Situationen und Planer dieselben stabilen IDs aus dem
pro Turn gebauten `WorldModel`. Der vollständige Vertrag ist in
[`jarvis-core.md`](jarvis-core.md) dokumentiert. Keine dieser Schichten darf
Home-Assistant-Dienste ausführen; auch Mehrschrittpläne und persistierte
Agentenaktionen laufen ausschließlich durch Execution Policy und gemeinsamen
Executor mit frischem Snapshot.

Katalogisierte Komfort- und Haushaltsszenarien werden erst nach dem
`LanguageDocument` erkannt. Sie arbeiten ausschließlich mit stabilen IDs und
geben typisierte ASK-Vorschläge beziehungsweise `AutomationModel`-Bäume an
die vorhandenen Validator-, Vorschau- und Bestätigungsstrecken weiter. Sie
führen keine rohe Äußerung direkt in einen Home-Assistant-Serviceaufruf um.
