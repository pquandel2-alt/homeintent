"""The live test house (``sim/``) as stub-suite fixture.

``tests/data/testhaus.json`` is exported from the running test bed by
``sim/export_house_fixture.py``.  ``house_entities()`` rebuilds the same
``EntitySnapshot`` list ``hass_entities.build_entity_snapshots()`` produces
live, so language tests run against a realistic house without Home
Assistant.  ``HouseConversation`` drives the real ``NluConversationEntity``
through ``tests/_ha_stub.py`` and records every service call.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.nlu.capabilities import derive_capabilities  # noqa: E402

FIXTURE = Path(__file__).parent / "data" / "testhaus.json"


@lru_cache(maxsize=1)
def _raw() -> tuple[dict[str, Any], ...]:
    return tuple(json.loads(FIXTURE.read_text(encoding="utf-8")))


def house_entities() -> list[EntitySnapshot]:
    """Every exposed entity of the test house as HomeIntent sees it."""
    snapshots: list[EntitySnapshot] = []
    for item in _raw():
        domain = item["entity_id"].split(".", 1)[0]
        attributes = item["attributes"]
        device_class = attributes.get("device_class")
        snapshots.append(EntitySnapshot(
            entity_id=item["entity_id"],
            friendly_name=attributes.get("friendly_name") or item["entity_id"],
            domain=domain,
            state=item["state"],
            area_id=item["area_id"],
            area_name=item["area_name"],
            floor_id=item["floor_id"],
            floor_name=item["floor_name"],
            floor_level=item["floor_level"],
            unit=attributes.get("unit_of_measurement"),
            device_class=device_class,
            state_class=attributes.get("state_class"),
            aliases=tuple(item["aliases"]),
            area_aliases=tuple(item["area_aliases"]),
            floor_aliases=tuple(item.get("floor_aliases") or ()),
            attributes=attributes,
            capabilities=frozenset(
                capability.name
                for capability in derive_capabilities(domain, device_class, attributes)
            ),
        ))
    return snapshots


def with_states(
    entities: list[EntitySnapshot], **states: str
) -> list[EntitySnapshot]:
    """Return a copy with ``entity_id__with__underscores=state`` overrides.

    Keyword names use ``__`` for the entity-id dot: ``light__stehlampe="on"``.
    """
    wanted = {key.replace("__", ".", 1): value for key, value in states.items()}
    return [
        replace(entity, state=wanted[entity.entity_id])
        if entity.entity_id in wanted else entity
        for entity in entities
    ]


@dataclass
class Turn:
    text: str
    speech: str
    response_type: Any
    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    @property
    def targets(self) -> set[str]:
        found: set[str] = set()
        for _, _, data in self.calls:
            entity_id = data.get("entity_id")
            if isinstance(entity_id, str):
                found.add(entity_id)
            elif isinstance(entity_id, (list, tuple)):
                found.update(entity_id)
        return found


class HouseConversation:
    """Drive the real conversation entity against the stub test house."""

    def __init__(self, monkeypatch, entities: list[EntitySnapshot] | None = None,
                 area: str | None = None) -> None:
        import _ha_stub

        _ha_stub.install()
        import homeintent.conversation as ha_conversation
        from homeintent.areas import AreaSnapshot
        from homeintent.conversation import NluConversationEntity
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.core import HomeAssistant

        self.entities = entities if entities is not None else house_entities()
        self.entity = NluConversationEntity(ConfigEntry())
        self.entity.hass = HomeAssistant()
        monkeypatch.setattr(
            ha_conversation, "build_entity_snapshots", lambda hass, entry: self.entities
        )
        if area is not None:
            area_name = next(
                entity.area_name for entity in self.entities if entity.area_id == area
            )
            monkeypatch.setattr(
                ha_conversation,
                "resolve_conversation_area",
                lambda hass, user_input: AreaSnapshot(area, area_name or area),
            )
        self.conversation_id = "testhaus"

    def say(self, text: str) -> Turn:
        from homeassistant.components.conversation import ConversationInput

        mock = self.entity.hass.services.async_call
        before = len(mock.await_args_list)
        result = asyncio.run(self.entity._async_handle_message(
            ConversationInput(text=text, conversation_id=self.conversation_id),
            chat_log=None,
        ))
        calls = [
            (call.args[0], call.args[1], dict(call.args[2]) if len(call.args) > 2 else {})
            for call in mock.await_args_list[before:]
        ]
        return Turn(text, result.response.speech or "", result.response.response_type, calls)
