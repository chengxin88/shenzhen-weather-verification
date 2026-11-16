'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check){for(let i=0;i<400;i++){if(check())return;await pause(5);}throw Error('Condition timed out');}

const qpe={
 version:1,generated:'2026-11-10T10:17:10+08:00',clock:'整点小时',
 truth:'深圳格点 1 小时雨量（自动站插值）',radar:'华南雷达拼图组合反射率（第三方存档）',region:'深圳市',
 period:{start:'2025-04-01',end:'2026-04-24',events:16,independent_events:3,development_events:13},
 thresholds:[0.1,5,10,20,50],
 methods:[
  {id:'ZR200',label:'固定公式 Z=200R^1.6',show:true,fitted_on_development:false,counts:{
   '0.1':{H:8,M:2,F:2,CN:4},'5':{H:3,M:1,F:0},'10':{H:1,M:0,F:0},'20':{H:1,M:1,F:0},'50':{H:0,M:5,F:0}}},
  {id:'local_ZR',label:'本地拟合公式',show:true,fitted_on_development:true,counts:{
   '0.1':{H:5,M:1,F:2},'5':{H:2,M:2,F:0},'20':{H:2,M:1,F:1},'50':{H:0,M:3,F:0}}},
  {id:'ZR300',label:'固定公式 Z=300R^1.4',show:true,fitted_on_development:false,counts:{
   '0.1':{H:5,M:2,F:3},'5':{H:2,M:1,F:3},'20':{H:1,M:3,F:0},'50':{H:0,M:2,F:0}}},
  {id:'LUT',label:'不应出现',show:false,counts:{'0.1':{H:99,M:0,F:0}}}
 ]};

function qpf(days){
 const start=days===3?'2026-11-07T07:00:00':'2026-10-26T07:00:00';
 const observed=days===3?{'0.1':99,'5':null,'10':null,'20':null,'50':null}
  :{'0.1':12345,'5':null,'10':678,'20':9,'50':0};
 return {days:days===3?3:15,start,end:'2026-11-10T07:00:00',thresholds:[0.1,5,10,20,50],hours:[1,2,3,4,5,6],observed,
  methods:[
   {id:'ec9km',label:'EC 9 km',delay_hours:8,leads:{
    '1':{hours:700,scores:{'0.1':{H:70,M:20,F:10},'5':{H:5,M:5,F:0},'10':null,'20':null,'50':null}},
    '2':{hours:600,scores:{'0.1':{H:40,M:20,F:20},'5':{H:1,M:1,F:0},'10':null,'20':null,'50':null}},
    '3':{hours:500,scores:{'0.1':{H:10,M:10,F:10},'5':null,'10':{H:1,M:0,F:0},'20':null,'50':null}},
    '4':{hours:400,scores:{'0.1':{H:10,M:10,F:10},'5':{H:1,M:1,F:0},'10':null,'20':null,'50':null}},
    '5':{hours:0,scores:{'0.1':null,'5':null,'10':null,'20':null,'50':null}},
    '6':{hours:100,scores:{'0.1':{H:1,M:1,F:1},'5':null,'10':null,'20':null,'50':null}}}},
   {id:'wn3',label:'WN3（集合平均）',delay_hours:8,leads:{
    '1':{hours:500,scores:{'0.1':{H:50,M:20,F:20},'5':{H:1,M:1,F:0},'10':null,'20':null,'50':null}},
    '2':{hours:400,scores:{'0.1':{H:30,M:10,F:20},'5':{H:1,M:1,F:0},'10':null,'20':null,'50':null}},
    '3':{hours:300,scores:{'0.1':{H:5,M:5,F:5},'5':null,'10':null,'20':null,'50':null}},
    '4':{hours:200,scores:{'0.1':{H:5,M:5,F:5},'5':{H:1,M:1,F:0},'10':null,'20':null,'50':null}},
    '5':{hours:100,scores:{'0.1':null,'5':null,'10':null,'20':null,'50':null}},
    '6':{hours:50,scores:{'0.1':{H:1,M:1,F:1},'5':null,'10':null,'20':{H:1,M:1,F:0},'50':null}}}},
   {id:'gfs',label:'GFS',delay_hours:8,leads:{
    '1':{hours:300,scores:{'0.1':{H:20,M:30,F:20},'5':{H:1,M:3,F:0},'10':null,'20':null,'50':null}},
    '2':{hours:300,scores:{'0.1':{H:10,M:10,F:10},'5':{H:1,M:3,F:0},'10':null,'20':null,'50':null}},
    '3':{hours:300,scores:{'0.1':{H:5,M:5,F:5},'5':null,'10':null,'20':null,'50':null}},
    '4':{hours:300,scores:{'0.1':{H:5,M:5,F:5},'5':{H:1,M:3,F:0},'10':null,'20':null,'50':null}},
    '5':{hours:0,scores:{'0.1':null,'5':null,'10':null,'20':null,'50':null}},
    '6':{hours:50,scores:{'0.1':{H:1,M:1,F:1},'5':null,'10':null,'20':null,'50':null}}}}
  ]};
}

