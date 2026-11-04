"use strict";
import {commandSuggestions, historyStep} from "./mud_suggestions.mjs";

for(const mud of document.querySelectorAll("[data-mud]")){
  const events=mud.querySelector("[data-mud-events]");
  const gameScroll=mud.querySelector("[data-mud-game-scroll]");
  const roomView=mud.querySelector("[data-mud-room-view]");
  const latestButton=mud.querySelector("[data-mud-jump-latest]");
  const roomButton=mud.querySelector("[data-mud-jump-room]");
  const clearButton=mud.querySelector("[data-mud-clear-events]");
  const form=mud.querySelector("[data-mud-command]");
  const input=form.querySelector("[data-command-input]");
  const suggestions=form.querySelector("[data-mud-suggestions]");
  const initialState=JSON.parse(mud.querySelector("[data-mud-initial-state]").textContent);
  const catalog=initialState.catalog;
  const suggestionState={room:initialState.room,combat:initialState.combat,inventory:initialState.inventory,hub:initialState.hub};
  const commandHistory=[];
  let historyIndex=-1, historyDraft="", activeSuggestion=0, suggestionItems=[];
  const error=mud.querySelector("[data-mud-error]");
  const roomName=mud.querySelector("[data-mud-room-name]");
  const roomPreview=mud.querySelector("[data-mud-room-preview]");
  const travelStatus=mud.querySelector("[data-mud-travel]");
  const wayfindingStatus=mud.querySelector("[data-mud-wayfinding]");
  const sceneName=mud.querySelector("[data-mud-scene-name]");
  const roomDescription=mud.querySelector("[data-mud-room-description]");
  const roomInstructions=mud.querySelector("[data-mud-room-instructions]");
  const roomExits=mud.querySelector("[data-mud-room-exits]");
  const roomItems=mud.querySelector("[data-mud-room-items]");
  const roomNpcs=mud.querySelector("[data-mud-room-npcs]");
  const roomGathering=mud.querySelector("[data-mud-room-gathering]");
  const combat=mud.querySelector("[data-mud-combat]");
  const occupants=mud.querySelector("[data-mud-occupants] > div");
  const characterName=mud.querySelector("[data-character-name]");
  const characterLevel=mud.querySelector("[data-character-level]");
  const characterTrack=mud.querySelector("[data-character-track]");
  const characterOrigin=mud.querySelector("[data-character-origin]");
  const characterOriginFeatures=mud.querySelector("[data-character-origin-features]");
  const characterSkills=mud.querySelector("[data-character-skills]");
  const characterMode=mud.querySelector("[data-character-mode]");
  const characterHealth=mud.querySelector("[data-character-health]");
  const characterHealthBar=mud.querySelector("[data-character-health-bar]");
  const characterExperience=mud.querySelector("[data-character-experience]");
  const characterExperienceBar=mud.querySelector("[data-character-experience-bar]");
  const characterExperienceRemaining=mud.querySelector("[data-character-experience-remaining]");
  const characterMana=mud.querySelector("[data-character-mana]");
  const characterManaBar=mud.querySelector("[data-character-mana-bar]");
  const characterManaMeter=mud.querySelector("[data-character-mana-meter]");
  const characterManaLabel=mud.querySelector("[data-character-mana-label]");
  const characterPoints=mud.querySelector("[data-character-points]");
  const carried=mud.querySelector("[data-mud-carried]");
  const equipped=mud.querySelector("[data-mud-equipped]");
  const hubPanel=mud.querySelector("[data-mud-hub-panel]");
  const hubCoins=mud.querySelector("[data-mud-coins]");
  const hubShop=mud.querySelector("[data-mud-shop-items]");
  const hubInventory=mud.querySelector("[data-mud-hub-inventory]");
  const hubStored=mud.querySelector("[data-mud-stored]");
  const quests=mud.querySelector("[data-mud-quests]");
  const standingPanel=mud.querySelector("[data-mud-standing-panel]");
  const standingList=mud.querySelector("[data-mud-standing]");
  const questCount=mud.querySelector("[data-mud-quest-count]");
  let busy=false, pollBusy=false;
  let showingDeath=mud.dataset.deathSummary==="true";
  let clearedThroughId=Number(mud.dataset.clearedThrough)||0;
  let lastId=Math.max(clearedThroughId,...Array.from(events.querySelectorAll("[data-event-id]"),row=>Number(row.dataset.eventId)||0));

  const hideSuggestions=()=>{
    suggestions.hidden=true;
    suggestions.replaceChildren();
    suggestionItems=[];
    input.setAttribute("aria-expanded","false");
    input.removeAttribute("aria-activedescendant");
  };
  const fillSuggestion=command=>{
    input.value=command;
    hideSuggestions();
    historyIndex=-1;
    input.focus({preventScroll:true});
    input.setSelectionRange(command.length,command.length);
  };
  const refreshSuggestions=()=>{
    const next=commandSuggestions(input.value,suggestionState,catalog);
    if(!suggestions.hidden&&next.length===suggestionItems.length&&
       next.every((item,index)=>item.command===suggestionItems[index].command&&item.detail===suggestionItems[index].detail))return;
    suggestionItems=next;
    suggestions.replaceChildren();
    if(!suggestionItems.length){hideSuggestions();return}
    activeSuggestion=Math.min(activeSuggestion,suggestionItems.length-1);
    suggestionItems.forEach((item,index)=>{
      const option=document.createElement("button");
      option.type="button";
      option.id=`mud-command-option-${index}`;
      option.className=`mud-suggestion${index===activeSuggestion?" is-active":""}`;
      option.setAttribute("role","option");
      option.setAttribute("aria-selected",String(index===activeSuggestion));
      const command=document.createElement("span");command.textContent=item.command;
      const detail=document.createElement("small");detail.textContent=item.detail;
      option.append(command,detail);
      option.addEventListener("mousedown",event=>event.preventDefault());
      option.addEventListener("click",()=>fillSuggestion(item.command));
      suggestions.append(option);
    });
    suggestions.hidden=false;
    input.setAttribute("aria-expanded","true");
    input.setAttribute("aria-activedescendant",`mud-command-option-${activeSuggestion}`);
  };
  const updateSuggestionState=payload=>{
    for(const key of ["room","combat","inventory","hub"]){
      if(Object.hasOwn(payload,key))suggestionState[key]=payload[key];
    }
    if(!suggestions.hidden)refreshSuggestions();
  };

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
  if(showingDeath)showRoom(true);
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
    body.textContent=window.StationNet?.localizeText?window.StationNet.localizeText(event.body):event.body;
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
    const death=room.id==="stationnet:death-summary";
    if(death!==showingDeath){showingDeath=death;showRoom(death)}
    const displayRoomName=`${room.name||"Unknown Location"}${room.is_dungeon_exit?" [EXIT]":""}`;
    roomName.textContent=displayRoomName;
    roomPreview.textContent=room.description||"";
    if(travelStatus){
      travelStatus.hidden=!room.travel;
      travelStatus.textContent=room.travel?`TRAVELLING ${room.travel.direction.toUpperCase()} · ${room.travel.steps} ROOM${room.travel.steps===1?"":"S"} · /stop`:"";
    }
    if(wayfindingStatus){
      const way=room.wayfinding;
      wayfindingStatus.hidden=!way;
      wayfindingStatus.textContent=way?`TOWARD ${way.destination.toUpperCase()}${way.direction?` · ${way.direction.toUpperCase()}`:""} · /directions`:"";
      wayfindingStatus.title=way?.heading||"";
    }
    sceneName.textContent=displayRoomName;
    roomDescription.textContent=room.description||"";
    if(roomInstructions){roomInstructions.textContent=room.instructions||"";roomInstructions.hidden=!room.instructions}
    roomExits.textContent=(room.exits||[]).map(exit=>exit.direction||exit.name).join(", ")||"none";
    if(roomItems)roomItems.textContent=(room.items||[]).map(item=>item.display_name||item.name).join(", ")||"none";
    if(roomNpcs)roomNpcs.textContent=(room.npcs||[]).map(npc=>`${npc.display_name||npc.name}${(npc.service_labels||[]).map(label=>` [${label}]`).join("")}`).join(", ")||"none";
    if(roomGathering)roomGathering.textContent=(room.gathering||[]).map(node=>`${node.display_name||node.name} (${node.activity}${node.available?"":", replenishing"})`).join(", ")||"none";
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
      entity.textContent=player.display_name||player.character_name||"Adventurer";
      occupants.append(entity);
    }
  };
  const renderCharacter=character=>{
    if(!character)return;
    characterName.textContent=character.name;
    characterLevel.textContent=`LEVEL ${character.level}`;
    characterTrack.textContent=character.progression_label;
    if(characterOrigin)characterOrigin.textContent=character.race?`${character.race} · ${character.class}`:"";
    if(characterOriginFeatures)characterOriginFeatures.replaceChildren(...(character.origin?.summary||[]).map(line=>{
      const item=document.createElement("li");item.textContent=line;return item;
    }));
    if(characterSkills){
      const points=characterSkills.querySelector("[data-character-skill-points]");
      points.hidden=!character.skill_points;
      points.textContent=`${character.skill_points} skill point${character.skill_points===1?"":"s"} — use /skills`;
      characterSkills.querySelector("ul").replaceChildren(...(character.skills||[]).map(skill=>{
        const row=document.createElement("li");
        const label=document.createElement("span");
        const bar=document.createElement("progress");
        const maxed=skill.rank>=skill.max_rank;
        row.className=`mud-skill${skill.active?"":" inactive"}`;
        row.title=skill.active?skill.effects:"Inactive on this station";
        label.textContent=`${skill.name} ${skill.rank}`;
        bar.max=maxed?1:skill.next_rank_at;
        bar.value=maxed?1:skill.experience;
        row.append(label,bar);
        return row;
      }));
    }
    if(characterMode)characterMode.textContent=`${character.mode?.replace(/^./,letter=>letter.toUpperCase())||"Current"} mode`;
    characterHealth.textContent=`${character.health} / ${character.maximum_health}`;
    characterHealthBar.max=character.maximum_health;
    characterHealthBar.value=character.health;
    if(characterMana){
      // Worlds may rename the resource or go without it; procedural mode always shows mana.
      characterManaMeter.hidden=!character.resource.enabled;
      characterManaLabel.textContent=character.resource.name.toUpperCase();
      characterMana.textContent=`${character.mana} / ${character.maximum_mana}`;
      characterManaBar.max=character.maximum_mana;
      characterManaBar.value=character.mana;
    }
    if(character.at_max_level){
      characterExperience.textContent=`${character.experience}`;
      characterExperienceBar.max=1;
      characterExperienceBar.value=1;
      characterExperienceRemaining.textContent="MAX LEVEL";
    }else{
      characterExperience.textContent=`${character.experience_into_level} / ${character.experience_for_next_level}`;
      characterExperienceBar.max=character.experience_for_next_level;
      characterExperienceBar.value=character.experience_into_level;
      characterExperienceRemaining.textContent=`${character.experience_to_next_level} XP to next level`;
    }
    if(characterPoints){
      const points=character.attribute_points||0;
      characterPoints.hidden=!points;
      characterPoints.textContent=`${points} attribute point${points===1?"":"s"} to spend — use /train`;
    }
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
    const fill=(node,items,slot)=>{
      node.replaceChildren();
      if(!items?.length){const empty=document.createElement("span");empty.className="muted";empty.textContent=slot?"nothing equipped":"nothing carried";node.append(empty);return}
      for(const item of items){const label=document.createElement("span");const ammo=item.loaded_rounds!==undefined?` · ${item.loaded_rounds}/${item.magazine_size} loaded`:item.rounds_remaining!==undefined?` · ${item.rounds_remaining} rounds`:"";label.textContent=`${slot?`${(item.equipped_slot||"equipment").replaceAll("_"," ")}: `:""}${item.display_name||item.name}${slot&&item.requirements_met===false?" · REQUIREMENTS UNMET":""}${ammo}${item.acquired_run_id?" · RUN LOOT":""}`;node.append(label)}
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
  const questMarks={done:"✓",failed:"✗",locked:"🔒"};
  const questLine=(className,text)=>{const line=document.createElement("p");line.className=className;line.textContent=text;return line};
  const renderQuests=state=>{
    if(!quests||!state)return;
    const active=state.active||[];const failed=state.failed||[];
    if(questCount)questCount.textContent=active.length;
    quests.replaceChildren();
    if(!active.length){const empty=document.createElement("span");empty.className="muted";empty.textContent="no active quests";quests.append(empty)}
    for(const quest of active){
      const article=document.createElement("article");
      const title=document.createElement("strong");title.textContent=quest.name;
      article.append(title,questLine("",quest.description));
      if(quest.summary)article.append(questLine("mud-quest-goal",`Goal: ${quest.summary}`));
      if(quest.time_left)article.append(questLine("mud-quest-timer",`Time left: ${quest.time_left}`));
      if(quest.ready)article.append(questLine("mud-quest-ready",`READY — return to ${quest.turn_in_npc_name}`));
      const list=document.createElement("ul");
      for(const objective of quest.objectives||[]){
        const state=objective.state||(objective.completed_at?"done":objective.locked?"locked":"current");
        const row=document.createElement("li");row.className=state==="done"?"complete":state==="current"?"":state;
        row.append(`${questMarks[state]||"·"} ${objective.description}${objective.optional?" (optional)":""}${objective.resolves?" (another way)":""} `);
        const progress=document.createElement("span");progress.textContent=`${objective.progress}/${objective.required_count}`;row.append(progress);list.append(row)
      }
      article.append(list);quests.append(article);
    }
    if(failed.length){
      const heading=document.createElement("h3");heading.textContent="FAILED";quests.append(heading);
      for(const quest of failed){const article=document.createElement("article");const title=document.createElement("strong");title.textContent=quest.name;article.append(title,questLine("mud-quest-failed",quest.ending||"Failed."));quests.append(article)}
    }
  };
  const renderStanding=factions=>{
    if(!standingPanel||!standingList||!Array.isArray(factions))return;
    standingPanel.hidden=!factions.length;
    standingList.replaceChildren(...factions.map(faction=>{
      const row=document.createElement("p");
      row.textContent=`${faction.name}: ${faction.rank} (${faction.reputation>=0?"+":""}${faction.reputation})${faction.member?" · member":""}`;
      return row;
    }));
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
      if(payload.compatibility_url){location.assign(payload.compatibility_url);return false}
      updateSuggestionState(payload);
      renderRoom(payload.room);
      renderCharacter(payload.character);
      renderInventory(payload.inventory);
      renderHub(payload.hub);
      renderQuests(payload.quests);
      renderStanding(payload.standing);
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
      if(payload.compatibility_url){location.assign(payload.compatibility_url);return}
      if(!response.ok)throw new Error(payload.error||"MUD link interrupted.");
      updateSuggestionState(payload);
      renderRoom(payload.room);
      renderCharacter(payload.character);
      renderInventory(payload.inventory);
      renderHub(payload.hub);
      renderQuests(payload.quests);
      renderStanding(payload.standing);
      renderCombat(payload.combat);
      for(const event of payload.events||[])appendEvent(event);
      if(error.textContent==="LINK RETRYING...")error.textContent="";
    }catch(problem){error.textContent=problem.message||"LINK RETRYING..."}
    finally{pollBusy=false}
  };

  roomButton.addEventListener("click",()=>showRoom(roomView.hidden));
  latestButton.addEventListener("click",scrollEventsToLatest);
  clearButton.addEventListener("click",clearEvents);
  document.addEventListener("pointerdown",event=>{
    if(event.target!==input&&!suggestions.contains(event.target))hideSuggestions();
  });
  gameScroll.addEventListener("scroll",()=>{if(roomView.hidden&&atLatest())latestButton.classList.remove("is-unread")},{passive:true});
  mud.addEventListener("keydown",event=>{if(event.key==="Escape"&&!roomView.hidden)showRoom(false)});
  input.addEventListener("input",()=>{
    historyIndex=-1;
    historyDraft="";
    activeSuggestion=0;
    refreshSuggestions();
  });
  input.addEventListener("keydown",event=>{
    if(event.isComposing||event.ctrlKey||event.altKey||event.metaKey)return;
    if(event.key==="Tab"&&!suggestions.hidden&&suggestionItems.length){
      event.preventDefault();
      fillSuggestion(suggestionItems[activeSuggestion].command);
    }else if(event.key==="ArrowUp"||event.key==="ArrowDown"){
      if(!commandHistory.length)return;
      event.preventDefault();
      if(historyIndex===-1&&event.key==="ArrowUp")historyDraft=input.value;
      const next=historyStep(commandHistory,historyIndex,historyDraft,event.key==="ArrowUp"?1:-1);
      historyIndex=next.index;
      input.value=next.value;
      input.setSelectionRange(input.value.length,input.value.length);
      activeSuggestion=0;
      hideSuggestions();
    }else if(event.key==="Escape"&&!suggestions.hidden){
      event.stopPropagation();
      hideSuggestions();
    }
  });
  form.addEventListener("submit",async event=>{
    event.preventDefault();
    if(busy)return;
    const text=input.value.trim();
    if(!text)return;
    if(text.startsWith("/")&&commandHistory.at(-1)!==text){
      commandHistory.push(text);
      if(commandHistory.length>50)commandHistory.shift();
    }
    historyIndex=-1;
    historyDraft="";
    hideSuggestions();
    const sent=await dispatch(text.startsWith("/")?{command:text}:{message:text});
    if(sent&&input.value.trim()===text)input.value="";
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
