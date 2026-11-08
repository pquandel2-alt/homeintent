# HomeIntent 7.13.4

7.13.4 korrigiert die in der unabhängigen Nachprüfung von 7.13.3
reproduzierten Zuverlässigkeitsfehler. Die Verarbeitung bleibt deutschsprachig
und deterministisch; es gibt kein Laufzeit-LLM und keine zweite Dialogarchitektur.

## Sprachverständnis und Dialogzustand

Medienauswahl, Ortskorrekturen und Music-Assistant-Fortsetzungen prüfen die
vollständige Antwort. Negation, Zeitbedingungen und unverstandene zusätzliche
Klauseln autorisieren keine sofortige Teilaktion. Abbruch und Ablehnung
verwerfen den Entwurf; ein späteres „Ja“ aktiviert ihn nicht erneut. Gültige
Auswahlantworten, Auftragswechsel sowie Quellen- und Queue-Erhalt bleiben möglich.

Das vorhandene Dialoggedächtnis unterscheidet Vorschau, Ablehnung,
Ausführungsversuch, Dienstannahme, beobachtete Wirkung und Fehler. Eine
Quellenauswahl ist keine bestätigte Wiedergabe; ein laufender Titel bestätigt
keine Änderung der Warteschlange.

## Vorprüfung, Teilfehler und Rücknahme

Mehrbefehle prüfen alle Klauseln gemeinsam vor dem ersten Write. Der Executor
verwendet dieselben Prüfbausteine unmittelbar vor jeder Ausführung erneut:
Ziele, Verfügbarkeit, bekannte Fähigkeiten, Service-Schema, Rechte, Policy,
Bestätigungsscope und bekannte transitive Einschränkungen.

Teilweise erfolgreiche Batches werden anhand ihrer einzelnen Ergebnisse
beantwortet. Ein abbrechender Fehler stoppt spätere Klauseln, auch nach einer
Bestätigung. Unabhängige Geräte innerhalb eines Batches behalten ihre Regeln.
Nach einem abgebrochenen Mehrbefehl bleibt kein aktivierbarer Rest- oder Undo-Plan.
Rücknahmen erfolgreicher Teile eines einzelnen Batches sind an beobachtete
Zustände gebunden und überschreiben keine späteren Änderungen.

Der Klauselvalidator bestimmt zulässige Medienziele unabhängig aus der
Klauselbedeutung und prüft Operation, Zielmenge und Parameter gegen den Plan.
Der ursprüngliche falsche-TV-Befund war ein isolierter Validator-Test, kein
Nachweis eines solchen Plans im normalen Sprachpfad.

## Nachweise und Grenzen

Vor der Korrektur scheiterten zwölf unveränderte unabhängige Regressionen.
Auf dem korrigierten Produktstand bestanden lokal 10.656 Tests bei zwölf
bereits vorhandenen Skips, 97 gezielte Regressionen sowie 26 Tests mit echten
Home-Assistant-Komponenten. Typ-, Import- und Exception-Inventarprüfungen
bestanden. Der Entwicklungskorpus erreichte 478/516 Fälle bei null unsicheren
Ausführungen; der Arbiter meldete keine Sicherheitsabweichung.

Das allgemeine lokale 100-ms-p95-Latenzgate war auf dem stark ausgelasteten
Rechner sowohl auf der Baseline als auch auf dem Arbeitsstand rot. Es wurde
nicht gelockert. Für die Veröffentlichung müssen sämtliche verpflichtenden
CI- und Nightly-Jobs auf dem exakten Release-Commit erfolgreich sein; ihre
Nachweise werden durch den bestehenden Release-Workflow angefügt.

Simulierte Serviceaufrufe und echte HA-Komponentenprüfungen sind keine
Hardware- oder Audiowiedergabetests. Zeitversetzte Medienfortsetzungen bleiben
nicht sicher unterstützt. Bereits angenommene Hardwareaktionen sind nicht
atomar rücknehmbar. Vollständige Fehlerfreiheit wird nicht behauptet.

Details: [Reproduktion, Ursachen und lokale Prüfung](reliability-7133-review.md).
