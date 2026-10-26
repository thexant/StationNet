"use strict";

for(const mud of document.querySelectorAll("[data-mud]")){
  const events=mud.querySelector("[data-mud-events]");
  const gameScroll=mud.querySelector("[data-mud-game-scroll]");
  const roomView=mud.querySelector("[data-mud-room-view]");
  const latestButton=mud.querySelector("[data-mud-jump-latest]");
  const roomButton=mud.querySelector("[data-mud-jump-room]");
  const clearButton=mud.querySelector("[data-mud-clear-events]");
  const form=mud.querySelector("[data-mud-command]");
  const input=form.querySelector("[data-command-input]");
  const error=mud.querySelector("[data-mud-error]");
  const roomName=mud.querySelector("[data-mud-room-name]");
  const roomPreview=mud.querySelector("[data-mud-room-preview]");
  const sceneName=mud.querySelector("[data-mud-scene-name]");
  const roomDescription=mud.querySelector("[data-mud-room-description]");
  const roomInstructions=mud.querySelector("[data-mud-room-instructions]");
  const roomExits=mud.querySelector("[data-mud-room-exits]");
  const roomItems=mud.querySelector("[data-mud-room-items]");
  const roomNpcs=mud.querySelector("[data-mud-room-npcs]");
  const combat=mud.querySelector("[data-mud-combat]");
  const occupants=mud.querySelector("[data-mud-occupants] > div");
  const characterName=mud.querySelector("[data-character-name]");
  const characterLevel=mud.querySelector("[data-character-level]");
  const characterTrack=mud.querySelector("[data-character-track]");
  const characterOrigin=mud.querySelector("[data-character-origin]");
  const characterSkills=mud.querySelector("[data-character-skills]");
  const characterMode=mud.querySelector("[data-character-mode]");
  const characterHealth=mud.querySelector("[data-character-health]");
  const characterHealthBar=mud.querySelector("[data-character-health-bar]");
  const characterExperience=mud.querySelector("[data-character-experience]");
  const characterExperienceBar=mud.querySelector("[data-character-experience-bar]");
  const characterExperienceRemaining=mud.querySelector("[data-character-experience-remaining]");
  const carried=mud.querySelector("[data-mud-carried]");
  const equipped=mud.querySelector("[data-mud-equipped]");
  const inventoryScope=mud.querySelector("[data-mud-inventory-scope]");
  const hubPanel=mud.querySelector("[data-mud-hub-panel]");
  const hubCoins=mud.querySelector("[data-mud-coins]");
  const hubShop=mud.querySelector("[data-mud-shop-items]");
  const hubInventory=mud.querySelector("[data-mud-hub-inventory]");
  const hubStored=mud.querySelector("[data-mud-stored]");
  const quests=mud.querySelector("[data-mud-quests]");
  const questCount=mud.querySelector("[data-mud-quest-count]");
  let busy=false, pollBusy=false;
  let clearedThroughId=Number(mud.dataset.clearedThrough)||0;
  let lastId=Math.max(clearedThroughId,...Array.from(events.querySelectorAll("[data-event-id]"),row=>Number(row.dataset.eventId)||0));

  const atLatest=()=>gameScroll.scrollHeight-gameScroll.scrollTop-gameScroll.clientHeight<48;
  const showRoom=open=>{
    roomView.hidden=!open;
    gameScroll.inert=open;
    gameScroll.setAttribute("aria-hidden",String(open));
    roomButton.setAttribute("aria-expanded",String(open));
    roomButton.setAttribute("aria-label",open?"Return to gameplay activity":"Show current room details");
    roomButton.textContent=open?"BACK":"ROOM";
    if(open)roomView.scrollTop=0;
    else if(atLatest())latestButton.classList.remove("is-unread");
  };
  const scrollEventsToLatest=()=>{
    showRoom(false);
    gameScroll.scrollTop=gameScroll.scrollHeight;
    latestButton.classList.remove("is-unread");
  };
  const requestId=()=>globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}`;
  let clientId;
  try{
    clientId=sessionStorage.getItem("stationnet.mud.client");
    if(!clientId){clientId=requestId();sessionStorage.setItem("stationnet.mud.client",clientId)}
  }catch(_){clientId=requestId()}

  const appendEvent=event=>{
    lastId=Math.max(lastId,Number(event.id)||0);
    if(event.id&&Number(event.id)<=clearedThroughId)return;
    if(event.id&&events.querySelector(`[data-event-id="${event.id}"]`))return;
    const follow=atLatest();
    events.querySelector("[data-mud-empty-activity]")?.remove();
    const row=document.createElement("article");
    row.className=`mud-event mud-event-${event.event_type||"system"}`;
    if(event.id)row.dataset.eventId=event.id;
    const type=document.createElement("span");
    type.textContent=(event.event_type||"system").toUpperCase();
    const body=document.createElement("p");
    body.textContent=event.body;
    row.append(type,body);
    events.append(row);
    while(events.querySelectorAll("[data-event-id]").length>200){
      events.querySelector("[data-event-id]")?.remove();
    }
    if(follow&&roomView.hidden)scrollEventsToLatest();
    else latestButton.classList.add("is-unread");
  };
  const renderRoom=room=>{
    if(!room)return;
    roomName.textContent=room.name||"Unknown Location";
    roomPreview.textContent=room.description||"";
    sceneName.textContent=room.name||"Unknown Location";
    roomDescription.textContent=room.description||"";
    if(roomInstructions){roomInstructions.textContent=room.instructions||"";roomInstructions.hidden=!room.instructions}
    roomExits.textContent=(room.exits||[]).map(exit=>exit.direction||exit.name).join(", ")||"none";
    if(roomItems)roomItems.textContent=(room.items||[]).map(item=>item.display_name||item.name).join(", ")||"none";
    if(roomNpcs)roomNpcs.textContent=(room.npcs||[]).map(npc=>npc.display_name||npc.name).join(", ")||"none";
    occupants.replaceChildren();
    if(!room.occupants?.length){
      const empty=document.createElement("span");
      empty.className="muted";empty.dataset.mudNoOccupants="";empty.textContent="none";
      occupants.append(empty);
    }else for(const player of room.occupants){
      const entity=document.createElement("span");
      entity.className="mud-entity";
      entity.dataset.playerId=player.id;
      entity.dataset.characterId=player.character_id||"";
      entity.textContent=player.character_name||"Adventurer";
      occupants.append(entity);
    }
  };
  const renderCharacter=character=>{
    if(!character)return;
    characterName.textContent=character.name;
    characterLevel.textContent=`LEVEL ${character.level}`;
    characterTrack.textContent=character.progression_label;
    if(characterOrigin)characterOrigin.textContent=character.race?`${character.race} · ${character.class}`:"";
    if(characterSkills)characterSkills.textContent=(character.skills||[]).map(skill=>`${skill.name} ${skill.rank}`).join(" · ");
    if(characterMode)characterMode.textContent=`${character.mode?.replace(/^./,letter=>letter.toUpperCase())||"Current"} mode`;
    characterHealth.textContent=`${character.health} / ${character.maximum_health}`;
    characterHealthBar.max=character.maximum_health;
    characterHealthBar.value=character.health;
    characterExperience.textContent=`${character.experience_into_level} / ${character.experience_for_next_level}`;
    characterExperienceBar.max=character.experience_for_next_level;
    characterExperienceBar.value=character.experience_into_level;
    characterExperienceRemaining.textContent=`${character.experience_to_next_level} XP to next level`;
    for(const [name,value] of Object.entries(character.attributes||{})){
      const output=mud.querySelector(`[data-character-attribute="${name}"]`);
      if(output)output.textContent=value;
    }
  };
  const renderCombat=state=>{
    if(!combat)return;
    combat.replaceChildren();
    for(const enemy of state?.enemies||[]){
      const row=document.createElement("article");
      row.className=`mud-enemy ${enemy.status==="defeated"?"defeated":""}`.trim();
      const heading=document.createElement("span");
      const name=document.createElement("strong");name.textContent=enemy.display_name||enemy.name;
      const status=document.createElement("small");status.textContent=(enemy.status||"ready").toUpperCase();
      heading.append(name,status);
      const health=document.createElement("progress");health.max=enemy.maximum_health;health.value=enemy.health;
      const output=document.createElement("output");output.textContent=`${enemy.health} / ${enemy.maximum_health} HP`;
      row.append(heading,health,output);combat.append(row);
    }
  };
  const renderInventory=inventory=>{
    if(!inventory||!carried||!equipped)return;
    if(inventoryScope)inventoryScope.textContent=inventory.sharing==="shared"?"Shared across modes":`${inventory.mode?.replace(/^./,letter=>letter.toUpperCase())||"Current mode"} only`;
    const fill=(node,items,slot)=>{
      node.replaceChildren();
      if(!items?.length){const empty=document.createElement("span");empty.className="muted";empty.textContent=slot?"nothing equipped":"nothing carried";node.append(empty);return}
      for(const item of items){const label=document.createElement("span");label.textContent=`${slot?`${(item.equipped_slot||"equipment").replaceAll("_"," ")}: `:""}${item.display_name||item.name}${item.acquired_run_id?" · RUN LOOT":""}`;node.append(label)}
    };
    fill(carried,inventory.inventory,false);fill(equipped,inventory.equipment,true);
    if(hubInventory){
      hubInventory.replaceChildren();
      for(const item of [...inventory.inventory,...inventory.equipment]){
        const label=document.createElement("span");label.textContent=item.name;hubInventory.append(label);
      }
      if(!hubInventory.childElementCount)hubInventory.textContent="nothing carried";
    }
  };
  const renderHub=hub=>{
    if(!hubPanel)return;
    hubPanel.hidden=!hub;
    if(!hub)return;
    hubCoins.textContent=hub.coins;
    hubShop.replaceChildren();
    for(const item of hub.shop||[]){
      const label=document.createElement("span");label.textContent=`${item.name} · ${item.price} coins`;hubShop.append(label);
    }
    hubStored.replaceChildren();
    for(const item of hub.storage||[]){
      const label=document.createElement("span");label.textContent=item.name;hubStored.append(label);
    }
    if(!hubStored.childElementCount)hubStored.textContent="empty";
  };
  const renderQuests=state=>{
    if(!quests||!state)return;
    const active=state.active||[];
    if(questCount)questCount.textContent=active.length;
    quests.replaceChildren();
    if(!active.length){const empty=document.createElement("span");empty.className="muted";empty.textContent="no active quests";quests.append(empty);return}
    for(const quest of active){
      const article=document.createElement("article");
      const title=document.createElement("strong");title.textContent=quest.name;
      const description=document.createElement("p");description.textContent=quest.description;
      const list=document.createElement("ul");
      for(const objective of quest.objectives||[]){const row=document.createElement("li");if(objective.completed_at)row.className="complete";row.append(`${objective.completed_at?"✓":"·"} ${objective.description} `);const progress=document.createElement("span");progress.textContent=`${objective.progress}/${objective.required_count}`;row.append(progress);list.append(row)}
      article.append(title,description,list);quests.append(article);
    }
  };
  const setBusy=value=>{
    busy=value;
    form.querySelector("button").disabled=value;
    clearButton.disabled=value;
  };
  const clearEvents=async()=>{
    if(busy)return;
    setBusy(true);
    error.textContent="";
    const data=new FormData();
    data.set("csrf_token",mud.dataset.csrf);
    try{
      const response=await fetch(mud.dataset.clearUrl,{method:"POST",body:data,headers:{"Accept":"application/json"}});
      if(response.status===401||response.redirected){location.reload();return}
      const payload=await response.json();
      if(!response.ok)throw new Error(payload.error||"Could not clear gameplay activity.");
      clearedThroughId=Math.max(clearedThroughId,Number(payload.cleared_through_id)||0);
      lastId=Math.max(lastId,clearedThroughId);
      const recent=Array.from(events.querySelectorAll("[data-event-id]"))
        .filter(row=>Number(row.dataset.eventId)>clearedThroughId);
      events.replaceChildren(...recent);
      if(!recent.length){
        const empty=document.createElement("p");
        empty.className="mud-empty-activity";
        empty.dataset.mudEmptyActivity="";
        empty.textContent="Activity cleared. New events will appear here.";
        events.append(empty);
      }
      scrollEventsToLatest();
    }catch(problem){error.textContent=problem.message||"MUD link interrupted."}
    finally{setBusy(false)}
  };
  const dispatch=async fields=>{
    if(busy)return false;
    setBusy(true);
    error.textContent="";
    const data=new FormData();
    data.set("csrf_token",mud.dataset.csrf);
    data.set("request_id",requestId());
    data.set("client_id",clientId);
    for(const [key,value] of Object.entries(fields))data.set(key,value);
    try{
      const response=await fetch(mud.dataset.actionUrl,{method:"POST",body:data,headers:{"Accept":"application/json"}});
      if(response.status===401||response.redirected){location.reload();return false}
      const payload=await response.json();
      renderRoom(payload.room);
      renderCharacter(payload.character);
      renderInventory(payload.inventory);
      renderHub(payload.hub);
      renderQuests(payload.quests);
      renderCombat(payload.combat);
      if(!response.ok)throw new Error(payload.error||"Action rejected by the station.");
      for(const event of payload.events||[])appendEvent(event);
      return true;
    }catch(problem){error.textContent=problem.message||"MUD link interrupted.";return false}
    finally{setBusy(false)}
  };
  const poll=async()=>{
    if(pollBusy||busy)return;
    pollBusy=true;
    try{
      const url=new URL(mud.dataset.updatesUrl,location.href);
      url.searchParams.set("after",lastId);
      url.searchParams.set("client_id",clientId);
      const response=await fetch(url,{headers:{Accept:"application/json"},cache:"no-store"});
      if(response.status===401||response.redirected){location.reload();return}
      const payload=await response.json();
      if(!response.ok)throw new Error(payload.error||"MUD link interrupted.");
      renderRoom(payload.room);
      renderCharacter(payload.character);
      renderInventory(payload.inventory);
      renderHub(payload.hub);
      renderQuests(payload.quests);
      renderCombat(payload.combat);
      for(const event of payload.events||[])appendEvent(event);
      if(error.textContent==="LINK RETRYING...")error.textContent="";
    }catch(problem){error.textContent=problem.message||"LINK RETRYING..."}
    finally{pollBusy=false}
  };

  roomButton.addEventListener("click",()=>showRoom(roomView.hidden));
  latestButton.addEventListener("click",scrollEventsToLatest);
  clearButton.addEventListener("click",clearEvents);
  gameScroll.addEventListener("scroll",()=>{if(roomView.hidden&&atLatest())latestButton.classList.remove("is-unread")},{passive:true});
  mud.addEventListener("keydown",event=>{if(event.key==="Escape"&&!roomView.hidden)showRoom(false)});
  form.addEventListener("submit",async event=>{
    event.preventDefault();
    const text=input.value.trim();
    if(!text)return;
    const sent=await dispatch(text.startsWith("/")?{command:text}:{message:text});
    if(sent)input.value="";
    input.focus();
  });
  addEventListener("pagehide",()=>{
    const data=new FormData();
    data.set("csrf_token",mud.dataset.csrf);data.set("client_id",clientId);
    navigator.sendBeacon?.(mud.dataset.disconnectUrl,data);
  });
  requestAnimationFrame(scrollEventsToLatest);
  addEventListener("load",scrollEventsToLatest,{once:true});
  poll();
  setInterval(poll,2000);
}
