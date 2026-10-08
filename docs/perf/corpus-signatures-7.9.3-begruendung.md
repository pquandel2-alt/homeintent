# Korpus-Signaturen 7.9.3 – Begründungsliste

Neue Baseline `corpus-signatures-7.9.3.json` (ersetzt `corpus-signatures-7.9.2.json`
im CI-Gate). Prüfung 7.9.2 (`e43fd9c`) → 7.9.3 über dieselben Sätze der
7.9.2-Baseline (`scripts/corpus_shadow.py --check docs/perf/corpus-signatures-7.9.2.json`):
**0 geänderte Signaturen** über alle 3826 gemeinsamen Sätze – weder Engine- noch
IR-Abweichungen, kein SAFETY_DRIFT (`shadow_compare.py --candidate identity`:
2108 EQUIVALENT; `arbiter_shadow.py`: kein SAFETY_DRIFT).

Unterschiede in der Satzmenge:

| Änderung | Anzahl | Begründung |
|---|---:|---|
| neu (Testsätze und Live-Szenarien 7.9.3) | 272 | Sätze der neuen Tests (`tests/test_*_793.py`) und der Szenarien „Nachtest 7.9.3“; ohne Vorgänger-Signatur. |
| entfallen | 1 | „Wenn das Wohnzimmer Fenster geöffnet wird, sende ich eine Push-Benachrichtigung an „Philipp Handy“: … Soll ich das so einrichten?“ – kein Benutzersatz, sondern der erwartete Vorschautext `PUSH_PREVIEW_9` in `tests/test_integration_wave_e2e.py`, der wegen A1 (Hinweis auf schon erfüllte Bedingung) einen anderen Wortlaut hat (siehe `docs/umsetzung-7.9.3.md`, „Geänderte Test-Erwartungen“). |

Die neuen Fähigkeiten (Wetter, Personen, Medien, Haus-Bericht, Überwachungen
ändern) betreffen nur Sätze, die 7.9.2 nicht verstand; an keinem Satz der
7.9.2-Baseline ändert sich die Signatur.
