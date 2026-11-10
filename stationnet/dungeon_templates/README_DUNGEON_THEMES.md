# StationNet dungeon theme authoring

A dungeon theme is a portable, UTF-8 JSON package that tells StationNet what a
procedurally generated dungeon looks like: its rooms, creatures, items, and how
they are placed. StationNet does not invent content or execute theme scripts;
everything players see comes from the file you write.

**Contents**

- [Quick start](#quick-start)
- [Bundled themes](#bundled-themes)
- [Anatomy of a theme](#anatomy-of-a-theme)
- [Definitions: items, NPCs, enemies](#definitions-items-npcs-enemies)
- [Item properties and economy](#item-properties-and-economy)
- [Generation settings](#generation-settings)
- [Pools and encounters](#pools-and-encounters)
- [Conditions reference](#conditions-reference)
- [Gameplay rules themes should know about](#gameplay-rules-themes-should-know-about)
- [Install, validate, and preview](#install-validate-and-preview)
- [Determinism and versioning](#determinism-and-versioning)
- [Authoring checklist](#authoring-checklist)
- [Troubleshooting](#troubleshooting)

---

## Quick start

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
format as the [race and class templates](../mud_origin_templates/README_RACES_CLASSES.md).
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
definitions in the [skills guide](../mud_skill_templates/README_SKILLS.md)
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

### Small playable theme, written from scratch

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

## Bundled themes

| File | Setting |
| --- | --- |
| `generic-fantasy-dungeon.json` | The conventional default starter theme. |
| `desert.json` | Buried desert temples. |
| `jungle-ruins.json` | Overgrown stone terraces in a river valley. |
| `frozen-frontier.json` | A snowbound expedition route leading to ruins beneath a glacier. |

StationNet copies each bundled file into the configured theme directory only
when that file is missing, so they appear in Dungeon Portals automatically.
Files you have installed are never overwritten. See
[Determinism and versioning](#determinism-and-versioning) for how to adopt an
updated bundled theme.

## Anatomy of a theme

| Top-level key | Required | Purpose |
| --- | --- | --- |
| `schema_version` | yes | `1` or `2`. Schema 2 adds the sections marked "2" below and rejects top-level keys it does not know. |
| `id` | yes | Unique theme identifier. |
| `version` | yes | Version string; see [versioning](#determinism-and-versioning). |
| `name` | yes | Display name. |
| `author` | yes for a custom file in `data/dungeons/` | Nonempty author name. |
| `description` | no | Author-facing notes. |
| `definitions` | no | Items, NPCs, and enemies owned by this theme. |
| `generation` | yes | Size, difficulty, layout types, and special roles. |
| `pools` | yes | Weighted text and content used to populate rooms. |
| `origins`, `skills` | no | Races, classes, and skills the theme bundles (portal themes only). |
| `effects`, `abilities` | no (2) | Timed effects and castable abilities the theme bundles (portal themes only). See [Bundled abilities and effects](#bundled-abilities-and-effects-schema-2). |
| `rewards` | no (2) | Experience rate and risk-loot settings. See [Rewards, scaling, and bosses](#rewards-scaling-and-bosses-schema-2). |
| `scaling` | no (2) | How much enemies strengthen per difficulty step. |
| `bosses` | no (2) | Multipliers for enemies marked `"boss": true`. |
| `triggers` | no | Run-scoped [triggers](#triggers-optional). |

**ID format.** Every `id` (theme, version, local definitions, pools, dialogue
nodes and responses) must match `^[a-z0-9][a-z0-9._-]{0,63}$`: lowercase
letters, digits, `.`, `_`, and `-`, starting with a letter or digit, at most 64
characters.

**Weights.** Wherever `weight` appears it is an integer from 1 to 1,000,000,
defaulting to 1. Higher weights are chosen proportionally more often.

## Definitions: items, NPCs, enemies

Use the top-level `definitions` object for content owned by the theme.

| Array | What it defines |
| --- | --- |
| `items` | Miscellaneous items, equipment, containers, consumables, and keys. |
| `npcs` | Conversational NPCs, optionally combat-capable. |
| `enemies` | Same shape as an NPC, but `combat` is required. |

### Items

Required fields: `id`, `name`, `description`.

- `item_type`: `misc`, `equipment`, `container`, `consumable`, or `key`.
- `equipment_slot`: `head`, `body`, `hands`, `legs`, `feet`, `main_hand`,
  `off_hand`, or `accessory`.
- `properties`: combat bonuses, consumable effects, and coin value. See
  [Item properties and economy](#item-properties-and-economy). Unknown keys are
  kept, so you can store theme-specific data here.
- `behaviors`: free-form theme-specific metadata, preserved as written.

### NPCs and enemies

Required fields: `id`, `name`, `description` (enemies also require `combat`).

- `properties` and `behaviors`: free-form objects. For combat-capable NPCs,
  `behaviors` also controls aggression (see
  [Gameplay rules](#gameplay-rules-themes-should-know-about)).
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
  [custom world guide](../mud_world_templates/README_CUSTOM_WORLDS.md#conditions)
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

### Item references

Equipment and item loot point at an item with **exactly one** of:

- `item_local_id`: the `id` of an item in this file's `definitions.items`.
- `item_definition_id`: an existing station-shared item definition (optional).

### Local IDs and ownership

Local IDs are short identifiers within the file. At installation StationNet maps
them to immutable runtime IDs containing the theme ID and version, records
their ownership, and materializes them through the existing item, equipment,
NPC, dialogue, combat, and reward systems. They cannot collide with another
theme or with traditional-world definitions. Generated room content keeps a
snapshot, and the complete template is retained in the version table.

## Item properties and economy

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
| Restricted equipment | `requirements`: attribute minimums (1 to 30), `level`, and `races`, `classes`, or `tags` lists, plus `not_races`, `not_classes`, `not_tags` | Only characters who meet every requirement can equip it, for example `{"tags": ["arcane"], "level": 3}`. See the [custom world guide](../mud_world_templates/README_CUSTOM_WORLDS.md) for details. |

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
  [Protective gear, cures, and hazards](../mud_world_templates/README_CUSTOM_WORLDS.md#protective-gear-cures-and-hazards).
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

### Loot ownership

The portal room provides the shop and private persistent storage. Dungeon loot
is marked as belonging to the active run:

| Outcome | Loot from the run |
| --- | --- |
| Complete at the exit | Banked. |
| Extract alive at the entrance | Banked, but the completion experience bonus is forfeited. |
| Death or `/abandon` | Destroyed. |

Equipment brought in from before the run and items already in storage survive
in every case.

## Generation settings

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

## Pools and encounters

`pools` controls placement. All pool entries share a base:

| Field | Meaning |
| --- | --- |
| `id` | Required. Unique within its pool list; referenced by encounters. |
| `weight` | Relative likelihood among eligible pools. |
| `conditions` | Filters for when the pool applies; see [Conditions reference](#conditions-reference). |

### Room and environment pools

- `pools.rooms` (at least one required): each needs `names` and `descriptions`.
- `pools.environments` (optional): each needs `descriptions`, adding variation
  on top of the room description.

`names` and `descriptions` are lists of `{ "text": "...", "weight": n }`
entries with at least one item. Cover every role you use (`entrance`, `exit`,
`normal`, and any special role) with a matching role-specific pool or an
unrestricted fallback pool, as in the small example above.

### Content pools: NPCs, enemies, items

Each entry in `pools.npcs`, `pools.enemies`, and `pools.items` needs:

- `count`: `{ "minimum": 0-100, "maximum": 0-100 }`, inclusive.
- `choices`: at least one entry. Each choice must contain **exactly one** of
  `local_id` (an ID in the matching `definitions` array) or `definition_id` (an
  existing shared StationNet definition). Choices may also carry their own
  `weight` and `conditions`.
- `chance` (optional): 0 to 1, the probability the pool produces anything.

A theme may mix local and shared choices, or use only embedded content.

### Encounters

At most one eligible encounter is selected per room. An entry in
`pools.encounters` is the complete encounter definition. It can
connect any combination of `npc_pool`, `enemy_pool`, and `item_pool` (each the
`id` of a pool in the matching list), plus `chance` and an open-ended `config`
object. The selected encounter, its `config`, and the selected content are
snapshotted into the generated room. `config` is stored with the run for
reference only; the engine does not interpret it. Express behavior through
pools, roles, `bosses`, and triggers instead.

### Environmental features

Environment pools add descriptive text to rooms. A trigger whose source is
`{"environment": "<environment pool id>"}` fires in rooms that drew that
environment, so combined with `apply_effect` an environment becomes a hazard
(a lava floor applying `burning`) or a boon (a spring applying `regeneration`).
`cure_effect` makes a cleansing environment, such as a clean spring that
removes `poison`.

The starter demonstrates every supported definition and connection with neutral
placeholders: an NPC-only encounter, and a combat encounter that draws from an
enemy pool and an item pool.

## Conditions reference

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

## Triggers (optional)

A theme may add a top-level `triggers` array. Triggers make a run react: when
something happens, if the conditions hold, the actions run in order. They use
the format of world triggers (section 5c of `README_CUSTOM_WORLDS.md`), with
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

## Bundled abilities and effects (schema 2)

A portal theme may bundle `effects` and `abilities` in the same format as a
world's `effects.json` and `abilities.json` (see
`../mud_ability_templates/README_ABILITIES_EFFECTS.md`). Its classes and races
grant the abilities by local key in `spells`, and its items, enemies, and
triggers apply its effects by local key. On import they are installed under
keys derived from the theme ID; the theme's races, classes, and skills are
offered in the dungeon world. Keys may not reuse a built-in ability or effect.
World dungeon templates cannot bundle these; they use the world's own.

## Rewards, scaling, and bosses (schema 2)

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

## Gameplay rules themes should know about

- Known abilities come from the character's race and class, including imported
  ones and those a theme bundles. `/spells` lists them, `/cast <ability>
  [enemy]` spends mana, and `/rest` restores mana when no hostile enemy is
  nearby. The built-in abilities are `ember`, `venom`, `rend`, `hex`, `mend`,
  `ward`, and `might`; see the [race and class guide](../mud_origin_templates/README_RACES_CLASSES.md)
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

## Install, validate, and preview

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

### Export, share, and upgrade

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

### Using a theme inside an authored world

The same format powers **world dungeons**. Builders can create templates
in World Forge (**Dungeon templates → New from a theme**), starting from any
installed theme, and place entrances in their rooms. Those templates belong to the world and
travel in its ZIP as `dungeons.json`. They take no `id` or `version`, because
the station assigns them per revision. They also cannot bundle `origins`,
`skills`, `abilities`, or `effects`; they use the world's own, by local key. Enabling or disabling themes here does not affect them. See section
5b of `README_CUSTOM_WORLDS.md`.

## Determinism and versioning

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

## Authoring checklist

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

## Troubleshooting

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
