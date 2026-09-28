# Auftrag: HomeIntent nach 7.3.0 – was jetzt verbessert werden muss

> **Ersetzt durch [`PROMPT_GESAMT.md`](PROMPT_GESAMT.md).** Dieser Auftrag ist dort vollständig enthalten und mit weiteren Architekturanforderungen zusammengeführt. Bitte nur noch `PROMPT_GESAMT.md` umsetzen; diese Datei bleibt als Verlauf erhalten.

Du arbeitest im Repository `pquandel2-alt/homeintent`. Basis ist **7.3.0** (Branch
`claude/sprachverstaendnis-prompt-dkzpr9`, Commit `df3ae85`). Lege von dort einen
eigenen Arbeitsbranch an.

Dieser Auftrag fasst alles zusammen, was der Live-Nachtest von 7.3.0 im echten Home
Assistant (2026.9.2, simuliertes Einfamilienhaus in `sim/`) und ein realer Vorfall beim
Projektinhaber ergeben haben. Messbericht: `docs/nachtest-sprachverstaendnis-7.3.0.md`
im Branch `claude/sleepy-meitner-xd7oux`.

**Kein großes Release.** Liefere in **getrennten, einzeln testbaren Schritten**, jeweils
mit eigener Versionsnummer und eigenem Commit. Die Reihenfolge ist verbindlich (G folgt direkt auf C):

| Schritt | Inhalt | Version |
| --- | --- | --- |
| A | Sicherheit: Skripte, Szenen und Gruppen gegen die Freigabeliste prüfen | 7.3.1 |
| B | Nie raten: Ort, Einmaligkeit und Transparenz | 7.3.2 |
| C | Eine einzige Geräteauflösung (Shadow-Vergleich, dann Umschalten) | 7.4.0 |
| G | Selbstlernen aus dem Dialog (Wörter, Rückfragen, Korrekturen, Gedächtnis) | 7.4.1 |
| D | Sprachinseln Bereich für Bereich auf die gemeinsame Bedeutung umstellen | 7.4.x |
| E | Generalisierung: Bedeutung statt Sätze | 7.5.0 |
| F | Ehrliche Messung und sauberes Testbett | laufend |

Nach jedem Schritt: alle Unit-Tests (hassil 3.11 und 3.12), `ruff` und hassfest grün,
Commit, Push. Die Test-Session prüft jeden Schritt im Live-Testhaus, bevor der nächste
beginnt.

## Verbindliche Grundsätze (aus dem Architektur-Briefing)

- **Kein LLM und kein ML-Modell**, auch nicht optional: keine Embeddings, kein
  `ai_task`, keine Aufrufe anderer Conversation Agents.
- **Deterministisch; nie raten.** Gleicher Satz + gleicher Hauszustand = gleiches
  Ergebnis. Fehlt eine Information, wird gefragt und nicht geschätzt.
- **Parser rufen nie Dienste auf.** Sie liefern Bedeutung, sonst nichts.
- **Eine zentrale Geräteauflösung.** Keine zweite, keine private Suche in Features.
- **Validator, ExecutionPolicy und Executor** sind die einzigen Autoritäten über das,
  was geschaltet wird. Nichts darf sie umgehen.
- Vorhersage ≠ Ausführung, Konfidenz ≠ Einwilligung, Gewohnheit ≠ Automation,
  proaktiv ≠ autonom.
- **Verstehen statt Auswendiglernen.** Jede Verbesserung ist eine allgemeine Regel über
  Bedeutungsbausteine (Lexikon, Ontologie, Struktur, Diskurs), nie ein Satzmuster und
  nie ein neuer Regex für einen einzelnen Satz.

---

## Schritt A – Sicherheit (7.3.1, zuerst und allein)

Vollständige Spezifikation: **`sim/FIX_PROMPT_SKRIPT_FREIGABE.md`** (Branch
`claude/sleepy-meitner-xd7oux`). Kurzfassung:

HomeIntent prüft bei Skripten, Szenen und Gruppen nur die eine Entität selbst. Im
Testhaus startete „Aktiviere Nachtruhe.“ den **nicht freigegebenen** Saugroboter und die
**nicht freigegebene** Kaffeemaschine, während direkte Befehle an beide korrekt
abgelehnt wurden. Beim Projektinhaber drückte ein Skript mit
`button.press` auf `floor_id: erdgeschoss` alle Buttons der Etage: Der Saugroboter
startete, und die Brandmelder lösten ihren Selbsttest aus.

