/* Cybercrawler v1.2 — StationNet Community — desktop/touch action HUD and bundled MIDI. */
StationNet.start(async (ui) => {
  'use strict';

  const MW = 29, MH = 29, TILE = 24, OX = 12, OY = 49, FINAL_FLOOR = 12;
  // A larger tactical window is available on desktop, without stretching the
  // mobile playfield. The action toolbar is painted into both canvas layouts.
  let VW = 13, VH = 12, SW = OX*2 + VW*TILE, SH = OY + VH*TILE + 125;
  const ACTION_GAP = 6, ACTION_HEIGHT = 38;
  function actionTop(){return OY + VH*TILE + 75;}
  function actionRect(i){
    const width = (SW-2*OX-3*ACTION_GAP)/4;
    return {x:OX+i*(width+ACTION_GAP),y:actionTop(),width,height:ACTION_HEIGHT};
  }
  function setBoardLayout(viewport){
    if(!viewport)return false;
    const desktop = viewport.width >= 650 && viewport.height >= 440;
    const nextW=desktop?17:13, nextH=desktop?13:12;
    if(VW===nextW&&VH===nextH)return false;
    VW=nextW;VH=nextH;
    SW=OX*2+VW*TILE;
    SH=OY+VH*TILE+125;
    return true;
  }
  const DIRS = [[0,-1],[-1,0],[0,1],[1,0]];
  const COLORS = {
    void:'#02090e', back:'#031016', panel:'#071920', floor:'#0a2028', floorB:'#0b252d',
    grid:'#11343e', wall:'#09262d', wallCore:'#12343b', neon:'#3bffb8',
    cyan:'#54dfff', amber:'#ffbb50', red:'#ff627a', violet:'#ce80ff',
    dull:'#66949d', white:'#d4faff', unseen:'#020a0d'
  };
  const CLASSES = {
    enforcer:{ label:'ENFORCER', mark:'E', color:COLORS.amber, hp:38, energy:9, power:7, armor:2, range:1, evade:0.02, crit:0.09,
      description:'Heavy armor / close-quarters damage. Skill: BREACH (adjacent shockwave).', skill:'BREACH', cost:4 },
    runner:{ label:'RUNNER', mark:'R', color:COLORS.cyan, hp:27, energy:12, power:5, armor:0, range:2, evade:0.23, crit:0.25,
      description:'Fast, evasive operator. Skill: BLINK (teleport 3 tiles forward).', skill:'BLINK', cost:4 },
    netrunner:{ label:'NETRUNNER', mark:'N', color:COLORS.violet, hp:24, energy:17, power:5, armor:0, range:3, evade:0.07, crit:0.12,
      description:'Ranged cyber-attacker. Skill: OVERLOAD (AOE damage + stun).', skill:'OVERLOAD', cost:5 },
    scrapper:{ label:'SCRAPPER', mark:'S', color:COLORS.neon, hp:32, energy:12, power:6, armor:1, range:1, evade:0.09, crit:0.12,
      description:'Adaptive salvager, extra loot. Skill: REPAIR (HP + barrier).', skill:'REPAIR', cost:5 }
  };
  const FOES = [
    {kind:'probe',name:'Recon Probe',mark:'p',color:'#f3a963',hp:7,dmg:2,armor:0,range:1,xp:4,aggro:6},
    {kind:'skitter',name:'Skitter Bot',mark:'s',color:'#f58ac1',hp:10,dmg:3,armor:0,range:1,xp:5,aggro:7},
    {kind:'sentry',name:'Sentry Unit',mark:'t',color:'#ff6a76',hp:13,dmg:4,armor:1,range:4,xp:7,aggro:6,stationary:true},
    {kind:'warden',name:'Grid Warden',mark:'W',color:'#ea69ff',hp:17,dmg:5,armor:2,range:1,xp:10,aggro:8},
    {kind:'hunter',name:'Hunter-Killer',mark:'H',color:'#ff405c',hp:21,dmg:6,armor:1,range:2,xp:12,aggro:9},
    {kind:'daemon',name:'ICE Daemon',mark:'D',color:'#bf7aff',hp:26,dmg:7,armor:2,range:3,xp:16,aggro:9},
    {kind:'boss',name:'ARCHON Core',mark:'A',color:'#ffde66',hp:88,dmg:12,armor:3,range:3,xp:75,aggro:14,stationary:false}
  ];
  let g = null, screen='title', cameraX=0, cameraY=0;
  let autoTimer=null, pendingSave=null, writing=false;
  let generation=0, lastLeaderboard=[];
  // The bundled score is 16 bars at 92 BPM. Music begins on a player action,
  // rather than before the browser has unlocked audio.
  const MUSIC_FILE='assets/cybercrawler.mid', MUSIC_SECONDS=16*4*60/92;
  let musicEnabled=true, musicReady=false, musicPlaying=false, musicLoading=false;
  let musicToken=0, musicPoll=null, musicChecking=false, musicPosition=0, musicFault='';

  async function startMusic(){
    if(!musicEnabled||musicPlaying||musicLoading||musicFault)return;
    musicLoading=true;
    const token=++musicToken;
    try{
      if(!musicReady){await ui.midi.load(Array.from(ui.assets.bytes(MUSIC_FILE)));musicReady=true;}
      if(token!==musicToken||!musicEnabled)return;
      await ui.midi.play();
      if(token!==musicToken||!musicEnabled){await ui.midi.stop();return;}
      musicPlaying=true;musicPosition=0;
      if(!musicPoll)musicPoll=ui.interval(()=>{void checkMusicLoop();},1100);
    }catch(e){
      musicFault=e&&e.message?String(e.message):'MIDI playback unavailable';
      musicEnabled=false;musicPlaying=false;
    }finally{musicLoading=false;}
  }
  async function checkMusicLoop(){
    if(!musicEnabled||!musicPlaying||musicChecking)return;
    musicChecking=true;
    try{
      const at=Number(await ui.midi.position());
      if(!musicEnabled||!musicPlaying)return;
      if(Number.isFinite(at)){
        // Some MIDI players hold at the final tick; others reset position to 0.
        if(at>=MUSIC_SECONDS-.4||(musicPosition>10&&at<.4)){
          await ui.midi.seek(0);
          if(musicPlaying&&musicEnabled)await ui.midi.play();
          musicPosition=0;
        }else musicPosition=at;
      }
    }catch(_e){}finally{musicChecking=false;}
  }
  function stopMusic(){
    musicToken+=1;musicPlaying=false;musicPosition=0;
    if(musicPoll){musicPoll();musicPoll=null;}
    try{void ui.midi.stop();}catch(_e){}
  }
  function toggleMusic(){
    musicEnabled=!musicEnabled;
    musicFault='';
    void ui.storage.set('cybercrawler.music',musicEnabled).catch(()=>{});
    if(musicEnabled)void startMusic();else stopMusic();
    if(screen==='title')showTitle(Boolean(g&&g.state==='playing'));
    else if(screen==='pack')showPack();
    else if(screen==='game')showGame();
  }
  function musicButton(){return ui.button('MUSIC: '+(musicEnabled?'ON':'OFF'),toggleMusic);}


  function ri(a,b){return ui.random.int(a,b);}
  function random(){return ui.random.next();}
  function clamp(n,a,b){return Math.max(a,Math.min(b,n));}
  function dist(a,b){return Math.abs(a.x-b.x)+Math.abs(a.y-b.y);}
  function inside(x,y){return x>=0&&x<MW&&y>=0&&y<MH;}
  function idx(x,y){return y*MW+x;}
  function tile(x,y){return inside(x,y)?g.map[idx(x,y)]:0;}
  function passable(x,y){return tile(x,y)!==0;}
  function foeAt(x,y){return g.foes.find(e=>e.hp>0&&e.x===x&&e.y===y);}
  function itemAt(x,y){return g.ground.find(it=>it.x===x&&it.y===y);}
  function note(t){g.message=t;g.logs.unshift(t);g.logs=g.logs.slice(0,14);}
  function beep(kind){
    try {
      const data={move:[210,22,0.018],hit:[105,70,0.070],hurt:[78,100,0.080],pick:[660,66,0.055],
        skill:[880,155,0.070],error:[115,75,0.043],down:[330,190,0.055],level:[975,140,0.055],
        death:[66,440,0.11],win:[1240,320,0.09]}[kind];
      if(data)ui.sound.playTone(data[0],data[1],{waveform:kind==='hurt'||kind==='death'?'sawtooth':'square',volume:data[2]});
    } catch(_e){}
  }
  function uid(){g.nextId+=1;return String(g.nextId);}
  function makeItem(kind,depth){
    const n=depth||g.floor;
    const base={id:uid(),kind};
    switch(kind){
      case 'med':return {...base,name:'Medpatch',desc:'Restores 15 HP.',heal:15};
      case 'battery':return {...base,name:'Energy Cell',desc:'Restores 9 energy.',energy:9};
      case 'emp':return {...base,name:'EMP Charge',desc:'Throw up to 5 tiles. Stuns machines within radius 2.'};
      case 'frag':return {...base,name:'Shock Charge',desc:'Throw up to 5 tiles. Damages enemies within radius 1.'};
      case 'shield':return {...base,name:'Barrier Charge',desc:'Adds a 10-point temporary shield.',shield:10};
      case 'weapon':{
        const tier=clamp(Math.floor(n/3)+ri(0,1),1,7);
        const names=['','Carbon Edge','Pulse Sidearm','Arc Injector','Mono Sabre','Rail Driver','Flux Cutter','Nova Rifle'];
        return {...base,name:names[tier]+' Mk'+tier,desc:'Equip to increase attack power.',power:tier+1};
      }
      case 'armor':{
        const tier=clamp(Math.floor((n+1)/4)+ri(0,1),1,5);
        return {...base,name:['','Mesh Vest','Reinforced Mesh','Phaseweave','Titan Plate','Aegis Shell'][tier],desc:'Equip to improve damage reduction.',armor:tier};
      }
      case 'data':return {...base,name:'Encrypted Cache',desc:'Pickup grants credits and experience.',credits:ri(7,14)+n*3,xp:ri(2,4)+n};
      default:return {...base,kind:'med',name:'Medpatch',desc:'Restores 15 HP.',heal:15};
    }
  }
  function lootKind(){
    const roll=ri(0,99);
    if(roll<26)return 'med';
    if(roll<44)return 'battery';
    if(roll<57)return 'frag';
    if(roll<69)return 'emp';
    if(roll<79)return 'shield';
    if(roll<88)return 'weapon';
    if(roll<94)return 'armor';
    return 'data';
  }
  function freshRun(cls){
    cancelRoute();
    if(musicEnabled)void startMusic();
    generation+=1;
    const c=CLASSES[cls];
    g={state:'playing',classId:cls,seed:ri(1,2000000000),floor:1,turns:0,kills:0,score:0,
      hp:c.hp,maxHp:c.hp,energy:c.energy,maxEnergy:c.energy,power:c.power,armor:c.armor,
      shield:0,lvl:1,xp:0,credits:0,weapon:null,vest:null,inventory:[],nextId:0,
      x:1,y:1,face:[0,1],map:[],explored:[],visible:[],foes:[],ground:[],exit:{x:1,y:1},
      message:'',logs:[],targetMode:null,sectorName:'',bossKilled:false};
    g.inventory.push(makeItem('med',1),makeItem('battery',1));
    if(cls==='runner')g.inventory.push(makeItem('emp',1));
    if(cls==='netrunner')g.inventory.push(makeItem('battery',1));
    if(cls==='scrapper')g.inventory.push(makeItem('shield',1));
    buildFloor();
    note('Sector 01 online. Locate the downlink.');
    screen='game';
    showGame();
    persist();
  }
  function roomOverlap(a,b){return a.x-1<b.x+b.w+1&&a.x+a.w+1>b.x-1&&a.y-1<b.y+b.h+1&&a.y+a.h+1>b.y-1;}
  function roomCenter(r){return {x:Math.floor(r.x+r.w/2),y:Math.floor(r.y+r.h/2)};}
  function carve(x,y){if(inside(x,y)&&x>0&&y>0&&x<MW-1&&y<MH-1)g.map[idx(x,y)]=1;}
  function hall(a,b){
    let x=a.x,y=a.y;
    if(ri(0,1)){
      while(x!==b.x){carve(x,y);x+=Math.sign(b.x-x);}
      while(y!==b.y){carve(x,y);y+=Math.sign(b.y-y);}
    }else{
      while(y!==b.y){carve(x,y);y+=Math.sign(b.y-y);}
      while(x!==b.x){carve(x,y);x+=Math.sign(b.x-x);}
    }
    carve(b.x,b.y);
  }
  function bfs(start,goal,opts={}){
    const q=[{x:start.x,y:start.y}], visited=new Int16Array(MW*MH).fill(-1);
    const pidx=idx(start.x,start.y);visited[pidx]=pidx;
    let head=0,found=null;
    while(head<q.length){
      const n=q[head++];
      if(goal(n)){found=n;break;}
      for(const [dx,dy] of DIRS){
        const x=n.x+dx,y=n.y+dy,i=idx(x,y);
        if(!inside(x,y)||visited[i]>=0||!passable(x,y))continue;
        if(opts.known&&!g.explored[i])continue;
        if(opts.avoidHazard&&tile(x,y)===2)continue;
        if(opts.avoidFoes&&foeAt(x,y))continue;
        visited[i]=idx(n.x,n.y);q.push({x,y});
      }
    }
    if(!found)return null;
    const path=[];
    let cur=idx(found.x,found.y);
    while(cur!==pidx){path.push({x:cur%MW,y:Math.floor(cur/MW)});cur=visited[cur];}
    path.reverse();
    return path;
  }
  function farthest(start){
    const queue=[start],distance=new Int16Array(MW*MH).fill(-1);
    distance[idx(start.x,start.y)]=0;
    let head=0,best=start,bestD=0;
    while(head<queue.length){
      const n=queue[head++],d=distance[idx(n.x,n.y)];
      if(d>bestD&&g.map[idx(n.x,n.y)]===1){best=n;bestD=d;}
      for(const [dx,dy] of DIRS){
        const x=n.x+dx,y=n.y+dy;
        if(!inside(x,y)||!passable(x,y)||distance[idx(x,y)]>=0)continue;
        distance[idx(x,y)]=d+1;queue.push({x,y});
      }
    }
    return best;
  }
  function buildFloor(){
    const f=g.floor;
    g.map=Array(MW*MH).fill(0);
    const rooms=[];
    for(let attempt=0;attempt<180&&rooms.length<ri(10,13);attempt++){
      const w=ri(4,7),h=ri(4,7),r={x:ri(2,MW-w-3),y:ri(2,MH-h-3),w,h};
      if(rooms.some(old=>roomOverlap(r,old)))continue;
      rooms.push(r);
      for(let y=r.y;y<r.y+h;y++)for(let x=r.x;x<r.x+w;x++)carve(x,y);
    }
    // Always keep a second, simple connected layout if random packing was unlucky.
    if(rooms.length<3){
      g.map=Array(MW*MH).fill(0);
      for(let y=3;y<MH-3;y++)for(let x=3;x<MW-3;x++)carve(x,y);
      rooms.length=0;rooms.push({x:3,y:3,w:6,h:6},{x:18,y:18,w:7,h:7});
    }
    for(let r=1;r<rooms.length;r++)hall(roomCenter(rooms[r-1]),roomCenter(rooms[r]));
    for(let r=0;r<Math.floor(rooms.length/3);r++){
      const a=ri(0,rooms.length-1),b=ri(0,rooms.length-1);
      if(a!==b)hall(roomCenter(rooms[a]),roomCenter(rooms[b]));
    }
    const start=roomCenter(rooms[0]);
    g.x=start.x;g.y=start.y;g.face=[0,1];
    g.exit=farthest(start);
    g.sectorName=['LOWER GRID','DATA VAULT','SERVICE SUBNET','BLACKSITE','NEON FOUNDRY','DEAD TERMINAL'][((f-1)%6)];
    g.foes=[];g.ground=[];g.explored=Array(MW*MH).fill(false);g.visible=Array(MW*MH).fill(false);
    g.bossKilled=false;g.targetMode=null;
    const cells=[];
    for(let y=2;y<MH-2;y++)for(let x=2;x<MW-2;x++){
      if(g.map[idx(x,y)]!==1||Math.abs(x-g.x)+Math.abs(y-g.y)<7||dist({x,y},g.exit)<2)continue;
      cells.push({x,y});
    }
    for(let k=0;k<Math.floor(8+f*0.6);k++){
      if(!cells.length)break;
      const c=cells.splice(ri(0,cells.length-1),1)[0];
      if(random()<0.52)g.map[idx(c.x,c.y)]=2;
    }
    const nFoes=Math.min(8+f*2,29);
    for(let n=0;n<nFoes;n++){
      const c=takeCell(cells);
      if(!c)break;
      const available=FOES.filter(e=>e.kind!=='boss');
      const tier=clamp(Math.floor((f+1)/3)+ri(-1,1),0,available.length-1);
      const t=available[tier];
      const hp=t.hp+Math.floor(f*1.9);
      g.foes.push({id:uid(),kind:t.kind,x:c.x,y:c.y,hp,maxHp:hp,dmg:t.dmg+Math.floor((f-1)*0.7),
        armor:t.armor+(f>=9?1:0),range:t.range,xp:t.xp+f*2,stun:0,alert:false});
    }
    const nItems=7+Math.floor(f/2);
    for(let n=0;n<nItems;n++){
      const c=takeCell(cells);if(!c)break;
      g.ground.push({...c,item:makeItem(lootKind(),f)});
    }
    if(f===FINAL_FLOOR){
      const p=closestOpen(g.exit,g.x,g.y);
      if(p){
        const t=FOES[6];
        g.foes.push({id:uid(),kind:t.kind,x:p.x,y:p.y,hp:t.hp,dmg:t.dmg,armor:t.armor,
          range:t.range,xp:t.xp,stun:0,alert:true});
      }
    }
    updateSight();
  }
  function takeCell(arr){return arr.length?arr.splice(ri(0,arr.length-1),1)[0]:null;}
  function closestOpen(point,notX,notY){
    let best=null,bd=Infinity;
    for(let y=2;y<MH-2;y++)for(let x=2;x<MW-2;x++){
      if(!passable(x,y)||x===notX&&y===notY||x===point.x&&y===point.y||foeAt(x,y))continue;
      const d=dist({x,y},point);
      if(d<bd){best={x,y};bd=d;}
    }
    return best;
  }
  function lineClear(a,b){
    let x=a.x,y=a.y,dx=Math.abs(b.x-a.x),dy=Math.abs(b.y-a.y);
    const sx=a.x<b.x?1:-1,sy=a.y<b.y?1:-1;
    let err=dx-dy;
    while(x!==b.x||y!==b.y){
      const e=2*err;
      if(e>-dy){err-=dy;x+=sx;}
      if(e<dx){err+=dx;y+=sy;}
      if((x!==b.x||y!==b.y)&&!passable(x,y))return false;
    }
    return true;
  }
  function updateSight(){
    g.visible=Array(MW*MH).fill(false);
    const r=6;
    for(let y=Math.max(0,g.y-r);y<=Math.min(MH-1,g.y+r);y++){
      for(let x=Math.max(0,g.x-r);x<=Math.min(MW-1,g.x+r);x++){
        if((x-g.x)**2+(y-g.y)**2>r*r+2)continue;
        if(lineClear({x:g.x,y:g.y},{x,y})){
          g.visible[idx(x,y)]=true;g.explored[idx(x,y)]=true;
        }
      }
    }
  }
  function attackRange(){return CLASSES[g.classId].range+(g.weapon&&g.weapon.kind==='weapon'&&g.weapon.power>=6?1:0);}
  function armor(){return g.armor+(g.vest?g.vest.armor:0);}
  function power(){return g.power+(g.weapon?g.weapon.power:0);}
  function canHit(enemy){return dist(g,enemy)<=attackRange()&&lineClear(g,enemy);}
  function gainXp(n){
    g.xp+=n;
    while(g.xp>=g.lvl*11+6){
      g.xp-=g.lvl*11+6;g.lvl+=1;
      g.maxHp+=5;g.hp=Math.min(g.maxHp,g.hp+12);
      g.maxEnergy+=2;g.energy=Math.min(g.maxEnergy,g.energy+5);
      if(g.lvl%2===0)g.power+=1;
      if(g.lvl%4===0)g.armor+=1;
      note('LEVEL '+g.lvl+' // systems upgraded!');beep('level');
    }
  }
  function hostileTurns(){
    if(g.state!=='playing')return;
    const occupied=new Set(g.foes.filter(e=>e.hp>0).map(e=>idx(e.x,e.y)));
    const enemies=g.foes.filter(e=>e.hp>0);
    for(const e of enemies){
      if(g.state!=='playing')break;
      if(e.stun>0){e.stun-=1;continue;}
      const t=FOES.find(v=>v.kind===e.kind);
      const d=dist(e,g);
      const sight=d<=t.aggro&&lineClear(e,g);
      if(sight)e.alert=true;
      if(!e.alert)continue;
      if(d<=e.range&&lineClear(e,g)&&(!t.stationary||d>0)){
        if(random()<CLASSES[g.classId].evade){note('DODGED '+t.name.toUpperCase()+' attack.');continue;}
        let damage=Math.max(1,e.dmg+ri(-1,2)-armor());
        if(g.shield>0){const absorbed=Math.min(g.shield,damage);g.shield-=absorbed;damage-=absorbed;}
        if(damage>0)g.hp=Math.max(0,g.hp-damage);
        note(t.name+' hits for '+damage+' damage.');
        if(damage>0)beep('hurt');
        if(g.hp<=0){die();break;}
      }else if(!t.stationary&&d>1){
        // Shortest-path chase navigates walls and bends in procedural corridors.
        const path=bfs(e,n=>dist(n,g)<=e.range&&lineClear(n,g),{avoidFoes:true});
        if(path&&path.length){
          const best=path[0];
          if(!occupied.has(idx(best.x,best.y))&&(best.x!==g.x||best.y!==g.y)){
            occupied.delete(idx(e.x,e.y));
            e.x=best.x;e.y=best.y;
            occupied.add(idx(e.x,e.y));
          }
        }
      }
    }
  }
  function endTurn(){
    g.turns+=1;
    if(g.turns%4===0)g.energy=Math.min(g.maxEnergy,g.energy+1);
    hostileTurns();
    if(g.state!=='playing')return;
    updateSight();
    refresh();persist();
  }
  function damageFoe(e,dmg,source){
    const real=Math.max(1,dmg-e.armor);
    e.hp=Math.max(0,e.hp-real);
    if(e.hp===0){
      const t=FOES.find(v=>v.kind===e.kind);
      g.kills+=1;g.credits+=1+Math.floor(g.floor/2);
      gainXp(e.xp);
      note(t.name+' eliminated (+'+e.xp+' XP).');
      if(e.kind==='boss')g.bossKilled=true;
      const odds=g.classId==='scrapper'?0.43:0.26;
      if(random()<odds&&!itemAt(e.x,e.y))g.ground.push({x:e.x,y:e.y,item:makeItem(lootKind(),g.floor)});
    }else note(source+' -> '+real+' damage to '+FOES.find(v=>v.kind===e.kind).name+'.');
    return real;
  }
  function basicAttack(e){
    if(!canHit(e)){beep('error');note('Target out of weapon range.');refresh();return false;}
    cancelRoute();
    let dmg=power()+ri(0,3);
    const crit=random()<CLASSES[g.classId].crit;
    if(crit)dmg=Math.round(dmg*1.65);
    damageFoe(e,dmg,crit?'CRITICAL':'STRIKE');
    beep('hit');endTurn();return true;
  }
  function attemptMove(dx,dy,automatic=false){
    if(g.state!=='playing')return false;
    if(!automatic)cancelRoute();
    const x=g.x+dx,y=g.y+dy;
    if(!passable(x,y)){note('Blocked: solid firewall.');beep('error');refresh();return false;}
    const e=foeAt(x,y);
    if(e)return basicAttack(e);
    g.face=[dx,dy];g.x=x;g.y=y;
    beep('move');
    if(tile(x,y)===2){
      const harm=2+Math.floor(g.floor/4);
      let dmg=harm;
      if(g.shield>0){const a=Math.min(g.shield,dmg);g.shield-=a;dmg-=a;}
      g.hp=Math.max(0,g.hp-dmg);
      note('EXPOSED CONDUIT: '+dmg+' arc damage.');
      if(!g.hp){die();return true;}
    }else if(!itemAt(x,y))note('Grid position '+x+':'+y+'.');
    if(itemAt(x,y))collect();
    if(g.x===g.exit.x&&g.y===g.exit.y){
      cancelRoute();
      if(g.floor===FINAL_FLOOR){
        if(!g.bossKilled){note('DOWNLINK LOCKED: ARCHON signature active.');endTurn();return true;}
        win();return true;
      }
      descend();return true;
    }
    endTurn();return true;
  }
  function collect(){
    const found=itemAt(g.x,g.y);
    if(!found){note('No salvage at your position.');refresh();return false;}
    const it=found.item;
    if(it.kind!=='data'&&g.inventory.length>=16){note('PACK FULL. Drop or use an item.');beep('error');refresh();return false;}
    g.ground.splice(g.ground.indexOf(found),1);
    if(it.kind==='data'){
      g.credits+=it.credits;gainXp(it.xp);note('Cache decoded: '+it.credits+' credits, '+it.xp+' XP.');
    }else{
      g.inventory.push(it);note('Salvaged: '+it.name+'.');
    }
    beep('pick');refresh();return true;
  }
  function manualPick(){
    cancelRoute();
    if(collect())endTurn();
  }
  function waitTurn(){
    if(g.targetMode){cancelTarget();return;}
    cancelRoute();note('Holding position.');endTurn();
  }
  function cancelTarget(){
    if(!g||!g.targetMode)return;
    g.targetMode=null;cancelRoute();note('Throw cancelled. No turn spent.');refresh();
  }
  function descend(){
    cancelRoute();
    const from=g.floor;
    g.floor++;
    g.hp=Math.min(g.maxHp,g.hp+5);
    g.energy=Math.min(g.maxEnergy,g.energy+4);
    buildFloor();
    note('DOWNLINK ACCEPTED // Sector '+String(g.floor).padStart(2,'0')+' connected.');
    beep('down');refresh();persist();
  }
  function skill(){
    if(g.state!=='playing')return;
    cancelRoute();
    const c=CLASSES[g.classId];
    if(g.energy<c.cost){note('Insufficient energy. Need '+c.cost+'.');beep('error');refresh();return;}
    const near=g.foes.filter(e=>e.hp>0);
    if(g.classId==='enforcer'){
      const adjacent=near.filter(e=>dist(g,e)<=1);
      if(!adjacent.length){note('BREACH: no adjacent targets.');beep('error');refresh();return;}
      g.energy-=c.cost;
      for(const e of adjacent){damageFoe(e,power()+ri(5,8),'BREACH');if(e.hp>0)e.stun=1;}
      note('BREACH shockwave discharged.');
    }else if(g.classId==='runner'){
      let x=g.x,y=g.y,moved=0;
      for(let i=0;i<3;i++){
        const nx=x+g.face[0],ny=y+g.face[1];
        if(!passable(nx,ny)||foeAt(nx,ny))break;
        x=nx;y=ny;moved++;
      }
      if(!moved){note('BLINK blocked. Face an open corridor.');beep('error');refresh();return;}
      g.energy-=c.cost;g.x=x;g.y=y;
      if(tile(x,y)===2){g.hp=Math.max(0,g.hp-2);if(!g.hp){die();return;}}
      if(itemAt(x,y))collect();
      note('BLINK displacement: '+moved+' tiles.');
      if(g.x===g.exit.x&&g.y===g.exit.y){
        if(g.floor<FINAL_FLOOR){descend();return;}
        if(g.bossKilled){win();return;}
      }
    }else if(g.classId==='netrunner'){
      const targets=near.filter(e=>dist(g,e)<=4&&lineClear(g,e));
      if(!targets.length){note('OVERLOAD: no target within 4 tiles.');beep('error');refresh();return;}
      g.energy-=c.cost;
      for(const e of targets){damageFoe(e,5+g.lvl+ri(0,3),'OVERLOAD');if(e.hp>0)e.stun=2;}
      note('OVERLOAD struck '+targets.length+' target(s).');
    }else{
      if(g.hp===g.maxHp&&g.shield>=12){note('REPAIR: integrity already optimal.');refresh();return;}
      g.energy-=c.cost;
      const healed=Math.min(g.maxHp-g.hp,12+g.lvl*2);
      g.hp+=healed;g.shield=clamp(g.shield+4,0,16);
      note('REPAIR: +'+healed+' HP, protective mesh reinforced.');
    }
    beep('skill');endTurn();
  }
  function useItem(id){
    if(!g||g.state!=='playing')return;
    cancelRoute();
    const i=g.inventory.findIndex(it=>it.id===id);
    if(i<0){showPack();return;}
    const it=g.inventory[i];
    if((it.kind==='med'&&g.hp===g.maxHp)||(it.kind==='battery'&&g.energy===g.maxEnergy)){
      note('No repair necessary.');showPack();return;
    }
    if(it.kind==='frag'||it.kind==='emp'){
      g.targetMode={id,kind:it.kind};
      note('TARGETING '+it.name.toUpperCase()+' // tap a tile within 5.');
      showGame();return;
    }
    let msg='';
    switch(it.kind){
      case 'med':{const n=Math.min(it.heal,g.maxHp-g.hp);g.hp+=n;msg='Applied Medpatch: +'+n+' HP.';break;}
      case 'battery':{const n=Math.min(it.energy,g.maxEnergy-g.energy);g.energy+=n;msg='Energy Cell: +'+n+' energy.';break;}
      case 'shield':g.shield=clamp(g.shield+it.shield,0,20);msg='Barrier stabilized: '+g.shield+' shield.';break;
      case 'weapon':{
        const old=g.weapon;g.weapon=it;g.inventory.splice(i,1);
        if(old)g.inventory.push(old);
        msg='Equipped '+it.name+'.';break;
      }
      case 'armor':{
        const old=g.vest;g.vest=it;g.inventory.splice(i,1);
        if(old)g.inventory.push(old);
        msg='Equipped '+it.name+'.';break;
      }
      default:showPack();return;
    }
    if(it.kind!=='weapon'&&it.kind!=='armor')g.inventory.splice(i,1);
    note(msg);beep('pick');showGame();endTurn();
  }
  function dropItem(id){
    const ix=g.inventory.findIndex(it=>it.id===id);
    if(ix<0)return;
    if(itemAt(g.x,g.y)){note('Tile already contains salvage.');showPack();return;}
    const it=g.inventory.splice(ix,1)[0];
    g.ground.push({x:g.x,y:g.y,item:it});
    note('Dropped '+it.name+'.');showGame();endTurn();
  }
  function throwItem(x,y){
    const aim=g.targetMode;
    if(!aim)return;
    if(dist(g,{x,y})>5||!lineClear(g,{x,y})){note('Invalid throw: target must be within 5 clear tiles.');beep('error');refresh();return;}
    const index=g.inventory.findIndex(it=>it.id===aim.id);
    if(index<0){g.targetMode=null;refresh();return;}
    g.inventory.splice(index,1);g.targetMode=null;
    const targets=g.foes.filter(e=>e.hp>0&&dist(e,{x,y})<=(aim.kind==='emp'?2:1));
    for(const e of targets){
      if(aim.kind==='emp')e.stun=Math.max(e.stun,3);
      else damageFoe(e,12+Math.floor(g.floor/2),'SHOCK');
    }
    note((aim.kind==='emp'?'EMP':'SHOCK')+' charge detonated: '+targets.length+' contacts.');
    beep('skill');endTurn();
  }
  function routeTo(x,y,target){
    const enemy=target||foeAt(x,y);
    const goals=enemy?
      n=>dist(n,enemy)<=attackRange()&&lineClear(n,enemy):
      n=>n.x===x&&n.y===y;
    const route=bfs(g,goals,{known:true,avoidFoes:true,avoidHazard:true}) ||
      bfs(g,goals,{known:true,avoidFoes:true});
    if(!route){note('No known path. Explore further or clear obstacles.');beep('error');refresh();return;}
    if(route.length===0){if(enemy)basicAttack(enemy);else if(itemAt(x,y))manualPick();return;}
    cancelRoute();
    const goal={x,y,enemyId:enemy?enemy.id:null};
    let steps=0;
    const execute=()=>{
      autoTimer=null;
      if(screen!=='game'||g.state!=='playing'||g.targetMode)return;
      const currentEnemy=goal.enemyId?g.foes.find(e=>e.id===goal.enemyId&&e.hp>0):null;
      if(currentEnemy&&canHit(currentEnemy)){basicAttack(currentEnemy);return;}
      const endpoint=currentEnemy||goal;
      const finish=currentEnemy?
        n=>dist(n,currentEnemy)<=attackRange()&&lineClear(n,currentEnemy):
        n=>n.x===endpoint.x&&n.y===endpoint.y;
      const recalc=bfs(g,finish,{known:true,avoidFoes:true,avoidHazard:true}) ||
        bfs(g,finish,{known:true,avoidFoes:true});
      if(!recalc||!recalc.length){refresh();return;}
      const next=recalc[0],dx=next.x-g.x,dy=next.y-g.y;
      if(Math.abs(dx)+Math.abs(dy)!==1)return;
      const ok=attemptMove(dx,dy,true);
      if(!ok||g.state!=='playing'||screen!=='game')return;
      steps++;
      if(g.foes.some(e=>e.hp>0&&g.visible[idx(e.x,e.y)]&&dist(e,g)<=3&&e.id!==goal.enemyId)){
        note('ROUTE HALTED: hostile contact nearby.');refresh();return;
      }
      if(steps>100)return;
      autoTimer=ui.timeout(execute,110);
    };
    execute();
  }
  function cancelRoute(){if(autoTimer){autoTimer();autoTimer=null;}}
  function canvasAction(index){
    if(!g||screen!=='game'||g.state!=='playing')return;
    // An aimed throw cannot trigger another action; CANCEL costs no turn.
    if(g.targetMode){
      if(index===3)cancelTarget();
      else{note('THROW MODE: tap a target or press CANCEL.');refresh();}
      return;
    }
    switch(index){
      case 0:showPack();break;
      case 1:skill();break;
      case 2:manualPick();break;
      case 3:waitTurn();break;
    }
  }
  function mapTap(event){
    if(screen!=='game'||g.state!=='playing'||event.phase!=='start')return;
    const x=event.x,y=event.y;
    // These are ordinary canvas hitboxes: they work with a mouse, finger,
    // stylus, or fullscreen canvas, and never depend on touch-only controls.
    for(let i=0;i<4;i++){
      const b=actionRect(i);
      if(x>=b.x&&x<b.x+b.width&&y>=b.y&&y<b.y+b.height){
        canvasAction(i);return;
      }
    }
    if(y<OY||y>=OY+VH*TILE||x<OX||x>=OX+VW*TILE)return;
    const mx=cameraX+Math.floor((x-OX)/TILE),my=cameraY+Math.floor((y-OY)/TILE);
    if(!inside(mx,my))return;
    if(g.targetMode){throwItem(mx,my);return;}
    if(!g.explored[idx(mx,my)]){note('UNKNOWN GRID. Move to reveal this sector.');refresh();return;}
    const enemy=foeAt(mx,my);
    if(enemy&&g.visible[idx(mx,my)]){
      if(canHit(enemy))basicAttack(enemy);else routeTo(mx,my,enemy);
      return;
    }
    if(mx===g.x&&my===g.y){
      if(itemAt(mx,my))manualPick();
      else {note('Standing by. Use WAIT to advance a turn.');refresh();}
      return;
    }
    if(!passable(mx,my)){note('Solid firewall.');refresh();return;}
    const dx=mx-g.x,dy=my-g.y;
    if(Math.abs(dx)+Math.abs(dy)===1){attemptMove(dx,dy);return;}
    routeTo(mx,my,null);
  }
  function die(){
    cancelRoute();g.state='dead';
    note('SIGNAL LOST // OPERATOR TERMINATED.');beep('death');
    finishScore();persist();showEnd(false);
  }
  function win(){
    cancelRoute();g.state='won';
    note('ARCHON DEFEATED // ROOT ACCESS GRANTED.');beep('win');
    finishScore();persist();showEnd(true);
  }
  function finishScore(){
    g.score=g.floor*200+g.kills*25+g.credits*3+g.lvl*50;
    try{ui.scores.submit(g.score).then(r=>{if(r&&r.leaderboard)lastLeaderboard=r.leaderboard;}).catch(()=>{});}catch(_e){}
  }
  function persist(){
    if(!g)return;
    pendingSave=JSON.stringify(g);
    if(!writing)void flushSave();
  }
  async function flushSave(){
    writing=true;
    while(pendingSave!==null){
      const val=pendingSave;pendingSave=null;
      try{await ui.storage.set('cybercrawler.run',JSON.parse(val));}catch(_e){}
    }
    writing=false;
  }
  function bitmap(t,x,y,color){return ui.graphics.bitmapText(t,ui.graphics.fonts.vga8x16,x,y,{color});}
  function text(t,x,y,color,size=11,align='left'){return {type:'text',text:String(t),x,y,size,align,color};}
  function rect(x,y,w,h,color){return {type:'rect',x,y,width:w,height:h,color};}
  function draw(){
    const out=[rect(0,0,SW,SH,COLORS.back),rect(0,0,SW,2,COLORS.neon),
      text('CYBERCRAWLER',12,15,COLORS.neon,15),
      text('SECTOR '+String(g.floor).padStart(2,'0'),SW-12,15,COLORS.white,11,'right'),
      text(CLASSES[g.classId].label+' / LV '+g.lvl,12,30,CLASSES[g.classId].color,10),
      text('CR '+g.credits,SW-12,30,COLORS.amber,10,'right'),
      rect(12,39,194,5,'#273640'),rect(12,39,194*g.hp/g.maxHp,5,COLORS.red),
      rect(214,39,110,5,'#273640'),rect(214,39,110*g.energy/g.maxEnergy,5,COLORS.cyan),
      rect(OX-1,OY-1,VW*TILE+2,VH*TILE+2,COLORS.grid)];
    cameraX=clamp(g.x-Math.floor(VW/2),0,MW-VW);
    cameraY=clamp(g.y-Math.floor(VH/2),0,MH-VH);
    const lines=[];
    for(let y=0;y<VH;y++)for(let x=0;x<VW;x++){
      const xx=cameraX+x,yy=cameraY+y,px=OX+x*TILE,py=OY+y*TILE,i=idx(xx,yy);
      const known=g.explored[i],seen=g.visible[i],t=g.map[i];
      if(!known){out.push(rect(px,py,TILE,TILE,COLORS.unseen));continue;}
      const floorColor=seen?(xx+yy)%2?COLORS.floor:COLORS.floorB:'#07171d';
      out.push(rect(px,py,TILE,TILE,t===0?(seen?COLORS.wall:'#06171b'):t===2?(seen?'#3c172c':'#241222'):floorColor));
      if(t===0){
        if(seen){out.push(rect(px+2,py+2,TILE-4,3,COLORS.wallCore));
          if((xx+yy)%3===0)out.push(rect(px+7,py+12,10,2,'#195360'));}
      }else if(seen){
        if(t===2)out.push(bitmap('~',px+8,py+4,COLORS.red));
        else if((xx*23+yy*13)%11===0)out.push(rect(px+11,py+11,2,2,'#16505b'));
      }
    }
    // Grid line batching avoids one command per edge.
    for(let x=0;x<=VW;x++)lines.push([OX+x*TILE,OY,OX+x*TILE,OY+VH*TILE]);
    for(let y=0;y<=VH;y++)lines.push([OX,OY+y*TILE,OX+VW*TILE,OY+y*TILE]);
    out.push({type:'lines',segments:lines,color:'#12303a',width:0.6});
    const overlay=(worldX,worldY,mark,color,background)=>{
      const x=(worldX-cameraX)*TILE+OX,y=(worldY-cameraY)*TILE+OY;
      if(x<OX||x>=OX+VW*TILE||y<OY||y>=OY+VH*TILE)return;
      if(background)out.push(rect(x+3,y+3,18,18,background));
      out.push(bitmap(mark,x+8,y+4,color));
    };
    if(g.explored[idx(g.exit.x,g.exit.y)])overlay(g.exit.x,g.exit.y,'>',g.visible[idx(g.exit.x,g.exit.y)]?COLORS.neon:'#32615d','#124032');
    for(const item of g.ground){
      if(!g.visible[idx(item.x,item.y)])continue;
      const glyph=item.item.kind==='data'?'$':item.item.kind==='med'?'+':item.item.kind==='battery'?'=':item.item.kind==='weapon'?'/':item.item.kind==='armor'?'#':'!';
      overlay(item.x,item.y,glyph,item.item.kind==='data'?COLORS.amber:COLORS.cyan,'#14383b');
    }
    for(const e of g.foes){
      if(e.hp<=0||!g.visible[idx(e.x,e.y)])continue;
      const t=FOES.find(z=>z.kind===e.kind);
      overlay(e.x,e.y,t.mark,t.color,'#421a30');
      const px=OX+(e.x-cameraX)*TILE,py=OY+(e.y-cameraY)*TILE;
      out.push(rect(px+3,py+21,18*e.hp/e.maxHp,2,COLORS.red));
      if(e.stun)out.push(rect(px+1,py+1,4,4,COLORS.cyan));
    }
    overlay(g.x,g.y,'@',CLASSES[g.classId].color,'#164f56');
    const foot=OY+VH*TILE+7;
    out.push(rect(0,foot,SW,SH-foot,COLORS.panel),
      text('HP '+g.hp+'/'+g.maxHp+'    EN '+g.energy+'/'+g.maxEnergy+'    ARM '+armor()+'   SH '+g.shield,12,foot+12,COLORS.white,11),
      text(g.targetMode?'[ THROW MODE ] TAP TARGET / CANCEL':g.sectorName+'  /  '+g.inventory.length+'/16 PACK',12,foot+28,g.targetMode?COLORS.amber:COLORS.dull,10),
      text(g.message.slice(0,Math.floor((SW-24)/7)),12,foot+46,g.targetMode?COLORS.amber:COLORS.neon,10));
    const labels=['PACK','SKILL','PICK',g.targetMode?'CANCEL':'WAIT'];
    const accents=[COLORS.cyan,COLORS.violet,COLORS.neon,g.targetMode?COLORS.amber:COLORS.dull];
    for(let i=0;i<4;i++){
      const b=actionRect(i),inactive=g.targetMode&&i!==3;
      const fg=inactive?'#49656a':accents[i];
      out.push(rect(b.x,b.y,b.width,b.height,inactive?'#122329':COLORS.grid));
      out.push(rect(b.x+1,b.y+1,b.width-2,b.height-2,inactive?'#0a161b':'#0c252e'));
      out.push(rect(b.x+3,b.y+4,3,b.height-8,fg));
      out.push(text(labels[i],b.x+b.width/2,b.y+b.height/2,fg,11,'center'));
    }
    return out;
  }
  function board(){return ui.canvas(SW,SH,draw(),{id:'board',label:'Cybercrawler tactical grid. Click tiles to move or attack, and click PACK, SKILL, PICK, WAIT to act.',
    touchLayout:'gamepad',controls:[],onPointer:mapTap});}
  function showGame(){
    if(!g)return;
    screen='game';
    ui.render(board(),ui.text(g.targetMode?
      'THROW MODE: tap a target, or press CANCEL (Space / Esc). No turn spent.' :
      'Click/tap tiles to move or attack • Arrow keys / WASD • Action buttons on the grid',
      {id:'help'}),musicButton());
  }
  function refresh(){
    if(screen==='game'&&g){
      ui.update('board',board());
      ui.update('help',ui.text(g.targetMode?
        'THROW MODE: tap a target, or press CANCEL (Space / Esc). No turn spent.' :
        'Click/tap tiles to move or attack • Arrow keys / WASD • Action buttons on the grid',
        {id:'help'}));
    }
  }
  function showPack(){
    if(!g||g.state!=='playing')return;
    cancelRoute();screen='pack';
    const contents=g.inventory.map(it=>({label:it.name+(it.kind==='weapon'?' [ATK +'+it.power+']':it.kind==='armor'?' [ARM +'+it.armor+']':''),value:it.id}));
    ui.render(ui.title('CYBERCRAWLER // PACK'),
      ui.text('HP '+g.hp+'/'+g.maxHp+'  |  ENERGY '+g.energy+'/'+g.maxEnergy+'  |  SLOTS '+g.inventory.length+'/16'),
      ui.text('WEAPON: '+(g.weapon?g.weapon.name:'Factory-issued')+'  |  ARMOR: '+(g.vest?g.vest.name:'Standard chassis')),
      ui.menu('INVENTORY',contents.length?contents:[{label:'(EMPTY)',value:'none',disabled:true}],({value})=>{
        if(value!=='none')showItem(value);
      }),
      musicButton(),
      ui.button('RETURN TO GRID',showGame));
  }
  function showItem(id){
    const it=g.inventory.find(v=>v.id===id);
    if(!it){showPack();return;}
    screen='item';
    ui.render(ui.title('PACK // '+it.name),ui.text(it.desc),
      ui.text(it.kind==='weapon'?'WEAPON POWER +'+it.power:it.kind==='armor'?'ARMOR +'+it.armor:'Utility consumable'),
      ui.button(it.kind==='weapon'||it.kind==='armor'?'EQUIP':'USE',()=>useItem(id)),
      ui.button('DROP ON CURRENT TILE',()=>dropItem(id)),
      ui.button('BACK TO PACK',showPack),ui.button('RETURN TO GRID',showGame));
  }
  function showTitle(hasSave){
    screen='title';
    const arr=[ui.title('CYBERCRAWLER'),
      ui.text('NEON GRID // ROGUELIKE ACCESS TERMINAL'),
      ui.text('Descend 12 randomized sectors, salvage upgrades, and defeat the ARCHON security core. Death ends the run.'),
      ui.heading('CHOOSE OPERATOR')];
    for(const key of Object.keys(CLASSES)){
      const c=CLASSES[key];
      arr.push(ui.button(c.label,()=>freshRun(key)),ui.text(c.description));
    }
    if(hasSave)arr.splice(3,0,ui.button('RESUME SAVED RUN',()=>{if(!g)return;if(musicEnabled)void startMusic();screen='game';showGame();}));
    arr.push(musicButton(),ui.text('Desktop: click grid tiles, use arrows/WASD, or click the four buttons under the map. I = PACK, Q = SKILL, E = PICK, Space = WAIT. Touch: tap grid tiles; directional pad remains available below the canvas. WAIT becomes CANCEL while aiming.'),
      ui.text('AUTOSAVE: your current run is stored privately on this station.'));
    ui.render(...arr);
  }
  function showEnd(success){
    screen='end';
    ui.render(ui.title(success?'ROOT ACCESS GRANTED':'SIGNAL LOST'),
      ui.text(success?'ARCHON security has been breached. The network is yours.':'Your operator has been derezzed. Your run is over.'),
      ui.heading('RUN REPORT'),
      ui.text('CLASS '+CLASSES[g.classId].label+' | SECTOR '+g.floor+'/'+FINAL_FLOOR+' | LEVEL '+g.lvl),
      ui.text('KILLS '+g.kills+' | TURNS '+g.turns+' | CREDITS '+g.credits),
      ui.text('SCORE '+g.score),
      ui.button('NEW RUN / SELECT CLASS',()=>{g=null;showTitle(false);}),
      ui.button('VIEW EVENT LOG',()=>showLog()));
  }
  function showLog(){
    screen='log';
    ui.render(ui.title('CYBERCRAWLER // EVENT LOG'),
      ui.list(g.logs.length?g.logs:['No events.']),
      ui.button('BACK TO REPORT',()=>showEnd(g.state==='won')));
  }
  function handleKey(e){
    if(e.phase!=='down'||e.repeat)return;
    const code=e.code;
    if(screen==='game'&&g&&g.state==='playing'){
      const dirs={ArrowUp:[0,-1],KeyW:[0,-1],ArrowLeft:[-1,0],KeyA:[-1,0],ArrowDown:[0,1],KeyS:[0,1],ArrowRight:[1,0],KeyD:[1,0]};
      if(g.targetMode&&(code==='Escape'||code==='Space'||code==='Backspace')){cancelTarget();return;}
      if(g.targetMode){note('Tap a target, or press CANCEL.');refresh();return;}
      if(dirs[code]){attemptMove(...dirs[code]);return;}
      if(code==='KeyI'||code==='Enter'){showPack();return;}
      if(code==='KeyQ'||code==='ShiftRight'){skill();return;}
      if(code==='KeyE'){manualPick();return;}
      if(code==='Space'){waitTurn();return;}
      if(code==='Escape'||code==='Backspace'){
        if(autoTimer){cancelRoute();note('Automatic movement cancelled.');refresh();}
        return;
      }
    }else if(screen==='item'&&(code==='Escape'||code==='Backspace'||code==='KeyI'))showPack();
    else if(screen==='pack'&&(code==='Escape'||code==='Backspace'||code==='KeyI'))showGame();
  }
  ui.onKey(handleKey);
  try{setBoardLayout(await ui.display.viewport());}catch(_e){}
  ui.presentation.onChange((_mode, viewport)=>{
    if(setBoardLayout(viewport)&&screen==='game')showGame();
  });
  ui.onCleanup(()=>{
    cancelRoute();stopMusic();
  });
  // Seed from the current clock; the host API owns the random stream thereafter.
  ui.random.seed((Date.now()>>>0)^((Date.now()/997)|0));
  try{
    const preference=await ui.storage.get('cybercrawler.music');
    if(preference===false)musicEnabled=false;
    const saved=await ui.storage.get('cybercrawler.run');
    if(saved&&saved.state==='playing'&&saved.hp>0&&Array.isArray(saved.map)&&saved.map.length===MW*MH&&
      Array.isArray(saved.foes)&&Array.isArray(saved.inventory)&&CLASSES[saved.classId]&&
      saved.exit&&Number.isInteger(saved.x)&&Number.isInteger(saved.y)){
      g=saved;g.targetMode=null;g.visible=Array(MW*MH).fill(false);updateSight();
      showTitle(true);
    }else showTitle(false);
  }catch(_e){showTitle(false);}
});
