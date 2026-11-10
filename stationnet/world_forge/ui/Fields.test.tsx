// @vitest-environment jsdom
import React from 'react';
import {afterEach,describe,it,expect,vi} from 'vitest';
import {render,screen,cleanup,fireEvent} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {Field,RecordFields} from './Fields';
afterEach(cleanup);
const registry:any={collections:{rooms:{title:'Rooms',identity:['id'],file:'rooms.json',path:[]},manifest:{title:'World settings',fields:{name:{type:'string'},crime:{type:'object',properties:{enabled:{type:'boolean'},detection:{type:'number'}}}}}},shapes:{},conditions:{level:{type:'object',properties:{min:{type:'number'}}}},actions:{message:{type:'object',properties:{text:{type:'string'}}}},events:{enter_room:{properties:{room:{type:'string',ref:'rooms'}}}},directions:['north','east','south','west']};
const project:any={files:{'rooms.json':[{id:'harbor',name:'Harbor'}]}};
describe('Guided authoring controls',()=>{
 it('commits text on blur rather than each keystroke',async()=>{const changed=vi.fn();render(<Field name="name" value="Old room" schema={{type:'string'}} {...{registry,project}} onChange={changed} onNavigate={()=>{}}/>);const input=screen.getByLabelText('Name');await userEvent.clear(input);await userEvent.type(input,'New room');expect(changed).not.toHaveBeenCalled();fireEvent.blur(input);expect(changed).toHaveBeenCalledWith('New room');});
 it('opens a selected reference',async()=>{const navigate=vi.fn();render(<Field name="room_id" value="harbor" schema={{type:'string',ref:'rooms'}} {...{registry,project}} onChange={()=>{}} onNavigate={navigate}/>);await userEvent.click(screen.getByTitle('Open referenced record'));expect(navigate).toHaveBeenCalledWith({collection:'rooms',index:0});});
 it('adds guided nested object settings without requiring JSON',async()=>{const changed=vi.fn();render(<Field name="crime" value={{}} schema={{type:'object',properties:{detection:{type:'number'}}}} {...{registry,project}} onChange={changed} onNavigate={()=>{}}/>);await userEvent.selectOptions(screen.getByLabelText('Add crime setting'),'detection');expect(changed).toHaveBeenCalledWith({detection:0});});
 it('keeps condition-specific controls typed',()=>{render(<Field name="Entry" value={{type:'level',min:3}} schema={{type:'object'}} variant="condition" {...{registry,project}} onChange={()=>{}} onNavigate={()=>{}}/>);expect((screen.getByLabelText('Min') as HTMLInputElement).type).toBe('number');});
 it('edits imported JSON-text properties through guided fields',()=>{const changed=vi.fn();render(<Field name="properties_json" value={'{"value":12}'} schema={{type:'object'}} {...{registry,project}} onChange={changed} onNavigate={()=>{}}/>);const input=screen.getByLabelText('Value');fireEvent.change(input,{target:{value:'20'}});fireEvent.blur(input);expect(changed).toHaveBeenCalledWith('{"value":20}');});
 it('exposes optional fields with the registered structured type',async()=>{const changed=vi.fn();render(<RecordFields record={{name:'World'}} collection="manifest" {...{registry,project}} onField={changed} onNavigate={()=>{}} onHelp={()=>{}}/>);await userEvent.selectOptions(screen.getByLabelText('Add optional field'),'crime');await userEvent.click(screen.getByTitle('Add field'));expect(changed).toHaveBeenCalledWith('crime',{});});
});
