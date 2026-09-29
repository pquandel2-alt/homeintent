import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeintent.alias_learning import (  # noqa: E402
    append_alias_rule,
    parse_alias_learning,
    remove_alias_rule,
)
from homeintent.bindings import BindingKind, BindingScope  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.learning_policy import LearningPolicy  # noqa: E402
from homeintent.model_registry import ModelRegistry  # noqa: E402
import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402


ENTITIES = [
    EntitySnapshot("light.desk", "Schreibtischlampe", "light", "off"),
    EntitySnapshot("light.ceiling", "Deckenlampe", "light", "off"),
]

AREA_ENTITIES = [
    EntitySnapshot(
        "light.floor", "Stehlampe", "light", "off",
        area_id="living_room", area_name="Wohnzimmer",
    ),
    EntitySnapshot(
        "light.ceiling", "Deckenlampe", "light", "off",
        area_id="living_room", area_name="Wohnzimmer",
    ),
    EntitySnapshot(
        "light.kitchen", "Stehlampe", "light", "off",
        area_id="kitchen", area_name="Küche",
    ),
]


def test_explicit_alias_teaching_creates_a_bounded_draft():
    draft = parse_alias_learning(
        "Mit Bürolicht meine ich Schreibtischlampe.", ENTITIES
    )

    assert draft is not None
    assert draft.alias == "Bürolicht"
    assert draft.entity_id == "light.desk"
    assert append_alias_rule("", draft) == "Bürolicht = light.desk"


def test_alias_rule_is_idempotent_and_conflicts_are_rejected():
    draft = parse_alias_learning(
        "Nenne Schreibtischlampe künftig Bürolicht", ENTITIES
    )
    assert draft is not None
    existing = "Bürolicht = light.desk"
    assert append_alias_rule(existing, draft) == existing

    with pytest.raises(ValueError):
        append_alias_rule(
            "Bürolicht = light.ceiling", draft
        )
    assert remove_alias_rule(
        "# lokal\nBürolicht = light.desk\nDecke = light.ceiling",
        alias="bürolicht", entity_id="light.desk",
    ) == "# lokal\nDecke = light.ceiling"


def test_implicit_or_colliding_alias_learning_is_refused():
    assert parse_alias_learning("Bürolicht Schreibtischlampe", ENTITIES) is None
    assert parse_alias_learning(
        "Mit Deckenlampe meine ich Schreibtischlampe", ENTITIES
    ) is None


def test_contextual_alias_teaching_uses_exact_area_binding():
    draft = parse_alias_learning(
        "Mit Lampe meine ich im Wohnzimmer normalerweise die Stehlampe.",
        AREA_ENTITIES,
    )

    assert draft is not None
    assert draft.alias == "Lampe"
    assert draft.entity_id == "light.floor"
    assert draft.area_id == "living_room"


def test_alias_learning_requires_confirmation_before_persistence(monkeypatch):
    entry = ConfigEntry(options={})
    agent = NluConversationEntity(entry)
    agent.hass = HomeAssistant()
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda hass, config_entry: ENTITIES
    )
    monkeypatch.setattr(
        ha_conversation, "build_device_snapshots", lambda hass, config_entry: []
    )

    def update_entry(config_entry, *, options):
        config_entry.options = options

    agent.hass.config_entries = SimpleNamespace(async_update_entry=update_entry)

    preview = asyncio.run(agent._async_handle_message(
        ConversationInput(
            text="Mit Bürolicht meine ich Schreibtischlampe",
            conversation_id="alias-dialog",
        ),
        chat_log=None,
    ))
    assert "Soll ich" in preview.response.speech
    assert entry.options == {}

    saved = asyncio.run(agent._async_handle_message(
        ConversationInput(text="ja", conversation_id="alias-dialog"),
        chat_log=None,
    ))
    assert "Gespeichert" in saved.response.speech
    # 7.4.1: one store for everything learned; options stay configuration.
    assert entry.options == {}
    (alias,) = agent._runtime_data.bindings.all(BindingKind.ALIAS)
    assert (alias.data["spoken"], alias.target, alias.scope) == (
        "Bürolicht", "light.desk", BindingScope.HOUSEHOLD
    )


def test_contextual_preference_is_persistent_and_area_scoped(tmp_path):
    entry = ConfigEntry(options={})
    agent = NluConversationEntity(entry)
    agent.hass = HomeAssistant()
    registry = ModelRegistry(tmp_path / "models.json", LearningPolicy())
    agent._runtime_data.learned_models = registry
    draft = parse_alias_learning(
        "Mit Lampe meine ich im Wohnzimmer normalerweise die Stehlampe.",
        AREA_ENTITIES,
    )
    assert draft is not None

    asyncio.run(agent._learning.async_confirm_alias_learning(draft, "philipp"))
    (alias,) = agent._runtime_data.bindings.all(BindingKind.ALIAS)
    assert (alias.target, alias.scope, alias.user_id, alias.data["area_id"]) == (
        "light.floor", BindingScope.USER, "philipp", "living_room"
    )
    assert "custom_aliases" not in entry.options

    living = asyncio.run(agent._learning.async_apply_confirmed_preferences(
        AREA_ENTITIES, area_id="living_room", user_id="philipp"
    ))
    kitchen = asyncio.run(agent._learning.async_apply_confirmed_preferences(
        AREA_ENTITIES, area_id="kitchen", user_id="philipp"
    ))
    assert "Lampe" in next(item for item in living if item.entity_id == "light.floor").aliases
    assert "Lampe" not in next(item for item in kitchen if item.entity_id == "light.floor").aliases
