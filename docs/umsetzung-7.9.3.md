# HomeIntent 7.9.3 – Befunde aus dem Nachtest 7.9.2 und neue Fähigkeiten

Auftrag: `sim/PROMPT_7.9.3.md`, Grundlage `docs/nachtest-7.9.2.md` (beide im
Zweig `claude/sleepy-meitner-xd7oux`). Ausgangsstand 7.9.2 auf `main`
(`e43fd9c`). Arbeitszweig `claude/homeintent-7-9-3-6jotis`.

Entscheidung des Projekteigentümers: alle Befunde (B1–B6 des Nachtests =
A1–A6 hier) und alle Erweiterungen (E2–E8 = B1–B7 hier) werden umgesetzt. Der
Test im echten Haus ist nicht Teil dieses Auftrags.

Alles bleibt lokal und deterministisch, ohne Satzlisten: erkannt werden
Konstruktionen, die Tests erzeugen ihre Paraphrasen kombinatorisch. Die
Sicherheitsgrenze ist unverändert: Grounding → Validator → gesprochene
Vorschau → ausdrückliches „Ja“ → Schreiben. `service_executor` bleibt der
einzige Schreibweg für Geräte (auch Musikbefehle laufen über ihn), die
Negations-Sperre in `conversation.py` ist unberührt, Zugangsregel,
Eigentümerrechte und Haushalts-Satellit aus 7.9.1/7.9.2 gelten weiter.
Lesende Antworten (Wetter, Personen, „Was läuft?“, Bericht anzeigen) kommen
ohne „Ja“; alles, was schaltet oder dauerhaft anlegt, geht über Vorschau und
„Ja“ bzw. die Execution Policy.

„Vorher rot“ heißt: dieselbe Testdatei mit denselben Test-Helfern
(`tests/_ha_sim.py`, `tests/_testhaus.py`, Fixture `tests/data/testhaus.json`)
gegen den unveränderten Stand 7.9.2 (Worktree von `e43fd9c`) ausgeführt.

| Testdatei | Punkt | Fälle | vorher rot | nachher |
|---|---|---:|---:|---|
| `test_already_met_793.py` | A1 | 60 | 41 | grün |
| `test_vacation_793.py` | A2, A3 | 236 | 178 | grün |
| `test_event_summary_793.py` | A4 | 68 | 36 | grün |
| `test_no_placeholders_793.py` | A5 | 47 | 30 | grün |
| `test_small_things_793.py` | A6 | 9 | 9 | grün |
| `test_house_report_793.py` | A6 (Heimkommen), B6 | 159 | 159 | grün |
| `test_weather_793.py` | B1 | 68 | 68 | grün |
| `test_presence_793.py` | B2 | 52 | 51 | grün |
| `test_media_793.py` | B3 | 101 | 98 | grün |
| `test_rain_irrigation_793.py` | B4 | 32 | 31 | grün |
| `test_tone_793.py` | B5 | 302 | 300 | grün |
| `test_monitor_edit_793.py` | B7 | 37 | 36 | grün |
| **Summe** | | **1171** | **1037** | |

Die vorher grünen Fälle sind Gegenproben, die schon 7.9.2 richtig behandelte
(z. B. „Schalte den Urlaubsmodus ein“ bleibt Gerätebefehl, Personen-Aussagen
bleiben Urlaub, „Stopp“/„Halt“ bleiben Abbruch, „Wer ist zuhause?“ behält
seine Antwort, Überwachungen ohne erfüllte Bedingung fragen nichts).

## Teil A – Befunde

### A1 (B1 = E1): Beim Anlegen schon erfüllte Bedingung

- **Ursache:** Zustands- und Grenzwertauslöser feuern nur beim Übergang. Was
  beim Anlegen schon erfüllt ist (Batterie Bad 14 %, Rauchmelder 9 %), meldet
  die Überwachung nie; die Vorschau schwieg dazu.
