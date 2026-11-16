'use strict';
/* Single-station picker in the filter bar: list choice, typed name/id, unknown text,
   reset, address-bar station on arrival, and the map page following the picker. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check,label){for(let i=0;i<700;i++){if(check())return;await pause(5);}throw Error('Station picker condition timed out: '+(label||''));}
const models=['ECMWF-IFS-9km-OpenMeteo','NCEP-GFS'];
const sites=[{id:'59493',name:'深圳',lon:114,lat:22.5,elevation:60},{id:'59287',name:'广州',lon:113,lat:23,elevation:10},{id:'SZ-1',name:'深圳福田区',lon:114.05,lat:22.54,elevation:10},{id:'SZ-10',name:'深圳大鹏新区',lon:114.5,lat:22.6,elevation:10}];
function open(hash){
 const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{url:'http://spark/Verification/test/'+hash,runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,calls=[],errors=[];
 w.AbortSignal.timeout=()=>new w.AbortController().signal;
 w.DemoModels={ready:async()=>{},profiles:()=>[],profile:()=>null,groups:()=>({}),gridNotice(){}};
 w.addEventListener('error',e=>errors.push(e.error));
 w.eval(fs.readFileSync('core.js','utf8'));
 const {start,end}=w.VerificationCore.calendarWindow(3),times=[start+'T08:00:00',start+'T20:00:00',end+'T08:00:00'];
 const datasets=['rain6','t'].map(v=>({id:v==='t'?'station-气温-2m':'station-降水-6',kind:v==='t'?'气温':'降水',var:v,source:'station',
  accumulation:v==='t'?null:6,has_n:true,has_acc:v==='t',models,supported_models:models,start:times[0],end:times.at(-1),updated:times.at(-1),leads:[6,24,48],
  thresholds:v==='t'?[]:[.1,10],reference:'华南站点实况',region:'中国南方'}));
 const regular=datasets.map(ds=>({...ds,id:'grid-'+ds.var,source:'native'}));
 const rows=(ds,site)=>models.flatMap(model=>ds.leads.flatMap(lead=>times.flatMap((valid,i)=>(ds.var==='t'?[null]:ds.thresholds).map(threshold=>({
  dataset:ds.id,model,run:valid,valid,lead,threshold,n:site?1:10,
  ...(ds.var==='t'?(site?{station:site,fc:2*(i+1),ob:i+1,ME:i+1,MAE:i+1,RMSE:i+1,CC:null,N1:2,N2:7}:{ME:1,MAE:2,RMSE:3,CC:.5,N1:2,N2:7}):{H:site?1:3,M:site?0:1,F:site?0:2})
 })))));
 const response=x=>({ok:true,json:async()=>structuredClone(x)});
 w.fetch=async path=>{
  const u=new URL(path,w.location.href);calls.push(u);
  if(u.pathname.endsWith('/api/catalog'))return response({datasets:regular,snapshot_id:'fixture',status:{accepted:1,unverified:0,unreadable:0}});
  if(u.pathname.endsWith('/api/rows')){const ds=regular.find(x=>x.id===u.searchParams.get('dataset'));return response({dataset:ds.id,snapshot_id:'fixture',rows:rows(ds)});}
  if(u.pathname.endsWith('/grid-datasets'))return response(datasets);
  if(u.pathname.endsWith('/grid-rows'))return response(rows(datasets.find(x=>x.id===u.searchParams.get('dataset')),u.searchParams.get('station')));
  if(u.pathname.endsWith('/meta'))return response({stations:sites,coastline:[],models:[{id:'ec01',leads:[6,24,48]},{id:'gfs',leads:[6,24,48]}],variables:datasets.map(x=>({id:x.var,label:x.var==='t'?'2米气温':'6小时降水',unit:x.var==='t'?'°C':'mm'}))});
  if(u.pathname.endsWith('/map'))return response({rows:sites.map(site=>({station:site.id,n:3,TS:.5,RMSE:2,MSE:3})),sample:'区域评分说明'});
  if(u.pathname.endsWith('/series')){const site=u.searchParams.get('station');return response({station:sites.find(x=>x.id===site),series:times.map(valid=>({valid:valid+'Z',fc:2,ob:1,source:'ogimet'}))});}
  throw Error('Unexpected API '+u);
 };
 w.localStorage.setItem('szverify-workbench-v2',JSON.stringify({kind:'降水',datasets:{[datasets[0].id]:{lead:24,models}}}));
 for(const f of ['app.js','stations.js','grid-stations.js'])w.eval(fs.readFileSync(f,'utf8'));
 const input=d.getElementById('station-pick');
 // What a browser does: focus the box, put text in it, then fire input (list choice) or change (typed + Enter/blur).
 const enter=(text,event)=>{input.focus();input.value=text;input.dispatchEvent(new w.Event(event,{bubbles:true}));};
 const scope=()=>w.GridVerification.selection().scoreStation;
 const rowCalls=id=>calls.filter(u=>u.pathname.endsWith('/grid-rows')&&u.searchParams.get('station')===id).length;
 return {w,d,calls,errors,input,enter,scope,rowCalls};
}
async function run(){
 let page=open('#grid&ref=station');
 try{
  const {w,d,input,enter,scope,rowCalls,errors}=page;
  assert(await w.GridVerification.open());
  await until(()=>d.querySelector('#chart svg')&&w.GridVerification.selection().station,'initial chart');
  assert.equal(d.getElementById('station-field').hidden,false);
  await until(()=>d.querySelectorAll('#station-pick-list option').length===sites.length,'station list');
  assert.equal(d.querySelector('#station-pick-list option').value,'深圳（59493）');
  assert.equal(d.getElementById('station-score-reset').hidden,true);
  // Choosing from the list.
  enter('广州（59287）','input');
  await until(()=>scope()?.id==='59287'&&d.getElementById('chart-title').textContent.includes('广州（59287）'),'list choice');
  assert.equal(rowCalls('59287'),1);
  assert.equal(input.value,'广州（59287）');
  assert(input.classList.contains('chosen'));
  assert.equal(d.getElementById('station-score-reset').hidden,false);
  assert.equal(new URLSearchParams(w.location.hash).get('station'),'59287');
  assert(![...d.querySelectorAll('#region-switch button')].some(b=>b.getAttribute('aria-pressed')==='true'));
  // Focusing empties the box so the browser can offer the full list; leaving restores it.
  input.focus();assert.equal(input.value,'');input.blur();await pause(20);assert.equal(input.value,'广州（59287）');
  // Typing while a longer id shares the prefix must not jump to the shorter one.
  enter('深圳福田区（SZ-1','input');await pause(30);assert.equal(scope().id,'59287');input.blur();await pause(20);
  // Typed station id, then a typed fragment that matches one station only.
  enter('59493','change');await until(()=>scope()?.id==='59493'&&input.value==='深圳（59493）','typed id');
  enter('大鹏','change');await until(()=>scope()?.id==='SZ-10','typed fragment');
  enter('深圳','change');await until(()=>scope()?.id==='59493','exact name wins over fragments');
  // Unknown or ambiguous text keeps the current station and says so.
  enter('没有这个站','change');await pause(30);
  assert.equal(scope().id,'59493');assert.match(input.placeholder,/没有找到/);
  input.blur();await pause(20);assert.equal(input.value,'深圳（59493）');
  enter('SZ-','change');await pause(30);assert.equal(scope().id,'59493');assert.match(input.placeholder,/多个站/);input.blur();await pause(20);
  // The map page follows the picker.
  [...d.querySelectorAll('#detail-nav [data-page="map"]')][0].click();
  await until(()=>d.getElementById('grid-station-detail-body')?.textContent.includes('59493')&&d.querySelector('#grid-station-detail-body svg'),'map follows scope');
  enter('广州（59287）','input');
  await until(()=>scope()?.id==='59287'&&d.getElementById('grid-station-detail-body')?.textContent.includes('59287')&&d.querySelector('#grid-station-detail-body svg'),'map follows picker');
  // Back to the region.
  d.getElementById('station-score-reset').click();
  await until(()=>scope()===null&&d.getElementById('station-score-reset').hidden&&input.value==='','reset');
  assert.equal(new URLSearchParams(w.location.hash).get('station'),null);
  assert(!input.classList.contains('chosen'));
  // A region button also leaves the single station.
  enter('广州（59287）','input');await until(()=>scope()?.id==='59287','pick again');
  d.querySelector('#region-switch [data-region="gba"]').click();
  await until(()=>scope()===null&&input.value===''&&d.querySelector('#region-switch [data-region="gba"]').getAttribute('aria-pressed')==='true','region leaves station');
  // The grid reference has no stations to choose from.
  [...d.querySelectorAll('#reference-switch button')].find(b=>!/站/.test(b.textContent)).click();
  await until(()=>d.getElementById('station-field').hidden,'hidden for grid reference');
  assert.deepEqual(errors,[]);
 }finally{page.w.close();}
 // Arriving with a station in the address bar.
 page=open('#grid&page=%E9%99%8D%E6%B0%B4%3ATS&ref=station&station=59287');
 try{
  const {w,d,input,scope,rowCalls,calls,errors}=page;
  assert(await w.GridVerification.open());
  await until(()=>d.querySelector('#chart svg')&&d.getElementById('chart-title').textContent.includes('广州（59287）')&&input.value==='广州（59287）','arrival with station');
  assert.equal(scope().id,'59287');assert(rowCalls('59287')>=1);
  assert(!calls.some(u=>u.pathname.endsWith('/grid-rows')&&!u.searchParams.get('station')),'no region rows fetched first');
  assert.equal(new URLSearchParams(w.location.hash).get('station'),'59287');
  assert.deepEqual(errors,[]);
 }finally{page.w.close();}
 // An unknown station in the address bar falls back to the region.
 page=open('#grid&ref=station&station=00000');
 try{
  const {w,d,input,scope,errors}=page;
  assert(await w.GridVerification.open());
  await until(()=>scope()===null&&d.querySelector('#chart svg')&&input.value==='','unknown station falls back');
  await until(()=>new URLSearchParams(w.location.hash).get('station')===null,'unknown station leaves the address');
  assert.deepEqual(errors,[]);
 }finally{page.w.close();}
 console.log('PASS station picker: list choice, typed id/name, unknown text, map follows, reset, region, address-bar station');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
