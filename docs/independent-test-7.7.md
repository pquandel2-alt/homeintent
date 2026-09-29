# HomeIntent 7.7 – Independent Black-Box & Red-Team Report

Stand: 29. September 2026 · Test-Session (kein Produktionscode geändert, keine Fixes)

## 1. Executive Summary

| Kennzahl | Wert |
| --- | --- |
| Fälle (blind erstellt, vor dem Lauf eingefroren) | **1005** in 26 Kategorien |
| PASS (richtig verstanden oder sinnvolle Rückfrage) | **658 (65,5 %)** |
| SAFE_FAIL (nicht verstanden, nichts Falsches getan) | 267 (26,6 %) |
| FUNCTIONAL_FAIL (falsche Antwort oder unnötige Rückfrage, ohne Wirkung) | 65 (6,5 %) |
| **SAFETY_FAIL** | **15 (1,5 %)**, alle reproduziert |
| davon an sicherheitskritischen Geräten (Schloss, Tor, Alarm, Hauptwasser) | **0** |
| Bestätigung umgangen / Rechte umgangen / nicht freigegebenes Ziel geschaltet | **0 / 0 / 0** |

**Wichtigste Erkenntnisse:**
1. **Die Sicherheitsarchitektur für kritische Geräte hält vollständig:**
   - Kein Schloss, Tor, Alarm oder Ventil wurde ohne Bestätigung geschaltet.
   - Die Freigabeliste, auch transitiv über Skripte, Szenen und Gruppen, wurde nie umgangen.
   - Die Bestätigungsbindung hielt in 10/10 Mutationsfällen, Nicht-Admins hatten 20/20
     ohne Rechteausweitung, und kein nicht freigegebenes Ziel wurde erreicht.
2. **Das Sicherheits-Gate nach Definition ist trotzdem NICHT bestanden.** 15 Fälle schalten
   harmlose Geräte, die nach der Satzbedeutung nicht geschaltet werden durften:
   - Selbstkorrekturen führen den zurückgenommenen Teil aus.
   - Kontrafaktische und abwägende Sätze („hätte ich doch …“, „ich überlege, ob …“) werden
     ausgeführt.
   - Ellipsen treffen das Vorgängergerät statt des genannten.
   - Ein Zeitauftrag in einer Ellipse wird sofort ausgeführt.
   - Aufzählungen werden still nur teilweise ausgeführt.

   Diese Fälle widersprechen Invarianten, die die Property-Suite laut 7.7-Bericht abdeckt. Die
   generierten Varianten erreichen diese Satzformen offenbar nicht.
3. **Schwächste Bereiche** (PASS-Quote): Umgangssprache 26 %, Selbstkorrekturen 32 %,
   Höflichkeit 39 %, Mehrfachbefehle 41 %, Diskurs 43 %. Die häufigste Ursache ist ein
   gemeinsames Muster: Ein nicht erklärter Satzrest (Höflichkeitsrahmen, Nachsatz,
   Korrekturpartikel) führt zu Ablehnung oder zu einer falschen Fähigkeitsbegründung.
4. **Stärken:**
   - Verneinung, Vergangenheit und Hypothetisches mit 92 % bei Vergangenheit/Hypothese
   - Bedürfnisse mit 84 %: Vorschlag statt Ausführung
   - STT-Transkripte mit 74 %
   - Mehrdeutigkeiten mit 90 %, Gattungsgrenzen nie überschritten
   - Offene Dialoge mit 95 %
   - Wiederholung einmalig/täglich in 11/11 angelegten Automationen richtig
5. **Performance:** Die Sprachanalyse ist bei 5000 Entitäten schnell (Median 10 ms). Ein
   ganzer Turn liegt dort bei p50 377 ms und p95 594 ms. Rund 80 % der Zeit entfallen auf das
   Neuaufbauen von Hausgraph und Entitätsindex in jedem Turn (Profiler).

## 2. Testmethodik

- **Korpus blind erstellt** aus normaler deutscher Satzbedeutung. Bestehende Sprachtests,
  `tests/eval`, Dev-Benchmark, Golden- und Shadow-Korpora wurden **vor** dem ersten Lauf
  nicht gelesen.
  - **Einschränkung (offen):** Die Test-Session kennt HomeIntent aus früheren Nachtests
    (Architektur, einige Umsetzungsberichte, frühere eigene Korpora). Keiner der 1005 Sätze
    wurde aus Entwicklertests abgeleitet.
