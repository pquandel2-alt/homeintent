# Auftrag: HomeIntent versteht Sprache wie ein LLM – ohne LLM (Release 7.3.0)

Du arbeitest im Repository `pquandel2-alt/homeintent` (HomeIntent, lokale deutsche
Sprachsteuerung für Home Assistant, Integration `custom_components/homeintent/`).
Basis: aktueller `main` (7.2.1). Lege einen neuen Arbeitsbranch von `main` an.

## Die Herausforderung

HomeIntent soll Fragen, Aussagen und Befehle im Haus so verstehen, wie ein Mensch oder
ein großes Sprachmodell sie meint – indirekte Wünsche, Umgangssprache, Gerätearten statt
Namen, Etagen, Schlussfolgerungsfragen, Kontext, Verneinung – und die passenden Aktionen
**sicher** ausführen. Push-Benachrichtigungen („Benachrichtige mich, wenn Fenster X
aufgeht“) müssen in allen natürlichen Formen zuverlässig bis aufs Handy funktionieren.

**Das Ziel ist Verstehen, nicht Auswendiglernen.** Es geht nicht darum, dass HomeIntent
1000 Sätze beherrscht, sondern dass es Sätze *versteht*, die es nie gesehen hat: aus
Wortbedeutungen, deutscher Grammatik und dem Wissen über das konkrete Haus. Jede neue
Fähigkeit muss sich deshalb als allgemeine Regel über Bedeutungsbausteine ausdrücken
lassen – nie als Satzmuster.

**HomeIntent hat dafür bereits ein eigenes Sprachmodell – das wird ausgebaut, nicht
ersetzt.** In `custom_components/homeintent/nlu/` steckt ein deterministisches,
kompositionelles Modell: `semantic_catalog.py`/`semantic_lexicon.py` (Lexikon mit
Bedeutungsbausteinen), `german_morphology.py`, `language_frontend.py`,
`german_structure.py` (Satzstruktur, Klauseln, Rollen), `semantic_graph.py`,
`meaning.py`/`semantic_interpreter.py` (Bedeutungskandidaten), `semantic_compiler.py`
(typisierte Frames), `entity_resolution.py`/`house_graph.py` (Weltmodell). Sein
Grundsatz steht in `semantic_lexicon.py`: *„Adding a synonym extends every permutation in
which that meaning can occur; it does not add another sentence template.“* Genau so soll
es weiterwachsen.

Zwei Ursachen verhindern heute LLM-ähnliches Verstehen:

1. **Das Wissen des Modells ist zu dünn.** Der Katalog kennt zu wenige Gerätearten,
   keine Bedürfnisse, Etagen und Komposita nur teilweise; die Zielauflösung fällt auf
   Friendly Names zurück.
2. **Viele Funktionen umgehen das Modell.** Benachrichtigungen
   (`notification_language.py`), Haushaltsfragen (`household_query.py`), Listen/Timer
   (`productivity.py`), Kalender (`calendar_management.py`), Verlauf
   (`history_query.py`), Automationsverwaltung (`automation_management.py`), Ziele
   (`goal_intent.py`) u. a. arbeiten mit eigenen regulären Ausdrücken (insgesamt rund
   **824 Regex-Verwendungen** in der Integration). Was das Modell lernt, kommt dort nicht
   an – deshalb versteht der Push-Pfad kein „im Keller“, kein „Garagentor“ und kein
   „Schreib Anna, dass …“, obwohl das Modell Orte und Empfänger grundsätzlich kennt.

Der Auftrag ist daher: **das vorhandene Modell mit Weltwissen anreichern und alle
Funktionen über dieses eine Modell führen.** Ein Satz wird genau einmal analysiert
(`LanguageDocument` → Struktur → Bedeutung), und jede Funktion liest ihre Bedeutung aus
diesem Ergebnis statt aus dem Rohtext.

