"""World Forge's editing model, ported from the standalone editor's suite."""
import copy
import io
import json
import time
import zipfile

import pytest

from stationnet.world_forge.schema import COLLECTIONS, FILES, registry
from stationnet.world_forge.storage import WorldError, unpack
from stationnet.world_forge.world import WorldIndex, records, validate, mutate, decode_runs, encode_runs, replace_span, uses


@pytest.fixture
def world():
 return {'manifest.json':{'format_version':2,'id':'test-world','name':'Test world','author':'Tester','start_room':'arrival'},
         'rooms.json':[{'id':'arrival','name':'Arrival','description':'Welcome.','x':0,'y':0,'z':0,'grid_link':False}], 'exits.json':[]}


def terrain(world,width=1000,height=1000):
 world['wilderness.json']={'presets':[{'key':'forest','name':'Forest','terrain':'forest','glyph':'t','links':'4','descriptions':['Trees rustle.']}],
 'regions':[],'layers':[{'z':0,'origin':[10,10],'width':width,'height':height,'palette':['','forest'],'rows':[f'{width}*1']*height}]}
 return world


def test_blank_world_valid(world):assert not [i for i in validate(world) if i['severity']=='error']


def test_all_categories_have_guided_schema():
 assert len(COLLECTIONS)==51
 for c in COLLECTIONS.values():assert c['fields'] and c['file'] in FILES
 assert 'spawn_conditions_json' in COLLECTIONS['mud_world_start_rules']['fields']
 assert 'join_conditions_json' in COLLECTIONS['mud_world_factions']['fields']
 assert 'learn' in COLLECTIONS['skills']['fields']
 assert registry()['actions']['apply_effect']['properties']['effect']['ref']=='effects'


def test_nested_patches_preserve_queued_edits(world):
 world['manifest.json']['progression']={'level_cap':20,'starting_coins':10}
 first,_=mutate(world,'patch_record',{'collection':'manifest','index':0,'patches':[{'path':['progression','level_cap'],'value':30}]})
 second,_=mutate(first,'patch_record',{'collection':'manifest','index':0,'patches':[{'path':['progression','starting_coins'],'value':50}]})
 assert second['manifest.json']['progression']=={'level_cap':30,'starting_coins':50}


@pytest.mark.parametrize('names',[['../manifest.json'],['folder/manifest.json'],['manifest.json','manifest.json'],['audit.py'],['MANIFEST.JSON']])
def test_unsafe_archive_paths(names):
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w') as z:
  for name in names:z.writestr(name,'{}')
 with pytest.raises(WorldError):unpack(out.getvalue())


def test_size_limits():
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('manifest.json',' '*((8*1024*1024)+1))
 with pytest.raises(WorldError):unpack(out.getvalue())


def test_explicit_links_and_reciprocals(world):
 new,_=mutate(world,'create_record',{'collection':'rooms','record':{'id':'second','name':'Second','description':'Next room.','x':1,'y':0,'z':0}})
 assert WorldIndex(new).links('arrival')==[]
 new,_=mutate(new,'connect',{'from':'arrival','to':'second','direction':'east','reciprocal':True})
 assert WorldIndex(new).links('arrival')[0]['to']=='second'
 assert WorldIndex(new).links('second')[0]['direction']=='west'
 with pytest.raises(WorldError):mutate(new,'connect',{'from':'arrival','to':'second','direction':'east'})


def test_auto_links_walls_and_diagonals(world):
 terrain(world,3,3);idx=WorldIndex(world)
 assert len(idx.links('@11,11,0'))==4
 world['wilderness.json']['presets'][0]['links']='8'
 assert len(WorldIndex(world).links('@11,11,0'))==8
 new,_=mutate(world,'set_walls',{'x':11,'y':11,'z':0,'directions':['east']})
 assert not any(e['to']=='@12,11,0' for e in WorldIndex(new).links('@11,11,0'))
 assert not any(e['to']=='@11,11,0' for e in WorldIndex(new).links('@12,11,0'))
 new['exits.json']=[{'from':'@11,11,0','to':'arrival','direction':'north','name':'portal'}]
 assert sum(e.get('direction')=='north' for e in WorldIndex(new).links('@11,11,0'))==1


