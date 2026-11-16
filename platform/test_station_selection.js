'use strict';
/* Single-station scope on the map page: click changes scores, reset, empty
   stations, stale responses, run hour, date reload and temperature CC. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check,label){for(let i=0;i<700;i++){if(check())return;await pause(5);}throw Error('Selected station condition timed out: '+(label||''));}
async function run(){
 const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{url:'http://spark/Verification/test/#grid&ref=station',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,calls=[],errors=[];
 w.AbortSignal.timeout=()=>new w.AbortController().signal;
 w.DemoModels={ready:async()=>{},profiles:()=>[],profile:()=>null,groups:()=>({}),gridNotice(){}};
 w.addEventListener('error',e=>errors.push(e.error));
 w.eval(fs.readFileSync('core.js','utf8'));
 const models=['ECMWF-IFS-9km-OpenMeteo','NCEP-GFS'];
 const {start,end}=w.VerificationCore.calendarWindow(3),times=[start+'T08:00:00',start+'T20:00:00',end+'T08:00:00'];
 const sites=[{id:'59493',name:'深圳',lon:114,lat:22.5,elevation:60},{id:'59287',name:'广州',lon:113,lat:23,elevation:10},{id:'empty',name:'无配对站',lon:115,lat:24,elevation:10}];
 const datasets=['rain6','t'].map(v=>({id:v==='t'?'station-气温-2m':'station-降水-6',kind:v==='t'?'气温':'降水',var:v,source:'station',
  accumulation:v==='t'?null:6,has_n:true,has_acc:v==='t',models,supported_models:models,start:times[0],end:times.at(-1),updated:times.at(-1),leads:[6,24,48],
  thresholds:v==='t'?[]:[.1,10],reference:'华南站点实况',region:'中国南方'}));
 const regular=datasets.map(ds=>({...ds,id:'grid-'+ds.var,source:'native'}));
 let delayed=false,release;
 const rows=(ds,site)=>site==='empty'?[]:models.flatMap(model=>ds.leads.flatMap(lead=>times.flatMap((valid,i)=>(ds.var==='t'?[null]:ds.thresholds).map(threshold=>({
  dataset:ds.id,model,run:valid,valid,lead,threshold,n:site?1:10,
  ...(ds.var==='t'?(site?{station:site,fc:2*(i+1),ob:i+1,ME:i+1,MAE:i+1,RMSE:i+1,CC:null,N1:2,N2:7}:{ME:1,MAE:2,RMSE:3,CC:.5,N1:2,N2:7}):
   {H:site?(site==='59287'?1:0):3,M:site?(site==='59493'?1:0):1,F:site?0:2})
 })))));
 const response=x=>({ok:true,json:async()=>structuredClone(x)});
 w.fetch=async path=>{
  const u=new URL(path,w.location.href);calls.push(u);
  if(u.pathname.endsWith('/api/catalog'))return response({datasets:regular,snapshot_id:'fixture',status:{accepted:1,unverified:0,unreadable:0}});
  if(u.pathname.endsWith('/api/rows')){const ds=regular.find(x=>x.id===u.searchParams.get('dataset'));return response({dataset:ds.id,snapshot_id:'fixture',rows:rows(ds)});}
  if(u.pathname.endsWith('/grid-datasets'))return response(datasets);
  if(u.pathname.endsWith('/grid-rows')){
   const site=u.searchParams.get('station');
   if(delayed&&site==='59493')await new Promise(r=>{release=r;});
   return response(rows(datasets.find(x=>x.id===u.searchParams.get('dataset')),site));
  }
  if(u.pathname.endsWith('/meta'))return response({stations:sites,coastline:[],models:[{id:'ec01',leads:[6,24,48]},{id:'gfs',leads:[6,24,48]}],variables:datasets.map(x=>({id:x.var,label:x.var==='t'?'2米气温':'6小时降水',unit:x.var==='t'?'°C':'mm'}))});
  if(u.pathname.endsWith('/map'))return response({rows:sites.map(site=>({station:site.id,n:3,TS:.5,RMSE:2,MSE:3})),sample:'区域评分说明'});
  if(u.pathname.endsWith('/series')){
   const site=u.searchParams.get('station');
   return response({station:sites.find(x=>x.id===site),series:site==='empty'?[]:times.map(valid=>({valid:valid+'Z',fc:2,ob:1,source:'ogimet'}))});
  }
  throw Error('Unexpected API '+u);
 };
 w.localStorage.setItem('szverify-workbench-v2',JSON.stringify({kind:'降水',datasets:{[datasets[0].id]:{lead:24,models:['ECMWF-IFS-9km-OpenMeteo','NCEP-GFS']}}}));
 const refButton=name=>[...d.querySelectorAll('#reference-switch button')].find(b=>b.textContent===name);
 const mapButton=()=>[...d.querySelectorAll('#detail-nav [data-page="map"]')][0];
 const choose=id=>d.querySelector(`#grid-station-map [data-station-id="${id}"]`).dispatchEvent(new w.MouseEvent('click',{bubbles:true}));
 const detail=()=>d.getElementById('grid-station-detail-body')?.textContent||'';
 try{
  for(const f of ['app.js','stations.js','grid-stations.js'])w.eval(fs.readFileSync(f,'utf8'));
  assert(await w.GridVerification.open());
  await until(()=>d.querySelector('#chart svg')&&w.GridVerification.selection().station,'initial chart');
  mapButton().click();
  await until(()=>d.querySelector('#grid-station-map [data-station-id="59287"]')&&d.querySelector('#grid-station-detail-body svg'),'map');
  choose('59287');await until(()=>w.GridVerification.selection().scoreStation?.id==='59287'&&detail().includes('59287'),'广州');
  assert(calls.some(u=>u.pathname.endsWith('/grid-rows')&&u.searchParams.get('station')==='59287'));
  assert(d.getElementById('grid-station-reset').hidden===false);
  choose('59493');await until(()=>w.GridVerification.selection().scoreStation?.id==='59493'&&detail().includes('59493'),'深圳');
  d.getElementById('grid-station-reset').click();
  await until(()=>w.GridVerification.selection().scoreStation===null&&d.getElementById('grid-station-reset').hidden===true,'reset');
  // Run hour.
  d.querySelector('#hour-switch [data-hour="08"]').click();
  await until(()=>w.GridVerification.selection().hour==='08'&&d.querySelector('#grid-station-map [data-station-id="59287"]'),'hour');
  // Empty station must not keep another station's chart.
  await until(()=>d.querySelector('#grid-station-map [data-station-id="empty"]'),'empty point');
  choose('empty');await until(()=>w.GridVerification.selection().scoreStation?.id==='empty'&&detail().includes('暂无'),'empty station');
  choose('59287');await until(()=>detail().includes('59287'),'back to 广州');
  // Temperature group and the CC page.
  [...d.querySelectorAll('#detail-nav [data-kind]')].find(b=>b.textContent.includes('气温')).click();
  await until(()=>d.getElementById('page-title').textContent.startsWith('气温')&&d.querySelector('#chart svg'),'temperature');
  [...d.querySelectorAll('#detail-nav [data-metric]')].find(b=>b.textContent==='相关系数').click();
  await until(()=>d.getElementById('page-title').textContent.includes('单站时间相关')&&d.querySelector('#chart svg'),'CC');
  assert.match(d.getElementById('results-table').textContent,/单站时间相关 CC|相关系数/);
  assert.match(d.getElementById('chart-explainer').textContent,/单站预报与实况随时间的相关系数/);
  // Map page remains available after the element switch.
  mapButton().click();
  await until(()=>d.querySelector('#grid-station-map svg')&&d.querySelector('#grid-station-detail-body svg'),'temp map');
  // Date change reloads the current station rows.
  const before=calls.length;const startInput=d.getElementById('start');startInput.value=end;startInput.dispatchEvent(new w.Event('change'));
  await until(()=>calls.slice(before).some(u=>u.pathname.endsWith('/grid-rows')&&u.searchParams.get('start')===end),'date reload');
  // Delayed response cannot overwrite a later station.
  d.getElementById('grid-station-reset').click();await pause(60);
  await until(()=>d.querySelector('#grid-station-map [data-station-id="59493"]'),'point after reset');
  delayed=true;choose('59493');await until(()=>release,'delayed request');
  choose('59287');await until(()=>w.GridVerification.selection().scoreStation?.id==='59287'&&detail().includes('59287'),'later station');
  release();await pause(80);
  assert.equal(w.GridVerification.selection().scoreStation?.id,'59287');
  assert(detail().includes('59287'));
  delayed=false;
  // Core correlation keeps the retained raw pairs.
  const C=w.VerificationCore,paired=[1,2,3].map((ob,i)=>({station:'59287',fc:ob*2,ob,n:1,lead:24,valid:String(i)}));
  const score=C.score(paired);assert.equal(score.CC,1);assert.equal(score.n,3);assert.equal(score.RMSE,Math.sqrt(14/3));
  assert.deepEqual(errors,[]);
  console.log('PASS station click/scope/reset, empty station, run hour, temperature CC, date reload and stale-response isolation');
 }finally{w.close();}
}
run().catch(e=>{console.error(e);process.exitCode=1;});
