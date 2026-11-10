export type Row = Record<string, any>;
export type FieldSchema = {type?:string; properties?:Record<string,FieldSchema>;items?:FieldSchema;enum?:any[];ref?:string;variant?:string;widget?:string;flexible?:boolean;min?:number;max?:number};
export type Collection = {key:string;title:string;group:string;file:string;path:string[]|null;identity:string[];fields:Record<string,FieldSchema>;defaults:Row};
export type Registry = {collections:Record<string,Collection>;shapes:Record<string,FieldSchema>;references:Record<string,string>;conditions:Record<string,FieldSchema>;actions:Record<string,FieldSchema>;events:Record<string,FieldSchema>;handbook:string;coverage:{key:string;title:string;fields:number}[];terrain:string[];directions:string[]};
export type Project = {id:string;kind:'existing'|'new';worldId:string;path:string;name:string;files:Record<string,any>;revision:number;dirty:boolean;canUndo:boolean;canRedo:boolean;selected?:Selection;publishing?:boolean;editors?:string[];stale?:boolean;seedWarnings?:Row[];seededAt?:number;publishedAt?:number};
export type Selection = {collection:string;index:number};
export type Issue = {severity:string;category:string;file:string;path:string;message:string;collection:string;index:number;field:string|null};
export type Use = Selection & {path:(string|number)[];target:string;value:string};
export type MapCell = Row & {id:string;x:number;y:number;z:number;name:string;markers?:Selection[]};
export type Edge = Row & {from:string;to:string;automatic:boolean;targetPosition?:number[]};
export const title=(s:string)=>s.replace(/_json$/,'').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
export function rows(project:Project|null,registry:Registry|null,key:string):Row[]{
 if(!project||!registry||!registry.collections[key])return [];
 const c=registry.collections[key];let v=project.files[c.file];
 if(c.path===null)return v?[v]:[];
 if(Array.isArray(v))return v;
 for(const k of c.path||[])v=v?.[k];
 return Array.isArray(v)?v:[];
}
export function label(r:Row){return String(r.name||r.response_text||r.body||r.description||r.id||r.key||r.npc_definition_id||r.room_id||r.room||'Untitled').slice(0,100);}
export function identifier(r:Row,c:Collection){return c.identity.map(k=>r[k]??'—').join(' · ');}
export function infer(v:any):FieldSchema{
 if(Array.isArray(v))return {type:'array',items:v.length?infer(v[0]):{type:'string'}};
 if(v!==null&&typeof v==='object')return {type:'object',properties:Object.fromEntries(Object.entries(v).map(([k,x])=>[k,infer(x)]))};
 return {type:typeof v==='number'?'number':typeof v==='boolean'?'boolean':'string'};
}
export function initial(s:FieldSchema):any{
 if(s.enum?.length)return s.enum[0];
 if(s.type==='object')return {};
 if(s.type==='array')return [];
 if(s.type==='number')return s.min??0;
 if(s.type==='boolean')return false;
 return '';
}
export function slug(s:string){return s.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,64);}
export function changes(before:any,after:any,path:(string|number)[]):Row[]{
 if(JSON.stringify(before)===JSON.stringify(after))return [];
 if(before!==null&&after!==null&&typeof before==='object'&&typeof after==='object'&&Array.isArray(before)===Array.isArray(after)&&(!Array.isArray(before)||before.length===after.length)){
  return Array.from(new Set([...Object.keys(before),...Object.keys(after)])).flatMap(key=>{
   const p=[...path,Array.isArray(before)?Number(key):key];
   return !(key in after)?[{path:p,remove:true}]:changes(before[key],after[key],p);
  });
 }
 return [{path,value:after}];
}
// The page tells the client where its API lives and which CSRF token to send (templates/world_forge/index.html).
const root=typeof document!=='undefined'?document.getElementById('world-forge'):null;
let base=root?.dataset.api||'/mud/forge/api';let csrf=root?.dataset.csrf||'';
export function configure(options:{base?:string;csrf?:string}){if(options.base!==undefined)base=options.base;if(options.csrf!==undefined)csrf=options.csrf;}
export function apiUrl(url:string){return base+url;}
export async function api<T=any>(url:string,body?:any,method?:string):Promise<T>{
 const response=await fetch(base+url,{method:method||(body===undefined?'GET':'POST'),credentials:'same-origin',
  headers:{...(body instanceof Blob?{'Content-Type':'application/zip'}:{'Content-Type':'application/json'}),'X-CSRF-Token':csrf},
  body:body===undefined?undefined:body instanceof Blob?body:JSON.stringify(body)});
 if(!response.ok){const error=await response.json().catch(()=>({detail:response.status===403?'World Forge needs builder access.':response.statusText||'The station did not answer.'}));const failure=new Error(typeof error.detail==='string'?error.detail:JSON.stringify(error.detail));(failure as any).problems=error.problems;throw failure;}
 return response.json();
}
export async function fetchFile(url:string){
 const response=await fetch(base+url,{credentials:'same-origin'});
 if(!response.ok){const error=await response.json().catch(()=>({detail:response.statusText}));throw new Error(error.detail||'Download failed.');}
 return response.blob();
}
export function download(blob:Blob,name:string){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}

// Definitions the station already provides (installed races, classes, skills, effects, abilities,
// shared items and NPCs). Reference fields offer them alongside the draft's own records.
const CATALOG_TARGETS:Record<string,string>={races:'races',classes:'classes',skills:'skills',effects:'effects',abilities:'abilities',mud_item_definitions:'items',mud_npc_definitions:'npcs'};
let stationCatalog:Record<string,{key:string;name:string}[]>={};
export function setStationCatalog(catalog:Record<string,any>|null){stationCatalog=catalog||{};}
export function stationReferences(target:string|undefined){const list=target?stationCatalog[CATALOG_TARGETS[target]]:undefined;return list||[];}
