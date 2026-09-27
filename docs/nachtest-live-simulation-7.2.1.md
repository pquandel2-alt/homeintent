# Nachtest: HomeIntent 7.2.1 im simulierten Einfamilienhaus

Stand: 27. September 2026 · getestet: Tag `v7.2.1` (Commit `0080230`) ·
Bezug: [Testbericht 7.1.2](testbericht-live-simulation-7.1.2.md)

Unabhängiger Nachtest in der Test-Session: frisches Home Assistant 2026.9.2
(Python 3.14, hassil 3.12.0), neues Testhaus, alle Szenarien, danach
zusätzlich **neue Formulierungen, die in keinem Szenario stehen**.

## Ergebnis

| Prüfung | 7.1.2 | 7.2.1 |
| --- | --- | --- |
| Live-Szenarien (echtes HA) | 87 / 126 | **126 / 126** |
| Unit-Tests mit hassil 3.11 | 4244 grün | 4588 grün |
| Unit-Tests mit hassil 3.12 (HA 2026.9) | 46 rot | **4588 grün** |
| `tests_ha` (echtes HA) | 8 grün | 16 grün |
| HomeIntent-Fehler, Thread- oder Blocking-Warnungen im HA-Log | mehrere | **keine** |
| Latenz p50 / p95 / Maximum | 27 / 76 ms / **25 s** | 24 / 68 ms / **2 s** |

Drei Szenario-Erwartungen hat die Fix-Session angepasst, jeweils nachvollziehbar
begründet: Ventilator per `fan.turn_on` mit Prozentwert statt `set_percentage`
(gleiches Ergebnis), Verriegeln fragt wie Entriegeln nach (Schlösser sind in der
Richtlinie hohes Risiko), Waschmaschinen-Hinweis wird nach dem
Gruppierungsfenster als Sammelmeldung zugestellt. Der frühere Workaround des
Runners für F6 wurde entfernt, der Test ist damit strenger geworden.

## Gegenprobe mit neuen Formulierungen

Funktioniert (Auswahl):

- „In 20 Sekunden mach die Stehlampe an.“, „Mach das Kinderzimmerlicht in 45 Sekunden aus.“,
  „Schick mir in 2 Stunden eine Nachricht, dass ich die Wäsche aufhängen soll.“,
  „Erinnere mich in einer Stunde an den Ofen.“ (F1)
- „Schalte im Erdgeschoss alle Lichter aus, außer Kücheninsel, Stehlampe und Schreibtischlampe.“
  → genau diese drei bleiben an; ein unbekannter Ausschluss („Blumenlampe“) bricht ohne Aktion ab (F5)
- „Wie warm war es gestern durchschnittlich im Büro?“ → 19,8 Grad; „niedrigste
  Außentemperatur heute“; „Wie viel Energie wurde gestern verbraucht?“ → 13,2 kWh (F3)
- „Mach das Kinderzimmerlicht kaltweiß.“ → `color_temp_kelvin: 6500` (F4)
- „Setz das Heizprogramm auf Urlaub.“, „Stelle beim Saugroboter die Saugstufe auf leise.“,
  „Stell die Heizung im Kinderzimmer etwas wärmer.“ (F11)
- „Welche Geräte sind im Schlafzimmer an?“, „Welche Fenster gibt es im Obergeschoss?“ (F12)
- „Hake Käse auf der Einkaufsliste ab.“ (F27), „Abbrechen.“ in einer offenen Rückfrage (F10)

Verbleibende kleine Lücken (keine Regression, P2):

| Satz | Ergebnis |
| --- | --- |
| „Stell die Heizung im Kinderzimmer etwas kühler.“ | fragt nach der Temperatur („etwas wärmer“ funktioniert) |
| „Mach es im Kinderzimmer etwas kühler.“ | kein Gerät gefunden |
| „Saugroboter bitte auf leise.“ | Wert nicht verstanden (mit „Saugstufe“ ok) |
| „Schalte beim Fernseher auf YouTube um.“ | „Fernseher“ wird nicht dem „Wohnzimmer TV“ zugeordnet |
| „Stelle die Schreibtischlampe auf neutralweiß.“ | nicht unterstützt (warm-/kaltweiß ok) |
| „Wie viele Timer laufen?“ | nicht zugeordnet („Welche Timer laufen?“ ok) |
| Ausschluss mit unbekanntem Namen | sicher abgelehnt, aber Meldung nennt das falsche Gerät („Nachtlicht gefunden, Funktion nicht unterstützt“) statt „Blumenlampe nicht gefunden“ |

## Fazit

Alle 28 Befunde aus dem Testbericht sind im echten Home Assistant behoben.
Kein vorher grünes Szenario ist rot geworden, die Unit-Suite ist unter beiden
hassil-Versionen grün und das HA-Log ist sauber. Die verbleibenden Punkte sind
kleine Sprachlücken ohne Sicherheitsrelevanz.

Rohdaten: [`sim/results/nachtest-7.2.1.json`](../sim/results/nachtest-7.2.1.json)