Soll: Vor jedem Start werden die **wirksamen Ziele** bestimmt (HA-Helfer
`entities_in_script`/`devices_in_script`/`areas_in_script`/`floors_in_script`/
`labels_in_script`, `entities_in_scene`, Gruppenmitglieder, Registry-UUIDs von
Geräte-Aktionen, verschachtelte Skripte, alle `if`/`choose`-Zweige). Darauf gelten
Freigabe, Nur-Lesen, Nur-Admin, Risiko und Zielgrenze exakt wie bei direkten Befehlen.
Ein nicht freigegebenes Ziel führt zu DENY mit konkreter Liste, und eine Bestätigung
kann das nicht überstimmen. Die Prüfung sitzt nur in Policy und Executor und wird
direkt vor dem Schreiben wiederholt.

---

## Schritt B – Nie raten (7.3.2)

### B1 Deiktisches „hier“ ohne bekannten Ort

Im Nachtest wurde auf eine Kälte-Aussage mit „hier“ **ohne** `device_id` und ohne
Raumangabe die Heizung eines bestimmten Raums verstellt. Ermittle, woher der Raum kam.

Erlaubte Quellen für „hier“ und „da“, in dieser Reihenfolge:
1. der Bereich des Sprachsatelliten (`device_id` → Bereich),
2. ein ausdrücklich genannter Raum im laufenden Gespräch (Diskurs),
3. eine eindeutige, **aktuelle** Anwesenheit der sprechenden Person, sofern diese
   Funktion aktiviert ist.

Sonst wird gefragt: „In welchem Raum?“. Das Ergebnis immer in der Antwort nennen („im
Schlafzimmer“).

### B2 Einmalig oder wiederkehrend

Ein Befehl der Form „Mach um <Uhrzeit> <Gerät> an.“ legte eine **tägliche
Automation** an. Im Deutschen ist das ein einmaliger Auftrag.

Regel über Bedeutung, nicht über Wörter:
- Uhrzeit oder Ereignis **ohne** Wiederholungsmarker („jeden“, „immer“, „täglich“,
  „werktags“, „wenn … immer“) → einmaliger geplanter Befehl.
- Mit Wiederholungsmarker → Automation mit Vorschau.
- Bei Ereignissen, die typischerweise wiederkehren (Sonnenauf-/-untergang, Dunkelheit),
  ohne Marker nachfragen: „Nur heute oder jeden Tag?“

### B3 Falsche Begründungen

„Dann dreh da die Heizung hoch.“ antwortete „Heizung Kinderzimmer unterstützen diese
Aktion nicht“, obwohl die Heizung das kann. Fehlermeldungen müssen die **tatsächliche**
Ursache aus Validator und Policy wiedergeben: Ziel nicht gefunden, nicht freigegeben,
Fähigkeit fehlt oder Wert fehlt. Grammatik korrigieren (Singular/Plural).

### B4 Transparenz bei Sammelaktionen

Nach Skripten, Szenen und Gruppen sagt HomeIntent kurz, **was** geschaltet wurde:
„Gute Nacht ausgeführt: 9 Rollläden.“ Die Grundlage sind die wirksamen Ziele aus
Schritt A, begrenzt auf eine Zeile. So fällt eine unerwartete Wirkung sofort auf.

### B5 Negation und Modalität ohne Aktion korrekt beenden

„Lass X so, wie es ist“ bedeutet: nichts tun und das bestätigen. Solche Vergleichs-
und Rest-Phrasen werden heute als unverstanden gemeldet.

---

## Schritt C – Eine einzige Geräteauflösung (7.4.0)

Heute gibt es zwei: `nlu/target_resolution.py` (neu, Gattung × Ort × Merkmal × Menge,
8 Nutzer) und `nlu/entity_resolution.py` (alt, 24 Nutzer), ohne Verbindung
untereinander. Dazu kommen private Suchen in Features (`entity_scope.py`,
`automation_target_resolver.py`, `query_target.py`, Push-Ziele).

1. **Shadow-Modus:** Jeder Aufruf der alten Auflösung ruft zusätzlich die neue auf
   (ohne Wirkung) und protokolliert Abweichungen strukturiert: Satz-Hash, beide
   Ergebnisse, Grund. Die Protokolle landen in den Diagnosedaten und in einem
   Test-Report über alle vorhandenen Korpora.
