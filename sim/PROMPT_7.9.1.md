# Umsetzungsauftrag HomeIntent 7.9.1: Befunde aus dem Nachtest 7.9.0 und Bestätigungston statt Sprachausgabe

Repository `pquandel2-alt/homeintent`.

**Ausgangsstand:** 7.9.0 (`main` = `2cc962f`, Code identisch mit
`claude/homeintent-monitoring-783-atqgyr` / `317e848`).

**Grundlage:** Nachtestbericht `docs/nachtest-7.9.0.md` im Zweig
`claude/sleepy-meitner-xd7oux`. Lies ihn vollständig, bevor du etwas änderst:

```bash
git fetch origin claude/sleepy-meitner-xd7oux
git show origin/claude/sleepy-meitner-xd7oux:docs/nachtest-7.9.0.md
```

## Harte Regeln (unverändert)

1. **Kein LLM, kein ML-Modell, kein probabilistisches Raten.** Alles bleibt
   lokal und deterministisch.
2. **Keine Satzlisten, keine Hardcode-Sonderfälle.**
   - Erkannt werden Konstruktionen.
   - Tests erzeugen Paraphrasen kombinatorisch.
   - Die Beispielsätze unten sind Prüfsätze, **keine** Muster zum Einbauen.
3. **Die Sicherheitsgrenze bleibt:**
   - Grounding → Validator → Vorschau → ausdrückliches „Ja“ → Schreiben.
   - Execution Policy und `service_executor` bleiben der einzige Schreibweg.
   - Die Negations-Sperre in `conversation.py` wird nicht umgangen.
4. **Nachfragen statt raten.** Eine gesprochene Einschränkung (Raum, Etage,
   „draußen“, Person) wird nie still fallengelassen.
5. **Tests werden nicht abgeschwächt.** Ändert sich eine Erwartung, steht die
   Begründung im Test und im Releasebericht.
6. **Keine Modellnamen** in Commits, Code oder Dokumenten. Commits auf Deutsch.
7. Pushe auf deinen Arbeitszweig. Erstelle **keinen** Pull Request.

---

## Teil A – Befunde aus dem Nachtest 7.9.0

Die Reihenfolge ist die Priorität. Zu jedem Befund schreibst du einen Test,
der vorher rot ist und danach grün.

### A1 (B1, Sicherheit): Zugänge öffnen sich nie automatisch

**Ist-Zustand:** „Wenn alle weg sind, öffne das Garagentor.“ wird als
Automation angeboten (seit 7.9 auch mit „alle weg“ bzw. „niemand zuhause“). Die
Vorschau nennt das Tor „Rollladen im Bereich Garage“. Schon seit 7.8.2 wird
„Wenn Philipp das Haus verlässt, öffne das Garagentor“ angeboten.

**Soll:**
- **Keine Öffnen-Aktion für Zugänge in Automationen.** Zugänge sind:
  - Cover der Klassen `garage`, `gate`, `door`;
  - Ventile;
  - Schlösser (schon heute ausgeschlossen).

  Das gilt unabhängig vom Auslöser. Behandle diese Aktionen wie Schlösser:
  HomeIntent lehnt ehrlich ab und bietet stattdessen eine Benachrichtigung an
  („… melde ich dir stattdessen, dann entscheidest du selbst“).
- **Schließen** von Zugängen bleibt erlaubt, mit Bestätigung wie bisher.
- **Die Regel steht an einer Stelle:** Die Policy für Automationsaktionen ist
  eine Funktion, die Validator **und** Vorschau nutzen, und sie ist durch
  Tests abgesichert.
  - Prüfe dabei auch Skripte und Szenen als Automationsaktion: Öffnet ein
    Skript ein Garagentor, gilt dieselbe Regel (EffectGraph).
- **Die Vorschau benennt die Geräteart korrekt:**
  - Garagentor, Tor, Tür, Markise, Rollladen.
  - Grundlage ist die Device-Class bzw. Ontologie, nicht pauschal „Rollladen“.
  - Prüfe alle Stellen in `nlu/automation_preview.py`, an denen ein Cover-Name
    erzeugt wird.

### A2 (B2, Rechte): Überwachungen und Automationen gehören jemandem

