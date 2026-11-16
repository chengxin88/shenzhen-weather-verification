'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check){for(let i=0;i<200;i++){if(check())return;await pause(5);}throw Error('Selection did not finish');}
async function test(){
  const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{url:'http://localhost/Verification/test/#grid',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,doc=w.document,calls=[],errors=[];
  w.AbortSignal.timeout=()=>new w.AbortController().signal;
  w.DemoModels={ready:async()=>{},profiles:()=>[],profile:()=>null,groups:()=>({}),gridNotice(){}};
  w.addEventListener('error',e=>errors.push(e.error));
  w.eval(fs.readFileSync('core.js','utf8'));
  const day=w.VerificationCore.calendarWindow(1).end,valid=day+'T08:00:00';
  const common={source:'hk',models:['ECMWF-IFS'],supported_models:['ECMWF-IFS'],reference:'深圳开放数据格点分析场',region:'深圳市行政区域',
    leads:[24],times:[valid],start:valid,end:valid,updated:valid,level:null,has_n:true};
  const datasets=[{...common,id:'rain',kind:'降水',accumulation:3,thresholds:[.1]},
    {...common,id:'temperature',kind:'气温',accumulation:null,thresholds:[]}];
  const rows=dataset=>[{dataset,model:'ECMWF-IFS',run:valid,valid,lead:24,threshold:dataset==='rain'?.1:null,n:10,created:1,
    ...(dataset==='rain'?{H:2,M:1,F:1}:{ME:1,MAE:1,RMSE:1,CC:.9})}];
  let releaseRain;
  const rainGate=new Promise(resolve=>releaseRain=resolve);
  const response=value=>({ok:true,json:async()=>value});
  w.fetch=async path=>{
    const url=new URL(path,w.location.href);calls.push(url);
    if(url.pathname.endsWith('/catalog'))return response({datasets,snapshot_id:'fixture',status:{accepted:1,unverified:0,unreadable:0}});
    if(url.pathname.endsWith('/rows')){
      const dataset=url.searchParams.get('dataset');
      if(dataset==='rain')await rainGate;
      return response({dataset,snapshot_id:'fixture',rows:rows(dataset)});
    }
    throw Error('Unexpected request '+url);
  };
  try{
    w.eval(fs.readFileSync('app.js','utf8'));
    const opening=w.GridVerification.open();
    await until(()=>calls.some(u=>u.searchParams.get('dataset')==='rain'));
    assert(!doc.querySelector('#chart svg'));
    doc.querySelector('[data-kind="气温"]').click();
    await until(()=>doc.querySelector('#chart svg')&&doc.getElementById('page-title').textContent.startsWith('气温'));
    assert.equal(doc.getElementById('grid-workspace').getAttribute('aria-busy'),'false');
    assert.equal(doc.getElementById('export-csv').disabled,false);
    doc.querySelector('[data-kind="降水"]').click();
    await until(()=>doc.getElementById('page-title').textContent.startsWith('降水'));
    doc.querySelector('[data-kind="气温"]').click();
    await until(()=>doc.querySelector('#chart svg')&&doc.getElementById('page-title').textContent.startsWith('气温'));
    releaseRain();await opening;await pause(20);
    assert(doc.getElementById('page-title').textContent.startsWith('气温'),'late rainfall response must not overwrite temperature');
    assert(doc.querySelector('#chart svg'));
    assert.equal(calls.filter(u=>u.searchParams.get('dataset')==='temperature').length,1,'temperature cache is reused');
    assert.equal(calls.filter(u=>u.searchParams.get('dataset')==='rain').length,1,'pending rainfall request is shared');
    assert.equal(errors.length,0);
    console.log('PASS temperature opens before initial rainfall finishes; latest selection wins; cached and pending requests reused');
  }finally{releaseRain();dom.window.close();}
}
test().catch(e=>{console.error(e);process.exitCode=1;});
