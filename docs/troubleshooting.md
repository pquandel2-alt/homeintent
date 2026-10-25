# HomeIntent – Fehlerdiagnose

Diese Anleitung behandelt die häufigsten Laufzeitprobleme, ohne dass
Gesprächsinhalte oder private Zustände veröffentlicht werden müssen.

## Ein Satz wird nicht verstanden

1. Prüfen, ob **HomeIntent** in der verwendeten Assist-Pipeline ausgewählt
   ist.
2. Prüfen, ob das Ziel für Assist freigegeben oder in den HomeIntent-Optionen
   ausgewählt ist.
3. Friendly Name, Bereich, Etage, Geräteklasse und Aliase in Home Assistant
   prüfen.
4. Bei einer festen HomeIntent-Auswahl beachten, dass neue Entitäten nicht
   automatisch ergänzt werden.
5. HomeIntent nach einer Optionsänderung neu laden.

HomeIntent führt bei Mehrdeutigkeit bewusst nichts aus. Eigene Aliasregeln
haben das Format `gesprochener Name = domain.entity` und dürfen nicht auf
mehrere Ziele zeigen.

## „Hier“ findet keinen Raum

Die verwendete Assist-Anfrage muss eine `device_id` oder `satellite_id`
liefern. Das zugehörige Gerät beziehungsweise die Satellitenentität muss in
Home Assistant einem Bereich zugeordnet sein. Ohne diese Information rät
HomeIntent keinen Raum.

## Ein Befehl wird wegen einer Richtlinie abgelehnt

In den Integrationsoptionen prüfen:

- Nur-Lesen-Entitäten,
- nur administrativ steuerbare Entitäten,
- erlaubte Steuerungsbenutzer,
- maximale Zielanzahl,
- Bestätigungsstufe und
- Regeln für Nicht-Administratoren.

Eine leere Benutzerliste bedeutet „alle Benutzer“. Sobald mindestens ein
Benutzer gewählt ist, benötigen steuernde Anfragen eine passende
Home-Assistant-Benutzer-ID.

## Eine Benachrichtigungsautomation erreicht keine Person

Seit 7.1.2 bedeutet „mich“/„mir“ den angemeldeten Home-Assistant-Benutzer
und führt immer zu einer echten Push-Nachricht (`notify.send_message`), nie zu
einer persistenten Home-Assistant-Benachrichtigung. Das Ziel wird so bestimmt:

1. eine bestätigte Gerätezuordnung dieses Benutzers (HomeIntent-Benutzer-
   bzw. Personenbindung) gewinnt;
2. ohne Zuordnung wird genau ein unter **Push-Ziele** konfiguriertes
   `notify.*`-Gerät verwendet – nicht, wenn es einem anderen Benutzer
   zugeordnet ist;
3. mehrere konfigurierte Ziele ohne Zuordnung: „Ich habe mehrere Push-Ziele
   gefunden. Bitte ordne dein Gerät deinem HomeIntent-Benutzer zu.“;
4. kein Ziel: „Ich habe noch kein eindeutiges Push-Ziel für dich.“

Ist der Push-Kanal in den Optionen abgeschaltet, sendet HomeIntent auch auf
ausdrücklichen Wunsch nichts. Die proaktive Situationserkennung muss für
ausdrückliche Anfragen dagegen nicht aktiv sein. Antwortet HomeIntent „Dein
Push-Ziel ist momentan nicht verfügbar.“, existiert die `notify.*`-Entität
nicht oder ist `unavailable`. „uns“ nutzt ausschließlich den bestätigten
Haushalt. Für einen Namen wie „Philipp“ muss genau eine freigegebene
`notify.*`-Entität über Friendly Name oder Alias passen.

## Verlaufsfragen liefern keine Daten

Recorder muss aktiviert sein und die betreffende Entität aufzeichnen.
Numerische Mittelwerte, Minima, Maxima und Veränderungen benötigen passende
Langzeitstatistiken. „Wie oft“ und „wie lange“ lesen die Zustandsfolge aus der
Recorder-Historie. Fehlen Daten, ersetzt HomeIntent sie nicht durch den
aktuellen Zustand.

## Eine Automation wurde bestätigt, läuft aber nicht

1. In **Einstellungen → Automationen & Szenen** nach der Kategorie
   **Homeintent** suchen.
2. Prüfen, ob die Automation aktiviert ist und der erwartete Trigger in der
   Ablaufverfolgung erscheint.
3. Nach einem HACS-Update Home Assistant neu starten; bei großen
   Versionssprüngen die Integration einmal neu laden.
4. Home-Assistant-Protokolle nach `homeintent`, `automation.reload` oder einem
   Konflikt mit `automations.yaml` durchsuchen.

