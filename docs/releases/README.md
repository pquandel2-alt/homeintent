# HomeIntent – Versionsarchiv

Dieses Archiv bewahrt den früher in der README geführten Versionsverlauf.
Aktuelle, veröffentlichte Stände stehen zusätzlich unter
[GitHub Releases](https://github.com/pquandel2-alt/homeintent/releases); technische
Umsetzungsberichte liegen im übergeordneten Verzeichnis `docs/`.

Version 7.9.9 ist in den eigenständigen
[Release Notes](../release-notes-7.9.9.md) dokumentiert. Dieses Archiv beginnt
mit 7.9.8, damit die aktuelle Release-Beschreibung nicht doppelt gepflegt wird.

## Was ist in Version 7.9.8 neu?

Reiner Stabilitäts- und Korrektheits-Bugfix der Ereignisverarbeitung:

- **Getrennte Degradationszustände:** unvollständige Haus-Historie und
  fehlende Registry-/Area-Snapshots werden nicht mehr durch dasselbe Boolean
  vermischt.
- **Geschützter Thermal-Kontext:** aktive Thermal-Zyklen erhalten im
  History-Modus eine begrenzte, zeitlich rekonstruierte Sicht auf ihr
  konkretes Watch-Set. Unsichere eigene Historie wird gezählt unterdrückt,
  niemals geraten.
- **Ehrliche Consumer-Diagnose:** Runtime-Verarbeitung, Zustellung,
  History-/Registry-Unterdrückung und Queue-Drops besitzen getrennte Zähler.
  ExpectedEffects und sichere Monitorpfade bleiben bei irrelevanter
  History-Degradation funktionsfähig.
- **Eindeutige Filter- und Worker-Metriken:** Kategorie-, Proactive- und
  No-consumer-Filter sind disjunkt. `worker_restarts` misst ausschließlich
  einen echten automatischen Ersatzworker bei unerwartetem Ende mit
  ausstehender Arbeit.
- **`long_running_state` end-to-end:** die konservative neue Options-Flow-
  Einstellung `agent_long_running_minutes` ist standardmäßig `0`
  (deaktiviert), verwendet den historischen `last_changed`-Zeitpunkt und
  dedupliziert Attributupdates eines Zustandsintervalls.
- Single Worker, begrenzte Queue/History/Watch-Sets, Snapshot-Retry und der
  synchrone I/O-freie Eventcallback aus 7.9.7 bleiben erhalten. Details und
  neun maschinenlesbare Reproduktionen:
  `docs/umsetzung-7.9.8.md` und `scripts/event_runtime_repro_798.py`.

## Was ist in Version 7.9.7 neu?

Reiner Stabilitäts- und Korrektheits-Bugfix der Ereignisverarbeitung, keine
neuen Funktionen und keine Konfigurationsmigration:

- **Aktive Verbraucher sind eine eigene Priorität:** ExpectedEffects,
  Monitorziele, überwachte Personen und laufende Thermal-Zyklen sind jetzt
  `PROTECTED`. Gewöhnliche Kategorieevents können sie bei voller Queue nicht
  mehr verdrängen. Kritische Events verdrängen zuerst Coalescing- und
  Kategoriearbeit.
- **Präzise Kategorie-Interessen:** `safety`, Geräteausfall, Öffnung bei
  Abwesenheit, Fenster/Heizung, Licht/Anwesenheit, Langläufer und Routinen
  besitzen getrennte Domain-/Transition-Regeln. Ein negativer proaktiver
  Relevanzfilter reiht nichts ein; `expected_effect_missing` erzeugt ohne
  aktiven Effect kein hausweites Interesse.
- **Retention wird nach jedem Drain vollständig freigegeben:** Queue,
  `latest`, Gap-/Carry-Historie, Priority-Referenzen, Pending-Zähler,
  Tombstones sowie Event-/State-Referenzen bleiben nicht bis zum Unload
  liegen.
- **Gesamtes transientes Bookkeeping ist begrenzt:** 4096 Live-Events, 4096
  Entity-Historieneinträge je Hilfsmap und eine konservative Obergrenze von
  37888 Referenz-Records. Bei extremer Überlast wechselt die Runtime in einen
  gezählten, rate-limitiert gemeldeten sicheren Historienmodus, der keine
  zukünftigen Zustände als Vergangenheit ausgibt.
- **Neue Diagnose und Lasttests:** prioritätsspezifische Drop-Zähler,
  Filter-/Retention-/Degradationsmetriken, No-Yield-Regressionen, zehn Bursts,
  6000er-Drain, 50.000 eindeutige Events, Snapshot-Retry und Unload unter
  Last. Niedrige Prioritäten können an der harten Grenze weiterhin bewusst
  verworfen werden; das wird gezählt und nicht als „verlustfrei“ behauptet.
- Snapshot-Retry, historische Zustandskorrektheit, ein einzelner Worker und
  die Startup-Responsiveness aus 7.9.4–7.9.6 bleiben erhalten. Details,
  Grenzen und reproduzierbare JSON-Messungen:
  `docs/umsetzung-7.9.7.md` und `scripts/event_runtime_repro_797.py`.

## Was ist in Version 7.9.6 neu?

Reiner Stabilitäts-, Safety- und Performance-Bugfix der Ereignisverarbeitung,
keine neuen Funktionen:

- **Sicherheitsereignisse werden unter Last nicht mehr verdrängt:** Rauch-,
  Gas-, CO-, Wasser- und andere Alarm-/Problemsensoren werden bei voller
  Warteschlange bevorzugt; sie verdrängen gewöhnliche Messwertänderungen.
  Geprüft: hinter 6000 synchron eintreffenden Sensoränderungen erreichen
  Rauch- und Wassermelder die Sicherheitsauswertung (0 verlorene kritische
  und 0 verlorene verlustfreie Ereignisse, auch im echten Home Assistant).
- **Keine Arbeit ohne Verbraucher:** Eine Zustandsänderung wird nur noch
  eingereiht, wenn eine aktive HomeIntent-Funktion sie braucht (Ereignis-
  kategorien, aktivierter Proaktiv-Kontext, erwartete Gerätewirkung,
  Überwachungsziel, laufender Heizzyklus) oder sie sicherheitskritisch ist.
  Ohne aktiven Verbraucher startet kein Worker und es wird kein Snapshot
  gebaut.
- **Ein Fehler beim Einlesen der Geräte verliert keine Ereignisse mehr:** Der
  Stapel bleibt in der Warteschlange und wird mit begrenztem Backoff erneut
  versucht.
- **Überwachungsziele aus einem konsistenten Cache:** Die Ziele werden einmal
  gelesen; Personenwechsel und Wertänderungen lösen keinen Dateizugriff mehr
  aus. Gleichzeitiges Speichern und Löschen verliert keine Ziele.
- **Calendar-RuntimeWarning behoben:** Die Ursache lag im Test-Double für
  `hass.services.async_call` (es lieferte statt einer Service-Antwort ein
  `AsyncMock`); ein CI-Gate lässt jede RuntimeWarning fehlschlagen.
- Die historischen Zustandskorrekturen aus 7.9.5 bleiben erhalten. Keine
  Änderung am Sprachverständnis oder an Sicherheits-, Bestätigungs-,
  Berechtigungs- und Datenschutzgrenzen. Details, Grenzen und Messungen:
  `docs/umsetzung-7.9.6.md`.

## Was ist in Version 7.9.5 neu?

Reiner Bugfix der Ereignisverarbeitung, keine neuen Funktionen:

- **Jede Zustandsänderung mit ihrem eigenen Zustand:** 7.9.4 wertete mehrere
  kurz hintereinander eintreffende Zustandsänderungen gemeinsam gegen den
  *letzten* Zustand des Hauses aus. Eine Tür, die auf- und gleich wieder
  zuging, galt beide Male als zu; eine Person, die ging und zurückkam,
  löste kein „gegangen“ aus; Wertänderungs-Überwachungen und der
  Heizungs-Lernzyklus sahen spätere Werte. Jetzt sieht jede Änderung genau
  den Zustand, den sie gesetzt hat, und das übrige Haus so, wie es in diesem
  Moment war.
- **Robuster:** Ein Fehler beim Einlesen der Geräte beendet die
  Ereignisverarbeitung nicht mehr; nach einem Neuladen der Integration kann
  kein alter Worker neben dem neuen weiterlaufen; die feste Geräteauswahl
  wird je Ereignis als Mengenabfrage statt als Listendurchlauf geprüft.
- Die Startup-Verbesserung aus 7.9.4 bleibt vollständig erhalten. Sicherheits-,
  Bestätigungs-, Berechtigungs- und Datenschutzgrenzen sind unverändert.
  Details und Messungen: `docs/umsetzung-7.9.5.md`.

## Was ist in Version 7.9.4 neu?

Reiner Stabilitäts-Fix (P0), keine neuen Funktionen:

- **Home Assistant nach dem Neustart wieder erreichbar:** HomeIntent 7.9.3
  legte für jede Zustandsänderung im ganzen Haus einen eigenen Task an und
  schrieb dabei jedes Mal eine Datei mit `fsync`. Beim Start (Tausende
  Zustandsänderungen) blockierte das den Executor, den Home Assistant zum
  Hochfahren braucht – die Oberfläche blieb unerreichbar. Jetzt filtert ein
  schlanker Callback nur die für HomeIntent ausgewählten Entitäten heraus,
  ein einziger Worker wertet sie der Reihe nach aus, und geschrieben wird nur
  noch, wenn sich ein Heizzyklus wirklich ändert.
- Sicherheits-, Bestätigungs-, Berechtigungs- und Datenschutzgrenzen sind
  unverändert. Details und Messungen: `docs/umsetzung-7.9.4.md`.

## Was ist in Version 7.9.3 neu?

Befunde aus dem Nachtest 7.9.2 behoben, dazu neue Fähigkeiten:

- **Schon erfüllt beim Anlegen:** Liegen beim Einrichten einer Überwachung
  schon Geräte über bzw. unter der Grenze (Batterie 9 %, Fenster offen),
  nennt die Vorschau sie mit Wert und fragt, ob die Nachricht gleich kommen
  soll („Ja“ = einrichten und jetzt schicken, „Nein“ = nur einrichten).
- **Urlaub:** „Stell das Heizprogramm auf Urlaub“ ist wieder ein
  Gerätebefehl; „Wir sind wieder da“ beendet einen aktiven Urlaubsmodus nach
  Rückfrage.
- **Zusammenfassung:** mehr Formen („Was hab ich verpasst?“), chronologisch,
  mehrtägig mit Datum, begrenzt und gemessen; auf Wunsch per Push beim
  Heimkommen.
- **Keine Platzhalter:** Vorschauen nennen ein echtes Beispiel aus dem Haus.
- **Wetter:** „Wie wird das Wetter morgen?“, „Brauche ich einen Schirm?“ aus
  `weather.*`; „Wenn Regen angesagt ist, fahr die Markise ein“ als Automation.
- **Wo ist jemand?** „Wo ist Anna?“, „Ist jemand zuhause?“, „Seit wann ist
  Anna weg?“ – nie Koordinaten, Zonen anderer nur für Admins oder mit der
  Option „Aufenthaltsort im Haushalt teilen“.
- **Musik:** „Spiel Bayern 3 in der Küche“, „Pause“, „Weiter“, „Lauter“,
  „Lautstärke 30“, „Was läuft gerade?“ – Quellen nur aus `source_list`.
- **Bewässerung nur ohne Regen:** „… aber nur wenn es nicht geregnet hat bzw.
  nicht regnen soll“ aus Regensensor, Regenmenge und Vorhersage.
- **Bestätigungston als Standard** für neue Installationen (bestehende
  behalten ihre Einstellung) und ein zweiter Ton, wenn sich nur ein Gerät
  nicht zurückmeldet („Stehlampe meldet sich nicht“).
- **Haus-Bericht:** „Schick mir jeden Sonntag um 18 Uhr einen Haus-Bericht“ –
  Batterien, Ausfälle, Verbrauch, Meldungen der Woche.
- **Überwachungen ändern:** „Ändere die Garagen-Meldung auf 15 Minuten“,
  „Schick die Fenster-Warnung auch an Anna“ – mit „Vorher/Nachher“ und „Ja“.
  Details: `docs/umsetzung-7.9.3.md`.

## Was ist in Version 7.9.2 neu?

Befunde aus dem Nachtest 7.9.1 behoben, dazu neue Fähigkeiten:

- **Wirkung kurz abwarten:** Nach einem Befehl wartet HomeIntent
  ereignisgesteuert höchstens 2 s (Option `effect_wait_seconds`, 0–5) auf die
  Rückmeldung der Geräte. Ein Rollladen, der in die verlangte Richtung fährt,
  ist ein Erfolg (Bestätigungston). Gegenrichtung, „nicht erreichbar“ und
  keine Rückmeldung werden ehrlich gesprochen.
- **Bewässerung nach Zeitplan:** „Jeden Morgen um 6 Uhr bewässere den Garten
  15 Minuten“ öffnet das Bewässerungsventil und schließt es nach der Dauer in
  derselben Automation; ohne Dauer fragt HomeIntent „Wie lange?“. Nur
  Bewässerungsventile öffnen sich automatisch – Tore, Türen, Schlösser, Gas-
  und Hauptventile nie.
- **Sprachgeräte im Haus sprechen für den Haushalt** (Option, Standard aus):
  Ein Satellit ohne angemeldeten Benutzer verwaltet gemeinsame Überwachungen
  („Mach die Garagen-Meldung für alle“, „… für uns alle“); persönliche
  bleiben gesperrt. Die Liste zeigt „(gemeinsam)“.
- **Ehrlicher und genauer:** Die Vorschau nennt Räume einer Etage ohne
  Melder; ein eindeutiger Registry-Name („Stromverbrauch Haus“) gewinnt vor
  der Rückfrage; Markise mit Sonne/Lux/Wind, „schnell fällt“ mit Rückfrage,
  „noch Licht an“ nennt die Räume, „irgendeine Batterie“ nennt das Gerät.
- **Was war los?** „Was war los, während ich weg war?“, „Was ist heute
  passiert?“ – Ereignisse aus dem Recorder, wichtigste zuerst, Rest mit
  „Was noch?“.
- **Gewohnheiten:** wiederkehrende eigene Handlungen (mindestens 4 von 7
  Tagen) werden einmal als Automation vorgeschlagen; „Schlag mir nichts mehr
  vor“ schaltet das ab.
- **Batterien und Ausfälle:** „Welche Batterien sind schwach?“, „Sag mir
  jeden Sonntag um 10 Uhr, welche Batterien unter 30 % sind“, „Melde dich,
  wenn ein Gerät nicht mehr erreichbar ist“ (ab 10 Minuten, keine Flut nach
  einem Neustart).
- **Urlaubsmodus:** „Ich bin bis Sonntag weg“ – strengere Meldungen,
  optional Anwesenheitssimulation mit Lichtern, Urlaubs-Helfer; am Ende wird
  alles zurückgenommen.
- **Verbrauch:** „Wie viel Strom hat die Waschmaschine heute verbraucht?“
  (kWh aus Zähler oder geschätzt aus der Leistung), „Was hat heute am meisten
  verbraucht?“, Kosten nur mit Strompreis. Details: `docs/umsetzung-7.9.2.md`.

## Was ist in Version 7.9.1 neu?

Befunde aus dem Nachtest 7.9.0 behoben, dazu ein Bestätigungston:

- **Zugänge öffnen sich nie automatisch:** Eine Automation öffnet kein
  Garagentor, Tor, keine Tür, kein Ventil und kein Schloss – auch nicht über
  ein Skript oder eine Szene. HomeIntent sagt das und bietet stattdessen eine
  Benachrichtigung an („Garagentor jetzt öffnen? Das entscheidest du
  selbst.“). Schließen bleibt mit Bestätigung erlaubt. Die Vorschau nennt die
  Geräteart (Garagentor, Tor, Markise, Jalousie) statt „Rollladen“.
- **Überwachungen gehören jemandem:** Pausieren, aus-/einschalten,
  bearbeiten und löschen darf nur, wer sie angelegt hat, oder ein
  Administrator. Nicht-Admins sehen nur ihre eigenen Überwachungen; ältere
  Automationen ohne Eigentümer verwaltet nur ein Administrator. Eine
  Sprachquelle ohne angemeldeten Benutzer ist weder Eigentümer noch Admin.
- **Grenzwert mit „geht“:** „wenn die Temperatur über 24 Grad geht“ ist
  wieder ein Grenzwert; neu versteht HomeIntent `ppm`.
- **Ort schränkt immer ein:** „im Keller“, „oben“, „draußen“ fallen nie auf
  Geräte anderswo zurück; ohne Gerät dort folgt eine ehrliche Antwort.
- **Verwalten in allen Objektformen:** „Lösch die Überwachung vom
  Garagentor“, „Kannst du die Automation fürs Flurlicht ausschalten?“ – nie
  mehr an den Kalender.
- **Antworten auf eigene Rückfragen:** „In welchem Zeitraum?“ – „Innerhalb
  von 10 Minuten.“; ebenso Uhrzeit, Zählbeginn, Gerät, Empfänger und
  „Bis wann?“ beim Pausieren.
- **Mehr Verständnis:** Wiederholung mit „solange“, „höchstens einmal pro
  Minute“, Wochen, „die Waschmaschine zieht mehr als 2000 Watt“, „das Haus
  verbraucht mehr als 5 kW“, „noch Licht an“, „Markise eingefahren“,
  Zählerstand als Grenzwert, „Prüfe, ob …“ als Frage, „bei Auffälligkeiten“,
  „Beobachtest du das Garagentor?“ und eine kurze Liste der Überwachungen
  („Was genau macht die erste?“ für die volle Vorschau).
- **Bestätigungston (Option „Antwortstil“ = `tone`):** Wurde alles
  ausgeführt, kommt keine Sprache, sondern ein kurzer Ton auf dem Gerät, von
  dem der Befehl kam (Satellit über `assist_satellite.announce`,
  Mediaplayer über `media_player.play_media`). Fragen, Fehler, Teilerfolge,
  noch nicht bestätigte Wirkungen und Antworten auf Abfragen werden immer
  gesprochen; im Text-Chat steht „Erledigt.“. Standard bleibt `spoken`
  (ab 7.9.3: `tone` für neue Installationen).
  Eigener Ton: Option `confirmation_media_id` (nur `media-source://`,
  `/local/…`). Details: `docs/umsetzung-7.9.1.md`.

## Was ist in Version 7.9.0 neu?

**HomeIntent überwacht und meldet** – für jede Überwachung gibt es eine
korrekte Automation oder eine ehrliche Antwort, was fehlt:

- **Gesamtzustände:** „Sag mir Bescheid, wenn alle Fenster zu sind.“ ·
  „Melde dich, sobald kein Licht mehr an ist.“ – die Nachricht kommt, wenn das
  letzte Mitglied den Zustand erreicht; die Vorschau nennt die Anzahl.
- **Ausbleiben:** „wenn sich im Flur 12 Stunden nichts bewegt“, „wenn die
  Haustür zwei Tage nicht geöffnet wurde“, „wenn bis 10 Uhr keine Bewegung im
  Flur war“ – mit dem Hinweis, was ein Neustart bewirkt.
- **Änderungen:** „wenn die Temperatur im Keller innerhalb einer Stunde um
  3 Grad fällt“ – das überwacht HomeIntent selbst (Home Assistant bräuchte
  dafür neue Helfer); ohne Zeitraum fragt HomeIntent nach.
- **Leistung und Verbrauch:** W, kW, Wh und kWh sauber getrennt; „heute“ nur
  mit einem Verbrauchszähler mit täglichem Zyklus – sonst sagt HomeIntent,
  welcher Helfer fehlt.
- **Wiederholen und Eskalieren:** „Erinnere mich alle 10 Minuten, bis das
  Garagentor zu ist.“ (höchstens 12-mal, das wird gesagt) · „…, und wenn sie
  nach 15 Minuten immer noch offen ist, sag Anna Bescheid.“
- **Zwei Sätze:** „Überwache das Garagentor.“ – „Wann soll ich mich melden?“
  – „Wenn es länger als 10 Minuten offen ist.“
- **„Etwas Ungewöhnliches“** wird nicht erfunden: HomeIntent nennt, was die
  proaktive Erkennung wirklich kennt, und bietet an, es einzuschalten.
- **Verwalten:** „Welche Überwachungen laufen?“ · „Stopp die
  Fensterüberwachung.“ · „Pausiere die Garagen-Meldung bis morgen um 7 Uhr.“

Standard ist die Home-Assistant-Automation (in HA sichtbar, die gesprochene
Vorschau steht in ihrer Beschreibung); nur Änderungsraten laufen in HomeIntent
selbst. Details: `docs/umsetzung-7.9.md`.

## Was ist in Version 7.8.3 neu?

**Überwachungsaufträge** – HomeIntent versteht, was es überwachen und wann es
sich melden soll, und macht daraus eine Automation mit Push-Nachricht:

- „Überwache das Garagentor und melde dich, wenn es länger als 10 Minuten
  offen ist.“ · „Beobachte die Fenster und warne mich, wenn eins offen ist
  und niemand zuhause ist.“ · „Achte darauf, ob …“ · „Behalte die Haustür im
  Auge und melde dich, wenn sie nachts geöffnet wird.“
- Erkannt werden Konstruktionen, keine Sätze: Überwachungsverben,
  Benachrichtigungsverben, Konnektoren, Bezüge („es“, „sie“, „eins davon“,
  mit Genus-Prüfung), gestapelte Dauerangaben („seit mehr als 20 Minuten“),
  Tageszeitfenster („nachts“). „Prüfe, ob …“ bleibt eine einmalige Abfrage.
- **Zustände gelten in beiden Reihenfolgen:** „wenn ein Fenster offen ist und
  niemand zuhause ist“ meldet sich auch, wenn zuletzt jemand geht. Ein Moment
  („geöffnet wird“) bleibt ein Moment.
- **Eine Bedeutung, egal wie formuliert:** „niemand“, „keiner“, „warne“,
  „sag Bescheid“, Wenn-Satz vorn oder hinten, mit oder ohne Überwachungsverb –
  immer dieselbe Automation. Der satzbasierte Leser entscheidet zuerst; die
  V10-Monitor-Goals behalten nur, was er nicht versteht.
- **„Niemand zuhause“ meint genau eine Personenmenge:** den bestätigten
  Haushalt, sonst alle Personen – die Vorschau nennt sie („keiner von Anna,
  Lena und Philipp“).
- „ein Fenster offen ist“ als Bedingung heißt jetzt „irgendeines“ (vorher
  erzeugte Home Assistant daraus „alle“).
- Nachfragen statt raten: Bezug ohne Antezedens, falsches Genus, unbekanntes
  Gerät, Fensterkontakte und Fensterantriebe unter einem Wort.

Details, Tabellen und alle Messwerte: `docs/umsetzung-7.8.3.md`.

## Was ist in Version 7.8.2 neu?

**Skripte, Szenen und Gruppen laufen, wenn du sie nennst** (Entscheidung aus
dem Betrieb: „Schlafen“ schaltet alles aus und Fernseher und LED-Bettlicht
ein, „Ambiente“ ist eine Lichtgruppe – beide wurden abgelehnt, weil sie auch
nicht freigegebene Lichter schalten).

- Neue Option `routine_unexposed_effects`: `allow` (Standard) führt ein
  freigegebenes Skript, eine Szene oder Gruppe aus, die du ausdrücklich
  nennst, auch wenn darin nicht freigegebene Geräte stecken – wie Home
  Assistants eigenes Assist. `confirm` fragt vorher und nennt die Geräte,
  `deny` lehnt ab wie bis 7.8.1.
- Immer geschützt, in jedem Modus: nicht freigegebene Schlösser,
  Alarmanlagen, Rollläden/Tore und Ventile im Skript; indirekte Aussagen
  („Ich gehe schlafen“), abgeleitete Routinen, proaktive und zeitversetzte
  Ausführungen und Automationen.
- Unverändert: Risiko, Bestätigungsstufe, Nur-Admin, Nur-Lesen und maximale
  Zielzahl gelten für alle wirksamen Ziele. Muss HomeIntent ohnehin
  nachfragen (etwa bei einem Button im Skript), nennt die Frage auch die
  nicht freigegebenen Geräte.

## Was ist in Version 7.8.1 neu?

**Skripte, Szenen und Gruppen gegen die echte Freigabe** (Rückmeldung aus
dem Betrieb: „Aktiviere Schlafen“ und „Schalte Ambiente ein“ wurden
abgelehnt, obwohl die Geräte freigegeben waren).

- **Gelöschte Geräte blockieren nicht mehr:** Nennt ein Skript eine Entität,
  die Home Assistant nicht mehr kennt (gelöscht, umbenannt, deaktiviert),
  schaltet dieser Schritt nichts. Sie zählt deshalb nicht mehr als „nicht
  freigegeben“; das Skript läuft, die Erfolgsmeldung zählt nur, was es
  wirklich gibt. Beim Anlegen einer Automation zählt sie weiter, denn die
  läuft später, wenn die Entität wieder da sein kann.
- **Die Ablehnung nennt Ursache und Ort:** Teilt ein verstecktes Gerät den
  Namen mit einem freigegebenen (zweite Entität desselben Geräts, Gruppe und
  Lampe gleichen Namens), steht die Entitäts-ID dabei: „Kücheninsel
  (light.kuecheninsel_2)“. Nutzt HomeIntent eine feste Geräteauswahl, sagt
  die Antwort das und wo sie ergänzt oder geleert wird; sonst, wo in Home
  Assistant freigegeben wird.
- Unverändert: Ein vorhandenes, nicht freigegebenes Gerät in einem Skript
  wird nie geschaltet, auch nicht nach „Ja“.
- **Ellipse mit unbekanntem Objekt:** „Mach das Flurlicht an.“ → „Und
  Deckenfluter aus.“ mit einem Deckenfluter, den HomeIntent nicht kennt (zum
  Beispiel nicht freigegeben), schaltete das Flurlicht aus. Ein unbekanntes
  Substantiv an Objektstelle ist jetzt ein neues, unbekanntes Objekt: „Ein
  Gerät „Deckenfluter“ finde ich nicht. Ich habe nichts ausgeführt.“ Gefunden
  hat das die Nightly-Property-Suite, nachdem ein Testleck behoben war (Tests
  setzten ihren Geräte-Patch nicht zurück); neue Invariante und
  Live-Szenario `s781-ellipsis-unknown`.

## Was ist in Version 7.8.0 neu?

**Sprachverständnis und Leistung** nach dem unabhängigen Test 7.7
(Bericht: `docs/umsetzung-7.7.1-7.8.md`). Jede Verbesserung ist eine Regel
über Struktur, Lexikon, Modalität, Diskurs oder Ontologie; die Zahl der
Satzmuster steigt nicht (173).

- **Ehrliche Restmeldung:** Kann das Gerät, was verlangt ist, nennt
  HomeIntent den nicht verstandenen Teil („Den Teil „…“ habe ich nicht
  verstanden.“) statt einer falschen Fähigkeitsmeldung.
- **Höflichkeit, Dank, Begründung, Eile** sind Rahmen ohne Wirkung auf Ziel
  und Operation: „Sei so lieb und …“, „Hättest du die Güte, … anzuschalten“,
  „Magst du … runterfahren“, „Wäre super, wenn du …“, „…, danke“,
  „…, wir essen gleich“, „…, fix“. Ein höflicher Konditionalsatz ist nie
  eine Automation.
- **Kurzbefehle:** „Büro an“, „Markise raus“, „Saugroboter los“, „Esszimmer
  Rollladen halb“, „Heizung Schlafzimmer 18 Grad“, „heizung büro auf
  einundzwanzig“; die Einheit folgt aus der Gattung („auf 23“ bei der
  Heizung sind 23 °C).
- **Ellipsen übernehmen die Operation:** „Und in der Küche auf 18“, „Im
  Esszimmer ebenso“, „Dasselbe im Büro“, „Noch eins heller“.
- **Automationen:** verblose Aktionen („Jeden Morgen um sieben die
  Kaffeemaschine an“), Präsenz („Wenn im Wohnzimmer jemand ist …“), der Ort
  des Auslösers begrenzt die Messgröße („draußen wärmer als 25 Grad“ →
  Außentemperatur). Nicht-Admins ohne Freigabe hören die Ablehnung vor der
  Vorschau.
- **Gerät vor Raum/Kontakt:** „Öffne die Garage“ → Garagentor, „Mach die
  Haustür zu“ → Haustürschloss, jeweils mit Bestätigung; die Alarmanlage
  verweist auf den Code.
- **Dialoge und Namen:** „Ja, mach“/„los“ bestätigen; eine neue Frage wird
  beantwortet und die offene Sicherheitsfrage ausdrücklich verworfen; „Gute
  Nacht Test“ schlägt „Gute Nacht“; „Vergiss die Sonnenlampe“ löscht den
  Alias; „Schalte die Gruppe Treppe ein“.
- **Leistung:** Entitätsindex, Weltmodell-Gruppen und Hausgraph werden je
  Registry-/Freigabe-/Alias-Stand gecacht und bei jeder Änderung neu gebaut;
  Zustände bleiben live. Ganzer Turn bei 5000 Entitäten: p50 ≈ 31 ms,
  p95 ≈ 110 ms (7.7.1: p50 394 ms, p95 612 ms).
- **Entwicklungs-Benchmark 7.8** (eigene Paraphrasen): dev 32 → 70/71,
  zurückgehalten 13 → 32/36, `unsafe_execution_count` 0 – ein
  Entwicklungswerkzeug, kein unabhängiger Nachweis.

## Was war in Version 7.7.1 neu?

**Sicherheit nach dem unabhängigen Test 7.7** (1005 blind erstellte Sätze,
`docs/independent-test-7.7.md`). Dort wurde 15-mal ein harmloses Gerät
geschaltet, das nach der Satzbedeutung nicht geschaltet werden durfte. Jede
Ursache ist jetzt eine Regel mit eigener Sicherheitsinvariante
(Bericht: `docs/umsetzung-7.7.1-7.8.md`):

- **Selbstkorrektur ist Satzstruktur:** „Schalte das Radio aus, ich meine den
  Fernseher“, „Licht im Kinderzimmer an, halt, im Schlafzimmer“. Ein
  Korrekturmarker (nein, äh, halt, ich meine, sorry, Moment, also, lieber …)
  trennt Widerruf und Ersatz. Der Ersatz ersetzt nur die Felder, die er
  nennt (Ziel, Ort, Seite, Wert, Operation, Zeit). „…, nein, doch nicht“
  führt nichts aus. Ist die Struktur unklar, fragt HomeIntent mit beiden
  Lesarten. Der widerrufene Teil wird nie ausgeführt, beide Teile nie.
- **Irrealis, Abwägung, Beibehaltung:** „Hätte ich doch …“, „Ich hätte …
  sollen“, „Ich überlege, ob …“ und „Den Fernseher lass bitte aus“ schalten
  nie. Beibehaltung wird bestätigt („Ich lasse den Fernseher aus“).
  **Geändert:** „Lass X aus“ schaltet nicht mehr aus, sondern hält den
  Zustand – wie „Lass X an“ seit 7.3.0.
- **Ellipsen-Vertrag:** Eine Folgeäußerung übernimmt nur Felder, die sie
  nicht selbst nennt. „Den rechten runter“ nach dem linken Rollladen fährt
  den rechten, „Und das Deckenlicht aus“ nie die Stehlampe, „Morgen früh
  wieder an“ wird ein zeitgebundener Auftrag mit Vorschau, „Oben auch“
  überträgt die Rolle (Flurlicht → Flurlicht oben) und erweitert nie die
  Menge.
- **Aufzählungen ohne stille Teilausführung:** „Garten- und
  Terrassenlicht“, „Küche und Esszimmer Rollladen“, „…, dann im Keller und
  in der Waschküche“ werden vollständig gelesen; ein Ort des ersten Teils
  („Im Wohnzimmer das Licht aus und die Rollläden runter“) gilt für die
  folgenden Teile.
- **Satellitenraum hat Vorrang:** Ohne Ortsangabe begrenzt der Raum des
  Sprachsatelliten die Auswahl. Gibt es dort nichts Passendes, sagt
  HomeIntent das und bietet das Gerät eines anderen Raums nur an.
- **Informierte Bestätigung:** Jede Rückfrage zu Skript, Szene, Gruppe oder
  Routine nennt die Wirkungen ab Risiko HIGH aus dem EffectGraph („Das
  Skript Schlafen entriegelt dabei Haustürschloss. Soll ich …?“).

## Was war in Version 7.7.0 neu?

**Architekturabschluss.** Das Verhalten bleibt gleich, der Aufbau wird
eindeutig (Bericht: `docs/architecture-completion-7.7.md`). Jede der Fragen
„Wo entsteht die Bedeutung? Wo wird das Ziel aufgelöst? Wer entscheidet
zwischen zwei Deutungen? Wer autorisiert? Wo wird geschaltet? Warum wurde
etwas ausgeführt?“ hat genau eine Stelle im Code.

- **Bedeutung ohne Altlasten:** Die Meaning IR nutzt öffentliche Primitive
  (Klausellesung, Mengen, Namensindex) statt Interna alter Parser.
- **Ein Entscheider:** Der Arbiter entscheidet auch mit offenem Dialog;
  Rückfrage, Bestätigung, Entwurf und neuer Satz sind typisierte Evidenz.
  Ein offener Dialog senkt nie die Bestätigungspflicht.
- **Zerlegt:** `conversation.py` 8540 → 2191 Zeilen; Controller für Geräte,
  Abfragen, Ziele, Routinen, Komfort, Lernen, Benachrichtigungen,
  Automationen, Verwaltung und Produktivität mit ausdrücklichen
  Abhängigkeiten. Ein Architekturtest hält die Importrichtung fest.
- **Alte Pfade gelöscht:** Erster-Treffer-Matcher, die alten
  hassil-Grammatiken, der historische Zielauflöser und der Legacy/V7-Report
  (rund 25 000 Zeilen). An ihre Stelle tritt eine Signatur-Baseline je
  Korpussatz.
- **Bestätigung an ihre Wirkung gebunden:** Ein „Ja“ führt nur aus, was bei
  der Frage gezeigt wurde; hat sich ein Skript inzwischen geändert (mehr
  Risiko oder andere Ziele), wird neu gefragt. EffectGraph erkennt auch
  Szenen, die ein Skript anlegt.
- **Weniger Satzmuster:** 212 → 173, durch gelöschten und zusammengeführten
  Code, nicht durch Umklassifizieren.
- **Spracherkennung:** Getrennte Komposita aus der Spracherkennung
  („küchen licht“, „außen beleuchtung“, „kinder zimmer licht“) werden nur zu
  exakten Registry-Namen verbunden. Eine gesprochene Wiederholungszahl
  („1000 Mal“) wird nie still weggelassen.
- **Entwicklungs-Benchmark:** 503 eigene Äußerungen in 18 Kategorien, 442/503,
  `unsafe_execution_count` 0; der zurückgehaltene Teil erreichte im ersten
  Lauf 88/113. Das ist ein Entwicklungswerkzeug der umsetzenden Session,
  kein unabhängiger Nachweis; die unabhängige Messung ist der Nachtest
  (für 7.6.0: `docs/nachtest-7.6.0.md`) und wird getrennt berichtet.

## Was war in Version 7.6.1 neu?

Behebt die acht Befunde des unabhängigen Nachtests von 7.6.0. Keiner davon
führte zu einer falschen Geräteaktion.

- **Nicht-Admins legen wieder Automationen an** (Regression seit 7.3.2): Das
  Neuladen der Automationen ist in Home Assistant ein Admin-Dienst. HomeIntent
  prüft die Berechtigung selbst (`allow_non_admin_automations`) und ruft nur
  diese Verwaltungsaufrufe im Systemkontext des Turns auf (ohne Benutzer, mit
  dem Turn als Eltern-Kontext). Gerätewrites laufen weiter mit dem Kontext des
  sprechenden Benutzers. Ablehnungen von Home Assistant erscheinen nie roh
  oder englisch.
- **Namen mit Grußformel:** „Aktiviere Guten Morgen.“ startet die Szene. Wörter
  eines im Satz genannten Geräte- oder Aliasnamens sind keine Zeitangabe. Die
  Rückfrage nennt die Gattung („Welche Szene meinst du …“).
- **Mengen bei relativen Änderungen:** „zwei Grad wärmer“, „um 20 Prozent
  heller“, „zehn Prozent lauter“, „30 Prozent höher“, „um drei Grad hoch“ –
  in Ziffern und Worten; Gerätegrenzen gelten. „um drei Grad“ ist keine
  Uhrzeit mehr.
- **„oben“/„unten“:** eine Regel für Etage, Richtung und Stellung. Bei „gibt
  es“, „wie viele“, „welche“ und Befehlen mit Ort ist es die Etage; nur die
  Zustandsfrage („Sind die Rollläden oben?“) meint die Stellung. „unten“ ist
  überall das Erdgeschoss.
- **Nicht freigegebene Geräte** werden so benannt („Saugroboter ist für
  HomeIntent nicht freigegeben.“, für Admins mit Hinweis, wo man das ändert);
  ihre Nebenentitäten sind kein Ersatzziel.
- **Verschmelzungen** („fürs“, „ins“, „ans“, „aufs“ …) gehören zur gemeinsamen
  Normalisierung.
- **„Mach alles für die Nacht fertig.“** bietet vorhandene Routinen an wie „Ich
  gehe schlafen.“; angelegt wird nur, wenn es keine gibt.
- **Abschwächungspartikel** („Könntest du vielleicht irgendwann mal …“) ändern
  die höfliche Bitte nicht; eingebettete Fragen bleiben Fragen.

## Was war in Version 7.6.0 neu?

**Generalisierung: Bedeutungsklassen statt einzelner Sätze.** Abschluss des
Umbaus 7.3.1 – 7.6.0 (Bericht: `docs/umsetzung-7.3.1-7.6.md`).

- **Verbklassen:** anwerfen, anknipsen, anschmeißen, starten („Starte die
  Kaffeemaschine“), rauf/herauf/„nach oben“, höher/niedriger als Stufe bei
  Licht, Heizung, Medien und Ventilator; verblose Kurzbefehle mit Wert
  („Heizung Wohnzimmer auf 22 Grad“, „Rollladen Wohnzimmer auf 50 Prozent“).
- **Bedürfnisse:** „Ich kann kaum lesen“ (Licht), „Das Radio nervt“
  (leiser/aus als Vorschlag). Die Wirkung hängt wie bisher von
  `implicit_action_level` ab.
- **Situationsfragen:** „irgendwo“, „Steht noch ein Fenster offen?“,
  „Sind alle Rollläden unten?“ (unten/oben nach einer Beschattung ist eine
  Position, keine Etage), schwache Batterien ohne Zahl, „Läuft die
  Waschmaschine noch?“. Existenzantworten nennen die Geräte („Ja,
  Badezimmerfenster und Küchenfenster sind geöffnet.“), Allantworten die
  Ausnahmen („Nein, nicht alle … Nicht geöffnet: Rollladen Küche.“).
- **Diskurs:** „Und den rechten auch.“ wiederholt die letzte Aktion am
  Gegenstück (links/rechts, oben/unten, vorne/hinten). „Vergiss es“ oder
  „Lieber nicht“ direkt nach einer Ausführung sagt ehrlich, dass schon
  ausgeführt wurde, und bietet „Mach das rückgängig“ an („Rückgängig.“
  genügt); es wird nichts ungefragt zurückgenommen.
- **Höflichkeit:** „Wärst du so lieb und machst …“, „Es wäre nett, wenn du …
  ausmachst“, „Kannst du mal eben …“, „Kannst du das Küchenlicht an?“ sind
  Bitten. Verneinungen und echte Bedingungen bleiben es nicht.
- **Mehrfachbefehle:** „dort“ im zweiten Teil bindet an den einzigen Ort des
  vorigen Teils und verhält sich genau wie die ausdrückliche Ortsangabe;
  bei keinem oder mehreren Orten wird nichts eingesetzt.
- **Test-Reset:** Der Admin-Dienst `homeintent.reset_test_state` vergisst
  Gesprächskontext, offene Rückfragen und den Ausführungs-Trace (gelernte
  Bindungen nur mit `include_bindings`), damit Messreihen unabhängig sind.

**Wert auf ungesehenen Sätzen.** Den unveröffentlichten Korpus kennt dieses
Repository bewusst nicht. Gemessen wurde mit eigenen Paraphrasen:

| Satz | 7.5.2 | 7.6.0 |
|---|---|---|
| Entwicklungskorpus Phase 9 (81 Fälle, `tests/eval/generalisierung_76.json`; an ihm wurde entwickelt) | 57 / 81 (70 %) | 81 / 81 |
| Zurückgehaltene Paraphrasen (32 Fälle, erst nach den Änderungen geschrieben, nicht nachgebessert) | 14 / 32 (44 %) | **30 / 32 (94 %)** |

Die zwei Fehlschläge im zurückgehaltenen Satz: „Wo ist es am kühlsten?“
(Synonym zu „am kältesten“ fehlt) und „Ist die Spülmaschine schon fertig?“
(das Testhaus hat keine Spülmaschine; die Antwort „Ziel nicht gefunden“ ist
richtig, die Erwartung war falsch).

**Zielwerte des Umbaus 7.3.1 – 7.6.0:**

| Kennzahl | 7.3.0 | Ziel | 7.6.0 |
| --- | --- | --- | --- |
| Nicht freigegebene Geräte über Skript/Szene/Gruppe schaltbar | ja | nein | **nein** (Unit + live) |
| Skript/Szene mit unvollständigem EffectGraph als LOW | ja | nie | **nie** |
| Dienstaufrufe mit HA-Kontext | 0 / 45 | alle | **45 / 45** (AST-Test) |
| „Warum ist X angegangen?“ mit belegter Kette | nein | ja | **ja** (VERIFIED/POSSIBLE/UNKNOWN) |
| Routinewahl über Namensähnlichkeit ohne Bestätigung | ja (Szenen) | nie | **nie** |
| Geraten statt gefragt (Ort, Einmaligkeit) | vorhanden | 0 | **0** bekannte Fälle |
| Verletzte Sicherheitsinvarianten (Property-Suite) | – | 0 | **0** (19 Invarianten, Nightly 300 Beispiele) |
| SAFETY_DRIFT bei jedem Umschalten | – | 0 | **0** |
| Zielauflösungen im Code | 2 + private | 1 | **1** (`resolve_phrase`, Architekturtest) |
| `SEMANTIC_SENTENCE_PATTERN`-Regex | nicht gezählt | sinkend | 272 → 258 → **212** (7.6.0: keine neuen) |
| Gelerntes Wort wirkt in verschiedenen Satzformen | 3 von 4 | ≥ 10 | **10** Befehlsformen + Frage, Zeitauftrag, Verneinung |
| Unveröffentlichter Korpus | 38 % | ≥ 65 % | hier nicht messbar; eigene ungesehene Paraphrasen 44 % → **94 %** |
| Funktionsszenarien live | 162/162 | nicht schlechter | 155/155 im CI-Lauf; 4 Szenarien auf Vorschlag + „Ja“ umgestellt |
| Latenz p95 | < 100 ms | < 100 ms | **< 100 ms** (Benchmarks mit 5000 Entitäten) |

## Was ist in Version 7.5.2 neu?

**Die übrigen Sprachinseln: Listen und Timer, Kalender, Haushaltsfragen,
Ziele/Prozeduren und Erinnerungen.**

- Mehrwortausdrücke sind Lexikondaten (`nlu/phrases.py`): Phrasen mit
  Alternativen und Wortstämmen („was steht|ist|fehlt“, „erledig*“) werden
  auf Wort-Tokens mit Zeichenpositionen geprüft. Namens-, Titel- und
  Nachrichten-Platzhalter werden über Wortgrenzen bestimmt, nicht über
  Satzmuster.
- Umgestellt und die alten Satzmuster gelöscht:
  - Listen und Timer: Anzeigen, Abhaken, Fülltexte, Beschreibung,
    Timername, Antwort auf „Wie soll der Timer heißen?“
  - Kalender: Liste, „Wann ist mein …“, freie Zeit, Zeitfenster,
    Umbenennen, Dauer ändern, Verschieben, ganztägig, halbe Stunde, Titel
  - Haushaltsfragen: Uhrzeit, Datum, Anwesenheit, Raumfrage,
    Hausverbrauch, Durchschnittstemperatur, Solltemperatur, Probleme,
    Batteriegrenze, Sonne, Wetter, Vorhersage, Szenen und Skripte
  - Ziele/Prozeduren: speichern, starten, vergessen, auflisten
  - Erinnerungen: „Sag … Bescheid“, „Benachrichtige …“, Ruhezeiten
- Shadow je Insel gegen 7.5.1 (alle Korpussätze, alle Satzliterale der
  Testsuite, erzeugte Inselkorpora), jeweils **0 Abweichungen**:

  | Insel | Sätze | mit Frame |
  |---|---|---|
  | Listen/Timer | 5257 | 1931 |
  | Kalender | 5218 | 178 |
  | Haushalt | 5214 | 56 |
  | Ziele | 5200 | 49 |
  | Erinnerung | 5197 | 19 |
- SEMANTIC_SENTENCE_PATTERN: 272 (7.5.0) → 258 (7.5.1) → **212** (7.5.2).

## Was ist in Version 7.5.1 neu?

**Bedeutung statt Satzmuster: die ersten zwei Sprachinseln.**

- **Regex-Klassifikation:** Alle 773 Regex-Stellen der Integration sind
  einmal klassifiziert (`docs/regex-klassifikation.json`, erzeugt von
  `scripts/regex_inventory.py`). Die Klassen sind LEXICAL, MORPHOLOGICAL,
  STRUCTURAL und SEMANTIC_SENTENCE_PATTERN. Nur die letzte wird abgebaut und
  pro Release gezählt: 272 in 7.5.0, **258** in 7.5.1. Ein Test hält die Datei
  aktuell und verhindert, dass die Zahl steigt.
- **Verlauf** (`nlu/history_frame.py`): Statistik, Zustandsfragen („wie
  oft“, „wie lange“, „wann zuletzt“) und Zeiträume werden aus Wörtern und
  kleinen Lexikontabellen abgeleitet. Die kanonischen Frames
  (`HistoryQuery`, `StateHistoryQuery`, `ComparativeHistoryQuery`) sind
  unverändert. Der alte Parser samt Satzmustern ist gelöscht.
- **Automationsverwaltung** (`nlu/management_frame.py`): Die 16 Arten
  (anzeigen, erklären, simulieren, duplizieren, pausieren, verschieben …)
  sind Zeilen einer Frame-Tabelle mit Stichwörtern, Objekt,
  Namensgrenzen und Parametern. Der alte Parser samt Satzmustern ist
  gelöscht.
- **Shadow je Insel** (`scripts/island_shadow.py`): Der alte Code-Stand (Git)
  und der neue werden auf allen Korpussätzen, allen Satzliteralen der
  Testsuite und einem erzeugten Inselkorpus verglichen. Ergebnis: Verlauf
  6069 Sätze (721 mit Frame), Automationsverwaltung 5360 Sätze (209 mit
  Frame), jeweils 0 Abweichungen.

## Was ist in Version 7.5.0 neu?

**Eine gemeinsame Bedeutungsebene und ein Schiedsrichter statt „wer zuerst passt“.**

- **Bedeutungsebene erweitert, nicht neu gebaut.** `MeaningClause` trägt jetzt
  neben Sprechakt, Modalität und Polarität auch diese Felder:
  - Operation, Ziel (Gattung, Ort, Menge, Merkmal, Referenz, ausdrücklich
    genannte Geräte), Wert
  - Zeit (jetzt / einmalig / wiederkehrend / später ohne Angabe)
  - Bedingungen, Ausnahmen
  - Herkunft (ausdrücklicher Befehl / Bedürfnis)
  - unerklärter Rest, Evidenz

  `nlu/meaning_ir.ground_meaning` füllt sie aus den vorhandenen Analysen.
  Es gibt keinen neuen Bedeutungstyp, und die Ebene ruft nie einen Dienst
  auf.
- **Arbitration** (`arbitration.py`): Parser, Bedürfnis, Alarmanlage,
  „kann aus“ und Situationsfragen liefern Kandidaten mit Wirkung, Zielen,
  Autorität und Rest. Die Regeln:
  - Ein ausführbarer Kandidat ohne Rest wird ausgeführt.
  - Kandidaten mit gleicher Wirkung werden zusammengeführt.
  - Bei Widerspruch entscheidet eindeutige Evidenz: Eine ausdrückliche Frage
    schlägt einen Befehl, ein ausdrücklich genanntes Gerät schlägt ein
    Bedürfnis. Sonst wird nachgefragt oder nichts getan.
  - Mit Rest wird nie ausgeführt.
  - Zeitgebundenes und Bedingtes wird nie sofort ausgeführt.
- **Erst Shadow, dann umgeschaltet:** Der Arbiter lief gegen die Kaskade der
  Konversation über 2052 Sätze (alle Korpora plus neuer Kollisionskorpus
  mit 30 Sätzen). Ergebnis: 2045/2045 messbare Sätze gleichwertig, 0
  SAFETY_DRIFT. Umgeschaltet ist die Entscheidung Bedürfnis ↔ Frage. Die
  übrigen Paare (Automation ↔ zeitversetzter Befehl, Routine ↔ Szenenname)
  entscheiden im Shadow gleich und werden in 7.5.x Handler für Handler
  umgestellt. `scripts/arbiter_shadow.py --check` ist ein CI-Schritt.
- **Nebenbei behoben:** „Mach jetzt das Flurlicht an“ und „Schalte sofort …“
  wurden bisher nicht ausgeführt („jetzt“ galt als Zeitplanung).

## Was ist in Version 7.4.1 neu?

**HomeIntent lernt die Sprache deines Haushalts – nur nach deinem „Ja“.**

Alles Gelernte liegt im Bindungsspeicher. Es ist ein Baustein der Bedeutung,
kein fester Satz, und berechtigt zu nichts: Jede Nutzung läuft weiter durch
Zielauflösung, Validator, EffectGraph und Ausführungsrichtlinie.

- **Unbekannte Wörter werden erfragt.** „Schalte den Zauberkasten aus“ →
  „Was meinst du mit ‚Zauberkasten‘? 1. … 2. …“. Die Optionen ergeben sich aus
  Ort und Aktion. Nach der Antwort wird der Befehl ausgeführt, danach fragt
  HomeIntent: „Soll ich mir ‚Zauberkasten‘ als Namen für die Stehlampe
  merken?“ Ein Befehl mit unbekanntem Wort wird nie mehr stillschweigend mit
  dem Gerät der vorigen Frage ergänzt.
- **Gelernte Namen wirken überall.** Ein gelernter Name wirkt in Befehl,
  Kurzform, Frage, Mehrfachbefehl, Dimmen, Zeitauftrag, Verneinung,
  Ausnahme und „lass an“, auch für andere Personen im Haushalt. Er wird als
  Alias an die Geräte der einen Zielauflösung gehängt.
- **Standardauswahl aus Rückfragen und Korrekturen.** Beantwortest du dieselbe
  Rückfrage (dieselben Kandidaten, derselbe Ort) zweimal gleich, fragt
  HomeIntent einmal, ob das künftig ohne Rückfrage gelten soll. Dasselbe gilt
  für Korrekturen wie „Nein, ich meinte die rechte“ oder „Nein, die
  Nachttischlampe rechts“. „Die rechte“ nach „Nachttischlampe links“
  versteht HomeIntent neu als das Geschwistergerät. Ein ausdrücklich
  genanntes anderes Gerät hat immer Vorrang. Standardauswahlen gelten pro
  Person.
- **Vorlieben werden benutzt.**
  - Merken: „Wenn ich lese, möchte ich die Stehlampe auf 60 Prozent.“
  - Abfragen: „Wie hell möchte ich lesen?“
  - Anwenden: „Ich lese jetzt“ bzw. „Ich will lesen“. Beim ersten Mal kommt
    eine Vorschau, danach wird direkt ausgeführt.
  - Löschen: „Vergiss, wie hell ich lesen möchte.“
- **Sprachmakros.** „Wenn ich ‚Kinoabend‘ sage, dann mach das Wohnzimmer
  Deckenlicht aus und fahre die Rollläden runter.“ Das wird nach „Ja“ als
  Satz gespeichert, nicht als Automation. Jeder Aufruf läuft wie ein
  gesprochener Befehl durch alle Prüfungen; kritische Schritte fragen
  weiterhin nach.
- **„Was weißt du über mich?“** zählt Namen, Standardauswahlen, Vorlieben und
  Makros auf Deutsch auf. Wirkungslose Einträge (Gerät nicht mehr
  freigegeben) sind markiert. „Vergiss …“ löscht gezielt.
- **Sicherheit beim Lernen:**
  - Gespeichert wird nur nach „Ja“.
  - Es gibt nur freigegebene Ziele; ein entzogenes Ziel macht die Bindung
    wirkungslos.
  - Geräte-, Raum- und Gattungsnamen werden nie überschrieben.
  - Namen für Schlösser, Alarmanlagen, Sirenen, Ventile, Tore und Türen
    vergeben nur Administratoren.
  - Namen gelten für den Haushalt, raumbezogene Namen, Standardauswahlen und
    Vorlieben pro Person.
- **Deutsche Bezeichnungen.** Gewohnheiten, Modelle und Status erscheinen
  auf Deutsch („Zuverlässigkeit“, „morgens“, „noch unsicher“). Ist das
  Gedächtnis ausgeschaltet, sagt HomeIntent, wo man es einschaltet.
- **Zeitraffer-Test:** Zwei simulierte Wochen Nutzung. Gewohnheiten werden
  nur vorgeschlagen, es entsteht nie eine Automation, und ein abgelehnter
  Vorschlag kommt nicht wieder.

## Was ist in Version 7.4.0 neu?

**Eine Zielauflösung für alle Namen.**

- Jede Frage „welches Gerät ist mit diesem Namen gemeint?“ läuft jetzt durch
  genau eine Funktion, `resolve_phrase` in `nlu/target_resolution.py`. Das
  betrifft Befehle, Abfragen, Automationen (Auslöser, Bedingungen, Ziele),
  Korrekturen, Alias-Lernen, Ausnahmen und Schlösser. Die Namensstufe (exakte
  Namen und Aliasse, Teilnamen, begrenzte Tippfehler-Korrektur) ist aus dem
  historischen Resolver übernommen. Dazu kommen die Regeln dieses Moduls:
  - Kandidaten sind nur die freigegebenen Geräte.
  - Eine Korrektur überschreitet nie die genannte Gerätegattung. Aus
    „Rollladen Büro“ wird nie mehr das Bürolicht. Ein Gerät, das die Gattung
    selbst im Namen trägt („Licht Sportraum“ als Schalter), bleibt Kandidat.
  - Exakte Registry-Namen bleiben maßgeblich.
  - Mehrdeutigkeit bleibt Mehrdeutigkeit. Nur ein gesprochener Ort engt ein.
    Für die Rückfrage gibt es eine nummerierte Form („Welches Gerät meinst du:
    1. …, 2. … oder 3. …?“).
  - Befehlswörter wie „Automation“, „Skript“ und „Szene“ gelten nicht als
    Gerätegattung.
- Umgestellt wurde erst nach einem Shadow-Lauf alt gegen neu bei jedem Aufruf:
  438 Aufrufe im Korpus, 1743 in der Testsuite, 0 SAFETY_DRIFT, 0 Fälle „alt
  besser“. Ende-zu-Ende blieben alle 2022 Korpussätze gleichwertig.
  `scripts/resolver_shadow.py --check` bleibt als CI-Schritt: Der neue
  Resolver darf nie neue Ziele liefern oder eine Rückfrage weglassen, die der
  historische gestellt hätte.

## Was ist in Version 7.3.4 neu?

**Sicherheitsnetz für die großen Umbauten.**

- **Generative Sicherheitsinvarianten** (`tests/test_safety_properties.py`,
  `hypothesis` nur als Testabhängigkeit): Sätze werden aus den Bausteinen des
  Lexikons zusammengesetzt (Gattungen mit Genus, Plural und Synonymen, Orte des
  Testhauses, Artikel, Höflichkeit, Negation, Zeit, Nebensätze, Ausnahmen) –
  keine festen Sätze. 17 Invarianten, jede als eigener Test: Negation, Frage,
  Vergangenheit und Kontrafaktisches schreiben nie; ein unbekanntes Wort
  vergrößert nie die Zielmenge; Ziele bleiben in der genannten Gattung;
  unbekannte Ausnahmen und halb verstandene Mehrfachsätze führen nichts aus;
  Zeitaufträge laufen nie sofort; Einzahl bei mehreren Treffern fragt nach;
  indirekte Herkunft ist nie lockerer; das Risiko eines Skripts ist mindestens
  das seiner stärksten Wirkung; ein unvollständiger EffectGraph ist nie LOW;
  mehrdeutige Bedeutung, nicht freigegebene Wirkziele und gelernte Bindungen
  auf solche Ziele schreiben nie. In CI mit festen Seeds, nächtlich mit
  wechselnden Seeds; Gegenbeispiele werden als feste Regressionen übernommen.
- **Allgemeiner Shadow-Vergleich** (Erweiterung von
  `nlu/understanding.py`): Verhaltenssignaturen (Sprechakt, Operation,
  Gattung/Domäne, Ziele, Ort, Menge, Herkunft, Risiko, Bestätigungspflicht,
  Plan) und Drift-Klassen `EQUIVALENT`, `REFINEMENT`, `BEHAVIOR_CHANGE`,
  `SAFETY_DRIFT`. Offline über alle veröffentlichten Korpora
  (`scripts/shadow_compare.py`, 2022 Sätze; `--check` scheitert bei
  SAFETY_DRIFT) und optional live (`shadow_mode: log`): ausgeführt wird immer
  nur die aktive Pipeline, Kandidaten werden nur protokolliert (Satz-Hash,
  beide Ergebnisse, Drift-Klasse) – sichtbar in den Diagnosedaten und im
  Learning Center. SAFETY_DRIFT blockiert jedes Umschalten.

## Was ist in Version 7.3.3 neu?

**Gelernte Routinen, vorsichtige Bedürfnisse, nie raten.**

- **Routine-Bindungen.** „Ich gehe schlafen“, „Gute Nacht“, „Filmabend“ oder
  „Starte die Schlafroutine“ suchen beim ersten Mal nach passenden Skripten und
  Szenen und **fragen**: „Welche Routine meinst du: Schlafen und Gute Nacht?“
  bzw. „Meinst du mit schlafen gehen das Skript Gute Nacht?“. Erst nach deiner
  Wahl oder deinem „Ja“ wird die Zuordnung gespeichert; danach sucht HomeIntent
  für diesen Anlass nicht mehr nach Namensähnlichkeit. Jede Ausführung prüft
  trotzdem wieder Freigabe, EffectGraph und Richtlinie; wird die gewählte
  Routine abgelehnt (z. B. weil sie einen nicht freigegebenen Saugroboter
  startet), wird auch nichts gespeichert. Ist das gebundene Ziel verschwunden
  oder nicht mehr freigegeben, führt HomeIntent nichts aus und bietet eine neue
  Zuordnung an.
- Per Sprache steuerbar: „Vergiss die Schlafroutine“, „Schlafen ist ab jetzt das
  Skript Gute Nacht“, „Welche Routine nutzt du für den Filmabend?“. Im Learning
  Center (Tab Autonomie) sind alle Zuordnungen sichtbar und löschbar.
- **Implicit Action Policy.** Neue Option `implicit_action_level` (auch im
  Learning Center einstellbar):

  | Stufe | Bedürfnis („Mir ist kalt“) | abgeleitete Routine |
  | --- | --- | --- |
  | `understand_only` | nur Antwort | nur Antwort |
  | `propose` (**Standard**) | Vorschlag + „Ja“ | Vorschlag + „Ja“ |
  | `low_risk_auto` | Harmloses direkt, sonst Vorschlag | Vorschlag + „Ja“ |
  | `bound_routines_auto` | wie `low_risk_auto` | gebundene Routine direkt, wenn unter der Bestätigungsschwelle |

  Eine indirekte Herkunft ist nie lockerer als derselbe ausdrückliche Befehl;
  NEVER_AUTO gilt unverändert.
- **Nie raten.**
  - „hier“/„da“ nur aus dem Bereich des Sprachsatelliten oder einem im Gespräch
    genannten Ort, sonst „In welchem Raum?“; die Antwort nennt immer den Ort.
  - „Schalte um 22 Uhr das Licht aus“ ist ein **einmaliger** Auftrag (nächstes
    22:00). Mit „jeden Tag“, „immer“, „täglich“, „werktags“ … entsteht eine
    wiederkehrende Automation. Bei Sonnenauf-/-untergang und „wenn es dunkel
    wird“ ohne solches Wort fragt HomeIntent: „Nur heute oder jeden Tag?“
  - Ehrliche Begründungen aus den echten Fähigkeiten: „Flurlicht lässt sich nur
    ein- und ausschalten.“ statt „unterstützt die Aktion nicht“. „Dreh da die
    Heizung hoch“ nach einer Temperaturfrage erhöht den Sollwert.
  - „Lass das Licht so, wie es ist“ ändert nichts und sagt das; ein „Ja“ ohne
    offene Frage wird ehrlich beantwortet.

## Was ist in Version 7.3.2 neu?

**Nachvollziehbar, was HomeIntent ausgelöst hat.**

- **Home-Assistant-Kontext an jedem Dienstaufruf.** Jede Äußerung, die zu einer
  Ausführung führt, bekommt genau einen HA-`Context` (mit dem sprechenden
  Benutzer); alle Aufrufe dieser Äußerung – auch Skripte, Undo und
  Mehrfachbefehle – tragen ihn. Dessen ID ist die `execution_id`. Home
  Assistant kann Folgeeffekte (Skripte, ausgelöste Automationen) damit der
  HomeIntent-Aktion und dem Nutzer zuordnen und prüft zusätzlich dessen
  eigene Entitätsberechtigungen. Proaktive Aktionen und Daueranweisungen laufen
  mit einem Kontext ohne Benutzer.
- **Ausführungsprotokoll (ExecutionTrace).** Ein begrenzter Ringspeicher
  (Standard 500 Ausführungen, 14 Tage, einstellbar) hält je Ausführung Satz
  (gekürzt oder nur als Hash), Herkunft, Plan, geprüfte Wirkung und Risiko fest
  – mit gehashter Benutzerkennung wie im Audit. Er verweist auf HA-Daten statt
  sie zu kopieren.
- **„Warum ist der Saugroboter angegangen?“** beantwortet HomeIntent aus der
  Kontextkette von Home Assistant: „Du hast um 22:13 ‚Aktiviere Nachtruhe‘
  gesagt. Ich habe das Skript Nachtruhe gestartet. Dessen Schritt ‚Saugen
  starten‘ hat Saugroboter gestartet.“ Auch fremde Ursachen werden genannt,
  soweit HA sie belegt (Automation X, ausgelöst durch …; Anna in der App).
  Belegstufen: **belegt** (Kontextkette), **möglich** (nur zeitliche Nähe –
  immer als Vermutung formuliert) und **unbekannt**. Ohne Beleg gibt es keine
  erfundene Kausalkette.
- **Learning Center:** neuer Abschnitt „Was hat HomeIntent ausgelöst?“ im
  Tab Aktivität (Admins sehen den Haushalt, alle anderen nur eigene
  Ausführungen).
- Neue Optionen: `trace_limit`, `trace_days`, `trace_store_text`.

## Was ist in Version 7.3.1 neu?

**Transitive Sicherheit für Skripte, Szenen und Gruppen.** Bisher prüfte
HomeIntent bei „Aktiviere Nachtruhe.“ nur, ob das Skript selbst freigegeben
ist – nicht, was es schaltet. Ein realer Vorfall (ein Skript drückte per
`floor_id` alle Buttons einer Etage; Saugroboter und Brandmelder-Selbsttest
liefen los) zeigte die Lücke. Jetzt:

- **EffectGraph:** HomeIntent liest Skripte, Szenen, Gruppen und per
  `automation.trigger` ausgelöste Automationen nur lesend aus und ermittelt
  alle wirksamen Ziele – über alle Zweige, Verschachtelungen, Geräte-Aktionen
  und `device_id`/`area_id`/`floor_id`/`label_id` so, wie Home Assistant sie
  auflöst. Details: [Skripte, Szenen und Gruppen](#skripte-szenen-und-gruppen-transitive-prüfung-seit-731).
- **Freigabe gilt transitiv:** Schaltet ein Skript ein nicht freigegebenes
  Gerät, entscheidet seit 7.8.2 die Option `routine_unexposed_effects`
  (Standard `allow`: ein ausdrücklich genanntes Skript läuft; Schlösser,
  Alarm, Rollläden/Tore und Ventile nie). Entitäten, die Home Assistant nicht
  kennt, schalten nichts und zählen nicht (seit 7.8.1). Eine Ablehnung sagt,
  wo freigegeben wird (feste HomeIntent-Auswahl oder Assist).
- **Risiko = höchste Wirkung:** Ein Schloss im Skript macht das Skript HIGH,
  eine Alarmanlage CRITICAL; ein reines Lichtskript bleibt LOW.
- **Nicht prüfbare Schritte** (Vorlagen, `event:`, `shell_command` …) gelten
  nie als harmlos: Standard ist Ablehnen, neue Option `effect_graph_unknown:
  confirm` fragt stattdessen nach.
- **Geprüft wird direkt vor dem Schalten**, auch nach einer Bestätigung.
- **Abgeleitete Routinen** („Ich gehe schlafen“, „Filmabend“, „Starte die
  Schlafroutine“) starten nie ohne Bestätigung – auch Szenen nicht.
- Nach dem Ausführen nennt HomeIntent kurz die geprüfte Wirkung
  („Schlafen ausgeführt: 2 Rollläden und 2 Lichter.“).

## Was ist in Version 7.3.0 neu?

**HomeIntent 7.3.0 — Sprachverständnis ohne Sprachmodell.** HomeIntent
versteht Alltagsdeutsch jenseits exakter Gerätenamen und fester Satzformen –
weiterhin ohne LLM, ohne ML-Modell und ohne externe Dienste. Wissen liegt als
Daten vor (Geräte-Ontologie, Orte, Bedürfnis- und Sichtentabellen), Regeln
arbeiten über Bedeutungsbausteine statt über Satzschablonen. Parser führen
weiterhin nichts aus; Validator, ExecutionPolicy, Bestätigungen und
`NEVER_AUTO` sind unverändert. Details: [`docs/architecture-v13.md`](../architecture-v13.md)
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
(Bericht und Nachtest: [`docs/testbericht-live-simulation-7.1.2.md`](../testbericht-live-simulation-7.1.2.md)).
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

Details: [`docs/learning-center.md`](../learning-center.md).

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
[docs/architecture-v12.md](../architecture-v12.md).

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
[`docs/architecture-v11.md`](../architecture-v11.md).

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
[`docs/homeintent-rename-migration.md`](../homeintent-rename-migration.md),
die echte Architektur in [`docs/architecture-v10.md`](../architecture-v10.md).

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
[`docs/architecture-v9.md`](../architecture-v9.md) beschrieben.

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
[`docs/perf/v9-completion.md`](../perf/v9-completion.md). Hassfest, HACS und
der stabile Home-Assistant-Container-Smoke waren im selben CI-Lauf grün.

Ältere Änderungen stehen in den
[GitHub-Releases](https://github.com/pquandel2-alt/homeintent/releases).
