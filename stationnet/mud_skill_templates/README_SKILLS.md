# MUD Skills

Skills give characters combat, weapon and armor proficiency, magic, stealth,
exploration, and merchant bonuses. They are plain JSON, so SYSOPs and world
creators can add skills without changing Python code. Every accepted effect
changes a real game rule; definitions that name an unknown effect or trigger
are rejected when you import them.

Players:

- choose starting skills during character creation (two by default);
- are granted skills by their race and class;
- improve skills by using them;
- earn **skill points** as they level, and spend them with `/learn <skill>`
  (a new skill at rank 1) or `/practice <skill>` (one more rank);
- can pay **trainer NPCs** in coins with `/learn <skill> from <trainer>` and
  `/practice <skill> from <trainer>`.

`/skills` lists ranks, progress, current effects, skill points, and learnable
skills. `/skills <name>` shows one skill in detail.

## Installing skills

**SYSOP CONTROL → Station settings → MUD service → Skills** has three parts:

- **Availability.** A checkbox for every default and imported skill, with
  SELECT ALL / NONE / DEFAULT / IMPORTED shortcuts.
- **Import.** Import a single skill file (`skill.json`, at most 32 KB) or an
  atomic pack (`pack.json`, `{"skills": [...]}`, at most 1 MB and 100 skills).
  A pack is rejected as a whole if any skill is invalid. Skills inside one
  pack may require each other.
- **Removal.** Bulk removal of imported skills, with a summary of what each
  removal will do.

Skills can also travel with content. World ZIPs may include `skills.json` (see
the [world guide](../mud_world_templates/README_CUSTOM_WORLDS.md)), and dungeon
themes may include a top-level `skills` array (see the
[dungeon guide](../dungeon_templates/README_DUNGEON_THEMES.md)). A world's
bundled skills are offered in that world according to its import choice; no
other world offers them until a SYSOP adds them.

### Per-world availability and removal

Installed skills are shared by the whole station, and each world chooses which
ones it offers on its **RACES, CLASSES & SKILLS** page in SYSOP Settings.
Standalone imports are offered in the worlds ticked on the import form (by
default, every enabled world).

- **Skills a world does not offer go dormant there.** Characters in that world
  keep their ranks and progress, but the skill gives no effect and does not
  practice. It is shown as inactive. Skill points spent on it are returned
  while it is not offered. Offering it again restores everything. Other worlds
  are unaffected.
- **Default skills** can be withdrawn from a world but never removed.
- **Blocked removals.** An imported skill cannot be removed while an installed
  race or class names it (as `skill` or in `skills`), or while another installed
  skill requires it. Remove those together, or update them first.
- **Removal reaches every world.** A removed skill leaves every world's
  selection.
- **Skills characters still hold** are kept as removed-but-reserved definitions
  (including skills in archived campaigns), so ranks stay readable and the key
  cannot be reused for a different skill.
- **Trainer lessons** for a removed skill are deleted.
- **Minimum offered.** Every world offers at least `starting_skill_choices`
  skills with `"starting": true`. If a removal would leave a world short, that
  world offers the default starting skills again.

## Example

```json
{
  "key": "spear-fighting",
  "name": "Spear Fighting",
  "group": "weapon",
  "description": "Reach and footwork with spears and polearms.",
  "starting": true,
  "max_rank": 20,
  "effects": [{"type": "melee_damage", "per_rank": 1, "every": 2, "max": 8, "weapon_category": "spear"}],
  "proficiencies": {"weapons": ["spear"], "armor": []},
  "practice": {"triggers": ["melee_hit"], "rate": 1},
  "learn": {"cost": 1, "trainer_required": false},
  "requirements": {"level": 1, "attributes": {"strength": 10}, "races": [], "classes": [], "skills": {}}
}
```

Only `key`, `name`, `description`, and at least one effect or proficiency are
required. Everything else has a default.

## Fields

| Field | Default | Rules |
| --- | --- | --- |
| `key` | required | 2–40 lowercase letters, digits, or hyphens, starting with a letter. Unique, including removed skills still held by characters. |
| `name` | required | 2–80 characters; unique among installed skills. |
| `description` | required | 1–1,000 characters. Shown in `/skills` and character creation. |
| `group` | `combat` | One of `combat`, `weapon`, `armor`, `magic`, `stealth`, `exploration`, `social`, `knowledge`. Used for grouping. |
| `starting` | `true` | Whether players may pick it during character creation. Skills with `false` can still be learned later. |
| `max_rank` | `20` | 1–20. |
| `effects` | `[]` | Up to eight effects (below). |
| `proficiencies` | none | `{"weapons": [...], "armor": [...]}` item categories this skill makes the character proficient with. |
| `practice` | none | `{"triggers": [...], "rate": 1}`. `rate` (1–10) is the practice gained per use. With no triggers, the skill only grows with points or trainers. |
| `learn` | `{"cost": 1, "trainer_required": false}` | `cost` is 0–10 skill points. If `trainer_required` is true, `/learn` only works at a trainer NPC that teaches the skill. |
| `requirements` | none | `level` (1–50), `attributes` (minimums 1–30), `races` and `classes` (lists of allowed keys; empty means any), `skills` (`{"key": rank}`). |

### Effects

Each effect is `{"type": ..., "per_rank": 1, "every": 1, "max": ...}`. The
bonus at rank *r* is `(r // every) × per_rank`, capped at `max` if given.
`per_rank` is 1–100, `every` is 1–20, and `max` is 1–1,000.

