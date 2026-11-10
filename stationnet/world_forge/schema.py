"""Handbook-backed field registry. Package data is never coerced by these UI hints."""
from __future__ import annotations
import copy
import json
import re
from pathlib import Path

from .. import mud_abilities, mud_effects, mud_wilderness
from ..mud import DIRECTIONS, EQUIPMENT_SLOTS

# The handbook ships inside this package (scripts/build_mud_creator_handbook.py writes it).
HANDBOOK = Path(__file__).resolve().parent / 'handbook.md'
FILES = ['manifest.json', 'rooms.json', 'exits.json', 'content.json', 'origins.json',
         'skills.json', 'abilities.json', 'effects.json', 'dungeons.json', 'events.json', 'wilderness.json']
# Engine vocabularies come from the engine itself so the editor never drifts from it.
TERRAIN = list(mud_wilderness.TERRAINS)
DIRS = list(DIRECTIONS)
SLOTS = list(EQUIPMENT_SLOTS)
EFFECTS = list(mud_effects.EFFECTS)
ABILITIES = list(mud_abilities.BUILT_IN)
TEXT = HANDBOOK.read_text(encoding='utf-8')

# Explicit collection identities are essential: several StationNet tables have no id.
CONTENT = {
 'mud_item_definitions':('Items','Items','id'),
 'mud_item_instances':('Placed items','Items','id'),
 'mud_item_spawns':('Item spawns','Items','id'),
 'mud_npc_definitions':('NPCs','Characters','id'),
 'mud_npc_instances':('Placed NPCs','Characters','id'),
 'mud_npc_combat_profiles':('Combat profiles','Characters','npc_definition_id'),
 'mud_npc_equipment':('NPC equipment','Characters','npc_definition_id,equipment_slot'),
 'mud_npc_combat_rewards':('Combat loot','Characters','id'),
 'mud_conversations':('Conversations','Stories','id'),
 'mud_dialogue_nodes':('Dialogue nodes','Stories','id'),
 'mud_dialogue_responses':('Dialogue choices','Stories','id'),
 'mud_quests':('Quests','Stories','id'),
 'mud_quest_objectives':('Quest objectives','Stories','id'),
 'mud_quest_rewards':('Quest rewards','Stories','id'),
 'mud_quest_givers':('Quest givers','Stories','quest_id,npc_definition_id'),
 'mud_npc_shops':('Merchants','Economy','npc_definition_id'),
 'mud_npc_banks':('Bankers','Economy','npc_definition_id'),
 'mud_shop_stock':('Shop stock','Economy','id'),
 'mud_npc_trainers':('Trainers','Economy','id'),
 'mud_gathering_nodes':('Gathering nodes','Economy','id'),
 'mud_room_discoveries':('Discoveries','Exploration','id'),
 'mud_world_destinations':('Destinations','Exploration','id'),
 'mud_professions':('Professions','Economy','id'),
 'mud_work_activities':('Work & jobs','Economy','id'),
 'mud_work_rewards':('Work rewards','Economy','id'),
 'mud_world_start_rules':('Starting rules','World','id'),
 'mud_world_factions':('Factions','World life','id'),
 'mud_faction_relations':('Faction relations','World life','id'),
 'mud_properties':('Properties','Housing','id'),
 'mud_property_rooms':('Property rooms','Housing','room_id'),
 'mud_storage_points':('Storage','Housing','id'),
 'mud_vehicle_definitions':('Vehicles','Transport','id'),
 'mud_vehicle_placements':('Public vehicles','Transport','id'),
 'mud_fuel_stations':('Fuel stations','Transport','id'),
 'mud_transit_lines':('Transit lines','Transport','id'),
 'mud_transit_stops':('Transit stops','Transport','id'),
}

def infer(value):
 if isinstance(value,bool): return {'type':'boolean'}
 if isinstance(value,(int,float)): return {'type':'number'}
 if isinstance(value,str): return {'type':'string'}
 if isinstance(value,list):
  s={'type':'array','items':{}}
  for v in value: s['items']=merge(s['items'],infer(v))
  return s
 if isinstance(value,dict): return {'type':'object','properties':{k:infer(v) for k,v in value.items()}}
 return {'type':'string','nullable':True}

def merge(a,b):
 if not a:return copy.deepcopy(b)
 if not b:return a
 if a.get('type')!=b.get('type'):
  a=copy.deepcopy(a); a['flexible']=True; return a
 a=copy.deepcopy(a)
 if a.get('type')=='object':
  for k,v in b.get('properties',{}).items(): a.setdefault('properties',{})[k]=merge(a.get('properties',{}).get(k,{}),v)
 if a.get('type')=='array':a['items']=merge(a.get('items',{}),b.get('items',{}))
 return a