2. **Abweichungen klassifizieren:** Neu ist besser, alt ist besser oder beide falsch.
   Für jede Klasse „alt besser“ die neue Auflösung verbessern, bis diese Klasse leer
   ist.
3. **Umschalten** pro Aufrufer, einzeln und mit Test. Erst wenn alle Aufrufer
   umgestellt sind, die alte Auflösung entfernen.
4. **Harte Regeln der einen Auflösung:**
   - Korrektur nie über Klassengrenzen (Wassermelder ≠ Bewegungsmelder).
   - Mehrdeutig → Rückfrage mit nummerierten Optionen.
   - Plural oder „alle“ → Menge mit Vorschau ab der Zielgrenze.
   - Nur freigegebene Entitäten werden berücksichtigt.
   - Etagen, Komposita („Wohnzimmerlicht“ = Gattung Licht × Ort Wohnzimmer) und
     Gerätegattungen statt Namen überall gleich behandeln.

---

## Schritt D – Sprachinseln umstellen (7.4.x, je Bereich ein Release)

Push ist erledigt (24 → 1 Regex). Offen sind noch eigene Muster in: Verlauf
(`history_query.py`), Listen/Timer (`productivity.py`), Kalender (`calendar_*.py`),
Automationsverwaltung (`automation_management.py`), Haushaltsfragen
(`household_query.py`) und Ziele (`goal_intent.py`). Gesamt 770 Regex-Verwendungen.

Pro Bereich:
1. Kanonische Bedeutung als typisierten Frame definieren (z. B. `HistoryQuery(target,
   event, time_range, aggregation)`).
2. Den Frame aus der gemeinsamen Analyse ableiten (`LanguageDocument` → Struktur →
   Bedeutung), Ziele über die eine Auflösung aus Schritt C.
3. **Shadow-Vergleich** alter Parser ↔ neuer Frame über alle Tests und Korpora;
   Abweichungen klassifizieren wie in C.
4. Umschalten, alten Parser löschen und die Regex-Zahl im Changelog nennen.

Reihenfolge nach Nutzen: Verlauf → Automationsverwaltung → Listen/Timer → Kalender →
Haushaltsfragen → Ziele.

---

## Schritt E – Generalisierung: Bedeutung statt Sätze (7.5.0)

Auf den veröffentlichten Korpora erreicht 7.3.0 **85 %**. Auf 45 **nie gezeigten**
Sätzen der Test-Session sind es nur **38 %**. Die Verbesserungen haben also vor allem
bekannte Sätze gelernt. Die folgenden Lücken sind als **Bedeutungsklassen**
beschrieben; die geheimen Testsätze bekommst du bewusst nicht.

### E1 Verbklassen statt Verblisten

Umgangssprachliche Aktionsverben und Richtungspartikeln gehören auf Bedeutungsklassen:
- Einschalten/Starten: an, anwerfen, anschmeißen, anknipsen, los, starten.
- Erhöhen/Senken: rauf, hoch, runter, höher, wärmer, kälter, lauter.

Dazu kommen Mengen („zwei Grad“, „ein bisschen“, „deutlich“) als eigene Bausteine.
Verblose Kurzbefehle der Form „<Gattung> [<Ort>] [<Menge>] <Partikel>“ werden über Gattung +
Partikel + Menge verstanden, unabhängig von der Wortstellung.

### E2 Bedürfnis- und Beschwerde-Ontologie erweitern

Heute: kalt/warm. Neu als typisierte Abbildung **Zustand → Wirkung → Gerätegattung**:
- zu dunkel, „man sieht nichts“, Lesen/Arbeiten schwierig → Licht an/heller
- blendet, grelle Sonne → beschatten
- beschlagen, feucht, stickig → lüften/Lüfter
- Gerät stört oder ist zu laut → leiser/aus (mit Rückfrage)
- Gerät wird nicht mehr gebraucht → aus

Ort aus Satz, Satellit oder Diskurs (B1). Nur bei eindeutiger, risikoarmer Wirkung
ausführen, sonst als Vorschlag nachfragen. Die Ontologie wird als **Daten** gepflegt,
nicht als Code-Verzweigung.

### E3 Situationsfragen paraphraseninvariant

