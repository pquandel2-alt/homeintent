# HomeIntent 7.9.1 – Befunde aus dem Nachtest 7.9.0 und Bestätigungston

Auftrag: `sim/PROMPT_7.9.1.md`, Grundlage `docs/nachtest-7.9.0.md` (beide im
Zweig `claude/sleepy-meitner-xd7oux`). Ausgangsstand 7.9.0 (`main` = `2cc962f`).
Arbeitszweig `claude/homeintent-7.9.1`.

Alles bleibt lokal und deterministisch. Die Sicherheitsgrenze ist unverändert:
Grounding → Validator → gesprochene Vorschau → ausdrückliches „Ja“ → Schreiben;
`service_executor` bleibt der einzige Schreibweg für Geräte, die
Negations-Sperre in `conversation.py` ist unberührt.

„Vorher rot“ heißt: dieselbe Testdatei, gegen den unveränderten Stand 7.9.0
ausgeführt (Worktree von `main`).

## Teil A – Befunde

### A1 (B1, Sicherheit): Zugänge öffnen sich nie automatisch

- **Ursache:** Automationsaktionen kannten nur Schlösser als ausgeschlossen
  (sie fehlen in der Operations-Allowlist). `cover.open_cover` und
  `valve.open_valve` waren für jeden Auslöser erlaubt, auch für „alle weg“.
  Die Vorschau nannte jedes Cover ohne Geräteklasse „Rollladen“
  (`_DOMAIN_NOUN_DE["cover"]`).
- **Änderung:**
  - `nlu/automation_access.py`: `access_openings()` ist die eine Regel. Zugänge
    sind Cover der Klassen `garage`, `gate`, `door` (ohne Klasse: Name nach
    Ontologie „Garagentor/Tor“), Ventile und Schlösser. Öffnend sind
    `TURN_ON`, Position > 0, `open_valve`, `set_valve_position` > 0,
    `unlock/open` – auch in verschachtelten Schritten (CHOOSE, REPEAT,
    ESCALATE) und über die statischen Effekte von Skripten und Szenen
    (`effect_graph.Effect`, auch Szenenzustände `open`). Unbekannte Cover
    gelten vorsichtshalber als Zugang.
  - Dieselbe Funktion nutzen der Validator (`validate_automation(model,
    entities, effects)` → `UNSAFE_ACCESS_OPENING`), die Vorschau
    (`render_automation_preview` lehnt ab statt zu fragen), die eine
    Angebotsstelle im Controller (`AutomationController.offer_preview`, von
    allen vier bisherigen Speicherstellen genutzt) und der Schreibweg nach
    „Ja“ (harte Sperre). Auch eine Aktionsänderung („Ersetze die Aktion …“)
    kann kein Öffnen einführen.
  - Ablehnung: „„Garagentor“ öffne ich nicht automatisch: Tore, Türen, Ventile
    und Schlösser öffnen sich bei mir nur, wenn du es in dem Moment selbst
    sagst. Stattdessen melde ich es dir, dann entscheidest du selbst. …“ Das
    Angebot ist dieselbe Automation mit einer Push-Nachricht an den Sprecher
    statt des Öffnens (Alias „… (nur Benachrichtigung, kein automatisches
    Öffnen)“). Lässt sich nichts ersetzen (ein Skript, das öffnet), bleibt es
    bei der Ablehnung.
  - Schließen bleibt erlaubt (mit Vorschau und „Ja“).
  - Vorschau: Cover-Name aus Geräteklasse bzw. Ontologie (Garagentor, Tor,
    Markise, Jalousie, Vorhang, Rollladen).
- **Test:** `tests/test_access_policy_791.py` – 59 Fälle (6 Auslöser × 5
  Öffnen-Formulierungen über Garagentor und Bewässerung, Schließen, Skript,
  Szene, harte Sperre, Klassen, Effekte, Validator = Vorschau, Benennung).
  Vorher 57 rot, nachher grün.

### A2 (B2, Rechte): Überwachungen und Automationen gehören jemandem

- **Ursache:** Weder Metadaten-Sidecar noch W8-Verwaltung kannten einen
  Eigentümer; `allow_non_admin_automations` galt nur für das Anlegen.
