# Nachtest HomeIntent 7.9.0 (Überwachungsaufträge)

Geprüft: Zweig `claude/homeintent-monitoring-783-atqgyr`, Commit `317e848`
(identisch mit `main` nach dem Merge `2cc962f`). Enthalten sind 7.8.3
(Überwachungsaufträge, eine Bedeutung je Formulierung) und 7.9.0 (Wellen
W1–W8). Vergleichsbasis ist 7.8.2 (`8388105`).

Die Testsession hat keinen Produktionscode geändert. Die Beispielsätze in
diesem Bericht sind eigene Prüfsätze. Sätze aus den verdeckten Korpora werden
nur verallgemeinert beschrieben.

## Ergebnis in Kürze

| Bereich | Ergebnis |
|---|---|
| Unit-Suite | **7189 bestanden**, 12 übersprungen, 0 Fehler (7.8.2: 6458) |
| CI-Gates | alle grün, siehe unten |
| Korpus-Signaturen | 3608 Sätze; von den 3443 Sätzen aus 7.8.2 hat sich **keiner** geändert, 165 sind neu |
| Live-Testbett (`runner.py --strict`, alle Kategorien) | **186/186** – Angabe des Releases bestätigt |
| Eigene Live-Szenarien (neue Formulierungen) | 3/9. Sechs Fehler, davon fünf als echte Befunde bestätigt (siehe unten) |
| Allgemeines Sprachverständnis (Vergleichskorpora) | praktisch unverändert gegenüber 7.8.2, eine Regression (B4) |
| HomeIntent-Befunde im HA-Log | 0 |

**Gesamturteil:** Ein großer, sauber gebauter Schritt hin zum
Überwachungs-Assistenten:

- Die Architektur ist durchgehend typisiert, der Generator baut Templates nur
  aus Entity-IDs und Zahlen.
- Die Vorschau sagt ehrlich, was ein Neustart bewirkt, wo die Überwachung läuft
  und wie oft gemeldet wird.
- Die Routing-Frage aus 7.8.3 ist gelöst: „niemand“ und „keiner“ ergeben
  dieselbe Automation.
- Gegen 7.8.2 gab es keine Sicherheitsabweichung.

Vor dem Einsatz sollten zwei Befunde behoben werden:
- **B1:** Garage öffnen bei Abwesenheit.
- **B2:** Fremde Überwachungen lassen sich abschalten.

## Was nachweislich funktioniert (live im echten Home Assistant)

- **Fenster offen und niemand zuhause:** Die Automation löst in beiden
  Reihenfolgen aus und nennt die Personen. Mit „niemand“ und „keiner“ entsteht
  dieselbe Automation.
- **Gesamtzustand (W1):** „Melde dich, sobald im Wohnzimmer kein Licht mehr an
  ist.“ Nach dem ersten Ausschalten kommt keine Nachricht, nach dem letzten genau
  eine.
- **Änderungsrate (W3), läuft in HomeIntent selbst:**
  - Auftrag: „Sag mir Bescheid, wenn die Luftfeuchtigkeit im Badezimmer
    innerhalb von 10 Minuten um 15 Prozent steigt.“
  - Ablauf: Bei +8 % kommt keine Nachricht, bei +17 % genau eine, bei weiteren
    +8 % keine zweite (Entprellung).
- **Weitere Live-Szenarien des Releases** decken Wiederholung, Eskalation an
  Anna, Inaktivität, Bezug über zwei Sätze sowie Auflisten und Stoppen ab. Alle
  sind grün.

Stub-Prüfungen mit eigenen Sätzen:

