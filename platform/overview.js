'use strict';
window.AssessmentOverview=(()=>{
 const by=id=>document.getElementById(id);
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const state={region:'all',element:'rain',day:'1',days:'7'};
 const regions={all:'南方地区',hn:'华南地区',hun:'湖南地区',gba:'粤港澳大湾区',hk:'香港',sz:'深圳',xj:'新疆巴里坤'};
 let initialized=false,sequence=0,controller;
 const finite=Number.isFinite;
 function detailLink(){
  return '#grid&'+new URLSearchParams({ref:'station',element:state.element,day:state.day,days:state.days});
 }
 function controls(){
  if(initialized)return;
  initialized=true;
  by('overview-workspace').innerHTML=`<div class="page-heading"><h1>考核总览</h1></div>
   <section class="filter-card overview-filters" aria-label="考核条件">
    <label>区域<select id="overview-region">${Object.entries(regions).map(([id,name])=>`<option value="${id}">${name}</option>`).join('')}</select></label>
    <label>要素<select id="overview-element"><option value="rain">降水</option><option value="t">气温</option></select></label>
    <label>时效<select id="overview-day"><option value="1">24h</option><option value="2">48h</option><option value="3">72h</option></select></label>
    <label>日期<select id="overview-days"><option value="3">近3天</option><option value="7" selected>近7天</option><option value="15">近15天</option></select></label>
   </section><section class="table-card overview-card" aria-label="考核评分">
    <p id="overview-caption" class="overview-caption" aria-live="polite"></p>
    <div id="overview-result" class="table-scroll overview-scroll" tabindex="0" aria-label="考核评分表，可左右滚动"></div>
    <div id="overview-notes" class="overview-notes"></div>
    <div class="overview-footer"><a id="overview-detail" href="${detailLink()}">查看详细分析 →</a></div>
   </section>`;
  for(const key of ['region','element','day','days'])by('overview-'+key).onchange=e=>{state[key]=e.target.value;load();};
 }
 function render(data){
  const rain=state.element==='rain';
  const columns=rain?[{key:'PC',label:'晴雨准确率',percent:true},
   ...(data.grades||[]).map(g=>({key:'grade'+g.id,label:g.label+'TS',grade:g.id,observed:g.observed}))]:
   [{key:'ACC2',label:'≤2℃准确率',percent:true},{key:'ACC1',label:'≤1℃准确率',percent:true},{key:'MAE',label:'平均绝对误差（℃）',min:true}];
  const rows=data.rows||[];
  const value=(row,col)=>col.grade?row.grades?.find(g=>g.grade===col.grade)?.TS:row[col.key];
  const shown=(v,col)=>Math.round(v*(col.percent?1000:100));
  const best=columns.map(col=>{const vals=rows.map(row=>value(row,col)).filter(finite).map(v=>shown(v,col));return new Set(vals).size>1?(col.min?Math.min(...vals):Math.max(...vals)):null;});
  const caption=data.start&&data.end?`近${state.days}天（${data.start.slice(5,10)} 至 ${data.end.slice(5,10)}，北京时）`:`近${state.days}天`;
  by('overview-caption').textContent=`${rain?'降水':'气温'} · ${caption} · 第${state.day}天 ${rain?'24小时雨量':`（预报 ${(+state.day-1)*24}–${+state.day*24} 小时各时次）`} · ${regions[state.region]} ${data.station_count||0} 个站`;
  if(data.data_end_reason==='nmic'&&data.end)by('overview-caption').textContent+=` · 国家站资料到 ${Number(data.end.slice(5,7))} 月 ${Number(data.end.slice(8,10))} 日，窗口截止到这一天（北京时间）。`;
  by('overview-result').innerHTML=rows.length?`<table><thead><tr><th scope="col">模式</th>${columns.map(col=>`<th scope="col">${esc(col.label)}${col.grade?`<small>实况 ${esc(col.observed)} 站次</small>`:''}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr><th scope="row">${esc(row.label)}</th>${columns.map((col,i)=>{const v=value(row,col),text=finite(v)?col.percent?(v*100).toFixed(1)+'%':v.toFixed(2):'—';return `<td data-column="${col.key}">${finite(v)&&shown(v,col)===best[i]?`<strong>${text}</strong>`:text}</td>`;}).join('')}</tr>`).join('')}</tbody></table>`:'<p class="overview-empty" role="status">当前窗口暂无有效配对，评分资料准备好后可在此查看。</p>';
  by('overview-notes').innerHTML=rain?
   '<p>晴雨准确率：报对有雨/无雨的比例；实况只有微量时，报有雨也算对（规范规定）。</p><p>TS：报对的站次 ÷（报对 + 空报 + 漏报），越接近 1 越好；24小时雨量：小雨 0.1–&lt;10、中雨 10–&lt;25、大雨 25–&lt;50、暴雨 50–&lt;100、大暴雨 100–&lt;250、特大暴雨 ≥250 mm。</p><p>— 表示该级无报对、空报或漏报；只报了而实况没出现显示 0.00。各模式按自身有效配对评分，粗体为本列最佳（含并列）。</p>':
   '<p>气温准确率：预报误差不超过 2℃（1℃）的站次比例；合并当天所有时效的计数后计算。</p><p>平均绝对误差：每次预报与实况的温差取绝对值后求平均，越小越好。</p><p>各模式按自身有效配对评分，粗体为本列最佳（含并列）。</p>';
 }
 async function load(){
  controls();
  const current=++sequence;controller?.abort();controller=new AbortController();
  by('overview-detail').href=detailLink();
  by('overview-workspace').setAttribute('aria-busy','true');
  by('overview-caption').textContent='正在读取考核结果…';
  by('overview-result').replaceChildren();by('overview-notes').replaceChildren();
  try{
   const response=await fetch('./api/stations/overview?'+new URLSearchParams(state),{cache:'no-store',signal:controller.signal});
   const data=await response.json();
   if(!response.ok)throw Error(data.error||'考核结果暂时无法读取。');
   if(current===sequence)render(data);
  }catch(error){if(current===sequence&&error.name!=='AbortError'){by('overview-caption').textContent='考核结果暂不可用';by('overview-result').textContent=error.message;}}
  finally{if(current===sequence)by('overview-workspace').setAttribute('aria-busy','false');}
 }
 return {open:load,refresh:load,close(){sequence++;controller?.abort();}};
})();
