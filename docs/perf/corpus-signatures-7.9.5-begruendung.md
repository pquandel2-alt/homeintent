# Korpus-Signaturen 7.9.5 – Begründungsliste

Neue Baseline `corpus-signatures-7.9.5.json` (ersetzt `corpus-signatures-7.9.4.json`
im CI-Gate). 7.9.5 ist ein reiner Bugfix der Ereignisverarbeitung
(`event_runtime.py`, `hass_entities.py`, siehe `docs/umsetzung-7.9.5.md`);
Sprachverständnis und Planung sind unberührt.

Prüfung 7.9.4 (`517d76c`) → 7.9.5 (`scripts/corpus_shadow.py --check
docs/perf/corpus-signatures-7.9.4.json`): **0 geänderte Signaturen** über alle
4098 Sätze der alten Baseline. Die neue Baseline enthält diese 4098 Einträge
unverändert und drei neue.

| Änderung | Anzahl | Begründung |
|---|---:|---|
| neu | 3 | Docstrings aus `tests/test_event_runtime_correctness_795.py`, die das Skript als satzartige Strings mitnimmt („Runs a ``@callback`` inline like Home Assistant does.“, „A minimal state machine that fires ``state_changed`` like HA.“, „Stands in for the V12 context: records what each event saw.“); keine Benutzersätze. |
| entfallen | 0 | – |
| geändert | 0 | – |