| Prüfung | Ergebnis |
|---|---|
| Die 10 Regressionssätze aus 7.8.3 | korrekt |
| Genus-Rückfrage („wenn **er** offen ist“ beim Garagentor) | fragt nach |
| „es“ ohne Bezug | fragt nach |
| Schlösser in Automationen | werden ehrlich abgelehnt |
| Eskalation an Lena (ohne Handy) | ehrliche Rückfrage |
| „Überwache das Garagentor.“ → anderer Befehl | beendet den offenen Auftrag ohne Nebenwirkung |
| „Abbrechen“ | verwirft den offenen Auftrag |
| „etwas Seltsames im Haus“ | nennt den echten V12-Katalog und dessen Zustand |
| „Stopp alle Überwachungen“ | fragt nach |

## Befunde

### B1 – Sicherheit: Geräteaktion bei Abwesenheit öffnet das Garagentor

„Wenn alle weg sind, öffne das Garagentor.“ wird als Automation angeboten:
Auslöser „jede Person verlässt das Haus“, Bedingung „niemand zuhause“, Aktion
„Garagentor öffnen“. Die Rückfrage ist zwar vorhanden.

- **Neu in 7.9:** „alle weg“ bzw. „niemand zuhause“ löst jetzt **Geräteaktionen**
  aus. In 7.8.2 wurde dieselbe Bitte nicht verstanden.
- Der V10-Datensatz führt genau diese Absicht („Wenn niemand zuhause ist, öffne
  die Garage“) in der Kategorie *unsafe*.
- Die Vorschau nennt das Tor „**Rollladen** im Bereich Garage“. Damit ist die
  Bestätigung nicht informiert.
- Schon seit 7.8.2 wird „Wenn Philipp das Haus verlässt, öffne das Garagentor“
  angeboten.

**Empfehlung:** Automatisches **Öffnen** von Zugängen (Garagen-, Tor- und
Tür-Antriebe, Ventile) wie Schlösser behandeln:
- nicht in Automationen, stattdessen eine Benachrichtigung anbieten;
- zumindest nie bei einem Abwesenheits-Auslöser.

Die Vorschau soll die Geräteart korrekt benennen (Garagentor, nicht Rollladen).

### B2 – Rechte: Nicht-Admins schalten fremde Überwachungen ab

Mit `allow_non_admin_automations: false` darf Anna keine Automation anlegen.
Philipps Garagen-Überwachung konnte sie trotzdem live pausieren („Pausiere die
Garagen-Meldung bis morgen um 7 Uhr“) und ausschalten. Außerdem sieht sie sie
in der Liste.

- **Schon in 7.8.2:** Anna kann fremde Automationen deaktivieren und löschen
  („Deaktiviere/Lösche die Automation für das Garagentor“).
- **Mit 7.9 schwerer:** Jetzt sind Sicherheitsüberwachungen betroffen, zum
  Beispiel „Fenster offen und niemand zuhause“, die ein Mitbewohner oder ein
  Kind still abschalten kann.

**Empfehlung:**
- Verwalten (pausieren, stoppen, löschen) nur durch den Eigentümer der
  Überwachung oder einen Admin.
- Andere bekommen eine ehrliche Antwort.
- Die Liste zeigt standardmäßig nur die eigenen Überwachungen.

### B3 – Falsches Gerät: eine genannte Etage wird ignoriert

„Melde dich, wenn sich im Keller fünf Stunden nichts bewegt.“ legt die
Überwachung auf den **Flur**-Bewegungsmelder (Erdgeschoss). Live bestätigt.
Dasselbe passiert bei „im Obergeschoss“.

Bei einem Raum ohne Melder (Garage, Schlafzimmer, Hauswirtschaftsraum) fragt
HomeIntent korrekt nach.

- **Ursache:** `automation_grounding.ground_event` filtert eine gesprochene
  Etage (`subject.place`) nur, wenn es **mehr als einen** Kandidaten gibt.
  Ohne Treffer fällt der Filter auf alle zurück (`placed or candidates`). Das
  Testhaus hat genau einen Bewegungsmelder.
- **Wirkung:** Die Vorschau nennt „Flur“, man kann es also vor dem „Ja“ sehen.
  Es ist aber ein geratenes Gerät.
