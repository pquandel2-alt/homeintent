# Testbericht: HomeIntent 7.1.2 im simulierten Einfamilienhaus

Stand: 26. September 2026 · getestet: Commit `10d9513` (HomeIntent 7.1.2, `main`)
Testumgebung: **echtes Home Assistant 2026.9.2** (Python 3.14.7, hassil 3.12.0,
home-assistant-intents 2026.8.28), Recorder mit SQLite, lokale Kalender/Listen,
drei echte HA-Benutzer. Testbett und Szenarien liegen unter [`sim/`](../sim/README.md)
und sind reproduzierbar.

## 1. Zusammenfassung

Getestet wurde HomeIntent 7.1.2 in einem vollständig simulierten Einfamilienhaus
auf einem **echten Home Assistant 2026.9.2**: 126 Szenarien mit 309 Gesprächszügen
über drei Benutzerkonten, dazu Neustart-, Learning-Center- und Optionsfluss-Tests.

**Ergebnis: 87 von 126 Szenarien (69 %) bestanden.** Fast alle Ausfälle gehen auf
eine überschaubare Zahl von Ursachen zurück, von denen vier Kernfunktionen im
Live-Betrieb komplett abschalten, obwohl die 4244 Unit-Tests grün sind. Der
Grund ist fast immer derselbe: Die Stub-Testsuite bildet das echte Home
Assistant 2026.9 (hassil-Version, Thread-Modell, Antwortformate, Dienstschemata)
nicht exakt ab.

**Die vier wichtigsten Punkte:**

1. **HA 2026.9 liefert hassil 3.12 – damit ist jeder Satz mit „in N Minuten/
   Sekunden/Stunden …“ vor dem Befehl kaputt.** Verzögerte Push-Nachrichten
   (die Hauptfunktion von 7.1.2), relative Erinnerungen und „In 5 Minuten
   schalte …“ antworten mit „Das habe ich nicht verstanden.“ Die CI merkt es
   nicht, weil sie hassil 3.11 pinnt; mit hassil 3.12 schlagen **46 Unit-Tests** fehl.
2. **Alle zeitverzögerten V12-Situationen laufen nie.** Der Timer-Callback in
   `proactive_runtime.py` ist nicht als `@callback` markiert; HA führt ihn im
   Thread-Pool aus, `hass.async_create_task` wirft `RuntimeError`. Garage offen,
   „Später“, Licht an bei Abwesenheit, Daueranweisungen – nichts davon meldet
   sich. Mit einem Einzeiler-Fix lief die gesamte Garagen-Kette fehlerfrei.
3. **Recorder-Statistik ist gegen echtes HA vollständig defekt.** HA antwortet
   mit `{"statistics": {…}}`, HomeIntent liest die Entity-ID auf oberster Ebene
   → jede Durchschnitts-/Min-/Max-/Veränderungsfrage endet in „keine Statistikdaten“.
4. **Farbtemperatur schlägt mit einer Fehlermeldung fehl** („not a valid option
   at 'kelvin'“). `light.turn_on` akzeptiert `kelvin` in HA 2026.9 nicht mehr;
   derselbe Schlüssel steckt im Automationsgenerator.

Die Grundlagen sind dagegen solide: Licht/Rollläden/Heizung per vollem Namen,
Bestätigungen für Tor, Schloss, Ventil, Taste und Alarm, benutzergebundene
Bestätigungen, Richtlinien (Nur-Lesen, Nur-Admin, erlaubte Benutzer), Paraphrasen,
Negation, Hypothetisches, Rückfragen mit Ordinalauswahl, „hier“ über den
Satellitenbereich, Rückgängig, Kalender, benannte Timer, Automationen mit
Kategorie und Neustart-Beständigkeit sowie das Learning Center. Die Latenz ist
sehr gut (p50 26 ms, p95 78 ms).

## 2. Testaufbau

| Baustein | Umsetzung |
| --- | --- |
| Home Assistant | 2026.9.2 aus PyPI (Docker-Registry im Container gesperrt), eigene Config, Onboarding per API |
| Haus | Eigene Integration `haus_sim`: 4 Etagen, 15 Räume, ~125 Geräte in 21 Domänen mit realistischen Fähigkeiten (Farbmodi, Positionen, Lamellen, Presets, Quellen, Saugstufen …), Rollläden mit Fahrzeit, Heizungen mit Temperaturverlauf, Geräte protokollieren jeden empfangenen Aufruf |
| Personen | Philipp (Admin, Handy-Tracker), Anna (Benutzerin, Tracker), Lena (Kind, ohne Tracker) |
| Weitere HA-Bausteine | 3 Szenen, 2 Skripte, 2 „fremde“ Benutzer-Automationen, `timer`-/`input_boolean`-/`input_number`-Helfer, 2 lokale Kalender, 2 Listen, Notify-Entities, TTS-Entity, 10 Tage importierte Recorder-Statistik |
| Ablauf | `conversation/process` über WebSocket als jeweils echter Benutzer; nach jedem Satz Abgleich von Antworttext, Antworttyp, tatsächlich empfangenen Geräteaufrufen, Endzuständen, Push- und TTS-Ausgaben |
| Umfang | 126 Szenarien in 15 Kategorien, 309 Gesprächszüge, plus Neustart-, Learning-Center-API- und Optionsfluss-Tests |
| Zusätzlich | vorhandene Suiten: 4244 Unit-Tests (grün mit hassil 3.11, **46 rot mit hassil 3.12**), 8 `tests_ha`-Tests (grün) |