- **Änderung:**
  - `AutomationMetadata.owner_user_id` (Sidecar) und
    `AutomationSummary.owner_user_id`; gesetzt beim „Ja“, bei geplanten
    Schritten und beim Duplizieren (Kopie behält den Eigentümer).
    HomeIntent-Überwachungen (W3) nutzen die vorhandene
    `GoalProvenance.user_id`.
  - `automation_ownership.may_manage()` ist die eine Regel: Admin immer;
    sonst nur der Eigentümer; ohne Eigentümer nur ein Admin. Eine
    Sprachquelle ohne angemeldeten Benutzer ist weder Eigentümer noch Admin.
  - Geprüft wird beim Erkennen *und* unmittelbar vor dem Schreiben: W8
    (Stoppen, Pausieren, Löschen samt Bestätigung) und Automationsverwaltung
    (Aktivieren/Deaktivieren, Löschen, Pausieren, Verschieben, Duplizieren,
    Begrenzen, Aktions- und Strukturänderung).
  - Antwort: „Diese Überwachung hat Philipp angelegt; ändern kann sie nur
    Philipp oder ein Administrator.“
  - Liste: Nicht-Admins sehen nur eigene Überwachungen, Admins alle, fremde
    mit „(von Anna)“.
  - Migration ohne Datenverlust: Einträge vor 7.9.1 haben den Schlüssel
    nicht; sie werden gelesen wie sie sind (Admin-only) und nie umgeschrieben.
- **Test:** `tests/test_automation_ownership_791.py` – 32 Fälle (7
  Verwaltungsformen × mit/ohne `allow_non_admin_automations`, Eigentümer,
  Admin, anonyme Stimme, Liste, Legacy-Sidecar unverändert, W3-Goal,
  Regelmatrix, Duplikat). Vorher 28 rot.

### A3 (B4, Regression): „über/unter … geht“ ist ein Grenzwert

- **Ursache:** Die 7.8.3-Erweiterung von `_LEAVE_RE` las jedes verbfinale
  „geht“ als Weggehen.
- **Änderung:** Verbfinales „gehen“ ist ein eigenes Muster (`_GO_FINAL_RE`)
  und gilt nur dann als Anwesenheit, wenn davor keine Komparator-Wert-Phrase
  steht und kein Wort des Subjekts ein Messwert oder Gerät der Ontologie ist
  (`_goes_away`). Dazu versteht der Leser die Einheit `ppm` (nur für Sensoren
  in ppm), die für die Prüfung „CO2 über 1200 ppm“ fehlte.
- **Test:** `tests/test_threshold_go_791.py` – 87 Fälle: 8 Komparatoren × 5
  Einheiten (Grad, Prozent, ppm, Watt, ohne) × Wenn-Satz vorn/hinten, je
  „geht“, „gehen“ (Plural), „steigt/fällt“, „liegt“ – identische Trigger;
  Gegenprobe „Wenn ich gehe“, „Wenn Anna/Philipp geht“. Vorher 83 rot.

### A4 (B3): Eine gesprochene Etage schränkt immer ein

- **Ursache:** (1) Der Inaktivitätspfad baute das Subjekt neu und verlor dabei
  `place`; (2) `ground_event` filterte nur bei mehreren Kandidaten und fiel
  sonst zurück (`placed or candidates`); (3) „oben/unten“ wurde bei Bewegung
  als Rollladenzustand verbraucht.
- **Änderung:** Ort bleibt erhalten und filtert immer; ohne Gerät dort die
  ehrliche Antwort („Im Keller gibt es keinen Bewegungs- oder
  Präsenzmelder …“). Gibt es am Ort nur einen Präsenzmelder, überwacht er die
  Bewegung. Bei Bewegungsereignissen ist „oben/unten“ der Ort. Weitere
  `place.contains`-Stellen geprüft: `target_resolution` (ein benanntes Gerät
  an einem anderen Ort wird genannt, nicht still gewählt: „„Stehlampe“ ist
  nicht im Keller …“ bzw. bestehende Antwort), `conversation_learning`
  (keine Lern-Rückfrage mit Geräten außerhalb des Orts). Die übrigen Stellen
  (`situation_views`, `need_compiler`, `discourse_compiler`,
  `operable_target`, `ontology_compiler`) filtern bereits ohne Rückfall;
  `target_resolution` Z. 869 verengt nur eine Rückfrage (fragt bei leerem
  Ergebnis weiter).
- **Test:** `tests/test_place_limit_791.py` – 70 Fälle (9 Orte × 2
  Formulierungen × 3 Dauern; 4 Orte × 3 Messgrößen; benannte Geräte). Die
  Erwartung wird aus dem Haus abgeleitet, nicht gelistet. Vorher 36 rot.

### A5 (B5): Löschen geht nie an den Kalender

