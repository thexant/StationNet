import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {webcrypto} from "node:crypto";
import vm from "node:vm";

const source=readFileSync(new URL("../stationnet/static/radio.js",import.meta.url),"utf8");

async function pressV({radioPage=true,powered=true,access="FULL",voice=true,frequency="145.00",mode="VOICE",modifier=null}={}){
  const listeners=new Map(),storage=()=>{const values=new Map();return{getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key)}};
  const voicePanel={hidden:true},pttButton={disabled:true,addEventListener(){},classList:{add(){},remove(){}}};
  const consolePanel=radioPage?{querySelector:selector=>({"[data-radio-voice]":voicePanel,"[data-radio-ptt]":pttButton})[selector]||null,querySelectorAll:()=>[]}:null;
  const player={querySelector:()=>null};
  const document={body:{dataset:{radioStateUrl:"/state",radioStartUrl:"/start"}},querySelector:selector=>selector==="[data-radio-player]"?player:selector==="[data-radio-console]"?consolePanel:null,querySelectorAll:()=>[],addEventListener:(name,callback)=>listeners.set(name,callback)};
  let microphoneRequests=0;
  const response={ok:true,json:async()=>({frequency,band:{supports_live_voice:Number(voice),supports_cw:1,supports_sstv:0},bands:[],access,powered,cursor:0,active:null})};
  const fetch=async()=>response;
  const window={fetch,isSecureContext:true,addEventListener(){},dispatchEvent(){},AudioContext:null,BroadcastChannel:null};
  const navigator={mediaDevices:{getUserMedia:()=>{microphoneRequests++;return new Promise(()=>{})}}};
  const localStorage=storage();localStorage.setItem("stationnet.radio.mode",mode);
  const context={document,window,navigator,crypto:webcrypto,sessionStorage:storage(),localStorage,fetch,Headers,FormData,URL,location:{href:"http://localhost/radio"},setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){},requestAnimationFrame(){},CustomEvent:class{},HTMLInputElement:class{},HTMLTextAreaElement:class{},HTMLSelectElement:class{},console};
  vm.runInNewContext(source,context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(voicePanel.hidden,!radioPage||!powered||access!=="FULL"||!voice||mode!=="VOICE");
  const event={code:"KeyV",target:{},repeat:false,ctrlKey:modifier==="ctrl",metaKey:modifier==="meta",altKey:modifier==="alt",preventDefault(){this.prevented=true}};
  listeners.get("keydown")(event);
  return{microphoneRequests,prevented:!!event.prevented};
}

for(const scenario of [
  {radioPage:false},
  {powered:false},
  {access:"LISTEN"},
  {access:"DENIED"},
  {voice:false},
  {mode:"CW"},
  {frequency:null},
  {modifier:"ctrl"},
]){
  const result=await pressV(scenario);
  assert.equal(result.microphoneRequests,0,JSON.stringify(scenario));
  assert.equal(result.prevented,false,JSON.stringify(scenario));
}
assert.deepEqual(await pressV(),{microphoneRequests:1,prevented:true});
console.log("Radio V shortcut requires an eligible voice channel on the radio page.");