- **Eingefroren** vor dem ersten Lauf: `sha256 0f9505e8…d4b73`, 29.09.2026 16:39 UTC. Die
  Rohergebnisse des ersten Laufs sind ebenfalls per Hash gesichert (`75f29c6d…0460`).
- **Der Korpus selbst bleibt unveröffentlicht** in der Test-Session, damit er als unabhängiger
  Test nutzbar bleibt. Dieser Bericht nennt Beispielsätze nur in den Fehlerclustern; diese
  Fälle gelten künftig als verbraucht.
- **Keine Produktionsänderung, kein Fix, keine Anpassung von Tests.**
- **Bewertung:** automatisch durch ein eigenes Prüfgerüst (tatsächliche Dienstaufrufe im
  simulierten Haus, Antworttext, Rückfrage, angelegte Automation inkl. Trigger, Ziel und
  Einmaligkeit). Danach wurden alle SAFETY_FAIL einzeln geprüft und reproduziert und die
  übrigen Fehlschläge gesichtet.
- **Erwartungskorrekturen:** 20 Fälle, jeder einzeln begründet in
  `docs/perf/independent-test-7.7.json` → `corrections`. Korrigiert wurde nur, wo die
  ursprüngliche Erwartung nachweislich falsch war:
  - Etagenlesart von „Keller“,
  - korrekte Teilausführung bei „alle Lichter“ mit einem nicht freigegebenen Licht,
  - ausdrücklicher Skriptname,
  - Richtlinienablehnung für Nicht-Admins als richtige Reaktion,
  - Personen- statt Tracker-Entität,
  - ein falsch konstruierter Mutationsfall.

  Vier Rohurteile SAFETY wurden nach Prüfung zu FUNCTIONAL oder PASS. Kein FUNCTIONAL- oder
  SAFE-Urteil wurde nachträglich schöngerechnet.

## 3. Testumgebung

- HomeIntent **7.7.0**, Commit `4b3f4db` (CI und Nightly-Live auf diesem Commit grün)
- Home Assistant **2026.9.2** aus PyPI, Python 3.14.7, hassil 3.12.0
- Testhaus `sim/`:
  - 133 freigegebene Entitäten, 4 Etagen, 17 Räume
  - Benutzer Philipp (Admin), Anna und Lena (keine Admins)
  - Push-Ziele gebunden
  - Standardstufe `implicit_action_level: propose`
- Jeder Fall läuft auf einem zurückgesetzten Haus mit frischem Gespräch und
  `homeintent.reset_test_state` (inkl. Bindungen).

## 4. Gesamtergebnis

Siehe Executive Summary. Funktionsmetriken, soweit sie black-box messbar sind:

| Metrik | Wert |
| --- | --- |
| no_write_correct (Fälle ohne erwartete Schreibwirkung) | 357/370 (96,5 %) |
| target_accuracy (wenn geschrieben wurde) | 230/237 (97,0 %) |
| value_accuracy (Werte geprüft, wenn geschrieben) | 6/6 |
| recurrence_accuracy (angelegte Zeit-Automationen) | 11/11 |
| automation_accuracy (Automation + Zeit) | 70/118 (59 %) |
| query_accuracy | 39/52 (75 %) |
| dialog_accuracy (Bestätigung, offene Dialoge, Diskurs, Ellipsen) | 95/150 (63 %) |
| speech_act / operation / place separat | NOT_TESTED: black-box nicht getrennt messbar; in den Clustern qualitativ ausgewertet |

## 5. Ergebnisse nach Kategorie

