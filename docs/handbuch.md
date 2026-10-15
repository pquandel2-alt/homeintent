# HomeIntent – ausführliches Handbuch

Diese Inhalte wurden aus der früheren Langfassung der README übernommen. Die
kompakte [Projektübersicht](../README.md) führt Installation, Schnellstart und
die wichtigsten Sicherheitsgrenzen zusammen; dieses Dokument bewahrt die
ausführlichen Beispiele, Fachfunktionen, Architektur- und Entwicklungshinweise.

## Wie HomeIntent Sprache versteht

HomeIntent kombiniert deutsche Hassil-Grammatiken mit einem lokalen
symbolischen Sprach-Compiler. Ein Satz wird nicht nur mit vollständigen
Vorlagen verglichen, sondern in Bedeutungsbausteine zerlegt:

```text
Eingabe
  → Normalisierung und Äußerungsanalyse
  → Sprechakt, Modalität, Negation und Klauselrollen
  → semantisches Lexikon
  → Aktion, Ziel, Ort, Menge, Eigenschaft und Wert
  → World Model aus Home Assistant
  → Entity- und Capability-Auflösung
  → Validierung und Sicherheitsrichtlinie
  → Antwort, Rückfrage, Serviceplan oder AutomationModel
  → bestätigte Ausführung
```

Dadurch können viele Satzbestandteile ihre Position ändern:

```text
Fahre alle Rollläden im Erdgeschoss hoch.
Im Erdgeschoss bitte alle Rollläden hochfahren.
Nach oben fahren sollen im Erdgeschoss alle Rollläden.

Sind im Erdgeschoss offene Fenster?
Offene Fenster, gibt es die im Erdgeschoss?
Welche Fenster im Erdgeschoss sind noch geöffnet?

Welche Lichter im Wohnzimmer sind heller als 50 Prozent?
Im Wohnzimmer heller als 50 Prozent: Welche Lichter gibt es?
```

Das ist kein eingebautes LLM. Erlaubte Wörter und Bedeutungen bleiben
kontrolliert, testbar und reproduzierbar. Unbekannte wichtige Zusätze,
widersprüchliche Angaben oder fachlich unpassende Einheiten werden nicht
stillschweigend ignoriert.

### Natürliche Formulierungen ohne Satzschablonen

HomeIntent analysiert zusätzlich die Art einer Äußerung:
Befehl, Abfrage, Automation, Bestätigung, Korrektur oder Aussage. Außerdem
werden höfliche Wünsche, hypothetische beziehungsweise unsichere Aussagen,
Negation und die Rollen einzelner Klauseln unterschieden. Diese Information
ist ausführungsrelevant: Ein Satz mit „wenn“, ein Gedankenspiel oder ein
unsicherer Wunsch kann nicht versehentlich als sofortiger Gerätebefehl
ausgeführt werden.

Unterschiedliche sprachliche Hüllen führen zum selben geprüften semantischen
Ergebnis:

```text
Schalte das Küchenlicht ein.
Kannst du das Küchenlicht anmachen?
Wäre es möglich, das Küchenlicht einzuschalten?
Ich hätte gerne das Küchenlicht an.
Sorge bitte dafür, dass das Küchenlicht an ist.
Das Küchenlicht soll an sein.
```

Die gemeinsame Normalisierung versteht außerdem zusammengesetzte deutsche
Zahlwörter und gebräuchliche Bruchteile in passenden Wertekontexten:

```text
Stelle die Heizung auf zweiundzwanzig Grad.
Fahre den Rollladen auf drei Viertel.
Fahre den Rollladen zur Hälfte.
```

Diese Sprachschicht wird nicht nur für Licht und Rollläden verwendet. Auch
Thermostate, Mediengeräte, Ventilatoren, Staubsauger, Szenen und die weiteren
unterstützten Geräteklassen erhalten dieselbe Normalisierung und denselben
Sicherheitscheck. Kurze Folgeäußerungen wie „Kannst du es pausieren?“, „Bitte
weiterspielen“ oder „Und jetzt zur Station“ werden aus Gesprächskontext und
Operation zusammengesetzt, nicht über eine Liste vollständiger Sätze.

Bei Automationen werden Aktions-, Auslöser- und Bedingungsklauseln getrennt.
Die konkrete Aktion läuft anschließend durch denselben semantischen Compiler
und dieselben Entity-/Capability-Prüfungen wie ein direkter Befehl. Eine
reine Informationsfrage wie „Was passiert, wenn …?“ bleibt dagegen immer
lesend.

### Ähnliche Namen und natürliche Auswahl

Exakte Namen, konfigurierte und abgeleitete Aliase, Wortteile sowie eng
begrenzte Schreib-/ASR-Ähnlichkeit werden über einen gemeinsamen
Kandidatenvertrag bewertet. Mehrere gleichwertige oder ähnlich gute Ziele
werden nummeriert und mit Bereich, Etage, Alias oder nötigenfalls Entity-ID
unterscheidbar gemacht:

```text
Du: Mach das Bürolicht an.
Assist: Ich habe mehrere passende Lichter gefunden:
        1. Bürolicht im Bereich Büro 1;
        2. Bürolicht im Bereich Büro 2. Welches meinst du?
Du: Das zweite.
```

Auswahlen funktionieren per vollständigem Namen, Alias, Bereich, Etage,
Merkmal oder Nummer. Eine ungültige Antwort verwirft die Rückfrage nicht.
Vor der späteren Ausführung werden Entity und Capability erneut aus dem
aktuellen Home-Assistant-Zustand geladen. Ein einzelner nur unscharfer
Treffer benötigt weiterhin eine ausdrückliche Bestätigung; mehrere
unscharfe Treffer werden niemals geraten.

### Mengen, Orte und Ausschlüsse

HomeIntent berücksichtigt Friendly Names, Entity-IDs, Aliase, Bereiche,
Bereichs-Aliase, Etagen, Domänen, Geräteklassen und Fähigkeiten.

```text
Fahre beide Rollläden im Wohnzimmer hoch.
Schalte alle Lichter in Küche und Flur aus.
Mach alle Lichter aus außer der Stehlampe.
Schalte alle Lichter aus, außer Kücheninsel und Nachtlicht.
Mach die drei Lampen im Büro an.
```

„Ein paar“ oder „einige“ führt nie zu einer zufälligen Auswahl. HomeIntent
fragt nach den konkreten Gerätenamen.

### Gattungen, Bedürfnisse, Sichten und Diskurs (seit 7.3)

Ein Ziel ist eine Kombination aus **Gattung × Ort × Merkmal × Menge**, nicht
nur ein Name. Eine Geräte-Ontologie (Daten, keine Satzschablonen) kennt
deutsche Gattungswörter mit Plural, Umgangssprache und Komposita
(„Lampe“, „Leuchte“, „Rollo“, „Jalousie“, „Glotze“, „Wohnzimmer|licht“,
„Kinderzimmer|jalousie“, „Rauch|melder“). Orte sind Bereiche, Bereichs- und
Etagenaliase („oben“, „unten“, „im Keller“, „draußen“) – in Befehlen,
Abfragen, Automationen und Benachrichtigungen gleich.

```text
Mach die Leuchte im Kinderzimmer an.
Fahre alle Jalousien im Obergeschoss hoch.
Mach im Büro den Ventilator an.
Schalte im Wohnzimmer alle Lampen aus, bis auf die Stehlampe.
Mach das Licht im Wohnzimmer neutral weiß.
```

Im Testhaus fragt der erste Satz nach (im Kinderzimmer gibt es zwei
Lichter), der zweite fährt nur die Rollläden oben (nie das Garagentor), der
dritte antwortet „Im Büro gibt es keinen Ventilator.“ und der letzte stellt
nur Lichter mit Farbtemperatur um.

**Bedürfnisse** werden als gewünschte Wirkung verstanden; der Ort kommt aus
dem Satz, vom Satelliten oder aus dem Kontext:

```text
Ich friere.
Im Schlafzimmer bitte etwas kühler.
Hier ist es muffig.
Es ist zu laut.
Ich gehe schlafen.
```

Am Satelliten im Kinderzimmer erhöht „Ich friere.“ die Heizung dort um ein
Grad; „Hier ist es muffig.“ im Bad schaltet den Badlüfter ein; „Es ist zu
laut.“ in der Küche stellt das Küchenradio leiser; „Ich gehe schlafen.“
schlägt das Skript Gute Nacht vor.

Fragen, Verneinungen, Vergangenes und Hypothetisches („Gestern war mir
kalt“, „Wäre es kalt, …“) lösen nie eine Aktion aus.

**Situationssichten** beantworten Fragen über das Haus aus beobachteten
Zuständen und führen nie etwas aus:

