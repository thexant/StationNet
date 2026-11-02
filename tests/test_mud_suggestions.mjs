import assert from "node:assert/strict";
import {commandSuggestions, historyStep} from "../stationnet/static/mud_suggestions.mjs";

const catalog = [
  {name:"attack",key:"attack",description:"Attack an enemy"},
  {name:"expedition",key:"expedition",description:"Launch an expedition"},
  {name:"expeditions",key:"expedition",description:"List expedition themes"},
  {name:"go",key:"go",description:"Travel"},
  {name:"help",key:"help",description:"Show help"},
];
const state = {
  room:{id:"village",mode:"traditional",npcs:[],items:[],exits:[{direction:"north"}]},
  combat:{enemies:[]}, inventory:{inventory:[],equipment:[]}, hub:null,
};
const values = (query, snapshot = state) => commandSuggestions(query,snapshot,catalog).map(item=>item.command);

assert.deepEqual(values("hello"), []);
assert.deepEqual(values("/exp"), []);
assert.deepEqual(values("/go n"), ["/go north"]);
assert.deepEqual(values("/att"), []);

const oneRat = {...state,room:{...state.room,npcs:[{name:"Rat",display_name:"Rat",hostile:true}]},
  combat:{enemies:[{name:"Rat",display_name:"Rat",status:"active"}]}};
assert.deepEqual(values("/att",oneRat), ["/attack Rat", "/attack"]);

const twoRats = {...oneRat,room:{...state.room,npcs:[
  {name:"Rat",display_name:"Rat (1)",hostile:true},
  {name:"Rat",display_name:"Rat (2)",hostile:true},
]},combat:{enemies:[
  {name:"Rat",display_name:"Rat (1)",status:"active"},
  {name:"Rat",display_name:"Rat (2)",status:"defeated"},
]}};
assert.deepEqual(values("/att",twoRats), ["/attack Rat 1", "/attack"]);
assert.deepEqual(values("/attack rat",twoRats), ["/attack Rat 1"]);

const serviceNpc = {...state,room:{...state.room,npcs:[
  {name:"Arkady Rudenko",display_name:"Arkady Rudenko",service_labels:["Merchant","Banker"],hostile:false},
]}};
assert.deepEqual(values("/talk Ark",serviceNpc), ["/talk Arkady Rudenko"]);
assert.deepEqual(values("/look Ark",serviceNpc), ["/look Arkady Rudenko"]);

const portal = {...state,room:{...state.room,id:"stationnet:portal",mode:"procedural"},
  hub:{shop:[{name:"Health Potion"}],storage:[]}};
assert.deepEqual(values("/exp",portal), ["/expeditions", "/expedition"]);
assert.deepEqual(values("/shop b",portal), ["/shop buy Health Potion"]);

const socialAndItems = {...state,room:{...state.room,
  occupants:[{character_name:"Bea Two"}], dialogue_responses:[{number:1,text:"Ask about town"}],
  items:[{id:"chest",name:"Oak Chest",display_name:"Oak Chest",item_type:"container",is_open:true,
    contents:[{name:"Red Apple",display_name:"Red Apple"}]}]},
  inventory:{inventory:[{id:"apple",name:"Blue Apple",item_type:"misc"}],equipment:[]}};
assert.deepEqual(values("/get Red",socialAndItems), ["/get Red Apple from Oak Chest"]);
assert.deepEqual(values("/put Blue",socialAndItems), ["/put Blue Apple in Oak Chest"]);
assert.deepEqual(values("/respond",socialAndItems), ["/respond 1"]);
assert.deepEqual(values("/tell",socialAndItems), ['/tell "Bea Two" ']);

const storageCatalog = [...catalog, {name:"storage",key:"storage",description:"Deposit or withdraw items"}];
const lockerRoom = {...state,room:{...state.room,storage:[{id:"locker",name:"Locker",scope:"personal"}]},
  inventory:{inventory:[{id:"apple",name:"Blue Apple",item_type:"misc"}],equipment:[]}};
assert.deepEqual(commandSuggestions("/sto",lockerRoom,storageCatalog).map(item=>item.command),
  ["/storage deposit Blue Apple", "/storage"]);
const twoPoints = {...lockerRoom,room:{...lockerRoom.room,storage:[{name:"Locker"},{name:"Oak Chest"}]}};
assert.deepEqual(commandSuggestions("/storage deposit Blue Apple into \"O",twoPoints,storageCatalog)
  .map(item=>item.command), ['/storage deposit Blue Apple into "Oak Chest"']);
assert.deepEqual(commandSuggestions("/sto",state,storageCatalog), []);

const history = ["/look", "/attack Rat 1"];
assert.deepEqual(historyStep(history,-1,"unfinished",1),{index:0,value:"/attack Rat 1"});
assert.deepEqual(historyStep(history,0,"unfinished",1),{index:1,value:"/look"});
assert.deepEqual(historyStep(history,1,"unfinished",-1),{index:0,value:"/attack Rat 1"});
assert.deepEqual(historyStep(history,0,"unfinished",-1),{index:-1,value:"unfinished"});
const wayfinding = {...state,room:{...state.room,wilderness:true,
  exits:[{direction:"north"},{direction:"northeast"}],
  wayfinding:{destination:"Kessa Gate",direction:"northeast",heading:"Follow the road east."}}};
assert.deepEqual(commandSuggestions("/tr",wayfinding,catalog), [
  {command:"/travel northeast",detail:"Toward Kessa Gate"},
  {command:"/travel north",detail:"Travel onward"},
]);
assert.deepEqual(values("/dir",wayfinding), ["/directions"]);
console.log("MUD suggestions and command history passed");
