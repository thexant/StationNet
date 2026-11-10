from __future__ import annotations
import bisect
import copy
import json
import re
from collections import defaultdict, deque
from .schema import COLLECTIONS, FILES, REFS, EFFECTS, ABILITIES, CONDITIONS, ACTIONS, EVENTS, DIRS, REQUIRED

ID = re.compile(r'^[a-z][a-z0-9-]{0,63}$')
DELTA={'north':(0,-1),'northeast':(1,-1),'east':(1,0),'southeast':(1,1),'south':(0,1),'southwest':(-1,1),'west':(-1,0),'northwest':(-1,-1)}
OPPOSITE={'north':'south','south':'north','east':'west','west':'east','northeast':'southwest','southwest':'northeast','northwest':'southeast','southeast':'northwest','up':'down','down':'up','in':'out','out':'in'}

class WorldError(ValueError):pass

def records(files,key,create=False):
 c=COLLECTIONS[key];f=c['file'];path=c['path']
 if path is None:return [files.get(f,{})]
 if f not in files:
  if not create:return []
  files[f]=[] if path==[] else {}
 value=files[f]
 # Legacy skill packs may use a bare list.
 if isinstance(value,list):return value
 for k in path:
  if not isinstance(value,dict):return []
  if create:value=value.setdefault(k,[])
  else:value=value.get(k,[])
 return value if isinstance(value,list) else []

def position(r):
 vals=[r.get(k) for k in ('x','y','z')]
 return tuple(vals) if all(type(x) is int for x in vals) else None

def cell_id(x,y,z):return f'@{x},{y},{z}'
def parse_cell(value):
 if not isinstance(value,str) or not re.fullmatch(r'@-?\d+,-?\d+,-?\d+',value):return None
 return tuple(map(int,value[1:].split(',')))

def decode_runs(row):
 if not isinstance(row,str):raise WorldError('Terrain rows must be run-length encoded strings.')
 out=[]
 if not row:return out
 for token in row.split(','):
  parts=token.split('*')
  if len(parts)>2:raise WorldError('Invalid terrain run.')
  try:count,index=(int(parts[0]),int(parts[1])) if len(parts)==2 else (1,int(parts[0]))
  except ValueError:raise WorldError('Invalid terrain run.')
  if count<=0 or index<0:raise WorldError('Terrain runs require a positive count and non-negative palette index.')
  if out and out[-1][1]==index:out[-1]=(out[-1][0]+count,index)
  else:out.append((count,index))
 return out

def encode_runs(runs):
 merged=[]
 for count,index in runs:
  if count<=0:continue
  if merged and merged[-1][1]==index:merged[-1]=(merged[-1][0]+count,index)
  else:merged.append((count,index))
 while merged and merged[-1][1]==0:merged.pop()
 return ','.join(f'{n}*{i}' if n!=1 else str(i) for n,i in merged)

def replace_span(runs,start,end,value,width):
 """Replace inclusive-exclusive interval without expanding its cells."""
 total=sum(n for n,_ in runs)
 runs=runs+([(width-total,0)] if total<width else [])
 result=[];p=0
 for n,i in runs:
  q=p+n
  if p<start:result.append((max(0,min(q,start)-p),i))
  if p<end and q>start:result.append((min(q,end)-max(p,start),value))
  if q>end:result.append((q-max(p,end),i))
  p=q
 return decode_runs(encode_runs(result))

class WorldIndex:
 def __init__(self,files):
  self.files=files;self.rooms={r.get('id'):r for r in records(files,'rooms') if isinstance(r,dict)}
  self.coords={position(r):r for r in self.rooms.values() if position(r) is not None}
  self.layers={l.get('z'):l for l in records(files,'terrain_layers') if isinstance(l,dict)}
  self.presets={p.get('key'):p for p in records(files,'presets') if isinstance(p,dict)}
  self.exceptions={position(c):c for c in records(files,'cells') if isinstance(c,dict)}
  self.out=defaultdict(list)
  for e in records(files,'exits'):
   if isinstance(e,dict):self.out[e.get('from')].append(e)
  self.rows={}

 def terrain(self,x,y,z):
  l=self.layers.get(z)
  if not l:return None
  ox,oy=l.get('origin',[0,0]);rx=x-ox;ry=y-oy
  if not 0<=rx<l.get('width',0) or not 0<=ry<l.get('height',0):return None
  key=(z,ry)
  if key not in self.rows:
   raw=l.get('rows',[]);runs=decode_runs(raw[ry] if ry<len(raw) else '')
   end=[];paints=[];p=0
   for n,i in runs:p+=n;end.append(p);paints.append(i)
   self.rows[key]=(end,paints)
  ends,paints=self.rows[key];i=bisect.bisect_right(ends,rx)
  pi=paints[i] if i<len(paints) else 0
  palette=l.get('palette',[])
  if pi>=len(palette):return None
  paint=palette[pi]
  if not paint:return None
  preset,_,region=paint.partition('@')
  return preset,region

 def at(self,x,y,z):
  if (x,y,z) in self.coords:return self.coords[x,y,z]
  paint=self.terrain(x,y,z)
  if not paint:return None
  preset,region=paint;p=self.presets.get(preset,{})
  return {'id':cell_id(x,y,z),'x':x,'y':y,'z':z,'name':p.get('name',preset),'description':description(p,x,y,z),'preset':preset,'region':region,'terrain':p.get('terrain','custom'),'glyph':p.get('glyph','.'),'links':p.get('links','4'),'requires_mode':p.get('requires_mode'),**self.exceptions.get((x,y,z),{})}

 def get(self,rid):
  if rid in self.rooms:return self.rooms[rid]
  p=parse_cell(rid)
  return self.at(*p) if p else None

 def links(self,rid):
  r=self.get(rid)
  if not r:return []
  explicit=self.out.get(rid,[]);overrides={e.get('direction') for e in explicit};result=[]
  p=position(r)
  if p and r.get('grid_link',True):
   for d,(dx,dy) in DELTA.items():
    if d in overrides or d in r.get('blocked_dirs','').split(','):continue
    other=self.at(p[0]+dx,p[1]+dy,p[2])
    if not other or other.get('grid_link',True) is False or OPPOSITE[d] in other.get('blocked_dirs','').split(','):continue
    if dx and dy and (('preset' in r and r.get('links','4')!='8') or ('preset' in other and other.get('links','4')!='8')):continue
    result.append({'from':rid,'to':other['id'],'direction':d,'name':d,'automatic':True,'requires_mode':other.get('requires_mode')})
  result.extend(dict(e,automatic=False) for e in explicit)
  return result

