'use strict';
window.DataQuality=(()=>{
 const API='/Verification/test/api/quality-control';
 const root=()=>document.getElementById('quality-workspace');
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const finite=value=>typeof value==='number'&&Number.isFinite(value);
 const count=value=>finite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:0}):'—';
 const number=(value,digits=2)=>finite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:digits}):'—';
 const unitLabel=value=>value==='degC'?'°C':value||'未确认';
 const list=value=>Array.isArray(value)?value:[];
 const BJT=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
 let opened=false,response=null,error='',requesting=false,controller=null,timer=null,sequence=0,pollStarted=0,autoStarted=false;
 let filters={model:'all',severity:'all',arrival:'all'};
 function time(value){
  if(!value)return '—';
  const parsed=new Date(value);
  return Number.isFinite(parsed.getTime())?BJT.format(parsed).replaceAll('/','-'):String(value);
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
 function monitored(source){return !['reserved','demo'].includes(sourceKind(source));}
 function scopeLabel(source){
  const kind=sourceKind(source);
  return kind==='demo'?'<span class="qc-tag neutral">WN3 演示共用</span>':kind==='reserved'?'<span class="qc-tag neutral">待接入</span>':'';
 }
 function badge(label,kind='neutral'){return `<span class="qc-tag ${esc(kind)}">${esc(label)}</span>`;}
 function age(hours){
  if(!finite(hours))return '时龄未确认';
  return hours<1?`${number(hours*60,0)} 分钟前起报`:`${number(hours,1)} 小时前起报`;
 }
 function frameProgress(arrival,source){
  const originalUnit=arrival.frame_unit||source.frame_unit||(source.id==='observations'?'个小时':'个预报时间');
  const unit=originalUnit==='字段文件'?'份要素数据':originalUnit==='图层文件'?'份图层':originalUnit;
  if(!finite(arrival.available_frames))return `<span class="qc-muted">${esc(arrival.progress_label||'已到报数量未确认')}</span>`;
  if(!finite(arrival.expected_frames))return `<span>已下载 ${count(arrival.available_frames)} ${esc(unit)}</span><small>预期总量未设，暂不评价完整性</small>`;
  const text=`${count(arrival.available_frames)} / ${count(arrival.expected_frames)} ${unit}`;
  if(arrival.expected_frames<=0)return `<span>${text}</span>`;
  const ratio=Math.max(0,Math.min(100,arrival.available_frames/arrival.expected_frames*100));
  return `<span class="qc-progress-text">${text}</span><span class="qc-progress" role="meter" aria-label="已到报预报帧" aria-valuemin="0" aria-valuemax="${esc(arrival.expected_frames)}" aria-valuenow="${esc(Math.max(0,Math.min(arrival.available_frames,arrival.expected_frames)))}" aria-valuetext="${esc(text)}"><i style="width:${ratio}%"></i></span>`;
 }
 function missingLeads(arrival){
  const values=list(arrival.missing_leads),valid=list(arrival.missing_valid_bjt);
  const shown=values.slice(0,12).map(value=>finite(value)?`+${number(value,2)}h`:String(value));
  const leads=values.length?`<small class="qc-missing">缺少 ${esc(shown.join('、'))}${values.length>shown.length?` 等 ${values.length} 个时效`:''}</small>`:'';
  const validTimes=valid.length?`<small class="qc-missing">${count(valid.length)} 个实况小时未入库：${esc(valid.slice(0,8).map(value=>time(value)).join('、'))}${valid.length>8?' 等':''}${finite(arrival.pending_recent_hours)&&arrival.pending_recent_hours>0?`；其中 ${count(arrival.pending_recent_hours)} 个还在允许的下载等待时间内`:''}</small>`:'';
  return leads+validTimes;
 }
 function backlogText(arrival){
  if(typeof arrival.backlog==='string')return arrival.backlog;
  if(finite(arrival.backlog))return arrival.backlog>0?`${count(arrival.backlog)} 批待补齐`:'未记录积压';
  if(arrival.backlog&&typeof arrival.backlog==='object'){
   if(arrival.backlog.label)return String(arrival.backlog.label);
   if(finite(arrival.backlog.pending_runs))return `${count(arrival.backlog.pending_runs)} 批待处理`;
   if(finite(arrival.backlog.missing_runs))return `${count(arrival.backlog.missing_runs)} 批未到报`;
  }
  return '—';
 }
 function issuesOf(sources){return sources.flatMap(source=>list(source.issues).map(issue=>({...issue,source_id:source.id,model:issue.model||source.name})));}
 function metricsOf(sources){return sources.flatMap(source=>list(source.metrics).map(metric=>({...metric,source_id:source.id,model:source.name})));}
 function selectedSources(sources){return filters.model==='all'?sources:sources.filter(source=>String(source.id)===filters.model);}
 function title(sources){
  const snapshot=response?.snapshot,running=response?.state==='running';
  return `<div class="page-heading qc-heading"><div><p class="eyebrow">DATA QUALITY</p><h1>数据质控</h1><p>检查资料到报进度、完整性及数值合理性。</p></div><div class="qc-heading-actions"><span class="coverage-label">${snapshot?`结果生成 ${esc(time(snapshot.generated_bjt||snapshot.generated_utc))} · 北京时间`:'尚无检查结果'}</span><button type="button" class="quiet" id="quality-refresh" ${requesting?'disabled':''}>${requesting?'读取中…':running?'检查中 · 查看进度':'↻ 重新检查'}</button></div></div>`;
 }
 function scanNotice(){
  const state=response?.state;
  if(error)return `<div class="notice error" role="alert">${esc(error)}${response?.snapshot?' 下方保留上次完成的结果。':''}</div>`;
  if(state==='running')return `<div class="notice qc-live" role="status"><span class="qc-live-dot" aria-hidden="true"></span>正在检查本地资料${response?.job?.started_bjt?`，开始于 ${esc(time(response.job.started_bjt))}`:''}。${response?.snapshot?'下方保留上次完成的结果，检查完成后更新。':'完成后将显示到报与质量明细。'}${pollStarted&&Date.now()-pollStarted>=180000?' 检查仍在进行，可稍后点击“查看进度”。':''}</div>`;
  if(state==='failed')return '<div class="notice error" role="alert">本次检查未完成。请重新检查；已有结果保留，不能据此判断当前资料全部正常。</div>';
  if(response?.stale)return '<div class="notice" role="status">当前报告已超过5分钟更新周期，重新检查后可获取最新资料状态。</div>';
  if(requesting&&!response?.snapshot)return '<div class="notice" role="status">正在读取检查状态…</div>';
  return '';
 }
 function summary(sources){
  const real=sources.filter(monitored),attention=real.filter(source=>['bad','warn'].includes(statusClass(source.arrival?.status)));
  const issues=issuesOf(real),errors=issues.filter(issue=>issue.severity==='error').length,warnings=issues.filter(issue=>issue.severity==='warning').length;
  const samples=real.reduce((sum,source)=>sum+(finite(source.checks?.sampled_files)?source.checks.sampled_files:0),0);
  const hasSampleCount=real.some(source=>finite(source.checks?.sampled_files));
  const good=real.filter(source=>statusClass(source.arrival?.status)==='good').length;
  return `<section class="qc-summary" aria-label="本次数据检查概况">
   <article class="qc-stat"><span>监测资料源</span><strong>${count(real.length)}<small>个</small></strong><p>${count(good)} 个资料源已到报 · 共 ${count(sources.length)} 个资料来源</p></article>
   <article class="qc-stat ${attention.length?'qc-stat-attention':''}"><span>到报需关注</span><strong>${count(attention.length)}<small>个资料源</small></strong><p>包括超时、未到报或批次尚未完整</p></article>
   <article class="qc-stat ${errors+warnings?'qc-stat-attention':''}"><span>检查问题</span><strong>${count(errors+warnings)}<small>条</small></strong><p>${count(errors)} 条异常 · ${count(warnings)} 条需核实；另 ${count(issues.filter(issue=>issue.severity==='info').length)} 条提示</p></article>
   <article class="qc-stat"><span>本次数值抽查</span><strong>${hasSampleCount?count(samples):'—'}<small>份要素数据</small></strong><p>仅代表已抽查范围，未检查数据不作质量结论</p></article>
  </section>`;
 }
 function controls(sources){
  return `<section class="filter-card qc-filters" aria-label="质控结果筛选"><label class="filter-field"><span>资料源</span><select id="quality-model">${[{id:'all',name:'全部资料源'},...sources].map(source=>`<option value="${esc(source.id)}" ${String(source.id)===filters.model?'selected':''}>${esc(source.name)}</option>`).join('')}</select></label><label class="filter-field"><span>到报状态</span><select id="quality-arrival"><option value="all" ${filters.arrival==='all'?'selected':''}>全部状态</option><option value="attention" ${filters.arrival==='attention'?'selected':''}>仅需关注</option><option value="monitored" ${filters.arrival==='monitored'?'selected':''}>已接入资料</option><option value="reserved" ${filters.arrival==='reserved'?'selected':''}>待接入 / 演示共用</option></select></label><div class="filter-field"><span>质量问题等级</span><div class="choice-buttons" role="group" aria-label="质量问题等级">${[['all','全部'],['error','异常'],['warning','需核实'],['info','提示']].map(([value,label])=>`<button type="button" data-quality-severity="${value}" aria-pressed="${filters.severity===value}">${label}</button>`).join('')}</div></div><p class="filter-note">按资料源和到报状态筛选，问题明细另按等级筛选。演示模式共用WN3资料，不代表目标模式真实到报。</p></section>`;
 }
 function arrivals(sources){
  const rows=selectedSources(sources).filter(source=>filters.arrival==='all'||filters.arrival==='monitored'&&monitored(source)||filters.arrival==='reserved'&&!monitored(source)||filters.arrival==='attention'&&monitored(source)&&['bad','warn'].includes(statusClass(source.arrival?.status)));
  return `<section class="table-card qc-card"><div class="table-heading"><div><h2>数据到报</h2><p>按最新预报起报时间和实况观测时间检查到报情况；文件到报与数值质量分别评价。</p></div><span class="qc-count">${count(rows.length)} / ${count(sources.length)} 个资料来源</span></div><div class="table-scroll qc-arrival-scroll"><table class="qc-table"><thead><tr><th scope="col">资料源 / 模式</th><th scope="col">到报状态</th><th scope="col">预报起报时间 / 实况观测时间 · 北京时间</th><th scope="col">更新提醒时限 / 到报规则</th><th scope="col">最新批次 / 时段进度</th><th scope="col">下载积压 / 抽查范围</th></tr></thead><tbody>${rows.length?rows.map(source=>{
   const a=source.arrival||{},checks=source.checks||{};
   return `<tr><th scope="row"><strong class="qc-source-name">${esc(source.name||source.id)}</strong>${scopeLabel(source)}</th><td>${badge(a.label||'状态未确认',statusClass(a.status))}${a.note?`<small>${esc(a.note)}</small>`:''}</td><td><span>${esc(time(a.latest_run_bjt||a.latest_run_utc))}</span>${monitored(source)?`<small>${source.id==='observations'?`实况有效时间${finite(a.age_hours)?` · ${number(a.age_hours,1)} 小时前`:''}`:esc(age(a.age_hours))}</small>`:''}</td><td>${esc(a.expected_label||'暂时没有到报说明')}${finite(a.warning_age_hours)?`<small>更新提醒：距最新${source.id==='observations'?'观测':'起报'}时间超过 ${number(a.warning_age_hours)} 小时且无更新资料时触发。</small>`:''}</td><td>${frameProgress(a,source)}${missingLeads(a)}</td><td><span>${esc(backlogText(a))}</span><small>${esc(checks.coverage_label||'未说明扫描范围')}</small></td></tr>`;
  }).join(''):'<tr><td colspan="6" class="qc-empty-cell">当前筛选下没有资料源。</td></tr>'}</tbody></table></div></section>`;
 }
 function anomalies(sources){
  const all=issuesOf(selectedSources(sources)),rows=all.filter(issue=>filters.severity==='all'||issue.severity===filters.severity);
  const scanned=selectedSources(sources).some(source=>finite(source.checks?.sampled_files)&&source.checks.sampled_files>0);
  return `<section class="table-card qc-card"><div class="table-heading"><div><h2>检查问题明细</h2><p>汇总缺报、文件完整性和数值异常。“需核实”不等同于数据错误，应结合天气背景复核。</p></div><span class="qc-count">${count(rows.length)} 条</span></div>${rows.length?`<div class="table-scroll qc-issues-scroll"><table class="qc-table"><thead><tr><th scope="col">等级</th><th scope="col">资料源</th><th scope="col">起报 / 实况有效时间 · BJT</th><th scope="col">检查要素</th><th scope="col">发现与解释</th></tr></thead><tbody>${rows.map(issue=>`<tr><td>${badge(severityLabel(issue.severity),issue.severity==='error'?'bad':issue.severity==='warning'?'warn':'neutral')}</td><td>${esc(issue.model||'—')}</td><td>${esc(time(issue.run_bjt||issue.run_utc))}${finite(issue.lead_hours)?`<small>+${number(issue.lead_hours)} 小时</small>`:''}</td><td>${esc(issue.field||'文件 / 批次')}</td><td class="qc-message"><span>${esc(issue.message||'未提供问题说明')}</span>${issue.code?`<small class="qc-code">规则 ${esc(issue.code)}</small>`:''}</td></tr>`).join('')}</tbody></table></div>`:`<div class="qc-empty"><span class="qc-empty-mark" aria-hidden="true">${all.length?'⌕':scanned?'✓':'—'}</span><strong>${all.length?'该等级下没有问题':scanned?'已抽查数据未发现规则异常':'暂无可评价的抽查结果'}</strong><p>${all.length?'可以切换问题等级或资料源。':scanned?'结论仅适用于本次抽查范围；未检查或未接入资料请参照到报表。':'暂无数值检查结果，请先核对到报情况。'}</p></div>`}</section>`;
 }
 function reviewBounds(metric){
  const limits=finite(metric.review_min)&&finite(metric.review_max)?`${number(metric.review_min,3)} ～ ${number(metric.review_max,3)}`:'未设规则';
  return `<span>${limits}</span>${metric.review_context?`<small>${esc(metric.review_context)}</small>`:''}${metric.review_scope?`<small>${esc(metric.review_scope)}</small>`:''}${finite(metric.general_min)&&finite(metric.general_max)?`<small>通用复核范围 ${number(metric.general_min)} ～ ${number(metric.general_max)}；超出季节或高度层提醒值 ${count(metric.context_range_count)} 个${finite(metric.context_checked_cells)?` / 已检 ${count(metric.context_checked_cells)} 个`:''}</small>`:''}`;
 }
 function contextRules(snapshot){
  const policy=snapshot.rule_policy,seasons=list(snapshot.seasonal_rules),winds=list(snapshot.wind_layer_rules),windows=['1','2','3','6','12','24'];
  if(!policy)return '';
  return `<div class="qc-scope"><h3>${esc(policy.label)}</h3><p>${esc(policy.basis)}</p><p>${esc(policy.time_basis)}</p><h3>气温与降水季节阈值</h3><p>${esc(policy.season_scope)} ${esc(policy.rain_basis)}</p>${seasons.length?`<div class="table-scroll"><table class="qc-table"><thead><tr><th scope="col">季节 / 月份</th><th scope="col">地面气温 °C</th>${windows.map(window=>`<th scope="col">${window}小时降水上限 mm</th>`).join('')}</tr></thead><tbody>${seasons.map(profile=>`<tr><th scope="row">${esc(profile.label)}<small>${esc(list(profile.months).join('、'))}月${profile.override?' · 月份覆盖':''}</small></th><td>${number(profile.temperature?.min)} ～ ${number(profile.temperature?.max)}</td>${windows.map(window=>`<td>${number(profile.rain_windows?.[window])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:''}<h3>风速分层阈值</h3><p>${esc(policy.wind_basis)} 未配置分层阈值的高度层采用通用范围。</p>${winds.length?`<div class="table-scroll qc-arrival-scroll"><table class="qc-table"><thead><tr><th scope="col">高度层</th><th scope="col">风速复核上限 m/s</th></tr></thead><tbody>${winds.map(rule=>`<tr><th scope="row">${esc(rule.label)}</th><td>${number(rule.max)}</td></tr>`).join('')}</tbody></table></div>`:''}<h3>通用范围与时序一致性规则</h3></div>`;
 }
 function metrics(sources){
  const rows=metricsOf(selectedSources(sources));
  return `<details class="qc-details qc-metrics"><summary><span>已检字段与取值</span><span>${count(rows.length)} 个检查项（含合成风速）</span></summary><div class="qc-detail-note">列出各要素的有效时间、高度层及实际复核范围。季节阈值仅用于评分区域；同一要素、同一格点的范围异常不重复计数。</div>${rows.length?`<div class="table-scroll"><table class="qc-table"><thead><tr><th scope="col">资料源 / 起报或实况有效时间 · BJT</th><th scope="col">时效 / 要素</th><th scope="col">单位</th><th scope="col">最小值</th><th scope="col">最大值</th><th scope="col">实际复核范围 / 判断依据</th><th scope="col">已检格点 / 总格点</th><th scope="col">缺测或无效值</th><th scope="col">超阈值数</th></tr></thead><tbody>${rows.map(metric=>`<tr><th scope="row"><span>${esc(metric.model)}</span><small>${esc(time(metric.run_bjt||metric.run_utc))}</small>${metric.valid_bjt?`<small>有效 ${esc(time(metric.valid_bjt))}</small>`:''}</th><td><span>${esc(metric.field||'—')}</span><small>${finite(metric.lead_hours)?`+${number(metric.lead_hours)} 小时`:'时效未确认'}${finite(metric.accumulation_hours)?` · ${number(metric.accumulation_hours)} 小时累计`:''}${metric.derived?' · u/v 合成风速':''}</small></td><td>${esc(unitLabel(metric.unit))}</td><td>${number(metric.min,3)}</td><td>${number(metric.max,3)}</td><td>${reviewBounds(metric)}</td><td>${count(metric.checked_cells)} / ${count(metric.total_cells)}</td><td class="${finite(metric.nonfinite_count)&&metric.nonfinite_count>0?'qc-error-number':''}">${count(metric.nonfinite_count)}</td><td class="${finite(metric.range_count)&&metric.range_count>0?'qc-error-number':''}">${count(metric.range_count)}</td></tr>`).join('')}</tbody></table></div>`:'<p class="qc-detail-note">当前资料源没有已检字段样本。</p>'}</details>`;
 }
 function rules(snapshot){
  const values=list(snapshot.rules);
  const entries=values.map(rule=>{
   if(typeof rule==='string')return `<li>${esc(rule)}</li>`;
   const label=rule.label||rule.name||rule.id||rule.code||'检查规则';
   const description=rule.description||rule.message||rule.note||'';
   const limits=rule.min!=null||rule.max!=null?`${rule.min==null?'未设下限':number(rule.min)} ～ ${rule.max==null?'未设上限':number(rule.max)}${rule.unit?' '+rule.unit:''}`:'';
   return `<li><strong>${esc(label)}</strong>${description?`<span>${esc(description)}</span>`:''}${limits?`<small>${esc(limits)}</small>`:''}</li>`;
  });
  return `<details class="qc-details qc-rules"><summary><span>质控规则与检查范围</span><span>查看季节 / 高度层阈值</span></summary><div class="qc-rule-body">${contextRules(snapshot)}${entries.length?`<ul class="qc-rule-grid">${entries.join('')}</ul>`:'<p class="qc-detail-note">暂无规则清单，不据此推断其他要素已完成检查。</p>'}${list(snapshot.limitations).length?`<div class="qc-scope"><h3>检查范围与适用说明</h3><ul>${snapshot.limitations.map(value=>`<li>${esc(typeof value==='string'?value:value.description||value.message||'')}</li>`).join('')}</ul></div>`:''}</div></details>`;
 }
 function bind(sources){
  const host=root();if(!host)return;
  host.querySelector('#quality-refresh').onclick=()=>refresh();
  const model=host.querySelector('#quality-model'),arrival=host.querySelector('#quality-arrival');
  if(model)model.onchange=event=>{filters.model=event.target.value;render();root().querySelector('#quality-model')?.focus();};
  if(arrival)arrival.onchange=event=>{filters.arrival=event.target.value;render();root().querySelector('#quality-arrival')?.focus();};
  host.querySelectorAll('[data-quality-severity]').forEach(button=>button.onclick=()=>{filters.severity=button.dataset.qualitySeverity;render();root().querySelector(`[data-quality-severity="${filters.severity}"]`)?.focus();});
 }
 function render(){
  const host=root();if(!host||!opened)return;
  const snapshot=response?.snapshot,sources=list(snapshot?.sources);
  if(filters.model!=='all'&&!sources.some(source=>String(source.id)===filters.model))filters.model='all';
  const savedScroll=[...host.querySelectorAll('.table-scroll')].map(element=>({top:element.scrollTop,left:element.scrollLeft}));
  const savedDetails=[...host.querySelectorAll('.qc-details')].map(element=>element.open);
  const focused=document.activeElement;
  const focusId=focused&&host.contains(focused)?focused.id:null;
  host.setAttribute('aria-busy',String(requesting||response?.state==='running'));
  host.innerHTML=title(sources)+scanNotice()+(snapshot?summary(sources)+(snapshot.excluded?.label?`<p class="notice qc-excluded">${esc(snapshot.excluded.label)}</p>`:'')+controls(sources)+arrivals(sources)+anomalies(sources)+metrics(sources)+rules(snapshot):`<section class="table-card qc-card"><div class="qc-empty"><span class="qc-empty-mark" aria-hidden="true">◎</span><strong>${response?.state==='running'?'正在建立检查结果':'等待首次检查'}</strong><p>检查完成后会展示各资料源的到报进度、扫描范围与检查问题。</p></div></section>`);
  bind(sources);
  host.querySelectorAll('.table-scroll').forEach((element,index)=>{if(savedScroll[index]){element.scrollTop=savedScroll[index].top;element.scrollLeft=savedScroll[index].left;}});
  host.querySelectorAll('.qc-details').forEach((element,index)=>{if(savedDetails[index]!==undefined)element.open=savedDetails[index];});
  if(focusId)document.getElementById(focusId)?.focus({preventScroll:true});
 }
 function clearPoll(){if(timer){clearTimeout(timer);timer=null;}}
 function planPoll(){
  clearPoll();
  if(!opened||response?.state!=='running')return;
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