async function main(){
 const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{url:'http://localhost/Verification/test/',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,calls=[],errors=[];
 w.addEventListener('error',e=>errors.push(e.error));
 w.AbortSignal.timeout=()=>new w.AbortController().signal;
 for(const name of ['NativeTyphoon','NativeCase','MonthlyPrecip','DataQuality','StationVerification','ModelInterfaces','AssessmentOverview'])w[name]={open(){},close(){},refresh(){}};
 let qpeAvailable=true;
 w.fetch=async path=>{
  const url=new URL(path,w.location.href);calls.push(url);
  let data;
  if(url.pathname.endsWith('/api/nowcast/qpe'))data=qpeAvailable?{available:true,data:structuredClone(qpe)}:{available:false};
  else if(url.pathname.endsWith('/api/nowcast/qpf'))data=qpf(Number(url.searchParams.get('days')));
  else throw Error('Unexpected request '+url);
  return {ok:true,json:async()=>structuredClone(data)};
 };
 const qpeCalls=()=>calls.filter(u=>u.pathname.endsWith('/api/nowcast/qpe'));
 const qpfCalls=()=>calls.filter(u=>u.pathname.endsWith('/api/nowcast/qpf'));
 const change=(id,value)=>{const el=d.getElementById(id);el.value=value;el.dispatchEvent(new w.Event('change'));};
 const loaded=()=>d.getElementById('nowcast-workspace').getAttribute('aria-busy')==='false';
 const cell=(lead,column)=>[...d.querySelectorAll(`#nowcast-qpf-result tbody td[data-lead="${lead}"][data-column="${column}"]`)];
 const qpeCell=(column,row)=>d.querySelectorAll(`#nowcast-qpe-result tbody [data-column="${column}"]`)[row];
 try{
  for(const file of ['core.js','demo-models.js','app.js','nowcast.js','modules.js'])w.eval(fs.readFileSync(file,'utf8'));
  await until(()=>d.querySelector('[data-module="nowcast"]'));
  assert.match(d.querySelector('[data-module="nowcast"]').textContent,/临近降水/);
  assert.equal(d.getElementById('overview-workspace').hidden,false,'no hash still opens the overview');
  assert.equal(d.getElementById('nowcast-workspace').hidden,true);

  d.querySelector('[data-module="nowcast"]').click();
  await until(()=>!d.getElementById('nowcast-workspace').hidden&&loaded()&&d.querySelector('#nowcast-qpf-result table'));
  assert.equal(w.location.hash,'#nowcast');
  assert.equal(d.getElementById('overview-workspace').hidden,true);

  // Exactly two controls and the intended defaults.
  assert.equal(d.querySelectorAll('#nowcast-workspace .filter-card select').length,1);
  assert.equal(d.querySelectorAll('#nowcast-workspace .table-heading select').length,1);
  assert.equal(d.getElementById('nowcast-metric').value,'ts');
  assert.equal(d.getElementById('nowcast-days').value,'15');
  assert.equal(d.querySelectorAll('#nowcast-workspace select').length,2);
  assert.equal(qpeCalls().length,1);
  assert.equal(qpfCalls().length,1);
  assert.equal(qpfCalls()[0].searchParams.get('days'),'15');

  // Radar table: caption, hidden method, real counts and TS scores.
  assert.match(d.getElementById('nowcast-qpe-caption').textContent,/历史回算 · 2025-04-01 至 2026-04-24 的 16 个降水过程 · 整点小时雨量 · 实况：深圳格点 1 小时雨量（自动站插值） · 雷达：华南雷达拼图组合反射率（第三方存档）/);
  assert.equal(d.querySelectorAll('#nowcast-qpe-result tbody tr').length,3,'show=false is not drawn');
  assert.equal(d.querySelectorAll('#nowcast-qpe-result tbody [data-column="0.1"]').length,3);
  assert.equal(qpeCell('0.1',0).textContent,'0.67');
  assert.equal(qpeCell('0.1',1).textContent,'0.63');
  assert.equal(d.querySelectorAll('#nowcast-qpe-result tbody [data-column="0.1"] strong').length,1,'largest TS wins');
  assert.match(d.querySelector('#nowcast-qpe-result thead').textContent,/实况 10 格点次/);
  assert.match(d.querySelector('#nowcast-qpe-result thead').textContent,/实况 4 格点次/);
  assert.equal(d.querySelectorAll('#nowcast-qpe-notes p').length,3);
  assert.match(d.getElementById('nowcast-qpe-notes').textContent,/其中 13 个过程拟合.*独立检验只有 3 个/);

  // Threshold 10 has one valid method: no bold even though it is the maximum.
  assert.equal(qpeCell('10',0).textContent,'1.00');
  assert.equal(d.querySelectorAll('#nowcast-qpe-result tbody [data-column="10"] strong').length,0);
  // Equal values are all bold.
  assert.equal(d.querySelectorAll('#nowcast-qpe-result tbody [data-column="20"] strong').length,2);
  // All-equal column is never bold.
  assert.equal(d.querySelectorAll('#nowcast-qpe-result tbody [data-column="50"] strong').length,0);
  assert.equal(qpeCell('50',0).textContent,'0.00');

  // QPF table: rowspan, observed counts and the 15-day window.
  assert.match(d.getElementById('nowcast-qpf-caption').textContent,/^近15天（10-26 至 11-10，北京时）· 实况：深圳格点 1 小时雨量 · 模式按当时实际能拿到的最新一次起报计（出数约晚 8 小时）/);
  assert.equal(d.querySelectorAll('#nowcast-qpf-result tbody tr').length,18);
  assert.equal(d.querySelectorAll('#nowcast-qpf-result th[rowspan="6"]').length,3);
  assert.match(d.querySelector('#nowcast-qpf-result thead').textContent,/实况 12,345 格点次/);
  assert.match(d.querySelector('#nowcast-qpf-result thead').textContent,/实况 — 格点次/);
  assert.match(d.querySelector('#nowcast-qpf-result thead').textContent,/实况 0 格点次/);
  assert.equal(cell(1,'0.1')[0].textContent,'0.70');
  assert.equal(cell(1,'0.1')[1].textContent,'0.56');
  assert.equal(cell(1,'0.1').filter(td=>td.querySelector('strong')).length,1,'best of the two methods at hour 1');
  assert.equal(cell(1,'5').filter(td=>td.querySelector('strong')).length,2,'equal scores are both bold');
  assert.equal(cell(2,'0.1').filter(td=>td.querySelector('strong')).length,2);
  assert.equal(cell(3,'10').filter(td=>td.querySelector('strong')).length,0,'one valid value is not bold');
  assert.equal(cell(6,'20').filter(td=>td.querySelector('strong')).length,0);
  assert.equal(cell(5,'0.1')[0].textContent,'—');
  assert.equal(cell(5,'0.1').filter(td=>td.querySelector('strong')).length,0);
  assert.equal(d.querySelectorAll('#nowcast-qpf-notes p').length,3);

  // Metric switch repaints both tables without another request.
  let count=calls.length;
  change('nowcast-metric','hit');
  await until(()=>qpeCell('0.1',0).textContent==='80.0%');
  assert.equal(calls.length,count,'metric switch must not fetch');
  assert.equal(qpeCell('0.1',0).textContent,'80.0%');
  assert.equal(qpeCell('0.1',1).textContent,'83.3%');
  assert.equal(d.querySelectorAll('#nowcast-qpe-result tbody [data-column="0.1"] strong').length,1);
  assert.equal(qpeCell('0.1',0).querySelector('strong'),null,'smallest FAR does not win the hit rate');
  assert.equal(qpeCell('0.1',1).querySelector('strong').textContent,'83.3%');
  assert.equal(cell(1,'0.1')[0].textContent,'77.8%');
  assert.equal(cell(1,'0.1')[0].querySelector('strong').textContent,'77.8%');

  change('nowcast-metric','far');
  await until(()=>qpeCell('0.1',0).textContent==='20.0%');
  assert.equal(calls.length,count);
  assert.equal(qpeCell('0.1',1).textContent,'28.6%');
  assert.equal(qpeCell('0.1',0).querySelector('strong').textContent,'20.0%','smallest FAR wins');
  assert.equal(d.querySelectorAll('#nowcast-qpe-result tbody [data-column="5"] strong').length,2,'both zero FAR are tied');
  assert.equal(d.querySelectorAll('#nowcast-qpe-result tbody [data-column="50"] strong').length,0,'dash never participates');
  assert.equal(qpeCell('50',0).textContent,'—');

  // The date control only reloads the short-range table.
  const qpeBefore=qpeCalls().length;
  change('nowcast-days','3');
  await until(()=>qpfCalls().length===2&&d.getElementById('nowcast-qpf-caption').textContent.startsWith('近3天'));
  assert.equal(qpeCalls().length,qpeBefore,'date switch must not reload radar scores');
  assert.equal(qpfCalls()[1].searchParams.get('days'),'3');
  assert.match(d.getElementById('nowcast-qpf-caption').textContent,/^近3天（11-07 至 11-10，北京时）/);
  assert.match(d.querySelector('#nowcast-qpf-result thead').textContent,/实况 99 格点次/);
  assert.equal(cell(1,'0.1')[0].textContent,'12.5%','metric is kept while the date changes');

  // Missing radar file: one sentence, the short-range table keeps working.
  qpeAvailable=false;
  await w.NowcastRain.refresh();
  await until(loaded);
  assert.equal(d.getElementById('nowcast-qpe-result').textContent.trim(),'雷达估测评分暂未生成');
  assert.equal(d.getElementById('nowcast-qpe-caption').textContent,'');
  assert.equal(d.querySelectorAll('#nowcast-qpe-notes p').length,0);
  assert(d.querySelector('#nowcast-qpf-result table'),'QPF table still renders');
  assert.equal(qpfCalls().at(-1).searchParams.get('days'),'3');

  const text=d.getElementById('nowcast-workspace').textContent;
  assert(!/POD|FAR|CSI|QPE|QPF/.test(text),'no English score abbreviations on the page');
  assert.equal(errors.length,0,errors.map(String).join('\n'));
  console.log('PASS nowcast route, two controls, QPE/QPF tables, metric and date switches, bold rules, missing radar file');
 }finally{dom.window.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