def description(p,x=0,y=0,z=0):
 # This is a text preview, not a reimplementation of StationNet's selection hash.
 values=p.get('descriptions',[])
 if not values:return ''
 v=values[0]
 return v.get('text','') if isinstance(v,dict) else str(v)

def target_for(field,parent,collection,path):
 if field in ['from','to']:return 'rooms' if collection=='exits' and len(path)==1 else None
 if field=='definition_id':return 'mud_npc_definitions' if collection=='mud_npc_instances' or (collection=='templates' and any(k in path for k in ['npcs','enemies'])) else 'mud_item_definitions'
 if field in ['target_id','objective_target_id']:
  typ=parent.get('objective_type',parent.get('reward_type'))
  return {'visit_room':'rooms','talk_npc':'mud_npc_definitions','defeat_npc':'mud_npc_definitions','possess_item':'mud_item_definitions','deliver_item':'mud_item_definitions','item':'mud_item_definitions','reputation':'mud_world_factions'}.get(typ)
 if field=='template' and collection=='entrances':return 'templates'
 if field=='key' and len(path)>1 and any(k in path for k in ['on_hit_effects','on_use_effects','effects','cures']):
  if isinstance(parent.get('key'),str) and not parent.get('type'):return 'effects'
 if field in ['skill','effect','npc','item','room','vehicle','property','faction','destination','trigger','object'] and parent.get('type') in ['race','class','tag']:return None
 if field=='key' and parent.get('type') in ['race','class']:return 'races' if parent['type']=='race' else 'classes'
 return REFS.get(field)

def references(files):
 result=[]
 def walk(value,col,index,path,json_root=None):
  def emit(use):
   if json_root is not None:use['json_root']=json_root
   result.append(use)
  if isinstance(value,dict):
   for k,v in value.items():
    p=path+[k];target=target_for(k,value,col,p)
    if col=='templates':
     theme=records(files,col)[index].get('template',{});defs=theme.get('definitions',{}) if isinstance(theme,dict) else {}
     if k in ['node_id','next_node_id','conversation_id'] and 'conversation' in path:target=None
     if k in ['item','npc'] and isinstance(v,str) and any(d.get('id')==v for category in (['items'] if k=='item' else ['npcs','enemies']) for d in defs.get(category,[]) if isinstance(d,dict)):target=None
    if target and isinstance(v,str) and v:emit({'collection':col,'index':index,'path':p,'target':target,'value':v})
    if k in ['skills','skill_affinities','gifts'] and isinstance(v,dict):
     for mapping_key in v:
      emit({'collection':col,'index':index,'path':p+[mapping_key],'target':'mud_item_definitions' if k=='gifts' else 'skills','value':mapping_key,'mapping_key':True})
    if isinstance(v,list) and k in ['spells','map_items_json','races','classes','skills']:
     target={'spells':'abilities','map_items_json':'mud_item_definitions','races':'races','classes':'classes','skills':'skills'}[k]
     for i,item in enumerate(v):
      if isinstance(item,str) and item:emit({'collection':col,'index':index,'path':p+[i],'target':target,'value':item})
    if k=='keys' and value.get('type') in ['race','class','skill'] and isinstance(v,list):
     for i,item in enumerate(v):
      if isinstance(item,str):emit({'collection':col,'index':index,'path':p+[i],'target':{'race':'races','class':'classes','skill':'skills'}[value['type']],'value':item})
    if k in ['properties_json','behaviors_json','state_json'] and isinstance(v,str):
     try:
      parsed=json.loads(v)
      if isinstance(parsed,(dict,list)):walk(parsed,col,index,p,p)
     except ValueError:pass
    else:walk(v,col,index,p,json_root)
  elif isinstance(value,list):
   for i,v in enumerate(value):walk(v,col,index,path+[i],json_root)
 for col in COLLECTIONS:
  # Terrain rows/palette contain compact implicit references, handled separately.
  if col=='terrain_layers':continue
  for i,row in enumerate(records(files,col)):walk(row,col,i,[])
 return result

def set_path(row,path,value):
 current=row
 for part in path[:-1]:current=current[part]
 current[path[-1]]=value

def rename_refs(files,target,old,new):
 for use in references(files):
  if use['target']==target and use['value']==old:
   row=records(files,use['collection'])[use['index']];path=use['path'];root=use.get('json_root')
   if root is not None:
    raw=row
    for k in root:raw=raw[k]
    parsed=json.loads(raw);dest=parsed;path=path[len(root):]
   else:dest=row
   if use.get('mapping_key'):
    parent=dest
    for k in path[:-1]:parent=parent[k]
    if new in parent and new!=old:raise WorldError('Renaming would collide with an existing dictionary key.')
    rewritten={new if k==old else k:v for k,v in parent.items()};parent.clear();parent.update(rewritten)
   else:set_path(dest,path,new)
   if root is not None:set_path(row,root,json.dumps(parsed,ensure_ascii=False))
 if target in ['presets','regions']:
  for layer in records(files,'terrain_layers'):
   for i,paint in enumerate(layer.get('palette',[])):
    p,_,r=paint.partition('@')
    if target=='presets' and p==old:p=new
    if target=='regions' and r==old:r=new
    layer['palette'][i]=p+('@'+r if r else '')

