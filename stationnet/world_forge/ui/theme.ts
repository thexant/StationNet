// World Forge draws its map and diagrams with the station's own theme colours.
// station.css defines the tokens and /masthead.css overrides them per station and user,
// so they are read from the computed style at draw time instead of being hard-coded.
import {useEffect,useState} from 'react';

export type Palette={black:string;green:string;greenBright:string;greenDim:string;cyan:string;amber:string;red:string;redDim:string;
 magenta:string;grey:string;white:string;border:string;borderMuted:string;surface:string;selected:string;mono:string};

const TOKENS:Record<keyof Palette,[string,string]>={
 black:['--black','#000000'],green:['--green','#00ed45'],greenBright:['--green-bright','#71ff8a'],greenDim:['--green-dim','#008d31'],
 cyan:['--cyan','#00d8cf'],amber:['--amber','#e6a431'],red:['--red','#ff3237'],redDim:['--red-dim','#7b1118'],magenta:['--magenta','#d829be'],
 grey:['--grey','#8b8b86'],white:['--white','#d8d8cf'],border:['--border-soft','#064d1d'],borderMuted:['--border-muted','#454540'],
 surface:['--surface-small','#020702'],selected:['--surface-selected','#10351c'],mono:['--mono','monospace']};

export function readPalette(element:Element=document.documentElement):Palette{
 const style=getComputedStyle(element);
 return Object.fromEntries(Object.entries(TOKENS).map(([key,[name,fallback]])=>[key,style.getPropertyValue(name).trim()||fallback])) as Palette;
}

export function rgb(color:string):[number,number,number]{
 const value=color.trim();
 const hex=/^#([0-9a-f]{3,8})$/i.exec(value);
 if(hex){let h=hex[1];if(h.length<6)h=h.split('').slice(0,3).map(c=>c+c).join('');return [0,2,4].map(i=>parseInt(h.slice(i,i+2),16)) as [number,number,number];}
 const parts=/rgba?\(([^)]+)\)/i.exec(value);
 if(parts){const n=parts[1].split(/[ ,/]+/).filter(Boolean).map(Number);return [n[0]||0,n[1]||0,n[2]||0];}
 return [0,0,0];
}
export function alpha(color:string,a:number){const [r,g,b]=rgb(color);return `rgba(${r},${g},${b},${a})`;}
export function mix(a:string,b:string,t:number){const x=rgb(a),y=rgb(b);return `rgb(${x.map((v,i)=>Math.round(v+(y[i]-v)*t)).join(',')})`;}

// Terrain keeps a recognisable hue (water is the accent, desert the warning colour…)
// while staying dark enough for the station's glyphs and labels to read on top.
export function terrainColor(terrain:string|undefined,p:Palette){
 const deep=(c:string,t:number)=>mix(p.black,c,t);
 switch(terrain){
  case 'water':return deep(p.cyan,.26);
  case 'desert':return deep(p.amber,.28);
  case 'mountains':return deep(p.grey,.34);
  case 'hills':return deep(mix(p.amber,p.green,.5),.22);
  case 'road':return deep(p.white,.22);
  case 'tundra':return deep(mix(p.grey,p.white,.5),.28);
  case 'swamp':return deep(mix(p.green,p.cyan,.3),.16);
  case 'grassland':return deep(p.green,.2);
  case 'forest':return deep(p.green,.14);
  case 'dense-forest':return deep(p.green,.09);
  default:return deep(p.magenta,.22);
 }
}

export type EdgeKind='explicit'|'automatic'|'hidden'|'locked'|'conditional'|'vehicle';
export function edgeColor(kind:EdgeKind,p:Palette){
 return {explicit:p.amber,automatic:p.green,hidden:p.magenta,locked:p.red,conditional:mix(p.amber,p.white,.4),vehicle:p.cyan}[kind];
}

// Re-read the palette when the station swaps theme stylesheets or the page regains focus.
export function usePalette(){
 const [palette,setPalette]=useState<Palette>(()=>readPalette());
 useEffect(()=>{
  const refresh=()=>setPalette(previous=>{const next=readPalette();return JSON.stringify(next)===JSON.stringify(previous)?previous:next;});
  const observer=new MutationObserver(refresh);
  observer.observe(document.head,{childList:true,subtree:true,attributes:true});
  observer.observe(document.documentElement,{attributes:true});
  window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',refresh);
  const links=Array.from(document.querySelectorAll('link[rel=stylesheet]'));links.forEach(l=>l.addEventListener('load',refresh));
  return()=>{observer.disconnect();window.removeEventListener('focus',refresh);document.removeEventListener('visibilitychange',refresh);links.forEach(l=>l.removeEventListener('load',refresh));};
 },[]);
 return palette;
}

// The page is zoomed (html{zoom:1.25} on desktop), so a pointer's client offset must be
// scaled from the element's visual box back into its own CSS pixels.
export function localPoint(element:Element,clientX:number,clientY:number){
 const bounds=element.getBoundingClientRect();const width=(element as HTMLElement).clientWidth||bounds.width;const height=(element as HTMLElement).clientHeight||bounds.height;
 const sx=bounds.width?width/bounds.width:1,sy=bounds.height?height/bounds.height:1;
 return {x:(clientX-bounds.left)*sx,y:(clientY-bounds.top)*sy,scale:sx};
}
