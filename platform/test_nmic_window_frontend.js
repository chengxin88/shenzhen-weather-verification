'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),{test}=require('node:test'),{JSDOM}=require('jsdom');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check){for(let i=0;i<500;i++){if(check())return;await pause(5);}throw Error('NMIC DOM condition timed out');}
const html=()=>fs.readFileSync('index.html','utf8');
const evaluate=(w,files)=>files.forEach(file=>w.eval(fs.readFileSync(file,'utf8')));

test('adjusted overview dates and plain explanation; unadjusted caption unchanged',async()=>{
 const dom=new JSDOM(html(),{url:'http://localhost/Verification/test/',runScripts:'outside-only'}),w=dom.window,d=w.document;
 w.fetch=async path=>{
  const region=new URL(path,w.location.href).searchParams.get('region'),adjusted=['hn','gba'].includes(region);
  return {ok:true,json:async()=>({start:adjusted?'2026-11-02T02:00:00':'2026-11-04T08:00:00',
   end:adjusted?'2026-11-09T02:00:00':'2026-11-11T08:00:00',station_count:5,rows:[],
   ...(adjusted?{data_end_reason:'nmic'}:{})})};
 };
 try{
  evaluate(w,['overview.js']);await w.AssessmentOverview.open();
  const caption=()=>d.getElementById('overview-caption').textContent,original=caption();
  assert.equal(original,'降水 · 近7天（11-04 至 11-11，北京时） · 第1天 24小时雨量 · 南方地区 5 个站');
  const region=d.getElementById('overview-region');
  for(const value of ['hn','gba']){
   region.value=value;region.dispatchEvent(new w.Event('change'));
   await until(()=>d.getElementById('overview-workspace').getAttribute('aria-busy')==='false');
   assert.match(caption(),/11-02 至 11-09，北京时/);
   assert.match(caption(),/国家站资料到 11 月 9 日，窗口截止到这一天（北京时间）/);
  }
  region.value='all';region.dispatchEvent(new w.Event('change'));
  await until(()=>d.getElementById('overview-workspace').getAttribute('aria-busy')==='false');
  assert.equal(caption(),original);
  assert.equal(d.querySelectorAll('#overview-workspace select').length,4);
 }finally{dom.window.close();}
});

