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
from .service_call import ServiceCallPlan

__all__ = (
    "MediaRequest",
    "claims_other_target",
    "MediaResolution",
    "now_playing",
    "parse_media_request",
    "resolve_media",
)

_PLAY_VERBS = frozenset({"spiel", "spiele", "spielen", "abspielen", "starte", "start", "leg", "lege", "mach", "mache"})
_MUSIC_WORDS = frozenset({"musik", "radio", "lied", "lieder", "song", "songs", "playlist", "sender", "titel"})
_PAUSE_WORDS = frozenset({"pause", "pausiere", "pausieren", "anhalten", "halt", "stopp", "stop", "stoppe"})
_RESUME_WORDS = frozenset({"weiter", "fortsetzen", "fortfahren", "weiterspielen", "weitermachen"})
_NEXT_WORDS = frozenset({"naechstes", "naechster", "naechsten", "naechste", "ueberspringe", "ueberspring", "skip"})
_PREVIOUS_WORDS = frozenset({"vorheriges", "vorheriger", "vorherigen", "vorherige", "letztes", "letzten"})
_TRACK_WORDS = frozenset({"lied", "titel", "song", "track", "stueck"})
_LOUDER = frozenset({"lauter"})
_QUIETER = frozenset({"leiser"})
_VOLUME_WORDS = frozenset({"lautstaerke", "volume"})
_QUERY_WORDS = frozenset({"laeuft", "spielt", "singt", "hoere", "hoeren", "kommt"})
_MEDIA_DOMAIN = "media_player"


@dataclass(frozen=True)
class MediaRequest:
    op: str  # play | pause | resume | next | previous | louder | quieter | volume | off | query
    source_words: tuple[str, ...] = ()  # "Bayern 3" for play
    volume: int | None = None  # percent for "volume"
    text: str = ""


def _words(text: str) -> list[str]:
    folded = normalize_for_compare(text)
    return "".join(char if char.isalnum() else " " for char in folded).split()


def _short(words: list[str], allowed: frozenset[str], extra: frozenset[str] = frozenset()) -> bool:
    """A short command made only of these words, music words, room phrases and fillers."""
    fillers = frozenset({"bitte", "mal", "die", "den", "das", "der", "im", "in", "auf", "am", "dem", "noch", "etwas",
                         "ein", "bisschen", "jetzt", "doch", "musik", "radio", "wieder", "es", "wiedergabe"})
    if len(words) > 7 or not set(words) & allowed:
        return False
    # Before a room phrase ("… im Wohnzimmer") only command words and fillers.
    cut = next((index for index, word in enumerate(words) if word in {"im", "in", "am", "auf"}), len(words))
    return all(word in allowed | extra | fillers | _MUSIC_WORDS for word in words[:cut])


def parse_media_request(text: str) -> MediaRequest | None:
    """A media command or "Was läuft?", else ``None``."""
    words = _words(text)
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
    if words[0] in {"mach", "mache", "schalte", "schalt", "stell", "stelle"} and "musik" in present and "aus" in present:
        return MediaRequest("off", text=text)
    if present & {"musik"} and present & {"aus", "stopp", "stop"} and len(words) <= 4:
        return MediaRequest("off", text=text)
    if words[0] in _RESUME_WORDS and len(words) <= 5 and (len(words) == 1 or present & _MUSIC_WORDS or present & {
        "im", "in", "am", "auf", "spielen"
    }):
        return MediaRequest("resume", text=text)
    if words[0] in {"spiel", "spiele", "spielen", "leg", "lege"} or (
        words[0] == "starte" and present & {"musik", "radio"}
    ) or (
        words[0] in {"mach", "mache"} and present & {"musik"} and "an" in present
    ):
        rest = [word for word in words[1:] if word not in {"bitte", "mal", "doch", "etwas", "auf", "an", "ab", "ein"}]
        cut = next((index for index, word in enumerate(rest) if word in {"im", "in", "am", "auf", "ueber"}), len(rest))
        named = tuple(word for word in rest[:cut] if word not in {"die", "den", "das", "der", "mir", "uns"})
        if not named:
            return None
        if set(named) <= _MUSIC_WORDS | {"etwas", "was", "ein", "bisschen"}:
            return MediaRequest("play", text=text)
        return MediaRequest("play", named, text=text)
    return None


@dataclass(frozen=True)
class MediaResolution:
    plan: ServiceCallPlan | None
    spoken: str  # the success text, or the honest answer / question
    question: bool = False


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


def _room_players(text: str, players: Sequence[EntitySnapshot]) -> tuple[bool, list[EntitySnapshot]]:
    """(a room was named, its players)."""
    words = set(_words(text))
    rooms: dict[str, list[EntitySnapshot]] = {}
    for player in players:
        if player.area_name:
            rooms.setdefault(player.area_name, []).append(player)
            for alias in player.area_aliases:
                rooms.setdefault(alias, []).append(player)
    for room, members in rooms.items():
        room_words = set(_words(room))
        if room_words and room_words <= words:
            return True, members
    return False, []


