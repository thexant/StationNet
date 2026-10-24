"use strict";

for(const creator of document.querySelectorAll("[data-character-creator]")){
  const config=JSON.parse(creator.dataset.config);
  const form=creator.querySelector("[data-creator-form]");
  const steps=[...creator.querySelectorAll("[data-step]")];
  const markers=[...creator.querySelectorAll(".mud-creator-steps li")];
  const previous=creator.querySelector("[data-prev]");
  const next=creator.querySelector("[data-next]");
  const submit=creator.querySelector("[data-submit]");
  const error=creator.querySelector("[data-creator-error]");
  let step=0;
  const chosen=name=>form.querySelector(`input[name="${name}"]:checked`)?.value;
  const build=()=>({
    name:form.querySelector("[data-name]").value.trim().replace(/\s+/g," "),
    race:chosen("race"),class:chosen("class"),
    attributes:Object.fromEntries([...form.querySelectorAll("[data-attribute]")].map(input=>[input.dataset.attribute,Number(input.value)])),
    skills:[...form.querySelectorAll('input[name="skill"]:checked')].map(input=>input.value)
  });
  const update=()=>{
    const data=build();
    const race=config.races.find(item=>item.key===data.race);
    const archetype=config.classes.find(item=>item.key===data.class);
    const final=Object.fromEntries(Object.entries(data.attributes).map(([key,value])=>
      [key,value+(race?.attributes?.[key]||0)+(archetype?.attributes?.[key]||0)]));
    const spent=Object.values(data.attributes).reduce((sum,value)=>sum+value-config.point_buy.base,0);
    creator.querySelector("[data-points-left]").textContent=config.point_buy.points-spent;
    creator.querySelector("[data-skills-left]").textContent=config.starting_skill_choices-data.skills.length;
    for(const [key,value] of Object.entries(data.attributes)){
      const result=final[key];
      creator.querySelector(`[data-final-attribute="${key}"]`).textContent=`Final: ${result}`;
    }
    const review=creator.querySelector("[data-review]");
    review.replaceChildren();
    const effect=key=>(race?.effects?.[key]||0)+(archetype?.effects?.[key]||0)+
      data.skills.reduce((sum,skillKey)=>{
        const skill=config.skills.find(item=>item.key===skillKey);
        return sum+(skill?.effect===key?(skill?.per_rank||1)*(archetype?.skill===skillKey?2:1):0);
      },0);
    const lines=[`Name: ${data.name||"—"}`,`Race: ${race?.name||"—"}`,`Archetype: ${archetype?.name||"—"}`,
      `Attributes: ${Object.entries(final).map(([key,value])=>`${key} ${value}`).join(" · ")}`,
      `Starting health: ${100+(final.vitality-10)*5} · Melee bonus: +${Math.floor(final.strength/2)+effect("attack")} · Damage reduction: ${Math.floor(final.vitality/10)+Math.floor(final.agility/5)+effect("defense")} · Experience bonus: +${Math.max(0,final.intellect-8)+effect("experience_percent")}%`,
      `Skills: ${data.skills.map(key=>config.skills.find(item=>item.key===key)?.name||key).join(" · ")||"—"}`,
      `Equipment: ${archetype?.equipment?.map(item=>item.name).join(", ")||"—"}`];
    for(const line of lines){const p=document.createElement("p");p.textContent=line;review.append(p)}
  };
  const validate=current=>{
    const data=build();
    if(current===0&&!/^\p{L}[\p{L} .'-]{1,31}$/u.test(data.name))return "Enter a 2–32 character name using letters and simple punctuation.";
    if(current===1&&!data.race)return "Choose a race.";
    if(current===2&&!data.class)return "Choose an archetype.";
    if(current===3){
      const values=Object.values(data.attributes);
      if(values.some(value=>!Number.isInteger(value)||value<config.point_buy.minimum||value>config.point_buy.maximum))return "Keep attributes within the allowed range.";
      if(values.reduce((sum,value)=>sum+value-config.point_buy.base,0)!==config.point_buy.points)return "Spend all attribute points.";
    }
    if(current===4&&data.skills.length!==config.starting_skill_choices)return `Choose ${config.starting_skill_choices} skills.`;
    return "";
  };
  const show=()=>{
    steps.forEach((node,index)=>{node.hidden=index!==step;markers[index].setAttribute("aria-current",index===step?"step":"false")});
    previous.hidden=step===0;next.hidden=step===steps.length-1;submit.hidden=step!==steps.length-1;
    error.textContent="";update();
  };
  next.addEventListener("click",()=>{const problem=validate(step);if(problem){error.textContent=problem;return}step++;show();steps[step].scrollIntoView({block:"nearest"})});
  previous.addEventListener("click",()=>{step--;show()});
  form.addEventListener("input",update);
  form.addEventListener("change",update);
  form.addEventListener("submit",event=>{
    for(let index=0;index<steps.length-1;index++){
      const problem=validate(index);
      if(problem){event.preventDefault();step=index;show();error.textContent=problem;return}
    }
    form.querySelector("[data-build]").value=JSON.stringify(build());
  });
  show();
}
