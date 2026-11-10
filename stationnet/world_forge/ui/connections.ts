import {Edge,Selection} from './data';
import {EdgeKind,Palette,edgeColor,readPalette} from './theme';

export type ConnectionNode={id:string;name:string;roomIndex:number|null;gamePosition:[number,number,number]|null;kind:'authored'|'wilderness'|'missing';boundary:boolean;preset?:string;region?:string;terrain?:string;glyph?:string};
export type Association=Selection & {room:string;kind:string;name:string};
export type ConnectionGraph={revision:number;nodes:ConnectionNode[];edges:Edge[];associations:Association[]};
export type DiagramNode=ConnectionNode & {dx:number;dy:number};
export const NODE_WIDTH=220,NODE_HEIGHT=72;

// Weak components group connected rooms without implying that a passage is reciprocal.
// Source order makes layout reproducible; these are pixels, never game coordinates.
export function layoutConnections(graph:ConnectionGraph,start:string):DiagramNode[]{
 const neighbors=new Map(graph.nodes.map(n=>[n.id,new Set<string>()]));
 for(const e of graph.edges){neighbors.get(e.from)?.add(e.to);neighbors.get(e.to)?.add(e.from);}
 const source=new Map(graph.nodes.map(n=>[n.id,n]));const placed=new Map<string,DiagramNode>();let top=32;
 for(const root of [start,...graph.nodes.map(n=>n.id)]){
  if(placed.has(root)||!source.has(root))continue;
  const queue=[{id:root,depth:0}];const seen=new Set([root]);const counts=new Map<number,number>();let bottom=top;
  for(let i=0;i<queue.length;i++){
   const {id,depth}=queue[i];const row=counts.get(depth)||0;counts.set(depth,row+1);
   const node={...source.get(id)!,dx:32+depth*300,dy:top+row*120};placed.set(id,node);bottom=Math.max(bottom,node.dy+NODE_HEIGHT);
   for(const next of neighbors.get(id)||[])if(source.has(next)&&!seen.has(next)){seen.add(next);queue.push({id:next,depth:depth+1});}
  }
  top=bottom+100;
 }
 return graph.nodes.map(n=>placed.get(n.id)!);
}

export function passageLabel(e:Edge){
 return [e.name||e.direction||'Passage',e.direction&&e.name!==e.direction?e.direction:null,
  e.automatic?'automatic':null,e.hidden?'hidden':null,e.key_item_id||e.pick_difficulty?'locked':null,
  e.conditions?.length?'conditional':null,e.requires_mode?'needs '+e.requires_mode:null,e.allow_vehicles===false?'on foot':null].filter(Boolean).join(' · ');
}

export function passageKind(e:Edge):EdgeKind{
 return e.hidden?'hidden':e.key_item_id||e.pick_difficulty?'locked':e.conditions?.length?'conditional':e.requires_mode?'vehicle':e.automatic?'automatic':'explicit';
}

export function passageStyle(e:Edge,palette:Palette=readPalette()){
 return {color:edgeColor(passageKind(e),palette),
  dash:e.hidden?'3 5':e.automatic?'5 5':e.requires_mode?'10 4':''};
}
