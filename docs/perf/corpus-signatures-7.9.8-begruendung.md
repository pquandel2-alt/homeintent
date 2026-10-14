# Korpus-Signaturen 7.9.8 – Begründungsliste

Neue Baseline `corpus-signatures-7.9.8.json` (ersetzt
`corpus-signatures-7.9.7.json` im CI-Gate). 7.9.8 ändert ausschließlich die
Event-Runtime, Consumer-/Monitor-Kontext, Diagnose und eine explizite
Long-running-Konfigurationsoption; NLU, Intent-Auflösung, Dialog, Planung und
Ausführung bleiben unverändert.

Prüfung gegen die 7.9.7-Baseline:

```text
4107 Sätze, 4106 mit 7.9.7-Baseline; geänderte Signaturen: 0
```

Die neue Baseline enthält 4107 Einträge und besteht gegen sich selbst mit
`0` geänderten Signaturen. Es gibt einen neuen und keinen entfallenen Eintrag:

| Änderung | Anzahl | Begründung |
|---|---:|---|
| neu | 1 | „Reproduce the 7.9.7 silent thermal loss behind a degraded history.“ – Beschreibung des neuen Runtime-Regressionstests, kein Benutzersatz. |
| entfallen | 0 | – |
| geändert | 0 | – |