**Ist-Zustand:** Mit `allow_non_admin_automations: false` kann Anna fremde
Überwachungen trotzdem:
- pausieren,
- ausschalten,
- löschen (über „Lösche die Automation für …“, schon seit 7.8.2).

Außerdem sieht sie sie in der Liste.

**Soll:**
- **Eigentümer festhalten:** Jede von HomeIntent angelegte Automation und
  Monitor-Überwachung speichert ihren Eigentümer (`user_id`) im vorhandenen
  Metadaten-Sidecar bzw. Goal-Store.
- **Verwalten nur durch Eigentümer oder Admin.** Verwalten heißt:
  - pausieren,
  - ausschalten,
  - einschalten,
  - löschen,
  - bearbeiten.

  Andere bekommen eine ehrliche Antwort („Diese Überwachung hat Philipp
  angelegt; ändern kann sie nur Philipp oder ein Administrator.“). Der Pfad
  gilt für `match_automation_disable/enable/delete` **und** für die
  W8-Verwaltung.
- **Liste:** Nicht-Admins sehen standardmäßig ihre eigenen Überwachungen.
  Admins sehen alle, mit Eigentümer.
- **Ältere Automationen ohne Eigentümer:** Nur ein Admin darf sie verwalten.
  Das ist eine Migration ohne Datenverlust; schreibe den Test dazu.

### A3 (B4, Regression): „über/unter … geht“ ist ein Grenzwert, keine Anwesenheit

**Ursache:** `_LEAVE_RE` in `automation_language.py` deutet ein „geht“ am
Satzende als „jemand verlässt das Haus“. In „die Temperatur … über 24 Grad
geht“ wird so `presence=LEAVE` gelesen.

**Soll:**
- **Strukturelle Regel:** Geht dem Verb eine Komparator-Wert-Phrase voraus
  (`über/unter/mehr als/weniger als/auf … [Einheit]`), ist das ein
  Wert-Prädikat. Ein Subjekt, das ein Messwert oder ein Gerät ist (kein
  `person.*`, kein „ich/jemand/niemand“), ist nie Anwesenheit.
- **Test:** generiert über alle Komparatoren × Einheiten (Grad, Prozent, ppm,
  Watt, ohne Einheit) × „geht/gehen“ × Satzstellung (Wenn-Satz vorn/hinten).
  Erwartet wird jeweils dieselbe kanonische Bedeutung wie mit „steigt/fällt/liegt“.
- **Gegenprobe:** „Wenn ich gehe …“ und „Wenn Anna geht …“ bleiben Anwesenheit.

### A4 (B3, falsches Gerät): eine gesprochene Etage ist eine harte Einschränkung

**Ursache:** In `automation_grounding.ground_event` filtert `subject.place`
nur bei `len(candidates) > 1`. Ohne Treffer fällt der Filter auf alle zurück
(`placed or candidates`). Deshalb wird aus „im Keller 5 Stunden nichts bewegt“
der Flur-Melder im Erdgeschoss.

**Soll:**
- Die Etage bzw. der Ort filtert immer, auch bei einem einzigen Kandidaten.
- Ohne Treffer gibt es eine ehrliche Antwort wie beim Raum („Im Keller gibt es
  keinen Bewegungs- oder Präsenzmelder …“).
- **Sonderfall „draußen“ (7.8 B5):** Gibt es draußen keinen passenden Sensor,
  fragt HomeIntent nach. Das Fallback auf alle ist nicht mehr erlaubt.
- **Prüfe dieselbe Logik** für Befehle und Abfragen, die `place.contains`
  nutzen. Eine Etage darf nirgends still ignoriert werden.

### A5 (B5): Überwachung löschen geht nie an den Kalender

**Soll:**
- `parse_monitoring_management` versteht auch Präpositionalobjekte:
  „die Überwachung vom/für das/des Garagentor(s)“, „die Meldung zur Haustür“,
  „die Automation fürs Küchenfenster“. Damit gelten für alle Verwaltungsverben
  dieselben Objektformen wie für Zusammensetzungen.
- **Strukturelle Grenze:** Ein Verwaltungsverb plus Objekt
  „Überwachung/Meldung/Automation/Benachrichtigung/Warnung“ geht nie an
  Kalender, Einkaufsliste oder Erinnerungen. Gibt es keinen Treffer, sagt
  HomeIntent ehrlich, dass keine passende Überwachung existiert.