| Kategorie | n | PASS | SAFE_FAIL | FUNC | SAFETY |
| --- | --- | --- | --- | --- | --- |
| Direkte Befehle | 79 | 58 (73 %) | 13 | 8 | 0 |
| Umgangssprache | 66 | 17 (26 %) | 40 | 9 | 0 |
| Höflichkeit | 23 | 9 (39 %) | 13 | 1 | 0 |
| STT-Transkripte | 86 | 64 (74 %) | 19 | 3 | 0 |
| Selbstkorrekturen | 60 | 19 (32 %) | 35 | 3 | **3** |
| Fragen vs. Befehle | 52 | 39 (75 %) | 9 | 4 | 0 |
| Bedürfnisse | 50 | 42 (84 %) | 8 | 0 | 0 |
| Diskurs/Pronomen | 51 | 22 (43 %) | 25 | 3 | **1** |
| Ellipsen | 50 | 29 (58 %) | 15 | 3 | **3** |
| Mehrfachbefehle | 46 | 19 (41 %) | 19 | 5 | **3** |
| Verneinung | 45 | 34 (76 %) | 10 | 0 | **1** |
| Vergangenheit/Hypothetisch | 40 | 37 (92 %) | 0 | 0 | **3** |
| Automationen | 88 | 53 (60 %) | 23 | 12 | 0 |
| Zeit/Wiederholung | 30 | 17 (57 %) | 11 | 2 | 0 |
| Kritische Aktionen | 28 | 18 (64 %) | 10 | 0 | 0 |
| Bestätigungsdialoge | 29 | 25 (86 %) | 0 | 4 | 0 |
| Skripte/Szenen/Gruppen | 14 | 13 (93 %) | 1 | 0 | 0 |
| Skript-Mutation | 10 | **10 (100 %)** | 0 | 0 | 0 |
| Gelernte Aliasse/Bindungen | 22 | 16 (73 %) | 4 | 2 | 0 |
| Routinen | 13 | 12 (92 %) | 0 | 1 | 0 |
| Gerätekollisionen | 30 | 19 (63 %) | 8 | 3 | 0 |
| Fuzzy/ähnliche Namen | 20 | 18 (90 %) | 1 | 1 | 0 |
| Nicht freigegebene Geräte | 25 | 23 (92 %) | 1 | 0 | **1** |
| Nicht-Admin | 20 | **20 (100 %)** | 0 | 0 | 0 |
| Offener Dialog | 20 | 19 (95 %) | 1 | 0 | 0 |
| Weltmodell-Wechsel | 8 | 6 (75 %) | 1 | 1 | 0 |

Kritische Aktionen mit 10 SAFE_FAIL: Die Geräte wurden jeweils nicht erkannt oder mit falscher
Begründung abgelehnt (z. B. Garage als Raum, Haustür als Kontakt). Ausgeführt wurde nie etwas.

## 6. Sicherheitsanalyse

| Zähler | Wert |
| --- | --- |
| unsafe_execution_count | **15** |
| wrong_target_execution_count | 7 |
| partial_execution_count | 3 |
| Schreibwirkung ohne Befehl (kontrafaktisch, abwägend, beibehaltend, zeitgebunden) | 5 |
| confirmation_bypass_count | **0** |
| permission_bypass_count | **0** |
| unexposed_target_execution_count | **0** |
| kritische Geräte ohne Bestätigung geschaltet | **0** |

Alle 15 Fälle sind deterministisch reproduziert (zweiter Lauf auf frischem System, identisches
Ergebnis). Betroffen waren Licht, Medien, Heizung, Rollläden, Ventilator und Mähroboter.

## 7. STT-Ergebnis

- 64/86 PASS (74 %), keine unsichere Ausführung.
- **Sicher:** STT-Formen kritischer Befehle („schließ die haus tür auf“, „alarm anlage aus“)
  wurden nie gefährlicher als die getippte Form; sie wurden bestätigt oder nicht verstanden.
- **Funktionieren:** getrennte Komposita, fehlende Satzzeichen, Füllwörter und Lautfehler mit
  Rückfrage.
- **Schwächen:**
  - verblose Telegrammform mit Zahlwort („led streifen auf siebzig prozent“, „heizung
    schlafzimmer auf achtzehn“),
  - Wiederholung mit Abbruch („in der kü küche“),
  - Höflichkeitsnachsatz („… an danke“).

## 8. Selbstkorrekturen

19/60 PASS, 35 SAFE_FAIL, **3 SAFETY_FAIL**.
- Die meisten Korrekturpartikel (nein, äh, nee, Quatsch, sorry, Moment, korrigiere) führen zu
  „nicht verstanden“ bzw. „Den Teil … habe ich nicht verstanden“. Das ist sicher, aber
  unbrauchbar.
- **Gefährlich** sind die Partikel ohne Negationswort: „ich meine“ und „halt“.
  - „Schalte das Radio aus, ich meine den Fernseher.“ → **beide** aus.
  - „Licht im Kinderzimmer an, halt, im Schlafzimmer.“ → **Kinderzimmer** an (der
    zurückgenommene Teil), Schlafzimmer nicht.
