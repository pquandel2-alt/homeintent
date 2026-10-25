"""Small bounded version history for recoverable automation mutations."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from typing import Any, cast


def _write_atomic(path: str, content: str) -> None:
    directory = os.path.dirname(path) or "."
    descriptor, temporary = tempfile.mkstemp(prefix=".homeintent_history_", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


AUTOMATION_HISTORY_FILENAME = ".storage/homeintent_automation_history.json"
LEGACY_AUTOMATION_HISTORY_FILENAME = ".storage/ha_nlu_automation_history.json"
HISTORY_SCHEMA_VERSION = 3


def canonical_state_digest(value: object) -> str:
    """Digest one automation/metadata value, independent of file formatting."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_history_delta(delta: dict[str, Any]) -> dict[str, Any]:
    """Validate one target delta, including every field used during restore."""
    required = {
        "automation_id", "before_automation_digest", "after_automation_digest",
        "before_metadata_digest", "after_metadata_digest",
        "before_position", "after_position",
    }
    if (
        not required <= delta.keys()
        or not isinstance(delta.get("automation_id"), str)
        or not delta["automation_id"]
    ):
        raise ValueError("Der Delta-Verlaufseintrag ist unvollständig")
    if any(
        value is not None and not isinstance(value, dict)
        for value in (
            delta.get("before_automation"),
            delta.get("after_automation"),
            delta.get("before_metadata"),
            delta.get("after_metadata"),
        )
    ):
        raise ValueError("Der Delta-Verlaufseintrag besitzt ungültige Objekttypen")
    if not all(
        isinstance(delta.get(name), str)
        for name in (
            "before_automation_digest", "after_automation_digest",
            "before_metadata_digest", "after_metadata_digest",
        )
    ):
        raise ValueError("Der Delta-Verlaufseintrag besitzt ungültige Prüfsummen")
    if any(
        value is not None
        and (not isinstance(value, int) or isinstance(value, bool) or value < 0)
        for value in (delta.get("before_position"), delta.get("after_position"))
    ):
        raise ValueError("Der Delta-Verlaufseintrag besitzt ungültige Positionen")
    for prefix in ("before", "after"):
        if (delta.get(f"{prefix}_automation") is None) != (
            delta.get(f"{prefix}_position") is None
        ):
            raise ValueError(
                "Der Delta-Verlaufseintrag besitzt widersprüchliche Positionen"
            )
    if canonical_state_digest(delta.get("before_automation")) != delta[
        "before_automation_digest"
    ] or canonical_state_digest(delta.get("after_automation")) != delta[
        "after_automation_digest"
    ]:
        raise ValueError("Der Automationszustand im Verlauf ist beschädigt")
    if canonical_state_digest(delta.get("before_metadata")) != delta[
        "before_metadata_digest"
    ] or canonical_state_digest(delta.get("after_metadata")) != delta[
        "after_metadata_digest"
    ]:
        raise ValueError("Der Metadatenzustand im Verlauf ist beschädigt")
    return dict(delta)


def _automation_state(
    automations: list[dict[str, Any]], automation_id: str
) -> tuple[dict[str, Any] | None, int | None]:
    matches = [
        (index, item)
        for index, item in enumerate(automations)
        if item.get("id") == automation_id
    ]
    if len(matches) > 1:
        raise ValueError("Die Automations-ID ist in automations.yaml nicht eindeutig")
    if not matches:
        return None, None
    index, item = matches[0]
    return item, index


