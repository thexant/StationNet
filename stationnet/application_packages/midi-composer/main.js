"use strict";

StationNet.start(async (ui) => {
  const { PPQ, GM, uid, blank, clone, normalize, tickSeconds, secondsTick, writeMidi, readMidi } = await ui.modules.load("midi.js");
  const INDEX = "composer-index-v1", DRAFT = "composer-draft-v1";
  let projects = [], projectId = null, doc = blank(), trackIndex = 0, selected = new Set();
  let undo = [], redo = [], clipboard = [], dirty = false, notice = "", savePromise = null;
  let tool = "draw", snap = 120, duration = 480, velocity = 96, zoom = 1, viewTick = 0, topPitch = 84;
  let playing = false, position = 0, loop = false, loopStart = 0, loopEnd = 7680, drag = null, lastPoll = 0, deleteArmed = false;
  let W = 1200, H = 624, ROW = 12, visibleRows = 49;
  const X = 66, Y = 34;
  const pxBeat = () => 72 * zoom;
  const track = () => doc.tracks[trackIndex];
  const clamp = (n,a,b) => Math.max(a,Math.min(b,n));
  const step = t => snap ? Math.round(t/snap)*snap : Math.round(t);
  const xTick = x => Math.max(0,viewTick+(x-X)/pxBeat()*PPQ);
  const px = tick => X+(tick-viewTick)/PPQ*pxBeat();
  const rowPitch = y => clamp(topPitch-Math.floor((y-Y)/ROW),0,127);
  const same = (a,b) => JSON.stringify(a)===JSON.stringify(b);
  const key = id => `composer-project-${id}`;

  function snapshot() { undo.push(clone(doc)); if(undo.length>80)undo.shift(); redo=[]; }
  function changed() { dirty=true; scheduleSave(); if(playing) { ui.midi.stop().catch(()=>{});playing=false; } render(); }
  let saveTimer = null;
  function scheduleSave() { saveTimer?.(); saveTimer=ui.timeout(()=>save(), 900); }
  function save() {
    if(savePromise)return savePromise;
    if(!dirty)return Promise.resolve();
    savePromise=(async()=>{
      let success=false;
      try {
        const snapshotDoc=clone(doc), id=projectId || `p${Date.now().toString(36)}${Math.random().toString(36).slice(2,6)}`;
        await ui.storage.set(key(id), snapshotDoc);
        const item={id,name:snapshotDoc.name,updated:Date.now()};
        projects=[item,...projects.filter(p=>p.id!==id)];
        await ui.storage.set(INDEX,projects);
        projectId=id; dirty=!same(doc,snapshotDoc);
        await ui.storage.delete(DRAFT).catch(()=>{});
        notice="Saved to your StationNet account.";success=true;
      } catch(e) {
        notice=`Save failed: ${e.message}`;
        try { await ui.storage.set(DRAFT,doc); } catch (_) {}
      } finally {savePromise=null;render();if(dirty&&success)scheduleSave();}
    })();
    return savePromise;
  }
  async function open(id) {
    if(dirty){await save();if(dirty)return;}
    try {const value=await ui.storage.get(key(id));if(!value)throw Error("Project not found.");doc=normalize(value);projectId=id;trackIndex=0;selected.clear();undo=[];redo=[];position=0;viewTick=0;notice=`Opened ${doc.name}.`;render();}
    catch(e){notice=`Open failed: ${e.message}`;render();}
  }
  async function create() {if(dirty){await save();if(dirty)return;}await stop();doc=blank();projectId=null;trackIndex=0;selected.clear();undo=[];redo=[];viewTick=0;position=0;notice="New composition. Draw a note on the grid.";dirty=true;scheduleSave();render();}
  async function removeProject() {if(!projectId)return;await stop();const id=projectId;await ui.storage.delete(key(id));projects=projects.filter(p=>p.id!==id);await ui.storage.set(INDEX,projects);doc=blank();projectId=null;trackIndex=0;selected.clear();undo=[];redo=[];dirty=false;notice="Composition deleted.";render();}
  function edit(fn) {snapshot();fn();changed();}
  function addTempo(tick,bpm) {tick=Math.max(0,step(tick));bpm=clamp(Math.round(Number(bpm)||120),20,300);edit(()=>{doc.tempo=[...doc.tempo.filter(t=>t.tick!==tick),{tick,bpm}].sort((a,b)=>a.tick-b.tick);doc.bpm=doc.tempo[0].bpm;});}
  function addTrack(percussion=false) {
    if(percussion&&doc.tracks.some(t=>t.percussion)){notice="Use the existing drum track for percussion.";render();return;}
    if(!percussion&&doc.tracks.filter(t=>!t.percussion).length>=15){notice="Standard MIDI supports 15 melodic channels plus drums.";render();return;}
    edit(()=>{doc.tracks.push({id:`t${Date.now().toString(36)}`,name:percussion?"Drums":`Track ${doc.tracks.length+1}`,instrument:0,volume:100,pan:64,mute:false,solo:false,percussion,notes:[]});trackIndex=doc.tracks.length-1;selected.clear();});
  }
  function deleteSelected() {if(!selected.size)return;edit(()=>{track().notes=track().notes.filter(n=>!selected.has(n.id));selected.clear();});}
  function duplicate() {if(!selected.size)return;edit(()=>{const copies=track().notes.filter(n=>selected.has(n.id)).map(n=>({...n,id:uid(),tick:n.tick+n.duration}));track().notes.push(...copies);selected=new Set(copies.map(n=>n.id));});}
  function copy() {clipboard=track().notes.filter(n=>selected.has(n.id)).map(n=>({...n}));notice=`Copied ${clipboard.length} notes.`;render();}
  function paste() {if(!clipboard.length)return;edit(()=>{const start=Math.min(...clipboard.map(n=>n.tick));const copies=clipboard.map(n=>({...n,id:uid(),tick:Math.max(0,n.tick-start+step(position))}));track().notes.push(...copies);selected=new Set(copies.map(n=>n.id));});}
  function history(back) {const from=back?undo:redo,to=back?redo:undo;if(!from.length)return;to.push(clone(doc));doc=from.pop();trackIndex=clamp(trackIndex,0,doc.tracks.length-1);selected.clear();changed();}
  function projectMidi() {const output=clone(doc), solo=output.tracks.some(t=>t.solo);output.tracks=output.tracks.filter(t=>!t.mute&&(!solo||t.solo));if(!output.tracks.length)output.tracks=[{...doc.tracks[0],notes:[]}];return writeMidi(output);}
  async function play() {try {if(playing)return;await ui.midi.load(projectMidi());await ui.midi.seek(tickSeconds(doc,position));await ui.midi.play();playing=true;notice="Playing";render();}catch(e){notice=`Playback failed: ${e.message}`;render();}}
  async function pause() {await ui.midi.pause();playing=false;render();}
  async function stop() {await ui.midi.stop();playing=false;position=0;render();}
  async function seek(tick) {position=Math.max(0,Math.round(tick));if(playing)await ui.midi.seek(tickSeconds(doc,position));draw();}
  async function preview(pitch) {try {const sample=blank("Preview");sample.tracks[0].instrument=track().instrument;sample.tracks[0].percussion=track().percussion;sample.tracks[0].notes=[{id:uid(),tick:0,duration:PPQ/2,pitch,velocity}];await ui.midi.load(writeMidi(sample));await ui.midi.play();playing=false;}catch(e){notice=e.message;render();}}
  function measureTicks(){return PPQ*4*doc.numerator/doc.denominator;}
  function noteAt(x,y) {const pitch=rowPitch(y),tick=xTick(x);return [...track().notes].reverse().find(n=>n.pitch===pitch&&tick>=n.tick&&tick<=n.tick+n.duration);}
  function pointer(e) {
    if(e.phase==="start") {
      if(e.y<Y){seek(step(xTick(e.x))).catch(()=>{});return;}
      if(e.x<X){preview(rowPitch(e.y));return;}
      const hit=noteAt(e.x,e.y);
      if(tool==="erase") {if(hit)edit(()=>{track().notes=track().notes.filter(n=>n.id!==hit.id);selected.delete(hit.id);});return;}
      if(hit) {
        if(!e.shiftKey&&!e.ctrlKey&&!e.metaKey&&!selected.has(hit.id))selected.clear();
        if(e.shiftKey||e.ctrlKey||e.metaKey){if(selected.has(hit.id))selected.delete(hit.id);else selected.add(hit.id);draw();return;}
        selected.add(hit.id);const endX=px(hit.tick+hit.duration);
        drag={kind:endX-e.x<11?"resize":"move",startX:e.x,startY:e.y,before:clone(track().notes),ids:new Set(selected)};
        draw();return;
      }
      if(tool==="select"){selected.clear();drag={kind:"marquee",startX:e.x,startY:e.y,x:e.x,y:e.y};draw();return;}
      const tick=step(xTick(e.x)),pitch=rowPitch(e.y),n={id:uid(),tick,duration,pitch,velocity};
      snapshot();track().notes.push(n);selected=new Set([n.id]);drag={kind:"draw",id:n.id,startX:e.x,startY:e.y};draw();return;
    }
    if(!drag)return;
    if(e.phase==="move") {
      if(drag.kind==="draw") {const n=track().notes.find(n=>n.id===drag.id);if(n)n.duration=Math.max(snap||1,step(xTick(e.x))-n.tick);}
      if(drag.kind==="move"||drag.kind==="resize") {
        const dt=step((e.x-drag.startX)/pxBeat()*PPQ),dp=-Math.round((e.y-drag.startY)/ROW);
        track().notes=drag.before.map(n=>drag.ids.has(n.id)?{...n,tick:drag.kind==="move"?Math.max(0,n.tick+dt):n.tick,
          pitch:drag.kind==="move"?clamp(n.pitch+dp,0,127):n.pitch,duration:drag.kind==="resize"?Math.max(snap||1,n.duration+dt):n.duration}:n);
      }
      if(drag.kind==="marquee"){drag.x=e.x;drag.y=e.y;}
      draw();return;
    }
    if(e.phase==="end") {
      if(drag.kind==="marquee") {const a=Math.min(drag.startX,e.x),b=Math.max(drag.startX,e.x),c=Math.min(drag.startY,e.y),d=Math.max(drag.startY,e.y);
        selected=new Set(track().notes.filter(n=>px(n.tick+n.duration)>=a&&px(n.tick)<=b&&Y+(topPitch-n.pitch)*ROW<=d&&Y+(topPitch-n.pitch+1)*ROW>=c).map(n=>n.id));drag=null;draw();return;}
      if(drag.kind==="move"||drag.kind==="resize")undo.push({...clone(doc),tracks:doc.tracks.map((t,i)=>i===trackIndex?{...t,notes:drag.before}:t)});
      drag=null;changed();
    }
  }
  function canvasCommands() {
    const out=[{type:"rect",x:0,y:0,width:W,height:H,color:"#07100b"},{type:"rect",x:0,y:0,width:X,height:H,color:"#142419"},{type:"rect",x:X,y:0,width:W-X,height:Y,color:"#17311e"}];
    for(let r=0;r<visibleRows;r++){
      const pitch=topPitch-r,y=Y+r*ROW;
      if(pitch%12===0)out.push({type:"rect",x:X,y,width:W-X,height:ROW,color:"#10281b"});
      else if([1,3,6,8,10].includes(pitch%12))out.push({type:"rect",x:0,y,width:X,height:ROW,color:"#0b160e"});
      out.push({type:"line",x1:0,y1:y,x2:W,y2:y,color:"#25452d"});
      if(pitch%12===0)out.push({type:"text",x:8,y:y+6,text:`C${Math.floor(pitch/12)-1}`,size:11,color:"#b9e2bd"});
    }
    const first=Math.floor(viewTick/PPQ)-1,last=Math.ceil(viewTick/PPQ+(W-X)/pxBeat())+1,bar=measureTicks();
    for(let beat=first;beat<=last;beat++){
      const tick=beat*PPQ,x=px(tick);if(x<X||x>W)continue;
      const major=tick%bar===0;
      out.push({type:"line",x1:x,y1:0,x2:x,y2:H,color:major?"#4d8058":"#294530",width:major?2:1});
      if(major)out.push({type:"text",x:x+5,y:17,text:`${Math.floor(tick/bar)+1}`,size:12,color:"#d5e8d6"});
      if(snap&&snap<PPQ&&pxBeat()/PPQ*snap>=7)for(let s=snap;s<PPQ;s+=snap){const sx=px(tick+s);if(sx>=X&&sx<=W)out.push({type:"line",x1:sx,y1:Y,x2:sx,y2:H,color:"#1b3322"});}
    }
    if(loop){out.push({type:"line",x1:px(loopStart),y1:0,x2:px(loopStart),y2:H,color:"#e4aa55",width:2});out.push({type:"line",x1:px(loopEnd),y1:0,x2:px(loopEnd),y2:H,color:"#e4aa55",width:2});}
    const notes=track().notes.filter(n=>n.pitch<=topPitch&&n.pitch>topPitch-visibleRows&&px(n.tick+n.duration)>=X&&px(n.tick)<=W);
    for(const n of notes.slice(0,260)){
      const x=Math.max(X,px(n.tick)),end=Math.min(W,px(n.tick+n.duration)),y=Y+(topPitch-n.pitch)*ROW;
      out.push({type:"rect",x,y:y+1,width:Math.max(2,end-x),height:ROW-2,color:selected.has(n.id)?"#e5bd63":track().percussion?"#b987db":"#62c78b"});
      if(selected.has(n.id)&&end-x>13)out.push({type:"rect",x:end-5,y:y+2,width:3,height:ROW-4,color:"#27462c"});
    }
    if(drag?.kind==="marquee")out.push({type:"rect",x:Math.min(drag.startX,drag.x),y:Math.min(drag.startY,drag.y),width:Math.abs(drag.x-drag.startX),height:Math.abs(drag.y-drag.startY),color:"#496d5555"});
    out.push({type:"line",x1:px(position),y1:0,x2:px(position),y2:H,color:"#ffdb83",width:2});
    return out.slice(0,600);
  }
  function draw(){ui.update("roll",ui.canvas(W,H,canvasCommands(),{id:"roll",label:"Piano roll. Tap or click to draw notes; drag notes to move or resize at their right edge.",onPointer:pointer}));}
  function render() {
    const t=track(),measure=Math.floor(position/measureTicks())+1;
    const controls=[
      ui.title("MIDI Composer"),ui.text(`${doc.name} · ${dirty?"Unsaved changes":"Saved"} · Bar ${measure} · ${t.notes.length} notes on ${t.name}`,{tone:"accent"}),
      ui.menu("Transport",[{label:playing?"Pause":"Play",value:"play"},{label:"Stop",value:"stop"},{label:loop?"Loop on":"Loop off",value:"loop"},{label:"← Bar",value:"back"},{label:"Bar →",value:"forward"}],async({value})=>{if(value==="play")playing?await pause():await play();if(value==="stop")await stop();if(value==="loop"){loop=!loop;render();}if(value==="back")await seek(Math.max(0,position-measureTicks()));if(value==="forward")await seek(position+measureTicks());}),
      ui.menu("Project",[{label:"New",value:"new"},{label:"Save",value:"save"},{label:"Import MIDI",value:"import"},{label:"Download .mid",value:"export"},{label:deleteArmed?"Confirm delete":"Delete project",value:"delete"}],async({value})=>{try{if(value!=="delete")deleteArmed=false;if(value==="new")await create();if(value==="save")await save();if(value==="import"){if(dirty){await save();if(dirty)return;}const file=await ui.files.open({accept:".mid,.midi,audio/midi"});await stop();doc=readMidi(file.bytes);doc.name=file.name.replace(/\.midi?$/i,"").slice(0,100);projectId=null;trackIndex=0;selected.clear();undo=[];redo=[];dirty=true;scheduleSave();notice=`Imported ${file.name}.`;render();}if(value==="export")await ui.download.binary(`${doc.name.replace(/[^a-z0-9_-]+/gi,"-")||"composition"}.mid`,writeMidi(doc),"audio/midi");if(value==="delete"){if(deleteArmed){deleteArmed=false;await removeProject();}else{deleteArmed=true;notice="Select Confirm delete to remove this saved composition.";render();}}}catch(e){notice=e.message;render();}}),
      ui.input("projectName","Composition name",{value:doc.name,maxLength:100,onSubmit:({values})=>{edit(()=>doc.name=String(values.projectName||"Untitled composition").trim().slice(0,100));}}),
      ui.button("Rename",({values})=>edit(()=>doc.name=String(values.projectName||"Untitled composition").trim().slice(0,100))),
      ui.select("saved","Saved compositions",[{label:"Choose a saved composition…",value:""},...projects.map(p=>({label:p.name,value:p.id}))],{value:"",onChange:({value})=>{if(value)open(value);}}),
      ui.menu("Edit",[{label:"Undo",value:"undo"},{label:"Redo",value:"redo"},{label:"Copy",value:"copy"},{label:"Paste",value:"paste"},{label:"Duplicate",value:"duplicate"},{label:"Quantize selected",value:"quantize"},{label:"Delete notes",value:"delete"}],({value})=>{if(value==="undo")history(true);if(value==="redo")history(false);if(value==="copy")copy();if(value==="paste")paste();if(value==="duplicate")duplicate();if(value==="quantize"&&snap&&selected.size)edit(()=>track().notes.forEach(n=>{if(selected.has(n.id))n.tick=step(n.tick);}));if(value==="delete")deleteSelected();}),
      ui.menu("Tools",[{label:tool==="draw"?"● Draw":"Draw",value:"draw"},{label:tool==="select"?"● Select":"Select",value:"select"},{label:tool==="erase"?"● Erase":"Erase",value:"erase"},{label:"← Timeline",value:"left"},{label:"Timeline →",value:"right"},{label:"Pitch ↑",value:"up"},{label:"Pitch ↓",value:"down"},{label:"Zoom +",value:"zin"},{label:"Zoom −",value:"zout"}],({value})=>{if(["draw","select","erase"].includes(value))tool=value;if(value==="left")viewTick=Math.max(0,viewTick-measureTicks());if(value==="right")viewTick+=measureTicks();if(value==="up")topPitch=clamp(topPitch+12,48,127);if(value==="down")topPitch=clamp(topPitch-12,48,127);if(value==="zin")zoom=clamp(zoom*1.5,.5,3);if(value==="zout")zoom=clamp(zoom/1.5,.5,3);render();}),
      ui.canvas(W,H,canvasCommands(),{id:"roll",label:"Piano roll. Draw by dragging. Select and drag to move; drag right edge to resize. Touch users can use the tool controls above.",onPointer:pointer}),
      ui.text("Grid: drag empty space to draw; drag a note to move; drag its right edge to resize. Shift-click adds to selection. Select tool drags a selection box. Tap a piano key to preview. Shortcuts: Space play/pause; Ctrl/Cmd+A, C, V, D, Z, Shift+Z; Delete; D/S/E tools; arrows scroll."),
      ui.select("track","Active track",doc.tracks.map((tr,i)=>({label:`${i+1}. ${tr.name}${tr.mute?" [M]":""}${tr.solo?" [S]":""}`,value:String(i)})),{value:String(trackIndex),onChange:({value})=>{trackIndex=Number(value);selected.clear();render();}}),
      ui.menu("Tracks",[{label:"Add instrument",value:"add"},{label:"Add drums",value:"drums"},{label:"Remove track",value:"remove"},{label:t.mute?"Unmute":"Mute",value:"mute"},{label:t.solo?"Unsolo":"Solo",value:"solo"}],({value})=>{if(value==="add")addTrack();if(value==="drums")addTrack(true);if(value==="remove"&&doc.tracks.length>1)edit(()=>{doc.tracks.splice(trackIndex,1);trackIndex=clamp(trackIndex,0,doc.tracks.length-1);selected.clear();});if(value==="mute")edit(()=>t.mute=!t.mute);if(value==="solo")edit(()=>t.solo=!t.solo);}),
      ui.input("trackName","Track name",{value:t.name,maxLength:80}),ui.button("Rename track",({values})=>edit(()=>t.name=String(values.trackName||t.name).slice(0,80))),
      t.percussion?ui.text("Percussion uses General MIDI channel 10. Choose drum sounds by pitch in the piano roll.",{tone:"muted"}):
        ui.select("instrument","General MIDI instrument",GM.map((name,i)=>({label:`${i+1}. ${name}`,value:String(i)})),{value:String(t.instrument),onChange:({value})=>edit(()=>t.instrument=Number(value))}),
      ui.numberInput("volume","Track volume (0–127)",{value:t.volume,min:0,max:127}),ui.numberInput("pan","Pan (0 left, 64 center, 127 right)",{value:t.pan,min:0,max:127}),
      ui.button("Apply track mix",({values})=>edit(()=>{t.volume=clamp(Number(values.volume)||0,0,127);t.pan=clamp(Number(values.pan)||0,0,127);})),
      ui.numberInput("tempo","Tempo BPM",{value:doc.tempo[0].bpm,min:20,max:300}),
      ui.numberInput("numerator","Beats per bar",{value:doc.numerator,min:1,max:16}),
      ui.select("denominator","Beat unit",[2,4,8,16].map(n=>({label:String(n),value:String(n)})),{value:String(doc.denominator)}),
      ui.button("Apply song settings",({values})=>{const bpm=clamp(Number(values.tempo)||120,20,300),num=clamp(Number(values.numerator)||4,1,16),den=Number(values.denominator);edit(()=>{doc.tempo[0].bpm=bpm;doc.bpm=bpm;doc.numerator=num;doc.denominator=den;});}),
      ui.numberInput("tempoAt","Tempo change at playhead (BPM)",{value:doc.tempo.find(e=>e.tick===position)?.bpm||doc.bpm,min:20,max:300}),
      ui.menu("Tempo map",[{label:"Set change here",value:"set"},{label:"Remove change here",value:"remove"}],({value,values})=>{if(value==="set")addTempo(position,values.tempoAt);if(value==="remove"&&step(position)>0)edit(()=>doc.tempo=doc.tempo.filter(e=>e.tick!==step(position)));}),
      ui.select("duration","New note length",[[60,"1/32"],[120,"1/16"],[240,"1/8"],[480,"1/4"],[960,"1/2"],[1920,"1 bar"]].map(([n,l])=>({label:l,value:String(n)})),{value:String(duration),onChange:({value})=>{duration=Number(value);}}),
      ui.select("snap","Snap / grid",[[0,"Off"],[30,"1/64"],[60,"1/32"],[120,"1/16"],[240,"1/8"],[480,"1/4"]].map(([n,l])=>({label:l,value:String(n)})),{value:String(snap),onChange:({value})=>{snap=Number(value);draw();}}),
      ui.numberInput("velocity","New note velocity (1–127)",{value:velocity,min:1,max:127}),ui.button("Set velocity",({values})=>{velocity=clamp(Number(values.velocity)||96,1,127);if(selected.size)edit(()=>track().notes.forEach(n=>{if(selected.has(n.id))n.velocity=velocity;}));}),
      ui.menu("Loop region",[{label:"Start at playhead",value:"start"},{label:"End at playhead",value:"end"},{label:"Clear loop",value:"clear"}],({value})=>{if(value==="start")loopStart=position;if(value==="end")loopEnd=Math.max(position,loopStart+PPQ);if(value==="clear")loop=false;render();}),
      ui.text(`Tempo changes: ${doc.tempo.map(e=>`bar ${Math.floor(e.tick/measureTicks())+1}: ${e.bpm} BPM`).join(" · ")}`,{tone:"muted"}),
      ui.text(notice,{tone:notice.includes("failed")?"warning":"muted"})
    ];
    ui.render(controls[0],controls[1],controls[2],controls[8],controls[9],controls[7],
      controls[3],controls[4],controls[5],controls[6],...controls.slice(10));
  }
  ui.onKey(({code,phase,ctrlKey,metaKey,shiftKey})=>{if(phase!=="down")return;const mod=ctrlKey||metaKey;
    if(mod&&code==="KeyS")save();else if(mod&&code==="KeyZ")history(!shiftKey);else if(mod&&code==="KeyY")history(false);
    else if(mod&&code==="KeyC")copy();else if(mod&&code==="KeyV")paste();else if(mod&&code==="KeyD")duplicate();
    else if(mod&&code==="KeyA"){selected=new Set(track().notes.map(n=>n.id));draw();}
    else if(code==="Delete"||code==="Backspace")deleteSelected();else if(code==="Space")playing?pause():play();
    else if(code==="KeyD"){tool="draw";render();}else if(code==="KeyS"){tool="select";render();}else if(code==="KeyE"){tool="erase";render();}
    else if(code==="ArrowLeft"){viewTick=Math.max(0,viewTick-PPQ);render();}else if(code==="ArrowRight"){viewTick+=PPQ;render();}
    else if(code==="ArrowUp"){topPitch=clamp(topPitch+12,48,127);render();}else if(code==="ArrowDown"){topPitch=clamp(topPitch-12,48,127);render();}
  });
  ui.animate(async({time})=>{if(!playing||time-lastPoll<120)return;lastPoll=time;try{position=secondsTick(doc,await ui.midi.position());if(loop&&position>=loopEnd){position=loopStart;await ui.midi.seek(tickSeconds(doc,position));}let end=0;for(const t of doc.tracks)for(const n of t.notes)end=Math.max(end,n.tick+n.duration);if(!loop&&end&&position>=end){playing=false;await ui.midi.pause();render();return;}draw();}catch(_){playing=false;}});
  ui.onCleanup(()=>{ui.midi.stop().catch(()=>{});if(dirty)save().catch(()=>{});});
  try {const viewport=await ui.display.viewport();if(viewport.width<700){W=clamp(viewport.width,300,600);ROW=16;visibleRows=29;H=Y+ROW*visibleRows;}projects=Array.isArray(await ui.storage.get(INDEX))?await ui.storage.get(INDEX):[];const draft=await ui.storage.get(DRAFT);if(draft){doc=normalize(draft);dirty=true;notice="Recovered an unsaved draft.";}else if(projects.length){projectId=projects[0].id;doc=normalize(await ui.storage.get(key(projectId)));notice="Loaded your latest composition.";}}
  catch(e){notice=`Could not load saved compositions: ${e.message}`;}
  render();
});
