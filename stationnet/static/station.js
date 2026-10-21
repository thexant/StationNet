"use strict";
const clock=document.querySelector("[data-clock]");
const dateReadout=document.querySelector("[data-date]");
if(clock){const tick=()=>{const now=new Date();clock.textContent=now.toLocaleTimeString([],{hour12:false});if(dateReadout)dateReadout.textContent=now.toLocaleDateString([],{weekday:"short",month:"short",day:"2-digit",year:"numeric"}).toUpperCase()};tick();setInterval(tick,1000)}
document.addEventListener("keydown",e=>{const editing=e.target instanceof HTMLInputElement||e.target instanceof HTMLTextAreaElement;if(e.key==="/"&&!editing){e.preventDefault();const command=document.querySelector("[data-command-input]");if(command){command.focus();if(!command.value)command.value="/"}else document.querySelector("#service-nav")?.focus()}if(e.key==="Escape"&&document.activeElement instanceof HTMLElement)document.activeElement.blur()});
for(const form of document.querySelectorAll("form[data-confirm]"))form.addEventListener("submit",e=>{if(!confirm(form.dataset.confirm))e.preventDefault()});

for(const button of document.querySelectorAll("[data-copy-button]"))button.addEventListener("click",async()=>{
  const input=button.parentElement?.querySelector("[data-copy-value]");
  if(!input)return;
  try{
    if(navigator.clipboard)await navigator.clipboard.writeText(input.value);
    else{input.select();document.execCommand("copy")}
    const original=button.textContent;
    button.textContent="COPIED";
    setTimeout(()=>button.textContent=original,1600);
  }catch(_){input.select();input.setSelectionRange(0,input.value.length)}
});

const inviteInput=document.querySelector('input[name="invite"]');
if(inviteInput&&!inviteInput.value&&location.hash){
  const inviteCode=new URLSearchParams(location.hash.slice(1)).get("invite");
  if(inviteCode){inviteInput.value=inviteCode;history.replaceState(null,"",location.pathname+location.search)}
}

for(const panel of document.querySelectorAll("[data-collapsible-panel]")){
  const toggle=panel.querySelector(":scope > [data-panel-toggle]");
  const content=panel.querySelector(":scope > [data-panel-content]");
  if(!toggle||!content)continue;
  const storageKey=`stationnet.panel.${panel.dataset.panelKey||content.id}`;
  let expanded=true;
  try{expanded=localStorage.getItem(storageKey)!=="collapsed"}catch(_){}
  const setExpanded=value=>{
    expanded=value;
    toggle.setAttribute("aria-expanded",String(expanded));
    content.hidden=!expanded;
    try{localStorage.setItem(storageKey,expanded?"expanded":"collapsed")}catch(_){}
  };
  setExpanded(expanded);
  toggle.addEventListener("click",()=>setExpanded(!expanded));
}

if(document.body.dataset.presenceUrl){
  setInterval(()=>fetch(document.body.dataset.presenceUrl,{cache:"no-store"}).catch(()=>{}),20000);
}

for(const chat of document.querySelectorAll("[data-live-chat]")){
  const log=chat.querySelector("[data-chat-log]");
  const form=chat.querySelector("[data-chat-form]");
  const error=chat.querySelector("[data-chat-error]");
  const url=chat.dataset.messagesUrl;
  let lastId=0;
  let busy=false;

  const localTimestamp=value=>{
    if(!value)return "";
    const normalized=/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)
      ? `${value.replace(" ","T")}Z`
      : value;
    const timestamp=new Date(normalized);
    if(Number.isNaN(timestamp.getTime()))return value;
    return timestamp.toLocaleString([],{dateStyle:"medium",timeStyle:"short"});
  };

  const addMessage=message=>{
    chat.querySelector("[data-chat-empty]")?.remove();
    const row=document.createElement("div");
    row.className="chat-message"+(message.mine?" mine":"");
    const meta=document.createElement("span");
    meta.textContent=`${message.username} - [${message.callsign}] · ${localTimestamp(message.created_at)}`;
    const body=document.createElement("p");
    body.textContent=message.body;
    row.append(meta,body);
    log.append(row);
    lastId=Math.max(lastId,message.id);
    log.scrollTop=log.scrollHeight;
  };

  const poll=async()=>{
    if(busy)return;
    busy=true;
    try{
      const response=await fetch(`${url}?after=${lastId}`,{headers:{"Accept":"application/json"},cache:"no-store"});
      if(response.status===401||response.redirected){location.reload();return}
      const payload=await response.json();
      for(const message of payload.messages||[])addMessage(message);
      if(payload.active===false){
        error.textContent="LINK CLOSED: USER OFFLINE";
        form.querySelector("textarea").disabled=true;
        form.querySelector("button").disabled=true;
      }
    }catch(_){error.textContent="LINK RETRYING..."}
    finally{busy=false}
  };

  form.addEventListener("submit",async event=>{
    event.preventDefault();
    error.textContent="";
    const button=form.querySelector("button");
    button.disabled=true;
    try{
      const response=await fetch(url,{method:"POST",body:new FormData(form),headers:{"Accept":"application/json"}});
      const payload=await response.json();
      if(!response.ok)throw new Error(payload.error||"Transmission failed.");
      form.querySelector("textarea").value="";
      await poll();
    }catch(problem){error.textContent=problem.message}
    finally{button.disabled=false;form.querySelector("textarea").focus()}
  });
  form.querySelector("textarea").addEventListener("keydown",event=>{
    if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){
      event.preventDefault();
      form.requestSubmit();
    }
  });
  poll();
  setInterval(poll,2000);
}

