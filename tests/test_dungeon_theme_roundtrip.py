"""Dungeon themes carry their own abilities, effects, loot, scaling, and bosses, and travel between stations."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import uuid

import pytest

from stationnet import create_app, character_creation, dungeons, mud_abilities, mud_effects, mud_scope
from stationnet.db import get_db

pytestmark = pytest.mark.mud_world(mud_scope.PROCEDURAL)

# Manifests the bundled themes generated before themes could tune scaling; defaults must not drift.
BUNDLED_MANIFESTS = {
    "desert": "e29c7a35833c1e8acdd5fb45",
    "frozen-frontier": "d683f5c98402a3558e20ec18",
    "generic-fantasy-dungeon": "4d49922a2468219ca5ca3f38",
    "jungle-ruins": "1eb2a803dbfe83d032f61fc0",
}


@pytest.fixture
def stations():
    made = []

    def make():
        data = Path(__file__).parent / f"_theme_roundtrip_{uuid.uuid4().hex}"
        data.mkdir()
        app = create_app({"TESTING": True, "DATA_DIR": str(data)})
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_procedural_enabled) "
                       "VALUES('Test','Test','TST',1,1)")
            sysop = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                               "VALUES('sysop','SYS','x',1)").lastrowid
            db.commit()
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = sysop
            session["csrf_token"] = "t"
        made.append((app, data))
        return app, client
    yield make
    for app, data in made:
        app.extensions["broadcast_coordinator"]._closed = True
        app.extensions["broadcast_coordinator"]._timer.cancel()
        shutil.rmtree(data, ignore_errors=True)


def _theme(**changes):
    theme = {
        "schema_version": 2, "id": "ember-vault", "version": "1.0.0", "name": "Ember Vault", "author": "Tester",
        "effects": [{"key": "cinders", "name": "Cinders", "kind": "damage", "resist_as": "burning"}],
        "abilities": [{"key": "cinder-bolt", "name": "Cinder Bolt", "command": "bolt", "target": "enemy",
                       "cost": 5, "damage": 6, "effects": [{"key": "cinders", "potency": 2, "duration": 2}]}],
        "origins": {"classes": [{"key": "ashwalker", "name": "Ashwalker", "description": "Walkers of the vault.",
                                 "traits": "Cinders", "skill": "guard", "spells": ["cinder-bolt"]}]},
        "definitions": {
            "items": [{"id": "ember", "name": "Ember Stone", "description": "A warm stone.", "item_type": "misc",
                       "properties": {"value": 9}}],
            "enemies": [
                {"id": "imp", "name": "Ash Imp", "description": "A smouldering imp.",
                 "properties": {"on_hit_effects": [{"key": "cinders", "potency": 1, "duration": 2}]},
                 "combat": {"maximum_health": 20, "attack": 4, "experience_reward": 10}},
                {"id": "warden", "name": "Vault Warden", "description": "A furnace in armor.", "boss": True,
                 "combat": {"maximum_health": 50, "attack": 8, "experience_reward": 50}},
            ],
        },
        "generation": {"size": {"minimum": 5, "default": 6, "maximum": 8},
                       "difficulty": {"minimum": 1, "default": 3, "maximum": 6},
                       "layouts": [{"type": "linear", "weight": 1}],
                       "special_roles": [{"role": "boss", "count": 1, "min_depth_ratio": 0.8}]},
        "rewards": {"experience_percent": 200,
                    "risk_loot": {"enabled": True, "chance_percent": 200, "tiers": {
                        "1": [{"item_local_id": "ember"}], "2": [{"item_local_id": "ember"}],
                        "3": [{"item_local_id": "ember"}]}}},
        "scaling": {"offset": 1, "per_step": {"maximum_health": 10, "defense": 0}},
        "bosses": {"health_percent": 300, "attack_percent": 150},
        "pools": {
            "rooms": [{"id": "hall", "names": [{"text": "Ash Hall"}], "descriptions": [{"text": "Soot drifts."}]}],
            "environments": [{"id": "embers", "descriptions": [{"text": "Embers glow underfoot."}]}],
            "enemies": [{"id": "imps", "count": {"minimum": 1, "maximum": 1}, "choices": [{"local_id": "imp"}]},
                        {"id": "boss", "count": {"minimum": 1, "maximum": 1}, "choices": [{"local_id": "warden"}]}],
            "encounters": [{"id": "imp-fight", "enemy_pool": "imps", "conditions": {"roles": ["normal", "exit"]}},
                           {"id": "boss-fight", "enemy_pool": "boss", "conditions": {"roles": ["boss"]}}],
        },
        "triggers": [{"id": "hot-floor", "event": "enter_room", "source": {"environment": "embers"},
                      "actions": [{"type": "apply_effect", "effect": "cinders", "potency": 1, "duration": 1}]}],
    }
    theme.update(changes)
    return theme


def _upload(client, theme, **form):
    return client.post("/sysop/settings/dungeons/import", data={
        "csrf_token": "t", "dungeon_file": (BytesIO(json.dumps(theme).encode()), "theme.json"), **form,
    }, follow_redirects=True)


def test_bundled_themes_generate_exactly_as_before(stations):
    app, _client = stations()
    with app.app_context():
        db = get_db()
        for theme_id, expected in BUNDLED_MANIFESTS.items():
            template = dungeons.discover_templates()[theme_id][1]
            digests = []
            for seed in ("alpha", "beta", "gamma"):
                for difficulty in (template["generation"]["difficulty"]["minimum"],
                                   template["generation"]["difficulty"]["maximum"]):
                    manifest = dungeons.generate_manifest(template, seed, difficulty=difficulty, database=db)
                    digests.append(hashlib.sha256(dungeons.canonical_json(manifest).encode()).hexdigest()[:16])
            assert hashlib.sha256("".join(digests).encode()).hexdigest()[:24] == expected, theme_id


def test_theme_tunes_rewards_scaling_bosses_and_risk_loot(stations):
    app, _client = stations()
    with app.app_context():
        db = get_db()
        manifest = dungeons.generate_manifest(_theme(), "seed", difficulty=3, database=db)
        enemies = {}
        for room in manifest["rooms"]:
            for entry in room["content"]:
                if entry["type"] == "enemy":
                    enemies.setdefault(entry["snapshot"]["name"], []).append((room["difficulty"], entry["snapshot"]))
        difficulty, imp = enemies["Ash Imp"][0]
        steps = difficulty - 1
        assert imp["maximum_health"] == 20 + steps * 10 and imp["defense"] == 2
        assert imp["experience_reward"] == (10 + steps * 5) * 2
        assert json.loads(imp["properties_json"])["on_hit_effects"][0]["key"] == \
            dungeons.bundled_origin_key("ember-vault", "effects", "cinders")
        difficulty, warden = enemies["Vault Warden"][0]
        steps = difficulty - 1
        assert warden["boss"] is True
        assert warden["maximum_health"] == (50 + steps * 10) * 300 // 100
        assert warden["attack"] == (8 + steps * 2) * 150 // 100
        loot = [entry for room in manifest["rooms"] for entry in room["content"]
                if entry["pool_id"] == "stationnet-risk-loot"]
        assert loot and {entry["snapshot"]["name"] for entry in loot} == {"Ember Stone"}
        off = _theme(rewards={"risk_loot": {"enabled": False}})
        assert not [entry for room in dungeons.generate_manifest(off, "seed", database=db)["rooms"]
                    for entry in room["content"] if entry["pool_id"] == "stationnet-risk-loot"]


@pytest.mark.parametrize("changes,message", [
    ({"schema_version": 1}, "requires schema_version 2"),
    ({"sparkles": True}, "unsupported top-level field"),
    ({"schema_version": 7}, "needs a newer StationNet"),
    ({"scaling": {"per_step": {"attack": 50}}}, "scaling.per_step.attack"),
    ({"bosses": {"health_percent": 900}}, "bosses.health_percent"),
    ({"effects": [{"key": "poison", "name": "Poison", "kind": "damage"}]}, "built-in effect"),
    ({"triggers": [{"id": "x", "event": "enter_room", "source": {"environment": "lava"},
                    "actions": [{"type": "message", "text": "hot"}]}]}, "unknown environment pool"),
    ({"triggers": [{"id": "x", "event": "enter_room", "source": {"environment": "embers"},
                    "actions": [{"type": "apply_effect", "effect": "frost"}]}]}, "effect 'frost'"),
])
def test_theme_validation_names_the_problem(stations, changes, message):
    app, _client = stations()
    with app.app_context():
        errors = dungeons.validate_template(_theme(**changes), get_db())
    assert any(message in error for error in errors), errors


def test_schema_one_themes_still_validate(stations):
    app, _client = stations()
    with app.app_context():
        starter = json.loads((Path(dungeons.__file__).with_name("dungeon_templates") / "starter-theme.json")
                             .read_text(encoding="utf-8"))
        assert starter["schema_version"] == 1
        assert dungeons.validate_template({**starter, "id": "starter-copy"}, get_db()) == []


def test_theme_exports_and_plays_on_a_clean_station(stations):
    app, client = stations()
    assert b"Imported dungeon theme Ember Vault" in _upload(client, _theme()).data
    exported = client.get("/sysop/settings/dungeons/ember-vault/export")
    assert exported.status_code == 200
    document = json.loads(exported.data)

    other, other_client = stations()
    assert b"Imported dungeon theme Ember Vault" in _upload(other_client, document).data
    with other.app_context():
        db = get_db()
        ashwalker = dungeons.bundled_origin_key("ember-vault", "classes", "ashwalker")
        bolt = dungeons.bundled_origin_key("ember-vault", "abilities", "cinder-bolt")
        cinders = dungeons.bundled_origin_key("ember-vault", "effects", "cinders")
        # The class is offered where the theme's runs happen, and grants the theme's ability.
        assert ashwalker in character_creation.world_availability(db, mud_scope.PROCEDURAL)["classes"]
        assert mud_abilities.catalog(db)[bolt]["effects"][0]["key"] == cinders
        assert mud_effects.catalog(db)[cinders]["name"] == "Cinders"
        # A generated run installs the theme's enemies applying the theme's effect.
        dungeons.create_instance(db, dungeons.discover_templates()["ember-vault"][1], "seed")
        imp = db.execute("SELECT properties_json FROM mud_npc_definitions WHERE id=?",
                         (dungeons.local_definition_id({"id": "ember-vault", "version": "1.0.0"}, "npcs", "imp"),)
                         ).fetchone()
        assert json.loads(imp["properties_json"])["on_hit_effects"][0]["key"] == cinders


def test_theme_upgrade_requires_a_new_version(stations):
    app, client = stations()
    _upload(client, _theme())
    assert b"already installed" in _upload(client, _theme()).data
    changed = _theme(name="Ember Vault Revised")
    assert b"without changing its version" in _upload(client, changed, upgrade="on").data
    upgraded = _upload(client, {**changed, "version": "1.1.0"}, upgrade="on").data
    assert b"Upgraded dungeon theme Ember Vault Revised" in upgraded
    with app.app_context():
        assert dungeons.discover_templates()["ember-vault"][1]["version"] == "1.1.0"


def test_self_contained_export_embeds_station_definitions(stations):
    app, client = stations()
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type,properties_json) "
                   "VALUES('local:coin','Old Coin','A worn coin.','misc','{\"value\":3}')")
        db.execute("INSERT INTO mud_npc_definitions(id,name,description) VALUES('local:rat','Rat','A rat.')")
        db.execute("INSERT INTO mud_npc_combat_profiles(npc_definition_id,maximum_health,attack) "
                   "VALUES('local:rat',10,2)")
        db.execute("INSERT INTO mud_npc_combat_rewards(id,npc_definition_id,reward_type,target_id,amount) "
                   "VALUES('local:rat:coin','local:rat','item','local:coin',1)")
        db.commit()
    theme = _theme()
    theme["pools"]["enemies"].append({"id": "rats", "count": {"minimum": 1, "maximum": 1},
                                      "choices": [{"definition_id": "local:rat"}]})
    assert b"Imported dungeon theme" in _upload(client, theme).data
    plain = client.get("/sysop/settings/dungeons/ember-vault/export", follow_redirects=True)
    assert b"station definitions other stations will not have" in plain.data
    embedded = json.loads(client.get("/sysop/settings/dungeons/ember-vault/export?embed=1").data)
    rats = next(pool for pool in embedded["pools"]["enemies"] if pool["id"] == "rats")
    assert "local_id" in rats["choices"][0] and "definition_id" not in rats["choices"][0]
    rat = next(entry for entry in embedded["definitions"]["enemies"] if entry["id"] == rats["choices"][0]["local_id"])
    coin = rat["loot"][0]["item_local_id"]
    assert any(item["id"] == coin and item["name"] == "Old Coin" for item in embedded["definitions"]["items"])
    other, other_client = stations()
    assert b"Imported dungeon theme" in _upload(other_client, embedded).data
