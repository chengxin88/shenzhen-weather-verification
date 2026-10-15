'use strict';
const $=id=>document.getElementById(id), C=VerificationCore;
const WN3='Google WeatherNext 3（集合均值）';
const palette={'ECMWF-IFS-9km-OpenMeteo':'#117f8b','ECMWF-IFS':'#0072b2','ECAI':'#009e73','ECMWF-ENSEMBLES平均':'#56b4e9','NCEP-GFS':'#d55e00','GFSAI':'#cc79a7','Google WeatherNext 2':'#e69f00','Google WeatherNext 3（集合均值）':'#8b5cf6'};
const names={'ECMWF-IFS-9km-OpenMeteo':'EC 9 km · Open-Meteo','ECMWF-IFS':'EC','ECAI':'ECAI','ECMWF-ENSEMBLES平均':'EC 集合平均','NCEP-GFS':'GFS','GFSAI':'GFSAI','GEFS集合平均':'GFS 集合平均','Google WeatherNext 2':'WN2 集合平均','Google WeatherNext 3（集合均值）':'WN3 集合平均'};
const ids={'ECMWF-IFS':'IFS','ECAI':'AIFS','ECMWF-ENSEMBLES平均':'ENS','GFSAI':'AIGFS','GEFS集合平均':'GEFS'};
const label=m=>window.DemoModels.profile(m)?window.DemoModels.label(m):names[m]?(ids[m]?`${names[m]}（${ids[m]}）`:names[m]):m;
const displayModel=m=>label(m);
const displayText=text=>String(text??'').replaceAll('集合均值','集合平均');
const metricNames={TS:'TS · 综合评分',ETS:'ETS · 公平技巧评分',POD:'POD · 命中率',FAR:'FAR · 空报率',MR:'MR · 漏报率',BIAS:'BIAS · 频率偏差',ME:'ME · 平均误差',MAE:'MAE · 平均绝对误差',RMSE:'RMSE · 均方根误差',CC:'CC · 相关系数',VRMSE:'风矢量 RMSE'};
const metricHelp={TS:'越接近 1 越好；综合考虑命中、漏报和空报。',ETS:'扣除随机命中的影响，越接近 1 越好；需要完整样本总数。',POD:'越接近 1 越好；应结合空报率一起判断。',FAR:'越接近 0 越好；分母为所有预报事件。',MR:'越接近 0 越好；MR = 1 − POD。',BIAS:'越接近 1 越好；大于 1 表示事件预报偏多，小于 1 表示偏少。',ME:'越接近 0 越好；正值表示预报偏高。',MAE:'越接近 0 越好。',RMSE:'越接近 0 越好，对较大的误差更敏感。',CC:'越接近 1 表示空间分布越一致；不代表量值没有偏差。',VRMSE:'越接近 0 越好，合并考察东西、南北风分量误差。'};
let catalog=null, ds=null, kind='降水', view='multi', scoreScope='period', leadMode='single', leadGroup='near', selected=new Set(), tableRows=[], tableColumns=[], timeCommon=false;
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(v,d=3)=>Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:d,minimumFractionDigits:d}):'—';
const short=t=>t?t.slice(5,10)+' '+t.slice(11,16):'—';
const full=t=>t?t.slice(0,16).replace('T',' '):'—';
const unique=x=>[...new Set(x)];
const unit=()=>$('metric').value==='CC'?'':kind==='气温'?'℃':kind==='地面'?ds.unit:kind==='环流'?($('metric').value==='VRMSE'?'m/s':'m'):'';
function options(el,items,value){el.replaceChildren(...items.map(([v,label])=>{const o=document.createElement('option');o.value=v;o.textContent=label;return o;}));if(items.some(x=>String(x[0])===String(value)))el.value=value;}
function labelFor(d){return d.kind==='降水'?`${d.accumulation}小时累计降水`:d.kind==='地面'?d.label:d.kind==='环流'?`${d.level} hPa · ${circulationField==='wind'?'风场':'高度场'}`:'2米气温';}
const sourceName=(d,model)=>model==='ECMWF-IFS-9km-OpenMeteo'?`EC 9 km · Open-Meteo · ${d?.reference||'本地固定起报归档'}`:window.DemoModels.profile(model)?`WN3演示 · ${d?.reference||'实况配对'} · 不计入正式排名`:(d?.source==='analysis'||d?.original_source==='analysis')?`本机预报 · ${d.reference}`:d?.pilot?'WN3 集合平均 · 预计算':d?.source==='comparison'?'全部模式 · 同图对比':d?.source==='hk'?(model===WN3?'WN3 集合平均 · 本机0.1°':model?'EC / GFS · 本地0.25°归档':(d.supported_models||d.models).includes(WN3)?'本地预报缓存 · EC / GFS / WN3':'EC / GFS · 本地0.25°归档'):d?.source==='weekly'?'原 GRIB · 逐时次汇总':'原 GRIB · 多时效归档';
const elementName=()=>kind==='地面'?(ds?.label||'地面'):kind==='环流'?(circulationField==='wind'?'风场':'高度场'):kind;
const exportElement=()=>kind==='环流'?`${elementName()}_${ds.level}hPa`:kind==='地面'?elementName():kind;
const metricsForView=()=>kind==='降水'?['TS',...((ds.source==='weekly'||ds.has_n)?['ETS']:[]),'POD','FAR','MR','BIAS']:kind==='环流'?C.circulationMetrics(circulationField):kind==='地面'&&ds.element.startsWith('wind')?['VRMSE']:['RMSE','ME','MAE','CC'];
const metricLabel=m=>m==='CC'&&(periodSummary()||groupSummary())?'CC · 平均空间相关':kind==='地面'?(m==='VRMSE'?'VRMSE · 风矢量均方根误差':metricNames[m]+(m==='CC'?'':' · hPa')):kind==='环流'?({RMSE:'RMSE · 高度均方根误差',ME:'ME · 高度平均误差',MAE:'MAE · 高度平均绝对误差',CC:'CC · 高度空间相关',VRMSE:'VRMSE · 风矢量均方根误差'}[m]||metricNames[m]):metricNames[m];
const metricColumn=m=>m==='CC'&&(periodSummary()||groupSummary())?'平均空间相关 CC':kind==='地面'?(m==='VRMSE'?'风矢量 RMSE (m/s)':m==='CC'?'CC':`${m} (hPa)`):kind==='环流'?(m==='VRMSE'?'风矢量 RMSE (m/s)':m==='CC'?'高度 CC':`高度 ${m} (m)`):m;
function setChartTitle(title){if(groupActive()&&!groupSummary())title=selectedGroup().label+' · '+title;if(periodSummary())title='整段时间汇总 · '+title;if(activeModels().some(m=>window.DemoModels.profile(m)))title='含 WN3 演示资料 · '+title;$('chart-title').textContent=kind==='环流'?`${elementName()} · ${ds.level} hPa · ${ds.reference} · ${title}`:kind==='地面'?`${elementName()} · ${ds.reference} · ${title}`:title;}
function referenceControls(){
  $('reference-filters').hidden=!['环流','地面'].includes(kind);
  if($('reference-filters').hidden)return;
  const choices=catalog.datasets.filter(d=>d.kind===kind);
  $('reference-switch').replaceChildren(...unique(choices.map(d=>d.reference)).sort((a,b)=>Number(b.includes('ERA5'))-Number(a.includes('ERA5'))).map(reference=>{
    const b=document.createElement('button');b.type='button';b.textContent=reference;b.setAttribute('aria-pressed',reference===ds.reference);
    b.onclick=()=>{
      remember();
      const target=choices.find(d=>d.reference===reference&&d.level===ds.level&&(d.element??null)===(ds.element??null))||choices.find(d=>d.reference===reference);
      $('dataset').value=target.id;setDataset();
    };return b;
  }));
}
function circulationControls(){
  $('circulation-filters').hidden=kind!=='环流';
  $('page-title').textContent=elementName()+'检验';
  if(kind!=='环流')return;
  for(const button of document.querySelectorAll('[data-circulation-field]'))button.setAttribute('aria-pressed',button.dataset.circulationField===circulationField);
  const choices=catalog.datasets.filter(d=>d.kind==='环流'&&!d.pilot&&d.reference===ds.reference);
  const scored=new Set(catalog.rows.filter(r=>C.circulationHasScore(r,circulationField)).map(r=>r.dataset));
  const levels=unique([1000,925,850,700,500,300,200,...choices.map(d=>d.level)]).sort((a,b)=>b-a);
  $('circulation-level-switch').replaceChildren(...levels.map(level=>{
    const target=choices.find(d=>d.level===level&&scored.has(d.id)),button=document.createElement('button');
    button.type='button';button.dataset.level=level;button.disabled=!target;
    button.textContent=level+' hPa';button.setAttribute('aria-pressed',level===ds.level);
    button.title=target?`${level} hPa ${elementName()} · 已有评分`:`${level} hPa ${elementName()}暂无评分归档`;
    button.setAttribute('aria-label',button.title);
    button.onclick=()=>{remember();$('dataset').value=target.id;setDataset();};return button;
  }));
  const available=unique(choices.filter(d=>scored.has(d.id)).map(d=>d.level)).sort((a,b)=>b-a);
  $('circulation-coverage').textContent=(available.length?`当前有评分：${available.join('、')} hPa。`:'当前要素暂无评分归档。')+' 灰色气压层暂无评分，新增资料后刷新即可选择。';
}
function setCirculationField(field){
  if(kind!=='环流'||!['height','wind'].includes(field))return;
  if(circulationField==='height')preferences.circulationHeightMetric=$('metric').value;
  circulationField=field;
  options($('metric'),metricsForView().map(m=>[m,metricLabel(m)]),field==='height'?preferences.circulationHeightMetric:'VRMSE');
  circulationControls();render();
}
function choiceControls(id,selectId,shortLabel=false){
  const control=$(selectId),container=$(id),items=[...control.options];
  const signature=items.map(o=>o.value+'|'+o.textContent).join(';');
  if(container.dataset.options!==signature){
    container.dataset.options=signature;
    container.replaceChildren(...items.map(o=>{
      const b=document.createElement('button');b.type='button';b.dataset.value=o.value;
      b.textContent=shortLabel?o.value:o.textContent;b.title=o.textContent;
      b.setAttribute('aria-label',o.textContent);
      b.onclick=()=>{control.value=o.value;render();};return b;
    }));
  }
  for(const b of container.children)b.setAttribute('aria-pressed',b.dataset.value===control.value);
}
function validChoiceControls(){
  const control=$('valid'),container=$('valid-switch'),items=[...control.options],size=6;
  const signature=JSON.stringify(items.map(o=>o.value)),pages=Math.max(1,Math.ceil(items.length/size));
  const changed=container.dataset.options!==signature||container.dataset.selection!==control.value;
  let page=changed?Math.floor(Math.max(0,items.findIndex(o=>o.value===control.value))/size):Number(container.dataset.page)||0;
  page=Math.max(0,Math.min(pages-1,page));
  container.dataset.options=signature;container.dataset.selection=control.value;container.dataset.page=page;
  const rendered=signature+'|'+page;
  if(container.dataset.rendered!==rendered){
    container.dataset.rendered=rendered;
    container.replaceChildren(...items.slice(page*size,(page+1)*size).map(o=>{
      const b=document.createElement('button');b.type='button';b.dataset.value=o.value;
      b.textContent=short(o.value);b.title=full(o.value)+' BJT';
      b.setAttribute('aria-label','预报对应时间 '+full(o.value)+' 北京时间');
      b.onclick=()=>{control.value=o.value;render();};return b;
    }));
    if(!items.length){const note=document.createElement('span');note.className='option-empty';note.textContent='无可用时刻';container.append(note);}
  }
  for(const b of container.querySelectorAll('button'))b.setAttribute('aria-pressed',b.dataset.value===control.value);
  $('valid-pager').hidden=pages===1;
  $('valid-previous').disabled=page===0;$('valid-next').disabled=page===pages-1;
  $('valid-page').textContent=(page+1)+' / '+pages;
  $('valid-page').setAttribute('aria-label','第'+(page+1)+'页，共'+pages+'页');
  const turn=step=>{container.dataset.page=page+step;validChoiceControls();};
  $('valid-previous').onclick=()=>turn(-1);$('valid-next').onclick=()=>turn(1);
}
const matchesPrevious=(d,id)=>d.id===id||d.aliases?.includes(id)||(id?.startsWith('compare:')&&d.source_ids?.some(s=>id.slice(8).split('+').includes(s)));

