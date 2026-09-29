# Auftrag: HomeIntent 7.6.1 (Fehlerbehebung) und 7.7 (Architecture Completion & Hardening)

Repository `pquandel2-alt/homeintent`. Basis ist Branch
`claude/homeintent-sprachverstaendnis-phases-6feab4`, Stand `e674b4a` (7.6.0 mit angepassten
Testbett-Szenarien, CI grün). Arbeite dort weiter oder auf einem Branch davon.

Hintergrund: `docs/nachtest-7.6.0.md` im Branch `claude/sleepy-meitner-xd7oux`, dazu der
Umsetzungsbericht `docs/umsetzung-7.3.1-7.6.md` in deinem Branch.

Der Auftrag hat zwei Teile in fester Reihenfolge:
- **Teil A (7.6.1):** konkrete Fehler aus dem unabhängigen Nachtest, darunter eine Regression.
  Klein, schnell, zuerst.
- **Teil B (7.7):** keine Feature-Runde, sondern eine Konsolidierung. Weniger konkurrierende
  Autoritäten, weniger Spezialwege, klarere Modulgrenzen, mindestens dieselbe Funktion und
  Sicherheit.

Setze beide Teile in dieser Session um. Halte nicht zwischen den Wellen an, außer ein Haltepunkt
aus Abschnitt 0 greift. Wird der Kontext knapp: committen, pushen und im Bericht festhalten, wo es
weitergeht.

---

## 0. Unverrückbar (gilt für A und B)

- **Lokal, deterministisch, nachvollziehbar.**
  - Kein LLM, keine Cloud-NLU, keine Embeddings, kein ML-Modell, kein probabilistisches Raten bei
    Geräteaktionen.
  - Bei Unsicherheit fail-closed: Rückfrage oder nichts tun.
- **Eine Kette, die niemand umgeht:**

  ```
  Sprache → Bedeutung → Grounding → Validator → EffectGraph → ExecutionPolicy (+ NEVER_AUTO)
    → ggf. Bestätigung → Executor → Home Assistant → Effect Verification → Trace
  ```

  Keine Sprach-, Lern-, Dialog- oder Agentenschicht darf diese Kette abkürzen.
- **Diese 7.3–7.6-Bausteine bleiben und werden nicht abgeschwächt:**
  - `effect_graph.py` samt erneuter Prüfung direkt vor dem Schreiben
  - `execution_context.py`, `execution_trace.py`
  - `bindings.py` (Binding ≠ Autorisierung)
  - `resolve_phrase` als einzige Namensauflösung
  - `meaning_ir.py`, `arbitration.py`
  - Property-Suite, Resolver-/Arbiter-/Island-Shadow, Regex-Klassifikation
  - NEVER_AUTO, zentrale ExecutionPolicy, `service_executor.py` als einziger Schreibpfad
  - Fail-closed bei unbekannten Skript-/Szenenwirkungen
  - keine Teilausführung teilweise verstandener Mehrfachbefehle
- **Keine neue parallele Bedeutungsrepräsentation.** Kein `SemanticIntentV2`,
  `UnifiedMeaning`, `CanonicalIntent` o. Ä. Weiterentwickelt wird `SemanticUtterance`/
  `MeaningClause`. Ein neuer Typ ist nur erlaubt mit Nachweis, dass die bestehende Struktur eine
  notwendige Eigenschaft nicht darstellen kann.
- **Leitfrage bei jeder Änderung:** Wird HomeIntent dadurch einfacher und verständlicher, oder
  kommt nur eine Schicht dazu? Im zweiten Fall: nicht machen.
- **Harte Haltepunkte:** Weiter geht es nur, wenn
  - alle Tests grün sind,
  - die Property-Suite 0 Verletzungen zeigt,
  - jede Umschaltung 0 SAFETY_DRIFT hat.

  Sonst bleibt der betroffene Teil im Shadow-Modus, der Grund kommt in den Bericht, und du machst
  mit Unabhängigem weiter.

---

# Teil A – HomeIntent 7.6.1: Befunde aus dem Nachtest

Jeder Punkt bekommt einen eigenen Commit mit Test. Am Ende von Teil A: Version 7.6.1, Changelog,
CI grün auf dem Commit, Push.

### A1 (hoch, Regression) – Nicht-Admins können keine Automationen mehr anlegen

- **Befund:** Anna (kein Admin, `allow_non_admin_automations: true`) sagt „Benachrichtige mich,
  wenn das Küchenfenster aufgeht.“ und bestätigt mit „Ja.“. Antwort: „Fehler beim Erstellen der
  Automation: Unauthorized“; es wird keine Automation angelegt. In 7.3.0 ging das.