- **Änderung:**
  - Neues Modul `already_met.py`. `already_met()` prüft für jede
    Benachrichtigungs-Überwachung den aktuellen Zustand aller betroffenen
    Entitäten gegen die erzeugte HA-Konfiguration (Zustand, `numeric_state`
    über/unter, `not_from`, Kombinationen mit Bedingungen). Ein Auslöser mit
    `for` zählt nur, wenn der Zustand schon so lange besteht
    (`last_changed`); „nicht erreichbar“ zählt sofort. Ereignis-Klassen
    (Tür wird geöffnet) zählen nicht – „wird geöffnet“ ist kein Zustand.
  - Die Vorschau nennt die Geräte mit Wert („Batterie Fenstersensor Bad
    (14 %) und Batterie Rauchmelder oben (9 %) liegen schon darunter; das
    meldet die Überwachung erst, wenn es sich einmal ändert und wieder
    eintritt. Soll ich dir das jetzt gleich schicken? Sag „Ja“ (einrichten
    und jetzt schicken), „Nein“ (nur einrichten) oder „Abbrechen“.“). Mehr als
    vier Geräte: „… und N weitere“.
  - Antwort: Ja → anlegen und sofort die Nachricht über `AgentDelivery`
    (dieselbe Empfängerauflösung wie die Überwachung, kein neuer
    Schreibweg); Nein bzw. „nur einrichten“ → nur anlegen; Abbrechen → nichts.
    Andere Antworten wiederholen die Frage.
  - Gruppen melden je Gerät: Benachrichtigungs-Überwachungen über mehrere
    Entitäten laufen im Modus `queued`, damit zwei Geräte kurz nacheinander
    zwei Nachrichten ergeben.
- **Test:** Überwachungsart (Batterie, Fenster, Leistung, nicht erreichbar,
  Kombination „Fenster offen und niemand zuhause“) × keine/einige/alle
  erfüllt × Ja/Nein/Abbrechen; HA-Nachbildung: ein später erfülltes Gerät
  ergibt genau eine Nachricht. Live: Batterie- und Fensterfall.

### A2 (B5, Regression): „Stell das Heizprogramm auf Urlaub“

- **Ursache:** Seit 7.9.2 erkannte die Urlaubs-Konstruktion das Wort
  „Urlaub“ überall, auch als Wert eines Programms.
- **Änderung:** `vacation.device_value_reading()` – steht
  Urlaub/Ferien/Abwesend als **Wert** nach „auf“ bzw. nach einem
  Einstellungs-Nomen (Programm, Modus, Preset, Betriebsart, Profil …) und ist
  der Satz keine Aussage über Personen („ich bin/wir sind … weg“), ist es ein
  Gerätebefehl. Der Urlaubsmodus entsteht nur aus einer Personen-/Reise-Aussage
  oder „Urlaubsmodus bis …“.
- **Test:** Paraphrasen beider Seiten (Gerät × Verb × Wert; Personen × Zeit);
  der Entwicklungs-Benchmark-Fall bleibt grün. Live: der Select wechselt
  auf „Urlaub“, der Urlaubs-Helfer bleibt aus.

### A3 (B2): Urlaub beenden

- **Ursache:** Nur „Urlaub vorbei“ war erkannt; „Wir sind wieder da“ blieb
  unverstanden, der Helfer blieb an.
- **Änderung:** Konstruktion statt Liste in `vacation.py`: Rückkehr (wieder
  da, zurück, heim, angekommen, wieder zuhause) **oder** Ende (vorbei, zu
  Ende, beenden, aus, abschalten) bezogen auf Urlaub, Reise oder Abwesenheit
  bzw. auf die sprechenden Personen. Bei aktivem Urlaubsmodus fragt jede
  Rückkehr-Aussage „Willkommen zurück! Der Urlaubsmodus läuft noch bis …
  Soll ich ihn beenden?“; „Ja“ nimmt alles zurück (bisheriger Endpfad).
  Ohne aktiven Urlaubsmodus: ein ausdrückliches Ende („Urlaub vorbei“) sagt
  ehrlich „Der Urlaubsmodus ist nicht aktiv; ich habe nichts geändert.“; eine
  reine Rückkehr-Aussage bleibt frei für eine Ankunfts-Szene bzw. -Routine
  (ohne Wirkung, wenn es keine gibt).
- **Test:** Paraphrasen-Matrix (Subjekt × Rückkehr/Ende × Bezug × aktiv/aus).
  Live: nach dem Beenden ist der Helfer aus und es kommt keine
  Urlaubsnachricht mehr.

### A4 (B3): Zusammenfassung