Rohdaten mit allen Transkripten, Geräteaufrufen und Latenzen:
[`sim/results/final.json`](../sim/results/final.json) (Repo-Stand) und
[`sim/results/final-proaktiv-mit-fix.json`](../sim/results/final-proaktiv-mit-fix.json)
(Proaktiv mit `@callback`-Fix aus F2). Lesbar mit `python sim/dump.py <datei> [szenario]`.

Jede Fehlererwartung wurde gegengeprüft: Formulierungsvarianten, direkte
Zustandsabfrage in HA und – bei den kritischen Punkten – Ursachenanalyse bis zur
Codezeile. Artefakte des Testbetts (z. B. ein Media Player, der TTS-URLs nicht
auflöst) sind nicht als HomeIntent-Fehler gezählt.

## 3. Ergebnis nach Bereichen

| Bereich | Szenarien bestanden | Einschätzung |
| --- | --- | --- |
| Geräte (Grundsteuerung) | 11 / 21 | Licht, Schalter, Rollläden inkl. Bruchteile/Lamellen, Garage, Ventil, Taste, Szene/Skript, Alarm gut; Presets, Select/Number, Warmwasser, Befeuchter, Saugstufe, Quelle, Farbtemperatur scheitern (F4, F8, F11) |
| Sicherheit & Richtlinien | 10 / 11 | Bestätigungen, Benutzerbindung, Nur-Lesen, Nur-Admin, erlaubte Benutzer, Zielgrenze korrekt; „Verriegle die Haustür“ nicht verstanden |
| Sprache | 13 / 16 | Paraphrasen, Negation, Hypothetisches, Tippfehler, Englisch/Unsinn sicher abgelehnt; „Licht in der Küche“ fragt nach (F20) |
| Mengen & Orte | 6 / 7 | Etagen, Etagen-Aliase, „beide“, Zahlwörter gut; Ausschluss mit „und“ schaltet Falsches (F5) |
| Dialog & Kontext | 10 / 11 | Rückfragen, Ordinalauswahl, „hier“, Undo, Korrektur gut; Pronomen-Folgebefehl scheitert (F13) |
| Abfragen | 5 / 11 | Zustände, Fenster, Batterien, Waschmaschinen-Zeit gut; siehe F12 |
| Recorder-Statistik | 1 / 2 | Zustandshistorie ok; Statistik defekt (F3) |
| Kalender | 4 / 4 | Anlegen (Satz und Dialog), Abfragen, Freizeit, Verschieben, Umbenennen, Löschen – sehr gut |
| Listen | 1 / 2 | Hinzufügen/Verschieben/Entfernen ok; Abhaken/Aufräumen nicht (F27) |
| Timer | 2 / 3 | Benannte Timer, Pausieren, Fortsetzen, Verlängern, Auswahlfrage, „Lösche alle“ mit Bestätigung, TTS-Ablaufmeldung gut; `timer.*`-Helfer nicht startbar |
| Push & Erinnerungen | 5 / 7 | Sofort-Push, Ziel-Rückfragen, Fenster-Automation mit Push gut; alles mit „in N Minuten“ defekt (F1) |
| Automationen | 8 / 9 | Zeitversetzt, Zustand, Zahlenwert, Sonne, Werktage, „bleibt N Minuten“, Kategorie, Selbstlöschung, Neustart gut; Verwaltung per Name schwach (F9) |
| Proaktiv (V12) | 3 / 7 | Rauchmelder kritisch an alle Kanäle ✓, NEVER_AUTO für Garage ✓; alles Zeitverzögerte tot (F2), Daueranweisung (F7), Gruppierung (F28) |
| Agent (Gedächtnis, Ziele, Routinen) | 6 / 8 | Uhrzeit/Datum, „Sichere das Haus“, Medien pausieren, unbekannte Routine fragt nach; gespeicherte Routine per Name nicht erreichbar (F14), Komfortdialog-Falle (F10) |
| Gezielte Regressionen | 2 / 7 | bestätigen F1, F4, F9, F10 |
| **Gesamt** | **87 / 126** | |

**Mit dem Einzeiler-Fix für F2** stieg „Proaktiv“ auf 4 / 7: Die Garagen-Kette
lief vollständig. Die übrigen Ausfälle haben eigene Ursachen: Waschmaschine per
Leistungssensor (F15), Waschmaschine per Statussensor wurde mit dem Rauchalarm
gruppiert und nie zugestellt (F28), Daueranweisung scheiterte an Duplikaten (F7) –
mit genau einer Anweisung schaltete HomeIntent das Licht nach exakt 5 min aus.

