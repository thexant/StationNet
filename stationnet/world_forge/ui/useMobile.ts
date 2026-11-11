import {useEffect,useState} from 'react';

/** Phone-width layout. Matches the `max-width: 760px` block in style.css. */
export const MOBILE_QUERY='(max-width: 760px)';

export function useMediaQuery(query:string){
 const read=()=>typeof window!=='undefined'&&typeof window.matchMedia==='function'?window.matchMedia(query).matches:false;
 const [matches,setMatches]=useState(read);
 useEffect(()=>{
  if(typeof window.matchMedia!=='function')return;
  const list=window.matchMedia(query);const change=()=>setMatches(list.matches);change();
  list.addEventListener?.('change',change);return()=>list.removeEventListener?.('change',change);
 },[query]);
 return matches;
}

export const useMobile=()=>useMediaQuery(MOBILE_QUERY);
