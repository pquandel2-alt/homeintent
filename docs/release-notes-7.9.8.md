# HomeIntent 7.9.8

7.9.8 korrigiert verbleibende Zustellungs- und Diagnosefehler der in 7.9.7
gehärteten EventRuntime. Es ändert weder NLU noch Ausführungs- oder
Safety-Regeln.

## Korrekturen

- History- und Registry-Degradation sind getrennte Zustände. Ein
  unvollständiger globaler Verlauf deaktiviert konkrete geschützte Consumer
  nicht mehr pauschal.
- Aktive Thermal-Zyklen erhalten eine begrenzte, aus Snapshot, Batch und
  Restqueue zeitlich korrekt rekonstruierte Sicht auf Climate-, Temperatur-,
  Außen- und Fenster-Entities.
- Wenn die Historie einer konkret geschützten Entity selbst unsicher ist,
  wird der Consumer nicht mit geratenen Daten aufgerufen. Die Unterdrückung
  wird nach Grund, Consumerklasse und Eventpriorität gezählt und
  rate-limitiert protokolliert.
- ExpectedEffects sowie sichere Personen- und Werte-Monitore bleiben im
  History-Modus zustellbar. Nobody-home-Ziele erhalten eine zeitlich korrekte,
  begrenzte Sicht auf alle ausgewählten Haushaltspersonen.
  Hauskontextabhängige Monitor-/Proactive-Pfade werden nur mit nachweislich
  sicherem Kontext ausgeführt.
- `processed` bezeichnet jetzt ausdrücklich den abgeschlossenen
  eventbezogenen Dispatch, einschließlich bewusster Suppression, aber nicht
  eines Registry-Discards. Separate Delivery-, Suppression-,
  Registry-Discard- und Queue-Drop-Zähler verhindern einen falschen
  Zustellungseindruck.
- `filtered_by_category`, `filtered_by_proactive` und
  `filtered_no_consumer` sind disjunkte Filterursachen.
- `worker_restarts` zählt nun ausschließlich einen echten automatischen
  Ersatz nach unerwartetem Worker-Ende mit ausstehender Arbeit;
  `worker_starts` zählt jeden angelegten Worker.
- `long_running_state` ist end-to-end angebunden. Die neue Option
  `agent_long_running_minutes` ist standardmäßig 0 (deaktiviert), im Options
  Flow validiert und verhindert dadurch überraschende Meldungen bestehender
  Installationen.

## Kompatibilität und Grenzen

Bestehende Installationen werden nicht migriert und erhalten keine neue
Langläufermeldung, solange die Schwelle nicht ausdrücklich gesetzt wird. Die
Single-Worker-Architektur, die begrenzte Queue und der I/O-freie synchrone
Callback bleiben erhalten.

Unter beliebiger Überlast wird keine Verlustfreiheit versprochen. Critical
und konkrete Protected-Events verdrängen niedrigere Ränge; an einer komplett
gleich- oder höherrangig gefüllten Queue kann auch ein wichtiges Event
verloren gehen. Das wird als Queue-Drop gezählt. Kann ein Consumer wegen
unsicherer Historie oder fehlender Registry-Daten nicht korrekt arbeiten,
wird er stattdessen ausdrücklich unterdrückt und separat diagnostiziert.

Technische Details, Metriksemantik und Testergebnisse stehen in
[`umsetzung-7.9.8.md`](umsetzung-7.9.8.md).
