"""Read-only schematic data. Diagram positions never enter a world package."""
from .world import WorldIndex, records, position


def connection_graph(files):
 index=WorldIndex(files)
 nodes={}
 def include(rid,room_index=None):
  if not isinstance(rid,str) or not rid or rid in nodes:return
  room=index.get(rid)
  nodes[rid]={'id':rid,'name':room.get('name',rid) if room else rid,
   'roomIndex':room_index,'gamePosition':position(room) if room else None,
   'kind':'authored' if rid in index.rooms else 'wilderness' if room else 'missing',
   'boundary':False,
   **({k:room[k] for k in ['preset','region','terrain','glyph'] if k in room} if room else {})}
 for i,room in enumerate(records(files,'rooms')):
  if isinstance(room,dict):include(room.get('id'),i)
 exits=records(files,'exits')
 for edge in exits:
  if isinstance(edge,dict):
   include(edge.get('from'));include(edge.get('to'))
 # Exactly one ring of automatic neighbors; never expand the wilderness recursively.
 for rid in list(nodes):
  for edge in index.links(rid):
   if edge.get('automatic'):include(edge.get('to'))
 edges=[dict(edge,automatic=False,exitIndex=i) for i,edge in enumerate(exits)
        if isinstance(edge,dict) and edge.get('from') in nodes and edge.get('to') in nodes]
 for rid,node in nodes.items():
  for edge in index.links(rid):
   if not edge.get('automatic'):continue
   if edge.get('to') in nodes:edges.append(dict(edge,exitIndex=None))
   else:node['boundary']=True
 associations=[]
 for collection,field,kind in [('mud_transit_lines','cabin_room_id','Transit cabin'),
                                ('mud_vehicle_definitions','interior_room_id','Vehicle interior')]:
  for i,owner in enumerate(records(files,collection)):
   if isinstance(owner,dict) and owner.get(field) in nodes:
    associations.append({'room':owner[field],'kind':kind,'name':owner.get('name',owner.get('id','Transport')),
                         'collection':collection,'index':i})
 return {'nodes':list(nodes.values()),'edges':edges,'associations':associations}
