/* WIRESTORM RALLY v1.7.0 — canvas garage, race modes, saved records, ghosts and a multi-pointer touch dashboard. */
StationNet.start(async (ui) => {
  "use strict";

  const W = 1000, H = 560, TAU = Math.PI * 2;
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const mix = (a, b, t) => a + (b - a) * t;
  const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
  const mod = (n, m) => ((n % m) + m) % m;
  const rand = (seed) => { let x = seed | 0; return () => { x ^= x << 13; x ^= x >>> 17; x ^= x << 5; return (x >>> 0) / 4294967296; }; };
  const moneyTime = (t) => `${Math.floor(t / 60).toString().padStart(2, "0")}:${(t % 60).toFixed(2).padStart(5, "0")}`;
  const C = {
    cyan: "#6af8ff", blue: "#438efa", green: "#7aff9c", amber: "#ffcc74",
    red: "#ff6172", white: "#edfaff", dim: "#32616d", faint: "#16333e",
    violet: "#c19aff", yellow: "#eaff70"
  };

  // Vehicle affinity indexes: [RIDGE, WASH, HAUL]. Off-road traits affect hazards too.
  const VEHICLES = [
    { name: "MOTORBIKE", tag: "Shortcut specialist", top: 61, acc: 12.0, turn: 1.32, weight: .45, grip: .94, width: .85, length: 2.1, height: 1.5, color: C.cyan, affin: [1.27, .76, .79], mud: .15, suspension: .84, clearance: .35, stability: .57 },
    { name: "ATV", tag: "Light all-terrain", top: 53, acc: 10.7, turn: 1.17, weight: .84, grip: 1.10, width: 1.55, length: 2.4, height: 1.5, color: C.green, affin: [1.15, 1.14, .88], mud: .78, suspension: 1.12, clearance: .86, stability: .78 },
    { name: "BUGGY", tag: "Ruts and jumps", top: 59, acc: 9.6, turn: 1.07, weight: 1.35, grip: 1.16, width: 2.15, length: 3.4, height: 1.8, color: C.amber, affin: [1.00, 1.23, 1.02], mud: .85, suspension: 1.40, clearance: 1.0, stability: 1.04 },
    { name: "RALLY CAR", tag: "Fast hardpack runner", top: 67, acc: 9.1, turn: 1.05, weight: 1.65, grip: 1.11, width: 2, length: 4.0, height: 1.5, color: C.violet, affin: [1.10, .86, 1.12], mud: .30, suspension: .78, clearance: .32, stability: .98 },
    { name: "BIG RIG", tag: "Roadgoing heavyweight", top: 47, acc: 6.4, turn: .60, weight: 4.55, grip: .96, width: 3.10, length: 6.5, height: 3.2, color: C.red, affin: [.64, .83, 1.31], mud: .65, suspension: .65, clearance: .75, stability: 1.48 },
    { name: "RACING TRUCK", tag: "High-speed bruiser", top: 62, acc: 8.8, turn: .84, weight: 3.05, grip: 1.07, width: 2.7, length: 5.5, height: 2.7, color: C.blue, affin: [.83, 1.04, 1.29], mud: .76, suspension: 1.16, clearance: 1.02, stability: 1.36 },
    { name: "MUDPLUGGER", tag: "Deep mud specialist", top: 50, acc: 7.5, turn: .78, weight: 2.85, grip: 1.13, width: 2.7, length: 4.5, height: 2.8, color: C.yellow, affin: [.81, 1.39, .99], mud: 1.80, suspension: 1.30, clearance: 1.62, stability: 1.33 },
    { name: "MONSTER TRUCK", tag: "Huge tires and jumps", top: 53, acc: 8.0, turn: .75, weight: 4.8, grip: 1.02, width: 3.6, length: 4.9, height: 4.1, color: "#ff82d1", affin: [.75, 1.25, 1.12], mud: 1.51, suspension: 1.92, clearance: 2.10, stability: 1.62 }
  ];
  // Every track has three sectors with changing hazards [ridge, wash, haul].
  const TRACKS = [
    { name: "DUSTBOWL CIRCUIT", length: 2970, laps: 3, seed: 185, palette: ["#143c3b", "#245c57", "#347d70"], bend: .68, relief: 1.0, difficulty: "DESERT / DUNES / HARDPACK", zones: [["loose","sand","hard"],["rock","ruts","gravel"],["loose","mud","hard"]] },
    { name: "SCARP PASS", length: 3380, laps: 2, seed: 902, palette: ["#252a4d", "#405578", "#6886ab"], bend: 1.05, relief: 1.55, difficulty: "MOUNTAIN / LEDGES / ROCKFALL", zones: [["ledge","ruts","hard"],["loose","rock","gravel"],["ledge","scree","hard"]] },
    { name: "BLACKWATER RUN", length: 2790, laps: 3, seed: 477, palette: ["#122d35", "#245662", "#348b9a"], bend: .84, relief: 1.24, difficulty: "MARSH / WATER / DEEP BOG", zones: [["roots","water","mud"],["clear","bog","hard"],["rock","water","gravel"]] },
    { name: "CINDER GORGE", length: 3240, laps: 2, seed: 682, palette: ["#4c2024", "#783d2f", "#b16b40"], bend: 1.14, relief: 1.67, difficulty: "VOLCANIC ROCK / ASH / LEDGES", zones: [["rock","ash","hard"],["ledge","ruts","gravel"],["loose","ash","hard"]] },
    { name: "FROSTLINE", length: 3080, laps: 3, seed: 386, palette: ["#223c60", "#477b9e", "#84b8c7"], bend: 1.04, relief: 1.37, difficulty: "ICE / SNOW / SLIPPERY RIDGES", zones: [["ice","snow","hard"],["ledge","ice","snow"],["rock","snow","hard"]] },
    { name: "TIMBERLINE RUN", length: 3020, laps: 3, seed: 711, palette: ["#213e29", "#3b7650", "#61996c"], bend: .94, relief: 1.18, difficulty: "FOREST / ROOTS / WATER", zones: [["roots","mud","hard"],["loose","bog","gravel"],["roots","water","hard"]] },
    { name: "IRON QUARRY", length: 3560, laps: 2, seed: 143, palette: ["#3e3f49", "#667382", "#9ca0aa"], bend: .75, relief: 1.48, difficulty: "QUARRY / JUMPS / ROCK TRACKS", zones: [["rock","ruts","hard"],["ledge","mud","hard"],["loose","ruts","gravel"]] },
    { name: "SALTBLADE FLATS", length: 2670, laps: 3, seed: 834, palette: ["#333b52", "#527f99", "#96cad0"], bend: .53, relief: .75, difficulty: "SALT FLATS / SOFT SAND / FLOODS", zones: [["clear","sand","hard"],["clear","water","hard"],["rock","sand","gravel"]] },
    { name: "BADLANDS BLITZ", length: 3150, laps: 3, seed: 206, palette: ["#3c2226", "#8a4034", "#dc8051"], bend: 1.15, relief: 1.60, difficulty: "RED MESA / CANYONS / SCREE", zones: [["ledge","sand","hard"],["rock","ruts","gravel"],["scree","mud","hard"]] },
    { name: "TYPHOON COAST", length: 3440, laps: 2, seed: 658, palette: ["#153e51", "#2c7c98", "#65b9ae"], bend: .83, relief: .98, difficulty: "STORM COAST / TIDAL WATER / SAND", zones: [["roots","water","hard"],["loose","sand","gravel"],["ledge","bog","hard"]] },
    { name: "THUNDERHEAD RIDGE", length: 3220, laps: 3, seed: 924, palette: ["#28294b", "#555e84", "#bdacd7"], bend: 1.24, relief: 1.78, difficulty: "HIGHLANDS / CLIFFS / LIGHTNING", zones: [["ledge","ruts","hard"],["rock","mud","gravel"],["scree","water","hard"]] },
    { name: "TEMPLE RUINS", length: 2890, laps: 3, seed: 539, palette: ["#193b2b", "#417950", "#b3aa65"], bend: 1.12, relief: 1.26, difficulty: "JUNGLE / STONE RUINS / FLOODS", zones: [["roots","mud","hard"],["rock","bog","gravel"],["roots","water","hard"]] },
    { name: "NIGHTSHIFT YARD", length: 2730, laps: 3, seed: 310, palette: ["#24273d", "#47526c", "#a18ed2"], bend: .90, relief: 1.05, difficulty: "NIGHT INDUSTRIAL / GRAVEL / RAMPS", zones: [["clear","ruts","hard"],["rock","water","gravel"],["loose","ash","hard"]] },
    { name: "GLACIER DIVIDE", length: 3650, laps: 2, seed: 808, palette: ["#203956", "#5e98bc", "#c1e8ee"], bend: 1.21, relief: 1.75, difficulty: "ICE CAVES / CREVASSES / SNOW", zones: [["ice","snow","hard"],["ledge","ice","gravel"],["scree","snow","hard"]] }
  ];
  // Sky tint, weather effect and sun/moon per course (same order as TRACKS).
  const ATMOSPHERE = [
    { sky: ["#0d1f2c", "#1b3640", "#3a4a3c"], weather: "dust", sun: "#ffd27a" },
    { sky: ["#0a1023", "#172340", "#2c3c5c"], weather: null, sun: "#e8f0ff" },
    { sky: ["#071a1f", "#0f2a30", "#1c3c40"], weather: "rain", sun: null },
    { sky: ["#1a0708", "#38110f", "#5c2616"], weather: "ash", sun: "#ff7a3a" },
    { sky: ["#0c1a30", "#1d3656", "#4a6f8c"], weather: "snow", sun: "#f4fbff" },
    { sky: ["#081a10", "#12301e", "#25472e"], weather: null, sun: "#ffe9a6" },
    { sky: ["#101218", "#202632", "#3a404c"], weather: "dust", sun: "#ffcf9a" },
    { sky: ["#141a2e", "#2c3e5a", "#6f8ea0"], weather: null, sun: "#fff6d8" },
    { sky: ["#1a0a0c", "#3c1814", "#7a3622"], weather: "dust", sun: "#ffb35c" },
    { sky: ["#06141c", "#0e2632", "#1d4048"], weather: "rain", sun: null },
    { sky: ["#0a0a1c", "#1a1a38", "#34345a"], weather: "storm", sun: null },
    { sky: ["#071710", "#10301c", "#2a4a2a"], weather: "rain", sun: "#fff0b0" },
    { sky: ["#020308", "#060a18", "#10162a"], weather: "night", sun: "#dfe6ff" },
    { sky: ["#0a1626", "#1a3450", "#4d7a96"], weather: "snow", sun: "#f4fbff" }
  ];
  TRACKS.forEach((t, i) => { t.idx = i; t.slug = t.name.toLowerCase().replace(/[^a-z0-9]+/g, "-"); t.air = ATMOSPHERE[i]; });
  const MODES = ["QUICK RACE", "TIME TRIAL", "CHAMPIONSHIP"];
  const MODE_TAGS = ["Eight-car race on any circuit", "Solo run against your best ghost", "Four-round cups for points"];
  const DIFFICULTY = [
    { name: "ROOKIE", skill: .93, risk: -.14, boost: .80, rubber: true },
    { name: "PRO", skill: 1.00, risk: 0, boost: 1.00, rubber: false },
    { name: "ELITE", skill: 1.055, risk: .10, boost: 1.15, rubber: false }
  ];
  const CUPS = [
    { name: "DUST CUP", tracks: [0, 7, 12, 8] },
    { name: "STORM CUP", tracks: [5, 2, 11, 9] },
    { name: "ICE CUP", tracks: [4, 1, 13, 10] },
    { name: "IRON CUP", tracks: [6, 3, 8, 10] }
  ];
  const POINTS = [10, 8, 6, 5, 4, 3, 2, 1];
  // Filled road surface tint per hazard, drawn under the wireframe edges.
  const HAZARD_FILL = {
    clear: "#1e3038", hard: "#22303c", gravel: "#353438", loose: "#40382c", ledge: "#33304a",
    rock: "#43342e", scree: "#463e3a", roots: "#2a3620", ruts: "#40301e", sand: "#5e4e2c",
    mud: "#45291a", bog: "#2b3218", water: "#173f5a", ice: "#4f7f96", snow: "#66808f", ash: "#382a2a"
  };
  const KICKUP = { sand: "#c8a866", mud: "#7a5232", bog: "#5d6a32", water: "#7fd0ff", snow: "#e6f2ff",
    ash: "#8a7a78", loose: "#a89070", gravel: "#9a9a9a", scree: "#a09890" };
  const ROUTES = [
    { name: "RIDGE CUT", offset: -32, width: 7.5, color: C.cyan, distance: 1.145 },
    { name: "WASH LINE", offset: 0, width: 12.2, color: C.amber, distance: 1.04 },
    { name: "HAUL ROAD", offset: 32, width: 14.0, color: C.green, distance: .94 }
  ];
  // Hazards have vehicle-dependent penalties; hard surfaces favor fast/heavy machines.
  const HAZARDS = {
    clear: { grip: 1.05, speed: 1.04, wear: 0, trait: "stability" },
    hard: { grip: 1.14, speed: 1.04, wear: 0, trait: "stability" },
    gravel: { grip: 1.01, speed: .99, wear: 0, trait: "stability" },
    loose: { grip: .83, speed: .94, wear: .08, trait: "stability" },
    ledge: { grip: .91, speed: .95, wear: .10, trait: "suspension" },
    rock: { grip: .83, speed: .79, wear: .25, trait: "suspension" },
    scree: { grip: .76, speed: .78, wear: .26, trait: "suspension" },
    roots: { grip: .88, speed: .88, wear: .17, trait: "suspension" },
    ruts: { grip: .78, speed: .86, wear: .17, trait: "suspension" },
    sand: { grip: .82, speed: .78, wear: .06, trait: "mud" },
    mud: { grip: .77, speed: .66, wear: .13, trait: "mud" },
    bog: { grip: .68, speed: .54, wear: .25, trait: "mud" },
    water: { grip: .72, speed: .68, wear: .16, trait: "clearance" },
    ice: { grip: .55, speed: .94, wear: .10, trait: "stability" },
    snow: { grip: .75, speed: .73, wear: .12, trait: "clearance" },
    ash: { grip: .80, speed: .80, wear: .10, trait: "mud" }
  };
  const AI_DRIVERS = [
    { name: "VOLT", style: "RECKLESS", skill: 1.04, risk: 1.00, drift: .65, boost: .78, discipline: .68 },
    { name: "FOX", style: "SHORTCUT HUNTER", skill: 1.00, risk: .90, drift: .48, boost: .60, discipline: .94 },
    { name: "ANVIL", style: "ENFORCER", skill: .99, risk: .68, drift: .22, boost: .70, discipline: .80 },
    { name: "ECHO", style: "TECHNICIAN", skill: 1.08, risk: .40, drift: .15, boost: .42, discipline: 1.14 },
    { name: "DIRTKING", style: "MUD RUNNER", skill: .97, risk: .72, drift: .38, boost: .58, discipline: .95 },
    { name: "DRIFTER", style: "OPPORTUNIST", skill: .96, risk: .74, drift: .70, boost: .91, discipline: .83 },
    { name: "SANDWITCH", style: "CAUTIOUS", skill: .94, risk: .28, drift: .18, boost: .32, discipline: 1.14 }
  ];
  function sectorAt(s) { return Math.floor(mod(s, track.length) / (track.length / 3)); }
  function hazardAt(s, lane) { return track.zones[sectorAt(s)][lane]; }
  function widthAt(s, lane) {
    return ROUTES[lane].width * (1 - (lane === 0 ? .23 : lane === 1 ? .08 : .05) * (0.5 + 0.5 * Math.sin(s / 62 + lane * 2)));
  }
  function effectFor(h, stat) {
    const trait = stat[h.trait];
    const blend = clamp((trait - .5) * .28, -.20, .38);
    return { speed: clamp(h.speed + blend, .36, 1.23), grip: clamp(h.grip + blend * .52, .37, 1.30), wear: h.wear * clamp(1.3 - trait * .45, .40, 1.22) };
  }
  function routeScore(s, vehicle, lane, risk = .6) {
    const stat = VEHICLES[vehicle], h = HAZARDS[hazardAt(s, lane)], effect = effectFor(h, stat);
    let speed = stat.top * stat.affin[lane] * effect.speed * ROUTES[lane].distance;
    if (lane === 0) speed *= clamp(1.24 - stat.width * .115, .72, 1.20);
    if (lane === 2 && stat.weight > 2) speed *= 1.05;
    return speed * (1 - (1 - risk) * (h.wear * .18 + (lane === 0 ? .045 : 0)));
  }
  function bestRoute(s, vehicle, risk = .6) {
    let best = 0, score = -Infinity;
    for (let lane = 0; lane < 3; lane++) {
      const value = routeScore(s, vehicle, lane, risk);
      if (value > score) { best = lane; score = value; }
    }
    return best;
  }
  // Per-route rating for one sector: 2 = best line, 1 = within 8%, 0 = poor for this vehicle.
  function suitability(s, vehicle, risk = .7) {
    const scores = [0, 1, 2].map(lane => routeScore(s, vehicle, lane, risk));
    const top = Math.max(...scores);
    return scores.map(v => v >= top * .999 ? 2 : v >= top * .92 ? 1 : 0);
  }
  const RATING_COLOR = [C.red, C.amber, C.green];

  let trackIndex = 0, vehicleIndex = 2, track = TRACKS[0];
  let raceMode = 0, difficulty = 1, cupIndex = 0;
  let mode = "garage", pausedFrom = "race", muted = false, clock = 0, countdown = 0, countdownBeep = 0;
  let player = null, rivals = [], obstacles = [], ramps = [], keys = Object.create(null);
  // Native canvas touch dashboard uses independent pointerId streams, so holding
  // throttle + steering + boost works with multiple fingers. No HTML/touch gamepad overlay.
  // Physical gamepads still come from StationNet's ui.input.gamepad API.
  // CONTROLS: AUTO starts on the desktop layout and switches once a finger touches
  // either canvas (or the viewport is a narrow portrait phone).
  const CONTROL_MODES = ["AUTO", "KEYBOARD", "TOUCH"];
  let controlsMode = 0, sawTouch = false, canvasWidth = W, canvasHeight = H;
  let viewportWidth = W, viewportHeight = 800, viewportOrientation = "landscape";
  let cameraZoom = 1, pinchBase = null, lastInput = "keyboard";
  let drive = { throttle: 0, brake: 0, steer: 0, boost: false };
  const touchPointers = new Map(); // pointerId -> { kind, x, y }, even during simultaneous holds
  let fingerDrive = { steer: 0, gas: false, brake: false, boost: false };
  let touchPadW = 480, touchPadH = 255, controlRedraw = 0;
  let gamepadPrev = [], garageFocus = 1, garageNotice = "", garagePage = "main";
  let garageHits = [], hoverX = -1, hoverY = -1;
  function touchMode() {
    if (controlsMode === 1) return false;
    if (controlsMode === 2) return true;
    return sawTouch || (viewportOrientation === "portrait" && viewportWidth < 700);
  }
  function noteTouch() {
    lastInput = "touch";
    if (sawTouch) return;
    sawTouch = true;
    if (controlsMode === 0) relayout();
  }
  function clearFingerInput() {
    touchPointers.clear();
    fingerDrive = { steer: 0, gas: false, brake: false, boost: false };
    controlRedraw = 1;
  }
  function held(code) {
    return !!keys[code] || !!(ui.input && typeof ui.input.isDown === "function" && ui.input.isDown(code));
  }
  function samplePad() {
    if (!ui.input || typeof ui.input.gamepad !== "function") return { buttons: [], axes: [] };
    const pad = ui.input.gamepad(0, { deadzone: .12 });
    return pad && pad.buttons && pad.axes && typeof pad.buttons.length === "number" && typeof pad.axes.length === "number"
      ? pad : { buttons: [], axes: [] };
  }
  function sampleDrive(pad) {
    const buttons = pad.buttons, axes = pad.axes;
    const btn = (i) => clamp(Number(buttons[i]) || 0, 0, 1);
    const physicalStick = clamp(Number(axes[0]) || 0, -1, 1);
    const arrows = Number(held("ArrowRight")) - Number(held("ArrowLeft"));
    const keyboard = Number(held("KeyD")) - Number(held("KeyA"));
    const padDigital = Number(btn(15) > .5) - Number(btn(14) > .5);
    const padSteer = Math.abs(physicalStick) > .12 ? physicalStick : padDigital;
    drive = {
      // Pointer-held canvas buttons drive gas/brake/boost independently;
      // physical gamepad triggers retain proportional throttle/brake.
      throttle: Math.max(Number(held("KeyW") || held("ArrowUp") || fingerDrive.gas), btn(7), btn(12)),
      brake: Math.max(Number(held("KeyS") || held("ArrowDown") || fingerDrive.brake), btn(6), btn(13), btn(1)),
      steer: clamp(keyboard + arrows + padSteer + fingerDrive.steer, -1, 1),
      boost: held("Space") || fingerDrive.boost || btn(0) > .5 || btn(5) > .5
    };
  }
  function samplePadActions(pad) {
    const buttons = pad.buttons;
    const down = (i) => (Number(buttons[i]) || 0) >= .55;
    const just = (i) => down(i) && !(gamepadPrev[i] >= .55);
    if (Array.prototype.some.call(buttons, (_, i) => just(i)) ||
        Array.prototype.some.call(pad.axes, a => Math.abs(Number(a) || 0) > .5)) lastInput = "gamepad";
    if (mode === "garage") {
      if (garagePage !== "main") {
        if (just(1) || just(0) || just(9)) setGaragePage("main");
      } else {
        // Controller navigation: vertical d-pad picks a row, horizontal changes it.
        if (just(12)) moveGarageFocus(-1);
        if (just(13)) moveGarageFocus(1);
        if (just(14) || just(15)) changeGarageRow(garageFocus, just(15) ? 1 : -1);
        if (just(0) || just(9)) startRace();
        if (just(3)) toggleMusic();
        if (just(2)) setGaragePage("help");
      }
    } else if (mode === "finish") {
      if (just(9)) finishAction("primary");
      if (just(0) && finishActions().includes("NEXT")) finishAction("NEXT");
      if (just(8) || just(1)) backToGarage();
    } else {
      if (just(9)) pauseRace();
      if (just(8)) backToGarage();
      if (just(2) && player && (mode === "race" || mode === "paused")) resetRacer(player, true);
      if (just(3)) toggleMusic();
    }
    gamepadPrev = Array.from(buttons);
  }
  function updateControls() {
    const pad = samplePad();
    samplePadActions(pad);
    sampleDrive(pad);
  }
  // Physical buttons drawn in a *second canvas*, rather than StationNet's
  // conventional touchLayout. The 3D racing canvas therefore remains unobstructed,
  // and control targets can be substantially larger in phone portrait mode.
  // The nav row follows the race state, so touch players can always restart,
  // advance, mute or leave without a keyboard.
  function navItems() {
    if (mode === "finish") return [...finishActions(), "GARAGE", "FULL"];
    if (mode === "paused") return ["RESUME", "RESET", "MUSIC", "GARAGE", "FULL"];
    return ["PAUSE", "RESET", "SOUND", "GARAGE", "FULL"];
  }
  function navLabel(item) {
    if (item === "SOUND") return muted ? "SOUND OFF" : "SOUND ON";
    if (item === "MUSIC") return musicEnabled ? "MUSIC ON" : "MUSIC OFF";
    if (item === "NEXT") return raceMode === 2 ? (cupFinished() ? "CUP DONE" : "NEXT ROUND") : "NEXT TRACK";
    if (item === "FULL") return ui.presentation?.fullscreen ? "EXIT FULL" : "FULL";
    return item;
  }
  function navAction(item) {
    if (item === "PAUSE" || item === "RESUME") pauseRace();
    else if (item === "RESET" && player && (mode === "race" || mode === "paused")) resetRacer(player, true);
    else if (item === "SOUND") toggleMute();
    else if (item === "MUSIC") toggleMusic();
    else if (item === "GARAGE") backToGarage();
    else if (item === "FULL") fullscreenToggle();
    else if (item === "RESTART" || item === "NEXT") finishAction(item);
  }
  function touchPadGeometry() {
    const w = touchPadW, h = touchPadH, nav = viewportOrientation === "portrait" ? 52 : 42;
    const content = h - nav - 12;
    const items = navItems(), n = items.length;
    const boxes = { nav: items };
    items.forEach((item, i) => { boxes[item] = { x: i * w / n + 4, y: 3, w: w / n - 8, h: nav - 8 }; });
    boxes.STEER = { x: 8, y: nav + 5, w: w * .47 - 14, h: content - 1 };
    boxes.GAS = { x: w * .50, y: nav + 5, w: w * .245 - 5, h: content - 1 };
    boxes.BRAKE = { x: w * .76, y: nav + 5, w: w * .235 - 8, h: content * .47 - 3 };
    boxes.BOOST = { x: w * .76, y: nav + 5 + content * .51, w: w * .235 - 8, h: content * .49 - 3 };
    return boxes;
  }
  function inBox(x, y, r) { return r && x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h; }
  function fingerRegion(x, y, boxes) {
    for (const kind of ["STEER", "GAS", "BRAKE", "BOOST"]) {
      if (inBox(x, y, boxes[kind])) return kind;
    }
    return null;
  }
  function recomputeFingerDrive() {
    const next = { steer: 0, gas: false, brake: false, boost: false };
    const steer = touchPadGeometry().STEER;
    for (const p of touchPointers.values()) {
      if (p.kind === "STEER") {
        const center = steer.x + steer.w / 2;
        const value = clamp((p.x - center) / Math.max(1, steer.w * .42), -1, 1);
        if (Math.abs(value) > Math.abs(next.steer)) next.steer = Math.abs(value) < .07 ? 0 : value;
      }
      if (p.kind === "GAS") next.gas = true;
      if (p.kind === "BRAKE") next.brake = true;
      if (p.kind === "BOOST") next.boost = true;
    }
    fingerDrive = next;
    controlRedraw = 1;
  }
  function onTouchPadPointer(event) {
    if (!event || mode === "garage") return;
    if (event.pointerType === "touch") lastInput = "touch";
    const id = event.pointerId === undefined || event.pointerId === null ? "mouse" : String(event.pointerId);
    const x = Number(event.x), y = Number(event.y);
    if (event.phase === "end" || event.phase === "cancel") {
      touchPointers.delete(id);
      recomputeFingerDrive();
      return;
    }
    if (event.phase !== "start" && event.phase !== "move") return; // ignore synthesized tap/drag gestures
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    const boxes = touchPadGeometry();
    if (event.phase === "start") {
      for (const action of boxes.nav) {
        if (!inBox(x, y, boxes[action])) continue;
        navAction(action);
        controlRedraw = 1;
        return;
      }
      if (mode === "finish") return;
      const kind = fingerRegion(x, y, boxes);
      if (kind) touchPointers.set(id, { kind, x, y });
    } else if (touchPointers.has(id)) {
      // Hold the button assigned at touchdown, even if a thumb slides slightly
      // outside its bounds. Steering alone tracks the horizontal finger position.
      const p = touchPointers.get(id);
      p.x = x;
      p.y = y;
    }
    recomputeFingerDrive();
  }
  function touchControlCanvas() {
    const g = touchPadGeometry(), out = [
      { type: "rect", x: 0, y: 0, width: touchPadW, height: touchPadH, color: "#06151d" }
    ];
    const drawBox = (key, label, color, isDown) => {
      const b = g[key];
      out.push({ type: "rect", x: b.x, y: b.y, width: b.w, height: b.h,
        color: isDown ? "#235064" : "#102630" });
      out.push({ type: "lines", segments: [
        [b.x,b.y,b.x+b.w,b.y],[b.x+b.w,b.y,b.x+b.w,b.y+b.h],
        [b.x+b.w,b.y+b.h,b.x,b.y+b.h],[b.x,b.y+b.h,b.x,b.y]
      ], color: isDown ? C.white : color, width: isDown ? 2.6 : 1.2 });
      putText(out, b.x+b.w/2, b.y+b.h/2, label,
        clamp(Math.round(Math.min(b.w / 5.5, b.w * 1.55 / Math.max(1, label.length))), 10, key === "STEER" ? 19 : 21),
        isDown ? C.white : color, "center");
    };
    for (const action of g.nav) {
      const primary = action === "RESTART" || action === "NEXT" || action === "RESUME";
      drawBox(action, navLabel(action), action === "GARAGE" ? C.amber : primary ? C.green : C.dim, false);
    }
    const steerBox = g.STEER, centerX = steerBox.x + steerBox.w/2;
    const steerActive = [...touchPointers.values()].some(p => p.kind === "STEER");
    drawBox("STEER", "", C.cyan, steerActive);
    const centerY = steerBox.y + steerBox.h/2;
    const laneY = centerY + Math.min(25, steerBox.h*.23);
    out.push({ type: "line", x1: steerBox.x+12, y1: laneY,
      x2: steerBox.x+steerBox.w-12, y2: laneY, color: C.dim, width: 3 });
    out.push({ type: "rect", x: centerX + fingerDrive.steer * steerBox.w*.34 - 12, y: laneY - 9,
      width: 24, height: 18, color: steerActive ? C.white : C.cyan });
    putText(out, centerX, centerY - Math.min(19, steerBox.h*.20), "◀  STEER  ▶", 17, C.cyan, "center");
    drawBox("GAS", "GAS", C.green, fingerDrive.gas);
    drawBox("BRAKE", "BRAKE", C.red, fingerDrive.brake);
    drawBox("BOOST", "BOOST", player && player.boost <= 30 ? C.red : C.yellow, fingerDrive.boost);
    return ui.canvas(touchPadW, touchPadH, out, { id: "touchpad",
      label: "Multitouch steering, accelerator, brake, boost, pause and race controls",
      onPointer: onTouchPadPointer });
  }
  function canvasLayout() {
    touchPadW = viewportOrientation === "portrait" ? 480 : 900;
    touchPadH = viewportOrientation === "portrait" ? 255 : 155;
    if (!touchMode()) { canvasWidth = W; canvasHeight = H; return; }
    const physicalPadHeight = viewportWidth * touchPadH / touchPadW;
    const controlReserve = physicalPadHeight + 30;
    const scale = Math.min(1, Math.max(230, viewportWidth - 20) / W,
      Math.max(130, viewportHeight - controlReserve) / H);
    canvasWidth = Math.round(W * scale);
    canvasHeight = Math.round(H * scale);
  }
  function relayout() {
    canvasLayout();
    clearFingerInput();
    if (mode === "garage") renderGarageScreen();
    else renderRaceScreen();
  }
  function adjustViewport(view) {
    if (!view || !Number.isFinite(view.width) || !Number.isFinite(view.height)) return;
    const w = clamp(Math.floor(view.width), 240, 2500), h = clamp(Math.floor(view.height), 240, 2000);
    const oldCompact = touchMode(), oldW = canvasWidth, oldH = canvasHeight;
    const oldPadW = touchPadW, oldPadH = touchPadH;
    viewportWidth = w; viewportHeight = h;
    viewportOrientation = view.orientation === "portrait" || view.orientation === "landscape"
      ? view.orientation : (h > w ? "portrait" : "landscape");
    canvasLayout();
    // StationNet automatically lays out the on-screen controls when the device
    // rotates. Rebuild only when the game really needs different logical sizes.
    if (oldCompact !== touchMode() || oldW !== canvasWidth || oldH !== canvasHeight ||
        oldPadW !== touchPadW || oldPadH !== touchPadH) relayout();
  }
  function refreshViewport() {
    if (ui.display && typeof ui.display.viewport === "function") {
      Promise.resolve(ui.display.viewport()).then(adjustViewport).catch(() => {});
    }
  }
  function fullscreenToggle() {
    if (!ui.presentation || typeof ui.presentation.requestFullscreen !== "function") return;
    const action = ui.presentation.fullscreen && typeof ui.presentation.exitFullscreen === "function"
      ? ui.presentation.exitFullscreen() : ui.presentation.requestFullscreen();
    Promise.resolve(action).then(() => {
      refreshViewport();
    }).catch(error => {
      if (mode === "garage") garageNotice = `FULLSCREEN UNAVAILABLE: ${error?.message || "REQUEST CANCELLED"}`;
      else announce("FULLSCREEN UNAVAILABLE", 2);
    });
  }
  function immersive() { return ui.presentation?.mode === "immersive"; }
  function toggleImmersive() {
    const p = ui.presentation;
    if (!p || typeof p.enterImmersive !== "function") { garageNotice = "IMMERSIVE LAYOUT UNAVAILABLE"; return; }
    if (immersive()) p.exitImmersive(); else p.enterImmersive();
    tone(600, 60);
  }

  // ---- Garage selection ----
  // Rows: 0 MODE, 1 TRACK (or CUP), 2 VEHICLE, 3 DIFFICULTY.
  function cupRound() { return career.active && career.active.cup === cupIndex ? career.active.round : 0; }
  function cupUnlocked(i) { return i === 0 || !!career.trophies[CUPS[i - 1].name]; }
  function cupFinished() { return !!(cupResult && cupResult.done); }
  function selectedTrack() { return raceMode === 2 ? CUPS[cupIndex].tracks[cupRound()] : trackIndex; }
  function syncGarageTrack() { track = TRACKS[selectedTrack()]; }
  function moveGarageFocus(delta) { garageFocus = mod(garageFocus + delta, 4); tone(480, 40, "triangle", .05); }
  function changeGarageRow(row, delta) {
    if (mode !== "garage") return;
    garageFocus = row;
    if (row === 0) raceMode = mod(raceMode + delta, MODES.length);
    else if (row === 1) {
      if (raceMode === 2) cupIndex = mod(cupIndex + delta, CUPS.length);
      else trackIndex = mod(trackIndex + delta, TRACKS.length);
    } else if (row === 2) vehicleIndex = mod(vehicleIndex + delta, VEHICLES.length);
    else if (raceMode !== 1) difficulty = mod(difficulty + delta, DIFFICULTY.length);
    syncGarageTrack();
    garageNotice = "";
    tone(530 + row * 45, 65, "triangle", .065);
    saveSettings();
  }
  function setGaragePage(page) {
    garagePage = page;
    if (page === "records") fetchLeaderboard();
    tone(page === "main" ? 520 : 700, 55, "triangle", .06);
  }
  function garageAction(id) {
    const parts = id.split(":");
    if (parts[0] === "row") { changeGarageRow(Number(parts[1]), Number(parts[2])); return; }
    if (parts[0] === "rowarea") { garageFocus = Number(parts[1]); return; }
    if (id === "start") startRace();
    else if (id === "help" || id === "records") setGaragePage(id);
    else if (id === "back") setGaragePage("main");
    else if (id === "immersive") toggleImmersive();
    else if (id === "full") fullscreenToggle();
    else if (id === "difficulty") changeGarageRow(3, 1);
    else if (id === "controls") {
      controlsMode = mod(controlsMode + 1, CONTROL_MODES.length);
      tone(560, 60); saveSettings(); relayout();
    } else if (id === "sound") toggleMute();
    else if (id === "music") toggleMusic();
  }
  function hitAt(x, y) {
    for (let i = garageHits.length - 1; i >= 0; i--) if (inBox(x, y, garageHits[i])) return garageHits[i];
    return null;
  }
  // Garage hit boxes are recorded while drawing, in 1000x560 scene coordinates.
  // StationNet sends scaled canvas coordinates, so map them back to the scene.
  function onGaragePointer(event) {
    if (mode !== "garage" || !event) return;
    // Map to scene coordinates before a first touch can resize the canvas.
    const x = Number(event.x) * W / canvasWidth;
    const y = Number(event.y) * H / canvasHeight;
    if (event.pointerType === "touch" && event.phase === "start") noteTouch();
    else if (event.pointerType === "mouse" && event.phase === "start") lastInput = "keyboard";
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    if (event.phase === "move") { hoverX = x; hoverY = y; return; }
    if (event.phase === "end" || event.phase === "cancel") { if (event.pointerType !== "mouse") hoverX = hoverY = -1; return; }
    if (event.phase === "gesture" && event.gesture === "swipe") {
      const hit = hitAt(x, y);
      const row = hit && hit.id.startsWith("rowarea:") ? Number(hit.id.split(":")[1]) : -1;
      if (row >= 0 && (event.direction === "left" || event.direction === "right"))
        changeGarageRow(row, event.direction === "left" ? 1 : -1);
      return;
    }
    // Press-on-start makes mouse and finger taps react immediately, without a
    // second action when the host emits its higher-level tap gesture.
    if (event.phase !== "start") return;
    const hit = hitAt(x, y);
    if (hit) garageAction(hit.id);
  }
  function onRacePointer(event) {
    if (mode === "garage" || !event) return;
    if (event.pointerType === "touch" && event.phase === "start") noteTouch();
    if (event.phase === "gesture" && event.gesture === "pinch") {
      if (pinchBase === null) pinchBase = cameraZoom;
      if (Number.isFinite(event.scale) && event.scale > 0)
        cameraZoom = clamp(pinchBase / event.scale, .67, 1.7);
    }
    if ((event.phase === "end" || event.phase === "cancel") && event.pointers < 2) {
      if (pinchBase !== null) saveSettings();
      pinchBase = null;
    }
  }
  function scaleFrame(commands) {
    if (canvasWidth === W && canvasHeight === H) return commands;
    const sx = canvasWidth / W, sy = canvasHeight / H, sw = Math.min(sx, sy);
    return commands.map(c => {
      switch (c.type) {
        case "rect": return { ...c, x: c.x*sx, y: c.y*sy, width: c.width*sx, height: c.height*sy };
        case "circle": return { ...c, x: c.x*sx, y: c.y*sy, radius: c.radius*sw };
        case "line": return { ...c, x1:c.x1*sx, y1:c.y1*sy, x2:c.x2*sx, y2:c.y2*sy, width:(c.width || 1)*sw };
        case "lines": return { ...c, segments:c.segments.map(q => [q[0]*sx,q[1]*sy,q[2]*sx,q[3]*sy]), width:(c.width || 1)*sw };
        case "polygon": return { ...c, points:c.points.map(q => [q[0]*sx,q[1]*sy]), width:(c.width || 1)*sw };
        case "text": return { ...c, x:c.x*sx, y:c.y*sy, size:clamp(Math.round(c.size*sw), 8, 72) };
        default: return c;
      }
    });
  }
  // Compact HUD drawn at the real (scaled) canvas size so it stays legible on phones.
  function mobileHud(out) {
    if (!touchMode() || !player || mode === "finish") return;
    const w = canvasWidth, h = canvasHeight;
    const rank = racePosition();
    const lap = Math.min(track.laps, Math.floor(player.s / track.length) + 1);
    const low = player.boost <= 30 && !player.crashed;
    out.push({ type:"rect", x:0,y:0,width:w,height:30,color:"#06151d" });
    out.push({ type:"text", x:9,y:15,text: rivals.length ? `P${rank}/${rivals.length + 1}  L${lap}/${track.laps}` : `TRIAL  L${lap}/${track.laps}`,color:rank <= 3 ? C.green : C.white,size:13 });
    out.push({ type:"text", x:w-8,y:15,text:`${Math.round(player.v*3.6)} KM/H  ${moneyTime(player.time)}`,color:C.white,size:12,align:"right" });
    const gap = gapText();
    if (gap) out.push({ type:"text", x:9,y:41,text:gap.text,color:gap.color,size:10 });
    const chip = hazardPreview();
    if (chip) chip.forEach((c, i) => out.push({ type:"text", x:w*.30 + i*w*.16, y:41, text:`${c.short} ${c.hazard.toUpperCase()}`, size:9, color:RATING_COLOR[c.rating] }));
    drawRadar(out, w - 30, 58, 22);
    out.push({ type:"rect", x:0,y:h-40,width:w,height:40,color:"#06151d" });
    out.push({ type:"text", x:8,y:h-30,text:low ? "BOOST LOW" : "BOOST",color:low?C.red:C.cyan,size:10 });
    out.push({ type:"rect", x:8,y:h-20,width:(w*.52)-16,height:11,color:C.faint });
    out.push({ type:"rect", x:8,y:h-20,width:((w*.52)-16)*clamp(player.boost/100,0,1),height:11,color:low?(Math.floor(clock*6)%2?C.red:C.yellow):C.cyan });
    out.push({ type:"text", x:w*.54,y:h-30,text:"DAMAGE",color:C.red,size:10 });
    out.push({ type:"rect", x:w*.54,y:h-20,width:w*.43,height:11,color:"#411f2c" });
    out.push({ type:"rect", x:w*.54,y:h-20,width:w*.43*clamp(player.damage/100,0,1),height:11,color:C.red });
    if (messageTimer > 0 && mode === "race") out.push({ type:"text", x:w/2,y:h*.30,text:message,size:13,align:"center",color:C.yellow });
    else if (low && mode === "race") out.push({ type:"text", x:w/2,y:h*.30,text:"! BOOST CRITICAL !",size:12,align:"center",color:C.red });
    if (posFlash.timer > 0) out.push({ type:"text", x:w/2,y:h*.30+18,text:posFlash.text,size:14,align:"center",color:posFlash.up ? C.green : C.red });
  }
  function gameCanvas(commands) {
    const out = scaleFrame(commands);
    mobileHud(out);
    const options = { id: "race", label: mode === "garage" ? "Wirestorm Rally garage" : "Wirestorm Rally racing view" };
    if (mode === "garage") {
      options.onPointer = onGaragePointer;
      options.hover = !touchMode(); // desktop mouse highlights garage buttons
    } else {
      options.onPointer = onRacePointer; // pinch-to-zoom; no overlay buttons
    }
    return ui.canvas(canvasWidth, canvasHeight, out, options);
  }
  let shake = 0, message = "", messageTimer = 0, engineVoice = null, engineTier = -1;
  let soundClock = 0, lastPosition = 8, posFlash = { text: "", timer: 0, up: true };
  let soundBank = null, alarmVoice = null, alarmCooldown = 0, warningActive = false;
  let explosionAge = 0, explosionOrigin = null, flash = 0, thunderClock = 7;
  let musicEnabled = true, musicClock = 0, musicStep = 0;
  // StationNet plays at most eight sample voices and steals the oldest, which is
  // always the looping engine. Track one-shot lifetimes so the engine can be re-armed.
  let audioClock = 0, oneShots = [];
  // Saved data (ui.storage) and the current race's results.
  let records = {}, career = { races: 0, wins: 0, trophies: {}, active: null };
  let loaded = { settings: false, records: false, career: false };
  let leaderboard = null, rating = 0, cupResult = null, results = null;
  let ghost = null, ghostRun = [], ghostSampleAt = 0, raceToken = 0;
  let particles = [], weather = [], speedTrail = 0;
  // Four bars of per-track synthesized dance/rock motifs; each course has a distinct key,
  // mode, tempo, progression and 16-step lead theme. Nothing is downloaded or streamed.
  const SOUNDTRACKS = [
    { name: "Dustline Drive", bpm: 136, root: 38, scale: [0,2,3,5,7,8,10], chord: [0,5,3,4], lead: [0,-1,2,-1,4,-1,2,1, 0,-1,3,-1,4,2,1,-1], style: 0 },
    { name: "Cliffside Voltage", bpm: 148, root: 41, scale: [0,2,3,5,7,9,10], chord: [0,4,5,3], lead: [0,2,-1,5,4,-1,3,1, 0,4,5,-1,6,4,2,-1], style: 2 },
    { name: "Submerged Pulse", bpm: 120, root: 36, scale: [0,2,3,5,7,8,10], chord: [0,3,5,4], lead: [0,-1,-1,2,3,-1,4,-1, 2,-1,0,1,-1,5,3,-1], style: 1 },
    { name: "Magma Engine", bpm: 154, root: 35, scale: [0,1,3,5,7,8,10], chord: [0,1,5,3], lead: [0,0,-1,4,3,2,-1,1, 0,4,5,4,2,1,-1,6], style: 2 },
    { name: "Snowblind", bpm: 127, root: 45, scale: [0,2,3,5,7,9,10], chord: [0,5,3,4], lead: [0,-1,2,-1,4,-1,6,-1, 5,-1,3,-1,2,-1,0,-1], style: 1 },
    { name: "Canopy Chase", bpm: 143, root: 40, scale: [0,2,4,5,7,9,11], chord: [0,4,3,5], lead: [0,2,3,-1,5,3,2,1, 0,-1,2,4,6,-1,4,2], style: 0 },
    { name: "Grinding Steel", bpm: 157, root: 34, scale: [0,1,3,5,7,8,10], chord: [0,3,4,1], lead: [0,0,3,0,4,-1,3,2, 0,0,5,4,3,-1,1,0], style: 2 },
    { name: "Mirage Express", bpm: 132, root: 43, scale: [0,2,4,6,7,9,11], chord: [0,4,2,5], lead: [0,-1,2,3,4,-1,6,5, 4,2,0,-1,3,2,1,-1], style: 1 },
    { name: "Razor Mesa", bpm: 150, root: 39, scale: [0,2,3,5,7,8,10], chord: [0,5,3,1], lead: [0,2,4,-1,2,0,5,-1, 3,4,6,-1,4,2,1,0], style: 2 },
    { name: "Riptide Runner", bpm: 138, root: 38, scale: [0,2,4,5,7,9,11], chord: [0,3,4,2], lead: [0,1,2,-1,4,2,5,-1, 6,5,4,-1,2,1,0,-1], style: 0 },
    { name: "Storm Warning", bpm: 164, root: 33, scale: [0,1,3,5,7,8,10], chord: [0,1,4,3], lead: [0,0,1,3,4,6,4,3, 0,-1,5,3,2,-1,1,0], style: 2 },
    { name: "Overgrown Circuit", bpm: 141, root: 42, scale: [0,2,3,5,7,9,10], chord: [0,3,5,4], lead: [0,-1,3,2,4,-1,3,1, 5,4,3,-1,2,4,1,-1], style: 1 },
    { name: "Neon Graveyard", bpm: 156, root: 36, scale: [0,2,3,5,7,8,11], chord: [0,5,3,4], lead: [0,2,4,6,4,2,0,-1, 3,-1,5,4,3,2,1,0], style: 0 },
    { name: "Whiteout Breaker", bpm: 146, root: 40, scale: [0,2,3,5,7,9,10], chord: [0,4,5,3], lead: [0,-1,2,4,6,-1,5,3, 4,-1,2,0,1,3,5,-1], style: 1 }
  ];

  // Short, procedural PCM waveforms and non-looping oscillator effects.
  function createSoundBank() {
    if (soundBank) return;
    const hz = 22050, wave = (seconds, f) => {
      const n = Math.floor(seconds * hz), a = new Float32Array(n);
      for (let i = 0; i < n; i++) a[i] = clamp(f(i / hz, i / n), -1, 1);
      return ui.sound.createSample(a, hz);
    };
    const engine = [70, 95, 130, 175].map((fund) => wave(.4, (t) => {
      const p = TAU * fund * t;
      const grit = Math.sin(p) * .35 + Math.sin(2 * p + .2) * .17 + Math.sin(3 * p) * .11;
      return grit * (.83 + .17 * Math.sin(TAU * 10 * t));
    }));
    const r = rand(12345);
    const hit = wave(.56, (t) => (r() * 2 - 1) * Math.exp(-t * 10) * .78 + Math.sin(TAU * (72 - t * 58) * t) * Math.exp(-t * 7) * .32);
    const dirt = wave(.34, (t) => (r() * 2 - 1) * Math.exp(-t * 9) * .45);
    const boost = wave(.48, (t) => {
      const ph = TAU * (100 * t + 410 * t * t);
      return (Math.sin(ph) * .34 + (r() * 2 - 1) * .18) * Math.exp(-t * 3);
    });
    const alarm = wave(.24, (t) => {
      const freq = t < .115 ? 980 : 760;
      const envelope = Math.min(1, t * 90) * Math.min(1, (.24 - t) * 55);
      return envelope * (Math.sin(TAU * freq * t) * .68 + Math.sign(Math.sin(TAU * freq * t)) * .13);
    });
    const explosion = wave(.95, (t) => {
      const noise = (r() * 2 - 1) * Math.exp(-t * 6);
      return noise * .75 + Math.sin(TAU * (70 - t * 51) * t) * Math.exp(-t * 4.5) * .5;
    });
    const kick = wave(.19, (t) => Math.sin(TAU * (145 * t - 230 * t * t)) * Math.exp(-t * 27) * .85);
    const snare = wave(.14, (t) => (r() * 2 - 1) * Math.exp(-t * 29) * .38 + Math.sin(TAU * 175 * t) * Math.exp(-t * 23) * .14);
    const hat = wave(.065, (t) => (r() * 2 - 1) * Math.exp(-t * 78) * .25);
    soundBank = { engine, hit, dirt, boost, alarm, explosion, kick, snare, hat,
      length: new Map([[hit, .56], [dirt, .34], [boost, .48], [alarm, .24], [explosion, .95], [kick, .19], [snare, .14], [hat, .065]]) };
  }
  function shot(handle, options) {
    const rate = options.rate || 1;
    oneShots = oneShots.filter(t => t > audioClock);
    oneShots.push(audioClock + (soundBank.length.get(handle) || .5) / rate);
    // Seven overlapping one-shots plus the engine fill all eight voices.
    if (oneShots.length >= 7 && engineVoice !== null) engineTier = -1;
    return ui.sound.playSample(handle, options);
  }
  function tone(f, ms, waveform = "triangle", volume = .12) {
    if (!muted) ui.sound.playTone(f, ms, { waveform, volume });
  }
  function sfx(name) {
    if (muted) return;
    createSoundBank();
    if (name === "crash") { shot(soundBank.hit, { volume: .70 }); tone(66, 300, "square", .08); }
    if (name === "scrape") shot(soundBank.dirt, { volume: .29 });
    if (name === "boost") shot(soundBank.boost, { volume: .40 });
    if (name === "lap") ui.sound.sequence([784, 988, 1175], { duration: 115, waveform: "triangle", volume: .12 });
    if (name === "best") ui.sound.sequence([988, 1319, 1568, 1976], { duration: 95, waveform: "triangle", volume: .11 });
    if (name === "finish") ui.sound.sequence([523, 659, 784, 1047, 1318], { duration: 140, waveform: "square", volume: .09 });
    if (name === "explode") { shot(soundBank.explosion, { volume: .82 }); tone(48, 430, "sawtooth", .13); }
    if (name === "thunder") shot(soundBank.explosion, { volume: .42, rate: .55 });
  }
  function stopMotor() {
    if (engineVoice !== null) ui.sound.stopSample(engineVoice);
    engineVoice = null; engineTier = -1;
  }
  function motorTick(dt) {
    audioClock += dt;
    if (muted || mode !== "race" || !player || player.crashed) { stopMotor(); return; }
    createSoundBank();
    const ratio = player.v / VEHICLES[player.i].top;
    const tier = ratio < .16 ? 0 : ratio < .45 ? 1 : ratio < .76 ? 2 : 3;
    if (tier !== engineTier) {
      stopMotor();
      engineTier = tier;
      engineVoice = ui.sound.playSample(soundBank.engine[tier], { loop: true, volume: .23 });
    }
    soundClock -= dt;
    if (soundClock <= 0) {
      soundClock = .20;
      if (player.boosting) tone(175 + player.v * 3.5, 160, "sawtooth", .027);
    }
  }

  function stopWarning() {
    if (alarmVoice !== null) ui.sound.stopSample(alarmVoice);
    alarmVoice = null; alarmCooldown = 0; warningActive = false;
  }
  function warningTick(dt) {
    const critical = !muted && player && mode === "race" && !player.crashed && !player.finished && player.boost <= 30;
    if (!critical) { if (warningActive) stopWarning(); return; }
    if (!warningActive) { warningActive = true; alarmCooldown = 0; }
    alarmCooldown -= dt;
    if (alarmCooldown <= 0) {
      createSoundBank();
      if (alarmVoice !== null) ui.sound.stopSample(alarmVoice);
      alarmVoice = shot(soundBank.alarm, { volume: .72 });
      alarmCooldown = .44; // repeating two-tone alarm until boost RECOVERS above 30%
    }
  }
  function stopMusic() { musicClock = 0; musicStep = 0; }
  const midiHz = (note) => 440 * Math.pow(2, (note - 69) / 12);
  function songNote(song, degree, octave = 0) {
    return midiHz(song.root + 12 * octave + song.scale[mod(degree, 7)] + 12 * Math.floor(degree / 7));
  }
  function musicTick(dt) {
    if (muted || !musicEnabled || mode !== "race") return;
    const song = SOUNDTRACKS[track.idx];
    const interval = 60 / song.bpm / 4;
    musicClock -= dt;
    let n = 0;
    while (musicClock <= 0 && n++ < 3) {
      const step = mod(musicStep, 16), bar = Math.floor(musicStep / 16);
      const chord = song.chord[bar % song.chord.length];
      const notes = [];
      if (step % 4 === 0 || (song.style === 2 && step % 4 === 3)) {
        notes.push({ frequency: songNote(song, chord, 0), duration: 215, waveform: "sawtooth", volume: .047, attack: 5, release: 65 });
      }
      const lead = song.lead[step];
      if (lead >= 0 && (bar % 4 !== 3 || step % 2 === 0)) {
        notes.push({ frequency: songNote(song, lead + (bar % 4 === 2 ? 2 : 0), 2), duration: song.style === 1 ? 190 : 106,
          waveform: song.style === 2 ? "square" : "triangle", volume: .031, attack: 8, release: 47 });
      }
      if (step === 0 || step === 8) {
        notes.push({ frequency: songNote(song, chord, 1), duration: 375, waveform: "sine", volume: .024, attack: 12, release: 130 });
      }
      if (notes.length) ui.sound.play(notes, { volume: .05 });
      // Simple four-on-the-floor / breakbeat kit. Samples use StationNet's local PCM synthesizer.
      if (step % 4 === 0 || (song.style === 2 && step === 14)) shot(soundBank.kick, { volume: .27 });
      if (step === 4 || step === 12) shot(soundBank.snare, { volume: .27 });
      // Thin the hats while the alarm or other one-shots are using voices.
      const busy = warningActive || oneShots.filter(t => t > audioClock).length > 3;
      if (busy ? step % 4 === 2 : (step % 2 === 0 || song.style === 0)) shot(soundBank.hat, { volume: .105 });
      musicStep++;
      musicClock += interval;
    }
  }

  // Parameterized, continuously wrapping course centerlines (all distances in meters).
  function center(s) {
    const t = mod(s, track.length) * TAU / track.length;
    const radius = track.length / 5.67, phase = track.seed * .011;
    return {
      x: radius * Math.cos(t) + radius * .12 * track.bend * Math.cos(3 * t + phase)
        + radius * .032 * track.bend * Math.cos(5 * t - phase * .7),
      y: radius * .76 * Math.sin(t) + radius * .086 * track.bend * Math.sin(4 * t - phase * .5)
        + radius * .023 * track.bend * Math.sin(6 * t + phase)
    };
  }
  function frameAt(s) {
    const a = center(s - 1), b = center(s + 1);
    const dx = b.x - a.x, dy = b.y - a.y, mag = Math.hypot(dx, dy) || 1;
    return { tx: dx / mag, ty: dy / mag, nx: dy / mag, ny: -dx / mag };
  }
  function curvatureAt(s) {
    const a = frameAt(s - 9), b = frameAt(s + 9);
    return (a.tx * b.ty - a.ty * b.tx) / 18;
  }
  function section(s) { return mod(s, track.length / 3); }
  function fork(s) {
    const p = section(s), len = track.length / 3;
    return smooth(82, 225, p) * (1 - smooth(len - 240, len - 93, p));
  }
  function roadCenter(s, lane) { return ROUTES[lane].offset * fork(s); }
  function ground(s, d) {
    const f = track.relief;
    return f * (2.0 * Math.sin(s / (39 + track.seed % 13) + track.seed * .031)
      + 1.6 * Math.sin(s / 85 + track.seed * .015)
      + .8 * Math.sin(s / 17 + d / 21));
  }
  function roadHeight(s, lane) {
    const f = fork(s);
    return ground(s, roadCenter(s, lane)) + f * (lane === 0 ? 4.5 : lane === 1 ? -1.8 : 0);
  }
  function point(s, d, z = null) {
    const c = center(s), f = frameAt(s);
    return { x: c.x + f.nx * d, y: c.y + f.ny * d, z: z === null ? ground(s, d) : z };
  }
  function surface(s, d, vIndex) {
    const spread = fork(s), stat = VEHICLES[vIndex];
    if (spread < .12) {
      const off = Math.abs(d), on = off < (14 - stat.width * .3);
      return { lane: 1, on, dist: off, factor: on ? 1 : .53,
        traction: on ? 1.03 : .55, progress: on ? 1 : .84, wear: on ? 0 : .26, hazard: "clear" };
    }
    let best = 0, distance = Infinity;
    for (let i = 0; i < 3; i++) {
      const dis = Math.abs(d - roadCenter(s, i)) / Math.max(2, widthAt(s, i) - stat.width * .34);
      if (dis < distance) { distance = dis; best = i; }
    }
    const r = ROUTES[best], on = distance < 1;
    const hazard = hazardAt(s, best), condition = effectFor(HAZARDS[hazard], stat);
    const pinch = best === 0 ? clamp(1.22 - stat.width * .12, .62, 1.15) : 1;
    const heavyBonus = best === 2 && stat.weight > 2 ? 1.045 : 1;
    return {
      lane: best, on, dist: distance * widthAt(s, best), hazard,
      factor: on ? stat.affin[best] * condition.speed * pinch * heavyBonus : .43,
      traction: on ? condition.grip * (best === 1 ? .94 : best === 2 ? 1.04 : 1) : .46,
      progress: on ? r.distance : .82,
      wear: on ? condition.wear : .48
    };
  }

  function buildTrack() {
    const r = rand(track.seed);
    obstacles = []; ramps = [];
    const third = track.length / 3;
    for (let sector = 0; sector < 3; sector++) {
      for (let lane = 0; lane < 3; lane++) {
        const h = hazardAt(sector * third + 280, lane);
        const count = lane === 0 ? 4 : lane === 1 ? 5 : 3;
        for (let n = 0; n < count; n++) {
          const s = sector * third + 240 + (n + .25 + r() * .30) * (third - 490) / count;
          const offset = (r() - .5) * widthAt(s, lane) * 1.5;
          const rocky = ["rock","scree","ledge","ruts","roots"].includes(h);
          const kind = rocky || n % 4 ? "rock" : "barrel";
          const size = rocky ? 1.4 + r() * 2.7 : 1 + r() * 1.5;
          obstacles.push({ s, lane, offset, radius: size * (lane === 0 ? .65 : .92), size, kind });
        }
        if (lane === 1 && ["ruts", "mud", "bog", "sand", "ash"].includes(h)) {
          ramps.push({ s: sector * third + 330, lane, force: 1.05 });
        }
      }
      ramps.push({ s: sector * third + third - 355, lane: 0, force: 1.12 });
    }
  }
  function makeRacer(i, s = 0, d = 0) {
    return { i, s, d, v: 0, latV: 0, yaw: 0, boost: 100, damage: 0,
      air: 0, vz: 0, crashed: 0, collisionLock: 0, landingLock: 0,
      time: 0, finished: false, finishTime: 0, bestLap: Infinity, lastLapTime: 0,
      lapStart: 0, lastLap: 0, aiRoute: 1, routeSector: -1, skill: 1, brain: null, laneBias: 0,
      boosting: false, persona: "YOU" };
  }
  function announce(text, time = 2) { message = text; messageTimer = time; }
  function backToGarage() {
    if (mode === "finish") finalizeResults();
    clearFingerInput();
    pinchBase = null;
    stopMotor(); stopWarning(); stopMusic(); ui.sound.stop();
    explosionAge = 0; explosionOrigin = null; raceToken++;
    keys = Object.create(null); drive = {throttle:0,brake:0,steer:0,boost:false};
    mode = "garage"; garagePage = "main"; player = null; rivals = []; ghost = null;
    syncGarageTrack();
    renderGarageScreen();
  }
  function startRace() {
    if (mode === "race" || mode === "countdown" || mode === "paused") return;
    if (!ready || (mode === "garage" && garagePage !== "main")) return;
    if (raceMode === 2 && !cupUnlocked(cupIndex)) {
      garageNotice = `LOCKED — WIN THE ${CUPS[cupIndex - 1].name} TO UNLOCK`;
      tone(196, 140, "square", .06);
      return;
    }
    if (raceMode === 2 && (!career.active || career.active.cup !== cupIndex)) {
      career.active = { cup: cupIndex, round: 0, points: {} };
      saveCareer();
    }
    clearFingerInput();
    pinchBase = null;
    createSoundBank(); stopMotor(); stopWarning(); stopMusic();
    cupResult = null; results = null;
    track = TRACKS[raceMode === 2 ? CUPS[cupIndex].tracks[career.active.round] : trackIndex];
    buildTrack();
    keys = Object.create(null); drive = {throttle:0,brake:0,steer:0,boost:false};
    player = makeRacer(vehicleIndex, 0, 0);
    const random = rand(2000 + track.seed + vehicleIndex * 43);
    rivals = [];
    if (raceMode !== 1) {
      // Every grid features all seven *other* vehicle classes and seven distinct driving personalities.
      const diff = DIFFICULTY[difficulty];
      const lineup = VEHICLES.map((_, i) => i).filter(i => i !== vehicleIndex);
      for (let j = lineup.length - 1; j > 0; j--) {
        const k = Math.floor(random() * (j + 1));
        [lineup[j], lineup[k]] = [lineup[k], lineup[j]];
      }
      for (let j = 0; j < 7; j++) {
        const c = lineup[j], base = AI_DRIVERS[(j + track.idx) % AI_DRIVERS.length];
        const brain = { ...base, risk: clamp(base.risk + diff.risk, .1, 1.1), boost: clamp(base.boost * diff.boost, .2, 1) };
        const ai = makeRacer(c, 12 + (6 - j) * 8, (j % 3 - 1) * 3.2);
        ai.brain = brain;
        ai.aiRoute = bestRoute(275, c, brain.risk);
        ai.skill = brain.skill * diff.skill * (.97 + random() * .06);
        ai.laneBias = (random() - .5) * (1.1 + brain.drift * 4);
        ai.persona = brain.name;
        rivals.push(ai);
      }
    }
    const token = ++raceToken;
    ghost = null; ghostRun = []; ghostSampleAt = 0;
    if (raceMode === 1) loadGhost(track, token);
    particles = []; weather = []; flash = 0; thunderClock = 5; speedTrail = 0;
    mode = "countdown"; countdown = 3.6; countdownBeep = 4;
    clock = 0; soundClock = 0; shake = 0; explosionAge = 0; explosionOrigin = null; lastPosition = rivals.length + 1;
    posFlash = { text: "", timer: 0, up: true };
    announce(raceMode === 2 ? `${CUPS[cupIndex].name} — ROUND ${career.active.round + 1} / ${CUPS[cupIndex].tracks.length}`
      : raceMode === 1 ? "TIME TRIAL" : "GET READY", 3.4);
    tone(440, 100, "square", .10);
    saveSettings();
    renderRaceScreen(); // Remove the focused START button so Space cannot click it.
  }
  function resetRacer(r, isPlayer, automatic = false) {
    if (isPlayer) stopWarning();
    r.crashed = 0; r.air = 0; r.vz = 0; r.latV = 0; r.yaw = 0;
    if (r.blowout) r.boost = 58; // after an explosion, a new engine begins safely above the alarm threshold
    r.blowout = false;
    // A wreck recovers with enough repairs to escape the wreck site; a manual
    // reset only moves the car, so it is not a free repair.
    if (automatic || !isPlayer) r.damage = Math.min(55, Math.max(0, r.damage - 40));
    const checkpoint = Math.max(0, Math.floor((r.s - .01) / 135) * 135);
    r.s = Math.min(r.s, checkpoint); // lost distance, but never advance the lap
    const f = fork(r.s);
    r.d = isPlayer ? (f < .15 ? 0 : roadCenter(r.s, 1)) : roadCenter(r.s, r.aiRoute);
    r.v = 16; r.collisionLock = 6.4; r.routeSector = -1;
    if (isPlayer) {
      shake = .5;
      announce(automatic ? "RESPAWNED / TIME LOST" : "RESET TO CHECKPOINT", 2.5);
      tone(330, 110, "square", .08);
    }
  }
  function wreck(r, isPlayer, reason = "impact") {
    if (r.crashed || r.finished) return;
    r.crashed = reason === "boost" ? 2.0 : 1.45; r.v = 0; r.vz = 0;
    r.blowout = reason === "boost";
    if (isPlayer) {
      stopMotor(); stopWarning(); shake = 1.6;
      if (r.blowout) {
        explosionAge = 1.1;
        explosionOrigin = { s: r.s, d: r.d, h: r.air };
        announce("BOOST EXPLODED — RESETTING", 2.6); sfx("explode");
      } else { announce("WRECKED — RESETTING", 2); sfx("crash"); }
    }
  }
  function hitRacer(r, severity, isPlayer) {
    if (r.collisionLock > 0 || r.crashed) return;
    const stat = VEHICLES[r.i];
    r.damage += severity * (1.25 / Math.sqrt(stat.weight + .3));
    r.v *= clamp(1 - severity / 105, .26, .88);
    r.collisionLock = .85;
    if (isPlayer) { shake = clamp(severity / 65, .25, 1.5); sfx("crash"); announce("IMPACT / VEHICLE DAMAGE", 1.25); }
    if (r.damage >= 100 || severity > 90) wreck(r, isPlayer);
  }
  function collisions(r, oldS, isPlayer) {
    const len = track.length, startLap = Math.floor(oldS / len);
    for (const o of obstacles) {
      for (let k = startLap; k <= startLap + 1; k++) {
        const at = k * len + o.s;
        if (at > oldS && at <= r.s && Math.abs(r.d - (roadCenter(at, o.lane) + o.offset)) < o.radius + VEHICLES[r.i].width * .45 && r.air < 2.2) {
          const stat = VEHICLES[r.i];
          const crush = clamp((stat.clearance - .65) * .38 + (stat.weight - 1) * .036, 0, .62);
          const hit = (29 + r.v * .57) * (1 - crush * (o.size < 2.6 ? 1 : .55));
          hitRacer(r, hit, isPlayer); break;
        }
      }
    }
    for (const a of ramps) {
      for (let k = startLap; k <= startLap + 1; k++) {
        const at = k * len + a.s;
        if (at > oldS && at <= r.s && r.v > 18 && r.air <= 0 && Math.abs(r.d - roadCenter(at, a.lane)) < ROUTES[a.lane].width * .85) {
          const stat = VEHICLES[r.i];
          r.air = .1; r.vz = (3.0 + r.v * .115) * (a.force || 1) / clamp(1 + stat.weight * .085, 1, 1.42);
          if (isPlayer) { announce("AIRBORNE", .8); tone(345, 140, "sawtooth", .055); }
        }
      }
    }
  }
  function progressRacer(r, dt, isPlayer) {
    const stat = VEHICLES[r.i];
    r.time += dt;
    r.collisionLock = Math.max(0, r.collisionLock - dt);
    if (r.crashed > 0) { r.crashed -= dt; if (r.crashed <= 0) resetRacer(r, isPlayer, true); return; }
    if (r.finished) return;
    let throttle = 1, brake = false, steer = 0, usingBoost = false;
    const nextCurve = Math.max(Math.abs(curvatureAt(r.s + 28)), Math.abs(curvatureAt(r.s + 58)));
    const road = surface(r.s, r.d, r.i);
    const safeGrip = 17 * stat.grip * road.traction / (1 + stat.weight * .05);
    if (isPlayer) {
      throttle = drive.throttle;
      brake = drive.brake;
      steer = drive.steer;
      usingBoost = drive.boost && r.boost > 0 && throttle > .05;
    } else {
      const brain = r.brain;
      const sector = Math.floor(Math.max(0, r.s + 65) / (track.length / 3));
      if (sector !== r.routeSector) {
        r.routeSector = sector;
        // Personality makes short-cut gamblers, cautious haulers and opportunists behave differently.
        let best = -Infinity, chosen = 1;
        for (let lane = 0; lane < 3; lane++) {
          const evalS = sector * (track.length / 3) + 280;
          let score = routeScore(evalS, r.i, lane, brain.risk);
          if (brain.style === "SHORTCUT HUNTER" && lane === 0) score *= 1.13;
          if (brain.style === "MUD RUNNER" && lane === 1) score *= 1.13;
          if (brain.style === "CAUTIOUS" && lane === 2) score *= 1.12;
          if (brain.style === "RECKLESS" && lane === 0) score *= 1.055;
          score *= .94 + ((Math.sin(track.seed + sector * 9 + r.i * 5 + lane * 19) + 1) * .05) * (1.15 - brain.discipline * .26);
          if (score > best) { chosen = lane; best = score; }
        }
        r.aiRoute = chosen;
      }
      const goalS = r.s + clamp(r.v * .72, 23, 49);
      let desired = roadCenter(goalS, r.aiRoute) + r.laneBias * fork(goalS);
      let block = Infinity, bumpSide = 0;
      for (const o of obstacles) {
        const ds = mod(o.s - mod(r.s, track.length), track.length);
        if (ds < block && ds < 55 && o.lane === r.aiRoute && Math.abs(o.offset - r.laneBias) < o.radius + 2.3) {
          block = ds;
          bumpSide = o.offset >= r.laneBias ? -1 : 1;
        }
      }
      if (block < 55) {
        const w = widthAt(r.s + block, r.aiRoute);
        desired += bumpSide * Math.min(w * .52, 4.8) * smooth(55, 8, block);
        // Careful drivers lift for obstacles; aggressive opponents charge through.
        if (block < 17 && brain.risk < .6) brake = r.v > 32;
      }
      steer = clamp((desired - r.d) * (.13 + brain.discipline * .018) - r.latV * .082, -1, 1);
      const speedForTurn = Math.sqrt(safeGrip / Math.max(nextCurve, .0001)) * (1.04 + brain.risk * .23) * (.88 + brain.discipline * .10);
      if (r.v > speedForTurn + 5 && nextCurve > .003) brake = true;
      usingBoost = r.boost > 19 + (1 - brain.boost) * 55 && r.v > 15 && !brake && nextCurve < .016 &&
        (brain.boost > .68 || section(r.s) > 280) && road.on && road.traction > .56;
    }
    const health = 1 - clamp(r.damage / 160, 0, .48);
    if (isPlayer && usingBoost && !r.boosting) sfx("boost");
    r.boosting = usingBoost;
    let cap = stat.top * road.factor * health * (usingBoost ? 1.27 : 1) * (isPlayer ? 1 : r.skill);
    if (!isPlayer) {
      const brain = r.brain;
      cap = Math.min(cap, Math.sqrt(safeGrip / Math.max(nextCurve, .00001)) *
        (1.01 + brain.risk * .26) * (.93 + brain.discipline * .08));
      // ROOKIE only: leaders far ahead of an unfinished player ease off.
      if (DIFFICULTY[difficulty].rubber && player && !player.finished && r.s - player.s > 120) cap *= .93;
    }
    const engineAccel = stat.acc * (usingBoost ? 1.77 : 1) * (road.on ? 1 : .70);
    if (brake) r.v -= 26 * Math.min(1, Number(brake)) * dt;
    else if (throttle) r.v += engineAccel * Math.min(1, Number(throttle)) * (1 - .53 * Math.pow(clamp(r.v / Math.max(cap, 1), 0, 1), 2)) * dt;
    else r.v -= (3 + r.v * .09) * dt;
    r.v -= (road.on ? .62 + road.wear * 2 : 1.6) * dt;
    r.v = clamp(r.v, 0, Math.max(3, cap));
    const response = 4.4 * stat.turn * stat.grip * road.traction / (1 + stat.weight * .09);
    const wanted = steer * (7.1 + r.v * .36) * stat.turn * road.traction;
    r.latV = mix(r.latV, wanted, clamp(response * dt, 0, 1));
    const curvature = curvatureAt(r.s);
    const overload = clamp((r.v * r.v * Math.abs(curvature) - safeGrip) / safeGrip, 0, 2.6);
    if (overload > 0 && r.air === 0) {
      r.latV += Math.sign(curvature) * overload * (3.5 + stat.weight * .28) * dt;
      r.v = Math.max(0, r.v - overload * 2.2 * dt);
    }
    if (r.air > 0) r.latV *= (1 - .3 * dt);
    r.d += r.latV * dt;
    r.yaw = clamp(Math.atan2(r.latV, Math.max(r.v, 7)) * .86, -.49, .49);
    const previous = r.s;
    r.s += r.v * dt * road.progress * (road.on ? 1 : .94);
    if (usingBoost) {
      r.boost = Math.max(0, r.boost - (isPlayer ? 26 : 19 + r.brain.boost * 8) * dt);
      if (r.boost <= 0) { wreck(r, isPlayer, "boost"); return; }
    } else r.boost = Math.min(100, r.boost + (road.on ? 9.1 : 4) * dt);
    if (r.air > 0) {
      r.air += r.vz * dt;
      r.vz -= 17.5 * dt;
      if (r.air <= 0) {
        const impact = -r.vz;
        r.air = 0; r.vz = 0;
        const suspensionFactor = clamp(1.6 - stat.suspension * .4, .48, 1.45);
        if (impact > 12) hitRacer(r, (impact - 11) * 4.4 * suspensionFactor, isPlayer);
        if (isPlayer) { shake = Math.max(shake, .13); sfx("scrape"); }
      }
    }
    if (Math.abs(r.d) > 67) { wreck(r, isPlayer); return; }
    if (r.air === 0 && r.v > 15) {
      r.damage += dt * road.wear * r.v * (road.on ? .013 : .14) / Math.sqrt(stat.weight + .25);
      if (r.damage >= 100) { wreck(r, isPlayer); return; }
      if (isPlayer && (road.wear > .15 || !road.on) && Math.random() < dt * .8) sfx("scrape");
    }
    collisions(r, previous, isPlayer);
    const newLap = Math.floor(Math.max(0, r.s) / track.length);
    if (newLap > r.lastLap) {
      const lapTime = r.time - r.lapStart;
      // Compare with this race's best lap and the saved record for this track/vehicle.
      const saved = records[recordKey(track, r.i)];
      const reference = Math.min(r.bestLap, saved ? saved.l : Infinity);
      r.bestLap = Math.min(r.bestLap, lapTime);
      r.lastLapTime = lapTime;
      r.lapStart = r.time;
      r.lastLap = newLap;
      if (isPlayer && newLap < track.laps) {
        const label = newLap === track.laps - 1 ? "FINAL LAP" : `LAP ${newLap + 1} / ${track.laps}`;
        const delta = Number.isFinite(reference) ? `  ${lapTime < reference ? "−" : "+"}${Math.abs(lapTime - reference).toFixed(2)}` : "";
        if (Number.isFinite(reference) && lapTime < reference) { announce(`BEST LAP ${moneyTime(lapTime)}${delta} — ${label}`, 2.8); sfx("best"); }
        else { announce(`${label} — ${moneyTime(lapTime)}${delta}`, 2.6); sfx("lap"); }
      }
    }
    if (r.s >= track.length * track.laps) {
      r.s = track.length * track.laps; r.finished = true; r.finishTime = r.time;
      if (isPlayer) endRace();
    }
  }
  // ---- Saved data: settings, records, career and ghosts (ui.storage), rating (ui.scores) ----
  // Keys: settings, records, career, ghost:<track> — 17 of the 100 allowed.
  const store = ui.storage && typeof ui.storage.get === "function" ? ui.storage : null;
  let settingsTimer = null;
  function save(key, value) {
    if (!store || typeof store.set !== "function") return;
    try { Promise.resolve(store.set(key, value)).catch(() => {}); } catch (error) { /* storage is optional */ }
  }
  function settingsSnapshot() {
    return { muted, music: musicEnabled, controls: controlsMode, difficulty, track: trackIndex,
      vehicle: vehicleIndex, mode: raceMode, cup: cupIndex, zoom: Math.round(cameraZoom * 100) / 100 };
  }
  function saveSettings() {
    if (!loaded.settings) return; // never overwrite data that failed to load
    if (typeof ui.timeout !== "function") { save("settings", settingsSnapshot()); return; }
    if (settingsTimer) settingsTimer();
    settingsTimer = ui.timeout(() => { settingsTimer = null; save("settings", settingsSnapshot()); }, 400);
  }
  function saveRecords() { if (loaded.records) save("records", records); }
  function saveCareer() { if (loaded.career) save("career", career); }
  const index = (value, count, fallback) => Number.isInteger(value) && value >= 0 && value < count ? value : fallback;
  const positive = (value) => typeof value === "number" && Number.isFinite(value) && value > 0;
  function applySettings(s) {
    if (!s || typeof s !== "object") return;
    if (typeof s.muted === "boolean") muted = s.muted;
    if (typeof s.music === "boolean") musicEnabled = s.music;
    controlsMode = index(s.controls, CONTROL_MODES.length, controlsMode);
    difficulty = index(s.difficulty, DIFFICULTY.length, difficulty);
    trackIndex = index(s.track, TRACKS.length, trackIndex);
    vehicleIndex = index(s.vehicle, VEHICLES.length, vehicleIndex);
    raceMode = index(s.mode, MODES.length, raceMode);
    cupIndex = index(s.cup, CUPS.length, cupIndex);
    if (typeof s.zoom === "number" && Number.isFinite(s.zoom)) cameraZoom = clamp(s.zoom, .67, 1.7);
  }
  function readRecords(value) {
    const out = {};
    if (!value || typeof value !== "object") return out;
    for (const t of TRACKS) for (const v of VEHICLES) {
      const key = `${t.slug}:${v.name}`, r = value[key];
      if (r && positive(r.t)) out[key] = { t: r.t, l: positive(r.l) ? r.l : r.t };
    }
    return out;
  }
  function readCareer(value) {
    const out = { races: 0, wins: 0, trophies: {}, active: null };
    if (!value || typeof value !== "object") return out;
    if (Number.isInteger(value.races) && value.races >= 0) out.races = value.races;
    if (Number.isInteger(value.wins) && value.wins >= 0) out.wins = value.wins;
    for (const cup of CUPS) if (value.trophies && value.trophies[cup.name] === true) out.trophies[cup.name] = true;
    const a = value.active;
    if (a && Number.isInteger(a.cup) && a.cup >= 0 && a.cup < CUPS.length &&
        Number.isInteger(a.round) && a.round >= 0 && a.round < CUPS[a.cup].tracks.length) {
      const names = new Set(["YOU", ...AI_DRIVERS.map(d => d.name)]), points = {};
      for (const [name, pts] of Object.entries(a.points || {}))
        if (names.has(name) && Number.isInteger(pts) && pts >= 0) points[name] = pts;
      out.active = { cup: a.cup, round: a.round, points };
    }
    return out;
  }
  async function loadSaved() {
    if (!store) return;
    const get = async (key) => { try { return { ok: true, value: await store.get(key) }; } catch (error) { return { ok: false }; } };
    const [s, r, c] = await Promise.all([get("settings"), get("records"), get("career")]);
    if (s.ok) { loaded.settings = true; applySettings(s.value); }
    if (r.ok) { loaded.records = true; records = readRecords(r.value); }
    if (c.ok) { loaded.career = true; career = readCareer(c.value); }
    if (!s.ok || !r.ok || !c.ok) garageNotice = "SAVED DATA COULD NOT BE LOADED — CHANGES WILL NOT BE SAVED";
    rating = computeRating();
  }
  function recordKey(t, vehicle) { return `${t.slug}:${VEHICLES[vehicle].name}`; }
  function trackBest(t) {
    let best = null;
    VEHICLES.forEach((_, i) => {
      const r = records[recordKey(t, i)];
      if (r && (!best || r.t < best.t)) best = { t: r.t, l: r.l, vehicle: i };
    });
    return best;
  }
  // Rally Rating: 1000 points per track for averaging 50 m/s; improving anywhere raises it.
  function trackPoints(t) {
    const best = trackBest(t);
    return best ? clamp(Math.round(1000 * (t.length * t.laps / 50) / best.t), 0, 1600) : 0;
  }
  function computeRating() { return TRACKS.reduce((sum, t) => sum + trackPoints(t), 0); }
  function submitScore() {
    if (!ui.scores || typeof ui.scores.submit !== "function" || rating <= 0) return;
    try {
      Promise.resolve(ui.scores.submit(rating)).then(res => { if (res && Array.isArray(res.leaderboard)) leaderboard = res; }).catch(() => {});
    } catch (error) { /* leaderboard is optional */ }
  }
  function fetchLeaderboard() {
    if (!ui.scores || typeof ui.scores.leaderboard !== "function") { leaderboard = { error: true }; return; }
    if (!leaderboard || leaderboard.error) leaderboard = { loading: true };
    try {
      Promise.resolve(ui.scores.leaderboard()).then(res => {
        leaderboard = res && Array.isArray(res.leaderboard) ? res : { error: true };
      }).catch(() => { leaderboard = { error: true }; });
    } catch (error) { leaderboard = { error: true }; }
  }
  // Time-trial ghost: 10 samples per second of [s, d, air, yaw] as scaled integers.
  let ghostState = "none", ghostCursor = 0;
  function loadGhost(t, token) {
    ghostState = "loading"; ghostCursor = 0;
    if (!store) { ghostState = "error"; return; }
    Promise.resolve().then(() => store.get(`ghost:${t.slug}`)).then(value => {
      if (token !== raceToken) return;
      const ok = value && positive(value.time) && Array.isArray(value.frames) && value.frames.length >= 8 &&
        value.frames.length % 4 === 0 && value.frames.every(Number.isFinite);
      ghost = ok ? { time: value.time, vehicle: index(value.vehicle, VEHICLES.length, 0), frames: value.frames } : null;
      ghostState = ghost ? "ok" : "none";
    }).catch(() => { if (token === raceToken) ghostState = "error"; });
  }
  function recordGhost() {
    if (raceMode !== 1 || !player) return;
    while (player.time >= ghostSampleAt && ghostRun.length < 4 * 10 * 900) {
      ghostRun.push(Math.round(player.s * 10), Math.round(player.d * 10), Math.round(player.air * 10), Math.round(player.yaw * 100));
      ghostSampleAt += .1;
    }
  }
  function ghostAt(time) {
    const f = ghost.frames, n = f.length / 4, at = clamp(time * 10, 0, n - 1);
    const i = Math.min(n - 2, Math.floor(at)), k = at - i, j = i * 4;
    return { s: mix(f[j], f[j + 4], k) / 10, d: mix(f[j + 1], f[j + 5], k) / 10,
      air: Math.max(0, mix(f[j + 2], f[j + 6], k) / 10), yaw: f[j + 3] / 100 };
  }
  function ghostDelta() {
    if (!ghost || !player) return null;
    const f = ghost.frames, n = f.length / 4, target = player.s * 10;
    if (ghostCursor > 0 && f[ghostCursor * 4] > target) ghostCursor = 0; // after a reset
    while (ghostCursor < n - 2 && f[(ghostCursor + 1) * 4] <= target) ghostCursor++;
    const a = f[ghostCursor * 4], b = f[(ghostCursor + 1) * 4];
    const t = (ghostCursor + (b > a ? clamp((target - a) / (b - a), 0, 1) : 0)) / 10;
    return player.time - t;
  }
  function saveGhost() {
    if (ghostState === "loading" || ghostState === "error" || ghostRun.length < 8) return false;
    if (ghost && player.time >= ghost.time) return false;
    const value = { time: Math.round(player.time * 1000) / 1000, vehicle: player.i, frames: ghostRun.slice() };
    save(`ghost:${track.slug}`, value);
    return true;
  }

  // ---- Race standings, results and championship points ----
  function racePosition() {
    return 1 + rivals.filter(a => a.finished ? (!player.finished || a.finishTime < player.finishTime) : a.s > player.s).length;
  }
  function standings() {
    const total = track.length * track.laps, final = results && results.final;
    return [player, ...rivals].map(r => ({ r, name: r.persona, vehicle: VEHICLES[r.i].name, done: r.finished,
      time: r.finished ? r.finishTime : r.time + Math.max(0, total - r.s) / Math.max(18, r.v), best: r.bestLap }))
      .sort((a, b) => final ? a.time - b.time
        : (Number(b.done) - Number(a.done)) || (a.done ? a.time - b.time : b.r.s - a.r.s));
  }
  function gapText() {
    if (!player || mode !== "race") return null;
    if (raceMode === 1) {
      if (ghostState === "loading") return { text: "LOADING GHOST…", color: C.dim };
      if (!ghost) return { text: "NO GHOST YET — SET A TIME", color: C.dim };
      const delta = ghostDelta();
      return { text: `GHOST ${delta <= 0 ? "−" : "+"}${Math.abs(delta).toFixed(2)}`, color: delta <= 0 ? C.green : C.red };
    }
    const racing = rivals.filter(a => !a.finished);
    const ahead = racing.filter(a => a.s > player.s).sort((a, b) => a.s - b.s)[0];
    const behind = racing.filter(a => a.s <= player.s).sort((a, b) => b.s - a.s)[0];
    const secs = (a) => (Math.abs(a.s - player.s) / Math.max(player.v, 12)).toFixed(1);
    const front = ahead ? `▲ ${ahead.persona} ${secs(ahead)}s` : "LEADING";
    return { text: behind ? `${front}   ▼ ${behind.persona} ${secs(behind)}s` : front, color: ahead ? C.white : C.green };
  }
  // Route preview for the next fork: hazard per route, colored for this vehicle.
  function hazardPreview() {
    if (!player || mode !== "race") return null;
    const len = track.length / 3, p = section(player.s), start = player.s - p;
    let evalS, distance;
    if (p < 82) { distance = 82 - p; evalS = start + 280; }
    else if (p > len - 230) { distance = len - p + 82; evalS = start + len + 280; }
    else return null;
    if (distance > 300) return null;
    const rating = suitability(evalS, player.i);
    return [0, 1, 2].map(lane => ({ short: ROUTES[lane].name.split(" ")[0][0], name: ROUTES[lane].name,
      hazard: hazardAt(evalS, lane), rating: rating[lane] }));
  }
  function endRace() {
    mode = "finish"; stopMotor(); stopWarning(); stopMusic(); sfx("finish");
    clearFingerInput();
    const pos = racePosition();
    results = { pos, final: rivals.length === 0, clock: 0, notes: [], ghostDelta: null, earned: null };
    const key = recordKey(track, player.i), old = records[key], prior = trackBest(track);
    const lap = Number.isFinite(player.bestLap) ? player.bestLap : player.time;
    if (!old || player.time < old.t) results.notes.push("NEW PERSONAL BEST");
    if (prior && player.time < prior.t) results.notes.push("NEW TRACK RECORD");
    if (old && lap < old.l) results.notes.push("NEW BEST LAP");
    records[key] = { t: Math.round(Math.min(old ? old.t : Infinity, player.time) * 1000) / 1000,
      l: Math.round(Math.min(old ? old.l : Infinity, lap) * 1000) / 1000 };
    saveRecords();
    const newRating = computeRating();
    if (newRating > rating) { rating = newRating; submitScore(); }
    if (raceMode === 1) {
      results.ghostDelta = ghost ? player.time - ghost.time : null;
      if (saveGhost()) results.notes.push("GHOST SAVED");
    }
    career.races++;
    if (rivals.length && pos === 1) career.wins++;
    saveCareer();
    announce(rivals.length && pos === 1 ? "VICTORY!" : "FINISH LINE", 7);
  }
  function finalizeResults() {
    if (!results || results.final) return;
    results.final = true;
    if (raceMode !== 2 || !career.active) return;
    const cup = CUPS[career.active.cup], pts = career.active.points;
    results.earned = {};
    standings().forEach((row, i) => {
      pts[row.name] = (pts[row.name] || 0) + POINTS[i];
      results.earned[row.name] = POINTS[i];
    });
    career.active.round++;
    // Ties go to the player.
    const table = Object.entries(pts).sort((a, b) => b[1] - a[1] || (a[0] === "YOU" ? -1 : b[0] === "YOU" ? 1 : 0));
    if (career.active.round >= cup.tracks.length) {
      const place = 1 + table.findIndex(([name]) => name === "YOU"), won = place === 1;
      const next = CUPS[career.active.cup + 1];
      const unlocked = won && next && !career.trophies[cup.name] ? next.name : null;
      if (won) career.trophies[cup.name] = true;
      cupResult = { done: true, table, place, won, cup: cup.name, unlocked };
      career.active = null;
    } else cupResult = { done: false, table, cup: cup.name, round: career.active.round, rounds: cup.tracks.length };
    saveCareer();
  }
  function finishActions() {
    if (raceMode === 2) return cupFinished() ? [] : ["NEXT"];
    return ["RESTART", "NEXT"];
  }
  function finishAction(kind) {
    if (mode !== "finish") return;
    finalizeResults();
    if (kind === "primary") kind = finishActions()[0] || "GARAGE";
    if (kind === "GARAGE" || (kind === "NEXT" && raceMode === 2 && cupFinished())) { backToGarage(); return; }
    if (kind === "RESTART" && raceMode === 2) return;
    if (kind === "NEXT" && raceMode !== 2) { trackIndex = mod(trackIndex + 1, TRACKS.length); }
    startRace();
  }
  function tick(dt) {
    if (mode === "countdown") {
      countdown -= dt;
      const n = Math.ceil(countdown);
      if (n < countdownBeep && n >= 0) {
        tone(n === 0 ? 990 : 495, n === 0 ? 250 : 100, "square", .11);
        countdownBeep = n;
      }
      if (countdown <= 0) { mode = "race"; musicClock = 0; musicStep = 0; announce("GO!", 1.2); }
      return;
    }
    if (mode === "finish") {
      // Rivals keep racing so the results table fills in with real finishing times.
      clock += dt;
      messageTimer = Math.max(0, messageTimer - dt);
      shake = Math.max(0, shake - dt * 1.6);
      if (results && !results.final) {
        results.clock += dt;
        for (const a of rivals) progressRacer(a, dt, false);
        if (rivals.every(a => a.finished) || results.clock > 25) finalizeResults();
      }
      return;
    }
    if (mode !== "race") return;
    clock += dt;
    messageTimer = Math.max(0, messageTimer - dt);
    posFlash.timer = Math.max(0, posFlash.timer - dt);
    shake = Math.max(0, shake - dt * 1.6);
    explosionAge = Math.max(0, explosionAge - dt);
    progressRacer(player, dt, true);
    recordGhost();
    if (mode !== "race") return;
    for (const a of rivals) progressRacer(a, dt, false);
    for (const a of rivals) {
      if (a.crashed || a.finished || player.crashed || player.collisionLock > 0) continue;
      if (Math.abs(player.s - a.s) < 3.0 && Math.abs(player.d - a.d) < (VEHICLES[player.i].width + VEHICLES[a.i].width) * .56) {
        const relative = Math.abs(player.v - a.v);
        if (relative > 5) { hitRacer(player, 17 + relative * 1.5, true); hitRacer(a, 14 + relative, false); }
      }
    }
    // Rival/rival impacts are possible too; large vehicles bully lighter opponents.
    for (let i = 0; i < rivals.length; i++) for (let j = i + 1; j < rivals.length; j++) {
      const a = rivals[i], b = rivals[j];
      if (a.crashed || b.crashed || a.finished || b.finished || a.collisionLock > 0 || b.collisionLock > 0) continue;
      if (Math.abs(a.s - b.s) < 3.2 && Math.abs(a.d - b.d) < (VEHICLES[a.i].width + VEHICLES[b.i].width) * .52) {
        const force = 10 + Math.abs(a.v - b.v) * .9;
        hitRacer(a, force * Math.sqrt(VEHICLES[b.i].weight / VEHICLES[a.i].weight), false);
        hitRacer(b, force * Math.sqrt(VEHICLES[a.i].weight / VEHICLES[b.i].weight), false);
      }
    }
    const position = racePosition();
    if (rivals.length && clock > 3 && position !== lastPosition) {
      const up = position < lastPosition;
      posFlash = { text: `P${position} ${up ? "▲" : "▼"}`, timer: 1.4, up };
      tone(up ? 920 : 330, 75, "triangle", .055);
    }
    lastPosition = position;
  }

  // Software 3D projection to StationNet's geometry-only canvas: no WebGL or DOM.
  let camera = null;
  function makeCamera() {
    const f = frameAt(player.s), yaw = player.yaw * .43;
    const fx = f.tx * Math.cos(yaw) + f.nx * Math.sin(yaw);
    const fy = f.ty * Math.cos(yaw) + f.ny * Math.sin(yaw);
    const rx = fy, ry = -fx;
    const body = point(player.s, player.d);
    const support = surface(player.s, player.d, player.i);
    const roadZ = support.on && fork(player.s) > .12 ? roadHeight(player.s, support.lane) : ground(player.s, player.d);
    return { x: body.x - fx * (14 * cameraZoom), y: body.y - fy * (14 * cameraZoom),
      z: roadZ + 5.5 * cameraZoom + player.air * .28,
      fx, fy, rx, ry, focal: 650 - speedTrail * 70, // boost widens the view slightly
      horizon: 205 + (shake ? (Math.random() - .5) * shake * 8 : 0) };
  }
  function project(p) {
    const x = p.x - camera.x, y = p.y - camera.y;
    const f = x * camera.fx + y * camera.fy;
    if (f < 3.7 || f > 820) return null;
    const side = x * camera.rx + y * camera.ry;
    return { x: W / 2 + camera.focal * side / f,
      y: camera.horizon - camera.focal * (p.z - camera.z) / f,
      depth: f };
  }
  function link(a, b, seg) {
    if (a && b && Math.abs(a.x) < 3500 && Math.abs(b.x) < 3500 && Math.abs(a.y) < 3500 && Math.abs(b.y) < 3500)
      seg.push([a.x, a.y, b.x, b.y]);
  }
  function segmentCmd(segments, color, width = 1) {
    return { type: "lines", segments, color, width };
  }
  function putText(out, x, y, str, size = 15, color = C.white, align = "left") {
    out.push({ type: "text", x, y, text: String(str), size, color, align });
  }
  // Filled sky bands, a sun or moon that swings with the course heading, and
  // filled mountain silhouettes behind the wire ridgelines.
  function drawSky(out) {
    const sky = track.air.sky, horizon = camera.horizon;
    out.push({ type: "rect", x: 0, y: 0, width: W, height: horizon * .45, color: sky[0] },
      { type: "rect", x: 0, y: horizon * .45, width: W, height: horizon * .35, color: sky[1] },
      { type: "rect", x: 0, y: horizon * .80, width: W, height: horizon * .20 + 1, color: sky[2] },
      { type: "rect", x: 0, y: horizon, width: W, height: H - horizon, color: "#030b0d" });
    if (track.air.weather === "night") {
      const stars = [], r = rand(track.seed);
      for (let i = 0; i < 46; i++) {
        const x = r() * W, y = r() * horizon * .7;
        stars.push([x, y, x + 1.4, y]);
      }
      out.push(segmentCmd(stars, "#c8d4ff", 1.4));
    }
    if (track.air.sun) {
      const angle = track.seed * .37, dx = Math.cos(angle), dy = Math.sin(angle);
      const forward = dx * camera.fx + dy * camera.fy, side = dx * camera.rx + dy * camera.ry;
      if (forward > .25) {
        const x = W / 2 + camera.focal * side / forward * .5, y = 52 + track.seed % 50;
        const night = track.air.weather === "night";
        out.push({ type: "circle", x, y, radius: night ? 30 : 42, color: track.air.sky[2] + "66" },
          { type: "circle", x, y, radius: night ? 15 : 24, color: track.air.sun });
      }
    }
  }
  function drawMountains(out) {
    const ranges = [[], []], ridge = [[], []];
    for (let k = 0; k < 2; k++) {
      for (let x = -20; x < W + 20; x += 16) {
        const y = 199 - k * 7 - 30 * Math.abs(Math.sin(x * .010 + track.seed * .01 + k))
          - 19 * Math.abs(Math.sin(x * .028 + k * 3)) - 12 * Math.sin(x * .006);
        if (x > -20) link({ x: x - 16, y: ranges[k].prevY }, { x, y }, ranges[k]);
        ranges[k].prevY = y;
        ridge[k].push([x, y]);
      }
    }
    for (let k = 1; k >= 0; k--) {
      const points = [...ridge[k], [W + 20, 206], [-20, 206]];
      out.push({ type: "polygon", points, fill: k ? track.palette[0] + "80" : track.palette[0] + "c0" });
    }
    out.push(segmentCmd(ranges[0], track.palette[0]), segmentCmd(ranges[1], track.palette[1]));
    out.push({ type: "line", x1: 0, y1: 205, x2: W, y2: 205, color: C.faint });
  }
  function drawTerrain(out) {
    const segments = [], sides = [-78, -60, -42, -22, 0, 22, 42, 60, 78];
    const st = Math.floor((player.s - 12) / 22) * 22;
    for (let s = st; s < player.s + 340; s += 22) {
      for (let j = 0; j < sides.length - 1; j++) {
        const a = project(point(s, sides[j])), b = project(point(s, sides[j + 1]));
        link(a, b, segments);
      }
      for (let j = 0; j < sides.length; j++) {
        link(project(point(s, sides[j])), project(point(s + 22, sides[j])), segments);
      }
    }
    out.push(segmentCmd(segments, track.palette[0]));
  }
  function drawLandmarks(out) {
    // Each course has recognizable distant roadside silhouette geometry.
    const features = [], course = track.idx;
    const start = Math.floor((player.s - 25) / 68) * 68;
    for (let s = start; s < player.s + 335; s += 68) {
      for (let side of [-1, 1]) {
        const d = side * (62 + (Math.floor(s / 68) % 3) * 7), ht = 6 + Math.abs(Math.sin(s * .073)) * 8;
        const pt = (f, dOff, h) => project(worldAt(s + f, d + dOff, h));
        if (course === 0) { // desert cactus
          link(pt(0,0,0),pt(0,0,ht),features);
          link(pt(0,0,ht*.65),pt(0,side*3,ht*.65),features);
          link(pt(0,side*3,ht*.65),pt(0,side*3,ht*.95),features);
        } else if (course === 1 || course === 3) { // mountain rock pillars / basalt spires
          link(pt(-4,-3,0),pt(0,0,ht*1.5),features);
          link(pt(0,0,ht*1.5),pt(4,3,0),features);
          link(pt(4,3,0),pt(-4,-3,0),features);
          if (course === 3) link(pt(0,0,ht*1.5),pt(2,1,ht*2),features);
        } else if (course === 2) { // marsh reeds
          for (let i = 0; i < 4; i++) link(pt(i*2-4,i*1.6-3,0),pt(i*2-3,i*1.6-2,ht*.7),features);
        } else if (course === 4) { // ice crystal clusters
          link(pt(-4,-4,0),pt(0,0,ht*1.35),features);
          link(pt(0,0,ht*1.35),pt(4,4,0),features);
          link(pt(-4,-4,0),pt(4,4,0),features);
          link(pt(0,0,ht*1.35),pt(2,-3,0),features);
        } else if (course === 5) { // evergreen trees
          link(pt(0,0,0),pt(0,0,ht*1.25),features);
          for (let j = 0; j < 3; j++) {
            const h = ht * (.32 + j * .25), branch = 6 - j * 1.6;
            link(pt(0,0,h),pt(0,-branch,h*.60),features);
            link(pt(0,0,h),pt(0,branch,h*.60),features);
          }
        } else if (course === 6) { // quarry steel cranes
          link(pt(-3,0,0),pt(-3,0,ht*1.6),features);
          link(pt(-3,0,ht*1.6),pt(8,0,ht*1.6),features);
          link(pt(8,0,ht*1.6),pt(8,0,ht*.9),features);
          link(pt(-3,0,ht*1.6),pt(3,0,ht*.7),features);
        } else if (course === 7) { // salt flats marker towers
          link(pt(-3,-2,0),pt(-3,-2,ht*.6),features);
          link(pt(3,2,0),pt(3,2,ht*.6),features);
          link(pt(-3,-2,ht*.6),pt(3,2,ht*.6),features);
        } else if (course === 8) { // red mesa sandstone stacks
          link(pt(-5,-3,0),pt(-4,-3,ht*.8),features);
          link(pt(-4,-3,ht*.8),pt(3,-3,ht*.8),features);
          link(pt(3,-3,ht*.8),pt(4,3,0),features);
          link(pt(-5,-3,0),pt(4,3,0),features);
        } else if (course === 9) { // typhoon coast palm trees
          link(pt(0,0,0),pt(0,0,ht*.95),features);
          for (let j = -2; j <= 2; j++) link(pt(0,0,ht*.95),pt(j*3,j*2,ht*.62),features);
        } else if (course === 10) { // thunderhead pylons and bolts
          link(pt(-3,0,0),pt(0,0,ht*1.5),features);
          link(pt(3,0,0),pt(0,0,ht*1.5),features);
          link(pt(-3,0,ht*.7),pt(3,0,ht*.7),features);
          link(pt(0,0,ht*1.5),pt(4,-2,ht*1.65),features);
        } else if (course === 11) { // ruined stone archways
          link(pt(-4,0,0),pt(-4,0,ht),features);
          link(pt(-4,0,ht),pt(4,0,ht),features);
          link(pt(4,0,ht),pt(4,0,0),features);
          link(pt(-4,0,ht*.55),pt(4,0,ht*.55),features);
        } else if (course === 12) { // yard floodlight towers
          link(pt(-2,0,0),pt(-2,0,ht*1.4),features);
          link(pt(-2,0,ht*1.4),pt(6,0,ht*1.4),features);
          link(pt(6,0,ht*1.4),pt(6,0,ht*.9),features);
          link(pt(5,0,ht*1.4),pt(7,0,ht*1.2),features);
        } else { // glacier chasm crystals
          link(pt(-5,-4,0),pt(0,0,ht*1.6),features);
          link(pt(0,0,ht*1.6),pt(5,4,0),features);
          link(pt(-3,3,0),pt(1,1,ht),features);
          link(pt(1,1,ht),pt(4,-2,0),features);
        }
      }
    }
    out.push(segmentCmd(features, track.palette[2], 1.4));
  }
  function drawRoads(out) {
    const perLane = [[], [], []], rungs = [[], [], []], edges = [[], [], []], bad = [[], [], []];
    // Filled surface strips, one polygon per run of the same surface on a route.
    const strips = [], open = [null, null, null];
    const flush = (i) => {
      const run = open[i];
      if (run && run.left.length > 1) strips.push({ type: "polygon", points: [...run.left, ...run.right.reverse()], fill: run.fill });
      open[i] = null;
    };
    const start = Math.floor((player.s - 8) / 10) * 10;
    for (let s = start; s < player.s + 370; s += 10) {
      const split = fork(s);
      const drawLanes = split > .12 ? [0, 1, 2] : [1];
      for (let i = 0; i < 3; i++) if (!drawLanes.includes(i)) flush(i);
      for (const i of drawLanes) {
        const w = split <= .12 ? 14 : widthAt(s, i);
        const d = split <= .12 ? 0 : roadCenter(s, i);
        const z = split <= .12 ? ground(s, 0) : roadHeight(s, i);
        const ld = project(point(s, d - w, z)), rd = project(point(s, d + w, z));
        const fill = (split <= .12 ? HAZARD_FILL.hard : HAZARD_FILL[hazardAt(s, i)]) + "d8";
        if (!ld || !rd) flush(i);
        else {
          if (open[i] && open[i].fill !== fill) {
            const last = [open[i].left.at(-1), open[i].right.at(-1)];
            flush(i);
            open[i] = { fill, left: [last[0]], right: [last[1]] };
          }
          if (!open[i]) open[i] = { fill, left: [], right: [] };
          open[i].left.push([ld.x, ld.y]); open[i].right.push([rd.x, rd.y]);
        }
        const lNext = project(point(s + 10, (split <= .12 ? 0 : roadCenter(s + 10, i)) - (split <= .12 ? 14 : widthAt(s + 10, i)),
          split <= .12 ? ground(s + 10, 0) : roadHeight(s + 10, i)));
        const rNext = project(point(s + 10, (split <= .12 ? 0 : roadCenter(s + 10, i)) + (split <= .12 ? 14 : widthAt(s + 10, i)),
          split <= .12 ? ground(s + 10, 0) : roadHeight(s + 10, i)));
        link(ld, lNext, perLane[i]); link(rd, rNext, perLane[i]);
        if (Math.floor(s / 10) % 2 === 0) link(ld, rd, rungs[i]);
        const danger = HAZARDS[hazardAt(s,i)].wear;
        if (danger > .1 && Math.floor(s / 10) % 5 === 0) {
          const c1 = project(point(s, d - w * .38, z + .12)), c2 = project(point(s + 7, d + w * .38, z + .12));
          link(c1, c2, bad[i]);
        }
        if (i === 1 && Math.floor(s / 10) % 4 <= 1) {
          const c = project(point(s, d, z + .07));
          const c2 = project(point(s + 10, (split <= .12 ? 0 : roadCenter(s + 10, i)), (split <= .12 ? ground(s + 10, 0) : roadHeight(s + 10, i)) + .07));
          link(c, c2, edges[i]);
        }
      }
    }
    for (let i = 0; i < 3; i++) flush(i);
    out.push(...strips.reverse()); // far strips first so nearer road paints over them
    for (let i = 0; i < 3; i++) {
      out.push(segmentCmd(rungs[i], track.palette[1], .7));
      out.push(segmentCmd(perLane[i], ROUTES[i].color, 2));
      if (edges[i].length) out.push(segmentCmd(edges[i], C.dim, 1));
      if (bad[i].length) out.push(segmentCmd(bad[i], i === 1 ? C.red : C.amber, 1.15));
    }
  }
  function worldAt(s, d, h = 0, lane = -1) {
    const base = lane < 0 ? ground(s, d) : roadHeight(s, lane);
    return point(s, d, base + h);
  }
  function drawProps(out) {
    const stone = [], danger = [], markers = [], rampsWire = [];
    const base = Math.floor((player.s - 20) / track.length);
    for (const o of obstacles) {
      for (let k = base; k <= base + 1; k++) {
        const s = k * track.length + o.s;
        if (s < player.s - 15 || s > player.s + 310) continue;
        const d = roadCenter(s, o.lane) + o.offset;
        const a = project(worldAt(s - o.radius, d - o.radius, 0, o.lane));
        const b = project(worldAt(s + o.radius, d - o.radius, 0, o.lane));
        const c = project(worldAt(s + o.radius, d + o.radius, 0, o.lane));
        const e = project(worldAt(s - o.radius, d + o.radius, 0, o.lane));
        const peak = project(worldAt(s, d, o.size, o.lane));
        const target = o.kind === "barrel" ? danger : stone;
        link(a, b, target); link(b, c, target); link(c, e, target); link(e, a, target);
        for (const q of [a, b, c, e]) link(q, peak, target);
      }
    }
    for (const ramp of ramps) {
      for (let k = base; k <= base + 1; k++) {
        const s = k * track.length + ramp.s;
        if (s < player.s - 15 || s > player.s + 260) continue;
        const d = roadCenter(s, ramp.lane), w = ROUTES[ramp.lane].width * .75;
        const a = project(worldAt(s - 6, d - w, .1, ramp.lane)), b = project(worldAt(s - 6, d + w, .1, ramp.lane));
        const c = project(worldAt(s + 5, d - w, 2.4, ramp.lane)), e = project(worldAt(s + 5, d + w, 2.4, ramp.lane));
        link(a, b, rampsWire); link(a, c, rampsWire); link(b, e, rampsWire); link(c, e, rampsWire);
      }
    }
    for (let s = Math.floor((player.s - 10) / 55) * 55; s < player.s + 350; s += 55) {
      for (const side of [-1, 1]) {
        const d = side * 58;
        link(project(worldAt(s, d, 0)), project(worldAt(s, d, 4)), markers);
      }
    }
    out.push(segmentCmd(markers, C.dim, 1.5), segmentCmd(stone, "#fb9b66", 1.6),
      segmentCmd(danger, C.red, 2), segmentCmd(rampsWire, C.yellow, 2));
  }
  function hull(points) {
    const p = points.slice().sort((a, b) => a.x - b.x || a.y - b.y), lower = [], upper = [];
    const cross = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
    for (const q of p) { while (lower.length > 1 && cross(lower.at(-2), lower.at(-1), q) <= 0) lower.pop(); lower.push(q); }
    for (const q of p.reverse()) { while (upper.length > 1 && cross(upper.at(-2), upper.at(-1), q) <= 0) upper.pop(); upper.push(q); }
    return lower.slice(0, -1).concat(upper.slice(0, -1)).map(q => [q.x, q.y]);
  }
  // style: "player" (white wire), "rival" (class color and name tag) or "ghost" (faint, no fill).
  function drawCar(r, out, style) {
    if (r.crashed && Math.floor(r.crashed * 9) % 2) return;
    const isPlayer = style === "player", isGhost = style === "ghost";
    const stat = VEHICLES[r.i], fr = frameAt(r.s);
    const a = r.yaw, fx = fr.tx * Math.cos(a) + fr.nx * Math.sin(a);
    const fy = fr.ty * Math.cos(a) + fr.ny * Math.sin(a), rx = fy, ry = -fx;
    const p = point(r.s, r.d);
    const support = surface(r.s, r.d, r.i);
    const cz = (support.on && fork(r.s) > .12 ? roadHeight(r.s, support.lane) : ground(r.s, r.d))
      + r.air + .45, hw = stat.width * .5, hl = stat.length * .5;
    const xyz = (fwd, side, height) => project({ x: p.x + fx * fwd + rx * side,
      y: p.y + fy * fwd + ry * side, z: cz + height });
    const verts = [xyz(-hl, -hw, 0), xyz(-hl, hw, 0), xyz(hl, hw, 0), xyz(hl, -hw, 0),
      xyz(-hl * .45, -hw * .8, stat.height * .70), xyz(-hl * .45, hw * .8, stat.height * .70),
      xyz(hl * .4, hw * .8, stat.height * .72), xyz(hl * .4, -hw * .8, stat.height * .72)];
    // A dark filled silhouette lets near cars hide the road and cars behind them.
    if (!isGhost && verts.every(v => v && Math.abs(v.x) < 3500 && Math.abs(v.y) < 3500))
      out.push({ type: "polygon", points: hull(verts), fill: isPlayer ? "#0a1c28e8" : "#07141cd8" });
    const segs = [], bars = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],
      [0,4],[1,5],[2,6],[3,7],[0,7],[1,6]];
    for (const [a,b] of bars) link(verts[a], verts[b], segs);
    if (stat.name === "BIG RIG" || stat.name === "RACING TRUCK") {
      const ra = xyz(-hl * .55, -hw, stat.height * .14), rb = xyz(-hl * .55, hw, stat.height * .14);
      link(ra, rb, segs); // separate hood and box cab
    }
    if (stat.name === "MONSTER TRUCK" || stat.name === "MUDPLUGGER" || stat.name === "RACING TRUCK") {
      const tireSize = stat.name === "MONSTER TRUCK" ? 1.25 : .55;
      for (const side of [-1,1]) for (const fwd of [-hl*.64, hl*.64]) {
        const a = xyz(fwd - tireSize, side * (hw + tireSize*.26), .02);
        const b = xyz(fwd + tireSize, side * (hw + tireSize*.26), .02);
        const c = xyz(fwd, side * (hw + tireSize*.26), tireSize * 1.4);
        link(a,b,segs); link(a,c,segs); link(b,c,segs);
      }
    }
    const flame = [];
    if (r.boosting && mode === "race") {
      for (const side of [-.25, .25]) link(xyz(-hl - .3, side * hw, .3), xyz(-hl - 2.4 - Math.random() * 1.6, side * hw, .3), flame);
    }
    if (flame.length) out.push(segmentCmd(flame, C.amber, 2.4));
    out.push(segmentCmd(segs, isGhost ? "#9fe6ff66" : isPlayer ? C.white : stat.color, isGhost ? 1.5 : isPlayer ? 2.7 : 1.8));
    if (style === "rival") {
      const label = xyz(0, 0, stat.height + 1.4);
      if (label && label.depth < 100) putText(out, label.x, label.y, r.persona, 10, stat.color, "center");
    } else if (isGhost) {
      const label = xyz(0, 0, stat.height + 1.4);
      if (label && label.depth < 120) putText(out, label.x, label.y, "GHOST", 10, "#9fe6ff", "center");
    }
  }
  function ghostRacer() {
    if (!ghost || raceMode !== 1 || !player) return null;
    const g = ghostAt(mode === "countdown" ? 0 : player.finished ? player.finishTime : player.time);
    return { i: ghost.vehicle, s: g.s, d: g.d, air: g.air, yaw: g.yaw, crashed: 0, boosting: false };
  }
  function drawRacers(out) {
    const g = ghostRacer();
    if (g && g.s > player.s - 36 && g.s < player.s + 390) drawCar(g, out, "ghost");
    const others = rivals.filter(a => a.s > player.s - 36 && a.s < player.s + 390)
      .sort((a,b) => b.s - a.s);
    for (const r of others) drawCar(r, out, "rival");
    drawCar(player, out, "player");
  }
  function drawExplosion(out) {
    if (explosionAge <= 0 || !explosionOrigin) return;
    const p = project(worldAt(explosionOrigin.s, explosionOrigin.d, explosionOrigin.h + 2.2));
    if (!p) return;
    const elapsed = 1.1 - explosionAge, radius = 18 + 90 * elapsed;
    const rays = [], sparks = [];
    for (let i = 0; i < 26; i++) {
      const theta = TAU * i / 26 + Math.sin(i * 9.5) * .20;
      const x = Math.cos(theta), y = Math.sin(theta);
      rays.push([p.x + x * radius * .32, p.y + y * radius * .32, p.x + x * radius * (1 + (i % 4) * .12), p.y + y * radius * (1 + (i % 4) * .12)]);
      if (i % 3 === 0) sparks.push([p.x + x * radius * .9, p.y + y * radius * .9, p.x + x * radius * 1.1, p.y + y * radius * 1.1]);
    }
    out.push(segmentCmd(rays, C.red, 3), segmentCmd(sparks, C.yellow, 2));
  }
  function trackOutline(x, y, radius, steps = 95) {
    const scale = (radius - 4) / (track.length / 5.67 * 1.32), route = [];
    for (let j = 0; j < steps; j++) {
      const a = center(j / steps * track.length), b = center((j + 1) / steps * track.length);
      link({ x: x + a.x * scale, y: y + a.y * scale }, { x: x + b.x * scale, y: y + b.y * scale }, route);
    }
    return { route, scale };
  }
  function drawRadar(out, x = 900, y = 116, radius = 44) {
    const { route, scale } = trackOutline(x, y, radius, radius > 30 ? 95 : 48);
    out.push({ type: "circle", x, y, radius: radius + 10, color: "#08202ad0" });
    out.push(segmentCmd(route, C.dim, radius > 30 ? 1.3 : 1));
    const dot = radius > 30 ? 2.2 : 1.6;
    const g = ghostRacer();
    if (g) { const p = center(g.s); out.push({ type: "circle", x: x + p.x * scale, y: y + p.y * scale, radius: dot + .6, color: "#9fe6ff" }); }
    for (const rival of rivals) {
      const p = center(rival.s);
      out.push({ type: "circle", x: x + p.x * scale, y: y + p.y * scale, radius: dot, color: VEHICLES[rival.i].color });
    }
    const here = center(player.s);
    out.push({ type: "circle", x: x + here.x * scale, y: y + here.y * scale, radius: dot * 1.8, color: C.white });
  }
  // Prompts follow the device the player used last.
  function hint(kind) {
    const k = lastInput === "touch" ? 2 : lastInput === "gamepad" ? 1 : 0;
    if (kind === "drive") return [
      "W GAS  •  A/D STEER  •  SPACE BOOST  •  S BRAKE",
      "RT GAS  •  STICK STEER  •  A BOOST  •  LT BRAKE",
      "HOLD GAS  •  SLIDE THE STEER PAD  •  BOOST WITH ANOTHER FINGER"][k];
    if (kind === "pause") return [
      "P RESUME  •  R RESET  •  G GARAGE  •  M SOUND  •  N MUSIC",
      "START RESUME  •  X RESET  •  BACK GARAGE  •  Y MUSIC",
      "TAP RESUME BELOW  •  PINCH THE VIEW TO ZOOM"][k];
    if (kind === "finish") {
      if (k === 2) return "USE THE BUTTONS BELOW";
      if (raceMode === 2) return cupFinished() ? ["G GARAGE", "BACK GARAGE"][k]
        : ["ENTER NEXT ROUND  •  G GARAGE", "START NEXT ROUND  •  BACK GARAGE"][k];
      return ["ENTER RESTART  •  E NEXT TRACK  •  G GARAGE", "START RESTART  •  A NEXT TRACK  •  BACK GARAGE"][k];
    }
    return [
      "W GAS  S BRAKE  A/D STEER  SPACE BOOST  R RESET  P PAUSE  F FULL  M SOUND  N MUSIC  G GARAGE",
      "RT GAS  LT BRAKE  STICK STEER  A BOOST  X RESET  Y MUSIC  START PAUSE  BACK GARAGE",
      ""][k];
  }
  function hud(out) {
    const stat = VEHICLES[player.i];
    const lap = Math.min(track.laps, Math.floor(player.s / track.length) + 1);
    const rank = racePosition();
    out.push({ type: "rect", x: 0, y: 0, width: W, height: 54, color: "#06151dee" });
    putText(out, 18, 18, "WIRESTORM  /  " + track.name, 18, C.cyan);
    const modeTag = raceMode === 1 ? "TIME TRIAL" : raceMode === 2 && career.active
      ? `${CUPS[career.active.cup].name} R${career.active.round + 1}` : DIFFICULTY[difficulty].name;
    putText(out, 18, 40, `${stat.name}  •  ${modeTag}  •  ${track.difficulty}`, 12, C.dim);
    if (rivals.length) putText(out, 520, 19, `P${rank} / ${rivals.length + 1}`, 20, rank <= 3 ? C.green : C.white);
    else putText(out, 500, 19, "SOLO", 20, C.cyan);
    putText(out, 628, 18, `LAP ${lap} / ${track.laps}`, 18, C.white);
    putText(out, 628, 41, moneyTime(player.time), 14, C.dim);
    putText(out, 760, 18, `LAST ${player.lastLapTime ? moneyTime(player.lastLapTime) : "--:--.--"}`, 12, C.dim);
    putText(out, 760, 40, `BEST ${Number.isFinite(player.bestLap) ? moneyTime(player.bestLap) : "--:--.--"}`, 12, C.white);
    const gap = gapText();
    if (gap) putText(out, 18, 70, gap.text, 13, gap.color);
    out.push({ type: "rect", x: 0, y: 470, width: W, height: 90, color: "#06151dee" });
    putText(out, 20, 504, String(Math.round(player.v * 3.6)).padStart(3, "0"), 39, C.white);
    putText(out, 120, 505, "KM/H", 14, C.dim);
    putText(out, 20, 542, hint("hud"), 12, C.dim);
    const lowBoost = player.boost <= 30 && !player.crashed;
    putText(out, 250, 490, lowBoost ? "BOOST / CRITICAL" : "BOOST", 13, lowBoost ? C.red : C.cyan);
    putText(out, 495, 490, "DAMAGE", 13, C.red);
    out.push({ type: "rect", x: 250, y: 507, width: 210, height: 16, color: "#163846" },
      { type: "rect", x: 251, y: 508, width: 208 * player.boost / 100, height: 14, color: lowBoost ? (Math.floor(clock * 6) % 2 ? C.red : C.yellow) : C.cyan },
      { type: "line", x1: 251 + 208 * .3, y1: 505, x2: 251 + 208 * .3, y2: 524, color: C.red, width: 2 },
      { type: "rect", x: 495, y: 507, width: 165, height: 16, color: "#411f2c" },
      { type: "rect", x: 496, y: 508, width: 163 * clamp(player.damage / 100, 0, 1), height: 14, color: C.red });
    const surf = surface(player.s, player.d, player.i);
    const laneInfo = surf.on ? `${ROUTES[surf.lane].name} / ${surf.hazard.toUpperCase()}` : "OFF COURSE";
    putText(out, 688, 492, laneInfo, 12, surf.on ? ROUTES[surf.lane].color : C.red);
    const ahead = bestRoute(player.s + 225, player.i, .7);
    putText(out, 688, 509, `BEST LINE AHEAD: ${ROUTES[ahead].name}`, 11, C.white);
    putText(out, 688, 526, muted ? "AUDIO OFF (M)" : (musicEnabled ? `MUSIC: ${SOUNDTRACKS[track.idx].name.toUpperCase()}` : "MUSIC OFF (N)"), 10, lowBoost ? C.red : C.dim);
    // Route chip for the coming fork.
    const chip = hazardPreview();
    if (chip) {
      putText(out, 500, 404, "NEXT FORK", 11, C.dim, "center");
      chip.forEach((c, i) => {
        const x = 320 + i * 124, color = RATING_COLOR[c.rating];
        out.push({ type: "rect", x, y: 416, width: 112, height: 42, color: "#06151dcc" },
          { type: "lines", segments: boxSegs(x, 416, 112, 42), color, width: c.rating === 2 ? 2.4 : 1.1 });
        putText(out, x + 56, 429, (c.rating === 2 ? "★ " : "") + c.name, 11, ROUTES[i].color, "center");
        putText(out, x + 56, 447, c.hazard.toUpperCase(), 13, color, "center");
      });
    }
    if (lowBoost && mode === "race") putText(out, 500, 138, "! BOOST CRITICAL — RELEASE BOOST !", 19, C.red, "center");
    drawRadar(out);
    if (messageTimer > 0 && mode === "race") putText(out, 500, 102, message, 21, C.yellow, "center");
    if (posFlash.timer > 0 && mode === "race") putText(out, 500, 172, posFlash.text, 26, posFlash.up ? C.green : C.red, "center");
  }

  // ---- Atmosphere and speed effects (render-time only, never affect the simulation) ----
  const fxRandom = rand(4242);
  function fxTick(dt) {
    if (!player || !dt) return;
    speedTrail = mix(speedTrail, player.boosting ? 1 : 0, clamp(dt * 3, 0, 1));
    flash = Math.max(0, flash - dt);
    // Kick-up from wheels on loose surfaces, and smoke from badly damaged cars.
    for (const r of [player, ...rivals]) {
      if (Math.abs(r.s - player.s) > 140 || r.crashed || r.finished) continue;
      const surf = surface(r.s, r.d, r.i), color = surf.on ? KICKUP[surf.hazard] : KICKUP.loose;
      if (color && r.air === 0 && r.v > 12 && fxRandom() < dt * r.v * .35) {
        const half = VEHICLES[r.i].width * .5;
        particles.push({ s: r.s - VEHICLES[r.i].length * .5, d: r.d + (fxRandom() - .5) * half * 2, h: .2,
          vs: r.v * .25, vd: (fxRandom() - .5) * 3, vh: 1.5 + fxRandom() * 2.5, g: 7, age: 0, life: .55, color });
      }
      if (r.damage > 60 && fxRandom() < dt * 9) {
        particles.push({ s: r.s, d: r.d, h: VEHICLES[r.i].height * .8, vs: r.v * .55, vd: 0,
          vh: 2 + fxRandom(), g: -1, age: 0, life: 1.1, color: "#8a9096" });
      }
    }
    for (const p of particles) { p.age += dt; p.s += p.vs * dt; p.d += p.vd * dt; p.h += p.vh * dt; p.vh -= p.g * dt; }
    particles = particles.filter(p => p.age < p.life && p.h > -.2).slice(-110);
    // Screen-space weather.
    const kind = track.air.weather, want = { snow: 70, rain: 90, storm: 90, ash: 40 }[kind] || 0;
    while (weather.length < want) weather.push({ x: fxRandom() * W, y: fxRandom() * H, z: .4 + fxRandom() * .6 });
    const drift = player.v * (kind === "snow" ? .6 : .3);
    for (const w of weather) {
      if (kind === "snow") { w.y += (40 + drift) * w.z * dt; w.x += Math.sin(clock * 1.7 + w.z * 9) * 18 * dt + (w.x - W / 2) * .002 * drift * dt; }
      else if (kind === "ash") { w.y -= (18 + w.z * 22) * dt; w.x += (Math.sin(clock + w.z * 7) * 14 + (w.x - W / 2) * .003 * drift) * dt; }
      else { w.y += (420 + drift * 3) * w.z * dt; w.x -= 70 * w.z * dt; }
      if (w.y > H) { w.y -= H; w.x = fxRandom() * W; }
      if (w.y < 0) { w.y += H; w.x = fxRandom() * W; }
      if (w.x < 0) w.x += W; else if (w.x > W) w.x -= W;
    }
    if (kind === "storm" && mode === "race") {
      thunderClock -= dt;
      if (thunderClock <= 0) { flash = .22; thunderClock = 6 + fxRandom() * 7; sfx("thunder"); }
    }
  }
  function drawEffects(out) {
    const groups = new Map();
    for (const p of particles) {
      const q = project(worldAt(p.s, p.d, p.h));
      if (!q || q.depth > 160) continue;
      const size = clamp(55 / q.depth, .8, 7) * (1 - p.age / p.life * .5);
      if (!groups.has(p.color)) groups.set(p.color, []);
      groups.get(p.color).push([q.x - size, q.y, q.x + size, q.y], [q.x, q.y - size * .6, q.x, q.y + size * .6]);
    }
    for (const [color, segs] of groups) out.push(segmentCmd(segs, color + "b0", 1.6));
    const kind = track.air.weather;
    if (weather.length) {
      const segs = weather.map(w => kind === "snow" ? [w.x, w.y, w.x + 1.6, w.y + 1.6 * w.z + 1]
        : kind === "ash" ? [w.x, w.y, w.x + 1.2, w.y - 1.8]
        : [w.x, w.y, w.x - 5 * w.z, w.y + 18 * w.z]);
      out.push(segmentCmd(segs, kind === "snow" ? "#eef6ffc8" : kind === "ash" ? "#ff8a3ad0" : "#9cc8e888",
        kind === "snow" ? 2.2 : kind === "ash" ? 1.8 : 1.1));
    }
    // Speed streaks once the car is near its top speed or boosting.
    const pace = player.v / VEHICLES[player.i].top;
    if (mode === "race" && (pace > .8 || speedTrail > .3)) {
      const streaks = [], cx = W / 2, cy = camera.horizon, n = Math.round(6 + speedTrail * 12);
      for (let i = 0; i < n; i++) {
        const angle = Math.random() * TAU, r1 = 330 + Math.random() * 160, r2 = r1 + 60 + speedTrail * 90;
        streaks.push([cx + Math.cos(angle) * r1, cy + Math.sin(angle) * r1 * .62, cx + Math.cos(angle) * r2, cy + Math.sin(angle) * r2 * .62]);
      }
      out.push(segmentCmd(streaks, "#d8f6ff55", 1.4));
    }
    if (flash > 0) {
      const alpha = Math.round(clamp(flash / .22, 0, 1) * 110).toString(16).padStart(2, "0");
      out.push({ type: "rect", x: 0, y: 0, width: W, height: H, color: "#dfe8ff" + alpha });
      if (flash > .1) {
        const bolt = [], r = rand(Math.floor(thunderClock * 1000));
        let x = 200 + r() * 600, y = 0;
        while (y < camera.horizon) { const nx = x + (r() - .5) * 60, ny = y + 18 + r() * 22; bolt.push([x, y, nx, ny]); x = nx; y = ny; }
        out.push(segmentCmd(bolt, "#ffffff", 2.6));
      }
    }
  }

  const boxSegs = (x, y, w, h) => [[x,y,x+w,y],[x+w,y,x+w,y+h],[x+w,y+h,x,y+h],[x,y+h,x,y]];
  function overlay(out, heading, caption, sub = "") {
    out.push({ type: "rect", x: 177, y: 177, width: 646, height: 178, color: "#071722e8" });
    out.push({ type: "lines", segments: boxSegs(177, 177, 646, 178), color: C.cyan, width: 2 });
    putText(out, 500, 221, heading, 38, C.white, "center");
    putText(out, 500, 276, caption, 18, C.cyan, "center");
    if (sub) putText(out, 500, 321, sub, 14, C.dim, "center");
  }
  function drawResults(out) {
    if (!results) return;
    const x = 130, y = 62, w = 740, h = 404;
    out.push({ type: "rect", x, y, width: w, height: h, color: "#061520f0" },
      { type: "lines", segments: boxSegs(x, y, w, h), color: results.pos === 1 && rivals.length ? C.yellow : C.cyan, width: 2 });
    const cupName = raceMode === 2 && cupResult ? cupResult.cup : raceMode === 2 && career.active ? CUPS[career.active.cup].name : "";
    const heading = raceMode === 1 ? "TIME TRIAL COMPLETE" : raceMode === 2 ? `${cupName} — RACE RESULT`
      : results.pos === 1 ? "VICTORY" : "RACE COMPLETE";
    putText(out, 500, y + 28, heading, 28, results.pos === 1 && rivals.length ? C.yellow : C.white, "center");
    putText(out, 500, y + 56, `${track.name}  •  ${VEHICLES[player.i].name}  •  ${raceMode === 1 ? "SOLO" : DIFFICULTY[difficulty].name}`, 12, C.dim, "center");
    if (raceMode === 1) {
      const pb = records[recordKey(track, player.i)];
      putText(out, 500, y + 120, moneyTime(player.finishTime), 44, C.white, "center");
      putText(out, 500, y + 166, `BEST LAP ${moneyTime(player.bestLap)}`, 17, C.cyan, "center");
      const delta = results.ghostDelta;
      putText(out, 500, y + 204, delta === null ? "FIRST GHOST FOR THIS TRACK" : `VS GHOST ${delta <= 0 ? "−" : "+"}${Math.abs(delta).toFixed(2)}`,
        19, delta === null || delta <= 0 ? C.green : C.red, "center");
      if (pb) putText(out, 500, y + 238, `PERSONAL BEST ${moneyTime(pb.t)}  •  RALLY RATING ${rating}`, 14, C.dim, "center");
    } else {
      const cols = [x + 26, x + 76, x + 210, x + 390, x + 520, x + 650];
      ["POS", "DRIVER", "MACHINE", "TIME", "BEST LAP", raceMode === 2 ? "PTS" : ""].forEach((label, i) =>
        putText(out, cols[i], y + 84, label, 11, C.dim));
      const rows = standings(), leader = rows[0].time;
      rows.forEach((row, i) => {
        const ry = y + 108 + i * 28, mine = row.r === player;
        if (mine) out.push({ type: "rect", x: x + 12, y: ry - 12, width: w - 24, height: 24, color: "#173c4c" });
        const color = mine ? C.white : VEHICLES[row.r.i].color;
        putText(out, cols[0], ry, `P${i + 1}`, 14, i === 0 ? C.yellow : C.white);
        putText(out, cols[1], ry, row.name, 14, color);
        putText(out, cols[2], ry, row.vehicle, 12, C.dim);
        const lapNow = Math.min(track.laps, Math.floor(row.r.s / track.length) + 1);
        const time = row.done || results.final
          ? (i === 0 ? moneyTime(row.time) : `+${(row.time - leader).toFixed(2)}`) + (row.done ? "" : " ~")
          : `RACING L${lapNow}`;
        putText(out, cols[3], ry, time, 14, row.done || results.final ? C.white : C.dim);
        putText(out, cols[4], ry, Number.isFinite(row.best) ? moneyTime(row.best) : "--", 12, C.dim);
        if (raceMode === 2) {
          const earned = results.earned ? results.earned[row.name] : POINTS[i];
          putText(out, cols[5], ry, `+${earned}${results.final ? "" : "?"}`, 13, C.green);
        }
      });
    }
    if (results.notes.length) putText(out, 500, y + h - 66, results.notes.join("  •  "), 13, C.yellow, "center");
    if (cupResult) {
      const top = cupResult.table.slice(0, 4).map(([name, pts]) => `${name} ${pts}`).join("  •  ");
      const line = cupResult.done
        ? (cupResult.won ? `${cupResult.cup} CHAMPION!${cupResult.unlocked ? `  ${cupResult.unlocked} UNLOCKED` : ""}` : `${cupResult.cup}: P${cupResult.place} OVERALL`) + `   ${top}`
        : `AFTER ROUND ${cupResult.round}/${cupResult.rounds}:  ${top}`;
      putText(out, 500, y + h - 42, line, 13, cupResult.done && cupResult.won ? C.yellow : C.cyan, "center");
    } else if (!results.final) putText(out, 500, y + h - 42, "RIVALS STILL FINISHING…", 12, C.dim, "center");
    putText(out, 500, y + h - 16, hint("finish"), 13, C.white, "center");
  }

  // ---- Garage (main, help and records pages), drawn and hit-tested on one canvas ----
  function gbtn(out, id, x, y, w, h, label, { color = C.cyan, size = 14, fill = "#12303f", active = false, disabled = false } = {}) {
    const hover = !disabled && inBox(hoverX, hoverY, { x, y, w, h });
    out.push({ type: "rect", x, y, width: w, height: h, color: hover ? "#1d4b5e" : fill });
    out.push({ type: "lines", segments: boxSegs(x, y, w, h), color: active ? color : hover ? C.white : C.dim, width: active || hover ? 2.2 : 1.1 });
    if (label) putText(out, x + w / 2, y + h / 2, label, size, disabled ? C.dim : color, "center");
    if (!disabled) garageHits.push({ x, y, w, h, id });
  }
  function garageRow(out, row, y, h, title, value, sub, accent) {
    const focus = garageFocus === row;
    garageHits.push({ x: 20, y, w: 580, h, id: `rowarea:${row}` });
    out.push({ type: "rect", x: 20, y, width: 580, height: h, color: "#0a2331" },
      { type: "lines", segments: boxSegs(20, y, 580, h), color: focus ? accent : C.faint, width: focus ? 2.4 : 1.2 });
    gbtn(out, `row:${row}:-1`, 26, y + 6, 64, h - 12, "◀", { color: accent, size: h > 64 ? 32 : 24, fill: "#183947" });
    gbtn(out, `row:${row}:1`, 530, y + 6, 64, h - 12, "▶", { color: accent, size: h > 64 ? 32 : 24, fill: "#183947" });
    putText(out, 310, y + 12, title, 11, focus ? accent : C.dim, "center");
    putText(out, 310, y + h * .5 + 2, value, h > 64 ? 22 : 19, accent, "center");
    putText(out, 310, y + h - 12, sub, 11, C.dim, "center");
  }
  function garageBackdrop(out) {
    out.push({ type: "rect", x: 0, y: 0, width: W, height: H, color: "#06121d" });
    const rays = [];
    for (let i = -13; i <= 13; i++) rays.push([W/2, 255, W/2 + i * 90, H]);
    for (let y = 270; y < H; y += (y - 245) * .22 + 15) rays.push([0, y, W, y]);
    out.push(segmentCmd(rays, C.faint, 1));
  }
  function garageMain(out) {
    putText(out, 24, 30, "W I R E S T O R M", 32, C.cyan);
    putText(out, 26, 58, "OFF-ROAD VECTOR RALLY", 12, C.white);
    gbtn(out, "help", 470, 14, 100, 40, "HELP", { size: 13 });
    gbtn(out, "records", 578, 14, 116, 40, "RECORDS", { size: 13 });
    gbtn(out, "immersive", 702, 14, 136, 40, immersive() ? "EXIT IMMERSIVE" : "IMMERSIVE", { size: 12 });
    gbtn(out, "full", 846, 14, 134, 40, ui.presentation?.fullscreen ? "EXIT FULL" : "FULLSCREEN", { size: 12 });

    garageRow(out, 0, 76, 58, `MODE  ${raceMode + 1} / ${MODES.length}`, MODES[raceMode], MODE_TAGS[raceMode].toUpperCase(), C.cyan);
    let locked = false;
    if (raceMode === 2) {
      const cup = CUPS[cupIndex], active = career.active && career.active.cup === cupIndex;
      locked = !cupUnlocked(cupIndex);
      const sub = locked ? `WIN THE ${CUPS[cupIndex - 1].name} TO UNLOCK`
        : active ? `ROUND ${career.active.round + 1}/${cup.tracks.length} NEXT: ${track.name}  •  ${career.active.points.YOU || 0} PTS`
        : cup.tracks.map(t => TRACKS[t].name.split(" ")[0]).join(" / ");
      const title = `CUP  ${cupIndex + 1} / ${CUPS.length}${career.trophies[cup.name] ? "  ★ WON" : ""}`;
      garageRow(out, 1, 140, 76, title, locked ? `${cup.name}  (LOCKED)` : cup.name, sub, locked ? C.dim : C.amber);
    } else {
      garageRow(out, 1, 140, 76, `TRACK  ${String(trackIndex + 1).padStart(2, "0")} / ${TRACKS.length}`, track.name,
        `${track.laps} LAPS  •  ${track.difficulty}`, C.amber);
    }
    const stat = VEHICLES[vehicleIndex];
    garageRow(out, 2, 222, 76, `VEHICLE  ${vehicleIndex + 1} / ${VEHICLES.length}`, stat.name, stat.tag.toUpperCase(), stat.color);
    const bars = (y, labels, values, step, width) => labels.forEach((label, i) => {
      const x = 26 + i * step;
      putText(out, x, y, label, 10, C.white);
      out.push({ type: "rect", x, y: y + 9, width, height: 7, color: C.faint },
        { type: "rect", x, y: y + 9, width: width * clamp(values[i], .03, 1), height: 7, color: stat.color });
    });
    bars(310, ["TOP SPEED", "ACCEL", "HANDLING", "WEIGHT", "TRACTION"],
      [stat.top / 68, stat.acc / 12, stat.turn / 1.35, stat.weight / 5, stat.grip / 1.2], 116, 100);
    bars(336, ["MUD", "SUSPENSION", "CLEARANCE", "STABILITY"],
      [stat.mud / 1.8, stat.suspension / 1.92, stat.clearance / 2.1, stat.stability / 1.62], 145, 126);

    // Track intel: minimap, saved times and the sector x route surface grid.
    out.push({ type: "rect", x: 612, y: 76, width: 368, height: 280, color: "#0a2331" },
      { type: "lines", segments: boxSegs(612, 76, 368, 280), color: C.faint, width: 1.2 });
    putText(out, 624, 90, "TRACK INTEL", 12, C.cyan);
    putText(out, 968, 90, track.name, 10, C.dim, "right");
    const { route, scale } = trackOutline(672, 150, 52, 80);
    out.push({ type: "circle", x: 672, y: 150, radius: 56, color: "#08202a" }, segmentCmd(route, C.amber, 1.4));
    for (let k = 0; k < 3; k++) {
      const p = center(k * track.length / 3);
      out.push({ type: "circle", x: 672 + p.x * scale, y: 150 + p.y * scale, radius: k ? 2.6 : 4, color: k ? C.cyan : C.white });
    }
    const pb = records[recordKey(track, vehicleIndex)], best = trackBest(track);
    putText(out, 740, 112, `${track.laps} LAPS  •  ${(track.length * track.laps / 1000).toFixed(1)} KM`, 12, C.white);
    putText(out, 740, 134, `YOUR BEST  ${pb ? moneyTime(pb.t) : "--:--.--"}`, 12, pb ? C.green : C.dim);
    putText(out, 740, 154, `BEST LAP   ${pb ? moneyTime(pb.l) : "--:--.--"}`, 12, pb ? C.white : C.dim);
    putText(out, 740, 174, best ? `RECORD ${moneyTime(best.t)} ${VEHICLES[best.vehicle].name}` : "NO RECORD YET", 11, best ? C.amber : C.dim);
    putText(out, 740, 194, `TRACK POINTS ${trackPoints(track)}`, 11, C.dim);
    ["RIDGE", "WASH", "HAUL"].forEach((name, i) => putText(out, 736 + i * 82, 218, name, 11, ROUTES[i].color, "center"));
    for (let k = 0; k < 3; k++) {
      const s = k * track.length / 3 + 290, rating = suitability(s, vehicleIndex), cy = 232 + k * 38;
      putText(out, 628, cy + 15, `S${k + 1}`, 12, C.dim);
      for (let lane = 0; lane < 3; lane++) {
        const cx = 736 + lane * 82, color = RATING_COLOR[rating[lane]];
        out.push({ type: "rect", x: cx - 39, y: cy, width: 78, height: 32, color: color + "26" },
          { type: "lines", segments: boxSegs(cx - 39, cy, 78, 32), color, width: rating[lane] === 2 ? 2 : 1 });
        putText(out, cx, cy + 16, (rating[lane] === 2 ? "★" : "") + hazardAt(s, lane).toUpperCase(), 11, color, "center");
      }
    }
    putText(out, 796, 348, "★ FASTEST FOR THIS MACHINE  •  AMBER CLOSE  •  RED SLOW", 9, C.dim, "center");

    const solo = raceMode === 1;
    gbtn(out, "difficulty", 20, 364, 230, 38, solo ? "DIFFICULTY: SOLO" : `DIFFICULTY: ${DIFFICULTY[difficulty].name}`,
      { size: 13, disabled: solo, active: garageFocus === 3, color: solo ? C.dim : C.yellow });
    gbtn(out, "controls", 262, 364, 230, 38, `CONTROLS: ${CONTROL_MODES[controlsMode]}${controlsMode === 0 ? (touchMode() ? " (TOUCH)" : " (KEYS)") : ""}`, { size: 13 });
    gbtn(out, "sound", 504, 364, 230, 38, muted ? "SOUND: OFF" : "SOUND: ON", { size: 13 });
    gbtn(out, "music", 746, 364, 234, 38, musicEnabled ? "MUSIC: ON" : "MUSIC: OFF", { size: 13 });

    const active = raceMode === 2 && career.active && career.active.cup === cupIndex;
    const startLabel = !ready ? "LOADING…" : locked ? "LOCKED" : raceMode === 1 ? "START TIME TRIAL  ▶"
      : raceMode === 2 ? (active ? `CONTINUE ROUND ${career.active.round + 1}  ▶` : "START CUP  ▶") : "START RACE  ▶";
    gbtn(out, "start", 320, 414, 360, 56, startLabel, { size: 23, fill: locked || !ready ? "#182a30" : "#155548", color: locked || !ready ? C.dim : C.white, active: !locked && ready, disabled: locked || !ready });
    let notice = garageNotice;
    if (!notice && raceMode === 2 && !active && career.active && career.active.cup !== cupIndex)
      notice = `STARTING ${CUPS[cupIndex].name} ABANDONS YOUR ${CUPS[career.active.cup].name} PROGRESS`;
    putText(out, 500, 488, notice || "↑↓ CHOOSE ROW  •  ←→ OR SWIPE TO CHANGE  •  ENTER START  •  H HELP  •  R RECORDS", 12, notice ? C.amber : C.dim, "center");
    const cups = CUPS.filter(c => career.trophies[c.name]).length;
    putText(out, 500, 514, `RALLY RATING ${rating}   •   RACES ${career.races}   •   WINS ${career.wins}   •   CUPS ${cups}/${CUPS.length}`, 13, C.green, "center");
    putText(out, 500, 538, `♪ ${SOUNDTRACKS[track.idx].name.toUpperCase()}`, 11, C.dim, "center");
  }
  const HELP = [
    ["CONTROLS", C.cyan, 14],
    ["KEYBOARD  W/↑ gas  •  S/↓ brake  •  A/D or ←/→ steer  •  SPACE boost  •  R reset  •  P or ENTER pause  •  G garage", C.white, 12],
    ["                  M sound  •  N music  •  F fullscreen", C.white, 12],
    ["GAMEPAD   stick or d-pad steer  •  RT gas  •  LT/B brake  •  A/RB boost  •  X reset  •  Y music  •  START pause  •  BACK garage", C.white, 12],
    ["TOUCH        slide the STEER pad  •  hold GAS, BRAKE and BOOST with separate fingers  •  pinch the view to zoom the camera", C.white, 12],
    ["ROUTES", C.cyan, 14],
    ["Every sector forks into three lines: RIDGE CUT (narrow and short), WASH LINE (middle) and HAUL ROAD (wide but longest).", C.white, 12],
    ["Each line has its own surface, and every machine copes differently with each surface.", C.white, 12],
    ["SURFACES", C.cyan, 14],
    ["Mud, sand, bog and ash reward the MUD rating  •  rock, scree, ruts, roots and ledges reward SUSPENSION.", C.white, 12],
    ["Water and snow reward CLEARANCE  •  hardpack, gravel, loose dirt and ice reward STABILITY.", C.white, 12],
    ["TRACK INTEL in the garage colors every sector for your machine. In a race, the NEXT FORK chip does the same.", C.white, 12],
    ["BOOST AND DAMAGE", C.cyan, 14],
    ["Boost adds speed but drains the tank. Below 30% the alarm sounds. At 0% the engine EXPLODES and you respawn.", C.amber, 12],
    ["Impacts, rough ground and hard landings add damage, which lowers your top speed. At 100% you wreck.", C.white, 12],
    ["A manual reset (R) puts you back at the last checkpoint but does not repair the car.", C.white, 12],
    ["MODES", C.cyan, 14],
    ["QUICK RACE: seven rivals on any track  •  TIME TRIAL: alone against your saved ghost.", C.white, 12],
    ["CHAMPIONSHIP: four-round cups scoring 10-8-6-5-4-3-2-1. Win a cup to unlock the next.", C.white, 12],
    ["RALLY RATING adds up points from your best time on every track and goes to the station leaderboard.", C.white, 12]
  ];
  function garageHelp(out) {
    putText(out, 24, 32, "HOW TO RACE", 28, C.cyan);
    gbtn(out, "back", 846, 14, 134, 40, "◀  BACK", { size: 14 });
    out.push({ type: "rect", x: 14, y: 64, width: 972, height: 422, color: "#06121de6" });
    let y = 78;
    for (const [text, color, size] of HELP) {
      if (size > 12 && y > 78) y += 6;
      putText(out, 28, y, text, size, color);
      y += size > 12 ? 20 : 19;
    }
  }
  function garageRecords(out) {
    putText(out, 24, 32, "RECORDS", 28, C.cyan);
    putText(out, 250, 34, `RALLY RATING ${rating}`, 18, C.green);
    gbtn(out, "back", 846, 14, 134, 40, "◀  BACK", { size: 14 });
    out.push({ type: "rect", x: 14, y: 66, width: 664, height: 418, color: "#06121de6" });
    ["TRACK", "BEST", "MACHINE", "BEST LAP", "PTS"].forEach((label, i) => putText(out, [28, 250, 360, 510, 620][i], 80, label, 11, C.dim));
    TRACKS.forEach((t, i) => {
      const best = trackBest(t), y = 104 + i * 25;
      putText(out, 28, y, t.name, 12, best ? C.white : C.dim);
      putText(out, 250, y, best ? moneyTime(best.t) : "--:--.--", 12, best ? C.green : C.dim);
      putText(out, 360, y, best ? VEHICLES[best.vehicle].name : "", 11, best ? VEHICLES[best.vehicle].color : C.dim);
      let lap = Infinity;
      VEHICLES.forEach((_, v) => { const r = records[recordKey(t, v)]; if (r) lap = Math.min(lap, r.l); });
      putText(out, 510, y, Number.isFinite(lap) ? moneyTime(lap) : "", 12, C.white);
      putText(out, 620, y, best ? String(trackPoints(t)) : "", 12, C.amber);
    });
    out.push({ type: "rect", x: 690, y: 66, width: 290, height: 300, color: "#0a2331" },
      { type: "lines", segments: boxSegs(690, 66, 290, 300), color: C.faint, width: 1.2 });
    putText(out, 835, 84, "STATION LEADERBOARD", 13, C.cyan, "center");
    if (!leaderboard || leaderboard.loading) putText(out, 835, 200, "LOADING…", 13, C.dim, "center");
    else if (leaderboard.error) putText(out, 835, 200, "LEADERBOARD UNAVAILABLE", 12, C.dim, "center");
    else if (!leaderboard.leaderboard.length) putText(out, 835, 200, "NO RATINGS YET — FINISH A RACE", 11, C.dim, "center");
    else leaderboard.leaderboard.slice(0, 10).forEach((row, i) => {
      const y = 112 + i * 24;
      putText(out, 706, y, `${i + 1}.`, 12, i === 0 ? C.yellow : C.dim);
      putText(out, 734, y, String(row.username).slice(0, 18), 12, C.white);
      putText(out, 966, y, String(row.score), 12, C.green, "right");
    });
    if (leaderboard && Number.isFinite(leaderboard.personal_best))
      putText(out, 835, 354, `YOUR SUBMITTED BEST ${leaderboard.personal_best}`, 11, C.dim, "center");
    putText(out, 690, 392, `RACES ${career.races}   •   WINS ${career.wins}`, 13, C.white);
    CUPS.forEach((cup, i) => putText(out, 690, 420 + i * 22, `${career.trophies[cup.name] ? "★" : cupUnlocked(i) ? "○" : "·"}  ${cup.name}${career.trophies[cup.name] ? "  WON" : cupUnlocked(i) ? "" : "  LOCKED"}`,
      12, career.trophies[cup.name] ? C.yellow : cupUnlocked(i) ? C.white : C.dim));
    putText(out, 28, 470, "RECORDS COVER EVERY MODE. EACH TRACK SCORES UP TO 1600 POINTS FROM ITS BEST TIME.", 11, C.dim);
  }
  function draw() {
    const out = [{ type: "rect", x: 0, y: 0, width: W, height: H, color: "#010a13" }];
    if (mode === "garage") {
      garageHits = [];
      garageBackdrop(out);
      if (garagePage === "help") garageHelp(out);
      else if (garagePage === "records") garageRecords(out);
      else garageMain(out);
      return out;
    }
    camera = makeCamera();
    drawSky(out); drawMountains(out); drawTerrain(out); drawLandmarks(out); drawRoads(out); drawProps(out);
    drawRacers(out); drawEffects(out); drawExplosion(out);
    if (!touchMode()) hud(out);
    if (mode === "countdown") {
      const caption = raceMode === 1 ? (ghost ? "BEAT YOUR GHOST" : "SET THE FIRST GHOST")
        : raceMode === 2 && career.active ? `ROUND ${career.active.round + 1} / ${CUPS[career.active.cup].tracks.length}  •  ${track.name}`
        : "FULL THROTTLE / FIND YOUR LINE";
      overlay(out, countdown > .5 ? String(Math.ceil(countdown)) : "GO!", caption, hint("drive"));
    } else if (mode === "paused") {
      overlay(out, "PAUSED", hint("pause"), lastInput === "touch" ? "" : "F FULLSCREEN  •  PINCH ON TOUCH SCREENS TO ZOOM");
    } else if (mode === "finish") {
      drawResults(out);
    } else if (player.crashed) {
      putText(out, 500, 173, player.blowout ? "BOOST EXPLOSION" : "WRECKED", 42, C.red, "center");
      putText(out, 500, 215, "RECOVERING TO LAST CHECKPOINT", 17, C.white, "center");
    }
    return out;
  }

  function toggleMute() {
    muted = !muted;
    if (muted) { stopMotor(); stopWarning(); ui.sound.stop(); }
    else tone(660, 100);
    saveSettings();
  }
  function toggleMusic() {
    musicEnabled = !musicEnabled;
    tone(650, 75);
    saveSettings();
  }
  function pauseRace() {
    if (mode === "race" || mode === "countdown") {
      pausedFrom = mode; mode = "paused";
      clearFingerInput(); drive = {throttle:0,brake:0,steer:0,boost:false}; stopMotor(); stopWarning();
    } else if (mode === "paused") mode = pausedFrom;
  }
  function renderGarageScreen() {
    // No menus above the game: every selector, toggle and page button is drawn and
    // hit-tested directly on the canvas (mouse with hover, touchscreen and controller).
    ui.render(
      gameCanvas(draw()),
      ui.text("Click or tap the garage, swipe across a row, or use the arrow keys and Enter. H opens How to Race; R opens Records.")
    );
  }
  function renderRaceScreen() {
    // Mobile buttons are rendered on a dedicated canvas with true per-finger
    // pointer streams. The 3D view stays completely unobstructed.
    const items = [];
    if (!touchMode()) items.push(ui.title("WIRESTORM RALLY"));
    items.push(gameCanvas(draw()));
    if (touchMode()) items.push(touchControlCanvas());
    if (!touchMode()) items.push(ui.text("KEYBOARD: WASD, Space boost, R reset, P pause, F fullscreen, N music, M mute, G garage. GAMEPAD: left stick + triggers, A boost, Start pause. Touch screens switch to separate multitouch controls automatically."));
    ui.render(...items);
  }

  let ready = !store;
  syncGarageTrack();
  renderGarageScreen();
  refreshViewport();
  // Viewport changes, orientation and fullscreen are now emitted by the host.
  // No polling loop needed while a touch control is being held.
  if (ui.presentation && typeof ui.presentation.onChange === "function") {
    ui.presentation.onChange((_mode, viewport) => {
      if (viewport) adjustViewport(viewport); else refreshViewport();
    });
  }
  if (typeof ui.onVisibility === "function") ui.onVisibility(({value}) => {
    if (value === "hidden") {
      // StationNet's gamepad touch implementation releases all virtual held keys on blur.
      if (mode === "race" || mode === "countdown") pauseRace();
      clearFingerInput();
      drive = {throttle:0,brake:0,steer:0,boost:false};
      gamepadPrev = [];
    } else refreshViewport();
  });

  const watched = new Set(["KeyW", "KeyS", "KeyA", "KeyD", "Space", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]);
  ui.onKey(({ code, phase, repeat }) => {
    if (watched.has(code)) keys[code] = phase === "down";
    if (phase !== "down") return;
    lastInput = "keyboard";
    if (repeat) return;
    if (code === "KeyM") { toggleMute(); return; }
    if (code === "KeyN") { toggleMusic(); return; }
    if (code === "KeyF") { fullscreenToggle(); return; }
    if (mode === "garage") {
      if (garagePage !== "main") {
        if (code === "KeyH") setGaragePage(garagePage === "help" ? "main" : "help");
        else if (code === "KeyR") setGaragePage(garagePage === "records" ? "main" : "records");
        else if (code === "Enter" || code === "Backspace") setGaragePage("main");
        return;
      }
      if (code === "ArrowUp" || code === "KeyW") moveGarageFocus(-1);
      else if (code === "ArrowDown" || code === "KeyS") moveGarageFocus(1);
      else if (code === "ArrowLeft" || code === "KeyA") changeGarageRow(garageFocus, -1);
      else if (code === "ArrowRight" || code === "KeyD") changeGarageRow(garageFocus, 1);
      else if (code === "Enter") startRace();
      else if (code === "KeyH") setGaragePage("help");
      else if (code === "KeyR") setGaragePage("records");
      else if (code === "KeyC") garageAction("controls");
      else if (code === "KeyI") toggleImmersive();
      return;
    }
    if (code === "ShiftRight" || code === "KeyG") { backToGarage(); return; }
    if (mode === "finish") {
      if (code === "Enter") finishAction("primary");
      else if (code === "KeyE" && finishActions().includes("NEXT")) finishAction("NEXT");
      return;
    }
    if (code === "Enter" || code === "KeyP") pauseRace();
    if (code === "KeyR" && player && (mode === "race" || mode === "paused")) resetRacer(player, true);
  });
  // 60 Hz fixed-step simulation prevents different frame rates from changing handling.
  // A separate frame callback draws once per refresh; music/SFX clocks stay real-time.
  const fixed = typeof ui.fixedStep === "function";
  if (fixed) ui.fixedStep(60, ({delta}) => { updateControls(); tick(delta); });
  ui.animate(({ delta }) => {
    const dt = clamp(delta || 0, 0, 1 / 30);
    if (!fixed && dt) { updateControls(); tick(dt); }
    if (mode !== "garage" && mode !== "paused") fxTick(dt);
    motorTick(dt);
    warningTick(dt);
    musicTick(dt);
    ui.update("race", gameCanvas(draw()));
    if (touchMode() && mode !== "garage") {
      controlRedraw += dt;
      // Refresh button highlights promptly and the BOOST warning periodically.
      if (controlRedraw >= .13) {
        ui.update("touchpad", touchControlCanvas());
        controlRedraw = 0;
      }
    }
  });
  ui.onCleanup(() => { clearFingerInput(); drive = {throttle:0,brake:0,steer:0,boost:false}; stopMotor(); stopWarning(); ui.sound.stop(); });
  await loadSaved();
  ready = true;
  syncGarageTrack();
  if (mode === "garage") relayout();
});