Weitere Einzeltests außerhalb des Runners:

- **Neustart:** Ein ausstehender Einmal-Auftrag („Schalte in 2 Minuten das
  Kellerlicht ein“) überlebte den HA-Neustart, schaltete pünktlich und löschte
  sich selbst ✓. Ein laufender Timer ging verloren (F19).
- **Learning Center:** Panel `/homeintent` wird ausgeliefert, alle
  Lesebefehle der WebSocket-API antworten, Admin-Befehle sind für Nicht-Admins
  gesperrt (`admin_required`) ✓. Daueranweisungen erscheinen dort als Duplikate (F7).
- **Optionsfluss:** Öffnen und Speichern funktionieren unter HA 2026.9 ✓, aber
  Speichern friert die Entitätsauswahl ein (F6).
- **Latenz:** p50 26 ms, p95 78 ms über alle Gesprächszüge – sehr gut. Ausreißer
  sind nur Planausführungen, die vor der Antwort auf die Wirkung warten (F17).

## 4. Befunde und Empfehlungen

Priorität: **P0** = Kernfunktion defekt · **P1** = falsches oder gefährliches
Verhalten / dokumentierte Funktion fehlt · **P2** = Qualität, UX, Robustheit.

### P0 – Kernfunktion defekt

**F1 · hassil 3.12: „in N Minuten …“ zerstört den Befehl** (`relative_time_command_parser.py`, Grammatik `intents/de/relative_time/`)
- Beobachtet: „Erinnere mich in 30 Minuten an die Waschmaschine.“, „Kannst du mir in 10 Sekunden eine Test Benachrichtigung schicken?“ (Screenshot-Satz aus 7.1.2), „In 5 Minuten schalte das Küchenlicht ein.“ → „Das habe ich nicht verstanden.“
- Ursache: Mit hassil 3.12 matcht die Einheitenliste „Minute“ statt „Minuten“, das „n“ landet im Wildcard-Slot: `decompose("in 30 Minuten erinnere mich …")` liefert `("n erinnere mich …", 1800)` statt `("erinnere mich …", 1800)`. Formen mit Verb vorne („Schalte in 15 Sekunden …“) sind nicht betroffen.
- Reichweite: 46 Unit-Tests schlagen mit hassil 3.12 fehl (Benachrichtigungskorpus, Push-E2E, Erinnerungen, Relativzeit-Parser, zusammengesetzte Dauern).
- Empfehlung: `command_text` nur an einer Wortgrenze akzeptieren bzw. Einheitenliste so aufbauen, dass Pluralformen Vorrang haben (oder Regex statt Wildcard); CI zusätzlich mit hassil 3.12 laufen lassen.

**F2 · V12-Zeitsteuerung läuft im falschen Thread** (`proactive_runtime.py:611`)
- Beobachtet: Garagentor 80 s offen bei Schwelle 1 min → keine Meldung; „Welche Hinweise gab es heute?“ → keine. HA-Log: `RuntimeError: Detected that custom integration 'homeintent' calls hass.async_create_task from a thread other than the event loop` und `coroutine '…_schedule_check.<locals>._check' was never awaited`.
- Ursache: `_fire` ist eine normale Funktion ohne `@callback`; `async_track_point_in_utc_time` führt sie deshalb im Executor aus.
- Folge: Jede zeitverzögerte V12-Prüfung fällt aus – offen gelassene Eingänge, Licht bei Abwesenheit, „Später/Erinnere mich in 20 Minuten“, Daueranweisungen.
- Gegenprobe: Mit `@callback` lief die Kette vollständig: Push „Das Garagentor ist noch offen. Soll ich es schließen?“ an beide Haushaltsmitglieder → „Ja.“ → Tor geschlossen und Wirkung geprüft → „Warum hast du mich …?“ mit Belegen.
- Empfehlung: `@callback` ergänzen und einen Test gegen echtes HA (Thread-Prüfung) aufnehmen.

**F3 · Recorder-Statistik liest das Antwortformat falsch** (`history_query.py:246`, ebenso Vergleichspfad `:335`)
- Beobachtet: „Was war gestern der höchste Wert vom Stromverbrauch Haus?“ → „Für Stromverbrauch Haus liegen gestern keine Statistikdaten vor.“, obwohl 10 Tage Stundenstatistik vorhanden sind (per `recorder/statistics_during_period` verifiziert).
- Ursache: `recorder.get_statistics` liefert `{"statistics": {entity_id: [...]}}`; HomeIntent erwartet `{entity_id: [...]}`.
- Zusätzlich: „Wie hoch war die durchschnittliche Temperatur im Wohnzimmer gestern?“ (README-Beispiel) findet den Sensor „Temperatur Wohnzimmer“ nicht (Frage wird gar nicht zugeordnet), ebenso „Wie hat sich der Energiezähler diese Woche verändert?“.
- Empfehlung: `mapping.get("statistics", mapping)` und Test mit echtem Antwortformat; Sensorauflösung über Bereich + Messgröße statt Namenstreffer.

