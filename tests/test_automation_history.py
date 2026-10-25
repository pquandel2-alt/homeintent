import json

import pytest

from homeintent.automation_history import (
    AutomationHistoryStore,
    HISTORY_SCHEMA_VERSION,
    canonical_state_digest,
    validate_history_delta,
)


def test_history_is_bounded_and_pop_restores_latest(tmp_path):
    store = AutomationHistoryStore(str(tmp_path / "history.json"), limit=2)
    for index in range(3):
        store.append(operation="edit", automation_id=str(index), created_at="now", automations=[{"id": str(index)}], metadata={str(index): {"version": 1}})
    assert [item["automation_id"] for item in store.read()] == ["1", "2"]
    assert store.pop()["automation_id"] == "2"
    assert json.loads((tmp_path / "history.json").read_text())[0]["automation_id"] == "1"


def test_schema_three_stores_only_target_delta_without_full_snapshots(tmp_path):
    store = AutomationHistoryStore(str(tmp_path / "history.json"))
    before = [
        {"id": "foreign", "alias": "Nicht kopieren"},
        {"id": "target", "alias": "Vorher"},
    ]
    after = [
        {"id": "foreign", "alias": "Nicht kopieren"},
        {"id": "target", "alias": "Nachher"},
    ]
    store.append(
        operation="edit",
        automation_id="target",
        created_at="2026-10-24T00:00:00+00:00",
        automations=before,
        after_automations=after,
        metadata={
            "foreign": {"lifecycle_token": "must-not-be-copied"},
            "target": {"version": 1},
        },
        owner_user_id="owner",
    )
    store.finalize(
        "2026-10-24T00:00:00+00:00",
        post_metadata_digest="unused-by-schema-3",
        after_metadata={"version": 2},
        after_metadata_by_id={"target": {"version": 2}},
    )

    entry = store.peek()
    assert entry is not None
    assert entry["schema_version"] == HISTORY_SCHEMA_VERSION == 3
    assert "automations" not in entry
    assert "metadata" not in entry
    assert "post_write_digest" not in entry
    assert "post_metadata_digest" not in entry
    assert entry["before_automation"] == before[1]
    assert entry["after_automation"] == after[1]
    assert "must-not-be-copied" not in json.dumps(entry)


def test_large_automation_file_is_not_duplicated_in_bounded_history(tmp_path):
    store = AutomationHistoryStore(str(tmp_path / "history.json"), limit=10)
    foreign = [
        {"id": f"foreign-{index}", "alias": "x" * 200}
        for index in range(2_000)
    ]
    before = [*foreign, {"id": "target", "alias": "Vorher"}]
    after = [*foreign, {"id": "target", "alias": "Nachher"}]
    for index in range(10):
        created_at = f"2026-10-24T00:00:{index:02d}+00:00"
        store.append(
            operation="edit",
            automation_id="target",
            created_at=created_at,
            automations=before,
            after_automations=after,
            metadata={"target": {"version": index}},
        )
        store.finalize(
            created_at,
            post_metadata_digest="unused",
            after_metadata={"version": index + 1},
            after_metadata_by_id={"target": {"version": index + 1}},
        )

    serialized = (tmp_path / "history.json").read_text(encoding="utf-8")
    assert "foreign-0" not in serialized
    assert "automations\"" not in serialized
    assert len(serialized) < 50_000


@pytest.mark.parametrize("position", [-1, True, "0", 1.5])
def test_delta_validator_rejects_corrupt_positions(position):
    before = {"id": "target", "alias": "Vorher"}
    after = {"id": "target", "alias": "Nachher"}
    metadata = {"automation_id": "target", "version": 1}
    delta = {
        "automation_id": "target",
        "before_automation": before,
        "after_automation": after,
        "before_automation_digest": canonical_state_digest(before),
        "after_automation_digest": canonical_state_digest(after),
        "before_metadata": metadata,
        "after_metadata": metadata,
        "before_metadata_digest": canonical_state_digest(metadata),
        "after_metadata_digest": canonical_state_digest(metadata),
        "before_position": position,
        "after_position": 0,
    }

    with pytest.raises(ValueError, match="Positionen"):
        validate_history_delta(delta)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("before_automation", "not-an-object", "Objekttypen"),
        ("after_metadata", ["not-an-object"], "Objekttypen"),
        ("before_position", None, "widersprüchliche Positionen"),
    ],
)
def test_delta_validator_rejects_corrupt_object_shapes(field, value, message):
    before = {"id": "target", "alias": "Vorher"}
    after = {"id": "target", "alias": "Nachher"}
    metadata = {"automation_id": "target", "version": 1}
    delta = {
        "automation_id": "target",
        "before_automation": before,
        "after_automation": after,
        "before_metadata": metadata,
        "after_metadata": metadata,
        "before_position": 0,
        "after_position": 0,
    }
    delta[field] = value
    delta.update(
        {
            "before_automation_digest": canonical_state_digest(
                delta["before_automation"]
            ),
            "after_automation_digest": canonical_state_digest(
                delta["after_automation"]
            ),
            "before_metadata_digest": canonical_state_digest(
                delta["before_metadata"]
            ),
            "after_metadata_digest": canonical_state_digest(
                delta["after_metadata"]
            ),
        }
    )

    with pytest.raises(ValueError, match=message):
        validate_history_delta(delta)
