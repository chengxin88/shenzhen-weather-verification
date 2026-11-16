'use strict';
// 数据质控：一屏状态板。每个资料源一行（最新一批、状态和原因、近 7 天），细节点开再看。
window.DataQuality=(()=>{
 const API='/Verification/test/api/quality-control';
 const root=()=>document.getElementById('quality-workspace');
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const finite=value=>typeof value==='number'&&Number.isFinite(value);
 const count=value=>finite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:0}):'—';
 const number=(value,digits=2)=>finite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:digits}):'—';
 const unitLabel=value=>value==='degC'?'°C':value||'未确认';
 const list=value=>Array.isArray(value)?value:[];
 const BJT=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
 const TONE_ORDER={bad:0,warn:1,good:2,neutral:3};
 let opened=false,response=null,error='',requesting=false,controller=null,timer=null,sequence=0,pollStarted=0,autoStarted=false;
 function time(value,withYear=false){
  if(!value)return '—';
  const parsed=new Date(value);
  if(!Number.isFinite(parsed.getTime()))return String(value);
  const p=Object.fromEntries(BJT.formatToParts(parsed).map(part=>[part.type,part.value]));
  return `${withYear?p.year+'-':''}${p.month}-${p.day} ${p.hour}:${p.minute}`;
 }
 function ago(hours){
  if(!finite(hours))return '';
  return hours<1?`${number(hours*60,0)} 分钟前`:hours<48?`${number(hours,1)} 小时前`:`${number(hours/24,1)} 天前`;
 }
 function statusClass(value){
  if(['ok','healthy','complete','on_time','available','normal','ready'].includes(value))return 'good';
  if(['missing','error','failed','late','delayed','stale','invalid','unavailable'].includes(value))return 'bad';
  if(['partial','incomplete','preparing','pending','backlog','warning'].includes(value))return 'warn';
  return 'neutral';
 }
 function severityLabel(value){return ({error:'异常',warning:'需核实',info:'提示'})[value]||'提示';}
 function sourceKind(source){
  if(source.is_demo||source.kind==='demo'||source.arrival?.status==='demo')return 'demo';
  if(source.kind==='reserved'||source.arrival?.status==='reserved')return 'reserved';
  return source.kind||'native';
 }
 const monitored=source=>!['reserved','demo'].includes(sourceKind(source));
 const isObs=source=>source.id==='observations';
 const historyOf=source=>response?.history?.sources?.[source.id]||null;
 function badge(label,kind='neutral'){return `<span class="qc-tag ${esc(kind)}">${esc(label)}</span>`;}
 // 一行的颜色：到报状态和检查问题里取最严重的；未接入的资料源不参与。
 function tone(source){
  if(!monitored(source))return 'neutral';
  const arrival=statusClass(source.arrival?.status),issues=list(source.issues);
  if(arrival==='bad'||issues.some(issue=>issue.severity==='error'))return 'bad';
  if(arrival!=='good'||issues.some(issue=>issue.severity==='warning'))return 'warn';
  return 'good';
 }
 function stateLabel(source,kind){
  const arrival=source.arrival||{};
  if(kind==='neutral')return arrival.label||'未接入';
  if(kind==='good')return arrival.label||'正常';
  if(statusClass(arrival.status)==='good')return kind==='bad'?'有异常':'需关注';
  return arrival.label||'状态未确认';
 }
 function frameUnit(source){
  const unit=source.arrival?.frame_unit||source.frame_unit;
  return unit==='字段文件'?'份要素数据':unit==='图层文件'?'份图层':unit||'个预报时间';
 }
 // 状态后面那一句话：到了多少、缺什么、最要紧的问题是什么。
 function reason(source){
  const a=source.arrival||{},out=[];
  if(!monitored(source))return a.expected_label||'';
  if(finite(a.available_frames)){
   if(finite(a.expected_frames)&&a.expected_frames>0)out.push(isObs(source)?`最近 ${count(a.expected_frames)} 小时已入库 ${count(a.available_frames)} 小时`:`${count(a.available_frames)}/${count(a.expected_frames)} ${frameUnit(source)}${a.available_frames>=a.expected_frames?'，已到齐':''}`);
   else out.push(`已下载 ${count(a.available_frames)} ${frameUnit(source)}`);
  }
  // “缺失时效”这条问题只是重复缺哪些时效，不单列。
  const serious=list(source.issues).filter(issue=>issue.code!=='missing_leads'&&issue.severity!=='info').sort((a,b)=>(a.severity==='error'?0:1)-(b.severity==='error'?0:1));
  const explained=serious.slice(0,2).map(issue=>String(issue.message||'')).join(' ');
  const leads=list(a.missing_leads);
  // 缺的时效如果已在后面的问题说明里逐个写出原因，这里就不重复。
  if(leads.length&&!leads.every(lead=>new RegExp(`\\+${lead}h(?!\\d)`).test(explained)))out.push(`缺 ${leads.slice(0,6).map(lead=>finite(lead)?`+${number(lead,2)}h`:String(lead)).join('、')}${leads.length>6?` 等 ${count(leads.length)} 个时效`:''}`);
  const hours=list(a.missing_valid_bjt);
  if(hours.length){
   const waiting=finite(a.pending_recent_hours)?Math.min(a.pending_recent_hours,hours.length):0;
   out.push(waiting>=hours.length?`${count(hours.length)} 个小时还在正常等待时间内`:`${count(hours.length)} 个小时没入库${waiting?`（其中 ${count(waiting)} 个还在正常等待时间内）`:''}`);
  }
  serious.slice(0,2).forEach(issue=>out.push(String(issue.message||'').replace(/[。.]$/,'')));
  if(serious.length>2)out.push(`另有 ${count(serious.length-2)} 条，点开查看`);
  return out.join('；');
 }
 function latest(source){
  const a=source.arrival||{},value=a.latest_run_bjt||a.latest_run_utc;
  if(!monitored(source)||!value)return '<span class="qc-muted">—</span>';
  return `<span>${esc(time(value))} ${isObs(source)?'观测':'起报'}</span><small>${esc(ago(a.age_hours))}</small>`;
 }
 function week(source){
  if(!monitored(source))return '';
  const days=list(historyOf(source)?.days),obs=isObs(source),batch=obs?'个时次':'批';
  if(!days.some(day=>day.runs>0))return '<span class="qc-muted qc-week-empty">暂无记录</span>';
  const missed=day=>Math.max(0,day.runs-day.complete-day.pending);
  const cells=days.map(day=>{
   const kind=!day.runs?'none':missed(day)?'miss':day.complete?'full':'wait';
   const label=`${String(day.date).slice(5)}：`+(!day.runs?'没有记录':`记录到 ${count(day.runs)} ${batch}，到齐 ${count(day.complete)} ${batch}`+(missed(day)?`，${count(missed(day))} ${batch}没到齐`:'')+(day.pending?'，最新的还在下载':'')+(finite(day.median_lag_hours)?`，一般${obs?'观测':'起报'}后 ${number(day.median_lag_hours,1)} 小时到齐`:''));
   return `<i class="qc-day ${kind}" title="${esc(label)}"></i>`;
  }).join('');
  const logged=days.filter(day=>day.runs>0).length,short=days.filter(day=>missed(day)).length;
  return `<span class="qc-week" role="img" aria-label="近 7 天：${logged} 天有记录，${short?short+' 天有没到齐的'+batch:'没有发现没到齐的'}">${cells}</span>`;
 }
 function backlogText(arrival){
  if(typeof arrival.backlog==='string')return arrival.backlog;
  if(finite(arrival.backlog))return arrival.backlog>0?`${count(arrival.backlog)} 批待补齐`:'';
  if(arrival.backlog&&typeof arrival.backlog==='object'){
   if(arrival.backlog.label)return String(arrival.backlog.label);
   if(finite(arrival.backlog.pending_runs))return `${count(arrival.backlog.pending_runs)} 批待处理`;
   if(finite(arrival.backlog.missing_runs))return `${count(arrival.backlog.missing_runs)} 批未到报`;
  }
  return '';
 }
 function arrived(run){
  const done=run.complete_bjt||run.complete_utc;
  if(done)return run.baseline?'开始记录前已到齐':esc(time(done));
  return run.latest?'还在下载':'<span class="qc-short">没到齐</span>';
 }
 // 点开一行后看到的内容：问题清单、最近几批的到报记录、这一行是怎么检查的。
 function rowBody(source){
  const a=source.arrival||{},issues=list(source.issues),recent=list(historyOf(source)?.recent),obs=isObs(source);
  if(!monitored(source))return `<div class="qc-row-body"><p class="qc-muted">${esc(a.expected_label||'这个资料源还没有接入，不检查到报。')}</p></div>`;
  const problems=issues.length?`<ul class="qc-issues">${issues.map(issue=>{
   const where=[issue.run_bjt||issue.run_utc?`${time(issue.run_bjt||issue.run_utc)} ${obs?'观测':'起报'}`:'',finite(issue.lead_hours)?`+${number(issue.lead_hours)} 小时`:'',issue.field||''].filter(Boolean).join(' · ');
   return `<li>${badge(severityLabel(issue.severity),issue.severity==='error'?'bad':issue.severity==='warning'?'warn':'neutral')}<span>${esc(issue.message||'未提供问题说明')}${where?`<small>${esc(where)}</small>`:''}</span></li>`;
  }).join('')}</ul>`:'<p class="qc-muted">本次检查没有发现问题。</p>';
  const runs=recent.length?`<div class="table-scroll"><table class="qc-table"><thead><tr><th scope="col">${obs?'观测时间':'起报时间'}</th><th scope="col">到齐时间</th><th scope="col">${obs?'观测后多久到齐':'起报后多久到齐'}</th><th scope="col">到了多少</th></tr></thead><tbody>${recent.map(run=>`<tr><th scope="row">${esc(time(run.run_bjt||run.run_utc))}</th><td>${arrived(run)}</td><td>${finite(run.lag_hours)?`${number(run.lag_hours,1)} 小时`:'—'}</td><td>${finite(run.available)?`${count(run.available)}${finite(run.expected)?' / '+count(run.expected):''}`:'—'}</td></tr>`).join('')}</tbody></table></div>`:'<p class="qc-muted">到报记录从本次改版后开始积累，暂时还没有。</p>';
  const how=[a.expected_label,finite(a.warning_age_hours)?`最新${obs?'观测':'起报'}时间超过 ${number(a.warning_age_hours)} 小时没有更新就提醒。`:'',backlogText(a),source.checks?.coverage_label].filter(Boolean);
  return `<div class="qc-row-body"><div class="qc-block"><h3>发现的问题</h3>${problems}</div><div class="qc-block"><h3>最近几${obs?'个时次':'批'}</h3>${runs}</div>${how.length?`<div class="qc-block qc-how"><h3>怎么检查的</h3><ul>${how.map(text=>`<li>${esc(text)}</li>`).join('')}</ul></div>`:''}</div>`;
 }
 function title(){
  const snapshot=response?.snapshot,running=response?.state==='running';
  return `<div class="page-heading qc-heading"><div><p class="eyebrow">DATA QUALITY</p><h1>数据质控</h1><p>各路资料到了没有、齐不齐、数值有没有明显不合理。时间均为北京时间。</p></div><div class="qc-heading-actions"><span class="coverage-label">${snapshot?`检查于 ${esc(time(snapshot.generated_bjt||snapshot.generated_utc))} · 每 5 分钟自动检查`:'尚无检查结果'}</span><button type="button" class="quiet" id="quality-refresh" ${requesting?'disabled':''}>${requesting?'读取中…':running?'检查中 · 查看进度':'↻ 重新检查'}</button></div></div>`;
 }
 function scanNotice(){
  const state=response?.state;
  if(error)return `<div class="notice error" role="alert">${esc(error)}${response?.snapshot?' 下方保留上次完成的结果。':''}</div>`;
  if(state==='running')return `<div class="notice qc-live" role="status"><span class="qc-live-dot" aria-hidden="true"></span>正在检查${response?.job?.started_bjt?`，开始于 ${esc(time(response.job.started_bjt))}`:''}。${response?.snapshot?'下方是上次的结果，检查完会自动更新。':'完成后显示各资料源状态。'}${pollStarted&&Date.now()-pollStarted>=180000?' 检查仍在进行，可稍后点击“查看进度”。':''}</div>`;
  if(state==='failed')return '<div class="notice error" role="alert">本次检查没有完成，下方是上次的结果，不能据此判断当前资料正常。请重新检查。</div>';
  if(requesting&&!response?.snapshot)return '<div class="notice" role="status">正在读取检查结果…</div>';
  return '';
 }
 function verdict(sources){
  const real=sources.filter(monitored),tones=real.map(tone);
  const bad=tones.filter(kind=>kind==='bad').length,warn=tones.filter(kind=>kind==='warn').length,good=tones.filter(kind=>kind==='good').length,off=sources.length-real.length;
  const kind=bad?'bad':warn?'warn':'good';
  const head=bad+warn?`${count(real.length)} 个资料源中有 ${count(bad+warn)} 个需要关注`:`${count(real.length)} 个资料源全部正常`;
  const tail=[bad?`${count(bad)} 个异常`:'',bad&&warn?`${count(warn)} 个需关注`:'',bad+warn?`${count(good)} 个正常`:'',off?`另有 ${count(off)} 个未接入`:''].filter(Boolean).join(' · ');
  return `<div class="qc-verdict ${kind}" role="status"><span class="qc-dot" aria-hidden="true"></span><strong>${head}</strong>${tail?`<span>${tail}</span>`:''}</div>`;
 }
 function board(sources){
  const rows=sources.map((source,index)=>({source,index,kind:tone(source)})).sort((a,b)=>TONE_ORDER[a.kind]-TONE_ORDER[b.kind]||a.index-b.index);
  const since=response?.history?.since_bjt||response?.history?.since_utc;
  return `<section class="table-card qc-board" aria-label="各资料源状态"><div class="qc-board-head" aria-hidden="true"><span>资料源</span><span>最新一批</span><span>状态</span><span>近 7 天</span></div>${rows.map(({source,kind})=>`<details class="qc-row ${kind}" data-key="source:${esc(source.id)}"><summary id="quality-row-${esc(source.id)}"><span class="qc-name">${esc(source.name||source.id)}</span><span class="qc-latest">${latest(source)}</span><span class="qc-state">${badge(stateLabel(source,kind),kind)}<span class="qc-reason">${esc(reason(source))}</span></span><span class="qc-week-cell">${week(source)}</span></summary>${rowBody(source)}</details>`).join('')}<p class="qc-board-note">点一行看问题明细和最近几批的到报记录。近 7 天每格代表一天：<i class="qc-day full"></i>都到齐 <i class="qc-day miss"></i>有没到齐的 <i class="qc-day wait"></i>还在下载 <i class="qc-day none"></i>没有记录${since?`；记录自 ${esc(time(since))} 起积累，只统计检查时看到的最新一批。`:'。'}</p></section>`;
 }
 function reviewBounds(metric){
  const limits=finite(metric.review_min)&&finite(metric.review_max)?`${number(metric.review_min,3)} ～ ${number(metric.review_max,3)}`:'未设规则';
  return `<span>${limits}</span>${metric.review_context?`<small>${esc(metric.review_context)}</small>`:''}${metric.review_scope?`<small>${esc(metric.review_scope)}</small>`:''}${finite(metric.general_min)&&finite(metric.general_max)?`<small>通用复核范围 ${number(metric.general_min)} ～ ${number(metric.general_max)}；超出季节或高度层提醒值 ${count(metric.context_range_count)} 个${finite(metric.context_checked_cells)?` / 已检 ${count(metric.context_checked_cells)} 个`:''}</small>`:''}`;
 }
 function metrics(sources){
  const rows=sources.flatMap(source=>list(source.metrics).map(metric=>({...metric,model:source.name})));
  const flagged=rows.filter(metric=>metric.range_count>0||metric.nonfinite_count>0).length;
  return `<details class="qc-details qc-metrics" data-key="metrics"><summary><span>数值抽查明细</span><span>${count(rows.length)} 项${rows.length?flagged?`，${count(flagged)} 项有超范围或无效值`:'，都在复核范围内':''}</span></summary><div class="qc-detail-note">每个资料源抽查最近两个预报时间（或实况时次）的数值，列出最小值、最大值和复核范围。只代表抽查到的部分。</div>${rows.length?`<div class="table-scroll"><table class="qc-table"><thead><tr><th scope="col">资料源 / 起报或观测时间</th><th scope="col">时效 / 要素</th><th scope="col">单位</th><th scope="col">最小值</th><th scope="col">最大值</th><th scope="col">复核范围</th><th scope="col">已检格点 / 总格点</th><th scope="col">缺测或无效值</th><th scope="col">超范围数</th></tr></thead><tbody>${rows.map(metric=>`<tr><th scope="row"><span>${esc(metric.model)}</span><small>${esc(time(metric.run_bjt||metric.run_utc))}</small>${metric.valid_bjt?`<small>有效 ${esc(time(metric.valid_bjt))}</small>`:''}</th><td><span>${esc(metric.field||'—')}</span><small>${finite(metric.lead_hours)?`+${number(metric.lead_hours)} 小时`:'时效未确认'}${finite(metric.accumulation_hours)?` · ${number(metric.accumulation_hours)} 小时累计`:''}${metric.derived?' · u/v 合成风速':''}</small></td><td>${esc(unitLabel(metric.unit))}</td><td>${number(metric.min,3)}</td><td>${number(metric.max,3)}</td><td>${reviewBounds(metric)}</td><td>${count(metric.checked_cells)} / ${count(metric.total_cells)}</td><td class="${finite(metric.nonfinite_count)&&metric.nonfinite_count>0?'qc-error-number':''}">${count(metric.nonfinite_count)}</td><td class="${finite(metric.range_count)&&metric.range_count>0?'qc-error-number':''}">${count(metric.range_count)}</td></tr>`).join('')}</tbody></table></div>`:'<p class="qc-detail-note">本次没有抽查到数值。</p>'}</details>`;
 }
 function contextRules(snapshot){
  const policy=snapshot.rule_policy,seasons=list(snapshot.seasonal_rules),winds=list(snapshot.wind_layer_rules),windows=['1','2','3','6','12','24'];
  if(!policy)return '';
  return `<div class="qc-scope"><h3>${esc(policy.label)}</h3><p>${esc(policy.basis)}</p><p>${esc(policy.time_basis)}</p><h3>气温与降水季节阈值</h3><p>${esc(policy.season_scope)} ${esc(policy.rain_basis)}</p>${seasons.length?`<div class="table-scroll"><table class="qc-table"><thead><tr><th scope="col">季节 / 月份</th><th scope="col">地面气温 °C</th>${windows.map(window=>`<th scope="col">${window}小时降水上限 mm</th>`).join('')}</tr></thead><tbody>${seasons.map(profile=>`<tr><th scope="row">${esc(profile.label)}<small>${esc(list(profile.months).join('、'))}月${profile.override?' · 月份覆盖':''}</small></th><td>${number(profile.temperature?.min)} ～ ${number(profile.temperature?.max)}</td>${windows.map(window=>`<td>${number(profile.rain_windows?.[window])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:''}<h3>风速分层阈值</h3><p>${esc(policy.wind_basis)} 未配置分层阈值的高度层采用通用范围。</p>${winds.length?`<div class="table-scroll"><table class="qc-table"><thead><tr><th scope="col">高度层</th><th scope="col">风速复核上限 m/s</th></tr></thead><tbody>${winds.map(rule=>`<tr><th scope="row">${esc(rule.label)}</th><td>${number(rule.max)}</td></tr>`).join('')}</tbody></table></div>`:''}<h3>通用范围与时序一致性规则</h3></div>`;
 }
 function rules(snapshot){
  const entries=list(snapshot.rules).map(rule=>{
   if(typeof rule==='string')return `<li>${esc(rule)}</li>`;
   const label=rule.label||rule.name||rule.id||rule.code||'检查规则';
   const description=rule.description||rule.message||rule.note||'';
   const limits=rule.min!=null||rule.max!=null?`${rule.min==null?'未设下限':number(rule.min)} ～ ${rule.max==null?'未设上限':number(rule.max)}${rule.unit?' '+rule.unit:''}`:'';
   return `<li><strong>${esc(label)}</strong>${description?`<span>${esc(description)}</span>`:''}${limits?`<small>${esc(limits)}</small>`:''}</li>`;
  });
  const notes=[...list(snapshot.limitations).map(value=>typeof value==='string'?value:value.description||value.message||''),snapshot.excluded?.label].filter(Boolean);
  return `<details class="qc-details qc-rules" data-key="rules"><summary><span>检查规则与适用说明</span><span>阈值、抽查范围、哪些没查</span></summary><div class="qc-rule-body">${notes.length?`<div class="qc-scope qc-scope-first"><h3>适用说明</h3><ul>${notes.map(value=>`<li>${esc(value)}</li>`).join('')}</ul></div>`:''}${contextRules(snapshot)}${entries.length?`<ul class="qc-rule-grid">${entries.join('')}</ul>`:'<p class="qc-detail-note">暂无规则清单。</p>'}</div></details>`;
 }
 function render(){
  const host=root();if(!host||!opened)return;
  const snapshot=response?.snapshot,sources=list(snapshot?.sources);
  const openKeys=new Set([...host.querySelectorAll('details[data-key]')].filter(element=>element.open).map(element=>element.dataset.key));
  const focused=document.activeElement;
  const focusId=focused&&host.contains(focused)?focused.id:null;
  host.setAttribute('aria-busy',String(requesting||response?.state==='running'));
  host.innerHTML=title()+scanNotice()+(snapshot?verdict(sources)+board(sources)+metrics(sources)+rules(snapshot):`<section class="table-card qc-card"><div class="qc-empty"><span class="qc-empty-mark" aria-hidden="true">◎</span><strong>${response?.state==='running'?'正在做第一次检查':'还没有检查结果'}</strong><p>检查完成后显示各资料源的到报状态。</p></div></section>`);
  host.querySelector('#quality-refresh').onclick=()=>refresh();
  host.querySelectorAll('details[data-key]').forEach(element=>{if(openKeys.has(element.dataset.key))element.open=true;});
  if(focusId)document.getElementById(focusId)?.focus({preventScroll:true});
 }
 function clearPoll(){if(timer){clearTimeout(timer);timer=null;}}
 function planPoll(){
  clearPoll();
  if(!opened)return;
  // 页面一直开着时，每 5 分钟取一次后台定时检查的新结果。
  if(response?.state!=='running'){timer=setTimeout(()=>request('GET',false),300000);return;}
  if(!pollStarted)pollStarted=Date.now();
  if(Date.now()-pollStarted>=180000){render();return;}
  timer=setTimeout(()=>request('GET',false),2500);
 }
 async function request(method,allowAutoStart=false){
  clearPoll();if(!opened||requesting)return;
  const id=++sequence;requesting=true;error='';controller=new AbortController();
  const activeController=controller;
  const timeout=setTimeout(()=>activeController.abort(),15000);render();
  try{
   const result=await fetch(API+(method==='POST'?'/refresh':'/catalog'),{method,cache:'no-store',credentials:'same-origin',signal:activeController.signal,...(method==='POST'?{headers:{'Content-Type':'application/json'},body:'{}'}:{})});
   let data;try{data=await result.json();}catch{throw Error(result.redirected||result.status===401?'登录会话已过期，请重新登录检验平台。':`读取检查结果失败（HTTP ${result.status}）。`);}
   if(!result.ok)throw Error(data.error||`读取检查结果失败（HTTP ${result.status}）。`);
   if(!data||data.schema!==1||!['idle','running','ready','failed'].includes(data.state)||data.snapshot&&!Array.isArray(data.snapshot.sources))throw Error('检查结果格式不完整，请稍后重试。');
   if(id!==sequence||!opened)return;
   response=data;if(data.state!=='running')pollStarted=0;
  }catch(failure){
   if(id===sequence&&opened)error=failure.name==='AbortError'?'读取检查状态超时，请点击“查看进度”或“重新检查”重试。':failure.message;
  }finally{
   clearTimeout(timeout);
   if(id===sequence){requesting=false;controller=null;render();}
  }
  if(id!==sequence||!opened||error)return;
  // 结果过期（定时检查没跑到）或还没有结果时，打开页面自动补查一次。
  if(allowAutoStart&&!autoStarted&&response?.state!=='running'&&response?.state!=='failed'&&(response?.state==='idle'&&!response.snapshot||response?.stale)){autoStarted=true;return request('POST');}
  planPoll();
 }
 function refresh(){
  if(!opened||requesting)return;
  pollStarted=0;return request(response?.state==='running'?'GET':'POST');
 }
 return {
  open(){if(opened)return;opened=true;autoStarted=false;render();return request('GET',true);},
  refresh,
  close(){opened=false;sequence++;clearPoll();controller?.abort();controller=null;requesting=false;pollStarted=0;}
 };
})();