def test_cross_layers_and_placement(world):
 world['rooms.json'].append({'id':'above','name':'Above','description':'Upstairs.','x':0,'y':0,'z':1})
 with pytest.raises(WorldError):mutate(world,'connect',{'from':'arrival','to':'above','direction':'north'})
 connected,_=mutate(world,'connect',{'from':'arrival','to':'above','direction':'up'})
 assert connected['exits.json'][1]['direction']=='down'
 with pytest.raises(WorldError):mutate(world,'place_room',{'index':1,'x':0,'y':0,'z':0})


def test_moving_room_preserves_coordinate_references(world):
 world['exits.json']=[{'from':'arrival','to':'@0,0,0','name':'return'}]
 moved,_=mutate(world,'place_room',{'index':0,'x':4,'y':5,'z':0})
 assert moved['exits.json'][0]['to']=='arrival'
 assert WorldIndex(moved).at(0,0,0) is None


def test_paint_shapes_and_protection(world):
 world['wilderness.json']={'presets':[{'key':'forest','name':'Forest','descriptions':['Trees.']}],'regions':[],'layers':[]}
 painted,_=mutate(world,'paint',{'z':0,'preset':'forest','x1':-2,'y1':-2,'x2':2,'y2':2})
 idx=WorldIndex(painted);assert idx.at(0,0,0)['id']=='arrival' and idx.at(1,1,0)['preset']=='forest'
 assert len(painted['wilderness.json']['layers'][0]['rows'])==5
 erased,_=mutate(painted,'paint',{'z':0,'preset':'','x1':-2,'y1':-2,'x2':2,'y2':2})
 assert WorldIndex(erased).at(1,1,0) is None and WorldIndex(erased).at(0,0,0)['id']=='arrival'
 ellipse,_=mutate(world,'paint',{'z':0,'preset':'forest','shape':'ellipse','x1':10,'y1':10,'x2':16,'y2':16})
 assert WorldIndex(ellipse).at(10,10,0) is None and WorldIndex(ellipse).at(13,13,0)


def test_conversion_references_and_erasure_protection(world):
 terrain(world,3,3);world['content.json']={'mud_item_definitions':[{'id':'stone','name':'Stone','description':'A stone.','item_type':'misc'}],
 'mud_item_instances':[{'id':'placed-stone','definition_id':'stone','room_id':'@11,11,0'}]}
 with pytest.raises(WorldError):mutate(world,'paint',{'preset':'','z':0,'x1':11,'y1':11,'x2':11,'y2':11})
 converted,selected=mutate(world,'convert_cell',{'x':11,'y':11,'z':0})
 r=records(converted,'rooms')[selected['index']]
 assert converted['content.json']['mud_item_instances'][0]['room_id']==r['id']
 assert WorldIndex(converted).terrain(11,11,0) is None
 assert WorldIndex(converted).at(11,11,0)['id']==r['id']


def test_safe_rename_and_delete_dependencies(world):
 world['exits.json']=[{'from':'arrival','to':'arrival','name':'circle'}]
 renamed,_=mutate(world,'set_field',{'collection':'rooms','index':0,'path':['id'],'value':'harbor'})
 assert renamed['manifest.json']['start_room']=='harbor'
 assert renamed['exits.json'][0]['from']=='harbor' and renamed['exits.json'][0]['to']=='harbor'
 assert len(uses(renamed,'rooms','harbor'))==3
 with pytest.raises(WorldError):mutate(renamed,'delete_record',{'collection':'rooms','index':0})


def test_rename_dictionary_and_json_text_references(world):
 world['skills.json']={'skills':[{'key':'sailing','name':'Sailing','description':'Sail safely.'}]}
 world['origins.json']={'races':[{'key':'islander','name':'Islander','description':'An islander.','traits':'Sea legs','skills':{'sailing':2}}]}
 renamed,_=mutate(world,'set_field',{'collection':'skills','index':0,'path':['key'],'value':'seamanship'})
 assert renamed['origins.json']['races'][0]['skills']=={'seamanship':2}
 renamed['content.json']={'mud_npc_definitions':[{'id':'guide','name':'Guide','description':'A guide.','behaviors_json':json.dumps({'schedule':{'home':'arrival','entries':[{'room':'arrival','from':8,'to':18}]}})}]}
 renamed,_=mutate(renamed,'set_field',{'collection':'rooms','index':0,'path':['id'],'value':'harbor'})
 behavior=json.loads(renamed['content.json']['mud_npc_definitions'][0]['behaviors_json'])
 assert behavior['schedule']['home']=='harbor' and behavior['schedule']['entries'][0]['room']=='harbor'