**Harte Randbedingung: Es darf kein LLM und kein anderes ML-Modell hinzugefügt werden** –
weder lokal noch in der Cloud, weder optional noch als Fallback. Keine
Embeddings, keine neuronalen Netze, keine Modell-Downloads, kein `ai_task`, keine
Aufrufe anderer Conversation Agents. Das Verständnis muss vollständig aus HomeIntents
eigener deterministischer Sprachverarbeitung kommen (Lexikon, Morphologie,
Strukturanalyse, semantische Frames, Ontologie, Weltmodell aus der HA-Registry,
Diskurszustand). Gleicher Satz + gleicher Hauszustand = gleiches Ergebnis.

## Ausgangslage – gemessen im echten Home Assistant

Eine Test-Session hat HomeIntent 7.2.1 gegen ein **echtes Home Assistant 2026.9.2** mit
simuliertem Einfamilienhaus getestet (Testbett `sim/`, liegt auf `main`). Alle Details,
Transkripte und Rohdaten liegen auf dem Branch `claude/sleepy-meitner-xd7oux`:

```bash
git fetch origin claude/sleepy-meitner-xd7oux
git show origin/claude/sleepy-meitner-xd7oux:docs/testbericht-werbeversprechen-7.2.1.md
```

Übernimm von dort ins Arbeitsverzeichnis: `sim/readme_check.py`, `sim/push_check.py`,
`sim/nlu_probe.py`, `sim/results/readme_check_7.2.1.json`,
`sim/results/push_check_7.2.1.json`, `sim/results/nlu_probe_7.2.1.json`,
`docs/testbericht-werbeversprechen-7.2.1.md`, `docs/sprachverstaendnis-analyse-7.2.1.md`.

| Messung | 7.2.1 |
| --- | --- |
| Funktionsszenarien (`sim/runner.py`, frisches HA) | 126 / 126 |
| Beispielsätze aus README-Codeblöcken (`sim/readme_check.py`) | 101 ok, 17 Rückfragen, 23 fehlgeschlagen von 141 |
| Push-Matrix mit echter Auslösung (`sim/push_check.py`) | 23 / 35 |
| Alltagssprache-Korpus (`sim/nlu_probe.py`) | 11 / 66 (17 %), 3 falsche Geräteaktionen |
| Zum Vergleich HA-Standard-Agent, gleicher Korpus | 5 / 66 |

Die Funktionen selbst arbeiten. Was fehlt, ist **Verständnis jenseits exakter Gerätenamen
und fester Satzformen**.

## Ursachen (aus den Transkripten abgeleitet)

1. **Zielauflösung hängt am Friendly Name.** „Wohnzimmerlicht“, „Büro-Rolllade“, „den
   Rollladen“, „den Ventilator“, „die Heizung“ (ohne Raum), „Glotze“, „Rollos“, „Radio“,
   „Staubsauger“, „Raffstore“, „Garagentor“ im Automationspfad → „kein eindeutig
   passendes Gerät gefunden“, obwohl Art + Raum eindeutig sind oder eine Rückfrage
   richtig wäre.
2. **Zusammengesetzte Wörter werden falsch zerlegt.** „Terrassentür“ (Gerät im Wohnzimmer)
   wird als „Tür im Bereich Terrasse“ gesucht; ein exakter Gerätename muss Vorrang vor
   der Zerlegung haben.
3. **Etagen sind nicht überall Orte.** „im Obergeschoss“, „im Keller“ funktionieren bei
   Befehlen, aber nicht im Benachrichtigungs-/Automationspfad („Einen Bereich
   ‚Obergeschoss‘ kenne ich nicht“).
4. **Unscharfe Korrektur über Klassengrenzen.** „Wassermelder“ wird zu „Bewegungsmelder“
   korrigiert. Eine Korrektur darf nie in eine andere Geräteklasse springen.
5. **Diktierter Nachrichtentext stört die Auflösung.** „…wenn das Küchenfenster geöffnet
   wird, dass ich lüften soll“ → „kein passendes Gerät für Küchenfenster“.
