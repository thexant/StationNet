# Custom MUD races and classes

StationNet accepts individual race or class UTF-8 JSON files and combined
origin packs in SYSOP Settings > MUD service. Download a template there, or
start with the complete examples below. The same choices appear in traditional
and procedural character creation. Each individual file is limited to 32 KB.

Races and classes do more than adjust starting attributes. A definition can
give lasting bonuses, traits that world content reacts to, weapon and armor
training, resistances, innate abilities, faster learning for chosen skills,
and extra health, mana, or skill points as the character levels. Every one of
these fields is optional, so older definitions keep working unchanged.

## Origin packs

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

## Complete race example

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

## Complete class example

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
effects, define items in a [traditional world](../mud_world_templates/README_CUSTOM_WORLDS.md)
or [dungeon theme](../dungeon_templates/README_DUNGEON_THEMES.md).

## Fields and validation

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
| `spells` | optional | optional | Array of distinct supported spell keys; `[]` gives no spells. On a race these are innate abilities, known in addition to the class's spells. |
| `equipment` | optional | optional | Array of up to eight starter items; `[]` gives none. |
| `tags` | optional | optional | Up to 12 lowercase keys, such as `["darkvision", "undead"]`. Tags mean nothing by themselves; world content, items, and dialogue can require or exclude them. |
| `proficiencies` | optional | optional | `{"weapons": [...], "armor": [...]}` gear categories this origin is trained with, as if a skill granted them. |
| `resistances` | optional | optional | Percentages from -100 to 100 against `physical` (enemy blows) and the hostile effects `poison`, `burning`, `bleeding`, `weakness`, `vulnerability`, `blinded`, `snared`. 100 is immunity; negative values are vulnerabilities. |
| `skill_affinities` | optional | optional | Up to 12 entries mapping an installed skill key or a skill group (`combat`, `weapon`, `armor`, `magic`, `stealth`, `exploration`, `social`, `knowledge`) to a percentage from -50 to 200. Practice toward those skills is scaled by it. |
| `progression` | optional | optional | Growth on top of the usual rules: `health` and `mana` (-50 to 50), `health_per_level` and `mana_per_level` (-5 to 5), `skill_points` (0 to 10), and `starting_skill_choices` (0 to 3 extra picks at creation). |

### Effects

`effects` accepts the same effect types as skills: `attack`,
`melee_damage`, `firearm_damage`, `spell_power`, `healing`, `defense`,
`accuracy`, `evasion`, `experience_percent`, `stealth`, `flee`, `search`,
`lockpick`, and `shop_discount`. Origin effects always apply. For a bonus
that grows with rank or only applies with certain gear, define a custom skill
(see the [skills guide](../mud_skill_templates/README_SKILLS.md)) and grant it
through `skills`.

### How the other fields play out

- **Resistances.** `physical` scales the damage of enemy blows that land
  after armor; any resistance below 100 still lets at least 1 point through.
  Effect resistances scale the potency and duration of hostile effects
  enemies inflict. An effect reduced to nothing is shrugged off.
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
  exclude them (see [custom worlds](../mud_world_templates/README_CUSTOM_WORLDS.md)).
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
[skills guide](../mud_skill_templates/README_SKILLS.md) to add new ones.
Origin imports cannot change the point budget.

## Spells and mana

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
Adding an unknown spell name to JSON cannot create a new spell implementation.

A traditional world may rename mana (for example to Energy) or disable it; see
the [world guide](../mud_world_templates/README_CUSTOM_WORLDS.md#rename-or-disable-mana).
Renaming changes only the wording. In a world without the resource, spells are
unavailable and mana growth is hidden, but `progression` mana values still
apply, so they take effect again in procedural dungeons or if the world turns
the resource back on.

## Install and maintain choices

1. Pick stable keys. Save one valid UTF-8 JSON object under 32 KB, or group
   definitions into a pack under 1 MB.
2. Import it in the matching individual control or the Origin Pack control.
   Correct any validation error, then
   create a test character to check its combinations, gear, and spells.
3. Imported choices start enabled. Disabling one hides it from new character
   creation. At least one race and class must stay enabled while either MUD
   mode is enabled.
4. Removing an imported choice takes it out of character creation. Its
   definition is kept while any character or archived campaign still uses
   it, so those characters retain their origin abilities, and the key stays
   reserved. Built-in choices can be disabled but cannot be removed.

The character-options file is a separate, station-wide configuration source
for the point budget, skill list, and built-in races/classes. If you provide
one through `MUD_CHARACTER_OPTIONS`, it must include `id`, `point_buy`,
`attribute_descriptions`, `races`, `classes`, `skills`, and
`starting_skill_choices`; see `stationnet/character_options.json` for that
advanced format. Imports add choices without replacing existing definitions.
Selection shortcuts in SYSOP Settings can enable all, none, default, or
imported choices. Imported choices can also be selected and removed together;
the confirmation summary lists every affected race and class before submission.
