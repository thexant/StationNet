import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { chapters } from "../stationnet/application_packages/echo-choir/levels.js";
import * as engine from "../stationnet/application_packages/echo-choir/engine.js";

const source = readFileSync(new URL("../stationnet/application_packages/echo-choir/main.js",import.meta.url),"utf8");
const saved = new Map();
saved.set(engine.SAVE_KEY,{unlocked:18,best:{},lastChapter:0,soundOn:false});
let tree = [], keyHandler, scoreAttempts = 0, scoreSubmitted = 0, started;
const component = type => (value,options={}) => ({type,value,...options});
const ui = {
  modules: { async load(path) { return path === "levels.js" ? {chapters} : engine; } },
  storage: { async get(key) { return saved.get(key) ?? null; },
    async set(key,value) { saved.set(key,structuredClone(value)); return true; } },
  scores: { async leaderboard() { return {leaderboard:[]}; },
    async submit(score) { scoreAttempts++; if(scoreAttempts===1) throw Error("offline");
      scoreSubmitted=score; return {leaderboard:[{username:"singer",score}]}; } },
  sound: {playTone(){},play(){},sequence(){}},
  display: {async viewport() {return {width:390,height:844,orientation:"portrait"};}},
  presentation: {onChange(){}}, animate(){}, onKey(handler){keyHandler=handler;},
  render(...items){tree=items;},
  update(id,item) {
    const replace=nodes=>{for(let i=0;i<nodes.length;i++) {
      if(nodes[i]?.id===id) {nodes[i]=item;return true;}
      if(nodes[i]?.items && nodes[i].type==="panel" && replace(nodes[i].items)) return true;
    }return false;};
    replace(tree);
  },
  title:component("title"), text:component("text"), statusBar:component("status-bar"), preformatted:component("pre"),
  canvas:(width,height,commands,options={})=>({type:"canvas",width,height,commands,...options}),
  menu:(label,items,handler,options={})=>({type:"menu",label,items,handler,...options}),
  button:(label,handler,options={})=>({type:"button",label,handler,...options}),
  select:(name,label,items,options={})=>({type:"select",name,label,items,...options}),
  slider:(name,label,options={})=>({type:"slider",name,label,...options}),
  panel:(label,items,options={})=>({type:"panel",label,items,...options}),
  table:(headers,rows,options={})=>({type:"table",headers,rows,...options})
};
globalThis.StationNet={start(callback){started=callback(ui);}};
vm.runInThisContext(source,{filename:"echo-choir/main.js"});
await started;
const flush=async()=>{for(let i=0;i<50;i++) await new Promise(setImmediate);};
const key=code=>keyHandler({code,phase:"down",repeat:false,ctrlKey:false,altKey:false,metaKey:false});
const find=(type,label,nodes=tree)=>{
  for(const node of nodes) {
    if(node?.type===type && (label===undefined||node.label===label)) return node;
    if(node?.type==="panel") {const child=find(type,label,node.items);if(child)return child;}
  }
  return null;
};
await flush();
assert.equal(find("canvas").width,350);
assert.equal(find("select").items.length,19);
for(let i=0;i<2;i++) key("ArrowRight");
for(let i=0;i<2;i++) key("ArrowDown");
key("Enter");
for(let i=0;i<8;i++) key("ArrowRight");
for(let i=0;i<4;i++) key("ArrowDown");
key("ArrowDown");
key("ArrowRight");
key("ArrowDown");
await flush();
assert.equal(scoreAttempts,1,"First chapter submits its improved campaign total");
assert.ok(saved.get(engine.SAVE_KEY).pendingScore>0,"Failed score remains in private storage");
assert.match(find("panel","Station leaderboard").items.find(x=>x.id==="score-message").value,/Score pending/);
find("button","RETRY SCORE").handler();
await flush();
assert.equal(scoreAttempts,2);
assert.equal(scoreSubmitted,saved.get(engine.SAVE_KEY).best[0]);
assert.equal(saved.get(engine.SAVE_KEY).pendingScore,0);
assert.equal(find("table").rows[0][1],"singer");
for(let i=0;i<chapters.length;i++) {
  find("select").onChange({value:String(i)});
  const board=find("canvas");
  assert.ok(board.commands.length<=600,`Chapter ${i+1} board stays inside the API budget`);
  assert.equal(board.width,350);
}
console.log("Echo Choir runtime: chapter score retry and mobile package UI passed");
