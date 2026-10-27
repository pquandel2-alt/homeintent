# HomeIntent 7.13.0

HomeIntent 7.13.0 behebt den von HACS gemeldeten Event-Loop-Blocker und die
Autorisierungs- und Binding-Lücke von `proactive_message`. Darüber hinaus
werden die nachvollziehbaren Fälle aus Issue #38 über gemeinsame semantische
Modelle statt über Satzschablonen abgedeckt.

## Änderungen

- Sämtliche HomeIntent- und Legacy-Speicherpfade werden beim Setup in einem
  Executor-Job aufgelöst und als unveränderliche Laufzeitdaten injiziert.
  Konstruktoren und Requestpfade prüfen oder migrieren keine Dateien mehr.
- Öffentliche ASK-/AUTO-Proaktivnachrichten benötigen einen authentifizierten
  Administrator. Interne Automationen verwenden einen zufälligen, widerrufbaren
  und exakt an Automation, Aktion und Zielmenge gebundenen Nachweis. Generische
  Unlock-/Disarm-Vorschläge sind gesperrt; Push-Texte nennen die echte Aktion.
- Bereiche, Bereichs- und Etagenaliase, Innen/Außen, Gesamthaushalt und
  Satellitenbereich verwenden dasselbe Place-Modell. Erkannte Orte sind harte
  Einschränkungen und werden durch zusätzliche Entitäten nicht erweitert.
- Komposita, Flexionsformen, Füllwörter, elliptische Lichtwerte und begrenzte
  LIST-Antworten werden kompositionell verarbeitet. Umgangssprachliche
  Skriptanforderungen bleiben an exakte, freigegebene Skriptnamen gebunden.
- Skriptläufe unterscheiden abgelehnt, angenommen, laufend, beendet,
  fehlgeschlagen und Zeitüberschreitung. Nur über den öffentlichen direkten
  Home-Assistant-Skriptdienst beobachtete Abschlüsse gelten als beendet.
- Unabhängige, risikoarme Licht-/Cover-Sammelaktionen werden vollständig
  vorgeprüft und pro Ziel ausgeführt. Antwort, Audit und Undo enthalten nur
  tatsächlich erfolgreiche Teilaktionen; kritische Aktionen bleiben atomar.
- Medienbefehle verwenden dasselbe Ortsmodell und die gemeldeten
  Player-Fähigkeiten. Sender werden ausschließlich aus belegten Quellen oder
  Music-Assistant-Favoriten eindeutig aufgelöst; Sonos wird ohne belegtes
  `TURN_OFF` pausiert oder gestoppt.

## Verifikation und Grenzen

Die Release-Freigabe setzt die vollständige CI für den exakten Release-Commit
voraus: Python 3.12/3.13, Coverage, Pyright-Profile, Pyflakes, HACS, Hassfest,
Home-Assistant-Smoke-/Komponententests, Music-Assistant-Verträge, Sprach- und
OOD-Gates sowie die 5.000-Entitäten-Benchmarks. Die endgültigen Lauf-Links
stehen beim GitHub-Release.

Sonos und Music Assistant sind gegen Home-Assistant-Verträge und realistische
Fakes geprüft. Ein echter Sonos-Lautsprecher und ein echter Music-Assistant-
Server mit hörbarer Ausgabe waren nicht Teil der automatisierten Prüfung.
Mocks belegen daher keine reale Hardwarewiedergabe.

## Korrektur (nachgetragen mit 7.13.1)

- Die Aussage zu unabhängigen **Cover**-Sammelaktionen traf auf 7.13.0 nicht
  zu: Die partielle Ausführung lief nur bei `RiskLevel.LOW`; Cover sind
  `MEDIUM` und fielen auf einen gemeinsamen Service Call zurück. Erst 7.13.1
  führt Standard-Rollläden tatsächlich pro Ziel aus.
- Licht-Sammelaktionen meldeten Geräte, die erst kurz nach dem Service Call
  bestätigten, als „nicht verifiziert“ und nahmen sie aus dem Undo. Behoben in
  7.13.1.
- Die normale CI von `324cfb8` war grün
  ([Lauf 40457654872](https://github.com/pquandel2-alt/homeintent/actions/runs/40457654872)),
  der Nightly-Sicherheitslauf desselben Commits jedoch fehlgeschlagen
  ([Lauf 40470216752](https://github.com/pquandel2-alt/homeintent/actions/runs/40470216752)):
  „Mach das Radio in der Küche aus und dann Quirlomat den Quirlomat.“
  pausierte das Radio. Das Release-Gate prüfte den Nightly-Lauf nicht. Beides
  ist in 7.13.1 behoben.
