# NLU-Roadmap Phase 4: erster migrierter Ausschnitt

Diese Änderung ist ausdrücklich **kein Abschluss von Phase 4**. Sie führt eine
typisierte Routergrenze für eine kleine Capability-Matrix ein und lässt die
bestehenden Compilerpfade als Compatibility Adapter aktiv.

## Aktueller Einstieg

`conversation.py` erzeugt pro Turn ein gemeinsames `LanguageDocument`. Der neue
`automation_turn_router` erhält genau dieses Dokument sowie die bereits
aufgenommenen Entity-Snapshots. Er führt keine zweite Frontend-Analyse durch
und darf selbst keinen Service- oder Automationsplan erzeugen.

Die erste autoritative Matrix umfasst unvollständige zukünftige beziehungsweise
solare Automationsfragmente. Sie projiziert typisierte Slots für:

- Datum, Uhrzeit, Sonnenereignis und Vor-/Nach-Offset
- Bedingung, Dauer, Zustand, Aktion und Ziel
- Ausnahme, Wiederholung und Gültigkeitszeitraum

Für einen zukünftigen Zustand ohne Aktionsprädikat erzeugt sie eine gezielte
Rückfrage nach den fehlenden Slots. „Morgen nach Sonnenuntergang noch warm.“
fragt beispielsweise nach Ziel, gewünschter Aktion und Bedingung und legt
nichts an. Negierte, hypothetische, irreale und überlegende Formen werden in
dieser Matrix als nicht ausführbar markiert. Explizite Fragen verbleiben im
Query-Pfad.

Metamorphische Tests decken Wortstellung, Höflichkeit, Füllwörter,
Wunschformen, Zahlwort/Ziffer, Datumsvarianten, Sonnenoffsets, Negation,
Hypothese, bedeutungstragende Zusätze, Mehrdeutigkeit und fehlende Slots ab.

## Noch nicht migriert

Die folgenden Bereiche sind weiterhin Compatibility-/Legacy-Pfade und daher
nicht als autoritativ migriert zu bezeichnen:

- der vollständige Automationscompiler einschließlich `match_automation()` und
  seiner internen erneuten Sprachanalyse
- relative, wiederkehrende, persistente, Kalender-, Reminder- und
  Mehrklausel-Automationen
- Automation-Management, Monitoring, Routinen, Aliaslernen und Produktivität
- Query- und Gerätecontroller mit eigenen Rohtext- oder Regex-Gates
- einzelne Reihenfolgeentscheidungen in der Conversation-Kaskade

Diese Pfade werden erst entfernt, wenn ein Shadow-Vergleich für die jeweilige
Capability-Matrix Parität zeigt. Bis dahin entscheidet der neue Router nur in
seinem schmalen unvollständigen Zukunfts-/Sonnen-Ausschnitt; vollständige
Äußerungen werden unverändert an die vorhandenen Adapter weitergegeben.

## Sicherheitsinvarianten

Der migrierte Router kann nur `NOT_APPLICABLE`, `COMPATIBILITY`, `INCOMPLETE`
oder `UNSAFE` liefern. Nur `INCOMPLETE` öffnet einen Rückfragedialog. Er besitzt
keinen Ausführungspfad. Queries, Negation, Hypothesen, unvollständige Sätze,
unbekannte bedeutungstragende Zusätze und mehrdeutige Ziele dürfen dadurch
keinen reduzierten Schreibplan erhalten. Das globale Benchmark-Gate verlangt
weiterhin exakt null unsichere Ausführungen und null Wrong-Target-/Safety-
Fehler.