def uses(files,target,value):
 result=[r for r in references(files) if r['target']==target and r['value']==value]
 if target in ['presets','regions']:
  for i,l in enumerate(records(files,'terrain_layers')):
   for j,p in enumerate(l.get('palette',[])):
    a,_,b=p.partition('@')
    if (a if target=='presets' else b)==value:result.append({'collection':'terrain_layers','index':i,'path':['palette',j],'target':target,'value':value})
 return result

def paint(files,args):
 z=int(args.get('z',0));x1,x2=sorted([int(args['x1']),int(args['x2'])]);y1,y2=sorted([int(args['y1']),int(args['y2'])])
 if (x2-x1+1)*(y2-y1+1)>250000:raise WorldError('One fill is limited to 250,000 squares.')
 preset=args.get('preset','');region=args.get('region','')
 if preset and preset not in {p.get('key') for p in records(files,'presets')}:raise WorldError('Choose an existing terrain preset.')
 if region and region not in {r.get('key') for r in records(files,'regions')}:raise WorldError('Choose an existing region.')
 index=WorldIndex(files);layers=records(files,'terrain_layers',True)
 layer=next((l for l in layers if l.get('z')==z),None)
 if not layer:
  if not preset:return
  layer={'z':z,'origin':[x1,y1],'width':x2-x1+1,'height':y2-y1+1,'palette':[''],'rows':[]};layers.append(layer)
 ox,oy=layer['origin'];ow,oh=layer['width'],layer['height']
 nx,ny=min(x1,ox),min(y1,oy);nw=max(x2+1,ox+ow)-nx;nh=max(y2+1,oy+oh)-ny
 if nw>1000000 or nh>1000000:raise WorldError('Terrain layer bounds are too large.')
 # Preserve palette positions and row order; zero is explicitly the empty entry.
 palette=layer['palette']
 if not palette or palette[0]!='':
  palette.insert(0,'')
  layer['rows']=[encode_runs([(n,i+1) for n,i in decode_runs(row)]) for row in layer.get('rows',[])]
 paint_name=preset+('@'+region if preset and region else '')
 if paint_name not in palette:palette.append(paint_name)
 pi=palette.index(paint_name)
 oldrows=layer.get('rows',[]);newrows=[]
 authored={p for p in index.coords if p[2]==z}
 removed=[]
 for ay in range(ny,ny+nh):
  ry=ay-oy;raw=oldrows[ry] if 0<=ry<len(oldrows) else ''
  runs=([(ox-nx,0)] if ox>nx else [])+decode_runs(raw)
  if y1<=ay<=y2:
   a,b=x1,x2+1
   if args.get('shape')=='ellipse':
    cx=(x1+x2)/2;cy=(y1+y2)/2;rx=(x2-x1+1)/2;ryy=(y2-y1+1)/2
    half=rx*max(0,1-((ay-cy)/ryy)**2)**.5
    import math
    a=max(a,math.ceil(cx-half));b=min(b,math.floor(cx+half)+1)
   # Split only at authored cells and existing run boundaries, not per terrain cell.
   intervals=[(a,b)] if a<b else []
   for ax,yy,zz in authored:
    if yy==ay:intervals=[s for l,r in intervals for s in ([(l,min(r,ax))] if l<ax else [])+([(max(l,ax+1),r)] if r>ax+1 else []) if s[0]<s[1]]
   if args.get('empty_only') and preset:
    existing=[];p=ox
    for n,i in decode_runs(raw):
     if i<len(palette) and palette[i]:existing.append((p,p+n))
     p+=n
    for l,r in existing:
     intervals=[s for a,b in intervals for s in ([(a,min(b,l))] if a<l else [])+([(max(a,r),b)] if b>r else []) if s[0]<s[1]]
   for a,b in intervals:
    if not preset:removed.append((a,b,ay,z))
    runs=replace_span(runs,a-nx,b-nx,pi,nw)
  newrows.append(encode_runs(runs))
 if removed:
  for use in references(files):
   p=parse_cell(use['value'])
   if p and any(p[2]==zz and p[1]==yy and a<=p[0]<b for a,b,yy,zz in removed) and index.at(*p):raise WorldError('Terrain is referenced by content. Remove or relocate its references before erasing.')
 layer.update(origin=[nx,ny],width=nw,height=nh,rows=newrows)

def unique_id(files,col,base):
 c=COLLECTIONS[col];key=c['identity'][0] if c['identity'] else 'id';used={str(r.get(key)) for r in records(files,col) if isinstance(r,dict)}
 base=re.sub('[^a-z0-9-]+','-',base.lower()).strip('-') or 'record'
 if not base[0].isalpha():base='new-'+base
 base=base[:54];candidate=base;i=2
 while candidate in used:candidate=f'{base}-{i}';i+=1
 return candidate

