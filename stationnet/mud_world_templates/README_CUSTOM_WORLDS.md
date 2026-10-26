# File-authored MUD worlds

StationNet stores imported world ZIPs in `data/mud_worlds/` (or
`<STATIONNET_DATA>/mud_worlds/`). You can build a traditional world with a text
editor. Put three UTF-8 JSON files at the root of a ZIP archive:
`manifest.json`, `rooms.json`, and `exits.json`. Add `content.json` for items,
NPCs, dialogue, quests, and placements. Add `origins.json` for custom races and
classes. No code or scripts run from a world
package.

Archives are limited to 16 MB, each JSON file to 8 MB, 1000 rooms, and 5000
exits. `content.json` may have up to 5,000 records in each supported array.
The ZIP must contain only those three required files and optional
`content.json` and `origins.json`, all at its root. Imports validate references
and reject duplicate world IDs. Standalone race and class imports remain
available through the [origin guide](../mud_origin_templates/README_RACES_CLASSES.md).

## 1. Name the world and choose its entry room

`manifest.json`:

```json
{
  "id": "lantern-isles",
  "name": "Lantern Isles",
  "author": "Your name",
  "start_room": "harbor"
}
```

The world ID must be unique on the station. World, room, and content record IDs
match `^[a-z][a-z0-9-]{0,63}$`: 1 to 64 lowercase letters, digits, and hyphens,
starting with a letter. `stationnet` is reserved for the original builder
world. `name` is 2 to 80 characters; `author` is 1 to 80. `start_room` must
match a room ID in `rooms.json`.

## 2. Write rooms

`rooms.json`:

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

## 4. Add optional world content

### Magic, mana, and timed effects

Item and NPC definition `properties_json` may include shared effects. A consumable
can use `"on_use_effects"`; equipped weapons and hostile NPCs can use
`"on_hit_effects"`. For example:

```json
{"on_hit_effects": [{"key": "poison", "potency": 2, "duration": 3}]}
```

Keys are `poison`, `burning`, `bleeding` (damage each combat turn), `might` and
`weakness` (attack), or `ward` and `vulnerability` (defense). Potency is 1–1000
and duration is 1–100 turns. Reapplying a key keeps its stronger potency and
longer remaining duration. Classes declare known spells through imported
class JSON or `character_options.json`; the world ZIP cannot add spell
implementations. Supported spells are `ember` (8 mana), `venom` (7), `rend`
(6), `hex` (7), `mend` (7), `ward` (6), and `might` (6). The first four target
an enemy; the last three target the caster. Players use `/spells`,
`/cast <spell> [enemy]`, and `/rest` to recover mana outside combat when no
hostile enemy is nearby. Intellect improves mana and spell strength. See the
[race and class guide](../mud_origin_templates/README_RACES_CLASSES.md) for class setup.

`content.json` is an object whose keys are authored MUD table names and whose
values are arrays of records. IDs are **local to the ZIP** and can be short
names such as `lantern` or `keeper` (with the same lowercase ID rule as rooms);
references use those local IDs. Rooms use
the IDs from `rooms.json`. StationNet gives every imported record a unique
internal ID, so two worlds may both have an item called `lantern`.

Equipment can declare minimum character attributes in `properties_json`, for
example `{"attack_bonus": 4, "requirements": {"strength": 14}}`. Players can
inspect these requirements and must meet all of them before equipping the item.
Supported attributes are `strength`, `agility`, `intellect`, and `vitality`, with
minimum values from 1 to 30. Existing equipment without requirements remains
usable by all characters. Gear becomes inactive if a character no longer meets
its requirements on the current progression track. Strength improves equipped melee weapon bonuses,
Agility improves firearm bonuses, and Vitality improves equipped armor bonuses.

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
starting point even when there is no existing builder world to export.

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
| `mud_dialogue_responses` | Choices and quest acceptance | `node_id`, `next_node_id`, `accept_quest_id` |
| `mud_npc_definitions` | NPC templates and behavior | `conversation_id` |
| `mud_npc_instances` | NPCs placed in rooms | `definition_id`, `room_id` |
| `mud_npc_combat_profiles` | Health, attack, defense, XP, respawn | `npc_definition_id` |
| `mud_npc_equipment` | NPC equipment slots | `npc_definition_id`, `item_definition_id` |
| `mud_npc_combat_rewards` | Item or XP drops | `npc_definition_id`, `target_id` for items |
| `mud_quests` | Quests and prerequisites | `prerequisite_quest_id` |
| `mud_quest_objectives` | Talk, visit, or possess objectives | `quest_id`, `target_id` |
| `mud_quest_rewards` | Quest item or XP rewards | `quest_id`, `target_id` for items |
| `mud_quest_givers` | NPCs that offer quests | `quest_id`, `npc_definition_id` |

Only these fields are accepted for each array; omitted optional fields use
database defaults. IDs and references are local to this ZIP. All references
must point to records in the same package, including room IDs in `rooms.json`.