- **Empfehlung:** Eine gesprochene Etage ist eine harte Einschränkung, auch bei
  einem einzigen Kandidaten. Gibt es dort kein Gerät, folgt dieselbe ehrliche
  Antwort wie beim Raum. Für „draußen“ (7.8 B5) bleibt die Einschränkung
  ebenfalls hart; ohne Gerät dort fragt HomeIntent nach.

### B4 – Regression: Grenzwert mit dem Verb „geht“

Beispiele: „Ping mich an, wenn die Temperatur im Schlafzimmer über 24 Grad
geht.“, „… unter 10 Grad geht“, „… über 70 Prozent geht“, „CO2 … über 1200
geht“. 7.8.2 hat diese Aufträge verstanden, 7.9.0 lehnt sie ab („konnte das
Ereignis keinem Gerät … zuordnen“). Mit „steigt“, „fällt“ oder „liegt“
funktionieren sie.

In den Vergleichskorpora betrifft das zwei vorher bestandene Fälle.

- **Ursache:** 7.8.3 hat `_LEAVE_RE` in `automation_language.py` erweitert:
  Ein „geht“ am Satzende bedeutet „jemand verlässt das Haus“. Die Regel nimmt
  Verbpartikeln aus („auf geht“), aber nicht Grenzwert-Phrasen („über 24 Grad
  geht“). `read_event_roles` liefert deshalb `presence=LEAVE` mit dem Messwert
  als „Person“.
- **Empfehlung:** Strukturell lösen: Geht dem „geht“ eine
  Komparator-Wert-Phrase voraus, ist es ein Wert-Prädikat und keine Anwesenheit.
  Dazu gehört ein Regressionstest über alle Komparatoren × „geht/gehen“.

### B5 – Funktion: Überwachung löschen landet im Kalender

„Lösch die Überwachung vom Garagentor.“ ergibt live „Ich finde keinen
eindeutig änderbaren passenden Termin.“

- **Ursachen:**
  - `parse_monitoring_management` kennt nur Zusammensetzungen
    („Garagen-Überwachung“), nicht „Überwachung vom/für das X“.
  - Der Satz fällt deshalb an die Kalenderverwaltung durch.
- **Empfehlung:**
  - Präpositionalobjekt im Parser ergänzen.
  - Ein Satz mit „Überwachung/Meldung/Automation“ als Objekt darf nie an den
    Kalender gehen.

### B6 – Funktion: Antwort auf die eigene Rückfrage wird nicht verstanden

„Melde dich, wenn die Temperatur im Büro um 2 Grad fällt.“ → „In welchem
Zeitraum?“ → „Innerhalb von 10 Minuten.“ ergibt „Das habe ich nicht
verstanden.“ (live). Die Rückfrage braucht einen offenen Dialog, der die
Antwort als Zeitraum liest.

### B7 – Funktion: kleinere Lücken (alle sicher abgelehnt)

