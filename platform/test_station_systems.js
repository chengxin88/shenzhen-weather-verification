'use strict';
/* WN3 demo aliases are individual chips in the redesigned model picker.
   They share the WN3 source rows, keep their identity in CSV, and disappear
   when the element has no WN3 pairing. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check,label){for(let i=0;i<600;i++){if(check())return;await pause(5);}throw Error('Station-system condition timed out: '+(label||''));}
async function run(){
 const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{url:'http://localhost/Verification/test/#grid&ref=station',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,doc=w.document,calls=[],errors=[];
 const WN3='Google WeatherNext 3（集合均值）',real=['ECMWF-IFS-9km-OpenMeteo','ECMWF-IFS',WN3];
 const profiles=[
  {id:'wn3-demo:cma-gd-ec-mt-500m',label:'CMA-GD(EC-MT) 500m',group:'nowcasting',target_product_id:'cma-gd-ec-mt-500m'},
  {id:'wn3-demo:cma-gd-ec-mt-1km',label:'CMA-GD(EC-MT) 1km',group:'nowcasting',target_product_id:'cma-gd-ec-mt-1km'},
  {id:'wn3-demo:cma-gd-3km',label:'CMA-GD 3km',group:'short_range',target_product_id:'cma-gd-3km'},
  {id:'wn3-demo:cma-trams',label:'CMA-TRAMS',group:'typhoon',target_product_id:'cma-trams'}];
 const profile=id=>profiles.find(p=>p.id===id);
 w.DemoModels={ready:async()=>{},profiles:()=>profiles,profile,label:id=>profile(id).label+' · WN3演示',color:()=> '#397c91',
  groups:()=>({nowcasting:'短临预报系统',short_range:'短期预报系统',typhoon:'台风预报系统'}),gridNotice(){}};
 w.AbortSignal.timeout=()=>new w.AbortController().signal;
 w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>{};w.HTMLAnchorElement.prototype.click=function(){};
 w.addEventListener('error',event=>errors.push(event.error));
 w.eval(fs.readFileSync('core.js','utf8'));
 let exported=[];const csv=w.VerificationCore.csv;
 w.VerificationCore.csv=(rows,columns)=>{exported=rows;return csv(rows,columns);};
 const {start,end}=w.VerificationCore.calendarWindow(3),times=[start+'T08:00:00',end+'T08:00:00'];
 const base={source:'station',models:real,supported_models:real,leads:[24,48],times,start:times[0],end:times[1],updated:times[1],reference:'华南站点实况',region:'华南',has_n:true};
 const temperature={...base,id:'station-气温-2m',kind:'气温',var:'t',accumulation:null,level:null,thresholds:[]};
 const rain={...base,id:'station-降水-6',kind:'降水',var:'rain6',accumulation:6,level:null,thresholds:[.1,10],models:real.slice(0,2),supported_models:real.slice(0,2)};
 const datasets=[temperature,rain],grid=datasets.map(d=>({...d,id:'grid-'+d.var,source:'native'}));
 w.localStorage.setItem('szverify-workbench-v2',JSON.stringify({kind:'气温',datasets:{[temperature.id]:{lead:24,view:'multi'}}}));
 const rowsFor=d=>d.models.flatMap(model=>d.leads.flatMap(lead=>times.flatMap(valid=>(d.kind==='降水'?d.thresholds:[null]).map(threshold=>({dataset:d.id,model,lead,valid,run:valid,threshold,n:10,created:1,
  ...(d.kind==='降水'?{H:2,M:1,F:1}:{RMSE:model===WN3?2:1,ME:0,MAE:1,CC:.9})})))));
 const site={id:'59493',name:'深圳',type:'synop',lon:114,lat:22.5,elevation:24,elevation_flags:['wn3']};
 const response=value=>({ok:true,json:async()=>structuredClone(value)});
 w.fetch=async(path,options={})=>{
  const url=new URL(path,w.location.href);calls.push(url);
  assert(!options.body&&(!options.method||options.method==='GET'));
  if(url.pathname.endsWith('/api/catalog'))return response({datasets:grid,snapshot_id:'fixture',status:{accepted:1,unverified:0,unreadable:0}});
  if(url.pathname.endsWith('/api/rows')){const id=url.searchParams.get('dataset');return response({dataset:id,snapshot_id:'fixture',rows:rowsFor(grid.find(d=>d.id===id))});}
  if(url.pathname.endsWith('/grid-datasets'))return response(datasets);
  if(url.pathname.endsWith('/grid-rows'))return response(rowsFor(datasets.find(d=>d.id===url.searchParams.get('dataset'))));
  if(url.pathname.endsWith('/meta'))return response({stations:[site],coastline:[],models:[{id:'ec01',leads:[24,48]},{id:'ecmwf',leads:[24,48]},{id:'wn3',leads:[24,48]}],variables:[{id:'t',label:'2米气温',unit:'°C'},{id:'rain6',label:'6小时降水',unit:'mm'}]});
  if(url.pathname.endsWith('/map')||url.pathname.endsWith('/series'))assert(real.includes(url.searchParams.get('model')),'station APIs receive the real source');
  if(url.pathname.endsWith('/map'))return response({rows:[{station:site.id,n:10,RMSE:2,ME:0,MAE:1,CC:.9}],sample:'站点配对'});
  if(url.pathname.endsWith('/series'))return response({station:site,series:times.map(valid=>({valid:valid+'Z',fc:20,ob:19,source:'ogimet'}))});
  throw Error('Unexpected API '+url);
 };
 const chosen=()=>[...doc.querySelectorAll('#models [data-model][aria-pressed="true"]')].map(b=>b.dataset.model);
 const chip=id=>doc.querySelector(`#models [data-model="${id}"]`);
 try{
  for(const file of ['app.js','stations.js','grid-stations.js'])w.eval(fs.readFileSync(file,'utf8'));
  assert(await w.GridVerification.open());
  await until(()=>doc.querySelector('#chart svg')&&doc.getElementById('page-title').textContent.startsWith('气温'),'temperature chart');
  const systemTitles=()=>[...doc.querySelectorAll('#models .model-system-group .model-system-title')].map(t=>t.textContent);
  assert.deepEqual(systemTitles(),['短临预报系统','短期预报系统','中期预报系统','台风预报系统'],'chips are grouped by forecast system');
  assert.deepEqual([...doc.querySelectorAll('#models .model-system-group')].map(g=>g.querySelectorAll('[data-model]').length),[2,1,3,1]);
  assert.deepEqual(chosen().sort(),[WN3,'ECMWF-IFS-9km-OpenMeteo'].sort(),'official defaults use EC 9 km and keep WN3');
  assert(chosen().every(id=>!profile(id)),'demo aliases start unselected');
  for(const p of profiles)assert(chip(p.id),'each demo alias is a chip');
  chip(profiles[0].id).click();chip(profiles[1].id).click();
  await until(()=>doc.getElementById('chart-title').textContent.includes('含 WN3 演示资料'),'demo title');
  assert.equal(chosen().length,4);
  // A system name selects exactly that system's models; the chips stay toggles.
  doc.querySelector('#models [data-system="global"]').click();
  await until(()=>chosen().length===3&&chosen().every(id=>real.includes(id)),'whole global system selected');
  assert.equal(doc.querySelector('#models [data-system="global"]').getAttribute('aria-pressed'),'true');
  chip(real[1]).click();
  await until(()=>chosen().length===2,'chip toggles inside a system');
  assert.equal(doc.querySelector('#models [data-system="global"]').getAttribute('aria-pressed'),'false');
  chip(profiles[0].id).click();chip(profiles[1].id).click();
  await until(()=>chosen().length===4,'demo aliases reselected');
  // Station map shows one shared WN3 source for both aliases.
  [...doc.querySelectorAll('#detail-nav [data-page="map"]')][0].click();
  await until(()=>doc.querySelector('#grid-station-map svg')&&doc.querySelector('#grid-station-detail-body svg'),'demo map');
  doc.querySelector(`#grid-station-model [data-model="${profiles[0].id}"]`).click();
  await until(()=>doc.getElementById('grid-station-caption').textContent.includes('WN3演示'),'demo map caption');
  assert.match(doc.getElementById('grid-station-map-note').textContent,/共用 WN3 站点配对/);
  assert.equal(calls.filter(u=>u.pathname.endsWith('/series')&&u.searchParams.get('model')===WN3).length,1,'both demo curves share one source request');
  // Export retains demo identity and the source score.
  doc.getElementById('export-csv').click();
  const demoRows=exported.filter(row=>row.is_demo);
  assert(demoRows.length>=2,'demo rows are exported');
  assert(demoRows.every(row=>row.source_model===WN3&&row.excluded_from_official_scores===true&&row.MAE===1),'demo identity and source score are retained');
  // Grid reference exposes the same alias chips.
  doc.querySelector('#reference-switch [data-reference="grid"]').click();
  await until(()=>!w.GridVerification.selection().station&&doc.querySelector('#chart svg'),'grid demo');
  for(const p of profiles)assert(chip(p.id)&&chip(p.id).disabled===false,'aliases stay available on the grid product');
  // A rainfall product without WN3 pairing disables every demo alias.
  [...doc.querySelectorAll('#detail-nav [data-kind]')].find(b=>b.textContent.includes('降水')).click();
  await until(()=>doc.getElementById('page-title').textContent.startsWith('降水')&&doc.querySelector('#chart svg'),'rain page');
  for(const p of profiles)assert(chip(p.id)&&chip(p.id).disabled===true,'missing WN3 source disables its aliases');
  assert.equal(chip(WN3),null,'WN3 is not offered when the product has no WN3 pairing');
  assert.deepEqual(errors,[]);
  console.log('PASS grouped demo chips, shared WN3 source, demo map/series, export identity and missing-source states');
 }finally{dom.window.close();}
}
run().catch(error=>{console.error(error);process.exitCode=1;});