SHAPES={}
EXAMPLES=[]
def collect(value):
 if isinstance(value,dict):
  for k,v in value.items():
   SHAPES[k]=merge(SHAPES.get(k,{}),infer(v));collect(v)
 elif isinstance(value,list):
  for v in value:collect(v)

for block in re.findall(r'```json\s*\n(.*?)```',TEXT,re.S):
 for candidate in [block,'{'+block.strip().rstrip(',')+'}']:
  try:
   value=json.loads(candidate);collect(value);EXAMPLES.append(value);break
  except (ValueError,TypeError):pass

def string(**kw):return {'type':'string',**kw}
def number(**kw):return {'type':'number',**kw}
def boolean(**kw):return {'type':'boolean',**kw}
def obj(**fields):return {'type':'object','properties':fields}
def arr(item=None,**kw):return {'type':'array','items':item or string(),**kw}
def enum(*values):return string(enum=list(values))
def ref(target):return string(ref=target)

REFS={
 'from':'rooms','to':'rooms','room_id':'rooms','room':'rooms','start_room':'rooms','return_room':'rooms',
 'entrance_room_id':'rooms','interior_room_id':'rooms','cabin_room_id':'rooms','home':'rooms',
 'npc_definition_id':'mud_npc_definitions','npc_instance':'mud_npc_instances','npc':'mud_npc_definitions',
 'recruiter_npc_definition_id':'mud_npc_definitions','agent_npc_definition_id':'mud_npc_definitions',
 'provider_npc_definition_id':'mud_npc_definitions','turn_in_npc_definition_id':'mud_npc_definitions',
 'recipient_npc_definition_id':'mud_npc_definitions','item_definition_id':'mud_item_definitions',
 'tool_definition_id':'mud_item_definitions','item':'mud_item_definitions','item_id':'mud_item_definitions',
 'key_item_id':'mud_item_definitions','fuel_item_id':'mud_item_definitions','deliver_item_id':'mud_item_definitions',
 'container_id':'mud_item_instances','object':'mud_item_instances',
 'conversation_id':'mud_conversations','node_id':'mud_dialogue_nodes','next_node_id':'mud_dialogue_nodes',
 'response':'mud_dialogue_responses','quest_id':'mud_quests','quest':'mud_quests',
 'accept_quest_id':'mud_quests','turn_in_quest_id':'mud_quests','prerequisite_quest_id':'mud_quests',
 'objective':'mud_quest_objectives','faction':'mud_world_factions','faction_id':'mud_world_factions',
 'other_faction_id':'mud_world_factions','property':'mud_properties','property_id':'mud_properties',
 'vehicle':'mud_vehicle_definitions','vehicle_definition_id':'mud_vehicle_definitions',
 'destination':'mud_world_destinations','profession_id':'mud_professions','activity_id':'mud_work_activities',
 'line_id':'mud_transit_lines','skill_key':'skills','practice_skill_key':'skills','skill':'skills',
 'effect':'effects','trigger':'triggers','exit':'exits','exit_id':'exits',
 'reveal_exit':'exits','owner_faction':'mud_world_factions','owner_npc':'mud_npc_instances',
}
ENUMS={
 'direction':DIRS,'item_type':['misc','equipment','container','consumable','key'],'equipment_slot':SLOTS,
 'terrain':TERRAIN,'links':['4','8'],'completion_mode':['auto','turn_in'],
 'objective_type':['visit_room','talk_npc','defeat_npc','possess_item','deliver_item','script'],
 'reward_type':['item','experience','coins','reputation'],'retry_mode':['retry','once'],
 'acquisition':['purchase','claim','grant'],'travel':['walk','teleport'],'light':['bright','dim','dark'],
 'exposure':['open','sheltered'],'target':['self','enemy'],'attribute':['strength','agility','intellect','vitality'],
 'layout':['linear','branching','maze','hub'],
}
BOOLS=set('grid_link bank hidden enabled once negate outside_combat starting firearm aggressive friendly neutral passive mode_only shareable risk_loot'.split())
FLAGS=set('is_open is_start buys_items sequential abandonable optional consume_on_turn_in public discover_on_visit available'.split())
NUMS=set('x y z amount count price quantity sort_order required_count maximum_health attack defense experience_reward respawn_seconds accuracy evasion chance_percent chance min max min_rank max_rank cost damage heal potency duration sign value cooldown_seconds max_fires interval_seconds delay_seconds despawn_seconds max_live pick_difficulty speed fuel_capacity fuel_per_move fuel_per_item return_seconds fare fare_per_stop travel_seconds capacity rent rent_period_seconds grace_seconds prepay_periods max_owners refund_percent sell_percent share_percent difficulty size max_party starting_coins level_cap experience_percent health_per_level mana_per_level day_minutes start_hour success_percent time_limit_seconds retry_cooldown_seconds default_reputation defeat_reputation rounds magazine_size'.split())
LISTS=set('conditions conditions_json outcomes_json fail_conditions_json start_conditions_json join_conditions_json actions effects rewards_json details_json ranks_json'.split())

