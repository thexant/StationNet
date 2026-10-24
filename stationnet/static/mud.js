"use strict";

for(const mud of document.querySelectorAll("[data-mud]")){
  const events=mud.querySelector("[data-mud-events]");
  const main=mud.querySelector(".mud-main");
  const history=mud.querySelector(".mud-history");
  const actions=mud.querySelector("[data-mud-actions]");
  const form=mud.querySelector("[data-mud-command]");
  const input=form.querySelector("[data-command-input]");
  const error=mud.querySelector("[data-mud-error]");
  const roomName=mud.querySelector("[data-mud-room-name]");
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
  const quests=mud.querySelector("[data-mud-quests]");
  const questCount=mud.querySelector("[data-mud-quest-count]");
  let busy=false, pollBusy=false;
  let lastId=Math.max(0,...Array.from(events.querySelectorAll("[data-event-id]"),row=>Number(row.dataset.eventId)||0));

  const scrollEventsToLatest=()=>requestAnimationFrame(()=>{
    events.scrollTop=Math.max(0,events.scrollHeight-events.clientHeight);
  });
  const syncHistoryLayout=()=>main?.classList.toggle("mud-history-collapsed",!history?.open);
  history?.addEventListener("toggle",()=>{
    syncHistoryLayout();
    if(history.open)scrollEventsToLatest();
  });
  syncHistoryLayout();

  const requestId=()=>globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}`;
  let clientId;
  try{
    clientId=sessionStorage.getItem("stationnet.mud.client");
    if(!clientId){clientId=requestId();sessionStorage.setItem("stationnet.mud.client",clientId)}
  }catch(_){clientId=requestId()}

  const appendEvent=event=>{
    lastId=Math.max(lastId,Number(event.id)||0);
    if(event.id&&events.querySelector(`[data-event-id="${event.id}"]`))return;
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
    scrollEventsToLatest();
  };
  const renderActions=available=>{
    const groups=new Map();
    for(const action of available||[]){
      const name=action.group||"ACTIONS";
      if(!groups.has(name))groups.set(name,[]);
      groups.get(name).push(action);
    }
    actions.replaceChildren();
    for(const [name,items] of groups){
      const group=document.createElement("div");
      group.className="mud-action-group";
      group.dataset.actionGroup=name;
      const heading=document.createElement("span");
      heading.textContent=name;
      const controls=document.createElement("div");
      for(const action of items){
        const button=document.createElement("button");
        button.type="button";
        button.className="secondary";
        button.dataset.mudAction=action.key;
        button.dataset.mudParameters=JSON.stringify(action.parameters||{});
        button.title=action.description||"";
        button.textContent=action.label;
        controls.append(button);
      }
      group.append(heading,controls);
      actions.append(group);
    }
  };
  const renderRoom=room=>{
    if(!room)return;
    roomName.textContent=room.name||"Unknown Location";
    roomDescription.textContent=room.description||"";
    if(roomInstructions){roomInstructions.textContent=room.instructions||"";roomInstructions.hidden=!room.instructions}
    roomExits.textContent=(room.exits||[]).map(exit=>exit.direction||exit.name).join(", ")||"none";
    if(roomItems)roomItems.textContent=(room.items||[]).map(item=>item.name).join(", ")||"none";
    if(roomNpcs)roomNpcs.textContent=(room.npcs||[]).map(npc=>npc.name).join(", ")||"none";
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
      const name=document.createElement("strong");name.textContent=enemy.name;
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
      for(const item of items){const button=document.createElement("button");button.type="button";button.className="secondary";button.dataset.mudAction="look";button.dataset.mudParameters=JSON.stringify({target:item.id});button.textContent=slot?`${(item.equipped_slot||"equipment").replaceAll("_"," ")}: ${item.name}`:item.name;node.append(button)}
    };
    fill(carried,inventory.inventory,false);fill(equipped,inventory.equipment,true);
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
    for(const button of actions.querySelectorAll("button"))button.disabled=value;
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
      renderQuests(payload.quests);
      renderCombat(payload.combat);
      if(payload.actions)renderActions(payload.actions);
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
      renderQuests(payload.quests);
      renderCombat(payload.combat);
      if(payload.actions)renderActions(payload.actions);
      for(const event of payload.events||[])appendEvent(event);
      if(error.textContent==="LINK RETRYING...")error.textContent="";
    }catch(problem){error.textContent=problem.message||"LINK RETRYING..."}
    finally{pollBusy=false}
  };

  mud.addEventListener("click",event=>{
    const button=event.target.closest("[data-mud-action]");
    if(button)dispatch({action:button.dataset.mudAction,parameters:button.dataset.mudParameters||"{}"});
  });
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
  scrollEventsToLatest();
  addEventListener("load",scrollEventsToLatest,{once:true});
  poll();
  setInterval(poll,2000);
}