6. **Keine Bedürfnis-Semantik.** „Mir ist kalt“, „zu dunkel“, „stickig“, „blendet“,
   „laut“, „ich gehe schlafen“, „ich will einen Film schauen“ → „nicht verstanden“.
7. **Keine Situationssichten.** „Muss ich lüften?“, „Ist das Haus abgeschlossen?“, „Ist
   unten noch was an?“, „Warum ist es im Büro so kalt?“, „Was ist los im Haus?“, „Ist
   jemand im Wohnzimmer?“, „Was kann ich im Wohnzimmer steuern?“, „Welche Räume gibt es
   oben?“, „Was macht das Skript Gute Nacht?“ → „Frage erkannt, Ziel nicht gefunden“.
8. **Schmaler Diskurs.** „Etwas heller bitte“, „Die andere“, „Mach's da wärmer“, „Dann
   mach die Heizung da aus“, „Mach alle aus“ (nach einer Liste) → nicht verstanden.
9. **Modalität/Verneinung unvollständig**, teils gefährlich (siehe Sicherheitsfehler).
10. **Sofortnachrichten mit Inhalt** („Schick mir aufs Handy: Essen ist fertig“,
    „Schreib Anna, dass …“, „Sag Anna Bescheid, dass …“) → nicht verstanden, während
    „Schick mir eine Nachricht: …“ funktioniert.

### Sicherheitsfehler (zuerst beheben)

- **S1** „Lass das Licht in der Küche an.“ schaltet zusätzlich die Kücheninsel **ein**.
  „lass X an/zu/offen“ = Beibehaltung, nie eine Aktion.
- **S2** „Mach im Wohnzimmer alles aus.“ schaltet **nur die Heizung** aus. „alles“ im Raum =
  alle schaltbaren, nicht sicherheitskritischen Geräte; ab Zielgrenze oder bei
  gemischten Domänen Vorschau mit Bestätigung; Heizung und Schlösser nur, wenn genannt
  oder in der Vorschau aufgeführt.
- **S3** Drei Befehle in einem Satz: der erste fällt **stillschweigend** weg. Nie Klauseln
  verwerfen – alle ausführen oder genau sagen, welcher Teil nicht verstanden wurde.
- **S4** „Mach die Musik in der Küche leiser.“ → Plan „Küchenradio **paused**“ (pausieren
  statt leiser; Englisch in der Antwort).
- **S5** „Wo ist es am kältesten?“ → „Garten.“ Ohne Außenbezug nur Innenräume vergleichen.
- **S6** „Hier ist es zu hell.“ liest Lichtwerte vor, statt zu dimmen oder nachzufragen.
- **S7** „Wassermelder“ → „Bewegungsmelder“ (Ursache 4).

### Restlücken aus dem Nachtest 7.2.1

R1 „etwas kühler“ fragt nach der Temperatur („wärmer“ geht) · R2 „Mach es im Kinderzimmer
etwas kühler“ ohne Gerät · R3 „Saugroboter bitte auf leise“ ohne „Saugstufe“ · R4
„Fernseher/TV/Glotze“ als Gattung · R5 „neutralweiß/tageslichtweiß“ · R6 „Wie viele Timer
laufen?“ · R7 unbekannter Name in Ausschlussliste: richtige Ablehnung, aber Meldung nennt
das falsche Gerät (muss „Ich finde kein Gerät ‚Blumenlampe‘. Ich habe nichts ausgeführt.“
lauten) · R8 README-Beispiel „Welche Lichter sind mindestens 50 Prozent hell?“ ·
R9 README-Beispiel „Wenn der Termin Urlaub beginnt, aktiviere die Szene Abwesend.“ ·
R10 Tippfehler „2 Eintrage wurden als erledigt markiert“ (Umlaut) – prüfe alle
Antworttexte auf fehlende Umlaute und Singular/Plural · R11 Testbett: `haus_sim.reset`
soll optional auch Listen, laufende Timer und Testautomationen zurücksetzen, damit
Messreihen im selben HA unabhängig bleiben

