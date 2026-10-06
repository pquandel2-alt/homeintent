# Nachtest HomeIntent 7.9.1 (Befunde aus 7.9.0, Bestätigungston)

Geprüft wurde der Zweig `claude/homeintent-7.9.1`, Commit `3988ac8`.
Vergleichsbasis ist 7.9.0 (`2cc962f`). Der Auftrag stand in `sim/PROMPT_7.9.1.md`.
Die Testsession hat keinen Produktionscode geändert. Beispielsätze sind eigene
Prüfsätze; Sätze aus den verdeckten Korpora werden nur verallgemeinert beschrieben.

## Ergebnis in Kürze

| Bereich | 7.9.0 | 7.9.1 |
|---|---|---|
| Unit-Suite | 7189 bestanden | **7936 bestanden**, 12 übersprungen, 0 Fehler |
| CI-Gates (Sprach-Eval, Signaturen, Shadow, Arbiter, Dev-Benchmarks, alle Latenzen, pyright ×4, pyflakes) | grün | **grün**; Signaturen 3679/0 geändert, Shadow 2078 EQUIVALENT / 0 SAFETY_DRIFT |
| Live-Testbett `runner.py --strict` | 186/186 | **198/198** |
| Eigene Live-Szenarien (Befunde 7.9.0 + Ton) | 3/9 | **11/11 fachlich korrekt**. Gemeldet 10/11: Ein Fehlschlag ist ein Artefakt meiner Testanordnung, weil eine W3-Überwachung aus einem früheren Szenario im Goal-Store weiterlebt. |
| Korpus 236 (verdeckt) | 175 PASS | 175 PASS (+1 durch die Grenzwert-Regression, −1 Messartefakt: Temperatur 21,9 statt 21,8) |
| Korpus 1005 (verdeckt) | 726 PASS / 14 SAFETY | **729 PASS** / 14 SAFETY (unverändert, alte Fälle) |
| Probe, Holdout 1/2, README, Push, Sicherheitsfälle | – | identisch zu 7.9.0, 0 unsicher |
| HomeIntent-Befunde im HA-Log | 0 | 0 |

**Urteil:** Alle sieben Befunde aus dem Nachtest 7.9.0 sind behoben und live
bestätigt. Der Bestätigungston ist sauber gebaut:
- typisierte Entscheidung an einer Stelle;
- Ausgabe erst, wenn der Satellit `idle` ist;
- Rückfall auf „Erledigt.“, wenn der Ton scheitert;
- funktioniert in der echten Assist-Pipeline: kein TTS, genau ein Ton.

Verbleibend sind vor allem eine praktische Grenze des Tons bei echten Geräten
(T1) und zwei Entscheidungen für den Projekteigentümer (T2, T3).

## Befunde aus 7.9.0 – Status (live bzw. Stub, eigene Formulierungen)

| Befund 7.9.0 | Status 7.9.1 |
|---|---|
| B1 Garagentor öffnen bei Abwesenheit | **behoben.** Abgelehnt mit Angebot einer Benachrichtigung, auch bei „Wenn Philipp das Haus verlässt“ und „Überwache … und öffne es“. Rollläden morgens hochfahren und das Hauptventil bei Wasseralarm schließen bleiben erlaubt. |
| B2 fremde Überwachungen abschalten | **behoben.** Anna kann sie weder pausieren noch ausschalten, deaktivieren oder löschen. Sie sieht nur eigene Überwachungen. |
| B3 Etage ignoriert (Keller → Flur) | **behoben.** Antwort: „Im Keller gibt es keinen Bewegungs- oder Präsenzmelder …“ |
| B4 „über … geht“ | **behoben** für Grad, Prozent, ppm und „unter … geht“. |
| B5 Löschen im Kalender | **behoben.** Rückfrage, dann wird gelöscht. |
| B6 Antwort auf „In welchem Zeitraum?“ | **behoben** (live). |
| B7 Lücken | weitgehend behoben: alle 5 Minuten solange, 30 Sekunden, Wochen, zieht Watt, das Haus verbraucht kW, Zählerstand, „bei Auffälligkeiten“, „Beobachtest du …?“, Kurzliste. Rest siehe T6. |

## Neue bzw. verbleibende Befunde

### T1 – Bestätigungston: Bei echten Geräten wird der Ton vermutlich selten

`service_executor._effect_reached` prüft den Zielzustand **sofort** nach dem
Dienstaufruf, ohne zu warten. Nur wenn er dann schon erreicht ist, gilt die
Wirkung als bestätigt (`EXECUTED`) und der Ton kommt.

- Im Testhaus melden Lichter und Thermostate sofort.
- Echte Zigbee-, Z-Wave-, Matter- und WLAN-Geräte melden ihren neuen Zustand oft
  erst 100 ms bis 2 s später.
- Rollläden und Markisen melden während der Fahrt `opening`/`closing`. Das
  Release-Szenario `n791-b-partial` legt fest, dass „Fahre den Küchenrollladen
  runter“ **gesprochen** wird.
- Ein vollständig erfolgreicher Alltagsbefehl wird so oft gesprochen statt
  getönt. Das widerspricht dem Wunsch „Ton, wenn es ausgeführt wurde“.

**Empfehlung:**
- Begrenzt warten, bis die Wirkung bestätigt ist: einen Listener auf
  `state_changed` der Ziele registrieren, höchstens etwa 2 s, und vor dem Ende
  des Turns entscheiden.
- Zustände in Bewegung (`opening`/`closing` in die verlangte Richtung, eine
  Heizung mit dem neuen Sollwert) als **„ausgelöst“** werten. Das ist ein Erfolg
  im Sinne von B.1, solange kein Gegenzustand und kein `unavailable` gemeldet
  wird.
