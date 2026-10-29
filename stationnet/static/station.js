"use strict";
// Keep spelling and automatic capitalization out of terminal-style fields.
// Authentication fields retain their declared autocomplete values so password
// managers and the browser can fill them reliably.
const disableTextEntrySuggestions=root=>{
  if(!(root instanceof Element||root instanceof Document))return;
  const controls=root.matches?.("input,textarea")?[root]:[];
  controls.push(...root.querySelectorAll("input,textarea"));
  for(const control of controls){
    const textLike=control instanceof HTMLTextAreaElement||(control instanceof HTMLInputElement&&["text","search","email","url","tel","password"].includes(control.type));
    if(!textLike)continue;
    if(!control.hasAttribute("autocomplete"))control.setAttribute("autocomplete","off");
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
  audio.play().catch(()=>audio.dispatchEvent(new Event("error")));
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

const vfxStorageKey="stationnet.vfx.enabled";
let vfxEnabled=true;
try{vfxEnabled=localStorage.getItem(vfxStorageKey)!=="false"}catch(_){}
const vfxToggle=document.querySelector("[data-vfx-toggle]");
const renderVfxToggle=()=>{
  document.documentElement.dataset.vfxEnabled=String(vfxEnabled);
  if(!vfxToggle)return;
  vfxToggle.textContent=`VFX: ${vfxEnabled?"ON":"OFF"}`;
  vfxToggle.setAttribute("aria-pressed",String(vfxEnabled));
  vfxToggle.setAttribute("aria-label",`${vfxEnabled?"Disable":"Enable"} CRT scanline effect`);
};
renderVfxToggle();
vfxToggle?.addEventListener("click",()=>{
  vfxEnabled=!vfxEnabled;
  try{localStorage.setItem(vfxStorageKey,String(vfxEnabled))}catch(_){}
  renderVfxToggle();
});

// Each page load leaves fullscreen, and browsers only re-enter it from a user
// gesture, so a remembered preference resumes on the page's first click or key.
const fullscreenStorageKey="stationnet.fullscreen.enabled";
let fullscreenWanted=false;
try{fullscreenWanted=localStorage.getItem(fullscreenStorageKey)==="true"}catch(_){}
let pageLeaving=false;
addEventListener("pagehide",()=>{pageLeaving=true});
const fullscreenToggle=document.querySelector("[data-fullscreen-toggle]");
const saveFullscreen=value=>{
  fullscreenWanted=value;
  try{localStorage.setItem(fullscreenStorageKey,String(value))}catch(_){}
};
const enterFullscreen=()=>document.documentElement.requestFullscreen().catch(()=>{});
const renderFullscreenToggle=()=>{
  if(!fullscreenToggle)return;
  const active=Boolean(document.fullscreenElement);
  const label=`${active?"Exit":"Enter"} fullscreen`;
  fullscreenToggle.setAttribute("aria-pressed",String(active));
  fullscreenToggle.setAttribute("aria-label",label);
  fullscreenToggle.title=label;
};
if(fullscreenToggle&&document.fullscreenEnabled&&typeof document.documentElement.requestFullscreen==="function"){
  fullscreenToggle.hidden=false;
  renderFullscreenToggle();
  fullscreenToggle.addEventListener("click",()=>{
    if(document.fullscreenElement){
      saveFullscreen(false);
      document.exitFullscreen().catch(()=>{});
    }else{
      saveFullscreen(true);
      enterFullscreen();
    }
  });
  document.addEventListener("fullscreenchange",()=>{
    // Leaving with Esc or the browser's own controls turns the preference off.
    if(!document.fullscreenElement&&!pageLeaving&&fullscreenWanted)saveFullscreen(false);
    renderFullscreenToggle();
  });
  if(fullscreenWanted&&!document.fullscreenElement){
    const resume=event=>{
      removeEventListener("pointerdown",resume,true);
      removeEventListener("keydown",resume,true);
      if(event.target===fullscreenToggle||document.fullscreenElement||!fullscreenWanted)return;
      enterFullscreen();
    };
    addEventListener("pointerdown",resume,true);
    addEventListener("keydown",resume,true);
  }
}

const musicToggle=document.querySelector("[data-music-toggle]");
if(musicToggle){
  const musicPositionKey=`stationnet.music.position.${musicToggle.dataset.userId}.${musicToggle.dataset.midiVersion}`;
  const musicKey=`station-theme-${musicToggle.dataset.midiVersion}`;
  const configuredMusicVolume=Number(musicToggle.dataset.midiVolume);
  const musicVolume=Number.isFinite(configuredMusicVolume)?Math.min(1,Math.max(0,configuredMusicVolume/100)):1;
  let musicEnabled=musicToggle.dataset.midiEnabled==="true";
  let radioPowered=sessionStorage.getItem("stationnet.radio.powered")==="true";
  let musicContext=null,musicEngine=null,musicStarting=null,musicAttempt=0;
  let savedPosition=Number(sessionStorage.getItem(musicPositionKey))||0;
  const saveMusicPosition=()=>{
    if(musicEngine?.key===musicKey)savedPosition=musicEngine.position;
    try{sessionStorage.setItem(musicPositionKey,String(Math.max(0,savedPosition)))}catch(_){}
  };
  const renderMusicToggle=()=>{
    musicToggle.textContent=`MUSIC: ${musicEnabled?"ON":"OFF"}`;
    musicToggle.setAttribute("aria-pressed",String(musicEnabled));
    musicToggle.setAttribute("aria-label",`${musicEnabled?"Disable":"Enable"} station MIDI music`);
  };
  const pauseMusic=()=>{
    musicAttempt++;
    saveMusicPosition();
    if(musicStarting)musicEngine?.stop();
    else musicEngine?.pause();
  };
  const startMusic=()=>{
    if(!musicEnabled||radioPowered||musicStarting)return;
    const AudioContextClass=window.AudioContext||window.webkitAudioContext;
    if(!AudioContextClass)return;
    if(!musicContext)musicContext=new AudioContextClass();
    if(musicContext.state==="suspended")musicContext.resume().catch(()=>{});
    if(musicEngine?.key===musicKey){musicEngine.play();return}
    const attempt=++musicAttempt;
    musicStarting=(async()=>{
      const module=await import(`${musicToggle.dataset.midiAssetBase}/engine.js`);
      if(attempt!==musicAttempt)return;
      if(!musicEngine)musicEngine=new module.MidiPlaybackEngine(musicContext,musicContext.destination,musicToggle.dataset.midiAssetBase,key=>{
        if(key!==musicKey||!musicEnabled||radioPowered)return;
        musicEngine.seek(0);
        savedPosition=0;
        try{sessionStorage.setItem(musicPositionKey,"0")}catch(_){}
        musicEngine.play();
      });
      await musicEngine.initialize();
      if(attempt!==musicAttempt)return;
      musicEngine.gain.gain.value=musicVolume;
      await musicEngine.sync(musicKey,musicToggle.dataset.midiUrl,savedPosition,musicVolume);
      if(attempt!==musicAttempt||!musicEnabled||radioPowered){musicEngine.pause();return}
      musicEngine.sequencer.loopCount=-1;
    })().catch(error=>console.warn("Station MIDI Theme could not play:",error)).finally(()=>{
      musicStarting=null;
      if(attempt!==musicAttempt&&musicEnabled&&!radioPowered)startMusic();
    });
  };
  renderMusicToggle();
  window.addEventListener("stationnet:radio-power",event=>{
    radioPowered=Boolean(event.detail?.powered);
    if(radioPowered)pauseMusic();else startMusic();
  });
  musicToggle.addEventListener("click",async()=>{
    musicToggle.disabled=true;
    const previous=musicEnabled;
    musicEnabled=!musicEnabled;
    renderMusicToggle();
    if(musicEnabled)startMusic();else pauseMusic();
    try{
      const data=new FormData();
      data.set("csrf_token",musicToggle.dataset.midiCsrf);
      data.set("enabled",String(musicEnabled));
      const response=await fetch(musicToggle.dataset.midiPreferenceUrl,{
        method:"POST",credentials:"same-origin",
        body:data
      });
      if(!response.ok)throw new Error(`Preference save failed (${response.status}).`);
    }catch(error){
      musicEnabled=previous;
      renderMusicToggle();
      if(musicEnabled)startMusic();else pauseMusic();
      console.warn(error);
    }finally{musicToggle.disabled=false}
  });
  window.addEventListener("pagehide",saveMusicPosition);
  setInterval(()=>{if(musicEnabled&&!radioPowered)saveMusicPosition()},1000);
  document.addEventListener("pointerdown",()=>{if(musicEnabled&&!radioPowered&&musicContext?.state==="suspended")musicContext.resume().catch(()=>{})});
  document.addEventListener("keydown",()=>{if(musicEnabled&&!radioPowered&&musicContext?.state==="suspended")musicContext.resume().catch(()=>{})});
  if(!document.querySelector("[data-radio-player],[data-radio-console]"))startMusic();
}
document.addEventListener("click",event=>{
  if(!(event.target instanceof Element))return;
  const control=event.target.closest('a,button,summary,[role="button"],[role="menuitem"],input[type="button"],input[type="submit"],input[type="reset"],input[type="checkbox"],input[type="radio"],input[type="file"],select,option');
  if(!control||control===sfxToggle||control.matches(":disabled,[aria-disabled='true']"))return;
  const audio=playSfx("click");
  const link=control.closest("a[href]");
  if(!audio||!link||event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||link.target&&link.target!=="_self"||link.hasAttribute("download"))return;
  const destination=new URL(link.href,location.href);
  if(!["http:","https:"].includes(destination.protocol)||destination.href===location.href||destination.origin===location.origin&&destination.pathname===location.pathname&&destination.search===location.search&&destination.hash)return;
  event.preventDefault();
  let navigated=false;
  const navigate=()=>{if(navigated)return;navigated=true;location.assign(destination.href)};
  audio.addEventListener("ended",navigate,{once:true});
  audio.addEventListener("error",navigate,{once:true});
  setTimeout(navigate,1000);
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
  const mailBadge=document.querySelector("[data-mail-count]");
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
      const unread=Math.max(0,Number(payload.unread_count)||0);
      mailBadge.hidden=unread===0;
      mailBadge.textContent=unread>99?"99+":String(unread);
      mailBadge.parentElement.setAttribute("aria-label",unread?`Mail, ${unread} unread messages`:"Mail");
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

const directNotificationsUrl=document.body.dataset.directNotificationsUrl;
if(directNotificationsUrl){
  const alerts=document.querySelector("[data-direct-alerts]");
  const badge=document.querySelector("[data-direct-count]");
  const activeChat=document.querySelector("[data-direct-peer]");
  const activePeer=Number(activeChat?.dataset.directPeer)||0;
  const viewingDirect=()=>{
    const log=activeChat?.querySelector("[data-chat-log]");
    return activePeer&&document.visibilityState==="visible"&&document.hasFocus()&&
      log&&log.scrollHeight-log.scrollTop-log.clientHeight<80;
  };
  let directPollBusy=false;
  let previousUnread="";
  const pollDirect=async()=>{
    if(directPollBusy)return;
    directPollBusy=true;
    try{
      const claim=document.visibilityState==="visible"&&document.hasFocus()?"1":"0";
      const response=await fetch(`${directNotificationsUrl}?claim=${claim}`,{headers:{Accept:"application/json"},cache:"no-store"});
      if(!response.ok||response.redirected)return;
      const payload=await response.json();
      const unread=payload.unread||[];
      const count=unread.reduce((total,item)=>total+item.count,0);
      badge.hidden=count===0;
      badge.textContent=count>99?"99+":String(count);
      badge.parentElement.setAttribute("aria-label",count?`Chat, ${count} unread direct messages`:"Chat");
      const signature=JSON.stringify(unread);
      if(signature!==previousUnread){
        previousUnread=signature;
        alerts.replaceChildren();
        for(const item of unread){
          const link=document.createElement("a");
          link.className="direct-alert";
          link.href=document.body.dataset.directBaseUrl.replace(/0$/,String(item.user_id));
          link.textContent=`DIRECT / ${item.username} [${item.callsign}] · ${item.count} unread`;
          link.setAttribute("aria-label",`${item.count} unread direct messages from ${item.username}. Open conversation.`);
          alerts.append(link);
        }
      }
      if((payload.alerts||[]).some(item=>item.user_id!==activePeer||!viewingDirect()))playSfx("newchat");
    }catch(_){}finally{directPollBusy=false}
  };
  pollDirect();
  setInterval(pollDirect,2000);
  window.addEventListener("focus",pollDirect);
  document.addEventListener("visibilitychange",()=>{if(!document.hidden)pollDirect()});
}

const clock=document.querySelector("[data-clock]");
const dateReadout=document.querySelector("[data-date]");
const stationHours=document.querySelector("[data-station-hours]");
if(stationHours){
  // Station hours arrive as UTC moments; show them in this visitor's time zone.
  const twelve=stationHours.dataset.clockFormat!=="24h";
  const local=value=>{const moment=new Date(value);if(Number.isNaN(moment.getTime()))return null;const hour=moment.getHours(),minute=String(moment.getMinutes()).padStart(2,"0");return twelve?`${hour%12||12}:${minute}${hour<12?"AM":"PM"}`:`${String(hour).padStart(2,"0")}:${minute}`};
  const opens=local(stationHours.dataset.opens),closes=local(stationHours.dataset.closes);
  if(opens&&closes){const original=stationHours.textContent;const shown=`${opens} - ${closes}`;stationHours.textContent=shown;if(shown!==original)stationHours.title=`Your local time. Station time: ${original}`}
}
if(clock){const tick=()=>{const now=new Date();clock.textContent=now.toLocaleTimeString([],{hour12:false});if(dateReadout)dateReadout.textContent=now.toLocaleDateString([],{weekday:"short",month:"short",day:"2-digit",year:"numeric"}).toUpperCase()};tick();setInterval(tick,1000)}
const localTimestamp=value=>{
  if(!value)return "";
  const normalized=/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)?`${value.replace(" ","T")}Z`:value;
  const timestamp=new Date(normalized);
  return Number.isNaN(timestamp.getTime())?value:timestamp.toLocaleString([],{dateStyle:"medium",timeStyle:"short"});
};
for(const time of document.querySelectorAll("[data-local-time]"))time.textContent=localTimestamp(time.dateTime||time.textContent);
const reducedTaglineMotion=window.matchMedia("(prefers-reduced-motion: reduce)");
const taglineTimers=new WeakMap();
const taglineCharacters=value=>typeof Intl.Segmenter==="function"?[...new Intl.Segmenter(undefined,{granularity:"grapheme"}).segment(value)].map(part=>part.segment):Array.from(value);
const stopTagline=element=>{
  if(!element)return;
  clearInterval(taglineTimers.get(element));
  taglineTimers.delete(element);
};
const typeTagline=(element,value)=>{
  stopTagline(element);
  element.dataset.taglineFullText=value;
  element.textContent=value;
  if(reducedTaglineMotion.matches)return;
  const characters=taglineCharacters(value);
  element.parentElement.style.minHeight=`${element.parentElement.getBoundingClientRect().height}px`;
  element.textContent="";
  let position=0;
  const timer=setInterval(()=>{
    position=Math.min(position+1,characters.length);
    element.textContent=characters.slice(0,position).join("");
    if(position===characters.length){clearInterval(timer);taglineTimers.delete(element)}
  },Math.max(25,Math.min(70,2400/Math.max(characters.length,1))));
  taglineTimers.set(element,timer);
};
const scrambleTagline=(element,value)=>{
  stopTagline(element);
  element.dataset.taglineFullText=value;
  element.textContent=value;
  if(reducedTaglineMotion.matches)return;
  const characters=taglineCharacters(value);
  const glyphs="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
  let frame=0;
  const timer=setInterval(()=>{
    frame++;
    const resolved=Math.min(characters.length,Math.floor(frame/18*characters.length));
    element.textContent=characters.map((character,index)=>index<resolved||!/[A-Za-z0-9]/.test(character)?character:glyphs[Math.floor(Math.random()*glyphs.length)]).join("");
    if(frame>=18){element.textContent=value;stopTagline(element)}
  },80);
  taglineTimers.set(element,timer);
};
const renderTagline=(container,value,effect)=>{
  stopTagline(container.querySelector("[data-tagline-full-text]"));
  container.style.minHeight="";
  container.replaceChildren();
  const visible=document.createElement("span");
  visible.className="tagline-visible";
  visible.textContent=value;
  container.append(visible);
  if(["typewriter","scramble","wave","jitter"].includes(effect)){
    visible.setAttribute("aria-hidden","true");
    const readable=document.createElement("span");
    readable.className="tagline-screenreader";
    readable.textContent=value;
    container.append(readable);
  }
  if(effect==="typewriter")typeTagline(visible,value);
  else if(effect==="scramble")scrambleTagline(visible,value);
  else if((effect==="wave"||effect==="jitter")&&!reducedTaglineMotion.matches){
    const fragment=document.createDocumentFragment();
    taglineCharacters(value).forEach((character,index)=>{
      const span=document.createElement("span");
      span.className="tagline-char";
      span.style.setProperty("--char-index",index);
      span.textContent=character;
      fragment.append(span);
    });
    visible.replaceChildren(fragment);
  }
};
for(const tagline of document.querySelectorAll(".brand-tag[data-tagline-effect]")){
  const effect=tagline.dataset.taglineEffect;
  if(["typewriter","scramble","wave","jitter"].includes(effect)){
    const value=tagline.querySelector(".tagline-visible")?.textContent||"";
    renderTagline(tagline,value,effect);
  }
}
reducedTaglineMotion.addEventListener?.("change",()=>{
  if(reducedTaglineMotion.matches){
    for(const element of document.querySelectorAll("[data-tagline-full-text]")){
      stopTagline(element);
      element.textContent=element.dataset.taglineFullText;
    }
    for(const element of document.querySelectorAll(".tagline-effect-wave .tagline-visible, .tagline-effect-jitter .tagline-visible")){
      if(element.querySelector(".tagline-char"))element.textContent=element.parentElement.querySelector(".tagline-screenreader")?.textContent||"";
    }
  }else{
    for(const tagline of document.querySelectorAll(".brand-tag[data-tagline-effect]")){
      const effect=tagline.dataset.taglineEffect;
      if(["typewriter","scramble","wave","jitter"].includes(effect)){
        const value=tagline.querySelector(".tagline-screenreader")?.textContent||"";
        renderTagline(tagline,value,effect);
      }
    }
  }
});
for(const editor of document.querySelectorAll("[data-tagline-editor]")){
  const list=editor.querySelector("[data-tagline-list]");
  const template=editor.querySelector("[data-tagline-template]");
  const output=editor.querySelector("[data-tagline-preview-output]");
  const style=document.querySelector("[data-tagline-preview-style]");
  const original=list.innerHTML;
  const preview=row=>{
    const value=row.querySelector("[data-tagline-text]").value;
    const effect=row.querySelector("[data-tagline-effect]").value;
    const mode=row.querySelector("[data-tagline-color-mode]").value;
    const color=row.querySelector("[data-tagline-color-value]").value;
    stopTagline(output.querySelector("[data-tagline-full-text]"));
    output.className=`brand-tag tagline-effect-${effect}${mode==="custom"?" tagline-preview-current":""}`;
    output.dataset.taglineEffect=effect;
    style.textContent=mode==="custom"?`.tagline-preview-current { color: ${color}; --tagline-ink: ${color}; }`:"";
    renderTagline(output,value,effect);
  };
  editor.querySelector("[data-tagline-add]").addEventListener("click",()=>{
    if(list.children.length>=32){editor.querySelector("[data-tagline-status]").textContent="A station can have up to 32 taglines.";return}
    const row=template.content.firstElementChild.cloneNode(true);
    list.append(row);
    editor.querySelector("[data-tagline-status]").textContent="Tagline added. Save to apply changes.";
    row.querySelector("[data-tagline-text]").focus();
  });
  list.addEventListener("click",event=>{
    const button=event.target.closest("button");
    const row=button?.closest("[data-tagline-row]");
    if(!row)return;
    if(button.matches("[data-tagline-preview-button]"))preview(row);
    else if(button.matches("[data-tagline-delete]")){row.remove();stopTagline(output.querySelector("[data-tagline-full-text]"));output.className="brand-tag";output.style.minHeight="";delete output.dataset.taglineEffect;output.textContent="Choose PREVIEW on a tagline to see its current text, color, and effect.";style.textContent=""}
    else if(button.matches("[data-tagline-up]")&&row.previousElementSibling)list.insertBefore(row,row.previousElementSibling);
    else if(button.matches("[data-tagline-down]")&&row.nextElementSibling)list.insertBefore(row.nextElementSibling,row);
  });
  list.addEventListener("input",event=>{
    if(!event.target.matches("[data-tagline-color-value]"))return;
    event.target.closest("[data-tagline-row]").querySelector("[data-tagline-color-mode]").value="custom";
  });
  editor.addEventListener("reset",()=>setTimeout(()=>{
    list.innerHTML=original;
    stopTagline(output.querySelector("[data-tagline-full-text]"));
    output.className="brand-tag";
    output.style.minHeight="";
    delete output.dataset.taglineEffect;
    output.textContent="Choose PREVIEW on a tagline to see its current text, color, and effect.";
    style.textContent="";
    editor.querySelector("[data-tagline-status]").textContent="Changes canceled.";
  }));
}
document.addEventListener("keydown",e=>{const editing=e.target instanceof HTMLInputElement||e.target instanceof HTMLTextAreaElement||e.target instanceof HTMLSelectElement||e.target?.isContentEditable;if(e.key==="/"&&!editing&&!e.ctrlKey&&!e.metaKey&&!e.altKey){e.preventDefault();const command=document.querySelector("[data-command-input]");if(command){command.focus();if(!command.value)command.value="/"}else document.querySelector("#service-nav")?.focus()}});
for(const selector of document.querySelectorAll("[data-theme-selector]")){
  const preview=selector.closest("form")?.querySelector(`[data-theme-preview="${selector.dataset.themeSelector}"]`);
  if(!preview)continue;
  const update=()=>{
    const id=selector.value||preview.dataset.inheritTheme||"cyan";
    preview.className=preview.className.replace(/theme-swatch--[a-z0-9-]+/g,"").trim()+` theme-swatch--${id}`;
  };
  selector.addEventListener("change",update);
  selector.form?.addEventListener("reset",()=>setTimeout(update));
}
for(const editor of document.querySelectorAll("[data-banner-editor]")){
  const text=editor.querySelector("[data-banner-text]");
  const font=editor.querySelector("[data-font-browser]");
  const size=editor.querySelector("[data-banner-size]");
  const search=editor.querySelector("[data-font-search]");
  const color=editor.querySelector("[data-banner-color]");
  const picker=editor.querySelector("[data-banner-color-picker]");
  const preset=editor.querySelector("[data-banner-color-preset]");
  const preview=editor.querySelector("[data-banner-preview]");
  const output=editor.querySelector("[data-banner-preview-output]");
  const status=editor.querySelector("[data-banner-preview-status]");
  const restore=editor.querySelector("[data-banner-restore]");
  if(!text||!font||!size||!color||!picker||!preset||!preview||!output)continue;
  const mastheadTheme=editor.querySelector('[data-theme-selector="masthead"]');
  const mastheadPreview=editor.querySelector('[data-theme-preview="masthead"]');
  const selectedThemeColor=()=>mastheadPreview?getComputedStyle(mastheadPreview).getPropertyValue("--swatch-masthead").trim()||"#00d8cf":"#00d8cf";
  let timer, controller;
  const setPreviewColor=()=>{
    preview.style.setProperty("--banner-preview-color",color.value||selectedThemeColor());
    if(mastheadPreview)preview.style.setProperty("--masthead-shadow",getComputedStyle(mastheadPreview).getPropertyValue("--swatch-shadow"));
  };
  const syncPreset=()=>{
    const matching=[...preset.options].find(option=>option.value===color.value);
    preset.value=matching?color.value:(color.value?"custom":"");
    picker.value=color.value||selectedThemeColor();
    setPreviewColor();
  };
  const render=()=>{
    clearTimeout(timer);
    timer=setTimeout(async()=>{
      if(!font.value||!size.checkValidity())return;
      controller?.abort();controller=new AbortController();
      status.textContent="Rendering preview…";
      const query=new URLSearchParams({text:text.value,font:font.value,size:size.value});
      try{
        const response=await fetch(`${editor.dataset.previewUrl}?${query}`,{headers:{Accept:"application/json"},cache:"no-store",signal:controller.signal});
        const payload=await response.json();
        if(!response.ok)throw new Error(payload.error||"Preview unavailable.");
        output.textContent=payload.banner;
        status.textContent="Matches the saved masthead renderer.";
      }catch(problem){if(problem.name!=="AbortError")status.textContent=problem.message||"Preview unavailable."}
    },220);
  };
  search?.addEventListener("input",()=>{
    const term=search.value.trim().toLocaleLowerCase();
    for(const option of font.options)option.hidden=!!term&&!option.value.toLocaleLowerCase().includes(term);
  });
  for(const control of [text,font,size])control.addEventListener("input",render);
  font.addEventListener("change",render);
  preset.addEventListener("change",()=>{
    if(preset.value!=="custom")color.value=preset.value;
    syncPreset();
  });
  picker.addEventListener("input",()=>{color.value=picker.value;syncPreset()});
  mastheadTheme?.addEventListener("change",()=>{color.value="";syncPreset()});
  restore?.addEventListener("click",()=>{
    text.value="";font.value="small";size.value="100";color.value="";syncPreset();render();
  });
  editor.addEventListener("reset",()=>setTimeout(()=>{syncPreset();render()}));
  syncPreset();
}
for(const form of document.querySelectorAll("form[data-confirm]"))form.addEventListener("submit",e=>{if(!confirm(form.dataset.confirm))e.preventDefault()});
for(const toggle of document.querySelectorAll("[data-feed-comment-toggle]"))toggle.addEventListener("click",()=>{
  const form=document.getElementById(toggle.getAttribute("aria-controls"));
  if(!form)return;
  const expanded=form.hidden;
  form.hidden=!expanded;
  toggle.setAttribute("aria-expanded",String(expanded));
  if(expanded)form.querySelector("textarea")?.focus();
});
for(const form of document.querySelectorAll('form[method="post"]:not([data-chat-form]):not([data-sfx-submit])'))form.addEventListener("submit",event=>{
  if(event.defaultPrevented||!form.checkValidity())return;
  const button=event.submitter;
  if(!(button instanceof HTMLButtonElement))return;
  if(button.dataset.submitting){event.preventDefault();return}
  button.dataset.submitting="true";
  button.setAttribute("aria-disabled","true");
  button.classList.add("is-submitting");
  window.addEventListener("pageshow",()=>{delete button.dataset.submitting;button.removeAttribute("aria-disabled");button.classList.remove("is-submitting")},{once:true});
});

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
  const toggle=panel.querySelector("[data-panel-toggle]");
  const content=panel.querySelector(":scope > [data-panel-content]");
  if(!toggle||!content)continue;
  const storageKey=`stationnet.panel.${panel.dataset.panelKey||content.id}`;
  let expanded=panel.closest(".mud-shell")
    ? (toggle.getAttribute("aria-expanded")==="true" || (panel.dataset.panelKey==="mud-inventory" && matchMedia("(min-width: 1180px)").matches))
    : true;
  try{
    const saved=localStorage.getItem(storageKey);
    if(saved!==null)expanded=saved!=="collapsed";
  }catch(_){}
  const setExpanded=(value,persist=true)=>{
    expanded=value;
    toggle.setAttribute("aria-expanded",String(expanded));
    content.hidden=!expanded;
    panel.classList.toggle("is-collapsed",!expanded);
    if(persist)try{localStorage.setItem(storageKey,expanded?"expanded":"collapsed")}catch(_){}
  };
  setExpanded(expanded,false);
  toggle.addEventListener("click",()=>setExpanded(!expanded));
}

for(const section of document.querySelectorAll("[data-activity-broadcasts]")){
  const toggle=section.querySelector("[data-activity-broadcast-toggle]");
  if(!toggle)continue;
  const broadcasts=section.querySelectorAll(".activity-broadcast[hidden]");
  const more=section.querySelector("[data-activity-broadcast-more]");
  toggle.addEventListener("click",()=>{
    const expanded=toggle.getAttribute("aria-expanded")!=="true";
    toggle.setAttribute("aria-expanded",String(expanded));
    toggle.setAttribute("aria-label",expanded?"Show fewer active broadcasts":"Show all active broadcasts");
    for(const broadcast of broadcasts)broadcast.hidden=!expanded;
    if(more)more.hidden=expanded;
  });
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
    if(log.querySelector(`[data-message-id="${message.id}"]`))return;
    const nearBottom=log.scrollHeight-log.scrollTop-log.clientHeight<80;
    chat.querySelector("[data-chat-empty]")?.remove();
    const row=document.createElement("div");
    row.className="chat-message"+(message.mine?" mine":"");
    row.dataset.messageId=String(message.id);
    const meta=document.createElement("span");
    meta.textContent=`${message.username} - [${message.callsign}] · ${localTimestamp(message.created_at)}`;
    const body=document.createElement("p");
    body.textContent=message.body;
    row.append(meta,body);
    if(chat.dataset.reportUrl||chat.dataset.deleteUrl){
      const menu=document.createElement("details");menu.className="moderation-actions";
      const summary=document.createElement("summary");summary.setAttribute("aria-label","Message actions");summary.title="Message actions";
      const arrow=document.createElement("span");arrow.setAttribute("aria-hidden","true");arrow.textContent="▾";summary.append(arrow);menu.append(summary);
      if(chat.dataset.reportUrl&&!message.mine){
        const report=document.createElement("button");report.type="button";report.textContent="REPORT";
        report.addEventListener("click",async()=>{
          const reason=prompt("Reason for reporting this message (at least 10 characters):");
          if(!reason)return;
          const data=new FormData();data.set("csrf_token",form.elements.csrf_token.value);data.set("target_type","chat");data.set("target_id",message.id);data.set("reason",reason);
          const response=await fetch(chat.dataset.reportUrl,{method:"POST",body:data});
          alert(response.ok?"Report sent to the sysops.":"Report could not be sent. Check the reason or try later.");
        });menu.append(report);
      }
      if(chat.dataset.deleteUrl){
        const remove=document.createElement("button");remove.type="button";remove.textContent="SYSOP DELETE";
        remove.addEventListener("click",async()=>{
          const reason=prompt("Reason for deleting this message:");if(!reason||!confirm("Delete this message?"))return;
          const data=new FormData();data.set("csrf_token",form.elements.csrf_token.value);data.set("reason",reason);
          const response=await fetch(chat.dataset.deleteUrl.replace("/0/delete",`/${message.id}/delete`),{method:"POST",body:data});
          if(response.ok)row.remove();else alert("Message could not be deleted.");
        });menu.append(remove);
      }
      row.append(menu);
    }
    log.append(row);
    lastId=Math.max(lastId,message.id);
    if(nearBottom||message.mine)log.scrollTop=log.scrollHeight;
  };

  const poll=async()=>{
    if(busy)return;
    busy=true;
    try{
      const read=chat.dataset.directPeer&&document.visibilityState==="visible"&&document.hasFocus()&&
        log.scrollHeight-log.scrollTop-log.clientHeight<80?"&read=1":"";
      const response=await fetch(`${url}?after=${lastId}${read}`,{headers:{"Accept":"application/json"},cache:"no-store"});
      if(response.status===401||response.redirected){location.reload();return}
      const payload=await response.json();
      if(!response.ok){error.textContent=payload.error||"CHAT UNAVAILABLE";form.querySelector("textarea").disabled=true;form.querySelector("button").disabled=true;return}
      if(error.textContent==="LINK RETRYING...")error.textContent="";
      const messages=payload.messages||[];
      if(payload.active!==false&&chat.dataset.closed){delete chat.dataset.closed;log.replaceChildren();lastId=0}
      for(const id of payload.deleted_ids||[])log.querySelector(`[data-message-id="${id}"]`)?.remove();
      for(const message of messages)addMessage(message);
      if(payload.muted){error.textContent="CHAT MUTED: you cannot send messages.";form.querySelector("textarea").disabled=true;form.querySelector("button").disabled=true}
      else if(payload.active!==false&&form.querySelector("textarea").disabled){error.textContent="";form.querySelector("textarea").disabled=false;form.querySelector("button").disabled=false}
      if(!chat.dataset.directPeer&&initialized&&messages.some(message=>!message.mine))playSfx("newchat");
      if(!initialized&&messages.length===0){const empty=chat.querySelector("[data-chat-empty]");if(empty)empty.textContent=chat.dataset.directPeer?"No messages in this direct chat yet.":"Quiet for now. Start the conversation below."}
      initialized=true;
      if(payload.active===false){
        error.textContent="LINK CLOSED: USER OFFLINE";
        form.querySelector("textarea").disabled=true;
        form.querySelector("button").disabled=true;
        if(chat.dataset.directPeer&&!chat.dataset.closed){chat.dataset.closed="true";log.replaceChildren();lastId=0;const closed=document.createElement("p");closed.className="muted";closed.textContent="This direct chat ended when the user went offline.";log.append(closed)}
      }
    }catch(_){error.textContent="LINK RETRYING..."}
    finally{busy=false}
  };

  form.addEventListener("submit",async event=>{
    event.preventDefault();
    error.textContent="";
    const button=form.querySelector("button");
    const buttonLabel=button.textContent;
    button.disabled=true;
    button.textContent="SENDING...";
    form.setAttribute("aria-busy","true");
    try{
      const response=await fetch(url,{method:"POST",body:new FormData(form),headers:{"Accept":"application/json"}});
      const payload=await response.json();
      if(!response.ok)throw new Error(payload.error||"Transmission failed.");
      form.querySelector("textarea").value="";
      playSfx("sendmessage");
      await poll();
    }catch(problem){error.textContent=problem.message}
    finally{button.textContent=buttonLabel;form.removeAttribute("aria-busy");if(!form.querySelector("textarea").disabled)button.disabled=false;form.querySelector("textarea").focus()}
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

