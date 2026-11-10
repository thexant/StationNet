import {describe,it,expect,vi,afterEach} from 'vitest';
import {api,configure} from './data';

afterEach(()=>vi.unstubAllGlobals());

describe('station API client',()=>{
 it('sends the page CSRF token and keeps requests same-origin',async()=>{
  configure({base:'/mud/forge/api',csrf:'page-token'});
  const fetch=vi.fn(async()=>({ok:true,json:async()=>({done:true})}));vi.stubGlobal('fetch',fetch);
  expect(await api('/d/w:home/checkpoint',{revision:3})).toEqual({done:true});
  const [url,options]=fetch.mock.calls[0] as any;
  expect(url).toBe('/mud/forge/api/d/w:home/checkpoint');
  expect(options.headers['X-CSRF-Token']).toBe('page-token');
  expect(options.credentials).toBe('same-origin');
 });
 it('reports the station error message',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status:409,json:async()=>({detail:'This draft changed in another tab.'})})));
  await expect(api('/d/x/transaction',{})).rejects.toThrow('This draft changed in another tab.');
 });
});
