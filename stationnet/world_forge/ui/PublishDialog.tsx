import {useEffect,useState} from 'react';
import {AlertTriangle,CheckCircle2,Rocket,X} from 'lucide-react';
import {api,Project,Row} from './data';

type Preview=Row & {ok:boolean;blocked:boolean;hash:string;mode:'install'|'light'|'full';problems:Row[];error?:string};

const SECTION_NAMES:Row={rooms:'Rooms',exits:'Passages',events:'Triggers',dungeons:'Dungeon templates',skills:'Skills',abilities:'Abilities',effects:'Effects',races:'Races',classes:'Classes'};
const sectionName=(key:string)=>SECTION_NAMES[key]||key.replace(/^mud_/,'').replace(/_/g,' ');

export function PublishDialog({project,admin,onClose,onPublished,onRebase}:{project:Project;admin:boolean;onClose:()=>void;onPublished:(result:Row)=>void;onRebase:()=>void}){
 const [preview,setPreview]=useState<Preview|null>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 const [announce,setAnnounce]=useState(true);const [reset,setReset]=useState(false);const [acceptStale,setAcceptStale]=useState('');
 useEffect(()=>{let live=true;api<Preview>(`/d/${project.id}/publish/preview`,{}).then(p=>{if(live)setPreview(p);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[project.id,project.revision]);
 useEffect(()=>{const key=(e:KeyboardEvent)=>{if(e.key==='Escape'&&!busy)onClose();};document.addEventListener('keydown',key);return()=>document.removeEventListener('keydown',key);},[onClose,busy]);
 const errors=(preview?.problems||[]).filter(p=>p.severity==='error');const warnings=(preview?.problems||[]).filter(p=>p.severity==='warning');
 const staleBlocked=!!preview?.stale&&acceptStale.trim().toLowerCase()!=='publish';
 const publish=async()=>{if(!preview)return;setBusy(true);setError('');try{const result=await api<Row>(`/d/${project.id}/publish`,{revision:preview.revision,hash:preview.hash,announce,reset_world_state:reset,accept_stale:!!preview.stale});onPublished(result);}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
 const changes=preview?.changes||{};
 return <div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget&&!busy)onClose();}}><div role="dialog" aria-modal="true" aria-label="Publish world" className="modal wide publish-dialog">
  <div className="modal-header"><h2>{preview?.mode==='install'?'Install this world':'Publish to the live world'}</h2><button className="icon" title="Close dialog" aria-label="Close dialog" disabled={busy} onClick={onClose}><X size={18}/></button></div>
  {!preview&&!error&&<p className="muted">Checking the draft against the engine…</p>}
  {preview&&<>
   {preview.mode==='install'&&<p className="muted">The world is installed <strong>hidden</strong>. {admin?'Enable it for players in SYSOP Settings → MUD worlds when you are ready.':'A SYSOP enables it for players in Settings when it is ready.'}</p>}
   {preview.mode==='light'&&preview.edits===0&&<p className="muted"><CheckCircle2 size={14}/> The draft matches the published world. There is nothing to publish.</p>}
   {preview.mode==='light'&&preview.edits>0&&<p className="muted"><CheckCircle2 size={14}/> Only text and numbers changed ({preview.edits} edit{preview.edits===1?'':'s'}). They are applied in place: items on the ground, characters, NPCs and dungeon runs are untouched.</p>}
   {preview.mode==='full'&&<>
    <p className="muted">This draft adds, removes, or rewires content, so the world is <strong>upgraded</strong>. Characters keep their level, quests, reputation and possessions; anything standing in a removed room moves to the start.</p>
    {preview.impact&&<ul className="publish-impact"><li>{preview.characters} character{preview.characters===1?'':'s'} in this world</li><li>{preview.moved_to_start} would move to the starting room</li><li>{preview.impact.room_items} item{preview.impact.room_items===1?'':'s'} lying in rooms are reset to the authored placements</li><li>{preview.impact.dungeon_runs} active dungeon run{preview.impact.dungeon_runs===1?'':'s'} end</li><li>NPCs return to their authored rooms</li></ul>}
    <label className="check"><input type="checkbox" checked={reset} onChange={e=>setReset(e.target.checked)}/>Also reset world flags, trigger counts and object states</label>
   </>}
   {preview.home&&preview.first_home_publish&&preview.mode==='full'&&<div className="hint-box"><AlertTriangle size={15}/>This is the home world's first full publish. It converts the home world's internal IDs to package IDs, carrying player progress across. Take a station backup first if you have not recently.</div>}
   {Object.keys(changes).length>0&&<div className="publish-changes"><h4>Changes</h4>{Object.entries(changes).map(([section,c]:[string,any])=><div key={section}><strong>{sectionName(section)}</strong>{c.added.length>0&&<span className="positive">+{c.added.length} added</span>}{c.changed.length>0&&<span>~{c.changed.length} changed</span>}{c.removed.length>0&&<span className="negative">−{c.removed.length} removed</span>}</div>)}</div>}
   {preview.requires_admin?.length>0&&<div className={'hint-box '+(preview.allowed?'':'error-banner')}><AlertTriangle size={15}/>{preview.allowed?'This publish changes SYSOP-only settings':'Only a SYSOP can publish changes to'}: {preview.requires_admin.map((name:string)=>name.replace('_',' ')).join(', ')}.</div>}
   {preview.stale&&<div className="error-banner"><AlertTriangle size={15}/> The published world changed since this draft was started (for example by a ZIP upgrade in Settings). Publishing replaces those changes. <button className="text-button" onClick={onRebase}>Rebase the draft instead</button><label className="dialog-field">Type <code>publish</code> to replace them anyway<input value={acceptStale} onChange={e=>setAcceptStale(e.target.value)}/></label></div>}
   {(errors.length>0||warnings.length>0)&&<div className="issues-list compact">{[...errors,...warnings].slice(0,30).map((p,i)=><div className={'issue '+p.severity} key={i}><AlertTriangle size={14}/><div><strong>{p.message}</strong><code>{p.file} {p.path}</code></div></div>)}</div>}
   {preview.error&&<div className="error-banner">{preview.error}</div>}
   {preview.mode!=='install'&&!(preview.mode==='light'&&preview.edits===0)&&<label className="check"><input type="checkbox" checked={announce} onChange={e=>setAnnounce(e.target.checked)}/>Tell players in this world that it changed</label>}
  </>}
  {error&&<div className="error-banner">{error}</div>}
  <div className="modal-actions"><button onClick={onClose} disabled={busy}>Cancel</button><button className="primary" disabled={!preview||preview.blocked||busy||staleBlocked||(preview.mode==='light'&&preview.edits===0)} onClick={publish}><Rocket size={15}/>{busy?'Publishing…':preview?.mode==='install'?'Install world':'Publish'}</button></div>
 </div></div>;
}