- **Ursache:** `automation_executor.py` ruft `automation.reload`, einen Admin-Dienst in HA, mit
  `call_context()` auf, also seit 7.3.2 mit Annas Benutzer-ID.
- **Soll:**
  - Für HomeIntent-interne **Verwaltungsaufrufe** (Automations-Reload; Anlegen, Ändern und
    Löschen der eigenen Automationskonfiguration) gibt es eine benannte Funktion in
    `execution_context.py`, z. B. `system_context_for_turn()`. Sie liefert einen Kontext ohne
    `user_id`, dessen `parent_id` der Turn-Kontext ist; so bleibt die Trace-Kette erhalten.
  - Die Berechtigung hat die HomeIntent-Policy vorher geprüft (`allow_non_admin_automations`,
    `validate_automation_action_targets`).
  - **Gerätewrites bleiben beim Nutzerkontext.**
  - Ein AST-Test legt fest, welche Dienste den Systemkontext verwenden dürfen.
  - HA-Fehler erscheinen nie roh oder englisch: „Home Assistant erlaubt diesem Benutzer diese
    Aktion nicht.“
- **Tests:**
  - Nicht-Admin mit und ohne Option.
  - Die angelegte Push-Automation löst beim richtigen Handy aus.
  - Der Trace enthält den Turn.

### A2 – Namen mit Grußformel sind nicht aktivierbar

- **Befund:**
  - „Aktiviere Guten Morgen.“ → „Guten Morgen kann ich nicht steuern, nur abfragen.“
  - „Aktiviere die Szene Guten Morgen.“ → „Welches **Gerät** meinst du: Abwesend, Filmabend oder
    Guten Morgen?“
- **Soll:**
  - Die Normalisierung entfernt Gruß- und Füllwörter nicht, wenn sie Teil eines im Satz
    vorkommenden freigegebenen Entitäts- oder Aliasnamens sind.
  - Die Rückfrage nennt die Gattung („Welche Szene …“).
  - Ein reiner Gruß ohne Befehlsverb startet weiterhin nichts.

### A3 – Mengen bei relativen Änderungen werden ignoriert

- **Befund:** „Mach die Heizung im Bad zwei Grad wärmer.“ erhöht um 1 Grad.
- **Soll:** Menge × Einheit aus der Bedeutungsebene bestimmt den Schritt für wärmer/kälter,
  heller/dunkler, lauter/leiser und höher/tiefer, in Ziffern und Worten, mit Adjektiv oder
  Richtungspartikel. Gerätegrenzen gelten weiter.

### A4 (Regression) – „oben“ in Anzahl- und Bestandsfragen

- **Befund:** Eine Anzahlfrage nach Rollläden mit „oben“ zählt seit 7.6.0 das ganze Haus.
- **Soll:** `level_is_position` gilt nur bei Zustandsfragen über die Stellung („Sind die
  Rollläden oben?“). Bei „gibt es“, „wie viele“, „welche“ und Befehlen mit Ort bleibt
  „oben“/„unten“ die Etage. Test mit beidem: „Sind oben alle Rollläden unten?“

### A5 – Irreführende Antwort bei nicht freigegebenen Geräten

- **Befund:**
  - „Starte den Saugroboter.“ → „Im Haus gibt es keinen Staubsauger.“
  - „Schalte die Kaffeemaschine ein.“ → Angebot von Entkalken-Button und Leistungssensor.
- **Soll:**
  - Antwort: „Saugroboter ist für HomeIntent nicht freigegeben.“ Für Admins mit Hinweis, wo man
    das ändert.
  - Nebenentitäten eines nicht freigegebenen Geräts sind kein Ersatzziel.
  - Es wird weiterhin nichts ausgeführt.

### A6 – Verschmelzungen „fürs“, „ans“, „ins“, „aufs“, „beim“, „zum“

„Welche Routine nutzt du fürs Schlafen?“ wird nicht verstanden. Diese Formen gehören als
Morphologie in die gemeinsame Normalisierung und gelten damit überall.

### A7 – „Mach alles für die Nacht fertig.“

- **Befund:** Der Satz startet den Dialog zum Anlegen einer Routine.
- **Soll:** Sind Kandidaten oder eine Bindung vorhanden, werden sie wie bei „Ich gehe schlafen.“
  angeboten; das Anlegen nur, wenn es keine gibt. Grammatik: „beim Schlafengehen“.

### A8 – Mehrere Abschwächungspartikel