def mutate(files,operation,args):
 files=copy.deepcopy(files);selected=None
 if operation=='patch_record':
  row=copy.deepcopy(records(files,args['collection'])[int(args.get('index',0))])
  for patch in args.get('patches',[]):
   path=patch.get('path',[])
   if not path:raise WorldError('A record patch needs a field path.')
   if patch.get('remove'):
    parent=row
    for part in path[:-1]:parent=parent[part]
    if isinstance(parent,list):parent.pop(path[-1])
    else:parent.pop(path[-1],None)
   else:set_path(row,path,patch.get('value'))
  operation='set_record';args={**args,'record':row}
 if operation=='set_field':
  row=copy.deepcopy(records(files,args['collection'])[int(args.get('index',0))]);path=args['path']
  if not path:raise WorldError('Choose a field to edit.')
  if args.get('remove'):
   parent=row
   for part in path[:-1]:parent=parent[part]
   if isinstance(parent,list):parent.pop(path[-1])
   else:parent.pop(path[-1],None)
  else:set_path(row,path,args.get('value'))
  operation='set_record';args={**args,'record':row}
 if operation in ['set_record','create_record','duplicate_record','delete_record']:
  col=args['collection'];c=COLLECTIONS[col];rows=records(files,col,True);i=int(args.get('index',0))
  if operation=='set_record':
   row=copy.deepcopy(args['record'])
   if not isinstance(row,dict):raise WorldError('A record must be a JSON object.')
   old=rows[i]
   if c['path'] is None:files[c['file']]=row
   else:rows[i]=row
   for key in c['identity']:
    if len(c['identity'])==1 and old.get(key)!=row.get(key) and old.get(key) is not None:rename_refs(files,col,old[key],row.get(key))
   if col=='rooms' and position(old) and position(old)!=position(row) and row.get('id'):rename_refs(files,'rooms',cell_id(*position(old)),row['id'])
   selected={'collection':col,'index':i}
  elif operation=='delete_record':
   if c['path'] is None:raise WorldError('World settings cannot be deleted.')
   value=rows[i].get(c['identity'][0]) if len(c['identity'])==1 else None
   refs=uses(files,col,value) if value is not None else []
   refs=[u for u in refs if not (u['collection']==col and u['index']==i)]
   if refs:
    raise WorldError(f'This record has {len(refs)} reference(s). Resolve them in Used by before deleting.')
   rows.pop(i)
  else:
   row=copy.deepcopy(rows[i] if operation=='duplicate_record' else {**c['defaults'],**args.get('record',{})})
   if len(c['identity'])==1 and c['identity'][0] in ['id','key']:
    key=c['identity'][0];row[key]=unique_id(files,col,row.get(key,col.replace('mud_','').replace('_','-')))
   elif operation=='duplicate_record':raise WorldError('This link uses a composite or reference key. Create a new link with different references instead.')
   if operation=='duplicate_record' and col=='rooms':
    for k in ['x','y','z']:row.pop(k,None)
   if col=='manifest':raise WorldError('World settings already exist.')
   rows.append(row);selected={'collection':col,'index':len(rows)-1}
 elif operation=='place_room':
  row=records(files,'rooms')[int(args['index'])];new=tuple(int(args[k]) for k in ('x','y','z'));index=WorldIndex(files);other=index.at(*new)
  if other and other['id']!=row['id']:raise WorldError('This square is occupied. Convert terrain or choose an empty square.')
  old=copy.deepcopy(row);row.update(zip(('x','y','z'),new))
  # Newly placed authored rooms do not join the automatic grid until the builder turns grid links on.
  if not position(old):row.setdefault('grid_link',False)
  if position(old) and position(old)!=new:rename_refs(files,'rooms',cell_id(*position(old)),row['id'])
  check_cross_layers(files)
  selected={'collection':'rooms','index':int(args['index'])}
 elif operation=='unplace_room':
  row=records(files,'rooms')[int(args['index'])]
  for k in ('x','y','z','grid_link','blocked_dirs'):row.pop(k,None)
 elif operation=='connect':
  a,b=args['from'],args['to'];index=WorldIndex(files)
  if not index.get(a) or not index.get(b):raise WorldError('Both passage endpoints must exist.')
  direction=args.get('direction') or None;name=args.get('name') or direction or 'passage'
  edges=[{'from':a,'to':b,'name':name}]
  if direction:edges[0]['direction']=direction
  if args.get('reciprocal',True):
   if not direction:raise WorldError('Choose a direction for a reciprocal pair, or create a named one-way passage.')
   edges.append({'from':b,'to':a,'name':OPPOSITE[direction],'direction':OPPOSITE[direction]})
  exits=records(files,'exits',True)
  for edge in edges:
   if any(e.get('from')==edge['from'] and (e.get('name')==edge['name'] or (edge.get('direction') and e.get('direction')==edge['direction'])) for e in exits):raise WorldError('A passage with this name or direction already leaves the room.')
   exits.append(edge)
  check_cross_layers(files)
 elif operation=='paint':paint(files,args)
 elif operation=='paint_stroke':
  points=args.get('points',[])
  if len(points)>1000:raise WorldError('A brush stroke is limited to 1,000 squares.')
  for x,y in dict.fromkeys(tuple(p) for p in points):paint(files,{**args,'x1':x,'x2':x,'y1':y,'y2':y})
 elif operation=='convert_cell':
  p=tuple(int(args[k]) for k in ('x','y','z'));index=WorldIndex(files);r=index.at(*p)
  if not r or 'preset' not in r:raise WorldError('Select a wilderness square to convert.')
  old=r['id'];new=unique_id(files,'rooms',args.get('id','landmark'));rename_refs(files,'rooms',old,new)
  # Temporarily remove the compact cell without treating its remapped references as dependents.
  paint(files,dict(z=p[2],x1=p[0],x2=p[0],y1=p[1],y2=p[1],preset=''))
  room={k:r[k] for k in ['x','y','z','name','description','grid_link','blocked_dirs'] if k in r}
  room.update(id=new,grid_link=r.get('grid_link',True));records(files,'rooms',True).append(room)
  cells=records(files,'cells')
  cells[:]=[c for c in cells if position(c)!=p]
  selected={'collection':'rooms','index':len(records(files,'rooms'))-1}
 elif operation=='set_walls':
  p=tuple(int(args[k]) for k in ('x','y','z'));index=WorldIndex(files);r=index.at(*p)
  if not r:raise WorldError('Choose an occupied square.')
  if r['id'] in index.rooms:r=index.rooms[r['id']]
  else:
   cells=records(files,'cells',True);r=next((c for c in cells if position(c)==p),None)
   if r is None:r=dict(zip(('x','y','z'),p));cells.append(r)
  r['blocked_dirs']=','.join(d for d in DELTA if d in args.get('directions',[]))
 elif operation=='upgrade_format':files['manifest.json']['format_version']=2
 else:raise WorldError('Unknown editor operation.')
 # Coordinates and cross-layer restrictions must never be introduced by an editor operation.
 if operation in ['place_room','set_record','create_record','duplicate_record']:
  seen={};index=WorldIndex(files)
  for r in records(files,'rooms'):
   p=position(r)
   if p is not None:
    if p in seen or index.terrain(*p):raise WorldError('Two rooms cannot occupy the same square.')
    seen[p]=r.get('id')
  check_cross_layers(files)
 return files,selected

