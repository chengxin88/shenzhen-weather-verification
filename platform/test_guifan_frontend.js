'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom'),C=require('./core.js');
// Counts are pooled; undefined historical counts never enter the denominator.
const temp=[{model:'A',lead:24,valid:'a',n:1,N1:1,N2:1,RMSE:1},
 {model:'A',lead:24,valid:'b',n:9,N1:0,N2:0,RMSE:2},
 {model:'A',lead:24,valid:'old',n:100,RMSE:3}];
assert.equal(C.score(temp).ACC2,.1);assert.equal(C.score(temp).ACC2_n,10);
assert.equal(C.score(temp).RMSE,Math.sqrt(14/3));
assert.equal(C.score([temp[2]]).ACC2,null);
assert.equal(C.commonTimes([...temp,{model:'B',lead:24,valid:'a',n:10},{model:'B',lead:24,valid:'b',n:1,N1:1,N2:1}],['A','B'],{metric:'ACC2'}).length,2);
assert(!C.metricsFor({kind:'气温',source:'lead'}).includes('ACC2'));
assert(!C.metricsFor({kind:'降水',source:'weekly',has_n:true}).includes('PC'));
assert.deepEqual(C.metricsFor({kind:'降水',grades:[{id:1}]},'grade'),['TS','ETS','POD','FAR','MR','BIAS']);
assert.equal(C.score([{H:1,M:0,F:1,n:2,T:1,threshold:.1}]).PC,1);
assert(Math.abs(C.score([{H:2,M:1,F:1,n:10,grade:1}]).ETS-1.1/3.1)<1e-12);
assert.equal(C.grouped([{model:'A',grade:1,H:1,M:0,F:0,n:1},{model:'A',grade:2,H:0,M:1,F:0,n:1}],['model']).length,2);
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check){for(let i=0;i<300;i++){if(check())return;await pause(5);}throw Error('Condition timed out');}
async function stationUI(){
 const dom=new JSDOM('<div id="stations-workspace"></div>',{url:'http://localhost/Verification/test/#stations',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,errors=[];w.addEventListener('error',e=>errors.push(e.error));
 w.eval(fs.readFileSync('core.js','utf8'));
 const grades=Array.from({length:6},(_,i)=>({id:i+1,label:['小雨','中雨','大雨','暴雨','大暴雨','特大暴雨'][i],range:'范围'}));
 const site={id:'59493',name:'深圳',lon:114,lat:22.5,type:'synop',elevation:10},valid='2026-10-25T00:00:00Z';
 const meta={stations:[site],sources:{},models:[{id:'ec01',label:'EC',leads:[24]},{id:'gfs',label:'GFS',leads:[24]}],
  variables:[{id:'t',label:'2米气温',unit:'℃',has_acc:true,thresholds:[]},{id:'rain24',label:'24小时降水',unit:'mm',thresholds:[.1,10,250],grades,has_trace:true},
  {id:'rain6',label:'6小时降水',unit:'mm',thresholds:[.1,10],has_trace:true}],generated:valid,window_end:valid,ogimet:{},windows:[30]};
 const scored=variable=>['ec01','gfs'].flatMap(model=>{
  const base={station:site.id,model,var:variable,lead:24,valid,n:10};
  if(variable==='t')return [{...base,N1:2,N2:7,ACC1:.2,ACC2:.7,ME:1,MAE:1,RMSE:1,CC:.8}];
  return [.1,10,250].map(threshold=>({...base,threshold,H:3,M:1,F:1,T:threshold===.1?1:undefined,TS:.6,POD:.75,FAR:.25,MR:.25,BIAS:1,ETS:.5,PC:threshold===.1?.9:undefined})).concat(variable==='rain24'?grades.map(g=>({...base,grade:g.id,threshold:null,H:1,M:1,F:1,TS:1/3,POD:.5,FAR:.5,MR:.5,BIAS:1})):[]);
 });
 w.fetch=async path=>{const u=new URL(path,w.location.href),v=u.searchParams.get('var')||'t',rows=scored(v);return {ok:true,json:async()=>u.pathname.endsWith('/meta')?meta:u.pathname.endsWith('/scores')?{rows,station_rows:rows}:u.pathname.endsWith('/station-leads')?{leads:[24]}:{station:site,stats:rows.filter(r=>r.model===u.searchParams.get('model')),series:[{valid,fc:1,ob:0,source:'ogimet'}]}};};
 const change=(id,value)=>{d.getElementById(id).value=value;d.getElementById(id).dispatchEvent(new w.Event('change'));};
 try{
  w.eval(fs.readFileSync('stations.js','utf8'));w.StationVerification.open();
  await until(()=>d.getElementById('stations-metric'));
  change('stations-metric','ACC2');await until(()=>d.getElementById('stations-results').textContent.includes('70.0%'));
  change('stations-var','rain24');await until(()=>d.getElementById('stations-threshold'));
  assert(!d.getElementById('stations-verification-mode'));
  assert(!d.getElementById('stations-controls').textContent.includes('检验方式')); 
  change('stations-metric','PC');assert(d.getElementById('stations-threshold').disabled);
  await until(()=>d.getElementById('stations-results').textContent.includes('90.0%'));
  assert.equal(d.getElementById('stations-threshold').options.length,3);
  await until(()=>d.getElementById('stations-detail-body')?.querySelector('.stations-stats'));
  change('stations-var','rain6');assert(!d.getElementById('stations-verification-mode'));
  await until(()=>d.getElementById('stations-results').querySelector('svg'));
  assert.equal(errors.length,0,errors.map(String).join('\n'));
 }finally{dom.window.close();}
}

async function grid(){
 const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{url:'http://localhost/Verification/test/#grid',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,errors=[];
 w.AbortSignal.timeout=()=>new w.AbortController().signal;
 w.DemoModels={ready:async()=>{},profiles:()=>[],profile:()=>null,groups:()=>({}),gridNotice(){}};
 w.addEventListener('error',e=>errors.push(e.error));w.eval(fs.readFileSync('core.js','utf8'));
 const day=C.calendarWindow(1).end,valid=day+'T08:00:00',old=C.calendarWindow(2).start+'T08:00:00';
 const common={source:'hk',models:['ECMWF-IFS'],supported_models:['ECMWF-IFS'],reference:'深圳开放数据格点分析场',region:'深圳市行政区域',
  leads:[24],times:[old,valid],start:old,end:valid,updated:valid,level:null,has_n:true};
 const grades=Array.from({length:6},(_,i)=>({id:i+1,label:['小雨','中雨','大雨','暴雨','大暴雨','特大暴雨'][i],range:'范围'}));
 const datasets=[{...common,id:'hk-降水-3',kind:'降水',accumulation:3,thresholds:[.1,3,70],has_trace:true,grades},
  {...common,id:'hk-气温-2m',kind:'气温',accumulation:null,thresholds:[],has_acc:true},
  {...common,id:'lead-降水-6',source:'lead',kind:'降水',accumulation:6,thresholds:[.1,10]}];
 const base={model:'ECMWF-IFS',run:valid,valid,lead:24,n:10,created:1};
 const rows=id=>id==='hk-气温-2m'?[{...base,dataset:id,threshold:null,ME:1,MAE:1,RMSE:1,CC:.8,N1:2,N2:7},
  {...base,dataset:id,valid:old,n:100,threshold:null,ME:2,MAE:2,RMSE:2,CC:.5}]:[
  ...datasets.find(x=>x.id===id).thresholds.map(t=>({...base,dataset:id,threshold:t,H:2,M:1,F:1,...(id.startsWith('hk')&&t===.1?{T:1}:{})})),
  ...(id.startsWith('hk')?grades.map(g=>({...base,dataset:id,grade:g.id,threshold:null,H:g.id,M:1,F:2})):[])];
 w.fetch=async path=>{const u=new URL(path,w.location.href);return {ok:true,json:async()=>u.pathname.endsWith('/catalog')?
  {datasets,snapshot_id:'fixture',status:{accepted:1,unverified:0,unreadable:0}}:
  {dataset:u.searchParams.get('dataset'),snapshot_id:'fixture',rows:rows(u.searchParams.get('dataset'))}};};
 w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>{};w.HTMLAnchorElement.prototype.click=function(){};
 const change=(id,value)=>{d.getElementById(id).value=value;d.getElementById(id).dispatchEvent(new w.Event('change'));};
 const metric=name=>[...d.querySelectorAll('#detail-nav [data-metric]')].find(b=>b.textContent===name).click();
 try{
  w.eval(fs.readFileSync('app.js','utf8'));await w.GridVerification.open();
  await until(()=>d.querySelector('#chart svg'),'rain chart');
  assert.equal(d.getElementById('page-title').textContent,'降水 · 晴雨准确率','PC is the first rain metric when trace is available');
  assert.equal(d.getElementById('threshold-field').hidden,true,'PC fixes ≥0.1 mm and hides the threshold control');
  assert.equal(d.getElementById('threshold').value,'0.1');
  assert.match(d.getElementById('results-table').textContent,/90\.0%/);
  assert.equal(d.querySelectorAll('[data-view], #metric-switch, #lead-switch').length,0,'old tabs and metric/lead button rows are gone');
  assert(d.getElementById('verification-mode-label').hidden);
  metric('TS 评分');await until(()=>d.getElementById('page-title').textContent==='降水 · TS 评分'&&d.querySelector('#chart svg'),'TS');
  assert.equal(d.getElementById('threshold-field').hidden,false);
  assert.equal(d.getElementById('threshold').options.length,3);
  metric('评分综合图');await until(()=>d.querySelectorAll('.performance-panel').length===3,'performance');
  assert(!d.getElementById('results-table').textContent.includes('量级'));
  // Switching element never leaks the rain PC threshold into temperature.
  [...d.querySelectorAll('#detail-nav [data-kind]')].find(b=>b.textContent.includes('气温')).click();
  await until(()=>d.getElementById('page-title').textContent.startsWith('气温')&&d.querySelector('#chart svg'),'temperature');
  [...d.querySelectorAll('#detail-nav [data-metric]')].find(b=>b.textContent==='≤2℃准确率').click();
  await until(()=>d.getElementById('page-title').textContent.includes('≤2℃准确率')&&d.querySelector('#chart svg'),'ACC2');
  assert.match(d.getElementById('results-table').textContent,/70\.0%/);
  assert.equal(d.getElementById('threshold-field').hidden,true);
  assert.equal(errors.length,0,errors.map(String).join('\n'));
  // Map ramp and pooled region score use the same station-time counters.
  w.eval(fs.readFileSync('stations.js','utf8'));
  const V=w.StationVisuals;
  const map=V.map({stations:[{id:'a',lon:114,lat:22.5,name:'站点',type:'synop'}],variables:[{id:'t',label:'2米气温',unit:'℃'}]},
    {model:'ec01',metric:'ACC2',var:'t'},[{station:'a',n:10,N1:2,N2:7,ACC2:.7}],'模式');
  assert.match(map,/越深越好/);assert.match(map,/70\.0%/);
  change('start',old.slice(0,10));change('end',old.slice(0,10));
  await until(()=>d.getElementById('chart').textContent.includes('没有可比较'),'empty range');
 }finally{dom.window.close();}
}
stationUI().then(grid).then(()=>console.log('PASS guifan pooling, capabilities, cumulative/PC controls, performance and mixed coverage')).catch(e=>{console.error(e);process.exitCode=1;});
