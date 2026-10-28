"""Test helpers for the multi-world MUD."""
from stationnet import mud_scope


def enter_world(db, world_id):
    """Enable a world and make the rest of the test act in it.

    Requests still pick their world from the URL; this covers MUD helpers the
    test calls directly. The autouse conftest fixture restores the previous
    world when the test ends.
    """
    db.execute("UPDATE mud_world_packages SET enabled=1 WHERE id=?", (world_id,))
    mud_scope.enter(world_id)
    return world_id


def world_url(world_id, path=""):
    return f"/mud/w/{world_id}{path}"


def activate_world(db, world_id):
    """Play the test's existing characters in another world, as the old campaign switch did.

    Characters are created in the home world before the test installs its own
    world; this moves them there and enters that world.
    """
    db.execute("UPDATE mud_characters SET world_key=? WHERE world_key=?", (world_id, mud_scope.HOME))
    return enter_world(db, world_id)


def url(path=""):
    """A MUD URL in the world the test is currently acting in."""
    return f"/mud/w/{mud_scope.current()}{path}"


def in_current_world(function):
    """Wrap a function for a worker thread, which starts without the test's world."""
    world = mud_scope.current()

    def run(*args, **kwargs):
        with mud_scope.use(world):
            return function(*args, **kwargs)
    return run


def set_offered(db, kind, key, offered, world=None):
    """Offer or withdraw one race, class, or skill in a world (the current one by default)."""
    from stationnet import character_creation
    world = mud_scope.current() if world is None else world
    chosen = character_creation.world_availability(db, world)
    chosen[kind] = chosen[kind] | {key} if offered else chosen[kind] - {key}
    return character_creation.set_world_availability(db, world, chosen)


def retire_origin(db, character_id, kind):
    """Point a character at a race or class that no longer exists, as if a sysop deleted it."""
    column = "race_key" if kind == "races" else "class_key"
    db.execute(f"UPDATE mud_characters SET {column}='deleted-origin' WHERE id=?", (character_id,))
