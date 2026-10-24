"use strict";
// Run with: node tests/test_radio_client_recovery.js
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const status={textContent:""};
const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)}};
const intervals=[];
const body={dataset:{radioStateUrl:"/state",radioReceiveUrl:"/receive",broadcastAudioUrl:"/audio/0",broadcastAdvanceUrl:"/advance",broadcastTimingUrl:"/timing",radioCsrf:"token"}};
const document={body,querySelector:selector=>selector==="[data-radio-player]"?{querySelector:()=>null}:null,querySelectorAll:selector=>selector==="[data-radio-status]"?[status]:[],addEventListener:()=>{}};
let position=45,failReceive=true,advanceCalls=0,audioInstances=[],activeAvailable=true;
const active=()=>({id:2,hosted:true,mode:"MUSIC",action_type:"music",audio_file_id:7,track_serial:1,track_offset:position});
const response=payload=>({ok:true,status:200,redirected:false,json:async()=>payload});
const fetch=async url=>{
  if(url==="/state")return response({bands:[],frequency:"101.50",band:{name:"FM",access:"FULL"},cursor:0,powered:true,access:"FULL",active:active()});
  if(url.startsWith("/receive")){if(failReceive){failReceive=false;throw new TypeError("network disconnected")}return response({band:{name:"FM",access:"FULL"},access:"FULL",events:[],cursor:0,active:activeAvailable?active():null})}
  if(url==="/advance"){advanceCalls++;return response({advanced:true})}
  return response({});
};
class AudioElement{
  constructor(){this.listeners={};this.paused=true;this.ended=false;this.duration=120;this.currentTime=0;this.readyState=4;this.error=null;audioInstances.push(this)}
  addEventListener(name,fn){this.listeners[name]=fn}
  load(){this.paused=true;this.currentTime=0}
  pause(){this.paused=true}
  play(){this.paused=false;return Promise.resolve()}
  removeAttribute(name){if(name==="src")this.src=""}
  emit(name){this.listeners[name]?.()}
}
class AudioContext{
  constructor(){this.state="running";this.currentTime=0;this.destination={}}
  createAnalyser(){return {connect:()=>({connect:()=>{}})}}
  createGain(){return {gain:{setTargetAtTime:()=>{}},connect:()=>({connect:()=>{}})}}
  createMediaElementSource(){return {connect:()=>({connect:()=>{}})}}
}
const sandbox={document,window:{AudioContext,addEventListener:()=>{}},Audio:AudioElement,fetch,localStorage:storage(),sessionStorage:storage(),console,location:{reload:()=>{throw new Error("unexpected reload")}},requestAnimationFrame:()=>{},setTimeout,clearTimeout,setInterval:fn=>{intervals.push(fn);return intervals.length},clearInterval:()=>{},FormData:class{set(){}},AbortController,Date,performance};
vm.runInNewContext(fs.readFileSync("stationnet/static/radio.js","utf8"),sandbox);
(async()=>{
  await wait(10);
  assert.equal(audioInstances.length,1,"one media element per listener");
  assert.equal(audioInstances[0].currentTime,45,"starts at live broadcast position");
  const receive=intervals[0];
  await receive();
  assert.equal(status.textContent,"LINK RETRYING");
  assert.equal(audioInstances[0].paused,false,"a polling outage must not stop current audio");
  await wait(550);
  position=47;await receive();
  assert.match(status.textContent,/RECEIVING MUSIC/);
  const audio=audioInstances[0];audio.error={code:2};audio.emit("error");
  assert.equal(advanceCalls,0,"local media errors must not advance the shared broadcast");
  await receive();assert.equal(status.textContent,"AUDIO RETRYING");
  await wait(550);
  position=72;await receive();
  assert.equal(audio.currentTime,72,"recovery seeks to the current live position");
  assert.equal(audioInstances.length,1,"retry reuses the media element");
  assert.equal(advanceCalls,0);
  for(let i=0;i<20;i++){position+=.25;await receive()}
  assert.equal(audioInstances.length,1,"prolonged polling does not create duplicate streams");
  activeAvailable=false;await receive();
  assert.equal(audio.paused,true,"an unavailable broadcast stops its media");
  assert.equal(status.textContent,"IDLE");
  console.log("radio client recovery: passed");
})().catch(problem=>{console.error(problem);process.exitCode=1});



