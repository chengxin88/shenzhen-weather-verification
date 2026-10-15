'use strict';
const assert=require('node:assert/strict'),C=require('./core.js');
const source='Google WeatherNext 3（集合均值）';
const profiles=[{id:'wn3-demo:sample',label:'模式样例',target_product_id:'sample'}];
const rows=[{dataset:'rain',model:source,lead:6,run:'2026-10-13T01:00:00',valid:'2026-10-13T07:00:00',n:1719,threshold:.1,H:0,M:0,F:0},
 {dataset:'rain',model:'ECMWF-IFS',lead:6,run:'2026-10-13T01:00:00',valid:'2026-10-13T07:00:00',n:1719,threshold:.1,H:1,M:2,F:3}];
const input={datasets:[{id:'rain',kind:'降水',models:[source,'ECMWF-IFS'],supported_models:[source,'ECMWF-IFS']},
 {id:'pressure',kind:'环流',models:['ECMWF-IFS'],supported_models:['ECMWF-IFS']}],rows};
const before=JSON.stringify(input),catalog=C.demoCatalog(input,profiles,source);
assert.equal(JSON.stringify(input),before,'source catalog stays unchanged');
assert.equal(catalog.rows.length,2,'selector registration never multiplies all rows');
assert.ok(catalog.datasets[0].models.includes(profiles[0].id));
assert.ok(!catalog.datasets[1].models.includes(profiles[0].id),'no invented high-altitude scores');
assert.match(catalog.datasets[1].model_issues[profiles[0].id],/WN3/);
assert.deepEqual(C.demoRows(rows,['ECMWF-IFS'],profiles,source),[rows[1]],'formal selection unchanged');
const demos=C.demoRows(rows,[profiles[0].id],profiles,source);
assert.equal(demos.length,1);assert.equal(demos[0].source_model,source);assert.equal(demos[0].is_demo,true);assert.equal(demos[0].excluded_from_official_scores,true);
for(const key of ['lead','run','valid','n','threshold','H','M','F'])assert.equal(demos[0][key],rows[0][key]);
assert.equal(C.score(demos).TS,null,'zero events stay undefined');
const combined=C.demoRows(rows,['ECMWF-IFS',profiles[0].id],profiles,source);
assert.equal(C.commonTimes(combined,['ECMWF-IFS',profiles[0].id]).length,2);
assert.match(C.csv(demos,[['is_demo','是否演示'],['source_model','来源'],['excluded_from_official_scores','排除排名']]),/true/);
console.log('Demo selectors, exact source reuse, no high-altitude fabrication, formal isolation and export flags: passed.');
