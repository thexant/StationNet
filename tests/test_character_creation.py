"""Character creation invariants against the real MUD schema."""
import copy
import sqlite3
import unittest

from flask import Flask

from stationnet import character_creation, db, mud


class CharacterCreationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.context = self.app.app_context()
        self.context.push()
        self.database = sqlite3.connect(":memory:")
        self.database.row_factory = sqlite3.Row
        self.database.execute("PRAGMA foreign_keys=ON")
        self.database.executescript(db.SCHEMA)
        self.database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES('account','RADIO','x')"
        )
        self.user_id = self.database.execute("SELECT id FROM users").fetchone()[0]
        self.character_id = f"stationnet:character:{self.user_id}"
        self.build = {"name": "Mira Ash", "race": "elf", "class": "warrior",
                      "attributes": {"strength": 12, "agility": 11, "intellect": 10, "vitality": 11},
                      "skills": ["arms", "lore"]}

    def tearDown(self):
        self.database.close()
        self.context.pop()

    def test_creation_preserves_account_and_populates_every_track(self):
        character_creation.complete(self.database, self.user_id, self.build)
        character = self.database.execute(
            "SELECT display_name,creation_state,race_key,class_key FROM mud_characters"
        ).fetchone()
        self.assertEqual(tuple(character), ("Mira Ash", "complete", "elf", "warrior"))
        self.assertEqual(self.database.execute("SELECT callsign FROM users").fetchone()[0], "RADIO")
        tracks = self.database.execute(
            "SELECT strength,agility,intellect,vitality,maximum_health FROM mud_character_progression"
        ).fetchall()
        self.assertEqual(len(tracks), 3)
        self.assertTrue(all(tuple(track) == (12, 13, 10, 10, 100) for track in tracks))
        self.assertEqual(character_creation.skill_ranks(self.database, self.character_id),
                         {"arms": 2, "lore": 1})
        self.assertEqual(self.database.execute(
            "SELECT COUNT(*) FROM mud_item_instances WHERE owner_character_id=?",
            (self.character_id,),
        ).fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, "already been created"):
            character_creation.complete(self.database, self.user_id, self.build)

    def test_rejects_overspend_and_duplicate_skills(self):
        build = copy.deepcopy(self.build)
        build["attributes"]["strength"] = 14
        with self.assertRaisesRegex(ValueError, "exactly"):
            character_creation.complete(self.database, self.user_id, build)
        build = copy.deepcopy(self.build)
        build["skills"] = ["arms", "arms"]
        with self.assertRaisesRegex(ValueError, "distinct"):
            character_creation.complete(self.database, self.user_id, build)
        self.assertEqual(self.database.execute(
            "SELECT creation_state FROM mud_characters"
        ).fetchone()[0], "pending")

    def test_legacy_customization_keeps_progress_and_inventory(self):
        self.database.execute(
            "UPDATE mud_characters SET creation_state='legacy' WHERE id=?", (self.character_id,)
        )
        self.database.execute(
            "UPDATE mud_character_progression SET experience=400,level=3,"
            "maximum_health=120,current_health=71 WHERE character_id=? AND track_key='shared'",
            (self.character_id,),
        )
        character_creation.complete(self.database, self.user_id, self.build)
        row = self.database.execute(
            "SELECT experience,level,maximum_health,current_health FROM mud_character_progression "
            "WHERE character_id=? AND track_key='shared'", (self.character_id,),
        ).fetchone()
        self.assertEqual(tuple(row), (400, 3, 120, 71))

    def test_skill_progression_and_experience_bonus(self):
        character_creation.complete(self.database, self.user_id, self.build)
        self.assertEqual(mud._character_effects(self.database, self.character_id)["attack"], 3)
        for _ in range(20):
            character_creation.practice(self.database, self.character_id, "lore")
        self.assertEqual(character_creation.skill_ranks(self.database, self.character_id)["lore"], 2)
        reward = mud.award_experience(self.database, self.user_id, 100)
        self.assertGreater(reward["amount"], 100)
        self.assertEqual(reward["character"]["level"], 2)

    def test_existing_rows_become_legacy_without_reset(self):
        older = sqlite3.connect(":memory:")
        older.row_factory = sqlite3.Row
        older.executescript(
            "CREATE TABLE users(id INTEGER PRIMARY KEY);"
            "CREATE TABLE mud_characters(id TEXT PRIMARY KEY,user_id INTEGER UNIQUE,display_name TEXT);"
            "INSERT INTO users(id) VALUES(1);"
            "INSERT INTO mud_characters VALUES('old',1,'Old Hero');"
        )
        db._migrate_mud_character_creation(older)
        self.assertEqual(older.execute(
            "SELECT display_name,creation_state FROM mud_characters"
        ).fetchone()["creation_state"], "legacy")
        db._migrate_mud_character_creation(older)
        self.assertEqual(older.execute(
            "SELECT COUNT(*) FROM mud_characters"
        ).fetchone()[0], 1)
        older.close()


if __name__ == "__main__":
    unittest.main()
