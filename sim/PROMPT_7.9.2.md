# Umsetzungsauftrag HomeIntent 7.9.2

Repository `pquandel2-alt/homeintent`.

- **Ausgangsstand:** 7.9.1, Zweig `claude/homeintent-7.9.1`, Commit `3988ac8`.
  Ist `main` inzwischen auf 7.9.1, nimm `main`.
- **Grundlage:** Nachtestbericht `docs/nachtest-7.9.1.md` im Zweig
  `claude/sleepy-meitner-xd7oux`.

```bash
git fetch origin claude/sleepy-meitner-xd7oux claude/homeintent-7.9.1 main
git show origin/claude/sleepy-meitner-xd7oux:docs/nachtest-7.9.1.md
```

**Entscheidungen des Projekteigentümers** (verbindlich):

- **T1:** Ein Gerät, das sich in die verlangte Richtung bewegt („fährt“), gilt
  als Erfolg und bekommt den Bestätigungston.
- **T2: Variante C.** Nur Wasser- bzw. Bewässerungsventile dürfen sich
  automatisch öffnen. Tore, Türen, Schlösser, Gas- und Hauptventile öffnen sich
  nie automatisch.
- **Umfang:** Alle weiteren Vorschläge aus dem Nachtest werden umgesetzt.
  **Nicht** Teil dieses Auftrags ist der Test mit dem echten Haus des
  Eigentümers.

## Harte Regeln (unverändert)

1. **Kein LLM, kein ML-Modell, kein probabilistisches Raten.** Alles lokal und
   deterministisch.
2. **Keine Satzlisten, keine Hardcode-Sonderfälle.**
   - Erkannt werden Konstruktionen.
   - Tests erzeugen Paraphrasen kombinatorisch.
   - Die Beispielsätze sind Prüfsätze, keine Muster.
3. **Die Sicherheitsgrenze bleibt:**
   - Grounding → Validator → gesprochene Vorschau → ausdrückliches „Ja“ →
     Schreiben.
   - `service_executor` bleibt der einzige Schreibweg.
   - Die Negations-Sperre wird nicht umgangen.
   - Zugangsregel und Eigentümerrechte aus 7.9.1 bleiben.
4. **Nachfragen statt raten.** Eine gesprochene Einschränkung wird nie still
   fallengelassen.
5. **Tests werden nicht abgeschwächt.** Geänderte Erwartungen werden im Test
   und im Releasebericht begründet.
6. **Keine Modellnamen** in Commits, Code oder Dokumenten. Commits auf Deutsch.
7. **Pushe auf deinen Arbeitszweig. Erstelle keinen Pull Request.**

---

## Teil A – Befunde aus dem Nachtest 7.9.1

### A1 (T1): „fährt“ ist ein Erfolg, und auf Rückmeldung wird kurz gewartet

- **Ist:** `service_executor._effect_reached` prüft den Zielzustand sofort nach
  dem Aufruf.
  - Echte Geräte (Zigbee, Z-Wave, Matter, WLAN) melden oft erst nach 0,1–2 s.
  - Rollläden und Markisen melden während der Fahrt `opening`/`closing`.
  - Folge: Der Ton kommt selten, und „Fahre den Rollladen runter“ wird immer
    gesprochen (Szenario `n791-b-partial`).
