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

const portal = {...state,room:{...state.room,id:"stationnet:portal",mode:"procedural"},
  hub:{shop:[{name:"Health Potion"}],storage:[]}};
assert.deepEqual(values("/exp",portal), ["/expeditions", "/expedition"]);
assert.deepEqual(values("/shop b",portal), ["/shop buy Health Potion"]);

const history = ["/look", "/attack Rat 1"];
assert.deepEqual(historyStep(history,-1,"unfinished",1),{index:0,value:"/attack Rat 1"});
assert.deepEqual(historyStep(history,0,"unfinished",1),{index:1,value:"/look"});
assert.deepEqual(historyStep(history,1,"unfinished",-1),{index:0,value:"/attack Rat 1"});
assert.deepEqual(historyStep(history,0,"unfinished",-1),{index:-1,value:"unfinished"});
console.log("MUD suggestions and command history passed");
