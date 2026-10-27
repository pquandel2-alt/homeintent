"""Complete sentence coverage of a bounded media command (7.13.1).

The media reader (``media.parse_media_request``) selects an operation from
closed lexemes.  Before such a reading may lead to a write, every word of the
sentence must be explained by it: the operation lexemes of exactly that
operation, the media target (a media genus noun or a media player's registry
name), at most one place phrase, the free-text name of a play command, a
volume value, the queue directive, articles and politeness fillers.

What remains is the *residue*.  A coordinating joiner ("und", "dann",
"danach", "außerdem" …) is never explained by a single media reading.  For a
write the media fast path therefore takes the sentence only when the
coverage is complete; anything else goes to the shared multi-command
pipeline, which compiles every clause before the first write and rejects the
whole sentence atomically when one clause is not understood.

The analysis is structural (token spans, place lexicon, device ontology,
registry names, the parser's own lexemes); it knows no sentence template.
Home-Assistant-free and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .entities import EntitySnapshot
from .media import (
    MEDIA_CONTENT_WORDS,
    MEDIA_LIGHT_VERBS,
    MEDIA_OPERATION_WORDS,
    PLAY_NAME_FILLERS,
    PLAY_NAME_STOPS,
    MediaRequest,
    media_words,
    queue_directive_positions,
)
from .nlu.coordination import opens_clause
from .nlu.device_ontology import GENERA
from .nlu.language_frontend import LanguageToken, tokenize_language
from .nlu.self_correction import utterance_fields

__all__ = ("MediaCoverage", "MediaSpan", "analyse_media_coverage")

# Device genera that only media players carry ("Radio", "Fernseher",
# "Lautsprecher"); "Musik" is content, not a device (``MEDIA_CONTENT_WORDS``).
_MEDIA_DEVICE_GENERA = frozenset(
    item.key for item in GENERA
    if item.domains == frozenset({"media_player"}) and item.key != "music"
)
_MEDIA_GENERA = _MEDIA_DEVICE_GENERA | frozenset({"music"})
_ARTICLES = frozenset({"der", "die", "das", "den", "dem", "des"})
# Politeness and discourse fillers that never change a media operation.
_FILLERS = frozenset({
    "bitte", "mal", "eben", "doch", "halt", "ja", "danke", "gleich", "jetzt",
    "gerade", "noch", "etwas", "bisschen", "wieder", "kurz", "schnell",
    "einmal", "mir", "uns", "schon", "bitteschoen", "dort", "da", "hier",
})
# Joiners that open, or may open, a further clause.  None of them belongs to
# a single media reading; inside the free-text name of a play command only
# "und"/"oder" may (a band name), and only where they do not open a clause.
_JOINERS = frozenset({
    "und", "oder", "dann", "danach", "anschliessend", "ausserdem", "sowie",
    "zudem", "aber", "sondern", "ausser", "ebenfalls", "bevor", "nachdem",
    "waehrend", "sowohl", "als", "auch",
})
_NAME_JOINERS = frozenset({"und", "oder"})
_POLITE_MODALS = frozenset({"kannst", "koenntest", "wuerdest", "magst"})


@dataclass(frozen=True)
class MediaSpan:
    """One explained part of the sentence (character offsets)."""

    role: str  # operation, target, place, source, value, queue, filler
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class MediaCoverage:
    """Which words a media reading explains and what remains."""

    operation: str
    spans: tuple[MediaSpan, ...]
    residue: tuple[str, ...]
    coordinations: tuple[str, ...]
    conflicts: tuple[str, ...] = ()

    def of(self, role: str) -> tuple[MediaSpan, ...]:
        return tuple(span for span in self.spans if span.role == role)

    @property
    def target_phrases(self) -> tuple[str, ...]:
        return tuple(span.text for span in self.of("target"))

    @property
    def place_phrases(self) -> tuple[str, ...]:
        return tuple(span.text for span in self.of("place"))

    @property
    def source_phrase(self) -> str | None:
        words = [span.text for span in self.of("source")]
        return " ".join(words) if words else None

    @property
    def complete(self) -> bool:
        """Every semantically relevant word is explained by one reading."""
        return not self.residue and not self.coordinations and not self.conflicts


def _word_tokens(text: str) -> list[LanguageToken]:
    return [token for token in tokenize_language(text) if token.is_word or token.is_number]


def analyse_media_coverage(
    text: str,
    entities: Sequence[EntitySnapshot],
    request: MediaRequest,
) -> MediaCoverage:
    """Explain every word of ``text`` by the media ``request`` or report it."""
    all_tokens = tokenize_language(text)
    tokens = _word_tokens(text)
    words = [token.canonical for token in tokens]
    if words != media_words(text):
        # The tokenizers disagree (unusual punctuation inside a word): the
        # reading cannot be aligned with the sentence, so nothing is proven.
        return MediaCoverage(request.op, (), tuple(words), (), ("alignment",))
    roles: list[str | None] = [None] * len(tokens)
    conflicts: list[str] = []
    coordinations: list[str] = []

    def mark(index: int, role: str) -> None:
        if roles[index] is None:
            roles[index] = role

    def overlapping(start: int, end: int) -> list[int]:
        return [
            index for index, token in enumerate(tokens)
            if token.start < end and start < token.end
        ]

    # Coordination: a joiner that opens a clause, wherever it stands.
    joiner_at = {
        index for index, token in enumerate(tokens)
        if token.canonical in _JOINERS
    }
    clause_openers = {
        index for index in joiner_at
        if opens_clause(all_tokens, all_tokens.index(tokens[index]))
        or tokens[index].canonical not in _NAME_JOINERS
    }

    # A leading polite request shell ("Kannst du …", "Bitte würdest du …")
    # is the utterance's modality, not part of the media operation.
    shell = 1 if words[:1] == ["bitte"] else 0
    if words[shell:shell + 2] and words[shell] in _POLITE_MODALS and words[shell + 1:shell + 2] == ["du"]:
        for index in range(shell + 2):
            mark(index, "filler")

    # Queue directive of a play command.
    queue, _mode = queue_directive_positions(words)
    for index in queue:
        mark(index, "queue")

    # The free-text name of a play command: everything between the verb and
    # the place/player, validated later by exact source, alias or library
    # match - never fuzzily.
    if request.op == "play" and request.source_words:
        for index in range(1, len(words)):
            if words[index] in PLAY_NAME_STOPS:
                break
            if index in queue:
                continue
            if index in joiner_at and index in clause_openers:
                break
            mark(index, "filler" if words[index] in PLAY_NAME_FILLERS else "source")

    # Registry names, places and genus nouns from the shared field reader.
    fields, _rest = utterance_fields(text, list(entities))
    targets = 0
    places = 0
    for item in fields:
        positions = overlapping(item.start, item.end)
        if positions and all(roles[index] == "source" for index in positions):
            continue  # part of the free-text name ("Spiel Radio Bob")
        if item.kind == "place":
            places += 1
            for index in positions:
                mark(index, "place")
        elif item.kind == "target":
            media_registry = bool(item.registry_ids) and all(
                entity_id.startswith("media_player.") for entity_id in item.registry_ids
            )
            media_genus = bool(item.genera) and set(item.genera) <= _MEDIA_GENERA
            if not (media_registry or media_genus):
                continue
            if media_registry or set(item.genera) & _MEDIA_DEVICE_GENERA:
                targets += 1
            for index in positions:
                mark(index, "target")
        elif item.kind == "value" and request.op == "volume":
            for index in positions:
                mark(index, "value")
    if places > 1:
        conflicts.append("place")
    if targets > 1:
        conflicts.append("target")

    # A preposition directly before the media target ("auf dem Küchenradio",
    # "über Wohnzimmer Lautsprecher") belongs to that target phrase.
    for index in range(len(words) - 1, -1, -1):
        if roles[index] is not None or words[index] not in PLAY_NAME_STOPS:
            continue
        following = index + 1
        while following < len(words) and roles[following] is None and words[following] in _ARTICLES:
            following += 1
        if following < len(words) and roles[following] == "target":
            for position in range(index, following):
                mark(position, "target")

    allowed = MEDIA_OPERATION_WORDS.get(request.op, frozenset()) | MEDIA_LIGHT_VERBS
    for index, word in enumerate(words):
        if roles[index] is not None:
            continue
        if word in allowed:
            mark(index, "operation")
        elif word in MEDIA_CONTENT_WORDS:
            mark(index, "target")
        elif word in _ARTICLES or word in _FILLERS:
            mark(index, "filler")
        elif word == "ein" and index + 1 < len(words) and words[index + 1] == "bisschen":
            mark(index, "filler")
        elif request.op == "volume" and tokens[index].is_number:
            mark(index, "value")

    residue: list[str] = []
    for index, role in enumerate(roles):
        if role is not None:
            continue
        if index in joiner_at:
            coordinations.append(tokens[index].text)
        else:
            residue.append(tokens[index].text)
    spans = tuple(
        MediaSpan(role, tokens[index].text, tokens[index].start, tokens[index].end)
        for index, role in enumerate(roles) if role is not None
    )
    return MediaCoverage(
        request.op, spans, tuple(residue), tuple(coordinations), tuple(conflicts)
    )