- **Ursache:** `parse_monitoring_management` verlangte das Verb am Satzanfang
  in Vollform und kannte „des/zu …“ nicht; „Lösch …“ fiel in die
  Kalenderverwaltung.
- **Änderung:** Kurzimperative (lösch, entfern, pausier), Modalrahmen
  („Kannst du … löschen?“), Präpositionalobjekte (vom, für das, fürs, zum,
  zur, des). Die Antwort nennt das Gesprochene („Haustür“ statt
  „haustuer“). Strukturelle Grenze `names_managed_object()`: Verwaltungsverb
  + Überwachung/Meldung/Warnung/Benachrichtigung/Automation → keine
  Kalender-, Listen- oder Erinnerungsverwaltung; ohne Treffer „Ich finde
  keine passende Überwachung oder Automation dazu.“ Die Automations-
  Grammatiken (Löschen, Aus-/Einschalten) nehmen dieselben Objektformen.
- **Test:** `tests/test_monitor_management_791.py` – 231 Fälle (9 Verbformen
  × 4 Nomen × 5 Präpositionen, mit vorhandener Überwachung; ohne Treffer;
  Automationsformen). Kalender-, Listen- und Entwurfs-Handler sind durch
  Rekorder ersetzt, die leer bleiben müssen. Vorher 127 rot.

### A6 (B6): Die eigene Rückfrage wird beantwortet

- **Ursache:** Rückfragen nach einem Teil hatten keinen Antwortpfad.
- **Änderung:** `missing_part.MissingPart` (Zeitraum, Uhrzeit, Zählbeginn,
  Gerät, Empfänger) am `GroundedEvent`, weitergereicht über
  `CompositionOutcome.part` → `AutomationClarificationResult.part`. Der
  Controller öffnet `DialogTaskKind.MONITOR_PART`. Die nächste Äußerung
  desselben Benutzers wird nur als dieser Teil gelesen (geschlossene
  Wortklassen, keine Satzrahmen) und ergänzt den Ursprungsauftrag
  deterministisch (nach „wenn“ eingefügt bzw. ersetzt genau die erfragten
  Wörter); der läuft erneut über Grounding, Validator, Vorschau, „Ja“. Kurze
  Nicht-Antworten werden erneut gefragt; „Abbrechen“ und ein neuer
  vollständiger Befehl beenden den Dialog ohne Nebenwirkung; ein anderer
  Benutzer beantwortet ihn nicht. W8 „Bis wann?“ nutzt denselben Dialog.
- **Rückfragen aus 7.9 geprüft:** Zeitraum (W3), „Bis wann soll ich prüfen“
  (W2), „Ab wann soll ich den Verbrauch zählen“ (W4), „Welchen Melder …“,
  „Welches Gerät meinst du?“, „Welchen Sensor …“, „Wen soll ich
  benachrichtigen?“ (W5, Komposition), W8 „Bis wann?“ → jetzt mit Dialog.
  Bereits vorhanden: Geräteauswahl „Welche X meinst du: A oder B?“
  (EventClarification), „Nur jetzt oder jedes Mal?“ (RECURRENCE_CHOICE),
  W6 „Wann soll ich mich melden?“. „Welches bestätigte Gerät soll ich für
  diese Push-Benachrichtigung verwenden?“ hatte keine beantwortbare Form (die
  Antwort ist eine Einstellung) und ist jetzt eine ehrliche Aussage.
- **Test:** `tests/test_missing_part_dialog_791.py` – 48 Fälle (Präpositionen
  × Mengen × Einheiten, Uhrzeiten, Gerät, Empfänger, Pause, Abbrechen, neuer
  Befehl, anderer Benutzer, Leser). Vorher 47 rot.

### A7 (B7): Lücken im Verstehen

