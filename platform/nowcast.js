'use strict';
window.NowcastRain=(()=>{
 const by=id=>document.getElementById(id);
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const finite=Number.isFinite;
 const QPE_THRESHOLDS=[0.1,5,10,20,50];
 const state={metric:'ts',days:'15'};
 let initialized=false,qpeSequence=0,qpfSequence=0,qpeController,qpfController,pending=0,qpeData=null,qpfData=null;
 const group=value=>String(value).replace(/\B(?=(\d{3})+(?!\d))/g,',');
 const short=value=>value?String(value).slice(5,10):'—';
 function busy(delta){
  pending=Math.max(0,pending+delta);
  by('nowcast-workspace').setAttribute('aria-busy',pending?'true':'false');
 }
 function controls(){
  if(initialized)return;
  initialized=true;
  by('nowcast-workspace').innerHTML=`<div class="page-heading"><div class="page-heading-main"><div><h1>临近降水（深圳，1 小时雨量）</h1></div></div></div>
   <section class="filter-card overview-filters" aria-label="显示指标">
    <label>指标<select id="nowcast-metric"><option value="ts" selected>TS 评分</option><option value="hit">命中率</option><option value="far">空报率</option></select></label>
   </section>
   <section class="table-card overview-card" aria-label="雷达估测降水">
    <div class="table-heading"><div><h2>雷达估测降水（历史回算）</h2></div></div>
    <p id="nowcast-qpe-caption" class="overview-caption" aria-live="polite"></p>
    <div id="nowcast-qpe-result" class="table-scroll overview-scroll nowcast-scroll" tabindex="0" aria-label="雷达估测评分表，可左右滚动"></div>
    <div id="nowcast-qpe-notes" class="overview-notes"></div>
   </section>
   <section class="table-card overview-card" aria-label="短时预报">
    <div class="table-heading"><div><h2>短时预报（按实际能拿到的预报计）</h2></div><label class="nowcast-days">日期<select id="nowcast-days"><option value="3">近3天</option><option value="7">近7天</option><option value="15" selected>近15天</option></select></label></div>
    <p id="nowcast-qpf-caption" class="overview-caption" aria-live="polite"></p>
    <div id="nowcast-qpf-result" class="table-scroll overview-scroll nowcast-scroll" tabindex="0" aria-label="短时预报评分表，可左右滚动"></div>
    <div id="nowcast-qpf-notes" class="overview-notes"></div>
   </section>`;
  by('nowcast-metric').onchange=event=>{state.metric=event.target.value;renderQpe();renderQpf();};
  by('nowcast-days').onchange=event=>{state.days=event.target.value;loadQpf();};
 }
 function metricValue(cell){
  if(!cell)return null;
  const h=Number(cell.H)||0,m=Number(cell.M)||0,f=Number(cell.F)||0;
  if(state.metric==='ts'){const total=h+m+f;return total?h/total:null;}
  if(state.metric==='hit'){const total=h+m;return total?h/total:null;}
  const total=h+f;return total?f/total:null;
 }
 function displayValue(value){
  if(!finite(value))return null;
  return state.metric==='ts'?Number(value.toFixed(2)):Number((value*100).toFixed(1));
 }
 function cellText(value){
  const shown=displayValue(value);
  return shown===null?'—':state.metric==='ts'?shown.toFixed(2):shown.toFixed(1)+'%';
 }
 function winner(values){
  const shown=values.filter(value=>value!==null);
  if(shown.length<2||new Set(shown).size===1)return null;
  return state.metric==='far'?Math.min(...shown):Math.max(...shown);
 }
 function renderQpe(){
  const host=by('nowcast-qpe-result'),notes=by('nowcast-qpe-notes');
  const data=qpeData&&qpeData.available?qpeData.data||{}:null;
  const allMethods=data&&Array.isArray(data.methods)?data.methods.filter(method=>method):[];
  const methods=allMethods.filter(method=>method.show);
  if(!data||!methods.length){
   by('nowcast-qpe-caption').textContent='';
   host.innerHTML='<p class="overview-empty" role="status">雷达估测评分暂未生成</p>';
   notes.replaceChildren();
   return;
  }
  const thresholds=Array.isArray(data.thresholds)&&data.thresholds.length?data.thresholds:QPE_THRESHOLDS;
  const period=data.period||{};
  by('nowcast-qpe-caption').textContent=`历史回算 · ${period.start||'—'} 至 ${period.end||'—'} 的 ${period.events??'—'} 个降水过程 · ${data.clock||'整点小时'}雨量 · 实况：${data.truth||'—'} · 雷达：${data.radar||'—'}`;
  const first=methods[0].counts||{};
  const columns=thresholds.map(value=>{
   const key=String(value),cell=first[key];
   const observed=cell?Number(cell.H||0)+Number(cell.M||0):null;
   return {key,label:`≥${value}`,observed:observed===null?null:group(observed)};
  });
  const values=columns.map(column=>methods.map(method=>metricValue((method.counts||{})[column.key])));
  const best=values.map(list=>winner(list.map(displayValue)));
  host.innerHTML=`<table><thead><tr><th scope="col">方法</th>${columns.map(column=>`<th scope="col" data-column="${column.key}">${esc(column.label)}<small>实况 ${column.observed===null?'—':column.observed} 格点次</small></th>`).join('')}</tr></thead><tbody>${methods.map((method,row)=>`<tr><th scope="row">${esc(method.label)}</th>${columns.map((column,index)=>{const raw=values[index][row],shown=displayValue(raw);return `<td data-column="${column.key}">${shown!==null&&shown===best[index]?`<strong>${cellText(raw)}</strong>`:cellText(raw)}</td>`;}).join('')}</tr>`).join('')}</tbody></table>`;
  if(!allMethods.some(method=>method.fitted_on_development)){
   notes.innerHTML='<p>雷达估测：用雷达回波强度按公式换算成雨量，再和地面实况比。</p><p>雷达资料还在补下，过程数会增加；大雨档样本少，分数波动大。</p>';
   return;
  }
  notes.innerHTML=`<p>雷达估测：用雷达回波强度按公式换算成雨量，再和地面实况比。</p><p>本地拟合公式的系数是用其中 ${period.development_events??'—'} 个过程拟合的，独立检验只有 ${period.independent_events??'—'} 个过程，分数偏乐观。</p><p>雷达资料还在补下，过程数会增加；大雨档样本少，分数波动大。</p>`;
 }
 function renderQpf(){
  const host=by('nowcast-qpf-result'),notes=by('nowcast-qpf-notes');
  const methods=qpfData&&Array.isArray(qpfData.methods)?qpfData.methods:[];
  if(!qpfData||!methods.length){
   by('nowcast-qpf-caption').textContent='';
   host.innerHTML='<p class="overview-empty" role="status">所选日期内没有可用评分</p>';
   notes.replaceChildren();
   return;
  }
  const thresholds=Array.isArray(qpfData.thresholds)&&qpfData.thresholds.length?qpfData.thresholds:QPE_THRESHOLDS;
  const hours=Array.isArray(qpfData.hours)&&qpfData.hours.length?qpfData.hours:[1,2,3,4,5,6];
  const delay=methods[0].delay_hours??8;
  by('nowcast-qpf-caption').textContent=`近${qpfData.days??state.days}天（${short(qpfData.start)} 至 ${short(qpfData.end)}，北京时）· 实况：深圳格点 1 小时雨量 · 模式按当时实际能拿到的最新一次起报计（出数约晚 ${delay} 小时）`;
  const columns=thresholds.map(value=>{
   const key=String(value),observed=qpfData.observed?.[key];
   return {key,label:`≥${value}`,observed:observed===null||observed===undefined?null:group(observed)};
  });
  const best={};
  for(const hour of hours)for(const column of columns){
   best[hour+'|'+column.key]=winner(methods.map(method=>displayValue(metricValue(method.leads?.[String(hour)]?.scores?.[column.key]))));
  }
  host.innerHTML=`<table><thead><tr><th scope="col">方法</th><th scope="col">预报时效</th>${columns.map(column=>`<th scope="col" data-column="${column.key}">${esc(column.label)}<small>实况 ${column.observed===null?'—':column.observed} 格点次</small></th>`).join('')}</tr></thead><tbody>${methods.map(method=>hours.map((hour,index)=>`<tr>${index===0?`<th scope="rowgroup" rowspan="${hours.length}">${esc(method.label)}</th>`:''}<th scope="row">第${hour}小时</th>${columns.map(column=>{const raw=metricValue(method.leads?.[String(hour)]?.scores?.[column.key]),shown=displayValue(raw);return `<td data-lead="${hour}" data-column="${column.key}">${shown!==null&&shown===best[hour+'|'+column.key]?`<strong>${cellText(raw)}</strong>`:cellText(raw)}</td>`;}).join('')}</tr>`).join('')).join('')}</tbody></table>`;
  notes.innerHTML='<p>第 N 小时：发布预报之后的第 N 个小时的雨量。</p><p>模式出数据要晚几个小时，所以这里用的不是起报后第 N 小时，而是发布时刻手里最新那次起报对应的时次。</p><p>雷达外推（0～2 小时）正在开发，做好后加入本表。</p>';
 }
 async function loadQpe(){
  const current=++qpeSequence;
  qpeController?.abort();qpeController=new AbortController();
  busy(1);
  by('nowcast-qpe-caption').textContent='正在读取雷达估测评分…';
  try{
   const response=await fetch('./api/nowcast/qpe',{cache:'no-store',signal:qpeController.signal});
   const data=await response.json();
   if(!response.ok)throw Error(data.error||'雷达估测评分暂未生成');
   if(current!==qpeSequence)return;
   qpeData=data&&data.available?data:{available:false};
   renderQpe();
  }catch(error){
   if(current!==qpeSequence||error.name==='AbortError')return;
   qpeData={available:false};renderQpe();
  }finally{busy(-1);}
 }
 async function loadQpf(){
  const current=++qpfSequence;
  qpfController?.abort();qpfController=new AbortController();
  busy(1);
  by('nowcast-qpf-caption').textContent='正在读取短时预报评分…';
  try{
   const response=await fetch('./api/nowcast/qpf?'+new URLSearchParams({days:state.days}),{cache:'no-store',signal:qpfController.signal});
   const data=await response.json();
   if(!response.ok)throw Error(data.error||'所选日期内没有可用评分');
   if(current===qpfSequence){qpfData=data;renderQpf();}
  }catch(error){
   if(current===qpfSequence&&error.name!=='AbortError'){
    qpfData=null;
    by('nowcast-qpf-caption').textContent='短时预报评分暂不可用';
    by('nowcast-qpf-result').textContent=error.message;
    by('nowcast-qpf-notes').replaceChildren();
   }
  }finally{busy(-1);}
 }
 function open(){
  controls();
  return Promise.all([loadQpe(),loadQpf()]);
 }
 return {open,refresh:open,close(){qpeSequence++;qpfSequence++;qpeController?.abort();qpfController?.abort();}};
})();
