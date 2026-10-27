# Umsetzung 7.13.1 – Satzverbrauch, finalisierte Batches, Cover, Release-Gate

Ausgangsstand: v7.13.0, Commit `324cfb8aaf7dc7ef7254cd4a9f0f810b9d2eb94a`
(normale CI grün in [Lauf 40457654872](https://github.com/pquandel2-alt/homeintent/actions/runs/40457654872),
Nightly-Sicherheitslauf desselben Commits fehlgeschlagen in
[Lauf 40470216752](https://github.com/pquandel2-alt/homeintent/actions/runs/40470216752)).

## 1. Reproduktion vor der Änderung

Alle Reproduktionen liefen gegen den unveränderten Stand `324cfb8` über die
vollständige Conversation-Pipeline (`tests/_testhaus.HouseConversation`).

| Eingabe / Szenario | v7.13.0 | erwartet |
| --- | --- | --- |
| „Mach das Radio in der Küche aus und dann Quirlomat den Quirlomat.“ | „Küchenradio pausiert.“, `media_player.media_pause` | kein Write, Ablehnung |
| „Pausiere das Radio in der Küche und mach das Licht im Büro aus.“ | nur `media_pause`, die gültige zweite Klausel entfällt stumm | beide Klauseln gemeinsam validiert |
| „Mach das Licht im Büro aus und dann das.“ | drei Lichter aus | kein Write (unvollständige Klausel) |
| „Pausiere das Radio in der Küche und im Wohnzimmer.“ | nur Küche pausiert, Wohnzimmer entfällt stumm | kein Write |
| „Mach die Musik im Schlafzimmer ein bisschen leiser.“ | Medienreader liest `on` (wegen „ein“) | Lautstärke leiser oder kein Write |
| Drei Bürolichter, eines meldet nach 100 ms, eines nie | „Ein Licht wurde ausgeschaltet. Deckenfluter und Schreibtischlampe konnten nicht verifiziert werden. Schreibtischlampe hat sich noch nicht zurückgemeldet.“ | „Zwei Lichter wurden ausgeschaltet. Schreibtischlampe hat sich nicht rechtzeitig zurückgemeldet.“ |
| Drei Bürolichter, alle melden nach 100 ms | „Bürolicht, Deckenfluter und Schreibtischlampe konnten nicht verifiziert werden.“, Undo leer | Erfolg, Undo für alle drei |
| „Fahr die Rollläden im Wohnzimmer runter.“ | ein gemeinsamer `cover.close_cover` (Cover = `MEDIUM`, Batch nur bei `LOW`) | ein Call pro Rollladen |
| Release-Gate mit den echten Läufen von `324cfb8` | freigegeben (liest nur `ci.yml`) | blockiert |

## 2. Ursachen

1. **Medien-Fastpath ohne Satzverbrauch.** `media.parse_media_request` wählt
   die Operation aus Wortmengen (`present & {...}`). Der vorgeschaltete
   Controller (`InsightsController._async_media`) prüfte nur fremde Ziele,
   Zeitangaben und Satzmodus – nicht, ob jedes Wort erklärt ist. Er fing damit
   zusammengesetzte Sätze vor der gemeinsamen Mehrbefehls-Pipeline ab.
   Wortanzahlgrenzen (`len(words) <= 5`, `> 7`) waren ein Ersatz für diese
   Prüfung und erzeugten zusätzlich Fehllesungen.
2. **Leere koordinierte Klausel und koordinierter Ort.** Die lexikalische
   Abdeckung (`analyse_semantics`) zählt Konjunktionen, Referenzen und
   Ortswörter als erklärt. Eine leere zweite Klausel („und dann das“) und ein
   Ort ohne Ziel blieben unbemerkt.
3. **Batch-Ergebnis vor dem Settle eingefroren.** Der Executor speicherte
   jedes Ziel direkt nach dem Service Call als `UNVERIFIED` im fertigen
   `BatchExecutionResult` und reichte die Wirkung an den Turn weiter.
   `async_settle_turn` aktualisierte nur die allgemeinen Turn-Outcomes.
   Antwort, Audit und Undo wurden aus dem eingefrorenen Ergebnis gebildet;
   die spätere Settle-Notiz widersprach zusätzlich.
4. **Batch-Eignung an Risikoklasse gekoppelt.** `overall_decision.risk > LOW`
   schickte jede Cover-Sammelaktion auf den alten gemeinsamen Call.
5. **Release-Gate las nur `ci.yml`.** Der Nightly-Sicherheitslauf desselben SHA
   wurde nicht geprüft.

## 3. Architekturentscheidungen

### Vollständiger Satzverbrauch (`media_coverage.py`)

Ein schreibender Medienbefehl wird vom Fastpath nur übernommen, wenn
`analyse_media_coverage` jedes Wort einer Rolle zuordnet:

| Rolle | Quelle |
| --- | --- |
| `operation` | Lexeme genau der gewählten Operation (`media.MEDIA_OPERATION_WORDS`) und allgemeine Stützverben |
| `target` | Medien-Gattungsnomen (Ontologie) oder Registry-Name eines `media_player`, Inhaltsnomen („Musik“), Präposition davor |
| `place` | genau ein Ort aus dem gemeinsamen Ortslexikon (`utterance_fields`) |
| `source` | freier Name eines Play-Befehls bis zum Ort/Player – später nur exakt gegen Quellenliste, Alias oder MA-Bibliothek aufgelöst |
| `value` | Lautstärkezahl |
| `queue` | Warteschlangen-Direktive (Positionen aus `queue_directive_positions`) |
| `filler` | Artikel, Höflichkeit, Diskurspartikeln (`normalize.DISCOURSE_FILLERS`), höfliche Fragehülle |

Übrig bleibt der *Rest*; Konjunktionen werden als *Koordinationen* geführt;
zwei Orte oder zwei Geräteziele sind *Konflikte*. Bei Rest, Konflikt oder
Koordination lehnt der Fastpath ab und übergibt den **ganzen Satz** an die
gemeinsame Mehrbefehls-Pipeline der Engine (`independent_predicate_clauses`,
`CommandPlan`), die jede Klausel vor dem ersten Schreibzugriff kompiliert und
den Satz atomar ablehnt, wenn eine Klausel nicht verstanden wird. Es gibt
keine Parallelgrammatik für Medien und keine Satzliste.

Zusätzlich prüft `nlu/clause_completeness.py` in `NluEngine.understand` jeden
schreibenden Plan: Eine koordinierte Klausel ohne Inhalt oder ein
koordinierter Ort, in dem kein geplantes Ziel liegt (und den die Lesart nicht
selbst als leeren Teil eines Allquantors gebunden hat), führt zu „Den Teil „…“
habe ich nicht verstanden. Ich habe deshalb nichts ausgeführt.“

### Settle-fähige Batch-Ergebnisse (`batch_execution.py`)

```
Vorprüfung aller Ziele ──► Service Calls (nur zulässige Ziele)
        │                         │
        ▼                         ▼
 PREFLIGHT-Status        PendingBatchExecution (Ziel = PENDING)
                                  │
                async_settle: ein Listener, ein Zeitfenster
                (effect_wait_seconds) für alle angenommenen Ziele
                                  │
                                  ▼
          finalize() ──► unveränderliches BatchExecutionResult
                                  │
         ┌────────────────────────┼─────────────────────────┐
         ▼                        ▼                         ▼
     Antwort              AuditTrail.record_batch      Undo (nur SUCCESS)
```

Endgültige Zielstatus: `success` (Wirkung bestätigt), `failed` (Service Call
fehlgeschlagen), `rejected` (unmittelbar vor dem Call erneut abgelehnt),
`contrary` (Gegenrichtung/Gegenteil), `timeout` (beobachtet, keine
Rückmeldung), `unverified` (Beobachtung nicht möglich, z. B. kein Event-Bus),
`unavailable`, `unsupported`, `unauthorized`, `not_confirmed`. `pending` gibt es
nur im `PendingBatchExecution`; ein finales Ergebnis mit `pending` wird beim
Erzeugen abgelehnt. Batch-Ziele werden nicht mehr an den Turn delegiert;
dadurch entsteht keine spätere, widersprechende Settle-Notiz.

### Risikoklasse und Batch-Eignung getrennt

`batch_eligibility` entscheidet unabhängig von `RiskLevel`, ob Teilergebnisse
voneinander unabhängig sind:

* Lichter (`turn_on`/`turn_off`) und Standard-Cover der Klassen `shutter`,
  `blind`, `shade`, `curtain` (`open_cover`, `close_cover`,
  `set_cover_position`) sind geeignet.
* `garage`, `gate`, `door`, `window`, `damper`, `awning`, fehlende Klasse,
  nicht freigegebene Ziele, gemischte Domänen und andere Dienste behalten den
  bisherigen atomaren, bestätigungspflichtigen Pfad.
* Ein Ziel, das für sich `HIGH` oder `CRITICAL` ist, läuft nie pro Ziel.
* Die Gesamt-Risikoklasse (inklusive Breitenstufe ab sechs Zielen) entscheidet
  weiterhin über Bestätigung und Autorität für den ganzen Batch vor dem ersten
  Call. Cover bleiben `MEDIUM`; Garagentore und Tore bleiben `CRITICAL`, jetzt
  auch für `set_cover_position`.

### Release-Gate

`scripts/release_gate.py` verlangt für den exakten SHA den neuesten
erfolgreichen Lauf von `ci.yml` **und** `nightly-live.yml` mit allen
Pflichtjobs. Ein fehlgeschlagener Sicherheits-Property-Job in irgendeinem
Nightly-Lauf desselben SHA blockiert dauerhaft (ein gefundenes Gegenbeispiel
wird durch einen späteren Lauf mit anderen Seeds nicht widerlegt). Das Gate
gibt alle geprüften Run-IDs, Versuche, SHAs und URLs aus und erzeugt den
Evidenzblock, den der Release-Workflow an den Release-Text anhängt. Der
Workflow läuft global seriell, legt das Tag vor dem Release direkt auf den
geprüften SHA (`git/refs`, schlägt bei existierendem Tag fehl), erstellt den
Release mit `--verify-tag` und prüft danach, dass das Tag auf den SHA zeigt.

## 4. Grenzen

* Ein Play-Befehl mit Quellenwahl in einem *zusammengesetzten* Satz
  („Spiel Bayern 3 in der Küche und mach das Licht aus“) wird abgelehnt, weil
  die gemeinsame Mehrbefehls-Pipeline keine Quellenwahl kompiliert. Das ist
  fail-closed, aber eine Funktionslücke.
* „Mach das Radio aus“ wird allein als `media_pause` (fähigkeitsbasiert), in
  einem zusammengesetzten Satz über die Engine als `media_player.turn_off`
  ausgeführt. Beides schreibt nur das genannte Gerät; die Operation
  unterscheidet sich.
* Batch-Calls laufen nacheinander; nur das Warten auf die Wirkung ist
  gemeinsam. Mehrere Batches in einem `CommandPlan` warten jeweils in einem
  eigenen begrenzten Fenster.
* Reale Sonos-, Music-Assistant-, Licht- und Rollladenhardware war nicht Teil
  der automatisierten Prüfung (siehe Testmatrix).

## 5. Manuelle Testmatrix für eine reale Installation

Status aller Zeilen: **noch manuell zu prüfen** – nicht automatisiert, nicht
als bestanden behauptet.

| # | Szenario | Eingabe | Erwartung | Status |
| --- | --- | --- | --- | --- |
| M1 | Sonos Play/Pause/Stop | „Pausiere die Musik im Wohnzimmer“, „Weiter“, „Stoppe die Musik im Wohnzimmer“ | genau der Sonos-Player; Pause/Play/Stop hörbar; kein `turn_off` ohne belegtes `TURN_OFF` | offen |
| M2 | gespeicherter Radiosender | „Spiel WDR 2 in der Küche“ (Sender in `source_list`) | `select_source` mit exakt „WDR 2“; unbekannter Sender → „finde ich nicht“ | offen |
| M3 | Music-Assistant-Favorit | „Spiel meine Playlist Entspannen im Wohnzimmer“ | `music_assistant.play_media` mit der Bibliotheks-ID; nichts bei Mehrdeutigkeit | offen |
| M4 | verzögerte Player-Zustandsmeldung | Sonos meldet `paused` erst nach ~1 s | Bestätigung innerhalb `effect_wait_seconds`, keine „nicht zurückgemeldet“-Notiz | offen |
| M5 | Satzrest bei Medien | „Mach das Radio in der Küche aus und dann Quirlomat den Quirlomat“ | kein Service Call, Ablehnung | offen (automatisiert gegen Fake geprüft) |
| M6 | mehrere Lichter, eines nicht erreichbar | „Mach das Licht im Wohnzimmer aus“, ein Leuchtmittel stromlos | erreichbare aus; das andere als nicht verfügbar/nicht rechtzeitig zurückgemeldet genannt | offen |
| M7 | mehrere Rollläden, einer nicht erreichbar | „Fahr die Rollläden im Wohnzimmer runter“, ein Aktor offline | ein Call pro Rollladen; der erreichbare fährt; der andere genannt | offen |
| M8 | Undo nach Teilerfolg | nach M6/M7: „Mach das rückgängig“ | nur die bestätigten Ziele werden zurückgestellt | offen |
| M9 | Garagentor in gemischter Zielmenge | „Öffne den Rollladen im Wohnzimmer und das Garagentor“ | Rückfrage/Admin-Pflicht, kein Teil-Call | offen |
