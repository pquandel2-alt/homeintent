"""Confirmed, user-owned aliases for exact Music Assistant library items."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from homeassistant.core import Context, HomeAssistant
from homeassistant.util.file import write_utf8_file_atomic

from .entities import EntitySnapshot, normalize_for_compare
from .media import MediaRequest, resolve_media_player
from .music_assistant import (
    DOMAIN,
    MusicAssistantItem,
    MusicAssistantPlan,
    MusicAssistantResolution,
    async_search_exact_music_assistant,
    is_safe_music_assistant_media_id,
)

MEDIA_ALIAS_FILENAME = ".storage/homeintent_music_aliases.json"
MEDIA_ALIAS_SCHEMA_VERSION = 1
MEDIA_ALIAS_LOCK_KEY = "homeintent_music_alias_lock"


@dataclass(frozen=True)
class MediaAlias:
    """One exact, non-URL MA item owned by an authenticated HA user."""

    alias: str
    owner_user_id: str
    config_entry_id: str
    media_type: str
    canonical_name: str
    media_id: str
    created_at: str
    schema_version: int = MEDIA_ALIAS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.owner_user_id or not self.config_entry_id:
            raise ValueError("Ein Medienalias benötigt einen eindeutigen Besitzer und MA-Eintrag")
        if not (2 <= len(self.alias.strip()) <= 80):
            raise ValueError("Der Medienalias muss zwischen 2 und 80 Zeichen lang sein")
        if not is_safe_music_assistant_media_id(self.media_id):
            raise ValueError("Der Medienalias enthält keine sichere Music-Assistant-ID")


@dataclass(frozen=True)
class MediaAliasRequest:
    """Typed alias-management meaning before any lookup or write."""

    operation: str  # create | delete
    alias: str
    canonical_query: str | None = None
    media_type: str | None = None


@dataclass(frozen=True)
class PendingMediaAlias:
    """A fully grounded alias mutation awaiting yes/no confirmation."""

    operation: str
    alias: MediaAlias


_TYPE_PREFIXES = {
    "radio": frozenset({"radio", "radiosender", "sender", "radiostation"}),
    "playlist": frozenset({"playlist", "playliste", "wiedergabeliste"}),
    "album": frozenset({"album"}),
    "track": frozenset({"titel", "song", "track", "lied"}),
    "artist": frozenset({"kuenstler", "interpret"}),
}
_POSSESSIVES = frozenset({"mein", "meine", "meinen"})


def _surface_words(text: str) -> tuple[str, ...]:
    """Split an alias command without encoding a sentence-shaped pattern."""
    cleaned = text.strip().rstrip(".!?").strip()
    return tuple(word.strip(" \"'„“") for word in cleaned.split() if word.strip(" \"'„“"))


def parse_media_alias_request(text: str) -> MediaAliasRequest | None:
    """Recognize only explicit create/delete language; never fuzzy teaching."""
    words = _surface_words(text)
    canonical = tuple(normalize_for_compare(word) for word in words)
    if not words:
        return None
    if canonical[0] in {"vergiss", "loesche"}:
        index = 1
        if index < len(words) and canonical[index] == "den":
            index += 1
        if index < len(words) and canonical[index] in {"medienalias", "mediaalias", "media-alias"}:
            index += 1
        elif canonical[index:index + 2] == ("media", "alias"):
            index += 2
        else:
            return None
        alias = " ".join(words[index:])
        return MediaAliasRequest("delete", alias) if 2 <= len(alias) <= 80 else None
    if canonical[:2] == ("merke", "dir"):
        start = 2
    elif canonical[0] == "speichere":
        start = 1
    else:
        return None
    separators = [index for index in range(start, len(words)) if canonical[index] == "als"]
    if len(separators) != 1:
        return None
    separator = separators[0]
    item_words = words[start:separator]
    alias_words = words[separator + 1:]
    if alias_words and normalize_for_compare(alias_words[0]) in _POSSESSIVES:
        alias_words = alias_words[1:]
    item = " ".join(item_words)
    alias = " ".join(alias_words)
    if not (2 <= len(alias) <= 80) or normalize_for_compare(item) == normalize_for_compare(alias):
        return None
    item_head = normalize_for_compare(item_words[0]) if item_words else ""
    media_type = next(
        (candidate for candidate, prefixes in _TYPE_PREFIXES.items() if item_head in prefixes),
        None,
    )
    if media_type is None:
        return None
    return MediaAliasRequest("create", alias, item, media_type)


class MediaAliasStore:
    """Atomic per-HA store with globally unambiguous names and user ownership."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._lock = hass.data.setdefault(MEDIA_ALIAS_LOCK_KEY, asyncio.Lock())

    def _path(self) -> str:
        return self._hass.config.path(MEDIA_ALIAS_FILENAME)

    async def async_list(self) -> tuple[MediaAlias, ...]:
        raw = await self._hass.async_add_executor_job(self._read, self._path())
        return tuple(self._decode(item) for item in raw)

    async def async_find_owned(self, alias: str, owner_user_id: str) -> MediaAlias | None:
        wanted = normalize_for_compare(alias)
        matches = [
            item for item in await self.async_list()
            if item.owner_user_id == owner_user_id
            and normalize_for_compare(item.alias) == wanted
        ]
        if len(matches) > 1:
            raise ValueError("Der Medienalias ist im lokalen Speicher nicht eindeutig")
        return matches[0] if matches else None

    async def async_find_for_delete(
        self, alias: str, actor_user_id: str, *, is_admin: bool
    ) -> MediaAlias | None:
        wanted = normalize_for_compare(alias)
        matches = [
            item for item in await self.async_list()
            if normalize_for_compare(item.alias) == wanted
            and (item.owner_user_id == actor_user_id or is_admin)
        ]
        if len(matches) > 1:
            raise ValueError("Mehrere Benutzer besitzen diesen Alias; bitte verwalte ihn in der UI")
        return matches[0] if matches else None

    async def async_name_exists(self, alias: str) -> bool:
        wanted = normalize_for_compare(alias)
        return any(
            normalize_for_compare(item.alias) == wanted
            for item in await self.async_list()
        )

    async def async_save(self, alias: MediaAlias) -> None:
        async with self._lock:
            raw = await self._hass.async_add_executor_job(self._read, self._path())
            decoded = [self._decode(item) for item in raw]
            wanted = normalize_for_compare(alias.alias)
            conflicts = [item for item in decoded if normalize_for_compare(item.alias) == wanted]
            if conflicts:
                raise ValueError(f"Der Medienalias „{alias.alias}“ ist bereits vergeben")
            await self._hass.async_add_executor_job(
                self._write, self._path(), [*raw, asdict(alias)]
            )

    async def async_delete(self, alias: MediaAlias, actor_user_id: str, *, is_admin: bool) -> None:
        if alias.owner_user_id != actor_user_id and not is_admin:
            raise PermissionError("Nur der Besitzer oder ein Administrator darf den Medienalias löschen")
        async with self._lock:
            raw = await self._hass.async_add_executor_job(self._read, self._path())
            decoded = [self._decode(item) for item in raw]
            matches = [index for index, current in enumerate(decoded) if current == alias]
            if len(matches) != 1:
                raise ValueError(
                    "Der Medienalias wurde inzwischen geändert oder existiert nicht mehr"
                )
            retained = [item for index, item in enumerate(raw) if index != matches[0]]
            await self._hass.async_add_executor_job(self._write, self._path(), retained)

    @staticmethod
    def _read(path: str) -> list[dict[str, Any]]:
        try:
            with open(path, encoding="utf-8") as handle:
                document = json.load(handle)
        except FileNotFoundError:
            return []
        if not isinstance(document, dict) or document.get("schema_version") != MEDIA_ALIAS_SCHEMA_VERSION:
            raise ValueError("Der lokale Medienalias-Speicher ist beschädigt")
        aliases = document.get("aliases")
        if not isinstance(aliases, list) or not all(isinstance(item, dict) for item in aliases):
            raise ValueError("Der lokale Medienalias-Speicher ist beschädigt")
        return aliases

    @staticmethod
    def _write(path: str, aliases: list[dict[str, Any]]) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        write_utf8_file_atomic(
            path,
            json.dumps(
                {"schema_version": MEDIA_ALIAS_SCHEMA_VERSION, "aliases": aliases},
                ensure_ascii=False,
                indent=2,
            ),
        )

    @staticmethod
    def _decode(raw: dict[str, Any]) -> MediaAlias:
        try:
            return MediaAlias(**raw)
        except (TypeError, ValueError) as err:
            raise ValueError("Ein lokaler Medienalias ist beschädigt") from err


