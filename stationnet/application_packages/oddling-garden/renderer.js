export function createSceneRenderer(ui, W, H, byId, mood, stage, starAt, pixelScene, getContext, onPointer) {
  let state, reaction, petZoom, game, sparkBursts, compact, page, setSparkBursts;
  const C = (x, y, radius, color) => ({ type: "circle", x, y, radius, color });
  const R = (x, y, width, height, color) => ({ type: "rect", x, y, width, height, color });
  const L = (x1, y1, x2, y2, color, width = 2) =>
    ({ type: "line", x1, y1, x2, y2, color, width });
  const P = (points, fill, stroke) => ({ type: "polygon", points, fill, stroke, width: 3 });
  const T = (x, y, text, size = 13, color = "#b6f5d6", align = "left") =>
    ({ type: "text", x, y, text, size, color, align });
  function backdrop(cmd, t) {
    const p = state.pet;
    const thriving = p && !p.death && mood(p) >= 65;
    cmd.push(R(0, 0, W, H, "#07151b"), R(10, 10, 340, 220, thriving ? "#17372f" : "#11252b"));
    cmd.push(R(15, 15, 330, 190, thriving ? "#193b32" : "#10292b"), R(16, 207, 328, 1, "#376369"));
    for (let i = 0; i < 13; i++) {
      const x = 28 + ((i * 79) % 300), y = 31 + ((i * 47) % 125);
      if (state.settings.reducedMotion || i % 3 !== 0 || Math.sin(t * .001 + i) > 0)
        cmd.push(R(x, y, 2, 2, thriving ? "#3b7861" : "#2c5559"));
    }
    cmd.push(R(29, 189, 302, 4, "#3b5a5b"), R(31, 194, 298, 9, thriving ? "#365b42" : "#334845"));
    for (const x of [51, 105, 252, 303]) cmd.push(C(x, 199, 3, "#698276"));
    if (p?.plant) drawPlant(cmd, p.plant);
  }
  function drawPlant(cmd, plant) {
    const x = 70, base = 188;
    cmd.push(C(x, base + 6, 19, "#284538"), R(x - 13, base + 2, 26, 5, "#694d36"));
    if (!plant.growth) { cmd.push(C(x, base, 5, "#c4b073")); return; }
    const height = 12 + plant.growth * 10;
    const leaf = plant.kind === "sunbud" ? "#a5d66c" :
      plant.kind === "starvine" ? "#7ac9b7" : "#7bcf86";
    cmd.push(L(x, base, x, base - height, "#548a65", 5));
    for (let i = 1; i <= plant.growth; i++) {
      const y = base - i * 9;
      const side = i % 2 ? -1 : 1;
      cmd.push(L(x, y + 3, x + side * (11 + i), y - 4, "#548a65", 3),
        C(x + side * (13 + i), y - 5, 6, leaf));
    }
    if (plant.growth >= 3) {
      const flower = plant.kind === "sunbud" ? "#ffd477" :
        plant.kind === "starvine" ? "#bad4ff" : "#e7d9f5";
      cmd.push(C(x, base - height, 10, flower), C(x, base - height, 4, "#fff5d1"));
    }
  }
  function drawEgg(cmd, p, t) {
    const jiggle = state.settings.reducedMotion ? 0 : Math.sin(t * .004) * 2;
    const y = 125 + jiggle;
    cmd.push(C(180, 181, 54, "#294447"),
      C(180, y - 14, 49, "#e5ddb8"), C(180, y + 18, 42, "#e5ddb8"),
      C(169, y - 28, 22, "#faf0cf"), C(202, y + 10, 12, "#a2bcaf"),
      C(157, y + 22, 8, "#a2bcaf"),
      P([[167,y-8],[182,y+1],[174,y+13],[190,y+23]], "#e5ddb8", "#8f9c91"),
      T(180, 46, "A LITTLE MYSTERY", 17, "#ffe3a6", "center"),
      T(180, 218, "TAP THE EGG TO WARM IT", 12, "#91cbc1", "center"));
  }
  function drawCreature(cmd, p, t) {
    const s = byId(p.species);
    const st = stage(p), m = mood(p);
    const k = (st === "Baby" ? .62 : st === "Child" ? .82 : st === "Elder" ? .94 : 1.0) * petZoom;
    const fx = reaction && (t - reaction.started) / reaction.duration;
    const active = reaction && fx >= 0 && fx < 1 ? reaction.kind : "";
    let bounce = state.settings.reducedMotion ? 0 : p.sleeping ? Math.sin(t * .002) : Math.sin(t * .006) * 4;
    if (!state.settings.reducedMotion && ["meal","snack","talk"].includes(active)) bounce += Math.sin(fx * 24 * Math.PI) * 3;
    if (!state.settings.reducedMotion && ["cuddle","pet","play","wake"].includes(active)) bounce -= Math.abs(Math.sin(fx * 5 * Math.PI)) * 13 * (1 - fx);
    if (!state.settings.reducedMotion && ["bath","medicine"].includes(active)) bounce += Math.sin(fx * 28 * Math.PI) * 3;
    const cx = 180, cy = 127 + bounce;
    const X = dx => cx + dx * k, Y = dy => cy + dy * k;
    const circle = (dx,dy,r,color) => cmd.push(C(X(dx),Y(dy),r*k,color));
    const line = (ax,ay,bx,by,color,width=3) => cmd.push(L(X(ax),Y(ay),X(bx),Y(by),color,width*k));
    const poly = (points,color,edge) => cmd.push(P(points.map(([x,y]) => [X(x),Y(y)]),color,edge));
    cmd.push(C(cx, 182, 54*k, "#1f4947"));
    if (s.id === "blorble") {
      line(-24,-36,-34,-72,s.shade,6); line(24,-36,36,-69,s.shade,6);
      circle(-34,-72,10,s.color); circle(36,-69,10,s.color);
      circle(0,0,51,s.shade); circle(0,-6,48,s.color);
      circle(-19,-37,13,"#d1fff0"); circle(19,-35,11,"#bcffe9");
      circle(-35,30,10,s.shade); circle(34,31,10,s.shade);
    } else if (s.id === "quibble") {
      line(-30,41,-38,58,s.shade,7); line(30,41,38,58,s.shade,7);
      poly([[-55,-45],[-5,-60],[46,-45],[52,45],[-50,45]],s.shade);
      poly([[-44,-39],[-3,-51],[39,-38],[42,35],[-42,35]],s.color);
      for (let i=-2;i<=2;i++) poly([[i*16-8,-43],[i*16,-67],[i*16+9,-43]],s.color,s.shade);
      circle(-35,50,10,s.shade); circle(35,50,10,s.shade);
    } else if (s.id === "mimsy") {
      circle(-27,2,36,s.shade); circle(27,2,36,s.shade);
      circle(0,-27,38,s.shade); circle(0,17,39,s.shade);
      circle(-29,-4,31,s.color); circle(29,-4,31,s.color);
      circle(0,-27,34,s.color); circle(0,16,34,s.color);
      circle(-33,45,11,s.shade); circle(33,45,11,s.shade);
      circle(0,-48,12,"#ffe4f7");
    } else if (s.id === "zonkly") {
      poly([[-42,5],[-64,-20],[-38,-20],[-26,-62],[-9,-44],
        [15,-75],[28,-38],[58,-28],[40,-7],[52,29],[15,53],[-38,37]],s.shade);
      poly([[-36,2],[-48,-16],[-28,-14],[-21,-47],[-4,-29],
        [14,-60],[22,-29],[42,-22],[30,-4],[41,21],[11,42],[-28,29]],s.color);
      circle(-29,41,10,s.shade); circle(24,45,10,s.shade);
    } else {
      for(let i=0;i<5;i++) {
        const dx=(i-2)*19;
        line(dx,-31,dx+(i-2)*5,-68,s.shade,9);
        circle(dx+(i-2)*5,-69,10,s.color);
      }
      poly([[-49,-36],[44,-37],[60,15],[45,45],[-44,45],[-60,14]],s.shade);
      poly([[-43,-33],[39,-33],[52,12],[36,37],[-36,37],[-51,12]],s.color);
      circle(-31,45,11,s.shade); circle(29,45,11,s.shade);
    }
    // Facial features are shared; Zonkly has a single oversize eye.
    const blink = p.sleeping || (!state.settings.reducedMotion && Math.floor(t / 2850) % 11 === 0 && t % 2850 < 170);
    const eyePositions = s.id === "zonkly" ? [0] : [-17,17];
    for (const ex of eyePositions) {
      if (blink) line(ex-9,-7,ex+9,-7,"#203d45",3);
      else {
        circle(ex,-7,s.id === "zonkly" ? 18 : 11,"#f6fff3");
        circle(ex+3,-6,s.id === "zonkly" ? 9 : 6,"#1b2634");
        circle(ex+5,-10,3,"#ffffff");
      }
    }
    if (["meal","snack","talk"].includes(active)) {
      circle(0,23,7,"#31424a");
    } else if (m >= 65) {
      line(-9,20,0,26,"#31424a",3); line(0,26,10,20,"#31424a",3);
    } else if (m >= 35) {
      line(-9,22,9,22,"#31424a",3);
    } else {
      line(-9,28,0,20,"#31424a",3); line(0,20,9,28,"#31424a",3);
    }
    if (st === "Elder") {
      line(-21,-26,-8,-22,"#d1ddc8",3); line(8,-22,21,-26,"#d1ddc8",3);
      circle(-37,5,7,"#c6b5ae");
    }
    if (st === "Baby") {
      circle(-33, 11, 5, "#f3b4bd"); circle(33, 11, 5, "#f3b4bd");
    } else if (st === "Child") {
      circle(-39, 19, 4, "#dff8bb"); circle(39, 19, 4, "#dff8bb");
    } else if (st === "Adult") {
      line(-30, 28, -23, 33, s.shade, 3); line(23, 33, 30, 28, s.shade, 3);
    }
    if (s.id === "blorble" && m >= 70) circle(0, -44, 5, "#f7ffb6");
    if (s.id === "quibble" && m >= 70) circle(42, -37, 5, "#fff1b1");
    if (s.id === "mimsy" && m >= 70) circle(0, -47, 5, "#ffe6f8");
    if (s.id === "zonkly" && m >= 70) circle(15, -57, 5, "#f3e8a4");
    if (s.id === "poffle" && m >= 70) circle(0, -66, 5, "#eaffbd");
    if (p.clean < 35) {
      circle(-36,18,5,"#695348"); circle(36,23,6,"#695348");
      circle(-9,37,4,"#695348");
    }
    if (p.sleeping) cmd.push(T(256,72,"Z z z",20,"#bad7ff"));
    else if (m >= 70) cmd.push(T(265,76,"*",28,"#fff4a1"));
    else if (m < 30) cmd.push(T(263,80,"...",17,"#ffcda8"));
    if (st === "Baby") cmd.push(T(180,217,"tiny little noises",12,"#96b5af","center"));
  }
  function heart(cmd, x, y, size, color) {
    cmd.push(C(x-size*.27,y-size*.2,size*.3,color),
      C(x+size*.27,y-size*.2,size*.3,color),
      P([[x-size*.58,y-size*.1],[x+size*.58,y-size*.1],[x,y+size*.63]],color));
  }
  function effects(cmd,t) {
    if (state.settings.reducedMotion) return;
    if (!reaction) return;
    const f = (t - reaction.started) / reaction.duration;
    if (f < 0 || f >= 1) { if (f >= 1) reaction = null; return; }
    const k = reaction.kind;
    if (k === "warm") {
      for (let i=0; i<4; i++) {
        const a = f*6.28+i*1.57;
        cmd.push(C(180+Math.cos(a)*(53+12*f),124+Math.sin(a)*(61+10*f),4+2*(1-f),"#fff2a7"));
      }
      cmd.push(T(180,66,"WARM!",15,"#ffe6a6","center"));
    } else if (k === "meal" || k === "snack") {
      for (let i=0;i<3;i++) {
        const q = Math.max(0,Math.min(1,f*2-i*.28));
        const x = 69+110*q;
        const y = 157-37*Math.sin(q*Math.PI);
        cmd.push(C(x,y,k === "meal" ? 9 : 6,k === "meal" ? "#f4aa65" : "#ff91b5"),
          C(x-2,y-2,2,"#fff2bd"));
      }
      cmd.push(T(180,64,k === "meal" ? "NOM NOM!" : "YUM!",18,"#ffe5a3","center"));
    } else if (k === "bath") {
      for (let i=0;i<17;i++) {
        const x = 80+(i*47%205)+Math.sin(f*8+i)*4;
        const y = 197-125*((f+i*.115)%1);
        cmd.push(C(x,y,4+i%4,"#b1f2ff"),C(x-1,y-1,2,"#ecffff"));
      }
      cmd.push(T(180,66,"SPLISH SPLASH",15,"#d1faff","center"));
    } else if (k === "tidy") {
      const x=70+217*f;
      cmd.push(L(x,155,x+24,86,"#c49c65",5),R(x-13,152,33,13,"#ffcc78"));
      for(let i=0;i<5;i++) cmd.push(C(x-16+i*8,171+Math.sin(f*18+i)*4,3,"#d8e3a5"));
      cmd.push(T(180,60,"SWISH!",17,"#eeffbd","center"));
    } else if (k === "cuddle" || k === "pet") {
      for (let i=0;i<(k === "pet" ? 4 : 7);i++) {
        const x=95+i*(k === "pet" ? 43 : 28)+Math.sin(f*5+i)*10;
        const y=168-f*(80+(i%3)*24);
        heart(cmd,x,y,9+(i%3)*3,"#ffaacb");
      }
      cmd.push(T(180,61,"<3  <3  <3",15,"#ffcee8","center"));
    } else if (k === "talk") {
      cmd.push(C(266,79,33,"#dbf7e7"),C(246,99,7,"#dbf7e7"),
        C(239,108,4,"#dbf7e7"),T(266,78,Math.floor(f*8)%2 ? "BIP!" : "BORP!",14,"#1c4645","center"));
    } else if (k === "medicine") {
      cmd.push(R(98+f*64,110,19,35,"#8df0ac"),R(102+f*64,104,11,8,"#d2ffd0"),
        T(107+f*64,129,"+",18,"#1a6541","center"));
      for(let i=0;i<6;i++) {
        const x=145+(i%3)*35, y=91+Math.floor(i/3)*40-25*f;
        cmd.push(L(x-5,y,x+5,y,"#9affae",3),L(x,y-5,x,y+5,"#9affae",3));
      }
    } else if (k === "sleep" || k === "wake") {
      for (let i=0;i<3;i++) cmd.push(T(234+i*18,123-i*18-f*25,k === "sleep" ? "z" : "!",
        18+i*3,"#bfdcff"));
    } else if (k === "warn") {
      cmd.push(T(180,68,"NOPE!",17,"#ffd69c","center"));
    } else if (k === "play" || k === "hatch") {
      for(let i=0;i<16;i++) {
        const a=i*2.39996;
        const rad=f*95+(i%4)*7;
        const x=180+Math.cos(a)*rad, y=116+Math.sin(a)*rad;
        cmd.push(C(x,y,5*(1-f)+2, i%2 ? "#ffe891" : "#94fff3"));
      }
    }
  }
  function skeletonCommands(t) {
    const cmd = [];
    backdrop(cmd,t);
    const p = state.pet, old = p.death.cause === "old age";
    cmd.push(T(180,49,old ? "FOURTEEN DAYS TOGETHER" : "THE NEST IS QUIET",17,"#d8d6c4","center"));
    // Bone pile and skull remain visible until the player chooses to say goodbye.
    cmd.push(C(180,191,65,"#20383a"));
    const bone = "#dbd5b7", light="#f1e9d0", dark="#1b3235";
    cmd.push(L(132,181,214,202,bone,10),L(137,201,221,179,bone,10));
    for(const [x,y] of [[131,181],[214,202],[137,201],[221,179]]) cmd.push(C(x,y,7,light));
    cmd.push(L(180,150,180,182,bone,9));
    for(let i=0;i<3;i++) {
      const y=157+i*10;
      cmd.push(L(180,y,152-i*3,y+9,bone,5),L(180,y,208+i*3,y+9,bone,5));
    }
    const species = byId(p.species);
    cmd.push(C(180,107,39,bone));
    if (species.id === "zonkly") cmd.push(C(180,101,19,dark), C(186,95,4,"#668176"));
    else cmd.push(C(164,101,12,dark), C(196,101,12,dark));
    cmd.push(P([[180,112],[174,122],[186,122]],dark), R(162,136,36,13,bone));
    for(let i=0;i<4;i++) cmd.push(L(166+i*9,137,166+i*9,149,dark,2));
    // Keep a recognizable silhouette even in the farewell scene.
    if (species.id === "blorble") {
      cmd.push(L(152,82,137,58,bone,5),L(207,82,221,58,bone,5),C(135,58,7,light),C(222,58,7,light));
    } else if (species.id === "quibble") {
      cmd.push(R(145,75,12,15,bone),R(204,75,12,15,bone));
      for (let i=0;i<3;i++) cmd.push(P([[148+i*32,80],[158+i*27,54],[167+i*24,78]],bone));
    } else if (species.id === "zonkly") {
      for (let i=0;i<4;i++) cmd.push(P([[145+i*24,81],[151+i*24,48-i%2*9],[161+i*24,78]],bone));
    } else if (species.id === "mimsy") {
      cmd.push(C(141,113,12,bone),C(220,113,12,bone),C(180,67,13,light));
    } else {
      for (let i=0;i<5;i++) cmd.push(L(149+i*15,80,139+i*20,51+(i%2)*8,bone,5));
    }
    cmd.push(T(180,221,canvasName(p),14,"#a5b8aa","center"));
    return cmd;
  }
  function sceneCommands(t) {
    const cmd = [];
    backdrop(cmd,t);
    const p = state.pet;
    if (!p) return cmd;
    if (p.death) return skeletonCommands(t);
    if (stage(p) === "Egg") drawEgg(cmd,p,t);
    else drawCreature(cmd,p,t);
    effects(cmd,t);
    cmd.push(T(24,29,canvasName(p),13,"#c9eee1"));
    cmd.push(T(332,29,stage(p).toUpperCase(),12,"#c9eee1","right"));
    return cmd;
  }
  function canvasName(p) {
    return /^[\x20-\x7e]+$/.test(p.name) ? p.name.toUpperCase().slice(0, 20) : byId(p.species).name.toUpperCase();
  }
  function gameCommands(t) {
    const cmd = [];
    backdrop(cmd,t);
    if (!game) return cmd;
    cmd.push(T(24,28,"CATCH THE SPARKS!",17,"#e5ffb2"));
    const left = Math.max(0, Math.ceil((game.endAt - Date.now()) / 1000));
    cmd.push(T(335,27,left + "s",16,"#e5ffb2","right"),
      T(24,217,"SCORE " + game.hits,14,"#e5ffb2"));
    for (const s of game.stars) {
      const q = starAt(s,t);
      cmd.push(C(q.x,q.y,14,"#e5b251"), C(q.x,q.y,8,"#fffbc3"),
        L(q.x-21,q.y,q.x+21,q.y,"#ffef9b",2),
        L(q.x,q.y-21,q.x,q.y+21,"#ffef9b",2));
    }
    sparkBursts = sparkBursts.filter(b => t - b.at < 560);
    setSparkBursts(sparkBursts);
    for (const b of sparkBursts) {
      const f = (t - b.at) / 560;
      for (let i = 0; i < 8; i++) {
        const a = i * Math.PI / 4;
        const q = 11 + f * 30;
        cmd.push(R(b.x + Math.cos(a) * q, b.y + Math.sin(a) * q,
          7 - f * 4, 7 - f * 4, i % 2 ? "#fff6b3" : "#98fdec"));
      }
    }
    const { cursorX:x,cursorY:y } = game;
    cmd.push(C(x,y,18,"#314a54"), C(x,y,11,"#14252b"),
      L(x-23,y,x-13,y,"#f6ffff",2),L(x+13,y,x+23,y,"#f6ffff",2),
      L(x,y-23,x,y-13,"#f6ffff",2),L(x,y+13,x,y+23,"#f6ffff",2));
    return cmd;
  }
  function sceneCanvas(full = true) {
    ({ state, reaction, petZoom, game, sparkBursts, compact, page, setSparkBursts } = getContext());
    const t=Date.now();
    const shapes=game && page==="game" ? gameCommands(t) : sceneCommands(t);
    const options = {
      id: "scene",
      label: page === "game" ? "Spark Chase: catch the glowing sparks" :
        state.pet?.death ? "The skeleton of " + state.pet.name + " in the quiet habitat" :
        state.pet && stage(state.pet) === "Egg" ? "Egg in its nest: tap or use Warm egg" :
        "Pixel-art habitat of " + (state.pet?.name || "your Oddling") + ": tap, stroke, or hold to care"
    };
    if (full) options.onPointer = onPointer;
    if (full && game && page === "game") {
      options.touchLayout = {
        type: "gamepad", dpad: false,
        joystick: { name: "oddling-aim", x: 19, y: 50, size: compact ? 86 : 108 }
      };
      options.controls = [
        { label: "Catch", code: "Space", x: 83, y: 38, size: 58 },
        { label: "Done", code: "KeyQ", x: 70, y: 80, size: 45 }
      ];
    }
    return ui.canvas(W,H,pixelScene(shapes), options);
  }
  return sceneCanvas;
}
