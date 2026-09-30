# Umsetzung 7.7.1 und 7.8

Grundlage: unabhängiger Black-Box-Test 7.7 (`docs/independent-test-7.7.md`,
`docs/perf/independent-test-7.7.json`), Auftrag `sim/PROMPT_7.8.md`.
Basis 7.7.0 (`4b3f4db`). Alles bleibt lokal und deterministisch; kein
Sprachmodell, kein ML-Modell. Parser führen nie aus; Validator, EffectGraph,
ExecutionPolicy, NEVER_AUTO und Executor bleiben die einzigen Instanzen, die
über Ausführung entscheiden. Der Testkorpus war nicht verfügbar; die
Beispielsätze des Berichts dienten nur zur Veranschaulichung, jede Änderung
ist eine Regel über Struktur, Lexikon, Modalität, Diskurs oder Ontologie.

---

# Teil A – 7.7.1: die 15 unsicheren Ausführungen

## Warum die Property-Suite diese Formen nicht erreichte

Die Invarianten aus 7.7 B9 deckten die Cluster dem Namen nach ab, ihre
Generatoren erzeugten die Formen aber nicht:

| Invariante 7.7 | Lücke im Generator |
| --- | --- |
| Selbstkorrektur | sechs feste Rahmen, nur „nein/äh“ und „ich meine“ in der Satzmitte; kein Ersatz nach einem vollständigen ersten Teil ohne Negationswort, kein Orts- oder Seitenersatz, kein Abbruch |
| Kontrafaktum | nur die Partizipien „angemacht/ausgemacht“; kein „hätte … sollen“, keine Abwägung, keine Beibehaltung mit vorangestelltem Objekt |
| – | kein Generator über zwei Turns (Ellipse), keine Zeit in der Ellipse |
| Teilausführung | nur unbekannte Wörter als Rest; kein Ergänzungsstrich, kein gemeinsamer Kopf, keine Ortsübernahme |
| – | kein Satellitenraum, keine Rückfragetexte |

Neu in `tests/test_safety_properties.py` (Abschnitt „7.7.1“): Marker-Lexikon
× sechs Satzrahmen × Registry-Namen, Ortsersatz, Abbruch; vier Partizip- und
zwei Infinitivformen in zehn Rahmen (Irrealis, Abwägung, Beibehaltung);
Zwei-Turn-Dialoge mit neuem Objekt, neuer Seite, Zeit und Ortswechsel;
Aufzählungen mit und ohne unbekannten Teil; Satellit in jedem Raum ohne
passende Gattung; Skripte mit zufälligen HIGH-Wirkungen. Die neuen
Generatoren fanden beim ersten Lauf sofort vier weitere Formen
(„…, halt.“ als Abbruch, „um 6:30“ in einer Ellipse, „Schalte A aus halt B“
ohne Komma, Satellit im Garten + „die Steckdose“ → Gartenpumpe), die mit
behoben sind.

## A1 – Selbstkorrektur als Satzstruktur (SC-1)

- **Ursache:** Korrektur war kein Teil der Satzstruktur. Nur „nein/sondern/
  stattdessen/äh“ bildeten eine REPAIR-Klausel, und nur für Zielnamen,
  Zahlen und Zeiten gab es Projektionen. „ich meine“ und „halt“ blieben
  unerkannt: beide Teile wurden gelesen („Radio und Fernseher aus“) bzw. der
  Ersatz „im Schlafzimmer“ wurde als Rest verworfen.
