// VOID TRADER - an Elite-style wireframe space trading and combat sim for StationNet.
StationNet.start(async (ui) => {
  const W = 640, H = 400, FOC = 330, NEAR = 5, MAXFUEL = 7, REFUEL = 20, BASEHOLD = 20, MAXCONTRACTS = 3, MAXPAX = 4, MAXMISSILES = 4;
  const COM = [["Food",20],["Textiles",35],["Minerals",55],["Alloys",75],["Machinery",90],["Medicine",120],["Computers",160],["Luxuries",260]];
  const ECON = [
    { name: "Agricultural", f: [.55,.75,1.2,1.15,1.25,1.1,1.3,1.05], tech: [1,5] },
    { name: "Industrial", f: [1.3,1.05,1.15,.7,.68,1,.95,1.1], tech: [3,8] },
    { name: "High-Tech", f: [1.25,1.15,1.2,1.05,.95,.72,.62,.9], tech: [5,9] },
    { name: "Mining", f: [1.35,1.2,.52,.8,1.2,1.3,1.15,1.3], tech: [2,6] }
  ];
  const DANGER = ["safe","low","moderate","high"];
  const RK = [[0,"Harmless"],[2,"Mostly Harmless"],[6,"Poor"],[12,"Average"],[20,"Above Average"],
    [32,"Competent"],[48,"Dangerous"],[64,"Deadly"],[128,"ELITE"]];
  const EVENTS = [
    { name: "Famine", text: "food and medicine in short supply", mul: { 0: 2.1, 5: 1.4 } },
    { name: "Bumper harvest", text: "food prices collapse", mul: { 0: .5 } },
    { name: "Mining boom", text: "cheap minerals, machinery wanted", mul: { 2: .5, 4: 1.35 } },
    { name: "Plague outbreak", text: "medicine urgently needed", mul: { 5: 2.2 } },
    { name: "Trade festival", text: "luxuries and textiles in demand", mul: { 7: 1.7, 1: 1.3 } },
    { name: "Tech expo", text: "computers fetch a premium", mul: { 6: 1.55 } },
    { name: "Factory strike", text: "alloys and machinery scarce", mul: { 3: 1.6, 4: 1.5 } },
    { name: "Pirate surge", text: "raiders active, danger raised", mul: {}, danger: 1 }
  ];
  const LASERS = [
    { name: "Pulse laser", dmg: 15, cool: .2, heat: 6, col: "#ff3333", tone: 900 },
    { name: "Beam laser", dmg: 10, cool: .07, heat: 4.5, col: "#ff66cc", tone: 760 },
    { name: "Military laser", dmg: 22, cool: .09, heat: 7, col: "#66ccff", tone: 620 }
  ];
  const EQUIP = [
    { id: "cargo", name: "Cargo bay extension (+15t)", price: 400, tech: 2 },
    { id: "beam", name: "Beam laser", price: 1000, tech: 4, laser: 1 },
    { id: "military", name: "Military laser", price: 2600, tech: 7, laser: 2 },
    { id: "shield", name: "Shield booster", price: 900, tech: 3 },
    { id: "scoop", name: "Fuel scoop", price: 500, tech: 3 },
    { id: "ecm", name: "ECM system", price: 700, tech: 4 },
    { id: "dock", name: "Docking computer", price: 1500, tech: 5 },
    { id: "missile", name: "Homing missile", price: 120, tech: 2 }
  ];
  const PIRATES = [
    { name: "Scout", hp: 30, s: 32, rad: 80, sp: 380, rof: 1.4, dmg: 6, bounty: [40,70], loot: 1, col: "#ff8877" },
    { name: "Raider", hp: 55, s: 40, rad: 90, sp: 330, rof: 1.1, dmg: 8, bounty: [70,120], loot: 1, col: "#ff5555" },
    { name: "Heavy", hp: 110, s: 55, rad: 120, sp: 260, rof: 1.3, dmg: 12, bounty: [150,250], loot: 2, col: "#ff3388", missiles: 1 }
  ];
  const V = {
    add: (a, b) => [a[0]+b[0], a[1]+b[1], a[2]+b[2]],
    sub: (a, b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]],
    mul: (a, s) => [a[0]*s, a[1]*s, a[2]*s],
    dot: (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2],
    cross: (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]],
    len: (a) => Math.hypot(a[0], a[1], a[2]),
    norm: (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0]/l, a[1]/l, a[2]/l]; }
  };
  ui.random.seed(Date.now());
  const rnd = () => ui.random.next();
  const rdir = () => V.norm([rnd()-.5, (rnd()-.5)*.6, rnd()-.5]);
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const round1 = (v) => Math.round(v * 10) / 10;
  const fmt = (n) => Math.round(n).toLocaleString("en-US");
  const hash = (a, b, c = 0) => { const x = Math.sin(a*12.9898 + b*78.233 + c*37.719) * 43758.5453; return x - Math.floor(x); };
  const mulberry = (a) => () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ t >>> 15, t | 1);
    t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  const basis = (f) => { f = V.norm(f); let r = V.cross([0,1,0], f); if (V.len(r) < 1e-3) r = [1,0,0]; r = V.norm(r); return [r, V.cross(f, r), f]; };
  const rot = (a, b, t) => { const c = Math.cos(t), s = Math.sin(t); return [V.add(V.mul(a,c), V.mul(b,s)), V.sub(V.mul(b,c), V.mul(a,s))]; };

  // ---- wireframe meshes (x right, y up, z forward) ----
  const pairs = (n, ok) => { const e = []; for (let i = 0; i < n; i++) for (let j = i+1; j < n; j++) if (ok(i, j)) e.push([i, j]); return e; };
  const box = (a, b, c) => { const v = []; for (let i = 0; i < 8; i++) v.push([(i&4?1:-1)*a, (i&2?1:-1)*b, (i&1?1:-1)*c]);
    return { v, e: pairs(8, (i, j) => { const x = i ^ j; return (x & (x-1)) === 0; }) }; };
  const octa = (q) => ({ v: [[q[0],0,0],[-q[1],0,0],[0,q[2],0],[0,-q[3],0],[0,0,q[4]],[0,0,-q[5]]],
    e: pairs(6, (i, j) => (i >> 1) !== (j >> 1)) });
  const STNM = { v: [], e: [] };
  for (let k = 0; k < 8; k++) { const a = k*Math.PI/4, c = Math.cos(a), s = Math.sin(a); STNM.v.push([c,s,.6],[c,s,-.6]); }
  for (let k = 0; k < 8; k++) { const n = (k+1) % 8; STNM.e.push([2*k,2*n],[2*k+1,2*n+1],[2*k,2*k+1]); }
  STNM.v.push([-.35,-.2,.6],[.35,-.2,.6],[.35,.2,.6],[-.35,.2,.6]); // docking slot
  STNM.e.push([16,17],[17,18],[18,19],[19,16]);
  const SUNM = { v: [], e: [] };
  for (let ring = 0; ring < 4; ring++) for (let k = 0; k < 18; k++) {
    const a = k * Math.PI / 9, c = Math.cos(a), s = Math.sin(a), tilt = ring * Math.PI / 4;
    SUNM.v.push([c * Math.cos(tilt), s, c * Math.sin(tilt)]); SUNM.e.push([ring*18 + k, ring*18 + (k+1) % 18]);
  }
  const SHIP = { v: [[0,0,3],[-2,0,-2],[2,0,-2],[0,.8,-2],[0,-.4,-2]],
    e: [[0,1],[0,2],[0,3],[0,4],[1,3],[2,3],[1,4],[2,4],[1,2]] };
  const MISM = { v: [[0,0,1.5],[0,0,-1.5],[.4,0,-1.5],[-.4,0,-1.5],[0,.4,-1.5],[0,-.4,-1.5]],
    e: [[0,1],[1,2],[1,3],[1,4],[1,5],[0,2],[0,3],[0,4],[0,5]] };
  const CAN = box(1, 1, 1);

  // ---- galaxy (seeded per commander) ----
  const SYL = ["la","ve","di","so","za","on","ce","le","es","ti","ri","ed","qu","at","or","re","us","an","er","bi","ge","xe","ma","in","te","ra","be","is","ar","en"];
  function makeGalaxy(seed) {
    ui.random.seed(seed);
    const out = [], names = new Set();
    const name = () => { for (;;) { let n = ""; const k = rnd() < .4 ? 3 : 2;
      for (let i = 0; i < k; i++) n += SYL[Math.floor(rnd() * SYL.length)];
      n = n[0].toUpperCase() + n.slice(1); if (n.length >= 4 && !names.has(n)) { names.add(n); return n; } } };
    const sys = (x, y, econ, danger) => { const [lo, hi] = ECON[econ].tech;
      return { name: name(), x, y, econ, tech: lo + Math.floor(rnd() * (hi - lo + 1)), danger }; };
    out.push(sys(15, 7, rnd() < .5 ? 0 : 1, 0));
    // Every new system lies within 5.5 LY of an existing one, so the whole chart is reachable by 7 LY hops.
    for (let tries = 0; out.length < 24 && tries < 6000; tries++) {
      const x = round1(1 + rnd() * 28), y = round1(1 + rnd() * 12);
      const near = Math.min(...out.map((s) => Math.hypot(s.x - x, s.y - y)));
      if (near < 2.4 || near > 5.5) continue;
      out.push(sys(x, y, Math.floor(rnd() * 4), Math.min(3, Math.floor(rnd() ** 1.3 * 4))));
    }
    ui.random.seed(Date.now());
    return out;
  }
  let G = [], HOPS = [];
  const dist = (a, b) => Math.hypot(G[a].x - G[b].x, G[a].y - G[b].y);
  function buildGalaxy(seed) {
    G = makeGalaxy(seed);
    HOPS = G.map((_, s) => { const d = G.map(() => Infinity), q = [s]; d[s] = 0;
      while (q.length) { const a = q.shift(); for (let b = 0; b < G.length; b++) if (d[b] === Infinity && dist(a, b) <= MAXFUEL) { d[b] = d[a] + 1; q.push(b); } }
      return d; });
  }
  const newSeed = () => ui.random.int(1, 2147483646);

  // ---- persistent state ----
  const fresh = (seed) => ({ v: 2, seed, day: 1, cr: 100, fuel: MAXFUEL, hull: 100, kills: 0, sys: 0, tgt: 0, cargo: {}, paid: {},
    contracts: [], taken: [], up: {}, laser: 0, missiles: 0, loan: 0, markets: {}, log: [], tutorial: false, migrated: false,
    stats: { jumps: 0, tonnes: 0, profit: 0, contracts: 0, bounty: 0, deaths: 0, best: 0 } });
  const num = (v, d, lo = -Infinity, hi = Infinity) => typeof v === "number" && Number.isFinite(v) ? clamp(v, lo, hi) : d;
  const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
  function cleanCargo(c) { const out = {}; if (isObj(c)) for (const [n] of COM) { const q = Math.floor(num(c[n], 0, 0, 99)); if (q > 0) out[n] = q; } return out; }
  function load(raw, n) {
    if (isObj(raw) && raw.v === 2 && Number.isSafeInteger(raw.seed)) {
      const s = fresh(raw.seed);
      s.day = Math.floor(num(raw.day, 1, 1)); s.cr = Math.floor(num(raw.cr, 100, 0)); s.fuel = round1(num(raw.fuel, MAXFUEL, 0, MAXFUEL));
      s.hull = Math.round(num(raw.hull, 100, 1, 100)); s.kills = Math.floor(num(raw.kills, 0, 0)); s.sys = Math.floor(num(raw.sys, 0, 0, n - 1));
      s.tgt = Math.floor(num(raw.tgt, s.sys, 0, n - 1)); s.laser = Math.floor(num(raw.laser, 0, 0, 2));
      s.missiles = Math.floor(num(raw.missiles, 0, 0, MAXMISSILES)); s.loan = Math.floor(num(raw.loan, 0, 0)); s.cargo = cleanCargo(raw.cargo);
      if (isObj(raw.paid)) for (const name of Object.keys(s.cargo)) if (Number.isFinite(raw.paid[name])) s.paid[name] = raw.paid[name];
      if (isObj(raw.up)) for (const e of EQUIP) if (raw.up[e.id] === true) s.up[e.id] = true;
      s.contracts = (Array.isArray(raw.contracts) ? raw.contracts : []).filter((c) => isObj(c) && ["freight","passenger","bounty"].includes(c.type) &&
        Number.isInteger(c.to) && c.to >= 0 && c.to < n && Number.isInteger(c.qty) && c.qty > 0 && Number.isInteger(c.reward) && Number.isInteger(c.deadline))
        .slice(0, MAXCONTRACTS).map((c) => ({ id: String(c.id), type: c.type, to: c.to, good: Math.floor(num(c.good, 0, 0, COM.length - 1)),
          qty: c.qty, reward: c.reward, deadline: c.deadline, done: Math.floor(num(c.done, 0, 0)) }));
      s.taken = (Array.isArray(raw.taken) ? raw.taken : []).map(String).slice(-30);
      if (isObj(raw.markets)) for (const [k, m] of Object.entries(raw.markets))
        if (isObj(m) && Array.isArray(m.st) && m.st.length === COM.length && Array.isArray(m.pr) && m.pr.length === COM.length)
          s.markets[k] = { d: Math.floor(num(m.d, s.day)), st: m.st.map((v) => Math.floor(num(v, 0, 0, 99))), pr: m.pr.map((v) => num(v, 0, -.45, .45)) };
      s.log = (Array.isArray(raw.log) ? raw.log : []).map(String).slice(-80);
      if (isObj(raw.stats)) for (const k of Object.keys(s.stats)) s.stats[k] = num(raw.stats[k], 0);
      s.tutorial = raw.tutorial === true;
      return s;
    }
    const s = fresh(newSeed());
    if (isObj(raw) && raw.cr !== undefined) { // Void Trader 1.x save: carry the commander into the new galaxy.
      s.cr = Math.floor(num(raw.cr, 100, 0)); s.fuel = round1(num(raw.fuel, MAXFUEL, 0, MAXFUEL)); s.hull = Math.round(num(raw.hull, 100, 1, 100));
      s.kills = Math.floor(num(raw.kills, 0, 0)); s.cargo = cleanCargo(raw.cargo); s.tutorial = true; s.migrated = true;
    }
    return s;
  }
  const [rawSave, rawSettings] = await Promise.all([ui.storage.get("save").catch(() => null), ui.storage.get("settings").catch(() => null)]);
  const set = { sound: true, vol: 70, invert: false, sens: 1 };
  if (isObj(rawSettings)) { set.sound = rawSettings.sound !== false; set.vol = num(rawSettings.vol, 70, 0, 100);
    set.invert = rawSettings.invert === true; set.sens = num(rawSettings.sens, 1, .25, 2.5); }
  else if (isObj(rawSave) && rawSave.soundOn === false) set.sound = false;
  let S = load(rawSave, 24);
  buildGalaxy(S.seed);
  S.sys = Math.min(S.sys, G.length - 1); S.tgt = Math.min(S.tgt, G.length - 1);
  const save = () => ui.storage.set("save", S).catch(() => {});
  const saveSettings = () => ui.storage.set("settings", set).catch(() => {});
  const log = (t) => { S.log.push(`Day ${S.day} · ${G[S.sys].name}: ${t}`); if (S.log.length > 80) S.log.splice(0, S.log.length - 80); };

  // ---- economy ----
  const holdCap = () => BASEHOLD + (S.up.cargo ? 15 : 0);
  const freightT = () => S.contracts.reduce((a, c) => a + (c.type === "freight" ? c.qty : 0), 0);
  const paxCount = () => S.contracts.reduce((a, c) => a + (c.type === "passenger" ? c.qty : 0), 0);
  const used = () => Object.values(S.cargo).reduce((a, b) => a + b, 0) + freightT();
  const rank = () => RK.filter((r) => S.kills >= r[0]).pop()[1];
  const eventAt = (s, d = S.day) => { const w = Math.floor((d + s * 3) / 4);
    return hash(s + 1, w, 5) < .25 ? EVENTS[Math.floor(hash(s + 1, w, 9) * EVENTS.length)] : null; };
  const dangerAt = (s) => Math.min(3, G[s].danger + (eventAt(s)?.danger || 0));
  const tgtStock = (s, i) => { const f = ECON[G[s].econ].f[i]; return Math.round((f < .8 ? 30 : f < 1.05 ? 14 : 5) * (.7 + .6 * hash(s, i, 7))); };
  function mkt(s) {
    let m = S.markets[s];
    if (!m) m = S.markets[s] = { d: S.day, st: COM.map((_, i) => tgtStock(s, i)), pr: COM.map(() => 0) };
    else if (m.d !== S.day) { // Stock recovers and trade pressure fades a little each day.
      const n = Math.max(0, S.day - m.d), k = 1 - .7 ** n, q = .6 ** n;
      m.st = m.st.map((v, i) => Math.round(v + (tgtStock(s, i) - v) * k)); m.pr = m.pr.map((v) => Math.round(v * q * 1000) / 1000); m.d = S.day;
    }
    return m;
  }
  const price = (s, i) => Math.max(1, Math.round(COM[i][1] * ECON[G[s].econ].f[i] * (.9 + .2 * hash(s, i, S.day)) *
    (eventAt(s)?.mul[i] || 1) * (1 + mkt(s).pr[i])));
  const sellP = (s, i) => Math.max(1, Math.floor(price(s, i) * .92));
  const shipValue = () => EQUIP.reduce((a, e) => a + (e.laser ? (S.laser === e.laser ? e.price : 0) : S.up[e.id] ? e.price : 0), 0) * .5 + S.missiles * 60;
  const netWorth = () => Math.max(0, Math.floor(S.cr + COM.reduce((a, c, i) => a + (S.cargo[c[0]] || 0) * sellP(S.sys, i), 0) + shipValue() - S.loan));
  const nearest = () => Math.min(...G.map((_, i) => i === S.sys ? Infinity : dist(S.sys, i)));
  const stranded = () => S.fuel < nearest() && S.cr + COM.reduce((a, c, i) => a + (S.cargo[c[0]] || 0) * sellP(S.sys, i), 0) < Math.ceil((nearest() - S.fuel) * REFUEL);
  const inRange = (i) => i !== S.sys && dist(S.sys, i) <= MAXFUEL;
  const cycle = () => { for (let i = 1; i <= G.length; i++) { const c = (S.tgt + i) % G.length; if (inRange(c)) { S.tgt = c; return; } } };
  const ensureTarget = () => { if (!inRange(S.tgt)) cycle(); };
  function offersAt(s) {
    const rng = mulberry((S.seed ^ Math.imul(s + 1, 2654435761) ^ Math.imul(S.day, 40503)) >>> 0);
    const pick = (list) => list[Math.floor(rng() * list.length)];
    const near = G.map((_, i) => i).filter((i) => i !== s && HOPS[s][i] <= 2);
    const hot = G.map((_, i) => i).filter((i) => HOPS[s][i] <= 2 && dangerAt(i) >= 1);
    const out = [];
    for (let k = 0; k < 3; k++) {
      const id = `${s}.${S.day}.${k}`, roll = rng();
      let kind = k === 2 && hot.length ? "bounty" : k === 1 && roll < .55 ? "passenger" : "freight";
      const to = kind === "bounty" ? pick(hot) : pick(near), d = dist(s, to), hops = Math.max(1, HOPS[s][to]);
      const o = { id, type: kind, to, good: Math.floor(rng() * COM.length), qty: 0, reward: 0, deadline: 0, done: 0 };
      if (kind === "freight") { o.qty = 3 + Math.floor(rng() * 6); o.deadline = S.day + hops + 2 + Math.floor(rng() * 3);
        o.reward = Math.round(50 + d * o.qty * 6 + COM[o.good][1] * o.qty * .12); }
      else if (kind === "passenger") { o.qty = 1 + Math.floor(rng() * 3); o.deadline = S.day + hops + 1 + Math.floor(rng() * 2);
        o.reward = Math.round(80 + d * 40 * o.qty); }
      else { o.qty = 2 + Math.floor(rng() * 3); o.deadline = S.day + hops + 4; o.reward = o.qty * 130 + dangerAt(to) * 60; }
      if (!S.taken.includes(id)) out.push(o);
    }
    return out;
  }
  const describe = (c) => c.type === "freight" ? `Freight: ${c.qty}t ${COM[c.good][0]} to ${G[c.to].name}`
    : c.type === "passenger" ? `Passage: ${c.qty} passenger${c.qty > 1 ? "s" : ""} to ${G[c.to].name}`
    : `Bounty: destroy ${c.qty} pirates in ${G[c.to].name}`;
  function deliver() {
    const notes = [];
    S.contracts = S.contracts.filter((c) => {
      if (c.type === "bounty" || c.to !== S.sys) return true;
      const late = S.day > c.deadline, pay = late ? Math.round(c.reward / 2) : c.reward;
      S.cr += pay; S.stats.contracts++;
      notes.push(`${describe(c)} complete${late ? " (late, half pay)" : ""}: ${fmt(pay)}cr.`); log(`${describe(c)} delivered for ${pay}cr.`);
      return false;
    });
    if (notes.length) tone(740, 150, "triangle");
    return notes;
  }
  function expire() {
    const notes = [];
    S.contracts = S.contracts.filter((c) => {
      const grace = c.type === "freight" ? 4 : c.type === "passenger" ? 2 : 0;
      if (S.day <= c.deadline + grace) return true;
      notes.push(c.type === "freight" ? `Freight contract to ${G[c.to].name} failed; the consignment was reclaimed.`
        : c.type === "passenger" ? `Your passengers for ${G[c.to].name} gave up and booked another ship.` : `Bounty contract in ${G[c.to].name} expired.`);
      log(`${describe(c)} failed.`); return false;
    });
    return notes;
  }

  // ---- flight state ----
  let mode = "dock", P = [0,0,0], R = [1,0,0], U = [0,1,0], F = [0,0,1], paused = false, dirty = true;
  let spd = 0, shield = 100, objs = [], STN = null, SUN = null, msg = "", msgT = 0, lock = null, auto = false;
  let cool = 0, laserT = 0, flash = 0, ecmFlash = 0, ecmT = 0, spawnT = 1e9, jumpT = 0, clock = 0, heat = 0, overheat = false, scooping = false;
  let mouseX = 0, mouseY = 0, mouseFire = false, prevPad = [], ctl = { yaw: 0, pitch: 0, roll: 0, thr: 0, fire: false };
  const shieldMax = () => S.up.shield ? 160 : 100;
  const dust = Array.from({ length: 45 }, () => [(rnd()-.5)*3000, (rnd()-.5)*3000, (rnd()-.5)*3000]);
  const say = (t) => { msg = t; msgT = 3.5; };
  const tone = (f, d, w = "square", v = .06) => { if (set.sound && set.vol > 0) try { ui.sound.playTone(f, d, { waveform: w, volume: v * set.vol / 100 }); } catch { /* optional */ } };

  function mkRock() {
    const rad = 60 + rnd() * 120;
    return { t: "rock", p: V.mul(rdir(), 2500 + rnd() * 8000), hp: 60, rad, s: rad, col: "#aab0b8", a: rnd() * 6, w: .2 + rnd() * .5,
      m: octa([0,0,0,0,0,0].map(() => .7 + rnd() * .5)), b: basis([0,0,1]) };
  }
  const mkCan = (p, c) => ({ t: "can", p: [...p], c, a: 0, rad: 30, s: 22, col: "#ffdd33", m: CAN, b: basis([0,0,1]) });
  function arrive() {
    STN = { t: "station", p: [0,0,0], rad: 450, s: 450, col: "#44ddff", a: 0, m: STNM, b: basis([0,0,1]) };
    const d = rdir(), sd = V.norm(V.add(V.mul(d, -1), V.mul(rdir(), .7)));
    SUN = { t: "sun", p: V.mul(sd, 16000), rad: 1600, s: 1600, col: "#ffaa33", a: 0, w: .03, m: SUNM, b: basis([0,0,1]) };
    objs = [STN, SUN]; for (let i = 0; i < 7; i++) objs.push(mkRock());
    P = V.mul(d, 9000); [R, U, F] = basis(V.mul(d, -1));
    spawnT = dangerAt(S.sys) > 0 ? 6 : 1e9; spd = 250; lock = null; auto = false;
  }
  function flightView() {
    ui.render(
      ui.canvas(W, H, [], { id: "view", label: "Cockpit view", onPointer: ({ phase }) => {
        if (phase === "start") { mouseFire = true; triggerLaser(); }
        else if (phase === "end" || phase === "cancel") mouseFire = false;
      }, touchLayout: { type: "gamepad", dpad: false, joystick: { name: "stick", x: 20, y: 50 } },
      controls: [{ label: "Fire", code: "KeyZ" }, { label: "Thrust", code: "KeyW" }, { label: "Brake", code: "KeyS" }, { label: "Dock", code: "KeyC" }] }),
      ui.menu("Flight", [
        { label: "Pause (P)", value: "pause" }, { label: "Hyperspace (J)", value: "jump" }, { label: "Next jump target (N)", value: "next" },
        { label: "Lock target (T)", value: "lock" }, { label: "Fire missile (M)", value: "missile" }, { label: "ECM (X)", value: "ecm" },
        { label: "Dock / docking computer (C)", value: "dock" }
      ], ({ value }) => flightAction(value), { id: "flight-menu" }),
      ui.row([
        ui.button("Capture mouse", async () => {
          try { await ui.pointerLock.request(); say("Mouse captured. Click to fire; Escape releases the mouse."); }
          catch (error) { ui.update("mouse-status", ui.text(`Mouse capture unavailable: ${error.message}`, { id: "mouse-status", tone: "warning" })); }
        }),
        ui.button("Release mouse", () => ui.pointerLock.release(), { variant: "secondary" })
      ]),
      ui.text("Keyboard: arrows or mouse pitch/yaw · A/D yaw · Q/E roll · W/S throttle · Space/Z/click fire · T lock · M missile · X ECM · C dock · N/J jump · P pause · I invert · F immersive", { tone: "muted" }),
      ui.text("Gamepad: left stick steer · RT/LT throttle · LB/RB roll · A fire · B missile · X lock · Y ECM · Back dock · D-pad up jump · Start pause. Touch: joystick steers; Select jumps, Start pauses.", { tone: "muted" }),
      ui.text("Capture the mouse for relative steering. Escape releases it.", { id: "mouse-status", tone: "muted" }));
  }
  function launch() {
    arrive(); P = [0, 0, -1150]; F = [0,0,-1]; U = [0,1,0]; R = V.norm(V.cross(U, F)); spd = 120; shield = shieldMax();
    mode = "fly"; paused = false; mouseFire = false; prevPad = []; cool = laserT = flash = ecmFlash = jumpT = heat = 0; overheat = false; ensureTarget();
    flightView(); say("Launch complete. Station is behind you - fly safe, Commander."); tone(330, 120, "triangle");
    anim.resume();
  }
  function dock(note = "") {
    if (mode === "fly") ui.pointerLock.release();
    mode = "dock"; anim.pause(); paused = false; auto = false; mouseFire = false; S.fuel = round1(S.fuel); S.hull = Math.max(1, Math.round(S.hull));
    const notes = [note, ...deliver()].filter(Boolean);
    if (!note) log("Docked.");
    ensureTarget(); S.stats.best = Math.max(S.stats.best, netWorth());
    ui.scores.submit(netWorth()).catch(() => {});
    stationView(notes.join(" "), /destroyed/.test(note) ? "warning" : "accent");
  }
  function die() {
    tone(60, 600, "sawtooth", .15);
    const lost = S.contracts.filter((c) => c.type !== "bounty").length;
    S.contracts = S.contracts.filter((c) => c.type === "bounty");
    S.cr = Math.floor(S.cr / 2); S.cargo = {}; S.paid = {}; S.missiles = 0; S.hull = 100; S.stats.deaths++;
    log("Ship destroyed; escape pod recovered.");
    dock(`Your ship was destroyed. An escape pod returned you to the station and insurance replaced the ship for half your credits. ` +
      `Cargo and missiles were lost${lost ? `, and ${lost} delivery contract${lost > 1 ? "s were" : " was"} cancelled` : ""}.`);
  }

  // ---- station console ----
  const TABS = ["Market", "Navigation", "Contracts", "Shipyard", "Commander"];
  let tab = 0, note = "", noteTone = "accent", tradeQty = 1, hoverSys = -1, modalOpen = false;
  let board = { status: "Open this tab to load station scores.", rows: [["—", "Not loaded", "—"]] };
  const statusLine = () => `Day ${S.day} · ${fmt(S.cr)} cr · Fuel ${S.fuel.toFixed(1)}/${MAXFUEL} LY · Hull ${Math.round(S.hull)}% · ` +
    `Hold ${used()}/${holdCap()}t · Missiles ${S.missiles} · ${rank()}${S.loan ? ` · Debt ${fmt(S.loan)} cr` : ""}`;
  const menus = () => [
    { label: "Game", items: [{ label: "Pilot's handbook", value: "help" }, { label: "Station leaderboard", value: "board" },
      { label: "Export logbook", value: "log" }, { label: "New game…", value: "new" }] },
    { label: "Options", items: [{ label: `Sound: ${set.sound ? "on" : "off"}`, value: "sound" }, { label: `Pitch: ${set.invert ? "inverted" : "normal"}`, value: "invert" }] },
    { label: "View", items: [{ label: ui.presentation.mode === "immersive" ? "Exit immersive" : "Immersive layout", value: "immersive" }, { label: "Fullscreen", value: "fullscreen" }] }
  ];
  function stationView(text = "", toneName = "accent") {
    note = text; noteTone = toneName;
    ui.render(
      ui.menuBar(menus(), ({ value }) => menuAction(value), { id: "menubar" }),
      ui.title(`${G[S.sys].name} Orbital Station`),
      ui.statusBar(statusLine(), { id: "status" }),
      ui.text(note, { id: "note", tone: noteTone }),
      ui.tabs(TABS, ({ value }) => selectTab(+value), { id: "tabs", selected: tab }),
      tabBody(),
      ui.row([ui.button("LAUNCH", requestLaunch), ui.button("Pilot's handbook", handbook, { variant: "secondary" })])
    );
    save();
  }
  function refresh(text, toneName = "accent") {
    if (text !== undefined) { note = text; noteTone = toneName; }
    ui.update("status", ui.statusBar(statusLine(), { id: "status" }));
    ui.update("note", ui.text(note, { id: "note", tone: noteTone }));
    ui.update("tab-body", tabBody());
    save();
  }
  function selectTab(i) {
    tab = clamp(i, 0, TABS.length - 1); hoverSys = -1;
    ui.update("tabs", ui.tabs(TABS, ({ value }) => selectTab(+value), { id: "tabs", selected: tab }));
    ui.update("tab-body", tabBody());
    if (tab === 4) refreshBoard();
  }
  const tabBody = () => [marketTab, navTab, contractsTab, shipyardTab, commanderTab][tab]();

  function qty(i, dir) {
    const n = COM[i][0];
    return dir > 0 ? Math.max(0, Math.min(tradeQty, mkt(S.sys).st[i], holdCap() - used(), Math.floor(S.cr / price(S.sys, i))))
      : Math.min(tradeQty, S.cargo[n] || 0);
  }
  function marketTab() {
    const s = S.sys, ev = eventAt(s), m = mkt(s);
    const rows = COM.map((c, i) => { const p = price(s, i), ratio = p / c[1];
      return [c[0], p, sellP(s, i), m.st[i], S.cargo[c[0]] || 0, S.paid[c[0]] ? Math.round(S.paid[c[0]]) : "—",
        ratio < .8 ? "cheap ▼" : ratio > 1.2 ? "dear ▲" : "average"]; });
    const best = inRange(S.tgt) ? COM.map((c, i) => ({ name: c[0], margin: sellP(S.tgt, i) - price(s, i), stock: m.st[i] }))
      .filter((r) => r.stock > 0).sort((a, b) => b.margin - a.margin)[0] : null;
    const hasCargo = Object.keys(S.cargo).length > 0;
    return ui.panel("Market", [
      ui.text(`${ECON[G[s].econ].name} economy · Tech level ${G[s].tech}${ev ? ` · NEWS: ${ev.name} — ${ev.text}` : ""}`, { tone: ev ? "warning" : "muted" }),
      ui.table(["Commodity", "Buy", "Sell", "Stock", "Hold", "Avg paid", "Local price"], rows),
      ui.slider("qty", `Trade quantity (hold ${used()}/${holdCap()}t)`, { min: 1, max: holdCap(), step: 1, value: Math.min(tradeQty, holdCap()),
        onChange: ({ value }) => { tradeQty = clamp(Math.round(value) || 1, 1, holdCap()); ui.update("tab-body", tabBody()); } }),
      ui.menu("Buy", COM.map((c, i) => { const q = qty(i, 1);
        return { label: `${c[0]} ×${q} — ${fmt(q * price(s, i))}cr`, value: String(i), disabled: q < 1 }; }), ({ value }) => trade(+value, 1)),
      ui.menu("Sell", COM.map((c, i) => { const q = qty(i, -1), gain = q * sellP(s, i), cost = S.paid[c[0]] ? Math.round(S.paid[c[0]] * q) : null;
        return { label: `${c[0]} ×${q} — ${fmt(gain)}cr${cost !== null && q ? ` (${gain - cost >= 0 ? "+" : ""}${fmt(gain - cost)})` : ""}`, value: String(i), disabled: q < 1 }; }),
        ({ value }) => trade(+value, -1)),
      ui.button("Sell all cargo", sellAll, { disabled: !hasCargo, variant: "secondary" }),
      ui.text(best ? `Best quoted margin to ${G[S.tgt].name}: ${best.name} ${best.margin >= 0 ? "+" : ""}${best.margin}cr/t. Buying raises local prices; selling lowers them, recovering over days.`
        : "Set a hyperspace target on the Navigation tab to see route margins.", { tone: "muted" })
    ], { id: "tab-body" });
  }
  function doTrade(i, dir, q) {
    const n = COM[i][0], m = mkt(S.sys), p = price(S.sys, i), sp = sellP(S.sys, i);
    if (dir > 0) {
      const have = S.cargo[n] || 0;
      S.paid[n] = ((S.paid[n] || p) * have + p * q) / (have + q);
      m.st[i] -= q; S.cr -= q * p; S.cargo[n] = have + q; m.pr[i] = Math.min(.45, m.pr[i] + .012 * q);
      S.stats.tonnes += q; log(`Bought ${q}t ${n} at ${p}cr.`);
      return `Bought ${q}t ${n} for ${fmt(q * p)}cr.`;
    }
    const gain = q * sp, profit = Math.round(gain - (S.paid[n] || sp) * q), repay = Math.min(S.loan, Math.ceil(gain * .3));
    S.cargo[n] -= q; if (!S.cargo[n]) { delete S.cargo[n]; delete S.paid[n]; }
    m.st[i] = Math.min(99, m.st[i] + q); m.pr[i] = Math.max(-.45, m.pr[i] - .015 * q);
    S.cr += gain - repay; S.loan -= repay; S.stats.tonnes += q; S.stats.profit += profit;
    log(`Sold ${q}t ${n} at ${sp}cr (${profit >= 0 ? "+" : ""}${profit}).`);
    return `Sold ${q}t ${n} for ${fmt(gain)}cr (${profit >= 0 ? "profit" : "loss"} ${fmt(Math.abs(profit))}cr)${repay ? `; ${fmt(repay)}cr repaid to the station` : ""}.`;
  }
  function trade(i, dir) {
    if (!Number.isInteger(i) || i < 0 || i >= COM.length) return;
    const q = qty(i, dir);
    if (q < 1) return refresh("Trade unavailable.", "warning");
    tone(dir > 0 ? 520 : 680, 50, "triangle", .04);
    refresh(doTrade(i, dir, q));
  }
  function sellAll() {
    const parts = [];
    COM.forEach((c, i) => { const q = S.cargo[c[0]] || 0; if (q > 0) parts.push(doTrade(i, -1, q)); });
    if (parts.length) tone(680, 80, "triangle", .04);
    refresh(parts.join(" ") || "Nothing to sell.");
  }

  // Star chart: grid units are light years.
  const CS = 19.8, CX = 24, CY = 22;
  const cpos = (i) => [CX + G[i].x * CS, CY + G[i].y * CS];
  const ring = (x, y, r, stroke) => ({ type: "polygon", points: Array.from({ length: 40 }, (_, k) =>
    [x + Math.cos(k * Math.PI / 20) * r, y + Math.sin(k * Math.PI / 20) * r]), stroke, width: 1 });
  function chartCmds() {
    const out = [{ type: "rect", x: 0, y: 0, width: 640, height: 320, color: "#07111d" }];
    const [cx, cy] = cpos(S.sys);
    out.push(ring(cx, cy, MAXFUEL * CS, "#24475c"), ring(cx, cy, Math.max(1, S.fuel) * CS, "#ddee4488"));
    const segs = [];
    for (let i = 0; i < G.length; i++) if (inRange(i)) { const [x, y] = cpos(i); segs.push([cx, cy, x, y]); }
    out.push({ type: "lines", segments: segs, color: "#1d3a4c", width: 1 });
    if (inRange(S.tgt)) { const [x, y] = cpos(S.tgt); out.push({ type: "line", x1: cx, y1: cy, x2: x, y2: y, color: "#aaccff", width: 2 }); }
    for (const c of S.contracts) { const [x, y] = cpos(c.to); out.push(ring(x, y, 11, c.type === "bounty" ? "#ff5555" : "#ffaa33")); }
    for (let i = 0; i < G.length; i++) {
      const [x, y] = cpos(i), dg = dangerAt(i);
      const color = i === S.sys ? "#55ff88" : i === S.tgt ? "#aaccff" : inRange(i) ? "#ffce68" : "#607080";
      if (dg) out.push({ type: "circle", x, y, radius: 5 + dg * 2, color: ["#0000", "#ff444422", "#ff444444", "#ff444477"][dg] });
      out.push({ type: "circle", x, y, radius: i === S.sys || i === S.tgt || i === hoverSys ? 5 : 3.5, color });
      out.push({ type: "text", x, y: y - 11, text: G[i].name, size: 9, align: "center", color });
      if (eventAt(i)) out.push({ type: "text", x: x + 8, y: y + 2, text: "!", size: 11, color: "#ff8844" });
    }
    if (hoverSys >= 0) {
      const i = hoverSys, [x, y] = cpos(i), ev = eventAt(i), bx = clamp(x + 12, 4, 640 - 214), by = clamp(y - 44, 4, 320 - 50);
      out.push({ type: "rect", x: bx, y: by, width: 210, height: 46, color: "#0b1f2ee6" },
        { type: "text", x: bx + 6, y: by + 9, text: `${G[i].name} · ${ECON[G[i].econ].name} · Tech ${G[i].tech}`, size: 10, color: "#ffffff" },
        { type: "text", x: bx + 6, y: by + 23, text: `${i === S.sys ? "You are here" : `${dist(S.sys, i).toFixed(1)} LY · ${HOPS[S.sys][i]} jump${HOPS[S.sys][i] > 1 ? "s" : ""}`} · Danger ${DANGER[dangerAt(i)]}`, size: 10, color: "#b3c5cf" },
        { type: "text", x: bx + 6, y: by + 37, text: ev ? `${ev.name}: ${ev.text}` : "No news", size: 9, color: ev ? "#ff9955" : "#71838e" });
    }
    out.push({ type: "text", x: 8, y: 310, text: "GREEN here · BLUE target · GOLD within 7 LY · rings: yellow fuel, red danger, orange/red contracts · ! news", size: 9, color: "#b3c5cf" });
    return out;
  }
  const chartHit = (x, y) => { let best = -1, bd = 12; G.forEach((_, i) => { const [sx, sy] = cpos(i), d = Math.hypot(x - sx, y - sy); if (d < bd) { bd = d; best = i; } }); return best; };
  const chartCanvas = () => ui.canvas(640, 320, chartCmds(), { id: "chart", hover: true, label: "Star chart: tap a system within 7 LY to target it",
    onPointer: ({ x, y, phase }) => {
      const i = chartHit(x, y);
      if (phase === "move" || phase === "start") {
        if (i !== hoverSys) { hoverSys = i; ui.update("chart", ui.canvas(640, 320, chartCmds(), { id: "chart", hover: true })); }
      } else if (phase === "end" && i >= 0) {
        if (inRange(i)) { S.tgt = i; hoverSys = i; refresh(`Hyperspace target set: ${G[i].name}.`); }
        else if (i !== S.sys) refresh(`${G[i].name} is ${dist(S.sys, i).toFixed(1)} LY away — beyond a single 7 LY jump. Plot a route through nearer systems.`, "warning");
      }
    } });
  function navTab() {
    const t = S.tgt, ok = inRange(t), ev = ok ? eventAt(t) : null;
    const reach = G.map((_, i) => i).filter(inRange).sort((a, b) => dist(S.sys, a) - dist(S.sys, b));
    const news = G.map((_, i) => i).filter((i) => HOPS[S.sys][i] <= 2 && eventAt(i)).map((i) => `${G[i].name}: ${eventAt(i).name} — ${eventAt(i).text}`);
    return ui.panel("Navigation", [
      chartCanvas(),
      ui.panel("Route", [ui.list(ok ? [
        `Target: ${G[t].name} — ${ECON[G[t].econ].name}, tech ${G[t].tech}, danger ${DANGER[dangerAt(t)]}`,
        `Distance ${dist(S.sys, t).toFixed(1)} LY · fuel after jump ${S.fuel >= dist(S.sys, t) ? (S.fuel - dist(S.sys, t)).toFixed(1) + " LY" : "insufficient — refuel first"}`,
        ev ? `News: ${ev.name} — ${ev.text}` : "No unusual activity reported."
      ] : ["No hyperspace target in range."])]),
      ui.menu("Hyperspace target (within 7 LY)", reach.map((i) => ({ label: `${G[i].name} ${dist(S.sys, i).toFixed(1)}LY${dangerAt(i) ? " " + "!".repeat(dangerAt(i)) : ""}${eventAt(i) ? " ★" : ""}`,
        value: String(i) })), ({ value }) => { S.tgt = +value; refresh(`Hyperspace target set: ${G[S.tgt].name}.`); }),
      ui.panel("Galactic news (within two jumps)", [ui.list(news.length ? news : ["All quiet in nearby space."])])
    ], { id: "tab-body" });
  }
  function contractsTab() {
    const offers = offersAt(S.sys);
    const rows = S.contracts.map((c) => [c.type === "freight" ? "Freight" : c.type === "passenger" ? "Passage" : "Bounty", G[c.to].name,
      c.type === "freight" ? `${c.qty}t ${COM[c.good][0]}` : c.type === "passenger" ? `${c.qty} pax` : `${c.done}/${c.qty} kills`,
      `${fmt(c.reward)}cr`, S.day > c.deadline ? "LATE" : `Day ${c.deadline} (${c.deadline - S.day} left)`]);
    const why = (o) => S.contracts.length >= MAXCONTRACTS ? "contract limit" : o.type === "freight" && holdCap() - used() < o.qty ? "hold full"
      : o.type === "passenger" && paxCount() + o.qty > MAXPAX ? "cabins full" : "";
    return ui.panel("Contracts", [
      ui.text(`Up to ${MAXCONTRACTS} contracts. Freight is loaded here and occupies hold space; passengers use the ${MAXPAX}-berth cabin; bounties pay the moment the last pirate falls. Late deliveries pay half.`, { tone: "muted" }),
      ui.table(["Type", "Destination", "Load", "Reward", "Deadline"], rows.length ? rows : [["—", "No active contracts", "", "", ""]]),
      ui.list(offers.length ? offers.map((o) => `${describe(o)} (${dist(S.sys, o.to).toFixed(1)} LY, danger ${DANGER[dangerAt(o.to)]}) — ${fmt(o.reward)}cr by day ${o.deadline}`)
        : ["The bulletin board is empty today."]),
      ui.menu("Accept offer", offers.map((o) => ({ label: `${describe(o)} — ${fmt(o.reward)}cr${why(o) ? ` (${why(o)})` : ""}`, value: o.id, disabled: !!why(o) })),
        ({ value }) => accept(offers.find((o) => o.id === value))),
      ...(S.contracts.length ? [ui.menu("Abandon contract", S.contracts.map((c, i) => ({ label: describe(c), value: String(i) })), ({ value }) => abandon(+value))] : [])
    ], { id: "tab-body" });
  }
  function accept(o) {
    if (!o || S.contracts.length >= MAXCONTRACTS) return refresh("Contract unavailable.", "warning");
    if (o.type === "freight" && holdCap() - used() < o.qty) return refresh("Not enough free hold space for that consignment.", "warning");
    if (o.type === "passenger" && paxCount() + o.qty > MAXPAX) return refresh("Not enough cabin berths.", "warning");
    S.contracts.push(o); S.taken.push(o.id); S.taken = S.taken.slice(-30);
    if (o.type !== "bounty" || o.to !== S.sys) S.tgt = inRange(o.to) ? o.to : S.tgt;
    log(`Accepted ${describe(o)} for ${o.reward}cr.`); tone(600, 80, "triangle", .05);
    refresh(`Contract accepted: ${describe(o)} by day ${o.deadline}.${o.type === "freight" ? " Consignment loaded." : ""}`);
  }
  async function abandon(i) {
    const c = S.contracts[i]; if (!c) return;
    const choice = await modal("Abandon contract?", [ui.text(`${describe(c)}. ${c.type === "freight" ? "The consignment will be returned to this station. " : ""}Your reputation is unaffected, but the reward is lost.`)],
      [{ label: "Abandon", value: "yes" }, { label: "Keep it", value: "no" }]);
    if (choice !== "yes" || S.contracts[i] !== c) return;
    S.contracts.splice(i, 1); log(`Abandoned ${describe(c)}.`); refresh(`Contract abandoned: ${describe(c)}.`, "warning");
  }
  function shipyardTab() {
    const tech = G[S.sys].tech, fullCost = Math.ceil((MAXFUEL - S.fuel) * REFUEL), repairCost = Math.ceil(100 - S.hull) * 3;
    const status = (e) => e.id === "missile" ? `${S.missiles}/${MAXMISSILES} loaded` : (e.laser ? S.laser >= e.laser : S.up[e.id]) ? "Installed"
      : e.tech > tech ? `Needs tech ${e.tech}` : "Available";
    const tradeIn = S.laser ? EQUIP.find((x) => x.laser === S.laser).price / 2 : 0;
    const cost = (e) => e.laser ? Math.max(0, Math.round(e.price - tradeIn)) : e.price;
    const services = [
      { label: `Refuel to full (${fmt(Math.min(fullCost, Math.floor(S.cr / 2) * 2))}cr)`, value: "fuel", disabled: S.fuel >= MAXFUEL - .05 || S.cr < 2 },
      { label: "Buy 1 LY of fuel (20cr)", value: "fuel1", disabled: S.fuel >= MAXFUEL - .05 || S.cr < 20 },
      { label: `Repair hull (${fmt(Math.min(repairCost, Math.floor(S.cr / 3) * 3))}cr)`, value: "repair", disabled: S.hull >= 100 || S.cr < 3 }
    ];
    if (stranded()) services.push({ label: "Request emergency assistance (loan)", value: "rescue" });
    return ui.panel("Shipyard", [
      ui.text(`${G[S.sys].name} shipyard · Tech level ${tech}. Equipment above this level is not sold here. Lasers include a 50% trade-in.`, { tone: "muted" }),
      ui.menu("Services", services, ({ value }) => service(value)),
      ui.table(["Equipment", "Price", "Status"], EQUIP.map((e) => [e.name, `${fmt(cost(e))}cr`, status(e)])),
      ui.menu("Buy equipment", EQUIP.map((e, i) => ({ label: `${e.name} — ${fmt(cost(e))}cr`, value: String(i),
        disabled: e.tech > tech || S.cr < cost(e) || (e.id === "missile" ? S.missiles >= MAXMISSILES : e.laser ? S.laser >= e.laser : !!S.up[e.id]) })),
        ({ value }) => buy(+value, cost)),
      ui.text(`Fitted: ${LASERS[S.laser].name}${EQUIP.filter((e) => !e.laser && S.up[e.id]).map((e) => `, ${e.name}`).join("")}. Ship value ${fmt(shipValue())}cr.`, { tone: "muted" })
    ], { id: "tab-body" });
  }
  function service(value) {
    if (value === "fuel" || value === "fuel1") {
      const want = value === "fuel" ? MAXFUEL - S.fuel : Math.min(1, MAXFUEL - S.fuel);
      const n = round1(Math.min(want, Math.floor(S.cr / 2) / 10));
      if (n <= 0) return refresh("Fuel unavailable.", "warning");
      S.fuel = round1(Math.min(MAXFUEL, S.fuel + n)); S.cr -= Math.round(n * REFUEL); tone(440, 60, "sine", .04);
      refresh(`Purchased ${n.toFixed(1)} LY of fuel.`);
    } else if (value === "repair") {
      const n = Math.min(Math.ceil(100 - S.hull), Math.floor(S.cr / 3));
      if (n <= 0) return refresh("Repair unavailable.", "warning");
      S.hull = Math.min(100, S.hull + n); S.cr -= n * 3; refresh(`Repaired ${n} hull points.`);
    } else if (value === "rescue" && stranded()) {
      const fuel = round1(Math.ceil((nearest() - S.fuel) * 10) / 10 + .1), loan = Math.round(fuel * REFUEL) + 100;
      S.fuel = round1(Math.min(MAXFUEL, S.fuel + fuel)); S.cr += 100; S.loan += loan; log(`Emergency assistance: ${loan}cr loan.`);
      refresh(`The station advanced you ${fuel.toFixed(1)} LY of fuel and 100cr. ${fmt(loan)}cr debt is repaid from 30% of future sales.`, "warning");
    }
  }
  function buy(i, cost) {
    const e = EQUIP[i]; if (!e) return;
    const p = cost(e);
    if (e.tech > G[S.sys].tech || S.cr < p) return refresh("Equipment unavailable.", "warning");
    if (e.id === "missile") { if (S.missiles >= MAXMISSILES) return refresh("Missile rack full.", "warning"); S.missiles++; }
    else if (e.laser) { if (S.laser >= e.laser) return refresh("Already fitted.", "warning"); S.laser = e.laser; }
    else { if (S.up[e.id]) return refresh("Already fitted.", "warning"); S.up[e.id] = true; }
    S.cr -= p; log(`Bought ${e.name} for ${p}cr.`); tone(880, 120, "triangle", .05);
    refresh(`${e.name} fitted for ${fmt(p)}cr.`);
  }
  function commanderTab() {
    const next = RK.find((r) => r[0] > S.kills), prev = RK.filter((r) => S.kills >= r[0]).pop();
    const frac = next ? (S.kills - prev[0]) / (next[0] - prev[0]) : 1, bar = "█".repeat(Math.round(frac * 12)).padEnd(12, "░");
    const st = S.stats;
    return ui.panel("Commander", [
      ui.text(`Rank ${rank()} — ${S.kills} kills  ${bar}  ${next ? `${next[0] - S.kills} to ${next[1]}` : "Maximum rank"}`, { emphasis: true }),
      ui.table(["Record", "Value"], [["Net worth", `${fmt(netWorth())}cr`], ["Best net worth", `${fmt(Math.max(st.best, netWorth()))}cr`], ["Day", S.day],
        ["Hyperspace jumps", st.jumps], ["Tonnes traded", st.tonnes], ["Trading profit", `${fmt(st.profit)}cr`], ["Contracts completed", st.contracts],
        ["Bounties collected", `${fmt(st.bounty)}cr`], ["Ships lost", st.deaths], ["Station debt", `${fmt(S.loan)}cr`]]),
      ui.panel("Settings", [
        ui.slider("sens", "Mouse sensitivity", { min: .25, max: 2.5, step: .05, value: set.sens, onChange: ({ value }) => { set.sens = clamp(+value || 1, .25, 2.5); saveSettings(); } }),
        ui.slider("vol", "Sound volume", { min: 0, max: 100, step: 5, value: set.vol, onChange: ({ value }) => { set.vol = clamp(+value || 0, 0, 100); saveSettings(); tone(660, 60); } }),
        ui.checkbox("snd", "Sound effects", { checked: set.sound, onChange: ({ value }) => toggleSetting("sound", !!value) }),
        ui.checkbox("inv", "Invert pitch", { checked: set.invert, onChange: ({ value }) => toggleSetting("invert", !!value) })
      ]),
      ui.panel("Station leaderboard — net worth", [
        ui.text(board.status, { id: "lb-status", tone: "muted" }),
        ui.table(["#", "Pilot", "Net worth"], board.rows, { id: "leaderboard" }),
        ui.button("Refresh scores", refreshBoard, { variant: "secondary" })
      ]),
      ui.row([ui.button("Export logbook", exportLog), ui.button("New game…", newGame, { variant: "secondary" })])
    ], { id: "tab-body" });
  }
  async function refreshBoard() {
    board.status = "Loading station scores…"; ui.update("lb-status", ui.text(board.status, { id: "lb-status", tone: "muted" }));
    try {
      const result = await ui.scores.submit(netWorth());
      const rows = (result.leaderboard || []).map((p, i) => [i + 1, p.username, `${fmt(p.score)}cr`]);
      board = { status: `Your best: ${fmt(result.personal_best || 0)}cr.`, rows: rows.length ? rows : [["—", "No scores yet", "—"]] };
    } catch { board = { status: "Leaderboard unavailable. Try again later.", rows: board.rows }; }
    ui.update("lb-status", ui.text(board.status, { id: "lb-status", tone: "muted" }));
    ui.update("leaderboard", ui.table(["#", "Pilot", "Net worth"], board.rows, { id: "leaderboard" }));
  }
  function toggleSetting(key, value) {
    set[key] = value; saveSettings();
    if (key === "sound" && !value) ui.sound.stop();
    if (mode === "dock") ui.update("menubar", ui.menuBar(menus(), ({ value: v }) => menuAction(v), { id: "menubar" }));
  }
  async function modal(title, components, buttons) {
    if (modalOpen) return null;
    modalOpen = true;
    try { return await ui.dialog(title, components, buttons); } finally { modalOpen = false; }
  }
  const handbook = () => modal("Pilot's handbook", [
    ui.text("Trade between systems: agricultural worlds sell cheap food, industrial worlds cheap alloys and machinery, high-tech worlds cheap computers and medicine, mining worlds cheap minerals. News events swing prices for a few days, and your own trading moves the local market."),
    ui.list(["Fly: mouse (capture it for relative steering), arrows or A/D to steer, Q/E roll, W/S throttle.",
      "Fight: Space, Z or click fires the laser — watch its temperature. T locks a target, M fires a homing missile, X triggers ECM against incoming missiles.",
      "Travel: N picks a jump target, J jumps once you are 3000u from the station. Each jump takes a day.",
      "Dock: C within 1400u of the station at speed 160 or less. A docking computer flies you in from up to 12000u.",
      "Sun: a fuel scoop refills your tank near the star — but do not fly too close.",
      "Gamepad and touch controls are listed under the cockpit view. P or Start pauses."]),
    ui.text("Contracts, equipment, your record, settings and the station leaderboard are on the station tabs. Your score is your net worth.", { tone: "muted" })
  ], [{ label: "Close", value: "close" }]);
  async function newGame() {
    const choice = await modal("Start a new game?", [ui.text("A new galaxy will be charted and your current commander, ship and contracts will be lost.")],
      [{ label: "New game", value: "new" }, { label: "Cancel", value: "cancel" }]);
    if (choice !== "new") return;
    S = fresh(newSeed()); buildGalaxy(S.seed); ensureTarget(); tab = 0; tradeQty = 1;
    log("Commission granted."); stationView("A fresh galaxy has been charted. Good luck, Commander.");
    tutorial();
  }
  async function tutorial() {
    if (S.tutorial) return;
    S.tutorial = true; save();
    await modal("Welcome, Commander", [
      ui.text(`You start at ${G[S.sys].name} with 100 credits, a Pulse laser and a 20-tonne hold.`),
      ui.list(["Market: buy goods that are cheap here and sell them where they are dear.", "Navigation: pick a system within 7 LY on the star chart, then LAUNCH.",
        "Contracts: carry freight or passengers, or hunt pirates for a bounty.", "Shipyard: refuel, repair and fit new equipment as you earn."]),
      ui.text("The Pilot's handbook lists every control.", { tone: "muted" })
    ], [{ label: "Let's fly", value: "ok" }]);
  }
  async function exportLog() {
    const text = [`VOID TRADER LOGBOOK — Commander rank ${rank()}, day ${S.day}, net worth ${fmt(netWorth())}cr`, "", ...S.log].join("\n");
    try { await ui.download.text("void-trader-logbook.txt", text); refresh("Logbook exported."); } catch { refresh("Logbook export was cancelled or unavailable.", "warning"); }
  }
  async function menuAction(value) {
    if (value === "help") handbook();
    else if (value === "board") selectTab(4);
    else if (value === "log") exportLog();
    else if (value === "new") newGame();
    else if (value === "sound") toggleSetting("sound", !set.sound);
    else if (value === "invert") toggleSetting("invert", !set.invert);
    else if (value === "immersive") toggleImmersive();
    else if (value === "fullscreen") {
      try { await ui.presentation.requestFullscreen(); } catch (error) { refresh(`Fullscreen unavailable: ${error.message}`, "warning"); }
    }
    if (value === "sound" || value === "invert") refresh(value === "sound" ? `Sound ${set.sound ? "on" : "off"}.` : `Pitch ${set.invert ? "inverted" : "normal"}.`);
  }
  const toggleImmersive = () => ui.presentation.mode === "immersive" ? ui.presentation.exitImmersive() : ui.presentation.enterImmersive();
  async function requestLaunch() {
    const warn = [];
    if (S.hull < 40) warn.push(`Hull integrity is ${Math.round(S.hull)}%.`);
    if (S.fuel < nearest()) warn.push("You do not have enough fuel to reach any other system.");
    if (warn.length) {
      const choice = await modal("Launch anyway?", [ui.text(warn.join(" ")), ui.text("The Shipyard tab offers fuel and repairs.", { tone: "muted" })],
        [{ label: "Launch", value: "go" }, { label: "Stay docked", value: "stay" }]);
      if (choice !== "go") return;
    }
    if (mode === "dock") launch();
  }

  // ---- combat ----
  function hurt(n, quiet = false) {
    if (shield > 0) { shield -= n; if (shield < 0) { S.hull += shield; shield = 0; } } else S.hull -= n;
    flash = quiet ? Math.max(flash, .06) : .15; if (!quiet) tone(110, 120, "sawtooth", .12);
    if (S.hull <= 0 && mode === "fly") die();
  }
  function spark(p) { objs.push({ t: "spark", p: [...p], life: .25 }); }
  function damage(o, n) { o.hp -= n; spark(o.p); tone(1200, 30, "triangle", .03); if (o.hp <= 0) kill(o); }
  function kill(o) {
    o.dead = 1; if (lock === o) lock = null;
    objs.push({ t: "boom", p: [...o.p], life: .7, rad: o.rad }); tone(70, 300, "sawtooth", .12);
    if (o.t === "missile") return;
    if (o.t === "pirate") {
      const T = PIRATES[o.k], b = T.bounty[0] + Math.floor(rnd() * (T.bounty[1] - T.bounty[0]));
      S.kills++; S.cr += b; S.stats.bounty += b; let text = `${T.name} destroyed! Bounty ${b}cr.`;
      for (const c of S.contracts) if (c.type === "bounty" && c.to === S.sys && c.done < c.qty) {
        c.done++;
        if (c.done >= c.qty) { S.cr += c.reward; S.stats.contracts++; text = `Bounty contract complete! ${fmt(c.reward)}cr credited.`; log(`${describe(c)} completed for ${c.reward}cr.`); tone(740, 150, "triangle"); }
        else text += ` Contract ${c.done}/${c.qty}.`;
        break;
      }
      S.contracts = S.contracts.filter((c) => c.type !== "bounty" || c.done < c.qty);
      say(text);
      const n = T.loot === 2 ? 1 + Math.floor(rnd() * 2) : rnd() < .6 ? 1 : 0;
      for (let i = 0; i < n; i++) objs.push(mkCan(V.add(o.p, V.mul(rdir(), 40 * i)), COM[Math.floor(rnd() * (o.k === 2 ? COM.length : 5))][0]));
    } else if (rnd() < .5) objs.push(mkCan(o.p, rnd() < .25 ? "Alloys" : "Minerals"));
  }
  function fire(dmg) {
    let best = null, bt = 4000;
    for (const o of objs) { if (o.t !== "pirate" && o.t !== "rock" && !(o.t === "missile" && o.own === "pirate")) continue;
      const d = V.sub(o.p, P), t = V.dot(d, F); if (t < 0 || t > bt) continue;
      if (V.len(V.sub(d, V.mul(F, t))) < o.rad) { bt = t; best = o; } }
    if (best) damage(best, dmg);
  }
  function triggerLaser() {
    if (mode !== "fly" || paused || cool > 0 || overheat) return;
    const L = LASERS[S.laser];
    cool = L.cool; laserT = .06; heat += L.heat; tone(L.tone, 50); fire(L.dmg);
    if (heat >= 100) { heat = 100; overheat = true; say("Laser overheated! Let it cool."); tone(150, 220, "sawtooth", .08); }
  }
  function cycleLock() {
    const list = objs.filter((o) => o.t === "pirate" && !o.dead).sort((a, b) => V.len(V.sub(a.p, P)) - V.len(V.sub(b.p, P)));
    if (!list.length) { lock = null; return say("No hostile contacts."); }
    lock = list[(list.indexOf(lock) + 1) % list.length]; say(`Locked: ${PIRATES[lock.k].name} at ${Math.round(V.len(V.sub(lock.p, P)))}u.`); tone(1000, 40);
  }
  function fireMissile() {
    if (S.missiles < 1) return say("No missiles. Buy them at a shipyard.");
    if (!lock || lock.dead) return say("No target locked (T).");
    S.missiles--; tone(300, 300, "sawtooth", .07);
    objs.push({ t: "missile", own: "player", tgt: lock, p: V.add(P, V.mul(F, 60)), v: V.mul(F, spd + 200), life: 7, hp: 10, rad: 25, s: 12, col: "#88ffcc", m: MISM, b: basis(F) });
    say(`Missile away! ${S.missiles} left.`);
  }
  function triggerEcm() {
    if (!S.up.ecm) return say("No ECM system fitted.");
    if (ecmT > 0) return say(`ECM recharging (${ecmT.toFixed(1)}s).`);
    let n = 0; for (const o of objs) if ((o.t === "missile" && o.own === "pirate") || o.t === "shot") { if (V.len(V.sub(o.p, P)) < 5000) { o.dead = 1; n++; } }
    ecmT = 6; ecmFlash = .3; tone(1500, 250, "sine", .07); say(n ? `ECM pulse destroyed ${n} projectile${n > 1 ? "s" : ""}.` : "ECM pulse.");
  }
  function requestDock() {
    if (auto) { auto = false; return say("Docking computer disengaged."); }
    const d = V.len(P);
    if (d <= 1400 && spd <= 160) return dock();
    if (S.up.dock && d < 12000) { auto = true; return say("Docking computer engaged. Press C to cancel."); }
    say(d > 1400 ? `Too far from the station (${Math.round(d)}u; need 1400u).` : "Reduce speed to 160 or less to dock.");
  }
  function tryJump() {
    if (jumpT > 0) return;
    if (!inRange(S.tgt)) return say("No target system in range (N).");
    const d = dist(S.sys, S.tgt); if (d > S.fuel + 1e-6) return say("Insufficient fuel for that jump.");
    if (V.len(P) < 3000) return say("Mass-locked by station. Move 3000+ units away.");
    S.fuel = round1(S.fuel - d); jumpT = 1.6; auto = false; tone(200, 1400, "sine", .1);
  }
  function setPause(on, text) {
    if (mode !== "fly" || paused === on) return;
    paused = on; dirty = true; mouseFire = false;
    if (on) { ui.pointerLock.release(); say(text || "Paused."); } else say("Resumed.");
  }
  function flightAction(value) {
    if (mode !== "fly") return;
    if (value === "pause") return setPause(!paused);
    if (paused) return;
    if (value === "jump") tryJump();
    else if (value === "next") { cycle(); say(inRange(S.tgt) ? `Jump target: ${G[S.tgt].name} ${dist(S.sys, S.tgt).toFixed(1)} LY` : "No systems in range."); }
    else if (value === "lock") cycleLock();
    else if (value === "missile") fireMissile();
    else if (value === "ecm") triggerEcm();
    else if (value === "dock") requestDock();
  }

  function ai(o, dt, d, dd) {
    const T = PIRATES[o.k], n = V.mul(d, 1 / dd);
    if (o.hp < o.max * .3) { // Badly damaged pirates break off and run.
      o.v = V.add(o.v, V.mul(V.sub(V.mul(n, -T.sp * 1.1), o.v), Math.min(1, dt * 1.5)));
      o.p = V.add(o.p, V.mul(o.v, dt)); o.b = basis(o.v);
      if (dd > 7000) { o.dead = 1; if (lock === o) lock = null; say(`${T.name} escaped.`); }
      return;
    }
    const sp = dd > 700 ? T.sp : T.sp * .55;
    const side = V.mul(V.cross(n, [0,1,0]), Math.sin(clock * 1.3 + o.a) * 120);
    o.v = V.add(o.v, V.mul(V.sub(V.add(V.mul(n, sp), side), o.v), Math.min(1, dt * 1.5)));
    o.p = V.add(o.p, V.mul(o.v, dt)); o.b = basis(o.v); o.f -= dt;
    if (dd < 1800 && o.f <= 0) { o.f = T.rof + rnd();
      objs.push({ t: "shot", dmg: T.dmg, p: [...o.p], v: V.add(V.mul(n, 850), [(rnd()-.5)*120, (rnd()-.5)*120, (rnd()-.5)*120]), life: 3 }); }
    if (o.mis > 0 && dd < 3200 && dd > 900 && rnd() < dt * .12) {
      o.mis--; objs.push({ t: "missile", own: "pirate", p: [...o.p], v: V.mul(n, 300), life: 9, hp: 10, rad: 25, s: 12, col: "#ff8800", m: MISM, b: basis(n) });
      say(S.up.ecm ? "INCOMING MISSILE! Press X for ECM or shoot it down." : "INCOMING MISSILE! Shoot it down or outrun it."); tone(1300, 400, "square", .07);
    }
  }
  function spawnPirate() {
    const dg = dangerAt(S.sys), roll = rnd() * (dg + 1.5), k = roll > 3 ? 2 : roll > 1.3 ? 1 : 0, T = PIRATES[k], dir = rdir();
    objs.push({ t: "pirate", k, p: V.add(P, V.mul(dir, 3500)), v: V.mul(dir, -50), hp: T.hp, max: T.hp, rad: T.rad, s: T.s,
      col: T.col, a: rnd() * 6, f: 2, mis: T.missiles || 0, m: SHIP, b: basis(dir) });
    say(`Hostile contact: ${T.name}!`); tone(300, 200, "triangle", .1);
  }

  function readControls() {
    const k = (c) => ui.input.isDown(c), stick = ui.input.axis("stick"), pad = ui.input.gamepad(0, { deadzone: .15 });
    const b = (i) => pad.buttons[i] || 0, axis = (i) => pad.axes[i] || 0, touch = stick.x !== 0 || stick.y !== 0;
    // The touch joystick also holds arrow keys past 0.4; read its analog value instead of counting both.
    const yaw = (touch ? stick.x : (k("ArrowRight") || k("KeyD") ? 1 : 0) - (k("ArrowLeft") || k("KeyA") ? 1 : 0)) + axis(0);
    const pitch = (touch ? -stick.y : (k("ArrowUp") ? 1 : 0) - (k("ArrowDown") ? 1 : 0)) - axis(1);
    const roll = (k("KeyE") ? 1 : 0) - (k("KeyQ") ? 1 : 0) + (b(5) > .5 ? 1 : 0) - (b(4) > .5 ? 1 : 0) + axis(2);
    const thr = (k("KeyW") ? 1 : 0) - (k("KeyS") ? 1 : 0) + b(7) - b(6);
    ctl = { yaw: clamp(yaw, -1, 1), pitch: clamp(pitch, -1, 1), roll: clamp(roll, -1, 1), thr: clamp(thr, -1, 1),
      fire: k("Space") || k("KeyZ") || mouseFire || b(0) > .5 };
    const edges = [[1, "missile"], [2, "lock"], [3, "ecm"], [8, "dock"], [9, "pause"], [12, "jump"], [13, "next"]];
    const now = edges.map(([i]) => b(i) > .5);
    edges.forEach(([, name], j) => { if (now[j] && !prevPad[j]) flightAction(name); });
    prevPad = now;
  }
  function step(dt) {
    clock += dt;
    const a = 1.5 * dt;
    if (auto) {
      const to = V.norm(V.mul(P, -1)), dd = V.len(P);
      F = V.norm(V.add(F, V.mul(V.sub(to, F), Math.min(1, dt * 2)))); R = V.norm(V.cross(U, F)); U = V.cross(F, R);
      spd += (clamp((dd - 900) * .5, 110, 480) - spd) * Math.min(1, dt * 2);
      mouseX = mouseY = 0;
    } else {
      const pitch = (ctl.pitch * a - clamp(mouseY, -200, 200) * .0025 * set.sens) * (set.invert ? -1 : 1);
      const yaw = ctl.yaw * a * .6 + clamp(mouseX, -200, 200) * .0025 * set.sens;
      mouseX = mouseY = 0;
      [F, U] = rot(F, U, pitch);
      [U, R] = rot(U, R, ctl.roll * a);
      [F, R] = rot(F, R, yaw);
      F = V.norm(F); R = V.norm(V.cross(U, F)); U = V.cross(F, R);
      if (ctl.thr) spd = clamp(spd + 260 * dt * ctl.thr, 0, 500);
    }
    P = V.add(P, V.mul(F, spd * dt));
    for (const d of dust) for (let i = 0; i < 3; i++) d[i] = P[i] + ((d[i] - P[i] + 1500) % 3000 + 3000) % 3000 - 1500;
    cool -= dt; laserT -= dt; flash -= dt; ecmFlash -= dt; msgT -= dt; ecmT = Math.max(0, ecmT - dt);
    shield = Math.min(shieldMax(), shield + (S.up.shield ? 4 : 2) * dt);
    heat = Math.max(0, heat - 30 * dt); if (overheat && heat < 40) { overheat = false; say("Laser cooled."); }
    if (ctl.fire) triggerLaser();
    // Sun: scoop fuel in the corona, take heat damage too close in.
    const sd = V.len(V.sub(P, SUN.p)); scooping = false;
    if (sd < SUN.rad * 2.2) {
      if (S.up.scoop && S.fuel < MAXFUEL) { S.fuel = Math.min(MAXFUEL, S.fuel + .25 * dt); scooping = true; }
      if (sd < SUN.rad * 1.5) { hurt(14 * dt, true); if (msgT <= 0) say("Hull temperature critical! Pull away from the star."); }
      if (mode !== "fly") return;
    }
    spawnT -= dt;
    if (spawnT <= 0) { const dg = dangerAt(S.sys); spawnT = (20 + rnd() * 30) / (dg || 1);
      if (V.len(P) > 2500 && objs.filter((o) => o.t === "pirate").length < 2 + Math.min(2, dg)) spawnPirate(); }
    for (const o of objs) { if (o.dead) continue;
      const d = V.sub(P, o.p), dd = V.len(d);
      if (o.t === "can") { o.a += dt; o.b = basis([Math.sin(o.a), .3, Math.cos(o.a)]);
        if (dd < 130) { if (used() < holdCap()) { S.cargo[o.c] = (S.cargo[o.c] || 0) + 1; say(`Scooped 1t ${o.c}.`); o.dead = 1; tone(660, 80, "sine", .08); } else if (msgT <= 0) say("Hold full!"); }
        continue; }
      if (o.t === "shot") { o.p = V.add(o.p, V.mul(o.v, dt)); if ((o.life -= dt) <= 0) o.dead = 1; else if (dd < 70) { o.dead = 1; hurt(o.dmg || 8); } continue; }
      if (o.t === "boom" || o.t === "spark") { if ((o.life -= dt) <= 0) o.dead = 1; continue; }
      if (o.t === "missile") {
        const target = o.own === "player" ? o.tgt : null;
        if (o.own === "player" && (!target || target.dead)) o.life = Math.min(o.life, .4);
        const tp = target && !target.dead ? target.p : o.own === "pirate" ? P : V.add(o.p, o.v), to = V.sub(tp, o.p), td = V.len(to);
        o.v = V.add(o.v, V.mul(V.sub(V.mul(V.norm(to), o.own === "player" ? 750 : 560), o.v), Math.min(1, dt * 2.2)));
        o.p = V.add(o.p, V.mul(o.v, dt)); o.b = basis(o.v);
        if ((o.life -= dt) <= 0) kill(o);
        else if (target && !target.dead && td < target.rad + 20) { kill(o); damage(target, 80); }
        else if (o.own === "pirate" && dd < 80) { kill(o); hurt(35); }
        continue;
      }
      if (o.t === "pirate") ai(o, dt, d, dd);
      else { o.a += dt * (o.w || .15); const c = Math.cos(o.a), s = Math.sin(o.a);
        o.b = o.t === "station" ? [[c,s,0],[-s,c,0],[0,0,1]] : [[c,0,-s],[0,1,0],[s,0,c]]; }
      if (!o.dead && dd < o.rad + 30) { hurt(Math.max(8, spd * .08)); P = V.add(o.p, V.mul(V.norm(d), o.rad + 32)); spd *= .3; auto = false;
        if (o.t === "pirate") damage(o, 30); }
      if (mode !== "fly") return;
    }
    objs = objs.filter((o) => !o.dead);
    if (lock && lock.dead) lock = null;
    if (auto && V.len(P) < 1300 && spd <= 160) return dock();
    if (jumpT > 0 && (jumpT -= dt) <= 0) {
      jumpT = 0; S.sys = S.tgt; S.day++; S.stats.jumps++; cycle(); arrive();
      const ev = eventAt(S.sys), notes = expire();
      say([`Arrived in ${G[S.sys].name}. Day ${S.day}.${ev ? ` News: ${ev.name}.` : ""}`, ...notes].join(" "));
      log(`Arrived by hyperspace.`); tone(520, 200, "triangle", .06); save();
    }
  }

  // ---- rendering ----
  const cam = (p) => { const d = V.sub(p, P); return [V.dot(d, R), V.dot(d, U), V.dot(d, F)]; };
  const scr = (c) => [W/2 + c[0] / c[2] * FOC, H/2 - c[1] / c[2] * FOC];
  function seg(out, a, b) {
    if (a[2] < NEAR && b[2] < NEAR) return;
    if (a[2] < NEAR) { const t = (NEAR - a[2]) / (b[2] - a[2]); a = [a[0] + (b[0]-a[0]) * t, a[1] + (b[1]-a[1]) * t, NEAR]; }
    else if (b[2] < NEAR) { const t = (NEAR - b[2]) / (a[2] - b[2]); b = [b[0] + (a[0]-b[0]) * t, b[1] + (a[1]-b[1]) * t, NEAR]; }
    const s = scr(a), e = scr(b); out.push([s[0], s[1], e[0], e[1]]);
  }
  function draw() {
    const out = [], G2 = {}, g = (c) => G2[c] || (G2[c] = []);
    const tx = (x, y, text, color = "#55ff88", size = 11, align = "left") => out.push({ type: "text", x, y, text, size, align, color });
    const bar = (x, y, label, f, color) => { out.push({ type: "rect", x, y, width: 90, height: 7, color: "#10222a" },
      { type: "rect", x, y, width: 90 * clamp(f, 0, 1), height: 7, color }); tx(x, y - 8, label, color, 9); };
    const edge = (p, label, color) => { const c = cam(p), sx = c[0] / c[2] * FOC, sy = c[1] / c[2] * FOC;
      if (c[2] > 1 && Math.abs(sx) < W/2 - 20 && Math.abs(sy) < H/2 - 20) return;
      const an = Math.atan2(c[1], c[0]); tx(W/2 + Math.cos(an) * (W/2 - 34), H/2 - Math.sin(an) * (H/2 - 70), label, color, 12, "center"); };
    for (const d of dust) { const c = cam(d); if (c[2] > 1) seg(g("#778899"), c, cam(V.sub(d, V.mul(F, spd * .03 + 2)))); }
    for (const o of objs) {
      if (o.t === "shot") { seg(g("#ff4444"), cam(o.p), cam(V.sub(o.p, V.mul(o.v, .05)))); continue; }
      if (o.t === "boom") { const c = cam(o.p); if (c[2] > 1) { const s = scr(c), r = (80 + 500 * (.7 - o.life)) / c[2] * FOC;
        out.push({ type: "circle", x: s[0], y: s[1], radius: r, color: "#ff880066" }, { type: "circle", x: s[0], y: s[1], radius: r * .55, color: "#ffee0099" }); } continue; }
      if (o.t === "spark") { const c = cam(o.p); if (c[2] > 1) { const s = scr(c), r = clamp(90 / c[2] * FOC, 3, 30);
        g("#ffee88").push([s[0] - r, s[1], s[0] + r, s[1]], [s[0], s[1] - r, s[0], s[1] + r], [s[0] - r*.6, s[1] - r*.6, s[0] + r*.6, s[1] + r*.6]); } continue; }
      if (o.t !== "station" && o.t !== "sun" && V.len(V.sub(o.p, P)) > 25000) continue;
      const [r, u, f] = o.b;
      const cv = o.m.v.map((v) => cam([o.p[0] + (r[0]*v[0] + u[0]*v[1] + f[0]*v[2]) * o.s,
        o.p[1] + (r[1]*v[0] + u[1]*v[1] + f[1]*v[2]) * o.s, o.p[2] + (r[2]*v[0] + u[2]*v[1] + f[2]*v[2]) * o.s]));
      for (const e of o.m.e) seg(g(o.col), cv[e[0]], cv[e[1]]);
    }
    if (jumpT > 0) { const q = 1.6 - jumpT; for (let i = 0; i < 40; i++) { const an = i * .157, r0 = 30 + q * 120, r1 = r0 + 40 + q * 300;
      g("#aaccff").push([W/2 + Math.cos(an) * r0, H/2 + Math.sin(an) * r0, W/2 + Math.cos(an) * r1, H/2 + Math.sin(an) * r1]); } }
    // Target lock bracket and health bar.
    if (lock && !lock.dead) {
      const c = cam(lock.p);
      if (c[2] > NEAR) { const [x, y] = scr(c), k = clamp(lock.rad / c[2] * FOC * 1.3, 10, 90), q = k * .4;
        g("#ff3355").push([x-k, y-k, x-k+q, y-k], [x-k, y-k, x-k, y-k+q], [x+k, y-k, x+k-q, y-k], [x+k, y-k, x+k, y-k+q],
          [x-k, y+k, x-k+q, y+k], [x-k, y+k, x-k, y+k-q], [x+k, y+k, x+k-q, y+k], [x+k, y+k, x+k, y+k-q]);
        out.push({ type: "rect", x: x - 20, y: y - k - 9, width: 40, height: 4, color: "#3a1018" },
          { type: "rect", x: x - 20, y: y - k - 9, width: 40 * clamp(lock.hp / lock.max, 0, 1), height: 4, color: "#ff3355" });
        tx(x, y + k + 9, `${PIRATES[lock.k].name.toUpperCase()} ${Math.round(V.len(V.sub(lock.p, P)))}u`, "#ff7788", 9, "center"); }
    }
    for (const c in G2) out.push({ type: "lines", segments: G2[c], color: c, width: 1.2 });
    if (laserT > 0) out.push({ type: "lines", segments: [[120, H, W/2, H/2], [W-120, H, W/2, H/2]], color: LASERS[S.laser].col, width: 3 });
    out.push({ type: "lines", segments: [[W/2-14, H/2, W/2-5, H/2], [W/2+5, H/2, W/2+14, H/2], [W/2, H/2-14, W/2, H/2-5], [W/2, H/2+5, W/2, H/2+14]], color: "#55ff88", width: 1 });
    edge(STN.p, "STN", "#44ddff"); edge(SUN.p, "SUN", "#ffaa33"); if (lock && !lock.dead) edge(lock.p, "TGT", "#ff3355");
    // HUD
    const sd = V.len(P);
    tx(10, 14, `${G[S.sys].name}  DAY ${S.day}  ${fmt(S.cr)}cr`); tx(W - 10, 14, `${rank()}  Kills ${S.kills}`, "#55ff88", 11, "right");
    tx(W/2, 14, `STATION ${Math.round(sd)}u${auto ? "  AUTO-DOCK" : sd < 1400 ? (spd <= 160 ? "  [C] DOCK" : "  SLOW TO DOCK") : ""}`, "#44ddff", 11, "center");
    if (msgT > 0) tx(W/2, 40, msg, "#ffee66", 13, "center");
    const c0 = S.contracts[0];
    if (c0) tx(W/2, 56, `${c0.type.toUpperCase()} ${c0.type === "bounty" ? `${c0.done}/${c0.qty} in` : "->"} ${G[c0.to].name} · day ${c0.deadline}${S.contracts.length > 1 ? ` (+${S.contracts.length - 1})` : ""}`, "#ffce68", 10, "center");
    if (scooping) tx(W/2, 72, "FUEL SCOOP ACTIVE", "#ffaa33", 10, "center");
    bar(10, 305, overheat ? "LASER OVERHEAT" : "LASER TEMP", heat / 100, overheat || heat > 75 ? "#ff4444" : "#cc88ff");
    bar(10, 330, "SPEED", spd / 500, "#55ff88"); bar(10, 355, "SHIELD", shield / shieldMax(), "#44aaff"); bar(10, 380, "HULL", S.hull / 100, S.hull < 35 ? "#ff4444" : "#ffaa33");
    bar(W - 100, 380, `FUEL ${S.fuel.toFixed(1)}`, S.fuel / MAXFUEL, "#ddee44");
    tx(W - 10, 325, inRange(S.tgt) ? `JUMP: ${G[S.tgt].name} ${dist(S.sys, S.tgt).toFixed(1)}LY [N/J]` : "NO JUMP TARGET", "#aaccff", 10, "right");
    tx(W - 10, 340, `HOLD ${used()}/${holdCap()}t`, "#55ff88", 10, "right");
    tx(W - 10, 355, `MISSILES ${S.missiles}${lock ? " · LOCK" : ""}${S.up.ecm ? ecmT > 0 ? " · ECM ..." : " · ECM" : ""}`, lock ? "#ff7788" : "#55ff88", 10, "right");
    // Scanner: blips sit on the plane; stalks show height above or below.
    const SY = 362, k = 36 / 8000;
    out.push({ type: "circle", x: W/2, y: SY, radius: 41, color: "#0c3a22" }, { type: "circle", x: W/2, y: SY, radius: 39, color: "#021208" },
      { type: "lines", segments: [[W/2 - 39, SY, W/2 + 39, SY], [W/2, SY - 39, W/2, SY + 39]], color: "#0c3a22", width: 1 }, ring(W/2, SY, 19, "#0c3a22"));
    const stalks = [];
    for (const o of objs) { if (o.t === "shot" || o.t === "boom" || o.t === "spark") continue;
      const c = cam(o.p); let dx = c[0] * k, dy = -c[2] * k * .9, m = Math.hypot(dx, dy); if (m > 36) { dx *= 36 / m; dy *= 36 / m; }
      const h = clamp(-c[1] * k * .9, -14, 14), x = W/2 + dx, y = SY + dy;
      const color = o.t === "pirate" ? (o === lock ? "#ffffff" : "#ff5555") : o.t === "station" ? "#44ddff" : o.t === "sun" ? "#ffaa33"
        : o.t === "can" ? "#ffdd33" : o.t === "missile" ? "#ff8800" : "#889";
      if (Math.abs(h) > 1) stalks.push([x, y, x, y + h]);
      out.push({ type: "rect", x: x - 1.5, y: y + h - 1.5, width: 3, height: 3, color }); }
    out.push({ type: "lines", segments: stalks, color: "#3f7f5a", width: 1 }, { type: "rect", x: W/2 - 1, y: SY - 1, width: 2, height: 2, color: "#fff" });
    if (flash > 0) out.push({ type: "rect", x: 0, y: 0, width: W, height: H, color: "#ff000033" });
    if (ecmFlash > 0) out.push({ type: "rect", x: 0, y: 0, width: W, height: H, color: "#4488ff33" });
    if (paused) out.push({ type: "rect", x: 0, y: 0, width: W, height: H, color: "#000000aa" },
      { type: "text", x: W/2, y: H/2 - 20, text: "PAUSED", size: 28, align: "center", color: "#55ff88" },
      { type: "text", x: W/2, y: H/2 + 14, text: "P, Enter or Start to resume", size: 12, align: "center", color: "#b3c5cf" });
    // The initial canvas owns pointer input and touch controls; frame updates only send drawing data.
    ui.update("view", ui.canvas(W, H, out, { id: "view" }));
  }

  const anim = ui.animate(({ delta }) => {
    if (mode !== "fly") return;
    readControls();
    if (mode !== "fly") return;
    if (paused) { if (dirty) { draw(); dirty = false; } return; }
    // Keep elapsed game time when rendering falls below 20 FPS, while retaining small collision steps.
    let remaining = Math.min(Math.max(delta, 0), .25);
    if (remaining === 0) step(0);
    while (remaining > 0 && mode === "fly") {
      const dt = Math.min(remaining, .05);
      step(dt); remaining -= dt;
    }
    if (mode === "fly") draw();
  });
  anim.pause();
  ui.pointerLock.onMove(({ x, y }) => { if (mode === "fly" && !paused) { mouseX += x; mouseY += y; } });
  ui.pointerLock.onChange(() => { mouseFire = false; mouseX = mouseY = 0; });
  ui.onVisibility(({ value }) => {
    if (value === "hidden") { mouseFire = false; mouseX = mouseY = 0; setPause(true, "Paused while the window was in the background."); }
  });
  ui.presentation.onChange(() => { if (mode === "dock") ui.update("menubar", ui.menuBar(menus(), ({ value }) => menuAction(value), { id: "menubar" })); });
  ui.onKey((e) => {
    if (e.phase !== "down" || e.repeat || mode !== "fly" || modalOpen) return;
    const map = { KeyC: "dock", KeyN: "next", KeyJ: "jump", ShiftRight: "jump", KeyT: "lock", KeyM: "missile", KeyX: "ecm", KeyP: "pause", Enter: "pause" };
    if (map[e.code]) flightAction(map[e.code]);
    else if (e.code === "KeyI") { set.invert = !set.invert; saveSettings(); say(`Pitch ${set.invert ? "inverted" : "normal"}.`); }
    else if (e.code === "KeyF") toggleImmersive();
  });
  ensureTarget();
  const welcome = S.migrated ? "Void Trader 2.0: the galaxy has been recharted. Your credits, cargo, hull and kill record transferred; any old contract was cancelled."
    : S.log.length ? "Welcome back, Commander." : "Welcome, Commander.";
  if (S.migrated) { S.migrated = false; log("Transferred to the recharted galaxy."); }
  if (!S.log.length) log("Commission granted.");
  dock(welcome);
  tutorial();
});
