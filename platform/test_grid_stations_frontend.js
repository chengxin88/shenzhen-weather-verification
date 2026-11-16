/* Map page + single-station card on the redesigned detail page. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitFor(predicate,label){for(let n=0;n<600;n++){if(predicate())return;await pause(5);}throw Error('Grid station DOM condition timed out: '+(label||''));}
async function testGridStations(){
 const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{url:'http://127.0.0.1:8524/Verification/test/#grid&ref=station',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,doc=w.document,calls=[],downloads=[],blobs=[],errors=[];
 w.addEventListener('error',event=>errors.push(event.error));
 w.AbortSignal.timeout=()=>new w.AbortController().signal;
 w.URL.createObjectURL=blob=>{blobs.push(blob);return 'blob:test';};w.URL.revokeObjectURL=()=>{};
 w.HTMLAnchorElement.prototype.click=function(){downloads.push(this.download);};
 const models=['ECMWF-IFS-9km-OpenMeteo','ECMWF-IFS'];
 const sites=[{id:'59493',name:'深圳',type:'synop',lon:114,lat:22.5,elevation:60},{id:'59287',name:'广州',type:'synop',lon:113,lat:23,elevation:10}];
 w.DemoModels={ready:async()=>{},profiles:()=>[],profile:()=>null,groups:()=>({}),gridNotice(){}};
 w.eval(fs.readFileSync('core.js','utf8'));
 const {start,end}=w.VerificationCore.calendarWindow(3),times=[start+'T08:00:00',start+'T20:00:00',end+'T08:00:00',end+'T20:00:00'];
 const datasets=['t','rain6'].map(variable=>({id:variable==='t'?'station-气温-2m':'station-降水-6',kind:variable==='t'?'气温':'降水',source:'station',var:variable,
  accumulation:variable==='t'?null:6,has_n:true,models,supported_models:models,start:times[0],end:times.at(-1),updated:times.at(-1),leads:[24,48],thresholds:variable==='t'?[]:[.1,10],reference:'华南站点实况',region:'华南 108–120°E / 18–27°N'}));
 const gridDatasets=datasets.map(d=>({...d,id:'grid-'+d.var,source:'native',reference:'深圳开放数据格点分析场'}));
 gridDatasets.push({...gridDatasets[1],id:'grid-rain12',accumulation:12});
 const rowsFor=d=>models.flatMap(model=>d.leads.flatMap(lead=>times.flatMap(valid=>(d.kind==='气温'?[null]:d.thresholds).map(threshold=>({dataset:d.id,model,lead,valid,run:valid,threshold,n:3,created:1,
  ...(d.kind==='气温'?{ME:1,MAE:2,RMSE:3,CC:.9,N1:2,N2:7}:{H:1,M:1,F:0})})))));
 const response=value=>({ok:true,json:async()=>structuredClone(value)});
 w.fetch=async(path,options={})=>{
  const url=new URL(path,w.location.href);calls.push(url);
  assert(!options.body&&(!options.method||options.method==='GET'));
  if(url.pathname.endsWith('/api/catalog'))return response({datasets:gridDatasets,snapshot_id:'fixture',status:{accepted:1,unverified:0,unreadable:0}});
  if(url.pathname.endsWith('/api/rows'))return response({dataset:url.searchParams.get('dataset'),snapshot_id:'fixture',rows:rowsFor(gridDatasets.find(d=>d.id===url.searchParams.get('dataset')))});
  if(url.pathname.endsWith('/grid-datasets'))return response(datasets);
  if(url.pathname.endsWith('/grid-rows'))return response(rowsFor(datasets.find(d=>d.id===url.searchParams.get('dataset'))));
  if(url.pathname.endsWith('/meta'))return response({stations:sites,coastline:[],models:[{id:'ec01',leads:[24,48]},{id:'ecmwf',leads:[24,48]}],variables:datasets.map(x=>({id:x.var,label:x.var==='t'?'2米气温':'6小时降水',unit:x.kind==='气温'?'°C':'mm'}))});
  if(url.pathname.endsWith('/map'))return response({rows:sites.map(site=>({station:site.id,n:3,ME:1,MAE:2,RMSE:3,CC:.9,TS:.5,POD:.5,FAR:0,ETS:.2})),sample:'地图按全部配对汇总；主图CC为平均空间相关。'});
  if(url.pathname.endsWith('/series'))return response({station:sites.find(site=>site.id===url.searchParams.get('station')),series:times.map(valid=>({valid:valid+'Z',fc:20,ob:19,source:'ogimet'}))});
  throw Error('Unexpected API '+url);
 };
 w.localStorage.setItem('szverify-workbench-v2',JSON.stringify({kind:'气温',datasets:{[datasets[0].id]:{lead:24,models:['ECMWF-IFS-9km-OpenMeteo'],knownModels:models}}}));
 const refButton=name=>[...doc.querySelectorAll('#reference-switch button')].find(b=>b.textContent===name);
 const mapButton=()=>[...doc.querySelectorAll('#detail-nav [data-page="map"]')][0];
 try{
  for(const file of ['app.js','stations.js','grid-stations.js'])w.eval(fs.readFileSync(file,'utf8'));
  assert(await w.GridVerification.open());
  await waitFor(()=>doc.querySelector('#chart svg')&&doc.getElementById('page-title').textContent.startsWith('气温'),'initial station chart');
  assert.equal(w.GridVerification.selection().station,true);
  assert.deepEqual([...doc.querySelectorAll('#models [data-model][aria-pressed="true"]')].map(b=>b.dataset.model),['ECMWF-IFS-9km-OpenMeteo'],'EC 9 km is the default');
  doc.querySelector('[data-model="ECMWF-IFS"]').click();
  assert.deepEqual([...doc.querySelectorAll('#models [data-model][aria-pressed="true"]')].map(b=>b.dataset.model).sort(),models.slice().sort());
  mapButton().click();
  await waitFor(()=>doc.querySelector('#grid-station-map svg')&&doc.querySelector('#grid-station-detail-body svg'),'map page');
  assert.equal(w.GridVerification.selection().page,'map');
  assert.equal(doc.getElementById('detail-results').hidden,true);
  assert.equal(doc.getElementById('detail-table-card').hidden,true);
  assert.deepEqual([...doc.querySelectorAll('#grid-station-model [data-model]')].map(b=>b.dataset.model),models,'map lists every selected model');
  assert.equal(doc.querySelector('#grid-station-model [aria-pressed="true"]').dataset.model,models[0]);
  doc.querySelector(`#grid-station-model [data-model="${models[1]}"]`).click();
  await waitFor(()=>calls.some(u=>u.pathname.endsWith('/map')&&u.searchParams.get('model')===models[1]),'map model switch');
  const mapLabel=doc.querySelector('#grid-station-model [aria-pressed="true"]').textContent;
  await waitFor(()=>doc.getElementById('grid-station-caption').textContent.startsWith(mapLabel+' ·'),'map caption');
  const mapTitle=()=>[...doc.querySelectorAll('#grid-station-map svg text')].at(-1).textContent;
  const mapIds=()=>[...doc.querySelectorAll('#grid-station-map [data-station-id]')].map(g=>g.dataset.stationId).sort();
  assert(doc.querySelector('#grid-station-map [data-zoom="out"]').disabled,'the whole box cannot zoom out');
  assert.doesNotMatch(mapTitle(),/放大/);assert.deepEqual(mapIds(),['59287','59493']);
  const preset=name=>[...doc.querySelectorAll('#grid-station-map [data-zoom-preset]')].find(b=>b.textContent===name);
  preset('香港').click();assert.match(mapTitle(),/放大 20×/);assert.deepEqual(mapIds(),['59493'],'Hong Kong view draws only stations inside it');
  assert(doc.querySelector('#grid-station-map .stations-point-label'));
  assert.equal(preset('香港').getAttribute('aria-pressed'),'true');
  doc.querySelector('#grid-station-map [data-zoom="out"]').click();assert.match(mapTitle(),/放大 12\.5×/);
  preset('大湾区').click();assert.match(mapTitle(),/放大 5×/);
  doc.querySelector('#grid-station-map [data-station-id="59287"]').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter'}));
  await waitFor(()=>doc.querySelector('#grid-station-map [data-station-id="59287"].selected')&&doc.querySelector('#grid-station-detail-body').textContent.includes('59287'),'keyboard station');
  assert.match(mapTitle(),/放大 5×/,'selecting a station keeps the zoom');
  assert(doc.getElementById('grid-station-reset').hidden===false,'reset appears for a single station');
  const lines=()=>doc.querySelectorAll('#grid-station-detail-body svg path[fill="none"]').length;
  assert.equal(lines(),models.length+1,'one line per model plus observations');
  doc.querySelector('#grid-station-detail-body [data-series]').click();
  assert.equal(lines(),models.length,'legend hides a model');
  doc.querySelector('#grid-station-detail-body [data-series]').click();assert.equal(lines(),models.length+1);
  // Run hour reaches both map and single-station series.
  const beforeHour=calls.length;
  doc.querySelector('#hour-switch [data-hour="08"]').click();
  await waitFor(()=>doc.querySelector('#grid-station-detail-body svg')&&calls.slice(beforeHour).some(u=>u.pathname.endsWith('/map')),'hour change');
  const hourCalls=calls.slice(beforeHour).filter(u=>u.pathname.endsWith('/map')||u.pathname.endsWith('/series'));
  assert(hourCalls.every(u=>u.searchParams.get('hour')==='08'),'map and series share the BJT run hour');
  assert.match(doc.getElementById('grid-station-caption').textContent,/08时起报/);
  // Single-station lead selector.
  const leadSelect=doc.getElementById('grid-station-lead');assert(leadSelect);
  leadSelect.value='48';leadSelect.dispatchEvent(new w.Event('change'));
  await waitFor(()=>w.GridVerification.selection().lead===48&&doc.getElementById('grid-station-detail-body')?.textContent.includes('+48小时'),'station lead');
  assert.equal(calls.filter(u=>u.pathname.endsWith('/series')).at(-1).searchParams.get('lead'),'48');
  // Reset back to the region.
  doc.getElementById('grid-station-reset').click();
  await waitFor(()=>!w.GridVerification.selection().scoreStation&&doc.getElementById('start').value===start,'region reset');
  await pause(80);
  // Custom date changes reload the station rows for the new range.
  const beforeDates=calls.length;
  const startInput=doc.getElementById('start');startInput.value=end;startInput.dispatchEvent(new w.Event('change'));
  await waitFor(()=>calls.slice(beforeDates).some(u=>u.pathname.endsWith('/grid-rows')&&u.searchParams.get('start')===end),'date reload');
  assert.equal(doc.getElementById('start').value,end);
  // Back to the grid reference: the map page leaves and the metric chart returns.
  refButton('格点分析场').click();
  await waitFor(()=>!w.GridVerification.selection().station&&!doc.getElementById('detail-results').hidden&&doc.querySelector('#chart svg'),'grid chart');
  assert.equal(w.location.hash.includes('ref=grid'),true);
  doc.getElementById('export-svg').click();
  doc.getElementById('export-csv').click();
  assert(downloads.some(name=>name.endsWith('.svg')));
  assert(downloads.some(name=>name.endsWith('.csv')));
  // Old ref switch still opens the station map.
  w.location.hash='#grid&ref=station';
  await waitFor(()=>w.GridVerification.selection().station&&doc.querySelector('#chart svg'),'old ref hash');
  const mapAgain=mapButton();assert(mapAgain,'station distribution entry exists');
  mapAgain.click();
  await waitFor(()=>doc.querySelector('#grid-station-map svg'),'map again');
  // Exports on the metric page.
  w.location.hash='#grid&ref=station';
  await waitFor(()=>w.GridVerification.selection().page==='map'&&doc.querySelector('#grid-station-map svg'),'settle');
  assert.deepEqual(errors,[]);
  console.log('PASS map page, model chips, zoom presets, station keyboard select, series legend, run hour, station lead, date reload, reference switches and exports');
 }finally{dom.window.close();}
}
module.exports=testGridStations;
if(require.main===module)testGridStations().catch(error=>{console.error(error);process.exitCode=1;});