**F4 · Farbtemperatur: ungültiger Parameter `kelvin`** (`service_call.py:659`, `nlu/ha_automation_generator.py:542`)
- Beobachtet: „Stelle das Wohnzimmer Deckenlicht auf warmweiß.“ → „Fehler beim Ausführen: not a valid option at 'kelvin'“.
- Empfehlung: `color_temp_kelvin` verwenden (auch im Automationsgenerator, sonst entstehen ungültige Automationen).

### P1 – falsches Verhalten oder dokumentierte Funktion fehlt

**F5 · Ausschlussliste mit „und“ ignoriert das zweite Gerät** (`nlu/semantic_projection.py:1359`)
- „Mach alle Lichter aus außer der Stehlampe und dem Nachtlicht.“ → 22 Lichter aus, **auch das Nachtlicht im Kinderzimmer**. „Stehlampe und Nachtlicht“ wird als eine Phrase aufgelöst und trifft nur die Stehlampe – ohne Rückfrage oder Hinweis.
- Empfehlung: Ausschlussphrasen an „und“/„sowie“/Komma trennen; wenn ein Teil nicht auflösbar ist, abbrechen statt ausführen.

**F6 · Options-Dialog friert die Entitätsauswahl ein** (`config_flow.py`, `async_step_init`)
- Das Formular ist mit `default_exposed_entities()` vorbelegt. Wer nur einen Schalter ändert (z. B. Proaktiv an), speichert damit eine feste Liste. Später freigegebene Entitäten – hier alle `person.*` und ein neuer Waschmaschinen-Statussensor – sieht HomeIntent **stillschweigend nie**. Folgen im Test: „Wer ist zu Hause?“ → „niemand zuhause“, Waschmaschinen-Hinweis und Daueranweisung ohne Wirkung.
- Empfehlung: Feld leer vorbelegen („leer = dynamisch“) oder expliziten Schalter „Feste Auswahl verwenden“; im Learning Center/Readiness-Check anzeigen, wenn eine feste Auswahl aktiv ist und neue freigegebene Entitäten fehlen.

**F7 · Doppelte Daueranweisungen legen die Funktion lahm** (`standing_permission.py:235`)
- Jede erneute Bestätigung legt eine identische Daueranweisung an (Test: 6×). `matching()` gibt bei mehr als einem Treffer `None` zurück → nie automatische Ausführung; stattdessen „attention_budget_exhausted → history_only“. Mit genau einer Anweisung (und F2-Fix) schaltete HomeIntent das Licht nach exakt 5 min aus.
- Empfehlung: Beim Speichern deduplizieren bzw. vorhandene verlängern; „Was darfst du ohne Rückfrage?“ (wird derzeit nicht verstanden) sollte die Liste nennen.

**F8 · Notify-Entities im Anfangszustand sind blockiert** (`service_executor.py:56`)
- „Schicke an Handy Anna die Nachricht Abendessen ist fertig.“ → „Mindestens ein Ziel meldet keinen verlässlichen aktuellen Zustand.“ Eine `notify.*`-Entity steht bis zur ersten Nachricht immer auf `unknown`.
- Empfehlung: Für zustandslose Domänen (`notify`, `button`, `scene`, `script`) `unknown` nicht als unzuverlässig werten.

**F9 · Automationsverwaltung per Name/Gerät unzuverlässig**
- „Deaktiviere die Automation Flurlicht bei Bewegung.“ → nicht verstanden; „Aktiviere die Automation Flurlicht bei Bewegung.“ → wird als Gerätebefehl an „Flurlicht“ interpretiert.
- „Füge der Automation für Außenbeleuchtung die Bedingung hinzu, dass jemand zuhause ist.“ → listet **alle** sechs Automationen als „nicht eindeutig“, obwohl nur eine die Außenbeleuchtung enthält. Danach bleibt der Dialog hängen: „Dupliziere …“, „Lösche …“, „Mache die letzte Änderung rückgängig“ bekommen alle dieselbe Liste.
- „Was passiert, wenn die Bewegung im Flur erkannt wird?“ → „Ich habe das genannte Gerät nicht gefunden.“
- Positiv: „Deaktiviere die Automation für Flurlicht bei Bewegung.“ (mit „für“) und „Welche Automation steuert die Außenbeleuchtung?“ funktionieren.

**F10 · Dialoge, aus denen man nicht herauskommt**
- „Mach es hier gemütlicher.“ (zwei Personen zuhause) → „Welche Temperatur soll gelten, wenn ihr beide zuhause seid?“ – danach wird **jeder** Satz („Vergiss meine Vorliebe …“, „Was hast du dir gemerkt?“) mit „Bitte nenne einen konkreten gemeinsamen Temperaturwert in Grad.“ beantwortet.
- Geführte Automation: „Keine Bedingung.“ → „Bitte antworte mit Ja oder Nein.“ in Schleife, auch für die folgenden Schritte.
- „Abbrechen.“ wird nicht verstanden.
- Empfehlung: universelles „Abbrechen/Stopp/Vergiss es“; ein vollständiger neuer Befehl beendet eine optionale Rückfrage; „keine/ohne“ als Nein akzeptieren.

