"""Pure validation and planning for target-scoped automation undo.

This module has no Home Assistant or filesystem dependency.  It turns one
history entry plus current YAML/sidecar values into a complete atomic restore
plan, or raises before any writer is invoked.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence, cast

from .automation_history import (
    HISTORY_SCHEMA_VERSION,
    canonical_state_digest,
    validate_history_delta,
)


@dataclass(frozen=True)
class AutomationUndoPlan:
    """All checked values needed by the transactional I/O boundary."""

    automation_id: str
    deltas: tuple[dict[str, Any], ...]
    updated_automations: list[dict[str, Any]]
    before_metadata: dict[str, dict[str, Any] | None]
    after_metadata: dict[str, dict[str, Any] | None]
    after_metadata_digests: dict[str, str]
    restored_metadata_digests: dict[str, str]


def automation_by_id(
    automations: Sequence[dict[str, Any]], automation_id: str
) -> tuple[dict[str, Any] | None, int | None]:
    """Return one exact ID match and reject duplicate YAML identities."""
    matches = [
        (index, item)
        for index, item in enumerate(automations)
        if item.get("id") == automation_id
    ]
    if len(matches) > 1:
        raise ValueError("Die Zielautomation ist nicht eindeutig")
    if not matches:
        return None, None
    position, automation = matches[0]
    return automation, position


def _is_composite_lifecycle_history(
    history: Mapping[str, Any], delta: Mapping[str, Any]
) -> bool:
    if history.get("operation") == "resume_pause":
        return True
    return any(
        isinstance(delta.get(name), dict)
        and (
            delta[name].get("pause_id") is not None
            or delta[name].get("pause_target_automation_id") is not None
        )
        for name in ("before_metadata", "after_metadata")
    )


def history_deltas(
    history: dict[str, Any],
    current_automations: list[dict[str, Any]],
    current_yaml_digest: str,
    current_metadata: dict[str, Any],
    current_full_metadata_digest: str,
) -> tuple[dict[str, Any], ...]:
    """Validate v3 deltas or derive one fully guarded legacy delta."""
    automation_id = history.get("automation_id")
    if not isinstance(automation_id, str) or not automation_id:
        raise ValueError("Der letzte Verlaufseintrag ist beschädigt")
    if history.get("schema_version") == HISTORY_SCHEMA_VERSION:
        raw_targets = history.get("targets")
        if raw_targets is None:
            return (validate_history_delta(history),)
        if not isinstance(raw_targets, list) or not raw_targets:
            raise ValueError("Der Mehrziel-Verlaufseintrag ist beschädigt")
        raw_target_items = cast(list[object], raw_targets)
        targets = tuple(
            validate_history_delta(cast(dict[str, Any], item))
            for item in raw_target_items
            if isinstance(item, dict)
        )
        ids = tuple(item["automation_id"] for item in targets)
        if (
            len(targets) != len(raw_target_items)
            or len(ids) != len(set(ids))
            or automation_id not in ids
        ):
            raise ValueError(
                "Der Mehrziel-Verlauf enthält widersprüchliche Ziel-IDs"
            )
        return targets

    if history.get("schema_version") == 2:
        # The redundant full snapshots are deliberately ignored and can never
        # overwrite unrelated current objects.
        return (validate_history_delta(history),)

    post_write_digest = history.get("post_write_digest")
    post_metadata_digest = history.get("post_metadata_digest")
    if not isinstance(post_write_digest, str) or not isinstance(
        post_metadata_digest, str
    ):
        raise ValueError(
            "Dieser ältere Verlaufseintrag besitzt keine sicheren Zustandsprüfsummen"
        )
    if current_yaml_digest != post_write_digest:
        raise ValueError(
            "Die Automationen wurden inzwischen geändert; Rückgängig wurde sicher verweigert"
        )
    if current_full_metadata_digest != post_metadata_digest:
        raise ValueError(
            "Die HomeIntent-Metadaten wurden inzwischen geändert; "
            "Rückgängig wurde sicher verweigert"
        )
    previous = history.get("automations")
    previous_metadata = history.get("metadata")
    if not isinstance(previous, list) or not isinstance(previous_metadata, dict):
        raise ValueError("Der ältere Verlaufseintrag ist beschädigt")
    previous_items = cast(list[object], previous)
    if any(not isinstance(item, dict) for item in previous_items):
        raise ValueError("Der ältere Automationsbestand ist beschädigt")
    typed_previous = cast(list[dict[str, Any]], previous_items)
    typed_previous_metadata = cast(dict[str, Any], previous_metadata)
    before_automation, before_position = automation_by_id(
        typed_previous, automation_id
    )
    after_automation, after_position = automation_by_id(
        current_automations, automation_id
    )
    before_metadata_value = typed_previous_metadata.get(automation_id)
    current_entry_value = current_metadata.get(automation_id)
    if before_metadata_value is not None and not isinstance(
        before_metadata_value, dict
    ):
        raise ValueError("Der ältere Metadatenbestand ist beschädigt")
    if automation_id in current_metadata and not isinstance(
        current_entry_value, dict
    ):
        raise ValueError("Der ältere Metadatenbestand ist beschädigt")
    before_metadata = (
        cast(dict[str, Any], before_metadata_value)
        if isinstance(before_metadata_value, dict)
        else None
    )
    current_entry = (
        cast(dict[str, Any], current_entry_value)
        if isinstance(current_entry_value, dict)
        else None
    )
    return (
        validate_history_delta(
            {
                **history,
                "schema_version": 2,
                "before_automation": before_automation,
                "after_automation": after_automation,
                "before_automation_digest": canonical_state_digest(
                    before_automation
                ),
                "after_automation_digest": canonical_state_digest(after_automation),
                "before_metadata": before_metadata,
                "after_metadata": current_entry,
                "before_metadata_digest": canonical_state_digest(before_metadata),
                "after_metadata_digest": canonical_state_digest(current_entry),
                "before_position": before_position,
                "after_position": after_position,
            }
        ),
    )


def prepare_undo_plan(
    history: dict[str, Any],
    current_automations: list[dict[str, Any]],
    current_yaml_digest: str,
    current_metadata: dict[str, Any],
    current_full_metadata_digest: str,
) -> AutomationUndoPlan:
    """Check every target and construct an all-or-nothing restore plan."""
    automation_id = history.get("automation_id")
    if not isinstance(automation_id, str) or not automation_id:
        raise ValueError("Der letzte Verlaufseintrag ist beschädigt")
    deltas = history_deltas(
        history,
        current_automations,
        current_yaml_digest,
        current_metadata,
        current_full_metadata_digest,
    )
    if history.get("schema_version") != HISTORY_SCHEMA_VERSION and any(
        _is_composite_lifecycle_history(history, delta) for delta in deltas
    ):
        raise ValueError(
            "Dieser ältere Pause-/Fortsetzen-Eintrag enthält kein vollständiges "
            "Mehrziel-Delta. Er bleibt unverändert. Ein Administrator kann nach "
            "Prüfung sagen: Verwirf den letzten nicht rückgängig machbaren "
            "Verlaufseintrag."
        )

    current_positions: dict[str, int | None] = {}
    before_metadata: dict[str, dict[str, Any] | None] = {}
    after_metadata: dict[str, dict[str, Any] | None] = {}
    after_metadata_digests: dict[str, str] = {}
    restored_metadata_digests: dict[str, str] = {}
    for delta in deltas:
        target_id = str(delta["automation_id"])
        current_automation, current_position = automation_by_id(
            current_automations, target_id
        )
        current_positions[target_id] = current_position
        if canonical_state_digest(current_automation) != delta[
            "after_automation_digest"
        ]:
            raise ValueError(
                f"Die Zielautomation ({delta.get('role', 'target')}) wurde inzwischen "
                "geändert; Rückgängig wurde sicher verweigert"
            )
        metadata_value = current_metadata.get(target_id)
        if target_id in current_metadata and not isinstance(metadata_value, dict):
            raise ValueError("Der Metadatenbestand ist beschädigt")
        current_entry = (
            cast(dict[str, Any], metadata_value)
            if isinstance(metadata_value, dict)
            else None
        )
        current_entry_digest = canonical_state_digest(current_entry)
        if current_entry_digest != delta["after_metadata_digest"]:
            raise ValueError(
                "Die HomeIntent-Metadaten wurden inzwischen geändert "
                f"({delta.get('role', 'target')}); Rückgängig wurde sicher verweigert"
            )
        before = cast(dict[str, Any] | None, delta["before_metadata"])
        before_metadata[target_id] = copy.deepcopy(before)
        after_metadata[target_id] = copy.deepcopy(current_entry)
        after_metadata_digests[target_id] = current_entry_digest
        restored_metadata_digests[target_id] = str(
            delta["before_metadata_digest"]
        )

    updated = list(current_automations)
    for position in sorted(
        (value for value in current_positions.values() if value is not None),
        reverse=True,
    ):
        updated.pop(position)
    insertions: list[tuple[int, dict[str, Any]]] = []
    for delta in deltas:
        before_automation = delta["before_automation"]
        before_position = delta["before_position"]
        if isinstance(before_automation, dict) and isinstance(before_position, int):
            insertions.append(
                (before_position, cast(dict[str, Any], before_automation))
            )
    insertions.sort(key=lambda item: item[0])
    for position, before_automation in insertions:
        updated.insert(min(position, len(updated)), copy.deepcopy(before_automation))

    return AutomationUndoPlan(
        automation_id=automation_id,
        deltas=deltas,
        updated_automations=updated,
        before_metadata=before_metadata,
        after_metadata=after_metadata,
        after_metadata_digests=after_metadata_digests,
        restored_metadata_digests=restored_metadata_digests,
    )
