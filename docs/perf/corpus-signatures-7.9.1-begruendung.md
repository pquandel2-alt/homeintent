# Korpus-Signaturen 7.9.1 – Begründungsliste

Neue Baseline `corpus-signatures-7.9.1.json` (ersetzt `corpus-signatures-7.9.0.json`
im CI-Gate). Vergleich 7.9.0 (`2cc962f`) → 7.9.1 über dieselben 3679 Sätze
(`scripts/corpus_shadow.py --compare`): **1 Engine-Abweichung, 0 IR-Abweichungen,
kein SAFETY_DRIFT** (`shadow_compare.py`: 2064 EQUIVALENT).

| Satz | 7.9.0 (Engine-Einzellesart) | 7.9.1 | Begründung |
|---|---|---|---|
| „Fahr Küche und Esszimmer Rollladen runter.“ | schließt nur `cover.esszimmer_rollladen`; „Küche“ fällt still weg | keine Einzellesart (leer) | A4: Ein gesprochener Ort wird nie still verworfen. Der benannte „Esszimmer Rollladen“ liegt nicht in der Küche, daher bindet `target_resolution` ihn nicht mehr unter dem Ort „Küche“. Im Gespräch übernimmt wie bisher die Koordinationslesung: beide Rollläden schließen (Live-Szenario `s771-a4-coordination` unverändert grün). |
