// @vitest-environment jsdom
import React from 'react';
import {afterEach,beforeEach,describe,it,expect,vi} from 'vitest';
import {render,screen,cleanup,fireEvent,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';
import {Project,Registry,Row} from './data';

afterEach(()=>{cleanup();vi.restoreAllMocks();localStorage.clear();});
const fields={id:{type:'string'},name:{type:'string'},description:{type:'string'},grid_link:{type:'boolean'},x:{type:'number'},y:{type:'number'},z:{type:'number'}};
const collections:Row={
 manifest:{key:'manifest',title:'World settings',group:'World',file:'manifest.json',path:null,identity:[],fields:{id:{type:'string'},name:{type:'string'},author:{type:'string'},start_room:{type:'string',ref:'rooms'},format_version:{type:'number'}},defaults:{}},
 rooms:{key:'rooms',title:'Rooms',group:'Exploration',file:'rooms.json',path:[],identity:['id'],fields,defaults:{name:'New room',description:'Describe this place.',grid_link:false}},
 exits:{key:'exits',title:'Passages',group:'Exploration',file:'exits.json',path:[],identity:['id'],fields:{from:{type:'string',ref:'rooms'},to:{type:'string',ref:'rooms'},name:{type:'string'},direction:{type:'string'}},defaults:{}},
};
const registry:any={collections,shapes:{},references:{},conditions:{},actions:{},events:{},handbook:'## Rooms\nRooms have names and descriptions.',coverage:Object.values(collections).map((c:any)=>({key:c.key,title:c.title,fields:4})),terrain:[],directions:['north','northeast','east','southeast','south','southwest','west','northwest','up','down','in','out']};
let project:Project;let savedFiles:Row;let undo:Row[];let requests:string[];
const clone=<T,>(x:T):T=>JSON.parse(JSON.stringify(x));
beforeEach(()=>{
 project={id:'n:test-world',kind:'new',worldId:'test-world',path:'New world test-world',name:'Test World',revision:0,dirty:false,canUndo:false,canRedo:false,files:{'manifest.json':{id:'test-world',format_version:2,name:'Test World',author:'Tester',start_room:'arrival'},'rooms.json':[{id:'arrival',name:'Arrival',description:'Welcome.',x:0,y:0,z:0,grid_link:false}],'exits.json':[]}};savedFiles=clone(project.files);undo=[];requests=[];
 vi.stubGlobal('ResizeObserver',class{observe(){ }disconnect(){ }});
 const memory=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(k:string)=>memory.get(k)??null,setItem:(k:string,v:string)=>memory.set(k,v),removeItem:(k:string)=>memory.delete(k),clear:()=>memory.clear()});
 vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockImplementation(()=>new Proxy({},{get:()=>()=>{}}) as any);
 Object.defineProperty(HTMLCanvasElement.prototype,'setPointerCapture',{configurable:true,value:()=>{}});
 vi.stubGlobal('PointerEvent',MouseEvent);
 vi.stubGlobal('fetch',vi.fn(async(url:string,options?:RequestInit)=>{
  requests.push(url);const body=options?.body?JSON.parse(String(options.body)):{};let result:any={};
  expect(url.startsWith('/mud/forge/api/')).toBe(true);
  if(url.endsWith('/schema'))result=registry;
  else if(url.endsWith('/worlds'))result={worlds:[],drafts:[]};
  else if(url.endsWith('/catalog'))result={presets:[],themes:[]};
  else if(url.endsWith('/worlds/new')){project.name=body.name;project.files['manifest.json'].name=body.name;result=project;}
  else if(url.endsWith('/transaction')){
   expect(body.revision).toBe(project.revision);
   if(body.operation==='undo'){project.files=undo.pop()!;}
   else{undo.push(clone(project.files));const {collection,index,path,value,record}=body.args;
    if(body.operation==='create_record'){const r={...collections[collection].defaults,...record,id:'room-'+(project.files['rooms.json'].length+1)};project.files['rooms.json'].push(r);project.selected={collection,index:project.files['rooms.json'].length-1};}
    if(body.operation==='set_field'){project.files[collections[collection].file][index][path[0]]=value;project.selected={collection,index};}
    if(body.operation==='patch_record'){for(const patch of body.args.patches){project.files[collections[collection].file][index][patch.path[0]]=patch.value;}project.selected={collection,index};}
   }
   project.revision++;project.dirty=true;project.canUndo=undo.length>0;result=project;
  }
  else if(url.endsWith('/checkpoint')){expect(body.revision).toBe(project.revision);savedFiles=clone(project.files);result=project;}
  else if(url.endsWith('/connections'))result={revision:project.revision,nodes:project.files['rooms.json'].map((r:Row,roomIndex:number)=>({id:r.id,name:r.name,roomIndex,gamePosition:[r.x,r.y,r.z].every(Number.isInteger)?[r.x,r.y,r.z]:null,kind:'authored',boundary:false})),edges:project.files['exits.json'].map((e:Row,exitIndex:number)=>({...e,automatic:false,exitIndex})),associations:[]};
  else if(url.includes('/map?'))result={cells:project.files['rooms.json'].filter((r:Row)=>Number.isInteger(r.x)).map((r:Row)=>({...r,markers:[]})),edges:[]};
  else if(url.includes('/uses?'))result=[];
  else if(url.includes('/inspect?')){const id=new URL(url,'http://test').searchParams.get('room');result={room:project.files['rooms.json'].find((r:Row)=>r.id===id),links:[],uses:[]};}
  else if(url.endsWith('/validate'))result=[];
  else result=project;
  return {ok:true,json:async()=>clone(result)} as Response;
 }));
});