- **Ursache:** Mehrere naheliegende Formen wurden nicht erkannt; die
  Reihenfolge war bei gleicher Wichtigkeit nicht chronologisch; mehrtägige
  Abwesenheiten nannten nur Uhrzeiten; die Abfrage las alle Entitäten.
- **Änderung (`event_summary.py`):**
  - Konstruktion „Abwesenheit“ statt Liste: seit/während/als + ich/wir +
    weg/gegangen/unterwegs, „in meiner Abwesenheit“, „verpasst/versäumt“.
  - Sortierung: Wichtigkeit, innerhalb gleicher Wichtigkeit chronologisch;
    Kommen und Gehen der fragenden Person nur einmal und kurz.
  - Mehrtägig: Überschrift mit Datum und Uhrzeit, Einträge nennen den Tag,
    wenn er vom heutigen abweicht.
  - Leistung: nur relevante Klassen (Türen, Fenster, Tore, Melder, Bewegung,
    Personen, Fertig-Sensoren, HomeIntent-Ausführungen, Automationen);
    Grenzen `MAX_SUMMARY_DAYS = 7`, `MAX_HISTORY_ENTITIES = 600`,
    `MAX_EVENTS = 400` (dokumentiert im Modul).
  - Neuer Benchmark `scripts/benchmark_event_summary.py` (5000 Entitäten,
    7 Tage, 100 800 Verlaufszeilen; Budget p95 500 ms), in der CI.
- **Test:** Paraphrasen, Sortierung, Datumsanzeige, Grenzen; Benchmark.

### A5 (B4): Keine Platzhalter in gesprochenen Vorschauen

- **Ursache:** Vorschauen sprachen die Laufzeit-Vorlage („\<Gerät\>: \<Wert\>
  %“, „\<Räume\>“, „\<Gerät\> ist seit …“).
- **Änderung:** `notification_language.runtime_message()` und
  `device_health.report_message()` bilden für die Vorschau ein echtes
  Beispiel aus dem aktuellen Haus („zum Beispiel „Batterie Rauchmelder oben:
  9 %““, „… im Badezimmer ist noch Licht an.“, „Alarmanlage ist seit 10
  Minuten nicht erreichbar.“). Die Push-Nachricht selbst entsteht weiter zur
  Laufzeit. `automation_preview.quoted_message()` setzt „zum Beispiel“ vor
  jedes Beispiel, das von der festen Nachricht abweicht.
- **Test:** über alle Vorschau-Erzeuger (Monitore, Batterien, Ausfälle,
  Licht beim Gehen, Bericht, Wetter): kein „<“ oder „>“ in gesprochenen
  Antworten.

### A6 (B6): Kleinigkeiten

- „Was hat heute am meisten verbraucht?“ nennt nur Geräte mit mehr als
  0 kWh; hat keines verbraucht: „Heute hat keines der N Geräte messbar Strom
  verbraucht.“
- Ohne Recorder: `history_query.warn_recorder()` schreibt je Ursache höchstens
  eine einzeilige Warnung pro Stunde, ohne Traceback; die Antwort bleibt
  ehrlich. Alle Recorder-Lesungen laufen jetzt im Executor des Recorders
  (Live-Befund, siehe unten).
- **Push beim Heimkommen mit Zusammenfassung:** „Schick mir beim
  Nachhausekommen eine Zusammenfassung.“ → Vorschau („… erstellt erst in dem
  Moment, in dem du ankommst. Ich schalte dabei nichts.“) → „Ja“. Auslöser:
  Personen-Zustand wird `home` (nicht von `unknown`/`unavailable`). Aktion:
  der neue Dienst `homeintent.send_report` (nur `report_id`), der die
  Zusammenfassung zur Laufzeit erzeugt und über `agent_delivery` **nur** an
  den bestätigten Empfänger schickt; er schaltet nie Geräte. Gleiche
  Rechte- und Empfängerprüfung wie bei Überwachungen.

## Teil B – Erweiterungen

### B1 (E2): Wetter

