'use strict';
window.StationVerification=(()=>{
 const by=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const num=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:3}):'—';
 const metricNum=(v,m)=>window.VerificationCore?.formatMetric(v,m)??num(v);
 const metricLabel=m=>window.VerificationCore?.guifanLabels[m]||m;
 const finite=v=>typeof v==='number'&&Number.isFinite(v);
 const colors={ec01:'#087f88',ecmwf:'#0072b2',ecens:'#56b4e9',gfs:'#d55e00',aifs:'#009e73',aigfs:'#cc79a7',gefs:'#8a5a2b',wn3ml:'#b5179e',wn3:'#8b5cf6'};
 const sources={ogimet:'OGIMET SYNOP',ghcnh:'NOAA GHCNh',iem:'IEM METAR',szgrid:'深圳格点实况插值',szopen:'深圳气象局开放接口',nmic:'国家站三小时资料（2 分钟平均风）'};
 const format=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
 const time=v=>v?format.format(new Date(v)):'—';
 let meta=null,data=null,opened=false,seq=0,controller=null,detailController=null;
 let state={var:'t',days:15,models:['ec01','gfs'],model:'ec01',metric:'RMSE',threshold:.1,verificationMode:'cumulative',grade:1,station:'59493'};
 // hostId: standalone page, or a container inside the 降水/气温 grid page (allowed limits the variables).
 let hostId='stations-workspace',allowed=null;
 const dayLabels={1:'近24小时',3:'近3天',7:'近7天',15:'近15天'};
 // 2026-11-15 国家站资料晚到时近24小时按国家站最新时刻往前算（meta.window_ends['1'] 早于 window_end）。
 const windowNote=()=>{const e=meta?.window_ends?.['1'];return e&&meta.window_end&&e!==meta.window_end&&meta.nmic_end?`近24小时按国家站资料最新时刻（${time(meta.nmic_end)}）往前算。`:'';};
 const host=()=>by(hostId);
 const windows=()=>meta?.windows||[7,15];
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
 function shell(){host().innerHTML=`${allowed?'':'<div class="page-heading"><div><p class="eyebrow">STATION VERIFICATION</p><h1>站点检验</h1><p>中国南方 · 97–123°E / 18–33°N · 实测站点与本地固定起报预报</p></div></div>'}<div id="stations-status" class="notice" role="status">读取已发布站点资料…</div><section id="stations-controls" class="filter-card" aria-label="站点检验条件"></section><div id="stations-results"></div><section id="stations-detail" class="results" aria-label="单站时间序列"></section>`;}
 function controls(){
  state.verificationMode='cumulative';
  if(rain()&&!variable()?.thresholds.includes(state.threshold))state.threshold=.1;
  const grade=state.verificationMode==='grade'&&variable()?.grades?.length;
  const metrics=rain()?grade?['TS','POD','FAR','ETS','MR','BIAS']:['TS','POD','FAR','ETS','MR','BIAS',...(variable()?.has_trace?['PC']:[])]:['RMSE','ME','MAE','CC',...(variable()?.has_acc?['ACC2','ACC1']:[])];
  if(!metrics.includes(state.metric))state.metric=metrics[0];
  if(state.metric==='PC')state.threshold=.1;
  const variables=meta.variables.filter(v=>!allowed||allowed.includes(v.id));
  const dayButtons=Object.keys(dayLabels).map(Number).map(d=>`<button type="button" data-station-days="${d}" aria-pressed="${d===state.days}" ${windows().includes(d)?'':'disabled title="下次定时评分后提供"'}>${dayLabels[d]}</button>`).join('');
  by('stations-controls').innerHTML=`<div class="stations-filters">${variables.length>1?`<label>检验要素<select id="stations-var">${options(variables.map(v=>[v.id,v.label+' · '+v.unit]),state.var)}</select></label>`:''}<div class="filter-field stations-days"><span>日期范围</span><div id="stations-days" class="choice-buttons" role="group" aria-label="站点检验日期范围">${dayButtons}<button type="button" disabled title="站点资料仅保留近16天，暂不支持自定义日期">自定义</button></div></div><label>评分指标<select id="stations-metric">${options(metrics.map(x=>[x,metricLabel(x)]),state.metric)}</select></label>${rain()?`<label>${grade?'降水量级':'降水阈值 · mm'}<select id="stations-threshold" ${state.metric==='PC'?'disabled':''}>${options(grade?variable().grades.map(g=>[g.id,g.label+' '+g.range]):variable().thresholds.map(t=>[t,t]),grade?state.grade:state.threshold)}</select></label>`:''}</div><div class="model-row"><span>比较模式</span><div class="model-chips">${meta.models.map(m=>`<button type="button" class="model-chip" data-station-model="${esc(m.id)}" aria-pressed="${state.models.includes(m.id)}"><i style="background:${colors[m.id]}"></i>${esc(m.label)}</button>`).join('')}</div></div><p class="filter-note">${allowed?'站点实况：OGIMET SYNOP / NOAA GHCNh / IEM METAR，中国南方 97–123°E、18–33°N；评分由定时任务离线计算，日期范围从最近一次评分时刻往前算。':''}${windowNote()}连续量按全部站点配对合并；ME＝预报－实况。各模式使用各自有效样本，请结合 n 比较。降水阈值事件为累计量 ≥ 阈值，微量不计为 ≥0.1 mm。</p>`;
  if(by('stations-var'))by('stations-var').onchange=e=>{
   state.var=e.target.value;state.metric=rain()?'TS':'RMSE';state.threshold=.1;
   controls();loadScores();
  };
  by('stations-days').querySelectorAll('[data-station-days]').forEach(b=>b.onclick=()=>{state.days=Number(b.dataset.stationDays);controls();loadScores();});
  by('stations-metric').onchange=e=>{state.metric=e.target.value;controls();render();loadDetail();};
  if(by('stations-verification-mode'))by('stations-verification-mode').onchange=e=>{state.verificationMode=e.target.value;controls();render();loadDetail();};
  if(rain())by('stations-threshold').onchange=e=>{state[grade?'grade':'threshold']=Number(e.target.value);render();loadDetail();};
  by('stations-controls').querySelectorAll('[data-station-model]').forEach(b=>b.onclick=()=>{
   const model=b.dataset.stationModel;
   if(state.models.includes(model)&&state.models.length===1)return;
   state.models=state.models.includes(model)?state.models.filter(m=>m!==model):[...state.models,model];
   if(!state.models.includes(state.model))state.model=state.models[0];
   controls();loadScores();
  });
 }
 function chooseRows(rows){return rows.filter(r=>(!rain()||(state.verificationMode==='grade'?r.grade===state.grade:!r.grade&&r.threshold===state.threshold))&&(!window.VerificationCore||window.VerificationCore.eligible(r,state.metric)));}
 function modelSelect(id){return `<label>地图 / 单站模式<select id="${id}">${options(state.models.map(m=>[m,label(m)]),state.model)}</select></label>`;}
 let mapSequence=0;
 // Map viewport: zoom k (1 = whole box, max 40) around a centre, clamped to the box.
 const MAP_ZOOM_MAX=40;
 function mapFrame(meta,view){
  const base=view?.base==='cmagd'?'cmagd':view?.base==='xj'&&meta.xj_bounds?'xj':null;
  const [west,south,east,north]=base==='cmagd'?(meta.cmagd_bounds||[93.64,16.60,126.34,42.88]):base?meta.xj_bounds:meta.map_bounds||[104,18,121,27];
  const k=Math.min(MAP_ZOOM_MAX,Math.max(1,Number(view?.k)||1)),w=(east-west)/k,h=(north-south)/k;
  const clamp=(v,lo,hi)=>Math.min(hi,Math.max(lo,v));
  const cx=clamp(Number.isFinite(view?.cx)?view.cx:(west+east)/2,west+w/2,east-w/2),cy=clamp(Number.isFinite(view?.cy)?view.cy:(south+north)/2,south+h/2,north-h/2);
  return {k,cx,cy,w,h,base,west:cx-w/2,south:cy-h/2,east:cx+w/2,north:cy+h/2,full:[west,south,east,north]};
 }
 function poolRegionScore(list,metric){
  if(!list.length)return null;
  if(['ACC1','ACC2','PC'].includes(metric)){
   const eligible=list.filter(r=>metric==='PC'?Number.isFinite(r.T):Number.isFinite(r[metric==='ACC1'?'N1':'N2']));
   const n=eligible.reduce((v,r)=>v+r.n,0);
   return n?eligible.reduce((v,r)=>v+(metric==='PC'?r.n-r.M-r.F+r.T:r[metric==='ACC1'?'N1':'N2']),0)/n:null;
  }
  if(['TS','ETS','POD','FAR','MR','BIAS'].includes(metric)&&list.some(r=>Number.isFinite(r.H))){
   let H=0,M=0,F=0,N=0;
   for(const r of list){H+=(r.H||0);M+=(r.M||0);F+=(r.F||0);N+=(r.n||0);}
   const div=(a,b)=>b>0?a/b:null;
   if(metric==='TS')return div(H,H+M+F);
   if(metric==='POD')return div(H,H+M);
   if(metric==='FAR')return div(F,H+F);
   if(metric==='MR')return div(M,H+M);
   if(metric==='BIAS')return div(H+F,H+M);
   if(metric==='ETS'){const ch=N>0?(H+M)*(H+F)/N:0;return div(H-ch,H+M+F-ch);}
  }
  let sumW=0,sumV=0,sumSq=0;
  for(const r of list){
   const v=r[metric],w=r.n>0?r.n:1;
   if(!finite(v))continue;
   sumW+=w;sumV+=v*w;sumSq+=v*v*w;
  }
  if(!sumW)return null;
  return metric==='RMSE'?Math.sqrt(sumSq/sumW):sumV/sumW;
 }
 // Fixed bins, so one colour means one score on every view. Ramps pass the ordinal checks on both surfaces.
 function scoreScale(metric){
  const dark=window.matchMedia?.('(prefers-color-scheme: dark)').matches;
  const better=dark?['#184f95','#256abf','#3987e5','#6da7ec','#b7d3f6']:['#86b6ef','#5598e7','#2a78d6','#1c5cab','#0d366b'];
  const worse=dark?['#8a3a17','#b34d20','#d95926','#ec8a5c','#f6c3a8']:['#eda27c','#e67f4e','#d95926','#a8431a','#6f2b10'];
  const balance=dark?['#6da7ec','#256abf','#4a4a46','#b83a39','#ee8a89']:['#256abf','#86b6ef','#cfcdc7','#f0a09f','#c9302f'];
  const unit=[.2,.4,.6,.8];
  if(['TS','ETS','POD','CC','ACC1','ACC2','PC'].includes(metric))return {breaks:unit,colors:better,note:'越深越好'};
  if(['FAR','MR'].includes(metric))return {breaks:unit,colors:worse,note:'越深越差'};
  if(['RMSE','MAE'].includes(metric))return {breaks:[1,2,3,4],colors:worse,note:'越深越差'};
  if(metric==='BIAS')return {breaks:[.5,.8,1.25,2],colors:balance,note:'灰色接近 1，蓝色偏少，红色偏多'};
  return {breaks:[-2,-.5,.5,2],colors:balance,note:'灰色接近 0，蓝色偏低，红色偏高'};
 }
 function mapView(meta,state,rows,modelLabel,view){
  if(view?.base!=='cmagd')meta={...meta,stations:meta.stations.filter(s=>!s.cmagd_extended)};
  const scores=new Map(rows.map(r=>[r.station,r]));
  const f=mapFrame(meta,view),{west,south,east,north}=f,margin=.02*f.w;
  const boxStations=meta.stations.filter(s=>s.lon>=west-margin&&s.lon<=east+margin&&s.lat>=south-margin&&s.lat<=north+margin);
  const presetMatch=MAP_PRESETS.find(([,p])=>(p.base||null)===f.base&&Math.abs(p.k-f.k)<.01&&(p.k===1||Math.abs(p.cx-f.cx)<1e-6&&Math.abs(p.cy-f.cy)<1e-6));
  const regId=presetMatch?presetMatch[1].region:null,regName=presetMatch?presetMatch[0]:(f.k>1?'当前区域':'南方地区');
  let regStations=boxStations;
  if(regId==='hk'){const hk=boxStations.filter(s=>(String(s.id).startsWith('450')&&s.id!=='45011')||s.id==='VHHH');if(hk.length)regStations=hk;}
  else if(regId==='sz'){const sz=boxStations.filter(s=>String(s.id).startsWith('SZ-')||s.id==='59493'||s.id==='ZGSZ'||(s.sources?.includes('nmic')&&s.lon>=113.7&&s.lon<=114.65&&s.lat>=22.44&&s.lat<=22.88));if(sz.length)regStations=sz;}
  const regIds=new Set(regStations.map(s=>s.id)),regRows=rows.filter(r=>regIds.has(r.station));
  const regScore=poolRegionScore(regRows,state.metric);
  const scale=scoreScale(state.metric),noScore=window.matchMedia?.('(prefers-color-scheme: dark)').matches?'#59636b':'#c9d1d6';
  const quantity=meta.variables?.find(v=>v.id===state.var),measured=quantity?quantity.label+(state.verificationMode==='grade'?` ${meta.variables?.find(v=>v.id===state.var)?.grades?.find(g=>g.id===state.grade)?.label||state.grade}`:state.var.startsWith('rain')&&state.threshold!=null?` ≥${state.threshold} ${quantity.unit}`:quantity.unit?`（${quantity.unit}）`:''):'';
  const color=value=>finite(value)?scale.colors[scale.breaks.filter(b=>value>=b).length]:noScore;
  const x=lon=>48+(lon-west)/(east-west)*574,y=lat=>440-(lat-south)/(north-south)*380;
  const steps=[.05,.1,.2,.25,.5,1,2,3,5],lonStep=steps.find(s=>f.w/s<=8.5)||5,latStep=steps.find(s=>f.h/s<=3.5)||5;
  const deg=(v,step)=>String(+v.toFixed(step<.1?2:step<1?(step===.25?2:1):0));
  let svg=`<svg viewBox="0 0 660 490" role="img" aria-label="中国南方站点评分分布，经度${deg(west,.05)}到${deg(east,.05)}，纬度${deg(south,.05)}到${deg(north,.05)}">`;
  svg+='<rect x="48" y="60" width="574" height="380" class="stations-water"/>';
  // Keep grid, line layers and markers inside the plotting rectangle; the pan group moves while dragging.
  const clipId='stations-map-clip-'+(++mapSequence);
  svg+=`<defs><clipPath id="${clipId}"><rect x="48" y="60" width="574" height="380"/></clipPath></defs>`;
  for(let lon=Math.ceil(west/lonStep-1e-9)*lonStep;lon<=east+1e-9;lon+=lonStep)svg+=`<text x="${x(lon).toFixed(1)}" y="462" text-anchor="middle">${deg(lon,lonStep)}°E</text>`;
  for(let lat=Math.ceil(south/latStep-1e-9)*latStep;lat<=north+1e-9;lat+=latStep)svg+=`<text x="42" y="${(y(lat)+4).toFixed(1)}" text-anchor="end">${deg(lat,latStep)}°N</text>`;
  svg+=`<g clip-path="url(#${clipId})"><g class="stations-pan"><g aria-hidden="true">`;
  for(let lon=Math.ceil(west/lonStep-1e-9)*lonStep;lon<=east+1e-9;lon+=lonStep)svg+=`<path class="stations-grid" d="M${x(lon).toFixed(1)} 60V440"/>`;
  for(let lat=Math.ceil(south/latStep-1e-9)*latStep;lat<=north+1e-9;lat+=latStep)svg+=`<path class="stations-grid" d="M48 ${y(lat).toFixed(1)}H622"/>`;
  // Zoomed into the Pearl River Delta: use the finer bundled lines there.
  const d=meta.detail_bounds,detail=f.k>=4&&d&&west>=d[0]&&south>=d[1]&&east<=d[2]&&north<=d[3];
  for(const [layer,style] of [['province_boundaries','stations-province'],['country_boundaries','stations-border'],['coastline','stations-coast']]){
   for(const line of meta[f.base==='cmagd'?layer+'_cmagd':detail?layer+'_detail':layer]||[])if(line.length>=2)svg+=`<path class="${style}" d="${line.map(([a,b],i)=>`${i?'L':'M'}${x(a).toFixed(1)} ${y(b).toFixed(1)}`).join('')}"/>`;
  }
  for(const line of meta.sz_district_boundaries||[])if(line.length>=2)svg+=`<path class="stations-sz-district" d="${line.map(([a,b],i)=>`${i?'L':'M'}${x(a).toFixed(1)} ${y(b).toFixed(1)}`).join('')}"/>`;
  svg+='</g>';
  for(const station of meta.stations){
   if(station.lon<west-margin||station.lon>east+margin||station.lat<south-margin||station.lat>north+margin)continue;
   const score=scores.get(station.id),value=score?.[state.metric],cx=x(station.lon),cy=y(station.lat),selected=station.id===state.station;
   const high=station.elevation_flags?.includes(state.model),r=selected?7:finite(value)?5:3;
   const title=`${station.id} ${station.name} · ${state.metric} ${metricNum(value,state.metric)} · n=${score?.n||0}${high?' · 海拔差超过300米':''}`;
   svg+=`<g data-station-id="${esc(station.id)}" role="button" tabindex="0" aria-label="${esc(title)}" class="stations-point ${selected?'selected':''}"><title>${esc(title)}</title>`;
   if(high)svg+=`<path d="M${cx} ${cy-r-2}l${r+2} ${2*r+3}h${-2*r-4}Z" fill="${color(value)}"/>`;
   else if(station.type==='szgrid')svg+=`<path d="M${cx} ${cy-r-1}L${cx+r+1} ${cy}L${cx} ${cy+r+1}L${cx-r-1} ${cy}Z" fill="${color(value)}"/>`;
   else if(station.type==='metar')svg+=`<rect x="${cx-r}" y="${cy-r}" width="${r*2}" height="${r*2}" fill="${color(value)}"/>`;
   else svg+=`<circle cx="${cx}" cy="${cy}" r="${r}" fill="${color(value)}"/>`;
   if(f.k>=6||f.base)svg+=`<text class="stations-point-label" x="${(cx+r+3).toFixed(1)}" y="${(cy+4).toFixed(1)}">${esc(station.name)}</text>`;
   svg+='</g>';
  }
  if(f.base==='xj')for(const mark of meta.xj_landmarks||[])svg+=`<g class="stations-landmark"><path d="M${x(mark.lon).toFixed(1)} ${(y(mark.lat)-7).toFixed(1)}l6 11h-12Z"/><text class="stations-point-label" x="${(x(mark.lon)+9).toFixed(1)}" y="${(y(mark.lat)+4).toFixed(1)}">${esc(mark.name)}</text></g>`;
  svg+='</g></g>';
  svg+=`<text x="48" y="30">${esc(modelLabel)} · ${esc(regName)} ${esc(state.metric)}${state.verificationMode==='grade'?` ${meta.variables?.find(v=>v.id===state.var)?.grades?.find(g=>g.id===state.grade)?.label||state.grade}`:state.var.startsWith('rain')&&state.threshold!=null?`（≥${state.threshold} mm）`:''}${finite(regScore)?' '+metricNum(regScore,state.metric):''} · 有配对 ${regRows.length} 站${f.k>1?` · 放大 ${+f.k.toFixed(1)}×`:''}</text></svg>`;
  return `${svg}<div class="stations-map-key"><span>● SYNOP　■ METAR　◆ 深圳各区格点插值　▲ |海拔差| &gt; 300 m</span><span class="stations-bins">${scale.colors.map((c,i)=>`<span><i style="background:${c}"></i>${i?(i<scale.breaks.length?`${metricNum(scale.breaks[i-1],state.metric)}–${metricNum(scale.breaks[i],state.metric)}`:`≥ ${metricNum(scale.breaks[i-1],state.metric)}`):`< ${metricNum(scale.breaks[0],state.metric)}`}</span>`).join('')}</span>${measured?`<span class="stations-measured">${esc(measured)} · ${esc(state.metric)}</span>`:''}<span>${scale.note}</span><span>小灰点：无有效指标</span>${state.var==='wind'&&String(state.model).includes('wn3')?'<span>WN3 风速：11-05 10:00（BJT）起报起为 64 成员风速平均，更早为平均 u、v 合成，偏小 4–20%</span>':''}<span>${f.base==='cmagd'?'CMA-GD 全域：93.64–126.34°E / 16.60–42.88°N':f.base==='xj'?'新疆站点单独评分，不计入南方地区':`站点评分范围：${meta.station_bounds?`${meta.station_bounds[0]}–${meta.station_bounds[2]}°E / ${meta.station_bounds[1]}–${meta.station_bounds[3]}°N`:''}`}</span></div>`;
 }
 function map(view){return mapView(meta,state,chooseRows(data.station_rows).filter(r=>r.model===state.model),label(state.model),view);}
 function niceAxis(rawMin,rawMax,metric){
  const isRain=/降水|mm/.test(metric),nonNeg=isRain||(rawMin>=0&&/风|RMSE|MAE|TS|POD|FAR|MR|BIAS/.test(metric));
  const unitCap=/^(TS|POD|FAR|MR)$/.test(metric)&&rawMax<=1;
  const niceStep=(raw,minStep=0)=>{const t=Math.max(raw,minStep||1e-9),mag=10**Math.floor(Math.log10(t));for(const m of [1,2,5,10])if(m*mag>=t-1e-12)return m*mag;return 10*mag;};
  let lo,hi,step;
  if(nonNeg){
   lo=0;
   if(rawMax<=0){step=isRain?.5:.2;hi=step*4;}
   else{
    let top=rawMax*1.06;if(unitCap)top=Math.min(1,top);
    step=niceStep(top/5,isRain?.1:0);let n=Math.max(3,Math.ceil(top/step-1e-9));
    if(n>6){step=niceStep(top/5,step*1.01);n=Math.max(3,Math.ceil(top/step-1e-9));}
    hi=n*step;if(unitCap&&hi>1){step=.2;hi=1;}
   }
  }else{
   const span=(rawMax-rawMin)||(Math.abs(rawMax)*.1||1),pLo=rawMin-span*.05,pHi=rawMax+span*.05;
   step=niceStep((pHi-pLo)/5);lo=Math.floor(pLo/step+1e-9)*step;hi=Math.ceil(pHi/step-1e-9)*step;
   if(Math.round((hi-lo)/step)>6){step=niceStep((pHi-pLo)/5,step*1.01);lo=Math.floor(pLo/step+1e-9)*step;hi=Math.ceil(pHi/step-1e-9)*step;}
  }
  const n=Math.max(1,Math.round((hi-lo)/step)),dec=Math.min(3,Math.max(0,Math.ceil(-Math.log10(step)-1e-9))),ticks=[];
  for(let i=0;i<=n;i++){const v=lo+step*i;ticks.push(Number((Math.abs(v)<1e-12?0:v).toFixed(dec)));}
  return {lo:ticks[0],hi:ticks[ticks.length-1],ticks,dec};
 }
 let lineSequence=0;
 const LINE_ZOOM_MAX=16,lineViews=new Map();
 function lineFrame(usable,metric,view){
  const fullLo=Math.min(...usable.map(r=>r.x)),fullHi=Math.max(...usable.map(r=>r.x)),span=fullHi-fullLo;
  const k=span>0?Math.min(LINE_ZOOM_MAX,Math.max(1,Number(view?.k)||1)):1,w=span>0?span/k:0;
  const clamp=(v,lo,hi)=>Math.min(hi,Math.max(lo,v));
  const cx=span>0?clamp(Number.isFinite(view?.cx)?view.cx:(fullLo+fullHi)/2,fullLo+w/2,fullHi-w/2):fullLo;
  const xlo=span>0?cx-w/2:fullLo,xhi=span>0?cx+w/2:fullHi,autoY=view?.autoY!==false;
  const vis=autoY&&k>1?usable.filter(r=>r.x>=xlo-1e-6&&r.x<=xhi+1e-6):usable;
  const pool=vis.length?vis:usable;
  const rawMin=Math.min(...pool.map(r=>r.y)),rawMax=Math.max(...pool.map(r=>r.y));
  const axis=niceAxis(rawMin,rawMax,metric);
  return {k,cx,w,xlo,xhi,fullLo,fullHi,autoY,...axis};
 }
 function lineChart(series,metric,xLabel,timeAxis=false,view=null){
  const usable=series.flatMap(s=>s.rows.filter(r=>finite(r.y)&&finite(r.x)));
  if(!usable.length)return '<p class="stations-empty">当前条件暂无有效配对。</p>';
  const f=lineFrame(usable,metric,view),{lo,hi,ticks,dec,xlo,xhi,k}=f;
  const fmtTick=v=>['ACC1','ACC2','PC'].includes(metric)?metricNum(v,metric):v.toLocaleString('zh-CN',{minimumFractionDigits:0,maximumFractionDigits:dec});
  const tipDigits=/降水|mm|°C|hPa|m\/s/.test(metric)?1:3;
  const fmtTip=v=>['ACC1','ACC2','PC'].includes(metric)?metricNum(v,metric):typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:tipDigits}):'—';
  const left=58,right=760,top=32,bottom=265;
  const x=v=>xhi===xlo?(left+right)/2:left+(v-xlo)/(xhi-xlo)*(right-left),y=v=>bottom-(v-lo)/(hi-lo||1)*(bottom-top);
  const clipId='stations-line-clip-'+(++lineSequence);
  let svg=`<svg viewBox="0 0 800 320" role="img" aria-label="${esc(metric)}折线图"><defs><clipPath id="${clipId}"><rect x="${left-4}" y="${top-6}" width="${right-left+8}" height="${bottom-top+12}"/></clipPath></defs><text x="58" y="18">${esc(metric)}${k>1?` · 放大 ${+k.toFixed(1)}×`:""}</text>`;
  for(const v of ticks){svg+=`<path class="stations-grid" d="M58 ${y(v)}H760"/><text x="50" y="${y(v)+4}" text-anchor="end">${fmtTick(v)}</text>`;}
  const xTicks=xhi===xlo?[xlo]:[0,1,2,3,4].map(i=>xlo+(xhi-xlo)*i/4);
  for(const v of xTicks){svg+=`<text x="${x(v)}" y="289" text-anchor="middle">${esc(timeAxis?time(new Date(v)):num(v))}</text>`;}
  svg+=`<g clip-path="url(#${clipId})"><g class="stations-line-pan">`;
  for(const s of series){let path='',previous=null;
   for(const r of s.rows){if(!finite(r.y)){previous=null;continue;}const gap=previous!==null&&timeAxis&&r.x-previous>12*3600000;path+=`${previous===null||gap?'M':'L'}${x(r.x)} ${y(r.y)} `;previous=r.x;}
   svg+=`<path d="${path}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
   for(const r of s.rows.filter(r=>finite(r.y)&&r.x>=xlo-1e-6&&r.x<=xhi+1e-6))svg+=`<circle cx="${x(r.x)}" cy="${y(r.y)}" r="2.8" fill="${s.color}"><title>${esc(s.label)} · ${esc(timeAxis?time(new Date(r.x)):'+'+r.x+'h')} · ${fmtTip(r.y)}${r.n?' · n='+r.n:''}</title></circle>`;
  }
  svg+=`</g><g class="series-hover-layer" pointer-events="none"></g></g>`;
  return svg+`<text x="410" y="314" text-anchor="middle">${esc(xLabel)}</text></svg>`;
 }
 function mountLineChart(host,key,series,metric,xLabel,timeAxis=false){
  if(!host)return;
  const usable=series.flatMap(s=>s.rows.filter(r=>finite(r.y)&&finite(r.x)));
  if(!usable.length){host.innerHTML='<p class="stations-empty">当前条件暂无有效配对。</p>';return;}
  let view=lineViews.get(key)||{k:1,autoY:true};
  host.innerHTML=`<div class="stations-zoom-bar series-zoom-bar" role="group" aria-label="折线图缩放"><button type="button" data-line-zoom="in" aria-label="放大">＋</button><button type="button" data-line-zoom="out" aria-label="缩小">－</button><button type="button" data-line-zoom="reset">全时段</button><button type="button" data-line-autoy="1" aria-pressed="${view.autoY!==false}" title="放大时间段时自动适配纵轴范围">Y轴自适应</button><span class="stations-zoom-hint">滚轮缩放 · 拖动平移 · 悬停查看数值</span></div><div class="stations-zoom-canvas stations-line-canvas"></div><div class="chart-value-tooltip stations-line-tooltip" hidden></div>`;
  const canvas=host.querySelector('.stations-line-canvas'),tip=host.querySelector('.stations-line-tooltip');
  const left=58,right=760,top=32,bottom=265,plotW=right-left,plotH=bottom-top;
  const tipDigits=/降水|mm|°C|hPa|m\/s/.test(metric)?1:3;
  const unitPart=metric.includes(' / ')?' '+metric.split(' / ').pop().trim():'';
  const fmtTip=v=>['ACC1','ACC2','PC'].includes(metric)?metricNum(v,metric):typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{minimumFractionDigits:tipDigits===1?1:0,maximumFractionDigits:tipDigits})+unitPart:'—';
  let dragged=false,frame=0,pinnedX=null,currentFrame=null;
  const hideTip=()=>{if(tip)tip.hidden=true;const layer=canvas.querySelector('.series-hover-layer');if(layer)layer.innerHTML='';};
  const showAt=(targetX,clientX,clientY)=>{
   const f=currentFrame;if(!f)return;
   const xs=[...new Set(usable.filter(r=>r.x>=f.xlo-1e-6&&r.x<=f.xhi+1e-6).map(r=>r.x))];
   if(!xs.length){hideTip();return;}
   const snapX=xs.reduce((best,v)=>Math.abs(v-targetX)<Math.abs(best-targetX)?v:best,xs[0]);
   const xPx=f.xhi===f.xlo?(left+right)/2:left+(snapX-f.xlo)/(f.xhi-f.xlo)*plotW;
   const yPx=v=>bottom-(v-f.lo)/(f.hi-f.lo||1)*plotH;
   const layer=canvas.querySelector('.series-hover-layer');
   if(layer){
    let overlay=`<line x1="${xPx.toFixed(1)}" y1="${top}" x2="${xPx.toFixed(1)}" y2="${bottom}" stroke="#526f82" stroke-width="1.2" stroke-dasharray="4 3"/>`;
    for(const s of series){
     const pt=s.rows.find(r=>r.x===snapX&&finite(r.y));
     if(pt)overlay+=`<circle cx="${xPx.toFixed(1)}" cy="${yPx(pt.y).toFixed(1)}" r="5" fill="${s.color}" stroke="#fff" stroke-width="2"/>`;
    }
    layer.innerHTML=overlay;
   }
   if(tip){
    const title=timeAxis?time(new Date(snapX))+' BJT':'+'+num(snapX)+' 小时';
    const items=series.map(s=>{
     const pt=s.rows.find(r=>r.x===snapX&&finite(r.y));
     if(!pt)return null;
     return `<div class="series-tip-row"><span class="series-tip-name"><i style="background:${s.color}"></i>${esc(s.label)}</span><strong>${esc(fmtTip(pt.y))}${pt.n?`<small> (n=${pt.n})</small>`:""}</strong></div>`;
    }).filter(Boolean);
    if(!items.length){hideTip();return;}
    tip.innerHTML=`<div class="series-tip-head">${esc(title)}</div>${items.join('')}`;
    tip.hidden=false;
    const rect=canvas.getBoundingClientRect();
    const cx=clientX??(rect.left+xPx/800*rect.width),cy=clientY??(rect.top+rect.height*0.35);
    const tw=tip.offsetWidth||220,th=tip.offsetHeight||120;
    const leftPos=cx+16+tw>window.innerWidth-10?Math.max(10,cx-tw-16):cx+16;
    const topPos=Math.max(10,Math.min(window.innerHeight-th-10,cy-th/2));
    tip.style.left=leftPos+'px';tip.style.top=topPos+'px';
   }
  };
  const paint=()=>{
   const f=lineFrame(usable,metric,view);currentFrame=f;
   view={k:f.k,cx:f.cx,autoY:f.autoY};lineViews.set(key,view);
   canvas.innerHTML=lineChart(series,metric,xLabel,timeAxis,view);
   const svg=canvas.querySelector('svg');if(svg)svg.style.touchAction=view.k>1?'none':'';
   host.querySelector('[data-line-zoom="out"]').disabled=view.k<=1;
   host.querySelector('[data-line-zoom="in"]').disabled=view.k>=LINE_ZOOM_MAX;
   host.querySelector('[data-line-zoom="reset"]').setAttribute('aria-pressed',view.k<=1);
   const autoBtn=host.querySelector('[data-line-autoy]');if(autoBtn)autoBtn.setAttribute('aria-pressed',view.autoY!==false);
   if(pinnedX!==null)showAt(pinnedX);else hideTip();
  };
  const zoomAt=(factor,sx=409)=>{
   const f=lineFrame(usable,metric,view);if(f.fullHi===f.fullLo)return;
   const k=Math.min(LINE_ZOOM_MAX,Math.max(1,f.k*factor));if(k===f.k)return;
   const ratio=Math.min(1,Math.max(0,(sx-left)/plotW));
   const anchor=f.xlo+ratio*f.w,nextW=(f.fullHi-f.fullLo)/k;
   view={...view,k,cx:anchor-ratio*nextW+nextW/2};paint();
  };
  const svgPoint=(svg,event)=>{const r=svg.getBoundingClientRect();return r.width?[(event.clientX-r.left)*800/r.width,(event.clientY-r.top)*320/r.height,800/r.width]:[409,150,1];};
  host.querySelector('[data-line-zoom="in"]').onclick=()=>zoomAt(1.5);
  host.querySelector('[data-line-zoom="out"]').onclick=()=>zoomAt(1/1.5);
  host.querySelector('[data-line-zoom="reset"]').onclick=()=>{view={...view,k:1,cx:(currentFrame.fullLo+currentFrame.fullHi)/2};pinnedX=null;paint();};
  host.querySelector('[data-line-autoy]').onclick=()=>{view={...view,autoY:view.autoY===false};paint();};
  canvas.addEventListener('wheel',event=>{
   const svg=canvas.querySelector('svg');if(!svg)return;event.preventDefault();
   const [sx]=svgPoint(svg,event),factor=event.deltaY<0?1.25:1/1.25;
   cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>zoomAt(factor,sx));
  },{passive:false});
  canvas.addEventListener('pointerdown',event=>{
   const svg=canvas.querySelector('svg');if(!svg||event.button!==0||view.k<=1)return;
   const [x0,,scale]=svgPoint(svg,event),pan=svg.querySelector('.stations-line-pan');let dx=0;dragged=false;
   const move=e=>{dx=(e.clientX-event.clientX)*scale;if(Math.abs(dx)>4)dragged=true;if(dragged&&pan){hideTip();pan.setAttribute('transform',`translate(${dx.toFixed(1)} 0)`);}};
   const up=()=>{
    window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);
    if(!dragged)return;const f=lineFrame(usable,metric,view);view={...view,k:f.k,cx:f.cx-dx/plotW*f.w};paint();
   };
   window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up);
  });
  canvas.addEventListener('pointermove',event=>{
   if(dragged||pinnedX!==null)return;
   const svg=canvas.querySelector('svg');if(!svg||!currentFrame)return;
   const [sx,sy]=svgPoint(svg,event);
   if(sx<left-12||sx>right+12||sy<top-12||sy>bottom+20){hideTip();return;}
   const ratio=Math.min(1,Math.max(0,(sx-left)/plotW));
   showAt(currentFrame.xlo+ratio*(currentFrame.xhi-currentFrame.xlo),event.clientX,event.clientY);
  });
  canvas.addEventListener('pointerleave',()=>{if(pinnedX===null)hideTip();});
  canvas.addEventListener('click',event=>{
   if(dragged){dragged=false;event.stopPropagation();event.preventDefault();return;}
   const svg=canvas.querySelector('svg');if(!svg||!currentFrame)return;
   const [sx,sy]=svgPoint(svg,event);
   if(sx<left-12||sx>right+12||sy<top-12||sy>bottom+20){pinnedX=null;hideTip();return;}
   if(pinnedX!==null){pinnedX=null;hideTip();return;}
   const ratio=Math.min(1,Math.max(0,(sx-left)/plotW));
   const targetX=currentFrame.xlo+ratio*(currentFrame.xhi-currentFrame.xlo);
   const xs=[...new Set(usable.filter(r=>r.x>=currentFrame.xlo-1e-6&&r.x<=currentFrame.xhi+1e-6).map(r=>r.x))];
   if(xs.length){pinnedX=xs.reduce((best,v)=>Math.abs(v-targetX)<Math.abs(best-targetX)?v:best,xs[0]);showAt(pinnedX,event.clientX,event.clientY);}
  },true);
  canvas.addEventListener('dblclick',()=>{if(view.k>1){view={...view,k:1};pinnedX=null;paint();}});
  paint();
 }
 function render(){
  if(!data)return;
  if(window.VerificationCore&&['ACC1','ACC2','PC'].includes(state.metric))by('stations-controls').querySelector('.filter-note').textContent=window.VerificationCore.guifanHelp[state.metric]+' '+window.VerificationCore.metricCoverage(chooseRows(data.rows),state.metric);
  const rows=chooseRows(data.rows),leads=[...new Set(rows.map(r=>r.lead))].sort((a,b)=>a-b);
  const series=state.models.map(model=>({label:label(model),color:colors[model],rows:rows.filter(r=>r.model===model).sort((a,b)=>a.lead-b.lead).map(r=>({x:r.lead,y:r[state.metric],n:r.n}))}));
  const index=new Map(rows.map(r=>[r.model+':'+r.lead,r]));
  by('stations-results').innerHTML=`<div class="stations-panels"><section class="results stations-map-card"><div class="result-heading"><h2>站点分布</h2>${modelSelect('stations-map-model')}</div><div id="stations-map"></div></section><section class="results stations-lead-card"><div class="result-heading"><div><h2>模式 × 时效</h2><p>${esc(variable()?.label||'')} · ${esc(state.metric)}${rain()?(state.verificationMode==='grade'?' · '+variable()?.grades?.find(g=>g.id===state.grade)?.label:' · ≥'+state.threshold+' mm'):''}</p></div></div><div class="legend">${state.models.map(m=>`<span><i style="background:${colors[m]}"></i>${esc(label(m))}</span>`).join('')}</div>${lineChart(series,state.metric,'预报时效 / 小时')}<p class="stations-note">${state.days<=3?`${esc(dayLabels[state.days])}样本较少，分数波动较大，请结合 n 判断。`:''}每个点汇总全部有效站点与起报，未对时效平均分数。缺测与无有效分母显示“—”。</p></section></div><section class="table-card"><div class="table-heading"><h2>逐时效评分明细</h2><span>${esc(state.metric)} / n</span></div><div class="table-scroll stations-score-table"><table><thead><tr><th>时效</th>${state.models.map(m=>`<th>${esc(label(m))}</th>`).join('')}</tr></thead><tbody>${leads.map(lead=>`<tr><th>+${lead} h</th>${state.models.map(m=>{const r=index.get(m+':'+lead);return `<td>${metricNum(r?.[state.metric],state.metric)}<small> / ${r?.n||0}</small></td>`;}).join('')}</tr>`).join('')}</tbody></table></div></section>`;
  by('stations-map-model').onchange=e=>{state.model=e.target.value;render();loadDetail();};
  mountMap(by('stations-map'),'stations',meta,view=>map(view),canvas=>canvas.querySelectorAll('[data-station-id]').forEach(point=>{
   const select=()=>{state.station=point.dataset.stationId;render();loadDetail();};point.onclick=select;point.onkeydown=e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();select();}};
  }));
 }
 function detailControls(){
  return `<div class="result-heading"><div><h2>单站预报与实况</h2><p>汇总本站全部可用时效，比较各有效时刻的预报均值与实况 · 北京时间</p></div></div><div class="stations-filters"><label>站点<select id="stations-id">${options(meta.stations.map(s=>[s.id,s.id+' '+s.name]),state.station)}</select></label>${modelSelect('stations-detail-model')}</div><div id="stations-detail-body" aria-live="polite"></div>`;
 }
 async function loadDetail(){
  detailController?.abort();detailController=new AbortController();const signal=detailController.signal;
  let detailLeads=null;
  try{detailLeads=(await api('station-leads?'+new URLSearchParams({id:state.station,var:state.var,model:state.model}),signal)).leads;}catch(error){if(signal.aborted)return;detailLeads=null;}
  if(detailLeads&&!detailLeads.length)detailLeads=null;
  const available=detailLeads||meta.models.find(m=>m.id===state.model)?.leads||[24];
  if(signal.aborted||!opened)return;
  by('stations-detail').innerHTML=detailControls();
  by('stations-id').onchange=e=>{state.station=e.target.value;render();loadDetail();};
  by('stations-detail-model').onchange=e=>{state.model=e.target.value;render();loadDetail();};
  by('stations-detail-body').textContent='读取单站配对…';
  try{
   const detail=await api('station?'+new URLSearchParams({id:state.station,var:state.var,model:state.model,leads:available.join(','),days:state.days}),signal);
   if(signal.aborted||!opened)return;
   const stat=chooseRows(detail.stats)[0],s=detail.station,z=s.model_elevations?.[state.model];
   const keys=rain()?['n','H','M','F','TS','POD','FAR','MR','BIAS','ETS',...(state.verificationMode==='grade'?[]:(variable()?.has_trace?['PC']:[]))]:['n','ME','MAE','RMSE','CC',...(variable()?.has_acc?['ACC2','ACC1']:[])];
   by('stations-detail-body').innerHTML=`<p class="stations-note">${esc(s.name)} · 全部可用时效 · ${s.lon.toFixed(3)}°E / ${s.lat.toFixed(3)}°N · 站高 ${num(s.elevation)} m · 模式格点海拔 ${num(z)} m${s.elevation_flags?.includes(state.model)?' · ▲ 海拔差超过300 m':''} · 未作海拔订正</p><div class="stations-stats">${keys.map(k=>`<span>${metricLabel(k)}<strong>${metricNum(stat?.[k],k)}</strong></span>`).join('')}</div><div class="legend"><span><i style="background:${colors[state.model]}"></i>${esc(label(state.model))}</span><span><i style="background:#536575"></i>站点实况</span></div><div id="stations-detail-chart"></div><p class="stations-note">${detail.series.length} 个有效时次（总配对 ${detail.pair_count??detail.series.length}） · 实况来源：${[...new Set(detail.series.map(r=>sources[r.source]||r.source))].join('、')||'暂无'}。METAR取模型有效时刻±10分钟内最近报告；SYNOP按UTC三小时时次。未评分的观测保留质控标记。</p>`;
    mountLineChart(by('stations-detail-chart'),'stations-detail',[{label:label(state.model),color:colors[state.model],rows:detail.series.map(r=>({x:Date.parse(r.valid),y:r.fc,n:r.n}))},{label:'站点实况',color:'#536575',rows:detail.series.map(r=>({x:Date.parse(r.valid),y:r.ob}))}],variable()?.label+' / '+variable()?.unit,'有效时间 / 北京时间',true);
  }catch(error){if(error.name!=='AbortError'&&!signal.aborted)by('stations-detail-body').textContent=error.message;}
 }
 async function loadScores(){
  controller?.abort();controller=new AbortController();const signal=controller.signal,current=++seq;data=null;
  by('stations-results').textContent='读取已发布评分…';by('stations-detail').textContent='读取当前条件…';detailController?.abort();
  try{
   const response=await api('scores?'+new URLSearchParams({var:state.var,models:state.models.join(','),days:state.days,range:'0-240'}),signal);
   if(current!==seq||!opened)return;data=response;
   // The default/selected station may have no pairs for this model/variable: jump to the best-sampled one.
   const have=(data.station_rows||[]).filter(r=>r.model===state.model&&r.n>0);
   if(have.length&&!have.some(r=>r.station===state.station))state.station=have.reduce((m,r)=>r.n>m.n?r:m).station;
   render();loadDetail();
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
   if(!windows().includes(state.days))state.days=windows().includes(15)?15:windows().at(-1);
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
 // Zoomable map host: draw(view) returns mapView HTML, bind(canvas) wires station clicks.
 // Wheel zooms about the pointer, drag pans (no station click after a drag), buttons zoom/preset.
 // The view is remembered per key, so model/station changes keep the zoom.
 const mapViews=new Map();
 const MAP_PRESETS=[['南方地区',{k:1,region:'all'}],['CMA-GD 全域',{k:1,base:'cmagd',region:'cmagd'}],['新疆巴里坤',{k:1,base:'xj',region:'xj'}],['大湾区',{k:5,cx:113.6,cy:22.55,region:'gba'}],['香港',{k:20,cx:114.15,cy:22.37,region:'hk'}],['深圳',{k:18,cx:114.13,cy:22.65,region:'sz'}]];
 function mountMap(host,key,meta,draw,bind,onPreset,region='all'){
  key+=':'+region;
  let view=mapViews.get(key)||{...(MAP_PRESETS.find(([,p])=>p.region===region)?.[1]||{k:1})};
  host.innerHTML=`<div class="stations-zoom-bar" role="group" aria-label="地图缩放"><button type="button" data-zoom="in" aria-label="放大">＋</button><button type="button" data-zoom="out" aria-label="缩小">－</button>${MAP_PRESETS.map(([name],i)=>`<button type="button" data-zoom-preset="${i}">${name}</button>`).join('')}<span class="stations-zoom-hint">滚轮缩放 · 拖动平移</span></div><div class="stations-zoom-canvas"></div>`;
  const canvas=host.querySelector('.stations-zoom-canvas');let dragged=false,frame=0;
  const paint=()=>{
   const f=mapFrame(meta,view);view={k:f.k,cx:f.cx,cy:f.cy,base:f.base};mapViews.set(key,view);
   canvas.innerHTML=draw(view);bind(canvas);
   const svg=canvas.querySelector('svg');if(svg)svg.style.touchAction=view.k>1?'none':'';
   host.querySelector('[data-zoom="out"]').disabled=view.k<=1;host.querySelector('[data-zoom="in"]').disabled=view.k>=MAP_ZOOM_MAX;
   host.querySelectorAll('[data-zoom-preset]').forEach(b=>{const p=MAP_PRESETS[b.dataset.zoomPreset][1];b.setAttribute('aria-pressed',(p.base||null)===(view.base||null)&&Math.abs(p.k-view.k)<.01&&(p.k===1||Math.abs(p.cx-view.cx)<1e-6&&Math.abs(p.cy-view.cy)<1e-6));});
  };
  const zoomAt=(factor,sx=335,sy=250)=>{
   const f=mapFrame(meta,view),k=Math.min(MAP_ZOOM_MAX,Math.max(1,f.k*factor));if(k===f.k)return;
   const lon=f.west+(sx-48)/574*f.w,lat=f.south+(440-sy)/380*f.h,w=f.w*f.k/k,h=f.h*f.k/k;
   view={k,cx:lon-(sx-48)/574*w+w/2,cy:lat-(440-sy)/380*h+h/2,base:f.base};paint();
  };
  const svgPoint=(svg,event)=>{const r=svg.getBoundingClientRect();return r.width?[(event.clientX-r.left)*660/r.width,(event.clientY-r.top)*490/r.height,660/r.width]:[335,250,1];};
  host.querySelector('[data-zoom="in"]').onclick=()=>zoomAt(1.6);
  host.querySelector('[data-zoom="out"]').onclick=()=>zoomAt(1/1.6);
  host.querySelectorAll('[data-zoom-preset]').forEach(b=>b.onclick=()=>{const [name,preset]=MAP_PRESETS[b.dataset.zoomPreset];view={...preset};paint();if(onPreset)onPreset(preset.region||'all',name,view);});
  canvas.addEventListener('wheel',event=>{
   const svg=canvas.querySelector('svg');if(!svg)return;event.preventDefault();
   const [sx,sy]=svgPoint(svg,event),factor=event.deltaY<0?1.25:1/1.25;
   cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>zoomAt(factor,sx,sy));
  },{passive:false});
  canvas.addEventListener('pointerdown',event=>{
   const svg=canvas.querySelector('svg');if(!svg||event.button!==0||view.k<=1)return;
   const [x0,y0,scale]=svgPoint(svg,event),pan=svg.querySelector('.stations-pan');let dx=0,dy=0;dragged=false;
   const move=e=>{dx=(e.clientX-event.clientX)*scale;dy=(e.clientY-event.clientY)*scale;if(Math.hypot(dx,dy)>4)dragged=true;if(dragged&&pan)pan.setAttribute('transform',`translate(${dx.toFixed(1)} ${dy.toFixed(1)})`);};
   const up=()=>{
    window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);
    if(!dragged)return;const f=mapFrame(meta,view);view={k:f.k,cx:f.cx-dx/574*f.w,cy:f.cy+dy/380*f.h,base:f.base};paint();
   };
   window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up);
  });
  // A drag must not also select the station under the pointer.
  canvas.addEventListener('click',event=>{if(dragged){dragged=false;event.stopPropagation();event.preventDefault();}},true);
  paint();
 }
 window.StationVisuals={map:mapView,mountMap,lineChart,mountLineChart,api,colors,sources,num,esc};
 return {open,close,refresh};
})();
