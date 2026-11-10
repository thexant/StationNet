import {describe,it,expect} from 'vitest';
import {ConnectionGraph,layoutConnections,passageLabel,passageStyle} from './connections';

describe('connection diagram',()=>{
 it('places weak components deterministically, starting at the start room, without changing game positions',()=>{
  const nodes=['other','inside','start','cabin'].map((id,roomIndex)=>({id,name:id,roomIndex,gamePosition:null,kind:'authored' as const,boundary:false}));
  const graph:ConnectionGraph={revision:0,nodes,edges:[{from:'inside',to:'start',automatic:false},{from:'start',to:'start',automatic:false}],associations:[]};
  const original=JSON.stringify(graph);const layout=layoutConnections(graph,'start');const byId=new Map(layout.map(n=>[n.id,n]));
  expect(byId.get('start')!.dy).toBe(32);expect(byId.get('inside')!.dx).toBeGreaterThan(byId.get('start')!.dx);
  expect(byId.get('other')!.dy).toBeGreaterThan(byId.get('inside')!.dy);expect(byId.get('cabin')!.dy).toBeGreaterThan(byId.get('other')!.dy);
  expect(new Set(layout.map(n=>[n.dx,n.dy].join(','))).size).toBe(4);
  expect(layoutConnections(graph,'start')).toEqual(layout);expect(JSON.stringify(graph)).toBe(original);
 });
 it('retains direction and every restriction in passage labels',()=>{
  const edge={from:'a',to:'b',name:'Gate',direction:'north',automatic:false,hidden:true,pick_difficulty:20,conditions:[{}],requires_mode:'rail',allow_vehicles:false};
  expect(passageLabel(edge)).toBe('Gate · north · hidden · locked · conditional · needs rail · on foot');
  expect(passageStyle(edge).dash).toBe('3 5');
  expect(passageLabel({...edge,automatic:true})).toContain('automatic');
 });
});
