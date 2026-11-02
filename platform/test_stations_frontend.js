/* DOM integration tests; npm test dependency is optional, never used by the app.
 * NODE_PATH=/tmp/st-jsdom/node_modules node test_stations_frontend.js
 */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync('index.html','utf8');
const dom=new JSDOM(html,{url:'http://127.0.0.1:8524/Verification/test/#stations',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,doc=w.document,calls=[];let empty=false,deferTemperature=false,pendingTemperature=null;
const stationTable=JSON.parse(fs.readFileSync('stations.json','utf8')).stations.filter(s=>['59287','59493','ZGGG'].includes(s.id));
stationTable[0].model_elevations={ec01:stationTable[0].elevation+400};stationTable[0].elevation_flags=['ec01'];
const metadata={state:'ready',generated:'2026-10-25T12:00:00Z',window_end:'2026-10-25T12:00:00Z',stations:stationTable,stations_with_data:3,
 sources:{ogimet:{stations:2,latest:'2026-10-25T12:00:00Z'}},ogimet:{stopped:null},...JSON.parse(fs.readFileSync('station-basemap.json','utf8')),
 models:[{id:'ec01',label:'EC 0.1°',leads:[6,24,48]},{id:'ecmwf',label:'EC 0.25°',leads:[6,24,48]},{id:'gfs',label:'GFS',leads:[6,24,48]}],
 variables:[{id:'t',label:'2米气温',unit:'°C',thresholds:[]},{id:'rain6',label:'6小时降水',unit:'mm',thresholds:[.1,10]}]};
function response(value){return {ok:true,status:200,json:async()=>structuredClone(value)};}
function scores(url){
 const variable=url.searchParams.get('var'),rain=variable.startsWith('rain'),models=url.searchParams.get('models').split(',');
 const stat=rain?{n:2,H:1,M:0,F:1,C:0,TS:.5,POD:1,FAR:.5,ETS:0}:{n:2,ME:1.5,MAE:1.5,RMSE:1.581,CC:1};
 const [lo,hi]=(url.searchParams.get('range')||'0-168').split('-').map(Number);
 const rows=models.flatMap(model=>[6,24,48].filter(h=>h>=lo&&h<=hi).flatMap(lead=>(rain?[.1,10]:[null]).map(threshold=>({...stat,model,var:variable,lead,...(rain?{threshold}:{})}))));
 const station_rows=stationTable.filter(s=>!rain||s.type==='synop').flatMap(s=>models.flatMap(model=>(rain?[.1,10]:[null]).map(threshold=>({...stat,station:s.id,model,var:variable,lead_min:lo,lead_max:hi,...(rain?{threshold}:{})}))));
 return {rows,station_rows};
}
w.fetch=async(path,options={})=>{
 const url=new URL(path,w.location.href);calls.push({url,options});
 assert(url.pathname.includes('/api/stations/'),'station tab must not fetch grid/demo/provider APIs');
 assert(!options.body && (!options.method||options.method==='GET'),'UI must stay read-only');
 if(url.pathname.endsWith('/meta'))return response(empty?{state:'empty',notice:'站点资料尚未刷新',stations:[]}:metadata);
 if(url.pathname.endsWith('/scores')){
  if(deferTemperature && url.searchParams.get('var')==='t'){
   deferTemperature=false;
   return new Promise(resolve=>{pendingTemperature=()=>resolve(response(scores(url)));});
  }
  return response(scores(url));
 }
 const rain=url.searchParams.get('var').startsWith('rain');
 return response({station:stationTable.find(s=>s.id===url.searchParams.get('id')),
  stats:rain?[{n:2,H:1,M:0,F:1,TS:.5,POD:1,FAR:.5,ETS:0,threshold:.1},{n:2,H:1,M:0,F:1,TS:.5,POD:1,FAR:.5,ETS:0,threshold:10}]:[{n:2,ME:1.5,MAE:1.5,RMSE:1.581,CC:1}],
  series:[{valid:'2026-10-24T12:00:00Z',fc:21,ob:20,source:'ogimet'},{valid:'2026-10-25T00:00:00Z',fc:24,ob:22,source:'ogimet'}]});
};
for(const name of ['NativeCase','NativeTyphoon','MonthlyPrecip','DataQuality','ModelInterfaces'])w[name]={close(){},open(){},refresh(){}};
w.GridVerification={open(){return Promise.resolve(true);}};
w.eval(fs.readFileSync('demo-models.js','utf8'));
w.eval(fs.readFileSync('stations.js','utf8'));
w.eval(fs.readFileSync('modules.js','utf8'));
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitFor(condition){for(let i=0;i<200;i++){if(condition())return;await pause(5);}throw Error('DOM condition timed out');}
function change(id,value){const el=doc.getElementById(id);assert(el,id);el.value=value;el.dispatchEvent(new w.Event('change'));}
function latestScores(){return calls.filter(c=>c.url.pathname.endsWith('/scores')).at(-1).url;}
(async()=>{
 try{
  await waitFor(()=>doc.querySelector('#stations-detail-body svg'));
  assert.equal(doc.getElementById('stations-workspace').hidden,false);
  assert.equal(doc.getElementById('grid-workspace').hidden,true);
  assert.equal(latestScores().searchParams.get('models'),'ec01,ecmwf');
  assert.equal(doc.querySelectorAll('.stations-point').length,3);
  assert(doc.querySelector('.stations-coast'),'full coastline is rendered');
  assert(doc.querySelector('.stations-province'),'province boundaries are rendered');
  assert(doc.querySelector('.stations-border'),'Guangxi land border is rendered');
  assert(doc.querySelector('.stations-coast').parentElement.hasAttribute('clip-path'),'map lines stay inside the plotting rectangle');
  assert.match(doc.querySelector('.stations-map-key').textContent,/省界/);
  assert(doc.querySelector('.stations-point path'),'high elevation difference should use a triangle');
  assert.match(doc.querySelector('.stations-score-table').textContent,/1\.581/);
  doc.querySelector('[data-station-id="59287"]').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  await waitFor(()=>doc.getElementById('stations-id')?.value==='59287' && doc.querySelector('#stations-detail-body svg'));
  change('stations-map-model','ecmwf');
  await waitFor(()=>doc.getElementById('stations-detail-model')?.value==='ecmwf');
  change('stations-var','rain6');
  await waitFor(()=>doc.getElementById('stations-threshold') && doc.querySelector('#stations-detail-body svg'));
  assert.equal(doc.getElementById('stations-metric').value,'TS');
  change('stations-threshold','10');
  await waitFor(()=>doc.querySelector('#stations-detail-body svg'));
  assert.match(doc.querySelector('.stations-score-table').textContent,/0\.5/);
  doc.querySelector('[data-station-model="gfs"]').click();
  await waitFor(()=>latestScores().searchParams.get('models').includes('gfs')&&doc.querySelector('#stations-detail-body svg'));
  assert(doc.querySelector('[data-station-days="1"]').disabled,'windows missing from an older snapshot stay disabled');
  doc.querySelector('[data-station-days="7"]').click();
  await waitFor(()=>latestScores().searchParams.get('days')==='7'&&doc.querySelector('#stations-detail-body svg'));
  change('stations-range','25-72');
  await waitFor(()=>latestScores().searchParams.get('range')==='25-72'&&doc.querySelector('#stations-detail-body svg'));
  assert.match(doc.querySelector('.stations-score-table').textContent,/48 h/);
  assert.doesNotMatch(doc.querySelector('.stations-score-table').textContent,/6 h/);
  // A delayed response deliberately ignores AbortSignal: it must not replace
  // the stored rain table after the user has changed variables.
  deferTemperature=true;change('stations-var','t');
  await waitFor(()=>pendingTemperature);
  change('stations-var','rain6');
  await waitFor(()=>doc.querySelector('#stations-detail-body svg')&&doc.getElementById('stations-metric').value==='TS');
  pendingTemperature();await pause(20);
  change('stations-metric','POD');
  assert.match(doc.querySelector('.stations-score-table').textContent,/1/);
  assert(doc.querySelector('.stations-lead-card svg'),'late T response must not poison active rain chart');
  doc.getElementById('refresh').click();
  await waitFor(()=>doc.querySelector('#stations-detail-body svg'));
  assert(calls.every(c=>c.url.pathname.includes('/api/stations/')));
  empty=true;await w.StationVerification.refresh();
  assert.equal(doc.getElementById('stations-results').children.length,0);
  assert.equal(doc.getElementById('stations-detail').children.length,0);
  assert.match(doc.getElementById('stations-status').textContent,/尚未刷新/);
  await require('./test_grid_stations_frontend.js')();
  console.log('PASS standalone station tab, default EC comparison, map/keyboard/elevation, detail, rain thresholds, filters, late-response isolation, read-only refresh, empty state');
 }finally{w.StationVerification.close();dom.window.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
