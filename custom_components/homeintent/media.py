"""Music and media (7.9.3 B3).

Commands: "Spiel Musik im Wohnzimmer", "Spiel Bayern 3 in der Küche",
"Pause", "Weiter", "Nächstes Lied", "Lauter", "Leiser", "Lautstärke 30",
"Mach die Musik aus".  Question: "Was läuft gerade?" (title and artist from
the attributes).

Target: a room in the sentence -> the media player there; a named player ->
that one; otherwise the player in the room of the voice satellite, else the
one that is playing.  Several candidates -> a question, never a guess.

Sources and stations only from ``source_list`` of the target (exact words,
case and umlauts folded).  "Musik" without a source plays the last used
source; without one HomeIntent asks.  The plan is executed like every
command (policy, ``service_executor``, confirmation tone, effect wait).

Home-Assistant-free and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .entities import EntitySnapshot, normalize_for_compare
from .media_semantics import MediaCapability, supports_media
from .nlu.device_ontology import entity_genera
from .nlu.place_model import PlaceKind
from .nlu.semantic_location import resolve_semantic_place
from .service_call import ServiceCallPlan

__all__ = (
    "QUEUE_MODES",
    "MediaRequest",
    "MediaPlayerResolution",
    "claims_other_target",
    "MediaResolution",
    "now_playing",
    "parse_media_request",
    "resolve_media",
    "resolve_media_player",
)

_PLAY_VERBS = frozenset({"spiel", "spiele", "spielen", "abspielen", "starte", "start", "leg", "lege", "mach", "mache"})
_MUSIC_WORDS = frozenset({"musik", "radio", "lied", "lieder", "song", "songs", "playlist", "sender", "titel"})
_PAUSE_WORDS = frozenset({"pause", "pausiere", "pausieren", "anhalten", "halt"})
_STOP_WORDS = frozenset({"stopp", "stop", "stoppe", "stoppen", "beende"})
_RESUME_WORDS = frozenset({"weiter", "fortsetzen", "fortfahren", "weiterspielen", "weitermachen"})
_NEXT_WORDS = frozenset({"naechstes", "naechster", "naechsten", "naechste", "ueberspringe", "ueberspring", "skip"})
_PREVIOUS_WORDS = frozenset({"vorheriges", "vorheriger", "vorherigen", "vorherige", "letztes", "letzten"})
_TRACK_WORDS = frozenset({"lied", "titel", "song", "track", "stueck"})
_LOUDER = frozenset({"lauter"})
_QUIETER = frozenset({"leiser"})
_VOLUME_WORDS = frozenset({"lautstaerke", "volume"})
_QUERY_WORDS = frozenset({"laeuft", "spielt", "singt", "hoere", "hoeren", "kommt"})
_POWER_ACTION_WORDS = frozenset({
    "mach", "mache", "schalte", "schalt", "stell", "stelle",
    "anmachen", "einschalten", "ausmachen", "ausschalten",
})
_MEDIA_DOMAIN = "media_player"
QUEUE_MODES = frozenset({"replace", "next", "add"})


@dataclass(frozen=True)
class MediaRequest:
    op: str  # play | pause | resume | next | previous | louder | quieter | volume | off | query
    source_words: tuple[str, ...] = ()  # "Bayern 3" for play
    volume: int | None = None  # percent for "volume"
    text: str = ""
    media_type: str | None = None
    library_only: bool = False
    enqueue: str = "replace"


_TYPE_MARKERS = {
    "artist": frozenset({"kuenstler", "kuenstlerin", "interpret", "interpretin"}),
    "album": frozenset({"album", "alben"}),
    "playlist": frozenset({"playlist", "playliste", "wiedergabeliste"}),
    "track": frozenset({"titel", "song", "track", "stueck", "lied"}),
    "radio": frozenset({"sender", "radiosender", "radiostation", "station"}),
}
_LIBRARY_MARKERS = frozenset({
    "mein", "meine", "meinen", "meiner", "gespeichert", "gespeicherte", "gespeicherten",
    "favorit", "favoriten", "favorisierte", "favorisierten",
})


def _words(text: str) -> list[str]:
    folded = normalize_for_compare(text)
    return "".join(char if char.isalnum() else " " for char in folded).split()


def _short(words: list[str], allowed: frozenset[str], extra: frozenset[str] = frozenset()) -> bool:
    """A short command made only of these words, music words, room phrases and fillers."""
    fillers = frozenset({"bitte", "mal", "die", "den", "das", "der", "im", "in", "auf", "am", "dem", "noch", "etwas",
                         "ein", "bisschen", "jetzt", "doch", "musik", "radio", "wieder", "es", "wiedergabe",
                         "dort", "da", "hier", "ton"})
    if len(words) > 7 or not set(words) & allowed:
        return False
    # Before a room phrase ("… im Wohnzimmer") only command words and fillers.
    cut = next((index for index, word in enumerate(words) if word in {"im", "in", "am", "auf"}), len(words))
    return all(word in allowed | extra | fillers | _MUSIC_WORDS for word in words[:cut])


def _has_media_power_form(words: Sequence[str], present: set[str]) -> bool:
    """An action lexeme or a bounded verbless media request, never a query."""
    if present & _POWER_ACTION_WORDS:
        return True
    return bool(words) and words[0] in {"musik", "radio", "bitte"} and not (
        present & {"ist", "sind", "war", "waren", "welche", "welcher", "welches"}
    )


def _queue_directive(words: list[str]) -> tuple[list[str], str]:
    """Remove one explicit queue modifier and return its closed value.

    Queue language only applies to an explicit play command.  The remaining
    words continue through the ordinary type/name/player composition, so a
    queue modifier can never become part of a library search term.
    """
    if not words or words[0] not in _PLAY_VERBS:
        return words, "replace"
    for index in range(len(words) - 1):
        if words[index:index + 2] == ["als", "naechstes"]:
            return [*words[:index], *words[index + 2:]], "next"
    queue_words = {"warteschlange", "queue"}
    if set(words) & queue_words and "hinzu" in words:
        removed = queue_words | {"hinzu", "zur"}
        cleaned = [word for word in words if word not in removed]
        if cleaned[-1:] == ["und"]:
            cleaned.pop()
        return cleaned, "add"
    if set(words) & queue_words and any(word.startswith("ersetz") for word in words):
        removed = queue_words | {"und", "die"}
        cleaned = [
            word for word in words
            if word not in removed and not word.startswith("ersetz")
        ]
        return cleaned, "replace"
    return words, "replace"


def parse_media_request(text: str) -> MediaRequest | None:
    """A media command or "Was läuft?", else ``None``."""
    words = _words(text)
    words, enqueue = _queue_directive(words)
    present = set(words)
    if not words or present & {"wenn", "sobald", "falls", "nicht", "kein", "keine"}:
        return None
    if present & {"alle", "allen", "saemtliche", "saemtlichen"}:
        return None  # "Pausiere alle Medien": the multi-target plan with its preview
    if words[0] in {"was", "welches", "welcher", "wer"} and present & _QUERY_WORDS and (
        present & {"gerade", "jetzt", "da", "lied", "titel", "song", "radio", "musik", "im", "in", "auf", "am"}
        or len(words) <= 3
    ):
        return MediaRequest("query", text=text)
    if present & _NEXT_WORDS and present & _TRACK_WORDS or (words[0] in {"ueberspringe", "ueberspring", "skip"}):
        return MediaRequest("next", text=text)
    if present & _PREVIOUS_WORDS and present & _TRACK_WORDS:
        return MediaRequest("previous", text=text)
    if present & _LOUDER and _short(words, _LOUDER, frozenset({"mach", "mache", "stell", "stelle", "dreh", "drehe"})):
        return MediaRequest("louder", text=text)
    if present & _QUIETER and _short(words, _QUIETER, frozenset({"mach", "mache", "stell", "stelle", "dreh", "drehe"})):
        return MediaRequest("quieter", text=text)
    if present & _VOLUME_WORDS:
        numbers = [int(word) for word in words if word.isdigit()]
        if len(numbers) == 1 and 0 <= numbers[0] <= 100:
            return MediaRequest("volume", volume=numbers[0], text=text)
        return None
    # A bare "Stopp" remains the universal conversation cancellation.  An
    # explicitly named playback is the standard media_player.media_stop.
    if words[0] in _STOP_WORDS and present & {"musik", "radio", "wiedergabe", "lied", "titel"}:
        return MediaRequest("stop", text=text)
    if present & {"stumm", "stummschalten"} and present & {"musik", "radio", "ton", "wiedergabe"}:
        return MediaRequest("mute", text=text)
    if words[0] in {"mach", "mache", "schalte", "schalt"} and "ton" in present and (
        "an" in present or "laut" in present
    ):
        return MediaRequest("unmute", text=text)
    # A bare "Stopp"/"Halt" stays the universal cancel; "Pause" pauses.
    if words[0] in _PAUSE_WORDS and len(words) <= 5 and (
        words == ["pause"] or words[0] in {"pausiere", "pausieren"}
        or present & _MUSIC_WORDS or present & {"im", "in", "am", "auf"}
    ):
        return MediaRequest("pause", text=text)
    if words[0] in {"mach", "mache"} and len(words) <= 5 and "pause" in present and _short(
        words, frozenset({"pause"}), frozenset({"mach", "mache"})
    ):
        return MediaRequest("pause", text=text)
    if words[0] in {"mach", "mache"} and "weiter" in present and _short(
        words, frozenset({"weiter"}), frozenset({"mach", "mache", "mit"})
    ):
        return MediaRequest("resume", text=text)
    if (
        present & {"musik", "radio"}
        and (present & {"aus", "stopp", "stop", "ausmachen", "ausschalten"})
        and _has_media_power_form(words, present)
    ):
        return MediaRequest("off", text=text)
    if present & {"musik", "radio"} and (
        present & {"an", "ein", "anmachen", "einschalten"}
    ) and (
        _has_media_power_form(words, present)
    ):
        return MediaRequest("on", text=text)
    if words[0] in _RESUME_WORDS and len(words) <= 5 and (len(words) == 1 or present & _MUSIC_WORDS or present & {
        "im", "in", "am", "auf", "spielen"
    }):
        return MediaRequest("resume", text=text)
    if words[0] in {"spiel", "spiele"} and present & _RESUME_WORDS and _short(
        words, _RESUME_WORDS, frozenset({"spiel", "spiele"})
    ):
        return MediaRequest("resume", text=text)
    if words[0] in {"spiel", "spiele", "spielen", "leg", "lege"} or (
        words[0] == "starte" and present & {"musik", "radio"}
    ) or (
        words[0] in {"mach", "mache"} and present & {"musik"} and "an" in present
    ):
        rest = [word for word in words[1:] if word not in {"bitte", "mal", "doch", "etwas", "an", "ab", "ein"}]
        cut = next((index for index, word in enumerate(rest) if word in {"im", "in", "am", "auf", "ueber"}), len(rest))
        candidate = rest[:cut]
        marked_types = [kind for kind, markers in _TYPE_MARKERS.items() if set(candidate) & markers]
        media_type = marked_types[0] if len(marked_types) == 1 else None
        marker_words = _TYPE_MARKERS[media_type] if media_type is not None else frozenset()
        library_only = bool(set(candidate) & _LIBRARY_MARKERS)
        named = tuple(word for word in candidate if word not in {
            "die", "den", "das", "der", "mir", "uns", *_LIBRARY_MARKERS, *marker_words,
        })
        if not named:
            return MediaRequest("play", text=text, enqueue=enqueue) if present & {"musik", "radio"} else None
        if set(named) <= _MUSIC_WORDS | {"etwas", "was", "ein", "bisschen"}:
            return MediaRequest("play", text=text, enqueue=enqueue)
        return MediaRequest(
            "play", named, text=text, media_type=media_type, library_only=library_only,
            enqueue=enqueue,
        )
    return None


@dataclass(frozen=True)
class MediaResolution:
    plan: ServiceCallPlan | None
    spoken: str  # the success text, or the honest answer / question
    question: bool = False


@dataclass(frozen=True)
class MediaPlayerResolution:
    player: EntitySnapshot | None
    spoken: str = ""
    question: bool = False
    candidates: tuple[EntitySnapshot, ...] = ()


def _players(entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    return [entity for entity in entities if entity.domain == _MEDIA_DOMAIN]


def _named_players(text: str, players: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    key = normalize_for_compare(text)
    found = []
    for player in players:
        names = (player.friendly_name, *player.aliases)
        if any(normalize_for_compare(name) in key for name in names if name):
            found.append(player)
    return found


def _room_players(
    text: str,
    entities: Sequence[EntitySnapshot],
    players: Sequence[EntitySnapshot],
) -> tuple[str | None, list[EntitySnapshot]]:
    """Return the grounded place label and players constrained by it."""
    resolved = resolve_semantic_place(text, list(entities))
    if resolved is None:
        return None, []
    _, place = resolved
    # ``hier`` is bound by ``resolve_media_player`` to the satellite area.
    if place.kind is PlaceKind.HERE:
        return None, []
    return place.label, [player for player in players if place.contains(player)]


def _match_source(words: Sequence[str], sources: Sequence[str]) -> str | None:
    wanted = _source_keys(words)
    exact = [source for source in sources if wanted & _source_keys(_words(source))]
    if len(exact) == 1:
        return exact[0]
    return None


_NUMBER_WORDS = {
    "null": "0", "eins": "1", "ein": "1", "eine": "1", "zwei": "2",
    "drei": "3", "vier": "4", "fuenf": "5", "sechs": "6", "sieben": "7",
    "acht": "8", "neun": "9", "zehn": "10", "elf": "11", "zwoelf": "12",
}


def _source_keys(words: Sequence[str]) -> frozenset[str]:
    """Exact orthographic keys; joins and number words never add fuzziness."""
    normalized = tuple(_NUMBER_WORDS.get(word, word) for word in words)
    return frozenset({" ".join(normalized), "".join(normalized)})


def _listed(names: Sequence[str]) -> str:
    quoted = [f"„{name}“" for name in names]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " oder " + quoted[-1]


def resolve_media_player(
    request: MediaRequest,
    entities: Sequence[EntitySnapshot],
    *,
    satellite_area_id: str | None,
    match_source_lists: bool = True,
) -> MediaPlayerResolution:
    """Resolve exactly one exposed player without guessing.

    Music Assistant reuses this target grounding but deliberately disables
    ``source_list`` matching: a library item is not a player input source.
    """
    players = _players(entities)
    if not players:
        return MediaPlayerResolution(None, "Ich finde kein Mediengerät.")
    if request.op != "play" and "radio" in _words(request.text):
        # A generic device genus remains typed.  A speaker in the local room
        # is not automatically a radio merely because it could play audio;
        # registry evidence must establish the requested kind.
        radios = [player for player in players if "radio" in entity_genera(player)]
        if not radios:
            return MediaPlayerResolution(
                None,
                "Ich finde kein als Radio freigegebenes Mediengerät. "
                "Ich habe nichts ausgeführt.",
            )
        players = radios
    place_label, in_room = _room_players(request.text, entities, players)
    room_named = place_label is not None
    scoped = in_room if room_named else players
    named = _named_players(request.text, scoped)
    if room_named and not in_room:
        genus = "Radio" if "radio" in _words(request.text) else "Mediengerät"
        return MediaPlayerResolution(
            None, f"Im Bereich {place_label} finde ich kein {genus}."
        )
    if named:
        candidates = named
    elif room_named:
        candidates = in_room
        if not candidates:
            return MediaPlayerResolution(None, "In diesem Raum finde ich kein Mediengerät.")
    else:
        candidates = []
        if satellite_area_id is not None:
            # The voice satellite supplies the implicit place.  It is the
            # same hard constraint as a spoken room: an active player or a
            # matching source elsewhere must never make the resolver leave
            # that room silently.
            candidates = [
                player for player in players
                if player.area_id == satellite_area_id
            ]
            if not candidates:
                return MediaPlayerResolution(
                    None,
                    "Im Raum des Sprachsatelliten finde ich kein Mediengerät. "
                    "Ich habe nichts ausgeführt.",
                )
        elif match_source_lists and request.op == "play" and request.source_words:
            candidates = [
                player for player in players
                if _match_source(request.source_words, _sources(player)) is not None
            ]
        if not candidates and request.op == "resume":
            candidates = [player for player in players if player.state == "paused"]
        if not candidates:
            candidates = [player for player in players if player.state == "playing"]
            if not candidates and request.op in {
                "pause", "stop", "mute", "unmute", "next", "previous", "louder", "quieter", "off", "volume"
            }:
                return MediaPlayerResolution(None, "Gerade spielt nichts.")
    if len(candidates) > 1:
        playing = [player for player in candidates if player.state == "playing"]
        if len(playing) == 1 and request.op != "play":
            candidates = playing
    if len(candidates) != 1:
        options = candidates or players
        return MediaPlayerResolution(
            None,
            f"Welches Gerät meinst du: {_listed([player.friendly_name for player in options])}?",
            question=True,
            candidates=tuple(options),
        )
    return MediaPlayerResolution(candidates[0])


def resolve_media(
    request: MediaRequest,
    entities: Sequence[EntitySnapshot],
    *,
    satellite_area_id: str | None,
) -> MediaResolution:
    """The plan for one media command, or the honest answer/question."""
    target = resolve_media_player(
        request, entities, satellite_area_id=satellite_area_id, match_source_lists=True
    )
    if target.player is None:
        return MediaResolution(None, target.spoken, target.question)
    player = target.player
    name = player.friendly_name
    if request.op == "pause":
        if not supports_media(player, MediaCapability.PAUSE):
            return MediaResolution(None, f"{name} unterstützt Pause nicht.")
        return MediaResolution(ServiceCallPlan(_MEDIA_DOMAIN, "media_pause", player.entity_id), f"{name} pausiert.")
    if request.op == "stop":
        if not supports_media(player, MediaCapability.STOP):
            return MediaResolution(None, f"{name} unterstützt Stoppen nicht.")
        return MediaResolution(ServiceCallPlan(_MEDIA_DOMAIN, "media_stop", player.entity_id), f"{name} gestoppt.")
    if request.op in {"mute", "unmute"}:
        if not supports_media(player, MediaCapability.VOLUME_MUTE):
            return MediaResolution(None, f"{name} unterstützt Stummschalten nicht.")
        muted = request.op == "mute"
        return MediaResolution(
            ServiceCallPlan(_MEDIA_DOMAIN, "volume_mute", player.entity_id, {"is_volume_muted": muted}),
            f"{name} ist {'stumm' if muted else 'nicht mehr stumm'}.",
        )
    if request.op == "off":
        if player.state in {"off", "standby"}:
            return MediaResolution(None, f"{name} ist bereits aus.")
        if supports_media(player, MediaCapability.PAUSE):
            return MediaResolution(
                ServiceCallPlan(_MEDIA_DOMAIN, "media_pause", player.entity_id),
                f"{name} pausiert.",
            )
        if supports_media(player, MediaCapability.STOP):
            return MediaResolution(
                ServiceCallPlan(_MEDIA_DOMAIN, "media_stop", player.entity_id),
                f"{name} gestoppt.",
            )
        if supports_media(player, MediaCapability.TURN_OFF):
            return MediaResolution(
                ServiceCallPlan(_MEDIA_DOMAIN, "turn_off", player.entity_id),
                f"{name} ausgeschaltet.",
            )
        return MediaResolution(None, f"{name} unterstützt weder Pause, Stoppen noch Ausschalten.")
    if request.op == "on":
        if player.state == "playing":
            return MediaResolution(None, f"{name} spielt bereits.")
        if player.state == "off" and supports_media(player, MediaCapability.TURN_ON):
            return MediaResolution(
                ServiceCallPlan(_MEDIA_DOMAIN, "turn_on", player.entity_id),
                f"{name} eingeschaltet.",
            )
        if player.state in {"paused", "idle", "on", "standby"} and supports_media(
            player, MediaCapability.PLAY
        ):
            return MediaResolution(
                ServiceCallPlan(_MEDIA_DOMAIN, "media_play", player.entity_id),
                f"{name} spielt weiter.",
            )
        return MediaResolution(None, f"{name} kann so nicht eingeschaltet oder fortgesetzt werden.")
    if request.op == "resume":
        if not supports_media(player, MediaCapability.PLAY):
            return MediaResolution(None, f"{name} unterstützt Fortsetzen nicht.")
        return MediaResolution(ServiceCallPlan(_MEDIA_DOMAIN, "media_play", player.entity_id), f"{name} spielt weiter.")
    if request.op == "next":
        if not supports_media(player, MediaCapability.NEXT_TRACK):
            return MediaResolution(None, f"{name} unterstützt den nächsten Titel nicht.")
        return MediaResolution(
            ServiceCallPlan(_MEDIA_DOMAIN, "media_next_track", player.entity_id), f"{name}: nächster Titel."
        )
    if request.op == "previous":
        if not supports_media(player, MediaCapability.PREVIOUS_TRACK):
            return MediaResolution(None, f"{name} unterstützt den vorherigen Titel nicht.")
        return MediaResolution(
            ServiceCallPlan(_MEDIA_DOMAIN, "media_previous_track", player.entity_id), f"{name}: vorheriger Titel."
        )
    if request.op == "louder":
        if not supports_media(player, MediaCapability.VOLUME_STEP):
            return MediaResolution(None, f"{name} unterstützt Lautstärkeschritte nicht.")
        return MediaResolution(ServiceCallPlan(_MEDIA_DOMAIN, "volume_up", player.entity_id), f"{name} lauter.")
    if request.op == "quieter":
        if not supports_media(player, MediaCapability.VOLUME_STEP):
            return MediaResolution(None, f"{name} unterstützt Lautstärkeschritte nicht.")
        return MediaResolution(ServiceCallPlan(_MEDIA_DOMAIN, "volume_down", player.entity_id), f"{name} leiser.")
    if request.op == "volume":
        assert request.volume is not None
        if not supports_media(player, MediaCapability.VOLUME_SET):
            return MediaResolution(None, f"{name} unterstützt keine genaue Lautstärke.")
        return MediaResolution(
            ServiceCallPlan(_MEDIA_DOMAIN, "volume_set", player.entity_id, {"volume_level": request.volume / 100}),
            f"{name}: Lautstärke {request.volume} Prozent.",
        )
    sources = _sources(player)
    if request.source_words:
        # "Starte Radio Küche" can name this exact player; an exposed script
        # of that exact name has already had priority before this resolver.
        if (
            _source_keys(request.source_words) & _source_keys(_words(name))
            and _words(request.text)[:1] in (["starte"], ["start"])
        ):
            return resolve_media(
                MediaRequest("on", text=request.text), [player],
                satellite_area_id=player.area_id,
            )
        source = _match_source(request.source_words, sources)
        if source is None:
            spoken = _as_said(request.text, request.source_words)
            listed = f" Verfügbar: {_listed(sources)}." if sources else ""
            return MediaResolution(None, f"„{spoken}“ finde ich bei {name} nicht.{listed}")
        if not supports_media(player, MediaCapability.SELECT_SOURCE):
            return MediaResolution(None, f"{name} bietet {source} an, unterstützt aber keine Quellenwahl.")
        return MediaResolution(
            ServiceCallPlan(_MEDIA_DOMAIN, "select_source", player.entity_id, {"source": source}),
            f"{name} spielt {source}.",
        )
    last = player.attributes.get("source")
    if player.state in {"paused", "idle", "on", "standby"} and isinstance(last, str) and last:
        if last not in sources or not supports_media(player, MediaCapability.PLAY):
            return MediaResolution(
                None, f"Was soll {name} spielen? Die letzte Quelle ist nicht sicher fortsetzbar.",
                question=True,
            )
        return MediaResolution(ServiceCallPlan(_MEDIA_DOMAIN, "media_play", player.entity_id), f"{name} spielt {last}.")
    if player.state == "playing":
        return MediaResolution(None, f"{name} spielt schon {last or ''}".rstrip() + ".")
    if isinstance(last, str) and last in sources:
        if not supports_media(player, MediaCapability.SELECT_SOURCE):
            return MediaResolution(None, f"{name} unterstützt keine Quellenwahl.")
        return MediaResolution(
            ServiceCallPlan(_MEDIA_DOMAIN, "select_source", player.entity_id, {"source": last}),
            f"{name} spielt {last}.",
        )
    listed = f" Zum Beispiel: {_listed(sources[:4])}." if sources else ""
    return MediaResolution(None, f"Was soll {name} spielen?{listed}", question=True)


def _as_said(text: str, words: Sequence[str]) -> str:
    """The words as the user spelled them ("Antenne 1", not "antenne 1")."""
    tokens = "".join(char if char.isalnum() else " " for char in text).split()
    keys = [normalize_for_compare(token) for token in tokens]
    for start in range(len(keys) - len(words) + 1):
        if keys[start:start + len(words)] == list(words):
            return " ".join(tokens[start:start + len(words)])
    return " ".join(words)


def _sources(player: EntitySnapshot) -> list[str]:
    raw = player.attributes.get("source_list")
    return [str(item) for item in raw] if isinstance(raw, (list, tuple)) else []


def now_playing(request: MediaRequest, entities: Sequence[EntitySnapshot], satellite_area_id: str | None) -> str:
    """"Was läuft gerade?" - title and artist, only what the attributes say."""
    players = _players(entities)
    named = _named_players(request.text, players)
    room_named, in_room = _room_players(request.text, entities, players)
    pool = named or (in_room if room_named else [p for p in players if p.state == "playing"])
    if not pool and satellite_area_id is not None:
        pool = [p for p in players if p.area_id == satellite_area_id and p.state == "playing"]
    if not pool:
        return "Gerade läuft nichts."
    parts = []
    for player in pool:
        if player.state != "playing":
            parts.append(f"{player.friendly_name} spielt gerade nicht")
            continue
        title = player.attributes.get("media_title")
        artist = player.attributes.get("media_artist")
        source = player.attributes.get("source")
        if title and artist and artist != title:
            parts.append(f"Auf {player.friendly_name} läuft „{title}“ von {artist}")
        elif title:
            parts.append(f"Auf {player.friendly_name} läuft „{title}“")
        elif source:
            parts.append(f"{player.friendly_name} spielt {source}; einen Titel meldet es nicht")
        else:
            parts.append(f"{player.friendly_name} spielt; einen Titel meldet es nicht")
    return "; ".join(parts) + "."



_OTHER_TARGET_WORDS = frozenset({
    "timer", "wecker", "skript", "script", "szene", "routine", "automation", "es", "ihn", "ihm",
})


def claims_other_target(
    text: str, entities: Sequence[EntitySnapshot], request: MediaRequest | None = None
) -> bool:
    """The sentence is for the established paths: it names a media player
    itself (except next/previous track, which only HomeIntent's media
    reading knows), another device, a timer/script/monitoring, or refers
    back with a pronoun ("Kannst du es pausieren?")."""
    words = _words(text)
    present = set(words)
    if present & _OTHER_TARGET_WORDS:
        return True
    key = " " + " ".join(words) + " "
    from .monitoring_management import _monitoring_noun  # noqa: PLC2701 - the one noun rule

    if any(_monitoring_noun(word)[0] for word in words):
        return True
    for entity in entities:
        for name in (entity.friendly_name, *entity.aliases):
            folded = " ".join(_words(name))
            if folded and f" {folded} " in key:
                if entity.domain != _MEDIA_DOMAIN:
                    return True
                if request is not None and request.op == "play" and request.source_words:
                    continue
                if not (present & (_NEXT_WORDS | _PREVIOUS_WORDS)):
                    return True
    return False
