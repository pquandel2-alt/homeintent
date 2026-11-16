# HomeIntent Zuverlässigkeits-Roadmap – überprüfbarer Zwischenstand

Stand: 16. November 2026. Dieser Bericht dokumentiert den geprüften lokalen
Arbeitsstand. Eine Release-Freigabe entsteht ausschließlich durch das grüne
CI-, Nightly- und Release-Gate für exakt denselben Commit.

## Ausgangslage und Arbeitsplan

- Ausgangsbranch: `main`
- Ausgangs-SHA und Release-Tag:
  `4b923581adf0ff4cbf6487bc5987ec129da065cb`, `v7.14.0`
- Der Ausgangsarbeitsbaum war sauber und unterschied sich nicht vom Tag.
- Im Checkout waren weder `AGENTS.md` noch `HOMEINTENT_TODO.md` vorhanden.
- Die beiden optionalen unabhängigen Dateien unter
  `/tmp/homeintent-independent-7140` waren vorhanden und wurden gelesen. Ihre
  externe Reproduktion wurde unverändert gegen den Release-Stand ausgeführt.
- Der Bericht entstand vor Commit, Push und Veröffentlichung. Ob der Stand
  veröffentlicht wurde, ist ausschließlich am commitfesten GitHub-Release
  erkennbar.

Gemessene Kernumfänge am Ausgangsstand und nach der gezielten Extraktion:

| Bereich | Zeilen | Hauptverantwortung |
| --- | ---: | --- |
| `engine.py` | 5.116 | deterministisches Understanding, Automation-Parsing und Ergebnisaufbau |
| `conversation.py` | 2.498 | HA-Conversation-Adapter und reihenfolgensensitive Turn-Orchestrierung |
| `event_runtime.py` | 1.606 | begrenzte Eventqueue, Priorisierung, Degradation und Ereignisverarbeitung |
| `controllers/devices.py` | 1.490 vor Änderung | Gerätevorprüfung, Bestätigung, Ausführung, Wirkung und Undo |
| `controllers/device_undo.py` | 364 nach Extraktion | konsumierter Undo-Zustandsautomat, Teilresultate und Kausalitätsprüfung |
| `controllers/insights.py` | 1.496 | lesende Insights |
| `controllers/automations.py` | 1.494 | Automationsdialoge |
| `controllers/goals.py` | 1.489 | Goal-Orchestrierung |
| `controllers/productivity.py` | 1.293 | Produktivitätsfunktionen |
| `controllers/automation_management.py` | 1.244 | Automationsverwaltung |
| `nlu/automation_slot_dialog.py` | 753 | typisierte Automations-Slotdialoge |

Der phasenweise Plan ist:

1. P0-Undo-Rennfall funktional korrigieren und mit Zustands-, Attribut-,
   Zeit-, Context-, Policy-, Permission-, Service- und Property-Tests härten.
2. Alle 38 Sprachbenchmarkabweichungen fachlich triagieren, bevor NLU-Logik
   oder Erwartungen geändert werden.
3. Bestätigte semantische Lücken in Automation, Mehrturn, Koordination,
   Query-Grounding, Benachrichtigung und STT jeweils über bestehende typisierte
   Modelle beheben.
4. MA-Softwarevertrag erhalten; reale Hardware nur bei tatsächlich
   vorhandenem Testbett behaupten.
5. Erst danach verhaltensneutrale Extraktionen mit Charakterisierungstests.
6. Kandidat und Baseline unter gleicher Last vergleichen und modellbasierte
   Dialogfolgen ergänzen.
7. HA-UX, Übersetzungen, Coverage, Strict Typing, Brand- und Release-Dokumente.
8. Optionale sichere Draft-Persistenz.
9. Strukturierte, datenschutzfreundliche Ablehnungsdiagnose.

Funktionale Änderungen und spätere breite Extraktionen bleiben getrennte
Arbeitsschritte. Die P0-Korrektur wurde lediglich aus dem auf 1.683 Zeilen
angewachsenen `controllers/devices.py` in `controllers/device_undo.py`
extrahiert. Der Controller liegt damit bei 1.418 Zeilen und das vorhandene
Architekturgate bleibt unverändert grün.

