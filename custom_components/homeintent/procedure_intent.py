"""Closed language meanings for named, revalidated household procedures."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .entities import normalize_for_compare
from .nlu.language_frontend import LanguageDocument


class ProcedureOperation(StrEnum):
    SAVE_ACTIVE_PLAN = "save_active_plan"
    LIST = "list"
    RUN = "run"
    FORGET = "forget"


@dataclass(frozen=True)
class ProcedureIntent:
    operation: ProcedureOperation
    name: str | None = None


# Language island "Ziele/Prozeduren" (7.5.2): closed frames as word
# sequences, not sentence patterns.
_KINDS = frozenset({"prozedur", "routine", "szenario"})
_WORD = re.compile(r"[\wäöüß]+")


def _words(text: str) -> list[str] | None:
    """Whitespace words that are all word characters (else no frame)."""
    parts = text.split()
    return parts if all(_WORD.fullmatch(part) for part in parts) else None


def _list_request(text: str) -> bool:
    parts = text.split()
    if parts and parts[-1] == "?":
        parts = parts[:-1]
    elif parts and parts[-1].endswith("?"):
        parts[-1] = parts[-1][:-1]
    if not parts or not all(_WORD.fullmatch(part) for part in parts):
        return False
    if parts[:1] == ["welche"]:
        rest = parts[1:]
    elif parts[:2] == ["was", "fuer"]:
        rest = parts[2:]
    else:
        return False
    if rest[:1] in (["prozeduren"], ["szenarien"]):
        rest = rest[1:]
    elif rest[:2] == ["benannte", "routinen"]:
        rest = rest[2:]
    else:
        return False
    return rest in (["kennst", "du"], ["hast", "du"])


def _named(parts: list[str], start: int, *, drop_final_aus: bool = False) -> str | None:
    name = parts[start:]
    if drop_final_aus and len(name) > 1 and name[-1] == "aus":
        name = name[:-1]
    return " ".join(name) if 1 <= len(name) <= 4 else None


def _procedure_frame(text: str) -> tuple[ProcedureOperation, str] | None:
    parts = _words(text)
    if not parts:
        return None
    if parts[0] == "speichere" or (len(parts) > 1 and parts[0].startswith("merk") and parts[1] == "dir"):
        index = 1 if parts[0] == "speichere" else 2
        if parts[index:index + 3][:1] in (["diesen"], ["den"]) and parts[index + 1:index + 3] == ["plan", "als"]:
            name = _named(parts, index + 3)
            return (ProcedureOperation.SAVE_ACTIVE_PLAN, name) if name else None
        return None
    if parts[0] in {"fuehre", "starte", "vergiss", "loesche"}:
        index = 2 if len(parts) > 1 and parts[1] == "die" else 1
        if index >= len(parts) or parts[index] not in _KINDS:
            return None
        run = parts[0] in {"fuehre", "starte"}
        name = _named(parts, index + 1, drop_final_aus=run)
        if name is None:
            return None
        return (ProcedureOperation.RUN if run else ProcedureOperation.FORGET), name
    return None


def interpret_procedure_intent(document: LanguageDocument) -> ProcedureIntent | None:
    text = normalize_for_compare(document.normalized_text)
    if _list_request(text):
        return ProcedureIntent(ProcedureOperation.LIST)
    frame = _procedure_frame(text)
    return ProcedureIntent(frame[0], frame[1].strip()) if frame is not None else None


__all__ = (
    "ProcedureIntent",
    "ProcedureOperation",
    "interpret_procedure_intent",
)