const stateKey='szverify-workbench-v2';
let preferences={datasets:{}};
try { preferences=JSON.parse(localStorage.getItem(stateKey))||preferences; } catch {}
preferences.datasets ||= {};
preferences.referenceByKind ||= {};
if(preferences.era5CirculationVersion!==1){preferences.referenceByKind['环流']='ERA5 再分析';preferences.era5CirculationVersion=1;}
let circulationField=preferences.circulationField==='wind'?'wind':'height';
// Reopening follows the last seven calendar dates in Beijing time.
// Within the current page users may choose a custom period or one instant.
for(const saved of Object.values(preferences.datasets)){
  if(!saved||typeof saved!=='object')continue;
  saved.days='7';saved.scoreScope='period';
  delete saved.start;delete saved.end;delete saved.valid;
}
const seriesColors=['#0072b2','#009e73','#d55e00','#cc79a7','#e69f00','#56b4e9','#000000','#999999'];
const color=m=>window.DemoModels.profile(m)?window.DemoModels.color(m):palette[m]||seriesColors[Math.max(0,ds?.leads.indexOf(Number(String(m).replace(/[^\d]/g,'')))||0)%seriesColors.length];
const timeLeads=()=>view==='time'&&$('time-mode').value==='leads';
const singleMode=()=>view==='single'||view==='matrix'||timeLeads();
const manyLeads=()=>view==='lead'||view==='matrix'||timeLeads();
const activeModels=()=>singleMode()?[$('single').value].filter(Boolean):[...selected];
const useCommon=()=>view==='time'?timeCommon:$('common').checked;
const commonOptions=()=>({acrossLeads:(manyLeads()||groupActive())&&useCommon()&&$('across-leads').checked});
function instantSelection(){return kind!=='降水'&&!['time','info'].includes(view)&&scoreScope==='instant';}
function periodSummary(){return kind!=='降水'&&!['time','info'].includes(view)&&scoreScope==='period';}
const groupActive=()=>leadMode==='group'&&view!=='info';
const groupSummary=()=>groupActive()&&!['lead','matrix'].includes(view)&&!timeLeads();
const selectedGroup=()=>C.leadGroups.find(g=>g.id===leadGroup)||C.leadGroups[0];
const leadCaption=()=>groupActive()?selectedGroup().label:$('lead').value!==''?'+'+$('lead').value+'小时':'暂无可用时效';
function leadRows(rows){return groupActive()?C.filterLeadGroup(rows,leadGroup):manyLeads()?rows:rows.filter(r=>r.lead===Number($('lead').value));}
function remember(){
  if(!ds)return;
  const state={models:[...selected],knownModels:[...ds.models],view,scoreScope,leadMode,leadGroup};
  state.days=document.querySelector('[data-days].chosen')?.dataset.days??null;
  for(const id of ['start','end','hour','metric','threshold','lead','valid','single','time-mode'])state[id]=$(id).value;
  state.common=$('common').checked;state.across=$('across-leads').checked;state.timeCommon=timeCommon;
  if(kind==='环流'){
    preferences.circulationField=circulationField;preferences.circulationLevel=ds.level;
    if(circulationField==='height')preferences.circulationHeightMetric=$('metric').value;
  }
  if(['环流','地面'].includes(kind))preferences.referenceByKind[kind]=ds.reference;
  preferences.kind=kind;preferences.leadMode=leadMode;preferences.leadGroup=leadGroup;preferences.dataset=ds.id;preferences.datasets[ds.id]=state;
  try{localStorage.setItem(stateKey,JSON.stringify(preferences));}catch{}
}
function modelControls(availableRows=null){
  const wn2='Google WeatherNext 2',models=[...(ds.supported_models||ds.models)];
  const wn2Windows=kind==='降水'&&!ds.pilot?unique(catalog.datasets.filter(d=>d.kind==='降水'&&!d.pilot&&d.models.includes(wn2)).map(d=>d.accumulation)).sort((a,b)=>a-b):[];
  if(wn2Windows.length&&!models.includes(wn2))models.push(wn2);
  $('models').replaceChildren(...models.map(m=>{
    const b=document.createElement('button');b.className='model-chip';b.dataset.model=m;b.setAttribute('aria-pressed',selected.has(m));
    const archived=ds.models.includes(m),available=archived&&(availableRows===null||availableRows.some(r=>r.model===m));
    const demo=window.DemoModels.profile(m);
    const demoAlternative=demo&&!archived&&kind==='降水'?catalog.datasets.find(d=>d.kind===kind&&d.models.includes(WN3)&&!d.pilot):null;
    b.disabled=!archived&&!demoAlternative;
    const issue=!archived?ds.model_issues?.[m]:null;
    const otherWindows=m===wn2&&!archived&&wn2Windows.length&&!wn2Windows.includes(ds.accumulation);
    b.title=demoAlternative?`${displayModel(m)} 当前只有${demoAlternative.accumulation}小时累计；点击切换到真实累计窗口。`:otherWindows?`${displayModel(m)} 暂无${ds.accumulation}小时降水评分；请选择 ${wn2Windows.join('、')} 小时累计查看。`:issue?displayModel(m)+'：'+issue:available?displayModel(m):displayModel(m)+' 在当前日期、起报时次和预报时效下没有'+labelFor(ds)+'评分';
    b.innerHTML=`<i style="background:${color(m)}"></i>${esc(label(m))}${demoAlternative?' · 仅'+demoAlternative.accumulation+'小时':otherWindows?' · 仅'+wn2Windows.join('/')+'小时':available?'':' · '+esc(issue||'当前条件无资料')}`;
    b.onclick=()=>{
      if(demoAlternative){$('dataset').value=demoAlternative.id;setDataset();selected.add(m);modelControls();render();return;}
      selected.has(m)?selected.delete(m):selected.add(m);modelControls();render();
    };return b;
  }));
  const systems=new Map(Object.entries({global:'全球预报系统',...window.DemoModels.groups(),other:'其他预报系统'}).map(([id,title])=>[id,{title,buttons:[]}]));
  for(const button of $('models').children){
    const model=button.dataset.model,profile=window.DemoModels.profile(model);
    const system=profile?.group||(Object.hasOwn(names,model)?'global':'other');
    (systems.get(system)||systems.get('other')).buttons.push(button);
  }
  $('models').replaceChildren(...[...systems].filter(([,system])=>system.buttons.length).map(([id,system])=>{
    const group=document.createElement('div'),title=document.createElement('h3'),chips=document.createElement('div');
    group.className='model-system-group';group.setAttribute('role','group');group.setAttribute('aria-labelledby','model-system-'+id);
    title.id='model-system-'+id;title.textContent=system.title;chips.className='model-chips';chips.append(...system.buttons);
    group.append(title,chips);return group;
  }));
  options($('single'),[...selected].map(m=>[m,displayModel(m)]),$('single').value);
}
function setKind(value,preferred){
  kind=value;
  $('grid-workspace').dataset.activeKind=kind;
  document.querySelectorAll('[data-kind]').forEach(b=>b.classList.toggle('active',!$('grid-workspace').hidden&&b.dataset.kind===kind));
  $('page-title').textContent=kind+'检验';
  const choices=catalog.datasets.filter(d=>d.kind===kind).sort((a,b)=>(a.accumulation??0)-(b.accumulation??0)||(a.level??0)-(b.level??0));
  const wantedReference=['环流','地面'].includes(kind)?(preferences.referenceByKind[kind]||(kind==='环流'?'ERA5 再分析':'GDAS 分析场')):null;
  const scoped=choices.some(d=>d.reference===wantedReference)?choices.filter(d=>d.reference===wantedReference):choices;
  const target=scoped.find(d=>matchesPrevious(d,preferred))||(kind==='环流'?(scoped.find(d=>d.level===preferences.circulationLevel)||scoped.find(d=>d.level===500)):null)||scoped[0];
  options($('dataset'),choices.map(d=>[d.id,labelFor(d)+(d.pilot?" · 早期试接入记录":"")]),target?.id);setDataset();
}
function datasetControls(){
  let field=$('dataset-choice-field');
  if(!field){
    field=document.createElement('div');field.id='dataset-choice-field';field.className='filter-field';
    field.innerHTML='<span>检验资料</span><div class="choice-buttons" role="group" aria-label="检验资料"></div>';
    $('accumulation-label').parentElement.prepend(field);
  }
  const choices=catalog.datasets.filter(d=>d.kind===kind&&(kind!=='地面'||d.reference===ds.reference));
  field.hidden=kind!=='地面'&&!choices.some(d=>d.pilot);
  field.firstElementChild.textContent=kind==='地面'?'检验要素':'检验资料';
  field.lastElementChild.replaceChildren(...choices.map(d=>{
    const b=document.createElement('button');b.type='button';
    b.textContent=kind==='地面'?d.label:d.pilot?'早期试接入记录':'全部模式';
    b.setAttribute('aria-pressed',d.id===ds.id);
    b.onclick=()=>{$('dataset').value=d.id;setDataset();};return b;
  }));
}
function setDataset(){
  ds=catalog.datasets.find(d=>d.id===$('dataset').value);
  if(!ds){$('chart').innerHTML=empty('该要素暂无可用归档');return;}
  datasetControls();referenceControls();circulationControls();
  const saved=preferences.datasets[ds.id]||{};
  scoreScope=saved.scoreScope==='instant'?'instant':'period';
  leadMode=(saved.leadMode??preferences.leadMode)==='group'?'group':'single';
  leadGroup=C.leadGroups.some(g=>g.id===(saved.leadGroup??preferences.leadGroup))?(saved.leadGroup??preferences.leadGroup):'near';
  const remembered=saved.models||ds.models.filter(m=>m!=='Google WeatherNext 2'&&!window.DemoModels.profile(m));
  const oldRoster=saved.knownModels||(kind==='环流'&&ds.reference.includes('ERA5')?['ECMWF-IFS','NCEP-GFS']:ds.models);
  const introduced=ds.models.filter(m=>!window.DemoModels.profile(m)&&!oldRoster.includes(m)&&catalog.rows.some(r=>r.dataset===ds.id&&r.model===m&&(leadMode==='group'?C.inLeadGroup(r.lead,leadGroup):r.lead===Number(saved.lead??24))));
  selected=new Set([...remembered,...introduced].filter(m=>ds.models.includes(m)));
  modelControls();
  const metrics=metricsForView();
  options($('metric'),metrics.map(m=>[m,metricLabel(m)]),kind==='环流'&&circulationField==='height'?(preferences.circulationHeightMetric||saved.metric):saved.metric);
  options($('threshold'),ds.thresholds.map(t=>[t,'≥ '+t]),saved.threshold??.1);
  options($('lead'),ds.leads.map(l=>[l,'+'+l+' 小时']),saved.lead??24);
  for(const id of ['start','end']){$(id).removeAttribute('min');$(id).max=C.calendarWindow(1).end;}
  const datePreset=['1','3','7','30','all'].includes(saved.days)?saved.days:null;
  setDates(datePreset??7);
  const defaultStart=$('start').value,defaultEnd=$('end').value;
  for(const id of ['start','end'])if(!datePreset&&saved[id])$(id).value=[saved[id],C.calendarWindow(1).end].sort()[0];
  if((saved.days===null&&!datePreset)||$('start').value!==defaultStart||$('end').value!==defaultEnd)document.querySelectorAll('[data-days]').forEach(b=>b.classList.remove('chosen'));
  $('hour').value=['all','02','08','14','20'].includes(saved.hour)?saved.hour:'all';
  $('common').checked=saved.common!==false;$('across-leads').checked=saved.across===true;
  $('time-mode').value=saved['time-mode']==='leads'?'leads':'models';timeCommon=saved.timeCommon===true;
  if(saved.single&&selected.has(saved.single))$('single').value=saved.single;
  if(saved.view&&['multi','single','lead','matrix','time','performance','info'].includes(saved.view))view=saved.view;
  if(['matrix','performance'].includes(view)&&kind!=='降水')view='multi';
  for(const name of ['matrix','performance'])document.querySelector(`[data-view="${name}"]`).hidden=kind!=='降水';
  if(saved.lead===undefined){
    const raw=baseRows([...selected]),rank=ds.leads.map(lead=>({lead,n:C.commonTimes(raw.filter(r=>r.lead===lead),[...selected]).length}));
    if(!rank.find(x=>x.lead===Number($('lead').value))?.n){rank.sort((a,b)=>b.n-a.n||Math.abs(a.lead-24)-Math.abs(b.lead-24));if(rank[0]?.n)$('lead').value=rank[0].lead;}
  }

  $('accumulation-label').hidden=kind!=='降水';
  $('accumulation-switch').hidden=kind!=='降水';
  $('accumulation-switch').replaceChildren(...unique(catalog.datasets.filter(d=>d.kind==='降水').map(d=>d.accumulation)).sort((a,b)=>a-b).map(hours=>{
    const b=document.createElement('button');b.textContent=hours+' 小时累计';b.setAttribute('aria-pressed',hours===ds.accumulation);
    b.onclick=()=>{const target=catalog.datasets.find(d=>d.kind==='降水'&&d.accumulation===hours&&d.source===ds.source)||catalog.datasets.find(d=>d.kind==='降水'&&d.accumulation===hours);$('dataset').value=target.id;setDataset();};return b;
  }));
  validOptions();if(saved.valid&&[...$('valid').options].some(o=>o.value===saved.valid))$('valid').value=saved.valid;
  render();
}
function setDates(days){
  const {start,end}=days==='all'?{start:ds.start.slice(0,10),end:ds.end.slice(0,10)}:C.calendarWindow(days);
  $('start').value=start;$('end').value=end;
  document.querySelectorAll('[data-days]').forEach(b=>b.classList.toggle('chosen',b.dataset.days===String(days)));
}
function dates(days){if(ds){setDates(days);scoreScope='period';render();}}
function baseRows(models){const rows=catalog.rows.filter(r=>r.dataset===ds.id&&(kind!=='环流'||C.circulationHasScore(r,circulationField))&&r.valid.slice(0,10)>=$('start').value&&r.valid.slice(0,10)<=$('end').value&&($('hour').value==='all'||r.run.slice(11,13)===$('hour').value));return C.demoRows(rows,models,window.DemoModels.profiles(),WN3);}
function scopeRows(fixed=true){
  let raw=baseRows(activeModels());
  raw=leadRows(raw);
  if(kind==='降水'&&!['multi','single','matrix'].includes(view))raw=raw.filter(r=>r.threshold===Number($('threshold').value));
  if(fixed&&instantSelection())raw=raw.filter(r=>r.valid===$('valid').value);
  return raw;
}
function validOptions(){
  if(!ds||kind==='降水')return;
  let raw=scopeRows(false);if(useCommon())raw=C.commonTimes(raw,activeModels(),commonOptions());
  const old=$('valid').value,times=unique(raw.map(r=>r.valid)).sort();
  options($('valid'),times.map(t=>[t,full(t)]),times.includes(old)?old:times.at(-1));
}
function currentRows(){const raw=scopeRows();return useCommon()?C.commonTimes(raw,activeModels(),commonOptions()):raw;}
function resultGroups(rows,keys){
  if(groupSummary())keys=keys.filter(k=>k!=='lead');
  const provenance=new Map(),key=r=>JSON.stringify(keys.map(k=>r[k]));
  for(const r of rows){if(!provenance.has(key(r)))provenance.set(key(r),new Set());provenance.get(key(r)).add(r.source_dataset||r.dataset);}
  return C.grouped(rows,keys).filter(r=>kind==='降水'||r[$('metric').value]!==undefined).map(r=>{
    const ids=[...provenance.get(key(r))];
    const demo=window.DemoModels.profile(r.model);
    return {...r,...(demo?{is_demo:true,excluded_from_official_scores:true,source_model:WN3,target_product_id:demo.target_product_id}:{}),...(groupSummary()?{lead:null,lead_group:leadGroup,lead_group_label:selectedGroup().label}:{}),source_datasets:ids.join(';'),source_label:unique(ids.map(id=>sourceName((catalog.origins||catalog.datasets).find(d=>d.id===id),r.model))).join(' / ')};
  });
}
function empty(title='当前条件下没有可比较的样本',detail='请调整日期、时效或模式；可展开“样本明细”查看可用资料。'){return `<div class="empty"><strong>${esc(title)}</strong><span>${esc(detail)}</span></div>`;}
function stats(rows){
  const models=unique(rows.map(r=>r.model)),times=unique(rows.map(r=>r.valid)).sort(),pairs=unique(rows.map(r=>r.lead+'|'+r.valid));
  const values=[['参与模式',models.length,'个','当前图表范围'],['参与时刻',times.length,'个','实际参与评分的时刻数'],['时效 / 时刻',pairs.length,'组','同一时效和有效时刻只计一次'],['资料截至',short(times.at(-1)),'','北京时间 BJT']];
  $('stats').innerHTML=values.map(([l,v,u,n])=>`<div class="stat" title="${n}"><div class="stat-label">${l}</div><div class="stat-value">${v}<small>${u}</small></div></div>`).join('');
}
function coverageDetails(raw,used){
  const models=activeModels(),cov=C.sampleCoverage(raw,models,commonOptions()),included=useCommon();
  const keys=unique(raw.map(r=>JSON.stringify([r.lead,r.valid]))).map(k=>JSON.parse(k)).sort((a,b)=>a[1].localeCompare(b[1])||a[0]-b[0]);
  const recordKey=r=>JSON.stringify([r.model,r.lead,r.valid]),available=new Set(raw.map(recordKey)),retained=new Set(used.map(recordKey));
  const grid=keys.length?`<div class="heatmap" style="grid-template-columns:160px repeat(${keys.length},minmax(13px,1fr))">${models.map(m=>`<span>${esc(label(m))}</span>`+keys.map(([lead,valid])=>{
    const key=JSON.stringify([m,lead,valid]),state=retained.has(key)?'yes':available.has(key)?'excluded':'',cellLabel=state==='yes'?'参与当前结果':state==='excluded'?'有归档，被共同样本筛选排除':'未归档';
    return `<span class="heat-cell ${state}" title="${esc(label(m))} · +${lead}h · ${full(valid)} BJT · ${cellLabel}"></span>`;
  }).join('')).join('')}</div>`:'<p>所选范围没有归档记录。</p>';
  return `<p>计数单位为“预报时效＋有效时间”，同一组合的多个阈值只计一次。共同样本逐阈值取交集；组合保留任一阈值即计入，具体阈值的时次数见评分明细。${commonOptions().acrossLeads?'各时效进一步使用同一组有效时间。':'各时效分别匹配有效时间。'}这里的并集仅代表现有归档，不是理论应有资料量，也不保证各模式采用完全相同的格点。</p><table><thead><tr><th>模式</th><th>可用组合</th><th>参与组合</th><th>筛选排除</th></tr></thead><tbody>${cov.perModel.map(r=>`<tr><td>${esc(label(r.model))}</td><td>${r.availableCount}</td><td>${included?r.commonCount:r.availableCount}</td><td>${included?r.excludedCount:0}</td></tr>`).join('')}</tbody></table><p class="coverage-legend">青色：参与结果　琥珀色：有归档但被筛除　灰色：未归档；悬停查看时效与时间。</p>${grid}`;
}
function sampleSummary(raw,used){
  const c=C.sampleCoverage(raw,activeModels(),commonOptions()),on=useCommon();
  $('sample-title').textContent=`样本明细 · 可用 ${c.unionCount} 组${on?' · 共同 '+c.commonCount+' 组'+(c.excludedCount?' · 排除 '+c.excludedCount+' 组':''):''}`;
  $('sample-details').innerHTML=coverageDetails(raw,used);
}
function svgStart(title,w=1000,h=350){return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}"><title>${esc(title)} · ${esc($('chart-subtitle').textContent)}</title><rect width="100%" height="100%" fill="white"/>`;}
function axes(metric,values,w=1000,h=350){const L=70,R=w-35,T=28,B=h-61;let lo=0,hi=1,vals=values.filter(Number.isFinite);if(!['TS','POD','FAR','MR','ETS','CC'].includes(metric)){lo=Math.min(0,...vals);hi=Math.max(metric==='BIAS'?1:0,...vals);const pad=(hi-lo||1)*.12;hi+=pad;if(lo<0)lo-=pad;}else if(['ETS','CC'].includes(metric)){lo=Math.min(0,...vals);if(lo<0)lo=-1;}const y=v=>B-(v-lo)/(hi-lo)*(B-T);let markup='';for(let i=0;i<=5;i++){const v=lo+(hi-lo)*i/5;markup+=`<line class="grid" x1="${L}" x2="${R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L-12}" y="${y(v)+4}" text-anchor="end">${fmt(v,Math.abs(hi)>10?1:2)}</text>`;}markup+=`<path class="axis" fill="none" d="M${L} ${T}V${B}H${R}"/><text x="${L}" y="15">${esc(metric)} ${esc(unit())}</text>`;if(metric==='BIAS')markup+=`<line x1="${L}" x2="${R}" y1="${y(1)}" y2="${y(1)}" stroke="#a0bbc7" stroke-dasharray="4 4"/>`;return {L,R,T,B,y,markup};}
function bars(data,xKey,xlabel){
  if(!data.length)return empty();
  const m=$('metric').value,models=unique(data.map(r=>r.model)),xs=unique(data.map(r=>r[xKey])).sort((a,b)=>typeof a==='number'?a-b:String(a).localeCompare(String(b)));
  const a=axes(m,data.map(r=>r[m])),group=(a.R-a.L)/xs.length,bw=Math.min(37,group*.75/models.length);
  let s=svgStart($('chart-title').textContent)+a.markup;
  for(let j=0;j<xs.length;j++){
    const center=a.L+group*(j+.5);
    s+=`<text x="${center}" y="${a.B+24}" text-anchor="middle">${esc(xKey==='model'?displayModel(xs[j]):xs[j])}</text>`;
    for(let k=0;k<models.length;k++){
      if(xKey==='model'&&models[k]!==xs[j])continue;
      const r=data.find(r=>r[xKey]===xs[j]&&r.model===models[k]),v=r?.[m],defined=Number.isFinite(v);
      const x=xKey==='model'?center-bw/2:center+(k-models.length/2)*bw+2,width=Math.max(3,bw-4),zero=a.y(0),y=defined?a.y(v):zero;
      const top=Math.min(zero,y),height=Math.max(1,Math.abs(y-zero)),hitWidth=Math.max(width,Math.min(18,bw)),hitHeight=Math.max(18,height);
      const hitY=Math.max(a.T,Math.min(a.B-hitHeight,top+height/2-hitHeight/2));
      const label=`${displayModel(models[k])} · ${xlabel} ${xKey==='model'?displayModel(xs[j]):xs[j]}\n${m}: ${fmt(v)}${!defined?'（无定义或缺测）':''}\n${r?'有效时次: '+(r.cycles??1)+' · 样本: '+(r.n??'未记录'):'无可用样本'}`;
      s+=`<g class="point"><title>${esc(label)}</title>`;
      s+=defined?`<rect class="bar-shape" x="${x}" y="${top}" width="${width}" height="${height}" rx="2" fill="${color(models[k])}"/>`:`<text x="${x+bw/2}" y="${a.B-5}" text-anchor="middle">—</text>`;
      s+=`<rect class="point-hitbox" x="${x+width/2-hitWidth/2}" y="${hitY}" width="${hitWidth}" height="${hitHeight}" fill="transparent" pointer-events="all"/></g>`;
    }
  }
  return s+`<text x="${(a.L+a.R)/2}" y="337" text-anchor="middle">${esc(xlabel)}</text></svg>`;
}
let chartValueEvents=null;
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
    const label=point.getAttribute('aria-label');if(tip.textContent!==label)tip.textContent=label;
    tip.hidden=false;
    const box=point.getBoundingClientRect(),x=event?.clientX??box.left+box.width/2,y=event?.clientY??box.top;
    const w=tip.offsetWidth,h=tip.offsetHeight;
    tip.style.left=Math.max(10,Math.min(innerWidth-w-10,x+12))+'px';
    tip.style.top=Math.max(10,Math.min(innerHeight-h-10,y-h-12<10?y+15:y-h-12))+'px';
  };
  const toggle=(point,event)=>{
    if(pinned===point){hide();return;}
    hide();pinned=point;point.classList.add('is-selected');point.setAttribute('aria-pressed','true');show(point,event);
  };
  for(const point of points){
    const title=point.querySelector('title');if(!title)continue;
    const label=title.textContent;point.setAttribute('aria-label',label);point.setAttribute('role','button');point.setAttribute('tabindex','0');point.setAttribute('aria-pressed','false');
    const description=document.createElementNS('http://www.w3.org/2000/svg','desc');description.textContent=label;title.replaceWith(description);
    point.addEventListener('pointerenter',event=>show(point,event),{signal});
    point.addEventListener('pointermove',event=>show(point,event),{signal});
    point.addEventListener('pointerleave',()=>{if(!pinned)tip.hidden=true;},{signal});
    point.addEventListener('focus',()=>show(point),{signal});
    point.addEventListener('blur',()=>{if(!pinned)tip.hidden=true;},{signal});
    point.addEventListener('click',event=>toggle(point,event),{signal});
    point.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();toggle(point);}},{signal});
  }
  chart.addEventListener('click',event=>{if(!event.target.closest('.point'))hide();},{signal});
  document.addEventListener('click',event=>{if(!chart.contains(event.target))hide();},{signal});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')hide();},{signal});
  const reposition=()=>{
    if(!pinned){tip.hidden=true;return;}
    const box=pinned.getBoundingClientRect();
    if(box.bottom<0||box.top>innerHeight||box.right<0||box.left>innerWidth){hide();return;}
    show(pinned);
  };
  window.addEventListener('resize',reposition,{signal});
  window.addEventListener('scroll',reposition,{signal,capture:true});
}

