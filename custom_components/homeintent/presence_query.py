"""Where is someone? (7.9.3 B2) – read-only, from ``person.*``.

"Wo ist Anna?", "Ist jemand zuhause?", "Wer ist zuhause?", "Ist Philipp
schon zuhause?", "Seit wann ist Anna weg?", "Wann ist Anna heimgekommen?",
"Wann kommt Philipp heim?".

Sources: the ``person`` state (``home``, ``not_home`` or a zone name), its
``last_changed`` and the recorder for earlier arrivals.  Never coordinates:
the attributes ``latitude``/``longitude``/``gps_accuracy`` are not read.

Rights: the zone of another person is said only to administrators, to the
person themselves, or when the option "Aufenthaltsort im Haushalt teilen"
(``share_household_location``) is on; otherwise only "zuhause"/"unterwegs".

"Wann kommt … heim?" has no data source: HomeIntent says so and offers at
most the usual arrival time from the history, clearly marked as a habit
value (median of at least ``HABIT_MIN_ARRIVALS`` arrivals in
``HABIT_DAYS`` days, the same kind of day: weekday or weekend).

Home-Assistant-free and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Mapping, Sequence

from .entities import normalize_for_compare

__all__ = (
    "HABIT_DAYS",
    "HABIT_MIN_ARRIVALS",
    "PersonState",
    "PresenceQuery",
    "answer_presence",
    "arrivals",
    "parse_presence_query",
    "usual_arrival",
)

HABIT_DAYS = 28
HABIT_MIN_ARRIVALS = 3
_HOME_WORDS = frozenset({"zuhause", "daheim", "heim", "hause", "da"})
_AWAY_WORDS = frozenset({"weg", "unterwegs", "fort", "aus", "abwesend"})
_SOMEBODY = frozenset({"jemand", "wer", "irgendwer", "irgendjemand", "niemand", "keiner", "alle"})
_ARRIVE = frozenset({"heimgekommen", "angekommen", "zurueckgekommen", "heimkam", "ankam", "gekommen"})
_WILL_ARRIVE = frozenset({"heim", "nachhause", "zurueck"})
_WEEKDAYS_DE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


@dataclass(frozen=True)
class PersonState:
    entity_id: str
    name: str
    state: str  # "home" | "not_home" | zone name | "unknown"
    since: datetime | None = None


@dataclass(frozen=True)
class PresenceQuery:
    kind: str  # "where" | "who_home" | "is_home" | "since" | "arrived" | "will_arrive"
    person_words: tuple[str, ...] = ()  # the named person's words; () = everybody


def _words(text: str) -> list[str]:
    folded = normalize_for_compare(text)
    return "".join(char if char.isalnum() else " " for char in folded).split()


def _named(words: Sequence[str], names: Sequence[str]) -> tuple[str, ...]:
    keys = {normalize_for_compare(name).split()[0] for name in names if name}
    return tuple(word for word in words if word in keys)


def parse_presence_query(text: str, person_names: Sequence[str]) -> PresenceQuery | None:
    """A question about where people are, else ``None``."""
    words = _words(text)
    present = set(words)
    if not words or present & {"wenn", "sobald", "falls"} or not (
        text.rstrip().endswith("?") or words[0] in {"wo", "wer", "ist", "sind", "seit", "wann", "sag"}
    ):
        return None
    named = _named(words, person_names)
    glued = present | {a + b for a, b in zip(words, words[1:])}
    if words[0] == "wann" and named:
        if present & {"kommt", "kommen"} and glued & (_WILL_ARRIVE | {"nachhause"}):
            return PresenceQuery("will_arrive", named)
        if present & _ARRIVE or (present & {"ist", "war"} and glued & {"heimgekommen", "nachhausegekommen"}):
            return PresenceQuery("arrived", named)
        if present & {"gegangen", "losgefahren", "weggefahren", "los"}:
            return PresenceQuery("since", named)
        return None
    if words[:2] == ["seit", "wann"] and named and present & (_AWAY_WORDS | _HOME_WORDS):
        return PresenceQuery("since", named)
    if words[0] == "wo" and named and present & {"ist", "sind", "steckt", "befindet"}:
        return PresenceQuery("where", named)
    home = bool(present & _HOME_WORDS) or "zuhause" in glued or "zuhaus" in glued
    if home and present & _SOMEBODY and not named:
        return PresenceQuery("who_home")
    if (home or present & _AWAY_WORDS) and named and words[0] in {"ist", "sind", "sag"}:
        return PresenceQuery("is_home", named)
    return None


def _spoken_state(state: str) -> str:
    if state == "home":
        return "zuhause"
    if state in {"not_home", "away"}:
        return "unterwegs"
    return state


def _at(moment: datetime, now: datetime) -> str:
    """"um 15:02 Uhr", "gestern um 15:02 Uhr", "am 05.10. um 15:02 Uhr"."""
    if moment.date() == now.date():
        return f"um {moment:%H:%M} Uhr"
    if (now.date() - moment.date()).days == 1:
        return f"gestern um {moment:%H:%M} Uhr"
    return f"am {moment:%d.%m.} um {moment:%H:%M} Uhr"


def _since(moment: datetime | None, now: datetime) -> str:
    """"seit 15:02 Uhr", "seit gestern, 15:02 Uhr", "seit dem 05.10., 15:02 Uhr"."""
    if moment is None:
        return ""
    if moment.date() == now.date():
        return f"seit {moment:%H:%M} Uhr"
    if (now.date() - moment.date()).days == 1:
        return f"seit gestern, {moment:%H:%M} Uhr"
    return f"seit dem {moment:%d.%m.}, {moment:%H:%M} Uhr"


def _zone_allowed(person: PersonState, speaker_person: str | None, is_admin: bool, share: bool) -> bool:
    return is_admin or share or person.entity_id == speaker_person


def _where(person: PersonState, speaker_person: str | None, is_admin: bool, share: bool) -> str:
    state = person.state
    if state in {"unknown", "unavailable", ""}:
        return f"Wo {person.name} ist, weiß ich nicht (kein Standort)"
    if state in {"home", "not_home", "away"} or not _zone_allowed(person, speaker_person, is_admin, share):
        return f"{person.name} ist {_spoken_state(state if state == 'home' else 'not_home')}"
    return f"{person.name} ist in der Zone „{state}“"


def arrivals(rows: Sequence[tuple[datetime, str]]) -> list[datetime]:
    """Every moment a person came home (a change into ``home``)."""
    found: list[datetime] = []
    previous: str | None = None
    for moment, state in sorted(rows, key=lambda item: item[0]):
        if state in {"unknown", "unavailable"}:
            continue
        if state == "home" and previous is not None and previous != "home":
            found.append(moment)
        previous = state
    return found


def usual_arrival(times: Sequence[datetime], now: datetime) -> tuple[int, int, int] | None:
    """(hour, minute, count) of the median arrival on the same kind of day
    (weekday or weekend) in the window, or ``None`` with too few."""
    weekend = now.weekday() >= 5
    minutes = [
        moment.hour * 60 + moment.minute for moment in times
        if (moment.weekday() >= 5) == weekend and (now - moment).days <= HABIT_DAYS
    ]
    if len(minutes) < HABIT_MIN_ARRIVALS:
        return None
    middle = int(median(minutes))
    return middle // 60, middle % 60, len(minutes)


def answer_presence(
    query: PresenceQuery,
    people: Sequence[PersonState],
    now: datetime,
    *,
    speaker_person: str | None,
    is_admin: bool,
    share: bool,
    arrival_history: Mapping[str, Sequence[datetime]] | None = None,
) -> str:
    """The spoken answer.  ``arrival_history`` is ``None`` without recorder."""
    if query.kind == "who_home":
        home = [person.name for person in people if person.state == "home"]
        unknown = [person.name for person in people if person.state in {"unknown", "unavailable", ""}]
        if home:
            names = home[0] if len(home) == 1 else ", ".join(home[:-1]) + " und " + home[-1]
            text = f"Zuhause {'ist' if len(home) == 1 else 'sind'} {names}."
        else:
            text = "Nein, niemand ist zuhause." if not unknown else "Von den Personen mit Standort ist niemand zuhause."
        if unknown:
            text += f" Von {' und '.join(unknown)} habe ich keinen Standort."
        return text
    wanted = set(query.person_words)
    chosen = [person for person in people if normalize_for_compare(person.name).split()[0] in wanted]
    if not chosen:
        return "Diese Person kenne ich nicht."
    person = chosen[0]
    if person.state in {"unknown", "unavailable", ""}:
        return f"Von {person.name} habe ich keinen Standort; ich kann dazu nichts sagen."
    since = _since(person.since, now)
    tail = f", {since}." if since else "."
    if query.kind == "where":
        return _where(person, speaker_person, is_admin, share) + tail
    if query.kind == "is_home":
        if person.state == "home":
            return f"Ja, {person.name} ist zuhause{tail}"
        return f"Nein, {person.name} ist unterwegs{tail}"
    if query.kind == "since":
        if person.state == "home":
            return f"{person.name} ist nicht weg, sondern zuhause{tail}"
        return f"{person.name} ist {since} unterwegs." if since else f"{person.name} ist unterwegs."
    if query.kind == "arrived":
        if person.state == "home":
            if person.since is None:
                return f"{person.name} ist zuhause; wann {person.name} heimgekommen ist, weiß ich nicht."
            return f"{person.name} ist {_at(person.since, now)} heimgekommen."
        if arrival_history is None:
            return f"{person.name} ist gerade unterwegs. Wann zuletzt heimgekommen, weiß ich ohne Verlauf nicht."
        times = sorted(arrival_history.get(person.entity_id, ()))
        if not times:
            return f"{person.name} ist gerade unterwegs; im Verlauf finde ich keine Heimkehr."
        return f"{person.name} ist gerade unterwegs; zuletzt heimgekommen ist {person.name} {_at(times[-1], now)}."
    # will_arrive: no data source - honest, at most the habit value.
    if person.state == "home":
        return f"{person.name} ist schon zuhause."
    text = f"Wann {person.name} heimkommt, kann ich nicht wissen – dafür habe ich keine Daten."
    usual = usual_arrival(arrival_history.get(person.entity_id, ()), now) if arrival_history is not None else None
    if usual is not None:
        kind = "am Wochenende" if now.weekday() >= 5 else "werktags"
        text += (
            f" Als Gewohnheit aus dem Verlauf: {kind} kam {person.name} meist gegen {usual[0]}:{usual[1]:02d} Uhr "
            f"heim ({usual[2]} Heimkehren in {HABIT_DAYS} Tagen) – das ist keine Vorhersage."
        )
    return text
