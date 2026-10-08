# Nachtest HomeIntent 7.9.3

- **Geprüft:** `main` = `3f72611` (Arbeitszweig `claude/homeintent-7-9-3-6jotis`).
- **Vergleichsbasis:** 7.9.2 (`e43fd9c`).
- **Auftrag:** `sim/PROMPT_7.9.3.md`.

Die Testsession hat keinen Produktionscode geändert. Die Beispielsätze sind
eigene Prüfsätze. Sätze aus den verdeckten Korpora werden nur verallgemeinert
beschrieben.

## Ergebnis in Kürze

| Bereich | 7.9.2 | 7.9.3 |
|---|---|---|
| Unit-Suite | 8492 | **9677 bestanden**, 12 übersprungen, 0 Fehler |
| CI-Gates | grün | **grün**. Signaturen: 4098 Sätze, 0 geändert. Shadow: 2131 EQUIVALENT, 0 SAFETY_DRIFT. Arbiter: 4 BEHAVIOR_CHANGE bei neuen Musikbefehlen, 0 SAFETY_DRIFT. |
| Live-Testbett `runner.py --strict` | 215/215 | **230/230**, HA-Log 0 Befunde |
| Eigene Live-Szenarien (28, eigene Formulierungen) | – | 20/28. Darunter 3 Artefakte meiner Testanordnung und 5 echte Formulierungslücken (siehe B2). |
| Push-Prüfung (`push_check.py`) | 35/35 | **33/35** (Befund B1) |
| Korpus 236 (verdeckt) | 178 PASS | **179 PASS**, Safety-Markierungen unverändert |
| Korpus 1005 (verdeckt) | 729 PASS / 15 SAFETY (davon 1 Testbett-Artefakt) | **735 PASS / 14 SAFETY**. Das Artefakt ist weg, die Sonnenuntergangs-Automation ist abgeschaltet. |
| Probe, Holdout 1/2, README, Sicherheitsfälle | – | identisch zu 7.9.2, 0 unsicher |

Im 1005er-Korpus gab es nur Verbesserungen:
- Musik: Lautstärke, nächster Titel, „Musik aus“, „Stopp die Musik“, „Lauter“;
- „Heizprogramm auf Urlaub“ ist wieder ein Gerätebefehl;
- keine einzige Verschlechterung.

**Urteil:** Die bisher beste Version. Die Befunde aus 7.9.2 sind behoben, und
alle Erweiterungen funktionieren live:
- Wetter;
- Aufenthaltsort mit Datenschutz;
- Musik;
- regenabhängige Bewässerung;
- Ton als Standard mit zweitem Ton;
- Haus-Bericht;
- Überwachungen ändern.

Es bleibt ein inhaltlicher Fehler in der neuen Regel „schon erfüllt“ (B1). Er
ist nicht sicherheitskritisch, erzeugt aber unnötige Nachrichten. Dazu kommen
einige Formulierungslücken bei den neuen Fähigkeiten.

## Was live nachweislich funktioniert

