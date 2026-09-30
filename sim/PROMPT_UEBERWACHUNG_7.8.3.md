# Umsetzungsauftrag HomeIntent 7.8.3: Überwachungsaufträge und Benachrichtigung

Du arbeitest am Repository `pquandel2-alt/homeintent` (Home-Assistant-Integration
„HomeIntent“, deutschsprachiges, vollständig lokales Sprachverständnis).
Ausgangsstand ist **7.8.2** auf dem Zweig
`claude/homeintent-phases-7-7-1-7-8-ls99gi` (Commit `8388105`).

## Ziel

HomeIntent soll natürliche Überwachungsaufträge verstehen und daraus eine
Überwachung mit Push-Benachrichtigung machen, zum Beispiel:

- „Überwache das Garagentor und melde dich, wenn es länger als 10 Minuten offen ist.“
- „Beobachte die Fenster und warne mich, wenn eins offen ist und niemand zuhause ist.“

Langfristig soll HomeIntent nicht nur Sprachbefehle ausführen, sondern Werte und
Zustände überwachen und melden, wenn etwas auffällig ist. Dieser Auftrag legt das
Fundament: **Ein Überwachungsauftrag hat genau eine Bedeutung, egal wie er
formuliert ist.**

## Harte Regeln (nicht verhandelbar)

1. **Kein LLM, kein ML-Modell, kein probabilistisches Raten.** Alles bleibt lokal
   und deterministisch.
2. **Keine Satzlisten.** Erkannt werden wiederverwendbare Konstruktionen:
   - Überwachungsverben
   - Benachrichtigungsverben
   - Konnektoren
   - Pronomen- und Gruppenbezüge
   - Dauer-Modifikatoren
   - Zustand oder Moment

   Einzelne Hardcode-Sonderfälle für Beispielsätze sind verboten.
3. **Sicherheitsgrenze bleibt:** Grounding/Resolver → Validator → gesprochene
   Vorschau → ausdrückliches „Ja“ → Schreiben. Nichts wird vor der Bestätigung
   geschrieben.
4. **Nie raten, sondern nachfragen.** Das gilt bei:
   - unklarem Bezug („er“ passt nicht zum Garagentor),
   - unbekanntem Gerät,
   - Geräten verschiedener Art unter einem Nomen (Fensterkontakte **und**
     Fensterantriebe).
5. Die zentrale Sicherheitssperre für verneinte oder unsichere Befehle in
   `conversation.py` wird **nicht** umgangen oder aufgeweicht.
6. **Tests werden nicht abgeschwächt.** Gegebenenfalls darf eine bestehende
   Test-Erwartung angepasst werden. Das geht nur mit einer Begründung im Test
   selbst, die eine echte Bedeutungsänderung benennt, und nur in Teil B.
7. Keine Modellnamen in Commits, Code, Kommentaren oder Dokumenten.
8. Die versteckten Holdout-Korpora aus den Testsessions werden nicht verwendet
   oder nachgebaut.

---

## Teil A – Fertige Patches einspielen (Überwachungsaufträge + Zustandskombination)

Die Testsession hat Teil A bereits auf 7.8.2 implementiert und vollständig
geprüft:

| Prüfung | Ergebnis |
|---|---|
| Gesamtsuite | 6549 bestanden, 12 übersprungen |
| `run_language_eval.sh` | 463/463 |
| Korpus-Signaturen | 0 Änderungen |
| Shadow-Vergleich | 0 Sicherheitsabweichungen |
| Arbiter-Vergleich | 0 Sicherheitsabweichungen |
| Dev-Benchmarks | 0 unsichere Ausführungen |
| Latenz Automationssprache bei 5000 Entitäten | p95 30 ms |
| pyright, pyflakes | 0 Fehler |

Die Patches liegen im Zweig `claude/sleepy-meitner-xd7oux` unter `sim/patches/`.

