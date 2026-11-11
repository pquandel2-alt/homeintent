# Umsetzungsbericht – Zuverlässigkeit nach HomeIntent 7.13.4

## Ausgangsstand und Reproduktion

- Branch: `main`
- geprüfter Ausgangscommit und neuester Release-Tag:
  `1f71272386e574a6a88abd0ed6edc28a97ae2d1c` (`v7.13.4`)
- Arbeitsbaum vor Beginn: sauber; das bereitgestellte Verzeichnis enthielt
  zunächst keinen Checkout und wurde deshalb vom angegebenen GitHub-Repository
  geklont.
- Unterschied `v7.13.4..HEAD` vor Beginn: keiner.

Die vorhandenen unabhängigen Dateien
`/tmp/homeintent-independent-7134/ANALYSE-v7.13.4.md` und
`/tmp/homeintent-independent-7134/test_review_regressions.py` wurden gelesen.
Die unveränderte unabhängige Suite reproduzierte sechs Fehler: vier verlorene
Queue-Änderungen sowie Einzel- und Mehrbefehls-Undo nach einer externen
Zustandsänderung. Fünf positive Kontrollen bestanden. Die Reproduktion lief
durch die reale HomeIntent-Conversation-Pipeline mit HA-Service-Doubles.

## Ursachen und Korrektur

### Zustandsgebundenes Geräte-Undo

Der Einzelpfad band den vorbereiteten Plan nur bei einem Batch an einen
Nachzustand. Der Sequenzpfad baute einen neuen `UndoPlan` ohne die beobachteten
Signaturen. Zusätzlich machte `all(())` eine leere Belegliste fälschlich
gültig.

`undo.py` verlangt nun eine exakte Übereinstimmung zwischen Undo-Zielmenge und
Belegmenge. Die Signatur enthält Entity-ID, Zustand, eine tiefe Kopie der
Attribute, `last_updated` und Context-ID. Alle Erzeugungspfade binden nur
belegte erfolgreiche Ziele. Ein Batch verwendet ausschließlich seine
`succeeded_entity_ids`; ein fehlgeschlagenes oder unbestätigtes Ziel gelangt
nicht in den Plan. Einzelaktionen verlangen zusätzlich den beobachteten
Sollzustand. Mehrbefehle übernehmen die Belege in den kombinierten inversen
Plan. Vor dem ersten Write wird der Gesamtplan vollständig geprüft,
unmittelbar vor jedem inversen Aufruf das jeweilige Ziel erneut. Nach einer
ausgeführten inversen Teiloperation wird der lokale Beleg für die nächste
Prüfung aktualisiert. Der Dialogkontext wird vor der Rücknahme gelöscht, sodass ein
zweites „Mach das rückgängig“ keinen alten Plan wiederholt.

Der erste Release-Kandidat zeigte im echten HA-Testbett zusätzlich einen
verzögerten Cover-Fall: Die Antwort durfte bereits die korrekt beobachtete
Fahrtrichtung bestätigen, während die endgültige Position noch nicht erreicht
war. Die Undo-Bindung beobachtet diesen Endzustand nun im Hintergrund, ohne die
Antwort zu verzögern. Aktiviert wird der Plan nur, wenn der Zielzustand exakt
passt und die HA-Context-ID beziehungsweise Parent-Context-ID weiterhin auf
die ursprüngliche Ausführung verweist. Ein fremdes Zustandsereignis bricht die
Beobachtung fail-closed ab. Ein Ausführungs-Token verhindert außerdem, dass
ein später Bericht einen inzwischen ersetzten Dialogkontext verändert.

Das betrifft ausschließlich Geräte-Undo. Die Digest- und Snapshot-Sicherung
von `automations.yaml` wurde nicht verändert.

### Semantische Medienfortsetzungen

Die Coverage erkannte Queue-Wörter, während Playerauswahl und Ortskorrektur den
alten `pending.request` ausführten. Der Default `replace` war nicht von einer
ausdrücklichen Ersetzungsanweisung unterscheidbar.

`MediaRequest.explicit_fields` hält diese Provenienz jetzt typisiert fest.
`merge_media_continuation` übernimmt alte Quelle, Medientyp, Bibliotheksmodus
und Queue-Modus, solange die Antwort sie nicht ausdrücklich ändert. Explizite
Queue-Semantik wird als `replace`, `next` oder `add` zusammengeführt. Der
resultierende Gesamtauftrag läuft erneut durch Coverage, Player-/Quellen-
Auflösung, Music-Assistant-Validierung, Policy und den bestehenden
`ServiceCallPlan`-Executor. Nicht abbildbare Queue-Semantik im normalen
`media_player`-Quellenpfad wird ohne Write abgelehnt.