test('shared detail dates: majority regions, BJT day, presets, manual dates, map and restoration',async()=>{
 const dom=new JSDOM(html(),{url:'http://localhost/Verification/test/#grid&ref=station',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,calls=[],errors=[];
 w.Date.now=()=>Date.parse('2026-11-11T04:00:00Z');
 w.AbortSignal.timeout=()=>new w.AbortController().signal;
 w.addEventListener('error',e=>errors.push(e.error));
 w.DemoModels={ready:async()=>{},profiles:()=>[],profile:()=>null,groups:()=>({}),gridNotice(){}};
 const model='ECMWF-IFS-9km-OpenMeteo';
 const station={id:'59493',name:'深圳',type:'synop',lon:114,lat:22.5,elevation:60,sources:['nmic']};
 const policy={window_end:'2026-11-11T00:00:00Z',nmic_end:'2026-11-08T18:00:00Z',
  region_nmic_majority:{all:false,hn:true,gba:true,hk:false,sz:false,xj:false}};
 const dataset={id:'station-气温-2m',kind:'气温',source:'station',var:'t',has_n:true,
  models:[model],supported_models:[model],start:'2026-10-12T08:00:00',end:'2026-11-11T08:00:00',updated:'2026-11-11T08:00:00',
  leads:[24],thresholds:[],times:['2026-11-11T08:00:00'],reference:'华南站点实况',region:'南方',...policy};
 const grid={...dataset,id:'grid-t',source:'native',reference:'深圳格点分析场'};
 const rows=Array.from({length:31},(_,i)=>({dataset:dataset.id,model,lead:24,valid:new Date(Date.UTC(2026,9,12+i,8)).toISOString().slice(0,19),
  run:'2026-11-08T08:00:00',n:1,ME:1,MAE:1,RMSE:1,CC:.5,N1:1,N2:1}));
 const response=value=>({ok:true,json:async()=>structuredClone(value)});
 w.fetch=async path=>{
  const url=new URL(path,w.location.href);calls.push(url);
  if(url.pathname.endsWith('/api/catalog'))return response({datasets:[grid],snapshot_id:'fixture',status:{accepted:1,unverified:0,unreadable:0}});
  if(url.pathname.endsWith('/api/rows'))return response({dataset:grid.id,snapshot_id:'fixture',rows:rows.map(r=>({...r,dataset:grid.id}))});
  if(url.pathname.endsWith('/grid-datasets'))return response([dataset]);
  if(url.pathname.endsWith('/grid-rows'))return response(rows.filter(r=>r.valid.slice(0,10)>=url.searchParams.get('start')&&r.valid.slice(0,10)<=url.searchParams.get('end')));
  if(url.pathname.endsWith('/meta'))return response({...policy,stations:[station],coastline:[],models:[{id:'ec01',leads:[24]}],variables:[{id:'t',label:'气温',unit:'℃'}]});
  if(url.pathname.endsWith('/map'))return response({rows:[{station:station.id,n:1,MAE:1}],sample:'配对样本'});
  if(url.pathname.endsWith('/series'))return response({station,series:[{valid:'2026-11-08T18:00:00Z',fc:20,ob:19,source:'nmic'}]});
  throw Error('Unexpected request '+url);
 };
 w.localStorage.setItem('szverify-workbench-v2',JSON.stringify({kind:'气温',datasets:{}}));
 const dates=()=>[d.getElementById('start').value,d.getElementById('end').value];
 const note=()=>d.getElementById('filter-note').textContent;
 const idle=()=>d.getElementById('grid-workspace').getAttribute('aria-busy')==='false'&&d.querySelector('[data-days]');
 const quick=async days=>{d.querySelector(`[data-days="${days}"]`).click();await until(idle);};
 const lastRows=()=>calls.filter(u=>u.pathname.endsWith('/grid-rows')).at(-1);
 try{
  evaluate(w,['core.js','app.js','stations.js','grid-stations.js']);
  assert(await w.GridVerification.open());await until(idle);
  assert.deepEqual(dates(),['2026-11-05','2026-11-11']);
  const originalNote=note(),controlCount=d.querySelectorAll('#grid-workspace .filter-card input,#grid-workspace .filter-card select').length;
  await w.GridVerification.setStationRegion('hn');
  assert.deepEqual(dates(),['2026-11-03','2026-11-09']);
  assert.equal(lastRows().searchParams.get('end'),'2026-11-09');
  assert.match(note(),/国家站资料到 11 月 9 日，默认和快捷日期窗口截止到这一天（北京时间）/);
  for(const [days,start] of [[3,'2026-11-07'],[7,'2026-11-03'],[15,'2026-10-26']]){
   await quick(days);assert.deepEqual(dates(),[start,'2026-11-09']);
   assert(calls.some(u=>u.pathname.endsWith('/grid-rows')&&u.searchParams.get('start')===start&&u.searchParams.get('end')==='2026-11-09'));
  }
  await quick(7);
  await w.GridVerification.setStationRegion('all');
  assert.deepEqual(dates(),['2026-11-05','2026-11-11']);assert.equal(note(),originalNote);
  await w.GridVerification.setStationRegion('gba');
  assert.deepEqual(dates(),['2026-11-03','2026-11-09']);
  // The map and detail card use the dates chosen by the shared date controls.
  d.querySelector('#detail-nav [data-page="map"]').click();
  await until(()=>d.querySelector('#grid-station-detail-body svg'));
  assert(calls.filter(u=>u.pathname.endsWith('/map')||u.pathname.endsWith('/series')).every(u=>u.searchParams.get('end')==='2026-11-09'));
  await w.GridVerification.setStationRegion('sz');
  await until(()=>d.getElementById('grid-station-caption')?.textContent.includes('2026-11-11'));
  assert.deepEqual(dates(),['2026-11-05','2026-11-11']);assert.doesNotMatch(note(),/国家站资料到/);
  await w.GridVerification.setStationRegion('hn');
  const end=d.getElementById('end');end.value='2026-11-11';end.dispatchEvent(new w.Event('change'));
  await until(()=>idle()&&lastRows().searchParams.get('region')==='hn'&&lastRows().searchParams.get('end')==='2026-11-11');
  assert.equal(end.value,'2026-11-11','manual dates can extend past NMIC');
  await w.GridVerification.setStationRegion('hk');
  assert.equal(end.value,'2026-11-11','manual dates survive region changes');
  await w.GridVerification.setStationRegion('hn');
  await quick(3);assert.deepEqual(dates(),['2026-11-07','2026-11-09']);
  // A new snapshot advances a preset, while manual ranges stay as selected.
  dataset.nmic_end='2026-11-09T18:00:00Z';
  assert(await w.GridVerification.refresh());await until(idle);
  assert.deepEqual(dates(),['2026-11-08','2026-11-10']);
  assert.match(note(),/国家站资料到 11 月 10 日/);
  for(const value of [null,'2026-11-12T00:00:00Z']){
   dataset.nmic_end=value;
   assert(await w.GridVerification.refresh());await until(idle);
   assert.deepEqual(dates(),['2026-11-09','2026-11-11']);
   assert.doesNotMatch(note(),/国家站资料到/);
  }
  assert.equal(d.querySelectorAll('#grid-workspace .filter-card input,#grid-workspace .filter-card select').length,controlCount,'no new filter controls');
  assert.deepEqual(errors,[]);
 }finally{dom.window.close();}
});
