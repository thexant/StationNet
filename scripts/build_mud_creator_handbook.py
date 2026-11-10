"""Build the standalone MUD creator handbook from the maintained guides."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# Shipped with the app: World Forge reads it for its field registry and Handbook tab.
OUTPUT = ROOT / "stationnet" / "world_forge" / "handbook.md"
GUIDES = [
    ("world", "stationnet/mud_world_templates/README_CUSTOM_WORLDS.md", None),
    ("origins", "stationnet/mud_origin_templates/README_RACES_CLASSES.md", "Race and class reference"),
    ("skills", "stationnet/mud_skill_templates/README_SKILLS.md", "Skill reference"),
    ("abilities", "stationnet/mud_ability_templates/README_ABILITIES_EFFECTS.md", "Ability and effect reference"),
    ("dungeons", "stationnet/dungeon_templates/README_DUNGEON_THEMES.md", "Dungeon theme reference"),
    ("presets", "stationnet/room_preset_templates/README_ROOM_PRESETS.md", "Room preset reference"),
]
PREFIX = {
    "origins": "race-class",
    "skills": "skill",
    "abilities": "ability-effect",
    "dungeons": "dungeon-theme",
    "presets": "room-preset",
}
TOP_ANCHOR = {kind: f"{PREFIX[kind]}-reference" for kind in PREFIX}
BY_PATH = {str((ROOT / path).resolve()): kind for kind, path, _ in GUIDES}


def slug(title: str) -> str:
    title = re.sub(r"`([^`]*)`", r"\1", title.lower())
    title = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE)
    return re.sub(r"[\s-]+", "-", title).strip("-")


def link_for(source: Path, target: str, current_kind: str) -> str:
    if target.startswith("#"):
        return f"#{PREFIX[current_kind]}-{target[1:]}" if current_kind in PREFIX else target
    file_part, _, anchor = target.partition("#")
    target_kind = BY_PATH.get(str((source.parent / file_part).resolve()))
    if target_kind is None:
        return target
    if target_kind == "world":
        return "#" + (anchor or "world-package-reference")
    return "#" + (f"{PREFIX[target_kind]}-{anchor}" if anchor else TOP_ANCHOR[target_kind])


def convert(kind: str, relative_path: str, heading: str | None) -> str:
    source = ROOT / relative_path
    body = source.read_text(encoding="utf-8").replace("\r\n", "\n")
    if kind == "dungeons":
        body = re.sub(r"\n\*\*Contents\*\*\n.*?\n---\n", "\n", body, count=1, flags=re.DOTALL)
    body = re.sub(
        r"\]\(([^)]+)\)",
        lambda match: f"]({link_for(source, match.group(1), kind)})",
        body,
    )
    for old, new in {
        "`../mud_ability_templates/README_ABILITIES_EFFECTS.md`": "the [ability and effect reference](#ability-effect-reference)",
        "`README_DUNGEON_THEMES.md`": "the [dungeon theme reference](#dungeon-theme-reference)",
        "`README_CUSTOM_WORLDS.md`": "the [world package reference](#world-package-reference)",
    }.items():
        body = body.replace(old, new)
    lines = body.splitlines()
    out: list[str] = []
    in_code = False
    seen: dict[str, int] = {}
    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
        match = re.match(r"^(#{1,4}) (.+)$", line) if not in_code else None
        if match:
            level, title = match.groups()
            if level == "#":
                if kind == "world":
                    out.append('<a id="world-package-reference"></a>')
                    line = "## World package reference"
                else:
                    out.append(f'<a id="{TOP_ANCHOR[kind]}"></a>')
                    line = f"## {heading}"
            elif kind in PREFIX:
                base = slug(title)
                number = seen.get(base, 0)
                seen[base] = number + 1
                anchor = f"{PREFIX[kind]}-{base}" + (f"-{number}" if number else "")
                out.append(f'<a id="{anchor}"></a>')
                line = f"{level}# {title}"
        out.append(line)
    return "\n".join(out).strip() + "\n"


INTRO = """# StationNet MUD creator handbook

This is the single-file handoff for authoring a complete, portable StationNet
MUD world. It contains the world ZIP format; race, class, skill, ability, and
effect definitions; authored rooms and content; NPCs, dialogue, quests, loot,
professions, events, wilderness, and procedural dungeons; and installation,
validation, export, and upgrade instructions. The complete example world at the
end can be copied into JSON files and imported on a clean station.