„Sind alle Fenster zu?“, „Ist irgendein Fenster auf?“ und „Ist noch was offen?“
sind dieselbe kanonische Abfrage mit umgekehrter Polarität. Situationsfragen werden
auf wenige kanonische Abfragen abgebildet:
- Existenz/Allquantor über Gattung × Ort × Zustand
- Extremwert („wo am wärmsten“, nur Innenräume, außer „draußen“ ist gemeint)
- Anwesenheit je Ort/Etage
- Bedarf (Lüften, Lüfter, Heizen über Schwellen von Feuchte, CO₂ und Temperatur)
- Zustand/Problem eines Geräts („ist mit X alles in Ordnung?“ über Batterie,
  Verfügbarkeit, letzten Alarm)
- Ursache („warum so kühl?“ über Sollwert, Heizbetrieb, offene Fenster)

### E4 Diskurs

- **Ellipse mit Modifikator:** „… und die hintere auch“ bindet an die letzte
  Ergebnismenge bzw. die letzte Rückfrage und wählt über Merkmal/Position.
- **„Und im <Ort>?“** wiederholt die **letzte Frage** mit neuem Ort.
- **„Vergiss es.“ / „Lieber nicht.“** bricht eine offene Rückfrage ab oder macht die
  letzte eigene Aktion rückgängig. Welches von beiden gilt, steht in der Antwort.
- **„Da“ nach einer Frage** bindet an den Ort dieser Frage.

### E5 Höflichkeit und Abschwächung

„Könntest du vielleicht irgendwann mal X?“ ist ein Befehl und keine Zustandsfrage.
Modalverben und Höflichkeitspartikeln werden als Sprechakt-Merkmal ausgewertet und
nicht als Frage nach dem Zustand.

### E6 Mehrfachbefehle mit gemeinsamem Ort

„<Gerät> im <Ort> an und <Gerät> dort <Wert>“: Das „dort“ im zweiten
Teil bindet an den Ort des ersten. Kein Teil darf still wegfallen; wird ein Teil nicht
verstanden, sagt HomeIntent das und führt nichts aus bzw. nur nach Rückfrage.

### E7 Absichtserklärungen

Aussagen wie „Ich fahre jetzt einkaufen“ sind keine Gerätesteuerung. Zu einem passenden,
vom Nutzer definierten Skript bzw. einer Szene („Abwesend“) nur als **Vorschlag**
mit Rückfrage, sonst eine freundliche Antwort ohne Aktion.

---

## Schritt G – Selbstlernen aus dem Dialog (7.4.1, direkt nach C)

Das ist der wichtigste Hebel für „versteht wie ein LLM“ **ohne** LLM: HomeIntent lernt
die Sprache **dieses Haushalts**, und zwar deterministisch, nur mit Bestätigung und nur
als Bedeutungsbaustein, nie als Satz.

### Live-Befund 7.3.0

| Test | Ergebnis |
| --- | --- |
| „Mit Funzel meine ich das Flurlicht.“ → „Ja.“ | ✓ gespeichert; wirkt danach in „Mach die Funzel an“, „Ist die Funzel an?“, „Funzel aus“, auch für Anna. **Das ist das richtige Prinzip.** |
| Unbekanntes Wort („Mach den Klunker im Wohnzimmer an.“) | ✗ „kein passendes Gerät“; keine Frage „Was meinst du mit Klunker?“, kein Angebot zu lernen |
| Rückfrage „Lampe im Kinderzimmer?“ → „Das Nachtlicht.“, danach derselbe Satz | ✗ fragt jedes Mal neu; kein Angebot, sich die Antwort zu merken |
| „Licht im Wohnzimmer an“ → „Nein, nur die Stehlampe.“, danach derselbe Satz | ✗ schaltet wieder alle 3 Lichter; Korrektur wird nicht gelernt |
| „Merk dir, dass ich beim Lesen die Stehlampe auf 40 % möchte.“ | ✓ gespeichert (nur mit `memory_enabled`), aber **nie benutzt**: „Ich lese jetzt.“ → nicht verstanden; „Ich will lesen.“ → keine Szene; „Was weißt du über mich?“ → nicht gefunden |
| „Ja.“ nach „Das lokale Gedächtnis ist deaktiviert.“ | ✗ „nicht verstanden“; besser: sagen, wo man es einschaltet |
| „Was hast du gelernt?“ | ✓ antwortet, aber mit englischem „preference“ statt „Vorliebe/Präferenz“ |
| Alias-Lernen selbst | läuft über 3 eigene Regex in `alias_learning.py`, also eine Sprachinsel |