```text
Ist im Erdgeschoss noch etwas an?
Ist alles abgeschlossen?
Sollte ich lüften?
Warum ist es im Büro so kalt?
Ist jemand im Büro?
Was kann ich im Wohnzimmer steuern?
Welche Räume gibt es im Keller?
Wie viele Fenster gibt es im Erdgeschoss?
Was macht das Skript Kaffee kochen?
Wofür ist das Hauptwasserventil?
```

Lüften folgt dokumentierten Schwellen (60 % Luftfeuchtigkeit, 1000 ppm
CO2); „Warum ist es kalt“ nennt nur belegte Fakten: Ist- und Sollwert,
Heizbetrieb, offene Fenster und die Außentemperatur.

**Diskurs**: Ellipsen und Verweise binden an das letzte Ziel, die letzte
Ergebnismenge oder den letzten Ort des Gesprächs:

```text
Du: Schalte die Nachttischlampe rechts ein.
Assist: Nachttischlampe rechts eingeschaltet.
Du: Die andere bitte auch.
Assist: Nachttischlampe links eingeschaltet.
```

```text
Du: Wie warm ist es im Kinderzimmer?
Du: Dort bitte wärmer.
Assist: Heizung Kinderzimmer wärmer gestellt.
```

```text
Du: Mach es im Büro wärmer.
Du: Und im Bad?
Assist: Heizung Badezimmer wärmer gestellt.
```

**Modalität**: „Lass die Kücheninsel an“ tut nichts, „Die Kaffeemaschine
kann jetzt aus“ schaltet aus, „Ich wüsste gern, ob die Haustür zu ist“ ist
eine Frage. **Zeitsprache**: „um halb sieben“, „um viertel vor neun“, „in
zwei Stunden und 30 Minuten“ (in jeder Wortstellung) und „Weck mich um
sieben mit Licht“ werden zu Automationen mit Vorschau – nie zu einer
sofortigen Aktion. Die Architektur beschreibt
[`docs/architecture-v13.md`](architecture-v13.md).

### Abstufungen und Zahlen

Prozentwerte, Grad Celsius und Gerätestufen werden intern als typisierte Werte
geführt. Relative Wörter besitzen feste, reproduzierbare Schritte:

```text
Mach die dimmbare Lampe etwas heller.
Mach die dimmbare Lampe deutlich heller.
Mach die Heizung im Büro stark wärmer.
Fahre die Rolllade halb runter.
Fahre die Rolllade komplett hoch.
```

### Erklärung statt Blackbox

Nach einem verstandenen Befehl oder während einer Automationsvorschau kann der
aufgelöste Plan abgefragt werden:

```text
Du: Schalte in Küche und Flur alle Lichter aus, außer der Kücheninsel.
Du: Was hast du verstanden?
Assist: Ich habe Folgendes verstanden: Aktion: ausschalten; Orte: Küche,
        Flur; ausgenommen: Kücheninsel; …
```

Die Erklärung verwendet bereits aufgelöste Fakten und startet keine Aktion.

## Unterstützte Geräte und Aktionen

Die tatsächliche Aktion hängt immer von den Fähigkeiten ab, die das jeweilige
Home-Assistant-Gerät meldet.

| Domäne | Unterstützte Kernfunktionen |
| --- | --- |
| `light` | ein/aus, umschalten, Helligkeit, Farbe, Farbtemperatur, relative Helligkeit |
| `switch` | ein/aus und umschalten |
| `cover` | öffnen, schließen, Position und Lamellenneigung |
| `fan` | ein/aus, Prozent, Stufe, Preset, Richtung und Oszillation |
| `climate` | Solltemperatur, HVAC-, Preset-, Lüfter- und Schwenkmodus |
| `media_player` | Wiedergabe, Pause, Stopp, Lautstärke und Quelle |
| `vacuum` | starten, pausieren, stoppen, Saugstufe und Ladestation |
| `scene` | aktivieren |
| `script` | starten |
| `lock` | verriegeln und bestätigtes Entriegeln |
| `humidifier` | ein/aus, Zielfeuchte und angebotener Modus |
| `water_heater` | Solltemperatur und angebotener Betriebsmodus |
| `select` | tatsächlich angebotene Option auswählen |
| `number`, `input_number` | Wert innerhalb der gemeldeten Grenzen setzen |
| `input_boolean` | ein/aus und umschalten |
| `button` | nach Bestätigung drücken |
| `valve` | nach Bestätigung öffnen, schließen oder positionieren |
| `lawn_mower` | starten, pausieren und zur Ladestation fahren |
| `camera` | Stream auf einem eindeutig genannten Media Player anzeigen |
| `notify` | Nachricht an ein eindeutig genanntes Notify-Ziel senden |
| `alarm_control_panel` | bestätigte Alarmaktionen; kritische Aktionen nur für Administratoren |
| `group` | explizit ausgewählte Gruppen nach Bestätigung steuern |
| `sensor`, `binary_sensor` | Zustände, Messwerte und Vergleiche abfragen |
| `calendar` | Termine lesen, anlegen und – je nach Integration – ändern oder löschen |
| `todo` | Listen lesen und Einträge verwalten |
| `timer` | starten, ändern, pausieren, fortsetzen, beenden und abfragen |

Beispiele:

```text
Mach das Wohnzimmerlicht an.
Fahre alle Rollläden im Erdgeschoss auf 50 Prozent.
Stelle den Ventilator auf Stufe 3.
Stelle die Heizung im Wohnzimmer auf 21 Grad.
Wähle beim Heizprogramm Eco.
Pausiere die Wiedergabe auf dem Wohnzimmer TV.
Schicke den Saugroboter zur Ladestation.
Aktiviere die Szene Filmabend.
Stelle den Luftbefeuchter auf 45 Prozent.
Zeige die Einfahrtkamera auf dem Wohnzimmer TV.
```

## Zustände, Messwerte und Verlauf

Abfragen sind lesend und erzeugen keinen steuernden Serviceplan.

### Aktueller Zustand

```text
Ist das Badezimmerfenster geschlossen?
Welchen Zustand hat das Badezimmerfenster?
Sind alle Rollläden hochgefahren?
Sind im Erdgeschoss offene Fenster?
Ist irgendein Fenster offen?
Ist kein Fenster offen?
Wie viele Fenster sind im Erdgeschoss geöffnet?
Wo sind Fenster offen?
Was ist im Badezimmer eingeschaltet?
Ist das Radio in der Küche an?
Läuft der Saugroboter?
Was macht der Mähroboter?
```

Zustandsfragen sind für jede Domäne nur dann freigeschaltet, wenn HomeIntent
deren Home-Assistant-Zustände eindeutig semantisch abbilden kann. Dazu zählen
neben Licht, Schaltern, Rollläden und Binärsensoren auch Ventilatoren,
Heizungen, Media Player, Staubsauger, Luftbefeuchter, boolesche Helfer,
Ventile und Mähroboter. Szenen und Tasten besitzen dagegen keinen
dauerhaften an/aus-Zustand; HomeIntent erfindet dafür keine Antwort.

### Messwerte und Vergleiche

Unterstützt werden unter anderem Temperatur, Luftfeuchtigkeit, Batterie,
Leistung, Energie und Helligkeit.

```text
Wie hoch ist die Temperatur im Erdgeschoss?
Welche Temperatur hat das Erdgeschoss?
Wie warm ist es im Wohnzimmer?
Wie hoch ist die Luftfeuchtigkeit im Badezimmer?
Welche Batterien oben sind unter 20 Prozent?
Welche Lichter sind mindestens 50 Prozent hell?
Welches Gerät verbraucht gerade am meisten Strom?
```

Mehrere passende Sensoren werden einzeln genannt. Einen Mittelwert bildet
HomeIntent nur bei einer ausdrücklichen Durchschnittsfrage.

### Recorder-Statistiken

Für eindeutig benannte numerische Sensoren fragt HomeIntent Home Assistants
`recorder.get_statistics` ab. Unterstützt werden Mittelwert, Minimum, Maximum
und Veränderung für heute, gestern, diese Woche, letzte Woche und den aktuellen
Monat.

```text
Wie hoch war die durchschnittliche Temperatur im Wohnzimmer gestern?
Was war heute der höchste Wert vom Stromverbrauch Haus?
Wie hat sich der Energiezähler diese Woche verändert?
War die Wohnzimmer Temperatur gestern niedriger als heute?
Wie oft war das Badezimmerfenster gestern offen?
Wie lange war das Badezimmerfenster heute geöffnet?
```

Fehlen Recorder-Daten, meldet HomeIntent das, statt aus dem aktuellen Zustand
einen historischen Wert zu erfinden.

