import json
from pathlib import Path
import sqlite3

import pytest

from stationnet import create_app, dungeons
from stationnet.db import get_db


@pytest.fixture()
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "chunk-10-tests",
        "DATABASE": str(tmp_path / "stationnet.sqlite"),
        "DATA_DIR": str(tmp_path),
        "DUNGEON_THEMES_DIR": str(tmp_path / "dungeon_themes"),
    })


def theme():
    return {
        "schema_version": 1,
        "id": "neutral-test",
        "version": "1.0.0",
        "name": "Neutral test fixture",
        "generation": {
            "size": {"minimum": 5, "default": 12, "maximum": 40},
            "difficulty": {"minimum": 1, "default": 3, "maximum": 20},
            "layouts": [
                {"type": "linear", "weight": 1},
                {"type": "branching", "weight": 2},
                {"type": "maze", "weight": 2},
                {"type": "hub", "weight": 1},
            ],
            "special_roles": [{
                "role": "special", "count": 1, "min_depth_ratio": 0.2,
                "max_depth_ratio": 0.9, "prefer_dead_ends": False,
            }],
        },
        "pools": {
            "rooms": [
                {"id": "entrance", "conditions": {"roles": ["entrance"]},
                 "names": [{"text": "Entrance"}],
                 "descriptions": [{"text": "Entrance description."}]},
                {"id": "exit", "conditions": {"roles": ["exit"]},
                 "names": [{"text": "Exit"}],
                 "descriptions": [{"text": "Exit description."}]},
                {"id": "special", "conditions": {"roles": ["special"]},
                 "names": [{"text": "Special"}],
                 "descriptions": [{"text": "Special description."}]},
                {"id": "common", "weight": 100, "conditions": {"roles": ["normal"]},
                 "names": [{"text": "Common", "weight": 100}],
                 "descriptions": [{"text": "Common description.", "weight": 100}]},
                {"id": "rare", "weight": 1, "conditions": {"roles": ["normal"]},
                 "names": [{"text": "Rare"}],
                 "descriptions": [{"text": "Rare description."}]},
            ],
            "environments": [{
                "id": "deep", "conditions": {"min_depth_ratio": 0.5},
                "descriptions": [{"text": "Deep variation."}],
            }],
            "enemies": [{
                "id": "test-enemies", "chance": 1,
                "count": {"minimum": 1, "maximum": 1},
                "choices": [{"definition_id": "test:npc", "weight": 1}],
            }],
            "items": [{
                "id": "test-items", "chance": 1,
                "count": {"minimum": 1, "maximum": 1},
                "choices": [{"definition_id": "test:item", "weight": 1}],
            }],
            "encounters": [{
                "id": "test-encounter", "chance": 1,
                "conditions": {"roles": ["special"]},
                "enemy_pool": "test-enemies", "item_pool": "test-items",
                "config": {"fixture": True},
            }],
        },
    }


def seed_definitions(database):
    database.execute(
        "INSERT INTO mud_npc_definitions(id,name,description,properties_json,behaviors_json) "
        "VALUES('test:npc','Test NPC','Fixture.','{}','{}')"
    )
    database.execute(
        "INSERT INTO mud_npc_combat_profiles(npc_definition_id,maximum_health,attack,defense) "
        "VALUES('test:npc',20,4,1)"
    )
    database.execute(
        "INSERT INTO mud_item_definitions(id,name,description) "
        "VALUES('test:item','Test Item','Fixture.')"
    )


@pytest.mark.parametrize("layout", sorted(dungeons.SUPPORTED_LAYOUTS))
def test_all_layouts_are_connected_bidirectional_and_completable(app, layout):
    with app.app_context():
        database = get_db()
        seed_definitions(database)
        manifest = dungeons.generate_manifest(
            theme(), "layout-seed", size=17, layout=layout, difficulty=4, database=database
        )
        assert len(manifest["rooms"]) == 17
        assert manifest["entrance"] != manifest["exit"]
        assert [room["role"] for room in manifest["rooms"]].count("entrance") == 1
        assert [room["role"] for room in manifest["rooms"]].count("exit") == 1
        assert [room["role"] for room in manifest["rooms"]].count("special") == 1
        dungeons.validate_manifest(manifest)


def test_same_seed_and_template_version_reproduce_exact_manifest(app):
    with app.app_context():
        database = get_db()
        seed_definitions(database)
        first = dungeons.generate_manifest(theme(), "repeatable", layout="maze", database=database)
        second = dungeons.generate_manifest(theme(), "repeatable", layout="maze", database=database)
        assert dungeons.canonical_json(first) == dungeons.canonical_json(second)
        changed = dungeons.generate_manifest(theme(), "different", layout="maze", database=database)
        assert dungeons.canonical_json(first) != dungeons.canonical_json(changed)


def test_conditions_weights_and_encounter_pools_preserve_theme_rules(app):
    with app.app_context():
        database = get_db()
        seed_definitions(database)
        manifest = dungeons.generate_manifest(
            theme(), "selection", size=40, layout="branching", database=database
        )
        special = next(room for room in manifest["rooms"] if room["role"] == "special")
        assert {entry["type"] for entry in special["content"]} == {
            "encounter", "enemy", "item",
        }
        assert all(room["room_template_id"] == room["role"]
                   for room in manifest["rooms"] if room["role"] != "normal")
        normal_templates = [room["room_template_id"] for room in manifest["rooms"]
                            if room["role"] == "normal"]
        assert normal_templates.count("common") > normal_templates.count("rare")
        assert all(room["environment_id"] == "deep" for room in manifest["rooms"]
                   if room["depth"] / max(item["depth"] for item in manifest["rooms"]) >= 0.5)