def check_cross_layers(files):
 index=WorldIndex(files)
 for e in records(files,'exits'):
  a=index.get(e.get('from'));b=index.get(e.get('to'))
  if a and b and position(a) and position(b) and a['z']!=b['z'] and e.get('direction') not in ['up','down']:raise WorldError('Passages between coordinate layers must use up or down.')

RANGES={
 'level_cap':(1,50),'experience_percent':(10,500),'health_per_level':(1,30),'mana_per_level':(0,10),'starting_coins':(0,100000),
 'maximum_health':(1,1000000),'attack':(1,100000),'defense':(0,100000),'experience_reward':(0,100000),
 'accuracy':(0,100),'evasion':(0,100),'chance_percent':(1,100),'pick_difficulty':(1,200),'required_count':(1,100),
 'potency':(1,1000),'duration':(1,100),'cost':(0,100),'capacity':(1,500),'max_owners':(1,20),'max_party':(1,20),
 'speed':(25,400),'magazine_size':(1,100),'rounds':(1,1000),'day_minutes':(5,1440),'start_hour':(0,23),
 'max_holdings':(1,20),'prepay_periods':(1,52),'refund_percent':(0,100),'sell_percent':(0,100),
 'travel_seconds':(5,86400),'time_limit_seconds':(60,2592000),'retry_cooldown_seconds':(0,2592000),
}