- **Test:** Konversationsebene, mit vorhandenem Kalender, ohne jeden Aufruf an
  einen Kalender.

### A6 (B6): die eigene Rückfrage wird beantwortet

„Melde dich, wenn die Temperatur im Büro um 2 Grad fällt.“ → „In welchem
Zeitraum?“ → „Innerhalb von 10 Minuten.“ ergibt heute „nicht verstanden“.

**Soll:**
- Jede Rückfrage, die nach genau **einem** fehlenden Teil fragt, öffnet einen
  typisierten Dialog. Das gilt für:
  - Zeitraum,
  - Uhrzeit („bis wann?“),
  - Intervall („nur jetzt oder jedes Mal?“),
  - Gerät,
  - Empfänger.
- Die Antwort wird **nur** als dieser Teil gelesen:
  - kurze Antworten wie „in 10 Minuten“, „eine Stunde“, „bis 9“;
  - „Abbrechen“ beendet den Dialog;
  - ein vollständiger neuer Befehl beendet ihn ohne Nebenwirkung.

  Nutze das vorhandene Dialogmodell (`PendingDialogKind` / `DialogTaskKind`).
- **Prüfe alle Rückfragen aus 7.9** auf diese Eigenschaft. Eine Rückfrage ohne
  Antwortpfad ist ein Fehler.

### A7 (B7): Lücken im Verstehen (alle sicher abgelehnt, nach Häufigkeit)

- **Wiederholung ohne Wenn-Satz:** „Schick mir alle 5 Minuten eine Nachricht,
  solange die Haustür offen ist.“ ist eine Wiederholung. Die heutige Begründung
  „Wiederholen kann ich nur Benachrichtigungen“ ist falsch.
- **Zu kurzes Intervall:** „alle 30 Sekunden“ ergibt eine ehrliche Antwort
  „höchstens einmal pro Minute“, nicht „nicht verstanden“.
- **Dauereinheiten:** Tage und Wochen („eine Woche nicht geöffnet“). Die
  falsche Antwort „kein passendes Gerät für ‚Haustür‘“ darf nie kommen, wenn
  das Gerät existiert.
- **Leistung mit Verben:** „zieht / verbraucht gerade / nimmt mehr als X Watt“
  und „das Haus verbraucht mehr als 5 kW“ (Leistungssensor, nicht Energie).
- **Raum-Präsenz als Inaktivität:** „im Wohnzimmer 2 Stunden niemand“ nutzt den
  Präsenzmelder des Raums.
- **Zustandswörter für Cover:** „eingefahren/ausgefahren/hochgefahren/
  heruntergefahren“ werden zum Zustand.
- **„noch Licht an“** (Stoffnomen ohne Artikel) bedeutet: irgendein Licht.
  Keine Rückfrage „welches Licht“, sondern ODER über alle Lichter.
- **Zählerstand als Grenzwert:** „Energiezähler über 12000 kWh“ ist ein gültiger
  Grenzwert (Zählerstand), keine Frage nach dem Zeitraum.
- **„Prüfe/Überprüfe/Kontrolliere, ob …“** ist eine einmalige Abfrage und wird
  sofort beantwortet. Es entsteht keine Automation.
- **„Melde dich bei Auffälligkeiten“** wird wie W7 behandelt. „Beobachtest du
  das Garagentor?“ wird aus der W8-Liste beantwortet.
- **„Welche Überwachungen laufen?“:** Für die Sprachausgabe gibt es eine kurze
  Form („Zwei: Garagentor länger als 10 Minuten offen; Fenster offen, wenn
  niemand zuhause ist.“). Die volle Vorschau gibt es nur auf Nachfrage („Was
  genau macht die erste?“).

---

## Teil B – Bestätigungston statt Sprachausgabe

**Wunsch des Projekteigentümers:** HomeIntent spricht nur, wenn es einen Fehler
gibt oder wenn es eine Frage stellt. Wurde ein Befehl ausgeführt, kommt nur ein
**Bestätigungston**.

### B.1 Bedeutung (verbindlich)

