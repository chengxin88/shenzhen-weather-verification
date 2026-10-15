'use strict';
window.NativeCase=(()=>{
 const by=id=>document.getElementById(id),escape=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const number=x=>Number.isFinite(x)?x.toLocaleString('zh-CN',{maximumFractionDigits:3}):'—';
 const colors=['#0072b2','#009e73','#d55e00','#cc79a7','#e69f00','#56b4e9','#000000','#999999'];
 const names={TS:'TS 综合评分',ETS:'ETS 公平技巧评分',POD:'POD 命中率',FAR:'FAR 空报率',BIAS:'BIAS 频率偏差',ME:'ME 平均误差',MAE:'MAE 平均绝对误差',RMSE:'RMSE 均方根误差',CC:'CC 相关系数'};
 const state={kind:'降水',hours:6,runs:['2026072500'],models:[],window:0,thresholds:[.1,10,25,50],fields:['t','wind','height'],levels:['850'],source:'sz-open-data-v1',spacing:.03,common:true};
 let meta=null,result=null,metric='TS',threshold=.1,field='温度',sequence=0,ready=false;
 let timer=null,controller=null,metaRead=0,resultKey='',cacheable=false;
 const cache=window.CaseViewCache;
 const host=()=>by('case-workspace');
 const buttons=(id,label,items,chosen)=>`<div class="filter-field"><span>${label}</span><div id="${id}" class="choice-buttons" role="group" aria-label="${label}">${items.map(([v,t])=>`<button type="button" data-value="${escape(v)}" aria-pressed="${Array.isArray(chosen)?chosen.includes(v):chosen===v}">${escape(t)}</button>`).join('')}</div></div>`;
 function supported(m){
  if(m.is_demo)return false;
  if(state.kind==='2米气温'&&m.id==='feed')return false;
  if(state.kind==='降水'&&state.hours===2&&['cma9','pangu','zhiji'].includes(m.id))return false;
  const need=state.kind==='降水'?[state.hours===2?'verify_compare_surface':'verify_compare_surface_6h']:['等压面','垂直廓线'].includes(state.kind)?state.levels.map(l=>'verify_'+l):['verify_surface'];
  return need.length>0&&state.runs.every(r=>need.every(l=>(m.runs[r]||[]).includes(l)));
 }
 function refreshModels(reset=false){const available=meta.models.filter(supported).map(m=>m.id);state.models=reset?available:state.models.filter(m=>available.includes(m));}
 function bind(id,fn){by(id)?.querySelectorAll('button').forEach(b=>b.onclick=()=>fn(b.dataset.value));}
 function cancel(){sequence++;clearTimeout(timer);controller?.abort();controller=null;}
 function dirty(){cancel();result=null;cacheable=false;by('case-results').hidden=true;by('case-status').textContent='正在加载所选条件…';drawControls();timer=setTimeout(calculate,300);}
 function requestSelection(){
  const upper=['等压面','垂直廓线'].includes(state.kind),rain=state.kind==='降水';
  return {...state,runs:meta.runs.map(r=>r.id).filter(r=>state.runs.includes(r)),models:meta.models.map(m=>m.id).filter(m=>state.models.includes(m)),
   hours:rain?state.hours:0,thresholds:rain?[...state.thresholds].sort((a,b)=>a-b):[],
   fields:upper?['t','wind','height'].filter(f=>state.fields.includes(f)):[{'降水':'rain','2米气温':'t','10米风速':'wind'}[state.kind]],
   levels:upper?meta.levels.filter(l=>state.levels.includes(l)):['surface'],source:upper?'ana-3d-alpha':state.source};
 }
 function useCache(){cache.useScope(JSON.stringify([meta.cache_version,meta.generation]));}
 function showResult(value,key,local=false,canCache=true){
  result=value;resultKey=key;cacheable=canCache;threshold=result.selection.thresholds[0]??.1;
  if(cacheable&&!local)cache.saveResult(key,result);
  by('case-status').textContent=`${local?'已载入缓存图表':result.cached?'已载入缓存评分':'检验完成'} · ${result.points} 个市界内目标格点 · ${result.computed.slice(0,16).replace('T',' ')} BJT`;
  renderResult();
 }
 function change(key,value,reset=false){state[key]=value;refreshModels(reset);dirty();}
 function toggle(key,v){state[key]=state[key].includes(v)?state[key].filter(x=>x!==v):[...state[key],v];refreshModels();dirty();}
 function drawControls(){
  const upper=['等压面','垂直廓线'].includes(state.kind);
  const expanded=by('case-controls').querySelector('details')?.open;
  by('case-controls').innerHTML=`<div class="primary-filters">${buttons('case-kinds','检验类别',meta.kinds.map(x=>[x,x]),state.kind)}</div>
   <div class="primary-filters">${buttons('case-runs','7月25日起报 · 北京时间',meta.runs.map(r=>[r.id,r.label]),state.runs)}${state.kind==='降水'?buttons('case-hours','累计降水',[2,6,12,24].map(n=>[n,n+' 小时']),state.hours):''}${buttons('case-window','预报时效',[[0,'0—24小时'],[24,'24—48小时'],[48,'48—72小时']],state.window)}</div>
   ${upper?`<div class="primary-filters">${buttons('case-fields','检验要素',[['t','温度'],['wind','风速'],['height','位势高度']],state.fields)}${buttons('case-levels',state.kind==='等压面'?'等压面':'廓线等压面',meta.levels.map(l=>[l,l+' hPa']),state.levels)}</div>`:''}
   <div class="model-row"><span>比较模式</span><div id="case-models" class="model-chips">${[...meta.models,...window.DemoModels.placeholders()].map((m,i)=>`<button type="button" class="model-chip" data-value="${escape(m.id)}" aria-pressed="${state.models.includes(m.id)}" ${supported(m)?'':'disabled title="当前类别、起报或层次没有可评分资料"'}><i style="background:${colors[i]}"></i>${escape(m.is_demo?window.DemoModels.label(m.id):m.label)}</button>`).join('')}</div></div>
   <details class="case-advanced" ${expanded?'open':''}><summary>更多设置 · 实况来源 / 网格 / 共同样本${state.kind==='降水'?' / 降水阈值':''}</summary><div class="primary-filters">
   ${!upper?buttons('case-source','实况分析场',meta.sources.filter(s=>s.id!=='ana-3d-alpha').map(s=>[s.id,s.id==='sz-open-data-v1'?'深圳开放数据':s.id==='ana-sfc-alpha'?'本地分析场 alpha':'本地分析场 gd']),state.source):''}
   ${buttons('case-spacing','经纬网格间隔',[[.03,'0.03°'],[.01,'0.01°']],state.spacing)}
   <label class="check"><input type="checkbox" id="case-common" ${state.common?'checked':''}>仅所选模式共同有效样本</label>
   ${state.kind==='降水'?`<label>降水阈值 · mm<input class="case-input" id="case-threshold-input" value="${state.thresholds.join(', ')}" aria-label="降水阈值，用逗号分隔"></label>`:''}</div>
   <p class="filter-note">双线性插值到同一经纬网格，按深圳市界取样；四角缺测不补值。共同样本要求所有所选模式在该时次都有有效配对。</p></details>
   <div class="case-actions"><span>参考：${escape(upper?'本地三维实况分析 · alpha':meta.sources.find(s=>s.id===state.source)?.label||state.source)}；当前个例为 2026年7月25日。选择条件后自动加载结果。</span></div>`;
  bind('case-kinds',v=>{state.kind=v;state.fields=['t','wind','height'];state.levels=v==='垂直廓线'?['925','850','700']:['850'];metric=v==='降水'?'TS':'RMSE';refreshModels(true);dirty();});
  bind('case-runs',v=>toggle('runs',v));bind('case-models',v=>toggle('models',v));bind('case-hours',v=>change('hours',Number(v),true));bind('case-window',v=>change('window',Number(v)));
  bind('case-fields',v=>toggle('fields',v));bind('case-levels',v=>state.kind==='等压面'?change('levels',[v],true):toggle('levels',v));bind('case-source',v=>change('source',v));bind('case-spacing',v=>change('spacing',Number(v)));
  by('case-common').onchange=e=>change('common',e.target.checked);
  if(by('case-threshold-input'))by('case-threshold-input').onchange=e=>{const ts=e.target.value.replaceAll('，',',').split(',').filter(x=>x.trim()).map(Number);if(!ts.length||ts.length>12||ts.some(t=>!Number.isFinite(t)||t<=0)){cancel();result=null;cacheable=false;by('case-results').hidden=true;by('case-status').textContent='请输入1—12个大于0的有限降水阈值。';return;}change('thresholds',[...new Set(ts)].sort((a,b)=>a-b));};
 }
 async function api(path,body,signal){const response=await fetch('./api/case/'+path,{...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{}),signal});let content;try{content=await response.json();}catch{throw Error(response.status===401||response.redirected?'登录已过期，请刷新页面重新登录。':`检验服务返回异常（HTTP ${response.status}），请稍后重试。`);}if(!response.ok){const error=Error(content.error||'读取检验数据失败。');error.retryAfter=content.retry_after;throw error;}return content;}
 function pause(ms,signal){return new Promise((resolve,reject)=>{const stop=()=>{clearTimeout(wait);reject(new DOMException('已切换条件','AbortError'));};const wait=setTimeout(()=>{signal.removeEventListener('abort',stop);resolve();},ms);signal.addEventListener('abort',stop,{once:true});});}
 async function calculate(){
  cancel();const current=sequence;
  const upper=['等压面','垂直廓线'].includes(state.kind);
  if(!state.models.length||!state.runs.length||(upper&&(!state.fields.length||!state.levels.length))){by('case-status').textContent=upper?'请至少选择一个模式、起报时次、要素和层次。':'请至少选择一个模式和起报时次。';return;}
  controller=new AbortController();const signal=controller.signal;
  result=null;cacheable=false;by('case-results').hidden=true;by('case-status').textContent='正在加载个例检验结果…';
  try{
   if(Date.now()-metaRead>60000){meta=await api('catalog',null,signal);if(current!==sequence)return;metaRead=Date.now();refreshModels();drawControls();}
   if(!state.models.length){by('case-status').textContent='当前资料不支持所选模式，请重新选择。';return;}
   const request=requestSelection(),key=JSON.stringify(request);useCache();
   const saved=cache.result(key);if(saved){showResult(saved,key,true);return;}
   let job;
   while(current===sequence){
    try{job=await api('score',request,signal);break;}
    catch(error){if(!error.retryAfter)throw error;by('case-status').textContent='正在等待已有检验任务完成…';await pause(error.retryAfter*1000,signal);}
   }
   while(current===sequence&&['queued','running'].includes(job.status)){
    by('case-status').innerHTML=`<div class="case-progress"><progress max="1" value="${job.progress||0}"></progress><span>${escape(job.message)} · 首次生成后会缓存结果</span></div>`;
    await pause(1000,signal);if(current!==sequence)return;job=await api('jobs/'+job.id,null,signal);
   }
   if(current!==sequence)return;if(job.status!=='done')throw Error(job.message||'本次检验未完成。');
   if(job.generation!==meta.generation){meta.generation=job.generation;metaRead=0;useCache();}
   showResult(job.result,key,false,!job.retryable);
  }catch(error){if(current===sequence&&error.name!=='AbortError')by('case-status').textContent=error.message;}
  finally{if(current===sequence)controller=null;}
 }
 function table(rows,cols){return `<table class="case-table"><thead><tr>${cols.map(k=>`<th>${escape(k)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(k=>`<td>${typeof r[k]==='number'?number(r[k]):escape(r[k]==='surface'?'地面':r[k]??'—')}</td>`).join('')}</tr>`).join('')}</tbody></table>`;}
 function download(rows,label){if(!rows.length)return;rows=rows.map(r=>({...r,'实况来源':result.reference,'实况来源ID':result.selection.source,'网格间隔(度)':result.selection.spacing,'仅共同样本':result.selection.common,'检验计算时间':result.computed}));const keys=[...new Set(rows.flatMap(r=>Object.keys(r)))];const q=v=>'"'+String(v??'').replaceAll('"','""')+'"';const csv='\ufeff'+[keys,...rows.map(r=>keys.map(k=>r[k]))].map(row=>row.map(q).join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='深圳个例-'+label+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);}
 function renderResult(){
  by('case-results').hidden=false;const rain=result.selection.kind==='降水';const rows=result.summary;
  const columns=['模式','起报','要素','层次',...(rain?['阈值(mm)','累计(h)','TS','ETS','POD','FAR','BIAS']:['ME','MAE','RMSE','CC','预报均值','实况均值']), '单位','N','有效时次数'];
  by('case-results').innerHTML=`<section class="results"><div class="result-heading"><div><h2>个例模式对比</h2><p>实况：${escape(result.reference)} · ${escape(result.selection.kind)}</p></div><button id="case-export-svg" class="quiet">导出图表 ↓</button></div><div id="case-view-tools" class="case-view-tools"></div><div id="case-plot" class="case-plot" role="region" aria-label="个例检验图表"></div><div class="case-explain">${(result.run_coverage||[]).map(c=>`<p>${c.run.endsWith('00')?'08时':'20时'}起报：实际纳入 ${c.valid_times.length} 个有效时次（${c.valid_times.join('、')||'无'}）。</p>`).join('')}<p>跨起报比较未自动对齐有效时间；请结合实际时次和时效判断。</p>汇总合并原始统计量，不对 TS、ETS、RMSE 或 CC 简单取平均。无有效分母显示“—”。ME＝预报－实况。</div></section>
   <section class="table-card"><div class="table-heading"><div><h2>汇总评分</h2><p>全部所选要素、层次和阈值 · N 为有效格点×时次对数</p></div><button id="case-summary-csv" class="quiet">导出汇总 CSV ↓</button></div><div class="table-scroll">${rows.length?table(rows,columns):'<div class="case-empty">当前条件没有可评分样本，请查看缺测记录或调整所选模式。</div>'}</div></section>
   <details class="case-detail"><summary>逐时次评分 · ${result.scores.length} 条</summary><div class="table-heading"><span>保留原始统计量供复核</span><button id="case-detail-csv" class="quiet">导出逐时次 CSV ↓</button></div><div class="table-scroll">${table(result.scores,[...columns.filter(c=>c!=='有效时次数'),'有效时间','时效(h)'])}</div></details>
   <details class="case-detail" ${rows.length?'':'open'}><summary>缺测与未评分 · ${result.issues.length} 条</summary><div class="table-heading"><span>缺测不替换为零分</span><button id="case-issues-csv" class="quiet">导出缺测记录 ↓</button></div><div class="table-scroll">${table(result.issues,['模式','起报','有效时间','时效(h)','要素','层次','原因'])}</div></details>
   <details class="case-detail"><summary>资料说明与检验口径</summary><div class="case-explain"><p>${escape(meta.notice)}</p><p>${escape(result.method)}</p><p>${result.selection.source==='sz-open-data-v1'?'深圳开放数据2／6小时降水分别取接口RAIN02H／RAIN06H，按实际有效时间精确读取。':'本地6小时实况降水由连续三个2小时段相加。'}12／24小时降水由连续、不重叠的6小时段相加，缺一段即不评分。FEED降水沿用每份2小时累计的暂定口径。</p><p>高空实况在原生相邻层间按 log(p) 插值，不外推。500／200 hPa及部分700 hPa可能超出实况覆盖；位势高度单位为 gpm。</p>${result.warnings.map(w=>`<p>${escape(w)}</p>`).join('')}</div></details>`;
  by('case-summary-csv').onclick=()=>download(rows,'汇总评分');by('case-detail-csv').onclick=()=>download(result.scores,'逐时次评分');by('case-issues-csv').onclick=()=>download(result.issues,'缺测记录');
  by('case-export-svg').onclick=()=>{const svg=by('case-plot').querySelector('svg');if(!svg)return;const url=URL.createObjectURL(new Blob([svg.outerHTML],{type:'image/svg+xml;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='深圳个例-'+metric+'.svg';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);};
  drawChart();
 }
 function drawChart(){
  const rain=result.selection.kind==='降水',profile=result.selection.kind==='垂直廓线';const available=rain?['TS','ETS','POD','FAR','BIAS']:['ME','MAE','RMSE','CC'];
  if(!available.includes(metric))metric=available[0];const fields=[...new Set(result.summary.map(r=>r['要素']))];if(!fields.includes(field))field=fields[0];
  by('case-view-tools').innerHTML=buttons('case-metric','评分指标',available.map(m=>[m,names[m]]),metric)+(rain?buttons('case-chart-threshold','降水阈值 · mm',result.selection.thresholds.map(t=>[t,String(t)]),threshold):fields.length>1?buttons('case-chart-field','显示要素',fields.map(f=>[f,f]),field):'');
  bind('case-metric',v=>{metric=v;drawChart();});bind('case-chart-threshold',v=>{threshold=Number(v);drawChart();});bind('case-chart-field',v=>{field=v;drawChart();});
  const rows=result.summary.filter(r=>rain?r['阈值(mm)']===threshold:r['要素']===field);
  if(!rows.length){by('case-plot').innerHTML='<div class="case-empty">当前条件没有评分。请查看下方缺测记录。</div>';return;}
  const finite=rows.map(r=>r[metric]).filter(Number.isFinite);if(!finite.length){by('case-plot').innerHTML='<div class="case-empty">当前指标没有有效分母，评分为缺测。</div>';return;}
  const viewKey=JSON.stringify([metric,threshold,field]);
  const savedChart=cacheable?cache.chart(resultKey,viewKey):null;
  if(savedChart){
   const template=document.createElement('template');template.innerHTML=savedChart;
   const allowed=new Set(['svg','rect','text','line','circle','title']);
   const attributes=new Set(['xmlns','viewBox','role','aria-label','style','width','height','fill','x','y','x1','x2','y1','y2','stroke','stroke-width','stroke-dasharray','text-anchor','font-size','rx','r','cx','cy']);
   const safe=[...template.content.querySelectorAll('*')].every(node=>allowed.has(node.localName)&&[...node.attributes].every(a=>attributes.has(a.name)&&(!/url\s*\(/i.test(a.value))&&(a.name!=='style'||a.value==='font-family:Segoe UI,Microsoft YaHei,sans-serif')));
   if(safe&&template.content.firstElementChild?.localName==='svg'){by('case-plot').replaceChildren(template.content);return;}
  }
  const color=r=>colors[Math.max(0,meta.models.findIndex(m=>m.label===r['模式']))%colors.length];
  const unitText=!rain&&metric!=='CC'?' / '+(rows[0]['单位']||''):'';
  const W=1000,left=250,right=90,span=W-left-right;let lo=Math.min(0,...finite),hi=Math.max(...finite,metric==='BIAS'?1:0);if(hi===lo)hi=lo+1;hi+=Math.max(.01,(hi-lo)*.06);const x=v=>left+(v-lo)/(hi-lo)*span;
  const plotHeight=profile?330:Math.max(180,rows.length*39);const H=plotHeight+100;
  let svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${escape(names[metric])}" style="font-family:Segoe UI,Microsoft YaHei,sans-serif"><rect width="${W}" height="${H}" fill="white"/><text x="${left}" y="20" font-size="15" fill="#203b4e">${escape(names[metric])}${rain?' · ≥'+threshold+' mm':' · '+escape(field)+escape(unitText)}</text>`;
  for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4;svg+=`<line x1="${x(v)}" x2="${x(v)}" y1="42" y2="${H-40}" stroke="#e1e9ee"/><text x="${x(v)}" y="${H-15}" text-anchor="middle" fill="#637a89" font-size="12">${number(v)}</text>`;}
  svg+=`<line x1="${x(0)}" x2="${x(0)}" y1="42" y2="${H-40}" stroke="#91a8b5"/>`;
  if(profile){
   const levels=[...new Set(rows.map(r=>Number(r['层次'])))].sort((a,b)=>b-a);const y=p=>55+(levels[0]-p)/(levels[0]-levels.at(-1)||1)*(plotHeight-35);
   for(const p of levels)svg+=`<text x="${left-18}" y="${y(p)+4}" text-anchor="end" fill="#637a89" font-size="12">${p} hPa</text>`;
   const groups=[...new Set(rows.map(r=>r['模式']+' · '+r['起报']))];
   groups.forEach((group,i)=>{const groupRows=rows.filter(r=>r['模式']+' · '+r['起报']===group).sort((a,b)=>Number(b['层次'])-Number(a['层次']));let previous=null;for(const r of groupRows){if(!Number.isFinite(r[metric])){previous=null;continue;}if(previous)svg+=`<line x1="${x(previous[metric])}" y1="${y(Number(previous['层次']))}" x2="${x(r[metric])}" y2="${y(Number(r['层次']))}" stroke="${color(r)}" stroke-width="2" ${r['起报'].includes('20')?'stroke-dasharray="5 4"':''}/>`;svg+=`<circle cx="${x(r[metric])}" cy="${y(Number(r['层次']))}" r="4" fill="${color(r)}"><title>${escape(group)} · ${r['层次']}hPa · ${number(r[metric])}</title></circle>`;previous=r;}svg+=`<text x="8" y="${55+i*23}" fill="${color(groupRows[0])}" font-size="11">${escape(group)}</text>`;});
  }else rows.forEach((r,i)=>{const y=57+i*(plotHeight/rows.length);const v=r[metric];svg+=`<text x="${left-14}" y="${y+5}" text-anchor="end" font-size="12" fill="#36566b">${escape(r['模式'])} · ${escape(r['起报'].slice(-3))}</text>`;if(Number.isFinite(v))svg+=`<rect x="${Math.min(x(0),x(v))}" y="${y-10}" width="${Math.max(1,Math.abs(x(v)-x(0)))}" height="21" rx="3" fill="${color(r)}"><title>${escape(r['模式'])} · ${escape(r['起报'])} · ${metric} ${number(v)} · N ${r.N}</title></rect>`;svg+=`<text x="${Number.isFinite(v)?x(v)+9:x(0)+9}" y="${y+5}" font-size="12" fill="#36566b">${number(v)}</text>`;});
  const image=svg+'</svg>';by('case-plot').innerHTML=image;if(cacheable)cache.saveChart(resultKey,viewKey,image);
 }
 async function open(){
  if(ready){if(Date.now()-metaRead>60000)return calculate();return;}ready=true;
  host().innerHTML='<div class="case-heading"><p class="eyebrow">CASE VERIFICATION</p><h1>深圳市 · 7月25日个例检验</h1><p>比较同一次天气过程中的模式表现。</p></div><section class="filter-card"><div id="case-controls">正在读取个例条件…</div></section><div id="case-status" class="notice" role="status"></div><div id="case-results" hidden></div>';
  const current=sequence;
  try{meta=await api('catalog');if(current!==sequence)return;metaRead=Date.now();useCache();refreshModels(true);drawControls();await calculate();}catch(error){if(current!==sequence)return;ready=false;by('case-controls').innerHTML='<button id="case-retry" class="quiet">重新读取个例资料</button>';by('case-status').textContent=error.message;by('case-retry').onclick=open;}
 }
 return {open,close(){cancel();ready=false;},refresh(){cancel();ready=false;result=null;return open();}};
})();