| Lücke | Änderung |
|---|---|
| Wiederholung ohne Wenn-Satz („…, solange die Haustür offen ist“) | Intervall und „solange/bis“ dürfen getrennt stehen (`_split_separated_repeat`) |
| „alle 30 Sekunden“ | Sekunden gelesen; unter einer Minute: „höchstens einmal pro Minute“ |
| Tage und Wochen | `wochen?` in der Dauer, „einen Tag“; Push-Text „seit 7 Tagen“ |
| „zieht/verbraucht/nimmt … Watt“, „das Haus verbraucht 5 kW“ | Verbrauchsverb + Leistungseinheit = Leistung des Subjekts; „Haus/Wohnung“ = Leistungssensor ohne Raum |
| „im Wohnzimmer 2 Stunden niemand war“ | Präteritum „war/waren/gewesen“ als Anwesenheitsprädikat → Präsenzmelder |
| eingefahren/ausgefahren/hochgefahren/heruntergefahren | Endzustände (stativ ohne Fahrtrichtung); „brennt/leuchtet“ = an |
| „noch Licht an“ | Stoffnomen ohne Artikel = irgendein (ODER über alle) |
| „Energiezähler über 12000 kWh“ | Zählerstand als Grenzwert (Lemma „Zählerstand“ ergänzt) |
| „Prüfe/Überprüfe/Kontrolliere, ob …“ | `embedded_question`: Prüfverb + „ob“-Satz → Verb nach vorn → sofortige Frage |
| „bei Auffälligkeiten“, „Beobachtest du …?“ | wie W7; W8-Operation ASK beantwortet aus der Liste |
| „Welche Überwachungen laufen?“ | Kurzform (Bedingung bis zum Hauptsatz); volle Vorschau auf „Was genau macht die erste/zweite/letzte?“ (`DialogTaskKind.MONITOR_LIST`) |

- **Test:** `tests/test_understanding_gaps_791.py` – 104 Fälle. Vorher 98 rot.

## Teil B – Bestätigungston statt Sprachausgabe

### Technische Entscheidung (Home Assistant 2026.9.2)

| Fundstelle | Befund |
|---|---|
| `assist_pipeline/pipeline.py` `_get_all_targets_in_satellite_area` und `if all_targets_in_satellite_area or tts_input.strip():` (≈ Z. 1803) | Der eingebaute Ton (`ACKNOWLEDGE_PATH`) nur für den eingebauten Agenten; leere Antwort eines anderen Agenten → TTS übersprungen, Stille. |
| `assist_satellite/entity.py` `async_internal_announce` (Z. 198 ff.) | Spielt `media_id` mit `preannounce=False`, ruft aber zuerst `_cancel_running_pipeline()` – während des eigenen Turns würde die laufende Pipeline abgebrochen. |
| `assist_satellite/entity.py` `_internal_on_pipeline_event` (Z. ≈ 555–585) | Ohne TTS setzt `RUN_END` den Satelliten auf `idle`. |
| `assist_satellite/__init__.py` Service `announce` (Feature `ANNOUNCE`), statische Bereitstellung von `preannounce.mp3` über `StaticPathConfig` | Offizielle API und Vorbild für die eigene Tondatei. |
| `media_player` `play_media` mit `announce` (`MEDIA_ANNOUNCE`), `async_process_play_media_url` | Weg für Geräte ohne Satellit. |

**Gewählt:** HomeIntent setzt die Sprache leer und startet einen
Hintergrund-Task, der wartet, bis der Satellit wieder `idle` ist (höchstens
15 s, dann 0,2 s Nachlauf), und dann `assist_satellite.announce` mit
`media_id=/api/homeintent/static/confirm.mp3`, `preannounce=False` aufruft. So
gibt es keinen Wettlauf mit der Pipeline. Für einen Mediaplayer des
anfragenden Geräts `media_player.play_media` (mit `announce`, wenn
unterstützt). Der Ton `sounds/confirm.mp3` (0,34 s, zwei Sinustöne, selbst
mit ffmpeg erzeugt, keine Fremdrechte) wird wie `preannounce.mp3` über einen
statischen Pfad bereitgestellt. Eigener Ton: Option `confirmation_media_id`,
nur `media-source://`, `/local/…`, `/api/…` – nie eine externe URL.

### Entscheidung an einer Stelle, aus Typen

- `turn_outcome.py`: `EXECUTED`, `UNCONFIRMED`, `NOT_DONE`, `LEARNED`.
  `service_executor` meldet jede Gerätewirkung selbst: `EXECUTED` nur, wenn
  jedes Ziel mit bekanntem Endzustand ihn schon zeigt (ein fahrender
  Rollladen ist `UNCONFIRMED`), sonst `NOT_DONE`. Automation nach „Ja“,
  Überwachung eingerichtet/gestoppt/pausiert/gelöscht, Automation
  aktiviert/deaktiviert/gelöscht/geändert melden `EXECUTED` ausdrücklich.
- `response_style.decide_response()` wird einmal am Turn-Ende in
  `conversation._async_handle_message` aufgerufen; Grundlage sind die
  Ergebnisse, Antworttyp, Fehlercode und „Frage offen“ (dieselbe Quelle wie
  `continue_conversation`). Kein Blick in den Antworttext.