```bash
git fetch origin claude/homeintent-phases-7-7-1-7-8-ls99gi claude/sleepy-meitner-xd7oux
git switch claude/homeintent-phases-7-7-1-7-8-ls99gi   # bzw. dein Arbeitszweig darauf
mkdir -p /tmp/p && for f in 0001-ueberwachungsauftraege.patch 0002-zustandskombinationen.patch; do
  git show origin/claude/sleepy-meitner-xd7oux:sim/patches/$f > /tmp/p/$f; done
git am /tmp/p/0001-ueberwachungsauftraege.patch /tmp/p/0002-zustandskombinationen.patch
```

**Lies die Patches trotzdem vollständig und prüfe sie kritisch.** Falls die
Basis inzwischen abweicht und `git am` scheitert, implementiere Teil A anhand der
folgenden Beschreibung neu. Übernimm die Tests aus
`tests/test_monitoring_automation_783.py` aus dem Patch.

### A.1 Befund (Ursache vor 7.8.3)

Benachrichtigungsverben („melde dich“, „sag/gib mir Bescheid“, „informiere“,
„benachrichtige“, „warne mich“) wurden bereits einheitlich auf NOTIFY
normalisiert (`notification_language.parse_notification_clause`). Die Sätze
scheiterten an anderen Stellen:

| Ursache | Wirkung |
|---|---|
| **Keine Überwachungsverben.** Bei „Überwache X und melde dich, wenn …“ ist der Teil vor dem Konnektor keine reine Benachrichtigung. `segment_event_automation` fand keine Aufteilung. | „Ich konnte Trigger und Aktion der Automation nicht eindeutig erkennen.“ |
| **Kein Konnektor.** „Achte darauf, ob …“ hat weder „wenn“ noch ein Benachrichtigungsverb. | „Das habe ich nicht verstanden.“ |
| **Keine Anaphern-/Gruppenauflösung.** „es“, „sie“ und „eins“ blieben als Subjektwort übrig. | Gerät nicht gefunden |
| **Dauerangaben nicht stapelbar.** Bei „seit mehr als 20 Minuten“ blieb „seit“ als Subjektrest übrig. | „kein passendes Gerät für Fenster“ |
| **„nachts“ als Gerätename gelesen,** nicht als Zeitfenster-Bedingung. | Haustür nicht gefunden |

### A.2 Architektur (was die Patches bauen)

- **Neu: `automation_monitoring.py` – `segment_monitoring()`.** Die Stufe
  läuft **vor** `segment_event_automation` in `compose_event_automation` und
  liefert nur Quelltext-Spannen plus eine typisierte Referenz.
  - `MONITOR_HEAD`: überwache/beobachte NP; behalte/hab NP im Auge|Blick; hab ein
    Auge auf NP; achte/pass auf NP; „kannst du NP überwachen/beobachten/im Auge
    behalten“.
  - `MONITOR_HEAD (und|,) REST`: REST wird mit dem bestehenden
    `segment_event_automation` gelesen. Die NP wird zum Antezedens.
  - `MONITOR_HEAD|CLAUSE_HEAD [,] ob|wenn|sobald|falls EVENT`: implizites NOTIFY an
    den Sprecher.
  - `CLAUSE_HEAD [,] dass VERBOT [, wenn|solange|während BEDINGUNG]`: Das Ereignis
    ist der Verstoß. „kein“ bedeutet: irgendein Element der Menge.
  - Ein positives Ziel („dass das Tor zu ist“) ergibt keinen Frame und keine
    Zustandsumkehr.
  - „prüfe/kontrolliere, ob …“ sind einmalige Abfragen und bewusst **keine**
    Überwachungsverben.
