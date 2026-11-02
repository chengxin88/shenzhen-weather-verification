'use strict';
// A reference selector and two station supplements. The primary controls/charts live in app.js.
(()=>{
 const by=id=>document.getElementById(id),workspace=by('grid-workspace'),host=by('grid-station-view');
 const V=window.StationVisuals;
 const WN3='Google WeatherNext 3（集合均值）';
 const sourceModel=model=>window.DemoModels.profile(model)?WN3:model;
 let metadata=null,selection=null,rows=[],station='59493',detailLead=null,mainLeads='',mapModel=null,key='',sequence=0,controller=null,timer=null;
 const modelId=model=>({
  'ECMWF-IFS-9km-OpenMeteo':'ec01','ECMWF-IFS':'ecmwf','NCEP-GFS':'gfs','ECAI':'aifs',
  'GFSAI':'aigfs','GEFS集合平均':'gefs','Google WeatherNext 3（集合均值）':'wn3'
 })[sourceModel(model)]||sourceModel(model);
 // Every model selected for the main chart, in its main-chart colour; the map shows one of them.
 const choices=()=>selection.models?.length?selection.models:[{model:selection.model,label:selection.modelLabel,color:V.colors[modelId(selection.model)]}];
 const chosen=()=>choices().find(m=>m.model===mapModel)||choices()[0];
 const OBS='__obs',hidden=new Set();
 const runCaption=s=>s.hour&&s.hour!=='all'?`${s.hour}时起报（北京时间）`:'全部起报时次';
 function shell(){
  host.innerHTML='<section class="results stations-map-card"><div class="result-heading"><div><h2>站点分布图</h2><p id="grid-station-caption"></p></div><label>地图模式<select id="grid-station-model"></select></label></div><div id="grid-station-map" aria-live="polite"></div><p id="grid-station-map-note" class="stations-note"></p></section><section class="results grid-station-detail"><div class="result-heading"><h2>单站预报与实况</h2><label>固定预报时效<select id="grid-station-lead"></select></label></div><div id="grid-station-detail-body" aria-live="polite"></div></section>';
 }
 function drawMap(){
  const m=chosen();
  by('grid-station-map').innerHTML=V.map(metadata,{model:modelId(m.model),metric:selection.metric,station},rows,m.label);
  by('grid-station-map').querySelectorAll('[data-station-id]').forEach(point=>{
   const select=()=>{station=point.dataset.stationId;drawMap();loadDetail(sequence,controller.signal);};
   point.onclick=select;point.onkeydown=event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();select();}};
  });
 }
 async function loadMap(current,signal){
  const s=selection,m=chosen();by('grid-station-map').textContent='读取站点分布…';
  const data=await V.api('map?'+new URLSearchParams({model:sourceModel(m.model),var:s.variable,threshold:s.threshold||.1,start:s.start,end:s.end,hour:s.hour||'all',leads:s.leads.join(',')}),signal);
  if(signal.aborted||current!==sequence)return;
  rows=data.rows;
  by('grid-station-caption').textContent=`${m.label} · ${s.start} — ${s.end} BJT · ${runCaption(s)} · ${s.leads.map(lead=>'+'+lead).join('/')}小时${s.variable==='t'?'':` · ≥${s.threshold} mm`}`;
  by('grid-station-map-note').textContent=(data.sample||'')+(metadata.update_notice?' '+metadata.update_notice:'')+(window.DemoModels.profile(m.model)?' 当前为 WN3 演示资料，共用 WN3 站点配对，不计入正式排名。':'');
  drawMap();
 }
 let detailSequence=0,detail=null;
 async function loadDetail(current,signal){
  const request=++detailSequence,s={...selection},ident=station,lead=detailLead;
  const body=by('grid-station-detail-body');if(!body)return;
  body.textContent='读取单站配对…';
  try{
   // One series per model; a model without pairs at this station/lead is listed, not fatal.
   const sources=new Map();
   const results=await Promise.all(choices().map(m=>{
    const source=sourceModel(m.model);
    if(!sources.has(source))sources.set(source,V.api('series?'+new URLSearchParams({station:ident,model:source,var:s.variable,lead,start:s.start,end:s.end,hour:s.hour||'all'}),signal));
    return sources.get(source).then(data=>({...m,data}),error=>{if(signal.aborted)throw error;return {...m,data:null,error:error.message};});
   }));
   if(signal.aborted||current!==sequence||request!==detailSequence)return;
   detail={s,lead,results};drawDetail();
  }catch(error){if(!signal.aborted&&current===sequence&&request===detailSequence)body.textContent=error.message;}
 }
 function drawDetail(){
  const body=by('grid-station-detail-body');if(!body||!detail)return;
  const {s,lead,results}=detail,site=results.find(r=>r.data?.station)?.data.station;
  if(!site){body.textContent=results.find(r=>r.error)?.error||'当前条件暂无单站配对。';return;}
  const variable=metadata.variables.find(v=>v.id===s.variable),observed=new Map();
  for(const r of results)for(const p of r.data?.series||[])if(!observed.has(p.valid))observed.set(p.valid,p);
  const obs=[...observed.values()].sort((a,b)=>Date.parse(a.valid)-Date.parse(b.valid));
  const lines=results.filter(r=>r.data&&!hidden.has(r.model)).map(r=>({label:r.label,color:r.color,rows:r.data.series.map(p=>({x:Date.parse(p.valid),y:p.fc}))}));
  if(!hidden.has(OBS))lines.push({label:'站点实况',color:'#536575',rows:obs.map(p=>({x:Date.parse(p.valid),y:p.ob}))});
  const toggle=(id,color,text)=>`<button type="button" class="legend-toggle" data-series="${V.esc(id)}" aria-pressed="${!hidden.has(id)}"><i style="background:${color}"></i>${V.esc(text)}</button>`;
  body.innerHTML=`<p class="stations-note">${V.esc(site.id)} ${V.esc(site.name)} · +${lead}小时 · ${s.start} — ${s.end} BJT · ${runCaption(s)} · 站高 ${V.num(site.elevation)} m；未作海拔订正。点击图例可隐藏/显示。</p><div class="legend">${results.map(r=>toggle(r.model,r.color,r.label+(r.data?.series.length?'':' · 无配对'))).join('')}${toggle(OBS,'#536575','站点实况')}</div>${V.lineChart(lines,(variable?.label||s.variable)+' / '+(variable?.unit||''),'有效时间 / 北京时间',true)}<p class="stations-note">配对数：${V.esc(results.map(r=>`${r.label} ${r.data?.series.length||0}`).join('、'))} · 实况来源：${V.esc([...new Set(obs.map(p=>V.sources[p.source]||p.source))].join('、')||'暂无')}。单站展示所选日期内固定时效的全部有效配对；实况取各模式配对时次的并集${s.variable==='t'?'。':`；阈值 ≥ ${s.threshold} mm 用于主图与地图评分，序列显示累计量。`}</p>`;
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
   if(!metadata.stations.some(site=>site.id===station))station=metadata.stations[0]?.id;
   by('grid-station-model').replaceChildren(...choices().map(m=>{const option=document.createElement('option');option.value=m.model;option.textContent=m.label;return option;}));
   by('grid-station-model').value=mapModel;
   by('grid-station-model').onchange=event=>{mapModel=event.target.value;loadMap(current,signal).catch(error=>{if(!signal.aborted)by('grid-station-map').textContent=error.message;});};
   await loadMap(current,signal);
   if(signal.aborted||current!==sequence)return;
   // Every supported dataset lead is selectable; short-range groups default to +48 h.
   const leads=(s.allLeads||s.leads).filter(lead=>choices().some(m=>metadata.models.find(model=>model.id===modelId(m.model))?.leads.includes(lead)));
   const nextMain=JSON.stringify([s.leadGroup,s.lead,s.leads]),preferredLead=s.leadGroup==='short'?48:s.lead;
   if(nextMain!==mainLeads||!leads.includes(detailLead))detailLead=[preferredLead,s.lead,...s.leads].find(lead=>s.leads.includes(lead)&&leads.includes(lead))??leads[0];
   mainLeads=nextMain;
   by('grid-station-lead').replaceChildren(...leads.map(lead=>{const option=document.createElement('option');option.value=lead;option.textContent='+'+lead+' 小时';return option;}));
   by('grid-station-lead').value=detailLead;
   by('grid-station-lead').onchange=event=>{detailLead=Number(event.target.value);loadDetail(current,signal);};
   if(station&&detailLead!==undefined)loadDetail(current,signal);
   else by('grid-station-detail-body').textContent='当前条件暂无单站配对。';
  }catch(error){if(!signal.aborted&&current===sequence)by('grid-station-map').textContent=error.message;}
 }
 function update(){
  const s=window.GridVerification.selection(),supported=['气温','降水'].includes(workspace.dataset.activeKind||'降水');
  by('grid-source-bar').hidden=!supported;
  const active=supported&&s.station;
  by('grid-source-bar').querySelectorAll('[data-grid-reference]').forEach(button=>button.setAttribute('aria-pressed',button.dataset.gridReference===(active?'station':'grid')));
  workspace.classList.toggle('station-mode',active);host.hidden=!active;
  const next=JSON.stringify(s);
  if(!active||workspace.hidden||!s.model||!s.leads.length||!s.start||!s.end||s.start>s.end){
   clearTimeout(timer);controller?.abort();sequence++;key='';
   if(active&&!workspace.hidden)host.textContent='请选择有效日期、时效和模式以查看站点分布与单站序列。';
   return;
  }
  if(next!==key){
   key=next;clearTimeout(timer);controller?.abort();sequence++;
   host.textContent='读取当前条件的站点资料…';timer=setTimeout(()=>load(s),80);
  }
  if(location.hash.startsWith('#grid'))history.replaceState(null,'',active?'#grid&ref=station':'#grid');
 }
 by('grid-source-bar').querySelectorAll('[data-grid-reference]').forEach(button=>button.onclick=async()=>{
  await window.GridVerification.setReference(button.dataset.gridReference);update();
 });
 window.addEventListener('hashchange',async()=>{
  if(location.hash.startsWith('#grid')){
   await window.GridVerification.setReference(/(^|&)ref=station(&|$)/.test(location.hash.slice(1))?'station':'grid');update();
  }
 });
 document.addEventListener('gridselectionchange',update);
 new MutationObserver(update).observe(workspace,{attributes:true,attributeFilter:['data-active-kind','hidden']});
 window.GridStations={active:()=>!workspace.hidden&&workspace.classList.contains('station-mode')};
 update();
})();