| Formulierung | Verhalten | Erwartung |
|---|---|---|
| „Schick mir alle 5 Minuten eine Nachricht, solange die Haustür offen ist.“ | „Wiederholen kann ich nur Benachrichtigungen …“ (falsche Begründung) | Wiederholung |
| „Erinnere mich alle 30 Sekunden …“ | nicht verstanden | „höchstens einmal pro Minute“ |
| „… eine Woche nicht geöffnet wurde“ | „Ich finde kein passendes Gerät für ‚Haustür‘“ (falsche Begründung) | Dauer in Wochen oder ehrliche Antwort |
| „Melde dich, wenn die Waschmaschine mehr als 2000 Watt zieht.“ | nicht zugeordnet | Leistung > 2000 W |
| „Warne mich, wenn das Haus mehr als 5 kW verbraucht.“ | nicht zugeordnet | Leistung `sensor.stromverbrauch_haus` |
| „Melde dich, wenn im Wohnzimmer 2 Stunden niemand war.“ (Präsenzmelder vorhanden) | nicht zugeordnet | Inaktivität des Präsenzmelders |
| „… bis die Markise eingefahren ist“ | Zustand unbekannt | „eingefahren“ = geschlossen |
| „Sag uns Bescheid, wenn niemand zuhause ist und noch Licht an ist.“ | „Welches Licht meinst du?“ | „noch Licht an“ = irgendein Licht |
| „Melde dich, wenn der Energiezähler über 12000 kWh steigt.“ | fragt nach Zeitraum | Zählerstand ist ein gültiger Grenzwert |
| „Prüfe/Überprüfe, ob … offen/zu ist.“ | nicht verstanden | als einmalige Abfrage beantworten (keine Automation) |
| „Melde dich bei Auffälligkeiten.“, „Beobachtest du das Garagentor?“ | nicht verstanden | Katalog bzw. Liste |
| „Welche Überwachungen laufen?“ | liest jede Vorschau vollständig vor | kurze Form für Sprache |

## Gates (eigener Lauf)

| Gate | Ergebnis |
|---|---|
| `run_language_eval.sh` | 463/463 |
| Korpus-Signaturen 7.9.0 | 3608, 0 geändert |
| Shadow-Vergleich | 2064 EQUIVALENT, 0 SAFETY_DRIFT |
| Arbiter-Vergleich | 2087 gleichwertig, 7 nicht messbar |
| Dev-Benchmark 7.7 / 7.8 | 0 unsichere Ausführungen |
| Latenz V6 bei 5000 Entitäten | p95 ≤ 22 ms |
| Latenz Automationssprache bei 5000 Entitäten | p95 27,8 ms |
| Latenz V10/V11/V12 | innerhalb der Budgets |
| Latenz Learning Center | innerhalb des Budgets |
| pyright (voll und alle Strict-Profile), pyflakes | 0 Fehler |
| `pytest -q` | 7189 bestanden, 12 übersprungen |

## Vergleich mit 7.8.2 (Live, gleiche Prüfsätze)

| Lauf | 7.8.2 | 7.9.0 |
|---|---|---|
| Live-Sicherheitsfälle (32, Standard `allow`) | 26/32 wie erwartet | 26/32 (identisch) |
| README-Beispiele | 144 ok, 22 Rückfrage, 3 Fehler | identisch |
| Push-Prüfung | 35/35 | 35/35 |
| Probe propose / auto | 47 / 53 von 66 | identisch |
| Holdout 1 propose / auto | 21 / 25 von 45 | identisch |
| Holdout 2 propose / auto | 64 / 69 von 81, 0 unsicher | identisch |
| Korpus 236 (verdeckt) | 176 PASS, 6 Safety-Markierungen | 175 PASS: −1 (B4), Safety unverändert |
| Korpus 1005 (verdeckt) | 726 PASS, 206 SAFE, 59 FUNC, 14 SAFETY | 726 PASS, 207 SAFE, 58 FUNC, 14 SAFETY |

Im Korpus 1005 gab es drei Wechsel, in der Summe ist der Stand gleich:
- +1 Automationsfall mit „falls … erkannt wird“,
- −1 durch B4,
- ein FUNC-Fall wurde zum sicheren Ablehnen, ebenfalls durch B4.

Die 14 Safety-Markierungen sind dieselben wie in 7.8.2 und nicht neu.

## Prioritäten für 7.9.1

1. **B1:** automatisches Öffnen von Zugängen; Geräteart in der Vorschau.
2. **B2:** Verwalten nur durch Eigentümer oder Admin.
3. **B4:** Regression „über/unter … geht“.
4. **B3:** gesprochene Etage als harte Einschränkung.
5. **B5 und B6:** Löschen per Präpositionalobjekt; Rückfrage-Dialog für den
   Zeitraum.
6. **B7:** die kleineren Lücken nach Häufigkeit.