- **`automation_language.py`:**
  - `EventReference` und `resolve_reference()`: „es/er/sie“, „eins/eines
    davon/welches“ oder ein fehlendes Subjekt werden an die Wörter des
    Antezedens gebunden. Ein eigenes Subjekt im Wenn-Satz hat Vorrang.
  - `EventRoles.reference` und `EventRoles.stative`.
  - Gestapelte Dauer-Modifikatoren in `_DURATION_RE`.
  - Zeitfenster („nachts“, „abends“, „zwischen 22 und 6 Uhr“) werden in
    `_extract_conditions` zur Bedingung. Gelesen werden sie vom bestehenden
    Bedingungsparser.
- **`automation_grounding.py`:**
  - „eins/irgendeins“ als Quantor ANY.
  - Genus-Kongruenz der Anapher: „er“ passt nicht zu „Garagentor“, das ergibt die
    Rückfrage „Worauf bezieht sich ‚er‘?“.
  - Ein unbekanntes überwachtes Objekt wird benannt.
  - Zustands-Kandidaten aus mehreren Domains (Sensoren + Antriebe unter
    „Fenster“) führen zu einer Rückfrage mit den Geräte-Arten. Vorher wurden sie
    still zu einem Auslöser gemischt.
- **`automation_composition.py`:**
  - Frame-Auswahl und implizites NOTIFY (`implicit_notification_reading`).
  - Zusatzbedingungen (`extra_conditions`), die nie still verworfen werden.
  - **`state_conjunction()`**: Zwei mit „und“ verbundene **Zustände** („ein
    Fenster offen ist und niemand zuhause ist“) gelten, egal welcher Teil zuletzt
    eintritt. Das ergibt:
    - je Teil einen Auslöser, also „Fenster geht auf“ sowie „Person X verlässt
      das Haus“ für jede Person;
    - alle Teile als Bedingung: irgendein Fenster offen als OR über einzelne
      State-Conditions (eine Liste in einer HA-State-Condition hieße „alle“),
      dazu „niemand zuhause“.

    Ein **Moment** („geöffnet wird“), eine **Dauer** („seit 20 Minuten offen“) und
    ein **Zeitfenster** bleiben wie gesprochen.
- **`notification_language.describe_holding_state()`** und
  **`AutomationModel.situation`**: Die Vorschau sagt „Sobald ein Fenster offen
  ist und niemand zuhause ist, egal was davon zuletzt eintritt, sende ich dir …“
  statt einer Auslöserliste. Die Push-Nachricht lautet „Ein Fenster ist offen.
  Niemand ist zuhause.“
- `docs/regex-klassifikation.json` ist neu erzeugt; die Frame-Regexe sind manuell
  als STRUCTURAL eingestuft. `automation_monitoring.py` steht in
  `pyrightconfig.json`.

### A.3 Bekannte Grenzen nach Teil A

Nichts davon wird ausgeführt, alle Fälle scheitern sicher:

- **„Pass auf, dass keiner die Haustür öffnet.“** Die Negations-Sicherheitssperre
  lehnt ab. Das ist korrekt und wird nicht umgangen.
- **„Überwache die Haustür und schließ sie ab, wenn sie offen ist.“** Das
  Pronomen in der **Geräteaktion** wird nicht aufgelöst. Optionaler Punkt,
  siehe C.3.

---

## Teil B – Eine Bedeutung für Überwachungsaufträge (Routing vereinheitlichen)

### B.1 Befund

HomeIntent hat drei Stellen, die „überwachen und melden“ betreffen:

1. **Automationspfad** (`engine.match_automation` → `compose_event_automation`,
   Teil A): satzbasiert. Das Ergebnis ist eine Home-Assistant-Automation.
2. **V10-Monitor-Goals** (`goal_intent._monitor_goal` →
   `controllers/goals.py`, `MonitorGoalStore`): Die Erkennung arbeitet mit
   **losen Stichwort-Mengen** (`_NOTIFY`, `_WINDOW`, `_OPEN`, `_LIGHT`, „niemand“ +
   „zuhause“). Die Goals werden in `conversation.py` **vor** dem Automationspfad
   gefragt (`self._goals.async_handle_goal_turn`).
