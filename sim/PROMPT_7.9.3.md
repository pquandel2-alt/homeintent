# Umsetzungsauftrag HomeIntent 7.9.3

Repository `pquandel2-alt/homeintent`.

- **Ausgangsstand:** 7.9.2 auf `main`, Commit `e43fd9c`.
- **Grundlage:** Nachtestbericht `docs/nachtest-7.9.2.md` im Zweig
  `claude/sleepy-meitner-xd7oux`.

```bash
git fetch origin claude/sleepy-meitner-xd7oux main
git show origin/claude/sleepy-meitner-xd7oux:docs/nachtest-7.9.2.md
```

**Entscheidung des Projekteigentümers:** Alle Befunde (B1–B6) **und alle
Erweiterungen (E2–E8)** aus dem Nachtest werden umgesetzt. E1 ist identisch mit
B1. Der Test im echten Haus macht der Eigentümer selbst.

## Harte Regeln (unverändert)

1. **Kein LLM, kein ML-Modell, kein probabilistisches Raten.** Alles lokal und
   deterministisch.
2. **Keine Satzlisten, keine Hardcode-Sonderfälle.**
   - Erkannt werden Konstruktionen.
   - Tests erzeugen Paraphrasen kombinatorisch.
   - Beispielsätze sind Prüfsätze, keine Muster.
3. **Die Sicherheitsgrenze bleibt:**
   - Grounding → Validator → gesprochene Vorschau → ausdrückliches „Ja“ →
     Schreiben.
   - `service_executor` bleibt der einzige Schreibweg für Geräte.
   - Die Negations-Sperre bleibt.
   - Zugangsregel (7.9.1/7.9.2), Eigentümerrechte und Haushalts-Satellit
     bleiben.
4. **Nachfragen statt raten.** Fehlen Daten (Wettervorhersage, Recorder,
   Sensor), antwortet HomeIntent ehrlich, nie mit einem geratenen Wert.
5. **Tests werden nicht abgeschwächt.** Geänderte Erwartungen werden im Test
   und im Releasebericht begründet.
6. **Keine Modellnamen** in Commits, Code oder Dokumenten. Commits auf Deutsch.
7. **Pushe auf deinen Arbeitszweig. Erstelle keinen Pull Request.**

---

## Teil A – Befunde aus dem Nachtest 7.9.2

### A1 (B1 = E1): Beim Anlegen schon erfüllte Bedingung

- **Ist:** „Gib mir Bescheid, sobald irgendeine Batterie unter 25 Prozent fällt“
  wird angelegt, obwohl zwei Batterien schon darunter liegen (Bad 14 %,
  Rauchmelder 9 %). Der Auslöser feuert nur beim Unterschreiten, also kommt für
  diese beiden nie eine Nachricht. Die Vorschau schweigt dazu.
- **Gilt grundsätzlich** für jede Zustands- oder Grenzwertüberwachung: Fenster
  offen, Leistung über …, nicht erreichbar, Gesamtzustände, Kombinationen.
- **Soll:**
  - **Die Vorschau prüft den aktuellen Zustand** aller betroffenen Entitäten.
    Ist die Bedingung für einige schon erfüllt, nennt sie diese mit Wert und
    fragt: „Fenstersensor Bad (14 %) und Rauchmelder oben (9 %) liegen schon
    darunter. Soll ich dir das jetzt gleich schicken?“
  - **Antwortmöglichkeiten:** Ja → Überwachung anlegen und sofort die Nachricht
    zu den schon erfüllten Geräten senden. Nein → nur anlegen. Abbrechen →
    nichts.
  - **Danach** meldet die Überwachung jedes **weitere** Gerät beim Erreichen.
    Bei Gruppen meldet sie je Gerät, nicht nur beim Wechsel der Gruppe.
  - **Zustandskombinationen** (zum Beispiel „Fenster offen und niemand
    zuhause“): Ist die Kombination beim Anlegen schon wahr, wird das genauso
    angeboten.
  - Die sofortige Nachricht geht über den vorhandenen Benachrichtigungsweg
    (`agent_delivery` bzw. Notify-Aktion mit derselben Empfängerauflösung), nicht
    über einen neuen Schreibweg.
