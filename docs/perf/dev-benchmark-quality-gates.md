# Versionierte Qualitätsgrenzen des Entwicklungsbenchmarks

`scripts/dev_benchmark.py --check` liest die zum Release gehörende Datei
`dev-benchmark-quality-gates-7.13.2.json`. Die Grenzen wurden nach der
Cover-Sicherheitskorrektur aus vollständigen Läufen übernommen. Die vier
Markisenfälle im Standardkorpus und die zwei Markisenfälle im 7.8-Korpus
erwarten nun die tatsächlich vorgeschriebene Bestätigung statt eines direkten
Writes. Dadurch bleiben 478/516 beziehungsweise 103/107 Fälle bestanden und
alle Safety-Zähler exakt null. Das Skript besitzt
absichtlich keinen Schalter, der diese Datei aus einem aktuellen,
möglicherweise schlechteren Lauf erzeugt.

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

Die einzige numerisch niedrigere Nicht-Safety-Grenze ist
`dimensions.no_write.passed` im 7.8-Korpus (94 → 93). Das ist keine
Produktverschlechterung: Zwei zuvor als direkte Writes gezählte Fälle wurden
in die separate Bestätigungsdimension verschoben, wodurch der Nenner von 96
auf 94 sank. Die fallgenaue Begründung steht in
[`7.13.2-cover-confirmation.md`](regression-justifications/7.13.2-cover-confirmation.md).
