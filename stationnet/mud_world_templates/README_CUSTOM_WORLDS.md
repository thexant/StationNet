# File-authored MUD worlds

StationNet stores imported world ZIPs in `data/mud_worlds/` (or
`<STATIONNET_DATA>/mud_worlds/`). You can build a traditional world with a text
editor. Put three UTF-8 JSON files at the root of a ZIP archive:
`manifest.json`, `rooms.json`, and `exits.json`. Add `content.json` for items,
NPCs, dialogue, quests, professions, trainers, and placements. Add `origins.json` for custom
races and classes, `skills.json` for custom skills, `dungeons.json` for
procedural dungeons, `events.json` for [triggers](#5c-events-and-triggers-eventsjson)
that make the world react to players, and `wilderness.json` for large
[wilderness areas built from room presets](#5d-wilderness-and-room-presets-wildernessjson). Triggers are declarative settings: no
code or scripts run from a world package.

Archives are limited to 16 MB, each JSON file to 8 MB, 1000 authored rooms, and 5000
exits. Wilderness rooms do not count toward the room limit; a world may hold up
to 1,000,000 of them. `content.json` may have up to 5,000 records in each supported array.
The manifest may also turn on the optional [world life](#5f-world-life)
systems. The ZIP must contain only those three required files and optional
`content.json`, `origins.json`, `skills.json`, `dungeons.json`,
`events.json`, and `wilderness.json`, all at its root. Imports validate references
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
this under **Resource** in the World Builder. Exports include the setting
whenever it differs from the default. Procedural dungeons always use the usual
Mana.

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

A room may also stand on the [wilderness grid](#5d-wilderness-and-room-presets-wildernessjson):
add whole-number `"x"`, `"y"` and `"z"` together. It keeps its own name, text,
exits and contents, and links automatically to neighbouring grid rooms. Add
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

## 4. Add optional world content

### Magic, mana, and timed effects

Item and NPC definition `properties_json` may include shared effects. A consumable
can use `"on_use_effects"`; equipped weapons and hostile NPCs can use
`"on_hit_effects"`. For example:

```json
{"on_hit_effects": [{"key": "poison", "potency": 2, "duration": 3}]}
```

Keys are `poison`, `burning`, `bleeding` (damage each combat turn), `might` and
`weakness` (attack), `ward` and `vulnerability` (defense), `focus` and `blinded`
(accuracy), or `evasive` and `snared` (evasion). Potency is 1–1000
and duration is 1–100 turns. Reapplying a key keeps its stronger potency and
longer remaining duration. Classes declare known spells through imported
class JSON or `character_options.json`; the world ZIP cannot add spell
implementations. Supported spells are `ember` (8 mana), `venom` (7), `rend`
(6), `hex` (7), `mend` (7), `ward` (6), and `might` (6). The first four target
an enemy; the last three target the caster. Players use `/spells`,
`/cast <spell> [enemy]`, and `/rest` to recover mana outside combat when no
hostile enemy is nearby. Intellect improves mana and spell strength. Worlds
can rename mana or disable it and its spells; see
[Rename or disable mana](#rename-or-disable-mana). See the
[race and class guide](../mud_origin_templates/README_RACES_CLASSES.md) for class setup.

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
| `mud_quests` | `name`, `description`, `prerequisite_quest_id`, `completion_mode`, `turn_in_npc_definition_id`, `sequential`, `abandonable`, `conditions_json` |
| `mud_quest_objectives` | `quest_id`, `objective_type`, `target_id`, `description`, `required_count`, `sort_order`, `optional`, `consume_on_turn_in`, `recipient_npc_definition_id` |
| `mud_quest_rewards` | `quest_id`, `reward_type`, `target_id`, `amount` |
| `mud_npc_shops` | `npc_definition_id`, `buys_items`, `sell_percent` (no `id`) |
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
| `abandonable` | `1` (default) or `0` | Whether `/quest abandon <name>` may drop the active quest. Abandoning clears its progress so it can be accepted again. Items already delivered are not returned. |
| `conditions_json` | Condition array (default `[]`) | Who may accept the quest. See the condition reference below. |

| Objective field | Values | Meaning |
| --- | --- | --- |
| `sort_order` | Integer | Display order and, in sequential quests, the stage number. |
| `optional` | `0` (default) or `1` | Optional objectives are tracked but never block completion. |
| `consume_on_turn_in` | `0` (default) or `1` | `possess_item` only. At turn-in the player must still carry `required_count` of the item (unequipped, not run loot), and those items are taken. |
| `recipient_npc_definition_id` | NPC definition ID or omitted | `deliver_item` only. Who receives the delivery. |

Rewards are paid exactly once per character. Repeated turn-ins, repeated
dialogue choices, and completed quests cannot pay again. Players manage quests
with `/quests`, `/quests completed`, `/quest available`, `/quest info <name>`,
`/quest accept <name>`, `/quest turnin <name>`, and `/quest abandon <name>`.

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
| `quest` | `quest`, `status`: one or more of `not_started`, `active`, `ready`, `completed` (default `completed`) | The character's quest status matches. `ready` means a `turn_in` quest whose objectives are done. |
| `objective` | `objective` | That quest objective is complete. |
| `state` | `key`, `value` | This NPC's saved per-player state for `key` equals `value` (`""` when unset). |
| `claimed` | none | This response's outcomes have already been granted. Use it with `negate` to hide a one-time offer after it is claimed. |
| `flag` | `scope` (`player`, the default, or `world`), `key`, and optionally `value` or `min`/`max` | With `value`, the flag equals that text. With `min`/`max`, the flag read as a number (unset is 0) is within the bounds. With neither, the flag is set. Triggers set flags; see [events](#5c-events-and-triggers-eventsjson). |
| `object_state` | `object` (a placed item), `state` | The object's current state equals `state` (`""` until changed). |
| `in_room` | `room` | The character is in that room. |
| `knows_destination` | `destination` | The character has learned that [destination](#5e-wayfinding-destinations) (by visiting, dialogue, a trigger, or reading). Carrying a map does not count. |
| `reputation` | `faction`, and `rank` (one or a list of rank keys) or `min`/`max` (-1000 to 1000) | The character's [standing](#factions-and-reputation) with the faction matches. |
| `wanted` | optional `faction`, optional `min_fine` (default 1) | The character owes at least `min_fine` coins in unpaid [fines](#crime-and-consequences), to that faction or to any. Never holds while crime is off; add `"negate": true` to bar wanted characters. |
| `disposition` | optional `npc` (definition), and `tier` (one or a list of `hostile`, `unfriendly`, `neutral`, `friendly`, `trusted`) or `min`/`max` (-100 to 100) | That NPC's [regard](#relationships) for the character matches. Without `npc` it means the NPC being spoken to. Never holds while relationships are off. |
| `time` | `from`, `to` (hours 0–23) | The [world clock](#world-clock) reads an hour from `from` up to, but not including, `to`. `{"from": 20, "to": 4}` wraps past midnight. Never holds while the clock is off. |

Flag keys are 1 to 80 lowercase letters, digits, and `. _ : -`, such as
`gate.open` or `rats_slain`. Inside a procedural dungeon, `world` flags belong
to that run. `object_state`, `in_room`, `knows_destination`, `time`,
`disposition`, `reputation`, and `wanted` are not available in dungeon templates.

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
| `disposition` | `amount` (-100 to 100, not 0), optional `npc` | Changes an NPC's [regard](#relationships) for the player; the NPC being spoken to unless `npc` names another. Not available in dungeon templates. |

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
`accuracy` applies only to its own shots.

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
The World Builder offers these results and can edit a discovery without erasing
characters' existing discovery records. Deleting a referenced object or exit
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
  "ambient": {"interval_seconds": 60, "lines": ["Mind the tide.", "Welcome ashore."]},
  "wander": {"interval_seconds": 120},
  "reactions": {"enter": "Welcome, traveler.", "say": "I hear you.", "get": "Take care with that."}
}
```

`wander` chooses a room exit. To follow specific exits, use `patrol` instead:
`{"patrol":{"interval_seconds":90,"directions":["north","south"]}}`.
The directions are attempted in order; a missing exit leaves the NPC in place
for that turn. Use either `wander` or `patrol`, never both. With the world clock
on, a [`schedule`](#npc-schedules) moves an NPC by the hour instead. Intervals must be
5–86400 seconds. `ambient.lines` accepts 1–20 lines of up to 300 characters;
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
[race and class guide](../mud_origin_templates/README_RACES_CLASSES.md),
including `tags`, `resistances`, `proficiencies`, `skill_affinities`, and
`progression`. Their `skills`, favored `skill`, and `skill_affinities` may name
skills from the same package's `skills.json` by local key.

Keys in this file are local to the world. StationNet derives stable global keys
from the world ID, kind, and local key, so another world or standalone import
may use the same local key. Conditions, exit conditions, and item requirements
that name a bundled race or class by its local key are mapped to the global key
on import and back to the local key on export. At most 100 definitions of each kind may be bundled.
Imported definitions stay installed even when disabled or when the world is
removed. Importing a world does not activate it or change existing characters.

### Optional skills

`skills.json` holds `{"skills": [...]}` with up to 100 skill definitions in the
format described in the [skills guide](../mud_skill_templates/README_SKILLS.md).

- **Local keys.** As with origins, keys are local to the world. Races,
  classes, trainers, and other bundled skills may refer to them by local key,
  and StationNet rewrites those references to the installed global keys.
- **Requirements.** A skill's `requirements.races` and `requirements.classes`
  may name races and classes from the same package.
- **Availability.** Bundled skills follow the same import or activation mode
  as races and classes. When a world bundles no skills, the station's current
  skills are kept.
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
  `README_DUNGEON_THEMES.md`: generation bounds, layouts, special roles such as
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
- **Changing the active world** ends that world's live runs; their loot is
  lost and their explorers are moved with everyone else. Deleting an entrance,
  room or template never interrupts a run in progress. A missing return room
  falls back to the start room.

World builders can do all of this from the **Dungeons** section of the World
Builder and the **Dungeon entrances** panel on each room. Templates can start
from any installed dungeon theme and have an unsaved layout preview. The
builder's export writes `dungeons.json` automatically.

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
| `crime` | `room`, `npc` (a witness or victim of that type), or `faction` (one whose standing the crime cost) | A player is seen committing a [crime](#crime-and-consequences). |
| `timer` | `room`, plus `interval_seconds` (30–86,400) | Repeatedly while the world is active, whether or not anyone is there. Timers have no player. |
| `manual` | none | Only when another trigger runs it with `run_trigger`. Use these to share a set of actions. |

### Trigger fields

| Field | Default | Meaning |
| --- | --- | --- |
| `id` | required | Lowercase letters, digits, and hyphens; unique in the file. |
| `name` | the `id` | Shown in the World Builder. |
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
| `run_trigger` | `trigger` | Runs a `manual` trigger. The manual trigger's own conditions and limits still apply. |
| `learn_destination`, `forget_destination` | `destination` | Teaches the player the way to a [destination](#5e-wayfinding-destinations), or makes them forget it. |
| `disposition` | `npc` (definition), `amount` (-100 to 100, not 0) | Changes that NPC's [regard](#relationships) for the player. |
| `reputation` | `faction`, `amount` (-1000 to 1000, not 0) | Changes the player's [standing](#factions-and-reputation) with a faction. |
| `fine` | `faction`, `amount` (1–100,000) | Fines the player, as a [crime](#crime-and-consequences) would, without changing standing. |
| `clear_fines` | optional `faction` | Pardons the player's unpaid fines, to that faction or to all. |

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

World builders can create, edit, disable, and remove triggers in the
**Events** section of the World Builder. Each room also lists the triggers
that start there. **Reset script state** clears the active world's flags, fire
counts, object and exit states, and spawned NPCs, which is useful while
testing. Builder forms use installed IDs; the **IDs to use** list shows them.
The builder's export writes `events.json` with simple local IDs.

## 5d. Wilderness and room presets (`wilderness.json`)

Large outdoor areas such as forests, plains, mountains, deserts and swamps are
not written room by room. Instead, `wilderness.json` defines reusable **room
presets** and paints them onto a coordinate grid. Every painted square becomes a
real room: players walk through it with ordinary directions, meet each other
there, drop items, fight, and find authored landmarks. Nothing about it is
fast travel.

Coordinates are whole numbers. `x` grows to the east, `y` grows to the **south**,
and `z` is the layer, which grows upward. Neighbouring squares link automatically:

- `north`, `east`, `south` and `west` link any two neighbours on the same layer.
- The diagonals link only when **both** rooms allow eight-way links
  (`"links": "8"`). Authored rooms on the grid accept whatever their neighbour
  allows.
- `up` and `down` link a square to the one directly above or below it on the
  next layer.
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
| `terrain` | `grassland`, `forest`, `dense-forest`, `hills`, `mountains`, `desert`, `swamp`, `tundra`, `water`, `road` or `custom` (the default). Colours the builder map. |
| `glyph` | One printable character for maps; not a space, `@`, `*` or `P`. Default `.`. |
| `links` | `"4"` (default) or `"8"`. |
| `names` | Optional list of up to 20 room-name variants (up to 80 characters each). |
| `descriptions` | Required list of 1–40 descriptions, each up to 4000 characters. A plain string means weight 1. |
| `environment` | Optional `light` (`bright`, `dim`, `dark`), `exposure` (`open`, `sheltered`), up to 20 `tags`, and up to 20 `lines` of ambient text added after the description. |
| `encounters` | Optional. `chance` is the percentage (0–100) rolled each time a player enters; `max_live` (0–5) caps creatures per room; `despawn_seconds` (10–86400) removes them later. Each creature names an NPC definition and may limit itself to a danger range (1–10). |
| `resources` | Optional. When a player enters, each item rolls its `chance` and places 1–5 copies, up to `max_per_room` (0–5). The room then waits `respawn_seconds` (60–86400) before gathering again. |

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

Painted squares are stored as **run-length encoded rows**, one layer per `z`:

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

Exports from the builder always write this compact form, so a 500 × 500 forest
takes only a few kilobytes.

### Building in the World Builder

Open **World Builder → Wilderness**.

- **Presets.** Create a preset from a starter (grassland, forest, dense forest,
  mountains, desert, swamp), as a copy of an existing preset, or from scratch.
  Edit it with the guided form, where creatures and items can be typed by name,
  or as JSON. Presets can be imported and exported as JSON files: one preset, or
  `{"presets": [...]}`. A preset that rooms still use can only be removed after
  its rooms are moved to another preset.
- **Map.** Pan, change layer and zoom. Choose **Inspect** to open a room,
  **Rectangle** or **Ellipse** to pick two corners, or **Brush** to paint
  individual squares.
- **Fill.** Choose a preset and, optionally, a region. **Only fill empty
  squares** leaves existing rooms alone. **Also repaint existing wilderness**
  changes the preset of wilderness rooms in place, so they keep their contents
  and anyone standing in them. Authored rooms are never overwritten. One fill
  covers up to 250,000 squares.
- **Remove wilderness.** Deletes only wilderness rooms in the area and anything
  lying in them. Players there move to the starting room. Dungeon entrances
  block the removal unless you also choose to remove them.
- **Room inspector.** Shows a square's text, exits, players, NPCs and items. From
  here you can place NPCs and items, add exits that reach beyond the grid, add a
  dungeon entrance, set walls and the region, or **convert it to an authored
  room**. Converting keeps its current text and position and unlocks every World
  Builder tool for it: descriptions, triggers, discoveries and locks. Use this
  for settlements, roads, ruins and other special locations.
- **Placing existing rooms.** Each authored room in the World Builder has
  **Wilderness grid placement**. Give it coordinates to drop it into the
  wilderness. A wilderness room already at that spot is absorbed: its NPCs,
  items, entrances and visitors move into the authored room.

Players can type `/map` for a 15 × 9 view of the land around them. `@` marks
the player, `P` other visible players, `*` authored landmarks, and each preset
appears as its glyph.

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
or nothing if the place is off the grid. In the World Builder, each room card
and the wilderness room inspector has a **Wayfinding destination** form. The
item form chooses which destinations an item teaches.

## 5f. World life

These optional systems make a world feel inhabited. Each is **off** until the
world turns it on, so existing worlds play exactly as before. Turn them on in
`manifest.json` or under **World Builder → World life**; exports keep the
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
panel. In the World Builder, the **Factions** section creates factions, ranks
and relations, and each NPC form chooses a faction.

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
  is an ordinary item, so dropping and taking it again is no crime. In the
  World Builder, each placed item in a room has an **Owner** choice.
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

If you omit `content.json`, `origins.json`, `skills.json`, `dungeons.json`, `events.json`, or `wilderness.json`, omit them from the command too. Confirm the ZIP
lists the JSON files directly, without a containing folder. In **SYSOP Settings → MUD service →
Traditional Worlds**, import the ZIP and select it as the active world. Choose
whether its bundled races, classes, and skills replace, combine with, or are
selected alongside currently available choices. The same controls are available on
activation; manual selection can include any installed origin. The
world builder can then edit its rooms and exits. The **Export World ZIP** button
downloads the current map, including builder changes. For a station-built
world, the exported manifest uses `my-world` as a starter ID; change it before
importing on the same station. The exporter assigns simple room IDs so the
files remain easy to edit. In sysop settings, choose any installed custom races,
classes, and skills to bundle in an export. The builder export includes the world's
linked definitions automatically.

Changing the active world moves traditional-world players to the new starting
room and informs them. Dungeon Portals players remain where they are; runs
in the outgoing world's own dungeons end, and their loot is lost. To
uninstall a custom world, select another active world first, then use **Remove
World**. The original station world cannot be uninstalled.

The installed ZIP is kept for sharing. The active world is read from the
database so builder changes are included the next time you export. Removing a
world deletes its rooms and placed content while keeping player possessions
and quest records intact.
