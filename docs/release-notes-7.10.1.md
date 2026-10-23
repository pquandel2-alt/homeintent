# HomeIntent 7.10.1

Diese Datei beschreibt den vorgesehenen Patch-Inhalt. Sie bestätigt weder Tag
noch Veröffentlichung; beides ist erst nach grünen Pflichtjobs für denselben
`main`-Commit zulässig.

## Korrektheit und Sicherheit

- Der Entwicklungsbenchmark verwendet denselben öffentlichen
  Conversation-Scope-Resolver wie die Produktion. Bestätigungen,
  Automationsentwürfe und Rückfragen werden nicht mehr unter der öffentlichen
  Conversation-ID im privaten Store gesucht.
- Versionierte Benchmark-Grenzen prüfen neben unsicheren Ausführungen auch
  Wrong-Target-/Safety-Fehler sowie Gesamt-, Split- und Dialogkategorien. Eine
  Verschlechterung kann nur mit einer separaten Begründungsdatei akzeptiert
  werden; Sicherheitsgrenzen sind nicht absenkbar.
- Automation-Undo verwendet ein zielbezogenes Schema-2-Delta. Fremde
  zwischenzeitliche Änderungen bleiben erhalten; veränderte Zielzustände
  werden fail-closed abgelehnt.
- Interne Lifecycle-Dienste verlangen einen einmaligen kryptografischen
  Nachweis für Operation, Ziel, Auftraggeber und offenen Status. Dies gilt
  identisch für die Legacy-Domain.

## Music Assistant

- Bibliothekssuchen haben ein explizites Timeout von neun Sekunden. Timeout,
  HA-Dienstfehler, verschwundene Dienste und ungültige Antworten erzeugen
  keinen Wiedergabeplan; `CancelledError` wird weitergegeben.
- Ein Vertragstest lädt das echte Schema der installierten
  Home-Assistant-Music-Assistant-Komponente. Er startet keinen echten
  Music-Assistant-Server und führt keine Wiedergabe aus.

Persönliche Music-Assistant-Aliase, dynamische Gruppenbildung und die erste
NLU-Phase-4-Matrix sind nicht Teil dieses Patch-Releases. Sie benötigen einen
getrennten Minor-Release-Review.

Details: [Delta-Undo](delta-undo-7.10.1.md),
[Lifecycle-Nachweis](automation-lifecycle-security-7.10.1.md),
[Music Assistant](music-assistant.md) und
[Benchmark-Qualitätsgrenzen](perf/dev-benchmark-quality-gates.md).
