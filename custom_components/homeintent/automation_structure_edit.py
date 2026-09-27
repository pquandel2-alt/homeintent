"""Language model for safe trigger/condition edits of HomeIntent automations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto


class AutomationEditSection(Enum):
    TRIGGERS = auto()
    CONDITIONS = auto()


class AutomationEditOperation(Enum):
    REPLACE = auto()
    ADD = auto()
    CLEAR = auto()
    REMOVE = auto()


@dataclass(frozen=True)
class AutomationStructureEditRequest:
    section: AutomationEditSection
    operation: AutomationEditOperation
    payload: str | None = None
    index: int | None = None


_SECTION_RE = re.compile(
    r"\b(?P<trigger>auslöser|ausloeser|trigger)|(?P<condition>bedingung(?:en)?)\b",
    re.IGNORECASE,
)
_EDIT_RE = re.compile(
    r"\b(?:änder(?:e|n|t)?|aender(?:e|n|t)?|ersetz(?:e|en|t)?|"
    r"füg(?:e|en|t)?|fueg(?:e|en|t)?|hinzufüg(?:e|en|t)?|hinzufueg(?:e|en|t)?|"
    r"entfern(?:e|en|t)?|lösch(?:e|en|t)?|loesch(?:e|en|t)?)\b",
    re.IGNORECASE,
)


def parse_automation_structure_edit(text: str) -> AutomationStructureEditRequest | None:
    section_match = _SECTION_RE.search(text)
    if section_match is None or _EDIT_RE.search(text) is None:
        return None
    section = (
        AutomationEditSection.TRIGGERS
        if section_match.group("trigger")
        else AutomationEditSection.CONDITIONS
    )
    lowered = text.casefold()
    if re.search(r"\b(?:entfern|lösch|loesch)", lowered):
        if section is not AutomationEditSection.CONDITIONS:
            return None
        if re.search(r"\balle\w*\b", lowered):
            return AutomationStructureEditRequest(section, AutomationEditOperation.CLEAR)
        ordinal = re.search(
            r"\b(?:die\s+)?(erste|zweite|dritte|vierte|fünfte|fuenfte|1\.?|2\.?|3\.?|4\.?|5\.?)\b",
            lowered,
        )
        if ordinal is None:
            semantic_kind = (
                "time" if re.search(r"\bzeit(?:bedingung)?\b", lowered)
                else "sun" if re.search(r"\bsonnen?(?:bedingung)?\b", lowered)
                else "state" if re.search(r"\bzustands?(?:bedingung)?\b", lowered)
                else None
            )
            return (
                AutomationStructureEditRequest(
                    section, AutomationEditOperation.REMOVE, payload=semantic_kind
                )
                if semantic_kind is not None else None
            )
        indexes = {"erste": 0, "zweite": 1, "dritte": 2, "vierte": 3,
                   "fünfte": 4, "fuenfte": 4, "1": 0, "1.": 0,
                   "2": 1, "2.": 1, "3": 2, "3.": 2, "4": 3,
                   "4.": 3, "5": 4, "5.": 4}
        return AutomationStructureEditRequest(
            section, AutomationEditOperation.REMOVE,
            index=indexes[ordinal.group(1).casefold()],
        )
    operation = (
        AutomationEditOperation.ADD
        if re.search(r"\b(?:füg|fueg|hinzufüg|hinzufueg)", lowered)
        else AutomationEditOperation.REPLACE
    )
    payload: str | None = None
    if operation is AutomationEditOperation.REPLACE:
        split = list(re.finditer(r"\s+(?:auf|durch|zu)\s+", text, re.IGNORECASE))
        if split:
            payload = text[split[-1].end():].strip(" .,!?") or None
    else:
        tail = text[section_match.end():]
        tail = re.sub(r"\bhinzu(?:fügen|fuegen)?\b\s*$", "", tail, flags=re.IGNORECASE)
        marker = re.search(r"\b(?:dass|wenn|sobald|falls)\b", tail, re.I)
        if marker:
            payload = tail[marker.start():].strip(" .,!?") or None
    return AutomationStructureEditRequest(section, operation, payload)
