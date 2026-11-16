'use strict';
const assert=require('node:assert/strict'),C=require('./core.js');
const WN3='Google WeatherNext 3（集合均值）';
const base={kind:'降水',accumulation:3,level:null,reference:'深圳开放数据格点分析场',region:'深圳市行政区域',
  models:['EC','GFS'],supported_models:['EC','GFS',WN3,'other'],thresholds:[.1,10],leads:[6,12,24],
  times:['2026-10-01T14:00:00','2026-10-02T08:00:00'],start:'2026-10-01T14:00:00',end:'2026-10-02T08:00:00',
  updated:'2026-10-02T08:00:00',has_n:true,score_has_n:true,score_metrics:[],row_count:0};
const datasets=[{...base,id:'native',source:'hk',models:['EC',WN3]},
  {...base,id:'weekly',source:'weekly'},{...base,id:'lead',source:'lead',models:['EC','GFS','other']},
  {...base,id:'surface',kind:'地面',element:'mslp',source:'analysis',score_metrics:['RMSE'],models:['EC']},
  {...base,id:'hidden',reference:'GDAS 分析场',source:'analysis'},
  {...base,id:'pilot',source:'lead',pilot:true}];
const row=(dataset,model,lead=6,created=1)=>({dataset,model,lead,created,run:'2026-10-01T08:00:00',
  valid:lead===6?'2026-10-01T14:00:00':'2026-10-02T08:00:00',threshold:.1,n:100,H:3,M:4,F:5});
const rows=[row('native','EC'),row('native',WN3),row('weekly','EC'),row('weekly','GFS'),
  row('lead','EC',6,5),row('lead','GFS',6,5),row('lead','other',6,5),row('lead','other',12,5),
  row('weekly','GFS',24,1),row('lead','GFS',24,2),row('surface','EC'),row('hidden','EC'),row('pilot','EC')];
// The full builder appends each source channel in dataset-registration order.
rows.sort((a,b)=>datasets.findIndex(d=>d.id===a.dataset)-datasets.findIndex(d=>d.id===b.dataset));
const input={datasets,rows,snapshot_id:'generation-1'};
const full=C.unifiedCatalog(input),metadata={datasets,snapshot_id:input.snapshot_id};
const slim=C.unifiedCatalog(metadata),canonical=items=>items.map(r=>JSON.stringify(r)).sort();

async function test(){
  assert.equal(Object.hasOwn(slim,'rows'),false,'metadata preparation does not invent empty score arrays');
  assert.deepEqual(slim.datasets.map(d=>d.id),full.datasets.map(d=>d.id),'stable dataset identities, aliases and visibility');
  const rain=slim.datasets.find(d=>d.kind==='降水'),surface=slim.datasets.find(d=>d.kind==='地面');
  assert.equal(rain.has_n,true,'ETS remains available before loading scores');
  assert.deepEqual(surface.score_metrics,['RMSE'],'circulation/surface availability needs no rows');
  const calls=[];
  const request=async path=>{
    const id=new URLSearchParams(path.split('?')[1]).get('dataset');calls.push(id);
    return {dataset:id,rows:rows.filter(r=>r.dataset===id),snapshot_id:input.snapshot_id};
  };
  const loader=C.createRowsLoader(metadata,request);
  assert.equal(calls.length,0,'no eager download');
  const first=loader.load(rain),second=loader.load(rain);
  assert.equal(first,second,'concurrent selections share a pending request');
  const loaded=await first;
  assert.deepEqual(calls.sort(),['lead','native','weekly'],'only the selected scientific group is fetched');
  const expectedRows=full.rows.filter(r=>r.dataset===rain.id);
  assert.deepEqual(canonical(loaded.rows),canonical(expectedRows),'native precedence and complete legacy cohorts are unchanged');
  assert.deepEqual(loaded.rows,expectedRows,'original source and row order preserve exact floating-point reduction order');
  assert.deepEqual(loaded.datasets.find(d=>d.id===rain.id),full.datasets.find(d=>d.id===rain.id),'loaded selector metadata is identical');
  assert.ok(!loaded.rows.some(r=>r.model==='other'&&r.lead===6),'weekly wins as an entire cohort');
  assert.ok(loaded.rows.some(r=>r.model==='other'&&r.lead===12),'independent legacy cohorts survive');
  const models=['EC','GFS'];
  assert.deepEqual(C.grouped(C.commonTimes(loaded.rows,models),['model','lead','threshold']),
    C.grouped(C.commonTimes(expectedRows,models),['model','lead','threshold']),'TS/ETS/common samples remain identical');
  await loader.load(rain);assert.equal(calls.length,3,'per-dataset response cache is reused');
  await loader.load(surface);assert.equal(calls.length,4,'switching datasets loads only the new source');
  let attempts=0;
  const retry=C.createRowsLoader(metadata,async path=>{if(++attempts===1)throw Error('offline');return request(path);});
  await assert.rejects(retry.load(surface),/offline/);
  await retry.load(surface);assert.equal(attempts,2,'failed requests are evicted so retry works');
  const stale=C.createRowsLoader(metadata,async()=>({dataset:'surface',rows:[],snapshot_id:'generation-2'}));
  await assert.rejects(stale.load(surface),/刷新资料/,'mixed snapshot generations require a refresh');
  console.log('Slim metadata, lazy rows, shared request cache, cohort equivalence, TS/ETS, retries and generation isolation: passed.');
}
test().catch(error=>{console.error(error);process.exitCode=1;});
