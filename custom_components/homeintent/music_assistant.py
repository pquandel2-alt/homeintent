"""Optional, capability-detected Music Assistant adapter.

Language parsing and player grounding stay pure in :mod:`media`.  This
module is the read boundary for ``music_assistant.search`` and turns one
unique, exact result into a typed plan.  It never imports Music Assistant,
never opens a media URL and never executes the resulting write itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
from typing import Mapping, Sequence

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .entities import EntitySnapshot, normalize_for_compare
from .media import QUEUE_MODES, MediaRequest, _as_said, resolve_media_player
from .service_call import ServiceCallPlan

_LOGGER = logging.getLogger(__name__)

DOMAIN = "music_assistant"
SEARCH = "search"
PLAY_MEDIA = "play_media"
SEARCH_TIMEOUT_SECONDS = 9.0
_SUPPORTED_TYPES = frozenset({"artist", "album", "playlist", "track", "radio"})
_RESPONSE_KEYS = {
    "artists": "artist",
    "albums": "album",
    "playlists": "playlist",
    "tracks": "track",
    "radio": "radio",
}
_TYPE_NAMES = {
    "artist": "Künstler",
    "album": "Album",
    "playlist": "Playlist",
    "track": "Titel",
    "radio": "Radiosender",
}
_SAFE_MEDIA_ID = re.compile(r"^[a-zA-Z][a-zA-Z0-9+._-]*://[^\s]{1,2040}$")
_FORBIDDEN_SCHEMES = ("http://", "https://", "file://", "data:", "ftp://")
_NUMBER_WORDS: dict[str, str] = {
    "null": "0",
    "eins": "1",
    "ein": "1",
    "eine": "1",
    "zwei": "2",
    "zwo": "2",
    "drei": "3",
    "vier": "4",
    "fuenf": "5",
    "sechs": "6",
    "sieben": "7",
    "acht": "8",
    "neun": "9",
    "zehn": "10",
    "elf": "11",
    "zwoelf": "12",
    "dreizehn": "13",
    "vierzehn": "14",
    "fuenfzehn": "15",
    "sechzehn": "16",
    "siebzehn": "17",
    "achtzehn": "18",
    "neunzehn": "19",
    "zwanzig": "20",
}


def is_safe_music_assistant_media_id(value: str) -> bool:
    """Whether a value is an opaque MA URI and never a direct external URL."""
    folded = value.casefold()
    return not folded.startswith(_FORBIDDEN_SCHEMES) and _SAFE_MEDIA_ID.fullmatch(value) is not None


@dataclass(frozen=True)
class MusicAssistantItem:
    """One strictly validated item returned by Music Assistant."""

    name: str
    uri: str
    media_type: str


@dataclass(frozen=True)
class MusicAssistantPlan:
    """Auditable typed media plan before conversion to a service call."""

    operation: str
    media_type: str
    query: str
    media_id: str
    target_player: str
    queue_mode: str
    resolution_source: str
    unique: bool

    def __post_init__(self) -> None:
        if self.queue_mode not in QUEUE_MODES:
            raise ValueError("unknown Music Assistant queue mode")

    def service_call(self) -> ServiceCallPlan:
        return ServiceCallPlan(
            DOMAIN,
            PLAY_MEDIA,
            self.target_player,
            {
                "media_id": self.media_id,
                "media_type": self.media_type,
                "enqueue": self.queue_mode,
            },
        )


@dataclass(frozen=True)
class MusicAssistantResolution:
    plan: ServiceCallPlan | None
    spoken: str
    question: bool = False
    media_plan: MusicAssistantPlan | None = None
    pending: PendingMusicAssistant | None = None


@dataclass(frozen=True)
class MusicAssistantSearchResult:
    """Fail-closed result of one bounded exact library lookup."""

    status: str
    items: tuple[MusicAssistantItem, ...] = ()


@dataclass(frozen=True)
class PendingMusicAssistant:
    """Typed, short-lived continuation; never contains an executable URL."""

    stage: str  # player | media_type
    request: MediaRequest
    player_ids: tuple[str, ...] = ()
    player_id: str | None = None
    media_types: tuple[str, ...] = ()


def _available(hass: HomeAssistant, service: str) -> bool:
    checker = getattr(hass.services, "has_service", None)
    return bool(checker and checker(DOMAIN, service))


def _safe_item(raw: object, media_type: str) -> MusicAssistantItem | None:
    if not isinstance(raw, Mapping):
        return None
    name = raw.get("name")
    uri = raw.get("uri")
    if not isinstance(name, str) or not name.strip() or not isinstance(uri, str):
        return None
    if not is_safe_music_assistant_media_id(uri):
        return None
    return MusicAssistantItem(name.strip(), uri, media_type)


def _number_name_parts(value: str) -> tuple[str, ...]:
    """Return the bounded speech-equivalent parts of one media name.

    This deliberately is not fuzzy matching.  Only punctuation boundaries and
    German number words that speech-to-text commonly emits for written digits
    are folded.  Compact branding (``1LIVE``) is compared separately below.
    """

    folded = normalize_for_compare(value)
    parts = re.findall(r"[a-z0-9]+", folded)
    return tuple(
        _NUMBER_WORDS[part] if part in _NUMBER_WORDS else part
        for part in parts
    )


def _number_name_equivalent(left: str, right: str) -> bool:
    """Whether two names differ only by a proven numeric speech spelling."""

    if normalize_for_compare(left) == normalize_for_compare(right):
        return True
    left_parts = _number_name_parts(left)
    right_parts = _number_name_parts(right)
    if not left_parts or not right_parts:
        return False
    numeric = any(
        any(char.isdigit() for char in part)
        for part in (*left_parts, *right_parts)
    )
    if not numeric:
        return False
    return left_parts == right_parts or "".join(left_parts) == "".join(right_parts)


def _search_name_variants(name: str) -> tuple[str, ...]:
    """Bounded service-search spellings for STT number words.

    Music Assistant may filter before returning results, so accepting an
    equivalent returned name is insufficient when ``eins live`` does not make
    ``1LIVE`` enter the response.  At most two deterministic numeric spellings
    are tried after the original query.
    """

    parts = _number_name_parts(name)
    original_parts = tuple(re.findall(r"[a-z0-9]+", normalize_for_compare(name)))
    if not parts or parts == original_parts:
        return (name,)
    candidates = (name, " ".join(parts), "".join(parts))
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def _exact_items(
    response: object, query: str, requested_type: str | None
) -> tuple[MusicAssistantItem, ...]:
    if not isinstance(response, Mapping):
        return ()
    wanted = normalize_for_compare(query)
    found: dict[tuple[str, str], MusicAssistantItem] = {}
    for key, media_type in _RESPONSE_KEYS.items():
        if requested_type is not None and media_type != requested_type:
            continue
        raw_items = response.get(key, ())
        if not isinstance(raw_items, (list, tuple)):
            continue
        for raw in raw_items:
            item = _safe_item(raw, media_type)
            if item is not None and (
                normalize_for_compare(item.name) == wanted
                or _number_name_equivalent(item.name, query)
            ):
                found[(item.media_type, item.uri)] = item
    return tuple(found.values())


def _valid_search_response(response: object) -> bool:
    """Whether the documented MA search result container is structurally valid."""
    return isinstance(response, Mapping) and all(
        isinstance(response.get(key), (list, tuple)) for key in _RESPONSE_KEYS
    )


def _query_as_said(request: MediaRequest) -> str:
    if request.text:
        return _as_said(request.text, request.source_words)
    return " ".join(request.source_words)


def _spoken_list(values: Sequence[str]) -> str:
    """Join a short, already deduplicated German option list."""

    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} oder {values[1]}"
    return f"{', '.join(values[:-1])} oder {values[-1]}"


async def async_search_exact_music_assistant(
    hass: HomeAssistant,
    *,
    config_entry_id: str,
    name: str,
    media_type: str | None,
    library_only: bool,
    context: Context | None = None,
) -> MusicAssistantSearchResult:
    """Search the real MA service once, bounded and without exposing errors."""
    if not config_entry_id or media_type not in _SUPPORTED_TYPES | {None}:
        return MusicAssistantSearchResult("invalid")
    if not _available(hass, SEARCH):
        return MusicAssistantSearchResult("unavailable")
    data: dict[str, object] = {
        "config_entry_id": config_entry_id,
        "limit": 10,
        "library_only": library_only,
    }
    if media_type is not None:
        data["media_type"] = media_type
    try:
        # One deadline covers every deterministic spelling variant.  Adding
        # STT normalization must never multiply the request latency budget.
        async with asyncio.timeout(SEARCH_TIMEOUT_SECONDS):
            for search_name in _search_name_variants(name):
                call_data = {**data, "name": search_name}
                response = await hass.services.async_call(
                    DOMAIN,
                    SEARCH,
                    call_data,
                    blocking=True,
                    return_response=True,
                    context=context,
                )
                if not _valid_search_response(response):
                    _LOGGER.warning("HomeIntent Music Assistant returned a malformed search response")
                    return MusicAssistantSearchResult("malformed")
                items = _exact_items(response, name, media_type)
                if items:
                    return MusicAssistantSearchResult("ok", items)
                if _has_raw_exact_name(response, name, media_type):
                    return MusicAssistantSearchResult("unsafe")
    except TimeoutError:
        _LOGGER.warning("HomeIntent Music Assistant library search timed out")
        return MusicAssistantSearchResult("timeout")
    except HomeAssistantError:
        _LOGGER.warning("HomeIntent Music Assistant library search failed")
        return MusicAssistantSearchResult("unavailable")
    return MusicAssistantSearchResult("ok")


async def async_resolve_music_assistant(
    hass: HomeAssistant,
    request: MediaRequest,
    entities: Sequence[EntitySnapshot],
    *,
    satellite_area_id: str | None,
    context: Context | None = None,
) -> MusicAssistantResolution | None:
    """Resolve a named play request using current registered capabilities.

    ``None`` means the request belongs to the established generic media
    resolver.  Once a typed Music Assistant request is recognized, all
    failures are explicit and closed: no fallback may reinterpret an
    ambiguous library name as a player source.
    """
    if request.op != "play" or not request.source_words:
        return None
    if request.enqueue not in QUEUE_MODES:
        return MusicAssistantResolution(
            None, "Diesen Warteschlangenmodus unterstütze ich nicht; ich habe nichts gestartet."
        )
    if re.search(r"(?:https?|ftp|file)://|data:", request.text, re.IGNORECASE):
        return MusicAssistantResolution(
            None, "Direkte Medien-URLs führe ich nicht aus; ich habe nichts gestartet."
        )
    if request.media_type is not None and request.media_type not in _SUPPORTED_TYPES:
        return MusicAssistantResolution(None, "Diesen Medientyp unterstütze ich nicht.")
    explicit_library_request = request.media_type is not None or request.library_only
    if not (_available(hass, SEARCH) and _available(hass, PLAY_MEDIA)):
        if explicit_library_request:
            return MusicAssistantResolution(
                None, "Music Assistant ist nicht verfügbar; ich habe nichts gestartet."
            )
        return None

    usable = [entity for entity in entities if entity.state not in {"unavailable", "unknown"}]
    target = resolve_media_player(
        request, usable, satellite_area_id=satellite_area_id, match_source_lists=False
    )
    if target.player is None:
        pending = (
            PendingMusicAssistant(
                "player", request, player_ids=tuple(item.entity_id for item in target.candidates)
            )
            if target.question and target.candidates else None
        )
        return MusicAssistantResolution(None, target.spoken, target.question, pending=pending)
    player = target.player
    if player.integration != DOMAIN or not player.config_entry_id:
        if explicit_library_request:
            return MusicAssistantResolution(
                None,
                f"{player.friendly_name} ist kein eindeutig erkannter Music-Assistant-Player; "
                "ich habe nichts gestartet.",
            )
        return None

    query = _query_as_said(request)
    requested_type = request.media_type
    try:
        # The initial full-name lookup and the optional structural ``Radio X``
        # retry share one deadline.  A fallback must not double latency.
        async with asyncio.timeout(SEARCH_TIMEOUT_SECONDS):
            searched = await async_search_exact_music_assistant(
                hass,
                config_entry_id=player.config_entry_id,
                name=query,
                media_type=requested_type,
                library_only=request.library_only,
                context=context,
            )
            items = searched.items
            # ``Radio Bob`` may be the exact library name, while ``Radio
            # Energy`` may be the natural German type plus the library name
            # ``ENERGY``.  Preserve the exact full-name result first.  Only
            # when it has no match, retry the bounded structural reading as a
            # radio request; never maintain a station dictionary or use a
            # fuzzy result.
            if (
                not items
                and searched.status == "ok"
                and requested_type is None
                and len(request.source_words) > 1
                and request.source_words[0] == "radio"
            ):
                fallback_words = request.source_words[1:]
                fallback_query = (
                    _as_said(request.text, fallback_words)
                    if request.text
                    else " ".join(fallback_words)
                )
                searched = await async_search_exact_music_assistant(
                    hass,
                    config_entry_id=player.config_entry_id,
                    name=fallback_query,
                    media_type="radio",
                    library_only=request.library_only,
                    context=context,
                )
                query = fallback_query
                requested_type = "radio"
                items = searched.items
    except TimeoutError:
        return MusicAssistantResolution(
            None, "Die Music-Assistant-Bibliothek antwortet gerade nicht; ich habe nichts gestartet."
        )
    if searched.status == "timeout":
        return MusicAssistantResolution(
            None, "Die Music-Assistant-Bibliothek antwortet gerade nicht; ich habe nichts gestartet."
        )
    if searched.status == "unavailable":
        return MusicAssistantResolution(
            None, "Die Music-Assistant-Bibliothek ist gerade nicht erreichbar; ich habe nichts gestartet."
        )
    if searched.status in {"malformed", "invalid"}:
        return MusicAssistantResolution(
            None, "Music Assistant hat keine gültige Suchantwort geliefert; ich habe nichts gestartet."
        )
    if not items:
        if searched.status == "unsafe":
            return MusicAssistantResolution(
                None, "Music Assistant hat für diesen Treffer keine sichere Media-ID geliefert."
            )
        kind = _TYPE_NAMES.get(requested_type or "", "Medium")
        return MusicAssistantResolution(
            None, f"{kind} „{query}“ finde ich in Music Assistant nicht."
        )
    if len(items) != 1:
        media_types = tuple(dict.fromkeys(item.media_type for item in items))
        if len(media_types) == 1:
            kind = _TYPE_NAMES[media_types[0]]
            return MusicAssistantResolution(
                None,
                f"Ich finde mehrere Einträge vom Typ {kind} mit dem Namen „{query}“. "
                "Bitte benenne den gewünschten Eintrag in Music Assistant eindeutig.",
                question=True,
            )
        options = _spoken_list(tuple(_TYPE_NAMES[kind] for kind in media_types))
        return MusicAssistantResolution(
            None,
            f"Ich finde „{query}“ als {options}. Welche meinst du?",
            question=True,
            pending=PendingMusicAssistant(
                "media_type",
                request,
                player_id=player.entity_id,
                media_types=media_types,
            ),
        )

    item = items[0]
    typed = MusicAssistantPlan(
        operation="play",
        media_type=item.media_type,
        query=query,
        media_id=item.uri,
        target_player=player.entity_id,
        queue_mode=request.enqueue,
        resolution_source="music_assistant_search_exact",
        unique=True,
    )
    return MusicAssistantResolution(
        typed.service_call(),
        f"{player.friendly_name} spielt {_TYPE_NAMES[item.media_type]} „{item.name}“.",
        media_plan=typed,
    )


def _has_raw_exact_name(response: object, query: str, requested_type: str | None) -> bool:
    if not isinstance(response, Mapping):
        return False
    wanted = normalize_for_compare(query)
    for key, media_type in _RESPONSE_KEYS.items():
        if requested_type is not None and requested_type != media_type:
            continue
        values = response.get(key, ())
        if isinstance(values, (list, tuple)) and any(
            isinstance(value, Mapping)
            and isinstance(value.get("name"), str)
            and (
                normalize_for_compare(value["name"]) == wanted
                or _number_name_equivalent(value["name"], query)
            )
            for value in values
        ):
            return True
    return False


__all__ = (
    "MusicAssistantItem",
    "MusicAssistantPlan",
    "MusicAssistantResolution",
    "MusicAssistantSearchResult",
    "PendingMusicAssistant",
    "SEARCH_TIMEOUT_SECONDS",
    "async_resolve_music_assistant",
    "async_search_exact_music_assistant",
    "is_safe_music_assistant_media_id",
)