- **Regel:** `nlu/self_correction.py`. Ein Marker aus dem Lexikon (starke
  Marker überall: nein, nee, äh, ähm, ich meine/meinte, sorry,
  Entschuldigung, korrigiere, Quatsch, stopp, ach nee, oder nee, besser
  gesagt …; schwache nur nach einer Pause oder nach der Operationspartikel:
  halt, Moment, also, sprich, lieber, oder, doch, ach, ja …) teilt die
  Äußerung in Widerruf und Ersatz. Beide Teile werden in typisierte Felder
  zerlegt (Ziel, Seite, Ort, Wert, Operation, Zeit; Ortslexikon,
  Geräteontologie, Registry-Namen). Der Ersatz ersetzt genau seine Felder,
  die übrigen kommen aus dem Widerruf; ein Registry-Name im Ersatz bringt
  seinen eigenen Ort mit. Leerer Ersatz oder Abbruch („doch nicht“, „lass
  mal“, „vergiss es“, „stopp“) → „In Ordnung, ich mache nichts.“ Unerklärter
  Rest im Ersatz oder bloßes „oder“ → Rückfrage mit beiden Lesarten, nichts
  ausgeführt. Mehrere Korrekturen werden der Reihe nach angewandt. „…, das
  Gartentor meine ich“ (nachgestellter Marker) ist eingeschlossen.
  Automationen (wenn/sobald/falls) behalten ihre eigene Reparaturlesung.
- **Dateien:** `nlu/self_correction.py` (neu), `conversation.py`
  (Vorverarbeitung vor der Sprachanalyse).
- **Invarianten:** `test_correction_never_writes_to_a_target_only_retracted`,
  `test_place_correction_never_writes_to_the_retracted_place`,
  `test_aborted_command_never_writes`.

## A2 – Irrealis, Abwägung, Beibehaltung (SC-2, SC-8)

- **Ursache:** Die Modalität kannte nur „wäre/würde … wenn“ als Hypothese.
  „Hätte ich doch … ausgeschaltet“ und „ich überlege, ob …“ waren DIRECT;
  „lass … aus“ war im Katalog der Beibehaltungszustände nicht enthalten, und
  das Objekt vor dem Verb („Den Fernseher lass …“) wurde nicht erkannt.
- **Regel:** `Modality.IRREALIS` (Konjunktiv II hätte/wäre, nicht als
  Höflichkeitsschale an „du“ und nicht als Wunsch mit „gern“, und die Klausel
  endet auf Partizip II oder „sollen/müssen/können/haben/sein“) und
  `Modality.DELIBERATION` (Abwägungsverb mit dem Sprecher als Subjekt, „ich
  frage mich/weiß nicht, ob ich …“, „vielleicht sollte ich“). Beides steht
  in `NON_EXECUTABLE_MODALITIES`; die Konversation antwortet vor jedem Router
  und schreibt nie. Beibehaltung erkennt zusätzlich das vorangestellte
  Objekt, „X bleibt an/aus“, „X soll an bleiben“ und „aus“ als gehaltenen
  Zustand; Antwort „In Ordnung, ich lasse den Fernseher aus. Ich ändere
  nichts.“ Eine Frage „Ich frage mich, ob das Fenster offen ist“ bleibt eine
  Frage.
- **Gewollte Verhaltensänderung:** „Lass X aus“ schaltete bisher aus
  (`tests/conftest.py` führte es als Ausschaltformel). Jetzt Beibehaltung,
  wie „Lass X an“ seit 7.3.0; Golden-Fall, Engine-Tests und
  Entwicklungs-Benchmark entsprechend umgestellt. Einzige geänderte
  Korpus-Signatur (`lass das Küchenlicht aus`).
- **Dateien:** `nlu/semantic_utterance.py`, `nlu/utterance_meaning.py`,
  `nlu/semantic_catalog.py`, `conversation.py`, `engine.py`.
- **Invariante:** `test_irrealis_deliberation_and_maintenance_never_write`.

## A3 – Ellipsen-Vertrag (SC-3, SC-4, SC-6)

- **Ursache:** Der Kontextleser `match_command_followup` las nur die letzte
  Partikel und ergänzte das vorige Ziel; neu genannte Objekte, Seiten und
  Zeiten wurden ignoriert, „oben auch“ wurde zur Etagenmenge.
