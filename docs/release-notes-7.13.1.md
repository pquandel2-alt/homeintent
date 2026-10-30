# HomeIntent 7.13.1

HomeIntent 7.13.1 sollte die offenen Fehler aus der unabhängigen Prüfung von
7.13.0 beheben. Die Nachprüfung fand jedoch verbliebene Fehler bei
zusammengesetzten Quellenbefehlen, Cover-Risiko und NLU-Latenz; diese werden
in 7.13.2 korrigiert. Der historische
[Umsetzungsbericht](https://github.com/pquandel2-alt/homeintent/blob/0f1e63d02fb97e23d626e924c62e9b3491147650/docs/umsetzung-7.13.1.md)
dokumentiert den damaligen Stand.

> Korrektur vom 28. Oktober 2026: Die ursprünglichen Aussagen zum vollständig
> fail-closed kompilierten Quellenverbund, zur Bestätigung aller nicht
> unabhängigen Cover und zu „keiner Verschlechterung“ waren nicht belegt und
> sind sachlich falsch. Die unten markierten Grenzen sind der tatsächliche
> Stand von 7.13.1.

## Sicherheitskorrekturen

- **Kein Teilbefehl bei teilweise verstandenen Medienbefehlen.**
  „Mach das Radio in der Küche aus und dann Quirlomat den Quirlomat.“
  pausierte in 7.13.0 das Radio (Gegenbeispiel des Nightly-Laufs
  [40470216752](https://github.com/pquandel2-alt/homeintent/actions/runs/40470216752)).
  Der Medien-Fastpath übernimmt einen Schreibbefehl jetzt nur, wenn die neue
  Satzverbrauchsprüfung (`media_coverage`) jedes Wort erklärt: Operation,
  Medienziel, ein Ort, Sender-/Bibliotheksname, Wert, Warteschlange und
  Füllwörter. Rest, widersprüchliche Orte/Ziele oder Koordinationen übergeben
  den ganzen Satz an die gemeinsame Mehrbefehls-Pipeline, die alle Klauseln
  vor dem ersten Schreibzugriff kompiliert und den Satz atomar ablehnt. Das
  gilt für Pause, Play, Stop, Quelle, Lautstärke, Stumm, Titelwechsel und
  Ein/Aus. Gültige Mehrbefehle („Pausiere das Radio in der Küche und mach das
  Licht im Büro aus“) laufen vollständig – 7.13.0 verwarf deren zweite Klausel.
- **Keine stillen Lücken in koordinierten Schreibsätzen.** Eine leere
  koordinierte Klausel („… und dann das“), ein koordinierter Ort ohne
  geplantes Ziel („… in der Küche und im Wohnzimmer“) und ein koordiniertes
  Ziel ohne geplantes Ziel („Öffne die Rollläden im Wohnzimmer und das
  Garagentor“ – in 7.13.0 entfiel das Garagentor stumm) führen zu einer
  Ablehnung ohne Service Call.
- **Garagentore und Tore** sind auch beim Setzen einer Position `CRITICAL`.

## Ehrliche Sammelergebnisse

- Licht- und Rollladen-Sammelaktionen werden vollständig vorgeprüft, dann pro
  Ziel aufgerufen. Alle angenommenen Ziele werden **gemeinsam** in einem
  begrenzten, ereignisbasierten Fenster (`effect_wait_seconds`) beobachtet;
  erst danach entsteht das unveränderliche `BatchExecutionResult`
  (`success`, `failed`, `rejected`, `contrary`, `timeout`, `unverified`,
  `unavailable`, `unsupported`, `unauthorized`, `not_confirmed`).
- Antwort, Audit (ein Eintrag pro Batch mit dem endgültigen Status jedes Ziels)
  und Undo entstehen aus demselben Ergebnis. Ein Gerät, das 100 ms nach dem
  Call bestätigt, gilt nicht mehr als „nicht verifiziert“; nur bestätigte
  Ziele kommen in den Undo-Plan. Keine nachträgliche, widersprechende
  Settle-Notiz mehr.

## Rollläden wirklich pro Ziel

7.13.0 behauptete partielle Cover-Batches, führte sie aber nicht aus (Cover
sind `MEDIUM`, der Batch lief nur bei `LOW`). 7.13.1 modelliert die
Batch-Eignung getrennt von der Risikoklasse: Standard-Cover (`shutter`,
`blind`, `shade`, `curtain`) mit `open_cover`, `close_cover` oder
`set_cover_position` laufen pro Ziel und bleiben `MEDIUM`. Der Batch-Selektor
hielt Türen, Fenster, Klappen, Markisen, unklassifizierte
oder nicht freigegebene Cover zwar vom partiellen Pfad fern. Die
Risikoklassifizierung stufte in 7.13.1 aber nur Garage/Garagentor/Tor kritisch
ein; beispielsweise `door` und `window` blieben `MEDIUM` und konnten atomar,
aber ohne Bestätigung ausgeführt werden. Das ist ein Sicherheitsfehler und
wird in 7.13.2 behoben. Ab sechs Zielen galt bereits die höhere Risikostufe.

## Release-Gate

`scripts/release_gate.py` verlangt für den exakten Release-SHA den neuesten
grünen Lauf von `ci.yml` **und** `nightly-live.yml` mit allen Pflichtjobs. Ein
fehlgeschlagener Sicherheits-Property-Job in irgendeinem Nightly-Lauf
desselben SHA blockiert dauerhaft. Das Gate gibt alle geprüften Run-IDs und
SHAs aus; der Release-Workflow hängt Commit- und Lauf-Links automatisch an
diesen Release-Text an, läuft global seriell und legt das Tag vor dem Release
fest auf den geprüften SHA. Mit den echten Läufen von 7.13.0 hätte das neue
Gate die Veröffentlichung blockiert.

## Verhaltensänderungen

- Die beabsichtigte vollständige Ablehnung eines Play-Befehls mit Quellenwahl
  in einem zusammengesetzten Satz war in 7.13.1 nicht durchgängig wirksam.
  Die Conversation-Pipeline konnte die Quellenklausel verwerfen und die
  Operation der anderen Klausel auf eine globale Player-Menge übertragen.
  7.13.2 führt dafür klauselspezifische Bindungen ein.
- Korpus-Baseline `corpus-signatures-7.13.1.json`: genau eine Signatur
  ändert sich gegenüber 7.13.0. Der rohe Engine-Aufruf für
  „Schalte Garten- und Terrassenlicht ein.“ plante nur die Außenbeleuchtung
  und wird jetzt abgelehnt; über die Conversation-Pipeline (mit
  Koordinationsexpansion) werden weiterhin beide Lichter geschaltet.

## Regressionstests

- [`tests/test_media_sentence_coverage_7131.py`](https://github.com/pquandel2-alt/homeintent/blob/0f1e63d02fb97e23d626e924c62e9b3491147650/tests/test_media_sentence_coverage_7131.py):
  fester Gegenbeispieltest, Varianten (und dann/danach, unbekanntes Verb,
  Ziel, Ausnahme, unvollständige Klausel, gültig/ungültig in beiden
  Reihenfolgen), positive Mehrbefehle, Metamorphie (Höflichkeit, Umstellung,
  unbekannte Zusätze) und Properties über Operationen, Quellen und Orte
- [`tests/test_safety_properties.py`](https://github.com/pquandel2-alt/homeintent/blob/0f1e63d02fb97e23d626e924c62e9b3491147650/tests/test_safety_properties.py):
  `REGRESSIONS` enthält das Nightly-Gegenbeispiel und die weiteren Fälle
- [`tests/test_batch_settle_7131.py`](https://github.com/pquandel2-alt/homeintent/blob/0f1e63d02fb97e23d626e924c62e9b3491147650/tests/test_batch_settle_7131.py) und
  [`tests/test_cover_batch_7131.py`](https://github.com/pquandel2-alt/homeintent/blob/0f1e63d02fb97e23d626e924c62e9b3491147650/tests/test_cover_batch_7131.py)
- [`tests/test_release_gate_7131.py`](https://github.com/pquandel2-alt/homeintent/blob/0f1e63d02fb97e23d626e924c62e9b3491147650/tests/test_release_gate_7131.py)

## Lokale Verifikation des Release-Stands

| Prüfung | Ergebnis |
| --- | --- |
| Pytest Python 3.12 mit Coverage | 10.252 bestanden, 12 übersprungen; Coverage 90 % |
| Pytest Python 3.13 | 10.252 bestanden, 12 übersprungen |
| Safety-Properties, CI-Profil | 93 bestanden |
| Safety-Properties, Nightly-Profil, Seeds 7131/424242/99991 | je 93 bestanden |
| Pyflakes, Pyright inkl. Strict-Profile | 0 Befunde |
| Sprachgate | 463 bestanden |
| Entwicklungsbenchmark | Entwicklung: 478 von 516 Fällen bestanden (573 Turns, 92,64 %); Held-out/OOD: 103 von 107 Fällen bestanden (126 Turns, 96,26 %); `unsafe_execution_count = 0` |
| Shadow / Arbiter / Korpus | 2.131 äquivalent; 2.156 äquivalent, 5 Verhaltensänderungen (wie 7.13.0); 0 Abweichungen zur neuen Baseline |
| 5.000-Entitäten-Benchmarks | absolute 100-ms-Budgets der damaligen CI bestanden; dennoch deutliche Regression gegenüber 7.13.0, unter anderem `multi_target` 4,51 → 46,61 ms und `multi_clause_command` 7,33 → 49,19 ms p95 |

Hassfest, HACS, Home-Assistant-Smoke-/Komponententests, Music-Assistant-Vertrag
und Live-Testbett laufen in der CI gegen echtes Home Assistant; die geprüften
Läufe für den exakten Release-Commit stehen im GitHub-Release.

## Grenzen

Sonos, Music Assistant, Lichter und Rollläden sind gegen Home-Assistant-
Verträge, das simulierte Live-Testbett und Fakes mit Event-Bus geprüft. Echte
Hardware war nicht Teil der automatisierten Prüfung; die manuelle Testmatrix
im [Umsetzungsbericht](https://github.com/pquandel2-alt/homeintent/blob/0f1e63d02fb97e23d626e924c62e9b3491147650/docs/umsetzung-7.13.1.md#5-manuelle-testmatrix-für-eine-reale-installation)
ist ausdrücklich noch offen.

## Geprüfte Freigabe

- Commit: [`0f1e63d02fb97e23d626e924c62e9b3491147650`](https://github.com/pquandel2-alt/homeintent/commit/0f1e63d02fb97e23d626e924c62e9b3491147650)
- `ci.yml`: [Lauf 40605416517](https://github.com/pquandel2-alt/homeintent/actions/runs/40605416517) (#530, Versuch 1, gleicher SHA)
- `nightly-live.yml`: [Lauf 40605390788](https://github.com/pquandel2-alt/homeintent/actions/runs/40605390788) (#44, Versuch 1, gleicher SHA)
