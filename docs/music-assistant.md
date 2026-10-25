# Music Assistant

HomeIntent unterstützt Music Assistant optional über dessen registrierte
Home-Assistant-Aktionen. Es gibt keine Python-Abhängigkeit auf Music Assistant
und keinen direkten Zugriff auf dessen Server oder auf Radio-URLs.

## Einrichtung

1. Music Assistant und seine Home-Assistant-Integration müssen eingerichtet
   sein.
2. Der gewünschte Music-Assistant-`media_player` muss für Assist beziehungsweise
   in der festen HomeIntent-Geräteauswahl freigegeben sein.
3. Raum- und Aliasnamen werden in Home Assistant gepflegt. Bei mehreren
   Playern in einem Raum fragt HomeIntent nach, sofern keine eindeutige
   Auswahl aus der Äußerung hervorgeht.

HomeIntent erkennt die Fähigkeiten zur Laufzeit. Die Bibliothekssuche wird nur
verwendet, wenn `music_assistant.search` und `music_assistant.play_media`
tatsächlich registriert sind. Fehlt Music Assistant oder eine der Aktionen,
wird nichts ausgeführt.

Die blockierende Suche ist auf neun Sekunden begrenzt. Timeout, ein zwischen
Fähigkeitsprüfung und Aufruf verschwundener Dienst, HA-Fehler sowie strukturell
ungültige Antworten erzeugen keinen Wiedergabeplan. Abbruch der übergeordneten
HA-Aufgabe (`CancelledError`) wird nicht abgefangen.

## Bibliothek und gespeicherte Radiosender

Titel, Künstler, Alben, Playlists und Radiosender werden über
`music_assistant.search` gesucht. Bei „meine Playlist“, „gespeicherter Sender“
oder „Favorit“ wird die Suche auf Bibliothekseinträge begrenzt. HomeIntent
akzeptiert nur einen exakt normalisierten Namen und eine von Music Assistant
gelieferte, nicht webbasierte Media-ID. Ähnlich klingende Namen reichen nicht.

Mehrere exakte Treffer, etwa ein Künstler und ein Titel mit demselben Namen,
führen zu einer Rückfrage. Die Antwort wird erneut gegen die aktuelle
Bibliothek geprüft. Ein Player-Dialog ist pro Benutzer und Conversation
isoliert; „Spiel es im Wohnzimmer“ kann nur einen eindeutig passenden Player
aus dem offenen Auftrag auswählen.

## Persönliche Medienaliase

Ein angemeldeter Benutzer kann einen eindeutigen aktuellen Bibliothekstreffer
lokal benennen, zum Beispiel: „Merke dir Radio Bob als meinen Rocksender.“
HomeIntent zeigt kanonischen Namen und Medientyp zunächst als Vorschau und
speichert erst nach einem ausdrücklichen „Ja“. Gespeichert werden Besitzer,
`config_entry_id`, Medientyp, kanonischer Name und die von MA gelieferte sichere
Media-ID; direkte Web-, Datei- und Daten-URLs sind ausgeschlossen.

Bei mehreren MA-Instanzen wird die Instanz in dieser Reihenfolge bestimmt:
ausdrücklich genannter MA-Player, vorhandener eindeutig genannter
MA-Gruppenplayer, Raum mit genau einem MA-Player, eindeutige aktuelle
Satellitenzuordnung und zuletzt genau eine verfügbare MA-Instanz. Bleiben
mehrere Möglichkeiten, fragt HomeIntent nach Player beziehungsweise Raum und
sucht erst nach der benutzergebundenen Antwort ausschließlich in dessen
`config_entry_id`. Reihenfolge, interne ID und „erster Treffer“ sind keine
Auswahlevidenz. Player und Instanz werden vor dem speichernden „Ja“ erneut
geprüft.

„Spiele meinen Rocksender im Wohnzimmer“ löst ausschließlich einen exakt
gleich geschriebenen Alias des angemeldeten Benutzers auf. Vor jeder
Wiedergabe wird der kanonische Eintrag erneut exakt in derselben MA-Instanz
gesucht und die Media-ID verglichen. Ein entfernter, mehrdeutiger oder
veränderter Eintrag startet nichts. „Vergiss den Medienalias Rocksender“ zeigt
ebenfalls zuerst eine Löschvorschau. Löschen darf nur der Besitzer oder ein
Home-Assistant-Administrator. Anonyme Sprach-Turns dürfen Aliase weder anlegen,
verwenden noch löschen. Namen, die mit Geräten, Räumen, Personen, HA-Benutzern
oder vorhandenen Medienaliasen kollidieren, werden abgelehnt.