Zustandswechsel und Zustandsdauer werden über Home Assistants Recorder-Historie
ermittelt. Vergleiche zwischen heute und gestern beziehungsweise dieser und
letzter Woche verwenden zwei klar begrenzte Statistikzeiträume.

## Gesprächskontext und Rückfragen

Der Kontext wird getrennt pro Assist-Konversation gespeichert. Er besteht aus
aufgelösten semantischen Fakten, nicht aus einer unbegrenzten Unterhaltung.

```text
Du: Wie hoch ist die Temperatur im Erdgeschoss?
Assist: 21,6 Grad.
Du: Kannst du die Temperatur auf 22 Grad erhöhen?
Assist: Erdgeschoss Heizung auf 22 Grad gestellt.

Du: Wie warm ist es im Wohnzimmer?
Du: Und in der Küche?

Du: Schalte das Wohnzimmerlicht ein.
Du: Und jetzt wieder aus.

Du: Schalte das Licht hier ein.
Assist: Bürolicht eingeschaltet.
```

Wenn die Assist-Anfrage eine `device_id` oder `satellite_id` enthält und das
Gerät einem Bereich zugeordnet ist, setzt HomeIntent „hier“, „in diesem Raum“
und „in diesem Zimmer“ auf diesen Bereich um. Ohne eindeutige Bereichszuordnung
wird kein Ort geraten.

Nach einer reversiblen direkten Aktion ist außerdem möglich:

```text
Du: Fahre die Büro-Rolllade auf 40 Prozent.
Du: Mach das rückgängig.
```

HomeIntent speichert dafür nur den unmittelbar vorherigen, tatsächlich
beobachteten Zustand im kurzlebigen Gesprächskontext. Skripte, Szenen,
Benachrichtigungen und sicherheitskritische Aktionen werden nicht automatisch
rückgängig gemacht.

Fehlt genau eine benötigte Angabe, fragt HomeIntent gezielt nach:

```text
Du: Stelle die Heizung im Büro ein.
Assist: Auf welche Temperatur soll ich die Heizung im Büro stellen?

Du: Stelle auf 22 Grad.
Assist: Welche Heizung meinst du?

Du: Mach ein paar Lichter an.
Assist: Welche Geräte genau? Bitte nenne ihre Namen.
```

Die normale Kontextdauer ist in den Optionen zwischen 10 und 600 Sekunden
einstellbar. Bestätigungen und mehrstufige Dialoge besitzen eine eigene,
begrenzte Frist.

## Kalender

HomeIntent verwendet Home Assistants Kalenderdienste. Ein über CalDAV
eingebundener Apple-/iCloud-Kalender funktioniert, wenn die `calendar.*`-
Entität die benötigte Funktion meldet und für HomeIntent freigegeben ist.

### Termin in einem Satz erstellen

```text
Trag nächsten Dienstag um 10 Uhr für eine Stunde Zahnarzt ein.
Erstelle am 25. August von 18 bis 20 Uhr den Termin Geburtstag.
Schreibe Urlaub morgen ganztägig in meinen Kalender ein.
```

### Fehlende Angaben im Dialog ergänzen

```text
Du: Trag einen Termin ein.
Assist: Wie soll der Termin heißen?
Du: Zahnarzt.
Assist: An welchem Datum findet der Termin statt?
Du: Nächsten Dienstag.
Assist: Um wie viel Uhr beginnt der Termin?
Du: Um 10 Uhr.
Assist: Wann endet der Termin oder wie lange dauert er?
Du: Eine Stunde.
```

Vor `calendar.create_event` nennt HomeIntent Titel, Datum, Beginn, Ende und
Kalender und verlangt eine Bestätigung. Bei mehreren beschreibbaren Kalendern
wird nach dem Zielkalender gefragt.

### Kalender abfragen und verwalten

```text
Was steht morgen in meinem Kalender?
Welche Termine habe ich am Wochenende?
Wann ist mein Zahnarzttermin?
Habe ich morgen zwischen 14 und 16 Uhr Zeit?
Verschiebe den Zahnarzttermin auf 11 Uhr.
Benenne den Termin Planung in Projektplanung um.
Lösche den Termin Zahnarzt.
```

Ändern und Löschen funktionieren nur, wenn die Kalenderintegration eine
Ereignis-ID und die entsprechenden Home-Assistant-Features bereitstellt.
Mehrdeutige Treffer führen zu einer Auswahlfrage. Schreibende und löschende
Vorgänge werden bestätigt.

## Aufgabenlisten und Timer

### Mehrere Listeneinträge in einem Satz

```text
Füge Milch, Brot, Butter und Äpfel zur Einkaufsliste hinzu.
Was steht auf meiner Einkaufsliste?
Markiere Milch und Brot als erledigt.
Entferne Milch und Brot von der Einkaufsliste.
Lösche alle erledigten Einträge.
Verschiebe Milch von der Einkaufsliste auf die Arbeitsliste.
```

Kommas, „und“ und „sowie“ trennen Einträge. Doppelte Angaben werden innerhalb
eines Befehls entfernt. HomeIntent kann außerdem Fälligkeit, Beschreibung und
sichtbare Prioritätspräfixe verwalten. Bei mehreren Listen wird nachgefragt.

### Timer

```text
Du: Stelle einen Timer für 20 Minuten.
Assist: Wie soll der Timer heißen?
Du: Pizza.
Assist: Timer „Pizza“ für 20 Minuten gestartet.

Stelle einen Timer für fünf Minuten mit dem Namen Nudeln.
Stelle einen Nudeltimer für acht Minuten.
Welche Timer laufen?
Wie lange läuft der Timer Pizza noch?
Pausiere den Timer Nudeln.
Setze den Nudeltimer fort.
Verlängere den Timer Pizza um fünf Minuten.
Lösche den Timer Pizza.
Brich den Nudeltimer ab.
Lösche alle Timer.
```

Jeder neue Assist-Timer bekommt einen Namen. Nennt der Satz keinen, fragt
HomeIntent danach; „ohne Namen“ startet ihn unbenannt. Ist der Name schon
vergeben, fragt HomeIntent nach einem anderen. Gesprochene Namen werden
tolerant zugeordnet („Nudeltimer“ findet den Timer „Nudeln“). Laufen mehrere
Timer und nennt ein Befehl keinen Namen, fragt HomeIntent „Welchen Timer
meinst du: Nudeln oder Pizza?“; die Antwort darf der Name oder „der erste“
sein. „Lösche alle Timer“ wird immer erst bestätigt.

Timer ohne benannten `timer.*`-Helper laufen über Home Assistants native
Assist-Timerverwaltung. Unterstützt der aufrufende Sprachsatellit Timer,
erhält er das native Ablaufereignis einschließlich Timername. Für andere
Assist-Clients verwendet HomeIntent die unter den Agentenoptionen konfigurierte
TTS-Engine und die ausgewählten Medienplayer; dabei wird zuerst der Name
gesprochen („Der Timer Nudeln ist abgelaufen.“) und danach der optional
konfigurierte Hinweiston abgespielt. Fehlt sowohl native
Timerunterstützung als auch ein vollständiges TTS-Ziel, lehnt HomeIntent den
Start ab, statt einen unhörbaren Timer anzulegen. Vorhandene `timer.*`-Helper
bleiben für benannte Haushalts-Timer kompatibel.

### Fertigstellungszeiten von Geräten

```text
Wann ist die Waschmaschine fertig?
```

HomeIntent beantwortet diese Frage ausschließlich aus einem für Assist
freigegebenen `sensor.*` mit `device_class: timestamp` und semantisch passender
Fertigstellungszeit. Der aktuelle Sensorwert wird lokal formatiert. Mehrere
passende Maschinen führen zu einer Rückfrage; fehlende oder ungültige Werte
werden nicht geschätzt. Dieser Lesepfad kann keinen Serviceplan erzeugen.

## Automationen erstellen

HomeIntent erzeugt echte Home-Assistant-Automationen. Parsing, Validierung und
Vorschau verändern noch nichts. Erst ein ausdrückliches „Ja“ startet Generator
und Executor.

### Zeitversetzte Einmal-Aufträge

```text
Fahre in 30 Sekunden die Rolllade im Büro auf 50 Prozent.
Schalte in fünf Minuten das Küchenlicht ein.
Fahre in zwei Stunden und 30 Minuten die Rollläden herunter.
```

Der relative Zeitpunkt wird bei der Bestätigung in einen absoluten lokalen
Zeitpunkt umgerechnet. Die Automation erhält einen sekundengenauen Zeittrigger,
eine Datumsbegrenzung und eine abschließende Self-Delete-Aktion. Nach
erfolgreicher Ausführung entfernt HomeIntent Automation und Metadaten wieder.