„Könntest du vielleicht irgendwann mal die Markise einfahren?“ wird als Zustandsfrage
beantwortet. Beliebig viele Abschwächungspartikel in beliebiger Reihenfolge ändern die höfliche
Bitte nicht. Eingebettete Fragen („Kannst du mir sagen, ob …“) bleiben Fragen.

---

# Teil B – HomeIntent 7.7: Architecture Completion & Hardening

Keine neuen Features. **Nicht Teil von 7.7:**
- neue Agenten, V13-Proaktivlogik, neue Produktivitäts- oder Kalenderfunktionen,
- neue Oberflächen ohne Architekturbezug,
- allgemeiner Chatbot, jede Form von LLM.

## B0 – Bestandsaufnahme vor dem ersten Code

Schreibe zuerst `docs/architektur-7.7-bestandsaufnahme.md` mit:
1. **Verantwortungskarte von `conversation.py`** (8440 Zeilen): welche fachlichen Bereiche,
   welche Methoden, welcher Dialogzustand.
2. **Verantwortungskarte von `engine.py`** (5325 Zeilen): Routing, Kompilierung, Legacy,
   Override-Logik.
3. **Verbleibende first-match-Kaskaden** mit ihrer Reihenfolge und den Konflikten, die sie heute
   entscheiden.
4. **Abhängigkeiten der Meaning IR auf Legacy-Interna.** Bekannt:
   `meaning_ir.ground_meaning` importiert `ontology_compiler._clause_meanings`; es gibt weitere.
5. **Verbleibende Sonderwege der Zielauflösung** (`entity_scope`, `mentioned_entities`,
   Friendly-Name-Suchen, Alias-Auflösung außerhalb von `target_resolution`).
6. **Regex-Hotspots** (`SEMANTIC_SENTENCE_PATTERN` je Datei).
7. **Lücken in CI und Live-Tests.**
8. **Die daraus abgeleiteten Wellen.** Die Reihenfolge unten ist ein Vorschlag; ändern darfst du
   sie mit Begründung.

## B1 – Welle 1: Release-Basis

- Live-Testbett **vollständig** grün, einschließlich der Proaktiv-Szenarien (nicht nur der
  Strict-Lauf ohne Proaktiv). Rot bedeutet wieder Regression.
- CI grün auf genau dem Commit; das gilt für jeden späteren Release-Commit erneut. Die Workflows
  laufen bei `push`; startet einer nicht, die Ursache beheben statt „lokal war es grün“.

## B2 – Welle 2: Meaning IR ohne Legacy-Interna

- `ground_meaning` soll keine privaten Funktionen historischer Compiler mehr importieren.
- Was IR **und** ein Compiler brauchen (Klausellesung, Gattungs-/Ortsbestimmung, Mengen, Zeit),
  wird zu **öffentlichen semantischen Primitiven** in `nlu/`. Beide nutzen dieselben Primitive.
- **Zielrichtung:** `LanguageDocument → semantische Analyse → MeaningClause`, danach Grounding,
  Arbitration und Domänencompiler. Nicht: Legacy-Compiler, aus dessen Ergebnis die IR Bedeutung
  rekonstruiert.
- Shadow: Engine-Korpus vorher/nachher, 0 Abweichung.

## B3 – Welle 3: Arbitration vollständig autoritativ, mit Dialogzustand

- **Dialogzustand als Evidenz:** Offene Rückfragen (Routine-Wahl, Wiederholung einmalig/täglich,
  Bestätigung, Zielklärung, Lern-Angebote) fließen als **typisierte Evidenz bzw. Kandidatenquelle**
  in die Arbitration. Der Arbiter verwaltet keine Dialoge; er bekommt nur genug Kontext, damit
  keine äußere Kaskade nötig ist.
- **Umschalten**, jeweils mit Shadow und 0 SAFETY_DRIFT:
  - Automation ↔ zeitversetzter Befehl,
  - Routine ↔ Szenenname,
  - Diskurs-Anschlüsse,
  - gelernte Bindung,
  - direkte Befehle.
- Benachrichtigungen, Kalender und Listen dürfen einen eigenen, dokumentierten Vertrag behalten,
  wenn ihre Bedeutung eigenständig ist; dann mit Begründung im Bericht.
- Der Arbiter entscheidet **nur**, welche Bedeutung gilt. Er führt nicht aus und autorisiert
  nicht.

**Begründung für diese Reihenfolge (vor der Zerlegung von `conversation.py`):** Die Kaskade in
`conversation.py` ist heute das Routing. Würde man die Datei vorher in Controller zerlegen,
entstünde ein neuer Router, der die Kaskade nur kopiert und danach wieder ersetzt werden müsste.
Erst die Arbitration abschließen, dann wird der Arbiter selbst der Router.