## Was zu bauen ist

### 0. Architektur: ein Modell für alle Funktionen

- Jede Fachfunktion (Benachrichtigung, Haushaltsfrage, Liste, Timer, Kalender, Verlauf,
  Automationsverwaltung, Ziel/Routine, Gedächtnis) konsumiert das eine
  `LanguageDocument` bzw. die daraus kompilierte Bedeutung (Sprechakt, Aktion, Ziel,
  Ort, Menge, Zeit, Empfänger, Inhalt, Modalität). Reguläre Ausdrücke auf dem Rohtext
  werden schrittweise durch Abfragen auf diese Bedeutung ersetzt. Miss die Zahl der
  Regex-Verwendungen vorher/nachher und nenne sie im PR; sie soll deutlich sinken.
- Neues Wissen kommt als **Daten** in den Katalog bzw. die Ontologie (Wortbedeutung,
  Wortart, Flexion, Synonyme, Gattung, Bedürfnis → Wirkung), nicht als Code-Sonderfall.
  Ein neues Wort muss automatisch in Befehlen, Fragen, Automationen und
  Benachrichtigungen wirken.
- Wo das Modell eine Bedeutung nur teilweise versteht, sagt es das präzise („Ich
  verstehe ‚Wassermelder‘, aber keinen Zeitpunkt“), statt „nicht verstanden“.

Arbeite am bestehenden Weg eines Satzes (`nlu/language_frontend.py` →
`nlu/german_structure.py`/`semantic_graph.py`/`german_morphology.py` →
`nlu/semantic_interpreter.py`/`semantic_compiler.py` → `nlu/understanding.py` →
`nlu/entity_resolution.py`/`entities.py`/`house_graph.py` → Validator,
`execution_policy.py`, `service_executor.py`; Abfragen über `nlu/query_executor.py`,
`household_query.py`; Benachrichtigungen über `notification_language.py`,
`notification_request.py`, `automation_notification.py`). **Keine parallelen
Sonderparser für einzelne Sätze** – jede Fähigkeit wird als allgemeine, kompositionelle
Regel im gemeinsamen Pfad umgesetzt, damit Befehle, Fragen, Automationen und
Benachrichtigungen sie gleichermaßen nutzen.

### 1. Kompositionelle Zielauflösung (Kern)

Ein Ziel ist eine Kombination aus **Gattung × Ort × Merkmal × Menge**, nicht ein Name.

- **Gattungs-Ontologie** (neu, z. B. `nlu/device_ontology.py`): deutsche Wörter inkl.
  Umgangssprache, Plural, Diminutiv und Komposita → Domäne + `device_class` + optionale
  Fähigkeit. Mindestens: Licht/Lampe/Leuchte/Beleuchtung; Rollladen/Rolllade/Rollo/
  Jalousie/Raffstore/Markise/Tor/Garagentor/Garage; Fenster; Tür/Haustür/Terrassentür
  (Kontakt) vs. Schloss; Heizung/Thermostat/Heizkörper; Ventilator/Lüfter;
  Fernseher/TV/Glotze; Radio/Musik/Lautsprecher/Box; Staubsauger/Sauger/Saugroboter;
  Mäher; Steckdose/Schalter; Melder (Rauch/Wasser/Bewegung/Präsenz); Temperatur/
  Feuchte/CO2/Strom/Energie/Batterie. Die Ontologie ist Daten, testbar und erweiterbar.
- **Kompositum-Zerlegung** mit Vorrang: exakter Name/Alias → Name + Raum → Gattung +
  Raum („Wohnzimmer|licht“, „Büro-|Rolllade“, „Bad|fenster“, „Terrassen|tür“ nur, wenn es
  kein Gerät namens „Terrassentür“ gibt).
