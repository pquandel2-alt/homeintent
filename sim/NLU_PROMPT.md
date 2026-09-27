# Auftrag: HomeIntent zu natürlichem Sprachverständnis ausbauen (Release 7.3.0)

Repository `pquandel2-alt/homeintent`, Basis: aktueller `main` (nach 7.2.2, falls
vorhanden, sonst 7.2.1). Lege einen neuen Branch an.

**Ziel:** HomeIntent soll Fragen und Aussagen im Haus so verstehen, wie ein Mensch oder
ein LLM sie meint – auch indirekte Wünsche („Mir ist kalt“), Gerätegattungen statt
Namen („Mach die Glotze an“), Schlussfolgerungsfragen („Muss ich lüften?“), Kontext
(„Etwas heller bitte“) – und die passenden Aktionen sicher ausführen.

## Zuerst lesen

Vom Branch `claude/sleepy-meitner-xd7oux` (`git fetch origin claude/sleepy-meitner-xd7oux`):

- `docs/sprachverstaendnis-analyse-7.2.1.md` – Messung (11/66 = 17 %), Ursachen und der
  empfohlene Drei-Stufen-Weg. **Dieses Dokument ist die fachliche Spezifikation.**
- `sim/nlu_probe.py` – Messkorpus (66 Sätze) und `sim/results/nlu_probe_7.2.1.json` – Ausgangswerte.
  Beide ins Arbeitsverzeichnis übernehmen.

Außerdem: `README.md`, `docs/architecture-v8*.md`–`v12.md`, `docs/jarvis-core.md`, das
Testbett `sim/README.md`. Mach dich mit dem Weg vertraut, den ein Satz heute nimmt:
`nlu/language_frontend.py` → `nlu/german_structure.py`/`semantic_graph.py` →
`nlu/semantic_interpreter.py`/`semantic_compiler.py` → `nlu/understanding.py`
(`UnderstandingKind`) → Entity-Auflösung (`nlu/entity_resolution.py`, `entities.py`) →
Validator/`execution_policy.py`/`service_executor.py`, und Abfragen über
`nlu/query_executor.py`, `household_query.py`, `house_graph.py`.

## Umsetzung

### Stufe 1 – symbolisch generalisieren (Standard, ohne neues Modell)

Umsetzen wie in Abschnitt 3 „Stufe 1“ der Analyse beschrieben:

1. **Bedürfnis-Ontologie** (`nlu/needs.py` o. ä.): typisierte Abbildung
   Zustandsaussage → gewünschte Wirkung → Fähigkeit, mit Raum aus Satz, Satellit oder
   Kontext. Risikoarm und eindeutig → ausführen mit kurzer Begründung („Ich habe die
   Heizung im Wohnzimmer um ein Grad erhöht.“); sonst Vorschlag mit Rückfrage.
   Abdeckung mindestens: kalt/warm/heiß, dunkel/hell/blendet, stickig/feucht/muffig,
   laut/leise, „ich gehe schlafen/aus dem Haus/bin zurück“, „ich will fernsehen/einen
   Film schauen/lesen“ (Szenen/Routinen über Namen, Tags und Beschreibung abgleichen).
2. **Gattungs-Lexikon**: Synonyme je Geräteklasse, aufgelöst über Domäne,
   `device_class`, Fähigkeiten und Bereich – nicht über den Friendly Name. Mehrere
   Treffer → bestehender Rückfrage-Mechanismus.
3. **Situations-Sichten** als eigene, lesende Abfragetypen im Query-Executor: „noch an /
   vergessen auszuschalten“, „alles zu / abgeschlossen / sicher“, „muss ich lüften“
   (dokumentierte Schwellen für Feuchte/CO2), „warum ist es kalt/warm in X“ (Sollwert,
   Heizbetrieb, offenes Fenster, Außentemperatur – nur belegte Fakten), „was ist los“,
   Präsenz je Raum, „was kann ich in X steuern“, Räume je Etage, Anzahl je Gattung,
   „was macht Skript/Szene X“ (aus der HA-Konfiguration), „wofür ist X“ (Bereich, Klasse,
   Fähigkeiten).
4. **Diskurs**: Ellipsen und Pronomen („etwas heller“, „die andere“, „alle“, „die“, „da“,
   „dann …“, „auch“) binden an letztes Ziel/letzte Ergebnismenge/letzten Raum mit
   bestehender Kontext-TTL.
5. **Modalität**: „lass X an“ = nichts; „X muss/braucht nicht an sein“, „X kann aus“,
   „ich brauche X nicht mehr“ = aus; „nicht X, Y meine ich“ = Korrektur; höflich-vage
   Formen („könntest du vielleicht irgendwann mal …“) = normaler Befehl.
6. **Zeitsprache**: „halb/viertel nach/vor“, „sobald es dunkel wird“ (Sonne oder
   Helligkeitssensor), „wenn ich nach Hause komme“ (eigene Person), „weck mich um … mit
   Licht“ (Einmal-Automation mit Vorschau).

Alles bleibt deterministisch, lokal und innerhalb des heutigen Latenzbudgets.

### Stufe 2 – lokaler Kandidaten-Ranker (optional, ohne LLM)

Wie in Abschnitt 3 „Stufe 2“ beschrieben: kleines mehrsprachiges Satz-Embedding (ONNX,
CPU) als **Kandidatengenerator** für Sätze, die Stufe 1 als `UNSUPPORTED` einstuft.
Jeder Kandidat wird vom symbolischen Compiler vollständig verifiziert; unsicherer
Abstand → Rückfrage. Als optionales Paket bzw. Option „Erweitertes Sprachverständnis
(lokal)“, standardmäßig aus, falls die Abhängigkeit für HACS-Installationen zu groß ist.
Wenn du nach Prüfung zu dem Schluss kommst, dass Stufe 2 den Aufwand gegenüber Stufe 1 und
3 nicht rechtfertigt, begründe das im PR und lass sie weg.