### Kalendarische Einmal-Aufträge

```text
Fahre morgen um 8 Uhr die Rolllade hoch.
Schalte heute Abend um 20 Uhr das Licht aus.
Starte am Samstag um 10 Uhr den Staubsauger.
Aktiviere am 25. August um 18 Uhr die Szene Filmabend.
Erinnere mich übermorgen früh an die Mülltonnen.
```

„Früh“ verwendet einen festen dokumentierten Zeitpunkt von 08:00 Uhr.
Vergangene und bei einer Zeitumstellung nicht existierende lokale Zeitpunkte
werden abgelehnt.

### Zustands-, Sonnen-, Zahlen- und Kalenderauslöser

```text
Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein.
Wenn das Küchenfenster zehn Minuten offen bleibt, schalte das Licht ein.
Wenn die Temperatur unter 18 Grad fällt, schalte die Heizung ein.
Kannst du mich benachrichtigen, wenn die Außentemperatur 28 Grad beträgt?
Kannst du Philipp benachrichtigen, wenn die Außentemperatur 28 Grad beträgt?
Bei Sonnenuntergang schalte die Außenbeleuchtung ein.
Zehn Minuten vor dem Termin Müllabfuhr erinnere mich.
Wenn der Termin Urlaub beginnt, aktiviere die Szene Abwesend.
```

Zustandsauslöser und Zustandsbedingungen verwenden denselben semantischen
Predicate-Compiler wie direkte Zustandsfragen. Kalenderauslöser können Start,
Ende, Offset und eine Titelbedingung enthalten.

Eine exakte numerische Formulierung mit „beträgt“, „erreicht“ oder „hat“ wird
nicht fälschlich zu „größer als“ beziehungsweise „kleiner als“ gerundet.
HomeIntent erzeugt dafür einen eng begrenzten Template-Trigger. Bei einem
genannten Empfänger wird nur dann ein konkretes `notify`-Ziel verwendet, wenn
es eindeutig über Friendly Name oder Alias aufgelöst werden kann.

### Wiederholungen, Zeiträume und Laufbegrenzungen

```text
Jeden Werktag um 7 Uhr schalte das Küchenlicht ein.
Jeden zweiten Samstag um 10 Uhr schalte das Küchenlicht ein.
Jeden Montag um 8 Uhr, nur zwischen Oktober und März, schalte das Licht ein.
Wenn das Fenster innerhalb von fünf Minuten zweimal geöffnet wird, schalte das Licht ein.
Wiederhole die Automation für Küchenlicht nur dreimal.
```

„Bleibt N Minuten“ verwendet Home Assistants natives Trigger-Feld `for`.
„Zweimal innerhalb von …“ wartet nach dem ersten Ereignis begrenzt auf ein
zweites passendes Ereignis. Laufbegrenzte Automationen zählen erfolgreiche
Ausführungen und löschen sich nach dem letzten Lauf.

### Bedingungen und Aktionsfolgen

Das `AutomationModel` unterstützt mehrere Trigger, logische Bedingungen,
sequentielle oder parallele Aktionen, Verzögerungen, begrenztes Warten und
`if/then/else`-Verzweigungen. Unterstützte Zeit-, Datums-, Wochentags-, Sonnen-,
Zustands-, Anwesenheits- und numerische Bedingungen werden in native
Home-Assistant-Strukturen beziehungsweise eng begrenzte Templates übersetzt.

### Geführter Automationsdialog

Mit „Erstelle eine Automation“ startet ein strukturierter Dialog. HomeIntent
fragt nach Auslöser, optionaler Bedingung, Aktion und Laufzeit. Jeder Schritt
wird sofort in das typisierte `AutomationModel` übersetzt; freier Dialogtext
wird nicht als ausführbarer Code gespeichert.

### Erinnerungen

```text
Erinnere mich in 30 Minuten an die Waschmaschine.
Erinnere mich morgen um 18 Uhr an den Elternabend.
Sag Philipp morgen um 8 Uhr Bescheid, dass das Fenster offen ist.
Erinnere mich in 30 Minuten an die Waschmaschine, aber nicht zwischen 22 und 7 Uhr.
```

„Mich“ verwendet eine lokale persistente Benachrichtigung. Eine Person wird
nur verwendet, wenn genau ein passendes freigegebenes `notify`-Ziel existiert.
Eine ausdrücklich genannte Ruhezeit verschiebt eine einmalige Erinnerung auf
das Ende der Ruhezeit und wird bereits in der Vorschau genannt.

## Automationen verwalten

HomeIntent verwaltet ausschließlich eindeutig zugeordnete Automationen und
fragt bei mehreren Treffern nach.

```text
Zeige nur HomeIntent-Automationen.
Wie viele HomeIntent-Automationen sind aktiv?
Welche einmaligen Aufträge sind noch geplant?
Wann wird die Rolllade gefahren?
Welche Automation steuert die Büro Rolllade?
Was passiert, wenn das Küchenfenster geöffnet wird?
Warum wurde die Automation für Büro Rollladen nicht ausgelöst?

Aktiviere die Automation für Küchenlicht.
Deaktiviere die Automation für Küchenlicht.
Lösche die Automation für Küchenlicht.
Verschiebe den Auftrag auf 20 Uhr.
Lösche alle abgelaufenen HomeIntent-Automationen.

Ändere bei der Automation für Küchenlicht nur die Aktion.
Füge der Automation für Küchenlicht eine weitere Aktion hinzu.
Verschiebe bei der Automation für Küchenlicht die zweite Aktion an die erste Stelle.
Ändere den Auslöser der Automation für Küchenlicht auf Sonnenuntergang.
Füge der Automation für Küchenlicht die Bedingung hinzu, dass jemand zuhause ist.
Entferne die zweite Bedingung der Automation für Küchenlicht.
Lösche alle Bedingungen der Automation für Küchenlicht.

Dupliziere die Automation für Küchenlicht.
Pausiere die Automation für Küchenlicht bis morgen 20 Uhr.
Mache die letzte HomeIntent-Automationsänderung rückgängig.
```

Strukturänderungen an einmaligen Aufträgen werden abgelehnt, wenn dadurch Zeit-
oder Lebenszyklusmetadaten inkonsistent werden könnten. Löschen, Verschieben,
Bereinigung, Laufbegrenzung und Rücknahme benötigen eine Bestätigung.

## Beständigkeit und Kategorie „Homeintent“

Neue HomeIntent-Automationen erhalten die Home-Assistant-Kategorie
**Homeintent**. Das gilt für dauerhafte, zustandsbasierte einmalige und
zeitversetzte Automationen.

Beim Start gleicht HomeIntent eigene YAML-Einträge, Metadaten und Kategorien
ab. Fehlende Kategoriezuordnungen werden repariert und verwaiste Metadaten
entfernt.

Schreibvorgänge auf `automations.yaml` sind abgesichert durch:

- eine gemeinsame Sperre aller Executor-Instanzen,
- einen Fingerabdruckvergleich unmittelbar vor dem Speichern,
- Abbruch statt Überschreiben bei konkurrierenden Änderungen,
- ein dauerhaftes Transaktionsjournal,
- kontrolliertes Rollback nur bei unverändertem HomeIntent-Schreibstand,
- Abgleich nach `automation.reload` und
- einen begrenzten Verlauf der letzten zehn Automationsstände.

## Sicherheit

### Niemals bei Mehrdeutigkeit raten

Mehrere gleichwertige Ziele führen zu einer Rückfrage oder Ablehnung. Die
erste gefundene Entität wird nicht zufällig verwendet.

### Fähigkeiten vor Ausführung prüfen

Eine Positionsaktion benötigt Positionsunterstützung, eine Farbe ein
farbfähiges Licht und ein Modus muss in den aktuell gemeldeten Optionen des
Geräts enthalten sein.

### Zentrale Ausführungsrichtlinie

In den Integrationsoptionen lassen sich einstellen:

- die Risikostufe, ab der eine Bestätigung erforderlich ist,
- die maximale Zahl gleichzeitig steuerbarer Ziele,
- ob Nicht-Administratoren kritische Aktionen ausführen dürfen,
- ob Nicht-Administratoren Automationen erstellen dürfen,
- welche freigegebenen Entitäten ausschließlich lesbar sind,
- welche Benutzer überhaupt Geräte steuern dürfen,
- welche Entitäten ausschließlich Administratoren steuern dürfen,
- welche zusätzlichen lokalen Sprach-Aliase gelten und
- wie lange normaler Dialogkontext gültig bleibt.

