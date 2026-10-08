# Korpus-Signaturen 7.9.4 – Begründungsliste

Neue Baseline `corpus-signatures-7.9.4.json` (ersetzt `corpus-signatures-7.9.3.json`
im CI-Gate). 7.9.4 ist ein reiner Stabilitäts-Fix der Ereignisverarbeitung
(`event_runtime.py`, `thermal_tracker.py`, `monitor_goal.py`, siehe
`docs/umsetzung-7.9.4.md`); Sprachverständnis und Planung sind unberührt.

Prüfung 7.9.3 (`3f72611`) → 7.9.4 (`scripts/corpus_shadow.py --check
docs/perf/corpus-signatures-7.9.3.json`): **0 geänderte Signaturen** über alle
4098 Sätze; die neue Baseline ist **byte-identisch** mit der von 7.9.3.

| Änderung | Anzahl | Begründung |
|---|---:|---|
| neu | 0 | Die neuen Tests enthalten keine Benutzersätze. |
| entfallen | 0 | – |
| geändert | 0 | – |