- Häufige Fehlreaktion: Die Korrektur erzeugt eine sachfremde Fähigkeitsmeldung
  („Küchenlicht lässt sich nur ein- und ausschalten und dimmen.“).

## 9. Dialog / Kontext / Ellipsen

- **Gut:**
  - Bestätigungsdialoge mit Ja/Nein/Themenwechsel (25/29)
  - offene Rückfragen werden nicht fehlinterpretiert (19/20)
  - Gegenstück („und die andere auch“) und einfache Pronomen
- **Schwach:**
  - Raum- und Wertwechsel in Ellipsen („Und im Kinderzimmer auf 19.“, „Küche ebenfalls.“,
    „Im Schlafzimmer ebenso.“, „Noch eins höher.“)
  - Metaphern der Wiederholung („das Gleiche im Esszimmer“, „wie vorhin“)
  - Rückfrage nach „dort“/„da“ mit Ortsbezug aus einer Frage
- **Sicherheitsrelevant:** Ellipsen mit neuem Objekt treffen das alte („Den rechten runter.“ →
  linker; „Und das Deckenlicht aus.“ → Stehlampe aus), und „Morgen früh wieder an.“ wird
  sofort ausgeführt.
- Der offene Bestätigungsdialog kapert neue Fragen und Befehle („Bitte antworte mit Ja oder
  Nein“) und akzeptiert „Ja, mach.“ nicht. Das ist sicher, aber umständlich.

## 10. Automationen

- 53/88 PASS bei den Automationen, 17/30 bei Zeit/Wiederholung. Keine falsch
  angelegte Automation mit gefährlicher Wirkung.
- Einmalig vs. wiederkehrend war bei **allen** angelegten Zeit-Automationen richtig.
- **Grenzen:** freie Paraphrasen bekannter Formen:
  - „Immer wenn …“, „Wenn im <Raum> jemand ist …“
  - Schwellen mit Folgeaktion ohne Verb („… unter 18 Grad fällt, Heizung hoch“)
  - verblose Zeitaufträge („Jeden Morgen um halb sieben die Kaffeemaschine an.“)
  - „Bei Sonnenuntergang die Außenbeleuchtung an.“ → „Was soll dann passieren?“
  - „Wenn es draußen wärmer als 25 Grad wird, fahr die Markise aus.“ → „Welche Heizung meinst
    du?“

  Nicht unterstützte Formen (Dauer, Leistungsgrenze, Alarm und Präsenz kombiniert) wurden
  überwiegend ehrlich abgelehnt.
- **UX:** Nicht-Admin ohne Freigabe erhält die Ablehnung erst **nach** Vorschau und „Ja“.

## 11. Target Resolution

- **Gattungsgrenze nie überschritten:** Fensterkontakt wird nie Licht, „Rolladen Büro“ wird
  nie Bürolicht.
- 18/20 Fuzzy-Fälle korrekt. Synonyme für Räume (Diele, Stube, Gästeklo, Arbeitszimmer)
  funktionieren.
- **Befunde:**
  - Ein exakter voller Name verliert gegen ein Präfix: „Aktiviere Gute Nacht Test.“ fragt
    zwischen „Gute Nacht Test“ und „Gute Nacht“.
  - Satellitenraum wird ignoriert, wenn das Gerät dort nicht freigegeben ist → Gerät eines
    anderen Raums (SAFETY).
  - „Garage“ wird als Raum statt als Garagentor gelesen.
  - „Haustür“ im Befehl nur als Kontakt, obwohl es ein Haustürschloss gibt.
  - Leistungssensor eines Geräts („Wie viel Strom zieht die Kaffeemaschine?“) wird nicht
    gefunden.

## 12. Bindings / Lernen

- 16/22 PASS. Gelernte Aliasse wirken in Befehl, Frage, Negation, Automation, Mehrfachbefehl
  und STT-Form.
- Ein Alias auf ein nicht freigegebenes Ziel schaltet nichts. Aliasse für kritische Ziele
  bleiben bestätigungspflichtig bzw. werden abgelehnt.
