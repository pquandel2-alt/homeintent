# HomeIntent 7.15.1

7.15.1 erweitert die deterministische Music-Assistant-Unterstützung um
natürlichere deutsche Wortfolgen und eine sichere Zuordnung nativer
Sonos-Entities zu Music-Assistant-Playern. HomeIntent bleibt lokal und nutzt
weiterhin kein LLM oder ML-Modell zur Laufzeit.

## Natürliches, kompositionelles Medienverständnis

- Aktion, freie Quelle und geerdeter Player beziehungsweise Bereich werden
  unabhängig erkannt und anschließend als ein typisierter PLAY-Auftrag
  zusammengeführt. Es wurden keine Listen vollständiger Befehlssätze ergänzt.
- Unterschiedliche Wortfolgen wie „Spiele 1Live im Studio“, „Im Studio bitte
  WDR zwei“, „Ich möchte dort Radio Energy hören“ und „Lass dort Eins Live
  laufen“ verwenden denselben Bedeutungsweg.
- Kompakte alphanumerische Namen wie `1LIVE`, `WDR2` und `rbb24` bleiben ein
  Token. Ausgeschriebene Zahlen werden weiterhin nur über die bereits
  begrenzte exakte Schreibäquivalenz aufgelöst.
- Die Coverage-Grenze erhält die belegten Quellpositionen aus dem Parser.
  Unbekannte Räume, zweite Klauseln und sonstige Satzreste können dadurch
  weder Teil des Sendernamens werden noch unbemerkt verschwinden.

## Sichere Sonos-/Music-Assistant-Zielauflösung

- Direkt geerdete Music-Assistant-Player, eindeutig benannte Bereiche und die
  vorhandene Satellitenzuordnung bleiben die stärkste Evidenz.
- Wird ausdrücklich eine native Sonos-Entity genannt, ordnet HomeIntent sie
  nur dann einem MA-Player zu, wenn genau ein verfügbarer MA-Player im selben
  Home-Assistant-Bereich existiert.
- Fehlt die Bereichsevidenz oder gibt es mehrere Gegenstücke, fragt HomeIntent
  nach beziehungsweise führt nichts aus. Eine Zuordnung über ähnliche Namen,
  Reihenfolge oder einen anderen Bereich findet nicht statt.

## Schutz bestehender Bedeutungen

- Resume-Formen wie „weiterspielen“, „weiterzuspielen“ und „weiterspielt“ sind
  Transportoperationen und werden nicht als Bibliothekstitel interpretiert.
- Umgangssprachliche Lautstärkeformen wie „Dreh das Radio runter“ bleiben beim
  bestehenden Gerätepfad.
- „Starte die Wiedergabe“ bleibt ein generischer Transportbefehl; Routinen,
  Zustandserhaltung und Automationsformulierungen werden nicht vom freien
  Medienparser übernommen.

## Prüfung und Grenzen

Die vollständige Testsuite wurde lokal abschnittsweise bis zum Ende geprüft;
alle nach den dabei gefundenen Kollisionen betroffenen Medien-, Geräte- und
Domainmatrizen wurden erneut ausgeführt. Vollständiges Pyright meldete null
Fehler und null Warnungen, Pyflakes und die Architektur-/Regex-Gates blieben
grün. Der Entwicklungsbenchmark blieb bei 478/516, davon 109/117 Held-out;
`unsafe_execution_count` und sämtliche Safety-Zähler blieben null.

Die automatisierten Music-Assistant-Tests verwenden reale Home-Assistant-
Verträge beziehungsweise kontrollierte Service-Fakes. Ein echter
Music-Assistant-Server und die physische Sonos-Wiedergabe wurden nicht
angesteuert und werden nicht behauptet. Veröffentlichung ist erst nach grüner
CI und grünem Pflicht-Nightly-Lauf für exakt den Release-Commit zulässig.
