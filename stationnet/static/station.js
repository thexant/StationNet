"use strict";
const clock=document.querySelector("[data-clock]");
const dateReadout=document.querySelector("[data-date]");
if(clock){const tick=()=>{const now=new Date();clock.textContent=now.toLocaleTimeString([],{hour12:false});if(dateReadout)dateReadout.textContent=now.toLocaleDateString([],{weekday:"short",month:"short",day:"2-digit",year:"numeric"}).toUpperCase()};tick();setInterval(tick,1000)}
document.addEventListener("keydown",e=>{const editing=e.target instanceof HTMLInputElement||e.target instanceof HTMLTextAreaElement;if(e.key==="/"&&!editing){e.preventDefault();document.querySelector("#service-nav")?.focus()}if(e.key==="Escape"&&document.activeElement instanceof HTMLElement)document.activeElement.blur()});
for(const form of document.querySelectorAll("form[data-confirm]"))form.addEventListener("submit",e=>{if(!confirm(form.dataset.confirm))e.preventDefault()});

