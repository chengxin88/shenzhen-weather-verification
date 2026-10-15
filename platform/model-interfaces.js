'use strict';
window.ModelInterfaces=(()=>{
 const root=document.getElementById('interfaces-workspace');
 const api='/Verification/test/api/model-interfaces';
 let snapshot=null,loading=false;
 function el(tag,text,className){
  const node=document.createElement(tag);
  if(text!==undefined)node.textContent=text;
  if(className)node.className=className;
  return node;
 }
 function render(data){
  root.replaceChildren();
  const heading=el('div',undefined,'page-heading');
  const title=el('div');title.append(el('h1','模式接入'));heading.append(title);
  heading.append(el('span',`${data.products.length} 个产品入口`,'coverage-label'));root.append(heading);
  const reserved=data.products.filter(p=>p.status==='reserved').length;
  root.append(el('p',`${reserved} 个产品等待资料源；ECMWF 台风产品复用现有检验。新模式接入后按真实资料开展评分。`,'notice'));
  const showDemo=data.demo?renderDemo(data):null;
  if(data.ui_reference)renderReference(data.ui_reference);
  for(const [group,label] of Object.entries(data.groups)){
   const products=data.products.filter(p=>p.group===group);
   const section=el('section',undefined,'interface-group');section.append(el('h2',label));
   const cards=el('div',undefined,'interface-cards');
   for(const p of products){
    const card=el('article',undefined,'interface-card');
    const top=el('div',undefined,'interface-card-heading');top.append(el('h3',p.label));
    const state=p.status==='existing'?'已接入 · 复用':p.status==='adapter_registered'?'适配器已绑定':'待接入';
    top.append(el('span',state,'interface-state '+(p.status==='existing'?'connected':'pending')));card.append(top);
    const resolution=p.nominal_resolution_m===null?'分辨率待资料确认':`标称分辨率 ${p.nominal_resolution_m<1000?p.nominal_resolution_m+' m':p.nominal_resolution_m/1000+' km'}`;
    card.append(el('p',p.status==='existing'?'使用现有 ECMWF-IFS 台风路径及登陆检验。':resolution,'interface-description'));
    const details=el('details');details.append(el('summary','接入要求'));
    details.append(el('p',group==='typhoon'?'需要真实起报时刻、台风编号、逐时效经纬度；提供强度时须说明风速口径。':'需要真实批次样例、要素和单位、投影与覆盖范围、起报频率及预报时效。降水需说明累计起止时段。'));
    if(p.status!=='existing'&&group!=='typhoon')details.append(el('p','接入后的区域资料按大区域 7 天、评分区域 30 天规划保存；覆盖和分辨率以原始资料为准。'));
    const link=el('a','下载接口说明','quiet');link.href=p.contract_url;link.download=p.id+'-contract.json';details.append(link);card.append(details);
    if(p.status==='existing'){
     const link=el('a','打开台风检验 →','interface-existing-link');link.href='#typhoon';card.append(link);
    }
    if(p.demo_available&&showDemo){
     const button=el('button','查看 WN3 演示','interface-demo-link');button.type='button';
     button.addEventListener('click',()=>showDemo(p.id));card.append(button);
    }
    cards.append(card);
   }
   section.append(cards);root.append(section);
  }
 }
 function hours(minutes){
  return Number.isInteger(minutes/60)?String(minutes/60):String(Number((minutes/60).toFixed(2)));
 }
 function number(value,digits=2){return typeof value==='number'&&Number.isFinite(value)?value.toFixed(digits):'—';}
 function bjt(value){return String(value).slice(0,16).replace('T',' ');}
 function renderDemo(data){
  const demo=data.demo,products=data.products.filter(p=>p.demo_available);
  const section=el('section',undefined,'interface-demo');
  section.append(el('h2','WN3 演示资料'));
  section.append(el('p','使用同一批真实 WN3 资料测试 11 个格点接口，演示评分不计入正式模式排名。目标模式仍等待自己的资料源。','interface-demo-banner'));
  section.append(el('p',`起报 ${bjt(demo.run_bjt)} · 有效 ${bjt(demo.valid_bjt)} · +${hours(demo.lead_minutes)} 小时（北京时间）`,'interface-demo-time'));
  section.append(el('p',`来源：${demo.source_model}；保留 ${demo.grid_degrees}° 原生网格、${demo.interval_minutes} 分钟资料间隔。此样例没有转换为目标模式的 500 m / 1 km 网格或 15 分钟预报。`,'interface-reference-note'));
  const controls=el('div',undefined,'interface-demo-controls');
  const target=el('select'),variable=el('select');
  target.setAttribute('aria-label','演示目标接口');variable.setAttribute('aria-label','演示要素');
  for(const p of products){const option=el('option',p.label);option.value=p.id;target.append(option);}
  for(const field of demo.fields){const option=el('option',field.label);option.value=field.variable;variable.append(option);}
  const targetLabel=el('label','目标接口');targetLabel.append(target);
  const fieldLabel=el('label','要素');fieldLabel.append(variable);controls.append(targetLabel,fieldLabel);section.append(controls);
  const status=el('p',undefined,'interface-demo-status');status.setAttribute('role','status');section.append(status);
  const content=el('div');section.append(content);
  section.append(el('p','配对评分使用深圳范围的实况格点。台风路径接口需另提供真实路径样例，本次格点演示不生成台风路径。','interface-reference-note'));
  root.append(section);
  let revision=0,detailPromise=null;
  async function read(url){
   const response=await fetch(url,{cache:'no-store',credentials:'same-origin',signal:AbortSignal.timeout(15000)});
   if(!response.ok)throw new Error('sample unavailable');return response.json();
  }
  async function update(){
   const current=++revision,p=products.find(p=>p.id===target.value),spec=demo.fields.find(f=>f.variable===variable.value);
   if(!p||!spec)return;
   status.textContent='正在读取 WN3 演示格点及实况配对…';status.classList.remove('error');content.replaceChildren();
   const query=new URLSearchParams({run_utc:demo.run_utc,lead_minutes:String(demo.lead_minutes),variable:spec.variable});
   if(spec.accumulation_minutes!==null)query.set('accumulation_minutes',String(spec.accumulation_minutes));
   const endpoint=p.demo_endpoint+'?'+query;
   try{
    if(!detailPromise)detailPromise=read(api+'/demo/detail').catch(error=>{detailPromise=null;throw error;});
    const [field,detail]=await Promise.all([read(endpoint),detailPromise]);
    if(current!==revision||!root.contains(section))return;
    if(field.is_demo!==true||field.excluded_from_official_scores!==true||field.sample_id!==demo.sample_id||
       field.product_id!==p.id||detail.sample_id!==demo.sample_id||!detail.pairs[spec.variable])throw new Error('sample mismatch');
    const pairs=detail.pairs[spec.variable],rows=field.values.length,columns=field.lon.length;
    status.textContent=`${p.label}：演示通道读取成功 · ${columns} × ${rows} 格点 · ${pairs.forecast.length} 个深圳有效配对`;
    content.append(el('p','当前格点和评分均来自 WN3，不能作为所选目标模式的预报能力结论。','interface-demo-banner'));
    if(spec.variable==='precipitation')content.append(el('p',`累计窗口：${bjt(new Date(field.interval_start_utc).toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'}))} ～ ${bjt(demo.valid_bjt)}（北京时间，3 小时）`,'interface-demo-time'));
    renderScores(content,detail.scores,spec);
    const plots=el('div',undefined,'interface-demo-plots');
    plots.append(gridPlot(field,spec),scatterPlot(pairs,spec));content.append(plots);
    const download=el('button','下载 WN3 演示 JSON','interface-demo-link');download.type='button';
    download.addEventListener('click',()=>{
     const url=URL.createObjectURL(new Blob([JSON.stringify(field)],{type:'application/json'}));
     const link=el('a');link.href=url;link.download=`WN3-DEMO-${p.id}-${spec.variable}-${demo.sample_id}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });content.append(download);
   }catch(error){
    if(current!==revision||!root.contains(section))return;
    status.textContent='演示资料暂时无法读取，请切换要素或刷新资料重试。';status.classList.add('error');
   }
  }
  target.addEventListener('change',update);variable.addEventListener('change',update);update();
  return id=>{target.value=id;update();section.scrollIntoView({behavior:'smooth',block:'start'});};
 }
 function renderScores(parent,scores,spec){
  if(spec.variable==='temperature_2m'){
   const score=scores.temperature_2m,metrics=el('div',undefined,'interface-demo-metrics');
   for(const [label,value] of [['ME · °C',score.ME],['MAE · °C',score.MAE],['RMSE · °C',score.RMSE],['相关系数',score.CC]]){
    const item=el('div');item.append(el('span',label),el('strong',number(value)));metrics.append(item);
   }
   parent.append(metrics);return;
  }
  const scroll=el('div',undefined,'table-scroll'),table=el('table'),head=el('thead'),row=el('tr');
  for(const title of ['阈值 · mm / 3h','命中 H','漏报 M','空报 F','TS','POD','FAR'])row.append(el('th',title));
  head.append(row);table.append(head);const body=el('tbody');
  for(const score of scores.precipitation){
   const tr=el('tr');for(const value of [score.threshold,score.H,score.M,score.F,number(score.TS,3),number(score.POD,3),number(score.FAR,3)])tr.append(el('td',String(value)));body.append(tr);
  }
  table.append(body);scroll.append(table);parent.append(scroll);
  parent.append(el('p','TS 为命中 /（命中 + 漏报 + 空报）；无可评分事件时显示“—”。单批结果仅用于检查接入流程。','interface-reference-note'));
 }
 function gridPlot(field,spec){
  const panel=el('figure'),caption=el('figcaption',`WN3 ${spec.label} · 原生格点场`);panel.append(caption);
  const rows=field.lat.length,columns=field.lon.length,canvas=el('canvas');canvas.width=columns*4;canvas.height=rows*4;
  const values=field.values.flat().filter(Number.isFinite);let low=Math.min(...values),high=Math.max(...values);
  const context=canvas.getContext('2d');
  for(let y=0;y<rows;y++)for(let x=0;x<columns;x++){
   const value=field.values[y][x],ratio=high===low?.5:(value-low)/(high-low);
   context.fillStyle=value===null?'#dce2e6':`hsl(${220-ratio*200} 65% 53%)`;context.fillRect(x*4,(rows-1-y)*4,4,4);
  }
  canvas.setAttribute('role','img');canvas.setAttribute('aria-label',`${spec.label}格点，北在上，范围 ${number(low)} 至 ${number(high)} ${spec.units}`);panel.append(canvas);
  panel.append(el('p',`西 ${number(field.lon[0],1)}°E → 东 ${number(field.lon.at(-1),1)}°E；南 ${number(field.lat[0],1)}°N → 北 ${number(field.lat.at(-1),1)}°N`,'interface-reference-note'));
  panel.append(el('p',`色阶：蓝 ${number(low)} → 红 ${number(high)} ${spec.units}；北在上。`,'interface-reference-note'));return panel;
 }
 function scatterPlot(pairs,spec){
  const panel=el('figure');panel.append(el('figcaption','WN3 预报与深圳实况配对'));
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 380 310');
  svg.setAttribute('role','img');svg.setAttribute('aria-label',`${pairs.forecast.length} 个${spec.label}预报与实况配对散点，虚线为1比1线`);
  const all=[...pairs.forecast,...pairs.observed];let low=Math.min(...all),high=Math.max(...all);
  const pad=Math.max((high-low)*.06,.1);low=spec.variable==='precipitation'?0:low-pad;high+=pad;
  const x=value=>52+(value-low)/(high-low)*270,y=value=>265-(value-low)/(high-low)*235;
  function shape(tag,attributes,text){const node=document.createElementNS(svg.namespaceURI,tag);for(const [key,value] of Object.entries(attributes))node.setAttribute(key,String(value));if(text!==undefined)node.textContent=text;svg.append(node);return node;}
  shape('line',{x1:52,y1:265,x2:322,y2:265,stroke:'#81929d'});shape('line',{x1:52,y1:30,x2:52,y2:265,stroke:'#81929d'});
  shape('line',{x1:52,y1:265,x2:322,y2:30,stroke:'#81929d','stroke-dasharray':'4 4'});
  for(let i=0;i<=4;i++){
   const value=low+(high-low)*i/4;
   const digits=spec.variable==='precipitation'?2:1;
   shape('text',{x:x(value),y:282,'text-anchor':'middle',fill:'#647782','font-size':10},number(value,digits));
   shape('text',{x:45,y:y(value)+3,'text-anchor':'end',fill:'#647782','font-size':10},number(value,digits));
  }
  for(let i=0;i<pairs.forecast.length;i++)shape('circle',{cx:x(pairs.observed[i]),cy:y(pairs.forecast[i]),r:2,fill:'#237c88','fill-opacity':.35});
  shape('text',{x:187,y:305,'text-anchor':'middle',fill:'#425969','font-size':12},`实况 · ${spec.units}`);
  shape('text',{x:53,y:17,fill:'#425969','font-size':12},`预报 · ${spec.units}`);panel.append(svg);
  panel.append(el('p','虚线表示预报与实况相等；每个点对应一个有效实况格点。','interface-reference-note'));return panel;
 }
 function renderReference(reference){
  const section=el('section',undefined,'interface-reference');
  section.append(el('h2','业务页面参数参考'));
  section.append(el('p','时间均为北京时间。时效按有效时间减起报时间计算；这里记录的是页面可选产品，实际到报及原始网格仍需样例核实。','interface-reference-note'));
  const scroll=el('div',undefined,'table-scroll'),table=el('table');
  const header=el('tr');
  for(const title of ['页面模式','标称分辨率','可选起报时次 · BJT','已核对要素及时间轴'])header.append(el('th',title));
  const head=el('thead');head.append(header);table.append(head);
  const body=el('tbody');
  for(const row of reference.models){
   const tr=el('tr');tr.append(el('td',row.ui_model));
   const resolution=row.resolution_m_hint;
   tr.append(el('td',resolution?`${resolution<1000?resolution+' m':resolution/1000+' km'}（标称）`:'页面未标明'));
   const runHours=row.run_hour_choices_bjt;
   tr.append(el('td',runHours.length===24?'每小时可选':runHours.length?runHours.join('、')+' 时':'未核对'));
   const details=el('td');
   for(const field of row.fields){
    if(!field.lead_minutes.length)continue;
    const leads=field.lead_minutes,steps=field.time_steps_minutes;
    const interval=steps.map(m=>m<60?m+' 分钟':hours(m)+' 小时').join(' / ');
    details.append(el('p',`${field.field}：+${hours(leads[0])}～${hours(leads.at(-1))} 小时；${interval?'间隔 '+interval:'仅一个时效'}`));
    if(field.label_step_minutes===15)details.append(el('p','页面 +012 是第 12 个刻度，对应 +3 小时。','interface-timing-warning'));
   }
   if(!row.fields.length)details.append(el('p','本次未完成时间轴核对'));
   if(row.note)details.append(el('p',row.note,'interface-reference-note'));
   tr.append(details);body.append(tr);
  }
  table.append(body);scroll.append(table);section.append(scroll);
  section.append(el('p','500 m、1 km 及 3 km 为页面名称中的标称分辨率。未标明的项目暂不填数值；盘古与新盘古分别记录，版本对应关系待核对。','interface-reference-note'));
  root.append(section);
 }
 async function load(refresh=false){
  if(loading)return;
  if(snapshot&&!refresh){render(snapshot);return;}
  loading=true;
  root.replaceChildren(el('p','正在读取模式接入清单…','notice'));
  try{
   const response=await fetch(api,{cache:'no-store',credentials:'same-origin',signal:AbortSignal.timeout(15000)});
   if(!response.ok)throw new Error('unavailable');
   const data=await response.json();
   if(data.schema!==1||!Array.isArray(data.products)||!data.groups)throw new Error('invalid catalog');
   snapshot=data;render(data);
  }catch(error){
   root.replaceChildren(el('p','接入清单暂时无法读取，请点击“刷新资料”重试。','notice error'));
  }finally{loading=false;}
 }
 return {open:()=>load(),refresh:()=>load(true)};
})();
