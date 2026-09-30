# Auftrag: HomeIntent 7.7.1 (Sicherheit) und 7.8 (Sprachverständnis und Leistung)

Repository `pquandel2-alt/homeintent`. Basis ist **7.7.0**, Branch
`claude/homeintent-sprachverstaendnis-phases-6feab4`, Commit `4b3f4db`. Arbeite dort weiter
oder auf einem Branch davon.

**Grundlage** ist der unabhängige Black-Box-Test mit 1005 blind erstellten Sätzen:
`docs/independent-test-7.7.md` und `docs/perf/independent-test-7.7.json` im Branch
`claude/sleepy-meitner-xd7oux`. Lies den Bericht vollständig, bevor du Code änderst:

```
git fetch origin claude/sleepy-meitner-xd7oux
git show origin/claude/sleepy-meitner-xd7oux:docs/independent-test-7.7.md
```

Der Testkorpus selbst ist absichtlich **nicht** verfügbar; die Test-Session misst danach mit
neuen, unbekannten Sätzen. Die Beispielsätze im Bericht dienen nur der Veranschaulichung.
**Behebe Ursachen, nicht diese Sätze.**

Setze beide Teile in dieser Session um. Teil A zuerst und als eigenes Release. Wird dein
Kontext knapp: committen, pushen und im Bericht festhalten, wo es weitergeht.

---

## 0. Unverrückbar

Alle Grundsätze aus `sim/PROMPT_7.6.1-7.7.md`, Abschnitt 0, gelten weiter. Kurz:
- lokal und deterministisch, kein LLM oder ML-Modell,
- Parser führen nie Dienste aus,
- Validator, EffectGraph, ExecutionPolicy, NEVER_AUTO und Executor sind die einzigen Instanzen,
  die über Ausführung entscheiden,
- eine Bedeutungsebene (`SemanticUtterance`/`MeaningClause`), eine Zielauflösung
  (`resolve_phrase`), ein Arbiter; keine neue parallele Struktur,
- Migration mit Shadow, 0 SAFETY_DRIFT je Umschaltung, alte Pfade danach löschen,
- **keine Satzmuster:** Jede Verbesserung ist eine Regel über Struktur, Lexikon, Modalität,
  Diskurs oder Ontologie. Der Regex-Ratchet (`SEMANTIC_SENTENCE_PATTERN` ≤ 173) darf nicht
  steigen, Umklassifizieren zählt nicht,
- **„Lieber ehrlich nicht verstehen als falsch handeln.“** Keine Änderung darf eine bisher
  sichere Ablehnung in eine unsichere Ausführung verwandeln.

**Harte Haltepunkte:** Weiter geht es nur, wenn
- alle Tests grün sind,
- die Property-Suite 0 Verletzungen zeigt,
- der Arbiter-, Dialog- und Korpus-Shadow keinen SAFETY_DRIFT zeigt,
- das Live-Testbett vollständig grün ist.

---

# Teil A – 7.7.1: Die 15 unsicheren Ausführungen (P0/P1-Sicherheit)

Im Test wurde 15-mal ein Gerät geschaltet, das nach der Satzbedeutung nicht geschaltet werden
durfte. Kritische Geräte waren nie betroffen. Jeder Cluster wird **als Regel** behoben und
bekommt eine neue **Sicherheitsinvariante** in `tests/test_safety_properties.py`, deren
Satzvarianten diese Form tatsächlich erzeugen. Die bisherigen Generatoren haben diese Formen
offensichtlich nicht erreicht. Prüfe deshalb zuerst, warum, und erweitere die Generatoren um
die fehlenden Bausteine.

### A1 – Selbstkorrektur als Satzstruktur (SC-1)