**F11 · Erweiterte Geräteoperationen: README-Beispiele und naheliegende Formulierungen scheitern**

| Satz | Ergebnis |
| --- | --- |
| „Mach die Heizung im Schlafzimmer stark wärmer.“ (README) | „kein eindeutig passendes … Gerät gefunden“ |
| „Stelle die Heizung in der Küche auf Eco.“ | dito (mit „Heizung Küche auf Eco-Modus“ funktioniert es) |
| „Wähle beim Heizprogramm Eco.“ (README) | „Das habe ich nicht verstanden.“; „Stelle das Heizprogramm auf Eco.“ → fragt „Welche Option …?“ |
| „Stelle die Poolpumpe Drehzahl auf 1800.“ | „Welchen Wert soll ich einstellen?“ (Wert war genannt) |
| „Stelle den Warmwasserspeicher auf 55 Grad.“ | „Welche Heizung meinst du?“ |
| „Stelle den Warmwasserspeicher auf performance.“ | „Auf welche Temperatur …?“ |
| „Stelle den Luftbefeuchter auf 50 Prozent.“ | „Welche Luftfeuchtigkeit in Prozent möchtest du?“ (Wert war genannt) |
| „Stelle beim Saugroboter die Saugstufe auf stark.“ | „nicht eindeutig unterstützt“ – „Saugstufe“ fehlt in `_REGISTERED_CUE` (nur „Saugstärke“) |
| „Schalte den Wohnzimmer TV auf Netflix.“ | „nicht eindeutig unterstützt“ (Quelle ist angeboten) |
| „Schalte beim Deckenventilator die Oszillation ein.“ / „Lass … oszillieren.“ | nicht unterstützt / nicht verstanden |
| „Mach den LED-Streifen blau.“ (Name „LED-Streifen Wohnzimmer“) | kein Gerät gefunden; mit vollem Namen ok |
| „Starte den Timer Waschgang.“ (`timer.*`-Helfer) | „nicht eindeutig unterstützt“ |
| „Verriegle die Haustür.“ (Schloss „Haustürschloss“) | nicht verstanden |
| „Stelle die Heizung im Büro ein.“ (README: fragt nach Temperatur) | schaltet stattdessen den Heizmodus ein; Folgeantwort „Auf 21 Grad.“ nicht verstanden |

Auffällig: Die Fehlermeldung „kein eindeutig passendes, für HomeIntent freigegebenes Gerät gefunden“ erscheint, obwohl das Gerät gefunden wurde und nur die Satzform nicht passt – das führt Nutzer auf eine falsche Fährte (Freigabe prüfen).

**F12 · Abfragen: Lücken und falsche Antworten**

| Satz | Ergebnis |
| --- | --- |
| „Was ist im Badezimmer eingeschaltet?“ (README) | „Frage erkannt, … Ziel nicht gefunden“ |
| „Welche Rollläden gibt es im Erdgeschoss?“ | „Ja, es gibt 5 Rollläden.“ (keine Namen, „Ja“ auf W-Frage) |
| „Welches Gerät verbraucht gerade am meisten Strom?“ | „Energiezähler mit 18234.7 Watt“ – kWh-Zähler als Leistung gewertet |
| „Wie hoch ist der CO2-Wert im Wohnzimmer?“ | „unknown Grad.“ |
| „Wie warm ist es draußen?“ | „keine Wetter-Entität“, obwohl „Außentemperatur“ im Garten/Außenbereich existiert |
| „Wie viel Strom verbraucht das Haus gerade?“ / „durchschnittliche Temperatur im Haus“ / „Auf wie viel Grad ist die Heizung … gestellt?“ | nicht zugeordnet |
| „Wie viele Lichter sind an?“ → „Welche davon sind im Erdgeschoss?“ → „Schalte die aus.“ | 2. Antwort wiederholt nur die Anzahl, 3. „Keine passenden Ergebnisse“ |
| „Wer ist zu Hause?“ ohne freigegebene Personen | „Laut Home Assistant ist derzeit niemand zuhause.“ – irreführend; richtig wäre „keine Personen freigegeben“ |
| „Wie warm ist es im Wohnzimmer?“ | „21 Grad.“ – auf ganze Grad gerundet (README zeigt „21,6 Grad“) |
| „Wie lange war das Fenster heute geöffnet?“ (4 s) | „0 Minuten“ |

**F13 · Folgeäußerungen aus dem README scheitern**
- „Pausiere das Küchenradio.“ → „Bitte weiterspielen.“ → nicht verstanden.
- „Starte das Küchenradio.“ → „Kannst du es pausieren?“ → nicht verstanden.
- „Schick Anna eine Nachricht, dass das Essen fertig ist.“ (sofort, ohne Zeit) → nicht verstanden (mit Zeitangabe funktioniert „Sag Anna morgen um 8 Uhr Bescheid …“).