### Stufe 3 – optionale LLM-Brücke über `ai_task` (Opt-in, standardmäßig aus)

Wie in Abschnitt 3 „Stufe 3“ beschrieben:

- Neue Option „Sprachmodell für unverstandene Sätze“ mit Auswahl einer `ai_task`-Entität
  (Standard: aus). Aufruf nur, wenn Stufe 1 und 2 `UNSUPPORTED` liefern.
- `ai_task.async_generate_data(hass, task_name=…, entity_id=…, instructions=…, structure=…)`
  mit einem strikten Schema, das genau HomeIntents typisierten Bedeutungen entspricht
  (Befehl, Abfrage, Automation, Rückfrage, „nicht zuständig“). Übergeben werden nur der Satz,
  freigegebene Entitäten mit Name, Bereich, Etage, Klasse und Fähigkeiten, der
  Satellitenbereich und ein kurzer Diskurskontext – keine Verlaufsdaten, keine
  Personenstandorte, keine Geheimnisse.
- Die Antwort ist ein **Vorschlag**: Schema-Validierung → Überführung in `SemanticFrame`/
  `AutomationModel`/Query → derselbe Validator, dieselbe Richtlinie, Bestätigung,
  Wirkungsprüfung. Das LLM ruft nie Dienste auf. Aus LLM-Deutungen stammende Aktionen mit
  Risiko ≥ mittel verlangen immer eine Bestätigung. Unbekannte Entity-IDs,
  Fantasiewerte oder Schema-Fehler → verwerfen und ehrlich „nicht verstanden“.
- Zeitbudget (z. B. 4 s) mit sauberem Abbruch; ohne Modell/Netz verhält sich HomeIntent
  wie ohne die Option.
- **Lernen**: Nach bestätigter Ausführung fragt HomeIntent optional „Soll ich mir diese
  Formulierung merken?“ und speichert sie als lokale Paraphrase bzw. Alias (sichtbar und
  löschbar im Learning Center), damit derselbe Satz künftig ohne LLM geht.
- Antworten und Learning Center kennzeichnen, wenn ein Satz über das Sprachmodell
  verstanden wurde. README und `docs/security-privacy.md` beschreiben klar, dass das
  Versprechen „ohne LLM“ für den Standard gilt und die Option es bewusst aufhebt.

## Regeln

- Das Sicherheitsmodell bleibt unverändert: Parser und LLM führen nichts aus; Validator,
  ExecutionPolicy, Bestätigungen, Benutzerbindung, NEVER_AUTO, Nur-Lesen/Nur-Admin gelten
  für jede Bedeutungsquelle. Mehrdeutigkeit → Rückfrage, nie raten.
- **Generalisierung statt Auswendiglernen:** `sim/nlu_probe.py` ist Abnahme-Holdout. Leite
  daraus keine Lexikon- oder Grammatikeinträge ab. Lege einen eigenen
  Entwicklungskorpus an (mindestens 300 Sätze, handgeschrieben, andere Formulierungen,
  inkl. Negativ- und Sicherheitsfälle) unter `tests/eval/natural_language_cases.json`.
- Keine bestehenden Tests löschen oder abschwächen; bestehende Szenarien bleiben grün.
- Latenz ohne LLM: p95 < 100 ms bei 5000 Entitäten (bestehende Benchmarks).
- Keine Modellbezeichnungen in Commits, Code oder PR-Text.

## Abnahme

1. Alle CI-Prüfungen grün (Stub-Suite mit hassil 3.11 und 3.12, `tests_ha`, Pyright,
   Pyflakes, Sprach-Gate, Benchmarks).
2. Testhaus frisch (`sim/fresh_ha.sh`): `sim/runner.py` vollständig grün;
   `sim/check_log.py` ohne HomeIntent-Befunde.
3. `python sim/nlu_probe.py --out sim/results/nlu_probe_7.3.0.json`:
   - **ohne** LLM-Option: ≥ 60 % „ok“ (Ausgangswert 17 %), **0 falsche Geräteaufrufe**;
   - **mit** LLM-Option: zusätzlich eine Messung mit einer echten oder einer im Test
     simulierten `ai_task`-Entität (deterministische Test-Entität, die für Sätze eine
     feste strukturierte Antwort liefert, plus Fälle mit Fantasie-Entitäten,
     Schemafehlern und Zeitüberschreitung); die Sicherheitsfälle müssen abgewiesen werden.
4. Neue Szenarien in `sim/scenarios.py` (Kategorie „Natürliche Sprache 7.3“) für jede
   Unterfunktion aus Stufe 1, jeweils mit anderen Sätzen als im Korpus.
5. Dokumentation: README-Abschnitt „Wie HomeIntent Sprache versteht“ aktualisiert,
   `docs/architecture-v13.md` (Bedürfnis-Ontologie, Sichten, Diskurs, LLM-Brücke,
   Datenschutz), Version 7.3.0, PR gegen `main` mit Messwerten vorher/nachher.

Arbeite in dieser Reihenfolge: Stufe 1 (in kleinen, einzeln getesteten Commits je
Unterfunktion) → Messung → Stufe 3 → Messung → Stufe 2 (oder begründeter Verzicht) →
Dokumentation → PR.