- **Soll:**
  - **Begrenzt warten:** Nach der Schreiboperation wartet HomeIntent auf
    `state_changed` der Ziele.
    - Das Warten ist ereignisgesteuert, kein Polling.
    - Es dauert höchstens 2 s und ist als Option einstellbar (z. B.
      `effect_wait_seconds`, Bereich 0–5).
    - Mehrere Ziele werden parallel abgewartet.
    - Die Antwort wird erst danach entschieden.
    - Auch im Stil `spoken` darf der Turn höchstens um diese Zeit länger
      dauern, und nur, wenn eine Wirkung noch aussteht.
  - **Was als Erfolg (`EXECUTED`) zählt:**
    - der Zielzustand;
    - eine Bewegung in die verlangte Richtung: `opening` bei „auf/hoch“,
      `closing` bei „zu/runter“, bei einer Position die Bewegung zur Zielposition;
    - bei Klima-Geräten der neue Sollwert im Attribut (die Raumtemperatur muss
      dafür nicht erreicht sein);
    - bei Mediaplayern der verlangte Zustand bzw. die verlangte Lautstärke.
  - **Was nicht als Erfolg zählt (`UNCONFIRMED` oder `NOT_DONE`, wird gesprochen):**
    - Gegenrichtung;
    - `unavailable` oder `unknown`;
    - keine Änderung innerhalb der Wartezeit.
  - Teilerfolg bleibt Teilerfolg: Ein Ziel ist bestätigt, ein anderes nicht.
- **Testbett:**
  - `haus_sim` bekommt eine einstellbare Melde-Verzögerung je Gerät bzw.
    Domain, z. B. Option `report_delay` in Sekunden.
  - Live-Szenarien:
    - Licht mit 0,5 s Verzögerung → Ton;
    - Licht mit 3 s Verzögerung → Sprache (unbestätigt);
    - Rollladen „runter“ während der Fahrt → Ton;
    - Rollladen fährt in die Gegenrichtung → Sprache.
  - Passe `n791-b-partial` mit Begründung an. Ein echter Teilerfolg-Fall bleibt
    erhalten.
- **Latenz:** Die Wartezeit zählt nicht zum Sprachverständnis-Budget. Miss sie
  getrennt (p50/p95 der zusätzlichen Wartezeit im Live-Lauf).

### A2 (T2, Variante C): Bewässerungsventile dürfen sich automatisch öffnen

- **Ist:** `nlu/automation_access.access_openings()` zählt alle Ventile zu den
  Zugängen.
- **Soll:**
  - **Ausgenommen** sind Ventile, die eindeutig Wasser für Garten, Bewässerung
    oder Beregnung führen:
    - `device_class: water` **und** zusätzlich ein Ontologie- bzw. Namenshinweis
      auf Bewässerung/Garten/Beregnung/Rasen/Beet/Tropf (über die vorhandene
      Ontologie, keine Satzliste);
    - oder ein Ventil der Ontologie-Gattung „Bewässerung“.
  - **Nie ausgenommen:**
    - Gasventile (`device_class: gas`);
    - Ventile mit Haupt-/Zuleitungsbezug („Hauptwasser“, „Zuleitung“, „Haupthahn“);
    - unbekannte Ventile ohne Klasse und ohne Hinweis (vorsichtshalber
      weiterhin Zugang);
    - Tore, Türen, Schlösser.
  - **Bewässerung nach Zeitplan muss verstanden werden** („Jeden Morgen um
    6 Uhr öffne die Bewässerung“, „… schalte die Bewässerung für 20 Minuten
    ein“, „… bewässere den Garten 15 Minuten“). Heute antwortet HomeIntent
    „kein eindeutig passendes Gerät“, obwohl Bewässerung und Hauptwasserventil
    existieren.
    - Mehrdeutigkeit führt zur Rückfrage.
    - „für N Minuten“ wird Öffnen + Verzögerung + Schließen. Das Schließen ist
      Teil derselben Automation und **Pflicht**: Eine Bewässerung ohne Ende wird
      nicht angelegt. Ohne genannte Dauer fragt HomeIntent „Wie lange?“ mit dem
      Rückfrage-Dialog aus 7.9.1.
  - **Die Vorschau sagt ausdrücklich,** dass sich das Ventil automatisch öffnet
    und wann es wieder schließt.
  - Die Regel bleibt in **einer** Funktion, die Validator, Vorschau und
    Schreibweg teilen.
