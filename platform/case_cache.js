'use strict';
// Per-tab caches hold scores and rendered SVGs, never credentials or raw fields.
window.CaseViewCache=(()=>{
 const storageKey='verification-case-views-v1',maxEntries=12,maxCharts=24,maxBytes=2500000;
 let scope='',entries=new Map();
 const valid=data=>data&&Array.isArray(data.summary)&&Array.isArray(data.scores)&&Array.isArray(data.issues)&&data.selection;
 function persist(){
  try{
   let saved=JSON.stringify({scope,entries:[...entries]});
   while(saved.length>maxBytes&&entries.size>1){entries.delete(entries.keys().next().value);saved=JSON.stringify({scope,entries:[...entries]});}
   if(saved.length<=maxBytes)sessionStorage.setItem(storageKey,saved);
   else sessionStorage.removeItem(storageKey);
  }catch{/* Storage may be unavailable or full; the in-memory cache still works. */}
 }
 function touch(key){const entry=entries.get(key);if(entry){entries.delete(key);entries.set(key,entry);}return entry;}
 return {
  useScope(next){
   if(scope===next)return;
   scope=next;entries=new Map();
   try{
    const saved=JSON.parse(sessionStorage.getItem(storageKey)||'null');
    if(saved?.scope===scope&&Array.isArray(saved.entries)){
     for(const pair of saved.entries.slice(-maxEntries)){
      if(Array.isArray(pair)&&typeof pair[0]==='string'&&valid(pair[1]?.result))entries.set(pair[0],{result:pair[1].result,charts:pair[1].charts||{}});
     }
    }
   }catch{/* Ignore incomplete storage from a previous page. */}
   persist();
  },
  result(key){return touch(key)?.result||null;},
  saveResult(key,result){
   if(!valid(result))return;
   const old=touch(key);
   entries.set(key,{result,charts:old?.result.computed===result.computed?old.charts:{}});
   while(entries.size>maxEntries)entries.delete(entries.keys().next().value);
   persist();
  },
  chart(key,view){const svg=touch(key)?.charts[view];return typeof svg==='string'?svg:null;},
  saveChart(key,view,svg){
   const entry=touch(key);if(!entry)return;
   entry.charts[view]=svg;
   const views=Object.keys(entry.charts);while(views.length>maxCharts)delete entry.charts[views.shift()];
   persist();
  }
 };
})();
