export const MIN = 60000;
export const HOUR = 60 * MIN;
export const DAY = 24 * HOUR;
export const LIFESPAN = 14 * DAY;
export const INCUBATION = 2 * MIN;

export const SPECIES = [
  { id: "blorble", name: "Blorble", color: "#77e5d3", shade: "#267c84",
    description: "A bubbly, affectionate antenna-ball.", trait: "Loves company",
    care: "Needs company a little more often.", social: 1.25, fun: 1, hunger: 1, clean: 1, energy: 1 },
  { id: "quibble", name: "Quibble", color: "#ffd577", shade: "#bd754b",
    description: "A square little creature bristling with curiosity.", trait: "Playful explorer",
    care: "Gets bored a little sooner.", social: 1, fun: 1.25, hunger: 1, clean: 1, energy: 1 },
  { id: "mimsy", name: "Mimsy", color: "#f5a2de", shade: "#9452aa",
    description: "A three-lobed, easily amused oddity.", trait: "Cheerful spirit",
    care: "Stays amused longer, but gets hungry sooner.", social: 1, fun: .8, hunger: 1.1, clean: 1, energy: 1 },
  { id: "zonkly", name: "Zonkly", color: "#a9bdff", shade: "#5b65b4",
    description: "A spiky, one-eyed little whirlwind.", trait: "Always hungry",
    care: "Needs food more often, but tires more slowly.", social: 1, fun: 1, hunger: 1.3, clean: 1, energy: .9 },
  { id: "poffle", name: "Poffle", color: "#a8e779", shade: "#678b41",
    description: "A floppy-fringed, sleepy puff-creature.", trait: "Sleepy and tidy",
    care: "Stays clean longer and uses less energy.", social: 1, fun: 1, hunger: 1, clean: .7, energy: .75 }
];

export const byId = id => SPECIES.find(species => species.id === id) || SPECIES[0];
export const clamp = (n, lo = 0, hi = 100) => Math.max(lo, Math.min(hi, n));
export const round = n => Math.round(clamp(n));
export function mood(p) {
  return clamp(p.hunger * .20 + p.clean * .14 + p.energy * .15 +
    p.fun * .20 + p.social * .16 + p.health * .15);
}
export function stage(p, t = Date.now()) {
  if (t < p.hatchAt) return "Egg";
  const age = t - p.createdAt;
  if (age < DAY) return "Baby";
  if (age < 4 * DAY) return "Child";
  if (age < 11 * DAY) return "Adult";
  return "Elder";
}

export function advancePet(p, now, onDeath) {
  now = Math.max(p.lastUpdate, now);
  const end = Math.min(now, p.createdAt + LIFESPAN);
  const species = byId(p.species);
  let t = p.lastUpdate;
  let changed = false;
  while (t < end) {
    let n = Math.min(t + 5 * MIN, end);
    if (t < p.hatchAt) n = Math.min(n, p.hatchAt);
    if (n <= t) break;
    const dt = (n - t) / HOUR;
    if (t >= p.hatchAt) {
      const lifeStage = stage(p, t);
      const mult = lifeStage === "Baby" ? 1.18 : lifeStage === "Elder" ? .9 : 1;
      p.hunger = clamp(p.hunger - 2.7 * species.hunger * mult * dt);
      p.clean = clamp(p.clean - (p.sleeping ? .85 : 1.7) * species.clean * dt);
      p.fun = clamp(p.fun - (p.sleeping ? 1.0 : 2.25) * species.fun * dt);
      p.social = clamp(p.social - 1.65 * species.social * dt);
      p.energy = clamp(p.energy + (p.sleeping ? 13 : -4.8 * species.energy) * dt);
      if (p.energy <= 12) p.sleeping = true;
      if (p.energy >= 98) p.sleeping = false;
      const neglected = [p.hunger, p.clean, p.fun, p.social].filter(value => value < 18).length;
      p.health = clamp(p.health + (neglected ? -1.3 * neglected : .40) * dt);
      p.happyWeighted += mood(p) * (n - t);
      p.livedMs += n - t;
      if (p.health <= 0) {
        p.lastUpdate = n;
        onDeath("neglect", n);
        return true;
      }
    }
    t = n;
    changed = true;
  }
  p.lastUpdate = end;
  if (now >= p.createdAt + LIFESPAN) {
    onDeath("old age", p.createdAt + LIFESPAN);
    return true;
  }
  return changed;
}