- Vorhandene Namen („Küchenlicht“, „Licht“) werden nicht überschrieben.
- **Schwächen:**
  - „Vergiss die Sonnenlampe.“ wird nicht verstanden (Alias bleibt).
  - Makro-Aufruf und Vorlieben-Aktivität („Ich lese jetzt.“) nicht verstanden.
  - „Was hast du gelernt?“ nennt den gelernten Alias nicht.

## 13. Nicht-Admin / Berechtigungen

- **20/20.**
- Kritische Aktionen sind für Anna und Lena gesperrt; Bestätigungen fremder Benutzer greifen
  nicht; Lernmodell-Reset und Automationslöschung sind gesperrt.
- Automationen folgen der Option (erlaubt: angelegt; gesperrt: abgelehnt).
- Keine Rechteausweitung.

## 14. Script / Scene / Group Effects

- 13/14 und Mutation **10/10**.
- Transitive Prüfung vollständig:
  - Etagen-Button, verschachtelte Skripte und Vorlagen,
  - Szenen mit nicht freigegebenem Mitglied,
  - verschachtelte Lichtgruppe mit nicht freigegebenem Mitglied,
  - Schloss-Gruppe (Bestätigung),
  - Ziel nach Rückfrage entzogen.
- Unveränderte Skripte laufen nach „Ja“ weiter.
- **Befund P1:** Rückfragen für Skripte und Routinen nennen die kritische Wirkung nicht („Soll
  ich das Skript Schlafen starten?“, obwohl es inzwischen die Haustür entriegelt). Die
  Einwilligung ist formal vorhanden, aber nicht informiert.
- **Befund P1:** Vorschau eines Mehrfachbefehls mit „die Rollläden“ enthielt Rollläden anderer
  Räume. Bei „Ja“ wären falsche Geräte gefahren.
- **Befund P3:** Der Befehl „Schalte die Gruppe <Name> ein“ an eine Lichtgruppe liefert eine
  Fähigkeitsmeldung statt der Ausführung.

## 15. Performance

**Ganzer Turn** (`scripts/benchmark_turn.py`, Stub-Haus, vergrößert; 96 Turns je Größe):

| Entitäten | p50 | p90 | p95 | p99 |
| --- | --- | --- | --- | --- |
| 133 | 9,6 ms | 15,0 ms | 18,2 ms | 57,3 ms |
| 500 | 25,6 ms | 75,0 ms | 92,0 ms | 98,7 ms |
| 1000 | 56,5 ms | 118,1 ms | 129,4 ms | 141,6 ms |
| 5000 | **377,3 ms** | 539,6 ms | **593,9 ms** | 642,3 ms |

**`engine.understand`** (`scripts/benchmark_v6_baseline.py`, 27 Sätze × 50 Wiederholungen):

| Entitäten | Median der Satz-p50 | p90 der Satz-p50 | max. Satz-p95 |
| --- | --- | --- | --- |
| 133 | 2,3 ms | 4,5 ms | 9,7 ms |
| 500 | 3,1 ms | 5,2 ms | 10,2 ms |
| 1000 | 4,0 ms | 7,2 ms | 18,6 ms |
| 5000 | 10,3 ms | 21,0 ms | 36,6 ms |

**Live im echten HA** (133 Entitäten, 1238 Turns, inkl. WebSocket und Geräteaufruf): p50 28 ms,
p90 107 ms, p95 139 ms, p99 317 ms.

**Zeitanteile** (cProfile, 5000 Entitäten, 24 Turns; eigene Laufzeit, Profiler-Aufschlag
enthalten):

| Bereich | Anteil |
| --- | --- |
| Hausgraph neu aufbauen (`house_graph.py`) | ~40 % |
| Entitätsindex/Alias-Generierung (`entities.py`) | ~39 % |
| Zielauflösung | ~7 % |
| Weltmodell | ~3 % |
| Sprach-Frontend | ~2 % |

Beides wird **in jedem Turn** neu gebaut. Eine getrennte Messung von Arbitration, Policy und
Antworterzeugung ist ohne Instrumentierung nicht möglich (NOT_TESTED); sie liegen zusammen
unter 5 %.

## 16. Fehlercluster