| Ergebnis des Turns | Sprachausgabe | Ton |
|---|---|---|
| Befehl vollständig ausgeführt und Wirkung bestätigt (Licht an, Rollladen zu, Szene/Skript gestartet, Automation nach „Ja“ erstellt, Überwachung pausiert …) | **keine** | **Bestätigungston** |
| Rückfrage oder Bestätigungsfrage („Soll ich …?“, „Welches Licht meinst du?“, Vorschau einer Automation) | ja | nein |
| Fehler, Ablehnung oder „Ich habe nichts ausgeführt“ | ja | nein |
| Teilerfolg (ein Teil ausgeführt, ein Teil nicht) | ja, nennt den nicht ausgeführten Teil | nein |
| Wirkung nicht bestätigt (Gerät hat den Zielzustand nicht erreicht, siehe Action Evidence V11) | ja | nein |
| Antwort auf eine Abfrage („Wie warm ist es im Bad?“, „Ist das Garagentor offen?“) | ja – die Antwort **ist** der Inhalt | nein |
| Etwas wurde gelernt oder gemerkt („Das merke ich mir für …“) | ja, kurz | nein |

**Grundsatz:** Ein Ton bedeutet ausschließlich: **„Alles, was du gesagt hast,
ist passiert.“** Jede andere Lage wird gesprochen. Im Zweifel spricht
HomeIntent; der Ton wird nie benutzt, um eine Unsicherheit zu überdecken.

### B.2 Technischer Befund (vom Nachtest in HA 2026.9.2 geprüft)

- HA spielt seinen eingebauten Bestätigungston (`assist_pipeline/acknowledge.mp3`,
  `ACKNOWLEDGE_PATH`) **nur**, wenn der eingebaute Agent
  (`conversation.HOME_ASSISTANT_AGENT`) geantwortet hat. Zusätzlich müssen alle
  Ziele im Raum des Satelliten liegen (`assist_pipeline/pipeline.py`,
  `_get_all_targets_in_satellite_area`).
- Für einen eigenen Agenten wie HomeIntent gilt das nicht.
- Gibt ein eigener Agent eine **leere** Sprachausgabe zurück, überspringt die
  Pipeline TTS vollständig (`if all_targets_in_satellite_area or tts_input.strip()`).
  Dann herrscht Stille, aber es kommt kein Ton.
- `ConversationResult` hat kein Feld für ein Medium.

**Daraus folgt:** HomeIntent erzeugt den Ton selbst, auf dem Gerät, von dem der
Befehl kam.

**Untersuche zuerst die offizielle HA-API dieser Version, bevor du baust:**
- `assist_satellite` (`async_internal_announce`, `announce` mit `media_id`,
  `preannounce=False`);
- `media_player.play_media` auf dem Mediaplayer des Satelliten;
- ob eine Ansage **während** der laufenden Pipeline abgewiesen oder
  hintangestellt wird.

Wähle den Weg, der ohne Wettlauf funktioniert, zum Beispiel: die Ausgabe nach
Ende der Pipeline auslösen, wenn der Satellit wieder `idle` meldet, mit
Zeitlimit. Dokumentiere die Wahl mit Fundstellen im HA-Quellcode.

### B.3 Umsetzung

- **Option `response_style`** (Konfigurationsdialog, Diagnostics, Übersetzungen):
  - `spoken`: heutiges Verhalten, bleibt der Standard bis zur Freigabe.
  - `tone`: das Verhalten aus B.1.
- **Die Entscheidung „Ton oder Sprache“ fällt an genau einer Stelle,** am Ende
  des Turns in `conversation.py`. Grundlage ist ein **typisiertes Ergebnis**:
  ausgeführt + Wirkung bestätigt, Frage, Fehler, Teilerfolg, Abfrage-Antwort.
  Der Antworttext wird **nicht** per Regex untersucht. Erweitere die
  Ergebnistypen dort, wo sie fehlen, statt aus dem Text zu raten.
- **Nach Kanal unterscheiden:**
  - **Sprachgerät** (`satellite_id` oder ein `device_id`, das zu einem
    Satelliten oder Mediaplayer führt): leere Sprachausgabe plus Ton auf genau
    diesem Gerät.
  - **Text-Kanal** (Assist-Chat in der App, Websocket ohne Gerät): Es gibt
    nichts abzuspielen. Antworte mit einem knappen Text wie „✓“ oder
    „Erledigt.“, nie leer, damit der Nutzer im Chat eine Rückmeldung sieht.
    Dokumentiere die Entscheidung.
  - **Gerät ohne Abspielmöglichkeit:** Sprachausgabe wie heute, nie Stille ohne
    Rückmeldung.
