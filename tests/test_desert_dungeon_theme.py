"""The bundled desert theme is playable and remains user-overridable."""

from stationnet import create_app
from stationnet.db import get_db
from stationnet.dungeons import (create_instance, discover_templates,
                                 generate_manifest, validate_template)


def test_desert_theme_installs_and_generates(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    with app.app_context():
        themes = discover_templates()
        assert {"generic-fantasy-dungeon", "desert"} <= themes.keys()
        path, template = themes["desert"]
        assert path.name == "desert.json"
        assert validate_template(template, get_db()) == []

        for layout in ("linear", "branching", "maze", "hub"):
            manifest = generate_manifest(template, f"desert-{layout}", size=8,
                                         layout=layout, database=get_db())
            assert {room["role"] for room in manifest["rooms"]} == {
                "entrance", "exit", "normal", "treasure", "boss",
            }
            boss = next(room for room in manifest["rooms"] if room["role"] == "boss")
            assert any((content["definition_id"] or "").endswith(":jackal-guardian")
                       for content in boss["content"])

        instance_id, _manifest = create_instance(
            get_db(), template, "desert-persist", size=14, layout="branching",
            source_name=path.name,
        )
        assert get_db().execute(
            "SELECT COUNT(*) FROM mud_dungeon_content WHERE instance_id=?",
            (instance_id,),
        ).fetchone()[0] > 0

    installed = tmp_path / "dungeon_themes" / "desert.json"
    original = installed.read_bytes()
    installed.write_bytes(original + b"\n")
    create_app({"TESTING": True, "DATA_DIR": tmp_path})
    assert installed.read_bytes() == original + b"\n"