### Soll

**G1 Unbekannte Wörter erfragen.** Erkennt die Struktur ein Geräte-Nomen, das weder
Lexikon, Gattung noch Alias kennt, antwortet HomeIntent: „Was meinst du mit ‚Klunker‘?“
und bietet Optionen aus Ort und Aktion an (z. B. alle schaltbaren Geräte im
Wohnzimmer). Nach der Antwort führt es aus und fragt dann: „Soll ich mir merken, dass
‚Klunker‘ die Stehlampe ist?“

**G2 Aus Rückfragen lernen.** Wird dieselbe Mehrdeutigkeit (gleiche Gattung × Ort ×
Sprecher) **zweimal gleich** aufgelöst, fragt HomeIntent einmal: „Soll ich künftig bei
‚Lampe im Kinderzimmer‘ direkt das Nachtlicht nehmen?“ Erst nach „Ja“ wird das eine
Standardauswahl. Eine Konfidenz ist keine Einwilligung; ohne Zustimmung wird nichts
übernommen.

**G3 Aus Korrekturen lernen.** Gleiche Logik für „Nein, nur X“/„Ich meinte X“. Nach
wiederholter, gleicher Korrektur einmal fragen, ob die Korrektur künftig gelten soll.

**G4 Gelerntes Wissen wird benutzt.**
- Gespeicherte Vorlieben werden zu Parametern von Aktivitäten: „Ich lese jetzt“, „Ich
  will lesen“ und „Lesezeit“ lösen die gemerkte Einstellung aus. Beim ersten Mal kommt
  eine Vorschau, danach wird wie ein normaler Befehl ausgeführt, durch Policy und
  Executor.
- „Was weißt du über mich?“ und „Wie hell möchte ich …?“ beantworten aus dem
  Gedächtnis.
- „Vergiss, dass …“ und „Vergiss Funzel“ löschen gezielt.

**G5 Sprachmakros.** „Wenn ich ‚Kinoabend‘ sage, mach das Licht im Wohnzimmer aus und
die Rollläden runter.“ wird als benannter, bestätigter Ablauf gespeichert (Vorschau,
dann „Ja“). Jeder Aufruf läuft erneut durch Validator, Policy und die
Skript-Freigabeprüfung aus Schritt A. Es wird **keine** HA-Automation angelegt.

**G6 Gelerntes ist Lexikon, nicht Satz.** Jede gelernte Einheit ist ein Eintrag der
gemeinsamen Bedeutungsschicht, z. B. `Alias(wort → entity)`,
`Standardauswahl(gattung × ort × sprecher → entity)`, `Vorliebe(aktivität → Zustand)`
oder `Makro(name → Plan)`. Sie wirkt dadurch in allen Satzformen, Zeiten,
Wortstellungen, Fragen und Push-Sätzen. `alias_learning.py` wird dabei auf die
gemeinsame Bedeutung umgestellt (keine eigenen Regex mehr).

**G7 Sicherheit beim Lernen.**
- Lernen nur nach ausdrücklichem „Ja“ und nie aus unbestätigten Beobachtungen.
- Gelernte Wörter dürfen nur auf **freigegebene** Entitäten zeigen. Wird eine Entität
  später entzogen, ist der Eintrag wirkungslos und wird im Learning Center markiert.
- Ein Alias darf einen vorhandenen Geräte- oder Gattungsnamen nicht überschreiben (z. B.
  „Licht“). Für kritische Ziele (Schloss, Alarmanlage, Garagentor, Ventil) dürfen nur
  Admins einen Alias anlegen; der kritische Befehl bleibt bestätigungspflichtig.
- **Geltungsbereich** beim Speichern fragen oder als Option festlegen: nur für mich
  oder für den Haushalt. Standardauswahlen und Vorlieben gelten standardmäßig pro
  Person.
- Alles erscheint im Learning Center: wann, von wem, wie oft benutzt. Dort lässt es sich
  bearbeiten und löschen, und es ist exportierbar.