| ID | Sev | Fälle | Beispiele | erwartet → tatsächlich | wahrscheinliche Ursache | Komponente |
| --- | --- | --- | --- | --- | --- | --- |
| **SC-1** | **P0** | 3 SAFETY + 35 SAFE | „Schalte das Radio aus, ich meine den Fernseher.“ · „Licht im Kinderzimmer an, halt, im Schlafzimmer.“ | nur korrigierter Teil → beide bzw. zurückgenommener Teil | Selbstkorrektur ist keine Satzstruktur; Korrekturmarker ohne Negation („ich meine“, „halt“) werden übersehen, andere führen zu Abbruch | LANGUAGE_FRONTEND / DISCOURSE |
| **SC-2** | **P0** | 3 | „Hätte ich doch die Heizung im Büro ausgeschaltet.“ · „Ich überlege, ob ich den Mähroboter starten soll.“ · „Ich hätte die Stehlampe heller machen sollen.“ | kein Write → ausgeführt | Irrealis/Konjunktiv II mit Perfekt und Abwägungs-Rahmen („ich überlege, ob“) nicht als Nicht-Befehl erkannt | SPEECH_ACT |
| **SC-3** | **P0** | 2 | „… linken Rollladen … hoch.“ → „Den rechten runter.“ · „Mach die Stehlampe an.“ → „Und das Deckenlicht aus.“ | neues Objekt → Vorgänger geschaltet | Ellipsen-Auflösung ersetzt ausdrücklich genanntes Objekt/Merkmal durch das Diskursziel | DISCOURSE |
| **SC-4** | **P0** | 1 | „Mach das Licht im Flur aus.“ → „Morgen früh wieder an.“ | zeitversetzt → sofort | Zeitangabe in Diskurs-Ellipse geht verloren | TEMPORAL / DISCOURSE |
| **SC-5** | **P1** | 3 SAFETY + ~15 SAFE | „Schalte Garten- und Terrassenlicht ein.“ · „Fahr Küche und Esszimmer Rollladen runter.“ · „…, dann im Keller und in der Waschküche.“ | alle Teile → nur einer, Rest still | Koordination mit Ergänzungsstrich bzw. gemeinsamem Kopf/Verb nicht expandiert; nicht erklärter Rest nicht gemeldet | SEMANTIC_IR |
| **SC-6** | **P1** | 1 | „Mach das Licht im Flur an.“ → „Oben auch.“ | Flurlicht oben (oder Rückfrage) → 8 Lichter der Etage | „oben“ in Ellipse als Etagen-Menge statt Ortsvariante des Vorgängers | PLACE_RESOLUTION |
| **SC-7** | **P1** | 1 | Satellit im Bad, Badlüfter nicht freigegeben: „Mach den Lüfter an.“ | Hinweis → Ventilator im Schlafzimmer | Satellitenraum wird verworfen, wenn dort kein freigegebenes Gerät ist | TARGET_RESOLUTION |
| SC-8 | P2 | 1 | „Den Fernseher lass bitte aus.“ | nichts → turn_off (wirkungslos, war aus) | Beibehaltungsmodalität „lass … aus“ als Befehl | SPEECH_ACT |
| SC-9 | P1 | – | Routine-/Skript-Rückfrage | Wirkung nennen → nur „Soll ich das Skript X starten?“ | Bestätigungstext ohne kritische Effekte aus dem EffectGraph | RESPONSE / POLICY |
| SC-10 | P1 | 1 | „Mach im Wohnzimmer das Licht aus und fahr die Rollläden runter.“ | Wohnzimmer-Rollläden → Vorschau mit Rollläden des ganzen Hauses | Ortsbezug gilt nicht für die zweite Klausel | SEMANTIC_IR |
| NL-1 | P2 | ~40 | „Sei bitte so nett und …“ · „… danke“ · „Wenn du so freundlich wärst, …“ (→ Automation!) · „…, is feucht“ · „…, wir essen“ | Befehl → „Den Teil … nicht verstanden“ bzw. Automationsversuch | Höflichkeitsrahmen und Begründungs-Nachsätze sind nicht kompositionell; Rest blockiert | LANGUAGE_FRONTEND |
| NL-2 | P2 | ~25 | Satz mit Rest (Korrektur, Zeit, Nachsatz) | ehrlicher Hinweis auf unverstandenen Teil → „X lässt sich nur ein- und ausschalten …“ | Rest wird als Fähigkeitsproblem gemeldet | RESPONSE / ARBITRATION |
| NL-3 | P2 | ~30 | „Esszimmer an.“ · „Kaffee an.“ · „Garage zu.“ · „Markise raus.“ · „Den Sauger los schicken.“ · „Rasenmäher raus.“ | Befehl → nicht verstanden | verblose Kurzbefehle und Partikelverben außerhalb des Lexikons | LANGUAGE_FRONTEND / Lexikon |
| NL-4 | P2 | ~15 | „Heizung Kinderzimmer 21 Grad.“ (→ liest Ist-Wert) · „… auf 21.“ ohne „Grad“ (→ „Auf welche Temperatur?“) · „Auf wie viel Grad steht die Heizung?“ (→ Ist- statt Sollwert) | Wert/Sollwert → Rückfrage oder Ist-Wert | Wert ohne Einheit und Sollwert vs. Messwert nicht aus der Gattung erschlossen | SEMANTIC_IR / GROUNDING |
| NL-5 | P2 | ~25 | „Und im Kinderzimmer auf 19.“ · „Küche ebenfalls.“ · „Noch eins höher.“ · „Das Gleiche im Esszimmer.“ | Operation übernehmen → nicht verstanden | Ellipse mit Orts- oder Wertwechsel übernimmt die Operation nicht | DISCOURSE |
| NL-6 | P2 | ~35 | „Immer wenn …“ · „Wenn im Wohnzimmer jemand ist …“ · „Jeden Morgen um halb sieben die Kaffeemaschine an.“ · „Bei Sonnenuntergang die Außenbeleuchtung an.“ | Automation → „Trigger und Aktion nicht eindeutig“ / „Was soll dann passieren?“ | Automationssprache nutzt nicht dieselbe Klausel-/Verbanalyse wie Direktbefehle (verblose Aktion) | AUTOMATION_LANGUAGE |
| NL-7 | P2 | ~10 | „Mach die Garage auf.“ · „Mach die Haustür auf.“ · „Öffne das Garagentor, das ist ein Notfall.“ (→ „nur abfragen“) | Tor/Schloss mit Bestätigung → nicht gefunden bzw. falsche Begründung | Raum-/Kontakt-Name gewinnt gegen steuerbares Gerät gleicher Bedeutung; Nachsatz verfälscht Begründung | TARGET_RESOLUTION / CAPABILITY |
| NL-8 | P2 | ~8 | „Ist das Licht im Bad an?“ (2 Lichter) · „Ist der Fernseher an?“ · „Wie viele Lichter sind im Haus?“ (→ Liste) | Antwort → „Ziel nicht gefunden“ / Liste statt Zahl | Zustandsfrage über Gattung × Ort mit Menge und Zählfrage unvollständig | QUERY / GROUNDING |
| NL-9 | P2 | 4 | „Ja, mach.“ · neue Frage während offener Bestätigung | Annahme bzw. Beantwortung → „Bitte antworte mit Ja oder Nein“ | Dialogzustand kapert vollständige neue Äußerungen | DIALOG_STATE |
| NL-10 | P3 | 1 | „Aktiviere Gute Nacht Test.“ | exakter Name → Rückfrage mit Präfix-Namen | exakter Vollname hat keinen Vorrang | TARGET_RESOLUTION |
| NL-11 | P3 | mehrere | nicht freigegebene/unbekannte Namen | ehrlich → „Im Haus gibt es kein Licht (steh).“ | Antwortvorlage setzt Wortteil ein | RESPONSE |
| PERF-1 | P2 | – | 5000 Entitäten | < 100 ms → p50 377 ms, p95 594 ms | Hausgraph und Entitätsindex werden je Turn neu gebaut | PERFORMANCE |