def validate(files,known=None):
 # known: station catalog keys by reference target (installed races, classes, skills, effects, abilities).
 known=known or {}
 issues=[];manifest_value=files.get('manifest.json',{});legacy=isinstance(manifest_value,dict) and manifest_value.get('format_version',1)==1
 def issue(col,i,path,message,category='invalid',severity='error'):
  c=COLLECTIONS.get(col,COLLECTIONS['manifest']);base='$'
  if c['path'] is not None:
   for part in c['path']:base+='.'+part
   base+=f'[{i}]'
  for p in path:base+=f'[{p}]' if isinstance(p,int) else '.'+str(p)
  issues.append({'severity':severity,'category':category,'file':c['file'],'path':base,'message':message,'collection':col,'index':i,'field':path[0] if path else None})
 malformed=False
 for name,data in files.items():
  expect_list=name in ['rooms.json','exits.json'] or (name=='skills.json' and isinstance(data,list))
  if not isinstance(data,list if expect_list else dict):
   col=next((k for k,c in COLLECTIONS.items() if c['file']==name),'manifest');issue(col,0,[],f'{name} has an invalid JSON root type.');malformed=True;continue
  for col,c in COLLECTIONS.items():
   if c['file']!=name:continue
   if c['path'] is not None:
    v=data if isinstance(data,list) else data.get(c['path'][0],[]) if c['path'] else data
    if not isinstance(v,list):issue(col,0,[],'Collection must be an array.');malformed=True
    elif any(not isinstance(row,dict) for row in v):issue(col,0,[],'Each collection entry must be an object.');malformed=True
 if malformed:return issues
 for file in FILES[:3]:
  if file not in files:issue('manifest',0,[],f'Required package file {file} is missing.')
 for f,value in files.items():
  if len(json.dumps(value,ensure_ascii=False).encode('utf-8'))>8*1024*1024:issue('manifest',0,[],f'{f} exceeds the 8 MB file limit.')
 manifest=files.get('manifest.json',{})
 if manifest.get('format_version',1) not in [1,2]:issue('manifest',0,['format_version'],'Unsupported package format.','unsupported')
 if manifest.get('id')=='stationnet':issue('manifest',0,['id'],'stationnet is reserved.','engine_reserved')
 if not records(files,'rooms'):issue('rooms',0,[],'A world needs at least one authored room.')
 if len(records(files,'rooms'))>1000:issue('rooms',0,[],'At most 1,000 authored rooms are supported.')
 if len(records(files,'exits'))>5000:issue('exits',0,[],'At most 5,000 explicit exits are supported.')
 for file,keyset in [('content.json',{k for k,c in COLLECTIONS.items() if c['file']=='content.json'})]:
  data=files.get(file,{})
  if isinstance(data,dict):
   for key in data:
    if key not in keyset:issue('manifest',0,[],f'Unsupported content table: {key}.','unsupported','warning' if legacy else 'error')
 for key,c in COLLECTIONS.items():
  raw=files.get(c['file'])
  if raw is not None:
   valid_container=isinstance(raw,dict) if c['path'] is None or c['path'] else isinstance(raw,list)
   if c['file']=='skills.json' and isinstance(raw,list):valid_container=True
   if not valid_container:issue(key,0,[],'Incorrect JSON root type.');continue
   if isinstance(raw,dict) and c['path'] and c['path'][0] in raw and not isinstance(raw[c['path'][0]],list):issue(key,0,[],'Collection must be an array.');continue
  rows=records(files,key);seen=set()
  if c['file']=='content.json' and len(rows)>5000:issue(key,0,[],'At most 5,000 records per content array are supported.')
  for i,r in enumerate(rows):
   if not isinstance(r,dict):issue(key,i,[],'Record must be an object.');continue
   ident=tuple(r.get(k) for k in c['identity'])
   if c['identity'] and all(v is not None for v in ident):
    safe=json.dumps(ident,sort_keys=True)
    if safe in seen:issue(key,i,[],'Duplicate record identity.')
    seen.add(safe)
   for f,v in r.items():
    schema=c['fields'].get(f)
    if not schema:
     reserved=key=='manifest' and f in ['max_level','attributes','resistances','directions','equipment_slots','combat']
     issue(key,i,[f],'Engine rules cannot be changed by a world package.' if reserved else 'Field is not documented for this record.','engine_reserved' if reserved else 'unsupported','warning' if legacy else 'error');continue
    if v is None:continue
    typ=schema.get('type');ok={'string':isinstance(v,str),'number':type(v) in (int,float),'boolean':isinstance(v,bool),'object':isinstance(v,dict),'array':isinstance(v,list)}.get(typ,True)
    if not ok and not schema.get('flexible'):issue(key,i,[f],f'Expected {typ}.')
    if 'enum' in schema and v not in schema['enum']:issue(key,i,[f],'Choose a supported value.')
    if f in ['id','key'] and isinstance(v,str) and not ID.fullmatch(v):issue(key,i,[f],'Use 1–64 lowercase letters, digits, and hyphens, beginning with a letter.')
   for required in REQUIRED.get(key,[]):
    if r.get(required) in [None,'',[]]:issue(key,i,[required],f'{required.replace("_"," ").title()} is required.')
   if 'name' in r and key not in ['exits','entrances'] and isinstance(r['name'],str) and not 2<=len(r['name'])<=80:issue(key,i,['name'],'Name must be 2–80 characters.')
   if 'description' in r and isinstance(r['description'],str) and len(r['description'])>4000:issue(key,i,['description'],'Description exceeds 4,000 characters.')
   if key=='rooms' and any(k in r for k in ['x','y','z']) and position(r) is None:issue(key,i,['x'],'Supply all three coordinates as whole numbers.')
   def check_nested(v,path):
    if isinstance(v,dict):
     for f,x in v.items():
      if f in RANGES and x is not None and type(x) in (int,float) and (not path or f in ['potency','duration','magazine_size','rounds'] or (key=='manifest' and path==['progression'])):
       lo,hi=RANGES[f]
       if not lo<=x<=hi:issue(key,i,path+[f],f'{f} must be between {lo} and {hi}.')
      if f in ['conditions','conditions_json','join_conditions_json','start_conditions_json'] and isinstance(x,list):
       if len(x)>20:issue(key,i,path+[f],'At most 20 conditions are supported.')
       for j,cond in enumerate(x):
        if not isinstance(cond,dict):issue(key,i,path+[f,j],'Condition must be an object.');continue
        if 'any' not in cond and cond.get('type') not in CONDITIONS:issue(key,i,path+[f,j],'Unsupported condition type.','unsupported')
        if 'any' in cond and any(isinstance(a,dict) and 'any' in a for a in cond['any']):issue(key,i,path+[f,j],'Any groups cannot be nested.')
      if f in ['actions','outcomes_json'] and isinstance(x,list):
       if len(x)>10 or (f=='actions' and not x):issue(key,i,path+[f],'Actions require 1–10 entries; outcomes allow up to 10.')
       for j,a in enumerate(x):
        if not isinstance(a,dict) or a.get('type') not in ACTIONS:issue(key,i,path+[f,j],'Unsupported action type.','unsupported')
      check_nested(x,path+[f])
    elif isinstance(v,list):
     for j,x in enumerate(v):check_nested(x,path+[j])
   check_nested(r,[])
 index=WorldIndex(files)
 def exists(target,value):
  if target=='rooms':return bool(index.get(value))
  if value in known.get(target,()):return True
  if value.startswith('stationnet:'):return True
  if target=='effects' and value in EFFECTS:return True
  if target=='abilities' and value in ABILITIES:return True
  return any(r.get('key' if target in ['races','classes','skills','abilities','effects','templates','presets','regions'] else 'id')==value for r in records(files,target) if isinstance(r,dict))
 for use in references(files):
  # Equipment origin requirements may intentionally name choices installed later.
  if use['target'] in ['races','classes'] and ('requirements' in use['path'] or 'availability' in use['path']):continue
  try:found=exists(use['target'],use['value'])
  except (WorldError,TypeError,KeyError):found=False
  if not found:
   external=use['target'] in ['races','classes','skills']
   issue(use['collection'],use['index'],use['path'],f'Missing {use["target"].replace("mud_","").replace("_"," ")}: {use["value"]}.','missing_reference','warning' if external else 'error')
 coords={}
 for i,r in enumerate(records(files,'rooms')):
  if not isinstance(r,dict):continue
  p=position(r)
  if not p and any(k in r for k in ('grid_link','blocked_dirs')):
   issue('rooms',i,['grid_link' if 'grid_link' in r else 'blocked_dirs'],'Grid links and walls need x, y and z; place the room on the map or remove them.')
  if p:
   try:collision=p in coords or bool(index.terrain(*p))
   except (WorldError,TypeError,KeyError):collision=False
   if collision:issue('rooms',i,['x'],'Coordinate is already occupied.')
   coords[p]=r.get('id')
 names=set();directions=set()
 for i,e in enumerate(records(files,'exits')):
  if not isinstance(e,dict):continue
  for k,seen in [('name',names),('direction',directions)]:
   if e.get(k):
    v=e.get('from'),e[k]
    if v in seen:issue('exits',i,[k],f'Duplicate exit {k} from this room.')
    seen.add(v)
  a=index.get(e.get('from'));b=index.get(e.get('to'))
  if a and b and position(a) and position(b) and a['z']!=b['z'] and e.get('direction') not in ['up','down']:issue('exits',i,['direction'],'Cross-layer exits require up or down.')
  if e.get('requires_mode') and e.get('allow_vehicles') is False:issue('exits',i,['requires_mode'],'A passage cannot require a vehicle mode and disallow vehicles.')
 total=0;run_count=0;zs=set()
 for i,l in enumerate(records(files,'terrain_layers')):
  if not isinstance(l,dict):continue
  try:
   if l['z'] in zs:raise WorldError('Only one wilderness layer per z is supported.')
   zs.add(l['z']);pal=l['palette'];presets={p.get('key') for p in records(files,'presets')};regions={p.get('key') for p in records(files,'regions')}
   if l['width']<1 or l['height']<1:raise WorldError('Layer dimensions must be positive.')
   if len(l.get('rows',[]))>l['height']:raise WorldError('Layer has too many rows.')
   for p in pal:
    if not p:continue
    a,_,b=p.partition('@')
    if a not in presets or (b and b not in regions):raise WorldError('Palette references a missing preset or region.')
   for row in l.get('rows',[]):
    runs=decode_runs(row);run_count+=len(runs)
    if sum(n for n,_ in runs)>l['width']:raise WorldError('Terrain row exceeds layer width.')
    for n,p in runs:
     if p>=len(pal):raise WorldError('Palette index is out of range.')
     if pal[p]:total+=n
  except (KeyError,TypeError,WorldError) as e:issue('terrain_layers',i,[],str(e))
 if total>1000000:issue('terrain_layers',0,[],'At most 1,000,000 wilderness rooms are supported.')
 if run_count>200000:issue('terrain_layers',0,[],'At most 200,000 wilderness runs are supported.')
 for col,limit in [('presets',200),('regions',200),('terrain_layers',64)]:
  if len(records(files,col))>limit:issue(col,0,[],f'At most {limit} entries are supported.')
 for i,p in enumerate(records(files,'presets')):
  if not p.get('descriptions'):issue('presets',i,['descriptions'],'At least one description is required.')
  if len(p.get('key',''))>32:issue('presets',i,['key'],'Preset keys have at most 32 characters.')
  g=p.get('glyph','.')
  if not isinstance(g,str) or len(g)!=1 or not g.isprintable() or g in ' @*P<>&':issue('presets',i,['glyph'],'Choose a single printable non-reserved glyph.')
 # Dialogue starts, unique node keys, and links must stay within a conversation.
 nodes=records(files,'mud_dialogue_nodes');nodeidx={n.get('id'):n for n in nodes if isinstance(n,dict)}
 for i,c in enumerate(records(files,'mud_conversations')):
  related=[n for n in nodes if n.get('conversation_id')==c.get('id')]
  if sum(bool(n.get('is_start')) for n in related)!=1:issue('mud_conversations',i,[],'Conversation needs exactly one start node.')
  if len({n.get('node_key') for n in related})!=len(related):issue('mud_conversations',i,[],'Node keys must be unique within a conversation.')
 for i,r in enumerate(records(files,'mud_dialogue_responses')):
  a=nodeidx.get(r.get('node_id'));b=nodeidx.get(r.get('next_node_id'))
  if a and b and a.get('conversation_id')!=b.get('conversation_id'):issue('mud_dialogue_responses',i,['next_node_id'],'Dialogue links must remain in the same conversation.')
 def cycles(col,graph):
  done=set();active=set()
  def visit(node):
   if node in active:return True
   if node in done:return False
   active.add(node)
   cyclic=any(visit(other) for other in graph.get(node,[]))
   active.remove(node);done.add(node);return cyclic
  for i,row in enumerate(records(files,col)):
   if visit(row.get('id')):issue(col,i,[],'Reference chain contains a cycle.')
 triggers=records(files,'triggers');tidx={t.get('id'):t for t in triggers}
 graph={t.get('id'):[a.get('trigger') for a in t.get('actions',[]) if a.get('type')=='run_trigger'] for t in triggers}
 cycles('triggers',graph)
 for i,t in enumerate(triggers):
  if t.get('event') not in EVENTS:issue('triggers',i,['event'],'Unsupported event.','unsupported')
  if t.get('event')=='timer':
   for cond in t.get('conditions',[]):
    if cond.get('type') not in ['flag','object_state','time'] or (cond.get('type')=='flag' and cond.get('scope','player')!='world'):issue('triggers',i,['conditions'],'Timers can only check world flags, object state, or time.')
  for a in t.get('actions',[]):
   if a.get('type')=='run_trigger' and a.get('trigger') in tidx and tidx[a['trigger']].get('event')!='manual':issue('triggers',i,['actions'],'run_trigger must target a manual trigger.')
 cycles('mud_quests',{q.get('id'):[q['prerequisite_quest_id']] if q.get('prerequisite_quest_id') else [] for q in records(files,'mud_quests')})
 # Structural world-system requirements.
 property_rooms=records(files,'mud_property_rooms');private={r.get('room_id') for r in property_rooms}
 for i,p in enumerate(records(files,'mud_properties')):
  if not any(r.get('property_id')==p.get('id') for r in property_rooms):issue('mud_properties',i,[],'A property needs at least one room.')
  if p.get('entrance_room_id') in private:issue('mud_properties',i,['entrance_room_id'],'A property entrance must be outside property rooms.')
 if manifest.get('start_room') in private:issue('manifest',0,['start_room'],'The starting room cannot belong to a property.')
 for i,p in enumerate(records(files,'mud_storage_points')):
  if p.get('scope')=='property' and p.get('room_id') not in private:issue('mud_storage_points',i,['scope'],'Property storage must be in a property room.')
 for i,l in enumerate(records(files,'mud_transit_lines')):
  if sum(s.get('line_id')==l.get('id') for s in records(files,'mud_transit_stops'))<2:issue('mud_transit_lines',i,[],'A transit line needs at least two stops.')
 for col in ['mud_properties','mud_vehicle_definitions','mud_transit_lines']:
  seen=set()
  for i,r in enumerate(records(files,col)):
   name=str(r.get('name','')).strip().casefold()
   if name and name in seen:issue(col,i,['name'],'Names must be unique within this category.')
   seen.add(name)
 for i,activity in enumerate(records(files,'mud_work_activities')):
  if activity.get('kind','immediate')=='immediate' and bool(activity.get('room_id'))==bool(activity.get('provider_npc_definition_id')):issue('mud_work_activities',i,[],'Immediate work needs exactly one room or provider NPC.')
  if activity.get('kind')=='assignment' and not activity.get('provider_npc_definition_id'):issue('mud_work_activities',i,['provider_npc_definition_id'],'Assignments need a provider NPC.')
 for i,o in enumerate(records(files,'mud_quest_objectives')):
  if o.get('objective_type')!='script' and not o.get('target_id'):issue('mud_quest_objectives',i,['target_id'],'This objective needs a target.')
  if o.get('outcome_key') and (o.get('optional') or o['outcome_key'] in ['success','failed']):issue('mud_quest_objectives',i,['outcome_key'],'Alternative outcomes cannot be optional or use reserved outcome keys.')
 for i,t in enumerate(records(files,'templates')):
  theme=t.get('template')
  if not isinstance(theme,dict):continue
  if theme.get('schema_version') not in [1,2]:issue('templates',i,['template','schema_version'],'Dungeon schema_version must be 1 or 2.')
  for forbidden in ['id','version','origins','skills','abilities','effects']:
   if forbidden in theme:issue('templates',i,['template',forbidden],'World dungeon templates use world definitions and cannot bundle this field.','unsupported')
  generation=theme.get('generation',{})
  if not isinstance(generation,dict):issue('templates',i,['template','generation'],'Generation must be an object.');continue
  for dial,lo,hi in [('size',2,500),('difficulty',1,1000)]:
   rng=generation.get(dial,{})
   values=[rng.get(k) for k in ['minimum','default','maximum']] if isinstance(rng,dict) else []
   if len(values)!=3 or not all(type(v) is int for v in values) or not lo<=values[0]<=values[1]<=values[2]<=hi:issue('templates',i,['template','generation',dial],f'{dial.title()} needs ordered integer minimum, default, and maximum between {lo} and {hi}.')
  if not generation.get('layouts'):issue('templates',i,['template','generation','layouts'],'Choose at least one dungeon layout.')
  pools=theme.get('pools',{})
  if not isinstance(pools,dict) or not pools.get('rooms'):issue('templates',i,['template','pools'],'Define at least one room pool.');continue
  definitions=theme.get('definitions',{})
  for category,entries in pools.items():
   if not isinstance(entries,list):continue
   ids=set()
   for pi,entry in enumerate(entries):
    if not isinstance(entry,dict):issue('templates',i,['template','pools',category,pi],'Pool entries must be objects.');continue
    if entry.get('id') in ids:issue('templates',i,['template','pools',category,pi],'Pool IDs must be unique.')
    ids.add(entry.get('id'))
    for choice in entry.get('choices',[]):
     local=choice.get('local_id') if isinstance(choice,dict) else None
     if local and not any(d.get('id')==local for d in definitions.get(category,[])):issue('templates',i,['template','pools',category,pi],f'Missing embedded {category} definition: {local}.','missing_reference')
    for f,target in [('npc_pool','npcs'),('enemy_pool','enemies'),('item_pool','items')]:
     if entry.get(f) and not any(p.get('id')==entry[f] for p in pools.get(target,[])):issue('templates',i,['template','pools',category,pi],f'Missing {target} pool: {entry[f]}.','missing_reference')
 for i,item in enumerate(records(files,'mud_item_instances')):
  if bool(item.get('room_id'))==bool(item.get('container_id')):issue('mud_item_instances',i,[],'Place the item in exactly one room or container.')
 for i,npc in enumerate(records(files,'mud_npc_definitions')):
  behavior=npc.get('behaviors_json') or {}
  if behavior.get('schedule') and (behavior.get('wander') or behavior.get('patrol')):issue('mud_npc_definitions',i,['behaviors_json'],'Schedules cannot be combined with wander or patrol.')
 # Unreachable authored-only components are advisory; dynamic gates can be intentional.
 if len(index.rooms)<=1000 and manifest.get('start_room') in index.rooms:
  graph=defaultdict(list)
  for r in index.rooms:
   try:graph[r]=[e['to'] for e in index.links(r)]
   except WorldError:pass
  seen={manifest['start_room']};q=deque(seen)
  while q:
   for b in graph[q.popleft()]:
    if b not in seen:seen.add(b);q.append(b)
  # Wilderness traversal is intentionally not expanded for this static check.
  for i,r in enumerate(records(files,'rooms')):
   if r.get('id') not in seen and not position(r):issue('rooms',i,[],'No authored-room route from the start; wilderness, teleports, or private access may connect it.','unreachable','warning')
 return issues
