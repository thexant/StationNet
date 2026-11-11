import {useEffect,useId,useMemo,useRef,useState} from 'react';
import {api,Edge,Project,Selection} from './data';
import {ConnectionGraph,ConnectionNode,DiagramNode,layoutConnections,NODE_HEIGHT,NODE_WIDTH,passageKind,passageLabel,passageStyle} from './connections';
import {EdgeKind,edgeColor,localPoint,usePalette} from './theme';

type Props={project:Project;selectedId?:string;focus?:{id:string;nonce:number};onSelect:(node:ConnectionNode)=>void;onNavigate:(s:Selection)=>void;onGrid:(node:ConnectionNode)=>void;onError:(message:string)=>void};
type View={x:number;y:number;scale:number};
const empty:ConnectionGraph={revision:-1,nodes:[],edges:[],associations:[]};

export function ConnectionMap({project,selectedId,focus,onSelect,onNavigate,onGrid,onError}:Props){
 const key='forge-connections:'+project.id;const palette=usePalette();
 const restored=useMemo(()=>{try{const v=JSON.parse(localStorage.getItem(key)||'null');return v&&[v.x,v.y,v.scale].every(Number.isFinite)&&v.scale>=.02&&v.scale<=2.5?v as View:null;}catch{return null;}},[key]);
 const [view,setView]=useState<View>(restored||{x:40,y:40,scale:.85});const [loadedGraph,setGraph]=useState(empty);const graph=loadedGraph.revision===project.revision?loadedGraph:empty;
 const [loading,setLoading]=useState(true);const [failure,setFailure]=useState('');const [query,setQuery]=useState('');const [page,setPage]=useState(0);
 const [dimensions,setDimensions]=useState({width:800,height:450});const holder=useRef<HTMLDivElement>(null);const svg=useRef<SVGSVGElement>(null);
 const drag=useRef<{x:number;y:number;view:View;scale:number}|null>(null);
 // Two-finger pinch: zoom about the fingers' midpoint and pan with it.
 const pointers=useRef(new Map<number,{x:number;y:number}>());const pinch=useRef<{distance:number;mid:{x:number;y:number};view:View}|null>(null);
 const pinchState=(element:Element,current:View)=>{const [a,b]=Array.from(pointers.current.values());const mid=localPoint(element,(a.x+b.x)/2,(a.y+b.y)/2);return {distance:Math.max(1,Math.hypot(a.x-b.x,a.y-b.y)),mid:{x:mid.x,y:mid.y},view:current};};
 const release=(id:number)=>{pointers.current.delete(id);if(pointers.current.size<2)pinch.current=null;drag.current=null;};const initialized=useRef(false);const marker=useId().replace(/:/g,'');
 const callbacks=useRef({onError});callbacks.current={onError};
 useEffect(()=>{
  let live=true;setLoading(true);setFailure('');
  api<ConnectionGraph>(`/d/${project.id}/connections`).then(data=>{
   if(live&&data.revision===project.revision)setGraph(data);
   else if(live)setFailure('The draft changed while loading. Switch views to refresh.');
  }).catch(e=>{if(live){setFailure(e.message);callbacks.current.onError(e.message);}}).finally(()=>{if(live)setLoading(false);});
  return()=>{live=false;};
 },[project.id,project.revision]);
 useEffect(()=>{localStorage.setItem(key,JSON.stringify(view));},[key,view]);
 useEffect(()=>{
  if(!holder.current)return;const observer=new ResizeObserver(entries=>{const r=entries[0].contentRect;setDimensions({width:Math.max(100,r.width),height:Math.max(100,r.height)});});
  observer.observe(holder.current);return()=>observer.disconnect();
 },[]);
 const nodes=useMemo(()=>layoutConnections(graph,project.files['manifest.json']?.start_room),[graph,project.files['manifest.json']?.start_room]);
 const byId=useMemo(()=>new Map(nodes.map(n=>[n.id,n])),[nodes]);
 const selected=byId.get(selectedId||'');
 const center=(node:DiagramNode,scale=view.scale)=>setView({x:dimensions.width/2-(node.dx+NODE_WIDTH/2)*scale,y:dimensions.height/2-(node.dy+NODE_HEIGHT/2)*scale,scale});
 useEffect(()=>{
  if(!nodes.length||initialized.current)return;initialized.current=true;
  if(!restored){const n=byId.get(selectedId||'')||byId.get(project.files['manifest.json']?.start_room)||nodes[0];center(n,.85);}
 },[nodes,dimensions]);
 useEffect(()=>{const n=focus&&byId.get(focus.id);if(n)center(n,.85);},[focus?.nonce,nodes]);
 const zoom=(factor:number)=>setView(v=>{const scale=Math.max(.02,Math.min(2.5,v.scale*factor)),ratio=scale/v.scale;return {scale,x:dimensions.width/2+(v.x-dimensions.width/2)*ratio,y:dimensions.height/2+(v.y-dimensions.height/2)*ratio};});
 useEffect(()=>{
  const element=svg.current;if(!element)return;
  const wheel=(e:WheelEvent)=>{if(!e.ctrlKey&&!e.metaKey)return;e.preventDefault();zoom(e.deltaY>0?.85:1.18);};
  element.addEventListener('wheel',wheel,{passive:false});return()=>element.removeEventListener('wheel',wheel);
 },[dimensions]);
 const fit=()=>{
  if(!nodes.length)return;const w=Math.max(...nodes.map(n=>n.dx+NODE_WIDTH))+32,h=Math.max(...nodes.map(n=>n.dy+NODE_HEIGHT))+32;
  const scale=Math.max(.02,Math.min(1,dimensions.width/w,dimensions.height/h));setView({scale,x:(dimensions.width-w*scale)/2,y:(dimensions.height-h*scale)/2});
 };
 const choose=(node:DiagramNode)=>{onSelect(node);center(node,Math.max(.65,view.scale));};
 const matches=nodes.filter(n=>[n.id,n.name].some(s=>s.toLowerCase().includes(query.toLowerCase())));
 const currentPage=Math.min(page,Math.max(0,Math.ceil(matches.length/40)-1));
 const outgoing=graph.edges.filter(e=>e.from===selectedId);
 const visible=(n:DiagramNode)=>{const x=n.dx*view.scale+view.x,y=n.dy*view.scale+view.y;return x+NODE_WIDTH*view.scale>=0&&y+NODE_HEIGHT*view.scale>=0&&x<=dimensions.width&&y<=dimensions.height;};
 const edgePath=(e:Edge)=>{
  const a=byId.get(e.from),b=byId.get(e.to);if(!a||!b)return null;
  const ax=a.dx+NODE_WIDTH/2,ay=a.dy+NODE_HEIGHT/2,bx=b.dx+NODE_WIDTH/2,by=b.dy+NODE_HEIGHT/2;
  if(a===b)return {d:`M ${ax-30} ${a.dy} C ${ax-95} ${a.dy-85} ${ax+95} ${a.dy-85} ${ax+30} ${a.dy}`,x:ax,y:a.dy-62};
  const length=Math.hypot(bx-ax,by-ay);const bend=e.automatic?12:30;
  const cx=(ax+bx)/2-(by-ay)/length*bend,cy=(ay+by)/2+(bx-ax)/length*bend;
  // Leave arrowheads outside node cards rather than underneath the destination.
  const dx=bx-cx,dy=by-cy;const t=Math.min(NODE_WIDTH/2/Math.max(.001,Math.abs(dx)),NODE_HEIGHT/2/Math.max(.001,Math.abs(dy)));
  return {d:`M ${ax} ${ay} Q ${cx} ${cy} ${bx-dx*t*1.06} ${by-dy*t*1.06}`,x:(ax+2*cx+bx)/4,y:(ay+2*cy+by)/4};
 };
 return <div className="connections-workspace">
  <p className="connections-note">Schematic of authored rooms and passages; diagram positions do not change game coordinates. Wilderness is shown one step beyond authored rooms and referenced squares. This is not a player reachability check.</p>
  <div className="connections-toolbar"><button onClick={fit}>Fit all</button><button disabled={!selected} onClick={()=>selected&&center(selected,.85)}>Center selected</button><button onClick={()=>{const n=byId.get(project.files['manifest.json']?.start_room);if(n)center(n,.85);}}>Center start</button><button aria-label="Zoom connections out" onClick={()=>zoom(.8)}>−</button><span>{Math.round(view.scale*100)}%</span><button aria-label="Zoom connections in" onClick={()=>zoom(1.25)}>+</button><span className="hint">Drag background to pan · Ctrl-scroll to zoom</span></div>
  <div className="connections-body">
   <div className="connections-browser"><label>Find a room<input aria-label="Search connection rooms" placeholder="Name or ID" value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}}/></label><p className="hint">{graph.nodes.filter(n=>n.kind==='authored').length} authored rooms · {matches.length} matching nodes</p>
    <div className="connections-results">{matches.slice(currentPage*40,(currentPage+1)*40).map(n=><button key={n.id} aria-label={`Select room ${n.name} (${n.id})`} className={n.id===selectedId?'active':''} onClick={()=>choose(n)}><strong>{n.name}</strong><small>{n.id}</small><small>{n.kind==='missing'?'Missing endpoint':n.gamePosition?n.gamePosition.join(', '):'No grid coordinates'}</small></button>)}{!loading&&!matches.length&&<p className="hint">No matching rooms.</p>}</div>
    {matches.length>40&&<div className="connections-pages"><button disabled={!currentPage} onClick={()=>setPage(currentPage-1)} aria-label="Previous connection rooms">Previous</button><span>{currentPage+1}/{Math.ceil(matches.length/40)}</span><button disabled={(currentPage+1)*40>=matches.length} onClick={()=>setPage(currentPage+1)} aria-label="Next connection rooms">Next</button></div>}
   </div>
   <div className="connections-diagram" ref={holder}>
    <svg ref={svg} width="100%" height="100%" aria-label="Room connections diagram" role="group" onPointerDown={e=>{if(e.button!==0&&e.button!==1)return;e.currentTarget.setPointerCapture(e.pointerId);pointers.current.set(e.pointerId,{x:e.clientX,y:e.clientY});if(pointers.current.size===2){drag.current=null;pinch.current=pinchState(e.currentTarget,view);return;}if(pointers.current.size>2)return;drag.current={x:e.clientX,y:e.clientY,view,scale:localPoint(e.currentTarget,0,0).scale};}} onPointerMove={e=>{if(pointers.current.has(e.pointerId))pointers.current.set(e.pointerId,{x:e.clientX,y:e.clientY});const p=pinch.current;if(p&&pointers.current.size>=2){const now=pinchState(e.currentTarget,view);const scale=Math.max(.02,Math.min(2.5,p.view.scale*now.distance/p.distance));const wx=(p.mid.x-p.view.x)/p.view.scale,wy=(p.mid.y-p.view.y)/p.view.scale;setView({scale,x:now.mid.x-wx*scale,y:now.mid.y-wy*scale});return;}const d=drag.current;if(d)setView({...d.view,x:d.view.x+(e.clientX-d.x)*d.scale,y:d.view.y+(e.clientY-d.y)*d.scale});}} onPointerUp={e=>release(e.pointerId)} onPointerCancel={e=>release(e.pointerId)}>
     <defs>{['explicit','automatic','hidden','locked','conditional','vehicle'].map((name,i)=><marker key={name} id={marker+name} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L8,4 L0,8 Z" fill={edgeColor(name as EdgeKind,palette)}/></marker>)}</defs>
     <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
      {graph.edges.map((e,i)=>{const p=edgePath(e);if(!p)return null;const style=passageStyle(e,palette);const kind=passageKind(e);const label=passageLabel(e);return <g key={i} className="connection-edge"><title>{e.from+' → '+e.to+' · '+label}</title><path d={p.d} fill="none" stroke={style.color} strokeWidth={e.automatic?1:1.8} strokeDasharray={style.dash} markerEnd={`url(#${marker+kind})`}/>{(!e.automatic&&view.scale>=.65||e.from===selectedId)&&<text x={p.x} y={p.y-5} textAnchor="middle" fill={style.color}>{label.slice(0,75)}</text>}</g>;})}
      {nodes.filter(visible).map(n=><g key={n.id} transform={`translate(${n.dx} ${n.dy})`} className={'connection-node '+n.kind+(n.id===selectedId?' selected':'')} role="button" tabIndex={0} aria-label={'Inspect '+n.name+' '+n.id} onPointerDown={e=>e.stopPropagation()} onClick={()=>choose(n)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose(n);}}}><title>{n.name+' · '+n.id}</title><rect width={NODE_WIDTH} height={NODE_HEIGHT} rx="7"/><text x="12" y="23" className="connection-name">{n.id===project.files['manifest.json']?.start_room?'⌂ ':''}{n.name.slice(0,30)}</text><text x="12" y="43">{n.id.slice(0,34)}</text><text x="12" y="61">{n.kind==='missing'?'Missing endpoint':n.boundary?'Wilderness continues →':n.gamePosition?'Grid '+n.gamePosition.join(', '):'No grid coordinates'}</text></g>)}
     </g>
    </svg>
    {loading&&<span className="map-loading" role="status">Loading connections…</span>}{failure&&<p className="connections-failure" role="alert">{failure}</p>}
   </div>
  </div>
  <div className="connections-legend"><span>→ Explicit passage</span><span>┄ Automatic</span><span className="hidden-link">Hidden</span><span className="locked-link">Locked</span><span className="conditional-link">Conditional</span><span className="vehicle-link">Vehicle required</span></div>
  {selected&&<div className="connections-detail"><div className="inline"><strong>{selected.name}</strong><code>{selected.id}</code>{selected.gamePosition&&<button onClick={()=>onGrid(selected)}>{selected.boundary?'Continue on grid':'Show on grid'}</button>}</div>
   {selected.boundary&&<p className="hint">Automatic wilderness paths continue beyond this diagram. Continue on grid to explore them.</p>}
   {graph.associations.filter(a=>a.room===selected.id).map((a,i)=><div className="transport-association" key={i}><span>{a.kind} · entered through transport</span><button onClick={()=>onNavigate(a)}>{a.name}</button><small>Transport ownership, not a movement passage.</small></div>)}
   <div className="connection-passages">{outgoing.map((e,i)=><div key={i}><button onClick={()=>{const n=byId.get(e.to);if(n)choose(n);}}>{passageLabel(e)} → {byId.get(e.to)?.name||e.to}</button>{Number.isInteger(e.exitIndex)&&<button aria-label={'Edit passage '+(e.name||e.direction||i)} onClick={()=>onNavigate({collection:'exits',index:e.exitIndex})}>Edit passage</button>}</div>)}{!outgoing.length&&<p className="hint">No ordinary outgoing passages in this diagram.</p>}</div>
  </div>}
 </div>;
}