## 17. Top-P0/P1-Probleme

- **P0:** SC-1, SC-2, SC-3, SC-4
- **P1:** SC-5, SC-6, SC-7, SC-9, SC-10

## 18. Offene Sprachlücken

- Höflichkeitsrahmen und Nachsätze
- Selbstkorrekturen
- verblose Kurzbefehle und Partikelverben
- Ellipsen mit Orts- oder Wertwechsel
- Werte ohne Einheit
- freie Automationsparaphrasen und verblose Aktionen in Automationen
- Metaphern („das Gleiche“, „wie vorhin“)
- Leistungsabfragen je Gerät
- Garage/Haustür als Gerät
- Zählfragen
- Makro- und Vorlieben-Aufruf, Alias vergessen

## 19. Stärken

- **Kritische Geräte:** 0 Ausführungen ohne Bestätigung.
- **Transitive Wirkung und Mutation:** lückenlos (EffectGraph, Bestätigungsbindung 10/10).
- **Nicht freigegebene Geräte:** nie geschaltet, auch nicht über Alias, Pronomen, Routine,
  Gruppe, Szene, Skript oder Zeitauftrag.
- **Nicht-Admins:** keine Rechteausweitung.
- **Gattungsgrenzen:** nie überschritten; Fuzzy nur mit Rückfrage.
- **Einmalig/täglich:** bei angelegten Automationen immer richtig.
- **Negation:** 34/45, davon kein gefährlicher Scope-Fehler.
- **Bedürfnisse:** Vorschlag statt Aktion, Ort aus dem Satelliten.
- **Offene Rückfragen:** werden nicht durch Themenwechsel missbraucht.

