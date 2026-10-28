"""Regression tests for rebuilding SQLite tables during startup migrations."""
import sqlite3

import pytest

from stationnet.db import _rebuild_with_check


@pytest.mark.parametrize("quoted", [False, True])
def test_rebuild_with_check_preserves_rows_and_indexes(quoted):
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys=ON")
    name = '"mud_quest_objectives"' if quoted else "mud_quest_objectives"
    database.execute(
        f"CREATE TABLE {name} (id TEXT PRIMARY KEY, "
        "objective_type TEXT CHECK(objective_type IN ('deliver_item')))"
    )
    database.execute("CREATE INDEX objective_type_idx ON mud_quest_objectives(objective_type)")
    database.execute("INSERT INTO mud_quest_objectives VALUES ('saved', 'deliver_item')")

    migrate = lambda: _rebuild_with_check(database, "mud_quest_objectives", "'script'",
                                          "'deliver_item')", "'deliver_item','script')")
    migrate()
    migrate()

    assert database.execute("SELECT id, objective_type FROM mud_quest_objectives").fetchone()[:] == \
        ("saved", "deliver_item")
    database.execute("INSERT INTO mud_quest_objectives VALUES ('new', 'script')")
    assert database.execute("SELECT count(*) FROM mud_quest_objectives").fetchone()[0] == 2
    assert database.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert database.execute("SELECT count(*) FROM sqlite_master WHERE name='objective_type_idx'").fetchone()[0] == 1
    assert database.execute("SELECT count(*) FROM sqlite_master WHERE name='mud_quest_objectives_new'").fetchone()[0] == 0
    database.close()


def test_failed_rebuild_rolls_back_without_leaving_a_new_table():
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.execute("CREATE TABLE mud_quest_objectives (id TEXT PRIMARY KEY, "
                     "objective_type TEXT CHECK(objective_type IN ('deliver_item')))")
    database.execute("INSERT INTO mud_quest_objectives VALUES ('saved', 'deliver_item')")

    with pytest.raises(sqlite3.IntegrityError):
        _rebuild_with_check(database, "mud_quest_objectives", "'script'",
                            "'deliver_item'", "'script'")

    assert database.execute("SELECT objective_type FROM mud_quest_objectives").fetchone()[0] == "deliver_item"
    assert database.execute("SELECT count(*) FROM sqlite_master WHERE name='mud_quest_objectives_new'").fetchone()[0] == 0
    database.close()