function lines(data,xKey,xlabel){if(!data.length)return empty();const m=$('metric').value,models=unique(data.map(r=>r.model)),xs=unique(data.map(r=>r[xKey])).sort((a,b)=>typeof a==='number'?a-b:String(a).localeCompare(String(b))),a=axes(m,data.map(r=>r[m]));const numeric=v=>xKey==='valid'?Date.parse(v+'+08:00'):Number(v),min=numeric(xs[0]),max=numeric(xs.at(-1)),x=v=>xs.length===1?(a.L+a.R)/2:a.L+(numeric(v)-min)/(max-min)*(a.R-a.L);let s=svgStart($('chart-title').textContent)+a.markup;const stride=Math.ceil(xs.length/7);xs.forEach((v,i)=>{if(i%stride===0||i===xs.length-1)s+=`<text x="${x(v)}" y="${a.B+23}" text-anchor="middle">${esc(xKey==='valid'?short(v):'+'+v+'h')}</text>`;});for(const model of models){let segment=[],segments=[],last=null;for(const xv of xs){const r=data.find(r=>r[xKey]===xv&&r.model===model),v=r?.[m],gap=xKey==='valid'&&last!==null&&numeric(xv)-numeric(last)>(ds.source==='weekly'||kind==='环流'?6:12)*3600000;if(!Number.isFinite(v)||gap){if(segment.length)segments.push(segment);segment=[];}if(Number.isFinite(v)){segment.push(`${x(xv)},${a.y(v)}`);s+=`<circle class="point" cx="${x(xv)}" cy="${a.y(v)}" r="4" fill="${color(model)}"><title>${esc(label(model))} · ${esc(xKey==='valid'?full(xv):'+'+xv+'h')}\n${m}: ${fmt(v)}\n有效时次: ${r.cycles??1}</title></circle>`;}last=xv;}if(segment.length)segments.push(segment);for(const seg of segments)s+=`<polyline points="${seg.join(' ')}" fill="none" stroke="${color(model)}" stroke-width="2" opacity=".8"/>`;}
  return s+`<text x="${(a.L+a.R)/2}" y="337" text-anchor="middle">${esc(xlabel)}</text></svg>`;
}
function performance(data){if(!data.length)return empty();const L=235,T=25,size=330,B=T+size,R=L+size,x=v=>L+v*size,y=v=>B-v*size;let s=svgStart('降水评分综合图',1000,420);for(let i=0;i<=10;i++){const v=i/10;s+=`<path class="grid" d="M${x(v)} ${T}V${B}M${L} ${y(v)}H${R}"/>`;if(i%2===0)s+=`<text x="${x(v)}" y="${B+21}" text-anchor="middle">${v.toFixed(1)}</text><text x="${L-14}" y="${y(v)+4}" text-anchor="end">${v.toFixed(1)}</text>`;}
  for(const ts of [.1,.2,.3,.4,.5,.6,.7,.8,.9]){const pts=[];for(let i=1;i<=250;i++){const xx=i/250,yy=ts*xx/(xx*(1+ts)-ts);if(yy>=0&&yy<=1)pts.push(`${x(xx)},${y(yy)}`);}s+=`<polyline points="${pts.join(' ')}" fill="none" stroke="#cfac74" stroke-width="1.1"/><text x="${R+8}" y="${y(ts)+3}" style="fill:#b4935a;font-size:10px">${ts.toFixed(1)}</text>`;}
  for(const bias of [.25,.5,1,2,4]){const xx=Math.min(1,1/bias),yy=xx*bias;s+=`<path d="M${L} ${B}L${x(xx)} ${y(yy)}" stroke="#8cb6c8" stroke-dasharray="4 4"/><text x="${x(xx)-8}" y="${y(yy)-6}" text-anchor="end" style="fill:#6798ae;font-size:10px">${bias}</text>`;}
  let valid=0;for(const r of data){if(!Number.isFinite(r.POD)||!Number.isFinite(r.SR))continue;valid++;s+=`<circle class="point" cx="${x(r.SR)}" cy="${y(r.POD)}" r="6" fill="${color(r.model)}" stroke="white" stroke-width="2"><title>${esc(label(r.model))}\n成功率: ${fmt(r.SR)} · 命中率: ${fmt(r.POD)}\nTS: ${fmt(r.TS)} · BIAS: ${fmt(r.BIAS)}\nH ${r.H} / M ${r.M} / F ${r.F}</title></circle>`;}
  s+=`<path class="axis" fill="none" d="M${L} ${T}V${B}H${R}"/><text x="${(L+R)/2}" y="${B+49}" text-anchor="middle">成功率（1 − FAR） →</text><text transform="translate(${L-48} ${(T+B)/2}) rotate(-90)" text-anchor="middle">命中率 POD →</text><text x="${R+85}" y="105" style="font-size:14px;fill:#315968">如何读图</text><text x="${R+85}" y="137">右上方：命中率与成功率更高</text><text x="${R+85}" y="166" style="fill:#b4935a">金色曲线：TS 等值线</text><text x="${R+85}" y="195" style="fill:#6798ae">蓝色虚线：BIAS，1 为无频率偏差</text><text x="${R+85}" y="230">有效点 ${valid} / ${data.length} 个模式</text><text x="${R+85}" y="257">分母为零时不绘制该模式点</text>`;
  return s+'</svg>';
}

