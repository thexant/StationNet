# StationNet dungeon theme authoring

Dungeon themes are portable UTF-8 JSON packages. Copy `starter-theme.json`,
replace its bracketed placeholders, and keep authored text and data in the file.
StationNet does not invent content or execute theme scripts.

The bundled `generic-fantasy-dungeon.json` is the conventional default starter
theme. `desert.json` explores buried desert temples, `jungle-ruins.json`
explores a river valley's overgrown stone terraces, and `frozen-frontier.json`
crosses a snowbound expedition route into ruins beneath a glacier. StationNet
copies all four into the configured theme directory when each file is missing,
so they are available in Dungeon Portals; user-installed theme files are never
overwritten.

## A self-contained theme

Use the top-level `definitions` object for content owned by the theme:

- `items` defines miscellaneous items, equipment, containers, consumables, and
  keys. `properties` uses the existing MUD item and combat values; see
  [Item properties and economy](#item-properties-and-economy). `behaviors` is
  preserved for theme-specific metadata.
- `npcs` defines conversational NPCs and optionally combat-capable NPCs. An NPC
  may embed a `conversation` with nodes and responses, equipment, and loot.
- `enemies` has the same shape as an NPC but requires `combat`. Combat supports
  maximum health, attack, defense, base experience, and respawn time.
- NPC/enemy `equipment` and item `loot` use `item_local_id` for an embedded item,
  or `item_definition_id` for optional station-shared content. Experience loot
  needs only `type: "experience"` and `amount`.

Local IDs are short identifiers within the file. At installation StationNet
maps them to immutable IDs containing the theme ID and version, records their
ownership, and materializes them through the existing item, equipment, NPC,
dialogue, combat, and reward systems. They cannot collide with another theme or
with traditional-world definitions. Generated room content retains a snapshot,
and the complete template is retained in the version table.

## Item properties and economy

Use explicit properties for items that affect a run:

| Item | Properties | Effect |
| --- | --- | --- |
| Weapon | `damage`, `attack`, or `attack_bonus` | Adds to attack damage while equipped. |
| Armour or shield | `armor`, `defense`, or `defense_bonus` | Reduces incoming damage while equipped. |
| Healing consumable | `heal`: positive integer | Restores up to that much health, consumes the item, and advances an enemy turn if in combat. |
| Navigation consumable | `reveal_exit: true` | Reveals the next direction toward the exit and consumes the item. |
| Any saleable item | `value`: integer from 1 to 100000 | Sets its full coin value; the portal trader buys it for 40%, rounded down with a minimum of one coin. |

The MUD infers a modest value for items without `value`, so older themes remain
saleable. Explicit values make treasure rewards predictable. The trader does
not buy keys. Use `item_type: "consumable"` for one-use supplies; a
`use_message` alone displays text and consumes the item but does not restore
health. Older bundled recovery items with the former "standard recovery rules"
message still heal for compatibility. New themes should use `heal` explicitly.

The portal room provides the shop and private persistent storage. Dungeon loot
is marked as belonging to the active run. Completing at the exit or extracting
alive at the entrance banks it; death or `/abandon` destroys that run's loot.
Equipment brought from before the run and items already in storage survive.
An entrance extraction keeps loot but gives up the completion experience bonus.

## Pools and encounters

`pools` controls placement. Room and environment pools provide weighted text.
`npcs`, `enemies`, and `items` are content pools; each choice must contain
exactly one of:

- `local_id`: an ID in the matching embedded `definitions` array.
- `definition_id`: an existing StationNet definition (optional shared content).

An entry in `pools.encounters` is the complete encounter definition. It can
connect `npc_pool`, `enemy_pool`, and/or `item_pool`. The selected encounter and
its `config` are snapshotted into the generated room along with selected
content. The starter demonstrates each supported definition and connection
using neutral placeholders rather than a supplied fictional theme.

Generation creates a connected layout, assigns `entrance`, `exit`, `normal`,
and configured special roles, then evaluates pools. Integer `weight` applies to
every eligible weighted list. Conditions can filter by layout, room role,
difficulty, normalized depth, degree, and dead-end status. `chance` is from 0
to 1 and `count` is inclusive. Conditions are data, not executable expressions.

The generator also places a small amount of station supply or treasure loot
outside theme pools, with rarer rewards at greater risk and a reward at the
exit. Themes can still place their own weapons, healing supplies, and treasures
through item pools and enemy loot. Room difficulty increases enemy combat stats
and experience rewards. Completion pays coins based on difficulty and rooms
visited, so a harder run offers greater rewards.

Enemies with `combat` attack when a player enters their room. Combat advances
only when a player acts; waiting or disconnecting does not give enemies extra
turns. Players can attack, defend, use an item, or attempt a risky flee. A
combat-capable NPC can remain neutral by setting `behaviors.aggressive` to
`false` or `behaviors.friendly`, `neutral`, or `passive` to `true`. Defeated
dungeon enemies stay defeated for that run; `respawn_seconds` applies to the
traditional world.

## Install and validate

1. Set a unique lowercase `id` and a version. Never change a version after it
   has generated an instance; increment `version` instead.
2. Validate the package:

   `flask --app stationnet:create_app dungeon validate path/to/theme.json --check-definitions`

   Embedded-only themes pass this check on a station with no custom world
   definitions. The option additionally verifies any shared `definition_id` or
   `item_definition_id` references.
3. Copy the JSON file to `<STATIONNET_DATA>/dungeon_themes/` (normally
   `data/dungeon_themes/`). Theme discovery is live.
4. Inspect and preview it:

   `flask --app stationnet:create_app dungeon themes`

   `flask --app stationnet:create_app dungeon preview your-theme --seed test-001 --size 12 --layout maze`

Use `--json-output` for the canonical manifest. Persist a pending design with:

`flask --app stationnet:create_app dungeon generate your-theme --seed test-001 --type random`

Playable themes appear in Dungeon Portals. Launch with
`/expedition <theme-id> [difficulty] [size]`; `/join <code>` joins an active
cooperative run. `dungeon definitions` lists optional shared IDs, and
`dungeon failures` lists recent generation failures.

## Determinism and versioning

The seed, theme ID/version/content hash, generator version, size, difficulty,
explicit layout, and ordered arrays determine output. Reordering choices can
change generation. StationNet rejects changed content under an already-used
theme ID/version. Existing shared-reference themes remain valid; a theme may
mix local and shared choices, or contain only embedded content.

The bundled themes now include explicit healing and treasure values. Their
versions were incremented because installed versions are immutable. StationNet
copies a bundled theme into a station's theme directory only when that file is
missing. Existing stations keep their installed files and active runs; to use
an updated bundled theme, replace the corresponding installed JSON with its new
version when ready. Older versions still work with the compatibility rules
above.

The machine-readable contract is `theme-schema.json`. Runtime validation adds
range ordering, local/shared reference resolution, definition compatibility,
duplicate-ID, conversation graph, pool cross-reference, and database checks
with JSON-path errors.
