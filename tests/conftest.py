"""Shared test setup: MUD hit rolls are deterministic unless a test opts into them."""
import pytest

from stationnet import mud


def pytest_configure(config):
    config.addinivalue_line("markers", "combat_rolls: let MUD attacks roll to hit instead of always landing")


@pytest.fixture(autouse=True)
def _always_hit(request, monkeypatch):
    # Damage, death, and practice tests predate misses; a roll of 0 always connects.
    if request.node.get_closest_marker("combat_rolls") is None:
        monkeypatch.setattr(mud, "_hit_roll", lambda: 0)
