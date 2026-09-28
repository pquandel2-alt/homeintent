"""Spoken German clock times -> numeric clock times.

"um halb sieben", "um viertel nach acht", "um viertel vor sieben",
"um dreiviertel acht", "um zehn nach sechs", "um fünf vor halb neun",
"um 18 Uhr 30", "um sieben Uhr dreißig" all name one clock time.  The
reading is compositional over word classes: a time preposition, an
optional minute offset (number word or digits) with a relation word
(nach/vor), an optional anchor (halb/viertel/dreiviertel) and an hour
(number word or digits).  The expression is rewritten to ``H:MM Uhr`` so
every downstream time grammar sees one canonical form ("18:30",
"18.30 Uhr" are canonicalised the same way).

Only expressions introduced by a time preposition are rewritten; a bare
"halb" ("fahr die Rollläden halb runter") is never touched.  Like everyday
German, "halb sieben" is 6:30; "abends"/"nachmittags" shift to the
afternoon when the hour is 1-11.
"""

from __future__ import annotations

__all__ = ("normalize_clock_expressions", "split_relative_delay")

_HOURS = {
    "eins": 1, "ein": 1, "eine": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "fuenf": 5,
    "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,
    "zwoelf": 12,
}
_MINUTES = {
    **{word: value for word, value in _HOURS.items() if value != 1},
    "eins": 1, "ein": 1, "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15, "fuenfzehn": 15,
    "zwanzig": 20, "fünfundzwanzig": 25, "fuenfundzwanzig": 25, "dreißig": 30,
    "dreissig": 30, "vierzig": 40, "fünfundvierzig": 45, "fuenfundvierzig": 45,
    "fünfzig": 50, "fuenfzig": 50,
}
_PREPOSITIONS = frozenset({"um", "ab", "bis", "gegen", "von", "zwischen", "und", "auf", "für", "fuer"})
_ANCHORS = {"halb": -30, "viertel": 15, "dreiviertel": -15}
_RELATIONS = {"nach": 1, "vor": -1}
_AFTERNOON = frozenset({"abends", "nachmittags", "abend", "nachmittag"})


def _hour(word: str) -> int | None:
    if word.isdigit() and 0 <= int(word) <= 24:
        return int(word)
    return _HOURS.get(word)


def _minute(word: str) -> int | None:
    if word.isdigit() and 0 <= int(word) < 60:
        return int(word)
    return _MINUTES.get(word)


def _digit_clock(word: str) -> tuple[int, int] | None:
    """"18:30" / "18.30" as one token."""
    for separator in (":", "."):
        hour, found, minute = word.partition(separator)
        if found and hour.isdigit() and minute.isdigit() and len(minute) == 2:
            if int(hour) <= 24 and int(minute) < 60:
                return int(hour), int(minute)
    return None


