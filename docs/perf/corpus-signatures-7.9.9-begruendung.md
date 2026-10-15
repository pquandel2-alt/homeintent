# Korpus-Signaturen 7.9.9

7.9.9 verändert keine Parser-, Router-, Grounding- oder Policy-Semantik. Der
Release stabilisiert EventRuntime, Langläufer-Terminierung, Zeitzonentests
und die Abschirmung technischer Fehlertexte.

Der tatsächlich ausgeführte Vergleich gegen die 7.9.8-Baseline ergab:

- 4107 Korpussätze,
- 4107 vorhandene Basissignaturen,
- 0 geänderte Signaturen,
- `SAFETY_DRIFT = 0` im separaten Shadow-Vergleich.

Darum ist `corpus-signatures-7.9.9.json` bytegleich mit der 7.9.8-Datei
(SHA-256 jeweils
`8defd287d5083eecf1961a44bcdcf70186f304f03a047721e8d6d36e4126333e`).
Die neue Versionsdatei verhindert, dass spätere Sprachänderungen unbemerkt
gegen eine ältere Release-Oberfläche geprüft werden.
