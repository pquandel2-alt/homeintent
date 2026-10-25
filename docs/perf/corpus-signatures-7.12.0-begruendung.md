# Korpus-Signaturen 7.12.0

Die Baseline umfasst 4.191 Satzsignaturen. Gegenüber 7.11.0 wurden alle 4.153
gemeinsamen Einträge geprüft; **keine bestehende Signatur hat sich geändert**.

Die 38 neuen Digests stammen aus neuen Test-Docstrings und vier zusätzlichen,
versionierten Mehrturn-Benchmarkfällen. Sie belegen Slot-Filling, Korrektur,
explizite optionale Bedingungen und fail-closed Irrealis-Verhalten. Bestehende
Korpusfälle, Erwartungen und Kategorien wurden weder gelöscht noch umgedeutet.

Erzeugung und Gegenprüfung:

```bash
python scripts/corpus_shadow.py --check docs/perf/corpus-signatures-7.11.0.json
python scripts/corpus_shadow.py --write-baseline docs/perf/corpus-signatures-7.12.0.json
python scripts/corpus_shadow.py --check docs/perf/corpus-signatures-7.12.0.json
```