- **Orte**: Bereich, Bereichsalias, Etage, Etagenalias („oben“, „unten“, „im Keller“,
  „draußen“, „im ganzen Haus“) in **allen** Pfaden, auch Automation und Benachrichtigung.
- **Menge**: Singular → genau eins, sonst Rückfrage mit Nummern; Plural/„alle“/„die X“ →
  alle passenden (Zielgrenze → Vorschau); „irgendein“ → Auslöser „any“.
- **Ergebnis**: 0 Treffer → ehrliche Meldung, die Gattung und Ort nennt („Im Büro gibt es
  keinen Ventilator.“), 1 → handeln, mehrere → Rückfrage. Die Meldung „kein eindeutig
  passendes, für HomeIntent freigegebenes Gerät“ nur noch, wenn das stimmt.
- **Unscharfe Korrektur** nur innerhalb derselben Gattung; nie Melder ↔ Melder anderer
  Art, nie Licht ↔ Schloss usw.

### 2. Bedürfnis-Semantik

Typisierte Tabelle Aussage → gewünschte Wirkung → Fähigkeit, Ort aus Satz, Satellit
oder Kontext:
kalt/friere/frisch → wärmer · warm/heiß/schwitze → kühler · dunkel/sehe nichts → Licht
an/heller · hell/grell/blendet → dimmen/beschatten · stickig/muffig/feucht → lüften
(Lüfter an; Fenster nur als Hinweis) · laut → leiser · leise/hör nichts → lauter · „ich
gehe schlafen / ins Bett“, „ich gehe jetzt / verlasse das Haus“, „bin zurück“,
„ich will fernsehen / einen Film schauen / lesen“ → Routine/Szene über Name, Alias und
Szenenbeschreibung. Eindeutig und risikoarm → ausführen mit kurzer Begründung
(„Ich habe die Heizung im Wohnzimmer um ein Grad erhöht.“); sonst Vorschlag mit Rückfrage.

### 3. Situationssichten (lesende Abfragetypen)

„noch an / vergessen auszuschalten“, „alles zu / abgeschlossen / sicher“, „muss ich
lüften“ (dokumentierte Schwellen Feuchte/CO2), „warum ist es kalt/warm in X“ (nur belegte
Fakten: Sollwert, Ist-Wert, Heizbetrieb, offenes Fenster, Außentemperatur), „was ist los“
(Kurzüberblick), Präsenz je Raum, „was kann ich in X steuern“, Räume je Etage, Anzahl je
Gattung, „was macht Skript/Szene X“ (aus der HA-Konfiguration), „wofür ist X“, Vergleiche
mit Schwellen („mindestens 50 Prozent hell“).

### 4. Diskurs

Ellipsen und Pronomen binden an letztes Ziel, letzte Ergebnismenge oder letzten Ort:
„etwas heller“, „noch mehr“, „die andere/der andere“, „alle“, „die“, „da/dort“,
„dann …“, „auch“, „und im Bad?“. Gilt innerhalb der Kontext-TTL, pro Gespräch.

### 5. Modalität und Verneinung

„lass X an/zu“ = nichts tun · „X muss/braucht nicht an sein“, „X kann aus“, „ich brauche X
nicht mehr“ = ausschalten · „nicht X, Y meine ich“ = Korrektur · höflich-vage
(„könntest du vielleicht irgendwann mal …“) = normaler Befehl · „ich frage mich, ob …“ =
Frage · „benachrichtige mich nicht, wenn …“ = keine Automation (bleibt so).

### 6. Benachrichtigungen – vollständig

Eine gemeinsame Bedeutung für Sofort-, Verzögert-, Termin- und Ereignis-Push:

- **Empfänger**: „mich/mir“ (sprechender Benutzer über Bindung), Personen per Name,
  „alle/uns“ nur nach Rückfrage. Nie Broadcast ohne Rückfrage.