## Phase 0 – Befunde und Baseline

| Befund | Einordnung | Nachweis |
| --- | --- | --- |
| Undo übernimmt fremde Zustandsversion während eines vorherigen inversen Aufrufs | reproduziert | externer Test: 1 fehlgeschlagen; beide inversen Writes und falscher Vollerfolg |
| Vorhandene Undo-/Reliability-Suite | bereits behobene ältere Fälle | 230 bestanden |
| Sprachbenchmark | reproduziert | 478/516 Fälle, 573 Turns, Dev 369/399, Held-out 109/117 |
| Sprachsicherheit | bewusst sichere Grenzen erhalten | `unsafe_execution_count=0`, alle vier Safety-Zähler 0 |
| 38 Abweichungen | gemischte Ursachen | vollständige Triage in `docs/perf/dev-benchmark-triage-v7.14.0.json` |
| Einmalige relative Zeit in Benchmark-Zeile 178 | Test-/Fixturefehler | Produkt erzeugt korrekten One-shot-Draft; Benchmark liest TimeKind aus der falschen Ebene |
| Sonnenauf-/untergang ohne Wiederholungsangabe | veraltete Erwartung | Produkt fragt fail-closed „nur heute oder täglich?“ |
| Umgehungszusätze und unsichere Zielwahl | bewusst sichere Ablehnung | kein Write und kein Safety Drift |
| Reale Hardwarewirkung | nicht verifizierbar | kein MA-Server, Lautsprecher oder Geräte-Testbett in dieser Umgebung |
| Externer v7.14.0-GitHub-Release-Link | nicht lokal änderbar ohne Autorisierung | Repository-Links sind relativ korrekt; externe Release-Beschreibung bleibt offen |

Baseline-Host: Python 3.12.3, vier QEMU-CPU-Kerne, 9,4 GiB RAM. Während der
Messung lag die Systemlast bei ungefähr 55 und Swap war nahezu vollständig
belegt. Die lokalen 5.000-Entity-Gates sind deshalb Messwerte, aber kein grüner
Release-Nachweis:

- Music-Assistant-Auflösung: p95 11,888 ms, innerhalb 100 ms.
- Automation Language: p95 116,58 ms, über 100 ms.
- NLU `multi_target`: p95 88,9992 ms.
- NLU `multi_clause_command`: p95 161,5220 ms, über 100 ms.
- NLU `v9_relational_command`: p95 232,3531 ms, über 100 ms.

Budgets und Tests wurden nicht verändert. Diese stark belastete lokale
Messung wird nicht mit unbelasteten historischen CI-Zahlen gleichgesetzt.

## Phase 1 – P0 Undo

Status: funktional umgesetzt; echte HA-Komponenten- und Hardwareprüfung noch
offen.

Ursache war der Aufruf von `bind_undo_to_observed_states()` auf dem gesamten
Undo-Plan nach jedem inversen Teilschritt. Dadurch erhielten auch unberührte
Ziele neue Evidenz aus einem beliebigen aktuellen HA-Zustand.

Die Korrektur führt einen unveränderlichen `UndoRemainder` ein mit:

- verbleibenden inversen Plänen;
- bitgenau erhaltener Originalevidenz;
- nur für kausal fortgesetzte Mehrschrittziele aktualisierbarer Arbeitsevidenz;
- erfolgreich belegten Schritten samt Undo-Execution-ID;
- abgebrochenen und übersprungenen Schritten;
- Benutzerbindung sowie Quell- und Undo-Context-Identitäten.

