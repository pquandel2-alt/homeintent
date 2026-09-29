"""Confirmed, local bindings: the one store for everything HomeIntent learns.

A binding maps a meaning building block to a concrete target:

* ``ROUTINE`` – routine concept ("sleep") -> script/scene entity,
* ``ALIAS`` – a household word -> entity,
* ``DEFAULT_CHOICE`` – genus × place -> the entity chosen twice,
* ``PREFERENCE`` – activity ("read") -> a remembered setting,
* ``MACRO`` – a spoken macro name -> a confirmed plan description.

Rules that hold for every kind:

* a binding only exists after an explicit "Ja" (``confirmed`` is always
  ``True``; there is no confidence-based learning path);
* a binding never authorises anything: every use still runs through
  grounding, validator, EffectGraph and the execution policy;
* a binding whose target no longer exists or is no longer exposed is inert
  and reported as such (``binding_state``).

Persistence follows the other HomeIntent stores: one JSON file with a schema
version, atomic writes off the event loop, migration on load. Since schema 2
the file also keeps the answers given to clarifications (``choices``), so
that the same answer given twice can be offered as a default choice (7.4.1).
Choices are observations, never bindings: they authorise nothing.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import cast

SCHEMA_VERSION = 2


class BindingKind(StrEnum):
    ROUTINE = "routine"
    ALIAS = "alias"
    DEFAULT_CHOICE = "default_choice"
    PREFERENCE = "preference"
    MACRO = "macro"


class BindingScope(StrEnum):
    USER = "user"
    HOUSEHOLD = "household"


class BindingState(StrEnum):
    VALID = "valid"
    TARGET_MISSING = "target_missing"
    NOT_EXPOSED = "not_exposed"


KIND_LABELS_DE = {
    BindingKind.ROUTINE: "Routine",
    BindingKind.ALIAS: "Name",
    BindingKind.DEFAULT_CHOICE: "Standardauswahl",
    BindingKind.PREFERENCE: "Vorliebe",
    BindingKind.MACRO: "Sprachmakro",
}


@dataclass(frozen=True)
class Binding:
    binding_id: str
    kind: BindingKind
    key: str
    target: str
    scope: BindingScope = BindingScope.HOUSEHOLD
    user_id: str | None = None
    created_by: str | None = None
    created_at: str = ""
    confirmed: bool = True
    uses: int = 0
    last_used: str | None = None
    data: Mapping[str, object] = field(default_factory=dict)

    def targets(self) -> tuple[str, ...]:
        """Entity ids this binding points to (macros may point to several)."""
        raw = self.data.get("entity_ids")
        if isinstance(raw, (list, tuple)):
            return tuple(str(item) for item in cast(Sequence[object], raw))
        return (self.target,) if "." in self.target else ()

    def to_dict(self) -> dict[str, object]:
        return {
            "binding_id": self.binding_id,
            "kind": self.kind.value,
            "key": self.key,
            "target": self.target,
            "scope": self.scope.value,
            "user_id": self.user_id,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "confirmed": True,
            "uses": self.uses,
            "last_used": self.last_used,
            "data": dict(self.data),
        }


def normalize_key(text: str) -> str:
    folded = text.strip().casefold()
    for source, target in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        folded = folded.replace(source, target)
    return " ".join("".join(char if char.isalnum() else " " for char in folded).split())


def binding_state(binding: Binding, existing_ids: Iterable[str], exposed_ids: Iterable[str]) -> BindingState:
    existing = set(existing_ids)
    exposed = set(exposed_ids)
    targets = binding.targets()
    if any(target not in existing for target in targets):
        return BindingState.TARGET_MISSING
    if any(target not in exposed for target in targets):
        return BindingState.NOT_EXPOSED
    return BindingState.VALID


def _parse(item: Mapping[str, object]) -> Binding | None:
    try:
        kind = BindingKind(str(item.get("kind")))
        scope = BindingScope(str(item.get("scope", BindingScope.HOUSEHOLD.value)))
    except ValueError:
        return None
    key = item.get("key")
    target = item.get("target")
    if not isinstance(key, str) or not key or not isinstance(target, str) or not target:
        return None
    if item.get("confirmed") is not True:
        return None  # never load anything that was not confirmed
    data = item.get("data")
    user_id = item.get("user_id")
    uses = item.get("uses", 0)
    return Binding(
        binding_id=str(item.get("binding_id") or uuid.uuid4().hex),
        kind=kind,
        key=normalize_key(key),
        target=target,
        scope=scope,
        user_id=user_id if isinstance(user_id, str) else None,
        created_by=str(item["created_by"]) if isinstance(item.get("created_by"), str) else None,
        created_at=str(item.get("created_at") or ""),
        confirmed=True,
        uses=uses if isinstance(uses, int) and uses >= 0 else 0,
        last_used=str(item["last_used"]) if isinstance(item.get("last_used"), str) else None,
        data=dict(cast(Mapping[str, object], data)) if isinstance(data, Mapping) else {},
    )


def migrate(raw: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Schema migrations; version 0 was a bare list of routine bindings."""
    version = raw.get("version", 0)
    items = raw.get("bindings", [])
    if not isinstance(items, list):
        return []
    result: list[Mapping[str, object]] = []
    for item in cast(list[object], items):
        if not isinstance(item, Mapping):
            continue
        mapping = dict(cast(Mapping[str, object], item))
        if version == 0:
            mapping.setdefault("kind", BindingKind.ROUTINE.value)
            mapping.setdefault("scope", BindingScope.HOUSEHOLD.value)
            mapping.setdefault("confirmed", True)
        result.append(mapping)
    return result