- **Befund:** Korrekturmarker **ohne Negationswort** führen zur Ausführung des
  zurückgenommenen Teils bzw. beider Teile:
  - „…, ich meine den Fernseher.“ → Radio **und** Fernseher aus.
  - „…, halt, im Schlafzimmer.“ → das zurückgenommene Kinderzimmer an.

  Andere Marker (nein, äh, nee, Quatsch, sorry, Moment, korrigiere, ach nee, oder nee) führen
  zu „nicht verstanden“; in 35 Fällen war das sicher, aber unbrauchbar.
- **Soll:** Das gemeinsame Sprach-Frontend erkennt einen **Korrekturmarker** (Lexikonklasse:
  nein, nee, ne, äh, ähm, halt, stopp, Moment, Quatsch, sorry, Entschuldigung, korrigiere,
  ich meine, ich meinte, also, sprich, lieber, oder, doch, ach …) zwischen zwei Teilen.
  Es ergibt sich eine Struktur **Widerruf + Ersatz**:
  1. Der Ersatz ersetzt **nur** die Felder, die er nennt (Ziel, Ort, Seite, Wert, Operation,
     Zeit). Die übrigen Felder kommen aus dem widerrufenen Teil. Beispiel: „Küchenlicht an,
     nein, das im Flur“ ergibt Licht, Flur, an.
  2. Ist der Ersatz leer oder nur ein Abbruch („nein, doch nicht“, „lass mal“, „stopp“),
     wird nichts ausgeführt, und HomeIntent bestätigt das.
  3. Lässt sich die Struktur nicht eindeutig bilden, kommt eine Rückfrage mit beiden Lesarten.
     **Nie** wird der widerrufene Teil ausgeführt, und nie werden beide Teile ausgeführt.
  4. Mehrfache Korrekturen („rechte, nee die linke, ja die linke“) gelten in Reihenfolge; die
     letzte gewinnt.
- **Invariante:** Für jede Kombination Befehl × Marker × Ersatz schreibt HomeIntent nie auf
  ein Ziel, das nur im widerrufenen Teil vorkommt.

### A2 – Irrealis, Abwägung, Beibehaltung (SC-2, SC-8)

- **Befund:**
  - „Hätte ich doch die Heizung … ausgeschaltet.“ → Heizung aus.
  - „Ich hätte die Stehlampe heller machen sollen.“ → heller.
  - „Ich überlege, ob ich den Mähroboter starten soll.“ → gestartet.
  - „Den Fernseher lass bitte aus.“ → turn_off.
- **Soll:** Die Modalität der Bedeutungsebene kennt zusätzlich
  - **Irrealis der Vergangenheit** (Konjunktiv II + Perfekt: hätte/wäre … Partizip,
    „hätte … sollen/müssen“),
  - **Abwägung/Selbstgespräch** (ich überlege/frage mich/weiß nicht, ob …; vielleicht sollte
    ich …; ob ich wohl …),
  - **Beibehaltung** (lass/lasst … an/aus/zu/offen/so; bleibt/soll … bleiben).

  Alle drei sind nie ausführbar. Beibehaltung wird bestätigt („Ich lasse den Fernseher aus.“);
  bei Abwägung darf HomeIntent einen Vorschlag anbieten, führt aber nichts aus.
- **Invariante:** Diese Rahmen um einen beliebigen gültigen Befehl erzeugen nie einen Write.

### A3 – Ellipsen-Vertrag (SC-3, SC-4, SC-6)

- **Befund:**
  - „Fahr den linken … hoch.“ → „Den rechten runter.“ fährt den **linken**.
  - „Mach die Stehlampe an.“ → „Und das Deckenlicht aus.“ schaltet die **Stehlampe** aus.
  - „Mach das Licht im Flur aus.“ → „Morgen früh wieder an.“ schaltet **sofort** ein.
  - „Mach das Licht im Flur an.“ → „Oben auch.“ schaltet **8 Lichter** der Etage.
