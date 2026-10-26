# File-authored MUD worlds

StationNet stores imported world ZIPs in `data/mud_worlds/` (or
`<STATIONNET_DATA>/mud_worlds/`). You can build a traditional world with a text
editor. Put three UTF-8 JSON files at the root of a ZIP archive:
`manifest.json`, `rooms.json`, and `exits.json`. Add `content.json` for items,
NPCs, dialogue, quests, and placements. No code or scripts run from a world
package.

Archives are limited to 16 MB, each JSON file to 8 MB, 1000 rooms, and 5000
exits. Imports validate references and reject duplicate world IDs.

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

The world ID must be unique on the station. Use lowercase letters, digits, and
hyphens; begin with a letter. `stationnet` is reserved for the original builder
world. `start_room` must match a room ID in `rooms.json`.

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

### Magic and timed effects

Item and NPC definition `properties_json` may include shared effects. A consumable
can use `"on_use_effects"`; equipped weapons and hostile NPCs can use
`"on_hit_effects"`. For example:

```json
{"on_hit_effects": [{"key": "poison", "potency": 2, "duration": 3}]}
```

Keys are `poison`, `burning`, `bleeding` (damage each combat turn), `might` and
`weakness` (attack), or `ward` and `vulnerability` (defense). Potency is 1–1000
and duration is 1–100 turns. Reapplying a key keeps its stronger potency and
longer remaining duration. Character classes declare their known spells in
`character_options.json`. Players use `/spells`, `/cast <spell> [enemy]`, and
`/rest` to recover mana outside combat.

`content.json` is an object whose keys are authored MUD table names and whose
values are arrays of records. IDs are **local to the ZIP** and can be short
names such as `lantern` or `keeper` (with the same lowercase ID rule as rooms);
references use those local IDs. Rooms use
the IDs from `rooms.json`. StationNet gives every imported record a unique
internal ID, so two worlds may both have an item called `lantern`.

```json
{
  "mud_item_definitions": [
    {"id": "lantern", "name": "Brass lantern", "description": "A warm light.",
     "item_type": "misc", "properties_json": {}}
  ],
  "mud_item_instances": [
    {"id": "dock-lantern", "definition_id": "lantern", "room_id": "harbor"}
  ],
  "mud_npc_definitions": [
    {"id": "keeper", "name": "Harbor keeper", "description": "A patient watcher."}
  ],
  "mud_npc_instances": [
    {"id": "dock-keeper", "definition_id": "keeper", "room_id": "harbor"}
  ]
}
```

For more complex content, export an existing builder world and use its
`content.json` as a working example. The supported arrays are:

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

Use the same field names and values shown in exported content. JSON columns
such as `properties_json` and `state_json` may be JSON objects in the file.
Every reference must point to another record in the same package. The ZIP
contains authored definitions and placements; it does not carry characters,
inventories, quest progress, or procedural dungeon runs.

## 5. Import, edit, export, and remove

Download the starter ZIP from **SYSOP Settings → MUD service → Traditional
Worlds**, or zip the three files at the archive root yourself. In **SYSOP Settings → MUD service →
Traditional Worlds**, import the ZIP and select it as the active world. The
world builder can then edit its rooms and exits. The **Export World ZIP** button
downloads the current map, including builder changes. For a station-built
world, the exported manifest uses `my-world` as a starter ID; change it before
importing on the same station. The exporter assigns simple room IDs so the
files remain easy to edit.

Changing the active world moves traditional-world players to the new starting
room and informs them. Procedural dungeon players remain where they are. To
uninstall a custom world, select another active world first, then use **Remove
World**. The original station world cannot be uninstalled.

The installed ZIP is kept for sharing. The active world is read from the
database so builder changes are included the next time you export. Removing a
world deletes its rooms and placed content while keeping player possessions
and quest records intact.