| Type | What it changes |
| --- | --- |
| `attack` | Melee and firearm damage. |
| `melee_damage` | Melee damage only. Optional `weapon_category` limits it to an equipped weapon of that category. |
| `firearm_damage` | Firearm damage only. Optional `weapon_category`. |
| `spell_power` | Damage from offensive spells. |
| `healing` | Health restored by healing spells. |
| `defense` | Mitigation against enemy hits. Optional `armor_category` limits it to equipped gear of that category. |
| `accuracy` | Added to the chance to hit with melee, firearms, and spells. Optional `weapon_category` applies it only while a weapon of that category is equipped. |
| `evasion` | Subtracted from enemies' chance to hit you. Evasion above 20 counts half, and enemies always keep at least a 30% chance. |
| `experience_percent` | Percentage bonus to experience earned. |
| `stealth` | Percentage points added to hiding and sneaking past enemies (total chance at most 95%). |
| `flee` | Percentage points added to escape chance (total at most 95%). |
| `search` | Score for `/search` against discoveries with a search difficulty. |
| `lockpick` | Score for `/pick` against exits with a lock difficulty. |
| `gathering` | Percentage points added to gathering success chance, capped at 100%. |
| `shop_discount` | Percentage off merchant prices; all sources together are capped at 50%. |

Search and lockpicking use one formula: chance = 50% + skill score +
2 × (Intellect or Agility − 10) − difficulty, kept between 5% and 95%.
Builders set difficulties in the World Builder.

### Practice triggers

| Trigger | Fires when |
| --- | --- |
| `melee_hit` | The character lands a melee attack (misses and dodged attacks do not count). |
| `firearm_hit` | The character lands a firearm shot. |
| `spell_cast` | The character lands an offensive spell. |
| `dodge` | The character's evasion turns an enemy attack aside. |
| `heal_cast` | The character casts a healing spell. |
| `damage_taken` | An enemy hit actually deals damage (reloads and fully blocked hits do not count). |
| `defend` | The character braces with `/defend`. |
| `hide` | The character attempts `/hide`. |
| `flee` | The character attempts `/flee`. |
| `search` | The character searches a room that still hides a discovery with a difficulty. |
| `pick_lock` | The character attempts `/pick`. |
| `experience_gained` | The character earns experience: one practice per 25 XP, minimum one. |
| `purchase` | The character buys from a merchant. |
| `gather` | The character successfully gathers from a replenishing room source that requires this skill. |
| `work` | The character completes an authored work activity. An activity's selected skill practices on payment even if it does not list this trigger. A skill used only for work may omit effects and proficiencies when it lists `work`. |

A skill with `proficiencies` practices `melee_hit`, `firearm_hit`,
`damage_taken`, and `defend` only while the character has matching gear
equipped. For example, Blades improves only when fighting with a blade.

## Progression

- **Rank cost.** Advancing from rank *r* costs `6 × r` practice, so rank 10
  takes about 270 uses.
- **Soft cap.** A skill can be at most 4 ranks above the character's highest
  level, and never above `max_rank`. At the cap, practice stops one point
  short of the next rank and the player is told which level unlocks it.
- **Feedback.** Rank-ups are announced as they happen.
- **Skill points.** A character earns one point at every even level and a
  bonus point at every fifth level, based on their highest level across
  progression tracks. Existing characters receive points for levels they
  already have.
  - `/learn` spends the skill's `cost`.
  - `/practice <skill>` spends one point per rank, up to the soft cap.
  - Trainer lessons cost the trainer's price in coins, multiplied by the
    current rank, up to the lesson's maximum rank.

## Weapon and armor proficiency

Items can set `properties_json.category` (for example `"blade"`, `"axe"`,
`"heavy"`, `"shield"`, `"firearm"`) in the World Builder, world content, or a
class's starter equipment.

- **Untrained penalty.** When a character equips categorized gear without a
  skill whose `proficiencies` lists that category, the item gives three
  quarters of its attack and defense, and the player is told why.
- **Uncategorized gear** (all content created before skills existed) is never
  penalized.

The default skills cover these categories:

| Skill | Categories |
| --- | --- |
| Blades | `blade` |
| Heavy Weapons | `blunt`, `axe` |
| Marksmanship | `firearm` |
| Light Armor | `light` |
| Heavy Armor | `heavy`, `shield` |

## Races and classes

Races and classes may grant skills with `"skills": {"perception": 1}` (ranks
1–5). Granted skills are added at creation, don't count toward the starting
choices, and are added (never lowered) when a character replaces a removed
race or class. A class's favored `skill` starts at rank 2 if the player picks
it.

Races and classes also interact with skills in three other ways:

- `skill_affinities` speeds up or slows down practice for named skills or
  whole skill groups, for example `{"magic": 50, "stealth": -25}`.
- `proficiencies` grants gear categories directly, so a class can be trained
  with `heavy` armor without holding the Heavy Armor skill.
- `progression.skill_points` adds skill points on top of those earned from
  levels.

Removing a skill that an installed race or class names in `skills`, `skill`,
or `skill_affinities` is refused until that definition is updated. To give a
race or class a bonus that scales with rank or depends on equipped gear,
define a custom skill and grant it. See the
[race and class guide](../mud_origin_templates/README_RACES_CLASSES.md).

## Compatibility

- **Older skill files.** Character-option files using the earlier
  `{"effect": "attack", "per_rank": 1}` skill form still load; they are
  converted to the new `effects` list.
- **Existing characters.** Characters keep every rank they had.
- **Behavior change.** Arms now affects melee only; Marksmanship replaces its
  old firearm bonus.