**F27 · Aufgabenlisten: Abhaken und Aufräumen scheitern**
- „Markiere Milch und Brot als erledigt.“ (README) → „Das habe ich nicht verstanden.“
- „Lösche alle erledigten Einträge.“ (README) → „Ich finde keinen eindeutig änderbaren passenden Termin.“ – der Satz landet im **Kalender**-Pfad.
- Hinzufügen mehrerer Einträge, Abfragen, Entfernen und Verschieben zwischen Listen funktionieren.

**F14 · Routinen und Ziele mit fest verdrahtetem Vokabular** (`goal_intent.py:331`, `:341`)
- Nur „schlafengehen“, „filmabend“ und „abwesenheit“ sind als Routinenamen erkennbar; eine per `homeintent.save_routine` gespeicherte Routine „Lesezeit“ ist per Sprache nicht erreichbar („Bereite die Lesezeit vor“, „Starte die Routine Lesezeit“). Zielbereiche sind auf vier feste Räume begrenzt.
- Empfehlung: Namen und Bereiche aus Routinen-Store bzw. Area-Registry beziehen.

**F15 · Gerät-fertig-Erkennung nur mit Textzuständen** (`situation_detection.py:39`)
- Erkannt werden nur Zustände wie `running → finished`. Die Optionsauswahl erlaubt aber Leistungs- und Binärsensoren (in der Praxis häufig: Zwischenstecker mit Leistungsmessung); diese lösen **nie** aus. Mit Text-Statussensor funktionierte die Meldung sofort.
- Empfehlung: Leistungs-Schwelle mit Mindestdauer unterstützen oder die Auswahl auf passende Sensoren beschränken und warnen.

### P2 – Qualität, UX, Robustheit

- **F16 · Englisch in deutschen Antworten:** „Wohnzimmer Rollladen links closed“, „Küchenradio paused“, „Gesamtrisiko high“, „Antwort: accepted“, „Gespeichert sind 1 preference.“
- **F17 · Blockierende Antworten:** „Sichere das Haus.“ → „Ja.“ antwortete nach **25 s**, das Garagen-„Ja“ nach 6 s (Wirkungsprüfung vor der Antwort). Sprachsatelliten brechen solche Pipelines ab. Empfehlung: sofort bestätigen („Ich schließe das Garagentor …“), Wirkung asynchron prüfen und nur bei Fehlschlag nachmelden.
- **F18 · Blockierendes I/O im Event-Loop:** `engine.py:820` lädt den Shadow-Parser (YAML-Dateien) lazy während eines Gesprächszugs im Fehlerpfad (`understanding_feedback`); HA protokolliert „Detected blocking call to open/scandir“.
- **F19 · Laufende Timer verschwinden beim Neustart stillschweigend** („Welche Timer laufen?“ → „Es läuft kein Timer.“). HA-native Timer sind flüchtig; HomeIntent sollte nach einem Neustart darauf hinweisen oder benannte Timer persistieren.
- **F20 · „Licht in der Küche an“** fragt zwischen „Kücheninsel“ und „Küchenlicht“, „Licht im Bad an“ schaltet dagegen nur eines von zwei Lichtern – uneinheitlich. HAs Standard-Agent schaltet alle Lichter des Raums.
- **F21 · „Schalte alle Lichter im Haus aus“ / „Mach überall das Licht aus“** werden nicht verstanden (nur „im ganzen Haus“).
- **F22 · Antworttyp:** Kalender- und Automationsabfragen liefern `action_done` statt `query_answer` (relevant für Clients/Satelliten).
- **F23 · „Was hast du dir über mich gemerkt?“** nennt nur die Anzahl, nicht den Inhalt.
- **F24 · Vorhersagefragen** („Wann ist das Büro warm?“) enden in „Frage erkannt, Ziel nicht gefunden“ statt der dokumentierten ehrlichen „Kein-Modell“-Antwort.
- **F25 · V12-Verlauf** speichert jeden Hinweis pro Empfänger doppelt; die Erklärung „per Push-Nachricht mit Antwortknöpfen“ stimmt bei Notify-Entities nicht (dort gibt es seit 7.1.2 keine Knöpfe).
- **F28 · Gruppierte Hinweise gehen verloren:** Die Meldung „Waschmaschine fertig“ wurde mit einem kurz zuvor gesendeten Rauchalarm „gruppiert“ (`grouped_with_recent_message`) und danach nur im Verlauf abgelegt, nie zugestellt. Gruppierung sollte zusammenfassen, nicht verwerfen – und kritische Meldungen sollten nicht das Budget für normale Hinweise verbrauchen.
- **F26 · Nicht-Admins dürfen standardmäßig Automationen anlegen** (`allow_non_admin_automations` = true). Für Haushalte mit Kinderkonten wäre „aus“ der sicherere Standard.


## 5. Was gut funktioniert