class AutomationHistoryStore:
    def __init__(self, path: str, limit: int = 10) -> None:
        self._path = path
        self._limit = limit

    @property
    def available(self) -> bool:
        return os.path.isdir(os.path.dirname(self._path) or ".")

    def read(self) -> list[dict[str, Any]]:
        try:
            with open(self._path, encoding="utf-8") as handle:
                value = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []
        return cast(list[dict[str, Any]], value) if isinstance(value, list) else []

    def append(
        self,
        *,
        operation: str,
        automation_id: str,
        created_at: str,
        automations: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        post_write_digest: str | None = None,
        post_metadata_digest: str | None = None,
        owner_user_id: str | None = None,
        after_automations: list[dict[str, Any]] | None = None,
        target_ids: tuple[str, ...] | None = None,
        target_roles: dict[str, str] | None = None,
    ) -> None:
        entries = self.read()
        ids = target_ids or (automation_id,)
        if not ids or len(ids) != len(set(ids)) or automation_id not in ids:
            raise ValueError("Der Delta-Verlauf enthält widersprüchliche Ziel-IDs")
        entry: dict[str, Any] = {
            "operation": operation,
            "automation_id": automation_id,
            "created_at": created_at,
            "owner_user_id": owner_user_id,
        }
        if after_automations is not None:
            entry["schema_version"] = HISTORY_SCHEMA_VERSION
            targets: list[dict[str, Any]] = []
            for target_id in ids:
                before_automation, before_position = _automation_state(
                    automations, target_id
                )
                after_automation, after_position = _automation_state(
                    after_automations, target_id
                )
                before_metadata = (metadata or {}).get(target_id)
                targets.append(
                    {
                        "automation_id": target_id,
                        "role": (target_roles or {}).get(target_id, "target"),
                        "before_automation": before_automation,
                        "after_automation": after_automation,
                        "before_automation_digest": canonical_state_digest(
                            before_automation
                        ),
                        "after_automation_digest": canonical_state_digest(
                            after_automation
                        ),
                        "before_metadata": before_metadata,
                        "before_metadata_digest": canonical_state_digest(
                            before_metadata
                        ),
                        "after_metadata": None,
                        "after_metadata_digest": None,
                        "before_position": before_position,
                        "after_position": after_position,
                    }
                )
            if len(targets) == 1:
                # Keep the compact, documented single-target surface flat.
                entry.update(targets[0])
                entry.pop("role", None)
            else:
                entry["targets"] = targets
        else:
            # Compatibility-only writer for callers constructing a legacy
            # entry. Productive transactions always provide the after state
            # and therefore never serialize these full snapshots.
            entry.update(
                {
                    "automations": automations,
                    "metadata": metadata or {},
                    "post_write_digest": post_write_digest,
                    "post_metadata_digest": post_metadata_digest,
                }
            )
        entries.append(entry)
        _write_atomic(
            self._path,
            json.dumps(
                entries[-self._limit :],
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
        )

    def finalize(
        self,
        created_at: str,
        *,
        post_metadata_digest: str,
        after_metadata: dict[str, Any] | None = None,
        after_metadata_by_id: dict[str, dict[str, Any] | None] | None = None,
    ) -> None:
        """Complete the newest prepared entry after all side effects commit."""
        entries = self.read()
        if not entries or entries[-1].get("created_at") != created_at:
            raise RuntimeError("Der Automationsverlauf hat sich unerwartet geändert")
        if entries[-1].get("schema_version") == HISTORY_SCHEMA_VERSION:
            raw_targets = entries[-1].get("targets")
            if isinstance(raw_targets, list):
                if after_metadata_by_id is None:
                    raise ValueError("Für ein Mehrziel-Delta fehlen Metadaten")
                targets = cast(list[object], raw_targets)
                for target in targets:
                    if not isinstance(target, dict):
                        raise ValueError("Der Mehrziel-Verlauf ist beschädigt")
                    typed_target = cast(dict[str, Any], target)
                    target_id = typed_target.get("automation_id")
                    if not isinstance(target_id, str) or target_id not in after_metadata_by_id:
                        raise ValueError("Für ein Mehrziel-Delta fehlen Zielmetadaten")
                    value = after_metadata_by_id[target_id]
                    typed_target["after_metadata"] = value
                    typed_target["after_metadata_digest"] = canonical_state_digest(value)
            else:
                entries[-1]["after_metadata"] = after_metadata
                entries[-1]["after_metadata_digest"] = canonical_state_digest(
                    after_metadata
                )
        elif entries[-1].get("schema_version") == 2:
            # A transaction prepared by an older running version may finish
            # during an upgrade. Preserve its legacy verification fields.
            entries[-1]["post_metadata_digest"] = post_metadata_digest
            entries[-1]["after_metadata"] = after_metadata
            entries[-1]["after_metadata_digest"] = canonical_state_digest(
                after_metadata
            )
        _write_atomic(
            self._path,
            json.dumps(
                entries[-self._limit :],
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
        )

    def pop(self) -> dict[str, Any] | None:
        entries = self.read()
        if not entries:
            return None
        result = entries.pop()
        _write_atomic(
            self._path, json.dumps(entries, ensure_ascii=False, indent=2, default=str)
        )
        return result

    def peek(self) -> dict[str, Any] | None:
        entries = self.read()
        return entries[-1] if entries else None

    def pop_if_created_at(self, created_at: str) -> None:
        latest = self.peek()
        if latest is not None and latest.get("created_at") == created_at:
            self.pop()