function matrix(data){
  if(!data.length)return empty();
  const metric=$('metric').value,leads=groupActive()?ds.leads.filter(l=>C.inLeadGroup(l,leadGroup)):ds.leads,thresholds=ds.thresholds,values=data.map(r=>r[metric]).filter(Number.isFinite),w=Math.max(1000,140+leads.length*60),left=110,top=58,cw=(w-left-30)/leads.length,ch=43,h=top+ch*thresholds.length+92;
  const lo=metric==='ETS'?Math.min(0,...values):0,hi=['TS','POD','FAR','MR','ETS'].includes(metric)?1:Math.max(1,...values);
  const shade=v=>{const t=Math.max(0,Math.min(1,(v-lo)/(hi-lo)));return `rgb(${Math.round(235-214*t)},${Math.round(247-119*t)},${Math.round(246-101*t)})`;};
  let svg=svgStart($('chart-title').textContent,w,h).replace('<svg ',`<svg style="min-width:${w}px" `);
  svg+=`<text x="18" y="29">阈值 ≥ mm</text>`;
  leads.forEach((lead,j)=>svg+=`<text x="${left+(j+.5)*cw}" y="29" text-anchor="middle">+${lead}h</text>`);
  thresholds.forEach((threshold,i)=>{
    svg+=`<text x="92" y="${top+(i+.5)*ch+4}" text-anchor="end">≥ ${threshold}</text>`;
    leads.forEach((lead,j)=>{
      const r=data.find(x=>x.lead===lead&&x.threshold===threshold),v=r?.[metric],defined=Number.isFinite(v),t=defined?(v-lo)/(hi-lo):0;
      svg+=`<g class="point"><title>${esc(displayModel($('single').value))} · +${lead}h · ≥${threshold} mm\n${metric}: ${fmt(v)}\n${r?'有效时次 '+r.cycles+' · H '+r.H+' / M '+r.M+' / F '+r.F:'没有参与当前筛选的资料'}${r&&!defined?'；分母为零，评分无定义':''}</title><rect x="${left+j*cw+2}" y="${top+i*ch+2}" width="${cw-4}" height="${ch-4}" rx="4" fill="${defined?shade(v):'#edf1f4'}"/><text x="${left+(j+.5)*cw}" y="${top+(i+.5)*ch+4}" text-anchor="middle" style="fill:${t>.6?'#fff':'#284b58'}">${fmt(v,2)}</text></g>`;
    });
  });
  const bottom=top+thresholds.length*ch+24;
  for(let k=0;k<60;k++)svg+=`<rect x="${left+k*4}" y="${bottom}" width="4" height="10" fill="${shade(lo+(hi-lo)*k/59)}"/>`;
  svg+=`<text x="${left}" y="${bottom+28}">${fmt(lo,2)}</text><text x="${left+240}" y="${bottom+28}" text-anchor="end">${fmt(hi,2)}</text><text x="${left+270}" y="${bottom+10}">颜色越深，数值越大；优劣请按指标解释判断。灰色 — 为无资料或评分无定义。</text>`;
  return svg+'</svg>';
}
function info(){return `<div class="info-grid"><div class="info-block"><h3>参考资料与空间范围</h3><p>${esc(ds.reference)}</p><p>${esc(ds.region)}</p><p>${kind==='环流'?`${ds.level} hPa · ${elementName()}；${circulationField==='wind'?'风矢量误差单位 m/s。':'高度误差单位 m，空间相关无量纲。'}`:'格点分析场；配对格点数不等于独立站点数。'}</p><p>${ds.reference.includes('ERA5')?'此处使用 ERA5 再分析；近期 ERA5T 为初步资料，通常延迟约5天。':'此处评分参考：'+esc(ds.reference)+'。'}</p></div><div class="info-block"><h3>时间与样本口径</h3><p>起报时间 + 预报时效 = 预报对应时间（气象上称有效时间）；全部显示北京时间。</p><p>${kind==='降水'?`降水是有效时间之前 ${ds.accumulation} 小时的累计量，按 ≥ 阈值分别评分。`:'整段时间汇总：各时刻同等权重，ME、MAE取平均，RMSE和风矢量RMSE先平均平方再开方；CC显示各时刻空间相关的平均值。单个时刻和时间序列显示原始评分。'}</p><p>时效分组：临近0–24小时，短期超过24到72小时，中期超过72到168小时。24小时和72小时各只计入一组。分组汇总将每个“预报时效＋有效时间”作为一份预报；连续量各份预报等权，降水先合并命中、漏报、空报计数再评分。起报0小时仅在已有可配对资料时参与。</p><p>模式对比逐时效、逐阈值匹配共同时间；分组只汇总筛选后留下的组合。时间序列默认保留各模式已有时次。</p><p>${esc(displayText(ds.source_note||'共同有效时间不等于已验证完全相同的格点。'))}</p></div><div class="info-block"><h3>指标与无定义值</h3>${kind==='降水'?'<p>TS = H/(H+M+F)；POD = H/(H+M)；FAR = F/(H+F)。</p><p>MR = M/(H+M)；BIAS = (H+F)/(H+M)。</p><p>先累加 H/M/F 再计算评分；不平均逐次 TS。</p><p>分母为零显示 —，仍视为已归档记录。缺少总样本 N 时不提供 ETS。</p>':(kind==='环流'&&circulationField==='wind'||kind==='地面'&&ds.element.startsWith('wind'))?'<p>VRMSE = √mean[(u预报−u分析)² + (v预报−v分析)²]。</p><p>合并东西、南北风分量误差，单位 m/s，越接近 0 越好。</p><p>期间汇总先平均各时刻 VRMSE 的平方，再开方；无资料不计零分。</p>':'<p>ME、MAE、RMSE 分别为平均误差、平均绝对误差、均方根误差。</p><p>CC 表示空间相关；期间汇总显示平均空间相关，并非合并全部格点后计算的相关系数。</p><p>无资料不计零分；某指标在参与时刻中存在无定义值时，汇总值显示 —。</p>'}</div><div class="info-block"><h3>资料来源和限制</h3><p>检验归档缓存每 5 分钟更新。</p><p>读取 ${catalog.status.accepted} 份；未识别 ${catalog.status.unverified} 份；失败 ${catalog.status.unreadable} 份。</p><p>归档不足以支持年度报告、台站空间评分或降水过程诊断，当前仅展示可计算的结果。</p><p>展开“样本明细”查看筛选排除原因。</p></div></div>`;}
function renderTable(rows){const metrics=metricsForView();tableColumns=[['model','模式'],...(['time','info'].includes(view)?[['valid','有效时间 BJT']]:[]),...(groupSummary()?[['lead_group_label','时效分组'],['lead_hours','参与时效 h'],['forecast_count','时效 / 时刻组合']]:[['lead','时效 h']]),...(kind==='降水'?[['threshold','阈值 ≥ mm']]:[]),...metrics.map(m=>[m,metricColumn(m)]),['cycles','有效时次'],['n','配对样本 N'],...(kind==='降水'?[['H','命中 H'],['M','漏报 M'],['F','空报 F']]:[])];
  tableRows=rows.map(r=>({...r,element:elementName(),reference:ds.reference,accumulation:ds.accumulation,level:ds.level,height_m:ds.height_m,region:ds.region,period_start:$('start').value,period_end:$('end').value,run_hour:$('hour').value, score_scope:periodSummary()?'整段时间汇总':instantSelection()?'单个时刻':kind==='降水'?'期间累计计数':'逐时刻评分',lead_selection:groupActive()?'分组检验':manyLeads()?'逐时效检验':'单个时效',lead_group_bounds:groupActive()?(selectedGroup().includeMin?'0≤时效≤24':selectedGroup().min+'<时效≤'+selectedGroup().max):'',aggregation_rule:kind==='降水'?'累计H/M/F后评分':groupSummary()?'各时效/时刻组合等权；RMSE先平均平方再开方；CC为平均空间相关':periodSummary()?'各时刻等权；RMSE先平均平方再开方；CC为平均空间相关':'单次原始评分',fixed_valid:instantSelection()?$('valid').value:'', valid_times:r.valid_times||r.valid, sample_rule:useCommon()?(commonOptions().acrossLeads?'模式和所有时效共同有效时间（逐阈值）':'逐时效、逐阈值共同有效时间'):'各自有效时次'}));
  const metric=$('metric').value;$('results-table').innerHTML='<thead><tr>'+tableColumns.map(c=>`<th scope="col">${esc(c[1])}</th>`).join('')+'</tr></thead><tbody>'+tableRows.slice(0,600).map(r=>'<tr>'+tableColumns.map(([key])=>`<td${key===metric?' class="metric-cell"':''}>${metrics.includes(key)?fmt(r[key]):key==='valid'?esc(full(r[key])):r[key]==null?'—':typeof r[key]==='number'?fmt(r[key],key==='threshold'?1:0):esc(key==='model'?label(r[key]):key==='lead_hours'?r[key].split(';').join('、'):r[key])}</td>`).join('')+'</tr>').join('')+'</tbody>';
  $('table-note').textContent=`${rows.length} 行 · — 为缺测或无定义${rows.length>600?'；显示前600行，CSV导出全部':''}`;$('export-csv').disabled=!rows.length;
}