- **Der Ton:**
  - ein kurzes, lizenzfreies Audio als Teil der Integration, z. B.
    `custom_components/homeintent/sounds/confirm.mp3`, bereitgestellt über
    einen HA-konformen Weg (Media Source oder statischer Pfad);
  - optional eine Option für eine eigene `media_id`;
  - keine externe URL.
- **Gilt nicht für:**
  - **Proaktive Hinweise und Push-Nachrichten.** Sie bleiben, was sie sind.
  - **Bestätigungsfragen** zu kritischen Aktionen (Schlösser, Alarm, Tore,
    Skripte mit kritischer Wirkung). Sie bleiben gesprochen.
- **Folge-Turns:** Ein „Ja“ auf eine Vorschau, das ausführt, ergibt einen Ton.
  Ein „Nein“ ergibt eine kurze Sprachausgabe („Abgebrochen.“), weil sich nichts
  geändert hat. Prüfe, ob das ein Fehler im Sinne von B.1 ist, und begründe die
  Wahl.
- **„Warum ist X an?“ und `execution_trace`:** Der Trace bleibt vollständig.
  Nur die Ausgabe ändert sich.

### B.4 Tests

- **Matrix aus allen Ergebnisarten aus B.1 × beiden Stilen × allen Kanälen**
  (Satellit, Text, Gerät ohne Abspielmöglichkeit). Geprüft werden:
  - Sprachtext leer bzw. nicht leer,
  - Anzahl der Ton-Ausgaben (genau 0 oder 1, nie doppelt),
  - der Zielgerät-Bezug.
- **Teilerfolg, unbestätigte Wirkung und „nichts ausgeführt“** sprechen **immer**,
  auch im Stil `tone`. Diese Tests sind Pflicht.
- **Live:** Ergänze `sim/` um eine simulierte Assist-Satellite-Entität (bzw.
  einen Satelliten-Mediaplayer) in `haus_sim`, die Ansagen und abgespielte
  Medien protokolliert. Das Testbett hat heute keine.
  - Szenarien für Erfolg (genau ein Ton, kein TTS), Rückfrage, Fehler,
    Teilerfolg und Abfrage, jeweils im Stil `tone`.
  - Für die Pipeline: Prüfe über die echte Assist-Pipeline (`assist_pipeline/run`
    mit Text-Eingabe und TTS-Ende), dass bei Erfolg kein TTS erzeugt wird.

---

## Teil C – Gates, Live, Release

- **Alle Gates aus `.github/workflows/ci.yml`:**
  - Sprach-Eval,
  - Korpus-Signaturen (neue Baseline nur mit Begründungsliste, `SAFETY_DRIFT`
    nie),
  - Shadow-Vergleich,
  - Arbiter-Vergleich,
  - beide Dev-Benchmarks,
  - alle Latenz-Budgets,
  - `regex_inventory.py --write` mit sinnvoller Einstufung,
  - pyright (voll und alle Strict-Profile),
  - pyflakes,
  - `pytest -q`.
- **Live-Testbett:** `sim/fresh_ha.sh`, `runner.py --strict` über alle
  Kategorien, `check_log.py` mit 0 Befunden. Alle bisherigen 186 Szenarien
  bleiben grün. Ergänze Live-Szenarien für A1–A6 und Teil B.
- **Version 7.9.1.** Releasebericht `docs/umsetzung-7.9.1.md` mit:
  - je Befund: Ursache, Änderung, Test (vorher rot, nachher grün);
  - der technischen Entscheidung zu Teil B mit HA-Fundstellen;
  - geänderten Erwartungen samt Begründung;
  - allen Gate- und Live-Zahlen;
  - bekannten Grenzen.

## Abschlussbericht (letzte Antwort)

1. Tabelle A1–A7 und B: umgesetzt, teilweise oder nicht, jeweils mit
   Begründung.
2. Vorher → nachher für die Prüfsätze aus `docs/nachtest-7.9.0.md`.
3. Teil B: gewählter technischer Weg, Verhalten je Kanal, was bei einem
   Fehlschlag der Ton-Ausgabe passiert.
4. Geänderte Test-Erwartungen mit Begründung.
5. Alle Gate- und Live-Ergebnisse in Zahlen.
6. Offene Punkte.
