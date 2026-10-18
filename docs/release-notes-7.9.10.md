# HomeIntent 7.9.10

7.9.10 härtet Ereignisverarbeitung, Fehlergrenzen und die strukturelle
deutsche Sprachverarbeitung. Der Sicherheitsvertrag bleibt unverändert:
HomeIntent arbeitet weiterhin ohne LLM oder ML-Modell zur Laufzeit;
Grounding, Berechtigungen, Bestätigungen, `NEVER_AUTO`, `ExecutionPolicy` und
`ServiceExecutor` bleiben verbindlich.

## EventRuntime und Wiederanlauf

- Circuit-Retry-Fristen werden unabhängig von neu eintreffenden Events
  eingehalten. Ein Event kann einen offenen Circuit nicht mehr vorzeitig
  umgehen.
- Begrenzte Wiederholungsversuche, Zustandsübergänge, Resets und
  Wiederanläufe sind explizit instrumentiert und diagnostizierbar.
- Queue, Metriken, Supervisor und Scheduler sind in klar getrennte
  Laufzeitkomponenten aufgeteilt, ohne den bestehenden Lebenszyklusvertrag zu
  verändern.

## Fehlergrenzen und Datenschutz

- Die produktiven Ausnahmegrenzen wurden vollständig inventarisiert und um
  fehlende Rollback- und Diagnosepfade ergänzt.
- Unerwartete Provider-, Recorder- und Servicefehler bleiben intern mit
  Traceback analysierbar. Assist-Antworten und persistierte Fehler enthalten
  keine ungefilterten Ausnahmetexte, Pfade, Tokens oder sonstige sensible
  Details.

## Deutsche Sprache

- Formelle Höflichkeitsformen, Mitternacht und umgangssprachliche Befehle wie
  „hau … runter“ werden strukturell erkannt.
- Geordnete Mehrfachbefehle und ein lokal gebundenes „erst“ behalten ihre
  Reihenfolge und ihren Geltungsbereich.
- Koordinierte Ziele werden in einem gemeinsamen Scan aufgelöst. Dadurch
  bleiben Zuordnung und Reihenfolge auch bei mehreren Räumen und Geräten
  stabil.

## Dokumentation und Freigabe

- Die Dokumentation wurde bereinigt und an die aktuelle Laufzeitarchitektur
  und Fehlerbehandlung angepasst.
- Der Release-Workflow prüft Manifest, Release Notes, Tag-Freiheit und alle
  neun verpflichtenden CI-Jobs gegen exakt denselben vollständigen Commit-SHA,
  bevor der GitHub-Release erstellt wird.

## Verifikation

Der Release-Kandidat wurde unter Python 3.12 und 3.13 sowie mit Hassfest,
HACS, Home-Assistant-Smoke- und Options-Flow-Tests geprüft. Zusätzlich liefen
die Sprach-, Learning-Center- und Live-Testbed-Suiten erfolgreich durch.