## B4 – Welle 4: `conversation.py` und `engine.py` nach Verantwortung zerlegen

- **Grenzen aus dem echten Code ableiten** (B0), nicht nach Zeilenzahl.
- **`conversation.py`** soll am Ende nur noch vier Dinge tun: Eingabe entgegennehmen, Turn-Kontext
  bilden, an Arbitration und fachliche Controller delegieren, Antwort zurückgeben.
- Denkbare Controller (nicht blind übernehmen):
  - Geräte, Automationen, Routinen, Benachrichtigungen, Produktivität/Kalender, Lernen,
    Ziele/Proaktiv, Abfragen.
- Jeder Controller bekommt **nur** die Abhängigkeiten, die er braucht. Kein
  God-Object-`RuntimeData`, keine neue `conversation_helpers.py` mit Tausenden Zeilen.
- **Richtwerte:**
  - `conversation.py` ≤ 2500 Zeilen,
  - kein neues Modul > 1500 Zeilen,
  - `engine.py` ohne Dialogverwaltung, Policy, Dienstaufrufe und Domänen-Sonderlogik.

  Wichtiger als die Zahl: Was übrig bleibt, ist Orchestrierung.
- **Abhängigkeitsrichtung per Architekturtest (verbotene Imports):**

  ```
  HA-Adapter/Conversation → Controller → Bedeutung/Domäne → Grounding → Pläne → Policy/Executor
  ```

  Verboten sind etwa: NLU importiert Conversation, Policy importiert Parser, Zielauflösung
  importiert Controller, Lernen ruft den Executor direkt.
- **Verhalten unverändert:**
  - Engine-Korpus und Arbiter-Shadow gleichwertig,
  - Testsuite und Live-Testbett vollständig grün,
  - Latenz nicht schlechter.

## B5 – Welle 5: Alte Pfade löschen

- Nach sauberem Shadow und Live-Test werden alte Pfade **gelöscht**, nicht „zur Sicherheit“
  behalten. Das gilt für
  - die first-match-Zweige,
  - `resolve_entity_scored` als In-Code-Vergleichsresolver,
  - Override-Logik.
- Vergleiche gegen alte Stände laufen über Git-Worktrees (wie `island_shadow.py --root`); dafür
  muss kein Legacy-Code im Baum bleiben.
- Die Architekturtests verhindern die Wiedereinführung.

## B6 – Welle 6: Satzmuster weiter abbauen

- Nur `SEMANTIC_SENTENCE_PATTERN` wird abgebaut. LEXICAL, MORPHOLOGICAL und STRUCTURAL bleiben
  legitim.
- Ersatz durch Tokens, Phrasentabellen, Wortklassen, Sprechakt, Modalität, Satzstruktur,
  Ontologie und IR.
- Zielwert **< 180** (heute 212), aber nur ohne Funktionsverlust. **Umklassifizieren zählt
  nicht:** Jede Änderung der Klasse eines unveränderten Musters muss im Bericht einzeln
  begründet sein.

## B7 – Welle 7: EffectGraph-Härtung

Zusätzlich zu den bestehenden 40 Fällen prüfen und testen:
- **Skript-Parameter:**
  - `fields` und `script.turn_on` mit `variables`,
  - Ziele aus Variablen → `complete = False`.
- **Generische Dienste:** `homeassistant.turn_on/turn_off/toggle` und Ziele, die selbst Gruppen
  sind, jeweils rekursiv expandiert.
- **Szenen- und Automationsdienste:**
  - `scene.create` und `scene.apply` mit Entitäten im Datenblock,
  - `automation.trigger` mit `skip_condition`.
- **Weitere Ziele im Datenblock:** Dienstdaten mit weiteren `entity_id`-Feldern.
- **Mutationstest als Architekturregel** (Test existiert, als ausdrückliche Regression
  beibehalten):
  1. Das Skript enthält nur Licht; HomeIntent fragt nach.
  2. Das Skript wird auf `lock.unlock` geändert.
  3. Der Nutzer sagt „Ja“.
  4. Erwartung: neue Policy-Entscheidung (CRITICAL), keine Ausführung ohne passende Bestätigung.
- **Grundsatz:** Was nicht sicher statisch bestimmbar ist, ergibt `complete = False` und wird
  fail-closed behandelt. Keine Heuristik stuft Unbekanntes als LOW ein.

## B8 – Welle 8: Sprach-Benchmark und STT (Entwicklungswerkzeug)

