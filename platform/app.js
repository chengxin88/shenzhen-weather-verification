'use strict';
/* Detail analysis: one element → one metric per page.
   Scoring itself still lives in core.js; every number on screen, in the
   table and in the CSV comes from the same grouped rows. */
const $=id=>document.getElementById(id), C=VerificationCore;
const WN3='Google WeatherNext 3（集合均值）';
const XJ_MODEL='WN3 订正（LightGBM）';
const EC9KM='ECMWF-IFS-9km-OpenMeteo';
const palette={'WN3 订正（LightGBM）':'#b8860b','ECMWF-IFS-9km-OpenMeteo':'#117f8b','ECMWF-IFS':'#0072b2','ECAI':'#009e73','ECMWF-ENSEMBLES平均':'#56b4e9','NCEP-GFS':'#d55e00','GFSAI':'#cc79a7','GEFS集合平均':'#8a5a2b','Google WeatherNext 2':'#e69f00','Google WeatherNext 3（集合均值）':'#8b5cf6'};
const names={'ECMWF-IFS-9km-OpenMeteo':'EC 9 km','ECMWF-IFS':'EC','ECAI':'ECAI','ECMWF-ENSEMBLES平均':'EC 集合平均','NCEP-GFS':'GFS','GFSAI':'GFSAI','GEFS集合平均':'GFS 集合平均','Google WeatherNext 2':'WN2 集合平均','Google WeatherNext 3（集合均值）':'WN3 集合平均'};
const ids={'ECMWF-IFS':'IFS','ECAI':'AIFS','ECMWF-ENSEMBLES平均':'ENS','GFSAI':'AIGFS','GEFS集合平均':'GEFS'};
const label=m=>window.DemoModels.profile(m)?window.DemoModels.label(m):names[m]?(ids[m]?`${names[m]}（${ids[m]}）`:names[m]):m;
const displayModel=m=>label(m);
const aiModels=new Set(['ECAI','GFSAI','Google WeatherNext 2','Google WeatherNext 3（集合均值）','wn3-demo:pangu-short-range','wn3-demo:zhiji']);
const modelRank=['ECMWF-IFS','ECMWF-IFS-9km-OpenMeteo','ECMWF-ENSEMBLES平均','NCEP-GFS','GEFS集合平均','ECAI','GFSAI','Google WeatherNext 2','Google WeatherNext 3（集合均值）'];
const numericFirst=models=>models.map((m,i)=>[m,i,modelRank.includes(m)?modelRank.indexOf(m):99]).sort((a,b)=>aiModels.has(a[0])-aiModels.has(b[0])||a[2]-b[2]||a[1]-b[1]).map(x=>x[0]);
const displayText=text=>String(text??'').replaceAll('集合均值','集合平均');
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(v,d=3)=>Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:d,minimumFractionDigits:d}):'—';
const unique=x=>[...new Set(x)];
const finite=Number.isFinite;
const short=t=>t?t.slice(5,10)+' '+t.slice(11,16):'—';
const full=t=>t?t.slice(0,16).replace('T',' '):'—';
const seriesColors=['#0072b2','#009e73','#d55e00','#cc79a7','#e69f00','#56b4e9','#000000','#999999'];

/* ---------- element groups and metric pages ---------- */
const GROUPS=[
  {id:'降水',kind:'降水',label:'降水',icon:'☂'},
  {id:'气温',kind:'气温',label:'气温',icon:'°C'},
  {id:'地面气压',kind:'地面',element:'mslp',label:'地面气压',icon:'⇣'},
  {id:'10米风',kind:'地面',element:'wind10',label:'10 米风',icon:'≋'},
  {id:'100米风',kind:'地面',element:'wind100',label:'100 米风',icon:'≋'},
  {id:'高空高度场',kind:'环流',field:'height',label:'高空高度场',icon:'≋'},
  {id:'高空风场',kind:'环流',field:'wind',label:'高空风场',icon:'≋'}
];
const RAIN_PAGES=[
  {key:'PC',label:'晴雨准确率'},
  {key:'TS',label:'TS 评分'},
  {key:'ETS',label:'ETS 评分'},
  {key:'FAR',label:'空报率'},
  {key:'MR',label:'漏报率'},
  {key:'BIAS',label:'预报偏差'},
  {key:'performance',label:'评分综合图'}
];
const TEMP_ORDER=['ACC2','ACC1','MAE','RMSE','ME','CC'];
const ERROR_ORDER=['RMSE','MAE','ME','CC'];
const metricText={
  ACC2:'≤2℃准确率',ACC1:'≤1℃准确率',MAE:'平均绝对误差',RMSE:'均方根误差',ME:'平均误差',CC:'相关系数',VRMSE:'风矢量均方根误差',
  PC:'晴雨准确率',TS:'TS 评分',ETS:'ETS 评分',FAR:'空报率',MR:'漏报率',BIAS:'预报偏差',performance:'评分综合图'
};
const metricHelp={
  PC:'按 ≥0.1 mm 判断有无降水；实况微量而预报有雨算报对。',
  TS:'报对的站次 ÷（报对 + 空报 + 漏报），越接近 1 越好。',
  ETS:'扣除随机命中后越接近 1 越好；需要完整样本总数。',
  FAR:'空报站次 ÷ 所有预报有事件的站次，越接近 0 越好。',
  MR:'漏报站次 ÷ 所有实况有事件的站次，越接近 0 越好。',
  BIAS:'（报对 + 空报）÷（报对 + 漏报），越接近 1 越好；大于 1 表示预报偏多。',
  ACC2:'预报误差不超过 2℃ 的站次比例，越高越好。',
  ACC1:'预报误差不超过 1℃ 的站次比例，越高越好。',
  MAE:'每次预报与实况的差值取绝对值后求平均，越小越好。',
  RMSE:'误差平方平均后开方，越小越好；对大误差更敏感。',
  ME:'预报减实况的平均值，越接近 0 越好；正值表示预报偏高。',
  CC:'空间分布相关系数，越接近 1 越好；不代表量值没有偏差。',
  VRMSE:'东西、南北风分量误差合并后的均方根，单位 m/s，越小越好。',
  performance:'同时看命中率、成功率（1−空报率）与 TS、BIAS。'
};

/* ---------- state ---------- */
let gridReference=/(^|&)ref=station(&|$)/.test(location.hash.slice(1))||(!location.hash.includes('ref=')&&localStorage.getItem('gridReference')==='station')?'station':'grid';
let stationLoading=null,referenceSequence=0;
let rowsLoader=null,datasetRows=[],rowsReadyId=null,rowsSequence=0,stationRowsCache=new Map(),stationRowsRange=null;
let automaticModels=true,selectedBeforeXj=null;
let catalog=null,ds=null,kind='降水',groupId='降水',metricKey='TS',page='metric',chartMode='lead';
let selected=new Set(),stateLead=24,datePreset=7,timeCommon=false;
let tableState={rows:[],columns:[],xKey:'lead',xs:[]};
let pendingMetric=null,commonFallback=false;
let circulationField='height';
let stationRegion='all',scoreStation=null;
// Single-station picker next to the region buttons. The station list arrives lazily
// from the station metadata; a station id in the address bar is honoured on arrival.
let stationSites=null,stationSitesLoading=null;
const STATION_ID=/^[\w-]{1,16}$/;
const stationPickText=site=>`${site.name||site.id}（${site.id}）`;
{
  const wanted=new URLSearchParams(location.hash.slice(1)).get('station')||'';
  if(gridReference==='station'&&STATION_ID.test(wanted))scoreStation={id:wanted,name:wanted};
}
let preferences={datasets:{},groupDatasets:{}};
try{preferences=JSON.parse(localStorage.getItem('szverify-workbench-v2'))||preferences;}catch{}
preferences.datasets||={};preferences.groupDatasets||={};preferences.referenceByKind||={};
for(const saved of Object.values(preferences.datasets)){
  if(!saved||typeof saved!=='object')continue;
  delete saved.start;delete saved.end;delete saved.valid;delete saved.view;delete saved.scoreScope;
  delete saved.leadMode;delete saved.leadGroup;delete saved.timeCommon;
}
let stationRegionLabels={all:'南方地区',hn:'华南地区',hun:'湖南地区',gba:'粤港澳大湾区',hk:'香港',sz:'深圳',xj:'新疆巴里坤'};

const currentGroup=()=>GROUPS.find(g=>g.id===groupId)||GROUPS[0];
const stationDataset=()=>ds?.source==='station';
const stationScoreActive=()=>stationDataset()&&Boolean(scoreStation);
const stationScopeLabel=()=>stationScoreActive()?`${scoreStation.name}（${scoreStation.id}）`:stationRegionLabels[stationRegion]||ds?.region||'';
const referenceMatches=d=>!['降水','气温','地面'].includes(d.kind)||(d.source==='station')===(gridReference==='station');
const groupMatchesDataset=(g,d)=>Boolean(d)&&d.kind===g.kind&&(!g.element||d.element===g.element)&&(!g.field||C.circulationMetrics(g.field).some(m=>(d.score_metrics||[]).includes(m)));
const allDatasetsForGroup=g=>!catalog||!g?[]:catalog.datasets.filter(d=>groupMatchesDataset(g,d));
const datasetsForGroup=(g=catalog?GROUPS.find(x=>x.id===groupId):null)=>{
  if(!catalog||!g)return [];
  return allDatasetsForGroup(g).filter(referenceMatches);
};
const gridAvailableFor=(g=currentGroup())=>Boolean(catalog&&catalog.datasets.some(d=>d.source!=='station'&&groupMatchesDataset(g,d)));
const stationKind=g=>['降水','气温'].includes(g.kind)||g.kind==='地面'&&['mslp','wind10'].includes(g.element);
const referenceSwitchable=g=>stationKind(g)&&gridAvailableFor(g);
function preferredDatasetFor(g=currentGroup()){
  const list=datasetsForGroup(g);
  const saved=preferences.groupDatasets[g.id];
  return list.find(d=>d.id===saved)||list.find(d=>d.id===preferences.dataset)||list.find(d=>d.id===ds?.id)||list[0];
}
function availableGroups(){
  if(!catalog)return GROUPS.slice(0,2);
  return GROUPS.filter(g=>allDatasetsForGroup(g).length);
}
function groupMetricDefs(g=currentGroup()){
  const sample=ds&&groupMatchesDataset(g,ds)?ds:preferredDatasetFor(g)||allDatasetsForGroup(g)[0];
  if(g.kind==='降水'){
    const available=sample?C.metricsFor(sample):['TS'];
    return RAIN_PAGES.filter(m=>m.key==='PC'?Boolean(sample?.has_trace):m.key==='ETS'?available.includes('ETS'):true);
  }
  const order=g.kind==='气温'?TEMP_ORDER:g.kind==='环流'?C.circulationMetrics(g.field):g.kind==='地面'&&sample&&sample.source!=='station'&&String(sample.element||'').startsWith('wind')?['VRMSE']:ERROR_ORDER;
  const catalogMetrics=sample?.score_metrics||[];
  const available=g.kind==='环流'||order.length===1&&order[0]==='VRMSE'
    ?new Set(order.filter(m=>!catalogMetrics.length||catalogMetrics.includes(m)))
    :new Set(sample?C.metricsFor(sample):[]);
  const defs=order.filter(m=>available.has(m)).map(m=>({key:m,label:metricText[m]}));
  if(!defs.length)order.forEach(m=>defs.push({key:m,label:metricText[m]}));
  return defs;
}
const metricTitle=m=>m==='CC'&&stationScoreActive()?'单站时间相关 CC':(metricText[m]||m);
const needsThreshold=()=>kind==='降水'&&['TS','ETS','FAR','MR','BIAS'].includes(metricKey);
const unit=()=>C.percentMetrics.includes(metricKey)?'%':metricKey==='CC'?'':kind==='气温'?'℃':kind==='地面'?ds?.unit||'':kind==='环流'?(metricKey==='VRMSE'?'m/s':'m'):'';
const elementName=()=>kind==='地面'?(ds?.label||currentGroup().label):kind==='环流'?(circulationField==='wind'?'风场':'高度场'):kind;
const labelFor=d=>d.kind==='降水'?`${d.accumulation}小时累计降水`:d.kind==='地面'?d.label:d.kind==='环流'?`${d.level} hPa · ${circulationField==='wind'?'风场':'高度场'}`:'2米气温';
const exportElement=()=>(stationDataset()?'站点实况_':'')+(kind==='环流'?`${elementName()}_${ds?.level||''}hPa`:kind==='地面'?elementName():kind);
const color=m=>window.DemoModels.profile(m)?window.DemoModels.color(m):palette[m]||seriesColors[Math.max(0,(ds?.leads||[]).indexOf(Number(String(m).replace(/[^\d]/g,'')))||0)%seriesColors.length];
const defaultModels=(d,available=null)=>{const real=d.models.filter(m=>!window.DemoModels.profile(m)&&(!available||available.has(m)));return real.includes(EC9KM)?real.filter(m=>m!=='ECMWF-IFS'):real;};