The handbook is assembled from the maintained format guides with
`python scripts/build_mud_creator_handbook.py`. Those guides remain useful
for focused reading, but this file contains their authoring instructions and
examples in full. No other README is required to create a world.

## Hand this brief to an agent or developer

1. Decide the setting, player start, progression, playable races and classes,
   skills, abilities, equipment, enemies, quest arc, and rewards. Use only the
   supported fields and mechanics in this handbook.
2. Build a UTF-8 ZIP with `manifest.json`, `rooms.json`, and `exits.json` at its
   root. Add `content.json` for authored content; add `origins.json`,
   `skills.json`, `abilities.json`, `effects.json`, `dungeons.json`,
   `events.json`, and `wilderness.json` as needed. Do not put a containing
   directory inside the ZIP.
   From the directory containing the files, for example, run
   `python -m zipfile -c my-world.zip manifest.json rooms.json exits.json content.json origins.json skills.json abilities.json effects.json dungeons.json events.json wilderness.json`,
   omitting any optional file you did not create.
3. Keep IDs stable and references local to the package. Define a referenced
   skill, ability, effect, item, NPC, room, or dungeon in the package whenever
   the world needs it to work on a fresh station.
4. Import the ZIP in **SYSOP Settings → MUD service → Worlds**. Review all
   validation messages and fix the reported JSON paths. Choose race, class,
   and skill availability, then enable the world for players.
5. Play through character creation, movement, dialogue, quest acceptance and
   turn-in, combat and loot, and each dungeon entrance. World Forge (the
   station's built-in editor) publishes edits directly. For later releases, re-import the same world
   ID as an upgrade; preserve IDs for content whose player state must carry
   over.

The package format supports complete content, but the engine owns the core
rules. The following reference lists what can be authored and what is fixed.
Do not invent unsupported JSON fields or script hooks.

## Contents

- [World package reference](#world-package-reference): manifest, rooms, exits,
  content, quests, triggers, wilderness, world life, packaging and upgrades
- [Race and class reference](#race-class-reference)
- [Skill reference](#skill-reference)
- [Ability and effect reference](#ability-effect-reference)
- [Dungeon theme reference](#dungeon-theme-reference)
- [Room preset reference](#room-preset-reference)
- [Complete example world](#complete-example-world)

"""


def example() -> str:
    directory = ROOT / "stationnet/mud_world_templates/example-world"
    files = [
        "manifest.json", "rooms.json", "exits.json", "content.json",
        "origins.json", "skills.json", "abilities.json", "effects.json",
        "dungeons.json", "events.json",
    ]
    result = [
        "<a id=\"complete-example-world\"></a>",
        "## Complete example world",
        "",
        "Save each block under the filename shown, in one directory. Zip the JSON",
        "files themselves at the archive root and import the ZIP through",
        "**SYSOP Settings → MUD service → Worlds**. This example is the",
        "repository's complete `lantern-isles` starter; change its world ID",
        "and author before distributing a distinct world.",
        "",
    ]
    for name in files:
        result += [f"### `{name}`", "", "```json", (directory / name).read_text(encoding="utf-8").strip(), "```", ""]
    return "\n".join(result)


def main() -> None:
    import sys
    parts = [INTRO]
    for kind, path, heading in GUIDES:
        parts += [convert(kind, path, heading), "\n"]
    parts.append(example())
    rendered = "".join(parts).rstrip() + "\n"
    anchors = set(re.findall(r'<a id="([^"]+)"></a>', rendered))
    anchors.update(slug(title) for title in re.findall(r"^#{1,6} (.+)$", rendered, flags=re.MULTILINE))
    targets = set(re.findall(r"\]\(#([^)]+)\)", rendered))
    missing = targets - anchors
    if missing:
        raise ValueError(f"Broken handbook links: {', '.join(sorted(missing))}")
    if "--check" in sys.argv[1:]:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != rendered:
            raise SystemExit(f"{OUTPUT.relative_to(ROOT)} is stale; run scripts/build_mud_creator_handbook.py")
        print(f"{OUTPUT.relative_to(ROOT)} is current")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