def media_alias_conflicts(alias: str, entities: Sequence[EntitySnapshot]) -> bool:
    """Reject collisions with device, room, person and already exposed aliases."""
    wanted = normalize_for_compare(alias)
    names: set[str] = set()
    for entity in entities:
        names.update((entity.friendly_name, *entity.aliases))
        if entity.area_name:
            names.add(entity.area_name)
        names.update(entity.area_aliases)
    return any(normalize_for_compare(name) == wanted for name in names if name)


async def async_resolve_media_alias(
    hass: HomeAssistant,
    request: MediaRequest,
    entities: Sequence[EntitySnapshot],
    *,
    owner_user_id: str | None,
    satellite_area_id: str | None,
    context: Context | None = None,
) -> MusicAssistantResolution | None:
    """Resolve one owned alias, revalidating the exact item before playback."""
    if request.op != "play" or not request.source_words or owner_user_id is None:
        return None
    spoken_alias = " ".join(request.source_words)
    alias = await MediaAliasStore(hass).async_find_owned(spoken_alias, owner_user_id)
    if alias is None:
        return None
    target = resolve_media_player(
        request, entities, satellite_area_id=satellite_area_id, match_source_lists=False
    )
    if target.player is None:
        return MusicAssistantResolution(None, target.spoken, target.question)
    player = target.player
    if player.integration != DOMAIN or player.config_entry_id != alias.config_entry_id:
        return MusicAssistantResolution(
            None,
            "Dieser Medienalias gehört nicht zur Music-Assistant-Instanz des gewählten Players; "
            "ich habe nichts gestartet.",
        )
    searched = await async_search_exact_music_assistant(
        hass,
        config_entry_id=alias.config_entry_id,
        name=alias.canonical_name,
        media_type=alias.media_type,
        library_only=True,
        context=context,
    )
    valid = searched.status == "ok" and len(searched.items) == 1
    current = searched.items[0] if valid else None
    if current is None or current.uri != alias.media_id:
        return MusicAssistantResolution(
            None,
            f"Der Medienalias „{alias.alias}“ ist nicht mehr eindeutig gültig; ich habe nichts gestartet.",
        )
    typed = MusicAssistantPlan(
        operation="play",
        media_type=alias.media_type,
        query=alias.alias,
        media_id=alias.media_id,
        target_player=player.entity_id,
        queue_mode=request.enqueue,
        resolution_source="confirmed_user_media_alias",
        unique=True,
    )
    return MusicAssistantResolution(
        typed.service_call(),
        f"{player.friendly_name} spielt {alias.canonical_name}.",
        media_plan=typed,
    )


def new_media_alias(
    request: MediaAliasRequest,
    owner_user_id: str,
    config_entry_id: str,
    item: MusicAssistantItem,
) -> MediaAlias:
    """Build a validated pending record after one exact current match."""
    return MediaAlias(
        request.alias,
        owner_user_id,
        config_entry_id,
        item.media_type,
        item.name,
        item.uri,
        datetime.now(timezone.utc).isoformat(),
    )
