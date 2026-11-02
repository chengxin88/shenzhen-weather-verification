'use strict';
window.StationVerification=(()=>{
 const by=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const num=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:3}):'—';
 const finite=v=>typeof v==='number'&&Number.isFinite(v);
 const colors={ec01:'#087f88',ecmwf:'#0072b2',gfs:'#d55e00',aifs:'#009e73',aigfs:'#cc79a7',gefs:'#8a5a2b',wn3:'#8b5cf6'};
 const sources={ogimet:'OGIMET SYNOP',ghcnh:'NOAA GHCNh',iem:'IEM METAR'};
 const format=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
 const time=v=>v?format.format(new Date(v)):'—';
 let meta=null,data=null,opened=false,seq=0,controller=null,detailController=null;
 let state={var:'t',days:30,models:['ec01','ecmwf'],model:'ec01',metric:'RMSE',range:'0-168',threshold:.1,station:'59493',lead:24};
 // hostId: standalone page, or a container inside the 降水/气温 grid page (allowed limits the variables).
 let hostId='stations-workspace',allowed=null;
 const dayLabels={1:'近24小时',3:'近3天',7:'近7天',30:'近30天'};
 const host=()=>by(hostId);
 const windows=()=>meta?.windows||[7,30];
 const rain=()=>state.var.startsWith('rain');
 const variable=()=>meta?.variables.find(v=>v.id===state.var);
 const label=m=>meta?.models.find(x=>x.id===m)?.label||m;
 const options=(items,current)=>items.map(([v,t])=>`<option value="${esc(v)}" ${String(v)===String(current)?'selected':''}>${esc(t)}</option>`).join('');
 function status(text,error=false){by('stations-status').textContent=text;by('stations-status').classList.toggle('error',error);}
 async function api(path,signal){
  const response=await fetch('./api/stations/'+path,{signal,cache:'no-store'});let value;
  try{value=await response.json();}catch{throw Error('站点接口未返回资料，请检查登录状态。');}
  if(!response.ok)throw Error(value.error||'站点资料暂不可用。');return value;
 }
 function shell(){host().innerHTML=`${allowed?'':'<div class="page-heading"><div><p class="eyebrow">STATION VERIFICATION</p><h1>站点检验</h1><p>华南 · 104–121°E / 18–27°N · 实测站点与本地固定起报预报</p></div></div>'}<div id="stations-status" class="notice" role="status">读取已发布站点资料…</div><section id="stations-controls" class="filter-card" aria-label="站点检验条件"></section><div id="stations-results"></div><section id="stations-detail" class="results" aria-label="单站时间序列"></section>`;}
 function controls(){
  const metrics=rain()?['TS','POD','FAR','ETS']:['RMSE','ME','MAE','CC'];
  const variables=meta.variables.filter(v=>!allowed||allowed.includes(v.id));
  const dayButtons=Object.keys(dayLabels).map(Number).map(d=>`<button type="button" data-station-days="${d}" aria-pressed="${d===state.days}" ${windows().includes(d)?'':'disabled title="下次定时评分后提供"'}>${dayLabels[d]}</button>`).join('');
  by('stations-controls').innerHTML=`<div class="stations-filters">${variables.length>1?`<label>检验要素<select id="stations-var">${options(variables.map(v=>[v.id,v.label+' · '+v.unit]),state.var)}</select></label>`:''}<div class="filter-field stations-days"><span>日期范围</span><div id="stations-days" class="choice-buttons" role="group" aria-label="站点检验日期范围">${dayButtons}<button type="button" disabled title="站点资料仅保留近31天，暂不支持自定义日期">自定义</button></div></div><label>评分指标<select id="stations-metric">${options(metrics.map(x=>[x,x]),state.metric)}</select></label><label>预报时效<select id="stations-range">${options([['0-168','0–168小时'],['0-24','0–24小时'],['25-72','25–72小时'],['73-168','73–168小时']],state.range)}</select></label>${rain()?`<label>降水阈值 · mm<select id="stations-threshold">${options(variable().thresholds.map(t=>[t,t]),state.threshold)}</select></label>`:''}</div><div class="model-row"><span>比较模式</span><div class="model-chips">${meta.models.map(m=>`<button type="button" class="model-chip" data-station-model="${esc(m.id)}" aria-pressed="${state.models.includes(m.id)}"><i style="background:${colors[m.id]}"></i>${esc(m.label)}</button>`).join('')}</div></div><p class="filter-note">${allowed?'站点实况：OGIMET SYNOP / NOAA GHCNh / IEM METAR，华南 104–121°E、18–27°N；评分由定时任务离线计算，日期范围从最近一次评分时刻往前算。':''}连续量按全部站点配对合并；ME＝预报－实况。各模式使用各自有效样本，请结合 n 比较。降水阈值事件为累计量 ≥ 阈值，微量不计为 ≥0.1 mm。</p>`;
  for(const key of ['var','range'])if(by('stations-'+key))by('stations-'+key).onchange=e=>{
   state[key]=e.target.value;
   if(key==='var'){state.metric=rain()?'TS':'RMSE';state.threshold=.1;}
   controls();loadScores();
  };
  by('stations-days').querySelectorAll('[data-station-days]').forEach(b=>b.onclick=()=>{state.days=Number(b.dataset.stationDays);controls();loadScores();});
  by('stations-metric').onchange=e=>{state.metric=e.target.value;render();};
  if(rain())by('stations-threshold').onchange=e=>{state.threshold=Number(e.target.value);render();loadDetail();};
  by('stations-controls').querySelectorAll('[data-station-model]').forEach(b=>b.onclick=()=>{
   const model=b.dataset.stationModel;
   if(state.models.includes(model)&&state.models.length===1)return;
   state.models=state.models.includes(model)?state.models.filter(m=>m!==model):[...state.models,model];
   if(!state.models.includes(state.model))state.model=state.models[0];
   controls();loadScores();
  });
 }
 function chooseRows(rows){return rows.filter(r=>!rain()||r.threshold===state.threshold);}
 function modelSelect(id){return `<label>地图 / 单站模式<select id="${id}">${options(state.models.map(m=>[m,label(m)]),state.model)}</select></label>`;}
 let mapSequence=0;
 function mapView(meta,state,rows,modelLabel){
  const scores=new Map(rows.map(r=>[r.station,r]));
  const values=rows.map(r=>r[state.metric]).filter(finite),lo=values.length?Math.min(...values):0,hi=values.length?Math.max(...values):1;
  const color=value=>{if(!finite(value))return '#9cabb4';const t=(value-lo)/(hi-lo||1);return `hsl(${210-195*t} 64% 47%)`;};
  const [west,south,east,north]=meta.map_bounds||[104,18,121,27];
  const x=lon=>48+(lon-west)/(east-west)*574,y=lat=>440-(lat-south)/(north-south)*380;
  let svg=`<svg viewBox="0 0 660 490" role="img" aria-label="华南站点评分分布，经度${west}到${east}，纬度${south}到${north}">`;
  svg+='<rect x="48" y="60" width="574" height="380" class="stations-water"/>';
  for(let lon=Math.ceil(west/2)*2;lon<=east;lon+=2)svg+=`<path class="stations-grid" d="M${x(lon)} 60V440"/><text x="${x(lon)}" y="462" text-anchor="middle">${lon}°E</text>`;
  for(let lat=Math.ceil(south/3)*3;lat<=north;lat+=3)svg+=`<path class="stations-grid" d="M48 ${y(lat)}H622"/><text x="42" y="${y(lat)+4}" text-anchor="end">${lat}°N</text>`;
  // Keep line layers inside the plotting rectangle and beneath station markers.
  const clipId='stations-map-clip-'+(++mapSequence);
  svg+=`<defs><clipPath id="${clipId}"><rect x="48" y="60" width="574" height="380"/></clipPath></defs><g clip-path="url(#${clipId})" aria-hidden="true">`;
  for(const [layer,style] of [['province_boundaries','stations-province'],['country_boundaries','stations-border'],['coastline','stations-coast']]){
   for(const line of meta[layer]||[])if(line.length>=2)svg+=`<path class="${style}" d="${line.map(([a,b],i)=>`${i?'L':'M'}${x(a).toFixed(1)} ${y(b).toFixed(1)}`).join('')}"/>`;
  }
  svg+='</g>';
  for(const station of meta.stations){
   const score=scores.get(station.id),value=score?.[state.metric],cx=x(station.lon),cy=y(station.lat),selected=station.id===state.station;
   const high=station.elevation_flags?.includes(state.model),r=selected?7:score?5:3;
   const title=`${station.id} ${station.name} · ${state.metric} ${num(value)} · n=${score?.n||0}${high?' · 海拔差超过300米':''}`;
   svg+=`<g data-station-id="${esc(station.id)}" role="button" tabindex="0" aria-label="${esc(title)}" class="stations-point ${selected?'selected':''}"><title>${esc(title)}</title>`;
   if(high)svg+=`<path d="M${cx} ${cy-r-2}l${r+2} ${2*r+3}h${-2*r-4}Z" fill="${color(value)}"/>`;
   else if(station.type==='metar')svg+=`<rect x="${cx-r}" y="${cy-r}" width="${r*2}" height="${r*2}" fill="${color(value)}"/>`;
   else svg+=`<circle cx="${cx}" cy="${cy}" r="${r}" fill="${color(value)}"/>`;
   svg+='</g>';
  }
  svg+=`<text x="48" y="30">${esc(modelLabel)} · ${esc(state.metric)} · 有配对 ${rows.length} 站</text></svg>`;
  return `${svg}<div class="stations-map-key"><span>● SYNOP　■ METAR　▲ |海拔差| &gt; 300 m</span><span class="stations-boundary-key"><i class="stations-coast-key"></i>海岸线 / 国界</span><span class="stations-boundary-key"><i class="stations-province-key"></i>省界</span><span>灰色：无有效指标</span><span class="stations-color-scale"></span><span>${num(lo)} → ${num(hi)}（当前范围）</span><span>站点评分范围：104–121°E / 18–27°N</span></div>`;
 }
 function map(){return mapView(meta,state,chooseRows(data.station_rows).filter(r=>r.model===state.model),label(state.model));}
 function lineChart(series,metric,xLabel,timeAxis=false){
  const usable=series.flatMap(s=>s.rows.filter(r=>finite(r.y)&&finite(r.x)));
  if(!usable.length)return '<p class="stations-empty">当前条件暂无有效配对。</p>';
  let lo=Math.min(...usable.map(r=>r.y)),hi=Math.max(...usable.map(r=>r.y));const pad=(hi-lo||1)*.12;lo-=pad;hi+=pad;
  const left=58,right=760,top=32,bottom=265,xlo=Math.min(...usable.map(r=>r.x)),xhi=Math.max(...usable.map(r=>r.x));
  const x=v=>left+(v-xlo)/(xhi-xlo||1)*(right-left),y=v=>bottom-(v-lo)/(hi-lo)*(bottom-top);
  let svg=`<svg viewBox="0 0 800 320" role="img" aria-label="${esc(metric)}折线图"><text x="58" y="18">${esc(metric)}</text>`;
  for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4;svg+=`<path class="stations-grid" d="M58 ${y(v)}H760"/><text x="50" y="${y(v)+4}" text-anchor="end">${num(v)}</text>`;}
  for(let i=0;i<=4;i++){const v=xlo+(xhi-xlo)*i/4;svg+=`<text x="${x(v)}" y="289" text-anchor="middle">${esc(timeAxis?time(new Date(v)):num(v))}</text>`;}
  for(const s of series){let path='',previous=null;
   for(const r of s.rows){if(!finite(r.y)){previous=null;continue;}const gap=previous!==null&&timeAxis&&r.x-previous>12*3600000;path+=`${previous===null||gap?'M':'L'}${x(r.x)} ${y(r.y)} `;previous=r.x;}
   svg+=`<path d="${path}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
   for(const r of s.rows.filter(r=>finite(r.y)))svg+=`<circle cx="${x(r.x)}" cy="${y(r.y)}" r="2.5" fill="${s.color}"><title>${esc(s.label)} · ${esc(timeAxis?time(new Date(r.x)):'+'+r.x+'h')} · ${num(r.y)}${r.n?' · n='+r.n:''}</title></circle>`;
  }
  return svg+`<text x="410" y="314" text-anchor="middle">${esc(xLabel)}</text></svg>`;
 }
 function render(){
  if(!data)return;
  const rows=chooseRows(data.rows),leads=[...new Set(rows.map(r=>r.lead))].sort((a,b)=>a-b);
  const series=state.models.map(model=>({label:label(model),color:colors[model],rows:rows.filter(r=>r.model===model).sort((a,b)=>a.lead-b.lead).map(r=>({x:r.lead,y:r[state.metric],n:r.n}))}));
  const index=new Map(rows.map(r=>[r.model+':'+r.lead,r]));
  by('stations-results').innerHTML=`<div class="stations-panels"><section class="results stations-map-card"><div class="result-heading"><h2>站点分布</h2>${modelSelect('stations-map-model')}</div><div id="stations-map">${map()}</div></section><section class="results stations-lead-card"><div class="result-heading"><div><h2>模式 × 时效</h2><p>${esc(variable()?.label||'')} · ${esc(state.metric)}${rain()?' · ≥'+state.threshold+' mm':''}</p></div></div><div class="legend">${state.models.map(m=>`<span><i style="background:${colors[m]}"></i>${esc(label(m))}</span>`).join('')}</div>${lineChart(series,state.metric,'预报时效 / 小时')}<p class="stations-note">${state.days<=3?`${esc(dayLabels[state.days])}样本较少，分数波动较大，请结合 n 判断。`:''}每个点汇总全部有效站点与起报，未对时效平均分数。缺测与无有效分母显示“—”。</p></section></div><section class="table-card"><div class="table-heading"><h2>逐时效评分明细</h2><span>${esc(state.metric)} / n</span></div><div class="table-scroll stations-score-table"><table><thead><tr><th>时效</th>${state.models.map(m=>`<th>${esc(label(m))}</th>`).join('')}</tr></thead><tbody>${leads.map(lead=>`<tr><th>+${lead} h</th>${state.models.map(m=>{const r=index.get(m+':'+lead);return `<td>${num(r?.[state.metric])}<small> / ${r?.n||0}</small></td>`;}).join('')}</tr>`).join('')}</tbody></table></div></section>`;
  by('stations-map-model').onchange=e=>{state.model=e.target.value;render();loadDetail();};
  by('stations-map').querySelectorAll('[data-station-id]').forEach(point=>{
   const select=()=>{state.station=point.dataset.stationId;render();loadDetail();};point.onclick=select;point.onkeydown=e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();select();}};
  });
 }
 function detailControls(){return `<div class="result-heading"><div><h2>单站预报与实况</h2><p>固定起报时效，逐有效时间比较 · 北京时间</p></div></div><div class="stations-filters"><label>站点<select id="stations-id">${options(meta.stations.map(s=>[s.id,s.id+' '+s.name]),state.station)}</select></label>${modelSelect('stations-detail-model')}<label>固定预报时效<select id="stations-detail-lead">${options((meta.models.find(m=>m.id===state.model)?.leads||[24]).map(h=>[h,'+'+h+' 小时']),state.lead)}</select></label></div><div id="stations-detail-body" aria-live="polite"></div>`;}
 async function loadDetail(){
  detailController?.abort();detailController=new AbortController();const signal=detailController.signal;
  if(!meta.models.find(m=>m.id===state.model)?.leads.includes(state.lead))state.lead=meta.models.find(m=>m.id===state.model)?.leads[0]??24;
  by('stations-detail').innerHTML=detailControls();
  by('stations-id').onchange=e=>{state.station=e.target.value;render();loadDetail();};
  by('stations-detail-model').onchange=e=>{state.model=e.target.value;render();loadDetail();};
  by('stations-detail-lead').onchange=e=>{state.lead=Number(e.target.value);loadDetail();};
  by('stations-detail-body').textContent='读取单站配对…';
  try{
   const detail=await api('station?'+new URLSearchParams({id:state.station,var:state.var,model:state.model,lead:state.lead,days:state.days}),signal);
   if(signal.aborted||!opened)return;
   const stat=chooseRows(detail.stats)[0],s=detail.station,z=s.model_elevations?.[state.model];
   const keys=rain()?['n','H','M','F','TS','POD','FAR','ETS']:['n','ME','MAE','RMSE','CC'];
   by('stations-detail-body').innerHTML=`<p class="stations-note">${esc(s.name)} · ${s.lon.toFixed(3)}°E / ${s.lat.toFixed(3)}°N · 站高 ${num(s.elevation)} m · 模式格点海拔 ${num(z)} m${s.elevation_flags?.includes(state.model)?' · ▲ 海拔差超过300 m':''} · 未作海拔订正</p><div class="stations-stats">${keys.map(k=>`<span>${k}<strong>${num(stat?.[k])}</strong></span>`).join('')}</div><div class="legend"><span><i style="background:${colors[state.model]}"></i>${esc(label(state.model))}</span><span><i style="background:#536575"></i>站点实况</span></div>${lineChart([{label:label(state.model),color:colors[state.model],rows:detail.series.map(r=>({x:Date.parse(r.valid),y:r.fc}))},{label:'站点实况',color:'#536575',rows:detail.series.map(r=>({x:Date.parse(r.valid),y:r.ob}))}],variable()?.label+' / '+variable()?.unit,'有效时间 / 北京时间',true)}<p class="stations-note">${detail.series.length} 个配对 · 实况来源：${[...new Set(detail.series.map(r=>sources[r.source]||r.source))].join('、')||'暂无'}。METAR取模型有效时刻±10分钟内最近报告；SYNOP按UTC三小时时次。未评分的观测保留质控标记。</p>`;
  }catch(error){if(error.name!=='AbortError'&&!signal.aborted)by('stations-detail-body').textContent=error.message;}
 }
 async function loadScores(){
  controller?.abort();controller=new AbortController();const signal=controller.signal,current=++seq;data=null;
  by('stations-results').textContent='读取已发布评分…';by('stations-detail').textContent='读取当前条件…';detailController?.abort();
  try{
   const response=await api('scores?'+new URLSearchParams({var:state.var,models:state.models.join(','),days:state.days,range:state.range}),signal);
   if(current!==seq||!opened)return;data=response;render();loadDetail();
  }catch(error){if(error.name!=='AbortError'&&current===seq)by('stations-results').textContent=error.message;}
 }
 async function refresh(){
  controller?.abort();detailController?.abort();controller=new AbortController();const signal=controller.signal,current=++seq;
  try{
   const response=await api('meta',signal);if(current!==seq||!opened)return;meta=response;
   if(meta.state==='empty'){data=null;for(const id of ['stations-controls','stations-results','stations-detail'])by(id).textContent='';status(meta.notice);return;}
   const sourceText=Object.entries(meta.sources).map(([id,s])=>`${sources[id]||id} ${s.stations}站 / 最新 ${time(s.latest)}`).join('；');
   status(`有资料 ${meta.stations_with_data??'—'} 站。${sourceText}。评分更新 ${time(meta.generated)} BJT${meta.update_notice?'；'+meta.update_notice:''}${meta.ogimet.stopped?'；OGIMET已停止：'+meta.ogimet.stopped:''}`);
   if(!meta.stations.some(s=>s.id===state.station))state.station=meta.stations[0]?.id;
   if(!windows().includes(state.days))state.days=windows().includes(30)?30:windows().at(-1);
   controls();loadScores();
  }catch(error){if(error.name!=='AbortError'&&current===seq)status(error.message,true);}
 }
 function open(options={}){
  const next=options.host||'stations-workspace',vars=options.vars||null;
  if(next!==hostId){close();by(hostId)?.replaceChildren();hostId=next;}
  const rebuild=!host().querySelector('#stations-status')||String(vars)!==String(allowed);allowed=vars;
  if(allowed&&!allowed.includes(state.var)){state.var=allowed[0];state.metric=rain()?'TS':'RMSE';state.threshold=.1;}
  opened=true;if(rebuild)shell();refresh();
 }
 // close(id) only closes the view when it is currently shown in that container.
 function close(id){if(id&&id!==hostId)return;opened=false;seq++;controller?.abort();detailController?.abort();}
 // Shared, stateless renderers also serve the regular grid charts' station supplement.
 window.StationVisuals={map:mapView,lineChart,api,colors,sources,num,esc};
 return {open,close,refresh};
})();