- **Sofort mit Inhalt**: „Schick mir aufs Handy: …“, „Schreib Anna, dass …“, „Sag Anna
  Bescheid, dass …“, „Nachricht an Anna: …“, „Push an mich: …“.
- **Ereignisse** auf jeder freigegebenen Entität mit semantischem Zustand: Fenster/Tür
  auf/zu, Bewegung, Präsenz, Rauch/Wasser/CO, Garagentor/Rollladen auf/zu/Position,
  Schloss auf/zu, Temperatur/Feuchte/CO2 über/unter, Gerät fertig (Text-Status,
  Leistungssensor, Binärsensor), Person kommt/geht, „irgendein Fenster im
  Obergeschoss“, „länger als N Minuten offen“.
- **Wortstellung**: „Benachrichtige mich, wenn …“, „Wenn …, sag mir Bescheid“, „Push an
  mich, wenn …“, „Ich will eine Nachricht aufs Handy, sobald …“ – mit und ohne Komma,
  Verbzweit/Verbletzt, „auf geht/aufgeht/geöffnet wird/aufgemacht wird/auf ist“.
- **Diktierter Text** („…, dass ich lüften soll“) wird vor der Zielauflösung als Nachricht
  abgetrennt und nie als Gerät oder Befehl interpretiert; ohne Text formuliert HomeIntent
  aus dem Auslöser.
- Vorschau nennt Auslöser, Empfängergerät und Text; nach „Ja“ genau eine Automation;
  beim Ereignis genau eine Nachricht an genau das richtige Handy.

### 7. Zeitsprache

„halb sieben“, „viertel nach/vor“, „sobald es dunkel wird“ (Sonne oder
Helligkeitssensor), „wenn ich nach Hause komme“, „weck mich um … mit Licht“, „in zwei
Stunden und 30 Minuten die Rollläden“ (Plural = alle, mit Vorschau).

## Regeln

- **Kein LLM, kein ML-Modell, keine externen Dienste** (siehe oben). Keine neuen
  Laufzeitabhängigkeiten außer reinem Python-Code und Daten.
- Sicherheitsmodell unverändert: Parser führen nichts aus; Validator, ExecutionPolicy,
  Bestätigungen, Benutzerbindung, NEVER_AUTO, Nur-Lesen/Nur-Admin gelten für jede neue
  Bedeutung. Mehrdeutigkeit → Rückfrage, nie raten. Eine neue Regel darf nie ein anderes
  Gerät schalten als das gemeinte – im Zweifel fragen.
- **Verstehen statt Auswendiglernen.** Verboten sind Satzschablonen, Regex auf ganze
  Formulierungen, Nachschlagetabellen „Satz → Aktion“ und jede Regel, die nur für einen
  konkreten Testsatz existiert. Erlaubt und erwünscht sind allgemeine Regeln über
  Bedeutungsbausteine (Wortbedeutung, Flexion, Satzstruktur, Rollen, Ontologie,
  Weltmodell).
- **Beweis durch Kombinatorik statt Satzmenge.** Für jede Fähigkeit gibt es einen
  generierten Produktivitätstest, der Bausteine frei kombiniert – z. B. Gattung × Ort
  (Raum/Etage/Satellit) × Aktion × Satzhülle (Befehl, Bitte, Wunsch, Frage) ×
  Wortstellung × Verneinung – und gegen das Testhaus prüft. Der Test belegt, dass jede
  neue Einheit (ein Wort, eine Gattung, ein Ort) in *allen* Kombinationen wirkt. Eine
  kleine handgeschriebene Regressionsliste für Sonderfälle (Sicherheit, Mehrdeutigkeit)
  ist zusätzlich erlaubt; ihre Größe ist kein Ziel.
- `sim/nlu_probe.py`, `sim/push_check.py` und `sim/readme_check.py` sind
  **Abnahme-Holdouts**: nicht daraus ableiten, nicht darauf optimieren. Die
  Test-Session prüft am Ende mit einem **weiteren, unveröffentlichten Korpus**
  ungesehener Sätze – gemessen wird also, ob HomeIntent Neues versteht.
