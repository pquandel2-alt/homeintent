# Nachweis für interne Automation-Lifecycle-Aufträge

Öffentlich registrierte Lifecycle-Dienste sind kein allgemeiner Verwaltungsweg.
Für `delete_automation` und `record_automation_run` akzeptieren sowohl die
Domain `homeintent` als auch der Legacy-Alias `ha_nlu` nur einen zuvor von
HomeIntent ausgestellten, einmaligen Auftrag.

Beim Erzeugen oder sicheren Migrieren einer HomeIntent-Automation wird für jede
interne Lifecycle-Aktion ein kryptografisch zufälliges Token mit
`secrets.token_urlsafe(32)` erzeugt. Operation, Ziel-Automations-ID, Token und
Status `open` werden im Sidecar gespeichert. Dieselbe Ziel-ID, die ID der
auftraggebenden Lifecycle-Automation und das Token werden in die konkrete
YAML-Aktion eingebettet. Normale Assist-Antworten und normale Logs geben Tokens
nicht aus.

Ein Dienstaufruf wird nur ausgeführt, wenn alle folgenden Werte exakt
übereinstimmen:

- Operation (`delete` oder `record_run`)
- Ziel-Automations-ID
- ID der auftraggebenden Lifecycle-Automation
- eingebettetes Token
- offener Sidecar-Status
- genau eine entsprechende Aktion in der aktuellen Automation

Fehlende, falsche, doppelte oder bereits verbrauchte Nachweise werden
fail-closed abgelehnt. Erfolgreiche Löschaufträge werden verbraucht;
Run-Zähler-Aufträge erhalten vor dem nächsten Lauf ein neues Token in YAML und
Sidecar. Dadurch kann ein wiederholter externer Aufruf weder einen Löschauftrag
wiederholen noch den Zähler einer begrenzten Automation künstlich hochsetzen.
Schlägt die eigentliche Transaktion fehl, wird ausschließlich derselbe
nachgewiesene Auftrag für einen sicheren Retry wieder geöffnet.

Manuelle Sprachverwaltung läuft nicht über diesen internen Nachweis und prüft
weiterhin Eigentümer beziehungsweise Administrator. Der Legacy-Domain-Alias
verwendet dieselbe zentrale Prüfung und keine schwächere Kopie.

## Migration

Beim Start werden Nachweise nur ergänzt, wenn YAML-ID und vorhandener
HomeIntent-Sidecar eindeutig zusammengehören. Bereits teilweise eingebettete,
widersprüchliche oder fremde Angaben werden nicht repariert oder geraten; ihre
Lifecycle-Aufrufe bleiben gesperrt. Handgefertigte Automationen ohne gültigen
HomeIntent-Sidecar werden nicht als HomeIntent-Automationen übernommen.
