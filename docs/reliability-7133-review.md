# Zuverlässigkeitskorrekturen nach der unabhängigen Prüfung von 7.13.3

Dieser Bericht dokumentiert den lokalen Prüfabschluss **vor** dem anschließenden
Auftrag zur Veröffentlichung als 7.13.4. Commit-/Push-/Release-Aussagen im
Bericht beziehen sich auf diesen Zeitpunkt. Den späteren Freigabestand und die
commitgenauen Remote-Nachweise dokumentiert der
[Release 7.13.4](https://github.com/pquandel2-alt/homeintent/releases/tag/v7.13.4).
Die Rohprotokolle unter `artifacts/reliability/` liegen lokal und werden nicht
mit privaten Umgebungspfaden veröffentlicht.

Die beschriebenen funktionalen Gegenbeispiele sind korrigiert. Auf dem
abschließenden Arbeitsstand bestehen **10656 Tests**, zwölf bestehende Fälle
bleiben übersprungen; zusätzlich bestehen **26 echte HA-Komponententests**.
Die allgemeine Performance-Abnahme bleibt offen: Das 100-ms-p95-Gate ist auf
Arbeitsstand und Baseline rot. Es gibt keinen Release- oder externen CI-Nachweis.

## Ausgangslage

Arbeitsbasis: `main`, HEAD `1685d951d2fe2ce0027e4cf76b5ef07140793ed2`.
Letzter erreichbarer Release-Tag: `v7.13.3`, Commit
`0fb881c9e00959bedeb97ce0e3733887b3b8235e`. Zwischen diesen Commits unterscheiden
sich ausschließlich README und vier Dokumentationsdateien. Der Produktcode
entsprach damit unverändert dem geprüften Release.

Das bereitgestellte Arbeitsverzeichnis war leer. Das Repository wurde dort
frisch geklont; es wurden keine vorhandenen Änderungen überschrieben. Weder im
Repository noch in den übergeordneten Arbeitsverzeichnissen lag eine
`AGENTS.md`. Gelesen wurden insbesondere die Architekturgrenzen 7.12.0,
Qualitätscheckliste, Umsetzung 7.13.3, pytest-Konfiguration und CI-/Nightly-Jobs.
Die beiden optionalen unabhängigen Review-Dateien unter
`/tmp/homeintent-independent-7133/` waren vorhanden und wurden gelesen.

Die unabhängige Suite wurde unverändert als
`tests/test_review_regressions_7133.py` übernommen. Vor der ersten
Produktänderung: **12 fehlgeschlagen, 19 bestanden**. Alle zwölf Gegenbeispiele
waren somit auch auf dem aktuellen Stand nachweisbar. Es wurden keine alten
Tests entfernt, übersprungen oder deren Assertions geändert. Drei bestehende
positive Conversation-Tests verlangten bestätigten Erfolg beziehungsweise
verfügbares Undo, verwendeten aber ausschließlich einen Aufrufrekorder ohne
Geräterückmeldung. Ihre Setups simulieren jetzt ausdrücklich die angeforderten
Licht-/Cover-Zustandsmeldungen; Eingaben und Assertions bleiben unverändert.
Zusätzliche Tests mit fehlender Rückmeldung sichern die gegenteilige Aussage
„angenommen, Wirkung unbestätigt“ ab. Die V11-/V12-Testadapter stellen ihren bereits
vorhandenen Zustandsbestand jetzt auch über `hass.states.get` bereit, sodass
Executor und Verifier dieselbe simulierte Welt prüfen.

Bereits korrekt vorhandene Schutzmechanismen wurden weiterverwendet: Die
neun unbekannten Quellenvarianten verwarfen schon vorher den gesamten
Mehrbefehl einschließlich späterem „Ja“; die neun gültigen Quellen-/Pause-
Varianten hatten bereits korrekte Ziele und Reihenfolge. Auch der exakte
Music-Assistant-Quellenvertrag bestand. Diese 19 Ausgangstests bleiben grün;
sie wurden nicht durch eine neue Routingarchitektur ersetzt. Keines der fünf
beanstandeten Problemfelder war auf dem geklonten Stand vollständig behoben.

## Befunde und strukturelle Korrekturen

| Feld | Ausgangsbefund | Korrektur |
| --- | --- | --- |
| 1. Mehrturn-Verständnis | In der Conversation-Pipeline reproduziert | Die vorhandene Medien-Satzverbrauchsprüfung gilt auch vor Player-/Typ-/Instanzauswahl und Ortskorrektur. Ergänzungen besitzen keinen freien Quellen-Slot, der einen unbekannten Rest verschlucken könnte. Zeitbedingungen, Negation und zusätzliche unverstandene Klauseln autorisieren keine Sofortaktion. Ablehnung verwirft den Entwurf. Ein vollständiger neuer Geräteauftrag verwendet den bestehenden Router; eine MA-Pronomenfortsetzung behält ihren ursprünglichen Auftrag. |
| 2. Gemeinsame Vorprüfung | In der Conversation-Pipeline reproduziert | `async_preflight_service_plan` extrahiert die fachlichen Prüfungen aus der einzigen Schreibgrenze. Sämtliche Klauseln werden vor dem ersten Write geprüft, auch nach „Ja“. Unmittelbar vor jedem Aufruf wird erneut geprüft. Unabhängige Geräte innerhalb eines einzelnen Batches behalten ihre bisherigen Regeln. |
| 3. Teilfehler / Fail-fast | In der Conversation-Pipeline reproduziert | Direkte und bestätigte Mehrbefehle verwenden dieselbe Sequenzmethode. Das finale Batch-Ergebnis bestimmt die Antwort und den Abbruch weiterer Klauseln. Angenommene Aufrufe ohne bestätigte Wirkung werden als solche bezeichnet. |
| 4. Dialoggedächtnis | In der Conversation-Pipeline reproduziert | Die vorhandene `DialogTurnMemory` hält Absicht, Plan, Owner, ausstehende Bestätigung und typisiertes Ausführungsergebnis getrennt. Das Ergebnis enthält Versuch/Annahme/Fehler und die vorhandene Wirkungsevidenz. Ortskorrekturen behaupten keinen pauschalen früheren Start. |
| 5. Klauselvalidator | Nur im isolierten Modul nachgewiesen | Zielbeschreibungen werden unabhängig nach Ort, Gerätetyp und Quantor aufgelöst; die Medienauflösung bestimmt den zulässigen Dienst und die Parameter. Vorgeschlagene Pläne müssen exakt dazu passen. Zusätzliche ungebundene Pläne werden zurückgewiesen. Kein Beleg dafür, dass der normale Sprachpfad zuvor den eingespeisten falschen TV-Plan erzeugte. |

Vorprüfung umfasst Freigabe, Existenz/Verfügbarkeit, bekannte Fähigkeiten,
Zielparameter, Policy, Bestätigungsscope und statisch bekannte transitive
Einschränkungen. Mit der realen HA-Service-Registry werden Dienstexistenz und
registriertes Voluptuous-Schema schreibfrei geprüft; HA-Benutzerrechte werden
für direkte und effektive Ziele geprüft. Derselbe Baustein läuft an der
Schreibgrenze und in der zielweisen Batch-Vorprüfung. Das bestehende
EffectGraph-/Policy-Verhalten für dynamische und unbekannte Effekte bleibt
maßgeblich.

Die Auswahl-/Korrekturprüfung nutzt Wort-/Feldabdeckung und Satzsemantik,
keine Liste der beanstandeten Beispielsätze. Operation, Quelle, Medientyp und
Queue-Modus bleiben typisierte Felder des ursprünglichen Requests. Owner,
Conversation-Scope, Ablaufzeiten und die bisherigen Bestätigungsgrenzen
bleiben erhalten. Nicht sicher unterstützte zeitversetzte Medienfortsetzungen
werden ohne sofortige Ersatzaktion zurückgewiesen.

## Undo und verwandte Pfade

Der spezielle Undo-Fall war im Ausgangsreview **nicht verifiziert**. Die neuen
Tests prüfen ihn sowohl direkt als auch nach Bestätigung: Nach einem
abbrechenden Teilfehler eines Mehrbefehls bleibt kein Undo- oder Restplan
aktivierbar. Es wird bewusst keine Rücknahme für einen solchen Teilzustand
angeboten. Das verhindert insbesondere Rücknahmen nie ausgeführter oder
später fremd geänderter Aktionen in diesem Pfad. Es wird keine physische
Transaktion und kein automatischer Rollback behauptet.

Bei erfolgreich abgeschlossenen, vollständig beobachteten und reversiblen
Sequenzen bleibt der vorhandene Undo-Pfad bestehen. Einzeln ausgeführte
unabhängige Batches verwenden die bestätigten Erfolgs-IDs für ihre
Undo-Beschränkung. Eine zusätzliche Conversation-Reproduktion zeigte, dass
solche Rücknahmen eine spätere Änderung an einem erfolgreichen Ziel
überschreiben konnten: Der neue Test scheiterte vor der Undo-Korrektur mit
zwei unerwünschten Rücknahmeaufrufen. Diese Rücknahmen sind jetzt an den
beobachteten Zustand inklusive Attributen, Änderungszeit und Kontext gebunden;
vor Beginn und unmittelbar vor jedem inversen Aufruf wird erneut verglichen.
Fehlende Evidenz bietet kein Undo an. Die positive Kontrolle bestätigt, dass
unveränderte erfolgreiche Ziele weiterhin rückgängig gemacht werden können. Die Automation-Undo-Architektur samt Owner-/Admin-, Digest-
und Lifecycle-Grenzen wurde nicht umgebaut.

Zusätzlich korrigiert wurden:

- Ein isolierter Vorprüfungstest zeigte eine abweichende Fähigkeitsprüfung
  statisch bekannter Skriptwirkungen: `supported_features=0` wurde indirekt
  als unbekannt statt ausdrücklich leer behandelt. Direkte und transitive
  Ziele verwenden jetzt denselben Baustein. Vorher: eine negative Kontrolle
  fehlgeschlagen, positive Kontrolle bestanden (`transitive-capability-before.txt`).
- Bestätigte Mehrbefehle betrachteten vorher nur das letzte Ergebnis und
  prüften nachfolgende Klauseln nicht gemeinsam vor dem ersten Write.
- Bestätigte einzelne Batches und der semantische Gerätecontroller konnten
  ebenfalls vollständige Erfolgstexte trotz Teilfehler liefern; jetzt gilt
  auch dort das finale Batch-Ergebnis. Der semantische Pfad beschränkt sein
  Undo ebenfalls auf belegte erfolgreiche Ziele.
- `play_media` hatte keine quellenspezifische Wirkungserwartung. Bestätigung
  verlangt jetzt `playing` und eine passende `media_content_id`. Eine andere
  bereits laufende Quelle oder eine pausierte passende Quelle genügt nicht.
  Ein bereits laufender Titel bestätigt auch keine Änderung einer künftigen
  Queue (`enqueue=next/add`). Die Quelle eines Players und dessen Wiedergabe
  sind ebenfalls getrennt: Bestätigtes `select_source` wird direkt und nach
  „Ja“ als ausgewählte Quelle gemeldet, nicht als laufende Wiedergabe. Die
  beiden Conversation-Regressionen und der Queue-Wirkungstest scheiterten
  vor dieser Korrektur (`source-evidence-before.txt`, `queue-evidence-before.txt`).
- Mediengedächtnis bleibt nach einer bestätigten Ausführung mit ihrem
  tatsächlichen Ergebnis erhalten; reine Vorschau und Ablehnung bleiben
  davon unterscheidbar.
- Neue nichtmediale Aufträge während einer Medienauswahl verwenden den
  vorhandenen vollständigen Command-Kandidaten und denselben Router.
- Nach „Ja“ konnte die einzelne Medienbestätigung weiterhin eine laufende
  Wiedergabe behaupten. Eine Ortskorrektur während dieser Vorschau verlor
  außerdem das Absichtsgedächtnis. Zwei zusätzliche Conversation-Tests
  reproduzierten diese Übergänge. Die Bestätigung berichtet jetzt die
  Wirkungsevidenz; die Vorschaukorrektur verwendet denselben Medienleser,
  verwirft den alten Bestätigungstask und erhält die Policy-Herkunft.
- Ein vom Dienst angenommener Einzelaufruf konnte trotz anschließend bereits
  gemeldetem `unavailable` spätere Klauseln freigeben. Das wird nun ebenfalls
  als Ausführungsfehler ausgewertet, direkt und nach Bestätigung. Fehlende
  Zustandsrückmeldung bleibt davon getrennt: Sie ist keine positive
  Fehlermeldung und wird nicht als bestätigter Erfolg dargestellt. Zwei weitere
  Conversation-Tests zeigten dieselbe Unterscheidungslücke in den Antworten
  einzelner direkter und bestätigter Medienaufrufe: Ein explizit gemeldeter
  Ausfall wurde als bloß unbestätigte Wirkung formuliert. Antwort und spätere
  Ortskorrektur berücksichtigen jetzt diesen beobachteten Fehler
  (`media-observed-error-before.txt`: zwei Fehler vor der Korrektur).

Die Benchmark-Nachprüfung zeigte zwei veraltete Messadapter. Der Arbiter-
Vergleich beobachtete nur den bisherigen direkten Policy-Aufruf im Controller;
die ausgelagerte gemeinsame Vorprüfung war für ihn unsichtbar. Er beobachtet
jetzt zusätzlich deren tatsächlichen Aufruf, ohne die Auswertung oder die
Grenzwerte zu ändern. Wiederholte Vorprüfungen desselben Planobjekts zählen
nicht als weitere Klausel. Der Benachrichtigungsrekorder des Testhauses ersetzte
außerdem den bisherigen Simulator für Licht-/Cover-Rückmeldungen. Mit korrektem
Fail-fast liefen zwei positive Benchmark-Aufträge deshalb zu Recht nicht
weiter. Der Benchmark fordert diese simulierten Rückmeldungen nun ausdrücklich mit
`simulate_device_effects=True` an, auch bei aktiviertem
Benachrichtigungsrekorder. Die bisherigen Adapter-Defaults bleiben erhalten;
Tests mit ausbleibender oder später Rückmeldung bekommen keine vorzeitigen
synthetischen Erfolgszustände. Neue Tests scheiterten vor beiden
Adapterkorrekturen; keine Benchmark-Erwartung oder Qualitätsgrenze wurde geändert.

## Betroffene Dateien

| Bereich | Dateien |
| --- | --- |
| Medien und Dialogzustand | `controllers/media_commands.py`, `controllers/insights.py`, `controllers/replies.py`, `conversation.py`, `nlu/context.py` |
| Vorprüfung und Auswertung | `controllers/devices.py`, `service_executor.py`, `effect_wait.py` |
| Unabhängige Bindung | `nlu/clause_binding.py` |
| Rücknahme | `undo.py` |
| Neue Regressionen | `tests/test_review_regressions_7133.py`, `tests/test_reliability_semantics.py`, `tests_ha/test_service_preflight.py`, `tests/test_reliability_benchmark_instrumentation.py` |
| Zustandsmeldungen in positiven Testfixtures | `tests/test_conversation_integration.py`, `tests/test_conversation_semantic_dialog.py`, `tests/v12_harness.py`, `tests/test_v11_action_evidence_semantics.py`, `tests/_testhaus.py` |
| Benchmark-Instrumentierung | `scripts/arbiter_shadow.py`, `scripts/dev_benchmark.py` |
| Dokumentation und lokale Prüfkonfiguration | `README.md`, `docs/architecture-7.12.0.md`, `docs/quality-checklist.md`, dieser Bericht, `docs/except-exception-inventory.json`, `.github/workflows/nightly-live.yml` |

Produktpfade in dieser Tabelle beziehen sich auf `custom_components/homeintent/`.
Die vollständige Pyright-Prüfung verwendet auch den `strict`-Dateiscope der
Projektkonfiguration; V11, V12 und Learning Center haben zusätzliche eigene
Konfigurationen. Das Exception-Inventar enthält ausschließlich aktualisierte Zeilennummern;
Anzahl, Klassifizierung und geprüfte Handler sind unverändert.

## Tests und Nachweise

Die neue Matrix prüft direkte Aufträge, Ergänzungen und Ortskorrekturen,
Abbruch und späteres „Ja“, negative/zeitliche/unbekannte Zusätze, alle neun
geforderten Verbindungsformen, bekannte/unbekannte Quellen, mehrere Räume,
verfügbare/unavailable Ziele, Laufzeitfehler, drei Klauseln mit einem
Lichtbatch, direkte/bestätigte Ausführung, Dienstschema/Berechtigung,
Music-Assistant-Player-/Typauswahl und Queue-Erhalt. Hypothesis variiert
fremde/zusätzliche Ziele, Parameter und Klauselverbinder am unabhängigen
Validator. Positive Kontrollen bleiben Bestandteil der Tests.

Die neuen Suiten sind zusätzlich im vorhandenen Nightly-Sicherheitsjob
aufgeführt. Diese Workflow-Änderung ist lokal; sie belegt keinen CI-Lauf.

Der vollständige Testbestand enthält bereits zehn `skip`-Markierungen in
`test_automation_condition_parser.py` und `test_command_followup.py`. Gründe
sind dort noch nicht vorhandene YAML-Grammatikvarianten, die DEVICE-/STATE-
Zuordnung und nicht fertiggestellte Verschachtelung. Diese bestehenden
Markierungen ergeben zwölf übersprungene parametrisierte Fälle und wurden
unverändert gelassen; es gibt keine neu übersprungenen
Regressionen. Vollsuite umfasst auch alle `test_event_runtime*.py`,
`test_storage_paths_async.py`, `test_undo.py`, `test_cover_batch_7131.py` und
`test_cover_safety_7132.py`. RuntimeWarning und PytestUnraisableExceptionWarning
werden bei den abschließenden pytest-Läufen als Fehler behandelt.

Prüfumgebung: Python **3.12.3** mit den unveränderten Pins aus
`requirements-dev.txt`; für die parallele Vollsuite zusätzlich
`pytest-xdist==3.8.0`. Die getrennte echte HA-Umgebung verwendet Python
**3.14.2**, Home Assistant **2026.9.2** und `requirements-ha-test.txt`.
Die Tests verwenden zusätzlich Hypothesis für variierte Ziel-/Parameterpläne.
Die vollständigen Befehlsprotokolle liegen unter `artifacts/reliability/`.
`commands.tsv` hält Exitcode und Dauer der seriellen Prüfschritte fest.

| Prüfung | Ergebnis / Protokoll |
| --- | --- |
| Unveränderte Ausgangsbasis mit unabhängigen Tests | 12 fehlgeschlagen, 19 bestanden; `before.txt`, nochmals auf isoliertem HEAD bestätigt in `baseline-reproduced.txt` |
| Unabhängige Regressionen, neue Semantikmatrix, semantischer Dialog | Zwischenstand: 93 bestanden; `regressions-verified.txt` |
| Abschließende unabhängige Regressionen und Semantikmatrix | 97 bestanden (31 unabhängige, 66 zusätzliche Fälle); `regressions-delivery.txt` |
| V11-/V12-Dateien nach Bereitstellung derselben Zustände für den Executor | 400 bestanden; `adapter-retest.txt` |
| Wirkungsmeldungen, Bestätigungstöne, Benchmark-Instrumentierung | 378 bestanden; `effect-adapter-final.txt` |
| Vollsuite vor den abschließenden Medien-Wirkungstests | 10649 bestanden, 12 bestehende Fälle übersprungen; `full-final-current.txt` |
| Vollsuite auf abschließendem Arbeitsstand | **10656 bestanden, 12 bestehende Fälle übersprungen**, Exit 0; `full-delivery.txt` |
| Echte HA-Komponenten einschließlich Registry-/Schemaprüfung | 26 bestanden; `ha-delivery.txt` |
| Sprachgate, gepinnte Abhängigkeiten | 463 bestanden; `language-pinned.txt` |
| Sicherheitsproperties und damalige Semantikmatrix, Seed 7133 | 102 bestanden; `safety.txt`; Properties und erweiterte Matrix zusätzlich in der finalen Vollsuite |
| Pyright gesamt und V11-/V12-/Learning-Strict | Je 0 Fehler; `pyright-delivery.txt`, `pyright-v11.txt`, `pyright-v12.txt`, `pyright-learning.txt` |
| Pyflakes einschließlich Test- und Prüfskripten | Bestanden; `pyflakes-delivery.txt` |
| Exception-Inventar | Bestanden; 120 Exception-/2 BaseException-Handler, keine neue breite Fehlerbehandlung; `exceptions-delivery.txt` |
| Entwicklungskorpus | Gate bestanden: 478/516 Fälle, 0 unsichere Ausführungen; gleicher Gesamtwert wie Baseline, `dev-delivery.txt`, `dev-baseline.json` |
| Entwicklungskorpus 7.8 | Gate bestanden: 103/107 Fälle, 0 unsichere Ausführungen; `dev-benchmark78-final.txt` |
| Identischer Shadow-Kandidat | 2131/2131 äquivalent, 0 Safety Drift; `shadow.txt` |
| Arbiter-Vergleich | Gate bestanden: 2161 Sätze, 2156 äquivalent, 5 Behavior Changes, 0 Safety Drift, 0 RuntimeWarnings; `arbiter-delivery.txt` |
| Versionierte Korpussignaturen | 4336 Sätze, davon 4310 mit Baseline, 0 geänderte Signaturen; `corpus.txt` |
| EventRuntime-Reproduktion mit Warning-Gate | Bestanden, 0 RuntimeWarnings; `event-runtime.txt` |
| Media-Coverage-/Clause-Binding-Latenz bei 5000 Entities | p95 31,08 / 33,91 / 80,90 ms, alle unter 100 ms; `media-performance.txt` |
| Music-Assistant-Auflösung bei 5000 Entities | p95 9,137 ms unter 100 ms; `ma-performance.txt` |
| Allgemeines Pipeline-Latenzbudget | Nicht bestanden, auch auf unveränderter Baseline überschritten; `pipeline-baseline.txt`, `pipeline-final.txt` |
| Echter Music-Assistant-Komponentenvertrag | Schema-Vertrag für HA 2026.9.2 bestanden, kein MA-Server; `music-assistant-contract.txt` |

Die folgenden relevanten Reproduktions- und Prüfkommandos wurden im Repository ausgeführt. Für den Vollsuite-
Fehlerrekorder ist `PYTHONPATH=artifacts/reliability:tests:custom_components`
gesetzt; er protokolliert lediglich pytest-Fehler und verändert keine Tests.
Das Sprachskript wird mit `.venv/bin` am Anfang von `PATH` ausgeführt.

```sh
.venv/bin/python -m pytest -q tests/test_reliability_semantics.py tests/test_review_regressions_7133.py -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning --tb=short
.venv/bin/python -m pytest -q tests/test_reliability_semantics.py tests/test_review_regressions_7133.py tests/test_conversation_semantic_dialog.py -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning --tb=short
.venv/bin/python -m pytest -q tests/test_reliability_benchmark_instrumentation.py tests/test_tone_793.py tests/test_effect_wait_792.py -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning --tb=short
.venv/bin/python -m pytest -q -n 4 --dist loadfile -rs -p report_failures -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning --tb=short --durations=10
/tmp/homeintent-ha-venv/bin/python -m pytest -q tests_ha -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning --tb=short
bash scripts/run_language_eval.sh
.venv/bin/python -m pytest -q tests/test_safety_properties.py tests/test_reliability_semantics.py --hypothesis-seed=7133 -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning --tb=short
.venv/bin/python -m pyright
.venv/bin/python -m pyright --project pyrightconfig-v11-strict.json
.venv/bin/python -m pyright --project pyrightconfig-v12-strict.json
.venv/bin/python -m pyright --project pyrightconfig-learning-center-strict.json
.venv/bin/python -m pyflakes custom_components tests tests_ha scripts
.venv/bin/python scripts/exception_inventory.py --check
.venv/bin/python scripts/dev_benchmark.py --check --json artifacts/reliability/dev-delivery.json
.venv/bin/python scripts/dev_benchmark.py --corpus tests/eval/dev_benchmark_78.txt --check
.venv/bin/python scripts/shadow_compare.py --candidate identity --check
.venv/bin/python -W error::RuntimeWarning scripts/warning_gate.py scripts/arbiter_shadow.py --check --output artifacts/reliability/arbiter-delivery.json
.venv/bin/python scripts/corpus_shadow.py --check docs/perf/corpus-signatures-7.13.3.json
.venv/bin/python -W error::RuntimeWarning scripts/warning_gate.py scripts/event_runtime_repro_798.py
.venv/bin/python scripts/benchmark_media_coverage.py --registry-size 5000 --iterations 30 --warmup 5 --max-p95-ms 100
.venv/bin/python scripts/benchmark_music_assistant.py --registry-size 5000 --iterations 300 --max-p95-ms 100
.venv/bin/python scripts/benchmark_v6_baseline.py --pipeline understand --scales 5000 --iterations 20 --warmup 3 --max-p95-ms 100 --max-coordination-ratio 6.5
/tmp/homeintent-ha-venv/bin/python scripts/music_assistant_contract.py
```

Die abschließenden zusätzlichen Fehlerreproduktionen liefen mit:

```sh
.venv/bin/python -m pytest -q tests/test_reliability_semantics.py -k media_observed_failure -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning --tb=short
.venv/bin/python -m pytest -q tests/test_reliability_semantics.py -k transitive_empty -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning --tb=short
.venv/bin/python -m pytest -q tests/test_reliability_semantics.py -k 'transitive_empty or media_observed_failure' -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning --tb=short
```

Die letzte Kombination besteht nach der Korrektur mit vier Tests
(`last-boundaries-after.txt`). Die umfassende abschließende Suite enthält
auch diese Fälle. Bei der neuen Fehlerantwortprüfung wird der Aufruf von
`IntentResponse.async_set_error` beobachtet: Der vorhandene leichte HA-Stub
setzt zwar `error_code`, aktualisiert aber anders als HA nicht `response_type`.
Die Produktlogik wird dabei nicht ersetzt und der gemeinsame Stub nicht verändert.

Die Baseline-Reproduktion verwendet denselben Python-3.12-Interpreter mit
`-m pytest -q tests/test_review_regressions_7133.py -W error::RuntimeWarning
-W error::pytest.PytestUnraisableExceptionWarning --tb=short` im isolierten
Checkout von `1685d951d2fe2ce0027e4cf76b5ef07140793ed2`. Dort wurde ausschließlich
die unveränderte unabhängige Testdatei ergänzt. Der Pipeline-Vergleich läuft
mit denselben Argumenten und demselben Interpreter nacheinander auf Baseline
und Arbeitsstand.

Der abschließende Gesamtlauf dauerte unter zwischenzeitlich extremer
Rechnerlast 7750,29 Sekunden (2:09:10). Seine Laufzeit ist kein verwertbarer
NLU-Latenznachweis; dafür gilt der gesonderte, weiterhin rote Budgetvergleich.

Abgebrochene oder überholte Zwischenläufe sind keine grünen Gates. Der erste
vollständig beendete Gesamtlauf enthielt 49 Fehler der oben beschriebenen
noch nicht mit `hass.states` verbundenen V11-/V12-Adapter, 10596 bestandene
und zwölf bereits zuvor übersprungene Fälle. Alle betroffenen Dateien wurden
anschließend zusammen erneut geprüft (400 bestanden). Weitere Zwischenläufe
enthalten ausdrücklich reproduzierte Fehler vor deren Korrektur.

Das allgemeine 100-ms-p95-Latenzgate bleibt **rot**. Bei 5000 Entities lagen
im unmittelbar aufeinanderfolgenden Vergleich die höchsten p95-Werte bei
199,94 ms auf der unveränderten Basis und 267,03 ms auf dem Arbeitsstand.
Für Mehrbefehle: Baseline Mittelwert 103,25 ms / p95 157,58 ms, Arbeitsstand
113,23 ms / p95 267,03 ms. Der Rechner war gleichzeitig stark ausgelastet;
die Streuung betrifft auch unveränderte Lese- und Relationspfade. Das belegt
weder ein eingehaltenes Budget noch die Abwesenheit einer Performance-Regression.
Eine kontrollierte Messung auf einem unbelasteten Runner steht aus. Es wurden
keine Budgets gelockert. Der Entwicklungskorpus besteht sein konfiguriertes
Gate, aber nicht alle 516 Einzelbeispiele; seine 38 Abweichungen und die fünf
Arbiter-Behavior-Changes sind kein Beweis vollständiger Sprachabdeckung.

## Grenzen und Lieferstatus

Deterministische deutsche Sprachverarbeitung, kein Laufzeit-LLM und keine
zweite Dialog- oder Ausführungsarchitektur. Ein später auftretender Ausfall
kann bereits angenommene Hardwareaktionen nicht zurücknehmen. Änderungen
zwischen der letzten Vorprüfung und dem HA-Aufruf sind weiterhin möglich;
der Executor prüft erneut, HA setzt seine eigenen Dienstrechte/Schemata durch.

Simulierte Serviceaufrufe und echte HA-Komponentenprüfungen sind keine
Hardwaretests. Reale Sonos-/Music-Assistant-Wiedergabe, physische Lampen und
Cover sowie ein echter Music-Assistant-Server wurden nicht geprüft.
Das vollständige Langzeit-Live-Testbett (`sim/runner.py --strict --full-reset`)
wurde ebenfalls nicht gestartet: Es wurde hier die getrennte HA-Komponenten-
Testumgebung eingerichtet, kein laufender HA-Simulationsserver samt Szenarien.
Die komplette Python-/HA-Versionenmatrix der externen CI wurde lokal nicht
nachgebaut; maßgeblich sind die oben genannten Interpreter und Pins. Manche
Player melden keine vergleichbare Medien-ID; deren Wirkung bleibt korrekt
unbestätigt. Zeitversetzte Medienfortsetzungen sind weiterhin nicht sicher
unterstützt. Für abbrechende Mehrbefehle wird kein partielles Undo angeboten.

Die SHA-256-Dateiliste `artifacts/reliability/validation-source.sha256` hält
die geprüften Änderungen an Produkt, Tests, Prüfskripten und Workflow fest.
Zusammen mit dem genannten Basiscommit beschreibt sie den lokalen Prüfstand;
HEAD allein enthält diese Änderungen noch nicht. `validation-provenance.json`
und `validation-results.json` ergänzen die maschinenlesbaren Nachweise.
`changes.patch` enthält die vollständigen Produkt-, Test-, Workflow- und
Dokumentationsänderungen, ohne die temporären Prüfartefakte.

Die Änderungen liegen lokal im Arbeitsbaum. Kein Commit, kein Push, keine
externe Review-Anfrage, kein neuer Tag und keine Veröffentlichung. Für diesen
Arbeitsstand gibt es keinen externen CI-/Nightly-Nachweis. Historische grüne
Release-Läufe dürfen ihm nicht zugerechnet werden.

## Nachprüfung während der freigegebenen Veröffentlichung

Nach der ausdrücklichen Freigabe zum Push und Release wurde der Arbeitsstand
als `98b0e69b286143f73fe2472c9385a01ff9dc7955` auf `main` gepusht. Die obigen
Angaben zum noch uncommitteten Arbeitsbaum beschreiben die davor abgeschlossene
lokale Prüfphase. Ein Release wurde auf diesem Commit **nicht** veröffentlicht:

- [CI 42528577037](https://github.com/pquandel2-alt/homeintent/actions/runs/42528577037):
  alle zehn übrigen Pflichtjobs erfolgreich, einschließlich vollständiger
  Testsuiten, statischer Prüfungen und des unveränderten 100-ms-Latenzgates;
  Live-Testbett 220/221 Szenarien.
- [Nightly 42528485996](https://github.com/pquandel2-alt/homeintent/actions/runs/42528485996):
  Sicherheitsinvarianten erfolgreich, Live-Testbett 230/231 Szenarien.

Beide Live-Läufe scheiterten an `n791-b-partial`: Bei „Schalte die Stehlampe
und das Flurlicht ein“ wurde wegen der unerreichbaren Stehlampe auch das
erreichbare Flurlicht nicht geschaltet. Die neue strenge Vorprüfung hatte eine
gemeinsame Geräteauswahl wie mehrere unabhängige Prädikate behandelt. Das war
eine Regression gegenüber der ausdrücklich beizubehaltenden Batch-Regel.
Der Live-Test und seine Erwartungen bleiben unverändert.

Der kompositionelle Parser weist das gemeinsame Prädikat bereits nach.
`CommandPlan.shared_predicate` bewahrt diesen Nachweis bis zum Controller.
Nur gleiche Operationen und Parameter auf gemäß bestehender
`batch_eligibility` unabhängigen Zielen werden zum vorhandenen Batch-Executor
weitergereicht. Validator, Policy, Bestätigung, Einzelziel-Vorprüfung,
Ergebnisreduktion und Undo laufen durch die bestehenden Pfade. Getrennte
Prädikate und nicht unabhängige Geräte behalten die strenge gemeinsame
Vorprüfung. Es wird kein Satz anhand seiner Schreibweise sonderbehandelt.

Reproduktion im separaten Checkout von `98b0e69`: Zwei neue Regressionen
(direkte Ausführung und bestehender Policy-Bestätigungspfad) scheitern;
die Kontrolle mit getrennten Prädikaten besteht. Nach der Korrektur bestehen
125 gezielte Tests, einschließlich aller unveränderten unabhängigen
Review-Regressionen. Exakte Befehle:

```bash
# Vorher im Checkout 98b0e69, nur die neue Testdatei ergänzt:
python -m pytest -q tests/test_reliability_semantics.py -k 'shared_predicate_keeps or separate_predicates_still' -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning --tb=short
# Nachher im Arbeitsstand:
.venv/bin/python -m pytest -q tests/test_reliability_semantics.py tests/test_review_regressions_7133.py tests/test_batch_execution.py tests/test_composition.py tests/test_semantic_interpreter.py -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning --tb=short
```

Geänderte Dateien dieser Nachkorrektur: `engine.py`,
`controllers/devices.py`, `tests/test_reliability_semantics.py` und diese
Dokumentation samt Release-Notizen. Das Exception-Inventar wurde ausschließlich
für die verschobene Zeilennummer des unveränderten Undo-Handlers aktualisiert.
Zusätzlich bestanden 600 bestehende Cover-, Batch-, Undo- und Tonprüfungen:

```bash
.venv/bin/python -m pytest -q tests/test_cover_safety_7132.py tests/test_cover_batch_7131.py tests/test_batch_settle_7131.py tests/test_undo.py tests/test_tone_793.py -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning --tb=short
```

Ein erster Aufruf enthielt irrtümlich `tests/test_cover_safety_713.py` und
endete ohne Testausführung (Exit 4); oben steht der korrigierte, erfolgreiche
Aufruf. Der Korpusvergleich umfasst nach den Dokumentationsergänzungen
4340 Sätze, davon 4310 mit Baseline, ohne geänderte Signaturen. Der
Arbiter-Vergleich meldet 2154 äquivalente Fälle, sieben Verhaltensänderungen
und keine Sicherheitsabweichung. Pyright meldet null Fehler und null
Typwarnungen. Pyflakes und Exception-Inventarprüfung
bestehen ebenfalls (unverändert 120 `Exception`-, zwei `BaseException`-Handler).

```bash
.venv/bin/python scripts/corpus_shadow.py --check docs/perf/corpus-signatures-7.13.4.json
.venv/bin/python scripts/arbiter_shadow.py --check
.venv/bin/pyright
.venv/bin/python -m pyflakes custom_components tests tests_ha scripts
.venv/bin/python scripts/exception_inventory.py --refresh
.venv/bin/python scripts/exception_inventory.py --check
```

Die Veröffentlichung erfordert erneut
vollständig erfolgreiche CI- und Nightly-Läufe auf dem korrigierten Commit;
die erfolgreichen Teilprüfungen von `98b0e69` ersetzen diese nicht.