**G8 Kleinigkeiten.**
- Deutsche Bezeichnungen in allen Lern-Antworten.
- Nach „Gedächtnis ist deaktiviert“ auf „Ja“ erklären, wo man es einschaltet.
- Nicht dimmbare Lichter: „Das Flurlicht lässt sich nur ein- und ausschalten.“ statt
  „nicht eindeutig unterstützt“.

### Tests G

Unit-Tests für G1–G8, zusätzlich ein Live-Szenario pro Punkt. Messgröße: Nach dem Lernen
eines Wortes funktioniert es in mindestens 10 **verschiedenen** Satzformen (Befehl,
Frage, Kurzform, mit Ort, mit Zeit, als Push-Auslöser, als Teil eines
Mehrfachbefehls), ohne dass eine davon eigens programmiert wurde.

### Langzeit-Lernen (Verhalten, V11/V12)

Effektzeiten, Zuverlässigkeit, Heizmodell und Gewohnheitsvorschläge bleiben, wie sie
sind: lokal, transparent und ohne automatisches Ausführen. Für die Abnahme braucht das
Testbett einen **Zeitraffer-Test**: Das Testhaus simuliert zwei Wochen Nutzung, und es
wird geprüft, dass passende Gewohnheiten nur **vorgeschlagen** werden, nie eine
Automation entsteht und falsche Vorschläge nach Ablehnung nicht wiederkommen.
Stelle dafür im Code eine testbare Zeitquelle bereit, falls sie fehlt.

---

## Schritt F – Ehrliche Messung und sauberes Testbett (laufend)

1. **Messung der kanonischen Bedeutung:** Für einen Entwicklungskorpus wird nicht nur
   „ok/nicht ok“ geprüft, sondern der erzeugte Frame gegen den erwarteten kanonischen
   Frame (Aktion, Zielmenge, Wert, Zeit, Einmaligkeit, Empfänger). Paraphrasen derselben
   Bedeutung müssen **denselben** Frame liefern. Das ist die Kennzahl für
   Generalisierung.
2. **Entwicklungskorpus getrennt von Abnahmekorpora:** `sim/nlu_probe.py` bleibt
   Abnahme und liefert keine Lexikoneinträge. Die Test-Session misst zusätzlich mit
   einem unveröffentlichten Korpus.
3. **README ehrlich:** Neben dem Wert auf bekannten Korpora auch den Wert auf ungesehenen
   Sätzen nennen (7.3.0: 38 %).
4. **Testbett nicht verändern:** Keine Test-Automationen in `sim/config/automations.yaml`
   einchecken. 7.3.0 hat dort 16 Einträge ergänzt (18 statt 2). Diese Einträge
   entfernen, Test-Automationen in Tests zur Laufzeit anlegen und wieder löschen.
5. **Reset:** `haus_sim.reset` setzt nur Geräte zurück. HomeIntent braucht einen
   Diagnose-Dienst oder eine Testhilfe, die Timer, Listeneinträge, Bindungen, Verlauf
   und Diskurs zurücksetzt, damit Messreihen unabhängig sind.

## Zielwerte

| Kennzahl | 7.3.0 | Ziel |
| --- | --- | --- |
| Falsch geschaltete Geräte (alle Korpora + Skript-/Szenenfälle) | 1 Lücke (Skripte) | **0** |
| Geraten statt gefragt (Ort, Einmaligkeit) | vorhanden | **0** |
| Geräteauflösungen im Code | 2 + private | **1** |
| Regex-Verwendungen | 770 | deutlich sinkend, pro Bereich ausgewiesen |
| Unveröffentlichter Korpus | 38 % | **≥ 65 %** |
| Gelerntes Wort wirkt in verschiedenen Satzformen | 3 von 4 (Alias) | **≥ 10 Formen, auch für Rückfragen, Korrekturen und Vorlieben** |
| Veröffentlichte Korpora, Push-Matrix, Funktionsszenarien | 85 % / 35/35 / 162/162 | nicht schlechter |
| Latenz p95 | < 100 ms | < 100 ms |

## Lieferumfang je Schritt

- Code, Tests, Changelog/README-Abschnitt und Versionsnummer.
- Kurzer Bericht in `docs/`: was umgestellt wurde, Shadow-Abweichungen (Anzahl und
  Klassen) und Messwerte vorher/nachher.
- Keine Änderungen an `sim/` außer dem Entfernen der Test-Automationen (F4).
