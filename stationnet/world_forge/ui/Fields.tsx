import {useEffect,useId,useRef,useState} from 'react';
import {Plus,Trash2,ArrowUpRight,ChevronDown,BookOpen} from 'lucide-react';
import {FieldSchema,Registry,Project,Selection,Row,rows,label,title,infer,initial,stationReferences} from './data';

type Props={name:string;value:any;schema?:FieldSchema;registry:Registry;project:Project;onChange:(v:any)=>void;onRemove?:()=>void;onNavigate:(s:Selection)=>void;depth?:number;context?:Row;variant?:string;onHelp?:(text:string)=>void};
export function Field({name,value,schema={},registry,project,onChange,onRemove,onNavigate,depth=0,context,variant,onHelp}:Props){
 const id=useId();const [buffer,setBuffer]=useState('');const [newKey,setNewKey]=useState('');const [newType,setNewType]=useState('string');const [error,setError]=useState('');
 const timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
 const bufferRef=useRef('');const editing=useRef(false);const lastSent=useRef<string|undefined>(undefined);
 useEffect(()=>()=>clearTimeout(timer.current),[]);
 const serialized=JSON.stringify(value);
 useEffect(()=>{if(!editing.current||bufferRef.current===lastSent.current){const next=value==null?'':String(value);setBuffer(next);bufferRef.current=next;editing.current=false;}},[serialized]);
 const actual=value!==undefined&&value!==null?infer(value):schema;
 let type=actual.type||schema.type||'string';
 // Nested properties stored as JSON text can still be edited through guided controls.
 if(['properties_json','behaviors_json','state_json'].includes(name)&&typeof value==='string'){
  try{const parsed=JSON.parse(value);if(parsed&&typeof parsed==='object')return <Field {...{name,registry,project,onRemove,onNavigate,depth,context,onHelp}} value={parsed} schema={schema} onChange={v=>onChange(JSON.stringify(v))}/>;}catch{/* Keep malformed imported text editable. */}
 }
 const hints={...registry.shapes[name],...schema};
 const dynamicTarget=(name==='target_id'||name==='objective_target_id')?({visit_room:'rooms',talk_npc:'mud_npc_definitions',defeat_npc:'mud_npc_definitions',possess_item:'mud_item_definitions',deliver_item:'mud_item_definitions',item:'mud_item_definitions',reputation:'mud_world_factions'} as Row)[context?.objective_type||context?.reward_type]:undefined;
 const reference=schema.ref||dynamicTarget||(name==='key'&&context&&('potency' in context||'duration' in context)?'effects':undefined)||(!['from','to','key'].includes(name)?registry.references?.[name]:undefined);
 const directRefRows=reference?rows(project,registry,reference):[];
 const localRows:Row[]=reference==='mud_item_definitions'||reference==='mud_npc_definitions'?rows(project,registry,'templates').flatMap(t=>{const defs=t.template?.definitions||{};return (reference==='mud_item_definitions'?defs.items||[]:[...(defs.npcs||[]),...(defs.enemies||[])]).map((r:Row)=>({...r,name:`${t.name} / ${r.name||r.id}`}));}):[];
 const refRows=[...directRefRows,...localRows,...stationReferences(reference).filter(entry=>!directRefRows.some(r=>String(r[reference&&registry.collections[reference]?.identity[0]||'id'])===entry.key)).map(entry=>({[reference&&registry.collections[reference]?.identity[0]||'id']:entry.key,name:entry.name+' · station'}) as Row)];
 const refKey=reference&&registry.collections[reference]?.identity[0]||'id';
 const commit=(text=buffer)=>{
  clearTimeout(timer.current);
  if(editing.current&&lastSent.current===text)return;
  lastSent.current=text;
  if(type==='number'){
   if(text.trim()===''){if(value!==null)onChange(null);return;}
   const number=Number(text);if(!Number.isFinite(number)){setError('Enter a finite number.');return;}
   if(number!==value)onChange(number);
  }else if(text!==String(value??''))onChange(text);
  setError('');
 };
 const typeText=(text:string)=>{setBuffer(text);bufferRef.current=text;editing.current=true;clearTimeout(timer.current);timer.current=setTimeout(()=>commit(text),800);};
 const childProps={registry,project,onNavigate,depth:depth+1,onHelp};
 const remove=onRemove?<button type="button" className="icon subtle remove-field" onClick={onRemove} title={`Remove ${title(name)}`} aria-label={`Remove ${title(name)}`}><Trash2 size={13}/></button>:null;
 const heading=<div className="field-label"><label htmlFor={id}>{title(name)}</label><span className="field-label-tools">{onHelp&&<button className="icon subtle" title="Handbook help" onClick={()=>onHelp(name)}><BookOpen size={12}/></button>}{remove}</span></div>;
 if(type==='boolean')return <div className="field boolean-field">{heading}<button id={id} role="switch" aria-checked={!!value} className={'switch '+(value?'on':'')} onClick={()=>onChange(!value)}><span/></button><span className="hint">{value?'Enabled':'Disabled'}</span></div>;
 if(schema.widget==='walls'){
  const selected=String(value||'').split(',');return <div className="field">{heading}<div className="wall-grid">{registry.directions.slice(0,8).map(d=><label key={d}><input type="checkbox" checked={selected.includes(d)} onChange={e=>onChange((e.target.checked?[...selected,d]:selected.filter(x=>x!==d)).filter(Boolean).join(','))}/>{d}</label>)}</div></div>;
 }
 if(type==='object'){
  const v=value&&typeof value==='object'&&!Array.isArray(value)?value:{};
  let suggested:Record<string,FieldSchema>={...registry.shapes[name]?.properties,...schema.properties};
  if(variant==='condition')suggested={type:{type:'string',enum:schema.properties?.type?.enum||Object.keys(registry.conditions).filter(x=>x!=='any')},negate:{type:'boolean'},...(registry.conditions[v.type]?.properties||{})};
  if(variant==='action')suggested={type:{type:'string',enum:schema.properties?.type?.enum||Object.keys(registry.actions)},delay_seconds:{type:'number'},...(registry.actions[v.type]?.properties||{})};
  if(name==='source'&&context?.event&&!schema.properties?.room_role)suggested=registry.events[context.event]?.properties||{};
  const optional=Object.keys(suggested).filter(k=>!(k in v));
  return <details className={'structured field '+(depth?'nested':'')} open={depth<1||variant!==undefined}>
   <summary><span>{title(name)}</span><span className="count">{Object.keys(v).length} fields</span>{remove}<ChevronDown size={13}/></summary>
   <div className="structured-body">
    {variant==='condition'&&<button className="text-button" onClick={()=>onChange({any:[{type:'level',min:1}]})}>Use an “any of” group</button>}
    {Object.entries(v).map(([k,x])=><Field key={k} {...childProps} name={k} value={x} schema={suggested[k]||infer(x)} context={v} variant={k==='any'?'condition':undefined} onChange={n=>{let next={...v,[k]:n};if(k==='type'&&variant){next={type:n,...(variant==='condition'?{}:{})};}onChange(next);}} onRemove={()=>{const n={...v};delete n[k];onChange(n);}}/>)}
    {!Object.keys(v).length&&<p className="hint">Add the settings you need. Omitted settings use engine defaults.</p>}
    <div className="add-property"><select aria-label={`Add ${name} setting`} value="" onChange={e=>{if(e.target.value)onChange({...v,[e.target.value]:initial(suggested[e.target.value])});}}><option value="">+ Add a setting…</option>{optional.map(k=><option key={k} value={k}>{title(k)}</option>)}</select></div>
    <details className="custom-property"><summary>Custom key</summary><div className="inline"><input aria-label="Custom field name" value={newKey} onChange={e=>setNewKey(e.target.value)} placeholder="Field name"/><select aria-label="Custom field type" value={newType} onChange={e=>setNewType(e.target.value)}>{['string','number','boolean','object','array'].map(t=><option key={t}>{t}</option>)}</select><button className="icon" disabled={!newKey.trim()||newKey in v} onClick={()=>{onChange({...v,[newKey]:initial({type:newType})});setNewKey('');}} title="Add custom field"><Plus size={15}/></button></div></details>
   </div>
  </details>;
 }
 if(type==='array'){
  const v=Array.isArray(value)?value:[];
  const itemSchema=schema.items||hints.items||{};const itemVariant=schema.variant||variant;
  const starter=()=>itemVariant==='condition'?{type:'level',min:1}:itemVariant==='action'?{type:'message',text:''}:initial(itemSchema);
  return <details className="structured field" open={depth<1||v.length<3}><summary><span>{title(name)}</span><span className="count">{v.length} entries</span>{remove}<ChevronDown size={13}/></summary><div className="structured-body array-body">
   {v.slice(0,200).map((x,i)=><div className="array-entry" key={i}><div className="array-index">{i+1}<button className="icon subtle" aria-label={`Move ${name} entry ${i+1} up`} disabled={i===0} onClick={()=>{const n=[...v];[n[i-1],n[i]]=[n[i],n[i-1]];onChange(n);}}>↑</button><button className="icon subtle" aria-label={`Move ${name} entry ${i+1} down`} disabled={i===v.length-1} onClick={()=>{const n=[...v];[n[i+1],n[i]]=[n[i],n[i+1]];onChange(n);}}>↓</button></div><div className="array-value"><Field {...childProps} name={typeof x==='object'?'Entry':String(i+1)} value={x} schema={itemSchema} variant={itemVariant} context={context} onChange={n=>onChange(v.map((old,j)=>i===j?n:old))} onRemove={()=>onChange(v.filter((_,j)=>i!==j))}/></div></div>)}
   {v.length>200&&<p className="hint">Showing the first 200 entries. Use terrain painting for large grids, or JSON view for full arrays.</p>}
   <button className="text-button" onClick={()=>onChange([...v,starter()])}><Plus size={13}/> Add entry</button>
  </div></details>;
 }
 const options=schema.enum;
 const large=['description','body','response_text','clue','summary','text','fail_message','conditions_message'].includes(name)||String(value??'').length>120;
 return <div className="field">{heading}<div className="input-with-link">
  {options?<select id={id} value={value??''} onChange={e=>onChange(schema.type==='number'?Number(e.target.value):e.target.value)}><option value="">Choose…</option>{options.map(o=><option key={String(o)} value={o}>{title(String(o))}</option>)}{value!=null&&!options.includes(value)&&<option value={value}>{String(value)}</option>}</select>:
  large?<textarea id={id} value={buffer} rows={name==='description'||name==='body'?5:3} onChange={e=>typeText(e.target.value)} onBlur={()=>commit()}/>:
  <input id={id} type={type==='number'?'number':'text'} step={type==='number'?'any':undefined} min={schema.min} max={schema.max} value={buffer} list={reference?id+'-refs':undefined} placeholder={reference?'Search or enter an ID…':undefined} onChange={e=>typeText(e.target.value)} onBlur={()=>commit()} onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur();}}/>}
  {reference&&<><datalist id={id+'-refs'}>{refRows.map((r,i)=><option key={i} value={String(r[refKey]??'')}>{label(r)}</option>)}</datalist><button className="icon subtle" title="Open referenced record" disabled={!directRefRows.some(r=>String(r[refKey])===String(value))} onClick={()=>{const i=directRefRows.findIndex(r=>String(r[refKey])===String(value));if(i>=0)onNavigate({collection:reference,index:i});}}><ArrowUpRight size={15}/></button></>}
 </div>{error&&<span className="field-error">{error}</span>}{reference&&<small className="hint">Reference · {registry.collections[reference]?.title||reference}</small>}</div>;
}

