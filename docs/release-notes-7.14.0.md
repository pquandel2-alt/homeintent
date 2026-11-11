# HomeIntent 7.14.0

7.14.0 verbessert Geräte-Rücknahmen und Medienfortsetzungen und ergänzt eine
offizielle Einstellung für kürzere Erfolgsantworten. HomeIntent bleibt lokal,
deutschsprachig und deterministisch; es gibt kein Laufzeit-LLM.

## Zustandsgebundenes Geräte-Undo

- Einzelaktionen, bestätigte Aktionen, vollständige und teilweise Batches
  sowie Mehrbefehle binden jede angebotene Rücknahme an den tatsächlich
  beobachteten Nachzustand.
- Zustand, Attribute, `last_updated` und HA-Context-ID gehören zur
  unveränderlichen Signatur. Fehlende oder unvollständige Belege sperren die
  Rücknahme.
- Verzögert fahrende Cover binden ihren exakten Endzustand im Hintergrund an
  die ursprüngliche HA-Context-ID. Fremde Zustandsereignisse aktivieren keinen
  alten Undo-Plan und die gesprochene Antwort wird dafür nicht verzögert.
- Vor Beginn und unmittelbar vor jedem inversen Aufruf wird erneut geprüft.
  Spätere Änderungen durch Benutzer, Automationen oder Dashboards werden nicht
  überschrieben.
- Teilbatches enthalten ausschließlich nachweislich erfolgreiche Ziele. Ein
  zweiter Undo-Aufruf kann keinen bereits verbrauchten Plan wiederholen.

Geräteaktionen werden dadurch nicht atomar oder garantiert rückrollbar. Die
Korrektur verweigert eine unsichere spätere Rücknahme; sie macht bereits
ausgeführte Hardwareaktionen nicht ungeschehen.

## Semantische Medienfortsetzungen

- Offene Medienaufträge führen Player, Quelle, Medientyp,
  Bibliotheksbeschränkung und Queue-Modus typisiert mit der Fortsetzung
  zusammen.
- `replace`, `next` und `add` bleiben unterscheidbar. Eine reine Player- oder
  Ortsauswahl erbt den vorhandenen Modus; eine explizite Queue-Anweisung
  ändert ihn.
- Negation, Zeitbedingungen, unbekannter Rest und Abbruch schreiben weiterhin
  nichts. Nicht sicher abbildbare Queue-Semantik bei normalen
  `media_player`-Quellen wird abgelehnt statt still verworfen.

## Antwortdetails

Die neue Option `response_detail_level` unterstützt:

- `concise`: reine Erfolge ohne redundante Geräteanzahl, zum Beispiel
  „Ambiente eingeschaltet.“;
- `detailed`: die bisherige Form mit Anzahl, zum Beispiel
  „Ambiente eingeschaltet: 3 Lichter.“.

Neue Installationen verwenden `concise`. Bestehende Installationen ohne den
neuen Schlüssel behalten aus Kompatibilitätsgründen `detailed`. Die Option
verändert nur die Darstellung vollständiger Erfolge, niemals Plan, Zielmenge,
Batch-Ergebnis, Audit oder Undo. Teilfehler, vollständige Fehler, unbestätigte
Wirkungen, Statusfragen und Bestätigungsfragen werden nicht verkürzt.

## Prüfung und Grenzen

Die unabhängigen Ausgangstests reproduzierten auf 7.13.4 sechs Fehler. Auf dem
korrigierten Arbeitsstand bestanden 119 direkte Regressionen, 151 gemeinsame
Struktur-/Sprachtests und 647 lokale Nightly-/Property-Tests mit festem Seed.
Pyflakes und Pyright waren grün; 25 Tests liefen mit echten
Home-Assistant-2026.9.2-Komponenten.

Die vollständige lokale Suite hatte an einem Implementierungszwischenstand
10.692 bestandene, zwölf übersprungene und drei fehlgeschlagene Tests. Ein
Inventarfehler wurde danach behoben; zwei Effect-Wait-Zeitgrenzen waren unter
Last rot und bestanden anschließend isoliert. Unveränderte lokale
Performance-Gates außerhalb der geänderten Pfade lagen teilweise über Budget;
Grenzen wurden nicht angehoben. Die Veröffentlichung verlangt deshalb
zusätzlich die vollständige grüne CI und den grünen Pflicht-Nightly-Lauf für
exakt denselben Release-Commit.

Die automatisierten Medienprüfungen verwenden HA-Service-Doubles. Der echte
Music-Assistant-Komponentenvertrag wurde geprüft, aber kein echter
Music-Assistant-Server, Lautsprecher und keine Gerätehardware.

Details: [Umsetzungsbericht](umsetzung-next-reliability.md).
