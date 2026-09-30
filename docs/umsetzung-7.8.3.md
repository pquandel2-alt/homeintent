# HomeIntent 7.8.3 – Überwachungsaufträge und Benachrichtigung

Auftrag: `sim/PROMPT_UEBERWACHUNG_7.8.3.md` (Zweig `claude/sleepy-meitner-xd7oux`).
Ausgangsstand 7.8.2 (`8388105`).

## Teil A – Patches

Beide Patches (`0001-ueberwachungsauftraege`, `0002-zustandskombinationen`)
ließen sich per `git am` unverändert auf `8388105` einspielen (Commits
`37f14cc`, `0ade200`). Die Gesamtsuite danach: 6549 bestanden, 12
übersprungen – wie im Auftrag angegeben.

Kritische Prüfung der Patches, Befunde, die in Teil B behoben wurden:

| Befund im Patch | Wirkung | Behoben durch |
|---|---|---|
| `state_conjunction` nahm für „niemand zuhause“ alle `person.*`, der Goal-Pfad nur den bestätigten Haushalt | zwei Personenmengen für einen Satz | `presence_scope.py` (B.3) |
| Nur die Reihenfolge „Zustand und Bedingung“ wurde gelesen | „wenn niemand zuhause ist und ein Licht an ist“ → „kein passendes Gerät für Licht“ | `and_reversed_candidates` |
| Bedingung „ein Fenster offen ist“ wurde eine Home-Assistant-State-Condition mit Entitätsliste | HA liest das als „**alle** Fenster offen“ | `_existential_condition` (ODER je Gerät) |
| `_NEGATED_NOTIFICATION_RE` übersprang Wörter über das Komma hinweg | „Melde dich, wenn keiner zuhause ist“ galt als verneinte Benachrichtigung | Verneinung nur im eigenen Teilsatz |

Unverändert übernommen: `automation_monitoring.py` (Frames), Anaphern und
Genus-Prüfung, gestapelte Dauerangaben, Tageszeitfenster, Rückfrage bei
Sensoren und Antrieben unter einem Nomen, `describe_holding_state`,
`AutomationModel.situation`.

## Teil B – eine Bedeutung

**Routing-Regel** (`conversation.py`, `NluConversationEntity._event_reading_claims`,
angewandt in `controllers/goals.py`, `GoalController.async_handle_goal_turn`):
Bevor ein V10-Monitor-Goal (`MONITOR_AND_NOTIFY`) übernommen wird, liest der
satzbasierte Ereignisleser den Satz (`NluEngine.event_reading_kind`, dieselben
Schutzprüfungen wie `match_automation`: Fragen und verneinte Benachrichtigungen
werden nie beansprucht). Liefert er `AUTOMATION` oder eine Geräte-Rückfrage
(`CLARIFY`), gehört der Satz dem Automationspfad. Das Goal behält nur Sätze,
die der Leser nicht versteht (z. B. „Wenn ich gehe und die Haustür nicht
verriegelt ist, sag mir Bescheid.“). Keine Wortliste, kein Satz ist
hinterlegt. `interpret_goal` ist unverändert.

**„Niemand zuhause“ = eine Personenmenge** (`presence_scope.py`): bestätigter
Haushalt → genau dessen Personen (Bedingung und „verlässt das Haus“-Auslöser);
ohne Haushalt → alle `person.*`, die Vorschau nennt sie („keiner von Anna,
Lena und Philipp zuhause ist“); keine `person.*` → ehrliche Antwort mit
Handlungsanweisung. Gebunden wird im selben Schritt wie „ich“ und „mich“
(`NotificationController.materialize_presence_scope`), die Bedingung trägt die
Personen (`ConditionModel.person_entity_ids`). Der Goal-Pfad nutzt dieselbe
Funktion; „kein bestätigter Haushalt“ gibt es nicht mehr.

### Vorher/Nachher (Testhaus, Sätze aus B.1)

| Satz | Pfad vorher (7.8.2 + Teil A) | Auslöser/Bedingung vorher | Pfad nachher | Auslöser/Bedingung nachher |
|---|---|---|---|---|
| „Sag mir Bescheid, wenn ein Fenster offen ist und keiner zuhause ist.“ | Automation | Fenster geht auf ∨ Anna/Lena/Philipp gehen; Bedingung: ein Fenster offen ∧ niemand zuhause | Automation | unverändert; Vorschau nennt die Personen |
| derselbe Satz mit „niemand“ | V10-Goal | im Testhaus Ablehnung „kein bestätigter Haushalt“; sonst nur „alle gehen“ | Automation | identisch zum „keiner“-Satz |
| „… und **warne** mich …“ | Automation | wie „keiner“ | Automation | identisch |
| „Sag mir Bescheid, wenn das Garagentor offen ist und niemand zuhause ist.“ | V10-Goal | Ablehnung „kein bestätigter Haushalt“; Garagen-Scope ohne Garagentor-Kontakte | Automation | Garagentor geht auf ∨ Personen gehen; Bedingung: Tor offen ∧ niemand zuhause |
| „Wenn ich gehe und noch Licht an ist, sag mir Bescheid.“ | V10-Goal | ich gehe; irgendein Licht an | Automation | Philipp verlässt das Haus; Bedingung: ODER über alle Lichter an |
| „Sag mir Bescheid, wenn niemand zuhause ist und ein Licht an ist.“ | V10-Goal | Ablehnung „kein bestätigter Haushalt“ | Automation | Licht geht an ∨ Personen gehen; beide Zustände als Bedingung |

