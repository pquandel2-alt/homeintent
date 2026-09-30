# Umsetzungsauftrag HomeIntent 7.9: Überwachungsaufträge vollständig machen

Repository `pquandel2-alt/homeintent`.

**Voraussetzung:** Der Auftrag `sim/PROMPT_UEBERWACHUNG_7.8.3.md` (im Zweig
`claude/sleepy-meitner-xd7oux`) ist umgesetzt: Überwachungsaufträge,
Zustandskombination in beiden Reihenfolgen und eine einheitliche Bedeutung
zwischen Automationspfad und V10-Monitor-Goals.

Ist 7.8.3 in deinem Arbeitszweig noch nicht enthalten (`manifest.json` < 7.8.3),
setzt du **zuerst** 7.8.3 vollständig um, einschließlich Gates, und beginnst erst
dann mit diesem Auftrag.

## Ziel

HomeIntent soll nicht nur Sprachbefehle ausführen, sondern ein Assistent sein,
der Werte und Zustände überwacht und meldet, wenn etwas eintritt oder auffällig
ist. Nach 7.8.3 versteht HomeIntent diese Aufträge:

- Zustände und Dauer,
- Gruppen,
- Grenzwerte,
- Melder,
- „Gerät fertig“,
- Kommen und Gehen,
- Zeitpunkte,
- Bedingungen,
- kombinierte Zustände.

Dieser Auftrag schließt die Lücken, die danach bleiben (Wellen W1–W8). Für jede
Lücke gibt es zwei mögliche Ergebnisse:

- eine **korrekte** Überwachung, oder
- eine **ehrliche** Antwort, die sagt, was fehlt und was der Nutzer tun kann.

Nie etwas, das nur so ähnlich ist.

## Harte Regeln

1. **Kein LLM, kein ML-Modell, kein probabilistisches Raten.** Alles lokal und
   deterministisch.
2. **Keine Satzlisten.** Erkannt werden wiederverwendbare Konstruktionen:
   - Verben,
   - Quantoren,
   - Modifikatoren,
   - Konnektoren,
   - Bezüge.

   Tests erzeugen Paraphrasen **kombinatorisch** (siehe
   `tests/test_monitoring_automation_783.py`, `tests/test_sprache73_notifications.py`).
3. **Typisierte Modelle statt Freitext:**
   - Neue Bedeutungen werden als Felder oder Typen in `TriggerModel`,
     `ConditionModel` oder `ActionModel` modelliert.
   - Der Generator (`nlu/ha_automation_generator.py`) baut Home-Assistant-YAML und
     Templates nur aus diesen geschlossenen Typen.
   - Nutzertext gelangt nie in ein Template; Ausnahme ist der Nachrichtentext als
     Datenwert.
4. **Sicherheitsgrenze unverändert:** Grounding → Validator → gesprochene Vorschau
   → ausdrückliches „Ja“ → Schreiben.
5. **Die Vorschau sagt, was wirklich passiert.** Dazu gehören ausdrücklich:
   - Wie oft wird gemeldet?
   - Wie lange läuft die Wiederholung, und bis zu welcher Obergrenze?
   - Wer bekommt die Nachricht?
   - Läuft die Überwachung in Home Assistant oder in HomeIntent?
   - Was passiert bei einem Neustart?
6. **Nachfragen statt raten:**
   - fehlender Zeitraum,
   - fehlender Prüfzeitpunkt,
   - mehrdeutiges Gerät,
   - fehlender Sensor.
7. Die Negations- und Sicherheitssperre in `conversation.py` bleibt unangetastet.
   Schlösser, Alarmanlage, Tore und Ventile bleiben bestätigungspflichtig
   (Execution Policy).
8. Tests werden nicht abgeschwächt. Eine geänderte Erwartung braucht eine
   Begründung im Test, die eine echte Bedeutungsverbesserung benennt.
9. Keine Modellnamen in Commits, Code oder Dokumenten. Die Holdout-Korpora der
   Testsessions werden nicht benutzt oder nachgebaut.
10. **Wo die Überwachung läuft:**
    - **Standard ist die Home-Assistant-Automation.** Sie ist in HA sichtbar und
      läuft ohne HomeIntent.
    - **Die HomeIntent-eigene Monitor-Runtime** (V10 `MonitorGoalStore`,
      Zustellung über `agent_delivery`) nur dort, wo HA die Bedeutung ohne neue
      Helfer **nicht** ausdrücken kann. Nach aktuellem Stand ist das nur W3.
    - Die Vorschau sagt dann: „Das überwache ich selbst; es läuft, solange
      HomeIntent läuft.“

