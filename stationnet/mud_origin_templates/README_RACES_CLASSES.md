# Custom MUD races and classes

StationNet accepts individual race or class UTF-8 JSON files and combined
origin packs in SYSOP Settings > MUD service. Download a template there, or
start with the complete examples below. The same choices appear in traditional
and procedural character creation. Each individual file is limited to 32 KB.

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
  "effects": {"defense": 1}
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
  "effects": {"defense": 1},
  "skill": "guard",
  "spells": ["ward"],
  "equipment": [
    {"key": "trail-knife", "name": "Trail Knife",
     "description": "A short, sturdy knife carried on the trail.",
     "slot": "main_hand", "attack": 1, "defense": 0}
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
| `effects` | optional | optional | Object with any of `attack`, `defense`, `experience_percent`. Each value is an integer from -100 to 100; `{}` is valid. |
| `skill` | — | required | One key from the active character-options file's `skills` list. Defaults: `arms`, `guard`, `lore`. |
| `spells` | — | optional | Array of distinct supported spell keys; `[]` gives no spells. |
| `equipment` | — | optional | Array of up to eight starter items; `[]` gives none. |

Each equipment entry needs a unique `key` within the class, a `name` of 2 to
80 characters, a `description` of 1 to 1,000 characters, and a `slot`. Slots
are `head`, `body`, `hands`, `legs`, `feet`, `main_hand`, `off_hand`, and
`accessory`. Optional `attack` and `defense` are integers from 0 to 100 and
default to zero. Origin equipment supports these basic bonuses; its JSON
cannot specify firearm or consumable properties.

Character creation allocates points according to `stationnet/character_options.json`
(or Flask's `MUD_CHARACTER_OPTIONS` file), then adds race and class modifiers.
**Every final attribute must be from 1 to 30.** An origin can import
successfully yet produce an invalid race/class/point allocation combination,
so test combinations players can select. Origin effects add to attack,
defense, or earned experience percentage. A class's favored skill starts at
rank 2 if the player selects it among starting skills; other selected skills
start at rank 1. Individual imports cannot add skill types or change the point
budget.

## Spells and mana

`spells` belongs to a class. The supported keys and mana costs are:

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

## Install and maintain choices

1. Pick stable keys. Save one valid UTF-8 JSON object under 32 KB, or group
   definitions into a pack under 1 MB.
2. Import it in the matching individual control or the Origin Pack control.
   Correct any validation error, then
   create a test character to check its combinations, gear, and spells.
3. Imported choices start enabled. Disabling one hides it from new character
   creation. At least one race and class must stay enabled while either MUD
   mode is enabled.
4. Removing an imported choice takes it out of character creation. Existing
   characters retain their origin abilities through a private snapshot, and
   keys still used by characters remain reserved. Built-in choices can be
   disabled but cannot be removed.

The character-options file is a separate, station-wide configuration source
for the point budget, skill list, and built-in races/classes. If you provide
one through `MUD_CHARACTER_OPTIONS`, it must include `id`, `point_buy`,
`attribute_descriptions`, `races`, `classes`, `skills`, and
`starting_skill_choices`; see `stationnet/character_options.json` for that
advanced format. Imports add choices without replacing existing definitions.
Selection shortcuts in SYSOP Settings can enable all, none, default, or
imported choices. Imported choices can also be selected and removed together;
the confirmation summary lists every affected race and class before submission.
