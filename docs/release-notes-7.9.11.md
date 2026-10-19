# HomeIntent 7.9.11

7.9.11 ist ein eng begrenzter Sicherheits-, Datenintegritäts- und
Event-Loop-Patch. Er enthält keine neue NLU- oder Medienfunktion.

## Sichere Automationsdienste

- `homeintent.delete_automation` und der Legacy-Alias
  `ha_nlu.delete_automation` löschen nur Automationen mit einem konsistenten
  Sidecar-Eintrag `created_by: homeintent`. Prüfung und YAML-Änderung liegen
  unter derselben gemeinsamen Sperre. Fehlende, fremde, widersprüchliche oder
  beschädigte Metadaten beenden den Vorgang ohne Schreibzugriff, Reload,
  Registry-, History- oder Metadatenänderung.
- Der öffentliche Resume-Dienst aktiviert keine beliebige Automation mehr.
  Er verlangt Ziel-ID, Helper-ID und eine kryptographisch eindeutige Pause-ID,
  die exakt zu einem dauerhaften offenen HomeIntent-Pausenauftrag passen.
  Erfolgreich verwendete Aufträge werden als verbraucht markiert und können
  nicht wiederverwendet werden.
- Normale Aktivierung über die bestätigte HomeIntent-Automationsverwaltung
  bleibt getrennt und unterliegt weiterhin Eigentümer- oder Adminprüfung.

## Datenintegrität bei „Rückgängig“

- History-Einträge speichern die exakten Digests des von HomeIntent
  hinterlassenen YAML- und Metadatenzustands sowie den betroffenen Besitzer.
- Vor jedem Rollback müssen beide aktuellen Digests übereinstimmen. Externe
  Änderungen werden deshalb weder überschrieben noch wiederhergestellt; bei
  einem Konflikt bleibt auch der History-Eintrag erhalten.
- Nur der gespeicherte Besitzer oder ein Home-Assistant-Administrator darf
  zurückrollen. Eine Ja/Nein-Bestätigung ersetzt diese Autorisierung nicht.

## Event-Loop-Sicherheit

- Thermal-Dokumente werden in einem kontrollierten, zusammengefassten
  Hintergrund-Worker atomar geschrieben. Es läuft höchstens ein Schreib-Task;
  die jüngste Generation gewinnt und beim Unload wird der letzte Stand
  abgewartet.
- GoalRuns ohne passenden aktiven Thermal-Zyklus sowie deaktiviertes Lernen
  erzeugen keine Thermal-Persistenz.

## Speicherformat und Migration

- Automation-Sidecars können nun `pause_id`, `pause_target_automation_id`,
  `pause_resume_at`, `pause_status` und `pause_consumed_at` enthalten.
  Bestehende Sidecars bleiben lesbar. Alte Resume-Helper ohne vollständigen
  kryptographischen Nachweis werden absichtlich nicht automatisch ausgeführt;
  HomeIntent rät keine Zuordnung.
- Neue History-Einträge enthalten `post_write_digest`,
  `post_metadata_digest` und `owner_user_id`. Alte Einträge ohne diese sicheren
  Felder bleiben erhalten, werden aber fail-closed abgelehnt.

## Kompatibilität und Grenzen

- Sicherheits-, Bestätigungs-, Ambiguitäts- und `NEVER_AUTO`-Regeln werden
  nicht abgeschwächt.
- Die Korpusbaseline ist gegenüber 7.9.10 bytegleich, weil dieser Patch die
  Sprachverarbeitung nicht verändert.
- Veröffentlichung erfolgt nur, wenn die verpflichtenden CI- und
  Release-Gates für den exakten Release-Commit erfolgreich sind.