- **Abfragen** (`weather.py`): „Wie wird das Wetter morgen?“, „Wie warm wird
  es heute?“, „Regnet es heute noch?“, „Brauche ich einen Schirm?“, „Wird es
  am Wochenende sonnig?“, „Wie viel Wind ist morgen?“ … Die Vorhersage kommt
  aus `weather.get_forecasts` (täglich bzw. stündlich je nach Frage; der
  Aufruf liegt in `history_query.async_read_forecast` mit Kontext).
  Wahrscheinlichkeit und Menge nur, wenn die Vorhersage sie liefert. Mehrere
  Wetter-Entitäten → Rückfrage bzw. Bereichszuordnung; ohne Entität bzw.
  ohne lesbare Vorhersage eine ehrliche Antwort.
- **In Automationen:** „Wenn Regen angesagt ist, fahr die Markise ein“ →
  `TriggerType.WEATHER`: Zeitmuster alle 30 Minuten, Aktion
  `weather.get_forecasts` mit `response_variable`, dann eine
  Template-Bedingung (Regenwetter oder Wahrscheinlichkeit ≥ 50 % in den
  nächsten 6 Stunden) und „nur wenn noch nicht erledigt“ (Markise nicht schon
  zu). „Wenn es regnet“ nutzt den Regensensor. Bedingungen („nur wenn es
  heute nicht regnet“) werden als Wetter-Wächter vor die Aktionen gesetzt.
  Erzeugt nur aus Entity-IDs und geschlossenen Werten; der Validator prüft
  jede Quelle (Regensensor muss ein Regensensor sein, Regenmenge eine
  Regenmenge). Die HA-Nachbildung (`tests/_ha_sim.py`) kennt
  `weather.get_forecasts`, `response_variable`, Jinja-Templates und
  `time_pattern`.
- **Testbett:** `haus_sim` hat `weather.zuhause` („Wettervorhersage“) mit
  setzbarem Zustand und täglicher/stündlicher Vorhersage,
  `binary_sensor.regensensor` (`moisture`) und `sensor.regenmenge` (mm).
  Neue Gattung `rain_sensor` in der Ontologie (Regensensor ist kein
  Wassermelder).

### B2 (E3): Wo ist jemand?

- `presence_query.py`: „Wo ist Anna?“, „Ist jemand zuhause?“, „Seit wann ist
  Anna weg?“, „Wann ist Anna heimgekommen?“, „Wann kommt Philipp heim?“. Daten
  aus `person.*`, Zonen und Recorder. „Wer ist zuhause?“ und „Ist Philipp
  schon zuhause?“ behalten ihre bisherigen Antworten.
- **Rechte:** Nicht-Admins hören Zonen anderer Personen nur mit der neuen
  Admin-Option „Aufenthaltsort im Haushalt teilen“
  (`share_household_location`, Standard aus → nur „zuhause/unterwegs“). Nie
  Koordinaten.
- „Wann kommt … heim?“: „Wann Philipp heimkommt, kann ich nicht wissen –
  dafür habe ich keine Daten.“ Höchstens dazu die übliche Ankunftszeit aus dem
  Verlauf (Median, nur gleiche Tagesart, mindestens drei Ankünfte),
  ausdrücklich „als Gewohnheit … keine Vorhersage“.

### B3 (E4): Musik und Medien

- `media.py`: „Spiel Musik im Wohnzimmer“, „Spiel Bayern 3 in der Küche“,
  „Pause“, „Weiter“, „Nächstes Lied“, „Lauter“, „Leiser“, „Lautstärke 30“,
  „Mach die Musik aus“, „Was läuft gerade?“ (Titel und Interpret aus den
  Attributen).
- **Zielwahl:** genanntes Gerät → Raum → Quelle nur aus `source_list` →
  Satellitenraum → der spielende Player; „Weiter“ nimmt den pausierten;
  mehrere gleich gute → Rückfrage. „Musik“ ohne Quelle nimmt die letzte
  Quelle oder fragt mit Beispielen aus `source_list`. Eine unbekannte Quelle
  wird nie geraten.
- **Grenzen der Lesart:** keine Medienlesart, wenn der Satz ein anderes Ziel
  nennt (Pronomen, Timer, Skript, Szene, Überwachung, ein anderes Gerät),
  bei Bedingungen/Negation und bei „alle …“ (bleibt der Mehrfachplan mit
  Vorschau).
- Jeder Befehl läuft über den einen Gerätepfad (Policy, `service_executor`),
  die Wirkung wird wie jeder Befehl bewertet (`effect_wait` kennt
  `select_source`, `volume_up/down`).

