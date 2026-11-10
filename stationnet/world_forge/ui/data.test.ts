import {describe,it,expect} from 'vitest';
import {rows,infer,initial,slug,identifier,changes} from './data';
describe('World data adapters',()=>{
 it('keeps optional collections absent',()=>{const p:any={files:{'rooms.json':[]}};const r:any={collections:{items:{file:'content.json',path:['mud_item_definitions']}}};expect(rows(p,r,'items')).toEqual([]);expect(p.files['content.json']).toBeUndefined();});
 it('reads singleton settings and legacy list definitions',()=>{const p:any={files:{'manifest.json':{id:'world'},'skills.json':[{key:'skill'}]}};const r:any={collections:{manifest:{file:'manifest.json',path:null},skills:{file:'skills.json',path:['skills']}}};expect(rows(p,r,'manifest')[0].id).toBe('world');expect(rows(p,r,'skills')[0].key).toBe('skill');});
 it('infers nested controls and preserves false and zero',()=>{expect(infer({enabled:false,amount:0}).properties?.enabled.type).toBe('boolean');expect(initial({type:'number',min:1})).toBe(1);expect(initial({type:'array'})).toEqual([]);});
 it('uses composite identities for records without IDs',()=>{expect(identifier({npc_definition_id:'keeper',equipment_slot:'head'},{identity:['npc_definition_id','equipment_slot']} as any)).toBe('keeper · head');expect(slug('The Lantern Coast!')).toBe('the-lantern-coast');});
 it('patches only changed nested fields so queued edits preserve their neighbors',()=>{expect(changes({attack:2,defense:3},{attack:2,defense:4},['properties_json'])).toEqual([{path:['properties_json','defense'],value:4}]);});
});
