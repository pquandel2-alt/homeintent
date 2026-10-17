# Release-Ablauf

Ein HomeIntent-Release wird ausschließlich über den manuellen GitHub-Workflow
`Release` erstellt. Vorher müssen Versionsnummer und Release Notes auf `main`
vorbereitet und die vollständige CI für genau diesen `main`-Commit beendet sein.

1. Den vollständigen 40-stelligen SHA des aktuellen `main` notieren.
2. Prüfen, dass `custom_components/homeintent/manifest.json` die gewünschte
   Version enthält und `docs/release-notes-<version>.md` vollständig ist.
3. Die CI dieses exakten SHA abwarten. Ausstehende, laufende, abgebrochene,
   fehlgeschlagene oder fehlende Pflichtjobs blockieren die Freigabe.
4. Den Workflow `Release` manuell mit Version (ohne `v`) und SHA starten.
5. Der Workflow prüft SHA, aktuellen `main`, Manifest, Titel, Tag, Notes und
   alle Pflichtjobs. Direkt vor `gh release create` läuft dieselbe Prüfung
   erneut. Erst dann werden Tag und GitHub-Release atomar für den geprüften SHA
   erstellt.

Tag-Pushes starten die vollständige CI nicht erneut. Pull Requests — auch
Drafts — können keinen Release auslösen; ihr SHA ist nicht der aktuelle
`main`-SHA. Existiert Tag bereits oder ist eine Prüfung nicht eindeutig grün,
bricht der Workflow ohne Veröffentlichung ab.