const finite = (value, fallback, min = 0, max = 100) =>
  Number.isFinite(Number(value)) ? clamp(Number(value), min, max) : fallback;
const shortName = (value, fallback) => Array.from(String(value || fallback).trim()).slice(0, 20).join("") || fallback;
const entry = value => ({ at: finite(value?.at, 0, 0, Number.MAX_SAFE_INTEGER),
  text: String(value?.text || "").slice(0, 180) });
const journal = value => Array.isArray(value) ? value.slice(-32).map(entry).filter(item => item.at && item.text) : [];
const plant = value => value && ["moonfern", "sunbud", "starvine"].includes(value.kind)
  ? { kind: value.kind, growth: Math.trunc(finite(value.growth, 0, 0, 4)) } : null;

export function freshState() {
  return { version: 2, pet: null, memorials: [], settings: { sound: 65, reducedMotion: false } };
}

export function normalizeSave(value) {
  if (value == null) return freshState();
  if (!value || typeof value !== "object" || ![1, 2].includes(value.version) || !Array.isArray(value.memorials))
    throw Error("This save could not be read safely.");
  const state = freshState();
  state.settings.sound = finite(value.settings?.sound, 65);
  state.settings.reducedMotion = !!value.settings?.reducedMotion;
  state.memorials = value.memorials.map(item => {
    if (!item || typeof item !== "object") throw Error("A memorial entry is damaged.");
    const created = finite(item.created, Date.now(), 0, Number.MAX_SAFE_INTEGER);
    return { ...item, name: shortName(item.name, "Oddling"),
      created, passed: finite(item.passed, created, created, Number.MAX_SAFE_INTEGER),
      happiness: finite(item.happiness, 0), bestGame: finite(item.bestGame, 0, 0, Number.MAX_SAFE_INTEGER),
      careCount: finite(item.careCount, 0, 0, Number.MAX_SAFE_INTEGER),
      cause: item.cause === "neglect" ? "neglect" : "old age",
      lastStage: ["Egg", "Baby", "Child", "Adult", "Elder"].includes(item.lastStage) ? item.lastStage : "Elder",
      reason: String(item.reason || "").slice(0, 100),
      species: byId(item.species).id, journal: journal(item.journal),
      memory: String(item.memory || "").slice(0, 140), plant: plant(item.plant) };
  });
  if (value.pet != null) {
    const p = value.pet;
    if (!p || typeof p !== "object" || ![p.createdAt, p.hatchAt, p.lastUpdate].every(Number.isFinite) ||
        p.createdAt <= 0 || p.hatchAt < p.createdAt || p.lastUpdate < p.createdAt)
      throw Error("The active pet's timeline is damaged.");
    state.pet = { ...p, name: shortName(p.name, byId(p.species).name),
      species: byId(p.species).id, sleeping: !!p.sleeping,
      journal: journal(p.journal), memory: String(p.memory || "").slice(0, 140),
      plant: plant(p.plant), milestones: p.milestones && typeof p.milestones === "object" ? p.milestones : {} };
    for (const [key, fallback] of Object.entries({ hunger: 90, clean: 95, energy: 90,
      fun: 85, social: 85, health: 100 })) state.pet[key] = finite(p[key], fallback);
    for (const key of ["happyWeighted", "livedMs", "bestGame", "careCount", "warmth"])
      state.pet[key] = finite(p[key], 0, 0, Number.MAX_SAFE_INTEGER);
    if (p.death) {
      if (!Number.isFinite(p.death.at) || !["old age", "neglect"].includes(p.death.cause))
        throw Error("The active pet's memorial is damaged.");
      state.pet.death = { cause: p.death.cause, at: p.death.at,
        reason: String(p.death.reason || "").slice(0, 100) };
    }
  }
  return state;
}
