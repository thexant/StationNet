# Abilities and timed effects

Abilities are what characters cast with `/cast`. Effects are the timed
conditions that abilities, items, NPC attacks, and triggers put on a character
or an enemy. StationNet ships seven abilities and twelve effects, and you can
add more. Custom ones are built only from the engine's fixed parts, so every
one you install works in play; none of them can add a new mechanic.

Install them station-wide from **SYSOP Settings → Abilities & Effects** (one
ability, one effect, or a pack), bundle them in a world package
(`abilities.json`, `effects.json`), bundle them in a dungeon theme (schema 2
`abilities` and `effects`), or write a world's own in the World Builder's
**Definitions** section. Templates: `ability.json`, `effect.json`, `pack.json`.

## Effects

```json
{"key": "frostbite", "name": "Frostbite", "description": "Numbing cold that bites each turn.",
 "kind": "damage", "resist_as": "burning"}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `key` | yes | 2–40 lowercase letters, digits, or hyphens. Cannot reuse a built-in key. |
| `name` | yes | 2–40 characters; what players see. |
| `description` | no | Up to 500 characters. |
| `kind` | yes | What it does each combat turn. See below. |
| `sign` | stat kinds only | `1` for a bonus, `-1` for a penalty. |
| `resist_as` | no | A resistance that weakens it: `physical`, `poison`, `burning`, `bleeding`, `weakness`, `vulnerability`, `blinded`, or `snared`. Only harmful effects can be resisted. Equipment may also resist an effect by its own key. |
| `outside_combat` | no | `true` makes it also act, and wear off, every 30 seconds while its target is out of combat. It never takes a player below 1 health there. Default `false`: it acts only on combat turns and `/rest`. |

| `kind` | Each turn |
| --- | --- |
| `damage` | Deals its potency in damage. |
| `regen` | Restores its potency in health, never beyond the maximum. |
| `attack`, `defense`, `accuracy`, `evasion` | Raises (`sign: 1`) or lowers (`sign: -1`) that stat by its potency while it lasts. |

Whoever applies an effect sets its potency (1–1000) and duration (1–100
turns): `{"key": "frostbite", "potency": 2, "duration": 3}`. Reapplying keeps
the stronger potency and longer duration. Built-in effects are `poison`,
`burning`, `bleeding`, `regeneration`, `might`, `weakness`, `ward`,
`vulnerability`, `focus`, `blinded`, `evasive`, and `snared`.

## Abilities

```json
{"key": "frost-lance", "name": "Frost Lance", "command": "lance", "target": "enemy", "cost": 7,
 "damage": 8, "effects": [{"key": "frostbite", "potency": 2, "duration": 3}]}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `key` | yes | Cannot reuse a built-in ability key. |
| `name` | yes | 2–40 characters. |
| `command` | no | One word players type: `/cast lance`. Defaults to the key. The name works too. |
| `target` | yes | `enemy` (strikes one enemy) or `self` (acts on the caster). |
| `cost` | no | 0–100 of the world's resource (mana unless the world renames it). |
| `damage` | enemy abilities | 1–200, plus Intellect and spell power bonuses, rolled like other spells. |
| `heal` | self, optional | 1–200, plus Intellect and healing bonuses. |
| `effects` | no | Up to four effects. Enemy abilities apply harmful effects (damage or a penalty); self abilities apply helpful ones (regen or a bonus). |

A self ability needs `heal`, `effects`, or both. Built-in abilities are
`ember`, `venom`, `rend`, `hex` (enemy) and `mend`, `ward`, `might` (self).

## Granting abilities

A race or class lists ability keys in `spells` (up to 12). Characters know the
abilities of both. In a world package or theme, use the package's own local
keys; StationNet renames them on import. Abilities are unavailable in worlds
that turn their resource off.

## Removal

An ability a race or class still grants, or an effect an ability, item, NPC, or
trigger still applies, cannot be removed until those are updated. An effect
still active on someone, or applied by an item a player may still hold, is kept
for them but hidden from new use.
