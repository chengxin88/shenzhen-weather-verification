'use strict';
// The 站点分布 page: map + single-station forecast/observation card.
// All controls live in app.js; this file only draws the current selection.
(()=>{
 const by=id=>document.getElementById(id),workspace=by('grid-workspace'),host=by('grid-station-view');
 const V=window.StationVisuals;
 const WN3='Google WeatherNext 3（集合均值）';
 const sourceModel=model=>window.DemoModels.profile(model)?WN3:model;
 let metadata=null,selection=null,rows=[],station='59493',mapModel=null,lastMainThreshold=null,key='',sequence=0,controller=null,timer=null,mapSample='';
 const modelId=model=>({
  'ECMWF-IFS-9km-OpenMeteo':'ec01','ECMWF-IFS':'ecmwf','ECMWF-ENSEMBLES平均':'ecens','NCEP-GFS':'gfs','ECAI':'aifs',
  'GFSAI':'aigfs','GEFS集合平均':'gefs','Google WeatherNext 3（集合均值）':'wn3','WN3 订正（LightGBM）':'wn3ml'
 })[sourceModel(model)]||sourceModel(model);
 const choices=()=>selection.models?.length?selection.models:[{model:selection.model,label:selection.modelLabel,color:V.colors[modelId(selection.model)]}];
 const chosen=()=>choices().find(m=>m.model===mapModel)||choices()[0];
 const OBS='__obs',hidden=new Set();
 const runCaption=s=>s.hour&&s.hour!=='all'?`${s.hour}时起报（北京时间）`:'全部起报时次';
 function shell(){
  host.innerHTML='<section class="results stations-map-card"><div class="result-heading"><div><h2>站点分布图</h2><p id="grid-station-caption"></p></div><div class="grid-station-head-tools"><div id="grid-station-model" class="model-chips" role="group" aria-label="地图模式"></div><label class="grid-station-lead">单站时效<select id="grid-station-lead"></select></label><button type="button" id="grid-station-reset" class="quiet" hidden>返回区域</button></div></div><div id="grid-station-map" aria-live="polite"></div><p id="grid-station-map-note" class="stations-note"></p></section><section class="results grid-station-detail"><div class="result-heading"><h2>单站预报与实况</h2></div><div id="grid-station-detail-body" aria-live="polite"></div></section>';
 }
 function updateScopeNote(){
  const selected=window.GridVerification.selection().scoreStation;
  const note=by('grid-station-map-note');
  if(note)note.textContent=selected?`上方评分：${selected.name}（${selected.id}）。按所选日期、起报时次、时效及共同时间筛选计算该站评分；地图展示各站评分。`:mapSample;
  const reset=by('grid-station-reset');if(reset)reset.hidden=!selected;
 }
 function drawMap(){
  const m=chosen();updateScopeNote();
  V.mountMap(by('grid-station-map'),'grid',metadata,view=>V.map(metadata,{model:modelId(m.model),metric:selection.metric,var:selection.variable,threshold:selection.variable.startsWith('rain')?(selection.metric==='PC'?.1:selection.threshold):null,grade:null,verificationMode:'cumulative',station},rows,m.label,view),canvas=>canvas.querySelectorAll('[data-station-id]').forEach(point=>{
   const select=()=>{
    station=point.dataset.stationId;
    const site=metadata.stations.find(s=>s.id===station);
    if(site)window.GridVerification.setStationScore(site);
    drawMap();loadDetail(sequence,controller.signal);
   };
   point.onclick=select;point.onkeydown=event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();select();}};
  }),region=>{
   if(window.GridVerification?.setStationRegion){
    window.GridVerification.setStationRegion(region);
    updateScopeNote();
   }
  });
 }
 async function loadMap(current,signal){
  const s=selection,m=chosen(),th=s.metric==='PC'?.1:Number(s.threshold??.1);
  by('grid-station-map').textContent='读取站点分布…';
  const data=await V.api('map?'+new URLSearchParams({model:sourceModel(m.model),var:s.variable,threshold:th,start:s.start,end:s.end,hour:s.hour||'all',leads:s.leads.join(',')}),signal);
  if(signal.aborted||current!==sequence)return;
  rows=data.rows;
  by('grid-station-caption').textContent=`${m.label} · ${s.start} — ${s.end} BJT · ${runCaption(s)}${s.leads.length<=3?` · ${s.leads.map(lead=>'+'+lead).join('/')}小时`:''}${s.variable.startsWith('rain')?` · ≥${th} mm`:''}`;
  mapSample=(data.sample||'')+(metadata.update_notice?' '+metadata.update_notice:'')+(window.DemoModels.profile(m.model)?' 当前为 WN3 演示资料，共用 WN3 站点配对，不计入正式排名。':'');
  drawMap();
 }
 let detailSequence=0,detail=null;
 async function loadDetail(current,signal){
  const request=++detailSequence,s={...selection},ident=station;
  const body=by('grid-station-detail-body');if(!body)return;
  body.textContent='读取单站配对…';
  try{
   // One series per model; a model without pairs at this station/lead is listed, not fatal.
   const sources=new Map();
   const results=await Promise.all(choices().map(m=>{
    const source=sourceModel(m.model);
    if(!sources.has(source))sources.set(source,V.api('series?'+new URLSearchParams({station:ident,model:source,var:s.variable,lead:s.lead,start:s.start,end:s.end,hour:s.hour||'all'}),signal));
    return sources.get(source).then(data=>({...m,data}),error=>{if(signal.aborted)throw error;return {...m,data:null,error:error.message};});
   }));
   if(signal.aborted||current!==sequence||request!==detailSequence)return;
   detail={s,results};drawDetail();
  }catch(error){if(!signal.aborted&&current===sequence&&request===detailSequence)body.textContent=error.message;}
 }
 function drawDetail(){
  const body=by('grid-station-detail-body');if(!body||!detail)return;
  const {s,results}=detail,site=results.find(r=>r.data?.station)?.data.station;
  if(!site){body.textContent=results.find(r=>r.error)?.error||'当前条件暂无单站配对。';return;}
  const variable=metadata.variables.find(v=>v.id===s.variable),observed=new Map();
  for(const r of results)for(const p of r.data?.series||[])if(!observed.has(p.valid))observed.set(p.valid,p);
  const obs=[...observed.values()].sort((a,b)=>Date.parse(a.valid)-Date.parse(b.valid));
  const lines=results.filter(r=>r.data&&!hidden.has(r.model)).map(r=>({label:r.label,color:r.color,rows:r.data.series.map(p=>({x:Date.parse(p.valid),y:p.fc,n:p.n}))}));
  if(!hidden.has(OBS))lines.push({label:'站点实况',color:'#536575',rows:obs.map(p=>({x:Date.parse(p.valid),y:p.ob}))});
  const toggle=(id,color,text)=>`<button type="button" class="legend-toggle" data-series="${V.esc(id)}" aria-pressed="${!hidden.has(id)}"><i style="background:${color}"></i>${V.esc(text)}</button>`;
  body.innerHTML=`<p class="stations-note">${V.esc(site.id)} ${V.esc(site.name)} · +${s.lead}小时 · ${s.start} — ${s.end} BJT · ${runCaption(s)} · 站高 ${V.num(site.elevation)} m；未作海拔订正。${s.variable==='wind'?'此处检验10米风速，单位m/s。':''}点击图例可隐藏/显示。</p><div class="legend">${results.map(r=>toggle(r.model,r.color,r.label+(r.data?.series.length?'':' · 无配对'))).join('')}${toggle(OBS,'#536575','站点实况')}</div><div id="grid-station-line-host" class="stations-line-host"></div><p class="stations-note">有效时次（总配对）：${V.esc(results.map(r=>`${r.label} ${r.data?.series.length||0}（${r.data?.pair_count??r.data?.series.length??0}）`).join('、'))} · 实况来源：${V.esc([...new Set(obs.map(p=>V.sources[p.source]||p.source))].join('、')||'暂无')}。单站按所选时效比较预报与实况；实况取各模式配对时次的并集${!s.variable.startsWith('rain')?'。':`；阈值 ≥ ${Number(s.threshold??.1)} mm 用于主图与地图评分，序列显示累计量。`}</p>`;
  const scoreCards=results.map(r=>{const stat=(r.data?.stats||[]).find(x=>s.variable.startsWith('rain')?!x.grade&&x.threshold===(s.metric==='PC'?.1:Number(s.threshold??.1)):!x.grade);return `<span>${V.esc(r.label)} · ${V.esc(s.metric)} <strong>${window.VerificationCore.formatMetric(stat?.[s.metric],s.metric)}</strong></span>`;}).join('');
  body.insertAdjacentHTML('afterbegin',`<div class="stations-stats">${scoreCards}</div>`);
  V.mountLineChart(by('grid-station-line-host'),'grid-series',lines,(variable?.label||s.variable)+' / '+(variable?.unit||''),'有效时间 / 北京时间',true);
  body.querySelectorAll('[data-series]').forEach(button=>button.onclick=()=>{const id=button.dataset.series;hidden.has(id)?hidden.delete(id):hidden.add(id);drawDetail();});
 }
 async function load(s){
  controller?.abort();controller=new AbortController();const signal=controller.signal,current=++sequence;
  selection=s;shell();if(!choices().some(m=>m.model===mapModel))mapModel=choices()[0].model;
  by('grid-station-map').textContent='读取站点分布…';
  try{
   const nextMetadata=await V.api('meta',signal);
   if(signal.aborted||current!==sequence)return;
   metadata=nextMetadata;
   // The station chosen in the filter bar is also the one shown in the single-station card.
   const picked=window.GridVerification.selection().scoreStation;
   if(picked&&metadata.stations.some(site=>site.id===picked.id))station=picked.id;
   if(!metadata.stations.some(site=>site.id===station))station=metadata.stations[0]?.id;
   const mapChips=by('grid-station-model');
   mapChips.replaceChildren(...choices().map(m=>{
    const b=document.createElement('button');b.type='button';b.className='model-chip';b.dataset.model=m.model;
    const dot=document.createElement('span');dot.className='grid-station-model-dot';dot.style.background=m.color||'#8aa0ad';
    b.append(dot,m.label);b.setAttribute('aria-pressed',m.model===mapModel);
    b.onclick=()=>{
     if(m.model===mapModel)return;mapModel=m.model;
     mapChips.querySelectorAll('[data-model]').forEach(x=>x.setAttribute('aria-pressed',x.dataset.model===mapModel));
     loadMap(current,signal).catch(error=>{if(!signal.aborted)by('grid-station-map').textContent=error.message;});
    };
    return b;
   }));
   const leadSelect=by('grid-station-lead');
   if(leadSelect){
    leadSelect.replaceChildren(...s.leads.map(lead=>new Option('+'+lead+' 小时',String(lead),false,lead===s.lead)));
    leadSelect.value=String(s.lead);
    leadSelect.onchange=()=>{if(window.GridVerification.setDetailLead)window.GridVerification.setDetailLead(Number(leadSelect.value));};
   }
   const reset=by('grid-station-reset');if(reset)reset.onclick=()=>window.GridVerification.setStationScore(null);
   if(s.threshold!==lastMainThreshold)lastMainThreshold=s.threshold;
   await loadMap(current,signal);
   if(signal.aborted||current!==sequence)return;
   if(station)await loadDetail(current,signal);
   else by('grid-station-detail-body').textContent='当前条件暂无单站配对。';
  }catch(error){if(!signal.aborted&&current===sequence)by('grid-station-map').textContent=error.message;}
 }
 function update(){
  const s=window.GridVerification.selection();selection=s;
  const active=s.page==='map'&&s.station;
  host.hidden=!active;
  if(!active){clearTimeout(timer);controller?.abort();sequence++;key='';return;}
  const next=JSON.stringify(s);
  if(workspace.hidden||!s.model||!s.leads.length||!s.start||!s.end||s.start>s.end){
   clearTimeout(timer);controller?.abort();sequence++;key='';
   if(!workspace.hidden)host.textContent='请选择有效日期、时效和模式以查看站点分布与单站序列。';
   return;
  }
  if(next!==key){
   key=next;clearTimeout(timer);controller?.abort();sequence++;
   host.textContent='读取当前条件的站点资料…';timer=setTimeout(()=>load(s),80);
  }
 }
 document.addEventListener('gridselectionchange',update);
 document.addEventListener('gridstationscopechange',updateScopeNote);
 new MutationObserver(update).observe(workspace,{attributes:true,attributeFilter:['data-active-kind','hidden']});
 window.GridStations={active:()=>!host.hidden};
 update();
})();