---

## W1 – Gesamtzustände („alle“, „kein … mehr“)

Heute antwortet HomeIntent darauf mit `unsupported_text("aggregate")`, siehe
`automation_grounding.py` (`Quantifier.ALL`).

Beispiele: „Sag mir Bescheid, wenn alle Fenster zu sind.“ · „Melde dich, sobald
kein Licht mehr an ist.“ · „Wenn alle Rollläden unten sind, benachrichtige mich.“ ·
„… wenn im Obergeschoss alle Fenster geschlossen sind.“

- **Bedeutung:** Der Gesamtzustand wird wahr.
  - HA-Auslöser: für jedes Mitglied „wechselt in den Zielzustand“.
  - HA-Bedingung: **alle** Mitglieder sind im Zielzustand. Eine HA-State-Condition
    mit Entity-Liste heißt „alle“; das ist hier genau richtig.
- **„kein X mehr an“** bedeutet dasselbe wie „alle X aus“. Die Verneinung ist
  eine Eigenschaft des Quantors und wird nicht zum Befehl.
- **Moment gegen Zustand:** „sobald das letzte Fenster zugeht“ ist dieselbe
  Situation.
- **Grenzen:**
  - Leere Menge → Rückfrage.
  - Mehr als 50 Mitglieder → Vorschau nennt die Anzahl.
  - Mischung von Geräte-Arten → Rückfrage, wie in 7.8.3.
- **Zusammen mit W1/7.8.3:** „wenn alle weg sind und noch ein Fenster offen ist“
  ist eine Zustandskombination.

## W2 – Ausbleiben und Inaktivität

Beispiele: „Melde dich, wenn sich im Flur 12 Stunden nichts bewegt.“ · „Sag mir
Bescheid, wenn die Haustür zwei Tage nicht geöffnet wurde.“ · „Warne mich, wenn
Oma bis 10 Uhr keine Bewegung im Bad hatte.“

- **„X passiert D lang nicht“:**
  - HA-Auslöser: State-Trigger auf den Ruhezustand mit `for: D`.
  - Fehlt ein Melder für den Ort → ehrliche Antwort.
- **„bis Uhrzeit U nicht“:**
  - Zeit-Auslöser um U.
  - Bedingung: Der Melder war seit Tagesbeginn nicht im Aktivzustand. Die
    Bedingung wird typisiert gebaut: Template aus Entity-ID und Zeitpunkt,
    `last_changed` samt Zustand.
  - Ohne Uhrzeit → Rückfrage „Bis wann?“.
- **Ehrlichkeit bei Neustarts:** HA setzt `for:`-Timer und `last_changed` beim
  Neustart zurück. Die Vorschau sagt das in einem Satz.
  - Führe einen Test, der diesen Satz bei jedem Inaktivitäts-Auslöser verlangt.
- **Waschmaschine:** „wenn die Waschmaschine heute nicht lief“ ist nur möglich,
  wenn es einen Betriebs- oder Leistungssensor gibt (siehe
  `_ground_appliance_finished`). Sonst ehrliche Antwort.

## W3 – Änderungen und Raten („um 3 Grad“, „schnell“)

Heute antwortet HomeIntent `unsupported="relative_change"` (`automation_language.py`).

Beispiele: „Melde dich, wenn die Temperatur im Keller innerhalb einer Stunde um
3 Grad fällt.“ · „Warne mich, wenn die Luftfeuchtigkeit im Bad in 10 Minuten um
20 Prozent steigt.“

- **Zeitraum:** Ohne Zeitraum (nur „um 3 Grad fällt“) fragt HomeIntent nach:
  „In welchem Zeitraum?“. Ein Zeitraum wird nie angenommen.
- **Bedeutung:** Der aktuelle Wert unterscheidet sich vom Minimum bzw. Maximum im
  Fenster um mindestens Δ, in der genannten Richtung.
- **Umsetzung in der HomeIntent-Monitor-Runtime** (Regel 10):
  - Auswertung bei jeder Zustandsänderung des Sensors.
  - Fensterwerte kommen aus dem Recorder, über den bestehenden Weg in
    `history_query.py`.
  - Entprellung: höchstens eine Meldung je Fenster.
- **Keine HA-Helfer anlegen** (derivative/trend), ohne dass der Nutzer es
  ausdrücklich will.
- Einheit prüfen: „Grad“ nur bei Temperatursensoren, „Prozent“ nur bei
  %-Sensoren (vorhandene Logik `_sensor_unit_ok`).

