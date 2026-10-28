"""The MUD world a request, tick, or test is acting in.

Every station can run several independent MUD worlds at once, and an account
has one character per world. Player state that used to be keyed by account
alone is now keyed by account and world, so code reads the current world from
here instead of passing it through every helper. Requests set it from their
URL, background work sets it per world, and reading it while unset raises so
an unscoped code path fails loudly rather than mixing worlds.
"""
import contextlib
import contextvars

# The procedural dungeon world. A colon can never appear in an installed
# world ID, so this key cannot collide with an authored world.
PROCEDURAL = "stationnet:procedural"
HOME = "stationnet"

_WORLD = contextvars.ContextVar("mud_world", default=None)


class WorldScopeError(RuntimeError):
    pass


def current():
    world = _WORLD.get()
    if world is None:
        raise WorldScopeError("No MUD world is selected for this operation.")
    return world


def get(default=None):
    world = _WORLD.get()
    return default if world is None else world


def is_procedural(world=None):
    return (current() if world is None else world) == PROCEDURAL


def mode(world=None):
    """Authored worlds play in traditional mode; the dungeon world in procedural mode."""
    return "procedural" if is_procedural(world) else "traditional"


def authored():
    """The current authored world, or "" inside the procedural world."""
    world = current()
    return "" if world == PROCEDURAL else world


def enter(world):
    return _WORLD.set(world)


def leave(token):
    _WORLD.reset(token)


@contextlib.contextmanager
def use(world):
    token = _WORLD.set(world)
    try:
        yield world
    finally:
        _WORLD.reset(token)