| Array | Accepted fields beyond `id` |
| --- | --- |
| `mud_item_definitions` | `name`, `description`, `item_type`, `properties_json`, `behaviors_json`, `equipment_slot` |
| `mud_item_instances` | `definition_id`, `room_id`, `container_id`, `is_open`, `state_json` |
| `mud_conversations` | `name` |
| `mud_dialogue_nodes` | `conversation_id`, `node_key`, `body`, `is_start` |
| `mud_dialogue_responses` | `node_id`, `response_text`, `next_node_id`, `accept_quest_id`, `required_state_key`, `required_state_value`, `state_key`, `state_value`, `sort_order` |
| `mud_npc_definitions` | `name`, `description`, `conversation_id`, `properties_json`, `behaviors_json` |
| `mud_npc_instances` | `definition_id`, `room_id`, `state_json` |
| `mud_npc_combat_profiles` | `npc_definition_id`, `maximum_health`, `attack`, `defense`, `experience_reward`, `respawn_seconds` |
| `mud_npc_equipment` | `npc_definition_id`, `equipment_slot`, `item_definition_id` |
| `mud_npc_combat_rewards` | `npc_definition_id`, `reward_type`, `target_id`, `amount` |
| `mud_quests` | `name`, `description`, `prerequisite_quest_id` |
| `mud_quest_objectives` | `quest_id`, `objective_type`, `target_id`, `description`, `required_count`, `sort_order` |
| `mud_quest_rewards` | `quest_id`, `reward_type`, `target_id`, `amount` |
| `mud_quest_givers` | `quest_id`, `npc_definition_id` (no `id` field) |

`mud_npc_combat_profiles` and `mud_npc_equipment` also have no `id` field;
their NPC reference is their key. An item instance must be in a room **or**
inside another placed item container. Put the containing item in the same
array; it is installed before its children regardless of JSON order. Set
`is_open` to `0` or `1`. An NPC instance needs a room. Item types are `misc`,
`equipment`, `container`, `consumable`, and `key`; equipment slots are `head`,
`body`, `hands`, `legs`, `feet`, `main_hand`, `off_hand`, and `accessory`.

For NPC combat, `maximum_health` is 1 to 1,000,000; `attack` is 1 to 100,000;
`defense` and `experience_reward` are 0 to 100,000; `respawn_seconds` is 5
to 86,400. A profile makes the NPC fightable; omit it for a purely
conversational NPC. A combat reward uses `reward_type: "experience"` with no
`target_id`, or `reward_type: "item"` with an item definition `target_id`.
`amount` is 1 to 100,000. NPC equipment uses one item definition and one
equipment slot per NPC; the item should be equipment for that slot.

Give each conversation one start node (`is_start: 1`) and a distinct
`node_key` per node. Responses point to a `node_id` and may name a
`next_node_id` in the same conversation. `accept_quest_id` starts a quest;
`required_state_key` / `required_state_value` gate a response, and
`state_key` / `state_value` save conversation state. `sort_order` controls
display order. To offer a quest through a character, include both a
`mud_quest_givers` link and a dialogue response accepting that quest.

Quest objectives support `talk_npc` (target an NPC **definition**),
`visit_room` (target a room ID), and `possess_item` (target an item
**definition**). `required_count` is at least 1, and `sort_order` orders
objectives. A quest may name another local quest in
`prerequisite_quest_id`; prerequisite chains cannot form cycles. Quest
rewards use `reward_type: "experience"` with no `target_id`, or `"item"`
with an item definition `target_id`; `amount` is at least 1.

`properties_json`, `behaviors_json`, and `state_json` may be JSON objects or
JSON text in the file. Item properties can include `heal`, `value`,
`reveal_exit`, `use_message`, equipment bonuses, firearm data, attribute
`requirements`, and timed effects. `behaviors_json` can make a combat-capable
NPC peaceful with `{"aggressive": false}` or `{"friendly": true}`.
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

Keys in this file are local to the world. StationNet derives stable global keys
from the world ID, kind, and local key, so another world or standalone import
may use the same local key. At most 100 definitions of each kind may be bundled.
Imported definitions stay installed even when disabled or when the world is
removed. Importing a world does not activate it or change existing characters.

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

If you omit `content.json` or `origins.json`, omit them from the command too. Confirm the ZIP
lists the JSON files directly, without a containing folder. In **SYSOP Settings → MUD service →
Traditional Worlds**, import the ZIP and select it as the active world. Choose
whether its bundled races and classes replace, combine with, or are selected
alongside currently available choices. The same controls are available on
activation; manual selection can include any installed origin. The
world builder can then edit its rooms and exits. The **Export World ZIP** button
downloads the current map, including builder changes. For a station-built
world, the exported manifest uses `my-world` as a starter ID; change it before
importing on the same station. The exporter assigns simple room IDs so the
files remain easy to edit. In sysop settings, choose any installed custom races
and classes to bundle in an export. The builder export includes the world's
linked definitions automatically.

Changing the active world moves traditional-world players to the new starting
room and informs them. Procedural dungeon players remain where they are. To
uninstall a custom world, select another active world first, then use **Remove
World**. The original station world cannot be uninstalled.

The installed ZIP is kept for sharing. The active world is read from the
database so builder changes are included the next time you export. Removing a
world deletes its rooms and placed content while keeping player possessions
and quest records intact.