CONDITIONS={
 'level':obj(min=number(),max=number()),'race':obj(keys=arr(ref('races'))),'class':obj(keys=arr(ref('classes'))),
 'tag':obj(keys=arr()),'has_item':obj(item=ref('mud_item_definitions'),count=number()),
 'coins':obj(min=number()),'attribute':obj(attribute=enum('strength','agility','intellect','vitality'),min=number()),
 'skill':obj(skill=ref('skills'),min_rank=number()),
 'quest':obj(quest=ref('mud_quests'),status=arr(enum('not_started','active','ready','completed','failed','abandoned')),outcome=arr()),
 'objective':obj(objective=ref('mud_quest_objectives')),'state':obj(key=string(),value=string()),'claimed':obj(),
 'flag':obj(scope=enum('player','world'),key=string(),value=string(),min=number(),max=number()),
 'object_state':obj(object=ref('mud_item_instances'),state=string()),'in_room':obj(room=ref('rooms')),
 'knows_destination':obj(destination=ref('mud_world_destinations')),
 'reputation':obj(faction=ref('mud_world_factions'),rank=arr(),min=number(),max=number()),
 'wanted':obj(faction=ref('mud_world_factions'),min_fine=number()),'member':obj(faction=ref('mud_world_factions')),
 'disposition':obj(npc=ref('mud_npc_definitions'),tier=arr(enum('hostile','unfriendly','neutral','friendly','trusted')),min=number(),max=number()),
 'time':obj(**{'from':number(),'to':number()}),'owns_property':obj(property=ref('mud_properties'),role=enum('owner','primary')),
 'aboard':obj(vehicle=ref('mud_vehicle_definitions'),mode=string()),'has_vehicle':obj(vehicle=ref('mud_vehicle_definitions')),
 'any':obj(any=arr(obj(type=enum('level','has_item','flag','quest','tag'),negate=boolean()))),
}
ACTIONS={
 'message':obj(text=string(),to=enum('actor','room','other_room'),room=ref('rooms')),
 'set_flag':obj(scope=enum('player','world'),key=string(),value=string(),add=number()),
 'clear_flag':obj(scope=enum('player','world'),key=string()),
 'set_object_state':obj(object=ref('mud_item_instances'),state=string()),
 'set_exit':obj(exit=ref('exits'),state=enum('open','locked','hidden','default'),scope=enum('world','character')),
 'show':obj(npc_instance=ref('mud_npc_instances'),object=ref('mud_item_instances')),
 'hide':obj(npc_instance=ref('mud_npc_instances'),object=ref('mud_item_instances')),
 'spawn_npc':obj(npc=ref('mud_npc_definitions'),room=ref('rooms'),despawn_seconds=number(),max_live=number()),
 'teleport':obj(room=ref('rooms')),'start_quest':obj(quest=ref('mud_quests')),
 'complete_objective':obj(objective=ref('mud_quest_objectives')),
 'fail_quest':obj(quest=ref('mud_quests'),message=string()),'fail_objective':obj(objective=ref('mud_quest_objectives'),message=string()),
 'run_trigger':obj(trigger=ref('triggers')),
 'apply_effect':obj(effect=ref('effects'),potency=number(),duration=number()),
 'cure_effect':obj(effect=ref('effects'),category=string(),potency=number(),turns=number()),
 'fine':obj(faction=ref('mud_world_factions'),amount=number()),'clear_fines':obj(faction=ref('mud_world_factions')),
}
for name in ['give_item','take_item']:ACTIONS[name]=obj(item=ref('mud_item_definitions'),amount=number())
for name in ['experience','coins','take_coins','heal','damage']:ACTIONS[name]=obj(amount=number())
for name in ['learn_destination','forget_destination']:ACTIONS[name]=obj(destination=ref('mud_world_destinations'))
for name in ['reputation','disposition']:ACTIONS[name]=obj(**{('faction' if name=='reputation' else 'npc'):ref('mud_world_factions' if name=='reputation' else 'mud_npc_definitions'),'amount':number()})
for name in ['join_faction','leave_faction']:ACTIONS[name]=obj(faction=ref('mud_world_factions'))
for name in ['grant_property','revoke_property']:ACTIONS[name]=obj(property=ref('mud_properties'))
for name in ['grant_vehicle','revoke_vehicle']:ACTIONS[name]=obj(vehicle=ref('mud_vehicle_definitions'))
EVENTS={
 'enter_room':obj(room=ref('rooms')),'use_object':obj(object=ref('mud_item_instances'),item=ref('mud_item_definitions')),
 'take_item':obj(object=ref('mud_item_instances'),item=ref('mud_item_definitions')),
 'say_phrase':obj(room=ref('rooms'),phrase=string()),
 'defeat_npc':obj(npc=ref('mud_npc_definitions'),npc_instance=ref('mud_npc_instances')),
 'dialogue_choice':obj(response=ref('mud_dialogue_responses')),'quest_complete':obj(quest=ref('mud_quests')),
 'quest_failed':obj(quest=ref('mud_quests')),'crime':obj(room=ref('rooms'),npc=ref('mud_npc_definitions'),faction=ref('mud_world_factions')),
 'faction_join':obj(faction=ref('mud_world_factions')),'faction_leave':obj(faction=ref('mud_world_factions')),
 'timer':obj(room=ref('rooms'),interval_seconds=number()),'linger':obj(room=ref('rooms'),interval_seconds=number()),'manual':obj(),
}