- **Regel:** `nlu/ellipsis_contract.py`. Jede Kontext-Lesart (Folgebefehl,
  Eigenschaft, Referenz, Frage, Befehl) läuft durch `violation`: ein neu
  genanntes Objekt (Registry-Name oder Gattung samt Kompositum-Bestimmungswort),
  eine neue Seite oder eine Zeit darf nicht durch das vorige Ziel ersetzt
  werden; die Menge wächst nie ohne „alle“. Eine Zeitangabe baut aus der
  verletzenden Lesart einen vollständigen, zeitgebundenen Satz („Schalte
  Flurlicht morgen früh ein.“), der über den normalen Zeitpfad eine
  Automation mit Vorschau wird – nie sofort. Der Diskurs-Compiler überträgt
  bei Ortswechsel („oben auch“, „im Bad auch“) die Gattung und die Rolle:
  einziges Gerät dort, gleicher Name/Raum-Stamm („Flurlicht oben“) oder das
  nach dem Raum benannte Gerät („Küchenlicht“ → „Bürolicht“); sonst
  Rückfrage. „Den rechten runter“ nimmt die eigene Operation. „um 6:30“ ist
  jetzt eine absolute Zeit im Zeitlexikon.
- **Gewollte Verhaltensänderung:** „Schalte das Küchenlicht an“ → „Im Büro
  auch“ schaltet das Bürolicht statt aller drei Bürolampen (vorher
  Mengenerweiterung).
- **Dateien:** `nlu/ellipsis_contract.py` (neu), `nlu/discourse_compiler.py`,
  `nlu/temporal_semantics.py`, `conversation.py`.
- **Invarianten:** `test_ellipsis_with_a_new_object_never_writes_to_the_previous_target`,
  `test_ellipsis_with_another_side_never_writes_to_the_previous_side`,
  `test_ellipsis_with_a_time_never_runs_now`,
  `test_ellipsis_never_widens_the_target_set`.

## A4 – Koordination ohne stille Teilausführung (SC-5, SC-10)

- **Ursache:** Ergänzungsstrich und gemeinsamer Kopf wurden nicht
  expandiert (nur ein Name wurde gefunden); ein Teil, der nur Orte nennt,
  galt als erklärt und fiel weg; der Ort des ersten Teils galt nicht für den
  zweiten (Vorschau mit allen Rollläden des Hauses).
- **Regel:** `nlu/coordination.py` vor der Sprachanalyse: „Garten- und
  Terrassenlicht“ → Kopf aus der Ontologie; „Küche und Esszimmer Rollladen“
  → je Ort ein eigenes Objekt; ein Teil nur aus Orten übernimmt Objekt und
  Operation des ersten Teils; ein Ort mit Präposition im ersten Teil gilt
  für spätere Gattungsziele ohne eigenen Ort (Registry-Namen bleiben
  unberührt, Automationen ebenfalls). Die bestehende Regel „nicht erklärter
  Rest → nichts ausgeführt“ greift danach für jeden übrigen Teil.
- **Dateien:** `nlu/coordination.py` (neu), `conversation.py`.
- **Invarianten:** `test_executed_parts_equal_named_parts_or_nothing`,
  `test_first_place_bounds_later_parts`.

## A5 – Satellitenraum hat Vorrang (SC-7)

- **Ursache:** Die Zielauflösung nutzte den Satellitenraum nur, um unter
  mehreren Kandidaten zu wählen. Bei genau einem Kandidaten anderswo wurde
  dieser still gewählt. Im Semantik-Compiler ersetzte der Raum sogar die
  Gattung durch ein anderes Gerät derselben Domain („die Steckdose“ im
  Garten → Gartenpumpe).
- **Regel:** `resolve_description`: ohne Ortsangabe und ohne passendes Gerät
  im Satellitenraum → „Im Badezimmer gibt es keinen Ventilator. Meinst du
  Deckenventilator im Schlafzimmer? Dann sag es bitte mit dem Raum.“, nichts
  ausgeführt. Der Semantik-Compiler behält im Satellitenraum die genannte
  Gattung. Die ehrliche Fehlermeldung kennt den Satellitenraum.
- **Bewusst:** Ein Gerät, dessen Registry-Name das Gattungswort selbst ist
  („Luftbefeuchter“), ist benannt, nicht beschrieben, und wird geschaltet.