| Bereich | Nachweis |
|---|---|
| Schon erfüllte Bedingung (A1) | „Melde dich, wenn irgendeine Batterie unter 50 Prozent ist“: Die Vorschau nennt die Geräte, die schon darunter liegen. Nach „Ja“ kommt genau eine Nachricht. |
| Urlaub (A2/A3) | „Stell das Heizprogramm auf Urlaub“ und „Setz den Heizmodus auf Abwesend“ sind Gerätebefehle. „Wir sind bis Montag weg“ startet den Urlaub. „Ich bin wieder zuhause“ fragt, „Ja“ beendet. „Wir sind wieder da“ beendet ebenfalls, danach kommt keine Urlaubsnachricht mehr. |
| Zusammenfassung (A4) | „Was ist passiert, seit ich weg war?“ wird verstanden. |
| Keine Platzhalter (A5) | Die Vorschauen nennen echte Beispiele („im Badezimmer ist noch Licht an“, „Batterie Rauchmelder oben: 9 %“). |
| Wetter (B1) | „Wird es heute noch regnen?“ und „Wie wird das Wetter am Wochenende?“ werden aus der Vorhersage beantwortet. |
| Aufenthaltsort (B2) | „Ist Anna zuhause?“, „Wo ist Anna?“ (mit Uhrzeit). Lena hat keinen Standort; die Antwort sagt das ehrlich. |
| Musik (B3) | „Was spielt gerade in der Küche?“ nennt Titel und Sender. „Leiser“ funktioniert. Ein unbekannter Sender wird nicht geraten, die vorhandenen Quellen werden genannt. |
| Ton als Standard (B5) | Das Bootstrap bestätigt `response_style = tone` bei einer neuen Installation. Ton nach 1 s Verzögerung; der zweite Ton ist im Release-Szenario geprüft. |
| Überwachung ändern (B7) | „Mach aus der Haustür-Meldung 20 Minuten“ zeigt eine Vorschau, nach „Ja“ steht die neue Dauer in der Liste. |
| Regressionen aus 7.9.0–7.9.2 | alle weiterhin behoben (Garage, Rechte, Keller, Grenzwert „geht“, Löschen, Rückfragen, Bewässerung mit Ende, Urlaubs-Push) |

## Befunde

### B1 – „Schon erfüllt“ greift auch bei Momenten (unnötige Nachrichten)

Beispiele: „Benachrichtige mich, wenn ein Fenster aufgeht“ bzw. „… wenn im
Obergeschoss irgendein Fenster geöffnet wird“. Nach „Ja“ schickt HomeIntent
sofort „Schon beim Einrichten der Überwachung erfüllt: Badezimmerfenster
(offen) und Küchenfenster (offen).“ (`push_check.py`, 2 Fälle, live).

- Der Satz beschreibt einen **Moment**: aufgehen bzw. geöffnet werden. Ein
  Fenster, das schon offen ist, ist nicht aufgegangen. Die Sofortnachricht ist
  inhaltlich falsch und wird bei jeder neuen Fenster- oder Türmeldung zur
  Störung.
- Die Unterscheidung „Zustand oder Moment“ gibt es schon (7.8.3,
  `EventRoles.stative`). A1 soll nur für **Zustände und Grenzwerte** gelten
  („offen ist“, „unter 20 %“, „nicht erreichbar“), nicht für Momente
  („aufgeht“, „geöffnet wird“, „angeht“).
- Zusätzlich: Ein „Ja“ auf die kombinierte Frage heißt „einrichten **und**
  sofort schicken“. Sauberer wäre eine Frage mit zwei klar getrennten Antworten
  („Ja, und schick es jetzt“ / „Nur einrichten“). „Nur einrichten“ gibt es
  schon; das schnelle „Ja“ sollte im Zweifel nur einrichten.

### B2 – Formulierungslücken bei den neuen Fähigkeiten (alle sicher abgelehnt)

| Formulierung | Verhalten | Erwartet |
|---|---|---|
| „Brauche ich morgen eine Jacke?“ | „Frage erkannt, Ziel nicht gefunden“ | Antwort aus Temperatur, Regen und Wind (wie beim Schirm) |
| „Wer ist gerade daheim?“ | nicht beantwortet („Wer ist zuhause?“ funktioniert) | dieselbe Antwort |
| „Mach Radio Bob in der Küche an.“ | „Den Teil ‚bob‘ habe ich nicht verstanden“ („Spiel Bayern 3“ funktioniert) | Quelle aus `source_list` auch bei „mach X an“ bzw. „schalte X ein“, auch mehrteilige Namen |
| „Schick mir jeden Freitag um 17 Uhr eine Übersicht übers Haus.“ | nicht verstanden | Haus-Bericht (Übersicht, Zusammenfassung, Bericht, Status) |
| „Bewässere den Garten 10 Minuten.“ | nicht verstanden (im Release als Grenze benannt) | Sofortbefehl mit Pflicht-Ende, wie in der Automation |