# Fill gaps in examples with the handbook's accepted-field tables and prose.
SUPPLEMENT={
 'manifest': 'format_version id name author start_room progression availability layers resource clock relationships factions crime housing transport',
 'rooms':'id name description bank x y z grid_link blocked_dirs',
 'exits':'id from to name direction key_item_id pick_difficulty hidden conditions conditions_message requires_mode allow_vehicles',
 'mud_world_start_rules':'id room_id sort_order spawn_conditions_json',
 'mud_vehicle_definitions':'id name description modes terrains mode_only speed interior_room_id shareable fuel_capacity fuel_per_move fuel_item_id fuel_per_item return_seconds conditions_json conditions_message',
 'mud_vehicle_placements':'id vehicle_definition_id room_id',
 'mud_fuel_stations':'id room_id price modes',
 'mud_transit_lines':'id name description fare fare_per_stop cabin_room_id conditions_json conditions_message',
 'mud_transit_stops':'id line_id room_id sort_order travel_seconds',
 'triggers':'id name event source scope once cooldown_seconds max_fires enabled conditions fail_message actions interval_seconds',
 'presets':'key name terrain glyph links names descriptions environment encounters resources requires_mode',
 'regions':'key name overrides',
 'terrain_layers':'z origin width height palette rows',
 'cells':'x y z grid_link blocked_dirs',
 'templates':'key name template',
 'entrances':'room return_room template name description difficulty size layout max_party conditions conditions_message enabled',
 'races':'key name description traits attributes resistances tags effects spells equipment skills proficiencies skill_affinities progression',
 'classes':'key name description traits attributes resistances tags effects spells equipment skills skill proficiencies skill_affinities progression',
 'skills':'key name group description starting max_rank effects practice learn requirements proficiencies',
 'abilities':'key name description command target cost damage heal effects',
 'effects':'key name description kind sign resist_as outside_combat',
}
accepted=TEXT.split('Only these fields are accepted for each array;',1)[1].split('`mud_npc_combat_profiles` and',1)[0]
for line in accepted.splitlines():
 m=re.match(r'\| `(mud_\w+)` \| (.*) \|',line)
 if m:
  fields=re.findall(r'`([a-z_]+)`',m[2]); key=CONTENT.get(m[1],('','','id'))[2]
  SUPPLEMENT[m[1]]=' '.join(([] if 'no `id`' in m[2] or key!='id' else ['id'])+fields)
SUPPLEMENT['mud_world_factions']+=' joinable start_joinable recruiter_npc_definition_id join_cost join_reputation join_conditions_json join_conditions_message start_conditions_json leavable leave_reputation rejoin_hours expel_below'
SUPPLEMENT['mud_room_discoveries']+=' exit_id'

SHAPES.update({
 'progression':obj(level_cap=number(min=1,max=50),experience_percent=number(min=10,max=500),health_per_level=number(min=1,max=30),mana_per_level=number(min=0,max=10),starting_coins=number(min=0,max=100000)),
 'availability':obj(races=arr(ref('races')),classes=arr(ref('classes')),skills=arr(ref('skills'))),
 'clock':obj(enabled=boolean(),day_minutes=number(min=5,max=1440),start_hour=number(min=0,max=23)),
 'housing':obj(enabled=boolean(),max_holdings=number(min=1,max=20)),
 'transport':obj(enabled=boolean()),
 'attributes':obj(strength=number(),agility=number(),intellect=number(),vitality=number()),
 'source':obj(room=ref('rooms')),
 'blocked_dirs':string(widget='walls'),
 'resource':obj(enabled=boolean(),name=string()),
 'state_json':obj(state=string(),hidden=boolean()),
 'details_json':arr(obj(kind=enum('text','location','hint'),text=string(),room=ref('rooms'),reveal=enum('accepted','completed'))),
 'fail_conditions_json':arr(obj(conditions=arr(obj(type=enum(*(k for k in CONDITIONS if k!='any'))),variant='condition'),message=string())),
 'ranks_json':arr(obj(key=string(),name=string(),min=number(),hostile=boolean())),
})