- **Dateien:** `nlu/target_resolution.py`, `nlu/semantic_compiler.py`,
  `engine.py`, `controllers/devices.py`.
- **Invariante:** `test_satellite_room_is_never_left_silently`.

## A6 – Informierte Bestätigung (SC-9)

- **Regel:** `execution_policy.describe_critical_effects`: Jede Entscheidung
  mit EffectGraph nennt in ihrer Notiz jede Wirkung, deren eigenes Risiko
  HIGH oder CRITICAL ist („Das Skript Schlafen entriegelt dabei
  Haustürschloss.“). Alle Rückfragen (Geräte, Routinen, Bedürfnisse) setzen
  die Notiz vor die Frage. `PendingServiceConfirmation.scope` bleibt wie in
  7.7 an Risiko und Ziele gebunden.
- **Dateien:** `execution_policy.py`.
- **Invariante:** `test_confirmation_names_every_high_effect`.

## Shadows und Pflichtläufe 7.7.1

| Lauf | Ergebnis |
| --- | --- |
| Stub-Suite | 6358 passed, 12 skipped |
| Property-Suite | 36 Invarianten (24 → 36), CI-Profil 0 Verletzungen; Nightly-Profil dreimal mit wechselnden Seeds 0 Verletzungen |
| Korpus-Shadow gegen 7.7.0 | 1 geänderte Signatur, gewollt: „lass das Küchenlicht aus“ (Beibehaltung). Neue Baseline `docs/perf/corpus-signatures-7.7.1.json` |
| Dialog-Shadow gegen 7.7.0 | zunächst „0 Abweichungen“ gemeldet – Werkzeugfehler, korrigierte Messung: 9 gewollte Abweichungen, eine Fehllesung (siehe Teil B, „Dialog-Shadow“) |
| Arbiter-Shadow | 2045 gleichwertig, 7 nicht messbar, 0 SAFETY_DRIFT |
| Shadow-Vergleich | 2022 EQUIVALENT, 0 SAFETY_DRIFT |
| Entwicklungs-Benchmark 7.7 | 443/503 (7.7.0: 442), `unsafe_execution_count` 0 (siehe unten) |
| Live-Testbett | 161/161 ohne Proaktiv (neu: sechs Szenarien `s771-*`), Proaktiv 7/7, Push-Matrix 35/35, README-Beispiele wie 7.7.0 |
| Satzmuster | 173 (unverändert) |
| Pyright voll/Strict, Pyflakes | 0 |

Entwicklungs-Benchmark: Sieben Erwartungen wurden als gewollte Änderung
umgestellt und im Korpus kommentiert – fünf Selbstkorrekturen, die in 7.7
nur „nichts ausführen“ verlangten und jetzt den Ersatz ausführen, „Lass das
Flurlicht aus“ (Beibehaltung) und „Im Büro auch“ (Rolle statt Raummenge).

## Gemeinsame Oberfläche (Nachtrag zum 7.7.1-Release)

Der erste CI-Lauf des Release-Commits zeigte im Arbiter-Shadow drei
SAFETY_DRIFT: Der Shadow las die neuen Live-Szenarien roh („…, ich meine
den Fernseher“), die Konversation dagegen die korrigierte Oberfläche; die
rohe Lesart des Arbiters war die unsichere. Selbstkorrektur und
Koordination liegen deshalb jetzt in einer Funktion
(`nlu/surface.prepare_surface`), die Konversation und Arbiter-Shadow
gleichermaßen nutzen: 2062 gleichwertig, 7 nicht messbar, 0 SAFETY_DRIFT.
Außerdem ließ eine neue Invariante den Satellitenraum-Patch für spätere
Tests stehen; sie hebt ihn jetzt wieder auf (Stub-Suite ohne Parallelisierung
6358 passed).

## Bewusst offen (7.7.1)

- Zwei Registry-Namen ohne Konjunktion („Schalte A aus B“) ohne Marker
  werden weiter von den bestehenden Compilern gelesen; mit Marker gilt A1.