3. **V12 proaktive Situationserkennung** (`situation_detection.py`,
   `proactive_runtime.py`): meldet Auffälligkeiten ohne Auftrag. Sie ist nicht
   Teil dieses Auftrags, darf aber nicht brechen.

Folge: Paraphrasen bekommen **unterschiedliche Bedeutungen**, nachgeprüft im
Testhaus (`tests/_testhaus.py`):

| Satz | Pfad | Auslöser |
|---|---|---|
| „Sag mir Bescheid, wenn ein Fenster offen ist und **keiner** zuhause ist.“ | Automation | Fenster geht auf **oder** alle gehen (korrekt, Teil A) |
| derselbe Satz mit **„niemand“** | Goal | **nur** wenn alle gehen, nicht wenn bei leerem Haus ein Fenster aufgeht |
| „… und **warne** mich …“ | Automation | „warne“ fehlt in `_NOTIFY` |
| „Sag mir Bescheid, wenn das Garagentor offen ist und niemand zuhause ist.“ | Goal | Im Testhaus Ablehnung „kein bestätigter Haushalt“. Der Garagen-Scope (`cover` mit `device_class="garage"`) erfasst außerdem keine Garagentor-Kontakte (`binary_sensor` mit `garage_door`). |

Die Projektregel „Paraphrasen haben dieselbe Bedeutung“ (siehe
`tests/test_automation_paraphrase_invariance.py`) ist damit verletzt.

### B.2 Umsetzung

**Grundsatz:** Die **Bedeutung** eines Überwachungsauftrags kommt aus genau einer
Quelle, dem satzbasierten Leser aus Teil A (kanonische Bedeutung
`CanonicalEventNotification`). Wo der Auftrag **läuft** (HA-Automation oder
HomeIntent-Goal-Runtime), ist eine nachgelagerte Entscheidung und darf die
Bedeutung nicht verändern.

1. **Routing-Regel in `conversation.py`:** Bevor `_monitor_goal` einen
   Überwachungsauftrag übernimmt, prüfst du den Satz mit dem Automationsleser
   (`NluEngine.compose_event_automation` bzw. `_compose_with_run_limits`).
   - Liefert der Leser ein vollständiges Ergebnis (`OutcomeKind.AUTOMATION`) oder
     eine gezielte Rückfrage zu Geräten (`CLARIFY` mit Grounding-Grund), gewinnt
     der Automationspfad.
   - `_monitor_goal` behält nur Sätze, die der Leser nicht versteht. Beispiel:
     „Wenn ich gehe und noch Licht an ist, sag mir Bescheid.“ ist ein Moment
     („gehe“), der an die Person des Sprechers gebunden ist. Prüfe, ob der Leser
     das bereits typisiert kann. Falls ja, gilt dieselbe Regel.
   - Die Regel ist strukturell. Sie darf keine Wortliste enthalten, die
     Goal-Sätze „durchlässt“.
2. **`interpret_goal` bleibt als Funktion unverändert.** Die Tests in
   `tests/test_v10_ood_metamorphic.py` prüfen `interpret_goal` direkt und bleiben
   grün.
   - Brechen Tests auf Konversationsebene, weil ein Satz jetzt als Automation
     statt als Goal angelegt wird, passe die Erwartung nur an, wenn die neue
     Bedeutung **mindestens so vollständig** ist (beide Reihenfolgen statt nur
     „alle gehen“).
   - Schreibe die Begründung in den Test. Alles andere ist ein Fehler im neuen
     Code.
3. **„Niemand zuhause“ = Haushalt.** Der Goals-Pfad verlangt einen bestätigten
   Haushalt (`household_person_ids`), der Automationspfad nimmt heute alle
   `person.*`. Vereinheitliche das an **einer** Stelle, sodass beide Pfade
   dieselbe Personenmenge nutzen:
   - Mit bestätigtem Haushalt: genau dessen Personen, sowohl für die Bedingung
     als auch für die Auslöser „verlässt das Haus“.
   - Ohne Haushalt: alle `person.*`. Die Vorschau nennt die Personen dann
     ausdrücklich („Anna, Lena und Philipp“), damit der Nutzer es beim „Ja“ sieht.
   - Personen ohne `person.*`-Entität gibt es nicht: nie raten, sondern
     nachfragen.
