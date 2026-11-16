// Run from the repository root with the loopback preview on port 8543.
const {spawn}=require('child_process'),fs=require('fs'),assert=require('node:assert/strict');
const proc=spawn(process.env.CASE_TEST_CHROME||'/usr/bin/google-chrome',['--headless=new','--no-sandbox','--no-zygote','--in-process-gpu','--disable-features=MojoIpcz','--no-proxy-server','--disable-gpu','--disable-dev-shm-usage','--hide-scrollbars','--remote-debugging-pipe','--user-data-dir=/tmp/case-page-chrome','--no-first-run','--disable-background-networking','about:blank'],{env:{...process.env,DBUS_SESSION_BUS_ADDRESS:'disabled:',DBUS_SYSTEM_BUS_ADDRESS:'disabled:',XDG_CONFIG_HOME:'/tmp/case-chrome-config',XDG_CACHE_HOME:'/tmp/case-chrome-cache',XDG_DATA_HOME:'/tmp/case-chrome-data',XDG_RUNTIME_DIR:'/tmp/case-xdg-runtime'},stdio:['ignore','ignore','ignore','pipe','pipe']});
let seq=0,buf='';const pending=new Map();proc.stdio[4].setEncoding('utf8');proc.stdio[4].on('data',chunk=>{buf+=chunk;let i;while((i=buf.indexOf('\0'))>=0){const raw=buf.slice(0,i);buf=buf.slice(i+1);if(!raw)continue;const msg=JSON.parse(raw);if(pending.has(msg.id)){const p=pending.get(msg.id);pending.delete(msg.id);msg.error?p.reject(Error(JSON.stringify(msg.error))):p.resolve(msg.result);}}});
const send=(method,params={},sessionId)=>{const id=++seq;proc.stdio[3].write(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})})+'\0');return new Promise((resolve,reject)=>pending.set(id,{resolve,reject}));};
proc.on('exit',code=>{for(const p of pending.values())p.reject(Error('Chrome退出：'+code));pending.clear();});
const wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const {targetInfos}=await send('Target.getTargets');const {sessionId:s}=await send('Target.attachToTarget',{targetId:targetInfos.find(t=>t.type==='page').targetId,flatten:true});
 await send('Page.enable',{},s);await send('Runtime.enable',{},s);await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1800,deviceScaleFactor:1,mobile:false},s);
 const evaljs=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true},s);if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
 await send('Page.navigate',{url:'http://127.0.0.1:8543/Verification/test/#case'},s);
 for(let i=0;i<60;i++){if(await evaljs("document.querySelectorAll('.case-field-card').length===9"))break;await wait(2000);}
 assert(await evaljs("document.querySelectorAll('.case-field-card').length===9"),'未显示九张落区图');
 console.log(await evaljs("JSON.stringify({status:document.querySelector('#case-status')?.textContent,cards:document.querySelectorAll('.case-field-card').length,threshold:document.querySelector('#case-chart-threshold [aria-pressed=true]')?.textContent})"));
 fs.writeFileSync('evidence/case-page/default.png',Buffer.from((await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true},s)).data,'base64'));
 await evaljs("document.querySelector('#case-view [data-value=lead]')?.click()");await wait(500);
 fs.writeFileSync('evidence/case-page/lead.png',Buffer.from((await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true},s)).data,'base64'));
 await evaljs("document.querySelector('#case-chart-threshold [data-value=\"0.1\"]')?.click()");await wait(500);
 fs.writeFileSync('evidence/case-page/threshold-0.1.png',Buffer.from((await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true},s)).data,'base64'));
 console.log(await evaljs("JSON.stringify({note:document.querySelector('.case-threshold-note')?.textContent,tableRows:document.querySelectorAll('#case-summary-table tbody tr').length,cardY:[...document.querySelectorAll('.case-field-card')].map(x=>x.getBoundingClientRect().top),width:document.documentElement.scrollWidth})"));

 await send('Emulation.setDeviceMetricsOverride',{width:390,height:1000,deviceScaleFactor:1,mobile:true},s);await wait(250);
 assert(await evaljs("document.documentElement.scrollWidth===390"),'窄屏整页溢出');
 console.log(await evaljs("JSON.stringify({mobileWidth:document.documentElement.scrollWidth,viewport:innerWidth,fieldRows:new Set([...document.querySelectorAll('.case-field-card')].map(x=>x.getBoundingClientRect().top)).size})"));
 await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1800,deviceScaleFactor:1,mobile:false},s);
 await evaljs("document.querySelector('[data-demo-mode=demo]').click()");
 for(let i=0;i<15;i++){if(await evaljs("!!document.querySelector('#demo-case-plot svg')"))break;await wait(500);}
 assert(await evaljs("!!document.querySelector('#demo-case-plot svg')"),'WN3演示图表未显示');
 console.log(await evaljs("JSON.stringify({demoChart:!!document.querySelector('#demo-case-plot svg'),demoMode:document.querySelector('[data-demo-mode=demo]')?.getAttribute('aria-pressed')})"));
 await evaljs("document.querySelector('[data-demo-mode=formal]').click()");
 for(let i=0;i<15;i++){if(await evaljs("document.querySelectorAll('.case-field-card').length===9"))break;await wait(500);}
 assert(await evaljs("document.querySelectorAll('.case-field-card').length===9"),'业务资料未恢复');
 console.log(await evaljs("JSON.stringify({formalRestored:document.querySelectorAll('.case-field-card').length===9})"));
 proc.kill();process.exit(0);
})().catch(e=>{console.error(e);proc.kill();process.exit(1)});
