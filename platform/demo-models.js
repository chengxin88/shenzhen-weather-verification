'use strict';
window.DemoModels=(()=>{
 const WN3='Google WeatherNext 3（集合均值）',colors=['#397c91','#956fc0','#c57c48','#5f9694','#a16884','#79994d','#568bc2','#b79b4a','#7e789e','#bd735b','#529f87','#89929b'];
 let meta=null,pending=null,loadError=false,currentModule='grid',caseDemo=false,monthlyDemo=false,sample=null,caseSequence=0;
 const by=id=>document.getElementById(id),esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const profiles=()=>meta?.profiles||[],profile=id=>profiles().find(p=>p.id===id),label=id=>profile(id)?profile(id).label+' · WN3演示':id;
 const color=id=>colors[Math.max(0,profiles().findIndex(p=>p.id===id))%colors.length];
 const request=async path=>{const r=await fetch('./api/'+path,{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error('演示资料暂时无法读取');return r.json();};
 async function ready(){
  if(meta)return meta;if(!pending)pending=request('wn3-module-demo/catalog').then(value=>{
   if(value.schema!==1||!Array.isArray(value.profiles))throw Error('演示目录格式异常');meta=value;loadError=false;return value;
  }).catch(()=>{loadError=true;return null;}).finally(()=>{pending=null;});return pending;
 }
 function toolbar(module){
  const box=by('module-demo-mode');if(!box)return;box.hidden=module==='interfaces';if(box.hidden)return;
  const demo=module==='case'?caseDemo:monthlyDemo;
  const switchable=['case','monthly'].includes(module);
  box.innerHTML=`${switchable?`<div class="choice-buttons" role="group" aria-label="${module==='case'?'个例':'月降水'}资料通道"><button type="button" data-demo-mode="formal" aria-pressed="${!demo}">业务资料</button><button type="button" data-demo-mode="demo" aria-pressed="${demo}" ${loadError?'disabled':''}>WN3 演示资料</button></div>`:''}<p class="filter-note" id="module-demo-note">${loadError?'演示目录暂不可用，业务资料仍可正常查看。':module==='typhoon'?esc(meta?.unavailable.typhoon||'WN3尚无台风路径'):switchable?'新模式使用 WN3 演示资料；保留 WN3 实际起报、有效时间和网格，不计入正式模式排名。':'模式选项已增加 '+profiles().length+' 个 WN3 演示入口；点击对应模式即可查看同一来源的演示评分，不计入正式排名。'}</p>`;
  box.querySelectorAll('[data-demo-mode]').forEach(button=>button.onclick=()=>{
   const active=button.dataset.demoMode==='demo';
   if(module==='case'){
    caseDemo=active;window.NativeCase.close();toolbar(module);
    if(active)renderCase();else{caseSequence++;window.NativeCase.open();}
   }else{
    monthlyDemo=active;toolbar(module);window.MonthlyPrecip.setDemo(active);
   }
  });
 }
 function gridNotice(kind,models){
  if(currentModule!=='grid'||by('grid-workspace').hidden)return;
  const note=by('module-demo-note');if(!note)return;
  note.textContent=kind==='环流'?meta?.unavailable.circulation||'WN3暂无高空场':models.some(profile)?'当前图表含 WN3 演示资料：所选新模式共用 WN3 真实评分，并非各模式自身预报，不计入正式排名。':'模式选项中新增 '+profiles().length+' 个 WN3 演示入口；点击对应模式查看。演示入口只使用本要素已有的 WN3 实况配对。';
 }
 async function openModule(module){
  currentModule=module;
  if(module==='quality'){by('module-demo-mode').hidden=true;window.DataQuality.open();return;}
  await ready();if(currentModule!==module)return;
  toolbar(module);
  if(module==='case'){if(caseDemo)renderCase();else window.NativeCase.open();}
  else if(module==='monthly')window.MonthlyPrecip.open();
  else if(module==='typhoon')window.NativeTyphoon.open();
  else if(module==='interfaces')window.ModelInterfaces.open();
  else window.GridVerification.open();
 }
 function expandMonth(data){
  if(data.is_demo!==true||data.source_model!==WN3||data.excluded_from_official_scores!==true)throw Error('演示身份不匹配');
  const aliases=profiles();return {...data,models:aliases.map(p=>({id:p.id,slug:p.target_product_id,is_demo:true,source_model:WN3})),
   windows:data.windows.map(w=>({...w,models:Object.fromEntries(aliases.map(p=>[p.id,{...w.models[WN3],is_demo:true,source_model:WN3,excluded_from_official_scores:true,target_product_id:p.target_product_id}]))}))};
 }
 let caseModels=null,caseField='temperature_2m',caseMetric='RMSE',caseThreshold=.1;
 async function renderCase(){
  const sequence=++caseSequence,host=by('case-workspace');
  host.innerHTML='<div class="notice" role="status">正在读取 WN3 个例演示…</div>';
  try{
   if(!sample)sample=await request('model-interfaces/demo/detail');
   if(sequence!==caseSequence||currentModule!=='case'||!caseDemo)return;
   if(sample.is_demo!==true||sample.excluded_from_official_scores!==true)throw Error('演示身份不匹配');
   if(!caseModels)caseModels=new Set([profiles()[0]?.id].filter(Boolean));
   drawCase();
  }catch(error){if(sequence===caseSequence)host.innerHTML='<p class="notice error">WN3 个例演示暂不可用，请切回业务资料或刷新重试。</p>';}
 }
 function fmt(v,d=3){return Number.isFinite(v)?v.toFixed(d):'—';}
 function stamp(v){return v.slice(0,16).replace('T',' ');}
 function caseRows(){
  const rain=caseField==='precipitation';
  const base=rain?sample.scores.precipitation.find(s=>s.threshold===caseThreshold):sample.scores.temperature_2m;
  const values=rain?{...base,...window.VerificationCore.score([base])}:base;
  return profiles().filter(p=>caseModels.has(p.id)).map(p=>({...values,model:p.id,is_demo:true,source_model:WN3,
   target_product_id:p.target_product_id,excluded_from_official_scores:true,run_bjt:sample.run_bjt,valid_bjt:sample.valid_bjt,
   lead_minutes:sample.lead_minutes,variable:caseField,accumulation_minutes:rain?180:null}));
 }
 function drawCase(){
  const host=by('case-workspace'),rain=caseField==='precipitation',metrics=rain?['TS','ETS','POD','FAR','BIAS']:['RMSE','ME','MAE','CC'];
  if(!metrics.includes(caseMetric))caseMetric=metrics[0];const rows=caseRows(),rainStart=new Date(Date.parse(sample.valid_utc)-180*60000).toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'}).slice(0,16);
  host.innerHTML=`<div class="page-heading"><div><h1>WN3 个例演示 · ${esc(sample.valid_bjt.slice(0,10))}</h1><p>起报 ${stamp(sample.run_bjt)} · 有效 ${stamp(sample.valid_bjt)} · +${sample.lead_minutes/60} 小时（北京时间）</p></div></div>
   <section class="filter-card"><div class="choice-buttons" role="group" aria-label="演示个例要素">${[['temperature_2m','2米气温'],['precipitation','3小时累计降水']].map(([id,name])=>`<button data-case-field="${id}" aria-pressed="${id===caseField}">${name}</button>`).join('')}<button disabled title="本样例未准备对应实况配对">10米风速 · 待配对</button><button disabled title="WN3未接入等压面高空场">等压面 · 无WN3场</button></div>
   <div class="model-row"><span>演示模式</span><div class="model-chips">${profiles().map(p=>`<button class="model-chip" data-case-demo-model="${esc(p.id)}" aria-pressed="${caseModels.has(p.id)}"><i style="background:${color(p.id)}"></i>${esc(label(p.id))}</button>`).join('')}</div></div>
   <p class="filter-note">共用 WN3 原生 0.1° 集合均值和深圳实况。当前日期是缓存真实日期，与业务通道的7月25日个例分开；不会用当前资料填充历史日期。</p></section>
   <div class="notice">WN3 演示资料 · 各目标模式使用相同来源，不代表各模式自身能力，不计入正式排名。${rain?'降水窗口 '+stamp(sample.valid_bjt).slice(0,11)+'04:00 ～ '+stamp(sample.valid_bjt)+' 北京时间（3小时）。':''}</div>
   <section class="results"><div class="result-heading"><div><h2>WN3 演示评分</h2><p>深圳格点实况 · ${rain?'3小时累计降水':'2米气温'} · ${sample.fields.find(f=>f.variable===caseField).paired_points} 个有效配对</p></div></div>
   <div class="choice-buttons" role="group" aria-label="演示评分指标">${metrics.map(m=>`<button data-case-demo-metric="${m}" aria-pressed="${m===caseMetric}">${m}</button>`).join('')}</div>
   ${rain?`<div class="choice-buttons" role="group" aria-label="演示降水阈值">${sample.scores.precipitation.map(s=>`<button data-case-demo-threshold="${s.threshold}" aria-pressed="${s.threshold===caseThreshold}">≥${s.threshold} mm</button>`).join('')}</div>`:''}
   <div class="case-plot" id="demo-case-plot" role="region" aria-label="WN3演示个例图表">${caseChart(rows)}</div></section>
   <section class="table-card"><div class="table-heading"><h2>WN3 演示评分明细</h2><button id="demo-case-export" class="quiet" ${rows.length?'':'disabled'}>导出演示 CSV ↓</button></div><div class="table-scroll"><table><thead><tr><th>模式（WN3演示）</th><th>配对 N</th>${metrics.map(m=>'<th>'+m+'</th>').join('')}</tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(label(r.model))}</td><td>${r.n}</td>${metrics.map(m=>'<td>'+fmt(r[m])+'</td>').join('')}</tr>`).join('')}</tbody></table></div></section>`;
  host.querySelectorAll('[data-case-demo-model]').forEach(b=>b.onclick=()=>{caseModels.has(b.dataset.caseDemoModel)?caseModels.delete(b.dataset.caseDemoModel):caseModels.add(b.dataset.caseDemoModel);drawCase();});
  host.querySelectorAll('[data-case-field]').forEach(b=>b.onclick=()=>{caseField=b.dataset.caseField;drawCase();});
  host.querySelectorAll('[data-case-demo-metric]').forEach(b=>b.onclick=()=>{caseMetric=b.dataset.caseDemoMetric;drawCase();});
  host.querySelectorAll('[data-case-demo-threshold]').forEach(b=>b.onclick=()=>{caseThreshold=Number(b.dataset.caseDemoThreshold);drawCase();});
  by('demo-case-export').onclick=()=>{
   const columns=[['model','演示目标模式'],['source_model','实际来源'],['is_demo','是否演示'],['excluded_from_official_scores','排除正式排名'],['run_bjt','起报BJT'],['valid_bjt','有效BJT'],['lead_minutes','时效分钟'],['accumulation_minutes','累计分钟'],['variable','要素'],['n','配对N'],...metrics.map(m=>[m,m])];
   const blob=new Blob([window.VerificationCore.csv(rows.map(r=>({...r,model:label(r.model)})),columns)],{type:'text/csv;charset=utf-8'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='WN3演示_个例_'+sample.sample_id+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);
  };
 }
 function caseChart(rows){
  const values=rows.map(r=>r[caseMetric]).filter(Number.isFinite);if(!values.length)return '<p class="empty">当前指标没有有效分母，显示“—”；缺测不计零分。</p>';
  let low=Math.min(0,...values),high=Math.max(0,...values);if(high===low)high=low+1;const width=950,height=Math.max(220,rows.length*40+70),left=330,span=550,x=v=>left+(v-low)/(high-low)*span;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="WN3演示 ${caseMetric}"><text x="${left}" y="20">WN3 演示 · ${caseMetric}</text>${rows.map((r,i)=>{const value=r[caseMetric],y=50+i*40;return `<text x="${left-10}" y="${y+5}" text-anchor="end" font-size="12">${esc(label(r.model))}</text>${Number.isFinite(value)?`<rect x="${Math.min(x(0),x(value))}" y="${y-10}" width="${Math.max(1,Math.abs(x(value)-x(0)))}" height="21" fill="${color(r.model)}"/><text x="${x(value)+5}" y="${y+5}" font-size="12">${fmt(value)}</text>`:''}`;}).join('')}</svg>`;
 }
 return {ready,profiles,profile,label,color,gridNotice,openModule,expandMonth,groups:()=>meta?.groups||{},
  closeCase(){caseSequence++;},refreshCase(){sample=null;return openModule('case');},monthlyRequest:path=>request('wn3-module-demo/monthly/'+path),
  placeholders:()=>profiles().map(p=>({...p,color:color(p.id),disabled:true}))};
})();
