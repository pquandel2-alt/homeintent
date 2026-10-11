# Korpus-Signaturen 7.9.7 – Begründungsliste

Neue Baseline `corpus-signatures-7.9.7.json` (ersetzt
`corpus-signatures-7.9.6.json` im CI-Gate). 7.9.7 ändert ausschließlich die
Event-Runtime, ihren Interest-Index, Diagnose und Lasttests; NLU,
Intent-Auflösung, Dialog und Planung bleiben unverändert.

Prüfung gegen die 7.9.6-Baseline:

```text
4106 Sätze, 4104 mit Baseline; geänderte Signaturen: 0
```

Die neue Baseline enthält 4106 Einträge und besteht gegen sich selbst mit
`0` geänderten Signaturen. Zwei satzartige Test-Docstrings kamen hinzu:

| Änderung | Anzahl | Begründung |
|---|---:|---|
| neu | 1 | „Conservative undeclared future consumer used by 7.9.6 load tests.“ – interner Test-Helper, kein Benutzersatz. |
| neu | 1 | „A concrete consumer outranks a broad enabled category (review finding A).“ – Regressionstestbeschreibung, kein Benutzersatz. |
| entfallen | 0 | – |
| geändert | 0 | – |
