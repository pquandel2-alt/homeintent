# HomeIntent 7.13.2

HomeIntent 7.13.2 behebt die in der unabhängigen Nachprüfung von 7.13.1
gefundenen Sicherheits-, Semantik-, Performance- und Dokumentationsfehler.
Details und vollständige Reproduktionen stehen im
[Umsetzungsbericht](https://github.com/pquandel2-alt/homeintent/blob/v7.13.2/docs/umsetzung-7.13.2.md).

## Klauselspezifische Schreibgrenze

- `ClauseBinding` bindet pro koordiniertem Schreibprädikat Klauselspanne,
  Operation, Zielausdruck und exakte Ziel-IDs, Ortsausdruck und exakte
  Orts-IDs, Quelle/Wert, verbrauchte Token-Spans, Rest, Servicepläne und
  Ablehnungsgrund.
- Keine Operation, Quelle, kein Wert und kein Ziel darf aus einer anderen
  Klausel übernommen werden. Jeder explizite Ort begrenzt nur die Ziele
  seiner eigenen Klausel.
- Alle Klauselbindungen werden vor dem ersten Write gemeinsam validiert. Ein
  ungültiger Teil bedeutet: kein Plan, keine Vorschau, keine Bestätigung,
  kein Service Call und keine Zielmenge.
- Nominale Koordination wie „zwei Stunden und 30 Minuten“ bleibt im
  Zeit-/Automationsmodell und bezahlt nicht den Mehrbefehlsvalidator.

## Zusammengesetzte Medienquellen

Der gemeinsame Mehrbefehls-Compiler kann eine Quellenwahl noch nicht sicher
materialisieren. 7.13.2 verwendet deshalb die sichere Variante B:

- Bekannte `source_list`-Quellen (einschließlich Ziffern), Music-Assistant-
  Favoriten, unbekannte und mehrdeutige Quellen in einem zusammengesetzten
  Satz werden als vollständiger Turn abgelehnt.
- Die zweite Klausel darf weder übrig bleiben noch ihre Operation auf Player
  anderer Räume übertragen.
- Einzelne exakt aufgelöste Quellenbefehle bleiben unverändert unterstützt.

Insbesondere erzeugt „Spiel Quirlomat in der Küche und pausiere die Musik im
Schlafzimmer“ garantiert keinen Plan und keinen Write. Dasselbe gilt derzeit
bewusst auch für die zusammengesetzte Variante mit WDR 2, Bayern 3 oder One.

## Cover-Sicherheit

Die zentrale `CoverDeviceClass`-Enum der gepinnten Home-Assistant-Version
2026.9.2 wird automatisiert gegen HomeIntent geprüft.

| Klasse | Risiko bei Bewegung | Ausführung | Bestätigung |
| --- | --- | --- | --- |
| `shutter`, `blind`, `shade`, `curtain` | grundsätzlich `MEDIUM` | unabhängig pro Ziel, vollständige Vorprüfung | ab bestehender Breitenstufe |
| `garage`, Legacy `garage_door`, `gate` | `CRITICAL` | atomar | immer |
| `door`, `window` | mindestens `HIGH` | atomar | immer |
| `awning`, `damper`, sonstige/fehlende/unbekannte Klasse | mindestens `HIGH` | atomar | immer |
| gemischte Standard-/Sicherheitsmenge | mindestens höchstes Einzelrisiko | atomar | immer |

Die Regeln gelten für `open_cover`, `close_cover` und `set_cover_position`.
Nach „Ja“ werden aktuelle Freigabe, Verfügbarkeit, Effektmenge und die
vollständige Plansignatur erneut geprüft. Geänderte Aktion, Quelle/Wert oder
Zielmenge führt zu keinem Call.

## Performance

- `LanguageDocument` und Klauselgrenzen werden am Turn-Rand erzeugt und an
  die Vollständigkeits-/Bindungsprüfung weitergereicht.
- Einfache Einzelbefehle nehmen keinen klauselspezifischen Mehrbefehlsweg.
- Der begrenzte Registry-Namenscache speichert ausschließlich strukturelle
  Namen und Entity-IDs, nie Snapshot-Listen oder Benutzer-/Conversation-
  Zustand; Registry-Namensänderungen invalidieren den Schlüssel und die
  Zielauflösung bindet ihn turnlokal an die aktuellen Snapshots.
- Das bestehende absolute p95-Gate wird durch ein relatives Gate ergänzt:
  `multi_target` und `multi_clause_command` dürfen auf demselben Runner nicht
  mehr als Faktor 6,5 über dem langsameren Licht-Kontrollpfad liegen. Damit
  wird bereits eine siebenfache Verschlechterung blockiert.
- Der isolierte Sicherheitsbenchmark besitzt fünf Warmups und 30 Iterationen
  und misst `media_coverage`, `write_completeness` und `clause_binding`.

Das Profil vor der Korrektur wies `analyse_write_completeness`, `_Names` und
`utterance_fields` als Hauptkosten aus. Im Nachprofil benötigte die neue
Bindungsgrenze 0,586 s über 313 Aufrufe (≈1,9 ms pro Aufruf); die kumulierte
Vollständigkeitsprüfung sank von rund 2,7 s auf 0,054 s. Endgültige
CI-p95-Werte werden erst aus dem grünen Lauf des Release-SHA übernommen.

## Tests und unveränderte Sicherheitsgrenzen

Neue Conversation-, Matrix-, Property- und Metamorphietests decken Quellen,
Klauselumstellung, Höflichkeit, Orte, Satellitenkontext, unbekannte
Folgeklauseln, Confirmation Scope, sämtliche Cover-Klassen und Registry-
Cache-Lebenszyklen ab. Die bestehenden HACS-/Automation-/Proaktiv-/Undo-,
Batch-, Event-Loop-I/O-, Release-Gate-, Issue-#38-, Orts-, Script- und
Music-Assistant-Regressionen bleiben Teil der vollständigen CI und Nightly.

Die lokalen Abschlussläufe bestanden unter Python 3.12 und 3.13 jeweils mit
10.418 bestandenen und 12 übersprungenen Tests. Drei Nightly-Property-Seeds
(7132, 424242, 99991) bestanden jeweils 126 Tests ohne Gegenbeispiel. Die
echten HA-2026.9.2-Komponententests bestanden mit 23 Tests; der reale
Music-Assistant-Komponentenvertrag bestand gegen HA 2026.4.0 und 2026.9.2.
Diese automatisierten Verträge sind ausdrücklich kein Hardware- oder
Audiowiedergabetest.

Konkrete neue Regressionen:

- [Klausel- und Quellenbindung](https://github.com/pquandel2-alt/homeintent/blob/v7.13.2/tests/test_clause_binding_7132.py)
- [Cover-Sicherheitsmatrix](https://github.com/pquandel2-alt/homeintent/blob/v7.13.2/tests/test_cover_safety_7132.py)
- [Performance- und Cache-Grenzen](https://github.com/pquandel2-alt/homeintent/blob/v7.13.2/tests/test_performance_regression_7132.py)
- [Abgleich mit Home Assistants `CoverDeviceClass`](https://github.com/pquandel2-alt/homeintent/blob/v7.13.2/tests_ha/test_cover_safety_constants.py)

## Grenzen

- Zusammengesetzte Quellenwahl ist sicher abgelehnt, noch nicht funktional
  unterstützt.
- Echte Sonos-, Music-Assistant-, Licht- und Rollladenhardware wurde nicht
  geprüft. Automatisierte HA-Vertrags-, Komponenten- und Live-Testbettläufe
  ersetzen keinen Hardwaretest.
- Der HACS-PR bleibt bis zu einer tatsächlichen neuen Review-Entscheidung
  `CHANGES_REQUESTED`; diese Release Notes behaupten keine Freigabe durch HACS
  oder Frenck.
