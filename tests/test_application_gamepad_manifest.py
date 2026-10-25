import pytest

from stationnet.applications import Application, _validate_metadata


def test_gamepad_is_a_bounded_manifest_input():
    base = dict(slug="gamepad-test", name="Gamepad test", description="Test",
                author="Test", version="1", category="Games")
    _validate_metadata(Application(**base, required_inputs=("gamepad",)))
    with pytest.raises(ValueError, match="input requirements"):
        _validate_metadata(Application(**base, required_inputs=("gamepad", "gamepad")))
    with pytest.raises(ValueError, match="input requirements"):
        _validate_metadata(Application(**base, required_inputs=("controller",)))
