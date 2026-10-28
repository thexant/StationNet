"""Shared test setup: MUD hit rolls are deterministic unless a test opts into them, and MUD code
called directly (outside a request) acts in a world chosen by the ``mud_world`` marker."""
import pytest

from stationnet import mud, mud_scope


def pytest_configure(config):
    config.addinivalue_line("markers", "combat_rolls: let MUD attacks roll to hit instead of always landing")
    config.addinivalue_line("markers", "mud_world(key): the MUD world domain-level calls act in "
                                       "(default: the home world)")


@pytest.fixture(autouse=True)
def _always_hit(request, monkeypatch):
    # Damage, death, and practice tests predate misses; a roll of 0 always connects.
    if request.node.get_closest_marker("combat_rolls") is None:
        monkeypatch.setattr(mud, "_hit_roll", lambda: 0)


@pytest.fixture(autouse=True)
def _mud_world(request):
    # Requests choose their world from the URL; helpers called directly use this one.
    marker = request.node.get_closest_marker("mud_world")
    with mud_scope.use(marker.args[0] if marker else mud_scope.HOME):
        yield
