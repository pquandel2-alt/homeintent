# Release-Ablauf

Ein HomeIntent-Release wird ausschließlich über den manuellen GitHub-Workflow
`Release` erstellt. Vorher müssen Versionsnummer und Release Notes auf `main`
vorbereitet und die vollständige CI für genau diesen `main`-Commit beendet sein.

1. Den vollständigen 40-stelligen SHA des aktuellen `main` notieren.
2. Prüfen, dass `custom_components/homeintent/manifest.json` die gewünschte
   Version enthält und `docs/release-notes-<version>.md` vollständig ist.
3. Die CI (`ci.yml`) dieses exakten SHA abwarten und den Nightly-Workflow
   (`nightly-live.yml`, „Run workflow“ auf `main`) für denselben SHA starten
   und abwarten. Ausstehende, laufende, abgebrochene, fehlgeschlagene oder
   fehlende Läufe oder Pflichtjobs blockieren die Freigabe. Geprüft wird
   jeweils der neueste Lauf des exakten SHA; ein einmal fehlgeschlagener
   Sicherheits-Property-Job desselben SHA blockiert dauerhaft, auch wenn ein
   späterer Lauf mit anderen Seeds grün ist.
4. Den Workflow `Release` manuell mit Version (ohne `v`) und SHA starten.
5. Der Workflow prüft SHA, aktuellen `main`, Manifest, Titel, Tag, Notes und
   alle Pflichtjobs beider Workflows und gibt jede geprüfte Run-ID mit SHA
   aus. Direkt vor der Veröffentlichung läuft dieselbe Prüfung erneut. Dann
   wird das Tag direkt auf den geprüften SHA gelegt (schlägt bei vorhandenem
   Tag fehl), der Release mit `--verify-tag` erstellt und anschließend
   geprüft, dass das Tag auf den SHA zeigt. Der Release-Text erhält
   automatisch Commit- und Lauf-Links der geprüften Läufe. Releases laufen
   global nacheinander (eine Concurrency-Gruppe).

Tag-Pushes starten die vollständige CI nicht erneut. Pull Requests — auch
Drafts — können keinen Release auslösen; ihr SHA ist nicht der aktuelle
`main`-SHA. Existiert Tag bereits oder ist eine Prüfung nicht eindeutig grün,
bricht der Workflow ohne Veröffentlichung ab.