4. **Paraphrasen-Invarianztest (neu):** Ein generierter Test, keine Satzliste.
   Er bildet Kombinationen aus diesen Bausteinen:
   - Benachrichtigungsverb: sag/gib Bescheid, melde dich, informiere,
     benachrichtige, warne,
   - Abwesenheitswort: niemand, keiner, „niemand mehr“,
   - Satzform: mit Überwachungsverb, ohne, Wenn-Satz vorn, Wenn-Satz hinten,
   - Objekt: Fenster, Garagentor, Licht an.

   Für alle Kombinationen muss dieselbe kanonische Bedeutung entstehen:
   dieselben Auslöser-Entitäten, Bedingungen und Empfänger.
5. **Fehlermeldung „kein bestätigter Haushalt“** kommt nur noch, wenn wirklich
   kein einziges `person.*` existiert. Der Text sagt dann, was der Nutzer tun kann.

### B.3 Nicht Teil von B

- Die Goal-Runtime selbst (Zustellung, Nachverfolgung „Warum kam gestern keine
  Meldung?“) bleibt unverändert.
- Ob Überwachungen künftig standardmäßig als HA-Automation oder als Goal laufen,
  entscheidet der Projekteigentümer später. Baue nichts, was diese Entscheidung
  vorwegnimmt. Nur die **Bedeutung** wird vereinheitlicht.

---

## Teil C – Tests, Live-Test, Release

### C.1 Pflicht-Regressionssätze

Diese Sätze sind im Patch-Test enthalten und müssen weiter grün sein:

```
Überwache das Garagentor und melde dich, wenn es länger als 10 Minuten offen ist.
Beobachte das Garagentor und sag mir Bescheid, wenn es 5 Minuten offen steht.
Achte darauf, ob das Garagentor länger als 15 Minuten geöffnet bleibt.
Warne mich, wenn das Garagentor länger als 10 Minuten offen ist.
Benachrichtige mich, sobald das Garagentor geöffnet wird.
Beobachte die Fenster und warne mich, wenn eins offen ist und niemand zuhause ist.
Sag mir Bescheid, wenn irgendein Fenster offen ist und keiner zuhause ist.
Achte darauf, dass kein Fenster offen bleibt, wenn niemand zuhause ist.
Informiere mich, wenn ein Fenster seit mehr als 20 Minuten offen ist.
Behalte die Haustür im Auge und melde dich, wenn sie nachts geöffnet wird.
```

Dazu kommen die Negativ- und Mehrdeutigkeitsfälle aus dem Patch:

- **Rückfrage statt Automation:**
  - Pronomen ohne Bezug,
  - Pronomen mit falschem Genus,
  - unbekanntes Objekt,
  - Fensterkontakte + Fensterantriebe.
- **Keine Automation:**
  - „prüfe/kontrolliere, ob …“ (einmalige Abfrage),
  - „Überwache das Garagentor.“ ohne Ereignis,
  - positives Ziel („dass das Tor zu ist“).

Nach Teil B gilt zusätzlich: Der „niemand“-Satz aus B.1 erzeugt dieselbe
Automation wie der „keiner“-Satz. Entferne dafür im Patch-Test den Kommentar
„keiner: the V10 monitor-goal route claims …“ und teste **beide** Wörter.

### C.2 Gates (alle müssen grün sein, vgl. `.github/workflows/ci.yml`)

