# Zielbezogenes Automation-Undo ab 7.10.1

Neue Verlaufseinträge verwenden Schema 2. Ein Eintrag enthält den
Operationstyp, die Automations-ID, Eigentümer und Zeitpunkt sowie ausschließlich
für das betroffene Objekt den Zustand vor und nach der Änderung. Dazu gehören
die Position in `automations.yaml`, der zugehörige Sidecar-Eintrag und
kanonische SHA-256-Prüfsummen aller vier Zustände.

Beim Rückgängigmachen wird unter derselben Schreibsperre geprüft, ob die
betroffene Automation und ihr Sidecar-Eintrag noch exakt dem gespeicherten
Nachher-Zustand entsprechen. Nur dieses Ziel wird entfernt, eingefügt oder
ersetzt. Andere Automationen und deren Metadaten werden aus dem aktuellen
Zustand übernommen; zwischenzeitliche fremde Änderungen bleiben daher
erhalten. Mehrdeutige IDs, eine extern veränderte Zielautomation oder
veränderte Zielmetadaten führen ohne Schreibzugriff zum Abbruch.

Nach erfolgreicher YAML- und Metadatenänderung wird `automation.reload`
aufgerufen. Scheitert das Schreiben der Metadaten oder der Reload, stellt die
Transaktion den unmittelbar vorherigen YAML- und Sidecar-Zustand wieder her
und lädt diesen Zustand erneut. Erst nach vollständigem Erfolg wird der
Verlaufseintrag entfernt. Eigentümer oder Home-Assistant-Administrator sind
erforderlich; anonyme Aufrufer sind nicht berechtigt. Konkurrierende
Undo-Anfragen werden serialisiert, und nur die erste passende Anfrage kann den
Eintrag verbrauchen.

## Alte Snapshot-Einträge

Schema-1-Einträge werden nicht blind als vollständige Datei zurückgeschrieben.
Eine zielbezogene Darstellung wird nur im Speicher abgeleitet, wenn die alten
Prüfsummen für die vollständige YAML-Datei und die vollständigen Metadaten noch
exakt zum aktuellen Zustand passen. Fehlende, beschädigte oder veraltete
Prüfsummen lassen den alten Eintrag unverändert im Verlauf und verweigern Undo
fail-closed.

## Bewusste Grenze

Pause und Fortsetzen können eine Zielautomation und eine separate
Resume-Automation gleichzeitig verändern. Ein einzelnes Schema-2-Delta darf
nur ein Ziel anfassen. Solche zusammengesetzten Verlaufseinträge werden daher
derzeit ausdrücklich abgelehnt und nicht teilweise zurückgerollt. Eine spätere
Erweiterung benötigt ein typisiertes Mehrziel-Delta mit atomaren Prüfsummen für
jedes betroffene Objekt.