describe('Workbench workflow',()=>{
 it('keeps Grid first and inspects coordinate-free rooms and their passages in Connections',async()=>{
  project.files['rooms.json'].push({id:'inside',name:'Inside',description:'An interior.'});
  project.files['exits.json'].push({from:'arrival',to:'inside',name:'front door'});
  const original=clone(project.files);const user=userEvent.setup();render(<App/>);
  await user.click(await screen.findByRole('button',{name:'Create a world'}));await user.type(screen.getByLabelText('World name'),'Connections World');await user.click(screen.getByRole('button',{name:'Create world'}));
  await screen.findByRole('application',{name:'World grid map'});
  expect(screen.getByRole('tab',{name:'Grid'}).getAttribute('aria-selected')).toBe('true');
  expect(screen.getByText('Rooms without grid coordinates')).toBeTruthy();
  expect(screen.getByText(/These rooms can connect through passages/)).toBeTruthy();
  await user.click(screen.getByRole('tab',{name:'Connections'}));
  await screen.findByText(/2 authored rooms/);
  await user.type(screen.getByLabelText('Search connection rooms'),'inside');
  await user.click(screen.getByRole('button',{name:'Select room Inside (inside)'}));
  await waitFor(()=>expect((screen.getByLabelText('Description') as HTMLTextAreaElement).value).toBe('An interior.'));
  expect(screen.queryByRole('button',{name:'Show on grid'})).toBeNull();
  // Coordinate-free Show on map returns to the diagram without placing the room.
  await user.click(screen.getByRole('button',{name:'Show on map'}));
  expect(screen.getByRole('tab',{name:'Connections'}).getAttribute('aria-selected')).toBe('true');
  await user.clear(screen.getByLabelText('Search connection rooms'));
  await user.click(screen.getByRole('button',{name:'Select room Arrival (arrival)'}));
  await user.click(screen.getByRole('button',{name:'Edit passage front door'}));
  await waitFor(()=>expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('front door'));
  expect(project.files['rooms.json']).toEqual(original['rooms.json']);expect(project.files['exits.json']).toEqual(original['exits.json']);
  expect(requests.some(r=>r.endsWith('/transaction'))).toBe(false);
 });
 it('pages and searches all rooms without coordinates',async()=>{
  for(let i=0;i<101;i++)project.files['rooms.json'].push({id:'inside-'+i,name:'Interior '+i,description:'Interior.'});
  const user=userEvent.setup();render(<App/>);await user.click(await screen.findByRole('button',{name:'Create a world'}));await user.type(screen.getByLabelText('World name'),'Many Rooms');await user.click(screen.getByRole('button',{name:'Create world'}));await screen.findByRole('application');
  expect(screen.queryByRole('button',{name:'Select Interior 100 (inside-100) for optional grid placement'})).toBeNull();
  await user.click(screen.getByRole('button',{name:'Next rooms without coordinates'}));
  expect(screen.getByRole('button',{name:'Select Interior 100 (inside-100) for optional grid placement'})).toBeTruthy();
  await user.type(screen.getByLabelText('Search rooms without grid coordinates'),'Interior 0');
  expect(screen.getByRole('button',{name:'Select Interior 0 (inside-0) for optional grid placement'})).toBeTruthy();
  expect(screen.queryByRole('button',{name:'Next rooms without coordinates'})).toBeNull();
 });
 it('opens the grid for a positioned room from the connection inspector',async()=>{
  const user=userEvent.setup();render(<App/>);await user.click(await screen.findByRole('button',{name:'Create a world'}));await user.type(screen.getByLabelText('World name'),'Grid Return');await user.click(screen.getByRole('button',{name:'Create world'}));await screen.findByRole('application');
  await user.click(screen.getByRole('tab',{name:'Connections'}));await user.click(await screen.findByRole('button',{name:'Select room Arrival (arrival)'}));
  await user.click(screen.getByRole('button',{name:'Show on map'}));
  expect(screen.getByRole('tab',{name:'Grid'}).getAttribute('aria-selected')).toBe('true');
  expect(screen.getByRole('application',{name:'World grid map'})).toBeTruthy();
 });
 it('creates a world, draws a room, edits prose, and checkpoints the applied draft',async()=>{
  const user=userEvent.setup();render(<App/>);
  await user.click(await screen.findByRole('button',{name:'Create a world'}));
  await user.type(screen.getByLabelText('World name'),'The Test Coast');
  await user.click(screen.getByRole('button',{name:'Create world'}));
  await screen.findByRole('application',{name:'World grid map'});
  await user.click(screen.getByRole('button',{name:'Add room'}));
  fireEvent.pointerDown(screen.getByRole('application'),{clientX:480,clientY:288,button:0,pointerId:1});
  await waitFor(()=>expect(project.files['rooms.json']).toHaveLength(2));
  await waitFor(()=>expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('New room'));
  const prose=screen.getByLabelText('Description');await user.clear(prose);await user.type(prose,'Lanterns shine above the harbor.');
  await user.click(screen.getByRole('button',{name:/Checkpoint/}));
  await waitFor(()=>expect(savedFiles['rooms.json'][1].description).toBe('Lanterns shine above the harbor.'));
  expect(requests.some(r=>r.endsWith('/checkpoint'))).toBe(true);
  expect(screen.queryByRole('alert')).toBeNull();
 });
 it('opens review and runs checks without a real browser host',async()=>{
  const user=userEvent.setup();render(<App/>);await user.click(await screen.findByRole('button',{name:'Create a world'}));await user.type(screen.getByLabelText('World name'),'Review World');await user.click(screen.getByRole('button',{name:'Create world'}));await screen.findByRole('application');await user.click(screen.getByRole('button',{name:'Review'}));await user.click(screen.getByRole('button',{name:'Validate world'}));await screen.findByText('Your world passes every check.');
 });
});

