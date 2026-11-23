# HomeIntent 7.15.2

7.15.2 korrigiert die Zielbindung natürlicher Playernamen in der seit 7.15.1
kompositionellen Music-Assistant-Route. HomeIntent bleibt lokal,
deterministisch und verwendet weiterhin kein LLM oder ML-Modell zur Laufzeit.

## Behobener Fehler

- Der gemeinsame Feldleser kann in „Studio Lautsprecher“ zwei belegte Felder
  liefern: den exakten Registry-Namen `Studio` und die Geräteart
  `Lautsprecher`. Die vollständige Medienabdeckung zählte beide bisher als
  getrennte Ziele und lehnte den sicheren Medienpfad deshalb ab.
- Ein direkt angrenzendes Mediengenus qualifiziert jetzt den exakten
  Registry-Player und erzeugt kein zweites Ziel. Dadurch bleiben sowohl
  „Spiele 1LIVE auf Studio“ als auch „Spiele Radio 1LIVE auf dem Studio
  Lautsprecher“ ein vollständiger typisierter PLAY-Auftrag.
- Die Korrektur ist strukturell und enthält weder fest codierte Sendernamen
  noch Listen vollständiger Befehlssätze.

## Unveränderte Sicherheitsgrenzen

- Zwei tatsächlich genannte Registry-Player bleiben zwei Ziele und werden
  nicht durch die neue Regel zusammengeführt.
- Ein ausdrücklich genannter nativer Sonos-Player wird weiterhin nur dann auf
  Music Assistant abgebildet, wenn genau ein verfügbarer MA-Player im selben
  Home-Assistant-Bereich existiert.
- Native Sonos- und Music-Assistant-Entities müssen für Assist beziehungsweise
  in einer festen HomeIntent-Geräteauswahl freigegeben sein. Eine fehlende
  Freigabe wird nicht durch implizite Zielwahl umgangen.
- Unbekannte Satzreste, zweite Klauseln und unvollständige Zielbelege bleiben
  fail-closed und führen zu keiner Ausführung.

## Prüfung und Grenzen

Vor dem Release-Kandidaten bestanden 1.208 Medien-, Geräte-, Coverage- und
Routingtests sowie 463 Sprach-Sicherheitstests. Pyright meldete null Fehler
und null Warnungen, Pyflakes blieb ohne Befund. Der Vergleich von 4.386
Korpussignaturen ergab null Änderungen; Identity-Shadow war mit 2.131/2.131
äquivalent und `SAFETY_DRIFT=0`. Der Entwicklungsbenchmark blieb bei 478/516,
davon 109/117 Held-out; `unsafe_execution_count` und alle Safety-Zähler waren
null.

Die automatisierten Music-Assistant-Tests verwenden reale Home-Assistant-
Verträge beziehungsweise kontrollierte Service-Fakes. Ein echter
Music-Assistant-Server und eine physische Sonos-Wiedergabe wurden nicht
angesteuert und werden nicht behauptet. Veröffentlichung ist erst nach grüner
CI und grünem Pflicht-Nightly-Lauf für exakt den Release-Commit zulässig.
