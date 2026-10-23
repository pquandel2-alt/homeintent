"""Contract with the Music Assistant component shipped by real HA."""

from scripts.music_assistant_contract import validate_contract


def test_music_assistant_action_and_response_contract():
    result = validate_contract()

    assert result["real_component_schema"] is True
    assert result["music_assistant_server"] is False
