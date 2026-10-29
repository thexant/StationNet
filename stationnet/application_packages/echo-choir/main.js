// ECHO CHOIR: REMIX — a turn-based time-loop puzzle for StationNet.
StationNet.start(async (ui) => {
  const { chapters } = await ui.modules.load("levels.js");
  const engine = await ui.modules.load("engine.js");
  const parsed = chapters.map(engine.parseChapter);
  const COLORS = ["#5de8ff", "#ff83c9", "#ffd179", "#97ffc6", "#c3a0ff"];
  const NOTES = [261.63, 329.63, 392, 493.88, 587.33];
  let unlocked = 0, best = {}, soundOn = true, reduceMotion = false;
  let level = 0, game, message = "", hintTier = 0, inspect = "", previewBeat = null;
  let zoom = false, showTextMap = false, viewport = await ui.display.viewport();
  let boardGeometry, lastFrame = 0, phase = 0, leaderboard = [], boardMessage = "Loading station scores…";
  let pendingScore = 0, saving = Promise.resolve(), submitting = false;

  const maxChapter = chapters.length - 1;
  const same = (a, b) => a.x === b.x && a.y === b.y;
  const point = p => `${p.x},${p.y}`;
  const campaignScore = () => chapters.reduce((sum, _, i) => sum + (Number(best[i]) || 0), 0);
  const current = () => parsed[level];
  const sound = (frequency, duration = 100, volume = 0.045) => {
    if (soundOn) ui.sound.playTone(frequency, duration, { waveform: "triangle", volume });
  };
  function chord() {
    if (!soundOn) return;
    const voices = game.board.pads.filter(p => engine.padActive(game, p)).map(p => ({
      frequency: NOTES[(p.number - 1) % NOTES.length], duration: 145,
      volume: 0.025, waveform: "sine"
    }));
    game.echoes.forEach((path,i) => {
      const p = engine.echoAt(path,game.beat);
      voices.push({frequency:NOTES[(p.x+p.y+i)%NOTES.length] * (i%2 ? 0.5 : 1),
        duration:110,volume:0.012,waveform:i%2 ? "triangle" : "sine"});
    });
    if (!voices.length) voices.push({ frequency: NOTES[(game.player.x + game.player.y) % NOTES.length],
      duration: 85, volume: 0.026, waveform: "triangle" });
    ui.sound.play(voices, { duration: 145 });
  }
  function payload() {
    return { version: 2, unlocked, best, soundOn, reduceMotion, lastChapter: level,
      checkpoint: engine.snapshot(game), pendingScore };
  }
  function save() {
    const value = payload();
    saving = saving.catch(() => {}).then(() => ui.storage.set(engine.SAVE_KEY, value));
    saving.catch(() => {});
  }
  async function refreshScores() {
    try {
      const result = await ui.scores.leaderboard();
      leaderboard = Array.isArray(result?.leaderboard) ? result.leaderboard : [];
      boardMessage = "Station scores are up to date.";
    } catch (_) { boardMessage = "Station scores unavailable. Retry when connected."; }
    updateScores();
  }
  async function flushScore() {
    if (submitting || !pendingScore) return;
    submitting = true;
    while (pendingScore) {
      const submitted = pendingScore;
      boardMessage = "Submitting campaign score…";
      updateScores();
      try {
        const result = await ui.scores.submit(submitted);
        if (Array.isArray(result?.leaderboard)) leaderboard = result.leaderboard;
        if (pendingScore <= submitted) pendingScore = 0;
        boardMessage = pendingScore ? "Submitting improved score…" : "Campaign score saved to the station.";
        save();
      } catch (_) {
        boardMessage = "Score pending. Use Retry score when the station is available.";
        save();
        updateScores();
        break;
      }
      updateScores();
    }
    submitting = false;
  }
  function queueScore() {
    const total = campaignScore();
    if (total > pendingScore) pendingScore = total;
    save();
    void flushScore();
  }

  try {
    const saved = await ui.storage.get(engine.SAVE_KEY);
    if (saved && typeof saved === "object") {
      unlocked = Math.max(0, Math.min(maxChapter, Math.floor(Number(saved.unlocked) || 0)));
      if (saved.best && typeof saved.best === "object") {
        for (let i = 0; i < chapters.length; i++) {
          const value = Number(saved.best[i]);
          if (Number.isInteger(value) && value >= 200 && value <= 2500) best[i] = value;
        }
      }
      if (unlocked === 4 && best[4]) unlocked = 5; // Original five-chapter save.
      soundOn = saved.soundOn !== false;
      reduceMotion = saved.reduceMotion === true;
      level = Number.isInteger(saved.lastChapter) ? Math.max(0, Math.min(unlocked, saved.lastChapter)) : unlocked;
      pendingScore = Number.isInteger(saved.pendingScore) && saved.pendingScore > 0 ? saved.pendingScore : 0;
      game = engine.restore(chapters[level], saved.checkpoint);
    }
  } catch (_) { /* Private storage can be unavailable; play remains possible. */ }
  if (!game) game = engine.createGame(chapters[level]);
  zoom = viewport.width < 620;
  message = game.won ? "Signal restored. Choose Next Chapter or replay this one." :
    game.beat || game.echoes.length ? "Your previous timeline is ready." :
    "Record a path, rewind, then cooperate with your echoes.";

  function startChapter(index) {
    if (!Number.isInteger(index) || index < 0 || index > unlocked) return;
    level = index;
    game = engine.createGame(chapters[level]);
    hintTier = 0; inspect = ""; previewBeat = null;
    message = "New timeline. Make a recording to hold a pad.";
    save();
    renderPage();
  }
  function statusText() {
    if (game.beat >= game.board.beats) return `Beat ${game.beat}/${game.board.beats} · Echoes ${game.echoes.length}/${engine.MAX_ECHOES} · Tape full: Record or Undo.`;
    const next = Math.min(game.board.beats, game.beat + 1);
    const req = engine.requirements(game, next);
    const pads = req.pads.map(p => `${p.number}:${p.active ? "ON" : "off"}`).join(" ");
    return `Beat ${game.beat}/${game.board.beats} · Echoes ${game.echoes.length}/${engine.MAX_ECHOES} · ` +
      `Fragments ${game.collected.length}/${game.board.stars.length}. If you wait, beat ${next}: ` +
      `pads ${pads || "none"}; ghost ${game.board.ghost ? (req.ghost ? "ON" : "off") : "n/a"}; ` +
      `shutter ${req.shutter ? "OPEN" : "closed"}.`;
  }
  function objectiveText() {
    const remaining = game.board.stars.filter(p => !game.collected.includes(point(p))).length;
    const missingPads = game.board.pads.filter(p => !engine.padActive(game, p)).map(p => p.number);
    const tutorial = level === 0 && !game.won ?
      game.echoes.length ? " Tutorial: cross gate A while your echo holds pad 1, then reach the exit." :
      engine.padActive(game, game.board.pads[0]) ? " Tutorial: press RECORD now to leave an echo on pad 1." :
      " Tutorial: move with arrows or tile taps to pad 1. WAIT spends a beat without moving; RECORD saves your path." : "";
    return `Chapter ${level + 1}/${chapters.length}: ${current().name}. ` +
      `${remaining} fragment${remaining === 1 ? "" : "s"} left; ` +
      `${missingPads.length ? "pads " + missingPads.join(", ") + " need a voice" : "all pads active"}` +
      `${game.board.ghost && !engine.ghostActive(game) ? "; ghost echo needed" : ""}.${tutorial}`;
  }
  function report(result) {
    message = result.message;
    if (!result.ok) sound(130, 55, 0.026);
    if (result.ok) {
      previewBeat = null;
      if (result.won) {
        const value = engine.score(game);
        const improved = value > (Number(best[level]) || 0);
        if (improved) best[level] = value;
        unlocked = Math.max(unlocked, Math.min(maxChapter, level + 1));
        message = `SIGNAL RESTORED · ${value} points · ${game.echoes.length} echoes · ${game.beat} final beats.` +
          (improved ? " New personal best!" : "");
        if (soundOn) ui.sound.sequence([392, 493.88, 587.33, 783.99],
          { duration: 125, volume: 0.06, waveform: "triangle" });
        if (improved) queueScore(); else save();
        renderPage();
        return;
      }
      save();
      chord();
    }
    refresh();
  }
  function act(action) {
    if (action === "WAIT") report(engine.move(game, 0, 0));
    else if (action === "RECORD") { const result = engine.record(game); report(result); }
    else if (action === "UNDO") report(engine.undo(game));
    else if (action === "ERASE LAST") report(engine.erase(game));
  }
  function move(dx, dy) {
    const result = engine.move(game, dx, dy);
    if (result.ok && result.portal) sound(880, 150, 0.055);
    report(result);
  }
  function inspectTile(x, y) {
    const symbol = engine.cell(game, x, y);
    const next = game.beat + 1;
    const name = { "#": "wall", " ": "void", ".": "floor", S: "start", E: "exit",
      O: "wormhole", g: "ghost-only pad", G: "ghost gate", "|": "shutter", "*": "fragment" }[symbol] ||
      (/^[1-4]$/.test(symbol) ? `pad ${symbol}` : /^[A-D]$/.test(symbol) ? `gate ${symbol}` : symbol);
    let detail = "";
    if (/^[A-D]$/.test(symbol)) detail = engine.powered(game, symbol.charCodeAt(0) - 64, next, {x,y}) ?
      "powered next beat" : `needs pads 1–${symbol.charCodeAt(0) - 64} next beat`;
    if (symbol === "G") detail = engine.ghostActive(game, next) ? "powered next beat" : "needs a recorded echo on g";
    if (symbol === "|") detail = engine.shutterOpen(next) ? "open next beat" : "closed next beat";
    if (symbol === "E") detail = engine.exitReady(game, next, {x,y}) ? "ready next beat" :
      "needs all pads, fragments, and any ghost echo";
    if (symbol === "*") detail = game.collected.includes(`${x},${y}`) ? "collected this run" : "collect in the live run";
    inspect = `Tile ${x + 1},${y + 1}: ${name}${detail ? " — " + detail : ""}.`;
    refresh();
  }
  function boardCanvas() {
    const compact = viewport.width < 620;
    const W = compact ? 350 : 700, H = compact ? (zoom ? 368 : 300) : 432;
    const board = game.board, t = compact ? (zoom ? 38 : Math.min(26, Math.floor((W - 18) / board.width))) :
      zoom ? 52 : Math.min(38, Math.floor((W - 20) / board.width), Math.floor((H - 20) / board.height));
    const visibleX = Math.min(board.width, Math.floor((W - 12) / t));
    const visibleY = Math.min(board.height, Math.floor((H - 12) / t));
    const startX = zoom ? Math.max(0, Math.min(board.width - visibleX, game.player.x - Math.floor(visibleX / 2))) : 0;
    const startY = zoom ? Math.max(0, Math.min(board.height - visibleY, game.player.y - Math.floor(visibleY / 2))) : 0;
    const ox = Math.floor((W - visibleX * t) / 2), oy = Math.floor((H - visibleY * t) / 2);
    boardGeometry = { ox, oy, t, startX, startY, visibleX, visibleY };
    const commands = [];
    const rect = (x, y, w, h, color) => commands.push({ type: "rect", x, y, width: w, height: h, color });
    const circle = (x, y, r, color) => commands.push({ type: "circle", x, y, radius: r, color });
    const label = (x, y, text, size, color) => commands.push({ type: "text", x, y, text, size, color, align: "center" });
    const diamond = (x, y, r, color) => commands.push({ type: "polygon",
      points: [[x,y-r],[x+r,y],[x,y+r],[x-r,y]], fill: color });
    const px = x => ox + (x - startX + 0.5) * t;
    const py = y => oy + (y - startY + 0.5) * t;
    const visible = p => p.x >= startX && p.x < startX + visibleX && p.y >= startY && p.y < startY + visibleY;
    const displayBeat = previewBeat === null ? game.beat : previewBeat;
    rect(0, 0, W, H, "#06111f");
    for (let y = startY; y < startY + visibleY; y++) for (let x = startX; x < startX + visibleX; x++) {
      const s = engine.cell(game, x, y);
      if (s === " ") continue;
      const X = ox + (x - startX) * t, Y = oy + (y - startY) * t, cx = X + t / 2, cy = Y + t / 2;
      if (s === "#") { rect(X+1,Y+1,t-2,t-2,"#303c56"); rect(X+3,Y+3,t-6,2,"#51607c"); continue; }
      rect(X+1,Y+1,t-2,t-2,(x+y)%2 ? "#10283a" : "#142d40");
      if (/^[1-4]$/.test(s)) {
        const active = engine.padActive(game, {x,y}, displayBeat);
        circle(cx,cy,t*.38,active?"#4de9b4":"#536f7a");
        circle(cx,cy,t*.25,"#173444"); label(cx,cy,s,Math.max(13,t*.47),"#ffffff");
      } else if (s === "g") {
        circle(cx,cy,t*.38,engine.ghostActive(game, displayBeat)?"#f5a9dc":"#8b5089");
        label(cx,cy,"g",Math.max(13,t*.45),"#1e1834");
      } else if (/^[A-DG]$/.test(s)) {
        const on = s === "G" ? engine.ghostActive(game, displayBeat) :
          engine.powered(game, s.charCodeAt(0)-64, displayBeat);
        rect(X+3,Y+3,t-6,t-6,on?"#2b8c82":"#8a466c");
        rect(X+6,Y+5,t-12,t-10,"#182d42");
        label(cx,cy,s,Math.max(13,t*.45),"#ffffff");
      } else if (s === "|") {
        rect(X+3,Y+3,t-6,t-6,engine.shutterOpen(displayBeat)?"#4d8ca8":"#8154a2");
        label(cx,cy,"I",Math.max(13,t*.45),"#ffffff");
      } else if (s === "O") {
        circle(cx,cy,t*.4,"#64aaff"); circle(cx,cy,t*.25,"#17385d"); circle(cx,cy,t*.1,"#ffffff");
      } else if (s === "*") {
        diamond(cx,cy,t*.32,game.collected.includes(`${x},${y}`)?"#607181":"#ffd170");
        if (!game.collected.includes(`${x},${y}`)) diamond(cx,cy,t*.15,"#fff9cc");
      } else if (s === "E") {
        circle(cx,cy,t*.39,engine.exitReady(game, displayBeat)?"#edc970":"#6b748e");
        diamond(cx,cy,t*.25,"#fff5c5");
      } else if (s === "S") circle(cx,cy,t*.25,"#4484a0");
    }
    for (let i=0; i<game.echoes.length; i++) {
      const path = game.echoes[i], color = COLORS[i], segments = [];
      for (let j=1; j<path.length; j++) if (visible(path[j]) && visible(path[j-1])) {
        const d = Math.abs(path[j].x-path[j-1].x)+Math.abs(path[j].y-path[j-1].y);
        if (d === 1) segments.push([px(path[j-1].x),py(path[j-1].y),px(path[j].x),py(path[j].y)]);
      }
      if (segments.length) commands.push({type:"lines",segments,color:color+"88",width:2});
      const p = engine.echoAt(path, displayBeat);
      if (visible(p)) { circle(px(p.x),py(p.y),t*.31,color); circle(px(p.x),py(p.y),t*.13,"#132a3c"); label(px(p.x),py(p.y),String(i+1),Math.max(10,t*.31),"#ffffff"); }
    }
    if (visible(game.player)) {
      const p = game.player, pulse = reduceMotion ? 0 : Math.sin(phase)*1.5;
      circle(px(p.x),py(p.y),t*.38+pulse,"#7df4ff"); circle(px(p.x),py(p.y),t*.22,"#17374b");
      diamond(px(p.x)+game.facing.x*t*.12,py(p.y)+game.facing.y*t*.12,t*.1,"#ffffff");
    }
    return ui.canvas(W,H,commands,{id:"board",label:`Echo Choir chapter ${level+1} map. ${statusText()}`,onPointer:boardPointer});
  }
  function boardPointer(event) {
    if (event.phase !== "start" || !boardGeometry) return;
    const {ox,oy,t,startX,startY,visibleX,visibleY} = boardGeometry;
    const col = Math.floor((event.x-ox)/t), row = Math.floor((event.y-oy)/t);
    if (col<0 || row<0 || col>=visibleX || row>=visibleY) return;
    const x = startX+col, y = startY+row;
    const dx = x-game.player.x, dy = y-game.player.y;
    if (!game.won && Math.abs(dx)+Math.abs(dy) === 1) move(dx,dy);
    else if (!game.won && dx === 0 && dy === 0) act("WAIT");
    else inspectTile(x,y);
  }
  function timelineCanvas() {
    const W = viewport.width < 620 ? 350 : 700, H = 164, commands = [];
    const rect = (x,y,w,h,color) => commands.push({type:"rect",x,y,width:w,height:h,color});
    const text = (x,y,value,color) => commands.push({type:"text",x,y,text:value,size:11,color});
    rect(0,0,W,H,"#0a1b2e");
    const left = 63, width = W-left-12, step = width/(game.board.beats+1);
    const displayBeat = previewBeat === null ? game.beat : previewBeat;
    for (let i=0; i<=engine.MAX_ECHOES; i++) {
      const path = i<game.echoes.length ? game.echoes[i] : i===game.echoes.length ? game.tape : null;
      const y = 16+i*24, color = i<game.echoes.length ? COLORS[i] : "#eafcff";
      text(7,y+6,path ? (i<game.echoes.length ? `ECHO ${i+1}` : "LIVE") : "—",path?color:"#53677d");
      if (!path) continue;
      for (let b=0; b<=game.board.beats; b++) {
        const p=engine.echoAt(path,b), active=b<path.length;
        if (active || i<game.echoes.length) rect(left+b*step,y,Math.max(2,step-1),13,
          active ? color : color+"77");
        if (b === path.length-1 && i<game.echoes.length) rect(left+b*step,y-3,Math.max(2,step-1),19,"#ffffff");
      }
    }
    rect(left+displayBeat*step,4,Math.max(2,step),H-8,"#fff1b088");
    return ui.canvas(W,H,commands,{id:"timeline",label:`Exact beat timeline; preview beat ${displayBeat}. Preview does not advance play.`});
  }
  function timelineText() {
    const b = previewBeat === null ? game.beat : previewBeat;
    const voices = game.echoes.map((path,i) => {
      const p = engine.echoAt(path,b);
      const tile = engine.cell(game,p.x,p.y);
      return `Echo ${i+1} at ${p.x+1},${p.y+1}${/^[1-4g]$/.test(tile) ? " on pad "+tile : ""}${b>=path.length-1 ? " (holding)" : ""}`;
    });
    return `${previewBeat === null ? "Live" : "PREVIEW ONLY"} beat ${b}. ${voices.join("; ") || "No echoes recorded yet."}`;
  }
  function textMap() {
    const b = previewBeat === null ? game.beat : previewBeat;
    const actors = new Map(game.echoes.map((path,i) => [point(engine.echoAt(path,b)),String(i+1)]));
    actors.set(point(game.player),"@");
    const rows = game.board.map.map((row,y) => [...row].map((s,x) => actors.get(`${x},${y}`) ||
      (s==="*" && game.collected.includes(`${x},${y}`) ? "." : s)).join(""));
    return `@ player; 1–5 echoes; # wall; A–D numbered pad gates; g/G ghost pad/gate; O wormhole; | shutter; * fragment; E exit.\n`+
      rows.join("\n");
  }
  function ruleHint() {
    if (level === 0) return "Walk onto pad 1. A recording replays your moves and then stays where you left it.";
    if (game.board.ghost) return "Only a recorded echo can hold the pink g pad. Your live player does not power it.";
    if (game.board.portals.length) return "Entering a blue ring exits from its partner. The trip takes one beat.";
    if (game.board.map.some(row => row.includes("|"))) return "Shutters open on beats 0 and 1 of each four-beat cycle. Check the next beat before entering.";
    if (game.board.stars.length) return "Fragments must be collected by your live player after the last rewind.";
    return "A gate needs every numbered pad up to its letter held on the same beat.";
  }
  function updateScores() {
    ui.update("progress",ui.text(`Best this chapter: ${best[level] || "—"} · Campaign total: ${campaignScore()} · Unlocked: ${unlocked+1}/${chapters.length}`,{id:"progress"}));
    ui.update("score-message",ui.text(boardMessage,{id:"score-message"}));
    ui.update("leaderboard",ui.table(["#","Player","Campaign"],leaderboard.length ?
      leaderboard.map((entry,i)=>[i+1,entry.username,entry.score]) : [["—","No scores yet","—"]],{id:"leaderboard"}));
  }
  function refresh() {
    ui.update("board",boardCanvas());
    ui.update("timeline",timelineCanvas());
    ui.update("objective",ui.text(objectiveText(),{id:"objective"}));
    ui.update("status",ui.statusBar(message,{id:"status"}));
    ui.update("signals",ui.text(statusText(),{id:"signals"}));
    ui.update("inspection",ui.text(inspect || "Tap a distant tile to inspect it; tap an adjacent tile to move.",{id:"inspection"}));
    ui.update("timeline-text",ui.text(timelineText(),{id:"timeline-text"}));
    ui.update("preview-slider",ui.slider("preview","Preview beat (does not advance play)",
      {id:"preview-slider",min:0,max:game.board.beats,step:1,value:previewBeat ?? game.beat,
        onChange:({value})=>{ previewBeat=Math.round(Number(value)); refresh(); }}));
    ui.update("text-map",showTextMap ? ui.preformatted(textMap(),{id:"text-map"}) :
      ui.text("Text map hidden.",{id:"text-map"}));
    ui.update("hint",ui.text(hintTier===0 ? "Hints are optional and do not affect score." :
      hintTier===1 ? ruleHint() : `${ruleHint()} ${current().hint}`,{id:"hint"}));
    updateScores();
  }
  function renderPage() {
    ui.render(
      ui.title("ECHO CHOIR: REMIX"),
      ui.text(objectiveText(),{id:"objective"}),
      ui.statusBar(message,{id:"status"}),
      boardCanvas(),
      ui.text(statusText(),{id:"signals"}),
      ui.menu("MOVE · tap an adjacent tile or use arrows/WASD",["UP","LEFT","DOWN","RIGHT"],
        ({value}) => { const dirs={UP:[0,-1],LEFT:[-1,0],DOWN:[0,1],RIGHT:[1,0]}; move(...dirs[value]); },{layout:"dpad"}),
      ui.menu("TIME MACHINE",["WAIT","RECORD","UNDO","ERASE LAST"],({value})=>act(value)),
      ui.text(inspect || "Tap a distant tile to inspect it; tap an adjacent tile to move.",{id:"inspection"}),
      ui.button(zoom ? "SHOW WHOLE MAP" : "ZOOM TO PLAYER",()=>{zoom=!zoom;refresh();}),
      ui.button("RESTART CHAPTER · clears all echoes",()=>startChapter(level)),
      ...(game.won ? [ui.button(level === maxChapter ? "REPLAY FROM CHAPTER 1" : "NEXT CHAPTER",
        ()=>startChapter(level === maxChapter ? 0 : level+1))] : []),
      ui.select("chapter","Choose unlocked chapter",chapters.map((c,i)=>({
        label:`${String(i+1).padStart(2,"0")} · ${i<=unlocked ? c.name+" · best "+(best[i]||"—") : "LOCKED"}`,
        value:String(i)})),{value:String(level),onChange:({value})=>{
          const index=Number(value); if(index<=unlocked) startChapter(index); else renderPage();
        }}),
      ui.text(`Pad numbers power matching gates A–D. g/G is echo-only. O is a wormhole. I is a shutter. Diamonds are fragments. The exit requires all signals and fragments.`),
      ui.panel("Exact-beat timeline and preview",[
        timelineCanvas(),ui.text(timelineText(),{id:"timeline-text"}),
        ui.slider("preview","Preview beat (does not advance play)",
          {id:"preview-slider",min:0,max:game.board.beats,step:1,value:previewBeat ?? game.beat,
            onChange:({value})=>{ previewBeat=Math.round(Number(value)); refresh(); }}),
        ui.button("BACK TO LIVE BEAT",()=>{previewBeat=null;refresh();})
      ]),
      ui.panel("Help and accessibility",[
        ui.text(hintTier===0 ? "Hints are optional and do not affect score." : hintTier===1 ? ruleHint() :
          `${ruleHint()} ${current().hint}`,{id:"hint"}),
        ui.button("SHOW NEXT HINT",()=>{hintTier=Math.min(2,hintTier+1);refresh();}),
        ui.button(showTextMap ? "HIDE TEXT MAP" : "SHOW TEXT MAP",()=>{showTextMap=!showTextMap;renderPage();}),
        showTextMap ? ui.preformatted(textMap(),{id:"text-map"}) : ui.text("Text map hidden.",{id:"text-map"}),
        ui.button(soundOn?"SOUND ON":"SOUND OFF",()=>{soundOn=!soundOn;save();renderPage();}),
        ui.button(reduceMotion?"REDUCED MOTION ON":"REDUCED MOTION OFF",()=>{reduceMotion=!reduceMotion;save();renderPage();})
      ]),
      ui.panel("Station leaderboard",[
        ui.text(`Best this chapter: ${best[level]||"—"} · Campaign total: ${campaignScore()} · Unlocked: ${unlocked+1}/${chapters.length}`,{id:"progress"}),
        ui.text(boardMessage,{id:"score-message"}),
        ui.table(["#","Player","Campaign"],leaderboard.length ?
          leaderboard.map((entry,i)=>[i+1,entry.username,entry.score]) : [["—","No scores yet","—"]],{id:"leaderboard"}),
        ui.button("REFRESH SCORES",()=>{void refreshScores();}),
        ui.button("RETRY SCORE",()=>{void flushScore();})
      ]),
      ui.text("ARROWS/WASD move · SPACE wait · ENTER record · Z undo · X erase · R restart.")
    );
  }
  ui.onKey(({code,phase:state,repeat,ctrlKey,altKey,metaKey})=>{
    if(state!=="down"||repeat||ctrlKey||altKey||metaKey) return;
    const dirs={ArrowUp:[0,-1],KeyW:[0,-1],ArrowLeft:[-1,0],KeyA:[-1,0],
      ArrowDown:[0,1],KeyS:[0,1],ArrowRight:[1,0],KeyD:[1,0]};
    if(dirs[code]) move(...dirs[code]);
    else if(code==="Space") act("WAIT");
    else if(code==="Enter"||code==="KeyL") act("RECORD");
    else if(code==="KeyZ") act("UNDO");
    else if(code==="KeyX") act("ERASE LAST");
    else if(code==="KeyR") startChapter(level);
  });
  ui.presentation.onChange((_mode,next)=>{ if(next) {viewport=next;renderPage();} });
  ui.animate(({time})=>{
    if(reduceMotion||time-lastFrame<80) return;
    lastFrame=time;phase=time/250;
    ui.update("board",boardCanvas());
  });
  renderPage();
  void refreshScores();
  if(pendingScore) void flushScore();
});
