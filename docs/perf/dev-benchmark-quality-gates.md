# Versionierte Qualitätsgrenzen des Entwicklungsbenchmarks

`scripts/dev_benchmark.py --check` liest die zum Patch gehörende Datei
`dev-benchmark-quality-gates-7.10.1.json`. Die Grenzen wurden nach Reparatur
des Conversation-Scope-Lookups aus vollständigen Läufen beider unveränderter
Korpora übernommen. Das Skript besitzt absichtlich keinen Schalter, der diese
Datei aus einem aktuellen, möglicherweise schlechteren Lauf erzeugt.

Die exakten Safety-Grenzen dürfen nicht gelockert werden. Dazu gehören
unerwartete Schreibzugriffe, Ausführung statt Bestätigung, falsche Operation,
falsches Ziel, die zusammengefasste Unsafe-Zahl und die Fehlerklasse
`SAFETY`; sie bleiben alle exakt null.

## Kontrollierter Updateprozess

1. Beide Korpora vollständig mit JSON-Ausgabe laufen lassen und jeden neuen
   Fehler einzeln als Harness-, Erwartungs- oder Produktverhalten einordnen.
2. Korpusänderungen und ihre Signaturen separat prüfen; niemals einen Fehler
   durch Entfernen oder Umklassifizieren eines Falls verdecken.
3. Verbesserte Mindestwerte dürfen direkt angehoben werden.
4. Eine fachlich beabsichtigte Verschlechterung einer Nicht-Safety-Dimension
   benötigt vor der Änderung eine konkrete Markdown-Datei unter
   `docs/perf/regression-justifications/`. Sie nennt Metrik, alten und neuen
   Wert, betroffene Fälle, fachlichen Grund, Review und Commit. Die Baseline
   wird anschließend manuell in demselben Review geändert.
5. Für Safety-Dimensionen gibt es keine Ausnahme. Eine Verschlechterung
   blockiert den Merge und den Release.

Die Baseline ist damit ein geprüftes Eingabedokument, kein automatisch
aktualisiertes Testergebnis.
