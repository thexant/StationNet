// Suggestions are derived from the latest server snapshot. The server still validates every command.
const PORTAL = "stationnet:portal";
const DEATH = "stationnet:death-summary";

function targetName(item) {
  const label = item.display_name || item.name || "";
  // The server accepts both "Rat (2)" and "Rat 2"; the latter is easier to type.
  return label.replace(/ \((\d+)\)$/, " $1");
}

function quoteName(name) {
  return `"${name.replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`;
}

function available(command, state) {
  const room = state.room || {};
  const enemies = (state.combat?.enemies || []).filter(enemy => enemy.status !== "defeated");
  const portal = room.id === PORTAL;
  const dungeon = room.mode === "procedural" && !portal && room.id !== DEATH;
  switch (command.key) {
    case "expedition": case "daily": case "join_expedition":
    case "resume_expedition": case "shop": return portal;
    case "storage": return portal || (room.storage || []).length > 0;
    case "abandon_expedition": case "complete_expedition":
    case "expedition_status": return dungeon;
    case "continue": return room.id === DEATH;
    case "go": case "exits": return (room.exits || []).length > 0;
    case "travel": return Boolean(room.wilderness || room.travel);
    case "stop": return Boolean(room.travel);
    case "attack": case "fire": case "melee": case "hide":
    case "defend": case "flee": return enemies.length > 0;
    case "talk": return (room.npcs || []).some(npc => !npc.hostile);
    case "get": return (room.items || []).length > 0 || (room.items || []).some(item => item.contents?.length);
    case "drop": case "equip": case "use": return (state.inventory?.inventory || []).length > 0;
    case "unequip": return (state.inventory?.equipment || []).length > 0;
    case "board": return !room.vehicle && (room.vehicles || []).length > 0;
    case "disembark": return Boolean(room.vehicle);
    case "refuel": return Boolean(room.vehicle && room.vehicle.fuel_capacity > 0
      && room.vehicle.fuel < room.vehicle.fuel_capacity && room.vehicle.role === "operator");
    case "ride": return (room.transit || []).length > 0 || Boolean(room.trip);
    case "vehicles": return Boolean(room.vehicle || (room.vehicles || []).length);
    default: return true;
  }
}

export function commandSuggestions(value, state, catalog, limit = 6) {
  const query = value.trimStart().toLocaleLowerCase();
  if (!query.startsWith("/") || query === "/") return [];
  const room = state.room || {};
  const inventory = state.inventory || {};
  const candidates = [];
  const add = (command, detail) => {
    if (command.length <= 500 && !candidates.some(item => item.command.toLocaleLowerCase() === command.toLocaleLowerCase())) {
      candidates.push({command, detail});
    }
  };
  const targetCommands = ["attack", "fire", "melee"];
  for (const verb of targetCommands) {
    for (const npc of state.combat?.enemies || []) {
      if (npc.status !== "defeated") add(`/${verb} ${targetName(npc)}`, "Nearby enemy");
    }
  }
  for (const npc of room.npcs || []) {
    if (!npc.hostile) add(`/talk ${targetName(npc)}`, "Nearby NPC");
    add(`/look ${targetName(npc)}`, "Inspect NPC");
  }
  for (const response of room.dialogue_responses || []) {
    add(`/respond ${response.number}`, `Dialogue: ${response.text}`);
  }
  for (const player of room.occupants || []) {
    const name = quoteName(targetName({name:player.character_name,display_name:player.display_name}));
    add(`/look ${name}`, "Inspect player");
    add(`/tell ${name} `, "Private message");
  }
  for (const item of room.items || []) {
    add(`/get ${targetName(item)}`, "Item in room");
    add(`/look ${targetName(item)}`, "Inspect item");
    if (item.item_type === "container") {
      add(`/${item.is_open ? "close" : "open"} ${targetName(item)}`, "Container");
      for (const child of item.contents || []) add(`/get ${targetName(child)} from ${targetName(item)}`, "Container contents");
    }
  }
  if (room.wayfinding) {
    if (room.wilderness && room.wayfinding.direction && (room.exits || []).some(exit => exit.direction === room.wayfinding.direction))
      add(`/travel ${room.wayfinding.direction}`, `Toward ${room.wayfinding.destination}`);
    add("/directions", `Check the way to ${room.wayfinding.destination}`);
  }
  for (const exit of room.exits || []) {
    const direction = exit.direction || exit.name;
    if (direction) add(`/go ${direction}`, "Room exit");
    if (room.wilderness && exit.direction) add(`/travel ${exit.direction}`, "Travel onward");
    if (direction) add(`/look ${direction}`, "Inspect exit");
  }
  for (const item of inventory.inventory || []) {
    const name = targetName(item);
    for (const [verb, detail] of [["equip", "Carried item"], ["use", "Carried item"], ["drop", "Carried item"]]) add(`/${verb} ${name}`, detail);
    if (item.item_type === "container") add(`/${item.is_open ? "close" : "open"} ${name}`, "Carried container");
    for (const container of [...(room.items || []), ...(inventory.inventory || [])]) {
      if (container.item_type === "container" && container.is_open && container.id !== item.id)
        add(`/put ${name} in ${targetName(container)}`, "Open container");
    }
  }
  for (const item of inventory.equipment || []) add(`/unequip ${targetName(item)}`, "Equipped item");
  if (room.id === PORTAL) {
    add("/expeditions", "List expedition themes");
    for (const item of state.hub?.shop || []) add(`/shop buy ${item.name}`, "Portal trader");
    for (const item of inventory.inventory || []) {
      add(`/shop sell ${targetName(item)}`, "Portal trader");
      add(`/storage deposit ${targetName(item)}`, "Personal storage");
    }
    for (const item of state.hub?.storage || []) add(`/storage withdraw ${item.name}`, "Personal storage");
  }
  for (const vehicle of room.vehicles || []) add(`/board ${targetName(vehicle)}`, vehicle.yours ? "Your vehicle" : "Vehicle here");
  for (const offer of room.transit || []) add(`/ride ${offer.stop}`, `${offer.line}, ${offer.fare} coin${offer.fare === 1 ? "" : "s"}`);
  if (room.vehicle) {
    add("/disembark", `Get off the ${room.vehicle.name}`);
    if (room.vehicle.interior) add("/look out", `Outside: ${room.vehicle.outside}`);
  }
  const points = room.id === PORTAL ? [] : room.storage || [];
  for (const point of points) {
    // Name the storage only when the room has more than one.
    const into = points.length > 1 ? ` into ${quoteName(point.name)}` : "";
    for (const item of inventory.inventory || []) add(`/storage deposit ${targetName(item)}${into}`, point.name);
  }
  for (const entry of catalog || []) {
    if (available(entry, state)) add(`/${entry.name}`, entry.description);
  }
  return candidates.filter(item => item.command.toLocaleLowerCase().startsWith(query) && item.command.toLocaleLowerCase() !== query).slice(0, limit);
}

export function historyStep(history, index, draft, direction) {
  if (!history.length) return {index, value: draft};
  const next = Math.max(-1, Math.min(history.length - 1, index + direction));
  return {index: next, value: next === -1 ? draft : history[history.length - 1 - next]};
}