def test_palette_with_nonempty_zero_and_fill_empty(world):
 terrain(world,3,3);layer=world['wilderness.json']['layers'][0];layer['palette']=['forest'];layer['rows']=['3*0']*3
 painted,_=mutate(world,'paint',{'preset':'forest','z':0,'x1':8,'y1':8,'x2':10,'y2':10,'empty_only':True})
 idx=WorldIndex(painted)
 assert idx.at(8,8,0)['preset']=='forest' and idx.at(12,12,0)['preset']=='forest'
 assert len(painted['wilderness.json']['layers'][0]['palette'])==2


@pytest.mark.parametrize('file,value',[('rooms.json',[1]),('rooms.json',{}),('wilderness.json',{'layers':'bad'}),('manifest.json',[])])
def test_malformed_structures_are_validation_errors(world,file,value):
 world[file]=value
 assert any(i['severity']=='error' for i in validate(world))


def test_dungeon_embedded_dialogue_stays_local(world):
 world,_=mutate(world,'create_record',{'collection':'templates','record':{'key':'caves'}})
 theme=world['dungeons.json']['templates'][0]['template']
 theme['definitions']={'items':[{'id':'gem','name':'Gem','description':'A gem.'}], 'npcs':[{'id':'local-guide','name':'Guide','description':'A guide.','conversation':{'nodes':[{'id':'hello','body':'Hello','start':True,'responses':[{'id':'bye','text':'Goodbye','next_node_id':'end','outcomes':[{'type':'give_item','item':'gem'}]}]},{'id':'end','body':'Farewell','responses':[]}]}}]}
 assert not [i for i in validate(world) if i['severity']=='error']


def test_missing_required_fields_block_export(world):
 world,_=mutate(world,'create_record',{'collection':'mud_npc_instances'})
 errors=[i for i in validate(world) if i['severity']=='error']
 assert any(i['field']=='definition_id' for i in errors) and any(i['field']=='room_id' for i in errors)


def test_dialogue_and_trigger_validation(world):
 world['content.json']={'mud_conversations':[{'id':'talk','name':'Talk'}],
 'mud_dialogue_nodes':[{'id':'start','conversation_id':'talk','node_key':'start','body':'Hello','is_start':0}]}
 world['events.json']={'triggers':[{'id':'first','event':'manual','actions':[{'type':'run_trigger','trigger':'second'}]},
 {'id':'second','event':'manual','actions':[{'type':'run_trigger','trigger':'first'}]}]}
 issues=validate(world)
 assert any('start node' in i['message'] for i in issues)
 assert any('cycle' in i['message'] for i in issues)


def test_large_wilderness_is_compact(world):
 terrain(world);start=time.perf_counter();idx=WorldIndex(world)
 viewport=[idx.at(x,y,0) for y in range(20,40) for x in range(20,50)]
 assert all(viewport) and len(idx.rows)==20
 assert time.perf_counter()-start<1
 assert len(json.dumps(world))<20000
 assert not [i for i in validate(world) if i['severity']=='error']


def test_handbook_is_current_and_every_package_table_is_editable():
    """World Forge reads the shipped handbook; every table a package may carry has an editor."""
    import subprocess
    import sys
    from pathlib import Path
    from stationnet import mud_worlds
    root = Path(__file__).resolve().parents[1]
    check = subprocess.run([sys.executable, str(root / "scripts" / "build_mud_creator_handbook.py"), "--check"],
                           capture_output=True, text=True, cwd=root)
    assert check.returncode == 0, check.stdout + check.stderr
    for table in mud_worlds.CONTENT_TABLES:
        assert table in COLLECTIONS, f"{table} has no World Forge collection"
    for collection in ("rooms", "exits", "races", "classes", "skills", "abilities", "effects", "triggers",
                       "templates", "entrances", "presets", "regions", "terrain_layers", "cells", "manifest"):
        assert collection in COLLECTIONS