Nur-Lesen-Entitäten bleiben für Fragen und Recorder-Abfragen sichtbar. Direkte
Befehle und neu bestätigte HomeIntent-Automationen dürfen sie nicht steuern.

Zusätzliche Aliase werden in den Optionen zeilenweise und explizit definiert:

```text
Leselampe = light.wohnzimmer_sofa
Sofalicht = light.wohnzimmer_sofa
```

Ein Alias darf nie auf mehrere Entity-IDs zeigen. Für einen gemeinsamen Namen
mehrerer Geräte sollte stattdessen eine echte Home-Assistant-Gruppe freigegeben
werden.

### Skripte, Szenen und Gruppen: transitive Prüfung (seit 7.3.1)

Ein Skript, eine Szene, eine Gruppe oder eine per `automation.trigger`
ausgelöste Automation schaltet mehr als die eine äußere Entität. HomeIntent
liest deshalb vor jeder Ausführung ihre Konfiguration nur lesend aus und bildet
einen **EffectGraph**: alle Aktionen aller Zweige (`if`, `choose`, `parallel`,
`repeat`, auch die gerade nicht zutreffenden), verschachtelte Skripte und
Szenen (mit Zyklenschutz, höchstens 8 Ebenen), Gruppenmitglieder,
Geräte-Aktionen und Ziele über `device_id`, `area_id`, `floor_id` oder
`label_id` – aufgelöst genau so, wie Home Assistant sie auflöst, und nur in der
Domäne der Aktion (`button.press` auf eine Etage = alle Buttons dieser Etage).

Für diese **wirksamen Ziele** gelten dieselben Regeln wie für direkte Befehle:

- Nicht freigegebene wirksame Ziele regelt seit 7.8.2 die Option
  `routine_unexposed_effects`:
  - `allow` (Standard): Ein freigegebenes Skript, eine Szene oder Gruppe, die
    du ausdrücklich nennst („Aktiviere Schlafen“, „Schalte Ambiente ein“),
    läuft – wie bei Home Assistants eigenem Assist. Fragt HomeIntent aus
    anderem Grund ohnehin nach (Risiko, nicht prüfbarer Schritt), nennt die
    Frage die nicht freigegebenen Geräte.
  - `confirm`: HomeIntent fragt vorher und nennt die Geräte.
  - `deny`: HomeIntent lehnt ab und nennt die Geräte (Verhalten bis 7.8.1).
  - In jedem Modus abgelehnt: nicht freigegebene Schlösser, Alarmanlagen,
    Rollläden/Tore und Ventile (deren Art sieht HomeIntent ohne Freigabe
    nicht), indirekte Aussagen, abgeleitete Routinen, proaktive und
    zeitversetzte Ausführungen sowie Automationen.
- Nur-Lesen, Nur-Admin und die maximale Zielzahl zählen die wirksamen Ziele.
- Das Risiko ist das höchste Risiko aller wirksamen Effekte (Schloss im
  `choose`-Zweig → HIGH, Alarmanlage → CRITICAL).
- Schritte, deren Wirkung sich nicht statisch bestimmen lässt (Vorlagen im
  Ziel, `event:`, `python_script`, `shell_command`, `rest_command`, Ziele in
  Dienstdaten fremder Dienste, unlesbare Konfiguration), gelten nie als LOW.
  Standard ist Ablehnen (`effect_graph_unknown: deny`); mit `confirm` fragt
  HomeIntent nach und sagt „Schritt ‚…‘ kann ich nicht prüfen“. Ohne
  anwesenden Nutzer (Daueranweisung, proaktiv, zeitversetzt) wird immer
  abgelehnt.
- Automationen, die durch die Wirkung ausgelöst werden könnten, erscheinen nur
  als Hinweis („Kann Automation X auslösen“), nicht im Risiko.

Der EffectGraph wird im Executor unmittelbar vor dem Dienstaufruf neu gebaut,
auch nach einem „Ja“. Ein Skript, das zwischen Rückfrage und Bestätigung
geändert wurde, wird also mit seinem neuen Inhalt geprüft. Von HomeIntent
angelegte Automationen, die ein Skript oder eine Szene ausführen, werden beim
Anlegen geprüft. **Wird das Skript später geändert, prüft HomeIntent die
Automation nicht erneut**, denn sie läuft in Home Assistant ohne HomeIntent.

Beispiel: „Aktiviere Gute Nacht.“ → „Das Skript „Gute Nacht“ schaltet auch
Geräte, die für HomeIntent nicht freigegeben sind: Saugroboter Reinigung
starten und Brandmelder Flur Selbsttest. Der Schritt ‚Rolladen Runterfahren‘
drückt alle Buttons im Erdgeschoss. Ich habe nichts ausgeführt.“

Routinen, die HomeIntent nur ableitet („Ich gehe schlafen“, „Filmabend“,
„Starte die Schlafroutine“ für ein Skript namens „Schlafen“), werden nie allein
wegen Namensähnlichkeit gestartet, auch Szenen nicht: HomeIntent schlägt sie
vor und wartet auf „Ja“.

### Benutzergebundene Bestätigungen

Wenn Home Assistant eine Benutzer-ID bereitstellt, kann nur derselbe Benutzer
eine offene Service- oder Automationserstellungsbestätigung abschließen.
Sprach-Pipelines ohne authentifizierte Benutzerkennung bleiben aus
Kompatibilitätsgründen über die Integrationsoptionen steuerbar.

### Audit und Diagnose

Das Laufzeit-Audit enthält maximal 250 Einträge mit Zeitpunkt, Dienst,
Entity-ID und gekürztem Benutzer-Hash. Es enthält keinen gesprochenen Text und
keine Service-Daten und beginnt nach einem Neustart neu.

Die bewusst heruntergeladene Home-Assistant-Diagnose enthält nur technische
Kennzahlen und Richtlinieneinstellungen, aber keine Äußerungen, Entity-IDs oder
Zustände.

Der gesprochene Bereitschaftscheck „Ist HomeIntent bereit?“ arbeitet dagegen
nur mit den Entities, die im aktuellen Assist-Turn ohnehin sichtbar sind. Er
hilft dabei, fehlende Bereichszuordnungen und noch nicht unterstützte Domänen
zu finden, bevor eine konkrete Formulierung getestet wird.

## Installation über HACS

HomeIntent wird derzeit als benutzerdefiniertes HACS-Repository installiert.
Voraussetzung ist Home Assistant **2026.4.0** oder neuer.

1. Öffne **HACS → Integrationen**.
2. Öffne oben rechts **Benutzerdefinierte Repositories**.
3. Trage ein:

   ```text
   https://github.com/pquandel2-alt/homeintent
   ```

4. Wähle den Typ **Integration**.
5. Suche nach **HomeIntent** und installiere die aktuelle Version.
6. Starte Home Assistant neu.
7. Öffne **Einstellungen → Geräte & Dienste → Integration hinzufügen**.
8. Füge **HomeIntent** hinzu.

Danach erscheint in der Seitenleiste **🧠 HomeIntent** – das Learning Center
(Wissen & Autonomie). Eingerichtet wird HomeIntent weiterhin unter
**Einstellungen → Geräte & Dienste → HomeIntent → Konfigurieren**; das
Learning Center zeigt, was HomeIntent weiß und darf, die Optionen legen fest,
wie HomeIntent arbeitet.

Bei einem Update über HACS anschließend Home Assistant neu starten. Wenn eine
ältere Installation ungewöhnliches Verhalten zeigt, die Integration einmal
neu laden; ein vollständiges Entfernen ist normalerweise nicht erforderlich.

**Hinweis für bestehende Installationen mit der alten Domain `ha_nlu`:**
Vor 5.0.1 konnte HACS wegen eines Packaging-Fehlers das Legacy-Verzeichnis
`ha_nlu` statt `homeintent` installieren. Ein normales Update über HACS auf
5.0.1 (kein Neuhinzufügen des Repositories nötig) plus Neustart genügt, um
danach zuverlässig `custom_components/homeintent` zu installieren. Bestehende
`ha_nlu`-Config-Einträge bleiben dabei erhalten und funktionieren über den
Kompatibilitäts-Shim weiter. Details:
[`docs/homeintent-rename-migration.md`](homeintent-rename-migration.md).

## Assist einrichten

1. Öffne die Einstellungen der gewünschten Assist-Pipeline.
2. Wähle **HomeIntent** als Conversation Agent.
3. Gib die benötigten Entitäten für Assist frei.
4. Öffne bei Bedarf die Optionen von **HomeIntent** für eine feste Auswahl
   und die Sicherheitsrichtlinien.

