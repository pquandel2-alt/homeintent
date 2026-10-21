# Korpus-Signaturen 7.10.0 – Begründungsliste

Die neue Baseline `corpus-signatures-7.10.0.json` ersetzt 7.9.11 im CI-Gate.
Sie enthält 4.153 Signaturen gegenüber 4.107 in 7.9.11: 46 neue Einträge,
keinen entfallenen Eintrag und keine Änderung an einem bereits in der
7.9.11-Baseline enthaltenen Satz.

Für einen zusätzlichen Alt/Neu-Vergleich wurden alle 4.152 damals gemeinsamen
Sätze mit dem unveränderten 7.9.11-Code und dem neuen Code ausgewertet. Genau
zwei Engine- und zwei IR-Signaturen änderten sich; beide gehören zu denselben
beiden neuen Regressionstexten:

| Satz | 7.9.11 | 7.10.0 | Begründung |
|---|---|---|---|
| „Fahre den Büro Rollladen 10 Prozent weiter hoch.“ | absolute Position 10 | relative Erhöhung aus der beobachteten Position, auf 100 begrenzt | „weiter hoch“ bezeichnet Richtung plus relative Änderung, nicht den absoluten Zielwert 10. |
| „Fahre den Büro Rollladen etwas weiter herunter.“ | vollständig schließen | relative Verringerung aus der beobachteten Position | Der Grad „etwas“ darf nicht als vollständiges Schließen verloren gehen. |

Die übrigen neuen Einträge decken ab:

- benutzer- und satellitenisolierten Dialogkontext sowie sichere Pronomen- und
  Ortsrückbezüge,
- belegte vorherige Heizungsstellwerte und fail-closed Verhalten ohne Evidenz,
- kausale Erklärungen ohne erfundene Ausführung,
- optionale Music-Assistant-Suche, Medientypen, Queue-Modi, Transportbefehle,
  Player-/Medienmehrdeutigkeit und sichere Fehlerantworten,
- Test-Docstrings und feste Ausgabetexte, die der Korpussammler absichtlich als
  satzartige Literale erfasst.

Die produktive Erkennung verwendet dafür keine neuen vollständigen
Satzschablonen als Hauptlogik. Das klassifizierte Inventar bleibt bei 171
`SEMANTIC_SENTENCE_PATTERN`-Einträgen. Shadow- und Unsafe-Gates bleiben
unverändert blockierend; ihre tatsächlichen Release-Ergebnisse werden erst für
den geprüften Release-Commit berichtet.