### B4 (E5): Bewässerung „nur wenn es nicht geregnet hat“

- „Bewässere jeden Morgen um 6 Uhr 20 Minuten, aber nur wenn es nicht
  geregnet hat bzw. nicht regnen soll.“ → Bedingung aus Regensensor (letzte
  24 Stunden), ersatzweise Regenmenge, und/oder Tagesvorhersage. Die Vorschau
  nennt jede Quelle. Ohne jede Quelle: ehrliche Antwort und das Angebot ohne
  Bedingung. Ein Sofortbefehl mit Regenbedingung wird jetzt geprüft.
- Nebenbei: „um 6 Uhr 20 Minuten“ ist 6:00 Uhr plus Dauer; das
  Bewässerungsverb allein mit nackter Dauer ist eine Dauer.

### B5 (E6): Bestätigungston als Standard und zweiter Ton

- **Standard:** Neue Installationen bekommen `response_style = tone`
  (`NEW_INSTALL_RESPONSE_STYLE`, gesetzt im Config Flow). **Migration:**
  bestehende Installationen behalten ihren gespeicherten Wert; fehlt der
  Schlüssel (Installation vor 7.9.1), gilt weiter `spoken`
  (`DEFAULT_RESPONSE_STYLE`). Der Optionsdialog erklärt das.
- **Zweiter Ton** `sounds/notice.mp3` (mit ffmpeg aus zwei Sinustönen
  740 Hz/494 Hz erzeugt, lizenzfrei) für genau einen Fall: alles ausgeführt,
  aber mindestens ein Ziel hat sich in der Wartezeit nicht zurückgemeldet
  (`UNCONFIRMED`, keine Gegenrichtung, nicht `unavailable`). Optional
  (`notice_says_name`, Standard an) folgt eine sehr kurze Ansage („Stehlampe
  meldet sich nicht.“). Gegenrichtung, `unavailable`, Teilerfolg, Fehler und
  Fragen werden weiter gesprochen.
- **Optionen:** `confirmation_media_id`, `notice_media_id` (nur lokale Pfade).

### B6 (E7): Wöchentlicher Haus-Bericht

- „Schick mir jeden Sonntag um 18 Uhr einen Haus-Bericht.“ (ohne Uhrzeit:
  Rückfrage) → Vorschau mit Beispiel nach heutigem Stand → „Ja“ → Automation
  mit Zeit-Auslöser und Wochentags-Bedingung, Aktion `homeintent.send_report`.
- Inhalt (zur Laufzeit, `report_runtime.py`): schwache Batterien, nicht
  erreichbare Geräte, Verbrauch der letzten 7 Tage, ausgelöste Warnungen bzw.
  Überwachungen der Woche, auffällige Werte. Kurz in der Push-Nachricht, die
  Details auf „Zeig mir den Haus-Bericht“ bzw. „Was stand im Haus-Bericht?“
  (gespeichert in `ReportStore`). Empfänger wie bei Überwachungen; „für uns
  alle“ schickt an den Haushalt.

### B7 (E8): Überwachungen ändern statt neu anlegen

- `monitor_edit.py` + `monitoring_management` (Operation `EDIT`):
  „Ändere die Garagen-Meldung auf 15 Minuten“, „Schick die Fenster-Warnung
  auch an Anna“, „Die Batterie-Meldung erst unter 15 Prozent“, „Die
  Haustür-Meldung nur noch nachts“, „Nimm Anna aus der Fenster-Warnung raus“.
- Ablauf: die geänderte Konfiguration läuft erneut durch den Validator, dann
  „Vorher: … Nachher: … Soll ich das so ändern?“, dann „Ja“; geschrieben wird
  über die Transaktion des `AutomationExecutor`
  (`async_edit_automation`). Eine feste Nachricht, die die alte Dauer bzw.
  Grenze nennt, nennt danach die neue. Der letzte Empfänger wird nie
  entfernt.
- Rechte wie beim Verwalten (Eigentümer, Admin, gemeinsam); Mehrdeutigkeit →
  „Welche … meinst du?“.
- HomeIntent-Überwachungen im Goal-Store (Rate-Regeln): Zeitraum und Betrag
  änderbar; Empfänger und Zeitfenster dort nicht (ehrliche Antwort).

