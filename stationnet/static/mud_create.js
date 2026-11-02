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
  const skillName=key=>config.skill_names?.[key]||key;
  const grantsFor=(race,archetype)=>{
    const grants={};
    for(const origin of [race,archetype])for(const [key,rank] of Object.entries(origin?.skills||{}))grants[key]=Math.max(grants[key]||0,rank);
    return grants;
  };
  // Mirrors character_creation.eligible_skills so the form never offers a rejected build.
  const skillProblems=(skill,race,archetype,final,grants)=>{
    const needs=skill.requirements||{};
    const problems=[];
    if((needs.level||1)>1)problems.push(`level ${needs.level}`);
    for(const [key,value] of Object.entries(needs.attributes||{}))if((final[key]||0)<value)problems.push(`${key} ${value}`);
    if(needs.races?.length&&!needs.races.includes(race?.key))problems.push("another race");
    if(needs.classes?.length&&!needs.classes.includes(archetype?.key))problems.push("another archetype");
    for(const [key,value] of Object.entries(needs.skills||{}))if((grants[key]||0)<value)problems.push(`${skillName(key)} ${value}`);
    return problems;
  };
  const eligibility=data=>{
    const race=config.races.find(item=>item.key===data.race);
    const archetype=config.classes.find(item=>item.key===data.class);
    const final=Object.fromEntries(Object.entries(data.attributes).map(([key,value])=>
      [key,value+(race?.attributes?.[key]||0)+(archetype?.attributes?.[key]||0)]));
    const grants=grantsFor(race,archetype);
    const notes=Object.fromEntries(config.skills.map(skill=>[skill.key,
      grants[skill.key]?`Granted at rank ${grants[skill.key]}`:skillProblems(skill,race,archetype,final,grants).map(item=>`Requires ${item}`).join(" · ")]));
    const eligible=config.skills.filter(skill=>!notes[skill.key]).map(skill=>skill.key);
    // Mirrors character_creation.starting_skill_count: origins may add picks.
    const bonus=(race?.progression?.starting_skill_choices||0)+(archetype?.progression?.starting_skill_choices||0);
    return {grants,notes,eligible,required:Math.min(config.starting_skill_choices+bonus,eligible.length)};
  };
  // Mirrors the start condition types of mud_conditions; the server has the last word.
  const startHolds=(condition,facts)=>{
    if(condition.any)return condition.any.some(option=>startCheck(option,facts));
    switch(condition.type){
      case "level":return (condition.min||1)<=1&&1<=(condition.max??1);
      case "race":return condition.keys.includes(facts.race?.key);
      case "class":return condition.keys.includes(facts.archetype?.key);
      case "tag":return condition.keys.some(key=>[...facts.race?.tags||[],...facts.archetype?.tags||[]].includes(key));
      case "attribute":return (facts.final[condition.attribute]||0)>=condition.min;
      case "skill":return (facts.ranks[condition.skill]||0)>=condition.min_rank;
      default:return false;
    }
  };
  const startCheck=(condition,facts)=>startHolds(condition,facts)!==Boolean(condition.negate);
  const factionFacts=data=>{
    const race=config.races.find(item=>item.key===data.race);
    const archetype=config.classes.find(item=>item.key===data.class);
    const final=Object.fromEntries(Object.entries(data.attributes).map(([key,value])=>
      [key,value+(race?.attributes?.[key]||0)+(archetype?.attributes?.[key]||0)]));
    const ranks=grantsFor(race,archetype);
    for(const key of data.skills)ranks[key]=Math.max(ranks[key]||0,archetype?.skill===key?2:1);
    return {race,archetype,final,ranks};
  };
  const eligibleFactions=data=>{
    const facts=factionFacts(data);
    return (config.starting_factions||[]).filter(faction=>faction.conditions.every(condition=>startCheck(condition,facts)));
  };
  const build=()=>({
    name:form.querySelector("[data-name]").value.trim().replace(/\s+/g," "),
    race:chosen("race"),class:chosen("class"),
    attributes:Object.fromEntries([...form.querySelectorAll("[data-attribute]")].map(input=>[input.dataset.attribute,Number(input.value)])),
    skills:[...form.querySelectorAll('input[name="skill"]:checked:not(:disabled)')].map(input=>input.value),
    faction:chosen("faction")||null
  });
  const update=()=>{
    const data=build();
    const race=config.races.find(item=>item.key===data.race);
    const archetype=config.classes.find(item=>item.key===data.class);
    const final=Object.fromEntries(Object.entries(data.attributes).map(([key,value])=>
      [key,value+(race?.attributes?.[key]||0)+(archetype?.attributes?.[key]||0)]));
    const spent=Object.values(data.attributes).reduce((sum,value)=>sum+value-config.point_buy.base,0);
    creator.querySelector("[data-points-left]").textContent=config.point_buy.points-spent;
    const skillState=eligibility(data);
    for(const card of form.querySelectorAll("[data-skill-card]")){
      const checkbox=card.querySelector("input");
      const note=skillState.notes[card.dataset.skillCard];
      if(note)checkbox.checked=false;
      card.querySelector("[data-skill-note]").textContent=note;
    }
    data.skills=data.skills.filter(key=>skillState.eligible.includes(key));
    for(const checkbox of form.querySelectorAll('input[name="skill"]')){
      checkbox.disabled=!skillState.eligible.includes(checkbox.value)||
        (!checkbox.checked&&data.skills.length>=skillState.required);
    }
    creator.querySelector("[data-skill-choices]").textContent=skillState.required;
    creator.querySelector("[data-skills-left]").textContent=skillState.required-data.skills.length;
    creator.querySelector("[data-skill-granted]").textContent=Object.keys(skillState.grants).length
      ?`Granted: ${Object.entries(skillState.grants).map(([key,rank])=>`${skillName(key)} ${rank}`).join(" · ")}`:"";
    for(const button of form.querySelectorAll("[data-adjust]")){
      const value=data.attributes[button.dataset.for];
      button.disabled=Number(button.dataset.adjust)>0
        ?value>=config.point_buy.maximum||spent>=config.point_buy.points
        :value<=config.point_buy.minimum;
    }
    for(const [key,value] of Object.entries(data.attributes)){
      const result=final[key];
      creator.querySelector(`[data-final-attribute="${key}"]`).textContent=`Final: ${result}`;
    }
    const open=eligibleFactions(data).map(faction=>faction.id);
    for(const card of form.querySelectorAll("[data-faction-card]")){
      const radio=card.querySelector("input");
      const allowed=open.includes(card.dataset.factionCard);
      radio.disabled=!allowed;
      if(!allowed)radio.checked=false;
      card.querySelector("[data-faction-note]").textContent=allowed?"":"This character does not qualify.";
    }
    const none=form.querySelector("[data-faction-none]");
    if(none){
      const radio=none.querySelector("input");
      const hidden=Boolean(config.faction_required)&&open.length>0;
      none.hidden=hidden;radio.disabled=hidden;
      if(!hidden&&!chosen("faction"))radio.checked=true;
    }
    data.faction=chosen("faction")||null;
    const review=creator.querySelector("[data-review]");
    review.replaceChildren();
    const ranks={...skillState.grants};
    for(const key of data.skills)ranks[key]=Math.max(ranks[key]||0,archetype?.skill===key?2:1);
    // Category-limited bonuses depend on gear, so the preview counts only general effects.
    const effect=key=>(race?.effects?.[key]||0)+(archetype?.effects?.[key]||0)+
      Object.entries(ranks).reduce((sum,[skillKey,rank])=>{
        const skill=config.skills.find(item=>item.key===skillKey);
        return sum+(skill?.effects||[]).filter(item=>item.type===key&&!item.weapon_category&&!item.armor_category)
          .reduce((total,item)=>total+Math.min(item.max??Infinity,Math.floor(rank/(item.every||1))*item.per_rank),0);
      },0);
    const merged=field=>({...race?.[field]||{},...Object.fromEntries(Object.entries(archetype?.[field]||{})
      .map(([key,value])=>[key,value+(race?.[field]?.[key]||0)]))});
    const unique=items=>[...new Set(items.filter(Boolean))];
    const growth=merged("progression");
    const health=100+(final.vitality-10)*5+(growth.health||0);
    const resistances=Object.entries(merged("resistances")).map(([key,value])=>`${key} ${value}%`);
    const lines=[`Name: ${data.name||"—"}`,`Race: ${race?.name||"—"}`,`Archetype: ${archetype?.name||"—"}`,
      `Attributes: ${Object.entries(final).map(([key,value])=>`${key} ${value}`).join(" · ")}`,
      `Starting health: ${health} · Melee bonus: +${Math.floor(final.strength/2)+effect("attack")+effect("melee_damage")} · Damage reduction: ${Math.floor(final.vitality/10)+Math.floor(final.agility/5)+effect("defense")} · Experience bonus: +${Math.max(0,final.intellect-8)+effect("experience_percent")}%`,
      `Skills: ${Object.entries(ranks).map(([key,rank])=>`${skillName(key)} ${rank}`).join(" · ")||"—"}`,
      `Equipment: ${[...race?.equipment||[],...archetype?.equipment||[]].map(item=>item.name).join(", ")||"—"}`,
      `Traits: ${unique([...race?.tags||[],...archetype?.tags||[]]).join(", ")||"—"}`,
      `Abilities: ${unique([...archetype?.spells||[],...race?.spells||[]]).map(key=>config.ability_names?.[key]||key).join(", ")||"—"}${config.resource?.enabled===false?" (unavailable in this world)":""}`,
      `Trained with: ${unique([race,archetype].flatMap(origin=>[...origin?.proficiencies?.weapons||[],...origin?.proficiencies?.armor||[]])).join(", ")||"skills only"}`,
      `Resists: ${resistances.join(" · ")||"—"}`];
    if(config.starting_factions?.length)lines.push(`Faction: ${config.starting_factions.find(item=>item.id===data.faction)?.name||"Independent"}`);
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
    if(current===4){
      const required=eligibility(data).required;
      if(data.skills.length!==required)return `Choose ${required} skill${required===1?"":"s"}.`;
    }
    if(current===5&&steps.length===7&&config.faction_required&&!data.faction&&eligibleFactions(data).length)return "Choose a starting faction.";
    return "";
  };
  const show=()=>{
    steps.forEach((node,index)=>{node.hidden=index!==step;markers[index].setAttribute("aria-current",index===step?"step":"false")});
    previous.hidden=step===0;next.hidden=step===steps.length-1;submit.hidden=step!==steps.length-1;
    error.textContent="";update();
  };
  form.querySelectorAll("[data-adjust]").forEach(button=>button.addEventListener("click",()=>{
    const input=form.querySelector(`[data-attribute="${button.dataset.for}"]`);
    input.value=Number(input.value)+Number(button.dataset.adjust);
    update();
  }));
  form.querySelector("[data-reset-attributes]").addEventListener("click",()=>{
    form.querySelectorAll("[data-attribute]").forEach(input=>input.value=config.point_buy.base);
    update();
  });
  creator.querySelectorAll("[data-step-jump]").forEach(button=>button.addEventListener("click",()=>{
    const target=Number(button.dataset.stepJump);
    if(target>step){
      for(let index=step;index<target;index++){
        const problem=validate(index);
        if(problem){step=index;show();error.textContent=problem;return}
      }
    }
    step=target;show();
  }));
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
