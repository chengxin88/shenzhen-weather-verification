'use strict';
/* New detail-analysis contract: element → metric pages, lead chart, same-data
   table/CSV, station/grid switching, old hashes and the mobile menu. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check,label){for(let i=0;i<800;i++){if(check())return;await pause(5);}throw Error('Timed out: '+(label||''));}
async function run(){
 const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{url:'http://localhost/Verification/test/#grid',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,calls=[],downloads=[],blobs=[],errors=[];
 w.addEventListener('error',e=>errors.push(e.error));
 w.AbortSignal.timeout=()=>new w.AbortController().signal;
 w.URL.createObjectURL=blob=>{blobs.push(blob);return 'blob:test';};w.URL.revokeObjectURL=()=>{};
 w.HTMLAnchorElement.prototype.click=function(){downloads.push(this.download);};
 w.DemoModels={ready:async()=>{},profiles:()=>[],profile:()=>null,groups:()=>({}),gridNotice(){}};
 w.eval(fs.readFileSync('core.js','utf8'));
 const C=w.VerificationCore;
 const models=['ECMWF-IFS-9km-OpenMeteo','NCEP-GFS','GEFS集合平均'];
 const {start,end}=C.calendarWindow(3),dates=[start,end];
 const times=[start+'T08:00:00',start+'T20:00:00',end+'T08:00:00',end+'T20:00:00'];
 const common={has_n:true,models,supported_models:models,leads:[24,48,72],start:times[0],end:times.at(-1),updated:times.at(-1),reference:'华南格点分析场',region:'华南 108–120°E / 18–27°N'};
 const grid=[
  {...common,id:'grid-rain3',source:'hk',kind:'降水',accumulation:3,thresholds:[.1,10],has_trace:true},
  {...common,id:'grid-temp',source:'hk',kind:'气温',accumulation:null,thresholds:[],has_acc:true}
 ];
 const station=[
  {...common,id:'station-rain3',source:'station',kind:'降水',var:'rain3',accumulation:3,thresholds:[.1,10],has_trace:true,leads:[24,48,72],reference:'华南站点实况',region:'华南 108–120°E / 18–27°N'},
  {...common,id:'station-temp',source:'station',kind:'气温',var:'t',accumulation:null,thresholds:[],has_acc:true,leads:[24,48,72],reference:'华南站点实况',region:'华南 108–120°E / 18–27°N'}
 ];
 const runOf=valid=>valid; // tests use run=valid; hour switching still works
 // TS: EC 0.50/0.40/0.30 at 24/48/72; GFS 0.30/0.40/missing.
 const rainScores={24:[20,4,6],48:[16,6,6],72:[12,8,8]};
 const rainGfs={24:[12,8,8],48:[16,6,6],72:[0,0,0]};
 const rainGefs={24:[8,8,8],48:[8,8,8],72:[8,8,8]};
 function eventRow(dataset,model,lead,valid,threshold,H,M,F,extra={}){
  return {dataset,model,lead,valid,run:runOf(valid),threshold,n:H+M+F+20,created:1,H,M,F,...extra};
 }
 const rowsFor=ds=>{
  if(ds.kind==='降水'){
   const out=[];
   for(const lead of [24,48,72]) for(const valid of times) for(const threshold of ds.thresholds){
    const [h,m,f]=threshold===.1?rainScores[lead]:[4,2,2];
    out.push(eventRow(ds.id,models[0],lead,valid,threshold,h,m,f,threshold===.1?{T:1}:{}));
    if(rainGfs[lead])out.push(eventRow(ds.id,models[1],lead,valid,threshold,...rainGfs[lead],threshold===.1?{T:0}:{}));
    out.push(eventRow(ds.id,models[2],lead,valid,threshold,...rainGefs[lead],threshold===.1?{T:0}:{}));
   }
   return out;
  }
  return [24,48,72].flatMap(lead=>times.map(valid=>{
   const acc=lead===24?70:lead===48?80:90;
   return [{dataset:ds.id,model:models[0],lead,valid,run:runOf(valid),threshold:null,n:10,N1:5,N2:acc/10,ME:lead===24?1:2,MAE:lead===24?1:2,RMSE:lead===24?1:2,CC:.8},
     {dataset:ds.id,model:models[1],lead,valid,run:runOf(valid),threshold:null,n:10,N1:4,N2:acc/10-1,ME:0,MAE:1,RMSE:1.5,CC:.7},
     {dataset:ds.id,model:models[2],lead,valid,run:runOf(valid),threshold:null,n:10,N1:2,N2:acc/10-2,ME:2,MAE:3,RMSE:3,CC:.5}];
  })).flat();
 };
 const sites=[{id:'59493',name:'深圳',type:'synop',lon:114,lat:22.5,elevation:60},{id:'59287',name:'广州',type:'synop',lon:113,lat:23,elevation:10}];
 const response=value=>({ok:true,json:async()=>structuredClone(value)});
 w.fetch=async path=>{
  const u=new URL(path,w.location.href);calls.push(u);
  if(u.pathname.endsWith('/api/catalog'))return response({datasets:grid,snapshot_id:'fixture',status:{accepted:1,unverified:0,unreadable:0}});
  if(u.pathname.endsWith('/api/rows')){const ds=grid.find(x=>x.id===u.searchParams.get('dataset'));return response({dataset:ds.id,snapshot_id:'fixture',rows:rowsFor(ds)});}
  if(u.pathname.endsWith('/grid-datasets'))return response(station);
  if(u.pathname.endsWith('/grid-rows')){const ds=station.find(x=>x.id===u.searchParams.get('dataset'));return response(rowsFor(ds));}
  if(u.pathname.endsWith('/meta'))return response({stations:sites,coastline:[],models:[{id:'ec01',leads:[24,48,72]},{id:'gfs',leads:[24,48,72]}],variables:station.map(x=>({id:x.var,label:x.var,unit:x.kind==='气温'?'°C':'mm'}))});
  if(u.pathname.endsWith('/map'))return response({rows:sites.map(site=>({station:site.id,n:10,ME:1,MAE:1,RMSE:1,CC:.8,TS:.5,POD:.6,FAR:.2,H:2,M:1,F:1})),sample:'地图按全部配对汇总。'});
  if(u.pathname.endsWith('/series'))return response({station:sites.find(x=>x.id===u.searchParams.get('station')),series:times.map(valid=>({valid:valid+'Z',fc:3,ob:2,source:'ogimet'}))});
  throw Error('Unexpected API '+u);
 };
 const change=(id,value)=>{d.getElementById(id).value=value;d.getElementById(id).dispatchEvent(new w.Event('change'));};
 const clickMetric=async name=>{const b=[...d.querySelectorAll('#detail-nav [data-metric]')].find(x=>x.textContent===name);assert(b,'metric '+name);b.click();await until(()=>d.getElementById('page-title').textContent.includes(name)&&d.querySelector('#chart svg'),name);};
 const clickGroup=async name=>{const b=[...d.querySelectorAll('#detail-nav [data-kind]')].find(x=>x.textContent.includes(name));assert(b,'group '+name);b.click();await until(()=>d.getElementById('page-title').textContent.startsWith(name),name);};
 try{
  for(const file of ['app.js','stations.js','grid-stations.js'])w.eval(fs.readFileSync(file,'utf8'));
  assert(await w.GridVerification.open());
  await until(()=>d.querySelector('#chart svg'),'first chart');
  // 1. Element → metric menu, one page per metric.
  assert.equal(d.getElementById('page-title').textContent,'降水 · 晴雨准确率');
  assert([...d.querySelectorAll('#detail-nav [data-metric]')].some(b=>b.textContent==='晴雨准确率'));
  assert([...d.querySelectorAll('#detail-nav [data-metric]')].some(b=>b.textContent==='评分综合图'));
  assert(![...d.querySelectorAll('#detail-nav [data-metric]')].some(b=>b.textContent==='命中率'),'POD is not a separate page');
  await clickMetric('TS 评分');
  // 2. Lead chart and the valid-time second row.
  assert.equal(d.querySelectorAll('#chart svg polyline').length,3,'one line per selected model');
  // 悬停在某个时效上：一次列出该时效所有模式的评分。
  {const svg=d.querySelector('#chart svg');svg.dispatchEvent(new w.MouseEvent('pointermove',{bubbles:true,clientX:5,clientY:5}));
   const tip=d.getElementById('chart-value-tooltip');assert.equal(tip.hidden,false,'group tooltip shown');
   assert.equal(tip.querySelectorAll('.tip-row').length,3,'one row per model at the hovered lead');
   assert.match(tip.querySelector('strong').textContent,/^\+\d+h/);
   assert.equal(svg.querySelectorAll('.point.is-near').length,3);
   svg.dispatchEvent(new w.MouseEvent('pointerleave'));assert.equal(tip.hidden,true,'tooltip hidden on leave');}
  const axisText=()=>[...d.querySelectorAll('#chart svg text')].map(t=>t.textContent).join('|');
  assert.match(axisText(),/\b24\b/);assert.match(axisText(),/\b72\b/);
  const hourButtons=[...d.querySelectorAll('#hour-switch button')];
  assert.deepEqual(hourButtons.map(b=>b.textContent),['全部','08时','20时']);
  hourButtons.find(b=>b.textContent==='08时').click();
  await until(()=>d.querySelectorAll('#chart svg text').length&&[...d.querySelectorAll('#chart svg text')].some(t=>/08\(\+1\)/.test(t.textContent)),'valid hour row');
  const secondRow=[...d.querySelectorAll('#chart svg text')].map(t=>t.textContent).filter(t=>/^\d\d(\(\+\d\))?$/.test(t));
  assert(secondRow.length>=2,'valid-time labels exist under the lead axis');
  // 3. Table = same data, best values bold, missing cells use a dash.
  const table=d.getElementById('results-table');
  const headers=[...table.querySelectorAll('thead th')].map(th=>th.textContent);
  assert.deepEqual(headers.slice(1).map(x=>x.replace(/08\(\+\d\)/,'').trim()),['+24h','+48h','+72h']);
  const rows=[...table.querySelectorAll('tbody tr')];
  assert.equal(rows.length,3);
  const cells=row=>[...row.querySelectorAll('td')].map(td=>td.textContent);
  assert.equal(cells(rows[0])[0],'0.667');
  assert.equal(cells(rows[1])[0],'0.429');
  assert.equal(rows[0].querySelectorAll('td')[0].querySelector('strong')?.textContent,'0.667','larger TS wins');
  assert.equal(rows[1].querySelectorAll('td')[1].querySelector('strong')?.textContent,'0.571','shared best is bold for both');
  assert.equal(rows[0].querySelectorAll('td')[1].querySelector('strong')?.textContent,'0.571','tie is also bold');
  assert.equal(cells(rows[1])[2],'—','undefined score shows a dash');
  assert.doesNotMatch(table.textContent,/-999/);
  assert([...table.querySelectorAll('td')].every(td=>td.title.includes('样本')));
  // 4. Threshold pages and performance small multiples.
  await clickMetric('空报率');
  assert.equal(d.getElementById('threshold-field').hidden,false);
  assert([...d.querySelectorAll('#threshold-switch button')].some(b=>b.textContent==='≥10'));
  await clickMetric('评分综合图');
  
  assert.equal(d.querySelectorAll('.performance-panel').length,2,'one small chart per threshold');
  assert(d.querySelector('#chart svg'));
  const beforePerf=downloads.length;
  d.getElementById('export-svg').click();
  assert.equal(downloads.length,beforePerf+1,'small multiples export as one SVG');
  d.getElementById('export-csv').click();
  const perfCsv=await new Promise((resolve,reject)=>{const reader=new w.FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsText(blobs.at(-1));});
  assert.match(perfCsv,/命中率 POD/);
  // 5. Date mode: same page, fixed lead selector.
  await clickMetric('TS 评分');
  d.querySelector('[data-chart-mode="date"]').click();
  await until(()=>d.getElementById('date-lead-label').hidden===false&&d.querySelector('#chart svg'),'date mode');
  assert.match(d.getElementById('chart-title').textContent,/随日期/);
  change('date-lead','48');
  await until(()=>d.querySelector('#chart svg'));
  // 5b. Threshold mode: grouped bars per threshold at the fixed lead; the threshold picker gives way to the lead picker.
  const thresholdMode=d.querySelector('[data-chart-mode="threshold"]');
  assert.equal(thresholdMode.hidden,false,'threshold metrics offer the bar view');
  thresholdMode.click();
  await until(()=>d.getElementById('chart-title').textContent.includes('按阈值')&&d.querySelector('#chart svg rect.point'),'threshold bars');
  assert.match(d.getElementById('chart-title').textContent,/\+48h/);
  assert.equal(d.getElementById('threshold-field').hidden,true);
  assert.equal(d.getElementById('date-lead-label').hidden,false);
  assert.equal(new Set([...d.querySelectorAll('#chart svg rect.point')].map(r=>r.dataset.gx)).size,2,'one cluster per threshold');
  assert.deepEqual([...d.querySelectorAll('#results-table thead th')].slice(1).map(th=>th.textContent),['≥0.1 mm','≥10 mm']);
  assert.match(d.getElementById('table-title').textContent,/模式 × 阈值/);
  change('date-lead','24');
  await until(()=>d.getElementById('chart-title').textContent.includes('+24h'),'threshold bars follow the lead');
  assert.equal([...d.querySelectorAll('#results-table tbody tr')][0].querySelectorAll('td')[0].textContent,'0.667','same TS as the lead view');
  await clickMetric('晴雨准确率');
  assert.equal(thresholdMode.hidden,true,'single-threshold metrics hide the bar view');
  assert.doesNotMatch(d.getElementById('chart-title').textContent,/按阈值/);
  await clickMetric('TS 评分');
  // 6. Temperature accuracy page.
  await clickGroup('气温');
  await clickMetric('≤2℃准确率');
  assert.match(d.getElementById('results-table').textContent,/70\.0%/);
  // 7. Reference switch and 站点分布.
  const refButton=name=>[...d.querySelectorAll('#reference-switch button')].find(b=>b.textContent===name);
  assert(refButton('站点实况'),'reference switch is visible when both products exist');
  refButton('站点实况').click();
  await until(()=>w.GridVerification.selection().station&&d.querySelector('#chart svg'),'station reference');
  const mapButton=[...d.querySelectorAll('#detail-nav [data-page="map"]')][0];assert(mapButton);
  mapButton.click();
  await until(()=>d.querySelector('#grid-station-map svg')&&d.querySelector('#grid-station-detail-body svg'),'station map page');
  assert.equal(w.GridVerification.selection().page,'map');
  assert(d.getElementById('detail-results').hidden);
  assert(d.getElementById('grid-station-view').hidden===false);
  assert.equal(d.querySelectorAll('#grid-station-map [data-station-id]').length,2);
  assert.match(d.getElementById('grid-station-caption').textContent,/EC 9 km.*小时/);
  // Selecting a station keeps the map page and updates the single-station card.
  d.querySelector('#grid-station-map [data-station-id="59287"]').dispatchEvent(new w.MouseEvent('click',{bubbles:true}));
  await until(()=>d.getElementById('grid-station-detail-body')?.textContent.includes('59287'),'station click');
  // Lead control on the station page.
  assert(d.getElementById('grid-station-lead'));
  // 8. Back to grid reference and the metric page.
  refButton('格点分析场').click();
  await until(()=>!w.GridVerification.selection().station&&d.querySelector('#chart svg'),'grid reference');
  assert(d.getElementById('detail-results').hidden===false);
  // 9. Old hash compatibility.
  await clickGroup('降水');
  await clickMetric('TS 评分');
  w.location.hash='#grid&ref=grid&view=performance';
  await until(()=>d.getElementById('page-title').textContent.includes('评分综合图')&&d.querySelector('.performance-grid'),'old performance hash');
  w.location.hash='#grid&ref=grid&view=time';
  await until(()=>d.getElementById('chart-title').textContent.includes('随日期'),'old time hash');
  w.location.hash='#grid&ref=grid&view=info';
  await until(()=>d.getElementById('page-title').textContent==='说明'&&d.querySelector('.info-grid'),'old info hash');
  w.location.hash='#grid&ref=station';
  await until(()=>w.GridVerification.selection().station&&d.getElementById('page-title').textContent==='说明','old ref hash');
  // 10. Mobile menu exists and drives the same pages.
  const select=d.getElementById('detail-page-select');
  assert(select&&[...select.options].some(o=>o.value==='降水:TS'));
  select.value='降水:TS';select.dispatchEvent(new w.Event('change'));
  await until(()=>d.getElementById('page-title').textContent==='降水 · TS 评分'&&d.querySelector('#chart svg'),'mobile menu');
  d.querySelector('[data-chart-mode="lead"]').click();
  await until(()=>d.getElementById('chart-title').textContent.includes('随预报时效')&&d.querySelector('#chart svg'),'lead mode again');
  // 11. CSV equals the table (long format: one row per model × column).
  const before=downloads.length;
  d.getElementById('export-csv').click();
  assert.equal(downloads.length,before+1);
  const csv=await new Promise((resolve,reject)=>{const reader=new w.FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsText(blobs.at(-1));});
  const lines=csv.trim().split(/\r?\n/).slice(1);
  const csvHeaders=[...d.querySelectorAll('#results-table thead th')];
  assert.equal(lines.length,3*(csvHeaders.length-1),'one CSV row per model × lead');
  assert((lines[0].match(/"/g)||[]).length>=6);
  const csvNumbers=lines.flatMap(line=>(line.match(/-?\d+\.\d+/g)||[]).map(Number));
  const displayed=[...d.querySelectorAll('#results-table tbody tr')].flatMap(row=>[...row.querySelectorAll('td')]).map(td=>td.textContent).filter(v=>v!=='—').map(v=>parseFloat(v));
  assert(displayed.every(v=>csvNumbers.some(x=>Math.abs(x-v)<1e-3)),'every displayed table value is in the CSV');
  assert.equal(errors.length,0,errors.map(String).join('\n'));
  console.log('PASS detail metric menu, lead/second-row axis, bold/dash table, thresholds, performance, date mode, temperature, references, map page, old hashes, mobile select and CSV');
 }finally{dom.window.close();}
}
run().catch(e=>{console.error(e);process.exitCode=1;});