export function RecordFields({record,collection,registry,project,onField,onNavigate,onHelp}:{record:Row;collection:string;registry:Registry;project:Project;onField:(key:string,value:any,remove?:boolean)=>void;onNavigate:(s:Selection)=>void;onHelp:(text:string)=>void}){
 const c=registry.collections[collection];const [add,setAdd]=useState('');
 const common=['id','key','name','description','body','response_text','from','to','direction','room_id','definition_id','npc_definition_id','quest_id','node_id','conversation_id','item_type','equipment_slot','event','source','conditions','conditions_json','actions','objectives','template','x','y','z','grid_link','blocked_dirs'];
 const keys=Object.keys(record);const basics=keys.filter(k=>common.includes(k));const advanced=keys.filter(k=>!common.includes(k));
 const render=(key:string)=><Field key={key} name={key} value={record[key]} schema={c.fields[key]||infer(record[key])} registry={registry} project={project} context={record} onChange={v=>onField(key,v)} onRemove={()=>onField(key,undefined,true)} onNavigate={onNavigate} onHelp={onHelp}/>;
 const optional=Object.keys(c.fields).filter(k=>!(k in record));
 return <><div className="field-stack">{basics.map(render)}</div>{advanced.length>0&&<details className="advanced-fields" open={collection==='manifest'}><summary>More settings <span className="count">{advanced.length}</span></summary><div className="field-stack">{advanced.map(render)}</div></details>}
  <div className="add-record-field"><select aria-label="Add optional field" value={add} onChange={e=>setAdd(e.target.value)}><option value="">Add optional field…</option>{optional.map(k=><option key={k} value={k}>{title(k)}</option>)}</select><button className="icon" disabled={!add} onClick={()=>{onField(add,initial(c.fields[add]));setAdd('');}} title="Add field"><Plus size={16}/></button></div>
 </>;
}
