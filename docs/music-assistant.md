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

Bibliothekstreffer werden derzeit nicht dauerhaft gecacht. Dadurch können ein
Neustart oder in Music Assistant entfernte Sender nicht aus einem veralteten
HomeIntent-Cache wiederhergestellt werden.

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
- Es gibt derzeit keine von HomeIntent gepflegten Aliasnamen für
  Bibliothekseinträge. Maßgeblich ist der Name in Music Assistant.

HomeIntent selbst sendet dabei nichts an eine Cloud. Die angebundenen
Music-Assistant-Provider können eigene Netzwerk- und Datenschutzbedingungen
haben.
