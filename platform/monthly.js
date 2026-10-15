'use strict';
window.MonthlyPrecip=(()=>{
 const by=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const fin=v=>typeof v==='number'&&Number.isFinite(v),fmt=(v,d=1)=>fin(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:d,minimumFractionDigits:0}):'—';
 const palette={'实况':'#1f2d3a','ECMWF-IFS':'#0072b2','ECAI':'#009e73','ECMWF-ENSEMBLES平均':'#56b4e9','NCEP-GFS':'#d55e00','GFSAI':'#cc79a7','GEFS集合平均':'#e69f00'};
 const names={'ECMWF-IFS':'EC','ECAI':'ECAI','ECMWF-ENSEMBLES平均':'EC 集合平均','NCEP-GFS':'GFS','GFSAI':'GFSAI','GEFS集合平均':'GFS 集合平均','Google WeatherNext 2':'WN2 集合平均'};
 const ids={'ECMWF-IFS':'IFS','ECAI':'AIFS','ECMWF-ENSEMBLES平均':'ENS','GFSAI':'AIGFS','GEFS集合平均':'GEFS'};
 const label=m=>window.DemoModels.profile(m)?window.DemoModels.label(m):names[m]?(ids[m]?`${names[m]}（${ids[m]}）`:names[m]):m;
 const RAIN=[[0,'#ffffff'],[10,'#d9f0d3'],[25,'#a6dba0'],[50,'#5aae61'],[100,'#61a5d6'],[150,'#2c6fb5'],[200,'#f2c94c'],[300,'#ee8a3c'],[400,'#d7301f'],[600,'#8b1a59']];
 const BIAS=[[-1e9,'#2c5aa0'],[-100,'#5b8fd1'],[-50,'#9cc3ea'],[-25,'#d6e6f6'],[-10,'#f7f7f7'],[10,'#f8dcc7'],[25,'#f2a77a'],[50,'#de6a43'],[100,'#b2242b']];
 let catalog=null,data=null,state={month:null,window:'d1',threshold:'25',models:null},opened=false,seq=0;
 let demoMode=false,formalState=null,demoState=null;
 const host=()=>by('monthly-workspace');
 function status(text,error=false){const s=by('monthly-status');if(!s)return;s.hidden=!text;s.textContent=text||'';s.classList.toggle('error',error);}
 async function api(path){
  if(demoMode)return window.DemoModels.monthlyRequest(path);
  const r=await fetch('./api/monthly/'+path,{cache:'no-store'});let v;
  try{v=await r.json();}catch{throw Error(r.redirected||r.status===401?'登录已过期，请刷新后重新登录。':`服务返回异常（HTTP ${r.status}），请稍后重试。`);}
  if(!r.ok)throw Error(v.error||'月降水检验资料暂不可用。');return v;
 }
 function shell(){host().innerHTML=`<div class="page-heading"><div><p class="eyebrow">MONTHLY PRECIPITATION</p><h1>月降水检验</h1><p>逐日24小时降水（北京时间前一日20时至当日20时），对比模式与深圳开放数据格点实况。</p></div></div>
  <section class="filter-card" id="monthly-controls" aria-label="月降水检验条件"></section><div class="notice" id="monthly-status" role="status">正在读取月份目录…</div><div id="monthly-results" hidden></div>`;}
 function controls(){
  const w=data?.windows||[],ths=data?.thresholds||[],models=(data?.models||[]).map(m=>m.id);
  by('monthly-controls').innerHTML=`<div class="primary-filters monthly-filters">
   <label class="filter-field"><span>检验月份</span><select id="monthly-month">${(catalog?.months||[]).map(m=>`<option value="${esc(m.month)}" ${m.month===state.month?'selected':''}>${esc(m.month.replace('-','年'))}月</option>`).join('')}</select></label>
   <div class="filter-field"><span>起报时次 · 时效</span><div class="choice-buttons" role="group" aria-label="起报时次">${w.map(x=>`<button type="button" data-window="${esc(x.id)}" aria-pressed="${x.id===state.window}">${esc(x.label)}</button>`).join('')}</div></div>
   <div class="filter-field"><span>评分阈值 · 24小时 mm</span><div class="choice-buttons" role="group" aria-label="降水阈值">${ths.map(t=>`<button type="button" data-threshold="${t.value}" aria-pressed="${String(t.value)===state.threshold}">${esc(t.label)}</button>`).join('')}</div></div></div>
   <div class="model-row"><span>比较模式</span><div class="model-chips">${models.map(m=>`<button type="button" class="model-chip" data-model="${esc(m)}" aria-pressed="${state.models.includes(m)}"><i style="background:${palette[m]||'#777'}"></i>${esc(label(m))}</button>`).join('')}${demoMode?'':window.DemoModels.profiles().map(p=>`<button class="model-chip" data-model="${esc(p.id)}" disabled title="请切换至WN3演示资料，查看缓存实际月份">${esc(window.DemoModels.label(p.id))}</button>`).join('')}</div></div>
   <p class="filter-note">${demoMode?'WN3演示资料：原生0.1°集合均值，连续24个完整小时段相加；所有目标模式共用这批资料，不计入正式排名。仅有缓存覆盖的日期参与，缺测日不填零；不同起报窗口的有效天数可能不同。':'模式为本地下载的0.25°历史预报（最近邻对应到0.01°实况格点），仅评深圳行政区内格点；缺资料的日子不补、不插值。'}</p>`;
  by('monthly-month').onchange=e=>loadMonth(e.target.value);
  by('monthly-controls').querySelectorAll('[data-window]').forEach(b=>b.onclick=()=>{state.window=b.dataset.window;controls();render();});
  by('monthly-controls').querySelectorAll('[data-threshold]').forEach(b=>b.onclick=()=>{state.threshold=b.dataset.threshold;controls();render();});
  by('monthly-controls').querySelectorAll('[data-model]').forEach(b=>b.onclick=()=>{const m=b.dataset.model;state.models=state.models.includes(m)?state.models.filter(x=>x!==m):models.filter(x=>state.models.includes(x)||x===m);controls();render();});
 }
 async function loadCatalog(){
  const my=++seq;status('正在读取月份目录…');
  try{const loadedCatalog=await api('catalog');if(my!==seq)return;catalog=loadedCatalog;
   if(!catalog.months.length){by('monthly-controls').innerHTML='';status('暂无月降水检验结果。');return;}
   await loadMonth(state.month&&catalog.months.some(m=>m.month===state.month)?state.month:catalog.months[0].month);
  }catch(e){if(my===seq)status(e.message,true);}
 }
 async function loadMonth(month){
  const my=++seq;state.month=month;status(`正在读取 ${month} 检验结果…`);by('monthly-results').hidden=true;
  try{const loadedMonth=await api('data?month='+encodeURIComponent(month));if(my!==seq)return;data=loadedMonth;
   if(demoMode){data=window.DemoModels.expandMonth(data);for(const p of window.DemoModels.profiles()){names[p.id]=window.DemoModels.label(p.id);palette[p.id]=window.DemoModels.color(p.id);}}
   if(!state.models)state.models=demoMode?data.models.slice(0,1).map(m=>m.id):data.models.map(m=>m.id);
   if(!data.windows.some(w=>w.id===state.window))state.window=data.windows[0].id;
   status('');controls();render();
  }catch(e){if(my===seq)status(e.message,true);}
 }
 const win=()=>data.windows.find(w=>w.id===state.window);
 const observationFor=w=>data.is_demo&&w.paired_obs?w.paired_obs:data.obs;
 const colorOf=(v,scale)=>{let c=scale[0][1];for(const [t,col] of scale)if(v>=t)c=col;return c;};
 function drawMap(canvas,values,scale){
  const {nx,ny}=data.grid,s=4;canvas.width=nx*s;canvas.height=ny*s;const ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);
  for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){const v=values[j*nx+i];if(v==null)continue;ctx.fillStyle=colorOf(v,scale);ctx.fillRect(i*s,(ny-1-j)*s,s,s);}
 }
 function legend(scale,unit,bias=false){return `<div class="monthly-scale">${scale.map(([t,c],k)=>`<span><i style="background:${c}"></i>${bias?(k===0?'<'+scale[1][0]:(k===scale.length-1?'≥'+t:t+'~'+scale[k+1][0])):(k===scale.length-1?'≥'+t:t+'~'+scale[k+1][0])}</span>`).join('')}<em>${unit}</em></div>`;}
 function stats(w){
  const obs=observationFor(w),models=state.models.filter(m=>w.models[m]),th=state.threshold;
  const best=demoMode?null:models.map(m=>[m,w.models[m].month_scores[th]?.TS]).filter(x=>fin(x[1])).sort((a,b)=>b[1]-a[1])[0];
  return `<section class="stats monthly-stats" aria-label="样本概况">
   <article class="stat"><span class="stat-label">实况面雨量（月）</span><strong class="stat-value">${fmt(obs.mean_total)}<small>mm</small></strong><p class="stat-note">深圳格点平均，逐日相加 · 实况 ${obs.days} 天</p></article>
   ${models.slice(0,2).map(m=>`<article class="stat"><span class="stat-label">${esc(names[m]||m)} 面雨量（月）</span><strong class="stat-value">${fmt(w.models[m].mean_total)}<small>mm</small></strong><p class="stat-note">同期实况 ${fmt(w.models[m].obs_mean_total)} mm · 有效 ${w.models[m].days} 天</p></article>`).join('')}
   <article class="stat"><span class="stat-label">${demoMode?'WN3 演示有效天数':esc(data.thresholds.find(t=>String(t.value)===th)?.label||'')+' 最高TS'}</span><strong class="stat-value">${demoMode?models.length?w.models[models[0]].days:'—':best?fmt(best[1],3):'—'}<small>${demoMode?'天':best?esc(names[best[0]]||best[0]):''}</small></strong><p class="stat-note">${demoMode?'资料覆盖未满整月；演示不参与模式排名':'按格点×日累计的整月列联表'}</p></article></section>`;
 }
 function dailyChart(w){
  const days=observationFor(w).daily.map(d=>d.day),models=state.models.filter(m=>w.models[m]);
  const series=[['实况',observationFor(w).daily.map(d=>d.mean)],...models.map(m=>[m,w.models[m].daily.map(d=>d.mean)])];
  const all=series.flatMap(s=>s[1]).filter(fin),max=Math.max(5,...all)*1.1;
  const W=1100,H=300,L=52,R=16,T=18,B=42,pw=W-L-R,ph=H-T-B,x=i=>L+(i+.5)/days.length*pw,y=v=>T+(1-v/max)*ph,bw=pw/days.length*.62;
  let s=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="逐日面雨量" style="font-family:Segoe UI,Microsoft YaHei,sans-serif"><rect width="${W}" height="${H}" fill="white"/>`;
  for(let k=0;k<=4;k++){const v=max*k/4;s+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="#e3eaef"/><text x="${L-8}" y="${y(v)+4}" text-anchor="end" font-size="11" fill="#637a89">${fmt(v,0)}</text>`;}
  days.forEach((d,i)=>{if(i%2===0||i===days.length-1)s+=`<text x="${x(i)}" y="${H-24}" text-anchor="middle" font-size="11" fill="#637a89">${+d.slice(8)}日</text>`;});
  s+=`<text x="14" y="${T+ph/2}" transform="rotate(-90 14 ${T+ph/2})" text-anchor="middle" font-size="11" fill="#637a89">面雨量 mm</text>`;
  observationFor(w).daily.forEach((d,i)=>{if(fin(d.mean))s+=`<rect x="${x(i)-bw/2}" y="${y(d.mean)}" width="${bw}" height="${T+ph-y(d.mean)}" fill="#c9d6df"><title>${d.day} 实况 ${fmt(d.mean,2)} mm（格点最大 ${fmt(d.max)} mm）</title></rect>`;});
  for(const [m,vals] of series.slice(1)){let seg=[];const segs=[];vals.forEach((v,i)=>{if(fin(v))seg.push(`${x(i)},${y(v)}`);else if(seg.length){segs.push(seg);seg=[];}});if(seg.length)segs.push(seg);
   for(const g of segs)s+=`<polyline points="${g.join(' ')}" fill="none" stroke="${palette[m]}" stroke-width="2"/>`;
   vals.forEach((v,i)=>{if(fin(v))s+=`<circle cx="${x(i)}" cy="${y(v)}" r="3.2" fill="${palette[m]}"><title>${days[i]} ${esc(label(m))} ${fmt(v,2)} mm · 起报 ${esc(w.models[m].daily[i].run_bjt||'')}</title></circle>`;});}
  return s+'</svg>';
 }
 function scoreTable(w){
  const models=state.models.filter(m=>w.models[m]);
  const rows=data.thresholds.map(t=>models.map(m=>({m,t,c:w.models[m].month_scores[String(t.value)]}))).flat();
  return `<table><thead><tr><th>阈值</th><th>模式</th><th>有效天数</th><th>TS</th><th>ETS</th><th>命中率POD</th><th>空报率FAR</th><th>频率偏差BIAS</th><th>命中H</th><th>漏报M</th><th>空报F</th></tr></thead><tbody>${rows.map(r=>`<tr class="${String(r.t.value)===state.threshold?'chosen':''}"><td>${esc(r.t.label)}</td><td><i class="dot" style="background:${palette[r.m]}"></i>${esc(label(r.m))}</td><td>${w.models[r.m].days}</td><td>${fmt(r.c.TS,3)}</td><td>${fmt(r.c.ETS,3)}</td><td>${fmt(r.c.POD,3)}</td><td>${fmt(r.c.FAR,3)}</td><td>${fmt(r.c.BIAS,2)}</td><td>${r.c.H}</td><td>${r.c.M}</td><td>${r.c.F}</td></tr>`).join('')}</tbody></table>`;
 }
 function dailyTable(w){
  const models=state.models.filter(m=>w.models[m]),th=state.threshold;
  return `<table><thead><tr><th>日期（20–20时）</th><th>实况面雨量</th><th>实况格点最大</th>${models.map(m=>`<th>${esc(names[m]||m)} 面雨量</th><th>${esc(names[m]||m)} TS</th>`).join('')}</tr></thead><tbody>${observationFor(w).daily.map((d,i)=>`<tr><td>${d.day}</td><td>${fmt(d.mean,2)}</td><td>${fmt(d.max)}</td>${models.map(m=>{const x=w.models[m].daily[i];const sc=x.scores?.[th];return `<td>${fmt(x.mean,2)}</td><td>${sc&&(sc.H+sc.M+sc.F)?fmt(sc.TS,3):(sc?'无降水':'—')}</td>`;}).join('')}</tr>`).join('')}</tbody></table>`;
 }
 function render(){
  if(!data)return;const w=win(),models=state.models.filter(m=>w.models[m]);
  const missing=models.flatMap(m=>w.models[m].missing.map(x=>({...x,m})));
  by('monthly-results').hidden=false;
  by('monthly-results').innerHTML=`${stats(w)}
   <section class="results monthly-block"><div class="result-heading"><div><h2>${demoMode?'已有日期累计降水分布':'月累计降水分布'} · ${esc(data.month)}</h2><p>${esc(w.label)} · 各模式只累计自身有资料的日子；偏差 = 模式 − 同期实况</p></div></div>
    <div class="monthly-maps"><figure><canvas data-map="obs"></canvas><figcaption>实况</figcaption></figure>${models.map(m=>`<figure><canvas data-map="t:${esc(m)}"></canvas><figcaption>${esc(label(m))}</figcaption></figure>`).join('')}</div>${legend(RAIN,'mm')}
    <div class="monthly-maps">${models.map(m=>`<figure><canvas data-map="b:${esc(m)}"></canvas><figcaption>${esc(names[m]||m)} 偏差</figcaption></figure>`).join('')}</div>${legend(BIAS,'mm（负值偏少，正值偏多）',true)}</section>
   <section class="results monthly-block"><div class="result-heading"><div><h2>逐日面雨量</h2><p>灰柱为实况，折线为模式；悬停查看起报时刻。断线表示该日资料缺失。</p></div><button class="quiet" id="monthly-svg">导出图表 ↓</button></div>
    <div class="legend"><span><i style="background:#c9d6df"></i>实况</span>${models.map(m=>`<span><i style="background:${palette[m]}"></i>${esc(label(m))}</span>`).join('')}</div><div class="chart" id="monthly-daily">${dailyChart(w)}</div></section>
   <section class="table-card"><div class="table-heading"><div><h2>${demoMode?'缓存有效日期评分':'整月评分'}</h2><p>24小时降水，按格点×日累计列联表；高亮行为当前选择的阈值</p></div><button class="quiet" id="monthly-csv">导出 CSV ↓</button></div><div class="table-scroll">${scoreTable(w)}</div></section>
   <section class="table-card"><div class="table-heading"><div><h2>逐日明细</h2><p>TS 为当前阈值；当天实况与预报均无达标格点时记“无降水”</p></div></div><div class="table-scroll">${dailyTable(w)}</div></section>
   <details class="case-detail" ${missing.length||data.obs_missing.length?'':'hidden'}><summary>缺资料日 · ${missing.length+data.obs_missing.length}条</summary><div class="table-scroll"><table><thead><tr><th>模式</th><th>日期</th><th>对应起报（BJT）</th><th>原因</th></tr></thead><tbody>${data.obs_missing.map(x=>`<tr><td>实况</td><td>${esc(x.day)}</td><td>—</td><td>${esc(x.reason)}</td></tr>`).join('')}${missing.map(x=>`<tr><td>${esc(names[x.m]||x.m)}</td><td>${esc(x.day)}</td><td>${esc(x.run_bjt)}</td><td>${esc(x.reason)}</td></tr>`).join('')}</tbody></table></div></details>
   <details class="case-detail"><summary>资料与检验口径</summary><div class="case-explain"><p>日界：${esc(data.day_definition)}。</p><p>实况：${esc(data.reference)}。</p><p>${esc(data.method)}</p><p>${demoMode?'模式来源：所有演示目标均使用 Google WeatherNext 3 原生0.1°集合均值，不是目标模式自身预报。':'模式来源：EC 为 ECMWF 开放数据 IFS 0.25°（3小时累计）；GFS 为 NOAA GFS 0.25°；AIGFS 为 NOAA AI 版 GFS 0.25°（6小时累计）。均只用 00/12 UTC 起报。'}</p><p>结果生成：${esc(String(data.generated).slice(0,16).replace('T',' '))} BJT。</p></div></details>`;
  by('monthly-results').querySelectorAll('canvas[data-map]').forEach(c=>{const k=c.dataset.map;
   if(k==='obs')drawMap(c,observationFor(w).total_map,RAIN);else{const m=k.slice(2);drawMap(c,k[0]==='t'?w.models[m].total_map:w.models[m].bias_map,k[0]==='t'?RAIN:BIAS);}});
  by('monthly-csv').onclick=()=>exportCsv(w);by('monthly-svg').onclick=()=>save(by('monthly-daily').innerHTML,'image/svg+xml;charset=utf-8',`${demoMode?'WN3演示_':''}月降水逐日-${data.month}-${w.id}.svg`);
 }
 function save(content,type,name){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);}
 function exportCsv(w){
  const models=state.models.filter(m=>w.models[m]),cell=v=>'"'+String(v??'').replaceAll('"','""')+'"';
  const head=['是否演示','实际资料来源','排除正式排名','日期','实况面雨量','实况格点最大',...models.flatMap(m=>[`${names[m]||m}起报(BJT)`,`${names[m]||m}面雨量`,...data.thresholds.map(t=>`${names[m]||m} TS≥${t.value}`)])];
  const rows=observationFor(w).daily.map((d,i)=>[demoMode,demoMode?data.source_model:'历史模式原始资料',demoMode,d.day,d.mean,d.max,...models.flatMap(m=>{const x=w.models[m].daily[i];return [x.run_bjt||'',x.mean,...data.thresholds.map(t=>x.scores?.[String(t.value)]?.TS)];})]);
  save('\ufeff'+[head,...rows].map(r=>r.map(cell).join(',')).join('\r\n'),'text/csv;charset=utf-8',`${demoMode?'WN3演示_':''}月降水检验-${data.month}-${w.id}.csv`);
 }
 return {open(){opened=true;if(!by('monthly-status'))shell();if(data){controls();render();}else loadCatalog();},close(){seq++;opened=false;},setDemo(value){
  if(value===demoMode)return this.open();seq++;
  if(value){formalState=state;state=demoState||{month:null,window:'d1',threshold:'25',models:null};}
  else{demoState=state;state=formalState||{month:null,window:'d1',threshold:'25',models:null};}
  demoMode=value;data=null;catalog=null;opened=true;shell();return loadCatalog();
 },refresh(){data=null;catalog=null;if(opened){shell();loadCatalog();}}};
})();
