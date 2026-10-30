"""The classifier follows the central CoverDeviceClass enum of pinned HA."""

from homeassistant.components.cover import CoverDeviceClass

from custom_components.homeintent.cover_safety import HA_COVER_DEVICE_CLASSES


def test_cover_device_classes_match_home_assistant_enum() -> None:
    assert HA_COVER_DEVICE_CLASSES == frozenset(
        item.value for item in CoverDeviceClass
    )
