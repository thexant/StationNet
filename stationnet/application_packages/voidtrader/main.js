// VOID TRADER - an Elite-style wireframe space trading and combat sim for StationNet.
StationNet.start(async (ui) => {
  const W = 640, H = 400, FOC = 330, NEAR = 5, HOLD = 20, MAXFUEL = 7, REFUEL = 20;
  const SYS = [["Lave",4,10],["Diso",7,6],["Zaonce",10,12],["Leesti",13,8],["Riedquat",16,13],
    ["Orrere",8,15],["Tionisla",3,4],["Usleri",12,3],["Anreer",17,6],["Ceerdi",1,14]];
  const DG = [0,1,1,2,2,1,0,3,2,1]; // pirate danger per system
  const COM = [["Food",20],["Textiles",35],["Minerals",55],["Machinery",90],["Computers",160],["Luxuries",260]];
  const RK = [[0,"Harmless"],[2,"Mostly Harmless"],[6,"Poor"],[12,"Average"],[20,"Above Average"],
    [32,"Competent"],[48,"Dangerous"],[64,"Deadly"],[128,"ELITE"]];
  const V = {
    add: (a, b) => [a[0]+b[0], a[1]+b[1], a[2]+b[2]],
    sub: (a, b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]],
    mul: (a, s) => [a[0]*s, a[1]*s, a[2]*s],
    dot: (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2],
    cross: (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]],
    len: (a) => Math.hypot(a[0], a[1], a[2]),
    norm: (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0]/l, a[1]/l, a[2]/l]; }
  };
  const rnd = Math.random;
  const rdir = () => V.norm([rnd()-.5, (rnd()-.5)*.6, rnd()-.5]);
  const h = (a, b) => { const x = Math.sin(a*12.9898 + b*78.233) * 43758.5453; return x - Math.floor(x); };
  const dist = (a, b) => Math.hypot(SYS[a][1]-SYS[b][1], SYS[a][2]-SYS[b][2]);
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
  const SHIP = { v: [[0,0,3],[-2,0,-2],[2,0,-2],[0,.8,-2],[0,-.4,-2]],
    e: [[0,1],[0,2],[0,3],[0,4],[1,3],[2,3],[1,4],[2,4],[1,2]] };
  const CAN = box(1, 1, 1);

  // ---- persistent state ----
  const sv = (await ui.storage.get("save")) || {};
  let cr = sv.cr ?? 100, fuel = sv.fuel ?? MAXFUEL, cargo = sv.cargo || {}, sys = sv.sys ?? 0, kills = sv.kills || 0, tgt = sys;
  let hull = sv.hull ?? 100, contract = sv.contract || null, tradeQty = 1, soundOn = sv.soundOn ?? true;
  const used = () => Object.values(cargo).reduce((a, b) => a + b, 0);
  const rank = () => RK.filter((r) => kills >= r[0]).pop()[1];
  const save = () => ui.storage.set("save", { cr, fuel, cargo, sys, kills, hull, contract, soundOn, stock, stockSys }).catch(() => {});
  const priceAt = (s, i) => Math.round(COM[i][1] * (0.65 + 0.7 * h(s + 1, i + 1)));
  const price = (i) => priceAt(sys, i);
  const sellAt = (s, i) => Math.floor(priceAt(s, i) * 0.92);
  const sell = (i) => sellAt(sys, i);
  const offer = (s) => {
    const destinations = SYS.map((_, i) => i).filter((i) => i !== s && dist(s, i) <= MAXFUEL);
    if (!destinations.length) return null;
    const to = destinations[Math.floor(h(s + 11, 37) * destinations.length)];
    const good = Math.floor(h(s + 19, 43) * COM.length);
    const qty = 2 + Math.floor(h(s + 23, 61) * 4);
    return { from: s, to, good, qty, bonus: Math.max(60, Math.round(priceAt(s, good) * qty * .35)) };
  };
  let stock = Array.isArray(sv.stock) && sv.stock.length === COM.length ? sv.stock : [];
  let stockSys = sv.stockSys ?? -1;
  const newStock = () => {
    if (stockSys === sys && stock.length === COM.length) return;
    stock = COM.map(() => 4 + Math.floor(rnd() * 26)); stockSys = sys;
  };
  const cycle = () => { for (let i = 1; i <= SYS.length; i++) { const c = (tgt + i) % SYS.length;
    if (c !== sys && dist(sys, c) <= MAXFUEL) { tgt = c; return; } } };

  // ---- flight state ----
  let mode = "dock", keys = {}, P = [0,0,0], R = [1,0,0], U = [0,1,0], F = [0,0,1];
  let spd = 0, shield = 100, objs = [], STN = null, msg = "", msgT = 0;
  let cool = 0, laserT = 0, flash = 0, spawnT = 1e9, jumpT = 0, invert = false, clock = 0;
  let mouseX = 0, mouseY = 0;
  const dust = Array.from({ length: 45 }, () => [(rnd()-.5)*3000, (rnd()-.5)*3000, (rnd()-.5)*3000]);
  const say = (t) => { msg = t; msgT = 3.5; };
  const tone = (f, d, w = "square", v = .06) => { if (soundOn) ui.sound.playTone(f, d, { waveform: w, volume: v }); };

  function mkRock() {
    const rad = 60 + rnd() * 120;
    return { t: "rock", p: V.mul(rdir(), 2500 + rnd() * 8000), hp: 60, rad, s: rad, col: "#aab0b8", a: rnd() * 6, w: .2 + rnd() * .5,
      m: octa([0,0,0,0,0,0].map(() => .7 + rnd() * .5)), b: basis([0,0,1]) };
  }
  function arrive() {
    STN = { t: "station", p: [0,0,0], rad: 450, s: 450, col: "#44ddff", a: 0, m: STNM, b: basis([0,0,1]) };
    objs = [STN]; for (let i = 0; i < 7; i++) objs.push(mkRock());
    const d = rdir(); P = V.mul(d, 9000); [R, U, F] = basis(V.mul(d, -1));
    spawnT = DG[sys] > 0 ? 6 : 1e9; spd = 250;
  }
  function launch() {
    arrive(); P = [0, 0, -1150]; F = [0,0,-1]; U = [0,1,0]; R = V.norm(V.cross(U, F)); spd = 120; shield = 100;
    mode = "fly"; keys = {}; cool = laserT = flash = jumpT = 0; cycle2();
    ui.render(ui.canvas(W, H, [], cockpitOptions),
      ui.text("Mouse or arrows pitch/yaw | A/D yaw | Q/E roll | W/S throttle | Click or SPACE fire | C dock | N target | J jump | I invert | F immersive"),
      ui.text("Capture mouse for relative steering. Escape releases it.", { id: "mouse-status" }),
      ui.button("Capture mouse", async () => {
        try { await ui.pointerLock.request({ unadjustedMovement: true }); say("Mouse captured. Click to fire; Escape releases the mouse."); }
        catch (error) { ui.update("mouse-status", ui.text(`Mouse capture unavailable: ${error.message}`, { id: "mouse-status", tone: "warning" })); }
      }),
      ui.button("Release mouse", () => ui.pointerLock.release()));
    say("Launch complete. Station is behind you - fly safe, Commander.");
    anim.resume();
  }
  const cycle2 = () => { if (tgt === sys) cycle(); };

  function dock(note = "") {
    if (mode === "fly") ui.pointerLock.release();
    mode = "dock"; anim.pause(); keys = {}; shield = 100; hull = Math.max(25, hull); newStock(); cycle2();
    if (contract && sys === contract.to && (cargo[COM[contract.good][0]] || 0) >= contract.qty) {
      const name = COM[contract.good][0];
      cargo[name] -= contract.qty;
      cr += sellAt(sys, contract.good) * contract.qty + contract.bonus;
      note = `Delivery complete: ${contract.qty}t ${name} to ${SYS[sys][0]}. ${contract.bonus}cr bonus awarded.`;
      contract = null;
      tone(740, 150, "triangle");
    }
    ui.scores.submit(Math.floor(cr)).catch(() => {}); dockedView(note);
  }
  function chart() {
    const out = [{ type: "rect", x: 0, y: 0, width: 640, height: 220, color: "#07111d" }];
    const pos = (i) => [48 + SYS[i][1] * 30, 25 + SYS[i][2] * 11];
    const [cx, cy] = pos(sys);
    for (let i = 0; i < SYS.length; i++) {
      if (i === sys || dist(sys, i) > MAXFUEL) continue;
      const [x, y] = pos(i);
      out.push({ type: "line", x1: cx, y1: cy, x2: x, y2: y, color: i === tgt ? "#aaccff" : "#24475c", width: i === tgt ? 2 : 1 });
    }
    for (let i = 0; i < SYS.length; i++) {
      const [x, y] = pos(i), reachable = dist(sys, i) <= MAXFUEL;
      const color = i === sys ? "#55ff88" : i === tgt ? "#aaccff" : reachable ? "#ffce68" : "#607080";
      out.push({ type: "circle", x, y, radius: i === sys || i === tgt ? 6 : 4, color });
      out.push({ type: "text", x, y: y - 13, text: SYS[i][0], size: 10, align: "center", color });
    }
    out.push({ type: "text", x: 10, y: 207, text: "GREEN current  |  BLUE target  |  GOLD reachable on a full tank", size: 10, color: "#b3c5cf" });
    return out;
  }
  function dockedView(note = "") {
    save();
    const row = (a, b, c, d, e) => String(a).padEnd(11) + String(b).padStart(6) + String(c).padStart(6) + String(d).padStart(7) + String(e).padStart(6);
    const rows = COM.map((c, i) => row(c[0], price(i), sell(i), stock[i], cargo[c[0]] || 0));
    const td = tgt === sys ? "none in range" : `${SYS[tgt][0]} (${dist(sys, tgt).toFixed(1)} LY)`;
    const quantity = (i, dir) => dir > 0
      ? Math.min(tradeQty, stock[i], HOLD - used(), Math.floor(cr / price(i)))
      : Math.min(tradeQty, cargo[COM[i][0]] || 0);
    const mission = offer(sys);
    const route = tgt === sys ? null : COM.map((c, i) => ({ name: c[0], margin: sellAt(tgt, i) - price(i) }))
      .sort((a, b) => b.margin - a.margin)[0];
    ui.render(
      ui.title(`${SYS[sys][0]} Orbital Station`),
      ui.text(`Cr ${cr} | Fuel ${fuel.toFixed(1)}/${MAXFUEL} LY | Hull ${Math.round(hull)}% | Hold ${used()}/${HOLD}t | ${rank()} (${kills} kills)`),
      ...(note ? [ui.text(note, { tone: "warning" })] : []),
      ui.heading("Market"),
      ui.preformatted([row("COMMODITY", "BUY", "SELL", "STOCK", "HOLD"), ...rows].join("\n")),
      ui.menu(`Trade quantity: ${tradeQty === Infinity ? "maximum" : tradeQty}t`, [
        { label: "1 tonne", value: "1" }, { label: "5 tonnes", value: "5" }, { label: "Maximum", value: "max" }
      ], ({ value }) => { tradeQty = value === "max" ? Infinity : +value; dockedView(); }),
      ui.menu("Buy", COM.map((c, i) => ({ label: `${c[0]} ${quantity(i, 1)}t / ${quantity(i, 1) * price(i)}cr`, value: String(i),
        disabled: quantity(i, 1) < 1 })), ({ value }) => trade(+value, 1)),
      ui.menu("Sell", COM.map((c, i) => ({ label: `${c[0]} ${quantity(i, -1)}t / ${quantity(i, -1) * sell(i)}cr`, value: String(i),
        disabled: quantity(i, -1) < 1 })), ({ value }) => trade(+value, -1)),
      ui.heading("Navigation"),
      ui.text(`Hyperspace target: ${td}. Danger level here: ${["safe","low","moderate","high"][DG[sys]]}.`),
      ui.canvas(640, 220, chart(), { label: "Star chart: tap a reachable system to target it", onPointer: ({ x, y, phase }) => {
        if (phase !== "end") return;
        const i = SYS.findIndex((s) => Math.hypot(x - (48 + s[1] * 30), y - (25 + s[2] * 11)) < 13);
        if (i >= 0 && i !== sys && dist(sys, i) <= MAXFUEL) { tgt = i; dockedView(`Target set: ${SYS[i][0]}.`); }
      } }),
      ui.menu("Select destination", SYS.map((s, i) => ({ label: `${s[0]} ${dist(sys, i).toFixed(1)}LY${DG[i] ? " " + "!".repeat(DG[i]) : ""}`,
        value: String(i), disabled: i === sys || dist(sys, i) > MAXFUEL })), ({ value }) => { tgt = +value; dockedView(`Target set: ${SYS[tgt][0]}.`); }),
      ...(route ? [ui.text(`Best quoted margin to ${SYS[tgt][0]}: ${route.name} ${route.margin >= 0 ? "+" : ""}${route.margin}cr/t before fuel.`)] : []),
      ui.heading("Freight contract"),
      contract
        ? ui.text(`Deliver ${contract.qty}t ${COM[contract.good][0]} to ${SYS[contract.to][0]}. Cargo ${cargo[COM[contract.good][0]] || 0}/${contract.qty}t. Destination sale proceeds plus ${contract.bonus}cr bonus. No deadline.`)
        : ui.text(`Offer: deliver ${mission.qty}t ${COM[mission.good][0]} to ${SYS[mission.to][0]} for destination sale proceeds plus ${mission.bonus}cr bonus.`),
      ...(!contract ? [ui.button("Accept freight contract", () => { contract = mission; tgt = mission.to; dockedView(`Contract accepted. Buy or scoop ${mission.qty}t ${COM[mission.good][0]}.`); })] : []),
      ui.heading("Ship services"),
      ui.menu("Services", [
        { label: `Refuel (${REFUEL}cr/LY, 0.1LY units)`, value: "fuel", disabled: fuel > MAXFUEL - .05 || cr < 2 },
        { label: `Repair hull (3cr/point)`, value: "repair", disabled: hull >= 100 || cr < 3 },
        { label: soundOn ? "Sound: on" : "Sound: off", value: "sound" }
      ], ({ value }) => {
        if (value === "fuel") { const n = Math.min(MAXFUEL - fuel, Math.floor(cr / 2) / 10); fuel = Math.min(MAXFUEL, fuel + n); cr -= Math.round(n * REFUEL); dockedView(`Purchased ${n.toFixed(1)} LY of fuel.`); }
        else if (value === "repair") { const n = Math.min(Math.ceil(100 - hull), Math.floor(cr / 3)); hull = Math.min(100, hull + n); cr -= n * 3; dockedView(`Repaired ${n} hull points.`); }
        else { soundOn = !soundOn; if (!soundOn) ui.sound.stop(); dockedView(); }
      }),
      ui.button("Station leaderboard", async () => {
        try { await ui.scores.submit(Math.floor(cr)); const result = await ui.scores.leaderboard();
          dockedView(`Top pilots: ${result.leaderboard.map((p) => `${p.username} ${p.score}cr`).join(" | ") || "No scores yet."}`);
        } catch { dockedView("Leaderboard unavailable."); }
      }),
      ui.button("LAUNCH", launch)
    );
  }
  function trade(i, dir) {
    const n = COM[i][0];
    if (!Number.isInteger(i) || i < 0 || i >= COM.length) return;
    const q = dir > 0 ? Math.min(tradeQty, stock[i], HOLD - used(), Math.floor(cr / price(i)))
      : Math.min(tradeQty, cargo[n] || 0);
    if (q < 1) return dockedView("Trade unavailable.");
    if (dir > 0) { stock[i] -= q; cr -= q * price(i); cargo[n] = (cargo[n] || 0) + q; }
    else { cargo[n] -= q; cr += q * sell(i); stock[i] += q; }
    dockedView();
  }
  function die() {
    tone(60, 600, "sawtooth", .15);
    cr = Math.floor(cr / 2); cargo = {}; dock("Your ship was destroyed. An escape pod returned you to the station; half your credits and all cargo were lost.");
  }
  function hurt(n) {
    if (shield > 0) { shield -= n; if (shield < 0) { hull += shield; shield = 0; } } else hull -= n;
    flash = .15; tone(110, 120, "sawtooth", .12); if (hull <= 0 && mode === "fly") die();
  }
  function kill(o) {
    o.dead = 1; objs.push({ t: "boom", p: [...o.p], life: .7, rad: o.rad }); tone(70, 300, "sawtooth", .12);
    if (o.t === "pirate") { kills++; const b = 40 + Math.floor(rnd() * 80); cr += b; say(`Pirate destroyed! Bounty ${b}cr.`);
      if (rnd() < .6) objs.push({ t: "can", p: [...o.p], c: COM[Math.floor(rnd() * 6)][0], a: 0, rad: 30, s: 22, col: "#ffdd33", m: CAN, b: basis([0,0,1]) }); }
    else if (rnd() < .5) objs.push({ t: "can", p: [...o.p], c: "Minerals", a: 0, rad: 30, s: 22, col: "#ffdd33", m: CAN, b: basis([0,0,1]) });
  }
  function fire() {
    let best = null, bt = 4000;
    for (const o of objs) { if (o.t !== "pirate" && o.t !== "rock") continue;
      const d = V.sub(o.p, P), t = V.dot(d, F); if (t < 0 || t > bt) continue;
      if (V.len(V.sub(d, V.mul(F, t))) < o.rad) { bt = t; best = o; } }
    if (best && (best.hp -= 15) <= 0) kill(best);
  }
  function triggerLaser() {
    if (mode !== "fly" || cool > 0) return;
    cool = .2; laserT = .08; tone(900, 60); fire();
  }
  const cockpitOptions = { id: "view", label: "Cockpit view", onPointer: ({ phase }) => {
    if (phase === "start") { keys.MouseFire = true; triggerLaser(); }
    else if (phase === "end") keys.MouseFire = false;
  } };
  function tryJump() {
    if (jumpT > 0) return;
    if (tgt === sys) return say("No target system in range (N).");
    const d = dist(sys, tgt); if (d > fuel) return say("Insufficient fuel for that jump.");
    if (V.len(P) < 3000) return say("Mass-locked by station. Move 3000+ units away.");
    fuel -= d; jumpT = 1.6; tone(200, 1400, "sine", .1);
  }

  function ai(o, dt, d, dd) {
    const n = V.mul(d, 1 / dd), sp = dd > 700 ? 330 : 180;
    const side = V.mul(V.cross(n, [0,1,0]), Math.sin(clock * 1.3 + o.a) * 120);
    o.v = V.add(o.v, V.mul(V.sub(V.add(V.mul(n, sp), side), o.v), Math.min(1, dt * 1.5)));
    o.p = V.add(o.p, V.mul(o.v, dt)); o.b = basis(o.v); o.f -= dt;
    if (dd < 1800 && o.f <= 0) { o.f = 1.1 + rnd();
      objs.push({ t: "shot", p: [...o.p], v: V.add(V.mul(n, 850), [(rnd()-.5)*120, (rnd()-.5)*120, (rnd()-.5)*120]), life: 3 }); }
  }

  function step(dt) {
    clock += dt;
    const k = keys, a = 1.5 * dt;
    const pitch = (((k.ArrowUp ? 1 : 0) - (k.ArrowDown ? 1 : 0)) * a - Math.max(-200, Math.min(200, mouseY)) * .0025) * (invert ? -1 : 1);
    const yaw = ((k.KeyD || k.ArrowRight ? 1 : 0) - (k.KeyA || k.ArrowLeft ? 1 : 0)) * a * .6 + Math.max(-200, Math.min(200, mouseX)) * .0025;
    mouseX = mouseY = 0;
    [F, U] = rot(F, U, pitch);
    [U, R] = rot(U, R, ((k.KeyE ? 1 : 0) - (k.KeyQ ? 1 : 0)) * a);
    [F, R] = rot(F, R, yaw);
    F = V.norm(F); R = V.norm(V.cross(U, F)); U = V.cross(F, R);
    if (k.KeyW) spd = Math.min(500, spd + 260 * dt);
    if (k.KeyS) spd = Math.max(0, spd - 260 * dt);
    P = V.add(P, V.mul(F, spd * dt));
    for (const d of dust) for (let i = 0; i < 3; i++) d[i] = P[i] + ((d[i] - P[i] + 1500) % 3000 + 3000) % 3000 - 1500;
    cool -= dt; laserT -= dt; flash -= dt; msgT -= dt; shield = Math.min(100, shield + 2 * dt);
    if (k.Space || k.MouseFire) triggerLaser();
    spawnT -= dt;
    if (spawnT <= 0) { spawnT = (20 + rnd() * 30) / (DG[sys] || 1);
      if (V.len(P) > 2500 && objs.filter((o) => o.t === "pirate").length < 3) {
        const dir = rdir(); objs.push({ t: "pirate", p: V.add(P, V.mul(dir, 3500)), v: V.mul(dir, -50), hp: 40, rad: 90, s: 40,
          col: "#ff5555", a: rnd() * 6, f: 2, m: SHIP, b: basis(dir) }); say("Hostile contact!"); tone(300, 200, "triangle", .1); } }
    for (const o of objs) { if (o.dead) continue;
      const d = V.sub(P, o.p), dd = V.len(d);
      if (o.t === "can") { o.a += dt; o.b = basis([Math.sin(o.a), .3, Math.cos(o.a)]);
        if (dd < 130) { if (used() < HOLD) { cargo[o.c] = (cargo[o.c] || 0) + 1; say(`Scooped 1t ${o.c}.`); o.dead = 1; tone(660, 80, "sine", .08); } else say("Hold full!"); }
        continue; }
      if (o.t === "shot") { o.p = V.add(o.p, V.mul(o.v, dt)); if ((o.life -= dt) <= 0) o.dead = 1; else if (dd < 70) { o.dead = 1; hurt(8); } continue; }
      if (o.t === "boom") { if ((o.life -= dt) <= 0) o.dead = 1; continue; }
      if (o.t === "pirate") ai(o, dt, d, dd);
      else { o.a += dt * (o.w || .15); const c = Math.cos(o.a), s = Math.sin(o.a);
        o.b = o.t === "station" ? [[c,s,0],[-s,c,0],[0,0,1]] : [[c,0,-s],[0,1,0],[s,0,c]]; }
      if (dd < o.rad + 30) { hurt(Math.max(8, spd * .08)); P = V.add(o.p, V.mul(V.norm(d), o.rad + 32)); spd *= .3;
        if (o.t === "pirate" && (o.hp -= 30) <= 0) kill(o); }
    }
    objs = objs.filter((o) => !o.dead);
    if (jumpT > 0 && (jumpT -= dt) <= 0) { jumpT = 0; sys = tgt; tgt = sys; cycle(); arrive(); say(`Arrived in ${SYS[sys][0]}.`); save(); }
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
    const out = [], G = {}, g = (c) => G[c] || (G[c] = []);
    const tx = (x, y, text, color = "#55ff88", size = 11, align = "left") => out.push({ type: "text", x, y, text, size, align, color });
    const bar = (x, y, label, f, color) => { out.push({ type: "rect", x, y, width: 90, height: 7, color: "#10222a" },
      { type: "rect", x, y, width: 90 * Math.max(0, Math.min(1, f)), height: 7, color }); tx(x, y - 8, label, color, 9); };
    for (const d of dust) { const c = cam(d); if (c[2] > 1) seg(g("#778899"), c, cam(V.sub(d, V.mul(F, spd * .03 + 2)))); }
    for (const o of objs) {
      if (o.t === "shot") { seg(g("#ff4444"), cam(o.p), cam(V.sub(o.p, V.mul(o.v, .05)))); continue; }
      if (o.t === "boom") { const c = cam(o.p); if (c[2] > 1) { const s = scr(c), r = (80 + 500 * (.7 - o.life)) / c[2] * FOC;
        out.push({ type: "circle", x: s[0], y: s[1], radius: r, color: "#ff880066" }, { type: "circle", x: s[0], y: s[1], radius: r * .55, color: "#ffee0099" }); } continue; }
      if (o.t !== "station" && V.len(V.sub(o.p, P)) > 25000) continue;
      const [r, u, f] = o.b;
      const cv = o.m.v.map((v) => cam([o.p[0] + (r[0]*v[0] + u[0]*v[1] + f[0]*v[2]) * o.s,
        o.p[1] + (r[1]*v[0] + u[1]*v[1] + f[1]*v[2]) * o.s, o.p[2] + (r[2]*v[0] + u[2]*v[1] + f[2]*v[2]) * o.s]));
      for (const e of o.m.e) seg(g(o.col), cv[e[0]], cv[e[1]]);
    }
    if (jumpT > 0) { const q = 1.6 - jumpT; for (let i = 0; i < 40; i++) { const an = i * .157, r0 = 30 + q * 120, r1 = r0 + 40 + q * 300;
      g("#aaccff").push([W/2 + Math.cos(an) * r0, H/2 + Math.sin(an) * r0, W/2 + Math.cos(an) * r1, H/2 + Math.sin(an) * r1]); } }
    for (const c in G) out.push({ type: "lines", segments: G[c], color: c, width: 1.2 });
    if (laserT > 0) out.push({ type: "lines", segments: [[120, H, W/2, H/2], [W-120, H, W/2, H/2]], color: "#ff3333", width: 3 });
    out.push({ type: "lines", segments: [[W/2-14, H/2, W/2-5, H/2], [W/2+5, H/2, W/2+14, H/2], [W/2, H/2-14, W/2, H/2-5], [W/2, H/2+5, W/2, H/2+14]], color: "#55ff88", width: 1 });
    // station pointer
    const sc = cam(STN.p), sd = V.len(P), sx = sc[0] / sc[2] * FOC, sy = sc[1] / sc[2] * FOC;
    if (!(sc[2] > 1 && Math.abs(sx) < W/2 - 20 && Math.abs(sy) < H/2 - 20)) { const an = Math.atan2(sc[1], sc[0]);
      tx(W/2 + Math.cos(an) * (W/2 - 34), H/2 - Math.sin(an) * (H/2 - 70), "STN", "#44ddff", 12, "center"); }
    // HUD
    tx(10, 14, `${SYS[sys][0]}  Cr ${cr}`); tx(W - 10, 14, `${rank()}  Kills ${kills}`, "#55ff88", 11, "right");
    tx(W/2, 14, `STATION ${Math.round(sd)}u${sd < 1400 ? "  [D] DOCK" : ""}`, "#44ddff", 11, "center");
    if (msgT > 0) tx(W/2, 40, msg, "#ffee66", 13, "center");
    bar(10, 330, "SPEED", spd / 500, "#55ff88"); bar(10, 355, "SHIELD", shield / 100, "#44aaff"); bar(10, 380, "HULL", hull / 100, hull < 35 ? "#ff4444" : "#ffaa33");
    bar(W - 100, 380, `FUEL ${fuel.toFixed(1)}`, fuel / MAXFUEL, "#ddee44");
    tx(W - 10, 340, tgt === sys ? "NO JUMP TARGET" : `JUMP: ${SYS[tgt][0]} ${dist(sys, tgt).toFixed(1)}LY [N/J]`, "#aaccff", 10, "right");
    tx(W - 10, 355, `HOLD ${used()}/${HOLD}t`, "#55ff88", 10, "right");
    if (contract) tx(W/2, 55, `FREIGHT ${COM[contract.good][0]} ${cargo[COM[contract.good][0]] || 0}/${contract.qty}t -> ${SYS[contract.to][0]}`, "#ffce68", 10, "center");
    out.push({ type: "circle", x: W/2, y: 362, radius: 41, color: "#0c3a22" }, { type: "circle", x: W/2, y: 362, radius: 39, color: "#021208" });
    for (const o of objs) { if (o.t === "shot" || o.t === "boom") continue;
      const c = cam(o.p); let dx = c[0] * 36 / 8000, dy = -c[2] * 36 / 8000 * .9, m = Math.hypot(dx, dy); if (m > 36) { dx *= 36 / m; dy *= 36 / m; }
      out.push({ type: "rect", x: W/2 + dx - 1.5, y: 362 + dy - 1.5, width: 3, height: 3, color: o.t === "pirate" ? "#ff5555" : o.t === "station" ? "#44ddff" : o.t === "can" ? "#ffdd33" : "#889" }); }
    out.push({ type: "rect", x: W/2 - 1, y: 361, width: 2, height: 2, color: "#fff" });
    if (flash > 0) out.push({ type: "rect", x: 0, y: 0, width: W, height: H, color: "#ff000033" });
    ui.update("view", ui.canvas(W, H, out, cockpitOptions));
  }

  const anim = ui.animate(({ delta }) => { if (mode !== "fly") return; step(Math.min(delta, .05)); if (mode === "fly") draw(); });
  anim.pause();
  ui.pointerLock.onMove(({ x, y }) => {
    if (mode === "fly") { mouseX += x; mouseY += y; }
  });
  ui.pointerLock.onChange(() => { keys.MouseFire = false; mouseX = mouseY = 0; });
  ui.onVisibility(({ value }) => {
    if (value === "hidden") { keys.MouseFire = false; mouseX = mouseY = 0; }
  });
  ui.onKey((e) => {
    if (e.phase === "up") { delete keys[e.code]; return; }
    keys[e.code] = true;
    if (mode !== "fly" || e.repeat) return;
    if (e.code === "KeyC") { if (V.len(P) > 1400) say("Too far from the station (within 1400u).");
      else if (spd > 160) say("Reduce speed to 160 or less to dock."); else dock(); }
    else if (e.code === "KeyN") { cycle(); say(tgt === sys ? "No systems in range." : `Target: ${SYS[tgt][0]}`); }
    else if (e.code === "KeyJ") tryJump();
    else if (e.code === "KeyI") { invert = !invert; say(`Pitch ${invert ? "inverted" : "normal"}.`); }
    else if (e.code === "KeyF") ui.presentation.mode === "immersive" ? ui.presentation.exitImmersive() : ui.presentation.enterImmersive();
  });
  cycle2(); newStock();
  dock(sv.cr === undefined ? "Welcome, Commander. Buy low, sell high, jump between systems, and watch for pirates. Pirates drop cargo when destroyed." : "Welcome back, Commander.");
});
