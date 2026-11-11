import {useCallback,useEffect,useRef,useState} from 'react';
import {MousePointer2,Hand,Plus,Move,Link2,Paintbrush,RectangleHorizontal,Circle,Trash2,Minus,LocateFixed,Layers,Mountain,ArrowUpRight,PanelLeftClose} from 'lucide-react';
import {api,MapCell,Edge,Project,Registry,Row,Selection,rows} from './data';
import {alpha,edgeColor,localPoint,mix,terrainColor,usePalette} from './theme';

// `open` asks the host to bring the inspector forward; phones only do so for deliberate inspection.
type Props={project:Project;registry:Registry;selection:Selection|null;activeCell:MapCell|null;mobile?:boolean;onCell:(c:MapCell|null,open?:boolean)=>void;onSelect:(s:Selection,open?:boolean)=>void;onOperation:(operation:string,args:Row)=>Promise<any>;onPlace:(index:number,p:Row)=>void;onConnect:(from:string,to:string,direction:string)=>void;onError:(s:string)=>void;focus?:{x:number;y:number;z:number;nonce:number}};
type Point={x:number;y:number};
const tools=[['select','Inspect',MousePointer2],['pan','Pan',Hand],['room','Add room',Plus],['place','Place / move room',Move],['connect','Draw passage',Link2],['brush','Terrain brush',Paintbrush],['rectangle','Rectangle fill',RectangleHorizontal],['ellipse','Ellipse fill',Circle],['erase','Erase wilderness',Trash2]] as const;
const paintTools=['brush','rectangle','ellipse','erase'];
type Drag={start:Point;screen:Point;origin:Point;points:Point[];pan:boolean;scale:number;deferred:boolean;moved:boolean};
type Pinch={distance:number;mid:Point;view:Row};
export function MapCanvas({project,registry,selection,activeCell,mobile=false,onCell,onSelect,onOperation,onPlace,onConnect,onError,focus}:Props){
 const preferences='forge-map:'+project.id;const palette=usePalette();
 const [view,setView]=useState(()=>{try{return JSON.parse(localStorage.getItem(preferences)||'null')||{x:-6,y:-4,z:0,size:64};}catch{return {x:-6,y:-4,z:0,size:64};}});
 const [tool,setTool]=useState('select');const [preset,setPreset]=useState('');const [region,setRegion]=useState('');const [emptyOnly,setEmptyOnly]=useState(true);const [tray,setTray]=useState(!mobile);const [trayQuery,setTrayQuery]=useState('');
 const [data,setData]=useState<{cells:MapCell[];edges:Edge[]}>({cells:[],edges:[]});const [dimensions,setDimensions]=useState({width:800,height:600});const [hover,setHover]=useState<Point|null>(null);const [anchor,setAnchor]=useState<Point|null>(null);const [linkStart,setLinkStart]=useState<string|null>(null);const [loading,setLoading]=useState(false);
 const holder=useRef<HTMLDivElement>(null);const canvas=useRef<HTMLCanvasElement>(null);const drag=useRef<Drag|null>(null);const sequence=useRef(0);
 const pointers=useRef(new Map<number,Point>());const pinch=useRef<Pinch|null>(null);
 const roomRows=rows(project,registry,'rooms');const unplaced=roomRows.map((r,index)=>({r,index})).filter(({r})=>![r.x,r.y,r.z].every(Number.isInteger)&&(!trayQuery||[r.id,r.name].some(x=>String(x).toLowerCase().includes(trayQuery.toLowerCase()))));
 const [trayPage,setTrayPage]=useState(0);const visibleTrayPage=Math.min(trayPage,Math.max(0,Math.ceil(unplaced.length/100)-1));
 const presets=rows(project,registry,'presets');const regions=rows(project,registry,'regions');
 useEffect(()=>{if(!preset&&presets.length)setPreset(presets[0].key);},[presets.length]);
 useEffect(()=>{localStorage.setItem(preferences,JSON.stringify(view));},[view,preferences]);
 useEffect(()=>{if(!holder.current)return;const observer=new ResizeObserver(entries=>{const r=entries[0].contentRect;setDimensions({width:Math.max(10,r.width),height:Math.max(10,r.height)});});observer.observe(holder.current);return()=>observer.disconnect();},[]);
 useEffect(()=>{if(focus)setView((v:Row)=>({...v,z:focus.z,x:focus.x-Math.floor(dimensions.width/v.size/2),y:focus.y-Math.floor(dimensions.height/v.size/2)}));},[focus?.nonce]);
 useEffect(()=>{
  const i=++sequence.current;setLoading(true);
  const q=new URLSearchParams({z:String(view.z),x:String(Math.floor(view.x)),y:String(Math.floor(view.y)),width:String(Math.min(130,Math.ceil(dimensions.width/view.size)+2)),height:String(Math.min(130,Math.ceil(dimensions.height/view.size)+2))});
  const timer=setTimeout(()=>api<{cells:MapCell[];edges:Edge[]}>(`/d/${project.id}/map?${q}`).then(v=>{if(sequence.current===i)setData(v);}).catch(e=>{if(sequence.current===i)onError(e.message);}).finally(()=>{if(sequence.current===i)setLoading(false);}),80);
  return()=>clearTimeout(timer);
 },[view.x,view.y,view.z,view.size,dimensions.width,dimensions.height,project.revision,project.id]);
 const xy=(event:React.PointerEvent<HTMLCanvasElement>):Point=>{const local=localPoint(event.currentTarget,event.clientX,event.clientY);return {x:Math.floor(local.x/view.size+view.x),y:Math.floor(local.y/view.size+view.y)};};
 const pointerScale=(element:Element)=>localPoint(element,0,0).scale;
 const cellAt=(p:Point)=>data.cells.find(c=>c.x===p.x&&c.y===p.y);
 const selectCell=(c:MapCell|null,open=true)=>{onCell(c,open);if(c&&!c.preset){const index=roomRows.findIndex(r=>r.id===c.id);if(index>=0)onSelect({collection:'rooms',index},open);}};
 const handleClick=(p:Point)=>{
  const c=cellAt(p);
  if(tool==='select'){selectCell(c||null);return;}
  if(tool==='room'){
   if(c){onError('Choose an empty square, or convert wilderness in the inspector.');return;}
   onOperation('create_record',{collection:'rooms',record:{name:'New room',description:'Describe this place.',x:p.x,y:p.y,z:view.z,grid_link:false}}).catch(()=>{});return;
  }
  if(tool==='place'){
   if(selection?.collection!=='rooms'){onError('Choose a room from the tray or map first.');return;}
   onPlace(selection.index,{...p,z:view.z});return;
  }
  if(tool==='connect'){
   if(!c){onError('Choose a room as the passage endpoint.');return;}
   if(!linkStart){setLinkStart(c.id);selectCell(c,false);return;}
   if(linkStart===c.id){setLinkStart(null);return;}
   const a=roomRows.find(r=>r.id===linkStart)||data.cells.find(r=>r.id===linkStart);
   let d='north';
   if(a){const dx=Math.sign(c.x-a.x),dy=Math.sign(c.y-a.y);d=view.z!==a.z?(view.z>a.z?'up':'down'):({'0,-1':'north','1,-1':'northeast','1,0':'east','1,1':'southeast','0,1':'south','-1,1':'southwest','-1,0':'west','-1,-1':'northwest'} as Row)[`${dx},${dy}`]||'in';}
   onConnect(linkStart,c.id,d);setLinkStart(null);
  }
 };
 // Two fingers always pinch-zoom and pan. One finger with a click tool pans, and a tap
 // (released without moving) acts, so a pinch never fires the first finger's click.
 const pinchState=(element:Element):Pinch=>{
  const [a,b]=Array.from(pointers.current.values());const mid=localPoint(element,(a.x+b.x)/2,(a.y+b.y)/2);
  return {distance:Math.max(1,Math.hypot(a.x-b.x,a.y-b.y)),mid:{x:mid.x,y:mid.y},view};
 };
 const pointerDown=(e:React.PointerEvent<HTMLCanvasElement>)=>{
  e.currentTarget.setPointerCapture(e.pointerId);pointers.current.set(e.pointerId,{x:e.clientX,y:e.clientY});
  if(pointers.current.size===2){drag.current=null;setAnchor(null);setHover(null);pinch.current=pinchState(e.currentTarget);return;}
  if(pointers.current.size>2)return;
  const p=xy(e);const pan=tool==='pan'||e.button===1||e.button===2||e.altKey;const clickTool=!paintTools.includes(tool);
  const deferred=e.pointerType==='touch'&&clickTool&&!pan;
  drag.current={start:p,screen:{x:e.clientX,y:e.clientY},origin:{x:view.x,y:view.y},points:[p],pan,scale:pointerScale(e.currentTarget),deferred,moved:false};setAnchor(p);
  if(!pan&&clickTool&&!deferred)handleClick(p);
 };
 const pointerMove=(e:React.PointerEvent<HTMLCanvasElement>)=>{
  if(pointers.current.has(e.pointerId))pointers.current.set(e.pointerId,{x:e.clientX,y:e.clientY});
  const start=pinch.current;
  if(start&&pointers.current.size>=2){
   const now=pinchState(e.currentTarget);const size=Math.max(18,Math.min(128,start.view.size*now.distance/start.distance));
   const wx=start.view.x+start.mid.x/start.view.size,wy=start.view.y+start.mid.y/start.view.size;
   setView((v:Row)=>({...v,size,x:wx-now.mid.x/size,y:wy-now.mid.y/size}));return;
  }
  const d=drag.current;const p=xy(e);if(e.pointerType==='mouse'||d)setHover(p);if(!d)return;
  if(d.deferred&&!d.moved&&Math.hypot(e.clientX-d.screen.x,e.clientY-d.screen.y)>8){d.moved=true;d.pan=true;}
  if(d.pan)setView((v:Row)=>({...v,x:d.origin.x-(e.clientX-d.screen.x)*d.scale/v.size,y:d.origin.y-(e.clientY-d.screen.y)*d.scale/v.size}));
  else if(tool==='brush'&&!d.points.some(a=>a.x===p.x&&a.y===p.y)){
   const last=d.points[d.points.length-1];const steps=Math.max(Math.abs(p.x-last.x),Math.abs(p.y-last.y));
   for(let i=1;i<=steps&&d.points.length<1000;i++)d.points.push({x:Math.round(last.x+(p.x-last.x)*i/steps),y:Math.round(last.y+(p.y-last.y)*i/steps)});
  }
 };
 const releasePointer=(id:number)=>{pointers.current.delete(id);if(pinch.current){if(pointers.current.size<2)pinch.current=null;return true;}return false;};
 const pointerUp=(e:React.PointerEvent<HTMLCanvasElement>)=>{
  const pinching=releasePointer(e.pointerId);const touch=e.pointerType!=='mouse';
  const d=drag.current;drag.current=null;setAnchor(null);if(touch)setHover(null);
  if(pinching||!d)return;
  if(d.deferred){if(!d.moved)handleClick(d.start);return;}
  if(d.pan)return;
  const p=xy(e);
  if(paintTools.includes(tool)){
   if(!preset&&tool!=='erase'){onError('Create or choose a terrain preset first.');return;}
   const args={z:view.z,preset:tool==='erase'?'':preset,region,empty_only:emptyOnly,x1:d.start.x,y1:d.start.y,x2:p.x,y2:p.y,shape:tool==='ellipse'?'ellipse':'rectangle',points:d.points.map(a=>[a.x,a.y])};
   onOperation(tool==='brush'?'paint_stroke':'paint',args).catch(()=>{});
  }
 };
 const zoom=(n:number)=>setView((v:Row)=>{const size=Math.max(18,Math.min(128,v.size+n));return {...v,size,x:v.x+dimensions.width/v.size/2-dimensions.width/size/2,y:v.y+dimensions.height/v.size/2-dimensions.height/size/2};});
 useEffect(()=>{
  const element=canvas.current;if(!element)return;
  const wheel=(e:WheelEvent)=>{e.preventDefault();if(e.ctrlKey||e.metaKey)zoom(e.deltaY>0?-5:5);else setView((v:Row)=>({...v,x:v.x+e.deltaX/v.size,y:v.y+e.deltaY/v.size}));};
  element.addEventListener('wheel',wheel,{passive:false});return()=>element.removeEventListener('wheel',wheel);
 },[dimensions.width,dimensions.height]);
 useEffect(()=>{
  const element=canvas.current;if(!element)return;const ctx=element.getContext('2d');if(!ctx)return;
  // Back the canvas at the visual size so the page's CSS zoom does not blur the map.
  const dpr=(window.devicePixelRatio||1)/(localPoint(element,0,0).scale||1);element.width=Math.round(dimensions.width*dpr);element.height=Math.round(dimensions.height*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);
  const {width,height}=dimensions;const s=view.size;const sx=(x:number)=>(x-view.x)*s;const sy=(y:number)=>(y-view.y)*s;
  const P=palette;const mono=P.mono||'monospace';
  ctx.fillStyle=P.black;ctx.fillRect(0,0,width,height);
  ctx.strokeStyle=P.border;ctx.lineWidth=.5;
  for(let x=Math.floor(view.x);x<=view.x+width/s+1;x++){ctx.beginPath();ctx.moveTo(sx(x),0);ctx.lineTo(sx(x),height);ctx.stroke();}
  for(let y=Math.floor(view.y);y<=view.y+height/s+1;y++){ctx.beginPath();ctx.moveTo(0,sy(y));ctx.lineTo(width,sy(y));ctx.stroke();}
  const visible=new Map(data.cells.map(c=>[c.id,c]));const verticalEdges=new Map<string,Edge[]>();for(const e of data.edges){if(['up','down'].includes(e.direction)){const list=verticalEdges.get(e.from)||[];list.push(e);verticalEdges.set(e.from,list);}}
  for(const c of data.cells){
   if(!c.preset)continue;
   ctx.fillStyle=terrainColor(c.terrain,P);ctx.fillRect(sx(c.x)+1,sy(c.y)+1,s-2,s-2);
   if(s>28){ctx.fillStyle=alpha(P.greenBright,.55);ctx.font=`${Math.max(12,s*.27)}px ${mono}`;ctx.textAlign='center';ctx.fillText(c.glyph||'·',sx(c.x)+s/2,sy(c.y)+s*.54);}
   if(c.region&&s>46){ctx.fillStyle=P.grey;ctx.font=`8px ${mono}`;ctx.fillText(c.region.slice(0,9),sx(c.x)+s/2,sy(c.y)+s-8);}
  }
  for(const e of data.edges){
   const a=visible.get(e.from);if(!a)continue;
   const b=visible.get(e.to);const target=e.targetPosition;
   if(e.automatic&&e.from>e.to)continue;
   if(!b&&(!target||target[2]!==view.z))continue;
   const bx=b?.x??target![0],by=b?.y??target![1];
   const ax=sx(a.x)+s/2,ay=sy(a.y)+s/2,tx=sx(bx)+s/2,ty=sy(by)+s/2;
   ctx.strokeStyle=e.automatic?alpha(edgeColor('automatic',P),.4):e.hidden?edgeColor('hidden',P):e.key_item_id||e.conditions?.length?edgeColor('locked',P):edgeColor('explicit',P);ctx.lineWidth=e.automatic?1:2;ctx.setLineDash(e.hidden?[3,4]:e.automatic?[2,4]:[]);
   ctx.beginPath();ctx.moveTo(ax,ay);ctx.lineTo(tx,ty);ctx.stroke();ctx.setLineDash([]);
   if(!e.automatic){const angle=Math.atan2(ty-ay,tx-ax),mx=ax+(tx-ax)*.64,my=ay+(ty-ay)*.64;ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(mx,my);ctx.lineTo(mx-7*Math.cos(angle-.5),my-7*Math.sin(angle-.5));ctx.lineTo(mx-7*Math.cos(angle+.5),my-7*Math.sin(angle+.5));ctx.fill();}
  }
  for(const c of data.cells){
   const x=sx(c.x),y=sy(c.y);const selected=c.id===activeCell?.id;const start=c.id===project.files['manifest.json']?.start_room;
   if(!c.preset){
    const pad=Math.max(4,s*.12);ctx.fillStyle=selected?P.selected:mix(P.black,P.green,.1);ctx.strokeStyle=selected?P.greenBright:start?P.amber:P.greenDim;ctx.lineWidth=selected?2:1;ctx.beginPath();ctx.rect(x+pad,y+pad,s-pad*2,s-pad*2);ctx.fill();ctx.stroke();
    ctx.fillStyle=start?P.amber:P.green;ctx.font=`${Math.max(12,s*.23)}px ${mono}`;ctx.textAlign='center';ctx.fillText(start?'⌂':'◇',x+s/2,y+s*.49);
    if(s>=42){ctx.fillStyle=P.greenBright;ctx.font=`${s>70?10:9}px ${mono}`;ctx.fillText(c.name.slice(0,Math.floor(s/6.2)),x+s/2,y+s*.73);}
   }
   if(selected||c.id===linkStart){ctx.strokeStyle=c.id===linkStart?P.amber:P.greenBright;ctx.lineWidth=2;ctx.strokeRect(x+2,y+2,s-4,s-4);}
   if(c.markers?.length&&s>28){ctx.fillStyle=P.cyan;ctx.beginPath();ctx.arc(x+s-9,y+s-9,3,0,Math.PI*2);ctx.fill();}
   const vertical=verticalEdges.get(c.id)||[];
   if(vertical.length&&s>28){ctx.fillStyle=P.magenta;ctx.font=`12px ${mono}`;ctx.textAlign='right';ctx.fillText(vertical.some(e=>e.direction==='up')?'↥':'↧',x+s-5,y+15);}
  }
  if(hover){ctx.fillStyle=alpha(P.green,.08);ctx.fillRect(sx(hover.x),sy(hover.y),s,s);ctx.strokeStyle=alpha(P.green,.45);ctx.lineWidth=1;ctx.strokeRect(sx(hover.x)+1,sy(hover.y)+1,s-2,s-2);}
  if(anchor&&hover&&['rectangle','ellipse','erase'].includes(tool)){ctx.strokeStyle=tool==='erase'?P.red:P.greenBright;ctx.fillStyle=alpha(tool==='erase'?P.red:P.green,.14);const x=sx(Math.min(anchor.x,hover.x)),y=sy(Math.min(anchor.y,hover.y)),w=(Math.abs(hover.x-anchor.x)+1)*s,h=(Math.abs(hover.y-anchor.y)+1)*s;ctx.beginPath();if(tool==='ellipse')ctx.ellipse(x+w/2,y+h/2,w/2,h/2,0,0,Math.PI*2);else ctx.rect(x,y,w,h);ctx.fill();ctx.stroke();}
  ctx.fillStyle=alpha(P.black,.88);ctx.fillRect(0,0,width,19);ctx.fillRect(0,0,27,height);ctx.fillStyle=P.grey;ctx.font=`9px ${mono}`;ctx.textAlign='center';
  const step=s<34?2:1;
  for(let x=Math.ceil(view.x);x<=view.x+width/s;x++)if(x%step===0)ctx.fillText(String(x),sx(x)+s/2,13);
  for(let y=Math.ceil(view.y);y<=view.y+height/s;y++)if(y%step===0)ctx.fillText(String(y),13,sy(y)+s/2+3);
 },[data,view,dimensions,activeCell?.id,hover,anchor,tool,linkStart,project.revision,palette]);
 const layers=project.files['manifest.json']?.layers||[];const levels=Array.from(new Set([0,view.z,...layers.map((l:Row)=>l.z),...roomRows.filter(r=>Number.isInteger(r.z)).map(r=>r.z),...rows(project,registry,'terrain_layers').map(l=>l.z)])).sort((a:any,b:any)=>b-a) as number[];
 return <div className="map-workspace">
  <div className="map-topline"><div className="inline"><Layers size={15}/><select aria-label="Map layer" value={view.z} onChange={e=>setView((v:Row)=>({...v,z:Number(e.target.value)}))}>{levels.map(z=><option key={z} value={z}>{layers.find((l:Row)=>l.z===z)?.name||'Layer '+z} · z {z}</option>)}</select><button className="icon subtle" title="Add or switch layer" onClick={()=>{const z=window.prompt('Layer number',String(view.z+1));if(z!==null&&Number.isInteger(Number(z)))setView((v:Row)=>({...v,z:Number(z)}));}}><Plus size={14}/></button></div><span className="hint">N ↑ <span className="separator">/</span> east →</span><button className={'icon subtle '+(!tray?'active':'')} title="Toggle rooms without grid coordinates" onClick={()=>setTray(!tray)}><PanelLeftClose size={15}/></button></div>
  <div className="map-toolstrip">{tools.map(([key,name,Icon])=><button key={key} className={'tool '+(tool===key?'active':'')} title={name} aria-label={name} onClick={()=>{setTool(key);if(key!=='connect')setLinkStart(null);}}><Icon size={17}/></button>)}<span className="tool-label">{tools.find(t=>t[0]===tool)?.[1]}</span></div>
  {['brush','rectangle','ellipse','erase'].includes(tool)&&<div className="terrain-bar"><Mountain size={15}/><select aria-label="Terrain preset" value={preset} onChange={e=>setPreset(e.target.value)}><option value="">Choose preset…</option>{presets.map(p=><option key={p.key} value={p.key}>{p.name}</option>)}</select><select aria-label="Terrain region" value={region} onChange={e=>setRegion(e.target.value)}><option value="">No region</option>{regions.map(r=><option key={r.key} value={r.key}>{r.name}</option>)}</select><label className="check"><input type="checkbox" checked={emptyOnly} onChange={e=>setEmptyOnly(e.target.checked)}/>Only empty</label></div>}
  <div className="map-holder" ref={holder}>
   <canvas ref={canvas} aria-label="World grid map" role="application" style={{width:dimensions.width,height:dimensions.height,cursor:tool==='pan'?'grab':tool==='select'?'default':'crosshair'}} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={e=>{releasePointer(e.pointerId);drag.current=null;setAnchor(null);setHover(null);}} onPointerLeave={()=>setHover(null)} onContextMenu={e=>e.preventDefault()}/>
   {loading&&<div className="map-loading">Updating map…</div>}
   <div className="map-instructions">{tool==='connect'?(linkStart?'Choose the destination room. Switch layers for stairs.':'Choose the room the passage leaves.'):tool==='place'?'Select a room, then click an empty square.':tool==='room'?'Click an empty square to create a room.':tool==='erase'?'Drag to erase wilderness. Referenced cells are protected.':['brush','rectangle','ellipse'].includes(tool)?'Drag to paint. Authored rooms are protected.':mobile?'Tap to inspect · drag to pan · pinch to zoom':'Click to inspect · Alt-drag to pan · Ctrl-scroll to zoom'}</div>
   <div className="map-zoom"><button className="icon" onClick={()=>zoom(-8)} title="Zoom out"><Minus size={15}/></button><span>{Math.round(view.size/64*100)}%</span><button className="icon" onClick={()=>zoom(8)} title="Zoom in"><Plus size={15}/></button><button className="icon" title="Center on start room" onClick={()=>{const r=roomRows.find(r=>r.id===project.files['manifest.json']?.start_room);if(r&&Number.isInteger(r.x))setView((v:Row)=>({...v,z:r.z,x:r.x-Math.floor(dimensions.width/v.size/2),y:r.y-Math.floor(dimensions.height/v.size/2)}));}}><LocateFixed size={15}/></button></div>
  </div>
  <div className="map-legend"><span><i className="legend-room"/>Authored room</span><span><i className="legend-link"/>Explicit passage</span><span><i className="legend-auto"/>Automatic link</span><span><i className="legend-marker"/>Content</span><span className="coordinate">{hover?`${hover.x}, ${hover.y}, ${view.z}`:`Layer ${view.z}`}</span></div>
  {tray&&roomRows.some(r=>![r.x,r.y,r.z].every(Number.isInteger))&&<div className="room-tray"><div className="tray-heading"><strong>Rooms without grid coordinates <span className="count">{unplaced.length}</span></strong><input placeholder="Find a room…" aria-label="Search rooms without grid coordinates" value={trayQuery} onChange={e=>{setTrayQuery(e.target.value);setTrayPage(0);}}/></div><p className="tray-explanation">These rooms can connect through passages without grid placement. Explore them in Connections. Select a room here to optionally place it on the game grid.</p><div className="tray-items">{unplaced.length?unplaced.slice(visibleTrayPage*100,(visibleTrayPage+1)*100).map(({r,index})=><button key={r.id||index} aria-label={`Select ${r.name} (${r.id}) for optional grid placement`} className={'tray-room '+(selection?.collection==='rooms'&&selection.index===index?'active':'')} onClick={()=>{onSelect({collection:'rooms',index},false);onCell(null);setTool('place');}}><span>{r.name}</span><small>{r.id}</small><ArrowUpRight size={12}/></button>):<p className="hint">No matching rooms without grid coordinates.</p>}</div>{unplaced.length>100&&<div className="tray-pagination"><button aria-label="Previous rooms without coordinates" disabled={!visibleTrayPage} onClick={()=>setTrayPage(visibleTrayPage-1)}>Previous</button><span>{visibleTrayPage*100+1} - {Math.min((visibleTrayPage+1)*100,unplaced.length)} of {unplaced.length}</span><button aria-label="Next rooms without coordinates" disabled={(visibleTrayPage+1)*100>=unplaced.length} onClick={()=>setTrayPage(visibleTrayPage+1)}>Next</button></div>}</div>}
 </div>;
}
