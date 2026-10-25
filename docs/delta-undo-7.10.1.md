# Zielbezogenes Automation-Undo ab 7.12.0

Neue Verlaufseinträge verwenden Schema 3. Ein Single-Target-Eintrag enthält
nur Operation, Automations-ID, Eigentümer, Zeitpunkt, Vorher-/Nachherzustand
und Position der Zielautomation, den zugehörigen Vorher-/Nachher-Sidecareintrag
sowie kanonische SHA-256-Prüfsummen dieser vier Werte. Er enthält weder die
vollständige `automations.yaml` noch den vollständigen Metadatenbestand. Der
Verlauf bleibt auf zehn Einträge begrenzt.

Das getrennte Transaktionsjournal darf bei zusammengesetzten Pause-/Resume-
Schreibvorgängen zusätzlich die Vorher-/Nachher-Metadaten genau der betroffenen
Ziel- und Helper-ID enthalten. Diese begrenzte Write-ahead-Information gehört
nicht zur Undo-Historie und ermöglicht nach einem Absturz den gemeinsamen
Rollback von YAML und Sidecar. Unbeteiligte Automationen, Metadaten und deren
Lifecycle-Tokens werden nicht hineinkopiert.

Pause und Fortsetzen verwenden ein geordnetes `targets`-Array. Jedes Element
trägt dasselbe Zieldelta plus eine Rolle (`target` oder `resume_helper`). Vor
Undo prüft HomeIntent unter derselben Schreibsperre sämtliche YAML- und
Metadaten-Nachherzustände, erwartete Abwesenheit sowie eindeutige IDs. Weicht
ein Ziel ab, erfolgt kein Schreibzugriff.

Nur die geprüften Ziele werden entfernt, eingefügt oder ersetzt. Andere
Automationen und Sidecareinträge stammen aus dem aktuellen Zustand und bleiben
einschließlich fremder Änderungen erhalten. YAML, Zielmetadaten und
`automation.reload` bilden eine rückrollbare Transaktion; erst nach vollständig
erfolgreichem Reload wird der LIFO-Eintrag entfernt. Eigentümer oder
Home-Assistant-Administrator sind erforderlich, anonyme Aufrufer werden
abgewiesen, konkurrierende Undo-Anfragen sind serialisiert.

## Alte Einträge

- Schema 2 wird nur aus seinen gültigen Ziel-Deltas gelesen; redundante alte
  Vollsnapshots werden ignoriert und nie zurückgeschrieben.
- Schema 1 wird nur im Speicher in ein Zieldelta überführt, wenn seine
  vollständigen YAML- und Metadatenprüfsummen exakt zum aktuellen Zustand
  passen. Fehlende, beschädigte oder veraltete Prüfsummen blockieren Undo.
- Alte zusammengesetzte Pause-/Fortsetzen-Einträge besitzen kein beweiskräftiges
  Mehrziel-Delta. Sie bleiben deshalb unverändert an der LIFO-Spitze. HomeIntent
  erklärt den Grund. Ein angemeldeter Administrator kann den geprüften Eintrag
  nach einer eigenen Bestätigung mit „Verwirf den letzten nicht rückgängig
  machbaren Verlaufseintrag“ entfernen. Dabei werden weder YAML noch Metadaten
  verändert; unterstützte oder beliebige beschädigte Einträge lassen sich auf
  diesem Weg nicht überspringen.

## Sicherheitsgrenzen

Doppelte oder widersprüchliche IDs, beschädigte eingebettete Digests, extern
veränderte Ziele oder Helper und geänderte Zielmetadaten werden fail-closed
abgelehnt. Dateilesen, atomare Dateischreibvorgänge und Verlaufsoperationen
laufen über Home Assistants Executor und blockieren den Eventloop nicht.