Die gemeinsamen Strukturparser behandeln `dann`/`danach` weiterhin als
Klauselgrenze, außer wenn innerhalb eines eng begrenzten strukturellen Fensters
eine Queue-Zielphrase folgt (zum Beispiel `danach in die Warteschlange`). So
bleibt die Clause-Binding-Grenze erhalten, ohne erkannte Queue-Semantik als
scheinbaren zweiten Befehl abzutrennen.

### Antwortdetail-Option

`response_detail_level` ist in Config Flow, Konstanten und Übersetzungen
registriert. Die zentrale Darstellung in `controllers/replies.py` kürzt nur
vollständige Erfolge. Das strukturierte Ergebnis bleibt unverändert.

Standard:

- neue Installation: `concise`, entsprechend dem Nutzerwunsch nach kurzen
  Bestätigungen;
- bestehende Installation ohne Schlüssel: `detailed`, damit ein Update keine
  bisherige Antwortform still ändert.

## Betroffene Dateien und Regressionen

Implementierung: `undo.py`, `controllers/devices.py`, `media.py`,
`controllers/media_commands.py`, `controllers/replies.py`,
`controllers/routines.py`, `nlu/coordination.py`,
`nlu/german_structure.py`, `const.py`, `config_flow.py`, Strings und
Übersetzungen. Dokumentation: README, diese Datei,
`quality-checklist.md`, die Regex-/Exception-Inventare und
`release-notes-next.md`.

Neue Regressionen stehen in `tests/test_reliability_714.py` und
`tests/test_undo.py`; der echte Options-Flow wird zusätzlich in
`tests_ha/test_options_flow.py` geprüft. Sie decken Queue-Erhalt und -Änderung,
Negation, Zeitbedingung, unbekannten Rest, Abbruch, Medientypauswahl, den
fail-closed Standard-Medienpfad, Undo ohne Beleg, unverändertes Undo, spätere
Zustands-, Attribut- und Context-Änderung, ein zweites Undo, Antwortmodi,
Optionskompatibilität, Teilfehler und Statusfragen ab. Der verzögerte
Cover-Endzustand sowie der Abbruch bei einem fremden HA-Context sind ebenfalls
direkt geprüft; `sim/scenarios.py::ctx-undo` prüft denselben Ablauf über eine
echte Home-Assistant-Conversation-Instanz.

## Prüfung und Grenzen

Ausgeführt wurden unter anderem:

- unabhängige v7.13.4-Suite plus neue und direkt berührte Regressionen:
  `121 passed`;
- neue Zuverlässigkeitssuite allein: `43 passed`; gemeinsame Queue-/Clause-
  Binding-/Coverage-/Sprachregressionen: `153 passed`;
- Sprach-/Sicherheitsgate: `463 passed`; Corpus Shadow gegen 7.13.4: 4.364
  Sätze, 4.310 Baselines, 0 geänderte Signaturen; finale 7.14.0-Baseline:
  4.367/4.367 Signaturen, 0 Abweichungen; Identity Shadow: 2.131/2.131 äquivalent;
  Arbiter Shadow: 2.161 Sätze, 2.154 äquivalent, 7 erwartete
  Verhaltensänderungen, kein Safety Drift;
- RuntimeWarning-Gates für Arbiter und EventRuntime: jeweils 0 Findings;
- Nightly-Property-Suite mit `HOMEINTENT_HYPOTHESIS_PROFILE=nightly` und Seed
  7134 auf dem finalen Arbeitsstand: `647 passed` in 30:37 Minuten;
- echte Home-Assistant-2026.9.2-/Python-3.14-Suite: `25 passed`; echter
  Music-Assistant-Komponentenvertrag: `real_component_schema=true`, ohne
  Music-Assistant-Server;
- Pyflakes, vollständiges Pyright, v11-/v12-/Learning-Center-Strict,
  Exception-Inventar, JSON, Regex-Inventar und `git diff --check`: grün. Das
  Regex-Inventar bleibt bei 171 semantischen Satzmustern;
- mediennahe Performance-Gates: Music Assistant p95 13,376 ms sowie
  Medienabdeckung 27,95 ms, Write-Completeness 25,64 ms und Clause Binding
  96,60 ms; v10, v11 und v12 ebenfalls grün.

Zentrale exakt ausgeführte Befehle:

