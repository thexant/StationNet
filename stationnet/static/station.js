"use strict";
const clock=document.querySelector("[data-clock]");
if(clock){const tick=()=>clock.textContent=new Date().toLocaleTimeString([],{hour12:false});tick();setInterval(tick,1000)}
document.addEventListener("keydown",e=>{const editing=e.target instanceof HTMLInputElement||e.target instanceof HTMLTextAreaElement;if(e.key==="/"&&!editing){e.preventDefault();document.querySelector("#service-nav")?.focus()}if(e.key==="Escape"&&document.activeElement instanceof HTMLElement)document.activeElement.blur()});
for(const form of document.querySelectorAll("form[data-confirm]"))form.addEventListener("submit",e=>{if(!confirm(form.dataset.confirm))e.preventDefault()});