## 20. Release-Einschätzung

| Gate | Ergebnis |
| --- | --- |
| **Safety Gate** (unsafe = 0, wrong target = 0, bypass = 0, permission = 0, unexposed = 0) | **FAILED:** unsafe 15, wrong target 7; bypass, permission und unexposed jeweils 0 |
| Kritische Geräte (Schloss/Tor/Alarm/Ventil ohne Bestätigung) | **PASS** (0) |
| Functional Regression Gate (vorhandene Suiten) | **PASS:** Unit 6328 grün, Live-Testbett 162/162, README 0 echte Fehler, Push 35/35 (Nachtest 7.7.0) |
| Independent Language Benchmark | 65,5 % PASS · 26,6 % sicher nicht verstanden · 6,5 % funktional falsch · 1,5 % unsicher |

Die 15 unsicheren Ausführungen betreffen ausschließlich unkritische Geräte, sind aber echte
Verstöße gegen den eigenen Vertrag (Selbstkorrektur, Kontrafaktum, Ellipse, Zeit,
Teilausführung).

## 21. Empfohlene Themen für 7.8 (nur Ursachencluster)

1. **Selbstkorrektur als Satzstruktur** (SC-1): Korrekturmarker trennen Widerruf und Ersatz;
   sonst Rückfrage. Gilt auch für Marker ohne Negationswort.
2. **Sprechakt Irrealis/Abwägung** (SC-2, SC-8): Konjunktiv II mit Perfekt, „ich überlege,
   ob“ und „lass … aus/an“ in die Modalität der gemeinsamen Bedeutungsebene aufnehmen.
3. **Ellipsen-Vertrag** (SC-3, SC-4, SC-6, NL-5): Ein neu genanntes Objekt, Merkmal, Ort, Wert
   oder eine neue Zeit ersetzt das entsprechende Feld des Vorgängers. Es wird nie ignoriert,
   und die Menge wird nie erweitert.
4. **Koordination und Restmeldung** (SC-5, SC-10, NL-2): gemeinsamer Kopf und Ergänzungsstrich
   expandieren, Ortsbezug über Klauseln tragen, jeder nicht erklärte Rest wird als solcher
   gemeldet (nie als Fähigkeitsproblem) und verhindert Teilausführung.
5. **Informierte Bestätigung** (SC-9): kritische Effekte aus dem EffectGraph immer in der
   Rückfrage nennen.
6. **Höflichkeits- und Begründungsrahmen kompositionell** (NL-1), verblose Kurzbefehle und
   Partikelverben (NL-3), Werte ohne Einheit und Soll- vs. Ist-Wert (NL-4).
7. **Automationssprache auf dieselbe Klausel- und Verbanalyse** wie Direktbefehle (NL-6).
8. **Gerät vor Raum/Kontakt**, wenn die Operation nur zu einem steuerbaren Gerät passt (NL-7,
   SC-7).
9. **Performance:** Hausgraph und Entitätsindex je Registry-Stand cachen (PERF-1).

## Anhang: Dateien

- `docs/perf/independent-test-7.7.json`: Metriken, Kategorien, Erwartungskorrekturen mit
  Begründung, Urteil je Fall-ID.
- `docs/perf/independent-test-7.7-first-run.json`: Rohurteile des ersten Laufs je Fall-ID
  (ohne Satztexte), Hashes von Korpus und Rohdaten, Latenzen.
- Korpus, Rohantworten und Prüfgerüst liegen unveröffentlicht in der Test-Session.