- Ton nur, wenn mindestens eine Wirkung gemeldet wurde und alle `EXECUTED`
  sind, keine Frage offen ist, kein Fehler, keine Abfrage.

| Kanal | Erfolg (`tone`) | alles andere |
|---|---|---|
| Satellit (`satellite_id` oder Gerät mit `assist_satellite` + ANNOUNCE) | leere Sprache, genau ein Ton auf diesem Satelliten | Sprache |
| Mediaplayer des Geräts | leere Sprache, ein `play_media` | Sprache |
| Text ohne Gerät | „Erledigt.“ (nie leer – im Chat soll eine Rückmeldung stehen) | Sprache |
| Gerät ohne Abspielmöglichkeit | Sprache wie bisher | Sprache |

- **„Ja“** auf eine Vorschau, das ausführt → Ton. **„Nein“** → „Abgebrochen.
  …“ gesprochen: Es ist kein Fehler im Sinne von B.1, aber es wurde nichts
  ausgeführt, und ein Ton bedeutet ausschließlich „alles ist passiert“.
- **Fehlschlag der Tonausgabe:** Warnung im Log, Zähler in
  `hass.data["homeintent_response_tone_stats"]`, und auf dem Satelliten wird
  „Erledigt.“ angesagt (TTS). Scheitert auch das, bleibt es beim Log. Ein
  Gerät, das beim Turn nicht verfügbar ist, bekommt von vornherein Sprache.
- Unverändert: proaktive Hinweise, Push, Bestätigungsfragen zu kritischen
  Aktionen; der Ausführungstrace (nur die Ausgabe ändert sich).
- Teilerfolg nennt jetzt den nicht ausgeführten Teil („Flurlicht
  eingeschaltet. Nicht ausgeführt: Stehlampe (…)“).
- Option `response_style` (`spoken` Standard, `tone`) und
  `confirmation_media_id` im Optionsdialog, `strings.json`,
  `translations/de.json`, `translations/en.json`, Diagnostics
  (`response_style`, `confirmation_media_custom`).

### Tests Teil B

`tests/test_response_style_791.py` – 116 Fälle: 13 Ergebnisarten (Erfolg,
zwei Geräte, Szene, Automation nach „Ja“, Überwachung gestoppt, Rückfrage,
Sicherheitsfrage, Vorschau, Ablehnung, „Nein“, Abfrage, Teilerfolg,
unbestätigte Wirkung) × 2 Stile × 4 Kanäle; geprüft: Sprache leer/nicht leer,
Anzahl Töne 0 oder 1, Zielgerät. Pflichtfälle Teilerfolg/unbestätigt/nichts
ausgeführt, Ton erst nach `idle`, Ton-Fehlschlag, Trace unverändert, nur
lokale Töne, typisierte Entscheidungstabelle.

Live: `haus_sim` hat jetzt `assist_satellite.kuechen_satellit` (ANNOUNCE,
ohne Raum, damit keine Raumlogik der bestehenden Szenarien berührt wird); er
protokolliert jede Ansage. Der Runner spricht als Satellit, läuft durch die
echte Assist-Pipeline (`assist_pipeline/run`, Text bis TTS, eigene Pipeline
mit HomeIntent und der Sim-Sprachausgabe) und prüft `announce_count`,
`speech_empty`, `tts`. Ergebnis im Pipeline-Lauf: Erfolg → Ereignisse
`run-start, intent-start, intent-end, run-end` (kein `tts-start`), genau eine
Ansage `/api/homeintent/static/confirm.mp3` ohne Preannounce; Rückfrage →
`tts-start`/`tts-end`, keine Ansage.

## Geänderte Test-Erwartungen

| Test | Änderung | Begründung |
|---|---|---|
| `test_conversation_automation_delete.py`, `…_query.py`, `…_toggle.py`, `test_conversation_continue_conversation.py`, `test_automation_structure_edit.py` | laufen als angemeldeter Administrator (`tests/_users.py`) statt ohne Benutzer | A2: Verwalten ohne Eigentümer oder Admin wird abgelehnt; diese Tests prüfen die Mechanik. Die Regel selbst prüft `test_automation_ownership_791.py` (inkl. anonymer Stimme). |
| `test_architecture_layers_77.py` `SERVICE_CALLERS` | `response_style` ergänzt | Teil B spielt einen Ton (`assist_satellite.announce`, `media_player.play_media`), schreibt kein Gerät; wie `native_timer`. |
| Korpus-Baseline `corpus-signatures-7.9.1.json` | 1 geänderte Engine-Signatur | „Fahr Küche und Esszimmer Rollladen runter.“ – Begründung in `docs/perf/corpus-signatures-7.9.1-begruendung.md` (A4; Gespräch unverändert, beide Rollläden). |
| Verhalten ohne Testanpassung | Ventile (Bewässerung) öffnen nicht mehr per Automation | A1 zählt Ventile ausdrücklich zu den Zugängen; angeboten wird die Benachrichtigung. |