def _read(words: list[str], start: int) -> tuple[int, int, int] | None:
    """Read one clock expression at ``start``: (hour, minute, token count)."""
    index = start
    offset = 0
    digits = _digit_clock(words[index]) if index < len(words) else None
    if digits is not None:
        index += 1
        spoken_uhr = index < len(words) and words[index] == "uhr"
        if ":" not in words[index - 1] and not spoken_uhr:
            return None  # "auf 21.50 Grad" is a decimal, not a clock
        return digits[0], digits[1], index + int(spoken_uhr) - start
    minute_value = _minute(words[index]) if index < len(words) else None
    if (
        minute_value is not None and index + 2 < len(words)
        and words[index + 1] in {"minuten", "minute"}
    ):
        index += 1
    if minute_value is not None and index + 1 < len(words) and words[index + 1] in _RELATIONS:
        offset = minute_value * _RELATIONS[words[index + 1]]
        index += 2
    anchor = words[index] if index < len(words) else ""
    if anchor == "drei" and index + 1 < len(words) and words[index + 1] == "viertel":
        anchor, index = "dreiviertel", index + 1
    if anchor in _ANCHORS:
        index += 1
        relation = 0
        if anchor == "viertel" and index < len(words) and words[index] in _RELATIONS:
            relation = _RELATIONS[words[index]]
            index += 1
        hour = _hour(words[index]) if index < len(words) else None
        if hour is None or hour > 12:
            return None
        index += 1
        if anchor == "viertel":
            # "viertel acht" (east German) = 7:15; "viertel nach/vor acht".
            base = hour * 60 + (15 * relation if relation else -45)
        else:
            base = hour * 60 + _ANCHORS[anchor]
        total = base + offset
        return (total // 60) % 24, total % 60, index - start
    if offset:
        hour = _hour(anchor)
        if hour is None:
            return None
        total = hour * 60 + offset
        return (total // 60) % 24, total % 60, index + 1 - start
    hour = _hour(anchor)
    if hour is None or index + 1 >= len(words) or words[index + 1] != "uhr":
        return None
    index += 2
    minute = 0
    if index < len(words):
        spoken = _minute(words[index])
        if spoken is not None:
            minute, index = spoken, index + 1
    if minute == 0:
        return None  # "um sieben Uhr" is already understood everywhere
    return hour, minute, index - start


def normalize_clock_expressions(text: str) -> str:
    """Rewrite spoken clock times after a time preposition to ``H:MM``."""
    if not text:
        return text
    raw = text.split(" ")
    words = [part.strip(".,!?;:").casefold() for part in raw]
    output: list[str] = []
    index = 0
    changed = False
    while index < len(raw):
        output.append(raw[index])
        if words[index] in _PREPOSITIONS and index + 1 < len(raw):
            reading = _read(words, index + 1)
            if reading is not None:
                hour, minute, count = reading
                end = index + 1 + count
                if hour < 12 and end < len(words) and words[end] in _AFTERNOON:
                    hour += 12
                tail = raw[end - 1][len(raw[end - 1].rstrip(".,!?;:")):]
                output.append(f"{hour}:{minute:02d} Uhr" + tail)
                index = end
                changed = True
                continue
        index += 1
    return " ".join(output) if changed else text


_UNITS = {
    "sekunde": 1, "sekunden": 1, "minute": 60, "minuten": 60, "stunde": 3600, "stunden": 3600,
}
_AMOUNTS = {**_MINUTES, "einer": 1, "halben": 0.5, "halbe": 0.5, "anderthalb": 1.5, "eineinhalb": 1.5}


def _amount(words: list[str], index: int) -> tuple[float, int] | None:
    """One "<amount> <unit>" at ``index``: (seconds, token count)."""
    if index + 1 >= len(words):
        return None
    word = words[index]
    value: float | None = float(word) if word.isdigit() else _AMOUNTS.get(word)
    count = 1
    if word in {"einer", "eine"} and words[index + 1] in {"halben", "halbe"}:
        value, count = 0.5, 2
    if value is None or index + count >= len(words):
        return None
    unit = _UNITS.get(words[index + count])
    if unit is None:
        return None
    return value * unit, count + 1


def split_relative_delay(text: str) -> tuple[str, int] | None:
    """"Schließe in 150 Minuten die Rollläden" -> ("Schließe die Rollläden", 9000).

    Reads "in <amount> <unit> [und <amount> <unit>]" anywhere in the
    sentence; the remaining words are the command.  ``None`` without such
    a span or when nothing but the delay was said.
    """
    raw = text.split(" ")
    words = [part.strip(".,!?;:").casefold() for part in raw]
    for index, word in enumerate(words):
        if word != "in":
            continue
        first = _amount(words, index + 1)
        if first is None:
            continue
        seconds, end = first[0], index + 1 + first[1]
        if end < len(words) and words[end] == "und":
            second = _amount(words, end + 1)
            if second is not None:
                seconds, end = seconds + second[0], end + 1 + second[1]
        rest = raw[:index] + raw[end:]
        tail = raw[end - 1][len(raw[end - 1].rstrip(".,!?;:")):]
        command = " ".join(part for part in rest if part).strip(" ,")
        if tail and not command.endswith(tail):
            command += tail
        if not command or int(seconds) <= 0:
            return None
        return command, int(seconds)
    return None


_WAKE_VERBS = frozenset({"weck", "wecke", "wecken", "weckst", "weckt"})
_WAKE_OBJECTS = frozenset({"mich", "uns"})
_INSTRUMENT_ARTICLES = {"dem": "das", "der": "die", "den": "die"}


def wake_request(text: str) -> tuple[str, str] | None:
    """"Weck mich um 6:30 Uhr mit Licht" -> ("um 6:30 Uhr", "das Licht").

    A wake request is the verb *wecken* with a first-person object, a clock
    time and an instrument ("mit <Gerät>").  The instrument becomes the
    switched target; without one there is nothing to operate, so ``None``.
    """
    normalized = normalize_clock_expressions(text)
    raw = normalized.split(" ")
    words = [part.strip(".,!?;:").casefold() for part in raw]
    if not _WAKE_VERBS & set(words) or not _WAKE_OBJECTS & set(words):
        return None
    clock: str | None = None
    instrument: list[str] = []
    for index, word in enumerate(words):
        if word == "um" and index + 1 < len(words) and clock is None:
            follower = words[index + 1]
            if _digit_clock(follower) is not None or follower.isdigit() or follower in _HOURS:
                clock = f"um {raw[index + 1].strip('.,!?;:')} Uhr"
        if word == "mit" and index + 1 < len(words) and not instrument:
            for part in raw[index + 1:]:
                bare = part.strip(".,!?;:")
                if bare.casefold() in _WAKE_VERBS or bare.casefold() in {"um", "und", "bitte"}:
                    break
                instrument.append(bare)
                if bare != part:
                    break
            if instrument and instrument[0].casefold() in _INSTRUMENT_ARTICLES:
                instrument[0] = _INSTRUMENT_ARTICLES[instrument[0].casefold()]
            elif instrument and instrument[0][:1].isupper():
                instrument.insert(0, "das" if instrument[0].casefold().endswith(("licht", "radio")) else "die")
    if clock is None or not instrument:
        return None
    return clock, " ".join(instrument)