- **Test:** Matrix aus Ventilklassen × Namen × Auslösern. Abwesenheit
  („wenn alle weg sind“) bleibt auch für Bewässerung erlaubt; sie ist kein
  Zugang. Gas und Hauptventil sind immer gesperrt.

### A3 (T3): Sprachgeräte ohne angemeldeten Benutzer

- **Ist:** Eine Sprachquelle ohne Benutzer, typischerweise ein Wand-Satellit,
  kann keine Überwachung und keine Automation verwalten.
- **Soll:**
  - Admin-Einstellung **„Sprachgeräte im Haus sprechen für den Haushalt“**
    (Optionsdialog, Standard aus).
  - Ist sie an, darf eine Sprachquelle ohne Benutzer, die zu einem Satelliten
    oder Gerät des Hauses gehört:
    - Überwachungen und Automationen verwalten, die als **gemeinsam** markiert
      sind (`owner = household`);
    - selbst gemeinsame Überwachungen anlegen, nur wenn
      `allow_non_admin_automations` erlaubt ist.
  - **Persönliche Überwachungen** eines Benutzers bleiben gesperrt.
  - **Markieren:** „Mach die Fensterüberwachung für alle“ bzw. „… gemeinsam“
    (nur Eigentümer oder Admin) oder beim Anlegen „Überwache … für uns alle“.
    Die Liste zeigt „(gemeinsam)“.
  - Die Regel bleibt in `automation_ownership.may_manage()`.
  - **Empfänger:** Bei gemeinsamen Überwachungen gehen Benachrichtigungen an
    den bestätigten Haushalt, sonst ehrliche Antwort wie heute bei „uns“.
  - Ohne die Einstellung bleibt alles wie in 7.9.1.

### A4 (T4): Eine Etage mit nur einem Melder wird ehrlich benannt

„Melde dich, wenn sich im Obergeschoss zwei Stunden nichts bewegt“ überwacht
nur den Präsenzmelder im Schlafzimmer.

- Die Vorschau sagt ausdrücklich, welche Räume der Etage **nicht** beobachtet
  werden („Im Obergeschoss gibt es nur im Schlafzimmer einen Melder; Bad und
  Kinderzimmer kann ich nicht beobachten.“).
- Die Raumliste wird aus der Registry abgeleitet.

### A5 (T5): Ein exakter Registry-Name gewinnt vor der Rückfrage

„Wenn der Stromverbrauch über 3000 Watt geht, warn mich“ fragt heute zwischen
vier Leistungssensoren nach, obwohl es genau einen Sensor „Stromverbrauch Haus“
gibt.

- Enthält genau ein Registry-Name (ohne Raum) das gesprochene Nomen als ganzes
  Wort und passt die Messgröße, gewinnt dieser Sensor.
- Mehrere solche Namen führen weiterhin zur Rückfrage.
- Prüfe dieselbe Regel für Befehle und Abfragen.

### A6 (T6): verbleibende Lücken

| Formulierung | Soll |
|---|---|
| „Wenn die Sonne scheint, fahre die Markise aus.“, „Wenn es draußen heller als 30000 Lux ist, öffne die Markise.“, „Bei Wind über 40 km/h fahr die Markise ein.“ | Markisen-Automationen verstehen. „Sonne scheint“ wird ehrlich auf einen vorhandenen Helligkeits- oder Sonnensensor abgebildet oder es folgt eine Rückfrage nach dem Schwellwert. Nie ein geratener Wert. |
| „Erinnere mich jede Minute, bis die Markise eingefahren ist.“ | dieselbe Frage „nur jetzt oder jedes Mal?“ wie bei der Haustür (einheitlich) |
| „Sag mir Bescheid, wenn die Außentemperatur schnell fällt.“ | Rückfrage nach Betrag und Zeitraum (Dialog aus 7.9.1) |
| „Wenn ich gehe und noch Licht an ist, sag mir Bescheid.“ | Die Push-Nachricht nennt die Situation („Du hast das Haus verlassen; im Wohnzimmer und in der Küche ist noch Licht an.“). Die betroffenen Geräte werden zur Laufzeit per Template aus Entity-IDs ermittelt, nie aus Nutzertext. |
| „Sag mir Bescheid, wenn irgendeine Batterie unter 20 Prozent fällt.“ | **Fehler heute:** Die Vorschau sagt „Wenn **Sensor** unter 20 liegt … ‚Auslöser eingetreten.‘“. Soll: alle Batterie-Sensoren (`device_class: battery`), Vorschau mit Anzahl, Nachricht nennt das Gerät („Batterie Fenster Bad: 18 %“). |
| Rückmeldungen beim Pausieren und Stoppen | Kurzform wie in der Liste, nicht die volle Vorschau |

