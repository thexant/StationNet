# StationNet MUD creator handbook

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

<a id="world-package-reference"></a>
## World package reference

StationNet stores imported world ZIPs in `data/mud_worlds/` (or
`<STATIONNET_DATA>/mud_worlds/`). A station can run several worlds at once;
each enabled world appears on the MUD page as its own game, and players create
a separate character in every world they play. You can build a world with a text
editor. Put three UTF-8 JSON files at the root of a ZIP archive:
`manifest.json`, `rooms.json`, and `exits.json`. Add `content.json` for items,
NPCs, dialogue, quests, professions, trainers, and placements. Add `origins.json` for custom
races and classes, `skills.json` for custom skills, `abilities.json` and
`effects.json` for [custom abilities and timed effects](#abilities-and-effects-abilitiesjson-and-effectsjson),
`dungeons.json` for
procedural dungeons, `events.json` for [triggers](#5c-events-and-triggers-eventsjson)
that make the world react to players, and `wilderness.json` for large
[wilderness areas built from room presets](#5d-wilderness-and-room-presets-wildernessjson). Triggers are declarative settings: no
code or scripts run from a world package.

Archives are limited to 16 MB, each JSON file to 8 MB, 1000 authored rooms, and 5000
exits. Wilderness rooms do not count toward the room limit; a world may hold up
to 1,000,000 of them. `content.json` may have up to 5,000 records in each supported array.
The manifest may also turn on the optional [world life](#5f-world-life)
systems. The ZIP must contain only those three required files and optional
`content.json`, `origins.json`, `skills.json`, `abilities.json`, `effects.json`,
`dungeons.json`, `events.json`, and `wilderness.json`, all at its root. Imports validate references
and offer an [upgrade](#upgrade-an-installed-world) when the world ID is already installed.

A world package is meant to be complete: everything it needs to play, from its
races and classes to the effects its weapons apply, travels inside it, so a
station that has never seen it can import and play it without creating
anything by hand. **SYSOP Settings → MUD service → Worlds → DOWNLOAD EXAMPLE
WORLD ZIP** gives a small world that uses every file; its sources are in
`stationnet/mud_world_templates/example-world/`.

### What a package can and cannot change

| A package may define or tune | The engine keeps for itself |
| --- | --- |
| Rooms, exits, items, equipment, NPCs, merchants, banks, trainers, dialogue, quests, loot, factions, professions, wilderness, destinations | Attributes and their effects, the point-buy rules |
| Races, classes, skills (within the fixed skill effect types and practice triggers) | Equipment slots, item types, directions |
| Abilities and timed effects built from the fixed primitives below | Effect kinds, resistance categories, combat formulas |
| Progression dials within bounds (level cap, experience rate, health and mana per level, starting coins) | The experience curve, the maximum level of 50 |
| Mana's name or its absence; world life settings | Quest objective and reward types, trigger event and action types |
| Procedural dungeon templates, their rewards, scaling, and bosses | The dungeon generator and its layouts |

Manifest keys such as `max_level`, `attributes`, or `resistances` are rejected
as engine rules rather than silently ignored. Standalone race and class imports remain
available through the [origin guide](#race-class-reference).

## 1. Name the world and choose its entry room

`manifest.json`:

```json
{
  "format_version": 2,
  "id": "lantern-isles",
  "name": "Lantern Isles",
  "author": "Your name",
  "start_room": "harbor"
}
```

`format_version` says which package format the files follow. Packages without
it are format 1 and still import; checks added in format 2 (unknown manifest
keys, invalid effects on items and NPCs) are reported to them as warnings
instead of errors. Format 2 rejects anything it does not understand. A station
given a newer format than it reads says so instead of guessing. Exports always
write the newest format.

### Progression

A world may tune character growth within fixed bounds. Leave out any dial to
keep the engine's default:

```json
"progression": {"level_cap": 20, "experience_percent": 150, "health_per_level": 12,
                "mana_per_level": 3, "starting_coins": 50}
```

| Dial | Range | Default | Effect |
| --- | --- | --- | --- |
| `level_cap` | 1–50 | 50 | Characters stop leveling here; anyone already above keeps their level. |
| `experience_percent` | 10–500 | 100 | Scales every award before Intellect and skill bonuses. |
| `health_per_level` | 1–30 | 10 | Base health per level; Vitality still adjusts it. |
| `mana_per_level` | 0–10 | 2 | Base mana per level; Intellect still adjusts it. |
| `starting_coins` | 0–100000 | 30 | Coins in a new character's purse. |

In World Forge these are the **World settings → progression** fields; only a
SYSOP may publish changes to them. Procedural
portal dungeons always use the defaults.

### Availability

`"availability": {"races": ["islander"], "classes": ["lamplighter"], "skills": [...]}`
suggests which races, classes, and skills the world offers. Keys are the
package's own local keys or built-in keys. The importing sysop's choice still
wins; leave the list out to offer the world's bundled choices (or the built-ins
when it bundles none).
The world ID must be unique on the station. World, room, and content record IDs
match `^[a-z][a-z0-9-]{0,63}$`: 1 to 64 lowercase letters, digits, and hyphens,
starting with a letter. `stationnet` is reserved for the station's home
world, which is edited in World Forge. `name` is 2 to 80 characters; `author` is 1 to 80. `start_room` must
match a room ID in `rooms.json`.

### Give characters different beginnings

`start_room` is the world's default. To start some characters elsewhere, add
optional start rules to `mud_world_start_rules` in `content.json`. Worlds with a
single starting room need nothing here.

```json
{"mud_world_start_rules": [
  {"id": "elves", "room_id": "glade",
   "spawn_conditions_json": [{"type": "race", "keys": ["elf"]}]},
  {"id": "hill-scouts", "room_id": "watchtower",
   "spawn_conditions_json": [{"type": "race", "keys": ["dwarf", "gnome"]},
                             {"type": "class", "keys": ["scout"]}]},
  {"id": "wardens", "room_id": "barracks", "sort_order": -1,
   "spawn_conditions_json": [{"type": "member", "faction": "wardens"}]}
]}
```

Each rule names a `room_id` and at least one condition: `race`, `class`, or
`member` (the faction chosen at character creation). A rule uses each of these
once. Keys in one condition are alternatives, and all of a rule's conditions
must match. For several factions, use
`{"any": [{"type": "member", "faction": "a"}, {"type": "member", "faction": "b"}]}`.
`negate` and other condition types are not accepted. A rule with no conditions
is refused, because `start_room` already covers everyone else.

When several rules match a character:

1. The rule testing the most of race, class, and faction wins, so a race and
   class rule beats a race rule without needing an order.
2. Between equally specific rules, the lowest `sort_order` wins (default 0).
3. If those tie too, the room name decides, then the rule ID.

A character who matches no rule begins in `start_room`, as does one whose only
matching rules name rooms that are no longer valid. A faction rule only matches
factions offered at creation (`start_joinable`, with membership on).

Rules apply once, when a new character is created, using the finished race,
class, and faction. They never move existing characters, and changing a rule
later doesn't either. The room a rule gives is saved on the character, who also
recovers there after defeat. Characters that matched no rule recover in
`start_room`. Replacing a character's origins does not change either room, but
deleting a character and creating a new one applies the rules again.

In World Forge, start rules are a content collection (**Start rules**). A room
used by a rule cannot be deleted until the rule is moved or removed; **Used by**
on the room lists the rules that name it.

### Rename or disable mana

An optional `resource` object sets what this world calls mana, or turns it off:

```json
{
  "id": "rust-wastes",
  "name": "Rust Wastes",
  "author": "Your name",
  "start_room": "gate",
  "resource": {"enabled": true, "name": "Stamina"}
}
```

`name` is 1 to 20 letters, with single spaces, hyphens, or apostrophes between
words (for example `Energy`, `Power`, `Ki`, or `Soul Power`). Renaming changes
only the wording. The character sheet, sidebar meter, character creation, spell
costs, `/rest`, and help text use the new name, while costs, recovery, and
growth stay the same. With `"enabled": false`, the resource is hidden, and
`/spells` and `/cast` are unavailable in this world, so it suits settings
without magic. Characters keep their stored values, and races and classes that
grant spells or mana growth still install and play normally. A missing
`resource` means `{"enabled": true, "name": "Mana"}`. Builders can also change
this under **World settings → resource** in World Forge. Exports include the setting
whenever it differs from the default. Procedural dungeons always use the usual
Mana.

## 2. Write rooms

`rooms.json`:

Set optional `"bank": true` on an authored room to let characters use `/bank`
there. The default is `false`.

```json
[
  {
    "id": "harbor",
    "name": "Harbor Steps",
    "description": "Lanterns sway above the tide-worn steps."
  },
  {
    "id": "market",
    "name": "Night Market",
    "description": "Canvas stalls crowd a narrow square."
  }
]
```

Room IDs are unique within the ZIP and follow the same pattern as the world
ID. You need at least one room. Names must be 2–80 characters and descriptions
1–4000 characters.

A room may also stand on the [wilderness grid](#5d-wilderness-and-room-presets-wildernessjson):
add whole-number `"x"`, `"y"` and `"z"` together. `z` is its
[map layer](#map-layers-going-up-and-down). It keeps its own name, text,
exits and contents, and links automatically to neighbouring grid rooms on the
same layer. Add
`"grid_link": false` to stop the automatic links, or `"blocked_dirs": "north,east"`
to wall off particular sides. Two rooms cannot share a position.

## 3. Connect rooms

`exits.json`:

```json
[
  {"from": "harbor", "to": "market", "name": "market road", "direction": "north"},
  {"from": "market", "to": "harbor", "name": "harbor steps", "direction": "south"}
]
```

An exit is one-way; add its return route explicitly. `direction` can be
`north`, `northeast`, `east`, `southeast`, `south`, `southwest`, `west`,
`northwest`, `up`, `down`, `in`, or `out`. Omit it or set it to `null` for a
named exit. Names and nonempty directions must be unique among exits from the
same room. An empty exit list is valid for a one-room world.
`from` and `to` may also name a wilderness square as `"@x,y,z"`, such as
`{"from": "cave", "to": "@12,-4,0", "name": "cave mouth", "direction": "out"}`.
The same `"@x,y,z"` form works for every room reference in `content.json`,
`dungeons.json` and `events.json`.
Add `"key_item_id": "local-key-id"` to require a key item defined in
`content.json`. Add `"pick_difficulty": 30` (1–200) to make the lock pickable
with `/pick <exit>` and the Lockpicking skill; an exit may have a key, a
pickable lock, or both. Unlocking persists per character; `/look <exit>`
reports the lock state.

Give an exit an `"id"` (a lowercase ID, unique among exits) when a
[trigger](#5c-events-and-triggers-eventsjson) needs to open, seal, or hide it.
Add `"hidden": true` to keep it out of sight, and impassable, until a trigger
or room discovery reveals it.

An exit can also admit only some characters. Add `"conditions"` (the same
array format as [conditions](#conditions) below) and an optional
`"conditions_message"` of up to 200 characters shown to anyone turned away.
Barred exits stay visible and are marked `[barred]` for that player:

```json
{"from": "gate", "to": "deep-hall", "name": "dark stair", "direction": "down",
 "conditions": [{"type": "tag", "keys": ["darkvision"]}],
 "conditions_message": "The stair vanishes into perfect darkness."}
```

Two optional fields matter only in worlds with
[vehicles](#5h-vehicles-and-transportation). `"requires_mode": "water"` makes
the way passable only aboard a vehicle that travels by that mode (a sea
crossing, a rail line, a flight path); it shows as `[needs water]` to anyone
without one. `"allow_vehicles": false` makes it on foot only (a narrow door,
a ladder); riders must `/disembark` first. An exit cannot have both.

### Map layers: going up and down

A world can be built as a stack of separate maps called **layers**. Every room
with coordinates belongs to the layer named by its `z`: a city on layer `0`,
its sewers on `-1`, an office tower on `5`. Each layer has its own layout. Build
it from authored rooms with `x`, `y` and `z`, from squares painted in
[`wilderness.json`](#5d-wilderness-and-room-presets-wildernessjson), or both.
Rooms link automatically only to neighbours **on the same layer**. Layers never
link on their own, even where squares line up one above the other.

To join two layers, add an exit whose `direction` is `up` or `down`. It can lead
to any room on any other layer, so layers do not need to be stacked in order:
layer `1` can lead to layer `5`, and layer `10` to layer `-3`. A layer may have
as many ways up and down as you like. By convention `up` leads to a higher
number, but nothing enforces it. As with every exit, add the way back
explicitly.

```json
[
  {"from": "manhole", "to": "sewer-junction", "name": "ladder", "direction": "down"},
  {"from": "sewer-junction", "to": "manhole", "name": "ladder", "direction": "up"},
  {"from": "lift-lobby", "to": "office-lobby", "name": "elevator", "direction": "up"},
  {"from": "office-lobby", "to": "lift-lobby", "name": "elevator", "direction": "down"},
  {"from": "@4,7,0", "to": "@4,7,-1", "name": "storm drain", "direction": "down", "hidden": true}
]
```

Rules:

- An exit between two rooms with coordinates on **different** layers must use
  `up` or `down`. A `north`, `in`, `out` or named exit across layers is
  rejected on import and in World Forge.
- `up` and `down` exits may also join rooms on the same layer, or rooms without
  coordinates, such as the floors of a tower that has no map.
- Rooms without coordinates belong to no layer. Exits to and from them are not
  restricted, and `/map` is unavailable inside them.
- Walls (`blocked_dirs`) apply only to the eight compass directions, because
  `up` and `down` are never automatic.

Layers can have names. Add an optional `layers` list to `manifest.json`, with
each `z` at most once (up to 200 entries, each name 2–80 characters):

```json
"layers": [{"z": 0, "name": "Old City"}, {"z": -1, "name": "Sewers"},
           {"z": 5, "name": "Meridian Tower"}]
```

Names appear in `/map` and in World Forge, and exports keep them. A layer
without a name is shown by number.

What players see:

- **`/map`** shows only the layer the player is on, headed with its name and
  number: `Map around Plaza (0, 0, Old City, layer 0); north is up.` A room with
  a way up is marked `<`, a way down `>`, and both `&`. Hidden exits stay
  unmarked until a trigger or discovery reveals them to that player.
- **`/directions`** describes the route only as far as the first way up or
  down. It ends with, for example, *Then take the ladder down to Sewer Junction.
  Once you have gone down, ask for directions again.* On the new layer,
  `/directions` (or any move, while a destination is chosen) continues from
  there.
- **Movement** is ordinary: `/go up`, `up`, or `u`, and `down` or `d`.

In World Forge, the map's layer menu switches layers and **+** adds one; layer
names are the manifest's `layers`. A passage may lead to any wilderness square
by its `@x,y,z` ID. World Forge refuses a passage, or a room move, that would
join two layers by any direction other than `up` or `down`.

## 4. Add optional world content

### Magic, mana, and timed effects

Item and NPC definition `properties_json` may include shared effects. A consumable
can use `"on_use_effects"`; equipped weapons and hostile NPCs can use
`"on_hit_effects"`. Items can also cure effects (`"cures"`), and equipment can
resist them (`"resistances"`); see
[Protective gear, cures, and hazards](#protective-gear-cures-and-hazards). For example:

```json
{"on_hit_effects": [{"key": "poison", "potency": 2, "duration": 3}]}
```

Built-in keys are `poison`, `burning`, `bleeding` (damage each combat turn),
`regeneration` (healing each turn), `might` and
`weakness` (attack), `ward` and `vulnerability` (defense), `focus` and `blinded`
(accuracy), or `evasive` and `snared` (evasion). A key may also name one of the
world's own effects from `effects.json`. Potency is 1–1000
and duration is 1–100 turns. Reapplying a key keeps its stronger potency and
longer remaining duration. Classes and races grant abilities through their
`spells` list. Built-in abilities are `ember` (8 mana), `venom` (7), `rend`
(6), `hex` (7), `mend` (7), `ward` (6), and `might` (6); the first four target
an enemy and the last three the caster. A world adds more in `abilities.json`. Players use `/spells`,
`/cast <spell> [enemy]`, and `/rest` to recover mana outside combat when no
hostile enemy is nearby. Intellect improves mana and spell strength. Worlds
can rename mana or disable it and its spells; see
[Rename or disable mana](#rename-or-disable-mana). See the
[race and class guide](#race-class-reference) for class setup.

### Abilities and effects (`abilities.json` and `effects.json`)

A world can add abilities and effects built only from the engine's fixed
parts, so they always work in play:

```json
{"effects": [
  {"key": "searing-light", "name": "Searing Light", "kind": "damage", "resist_as": "burning"},
  {"key": "tide-blessing", "name": "Tide Blessing", "kind": "regen"},
  {"key": "brine-chill", "name": "Brine Chill", "kind": "defense", "sign": -1}
]}
```

An effect's `kind` is `damage` or `regen` (each combat turn), or `attack`,
`defense`, `accuracy`, or `evasion` with `sign` 1 (bonus) or -1 (penalty).
Harmful effects may name a `resist_as` resistance (`physical`, `poison`,
`burning`, `bleeding`, `weakness`, `vulnerability`, `blinded`, `snared`) so race,
class, and gear resistances weaken them. Equipment can also resist an effect by
its own key, so a gas mask can stop `radiation` without stopping snake venom.
Effects normally act only on combat turns (and one turn on `/rest`). Set
`"outside_combat": true` to make an effect also act every 30 seconds while its
target is out of combat. Players see the `name`.

```json
{"abilities": [
  {"key": "lantern-flare", "name": "Lantern Flare", "command": "flare", "target": "enemy", "cost": 6,
   "damage": 7, "effects": [{"key": "searing-light", "potency": 2, "duration": 3}]},
  {"key": "tide-prayer", "name": "Tide Prayer", "target": "self", "cost": 5, "heal": 6,
   "effects": [{"key": "tide-blessing", "potency": 2, "duration": 3}]}
]}
```

An ability strikes one enemy (`damage` 1–200 plus harmful effects) or acts on
its caster (`heal` 1–200 and/or helpful effects), costs 0–100 of the world's
resource, and applies up to four effects. Players cast it with
`/cast <command or name>`. Grant it with a race or class `spells` list, by its
local key. Keys may not reuse a built-in ability or effect key. Items, NPCs,
triggers (`apply_effect`), and dungeon templates apply effects by key too.
The same definitions can be installed station-wide from **SYSOP Settings →
Abilities & Effects**; see the [ability and effect reference](#ability-effect-reference).

`content.json` is an object whose keys are authored MUD table names and whose
values are arrays of records. IDs are **local to the ZIP** and can be short
names such as `lantern` or `keeper` (with the same lowercase ID rule as rooms);
references use those local IDs. Rooms use
the IDs from `rooms.json`. StationNet gives every imported record a unique
internal ID, so two worlds may both have an item called `lantern`.

Equipment can declare requirements in `properties_json`, for example
`{"attack_bonus": 4, "requirements": {"strength": 14, "classes": ["warrior"]}}`.
Players can inspect these requirements and must meet all of them before
equipping the item.

| Key | Value | Met when |
| --- | --- | --- |
| `strength`, `agility`, `intellect`, `vitality` | 1–30 | The attribute is at least this value. |
| `level` | 1–1000 | The character is at least this level. |
| `races`, `classes`, `tags` | Array of lowercase keys | The character's race, class, or one of its origin tags is listed. |
| `not_races`, `not_classes`, `not_tags` | Array of lowercase keys | The character's race, class, and tags are all absent from the list. |

Race and class keys may name built-in choices, bundled `origins.json` entries
(by local key, mapped on import), or choices the station might install later;
they are not required to exist. Tags come from the character's race and class
definitions, so `"tags": ["darkvision"]` suits any origin that declares that
trait. Existing equipment without requirements remains usable by all
characters. Gear becomes inactive if a character no longer meets its
requirements on the current progression track. Strength improves equipped melee weapon bonuses,
Agility improves firearm bonuses, and Vitality improves equipped armor bonuses.

Equipment may also set a proficiency `category`, for example
`{"attack_bonus": 6, "category": "blade"}`. The default skills cover `blade`,
`blunt`, `axe`, `firearm`, `light`, `heavy`, and `shield`, and custom skills can
add more. A character without a matching proficiency skill gets three quarters
of the item's bonuses; items without a category are never penalized.

### Firearms and ammunition

Define a firearm as `item_type: "equipment"` in `main_hand` or `off_hand` with
`properties_json.firearm: true`, a nonblank `ammo_type` of at most 64
characters, and an integer `magazine_size` from 1 to 100. Define matching
ammunition as `item_type: "misc"` with the same `ammo_type` and `rounds` from
1 to 1,000. Ammunition must not have `magazine_size`; a firearm must not have
loose `rounds`. A firearm begins empty. `/reload [gun]` transfers carried
rounds into it and costs a combat turn. `/fire <enemy>` requires a loaded gun;
`/attack` fires the loaded equipped gun and otherwise uses melee; `/melee`
forces melee. Shots consume rounds and use Agility. NPCs equipped with guns
also use the magazine and reload cycle, with abstract ammunition supply.

The following **complete `content.json`** works with the `manifest.json`,
`rooms.json`, and `exits.json` above. It adds a quest giver, a hostile NPC, a
quest, a gun and ammunition, an NPC weapon, and a combat drop. It is a useful
starting point even when there is no existing world to export.

```json
{
  "mud_item_definitions": [
    {"id": "pistol", "name": "Harbor pistol", "description": "A six-shot sidearm.",
     "item_type": "equipment", "equipment_slot": "main_hand",
     "properties_json": {"firearm": true, "ammo_type": "9mm",
                         "magazine_size": 6, "attack_bonus": 4, "value": 120}},
    {"id": "cartridges", "name": "9mm cartridges", "description": "A box of rounds.",
     "item_type": "misc", "properties_json": {"ammo_type": "9mm",
                                                   "rounds": 18, "value": 30}},
    {"id": "fang", "name": "Rat fang", "description": "A sharp trophy.",
     "item_type": "misc", "properties_json": {"value": 5}}
  ],
  "mud_item_instances": [
    {"id": "dock-pistol", "definition_id": "pistol", "room_id": "harbor"},
    {"id": "dock-ammo", "definition_id": "cartridges", "room_id": "harbor"}
  ],
  "mud_conversations": [
    {"id": "harbor-greeting", "name": "Harbor greeting"}
  ],
  "mud_dialogue_nodes": [
    {"id": "greeting-start", "conversation_id": "harbor-greeting",
     "node_key": "start", "body": "The market needs clearing.", "is_start": 1}
  ],
  "mud_quests": [
    {"id": "visit-market", "name": "Visit the market",
     "description": "Find the night market."}
  ],
  "mud_npc_definitions": [
    {"id": "keeper", "name": "Harbor keeper", "description": "A patient watcher.",
     "conversation_id": "harbor-greeting"},
    {"id": "rat", "name": "Dock rat", "description": "It bares its teeth."}
  ],
  "mud_npc_instances": [
    {"id": "dock-keeper", "definition_id": "keeper", "room_id": "harbor"},
    {"id": "market-rat", "definition_id": "rat", "room_id": "market"}
  ],
  "mud_npc_combat_profiles": [
    {"npc_definition_id": "rat", "maximum_health": 18, "attack": 4,
     "defense": 1, "experience_reward": 5, "respawn_seconds": 60}
  ],
  "mud_npc_combat_rewards": [
    {"id": "rat-fang-drop", "npc_definition_id": "rat",
     "reward_type": "item", "target_id": "fang", "amount": 1}
  ],
  "mud_quest_objectives": [
    {"id": "reach-market", "quest_id": "visit-market",
     "objective_type": "visit_room", "target_id": "market",
     "description": "Reach the night market", "required_count": 1}
  ],
  "mud_quest_rewards": [
    {"id": "market-reward", "quest_id": "visit-market",
     "reward_type": "experience", "amount": 20}
  ],
  "mud_quest_givers": [
    {"quest_id": "visit-market", "npc_definition_id": "keeper"}
  ],
  "mud_dialogue_responses": [
    {"id": "accept-market", "node_id": "greeting-start",
     "response_text": "I'll visit the market.", "accept_quest_id": "visit-market"}
  ]
}
```

The supported arrays are:

| Array | What it contains | Main references |
| --- | --- | --- |
| `mud_item_definitions` | Item templates, properties, equipment slots | `id` |
| `mud_item_instances` | Items in rooms or containers | `definition_id`, `room_id` or `container_id` |
| `mud_conversations` | Dialogue groups | `id` |
| `mud_dialogue_nodes` | Dialogue text and starting nodes | `conversation_id` |
| `mud_dialogue_responses` | Choices, conditions, outcomes, quest acceptance and turn-in | `node_id`, `next_node_id`, `accept_quest_id`, `turn_in_quest_id` |
| `mud_npc_definitions` | NPC templates and behavior | `conversation_id` |
| `mud_npc_instances` | NPCs placed in rooms | `definition_id`, `room_id` |
| `mud_npc_combat_profiles` | Health, attack, defense, XP, respawn | `npc_definition_id` |
| `mud_npc_equipment` | NPC equipment slots | `npc_definition_id`, `item_definition_id` |
| `mud_npc_combat_rewards` | Probabilistic item, XP, or coin drops | `npc_definition_id`, `target_id` for items |
| `mud_quests` | Quests, prerequisites, completion rules | `prerequisite_quest_id`, `turn_in_npc_definition_id` |
| `mud_quest_objectives` | Talk, visit, possess, defeat, or deliver objectives | `quest_id`, `target_id`, `recipient_npc_definition_id` |
| `mud_quest_rewards` | Quest item, XP, or coin rewards | `quest_id`, `target_id` for items |
| `mud_npc_shops` | NPC merchant settings | `npc_definition_id`, `buys_items`, `sell_percent` |
| `mud_npc_banks` | NPCs offering banking | `npc_definition_id` |
| `mud_shop_stock` | Merchant merchandise and prices | `npc_definition_id`, `item_definition_id`, `price`, optional `quantity` |
| `mud_item_spawns` | Renewable room-floor items | `definition_id`, `room_id`, `respawn_seconds` |
| `mud_gathering_nodes` | Harvesting, mining, scavenging, or collecting sources | `room_id`, `tool_definition_id`; reward item IDs in `rewards_json` |
| `mud_room_discoveries` | One-time per-character search clues and items | `room_id`, `clue`, optional `item_definition_id`, optional `difficulty` |
| `mud_npc_trainers` | Skills an NPC teaches for coins | `npc_definition_id`, `skill_key`, `price`, `max_rank` |
| `mud_professions` | Professions joined through a recruiter | `recruiter_npc_definition_id` |
| `mud_work_activities` | Immediate work and accepted jobs | `profession_id`; one room or provider NPC |
| `mud_work_rewards` | Coins, experience, and item pay | `activity_id`, optional item `target_id` |
| `mud_quest_givers` | NPCs that offer quests | `quest_id`, `npc_definition_id` |
| `mud_world_destinations` | Places players can ask the way to with `/directions` | `room_id`, `map_items_json`; see [wayfinding](#5e-wayfinding-destinations) |
| `mud_world_factions` | Factions players earn standing with; see [factions](#factions-and-reputation) | `id` |
| `mud_faction_relations` | How a change with one faction passes to another | `faction_id`, `other_faction_id` |
| `mud_properties` | Homes and other property players can hold; see [housing](#5g-player-housing-and-storage) | `entrance_room_id`, optional `agent_npc_definition_id` |
| `mud_vehicle_definitions`, `mud_vehicle_placements`, `mud_fuel_stations`, `mud_transit_lines`, `mud_transit_stops` | Vehicles, where public ones stand, fuel, and transit services; see [vehicles](#5h-vehicles-and-transportation) | `interior_room_id`, `fuel_item_id`, `vehicle_definition_id`, `room_id`, `cabin_room_id`, `line_id` |
| `mud_property_rooms` | Which rooms belong to a property | `room_id`, `property_id` |
| `mud_storage_points` | Lockers, chests, and owners' storage | `room_id` |

Only these fields are accepted for each array; omitted optional fields use
database defaults. IDs and references are local to this ZIP. All references
must point to records in the same package, including room IDs in `rooms.json`.

| Array | Accepted fields beyond `id` |
| --- | --- |
| `mud_item_definitions` | `name`, `description`, `item_type`, `properties_json`, `behaviors_json`, `equipment_slot` |
| `mud_item_instances` | `definition_id`, `room_id`, `container_id`, `is_open`, `state_json` |
| `mud_conversations` | `name` |
| `mud_dialogue_nodes` | `conversation_id`, `node_key`, `body`, `is_start` |
| `mud_dialogue_responses` | `node_id`, `response_text`, `next_node_id`, `accept_quest_id`, `deliver_item_id`, `turn_in_quest_id`, `required_state_key`, `required_state_value`, `state_key`, `state_value`, `sort_order`, `conditions_json`, `outcomes_json` |
| `mud_npc_definitions` | `name`, `description`, `conversation_id`, `properties_json`, `behaviors_json`, `faction_id` |
| `mud_npc_instances` | `definition_id`, `room_id`, `state_json` |
| `mud_npc_combat_profiles` | `npc_definition_id`, `maximum_health`, `attack`, `defense`, `experience_reward`, `respawn_seconds`, optional `accuracy`, `evasion` |
| `mud_npc_equipment` | `npc_definition_id`, `equipment_slot`, `item_definition_id` |
| `mud_npc_combat_rewards` | `npc_definition_id`, `reward_type`, `target_id`, `amount`, `chance_percent` |
| `mud_quests` | `name`, `description`, `prerequisite_quest_id`, `completion_mode`, `turn_in_npc_definition_id`, `sequential`, `abandonable`, `conditions_json`, optional `summary`, `details_json`, `time_limit_seconds`, `fail_conditions_json`, `retry_mode`, `retry_cooldown_seconds` |
| `mud_quest_objectives` | `quest_id`, `objective_type`, `target_id`, `description`, `required_count`, `sort_order`, `optional`, `consume_on_turn_in`, `recipient_npc_definition_id`, optional `hidden`, `outcome_key`, `fail_conditions_json` |
| `mud_quest_rewards` | `quest_id`, `reward_type`, `target_id`, `amount`, optional `outcome` |
| `mud_npc_shops` | `npc_definition_id`, `buys_items`, `sell_percent` (no `id`) |
| `mud_npc_banks` | `npc_definition_id` (no `id`) |
| `mud_shop_stock` | `npc_definition_id`, `item_definition_id`, `price`, `quantity`, `conditions_json` |
| `mud_item_spawns` | `definition_id`, `room_id`, `respawn_seconds` |
| `mud_gathering_nodes` | `room_id`, `name`, `activity`, `rewards_json`, `skill_key`, `min_rank`, `tool_definition_id`, `success_percent`, `respawn_seconds` |
| `mud_room_discoveries` | `room_id`, `clue`, `item_definition_id`, `difficulty` |
| `mud_npc_trainers` | `npc_definition_id`, `skill_key`, `price`, `max_rank` |
| `mud_professions` | `name`, `description`, `recruiter_npc_definition_id`, `conditions_json` |
| `mud_work_activities` | `profession_id`, `name`, `description`, `kind`, `room_id`, `provider_npc_definition_id`, `conditions_json`, `tool_definition_id`, `practice_skill_key`, `cooldown_seconds`, `objective_type`, `objective_target_id`, `required_count` |
| `mud_work_rewards` | `activity_id`, `reward_type`, `target_id`, `amount` |
| `mud_quest_givers` | `quest_id`, `npc_definition_id` (no `id` field) |
| `mud_world_destinations` | `room_id`, `name`, `aliases`, `category`, `description`, `public`, `discover_on_visit`, `map_items_json`, `conditions_json`, `sort_order` |
| `mud_world_factions` | `name`, `description`, `default_reputation`, `defeat_reputation`, `public`, `ranks_json`, `sort_order` |
| `mud_properties` | `name`, `description`, `kind`, `entrance_room_id`, `acquisition`, `price`, `refund_percent`, `rent`, `rent_period_seconds`, `grace_seconds`, `prepay_periods`, `max_owners`, `agent_npc_definition_id`, `conditions_json`, `available` |
| `mud_property_rooms` | `room_id`, `property_id` (no `id`) |
| `mud_storage_points` | `room_id`, `name`, `description`, `scope`, `capacity`, `item_types`, `conditions_json` |
| `mud_faction_relations` | `faction_id`, `other_faction_id`, `share_percent` |

`mud_npc_combat_profiles` and `mud_npc_equipment` also have no `id` field;
their NPC reference is their key. An item instance must be in a room **or**
inside another placed item container. Put the containing item in the same
array; it is installed before its children regardless of JSON order. Set
`is_open` to `0` or `1`. An NPC instance needs a room. Item types are `misc`,
`equipment`, `container`, `consumable`, and `key`; equipment slots are `head`,
`body`, `hands`, `legs`, `feet`, `main_hand`, `off_hand`, and `accessory`.

For NPC combat, `maximum_health` is 1 to 1,000,000; `attack` is 1 to 100,000;
`defense` and `experience_reward` are 0 to 100,000; `respawn_seconds` is 5
to 86,400. Optional `accuracy` and `evasion` are 0 to 100 (omitted or `null`
means 0). Every attack rolls to hit at 85% + accuracy − evasion, kept between
30% and 95%, and `defense` then reduces the damage of hits that land. Older
packages without these fields import unchanged. A profile makes the NPC fightable; omit it for a purely
conversational NPC. A combat reward uses `reward_type: "experience"` or
`"coins"` with no `target_id`, or `"item"` with an item definition `target_id`.
`amount` is 1 to 100,000; `chance_percent` is 1 to 100 and defaults to 100.
NPC equipment uses one item definition and one
equipment slot per NPC; the item should be equipment for that slot.

Give each conversation one start node (`is_start: 1`) and a distinct
`node_key` per node. Responses point to a `node_id` and may name a
`next_node_id` in the same conversation. `sort_order` controls display order.
A response can do several things when chosen:

- `accept_quest_id` starts a quest. The response is shown only while the
  player can accept it: not already taken, prerequisite done, quest
  conditions met.
- `turn_in_quest_id` turns in a `turn_in` quest. The response is shown only
  when that quest is ready and the speaking NPC is its turn-in NPC.
- `deliver_item_id` hands over one carried item for an open, unlocked delivery
  objective whose recipient is the speaking NPC. The response is shown only
  then.
- `state_key` / `state_value` save per-player conversation state with this NPC.
- `outcomes_json` grants or charges things (see below).

A response is visible only when its legacy `required_state_key` /
`required_state_value` pair matches **and** every entry in `conditions_json`
holds. Because quest responses hide when they cannot happen, response numbers
can shift as a quest progresses; players may also answer by response text.
To offer a quest through a character, include both a `mud_quest_givers` link
and a dialogue response accepting that quest. Players can also use
`/quest accept <name>` beside any giver. `/talk` to a giver or turn-in NPC
lists quests offered and quests ready to turn in there, even when the NPC has
no conversation.

### Quests

Quest objectives support `talk_npc` and `defeat_npc` (target an NPC
**definition**), `visit_room` (target a room ID), `possess_item` and
`deliver_item` (target an item **definition**), and `script`. A `script`
objective advances only when a trigger's `complete_objective` action names it;
its `target_id` is any short label, such as `"script"`. `talk_npc` advances when the
player starts a conversation with `/talk`; accepting a quest in dialogue does
not count as talking to the giver. Deliveries use `/deliver <item>` (or a
`deliver_item_id` response) beside the objective's `recipient_npc_definition_id`,
or beside one of the quest's givers when no recipient is set, and consume the
item. `required_count` is 1 to 100. A quest may name another local quest in
`prerequisite_quest_id`; prerequisite chains cannot form cycles. Quest
rewards use `reward_type: "experience"` or `"coins"` with no `target_id`, `"item"`
with an item definition `target_id`, or `"reputation"` with a faction `target_id`
and an `amount` of standing from 1 to 1000; `amount` is at least 1.

| Quest field | Values | Meaning |
| --- | --- | --- |
| `completion_mode` | `auto` (default) or `turn_in` | `auto` completes and pays the moment the required objectives are done. `turn_in` marks the quest **ready** instead; the player returns to an NPC and turns it in to receive the rewards. |
| `turn_in_npc_definition_id` | NPC definition ID or omitted | The NPC that accepts the turn-in. Omit it to accept turn-ins from any of the quest's givers. |
| `sequential` | `0` (default) or `1` | With `1`, objectives unlock in stages by `sort_order`. Objectives that share a `sort_order` run together; a stage opens once every required objective in earlier stages is complete. Locked objectives do not advance. Items already carried count as soon as their stage unlocks. |
| `abandonable` | `1` (default) or `0` | Whether `/quest abandon <name>` may drop the active quest. Abandoning clears its progress and records the quest as **abandoned**; it can be accepted again unless it is a one-attempt quest. Items already delivered are not returned. |
| `conditions_json` | Condition array (default `[]`) | Who may accept the quest. See the condition reference below. |
| `summary` | Text up to 300 characters (default empty) | The quest's overall goal, shown as **Goal:** above the individual tasks. Keep `description` for the story and `summary` for what "done" means. |
| `details_json` | Detail array (default `[]`) | Extra notes revealed as the quest goes on. See [Quest details](#quest-details). |
| `time_limit_seconds` | 60 to 2,592,000 (30 days), or omitted | A deadline counted in real time from acceptance; it keeps running while the player is offline. When it passes, the quest fails with "Time ran out." |
| `fail_conditions_json` | Failure rule array (default `[]`) | The quest fails as soon as every condition of any rule holds. See [Failing quests](#failing-quests). |
| `retry_mode` | `retry` (default) or `once` | What happens after a failure. `retry` lets the player accept the quest again; `once` allows a single attempt per character, so failing **or abandoning** closes the quest for good. |
| `retry_cooldown_seconds` | 0 (default) to 2,592,000 | `retry` quests only: how long after failing before the quest can be accepted again. |

| Objective field | Values | Meaning |
| --- | --- | --- |
| `sort_order` | Integer | Display order and, in sequential quests, the stage number. |
| `optional` | `0` (default) or `1` | Optional objectives are tracked but never block completion. |
| `consume_on_turn_in` | `0` (default) or `1` | `possess_item` only. At turn-in the player must still carry `required_count` of the item (unequipped, not run loot), and those items are taken. |
| `recipient_npc_definition_id` | NPC definition ID or omitted | `deliver_item` only. Who receives the delivery. |
| `hidden` | `0` (default) or `1` | A hidden objective is left out of the player's quest log until it shows progress, is finished, or fails. Use it for secrets and surprise twists. |
| `outcome_key` | Lowercase key such as `spared`, or omitted | Makes the objective an **alternative ending**: finishing it resolves the whole quest with that outcome. It cannot also be `optional`, and `success` and `failed` are reserved. |
| `fail_conditions_json` | Failure rule array (default `[]`) | This objective fails when a rule holds. See [Failing quests](#failing-quests). |

| Reward field | Values | Meaning |
| --- | --- | --- |
| `outcome` | Omitted (default), an objective's `outcome_key`, or `failed` | Omitted pays on any successful ending. An outcome key pays only when the quest ends that way. `failed` pays when the quest fails, once per character however often it is retried; use it for consolation prizes, and the `quest_failed` trigger event for penalties. |

Rewards are paid exactly once per character. Repeated turn-ins, repeated
dialogue choices, and completed quests cannot pay again. Players manage quests
with `/quests`, `/quests completed`, `/quests failed`, `/quests history`,
`/quest available`, `/quest info <name>`, `/quest accept <name>`,
`/quest turnin <name>`, and `/quest abandon <name>`. `/quest info` shows the
goal, who gave the quest and who takes the turn-in, the details revealed so
far, current tasks before finished ones, any time left, and how the quest
ended.

Every field above the objective table except the original ones is optional.
A quest that uses none of them never fails and behaves exactly as before.

#### Quest details

`details_json` lists up to 12 notes:

```json
[{"kind": "background", "text": "Wolves have taken three sheep.", "reveal": "offer"},
 {"kind": "location", "text": "The den lies north of the woods.", "room": "woods"},
 {"kind": "consequence", "text": "The elder will not forget cowardice.", "reveal": "finished"},
 {"kind": "note", "text": "The pack leader has a white muzzle.",
  "reveal": [{"type": "objective", "objective": "track"}]}]
```

`kind` is `background`, `location`, `consequence`, or `note`. Only `location`
may name a `room`, which is shown by name. `reveal` says when the player sees
the note: `offer` (already while deciding whether to accept), `accepted` (the
default, once on the quest), `finished` (after it is completed or failed), or a
condition array that must hold. Nothing hidden is ever sent to the player's
browser, so details and hidden objectives never spoil anything early.

#### Failing quests

A quest can fail four ways:

- **Deadline.** `time_limit_seconds` passes.
- **Failure rules.** Any rule in `fail_conditions_json` holds. Each rule is
  `{"when": [conditions], "message": "Shown to the player"}`; `message` is
  optional. Rules are checked after every action the player takes.
- **Triggers.** A trigger's `fail_quest` action fails the quest on any event:
  defeating the wrong NPC, a dialogue choice, a crime, entering a room, and so
  on. `fail_objective` fails a single objective.
- **Impossibility.** When world edits delete what a required objective targets,
  or a turn-in quest loses every NPC who could take it, the quest fails with
  "The world has changed; this quest can no longer be completed."

When an objective fails, an optional objective or one alternative ending just
closes; a failed required objective, or closing every way to finish the quest,
fails the whole quest. A failed quest can never be progressed, readied, or
turned in, so nothing pays twice by accident. Its record stays, with the
reason shown in `/quest info` and `/quests failed`. Accepting it again (when
`retry_mode` allows and any cooldown has passed) starts a fresh attempt with
cleared progress and a new deadline.

```json
{"id": "escort", "name": "Escort the Elder", "description": "Walk the elder to the shrine.",
 "summary": "Bring the elder safely to the shrine", "time_limit_seconds": 1800,
 "retry_mode": "retry", "retry_cooldown_seconds": 3600,
 "fail_conditions_json": [{"when": [{"type": "flag", "scope": "world", "key": "elder.dead"}],
                           "message": "The elder has fallen."}]}
```

#### Alternative endings

Give objectives an `outcome_key` to offer several ways to resolve a quest. The
first one finished decides the outcome; plain required objectives still give
the ordinary `success` ending, but a quest built only from alternative endings
waits for one of them. Turn-in quests remember the outcome until they are
turned in. Rewards with a matching `outcome` pay only for that ending, and the
`quest` condition's `outcome` field lets later dialogue, quests, and triggers
react to how a quest ended.

### Conditions

`conditions_json` is an array; **all** entries must hold. Any entry may add
`"negate": true` to invert it. `{"any": [ ... ]}` holds when at least one of
its entries holds; groups cannot be nested. A list holds up to 20 entries.

| Type | Fields | Holds when |
| --- | --- | --- |
| `level` | `min` and/or `max` (1–1000) | The character's level is within the bounds. |
| `race` / `class` | `keys`: array of origin keys (or a single `key`) | The character's race/class is one of them. Built-in keys such as `human` or `warrior` work, and bundled `origins.json` keys are mapped on import. |
| `tag` | `keys`: array of tags (or a single `key`) | The character's race or class declares at least one of these tags. |
| `has_item` | `item`, optional `count` (default 1) | The character carries at least `count` of the item definition. |
| `coins` | `min` | The character has at least `min` coins. |
| `attribute` | `attribute` (`strength`, `agility`, `intellect`, `vitality`), `min` | The attribute is at least `min`. |
| `skill` | `skill` key, optional `min_rank` (default 1) | The skill rank is at least `min_rank`. |
| `quest` | `quest`, `status`: one or more of `not_started`, `active`, `ready`, `completed`, `failed`, `abandoned` (default `completed`); optional `outcome`: one or more of `success`, an objective's `outcome_key`, or `failed` | The character's quest status matches. `ready` means a `turn_in` quest whose objectives are done. `not_started` also matches abandoned quests, as it did before abandonment was recorded. With `outcome`, the quest must also have ended (or be ready) that way. |
| `objective` | `objective` | That quest objective is complete. |
| `state` | `key`, `value` | This NPC's saved per-player state for `key` equals `value` (`""` when unset). |
| `claimed` | none | This response's outcomes have already been granted. Use it with `negate` to hide a one-time offer after it is claimed. |
| `flag` | `scope` (`player`, the default, or `world`), `key`, and optionally `value` or `min`/`max` | With `value`, the flag equals that text. With `min`/`max`, the flag read as a number (unset is 0) is within the bounds. With neither, the flag is set. Triggers set flags; see [events](#5c-events-and-triggers-eventsjson). |
| `object_state` | `object` (a placed item), `state` | The object's current state equals `state` (`""` until changed). |
| `in_room` | `room` | The character is in that room. |
| `knows_destination` | `destination` | The character has learned that [destination](#5e-wayfinding-destinations) (by visiting, dialogue, a trigger, or reading). Carrying a map does not count. |
| `reputation` | `faction`, and `rank` (one or a list of rank keys) or `min`/`max` (-1000 to 1000) | The character's [standing](#factions-and-reputation) with the faction matches. |
| `wanted` | optional `faction`, optional `min_fine` (default 1) | The character owes at least `min_fine` coins in unpaid [fines](#crime-and-consequences), to that faction or to any. Never holds while crime is off; add `"negate": true` to bar wanted characters. |
| `member` | `faction` | The character [belongs](#faction-membership) to the faction. Never holds while membership is off. |
| `disposition` | optional `npc` (definition), and `tier` (one or a list of `hostile`, `unfriendly`, `neutral`, `friendly`, `trusted`) or `min`/`max` (-100 to 100) | That NPC's [regard](#relationships) for the character matches. Without `npc` it means the NPC being spoken to. Never holds while relationships are off. |
| `time` | `from`, `to` (hours 0–23) | The [world clock](#world-clock) reads an hour from `from` up to, but not including, `to`. `{"from": 20, "to": 4}` wraps past midnight. Never holds while the clock is off. |
| `owns_property` | `property`, optional `role`: `owner` (default; primary or co-owner) or `primary` | The character holds or shares that [property](#5g-player-housing-and-storage). |
| `aboard` | optional `vehicle`, optional `mode` | The character is aboard a [vehicle](#5h-vehicles-and-transportation): any, that kind, or one that travels by that mode. |
| `has_vehicle` | `vehicle` | The character owns a vehicle of that kind. |

Flag keys are 1 to 80 lowercase letters, digits, and `. _ : -`, such as
`gate.open` or `rats_slain`. Inside a procedural dungeon, `world` flags belong
to that run. `object_state`, `in_room`, `knows_destination`, `time`,
`disposition`, `reputation`, `wanted`, `member`, `owns_property`, `aboard`, and `has_vehicle` are not available in dungeon templates.

### Outcomes

`outcomes_json` on a dialogue response is an array of up to 10 entries. They
apply the **first** time a character chooses the response. Later choices still
move the conversation but grant and charge nothing. Costs are checked before
anything changes: if the player cannot pay, the response is refused and
nothing is granted.

| Type | Fields | Effect |
| --- | --- | --- |
| `give_item` | `item`, `amount` (1–100, default 1) | Gives items. |
| `take_item` | `item`, `amount` (1–100, default 1) | Takes carried, unequipped items (refused if too few). |
| `experience` | `amount` (1–100,000) | Awards experience (Intellect and Lore bonuses apply). |
| `coins` | `amount` (1–100,000) | Gives coins. |
| `take_coins` | `amount` (1–100,000) | Charges coins (refused if too few). |
| `learn_destination` | `destination` | Teaches the way to a [destination](#5e-wayfinding-destinations). Not available in dungeon templates. |
| `reputation` | `faction`, `amount` (-1000 to 1000, not 0) | Changes the player's [standing](#factions-and-reputation) with a faction, and with related factions. Not available in dungeon templates. |
| `join_faction`, `leave_faction` | `faction` | Adds or removes the player's [membership](#faction-membership). Not available in dungeon templates. |
| `disposition` | `amount` (-100 to 100, not 0), optional `npc` | Changes an NPC's [regard](#relationships) for the player; the NPC being spoken to unless `npc` names another. Not available in dungeon templates. |
| `grant_property`, `revoke_property` | `property` | Gives the player a new holding of a [property](#5g-player-housing-and-storage) free of price and requirements (the first rent period is free too), or takes theirs away. Granting does nothing if they already have it. Not available in dungeon templates. |
| `grant_vehicle`, `revoke_vehicle` | `vehicle` | Gives the player their own [vehicle](#5h-vehicles-and-transportation) of that kind, full of fuel and waiting where they stand (or at their home room), or takes theirs away (anyone aboard steps off). A character owns at most one of each kind, so granting twice does nothing. Not available in dungeon templates. |

Item, quest, and objective IDs inside conditions and outcomes are local package
IDs, like other references, and must exist in the package.

### Example: a return-to-the-NPC quest

```json
{
  "mud_quests": [
    {"id": "wolf-hunt", "name": "Wolf Hunt", "description": "Bring two pelts to the hunter.",
     "completion_mode": "turn_in", "turn_in_npc_definition_id": "hunter", "sequential": 1,
     "conditions_json": [{"type": "level", "min": 2}]}
  ],
  "mud_quest_givers": [{"quest_id": "wolf-hunt", "npc_definition_id": "elder"}],
  "mud_quest_objectives": [
    {"id": "find-hunter", "quest_id": "wolf-hunt", "objective_type": "talk_npc",
     "target_id": "hunter", "description": "Find the hunter", "sort_order": 1},
    {"id": "pelts", "quest_id": "wolf-hunt", "objective_type": "possess_item", "target_id": "pelt",
     "description": "Collect two pelts", "required_count": 2, "sort_order": 2, "consume_on_turn_in": 1}
  ],
  "mud_quest_rewards": [{"id": "hunt-pay", "quest_id": "wolf-hunt", "reward_type": "coins", "amount": 20}],
  "mud_dialogue_responses": [
    {"id": "accept-hunt", "node_id": "elder-start", "response_text": "I will hunt wolves.",
     "accept_quest_id": "wolf-hunt"},
    {"id": "turn-in-hunt", "node_id": "hunter-start", "response_text": "Here are your pelts.",
     "turn_in_quest_id": "wolf-hunt"},
    {"id": "warrior-gift", "node_id": "elder-start", "response_text": "Any advice for a warrior?",
     "conditions_json": [{"type": "class", "keys": ["warrior"]}, {"type": "claimed", "negate": true}],
     "outcomes_json": [{"type": "give_item", "item": "pelt"}, {"type": "experience", "amount": 25}]}
  ]
}
```

1. The player accepts the quest from the elder.
2. They `/talk` to the hunter, which unlocks the pelt stage.
3. Once they carry two pelts, the quest becomes ready.
4. Beside the hunter, they choose "Here are your pelts." (or use `/quest turnin Wolf Hunt`). The hunter takes the pelts and pays 20 coins, once.

### Item and NPC properties

`properties_json`, `behaviors_json`, and `state_json` may be JSON objects or
JSON text in the file. For objects that triggers work with, item properties
may set `"fixed": true` (it cannot be picked up) and `"state_descriptions"`,
which maps up to 20 states to a line shown in the room description and by
`/look`. For example, `{"": "A rusty lever points up.", "down": "The lever is
pulled down."}`. A placed item's `state_json` may set its starting
`"state"`. A placed item or NPC with `{"hidden": true}` in its `state_json`
stays out of sight until a trigger's `show` action reveals it. Item properties can include `heal`, `value`,
`reveal_exit`, `use_message`, equipment bonuses, firearm data, attribute
`requirements`, and timed effects. Equipment may also set integer `accuracy`
and `evasion` (each −20 to 20; negative evasion suits heavy armor). A firearm's
`accuracy` applies only to its own shots. Equipment may set `resistances`, and
any usable item may set `cures`; see
[Protective gear, cures, and hazards](#protective-gear-cures-and-hazards).

`behaviors_json` controls hostility for combat-capable NPCs:
- By default, they attack on sight.
- `{"aggressive": false}`, `{"neutral": true}`, or `{"friendly": true}` keeps them from attacking first. They still fight back against a player who attacks them, and only against that player.
- `{"passive": true}` NPCs, such as training dummies, never strike back.

`mud_npc_shops` turns a placed NPC into a merchant. Set `buys_items` to 0 or 1
and `sell_percent` to 1–100 for player sales. `mud_shop_stock` sets each item's
coin `price` and optional `quantity` (omit or use `null` for unlimited stock).
Players use `/shop`, `/shop buy <item>`, and `/shop sell <item>` beside the NPC.
A stock row's optional `conditions_json` hides it from players who do not meet
the [conditions](#conditions), for example a `reputation` rank.

`mud_npc_banks` turns a placed NPC into a banker:

```json
"mud_npc_banks": [{"npc_definition_id": "keeper"}]
```

In a banking room or beside an available banker, players use `/bank` or
`/bank balance` to see carried and stored coins, `/bank deposit <amount>` to
store coins, and `/bank withdraw <amount>` to carry them again. Amounts must be
positive whole numbers. A banker who is hidden, defeated, or closed by a daily
schedule is unavailable. Bank balances belong to the character, and every world
has its own characters, so each world keeps its own balances. Shops and other
coin costs use carried coins only.
Banking is unavailable in the portal and procedural dungeons.

For renewable room-floor resources, add `mud_item_spawns` with a 5–86,400 second
`respawn_seconds`; do not also place an ordinary item instance for that spawn.
For an active gathering action, add a `mud_gathering_nodes` row:

```json
{"id":"copper-vein","room_id":"cave","name":"Copper vein","activity":"mine",
 "rewards_json":[{"item_id":"copper-ore","amount":2}],
 "skill_key":"mining","min_rank":2,"tool_definition_id":"pickaxe",
 "success_percent":70,"respawn_seconds":600}
```

`activity` is `harvest`, `mine`, `scavenge`, or `collect`. `rewards_json`
lists 1–10 existing items, each with `item_id` and `amount` (1–20).
`skill_key`, `min_rank` (0–20), and `tool_definition_id` are optional.
Setting `skill_key` requires the player to know that skill, even at rank 0;
`min_rank` raises the requirement. `skill_key` may name a bundled or installed skill. `success_percent` defaults
to 100. Players use `/gather <name>` or the matching activity command. Every
attempt, including a failed roll, depletes the shared source for
`respawn_seconds` (5–86,400). A success practices the required skill if that
skill has the `gather` trigger. The `gathering` skill effect adds percentage
points to success chance, capped at 100%.

### Protective gear, cures, and hazards

These pieces work together to build an environmental danger and the means to
survive it. Examples include radiation and gas masks, spore clouds and
filters, frost and furs, or curses and holy charms.

**Equipment resistances.** An equipped item's `resistances` maps a resistance
category (`physical`, `poison`, `burning`, `bleeding`, `weakness`,
`vulnerability`, `blinded`, `snared`) or an effect key (built-in or one of the
world's own) to a percentage from -100 to 100:

```json
{"resistances": {"radiation": 60, "poison": 25}}
```

- Gear adds to race and class resistances. The total for each key is kept within -100 to 100.
- An incoming effect is weakened by its own key's total plus its `resist_as` category's total. 100 or more is immunity: the player shrugs the effect off.
- Gear counts only while equipped in the current loadout and while the wearer meets its `requirements`.
- An item with a `category` the wearer is untrained in gives three quarters of its positive resistances. Negative values (drawbacks) always apply in full.
- `physical` also scales enemy blows, as it does for races.
- `/character` lists the combined totals. Examining the item shows what it protects against.

**Cures.** Any usable item may list up to 10 `cures`. Each names exactly one
`effect` (a key) or `category` (every effect that resists as that category).
Each cure may also set `potency` (1–1,000) to weaken the effect, `turns`
(1–100) to shorten it, or both. A cure with neither removes the effect outright.
An effect weakened or shortened to 0 is removed.

```json
{"cures": [{"effect": "radiation", "potency": 3, "turns": 5}]}
{"cures": [{"category": "poison"}]}
```

- On `/use`, cures run first, then `on_use_effects`, then `heal`. A consumable is used up.
- If an item only cures and the player has nothing it treats, it is not used up.
- Triggers can cure too, with the `cure_effect` [action](#actions), for example in a decontamination shower, at a shrine, or from a medic's dialogue.

**Lingering hazards.** An `enter_room` trigger fires once, on arrival. A
`linger` trigger fires every `interval_seconds` (30–86,400) for each player
still in its room, acting as that player, so their gear, conditions, and
cooldowns all apply. Use it with `apply_effect` so resistances count. The
`damage` action ignores resistances. Add a world-flag condition so a lever or
quest can switch the hazard off.

**Effects outside combat.** An effect marked `"outside_combat": true` deals
damage, heals, and wears off every 30 seconds for connected players who are not
in combat. In combat it ticks on combat turns as usual. Outside combat it never
takes a player below 1 health, just like the `damage` action. Effects without
the flag keep the classic behaviour: they wait for combat turns or `/rest`.

#### Worked example: a radiation zone

`effects.json`:

```json
{"effects": [{"key": "radiation", "name": "Radiation", "kind": "damage",
              "resist_as": "poison", "outside_combat": true}]}
```

`content.json` items:

```json
{"id": "gas-mask", "name": "Gas Mask", "description": "Rubber and a fresh filter.",
 "item_type": "equipment", "equipment_slot": "head",
 "properties_json": {"resistances": {"radiation": 60}}},
{"id": "hazmat-suit", "name": "Hazmat Suit", "description": "Heavy, but it seals.",
 "item_type": "equipment", "equipment_slot": "body",
 "properties_json": {"category": "hazmat", "resistances": {"radiation": 40}, "evasion": -3}},
{"id": "anti-rad", "name": "Anti-Rad Pills", "description": "Bitter iodine tablets.",
 "item_type": "consumable",
 "properties_json": {"cures": [{"effect": "radiation", "potency": 3, "turns": 5}]}}
```

`events.json`:

```json
{"id": "fallout", "event": "linger", "source": {"room": "surface"}, "interval_seconds": 60,
 "conditions": [{"type": "flag", "scope": "world", "key": "vent.sealed", "negate": true}],
 "actions": [{"type": "apply_effect", "effect": "radiation", "potency": 4, "duration": 6},
             {"type": "message", "text": "Your dosimeter clicks faster."}]},
{"id": "decon", "event": "enter_room", "source": {"room": "airlock"},
 "actions": [{"type": "cure_effect", "effect": "radiation"},
             {"type": "message", "text": "Decontamination spray hisses over you."}]}
```

On the surface, an unprotected player gains Radiation 4 every minute. It keeps
hurting after they leave, until it runs out or is treated. The mask alone takes
it down to 1. The mask and suit together (if trained in `hazmat`) make the
player immune. Pills weaken and shorten it, and the airlock clears it.

### Professions and repeatable work

Define a profession, a recruiter NPC, work activities, and their rewards in
`content.json`. The recruiter and any activity provider must be NPC definitions
in this package. Place them with `mud_npc_instances` where players can meet them.
Independent work instead names a room. For example:

```json
{
  "mud_professions": [
    {"id":"courier","name":"Courier","description":"Carry messages across town.",
     "recruiter_npc_definition_id":"postmaster"}
  ],
  "mud_work_activities": [
    {"id":"sort-mail","profession_id":"courier","name":"Sort mail",
     "description":"Sort the morning post.","kind":"immediate","room_id":"post-office",
     "cooldown_seconds":3600},
    {"id":"forest-route","profession_id":"courier","name":"Forest route",
     "description":"Visit the forest, then report to the postmaster.",
     "kind":"assignment","provider_npc_definition_id":"postmaster",
     "objective_type":"visit_room","objective_target_id":"forest",
     "cooldown_seconds":3600}
  ],
  "mud_work_rewards": [
    {"id":"sort-pay","activity_id":"sort-mail","reward_type":"coins","amount":5},
    {"id":"route-pay","activity_id":"forest-route","reward_type":"experience","amount":20}
  ]
}
```

`conditions_json` on a profession controls enrollment; on an activity it
controls doing or turning in work. Both use the existing [conditions](#conditions)
format. An activity may require a carried or equipped `tool_definition_id` and
may name a `practice_skill_key`. That skill advances only if the character
already knows it and it is enabled. Joining never teaches a skill. A custom
skill used only as a work rank requirement can list the `work` practice trigger.

Each activity is either `immediate`, with exactly one `room_id` or
`provider_npc_definition_id`, or `assignment`, with a provider NPC and one
objective. An assignment objective is `visit_room`, `talk_npc`, `defeat_npc`,
`possess_item`, or `deliver_item`; `objective_target_id` names the corresponding
room, NPC, or item. `required_count` defaults to 1 and may be 1–100. Progress
starts after acceptance. Possessed items must still be carried and unequipped
at turn-in and are consumed then. Delivered items are consumed as they are
handed over with `/work deliver <name>`.

An activity needs at least one reward or a practice skill. Each activity may
have up to ten `mud_work_rewards` rows: `coins` or `experience` amount 1–10,000,
or `item` with an item `target_id` and amount 1–20. Rewards are deterministic.
`cooldown_seconds` defaults to 3,600 and must be 60–604,800. It starts when
payment succeeds, separately for each character and activity. Abandoning a job
or leaving and rejoining a profession does not bypass it.

Players use `/professions` to find recruiters, `/profession join <name>` and
`/profession leave <name>` for enrollment, and `/work` to see nearby and active
work. Use `/work do <name>` for immediate work, or `/work accept <name>`,
`/work deliver <name>` when required, `/work turnin <name>`, and
`/work abandon <name>` for an accepted job. When names repeat, use
`<profession>/<activity>`.

`mud_room_discoveries` reveals a clue and optional item once per character with
`/search`. Set `difficulty` (0–200, default 0 = always found) to hide it
better; the Perception skill and Intellect improve the odds, and only rooms
with such hidden discoveries let searching practice Perception.

Each discovery may instead name one `item_instance_id` (a placed object in the
same room) or `exit_id` (an exit leaving that room). The exit must have a
package-local `id`. These targets remain invisible and unusable to a character
until found, then use the ordinary look, interaction, pickup, and movement
rules. A revealed exit still needs its key and still checks its entry conditions.
An optional `conditions_json` array uses the same conditions as exits, including
skills, attributes, held items, quests, and flags. Conditions must pass before
the search roll; they neither consume items nor reveal a clue on failure.
Only one of `item_definition_id`, `item_instance_id`, and `exit_id` may be set.
World Forge edits discoveries as a content collection; a light publish edits a
discovery's text without erasing characters' existing discovery records. Deleting a referenced object or exit
leaves the clue and those records in place.

`mud_npc_trainers` makes an NPC teach a skill. `skill_key` names a skill from
this package's `skills.json` (by local key) or any skill installed on the
station, such as `stealth`. `price` (0–100,000 coins) is charged to learn the
skill and, multiplied by the current rank, for each rank taught with
`/practice <skill> from <trainer>`, up to `max_rank` (1–20). Players use
`/learn <skill> from <trainer>` beside the NPC.

NPCs can also have small optional behaviours. These run only for placed NPCs
in the active traditional world. Existing NPCs remain still unless configured.
For example:

```json
{
  "ambient": {"interval_seconds": 60, "lines": ["Mind the tide.", "Welcome ashore.", "/emote coils a length of rope."]},
  "wander": {"interval_seconds": 120},
  "reactions": {"enter": "Welcome, traveler.", "say": "I hear you.", "get": "Take care with that."}
}
```

`wander` chooses a room exit. To follow specific exits, use `patrol` instead:
`{"patrol":{"interval_seconds":90,"directions":["north","south"]}}`.
The directions are attempted in order; a missing exit leaves the NPC in place
for that turn. Use either `wander` or `patrol`, never both. With the world clock
on, a [`schedule`](#npc-schedules) moves an NPC by the hour instead. Intervals must be
5–86400 seconds. `ambient.lines` accepts 1–20 lines of up to 300 characters.
Each tick picks one line at random: a plain line is speech (`Harbormaster says: Mind the tide.`),
while a line starting with `/emote ` is an action (`Harbormaster coils a length of rope.`),
so speech and actions can share one pool. An `/emote` line needs text after the prefix.
`reactions` accepts `enter`, `say`, `emote`, `get`, `drop`, `open`, `close`, and `put` with text up to 300
characters each. Invalid or unknown behaviour keys are rejected on import.
The server stores pending ticks in the database and resumes them after a
restart; missed ticks run once instead of replaying indefinitely.
The ZIP contains authored definitions and placements; it does not carry
characters, inventories, quest progress, or procedural dungeon runs.

## 5. Add optional races and classes

`origins.json` has `races` and/or `classes` arrays. Each object uses the same
fields as a standalone race or class JSON file. For example:

```json
{
  "races": [{"key": "riverfolk", "name": "Riverfolk",
    "description": "Travelers of the river", "traits": "Sure footing",
    "attributes": {"agility": 1}}],
  "classes": [{"key": "pathfinder", "name": "Pathfinder",
    "description": "A practiced guide", "traits": "Trail craft",
    "skill": "guard", "spells": ["ward"]}]
}
```

Bundled races and classes support every field in the
[race and class guide](#race-class-reference),
including `tags`, `resistances`, `proficiencies`, `skill_affinities`, and
`progression`. Their `skills`, favored `skill`, and `skill_affinities` may name
skills from the same package's `skills.json` by local key.

Keys in this file are local to the world. StationNet derives stable global keys
from the world ID, kind, and local key, so another world or standalone import
may use the same local key. Conditions, exit conditions, and item requirements
that name a bundled race or class by its local key are mapped to the global key
on import and back to the local key on export. At most 100 definitions of each kind may be bundled.
Imported definitions join the station-wide registry and stay installed even
when the world is removed. By default a world that bundles at least one race and
one class offers only its bundled ones; otherwise it offers its bundled
definitions alongside the built-ins. You can choose differently on import, and
change any world's selection later from its **RACES, CLASSES & SKILLS** page.
A world's selection never affects other worlds. Importing a world does not
enable it or change any existing world or character.

### Optional skills

`skills.json` holds `{"skills": [...]}` with up to 100 skill definitions in the
format described in the [skills guide](#skill-reference).

- **Local keys.** As with origins, keys are local to the world. Races,
  classes, trainers, and other bundled skills may refer to them by local key,
  and StationNet rewrites those references to the installed global keys.
- **Requirements.** A skill's `requirements.races` and `requirements.classes`
  may name races and classes from the same package.
- **Availability.** Bundled skills follow the same import choice as races and
  classes. A world always offers enough starting skills: when it bundles too
  few, the built-in skills are offered too.
- **Export.** Exports include the world's skills plus any imported skills that
  its exported races, classes, skills, or trainers depend on.

```json
{
  "skills": [{"key": "spear-forms", "name": "Spear Forms", "group": "weapon",
    "description": "Reach and footwork with spears.",
    "effects": [{"type": "melee_damage", "every": 2, "weapon_category": "spear"}],
    "proficiencies": {"weapons": ["spear"]},
    "practice": {"triggers": ["melee_hit"]}}]
}
```

## 5b. World dungeons (`dungeons.json`)

A world can place procedurally generated dungeons behind entrances in its own
rooms. Every `/enter` builds a new, private layout from the entrance's
template, so each run differs. A party shares one instance, and it never
interferes with other players' runs or with the Dungeon Portals.

`dungeons.json` is optional and holds two arrays:

```json
{
  "templates": [
    {"key": "old-mine", "name": "Old Mine", "template": { "...": "a dungeon theme, without id or version" }}
  ],
  "entrances": [
    {"room": "cave-mouth", "return_room": "village", "template": "old-mine", "name": "old mine",
     "description": "Rails vanish into the dark.", "difficulty": 2, "size": 12, "layout": "maze",
     "max_party": 4, "conditions": [{"type": "level", "min": 3}],
     "conditions_message": "The foreman waves you off.", "enabled": true}
  ]
}
```

- **Templates** use the dungeon theme format described in
  the [dungeon theme reference](#dungeon-theme-reference): generation bounds, layouts, special roles such as
  bosses and treasure, and pools of rooms, environments, NPCs, enemies, items
  and encounters, plus embedded definitions.
  - Leave out `id` and `version`. The station assigns them, and every edit
    becomes a new revision, so runs already in progress keep the layout and
    content they started with.
  - Templates cannot bundle `origins` or `skills`. Put those in the world's
    `origins.json` and `skills.json` instead.
  - Shared `definition_id` and `item_definition_id` values may name this
    package's `content.json` items and NPCs, which are remapped on import, or
    built-in `stationnet:` items. Conditions inside embedded dialogue should name
    embedded items.
- **Entrances.** `room` is where players type `/enter <name>`, or
  `go <name>`. `return_room` is where explorers arrive after completing the run,
  abandoning it, or recovering from death; it defaults to `room`.
  - `difficulty`, `size` and `layout` are optional overrides within the
    template's bounds.
  - `max_party` is 1 to 20 (default 4).
  - `conditions` use the same grammar as exit conditions.
- **Play.** A world run keeps the world's progression track, inventory,
  equipment, wallet and mana name, and follows the normal dungeon rules:
  - Dungeon rest rates apply, and death costs this run's loot rather than
    experience.
  - Run loot cannot be dropped, stored or sold until the run is completed.
  - Completing from the exit room pays every active explorer once.
- **Parties.** The leader's entry message shows a party code. Others join with
  `/join <code>` while standing at that entrance or its return room. `/run`
  shows the code and progress. Everyone shares one instance, its combat and its
  pickups.
- **Disconnects.** A run survives disconnects and restarts. Reconnecting
  returns you to your saved dungeon room. If you were moved out, use `/resume`
  to go back, or `/abandon` to give the run up.
- **Disabling the world** leaves its runs waiting; they resume when it is
  enabled again. **Removing the world** ends its live runs and loses their
  loot. Deleting an entrance, room or template never interrupts a run in
  progress. A missing return room falls back to the start room.

In World Forge, **Dungeon templates** and **Dungeon entrances** are content
collections. **New from a theme** starts a template from any installed dungeon
theme or another template, and each template has a **Preview a generated run**
button that generates a sample without saving anything.

## 5c. Events and triggers (`events.json`)

Triggers make a world react. Each one is a small rule: **when** something
happens, **if** its conditions hold, **do** a short list of actions. They are
plain JSON settings, not a programming language. There are no variables,
expressions, or loops, and nothing in a package can run code.

```json
{"triggers": [
  {"id": "pull-lever", "name": "Pull the rusty lever",
   "event": "use_object", "source": {"object": "lever-1"},
   "scope": "world", "once": true,
   "conditions": [{"type": "attribute", "attribute": "strength", "min": 12}],
   "fail_message": "The lever will not budge.",
   "actions": [
     {"type": "set_object_state", "object": "lever-1", "state": "down"},
     {"type": "set_exit", "exit": "vault-door", "state": "open"},
     {"type": "message", "to": "room", "text": "Gears grind somewhere below."},
     {"type": "spawn_npc", "npc": "vault-guard", "room": "vault", "delay_seconds": 5, "despawn_seconds": 300}
   ]}
]}
```

### Events

`source` names exactly one piece of content, using its local package ID.

| `event` | `source` | Happens when |
| --- | --- | --- |
| `enter_room` | `room` | A player walks, flees, teleports, or returns into the room. |
| `use_object` | `object` (a placed item) or `item` (any item of that type) | A player uses it: `/use` on something carried or in the room, or `/interact`, `/push`, `/pull`, `/press`, `/touch`, `/turn`, or `/activate` on something in the room. |
| `take_item` | `object` or `item` | A player picks it up. |
| `say_phrase` | `room`, plus `phrase` | A player says the phrase in that room. Case, punctuation, and extra spaces are ignored, so `Open, Sesame!` matches `open sesame`. |
| `defeat_npc` | `npc` (any NPC of that type) or `npc_instance` (one placed NPC) | An NPC is defeated. It fires once for **each** player who earned a share of the rewards. |
| `dialogue_choice` | `response` | A player picks that dialogue response. It runs after the response's own outcomes and quest changes. |
| `quest_complete` | `quest` | A player completes the quest, automatically or by turning it in. |
| `quest_failed` | `quest` | A player's quest fails, by deadline, failure rule, trigger, or impossibility. Use it for penalties such as `take_coins`, `disposition`, or `reputation`. |
| `crime` | `room`, `npc` (a witness or victim of that type), or `faction` (one whose standing the crime cost) | A player is seen committing a [crime](#crime-and-consequences). |
| `faction_join`, `faction_leave` | `faction` | A player joins a faction during play, or leaves or is expelled from one. See [Faction membership](#faction-membership). |
| `timer` | `room`, plus `interval_seconds` (30–86,400) | Repeatedly while the world is active, whether or not anyone is there. Timers have no player. |
| `linger` | `room`, plus `interval_seconds` (30–86,400) | Repeatedly, once for **each** player in the room at that moment, as that player. Use it for [hazards](#protective-gear-cures-and-hazards) and boons that last as long as someone stays. Every action and condition is available. |
| `manual` | none | Only when another trigger runs it with `run_trigger`. Use these to share a set of actions. |

### Trigger fields

| Field | Default | Meaning |
| --- | --- | --- |
| `id` | required | Lowercase letters, digits, and hyphens; unique in the file. |
| `name` | the `id` | Shown in World Forge. |
| `scope` | `character` (timers: `world`) | `character` tracks `once`, `cooldown_seconds`, and `max_fires` separately for every character. `world` shares them across everyone. |
| `once` | `false` | Fire only once per scope: once per character, or once ever for a world trigger. |
| `cooldown_seconds` | `0` | Minimum time between firings, per scope (up to a week). |
| `max_fires` | none | Stop after this many firings, per scope. |
| `enabled` | `true` | Disabled triggers do nothing. |
| `conditions` | `[]` | All must hold; see [Conditions](#conditions). Timers may only check `world` flags, `object_state`, and `time`. |
| `fail_message` | none | Shown to the player when the conditions fail or they cannot pay a cost. |
| `actions` | required | 1 to 10 actions, run in order. |

### Actions

Any action may add `"delay_seconds"` (up to 3,600) to run later. Delays are
saved, so they survive restarts. A delayed action is skipped if its player has
left the world, or the dungeon run, by the time it is due.

| `type` | Fields | Effect |
| --- | --- | --- |
| `message` | `text`, `to`: `actor` (default), `room`, or `other_room` with `room` | Tells the player, everyone in the room, or everyone in another room. |
| `set_flag` | `scope`: `player` (default) or `world`, `key`, and `value` (text) or `add` (a whole number) | Remembers something. `add` keeps a counter, such as wardens slain. |
| `clear_flag` | `scope`, `key` | Forgets a flag. |
| `give_item`, `take_item` | `item`, `amount` (1–100) | As in dialogue [outcomes](#outcomes). Costs are checked before anything changes. |
| `experience`, `coins`, `take_coins` | `amount` | As in dialogue outcomes. |
| `heal`, `damage` | `amount` (1–10,000) | Restores health, or deals it. Damage never takes a player below 1 health, so traps cannot kill. |
| `set_object_state` | `object`, `state` (up to 40 characters; `""` resets it) | Changes a placed object's state for everyone. See `state_descriptions` below. |
| `set_exit` | `exit`, `state`: `open`, `locked`, `hidden`, or `default`; `scope`: `world` (default) or `character` | `open` unlocks and reveals the exit. `locked` seals it so that no key, pick, or earlier unlock opens it. `hidden` removes it from view and use. `default` restores the authored exit. A character state applies to that player only and overrides the world's. |
| `show`, `hide` | `npc_instance` or `object` | Shows or hides a placed NPC or object for everyone. |
| `spawn_npc` | `npc` (definition), `room`, optional `despawn_seconds` (10–86,400, default 600) and `max_live` (1–5, default 1) | Places a temporary NPC. It disappears when its time runs out, but never in the middle of a fight. Once defeated, it never respawns. No more than `max_live` from the same action exist at once. |
| `teleport` | `room` | Moves the player to a room in this world. That room's `enter_room` triggers run. |
| `start_quest` | `quest` | Gives the player a quest, if they can accept it. |
| `complete_objective` | `objective` | Advances an objective by one, or completes it. Use it with `script` objectives, which only triggers can advance. |
| `fail_quest` | `quest`, optional `message` | Fails the player's active quest, showing `message` as the reason. Nothing happens if they are not on it. |
| `fail_objective` | `objective`, optional `message` | Fails one unfinished objective of an active quest; see [Failing quests](#failing-quests). |
| `run_trigger` | `trigger` | Runs a `manual` trigger. The manual trigger's own conditions and limits still apply. |
| `learn_destination`, `forget_destination` | `destination` | Teaches the player the way to a [destination](#5e-wayfinding-destinations), or makes them forget it. |
| `disposition` | `npc` (definition), `amount` (-100 to 100, not 0) | Changes that NPC's [regard](#relationships) for the player. |
| `reputation` | `faction`, `amount` (-1000 to 1000, not 0) | Changes the player's [standing](#factions-and-reputation) with a faction. |
| `join_faction`, `leave_faction` | `faction` | Adds or removes the player's [membership](#faction-membership). |
| `grant_property`, `revoke_property` | `property` | As in dialogue [outcomes](#outcomes). A refused grant only tells the player. |
| `grant_vehicle`, `revoke_vehicle` | `vehicle` | As in dialogue [outcomes](#outcomes). |
| `fine` | `faction`, `amount` (1–100,000) | Fines the player, as a [crime](#crime-and-consequences) would, without changing standing. |
| `clear_fines` | optional `faction` | Pardons the player's unpaid fines, to that faction or to all. |
| `apply_effect` | `effect`, optional `potency` (1–1,000, default 1) and `duration` (1–100 turns, default 3) | Puts a timed effect on the player: a built-in key or one of the world's own effects. Harmful effects are weakened by matching race, class, and gear resistances. Also allowed in dungeon triggers. |
| `cure_effect` | exactly one of `effect` (a key) or `category` (a resistance category); optional `potency` (1–1,000) and `turns` (1–100) | Removes the player's matching effects, or with `potency`/`turns` weakens or shortens them. See [cures](#protective-gear-cures-and-hazards). Also allowed in dungeon triggers. |

Exits must have an `"id"` in `exits.json` before a trigger can name them.

### Shared and personal state

- **Player flags**, character-scope exits, and character-scope fire counts
  belong to one character in one world. Other players never see them.
- **World flags**, object states, shown or hidden NPCs and objects,
  world-scope exits, and spawned NPCs are shared by everyone in the world.
- Every world, and every procedural dungeon run, keeps separate state.
  Switching worlds never carries flags across.
- Rewards and personal actions only ever affect the player who caused the
  event. A world trigger marked `once` rewards exactly one player, even when
  several act at the same moment.

### Safety limits

- A trigger cannot run itself again in the same chain. A chain of triggers
  that run each other, teleport players, or complete quests stops after 4
  nested triggers or 40 actions.
- `run_trigger` may only name `manual` triggers, and triggers that run each
  other in a loop are rejected on import.
- At most 20 delayed actions per trigger can wait at once. Delays carry their
  chain depth with them, so a delayed loop also ends.
- If an action fails, for example because the player cannot accept a quest,
  the player is told why and the remaining actions still run.
- Every ID a trigger names must exist in the package, or the import is
  rejected with the trigger and the missing ID.

### Recipes

**A boss opens the way for everyone.** The exit's own conditions read the
flag that the trigger sets:

```json
{"id": "warden-slain", "event": "defeat_npc", "source": {"npc": "warden"},
 "scope": "world", "once": true,
 "actions": [{"type": "set_flag", "scope": "world", "key": "warden.slain", "value": "yes"},
             {"type": "message", "to": "other_room", "room": "hall", "text": "The ward on the stairs fades."}]}
```

```json
{"from": "hall", "to": "tower", "name": "stairs", "direction": "up",
 "conditions": [{"type": "flag", "scope": "world", "key": "warden.slain", "value": "yes"}],
 "conditions_message": "A ward blocks the stairs."}
```

**A password that only opens the door for the speaker:**

```json
{"id": "password", "event": "say_phrase", "source": {"room": "hall"}, "phrase": "open sesame",
 "actions": [{"type": "set_exit", "exit": "crypt-gate", "state": "open", "scope": "character"},
             {"type": "message", "text": "The crypt gate swings open for you."}]}
```

**Count kills, then reward once:**

```json
[{"id": "count-rats", "event": "defeat_npc", "source": {"npc": "rat"},
  "actions": [{"type": "set_flag", "key": "rats", "add": 1}, {"type": "run_trigger", "trigger": "rat-reward"}]},
 {"id": "rat-reward", "event": "manual", "once": true,
  "conditions": [{"type": "flag", "key": "rats", "min": 10}],
  "actions": [{"type": "coins", "amount": 25}, {"type": "message", "text": "The ratcatcher's bounty is yours."}]}]
```

In World Forge, triggers are the **Events & triggers** collection, edited with
guided condition and action editors; a room's **Used by** lists the triggers that
name it. **Live world → Reset script state** clears the published world's flags,
fire counts, object and exit states, and spawned NPCs, which is useful while
testing. World Forge always uses the package's local IDs.

## 5d. Wilderness and room presets (`wilderness.json`)

Large outdoor areas such as forests, plains, mountains, deserts and swamps are
not written room by room. Instead, `wilderness.json` defines reusable **room
presets** and paints them onto a coordinate grid. Every painted square becomes a
real room: players walk through it with ordinary directions, meet each other
there, drop items, fight, and find authored landmarks. Nothing about it is
fast travel.

Coordinates are whole numbers. `x` grows to the east, `y` grows to the **south**,
and `z` is the [map layer](#map-layers-going-up-and-down). Each layer is a
separate map. Neighbouring squares on the same layer link automatically:

- `north`, `east`, `south` and `west` link any two neighbours on the same layer.
- The diagonals link only when **both** rooms allow eight-way links
  (`"links": "8"`). Authored rooms on the grid accept whatever their neighbour
  allows.
- Nothing links one layer to another automatically. Add an authored `up` or
  `down` exit, such as a ladder, stair, lift or cave shaft, to cross between
  layers.
- An authored exit in the same direction replaces the automatic link. A road,
  gate, bridge or cave mouth works this way. Walls (`blocked_dirs`) on either
  side of an edge remove the link both ways.

### Presets

```json
{
  "key": "pine-forest",
  "name": "Pine Forest",
  "terrain": "forest",
  "glyph": "t",
  "links": "4",
  "names": ["Pine Forest", "Needle-Strewn Path"],
  "descriptions": [
    "Tall pines crowd close, their needles muffling every step.",
    {"text": "A clearing opens among the pines, bright with sun.", "weight": 1},
    {"text": "Resin scents the cool, still air between the trunks.", "weight": 3}
  ],
  "environment": {"light": "dim", "exposure": "sheltered", "tags": ["outdoors"],
                  "lines": ["A crow calls somewhere overhead."]},
  "encounters": {"chance": 8, "max_live": 1, "despawn_seconds": 600,
                 "creatures": [{"npc": "wolf", "weight": 2, "min_danger": 1, "max_danger": 10}]},
  "resources": {"respawn_seconds": 3600, "max_per_room": 2,
                "items": [{"item": "pinecone", "chance": 40, "count": {"minimum": 1, "maximum": 2}}]}
}
```

| Field | Rules |
|---|---|
| `key` | Required. 1–32 lowercase letters, digits and hyphens, starting with a letter. |
| `name` | Required, 2–80 characters. Used as the room name unless `names` is given. |
| `terrain` | `grassland`, `forest`, `dense-forest`, `hills`, `mountains`, `desert`, `swamp`, `tundra`, `water`, `road` or `custom` (the default). Colours the World Forge map. |
| `glyph` | One printable character for maps; not a space, `@`, `*`, `P`, `<`, `>` or `&`, which `/map` uses for players, landmarks and ways up or down. Default `.`. |
| `links` | `"4"` (default) or `"8"`. |
| `names` | Optional list of up to 20 room-name variants (up to 80 characters each). |
| `descriptions` | Required list of 1–40 descriptions, each up to 4000 characters. A plain string means weight 1. |
| `environment` | Optional `light` (`bright`, `dim`, `dark`), `exposure` (`open`, `sheltered`), up to 20 `tags`, and up to 20 `lines` of ambient text added after the description. |
| `encounters` | Optional. `chance` is the percentage (0–100) rolled each time a player enters; `max_live` (0–5) caps creatures per room; `despawn_seconds` (10–86400) removes them later. Each creature names an NPC definition and may limit itself to a danger range (1–10). |
| `resources` | Optional. When a player enters, each item rolls its `chance` and places 1–5 copies, up to `max_per_room` (0–5). The room then waits `respawn_seconds` (60–86400) before gathering again. |
| `requires_mode` | Optional. A vehicle mode such as `water`: only a [vehicle](#5h-vehicles-and-transportation) that travels by it may enter these rooms, by any route. Use it for open sea, deep snow, or the sky. |

Each room picks its name, description and ambient line from its own position, so
**the same room reads the same on every visit** while its neighbours vary.
Editing a preset changes every room that uses it at once. Creatures and items
name definitions in `content.json`, or built-in `stationnet:` definitions.
Encounter creatures behave like trigger spawns: they never respawn and they
vanish after `despawn_seconds`. Hostile creatures attack as usual.
Gathered resources are play state and are not exported; they regrow from the
preset.

### Regions

A region applies local changes to whatever presets are painted inside it, so one
forest preset can be peaceful in the south and deadly in the north:

```json
{"key": "north-woods", "name": "North Woods",
 "overrides": {"danger": 6, "encounter_multiplier": 2, "name_prefix": "Northern",
               "creatures": {"mode": "add", "list": [{"npc": "troll", "min_danger": 5}]}}}
```

`danger` (1–10, default 1) chooses which creatures may appear.
`encounter_chance` (0–100) replaces a preset's chance, and
`encounter_multiplier` (0–5) scales it. `resource_multiplier` (0–5) scales
resource chances. `creatures` either adds to (`"add"`) or replaces
(`"replace"`) each preset's creatures. `name_prefix` (up to 30 characters) is
put in front of room names.

### Layers

Painted squares are stored as **run-length encoded rows**, one layer per `z`.
These are the painted squares of each [map layer](#map-layers-going-up-and-down);
name layers in `manifest.json`, and join them with `up` and `down` exits:

```json
{
  "presets": [],
  "regions": [],
  "layers": [
    {"z": 0, "origin": [-2, -2], "width": 5, "height": 3,
     "palette": ["", "pine-forest", "pine-forest@north-woods", "grassland"],
     "rows": ["5*2", "2*1,0,2*1", "3*3,1"]}
  ],
  "cells": [{"x": -2, "y": -2, "z": 0, "blocked_dirs": "east"}]
}
```

(Fill in `presets` and `regions` as described above.)

- `origin` is the `[x, y]` of the layer's top-left (north-west) square.
- `palette` lists what each number in a row means. `""` is an empty square;
  `"preset"` or `"preset@region"` paints a preset, optionally in a region.
- Each row is a comma-separated list of runs, `count*index`; a bare `index`
  means one square. The second row above is two `pine-forest` squares, one empty
  square, then two more. Rows may stop early, and missing rows are empty.
- `cells` holds only per-square exceptions: `"grid_link": false` or
  `blocked_dirs`.
- Limits: 200 presets, 200 regions, 64 layers, 200,000 runs in total, and
  1,000,000 wilderness rooms.
- An authored room's `x`, `y`, `z` must fall on an empty square of its layer.

Exports and World Forge drafts always use this compact form, so a 500 × 500 forest
takes only a few kilobytes.

### Building in World Forge

Open a world in **World Forge** and choose **World map → Grid**.

- **Presets.** In **Content library → Terrain presets**, add one of the
  station's starters (grassland, forest, dense forest, mountains, desert,
  swamp), duplicate an existing preset, or create one from scratch. Edit it with
  the guided form or as JSON. **Import file** and **Export all** read and write
  preset JSON files: one preset, or `{"presets": [...]}`. Importing can keep or
  replace presets with the same key, and can leave out creatures and items the
  world does not have. A preset that squares still use cannot be deleted.
- **Map.** Pan (Alt-drag or the hand tool), change layer, and zoom (Ctrl-scroll).
  The map shows one layer at a time, with arrows on rooms that have a way up or
  down. **Inspect** opens a square; **Rectangle** and **Ellipse** fill between
  two corners; **Brush** paints individual squares.
- **Fill.** Choose a preset and, optionally, a region. **Only empty** leaves
  existing squares alone; without it, painting changes the preset of wilderness
  squares in place. Authored rooms are never overwritten.
- **Erase.** Removes wilderness squares in an area. Squares that other content
  refers to (an entrance, an NPC, a passage) are protected.
- **Square inspector.** Shows a square's sample text, walls, and what refers to
  it. From here you can add passages that reach beyond the grid, set walls, or
  **convert it to an authored room**. Converting keeps its text and position
  and makes it an ordinary room for descriptions, triggers, discoveries, and
  locks. Use this for settlements, roads, ruins, and other special locations.
- **Placing existing rooms.** Rooms without coordinates are listed under the
  map; select one and click a square to place it. World Forge previews which
  passages and automatic links the move adds or removes.

When the world is published, players standing in squares that no longer
exist move to the starting room.

Players can type `/map` for a 15 × 9 view of the land around them on their
current layer only. `@` marks the player, `P` other visible players, `*`
authored landmarks, `<` a way up, `>` a way down, `&` both, and each preset
appears as its glyph. The heading names the layer.

## 5e. Wayfinding destinations

A destination names a room, authored or wilderness, as a place players can ask
the way to. Towns, gates, landmarks, and dungeon mouths are typical. Players
use these commands:

- `/destinations` lists every place they know the way to.
- `/directions <place>` describes a route and keeps that place as their
  destination. `/directions` alone checks again from wherever they stand, and
  `/directions clear` forgets the destination.

Directions follow the real rooms: stored exits, wilderness grid links, walls,
and exit locks and conditions, exactly as movement does. They never move
anyone. Routes prefer roads and avoid rough ground: road is quickest, then
grassland, then forest, hills, desert, and tundra, then dense forest and
swamp, then mountains, and water is slowest. They read like a traveller's
advice, not a list of steps:

```text
Kessa Gate is a long journey away to the northeast.
Head northeast across the grassland some distance until you meet a road.
Then follow the road north a long way, past the Old Watchtower.
Then take the gate east to Market Square, where you will find Kessa Gate.
```

Each [layer](#map-layers-going-up-and-down) is its own map, so directions stop
at the first way up or down on the route:

```text
Sewer Outflow is a short walk away.
Go east to Ladder Shaft.
Then take the ladder down to Sewer Junction.
Once you have gone down, ask for directions again.
```

Asking again on the new layer describes the next part of the way.

While a destination is chosen, an ordinary move mentions the new heading when
it changes. Wilderness `/travel` stops where the route turns away from the
travel direction, and on arrival. Whenever travel stops for any reason, the
next leg is shown. Travelling off the route behaves exactly as before.

Each `mud_world_destinations` row accepts these fields:

| Field | Meaning |
| --- | --- |
| `id`, `room_id` | Local ID, and a room ID or `@x,y,z` wilderness square. |
| `name` | 2–80 characters, unique in the world. Players type it, or any part of it. |
| `aliases` | Other names, separated by commas (up to 10). |
| `category` | `town`, `city`, `settlement`, `landmark` (default), `dungeon`, `road`, or `other`. Used to group `/destinations`. |
| `description` | A note for builders; players never see it. |
| `public` | `1` lets everyone ask the way. |
| `discover_on_visit` | `1` makes a character remember the way after entering the room once. |
| `map_items_json` | Item IDs (up to 10). Carrying any of them gives directions while it is carried; dropping it takes them away again. |
| `conditions_json` | [Conditions](#conditions) that must also hold, however the place is known. |
| `sort_order` | Lower numbers list first. |

Players also learn destinations in these ways, and keep what they learn:

- **Dialogue.** A response outcome: `{"type": "learn_destination", "destination": "kessa"}`.
- **Triggers.** A `learn_destination` or `forget_destination` action.
- **Books and letters.** An item whose `properties_json` has
  `"teaches_destinations": ["kessa"]` teaches when used, or read with `/read`.
  A consumable item is used up as it teaches.

A player never learns that an unknown destination exists. Asking for one gives
the same answer as asking for a place that does not exist. If no route exists
(a gate is sealed, for example), the player learns only the general bearing,
or nothing if the place is off the grid. In World Forge, destinations are the
**Destinations** collection, and an item's `teaches_destinations` property
chooses which destinations it teaches.

## 5f. World life

These optional systems make a world feel inhabited. Each is **off** until the
world turns it on, so existing worlds play exactly as before. Turn them on in
`manifest.json` or under **World settings** in World Forge; exports keep the
settings.

### World clock

```json
{"id": "lantern-isles", "name": "Lantern Isles", "author": "You", "start_room": "harbor",
 "clock": {"enabled": true, "day_minutes": 120, "start_hour": 6}}
```

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | The world keeps time. |
| `day_minutes` | `120` | Real minutes in one game day, 5 to 1440. `1440` follows real UTC time. |
| `start_hour` | `6` | Hours added to the clock, 0 to 23. With `1440` this is a time-zone offset from UTC. |

The hour is worked out from real time, so it is never stored and is the same
after a restart. Players type `/time`. The `time` [condition](#conditions)
gates dialogue, quests, exits, destinations, and triggers by the hour. Timer
triggers may check it too, for example to ring a bell at dawn. The day runs
night (21–5), dawn (5–7), morning (7–12), afternoon (12–17) and evening (17–21).

### NPC schedules

A `schedule` in an NPC's `behaviors_json` gives it a daily routine:

```json
{"schedule": {"interval_seconds": 30, "travel": "walk", "home": "cottage",
  "entries": [
    {"from": 8, "to": 18, "room": "forge", "open": true, "label": "working",
     "lines": ["The smith hammers a glowing blade.", "Sparks fly from the anvil."]},
    {"from": 18, "to": 22, "room": "tavern"}]}}
```

- Each entry covers `from` up to, but not including, `to` and may wrap past
  midnight. The first entry that covers the hour wins.
- When no entry applies the NPC goes `home`, which defaults to the room it was
  placed in.
- Every `interval_seconds` (5–86,400, default 60) the NPC moves toward where it
  should be. With `travel: "walk"`, the default, it takes one room along the
  shortest open route, so players can meet it on the way. With `"teleport"`, or
  when no route exists, it leaves and arrives directly.
- Once it is there, one of the entry's `lines` (up to 20, of up to 300
  characters) is shown to the room each interval.
- **Working hours.** A merchant or trainer with a schedule trades and teaches
  only during entries with `"open": true`. At other times `/shop` says it isn't
  trading. NPCs without a schedule are unaffected.
- A schedule cannot be combined with `wander` or `patrol`, and it rests while
  the clock is off.
- Rooms are local room IDs or `@x,y,z` wilderness squares, and must exist in
  the package.

### Relationships

With relationships on, every NPC definition remembers how much it likes each
character: its **regard**, from -100 to 100. Regard belongs to the NPC
definition, so it survives respawns and is shared by every placed copy. It
reads as a tier:

| Tier | Regard |
| --- | --- |
| `hostile` | at or below `hostile_at` (default -60) |
| `unfriendly` | below -20 |
| `neutral` | -20 to 19 |
| `friendly` | 20 to 59 |
| `trusted` | 60 and above |

```json
"relationships": {"enabled": true, "quest_complete": 5, "attack": -40, "crime": -25, "hostile_at": -60}
```

`quest_complete` is added for the quest's turn-in NPC (or each giver) when a
quest pays out. `attack` applies when a player attacks an NPC that was not
hostile to them, once per fight. `crime` is used by
[crime](#crime-and-consequences) once that is enabled. Each is -100 to 100,
and `hostile_at` is -100 to -21.

Regard also changes through the `disposition` dialogue outcome and trigger
action, and through gifts: `/give <item> to <npc>` hands over an item the NPC
values and is refused for anything else. Its effects are:

- The `disposition` condition can show, hide, or gate any dialogue response,
  quest, exit, or trigger.
- `/talk` opens with the NPC's greeting for the current tier. A hostile NPC
  refuses to talk.
- Merchants charge up to 10% less to trusted friends and up to 10% more to
  enemies, and pay correspondingly for what they buy.
- A combat-capable NPC at the hostile tier attacks that player on sight. Other
  players are unaffected.

Players check their standing with `/relations`. Each NPC can tune its own
behaviour with `relationship` in `behaviors_json`:

```json
{"relationship": {"default": 10, "gifts": {"apple-pie": 15, "rotten-fish": -20},
  "greetings": {"friendly": "Back again? Good!", "unfriendly": "Make it quick."},
  "refuse_talk_below": -40, "quest_complete": 10, "attack": -80, "crime": -50,
  "attacks_when_hostile": false}}
```

| Field | Meaning |
| --- | --- |
| `default` | Starting regard for every character (-100 to 100, default 0). |
| `gifts` | Up to 20 item IDs, each with the change it makes when given (-100 to 100). |
| `greetings` | Text per tier, shown when a conversation starts. |
| `refuse_talk_below` | Talk only at or above this regard (default: refuse only at `hostile`). |
| `quest_complete`, `attack`, `crime` | This NPC's own changes in place of the world's. |
| `attacks_when_hostile` | `false` keeps a hostile NPC from attacking (default `true`). |

### Factions and reputation

Factions are groups such as guilds, towns, temples, or gangs. A world has
factions when `content.json` defines them, so no setting is needed. Every
character has a **standing** with each faction from -1000 to 1000, starting at
its `default_reputation`.

```json
"mud_world_factions": [
  {"id": "guild", "name": "Smiths' Guild", "description": "Hammer and tongs.",
   "default_reputation": 0, "defeat_reputation": -100, "public": 1},
  {"id": "red-hand", "name": "Red Hand", "defeat_reputation": -50,
   "ranks_json": [{"key": "enemy", "name": "Enemy", "min": -1000, "attitude": "hostile", "talk": false},
                  {"key": "outsider", "name": "Outsider", "min": -100},
                  {"key": "brother", "name": "Brother", "min": 300, "attitude": "peaceful", "price_percent": 80}]}
],
"mud_faction_relations": [
  {"id": "guild-watch", "faction_id": "guild", "other_faction_id": "watch", "share_percent": 50},
  {"id": "watch-hand", "faction_id": "watch", "other_faction_id": "red-hand", "share_percent": -50}
]
```

| Field | Meaning |
| --- | --- |
| `default_reputation` | Starting standing, -1000 to 1000 (default 0). |
| `defeat_reputation` | Standing change for each player who helps defeat a member (default 0). |
| `public` | `1` (default) shows the faction in `/reputation` and the Standing panel, and announces changes. `0` keeps it hidden. |
| `ranks_json` | 2 to 10 ranks, lowest first. Leave it out for the default ranks. |
| `sort_order` | Lower numbers list first. |

Each rank has a `key`, a `name`, and a `min`; a character holds the highest
rank whose `min` they reach. A rank may also set:

| Field | Default | Meaning |
| --- | --- | --- |
| `price_percent` | `100` | Member merchants charge this percent (50–200) of their prices, and pay correspondingly. |
| `attitude` | none | `hostile` makes combat-capable members attack the player on sight. `peaceful` keeps normally aggressive members from attacking first. |
| `talk` | `true` | `false` makes members refuse `/talk`. |
| `trade` | `true` | `false` makes member merchants refuse `/shop`. |

The default ranks are Hated (-1000), Hostile (-600), Unfriendly (-200, prices
115%), Neutral (-50), Friendly (100, 95%), Honored (400, 90%) and Exalted (800,
85%). Members attack at Hated and Hostile, and won't talk or trade with them.

An NPC joins a faction with `faction_id` on its definition. Standing changes
through:

- `reputation` dialogue outcomes and trigger actions;
- quest rewards with `reward_type: "reputation"`;
- defeating members, by `defeat_reputation`;
- [crimes](#crime-and-consequences).

A **relation** passes `share_percent` of every change on to another faction,
rounded down. Use a negative share for rivals. Relations reach one step only:
in the example, helping the guild helps the watch, and helping the watch hurts
the Red Hand, but helping the guild does not change the Red Hand.

The `reputation` condition gates dialogue, quests, exits, destinations,
triggers and stock, so a guild hall can admit only members of standing. Players
see public factions with `/reputation` (or `/standing`) and in their Standing
panel. In World Forge, the **Factions** and **Faction relations** collections
hold factions, ranks and relations, and each NPC's `faction_id` chooses a faction.

### Faction membership

Standing alone never makes a player part of a faction. Membership is a separate,
optional layer: a world can have factions that nobody can join. It is off until
`manifest.json` enables it:

```json
"factions": {"enabled": true, "max_memberships": 1, "start_required": false}
```

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Players can belong to factions. While off, `/faction` is hidden, `member` conditions fail, and any stored memberships lie dormant until it is turned back on. |
| `max_memberships` | `1` | How many factions a character may belong to at once, 0 to 20; `0` means no limit. Lowering it removes nobody. |
| `start_required` | `false` | New characters must pick a starting faction. A character who qualifies for none proceeds without one, so a poorly chosen set of requirements can't lock anyone out. Characters that already exist are never asked. |

Each faction then opts in. Without these fields it cannot be joined by players,
and it behaves exactly as described above:

```json
{"id": "guild", "name": "Smiths' Guild", "joinable": 1, "start_joinable": 1,
 "recruiter_npc_definition_id": "guildmaster", "join_cost": 50, "join_reputation": 100,
 "join_conditions_json": [{"type": "level", "min": 3},
                          {"type": "member", "faction": "red-hand", "negate": true}],
 "join_conditions_message": "The guildmaster won't take a stranger or a Red Hand.",
 "start_conditions_json": [{"type": "class", "keys": ["warrior"]}],
 "leave_reputation": -200, "rejoin_hours": 24, "expel_below": -100}
```

| Field | Default | Meaning |
| --- | --- | --- |
| `joinable` | `0` | `1` lets players join during play with `/faction join`. Needs `public: 1`. |
| `start_joinable` | `0` | `1` offers the faction at character creation. Needs `public: 1`, and members must not start at a hostile rank or below `expel_below`. |
| `recruiter_npc_definition_id` | none | This NPC must be in the room, and willing to talk, to join during play. Without one, players can join anywhere. |
| `join_conditions_json` | `[]` | [Conditions](#conditions) to join during play. |
| `join_conditions_message` | generic | Up to 300 characters shown when the conditions fail. |
| `start_conditions_json` | `[]` | Who may choose the faction at creation. Only `level`, `race`, `class`, `tag`, `attribute` and `skill` conditions are allowed, since a new character has nothing else to test. |
| `join_cost` | `0` | Coins to join during play. Starting members pay nothing. |
| `join_reputation` | `0` | Standing change on joining, however the player joined. It passes through relations as usual. |
| `leavable` | `1` | `0` makes `/faction leave` refuse. Triggers and dialogue can still remove a member. |
| `leave_reputation` | `0` | Standing change on leaving or being expelled. |
| `rejoin_hours` | `0` | Hours before someone who left or was expelled can join again, up to 8760. |
| `expel_below` | none | A member whose standing falls below this is expelled. |

Players use `/faction` to list factions that are recruiting or that they belong
to, `/faction info <name>`, `/faction join <name>`, and `/faction leave <name>`.
Members are marked in `/reputation` and the Standing panel.

Rules that always apply:

- A member can't hold a hostile rank. Reaching one, or falling below
  `expel_below`, expels them, with the `leave_reputation` change. This can happen
  through any standing change, including a relation's share: joining a guild can
  cost you your place in its rival.
- Anyone who would land at such a standing can't join, and anyone owing the
  faction fines can't join during play.
- To keep rivals exclusive, add a negated `member` condition to each faction's
  `join_conditions_json`, or set `max_memberships` to `1`.
- Making a faction unjoinable keeps its existing members. Removing it ends their
  memberships.

Authored content reacts to membership through:

- the `member` [condition](#conditions), for dialogue, quests, exits, stock,
  destinations and triggers;
- `join_faction` and `leave_faction` [outcomes](#outcomes) and
  [trigger actions](#5c-events-and-triggers-eventsjson), which ignore `joinable`,
  the recruiter, conditions and the fee, so a hidden society can recruit through
  a quest. They still respect `enabled`, `max_memberships`, `rejoin_hours` and
  the hostile-rank rule. A refused `join_faction` refuses a dialogue choice, and
  in a trigger only tells the player;
- `faction_join` and `faction_leave` [trigger events](#5c-events-and-triggers-eventsjson).
  These don't fire for a faction chosen at creation, because the character isn't
  in the world yet; use a `member` condition instead.

In World Forge, turn membership on under **World settings → factions**, then
set each faction's joining rules on its record in the **Factions** collection.

### Crime and consequences

Crime is a light layer on top of factions and relationships. It is off until
the world turns it on:

```json
"crime": {"enabled": true, "detection": 70, "fine_expiry_hours": 0,
          "fines": {"theft": 25, "pickpocket": 40, "assault": 75},
          "reputation": {"theft": -50, "pickpocket": -75, "assault": -100}}
```

| Field | Meaning |
| --- | --- |
| `detection` | Each witness's base chance (0–100) to notice a crime. |
| `fine_expiry_hours` | Unpaid fines lapse after this many hours (0–720; `0` keeps them until paid). |
| `fines` | Coins fined for each crime (0–100,000). |
| `reputation` | Standing change for each crime (-1000 to 0), with every faction involved. |

**Crimes.** Only NPCs and their belongings can be victims; players can never
rob or report one another.

- **Theft.** Taking a placed item, or an item from a placed container, whose
  `state_json` names an owner: `{"owner_faction": "guild"}` or
  `{"owner_npc": "stall-keeper"}`, a placed NPC. The item becomes stolen
  goods, which merchants of the owning faction refuse to buy. Once taken it
  is an ordinary item, so dropping and taking it again is no crime. In
  World Forge, set `owner_faction` or `owner_npc` in a placed item's `state_json`.
- **Pickpocketing.** `/steal from <npc>` against an NPC with a `steal`
  behaviour. A failed attempt is always caught by the victim.
- **Assault.** Attacking an NPC that was not hostile to you, when it belongs
  to a faction or has `"lawful": true`. The victim always sees it, once per
  fight.

```json
{"steal": {"difficulty": 30, "cooldown_seconds": 3600,
  "coins": {"minimum": 2, "maximum": 12},
  "items": [{"item": "silver-ring", "chance": 25}]}}
```

A pickpocket succeeds at `50 + score − difficulty` percent (5–95), where the
score is the character's Stealth effect, plus twice their Agility above 10,
plus 15 while hidden. Each NPC can only be robbed once per `cooldown_seconds`
(60 to 604,800, default 3600). Success practices Stealth.

**Witnesses.** Every NPC in the room may notice, except defeated ones and
those with `"witness": false`. Each rolls `detection − Stealth − 2 × (Agility −
10)`, minus 20 more while the thief hides, kept between 5% and 95%. A crime
nobody notices has no consequences. A noticed crime:

- tells the room who was seen;
- fines the player on behalf of the first faction involved: the owner's,
  the victim's, or else a witness's. Without any faction there is no fine;
- costs standing with every faction involved, which passes on through
  relations;
- costs regard with each witness by the [relationship](#relationships)
  `crime` amount;
- fires `crime` [triggers](#5c-events-and-triggers-eventsjson);
- lets any NPC now hostile to the player, such as a guard, attack at once.

**Guards and fines.** An NPC with `"guard": true` and a faction attacks
players who owe that faction fines. Before its first blow it shouts how much
is owed. Players use `/fines` to list what they owe and `/pay` (or
`/pay <faction>`) beside any member of the faction to settle it. Paying ends
the fight with that faction and restores half the standing the crimes cost.
The `wanted` condition bars wanted characters from exits, dialogue, quests,
or anything else that takes conditions:

```json
{"from": "road", "to": "city", "name": "city gate", "direction": "north",
 "conditions": [{"type": "wanted", "faction": "watch", "negate": true}],
 "conditions_message": "The gate guard turns you away until you settle your fines."}
```

## 5g. Player housing and storage

Housing lets players hold a place of their own: a cottage, an apartment, a
barracks bunk, a fallout shelter, or anything else you describe. Storage lets
them keep belongings in the world. Both are optional and work separately: a
world may offer lockers but no homes, homes without storage, or neither.
Nothing changes in a world that defines none of these tables.

### Properties

A property is a set of rooms that only its owners may enter. There are two
kinds:

- **`unique`** (the default): the property's own rooms. One holding at a
  time, so once someone owns the lighthouse nobody else can.
- **`instanced`**: the rooms are a template. Every holder gets a private copy
  of them, with the template's names, descriptions, and exits. Use this for
  apartment blocks, inn rooms, or shelter cells that anyone may rent.

```json
"mud_properties": [
  {"id": "cottage", "name": "Harbor Cottage", "description": "A snug cottage by the water.",
   "entrance_room_id": "harbor-street", "price": 500, "refund_percent": 50,
   "rent": 30, "rent_period_seconds": 604800, "grace_seconds": 86400, "max_owners": 3},
  {"id": "flat", "name": "Apartment", "description": "Two rooms of your own.",
   "kind": "instanced", "acquisition": "claim", "entrance_room_id": "lobby",
   "agent_npc_definition_id": "landlady", "conditions_json": [{"type": "level", "min": 3}]},
  {"id": "bunk", "name": "Shelter Bunk", "description": "A cot behind a blast door.",
   "kind": "instanced", "acquisition": "grant", "entrance_room_id": "shelter-hall"}
],
"mud_property_rooms": [
  {"room_id": "cottage-parlour", "property_id": "cottage"},
  {"room_id": "cottage-loft", "property_id": "cottage"},
  {"room_id": "flat-main", "property_id": "flat"},
  {"room_id": "flat-bedroom", "property_id": "flat"},
  {"room_id": "bunk-cell", "property_id": "bunk"}
]
```

| Field | Default | Meaning |
| --- | --- | --- |
| `kind` | `unique` | `unique` or `instanced`. |
| `entrance_room_id` | required | A room outside the property. Players buy or claim it here (unless it has an agent), and anyone who loses access lands here. |
| `acquisition` | `purchase` | `purchase` (`/house buy`), `claim` (free, `/house claim`), or `grant` (only through a `grant_property` [outcome](#outcomes) or trigger action, for quest rewards, faction quarters, or story homes). |
| `price` | 0 | Coins for a purchase (0–1,000,000). Only purchased properties have a price. |
| `refund_percent` | 0 | Share of the price returned when the primary owner abandons it. |
| `rent` | 0 | Coins per period; 0 means no rent. Split evenly between the owners; the primary owner pays any remainder. |
| `rent_period_seconds` | 604800 | Length of a rent period (one hour to one year). |
| `grace_seconds` | 86400 | How long rent may go unpaid before the share ends (up to 30 days). |
| `prepay_periods` | 4 | How many periods an owner may pay ahead (1–52). |
| `max_owners` | 1 | Owners, including the primary owner (1–20). 1 keeps it private. |
| `agent_npc_definition_id` | none | An NPC beside whom the property is bought or claimed, instead of at the entrance. A defeated or closed agent cannot sell. |
| `conditions_json` | `[]` | [Conditions](#conditions) a character must meet to buy or claim it. Grants ignore them. |
| `available` | 1 | 0 stops new holders without touching existing ones. |

Rules checked on import: every property has at least one room; a room belongs
to at most one property; the entrance is not itself a property room; and
neither the start room nor a start rule's room may be a property room. Property
names are unique within a world.

Movement into property rooms by any route (walking, `/travel`, fleeing,
directions, and `teleport` actions) admits only the property's owners. Exits
into it show as `[private]` to everyone else. Entering an instanced template
room leads each owner to their own copy, so two players in "the" apartment
never meet unless they share one. Copies have no placed items, NPCs,
gathering sources, or room triggers of their own; give them storage instead.
Players cannot drop items, or put them into placed containers, inside a
property, because the floor is not kept for them; they use storage.

### Owners and rent

The character who acquires a property is its **primary owner**. They manage
it: `/house invite <property> <character>` asks someone to share it, and
the invitee must `/house accept <property>` (or `/house decline`). Co-owners
can enter, use the owners' storage, and pay their share of the rent. The
primary owner can `/house remove` a co-owner or withdraw an invitation, and
`/house transfer` the primary role to a co-owner. A co-owner can
`/house leave`. A character can hold each property only once, and at most
`max_holdings` properties in a world (see below).

With rent, buying charges the price plus the first period, and accepting an
invitation charges the first share. Each owner keeps their own share paid
with `/house pay <property> [periods]`, from carried coins, anywhere in the
world. Owners are reminded when their share falls due. When a share stays
unpaid past the grace period, a co-owner stops sharing the property; if it is
the primary owner, the holding ends for everyone. Paid periods are not refunded
when owners come or go, and a change in the number of owners changes each
share from the next payment.

A holding also ends when its primary owner abandons it
(`/house abandon <property> confirm`), when a `revoke_property` outcome takes
it, when a builder releases it (**Live world**) or removes the property, or when the property
is left out of an upgraded package. If a primary owner's character is
deleted, the longest-standing co-owner becomes the primary owner instead.

**Nothing is lost when a holding ends.** Everyone inside steps out to the
entrance, and every stored item goes straight back into the inventory of the
character who stored it. When a co-owner leaves or is removed, only the items
they stored go back with them.

### Storage

A storage point is a named place in a room where players `/storage` items:

```json
"mud_storage_points": [
  {"id": "chest", "room_id": "cottage-parlour", "name": "Oak Chest", "scope": "property", "capacity": 40},
  {"id": "locker", "room_id": "station-hall", "name": "Locker", "scope": "personal", "capacity": 10,
   "item_types": ["misc", "consumable"]},
  {"id": "cache", "room_id": "camp", "name": "Supply Cache", "scope": "communal",
   "conditions_json": [{"type": "member", "faction": "rangers"}]}
]
```

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | required | 2–80 characters, used in `/storage deposit <item> into <name>`. |
| `description` | `""` | Optional text for builders. |
| `scope` | `personal` | `personal`: each character has a private compartment here, like a bank vault. `property`: the owners of the holding share it (property rooms only; each instanced copy has its own). `communal`: one pile anyone who meets the conditions may use. |
| `capacity` | 20 | Items it holds (1–500). Items inside stored bags count too. |
| `item_types` | any | A list of item types it accepts: `misc`, `equipment`, `container`, `consumable`, `key`. |
| `conditions_json` | `[]` | [Conditions](#conditions) to use it. |

Players use `/storage` to list what is here and `/storage deposit <item>` or
`/storage withdraw <item>`, adding `into <storage>` or `from <storage>` when
a room has more than one. Equipped items are unequipped as they go in; loot
from an unfinished dungeon run cannot be stored. Stored items keep their state
(ammunition, open containers and their contents). Taking an item out of shared
or owners' storage makes it yours. Stored items do not count as carried for
conditions, quests, shops, or `/give`. The portal room's personal storage is
separate and unchanged.

### World switch

The `housing` manifest section (or **World settings → housing** in World Forge)
controls the system:

```json
"housing": {"enabled": true, "max_holdings": 3}
```

`enabled` (default `true`) switched off stops anyone acquiring property and
stops rent evictions; owners keep their homes and storage and can still pay
or leave. `max_holdings` (1–20, default 3) caps how many properties a
character may own or share in the world.

In World Forge, properties, property rooms, and storage points are content
collections; **Live world** lists holdings and releases them. A room that
belongs to a property, or is a property's entrance, cannot be deleted until the
property no longer uses it, and a property cannot switch between unique and
instanced while anyone holds it. Upgrading a world keeps holdings, co-owners,
private copies, and stored items by local ID.

## 5h. Vehicles and transportation

Vehicles let players ride: a horse, a bicycle, a car, a rowboat, a stagecoach,
a hover-sled, a salvaged truck. Transit lines carry them between stops for a
fare: a ferry, a tram, a mail coach, a caravan. Both are optional and
independent: a world may have only public buses, only a horse for sale, or
neither. Nothing changes in a world that defines none of these tables.

### How riding works

`/board <vehicle>` in the room where it stands. While aboard, `/go` and
`/travel` move the vehicle, and `/disembark` (or `/dismount`) gets off. There
are two shapes of vehicle:

- **Without an interior** (a horse, a bike, a cart): it carries one rider, who
  stays in the room the vehicle is in and sees that room as usual. Others see
  "Ada rides away north on the Horse."
- **With an interior** (a boat, a coach, a train car): everyone aboard sits in
  a private copy of the interior room, so they can talk and see each other.
  The **operator** steers; passengers ride along. Riders see where the vehicle
  arrives, and `/look out` shows the room outside. People outside see "The
  Rowboat leaves east."

Any other way of moving ends a ride and leaves the vehicle where it stands:
fleeing, a `teleport` trigger, entering a dungeon, dying, or being moved by a
builder. Vehicles never enter dungeons or property rooms. Riders inside an
interior do not set off `enter_room` triggers or meet wilderness creatures in
the rooms the vehicle passes, but they do complete `visit_room` objectives and
learn destinations there. Nobody can drop items aboard.

### Defining vehicles

```json
"mud_vehicle_definitions": [
  {"id": "rowboat", "name": "Rowboat", "description": "A tarred boat with two oars.",
   "modes": ["water"], "terrains": ["water"], "mode_only": true, "interior_room_id": "rowboat-deck",
   "fuel_capacity": 20, "return_seconds": 600},
  {"id": "horse", "name": "Horse", "description": "A bay mare.", "terrains": ["grassland", "road", "hills"],
   "speed": 200, "fuel_capacity": 30, "fuel_item_id": "oats", "fuel_per_item": 10},
  {"id": "car", "name": "Old Sedan", "description": "Rust and stubbornness.", "modes": ["road"],
   "terrains": ["road"], "speed": 300, "fuel_capacity": 40, "fuel_per_move": 2,
   "conditions_json": [{"type": "has_item", "item": "driving-licence"}],
   "conditions_message": "You need a licence to drive."},
  {"id": "coach", "name": "Family Coach", "description": "Four seats and a roof.",
   "interior_room_id": "coach-seat", "shareable": true}
],
"mud_vehicle_placements": [
  {"id": "harbor-boat", "vehicle_definition_id": "rowboat", "room_id": "harbor"}
]
```

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | required | 2–80 characters, used with `/board`. Unique within a world. |
| `description` | `""` | Up to 2000 characters. |
| `modes` | none | Up to 8 words of your own (`water`, `rail`, `air`, `road`, `snow`), each a short lowercase key. Exits and presets can require one. |
| `terrains` | any | Wilderness terrains it can cross (`grassland`, `forest`, `dense-forest`, `hills`, `mountains`, `desert`, `swamp`, `tundra`, `water`, `road`, `custom`). Leave it out to go anywhere a walker can. |
| `mode_only` | `false` | It keeps to its own ways: exits that need one of its modes, wilderness of its terrains, and authored rooms right beside that wilderness (a dock or a station). A boat cannot roll down the high street. Needs `modes` or `terrains`. |
| `speed` | 100 | Percent of walking pace for `/travel` (25–400). 200 moves twice as fast; one step every two seconds is the limit. |
| `interior_room_id` | none | A room in `rooms.json` that every vehicle of this kind copies for its riders. It must have no exits and not be the start room, a property, a stop, or used by anything else. |
| `shareable` | `false` | An owned vehicle with an interior may take passengers while its owner is aboard. |
| `fuel_capacity` | 0 | Fuel it holds; 0 means it needs none. |
| `fuel_per_move` | 1 | Fuel each move costs. A vehicle out of fuel cannot move until refuelled. |
| `fuel_item_id` | none | An item that `/refuel item` turns into fuel anywhere, e.g. oats or a fuel can. |
| `fuel_per_item` | 10 | Fuel one item gives. |
| `return_seconds` | 0 | A public vehicle left with nobody aboard goes back home, refuelled, after this long (up to a week). 0 leaves it where it was left. |
| `conditions_json` | `[]` | [Conditions](#conditions) to board it, for a licence, a skill, a faction, or a time of day. |
| `conditions_message` | `""` | Shown to anyone refused. |

**Public vehicles** come from `mud_vehicle_placements`: each placement keeps
one vehicle of that kind in its room, full of fuel, for anyone who meets the
conditions. The first to board is the operator. When the operator gets off,
the longest-aboard passenger takes over, and a passenger may take the
controls from an operator who has gone offline.

**Owned vehicles** come from a `grant_vehicle` [outcome](#outcomes) or trigger
action. Only the owner may board an owned vehicle, and they always steer. A
`shareable` one takes passengers while its owner is aboard, and when the owner
gets off everyone else steps off with them. To sell a vehicle, give a merchant
a dialogue response:

```json
{"id": "buy-horse", "node_id": "stable-hello", "response_text": "Buy the bay mare (120 coins).",
 "conditions_json": [{"type": "coins", "min": 120}, {"type": "has_vehicle", "vehicle": "horse", "negate": true}],
 "outcomes_json": [{"type": "take_coins", "amount": 120}, {"type": "grant_vehicle", "vehicle": "horse"}]}
```

Worlds that only need buses or ferries never have to use ownership.

### Ways that need a vehicle

An exit with `"requires_mode"` admits only riders whose vehicle travels by
that mode; an exit with `"allow_vehicles": false` admits only walkers (see
[Connect rooms](#3-connect-rooms)). A wilderness preset with `"requires_mode"`
does the same for every room of that preset, so open sea or the sky needs a
boat or a balloon however it is reached. Exits show `[needs water]` or
`[on foot]` to each player as it applies to them.

These rules are the same everywhere a player can move: walking, `/travel`,
fleeing, `/directions` and destination headings, which plan routes for what
the player is riding at the moment. A boat can be routed across the bay; a
walker cannot.

### Fuel

Vehicles with a `fuel_capacity` spend `fuel_per_move` on every move,
including each `/travel` step, and stop when they run dry. Refuel with
`/refuel` at a fuel station, buying all that is missing or `/refuel <units>`,
or with `/refuel item` anywhere, using one `fuel_item_id` item:

```json
"mud_fuel_stations": [
  {"id": "pump", "room_id": "garage", "price": 3},
  {"id": "boathouse", "room_id": "harbor", "price": 1, "modes": ["water"]}
]
```

`price` (0–100,000) is coins per unit; `modes`, when given, limits a station to
vehicles of those modes. A rider out of fuel somewhere they cannot step off
(open sea) can still `/disembark`: the vehicle is towed home and they land
there.

### Transit lines

A line is a service between stops. Riders do not steer anything: they pay,
wait out the trip, and arrive.

```json
"mud_transit_lines": [
  {"id": "ferry", "name": "Coast Ferry", "description": "Twice a day across the bay.",
   "fare": 5, "fare_per_stop": 2, "cabin_room_id": "ferry-cabin",
   "conditions_json": [{"type": "time", "from": 6, "to": 20}],
   "conditions_message": "The ferry runs from dawn to dusk."},
  {"id": "tram", "name": "Street Tram", "description": "Clanks along Main Street.", "fare": 1}
],
"mud_transit_stops": [
  {"id": "ferry-harbor", "line_id": "ferry", "room_id": "harbor", "sort_order": 0},
  {"id": "ferry-isle", "line_id": "ferry", "room_id": "isle", "sort_order": 1, "travel_seconds": 300},
  {"id": "tram-1", "line_id": "tram", "room_id": "market", "sort_order": 0},
  {"id": "tram-2", "line_id": "tram", "room_id": "museum", "sort_order": 1, "travel_seconds": 40},
  {"id": "tram-3", "line_id": "tram", "room_id": "depot", "sort_order": 2, "travel_seconds": 40}
]
```

| Field | Default | Meaning |
| --- | --- | --- |
| `fare` | 0 | Coins for any trip. |
| `fare_per_stop` | 0 | Extra coins for each stop travelled. |
| `cabin_room_id` | none | A room riders sit in during the trip, shared by everyone on the line. Same rules as a vehicle interior. Without one, riders wait where they boarded, and any move gives up the ride without a refund. |
| `conditions_json` | `[]` | [Conditions](#conditions) to ride. A `time` condition gives the line operating hours. |
| `conditions_message` | `""` | Shown to anyone refused. |
| stop `sort_order` | 0 | The order of stops along the line. Lines run both ways. |
| stop `travel_seconds` | 60 | Seconds from the stop before this one (5–86,400); the first stop's is ignored. |

Players see the lines at a stop when they look. `/ride` lists where each line
goes from here with its time and fare; `/ride <stop>` or
`/ride <line> to <stop>` pays and departs; `/ride status` tells how long is
left; `/ride cancel` stops waiting where there is no cabin. Riders arrive even
if they log off, and arriving counts as entering the room (objectives, room
triggers, private property rules).

### Rules checked on import

Vehicle and line names are unique within a world. Interior and cabin rooms
belong to exactly one vehicle or line, have no exits, and are not the start
room, a property room, a stop, a placement, a fuel station, or a start rule's
room. A line needs at least two stops. Fuel items must exist. A warning notes
modes that ways require but no vehicle in the world provides.

### World switch

The `transport` manifest section (or **World settings → transport** in World
Forge) controls the system:

```json
"transport": {"enabled": true}
```

Switched off, nobody can board, refuel, or buy a ride; anyone already aboard
can still drive and get off, and rides under way still arrive.

In World Forge, vehicles, vehicle placements, fuel stations, transit lines, and
stops are content collections, and **Live world** lists every vehicle in the
world (send one home or remove it); each passage has `requires_mode` and
`allow_vehicles`. Removing a vehicle kind or a placement lets its
riders off where it stands, and removing a line delivers its riders at once.
Upgrading a world keeps owned vehicles where they stood, by local ID: riders
step off beside them, rides under way arrive at their stops, and public
vehicles return to their placements.

## 6. Import, edit, export, and remove

Download the starter ZIP from **SYSOP Settings → MUD service → Traditional
Worlds**, or zip the files at the archive root yourself. For the four example
files above, run one of these commands from the directory containing them:

```powershell
Compress-Archive -Path manifest.json,rooms.json,exits.json,content.json -DestinationPath lantern-isles.zip
```

```sh
zip lantern-isles.zip manifest.json rooms.json exits.json content.json
```

If you omit `content.json`, `origins.json`, `skills.json`, `abilities.json`, `effects.json`, `dungeons.json`, `events.json`, or `wilderness.json`, omit them from the command too. Confirm the ZIP
lists the JSON files directly, without a containing folder. In **SYSOP Settings → MUD service →
Worlds**, import the ZIP. Choose whether the world offers only its bundled races,
classes, and skills, offers them alongside the built-ins, or offers a manual
selection, and tick **Enable it for players right away** if it is ready; other
worlds keep running unchanged. Use **ENABLE** or **DISABLE** in the world list at
any time: a disabled world disappears from the player world list but keeps its
content and characters. The **WORLD FORGE** link edits that world, enabled or not. World Forge's
**Live world → Download ZIP** downloads the published world, and **Export draft
ZIP** downloads the draft. For the home world, the exported manifest uses
`my-world` as a starter ID; change it before importing on the same station.
Exports keep the IDs content was imported with (older content gets simple IDs
such as `room-3`), so exporting twice gives the same files.

Exports are self-contained. Every custom race, class, skill, ability, and effect
the world owns, offers, or uses in its content, events, and dungeons is bundled,
along with its progression and race/class/skill selection. In sysop settings you
may instead pick which races, classes, and skills to bundle; anything the
content still needs is added anyway. References that cannot travel, such as an
NPC schedule naming a room in another world, are listed as warnings (World
Forge's **Live world → Check export** shows them); tick the strict option to refuse such an
export instead.

### Validation messages

Choosing a ZIP in the import form checks it completely, including a trial
install that is rolled back, and lists every problem at once with its file and
JSON path, for example `origins.json $.classes[0].spells[2]`. Each problem has a
category:

| Category | Meaning |
| --- | --- |
| `invalid` | A value is malformed or out of range. |
| `missing_reference` | Something names a room, item, ability, effect, or other definition that is neither in the package nor installed. |
| `unsupported` | A field or feature the engine does not have. |
| `engine_reserved` | An attempt to change an engine rule, or to redefine a built-in ability or effect. |
| `conflict` | A bundled definition differs from one another installed package also uses, or the world is already installed. |
| `legacy` | A format 1 package uses something format 2 would reject; it still imports. |

### Shared definitions

Bundled races, classes, skills, abilities, and effects are installed under keys
derived from the world ID, so two worlds never collide. Importing a definition
identical to one already installed reuses it. A changed version replaces the old
one when only this world uses it; when another installed package also uses it
the import stops with a conflict. A world's bundled races, classes, and skills
are offered in that world only; other worlds offer them only if a sysop adds
them.

### Upgrade an installed world

Importing a ZIP whose ID is already installed shows what would change (rooms,
content, definitions, and how many characters stand in rooms the new version
removes) and imports it as an upgrade after you confirm. The upgrade runs as
one transaction: if anything fails, the installed world is left untouched.
Characters keep their level, gear, and coins. Quests, objectives, reputation,
NPC regard, learned destinations, held items, and event history carry over to
content that keeps the same IDs; characters in removed rooms move to the start
room. Dungeon runs in progress end. The world keeps its enabled state and its
race, class, and skill selection, plus any new bundled choices. A World Forge
draft started before the upgrade is marked out of date; rebase it, or publish it
to replace the upgrade. Optionally reset the world's flags and event history.

To uninstall a custom world, disable it first, then use **Remove World**.
Removal deletes the world's rooms and content and ends its dungeon runs. Its
characters are kept, hidden, and return if a world with the same ID is
installed again. With **Also remove this world's unshared races, classes, and
skills**, its own abilities and effects go too, unless another package uses them.
Definitions still held by characters (a class, an effect burning on someone, an
item a player carries) are kept, hidden from new use, so nothing a player has
breaks. The original Station World cannot be uninstalled.

The installed ZIP is kept for sharing. Exports are read from the database, so
published World Forge changes are included the next time you export. Removing a
world deletes its rooms and placed content while keeping player possessions
and quest records intact.

<a id="race-class-reference"></a>
## Race and class reference

StationNet accepts individual race or class UTF-8 JSON files and combined
origin packs in SYSOP Settings > MUD service. Download a template there, or
start with the complete examples below. The same choices appear in traditional
and procedural character creation. Each individual file is limited to 32 KB.

Races and classes do more than adjust starting attributes. A definition can
give lasting bonuses, traits that world content reacts to, weapon and armor
training, resistances, innate abilities, faster learning for chosen skills,
and extra health, mana, or skill points as the character levels. Every one of
these fields is optional, so older definitions keep working unchanged.

<a id="race-class-origin-packs"></a>
### Origin packs

A pack uses the same `origins.json` object convention as a bundled MUD world:
it contains a `races` array, a `classes` array, or both. Each entry is an
ordinary definition in the formats documented below.

```json
{
  "races": [{"key": "riverfolk", "name": "Riverfolk", "description": "Travelers from the riverlands.", "traits": "Sure footing", "attributes": {"agility": 1}, "effects": {"defense": 1}}],
  "classes": [{"key": "pathfinder", "name": "Pathfinder", "description": "A guide on uncertain ground.", "traits": "Trail craft", "attributes": {}, "effects": {}, "skill": "guard", "spells": [], "equipment": []}]
}
```

Packs are limited to 1 MB, 100 races, and 100 classes. Keys and names must be
distinct within each type and cannot conflict with installed choices. Import
is atomic: if one entry is invalid or conflicts, nothing in the pack is
installed. A pack may omit either array, but it must contain at least one
definition.

<a id="race-class-complete-race-example"></a>
### Complete race example

Save as `riverfolk.json` and import as a race:

```json
{
  "key": "riverfolk",
  "name": "Riverfolk",
  "description": "Travelers who make their homes beside rivers and marshes.",
  "traits": "Sure footing",
  "attributes": {"agility": 1},
  "effects": {"defense": 1, "flee": 5},
  "skills": {"evasion": 1},
  "tags": ["amphibious", "river-born"],
  "proficiencies": {"weapons": ["blade"]},
  "skill_affinities": {"exploration": 25, "evasion": 50},
  "resistances": {"poison": 25, "snared": 50},
  "progression": {"health_per_level": 1}
}
```

<a id="race-class-complete-class-example"></a>
### Complete class example

Save as `pathfinder.json` and import as a class:

```json
{
  "key": "pathfinder",
  "name": "Pathfinder",
  "description": "A guide practiced at crossing uncertain ground.",
  "traits": "Trail craft",
  "attributes": {"agility": 1},
  "effects": {"defense": 1, "search": 3},
  "skill": "guard",
  "spells": ["ward"],
  "tags": ["wilderness"],
  "proficiencies": {"weapons": ["blade"], "armor": ["light"]},
  "skill_affinities": {"exploration": 50},
  "progression": {"health": 5, "skill_points": 1},
  "equipment": [
    {"key": "trail-knife", "name": "Trail Knife",
     "description": "A short, sturdy knife carried on the trail.",
     "slot": "main_hand", "attack": 1, "defense": 0, "accuracy": 2,
     "category": "blade"}
  ]
}
```

Starter equipment is placed in the character's inventory; the player equips
it with `/equip <item>`. For firearms, ammunition, consumables, and combat
effects, define items in a [traditional world](#world-package-reference)
or [dungeon theme](#dungeon-theme-reference).

<a id="race-class-fields-and-validation"></a>
### Fields and validation

| Field | Race | Class | Rules |
| --- | --- | --- | --- |
| `key` | required | required | 2 to 40 lowercase letters, digits, or hyphens; starts with a letter. Unique within its choice type, including removed choices still used by characters. |
| `name` | required | required | 2 to 80 nonblank characters. Must be unique within its choice type among installed choices. |
| `description` | required | required | 1 to 1,000 nonblank characters. |
| `traits` | required | required | 1 to 160 nonblank characters; display text, not a scripted ability. |
| `attributes` | optional | optional | Object with any of `strength`, `agility`, `intellect`, `vitality`. Each modifier is an integer from -10 to 10; `{}` is valid. |
| `effects` | optional | optional | Lasting bonuses. Object with any skill effect type (see below); each value is an integer from -100 to 100; `{}` is valid. |
| `skills` | optional | optional | Object granting starting ranks, such as `{"perception": 1}`. Keys must be installed skills; ranks are 1 to 5. Granted skills do not count toward the player's starting choices. |
| `skill` | — | required | Favored skill key; any installed skill (default or imported). It starts at rank 2 if the player picks it. |
| `spells` | optional | optional | Array of up to 12 distinct installed ability keys (built in, imported, or bundled with the same package); `[]` gives no spells. On a race these are innate abilities, known in addition to the class's spells. |
| `equipment` | optional | optional | Array of up to eight starter items; `[]` gives none. |
| `tags` | optional | optional | Up to 12 lowercase keys, such as `["darkvision", "undead"]`. Tags mean nothing by themselves; world content, items, and dialogue can require or exclude them. |
| `proficiencies` | optional | optional | `{"weapons": [...], "armor": [...]}` gear categories this origin is trained with, as if a skill granted them. |
| `resistances` | optional | optional | Percentages from -100 to 100 against `physical` (enemy blows) and the hostile effects `poison`, `burning`, `bleeding`, `weakness`, `vulnerability`, `blinded`, `snared`. 100 is immunity; negative values are vulnerabilities. |
| `skill_affinities` | optional | optional | Up to 12 entries mapping an installed skill key or a skill group (`combat`, `weapon`, `armor`, `magic`, `stealth`, `exploration`, `social`, `knowledge`) to a percentage from -50 to 200. Practice toward those skills is scaled by it. |
| `progression` | optional | optional | Growth on top of the usual rules: `health` and `mana` (-50 to 50), `health_per_level` and `mana_per_level` (-5 to 5), `skill_points` (0 to 10), and `starting_skill_choices` (0 to 3 extra picks at creation). |

<a id="race-class-effects"></a>
#### Effects

`effects` accepts the same effect types as skills: `attack`,
`melee_damage`, `firearm_damage`, `spell_power`, `healing`, `defense`,
`accuracy`, `evasion`, `experience_percent`, `stealth`, `flee`, `search`,
`lockpick`, and `shop_discount`. Origin effects always apply. For a bonus
that grows with rank or only applies with certain gear, define a custom skill
(see the [skills guide](#skill-reference)) and grant it
through `skills`.

<a id="race-class-how-the-other-fields-play-out"></a>
#### How the other fields play out

- **Resistances.** `physical` scales the damage of enemy blows that land
  after armor; any resistance below 100 still lets at least 1 point through.
  Effect resistances scale the potency and duration of hostile effects
  enemies inflict. An effect reduced to nothing is shrugged off. Equipped
  gear can add [resistances](#protective-gear-cures-and-hazards) of its own,
  and the totals stay within -100 to 100.
- **Skill affinities.** A skill's own key takes priority over its group. An
  affinity of 50 makes practice count 50% more; -50 halves it, but an action
  that teaches a skill always teaches at least one point.
- **Progression.** `health` and `mana` change maximums immediately;
  per-level amounts apply for every level above 1. Changes are kept in step
  automatically when a character levels, changes origin, or when a built-in
  definition is rebalanced. `skill_points` adds to the points earned from
  levels. `starting_skill_choices` raises how many skills a player must
  choose at creation.
- **Tags.** Tags are setting-defined. Quests, dialogue responses, and world
  exits can require them with a `tag` condition, and items can require or
  exclude them (see [custom worlds](#world-package-reference)).
  Prefer tags over listing race keys when several origins share a trait,
  such as several races that see in the dark.

Each equipment entry needs a unique `key` within its race or class, a `name`
of 2 to 80 characters, a `description` of 1 to 1,000 characters, and a
`slot`. Slots are `head`, `body`, `hands`, `legs`, `feet`, `main_hand`,
`off_hand`, and `accessory`. Optional `attack` and `defense` are integers
from 0 to 100 and default to zero; optional `accuracy` and `evasion` are
integers from -20 to 20. Optional `category` (a lowercase key such as
`blade`, `axe`, `heavy`, or `shield`) ties the item to weapon and armor
proficiency; list the category under `proficiencies` or grant a matching
skill so the character can use it fully. Origin equipment supports these
basic bonuses; its JSON cannot specify firearm or consumable properties.

Character creation allocates points according to `stationnet/character_options.json`
(or Flask's `MUD_CHARACTER_OPTIONS` file), then adds race and class modifiers.
**Every final attribute must be from 1 to 30.** An origin can import
successfully yet produce an invalid race/class/point allocation combination,
so test combinations players can select. A class's favored skill starts at
rank 2 if the player selects it among starting skills; other selected skills
start at rank 1. Skills are managed separately; see the
[skills guide](#skill-reference) to add new ones.
Origin imports cannot change the point budget.

<a id="race-class-spells-and-mana"></a>
### Spells and mana

`spells` may appear on a class or a race; a character knows both lists. The
supported keys and mana costs are:

| Spell | Mana | Effect |
| --- | ---: | --- |
| `ember` | 8 | Enemy damage and burning. |
| `venom` | 7 | Enemy damage and poison. |
| `rend` | 6 | Enemy damage and bleeding. |
| `hex` | 7 | Enemy damage, weakness, and vulnerability. |
| `mend` | 7 | Self healing. |
| `ward` | 6 | Self defense effect. |
| `might` | 6 | Self attack effect. |

Players use `/spells` to list known spells, `/cast <spell> <enemy>` for the
four damaging spells, and `/cast <spell>` for `mend`, `ward`, or `might`.
`/rest` restores mana outside combat when no hostile enemy is nearby.
Intellect raises starting mana, mana gained on level up, and spell strength.
Spells spend mana from the active traditional or procedural progression track.
Spells must name installed abilities. To add a new one, install it from **SYSOP Settings → Abilities & Effects** or bundle it in the world package or dungeon theme; see the [abilities guide](#ability-effect-reference).

A traditional world may rename mana (for example to Energy) or disable it; see
the [world guide](#rename-or-disable-mana).
Renaming changes only the wording. In a world without the resource, spells are
unavailable and mana growth is hidden, but `progression` mana values still
apply, so they take effect again in procedural dungeons or if the world turns
the resource back on.

<a id="race-class-install-and-maintain-choices"></a>
### Install and maintain choices

1. Pick stable keys. Save one valid UTF-8 JSON object under 32 KB, or group
   definitions into a pack under 1 MB.
2. Import it in the matching individual control or the Origin Pack control.
   Correct any validation error, then
   create a test character to check its combinations, gear, and spells.
3. Installed choices are shared by the station, and each world picks the ones
   it offers on its **RACES, CLASSES & SKILLS** page. An import is offered in
   the worlds ticked on its form (by default, every enabled world). Disabling
   a choice in one world hides it from new characters there only; existing
   characters keep it. Every world offers at least one race and one class.
4. Removing an imported choice takes it out of every world. Its definition is
   kept while any character or archived campaign still uses it, and the key
   stays reserved. Characters that used it keep their progress and choose a
   replacement on their next visit. A world left without any race or class
   offers the built-in ones again. Built-in choices can be disabled in a world
   but cannot be removed.

The character-options file is a separate, station-wide configuration source
for the point budget, skill list, and built-in races/classes. If you provide
one through `MUD_CHARACTER_OPTIONS`, it must include `id`, `point_buy`,
`attribute_descriptions`, `races`, `classes`, `skills`, and
`starting_skill_choices`; see `stationnet/character_options.json` for that
advanced format. Imports add choices without replacing existing definitions.
Selection shortcuts on each world's page select all installed choices, none,
the built-ins, or that world's bundled choices. Imported choices can also be selected and removed together;
the confirmation summary lists every affected race and class before submission.

<a id="skill-reference"></a>
## Skill reference

Skills give characters combat, weapon and armor proficiency, magic, stealth,
exploration, and merchant bonuses. They are plain JSON, so SYSOPs and world
creators can add skills without changing Python code. Every accepted effect
changes a real game rule; definitions that name an unknown effect or trigger
are rejected when you import them.

Players:

- choose starting skills during character creation (two by default);
- are granted skills by their race and class;
- improve skills by using them;
- earn **skill points** as they level, and spend them with `/learn <skill>`
  (a new skill at rank 1) or `/practice <skill>` (one more rank);
- can pay **trainer NPCs** in coins with `/learn <skill> from <trainer>` and
  `/practice <skill> from <trainer>`.

`/skills` lists ranks, progress, current effects, skill points, and learnable
skills. `/skills <name>` shows one skill in detail.

<a id="skill-installing-skills"></a>
### Installing skills

**SYSOP CONTROL → Station settings → MUD service → Skills** has three parts:

- **Availability.** A checkbox for every default and imported skill, with
  SELECT ALL / NONE / DEFAULT / IMPORTED shortcuts.
- **Import.** Import a single skill file (`skill.json`, at most 32 KB) or an
  atomic pack (`pack.json`, `{"skills": [...]}`, at most 1 MB and 100 skills).
  A pack is rejected as a whole if any skill is invalid. Skills inside one
  pack may require each other.
- **Removal.** Bulk removal of imported skills, with a summary of what each
  removal will do.

Skills can also travel with content. World ZIPs may include `skills.json` (see
the [world guide](#world-package-reference)), and dungeon
themes may include a top-level `skills` array (see the
[dungeon guide](#dungeon-theme-reference)). A world's
bundled skills are offered in that world according to its import choice; no
other world offers them until a SYSOP adds them.

<a id="skill-per-world-availability-and-removal"></a>
#### Per-world availability and removal

Installed skills are shared by the whole station, and each world chooses which
ones it offers on its **RACES, CLASSES & SKILLS** page in SYSOP Settings.
Standalone imports are offered in the worlds ticked on the import form (by
default, every enabled world).

- **Skills a world does not offer go dormant there.** Characters in that world
  keep their ranks and progress, but the skill gives no effect and does not
  practice. It is shown as inactive. Skill points spent on it are returned
  while it is not offered. Offering it again restores everything. Other worlds
  are unaffected.
- **Default skills** can be withdrawn from a world but never removed.
- **Blocked removals.** An imported skill cannot be removed while an installed
  race or class names it (as `skill` or in `skills`), or while another installed
  skill requires it. Remove those together, or update them first.
- **Removal reaches every world.** A removed skill leaves every world's
  selection.
- **Skills characters still hold** are kept as removed-but-reserved definitions
  (including skills in archived campaigns), so ranks stay readable and the key
  cannot be reused for a different skill.
- **Trainer lessons** for a removed skill are deleted.
- **Minimum offered.** Every world offers at least `starting_skill_choices`
  skills with `"starting": true`. If a removal would leave a world short, that
  world offers the default starting skills again.

<a id="skill-example"></a>
### Example

```json
{
  "key": "spear-fighting",
  "name": "Spear Fighting",
  "group": "weapon",
  "description": "Reach and footwork with spears and polearms.",
  "starting": true,
  "max_rank": 20,
  "effects": [{"type": "melee_damage", "per_rank": 1, "every": 2, "max": 8, "weapon_category": "spear"}],
  "proficiencies": {"weapons": ["spear"], "armor": []},
  "practice": {"triggers": ["melee_hit"], "rate": 1},
  "learn": {"cost": 1, "trainer_required": false},
  "requirements": {"level": 1, "attributes": {"strength": 10}, "races": [], "classes": [], "skills": {}}
}
```

Only `key`, `name`, `description`, and at least one effect or proficiency are
required. Everything else has a default.

<a id="skill-fields"></a>
### Fields

| Field | Default | Rules |
| --- | --- | --- |
| `key` | required | 2–40 lowercase letters, digits, or hyphens, starting with a letter. Unique, including removed skills still held by characters. |
| `name` | required | 2–80 characters; unique among installed skills. |
| `description` | required | 1–1,000 characters. Shown in `/skills` and character creation. |
| `group` | `combat` | One of `combat`, `weapon`, `armor`, `magic`, `stealth`, `exploration`, `social`, `knowledge`. Used for grouping. |
| `starting` | `true` | Whether players may pick it during character creation. Skills with `false` can still be learned later. |
| `max_rank` | `20` | 1–20. |
| `effects` | `[]` | Up to eight effects (below). |
| `proficiencies` | none | `{"weapons": [...], "armor": [...]}` item categories this skill makes the character proficient with. |
| `practice` | none | `{"triggers": [...], "rate": 1}`. `rate` (1–10) is the practice gained per use. With no triggers, the skill only grows with points or trainers. |
| `learn` | `{"cost": 1, "trainer_required": false}` | `cost` is 0–10 skill points. If `trainer_required` is true, `/learn` only works at a trainer NPC that teaches the skill. |
| `requirements` | none | `level` (1–50), `attributes` (minimums 1–30), `races` and `classes` (lists of allowed keys; empty means any), `skills` (`{"key": rank}`). |

<a id="skill-effects"></a>
#### Effects

Each effect is `{"type": ..., "per_rank": 1, "every": 1, "max": ...}`. The
bonus at rank *r* is `(r // every) × per_rank`, capped at `max` if given.
`per_rank` is 1–100, `every` is 1–20, and `max` is 1–1,000.

| Type | What it changes |
| --- | --- |
| `attack` | Melee and firearm damage. |
| `melee_damage` | Melee damage only. Optional `weapon_category` limits it to an equipped weapon of that category. |
| `firearm_damage` | Firearm damage only. Optional `weapon_category`. |
| `spell_power` | Damage from offensive spells. |
| `healing` | Health restored by healing spells. |
| `defense` | Mitigation against enemy hits. Optional `armor_category` limits it to equipped gear of that category. |
| `accuracy` | Added to the chance to hit with melee, firearms, and spells. Optional `weapon_category` applies it only while a weapon of that category is equipped. |
| `evasion` | Subtracted from enemies' chance to hit you. Evasion above 20 counts half, and enemies always keep at least a 30% chance. |
| `experience_percent` | Percentage bonus to experience earned. |
| `stealth` | Percentage points added to hiding and sneaking past enemies (total chance at most 95%). |
| `flee` | Percentage points added to escape chance (total at most 95%). |
| `search` | Score for `/search` against discoveries with a search difficulty. |
| `lockpick` | Score for `/pick` against exits with a lock difficulty. |
| `gathering` | Percentage points added to gathering success chance, capped at 100%. |
| `shop_discount` | Percentage off merchant prices; all sources together are capped at 50%. |

Search and lockpicking use one formula: chance = 50% + skill score +
2 × (Intellect or Agility − 10) − difficulty, kept between 5% and 95%.
Builders set difficulties in World Forge.

<a id="skill-practice-triggers"></a>
#### Practice triggers

| Trigger | Fires when |
| --- | --- |
| `melee_hit` | The character lands a melee attack (misses and dodged attacks do not count). |
| `firearm_hit` | The character lands a firearm shot. |
| `spell_cast` | The character lands an offensive spell. |
| `dodge` | The character's evasion turns an enemy attack aside. |
| `heal_cast` | The character casts a healing spell. |
| `damage_taken` | An enemy hit actually deals damage (reloads and fully blocked hits do not count). |
| `defend` | The character braces with `/defend`. |
| `hide` | The character attempts `/hide`. |
| `flee` | The character attempts `/flee`. |
| `search` | The character searches a room that still hides a discovery with a difficulty. |
| `pick_lock` | The character attempts `/pick`. |
| `experience_gained` | The character earns experience: one practice per 25 XP, minimum one. |
| `purchase` | The character buys from a merchant. |
| `gather` | The character successfully gathers from a replenishing room source that requires this skill. |
| `work` | The character completes an authored work activity. An activity's selected skill practices on payment even if it does not list this trigger. A skill used only for work may omit effects and proficiencies when it lists `work`. |

A skill with `proficiencies` practices `melee_hit`, `firearm_hit`,
`damage_taken`, and `defend` only while the character has matching gear
equipped. For example, Blades improves only when fighting with a blade.

<a id="skill-progression"></a>
### Progression

- **Rank cost.** Advancing from rank *r* costs `6 × r` practice, so rank 10
  takes about 270 uses.
- **Soft cap.** A skill can be at most 4 ranks above the character's highest
  level, and never above `max_rank`. At the cap, practice stops one point
  short of the next rank and the player is told which level unlocks it.
- **Feedback.** Rank-ups are announced as they happen.
- **Skill points.** A character earns one point at every even level and a
  bonus point at every fifth level, based on their highest level across
  progression tracks. Existing characters receive points for levels they
  already have.
  - `/learn` spends the skill's `cost`.
  - `/practice <skill>` spends one point per rank, up to the soft cap.
  - Trainer lessons cost the trainer's price in coins, multiplied by the
    current rank, up to the lesson's maximum rank.

<a id="skill-weapon-and-armor-proficiency"></a>
### Weapon and armor proficiency

Items can set `properties_json.category` (for example `"blade"`, `"axe"`,
`"heavy"`, `"shield"`, `"firearm"`) in World Forge, world content, or a
class's starter equipment.

- **Untrained penalty.** When a character equips categorized gear without a
  skill whose `proficiencies` lists that category, the item gives three
  quarters of its attack and defense, and the player is told why.
- **Uncategorized gear** (all content created before skills existed) is never
  penalized.

The default skills cover these categories:

| Skill | Categories |
| --- | --- |
| Blades | `blade` |
| Heavy Weapons | `blunt`, `axe` |
| Marksmanship | `firearm` |
| Light Armor | `light` |
| Heavy Armor | `heavy`, `shield` |

<a id="skill-races-and-classes"></a>
### Races and classes

Races and classes may grant skills with `"skills": {"perception": 1}` (ranks
1–5). Granted skills are added at creation, don't count toward the starting
choices, and are added (never lowered) when a character replaces a removed
race or class. A class's favored `skill` starts at rank 2 if the player picks
it.

Races and classes also interact with skills in three other ways:

- `skill_affinities` speeds up or slows down practice for named skills or
  whole skill groups, for example `{"magic": 50, "stealth": -25}`.
- `proficiencies` grants gear categories directly, so a class can be trained
  with `heavy` armor without holding the Heavy Armor skill.
- `progression.skill_points` adds skill points on top of those earned from
  levels.

Removing a skill that an installed race or class names in `skills`, `skill`,
or `skill_affinities` is refused until that definition is updated. To give a
race or class a bonus that scales with rank or depends on equipped gear,
define a custom skill and grant it. See the
[race and class guide](#race-class-reference).

<a id="skill-compatibility"></a>
### Compatibility

- **Older skill files.** Character-option files using the earlier
  `{"effect": "attack", "per_rank": 1}` skill form still load; they are
  converted to the new `effects` list.
- **Existing characters.** Characters keep every rank they had.
- **Behavior change.** Arms now affects melee only; Marksmanship replaces its
  old firearm bonus.

<a id="ability-effect-reference"></a>
## Ability and effect reference

Abilities are what characters cast with `/cast`. Effects are the timed
conditions that abilities, items, NPC attacks, and triggers put on a character
or an enemy. StationNet ships seven abilities and twelve effects, and you can
add more. Custom ones are built only from the engine's fixed parts, so every
one you install works in play; none of them can add a new mechanic.

Install them station-wide from **SYSOP Settings → Abilities & Effects** (one
ability, one effect, or a pack), bundle them in a world package
(`abilities.json`, `effects.json`), bundle them in a dungeon theme (schema 2
`abilities` and `effects`), or write a world's own in World Forge's
**Abilities** and **Effects** collections. Templates: `ability.json`, `effect.json`, `pack.json`.

<a id="ability-effect-effects"></a>
### Effects

```json
{"key": "frostbite", "name": "Frostbite", "description": "Numbing cold that bites each turn.",
 "kind": "damage", "resist_as": "burning"}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `key` | yes | 2–40 lowercase letters, digits, or hyphens. Cannot reuse a built-in key. |
| `name` | yes | 2–40 characters; what players see. |
| `description` | no | Up to 500 characters. |
| `kind` | yes | What it does each combat turn. See below. |
| `sign` | stat kinds only | `1` for a bonus, `-1` for a penalty. |
| `resist_as` | no | A resistance that weakens it: `physical`, `poison`, `burning`, `bleeding`, `weakness`, `vulnerability`, `blinded`, or `snared`. Only harmful effects can be resisted. Equipment may also resist an effect by its own key. |
| `outside_combat` | no | `true` makes it also act, and wear off, every 30 seconds while its target is out of combat. It never takes a player below 1 health there. Default `false`: it acts only on combat turns and `/rest`. |

| `kind` | Each turn |
| --- | --- |
| `damage` | Deals its potency in damage. |
| `regen` | Restores its potency in health, never beyond the maximum. |
| `attack`, `defense`, `accuracy`, `evasion` | Raises (`sign: 1`) or lowers (`sign: -1`) that stat by its potency while it lasts. |

Whoever applies an effect sets its potency (1–1000) and duration (1–100
turns): `{"key": "frostbite", "potency": 2, "duration": 3}`. Reapplying keeps
the stronger potency and longer duration. Built-in effects are `poison`,
`burning`, `bleeding`, `regeneration`, `might`, `weakness`, `ward`,
`vulnerability`, `focus`, `blinded`, `evasive`, and `snared`.

<a id="ability-effect-abilities"></a>
### Abilities

```json
{"key": "frost-lance", "name": "Frost Lance", "command": "lance", "target": "enemy", "cost": 7,
 "damage": 8, "effects": [{"key": "frostbite", "potency": 2, "duration": 3}]}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `key` | yes | Cannot reuse a built-in ability key. |
| `name` | yes | 2–40 characters. |
| `command` | no | One word players type: `/cast lance`. Defaults to the key. The name works too. |
| `target` | yes | `enemy` (strikes one enemy) or `self` (acts on the caster). |
| `cost` | no | 0–100 of the world's resource (mana unless the world renames it). |
| `damage` | enemy abilities | 1–200, plus Intellect and spell power bonuses, rolled like other spells. |
| `heal` | self, optional | 1–200, plus Intellect and healing bonuses. |
| `effects` | no | Up to four effects. Enemy abilities apply harmful effects (damage or a penalty); self abilities apply helpful ones (regen or a bonus). |

A self ability needs `heal`, `effects`, or both. Built-in abilities are
`ember`, `venom`, `rend`, `hex` (enemy) and `mend`, `ward`, `might` (self).

<a id="ability-effect-granting-abilities"></a>
### Granting abilities

A race or class lists ability keys in `spells` (up to 12). Characters know the
abilities of both. In a world package or theme, use the package's own local
keys; StationNet renames them on import. Abilities are unavailable in worlds
that turn their resource off.

<a id="ability-effect-removal"></a>
### Removal

An ability a race or class still grants, or an effect an ability, item, NPC, or
trigger still applies, cannot be removed until those are updated. An effect
still active on someone, or applied by an item a player may still hold, is kept
for them but hidden from new use.

<a id="dungeon-theme-reference"></a>
## Dungeon theme reference

A dungeon theme is a portable, UTF-8 JSON package that tells StationNet what a
procedurally generated dungeon looks like: its rooms, creatures, items, and how
they are placed. StationNet does not invent content or execute theme scripts;
everything players see comes from the file you write.


<a id="dungeon-theme-quick-start"></a>
### Quick start

Embedded item and enemy `properties` support the shared MUD effects format.
Use `on_use_effects` for consumables and `on_hit_effects` for equipment or
enemies. For example, `"properties": {"on_hit_effects":
[{"key":"bleeding","potency":2,"duration":3}]}`. Available keys are
`poison`, `burning`, `bleeding`, `might`, `weakness`, `ward`,
`vulnerability`, `focus`, `blinded` (accuracy), `evasive`, and `snared`
(evasion). Potency must be 1–1000 and duration 1–100 combat turns.

1. Start StationNet once to create `data/dungeons/` (or your
   `<STATIONNET_DATA>/dungeons/` directory). Copy `_template.json` to a new
   file, for example `my-theme.json`.
2. Set a unique lowercase `id`, a `version`, a `name`, and an `author`.
3. Replace every bracketed placeholder (`[Theme name]`, `[Entrance room name]`,
   and so on) with your own text. Rename the `*-placeholder` local IDs.
4. Validate it:

   ```sh
   flask --app stationnet:create_app dungeon validate my-theme.json --check-definitions
   ```

5. Import it through SYSOP Settings > MUD service > Dungeon Themes, or copy it
   to `<STATIONNET_DATA>/dungeons/` (normally `data/dungeons/`). Discovery is
   live, so no restart is needed. `_template.json` is ignored as a starter.
   SYSOPs can disable or re-enable any built-in or custom theme from the same
   panel without removing its file or changing existing dungeon runs.
6. Preview it:

   ```sh
   flask --app stationnet:create_app dungeon preview my-theme --seed test-001 --size 12 --layout maze
   ```

7. Play it from Dungeon Portals, or launch with
   `/expedition my-theme [difficulty] [size]`.

Themes imported through SYSOP Settings may include an optional top-level
`origins` object with `races` and/or `classes` arrays. Each entry uses the same
format as the [race and class templates](#race-class-reference).
Bundled choices support every field in that guide, including `tags`,
`resistances`, `proficiencies`, `skill_affinities`, and `progression`. Response
conditions and item requirements that name a bundled race or class by its local
key are mapped to the installed key automatically.
Imported choices are not offered anywhere yet; add them to the procedural
world (or any other world) on its **RACES, CLASSES & SKILLS** page when ready.
The theme removal form lists its linked choices and offers optional cleanup.
Definitions still linked to another installed world or theme are preserved.
If cleanup leaves a world without any race or class, that world offers the
built-in choices again.

Themes may also include a top-level `skills` array of up to 100 skill
definitions in the [skills guide](#skill-reference)
format. Bundled races and classes may name these skills by their local keys,
and StationNet rewrites them to the installed keys. Bundled skills are not
offered anywhere until added to a world's selection. Optional cleanup on theme
removal removes unshared skills last, after the origins that used them, and a
world left with too few starting skills offers the default ones again.

The starter's `"$schema": "./theme-schema.json"` line lets most editors validate
and autocomplete the file as you type. Keep `theme-schema.json` next to your
theme while editing. The copied starter contains placeholders and is not a
playable theme until you replace them, rename its ID, and save it under a
different filename.

<a id="dungeon-theme-small-playable-theme-written-from-scratch"></a>
#### Small playable theme, written from scratch

Save this as `glass-vault.json`. It has room text for every role, a consumable,
an enemy, and a possible encounter. It needs no shared definitions. Change
`id` and `author` before using it alongside another copy.

```json
{
  "schema_version": 1,
  "id": "glass-vault",
  "version": "1",
  "name": "Glass Vault",
  "author": "Your name",
  "description": "A short vault expedition.",
  "definitions": {
    "items": [
      {"id": "tonic", "name": "Vault tonic", "description": "A restorative vial.",
       "item_type": "consumable", "properties": {"heal": 12, "value": 20}}
    ],
    "enemies": [
      {"id": "sentry", "name": "Glass sentry", "description": "A cracked guardian.",
       "combat": {"maximum_health": 18, "attack": 4, "defense": 1,
                  "experience_reward": 5},
       "loot": [{"type": "item", "item_local_id": "tonic", "amount": 1}]}
    ]
  },
  "generation": {
    "size": {"minimum": 5, "default": 8, "maximum": 12},
    "difficulty": {"minimum": 1, "default": 1, "maximum": 5},
    "layouts": [{"type": "branching", "weight": 1}]
  },
  "pools": {
    "rooms": [
      {"id": "vault-rooms", "names": [{"text": "Glass chamber"}],
       "descriptions": [{"text": "Broken mirrors line the walls."}]}
    ],
    "enemies": [
      {"id": "vault-sentries", "chance": 1,
       "count": {"minimum": 1, "maximum": 1},
       "choices": [{"local_id": "sentry"}]}
    ],
    "encounters": [
      {"id": "guarded-room", "conditions": {"roles": ["normal"]},
       "chance": 0.5, "enemy_pool": "vault-sentries"}
    ]
  }
}
```

Run `dungeon validate` on this file, install it, and use `dungeon preview
glass-vault --seed first-run --size 8` to inspect generated rooms. The room
pool has no role condition, so it covers entrance, exit, and normal rooms.
Only normal rooms can select the sentry encounter. Its `chance` is a
probability per room, not a guarantee for any one preview.

<a id="dungeon-theme-bundled-themes"></a>
### Bundled themes

| File | Setting |
| --- | --- |
| `generic-fantasy-dungeon.json` | The conventional default starter theme. |
| `desert.json` | Buried desert temples. |
| `jungle-ruins.json` | Overgrown stone terraces in a river valley. |
| `frozen-frontier.json` | A snowbound expedition route leading to ruins beneath a glacier. |

StationNet copies each bundled file into the configured theme directory only
when that file is missing, so they appear in Dungeon Portals automatically.
Files you have installed are never overwritten. See
[Determinism and versioning](#dungeon-theme-determinism-and-versioning) for how to adopt an
updated bundled theme.

<a id="dungeon-theme-anatomy-of-a-theme"></a>
### Anatomy of a theme

| Top-level key | Required | Purpose |
| --- | --- | --- |
| `schema_version` | yes | `1` or `2`. Schema 2 adds the sections marked "2" below and rejects top-level keys it does not know. |
| `id` | yes | Unique theme identifier. |
| `version` | yes | Version string; see [versioning](#dungeon-theme-determinism-and-versioning). |
| `name` | yes | Display name. |
| `author` | yes for a custom file in `data/dungeons/` | Nonempty author name. |
| `description` | no | Author-facing notes. |
| `definitions` | no | Items, NPCs, and enemies owned by this theme. |
| `generation` | yes | Size, difficulty, layout types, and special roles. |
| `pools` | yes | Weighted text and content used to populate rooms. |
| `origins`, `skills` | no | Races, classes, and skills the theme bundles (portal themes only). |
| `effects`, `abilities` | no (2) | Timed effects and castable abilities the theme bundles (portal themes only). See [Bundled abilities and effects](#dungeon-theme-bundled-abilities-and-effects-schema-2). |
| `rewards` | no (2) | Experience rate and risk-loot settings. See [Rewards, scaling, and bosses](#dungeon-theme-rewards-scaling-and-bosses-schema-2). |
| `scaling` | no (2) | How much enemies strengthen per difficulty step. |
| `bosses` | no (2) | Multipliers for enemies marked `"boss": true`. |
| `triggers` | no | Run-scoped [triggers](#dungeon-theme-triggers-optional). |

**ID format.** Every `id` (theme, version, local definitions, pools, dialogue
nodes and responses) must match `^[a-z0-9][a-z0-9._-]{0,63}$`: lowercase
letters, digits, `.`, `_`, and `-`, starting with a letter or digit, at most 64
characters.

**Weights.** Wherever `weight` appears it is an integer from 1 to 1,000,000,
defaulting to 1. Higher weights are chosen proportionally more often.

<a id="dungeon-theme-definitions-items-npcs-enemies"></a>
### Definitions: items, NPCs, enemies

Use the top-level `definitions` object for content owned by the theme.

| Array | What it defines |
| --- | --- |
| `items` | Miscellaneous items, equipment, containers, consumables, and keys. |
| `npcs` | Conversational NPCs, optionally combat-capable. |
| `enemies` | Same shape as an NPC, but `combat` is required. |

<a id="dungeon-theme-items"></a>
#### Items

Required fields: `id`, `name`, `description`.

- `item_type`: `misc`, `equipment`, `container`, `consumable`, or `key`.
- `equipment_slot`: `head`, `body`, `hands`, `legs`, `feet`, `main_hand`,
  `off_hand`, or `accessory`.
- `properties`: combat bonuses, consumable effects, and coin value. See
  [Item properties and economy](#dungeon-theme-item-properties-and-economy). Unknown keys are
  kept, so you can store theme-specific data here.
- `behaviors`: free-form theme-specific metadata, preserved as written.

<a id="dungeon-theme-npcs-and-enemies"></a>
#### NPCs and enemies

Required fields: `id`, `name`, `description` (enemies also require `combat`).

- `properties` and `behaviors`: free-form objects. For combat-capable NPCs,
  `behaviors` also controls aggression (see
  [Gameplay rules](#dungeon-theme-gameplay-rules-themes-should-know-about)).
- `conversation`: an embedded dialogue with a `nodes` array. Each node has an
  `id`, a `body`, an optional `start: true`, and `responses`. Each response has
  an `id` and `text`, and may set `next_node_id`, `sort_order`, or read and write
  conversation state with `required_state_key` / `required_state_value` and
  `state_key` / `state_value`.

  A response may also have `conditions`, an array that must all hold for the
  response to appear. Supported types are `level`, `race`, `class`, `tag`
  (a trait the character's race or class declares), `has_item`, `coins`,
  `attribute`, `skill`, `state`, and `claimed`. Any
  condition can take `"negate": true`, and `{"any": [...]}` groups
  alternatives. Quest conditions are not available in dungeons.

  A response may have `outcomes` (`give_item`, `take_item`, `experience`,
  `coins`, `take_coins`). These are applied the first time each character
  chooses the response, and never again, across all runs. Item IDs in either
  list may name an embedded item's `id` or an existing item definition. The
  [custom world guide](#conditions)
  lists every field. For example:

  ```json
  {"id": "bribe", "text": "Take these coins and let me pass.",
   "conditions": [{"type": "coins", "min": 25}, {"type": "claimed", "negate": true}],
   "outcomes": [{"type": "take_coins", "amount": 25}, {"type": "give_item", "item": "gate-key"}]}
  ```
- `combat`: all optional individually.

  | Field | Range | Notes |
  | --- | --- | --- |
  | `maximum_health` | 1 to 1,000,000 | |
  | `attack` | 1 to 100,000 | |
  | `defense` | 0 to 100,000 | Flat damage reduction against attacks that land. |
  | `accuracy` | 0 to 100 | Default 0. Added to the base 85% chance to hit. |
  | `evasion` | 0 to 100 | Default 0. Subtracted from attackers' chance to hit; spells face half. |
  | `experience_reward` | 0 to 100,000 | Base experience. |
  | `respawn_seconds` | 5 to 86,400 | Traditional world only; dungeon enemies stay defeated for the run. |

- `equipment`: a list of `{ "slot": ..., <item reference> }`.
- `loot`: a list of either
  `{ "type": "experience", "amount": 1-100000 }` or
  `{ "type": "item", <item reference>, "amount": 1-100 }`.

<a id="dungeon-theme-item-references"></a>
#### Item references

Equipment and item loot point at an item with **exactly one** of:

- `item_local_id`: the `id` of an item in this file's `definitions.items`.
- `item_definition_id`: an existing station-shared item definition (optional).

<a id="dungeon-theme-local-ids-and-ownership"></a>
#### Local IDs and ownership

Local IDs are short identifiers within the file. At installation StationNet maps
them to immutable runtime IDs containing the theme ID and version, records
their ownership, and materializes them through the existing item, equipment,
NPC, dialogue, combat, and reward systems. They cannot collide with another
theme or with traditional-world definitions. Generated room content keeps a
snapshot, and the complete template is retained in the version table.

<a id="dungeon-theme-item-properties-and-economy"></a>
### Item properties and economy

Give items that affect a run explicit properties:

| Item | Properties | Effect |
| --- | --- | --- |
| Weapon | `damage`, `attack`, or `attack_bonus` | Adds to attack damage while equipped. |
| Firearm | `firearm: true`, a nonempty `ammo_type`, `magazine_size` from 1 to 100, and an attack bonus | A hand-slot weapon fired with `/fire <enemy>`; it starts empty. |
| Ammunition | `item_type: "misc"`, matching `ammo_type`, `rounds` from 1 to 1,000 | A carried item used by `/reload [gun]`. |
| Armour or shield | `armor`, `defense`, or `defense_bonus` | Reduces incoming damage while equipped. |
| Healing consumable | `heal`: positive integer | Restores up to that much health and consumes the item. In combat it advances an enemy turn. |
| Navigation consumable | `reveal_exit: true` | Reveals the next direction toward the exit and consumes the item. |
| Any saleable item | `value`: integer, 1 to 100,000 | Sets its full coin value. The portal trader pays 40% of it, rounded down, minimum one coin. |
| Restricted equipment | `requirements`: attribute minimums (1 to 30), `level`, and `races`, `classes`, or `tags` lists, plus `not_races`, `not_classes`, `not_tags` | Only characters who meet every requirement can equip it, for example `{"tags": ["arcane"], "level": 3}`. See the [custom world guide](#world-package-reference) for details. |

Notes:

- Use `item_type: "consumable"` for one-use supplies. A `use_message` alone
  displays text and consumes the item, but **does not** restore health. Set
  `heal` explicitly.
- Items without `value` get a modest inferred value, so older themes remain
  saleable. Explicit values make treasure rewards predictable.
- The trader does not buy keys.
- A firearm needs `item_type: "equipment"` and `main_hand` or `off_hand`.
  `/attack` fires a loaded equipped gun; `/melee` forces melee. Reloading in
  combat spends a turn. NPC firearms follow the same magazine and reload cycle.
- `properties.on_use_effects` on consumables and `properties.on_hit_effects`
  on equipment or enemies apply timed effects. Each entry has `key`, `potency`
  (1 to 1,000), and `duration` (1 to 100 combat turns). Supported keys are
  `poison`, `burning`, `bleeding`, `might`, `weakness`, `ward`,
  `vulnerability`, `focus`, `blinded`, `evasive`, and `snared`, plus any
  effect the theme bundles. Items may also carry `cures`, and equipment
  `resistances`, as in worlds; see
  [Protective gear, cures, and hazards](#protective-gear-cures-and-hazards).
- Older bundled recovery items carrying the former "standard recovery rules"
  message still heal, for compatibility. New themes should use `heal`.

For example, add these two objects to `definitions.items` and reference them
from an item pool or enemy loot. Their `ammo_type` values must match:

```json
[
  {"id":"sidearm","name":"Sidearm","description":"A compact pistol.","item_type":"equipment","equipment_slot":"main_hand","properties":{"firearm":true,"ammo_type":"9mm","magazine_size":6,"attack_bonus":4}},
  {"id":"sidearm-ammo","name":"9mm cartridges","description":"A box of cartridges.","item_type":"misc","properties":{"ammo_type":"9mm","rounds":18}}
]
```

These are entries for `definitions.items`, not a complete theme. Place ammunition
where players can acquire it because guns begin unloaded.

<a id="dungeon-theme-loot-ownership"></a>
#### Loot ownership

The portal room provides the shop and private persistent storage. Dungeon loot
is marked as belonging to the active run:

| Outcome | Loot from the run |
| --- | --- |
| Complete at the exit | Banked. |
| Extract alive at the entrance | Banked, but the completion experience bonus is forfeited. |
| Death or `/abandon` | Destroyed. |

Equipment brought in from before the run and items already in storage survive
in every case.

<a id="dungeon-theme-generation-settings"></a>
### Generation settings

```json
"generation": {
  "size":       {"minimum": 5, "default": 10, "maximum": 30},
  "difficulty": {"minimum": 1, "default": 1,  "maximum": 100},
  "layouts": [
    {"type": "linear", "weight": 1},
    {"type": "branching", "weight": 3},
    {"type": "maze", "weight": 3},
    {"type": "hub", "weight": 1}
  ],
  "special_roles": [
    {"role": "special", "count": 1, "min_depth_ratio": 0.25,
     "max_depth_ratio": 0.85, "prefer_dead_ends": true}
  ]
}
```

- `size` and `difficulty` are ranges. Each needs `minimum`, `default`, and
  `maximum`, ordered in that sequence. Size values must be integers from 2 to
  500; difficulty values must be integers from 1 to 1,000.
- `layouts` needs at least one entry. Types are `linear`, `branching`, `maze`,
  and `hub`; weights control how often each is chosen when no layout is forced.
- `special_roles` is optional. Each entry names a `role` and a `count` (1 to
  100), and can constrain placement with `min_depth_ratio`, `max_depth_ratio`
  (0 to 1), and `prefer_dead_ends`. Use the role name in room pools and
  conditions.

Generation builds a connected layout, assigns `entrance`, `exit`, `normal`, and
your configured special roles, then evaluates pools. Room difficulty scales
enemy combat stats and experience rewards: each step above difficulty 2 adds
8 health, 2 attack, half a point of defense, 2 accuracy, 1 evasion, and 5
experience. Completion pays coins based on
difficulty and rooms visited, so harder runs pay more.

Each difficulty is tuned for a recommended character level of
`3 × difficulty − 2`: difficulty 1 suits level 1, difficulty 4 level 10, and
difficulty 8 level 22. Deeper rooms run up to two difficulty steps higher.
Kill and completion experience drops for characters more than three levels
above the recommendation (15% per level, never below 25%). It rises 5% per
level for characters below it, up to +25%. Players gain attack, spell, healing,
and mitigation bonuses as they level. Choose difficulty ranges that cover the
levels you expect players to bring.

The generator also places a small amount of station supply or treasure loot
outside your pools, with rarer rewards at greater risk and a reward at the exit.
Your theme can still add its own weapons, healing supplies, and treasure via
item pools and enemy loot.

<a id="dungeon-theme-pools-and-encounters"></a>
### Pools and encounters

`pools` controls placement. All pool entries share a base:

| Field | Meaning |
| --- | --- |
| `id` | Required. Unique within its pool list; referenced by encounters. |
| `weight` | Relative likelihood among eligible pools. |
| `conditions` | Filters for when the pool applies; see [Conditions reference](#dungeon-theme-conditions-reference). |

<a id="dungeon-theme-room-and-environment-pools"></a>
#### Room and environment pools

- `pools.rooms` (at least one required): each needs `names` and `descriptions`.
- `pools.environments` (optional): each needs `descriptions`, adding variation
  on top of the room description.

`names` and `descriptions` are lists of `{ "text": "...", "weight": n }`
entries with at least one item. Cover every role you use (`entrance`, `exit`,
`normal`, and any special role) with a matching role-specific pool or an
unrestricted fallback pool, as in the small example above.

<a id="dungeon-theme-content-pools-npcs-enemies-items"></a>
#### Content pools: NPCs, enemies, items

Each entry in `pools.npcs`, `pools.enemies`, and `pools.items` needs:

- `count`: `{ "minimum": 0-100, "maximum": 0-100 }`, inclusive.
- `choices`: at least one entry. Each choice must contain **exactly one** of
  `local_id` (an ID in the matching `definitions` array) or `definition_id` (an
  existing shared StationNet definition). Choices may also carry their own
  `weight` and `conditions`.
- `chance` (optional): 0 to 1, the probability the pool produces anything.

A theme may mix local and shared choices, or use only embedded content.

<a id="dungeon-theme-encounters"></a>
#### Encounters

At most one eligible encounter is selected per room. An entry in
`pools.encounters` is the complete encounter definition. It can
connect any combination of `npc_pool`, `enemy_pool`, and `item_pool` (each the
`id` of a pool in the matching list), plus `chance` and an open-ended `config`
object. The selected encounter, its `config`, and the selected content are
snapshotted into the generated room. `config` is stored with the run for
reference only; the engine does not interpret it. Express behavior through
pools, roles, `bosses`, and triggers instead.

<a id="dungeon-theme-environmental-features"></a>
#### Environmental features

Environment pools add descriptive text to rooms. A trigger whose source is
`{"environment": "<environment pool id>"}` fires in rooms that drew that
environment, so combined with `apply_effect` an environment becomes a hazard
(a lava floor applying `burning`) or a boon (a spring applying `regeneration`).
`cure_effect` makes a cleansing environment, such as a clean spring that
removes `poison`.

The starter demonstrates every supported definition and connection with neutral
placeholders: an NPC-only encounter, and a combat encounter that draws from an
enemy pool and an item pool.

<a id="dungeon-theme-conditions-reference"></a>
### Conditions reference

Conditions are data, not executable expressions. All keys are optional, and
unknown keys are rejected.

| Key | Type | Filters on |
| --- | --- | --- |
| `layouts` | array of `linear` / `branching` / `maze` / `hub` | The run's layout type. |
| `roles` | array of strings | Room role (`entrance`, `exit`, `normal`, or a special role). |
| `min_difficulty`, `max_difficulty` | integer, 0 or more | Room difficulty. |
| `min_depth_ratio`, `max_depth_ratio` | number, 0 to 1 | Normalized distance from the entrance toward the exit. |
| `min_degree`, `max_degree` | integer, 0 or more | The room's number of connections. |
| `dead_end` | boolean | Whether the room is a dead end. |

Conditions can appear on pools and on individual content choices.

<a id="dungeon-theme-triggers-optional"></a>
### Triggers (optional)

A theme may add a top-level `triggers` array. Triggers make a run react: when
something happens, if the conditions hold, the actions run in order. They use
the format of world triggers (section 5c of the [world package reference](#world-package-reference)), with
names from the theme itself. Each run gets its own state, so flags set in one
run never reach another run, the world, or the next run of the same theme.

| `event` | `source` | Happens when |
| --- | --- | --- |
| `enter_room` | `room_role` (`entrance`, `exit`, `normal`, or a special role), `room_template` (a room pool `id`), or `environment` (an environment pool `id`) | A player enters a matching room, including the entrance on arrival. |
| `say_phrase` | `room_role`, `room_template`, or `environment`, plus `phrase` | A player says the phrase in a matching room. |
| `use_object`, `take_item` | `item`: an embedded item `id` or a shared definition ID | A player uses or picks up that kind of item. |
| `defeat_npc` | `npc`: an embedded NPC or enemy `id`, or a shared definition ID | Fires for each player rewarded for the kill. |
| `manual` | none | Run by another trigger's `run_trigger`. |

Allowed actions are `message` (to `actor` or `room`), `set_flag` and
`clear_flag` (`scope` `player`, or `world`, which means this run), `give_item`
and `take_item` (embedded or shared items), `experience`, `coins`,
`take_coins`, `heal`, `damage` (never below 1 health), `apply_effect`
(`effect`, `potency`, `duration`; a built-in effect or one the theme bundles),
`cure_effect` (`effect` or `category`, optional `potency` and `turns`), and
`run_trigger`. Dungeon themes have no `linger` event; use `enter_room` on an
environment for a hazard.
`delay_seconds`, `once`, `scope`, `cooldown_seconds`, `max_fires`,
`conditions`, and `fail_message` work as in worlds. Conditions may use
everything except quest, objective, `object_state`, and `in_room`.

```json
"triggers": [
  {"id": "boss-down", "event": "defeat_npc", "source": {"npc": "bone-king"},
   "scope": "world", "once": true,
   "actions": [{"type": "set_flag", "scope": "world", "key": "king.slain", "value": "yes"},
               {"type": "message", "to": "room", "text": "The crypt shudders as the Bone King falls."}]},
  {"id": "exit-blessing", "event": "enter_room", "source": {"room_role": "exit"}, "once": true,
   "conditions": [{"type": "flag", "scope": "world", "key": "king.slain", "value": "yes"}],
   "actions": [{"type": "experience", "amount": 40}, {"type": "message", "text": "A calm settles over you."}]}
]
```

Validation reports unknown roles, room pools, and definitions with their
JSON path. Triggers are part of the content hash, so bump `version` after
changing them.

<a id="dungeon-theme-bundled-abilities-and-effects-schema-2"></a>
### Bundled abilities and effects (schema 2)

A portal theme may bundle `effects` and `abilities` in the same format as a
world's `effects.json` and `abilities.json` (see
the [ability and effect reference](#ability-effect-reference)). Its classes and races
grant the abilities by local key in `spells`, and its items, enemies, and
triggers apply its effects by local key. On import they are installed under
keys derived from the theme ID; the theme's races, classes, and skills are
offered in the dungeon world. Keys may not reuse a built-in ability or effect.
World dungeon templates cannot bundle these; they use the world's own.

<a id="dungeon-theme-rewards-scaling-and-bosses-schema-2"></a>
### Rewards, scaling, and bosses (schema 2)

```json
"rewards": {"experience_percent": 150,
            "risk_loot": {"enabled": true, "chance_percent": 50,
                          "tiers": {"1": [{"item_local_id": "pearl"}], "3": [{"item_definition_id": "stationnet:treasure:idol"}]}}},
"scaling": {"offset": 2, "per_step": {"maximum_health": 6, "attack": 1, "defense": 0.5}},
"bosses": {"health_percent": 200, "attack_percent": 125, "experience_percent": 200}
```

- `rewards.experience_percent` (10–500) scales enemy experience.
- `rewards.risk_loot` controls the small supply and treasure caches StationNet
  adds to rooms: `enabled: false` turns them off, `chance_percent` (0–200)
  makes them rarer or more common, and `tiers` replaces the station's items for
  tier 1, 2, or 3 with up to 20 of the theme's own or shared items each.
- `scaling` sets how enemies grow with room difficulty: each step past
  `offset` (0–10, default 2) adds `per_step` amounts of `maximum_health`
  (default 8), `attack` (2), `defense` (0.5), `accuracy` (2), `evasion` (1),
  and `experience_reward` (5). Values allow one decimal place.
- `bosses` multiplies the health, attack, and experience of embedded enemies
  that set `"boss": true`, after scaling. A boss's embedded `loot` always drops,
  so put its guaranteed reward there. Place bosses with a `special_roles` entry
  and pools limited to that role, as the bundled themes do.

Without these sections a theme generates exactly as it did before they existed.

<a id="dungeon-theme-gameplay-rules-themes-should-know-about"></a>
### Gameplay rules themes should know about

- Known abilities come from the character's race and class, including imported
  ones and those a theme bundles. `/spells` lists them, `/cast <ability>
  [enemy]` spends mana, and `/rest` restores mana when no hostile enemy is
  nearby. The built-in abilities are `ember`, `venom`, `rend`, `hex`, `mend`,
  `ward`, and `might`; see the [race and class guide](#race-class-reference)
  for costs and targets. Intellect improves mana and spell strength. Theme
  items and enemies apply timed effects by key via `properties`.
- Enemies with `combat` attack when a player enters their room.
- Combat advances only when a player acts. Waiting or disconnecting never gives
  enemies extra turns.
- Players can attack, defend, use an item, or attempt a risky flee.
- Attacks roll to hit (85% + accuracy − evasion, kept within 30–95%), and
  `defense` reduces the damage of hits that land. Every enemy fighting a
  player acts after each of that player's actions; on room entry only the
  first strikes and the rest join in.
- Combat-capable NPCs attack on entry by default. To keep one from attacking
  first, set `behaviors.aggressive` to `false` or `behaviors.friendly` or
  `neutral` to `true`; it still fights back against whoever attacks it.
  `passive: true` NPCs never fight back.
- `/rest` in a cleared room restores 15% of health and 25% of mana, every 30
  seconds. Dying ends the run and forfeits its loot, but costs no
  experience; the player returns with half health and mana.
- Defeated dungeon enemies stay defeated for that run. `respawn_seconds` only
  applies in the traditional world.
- Cooperative runs are joined with `/join <code>`.

<a id="dungeon-theme-install-validate-and-preview"></a>
### Install, validate, and preview

| Task | Command |
| --- | --- |
| Validate a file | `flask --app stationnet:create_app dungeon validate path/to/theme.json --check-definitions` |
| List installed themes | `flask --app stationnet:create_app dungeon themes` |
| Preview a generation | `flask --app stationnet:create_app dungeon preview your-theme --seed test-001 --size 12 --layout maze` |
| Persist a pending design | `flask --app stationnet:create_app dungeon generate your-theme --seed test-001 --type random` |
| List shared definition IDs | `flask --app stationnet:create_app dungeon definitions` |
| List recent generation failures | `flask --app stationnet:create_app dungeon failures` |

- Add `--json-output` to `dungeon preview` to get the canonical manifest.
- Embedded-only themes pass `--check-definitions` on a station with no custom
  world definitions. The flag additionally verifies any shared `definition_id`
  or `item_definition_id` references.
- Install by importing the JSON in SYSOP Settings or copying it into
  `<STATIONNET_DATA>/dungeons/`.
  Playable themes then appear in Dungeon Portals.
- Disabled themes remain installed but are omitted from new expeditions,
  previews, generated designs, and future Dungeon of the Day selection until
  a SYSOP enables them again.
- Launch a run with `/expedition <theme-id> [difficulty] [size]`.

<a id="dungeon-theme-export-share-and-upgrade"></a>
#### Export, share, and upgrade

Every installed theme has **EXPORT** and **EXPORT SELF-CONTAINED** links in
SYSOP Settings. A theme that names station definitions (`definition_id` other
than built-in `stationnet:` ones) can only be exported self-contained: those
items and NPCs, with their combat stats, gear, loot, and any custom effects they
apply, are copied into the file as embedded definitions so it imports on any
station. NPC dialogue is not copied.

To replace an installed custom theme, raise its `version`, choose the file, and
tick **Upgrade**. Runs already started keep the version they began with. A file
with the same version but different content is refused.

`theme-schema.json` is the machine-readable contract. Runtime validation goes
further, adding range ordering, local/shared reference resolution, definition
compatibility, duplicate-ID, conversation-graph, pool cross-reference, and
database checks, each reported with a JSON-path error.

<a id="dungeon-theme-using-a-theme-inside-an-authored-world"></a>
#### Using a theme inside an authored world

The same format powers **world dungeons**. Builders can create templates
in World Forge (**Dungeon templates → New from a theme**), starting from any
installed theme, and place entrances in their rooms. Those templates belong to the world and
travel in its ZIP as `dungeons.json`. They take no `id` or `version`, because
the station assigns them per revision. They also cannot bundle `origins`,
`skills`, `abilities`, or `effects`; they use the world's own, by local key. Enabling or disabling themes here does not affect them. See section
5b of the [world package reference](#world-package-reference).

<a id="dungeon-theme-determinism-and-versioning"></a>
### Determinism and versioning

Output is determined by the seed, the theme ID/version/content hash, the
generator version, size, difficulty, explicit layout, and the **order** of
arrays. Reordering choices can change what is generated.

- StationNet rejects changed content under a theme ID/version that has already
  been used. **Never edit a version after it has generated an instance; bump
  `version` instead.**
- Installed versions are immutable. The bundled themes now include explicit
  healing and treasure values, so their versions were incremented.
- Existing stations keep their installed files and active runs. To adopt an
  updated bundled theme, replace the corresponding installed JSON with the new
  version when you are ready. Older versions keep working under the
  compatibility rules above.

<a id="dungeon-theme-authoring-checklist"></a>
### Authoring checklist

- [ ] Unique lowercase `id`; `version` incremented if the content changed.
- [ ] All bracketed placeholders replaced, and no leftover `*-placeholder` IDs.
- [ ] Room pools cover `entrance`, `exit`, `normal`, and every special role,
      either with role conditions or an unrestricted fallback pool.
- [ ] Every `local_id` / `item_local_id` points at a real embedded definition.
- [ ] Every `npc_pool` / `enemy_pool` / `item_pool` points at a real pool `id`.
- [ ] Each choice uses `local_id` **or** `definition_id`, never both.
- [ ] Healing items set `heal`; treasure and gear set `value`.
- [ ] Every `size` and `difficulty` range is ordered: minimum, default, maximum.
- [ ] Dialogue has one `start: true` node and every `next_node_id` resolves.
- [ ] `dungeon validate` passes, and previews look right across several seeds
      and layouts.

<a id="dungeon-theme-troubleshooting"></a>
### Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Validation fails on a choice | The choice has both or neither of `local_id` and `definition_id`. |
| An item is "used" but nothing heals | It has `use_message` but no `heal`. |
| Unexpected rooms are missing a name or description | No room pool matches that room's role or conditions. |
| An enemy appears in the wrong rooms | Check `conditions` on the pool, the choice, and the encounter. |
| A friendly NPC attacks the player | It has `combat`; set `behaviors.aggressive: false` or `friendly`/`neutral`/`passive: true` (only `passive` NPCs never fight back). |
| Output changed after a small edit | Array order is part of the seed's input; reordering changes generation. |
| StationNet rejects an edited installed theme | Its ID/version was already used; increment `version`. |
| A loot item vanished | The run ended in death or `/abandon`; run loot is only banked on exit or entrance extraction. |

<a id="room-preset-reference"></a>
## Room preset reference

These JSON files are the starters offered in World Forge under **Content library
→ Terrain presets → Add a station preset**. Each file is one room preset:

| File | Terrain | Links |
|---|---|---|
| `grassland.json` | Grassy plains | 4-way |
| `forest.json` | Open woodland | 4-way |
| `dense-forest.json` | Dark, tangled forest | 4-way |
| `mountains.json` | Rocky slopes and passes | 4-way |
| `desert.json` | Dunes and flats | 8-way |
| `swamp.json` | Marsh and bog | 4-way |

A world copies a starter when you create a preset from it, so editing that copy
never changes these files or other worlds. The starters name no creatures or
resources, because NPC and item definitions belong to each world. Add them in
World Forge after adding the preset.

To share your own presets, use **Export** on the Wilderness page. It writes
`{"presets": [...]}`, and **Import** reads either that format or a single
preset object. Creatures and items are stored by definition ID. When you import
into another world, tick **Drop creatures and items this world does not have**,
or fix the IDs in the JSON first.

The full preset format, regions, the `wilderness.json` package file and the
grid linking rules are documented in section 5d of the
[world guide](#5d-wilderness-and-room-presets-wildernessjson).

<a id="room-preset-long-distance-travel"></a>
### Long-distance travel

In rooms filled with a preset, players can use `/travel <direction>` to walk
onward one room at a time, with `/stop` to halt. Travel stops when the preset
changes, so a forest-to-grassland border always gives the traveller a fresh
look around. It also stops at authored rooms, stored exits, dungeon entrances,
creatures, NPCs and other players. Use preset borders and landmarks to decide
where long walks pause.

<a id="complete-example-world"></a>
## Complete example world

Save each block under the filename shown, in one directory. Zip the JSON
files themselves at the archive root and import the ZIP through
**SYSOP Settings → MUD service → Worlds**. This example is the
repository's complete `lantern-isles` starter; change its world ID
and author before distributing a distinct world.

### `manifest.json`

```json
{
  "format_version": 2,
  "id": "lantern-isles",
  "name": "Lantern Isles",
  "author": "StationNet",
  "start_room": "harbor",
  "resource": {"enabled": true, "name": "Glow"},
  "progression": {"level_cap": 20, "experience_percent": 150, "starting_coins": 50},
  "availability": {"races": ["islander"], "classes": ["lamplighter"]}
}
```

### `rooms.json`

```json
[
  {"id": "harbor", "name": "Lantern Harbor", "description": "Lanterns sway above tide-worn steps. A market lies north; a shrine glows to the east."},
  {"id": "market", "name": "Night Market", "description": "Stalls of oil, wick, and glass crowd the square."},
  {"id": "shrine", "name": "Tide Shrine", "description": "Warm light pools around a stone basin of seawater."},
  {"id": "cliffs", "name": "Wind Cliffs", "description": "Gulls wheel over a cave mouth that breathes cold air."}
]
```

### `exits.json`

```json
[
  {"id": "harbor-north", "from": "harbor", "to": "market", "name": "market", "direction": "north"},
  {"from": "market", "to": "harbor", "name": "harbor", "direction": "south"},
  {"from": "harbor", "to": "shrine", "name": "shrine", "direction": "east"},
  {"from": "shrine", "to": "harbor", "name": "harbor", "direction": "west"},
  {"from": "harbor", "to": "cliffs", "name": "cliffs", "direction": "west"},
  {"from": "cliffs", "to": "harbor", "name": "harbor", "direction": "east"}
]
```

### `content.json`

```json
{
  "mud_world_factions": [
    {"id": "keepers", "name": "Lantern Keepers", "description": "The guild that tends the harbor lights.",
     "default_reputation": 0, "public": 1}
  ],
  "mud_item_definitions": [
    {"id": "flare-oil", "name": "Flare Oil", "description": "A vial of oil that burns bright.",
     "item_type": "consumable", "properties_json": {"heal": 10, "value": 6}},
    {"id": "searing-hook", "name": "Searing Hook", "description": "A lantern hook that leaves embers in a wound.",
     "item_type": "equipment", "equipment_slot": "main_hand",
     "properties_json": {"attack_bonus": 4, "value": 30, "category": "blade",
                         "on_hit_effects": [{"key": "searing-light", "potency": 1, "duration": 2}]}},
    {"id": "glass-shard", "name": "Sea Glass", "description": "Wave-smoothed glass the keepers collect.",
     "item_type": "misc", "properties_json": {"value": 2}}
  ],
  "mud_conversations": [{"id": "keeper-talk", "name": "Keeper"}],
  "mud_dialogue_nodes": [
    {"id": "keeper-start", "conversation_id": "keeper-talk", "node_key": "start", "is_start": 1,
     "body": "The cliffs' gull is stealing our sea glass. Bring me a shard and drive it off."}
  ],
  "mud_quests": [
    {"id": "glass-hunt", "name": "Sea Glass", "description": "Recover sea glass for the keeper.",
     "completion_mode": "turn_in", "turn_in_npc_definition_id": "keeper"}
  ],
  "mud_npc_definitions": [
    {"id": "keeper", "name": "Old Keeper", "description": "A keeper with soot on her sleeves.",
     "conversation_id": "keeper-talk", "faction_id": "keepers"},
    {"id": "trader", "name": "Glass Trader", "description": "A trader with a tray of lamp glass."},
    {"id": "gull", "name": "Storm Gull", "description": "A huge gull with salt-crusted wings.",
     "behaviors_json": {"aggressive": true},
     "properties_json": {"on_hit_effects": [{"key": "brine-chill", "potency": 2, "duration": 2}]}}
  ],
  "mud_npc_combat_profiles": [
    {"npc_definition_id": "gull", "maximum_health": 18, "attack": 2, "defense": 0,
     "experience_reward": 20, "respawn_seconds": 60}
  ],
  "mud_npc_combat_rewards": [
    {"id": "gull-glass", "npc_definition_id": "gull", "reward_type": "item", "target_id": "glass-shard",
     "amount": 1, "chance_percent": 100}
  ],
  "mud_npc_shops": [{"npc_definition_id": "trader", "buys_items": 1, "sell_percent": 40}],
  "mud_shop_stock": [
    {"id": "oil-stock", "npc_definition_id": "trader", "item_definition_id": "flare-oil", "price": 5},
    {"id": "hook-stock", "npc_definition_id": "trader", "item_definition_id": "searing-hook", "price": 25}
  ],
  "mud_npc_trainers": [
    {"id": "wick-lessons", "npc_definition_id": "keeper", "skill_key": "wick-craft", "price": 10, "max_rank": 5}
  ],
  "mud_quest_objectives": [
    {"id": "find-glass", "quest_id": "glass-hunt", "objective_type": "possess_item", "target_id": "glass-shard",
     "description": "Carry a piece of sea glass", "consume_on_turn_in": 1},
    {"id": "drive-off-gull", "quest_id": "glass-hunt", "objective_type": "defeat_npc", "target_id": "gull",
     "description": "Drive off the storm gull"}
  ],
  "mud_quest_rewards": [
    {"id": "glass-coins", "quest_id": "glass-hunt", "reward_type": "coins", "amount": 25},
    {"id": "glass-standing", "quest_id": "glass-hunt", "reward_type": "reputation", "target_id": "keepers",
     "amount": 50}
  ],
  "mud_quest_givers": [{"quest_id": "glass-hunt", "npc_definition_id": "keeper"}],
  "mud_dialogue_responses": [
    {"id": "accept-glass", "node_id": "keeper-start", "response_text": "I'll find your glass.",
     "accept_quest_id": "glass-hunt"},
    {"id": "turn-in-glass", "node_id": "keeper-start", "response_text": "Here is the glass.",
     "turn_in_quest_id": "glass-hunt", "sort_order": 1}
  ],
  "mud_npc_instances": [
    {"id": "keeper-at-harbor", "definition_id": "keeper", "room_id": "harbor"},
    {"id": "trader-at-market", "definition_id": "trader", "room_id": "market"},
    {"id": "gull-at-cliffs", "definition_id": "gull", "room_id": "cliffs"}
  ]
}
```

### `origins.json`

```json
{
  "races": [
    {"key": "islander", "name": "Islander", "description": "Folk raised on salt and lamplight.",
     "traits": "Sea-hardy", "attributes": {"vitality": 1},
     "resistances": {"vulnerability": 25}, "tags": ["islander"],
     "skills": {"wick-craft": 1}}
  ],
  "classes": [
    {"key": "lamplighter", "name": "Lamplighter", "description": "Keepers of the harbor lanterns.",
     "traits": "Fire and tide", "skill": "wick-craft",
     "spells": ["lantern-flare", "tide-prayer", "ward"],
     "equipment": [{"key": "pole", "name": "Lantern Pole", "description": "A hooked brass pole.",
                    "slot": "main_hand", "attack": 3}]}
  ]
}
```

### `skills.json`

```json
{
  "skills": [
    {"key": "wick-craft", "name": "Wick Craft", "group": "magic", "starting": true,
     "description": "Trimming lantern wicks sharpens spellwork.",
     "effects": [{"type": "spell_power", "per_rank": 1, "every": 2, "max": 6}],
     "practice": {"triggers": ["spell_cast"], "rate": 1},
     "learn": {"cost": 1, "trainer_required": false}}
  ]
}
```

### `abilities.json`

```json
{
  "abilities": [
    {"key": "lantern-flare", "name": "Lantern Flare", "command": "flare", "target": "enemy", "cost": 6,
     "damage": 7, "description": "A burst of lantern fire that keeps burning.",
     "effects": [{"key": "searing-light", "potency": 2, "duration": 3}]},
    {"key": "tide-prayer", "name": "Tide Prayer", "command": "prayer", "target": "self", "cost": 5, "heal": 6,
     "description": "Ask the tide to mend you.",
     "effects": [{"key": "tide-blessing", "potency": 2, "duration": 3}]}
  ]
}
```

### `effects.json`

```json
{
  "effects": [
    {"key": "searing-light", "name": "Searing Light", "description": "Lantern fire that burns each turn.",
     "kind": "damage", "resist_as": "burning"},
    {"key": "tide-blessing", "name": "Tide Blessing", "description": "The sea mends wounds a little each turn.",
     "kind": "regen"},
    {"key": "brine-chill", "name": "Brine Chill", "description": "Cold spray slows your guard.",
     "kind": "defense", "sign": -1, "resist_as": "vulnerability"}
  ]
}
```

### `dungeons.json`

```json
{
  "templates": [
    {"key": "sea-caves", "name": "Sea Caves",
     "template": {
       "schema_version": 2,
       "name": "Sea Caves",
       "description": "Tide-carved caves under the cliffs.",
       "definitions": {
         "items": [
           {"id": "pearl", "name": "Cave Pearl", "description": "A pearl the size of a lantern bead.",
            "item_type": "misc", "properties": {"value": 15}}
         ],
         "enemies": [
           {"id": "crab", "name": "Lantern Crab", "description": "A crab with a glowing shell.",
            "properties": {"on_hit_effects": [{"key": "searing-light", "potency": 1, "duration": 2}]},
            "combat": {"maximum_health": 20, "attack": 3, "defense": 1, "experience_reward": 12,
                       "respawn_seconds": 60}},
           {"id": "tide-king", "name": "Tide King", "description": "An old crab crowned in barnacles.",
            "boss": true,
            "combat": {"maximum_health": 40, "attack": 5, "defense": 2, "experience_reward": 40,
                       "respawn_seconds": 60},
            "loot": [{"type": "item", "item_local_id": "pearl", "amount": 1}]}
         ]
       },
       "generation": {
         "size": {"minimum": 4, "default": 5, "maximum": 8},
         "difficulty": {"minimum": 1, "default": 1, "maximum": 4},
         "layouts": [{"type": "linear", "weight": 1}],
         "special_roles": [{"role": "boss", "count": 1, "min_depth_ratio": 0.8}]
       },
       "rewards": {"experience_percent": 120, "risk_loot": {"enabled": true, "chance_percent": 50,
                   "tiers": {"1": [{"item_local_id": "pearl"}]}}},
       "scaling": {"offset": 2, "per_step": {"maximum_health": 6}},
       "bosses": {"health_percent": 150},
       "pools": {
         "rooms": [
           {"id": "cave", "names": [{"text": "Dripping Cave"}],
            "descriptions": [{"text": "Water drips from a ceiling of pale stone."}]},
           {"id": "throne", "conditions": {"roles": ["boss"]}, "names": [{"text": "Barnacle Throne"}],
            "descriptions": [{"text": "Shells heap into a throne."}]}
         ],
         "environments": [
           {"id": "tidepool", "descriptions": [{"text": "A tidepool glows faintly here."}]}
         ],
         "enemies": [
           {"id": "crabs", "count": {"minimum": 1, "maximum": 1}, "choices": [{"local_id": "crab"}]},
           {"id": "king", "count": {"minimum": 1, "maximum": 1}, "choices": [{"local_id": "tide-king"}]}
         ],
         "encounters": [
           {"id": "crab-fight", "enemy_pool": "crabs", "conditions": {"roles": ["normal", "exit"]}},
           {"id": "king-fight", "enemy_pool": "king", "conditions": {"roles": ["boss"]}}
         ]
       },
       "triggers": [
         {"id": "tidepool-rest", "name": "Tidepool", "event": "enter_room", "source": {"environment": "tidepool"},
          "actions": [{"type": "apply_effect", "effect": "tide-blessing", "potency": 1, "duration": 2}]}
       ]
     }}
  ],
  "entrances": [
    {"room": "cliffs", "template": "sea-caves", "name": "Cave Mouth",
     "description": "A cold cave mouth under the cliffs.", "max_party": 4}
  ]
}
```

### `events.json`

```json
{
  "triggers": [
    {"id": "shrine-blessing", "name": "Shrine blessing", "event": "enter_room", "source": {"room": "shrine"},
     "cooldown_seconds": 60,
     "actions": [
       {"type": "message", "text": "Seawater in the basin glows, and warmth settles into your bones."},
       {"type": "apply_effect", "effect": "tide-blessing", "potency": 3, "duration": 3},
       {"type": "set_flag", "scope": "player", "key": "blessed", "value": "yes"}
     ]}
  ]
}
```
