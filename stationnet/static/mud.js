"use strict";

for(const mud of document.querySelectorAll("[data-mud]")){
  const events=mud.querySelector("[data-mud-events]");
  const actions=mud.querySelector("[data-mud-actions]");
  const form=mud.querySelector("[data-mud-command]");
  const input=form.querySelector("[data-command-input]");
  const error=mud.querySelector("[data-mud-error]");
  const roomName=mud.querySelector("[data-mud-room-name]");
  const roomDescription=mud.querySelector("[data-mud-room-description]");
  const roomExits=mud.querySelector("[data-mud-room-exits]");
  let busy=false;

  const requestId=()=>globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const appendEvent=event=>{
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
    events.scrollTop=events.scrollHeight;
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
    roomExits.textContent=(room.exits||[]).map(exit=>exit.direction||exit.name).join(", ")||"none";
  };
  const setBusy=value=>{
    busy=value;
    form.querySelector("button").disabled=value;
    for(const button of actions.querySelectorAll("button"))button.disabled=value;
  };
  const dispatch=async fields=>{
    if(busy)return;
    setBusy(true);
    error.textContent="";
    const data=new FormData();
    data.set("csrf_token",mud.dataset.csrf);
    data.set("request_id",requestId());
    for(const [key,value] of Object.entries(fields))data.set(key,value);
    try{
      const response=await fetch(mud.dataset.actionUrl,{method:"POST",body:data,headers:{"Accept":"application/json"}});
      if(response.status===401||response.redirected){location.reload();return}
      const payload=await response.json();
      renderRoom(payload.room);
      if(payload.actions)renderActions(payload.actions);
      if(!response.ok)throw new Error(payload.error||"Action rejected by the station.");
      for(const event of payload.events||[])appendEvent(event);
    }catch(problem){error.textContent=problem.message||"MUD link interrupted."}
    finally{setBusy(false)}
  };

  actions.addEventListener("click",event=>{
    const button=event.target.closest("[data-mud-action]");
    if(button)dispatch({action:button.dataset.mudAction,parameters:button.dataset.mudParameters||"{}"});
  });
  form.addEventListener("submit",event=>{
    event.preventDefault();
    const command=input.value.trim();
    if(!command)return;
    dispatch({command}).then(()=>{if(!error.textContent)input.value="";input.focus()});
  });
  events.scrollTop=events.scrollHeight;
}
