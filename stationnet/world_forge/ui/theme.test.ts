import {describe,it,expect,vi,afterEach} from 'vitest';
import {alpha,edgeColor,localPoint,mix,readPalette,terrainColor} from './theme';

afterEach(()=>{vi.restoreAllMocks();document.documentElement.removeAttribute('style');});

describe('station theme',()=>{
 it('reads the station tokens, so map colours follow the chosen theme',()=>{
  document.documentElement.style.setProperty('--green','#ffb000');
  document.documentElement.style.setProperty('--cyan','#3399ff');
  const palette=readPalette();
  expect(palette.green).toBe('#ffb000');
  expect(edgeColor('vehicle',palette)).toBe('#3399ff');
  // Terrain is the theme hue darkened toward the station black, never a fixed colour.
  expect(terrainColor('water',palette)).toBe(mix(palette.black,'#3399ff',.26));
  expect(terrainColor('forest',palette)).not.toBe(terrainColor('forest',{...palette,green:'#00ed45'}));
 });
 it('mixes and fades hex and rgb colours',()=>{
  expect(mix('#000000','#ffffff',.5)).toBe('rgb(128,128,128)');
  expect(alpha('rgb(10, 20, 30)',.5)).toBe('rgba(10,20,30,0.5)');
 });
 it('maps pointer positions through the page zoom',()=>{
  const element=document.createElement('canvas');
  // html{zoom:1.25}: the element is 800 CSS pixels wide but 1000 pixels on screen.
  vi.spyOn(element,'getBoundingClientRect').mockReturnValue({left:100,top:50,width:1000,height:500,right:1100,bottom:550,x:100,y:50,toJSON(){}} as DOMRect);
  Object.defineProperty(element,'clientWidth',{value:800});Object.defineProperty(element,'clientHeight',{value:400});
  const point=localPoint(element,600,300);
  expect(point.x).toBe(400);expect(point.y).toBe(200);expect(point.scale).toBe(.8);
 });
});