### B3 – Kleinigkeiten

- **Beispiel in der Vorschau „nicht erreichbar“:** Gewählt wird ausgerechnet
  „Alarmanlage ist seit 10 Minuten nicht erreichbar“. Das erschreckt unnötig.
  Besser ein unkritisches Gerät als Beispiel, oder die Anzahl je Art.
- **Ohne Recorder** gibt es jetzt eine einzeilige Warnung, höchstens einmal je
  Stunde. Erledigt.
- **Im Release benannte Grenzen:**
  - Der Wetter-Auslöser prüft alle 30 Minuten.
  - Der Regensensor zählt bis 24 h nach einem HA-Neustart als „kein Nachweis“.
  - Überwachungen im Goal-Store sind nur in Zeitraum und Betrag änderbar.

  Alle drei sind nachvollziehbar dokumentiert.

### Artefakte meiner Testanordnung (keine Produktbefunde)

| Szenario | Ursache |
|---|---|
| „Ton ist Standard“ | Das Testbett-Bootstrap prüft `tone` bei der neuen Installation und schaltet danach bewusst auf `spoken` zurück, damit die alten Szenarien gelten. |
| Batterie-Push „unter 25 %“ | Die Sofortnachricht kam schon beim „Ja“, vor dem Löschen des Protokolls. Die Bad-Batterie lag schon darunter. Das neue Szenario bestätigt die Nachricht. |
| Überwachung löschen | Eine Überwachung aus einem früheren Szenario lebt im Goal-Store weiter (bekannt). |
| „Leiser“ | HomeIntent nutzt `volume_down` statt `volume_set`. Das ist korrekt, meine Erwartung war zu eng. |

## Verbesserungs- und Erweiterungsvorschläge

| # | Vorschlag | Nutzen |
|---|---|---|
| V1 | **B1 beheben:** „schon erfüllt“ nur für Zustände und Grenzwerte; ein „Ja“ richtet im Zweifel nur ein | keine Fehlalarme bei jeder Fenster- oder Türmeldung |
| V2 | **Formulierungslücken aus B2** schließen, mit kombinatorischen Tests je Fähigkeit (Wetterfragen über Kleidung und Tätigkeiten, „daheim/gerade“, Quelle mit „mach … an“, Bericht-Synonyme, Bewässerung sofort) | die neuen Fähigkeiten im Alltag robuster |
| E1 | **Antworten in Push-Nachrichten** („Garagentor seit 10 Minuten offen – [Schließen] [Ignorieren]“). Ein Tippen läuft über denselben bestätigten Schreibweg wie „Ja“. V12 hat dafür schon Ansätze (`async_handle_push_action`). | aus Warnung wird direkte Handlung |
| E2 | **Wartungs-Erinnerungen:** „Erinnere mich alle 3 Monate an den Filterwechsel der Lüftung“ oder nach Betriebsstunden („nach 200 Stunden Trockner …“) | typischer Haushaltsbedarf |
| E3 | **Urlaub aus dem Kalender:** Ein Kalendereintrag „Urlaub“ startet bzw. beendet den Urlaubsmodus. Vorher Vorschau und „Ja“ beim ersten Mal, danach gilt die Bindung. | kein Vergessen beim Abreisen |
| E4 | **Szenen per Sprache speichern:** „Speichere das als Leselicht“ (aktueller Zustand ausgewählter Lichter → Szene), „Aktiviere Leselicht“ | eigene Lichtstimmungen ohne App |
| E5 | **Erklärbarkeit der Überwachungen:** „Warum hast du mir um 14:12 eine Nachricht geschickt?“ → Auslöser, Gerät, Wert (aus Trace und Automation) | Vertrauen in die Benachrichtigungen |

## Prioritäten für 7.9.4

1. V1 (B1).
2. V2 (B2) und B3-Beispiel.
3. Erweiterungen E1–E5 nach Wahl des Eigentümers.
