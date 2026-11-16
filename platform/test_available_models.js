'use strict';
/* Available-model defaults follow the current date/run-hour window, not a
   retired single-lead selector. Manual choices survive metric changes. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check){for(let i=0;i<400;i++){if(check())return;await pause(5);}throw Error('Available-model selection timed out');}
async function run(){
  const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{url:'http://localhost/Verification/test/#grid&ref=station',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,doc=w.document,errors=[];
  w.addEventListener('error',event=>errors.push(event.error));
  w.AbortSignal.timeout=()=>new w.AbortController().signal;
  w.DemoModels={ready:async()=>{},profiles:()=>[],profile:()=>null,groups:()=>({}),gridNotice(){}};
  w.eval(fs.readFileSync('core.js','utf8'));
  const {start,end}=w.VerificationCore.calendarWindow(3),valid=end+'T08:00:00';
  const pair=['ECMWF-IFS-9km-OpenMeteo','ECMWF-IFS'];
  const models=[...pair,'NCEP-GFS','ECAI','GFSAI','GEFS集合平均','Google WeatherNext 3（集合均值）'];
  const dataset={id:'station-气温-2m',kind:'气温',source:'station',var:'t',accumulation:null,level:null,
    models,supported_models:models,thresholds:[],leads:[24,48],times:[valid],start:start+'T08:00:00',end:valid,updated:valid,
    has_n:true,reference:'华南站点实况',region:'华南 108–120°E / 18–27°N'};
  const row=(model,lead,hour='08')=>({dataset:dataset.id,model,lead,valid,run:end+'T'+hour+':00:00',threshold:null,n:3,ME:1,MAE:1,RMSE:1,CC:.9});
  const rows=[...models.filter(model=>model!=='GEFS集合平均').map(model=>row(model,24)),
    row('ECMWF-IFS',48),row('GEFS集合平均',48,'20')];
  w.localStorage.setItem('szverify-workbench-v2',JSON.stringify({kind:'气温',datasets:{[dataset.id]:{lead:24,view:'multi',models:pair,knownModels:models}}}));
  const response=value=>({ok:true,json:async()=>structuredClone(value)});
  w.fetch=async path=>{
    const url=new URL(path,w.location.href);
    if(url.pathname.endsWith('/catalog'))return response({datasets:[{...dataset,id:'grid-t',source:'native'}],snapshot_id:'fixture',status:{accepted:1,unverified:0,unreadable:0}});
    if(url.pathname.endsWith('/grid-datasets'))return response([dataset]);
    if(url.pathname.endsWith('/grid-rows'))return response(rows);
    throw Error('Unexpected request '+url);
  };
  const chosen=()=>[...doc.querySelectorAll('#models [data-model][aria-pressed="true"]')].map(button=>button.dataset.model).sort();
  const change=(id,value)=>{doc.getElementById(id).value=value;doc.getElementById(id).dispatchEvent(new w.Event('change'));};
  try{
    w.eval(fs.readFileSync('app.js','utf8'));
    assert(await w.GridVerification.open());
    await until(()=>doc.querySelector('#chart svg'));
    // EC 0.25° is the same IFS run as EC 9 km: off by default while 9 km has rows.
    const expected=models.filter(model=>model!=='ECMWF-IFS').sort();
    assert.deepEqual(chosen(),expected,'old saved EC pair is replaced by every model with data in the window, EC 9 km instead of EC 0.25°');
    doc.querySelector('[data-model="NCEP-GFS"]').click();
    assert(!chosen().includes('NCEP-GFS'),'manual exclusions survive a metric change');
    doc.getElementById('models-default').click();
    assert.deepEqual(chosen(),expected,'default button selects available models again');
    // GEFS only has a 20 BJT run row in this fixture.
    change('hour','20');
    await until(()=>doc.querySelector('#chart svg'));
    assert.deepEqual(chosen(),['GEFS集合平均'],'default follows the selected run hour');
    change('hour','all');
    await until(()=>doc.querySelector('#chart svg'));
    assert.deepEqual(chosen(),expected,'all runs expose every available model');
    doc.querySelector('[data-model="GEFS集合平均"]').click();
    assert.deepEqual(chosen(),expected.filter(m=>m!=='GEFS集合平均'));
    assert(await w.GridVerification.refresh());
    await until(()=>doc.querySelector('#chart svg'));
    assert.deepEqual(chosen(),expected,'refresh defaults to every currently available model');
    assert.equal(errors.length,0,errors.map(String).join('\n'));
    console.log('PASS available model defaults, saved-pair replacement, run-hour filtering, manual choices and refresh');
  }finally{dom.window.close();}
}
run().catch(error=>{console.error(error);process.exitCode=1;});