## Gates

| Gate | Ergebnis |
|---|---|
| `pytest -q` (Stub-Suite) | 7936 passed, 12 skipped, 0 failed (7.9.0: 7189) |
| `run_language_eval.sh` | 463 passed |
| `corpus_shadow.py --check …-7.9.1.json` | 3679 Sätze, 0 geändert (gegen 7.9.0: 1 begründet, 0 IR) |
| `shadow_compare.py --candidate identity --check` | 2078 EQUIVALENT, 0 SAFETY_DRIFT |
| `arbiter_shadow.py --check` | 2101 gleichwertig, 7 nicht messbar |
| `dev_benchmark.py --check` (7.7) | 461/503 (7.9.0: 459), unsafe_execution_count 0 |
| `dev_benchmark.py … dev_benchmark_78.txt --check` | 102/107, unsafe_execution_count 0 |
| Latenz V6 (5000) | alle Formen p95 ≤ 11,7 ms (Budget 100) |
| Latenz Automationssprache (5000) | p95 25,3 ms (Budget 100) |
| Latenz V10 / V11 / V12 / Learning Center | innerhalb der Budgets (Exit 0) |
| `regex_inventory.py --write` | SEMANTIC_SENTENCE_PATTERN 173 (unverändert); neue Muster LEXICAL/STRUCTURAL |
| pyright voll / Strict-Scope (+6 neue Module) / V11 / V12 / Learning Center | je 0 Fehler |
| pyflakes | 0 |
| `tests_ha` (echtes HA 2026.9.2) | 15 passed; `test_live_recorder` scheitert in dieser Umgebung an `NameError: Recorder` in HA's `recorder/migration.py` – identisch auf 7.9.0 |

## Live-Testbett

`sim/fresh_ha.sh`, dann `runner.py --strict` über alle Kategorien (inklusive
Proaktiv) und `check_log.py`:

- Erster vollständiger Lauf: **197/198**, `check_log` 0 Befunde. Alle 186
  bisherigen Szenarien grün; neu 12 Szenarien (Kategorie „Nachtest 7.9.1“).
  Einziger Fehlschlag: `n791-a5-delete` erwartete danach eine *leere* Liste,
  aber die HomeIntent-Überwachung aus `m79-w3-rate` lebt im Goal-Store weiter
  (der Sim-Reset leert ihn nicht). Kein Produktfehler – die Prüfung fragt jetzt
  gezielt, dass die gelöschte Garagentor-Überwachung fehlt; Wiederholung grün.
- Zweiter vollständiger frischer Lauf (frisches HA, alle Kategorien inklusive
  Proaktiv, `--strict`): **198/198**, Exit 0, `check_log` **0 Befunde**.

## Bekannte Grenzen

- Eine Sprachquelle ohne angemeldeten Benutzer (typischer Wand-Satellit)
  kann Überwachungen und Automationen nicht mehr verwalten; HomeIntent sagt
  das ehrlich. Eine Zuordnung Satellit → Benutzer gibt es noch nicht.
- Ventile öffnen nicht mehr per Automation (auch keine Bewässerung nach
  Zeitplan); das ist die geforderte Regel, kann aber gewohnte Abläufe
  betreffen.
- Skripte/Szenen werden beim Anlegen geprüft; ein später geändertes Skript
  prüft HomeIntent nicht erneut (wie bisher bei der Effektprüfung).
- Ton: Kalender-, Listen- und Timer-Aktionen melden noch kein `EXECUTED` und
  werden daher auch im Stil `tone` gesprochen (im Zweifel Sprache).
- Ton: Gilt der Satellit nach 15 s nicht als `idle`, wird „Erledigt.“
  angesagt statt des Tons.
- Kosmetik: Vorschau „wenn Markise geöffnet wird“ (Artikel fehlt),
  „der Leistung Trockner“ (Artikel nach letztem Namenswort) – vorbestehende
  Benennungslogik.
- „Ist jemand zuhause?“ wird (wie in 7.9.0) nicht beantwortet; „Schau nach,
  ob jemand zuhause ist“ erbt diese Lücke.
