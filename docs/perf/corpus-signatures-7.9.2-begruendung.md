# Korpus-Signaturen 7.9.2 – Begründungsliste

Neue Baseline `corpus-signatures-7.9.2.json` (ersetzt `corpus-signatures-7.9.1.json`
im CI-Gate). Prüfung 7.9.1 (`3988ac8`) → 7.9.2 über dieselben 3679 Sätze der
7.9.1-Baseline (`scripts/corpus_shadow.py --check docs/perf/corpus-signatures-7.9.1.json`):
**1 geänderte Signatur, davon 0 Engine-Abweichungen und 1 IR-Abweichung, kein
SAFETY_DRIFT** (`shadow_compare.py --candidate identity`: 2078 EQUIVALENT;
`arbiter_shadow.py`: 2101 EQUIVALENT, 7 NOT_MEASURABLE wie bisher).
Die übrigen 128 Sätze des 7.9.2-Korpus sind neu (Testsätze der 7.9.2-Tests) und
haben keine Vorgänger-Signatur.

| Satz | 7.9.1 | 7.9.2 | Begründung |
|---|---|---|---|
| „Öffne die Bewässerung im Garten.“ | Engine: `valve.open_valve` auf `valve.bewaesserung`, Bestätigung nötig; IR-Ziel: Gattung `valve` | Engine: **unverändert**; IR-Ziel: Gattung `irrigation` | A2: Bewässerungsventile sind eine eigene Gattung (`irrigation`, nur über den Namen erkannt), damit nur sie in Automationen selbsttätig öffnen dürfen und Haupt-/Gasventile gesperrt bleiben. Das Verhalten (Ziel, Dienst, Rückfrage) ist identisch; nur die interne Gattungsbezeichnung ist feiner. |