Ohne feste Auswahl liest HomeIntent die aktuell für Assist freigegebenen
Entitäten bei jedem Gesprächszug neu. Eine gespeicherte feste Auswahl wird
dagegen nicht automatisch um später hinzugefügte Kalender, Listen, Timer oder
Geräte ergänzt.

Für zuverlässige Ergebnisse:

- eindeutige Friendly Names verwenden,
- Bereiche und Etagen korrekt zuordnen,
- sinnvolle Entity- und Bereichs-Aliase hinterlegen,
- Assist-Satelliten beziehungsweise verwendete Geräte einem Bereich zuordnen,
- Geräteklassen korrekt setzen und
- nur benötigte Entitäten freigeben.

## Bekannte Grenzen

- Die mitgelieferte Sprachlogik ist derzeit deutsch.
- Englische UI- und Diensttexte sind vorhanden; die eigentliche NLU-Grammatik
  und die dynamischen Sprachantworten bleiben derzeit deutsch.
- HomeIntent ist kein allgemeiner Chatbot.
- „Nebenan“ und „drüben“ werden ohne hinterlegte Raumbeziehungen nicht
  geraten; Home Assistant stellt dafür standardmäßig kein Adjazenzmodell bereit.
- Relative Einmal-Aufträge unterstützen Sekunden, Minuten und Stunden bis
  maximal 59 Stunden sowie Kombinationen aus zwei Zeiteinheiten.
- Ein verpasster einmaliger Zeitpunkt wird nach einem Home-Assistant-Ausfall
  aus Sicherheitsgründen nicht verspätet ausgeführt.
- Kalendarische Sprache deckt die dokumentierten Muster ab, ist aber kein
  universeller Parser für jede denkbare deutsche Datumsform.
- Neue wiederkehrende Kalendertermine lassen sich über
  `calendar.create_event` nicht integrationsübergreifend portabel erzeugen.
- Integrationsspezifische Device-Trigger und Device-Bedingungen werden ohne
  vollständiges HA-Subtypschema abgelehnt.
- Kalenderänderungen hängen von Ereignis-ID und den Features der konkreten
  Kalenderintegration ab.
- Eine Geräteaktion wird nur angeboten, wenn die benötigte Fähigkeit im
  aktuellen Home-Assistant-Zustand erkennbar ist.
- Hassil-Grammatiken bleiben als schneller Spezial- und Kompatibilitätspfad
  neben dem semantischen Compiler bestehen.
- Der lokale Dokumentadapter indexiert TXT, Markdown und begrenzte
  textbasierte PDFs. Verschlüsselte oder reine Scan-PDFs werden nicht per OCR
  interpretiert.
- Proaktive Sprachausgabe im Raum braucht konfigurierte, aktuelle Raumsensoren
  und genau einen Sprachsatelliten pro Bereich; sonst wird privat per Push
  zugestellt. Die Aktualität stützt sich auf die Zeitstempel von Home
  Assistant.
- Push-Knöpfe gibt es nur für Companion-App-Benachrichtigungen, deren Gerät an
  den Benutzer gebunden und in der Geräteregistrierung eingetragen ist. Das
  Token belegt die Zustellung an dieses Gerät, nicht, wer es beim Tippen in der
  Hand hält; wirksam ist es ohnehin nur für den angemeldeten Empfänger.
- Die meisten Sprachsatelliten melden keinen angemeldeten Benutzer. Eine
  anonyme Antwort gilt deshalb nur für Haushaltsfragen auf dem Satelliten, der
  gefragt hat, und unterliegt den Regeln für Nicht-Administratoren.
- Daueranweisungen decken derzeit genau eine Satzform ab (Licht aus, wenn
  niemand zu Hause ist); alles andere bleibt eine normale Automation.

## Architektur

Proaktive Hinweise (V12) laufen als zusätzliche Entscheidungsschicht über die
bestehende Kette; Details in [docs/architecture-v12.md](architecture-v12.md):

```text
HA-Zustand ─► Situation ─► V11-Vorhersage (nur gelesen) ─► Priorität/Privatsphäre
  ─► Unterbrechung sinnvoll? ─► Aufmerksamkeit ─► Raum + Satellit ─► Kanal
  ─► Vorschlag (keine Aktion) ─► Antwort ─► V10-Planer/-Richtlinie/-Ausführung
  ─► Wirkungsprüfung ─► GoalRun ─► V11-Erfahrung
```

```text
Home Assistant Assist
        │
        ▼
Loss-aware Language Frontend
        │
        ▼
German Structural Analysis + SemanticGraph
        │
        ▼
MeaningCandidates + Semantic Interpreter + UnderstandingOutcome
        │
        ├── semantischer Direktbefehl / Query / Dialog
        │     → SemanticFrame
        │     → WorldModel + Constraint Resolver
        │     → Capability- und Command-Validator
        │     → Execution Policy
        │     → ServiceCallPlan oder QueryResult
        │
        └── Automation
              → Trigger-/Condition-/Action-Semantik
              → AutomationModel
              → AutomationValidator
              → Vorschau + Benutzerbestätigung
              → HA Automation Generator
              → AutomationExecutor
              → Journal + Reload + Metadatenabgleich
```

Parser führen keine Home-Assistant-Dienste direkt aus. Sprachverständnis,
Auflösung, Validierung, Richtlinie, Vorschau und Ausführung bleiben getrennt
testbar.

Der direkte Command-Pfad projiziert unterstützte relative Filter,
Ausschlüsse, Shared-Predicate-Targets und unabhängige vollständige Prädikate
strukturiert aus dem bereits analysierten Dokument. Für diese Shapes wird
kein deutscher Zwischensatz rekonstruiert. `SemanticFrame` bleibt die
Compatibility-Projektion für Validator, Policy und ServiceMapper.
Entity-/Raumvergleiche und belegte Same-Area-Queries nutzen den vorhandenen
`QueryExecutor` und den indexierten `HouseGraph` produktiv. Automationen
beziehen ihre äußeren Trigger-/Condition-/Action-Grenzen aus der gemeinsamen
Strukturanalyse. Temporalbedeutung bleibt erhalten und blockiert den
generischen Sofortpfad; eindeutiger Entity-Repair wird als Replacement
projiziert. Nicht verlustfrei abbildbare Graphformen bleiben ausdrücklich
`UNSUPPORTED` oder verlangen eine Klärung.

Weitere Dokumentation:

- [`docs/jarvis-core.md`](jarvis-core.md)
- [`docs/security-privacy.md`](security-privacy.md)
- [`docs/agent-configuration.md`](agent-configuration.md)
- [`docs/proactive-agent.md`](proactive-agent.md)
- [`docs/architecture-v7.md`](architecture-v7.md)
- [`docs/architecture-v8-audit.md`](architecture-v8-audit.md)
- [`docs/architecture-v8.md`](architecture-v8.md)
- [`docs/architecture-v8-integration.md`](architecture-v8-integration.md)
- [`docs/architecture-v9.md`](architecture-v9.md)
- [`docs/natural-language-roadmap-v7.md`](natural-language-roadmap-v7.md)
- [`docs/quality-checklist.md`](quality-checklist.md)
- [`docs/troubleshooting.md`](troubleshooting.md)
- [`docs/learning-center.md`](learning-center.md)

## Entwicklung und Tests

```bash
python -m pip install --requirement requirements-dev.txt
python -m pytest -q
python -m pytest -q --cov=custom_components/homeintent --cov-report=term-missing
```

Options Flow und Learning Center gegen ein echtes Home Assistant (eigene
Umgebung mit Python ≥ 3.14.2, getrennt von der Stub-basierten Suite):

```bash
python -m pip install --requirement requirements-ha-test.txt
python -m pytest -q tests_ha
```

Geprüfter Release-Stand von Version 7.9.9 (Draft; keine Freigabe, solange die
vollständigen Pflicht-Gates noch laufen):

```text
Neue Lease-/Supervisor-/Long-running-/Zeitzonen-Regressionen: 35 passed
Historische Event-/Startup-/History-/Effect-Gates: 165 passed; RuntimeWarning und Unraisable-Warnings als Fehler
EventRuntime-Reproduktion 7.9.8: 9/9 Szenarien; RuntimeWarning 0; Retention/Pending nach Drain 0; unfinished_tasks 0
Zeitzonen-Gate: je 8 passed unter TZ=UTC und TZ=Europe/Berlin
Sprachverständnis: 463 passed; Korpus-Signaturen 4107/4107 unverändert; Shadow SAFETY_DRIFT 0; dev_benchmark 462/503, unsafe_execution_count 0
Vollständige Release-Freigabe: ausstehend, bis Python 3.12/3.13, tests_ha, Live-Testbett und Nightly tatsächlich grün sind
```