- **Soll:** Eine Folgeäußerung übernimmt vom Vorgänger nur Felder, die sie **nicht selbst
  nennt**:
  - Ein neu genanntes Objekt, eine Seite, ein Merkmal, ein Ort, ein Wert oder eine Zeit
    **ersetzt** das Feld. Es wird nie ignoriert.
  - Ein Ortswechsel („oben auch“, „im Bad auch“) überträgt die **Gattung/Rolle** des
    Vorgängers (Flurlicht → Flurlicht oben) und **erweitert nie die Menge** (keine
    Etagenmenge statt Einzelgerät). Bei mehreren Kandidaten kommt eine Rückfrage.
  - Eine Zeitangabe in der Ellipse macht den Auftrag zeitgebunden (Automation bzw. einmaliger
    Auftrag mit Vorschau), nie sofort.
- **Invariante:** Für Vorgänger × Ellipse mit neuem Feld schreibt HomeIntent nie auf das Ziel
  des Vorgängers, wenn die Ellipse ein anderes Objekt oder eine andere Seite nennt. Mit
  Zeitangabe schreibt es nie sofort. Die Zielmenge ist nie größer als die des Vorgängers,
  außer die Ellipse sagt ausdrücklich „alle“.

### A4 – Koordination ohne stille Teilausführung (SC-5, SC-10)

- **Befund:**
  - „Garten- und Terrassenlicht ein“ → nur eines.
  - „Küche und Esszimmer Rollladen runter“ → nur Esszimmer.
  - „…, dann im Keller und in der Waschküche“ → nur Flur.
  - Die Vorschau zu „Im Wohnzimmer Licht aus und die Rollläden runter“ enthielt die Rollläden
    des **ganzen Hauses**.
- **Soll:**
  - Ergänzungsstrich („Garten- und Terrassenlicht“), gemeinsamer Kopf („Küche und Esszimmer
    Rollladen“) und gemeinsames Verb über Aufzählungen werden in der Bedeutungsebene
    expandiert.
  - Ein im ersten Teil genannter Ort gilt für nachfolgende Teile ohne eigenen Ort.
  - **Jeder nicht erklärte Rest einer Aufzählung** führt zu „Teil X nicht verstanden, nichts
    ausgeführt“, nie zu stiller Teilausführung.
- **Invariante:** Die Zahl der ausgeführten Klauseln ist gleich der Zahl der genannten
  Klauseln, oder es wird nichts ausgeführt.

### A5 – Satellitenraum hat Vorrang (SC-7)

- **Befund:** Satellit im Bad, Badlüfter nicht freigegeben. „Mach den Lüfter an.“ schaltet
  den Ventilator im **Schlafzimmer**.
- **Soll:** Bei einem Befehl ohne Ortsangabe ist der Satellitenraum die Ortsbeschränkung.
  Gibt es dort kein passendes freigegebenes Gerät, sagt HomeIntent das und bietet ggf. Geräte
  anderer Räume als **Rückfrage** an. Es wählt nie still ein Gerät anderswo.

### A6 – Informierte Bestätigung (SC-9)

- **Befund:** „Soll ich das Skript Schlafen starten?“ wurde bestätigt, und die Haustür wurde
  entriegelt. Das Skript war zuvor geändert worden; die Rückfrage nannte die kritische Wirkung
  nicht.
- **Soll:** Jede Rückfrage zu einem Skript, einer Szene, einer Gruppe, einem Makro oder einer
  Routine nennt die **Wirkungen ab Risiko HIGH** aus dem EffectGraph, z. B. „Das Skript
  Schlafen entriegelt dabei die Haustür. Soll ich es starten?“. Die gespeicherte Wirkung der
  Rückfrage (`PendingServiceConfirmation.scope`) bleibt wie in 7.7.

**Release 7.7.1:**
- Version, Changelog, Tests grün, CI grün.
- Live-Testbett 162/162 plus je Cluster ein neues Live-Szenario in `sim/scenarios.py`. Das ist
  erlaubt; halte dich an das bestehende Format.
- Kurzer Abschnitt in `docs/umsetzung-7.7.1-7.8.md`.

---

# Teil B – 7.8: Sprachverständnis und Leistung

