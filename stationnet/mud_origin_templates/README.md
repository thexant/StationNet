# MUD race and class imports

Download a race or class JSON template from SYSOP Settings > MUD service. Edit
the file and import it in that panel. The key must be unique among built-in and
installed choices, contain 2-40 lowercase letters, digits, or hyphens, and
start with a letter. A single file defines one race or one class. The maximum
file size is 32 KB.

Both types use `key`, `name`, `description`, `traits`, `attributes`, and
`effects`. Attribute keys are `strength`, `agility`, `intellect`, and
`vitality`; each integer modifier can be -10 through 10. Effect keys are
`attack`, `defense`, and `experience_percent`; each integer can be -100 through
100. Omit unused modifiers or set the object to `{}`.

Classes also use `skill` (one of `arms`, `guard`, or `lore`), `spells` (a list of
distinct known spell keys), and `equipment` (up to eight starting items).
Supported spells are `ember`, `venom`, `rend`, `hex`, `mend`, `ward`, and `might`.
Each equipment item needs a unique key, name, description, slot, and integer
attack and defense values from 0 through 100. Slots are `head`, `body`,
`hands`, `legs`, `feet`, `main_hand`, `off_hand`, and `accessory`.

Imported definitions are enabled on import. Disabling a choice prevents new
characters from selecting it. Removing an imported choice removes it from
character creation; characters already using it retain their name and effects.
The station keeps a private snapshot of a removed choice when existing
characters still use it, so its key remains reserved.
