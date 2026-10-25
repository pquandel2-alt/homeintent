# NLU-Fehleranalyse 7.12.0

Ausgangslauf und Release-Candidate-Lauf wurden vollständig mit
`scripts/dev_benchmark.py` ausgeführt. Die 36 verbleibenden Fehler sind
unverändert; neue Mehrturnfälle erhöhen den Gesamtstand von 467/503 auf
471/507, ohne einen bestehenden Fall oder eine Erwartung zu ändern.

| Tatsächliche Ursache | Anzahl | Fälle |
|---|---:|---|
| Frontend/Antwortdarstellung | 4 | Strom Waschmaschine, Rauchmelder-Batterie, STT „wie warm draußen“, Schlafzimmer oben |
| Morphologie/STT-Normalisierung | 2 | „Stehlampe ann“, „Markiese“ |
| Trigger-/Clause-Binding | 5 | Rauchmelder→alle Lichter, Zeit+Bewegung, Präsenz endet, Alarmanlage, draußen hell |
| Temporal-/Recurrence-Dialog | 4 | dunkel, Sonnenaufgang, Sonnenuntergang, relative zehn Minuten |
| Dialog/Ellipse/Pronomen | 5 | Büro-/Küchenlicht-Auswahl, Heizungsellipse, „wieder aus“, „draußen?“ |
| Entity Grounding/Ambiguität | 6 | Temperatur Wohnzimmer, Markise, Lautsprecher, Kinderzimmer-„dort“, Deckenlicht, Badlüfter+Licht |
| Notification-Lexikon | 2 | „Sag Anna Bescheid“, „Teil Philipp mit“ |
| Confirmation-/Safety-Antwortvertrag | 7 | Haustür höflich, zwei kritische Mehrfachbefehle, Garagentor ohne Rückfrage, „Bestätigt“, alle Schlösser, sudo |
| Capability-Antwort | 1 | alle Schlösser |

Die Fälle werden nicht als Satzlisten in Produktionscode kodiert. In 7.12.0
wurden nur zwei allgemeine Regeln ergänzt: Der bereits vorhandene typisierte
Eventgrounder darf einen unvollständigen Triggerdraft eröffnen, und der
Benachrichtigungsakt „sag mir/uns (sofort) Bescheid“ bleibt bei koordinierten
Aktionen als semantisches Prädikat erhalten. Positive, negative,
metamorphische und No-Write-Tests decken beide Regeln ab.

Nicht behobene Fälle bleiben sichtbar, weil eine allgemeine, sichere Regel in
diesem Release nicht ausreichend belegt war. Insbesondere wurden kritische
Mehrfachaktionen und unbekannte Präfixe nicht gelockert, nur um das erwartete
Benchmarklabel zu treffen.

## Fehlerklassen vorher/nachher

| Klasse | 7.11.0 | 7.12.0 RC |
|---|---:|---:|
| UNDERSTANDING | 20 | 20 |
| GROUNDING | 1 | 1 |
| AMBIGUITY | 5 | 5 |
| CAPABILITY | 1 | 1 |
| DIALOG | 5 | 5 |
| POLICY | 0 | 0 |
| RESPONSE | 4 | 4 |
| TEST_EXPECTATION | 0 | 0 |
| SAFETY | 0 | 0 |

Safety-Zähler: `unsafe_execution_count`, Wrong Target, Wrong Operation,
Unexpected Write und Execution without Confirmation jeweils 0.
