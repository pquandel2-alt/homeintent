# Auftrag: HomeIntent 7.6.1 – Befunde aus dem Nachtest von 7.6.0 beheben

Repository `pquandel2-alt/homeintent`. Basis ist **7.6.0**, Branch
`claude/homeintent-sprachverstaendnis-phases-6feab4`, Commit `ce9e9e6`. Arbeite dort weiter oder
auf einem eigenen Branch davon. Der Nachtestbericht liegt in `docs/nachtest-7.6.0.md` im Branch
`claude/sleepy-meitner-xd7oux`.

Alle Grundsätze aus `sim/PROMPT_GESAMT.md` gelten weiter:
- kein LLM, deterministisch,
- Parser führen nie Dienste aus,
- Validator, ExecutionPolicy, NEVER_AUTO und der Executor bleiben die einzigen Instanzen, die über
  Ausführung entscheiden,
- keine zweite Pipeline, Bedeutungsschicht oder Zielauflösung,
- Verbesserungen sind Regeln über Bedeutungsbausteine, keine Satzmuster; der Regex-Ratchet darf
  nicht steigen.

Setze alle Punkte in dieser Session um. Jeder Punkt bekommt einen eigenen Commit mit Test. Am Ende:
Version 7.6.1, Changelog/README, alle Tests grün, Push.

## F1 (hoch, Regression) – Nicht-Admins können keine Automationen mehr anlegen

**Befund:** Mit `allow_non_admin_automations: true` sagt Anna (kein Admin) „Benachrichtige mich,
wenn das Küchenfenster aufgeht.“ und bestätigt mit „Ja.“. Antwort: „Fehler beim Erstellen der
Automation: Unauthorized“; es wird keine Automation angelegt. In 7.3.0 funktionierte das.

**Ursache:** `automation_executor.py` ruft `automation.reload` mit `context=call_context()` auf.
Seit 7.3.2 ist das der Kontext mit der Benutzer-ID des Sprechers. `automation.reload` ist in Home
Assistant ein Admin-Dienst.

**Soll:**
- HomeIntent-interne Verwaltungsaufrufe laufen mit einem **Systemkontext ohne `user_id`**, dessen
  `parent_id` der Turn-Kontext ist. So bleibt die Trace-Kette erhalten. Das betrifft
  `automation.reload` und das Anlegen, Ändern und Löschen der eigenen Automationskonfiguration.
  Die Berechtigung hat die HomeIntent-Policy zu diesem Zeitpunkt bereits geprüft
  (`validate_automation_action_targets`, `allow_non_admin_automations`).
- **Gerätewrites** bleiben beim Nutzerkontext.
- Das gehört in `execution_context.py` als eigene, benannte Funktion (z. B.
  `system_context_for_turn()`), nicht als Ausnahme an einzelnen Aufrufstellen. Ein Test regelt,
  welche Dienste sie verwenden dürfen, ähnlich dem AST-Test für `context=`.
- HA-Fehler wie `Unauthorized` erscheinen nie roh oder auf Englisch in einer Antwort, auch nicht
  beim Anlegen von Automationen: „Home Assistant erlaubt diesem Benutzer diese Aktion nicht.“
- **Tests:** Nicht-Admin mit und ohne `allow_non_admin_automations`; Push-Automation wird angelegt
  und löst beim richtigen Handy aus; Trace-Kette enthält den Turn.

## F2 – Namen mit Grußformel („Guten Morgen“) sind nicht aktivierbar

**Befund:**
- „Aktiviere Guten Morgen.“ → „Guten Morgen kann ich nicht steuern, nur abfragen.“
- „Aktiviere die Szene Guten Morgen.“ → „Welches Gerät meinst du: Abwesend, Filmabend oder Guten
  Morgen?“

**Soll:** Die Normalisierung entfernt Gruß- und Füllwörter nicht, wenn sie Teil eines
freigegebenen Entitäts- oder Aliasnamens sind, der im Satz vorkommt. Das ist dieselbe Schutzregel
wie für Namen, die Wörter wie „Licht“ enthalten. Die Rückfrage verwendet das Wort der Gattung
(„Welche Szene meinst du“), nicht „Gerät“.

**Tests:** Szenen und Skripte namens „Guten Morgen“, „Gute Nacht“ und „Hallo Wach“ sind per
Namen, mit und ohne „die Szene“, aktivierbar. Der EffectGraph wird dabei weiter geprüft. Ein
reiner Gruß „Guten Morgen.“ ohne Befehlsverb bleibt ein Gruß bzw. eine Routine-Rückfrage und
startet nichts.

## F3 – Mengen bei relativen Änderungen werden ignoriert

**Befund:** „Mach die Heizung im Bad zwei Grad wärmer.“ erhöht um 1 Grad (22 → 23).

**Soll:** Menge × Einheit aus der Bedeutungsebene („zwei Grad“, „3 Grad“, „um 10 Prozent“,
„etwas“, „deutlich“) bestimmt den Schritt für wärmer/kälter, heller/dunkler, lauter/leiser und
höher/tiefer. Ohne Menge gilt der bisherige Standardschritt. Plausibilitätsgrenzen (Min/Max des
Geräts) greifen weiter.