## Nachträge aus Live-Lauf und Prüfsätzen

Der Live-Lauf und die Gates fanden Folgendes; alles ist behoben und mit Tests
abgesichert:

| Befund | Ursache | Korrektur |
|---|---|---|
| „Weiter.“ nach „Pause.“ fragte „Welches Gerät meinst du?“ | „Weiter“ suchte nur spielende Player | „Weiter“ nimmt den pausierten Player (`test_resume_*`) |
| Entwicklungs-Benchmark 7.7: „Mach ma die Musik in der Küche aus“ pausierte statt auszuschalten (`unsafe_execution_count` 1) | B3 bildete „aus“ auf Pause ab | „Musik aus“ schaltet aus (`turn_off`), wie vor 7.9.3; „Pause“ pausiert |
| Arbiter-Shadow: „Pausiere alle Medien.“ führte ohne Vorschau aus | Medienlesart griff auch bei „alle …“ | „alle …“ bleibt der Mehrfachplan mit Vorschau |
| HA-Warnung „accesses the database without the database executor“ (Haus-Bericht, Verbrauch) | Recorder-Lesungen im allgemeinen Executor | `history_query._recorder_job()`: Executor des Recorders; `check_log.py` zählt die Warnung jetzt immer |
| Pyright: zwei Optional-Zugriffe in `monitor_edit.py`, ein überschatteter Name in `insights.py` | Typverengung | behoben, neue Module im Strict-Scope der CI |
| CI-Live-Lauf: Wetter-, Markisen- und Bewässerungsszenarien bekamen „Welche Wettervorhersage meinst du?“ | Das Onboarding legt met.no an; in der CI erreichbar, also zwei Wetter-Entitäten (lokal 403, daher unsichtbar). Die Rückfrage ist das verlangte Verhalten (B1). | `bootstrap.py` entfernt met.no; das Testbett nutzt nur `weather.zuhause` |
| Prüfsatz „Bewässere nur, wenn es heute nicht regnet“ zitierte „Bewässere nur“ | „nur/bloß/lediglich (dann)“ blieb an der Aktion | gehört jetzt zur Bedingung (`test_the_restricting_particle_belongs_to_the_condition`, 15 Fälle) |
| Testbett: die Sonnenuntergangs-Automation schloss während Läufen Rollläden | fremde Testbett-Automation | `initial_state: false` mit Begründung in `sim/config/automations.yaml` |

## Geänderte Test-Erwartungen

Keine Erwartung wurde abgeschwächt; jede Änderung folgt aus einem Punkt des
Auftrags und ist im Test kommentiert. (Neu und kein Alttest: B7 entfernt
Empfänger mit „Nimm Anna … raus“; „schick … nicht mehr an Anna“ trifft die
unveränderte Negations-Sperre und führt nichts aus.)

| Test | vorher | nachher | Begründung |
|---|---|---|---|
| `test_threshold_go_791`, `test_understanding_gaps_791`, `test_monitoring_79_w1/w2`, `test_monitoring_automation_783`, `test_monitoring_routing_783`, `test_sprache73_notifications`, `test_device_health_792` | Vorschau endet mit „Soll ich das so einrichten?“ | `offers_setup()` akzeptiert zusätzlich die A1-Frage „Soll ich dir das jetzt gleich schicken? …“ | A1: Ist im Testhaus beim Anlegen schon etwas erfüllt (Fenster offen, Batterie 9 %), fragt die Vorschau jetzt danach. |
| `test_integration_wave_e2e` (`PUSH_PREVIEW_9`, `PREVIEW_QUESTIONS`) | Vorschautext ohne Hinweis | mit A1-Hinweis | A1, wie oben. |
| `test_system_context_761` | nach „Ja“ keine Nachricht | sofortige Nachricht zu den schon erfüllten Geräten | A1: „Ja“ heißt einrichten **und** jetzt schicken. |
| `test_device_health_792` (Vorschau) | „\<Gerät\>: \<Wert\> %“ | „zum Beispiel „Batterie Rauchmelder oben: 9 %““ | A5: keine Platzhalter. |
| `test_effect_wait_792` (`too_late`, `partial`, `wait_zero`), `test_response_style_791` (unbestätigt) | gesprochener Satz | zweiter Ton (`notice`) | B5: nur Rückmeldung fehlt → zweiter Ton; Gegenrichtung/`unavailable`/Teilerfolg bleiben gesprochen und unverändert getestet. |
| Live-Szenario `n792-a1-delay-long` | gesprochen „… noch nicht zurückgemeldet“, kein Ton | zweiter Ton (`notice.mp3`) mit „Flurlicht meldet sich nicht.“ | B5, wie oben. |
| `test_automation_model` | Liste der Auslösertypen | plus `WEATHER` | B1. |
| `tests_ha` (Options-Flow) | `response_style` `spoken` | `tone` bei neuer Installation | B5-Migration: neue Installationen `tone`, bestehende behalten ihren Wert (eigener Test). |