def field(key,collection=''):
 if key in REFS and not (collection=='triggers' and key in ['from','to']):return ref(REFS[key])
 if key=='definition_id':return ref('mud_npc_definitions' if collection=='mud_npc_instances' else 'mud_item_definitions')
 if key in ENUMS:return enum(*ENUMS[key])
 if key in BOOLS:return boolean()
 if key in FLAGS:return number(enum=[0,1])
 if key in NUMS:return number()
 if key=='event':return enum(*EVENTS)
 if key in LISTS:
  if key in ['conditions','conditions_json','join_conditions_json','start_conditions_json']:return arr(obj(type=enum(*(k for k in CONDITIONS if k!='any')),negate=boolean()),variant='condition')
  if key in ['outcomes_json','actions']:
   allowed=list(ACTIONS) if key=='actions' else ['give_item','take_item','experience','coins','take_coins','learn_destination','reputation','join_faction','leave_faction','disposition','grant_property','revoke_property','grant_vehicle','revoke_vehicle']
   return arr(obj(type=enum(*allowed),delay_seconds=number()),variant='action')
 if key=='spells':return arr(ref('abilities'))
 if key=='map_items_json':return arr(ref('mud_item_definitions'))
 if key=='skills' and collection in ['races','classes']:return obj()
 if key in ['races','classes','skills','tags','modes','terrains','item_types','names','rows','palette']:return arr()
 if key=='template' and collection=='entrances':return ref('templates')
 if key=='template':return copy.deepcopy(SHAPES.get(key,obj()))
 if key=='kind' and collection=='effects':return enum('damage','regen','attack','defense','accuracy','evasion')
 if key=='kind' and collection=='mud_properties':return enum('unique','instanced')
 if key=='kind' and collection=='mud_work_activities':return enum('immediate','assignment')
 if key=='scope' and collection=='triggers':return enum('character','world')
 if key=='scope' and collection=='mud_storage_points':return enum('personal','property','communal')
 # Global samples may use the same name as both a collection and scalar.
 if key in ['id','key','name','description','body','summary','clue','response_text','node_key','glyph','command','resist_as','activity','outcome_key','outcome','requires_mode','conditions_message','fail_message','traits']:return string()
 return copy.deepcopy(SHAPES.get(key,string()))

COLLECTIONS={}
def add(key,title,group,file,path,identity='id',defaults=None):
 fields={k:field(k,key) for k in SUPPLEMENT.get(key,'id name description').split()}
 COLLECTIONS[key]={'key':key,'title':title,'group':group,'file':file,'path':path,'identity':identity.split(',') if identity else [],'fields':fields,'defaults':defaults or {}}

add('manifest','World settings','World','manifest.json',None,'',{'format_version':2})
# grid_link is a grid setting: the engine refuses it on rooms without x, y and z, so it is added on placement.
add('rooms','Rooms','Exploration','rooms.json',[],defaults={'name':'New room','description':'Describe this place.'})
add('exits','Passages','Exploration','exits.json',[],defaults={'from':'','to':'','name':'passage'})
for key,(title,group,identity) in CONTENT.items():add(key,title,group,'content.json',[key],identity)
for key,title,group,file in [('races','Races','Progression','origins'),('classes','Classes','Progression','origins'),('skills','Skills','Progression','skills'),('abilities','Abilities','Progression','abilities'),('effects','Effects','Progression','effects'),('triggers','Events & triggers','World life','events'),('templates','Dungeon templates','Dungeons','dungeons'),('entrances','Dungeon entrances','Dungeons','dungeons'),('presets','Terrain presets','Wilderness','wilderness'),('regions','Regions','Wilderness','wilderness'),('terrain_layers','Terrain layers','Wilderness','wilderness'),('cells','Cell overrides','Wilderness','wilderness')]:
 identity='key' if key in ['races','classes','skills','abilities','effects','templates','presets','regions'] else ('z' if key=='terrain_layers' else ('x,y,z' if key=='cells' else ('room,name' if key=='entrances' else 'id')))
 add(key,title,group,file+'.json',['layers' if key=='terrain_layers' else key],identity)