HomeIntent überschreibt eine gleichzeitig im UI geänderte Datei nicht blind.
Ein erkannter Dateikonflikt wird abgebrochen und protokolliert.

## Eine Automationsrückfrage lässt sich nicht fortsetzen

Antworten müssen aus demselben Conversation-Scope, vom selben angemeldeten
Benutzer und – sofern übermittelt – von demselben Satelliten/Gerät kommen. Nach
Ablauf der Dialog-TTL oder einem Home-Assistant-Neustart ist der flüchtige
Entwurf absichtlich verworfen. Beginne den Wunsch dann neu. Ein vollständiger
neuer Befehl mit „mach lieber …“ verwirft den alten Entwurf; ein späteres „Ja“
kann ihn nicht wiederbeleben.

## „Rückgängig“ meldet einen alten Pause-Eintrag

Schema-1/2-Einträge ohne vollständiges beweiskräftiges Mehrziel-Delta werden
nicht teilweise ausgeführt. Der Eintrag bleibt sichtbar an der LIFO-Spitze.
Nach manueller Prüfung kann ein angemeldeter Administrator sagen: „Verwirf den
letzten nicht rückgängig machbaren Verlaufseintrag.“ HomeIntent fragt nochmals
nach; dabei werden keine Automationen oder Metadaten verändert. Normale
beschädigte oder unterstützte Einträge lassen sich damit nicht überspringen.

## Mehrere Music-Assistant-Instanzen

Nenne einen vorhandenen freigegebenen MA-Player oder einen Raum mit genau einem
MA-Player. Ohne eindeutige Zuordnung fragt HomeIntent nach und speichert oder
spielt nichts. Ändert sich die `config_entry_id` zwischen Vorschau und „Ja",
wird die Alias-Erstellung abgelehnt. Ein Alias aus Instanz A lässt sich nicht
auf einem Player aus Instanz B wiedergeben.

## Datenschutzfreundliche Diagnose

Unter der HomeIntent-Integration kann eine Diagnose heruntergeladen werden.
Sie enthält Zähler und Richtlinieneinstellungen, aber keine Äußerungen,
Entity-IDs oder Zustände. Für einen Fehlerbericht sind zusätzlich die
Home-Assistant-Version, HomeIntent-Version, genaue Uhrzeit und der relevante
Protokollausschnitt hilfreich.

## Gedächtnis oder Routinen reagieren nicht

Gedächtnis und Routineerkennung sind standardmäßig deaktiviert. Beide
Schalter befinden sich in den Integrationsoptionen. Persönliche Präferenzen
benötigen eine von Assist übermittelte Benutzer-ID und eine ausdrückliche
Ja/Nein-Bestätigung. HomeIntent speichert keine beiläufige Aussage.

## AUTO wird nur als ASK angeboten

Das ist die sichere Normalform. Prüfen, ob AUTO global aktiviert ist, jede
Ziel-ID in der AUTO-Allowlist steht, der frische Zustand verfügbar ist und die
Aktion LOW-Risk sowie reversibel ist. Cover, Schloss, Alarm und vergleichbare
höhere Risiken werden nicht durch Routinewissen zu AUTO.

## Proaktive Ereignisregeln bleiben still

Mindestens eine Ereigniskategorie muss explizit eingetragen sein. Ruhezeiten
unterdrücken TTS, nicht jedoch konfigurierte kritische Kanäle. Ein veralteter,
unbekannter oder bereits deduplizierter Zustand erzeugt keine zweite Ausgabe.

## Frigate- oder HA-Quellenevidenz bleibt leer

Beide Adapter sind standardmäßig deaktiviert. Für Frigate muss zusätzlich die
HA-MQTT-Integration verfügbar und das konkrete Topic ohne `+` oder `#`
konfiguriert sein; alternativ werden strukturierte `frigate_events`
verarbeitet. HomeIntent liest keine Bilder. HA-Quellenevidenz entsteht nur aus
Zustandsänderungen unterstützter vorhandener Entitäten und wird ausschließlich
im begrenzten RAM-Puffer gehalten.

## Lokale Integrations-Smokes

Die CI-Kommandos lassen sich ohne Host-Installation in einem isolierten
Docker-Kontext wiederholen:

```bash
docker run --rm --entrypoint python3 \
  -v "$PWD:/workspace:ro" \
  -e PYTHONPATH=/workspace/custom_components \
  ghcr.io/home-assistant/home-assistant:stable \
  /workspace/scripts/validate_generated_automation_schema.py

docker run --rm -v "$PWD:/github/workspace" \
  ghcr.io/home-assistant/hassfest:latest
```

Der HACS-Validator benötigt zusätzlich ein GitHub-Token und prüft das
konfigurierte Repository/Ref über GitHub. Tokens dürfen weder in Diagnosen
noch in Shell-Historien oder Testausgaben geschrieben werden.