**Tests:** Zahl in Ziffern und Worten, mit Richtungspartikel („zwei Grad rauf“) und mit Adjektiv
(„zwei Grad wärmer“), Licht in Prozent, Grenze am Maximum.

## F4 (Regression) – „oben“ in Anzahl- und Bestandsfragen

**Befund:** Eine Anzahlfrage nach Rollläden mit „oben“ zählt seit 7.6.0 das ganze Haus, weil
„oben“ nach einer Beschattungsgattung als **Position** gelesen wird. Bei Lichtern und mit
„im Obergeschoss“ stimmt es.

**Soll:** `place_model.level_is_position` gilt nur, wenn der Satz eine **Zustandsfrage über die
Stellung** ist („Sind die Rollläden oben?“, „Ist die Markise unten?“). Bei „gibt es“, „wie viele“,
„welche … sind/gibt es“ und Befehlen mit Ort bleibt „oben“/„unten“ die Etage.

**Tests:** Beide Lesarten, auch mit Ort + Stellung („Sind oben alle Rollläden unten?“ = Etage
Obergeschoss, Stellung geschlossen).

## F5 – Irreführende Antwort bei nicht freigegebenen Geräten

**Befund:**
- „Starte den Saugroboter.“ (Sauger nicht freigegeben) → „Im Haus gibt es keinen Staubsauger.“
- „Schalte die Kaffeemaschine ein.“ (nicht freigegeben) → „mehrere passende Geräte (Kaffeemaschine
  entkalken, Leistung Kaffeemaschine) …“

**Soll:** Existiert ein passendes Gerät, das nur nicht für HomeIntent freigegeben ist, lautet die
Antwort: „Saugroboter ist für HomeIntent nicht freigegeben.“ Für Admins kommt der Hinweis dazu, wo
man das ändert. Zugeordnete Nebenentitäten (Button, Sensor) eines nicht freigegebenen Geräts
werden nicht als Ersatzziel angeboten. Die Freigabeprüfung selbst bleibt unverändert: Es wird
nichts ausgeführt.

## F6 – „fürs“, „ans“, „ins“, „aufs“ in Bindungs- und Routinefragen

„Welche Routine nutzt du fürs Schlafen?“ wird nicht verstanden, „… für schlafen gehen?“ schon.
Verschmelzungen aus Präposition und Artikel gehören als Morphologie in die gemeinsame
Normalisierung und gelten damit überall.

## F7 – „Mach alles für die Nacht fertig.“

Der Satz startet den Dialog zum **Anlegen** einer Routine, statt die vorhandenen Schlafroutinen
bzw. die Bindung anzubieten. Ist für ein Routinekonzept mindestens eine Kandidatin oder eine
Bindung vorhanden, wird sie wie bei „Ich gehe schlafen.“ angeboten. Das Anlegen wird nur
angeboten, wenn es keine gibt. Grammatik: „beim Schlafengehen“.

## F8 – Mehrere Abschwächungspartikel in einer Bitte

„Könntest du vielleicht irgendwann mal die Markise einfahren?“ wird weiterhin als Zustandsfrage
beantwortet. Abschwächungspartikel wie vielleicht, irgendwann, mal, eben, bitte und gerne sind
beliebig kombinierbar. Sie ändern den Sprechakt POLITE-Bitte nicht, egal in welcher Reihenfolge
und Anzahl. Eine eingebettete Zustandsfrage („Kannst du mir sagen, ob …“) bleibt eine Frage.

## F9 – Generalisierung, weitere Bedeutungsklassen

Auf echten ungesehenen Sätzen liegt 7.6.0 bei 51 %. Offen sind diese **Klassen**; die
Testsätze bekommst du bewusst nicht:
- bildhafte Zustände als Bedürfnis: grelle bzw. direkte Sonne, beschlagene Flächen,
  Redewendungen für völlige Dunkelheit,
- verblose Kurzbefehle aus Gattung + Ort + Menge + Richtungspartikel ohne Einheit am Ende sowie
  Gattung + „los“,
- Bedarfsfragen zu Lüfter/Heizung, Sorgenfragen zu einem Gerät (Batterie, Verfügbarkeit, letzter
  Alarm), Anwesenheit je Etage,
- Push-Wünsche ohne Benachrichtigungsverb („… will ich das wissen“, „Meld dich bei <Person>, wenn
  …“) mit Etagen als Ort,
- Absichtserklärungen („ich bin dann weg“, „ich fahr zur Arbeit“) → Vorschlag der gebundenen
  Routine bzw. freundliche Antwort,
- „Tür zu“ bei einem vorhandenen Türschloss → Rückfrage „Soll ich die Haustür abschließen?“
  (kritisch, Bestätigung), nie stillschweigend.

Arbeite wie in Phase 9 mit eigenen Paraphrasen und zurückgehaltenem Teil, und melde beide Werte.

## Nicht anfassen

- `sim/`: Das Testbett wird in der Test-Session angepasst.
- Die gewollten Verhaltensänderungen aus 7.3.3 (Standardstufe `propose`, Rückfrage bei Sonne).