DEFAULTS={
 'mud_item_definitions':{'name':'New item','description':'Describe this item.','item_type':'misc'},
 'mud_npc_definitions':{'name':'New character','description':'Describe this character.'},
 'mud_conversations':{'name':'New conversation'},
 'mud_dialogue_nodes':{'conversation_id':'','node_key':'start','body':'What do they say?','is_start':1},
 'mud_dialogue_responses':{'node_id':'','response_text':'Your reply.'},
 'mud_quests':{'name':'New quest','description':'Describe this quest.','completion_mode':'auto'},
 'mud_quest_objectives':{'quest_id':'','objective_type':'visit_room','target_id':'','description':'Reach your destination.','required_count':1},
 'triggers':{'name':'New event','event':'manual','actions':[{'type':'message','text':'Something happens.'}]},
 'presets':{'name':'New terrain','terrain':'grassland','glyph':'.','links':'4','descriptions':['Describe this landscape.']},
 'regions':{'name':'New region','overrides':{'danger':1}},
 'templates':{'name':'New dungeon','template':{'schema':2,'name':'New dungeon','description':'An unexplored place.','generation':{'size':{'minimum':4,'default':8,'maximum':20},'difficulty':{'minimum':1,'default':1,'maximum':5},'layouts':[{'type':'linear','weight':1}]},'pools':{'rooms':[{'id':'chamber','names':[{'text':'Stone chamber'}],'descriptions':[{'text':'A rough stone chamber.'}]}]}}},
 'entrances':{'room':'','template':'','name':'dungeon','max_party':4},
 'skills':{'name':'New skill','group':'general','max_rank':10,'effects':[],'practice':[]},
 'effects':{'name':'New effect','kind':'damage'},
 'abilities':{'name':'New ability','target':'enemy','cost':5,'damage':5},
 'races':{'name':'New race','description':'Describe this origin.'},'classes':{'name':'New class','description':'Describe this class.'},
}
for key,defaults in DEFAULTS.items():COLLECTIONS[key]['defaults']=defaults
COLLECTIONS['manifest']['fields']['layers']=arr(obj(z=number(),name=string()))
COLLECTIONS['terrain_layers']['fields'].update(origin=arr(number()),width=number(),height=number(),rows=arr(),palette=arr())
for key in ['races','classes']:
 COLLECTIONS[key]['fields']['effects']=obj(**{k:number() for k in ['attack','melee_damage','firearm_damage','spell_power','healing','defense','accuracy','evasion','experience_percent','stealth','flee','search','lockpick','shop_discount']})
 COLLECTIONS[key]['fields']['progression']=obj(health=number(),mana=number(),health_per_level=number(),mana_per_level=number(),skill_points=number(),starting_skill_choices=number())
COLLECTIONS['skills']['fields']['practice']=obj(triggers=arr(),rate=number())
COLLECTIONS['skills']['defaults'].update(group='knowledge',description='Describe this skill.',practice={'triggers':[],'rate':1})
for key in ['races','classes']:COLLECTIONS[key]['defaults']['traits']='Describe their defining traits.'

# Context-specific shapes avoid ambiguities such as crime (event vs world switch)
# and effects (origin modifiers vs timed effects vs rank-based skill bonuses).
COLLECTIONS['manifest']['fields']['crime']=obj(enabled=boolean(),detection=number(min=0,max=100),fine_expiry_hours=number(min=0,max=720),fines=obj(theft=number(),pickpocket=number(),assault=number()),reputation=obj(theft=number(),pickpocket=number(),assault=number()))
COLLECTIONS['manifest']['fields']['relationships']=obj(enabled=boolean(),quest_complete=number(),attack=number(),crime=number(),hostile_at=number())
COLLECTIONS['manifest']['fields']['factions']=obj(enabled=boolean(),max_memberships=number(),start_required=boolean())
COLLECTIONS['skills']['fields']['group']=enum('combat','weapon','armor','magic','stealth','exploration','social','knowledge')
COLLECTIONS['skills']['fields']['effects']=arr(obj(type=enum('attack','melee_damage','firearm_damage','spell_power','healing','defense','accuracy','evasion','experience_percent','stealth','flee','search','lockpick','gathering','shop_discount'),per_rank=number(),every=number(),max=number(),weapon_category=string(),armor_category=string()))
COLLECTIONS['skills']['fields']['practice']=obj(triggers=arr(enum('melee_hit','firearm_hit','spell_cast','dodge','heal_cast','damage_taken','defend','hide','flee','search','pick_lock','experience_gained','purchase','gather','work')),rate=number(min=1,max=10))
for key in ['properties_json','behaviors_json','state_json']:
 for c in COLLECTIONS.values():
  if key in c['fields']:c['fields'][key]['flexible']=True
