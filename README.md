# HomeIntent

**Lokale, schnelle und nachvollziehbare Sprachsteuerung für Home Assistant Assist – ohne LLM zur Laufzeit.**

- Aktuelle Version: **7.3.0** (Sprachverständnis ohne Sprachmodell)
- Sprache: **Deutsch**
- Installation: **HACS Custom Repository**
- Verarbeitung: **lokal in Home Assistant**
- Lizenz: **MIT**
- Website: **[pquandel2-alt.github.io/homeintent](https://pquandel2-alt.github.io/homeintent/)**

[![HomeIntent in HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pquandel2-alt&repository=homeintent&category=integration)
[![GitHub Release](https://img.shields.io/github/v/release/pquandel2-alt/homeintent)](https://github.com/pquandel2-alt/homeintent/releases/latest)
[![Website](https://img.shields.io/badge/Website-Live--Demo-5eead4?logo=github&logoColor=white)](https://pquandel2-alt.github.io/homeintent/)

> **[➜ HomeIntent im Browser ansehen](https://pquandel2-alt.github.io/homeintent/)** — eine
> interaktive Übersicht, die Schritt für Schritt zeigt, wie aus einem gesprochenen
> Satz ein geprüfter Serviceaufruf wird. Ohne Installation.

## Was ist HomeIntent?

HomeIntent ist ein eigener Conversation Agent für Home Assistant Assist. Er
versteht deutsche Smart-Home-Anweisungen, löst Geräte und Orte gegen die echte
Home-Assistant-Konfiguration auf und erzeugt daraus geprüfte Serviceaufrufe,
Abfragen, Dialoge oder Automationen.

Das Ziel ist nicht, ein allgemeiner Chatbot zu sein. HomeIntent konzentriert
sich auf zuverlässige Haussteuerung:

> Bedeutung erkennen, Ziel eindeutig auflösen, Fähigkeiten und Risiken prüfen
> und erst danach Home Assistant verändern.

HomeIntent benötigt für die Sprachverarbeitung:

- keine Cloud-API,
- kein Large Language Model,
- keine GPU,
- keinen Modell-Download und
- keinen externen HomeIntent-Server.

Der gleiche Satz führt bei gleichem Home-Assistant-Zustand und gleichem
Dialogkontext zum gleichen Ergebnis. Bei echter Mehrdeutigkeit fragt HomeIntent
nach oder führt nichts aus.

## Was ist in Version 7.3.0 neu?

**HomeIntent 7.3.0 — Sprachverständnis ohne Sprachmodell.** HomeIntent
versteht Alltagsdeutsch jenseits exakter Gerätenamen und fester Satzformen –
weiterhin ohne LLM, ohne ML-Modell und ohne externe Dienste. Wissen liegt als
Daten vor (Geräte-Ontologie, Orte, Bedürfnis- und Sichtentabellen), Regeln
arbeiten über Bedeutungsbausteine statt über Satzschablonen. Parser führen
weiterhin nichts aus; Validator, ExecutionPolicy, Bestätigungen und
`NEVER_AUTO` sind unverändert. Details: [`docs/architecture-v13.md`](docs/architecture-v13.md)
und der Abschnitt [„Wie HomeIntent Sprache versteht“](#wie-homeintent-sprache-versteht).

- **Ziele als Gattung × Ort × Menge:** „die Leuchte im Kinderzimmer“, „alle
  Jalousien im Obergeschoss“, „die Glotze“, „Kinderzimmerjalousie“; Einzahl
  bei mehreren Geräten fragt nach, fehlende Gattung wird ehrlich benannt.
- **Sicherheitsbefunde S1–S7 behoben:** „Lass … an“ tut nichts, „alles“ im
  Raum mit Vorschau ohne Heizung/Schlösser, keine verschluckten Teilsätze,
  „leiser“ pausiert nicht, Superlative nur über Innenräume, „zu hell“ wirkt,
  Melder bleiben in ihrer Gattung. Zusätzlich: „die Rollläden“ in
  verzögerten und geplanten Aufträgen erreicht nie Garagentor oder Markise,
  „um 18:30 …“ läuft nie sofort, und „in Küche und Flur“ verliert keinen Ort.
- **Bedürfnisse, Situationssichten, Diskurs, Modalität und Zeitsprache**
  („Ich friere“, „Ist alles abgeschlossen?“, „Die andere bitte auch“, „Die
  Kaffeemaschine kann jetzt aus“, „um viertel vor neun“, „Weck mich um
  sieben mit Licht“).
- **Benachrichtigungen** mit einer gemeinsamen Bedeutung für Sofort-,
  Verzögert-, Termin- und Ereignis-Push; diktierter Text wird vor der
  Zielauflösung abgetrennt.
- **Testhaus erweitert** (Einfahrtkamera, dritte Bürolampe) und neue
  Szenario-Kategorie „Sprache 7.3“ (36 Szenarien mit eigenen Sätzen).
- Regex-Verwendungen im Code: 854 → 793; Automationssprache p95 bei 5000
  Entitäten auf der Messmaschine 83 ms → 20 ms.

Messwerte gegen ein frisches, echtes Home Assistant 2026.9.2 mit dem
simulierten Einfamilienhaus (`sim/`):

| Messung | 7.2.1 | 7.3.0 |
| --- | --- | --- |
| Funktionsszenarien (`sim/runner.py`) | 126 / 126 | 162 / 162 |
| README-Beispiele (`sim/readme_check.py`) | 101 ok, 17 Rückfragen, 25 fehlgeschlagen von 143 | 131 ok, 32 Rückfragen, 6 fehlgeschlagen von 169 |
| Push-Matrix mit echter Auslösung (`sim/push_check.py`) | 23 / 35 | 34 / 35 |
| Alltagssprache-Korpus (`sim/nlu_probe.py`) | 11 / 66 (17 %) | 55 / 66 (83 %) |
| HomeIntent-Befunde im HA-Log (`sim/check_log.py`) | 0 | 0 |

Offen und ehrlich: Die verbliebenen README-Fehlschläge sind zwei
Konfigurationszeilen (`Leselampe = light.…`, keine Sätze), drei
Automationsbefehle, die bei mehreren passenden Automationen bewusst
ablehnen statt zu raten, und ein Listeneintrag, den der vorige Schritt schon
verschoben hatte. Der eine Push-Befund ist die unabhängige kritische
Wassermelder-Warnung, die zusätzlich an alle Haushaltshandys geht. Im
Alltagssprache-Korpus löste „Ich will einen Film schauen.“ die richtige
Szene Filmabend aus; das Testbett protokolliert Szenen nur über ihre
Lichtaufrufe.

## Was ist in Version 7.2.1 neu?

**HomeIntent 7.2.1 — Live-Test-Fix.** Behebt alle 28 Befunde des Live-Tests
gegen ein echtes Home Assistant 2026.9.2 mit simuliertem Einfamilienhaus
(Bericht und Nachtest: [`docs/testbericht-live-simulation-7.1.2.md`](docs/testbericht-live-simulation-7.1.2.md)).
Parser führen weiterhin nichts aus; Validator, ExecutionPolicy und
Executor bleiben die einzige Ausführungsinstanz, keine Sicherheitsgrenze
wurde gelockert.

- **hassil 3.12 (Home Assistant 2026.9):** Relative Zeitangaben, verzögerte
  Pushes und Erinnerungen („In 30 Minuten erinnere mich …“) werden wieder
  korrekt zerlegt; ein Platzhalter, der mitten im Wort beginnt, wird nie
  mehr ausgeführt.
- **Proaktive Hinweise laufen im Event-Loop:** Garage-offen-Vorschläge,
  Daueranweisungen und Erinnerungen feuern unter echtem Home Assistant
  zuverlässig; ein „Ja“ auf einen Push antwortet sofort, die Wirkung wird im
  Hintergrund geprüft.
- **Statistik und Messwerte:** „Wie war die Durchschnittstemperatur gestern
  im Wohnzimmer?“ liest die echte Recorder-Statistik; Sensoren werden über
  Raum und Messgröße gefunden, Außensensoren zählen nicht zum Hausmittel.
- **Farbtemperatur** nutzt `color_temp_kelvin`; Notify-Entities und Skripte
  im Anfangszustand `unknown` sind nutzbar.
- **Ausnahmen:** „alle Lichter außer Küche und Flur“ schließt jede genannte
  Ausnahme aus; eine unauflösbare Ausnahme führt nichts aus.
- **Optionen:** Speichern friert die Entitätsauswahl nicht mehr ein; betroffene
  Installationen erhalten einen Reparaturhinweis.
- **Daueranweisungen** werden bei erneuter Bestätigung erneuert statt
  dupliziert.
- **Automationen per Name** verwalten, Dialoge sauber verlassen („Abbrechen“,
  „keine Bedingung“), Routinen per gespeichertem Namen, Medien-Folgefragen,
  Timer nach Neustart („Der Timer … ging durch den Neustart verloren“),
  Listen ohne Listennamen, deutsche Zustandswörter statt Rohwerten.
- **Neue Regeln:** „das Licht im Bad“ schaltet alle Lichter eines Raums mit
  mehreren Lichtern (wie der Home-Assistant-Agent); „im Haus“, „überall“ und
  „in allen Räumen“ gelten als ganzes Haus.
- **Antworttyp:** Kalender- und Automationsabfragen liefern `query_answer`.
- **Vorhersagefragen** („Wann ist das Büro warm?“) erhalten eine
  modellgestützte Schätzung oder die ehrliche Keine-Modell-Antwort.
- **V12-Verlauf:** ein Eintrag pro Entscheidung statt pro Empfänger; die
  Erklärung nennt nur tatsächlich zugestellte Kanäle. Kritische Alarme
  verbrauchen kein normales Hinweisbudget, gruppierte Hinweise gehen nicht
  mehr verloren.
- **Geänderter Standard:** Neue Installationen erlauben das Anlegen von
  Automationen nur Administratoren (`allow_non_admin_automations` = aus).
  Bestehende Installationen behalten ihre Einstellung; die Option ist unter
  HomeIntent → Konfigurieren änderbar.
- **CI:** Die Stub-Suite läuft zusätzlich mit der hassil-Version von Home
  Assistant; ein neuer Job prüft das Live-Testbett (`sim/`) gegen echtes Home
  Assistant samt Log-Prüfung, nächtlich inklusive der Proaktiv-Ketten.

## Was ist in Version 7.2.0 neu?

**HomeIntent 7.2.0 — Natural Language Automations.** Ereignisgesteuerte
Benachrichtigungen und Automationen werden kompositionell verstanden statt
über auswendig gelernte Satzformen:

- **„Schicke mir eine Benachrichtigung wenn im Büro die Rolllade 50%
  erreicht hat“** wird verstanden: „Wenn der Rollladen im Büro 50 %
  erreicht, sende ich dir eine Push-Benachrichtigung … Soll ich das so
  einrichten?“ Nach „Ja“ entsteht ein Template-Trigger auf
  `state_attr('cover.…', 'current_position') == 50`; beim Erreichen von
  50 % geht genau eine Push-Nachricht an das konfigurierte Ziel. Der
  Rollladen selbst wird nie bewegt.
- **Rollladenposition, Lichthelligkeit und Ventilatorstufe sind typisierte
  Messwerte** (`MeasurementProperty`, geschlossene Zuordnung auf
  `current_position`/`brightness`/`percentage`). „50 Prozent“ bekommt seine
  Bedeutung erst durch die erkannte Domäne; gesprochener Text wird nie zu
  einem Attributnamen oder Template. „mindestens/höchstens“ bleiben
  inklusiv, eine ausdrücklich genannte Fahrtrichtung („beim
  Herunterfahren“) wird ausgewertet.
- **Ereignissatz + Aktionssatz in beiden Reihenfolgen, ohne Kommapflicht:**
  „Benachrichtige mich, wenn X“, „Wenn X, sag mir Bescheid“, „Sobald X
  schick mir eine Nachricht“, „Ich möchte informiert werden, falls X“,
  „Bei Sonnenuntergang benachrichtige mich“. Verbzweit, Verbletzt und
  Perfekt („50 Prozent erreicht hat“, „bei 50 Prozent steht“, „halb offen
  ist“) ergeben dieselbe Bedeutung.
- **Typisierte Geräteauflösung statt Rateversuch:** Gerätewort + Raum
  bestimmen das Ziel („die Rolllade im Büro“ = „im Büro der Rollladen“ =
  „Büro-Rollladen“). Bei zwei Rollläden im Büro fragt HomeIntent „Welche
  Rolllade im Büro meinst du …?“, und „Die linke.“ setzt denselben Entwurf
  fort. „Wenn es 50 Prozent erreicht“ ergibt „Was soll 50 Prozent
  erreichen?“.
- **Anwesenheit mit Richtung:** „wenn Julia nach Hause kommt“ löst nur
  beim Ankommen aus, „wenn ich das Haus verlasse“ nutzt die bestätigte
  Person des sprechenden Benutzers.
- **Selbstkorrekturen** („60, äh 50 Prozent“, „das Küchenfenster, nein das
  Bürofenster“, „Schick Julia, nein mir …“) behalten nur die korrigierte
  Bedeutung. Diktierte Nachrichtentexte bleiben wörtlich und werden nie
  als Befehl ausgeführt.
- **Sicherheitskorrekturen im bestehenden Automationspfad:** eine
  gesprochene Zeitbedingung („nach 21 Uhr“) oder eine zweite Aktion („… und
  benachrichtige mich“) wird nicht mehr stillschweigend verworfen;
  „Schlafzimmerfenster“ wird nicht mehr als Ventilator aufgelöst;
  „Benachrichtige mich nicht, wenn …“ erzeugt keine Automation.
- **Messung statt Behauptung:** ein vor der Implementierung eingefrorener
  Held-out-Korpus (363 Automations-/Rückfrage-Sätze, 313 adversariale
  Negative) und ein Entwicklungskorpus (645 Automations-/Rückfrage-Sätze,
  101 Negative) laufen durch den echten Gesprächspfad mit instrumentierter
  Service-Senke (`scripts/automation_language_report.py`).

Messergebnisse (Details in `docs/perf/automation-language-7.2.0-*.json`):

| Korpus | korrekt verstanden | Rückfrage korrekt | Negativ korrekt | falsche Vorschau | unsichere Ausführung |
|---|---|---|---|---|---|
| Held-out, erster blinder Lauf | 316/332 (95,2 %) | 18/21 | 311/313 | 3 | **2** |
| Held-out nach Sicherheitskorrekturen | 316/332 (95,2 %) | 19/21 | 313/313 | 2 | **0** |
| Entwicklung | 601/603 (99,7 %) | – | 101/101 | 0 | 0 |

Die zwei unsicheren Ausführungen des ersten Laufs („Bescheid.“, „Nachricht
an mich.“ lösten eine Sofort-Push aus) sind behoben; eine Sofort-Push
braucht jetzt ein ausdrückliches Verb. Die zwei verbleibenden „falschen
Vorschauen“ sind strittig (das historische „das Fenster“ = irgendein
Fenster; „der Akku“ bei genau einem Akku-Sensor). Nicht unterstützt und
ehrlich abgelehnt: relative Änderungen („um 2 Grad steigt“), Raten,
Gesamtzustände („alle Fenster offen“).

## Was ist in Version 7.1.2 neu?

**HomeIntent 7.1.2 — Push Notifications & Natural Language Fix.** Gezielte
Fehlerbehebung, kein Umbau:

- **„Schick mir eine Testbenachrichtigung.“** sendet sofort genau eine
  Push-Nachricht an dein Handy (`notify.send_message` an das aufgelöste
  Ziel) und antwortet „Die Testbenachrichtigung wurde gesendet.“ – erst,
  nachdem Home Assistant den Aufruf angenommen hat.
- **„Kannst du mir in 10 Sekunden eine Test Benachrichtigung schicken?“**
  wird als einmalige, persistente Home-Assistant-Automation mit absolutem
  Zeitpunkt, Datumsschutz und Selbstlöschung geplant – kein `sleep`.
- **„Benachrichtige mich sobald ein Fenster geöffnet wird“**,
  **„Kannst du mich benachrichtigen sobald im Wohnzimmer die Fenster auf
  ist?“** und **„Sobald die Fenster im Wohnzimmer geöffnet werden,
  benachrichtige mich, dass im Wohnzimmer ein Fenster offen ist.“** werden
  als Push-Automation erkannt. „ein Fenster“ / „die Fenster im Wohnzimmer“
  bedeutet: *irgendein* passendes Fenster öffnet sich.
- **„mich“ ist der angemeldete Home-Assistant-Benutzer.** Eine bestätigte
  Gerätezuordnung dieses Benutzers gewinnt; ohne Zuordnung wird genau ein
  konfiguriertes HomeIntent-Push-Ziel verwendet. Bei mehreren Zielen fragt
  HomeIntent nach einer Zuordnung statt an alle zu senden; ohne Ziel gibt
  es einen Konfigurationshinweis. „mich“ wird nie mehr stillschweigend zu
  `persistent_notification.create`.
- Nachrichten ohne eigenen Text werden aus dem Auslöser formuliert
  („Im Wohnzimmer wurde ein Fenster geöffnet.“); ein diktierter Text
  („…, dass im Wohnzimmer ein Fenster offen ist“) gewinnt immer.
- Erinnerungen, Benachrichtigungen, „Sag mir Bescheid“ und „Schick mir
  eine Nachricht“ teilen eine gemeinsame Bedeutung. „Sag mir, ob das
  Fenster offen ist“ bleibt eine Frage.
- Neuer handgeschriebener Evaluationskorpus mit 183 Benachrichtigungs- und
  Erinnerungsfällen (`tests/eval/notification_cases.json`).
- **Proaktive Push-Nachrichten an `notify.*`-Entities kommen wieder an.**
  Regel-, Monitor- und V12-Meldungen schickten bisher zusätzlich `data`
  (Tag, Buttons) an `notify.send_message`; Home Assistant lehnt das ab.
  Entities erhalten jetzt nur Titel und Text. Aktionsbuttons gibt es nur
  noch über eine bestätigte Bindung an einen `notify.mobile_app_*`-Dienst;
  sonst bleibt ein V12-Vorschlag per Sprache oder im Learning Center
  beantwortbar.

## Was ist in Version 7.1.1 neu?

**HomeIntent 7.1.1 — Options Flow Compatibility Fix.** Reine Fehlerbehebung,
keine neuen Funktionen:

- **Einstellungen → Geräte & Dienste → HomeIntent → Konfigurieren** öffnet
  unter Home Assistant 2026.9 wieder. 7.1.0 antwortete dort mit
  „Der Konfigurationsfluss konnte nicht geladen werden: 400: Bad Request“:
  vier Entitätsauswahlen erhielten ihre Domains als Tupel, Home Assistant
  erwartet `str | list[str]` und lehnte die Auswahl-Konfiguration ab
  (`expected str at 'domain[0]'`). Die Domains werden jetzt als Liste
  übergeben.
- **Speichern** der Optionen scheitert nicht mehr, wenn keine TTS-Entität
  gewählt ist (vorher `400` mit „Entity None is neither a valid entity ID
  nor a valid UUID“). Die TTS-Auswahl ist jetzt ein Vorschlagswert und lässt
  sich auch wieder leeren.
- Neue Regressionstests gegen ein **echtes Home Assistant 2026.9.2**
  (`tests_ha/`): Konfigurieren öffnen, Lern- und Proaktiv-Schalter speichern,
  Neuladen, Learning Center (Seitenleiste, Übersicht, Autonomie) zeigt die
  neuen Zustände.

## Was ist in Version 7.1.0 neu?

**HomeIntent 7.1.0 — Learning Center & Knowledge Control.** Keine neue
Lern- oder Vorhersagegeneration, sondern eine Oberfläche, die das bereits
vorhandene V11/V12-Wissen sichtbar, verständlich, kontrollierbar und
nachvollziehbar macht:

- **Seitenleiste → 🧠 HomeIntent** (Pfad `/homeintent`). Ohne YAML, ohne
  Lovelace-Ressource, ohne CDN: Panel und Datei liefert die Integration selbst
  mit; nach einem HACS-Update lädt der Browser automatisch die neue Version.
- **Übersicht, Wissen, Autonomie, Aktivität** – fürs iPhone entworfen, auf dem
  Desktop mehrspaltig, in Hell- und Dunkelmodus, Deutsch und Englisch.
- **Was hat HomeIntent gelernt – und warum?** Heizverhalten, Reaktionszeiten,
  Gerätezuverlässigkeit, Präferenzen und Gewohnheiten mit verständlichem
  Status („Gültig“, „Lernt noch“, „Vermutet“, „Bestätigt“, „Veraltet“,
  „Verhalten geändert“, „Unzuverlässig“, „Ungültig“), echten Kennzahlen,
  „Wofür wird das verwendet?“ und „Woher weißt du das?“. Der Qualitätswert
  wird nie als Wahrscheinlichkeit ausgegeben.
- **Korrigieren und entfernen** über dieselben Autoritäten wie per Sprache:
  Präferenz bestätigen/verwerfen, Gewohnheit als Routine übernehmen (wird
  gespeichert, nicht ausgeführt) oder dauerhaft nicht mehr vorschlagen,
  Modell vergessen, für Administratoren alle Lernmodelle zurücksetzen. Keine
  dieser Aktionen schaltet ein Gerät.
- **Was darf HomeIntent ohne Rückfrage?** Daueranweisungen mit genauem
  Geltungsbereich, Ablauf und Tagesnutzung – widerrufbar durch Eigentümer oder
  Administrator; stummgeschaltete Hinweise aufheben; Ruhezeit und
  Lernfunktionen auf einen Blick.
- **Mehrbenutzerfähig und privat.** Persönliche Präferenzen, Gewohnheiten,
  Stummschaltungen und persönlicher Verlauf werden serverseitig gefiltert –
  andere Benutzer erhalten sie gar nicht erst. Bestätigen kann nur der
  Eigentümer; Administratoren können Unerwünschtes entfernen, sehen aber
  keine persönlichen Inhalte.
- **Eine Wahrheit für Sprache und Oberfläche.** Bestätigen, Vergessen und
  Zurücksetzen laufen per Sprache und Panel durch dieselben Funktionen; das
  Panel aktualisiert sich ohne Polling über ein inhaltsfreies Änderungssignal.
- **Kein Datenumbau.** Keine Migration, kein zusätzlicher Speicher: Modelle,
  Tombstones, Daueranweisungen und V12-Verlauf aus 7.0.1 bleiben unverändert.

Details: [`docs/learning-center.md`](docs/learning-center.md).

## Was ist in Version 7.0.1 neu?

Ein reines Integritäts-Update für V12, ohne neue Funktionen:

- **Push-Knöpfe sind an dein Gerät gebunden.** Home Assistant meldet bei einem
  Tipp auf einen Benachrichtigungsknopf nur den angemeldeten Benutzer, nicht
  das Gerät. HomeIntent erzeugt deshalb pro Companion-App-Gerät ein
  Zufallstoken, das nur in dessen Knöpfe eingebettet und am Vorschlag
  gespeichert wird. Ein Tipp wirkt nur, wenn Benutzer, Empfänger und Token
  zusammenpassen und eine eventuell mitgelieferte Geräte-ID dem gebundenen
  Gerät entspricht. Abgelehnte Tipps verändern nichts. Ist kein Companion-App-
  Gerät im Benutzerkontext gebunden und in der Geräteregistrierung
  auffindbar, kommt die Nachricht ohne Knöpfe; beantworten lässt sie sich dann
  über Assist.
- **Raumbelege laufen ab.** Jeder Raumsensor, jede Anwesenheitsmeldung und
  jede Satellitenanfrage gilt nur ab ihrem eigenen Zeitstempel für eine feste
  Zeit (10 bzw. 5 Minuten); erneutes Nachfragen verlängert nichts. Ein
  veralteter Raum führt nicht mehr zu einer Sprachausgabe – persönliche
  Inhalte schon gar nicht –, widersprüchliche frische Belege gelten als
  mehrdeutig. Nach einem Neustart gibt es keine alten Raumbelege.
- **Ehrliche Zähler für Daueranweisungen.** Jeder automatische Lauf zählt als
  Versuch, nur ein geprüft wirksamer Lauf als Ausführung. Das Tageslimit zählt
  weiterhin alle Versuche, sodass fehlschlagende Läufe nicht endlos
  wiederholen. Gespeicherte Daten aus 7.0.0 bleiben lesbar.

## Was ist in Version 7.0 / V12 neu?

**Proaktive Kontextintelligenz.** HomeIntent wartet nicht mehr nur auf einen
Befehl. Es erkennt relevante Situationen im Haus, entscheidet, ob sie eine
Unterbrechung wert sind, wählt die richtige Person und den richtigen Kanal und
fragt nach – ausgeführt wird erst nach einer ausdrücklichen Antwort:

> „Die Garage ist noch offen. Soll ich sie schließen?“ – „Ja.“

> „Das Wohnzimmer wird voraussichtlich nicht rechtzeitig warm.“

> „Die Waschmaschine ist fertig.“

> „Du machst werktags morgens häufig dieselbe Abfolge. Als Nächstes folgen
> meist: Küchenrollladen öffnen und Wohnzimmerlicht einschalten. Soll ich die
> Routine starten?“

Das Wichtigste in der Praxis:

- **Vorschlagen, nicht eigenmächtig handeln.** Vorhersagen und erkannte
  Situationen führen nie selbst etwas aus. Jede Aktion läuft nach „Ja“, einem
  Push-Knopf oder einer vorher ausdrücklich bestätigten Daueranweisung durch
  die bestehende V10-Kette: Planer, Validator, Ausführungsrichtlinie,
  Konfliktprüfung, Wirkungsprüfung und GoalRun.
- **Der richtige Kanal.** Ist eindeutig belegt, in welchem Raum du bist, und
  gibt es dort genau einen Sprachsatelliten, spricht HomeIntent dort und hört
  ohne erneutes Aktivierungswort auf die Antwort. Ist der Raum unklar, gibt es
  keinen oder mehrere Satelliten, ist der Inhalt persönlich und jemand anderes
  zu Hause, oder bist du unterwegs, kommt eine private Push-Nachricht – mit den
  Knöpfen „Schließen“, „Später“ und „Ignorieren“, sofern ein Companion-App-
  Gerät an dich gebunden ist. HomeIntent rät nie einen
  Raum und sendet nichts ungefragt an alle Lautsprecher.
- **Nicht nerven.** Hinweise werden dedupliziert, in Ruhezeiten zurückgehalten
  oder privat zugestellt, mehrere kleine Meldungen werden zusammengefasst, und
  es gibt ein Hinweisbudget. Rauch-, Kohlenmonoxid-, Gas- und Wassermelder
  umgehen jede dieser Grenzen.
- **Dialoge bleiben getrennt.** Ein „Ja“ gehört immer zur aktuell offenen
  Rückfrage: Timer-Namen, Timer-Auswahl, „Lösche alle Timer“, Automationen und
  Sicherheitsbestätigungen haben Vorrang. Bei mehreren offenen Vorschlägen
  fragt HomeIntent nach („Meinst du das Licht in der Küche oder die Garage?“).
  Wer eine Frage nicht gestellt bekommen hat, kann sie nicht bestätigen.
- **„Später“, „Erinnere mich in 20 Minuten“, „Ignorieren“.** Nach Ablauf prüft
  HomeIntent den aktuellen Zustand – ist die Garage inzwischen zu, bleibt es
  still. „Ignorieren“ gilt nur für diese eine Situation; „Sag mir das künftig
  nicht mehr“ wird erst nach Rückfrage gespeichert.
- **Daueranweisungen (optional).** „Wenn niemand zuhause ist und im
  Wohnzimmer noch Licht an ist, darfst du es automatisch ausschalten.“ wird nach
  Vorschau und „Ja“ gespeichert. Schlösser, Garagen, Tore, Türen, Alarmanlagen,
  Ventile und herdähnliche Geräte werden niemals automatisch geschaltet –
  HomeIntent fragt dort immer.
- **Nachvollziehbar.** „Warum hast du mich wegen der Garage angesprochen?“ und
  „Welche Hinweise gab es heute?“ werden aus gespeicherten Belegen beantwortet.
- **Zuverlässigere Lernbasis.** Ein bereits erfüllter Zielzustand („Licht ist
  schon an“) zählt nicht mehr als erfolgreiche Geräteaktion; eine abgelehnte
  Serviceanfrage nicht mehr als Wirkungsfehler. Nur angenommene und geprüfte
  Aktionen fließen in die V11-Zuverlässigkeit ein.

Die proaktive Kontextintelligenz ist standardmäßig **ausgeschaltet** und wird
in den HomeIntent-Optionen aktiviert; Daueranweisungen sind zusätzlich separat
abgeschaltet. Empfänger und Push-Ziele kommen aus `homeintent.bind_user_context`
und `homeintent.set_household`. Alles läuft lokal. Details:
[docs/architecture-v12.md](docs/architecture-v12.md).

## Was ist in Version 6.1.0 neu?

**Benannte Timer.** Jeder neue Assist-Timer bekommt einen Namen; fehlt er im
Satz, fragt HomeIntent „Wie soll der Timer heißen?“. Mehrere Timer lassen sich
gezielt ansprechen („Lösche den Timer Pizza“, „Brich den Nudeltimer ab“). Ohne
Namen fragt HomeIntent bei mehreren laufenden Timern, welcher gemeint ist.
„Welche Timer laufen?“ nennt alle Timer mit Restzeit, „Lösche alle Timer“
fragt vorher nach. Beim Ablauf wird zuerst der Name gesagt („Der Timer Nudeln
ist abgelaufen.“), danach folgt der konfigurierte Hinweiston.

Weitere Verbesserungen:

- Messwerte werden mit deutschem Dezimalkomma gesprochen („19,5 Grad“ statt
  „19.5 Grad“, „78 Prozent“ statt „78.0 Prozent“).
- „Wenn es dunkel wird …“, „Wenn die Sonne untergeht/aufgeht …“ und „Wenn es
  hell wird …“ werden als Sonnen-Auslöser erkannt. Ein Satz mit Auslöser und
  nicht lesbarer Aktion wird nicht mehr als reiner Auslöser missverstanden.
  „Nein“ verwirft einen Automationsentwurf.
- „Entriegle/Verriegle …“ und „Sperr … auf/ab“ werden für Schlösser
  verstanden. Entriegeln bleibt kritisch und wird immer erst nach Bestätigung
  ausgeführt; Verneinungen, Fragen und „vielleicht“ führen nichts aus.
- Schlösser und Taster melden nach der Ausführung „aufgeschlossen“,
  „abgeschlossen“ und „gedrückt“.
- Die Restzeit eines `timer.*`-Helfers wird aus seinem Endzeitpunkt berechnet.
- Zustandsfragen ohne Ort und Einzeltreffer antworten vollständig
  („Kristallkugel ist ausgeschaltet.“, „Ja, es gibt 1 Licht.“).
- Automationssimulationen beschreiben Aktionen auf Deutsch; Automationsnamen
  erzeugen keinen doppelten Punkt mehr.
- Gelöschte HomeIntent-Automationen hinterlassen keinen verwaisten
  „nicht verfügbar“-Eintrag mehr.
- Der Thermal-Lernzustand wird außerhalb des Event-Loops geschrieben.

## Was ist in Version 6.0.3 neu?

Version 6.0.3 behebt Fehler, die ein Test gegen ein echtes Home Assistant
2026.9.3 mit einem virtuellen Haus aufgedeckt hat. Das Sprachverständnis ist
unverändert; der Shadow-Report über 3.772 Turns ist bis auf die Versionsnummer
identisch mit 6.0.2.

- Rollläden und Tore, die eine Position melden, lassen sich wieder öffnen und
  schließen. Seit 4.66.0 scheiterte das mit „Mindestens ein Ziel unterstützt
  die Aktion nicht mehr“.
- Tore (`device_class: gate`) gelten beim Öffnen und Schließen wie Garagentore
  als kritisch und brauchen eine Bestätigung. Sicherheitsrückfragen sind jetzt
  grammatisch formuliert („Soll ich wirklich Burgtor öffnen?“).
- Noch nie aktivierte Szenen und Taster (Zustand `unknown`) lassen sich
  auslösen und erscheinen nicht mehr als „Nicht verfügbar“.
- Aufrufe ohne eigene `conversation_id` (REST-API, `conversation.process`)
  teilen sich keinen gemeinsamen Dialogzustand mehr.
- Sprachsatelliten hören auch nach Routine-, Ziel- und Planrückfragen weiter zu.
- „Nein“ beendet eine offene Routinen-Rückfrage.
- „Wie lange läuft der Küchentimer noch?“ nennt die Restzeit statt einer
  Keine-Modell-Antwort; „Brich den Küchentimer ab“ wird verstanden.
- Grammatiken, Zahlregeln und der Thermal-Zustand werden nicht mehr blockierend
  im Event-Loop geladen.
- Die Mindestversion Home Assistant 2026.4.0 ist in `hacs.json` hinterlegt.

## Was ist in Version 6.0 / V11 neu?

Version 6.0.1 führte persistente, auch für die aktive In-Memory-Ansicht
maßgebliche Modell-Tombstones und schrittspezifische Ausführungszeiten ein.
Version 6.0.2 schließt das V11-Integritäts-Hardening ab und präzisiert diese Semantik:
Effektlatenz beginnt erst mit der bestätigten Serviceannahme. Unverifizierte,
zeitlich widersprüchliche oder nur mit dem historischen mehrdeutigen
`executed_at` belegte Vorgänge werden weder als Null-Latenz noch als Fehler
gelernt. Die Zuverlässigkeitsstatistik zählt ausschließlich verifizierte
Erfolge und verifizierte Fehlschläge.

V11 ergänzt V10 um eine vollständig lokale Advisory- und Lernschicht. Aus
verifizierten `GoalRun`-Wirkungen entstehen kompakte, bounded Experiences;
transparente Modelle lernen Effektzeiten, Zuverlässigkeit und thermische
Raumdauern. Vorhersagen enthalten Konfidenz, Unsicherheitsintervall,
Sample-Anzahl, Modellalter und Herkunft. Nur ausreichend validierte Modelle
dürfen den vorhandenen V10-Planner zeitlich beraten. Validator,
ExecutionPolicy, Bestätigung und Executor bleiben unverändert autoritativ.

Präferenzen bleiben bis zur ausdrücklichen Bestätigung `INFERRED`.
Gewohnheiten werden nur als deduplizierte Kandidaten vorgeschlagen und niemals
automatisch aktiviert. Persönliche Profile werden nicht gemittelt; Konflikte
führen ohne bestätigtes gemeinsames Profil zu einer Rückfrage. Lernen,
Vorhersagen, Gewohnheitserkennung und Vorschläge sind getrennt konfigurierbar
und standardmäßig deaktiviert. Es gibt keinen `AUTO_EXECUTE`-Lernmodus.

Thermische Modelle speichern ihren Trainingsbereich und prüfen neue Eingaben
auf `OUT_OF_DISTRIBUTION`. Bei genügend Daten entscheidet eine chronologische
Holdout-Validierung über die Planungsfreigabe. Externe Sollwert- oder
HVAC-Änderungen kontaminieren einen Heizzyklus; die Probe wird dann verworfen.
Zwischenprüfungen berechnen eine neue ETA und erkennen Verzögerungen, führen
aber keinen Gerätedienst aus. Persistierte aktive Lernzyklen werden nach einem
Neustart nur als Beobachtungszustand und nur bei weiterhin kompatiblem
Steuerkontext wiederhergestellt. Checkpoints besitzen eine persistierte,
einmalig konsumierbare kryptografische Identität.

Mehrbenutzer-Komfortprofile bleiben exakt nach Benutzer, Bereich und Konzept
getrennt. Bei mehreren anwesenden Personen und abweichenden Werten fragt der
Live-Dialog nach; ein gemeinsamer Wert gilt erst nach Bestätigung und nur für
die konkrete Personengruppe. Bestätigte Entitätspräferenzen verlieren ihre
Auflösungsautorität, wenn das Ziel entfernt oder in einen unpassenden Bereich
verschoben wurde. Gewohnheits-Support verwendet nur vergleichbare
Gelegenheiten derselben Sequenzfamilie; Ablehnungen bleiben dauerhaft
unterdrückt.

Zeitgestempelte Forget-Tombstones werden erst bereinigt, wenn die bounded
Experience-Retention garantiert keine Evidenz bis zu ihrem Lösch-Cutoff mehr
enthält. Erst danach darf neue Evidenz nach dem Löschen wieder ein Modell mit
derselben ID bilden; abgelehnte Gewohnheiten verwenden dagegen eine dauerhafte
Suppression.

Die Modellkonfidenz ist ein Qualitätswert und keine kalibrierte
Wahrscheinlichkeit. `DURATION`, `ENERGY` und `BATTERY_TREND` sind derzeit nur
Schemaslots, keine implementierten Vorhersagemodelle. Fragen nach normalem
Verbrauch, Gerätelaufzeit oder Batterieentladung erhalten ohne reales Modell
eine ausdrückliche Keine-Modell-Antwort.

Die technische Architektur, Schwellen, Invalidierung, Driftregeln,
Datenschutzgrenzen und Safety-Invarianten stehen in
[`docs/architecture-v11.md`](docs/architecture-v11.md).

## Was ist in Version 5.0 / V10 neu?

V10 ergänzt die bestehende V8/V9-Pipeline um typisierte Zielerkennung,
begrenzte Mehrschrittplanung, Vorbedingungsprüfung, Effektverifikation und
persistente Monitor-Ziele. Ein Ziel beschreibt das gewünschte Ergebnis und
ist nicht automatisch ein Serviceaufruf. HomeIntent verwendet weiterhin
weder Runtime-LLM noch Cloud-NLU und führt ausschließlich geschlossene,
policy-geprüfte Operatoren aus.

Bestätigte Routinen und Komfortprofile werden lokal gespeichert; unbekannte
Begriffe wie „Filmabend“ oder „angenehm“ lösen eine Rückfrage statt einer
Annahme aus. Die bestätigte Konfiguration erfolgt über die typisierten Dienste
`homeintent.save_routine` und `homeintent.save_comfort_profile`; persistente
Monitor-Ziele können über `homeintent.delete_monitor_goal` entfernt werden.
`person.*` bleibt die Anwesenheitsquelle. Zuordnungen von
Home-Assistant-Benutzer zu Person und Push-Ziel sind explizit. Persistente
Monitor-Ziele prüfen ihre Bedingungen zum Auslösezeitpunkt frisch, deduplizieren
Benachrichtigungen und überleben Neustarts. `GoalRun`-Datensätze unterscheiden
Serviceannahme von beobachteter Wirkung und ermöglichen belegte Antworten auf
„Warum hat das nicht funktioniert?“.

Version 5.0.2 schließt das V10-Hardening ab: historische Erklärungen verwenden
lokale, halb offene Zeitfenster und fragen bei mehreren passenden GoalRuns
nach. Temperatur-Ergebnisziele behalten ihr vollständiges `GoalModel` in einem
konversationsgebundenen Dialogzustand; „Sollwert zum Zeitpunkt“ nutzt nach
Bestätigung die bestehende persistente One-Shot-Automation, während „bis dahin
erreichen“ ohne bestätigtes thermisches Modell sicher stoppt. Push-Bindings
unterscheiden exakte Notify-Entities (`notify.send_message`) von expliziten
klassischen `notify.mobile_app_*`-Services und senden bei fehlender oder
mehrdeutiger Zuordnung niemals als Broadcast.

Die kanonische Integration heißt nun `homeintent`. Bestehende `ha_nlu`-
Config-Entries und Serviceaufrufe bleiben über einen minimalen, als veraltet
markierten Shim funktionsfähig. Details stehen in
[`docs/homeintent-rename-migration.md`](docs/homeintent-rename-migration.md),
die echte Architektur in [`docs/architecture-v10.md`](docs/architecture-v10.md).

### Version 4.77

Version 4.77 schließt weitere V9-Reasoning-Lücken innerhalb der bestehenden
V8/V9-Architektur: relationale Klassenprojektion ist nicht mehr auf Fenster
beschränkt, gruppierte Kardinalitätsfilter und negative relationale Mengen
werden in die zentrale Query-Algebra projiziert, und gleichwertige
Superlativ-Grenzwerte bleiben vollständig erhalten. Typisierte
Discourse-Resultatmengen können über `davon`/`dort` gefiltert und für einen
nachfolgenden Befehl live neu geerdet werden. Value- und Property-Repair
verwenden ausschließlich den finalen kompatiblen Slot. Kontinuierliche
Zustandsdauer wird aus `last_changed` belegt; Event-History bleibt ohne
Recorder-Evidenz ausdrücklich unsupported. Relative Zeitkorrekturen laufen
über denselben persistenten One-Shot-Automationspfad wie unkorrigierte
Zeitbefehle; eindeutige absolute Uhrzeiten werden ebenfalls als persistente
One-shot-Automation projiziert. HouseGraph-Traversals besitzen zusätzlich zu `max_depth`
deterministische Grenzen für besuchte Knoten, Frontier und Pfade.
Ein unabhängiger handgeschriebener deutscher V9-Korpus prüft 159 OOD-Fälle
einschließlich freier Wortstellung, Repair, Negation, Quantifier, Messungen,
Zustandsdauer, Temporalität und Safety-Grenzen. Echte Discourse-Set-Folgen
werden zusätzlich in separaten Mehrturn-E2E-Tests geprüft.

### Version 4.76

Sprachsatelliten hören nach einer Rückfrage weiter zu. Stellt HomeIntent eine
Frage – „Soll die Automation erstellt werden?“, „Welchen Rollladen meinst
du?“ –, bleibt das Mikrofon offen und die Antwort kann direkt gesprochen
werden. Bisher schloss der Satellit nach jeder Antwort sein Mikrofon, sodass
vor jeder Antwort erneut das Wakeword nötig war.

Dafür setzt HomeIntent das seit Home Assistant 2025.2 verfügbare Feld
`ConversationResult.continue_conversation`. Das Gerät selbst öffnet daraufhin
sein Mikrofon; an der ESPHome-Konfiguration eines Satelliten ist keine
Änderung nötig. Ältere Home-Assistant-Versionen ignorieren das Feld
folgenlos.

Das Flag wird zentral aus dem Dialogzustand abgeleitet und ist genau dann
gesetzt, wenn tatsächlich eine Rückfrage offen ist. Ein gewöhnlicher
erfolgreicher Befehl beendet das Gespräch wie bisher und lässt kein Mikrofon
offen.

### Version 4.75

Raumlose, singuläre Gerätebefehle berücksichtigen jetzt den physischen
Bereich des aktuellen Assist-Satelliten. Ein Satz wie „Fahr die Rolllade auf
50 Prozent“ wird dadurch auf eine eindeutig passende Rolllade im Raum des
Satelliten begrenzt. Explizit genannte Räume und Geräte bleiben stärker;
mehrere lokale Treffer führen weiterhin zu einer Rückfrage.

Der Quellbereich wird als typisierter Kontext durch die V8-Pipeline bis zur
semantischen Zielauflösung gereicht. Er bleibt vom zuletzt im Gespräch
erwähnten Bereich getrennt und wird nicht künstlich in den gesprochenen Text
eingefügt.

### Version 4.74

V9 ergänzt die bestehende V8-Verständnispipeline um typisierte semantische
Abfragen über belegte HouseGraph-Fakten. Relationale Filter, bounded
Multi-Hop-Traversal, Aggregate, Gruppierung, sichere Superlative,
Messwertbindung, Unit-Konvertierung, Mengenoperationen, ReasoningTrace und
live re-geerdete relationale Lichtbefehle sind in
[`docs/architecture-v9.md`](docs/architecture-v9.md) beschrieben.

V8 bleibt die autoritative Sprachverständnisschicht:

Direkte Befehle und Abfragen laufen über die verlustarme V8-
Verständnisgrenze. Historische Geräte- und Query-Grammatiken
werden im Produktivbetrieb weder geladen noch als Fallback verwendet; der
explizite Shadow-Audit lädt sie nur zum read-only Vergleich. Freie
Wortstellung, koordinierte Ziele und Orte, Referenzen, Korrekturen sowie
Query- und Command-Folgeäußerungen werden nativ semantisch kompiliert.

HomeIntent beantwortet nun auch Lebenszyklusfragen zu Waschmaschine,
Trockner und Geschirrspüler aus freigegebenen Zustands- und
Timestamp-Entitäten. Timer können vor der gesprochenen Ablaufmeldung einen
konfigurierten lokalen Hinweiston abspielen. Mehrdeutige Gerätenamen und
ungeklärte bedeutungstragende Wörter bleiben strikt nicht ausführbar.

Der lokale Agentenkern wurde um robuste Routine- und Anomaliestatistik,
weitere policygebundene HTN-Ziele, strukturierte Adapter-Evidenz,
Gedächtniskorrektur und -löschung sowie situationsgerechte Antwortplanung
erweitert. Dialogaufgaben verwenden typisierte Manager-Payloads. Persistierte
oder verzögerte Aktionen werden weiterhin mit frischem Snapshot erneut durch
Capability-Prüfung, Execution Policy und den zentralen Executor geführt.

Das 5.000-Entity-Gate behält sein 100-ms-p95-Budget unverändert. Im
maßgeblichen GitHub-Actions-Lauf lag der produktive Discourse-Follow-up bei
2,19 ms p95; auch alle komplexitätsspezifischen V9-Budgets wurden eingehalten.
Ein zusätzlicher lokaler Lauf war bei einer Host-Last von etwa 61 auf vier
sichtbaren CPUs nicht grün und wird nicht als Release-Messung ausgegeben. Die
vollständigen Werte und die Umgebungsgrenze stehen in
[`docs/perf/v9-completion.md`](docs/perf/v9-completion.md). Hassfest, HACS und
der stabile Home-Assistant-Container-Smoke waren im selben CI-Lauf grün.

Ältere Änderungen stehen in den
[GitHub-Releases](https://github.com/pquandel2-alt/homeintent/releases).

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
[`docs/architecture-v13.md`](docs/architecture-v13.md).

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
[`docs/homeintent-rename-migration.md`](docs/homeintent-rename-migration.md).

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
bestehende Kette; Details in [docs/architecture-v12.md](docs/architecture-v12.md):

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

- [`docs/jarvis-core.md`](docs/jarvis-core.md)
- [`docs/security-privacy.md`](docs/security-privacy.md)
- [`docs/agent-configuration.md`](docs/agent-configuration.md)
- [`docs/proactive-agent.md`](docs/proactive-agent.md)
- [`docs/architecture-v7.md`](docs/architecture-v7.md)
- [`docs/architecture-v8-audit.md`](docs/architecture-v8-audit.md)
- [`docs/architecture-v8.md`](docs/architecture-v8.md)
- [`docs/architecture-v8-integration.md`](docs/architecture-v8-integration.md)
- [`docs/architecture-v9.md`](docs/architecture-v9.md)
- [`docs/natural-language-roadmap-v7.md`](docs/natural-language-roadmap-v7.md)
- [`docs/quality-checklist.md`](docs/quality-checklist.md)
- [`docs/troubleshooting.md`](docs/troubleshooting.md)
- [`docs/learning-center.md`](docs/learning-center.md)

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

Geprüfter Release-Stand von Version 7.3.0:

```text
6034 passed, 12 skipped, 0 failed (mit hassil 3.11 und 3.12)
16 passed gegen echtes Home Assistant 2026.9.2 (tests_ha)
90 % Gesamt-Coverage
76 % Coverage für conversation.py
Held-out-Automationskorpus (7.2.0): 95,5 % korrekt (317/332), 0 unsichere Ausführungen
Attribut-Automation im echten Home-Assistant-Core ausgeführt
(scripts/validate_measurement_automation_ha.py)
```

Live-Testbett (`sim/`, frisches echtes Home Assistant 2026.9.2): 162 / 162
Szenarien (davon 36 in der Kategorie „Sprache 7.3“), keine
HomeIntent-Warnung im Log.

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

Legacy und vollständig kompiliertes V7 lassen sich außerdem ohne
Serviceausführung über den versionierten Shadow-Report vergleichen:

```bash
python scripts/v7_shadow_report.py \
  --check docs/perf/v7-shadow-baseline-7.3.0.json --quiet
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

HomeIntent steht unter der [MIT-Lizenz](LICENSE).
