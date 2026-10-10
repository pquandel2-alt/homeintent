# Korpus-Signaturen 7.9.6 – Begründungsliste

Neue Baseline `corpus-signatures-7.9.6.json` (ersetzt `corpus-signatures-7.9.5.json`
im CI-Gate). 7.9.6 ist ein reiner Stabilitäts-, Safety- und Performance-Bugfix
der Ereignisverarbeitung (`event_runtime.py`, `event_interest.py`,
`event_priority.py`, `monitor_goal.py`, siehe `docs/umsetzung-7.9.6.md`);
Sprachverständnis, Intent-Auflösung, Dialog und Planung sind unberührt.

Prüfung 7.9.5 (`dbb42d9`) → 7.9.6 (`scripts/corpus_shadow.py --check
docs/perf/corpus-signatures-7.9.5.json`): **0 geänderte Signaturen** über alle
4101 Sätze der alten Baseline. Die neue Baseline enthält diese 4101 Einträge
unverändert und drei neue.

| Änderung | Anzahl | Begründung |
|---|---:|---|
| neu | 1 | Docstring aus `tests/test_event_runtime_hardening_796.py` („A stub state machine firing ``state_changed`` like Home Assistant.“), den das Skript als satzartigen String mitnimmt; kein Benutzersatz. |
| neu | 1 | Docstring des RuntimeWarning-Regressionstests („The arbiter shadow sentences that left a coroutine unawaited (7.9.5).“); kein Benutzersatz. |
| neu | 1 | „Habe ich morgen einen Termin?“ – Eingabesatz des neuen Regressionstests `test_calendar_read_awaits_everything`. Die Signatur ist die der unveränderten Kalender-Lesefrage (Abfrage, keine Schreibaktion); sie kam nur neu in den Korpus, weil der Satz erstmals in einem Test steht. |
| entfallen | 0 | – |
| geändert | 0 | – |
