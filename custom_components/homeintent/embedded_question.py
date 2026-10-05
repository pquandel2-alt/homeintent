"""An embedded yes/no question after a checking verb (7.9.1 A7).

"Prüfe, ob das Garagentor offen ist." asks *now*: it is the question "Ist
das Garagentor offen?" and is answered at once - never an automation. The
reading is structural: a checking verb (closed class) with only lead-in
words before "ob", then a subordinate clause whose final finite verb moves
to the front. Home-Assistant-free.
"""

from __future__ import annotations

from .entities import normalize_for_compare

__all__ = ("embedded_check_question",)

_CHECK_VERBS = frozenset({
    "pruefe", "pruef", "pruefen", "ueberpruefe", "ueberpruef", "ueberpruefen", "kontrolliere",
    "kontrollier", "kontrollieren", "check", "checke", "checken", "schau", "schaue", "schauen",
    "sieh", "siehe", "guck", "gucke", "gucken", "teste", "testen",
})
_LEAD_INS = frozenset({
    "bitte", "mal", "doch", "kurz", "einmal", "nochmal", "nach", "kannst", "koenntest", "du",
    "magst", "wuerdest", "eben", "schnell", "gerade",
})


def embedded_check_question(text: str) -> str | None:
    """"Prüfe, ob X offen ist" -> "Ist X offen?"; otherwise ``None``."""
    words = text.replace(",", " ").strip().rstrip(".!?").split()
    keys = [normalize_for_compare(word) for word in words]
    if "ob" not in keys:
        return None
    split = keys.index("ob")
    lead = keys[:split]
    if not lead or not any(key in _CHECK_VERBS for key in lead):
        return None
    if any(key not in _CHECK_VERBS and key not in _LEAD_INS for key in lead):
        return None
    clause = words[split + 1:]
    if len(clause) < 2:
        return None
    verb = clause[-1]
    rest = clause[:-1]
    return f"{verb[:1].upper()}{verb[1:]} {' '.join(rest)}?"