function wn3CoverageNote(availableRows){
  let note=$('wn3-source-note');
  if(!note){note=document.createElement('p');note.id='wn3-source-note';note.className='filter-note current-source';$('filter-note').after(note);}
  note.hidden=['地面','环流'].includes(kind)||Boolean(ds.pilot)||!(ds.supported_models||ds.models).includes(WN3);
  if(note.hidden)return;
  const rows=availableRows.filter(r=>r.model===WN3),count=new Set(rows.map(r=>JSON.stringify([r.lead,r.valid]))).size;
  const provenance=displayText(ds.model_sources?.[WN3]?.note||'WN3 使用本机缓存的 0.1° 集合平均；仅接入标准 2 米气温和完整小时段组成的 3 小时降水。');
  note.textContent=provenance+' '+(count?`当前日期、起报和时效有 ${count} 组评分；共同样本按有效时间取交集。`:'当前日期、起报和时效没有 WN3 评分；缺资料不计为零分。');
}

function periodCoverage(rows){
  const box=$('period-summary');box.hidden=!periodSummary();if(box.hidden)return;
  const start=$('start').value,end=$('end').value;
  if(!start||!end){box.textContent='请选择完整的开始和结束日期。';return;}
  if(start>end){box.textContent='开始日期晚于结束日期，请重新选择。';return;}
  const total=Math.round((Date.parse(end+'T00:00:00Z')-Date.parse(start+'T00:00:00Z'))/86400000)+1;
  const times=unique(rows.map(r=>r.valid)).sort(),days=unique(times.map(t=>t.slice(0,10))),missing=Math.max(0,total-days.length);
  box.textContent=`统计范围：${start} 至 ${end}（${total}天）。实际参与 ${times.length} 个时刻，覆盖 ${days.length} 天${missing?'；'+missing+'天暂无可用评分':''}。`+(times.length?`参与资料：${full(times[0])} 至 ${full(times.at(-1))}，北京时间。`:'')+'仅汇总已有评分，缺资料不计零分。';
}

