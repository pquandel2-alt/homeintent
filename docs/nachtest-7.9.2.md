# Nachtest HomeIntent 7.9.2

Geprüft: `main` = `e43fd9c` (Merge von `claude/homeintent-7.9.2`, `50f3c89`).
Vergleichsbasis: 7.9.1 (`3988ac8`). Auftrag: `sim/PROMPT_7.9.2.md`.

Die Testsession hat keinen Produktionscode geändert. Beispielsätze sind eigene
Prüfsätze. Sätze aus den verdeckten Korpora sind nur verallgemeinert
beschrieben.

## Ergebnis in Kürze

| Bereich | 7.9.1 | 7.9.2 |
|---|---|---|
| Unit-Suite | 7936 | **8492 bestanden**, 12 übersprungen, 0 Fehler |
| CI-Gates (Sprach-Eval, Signaturen, Shadow, Arbiter, Dev-Benchmarks, alle Latenzen, pyright ×4, pyflakes) | grün | **grün** (Signaturen 3827/0 geändert, Shadow 2108 EQUIVALENT, 0 SAFETY_DRIFT) |
| Live-Testbett `runner.py --strict` | 198/198 | **215/215**, HA-Log 0 Befunde |
| Eigene Live-Szenarien (eigene Formulierungen, Wirkung geprüft) | 11/11 fachlich | **13/18** (siehe Befunde; 1 Fehlschlag ist ein Testanordnungs-Artefakt) |
| Korpus 236 (verdeckt) | 175 PASS | **178 PASS** (+3: Leistungsfragen „gerade“, Messartefakt behoben) |
| Korpus 1005 (verdeckt) | 729 PASS, 14 SAFETY | 729 PASS, 15 SAFETY. Die zusätzliche Safety-Markierung ist ein **Testbett-Artefakt** (siehe unten). Es gibt 1 echte kleine Regression (B5). |
| Holdout 2 propose / auto | 64 / 69 | **65 / 70** von 81, 0 unsicher |
| Probe, Holdout 1, README, Push, Sicherheitsfälle | – | identisch zu 7.9.1 |

**Urteil:** Ein großer, sauberer Schritt.

- Alle Punkte A1–A6 aus dem Nachtest 7.9.1 sind umgesetzt.
- Die neuen Fähigkeiten B1–B5 funktionieren im Kern live.
- Die Sicherheitslage ist unverändert gut: keine Sicherheitsabweichung,
  Zugangs- und Eigentümerregeln greifen.
- Offen sind vor allem Paraphrasen-Lücken bei den neuen Fähigkeiten und ein
  grundsätzlicher Punkt: Überwachungen, die beim Anlegen schon erfüllt sind,
  melden sich nie (B1).

## Was live nachweislich funktioniert

| Bereich | Nachweis |
|---|---|
| Bestätigungston mit Wartezeit (A1) | Stehlampe meldet nach 1 s: kein TTS, genau ein Ton, gemessene Wartezeit 978 ms. Rollladen „runter“ während der Fahrt: Ton. |
| Bewässerung (A2) | „Wenn die Terrassentür aufgeht, schalte die Bewässerung für 1 Minute ein“: Das Ventil öffnet live und ist nach 65 s wieder zu. Ohne Dauer fragt HomeIntent „Wie lange …?“, Hauptventil gesperrt. |
| Markise (A6) | „Wenn die Sonne scheint …“ fragt nach der Helligkeit (mit aktuellem Messwert), Lux-Grenzwert wird verstanden. |
| Stromverbrauch (A5) | „Stromverbrauch“ ergibt „Stromverbrauch Haus“ ohne Rückfrage. |
| Verbrauch (B5) | „Wie viel Strom hat das Haus heute verbraucht?“ ergibt den Tagesverbrauch aus dem Zähler. „Wie viel verbraucht … gerade?“ ergibt Watt. |
| Urlaubsmodus (B4) | Start mit Vorschau. Während des Urlaubs löst eine Fensteröffnung genau eine Push-Nachricht an jedes Haushaltsmitglied aus. |
| Zusammenfassung (B1) | „Was ist heute passiert?“ liefert eine Ereignisliste mit Uhrzeiten. |
| Licht beim Gehen (A6) | Die Push-Nachricht nennt den Raum (Wohnzimmer). |
| Regressionen aus 7.9.0/7.9.1 | alle Befunde weiterhin behoben (Garage, Rechte, Keller, Grenzwert „geht“, Löschen, Rückfragen) |

## Befunde

### B1 – Beim Anlegen schon erfüllter Zustand meldet sich nie (wichtig)

„Gib mir Bescheid, sobald irgendeine Batterie unter 25 Prozent fällt.“ legt die
Überwachung an, obwohl zwei Batterien schon darunter liegen (Fenster Bad 14 %,
Rauchmelder oben 9 %).