- **Sicherheitsmodell:** Garage, Schloss, Ventil, Taste und Alarm werden nur nach
  „Ja“ geschaltet, „Nein“ bricht sauber ab, eine Bestätigung kann nur der
  Benutzer abgeben, der gefragt wurde („Diese Bestätigung gehört zu einem anderen
  Benutzer.“). Nur-Lesen-, Nur-Admin- und Benutzer-Allowlist greifen mit klaren
  Meldungen; kritische Alarmfunktionen sind für Nicht-Admins gesperrt.
- **Nicht raten:** „ein paar Lichter“, „Mach das Licht an“ ohne Raum, zwei
  Nachttischlampen → Rückfrage; Auswahl per „die linke“, „die zweite“; ungültige
  Auswahl verwirft die Rückfrage nicht. Negation, Hypothetisches und „Was passiert,
  wenn …“ lösen nie eine Aktion aus.
- **Natürliche Sprache für Kernbefehle:** alle acht Paraphrasen für „Küchenlicht an“
  mit vollem Namen, Zahlwörter („zweiundzwanzig Grad“), Bruchteile („zur Hälfte“,
  „drei Viertel“), Etagen und Etagen-Aliase („oben“), freie Wortstellung,
  Tippfehler („Küchenlihct“), Entity- und Bereichsaliase („Leselampe“, „Stube“).
- **Kontext:** „Und in der Küche?“, „Und jetzt wieder aus“, „Mach das rückgängig“,
  „Nein, ich meinte im Esszimmer“ (nimmt die erste Aktion zurück), „hier“ über den
  Bereich des Assist-Geräts, raumlose Befehle im Satellitenraum.
- **Kalender** komplett, **benannte Timer** komplett, **Automationen** aus Sprache mit
  sauberem YAML, Kategorie „HomeIntent“, Selbstlöschung und Neustartfestigkeit.
- **Push 7.1.2:** Ohne Ziel ein klarer Konfigurationshinweis, bei zwei Zielen
  Rückfrage statt Broadcast, gebundener Benutzer erhält genau eine Nachricht;
  „Benachrichtige mich sobald im Wohnzimmer ein Fenster geöffnet wird“ erzeugt eine
  funktionierende Automation mit formuliertem Text.
- **V12 (mit F2-Fix):** Garagen-Kette inklusive Wirkungsprüfung und belegter
  „Warum?“-Antwort; Rauchmelder umgeht alle Grenzen und geht an Push und
  Lautsprecher; Garage kann nicht als Daueranweisung automatisiert werden.
- **Learning Center** mit sauberer Rechtetrennung; **Performance** weit unter dem
  100-ms-Budget.

## 6. Empfehlungen für Test und CI

1. **Testabhängigkeiten an Home Assistant koppeln.** `requirements-dev.txt`
   pinnt hassil 3.11.0, HA 2026.9.2 bringt 3.12.0 mit. Die Stub-Suite sollte
   zusätzlich (oder ausschließlich) gegen die hassil-/intents-Version laufen,
   die `requirements-ha-test.txt` festlegt; ein Matrix-Job fängt Upgrades ab.
2. **Das Live-Testbett in die CI aufnehmen** (nächtlich oder vor Releases):
   `sim/` startet ein echtes HA in ~30 s, `runner.py` liefert JSON. Die P0-Fehler
   dieses Berichts wären dort sofort rot gewesen – die Stub-Tests verwenden
   Antwortformate (Recorder), Thread-Modell (`@callback`) und Dienstschemata
   (`kelvin`), die vom echten HA abweichen.
3. **HA-Warnungen als Testfehler behandeln.** HA meldet den Thread-Fehler
   und blockierendes I/O im Event-Loop bereits im Log.
4. **Stub-Tests mit realistischen Häusern.** Viele E2E-Tests nutzen 5–10
   Entitäten; Fehler wie die Ausschlussliste oder die eingefrorene
   Entitätsauswahl zeigen sich erst mit einem vollen Haus.

## 7. Reproduktion

```bash
uv venv --python 3.14 havenv && uv pip install --python havenv/bin/python -r requirements-ha-test.txt
cd sim && HASS=../havenv/bin/hass ./run_ha.sh && ../havenv/bin/python bootstrap.py
../havenv/bin/python runner.py --out results/run.json      # alle Szenarien
../havenv/bin/python dump.py results/run.json pro-garage   # Transkript eines Szenarios
```

## 8. Nachtest mit HomeIntent 7.2.1

Alle 28 Befunde wurden in 7.2.1 behoben, jeweils mit Regressionstest
(Stub-Suite, bei F2/F3/F4/F8 zusätzlich gegen echtes Home Assistant in
`tests_ha/`). Nachtest am 26.09.2026 auf frischem Testbett
(`sim/fresh_ha.sh`: ohne `.storage`, Datenbank und Laufzeit-Automationen),
gleiches Haus, gleiche Szenarien, Home Assistant 2026.9.2 mit hassil 3.12.0.
Ergebnis: [`sim/results/nach-fix.json`](../sim/results/nach-fix.json).

