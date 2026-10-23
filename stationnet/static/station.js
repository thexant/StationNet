"use strict";
// StationNet is intentionally terminal-like: text entry should never surface
// browser, password-manager, spelling, or mobile-keyboard suggestions. Keep the
// policy here so it also covers controls added later by hosted applications.
const disableTextEntrySuggestions=root=>{
  if(!(root instanceof Element||root instanceof Document))return;
  const forms=root.matches?.("form")?[root]:[];
  forms.push(...root.querySelectorAll("form"));
  for(const form of forms)form.setAttribute("autocomplete","off");
  const controls=root.matches?.("input,textarea")?[root]:[];
  controls.push(...root.querySelectorAll("input,textarea"));
  for(const control of controls){
    const textLike=control instanceof HTMLTextAreaElement||(control instanceof HTMLInputElement&&["text","search","email","url","tel","password"].includes(control.type));
    if(!textLike)continue;
    control.setAttribute("autocomplete","off");
    control.setAttribute("autocorrect","off");
    control.setAttribute("autocapitalize","off");
    control.setAttribute("spellcheck","false");
    control.removeAttribute("list");
  }
};
disableTextEntrySuggestions(document);
new MutationObserver(records=>{
  for(const record of records)for(const node of record.addedNodes)if(node instanceof Element)disableTextEntrySuggestions(node);
}).observe(document.documentElement,{childList:true,subtree:true});

const sfxStorageKey="stationnet.sfx.enabled";
let sfxEnabled=true;
try{sfxEnabled=localStorage.getItem(sfxStorageKey)!=="false"}catch(_){}
const sfxBase=document.body.dataset.sfxBase||"";
const sfxFiles={click:"click.ogg",emailsent:"emailsent.ogg",newchat:"newchat.ogg",newmail:"newmail.ogg",sendmessage:"sendmessage.ogg","ptt-on":"ptt-on.ogg","ptt-off":"ptt-off.ogg"};
const sfxAudio=Object.fromEntries(Object.entries(sfxFiles).map(([name,file])=>{const audio=new Audio(`${sfxBase}${file}`);audio.preload="auto";return[name,audio]}));
const playSfx=name=>{
  if(!sfxEnabled||!sfxAudio[name])return null;
  const audio=sfxAudio[name].cloneNode();
  audio.volume=.7;
  audio.play().catch(()=>{});
  return audio;
};
window.StationNetSfx={play:playSfx,isEnabled:()=>sfxEnabled};

const sfxToggle=document.querySelector("[data-sfx-toggle]");
const renderSfxToggle=()=>{
  if(!sfxToggle)return;
  sfxToggle.textContent=`SFX: ${sfxEnabled?"ON":"OFF"}`;
  sfxToggle.setAttribute("aria-pressed",String(sfxEnabled));
  sfxToggle.setAttribute("aria-label",`${sfxEnabled?"Disable":"Enable"} interface sound effects`);
};
renderSfxToggle();
sfxToggle?.addEventListener("click",()=>{
  if(sfxEnabled)playSfx("click");
  sfxEnabled=!sfxEnabled;
  try{localStorage.setItem(sfxStorageKey,String(sfxEnabled))}catch(_){}
  renderSfxToggle();
  if(sfxEnabled)playSfx("click");
});
document.addEventListener("click",event=>{
  if(!(event.target instanceof Element))return;
  const control=event.target.closest('a,button,summary,[role="button"],[role="menuitem"],input[type="button"],input[type="submit"],input[type="reset"],input[type="checkbox"],input[type="radio"],input[type="file"],select,option');
  if(!control||control===sfxToggle||control.matches(":disabled,[aria-disabled='true']"))return;
  playSfx("click");
});
for(const form of document.querySelectorAll("form[data-sfx-submit]"))form.addEventListener("submit",event=>{
  if(form.dataset.sfxSubmitting||!sfxEnabled)return;
  event.preventDefault();
  form.dataset.sfxSubmitting="true";
  playSfx(form.dataset.sfxSubmit);
  setTimeout(()=>form.requestSubmit(),180);
});

const mailNotificationsUrl=document.body.dataset.mailNotificationsUrl;
if(mailNotificationsUrl){
  const mailCursorKey=`stationnet.mail.cursor.${document.body.dataset.userId}`;
  const storedMailCursor=sessionStorage.getItem(mailCursorKey);
  let mailCursor=storedMailCursor===null?Number(document.body.dataset.mailLatestId):Number(storedMailCursor);
  if(!Number.isFinite(mailCursor))mailCursor=0;
  sessionStorage.setItem(mailCursorKey,String(mailCursor));
  let mailPollBusy=false;
  const pollMail=async()=>{
    if(mailPollBusy)return;
    mailPollBusy=true;
    try{
      const response=await fetch(`${mailNotificationsUrl}?after=${mailCursor}`,{headers:{Accept:"application/json"},cache:"no-store"});
      if(response.status===401||response.redirected)return;
      if(!response.ok)throw new Error("Mail notification link unavailable.");
      const payload=await response.json();
      const latestId=Math.max(mailCursor,Number(payload.latest_id)||0);
      if(latestId!==mailCursor){mailCursor=latestId;sessionStorage.setItem(mailCursorKey,String(mailCursor))}
      if(Number(payload.new_count)>0)playSfx("newmail");
    }catch(_){}finally{mailPollBusy=false}
  };
  pollMail();
  setInterval(pollMail,5000);
}else{
  for(let index=sessionStorage.length-1;index>=0;index--){
    const key=sessionStorage.key(index);
    if(key?.startsWith("stationnet.mail.cursor."))sessionStorage.removeItem(key);
  }
}

const clock=document.querySelector("[data-clock]");
const dateReadout=document.querySelector("[data-date]");
if(clock){const tick=()=>{const now=new Date();clock.textContent=now.toLocaleTimeString([],{hour12:false});if(dateReadout)dateReadout.textContent=now.toLocaleDateString([],{weekday:"short",month:"short",day:"2-digit",year:"numeric"}).toUpperCase()};tick();setInterval(tick,1000)}
const localTimestamp=value=>{
  if(!value)return "";
  const normalized=/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)?`${value.replace(" ","T")}Z`:value;
  const timestamp=new Date(normalized);
  return Number.isNaN(timestamp.getTime())?value:timestamp.toLocaleString([],{dateStyle:"medium",timeStyle:"short"});
};
for(const time of document.querySelectorAll("[data-local-time]"))time.textContent=localTimestamp(time.dateTime||time.textContent);
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
  let initialized=false;

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
      const messages=payload.messages||[];
      for(const message of messages)addMessage(message);
      if(initialized&&messages.some(message=>!message.mine))playSfx("newchat");
      initialized=true;
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
      playSfx("sendmessage");
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

