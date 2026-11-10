"""World Forge's read-only connection diagram, ported from the standalone editor's suite."""
import copy

from stationnet.world_forge.connections import connection_graph
from stationnet.world_forge.world import validate


def example():
 return {'manifest.json':{'format_version':2,'id':'test-world','name':'Test world','author':'Tester','start_room':'arrival'},
  'rooms.json':[{'id':'arrival','name':'Arrival','description':'Welcome.','x':0,'y':0,'z':0,'grid_link':False},
                {'id':'inside','name':'Inside','description':'An interior.'},
                {'id':'above','name':'Above','description':'Upper floor.','x':0,'y':0,'z':1,'grid_link':False}],
  'exits.json':[{'from':'arrival','to':'inside','name':'front door'},
                {'from':'inside','to':'arrival','name':'outside','direction':'out'},
                {'from':'arrival','to':'above','name':'ladder','direction':'up','hidden':True,
                 'key_item_id':'key','conditions':[{'type':'tag','keys':['climber']}],'requires_mode':'air'},
                {'from':'above','to':'above','name':'circle'}]}


def test_connections_preserve_rooms_one_way_named_cycles_and_restrictions():
 files=example();before=copy.deepcopy(files);graph=connection_graph(files)
 assert files==before
 assert [n['id'] for n in graph['nodes']]==['arrival','inside','above']
 assert graph['nodes'][1]['gamePosition'] is None and graph['nodes'][1]['roomIndex']==1
 assert graph['nodes'][2]['gamePosition']==(0,0,1)
 assert len(graph['edges'])==4  # No invented reciprocal exits.
 for i,e in enumerate(graph['edges']):
  assert e==dict(files['exits.json'][i],automatic=False,exitIndex=i)
 assert graph==connection_graph(files)


def test_connections_bound_wilderness_and_use_automatic_link_rules():
 files=example();files['rooms.json'][0]['grid_link']=True
 files['wilderness.json']={'presets':[{'key':'forest','name':'Forest','descriptions':['Trees.'],'links':'4'}],
  'layers':[{'z':0,'origin':[1,0],'width':1000,'height':1,'palette':['','forest'],'rows':['1000*1']}]}
 files['exits.json'].append({'from':'inside','to':'@10,0,0','name':'trail'})
 graph=connection_graph(files);nodes={n['id']:n for n in graph['nodes']}
 assert set(nodes)=={'arrival','inside','above','@10,0,0','@1,0,0','@9,0,0','@11,0,0'}
 assert nodes['@1,0,0']['preset']=='forest' and nodes['@1,0,0']['boundary']
 assert nodes['@11,0,0']['boundary'] and '@12,0,0' not in nodes
 assert any(e['automatic'] and e['from']=='arrival' and e['to']=='@1,0,0' for e in graph['edges'])
 files['rooms.json'][0]['blocked_dirs']='east'
 assert '@1,0,0' not in {n['id'] for n in connection_graph(files)['nodes']}


def test_transport_associations_do_not_invent_movement():
 files=example()
 files['rooms.json']+=[{'id':'cabin','name':'Cabin','description':'A transit cabin.'},
                       {'id':'interior','name':'Interior','description':'A vehicle interior.'}]
 files['content.json']={'mud_transit_lines':[{'id':'train','name':'Train','cabin_room_id':'cabin'}],
  'mud_vehicle_definitions':[{'id':'boat','name':'Boat','interior_room_id':'interior'}]}
 graph=connection_graph(files)
 assert [(a['room'],a['collection'],a['index']) for a in graph['associations']]==[
  ('cabin','mud_transit_lines',0),('interior','mud_vehicle_definitions',0)]
 assert not any(e['from'] in ['cabin','interior'] or e['to'] in ['cabin','interior'] for e in graph['edges'])


def test_missing_endpoint_is_visible_without_inventing_room():
 files=example();files['exits.json'].append({'from':'inside','to':'missing','name':'broken'})
 node=next(n for n in connection_graph(files)['nodes'] if n['id']=='missing')
 assert node['kind']=='missing' and node['roomIndex'] is None
