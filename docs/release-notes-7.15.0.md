# HomeIntent 7.15.0

7.15.0 schließt einen Rennfall bei mehrstufigem Geräte-Undo und verbessert die
deterministische Auflösung natürlich gesprochener Radiosender in Music
Assistant. HomeIntent bleibt lokal, deutschsprachig und ohne LLM oder
ML-Modell zur Laufzeit.

## Rennfestes, evidenzgebundenes Geräte-Undo

- Ein unveränderlicher Restplan trennt ursprüngliche Evidenz, noch offene,
  erfolgreich abgeschlossene, abgebrochene und übersprungene Schritte.
- Der Zustand eines noch unberührten Ziels wird nach einem vorherigen inversen
  Teilschritt nicht neu gebunden. Fremde Änderungen können dadurch nicht als
  neue Undo-Evidenz übernommen werden.
- Sämtliche Schritte werden vor dem ersten Write vorgeprüft. Zustand,
  Berechtigung, Policy, Service und Verfügbarkeit werden vor jedem Schritt
  erneut geprüft; die Evidenzbedingung wird zusätzlich unmittelbar an der
  zentralen Executor-Grenze geprüft.
- Mehrere inverse Schritte desselben Ziels benötigen kausal passende
  Context-Evidenz. Teilweise oder abgebrochen ausgeführte Undo-Pläne sind
  endgültig verbraucht und erzeugen eine ehrliche Teilantwort.
- Verschachtelte Zustandsattribute werden unveränderlich und typgetreu in die
  Signatur aufgenommen.

Home Assistant bietet keinen atomaren Compare-and-swap-Dienst für physische
Geräte. Die erneute Prüfung minimiert das verbleibende Zeitfenster, kann aber
keine Hardwareatomizität garantieren.

## Natürlichere Radiosender über Music Assistant

- Ausgeschriebene deutsche Zahlen von null bis zwanzig werden ausschließlich
  als begrenzte Schreibäquivalenz behandelt. Dadurch kann ein exakter
  Bibliothekseintrag `1LIVE` zu „eins live“ und `WDR 2` zu „WDR zwei“ passen.
- `Radio X` wird zuerst als vollständiger Name gesucht. Nur ohne exakten
  Treffer wird `X` einmal als Medientyp Radio gesucht. Es gibt keine
  fest eingebaute Senderliste und keine unscharfe oder phonetische Suche.
- Alle Suchvarianten teilen dasselbe Zeitbudget. Nur genau ein sicherer,
  äquivalenter Music-Assistant-Treffer mit nicht webbasierter Media-ID wird
  ausgeführt.
- Mehrdeutigkeiten werden nach Medientyp zusammengefasst. Mehrere Treffer
  desselben Typs werden nicht geraten, sondern sicher abgelehnt.
- Ein neuer vollständiger Medienbefehl ersetzt einen noch offenen
  Medientypdialog, statt irrtümlich als dessen Antwort behandelt zu werden.

Welcher Lautsprecher verwendet wird, entscheidet weiterhin die eindeutige
Player-Auflösung aus dem ausgesprochenen Gerätenamen beziehungsweise Bereich
und dem offenen Dialogkontext. HomeIntent erfindet keine Playerzuordnung.

## Lokale Prüfung und Grenzen

Für den Undo-Pfad bestanden 277 verbreiterte Regressionstests sowie die
vollständige Suite mit 10.721 bestandenen und zwölf übersprungenen Tests. Nach
der Radiosender-Erweiterung bestanden 317 relevante Music-Assistant-, Medien-,
Dialog- und Koordinationstests. Pyflakes und vollständiges Pyright waren grün.
Der Entwicklungsbenchmark blieb bei 478/516, davon 109/117 Held-out;
`unsafe_execution_count` und sämtliche Safety-Zähler blieben null. Corpus- und
Identity-Shadow meldeten keine Verhaltens- oder Sicherheitsdrift. Das lokale
Music-Assistant-5.000-Entity-Budget lag mit p95 6,778 ms unter 100 ms.

Die vollständige Suite wurde nach der lokal begrenzten Radiosender-Änderung
nicht erneut ausgeführt. Deshalb sind eine grüne CI und ein grüner Pflicht-
Nightly-Lauf für exakt den Release-Commit zwingend. Die Tests verwenden
realistische Service-Doubles; ein echter Music-Assistant-Server, Sonos-
Lautsprecher und physische Audiowiedergabe wurden nicht angesteuert.

Die in der Roadmap ausgewiesenen späteren NLU-, Architektur-, UX-, Draft- und
Diagnosephasen bleiben offen. Details:
[Zuverlässigkeits-Roadmap](reliability-roadmap-progress.md) und
[Music-Assistant-Dokumentation](music-assistant.md).