---

## Teil B – Neue Fähigkeiten

Für jede Fähigkeit gilt:
- Nur lesende Funktionen dürfen ohne „Ja“ antworten.
- Alles, was schreibt oder dauerhaft etwas anlegt, geht über Vorschau und „Ja“.
- Ehrliche Antworten, wenn Daten fehlen (Recorder, Sensoren).

### B1 – „Was war los, während ich weg war?“ und Tageszusammenfassung

- **Ist:** „Was war los, während ich weg war?“ beschreibt den **jetzigen**
  Zustand. „Was ist heute passiert?“ wird nicht verstanden.
- **Soll:** eine **Ereignis-Zusammenfassung über einen Zeitraum.**
  - **Zeitraum:**
    - „während ich weg war“: die letzte Abwesenheit des Sprechers, aus dem
      Verlauf seiner `person.*`;
    - „heute“, „seit heute Morgen“, „letzte Nacht“, „seit 14 Uhr“, „gestern“.
  - **Inhalt**, nach Wichtigkeit, höchstens etwa 5 Punkte gesprochen, Rest auf
    Nachfrage:
    - ausgelöste Überwachungen und Warnungen (Melder, Fenster/Türen bei
      Abwesenheit);
    - Öffnungen von Türen und Toren mit Uhrzeit;
    - Geräte fertig (Waschmaschine);
    - auffällige Werte aus den Überwachungen;
    - Ausführungen von HomeIntent bzw. Automationen mit Wirkung (aus
      `execution_trace`).
  - **Quellen:** HA-Recorder bzw. Logbook über den vorhandenen Weg in
    `history_query.py`, HomeIntent-Trace, Monitor-Goal-Läufe. Keine neue
    Datenhaltung, wenn der Recorder es liefert.
  - **Datenschutz:** Ein Nicht-Admin bekommt Ereignisse zu Personen und deren
    Anwesenheit nur über sich selbst, sonst zusammengefasst („jemand ist um
    15:02 heimgekommen“).
  - **Ohne Recorder** gibt es eine ehrliche Antwort.
- **Optional als Push beim Heimkommen:** „Schick mir beim Nachhausekommen eine
  Zusammenfassung“. Das ist eine Überwachung mit Vorschau und „Ja“.

### B2 – Vorschläge aus Gewohnheiten

- **Vorher prüfen:** `habit_discovery.py` und die V12-Proaktiv-Laufzeit
  existieren. Stelle zuerst fest, was sie heute erkennen und vorschlagen, und
  baue darauf auf statt daneben.