- „Im Bad auch“ nach einem Flurlicht fragt bei zwei Bad-Lichtern ohne
  erkennbare Rolle nach.

---

# Teil B – 7.8.0: Sprachverständnis und Leistung

## Vorgehen und Messung

Eigener Paraphrasen-Korpus `tests/eval/dev_benchmark_78.txt` (107 Fälle,
126 Turns, B1–B7), vor jeder Änderung geschrieben; 36 Fälle als `heldout`
markiert und bis zur Schlussmessung nicht angesehen. Die Beispielsätze des
unabhängigen Berichts sind nicht enthalten.

| Stand | dev | held-out | unsichere Ausführungen |
| --- | --- | --- | --- |
| 7.7.1 (vor Teil B) | 32/71 | 13/36 | 0 |
| 7.8.0 | 70/71 | 32/36 | 0 |

Zwei dev-Erwartungen wurden als Fehler der Erwartung korrigiert und im
Korpus kommentiert („…, weil Frieda Geburtstag hat“ ist eine Begründung und
wird ausgeführt; der Sollwert wird als „eingestellt“ genannt). Die vier
verbleibenden held-out-Fehlschläge sind nicht nachgebessert: „Den
Staubsauger losschicken“ (Partikelverb in einem Wort), „Küchenrollladen auf
halb“, „Bei Sonnenuntergang …“ (fragt „Nur heute oder jeden Tag?“ – eine
berechtigte Rückfrage, die Erwartung „Entwurf“ war zu eng) und „Wie viele
Lampen sind im Büro?“ (nennt die drei Namen statt der Zahl).

Entwicklungs-Benchmark 7.7: 443/503 → 458/503 (held-out 97 → 101/113),
`unsafe_execution_count` 0. Beide Korpora sind Entwicklungswerkzeuge der
umsetzenden Session, kein unabhängiger Nachweis.

## B1 – Rest ehrlich melden (NL-2)

- **Ursache:** Blieb ein Satzrest unerklärt, fiel die Antwort auf die
  Fähigkeitsbeschreibung des genannten Geräts zurück.
- **Regel:** Unterstützt jedes genannte Gerät die verstandene Operation
  (`INTENT_BY_DOMAIN_ACTION`), wird der Rest genannt: „Den Teil „…“ habe ich
  nicht verstanden. Ich habe deshalb nichts ausgeführt.“ Der
  Ontologie-Compiler meldet einen Rest einer einzelnen Klausel mit Ziel und
  Operation ebenso. Die Fähigkeitsmeldung kommt nur noch, wenn die Operation
  wirklich fehlt.
- **Dateien:** `engine.py` (`_unexplained_rest`), `nlu/ontology_compiler.py`.

## B2 – Höflichkeit, Dank, Begründung, Dringlichkeit (NL-1)

- **Ursache:** Diese Teile waren weder Bedeutung noch Füllwort; sie blieben
  als Rest, und „Wenn du so nett wärst, …“ / „Wäre super, wenn du …“
  öffneten den Automationspfad.
