# Sicherheits- und Datenschutzmodell

HomeIntent arbeitet lokal und besitzt keine generative Laufzeitabhängigkeit.
Parser, Hausgraph, Gedächtnis, Reasoner, Statistik, Persona und Adapter haben
keinen HA-Servicezugriff. Nur ein validierter `ServiceCallPlan` darf die
Execution Policy und danach `async_execute_service_plan` erreichen.

Vor jeder verzögerten oder gespeicherten Aktion werden Ziel-Snapshot,
Verfügbarkeit, gemeldete Capability und Policy erneut geprüft. Queries,
Ambiguität, unsichere Sprache, Gedächtnisfakten und Statistik erzeugen keinen
Plan. AUTO ist nur für explizit konfigurierte IDs, reversible Operationen und
LOW-Risk erlaubt; bei fehlender Freigabe wird es ASK. Kritische Aktionen sind
kein lernbares AUTO.

Eine nach erfolgreicher Ausführung registrierte Wirkungserwartung besitzt
keine Ausführungsberechtigung. Ihr Ablauf darf nur informieren; es gibt weder
Retry noch Folgeplan. Frigate-Daten bleiben statistische Evidenz, enthalten
keine Rohbilder und können weder Faktenstatus noch Policy-Allowlist erhöhen.
Die Adapterpuffer sind RAM-begrenzt und werden nach Neustart nicht wiederbelebt.

Das Gedächtnis ist opt-in, lokal, migrationsfähig, ablaufbar und löschbar.
Persönliche dauerhafte Einträge benötigen Bestätigung und Benutzerbindung.
Beim kontrollierten Löschen werden Inhalt und Personenzuordnung im Tombstone
überschrieben; SQLite `secure_delete` ist aktiv und das WAL wird anschließend
trunkiert. Nur ID, Typ und Zeitmetadaten bleiben zur Löschdiagnose erhalten.
Diagnosen und redigierte Exporte enthalten keine Entity-IDs, Personen-IDs,
Zustände, Transkripte oder Erinnerungsinhalte. Sie zeigen ausschließlich
Schema-, Herkunfts- und Typzähler sowie die Anzahl statistischer Serien und
Beobachtungen, damit erlernte Daten sichtbar bleiben, ohne private Inhalte zu
exportieren.

Fertigstellungszeit-Abfragen lesen ausschließlich den frischen, für Assist
freigegebenen Entity-Snapshot und erzeugen niemals eine Aktion. Der Name eines
Timers ist begrenzte Nutzlast (maximal 80 Zeichen), keine auszuführende
Anweisung und wird nicht als Sprachtranskript persistiert. HomeIntent betreibt
keinen eigenen Timer-Thread oder persistenten Zeitplan: Start, Änderung und
Ablauf liegen beim nativen Home-Assistant-`TimerManager`. Ein Client ohne
native Timer-Audioausgabe erhält nur dann einen Timer, wenn TTS-Engine und
Medienplayer explizit konfiguriert sind; andernfalls wird der Start abgelehnt,
damit kein unhörbarer Alarm vorgetäuscht wird.

Die optionale Music-Assistant-Anbindung liest Bibliothekstreffer ausschließlich
über die registrierte read-only Suchaktion. Direkte Web-, Datei- und Data-URLs
werden nicht an Player übergeben. Nur ein exakt benannter, eindeutiger Treffer
und ein freigegebener, verfügbarer Music-Assistant-Player ergeben einen Plan;
dieser läuft anschließend durch dieselbe Execution Policy wie andere
Geräteaktionen. HomeIntent hält keinen dauerhaften Medienbibliotheks-Cache.

Sammelaktionen (7.13.1) schreiben einen Audit-Eintrag pro Batch mit dem
endgültigen Status jedes Ziels nach der begrenzten Wirkungsprüfung. Undo
enthält ausschließlich Ziele mit bestätigter Wirkung; fehlgeschlagene,
widersprüchliche, nicht zurückgemeldete oder nicht überprüfbare Ziele werden
nie zurückgestellt. Ein angenommener Service Call allein gilt nicht als
Erfolg. Garagentore und Tore sind auch beim Setzen einer Position
`CRITICAL`.

Offene Automations- und MA-Instanzdialoge sind flüchtiger RAM-Zustand. Ihr
Schlüssel bindet Conversation-ID, authentifizierten Benutzer sowie vorhandene
Satelliten- und Geräte-ID; ein Administrator erbt dadurch keinen fremden
Sprachdialog. Sie verfallen nach der Dialog-TTL und werden nach Neustart nicht
wiederhergestellt. Anonyme Turns dürfen keinen persönlichen Automationsentwurf
oder Medienalias eröffnen.

Automation-History Schema 3 enthält nur die betroffenen Zielobjekte und deren
Sidecarwerte. Vollständige Automationsdateien, fremde Metadaten und deren
Lifecycle-Tokens werden in neuen Einträgen nicht kopiert. Lifecycle-Token,
Zugangsdaten, Auth-Token, private Dateipfade und vollständige interne
Serviceantworten werden weder in Assist-Antworten noch in normalen Logs
ausgegeben.