- **Test:**
  - Matrix aus Überwachungsart × keine, einige, alle Entitäten schon erfüllt ×
    Antwort Ja/Nein/Abbrechen.
  - Nachbildung der HA-Auswertung: Für ein später erfülltes Gerät kommt genau
    eine Nachricht.
  - Live: Batterie- und Fensterfall.

### A2 (B5, Regression): „Stell das Heizprogramm auf Urlaub“

- **Ist:** Der Satz wird seit 7.9.2 zur Urlaubs-Rückfrage („Bis wann seid ihr
  weg?“). In 7.9.1 war er ein Gerätebefehl.
- **Soll:** Steht „Urlaub/Abwesenheit/Ferien“ als **Wert** eines Programms,
  Modus oder Presets eines Geräts im Satz („auf Urlaub stellen“, „Modus
  Urlaub“, „Preset Abwesend“), ist es ein Gerätebefehl. Der Urlaubsmodus
  entsteht nur aus einer Aussage über Personen bzw. Reise („ich bin/wir sind
  … weg/verreist/im Urlaub“) oder aus „Urlaubsmodus bis …“.
- **Test:** Paraphrasen beider Seiten. Der Entwicklungs-Benchmark-Fall bleibt
  grün.

### A3 (B2): Urlaub beenden

- **Ist:** „Wir sind wieder da.“ wird nicht verstanden. Die Urlaubsüberwachung
  schickt weiter, der Helfer bleibt an. Nur „Urlaub vorbei“ funktioniert.
- **Soll:**
  - **Konstruktion statt Liste:** Rückkehr (wieder da, zurück, heim,
    angekommen, wieder zuhause) oder Ende (vorbei, zu Ende, beenden, aus,
    abschalten) bezogen auf Urlaub, Reise oder Abwesenheit.
  - **Bei aktivem Urlaubsmodus** fragt jede Rückkehr-Aussage („Ich bin wieder
    zuhause“), ob der Urlaubsmodus beendet werden soll.
  - **Ohne aktiven Urlaubsmodus** gibt es eine ehrliche Antwort, keine Wirkung.
- **Test:** Paraphrasen-Matrix. Live: Nach dem Beenden kommt keine
  Urlaubsnachricht mehr.

### A4 (B3): Zusammenfassung

- **Paraphrasen:** „seit ich weg war“, „seit ich gegangen bin“, „in meiner
  Abwesenheit“, „Was hab ich verpasst?“, „Was war los, als ich weg war?“.
- **Reihenfolge:** Innerhalb gleicher Wichtigkeit chronologisch. Kommen und
  Gehen der fragenden Person nur einmal und kurz.
- **Mehrtägige Abwesenheit:** Die Überschrift nennt Datum und Uhrzeit, die
  Einträge nennen den Tag, wenn er vom heutigen abweicht.
- **Leistung:**
  - Nur relevante Klassen lesen (Türen, Fenster, Tore, Melder, Bewegung,
    Personen, Geräte-Fertig-Sensoren, HomeIntent-Ausführungen, Automationen).
  - Obergrenze für Zeitraum und Ereignisanzahl, dokumentiert.
  - Messung mit einem synthetischen großen Verlauf, z. B. 5000 Entitäten und
    7 Tage, als Benchmark mit Budget.
- **Test:** Paraphrasen, Sortierung, Datumsanzeige, Benchmark.

### A5 (B4): Keine Platzhalter in gesprochenen Vorschauen

- **Ist:** Vorschauen enthalten „\<Gerät\>: \<Wert\> %“, „\<Räume\>“ und
  „\<Gerät\> ist seit …“.
- **Soll:** Die Vorschau nennt ein **echtes Beispiel** aus dem aktuellen Haus
  („zum Beispiel: ‚Batterie Fenstersensor Bad: 14 %‘“) bzw. beschreibt den
  Inhalt in Worten („… die Nachricht nennt die Räume, in denen noch Licht an
  ist“).