function leadControls(){
  for(const b of document.querySelectorAll('[data-lead-mode]')){
    b.setAttribute('aria-pressed',b.dataset.leadMode===leadMode);
    if(b.dataset.leadMode==='single')b.textContent=manyLeads()?'全部时效':'单个时效';
  }
  $('lead-single-controls').hidden=groupActive()||manyLeads();
  $('lead-group-switch').hidden=!groupActive();$('lead-group-help').hidden=!groupActive();
  $('lead-group-help').textContent='按起报后经过的小时数分组。24小时计入临近预报，72小时计入短期预报。'+(kind==='降水'?`组内各时效均检验${ds.accumulation}小时累计降水。`:'每个时效只归入一组。');
  const raw=baseRows(ds.models);
  $('lead-group-switch').replaceChildren(...C.leadGroups.map(g=>{
    const b=document.createElement('button');b.type='button';b.dataset.leadGroup=g.id;
    b.textContent=g.label+(C.filterLeadGroup(raw,g.id).length?'':' · 暂无评分');
    b.setAttribute('aria-pressed',g.id===leadGroup);
    b.onclick=()=>{leadGroup=g.id;scoreScope='period';render();};return b;
  }));
  const select=$('lead'),items=[...select.options],dense=items.length>12;
  select.hidden=!dense;
  const shortcuts=dense?items.filter(o=>[0,6,12,24,48,72,120,168,Number(select.value)].includes(Number(o.value))):items;
  $('lead-switch').replaceChildren(...shortcuts.map(o=>{
    const b=document.createElement('button');b.type='button';b.textContent=o.textContent;
    b.dataset.value=o.value;b.setAttribute('aria-pressed',o.value===select.value);
    b.onclick=()=>{select.value=o.value;render();};return b;
  }));
}
function leadGroupCoverage(available,used){
  const box=$('lead-group-summary');box.hidden=!groupActive();if(box.hidden)return;
  const g=selectedGroup(),show=rows=>C.forecastCoverage(rows).lead_hours.split(';').filter(Boolean).map(h=>'+'+h+'小时').join('、')||'无';
  const a=C.forecastCoverage(available),u=C.forecastCoverage(used),omitted=a.forecast_count-u.forecast_count;
  const method=groupSummary()?(kind==='降水'?'累计组内H/M/F后评分。':'每个时效与时刻组合等权汇总。'):'当前视图逐时效展示组内评分。';
  box.innerHTML=`<strong>${esc(g.label)}</strong>：${g.includeMin?'包含0和24小时':'超过'+g.min+'小时，至'+g.max+'小时（含）'}。${esc(method)}<div class="group-coverage-line">所选模式可用时效：${show(available)}；实际参与：${show(used)}，共 ${u.forecast_count} 个时效 / 时刻组合。${omitted>0?'共同样本筛选排除 '+omitted+' 组。':''}</div><div class="group-coverage-line">仅统计已有且满足筛选条件的评分，不代表组内每小时都有资料；缺资料不计零分。${!used.length?'可调整日期、模式或共同样本筛选。':''}</div>`;
  let eligible=leadRows(baseRows(ds.models));if(instantSelection())eligible=eligible.filter(r=>r.valid===$('valid').value);
  const availableModels=unique(eligible.map(r=>r.model));
  if(!singleMode()&&availableModels.length&&activeModels().some(m=>!availableModels.includes(m))){
    const button=document.createElement('button');button.type='button';button.className='quiet';button.id='lead-models-available';
    button.textContent='只比较本组有资料的模式';
    button.onclick=()=>{selected=new Set(availableModels);render();};box.append(button);
  }
}