- Zum Testen eine einstellbare Melde-Verzögerung in `haus_sim` ergänzen (z. B.
  `report_delay`), damit sich das Verhalten live prüfen lässt.

### T2 – Entscheidung: Zugänge, die der Nutzer ausdrücklich öffnen lassen will

A1 lehnt jetzt jedes automatische Öffnen ab, wie gefordert. Das betrifft auch
beliebte Komfort-Automationen:
- „Wenn ich nach Hause komme, öffne das Garagentor.“
- Ventile, also auch eine **Gartenbewässerung nach Zeitplan**. Der Auftrag hatte
  „Ventile“ allgemein genannt.

**Empfehlung zur Entscheidung durch den Projekteigentümer:**
- (a) So lassen.
- (b) Eine ausdrückliche Admin-Option „automatisches Öffnen erlauben für …“ je
  Gerät, mit Warnung in der Vorschau. Abwesenheits-Auslöser bleiben in jedem
  Fall gesperrt.
- (c) Nur Wasser- und Bewässerungsventile ausnehmen, Gas- und Hauptventile nie.

### T3 – Entscheidung: Wand-Satelliten ohne angemeldeten Benutzer

Eine Sprachquelle ohne Benutzer kann seit A2 keine Überwachung und keine
Automation mehr verwalten. Das ist korrekt gemeldet, aber im Alltag ist ein
Wand-Satellit der Normalfall.

**Empfehlung:** Eine Zuordnung „Satellit → Benutzer bzw. Haushalt“ (Admin-Einstellung),
oder für Satelliten ohne Benutzer nur die Überwachungen des Haushalts, die
ein Admin als „gemeinsam“ markiert hat.

### T4 – Etage mit nur einem Melder

„Melde dich, wenn sich im Obergeschoss zwei Stunden nichts bewegt“ überwacht
nur den Präsenzmelder im Schlafzimmer, weil er der einzige Melder auf der Etage
ist. Die Vorschau nennt das Schlafzimmer, sagt aber nicht, dass Bad und
Kinderzimmer unbeobachtet bleiben.

**Empfehlung:** Die Vorschau sagt das ausdrücklich („Im Obergeschoss gibt es nur
im Schlafzimmer einen Melder; Bad und Kinderzimmer kann ich nicht beobachten.“).

### T5 – „Stromverbrauch“ ohne Raum

„Wenn der Stromverbrauch über 3000 Watt geht, warn mich.“ fragt zwischen vier
Leistungssensoren nach. Es gibt aber genau einen Sensor mit dem Namen
„Stromverbrauch Haus“, und „das Haus verbraucht …“ wählt ihn bereits.

Die Rückfrage ist sicher, aber unnötig. Ein Registry-Name, der das gesprochene
Nomen exakt enthält und keinen Raum hat, sollte vor einer Rückfrage über
Geräteklassen gewinnen.

### T6 – Verbleibende Lücken (alle sicher abgelehnt)

| Formulierung | Verhalten |
|---|---|
| „Wenn die Sonne scheint, fahre die Markise aus.“ / „Wenn es draußen heller als 30000 Lux ist, öffne die Markise.“ | nicht verstanden (wie 7.9.0) |
| „Jeden Morgen um 6 Uhr öffne die Bewässerung.“ | „kein eindeutig passendes Gerät“, obwohl zwei Ventile existieren. Erwartet: Rückfrage bzw. T2. |
| „Wenn ich gehe und noch Licht an ist, sag mir Bescheid.“ | Die Push-Nachricht lautet nur „Du hast das Haus verlassen.“ und nennt das Licht nicht. |
| „Erinnere mich jede Minute, bis die Markise eingefahren ist.“ | wird direkt zu „wenn Markise geöffnet wird …“ ohne die Frage „nur jetzt oder jedes Mal?“, die bei der Haustür kommt. Uneinheitlich. |
| „Sag mir Bescheid, wenn die Außentemperatur schnell fällt.“ | Rate ohne Betrag und Zeitraum, nicht zugeordnet. Erwartet: Rückfrage nach Betrag und Zeitraum. |
| „Welche Überwachungen laufen?“ nach dem Pausieren | Die Antwort enthält die volle Vorschau beim Pausieren und Stoppen. Die Kurzform gibt es nur in der Liste. |

## Bestätigungston – was live nachgewiesen ist

| Prüfung | Ergebnis |
|---|---|
| Stehlampe an, Heizung auf 21 °C, alle Lichter eines Raums aus, Szene | keine Sprache, genau ein Ton (`confirm.mp3`) |
| „Ja“ auf eine Automationsvorschau | Ton |
| Rückfrage, Sicherheitsfrage, „Nein“ | gesprochen |
| Teil nicht verstanden („… und den Toaster aus“) | gesprochen, nichts ausgeführt |
| Abfrage („Ist die Haustür zu?“) | gesprochen |
| Echte Assist-Pipeline | bei Erfolg kein `tts-start`, genau eine Ansage; bei Frage TTS |
| Text-Chat ohne Gerät | „Erledigt.“ |

Die Option `response_style` steht standardmäßig auf `spoken`. Der Ton ist also
erst nach dem Umschalten aktiv.

## Prioritäten für 7.9.2

1. **T1:** begrenztes Warten auf die bestätigte Wirkung; Fahrzustände als
   „ausgelöst“; Melde-Verzögerung im Testbett.
2. **T2 und T3** nach Entscheidung des Projekteigentümers.
3. **T4 und T5.**
4. **T6.**