- Keine bestehenden Tests löschen, überspringen oder abschwächen; bestehende
  Szenarioerwartungen nicht aufweichen.
- Latenz: p95 < 100 ms bei 5000 Entitäten (bestehende Benchmarks), Stub-Suite mit hassil
  3.11 und 3.12 grün.
- Keine Modellbezeichnungen in Commits, Code oder PR-Text.

## Testumgebung

```bash
uv venv --python 3.14 ../havenv && uv pip install --python ../havenv/bin/python -r requirements-ha-test.txt
cd sim && HASS=../../havenv/bin/hass ./fresh_ha.sh      # frisches HA + Bootstrap
../../havenv/bin/python runner.py                        # 126 Funktionsszenarien
../../havenv/bin/python readme_check.py                  # alle README-Beispiele
../../havenv/bin/python push_check.py                    # Push-Matrix mit echter Auslösung
../../havenv/bin/python nlu_probe.py                     # Alltagssprache
../../havenv/bin/python say.py "Mir ist kalt." --device <Bereich>   # Einzelprobe
../../havenv/bin/python check_log.py                     # HA-Log ohne HomeIntent-Befunde
```

Erweitere das Testhaus (`sim/custom_components/haus_sim/house.py`), sodass jedes Gerät
aus den README-Beispielen einen realistischen Gegenpart hat (z. B. zweites Bürolicht,
Einfahrtkamera), **ohne** Gerätenamen an Testsätze anzupassen.

## Abnahme

1. CI komplett grün (Stub-Suite mit hassil 3.11 und 3.12, `tests_ha`, Pyright-Scopes,
   Pyflakes, Sprach-Gate, Benchmarks, Live-Testbett).
2. Frisches Testhaus:
   - `runner.py`: alle Szenarien grün, plus neue Szenarien (Kategorie „Sprache 7.3“) für
     jede Fähigkeit aus Abschnitt 1–7 und für S1–S7, R1–R9, jeweils mit eigenen Sätzen.
   - `readme_check.py`: 0 „fehlgeschlagen“; Rückfragen nur, wo das Haus tatsächlich
     mehrdeutig ist.
   - `push_check.py`: 35 / 35; keine Nachricht an ein falsches Handy, keine Nachricht beim
     Anlegen.
   - `nlu_probe.py`: ≥ 75 % „ok“ und **0 falsche Geräteaktionen**.
   - Produktivitätstests aus den Regeln oben grün; Regex-Verwendungen deutlich reduziert.
   - `check_log.py`: keine HomeIntent-Befunde.
3. README ehrlich aktualisieren: Abschnitt „Wie HomeIntent Sprache versteht“ (Gattungen,
   Bedürfnisse, Sichten, Diskurs), neue Beispiele nur, wenn sie im Testhaus nachweislich
   funktionieren; `docs/architecture-v13.md` mit Ontologie, Zielauflösung und
   Sicherheitsargumentation; Messwerte vorher/nachher im README.
4. Version 7.3.0, Shadow-Baseline in `docs/perf/`, PR gegen `main` mit Messtabelle und
   einer Zeile pro Ursache/Befund (Umsetzung, Tests).

## Reihenfolge

S1–S7 (je ein Commit mit Regressionstest) → Abschnitt 0 (Funktionen auf das eine Modell
umstellen, beginnend mit Benachrichtigungen) → Abschnitt 1 Zielauflösung → Abschnitt 6
Benachrichtigungen → R1–R9 → Abschnitt 5 → Abschnitt 4 → Abschnitt 3 → Abschnitt 2 →
Abschnitt 7 → Messung aller Holdouts → Dokumentation → PR. Nach jedem Abschnitt Stub-Suite
und Testhaus laufen lassen; Rückschritte sofort beheben.