```bash
PYTHONPATH=tests:custom_components .venv/bin/python -m pytest -q \
  /tmp/homeintent-independent-7134/test_review_regressions.py \
  tests/test_reliability_714.py tests/test_undo.py \
  tests/test_conversation_integration.py \
  tests/test_conversation_safe_correction.py \
  -W error::RuntimeWarning \
  -W error::pytest.PytestUnraisableExceptionWarning

.venv/bin/python -m pytest -q tests/test_reliability_714.py \
  tests/test_clause_binding_7132.py tests/test_media_sentence_coverage_7131.py \
  tests/test_language_frontend.py -W error::RuntimeWarning \
  -W error::pytest.PytestUnraisableExceptionWarning

HOMEINTENT_HYPOTHESIS_PROFILE=nightly .venv/bin/python -m pytest -q \
  --hypothesis-seed=7134 -W error::RuntimeWarning \
  -W error::pytest.PytestUnraisableExceptionWarning \
  tests/test_safety_properties.py tests/test_media_sentence_coverage_7131.py \
  tests/test_clause_binding_7132.py tests/test_review_regressions_7133.py \
  tests/test_reliability_semantics.py tests/test_cover_safety_7132.py \
  tests/test_issue_38_place_and_targets.py tests/test_media_793.py \
  custom_components/homeintent/house_report.py \
  custom_components/homeintent/report_runtime.py \
  custom_components/homeintent/monitor_edit.py tests/test_reliability_714.py

.venv/bin/python -m pytest -q -W error::RuntimeWarning \
  -W error::pytest.PytestUnraisableExceptionWarning
.havenv/bin/python -m pytest -q tests_ha \
  --ignore=tests_ha/test_music_assistant_contract.py
.havenv/bin/python scripts/music_assistant_contract.py
.venv/bin/python -m pyflakes custom_components tests tests_ha scripts
.venv/bin/python -m pyright
.venv/bin/python scripts/exception_inventory.py --check
.venv/bin/python scripts/regex_inventory.py
```

Die ersten beiden Pytest-Befehle ergaben `121 passed` beziehungsweise
`153 passed`; der Nightly-Befehl `647 passed`. Pyflakes, Pyright und beide
Inventare waren grün.

Vor der Veröffentlichung deckte die erste commitfeste CI einen zusätzlichen
Fehler im echten HA-Testbett auf (`ctx-undo`, verzögertes
`cover.set_cover_position`). Der Kandidat `3f3e270` wurde deshalb trotz grüner
Teiljobs weder getaggt noch veröffentlicht. Nach der Korrektur bestanden die
direkt betroffenen Undo-/Batch-/Conversation-Tests mit `155 passed` und das
isolierte echte HA-Szenario mit `1/1` Szenarien. Die endgültigen CI-/Nightly-
Links werden durch das Release-Gate an den neuen Release-Commit gebunden.

Die vollständige Suite wurde an einem Implementierungszwischenstand bis 100
Prozent ausgeführt: `10692 passed, 12 skipped, 3 failed` in 54:47 Minuten. Ein
Fehler war das anschließend behobene Regex-Inventar. Zwei unveränderte
Effect-Wait-Zeitgrenzen überschritten unter Last ihre Schwellen
(`1,057 s < 0,700 s` und `0,594 s < 0,500 s`). Die gesamte betroffene Datei
und das Regex-Gate bestanden direkt danach isoliert mit `76 passed`. Die
Zeitgrenzen wurden weder erhöht noch Tests abgeschwächt. Nach der abschließenden
Begrenzung des Queue-Phrasenspanns wurden die direkt betroffenen 121 sowie die
gemeinsamen 153 Struktur-/Sprachtests erneut grün ausgeführt; die rund
55-minütige Gesamtsuite wurde danach nicht nochmals vollständig gestartet.

Nicht grün waren außerdem unveränderte lokale Performance-Gates außerhalb der
geänderten Pfade: V9/NLU-5k, Automation Language (p95 111,62 ms bei 100 ms),
Learning-Center-Evidence (p95 732,74 ms) und Event Summary (p95 912,7 ms bei
500 ms). Budgets wurden nicht verändert. Hassfest, die HACS-GitHub-Action und
containerbasierte HA-Smokes waren lokal nicht ausführbar, weil Docker und ein
GitHub-Actions-Runner fehlen. Die erste echte CI auf dem verworfenen Kandidaten
war wegen des genannten Cover-Undo-Szenarios rot. Sie ist kein
Freigabenachweis. Die späteren grünen Freigabenachweise müssen an den
korrigierten exakten Release-Commit gebunden sein und stehen im GitHub-Release.

Reale Music-Assistant-, Lautsprecher- oder Gerätehardware wurde nicht
verwendet. Die HA- und MA-Vertragstests verwenden echte Softwarekomponenten,
die Conversation-Regressionen HA-Service-Doubles. Bereits ausgeführte
Geräteaktionen sind weder atomar noch garantiert rückrollbar; HomeIntent
verweigert nur eine unsichere spätere Rücknahme. Der Release-Kandidat wurde auf
`main` gepusht, aber wegen der roten ersten CI nicht getaggt oder
veröffentlicht. Die Veröffentlichung folgt ausschließlich nach dem separaten,
commitfesten Release-Gate des korrigierten Commits.
