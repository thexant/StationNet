# Starter room presets

These JSON files are the starters offered under **World Builder → Wilderness →
Room presets → Start from**. Each file is one room preset:

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
the builder after creating the preset.

To share your own presets, use **Export** on the Wilderness page. It writes
`{"presets": [...]}`, and **Import** reads either that format or a single
preset object. Creatures and items are stored by definition ID. When you import
into another world, tick **Drop creatures and items this world does not have**,
or fix the IDs in the JSON first.

The full preset format, regions, the `wilderness.json` package file and the
grid linking rules are documented in section 5d of the
[world guide](../mud_world_templates/README_CUSTOM_WORLDS.md#5d-wilderness-and-room-presets-wildernessjson).