- **Soll:**
  - **Erkennen:** wiederkehrende, vom Nutzer selbst ausgelöste Handlungen
    (gleiche Wirkung, ähnliche Uhrzeit bzw. gleicher Auslöser, an mindestens N
    von M Tagen; N und M als Konstanten dokumentiert). Statistisch
    deterministisch, kein ML.
  - **Vorschlagen:** höchstens einmal pro Muster, nie während einer anderen
    Rückfrage, auf Wunsch per Push. Beispiel: „Du schaltest an Werktagen gegen
    22:30 Uhr die Lichter im Erdgeschoss aus. Soll ich das automatisch machen?“
    → bei „Ja“ die normale Automationsvorschau mit erneutem „Ja“.
  - **Abfragen:** „Welche Gewohnheiten hast du erkannt?“, „Hast du Vorschläge
    für Automationen?“, „Schlag mir nichts mehr vor“ (abschalten).
  - **Grenzen:** Zugänge, Schlösser und kritische Geräte werden nie
    vorgeschlagen. Vorschläge werden nur dem Benutzer gemacht, der die Handlung
    ausgeführt hat.

### B3 – Batterien und Ausfälle überwachen

- **Batterien:** „irgendeine Batterie unter X %“ (Fehler siehe A6). Dazu kommen:
  - „Welche Batterien sind schwach?“ (Abfrage);
  - „Sag mir jeden Sonntag, welche Batterien unter 30 % sind“ (Bericht).
- **Nicht erreichbar:** „Melde dich, wenn ein Gerät nicht mehr erreichbar ist“
  bzw. „… wenn der Bewegungsmelder im Flur ausfällt“.
  - Auslöser: Zustand `unavailable` länger als eine Mindestdauer (Standard
    10 Minuten, in der Vorschau genannt).
  - „Ein Gerät“ heißt: freigegebene Sensoren und Aktoren.
  - Die Vorschau nennt die Anzahl.
  - Die Nachricht nennt das Gerät.
  - Ein HA-Neustart darf keine Flut auslösen: Die Mindestdauer gilt ab dem
    Neustart. Teste das.
- **Abfrage:** „Welche Geräte sind nicht erreichbar?“

### B4 – Urlaubsmodus

- **Ist:** „Schalte den Urlaubsmodus ein“ schaltet nur einen vorhandenen
  Helfer. „Ich bin bis Sonntag im Urlaub“ wird nicht verstanden.
- **Soll:** Ein **Abwesenheitsprofil mit Enddatum.**
  - **Gesprochen:**
    - „Ich bin bis Sonntag weg“, „Wir fahren bis zum 20. in den Urlaub“,
      „Urlaubsmodus bis Freitag“;
    - Ende: „Urlaub vorbei“, „Wir sind zurück“, automatisch zum Enddatum.
  - **Wirkung,** jeweils in der Vorschau einzeln aufgezählt, mit „Ja“:
    1. Strengere Überwachung: jede Tür- oder Fensteröffnung und jede Bewegung im
       Haus sofort als Push an den Haushalt. Das sind vorhandene
       Überwachungstypen, zeitlich begrenzt.
    2. Optional eine Anwesenheitssimulation: Lichter, die zu den **gewohnten**
       Zeiten an- und ausgehen (aus dem Verlauf der letzten Wochen, B2-Daten).
       - Mit kleiner, deterministisch erzeugter zeitlicher Streuung, z. B. aus
         Datum und Entity-ID abgeleitet.
       - Nur Lichter.
       - Ohne genug Verlauf gibt es eine ehrliche Antwort und ein Angebot fester
         Zeiten.
    3. Ein vorhandener Urlaubs-Helfer (`input_boolean` mit Urlaub/Abwesenheit)
       wird mitgeschaltet, wenn es genau einen gibt.
  - Alles, was der Urlaubsmodus anlegt, wird am Ende **vollständig
    zurückgenommen** (Test).
  - „Was macht der Urlaubsmodus gerade?“ beantwortet den Stand.
  - **Grenzen:** keine Heizungs-, Tor-, Tür- oder Schlossaktionen. Die
    Zugangsregel und die Execution Policy gelten unverändert.

### B5 – Verbrauch fragen

- **Ist:** „Wie viel Strom hat die Waschmaschine heute verbraucht?“ wird nicht
  verstanden. „Was hat heute am meisten Strom verbraucht?“ nennt die
  **aktuelle Leistung** (W) statt des Tagesverbrauchs.