describe('Phone layout',()=>{
 const phone=()=>vi.stubGlobal('matchMedia',(query:string)=>({matches:query.includes('max-width: 760px'),media:query,addEventListener(){},removeEventListener(){}}));
 const touch=(target:Element,type:string,init:Row)=>{const event=new MouseEvent(type,{bubbles:true,cancelable:true,...init});Object.defineProperty(event,'pointerId',{value:init.pointerId});Object.defineProperty(event,'pointerType',{value:'touch'});fireEvent(target,event);};
 const open=async(user:ReturnType<typeof userEvent.setup>)=>{render(<App/>);await user.click(await screen.findByRole('button',{name:'Create a world'}));await user.type(screen.getByLabelText('World name'),'Pocket World');await user.click(screen.getByRole('button',{name:'Create world'}));await screen.findByRole('application',{name:'World grid map'});};
 it('reaches every workspace action through the menu, the collections drawer, and the inspector sheet',async()=>{
  phone();const user=userEvent.setup();await open(user);
  const shell=document.querySelector('.app-shell')!;
  await waitFor(()=>expect(shell.className).toContain('left-collapsed'));expect(shell.className).toContain('right-collapsed');
  await user.click(screen.getByRole('button',{name:'More actions'}));
  const menu=within(screen.getByRole('dialog',{name:'World actions'}));
  for(const name of ['Find anything','Checkpoint','World settings','Duplicate world','Export draft ZIP','All worlds'])expect(menu.getByRole('button',{name:new RegExp(name)})).toBeTruthy();
  await user.click(menu.getByRole('button',{name:/Backups & recovery/}));
  await waitFor(()=>expect(requests.some(r=>r.endsWith('/backups'))).toBe(true));
  await user.click(screen.getByRole('button',{name:'Done'}));
  await user.click(screen.getByRole('button',{name:'Content library'}));
  await user.click(screen.getByRole('button',{name:'Choose a content collection'}));
  expect(shell.className).not.toContain('left-collapsed');
  await user.click(screen.getByRole('button',{name:/^Passages/}));
  expect(shell.className).toContain('left-collapsed');
  await user.click(screen.getByRole('button',{name:'Choose a content collection'}));await user.click(screen.getByRole('button',{name:/^Rooms/}));
  await user.click(screen.getByRole('button',{name:/Arrival/}));
  expect(shell.className).not.toContain('right-collapsed');
  await user.click(screen.getByRole('button',{name:'Close inspector'}));
  expect(shell.className).toContain('right-collapsed');
 });
 it('adds a room on a touch tap, not when the finger first lands',async()=>{
  phone();const user=userEvent.setup();await open(user);
  await user.click(screen.getByRole('button',{name:'Add room'}));const canvas=screen.getByRole('application');
  touch(canvas,'pointerdown',{clientX:480,clientY:288,button:0,pointerId:7});
  expect(requests.some(r=>r.endsWith('/transaction'))).toBe(false);
  touch(canvas,'pointerup',{clientX:481,clientY:289,button:0,pointerId:7});
  await waitFor(()=>expect(project.files['rooms.json']).toHaveLength(2));
 });
});