| Bereich | 7.1.2 | 7.2.1 |
|---|---|---|
| Geräte | 11 / 21 | 21 / 21 |
| Sicherheit | 10 / 11 | 11 / 11 |
| Sprache | 13 / 16 | 16 / 16 |
| Mengen/Orte | 6 / 7 | 7 / 7 |
| Dialog | 10 / 11 | 11 / 11 |
| Abfragen | 5 / 11 | 11 / 11 |
| Statistik | 1 / 2 | 2 / 2 |
| Kalender | 4 / 4 | 4 / 4 |
| Listen | 1 / 2 | 2 / 2 |
| Timer | 2 / 3 | 3 / 3 |
| Push | 5 / 7 | 7 / 7 |
| Automationen | 8 / 9 | 9 / 9 |
| Proaktiv | 3 / 7 | 7 / 7 |
| Agent | 6 / 8 | 8 / 8 |
| Gezielte Regressionen | 2 / 7 | 7 / 7 |
| **Gesamt** | **87 / 126** | **126 / 126** |

Kein vorher grünes Szenario ist rot geworden. Das Home-Assistant-Log des
Laufs enthält keine HomeIntent-Tracebacks, keine Thread-Warnungen und keine
„Detected blocking call“-Meldung (`sim/check_log.py`: 0 Befunde).

**Korrigierte Szenarioerwartungen** (Verhalten war richtig, die Erwartung nicht):

- `dev-fan`: `fan.turn_on` mit `percentage` ist das dokumentierte Äquivalent
  zu `fan.set_percentage`; geprüft werden weiterhin Aufruf und 60 %.
- `dev-lock`: Verriegeln ist als hohes Risiko eingestuft und wird erst nach
  „Ja“ ausgeführt; das Szenario bestätigt jetzt (Sicherheitsgrenze unverändert).
- `pro-washer-status`: startet ~10 s nach dem Hinweis aus `pro-washer`;
  V12 fasst Info-Hinweise innerhalb von 2 Minuten bewusst zu einer
  Sammelnachricht zusammen. Die Prüfung wartet jetzt auf diese Nachricht.
- Testbett: `haus_sim.reset` aktualisiert die gespiegelten Temperatursensoren
  sofort (vorher 10 s `unknown`, Artefakt in q-measure/q-compare).

**Handnachweise mit dem echten Home Assistant** (Transkripte gekürzt):

1. Garage 1 min offen → Push an beide Handys „Das Garagentor ist noch offen.
   Soll ich es schließen?“ → „Ja.“ → „In Ordnung, ich schließe das Garagentor
   jetzt, prüfe die Wirkung und melde mich nur, falls es nicht klappt.“ →
   `cover.close_cover`, Zustand `closed`.
2. Daueranweisung zweimal bestätigt → „Diese Daueranweisung gab es schon; ich
   habe sie verlängert.“; „Welche Daueranweisungen gibt es?“ → „1 Daueranweisung
   aktiv …“; niemand zu Hause → Licht nach 5 min aus (`light.turn_off`).
3. „Erinnere mich in 30 Sekunden an die Waschmaschine.“ → „Ja.“ → nach 30 s
   Push „Erinnerung: die Waschmaschine.“ an „Handy Philipp“.
4. „Wie war die Durchschnittstemperatur gestern im Wohnzimmer?“ → „Der
   Durchschnitt von Temperatur Wohnzimmer betrug gestern 20,8 Grad.“

Dabei zusätzlich gefunden und in 7.2.1 behoben: Das Kompositum
„Durchschnittstemperatur“ und „im Schnitt“ wurden nicht als Statistikfrage
erkannt; „Welche Daueranweisungen gibt es?“ antwortete als `action_done`;
der V12-Verlauf fasste Empfänger unter echtem HA nicht zusammen
(Zeitstempel im Mikrosekundenabstand) und ein angenommener Vorschlag nannte
noch „Antwortknöpfe“.

**Produktentscheidungen:**

- F15: Leistungssensoren melden „fertig“, wenn nach einem Lauf über 10 W die
  Leistung eine Minute unter 5 W bleibt; binäre Sensoren bei `on → off`.
- F19: Nach einem Neustart verlorene Timer werden bei der nächsten Timerfrage
  benannt, nicht stillschweigend neu gestartet (der ursprüngliche Satellit ist
  nach dem Neustart unbekannt).
- F20: „das Licht im <Raum>“ schaltet alle Lichter eines Raums mit mehreren
  Lichtern (wie der Home-Assistant-Agent); ein genanntes Gerät oder „die
  Lampe“ fragt weiterhin nach.
- F26: Neue Installationen erlauben das Anlegen von Automationen nur
  Administratoren. Bestehende Einträge behalten ihr Verhalten, weil eine
  stille Umstellung auch Sprachsatelliten ohne Benutzerkontext sperren würde.

**CI:** Die Stub-Suite läuft zusätzlich mit hassil aus
`requirements-ha-test.txt`; der Job „Live-Testbett“ startet das Testbett bei
jedem Push und scheitert an roten Szenarien und an HomeIntent-Warnungen im
HA-Log; die Proaktiv-Ketten mit langen Wartezeiten laufen nächtlich
(`.github/workflows/nightly-live.yml`).