## W4 – Verbrauch, Zähler, Leistung

Beispiele: „Sag mir Bescheid, wenn der Stromverbrauch heute über 10 kWh liegt.“ ·
„Melde dich, wenn die Leistung länger als 5 Minuten über 3000 Watt liegt.“ ·
„… wenn die Waschmaschine mehr als 2 kWh verbraucht hat.“

- **Leistung mit Dauer:** Numeric-State mit `for`. Prüfe, ob das schon geht, und
  ergänze Tests.
- **„heute“ bzw. „diese Woche“:**
  - Nur mit einem Sensor, der täglich zurückgesetzt wird. Erkennbar ist er am
    Utility-Meter-Zyklus bzw. an Attributen. Namen sind kein Beweis.
  - Aus einem Gesamtzähler (`total_increasing`) rechnet HomeIntent **nicht**
    selbst.
  - Ohne passenden Sensor gibt es eine ehrliche Antwort, die sagt, welcher Helfer
    fehlt („Lege in Home Assistant einen Verbrauchszähler mit täglichem Zyklus an“).
- **Einheiten:** Wh, kWh und W sauber trennen. Eine falsche Einheit führt zur
  Rückfrage.

## W5 – Wiederholen und Eskalieren

Neu im Modell. Heute gibt es keinen Aktionstyp für Wiederholungen.

Beispiele: „Erinnere mich alle 10 Minuten, bis das Garagentor zu ist.“ · „Melde
dich, wenn die Haustür offen ist, und wenn sie nach 15 Minuten immer noch offen
ist, sag Anna Bescheid.“

- **`ActionType.REPEAT`**, typisiert: Schritte, Intervall, Abbruchbedingung und
  **Obergrenze**.
  - Standard-Obergrenze: 12 Wiederholungen. Die Vorschau nennt sie.
  - Der Generator erzeugt HA `repeat: until:` mit `delay`.
- **Eskalation:** `wait_for_trigger` bzw. Wartebedingung mit Timeout, danach der
  zweite Empfänger.
  - Der Empfänger wird wie bisher aufgelöst: eindeutig oder Rückfrage.
- **Abbruchbedingung:** Sie muss der Zustand sein, dessen Ende gemeint ist
  („bis es zu ist“). Die Anapher wird wie in 7.8.3 gebunden.

## W6 – Bezug über mehrere Sätze und unvollständige Aufträge

Beispiele:
- „Überwache das Garagentor.“ → „Wann soll ich mich melden?“ → „Wenn es länger als
  10 Minuten offen ist.“
- „Überwache das Garagentor.“ → „Melde dich, wenn es offen ist.“

- **Offener Überwachungsauftrag:** Ein Überwachungsverb mit Objekt, aber ohne
  Ereignis, ergibt einen offenen Dialog mit genau diesem Objekt. Nutze das
  bestehende Dialogmodell (`PendingDialogKind`).
- **Antwort:** Die nächste Äußerung wird mit dem Objekt als Antezedens gelesen.
- **Regeln für den Dialog:**
  - Gilt nur im selben Gespräch und für denselben Nutzer.
  - Läuft nach einer begrenzten Zeit ab (vorhandene Dialog-TTL).
  - „Abbrechen“ beendet ihn.
  - Ein anderer vollständiger Befehl beendet ihn ebenfalls, ohne Nebenwirkung.
- **Pronomen in Geräteaktionen** (aus 7.8.3 offen, falls dort nicht erledigt):
  „Überwache die Haustür und schließ sie ab, wenn sie offen ist.“
  - Gleiche Bindung und Genus-Prüfung wie im Ereignis.
  - Das Schloss bleibt bestätigungspflichtig.

## W7 – „Etwas Ungewöhnliches“

Beispiele: „Melde dich, wenn etwas Ungewöhnliches passiert.“ · „Sag mir Bescheid,
wenn im Haus was Komisches ist.“

- **Nichts erfinden.** „Ungewöhnlich“ hat nur die Bedeutungen, die der V12-Katalog
  der proaktiven Situationserkennung wirklich kennt (`situation_detection.py`,
  `proactive_model.SituationKind`).
- **Antwort:**
  - Sie zählt die tatsächlich verfügbaren Situationsarten im Klartext auf.
  - Sie bietet an, sie für diesen Nutzer einzuschalten (Ja/Nein bzw. Auswahl).
  - Sie nennt die Optionen, die dafür in der Konfiguration nötig sind.
  - Ist die proaktive Erkennung abgeschaltet, sagt sie das.