Andere Bibliothekstreffer werden nicht dauerhaft gecacht. Dadurch können ein
Neustart oder in Music Assistant entfernte Sender nicht aus einem veralteten
HomeIntent-Zustand wiederhergestellt werden.

Der lokale Adapterpfad wird in CI mit
`scripts/benchmark_music_assistant.py` gegen eine Registry mit 5.000 Entities
gemessen. Der Benchmark verwendet eine deterministische Service-Fake, öffnet
kein Netzwerk und führt keine Wiedergabe aus.

Zusätzlich validiert `scripts/music_assistant_contract.py` die Request- und
Response-Payloads gegen die tatsächlich installierte
`homeassistant.components.music_assistant`-Komponente. CI führt diesen Vertrag
mit der Mindestversion Home Assistant 2026.4.0 und der Stable-Testversion
2026.9.2 aus. Geprüft werden Domain/Aktionsnamen, `config_entry_id`, `name`,
ein einzelner oder mehrere `media_type`-Werte, `limit`, `library_only`, die
Antwortfelder `artists`, `albums`, `tracks`, `playlists`, `radio`, deren
`name`/`uri` sowie `media_id`, `media_type` und `enqueue` für `play_media`.
Dabei wird echter HA-Komponentencode und dessen echtes Schema geladen, aber
kein Music-Assistant-Server gestartet und keine Wiedergabe ausgelöst.

### E2E-Status und manuelle Matrix

Ein automatischer Test mit echtem Music-Assistant-Server und physischem oder
virtuellem Audioplayer ist **nicht Teil der verpflichtenden CI**. Im aktuellen
Repository existiert kein deterministischer lokaler MA-Testprovider samt
kontrolliertem Player, der ohne Providerkonto und ohne instabile öffentliche
Streamingquelle reale Wiedergabe beweist. Daher wird keine echte Wiedergabe
behauptet. Automatisiert sind echte HA-Komponenten-/Serviceschemas sowie der
HomeIntent-Pfad mit Fakes geprüft.

Für einen manuellen E2E-Test: Home Assistant 2026.4.0 und die im Stable-CI-Job
angegebene aktuelle Version jeweils mit einer kompatiblen Music-Assistant-
Version installieren, zwei MA-Config-Entries mit je einem freigegebenen Player
anlegen und einen lokalen Testprovider mit einer lokalen Audiodatei verwenden.
Dann (1) denselben Sendernamen in beiden Instanzen bereitstellen, (2) den Alias
ohne Player anlegen und die Rückfrage prüfen, (3) einen Player wählen und beim
Search-Aufruf dessen `config_entry_id` protokollieren, (4) Vorschau bestätigen
und die zweite identische Suche prüfen, (5) den Alias auf genau diesem Player
abspielen und `media_id`, `media_type`, `enqueue` sowie hörbare Wiedergabe
prüfen, (6) den anderen Instanzplayer wählen und den fail-closed Abbruch prüfen.
Private URLs, Tokens und vollständige Serviceantworten gehören nicht ins Log.

Explizite Queue-Angaben werden als geschlossene Planwerte behandelt:
„als Nächstes“ verwendet `next`, „zur Warteschlange hinzu“ verwendet `add`,
und „Warteschlange ersetzen“ verwendet `replace`. Ohne Queue-Angabe bleibt
`replace` der sichere Standard. Andere Werte werden vor Bibliothekssuche und
Wiedergabe abgelehnt.

## Sicherheit und Grenzen

- Direkte `http`, `https`, `ftp`, `file`- oder `data`-URLs werden abgelehnt.
- Bibliothekssuche ist read-only. Erst das eindeutige Ergebnis wird zu einem
  typisierten Medienplan und durch Execution Policy und Service Executor
  ausgeführt.
- Nicht verfügbare Player werden nicht gewählt; bei Mehrdeutigkeit erfolgt
  keine Wiedergabe.
- Pause, Fortsetzen, Stop, nächster/vorheriger Titel, Stummschaltung und
  Lautstärke verwenden die Standardaktionen von `media_player`.
- Gruppen werden nicht automatisch erzeugt oder verändert.
- Bereits in Music Assistant vorhandene und für Assist freigegebene,
  eindeutig benannte Gruppen-Player werden wie jeder andere MA-Player als
  Ziel verwendet. HomeIntent bildet keine dynamischen Gruppen: Für die
  unterstützten HA-Versionen ist hier kein eigener stabiler MA-Aktionsvertrag
  belegt, und HomeIntent erfindet keinen inoffiziellen Serviceaufruf.

HomeIntent selbst sendet dabei nichts an eine Cloud. Die angebundenen
Music-Assistant-Provider können eigene Netzwerk- und Datenschutzbedingungen
haben.