class BindingStore:
    """Small JSON-backed store; the only place learned bindings live."""

    def __init__(self, path: Path | str | None) -> None:
        self._path = Path(path) if path is not None else None
        self._bindings: list[Binding] = []
        # "<user>|<choice key>" -> {"counts": {entity_id: n}, "offered": bool}
        self._choices: dict[str, dict[str, object]] = {}
        self._lock = asyncio.Lock()

    # -- persistence -------------------------------------------------------
    async def async_load(self) -> None:
        if self._path is None:
            return
        raw = await asyncio.to_thread(self._read)
        self._bindings = [binding for item in migrate(raw) if (binding := _parse(item)) is not None]
        choices = raw.get("choices")
        if isinstance(choices, Mapping):
            self._choices = {
                str(key): dict(cast(Mapping[str, object], value))
                for key, value in cast(Mapping[object, object], choices).items()
                if isinstance(value, Mapping)
            }

    def _read(self) -> Mapping[str, object]:
        assert self._path is not None
        try:
            content = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return {}
        if isinstance(content, list):
            return {"version": 0, "bindings": content}
        return cast(Mapping[str, object], content) if isinstance(content, Mapping) else {}

    def _write(self) -> None:
        if self._path is None:
            return
        payload = {
            "version": SCHEMA_VERSION,
            "bindings": [item.to_dict() for item in self._bindings],
            "choices": self._choices,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".homeintent_bindings")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=1)
            os.replace(temp, self._path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise

    async def _async_save(self) -> None:
        await asyncio.to_thread(self._write)

    # -- queries -----------------------------------------------------------
    def all(self, kind: BindingKind | None = None) -> tuple[Binding, ...]:
        return tuple(item for item in self._bindings if kind is None or item.kind is kind)

    def visible_to(self, user_id: str | None, *, is_admin: bool = False) -> tuple[Binding, ...]:
        return tuple(
            item for item in self._bindings
            if item.scope is BindingScope.HOUSEHOLD or is_admin or item.user_id == user_id
        )

    def find(self, kind: BindingKind, key: str, user_id: str | None) -> Binding | None:
        """The user's own binding first, then the household's."""
        normalized = normalize_key(key)
        personal = next(
            (
                item for item in self._bindings
                if item.kind is kind and item.key == normalized
                and item.scope is BindingScope.USER and user_id is not None and item.user_id == user_id
            ),
            None,
        )
        if personal is not None:
            return personal
        return next(
            (
                item for item in self._bindings
                if item.kind is kind and item.key == normalized and item.scope is BindingScope.HOUSEHOLD
            ),
            None,
        )

    def get(self, binding_id: str) -> Binding | None:
        return next((item for item in self._bindings if item.binding_id == binding_id), None)

    # -- mutations (always after an explicit "Ja") --------------------------
    async def async_bind(
        self,
        kind: BindingKind,
        key: str,
        target: str,
        *,
        confirmed: bool,
        scope: BindingScope = BindingScope.HOUSEHOLD,
        user_id: str | None = None,
        created_by: str | None = None,
        now: datetime,
        data: Mapping[str, object] | None = None,
    ) -> Binding:
        if confirmed is not True:
            raise ValueError("Bindings are only stored after an explicit confirmation")
        if scope is BindingScope.USER and not user_id:
            raise ValueError("A personal binding needs a user")
        normalized = normalize_key(key)
        if not normalized:
            raise ValueError("Empty binding key")
        binding = Binding(
            binding_id=uuid.uuid4().hex,
            kind=kind,
            key=normalized,
            target=target,
            scope=scope,
            user_id=user_id if scope is BindingScope.USER else None,
            created_by=created_by,
            created_at=now.isoformat(),
            data=dict(data or {}),
        )
        async with self._lock:
            self._bindings = [
                item for item in self._bindings
                if not (
                    item.kind is kind and item.key == normalized and item.scope is binding.scope
                    and item.user_id == binding.user_id
                )
            ]
            self._bindings.append(binding)
            await self._async_save()
        return binding

    async def async_remove(self, binding_id: str) -> Binding | None:
        async with self._lock:
            removed = self.get(binding_id)
            if removed is None:
                return None
            self._bindings = [item for item in self._bindings if item.binding_id != binding_id]
            await self._async_save()
        return removed

    async def async_remove_key(self, kind: BindingKind, key: str, user_id: str | None) -> Binding | None:
        found = self.find(kind, key, user_id)
        if found is None:
            return None
        return await self.async_remove(found.binding_id)

    async def async_record_use(self, binding_id: str, now: datetime) -> None:
        async with self._lock:
            self._bindings = [
                replace(item, uses=item.uses + 1, last_used=now.isoformat())
                if item.binding_id == binding_id else item
                for item in self._bindings
            ]
            await self._async_save()

    async def async_clear(self) -> None:
        async with self._lock:
            self._bindings = []
            self._choices = {}
            await self._async_save()

    # -- clarification answers (observations, 7.4.1) -----------------------
    async def async_observe_choice(self, user_id: str, key: str, entity_id: str) -> bool:
        """Count one answer; ``True`` once the same answer was given twice
        and no default choice was offered for this key yet."""
        slot = f"{user_id}|{key}"
        async with self._lock:
            entry = self._choices.setdefault(slot, {"counts": {}, "offered": False})
            counts = cast(dict[str, int], entry.setdefault("counts", {}))
            counts[entity_id] = int(counts.get(entity_id, 0)) + 1
            offer = counts[entity_id] >= 2 and not entry.get("offered")
            if offer:
                entry["offered"] = True
            await self._async_save()
        return bool(offer)

    def choice_counts(self, user_id: str, key: str) -> Mapping[str, int]:
        entry = self._choices.get(f"{user_id}|{key}", {})
        counts = entry.get("counts", {})
        return dict(cast(Mapping[str, int], counts)) if isinstance(counts, Mapping) else {}
