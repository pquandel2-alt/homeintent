# Korpus-Signaturen 7.9.7 – Begründungsliste

Neue Baseline `corpus-signatures-7.9.7.json` (ersetzt `corpus-signatures-7.9.6.json`
im CI-Gate). 7.9.7 ist ein reiner Stabilitäts-, Überlastungs- und Speicher-Bugfix
der Ereignisverarbeitung (`event_runtime.py`, `event_interest.py`,
`event_priority.py`, siehe `docs/umsetzung-7.9.7.md`); Sprachverständnis,
Intent-Auflösung, Dialog und Planung sind unberührt.

Prüfung 7.9.6 (`fff609d`) → 7.9.7 (`scripts/corpus_shadow.py --check
docs/perf/corpus-signatures-7.9.6.json`): **0 geänderte Signaturen** über alle
4104 Sätze der alten Baseline. Die neue Baseline enthält diese 4104 Einträge
unverändert und zwei neue.

| Änderung | Anzahl | Begründung |
|---|---:|---|
| neu | 1 | Docstring „Deadlines reported as "effect missing" (the light did react).“ aus `tests/test_event_runtime_priority_memory_797.py` (Hilfsfunktion `_record_expiries`), den das Skript als satzartigen String mitnimmt; kein Benutzersatz. |
| neu | 1 | Docstring „An enabled V12 context; only ``RELEVANT`` matters to its detectors.“ des Test-Doubles `_Proactive` aus derselben Datei; kein Benutzersatz. |
| entfallen | 0 | – |
| geändert | 0 | – |