## Gates

| Gate | Ergebnis |
|---|---|
| `pytest -q` (Stub-Suite) | 9677 passed, 12 skipped, 0 failed (7.9.2: 8492) |
| `tests_ha` (HA 2026.9.2, Python 3.14) | 16 passed |
| Sprach-Eval (`run_language_eval.sh`) | 463 passed |
| Korpus-Signaturen (`corpus_shadow.py --check …-7.9.2.json`) | 0 geänderte Signaturen über alle 3826 gemeinsamen Sätze; neue Baseline `corpus-signatures-7.9.3.json` (Begründung: `corpus-signatures-7.9.3-begruendung.md`) |
| Shadow-Vergleich (`--candidate identity`) | 2131 EQUIVALENT (Korpus um die neuen Szenario-Sätze gewachsen), 0 SAFETY_DRIFT |
| Arbiter-Shadow | 2161 Sätze: 2150 EQUIVALENT, 7 NOT_MEASURABLE (wie bisher), 4 BEHAVIOR_CHANGE (neue Musikbefehle „Spiel Bayern 3 in der Küche.“, „Lauter.“, „Pause.“, „Weiter.“ aus den neuen Live-Szenarien – der beobachtende Arbiter hat keinen Medienkandidaten), 0 SAFETY_DRIFT |
| Entwicklungs-Benchmark 7.7 / 7.8 | 462/503 (7.9.2: 461) / 102/107, `unsafe_execution_count` 0 / 0 |
| Latenz V6 (5000) | p95 2,7 / 4,9 / 7,7 / 2,8 ms (Budget 100) |
| Automationssprache 5000 Entitäten | p95 13,4 ms (Budget 100) |
| V10 / V11 / V12 | alle unter Budget (V12 Ereignissturm 2,2 ms von 1000) |
| Learning Center | Zusammenfassung 4,4 ms, Liste 4,5 ms, Detail 0,2 ms, Evidenz 95 ms |
| Zusammenfassung (neu) | p95 123,7 ms bei 5000 Entitäten, 7 Tagen (Budget 500 ms) |
| `regex_inventory.py --write` | SEMANTIC_SENTENCE_PATTERN 171 (unverändert), LEXICAL 402, STRUCTURAL 149, MORPHOLOGICAL 37 |
| Pyright | 0 Fehler (voll, Strict-Scope der CI inkl. 7 neuer Module, V11, V12, Learning Center) |
| Pyflakes | 0 |

## Live-Testbett

`sim/fresh_ha.sh` (frisches HA 2026.9.2, Bootstrap bestätigt `response_style
= tone` für die neue Installation), `runner.py --strict` über alle
Kategorien inklusive Proaktiv: **230/230 Szenarien bestanden** (215 bisherige
+ 15 neue „Nachtest 7.9.3“), `check_log.py` **0 Befunde** (seit 7.9.3 auch
ohne Datenbankzugriff außerhalb des Recorder-Executors). Zusätzliche
Wartezeit auf Geräte-Rückmeldung: 5 von 556 Turns warteten (p50 2,0 s, alle
gewollt verzögerten Geräte), alle übrigen 0 ms.

Die neuen Szenarien prüfen die **Wirkung**:

| Szenario | geprüfte Wirkung |
|---|---|
| `n793-a1-battery-already-low` | nach „Ja“ genau eine Push-Nachricht „Schon beim Einrichten der Überwachung erfüllt: …“; Überwachung in der Liste |
| `n793-a1-window-already-open` | Fenster offen → sofortige Nachricht; danach ein weiteres Fenster → genau eine Nachricht |
| `n793-a1-only-create` | „Nur einrichten.“ → angelegt, keine Nachricht |
| `n793-a2-heating-vacation` | `select.heizprogramm` = Urlaub, Urlaubs-Helfer bleibt aus |
| `n793-a3-back-home` | ohne Urlaub keine Wirkung; mit Urlaub Rückfrage, „Ja“ → Helfer aus, danach keine Urlaubsnachricht |
| `n793-a4-summary-date` | Zusammenfassung nach echter Abwesenheit (gleicher Tag: ohne Tagesangabe) |
| `n793-a5-real-example` | Vorschau mit echtem Raum, ohne „<“/„>“ |
| `n793-b1-weather` | Wetterfragen aus der Vorhersage (jetzt, morgen, Regen heute, Schirm) |
| `n793-b1-awning-rain` | Automation ausgelöst: ohne Regen bleibt die Markise offen, mit Regen in der Vorhersage fährt sie zu |
| `n793-b2-where` | „Wo ist Anna?“ ohne und mit Haushaltsfreigabe, nie Koordinaten |
| `n793-b3-music` | Quelle gewählt (`source` = Bayern 3), „Was läuft?“, lauter, Pause (`paused`), Weiter (`playing`), unbekannte Quelle nicht geraten |
| `n793-b4-irrigation` | nach Regen: Automation ausgelöst, Ventil öffnet nicht |
| `n793-b5-notice-tone` | Rückmeldung zu spät → zweiter Ton (`notice.mp3`) mit „Stehlampe meldet sich nicht.“ |
| `n793-b6-house-report` | am falschen Wochentag keine Nachricht; ausgelöst → Push „Haus-Bericht: …“, Details abrufbar |
| `n793-b7-monitor-edit` | Garagen-Meldung auf 1 Minute geändert → nach 75 s genau die neue Nachricht „… seit 1 Minute offen.“ |

Zwischenlauf: Der erste vollständige Lauf (229 Szenarien, vor dem
Fensterfall) fand zwei Abweichungen: `n792-a1-delay-long` erwartete noch den
gesprochenen Satz (B5, Erwartung geändert, siehe oben) und
`n793-b7-monitor-edit` zählte eine Agent-Frage eines früheren
Proaktiv-Szenarios zum Garagentor mit (Szenario prüft jetzt genau die
geänderte Nachricht).

## Bekannte Grenzen

- **Regensensor nach einem HA-Neustart:** Home Assistant behält
  `last_changed` über einen Neustart nicht. Die Bedingung „in den letzten
  24 Stunden kein Regen“ gilt deshalb bis zu 24 Stunden nach einem Neustart
  als nicht erfüllt; die Bewässerung fällt dann aus (sichere Seite). Die
  HA-Nachbildung prüft den Trockenfall; live ist er direkt nach dem Start des
  Testbetts aus genau diesem Grund nicht prüfbar.
- **Bewässerung als Sofortbefehl** („Bewässere den Garten 10 Minuten“) gibt es
  weiterhin nicht (schon in 7.9.2); „Öffne die Bewässerung“ mit Rückfrage und
  die Automation mit Uhrzeit bzw. Ereignis funktionieren.
- **Wetter als Auslöser** ist eine Prüfung alle 30 Minuten, kein Ereignis;
  die Reaktion kommt also bis zu 30 Minuten nach der neuen Vorhersage.
- **Zusammenfassung:** höchstens 7 Tage, 600 Entitäten, 400 Ereignisse; eine
  mehrtägige Abwesenheit ist live nicht nachstellbar (Recorder lässt sich
  nicht zurückdatieren) und nur in den Stub-Tests mit synthetischem Verlauf
  geprüft.
- **Goal-Store-Überwachungen** (Rate-Regeln) lassen sich nur in Zeitraum und
  Betrag ändern.
- **Arbiter-Shadow:** Für die neuen Medienbefehle kennt der (nur
  beobachtende) Arbiter keinen Kandidaten; diese Sätze erscheinen dort als
  `BEHAVIOR_CHANGE` („Arbiter: nichts“), nie als `SAFETY_DRIFT`.
- „Wir sind wieder da.“ ohne aktiven Urlaubsmodus sucht eine
  Ankunfts-Szene bzw. -Routine und sagt ehrlich, wenn es keine gibt.