Geprüfter Release-Stand von Version 7.9.8:

```text
Neue 7.9.8 EventRuntime-Regressionen: 14 passed
Event-/Startup-/History-/Effect-Gates einschließlich 7.9.8: 101 passed; RuntimeWarning und Unraisable-Warnings als Fehler
EventRuntime-Reproduktion: 9/9 Szenarien; sichere Thermal-History zugestellt, unsichere Thermal-History gezählt unterdrückt; RuntimeWarning 0; Retention/Pending nach Drain 0
Vollständige Stub-Suite (Python 3.12.3): 9778 passed, 12 skipped, 0 failed; RuntimeWarning und Unraisable-Warnings als Fehler
Lokale echte-HA-Suite (Python 3.14.2 / HA 2026.9.2): 21 passed, 1 zeitabhängiger 6000er-Kategorie-Burst fehlgeschlagen; derselbe Test verliert auf unverändertem 7.9.7 im selben Host exakt dieselben 378 Kategorieevents. CI bleibt für diesen Durchsatz-Gate maßgeblich
Korpus-Signaturen 7.9.8: 4107 Sätze; gegenüber 7.9.7 0 geänderte Signaturen und 1 neuer Test-Docstring (docs/perf/corpus-signatures-7.9.8-begruendung.md)
```

Geprüfter Release-Stand von Version 7.9.7:

```text
Event-/Startup-/History-/Effect-Gates: 170 passed, RuntimeWarning und Unraisable-Warnings als Fehler
Vollständige Stub-Suite (Python 3.12.3): 9764 passed, 12 skipped, 0 failed; RuntimeWarning und Unraisable-Warnings als Fehler
EventRuntime-Reproduktion: 8/8 Szenarien; 0 dropped_critical, 0 dropped_protected, 0 RuntimeWarnings, 0 Retention/Pending nach Drain
50.000 eindeutige Events: queue_peak 4096, max_retained_total 24576 (Grenze 37888), history_degraded 1, retained_total 0
Korpus-Signaturen 7.9.7: 4106 Sätze; gegenüber 7.9.6 0 geänderte Signaturen, 2 neue Test-Docstrings (docs/perf/corpus-signatures-7.9.7-begruendung.md)
Sprachverständnis: 463 passed; Shadow: 2131 equivalent, SAFETY_DRIFT 0; Arbiter: SAFETY_DRIFT 0 und RuntimeWarning-Gate 0 Findings
Entwicklungs-Benchmarks 7.7/7.8: unsafe_execution_count 0, Safety-Fehlerklasse 0
Safety-Invarianten im Nightly-Profil (300 Beispiele je Property): 40 passed, 0 Counterexamples
Pyright vollständig und alle drei zusätzlichen Strict-Profile: 0 Fehler, 0 Warnungen; Pyflakes repositoryweit 0; compileall erfolgreich
Echtes HA, Live-/Proactive-Testbett und CI: noch nicht lokal behauptet; sie benötigen Python 3.14 beziehungsweise einen sauberen CI-Runner
```

Geprüfter Release-Stand von Version 7.9.6:

```text
9745 passed, 12 skipped, 0 failed (Stub-Suite, Python 3.12 und 3.13, auch mit hassil 3.12; RuntimeWarning = Fehler); tests_ha 21 passed
Neu: tests/test_event_runtime_hardening_796.py 42 Fälle, tests_ha/test_event_runtime_hardening.py 2 Fälle
Echtes HA, 6000 synchrone Zustandsänderungen ohne Yield + Rauch/Wasser/ExpectedEffect: dropped_critical 0, dropped_lossless 0, worker_starts 1, pending_tasks 0, monitor_store_reads 0, 0,71 s (7.9.5: Rauch und Wasser verloren, 3,61 s)
Ohne aktiven Verbraucher, 6000 Ereignisse: 0 Queue-Einträge, 0 Snapshots, 0 Worker-Starts
Sprachverständnis-Gate: 463 passed; Korpus-Signaturen 7.9.6: 4101 Sätze aus 7.9.5 unverändert, 3 neue Einträge, 0 geändert (docs/perf/corpus-signatures-7.9.6-begruendung.md)
Shadow-Vergleich 2131 EQUIVALENT; Arbiter 0 SAFETY_DRIFT, auch mit RuntimeWarning als Fehler (0 Funde)
Entwicklungs-Benchmark 7.7 und 7.8: unsafe_execution_count 0; alle Latenzbudgets der CI eingehalten
Live-Testbett nach frischer Installation und Neustart: 230/230 (inklusive Proaktiv), check_log ohne HomeIntent-Befund, ohne RuntimeWarning und ohne „Something is blocking Home Assistant“
Pyright 0 Fehler (voll und alle Strict-Profile), Pyflakes 0
```

Geprüfter Release-Stand von Version 7.9.5:

```text
9703 passed, 12 skipped, 0 failed (Stub-Suite, lokal, Python 3.12 und 3.13, auch mit hassil 3.12); tests_ha 19 passed
Neu: tests/test_event_runtime_correctness_795.py 15 Fälle (gegen 7.9.4: 15 rot), tests_ha/test_event_runtime_correctness.py 1 Fall (gegen 7.9.4: rot, 903 von 1206 Ereignissen mit falschem Zustand)
Echtes HA, 6000 Zustandsänderungen (Sturmtest 7.9.4): weiterhin kein Task je Ereignis, 0 Schreibvorgänge, 0,49 s (7.9.4: 0,44 s)
Sprachverständnis-Gate: 463 passed
Korpus-Signaturen 7.9.5 (docs/perf/corpus-signatures-7.9.5.json): 4098 Sätze aus 7.9.4 unverändert, 3 neue Einträge (Test-Docstrings), 0 geändert (docs/perf/corpus-signatures-7.9.5-begruendung.md)
Shadow-Vergleich 2131 EQUIVALENT; Arbiter 0 SAFETY_DRIFT
Entwicklungs-Benchmark 7.7 und 7.8: unsafe_execution_count 0; alle Latenzbudgets der CI eingehalten
Live-Testbett nach frischer Installation und Neustart: 230/230 (inklusive Proaktiv), HTTP nach Neustart in 2,0 s, check_log ohne HomeIntent-Traceback (einziger Treffer: libturbojpeg von HA, nur wegen des lokalen venv-Pfads gezählt)
Pyright 0 Fehler (voll und alle Strict-Profile), Pyflakes 0
```

Geprüfter Release-Stand von Version 7.9.4:

```text
9688 passed, 12 skipped, 0 failed (Stub-Suite, lokal, Python 3.12 und 3.13, auch mit hassil 3.12); tests_ha 18 passed
Neu: tests/test_startup_event_storm_794.py 11 Fälle (gegen 7.9.3: 10 rot), tests_ha/test_startup_event_storm.py 2 Fälle (gegen 7.9.3: 2 rot)
Echtes HA, 6000 Zustandsänderungen: 7.9.3 5998/6000 fsync-Schreibvorgänge und 26 s/138 s, 7.9.4 0 Schreibvorgänge und < 0,5 s
Sprachverständnis-Gate: 463 passed
Korpus-Signaturen 7.9.4 (docs/perf/corpus-signatures-7.9.4.json): byte-identisch mit 7.9.3, 4098 Sätze, 0 geändert (docs/perf/corpus-signatures-7.9.4-begruendung.md)
Shadow-Vergleich 2131 EQUIVALENT; Arbiter 0 SAFETY_DRIFT
Entwicklungs-Benchmark 7.7 und 7.8: unsafe_execution_count 0; alle Latenzbudgets der CI eingehalten
Live-Testbett nach frischer Installation und Neustart: 229/230 (n793-b1-weather tageszeitabhängig, mit 7.9.3 identisch), check_log ohne HomeIntent-Traceback (einziger Treffer: libturbojpeg von HA, nur wegen des lokalen venv-Pfads gezählt)
Pyright 0 Fehler (voll und alle Strict-Profile), Pyflakes 0
```

Geprüfter Release-Stand von Version 7.9.3:

```text
9677 passed, 12 skipped, 0 failed (Stub-Suite, lokal); tests_ha 16 passed
Sprachverständnis-Gate: 463 passed
Korpus-Signaturen 7.9.3 (docs/perf/corpus-signatures-7.9.3.json): gegenüber 7.9.2 0 geänderte Signaturen, 272 neue Sätze (docs/perf/corpus-signatures-7.9.3-begruendung.md)
Arbiter-Shadow 2150 gleichwertig, 7 nicht messbar, 4 neue Musikbefehle ohne Arbiter-Kandidat, 0 SAFETY_DRIFT; Shadow-Vergleich 2131 EQUIVALENT
Entwicklungs-Benchmark 7.7 462/503, 7.8 102/107, unsafe_execution_count 0
Live-Testbett 230/230 (inklusive Proaktiv; 215 bisherige + 15 neue „Nachtest 7.9.3“), check_log 0 Befunde
Zusammenfassung 5000 Entitäten, 7 Tage p95 123,7 ms (Budget 500 ms); Automationssprache 5000 Entitäten p95 13,4 ms
Pyright 0 Fehler (voll und alle Strict-Profile), Pyflakes 0
Satzmuster (SEMANTIC_SENTENCE_PATTERN) 171
```

Geprüfter Release-Stand von Version 7.9.2:

```text
8492 passed, 12 skipped, 0 failed (Stub-Suite, lokal); tests_ha 16 passed
Sprachverständnis-Gate: 463 passed
Korpus-Signaturen 7.9.2 (docs/perf/corpus-signatures-7.9.2.json): gegenüber 7.9.1 eine begründete IR-Änderung, Engine unverändert (docs/perf/corpus-signatures-7.9.2-begruendung.md)
Arbiter-Shadow 2131 gleichwertig, 7 nicht messbar, 0 SAFETY_DRIFT; Shadow-Vergleich 2108 EQUIVALENT
Entwicklungs-Benchmark 7.7 461/503, 7.8 102/107, unsafe_execution_count 0
Live-Testbett 215/215 (inklusive Proaktiv; 198 bisherige + 17 neue „Nachtest 7.9.2“), check_log 0 Befunde
Zusätzliche Wartezeit auf Geräte-Rückmeldung (live): 3 von 511 Turns warteten (0,5 s; 2,0 s; 2,0 s), alle übrigen 0 ms
Automationssprache 5000 Entitäten p95 20,3 ms
Pyright 0 Fehler (voll und alle Strict-Profile), Pyflakes 0
Satzmuster (SEMANTIC_SENTENCE_PATTERN) 171
```

Geprüfter Release-Stand von Version 7.9.1:

```text
7936 passed, 12 skipped, 0 failed (Stub-Suite, lokal)
Sprachverständnis-Gate: 463 passed
Korpus-Signaturen 7.9.1: 3679 Sätze, gegenüber 7.9.0 eine begründete Änderung (docs/perf/corpus-signatures-7.9.1-begruendung.md)
Arbiter-Shadow 2101 gleichwertig, 7 nicht messbar, 0 SAFETY_DRIFT; Shadow-Vergleich 2078 EQUIVALENT
Entwicklungs-Benchmark 7.7 461/503, 7.8 102/107, unsafe_execution_count 0
Live-Testbett 198/198 (inklusive Proaktiv; 186 bisherige + 12 neue „Nachtest 7.9.1“), check_log 0 Befunde
Automationssprache 5000 Entitäten p95 25,3 ms
Pyright 0 Fehler (voll und alle Strict-Profile), Pyflakes 0
Satzmuster (SEMANTIC_SENTENCE_PATTERN) 173
```

Geprüfter Release-Stand von Version 7.9.0:

```text
7189 passed, 12 skipped, 0 failed (Stub-Suite, lokal)
Sprachverständnis-Gate: 463 passed
Korpus-Signaturen: 0 Änderungen gegenüber 7.8.3 (Baseline um 116 neue Testsätze ergänzt)
Arbiter-Shadow 2087 gleichwertig, 7 nicht messbar, 0 SAFETY_DRIFT; Shadow-Vergleich 2064 EQUIVALENT
Entwicklungs-Benchmark 7.7 459/503, 7.8 102/107, unsafe_execution_count 0
Live-Testbett 186/186 (inklusive Proaktiv; neu: m79-w1-all-windows, m79-w2-no-motion, m79-w3-rate, m79-w5-repeat, m79-w5-escalate, m79-w6-two-turns, m79-w8-list-stop), check_log 0 Befunde
Automationssprache 5000 Entitäten p95 18,6 ms
Pyright 0 Fehler (voll und alle Strict-Profile), Pyflakes 0
Satzmuster (SEMANTIC_SENTENCE_PATTERN) 173
```

Live-Testbett und echte Home-Assistant-Tests laufen in CI; der vollständige
Lauf inklusive Proaktiv wird für den Release-Commit ausdrücklich angestoßen
(`nightly-live.yml`). Details: `docs/architecture-completion-7.7.md`.

Zusätzlich wurden ausgeführt:

- Pyflakes für Integration und Tests,
- eine blockierende Pyright-Prüfung für den Strict-Scope,
- blockierende Strict-Prüfungen für V11 (`pyrightconfig-v11-strict.json`),
  V12 (`pyrightconfig-v12-strict.json`) und das Learning Center
  (`pyrightconfig-learning-center-strict.json`),
- die Learning-Center-Latenzbudgets (`scripts/benchmark_learning_center.py`),
  die Prüfung des Panels bei iPhone-Breite in Hell und Dunkel
  (`scripts/check_learning_center_mobile.cjs`) und die Prüfung der
  Panel-/WebSocket-Anbindung gegen ein echtes Home Assistant
  (`scripts/validate_learning_center_ha.py`),
- eine ebenfalls blockierende vollständige Pyright-Prüfung,
- das V12-Sicherheits- und OOD-Gate (296 handgeschriebene Fälle) und der
  V12-Benchmark (`scripts/benchmark_v12.py`),
- ein versionierter XML-Coverage-Bericht als CI-Artefakt,
- JSON-Validierung der deutschen UI-Texte und
- `git diff --check`.

Neue Sprachlogik sollte positive Fälle, Mehrdeutigkeit, sichere Ablehnung und
Regressionen abdecken. Das datengetriebene Mehrturn-Korpus kann separat über
folgenden Befehl ausgeführt werden:

```bash
./scripts/run_language_eval.sh
```

Das Verhalten jedes Korpussatzes (Engine-Signatur und Bedeutungs-IR) ist
ohne Serviceausführung gegen eine versionierte Signatur-Baseline prüfbar;
eine geänderte Signatur schlägt fehl:

```bash
python scripts/corpus_shadow.py \
  --check docs/perf/corpus-signatures-7.9.9.json
```

Erweiterte direkte Geräteoperationen laufen inzwischen ebenfalls durch die
V7-Grenze. Sie werden als typisierte, registrierte Operation kompiliert und
erst nach Validator, Execution Policy und zentralem Service Mapper
materialisiert. Die früheren Fachparser `device_control.py` und
`extended_device_control.py` sind entfernt. `match()` besitzt keinen
produktiven Kompatibilitätsfallback mehr. Der unabhängige read-only
Shadow-Vergleich kann die historische Altseite weiterhin prüfen; deren
Grammatiken werden ausschließlich für diesen Audit lazy geladen.

Zusätzlich erzeugt die Test-Suite weiterhin 1.024 intensive Lichtparaphrasen.
Die neue domänenübergreifende Matrix kombiniert außerdem 2.688 fachlich
passende Formulierungen aus Domäne, Ziel, Operation, sprachlicher Hülle und
Füllpartikel. Jeder Fall wird einmal direkt gegen den semantischen Compiler
und einmal über die echte Live-Routingreihenfolge geprüft – insgesamt 5.376
domänenübergreifende Routingprüfungen. Die Matrizen sind ausschließlich
Regressionstests und werden niemals von der Produktivlogik eingelesen.
Dadurch können sie dem Parser keine Antworten „beibringen“.

### Reproduzierbarer Benchmark

Für echte Zielhardware lässt sich ein JSON-Bericht samt P95-Budget erzeugen:

```bash
python scripts/benchmark_v6_baseline.py \
  --pipeline understand --scales 5000 --iterations 50 --warmup 10 \
  --json --max-p95-ms 100
```

Der Grenzwert muss passend zur jeweiligen Hardware gewählt werden. Ein
perfektes Ergebnis in einem bekannten Korpus bedeutet weiterhin nicht, dass
jede mögliche Formulierung verstanden wird.

## Mitwirken

Fehlermeldungen und Beiträge sind willkommen. Für Änderungen gelten die
Grundprinzipien:

- lokal und deterministisch,
- niemals bei Mehrdeutigkeit raten,
- Bedeutung und Ausführung trennen,
- Fähigkeiten und Risiken vor jeder Aktion prüfen,
- neue Ausdrücke möglichst zentral statt als Satzvarianten modellieren und
- jedes neue Verhalten mit Tests absichern.

Repository: [github.com/pquandel2-alt/homeintent](https://github.com/pquandel2-alt/homeintent)

HomeIntent steht unter der [MIT-Lizenz](../LICENSE).
