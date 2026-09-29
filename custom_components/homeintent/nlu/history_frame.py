"""Language island "Verlauf" (7.5.1): history questions from words, not sentences.

The canonical frames stay the three query types of ``history_query``
(``HistoryQuery`` - a statistic of a measurement, ``StateHistoryQuery`` - how
often/how long/when/whether a state held, ``ComparativeHistoryQuery`` - two
periods). They are derived here from the words of the utterance and small
lexicon tables (metric stems, state-question cues, period phrases) instead
of German sentence patterns. Target resolution stays with the one target
resolution (explicit names) and the measured-sensor rule of
``history_query``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Sequence

from ..entities import normalize_for_compare

# -- lexicon ---------------------------------------------------------------
# Stems match a word start ("durchschnittlich", "Durchschnittstemperatur").
MEAN_STEMS = ("durchschnitt", "mittelwert")
MIN_STEMS = ("minimum", "minimal", "niedrigst", "kleinst", "tiefst")
MAX_STEMS = ("maximum", "maximal", "hoechst", "groesst")
CHANGE_STEMS = ("verbraucht", "verbrauch", "erzeugt", "produziert", "veraendert", "veraenderung")
# "tiefst" is a minimum word, but alone it does not make a history question.
HISTORY_STEMS = MEAN_STEMS + ("minimum", "minimal", "niedrigst", "kleinst") + MAX_STEMS + CHANGE_STEMS
COMPARISON_WORDS = frozenset({"vergleich", "verglichen", "hoeher", "niedriger", "mehr", "weniger"})
OCCURRED_OPENERS = frozenset({"war", "waren", "hat", "haben"})

THIS = frozenset({"diese", "dieser", "diesen", "aktuelle", "aktuellen", "aktueller"})
LAST = frozenset({"letzte", "letzter", "letzten", "letztes", "vergangene", "vergangenen", "vergangener", "vergangenes"})


def words_of(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", normalize_for_compare(text)).split()


def _starts(words: Sequence[str], stems: Sequence[str]) -> bool:
    return any(word.startswith(stem) for word in words for stem in stems)


def _bigram(words: Sequence[str], first: frozenset[str] | set[str], second: str) -> bool:
    return any(a in first and b == second for a, b in zip(words, words[1:]))


def period_key(words: Sequence[str]) -> str | None:
    """"gestern", "diese Woche", "in den letzten 24 Stunden" … as a key."""
    present = set(words)
    if "vorgestern" in present:
        return "vorgestern"
    if "gestern" in present:
        return "gestern"
    if present & {"heute", "heutigen"}:
        return "heute"
    if _bigram(words, THIS - {"diesen"}, "woche"):
        return "diese woche"
    if _bigram(words, {"letzte", "letzter", "vergangene", "vergangenen"}, "woche"):
        return "letzte woche"
    if _bigram(words, {"dieser", "diesen", "aktuelle", "aktuellen"}, "monat"):
        return "dieser monat"
    if any(a in LAST and b == "24" and c == "stunden" for a, b, c in zip(words, words[1:], words[2:])):
        return "24 stunden"
    if "zuletzt" in present or any(
        a in LAST and b == "sieben" and c == "tage" for a, b, c in zip(words, words[1:], words[2:])
    ):
        return "sieben tage"
    return None


def period_range(key: str | None, now: datetime) -> tuple[datetime, datetime, str, str] | None:
    """Start, end, statistics period and spoken label of a period key."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if key == "vorgestern":
        end = midnight - timedelta(days=1)
        return end - timedelta(days=1), end, "hour", "vorgestern"
    if key == "gestern":
        return midnight - timedelta(days=1), midnight, "hour", "gestern"
    if key == "heute":
        return midnight, now, "hour", "heute"
    week = midnight - timedelta(days=now.weekday())
    if key == "diese woche":
        return week, now, "day", "diese Woche"
    if key == "letzte woche":
        return week - timedelta(days=7), week, "day", "letzte Woche"
    if key == "dieser monat":
        return midnight.replace(day=1), now, "day", "diesen Monat"
    if key == "24 stunden":
        return now - timedelta(hours=24), now, "hour", "in den letzten 24 Stunden"
    if key == "sieben tage":
        return now - timedelta(days=7), now, "hour", "in den letzten sieben Tagen"
    return None


def statistic_of(words: Sequence[str]) -> str | None:
    """MEAN / MIN / MAX / CHANGE, or ``None`` when no history cue is spoken."""
    if not (_starts(words, HISTORY_STEMS) or _bigram(words, {"im"}, "schnitt")):
        return None
    if _starts(words, MEAN_STEMS) or _bigram(words, {"im"}, "schnitt"):
        return "MEAN"
    if _starts(words, MIN_STEMS):
        return "MIN"
    if _starts(words, MAX_STEMS):
        return "MAX"
    if _starts(words, CHANGE_STEMS):
        return "CHANGE"
    return None


def state_question_of(words: Sequence[str]) -> str | None:
    """COUNT ("wie oft"), DURATION ("wie lange"), LAST ("wann … zuletzt"),
    OCCURRED ("War … offen?")."""
    present = set(words)
    if _bigram(words, {"wie"}, "oft") or "anzahl" in present:
        return "COUNT"
    if _bigram(words, {"wie"}, "lange") or "dauer" in present:
        return "DURATION"
    if "wann" in present and ("zuletzt" in present or _bigram(words, {"wann"}, "wurde")):
        return "LAST"
    if words and words[0] in OCCURRED_OPENERS:
        return "OCCURRED"
    return None


def comparison_periods(words: Sequence[str]) -> tuple[str, str] | None:
    if not set(words) & COMPARISON_WORDS:
        return None
    present = set(words)
    if "gestern" in present and "vorgestern" in present:
        return "gestern", "vorgestern"
    if "heute" in present and "gestern" in present:
        return "heute", "gestern"
    if "woche" in present and present & {"diese", "dieser", "diesen", "aktuelle", "aktuellen", "aktueller"}:
        return "diese woche", "letzte woche"
    return None


__all__ = (
    "comparison_periods", "period_key", "period_range", "state_question_of",
    "statistic_of", "words_of",
)