Vor dem ersten Write werden sämtliche inversen Pläne über den bestehenden
Service-Preflight geprüft. Vor jedem Write folgen Ziel-Evidenz und erneuter
Preflight; die zentrale Executor-Grenze führt unmittelbar vor dem HA-Aufruf
zusätzlich eine read-only Evidenzbedingung aus. Nach dem Aufruf wird nur der
gerade betroffene Zustand gebunden und auf den Execution-Context geprüft.
Jeder inverse Teilschritt erhält einen eigenen Child-Context unterhalb des
Assist-Turns. Verschachtelte Zustandsattribute werden für die Evidenz rekursiv
in unveränderliche, typmarkierte Tupel überführt.
Unberührte Evidenz wird niemals neu aufgebaut. Verzögerte Cover-Endzustände
verwenden den vorhandenen kausalen Observer. Ein Abbruch leert den
Conversation-Plan vor dem ersten Write und lässt keinen Restplan wiederaufleben.

Ergebnisse:

- externe Originalreproduktion plus neue P0-Suite: 15 bestanden;
- verbreiterte Undo-, Batch-, Conversation-, Policy-, Executor- und
  Architektursuite: 277 bestanden;
- Architekturgrenzen separat: 9 bestanden; `controllers/devices.py` liegt
  mit 1.418 Zeilen wieder unter dem unveränderten Limit von 1.500;
- vollständige Suite mit harten Runtime-/Unraisable-Warning-Gates:
  10.721 bestanden, 12 übersprungen in 59:52 Minuten;
- gezielte Pyright-Prüfung der geänderten Module: 0 Fehler, 0 Warnungen;
- vollständiges Pyright sowie V11-, V12- und Learning-Center-Strict: jeweils
  0 Fehler, 0 Warnungen;
- Pyflakes: ohne Befund;
- Exception-Inventar: 119/120 `except Exception`, 2/2 `BaseException`,
  `POTENTIALLY_SILENT=0`; der breite Undo-Handler wurde entfernt;
- Dev-/Held-out-Benchmark nach der Änderung unverändert bei 478/516 und
  `unsafe_execution_count=0`.
- Corpus Shadow: 4.371 Sätze, 4.367 mit Baseline, 0 geänderte Signaturen;
  zwei neue Regressionstexte besitzen noch keine Baseline.
- Identity Shadow: 2.131/2.131 äquivalent, 0 Behavior Changes und
  `SAFETY_DRIFT=0`.

Der wiederholte 5k-Lauf blieb durch die Hostlast volatil: MA verbesserte sich
von p95 11,888 auf 9,021 ms; Automation Language schwankte von 116,58 auf
128,35 ms; `multi_target` von 88,9992 auf 123,5718 ms und
`multi_clause_command` von 161,5220 auf 243,9068 ms. Die geänderten Pfade
liegen nicht in der NLU-Hotpath, die Systemlast blieb bei ungefähr 55 und die
Budgets wurden nicht verändert. Diese Messung ist deshalb ein rotes lokales,
lastbeeinflusstes Gate und kein belastbarer logischer Performancevergleich.

Die Tests verwenden HA-Service-Doubles. Sie belegen keine physische Atomizität
und keine reale Hardwarewirkung. Bereits ausgeführte inverse Geräteaktionen
werden bei einem späteren Abbruch nicht automatisch zurückgerollt; die Antwort
benennt bereits rückgängig gemachte, extern veränderte und übersprungene Ziele
einschließlich der Eigenschaft bei mehrstufigen Klima-/Wert-Rücknahmen. Home
Assistant stellt keinen State-Version-Compare-and-swap-Service bereit: Die
synchrone Prüfung unmittelbar vor dem Dispatch minimiert das TOCTOU-Fenster,
kann aber keine physische Geräteatomizität garantieren.

## Phase 2 – Benchmarktriage

Status: vollständig triagiert, Produktkorrekturen aus Phase 3 noch offen.

Der maschinenlesbare Bericht enthält alle 38 Abweichungen mit Korpuszeile,
Kategorie, Eingabe, bisherigem Ergebnis, erwarteter Semantik, Klassifizierung,
Entscheidung, Modul und geplantem Regressionstest:
`docs/perf/dev-benchmark-triage-v7.14.0.json`.

Verteilung:

- 13 echte Produktfehler;
- 7 fehlende Funktionen;
- 2 Antworttextprobleme;
- 8 sichere und richtige Ablehnungen;
- 5 veraltete Benchmarkerwartungen;
- 2 absichtliche Capability-Grenzen;
- 1 Benchmark-/Testhaus-Instrumentierungsfehler.

Es wurde noch keine Korpuserwartung geändert. Insbesondere bleiben
Umgehungsversuche, unklare Cover-Ziele, mehrere kritische Cover und
nichtsteuerbare Schlösser fail-closed.

## Status der übrigen Phasen

| Phase | Status |
| --- | --- |
| 3 – NLU | bewusst noch nicht umgesetzt; 22 bestätigte Produkt-/Funktions-/Antwortlücken sind einzeln spezifiziert |
| 4 – MA/Hardware | deterministische Radiosender-Auflösung und Softwaretests ergänzt; reale Hardware nicht vorhanden und nicht behauptet |
| 5 – Architektur | breite Zerlegung nicht begonnen; nur die zur Einhaltung des bestehenden 1.500-Zeilen-Gates notwendige Undo-Extraktion ist erfolgt |
| 6 – Performance/Model-based | Baseline erfasst; Abschlussvergleich und Dialogmodelltests offen |
| 7 – HA-Produktreife/UX | nicht begonnen; externer Release-Link ohne Autorisierung nicht geändert |
| 8 – Draft-Persistenz | bewusst nachrangig und nicht begonnen |
| 9 – Diagnose | nicht begonnen |

## Exakte Befehle dieses Zwischenstands

```bash
PYTHONPATH=custom_components:tests .venv/bin/python -m pytest -q \
  -W error::RuntimeWarning \
  -W error::pytest.PytestUnraisableExceptionWarning \
  /tmp/homeintent-independent-7140/test_undo_race.py

.venv/bin/python -m pytest -q \
  -W error::RuntimeWarning \
  -W error::pytest.PytestUnraisableExceptionWarning \
  tests/test_undo_race_715.py tests/test_undo.py \
  tests/test_reliability_714.py tests/test_reliability_semantics.py \
  tests/test_batch_settle_7131.py tests/test_cover_batch_7131.py \
  tests/test_conversation_integration.py \
  tests/test_conversation_action_evidence.py \
  tests/test_execution_policy.py tests/test_service_executor.py \
  tests/test_architecture_layers_77.py

.venv/bin/python -m pytest -q \
  -W error::RuntimeWarning \
  -W error::pytest.PytestUnraisableExceptionWarning

.venv/bin/python scripts/dev_benchmark.py --check
.venv/bin/python scripts/corpus_shadow.py \
  --check docs/perf/corpus-signatures-7.14.0.json
.venv/bin/python scripts/shadow_compare.py --candidate identity --check
.venv/bin/python -m pyflakes custom_components tests tests_ha scripts
.venv/bin/python scripts/exception_inventory.py --check
.venv/bin/python -m pyright custom_components/homeintent/undo.py \
  custom_components/homeintent/controllers/device_undo.py \
  custom_components/homeintent/controllers/devices.py \
  custom_components/homeintent/service_executor.py tests/test_undo_race_715.py
.venv/bin/python -m pyright
.venv/bin/python -m pyright --project pyrightconfig-v11-strict.json
.venv/bin/python -m pyright --project pyrightconfig-v12-strict.json
.venv/bin/python -m pyright \
  --project pyrightconfig-learning-center-strict.json
```

## Noch durch den Release-Commit nachzuweisende Freigaben

Python 3.13, echte HA-Komponententests, MA-Komponentenvertrag, Hassfest, HACS,
HA-Stable-Smoke, Options Flow, Live-Testbett und Nightly-Profil müssen für den
exakten Release-Commit auf GitHub grün sein. Reale Hardware wurde nicht geprüft
und wird nicht behauptet. Kein früherer grüner Commit gilt als Nachweis für
diesen Arbeitsstand.