- **Ein konkreter Begriff** („wenn Wasser austritt“, „wenn nachts eine Tür
  aufgeht, während niemand da ist“) geht über den normalen, satzbasierten Weg
  (7.8.3), nicht über den Katalog.

## W8 – Überwachungen verwalten

Beispiele: „Welche Überwachungen laufen?“ · „Was überwachst du gerade?“ · „Stopp
die Fensterüberwachung.“ · „Pausiere die Garagen-Meldung bis morgen.“

- **Auflisten:** Automationen und Monitor-Goals, die HomeIntent angelegt hat,
  gemeinsam und in Klartext.
  - Genutzt wird die gespeicherte `situation` bzw. Vorschau, nicht YAML und keine
    Entity-IDs.
- **Löschen und Deaktivieren:** über die bestehenden Wege
  (`match_automation_delete`, `match_automation_disable`, Goal-Store).
  - Mehrere Treffer → Rückfrage.
- **„bis morgen“:** eine zeitlich begrenzte Deaktivierung mit automatischem
  Wiedereinschalten. Andernfalls gibt es eine ehrliche Antwort.

---

## Tests und Gates

- **Pro Welle eine Testdatei:** `tests/test_monitoring_79_w<N>.py`. Sie enthält:
  - kombinatorische Paraphrasen, die dieselbe kanonische Bedeutung ergeben;
  - Negativfälle: kein Automat, Rückfrage bzw. ehrliche Antwort;
  - Mehrdeutigkeitsfälle;
  - für jede erzeugte Automation eine **Nachbildung der HA-Auswertung**, wie
    `_fires` in `tests/test_monitoring_automation_783.py`, einschließlich
    `repeat`, `for` und Templates. Sie prüft die tatsächliche Wirkung in beiden
    Richtungen: löst aus bzw. löst nicht aus.
- **Kanonische Bedeutung:** `CanonicalEventNotification` bzw. ein Nachfolger
  erhält die neuen Felder:
  - Quantor ALL,
  - Inaktivität,
  - Rate und Fenster,
  - Wiederholung und Eskalation.
- **Alle CI-Gates aus `.github/workflows/ci.yml`:**
  - Sprach-Eval,
  - Korpus-Signaturen (neue Baseline nur mit Begründungsliste, `SAFETY_DRIFT` nie),
  - Shadow-Vergleich,
  - Arbiter-Vergleich,
  - beide Dev-Benchmarks,
  - Latenz-Benchmarks (Automationssprache p95 < 100 ms bei 5000 Entitäten),
  - `regex_inventory.py --write` mit sinnvoller Einstufung,
  - pyright,
  - pyflakes,
  - `pytest -q`.
- **Live-Testbett `sim/`:** je Welle mindestens ein Szenario, das die **echte
  Push-Nachricht** in Home Assistant prüft: Zustände über `haus_sim` setzen, dann
  Anzahl, Empfänger und Text der Nachrichten prüfen. Lange Wartezeiten in die
  Kategorie „Proaktiv“. Mindestens:
  - „alle Fenster zu“,
  - Inaktivität mit kurzer Dauer,
  - Wiederholung bis „zu“ mit Obergrenze,
  - Eskalation an Anna,
  - Bezug über zwei Sätze,
  - Auflisten und Stoppen.

  Alle bisherigen Live-Szenarien bleiben grün (`runner.py --strict`,
  `check_log.py`).

## Release

- Version **7.9.0**.
- Releasebericht im Stil des Repos mit:
  - Wellen,
  - Architektur, besonders wo welche Überwachung läuft und warum,
  - geänderten Erwartungen samt Begründung,
  - Gate- und Live-Ergebnissen in Zahlen,
  - bekannten Grenzen.
- Commits auf Deutsch, ohne Modellnamen. Pushe auf deinen Arbeitszweig. Keinen
  Pull Request ohne ausdrücklichen Auftrag des Projekteigentümers.

## Abschlussbericht (letzte Antwort)

1. Status je Welle W1–W8: umgesetzt, teilweise oder bewusst nicht, jeweils mit
   Begründung.
2. Tabelle mit je zwei Beispielsätzen pro Welle: vorher → nachher, mit Auslöser,
   Bedingung, Aktion und Laufort (HA oder HomeIntent).
3. Neue Modelltypen und Felder, und wo der Generator sie abbildet.
4. Geänderte Test-Erwartungen mit Begründung.
5. Alle Gate- und Live-Ergebnisse mit Zahlen.
6. Was danach noch fehlt, damit HomeIntent „jede sinnvolle Überwachung“
   versteht.