def _match_source(words: Sequence[str], sources: Sequence[str]) -> str | None:
    wanted = " ".join(words)
    exact = [source for source in sources if " ".join(_words(source)) == wanted]
    if len(exact) == 1:
        return exact[0]
    return None


def _listed(names: Sequence[str]) -> str:
    quoted = [f"„{name}“" for name in names]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " oder " + quoted[-1]


def resolve_media(
    request: MediaRequest,
    entities: Sequence[EntitySnapshot],
    *,
    satellite_area_id: str | None,
) -> MediaResolution:
    """The plan for one media command, or the honest answer/question."""
    players = _players(entities)
    if not players:
        return MediaResolution(None, "Ich finde kein Mediengerät.")
    named = _named_players(request.text, players)
    room_named, in_room = _room_players(request.text, players)
    if named:
        candidates = named
    elif room_named:
        candidates = in_room
        if not candidates:
            return MediaResolution(None, "In diesem Raum finde ich kein Mediengerät.")
    else:
        candidates = []
        if request.op == "play" and request.source_words:
            # "Spiel Bayern 3": the players whose source list has it.
            candidates = [p for p in players if _match_source(request.source_words, _sources(p)) is not None]
        if not candidates and satellite_area_id is not None:
            candidates = [player for player in players if player.area_id == satellite_area_id]
        if not candidates and request.op == "resume":
            # "Weiter" after "Pause": the paused player continues.
            candidates = [player for player in players if player.state == "paused"]
        if not candidates:
            candidates = [player for player in players if player.state == "playing"]
            if not candidates and request.op in {"pause", "next", "previous", "louder", "quieter", "off", "volume"}:
                return MediaResolution(None, "Gerade spielt nichts.")
    if len(candidates) > 1:
        playing = [player for player in candidates if player.state == "playing"]
        if len(playing) == 1 and request.op != "play":
            candidates = playing
    if len(candidates) != 1:
        options = candidates or players
        return MediaResolution(
            None, f"Welches Gerät meinst du: {_listed([player.friendly_name for player in options])}?", question=True
        )
    player = candidates[0]
    name = player.friendly_name
    if request.op == "pause":
        return MediaResolution(ServiceCallPlan(_MEDIA_DOMAIN, "media_pause", player.entity_id), f"{name} pausiert.")
    if request.op == "off":
        # "Musik aus" switches the player off, like every "aus" command
        # before 7.9.3 (dev benchmark 7.7, line 235); "Pause" pauses.
        return MediaResolution(ServiceCallPlan(_MEDIA_DOMAIN, "turn_off", player.entity_id), f"{name} ausgeschaltet.")
    if request.op == "resume":
        return MediaResolution(ServiceCallPlan(_MEDIA_DOMAIN, "media_play", player.entity_id), f"{name} spielt weiter.")
    if request.op == "next":
        return MediaResolution(
            ServiceCallPlan(_MEDIA_DOMAIN, "media_next_track", player.entity_id), f"{name}: nächster Titel."
        )
    if request.op == "previous":
        return MediaResolution(
            ServiceCallPlan(_MEDIA_DOMAIN, "media_previous_track", player.entity_id), f"{name}: vorheriger Titel."
        )
    if request.op == "louder":
        return MediaResolution(ServiceCallPlan(_MEDIA_DOMAIN, "volume_up", player.entity_id), f"{name} lauter.")
    if request.op == "quieter":
        return MediaResolution(ServiceCallPlan(_MEDIA_DOMAIN, "volume_down", player.entity_id), f"{name} leiser.")
    if request.op == "volume":
        assert request.volume is not None
        return MediaResolution(
            ServiceCallPlan(_MEDIA_DOMAIN, "volume_set", player.entity_id, {"volume_level": request.volume / 100}),
            f"{name}: Lautstärke {request.volume} Prozent.",
        )
    sources = _sources(player)
    if request.source_words:
        source = _match_source(request.source_words, sources)
        if source is None:
            spoken = _as_said(request.text, request.source_words)
            listed = f" Verfügbar: {_listed(sources)}." if sources else ""
            return MediaResolution(None, f"„{spoken}“ finde ich bei {name} nicht.{listed}")
        return MediaResolution(
            ServiceCallPlan(_MEDIA_DOMAIN, "select_source", player.entity_id, {"source": source}),
            f"{name} spielt {source}.",
        )
    last = player.attributes.get("source")
    if player.state in {"paused", "idle", "on", "standby"} and isinstance(last, str) and last:
        return MediaResolution(ServiceCallPlan(_MEDIA_DOMAIN, "media_play", player.entity_id), f"{name} spielt {last}.")
    if player.state == "playing":
        return MediaResolution(None, f"{name} spielt schon {last or ''}".rstrip() + ".")
    if isinstance(last, str) and last in sources:
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
    room_named, in_room = _room_players(request.text, players)
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


def claims_other_target(text: str, entities: Sequence[EntitySnapshot]) -> bool:
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
                if not (present & (_NEXT_WORDS | _PREVIOUS_WORDS)):
                    return True
    return False