- **Großer Entwicklungs-Benchmark** mit ≥ 500 Äußerungen, nicht aus Unit-Tests kopiert.
  - **Kategorien:** direkte Befehle, Fragen, Bedürfnisse, Automationen, Benachrichtigungen,
    Kalender/Timer, Mehrturn, Ellipsen, Umgangssprache, STT-Fehler, Negation, Vergangenheit,
    Hypothetisches, Höflichkeit, Mehrfachbefehle, Mehrdeutigkeit, adversarial.
  - **Granular bewertet:**
    - `speech_act`, `operation`, `target`, `place`, `quantity`, `value`, `time`, `condition`,
      `recipient`,
    - `clarification`, `confirmation`, `no_write`,
    - `unsafe_execution_count`, **immer 0**.
  - **Fehlerklassen:** UNDERSTANDING, GROUNDING, AMBIGUITY, CAPABILITY, DIALOG, POLICY,
    RESPONSE, TEST_EXPECTATION, SAFETY. Nicht jeder Fehler wird mit einer neuen Sprachregel
    beantwortet.
  - Einen Teil vor jeder Nachbesserung zurückhalten und getrennt berichten.
  - **Ehrlich einordnen:** Ein Benchmark, den die umsetzende Session selbst schreibt, ist ein
    **Entwicklungswerkzeug und kein unabhängiger Nachweis.** Die unabhängige Messung macht
    weiterhin die Test-Session mit einem unveröffentlichten Korpus. Die README nennt beide Werte
    getrennt.
- **STT-Korpus:** so, wie Home-Assistant-STT tatsächlich liefert.
  - Kleinschreibung, fehlende Satzzeichen,
  - getrennte bzw. zusammengezogene Komposita („küchen licht“, „roll laden“, „wohn zimmer“),
  - kleine Lautfehler, Füllwörter („äh“).
- **Selbstkorrekturen** („…, äh nein, das im Flur“; „um sieben, ach nein, um halb acht“):
  - Nur umsetzen, was die bestehende Korrektur- und Strukturanalyse sauber trägt.
  - Sonst Rückfrage, nie raten und nie beide Teile ausführen.

## B9 – Welle 9: Sicherheits-Härtung und Release-Gate

- Property-Suite um Invarianten erweitern:
  - STT-Varianten ändern die Sicherheitsform nie,
  - Selbstkorrekturen führen nie beide Teile aus,
  - Dialogzustand im Arbiter senkt nie die Bestätigungspflicht.
- **Lernen bleibt getrennt von Autorisierung:**
  - Keine Bindung, kein Alias, kein Makro und keine Standardauswahl umgeht Freigabe, Fähigkeit,
    Risiko, Bestätigung, EffectGraph, Nur-Admin oder NEVER_AUTO.
  - V11 lernt Gewohnheiten, Zeiten und Reaktionszeiten, aber **nie**
    „dieser unbekannte Satz bedeutet vermutlich X“.
- **Release-Gate 7.7:**

  | Gate | Anforderung |
  | --- | --- |
  | `unsafe_execution_count` | 0 |
  | SAFETY_DRIFT je Umschaltung | 0 |
  | Property-Suite | 0 Verletzungen |
  | Pyright (voll und strict) | 0 |
  | HACS, hassfest, echte HA-Tests, Live-Testbett (inkl. Proaktiv) | grün |
  | Latenz p95 bei 5000 Entitäten | < 100 ms, nicht schlechter als 7.6 |
  | Satzmuster | < 180, wenn sinnvoll erreichbar |
  | Entwicklungs-Benchmark | ≥ 500 Fälle |
  | Unabhängiger Nachtest (Test-Session) | nicht schlechter als 7.6.0 |

## B10 – Abschlussbericht

`docs/architecture-completion-7.7.md`:
- Ausgangslage und Bestandsaufnahme,
- je Welle: Ziel, Dateien, alte und neue Pfade, Shadow-Messung, Tests, Sicherheit, Leistung,
  bewusst Unverändertes,
- Aufteilung von `conversation.py`/`engine.py` vorher/nachher,
- Meaning IR und Arbitration vorher/nachher,
- gelöschte Legacy-Pfade,
- Regex-Verlauf, EffectGraph-Härtung, Benchmark-Ergebnisse, CI, verbleibende Schulden und
  offene Sprachlücken.

**Erfolgskriterium:** Ein Entwickler kann jede der folgenden Fragen mit **einer** Stelle im Code
beantworten:
- Wo entsteht die Bedeutung?
- Wo wird das Ziel aufgelöst?
- Wer entscheidet zwischen zwei Deutungen?
- Wer autorisiert?
- Wo wird geschaltet?
- Warum wurde etwas ausgeführt?

Einen Pull Request nur erstellen oder aktualisieren, wenn der Nutzer das verlangt.