COLLECTIONS['templates']['defaults']['template']['schema_version']=2
COLLECTIONS['templates']['defaults']['template'].pop('schema',None)
template_shape=copy.deepcopy(COLLECTIONS['templates']['fields']['template'])
template_shape['type']='object';tp=template_shape.setdefault('properties',{})
for k in ['id','version','origins','skills','abilities','effects','...']:tp.pop(k,None)
tp.update(schema_version=number(enum=[1,2]),name=string(),description=string())
tp['generation']=obj(size=obj(minimum=number(),default=number(),maximum=number()),difficulty=obj(minimum=number(),default=number(),maximum=number()),layouts=arr(obj(type=enum('linear','branching','maze','hub'),weight=number())),special_roles=arr(obj(role=string(),count=number(),min_depth_ratio=number(),max_depth_ratio=number(),prefer_dead_ends=boolean())))
for k in ['definitions','pools','rewards','scaling','bosses']:
 if k not in tp:tp[k]=copy.deepcopy(SHAPES.get(k,obj()))
tp['triggers']=arr(obj(id=string(),event=enum('enter_room','say_phrase','use_object','take_item','defeat_npc','manual'),source=obj(room_role=string(),room_template=string(),environment=string(),item=string(),npc=string(),phrase=string()),conditions=arr(obj(type=enum(*(k for k in CONDITIONS if k!='any'))),variant='condition'),actions=arr(obj(type=enum(*ACTIONS)),variant='action'),once=boolean(),scope=enum('character','world'),cooldown_seconds=number(),max_fires=number(),fail_message=string()))
COLLECTIONS['templates']['fields']['template']=template_shape
pool_conditions=obj(layouts=arr(enum('linear','branching','maze','hub')),roles=arr(),min_difficulty=number(),max_difficulty=number(),min_depth_ratio=number(),max_depth_ratio=number(),min_degree=number(),max_degree=number())
weighted_text=arr(obj(text=string(),weight=number(),conditions=pool_conditions))
pool_base={'id':string(),'weight':number(),'conditions':pool_conditions}
content_pool=obj(**pool_base,count=obj(minimum=number(),maximum=number()),chance=number(),choices=arr(obj(local_id=string(),definition_id=string(),weight=number(),conditions=pool_conditions)))
tp['pools']=obj(rooms=arr(obj(**pool_base,names=weighted_text,descriptions=weighted_text)),environments=arr(obj(**pool_base,descriptions=weighted_text)),npcs=arr(content_pool),enemies=arr(content_pool),items=arr(content_pool),encounters=arr(obj(**pool_base,npc_pool=string(),enemy_pool=string(),item_pool=string(),chance=number(),config=obj())))
embedded_item=obj(id=string(),name=string(),description=string(),item_type=enum('misc','equipment','container','consumable','key'),equipment_slot=enum(*SLOTS),properties=copy.deepcopy(SHAPES.get('properties_json',obj())),behaviors=obj())
embedded_response=obj(id=string(),text=string(),next_node_id=string(),sort_order=number(),required_state_key=string(),required_state_value=string(),state_key=string(),state_value=string(),conditions=arr(obj(type=enum(*(k for k in CONDITIONS if k!='any'))),variant='condition'),outcomes=arr(obj(type=enum('give_item','take_item','experience','coins','take_coins')),variant='action'))
embedded_npc=obj(id=string(),name=string(),description=string(),properties=copy.deepcopy(SHAPES.get('properties_json',obj())),behaviors=copy.deepcopy(SHAPES.get('behaviors_json',obj())),boss=boolean(),combat=obj(maximum_health=number(),attack=number(),defense=number(),accuracy=number(),evasion=number(),experience_reward=number(),respawn_seconds=number()),equipment=arr(obj(slot=enum(*SLOTS),item_local_id=string(),item_definition_id=ref('mud_item_definitions'))),loot=arr(obj(type=enum('item','experience'),amount=number(),item_local_id=string(),item_definition_id=ref('mud_item_definitions'))),conversation=obj(nodes=arr(obj(id=string(),body=string(),start=boolean(),responses=arr(embedded_response)))))
tp['definitions']=obj(items=arr(embedded_item),npcs=arr(embedded_npc),enemies=arr(embedded_npc))
tp['scaling']=obj(offset=number(),per_step=obj(maximum_health=number(),attack=number(),defense=number(),accuracy=number(),evasion=number(),experience_reward=number()))
tp['bosses']=obj(health_percent=number(),attack_percent=number(),experience_percent=number())
COLLECTIONS['templates']['fields']['template']=template_shape

