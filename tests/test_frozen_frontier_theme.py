"""The bundled Frozen Frontier theme installs and generates playable expeditions."""

from stationnet import create_app
from stationnet.db import get_db
from stationnet.dungeons import (create_instance, discover_templates,
                                 generate_manifest, validate_template)


def test_frozen_frontier_theme_installs_and_generates(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    with app.app_context():
        path, template = discover_templates()["frozen-frontier"]
        assert path.name == "frozen-frontier.json"
        assert validate_template(template, get_db()) == []

        for layout in ("linear", "branching", "maze", "hub"):
            manifest = generate_manifest(template, f"frozen-{layout}", size=8,
                                         layout=layout, database=get_db())
            assert {room["role"] for room in manifest["rooms"]} == {
                "entrance", "exit", "normal", "treasure", "boss",
            }
            boss = next(room for room in manifest["rooms"] if room["role"] == "boss")
            assert any((content["definition_id"] or "").endswith(":glacier-warden")
                       for content in boss["content"])

        instance_id, _manifest = create_instance(
            get_db(), template, "frozen-persist", size=14, layout="branching",
            source_name=path.name,
        )
        assert get_db().execute(
            "SELECT COUNT(*) FROM mud_dungeon_content WHERE instance_id=?",
            (instance_id,),
        ).fetchone()[0] > 0

    installed = tmp_path / "dungeon_themes" / "frozen-frontier.json"
    original = installed.read_bytes()
    installed.write_bytes(original + b"\n")
    create_app({"TESTING": True, "DATA_DIR": tmp_path})
    assert installed.read_bytes() == original + b"\n"