function render(){
  if(!ds)return;
  let availableRows=baseRows(ds.models);
  availableRows=leadRows(availableRows);
  modelControls(availableRows);
  wn3CoverageNote(availableRows);
  window.DemoModels.gridNotice(kind,activeModels());
  $('analysis-source-note').hidden=!['环流','地面'].includes(kind);
  $('analysis-source-note').textContent=ds.source_note||'参考：'+ds.reference+'。';
  validOptions();
  const rain=kind==='降水',allThresholds=rain&&['multi','single','matrix'].includes(view);
  document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-selected',b.dataset.view===view));
  $('single-label').hidden=!singleMode();$('time-mode-label').hidden=view!=='time';
  $('threshold-label').hidden=!rain||allThresholds;$('lead-label').hidden=view==='info';leadControls();
  $('score-scope-field').hidden=rain||['time','info'].includes(view);
  for(const b of document.querySelectorAll('[data-score-scope]'))b.setAttribute('aria-pressed',b.dataset.scoreScope===scoreScope);
  $('valid-label').hidden=!instantSelection();
  const ccOption=[...$('metric').options].find(o=>o.value==='CC');if(ccOption)ccOption.textContent=metricLabel('CC');
  $('metric-label').hidden=['performance','info'].includes(view);
  $('common-label').hidden=view==='time';$('time-sample-controls').hidden=view!=='time';
  $('across-label').hidden=!(manyLeads()||groupActive());$('across-leads').disabled=!useCommon();
  for(const b of document.querySelectorAll('[data-time-sample]'))b.setAttribute('aria-pressed',(b.dataset.timeSample==='common')===timeCommon);
  $('scope-hint').textContent=groupActive()?(manyLeads()?'展示所选组内各时效':'汇总所选组内时效'):allThresholds?'当前视图展示全部降水阈值':manyLeads()?'当前视图展示全部已归档时效':'';
  $('export-svg').hidden=view==='info';
  const metric=$('metric').value,lead=Number($('lead').value),threshold=Number($('threshold').value),source=scopeRows(rain||Boolean($('valid').value)),raw=currentRows();
  stats(raw);sampleSummary(source,raw);periodCoverage(raw);leadGroupCoverage(source,raw);
  if(view==='time'){
    const available=unique(source.map(r=>r.valid)).length,retained=unique(raw.map(r=>r.valid)).length;
    $('time-sample-note').textContent=useCommon()&&retained<available?`共同时次保留 ${retained} / ${available} 个` : '';
  }
  const age=(Date.now()-Date.parse(ds.end+'+08:00'))/3600000;
  $('data-notice').hidden=age<=24;$('data-notice').textContent=`${ds.reference.includes('ERA5')?'ERA5参考资料截至':'资料截至'} ${full(ds.end)} BJT${ds.reference.includes('ERA5')?' · 再分析资料存在发布延迟':''}`;
  choiceControls('metric-switch','metric',true);choiceControls('threshold-switch','threshold');choiceControls('hour-switch','hour');validChoiceControls();
  const missing=(ds.supported_models||ds.models).filter(m=>!ds.models.includes(m));
  $('model-status').textContent=missing.length?missing.map(displayModel).join('、')+' 暂无'+labelFor(ds)+'评分。':'';
  const statusRow=$('model-status-row');if(statusRow)statusRow.hidden=!missing.length;
  $('active-filter-summary').textContent=($('hour').value==='all'?'全部起报时次':$('hour').value+'时起报')+' · '+(useCommon()?(commonOptions().acrossLeads?'跨时效共同时间':'共同有效时次'):view==='time'?'全部已有时次':'各自有效时次');
  $('filter-note').textContent=`${labelFor(ds)} · ${$('start').value} — ${$('end').value} BJT · ${ds.region}${ds.pilot?' · 单时次接入验证，不用于模式排名。':''}`;
  $('chart-subtitle').textContent=`${ds.region} · ${labelFor(ds)} · ${$('start').value} — ${$('end').value} · ${useCommon()?(commonOptions().acrossLeads?'模式与时效共同时间':'逐时效共同时间'):view==='time'?'各模式已有时次':'各自可用时间'}${instantSelection()?' · 仅看 '+full($('valid').value)+' 北京时间':periodSummary()?' · 整段时间汇总':''}`;
  let data=[],chart='',title='';
  if(['multi','single'].includes(view)){
    title=(ds.pilot?'WN3 集合平均 · 早期试接入记录':view==='single'?displayModel($('single').value)+' · 单模式检验':'多模式'+(rain?'降水阈值':'评分')+'对比')+' · '+leadCaption();
    data=resultGroups(raw,rain?['model','lead','threshold']:['model','lead']);setChartTitle(title);
    chart=bars(data,rain?'threshold':'model',rain?`${ds.accumulation}小时累计降水阈值 ≥（mm）`:'模式');
  }else if(view==='matrix'){
    title=`${displayModel($('single').value)||'单模式'} · 时效 × 阈值 · ${metric}`;
    data=resultGroups(raw,['model','lead','threshold']);setChartTitle(title);chart=matrix(data);
  }else if(view==='lead'){
    title=`预报时效对比${rain?' · ≥ '+threshold+' mm':''}`;data=resultGroups(raw,['model','lead',...(rain?['threshold']:[])]);setChartTitle(title);chart=lines(data,'lead','预报时效（小时）');
  }else if(view==='time'){
    title=(timeLeads()?`${displayModel($('single').value)||'单模式'} · 多时效时间序列`:`多模式时间序列 · ${leadCaption()}`)+(rain?' · ≥ '+threshold+' mm':'');
    data=resultGroups(raw,['model','lead','valid',...(rain?['threshold']:[])]);setChartTitle(title);
    chart=lines(timeLeads()?data.map(r=>({...r,model:'+'+r.lead+'h'})):data,'valid','有效时间（北京时间）');
  }else if(view==='performance'){
    title=`评分综合图 · ${leadCaption()} · ≥ ${threshold} mm`;data=resultGroups(raw,['model','lead','threshold']);setChartTitle(title);chart=performance(data);
  }else{
    title='资料覆盖与检验口径';data=resultGroups(raw,['model','lead','valid',...(rain?['threshold']:[])]);setChartTitle(title);chart=info();
  }
  const modelList=timeLeads()?unique(data.map(r=>r.lead)).sort((a,b)=>a-b).map(l=>'+'+l+'h'):activeModels();
  $('legend').innerHTML=view==='info'?'':modelList.map(m=>`<span><i style="background:${color(m)}"></i>${esc(label(m))}</span>`).join('');
  if(!raw.length&&view!=='info'&&activeModels().length){
    const candidates=scopeRows(false),missing=activeModels().filter(m=>!candidates.some(r=>r.model===m));
    if(!candidates.length)chart=empty('所选日期和时效没有评分归档','当前选择：'+$('start').value+'—'+$('end').value+'，'+leadCaption()+'。可调整日期、时效或模式；没有评分的时效不计零分。');
    else if(useCommon()&&missing.length)chart=empty('所选模式缺少共同样本',missing.map(label).join('、')+' 在当前日期和时效没有评分。可取消这些模式、切换时效，或在“更多筛选”关闭“仅共同有效时次”。');
    else if(useCommon())chart=empty('所选模式没有重合的有效时间','各模式有归档，但当前范围没有共同时间；请调整日期、模式或共同时间筛选。');
  }
  if(!$('start').value||!$('end').value){data=[];chart=empty('请选择完整的日期范围','填写开始日期和结束日期后即可查看评分。');}
  else if($('start').value>$('end').value){data=[];chart=empty('开始日期晚于结束日期','请重新选择有效日期范围。');}
  if(!activeModels().length&&view!=='info')chart=empty('请选择至少一个模式','可点击“全选模式”或“默认模式”恢复选择。');
  if(!ds.models.length&&['环流','地面'].includes(kind)&&view!=='info')chart=empty(labelFor(ds)+'暂无已配对评分',ds.availability_note||'当前参考资料没有可配对的本地预报。');
  $('chart').classList.toggle('matrix-chart',view==='matrix');$('chart').innerHTML=chart;chartValues();
  $('chart-explainer').textContent=view==='performance'?'综合图同时显示 POD、1−FAR、TS 和 BIAS；点击或悬停查看数值。':view==='info'?'样本不足与无事件不等于零分；不依据不完整资料自动排名。':`${metricHelp[metric]} ${unit()?'单位：'+unit()+'。':''} ${groupSummary()?(rain?'合并组内命中、漏报、空报计数后计算评分。':'每个“时效＋时刻”组合等权；RMSE先平均平方再开方，CC为平均空间相关。'):periodSummary()?'汇总所选日期内已有时刻；各时刻同等权重。'+(metric==='CC'?'此处为平均空间相关。':''):view==='time'?(useCommon()?'仅显示共同时次；缺测处断线。':'各模式保留已有时次；缺测处断线。')+'点击或悬停查看每次评分。':rain?'先汇总命中、漏报和空报计数，再评分；点击或悬停查看数值。':''}`;
  $('export-svg').disabled=!$('chart').querySelector('svg');renderTable(data);
  $('updated').textContent='结果更新 '+full(ds.updated)+' BJT';remember();
}
function download(blob,name){const u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),2000);}
$('export-csv').onclick=()=>{const cols=[...tableColumns,['is_demo','是否演示'],['source_model','演示真实来源'],['target_product_id','演示目标接口'],['excluded_from_official_scores','排除正式排名'],['element','检验要素'],['source_label','资料溯源渠道'],['source_datasets','原始归档ID'],['reference','参考资料'],['region','检验区域'],['accumulation','累计小时'],['level','气压 hPa'],['height_m','离地高度 m'],['period_start','开始有效日期'],['period_end','结束有效日期'],['sample_rule','样本规则'],['run_hour','起报时次BJT'],['score_scope','查看方式'],['aggregation_rule','汇总方法'],['fixed_valid','预报对应时间BJT'],['valid_times','实际参与有效时间BJT'],['lead_selection','时效检验方式'],['lead_group_bounds','分组边界小时'],...(!groupSummary()?[['lead_hours','实际参与时效小时'],['forecast_count','时效时刻组合数']]:[])];download(new Blob([C.csv(tableRows.map(r=>({...r,model:label(r.model)})),cols)],{type:'text/csv;charset=utf-8'}),`${activeModels().some(m=>window.DemoModels.profile(m))?'WN3演示_':''}检验_${exportElement()}_${view}_${$('end').value}.csv`);};
$('export-svg').onclick=()=>{const svg=$('chart').querySelector('svg');if(!svg)return;const copy=svg.cloneNode(true),style=document.createElementNS('http://www.w3.org/2000/svg','style');for(const point of copy.querySelectorAll('.point')){const d=point.querySelector('desc');if(d){const title=document.createElementNS('http://www.w3.org/2000/svg','title');title.textContent=d.textContent;d.replaceWith(title);}for(const name of ['role','tabindex','aria-pressed'])point.removeAttribute(name);point.classList.remove('is-selected');}style.textContent='text{font-family:"Microsoft YaHei",sans-serif;font-size:11px;fill:#718593}.grid{stroke:#edf2f5;stroke-width:1}.axis{stroke:#d5e1e9;stroke-width:1}';copy.prepend(style);download(new Blob([new XMLSerializer().serializeToString(copy)],{type:'image/svg+xml;charset=utf-8'}),`${activeModels().some(m=>window.DemoModels.profile(m))?'WN3演示_':''}检验_${exportElement()}_${view}.svg`);};

