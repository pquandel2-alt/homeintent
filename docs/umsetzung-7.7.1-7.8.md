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
| Dialog-Shadow gegen 7.7.0 | 226 Dialoge / 441 Turns, 0 Abweichungen |
| Arbiter-Shadow | 2045 gleichwertig, 7 nicht messbar, 0 SAFETY_DRIFT |
| Shadow-Vergleich | 2022 EQUIVALENT, 0 SAFETY_DRIFT |
| Entwicklungs-Benchmark | `unsafe_execution_count` 0 (siehe unten) |
| Satzmuster | 173 (unverändert) |
| Pyright voll/Strict, Pyflakes | 0 |

Entwicklungs-Benchmark: Sieben Erwartungen wurden als gewollte Änderung
umgestellt und im Korpus kommentiert – fünf Selbstkorrekturen, die in 7.7
nur „nichts ausführen“ verlangten und jetzt den Ersatz ausführen, „Lass das
Flurlicht aus“ (Beibehaltung) und „Im Büro auch“ (Rolle statt Raummenge).

## Bewusst offen (7.7.1)

- Zwei Registry-Namen ohne Konjunktion („Schalte A aus B“) ohne Marker
  werden weiter von den bestehenden Compilern gelesen; mit Marker gilt A1.
- „Im Bad auch“ nach einem Flurlicht fragt bei zwei Bad-Lichtern ohne
  erkennbare Rolle nach.