- **Test:** über alle Vorschau-Erzeuger. Kein Zeichen „<“ oder „>“ in einer
  gesprochenen Antwort.

### A6 (B6): Kleinigkeiten

- „Was hat heute am meisten verbraucht?“ nennt nur Geräte mit Verbrauch größer
  0. Wenn nichts verbraucht hat, sagt HomeIntent das.
- Ohne Recorder: eine einzeilige Warnung je Ursache und Zeitraum statt eines
  Tracebacks je Frage. Die Antwort bleibt ehrlich.
- **Push beim Heimkommen mit Zusammenfassung** (aus 7.9.2 offen): umsetzen
  über den vorhandenen Zustellweg `agent_delivery`.
  - Auslöser: Ankunft der Person.
  - Die Zusammenfassung wird zur Laufzeit erzeugt.
  - Gleiche Rechte- und Empfängerprüfung wie Überwachungen.
  - Vorschau und „Ja“.
  - Ein eigener Dienst darf nur den Sprecher bzw. den bestätigten Empfänger
    benachrichtigen, nie Geräte schalten.

---

## Teil B – Erweiterungen

Für alle gilt:
- Lesende Antworten kommen ohne „Ja“.
- Alles, was schaltet oder dauerhaft anlegt, geht über Vorschau und „Ja“ bzw.
  die Execution Policy.
- Fehlende Daten werden ehrlich benannt.

### B1 (E2): Wetter

- **Abfragen** aus `weather.*`:
  - Vorhersage über den HA-Dienst `weather.get_forecasts`: täglich bzw.
    stündlich, je nach Frage.
  - Beispiele: „Wie wird das Wetter morgen?“, „Wie warm wird es heute?“,
    „Regnet es heute noch?“, „Brauche ich einen Schirm?“, „Wird es am
    Wochenende sonnig?“, „Wie viel Wind ist morgen?“.
  - Wahrscheinlichkeiten und Mengen nur nennen, wenn die Vorhersage sie
    liefert.
  - Mehrere Wetter-Entitäten führen zu einer Rückfrage bzw. zur
    Bereichszuordnung.
- **Als Auslöser oder Bedingung in Automationen:**
  - „Wenn Regen angesagt ist, fahr die Markise ein“, „… schließ die Fenster
    nicht“, „Bewässere nur, wenn es heute nicht regnet“.
  - Abbildung auf den aktuellen Zustand bzw. die Vorhersage. Für die Vorhersage
    eine Template- oder Zeit-Auslöser-Konstruktion, die
    `weather.get_forecasts` in der Automation aufruft (`response_variable`).
    Erzeugt nur aus Entity-IDs und geschlossenen Werten.
  - Der Generator, der Validator und die Nachbildung der HA-Auswertung müssen
    das abdecken.
  - Ohne Wetter-Entität gibt es eine ehrliche Antwort.
- **Testbett:** In `haus_sim` eine Wetter-Entität mit setzbarem Zustand und
  Vorhersage (täglich und stündlich) und einen Regensensor
  (`binary_sensor` `moisture` bzw. Regenmenge) ergänzen.

### B2 (E3): Wo ist jemand?

- „Wo ist Anna?“, „Ist jemand zuhause?“, „Wer ist zuhause?“, „Ist Philipp
  schon zuhause?“, „Seit wann ist Anna weg?“, „Wann ist Anna heimgekommen?“.
  Die Daten kommen aus `person.*`, den Zonen und dem Recorder.
- **Rechte:**
  - Nicht-Admins bekommen Zonen anderer Personen nur, wenn eine Admin-Option
    („Aufenthaltsort im Haushalt teilen“, Standard: nur „zuhause/unterwegs“)
    es erlaubt.
  - Nie Koordinaten.
- **„Wann kommt Philipp heim?“:** ehrlich, dass es dafür keine Daten gibt.
  Höchstens die übliche Ankunftszeit aus dem Verlauf, deutlich als
  Gewohnheitswert gekennzeichnet.

