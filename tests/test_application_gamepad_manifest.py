import pytest

from stationnet.applications import Application, _manifest_capabilities, _validate_metadata


def test_gamepad_is_a_bounded_manifest_input():
    base = dict(slug="gamepad-test", name="Gamepad test", description="Test",
                author="Test", version="1", category="Games")
    _validate_metadata(Application(**base, required_inputs=("gamepad",)))
    with pytest.raises(ValueError, match="input requirements"):
        _validate_metadata(Application(**base, required_inputs=("gamepad", "gamepad")))
    with pytest.raises(ValueError, match="input requirements"):
        _validate_metadata(Application(**base, required_inputs=("controller",)))


def test_fullscreen_target_defaults_to_application_and_validates_canvas():
    base = dict(slug="fullscreen-test", name="Fullscreen test", description="Test",
                author="Test", version="1", category="Games")
    assert _manifest_capabilities({})[-1] == "application"
    assert _manifest_capabilities({"fullscreen_target": "canvas"})[-1] == "canvas"
    _validate_metadata(Application(**base, fullscreen_target="canvas"))
    with pytest.raises(ValueError, match="fullscreen target"):
        _validate_metadata(Application(**base, fullscreen_target="unknown"))
    with pytest.raises(ValueError, match="fullscreen_target"):
        _manifest_capabilities({"fullscreen_target": ["canvas"]})
