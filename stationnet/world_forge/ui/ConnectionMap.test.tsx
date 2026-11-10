// @vitest-environment jsdom
import React from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {act,cleanup,render,screen,waitFor,fireEvent} from '@testing-library/react';
import {ConnectionMap} from './ConnectionMap';
import {Project} from './data';
import {ConnectionGraph} from './connections';

afterEach(()=>{cleanup();vi.unstubAllGlobals();});
const project:Project={id:'n:test',kind:'new',worldId:'test',path:'test',name:'Test',revision:0,dirty:false,canUndo:false,canRedo:false,files:{'manifest.json':{start_room:'cabin'}}};
const graph=(revision:number,name:string):ConnectionGraph=>({revision,nodes:[{id:'cabin',name,kind:'authored',roomIndex:0,gamePosition:null,boundary:false},{id:'@0,0,0',name:'Forest',kind:'wilderness',preset:'forest',roomIndex:null,gamePosition:[0,0,0],boundary:true}],edges:[],associations:[{room:'cabin',kind:'Transit cabin',name:'Train',collection:'mud_transit_lines',index:0}]});
function setup(){
 vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});
 vi.stubGlobal('localStorage',{getItem:()=>null,setItem:()=>{}});
}
it('discards stale responses after a draft revision changes',async()=>{
 setup();let resolveOld!:(value:any)=>void;
 vi.stubGlobal('fetch',vi.fn().mockImplementationOnce(()=>new Promise(resolve=>{resolveOld=resolve;})).mockResolvedValueOnce({ok:true,json:async()=>graph(1,'Current cabin')}));
 const props={project,onSelect:vi.fn(),onNavigate:vi.fn(),onGrid:vi.fn(),onError:vi.fn()};const {rerender}=render(<ConnectionMap {...props}/>);
 rerender(<ConnectionMap {...props} project={{...project,revision:1}}/>);
 await screen.findByRole('button',{name:'Select room Current cabin (cabin)'});
 await act(async()=>{resolveOld({ok:true,json:async()=>graph(0,'Old cabin')});});
 expect(screen.queryByRole('button',{name:'Select room Old cabin (cabin)'})).toBeNull();
 expect(screen.getByRole('button',{name:'Select room Current cabin (cabin)'})).toBeTruthy();
});
it('links transport ownership separately and continues wilderness on the grid',async()=>{
 setup();vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>graph(0,'Cabin')}));
 const onNavigate=vi.fn(),onGrid=vi.fn(),onSelect=vi.fn();const props={project,selectedId:'cabin',onSelect,onNavigate,onGrid,onError:vi.fn()};
 const {rerender}=render(<ConnectionMap {...props}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Train'}));expect(onNavigate).toHaveBeenCalledWith(expect.objectContaining({collection:'mud_transit_lines',index:0}));
 expect(screen.getByText('Transport ownership, not a movement passage.')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'Select room Forest (@0,0,0)'}));expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({id:'@0,0,0'}));
 rerender(<ConnectionMap {...props} selectedId="@0,0,0"/>);
 fireEvent.click(await screen.findByRole('button',{name:'Continue on grid'}));expect(onGrid).toHaveBeenCalledWith(expect.objectContaining({gamePosition:[0,0,0]}));
 await waitFor(()=>expect(screen.getByText(/Automatic wilderness paths continue/)).toBeTruthy());
});