```bash
bash scripts/run_language_eval.sh
python scripts/corpus_shadow.py --check docs/perf/corpus-signatures-7.8.2.json   # bzw. neue Baseline 7.8.3, begründet
python scripts/shadow_compare.py --candidate identity --check
python scripts/dev_benchmark.py --check
python scripts/dev_benchmark.py --corpus tests/eval/dev_benchmark_78.txt --check
python scripts/arbiter_shadow.py --check
python scripts/benchmark_automation_language.py --registry-size 5000 --max-p95-ms 100
python scripts/benchmark_v6_baseline.py --pipeline understand --scales 5000 --iterations 20
python scripts/regex_inventory.py --write   # neue Regexe klassifizieren, Frame-Regexe STRUCTURAL (manuell)
python -m pyright && python -m pyflakes custom_components tests tests_ha scripts
python -m pytest -q
```

Wenn sich Korpus-Signaturen durch Teil B ändern:
- Erzeuge eine neue Baseline `docs/perf/corpus-signatures-7.8.3.json`.
- Stelle `ci.yml` darauf um.
- Liste jede geänderte Signatur mit Begründung im Release-Dokument.
- `SAFETY_DRIFT` ist nie zulässig.

### C.3 Live-Testbett (`sim/`)

Ergänze in `sim/scenarios.py` Live-Szenarien, die **die Wirkung** prüfen und
nicht nur die Vorschau:

1. „Beobachte die Fenster und warne mich, wenn eins offen ist und niemand zuhause
   ist.“ + „Ja“ legt die Automation an. Dann über `haus_sim`:
   - Küchenfenster auf, danach alle Personen `not_home` → genau **eine**
     Push-Nachricht an das Handy des Sprechers;
   - alle Fenster zu, alle gehen → **keine** Nachricht;
   - Haus leer, dann Fenster auf → eine Nachricht.
2. „Überwache das Garagentor und melde dich, wenn es länger als 1 Minute offen
   ist.“ Das Tor öffnen, 70 s warten, dann muss eine Nachricht kommen. Das
   Szenario gehört in die langsame Kategorie „Proaktiv“, falls die Laufzeit das
   verlangt.
3. Dieselben Sätze als Anna (kein Admin, `allow_non_admin_automations`): Die
   Nachricht geht an Annas Handy.

Dann `sim/fresh_ha.sh`, `runner.py --strict` und `check_log.py` ausführen. Alle
bisherigen Live-Szenarien müssen grün bleiben.

Optional, wenn Zeit bleibt: das Pronomen in einer **Geräteaktion** auflösen
(„Überwache die Haustür und schließ sie ab, wenn …“). Dafür gilt:
- dieselbe Antezedens-Logik,
- dieselbe Genus-Prüfung,
- Schlösser bleiben bestätigungspflichtig wie bisher.

### C.4 Release

- Version **7.8.3** in `manifest.json`.
- Release-Notizen im üblichen Stil des Repos (README-Abschnitt bzw.
  `docs/`-Releasebericht) mit:
  - Befund,
  - Architektur,
  - geänderten Erwartungen samt Begründung,
  - Gate-Ergebnissen,
  - Live-Ergebnis.
- Commit-Nachrichten auf Deutsch, ohne Modellnamen.
- Pushe auf deinen Arbeitszweig. Erstelle keinen Pull Request, wenn der
  Projekteigentümer ihn nicht ausdrücklich verlangt.

## Abschlussbericht (in deiner letzten Antwort)

1. Wurde Teil A per Patch übernommen oder neu gebaut? Welche Abweichungen gab es?
2. Welche Routing-Regel gilt jetzt in Teil B, und wo steht sie (Datei/Funktion)?
3. Tabelle: Satz → Pfad → Auslöser/Bedingungen, vorher und nachher, für die
   Sätze aus B.1.
4. Welche Test-Erwartungen wurden geändert, jeweils mit Begründung?
5. Ergebnisse aller Gates und des Live-Testbetts, jeweils mit Zahlen.
6. Offene Punkte und bekannte Grenzen.
