/* Real app.js/core.js + station reference integration, with read-only fixture APIs. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitFor(predicate){for(let n=0;n<400;n++){if(predicate())return;await pause(5);}throw Error('Grid station DOM condition timed out');}
async function testGridStations(){
 const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{url:'http://127.0.0.1:8524/Verification/test/#grid',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,doc=w.document,calls=[],downloads=[],errors=[];
 w.addEventListener('error',event=>errors.push(event.error));
 w.AbortSignal.timeout=()=>new w.AbortController().signal;
 w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>{};
 w.HTMLAnchorElement.prototype.click=function(){downloads.push(this.download);};
 const models=['ECMWF-IFS-9km-OpenMeteo','ECMWF-IFS'];
 const sites=[{id:'59493',name:'深圳',type:'synop',lon:114,lat:22.5,elevation:60},{id:'59287',name:'广州',type:'synop',lon:113,lat:23,elevation:10}];
 w.DemoModels={ready:async()=>{},profiles:()=>[],profile:()=>null,groups:()=>({}),gridNotice(){}};
 w.eval(fs.readFileSync('core.js','utf8'));
 const {start,end}=w.VerificationCore.calendarWindow(3),times=[start+'T08:00:00',start+'T20:00:00',end+'T08:00:00',end+'T20:00:00'];
 const datasets=['t','rain6','rain24'].map(variable=>({id:variable==='t'?'station-气温-2m':'station-降水-'+variable.slice(4),kind:variable==='t'?'气温':'降水',source:'station',var:variable,
  accumulation:variable==='t'?null:Number(variable.slice(4)),has_n:true,models,supported_models:models,start:times[0],end:times.at(-1),updated:times.at(-1),leads:[0,24,25,30,48,72,96],
  thresholds:variable==='t'?[]:[.1,10],reference:'华南站点实况',region:'华南 108–120°E / 18–27°N',source_note:'站点实况：OGIMET SYNOP / NOAA GHCNh / IEM METAR'}));
 const gridDatasets=datasets.map(d=>({...d,id:'grid-'+d.var,source:'native',reference:'深圳开放数据格点分析场'}));
 w.localStorage.setItem('szverify-workbench-v2',JSON.stringify({datasets:{[datasets[0].id]:{lead:24,view:'multi'}}}));
 gridDatasets.push({...gridDatasets[1],id:'grid-rain12',accumulation:12});
 const rowsFor=d=>models.flatMap(model=>d.leads.flatMap(lead=>times.flatMap(valid=>(d.kind==='气温'?[null]:d.thresholds).map(threshold=>({dataset:d.id,model,lead,valid,run:valid,threshold,n:3,created:1,
  ...(d.kind==='气温'?{ME:1,MAE:2,RMSE:3,CC:.9}:{H:1,M:1,F:0})})))));
 const response=value=>({ok:true,json:async()=>structuredClone(value)});
 w.fetch=async(path,options={})=>{
  const url=new URL(path,w.location.href);calls.push(url);
  assert(!options.body&&(!options.method||options.method==='GET'));
  if(url.pathname.endsWith('/api/catalog'))return response({datasets:gridDatasets,snapshot_id:'fixture',status:{accepted:1,unverified:0,unreadable:0}});
  if(url.pathname.endsWith('/api/rows'))return response({dataset:url.searchParams.get('dataset'),snapshot_id:'fixture',rows:rowsFor(gridDatasets.find(d=>d.id===url.searchParams.get('dataset')))});
  if(url.pathname.endsWith('/grid-datasets'))return response(datasets);
  if(url.pathname.endsWith('/grid-rows')){
   const leads=url.searchParams.get('leads')?.split(',').map(Number);
   return response(rowsFor(datasets.find(d=>d.id===url.searchParams.get('dataset'))).filter(r=>(!leads||leads.includes(r.lead))&&r.valid.slice(0,10)>=url.searchParams.get('start')&&r.valid.slice(0,10)<=url.searchParams.get('end')));
  }
  if(url.pathname.endsWith('/meta'))return response({stations:sites,coastline:[],models:[{id:'ec01',leads:datasets[0].leads},{id:'ecmwf',leads:datasets[0].leads}],variables:[{id:'t',label:'2米气温',unit:'°C'},{id:'rain6',label:'6小时降水',unit:'mm'},{id:'rain24',label:'24小时降水',unit:'mm'}]});
  if(url.pathname.endsWith('/map'))return response({rows:sites.map(site=>({station:site.id,n:3,ME:1,MAE:2,RMSE:3,CC:.9,TS:.5,POD:.5,FAR:0,ETS:.2})),sample:'地图按全部配对汇总；主图CC为平均空间相关。'});
  if(url.pathname.endsWith('/series'))return response({station:sites.find(site=>site.id===url.searchParams.get('station')),series:[{valid:start+'T00:00:00Z',fc:20,ob:19,source:'ogimet'},{valid:end+'T00:00:00Z',fc:23,ob:22,source:'ogimet'}]});
  throw Error('Unexpected API '+url);
 };
 const change=(id,value)=>{doc.getElementById(id).value=value;doc.getElementById(id).dispatchEvent(new w.Event('change'));};
 try{
  for(const file of ['app.js','stations.js','grid-stations.js'])w.eval(fs.readFileSync(file,'utf8'));
  assert(await w.GridVerification.open());
  assert(doc.querySelector('#chart svg'),'grid starts with regular chart');
  assert.equal(calls.filter(url=>url.pathname.includes('/api/stations/')).length,0,'station products load lazily');
  doc.querySelector('[data-kind="气温"]').click();await waitFor(()=>doc.getElementById('page-title').textContent==='气温检验'&&doc.querySelector('#chart svg'));
  doc.querySelector('[data-grid-reference="station"]').click();
  await waitFor(()=>doc.querySelector('#grid-station-detail-body svg'));
  assert.equal(doc.getElementById('grid-station-lead').value,'24','single-lead mode follows the main chart');
  assert.equal(doc.getElementById('hour-switch').closest('details'),null,'run-hour control is visible among primary filters');
  const lines=()=>doc.querySelectorAll('#grid-station-detail-body svg path[fill="none"]').length;
  assert.deepEqual(new Set(calls.filter(url=>url.pathname.endsWith('/series')).map(url=>url.searchParams.get('model'))),new Set(models),'one station series per selected model');
  assert.equal(doc.querySelectorAll('#grid-station-detail-body [data-series]').length,models.length+1);
  assert.equal(lines(),models.length+1,'a line per model plus observations');
  doc.querySelector('#grid-station-detail-body [data-series]').click();
  assert.equal(lines(),models.length,'legend hides a model');
  assert.equal(doc.querySelector('#grid-station-detail-body [data-series]').getAttribute('aria-pressed'),'false');
  doc.querySelector('#grid-station-detail-body [data-series]').click();assert.equal(lines(),models.length+1);
  const gapPath=w.StationVisuals.lineChart([{label:'a',color:'#000',rows:[0,12,36,48].map(h=>({x:h*3600000,y:h}))}],'t','x',true);
  assert.equal(gapPath.match(/d="([^"]*)" fill="none" stroke="#000"/)[1].split('M').length-1,2,'time series breaks at gaps over 12 h');
  assert.deepEqual([...doc.getElementById('grid-station-model').options].map(option=>option.value),models);
  change('grid-station-model',models[1]);
  await waitFor(()=>calls.some(url=>url.pathname.endsWith('/map')&&url.searchParams.get('model')===models[1]));
  const mapLabel=doc.getElementById('grid-station-model').selectedOptions[0].textContent;
  await waitFor(()=>doc.getElementById('grid-station-caption').textContent.startsWith(mapLabel+' ·'));
  assert(doc.querySelector('#chart svg'),'temperature must use normal grid chart');
  assert.match(doc.getElementById('chart-title').textContent,/站点实况/);
  assert.match(doc.getElementById('results-table').textContent,/RMSE/);
  assert.equal(doc.getElementById('stations-controls'),null,'no embedded standalone controls');
  assert.equal(doc.querySelector('#grid-station-view .stations-lead-card'),null);
  assert.equal(doc.getElementById('start').min,start);assert.equal(doc.getElementById('end').max,end);
  assert.equal(w.location.hash,'#grid&ref=station');
  doc.querySelector('[data-view="time"]').click();await waitFor(()=>doc.querySelector('#chart svg'));
  doc.querySelector('[data-view="lead"]').click();await waitFor(()=>doc.querySelector('#chart svg'));
  doc.querySelector('[data-view="multi"]').click();await waitFor(()=>doc.querySelector('#chart svg'));
  doc.querySelector('[data-lead-mode="group"]').click();
  await waitFor(()=>calls.some(url=>url.pathname.endsWith('/map')&&url.searchParams.get('leads')==='0,24'));
  doc.querySelector('[data-lead-group="short"]').click();
  await waitFor(()=>calls.some(url=>url.pathname.endsWith('/map')&&url.searchParams.get('leads')==='25,30,48,72'));
  await waitFor(()=>doc.getElementById('grid-station-lead').value==='48');
  assert.deepEqual([...doc.querySelectorAll('#grid-station-lead option')].map(o=>Number(o.value)),[0,24,25,30,48,72,96],'detail lead list is not limited to the main lead group');
  assert.match(doc.getElementById('results-table').textContent,/24–72小时/);
  await waitFor(()=>doc.getElementById('grid-station-detail-body').textContent.includes('+48小时'));
  change('grid-station-lead','30');
  await waitFor(()=>doc.getElementById('grid-station-detail-body').textContent.includes('+30小时'));
  const manualLeadSelect=doc.getElementById('grid-station-lead');
  change('metric','MAE');
  await waitFor(()=>doc.getElementById('grid-station-lead')!==manualLeadSelect&&doc.getElementById('grid-station-lead')?.value==='30'&&doc.getElementById('grid-station-detail-body')?.textContent.includes('+30小时'));
  for(const hour of ['08','20','all']){
   const before=calls.length;
   doc.querySelector(`#hour-switch [data-value="${hour}"]`).click();
   await waitFor(()=>doc.querySelector('#grid-station-detail-body svg'));
   const requests=calls.slice(before).filter(url=>url.pathname.endsWith('/map')||url.pathname.endsWith('/series'));
   assert(requests.some(url=>url.pathname.endsWith('/map')),'run-hour change reloads map scores');
   assert.equal(requests.filter(url=>url.pathname.endsWith('/series')).length,models.length,'all selected model series reload');
   assert(requests.every(url=>url.searchParams.get('hour')===hour),'map and series share the selected BJT run hour');
   assert.equal(doc.getElementById('grid-station-lead').value,'30','run-hour change retains the manually selected detail lead');
   assert.match(doc.getElementById('grid-station-caption').textContent,hour==='all'?/全部起报时次/:new RegExp(hour+'时起报'));
   assert.match(doc.getElementById('grid-station-detail-body').textContent,hour==='all'?/全部起报时次/:new RegExp(hour+'时起报'));
   assert.equal(w.GridVerification.selection().hour,hour);
  }
  doc.querySelector('[data-lead-group="near"]').click();
  await waitFor(()=>doc.getElementById('grid-station-lead')?.value==='24');
  doc.querySelector('[data-lead-group="short"]').click();
  await waitFor(()=>doc.getElementById('grid-station-lead')?.value==='48');
  doc.querySelector('[data-lead-mode="single"]').click();change('lead','25');
  await waitFor(()=>doc.getElementById('grid-station-lead')?.value==='25');
  doc.querySelector('[data-lead-mode="group"]').click();
  await waitFor(()=>doc.getElementById('grid-station-lead')?.value==='48');
  change('start','1900-01-01');assert.equal(doc.getElementById('start').value,start);
  change('end','2099-01-01');assert.equal(doc.getElementById('end').value,end);
  doc.querySelector('[data-grid-reference="grid"]').click();await waitFor(()=>!w.GridStations.active()&&doc.querySelector('#chart svg'));
  assert(doc.querySelector('#chart svg'));assert(doc.getElementById('grid-station-view').hidden);
  doc.querySelector('[data-grid-reference="station"]').click();await waitFor(()=>w.GridStations.active()&&doc.querySelector('#chart svg'));
  const stationCalls=calls.filter(url=>url.pathname.endsWith('/grid-rows'));
  assert(stationCalls.every(url=>url.searchParams.get('dataset')===datasets[0].id),'temperature never downloads unrelated rainfall');
  assert(stationCalls.some(url=>url.searchParams.get('leads')==='24'),'single lead downloads only requested scores');
  assert.equal(new Set(stationCalls.map(String)).size,stationCalls.length,'same station selection is downloaded once');
  doc.querySelector('[data-kind="降水"]').click();await waitFor(()=>doc.getElementById('page-title').textContent==='降水检验'&&doc.querySelector('#chart svg'));
  assert.deepEqual([...doc.querySelectorAll('#accumulation-switch button')].map(button=>button.textContent),['6 小时累计','24 小时累计']);
  doc.querySelector('[data-lead-mode="single"]').click();
  for(const hours of [6,24]){
   [...doc.querySelectorAll('#accumulation-switch button')].find(button=>button.textContent.startsWith(hours+' ')).click();
   await waitFor(()=>doc.querySelector('#chart svg'));
   for(const view of ['multi','single','lead','matrix','time','performance']){
    doc.querySelector(`[data-view="${view}"]`).click();await waitFor(()=>doc.querySelector('#chart svg'));
    assert.match(doc.getElementById('chart-title').textContent,/站点实况/);
   }
  }
  doc.querySelector('[data-view="info"]').click();assert.match(doc.getElementById('chart').textContent,/OGIMET/);
  assert.doesNotMatch(doc.getElementById('chart').textContent,/格点分析场/);
  doc.querySelector('[data-view="lead"]').click();change('threshold','10');
  await waitFor(()=>calls.some(url=>url.pathname.endsWith('/map')&&url.searchParams.get('threshold')==='10'));
  await waitFor(()=>doc.querySelector('#grid-station-map [data-station-id="59287"]'));
  doc.querySelector('#grid-station-map [data-station-id="59287"]').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter'}));
  await waitFor(()=>calls.some(url=>url.pathname.endsWith('/series')&&url.searchParams.get('station')==='59287'));
  doc.getElementById('export-svg').click();doc.getElementById('export-csv').click();
  assert(downloads.some(name=>name.includes('站点实况')&&name.endsWith('.svg')));
  assert(downloads.some(name=>name.includes('站点实况')&&name.endsWith('.csv')));
  w.location.hash='#grid';await waitFor(()=>!w.GridStations.active()&&doc.querySelector('#chart svg'));
  w.location.hash='#grid&ref=station';await waitFor(()=>w.GridStations.active()&&doc.querySelector('#chart svg'));assert(doc.querySelector('#chart svg'));
  doc.querySelector('[data-days="1"]').click();
  await waitFor(()=>doc.querySelector('#chart svg')&&calls.some(url=>url.pathname.endsWith('/grid-rows')&&url.searchParams.get('start')===end&&url.searchParams.get('end')===end));
  assert.equal(doc.getElementById('start').value,end,'date switch fetches only the chosen station period');
  doc.querySelector('[data-days="3"]').click();
  await waitFor(()=>doc.querySelector('#chart svg')&&doc.getElementById('start').value===start);
  assert.equal(errors.length,0,errors.map(String).join('\n'));
  console.log('PASS shared grid temperature/rain 6h/24h charts, every view, lazy loading, BJT custom bounds, lead groups, reference/hash switches, map keyboard click, station series, SVG/CSV exports');
 }finally{dom.window.close();}
}
module.exports=testGridStations;
if(require.main===module)testGridStations().catch(error=>{console.error(error);process.exitCode=1;});