/* ---------- url / page ids ---------- */
const pageId=()=>page==='info'?'info':page==='map'?`${groupId}:map`:`${groupId}:${metricKey}`;
function writeHash(){
  if(!ds)return;
  const params={page:pageId(),ref:gridReference};
  if(stationScoreActive())params.station=scoreStation.id;
  try{history.replaceState(null,'','#grid&'+new URLSearchParams(params));}catch{}
}
function parseHashPage(hash){
  const text=hash.startsWith('#grid')?hash.slice(1).split('&').slice(1).join('&'):hash.slice(1);
  return new URLSearchParams(text);
}
function hashTarget(params){
  const p=params.get('page');
  if(p==='info')return {page:'info'};
  if(p&&p.endsWith(':map'))return {page:'map',groupId:p.slice(0,-4)};
  if(p){
    const index=p.indexOf(':');
    if(index>0)return {groupId:p.slice(0,index),metric:p.slice(index+1)};
  }
  const view=params.get('view'),metric=params.get('metric');
  if(view==='info')return {page:'info'};
  if(view==='performance')return {metric:'performance'};
  if(view==='time')return {metric:metric||null,mode:'date'};
  if(view==='multi'||view==='single'||view==='lead'||view==='matrix')return {metric:metric||null,mode:'lead'};
  if(metric)return {metric};
  return null;
}

/* ---------- navigation ---------- */
function navPageButton(label,attrs,active){
  return `<button type="button" class="detail-metric${active?' active':''}" ${active?'aria-current="page"':''} ${attrs}>${esc(label)}</button>`;
}
function renderNav(){
  const tree=$('detail-nav-tree');if(!tree)return;
  const groups=availableGroups();
  if(!groups.some(g=>g.id===groupId))groupId=groups[0]?.id||groupId;
  const activeGroup=groups.find(g=>g.id===groupId)||groups[0];
  if(!activeGroup)return;
  const defs=groupMetricDefs(activeGroup);
  const activeMetric=page==='metric'?metricKey:null;
  const html=groups.map(g=>`<div class="detail-group${g.id===groupId?' active':''}">
    <button type="button" class="detail-group-title nav-item${g.id===groupId?' active':''}" data-kind="${esc(g.id)}" aria-pressed="${g.id===groupId}" title="切换到${esc(g.label)}"><span aria-hidden="true">${esc(g.icon)}</span>${esc(g.label)}</button>
    ${g.id===activeGroup.id?`<div class="detail-subnav">
      ${defs.map(m=>navPageButton(m.label,`data-metric="${esc(m.key)}" data-page-group="${esc(g.id)}"`,activeMetric===m.key)).join('')}
      ${stationKind(g)?navPageButton('站点分布',`data-page="map" data-page-group="${esc(g.id)}"`,page==='map'):''}
      ${navPageButton('说明','data-page="info" data-page-group="' + esc(g.id) + '"',page==='info')}
    </div>`:''}
  </div>`).join('');
  tree.innerHTML=html;
  const select=$('detail-page-select');
  if(select){
    const value=pageId();
    const options=[];
    for(const g of groups){
      const defsFor=groupMetricDefs(g);
      options.push(`<optgroup label="${esc(g.label)}">${defsFor.map(m=>`<option value="${esc(g.id+':'+m.key)}">${esc(g.label+' · '+m.label)}</option>`).join('')}${stationKind(g)?`<option value="${esc(g.id+':map')}">${esc(g.label+' · 站点分布')}</option>`:''}</optgroup>`);
    }
    options.push(`<optgroup label="其他"><option value="info">说明</option></optgroup>`);
    select.innerHTML=options.join('');
    if([...select.options].some(o=>o.value===value))select.value=value;
  }
}

