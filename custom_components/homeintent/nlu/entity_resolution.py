"""Canonical HA-free entity resolution for semantic language consumers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..entities import (
    EntitySnapshot,
    EntityIndex,
    ResolutionResult,
    ResolutionStatus,
    ResolveResult,
    ResolveStatus,
    normalize_for_compare,
    resolve_entities_by_domain,
    resolve_entity,
    generate_aliases,
)
from ..entity_scope import resolve_entity_scope
from .semantic_catalog import DOMAIN_WORDS
from .target_resolution import resolve_phrase

__all__ = (
    "exact_registry_name",
    "registry_name_hits",
    "ResolutionResult", "ResolutionStatus", "ResolveResult", "ResolveStatus",
    "all_mentioned_entities", "mentioned_entities", "resolve_entities_by_domain", "resolve_entity",
    "resolve_phrase", "resolve_mentioned_target", "resolve_named_target",
    "resolve_query_targets", "rank_semantic_targets", "RankedTarget",
)


@dataclass(frozen=True)
class RankedTarget:
    """One explainable candidate from the shared semantic name resolver."""

    entity: EntitySnapshot
    score: int
    source: str
    matched_name: str


_WORD_RE = re.compile(r"[\wäöüß]+", re.I)


def _word_set(text: str, ignored: frozenset[str]) -> set[str]:
    return {
        token
        for raw in _WORD_RE.findall(text)
        if (token := normalize_for_compare(raw)) not in ignored
    }


def _indexed_exact_targets(
    text: str,
    index: EntityIndex,
    domains: frozenset[str],
    area_id: str | None,
    floor_id: str | None,
) -> tuple[RankedTarget, ...]:
    """Resolve embedded exact names through bounded utterance n-grams."""
    words = tuple(normalize_for_compare(word) for word in _WORD_RE.findall(text))
    best_by_id: dict[str, RankedTarget] = {}
    for start in range(len(words)):
        for end in range(start + 1, len(words) + 1):
            phrase = " ".join(words[start:end])
            for entity in index.by_normalized_name.get(phrase, ()):
                if domains and entity.domain not in domains:
                    continue
                if area_id is not None and entity.area_id != area_id:
                    continue
                if floor_id is not None and entity.floor_id != floor_id:
                    continue
                candidate = RankedTarget(
                    entity,
                    1030 + len(phrase),
                    "friendly_name",
                    phrase,
                )
                previous = best_by_id.get(entity.entity_id)
                if previous is None or candidate.score > previous.score:
                    best_by_id[entity.entity_id] = candidate
            for entity in index.by_normalized_alias.get(phrase, ()):
                if domains and entity.domain not in domains:
                    continue
                if area_id is not None and entity.area_id != area_id:
                    continue
                if floor_id is not None and entity.floor_id != floor_id:
                    continue
                configured = any(
                    normalize_for_compare(alias) == phrase for alias in entity.aliases
                )
                source = "configured" if configured else next(
                    (
                        alias.source
                        for alias in generate_aliases(entity)
                        if normalize_for_compare(alias.text) == phrase
                    ),
                    "generated",
                )
                candidate = RankedTarget(
                    entity,
                    1000 + (30 if configured else 0) + len(phrase),
                    source,
                    phrase,
                )
                previous = best_by_id.get(entity.entity_id)
                if previous is None or candidate.score > previous.score:
                    best_by_id[entity.entity_id] = candidate
    return tuple(
        sorted(best_by_id.values(), key=lambda item: (-item.score, item.entity.entity_id))
    )


def rank_semantic_targets(
    text: str,
    entities: Iterable[EntitySnapshot],
    *,
    domains: frozenset[str] = frozenset(),
    area_id: str | None = None,
    floor_id: str | None = None,
    ignored_tokens: frozenset[str] = frozenset(),
    index: EntityIndex | None = None,
) -> tuple[RankedTarget, ...]:
    """Rank embedded registry names once for every semantic consumer.

    Complete friendly/configured names outrank token-subset matches. Generic
    domain words may be omitted from a registry name only after the caller
    supplies the shared ignored-token set. Equal top scores remain equal so
    the caller can produce an ambiguity rather than selecting by iteration
    order.
    """
    normalized_text = normalize_for_compare(text)
    utterance = _word_set(text, ignored_tokens)
    if index is not None:
        indexed = _indexed_exact_targets(
            text, index, domains, area_id, floor_id
        )
        if indexed:
            return indexed
    if index is not None and len(domains) == 1:
        pool: Iterable[EntitySnapshot] = index.by_domain.get(next(iter(domains)), ())
    else:
        pool = entities
    ranked: list[RankedTarget] = []
    for entity in pool:
        if domains and entity.domain not in domains:
            continue
        if area_id is not None and entity.area_id != area_id:
            continue
        if floor_id is not None and entity.floor_id != floor_id:
            continue
        best: RankedTarget | None = None
        names = (
            ((entity.friendly_name, "friendly_name"),)
            + tuple((alias, "configured") for alias in entity.aliases)
            + tuple((alias.text, alias.source) for alias in generate_aliases(entity))
        )
        for name, source in names:
            normalized_name = normalize_for_compare(name)
            score = 0
            if normalized_name and re.search(
                rf"(?<!\w){re.escape(normalized_name)}(?!\w)", normalized_text
            ):
                source_bonus = 30 if source in {"friendly_name", "configured"} else 0
                score = 1000 + source_bonus + len(normalized_name)
            else:
                name_tokens = _word_set(name, frozenset())
                generic = {
                    normalize_for_compare(word)
                    for words in DOMAIN_WORDS.values()
                    for word in words
                }
                distinctive = name_tokens - generic
                required = (
                    name_tokens
                    if source in {"entity_name", "entity_id"}
                    else distinctive or name_tokens
                )
                if required and required <= utterance:
                    score = len(required) * 10 + len(name_tokens)
            if score and (best is None or score > best.score):
                best = RankedTarget(entity, score, source, name)
        if best is not None:
            ranked.append(best)
    if not ranked:
        generic = {
            normalize_for_compare(word)
            for words in DOMAIN_WORDS.values()
            for word in words
        }
        residual = " ".join(
            token
            for raw in _WORD_RE.findall(text)
            if (token := normalize_for_compare(raw)) not in ignored_tokens
            and token not in generic
        )
        if residual:
            scoped_pool = [
                entity
                for entity in pool
                if (not domains or entity.domain in domains)
                and (area_id is None or entity.area_id == area_id)
                and (floor_id is None or entity.floor_id == floor_id)
            ]
            resolved = resolve_phrase(
                residual,
                scoped_pool,
                area_id=area_id,
                domain=(next(iter(domains)) if len(domains) == 1 else None),
            )
            if resolved.candidate_set is not None:
                ranked.extend(
                    RankedTarget(
                        candidate.entity,
                        int(candidate.score),
                        candidate.source.name.casefold(),
                        candidate.matched_name,
                    )
                    for candidate in resolved.candidate_set.ranked
                )
    return tuple(sorted(ranked, key=lambda item: (-item.score, item.entity.entity_id)))


def _rank_mentions(
    text: str, entities: list[EntitySnapshot]
) -> list[tuple[int, EntitySnapshot]]:
    """Rank explicit registry-name and alias mentions once for all consumers."""
    normalized = normalize_for_compare(text)

    def contains_bounded(key: str) -> bool:
        """Substring lookup with regex-equivalent word boundaries.

        Compiling one escaped regex for every entity made read-only queries
        hundreds of milliseconds slower on 5,000-entity registries.  A
        literal scan preserves the same boundary rule without per-name regex
        construction.
        """
        start = normalized.find(key)
        while start >= 0:
            end = start + len(key)
            left_ok = start == 0 or not (
                normalized[start - 1].isalnum() or normalized[start - 1] == "_"
            )
            right_ok = end == len(normalized) or not (
                normalized[end].isalnum() or normalized[end] == "_"
            )
            if left_ok and right_ok:
                return True
            start = normalized.find(key, start + 1)
        return False

    found: list[tuple[int, EntitySnapshot]] = []
    for entity in entities:
        score = max(
            (
                len(key)
                for name in (entity.friendly_name, *entity.aliases)
                if (key := normalize_for_compare(name))
                and contains_bounded(key)
            ),
            default=0,
        )
        if score:
            found.append((score, entity))
    return found


def all_mentioned_entities(
    text: str,
    entities: list[EntitySnapshot],
    *,
    index: EntityIndex | None = None,
) -> tuple[EntitySnapshot, ...]:
    """Return every explicitly named entity, longest names first."""
    if index is not None:
        indexed = _indexed_exact_targets(text, index, frozenset(), None, None)
        if indexed:
            return tuple(candidate.entity for candidate in indexed)
    found = _rank_mentions(text, entities)
    found.sort(key=lambda item: (-item[0], item[1].entity_id))
    return tuple(entity for _, entity in found)


def mentioned_entities(
    text: str,
    entities: list[EntitySnapshot],
    *,
    index: EntityIndex | None = None,
) -> tuple[EntitySnapshot, ...]:
    """Return unique longest explicit registry-name or alias mentions."""
    if index is not None:
        indexed = _indexed_exact_targets(text, index, frozenset(), None, None)
        if indexed:
            best = max(
                len(normalize_for_compare(candidate.matched_name))
                for candidate in indexed
            )
            return tuple(
                candidate.entity
                for candidate in indexed
                if len(normalize_for_compare(candidate.matched_name)) == best
            )
    found = _rank_mentions(text, entities)
    if not found:
        return ()
    best = max(score for score, _ in found)
    return tuple(entity for score, entity in found if score == best)


def resolve_named_target(
    name: str,
    entities: list[EntitySnapshot],
    allowed_domains: frozenset[str] | None = None,
) -> ResolutionResult:
    """Resolve one complete spoken target through the canonical scorer."""
    candidates = (
        entities
        if allowed_domains is None
        else [entity for entity in entities if entity.domain in allowed_domains]
    )
    return resolve_phrase(name.strip(), candidates)


def resolve_mentioned_target(
    text: str,
    entities: list[EntitySnapshot],
    allowed_domains: frozenset[str],
    *,
    index: EntityIndex | None = None,
) -> ResolutionResult:
    """Resolve one registry name embedded anywhere in a larger utterance."""
    if index is not None:
        ranked = _indexed_exact_targets(
            text, index, allowed_domains, None, None
        )
        if ranked:
            best_score = ranked[0].score
            best = [candidate for candidate in ranked if candidate.score == best_score]
            # An exact complete name outranks names it contains ("Gute
            # Nacht Test" over "Gute Nacht", 7.8 B7).
            longest = max(len(normalize_for_compare(candidate.matched_name)) for candidate in best)
            matches = tuple(
                candidate.entity
                for candidate in best
                if len(normalize_for_compare(candidate.matched_name)) == longest
            )
        else:
            pool = [
                entity
                for domain in allowed_domains
                for entity in index.by_domain.get(domain, ())
            ]
            matches = mentioned_entities(text, pool)
    else:
        matches = mentioned_entities(
            text,
            [entity for entity in entities if entity.domain in allowed_domains],
        )
    if not matches:
        return ResolutionResult(status=ResolutionStatus.NOT_FOUND)
    if len(matches) > 1:
        return ResolutionResult(
            status=ResolutionStatus.AMBIGUOUS, candidates=matches
        )
    return ResolutionResult(
        status=ResolutionStatus.RESOLVED,
        entity=next(iter(matches)),
        score=100,
    )


def resolve_query_targets(
    text: str,
    entities: list[EntitySnapshot],
    allowed_domains: frozenset[str],
) -> tuple[EntitySnapshot, ...]:
    """Compose explicit name, domain, area and floor into concrete targets."""
    mentioned = tuple(
        entity for entity in mentioned_entities(text, entities)
        if entity.domain in allowed_domains
    )
    if mentioned:
        return mentioned

    scope = resolve_entity_scope(text, entities, allowed_domains)
    if scope is not None:
        return tuple(scope.entities)

    normalized = normalize_for_compare(text)
    spoken_domains = {
        domain
        for domain, words in DOMAIN_WORDS.items()
        if domain in allowed_domains
        and any(
            re.search(
                rf"(?<!\w){re.escape(normalize_for_compare(word))}(?!\w)",
                normalized,
            )
            for word in words
        )
    }
    candidates = [entity for entity in entities if entity.domain in spoken_domains]
    spoken_areas = {
        entity.area_id
        for entity in candidates
        if entity.area_id
        and any(
            area
            and re.search(
                rf"(?<!\w){re.escape(normalize_for_compare(area))}(?!\w)",
                normalized,
            )
            for area in (entity.area_name, *entity.area_aliases)
        )
    }
    if spoken_areas:
        candidates = [entity for entity in candidates if entity.area_id in spoken_areas]
    if not candidates:
        return ()
    return tuple(candidates)


def registry_name_hits(
    noun_word: str | None, candidates: Sequence[EntitySnapshot]
) -> tuple[EntitySnapshot, ...]:
    """Every room-less candidate whose registry name contains the spoken
    noun as a whole word (7.9.2 A5)."""
    if not noun_word:
        return ()
    word = normalize_for_compare(noun_word.strip("-"))
    return tuple(
        entity for entity in candidates
        if entity.area_id is None
        and any(
            word in normalize_for_compare(name).replace("-", " ").split()
            for name in (entity.friendly_name, *entity.aliases)
        )
    )


def exact_registry_name(
    noun_word: str | None, candidates: Sequence[EntitySnapshot]
) -> tuple[EntitySnapshot, ...]:
    """The one candidate whose registry name contains the spoken noun as a
    whole word and that has no room (7.9.2 A5): "der Stromverbrauch" with
    a sensor "Stromverbrauch Haus" among four power sensors. Several such
    names, or none, change nothing - the question stays."""
    hits = registry_name_hits(noun_word, candidates)
    return hits if len(hits) == 1 else ()