- Der Auslöser feuert nur beim **Unterschreiten**. Für diese beiden Geräte kommt
  nie eine Nachricht.
- Die Vorschau sagt das nicht. Live bestätigt: Nach Setzen auf 12 % kam keine
  Nachricht.
- Das ist grundsätzlich: Jede Zustands- oder Grenzwertüberwachung, deren
  Bedingung beim Anlegen schon wahr ist, bleibt stumm, bis der Zustand einmal
  endet. Beispiele: „Fenster offen“, „Leistung über …“, „nicht erreichbar“.

**Empfehlung:**
- Die Vorschau prüft den aktuellen Zustand. Ist die Bedingung schon erfüllt,
  sagt sie es und bietet eine sofortige Meldung an („Fenstersensor Bad (14 %)
  und Rauchmelder oben (9 %) liegen schon darunter – soll ich dir das jetzt
  schicken?“).
- Für „irgendeine Batterie“ zusätzlich: eine Nachricht je Gerät beim Erreichen,
  nicht nur beim Wechsel der Gruppe.

### B2 – Urlaub lässt sich nur mit bestimmten Worten beenden

„Wir sind wieder da.“ wird nicht verstanden (live). Die Urlaubsüberwachung
schickte danach weiter Push-Nachrichten, und der Helfer blieb an. Bis zum
Enddatum bleibt das so.

„Urlaub vorbei“ funktioniert (Release-Szenario).

**Empfehlung:** Das Ende als Konstruktion lesen, nicht als Liste:
- Rückkehr: wieder da, zurück, heimgekommen, angekommen;
- Ende: vorbei, zu Ende, beenden, aus;
- dazu Urlaub, Reise, Abwesenheit.

Bei einem aktiven Urlaubsmodus heißt außerdem jedes „Ich bin wieder zuhause“:
Soll ich den Urlaubsmodus beenden?

### B3 – Zusammenfassung: Paraphrasen und Reihenfolge

- „Was ist passiert, seit ich weg war?“ wird nicht verstanden. „Während ich weg
  war“ funktioniert.
- Weitere naheliegende Formen: „seit ich gegangen bin“, „in meiner
  Abwesenheit“, „Was hab ich verpasst?“.
- „Was ist heute passiert?“ mischt die Reihenfolge („um 16:06 …; um 16:08 …;
  um 16:05 du bist gegangen …“).
  - Besser: innerhalb gleicher Wichtigkeit zeitlich sortieren.
  - Kommen und Gehen eigener Personen nur kurz nennen.
- Mehrtägige Abwesenheit: Die Überschrift nennt nur Uhrzeiten („14:00 bis
  18:00 Uhr“), kein Datum.
- **Leistung im echten Haus:** Die Zusammenfassung liest bis zu 7 Tage
  Zustandsverlauf aller Melder, Schalter und Personen ohne Filter auf
  „wichtige Änderungen“ (`significant_changes_only=False`). Das läuft im
  Executor und blockiert nicht. In großen Häusern kann die Antwort trotzdem
  spürbar dauern. Für den Haustest des Eigentümers messen; gegebenenfalls auf
  relevante Klassen und eine Obergrenze beschränken.

### B4 – Platzhalter in gesprochenen Vorschauen

Drei Vorschauen enthalten Platzhalter:
- Batterie: „… Push-Benachrichtigung …: ‚\<Gerät\>: \<Wert\> %‘“;
- Licht beim Gehen: „‚Du hast das Haus verlassen; \<Räume\> ist noch Licht an.‘“;
- nicht erreichbar: „‚\<Gerät\> ist seit 10 Minuten nicht erreichbar.‘“.

Eine Sprachausgabe liest das wörtlich vor bzw. lässt Lücken. Besser ist ein
Beispiel mit echten Namen („zum Beispiel: ‚Batterie Fenstersensor Bad: 14 %‘“
bzw. „‚… im Wohnzimmer ist noch Licht an.‘“). Die Push-Nachricht selbst ist
korrekt (live: Raum genannt).

### B5 – Regression: „Stell das Heizprogramm auf Urlaub“

Die Anfrage wird jetzt zur Urlaubs-Rückfrage („Bis wann seid ihr weg?“). In
7.9.1 war sie ein Gerätebefehl. Das Wort „Urlaub“ als **Wert** eines Programms
oder Modus eines Geräts darf den Urlaubsmodus nicht auslösen. Die Regel aus
dem Release („Schalte den Urlaubsmodus ein“ bleibt Gerätebefehl) gilt sinngemäß
für „auf Urlaub stellen“.

### B6 – Kleinigkeiten

