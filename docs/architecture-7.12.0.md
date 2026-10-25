# Architekturgrenzen 7.12.0

7.12.0 führt keine zweite NLU und keinen zusätzlichen Schreibpfad ein. Die
bestehenden Fassaden bleiben kompatibel, fachliche Zustände liegen jedoch an
klareren Grenzen.

## Conversation und Dialoge

`conversation.py` erzeugt das `LanguageDocument` weiterhin genau einmal pro
Turn und orchestriert Arbitration und Dispatch. Die neue Datei
`nlu/automation_slot_dialog.py` besitzt die Home-Assistant-freie Fachlogik für
Automation-Slots: unveränderlicher Draft, Pflichtslotmatrix, Projektion,
Provenienz, kleinste Rückfrage, Modellkompilierung und eng begrenzte
Preview-Korrektur. `controllers/automations.py` bindet Benutzer/Scope/TTL und
übergibt vollständige Modelle an den vorhandenen Preview-/Confirmation-Pfad.

Der Music-Assistant-Instanzzustand und seine reine Auswahl stehen in
`music_aliases.py`; `controllers/insights.py` orchestriert nur Suche,
Rückfrage, erneute Prüfung und bestätigten Store-Zugriff. Der zentrale
`DialogManager` bleibt die einzige aktive Task-Koordination.

## Automation-Persistenz

`AutomationExecutor` bleibt die Fassade und einzige fachliche Schreibgrenze.
Die Verantwortlichkeiten sind getrennt in:

- `automation_transaction.py`: dauerhaftes Transaktionsjournal,
- `automation_history.py`: begrenzte Schema-3-Delta-Historie,
- `automation_undo.py`: HA-freie Validierung, Legacy-Migration und atomare
  Restore-Planung für Single-/Multi-Target-Deltas,
- `automation_metadata_store.py`: atomare, zielbezogene Sidecar-CAS-Operationen,
- `automation_executor.py`: Sperre, Lifecycle-/CRUD-/Pause-/Resume-Orchestrierung
  und HA-Reload-/Registry-Grenze.

Single- und Multi-Target-Undo teilen denselben reinen Planer und dieselbe
Executor-Sperre. Erst nach vollständiger Delta-, Digest-, Typ- und
Positionsprüfung erreicht ein Restore-Plan die I/O-Grenze. Kein Dialog schreibt
`automations.yaml` direkt.

Für Pause/Fortsetzen journalisiert die Transaktionsgrenze ausschließlich die
betroffenen Ziel-/Helper-Metadaten vor und nach der Mutation. Die
Startwiederherstellung vergleicht jeden dieser Werte kanonisch und rollt YAML
und Sidecar gemeinsam zurück; unbekannte externe Abweichungen blockieren den
Rollback.

## Engine und EventRuntime

Die vorhandenen Engine-Grenzen (`automation_grounding`, Automation-Compiler,
Query-Module, semantische Compiler und Response-Generator) bleiben
autoritative Fachmodule; 7.12.0 ergänzt nur den Slotprojektor. Ein weiteres
mechanisches Verschieben aus `engine.py` wäre ohne eigenen neuen Vertrag nicht
sicher begründbar und wurde deshalb nicht vorgenommen.

`event_runtime.py` nutzt bereits `event_runtime_queue`,
`event_runtime_supervisor`, `event_runtime_scheduler`, `event_interest`,
`event_priority` und persistente Stores. Da diese Änderung keine Eventlogik
berührt, wurden Queue-, Backpressure-, Circuit-Breaker- und Startschutzgrenzen
nicht umgebaut.

## Exception-Grenzen

Neue reine Fachlogik verwendet konkrete Rückgabewerte beziehungsweise
`ValueError` für ungültige Deltas. Breite Exception-Grenzen wurden nur an den
bereits transaktionalen HA-Reload-/Rollback-Grenzen verwendet; `CancelledError`
der Music-Assistant-Suche bleibt ungefangen. Sensible Lifecycle-Token werden
nicht in Antworten oder Logs aufgenommen.