- **Regel:** `nlu/pragmatic_frames.py` erkennt Rahmen über Wortklassen:
  Höflichkeitsschalen (sei so …/wärst du so … und; hättest du die …,
  würde es dir …, + zu-Infinitiv; magst/willst du … + Infinitiv; wäre
  <Lob>, wenn du … + 2. Person), höfliche Konditionale (wenn du so … wärst,
  wenn's geht, falls möglich), Dank am Anfang oder Ende, Dringlichkeit
  (schnell, fix, zack, zügig …) und Begründungen (ein Teilsatz, der kein
  Gerät, keinen Ort außer „hier“, keinen Wert, keine Zeit und keine Operation
  nennt und weder fragt, bedingt, verneint noch modal ist). Der Verbteil wird
  in den Imperativ gebracht. Die Rahmen stehen im Sprachdokument
  (`LanguageDocument.pragmatics`); Ziel und Operation bleiben unberührt. Eine
  echte Bedingung hat ein eigenes Subjekt und bleibt Automation; eine
  eingebettete Frage bleibt Frage.
- **Invarianten:** `test_frames_never_change_target_set_or_safety_form`,
  `test_embedded_question_stays_a_question`.

## B3 – Kurzbefehle, Partikelverben, Werte ohne Einheit (NL-3, NL-4)

- **Regel:** `nlu/short_commands.py`: Gattung/Raum/Gerät + Partikel
  (an/aus/zu/auf/raus/rein/hoch/runter/halb/los) + optionaler Wert ergibt
  einen Imperativ; ein Raum allein + an/aus ist das Licht des Raums; ein
  Gerät mit Zahl ohne Frage ist ein Befehl; die Einheit folgt aus der
  Gattung (Heizung °C, Licht/Rollladen/Lautstärke %), auch in vollständigen
  Befehlen („Stell die Heizung im Bad auf 23“). Zahlwörter werden gelesen.
- **Gewollte Änderung:** „Rollladen Büro 50.“ war im Automations-Korpus ein
  Negativbeispiel (keine Automation) und ist jetzt ein Befehl (50 %). Das
  Negativbeispiel steht dort jetzt als Frage („Rollladen Büro 50?“).
- **Invariante:** `test_short_command_never_writes_more_than_the_full_command`.

## B4 – Ellipsen mit Operationsübernahme (NL-5)

- **Regel:** Der Diskurs-Compiler kennt „ebenfalls/ebenso/genauso/das
  Gleiche/dasselbe“ als Übernahme und „eins/Stufe/Tick“ als Wiederholung;
  eine Einstellung (Temperatur, Prozent) wird mit neuem Wert oder unverändert
  auf den neuen Ort übertragen; „auf 18“ ist dort ein Wert, nicht „öffnen“.
  Der Ellipsen-Vertrag aus A3 gilt weiter (Rolle statt Menge).
- **Dateien:** `nlu/discourse_compiler.py`.

## B5 – Automationssprache (NL-6)

- **Regel:** Die Aktion wird mit derselben Kurzbefehls-Analyse gelesen wie
  ein Direktbefehl (`expand_verbless_action`: nach dem letzten Komma oder
  nach einer führenden Zeitangabe). Präsenz („jemand ist im …“, „niemand
  mehr im …“) wird an Präsenzmelder, sonst Bewegungsmelder des Raums
  gebunden. Ein gesprochener Ort begrenzt die Messgröße des Auslösers
  („draußen“ → Außentemperatur), unabhängig von der Gattung der Aktion.
  Nicht-Admins ohne Freigabe erhalten die Ablehnung vor der Vorschau.
- **Gewollte Änderung:** Zwei Tests erwarteten die Ablehnung erst nach
  „Ja“; sie prüfen jetzt die Ablehnung vor der Vorschau.
- **Dateien:** `automation_language.py`, `automation_grounding.py`,
  `nlu/short_commands.py`, `controllers/automations.py`, `conversation.py`.

## B6 – Gerät vor Raum/Kontakt (NL-7, NL-8)

- **Regel:** `nlu/operable_target.py`: Ist das genannte Objekt ein Raum
  („die Garage“) oder ein Kontakt („Haustür“) und passt die Operation
  (auf/zu) nur zu einem steuerbaren Gerät gleicher Bedeutung (Tor im Raum,
  Schloss, dessen Name den Kontakt fortsetzt), wird dieses gewählt; Schloss
  auf/zu heißt auf-/abschließen. Kritische Geräte bleiben über die Policy
  bestätigungspflichtig. Die Alarmanlage nennt den Code statt „nur
  abfragen“. Zustands- und Zählfragen sowie Leistungssensoren waren im
  eigenen Korpus bereits richtig (dev); „Wie viele Lampen sind im Büro?“
  nennt Namen statt Zahl (offen).

## B7 – Dialoge und Namen (NL-9, NL-10, NL-11, Lernen)

- Bestätigung: „Ja, mach“, „los“, „klar“, „gerne“ … sind Zustimmung. Eine
  vollständige neue Frage wird beantwortet, die offene Sicherheitsfrage
  ausdrücklich verworfen („Die offene Rückfrage habe ich verworfen; es
  wurde nichts ausgeführt.“); Metafragen zum Dialog bleiben Metafragen.
- Ein exakt gesprochener voller Name schlägt die Namen, die er enthält
  („Gute Nacht Test“ vor „Gute Nacht“).
- Unbekannte Namen: „Ein Gerät „Leselicht“ finde ich nicht.“ statt „kein
  Licht (lese)“.
- „Vergiss die Sonnenlampe“ löscht den Alias (Artikel gehört nicht zum
  Namen). Makroaufrufe, Vorlieben-Aktivitäten und „Was hast du gelernt?“
  mit Aliassen waren bereits korrekt.
- „Schalte die Gruppe <Name> ein“: das Wort „Gruppe“ vor dem Namen einer
  Gruppe ist Gattung, der Name ist das Ziel.
- **Gewollte Änderung:** `test_arbitration_77` erwartete, dass eine Frage die
  Sicherheitsfrage offen hält; sie wird jetzt ausdrücklich verworfen.

## B8 – Leistung (PERF-1)

- **Ursache:** Entitätsindex (Alias-Erzeugung) und Hausgraph wurden in jedem
  Turn neu gebaut; dazu Regex-Kompilierung je Raumname, ein Scan aller
  Namen für nicht freigegebene Geräte und eine Namenstabelle je Aufruf.
- **Regel:** `structure_cache.py`: Index, Weltmodell-Gruppierungen und
  Hausgraph werden je **Strukturschlüssel** gecacht (Entitäts-, Geräte-,
  Bereichs-, Etagen-Registry, Freigabe, Namen, Aliasse – gelernte Aliasse
  und Bindungen erreichen die Schnappschüsse als Aliasse – und konfigurierte
  Beziehungen). Jede Änderung ergibt einen neuen Schlüssel; ein entzogenes
  Gerät fehlt ab dem nächsten Turn. Zustände werden nie gecacht: der Index
  speichert Entitäts-IDs und löst sie gegen die Schnappschüsse des Turns auf,
  Graph-Knoten erhalten den aktuellen Zustand. Dazu Literal-Vorprüfungen vor
  Wortgrenzen-Mustern und eine Namenstabelle je Turn.
- **Test:** `tests/test_structure_cache_78.py` (Zustand ändert den Schlüssel
  nicht, jede Strukturänderung schon; entzogenes Gerät sofort weg;
  Umbenennung, Alias, Raumwechsel sofort wirksam; Zustände live).
- **Messung** (`scripts/benchmark_turn.py`, Stub-Haus vergrößert, 96 Turns;
  neu: Option `--fresh-agent` für die 7.7-Methode mit neuem Agenten je Turn,
  Standard ist ein Agent wie in Home Assistant):

| Entitäten | Methode | p50 | p90 | p95 | p99 |
| --- | --- | --- | --- | --- | --- |
| 5000 (7.7.1) | neuer Agent | 394 ms | – | 612 ms | – |
| 5000 | ein Agent | 31 ms | 92 ms | 108 ms | 128 ms |
| 5000 | neuer Agent | 32 ms | 78 ms | 113 ms | 135 ms |
| 1000 | ein Agent | 9 ms | 21 ms | 21 ms | 27 ms |
| 133 | ein Agent | 6 ms | 12 ms | 12 ms | 16 ms |

  Zeitmessungen schwanken in dieser Umgebung spürbar; die obere Hälfte der
  Verteilung wird von Speicherbereinigungen bestimmt.

## Dialog-Shadow: Werkzeugfehler und echte Vergleiche

`scripts/dialog_shadow.py --root <alter Stand>` lud die Test-Hilfsmodule
aus dem neuen Baum; deren `_testhaus` setzte den neuen Integrationscode vor
den alten. Beide Dumps entstanden so aus dem neuen Code, und „0
Abweichungen“ (7.7 B3/B4 und die ersten Messungen dieser Session) sagte
nichts. Das Werkzeug lädt jetzt die Hilfsmodule des gemessenen Baums
(`corpus_shadow.py` ebenso). Echte Vergleiche, 238 Dialoge / 477 Turns:

- **7.7.0 → 7.7.1: 9 Abweichungen**, alle gewollt: vier neue Szenarien
  `s771-*` (Korrektur, Irrealis, Ellipse, Koordination), zweimal die
  informierte Bestätigung („Das Skript Gute Nacht verriegelt dabei
  Haustürschloss“), der neue Beibehaltungstext, „Ich überlege, …“ statt
  „nicht verstanden“ – und **eine Fehllesung**: die Nachfrage „Es ist zu
  hell im Wohnzimmer, oder?“ wurde als Korrektur mit leerem Ersatz gelesen
  („In Ordnung, ich mache nichts.“ statt der Sicht). Sicher, aber falsch;
  in 7.8.0 behoben (ein Fragepartikel am Satzende vor „?“ ist kein Abbruch).
- **7.7.1 → 7.8.0: 12 Abweichungen**, alle gewollt: sechs neue Szenarien
  `s78-*`, drei Restmeldungen statt Fähigkeitsmeldung, Ablehnung für
  Nicht-Admins vor der Vorschau, „scharf schalten“ statt „scharf
  geschaltet?“, „Ein Gerät „Leselampe“ finde ich nicht.“

Der Live-Lauf fand zusätzlich eine Regression, die kein Shadow zeigte (die
Dumps waren wie beschrieben identisch): „auf drei Viertel“ bekam „Prozent“
angehängt. Behoben (Bruchteile sind Werte mit eigener Einheit), mit Test.

## Gemeinsame Oberfläche

Alle Vorverarbeitungen (A1, A4, B2, B3, B5, B6, B7) laufen in
`nlu/surface.prepare_surface` – eine Funktion für Konversation und
Arbiter-Shadow; keine parallele Struktur.

## Shadows und Pflichtläufe 7.8.0

| Lauf | Ergebnis |
| --- | --- |
| Stub-Suite | 6365 passed, 12 skipped (auch ohne Parallelisierung) |
| Property-Suite | 39 Invarianten, CI- und Nightly-Profil 0 Verletzungen |
| Korpus-Shadow gegen 7.7.1 | 0 geänderte Signaturen; Baseline `corpus-signatures-7.8.0.json` |
| Dialog-Shadow gegen 7.7.1 | 238 Dialoge / 477 Turns, 12 Abweichungen, alle gewollt (siehe unten) |
| Arbiter-Shadow | 2062 gleichwertig, 7 nicht messbar, 0 SAFETY_DRIFT |
| Shadow-Vergleich | 2039 EQUIVALENT |
| Sprachverständnis-Gate | 463 passed |
| Live-Testbett (echtes HA 2026.9.2) | 174/174: 167 ohne Proaktiv (davon 6 neue `s78-*`, 6 neue `s771-*`) und Proaktiv 7/7; ein erster Lauf fand „auf drei Viertel“ (behoben) |
| Push-Matrix | 35/35 |
| README-Beispiele | wie 7.7.0: drei bekannte Nicht-Sätze (Konfigurationszeilen, Listenzustand) |
| Latenzbudgets (V9, Automationssprache, V10, V11, V12, Learning Center) | eingehalten |
| Satzmuster | 173 (unverändert) |
| Pyright voll/Strict, Pyflakes | 0 |

## Bewusst offen (7.8.0)

- Partikelverben in einem Wort ohne Leerzeichen („losschicken“) und „auf
  halb“ im Kurzbefehl; Zählfrage „Wie viele Lampen sind im …?“.
- Begründungen mit Richtungspartikel („…, ich gehe jetzt hoch“) bleiben
  Rest bzw. werden anders gelesen – nichts Unsicheres, aber nicht verstanden.
- Präsenz „jemand kommt in den Flur“ (Akkusativ-Ort) als Auslöser.
- Die Konversation hält weiter mehrere Router; die Oberfläche ist
  gemeinsam, die Pläne entstehen weiter in den Engine-Compilern
  (Schuld aus 7.7, Abschnitt 9).