- „Was hat heute am meisten verbraucht?“ zählt Geräte mit 0 kWh auf
  („Kaffeemaschine 0 kWh und Trockner 0 kWh“). Nullwerte weglassen bzw. nur
  nennen, was etwas verbraucht hat.
- **Ohne Recorder** (z. B. deaktiviert): Jede Verlaufs-, Verbrauchs- und
  Zusammenfassungsfrage schreibt einen vollständigen Traceback ins Log. Die
  Antwort selbst ist ehrlich. Eine einzeilige Warnung reicht.
- „Push beim Heimkommen mit Zusammenfassung“ (B1 optional) ist nicht
  umgesetzt. Die Begründung im Releasebericht ist nachvollziehbar: Es bräuchte
  einen zweiten Schreibweg. Ein Weg wäre über das vorhandene `agent_delivery`
  mit derselben Prüfung wie Überwachungen.
- Die Anwesenheitssimulation im Urlaub ist ohne 4 Tage Verlauf nicht live
  prüfbar. Getestet ist sie nur in der Unit-Suite.

### Artefakte (keine Produktbefunde)

| Fall | Ursache |
|---|---|
| Korpus 1005, zusätzliche Safety-Markierung | Während eines Satzes über die Vergangenheit schlossen sich drei Rollläden. Ursache ist eine **Testbett-Automation** (`sim/config/automations.yaml`: bei Sonnenuntergang Wohnzimmer links/rechts und Küche schließen), die genau in diesem Moment (18:58 Uhr) auslöste. HomeIntent hat nichts geschaltet; die Antwort war „nicht verstanden“. |
| Eigenes Szenario „Überwachung löschen“ | Eine HomeIntent-Überwachung aus einem früheren Szenario lebt im Goal-Store weiter, die Liste ist deshalb nicht leer. Das ist bekannt und wurde auch vom Release beschrieben. |

## Erweiterungsvorschläge

Dazu wurde stichprobenartig geprüft, was es schon gibt:
- **Vorhanden:** „Mach das rückgängig“, Kalenderabfragen, „Welche Fenster sind
  offen?“.
- **Nicht vorhanden bzw. nicht verstanden:** „Wie wird das Wetter morgen?“,
  „Wo ist Anna?“, „Spiel Musik im Wohnzimmer“.

| # | Vorschlag | Nutzen |
|---|---|---|
| E1 | **Beim Anlegen erfüllte Bedingung sofort melden** (siehe B1) | verhindert stumme Überwachungen |
| E2 | **Wetter** aus `weather.*`: „Wie wird das Wetter morgen?“, „Brauche ich heute einen Schirm?“, „Regnet es heute noch?“. Dazu Wetter als Auslöser bzw. Bedingung („Wenn Regen angesagt ist, fahr die Markise ein“) aus der Vorhersage. | häufigste Alltagsfrage, passt zu Markise und Bewässerung („nur wenn es nicht regnet“) |
| E3 | **Personen-Ort:** „Wo ist Anna?“, „Ist jemand zuhause?“, „Wann kommt Philipp heim?“ (nur Zone, Rechte wie bei der Zusammenfassung) | Haushaltsfragen, Grundlage für Ankunft-Automationen |
| E4 | **Medien:** „Spiel Musik im Wohnzimmer“, „Pause“, „Lauter“, „Was läuft gerade?“ über `media_player` (Quelle bzw. Playlist nur, wenn eindeutig) | sehr häufige Sprachbefehle |
| E5 | **Bewässerung „nur wenn es nicht geregnet hat“:** Bedingung aus Regensensor bzw. Wetter (E2) | macht A2 alltagstauglich |
| E6 | **Bestätigungston als Standard** nach dem Haustest; dazu ein **anderer Ton für „nicht ganz geklappt“** statt eines langen Satzes, wenn nur die Rückmeldung fehlt | weniger Sprache im Alltag |
| E7 | **Wöchentlicher Haus-Bericht** per Push (Batterien, Ausfälle, Verbrauch der Woche, ausgelöste Warnungen), aufbauend auf B1/B3/B5 | Überblick ohne Nachfragen |
| E8 | **Überwachungen bearbeiten:** „Ändere die Garagen-Meldung auf 15 Minuten“, „Schick die Fenster-Warnung auch an Anna“ | bisher nur löschen und neu anlegen |

## Prioritäten für 7.9.3

1. **B1/E1:** schon erfüllte Bedingungen.
2. **B5:** Regression „auf Urlaub stellen“.
3. **B2:** Urlaub beenden.
4. **B3:** Zusammenfassung (Paraphrasen, Reihenfolge, Datum).
5. **B4:** Platzhalter.
6. **B6:** Kleinigkeiten.
7. Danach Erweiterungen nach Wahl des Eigentümers (E2–E8).