STARTERS={
 'mud_item_instances':{'definition_id':'','room_id':''},
 'mud_npc_instances':{'definition_id':'','room_id':''},
 'mud_npc_combat_profiles':{'npc_definition_id':'','maximum_health':20,'attack':3,'defense':0,'experience_reward':5,'respawn_seconds':60},
 'mud_npc_equipment':{'npc_definition_id':'','equipment_slot':'main_hand','item_definition_id':''},
 'mud_npc_combat_rewards':{'npc_definition_id':'','reward_type':'item','target_id':'','amount':1,'chance_percent':100},
 'mud_quest_rewards':{'quest_id':'','reward_type':'experience','amount':10},
 'mud_quest_givers':{'quest_id':'','npc_definition_id':''},
 'mud_npc_shops':{'npc_definition_id':'','buys_items':1,'sell_percent':40},
 'mud_npc_banks':{'npc_definition_id':''},
 'mud_shop_stock':{'npc_definition_id':'','item_definition_id':'','price':10},
 'mud_item_spawns':{'definition_id':'','room_id':'','respawn_seconds':60},
 'mud_gathering_nodes':{'room_id':'','name':'New gathering source','activity':'harvest','rewards_json':[{'item_id':'','amount':1}],'respawn_seconds':60},
 'mud_room_discoveries':{'room_id':'','clue':'Describe what the player discovers.'},
 'mud_npc_trainers':{'npc_definition_id':'','skill_key':'','price':10,'max_rank':5},
 'mud_professions':{'name':'New profession','description':'Describe this profession.','recruiter_npc_definition_id':''},
 'mud_work_activities':{'profession_id':'','name':'New work','description':'Describe this work.','kind':'immediate','room_id':''},
 'mud_work_rewards':{'activity_id':'','reward_type':'coins','amount':5},
 'mud_world_start_rules':{'room_id':'','sort_order':0,'spawn_conditions_json':[{'type':'race','keys':[]}]},
 'mud_world_destinations':{'room_id':'','name':'New destination','public':1,'discover_on_visit':1},
 'mud_world_factions':{'name':'New faction','description':'Describe this faction.','public':1,'default_reputation':0},
 'mud_faction_relations':{'faction_id':'','other_faction_id':'','share_percent':25},
 'mud_properties':{'name':'New property','description':'Describe this holding.','entrance_room_id':'','kind':'unique','acquisition':'purchase','price':100},
 'mud_property_rooms':{'property_id':'','room_id':''},
 'mud_storage_points':{'room_id':'','name':'New storage','scope':'personal','capacity':20},
 'mud_vehicle_definitions':{'name':'New vehicle','description':'Describe this vehicle.','speed':100},
 'mud_vehicle_placements':{'vehicle_definition_id':'','room_id':''},
 'mud_fuel_stations':{'room_id':'','price':1},
 'mud_transit_lines':{'name':'New transit line','fare':1},
 'mud_transit_stops':{'line_id':'','room_id':'','sort_order':0,'travel_seconds':60},
 'terrain_layers':{'z':0,'origin':[0,0],'width':10,'height':10,'palette':[''],'rows':[]},
 'cells':{'x':0,'y':0,'z':0,'grid_link':False},
}
for key,starter in STARTERS.items():COLLECTIONS[key]['defaults']={**starter,**COLLECTIONS[key]['defaults']}
COLLECTIONS['classes']['defaults']['skill']=''
COLLECTIONS['mud_gathering_nodes']['fields']['activity']=enum('harvest','mine','scavenge','collect')

REQUIRED={
 'manifest':['id','name','author','start_room'], 'rooms':['id','name','description'], 'exits':['from','to','name'],
 'mud_item_definitions':['id','name','description','item_type'],'mud_npc_definitions':['id','name','description'],
 'mud_item_instances':['id','definition_id'],'mud_npc_instances':['id','definition_id','room_id'],
 'mud_conversations':['id','name'],'mud_dialogue_nodes':['id','conversation_id','node_key','body'],
 'mud_dialogue_responses':['id','node_id','response_text'],'mud_quests':['id','name','description'],
 'mud_quest_objectives':['id','quest_id','objective_type','description'],
 'mud_quest_rewards':['id','quest_id','reward_type','amount'],'mud_npc_combat_rewards':['id','npc_definition_id','reward_type','amount'],
 'mud_work_activities':['id','profession_id','name','description','kind'],
 'races':['key','name','description','traits'],'classes':['key','name','description','traits','skill'],
 'skills':['key','name','description'],'abilities':['key','name','target','cost'],'effects':['key','name','kind'],
 'presets':['key','name','descriptions'],'regions':['key','name'],'triggers':['id','event','actions'],
 'templates':['key','name','template'],'entrances':['room','template','name'],
}
for key in CONTENT:
 if key not in REQUIRED:
  REQUIRED[key]=list(dict.fromkeys(COLLECTIONS[key]['identity']+[k for k,v in COLLECTIONS[key]['defaults'].items() if v=='' and k.endswith('_id')]))

def registry():
 return {'collections':COLLECTIONS,'shapes':SHAPES,'references':REFS,'conditions':CONDITIONS,'actions':ACTIONS,'events':EVENTS,'files':FILES,
         'coverage':[{'key':k,'title':v['title'],'fields':len(v['fields']),'guided':True} for k,v in COLLECTIONS.items()],
         'handbook':TEXT,'terrain':TERRAIN,'directions':DIRS}
