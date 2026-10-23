# StationNet dungeon theme authoring

Dungeon themes are UTF-8 JSON files. Start by copying `starter-theme.json`, then
replace every bracketed value and every `replace-with-...` identifier. The file
is deliberately content-neutral; StationNet never calls an AI or invents theme
content at runtime.

## Install a theme

1. Create the NPC and item definitions that the theme will reuse. Their stable
   IDs are the `definition_id` values in enemy and item pools. List available
   IDs with `flask --app stationnet:create_app dungeon definitions`.
2. Give the theme a unique lowercase `id` and a version. Once a version has
   generated an instance, never change its content in place; increment `version`.
3. Validate before installing:

   `flask --app stationnet:create_app dungeon validate path/to/theme.json --check-definitions`

4. Copy the file into `<STATIONNET_DATA>/dungeon_themes/`. By default that is
   `data/dungeon_themes/`. Restarting is not required; commands scan the folder.
5. Confirm and preview it:

   `flask --app stationnet:create_app dungeon themes`

   `flask --app stationnet:create_app dungeon preview your-theme --seed test-001 --size 12 --layout maze`

Use `--json-output` on `preview` for the complete canonical manifest. To persist
a pending design for inspection (without starting a playable run), use:

`flask --app stationnet:create_app dungeon generate your-theme --seed test-001 --type random`

Recent failures are available through `flask --app stationnet:create_app dungeon failures`.
The `validate`, `preview`, and `generate` commands report template paths and
generation constraints without exposing a theme as playable content.

## Selection model

Generation first creates a connected layout, then assigns `entrance`, `exit`,
`normal`, and configured special roles. The exit is a farthest room from the
entrance. Every eligible list is processed in file order and selected with its
integer `weight`. Conditions can filter by layout, role, difficulty, normalized
depth, exit degree, and dead-end status. Conditions are data, not executable
expressions.

Each room selects one room pool entry and, when available, one environmental
entry. It then selects at most one eligible encounter. An encounter can refer
to an enemy pool, item pool, or both. `chance` is from `0` to `1`; pool `count`
is inclusive. Selections may repeat a definition when the count is greater than
one. Definition records are copied into the generated content snapshot, so a
later edit to the traditional-world definition cannot mutate an existing
dungeon design.

Room text is authored literally. The generator does not perform variable
expansion, execute scripts, or send text to an external service.

## Reproducibility rules

The seed, theme ID/version/content hash, generator version, size, difficulty,
and explicit layout determine the output. A saved template version and full
manifest are retained with generated instances. StationNet rejects reusing the
same theme ID and version for changed content. Array ordering is meaningful;
reordering weighted choices changes the generated result and therefore requires
a new theme version.

The JSON Schema is `theme-schema.json`. Runtime validation adds cross-reference,
range-order, duplicate-ID, and database-definition checks with JSON-path errors.