Messziel: Die unabhängige Messung mit neuen Sätzen soll steigen, ohne dass unsichere
Ausführungen hinzukommen. Arbeite je Cluster mit eigenen Paraphrasen und halte einen Teil
zurück, bevor du nachbesserst. Berichte beide Werte getrennt, wie in 7.6/7.7.

### B1 – Rest-Behandlung ehrlich machen (NL-2), zuerst

- **Befund:** In etwa 25 Fällen führt ein nicht verstandener Satzrest (Korrektur, Zeit,
  Nachsatz, Ergänzung) zu einer **falschen Fähigkeitsmeldung** („Küchenlicht lässt sich nur
  ein- und ausschalten und dimmen.“, „Garagentor kann ich nicht steuern, nur abfragen.“).
- **Soll:** Die Fähigkeitsmeldung kommt nur, wenn die verstandene Operation wirklich nicht
  unterstützt wird. Ein Rest wird als Rest gemeldet („Den Teil ‚…‘ habe ich nicht
  verstanden.“). Das ist die Voraussetzung für die folgenden Punkte.

### B2 – Höflichkeits- und Begründungsrahmen kompositionell (NL-1)

- **Befund:** Etwa 40 Fälle scheitern an
  - Höflichkeitsrahmen („Sei so lieb und …“, „Hättest du die Güte, …“, „Würde es dir etwas
    ausmachen, …“, „Magst du …“, „Wäre super, wenn du …“),
  - Nachsätzen („…, danke“, „danke dir, …“),
  - Begründungen („…, is feucht“, „…, wir essen“, „…, ich arbeite“, „…, ich schlaf gleich“,
    „…, nervt“),
  - Beschleunigern („…, fix“, „…, aber zack“, „…, schnell!“).

  „Wenn du so freundlich wärst, …“ und „Wäre super, wenn du …“ wurden sogar als
  **Automation** gelesen.
