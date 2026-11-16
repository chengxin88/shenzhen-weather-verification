'use strict';
window.NativeCase=(()=>{
 const by=id=>document.getElementById(id),escape=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const number=x=>Number.isFinite(x)?x.toLocaleString('zh-CN',{minimumFractionDigits:3,maximumFractionDigits:3}):'—';
 const colors=['#0072b2','#009e73','#d55e00','#cc79a7','#e69f00','#56b4e9','#000000','#999999'];
 const names={TS:'TS 综合评分',ETS:'ETS 公平技巧评分',POD:'POD 命中率',FAR:'FAR 空报率',BIAS:'BIAS 频率偏差',ME:'ME 平均误差',MAE:'MAE 平均绝对误差',RMSE:'RMSE 均方根误差',CC:'CC 相关系数'};
 const state={kind:'降水',hours:6,case:null,runs:[],models:[],window:0,thresholds:[.1,10,25,50],fields:['t','wind','height'],levels:['850'],source:'sz-open-data-v1',spacing:.03,common:true};
 let meta=null,result=null,metric='TS',threshold=.1,field='温度',sequence=0,ready=false,view='models',valid='',fieldData=null;
 let timer=null,controller=null,metaRead=0,resultKey='',cacheable=false;
 let fieldController=null,fieldSequence=0;
 const fieldCache=new Map();
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
 function cancel(){fieldSequence++;fieldController?.abort();fieldController=null;sequence++;clearTimeout(timer);controller?.abort();controller=null;}
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
  result=value;fieldData=null;resultKey=key;cacheable=canCache;threshold=defaultThreshold();
  if(cacheable&&!local)cache.saveResult(key,result);
  by('case-status').textContent=`${local?'已载入缓存图表':result.cached?'已载入缓存评分':'检验完成'} · ${result.points} 个市界内目标格点 · ${result.computed.slice(0,16).replace('T',' ')} BJT`;
  renderResult();
 }
 // 每档阈值的实况情况：share=实况达到该档的样本占比；mixed=实况既非全有也非全无的时次数。
 function rainMix(t){
  const first=result.scores.find(r=>r['阈值(mm)']===t&&r.N>0);if(!first)return {share:NaN,mixed:0,times:0};
  let obs=0,n=0,mixed=0,times=0;
  for(const r of result.scores){if(r['阈值(mm)']!==t||r['模式']!==first['模式']||!(r.N>0))continue;const o=(r.H||0)+(r.M||0);obs+=o;n+=r.N;times++;if(o>0&&o<r.N)mixed++;}
  return {share:obs/n,mixed,times};
 }
 function defaultThreshold(){
  const all=result.selection.thresholds;if(result.selection.kind!=='降水')return all[0]??.1;
  const useful=all.filter(t=>{const m=rainMix(t);return m.mixed>0&&m.share>=.1&&m.share<=.9;});
  return useful.length?Math.max(...useful):all[0]??.1;
 }
 function change(key,value,reset=false){state[key]=value;refreshModels(reset);dirty();}
 function toggle(key,v){state[key]=state[key].includes(v)?state[key].filter(x=>x!==v):[...state[key],v];refreshModels();dirty();}
 function drawControls(){
  const upper=['等压面','垂直廓线'].includes(state.kind);
  const expanded=by('case-controls').querySelector('details')?.open;
  by('case-controls').innerHTML=`<div class="primary-filters case-selector-row"><label class="case-selector">个例<select id="case-select" aria-label="个例">${meta.cases.map(c=>`<option value="${escape(c.id)}" ${state.case===c.id?'selected':''}>${escape(c.label)}</option>`).join('')}</select></label>${buttons('case-runs','起报时间 · 北京时间',meta.runs.map(r=>[r.id,cnRun(r.id)]),state.runs)}</div><div class="primary-filters">${buttons('case-kinds','检验类别',meta.kinds.map(x=>[x,x]),state.kind)}</div>
   <div class="primary-filters">${state.kind==='降水'?buttons('case-hours','累计降水',[2,6,12,24].map(n=>[n,n+' 小时']),state.hours):''}${buttons('case-window','看起报后第几天',[[0,'第1天（0—24小时）'],[24,'第2天（24—48小时）'],[48,'第3天（48—72小时）']],state.window)}</div>
   ${upper?`<div class="primary-filters">${buttons('case-fields','检验要素',[['t','温度'],['wind','风速'],['height','位势高度']],state.fields)}${buttons('case-levels',state.kind==='等压面'?'等压面':'廓线等压面',meta.levels.map(l=>[l,l+' hPa']),state.levels)}</div>`:''}
   <div class="model-row"><span>比较模式</span><div id="case-models" class="model-chips">${meta.models.map((m,i)=>`<button type="button" class="model-chip" data-value="${escape(m.id)}" aria-pressed="${state.models.includes(m.id)}" ${supported(m)?'':'disabled title="当前类别、起报或层次没有可评分资料"'}><i style="background:${colors[i]}"></i>${escape(m.is_demo?window.DemoModels.label(m.id):m.label)}</button>`).join('')}</div></div>
   <details class="case-advanced" ${expanded?'open':''}><summary>更多设置 · 实况来源 / 网格 / 共同样本${state.kind==='降水'?' / 降水阈值':''}</summary><div class="primary-filters">
   ${!upper?buttons('case-source','实况分析场',meta.sources.filter(s=>s.id!=='ana-3d-alpha').map(s=>[s.id,s.id==='sz-open-data-v1'?'深圳开放数据':s.id==='ana-sfc-alpha'?'本地分析场 alpha':'本地分析场 gd']),state.source):''}
   ${buttons('case-spacing','经纬网格间隔',[[.03,'0.03°'],[.01,'0.01°']],state.spacing)}
   <label class="check"><input type="checkbox" id="case-common" ${state.common?'checked':''}>仅所选模式共同有效样本</label>
   ${state.kind==='降水'?`<label>降水阈值 · mm<input class="case-input" id="case-threshold-input" value="${state.thresholds.join(', ')}" aria-label="降水阈值，用逗号分隔"></label>`:''}</div>
   <p class="filter-note">双线性插值到同一经纬网格，按深圳市界取样；四角缺测不补值。共同样本要求所有所选模式在该时次都有有效配对。</p></details>
   <div class="case-actions"><span>参考：${escape(upper?'本地三维实况分析 · alpha':meta.sources.find(s=>s.id===state.source)?.label||state.source)}；当前个例为 ${escape(meta.cases.find(c=>c.id===state.case)?.date)}。选择条件后自动加载结果。</span></div>`;
  by('case-select').onchange=e=>{state.case=e.target.value;meta.runs=meta.cases.find(c=>c.id===state.case).runs;state.runs=[meta.runs[0].id];refreshModels(true);dirty();};
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
 function table(rows,cols){return `<table class="case-table"><thead><tr>${cols.map(k=>`<th>${escape(k)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(k=>`<td>${typeof r[k]==='number'?(['N','H','M','F','C','有效时次数','时效(h)','阈值(mm)','累计(h)'].includes(k)?String(r[k]):number(r[k])):escape(r[k]==='surface'?'地面':r[k]??'—')}</td>`).join('')}</tr>`).join('')}</tbody></table>`;}
 function download(rows,label){if(!rows.length)return;rows=rows.map(r=>({...r,'实况来源':result.reference,'实况来源ID':result.selection.source,'网格间隔(度)':result.selection.spacing,'仅共同样本':result.selection.common,'检验计算时间':result.computed}));const keys=[...new Set(rows.flatMap(r=>Object.keys(r)))];const q=v=>'"'+String(v??'').replaceAll('"','""')+'"';const csv='\ufeff'+[keys,...rows.map(r=>keys.map(k=>r[k]))].map(row=>row.map(q).join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='深圳个例-'+label+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);}
 function renderResult(){
  by('case-results').hidden=false;const rain=result.selection.kind==='降水';const rows=result.summary;
  const columns=['模式','起报','要素','层次',...(rain?['阈值(mm)','累计(h)','TS','ETS','POD','FAR','BIAS']:['ME','MAE','RMSE','CC','预报均值','实况均值']), '单位','N','有效时次数'].filter(k=>rain||!['要素','层次','单位'].includes(k)||new Set(rows.map(r=>r[k])).size!==1);
  by('case-results').innerHTML=`${rain?'<section class="results case-fields"><div class="result-heading"><div><h2>落区对比</h2><p id="case-field-caption"></p></div><div id="case-valid-tools"></div></div><div id="case-field-grid" class="case-field-grid"></div><div id="case-rain-legend" class="case-rain-legend"></div></section>':''}<section class="results"><div class="result-heading"><div><h2>评分</h2><p>实况：${escape(result.reference)} · ${escape(result.selection.kind)}</p></div><button id="case-export-svg" class="quiet">导出图表 ↓</button></div><div id="case-view-tools" class="case-view-tools"></div><div id="case-plot" class="case-plot" role="region" aria-label="个例检验图表"></div><div class="case-explain">${(result.run_coverage||[]).map(c=>`<p>${c.run.endsWith('00')?'08时':'20时'}起报：实际纳入 ${c.valid_times.length} 个有效时次（${c.valid_times.join('、')||'无'}）。</p>`).join('')}<p>跨起报比较未自动对齐有效时间；请结合实际时次和时效判断。</p>汇总合并原始统计量，不对 TS、ETS、RMSE 或 CC 简单取平均。无有效分母显示“—”。ME＝预报－实况。</div></section>
   <section class="table-card"><div class="table-heading"><div><h2>汇总评分</h2><p id="case-summary-note"></p></div><button id="case-summary-csv" class="quiet">导出汇总 CSV ↓</button></div><div class="table-scroll">${rows.length?'<div id="case-summary-table"></div>':'<div class="case-empty">当前条件没有可评分样本，请查看缺测记录或调整所选模式。</div>'}</div></section>
   <details class="case-detail"><summary>逐时次评分 · ${result.scores.length} 条</summary><div class="table-heading"><span>保留原始统计量供复核</span><button id="case-detail-csv" class="quiet">导出逐时次 CSV ↓</button></div><div class="table-scroll">${table(result.scores,[...columns.filter(c=>c!=='有效时次数'),'有效时间','时效(h)'])}</div></details>
   <details class="case-detail" ${rows.length?'':'open'}><summary>缺测与未评分 · ${result.issues.length} 条</summary><div class="table-heading"><span>缺测不替换为零分</span><button id="case-issues-csv" class="quiet">导出缺测记录 ↓</button></div><div class="table-scroll">${table(result.issues,['模式','起报','有效时间','时效(h)','要素','层次','原因'])}</div></details>
   <details class="case-detail"><summary>资料说明与检验口径</summary><div class="case-explain"><p>${escape(meta.notice)}</p><p>${escape(result.method)}</p><p>${result.selection.source==='sz-open-data-v1'?'深圳开放数据2／6小时降水分别取接口RAIN02H／RAIN06H，按实际有效时间精确读取。':'本地6小时实况降水由连续三个2小时段相加。'}12／24小时降水由连续、不重叠的6小时段相加，缺一段即不评分。FEED降水沿用每份2小时累计的暂定口径。</p><p>高空实况在原生相邻层间按 log(p) 插值，不外推。500／200 hPa及部分700 hPa可能超出实况覆盖；位势高度单位为 gpm。</p>${result.warnings.map(w=>`<p>${escape(w)}</p>`).join('')}</div></details>`;
  by('case-summary-csv').onclick=()=>download(rows,'汇总评分');by('case-detail-csv').onclick=()=>download(result.scores,'逐时次评分');by('case-issues-csv').onclick=()=>download(result.issues,'缺测记录');
  by('case-export-svg').onclick=()=>{const svg=by('case-plot').querySelector('svg');if(!svg)return;const url=URL.createObjectURL(new Blob([svg.outerHTML],{type:'image/svg+xml;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='深圳个例-'+metric+'.svg';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);};
  drawChart();if(rain)drawFields();
 }
 function drawChart(){
  const rain=result.selection.kind==='降水',profile=result.selection.kind==='垂直廓线';const available=rain?['TS','ETS','POD','FAR','BIAS']:['ME','MAE','RMSE','CC'];
  if(!available.includes(metric))metric=available[0];const fields=[...new Set(result.summary.map(r=>r['要素']))];if(!fields.includes(field))field=fields[0];
  by('case-view-tools').innerHTML=buttons('case-metric','评分指标',available.map(m=>[m,names[m]]),metric)+(rain?buttons('case-chart-threshold','降水阈值 · mm',result.selection.thresholds.map(t=>[t,String(t)]),threshold):fields.length>1?buttons('case-chart-field','显示要素',fields.map(f=>[f,f]),field):'')+buttons('case-view','视图',[['models','各模式对比'],['lead','随时效']],view);
  drawSummary();bind('case-view',v=>{view=v;drawChart();});
  bind('case-metric',v=>{metric=v;drawChart();});bind('case-chart-threshold',v=>{threshold=Number(v);drawChart();if(fieldData)renderFields();});bind('case-chart-field',v=>{field=v;drawChart();});
  if(rain){const mix=rainMix(threshold);if(mix.times&&!mix.mixed)by('case-view-tools').insertAdjacentHTML('beforeend',`<p class="filter-note case-threshold-note">≥${threshold} mm 这一档没有区分度：${mix.share>0?`${mix.times} 个时次里，实况要么全市都达到、要么全市都没达到，评分只反映降水时段报没报对，分不出落区好坏。`:'实况没有格点达到这一档。'}建议换一档阈值看。</p>`);}
  if(view==='lead'){drawLeadChart();return;}
  const rows=result.summary.filter(r=>rain?r['阈值(mm)']===threshold:r['要素']===field);
  if(!rows.length){by('case-plot').innerHTML='<div class="case-empty">当前条件没有评分。请查看下方缺测记录。</div>';return;}
  const finite=rows.map(r=>r[metric]).filter(Number.isFinite);if(!finite.length){by('case-plot').innerHTML='<div class="case-empty">当前指标没有有效分母，评分为缺测。</div>';return;}
  const viewKey=JSON.stringify([view,metric,threshold,field]);
  if(readChart(viewKey))return;
  const color=r=>colors[Math.max(0,meta.models.findIndex(m=>m.label===r['模式']))%colors.length];
  const unitText=!rain&&metric!=='CC'?' / '+(rows[0]['单位']||''):'';
  const W=1000,left=250,right=90,span=W-left-right,{lo,hi,ticks}=axis(finite);const x=v=>left+(v-lo)/(hi-lo)*span;
  const plotHeight=profile?330:Math.max(180,rows.length*39);const H=plotHeight+100;
  let svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${escape(names[metric])}" style="font-family:Segoe UI,Microsoft YaHei,sans-serif"><rect width="${W}" height="${H}" fill="white"/><text x="${left}" y="20" font-size="15" fill="#203b4e">${escape(names[metric])}${rain?' · ≥'+threshold+' mm':' · '+escape(field)+escape(unitText)}</text>`;
  for(const v of ticks){svg+=`<line x1="${x(v)}" x2="${x(v)}" y1="42" y2="${H-40}" stroke="#e1e9ee"/><text x="${x(v)}" y="${H-15}" text-anchor="middle" fill="#637a89" font-size="12">${tickNumber(v)}</text>`;}
  svg+=`<line x1="${x(0)}" x2="${x(0)}" y1="42" y2="${H-40}" stroke="#91a8b5"/>`;
  if(profile){
   const levels=[...new Set(rows.map(r=>Number(r['层次'])))].sort((a,b)=>b-a);const y=p=>55+(levels[0]-p)/(levels[0]-levels.at(-1)||1)*(plotHeight-35);
   for(const p of levels)svg+=`<text x="${left-18}" y="${y(p)+4}" text-anchor="end" fill="#637a89" font-size="12">${p} hPa</text>`;
   const groups=[...new Set(rows.map(r=>r['模式']+' · '+r['起报']))];
   groups.forEach((group,i)=>{const groupRows=rows.filter(r=>r['模式']+' · '+r['起报']===group).sort((a,b)=>Number(b['层次'])-Number(a['层次']));let previous=null;for(const r of groupRows){if(!Number.isFinite(r[metric])){previous=null;continue;}if(previous)svg+=`<line x1="${x(previous[metric])}" y1="${y(Number(previous['层次']))}" x2="${x(r[metric])}" y2="${y(Number(r['层次']))}" stroke="${color(r)}" stroke-width="2" ${r['起报'].includes('20')?'stroke-dasharray="5 4"':''}/>`;svg+=`<circle cx="${x(r[metric])}" cy="${y(Number(r['层次']))}" r="4" fill="${color(r)}"><title>${escape(group)} · ${r['层次']}hPa · ${number(r[metric])}</title></circle>`;previous=r;}svg+=`<text x="8" y="${55+i*23}" fill="${color(groupRows[0])}" font-size="11">${escape(group)}</text>`;});
  }else rows.forEach((r,i)=>{const y=57+i*(plotHeight/rows.length);const v=r[metric];svg+=`<text x="${left-14}" y="${y+5}" text-anchor="end" font-size="12" fill="#36566b">${escape(r['模式'])} · ${escape(r['起报'].slice(-3))}</text>`;if(Number.isFinite(v))svg+=`<rect x="${Math.min(x(0),x(v))}" y="${y-10}" width="${Math.max(1,Math.abs(x(v)-x(0)))}" height="21" rx="3" fill="${color(r)}"><title>${escape(r['模式'])} · ${escape(r['起报'])} · ${metric} ${number(v)} · N ${r.N}</title></rect>`;svg+=`<text x="${Number.isFinite(v)?x(v)+9:x(0)+9}" y="${y+5}" font-size="12" fill="#36566b">${number(v)}</text>`;});
  const image=svg+'</svg>';by('case-plot').innerHTML=image;if(cacheable)cache.saveChart(resultKey,viewKey,image);
 }
 const tickNumber=x=>Number(x.toPrecision(10)).toLocaleString('zh-CN',{maximumFractionDigits:6});
 function axis(values){
  if(['TS','ETS','POD','FAR'].includes(metric))return {lo:0,hi:1,ticks:[0,.2,.4,.6,.8,1]};
  let lo=Math.min(0,...values),hi=Math.max(0,...values,metric==='BIAS'?1:0);if(hi===lo)hi=lo+1;
  const raw=(hi-lo)/5,power=10**Math.floor(Math.log10(raw)),n=raw/power;
  const step=(n<=1?1:n<=2?2:n<=5?5:10)*power;
  lo=Math.floor(lo/step)*step;hi=Math.ceil(hi/step)*step;
  return {lo,hi,ticks:Array.from({length:Math.round((hi-lo)/step)+1},(_,i)=>lo+i*step)};
 }
 const modelColor=id=>colors[Math.max(0,meta.models.findIndex(m=>m.id===id||m.label===id))%colors.length];
 function rowValue(r){
  if(!r)return null;
  const h=r.H,m=r.M,f=r.F,ratio=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)&&b!==0?a/b:null;
  return metric==='POD'?ratio(h,h+m):metric==='FAR'?ratio(f,h+f):metric==='BIAS'?ratio(h+f,h+m):r[metric];
 }
 function drawSummary(){
  const rain=result.selection.kind==='降水',rows=result.summary,box=by('case-summary-table');if(!box)return;
  if(rain){
   const multi=result.selection.runs.length>1,groups=new Map();
   for(const r of rows){const key=r['模式']+' · '+r['起报'];if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
   const ts=result.selection.thresholds;
   box.innerHTML=`<table class="case-table"><thead><tr><th>模式${multi?' · 起报':''}</th>${ts.map(t=>`<th>≥${t} mm</th>`).join('')}<th>N</th><th>有效时次数</th></tr></thead><tbody>${[...groups.values()].map(g=>`<tr><td>${escape(g[0]['模式'])}${multi?' · '+escape(g[0]['起报']):''}</td>${ts.map(t=>`<td>${number(g.find(r=>r['阈值(mm)']===t)?.[metric])}</td>`).join('')}<td>${g[0].N}</td><td>${g[0]['有效时次数']}</td></tr>`).join('')}</tbody></table>`;
   by('case-summary-note').textContent=`${names[metric]} · ${result.selection.hours}小时累计 · N 为有效格点×时次对数；各阈值的 N 与有效时次数相同`;
  }else{
   const dimensions=['要素','层次','单位'],fixed=dimensions.filter(k=>new Set(rows.map(r=>r[k])).size===1);
   const columns=['模式','起报',...dimensions.filter(k=>!fixed.includes(k)), 'ME','MAE','RMSE','CC','预报均值','实况均值','N','有效时次数'];
   box.innerHTML=table(rows,columns);
   by('case-summary-note').textContent=fixed.map(k=>k+'：'+(rows[0][k]==='surface'?'地面':rows[0][k])).join(' · ')+' · N 为有效格点×时次对数';
  }
 }
 function readChart(viewKey){
  const saved=cacheable?cache.chart(resultKey,viewKey):null;if(!saved)return false;
  const template=document.createElement('template');template.innerHTML=saved;
  const allowed=new Set(['svg','rect','text','line','circle','title']);
  const attributes=new Set(['xmlns','viewBox','role','aria-label','style','width','height','fill','x','y','x1','x2','y1','y2','stroke','stroke-width','stroke-dasharray','text-anchor','font-size','rx','r','cx','cy']);
  const safe=[...template.content.querySelectorAll('*')].every(node=>allowed.has(node.localName)&&[...node.attributes].every(a=>attributes.has(a.name)&&! /url\s*\(/i.test(a.value)&&(a.name!=='style'||a.value==='font-family:Segoe UI,Microsoft YaHei,sans-serif')));
  if(safe&&template.content.childElementCount===1&&template.content.firstElementChild?.localName==='svg'){by('case-plot').replaceChildren(template.content);return true;}return false;
 }
 function drawLeadChart(){
  const rain=result.selection.kind==='降水',rows=result.scores.filter(r=>rain?r['阈值(mm)']===threshold:r['要素']===field);
  const key=JSON.stringify([view,metric,threshold,field]);if(readChart(key))return;
  const values=rows.map(rowValue).filter(Number.isFinite);
  if(!values.length){by('case-plot').innerHTML='<div class="case-empty">当前指标没有有效分母，评分为缺测。</div>';return;}
  const levels=new Set(rows.map(r=>r['层次'])),groupName=r=>r['模式']+' · '+r['起报']+(levels.size>1?' · '+r['层次']+' hPa':'');
  const groups=new Map();for(const r of rows){const name=groupName(r);if(!groups.has(name))groups.set(name,[]);groups.get(name).push(r);}
  const leads=[...new Set(rows.map(r=>r['时效(h)']))].sort((a,b)=>a-b),W=1000,H=420+Math.ceil(groups.size/3)*25,left=80,right=40,top=45,bottom=320;
  const {lo,hi,ticks}=axis(values),x=v=>left+(v-leads[0])/(leads.at(-1)-leads[0]||1)*(W-left-right),y=v=>bottom-(v-lo)/(hi-lo)*(bottom-top);
  let svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${escape(names[metric])}随预报时效" style="font-family:Segoe UI,Microsoft YaHei,sans-serif"><rect width="${W}" height="${H}" fill="white"/><text x="${left}" y="22" font-size="15" fill="#203b4e">${escape(names[metric])}${rain?' · ≥'+threshold+' mm':' · '+escape(field)}</text>`;
  for(const v of ticks)svg+=`<line x1="${left}" x2="${W-right}" y1="${y(v)}" y2="${y(v)}" stroke="#e1e9ee"/><text x="${left-12}" y="${y(v)+4}" text-anchor="end" font-size="12" fill="#637a89">${tickNumber(v)}</text>`;
  for(const lead of leads)svg+=`<text x="${x(lead)}" y="${bottom+23}" text-anchor="middle" font-size="12" fill="#637a89">${lead}</text>`;
  svg+=`<text x="${(W+left-right)/2}" y="${bottom+48}" text-anchor="middle" font-size="13" fill="#36566b">预报时效（小时）</text>`;
  [...groups].forEach(([name,rs],i)=>{
   const color=modelColor(rs[0]['模式']),dash=rs[0]['起报'].includes('20时')?'stroke-dasharray="5 4"':'';let previous=null;
   for(const lead of leads){const r=rs.find(r=>r['时效(h)']===lead),v=rowValue(r);if(!Number.isFinite(v)){previous=null;continue;}
    if(previous)svg+=`<line x1="${x(previous.lead)}" y1="${y(previous.v)}" x2="${x(lead)}" y2="${y(v)}" stroke="${color}" stroke-width="2" ${dash}/>`;
    svg+=`<circle cx="${x(lead)}" cy="${y(v)}" r="4" fill="${color}"><title>${escape(name)} · ${lead}小时 · ${number(v)}</title></circle>`;previous={lead,v};
   }
   const lx=left+(i%3)*300,ly=bottom+80+Math.floor(i/3)*25;
   svg+=`<line x1="${lx}" x2="${lx+20}" y1="${ly-4}" y2="${ly-4}" stroke="${color}" stroke-width="2" ${dash}/><text x="${lx+26}" y="${ly}" fill="${color}" font-size="11">${escape(name)}</text>`;
  });
  // One hover area per lead shows all model/run values, including missing points.
  leads.forEach((lead,i)=>{const start=i?(x(leads[i-1])+x(lead))/2:left,end=i<leads.length-1?(x(lead)+x(leads[i+1]))/2:W-right;const text=[lead+'小时',...[...groups].map(([name,rs])=>name+'：'+number(rowValue(rs.find(r=>r['时效(h)']===lead))))].join('\n');svg+=`<rect x="${start}" y="${top}" width="${end-start}" height="${bottom-top}" fill="transparent"><title>${escape(text)}</title></rect>`;});
  const html=svg+'</svg>';by('case-plot').innerHTML=html;if(cacheable)cache.saveChart(resultKey,key,html);
 }
 function fieldTimes(){
  const coverage=result.run_coverage?.find(c=>c.run===result.selection.runs[0]);if(!coverage)return [];
  return coverage.valid_times.map(label=>{const r=result.scores.find(r=>r['有效时间']===label&&r['起报']===runLabel(coverage.run));return r?{label,utc:r['有效UTC']}:null;}).filter(Boolean);
 }
 function cnRun(run){const d=new Date(Date.UTC(+run.slice(0,4),run.slice(4,6)-1,+run.slice(6,8),+run.slice(8,10)+8));return `${d.getUTCMonth()+1}月${d.getUTCDate()}日${String(d.getUTCHours()).padStart(2,'0')}时`;}
 const runLabel=run=>{const d=new Date(`${run.slice(0,4)}-${run.slice(4,6)}-${run.slice(6,8)}T${run.slice(8,10)}:00:00Z`);d.setUTCHours(d.getUTCHours()+8);return `${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2,'0')}时`;};
 async function drawFields(){
  const times=fieldTimes();if(!times.some(t=>t.utc===valid))valid=times[0]?.utc||'';  // 默认第一个时段（用户 11-11 定）
  const bj=u=>{const d=new Date(u);d.setUTCHours(d.getUTCHours()+8);return d;},hh=d=>String(d.getUTCHours()).padStart(2,'0'),span=u=>{const e=bj(u);return [new Date(e-result.selection.hours*3600000),e];};
  const [from,to]=valid?span(valid):[null,null];
  by('case-field-caption').textContent=`${cnRun(result.selection.runs[0])}起报${from?` · 图上是 ${from.getUTCDate()}日${hh(from)}时—${to.getUTCDate()!==from.getUTCDate()?to.getUTCDate()+'日':''}${hh(to)}时这 ${result.selection.hours} 小时的雨量`:''}${result.selection.runs.length>1?' · 同时选中多个起报，落区只显示第一个':''}`;
  by('case-valid-tools').innerHTML=buttons('case-valid','时段 · 北京时间',times.map(t=>{const [a,b]=span(t.utc);return [t.utc,`${hh(a)}—${hh(b)}时`];}),valid);
  bind('case-valid',v=>{valid=v;drawFields();});
  fieldSequence++;const current=fieldSequence;fieldController?.abort();fieldData=null;
  by('case-rain-legend').innerHTML='';
  if(!valid){by('case-field-grid').innerHTML='<p class="case-empty">当前起报没有纳入评分的有效时段。</p>';return;}
  const key=JSON.stringify([meta.generation,result.selection,valid]);
  if(fieldCache.has(key)){fieldData=fieldCache.get(key);renderFields();return;}
  by('case-field-grid').innerHTML='<p class="case-empty">正在读取与评分配对的落区格点…（这个时段第一次看约需半分钟，之后即时）</p>';
  fieldController=new AbortController();
  try{
   const data=await api('fields',{...result.selection,valid},fieldController.signal);if(current!==fieldSequence)return;
   fieldData=data;if(!data.issues.length&&data.models.every(m=>m.N>0)){fieldCache.set(key,data);while(fieldCache.size>12)fieldCache.delete(fieldCache.keys().next().value);}renderFields();
  }catch(error){if(current===fieldSequence&&error.name!=='AbortError')by('case-field-grid').innerHTML=`<p class="case-empty">${escape(error.message)}</p>`;}
 }
 function renderFields(){
  if(!fieldData||!by('case-field-grid'))return;
  const data=fieldData,hours=result.selection.hours;
  const breaks=hours===2?[.1,2,5,10,20,50]:hours===6?[.1,4,13,25,60,120]:hours===12?[.1,5,15,30,70,140]:[.1,10,25,50,100,250];
  const palette=['#f2f5f7','#a6f28f','#3dba3d','#61b8ff','#1664e8','#e040e8','#85002e'];
  const fill=v=>Number.isFinite(v)?palette[breaks.filter(t=>v>=t).length]:'#d7dce0';
  const rings=data.boundary.type==='Polygon'?data.boundary.coordinates:data.boundary.type==='MultiPolygon'?data.boundary.coordinates.flat():[];
  const coords=[...data.points,...rings.flat()],xs=coords.map(p=>p[0]),ys=coords.map(p=>p[1]),west=Math.min(...xs),east=Math.max(...xs),south=Math.min(...ys),north=Math.max(...ys);
  const W=240,H=145,scale=Math.min((W-12)/((east-west)*Math.cos(22.6*Math.PI/180)),(H-12)/(north-south)),sx=scale*Math.cos(22.6*Math.PI/180),sy=scale;
  const x=v=>(W-(east-west)*sx)/2+(v-west)*sx,y=v=>(H-(north-south)*sy)/2+(north-v)*sy;
  const spacing=result.selection.spacing,w=spacing*sx,h=spacing*sy;
  const outline=rings.map(r=>'<path d="'+r.map((p,i)=>(i?'L':'M')+x(p[0]).toFixed(2)+','+y(p[1]).toFixed(2)).join(' ')+'Z" fill="none" stroke="#526a77" stroke-width="0.8"/>').join('');
  const stats=values=>{const a=values.filter(Number.isFinite);return {mean:a.length?a.reduce((a,b)=>a+b,0)/a.length:null,max:a.length?Math.max(...a):null};};
  const mm=v=>Number.isFinite(v)?v.toFixed(1):"—";
  const obs=stats(data.observations),cards=[{id:'obs',label:'实况',values:data.observations,note:`面雨量 ${mm(obs.mean)} · 最大 ${mm(obs.max)} mm`},...data.models.map(m=>{const a=stats(m.values),r=result.scores.find(r=>r['模式']===m.label&&r['起报']===runLabel(data.run)&&r['有效UTC']===data.valid&&r['阈值(mm)']===threshold);return {...m,note:`≥${threshold} mm · TS ${number(r?.TS)}<br>面雨量 ${mm(a.mean)} mm`};})];
  by('case-field-grid').innerHTML=cards.map(c=>`<figure class="case-field-card"><figcaption style="color:${c.id==='obs'?'#36566b':modelColor(c.id)}">${escape(c.label)}</figcaption><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${escape(c.label)} ${escape(data.valid_time)} ${hours}小时降水"><rect width="${W}" height="${H}" fill="#f8fafb"/>${data.points.map((p,i)=>`<rect x="${x(p[0])-w/2}" y="${y(p[1])-h/2}" width="${w}" height="${h}" fill="${fill(c.values[i])}"><title>${p[0].toFixed(3)}°E, ${p[1].toFixed(3)}°N：${number(c.values[i])} mm</title></rect>`).join('')}${outline}</svg><p>${c.note}</p></figure>`).join('');
  by('case-rain-legend').innerHTML=`<span>${hours}小时降水（mm）</span>${palette.map((c,i)=>`<span><i style="background:${c}"></i>${i===0?'＜'+breaks[0]:i===palette.length-1?'≥'+breaks.at(-1):breaks[i-1]+'～'+breaks[i]}</span>`).join('')}<span><i style="background:#d7dce0"></i>缺测</span><span>市界内等权格点；灰色为缺测或未纳入评分。</span>${data.issues.map(i=>`<span>${escape(i)}</span>`).join('')}`;
 }

 async function open(){
  if(ready){if(Date.now()-metaRead>60000)return calculate();return;}ready=true;
  host().innerHTML='<div class="case-heading"><p class="eyebrow">CASE VERIFICATION</p><h1>个例检验</h1><p>比较同一次天气过程中的模式表现。</p></div><section class="filter-card"><div id="case-controls">正在读取个例条件…</div></section><div id="case-status" class="notice" role="status"></div><div id="case-results" hidden></div>';
  const current=sequence;
  try{meta=await api('catalog');if(current!==sequence)return;metaRead=Date.now();if(!meta.cases.some(c=>c.id===state.case))state.case=meta.case;meta.runs=meta.cases.find(c=>c.id===state.case).runs;if(!state.runs.length||state.runs.some(r=>!meta.runs.some(m=>m.id===r)))state.runs=[meta.runs[0].id];useCache();refreshModels(true);drawControls();await calculate();}catch(error){if(current!==sequence)return;ready=false;by('case-controls').innerHTML='<button id="case-retry" class="quiet">重新读取个例资料</button>';by('case-status').textContent=error.message;by('case-retry').onclick=open;}
 }
 return {open,close(){cancel();ready=false;},refresh(){cancel();ready=false;result=null;return open();}};
})();
