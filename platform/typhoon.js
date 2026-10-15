'use strict';
window.NativeTyphoon=(()=>{
 const by=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const finite=v=>typeof v==='number'&&Number.isFinite(v),num=v=>finite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:1}):'—';
 const date=v=>v?String(v).slice(0,16).replace('T',' '):'—';
 const sourceNames={'中央气象台':'CMA','ECMWF IFS':'EC（IFS）','NCEP GFS':'GFS','ECMWF AIFS':'ECAI（AIFS）','NCEP AIGFS':'GFSAI（AIGFS）'};
 const sourceLabel=label=>sourceNames[label]||label;
 const metrics=[['登陆时刻预报点误差(km)','登陆点位置误差','km',''],['时间误差(h)','到达时间误差','小时','正值偏晚，负值偏早'],['中心气压误差(hPa)','中心气压误差','hPa','正值偏高，负值偏低'],['最大风速误差(m/s)','最大风速误差','m/s','正值偏强，负值偏弱']];
 const columns=['来源','起报提前(h)','起报时刻(BJT)','登陆时刻预报点误差(km)','时间误差(h)','中心气压误差(hPa)','最大风速误差(m/s)','状态'];
 const columnNames={'登陆时刻预报点误差(km)':'登陆点误差(km)','时间误差(h)':'时间误差(h)','中心气压误差(hPa)':'气压误差(hPa)','最大风速误差(m/s)':'风速误差(m/s)'};
 let meta=null,detail=null,result=null,state={year:null,storm_id:null,landfall:0,sources:[]},leadWindow='all',seq=0,controller=null,timer=null,opened=false;
 const chartCache=new Map(),cacheKey='szverify-native-typhoon-v1';
 const host=()=>by('typhoon-workspace');
 function shell(){host().innerHTML=`<div class="page-heading"><div><p class="eyebrow">TYPHOON VERIFICATION</p><h1>台风登陆检验</h1><p>比较登陆前72小时内各个实际起报批次的位置、到达时间和强度误差。</p></div></div><section class="filter-card" id="typhoon-controls" aria-label="台风检验条件"></section><div class="notice" id="typhoon-status" role="status">正在读取台风归档…</div><div id="typhoon-results" hidden></div>`;}
 function cancel(){seq++;clearTimeout(timer);controller?.abort();controller=null;}
 function status(text,error=false){by('typhoon-status').textContent=text;by('typhoon-status').classList.toggle('error',error);}
 async function api(path,body,signal){
  const r=await fetch('./api/typhoon/'+path,{...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{}),signal});let value;
  try{value=await r.json();}catch{throw Error(r.redirected||r.status===401?'登录已过期，请刷新后重新登录。':`台风服务返回异常（HTTP ${r.status}），请稍后重试。`);}
  if(!r.ok){const err=Error(value.error||'台风资料暂不可用。');err.retryAfter=value.retry_after;throw err;}return value;
 }
 function pause(ms,signal){return new Promise((resolve,reject)=>{const stop=()=>{clearTimeout(t);reject(new DOMException('已切换条件','AbortError'));};const t=setTimeout(()=>{signal.removeEventListener('abort',stop);resolve();},ms);signal.addEventListener('abort',stop,{once:true});});}
 function remember(key,value){
  try{let items=JSON.parse(sessionStorage.getItem(cacheKey)||'[]').filter(x=>x.key!==key&&Date.now()-x.time<300000);items.push({key,time:Date.now(),value});items=items.slice(-4);const json=JSON.stringify(items);if(json.length<900000)sessionStorage.setItem(cacheKey,json);}catch{}
 }
 function remembered(key){try{return JSON.parse(sessionStorage.getItem(cacheKey)||'[]').find(x=>x.key===key&&Date.now()-x.time<300000)?.value;}catch{return null;}}
 function drawControls(){
  if(!meta)return;
  const options=(items,current)=>items.map(([v,label])=>`<option value="${esc(v)}" ${v===current?'selected':''}>${esc(label)}</option>`).join('');
  by('typhoon-controls').innerHTML=`<div class="primary-filters typhoon-selects"><label>台风年份<select id="typhoon-year">${options(meta.years.map(y=>[y,y+'年']),state.year)}</select></label><label>选择台风<select id="typhoon-storm" ${meta.storms.length?'':'disabled'}>${options(meta.storms.map(s=>[s.storm_id,`${s.number}号 ${s.chinese_name}（${s.english_name}）`]),state.storm_id)}</select></label><label>官方登陆记录<select id="typhoon-landfall" ${detail?.landfalls.length?'':'disabled'}>${detail?.landfalls.length?options(detail.landfalls.map(l=>[l.index,`第${l.index+1}次 · ${date(l.time)} BJT · ${l.address}`]),state.landfall):'<option>暂无官方登陆记录</option>'}</select></label></div>
   <div class="model-row"><span>检验来源</span><div id="typhoon-sources" class="model-chips">${[...meta.sources,...window.DemoModels.placeholders()].map(s=>`<button type="button" class="model-chip" data-source="${esc(s.id)}" ${s.disabled?'disabled title="WN3没有台风路径，暂不生成评分"':''} aria-pressed="${state.sources.includes(s.id)}"><i style="background:${esc(s.color)}"></i>${esc(s.is_demo?window.DemoModels.label(s.id):sourceLabel(s.label))}</button>`).join('')}</div></div>
   <p class="filter-note">选择条件后自动加载。默认显示官方机构；点击来源可加入数值和AI模式。时间均为北京时间，缺测保留为空。</p>`;
  by('typhoon-year').onchange=e=>loadCatalog(Number(e.target.value));by('typhoon-storm').onchange=e=>loadStorm(Number(e.target.value));by('typhoon-landfall').onchange=e=>{state.landfall=Number(e.target.value);dirty();};
  by('typhoon-sources').querySelectorAll('button').forEach(b=>b.onclick=()=>{state.sources=state.sources.includes(b.dataset.source)?state.sources.filter(s=>s!==b.dataset.source):meta.sources.map(s=>s.id).filter(s=>state.sources.includes(s)||s===b.dataset.source);dirty();});
 }
 function begin(text){cancel();controller=new AbortController();result=null;by('typhoon-results').hidden=true;status(text);return [seq,controller.signal];}
 async function loadCatalog(year){
  const [current,signal]=begin('正在读取台风归档…');
  try{
   meta=await api('catalog'+(year?'?year='+year:''),null,signal);if(current!==seq)return;
   state.year=meta.year;state.storm_id=meta.default_storm;detail=null;
   if(!state.sources.length)state.sources=meta.sources.filter(s=>['CMA/NMC','JMA','JTWC'].includes(s.id)).map(s=>s.id);
   drawControls();if(!meta.storms.length){status(`${state.year}年暂无台风资料。`);return;}
   detail=await api(`storm?year=${state.year}&storm_id=${state.storm_id}`,null,signal);if(current!==seq)return;
   state.landfall=detail.default_landfall;drawControls();await calculate(current,signal);
  }catch(e){if(current===seq&&e.name!=='AbortError')status(e.message,true);}
 }
 async function loadStorm(id){
  const [current,signal]=begin('正在读取官方登陆记录…');state.storm_id=id;detail=null;drawControls();
  try{detail=await api(`storm?year=${state.year}&storm_id=${id}`,null,signal);if(current!==seq)return;state.landfall=detail.default_landfall;drawControls();await calculate(current,signal);}catch(e){if(current===seq&&e.name!=='AbortError')status(e.message,true);}
 }
 function dirty(){const [current,signal]=begin('正在加载所选条件…');drawControls();timer=setTimeout(()=>calculate(current,signal),250);}
 async function calculate(current,signal){
  if(!detail?.landfalls.length){status('该台风暂无可用的官方登陆记录，请选择其他台风。');return;}
  if(!state.sources.length){status('请至少选择一个检验来源。');return;}
  const selection={...state,sources:[...state.sources]},key=JSON.stringify([meta.cache_version,meta.archive_updated,selection]),saved=remembered(key);
  try{
   if(saved){showResult(saved,true);return;}
   let job;
   while(current===seq){try{job=await api('score',selection,signal);break;}catch(e){if(!e.retryAfter)throw e;status('已有台风检验正在读取，稍后自动加载…');await pause(e.retryAfter*1000,signal);}}
   while(current===seq&&['queued','running'].includes(job.status)){status(job.message);await pause(1200,signal);job=await api('jobs/'+job.id,null,signal);}
   if(current!==seq)return;if(job.status==='error')throw Error(job.message);result=job.result;remember(key,result);showResult(result);
  }catch(e){if(current===seq&&e.name!=='AbortError')status(e.message,true);}
 }
 function selectedRows(){return result.rows.filter(r=>{const h=r['起报提前(h)'];return finite(h)&&(leadWindow==='all'||leadWindow==='0'&&h>0&&h<=24||leadWindow==='24'&&h>24&&h<=48||leadWindow==='48'&&h>48&&h<=72);});}
 function showResult(value,local=false){result={...value,sources:value.sources.map(s=>({...s,label:sourceLabel(s.label)})),rows:value.rows.map(r=>({...r,'来源':sourceLabel(r['来源'])}))};by('typhoon-results').hidden=false;status(`${local?'已载入页面缓存':result.cached?'已载入服务器缓存':'检验结果已加载'} · 评分更新 ${date(result.computed)} BJT`);render();}
 function mean(values){const v=values.filter(finite);return v.length?v.reduce((a,b)=>a+b,0)/v.length:null;}
 function table(rows,cols){return `<table><thead><tr>${cols.map(c=>`<th>${esc(columnNames[c]||c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${esc(c.includes('(BJT)')?date(r[c]):finite(r[c])?num(r[c]):r[c]??'—')}</td>`).join('')}</tr>`).join('')}</tbody></table>`;}
 function render(){
  const rows=selectedRows(),valid=rows.filter(r=>finite(r[metrics[0][0]])),failed=rows.filter(r=>!finite(r[metrics[0][0]]));
  by('typhoon-results').innerHTML=`<div class="typhoon-landfall"><div><strong>${esc(result.title)}</strong><span>第${state.landfall+1}次登陆 · ${esc(date(result.landfall.time))} BJT · ${esc(result.landfall.address)}</span></div><span>${num(result.landfall.lon)}°E / ${num(result.landfall.lat)}°N</span></div>
   <section class="stats" aria-label="台风样本概况"><article class="stat"><span>有效起报样本</span><strong>${valid.length}<small> / ${rows.length}</small></strong><p>逐来源、逐实际起报批次</p></article><article class="stat"><span>平均登陆点误差</span><strong>${num(mean(valid.map(r=>r[metrics[0][0]])))}<small> km</small></strong><p>所选有效样本的算术平均</p></article><article class="stat"><span>登陆实况中心气压</span><strong>${num(rows.find(r=>finite(r['登陆实况气压(hPa)']))?.['登陆实况气压(hPa)'])}<small> hPa</small></strong><p>最接近登陆时刻的实况路径点</p></article><article class="stat"><span>登陆实况最大风速</span><strong>${num(rows.find(r=>finite(r['登陆实况风速(m/s)']))?.['登陆实况风速(m/s)'])}<small> m/s</small></strong><p>中央气象台实况口径</p></article></section>
   <section class="results typhoon-results"><div class="result-heading"><div><h2>登陆前不同起报提前量的误差</h2><p>每个点对应一条实际起报记录，悬停查看时刻与数值。</p></div><button class="quiet" id="typhoon-export-svg">导出图表 ↓</button></div><div class="typhoon-view-tools"><span>起报提前量</span><div class="choice-buttons" role="group" aria-label="起报提前量">${[['all','全部 · 72小时内'],['0','0—24小时'],['24','24—48小时'],['48','48—72小时']].map(([v,label])=>`<button data-window="${v}" aria-pressed="${leadWindow===v}">${label}</button>`).join('')}</div></div><div class="legend">${result.sources.map(s=>`<span><i style="background:${esc(s.color)}"></i>${esc(s.label)}</span>`).join('')}</div><div class="typhoon-charts" role="region" aria-label="台风误差图表">${metrics.map(m=>`<article><h3>${m[1]} <small>/ ${m[2]}</small></h3><p>${m[3]||'预报点距官方登陆点的距离'}</p>${chart(rows,m)}</article>`).join('')}</div><div class="chart-explainer">到达时间按预报路径首次进入登陆点150公里范围的时刻计算，不等同于实际穿越海岸的登陆时间。风速平均时段因来源而异，适合同一来源随提前量比较。</div></section>
   <section class="table-card"><div class="table-heading"><div><h2>逐起报检验明细</h2><p>${rows.length}条记录 · 缺测不补零；正负号含义见误差图</p></div><button id="typhoon-export-csv" class="quiet">导出 CSV ↓</button></div><div class="table-scroll">${rows.length?table(rows,columns):'<p class="typhoon-empty">当前提前量没有检验记录。</p>'}</div></section>
   <details class="case-detail" ${failed.length?'':'hidden'}><summary>缺测与未评分 · ${failed.length}条</summary><div class="table-scroll">${table(failed,['来源','起报时刻(BJT)','状态'])}</div></details><details class="case-detail"><summary>资料与检验口径</summary><div class="case-explain"><p>${esc(result.method)}</p><p>官方登陆记录：浙江台风网机构资料；实况路径：中央气象台。CMA为2分钟、JMA为10分钟、JTWC为1分钟平均持续风；数值和AI模式主要取瞬时10米风场，NCEP AIGFS优先使用NOAA ATCF追踪器。未作平均时段换算。</p>${result.sources.map(s=>`<p>${esc(s.label)}：${esc(s.wind_convention)}</p>`).join('')}${result.warning?`<p>${esc(result.warning)}</p>`:''}<p>台风目录归档更新：${esc(date(meta.archive_updated))} BJT。评分更新：${esc(date(result.computed))} BJT。</p></div></details>`;
  by('typhoon-results').querySelectorAll('[data-window]').forEach(b=>b.onclick=()=>{leadWindow=b.dataset.window;render();});
  by('typhoon-export-csv').onclick=()=>downloadCsv(rows);by('typhoon-export-svg').onclick=exportChart;
 }
 function chart(rows,metric){
  const cacheId=JSON.stringify([result.computed,result.selection,leadWindow,metric[0]]);if(chartCache.has(cacheId))return chartCache.get(cacheId);
  const usable=rows.filter(r=>finite(r[metric[0]])&&finite(r['起报提前(h)']));if(!usable.length)return '<div class="typhoon-empty">该指标暂无有效样本</div>';
  const W=520,H=270,L=60,R=16,T=20,B=44,pw=W-L-R,ph=H-T-B;
  let min=Math.min(0,...usable.map(r=>r[metric[0]])),max=Math.max(0,...usable.map(r=>r[metric[0]]));if(max===min)max=min+1;const pad=(max-min)*.12;if(min<0)min-=pad;max+=pad;
  const x=h=>L+(72-h)/72*pw,y=v=>T+(max-v)/(max-min)*ph;
  let svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${metric[1]}，按起报提前量比较" style="font-family:Segoe UI,Microsoft YaHei,sans-serif"><rect width="${W}" height="${H}" fill="white"/>`;
  for(let i=0;i<=4;i++){const v=min+(max-min)*i/4,yy=y(v);svg+=`<line x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}" stroke="#dce5ec"/><text x="${L-9}" y="${yy+4}" text-anchor="end" fill="#637a89" font-size="11">${num(v)}</text>`;}
  if(min<=0&&max>=0)svg+=`<line x1="${L}" x2="${W-R}" y1="${y(0)}" y2="${y(0)}" stroke="#8ba1ad" stroke-dasharray="4 4"/>`;
  for(const h of [72,60,48,36,24,12,0])svg+=`<text x="${x(h)}" y="${H-22}" text-anchor="middle" fill="#637a89" font-size="11">${h}</text>`;
  svg+=`<text x="${L+pw/2}" y="${H-4}" text-anchor="middle" fill="#637a89" font-size="11">起报提前量 / 小时</text>`;
  for(const source of result.sources){
   const all=rows.filter(r=>r['来源']===source.label&&finite(r['起报提前(h)'])).sort((a,b)=>b['起报提前(h)']-a['起报提前(h)']);let last=null;
   for(const r of all){if(!finite(r[metric[0]])){last=null;continue;}const cx=x(r['起报提前(h)']),cy=y(r[metric[0]]);if(last)svg+=`<line x1="${last[0]}" y1="${last[1]}" x2="${cx}" y2="${cy}" stroke="${esc(source.color)}" stroke-width="1.5"/>`;svg+=`<circle cx="${cx}" cy="${cy}" r="3.5" fill="${esc(source.color)}" stroke="white" stroke-width="1"><title>${esc(source.label)} · ${esc(date(r['起报时刻(BJT)']))} BJT · 提前${num(r['起报提前(h)'])}小时 · ${num(r[metric[0]])} ${metric[2]}</title></circle>`;last=[cx,cy];}
  }
  svg+='</svg>';if(chartCache.size>=24)chartCache.delete(chartCache.keys().next().value);chartCache.set(cacheId,svg);return svg;
 }
 function save(content,type,name){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);}
 function downloadCsv(rows){
  const cols=[...new Set(rows.flatMap(r=>Object.keys(r)))];if(!cols.length)return;
  const cell=v=>{let s=v==null?'':String(v);if(typeof v==='string'&&/^[=+\-@]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';};
  save('\ufeff'+[cols,...rows.map(r=>cols.map(c=>r[c]))].map(row=>row.map(cell).join(',')).join('\r\n'),'text/csv;charset=utf-8',`台风登陆检验-${state.year}-${state.storm_id}.csv`);
 }
 function exportChart(){
  const rows=selectedRows();const panels=metrics.map((m,i)=>{const raw=chart(rows,m),body=raw.startsWith('<svg')?raw.slice(raw.indexOf('>')+1,raw.lastIndexOf('</svg>')):'<text x="240" y="140" fill="#637a89">暂无有效样本</text>';return `<g transform="translate(${(i%2)*550},${Math.floor(i/2)*310+100})"><text x="60" y="-9" fill="#203b4e" font-size="15">${m[1]} / ${m[2]}</text>${body}</g>`;}).join('');
  const svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1100 740" style="font-family:Segoe UI,Microsoft YaHei,sans-serif"><rect width="1100" height="740" fill="white"/><text x="30" y="30" font-size="20" fill="#203b4e">${esc(result.title)} · 台风登陆检验</text><text x="30" y="52" font-size="12" fill="#637a89">${esc(date(result.landfall.time))} BJT · ${esc(result.landfall.address)}</text>${result.sources.map((s,i)=>`<text x="${30+i*150}" y="75" font-size="12" fill="${esc(s.color)}">${esc(s.label)}</text>`).join('')}${panels}<text x="30" y="732" font-size="11" fill="#637a89">时间误差按首次进入登陆点150公里范围计算；风速口径因来源而异。</text></svg>`;
  save(svg,'image/svg+xml;charset=utf-8',`台风登陆误差-${state.storm_id}.svg`);
 }
 return {open(){opened=true;if(!by('typhoon-status'))shell();if(meta&&detail&&result){drawControls();showResult(result,true);}else if(meta&&state.storm_id)loadStorm(state.storm_id);else loadCatalog();},close(){opened=false;cancel();},refresh(){try{sessionStorage.removeItem(cacheKey);}catch{}chartCache.clear();if(opened)loadCatalog(state.year);}};
})();
