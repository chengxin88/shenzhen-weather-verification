'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check){for(let i=0;i<400;i++){if(check())return;await pause(5);}throw Error('Condition timed out');}
async function main(){
 const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{url:'http://localhost/Verification/test/',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,calls=[],errors=[];
 w.addEventListener('error',e=>errors.push(e.error));
 w.AbortSignal.timeout=()=>new w.AbortController().signal;
 for(const name of ['NativeTyphoon','NativeCase','MonthlyPrecip','DataQuality','StationVerification','ModelInterfaces'])w[name]={open(){},close(){},refresh(){}};
 const current='2026-11-09T08:00:00',start='2026-10-10T08:00:00';
 const common={source:'station',models:['ECMWF-IFS','NCEP-GFS'],supported_models:['ECMWF-IFS','NCEP-GFS'],
  reference:'华南站点实况',region:'华南',leads:[3,24,25,48,49,72],times:[current],start,end:current,updated:current,has_n:true};
 const datasets=[{...common,id:'station-降水-24',var:'rain24',kind:'降水',accumulation:24,thresholds:[.1,10,25,50,100,250],has_trace:true},
  {...common,id:'station-气温-2m',var:'t',kind:'气温',accumulation:null,thresholds:[],has_acc:true}];
 const grades=Array.from({length:4},(_,i)=>({id:i+1,label:['小雨','中雨','大雨','暴雨'][i],observed:i+1}));
 function overview(element){return {state:'ready',station_count:170,start:'2026-11-02T08:00:00',end:current,grades,rows:element==='rain'?
  [{model:'ecmwf',label:'EC',PC:.9,grades:[{grade:1,TS:.5},{grade:2,TS:null},{grade:3,TS:null},{grade:4,TS:0}]},
   {model:'gfs',label:'GFS',PC:.9,grades:[{grade:1,TS:.4},{grade:2,TS:0},{grade:3,TS:null},{grade:4,TS:0}]}]:
  [{model:'ecmwf',label:'EC',ACC2:.9,ACC1:.4,MAE:2},{model:'gfs',label:'GFS',ACC2:.9,ACC1:.7,MAE:1},{model:'ec01',label:'EC 9 km',ACC2:.8,ACC1:.7,MAE:1}]};}
 let defer=null,fail=false,empty=false;
 w.fetch=async path=>{
  const url=new URL(path,w.location.href);calls.push(url);
  let data;
  if(url.pathname.endsWith('/overview')){
   if(defer){const resolve=defer;defer=null;return new Promise(r=>resolve(()=>r({ok:true,json:async()=>overview(url.searchParams.get('element'))})));}
   if(fail)return {ok:false,json:async()=>({error:'合成错误'})};
   data=empty?{rows:[],station_count:0}:overview(url.searchParams.get('element'));
  }else if(url.pathname.endsWith('wn3-module-demo/catalog'))data={schema:1,profiles:[],unavailable:{}};
  else if(url.pathname.endsWith('/catalog'))data={datasets:[{...datasets[0],source:'hk',id:'hk-降水-24'}],rows:[],snapshot_id:'fixture',status:{accepted:1,unverified:0,unreadable:0}};
  else if(url.pathname.endsWith('/grid-datasets'))data=datasets;
  else if(url.pathname.endsWith('/grid-rows')){
   const dataset=datasets.find(x=>x.id===url.searchParams.get('dataset'));
   data=dataset.leads.flatMap(lead=>dataset.models.flatMap(model=>dataset.var==='t'?
    [{model,lead,dataset:dataset.id,run:current,valid:current,n:10,N1:5,N2:9,ME:1,MAE:1,RMSE:1,CC:.8}]:
    dataset.thresholds.map(threshold=>({model,lead,dataset:dataset.id,run:current,valid:current,n:10,H:5,M:1,F:1,T:0,threshold}))));
  }else throw Error('Unexpected request '+url);
  return {ok:true,json:async()=>structuredClone(data)};
 };
 const latest=()=>calls.filter(u=>u.pathname.endsWith('/overview')).at(-1);
 const change=(key,value)=>{const el=d.getElementById('overview-'+key);el.value=value;el.dispatchEvent(new w.Event('change'));};
 const loaded=()=>d.getElementById('overview-workspace').getAttribute('aria-busy')==='false';
 try{
  for(const file of ['core.js','demo-models.js','app.js','overview.js','modules.js'])w.eval(fs.readFileSync(file,'utf8'));
  await until(loaded);
  assert.equal(d.getElementById('overview-workspace').hidden,false,'no hash defaults to overview');
  assert.equal(d.getElementById('grid-workspace').hidden,true);
  assert.equal(calls.length,1,'home does not load detailed catalogs');
  assert.equal(d.querySelector('[data-module]').dataset.module,'overview');
  assert.equal(d.querySelectorAll('#overview-workspace select').length,4);
  assert.deepEqual(Object.fromEntries(latest().searchParams),{region:'all',element:'rain',day:'1',days:'7'});
  assert.match(d.getElementById('overview-caption').textContent,/11-02 至 11-09，北京时.*第1天 24小时雨量.*170 个站/);
  assert.match(d.querySelector('#overview-result thead').textContent,/实况 1 站次/);
  assert.equal(d.querySelectorAll('[data-column="PC"] strong').length,0,'two equal values: no winner, none bold');
  assert.equal(d.querySelectorAll('[data-column="grade1"] strong').length,1);
  assert.equal(d.querySelector('[data-column="grade2"]').textContent,'—');
  assert.equal(d.querySelectorAll('[data-column="grade2"]')[1].textContent,'0.00');
  assert.equal(d.querySelectorAll('[data-column="grade4"] strong').length,0,'all-equal column has no winner');
  assert.equal(d.querySelectorAll('[data-column="grade3"] strong').length,0,'null never bold');
  assert(d.querySelector('.overview-scroll table'));
  for(const [key,value] of [['day','3'],['days','15'],['element','t']]){change(key,value);await until(loaded);assert.equal(latest().searchParams.get(key),value);}
  assert.deepEqual(Object.fromEntries(latest().searchParams),{region:'all',element:'t',day:'3',days:'15'});
  assert.equal(d.querySelectorAll('[data-column="ACC2"] strong').length,2);
  assert.equal(d.querySelectorAll('[data-column="ACC1"] strong').length,2);
  assert.equal(d.querySelectorAll('[data-column="MAE"] strong').length,2,'smallest error wins');
  assert.equal(d.querySelector('[data-column="MAE"]').querySelector('strong'),null);
  assert.equal(d.querySelectorAll('#overview-notes p').length,3);
  const link=d.getElementById('overview-detail');assert.match(link.hash,/#grid&ref=station&element=t&day=3&days=15/);
  link.click();
  await until(()=>!d.getElementById('grid-workspace').hidden&&w.GridVerification.selection().kind==='气温'&&d.querySelector('[data-days="15"]').classList.contains('chosen'));
  assert.equal(w.GridVerification.selection().reference,'station');
  assert(d.getElementById('verification-mode-label').hidden);
  assert.equal(d.getElementById('dataset').value,'station-气温-2m');
  d.querySelector('[data-module="overview"]').click();await until(loaded);
  change('element','rain');change('day','2');change('days','3');await until(loaded);
  d.getElementById('overview-detail').click();
  await until(()=>d.getElementById('dataset').value==='station-降水-24'&&w.GridVerification.selection().lead===48&&d.querySelector('[data-days="3"]').classList.contains('chosen'));
  assert.equal(w.GridVerification.selection().reference,'station');
  assert(d.getElementById('verification-mode-label').hidden);
  d.querySelector('[data-module="overview"]').click();await until(loaded);
  let release;defer=resolve=>{release=resolve;};change('element','t');await until(()=>release);
  change('element','rain');await until(loaded);release();await pause(20);
  assert.match(d.getElementById('overview-caption').textContent,/^降水/,'stale response ignored');
  empty=true;await w.AssessmentOverview.refresh();assert.match(d.getElementById('overview-result').textContent,/暂无有效配对/);
  empty=false;fail=true;await w.AssessmentOverview.refresh();assert.match(d.getElementById('overview-result').textContent,/合成错误/);
  fail=false;d.getElementById('refresh').click();await until(loaded);assert(d.querySelector('#overview-result table'));
  w.location.hash='#overview';await until(()=>!d.getElementById('overview-workspace').hidden);await until(loaded);
  assert.equal(errors.length,0,errors.map(String).join('\n'));
  console.log('PASS overview default route, controls, count display, best/tied values, links, races and empty/error states');
 }finally{dom.window.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
