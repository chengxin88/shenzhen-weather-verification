'use strict';
// Browser DOM checks against the real replay API; no synthetic scores or fields.
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const base=process.env.CASE_TEST_ORIGIN||'http://127.0.0.1:8543/Verification/test/';
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check){for(let i=0;i<240;i++){if(check())return;await pause(500);}throw Error('等待页面结果超时');}
async function main(){
 const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{url:base,runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,calls=[],errors=[],blobs=[];
 w.addEventListener('error',e=>errors.push(String(e.error)));
 w.AbortController=global.AbortController;w.Blob=global.Blob;
 w.URL.createObjectURL=b=>{blobs.push(b);return 'blob:case-test';};w.URL.revokeObjectURL=()=>{};
 w.HTMLAnchorElement.prototype.click=function(){};
 w.fetch=async(path,opts)=>{calls.push({path,body:opts?.body?JSON.parse(opts.body):null});return fetch(new URL(path,base),opts);};
 w.DemoModels={label:id=>id};
 for(const file of ['case_cache.js','case.js'])w.eval(fs.readFileSync(file,'utf8'));
 const click=(id,v)=>{const b=d.querySelector(`#${id} [data-value="${v}"]`);assert(b,`${id} ${v}`);b.click();};
 await w.NativeCase.open();await until(()=>d.querySelectorAll('.case-field-card').length===9);
 assert.equal(d.querySelector('#case-select').options.length,1);
 assert.equal(d.querySelector('#case-select').value,'2026-07-25');
 assert(!d.querySelector('.case-advanced').open);
 assert.equal(d.querySelectorAll('#case-valid button').length,4);
 const defaultThreshold=Number(d.querySelector('#case-chart-threshold [aria-pressed=true]').dataset.value);assert.equal(defaultThreshold,50);
 assert.equal(d.querySelectorAll('#case-summary-table tbody tr').length,8);
 assert.deepEqual([...d.querySelectorAll('#case-summary-table th')].map(x=>x.textContent),['模式','≥0.1 mm','≥10 mm','≥25 mm','≥50 mm','N','有效时次数']);
 const plot=()=>d.querySelector('#case-plot').innerHTML;
 const bars=plot();assert(bars.includes('0.2'));assert(bars.includes('0.8'));assert(!bars.includes('0.265'));
 assert(d.querySelector('.case-field-card svg path'));
 click('case-view','lead');assert(plot().includes('预报时效（小时）'));const original=JSON.parse(fs.readFileSync('evidence/case-page/score-after.json')).result;assert.equal(d.querySelectorAll('#case-plot circle').length,original.scores.filter(r=>r['阈值(mm)']===defaultThreshold&&Number.isFinite(r.TS)).length);
 for(const m of ['POD','FAR','BIAS']){
  click('case-metric',m);const job=JSON.parse(fs.readFileSync('evidence/case-page/score-after.json'));
  const rows=job.result.scores.filter(r=>r['阈值(mm)']===defaultThreshold),expected=rows.filter(r=>m==='FAR'?r.H+r.F>0:r.H+r.M>0).length;
  assert.equal(d.querySelectorAll('#case-plot circle').length,expected,`${m} 零分母点应断开`);
  assert(d.querySelector('#case-summary-note').textContent.includes(m));
  assert(/\d\.\d{3}/.test(d.querySelector('#case-summary-table tbody').textContent));
 }
 click('case-metric','TS');click('case-chart-threshold','0.1');
 assert(d.querySelector('.case-threshold-note').textContent.includes('没有区分度'));
 assert(d.querySelectorAll('.case-field-card')[1].textContent.includes('≥0.1 mm'));
 click('case-chart-threshold',defaultThreshold);click('case-view','models');assert.equal(plot(),bars);
 const stored=JSON.parse(w.sessionStorage.getItem('verification-case-views-v1'));
 assert(Object.keys(stored.entries[0][1].charts).some(k=>JSON.parse(k)[0]==='lead'));
 assert(Object.keys(stored.entries[0][1].charts).some(k=>JSON.parse(k)[0]==='models'));
 const previous=calls.length;
 click('case-metric','ETS');click('case-metric','TS');assert.equal(calls.length,previous,'指标和视图切换不应重取数据');
 d.querySelector('#case-summary-csv').click();const csv=await blobs.at(-1).text();assert.equal(csv.split('\r\n').length,33);for(const k of ['H','M','F','C','POD','FAR','BIAS','累计(h)'])assert(csv.split('\r\n')[0].includes('"'+k+'"'));
 const times=[...d.querySelectorAll('#case-valid button')];click('case-valid',times[1].dataset.value);
 await until(()=>d.querySelectorAll('.case-field-card').length===9);assert(d.querySelector('.case-field-card svg').getAttribute('aria-label').includes('20:00'));
 click('case-runs','2026072512');await until(()=>!d.querySelector('#case-results').hidden&&d.querySelectorAll('#case-summary-table tbody tr').length===16);
 await until(()=>d.querySelectorAll('.case-field-card').length===9);
 assert(d.querySelector('#case-field-caption').textContent.includes('只显示第一个'));
 assert(d.querySelector('#case-field-caption').textContent.includes('08时'));
 assert(d.querySelector('#case-summary-table tbody td').textContent.includes('08时'));
 click('case-runs','2026072512');await until(()=>!d.querySelector('#case-results').hidden&&d.querySelectorAll('#case-summary-table tbody tr').length===8);
 click('case-kinds','2米气温');await until(()=>!d.querySelector('#case-results').hidden&&d.querySelector('#case-summary-table'));
 assert(!d.querySelector('.case-fields'));
 assert(d.querySelector('#case-summary-note').textContent.includes('单位：°C'));
 const heads=[...d.querySelectorAll('#case-summary-table th')].map(x=>x.textContent);for(const x of ['要素','层次','单位'])assert(!heads.includes(x));
 click('case-view','lead');assert(plot().includes('预报时效（小时）'));
 assert.deepEqual(errors,[]);
 fs.writeFileSync('evidence/case-page/frontend-requests.json',JSON.stringify(calls.filter(c=>c.path.endsWith('/score')).map(c=>c.body),null,2));
 console.log('PASS 个例目录、九张落区、默认阈值、无区分度提示、矩阵/完整CSV、逐时次POD/FAR/BIAS、零分母断线、视图缓存、双起报、气温表头与类别隐藏');
 w.NativeCase.close();w.close();
}
main().catch(e=>{console.error(e);process.exitCode=1;});
