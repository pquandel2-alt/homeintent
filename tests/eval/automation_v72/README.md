# HomeIntent 7.2.0 natural-language automation corpora

Handwritten German utterances with handwritten expected meanings.

* `heldout_*.txt` - **held-out** evaluation corpus.  Written and committed
  *before* any 7.2.0 parser work started; it is never used to tune parser
  patterns.  Its metrics are reported, not optimised.
* `dev_*.txt` - development corpus, used freely during implementation.

## Line format

```
<expectation> :: <turn 1> [>> <turn 2> ...]
```

Lines starting with `#` are comments, `## name` starts a category.  With
several turns the expectation describes the answer to the **last** turn; the
earlier turns run in the same conversation.

## Expectations

| expectation | meaning |
|---|---|
| `auto: TRIGGERS [; if: CONDS] => ACTIONS` | an automation preview with exactly this meaning; nothing executed before "Ja" |
| `clarify` | a clarification question; no preview, no service call, no write |
| `unsupported` | understood as an automation wish that cannot be built; no preview, no call |
| `reject` | not an automation request at all (question, statement, complaint, how-to, ...); no preview, no call, no write |
| `immediate` | an immediate notification: exactly one push, no automation |

`TRIGGERS` are separated by ` | `, `CONDS` by ` & `, `ACTIONS` by ` + `.
Entity sets are written sorted, comma separated.

Triggers:

* `state(IDS)=open|closed|on|off` - state transition of any listed entity
* `num(IDS).PROP OP VALUE` - `PROP` is `state`, `cover_position`,
  `brightness` (percent), `fan_percentage`; `OP` is `==`, `>`, `<`, `>=`, `<=`
* suffix `/for=SECONDS` - state must hold that long;
  suffix `/dir=up|down` - cover travel direction
* `time=HH:MM`, `sun=sunset|sunrise[+-MIN]`, `arrive(IDS)`, `leave(IDS)`
* `at(tomorrow HH:MM)`, `in(SECONDS)` - one-shot schedules

Conditions: `state(IDS)=...`, `num(...)`, `nobody_home`, `anybody_home`,
`time<HH:MM`, `time>HH:MM`, `weekday=mon,...`.

Actions: `notify(me|us|julia|philipp[,"explicit text"])`,
`turn_on(IDS)`, `turn_off(IDS)`, `open(IDS)`, `close(IDS)`,
`position(IDS)=N`, `brightness(IDS)=N`, `delay=SECONDS`.

Two equivalences are applied to both sides before comparing (they are
properties of Home Assistant, not of the parser): a `turn_on`/`turn_off` of a
cover is `open`/`close` (HomeIntent generates `cover.open_cover` /
`cover.close_cover`), and one device action on several entities equals the
same action once per entity.  Explicit message texts are compared ignoring
surrounding quotes, case and trailing punctuation.

## Evaluation world

Current conversation user `philipp` (push target *iPhone von Philipp*);
`julia` has *Handy von Julia*; both form the confirmed household.

| entity | name | area |
|---|---|---|
| cover.buero_rollladen | Büro Rollladen | Büro |
| cover.wohnzimmer_rollladen | Wohnzimmer Rollladen | Wohnzimmer |
| cover.kueche_rollladen | Küche Rollladen | Küche |
| cover.schlafzimmer_rollladen | Schlafzimmer Rollladen | Schlafzimmer |
| cover.bad_rollladen | Bad Rollladen | Bad |
| binary_sensor.wohnzimmer_fenster | Wohnzimmer Fenster (window) | Wohnzimmer |
| binary_sensor.buero_fenster | Bürofenster (window) | Büro |
| binary_sensor.kuechenfenster | Küchenfenster (window) | Küche |
| binary_sensor.schlafzimmer_fenster | Schlafzimmer Fenster (window) | Schlafzimmer |
| binary_sensor.haustuer | Haustür (door) | Flur |
| binary_sensor.terrassentuer | Terrassentür (door) | Wohnzimmer |
| binary_sensor.flur_bewegung | Bewegungsmelder Flur (motion) | Flur |
| sensor.wohnzimmer_temperatur | Wohnzimmer Temperatur (°C) | Wohnzimmer |
| sensor.aussentemperatur | Außentemperatur (°C) | - |
| sensor.buero_luftfeuchtigkeit | Büro Luftfeuchtigkeit (%) | Büro |
| sensor.handy_akku | Handy Akku (%, battery) | - |
| light.wohnzimmer | Wohnzimmerlicht | Wohnzimmer |
| light.kueche | Küchenlicht | Küche |
| light.buero | Bürolampe | Büro |
| light.flur | Flurlicht | Flur |
| fan.schlafzimmer | Schlafzimmer Ventilator | Schlafzimmer |
| media_player.wohnzimmer | Wohnzimmer Lautsprecher | Wohnzimmer |
| switch.kaffeemaschine | Kaffeemaschine | Küche |
| climate.wohnzimmer | Wohnzimmer Heizung | Wohnzimmer |
| person.philipp / person.julia | Philipp / Julia | - |

The ambiguity world additionally replaces `cover.buero_rollladen` by
`cover.buero_links` (*Büro Rollladen links*) and `cover.buero_rechts`
(*Büro Rollladen rechts*); lines using it are in `*_ambiguous.txt`.
