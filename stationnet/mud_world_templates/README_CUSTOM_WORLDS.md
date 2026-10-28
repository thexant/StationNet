# File-authored MUD worlds

StationNet stores imported world ZIPs in `data/mud_worlds/` (or
`<STATIONNET_DATA>/mud_worlds/`). You can build a traditional world with a text
editor. Put three UTF-8 JSON files at the root of a ZIP archive:
`manifest.json`, `rooms.json`, and `exits.json`. Add `content.json` for items,
NPCs, dialogue, quests, trainers, and placements. Add `origins.json` for custom
races and classes, and `skills.json` for custom skills. No code or scripts run
from a world package.

Archives are limited to 16 MB, each JSON file to 8 MB, 1000 rooms, and 5000
exits. `content.json` may have up to 5,000 records in each supported array.
The ZIP must contain only those three required files and optional
`content.json`, `origins.json`, and `skills.json`, all at its root. Imports validate references
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
Add `"key_item_id": "local-key-id"` to require a key item defined in
`content.json`. Add `"pick_difficulty": 30` (1–200) to make the lock pickable
with `/pick <exit>` and the Lockpicking skill; an exit may have a key, a
pickable lock, or both. Unlocking persists per character; `/look <exit>`
reports the lock state.

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
| `mud_room_discoveries` | One-time per-character search clues and items | `room_id`, `clue`, optional `item_definition_id`, optional `difficulty` |
| `mud_npc_trainers` | Skills an NPC teaches for coins | `npc_definition_id`, `skill_key`, `price`, `max_rank` |
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
| `mud_dialogue_responses` | `node_id`, `response_text`, `next_node_id`, `accept_quest_id`, `deliver_item_id`, `turn_in_quest_id`, `required_state_key`, `required_state_value`, `state_key`, `state_value`, `sort_order`, `conditions_json`, `outcomes_json` |
| `mud_npc_definitions` | `name`, `description`, `conversation_id`, `properties_json`, `behaviors_json` |
| `mud_npc_instances` | `definition_id`, `room_id`, `state_json` |
| `mud_npc_combat_profiles` | `npc_definition_id`, `maximum_health`, `attack`, `defense`, `experience_reward`, `respawn_seconds`, optional `accuracy`, `evasion` |
| `mud_npc_equipment` | `npc_definition_id`, `equipment_slot`, `item_definition_id` |
| `mud_npc_combat_rewards` | `npc_definition_id`, `reward_type`, `target_id`, `amount`, `chance_percent` |
| `mud_quests` | `name`, `description`, `prerequisite_quest_id`, `completion_mode`, `turn_in_npc_definition_id`, `sequential`, `abandonable`, `conditions_json` |
| `mud_quest_objectives` | `quest_id`, `objective_type`, `target_id`, `description`, `required_count`, `sort_order`, `optional`, `consume_on_turn_in`, `recipient_npc_definition_id` |
| `mud_quest_rewards` | `quest_id`, `reward_type`, `target_id`, `amount` |
| `mud_npc_shops` | `npc_definition_id`, `buys_items`, `sell_percent` (no `id`) |
| `mud_shop_stock` | `npc_definition_id`, `item_definition_id`, `price`, `quantity` |
| `mud_item_spawns` | `definition_id`, `room_id`, `respawn_seconds` |
| `mud_room_discoveries` | `room_id`, `clue`, `item_definition_id`, `difficulty` |
| `mud_npc_trainers` | `npc_definition_id`, `skill_key`, `price`, `max_rank` |
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
**definition**), `visit_room` (target a room ID), and `possess_item` and
`deliver_item` (target an item **definition**). `talk_npc` advances when the
player starts a conversation with `/talk`; accepting a quest in dialogue does
not count as talking to the giver. Deliveries use `/deliver <item>` (or a
`deliver_item_id` response) beside the objective's `recipient_npc_definition_id`,
or beside one of the quest's givers when no recipient is set, and consume the
item. `required_count` is 1 to 100. A quest may name another local quest in
`prerequisite_quest_id`; prerequisite chains cannot form cycles. Quest
rewards use `reward_type: "experience"` or `"coins"` with no `target_id`, or `"item"`
with an item definition `target_id`; `amount` is at least 1.

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
JSON text in the file. Item properties can include `heal`, `value`,
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
For renewable room-floor resources, add `mud_item_spawns` with a 5–86,400 second
`respawn_seconds`; do not also place an ordinary item instance for that spawn.
`mud_room_discoveries` reveals a clue and optional item once per character with
`/search`. Set `difficulty` (0–200, default 0 = always found) to hide it
better; the Perception skill and Intellect improve the odds, and only rooms
with such hidden discoveries let searching practice Perception.

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
for that turn. Use either `wander` or `patrol`, never both. Intervals must be
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

If you omit `content.json`, `origins.json`, `skills.json`, or `dungeons.json`, omit them from the command too. Confirm the ZIP
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
