"""Change a monitoring instead of creating it anew (7.9.3 B7).

"Ändere die Garagen-Meldung auf 15 Minuten.", "Schick die Fenster-Warnung
auch an Anna.", "Die Batterie-Meldung erst unter 15 Prozent.", "Die
Haustür-Meldung nur noch nachts.", "Nimm Anna aus der Fenster-Warnung raus."

Constructions over closed word classes (the monitoring noun comes from
``monitoring_management``):

* duration - a number with a time unit ("auf 15 Minuten");
* threshold - a comparison word and a number ("erst unter 15 Prozent");
* add a recipient - "auch an <Name>", "<Name> dazu/hinzu";
* remove a recipient - "<Name> aus der <Meldung> (raus)", "nicht mehr an <Name>";
* time window - "nur (noch) nachts/tagsüber/abends/morgens", "nur zwischen
  H und H Uhr", "nur von H bis H Uhr".

``edit_config`` changes exactly that part of the stored Home Assistant
configuration (``for``, ``above``/``below``, the notify targets, one
``time`` condition); everything else stays.  ``validate_edit`` checks that
nothing else changed and that every value is in range.  The preview says
"Vorher: … Nachher: …"; only "Ja" writes (``AutomationExecutor``).

Home-Assistant-free and deterministic.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .entities import format_spoken_number, normalize_for_compare

__all__ = (
    "MonitorEdit",
    "describe_part",
    "edit_config",
    "parse_monitor_edit",
    "validate_edit",
)

_UNITS = {
    "sekunde": 1, "sekunden": 1, "minute": 60, "minuten": 60, "min": 60, "stunde": 3600, "stunden": 3600,
    "tag": 86400, "tage": 86400, "tagen": 86400,
}
_BELOW = frozenset({"unter", "unterhalb", "weniger", "kleiner", "niedriger", "tiefer"})
_ABOVE = frozenset({"ueber", "oberhalb", "mehr", "groesser", "hoeher"})
_WINDOWS = {
    "nachts": ((22, 0), (6, 0), "nachts (22 bis 6 Uhr)"),
    "tagsueber": ((6, 0), (22, 0), "tagsüber (6 bis 22 Uhr)"),
    "abends": ((18, 0), (23, 0), "abends (18 bis 23 Uhr)"),
    "morgens": ((5, 0), (10, 0), "morgens (5 bis 10 Uhr)"),
    "nachmittags": ((12, 0), (18, 0), "nachmittags (12 bis 18 Uhr)"),
    "vormittags": ((8, 0), (12, 0), "vormittags (8 bis 12 Uhr)"),
}
_SEND_VERBS = frozenset({"schick", "schicke", "sende", "send", "melde", "gib", "informiere", "benachrichtige",
                         "warne"})
_REMOVE_VERBS = frozenset({"nimm", "nehme", "entferne", "entfern", "streiche", "streich", "loesche", "loesch"})
_FILLERS = frozenset({
    "die", "der", "das", "den", "dem", "bitte", "mal", "auch", "noch", "an", "aus", "raus", "heraus", "dazu",
    "hinzu", "nicht", "mehr", "fuer", "von", "vom", "zur", "zum",
})


@dataclass(frozen=True)
class MonitorEdit:
    kind: str  # "duration" | "threshold" | "add_recipient" | "remove_recipient" | "window"
    seconds: int | None = None
    comparator: str | None = None  # "below" | "above" | None (keep)
    value: float | None = None
    unit: str = ""
    person: str = ""  # as said ("Anna")
    start: tuple[int, int] | None = None
    end: tuple[int, int] | None = None
    window_label: str = ""


def _tokens(text: str) -> tuple[list[str], list[str]]:
    raw = re.sub(r"[?.!,;:„“\"]", " ", text).split()
    return raw, [normalize_for_compare(word).replace("-", "") for word in raw]


def _is_monitor_noun(key: str) -> bool:
    from .monitoring_management import _monitoring_noun  # noqa: PLC2701 - the one noun rule

    return _monitoring_noun(key)[0]


def _number(word: str) -> float | None:
    try:
        return float(word.replace(",", "."))
    except ValueError:
        return None


def _person(raw: list[str], keys: list[str], start: int, stop: int) -> str:
    words = [
        raw[index] for index in range(start, stop)
        if keys[index] not in _FILLERS and not _is_monitor_noun(keys[index])
        and keys[index] not in _SEND_VERBS | _REMOVE_VERBS
    ]
    return " ".join(words)


def _clock(word: str) -> tuple[int, int] | None:
    hour, _, minute = word.strip(".").partition(":")
    if not hour.isdigit() or (minute and not minute.isdigit()):
        return None
    clock = (int(hour), int(minute or 0))
    return clock if 0 <= clock[0] <= 23 and 0 <= clock[1] <= 59 else None


def _clock_window(words: list[str]) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """"zwischen 22 und 6 (Uhr)", "von 8:30 bis 18 Uhr" - word by word."""
    for index, word in enumerate(words):
        if word not in {"zwischen", "von"} or index + 1 >= len(words):
            continue
        start = _clock(words[index + 1])
        rest = [item for item in words[index + 2:index + 5] if item != "uhr"]
        if start is None or len(rest) < 2 or rest[0] not in {"und", "bis"}:
            continue
        end = _clock(rest[1])
        if end is not None and end != start:
            return start, end
    return None


def parse_monitor_edit(text: str) -> MonitorEdit | None:
    """A change of an existing monitoring, else ``None``."""
    raw, keys = _tokens(text)
    nouns = [index for index, key in enumerate(keys) if _is_monitor_noun(key)]
    if not nouns:
        return None
    noun = nouns[0]
    present = set(keys)
    if present & {"welche", "was", "wie", "wann", "zeig", "bis"} and not present & {"zwischen", "von"}:
        return None
    # Remove a recipient: "Nimm Anna aus der Fenster-Warnung raus",
    # "Entferne Anna aus der Fenster-Warnung", "… nicht mehr an Anna".
    if keys[0] in _REMOVE_VERBS and "aus" in keys and keys.index("aus") < noun:
        person = _person(raw, keys, 1, keys.index("aus"))
        if person:
            return MonitorEdit("remove_recipient", person=person)
    if "nicht" in present and "mehr" in present and "an" in present:
        position = len(keys) - 1 - keys[::-1].index("an")
        person = _person(raw, keys, position + 1, len(keys))
        if person:
            return MonitorEdit("remove_recipient", person=person)
    # Add a recipient: "Schick die Fenster-Warnung auch an Anna".
    if "auch" in present and (keys[0] in _SEND_VERBS or "an" in present):
        position = keys.index("an") if "an" in keys else keys.index("auch")
        person = _person(raw, keys, position + 1, len(keys))
        if person:
            return MonitorEdit("add_recipient", person=person)
    if present & {"dazu", "hinzu"} and keys[0] in {"fuege", "fueg", "nimm", "nehme", "setz", "setze"}:
        person = _person(raw, keys, 1, len(keys))
        if person:
            return MonitorEdit("add_recipient", person=person)
    # Time window: "nur noch nachts", "nur zwischen 22 und 6 Uhr".
    if "nur" in present:
        for word, (start, end, label) in _WINDOWS.items():
            if word in present:
                return MonitorEdit("window", start=start, end=end, window_label=label)
        window = _clock_window(normalize_for_compare(text).replace(",", " ").split())
        if window is not None:
            start, end = window
            return MonitorEdit("window", start=start, end=end,
                               window_label=f"zwischen {start[0]}:{start[1]:02d} und {end[0]}:{end[1]:02d} Uhr")
    numbers = [(index, value) for index, key in enumerate(keys) if (value := _number(key)) is not None]
    if len(numbers) != 1:
        return None
    index, value = numbers[0]
    unit = keys[index + 1] if index + 1 < len(keys) else ""
    before = set(keys[max(0, index - 3):index])
    if before & (_BELOW | _ABOVE):
        comparator = "below" if before & _BELOW else "above"
        return MonitorEdit("threshold", comparator=comparator, value=value, unit=raw[index + 1] if unit else "")
    if unit in _UNITS:
        seconds = int(value * _UNITS[unit])
        if seconds <= 0:
            return None
        return MonitorEdit("duration", seconds=seconds)
    if unit in {"prozent", "%", "grad", "w", "watt", "kwh", "ppm", "lux", "lx"} or "auf" in before:
        return MonitorEdit("threshold", value=value, unit=raw[index + 1] if unit else "")
    return None


# --- the configuration ------------------------------------------------------------

def _notify_steps(steps: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        if step.get("action") == "notify.send_message":
            found.append(step)
        for value in step.values():
            if isinstance(value, list):
                found.extend(_notify_steps(value))
            elif isinstance(value, dict):
                found.extend(_notify_steps([value]))
    return found


def _targets(step: Mapping[str, Any]) -> list[str]:
    target = step.get("target")
    ids = target.get("entity_id") if isinstance(target, Mapping) else None
    return [ids] if isinstance(ids, str) else [str(item) for item in ids or []]


def _seconds_of(value: Any) -> int | None:
    if isinstance(value, Mapping):
        return int(value.get("days", 0)) * 86400 + int(value.get("hours", 0)) * 3600 + int(
            value.get("minutes", 0)) * 60 + int(value.get("seconds", 0))
    if isinstance(value, (int, float)):
        return int(value)
    return None


def spoken_seconds(seconds: int) -> str:
    for size, singular, plural in ((86400, "Tag", "Tagen"), (3600, "Stunde", "Stunden"), (60, "Minute", "Minuten")):
        if seconds >= size and seconds % size == 0:
            count = seconds // size
            return f"{count} {singular if count == 1 else plural}"
    return f"{seconds} Sekunden"


def describe_part(config: Mapping[str, Any], edit: MonitorEdit, names: Mapping[str, str]) -> str:
    """The part ``edit`` touches, in words ("nach 10 Minuten", "unter 25 %")."""
    if edit.kind == "duration":
        durations = [_seconds_of(t.get("for")) for t in config.get("triggers", []) if isinstance(t, Mapping)]
        found = [value for value in durations if value]
        return f"nach {spoken_seconds(found[0])}" if found else "sofort"
    if edit.kind == "threshold":
        for trigger in config.get("triggers", []):
            if isinstance(trigger, Mapping) and trigger.get("trigger") == "numeric_state":
                for key, word in (("below", "unter"), ("above", "über")):
                    if key in trigger:
                        unit = edit.unit or ""
                        return f"{word} {format_spoken_number(trigger[key])} {unit}".strip()
        return "ohne Grenzwert"
    if edit.kind in {"add_recipient", "remove_recipient"}:
        ids = sorted({target for step in _notify_steps(config.get("actions")) for target in _targets(step)})
        labels = [f"„{names.get(target, target)}“" for target in ids]
        return "an " + (labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " und " + labels[-1]) \
            if labels else "an niemanden"
    windows = [c for c in config.get("conditions", []) if isinstance(c, Mapping) and c.get("condition") == "time"]
    if not windows:
        return "jederzeit"
    after = str(windows[0].get("after", "00:00:00"))[:5]
    before = str(windows[0].get("before", "00:00:00"))[:5]
    return f"nur zwischen {after} und {before} Uhr"


def edit_config(
    config: Mapping[str, Any], edit: MonitorEdit, *, recipient_ids: Sequence[str] = ()
) -> tuple[dict[str, Any] | None, str | None]:
    """The changed configuration, or the honest reason why not."""
    new = copy.deepcopy(dict(config))
    triggers = [t for t in new.get("triggers", []) if isinstance(t, dict)]
    if edit.kind == "duration":
        assert edit.seconds is not None
        changeable = [t for t in triggers if t.get("trigger") in {"state", "numeric_state"}]
        if not changeable:
            return None, "Diese Überwachung hat keine Dauer, die ich ändern könnte"
        old_seconds = next((value for t in changeable if (value := _seconds_of(t.get("for")))), None)
        for trigger in changeable:
            trigger["for"] = {"seconds": edit.seconds}
        if old_seconds:
            for step in _notify_steps(new.get("actions")):
                data = step.get("data")
                message = data.get("message") if isinstance(data, dict) else None
                if isinstance(message, str) and "{{" not in message:
                    # "… seit 10 Minuten offen." names the new duration.
                    data["message"] = message.replace(spoken_seconds(old_seconds), spoken_seconds(edit.seconds), 1)
        return new, None
    if edit.kind == "threshold":
        assert edit.value is not None
        numeric = [t for t in triggers if t.get("trigger") == "numeric_state"]
        if not numeric:
            return None, "Diese Überwachung hat keinen Grenzwert"
        for trigger in numeric:
            current = "below" if "below" in trigger else "above"
            wanted = edit.comparator or current
            trigger.pop("below", None)
            trigger.pop("above", None)
            trigger[wanted] = float(edit.value)
        old = describe_part(config, edit, {})
        for step in _notify_steps(new.get("actions")):
            data = step.get("data")
            message = data.get("message") if isinstance(data, dict) else None
            if isinstance(message, str) and "{{" not in message:
                # A fixed message naming the old limit names the new one.
                number = old.split(" ")[1] if " " in old else ""
                if number:
                    data["message"] = message.replace(number, format_spoken_number(edit.value), 1)
        return new, None
    if edit.kind in {"add_recipient", "remove_recipient"}:
        steps = _notify_steps(new.get("actions"))
        if not steps:
            return None, "Diese Überwachung schickt keine Nachricht"
        for step in steps:
            current = _targets(step)
            if edit.kind == "add_recipient":
                merged = current + [target for target in recipient_ids if target not in current]
            else:
                merged = [target for target in current if target not in recipient_ids]
                if not merged:
                    return None, "Dann bekäme niemand mehr die Meldung; lösch sie lieber"
                if merged == current:
                    return None, f"{edit.person} bekommt diese Meldung gar nicht"
            step["target"] = {"entity_id": merged}
        return new, None
    assert edit.start is not None and edit.end is not None
    conditions = [c for c in new.get("conditions", []) if not (isinstance(c, Mapping) and c.get("condition") == "time"
                                                                and "weekday" not in c)]
    conditions.append({
        "condition": "time",
        "after": f"{edit.start[0]:02d}:{edit.start[1]:02d}:00",
        "before": f"{edit.end[0]:02d}:{edit.end[1]:02d}:00",
    })
    new["conditions"] = conditions
    return new, None


def _without_messages(steps: Any) -> Any:
    """The actions without the text of their messages."""
    if isinstance(steps, list):
        return [_without_messages(step) for step in steps]
    if isinstance(steps, dict):
        return {key: ("<message>" if key == "message" else _without_messages(value)) for key, value in steps.items()}
    return steps


def validate_edit(
    before: Mapping[str, Any], after: Mapping[str, Any], edit: MonitorEdit, known: Sequence[str]
) -> str | None:
    """Only the edited part changed, every value in range, every target known."""
    allowed = {
        "duration": {"triggers"}, "threshold": {"triggers"}, "add_recipient": {"actions"},
        "remove_recipient": {"actions"}, "window": {"conditions"},
    }[edit.kind]
    for key in set(before) | set(after):
        if key in allowed or key == "description":
            continue
        if key == "actions" and edit.kind in {"duration", "threshold"}:
            # Only the wording of a fixed message may follow the new value.
            if _without_messages(before.get(key)) != _without_messages(after.get(key)):
                return "unerwartete Änderung an actions"
            continue
        if before.get(key) != after.get(key):
            return f"unerwartete Änderung an {key}"
    if edit.kind == "duration" and not (60 <= (edit.seconds or 0) <= 7 * 86400):
        return "Dauer zwischen 1 Minute und 7 Tagen"
    if edit.kind == "threshold" and (edit.value is None or not -1000 <= edit.value <= 1_000_000):
        return "Grenzwert außerhalb des Bereichs"
    targets = {target for step in _notify_steps(after.get("actions")) for target in _targets(step)}
    if any(not target.startswith("notify.") or target not in known for target in targets):
        return "unbekanntes Push-Ziel"
    return None