/* ---------- date / controls ---------- */
function stationWindow(dataset=ds){
  if(dataset?.source!=='station'||!dataset.region_nmic_majority?.[stationRegion]||!dataset.nmic_end||!dataset.window_end)return null;
  const end=Math.min(Date.parse(dataset.window_end),Date.parse(dataset.nmic_end));
  if(!Number.isFinite(end)||end>=Date.parse(dataset.window_end))return null;
  return new Date(end+8*3600000).toISOString().slice(0,10);
}
function stationWindowNote(){
  const end=stationWindow();
  return end?`国家站资料到 ${Number(end.slice(5,7))} 月 ${Number(end.slice(8,10))} 日，默认和快捷日期窗口截止到这一天（北京时间）。`:'';
}
function stationDateBounds(dataset){
  const end=stationWindow(dataset),first=end?C.calendarWindow(30,Date.parse(end+'T12:00:00+08:00')).start:null;
  return {start:first&&first<dataset.start.slice(0,10)?first:dataset.start.slice(0,10),
    end:end&&end>dataset.end.slice(0,10)?end:dataset.end.slice(0,10)};
}
function useSavedDates(dataset,saved){
  return dataset.source!=='station'||!saved.stationDatePreset||saved.stationWindowEnd===stationWindow(dataset);
}
function setDates(days){
  if(!ds)return;
  datePreset=days;
  const nmicEnd=stationWindow(),latest=nmicEnd||((stationDataset()||ds.reference?.includes('ERA5'))&&ds.end?ds.end.slice(0,10):null),anchored=latest&&(nmicEnd||latest<C.calendarWindow(1).end)?Date.parse(latest+'T12:00:00+08:00'):Date.now();
  const range=C.calendarWindow(days,anchored);
  $('start').value=range.start;$('end').value=range.end;clampStationDates();
}
function clampStationDates(){
  if(!ds||(!stationDataset()&&!ds.reference?.includes('ERA5')))return;
  const bounds=stationDateBounds(ds);
  for(const id of ['start','end']){const el=$(id);el.min=bounds.start;el.max=bounds.end;if(el.value)el.value=el.value<el.min?el.min:el.value>el.max?el.max:el.value;}
}
function hourLabel(){return $('hour').value==='all'?'全部起报时次':$('hour').value+'时起报';}
function scopeLabel(){return stationDataset()?stationScopeLabel():ds?.region||'';}
function thresholdValue(){if(kind!=='降水')return null;if(metricKey==='PC')return .1;const value=Number($('threshold').value);return Number.isFinite(value)?value:(ds?.thresholds?.[0]??.1);}
function validHourText(lead){
  const startHour=Number($('hour').value);
  if(!Number.isFinite(startHour))return '';
  const total=startHour+Number(lead),hour=total%24,days=Math.floor(total/24);
  return String(hour).padStart(2,'0')+(days>0?`(+${days})`:'');
}
function setHour(value){
  $('hour').value=['all','08','20'].includes(value)?value:'all';render();
}
function setThreshold(value){
  const choices=[...$('threshold').options].map(o=>Number(o.value));
  $('threshold').value=String(choices.includes(Number(value))?Number(value):(choices[0]??.1));render();
}
function renderFilter(){
  if(!ds)return;
  const presets=$('date-presets');
  presets.replaceChildren(...[3,7,15].map(days=>{const b=document.createElement('button');b.type='button';b.dataset.days=String(days);b.textContent=days===15?'近15天':`近${days}天`;b.className=datePreset===days?'chosen':'';b.setAttribute('aria-pressed',String(datePreset===days));b.onclick=()=>{setDates(days);render();};return b;}));
  const hours=$('hour-switch');
  hours.replaceChildren(...[['all','全部'],['08','08时'],['20','20时']].map(([value,text])=>{const b=document.createElement('button');b.type='button';b.textContent=text;b.dataset.hour=value;b.setAttribute('aria-pressed',String($('hour').value===value));b.onclick=()=>setHour(value);return b;}));
  const rain=kind==='降水';
  $('accumulation-field').hidden=!rain;
  if(rain){
    const values=unique((catalog?.datasets||[]).filter(d=>d.kind==='降水'&&referenceMatches(d)).map(d=>d.accumulation)).sort((a,b)=>a-b);
    $('accumulation-switch').replaceChildren(...values.map(hours=>{const b=document.createElement('button');b.type='button';b.textContent=hours+'小时';b.title=hours+'小时累计';b.dataset.accumulation=String(hours);b.setAttribute('aria-pressed',String(hours===ds.accumulation));b.onclick=()=>chooseAccumulation(hours);return b;}));
  }
  const threshold=needsThreshold();
  $('threshold-field').hidden=!threshold||chartMode==='threshold';
  if(rain){
    const choices=ds.thresholds||[];
    if(metricKey==='PC')$('threshold').value='0.1';
    else if(!choices.includes(Number($('threshold').value)))$('threshold').value=String(choices[0]??.1);
    $('threshold-switch').replaceChildren(...choices.map(t=>{const b=document.createElement('button');b.type='button';b.textContent='≥'+t+(metricKey==='PC'?'':'');b.dataset.threshold=String(t);b.setAttribute('aria-pressed',String(Number($('threshold').value)===Number(t)));b.onclick=()=>setThreshold(t);return b;}));
  }
  const g=currentGroup(),switchable=referenceSwitchable(g);
  $('reference-field').hidden=!switchable;
  if(switchable){
    $('reference-switch').replaceChildren(...[['station','站点实况'],['grid','格点分析场']].map(([value,text])=>{const b=document.createElement('button');b.type='button';b.textContent=text;b.dataset.reference=value;b.setAttribute('aria-pressed',String(gridReference===value));b.onclick=()=>setReference(value);return b;}));
  }
  const refs=unique(datasetsForGroup(g).map(d=>d.reference)).sort();
  const refField=$('reference-dataset-field');
  refField.hidden=refs.length<=1;
  if(refs.length>1){
    const select=$('reference-dataset');
    select.replaceChildren(...refs.map(ref=>new Option(ref,ref,false,ref===ds.reference)));
    select.value=ds.reference;
    select.onchange=()=>chooseReference(select.value);
  }
  $('circulation-level-field').hidden=kind!=='环流';
  if(kind==='环流'){
    const levels=unique(datasetsForGroup(g).filter(d=>d.reference===ds.reference).map(d=>d.level)).sort((a,b)=>b-a);
    const select=$('circulation-level');
    select.replaceChildren(...levels.map(level=>new Option(level+' hPa',String(level),false,level===ds.level)));
    select.onchange=()=>chooseLevel(Number(select.value));
  }
  const m=metricKey;
  $('chart-mode-toggle').querySelectorAll('[data-chart-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.chartMode===chartMode)));
  $('date-lead-label').hidden=!(page==='metric'&&chartMode!=='lead');
  if(page==='metric'&&chartMode!=='lead'){
    const select=$('date-lead');
    select.replaceChildren(...displayLeads(ds).map(l=>new Option('+'+l+' 小时',String(l),false,l===stateLead)));
    select.value=String(stateLead);
  }
  $('filter-note').textContent=`${currentGroup().label} · ${scopeLabel()} · ${$('start').value} — ${$('end').value} BJT · ${hourLabel()}${rain&&needsThreshold()&&chartMode!=='threshold'?` · ≥${thresholdValue()} mm`:''}${ds.reference?` · ${stationDataset()?'站点实况':ds.reference}`:''}`;
  if(stationWindowNote())$('filter-note').append(document.createElement('br'),stationWindowNote());
}

/* ---------- dataset loading ---------- */
function rowsLoaderFor(next){
  return C.createRowsLoader(next,async path=>{
    const query=new URLSearchParams(path.split('?')[1]);
    if(Array.isArray(next.rows))return {dataset:query.get('dataset'),rows:next.rows.filter(r=>r.dataset===query.get('dataset')),snapshot_id:next.snapshot_id};
    query.set('snapshot',next.snapshot_id);
    const response=await fetch('./api/rows?'+query,{cache:'default',signal:AbortSignal.timeout(65000)});
    if(!response.ok)throw Error('HTTP '+response.status);
    return response.json();
  });
}
async function chooseAccumulation(hours){
  const target=catalog.datasets.find(d=>d.kind==='降水'&&d.accumulation===hours&&d.source===ds.source&&d.reference===ds.reference&&referenceMatches(d))||catalog.datasets.find(d=>d.kind==='降水'&&referenceMatches(d)&&d.accumulation===hours);
  if(!target)return;
  pendingMetric=metricKey;
  $('dataset').value=target.id;
  await setDataset();
}
async function chooseLevel(level){
  const target=datasetsForGroup(currentGroup()).find(d=>d.level===level&&d.reference===ds.reference);
  if(!target)return;
  pendingMetric=metricKey;
  $('dataset').value=target.id;
  await setDataset();
}
async function chooseReference(reference){
  const list=datasetsForGroup(currentGroup()),g=currentGroup();
  const target=list.find(d=>d.reference===reference&&(d.accumulation??null)===(ds.accumulation??null)&&(d.element??null)===(g.element??ds.element??null)&&(kind!=='环流'||d.level===ds.level));
  if(!target)return;
  pendingMetric=metricKey;
  $('dataset').value=target.id;
  await setDataset();
}
function options(el,items,value){el.replaceChildren(...items.map(([v,text])=>{const o=document.createElement('option');o.value=v;o.textContent=text;return o;}));if(items.some(x=>String(x[0])===String(value)))el.value=value;}
async function loadGroup(g,metric){
  if(!catalog)return false;
  if(!(await window.GridVerification.open()))return false;
  if(gridReference==='station'&&['降水','气温','地面'].includes(g.kind))await ensureStationRows();
  gridReference=gridReference==='station'?'station':'grid';
  const list=datasetsForGroup(g);
  if(!list.length){
    $('chart').innerHTML=empty('该要素暂无可用归档');
    return false;
  }
  const target=preferredDatasetFor(g)||list[0];
  pendingMetric=metric??null;
  options($('dataset'),list.map(d=>[d.id,labelFor(d)]),target.id);
  groupId=g.id;kind=g.kind;
  if(g.field)circulationField=g.field;
  $('grid-workspace').dataset.activeKind=g.id;
  $('page-title').textContent=`${g.label} · ${metricTitle(pendingMetric||groupMetricDefs(g)[0]?.key||metricKey)}`;
  $('page-subtitle').textContent='读取资料…';
  return setDataset();
}
async function setDataset(){
  const previousDataset=ds?.id;
  ds=catalog.datasets.find(d=>d.id===$('dataset').value);
  if(ds?.id!==previousDataset)automaticModels=true;
  if(!ds){$('chart').innerHTML=empty('该要素暂无可用归档');return false;}
  const target=ds,current=++rowsSequence,loader=rowsLoader;
  const initialRange=target.source==='station'?stationDateRange(target):null;
  rowsReadyId=null;datasetRows=[];tableState={rows:[],columns:[],xKey:'lead',xs:[]};
  $('chart').innerHTML=empty('读取资料…','正在读取'+kind+'检验结果。');
  $('results-table').innerHTML='';$('legend').innerHTML='';$('table-note').textContent='读取资料…';$('updated').textContent='';
  $('export-csv').disabled=true;$('export-svg').disabled=true;$('load-error').hidden=true;
  $('catalog-status-wrap').hidden=false;$('catalog-status').textContent='读取'+kind+'资料…';
  $('grid-workspace').setAttribute('aria-busy','true');
  try{
    const loaded=target.source==='station'?{datasets:[target],rows:await loadStationDataset(target,initialRange)}
      :C.demoCatalog(await loader.load(target),window.DemoModels.profiles(),WN3);
    if(current!==rowsSequence||loader!==rowsLoader)return false;
    ds=loaded.datasets.find(d=>d.id===target.id);
    if(!ds)throw Error('暂无可识别的检验归档');
    catalog.datasets=catalog.datasets.map(d=>d.id===ds.id?ds:d);
    datasetRows=loaded.rows.filter(r=>r.dataset===ds.id);rowsReadyId=ds.id;
    stationRowsRange=initialRange;
    preferences.groupDatasets[groupId]=ds.id;
  }catch(error){
    if(current!==rowsSequence||loader!==rowsLoader)return false;
    $('load-error').hidden=false;$('load-error').textContent='检验资料暂时无法读取，请稍后点击“刷新资料”重试。';
    $('catalog-status').textContent='资料暂不可用';$('chart').innerHTML=empty('资料暂不可用','请稍后刷新或更换条件。');
    return false;
  }finally{if(current===rowsSequence)$('grid-workspace').setAttribute('aria-busy','false');}
  const saved=preferences.datasets[ds.id]||{};
  const remembered=saved.models||defaultModels(ds);
  const oldRoster=saved.knownModels||ds.models;
  const introduced=ds.models.filter(m=>!window.DemoModels.profile(m)&&!oldRoster.includes(m)&&datasetRows.some(r=>r.model===m));
  selected=new Set([...remembered,...introduced].filter(m=>ds.models.includes(m)));
  const defs=groupMetricDefs(currentGroup());
  const wanted=(pendingMetric===null?[saved.metric]:[pendingMetric,saved.metric,metricKey]).find(m=>m&&defs.some(d=>d.key===m));
  pendingMetric=null;
  metricKey=wanted||defs[0]?.key||'TS';
  if(defs.some(d=>d.key==='performance')&&metricKey==='performance')chartMode='lead';
  options($('metric'),defs.map(d=>[d.key,d.label]),metricKey);
  options($('threshold'),(ds.thresholds||[]).map(t=>[t,'≥ '+t]),saved.threshold??ds.thresholds?.[0]??.1);
  $('metric').value=metricKey;
  const lead=saved.lead??24;
  const shownLeads=displayLeads(ds);
  stateLead=shownLeads.includes(Number(lead))?Number(lead):(shownLeads.includes(24)?24:(shownLeads[0]??24));
  options($('lead'),shownLeads.map(l=>[l,'+'+l+' 小时']),stateLead);
  $('hour').value=['all','08','20'].includes(saved.hour)?saved.hour:'all';
  $('common').checked=true;$('across-leads').checked=false;
  const savedPreset=[3,7,15].includes(Number(saved.days))?Number(saved.days):7;
  setDates(savedPreset);
  if(saved.start&&saved.end&&useSavedDates(ds,saved)){
    const presetStart=$('start').value,presetEnd=$('end').value;
    $('start').value=saved.start;$('end').value=saved.end;clampStationDates();
    datePreset=$('start').value===presetStart&&$('end').value===presetEnd?savedPreset:null;
  }
  $('grid-workspace').dataset.activeKind=groupId;
  renderNav();render();
  $('catalog-status').textContent='';$('catalog-status-wrap').hidden=true;
  return true;
}
function stationDateRange(dataset){
  const saved=preferences.datasets[dataset.id]||{},preset=[3,7,15].includes(Number(saved.days))?Number(saved.days):7;
  const nmicEnd=stationWindow(dataset),latest=dataset.end.slice(0,10),today=C.calendarWindow(1).end;
  const now=nmicEnd||latest<today?Date.parse((nmicEnd||latest)+'T12:00:00+08:00'):Date.now();
  const range=C.calendarWindow(preset,now);
  const bounds=stationDateBounds(dataset);
  for(const id of ['start','end']){
    if(saved[id]&&useSavedDates(dataset,saved))range[id]=[saved[id],today].sort()[0];
    range[id]=range[id]<bounds.start?bounds.start:range[id]>bounds.end?bounds.end:range[id];
  }
  const leads=displayLeads(dataset);
  if(leads.length)range.leads=leads.join(',');
  if(scoreStation)range.station=scoreStation.id;
  else if(stationRegion&&stationRegion!=='all')range.region=stationRegion;
  return range;
}
function loadStationDataset(dataset,range){
  const cache=stationRowsCache;
  const key=dataset.id+'|'+range.start+'|'+range.end+'|'+(range.leads??'all')+'|'+(range.region||'all')+'|'+(range.station||'');
  if(!cache.has(key))cache.set(key,(async()=>{
    const response=await fetch('./api/stations/grid-rows?'+new URLSearchParams({dataset:dataset.id,...range,...(dataset.grid_snapshot?{generation:dataset.grid_snapshot}:{})}),{cache:'default',signal:AbortSignal.timeout(65000)});
    if(!response.ok)throw Error('站点图表资料暂不可用');
    const rows=await response.json();
    if(!Array.isArray(rows)||rows.some(r=>r.dataset!==dataset.id))throw Error('站点图表资料暂不可用');
    return rows;
  })().catch(error=>{cache.delete(key);throw error;}));
  return cache.get(key);
}
async function ensureStationRows(){
  if(stationLoading)return stationLoading;
  const target=catalog;
  stationLoading=(async()=>{
    const response=await fetch('./api/stations/grid-datasets',{cache:'no-store'});
    if(!response.ok)throw Error('站点资料暂不可用');
    const datasets=await response.json();if(!datasets.length)throw Error('站点共用图表尚未完成离线评分');
    if(target!==catalog)return;
    target.datasets.push(...C.demoCatalog({datasets},window.DemoModels.profiles(),WN3).datasets);
    target.origins=[...(target.origins||[]),...datasets];
  })().catch(error=>{stationLoading=null;throw error;});
  return stationLoading;
}

/* ---------- rows ---------- */
function baseRows(models,ignoreThreshold=false){
  if(!ds)return [];
  const start=$('start').value,end=$('end').value,hour=$('hour').value;
  let rows=datasetRows.filter(r=>{
    if(kind==='降水'&&r.grade)return false;
    if(kind==='环流'&&!C.circulationHasScore(r,circulationField))return false;
    if(metricKey!=='performance'&&!C.eligible(r,metricKey))return false;
    if(r.valid.slice(0,10)<start||r.valid.slice(0,10)>end)return false;
    if(hour!=='all'&&r.run.slice(11,13)!==hour)return false;
    if(kind==='降水'&&!ignoreThreshold&&metricKey!=='performance'){
      if(metricKey==='PC'){if(r.threshold!==.1)return false;}
      else if(chartMode==='threshold'){if(!Number.isFinite(r.threshold))return false;}
      else if(needsThreshold()&&r.threshold!==thresholdValue())return false;
    }
    if(kind==='降水'&&metricKey==='performance'&&!Number.isFinite(r.threshold))return false;
    return true;
  });
  if(chartMode!=='lead'&&page==='metric')rows=rows.filter(r=>r.lead===stateLead);
  return C.demoRows(rows,models,window.DemoModels.profiles(),WN3);
}
function scopedRows(models){
  let rows=baseRows(models);
  commonFallback=false;
  if($('common').checked){
    const common=C.commonTimes(rows,models,{metric:metricKey==='performance'?undefined:metricKey});
    if(common.length)rows=common;
    else commonFallback=true;
  }
  return rows;
}
function availableRowsForModels(){
  if(!ds)return [];
  let rows=datasetRows.filter(r=>{
    if(kind==='降水'&&r.grade)return false;
    if(kind==='环流'&&!C.circulationHasScore(r,circulationField))return false;
    if(metricKey!=='performance'&&!C.eligible(r,metricKey))return false;
    if(r.valid.slice(0,10)<$('start').value||r.valid.slice(0,10)>$('end').value)return false;
    if($('hour').value!=='all'&&r.run.slice(11,13)!==$('hour').value)return false;
    if(kind==='降水'&&metricKey!=='performance'){
      if(metricKey==='PC'&&r.threshold!==.1)return false;
      if(needsThreshold()&&chartMode!=='threshold'&&r.threshold!==thresholdValue())return false;
    }
    return true;
  });
  return C.demoRows(rows,ds.models,window.DemoModels.profiles(),WN3);
}

/* ---------- model chips ---------- */
function modelControls(availableRows=null){
  if(!ds)return;
  const models=numericFirst([...(ds.supported_models||ds.models)]);
  const buttons=models.map(m=>{
    const b=document.createElement('button');b.type='button';b.className='model-chip';b.dataset.model=m;
    const archived=ds.models.includes(m),available=archived&&(availableRows===null||availableRows.some(r=>r.model===m)),demo=window.DemoModels.profile(m);
    b.setAttribute('aria-pressed',String(selected.has(m)));b.disabled=!archived;
    const issue=!archived?ds.model_issues?.[m]:null;
    b.title=demo?displayModel(m):available?displayModel(m):issue?displayModel(m)+'：'+issue:displayModel(m)+' 在当前日期、起报时次下没有'+labelFor(ds)+'评分';
    b.innerHTML=`<i style="background:${color(m)}"></i>${esc(label(m))}${available?'':' · 当前条件无资料'}`;
    b.onclick=()=>{automaticModels=false;selected.has(m)?selected.delete(m):selected.add(m);render();};
    return b;
  });
  // One row per forecast system (短临/短期/中期/台风…); the system name selects its whole row.
  const {nowcasting,short_range,...otherSystems}=window.DemoModels.groups();
  const systems=new Map(Object.entries({nowcasting,short_range,global:'中期预报系统',...otherSystems,other:'其他预报系统'})
    .filter(([,title])=>title).map(([id,title])=>[id,{title,buttons:[]}]));
  for(const b of buttons){
    const m=b.dataset.model,system=window.DemoModels.profile(m)?.group||(Object.hasOwn(names,m)?'global':'other');
    (systems.get(system)||systems.get('other')).buttons.push(b);
  }
  $('models').replaceChildren(...[...systems].filter(([,system])=>system.buttons.length).map(([id,system])=>{
    const group=document.createElement('div'),title=document.createElement('h3'),all=document.createElement('button'),chips=document.createElement('div');
    const usable=system.buttons.filter(b=>!b.disabled).map(b=>b.dataset.model);
    group.className='model-system-group';group.setAttribute('role','group');group.setAttribute('aria-label',system.title);
    all.type='button';all.className='model-system-title';all.dataset.system=id;all.textContent=system.title;
    all.disabled=!usable.length;
    all.setAttribute('aria-pressed',String(usable.length>0&&selected.size===usable.length&&usable.every(m=>selected.has(m))));
    all.title=all.disabled?system.title+'暂无'+labelFor(ds)+'评分':`只看${system.title}的全部 ${usable.length} 个模式`;
    all.onclick=()=>{automaticModels=false;selected=new Set(usable);render();};
    title.append(all);chips.className='model-chips';chips.append(...system.buttons);
    group.append(title,chips);return group;
  }));
}

/* ---------- aggregation ---------- */
function withDemoIdentity(data,raw){
  const info=new Map(raw.filter(r=>r.is_demo).map(r=>[r.model,{is_demo:true,source_model:r.source_model,target_product_id:r.target_product_id,excluded_from_official_scores:true}]));
  return data.map(r=>info.has(r.model)?{...r,...info.get(r.model)}:r);
}
function chartRows(){
  const models=activeModels();
  const raw=scopedRows(models);
  if(metricKey==='performance')return {raw,data:withDemoIdentity(C.grouped(raw,['model','threshold']),raw)};
  const xKey=chartMode==='date'?'valid':chartMode==='threshold'?'threshold':'lead';
  return {raw,xKey,data:withDemoIdentity(C.grouped(raw,['model',xKey]),raw)};
}
function activeModels(){return [...selected];}
// Regular lead ticks: the accumulation step (3 h for instantaneous elements) up to 72 h,
// then the same step but never finer than 6 h, so 6 h and 12 h rain keep their spacing all
// the way. One tick per day beyond 72 h always landed on the same clock hours and missed
// the afternoon, so the later leads looked better than they are. Rows are fetched for these leads only,
// so a default page stays a few MB instead of every hourly lead.
// Station scores can be narrowed to the same regions as the station map presets.
function regionControls(){
  const show=stationDataset();
  $('region-field').hidden=!show;$('station-field').hidden=!show;
  if(!show)return;
  stationPickSync();ensureStationSites();
  $('region-switch').replaceChildren(...['all','hn','hun','gba','sz','hk','xj'].map(region=>{
    const b=document.createElement('button');b.type='button';b.dataset.region=region;
    b.textContent=stationRegionLabels[region];
    b.setAttribute('aria-pressed',String(!scoreStation&&stationRegion===region));
    b.onclick=()=>setStationRegion(region);return b;
  }));
}
function stationPickSync(){
  const input=$('station-pick'),active=Boolean(scoreStation);
  if(document.activeElement!==input)input.value=active?stationPickText(scoreStation):'';
  input.classList.toggle('chosen',active);
  $('station-score-reset').hidden=!active;
}
function ensureStationSites(){
  if(stationSites||stationSitesLoading)return;
  stationSitesLoading=fetch('./api/stations/meta',{cache:'no-store'}).then(response=>response.ok?response.json():Promise.reject(Error('HTTP '+response.status))).then(meta=>{
    stationSites=(meta.stations||[]).map(site=>({id:String(site.id),name:site.name||String(site.id)}));
    $('station-pick-list').replaceChildren(...stationSites.map(site=>new Option(stationPickText(site))));
    if(scoreStation){
      // A station that came from the address bar only has its id until the list arrives.
      const known=stationSites.find(site=>site.id===scoreStation.id);
      if(!known)setStationRegion(stationRegion);
      else if(known.name!==scoreStation.name){scoreStation={...known};render({skipStationEvent:true});document.dispatchEvent(new CustomEvent('gridstationscopechange'));}
    }
    stationPickSync();
  }).catch(()=>{}).finally(()=>{stationSitesLoading=null;});
}
// A list choice arrives as the full “站名（站号）” text; typed text may be a station id,
// a full station name, or any fragment that matches exactly one station.
function stationPickMatch(text,loose){
  if(!stationSites||!text)return null;
  const full=stationSites.find(site=>stationPickText(site)===text);
  if(full||!loose)return full||null;
  const lower=text.toLowerCase();
  const exact=stationSites.find(site=>site.id.toLowerCase()===lower)||stationSites.find(site=>site.name===text);
  if(exact)return exact;
  const partial=stationSites.filter(site=>stationPickText(site).toLowerCase().includes(lower));
  return partial.length===1?partial[0]:partial.length?'several':null;
}
function stationPickCommit(loose){
  const input=$('station-pick'),text=input.value.trim(),site=stationPickMatch(text,loose);
  if(!site||site==='several'){
    if(loose&&text)input.placeholder=!stationSites?'站点列表读取中，请稍后再试':site?`“${text}”对应多个站，请从列表中选`:`没有找到“${text}”`;
    return false;
  }
  input.placeholder='输入站名或站号';input.blur();
  if(site.id!==scoreStation?.id)setStationScore(site);
  stationPickSync();
  return true;
}
function displayLeads(dataset){
  const leads=[...new Set((dataset?.leads||[]).filter(Number.isFinite))].sort((a,b)=>a-b);
  const step=Math.max(3,Number(dataset?.accumulation)||3);
  const late=Math.max(6,Number(dataset?.accumulation)||0);
  const nice=leads.filter(l=>l<=72?l%step===0:l%late===0);
  return nice.length>=2?nice:sampleX(leads);
}
function sampleX(values,max=40){
  const sorted=[...values].sort((a,b)=>typeof a==='number'?a-b:String(a).localeCompare(String(b)));
  if(sorted.length<=max)return sorted;
  const out=[],seen=new Set(),step=(sorted.length-1)/(max-1);
  for(let i=0;i<max;i++){const index=Math.round(i*step);if(!seen.has(index)){seen.add(index);out.push(sorted[index]);}}
  return out;
}
function xLabelText(x,xKey){return xKey==='valid'?short(x):String(x);}
function issueRange(rows){const dates=unique(rows.map(r=>r.run?.slice(0,10)).filter(Boolean)).sort();return dates.length?`${dates[0]} — ${dates.at(-1)}`:'';}
function metricCellText(v,m){return finite(v)&&v!==-999?C.percentMetrics.includes(m)?(v*100).toFixed(1)+'%':fmt(v,3):'—';}
function metricTick(v,m){return C.percentMetrics.includes(m)?(v*100).toFixed(0)+'%':fmt(v,Math.abs(v)>=10?1:2);}
function bestPolicy(m){
  if(['RMSE','MAE','VRMSE','FAR','MR','FAR'].includes(m))return 'min';
  if(m==='ME')return 'abs';
  if(m==='BIAS')return 'one';
  return 'max';
}
function shownValue(v){return Number.isFinite(v)&&v!==-999?Math.round(v*1000):null;}
function bestSet(values,m){
  const displayed=values.map(shownValue).filter(v=>v!==null);
  if(new Set(displayed).size<=1)return new Set();
  const policy=bestPolicy(m);
  let best;
  if(policy==='min')best=Math.min(...displayed);
  else if(policy==='abs')best=displayed.reduce((a,b)=>Math.abs(b)<Math.abs(a)?b:a);
  else if(policy==='one')best=displayed.reduce((a,b)=>Math.abs(b-1000)<Math.abs(a-1000)?b:a);
  else best=Math.max(...displayed);
  return new Set(displayed.filter(v=>v===best));
}

/* ---------- charts ---------- */
function svgStart(title,w=1000,h=380){
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title><rect width="100%" height="100%" fill="white"/>`;
}
function yDomain(values,m){
  const vals=values.filter(v=>finite(v)&&v!==-999);
  if(!vals.length)return {lo:0,hi:1};
  const bounded=['TS','ETS','POD','FAR','MR','PC','ACC1','ACC2','CC','BIAS'].includes(m);
  if(bounded){
    const lo=m==='CC'||m==='ETS'?Math.min(0,...vals):0;
    const hi=Math.max(1,...vals);
    return {lo:lo<0?-1:0,hi:hi>1?Math.ceil(hi*10)/10:1};
  }
  let lo=Math.min(0,...vals),hi=Math.max(0,...vals);
  if(hi===lo)hi=lo+1;
  const pad=(hi-lo)*.12;if(lo<0)lo-=pad;hi+=pad;
  return {lo,hi};
}
function lineChartSvg(data,xKey,xs,secondRow){
  if(!data.length||!xs.length)return empty();
  const m=metricKey,W=1000,L=68,R=W-28,T=30,height=secondRow?400:370,B=height-(secondRow?82:56);
  const models=numericFirst(unique(data.map(r=>r.model)));
  const {lo,hi}=yDomain(data.map(r=>r[m]),m),y=v=>B-(v-lo)/(hi-lo||1)*(B-T);
  const numeric=v=>xKey==='valid'?Date.parse(v+'+08:00'):Number(v),xmin=numeric(xs[0]),xmax=numeric(xs.at(-1));
  const x=v=>xs.length===1?(L+R)/2:L+(numeric(v)-xmin)/(xmax-xmin||1)*(R-L);
  let svg=svgStart($('chart-title').textContent,W,height);
  for(let i=0;i<=5;i++){
    const v=lo+(hi-lo)*i/5;
    svg+=`<line class="grid" x1="${L}" x2="${R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L-12}" y="${y(v)+4}" text-anchor="end">${esc(metricTick(v,m))}</text>`;
  }
  svg+=`<path class="axis" fill="none" d="M${L} ${T}V${B}H${R}"/><line class="hover-guide" x1="0" x2="0" y1="${T}" y2="${B}" style="display:none"/>`;
  if(m==='BIAS'&&lo<=1&&hi>=1)svg+=`<line x1="${L}" x2="${R}" y1="${y(1)}" y2="${y(1)}" stroke="#a0bbc7" stroke-dasharray="4 4"/>`;
  const stride=Math.max(1,Math.ceil(xs.length/12));
  xs.forEach((v,i)=>{
    if(i%stride!==0&&i!==xs.length-1)return;
    svg+=`<text x="${x(v)}" y="${B+24}" text-anchor="middle">${esc(xLabelText(v,xKey))}</text>`;
    if(secondRow)svg+=`<text x="${x(v)}" y="${B+47}" text-anchor="middle" style="fill:#8797a3;font-size:12px">${esc(validHourText(v))}</text>`;
  });
  svg+=`<text x="${(L+R)/2}" y="${height-(secondRow?10:8)}" text-anchor="middle">${esc(xKey==='valid'?'有效时间（北京时间）':'预报时效（小时）')}${secondRow?' / 检验时刻（北京）':''}</text>`;
  for(const model of models){
    let segment=[],segments=[];
    for(const xv of xs){
      const row=data.find(r=>r.model===model&&r[xKey]===xv),v=row?.[m];
      if(finite(v)&&v!==-999)segment.push([x(xv),y(v)]);else if(segment.length){segments.push(segment);segment=[];}
    }
    if(segment.length)segments.push(segment);
    for(const seg of segments)svg+=`<polyline points="${seg.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ')}" fill="none" stroke="${color(model)}" stroke-width="2.2" opacity=".85"/>`;
    for(const xv of xs){
      const row=data.find(r=>r.model===model&&r[xKey]===xv),v=row?.[m];
      if(!finite(v)||v===-999)continue;
      const tip=`${displayModel(model)} · ${xKey==='valid'?full(xv):'+'+xv+'h'}\n${m}: ${metricCellText(v,m)}\n有效时次: ${row.cycles??1} · 样本: ${row.n??'未记录'}`;
      svg+=`<circle class="point" cx="${x(xv).toFixed(1)}" cy="${y(v).toFixed(1)}" r="4" fill="${color(model)}" data-gx="${xs.indexOf(xv)}" data-gl="${esc(xKey==='valid'?full(xv):'+'+xv+'h'+(secondRow?' · 检验 '+validHourText(xv):''))}" data-gm="${esc(displayModel(model))}" data-gv="${esc(metricCellText(v,m))}" data-gy="${v}" data-gn="${esc(String(row.n??''))}"><title>${esc(tip)}</title></circle>`;
    }
  }
  return svg+'</svg>';
}
// Grouped bars: one cluster per rainfall threshold, one bar per model, at a fixed lead.
function thresholdBarSvg(data,xs){
  if(!data.length||!xs.length)return empty();
  const m=metricKey,W=1000,L=68,R=W-28,T=30,height=370,B=height-56;
  const models=numericFirst(unique(data.map(r=>r.model)));
  const {lo,hi}=yDomain(data.map(r=>r[m]),m),y=v=>B-(v-lo)/(hi-lo||1)*(B-T);
  const slot=(R-L)/xs.length,bar=Math.min(46,slot*.8/models.length),left=i=>L+slot*i+(slot-bar*models.length)/2;
  const labelled=bar>=30;
  let svg=svgStart($('chart-title').textContent,W,height);
  for(let i=0;i<=5;i++){
    const v=lo+(hi-lo)*i/5;
    svg+=`<line class="grid" x1="${L}" x2="${R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L-12}" y="${y(v)+4}" text-anchor="end">${esc(metricTick(v,m))}</text>`;
  }
  svg+=`<path class="axis" fill="none" d="M${L} ${T}V${B}H${R}"/>`;
  if(lo<0)svg+=`<line x1="${L}" x2="${R}" y1="${y(0)}" y2="${y(0)}" stroke="#8ea4b0"/>`;
  if(m==='BIAS'&&lo<=1&&hi>=1)svg+=`<line x1="${L}" x2="${R}" y1="${y(1)}" y2="${y(1)}" stroke="#a0bbc7" stroke-dasharray="4 4"/>`;
  xs.forEach((t,i)=>{
    const centre=L+slot*(i+.5);
    svg+=`<text x="${centre}" y="${B+24}" text-anchor="middle">≥${esc(String(t))}</text>`;
    models.forEach((model,j)=>{
      const row=data.find(r=>r.model===model&&r.threshold===t),v=row?.[m];
      if(!finite(v)||v===-999)return;
      const x0=left(i)+bar*j,top=Math.min(y(v),y(0)),h=Math.max(1,Math.abs(y(v)-y(0)));
      const tip=`${displayModel(model)} · ≥${t} mm · +${stateLead}h\n${m}: ${metricCellText(v,m)}\n有效时次: ${row.cycles??1} · 样本: ${row.n??'未记录'}`;
      svg+=`<rect class="point" x="${(x0+1).toFixed(1)}" y="${top.toFixed(1)}" width="${Math.max(1,bar-2).toFixed(1)}" height="${h.toFixed(1)}" fill="${color(model)}" opacity=".9" data-cx="${centre.toFixed(1)}" data-gx="${i}" data-gl="${esc('≥'+t+' mm · +'+stateLead+'h')}" data-gm="${esc(displayModel(model))}" data-gv="${esc(metricCellText(v,m))}" data-gy="${v}" data-gn="${esc(String(row.n??''))}"><title>${esc(tip)}</title></rect>`;
      if(labelled)svg+=`<text x="${(x0+bar/2).toFixed(1)}" y="${(v<0?top+h+13:top-5).toFixed(1)}" text-anchor="middle" style="font-size:11px;fill:#4a6474;pointer-events:none">${esc(metricCellText(v,m))}</text>`;
    });
  });
  svg+=`<text x="${(L+R)/2}" y="${height-8}" text-anchor="middle">降水阈值（mm）</text>`;
  return svg+'</svg>';
}
function chartValues(){
  chartValueEvents?.abort();
  const chart=$('chart'),points=[...chart.querySelectorAll('.point')];
  if(!points.length)return;
  chartValueEvents=new AbortController();
  const signal=chartValueEvents.signal,tip=document.createElement('div');
  tip.id='chart-value-tooltip';tip.className='chart-value-tooltip';tip.hidden=true;
  tip.setAttribute('role','status');tip.setAttribute('aria-live','polite');tip.setAttribute('aria-atomic','true');chart.append(tip);
  let pinned=null;
  const hide=()=>{tip.hidden=true;if(pinned){pinned.classList.remove('is-selected');pinned.setAttribute('aria-pressed','false');}pinned=null;};
  const show=(point,event)=>{
    if(pinned&&pinned!==point)return;
    clearGroup();pinnedGroup=null;
    const text=point.getAttribute('aria-label');if(tip.textContent!==text)tip.textContent=text;
    tip.hidden=false;
    const box=point.getBoundingClientRect(),x=event?.clientX??box.left+box.width/2,y=event?.clientY??box.top;
    const w=tip.offsetWidth,h=tip.offsetHeight;
    tip.style.left=Math.max(10,Math.min(innerWidth-w-10,x+12))+'px';
    tip.style.top=Math.max(10,Math.min(innerHeight-h-10,y-h-12<10?y+15:y-h-12))+'px';
  };
  const place=(x,y)=>{
    const w=tip.offsetWidth,h=tip.offsetHeight;
    tip.style.left=Math.max(10,Math.min(innerWidth-w-10,x+16+w>innerWidth-10?x-w-16:x+16))+'px';
    tip.style.top=Math.max(10,Math.min(innerHeight-h-10,y-h/2))+'px';
  };
  let pinnedGroup=null,shownGroup=null;
  const clearGroup=()=>{
    if(!shownGroup)return;
    shownGroup.points.forEach(p=>p.classList.remove('is-near'));
    const guide=shownGroup.svg.querySelector('.hover-guide');if(guide)guide.style.display='none';
    shownGroup=null;tip.classList.remove('is-group');
  };
  const showGroup=(group,event)=>{
    if(shownGroup!==group){
      clearGroup();shownGroup=group;
      const head=document.createElement('strong');head.textContent=group.points[0].dataset.gl;
      const rows=[...group.points].sort((a,b)=>Number(b.dataset.gy)-Number(a.dataset.gy)).map(point=>{
        const row=document.createElement('div'),dot=document.createElement('i'),name=document.createElement('span'),value=document.createElement('b'),n=document.createElement('small');
        row.className='tip-row';dot.style.background=point.getAttribute('fill');name.textContent=point.dataset.gm;value.textContent=point.dataset.gv;n.textContent=point.dataset.gn?'样本 '+point.dataset.gn:'';
        row.append(dot,name,value,n);return row;
      });
      tip.classList.add('is-group');tip.replaceChildren(head,...rows);
      group.points.forEach(p=>p.classList.add('is-near'));
      const guide=group.svg.querySelector('.hover-guide');
      if(guide){const cx=group.points[0].getAttribute('cx');guide.setAttribute('x1',cx);guide.setAttribute('x2',cx);guide.style.display='';}
    }
    tip.hidden=false;
    const box=group.points[0].getBoundingClientRect();
    place(event?.clientX??box.left+box.width/2,event?.clientY??box.top);
  };
  const hideGroup=()=>{clearGroup();pinnedGroup=null;tip.hidden=true;};
  for(const svg of chart.querySelectorAll('svg')){
    const byX=new Map();
    for(const point of svg.querySelectorAll('.point[data-gx]')){
      if(!byX.has(point.dataset.gx))byX.set(point.dataset.gx,{svg,points:[],cx:Number(point.getAttribute('cx')??point.dataset.cx)});
      byX.get(point.dataset.gx).points.push(point);
    }
    const groups=[...byX.values()];if(!groups.length)continue;
    const nearest=event=>{
      const box=svg.getBoundingClientRect(),width=Number((svg.getAttribute('viewBox')||'').split(/\s+/)[2])||box.width;
      const sx=box.width?(event.clientX-box.left)/box.width*width:groups[0].cx;
      return groups.reduce((best,g)=>Math.abs(g.cx-sx)<Math.abs(best.cx-sx)?g:best);
    };
    svg.addEventListener('pointermove',event=>{if(pinned||pinnedGroup)return;showGroup(nearest(event),event);},{signal});
    svg.addEventListener('pointerleave',()=>{if(!pinned&&!pinnedGroup)hideGroup();},{signal});
    svg.addEventListener('click',event=>{
      event.stopPropagation();
      const group=nearest(event),same=pinnedGroup===group;
      hide();hideGroup();
      if(!same){showGroup(group,event);pinnedGroup=group;}
    },{signal});
  }
  const toggle=(point,event)=>{
    if(pinned===point){hide();return;}
    hide();pinned=point;point.classList.add('is-selected');point.setAttribute('aria-pressed','true');show(point,event);
  };
  for(const point of points){
    const title=point.querySelector('title');if(!title)continue;
    const text=title.textContent;point.setAttribute('aria-label',text);point.setAttribute('role','button');point.setAttribute('tabindex','0');point.setAttribute('aria-pressed','false');
    const desc=document.createElementNS('http://www.w3.org/2000/svg','desc');desc.textContent=text;title.replaceWith(desc);
    const grouped=point.dataset.gx!==undefined;
    if(!grouped){
      point.addEventListener('pointerenter',event=>show(point,event),{signal});
      point.addEventListener('pointermove',event=>show(point,event),{signal});
      point.addEventListener('pointerleave',()=>{if(!pinned)tip.hidden=true;},{signal});
      point.addEventListener('click',event=>toggle(point,event),{signal});
    }
    point.addEventListener('focus',()=>show(point),{signal});
    point.addEventListener('blur',()=>{if(!pinned)tip.hidden=true;},{signal});
    point.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();toggle(point);}},{signal});
  }
  chart.addEventListener('click',event=>{if(!event.target.closest('.point')){hide();hideGroup();}},{signal});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){hide();hideGroup();}},{signal});
}
let chartValueEvents=null;

function performancePanel(data,threshold){
  const W=560,H=420,L=64,T=28,size=300,B=T+size,R=L+size;
  const x=v=>L+v*size,y=v=>B-v*size;
  const values=data.filter(r=>finite(r.POD)&&finite(r.SR));
  let svg=svgStart(`≥${threshold} mm 评分综合图`,W,H);
  for(let i=0;i<=10;i++){
    const v=i/10;
    svg+=`<path class="grid" d="M${x(v)} ${T}V${B}M${L} ${y(v)}H${R}"/>`;
    if(i%2===0)svg+=`<text x="${x(v)}" y="${B+20}" text-anchor="middle">${v.toFixed(1)}</text><text x="${L-10}" y="${y(v)+4}" text-anchor="end">${v.toFixed(1)}</text>`;
  }
  for(const ts of [.1,.2,.3,.4,.5,.6,.7,.8,.9]){
    const pts=[];
    for(let i=1;i<=250;i++){const xx=i/250,yy=ts*xx/(xx*(1+ts)-ts);if(yy>=0&&yy<=1)pts.push(`${x(xx).toFixed(1)},${y(yy).toFixed(1)}`);}
    if(pts.length)svg+=`<polyline points="${pts.join(' ')}" fill="none" stroke="#d9c39a" stroke-width="1"/>`;
  }
  for(const row of values){
    const tip=`${displayModel(row.model)}\n成功率: ${fmt(row.SR)} · 命中率: ${fmt(row.POD)}\nTS: ${fmt(row.TS)} · BIAS: ${fmt(row.BIAS)}\n样本: ${row.n??'未记录'}`;
    svg+=`<circle class="point" cx="${x(row.SR).toFixed(1)}" cy="${y(row.POD).toFixed(1)}" r="6" fill="${color(row.model)}" stroke="white" stroke-width="2"><title>${esc(tip)}</title></circle>`;
  }
  svg+=`<path class="axis" fill="none" d="M${L} ${T}V${B}H${R}"/><text x="${(L+R)/2}" y="${B+44}" text-anchor="middle">成功率（1 − 空报率）→</text><text transform="translate(${L-42} ${(T+B)/2}) rotate(-90)" text-anchor="middle">命中率 →</text><text x="${R+12}" y="${T+16}" style="font-size:12px;fill:#8b7355">金色曲线为 TS 等值线</text><text x="${R+12}" y="${T+38}" style="font-size:12px;fill:#6c7f8c">右上方更好 · ${values.length} 个模式</text>`;
  return svg+'</svg>';
}
function performanceChart(data){
  if(!data.length)return empty('当前条件下没有可比较的样本');
  const thresholds=unique(data.map(r=>r.threshold)).sort((a,b)=>a-b);
  return `<div class="performance-grid">${thresholds.map(threshold=>{
    const rows=data.filter(r=>r.threshold===threshold);
    return `<section class="performance-panel"><h3>${esc('≥'+threshold+' mm')}</h3>${rows.length?performancePanel(rows,threshold):empty('该阈值暂无有效样本')}</section>`;
  }).join('')}</div>`;
}

/* ---------- table / CSV ---------- */
function buildTable(data,xKey,xs){
  const m=metricKey,models=numericFirst(activeModels().filter(model=>data.some(r=>r.model===model)));
  if(!models.length||!xs.length){tableState={rows:[],columns:[],xKey,xs:[]};return;}
  const modelRows=models.map(model=>({model,cells:xs.map(xv=>data.find(r=>r.model===model&&r[xKey]===xv))}));
  const best=xs.map((_,i)=>bestSet(modelRows.map(row=>row.cells[i]?.[m]),m));
  tableState={rows:modelRows,cells:modelRows,columns:[],xKey,xs,best,metric:m,models};
}
function renderMetricTable(data,xKey,xs){
  buildTable(data,xKey,xs);
  const m=metricKey,models=tableState.models||[];
  if(!models.length){$('results-table').innerHTML='';$('table-note').textContent='当前条件暂无有效样本';$('export-csv').disabled=true;return;}
  const head=`<thead><tr><th scope="col">模式</th>${xs.map(x=>`<th scope="col">${esc(xKey==='valid'?short(x):xKey==='threshold'?'≥'+x+' mm':'+'+x+'h')}${xKey==='lead'&&$('hour').value!=='all'?`<small>${esc(validHourText(x))}</small>`:''}</th>`).join('')}</tr></thead>`;
  const body=`<tbody>${tableState.rows.map((row,ri)=>`<tr><th scope="row">${esc(displayModel(row.model))}</th>${row.cells.map((cell,i)=>{
    const v=cell?.[m],shown=shownValue(v),bold=shown!==null&&tableState.best[i]?.size>0&&tableState.best[i].has(shown);
    const text=metricCellText(v,m),title=cell?`样本 n=${cell.n??'未记录'} · 有效时次 ${cell.cycles??1}`:'无有效样本';
    return `<td${bold?' class="is-best"':''} title="${esc(title)}">${bold?`<strong>${text}</strong>`:text}</td>`;
  }).join('')}</tr>`).join('')}</tbody>`;
  $('results-table').innerHTML=`<caption class="table-caption">${esc(metricTitle(metricKey))}${unit()?' ('+esc(unit())+')':''}</caption>`+head+body;
  $('table-title').textContent=xKey==='valid'?'模式 × 日期':xKey==='threshold'?`模式 × 阈值 · +${stateLead}h`:'模式 × 时效';
  $('table-note').textContent=`${xs.length} 个${xKey==='threshold'?'阈值':'时效'} · ${models.length} 个模式 · 粗体为每列最好（按显示精度，含并列）· — 为缺测 · 悬停查看样本数`;
  $('export-csv').disabled=false;
}
function renderPerformanceTable(data){
  const m='TS',thresholds=unique(data.map(r=>r.threshold)).sort((a,b)=>a-b),models=numericFirst(activeModels().filter(model=>data.some(r=>r.model===model)));
  if(!models.length||!thresholds.length){$('results-table').innerHTML='';$('table-note').textContent='当前条件暂无有效样本';$('export-csv').disabled=true;tableState={rows:[],columns:[],xKey:'threshold',xs:[]};return;}
  const rows=models.map(model=>({model,cells:thresholds.map(t=>data.find(r=>r.model===model&&r.threshold===t))}));
  const best=thresholds.map((_,i)=>bestSet(rows.map(row=>row.cells[i]?.TS),'TS'));
  tableState={rows,cells:rows,columns:[],xKey:'threshold',xs:thresholds,best,metric:'TS',models};
  $('results-table').innerHTML=`<thead><tr><th scope="col">模式</th>${thresholds.map(t=>`<th scope="col">≥${t} mm</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr><th scope="row">${esc(displayModel(row.model))}</th>${row.cells.map((cell,i)=>{const v=cell?.TS,shown=shownValue(v),bold=shown!==null&&best[i].size>0&&best[i].has(shown),text=metricCellText(v,'TS');return `<td${bold?' class="is-best"':''} title="${esc(cell?`样本 n=${cell.n??'未记录'}`:'无有效样本')}">${bold?`<strong>${text}</strong>`:text}</td>`;}).join('')}</tr>`).join('')}</tbody>`;
  $('table-title').textContent='模式 × 阈值';
  $('table-note').textContent='TS 越大越好；粗体为每个阈值的最好模式（按显示精度，含并列）· — 为缺测 · 悬停查看样本数';
  $('export-csv').disabled=false;
}
function exportCsv(){
  const {rows,xs,xKey}=tableState;
  if(!rows?.length)return;
  const m=tableState.metric||metricKey,columns=[['model','模式'],[xKey,xKey==='lead'?'时效 h':xKey==='valid'?'有效时间 BJT':'阈值 mm'],[m,m],['n','配对样本 N'],['cycles','有效时次']];
  if(kind==='降水'&&m!=='TS')columns.push(['H','命中 H'],['M','漏报 M'],['F','空报 F']);
  const data=rows.flatMap(row=>row.cells.map((cell,i)=>({model:displayModel(row.model),[xKey]:xs[i],[m]:C.percentMetrics.includes(m)&&finite(cell?.[m])?cell[m]*100:cell?.[m],n:cell?.n,cycles:cell?.cycles,H:cell?.H,M:cell?.M,F:cell?.F,POD:cell?.POD,FAR:cell?.FAR,BIAS:cell?.BIAS,SR:cell?.SR,is_demo:cell?.is_demo,source_model:cell?.source_model,target_product_id:cell?.target_product_id,excluded_from_official_scores:cell?.excluded_from_official_scores})));
  if(metricKey==='performance')columns.push(['POD','命中率 POD'],['FAR','空报率 FAR'],['BIAS','频率偏差 BIAS'],['SR','成功率']);
  if(data.some(r=>r.is_demo))columns.push(['is_demo','WN3演示'],['source_model','真实来源'],['target_product_id','演示目标接口'],['excluded_from_official_scores','排除正式排名']);
  download(new Blob([C.csv(data,columns)],{type:'text/csv;charset=utf-8'}),`${activeModels().some(m=>window.DemoModels.profile(m))?'WN3演示_':''}检验_${exportElement()}_${m}_${$('end').value}.csv`);
}
function empty(title='当前条件下没有可比较的样本',detail='请调整日期、起报时次或模式。'){return `<div class="empty"><strong>${esc(title)}</strong><span>${esc(detail)}</span></div>`;}
function download(blob,name){const u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),2000);}

/* ---------- info page ---------- */
function infoPage(){
  const g=currentGroup(),defs=groupMetricDefs(g);
  const metricRows=defs.map(m=>`<p><b>${esc(m.label)}</b>：${esc(metricHelp[m.key]||'')}</p>`).join('');
  const sample=ds?scopedRows(activeModels()).filter(row=>finite(row.n)):[];
  const n=sample.reduce((sum,row)=>sum+row.n,0);
  const scope=stationDataset()?`站点实况 · ${stationScopeLabel()}`:`格点分析场 · ${ds?.region||''}`;
  return `<div class="info-grid detail-info">
    <div class="info-block"><h3>资料与范围</h3><p>${esc(scope)}</p><p>${esc(ds?.reference||'')}${ds?.accumulation?` · ${ds.accumulation}小时累计降水`:''}${kind==='环流'?` · ${ds?.level} hPa` :''}</p><p>日期按有效时间（北京时间）筛选；起报时次 ${esc(hourLabel())}。${stationDataset()?'站点资料由定时任务离线评分，保留近16天。':'检验归档缓存定时更新。'}</p></div>
    <div class="info-block"><h3>时间与样本口径</h3><p>起报时间 + 预报时效 = 有效时间；全部显示北京时间。${chartMode==='date'?'“随日期”固定一个预报时效，比较各有效时刻。':'“随预报时效”按每个时效汇总所选日期和起报时次。'}</p><p>默认只保留各模式共同有效的时次（逐时效取交集），分类评分先累加命中、漏报、空报计数再计算。各模式按自身有效配对评分。</p><p>当前图表样本合计 n=${n.toLocaleString('zh-CN')}；缺资料不计零分。</p></div>
    <div class="info-block"><h3>指标口径</h3>${metricRows}<p>分级检验（按小雨/中雨等量级）暂不在详细分析中提供，可在考核总览查看分级 TS；降水详细分析使用累计阈值。</p></div>
    <div class="info-block"><h3>已收起的控件</h3><p>“仅共同有效时次”固定为开启，“各时效使用同一组有效时间”固定为关闭，“检验方式”固定为累加（≥阈值）。这些默认值与改版前一致，不再单独显示。</p><p>${esc(displayText(ds?.source_note||'共同有效时间不等于已验证完全相同的格点；各模式保留原始评分。'))}</p></div>
  </div>`;
}

/* ---------- render ---------- */
function setChartTitle(title){
  if(activeModels().some(m=>window.DemoModels.profile(m)))title='含 WN3 演示资料 · '+title;
  if(stationScoreActive())title=stationScopeLabel()+' · '+title;
  $('chart-title').textContent=title;
}
function render(opts={}){
  if(!ds||rowsReadyId!==ds.id)return;
  clampStationDates();
  if(stationDataset()&&stationRowsRange&&$('start').value&&$('end').value&&$('start').value<=$('end').value){
    const wantedStation=scoreStation?.id||'',wantedRegion=scoreStation?'all':(stationRegion||'all');
    if(stationRowsRange.start!==$('start').value||stationRowsRange.end!==$('end').value||(stationRowsRange.station||'')!==wantedStation||(stationRowsRange.region||'all')!==wantedRegion){
      remember();setDataset();return;
    }
  }
  regionControls();
  const g=currentGroup();
  if(page!=='metric'&&page!=='map'&&page!=='info')page='metric';
  if(page==='metric'&&!groupMetricDefs(g).some(d=>d.key===metricKey))metricKey=groupMetricDefs(g)[0]?.key||'TS';
  const defs=groupMetricDefs(g);
  options($('metric'),defs.map(d=>[d.key,d.label]),metricKey);$('metric').value=metricKey;
  if(needsThreshold()&&!(ds.thresholds||[]).includes(Number($('threshold').value)))$('threshold').value=String(ds.thresholds?.[0]??.1);
  const isPerformance=metricKey==='performance';
  if(isPerformance&&chartMode!=='lead')chartMode='lead';
  if(chartMode==='threshold'&&!needsThreshold())chartMode='lead';
  const available=availableRowsForModels();
  if(automaticModels&&!stationScoreActive()){
    const roster=new Set(available.map(r=>r.model));
    selected=new Set(defaultModels(ds,roster));
  }
  modelControls(available);
  renderFilter();
  $('chart-mode-toggle').hidden=isPerformance;
  $('date-lead-label').hidden=!(page==='metric'&&chartMode!=='lead'&&!isPerformance);
  $('grid-workspace').dataset.activeKind=groupId;
  renderNav();
  $('page-title').textContent=page==='info'?'说明':page==='map'?`${g.label} · 站点分布`:`${g.label} · ${metricTitle(metricKey)}`;
  $('page-subtitle').textContent=page==='info'?'资料、时间口径与指标定义':page==='map'?'站点分布图与单站预报实况':`${scopeLabel()} · ${$('start').value} — ${$('end').value} · ${hourLabel()}`;
  $('load-error').hidden=true;
  if(page==='map')return renderMapPage();
  if(page==='info')return renderInfoPage();
  $('detail-results').hidden=false;$('detail-table-card').hidden=false;$('grid-station-view').hidden=true;
  $('chart-mode-toggle').hidden=isPerformance;$('export-svg').hidden=false;$('legend').hidden=false;$('chart-explainer').hidden=false;
  $('chart-mode-toggle').querySelectorAll('[data-chart-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.chartMode===chartMode)));
  $('chart-mode-toggle').querySelector('[data-chart-mode="threshold"]').hidden=!needsThreshold();
  const models=activeModels();
  $('legend').innerHTML=numericFirst(models).map(m=>`<span><i style="background:${color(m)}"></i>${esc(label(m))}</span>`).join('');
  if(!models.length){showEmpty(available.length?'请选择至少一个模式':'当前条件没有可比较的样本',available.length?'可点击“默认模式”恢复选择。':'请调整日期、起报时次或指标。');return;}
  if(!$('start').value||!$('end').value||$('start').value>$('end').value){showEmpty('请选择有效日期范围','开始日期不能晚于结束日期。');return;}
  let data,xKey,xs,title,subtitle;
  if(isPerformance){
    const result=chartRows();data=result.data;
    setChartTitle(`评分综合图 · ${scopeLabel()}`);
    const span=`${$('start').value} — ${$('end').value}`,issue=issueRange(result.raw);
    $('chart-subtitle').textContent=['各阈值',issue&&issue!==span?`起报 ${issue}`:'',`检验 ${span} BJT`,hourLabel(),result.raw.length?'有效样本 '+result.raw.length+' 条':'暂无有效样本'].filter(Boolean).join(' · ');
    $('chart').innerHTML=performanceChart(data);renderPerformanceTable(data);
    $('chart-explainer').textContent='每个阈值一张小图：右上方命中率和成功率更高；金色曲线为 TS 等值线。悬停查看数值。';
  }else{
    const result=chartRows();data=result.data;xKey=result.xKey;
    const present=unique(data.map(r=>r[xKey])),nice=xKey==='lead'?displayLeads(ds).filter(l=>present.includes(l)):[];
    xs=xKey==='threshold'?present.filter(Number.isFinite).sort((a,b)=>a-b):(nice.length?nice:sampleX(present)).filter(v=>v!==undefined&&v!==null);
    const metricLabel=metricTitle(metricKey);
    title=chartMode==='date'?`${metricLabel} · 随日期 · 固定 +${stateLead}h`:chartMode==='threshold'?`${metricLabel} · 按阈值 · 固定 +${stateLead}h`:`${metricLabel} 随预报时效`;
    setChartTitle(title);
    const thresholdText=kind==='降水'?(metricKey==='PC'?'≥0.1 mm':needsThreshold()&&chartMode!=='threshold'?`≥${thresholdValue()} mm`:'各阈值'):'';
    const span=`${$('start').value} — ${$('end').value}`,issue=issueRange(result.raw);
    subtitle=[thresholdText,scopeLabel(),stationDataset()?'站点实况':'格点分析场',issue&&issue!==span?`起报 ${issue}`:'',`检验 ${span}`,hourLabel()].filter(Boolean).join(' · ');
    $('chart-subtitle').textContent=subtitle;
    if(!data.length){showEmpty('当前条件没有可比较的样本','请调整日期、起报时次或阈值。');return;}
    $('chart').innerHTML=xKey==='threshold'?thresholdBarSvg(data,xs):lineChartSvg(data,xKey,xs,chartMode==='lead'&&$('hour').value!=='all');
    renderMetricTable(data,xKey,xs);
    const explanation=metricKey==='CC'&&stationScoreActive()?'单站预报与实况随时间的相关系数，越接近 1 越好；至少需要 3 份配对。':(metricHelp[metricKey]||'');
    $('chart-explainer').textContent=`${explanation}${unit()?' 单位：'+unit()+'。':''}点击或悬停查看每个模式的数值和样本数。${commonFallback?' 所选模式没有共同有效时次，已按各模式各自有效时次显示。':''}`;
  }
  $('chart').querySelectorAll('svg').forEach(svg=>{svg.style.maxWidth='100%';});
  chartValues();
  $('export-svg').disabled=!$('chart').querySelector('svg');
  $('updated').textContent='结果更新 '+full(ds.updated)+' BJT';
  remember();writeHash();
  if(!opts.skipStationEvent)document.dispatchEvent(new CustomEvent('gridselectionchange',{detail:stationSelection()}));
}
function showEmpty(title,detail){
  $('chart').innerHTML=empty(title,detail);$('results-table').innerHTML='';$('table-note').textContent='';$('export-svg').disabled=true;$('export-csv').disabled=true;$('legend').innerHTML='';
}
function renderMapPage(){
  $('detail-results').hidden=true;$('detail-table-card').hidden=true;
  $('chart').innerHTML='';$('chart-title').textContent='';$('chart-subtitle').textContent='';
  $('grid-station-view').hidden=false;
  if(!stationKind(currentGroup())){
    $('grid-station-view').innerHTML='<div class="notice">当前要素没有站点实况评分，站点分布不可用。</div>';return;
  }
  if(!stationDataset()){
    $('grid-station-view').innerHTML='<div class="notice" role="status">读取站点实况资料…</div>';
    setReference('station').then(ok=>{
      if(page==='map'&&ok)render();
      else if(page==='map')$('grid-station-view').innerHTML='<div class="notice">站点实况资料暂不可用，请刷新或换回格点分析场。</div>';
    });
    return;
  }
  $('grid-station-view').innerHTML='<div class="notice" role="status">读取站点分布与单站配对…</div>';
  $('updated').textContent='结果更新 '+full(ds.updated)+' BJT';
  remember();writeHash();
  document.dispatchEvent(new CustomEvent('gridselectionchange',{detail:stationSelection()}));
}
function renderInfoPage(){
  $('detail-results').hidden=false;$('detail-table-card').hidden=true;$('grid-station-view').hidden=true;
  $('chart-mode-toggle').hidden=true;$('date-lead-label').hidden=true;$('export-svg').hidden=true;$('legend').hidden=true;$('chart-explainer').hidden=true;
  $('chart-title').textContent='资料与口径';$('chart-subtitle').textContent='';
  $('chart').innerHTML=infoPage();
  $('export-csv').disabled=true;$('updated').textContent='结果更新 '+full(ds.updated)+' BJT';
  remember();writeHash();
}

/* ---------- station integration ---------- */
function stationSelection(){
  const models=activeModels();
  return {station:stationDataset(),kind,element:ds?.element,model:models[0],modelLabel:label(models[0]),models:models.map(model=>({model,label:label(model),color:color(model)})),variable:ds?.var,
    start:$('start').value,end:$('end').value,hour:$('hour').value,
    leads:ds?ds.leads.slice():[],
    scoreStation:stationDataset()?scoreStation:null,
    updated:ds?.updated,verificationMode:'cumulative',grade:null,
    metric:metricKey==='performance'?'TS':metricKey,threshold:kind==='降水'?thresholdValue():null,lead:stateLead,reference:gridReference,page,region:stationRegion};
}
async function setStationScope(region,site=null){
  const next=['all','hn','hun','gba','hk','sz','xj'].includes(region)?region:'all';
  if(!stationDataset()||!ds)return;
  if(next===stationRegion&&(site?.id||'')===(scoreStation?.id||'')&&rowsReadyId===ds.id){render({skipStationEvent:true});return;}
  const previousEnd=stationWindow();
  stationRegion=next;scoreStation=site?{id:String(site.id),name:site.name||String(site.id)}:null;
  if(previousEnd!==stationWindow()&&datePreset!==null)setDates(datePreset);
  document.dispatchEvent(new CustomEvent('gridstationscopechange'));
  const target=ds,range={...(stationRowsRange||stationDateRange(target))};
  range.start=$('start').value;range.end=$('end').value;
  range.leads=displayLeads(target).join(',');
  delete range.station;delete range.region;
  if(scoreStation)range.station=scoreStation.id;else if(next!=='all')range.region=next;
  const current=++rowsSequence;rowsReadyId=null;datasetRows=[];
  $('grid-workspace').setAttribute('aria-busy','true');
  try{
    const rows=await loadStationDataset(target,range);
    if(current!==rowsSequence||ds?.id!==target.id)return;
    datasetRows=rows.filter(r=>r.dataset===ds.id);rowsReadyId=ds.id;stationRowsRange=range;
    if(next==='xj'&&!site){selectedBeforeXj??=new Set(selected);selected=new Set(ds.models.filter(m=>datasetRows.some(r=>r.model===m)));}
    else if(next!=='xj'&&selectedBeforeXj){selected=selectedBeforeXj;selectedBeforeXj=null;}
    render({skipStationEvent:true});
  }catch(error){
    if(current!==rowsSequence)return;
    $('load-error').hidden=false;$('load-error').textContent=error.message;
  }finally{if(current===rowsSequence)$('grid-workspace').setAttribute('aria-busy','false');}
}
const setStationRegion=region=>setStationScope(region);
const setStationScore=site=>setStationScope(stationRegion,site);
function setDetailLead(lead){
  if(!Number.isFinite(Number(lead)))return;
  stateLead=Number(lead);$('lead').value=String(stateLead);render();
}

/* ---------- reference / group switching ---------- */
async function setReference(reference,preferred){
  if(!['grid','station'].includes(reference))return false;
  if(reference===gridReference)return true;
  const sequence=++referenceSequence,previous=gridReference;
  try{
    if(!catalog&&!(await window.GridVerification.open()))return false;
    if(reference==='station')await ensureStationRows();
    if(sequence!==referenceSequence)return false;
    remember();const g=currentGroup(),old=ds,oldState=preferences.datasets[old?.id];
    gridReference=reference;
    if(reference==='grid'&&page==='map')page='metric';
    const list=datasetsForGroup(g);
    if(!list.length){
      const fallback=GROUPS.find(candidate=>datasetsForGroup(candidate).length);
      if(!fallback){gridReference=previous;return false;}
      groupId=fallback.id;kind=fallback.kind;if(fallback.field)circulationField=fallback.field;
      metricKey=pendingMetric||metricKey;renderNav();
      return loadGroup(fallback,metricKey);
    }
    const target=list.find(d=>d.id===preferred)||preferredDatasetFor(g)||list[0];
    if(target&&oldState&&!preferences.datasets[target.id]){
      const added=defaultModels(target).filter(m=>!(old.models||[]).includes(m));
      const carried=oldState.models.length===defaultModels(old).length&&oldState.models.every(m=>defaultModels(old).includes(m))?defaultModels(target):unique([...oldState.models.filter(m=>target.models.includes(m)),...added]);
      preferences.datasets[target.id]={...oldState,models:carried,knownModels:[...(old.models||[])]};
    }
    localStorage.setItem('gridReference',reference);
    $('dataset').value=target.id;
    return await loadGroup(g,metricKey);
  }catch(error){gridReference=previous;$('load-error').hidden=false;$('load-error').textContent=error.message;return false;}
}
async function openGroup(id,metric){
  const g=GROUPS.find(x=>x.id===id);if(!g||!window.GridVerification)return false;
  const workspace=$('grid-workspace');
  if(workspace.hidden){
    if(typeof activateVerificationModule==='function')activateVerificationModule('grid');
    else {workspace.hidden=false;if(typeof activateVerificationModule!=='function')document.querySelectorAll('[data-module]').forEach(b=>{const on=b.dataset.module==='grid';b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});}
  }
  if(!catalog&&!(await window.GridVerification.open()))return false;
  groupId=id;kind=g.kind;if(g.field)circulationField=g.field;page='metric';
  // A group may only exist in one reference (100 m wind is grid-only).
  if(!datasetsForGroup(g).length){
    const other=gridReference==='station'?'grid':'station';
    if(allDatasetsForGroup(g).some(d=>(d.source==='station')===(other==='station'))){
      gridReference=other;
      if(other==='station')await ensureStationRows();
    }
  }
  const defs=groupMetricDefs(g),wanted=[metric,metricKey].find(m=>defs.some(d=>d.key===m));
  if(!ds||!groupMatchesDataset(g,ds)||!referenceMatches(ds))return loadGroup(g,wanted||defs[0]?.key);
  metricKey=wanted||defs[0]?.key||'TS';
  if(metricKey==='performance')chartMode='lead';
  render();
  return true;
}
async function changeKind(value,preferred){
  const group=GROUPS.find(g=>g.id===value)||GROUPS.find(g=>g.kind===value);
  if(!group)return false;
  if(preferred&&catalog)preferences.groupDatasets[group.id]=preferred;
  return openGroup(group.id);
}

/* ---------- load ---------- */
async function load(){
  automaticModels=true;
  if(ds)remember();
  const previous=ds?.id||preferences.dataset;
  let success=false;
  $('refresh').disabled=true;$('load-error').hidden=true;$('catalog-status-wrap').hidden=false;$('catalog-status').textContent='读取资料…';
  try{
    const response=await fetch('./api/catalog?format=metadata'+(ds?'&refresh=1':''),{cache:'no-store',signal:AbortSignal.timeout(65000)});
    if(!response.ok)throw new Error('HTTP '+response.status);
    const next=await response.json();if(!next.datasets?.length)throw new Error('暂无可识别的检验归档');
    ++rowsSequence;rowsReadyId=null;stationRowsCache=new Map();
    rowsLoader=rowsLoaderFor(next);
    await window.DemoModels.ready();stationLoading=null;
    catalog=C.demoCatalog(C.unifiedCatalog(next),window.DemoModels.profiles(),WN3);
    if(previous){
      const combined=catalog.datasets.find(d=>matchesPrevious(d,previous));
      if(combined&&previous!==combined.id&&!preferences.datasets[combined.id]){
        const saved=preferences.datasets[previous]||{};
        preferences.datasets[combined.id]={...saved,models:previous?.startsWith('compare:')?saved.models:combined.models.filter(m=>m!=='Google WeatherNext 2'&&!window.DemoModels.profile(m))};
      }
    }
    if(gridReference==='station')await ensureStationRows();
    const savedData=catalog.datasets.find(d=>matchesPrevious(d,preferences.dataset));
    let targetGroup=GROUPS.find(g=>g.id===preferences.groupId)
      ||(savedData?GROUPS.find(g=>g.kind===savedData.kind&&(!g.element||g.element===savedData.element)&&(!g.field||g.field===(preferences.circulationField||'height'))):null)
      ||GROUPS.find(g=>g.kind===preferences.kind)
      ||GROUPS.find(g=>g.id===groupId)||GROUPS[0];
    if(!datasetsForGroup(targetGroup).length)targetGroup=availableGroups()[0]||targetGroup;
    groupId=targetGroup.id;kind=targetGroup.kind;if(targetGroup.field)circulationField=targetGroup.field;
    const hashPage=hashTarget(parseHashPage(location.hash));
    if(hashPage?.groupId)targetGroup=GROUPS.find(g=>g.id===hashPage.groupId)||targetGroup;
    if(hashPage?.page==='info')page='info';
    if(hashPage?.page==='map')page='map';
    if(hashPage?.mode)chartMode=hashPage.mode;
    const metric=hashPage?.metric;
    if(!datasetsForGroup(targetGroup).length)targetGroup=availableGroups()[0]||targetGroup;
    const preferred=preferences.groupDatasets[targetGroup.id]||previous;
    success=await loadGroup(targetGroup,metric||preferences.datasets[preferred]?.metric);
    if(page==='info')renderInfoPage();
    if(page==='map')render();
  }catch(error){
    $('load-error').hidden=false;$('load-error').textContent='检验资料暂时无法读取，请稍后点击“刷新资料”重试。'+(catalog?'下方保留上一次成功读取的结果。':'');
    $('catalog-status').textContent=catalog?'显示上次读取的资料':'资料暂不可用';
    if(!catalog)$('chart').innerHTML=empty('资料暂不可用','请稍后刷新或更换条件。');
  }finally{$('refresh').disabled=false;}
  return success;
}
function matchesPrevious(d,id){return d.id===id||d.aliases?.includes(id)||(id?.startsWith('compare:')&&d.source_ids?.some(s=>id.slice(8).split('+').includes(s)));}

/* ---------- remember ---------- */
function remember(){
  if(!ds||rowsReadyId!==ds.id)return;
  const state={models:[...selected],knownModels:[...ds.models],metric:metricKey,page,chartMode,lead:stateLead,threshold:Number($('threshold').value),hour:$('hour').value,days:datePreset??7,start:$('start').value,end:$('end').value};
  if(stationDataset())Object.assign(state,{stationDatePreset:datePreset!==null,stationWindowEnd:stationWindow()});
  if(kind==='环流'){preferences.circulationField=circulationField;preferences.circulationLevel=ds.level;}
  if(['环流','地面'].includes(kind)&&!stationDataset())preferences.referenceByKind[kind]=ds.reference;
  preferences.kind=kind;preferences.groupId=groupId;preferences.dataset=ds.id;preferences.groupDatasets[groupId]=ds.id;preferences.datasets[ds.id]=state;
  try{localStorage.setItem('szverify-workbench-v2',JSON.stringify(preferences));}catch{}
}

/* ---------- exports ---------- */
$('export-csv').onclick=exportCsv;
$('export-svg').onclick=()=>{
  const svgs=[...$('chart').querySelectorAll('svg')];if(!svgs.length)return;
  const NS='http://www.w3.org/2000/svg',style=document.createElementNS(NS,'style');
  style.textContent='text{font-family:"Microsoft YaHei",sans-serif;font-size:11px;fill:#718593}.grid{stroke:#edf2f5;stroke-width:1}.axis{stroke:#d5e1e9;stroke-width:1}';
  const prepared=svg=>{
    const copy=svg.cloneNode(true);copy.removeAttribute('style');
    for(const point of copy.querySelectorAll('.point')){const desc=point.querySelector('desc');if(desc){const title=document.createElementNS(NS,'title');title.textContent=desc.textContent;desc.replaceWith(title);}for(const name of ['role','tabindex','aria-pressed'])point.removeAttribute(name);point.classList.remove('is-selected');}
    return copy;
  };
  let out;
  if(svgs.length===1){out=prepared(svgs[0]);out.prepend(style);}
  else{
    // Small multiples are exported as one nested-SVG mosaic (two columns).
    const W=560,H=430,cols=2,rows=Math.ceil(svgs.length/cols);
    out=document.createElementNS(NS,'svg');out.setAttribute('xmlns',NS);out.setAttribute('viewBox',`0 0 ${W*cols} ${H*rows}`);
    const background=document.createElementNS(NS,'rect');background.setAttribute('width','100%');background.setAttribute('height','100%');background.setAttribute('fill','white');out.append(background);
    svgs.forEach((svg,index)=>{
      const copy=prepared(svg);
      copy.setAttribute('x',String((index%cols)*W));copy.setAttribute('y',String(Math.floor(index/cols)*H));
      copy.setAttribute('width',String(W));copy.setAttribute('height',String(H));
      copy.setAttribute('viewBox',svg.getAttribute('viewBox')||`0 0 ${W} ${H}`);
      out.append(copy);
    });
    out.prepend(style);
  }
  download(new Blob([new XMLSerializer().serializeToString(out)],{type:'image/svg+xml;charset=utf-8'}),`${activeModels().some(m=>window.DemoModels.profile(m))?'WN3演示_':''}检验_${exportElement()}_${metricKey}.svg`);
};

/* ---------- events ---------- */
document.addEventListener('click',event=>{
  const group=event.target.closest('[data-kind]');
  if(group&&group.closest('#detail-nav')){event.preventDefault();openGroup(group.dataset.kind);return;}
  const metric=event.target.closest('[data-metric]');
  if(metric&&metric.closest('#detail-nav')){event.preventDefault();openGroup(groupId,metric.dataset.metric);return;}
  const detailPage=event.target.closest('[data-page]');
  if(detailPage&&detailPage.closest('#detail-nav')){
    event.preventDefault();
    if(detailPage.dataset.page==='info')openPage('info');
    else if(detailPage.dataset.page==='map')openPage('map');
    return;
  }
});
async function openPage(name,group){
  if(group&&group!==groupId&&!(await openGroup(group)))return false;
  if(!catalog){
    if(typeof activateVerificationModule==='function')activateVerificationModule('grid');
    if(!(await window.GridVerification.open()))return false;
  }
  page=name;render();return true;
}
$('detail-page-select').onchange=event=>{
  const value=event.target.value;
  if(value==='info'){openPage('info');return;}
  const index=value.lastIndexOf(':');
  const id=value.slice(0,index),metric=value.slice(index+1);
  if(metric==='map'){openGroup(id).then(()=>openPage('map'));return;}
  openGroup(id,metric);
};
document.querySelectorAll('[data-chart-mode]').forEach(b=>b.onclick=()=>{chartMode=b.dataset.chartMode;if(chartMode!=='lead')$('date-lead-label').hidden=false;render();});
$('date-lead').onchange=event=>{stateLead=Number(event.target.value);$('lead').value=String(stateLead);render();};
$('models-default').onclick=()=>{automaticModels=true;render();};
$('hour').onchange=()=>render();
$('metric').onchange=()=>{metricKey=$('metric').value;if(metricKey==='performance')chartMode='lead';render();};
$('threshold').onchange=()=>render();
$('lead').onchange=()=>{stateLead=Number($('lead').value)||stateLead;render();};
$('common').onchange=()=>render();
for(const id of ['start','end'])$(id).onchange=()=>{datePreset=null;document.querySelectorAll('[data-days]').forEach(b=>b.classList.remove('chosen'));if(stationDataset()){remember();setDataset();}else render();};
$('refresh').onclick=load;
window.addEventListener('hashchange',async()=>{
  if(!location.hash.startsWith('#grid'))return;
  const params=parseHashPage(location.hash);
  const ref=params.get('ref');
  if(ref&&window.GridVerification)await setReference(ref);
  // A station id typed into the address bar switches the single-station scope; render reloads its rows.
  const wanted=params.get('station')||'';let stationChanged=false;
  if(gridReference==='station'&&STATION_ID.test(wanted)&&wanted!==scoreStation?.id){
    const known=stationSites?.find(site=>site.id===wanted);
    if(known||!stationSites){scoreStation=known?{...known}:{id:wanted,name:wanted};stationChanged=true;}
  }
  const target=hashTarget(params);
  if(!target){if(ref||stationChanged)render();return;}
  if(target.page==='info'){page='info';render();return;}
  if(target.page==='map'){page='map';if(target.groupId)await openGroup(target.groupId);render();return;}
  if(target.groupId)await openGroup(target.groupId,target.metric);
  else if(target.metric)await openGroup(groupId,target.metric);
  if(target.mode){if(target.mode==='date'&&metricKey==='performance')metricKey=groupMetricDefs(currentGroup())[0]?.key||'TS';chartMode=target.mode;render();}
  else if(stationChanged)render();
});

/* ---------- overview deep link ---------- */
let overviewDetailSequence=0;
async function openOverviewDetail(hash){
  const token=++overviewDetailSequence,params=parseHashPage(hash);
  const element=params.get('element'),day=Number(params.get('day')),days=params.get('days');
  if(!['rain','t'].includes(element)||![1,2,3].includes(day)||!['3','7','15'].includes(days))return;
  const current=()=>token===overviewDetailSequence&&!$('grid-workspace').hidden;
  if(!(await window.GridVerification.open())||!current())return;
  if(!(await setReference('station'))||!current())return;
  const group=element==='rain'?'降水':'气温';
  await openGroup(group,element==='rain'?'TS':'ACC2');
  if(!current()||!stationDataset())return;
  page='metric';chartMode='lead';stationRegion='all';scoreStation=null;
  stateLead=day*24;$('lead').value=String(stateLead);
  setDates(Number(days));datePreset=Number(days);
  render();
}
window.GridVerification={
  openOverviewDetail,setReference,setStationRegion,setStationScore,setDetailLead,selection:stationSelection,reference:()=>gridReference,
  open(){if(catalog){window.DemoModels.gridNotice(kind,activeModels());return Promise.resolve(true);}if(!gridLoading)gridLoading=load().finally(()=>gridLoading=null);return gridLoading;},
  refresh:load
};
let gridLoading=null;
$('station-score-reset').addEventListener('click',()=>setStationRegion(stationRegion));
{
  const input=$('station-pick');
  // Emptying the box on focus lets the browser offer the whole list; leaving restores the current station.
  input.addEventListener('focus',()=>{input.value='';});
  input.addEventListener('input',()=>stationPickCommit(false));
  input.addEventListener('change',()=>{if(!stationPickCommit(true))stationPickSync();});
  input.addEventListener('keydown',event=>{
    if(event.key==='Enter'){event.preventDefault();stationPickCommit(true);}
    else if(event.key==='Escape')input.blur();
  });
  input.addEventListener('blur',()=>setTimeout(stationPickSync,0));
}

/* Static navigation before the catalog arrives, so the sidebar is usable from the overview. */
renderNav();