document.querySelectorAll('[data-kind]').forEach(b=>b.onclick=async()=>{if(await window.GridVerification.open())setKind(b.dataset.kind);});
document.querySelectorAll('[data-circulation-field]').forEach(b=>b.onclick=()=>setCirculationField(b.dataset.circulationField));
document.querySelectorAll('[data-days]').forEach(b=>b.onclick=()=>dates(b.dataset.days));
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{view=b.dataset.view;render();});
document.querySelectorAll('[data-time-sample]').forEach(b=>b.onclick=()=>{timeCommon=b.dataset.timeSample==='common';render();});
document.querySelectorAll('[data-score-scope]').forEach(b=>b.onclick=()=>{scoreScope=b.dataset.scoreScope;render();});
document.querySelectorAll('[data-lead-mode]').forEach(b=>b.onclick=()=>{
  leadMode=b.dataset.leadMode;
  if(leadMode==='group')scoreScope='period';
  render();
});
$('dataset').onchange=setDataset;
for(const id of ['metric','threshold','lead','hour','common','single','valid','time-mode','across-leads'])$(id).onchange=render;
for(const id of ['start','end'])$(id).onchange=()=>{document.querySelectorAll('[data-days]').forEach(b=>b.classList.remove('chosen'));render();};
$('models-all').onclick=()=>{selected=new Set(ds.models);modelControls();render();};
$('models-default').onclick=()=>{selected=new Set(ds.models.filter(m=>m!=='Google WeatherNext 2'&&!window.DemoModels.profile(m)));modelControls();render();};
async function load(){
  if(ds)remember();
  const previous=ds?.id||preferences.dataset,targetKind=ds?kind:preferences.kind||kind;
  let success=false;$('refresh').disabled=true;$('load-error').hidden=true;$('catalog-status-wrap').hidden=false;$('catalog-status').textContent='读取资料…';
  try{
    const response=await fetch('./api/catalog'+(ds?'?refresh=1':''),{cache:'no-store',signal:AbortSignal.timeout(65000)});
    if(!response.ok)throw new Error('HTTP '+response.status);
    const next=await response.json();if(!Array.isArray(next.rows)||!next.datasets?.length)throw new Error('暂无可识别的检验归档');
    await window.DemoModels.ready();catalog=C.demoCatalog(C.unifiedCatalog(next),window.DemoModels.profiles(),WN3);
    const combined=catalog.datasets.find(d=>matchesPrevious(d,previous));
    const preferred=combined?.id;
    if(combined&&previous!==combined.id&&!preferences.datasets[combined.id]){
      const saved=preferences.datasets[previous]||{};
      preferences.datasets[combined.id]={...saved,models:previous?.startsWith('compare:')?saved.models:combined.models.filter(m=>m!=='Google WeatherNext 2'&&!window.DemoModels.profile(m))};
    }
    preferences.unifiedVersion=1;
    setKind(catalog.datasets.some(d=>d.kind===targetKind)?targetKind:'降水',preferred);
    $('catalog-status').textContent='';$('catalog-status-wrap').hidden=true;success=true;
  }catch(error){
    $('load-error').hidden=false;$('load-error').textContent='检验资料暂时无法读取，请稍后点击“刷新资料”重试。'+(catalog?'下方保留上一次成功读取的结果。':'');
    $('catalog-status').textContent=catalog?'显示上次读取的资料':'资料暂不可用';
    if(!catalog)$('chart').innerHTML=empty('资料暂不可用','请稍后刷新或更换条件。');
  }finally{$('refresh').disabled=false;}
  return success;
}
let gridLoading=null;
window.GridVerification={open(){if(catalog){window.DemoModels.gridNotice(kind,activeModels());return Promise.resolve(true);}if(!gridLoading)gridLoading=load().finally(()=>gridLoading=null);return gridLoading;},refresh:load};
$('refresh').onclick=load;
