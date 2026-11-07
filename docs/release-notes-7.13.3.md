# HomeIntent 7.13.3

7.13.3 ist der veröffentlichte Patch für die unabhängige Nachprüfung von
7.13.2. Der Release basiert auf dem commitfest geprüften Stand
`0fb881c9e00959bedeb97ce0e3733887b3b8235e`.

## Vollständige Schreibabdeckung

- Das gemeinsame Strukturmodell bildet `und`, `und dann`, `danach`,
  `anschließend`, `sowie`, `außerdem`, Komma, Semikolon und Satzgrenzen ab.
- Jede ausführbare Klausel bindet Operation, Zielmenge, Ort, Quelle oder Wert,
  verbrauchte Eingabespannen und ihren konkreten Serviceplan.
- Ein fehlendes Ergebnis bedeutet nicht mehr, dass der Goal-Router dieselbe
  Eingabe global als andere Schreibaktion neu deuten darf.
- Unvollständige, unbekannte oder mehrdeutige Klauseln verwerfen den gesamten
  Auftrag vor Vorschau und Write. Ein späteres „Ja“ findet keinen Restplan.
- Relationen werden für die Ausführung nach Quellspanne sortiert. Dadurch
  bleiben Kombinationen aus Komma und Konjunktion in gesprochener Reihenfolge.

## Cover-Pflichtbestätigung

Die Bestätigungspflicht ist jetzt eine eigene Policy-Eigenschaft und keine
Folge der frei konfigurierbaren Risikoschwelle:

| Cover | Risiko | Pflichtbestätigung bei Bewegung |
| --- | --- | --- |
| `shutter`, `blind`, `shade`, `curtain` | grundsätzlich `MEDIUM` | normale Schwellenregel |
| `garage`, `garage_door`, `gate` | `CRITICAL` | ja |
| `door`, `window`, `awning`, `damper` | mindestens `HIGH` | ja |
| fehlende, sonstige oder unbekannte Klasse | mindestens `HIGH` | ja |

Das gilt für Öffnen, Schließen, Positionieren und für generische HA-Helfer,
die dieselbe Cover-Bewegung auslösen. Statisch bestimmbare Effekte von
Gruppen, Szenen und Skripten werden einbezogen. Vor einem bestätigten Write
werden Berechtigung, Freigabe, Verfügbarkeit, Geräteklasse, effektiver Dienst,
Ziele und statische Parameter erneut geprüft. HomeIntent behauptet weder eine
physische Transaktion noch einen automatischen Hardware-Rollback.

## Anwesenheit und hausweiter Scope

- „Ich bin zu Hause“ und „Wir sind zuhause“ sind Anwesenheitsaussagen, kein
  implizites „alle Geräte im Haus“.
- Anwesenheitsbedingungen bleiben Bedingungen.
- Ein echter Hausquantor erweitert nur innerhalb des gesprochenen Gerätetyps:
  Garagentore bleiben Garagentore, Markisen Markisen, Fenster passende
  Cover-Fenster und Rollläden Rollläden.
- Gibt es kein passendes Ziel, wird nicht auf die gesamte Domain ausgewichen.

Die 7.13.2-Pipeline hatte die geprüften Anwesenheitsbeispiele bereits ohne
Write abgelehnt; dort war der Fehler nur im internen Ortsmodell sichtbar. Die
zu breite Domain-Auswahl bei ausdrücklichen Hausquantoren war dagegen direkt
im ausführbaren Plan reproduzierbar und ist behoben.

Der [HACS-PR #10191](https://github.com/hacs/default/pull/10191) wurde am
4. November 2026 von Frenck genehmigt und gemergt. 7.13.3 setzt seinen
nicht blockierenden Abschlusshinweis zu Anwesenheit und Gerätetypbindung um.

## Medien-Mehrbefehle und Fortsetzungen

Exakte `source_list`-Einträge und eindeutig aufgelöste Music-Assistant-
Treffer können jetzt als eigene Operation in einem Mehrbefehl stehen. Für
„Spiel WDR 2 in der Küche und pausiere die Musik im Schlafzimmer“ wird erst
der Küchenplayer mit exakt `WDR 2` gewählt und danach ausschließlich der
Schlafzimmerplayer pausiert. Ein unbeteiligter Wohnzimmerplayer bleibt außen
vor.

Unbekannte oder mehrdeutige Quellen verwerfen weiterhin den gesamten Auftrag.
Bei einem einzelnen Quellenbefehl kann der vorhandene semantische Dialog nur
den fehlenden Player oder Raum ergänzen; Abbruch und unabhängiger Themenwechsel
verwerfen den Entwurf. Eine Ortskorrektur nach bereits erfolgter Wiedergabe
startet die Quelle am neu genannten Player und weist auf den schon erfolgten
ersten Call hin. Es wird keine Gruppe erfunden.

Bei einem Laufzeitfehler stoppt der Mehrplan am fehlerhaften Schritt. Die
Antwort nennt bereits erfolgreich behandelte Schritte, den nicht ausgeführten
Schritt und übersprungene Folgeschritte. Es wird kein automatischer Rollback
behauptet.

## Tests, Nightly und Grenzen

Der Nightly-Sicherheitsjob enthält nun die Clause-, Cover-, Issue-#38- und
Mediendialogregressionen. Das Nightly-Profil exploriert mehr Beispiele und
protokolliert die GitHub-Run-ID als reproduzierbaren Hypothesis-Seed. Das
SHA-genaue Release-Gate bleibt unverändert streng.

Die tatsächlichen lokalen Abschlusszahlen, Performancewerte und der geprüfte
Commit stehen im [Umsetzungsbericht](umsetzung-7.13.3.md). Reale Sonos- oder
Music-Assistant-Hardware und hörbare Audiowiedergabe wurden nicht automatisiert
geprüft; dafür bleibt die [manuelle Matrix](music-assistant.md#e2e-status-und-manuelle-matrix)
verbindlich.

## Geprüfte Freigabe

- Commit: [`0fb881c9e00959bedeb97ce0e3733887b3b8235e`](https://github.com/pquandel2-alt/homeintent/commit/0fb881c9e00959bedeb97ce0e3733887b3b8235e)
- CI: [Lauf 42357216342](https://github.com/pquandel2-alt/homeintent/actions/runs/42357216342)
- Nightly: [Lauf 42357252381](https://github.com/pquandel2-alt/homeintent/actions/runs/42357252381)
- Release-Gate: [Lauf 42360490968](https://github.com/pquandel2-alt/homeintent/actions/runs/42360490968)
- [Tag und GitHub-Release v7.13.3](https://github.com/pquandel2-alt/homeintent/releases/tag/v7.13.3)
