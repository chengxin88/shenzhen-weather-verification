'use strict';
/* Surface elements are element → metric pages. Station and grid references
   carry their own metric set; 100 m wind is grid-only. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitFor(predicate,label){for(let n=0;n<600;n++){if(predicate())return;await pause(5);}throw Error('Surface station condition timed out: '+(label||''));}
async function run(){
 const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{url:'http://localhost/Verification/test/#grid',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,calls=[],errors=[],model='ECMWF-IFS-9km-OpenMeteo';
 w.addEventListener('error',e=>errors.push(e.error));
 w.AbortSignal.timeout=()=>new w.AbortController().signal;
 w.DemoModels={ready:async()=>{},profiles:()=>[],profile:()=>null,groups:()=>({}),gridNotice(){}};
 w.eval(fs.readFileSync('core.js','utf8'));
 const {start,end}=w.VerificationCore.calendarWindow(3),times=[start+'T08:00:00',end+'T08:00:00'];
 const base={kind:'地面',accumulation:null,has_n:true,models:[model],supported_models:[model],start:times[0],end:times[1],updated:times[1],leads:[24,48],thresholds:[],region:'华南'};
 const grids=[['mslp','海平面气压','hPa'],['wind10','10 米风场','m/s'],['wind100','100 米风场','m/s']].map(([element,label,unit])=>({...base,id:'grid-'+element,source:'analysis',element,label,unit,reference:'ERA5 再分析'}));
 const stations=[['msl','mslp','海平面气压','hPa'],['wind','wind10','10米风速','m/s']].map(([v,element,label,unit])=>({...base,id:'station-'+v,source:'station',var:v,element,label,unit,reference:'华南站点实况'}));
 const site={id:'59493',name:'深圳',lon:114,lat:22.5,elevation:60};
 const rows=ds=>ds.leads.flatMap(lead=>times.map(valid=>({dataset:ds.id,model,lead,valid,run:valid,threshold:null,n:3,ME:1,MAE:2,RMSE:3,CC:.9,VRMSE:4})));
 const response=x=>({ok:true,json:async()=>structuredClone(x)});
 w.fetch=async path=>{
  const u=new URL(path,w.location.href);calls.push(u);
  if(u.pathname.endsWith('/api/catalog'))return response({datasets:grids,snapshot_id:'fixture',status:{accepted:1,unverified:0,unreadable:0}});
  if(u.pathname.endsWith('/api/rows')){const ds=grids.find(x=>x.id===u.searchParams.get('dataset'));return response({dataset:ds.id,snapshot_id:'fixture',rows:rows(ds)});}
  if(u.pathname.endsWith('/grid-datasets'))return response(stations);
  if(u.pathname.endsWith('/grid-rows'))return response(rows(stations.find(x=>x.id===u.searchParams.get('dataset'))));
  if(u.pathname.endsWith('/meta'))return response({stations:[site],coastline:[],models:[{id:'ec01',leads:[24,48]}],variables:stations.map(x=>({id:x.var,label:x.label,unit:x.unit}))});
  if(u.pathname.endsWith('/map'))return response({rows:[{station:site.id,n:3,ME:1,MAE:2,RMSE:3,CC:.9}],sample:'站点配对'});
  if(u.pathname.endsWith('/series'))return response({station:site,series:times.map(valid=>({valid:valid+'Z',fc:5,ob:4,source:'ogimet'}))});
  throw Error('Unexpected API '+u);
 };
 const group=name=>[...d.querySelectorAll('#detail-nav [data-kind]')].find(x=>x.textContent.includes(name));
 const refButton=name=>[...d.querySelectorAll('#reference-switch button')].find(x=>x.textContent===name);
 try{
  for(const f of ['app.js','stations.js','grid-stations.js'])w.eval(fs.readFileSync(f,'utf8'));
  assert(await w.GridVerification.open());
  await waitFor(()=>d.querySelector('#chart svg')&&d.getElementById('page-title').textContent.startsWith('地面气压'),'first surface page');
  assert.match(d.getElementById('page-title').textContent,/地面气压 · 均方根误差/);
  assert([...d.getElementById('metric').options].map(x=>x.value).join()==='RMSE,MAE,ME,CC');
  assert.match(d.getElementById('results-table').textContent,/均方根误差 \(hPa\)/);
  assert.equal(d.getElementById('reference-switch').hidden,false,'pressure has both station and grid references');
  group('10 米风').click();
  await waitFor(()=>d.getElementById('page-title').textContent.startsWith('10 米风')&&d.querySelector('#chart svg'),'10m wind grid');
  assert.deepEqual([...d.getElementById('metric').options].map(x=>x.value),['VRMSE']);
  assert.match(d.getElementById('results-table').textContent,/风矢量均方根误差/);
  // Station reference changes the metric set to scalar wind errors.
  assert.equal(await w.GridVerification.setReference('station'),true);
  await waitFor(()=>w.GridVerification.selection().station&&d.querySelector('#chart svg'),'10m wind station');
  assert.match(d.getElementById('page-title').textContent,/均方根误差/);
  assert.deepEqual([...d.getElementById('metric').options].map(x=>x.value),['RMSE','MAE','ME','CC']);
  assert.match(d.getElementById('results-table').textContent,/均方根误差 \(m\/s\)/);
  // Station distribution page.
  [...d.querySelectorAll('#detail-nav [data-page="map"]')][0].click();
  await waitFor(()=>d.querySelector('#grid-station-map svg')&&d.querySelector('#grid-station-detail-body svg'),'surface map page');
  assert(d.getElementById('grid-station-caption').textContent.includes('起报'));
  // Back to grid.
  refButton('格点分析场').click();
  await waitFor(()=>!w.GridVerification.selection().station&&d.querySelector('#chart svg'),'back to grid');
  // 100 m wind is grid-only and hides the reference switch.
  group('100 米风').click();
  await waitFor(()=>d.getElementById('page-title').textContent.startsWith('100 米风')&&d.querySelector('#chart svg'),'100m wind');
  assert.equal(d.getElementById('reference-field').hidden,true);
  assert.deepEqual([...d.getElementById('metric').options].map(x=>x.value),['VRMSE']);
  assert.equal(w.GridVerification.selection().element,'wind100');
assert.equal(w.location.hash.includes('ref=grid'),true);
  assert.deepEqual(errors,[]);
  console.log('PASS surface element pages, grid/station metric sets, map page and 100 m wind reference exclusion');
 }finally{dom.window.close();}
}
run().catch(e=>{console.error(e);process.exitCode=1;});