- **Soll:**
  - **Energie in einem Zeitraum:** heute, gestern, diese Woche, diesen Monat,
    letzte Nacht.
  - **Quellen:**
    - Energie-Sensoren bzw. Zähler, aus dem Recorder als Differenz oder über
      Statistiken (`recorder.statistics`, `change` bzw. `sum`);
    - für Geräte nur mit Leistungssensor ein Integral über den Verlauf.
      Die Vorschau bzw. Antwort sagt dann „geschätzt aus der Leistung“.
  - **Rangfolge:** „Was hat heute am meisten verbraucht?“ antwortet in kWh,
    die drei größten Verbraucher.
  - **Kosten** nur, wenn ein Strompreis konfiguriert ist (HA-Energie-Dashboard
    bzw. Option); sonst keine Kostenangabe.
  - **Unterscheidung:** „Wie viel verbraucht die Waschmaschine **gerade**?“
    bleibt die Leistung in W. Leistung und Energie werden nie verwechselt.
  - **Ohne Recorder bzw. Statistik** gibt es eine ehrliche Antwort.

---

## Teil C – Tests, Gates, Live, Release

- **Tests:**
  - Je Punkt eine Testdatei, kombinatorische Paraphrasen, Negativ- und
    Mehrdeutigkeitsfälle.
  - Wo eine Automation entsteht, die Nachbildung der HA-Auswertung (`tests/_ha_sim.py`).
  - Für B1/B5 synthetische Recorder-Verläufe. Für B4 der Test, dass am Ende
    alles zurückgenommen wird.
- **Alle Gates aus `.github/workflows/ci.yml`:**
  - Sprach-Eval;
  - Korpus-Signaturen (neue Baseline nur mit Begründungsliste, `SAFETY_DRIFT` nie);
  - Shadow- und Arbiter-Vergleich;
  - beide Dev-Benchmarks;
  - alle Latenz-Budgets;
  - `regex_inventory.py --write`;
  - pyright (voll und alle Strict-Profile);
  - pyflakes;
  - `pytest -q`.
- **Live:**
  - `sim/fresh_ha.sh`, `runner.py --strict` über alle Kategorien,
    `check_log.py` mit 0 Befunden.
  - Die bisherigen 198 Szenarien bleiben grün (geänderte mit Begründung).
  - Neue Szenarien für A1–A6 und B1–B5. Dazu gehören: Melde-Verzögerung,
    Bewässerung mit automatischem Schließen, Gas- bzw. Hauptventil gesperrt,
    Haushalts-Satellit, Batterie-Push mit Gerätename, „nicht erreichbar“ nach
    Mindestdauer, Urlaubsmodus an/aus mit vollständiger Rücknahme,
    Zusammenfassung nach simulierter Abwesenheit, Tagesverbrauch aus
    simuliertem Zähler.
- **Version 7.9.2.** Releasebericht `docs/umsetzung-7.9.2.md` mit:
  - je Punkt: Ursache bzw. Ziel, Änderung, Test (vorher rot, nachher grün);
  - geänderten Erwartungen samt Begründung;
  - allen Gate- und Live-Zahlen;
  - gemessener zusätzlicher Wartezeit (A1);
  - bekannten Grenzen.

## Abschlussbericht (letzte Antwort)

1. Tabelle A1–A6 und B1–B5: umgesetzt, teilweise oder nicht, jeweils mit
   Begründung.
2. Vorher → nachher für die Prüfsätze aus diesem Auftrag und aus
   `docs/nachtest-7.9.1.md`.
3. A1: gewählter Wartemechanismus, gemessene Wartezeiten, Verhalten bei
   Gegenrichtung und `unavailable`.
4. Geänderte Test-Erwartungen mit Begründung.
5. Alle Gate- und Live-Ergebnisse in Zahlen.
6. Offene Punkte.