- **Soll:** Das Sprach-Frontend erkennt diese Teile als Rahmen, Dank, Begründung oder
  Dringlichkeit. Sie sind ohne Wirkung auf Ziel und Operation, werden aber in der
  Bedeutungsebene festgehalten.
  - Ein **höflicher Konditionalrahmen** („wenn du so nett wärst“, „wenn's geht“) ist nie ein
    Automations-Trigger.
  - Eine echte Bedingung („wenn das Fenster aufgeht“) bleibt eine.
- **Invariante:** Solche Rahmen ändern weder die Zielmenge noch die Sicherheitsform eines
  Befehls. Eine eingebettete Frage („Kannst du mir sagen, ob …“) bleibt eine Frage.

### B3 – Verblose Kurzbefehle, Partikelverben, Werte ohne Einheit (NL-3, NL-4)

- **Befund:**
  - Nicht verstanden werden „Esszimmer an.“, „Kaffee an.“, „Garage zu.“, „Markise raus.“,
    „Den Sauger los schicken.“, „Rasenmäher raus.“, „Wohnzimmer Rollläden halb.“,
    „led streifen auf siebzig prozent“ und „heizung schlafzimmer auf achtzehn“.
  - „Heizung Kinderzimmer 21 Grad.“ liest den Ist-Wert vor.
  - „… auf 21.“ ohne „Grad“ fragt „Auf welche Temperatur?“.
  - „Auf wie viel Grad steht die Heizung?“ nennt den Ist- statt den Sollwert.
- **Soll:**
  - Kurzbefehle werden aus Gattung/Raum/Gerät + Richtungs- oder Zustandspartikel
    (an/aus/zu/auf/raus/rein/hoch/runter/halb/los) + optionalem Wert gebildet.
  - Ein Raum allein + an/aus bedeutet das Licht des Raums.
  - Ein Gerät, eine Zahl und kein Frageanzeichen ergibt einen Befehl.
  - Die Einheit wird aus der Gattung erschlossen: Heizung → °C, Licht, Rollladen und
    Lautstärke → %.
  - „steht/eingestellt/Soll“ fragt nach dem Sollwert, „ist/hat/wie warm“ nach dem Messwert.
  - Wenn Ziel, Wert und Einheit gegeben sind und kein Fragewort vorkommt, entsteht nie eine
    Zustandsfrage.

### B4 – Ellipsen mit Operationsübernahme (NL-5)

- **Befund:** Nicht verstanden werden
  - „Und im Kinderzimmer auf 19.“, „Küche ebenfalls.“, „Im Schlafzimmer ebenso.“,
  - „Noch eins höher.“, „Lauter.“,
  - „Das Gleiche im Esszimmer.“, „Wie vorhin.“,
  - „Und wie viele davon sind an?“.
- **Soll:** Aufbauend auf A3 übernimmt eine Ellipse die **Operation** des Vorgängers, wenn sie
  selbst keine nennt. Dazu gehören „ebenfalls/ebenso/auch/das Gleiche/genauso“ sowie reine
  Werte, Stufen und Orte. Das gilt für Befehle und Fragen.

### B5 – Automationssprache auf die gemeinsame Klauselanalyse (NL-6)

- **Befund:** Etwa 35 Fälle scheitern mit „Trigger und Aktion nicht eindeutig“ bzw. „Was soll
  dann passieren?“:
  - „Immer wenn …“, „Wenn im <Raum> jemand ist …“,
  - Schwellen mit verbloser Folge („… unter 18 Grad fällt, Heizung hoch“),
  - „Jeden Morgen um halb sieben die Kaffeemaschine an.“,
  - „Bei Sonnenuntergang die Außenbeleuchtung an.“,
  - „Wenn es draußen wärmer als 25 Grad wird, fahr die Markise aus.“ → „Welche Heizung?“.
- **Soll:**
  - Die Aktionsklausel einer Automation wird mit **derselben** Klausel- und Verbanalyse wie
    ein Direktbefehl gelesen, also auch verblos (B3).
  - Trigger-Wörter „immer wenn/jedes Mal wenn/sobald/falls/bei“.
  - Präsenz („jemand ist/kommt in …“, „niemand mehr“) an Präsenzsensoren.
  - Messgröße des Triggers und Gattung der Aktion werden unabhängig aufgelöst (Temperatur
    draußen ≠ Heizung).
  - Nicht unterstützte Formen (Dauer, Leistung, Kombinationen) werden weiter ehrlich
    abgelehnt.
  - Nicht-Admins ohne Freigabe erhalten die Ablehnung **vor** der Vorschau.

### B6 – Gerät vor Raum/Kontakt, Zählfragen, Leistungssensoren (NL-7, NL-8)

- **Befund:**
  - „Mach die Garage auf.“ wird nicht gefunden.
  - „Mach die Haustür auf/zu“ geht an den Kontakt statt an das Schloss.
  - „Alarmanlage aus“ bekommt „nur abfragen“.
  - „Ist das Licht im Bad an?“ (2 Lichter) und „Ist der Fernseher an?“ → „Ziel nicht
    gefunden“.
  - „Wie viele Lichter sind im Haus?“ liefert eine Liste.
  - „Wie viel Strom zieht die Kaffeemaschine?“ findet den Leistungssensor nicht.
- **Soll:**
  - Passt eine Operation nur zu einem steuerbaren Gerät gleicher Bedeutung (Tor zu Garage,
    Schloss zu Haustür), wird dieses gewählt, **bei kritischen Geräten immer mit
    Bestätigung**. Sonst kommt eine Rückfrage.
  - Deaktivieren der Alarmanlage: Rückfrage bzw. Hinweis auf den Code, keine falsche
    Fähigkeitsmeldung.
  - Zustandsfragen über Gattung × Ort mit mehreren Treffern beantworten für alle.
  - „wie viele“ antwortet mit Zahl (plus Namen bis 6).
  - Messgrößen eines Geräts (Leistung, Batterie, Energie) über die Geräteverknüpfung der
    Registry finden.

### B7 – Dialoge und Namen (NL-9, NL-10, NL-11, Lernen)

- Eine offene Bestätigung
  - akzeptiert „Ja, mach“, „genau“, „klar“ und „los“,
  - beantwortet eine **vollständige neue Frage** und lässt die Bestätigung offen oder
    verwirft sie ausdrücklich,
  - führt einen neuen vollständigen Befehl aus und verwirft die offene Bestätigung (wie in
    7.7 im Arbiter modelliert).

  „Bitte antworte mit Ja oder Nein“ darf nur bei unklaren Kurzantworten kommen.
- Ein exakter voller Entitätsname hat Vorrang vor Präfixtreffern („Gute Nacht Test“ vor
  „Gute Nacht“).
- Keine Antwortvorlage setzt Wortteile ein („kein Licht (steh)“). Bei unbekannten oder nicht
  freigegebenen Namen gilt der Text aus 7.6.1 A5.
- Lernen:
  - „Vergiss <Alias>“ löscht den Alias.
  - Makroaufrufe („Kinoabend.“, „Nachtruhe jetzt.“) und Vorlieben-Aktivitäten („Ich lese
    jetzt.“) werden über die Bindings erkannt.
  - „Was hast du gelernt?“ nennt auch Aliasse und Bindungen.
- Der Befehl „Schalte die Gruppe <Name> ein“ an Licht- und Schaltergruppen wird ausgeführt.

### B8 – Leistung (PERF-1)

- **Befund:** Bei 5000 Entitäten ganzer Turn p50 377 ms, p95 594 ms. Rund 80 % der Zeit gehen
  in `house_graph.build_house_graph` und `entities.build_entity_index`, die **in jedem Turn**
  neu gebaut werden.
- **Soll:**
  - Hausgraph und Entitätsindex werden je **Registry- und Freigabe-Stand** gecacht und bei
    Änderungen gezielt invalidiert: Entitäts-, Geräte-, Bereichs- und Etagen-Registry,
    Freigabe, Aliasse, Bindungen.
  - Zustandswerte werden live gelesen und nicht mitgecacht.
  - Ein Test belegt, dass nach jeder dieser Änderungen der neue Stand gilt; insbesondere
    wirkt ein **entzogenes Gerät sofort** nicht mehr.
- **Ziel:** ganzer Turn bei 5000 Entitäten p95 < 150 ms (Messung mit
  `scripts/benchmark_turn.py`, p50/p90/p95/p99 berichten).

---

## Tests und Messung

- **Je Cluster:**
  - Unit-Tests der Regel,
  - neue bzw. erweiterte Sicherheitsinvariante (Teil A),
  - eigene Paraphrasen mit zurückgehaltenem Teil (Teil B).
- **Pflicht-Shadows vor jedem Umschalten:** Korpus-Shadow, Dialog-Shadow, Arbiter-Shadow.
  0 SAFETY_DRIFT; gewollte Verhaltensänderungen einzeln im Bericht.
- **Pflichtläufe:**
  - Property-Suite im Nightly-Profil,
  - Live-Testbett vollständig inkl. Proaktiv,
  - README-Beispiele,
  - Push-Matrix.
- Entwicklungs-Benchmark 7.7 (`tests/eval/dev_benchmark_77.txt`): nicht schlechter,
  `unsafe_execution_count` 0.

## Lieferumfang

- **7.7.1** (Teil A) und **7.8.0** (Teil B) als eigene Versionen mit Changelog und
  README-Abschnitt; CI grün auf dem jeweiligen Release-Commit.
- Bericht `docs/umsetzung-7.7.1-7.8.md`: je Cluster Ursache, Regel, betroffene Dateien,
  Shadow-Ergebnis, Tests, bewusst Offengelassenes.
- Keine Modellnamen in Commits oder Dateien. Einen Pull Request nur erstellen oder
  aktualisieren, wenn der Nutzer das verlangt.
- Danach misst die Test-Session unabhängig mit einem **neuen** Korpus.