## Geänderte Test-Erwartungen

| Test | Änderung | Begründung (auch im Test) |
|---|---|---|
| `test_monitoring_automation_783.py::test_preview_speaks_the_situation_not_the_trigger_list` | Vorschau „… und keiner von Anna, Lena und Philipp zuhause ist“ statt „… und niemand zuhause ist“ | B.3: ohne bestätigten Haushalt nennt die Vorschau die Personen; Bedeutung unverändert, nur ausdrücklich |
| `test_monitoring_automation_783.py::test_a_duration_is_not_completed` | parametrisiert über „keiner“ **und** „niemand“; Kommentar „keiner: the V10 monitor-goal route claims …“ entfernt | C.1: nach der Routing-Regel ergeben beide Wörter dieselbe Automation |

Keine andere bestehende Erwartung wurde geändert.

## Tests

- `tests/test_monitoring_automation_783.py` (aus dem Patch, 86 Fälle).
- `tests/test_monitoring_routing_783.py` (neu, 285 Fälle): 270 generierte
  Paraphrasen aus Benachrichtigungsverb (6) × Abwesenheitswort (3) ×
  Satzform (5) × Objekt (3) – dieselben Auslöser-Entitäten, Bedingungen und
  Empfänger; Haushalt, Moment mit Haushalt, keine Person, Routing-Regel,
  „ich gehe und noch Licht an“, Goal-Pfad bleibt erreichbar.

## Gates

| Gate | Ergebnis |
|---|---|
| `pytest -q` | 6830 bestanden, 12 übersprungen, 0 Fehler |
| `run_language_eval.sh` | 463/463 |
| Korpus-Signaturen gegen 7.8.2 | 0 geänderte Signaturen (3492 Sätze); neue Baseline `corpus-signatures-7.8.3.json` = 7.8.2 plus 49 neue Testsätze, keine Zeile geändert |
| Shadow-Vergleich | 2055 EQUIVALENT, 0 SAFETY_DRIFT |
| Arbiter-Vergleich | 2078 gleichwertig, 7 nicht messbar, 0 SAFETY_DRIFT |
| Dev-Benchmark 7.7 / 7.8 | 458/503 / 102/107, `unsafe_execution_count` 0 |
| Latenz Automationssprache (5000 Entitäten) | p95 16,6 ms (Budget 100 ms) |
| V6/V10/V11/V12/Learning-Center-Budgets | grün |
| `regex_inventory.py --write` | `_PLAIN_AND_RE` manuell STRUCTURAL; SEMANTIC_SENTENCE_PATTERN 173 (unverändert) |
| pyright (voll + Strict-Profile), pyflakes | 0 Fehler |

## Live-Testbett

Frisches Home Assistant 2026.9.2 (`sim/fresh_ha.sh`), `runner.py --strict`
über alle Kategorien inklusive Proaktiv: **179/179**, `check_log.py`: 0
Befunde. Neue Szenarien prüfen die Wirkung (Anzahl, Empfänger, Text der
Push-Nachrichten; `runner.py` kennt dafür `notify_count`, `notify_to`,
`notify_match`):

- `mon-window-away`: Küchenfenster auf, dann gehen alle → genau eine
  Nachricht an Philipps Handy; alle Fenster zu, alle gehen → keine; Haus leer,
  dann Fenster auf → eine.
- `mon-window-away-anna`: derselbe Auftrag von Anna → Annas Handy.
- `mon-routing-niemand`: „niemand“-Satz wird Automation, nicht Goal.
- `mon-garage-duration` (Proaktiv): Tor auf, nach 30 s keine, nach 80 s genau
  eine Nachricht.

## Offene Punkte

- Pronomen in einer Geräteaktion („Überwache die Haustür und schließ sie ab,
  wenn …“) → 7.9 W6.
- „Pass auf, dass keiner die Haustür öffnet.“ lehnt die Negations-Sperre ab
  (gewollt).
- Ein bestimmtes Nomen mit mehreren Geräten als Bedingung („das Licht“ bei 24
  Lichtern) wird nicht mehr still zu „alle“, sondern nicht verstanden.