### B3 (E4): Musik und Medien

- **Befehle:** „Spiel Musik im Wohnzimmer“, „Spiel Bayern 3 in der Küche“,
  „Pause“, „Weiter“, „Nächstes Lied“, „Lauter“, „Leiser“, „Lautstärke 30“,
  „Mach die Musik aus“.
- **Abfrage:** „Was läuft gerade?“ (Titel und Interpret aus den Attributen).
- **Zielwahl:**
  - Raum → Mediaplayer im Raum.
  - Ohne Raum: der Mediaplayer im Raum des Satelliten bzw. der gerade spielende.
  - Mehrdeutig → Rückfrage.
- **Quellen bzw. Sender** nur aus `source_list` bzw. vorhandenen Favoriten,
  nie geraten. „Musik“ ohne Angabe nimmt die zuletzt genutzte Quelle; wenn es
  keine gibt, fragt HomeIntent nach.
- Es geht nur über `service_executor`. Der Bestätigungston und das Warten auf
  die Wirkung (7.9.2 A1) gelten auch für Medien.
- **Testbett:** Die vorhandenen Mediaplayer (Küchenradio,
  Schlafzimmer-Lautsprecher, Wohnzimmer-TV) bekommen Titel- und
  Interpret-Attribute.

### B4 (E5): Bewässerung abhängig vom Regen

- „Bewässere jeden Morgen um 6 Uhr 20 Minuten, aber nur wenn es nicht geregnet
  hat bzw. nicht regnen soll.“
- **Bedingung:** Regensensor bzw. Regenmenge der letzten 24 h (Recorder) oder
  die Wettervorhersage (B1).
- **Fehlt beides:** ehrliche Antwort und Angebot ohne die Bedingung.
- Die Vorschau nennt die Quelle der Bedingung.

### B5 (E6): Bestätigungston als Standard und Ton für „nicht ganz geklappt“

- **Standard:** `response_style` steht künftig standardmäßig auf `tone`.
  - Migration: bestehende Installationen behalten ihren gespeicherten Wert.
  - Neue Installationen bekommen `tone`.
  - Im Releasebericht und im Optionsdialog deutlich erklären.
- **Zweiter Ton** (`notice.mp3`, selbst erzeugt, lizenzfrei) für genau einen
  Fall: Alles wurde ausgeführt, aber mindestens ein Ziel hat sich in der
  Wartezeit nicht zurückgemeldet (`UNCONFIRMED` ohne Gegenrichtung und ohne
  `unavailable`).
  - Optional mit einer sehr kurzen Ansage („Stehlampe meldet sich nicht“).
  - Gegenrichtung, `unavailable`, Teilerfolg, Fehler, Frage: weiterhin
    gesprochen.
- **Option** für beide Töne (`confirmation_media_id`, `notice_media_id`), nur
  lokale Pfade.

### B6 (E7): Wöchentlicher Haus-Bericht

- „Schick mir jeden Sonntag um 18 Uhr einen Haus-Bericht.“ Inhalt:
  - schwache Batterien,
  - nicht erreichbare Geräte,
  - Verbrauch der Woche (B5 aus 7.9.2),
  - ausgelöste Warnungen bzw. Überwachungen der Woche,
  - auffällige Werte (Monitor-Runtime).
- Kurzform in der Push-Nachricht, Details auf Nachfrage („Zeig mir den
  Haus-Bericht“ bzw. „Was stand im Haus-Bericht?“).
- Läuft über denselben Zustellweg wie A6 (Push beim Heimkommen), mit Vorschau
  und „Ja“, Eigentümer bzw. gemeinsam wie Überwachungen.

### B7 (E8): Überwachungen ändern statt neu anlegen

- **Beispiele:**
  - „Ändere die Garagen-Meldung auf 15 Minuten.“
  - „Schick die Fenster-Warnung auch an Anna.“
  - „Die Batterie-Meldung erst unter 15 Prozent.“
  - „Die Haustür-Meldung nur noch nachts.“
  - „Nimm Anna aus der Fenster-Warnung raus.“