def test_persisted_instance_is_isolated_and_snapshots_shared_definitions(app):
    with app.app_context():
        database = get_db()
        seed_definitions(database)
        world_before = database.execute("SELECT COUNT(*) FROM mud_world_rooms").fetchone()[0]
        instance_id, manifest = dungeons.create_instance(
            database, theme(), "persisted", size=9, layout="linear"
        )
        database.commit()
        assert database.execute("SELECT status FROM mud_procedural_instances WHERE id=?", (instance_id,)).fetchone()["status"] == "pending"
        assert database.execute("SELECT COUNT(*) FROM mud_dungeon_rooms WHERE instance_id=?", (instance_id,)).fetchone()[0] == 9
        assert database.execute("SELECT COUNT(*) FROM mud_world_rooms").fetchone()[0] == world_before
        assert database.execute("SELECT COUNT(*) FROM mud_npc_instances").fetchone()[0] == 0
        snapshot = database.execute(
            "SELECT snapshot_json FROM mud_dungeon_content WHERE instance_id=? AND content_type='enemy'",
            (instance_id,),
        ).fetchone()
        assert json.loads(snapshot["snapshot_json"])["name"] == "Test NPC"
        database.execute("UPDATE mud_npc_definitions SET name='Changed' WHERE id='test:npc'")
        saved = json.loads(database.execute(
            "SELECT generation_manifest_json FROM mud_procedural_instances WHERE id=?", (instance_id,)
        ).fetchone()["generation_manifest_json"])
        assert saved == manifest
        assert json.loads(snapshot["snapshot_json"])["name"] == "Test NPC"


def test_validation_reports_paths_missing_definitions_and_version_collisions(app):
    broken = theme()
    broken["generation"]["size"]["default"] = 99
    broken["pools"]["rooms"][0]["conditions"]["unexpected"] = True
    with app.app_context():
        database = get_db()
        errors = dungeons.validate_template(broken, database)
        assert any("$.generation.size" in error for error in errors)
        assert any("unexpected: unknown condition" in error for error in errors)
        assert any("does not exist" in error for error in errors)

        seed_definitions(database)
        instance_id, _manifest = dungeons.create_instance(database, theme(), "first", layout="linear")
        database.commit()
        changed = theme()
        changed["name"] = "Changed without a new version"
        with pytest.raises(dungeons.TemplateError, match="change the template version"):
            manifest = dungeons.generate_manifest(changed, "second", layout="linear", database=database)
            dungeons.persist_instance(database, changed, manifest)
        assert database.execute("SELECT 1 FROM mud_procedural_instances WHERE id=?", (instance_id,)).fetchone()


def test_generation_failure_is_diagnostic_and_leaves_no_partial_instance(app):
    impossible = theme()
    impossible["generation"]["special_roles"][0]["count"] = 20
    with app.app_context():
        database = get_db()
        seed_definitions(database)
        with pytest.raises(dungeons.GenerationError, match="needs 20 rooms"):
            dungeons.create_instance(database, impossible, "failure", size=5, layout="linear")
        database.commit()
        assert database.execute("SELECT 1 FROM mud_procedural_instances").fetchone() is None
        failure = database.execute(
            "SELECT status,message,request_json FROM mud_dungeon_generation_log"
        ).fetchone()
        assert failure["status"] == "failure"
        assert "needs 20 rooms" in failure["message"]
        assert json.loads(failure["request_json"])["seed"] == "failure"


def test_existing_chunk_nine_instance_table_migrates_non_destructively(tmp_path):
    database_path = tmp_path / "legacy.sqlite"
    database = sqlite3.connect(database_path)
    database.execute(
        "CREATE TABLE mud_procedural_instances ("
        "id TEXT PRIMARY KEY,instance_type TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',"
        "seed TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    database.execute(
        "INSERT INTO mud_procedural_instances(id,instance_type,status,seed) "
        "VALUES('legacy','random','pending','old-seed')"
    )
    database.commit()
    database.close()
    migrated_app = create_app({
        "TESTING": True, "SECRET_KEY": "migration", "DATABASE": str(database_path),
        "DATA_DIR": str(tmp_path), "DUNGEON_THEMES_DIR": str(tmp_path / "themes"),
    })
    with migrated_app.app_context():
        row = get_db().execute(
            "SELECT id,seed,theme_id,generator_version FROM mud_procedural_instances "
            "WHERE id='legacy'"
        ).fetchone()
        assert dict(row) == {
            "id": "legacy", "seed": "old-seed", "theme_id": None,
            "generator_version": None,
        }


def test_admin_cli_validates_previews_generates_and_lists_failures(app):
    template_path = Path(app.config["DUNGEON_THEMES_DIR"]) / "neutral.json"
    template_path.write_text(json.dumps(theme()), encoding="utf-8")
    with app.app_context():
        seed_definitions(get_db())
        get_db().commit()
    runner = app.test_cli_runner()
    valid = runner.invoke(args=["dungeon", "validate", str(template_path), "--check-definitions"])
    assert valid.exit_code == 0, valid.output
    definitions = runner.invoke(args=["dungeon", "definitions"])
    assert definitions.exit_code == 0
    assert "test:npc" in definitions.output and "test:item" in definitions.output
    preview = runner.invoke(args=["dungeon", "preview", "neutral-test", "--seed", "cli", "--layout", "hub"])
    assert preview.exit_code == 0, preview.output
    assert "layout=hub" in preview.output and "entrance=" in preview.output
    generated = runner.invoke(args=["dungeon", "generate", "neutral-test", "--seed", "cli"])
    assert generated.exit_code == 0, generated.output
    failures = runner.invoke(args=["dungeon", "failures"])
    assert failures.exit_code == 0
    assert "No generation failures" in failures.output