- **Ablauf:** Die geänderte Überwachung läuft erneut durch Validator und
  Vorschau („Vorher: … Nachher: …“), dann kommt das „Ja“.
- Das gilt für HA-Automationen (bestehende Edit-Pfade:
  `automation_action_edit`, `automation_structure_edit`) und für
  HomeIntent-Überwachungen (Goal-Store).
- **Rechte:** wie Verwalten (Eigentümer, Admin, gemeinsam).
- **Mehrdeutigkeit** führt zur Rückfrage („Welche Fenster-Warnung meinst du?“).

---

## Teil C – Tests, Gates, Live, Release

- **Tests:**
  - Je Punkt eine Testdatei mit kombinatorischen Paraphrasen, Negativ- und
    Mehrdeutigkeitsfällen.
  - Wo eine Automation entsteht: Nachbildung der HA-Auswertung (`tests/_ha_sim.py`
    um Wetter-Vorhersage bzw. `response_variable` erweitern).
  - Synthetische Recorder-Verläufe für Zusammenfassung, Personen und Regen.
- **Alle Gates aus `.github/workflows/ci.yml`:**
  - Sprach-Eval;
  - Korpus-Signaturen (neue Baseline nur mit Begründungsliste, `SAFETY_DRIFT`
    nie);
  - Shadow- und Arbiter-Vergleich;
  - beide Dev-Benchmarks;
  - alle Latenz-Budgets plus der neue Zusammenfassungs-Benchmark;
  - `regex_inventory.py --write`;
  - pyright (voll und alle Strict-Profile);
  - pyflakes;
  - `pytest -q`;
  - `tests_ha`.
- **Live:**
  - `sim/fresh_ha.sh`, `runner.py --strict` über alle Kategorien,
    `check_log.py` mit 0 Befunden.
  - Die bisherigen 215 Szenarien bleiben grün.
  - Neue Szenarien prüfen die **Wirkung**, nicht nur die Vorschau, unter
    anderem:
    - Batterie schon unter der Grenze → sofortige Nachricht nach „Ja“;
    - „Heizprogramm auf Urlaub“ schaltet das Gerät;
    - „Wir sind wieder da“ beendet den Urlaub, danach keine Urlaubsnachricht;
    - Zusammenfassung mit Datum;
    - Wetterfrage, Markise bei Regenvorhersage fährt ein;
    - „Wo ist Anna?“ mit und ohne Freigabe;
    - Musik starten, pausieren, lauter, „Was läuft gerade?“;
    - Bewässerung entfällt nach Regen;
    - zweiter Ton bei fehlender Rückmeldung;
    - Haus-Bericht mit Inhalt;
    - Überwachung ändern mit neuer Wirkung.
  - **Testbett-Hinweis:** Die Testbett-Automation „Rollläden bei
    Sonnenuntergang“ (`sim/config/automations.yaml`) kann Läufe stören, die
    über den Sonnenuntergang gehen. Für Korpus- und Szenarioläufe abschalten
    oder dokumentieren.
- **Version 7.9.3.** Releasebericht `docs/umsetzung-7.9.3.md` mit:
  - je Punkt: Ursache bzw. Ziel, Änderung, Test (vorher rot, nachher grün);
  - geänderten Erwartungen samt Begründung;
  - allen Gate- und Live-Zahlen;
  - bekannten Grenzen.

## Abschlussbericht (letzte Antwort)

1. Tabelle A1–A6 und B1–B7: umgesetzt, teilweise oder nicht, jeweils mit
   Begründung.
2. Vorher → nachher für die Prüfsätze aus diesem Auftrag und aus
   `docs/nachtest-7.9.2.md`.
3. Geänderte Test-Erwartungen und die Migration des Standards
   `response_style`.
4. Alle Gate- und Live-Ergebnisse in Zahlen.
5. Offene Punkte.
