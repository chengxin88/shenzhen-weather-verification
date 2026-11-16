'use strict';
// 台风单次起报视图：一张路径图加最大风速、最低气压两张时序图。
window.TyphoonTrack=(()=>{
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const finite=v=>typeof v==='number'&&Number.isFinite(v);
 const pad=n=>String(n).padStart(2,'0');
 const bjt=t=>new Date(new Date(t).getTime()+8*3600000);
 const dayHour=t=>{const d=bjt(t);return `${pad(d.getUTCDate())}${pad(d.getUTCHours())}`;};
 const issueLabel=t=>{const d=bjt(t);return `${d.getUTCFullYear()}年${d.getUTCMonth()+1}月${d.getUTCDate()}日${pad(d.getUTCHours())}时`;};
 const tickLabel=t=>{const d=bjt(t);return `${pad(d.getUTCDate())}日${pad(d.getUTCHours())}时`;};
 const SAME_RANGE={wind:[0,70],pressure:[900,1020]};
 let root=null,catalog=null,info=null,data=null,basemap=null,hidden=new Set(),seq=0,timer=null,controller=null;
 const state={year:null,storm_id:null,issue:null,sources:null};
 const el=id=>root?.querySelector('#'+id);
 function status(text,error=false){const s=el('tt-status');if(!s)return;s.hidden=!text;s.textContent=text||'';s.classList.toggle('error',error);}
 function cancel(){seq++;clearTimeout(timer);controller?.abort();controller=null;}
 async function api(path,body,signal){
  const r=await fetch('./api/typhoon/'+path,{...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{}),signal});
  let value;try{value=await r.json();}catch{throw Error(`台风服务返回异常（HTTP ${r.status}），请稍后重试。`);}
  if(!r.ok){const e=Error(value.error||'台风资料暂不可用。');e.retry=r.status===429;throw e;}return value;
 }
 const wait=(ms,signal)=>new Promise((ok,no)=>{const t=setTimeout(ok,ms);signal.addEventListener('abort',()=>{clearTimeout(t);no(new DOMException('已切换条件','AbortError'));},{once:true});});
 function shell(){root.innerHTML=`<section class="filter-card" aria-label="单次起报条件"><div class="primary-filters typhoon-selects tt-selects"><label>台风年份<select id="tt-year"></select></label><label>选择台风<select id="tt-storm"></select></label><label>起报时刻（北京时）<select id="tt-issue"></select></label></div><div class="model-row"><span>显示来源</span><div id="tt-sources" class="model-chips"></div></div><p class="filter-note">选一次起报，比较各来源路径和强度。起报时次为08时、20时（00Z/12Z）。各模式路径由本平台从气压场识别，强度与官方风速口径不同，不能直接比较数值。</p></section><div class="notice" id="tt-status" role="status"></div><div id="tt-results" hidden></div>`;
  el('tt-year').onchange=e=>loadYear(Number(e.target.value));el('tt-storm').onchange=e=>loadStorm(Number(e.target.value));el('tt-issue').onchange=e=>{state.issue=e.target.value;loadTracks();};}
 function selectOptions(id,items,current){const s=el(id);s.innerHTML=items.map(([v,l])=>`<option value="${esc(v)}" ${String(v)===String(current)?'selected':''}>${esc(l)}</option>`).join('');}
 async function loadYear(year){
  const mine=++seq;status('正在读取台风目录…');
  try{catalog=await api('catalog'+(year?`?year=${year}`:''));if(mine!==seq)return;
   state.year=catalog.year;selectOptions('tt-year',catalog.years.map(y=>[y,y+'年']),state.year);
   selectOptions('tt-storm',catalog.storms.map(s=>[s.storm_id,`${s.number} ${String(s.chinese_name||'').trim()}（${s.english_name}）${s.status==='start'?' · 进行中':''}`]),catalog.default_storm);
   if(!catalog.storms.length){status('该年份没有台风记录。');return;}
   await loadStorm(state.storm_id&&catalog.storms.some(s=>s.storm_id===state.storm_id)?state.storm_id:catalog.default_storm);
  }catch(e){if(mine===seq)status(e.message,true);}
 }
 async function loadStorm(id){
  const mine=++seq;state.storm_id=id;status('正在读取起报时刻…');el('tt-results').hidden=true;
  el('tt-storm').value=String(id);
  try{info=await api(`issues?year=${state.year}&storm_id=${id}`);if(mine!==seq)return;
   state.issue=info.default_issue;selectOptions('tt-issue',[...info.issues].reverse().map(t=>[t,issueLabel(t)]),state.issue);
   state.sources??=info.sources.map(s=>s.id);drawChips();await loadTracks();
  }catch(e){if(mine===seq)status(e.message,true);}
 }
 function drawChips(){const box=el('tt-sources');box.innerHTML=info.sources.map(s=>`<button type="button" class="model-chip" data-source="${esc(s.id)}" aria-pressed="${state.sources.includes(s.id)}" title="${esc(s.nature)} · ${esc(s.wind_convention)}"><i style="background:${esc(s.color)}"></i>${esc(s.label)}</button>`).join('');
  box.querySelectorAll('button').forEach(b=>b.onclick=()=>{const id=b.dataset.source,on=state.sources.includes(id);if(on&&state.sources.length===1)return;state.sources=on?state.sources.filter(s=>s!==id):[...state.sources,id];b.setAttribute('aria-pressed',String(!on));loadTracks();});}
 async function loadTracks(){
  cancel();const mine=seq;controller=new AbortController();const signal=controller.signal;status('正在提取各来源路径，首次读取盘古约需1分钟…');
  const body={year:state.year,storm_id:state.storm_id,issue:state.issue,sources:state.sources};
  try{
   let job;for(;;){try{job=await api('tracks',body,signal);break;}catch(e){if(!e.retry)throw e;status('已有台风任务在运行，稍后自动重试…');await wait(2000,signal);}}
   while(job.status==='queued'||job.status==='running'){status(job.message||'正在提取路径…');await wait(1500,signal);job=await api('jobs/'+job.id,null,signal);}
   if(mine!==seq)return;if(job.status!=='done')throw Error(job.message||'台风路径暂不可用。');
   data=job.result;status('');await ensureBasemap(signal);if(mine===seq)render();
  }catch(e){if(e.name!=='AbortError'&&mine===seq)status(e.message,true);}
 }
 async function ensureBasemap(signal){if(basemap)return;const r=await fetch('./typhoon-basemap.json',{signal});if(!r.ok)throw Error('底图读取失败。');basemap=await r.json();}
 const visible=()=>data.tracks.filter(t=>t.rows.length&&!hidden.has(t.source));
 function extent(){
  const start=new Date(data.issue).getTime(),end=start+data.horizon*3600000;
  const pts=[...visible().flatMap(t=>t.rows),...data.observed.filter(r=>{const m=new Date(r.time).getTime();return m>=start-12*3600000&&m<=end;})];
  let w=Math.min(...pts.map(p=>p.lon))-3,e=Math.max(...pts.map(p=>p.lon))+3,s=Math.min(...pts.map(p=>p.lat))-3,n=Math.max(...pts.map(p=>p.lat))+3;
  const widen=(a,b,span)=>{const extra=Math.max(0,span-(b-a))/2;return [a-extra,b+extra];};[w,e]=widen(w,e,22);[s,n]=widen(s,n,14);
  return {w:Math.max(95,w),e:Math.min(180,e),s:Math.max(-2,s),n:Math.min(58,n)};
 }
 function mapSvg(){
  const b=extent(),W=560,L=40,T=10,R=10,B=26,pw=W-L-R,mid=Math.cos((b.s+b.n)/2*Math.PI/180),ph=Math.round(pw*(b.n-b.s)/((b.e-b.w)*mid)),H=ph+T+B;
  const X=x=>L+(x-b.w)/(b.e-b.w)*pw,Y=y=>T+(b.n-y)/(b.n-b.s)*ph,path=c=>c.map((p,i)=>`${i?'L':'M'}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join('');
  const inView=c=>c.some(p=>p[0]>=b.w-2&&p[0]<=b.e+2&&p[1]>=b.s-2&&p[1]<=b.n+2);
  let s=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(data.title)} 路径图" class="tt-map"><defs><clipPath id="tt-clip"><rect x="${L}" y="${T}" width="${pw}" height="${ph}"/></clipPath></defs><rect x="${L}" y="${T}" width="${pw}" height="${ph}" fill="#c7e9f7"/><g clip-path="url(#tt-clip)">`;
  s+=`<path d="${basemap.land.filter(inView).map(c=>path(c)+'Z').join('')}" fill="#fbe6cc" stroke="#7d8a93" stroke-width=".7" fill-rule="evenodd"/>`;
  s+=`<path d="${basemap.provinces.filter(inView).map(path).join('')}" fill="none" stroke="#a9b2b8" stroke-width=".5"/><path d="${basemap.countries.filter(inView).map(path).join('')}" fill="none" stroke="#6d7a83" stroke-width=".8"/>`;
  const step=b.e-b.w>40?10:5;
  for(let x=Math.ceil(b.w/step)*step;x<=b.e;x+=step)s+=`<line x1="${X(x)}" x2="${X(x)}" y1="${T}" y2="${T+ph}" stroke="#42525c" stroke-width=".4" stroke-dasharray="2 3"/>`;
  for(let y=Math.ceil(b.s/step)*step;y<=b.n;y+=step)s+=`<line x1="${L}" x2="${L+pw}" y1="${Y(y)}" y2="${Y(y)}" stroke="#42525c" stroke-width=".4" stroke-dasharray="2 3"/>`;
  const [cx,cy]=data.shenzhen||[];
  if(finite(cx)&&cx>b.w&&cx<b.e&&cy>b.s&&cy<b.n){for(const km of [100,200,400,800]){const rx=km/111/Math.cos(cy*Math.PI/180)/(b.e-b.w)*pw,ry=km/111/(b.n-b.s)*ph;s+=`<ellipse cx="${X(cx)}" cy="${Y(cy)}" rx="${rx}" ry="${ry}" fill="none" stroke="#5b6b75" stroke-width=".5" stroke-dasharray="1 3"/><text x="${X(cx)}" y="${Y(cy)-ry-2}" text-anchor="middle" font-size="9" fill="#5b6b75">${km}KM</text>`;}s+=`<rect x="${X(cx)-2}" y="${Y(cy)-2}" width="4" height="4" fill="#111"/>`;}
  const line=(rows,color,width)=>`<polyline points="${rows.map(r=>`${X(r.lon).toFixed(1)},${Y(r.lat).toFixed(1)}`).join(' ')}" fill="none" stroke="${esc(color)}" stroke-width="${width}" stroke-linejoin="round"/>`;
  const start=new Date(data.issue).getTime(),end=start+data.horizon*3600000;
  const seen=data.observed.filter(r=>{const m=new Date(r.time).getTime();return m>=start-36*3600000&&m<=end;});
  s+=line(seen,'#111',3);
  for(const t of visible()){
   s+=line(t.rows,t.color,2);
   for(const r of t.rows){s+=`<circle cx="${X(r.lon).toFixed(1)}" cy="${Y(r.lat).toFixed(1)}" r="${r.lead%24===0?3.4:2}" fill="${esc(t.color)}"><title>${esc(t.label)} +${r.lead}h ${esc(tickLabel(r.time))}${finite(r.pressure)?` · ${Math.round(r.pressure)} hPa`:''}${finite(r.wind)?` · ${r.wind.toFixed(1)} m/s`:''}</title></circle>`;
    if(r.lead>0&&r.lead%24===0)s+=`<text x="${X(r.lon)+5}" y="${Y(r.lat)-4}" font-size="10" fill="#4b5a63" stroke="#fff" stroke-width="2" paint-order="stroke">${dayHour(r.time)}</text>`;}
  }
  s+='</g>';
  for(let x=Math.ceil(b.w/step)*step;x<=b.e;x+=step)s+=`<text x="${X(x)}" y="${H-10}" text-anchor="middle" font-size="12" fill="#3f4f59">${x}°E</text>`;
  for(let y=Math.ceil(b.s/step)*step;y<=b.n;y+=step)s+=`<text x="${L-5}" y="${Y(y)+4}" text-anchor="end" font-size="12" fill="#3f4f59">${y}°N</text>`;
  return s+`<rect x="${L}" y="${T}" width="${pw}" height="${ph}" fill="none" stroke="#2c3a43"/></svg>`;
 }
 function series(key,title,unit){
  const W=560,H=228,L=44,R=12,T=24,B=52,pw=W-L-R,ph=H-T-B,start=new Date(data.issue).getTime();
  const obs=data.observed.filter(r=>finite(r[key]));const lines=visible().map(t=>({...t,rows:t.rows.filter(r=>finite(r[key]))})).filter(t=>t.rows.length);
  if(!obs.length&&!lines.length)return '';
  const times=[...data.observed,...visible().flatMap(t=>t.rows)].map(r=>new Date(r.time).getTime());
  const end=Math.min(Math.max(...times),start+data.horizon*3600000),begin=Math.max(Math.min(...times),start-120*3600000);
  const values=[...obs,...lines.flatMap(t=>t.rows)].filter(r=>{const m=new Date(r.time).getTime();return m>=begin&&m<=end;}).map(r=>r[key]);
  let lo=Math.min(...values),hi=Math.max(...values);const span=Math.max(hi-lo,10);lo=Math.max(SAME_RANGE[key][0],Math.floor((lo-span*.08)/5)*5);hi=Math.min(SAME_RANGE[key][1]+20,Math.ceil((hi+span*.08)/5)*5);
  const X=m=>L+(m-begin)/(end-begin)*pw,Y=v=>T+(hi-v)/(hi-lo)*ph;
  let s=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${title}" class="tt-chart"><text x="${L+pw/2}" y="16" text-anchor="middle" font-size="14" font-weight="600" fill="#22323c">${title}</text>`;
  const gap=(hi-lo)>60?20:10;for(let v=Math.ceil(lo/gap)*gap;v<=hi;v+=gap)s+=`<line x1="${L}" x2="${L+pw}" y1="${Y(v)}" y2="${Y(v)}" stroke="#dce5ec"/><text x="${L-7}" y="${Y(v)+4}" text-anchor="end" font-size="11" fill="#566b78">${v}</text>`;
  const tick=12*3600000,step=(end-begin)/tick>12?2:1;
  let n=0;for(let m=Math.ceil(begin/tick)*tick;m<=end;m+=tick){if(n++%step)continue;const x=X(m);s+=`<line x1="${x}" x2="${x}" y1="${T}" y2="${T+ph}" stroke="#edf1f4"/><text transform="translate(${x},${T+ph+14}) rotate(-40)" text-anchor="end" font-size="10" fill="#566b78">${tickLabel(m)}</text>`;}
  s+=`<text transform="translate(14,${T+ph/2}) rotate(-90)" text-anchor="middle" font-size="11" fill="#566b78">${unit}</text><line x1="${X(start)}" x2="${X(start)}" y1="${T}" y2="${T+ph}" stroke="#8ba1ad" stroke-dasharray="4 4"/>`;
  const draw=(rows,color,width,label)=>{const pts=rows.map(r=>[new Date(r.time).getTime(),r]).filter(([m])=>m>=begin&&m<=end);s+=`<polyline points="${pts.map(([m,r])=>`${X(m).toFixed(1)},${Y(r[key]).toFixed(1)}`).join(' ')}" fill="none" stroke="${esc(color)}" stroke-width="${width}" stroke-linejoin="round"/>`;for(const [m,r] of pts)s+=`<circle cx="${X(m).toFixed(1)}" cy="${Y(r[key]).toFixed(1)}" r="2.4" fill="${esc(color)}"><title>${esc(label)} ${esc(tickLabel(m))} ${r[key].toFixed(1)} ${unit}</title></circle>`;};
  draw(obs,'#111',2.6,'实况');for(const t of lines)draw(t.rows,t.color,1.8,t.label);
  return s+`<rect x="${L}" y="${T}" width="${pw}" height="${ph}" fill="none" stroke="#b8c6cf"/></svg>`;
 }
 function legend(){return `<div class="tt-legend"><span><i style="background:#111"></i>实况</span>${data.tracks.map(t=>`<button type="button" data-hide="${esc(t.source)}" aria-pressed="${!hidden.has(t.source)}" ${t.rows.length?'':'disabled'} title="${esc(t.status==='ok'?'点击隐藏或显示':t.status)}"><i style="background:${esc(t.color)}"></i>${esc(t.label)}</button>`).join('')}</div>`;}
 function errorTable(){
  const leads=[24,48,72,96,120],rows=visible().filter(t=>Object.keys(t.errors||{}).length);
  if(!rows.length)return '<p class="filter-note">实况时段内还没有可计算的时效，暂无误差。</p>';
  const cell=(t,l,k,u)=>{const v=t.errors[l]?.[k];return finite(v)?`${k==='km'?'':v>0?'+':''}${v}${u}`:'—';};
  return `<div class="table-scroll"><table><thead><tr><th>来源</th>${leads.map(l=>`<th>${l}h 路径(km)</th><th>${l}h 气压/风速</th>`).join('')}</tr></thead><tbody>${rows.map(t=>`<tr><td>${esc(t.label)}</td>${leads.map(l=>`<td>${cell(t,l,'km','')}</td><td>${cell(t,l,'pressure','')} / ${cell(t,l,'wind','')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
 }
 function render(){
  const box=el('tt-results');box.hidden=false;const missing=data.tracks.filter(t=>!t.rows.length);
  box.innerHTML=`<section class="results typhoon-results"><div class="result-heading"><div><h2>${esc(data.title)}</h2><p>${esc(issueLabel(data.issue))}起报 +${data.horizon}小时（北京时）。图中数字为预报时刻（日时）。</p></div><div class="tt-actions"><button class="quiet" id="tt-png">导出图片 ↓</button><button class="quiet" id="tt-csv">导出 CSV ↓</button></div></div>${legend()}<div id="tt-panels">${mapSvg()}${series('wind','中心最大风速','m/s')}${series('pressure','中心最低气压','hPa')}</div></section><section class="table-card"><div class="table-heading"><div><h2>相对实况的误差</h2><p>路径为预报点到同时刻实况的距离；气压、风速为预报减实况。模式风速不是官方口径，只宜看趋势。</p></div></div>${errorTable()}</section>${missing.length?`<details class="case-detail"><summary>无路径的来源 · ${missing.length}个</summary><p class="filter-note">${missing.map(t=>`${esc(t.label)}：${esc(t.status)}`).join('；')}。EC、GFS、AIFS、AIGFS、WN3 的本地场从2026年10月起才有，更早的台风只显示已缓存的路径；WN3、本地模式覆盖到150°E，台风东移出界后路径结束。</p></details>`:''}`;
  box.querySelectorAll('[data-hide]').forEach(b=>b.onclick=()=>{const id=b.dataset.hide;hidden.has(id)?hidden.delete(id):hidden.add(id);render();});
  el('tt-png').onclick=exportPng;el('tt-csv').onclick=exportCsv;
 }
 function save(blob,name){const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000);}
 function baseName(){return `${data.title.split('号')[0]}_${dayHour(data.issue)}起报`;}
 function exportCsv(){const lines=['来源,时效(h),时刻(BJT),经度,纬度,气压(hPa),风速(m/s)'];for(const t of data.tracks)for(const r of t.rows)lines.push([t.label,r.lead,tickLabel(r.time),r.lon,r.lat,r.pressure??'',r.wind??''].join(','));save(new Blob(['﻿'+lines.join('\n')],{type:'text/csv'}),baseName()+'.csv');}
 async function exportPng(){
  // 导出为一张竖排窄图：标题、图例、路径图、风速、气压。
  const svgs=[...el('tt-panels').querySelectorAll('svg')],scale=2,W=560,pad=40,title=`${data.title}  ${issueLabel(data.issue)}+${data.horizon}小时（北京时）`;
  const c=document.createElement('canvas'),g=c.getContext('2d');g.font='13px sans-serif';
  let x=16,row=0;const items=[['实况','#111'],...visible().map(t=>[t.label,t.color])].map(([label,color])=>{const w=g.measureText(label).width+38;if(x+w>W-8&&x>16){x=16;row++;}const item=[label,color,x,row];x+=w;return item;});
  const legendH=(row+1)*20+10,size=svgs.map(s=>s.viewBox.baseVal.height*W/s.viewBox.baseVal.width),H=pad+legendH+size.reduce((a,b)=>a+b+8,0);
  c.width=W*scale;c.height=H*scale;g.scale(scale,scale);g.fillStyle='#fff';g.fillRect(0,0,W,H);g.fillStyle='#111';g.font='600 16px sans-serif';g.fillText(title,16,26);
  g.font='13px sans-serif';for(const [label,color,px,r] of items){const y=pad+14+r*20;g.fillStyle=color;g.fillRect(px,y-8,16,4);g.fillStyle='#22323c';g.fillText(label,px+20,y-1);}
  let top=pad+legendH;for(let i=0;i<svgs.length;i++){const url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svgs[i])],{type:'image/svg+xml'}));await new Promise((ok,no)=>{const m=new Image();m.onload=()=>{g.drawImage(m,0,top,W,size[i]);URL.revokeObjectURL(url);ok();};m.onerror=no;m.src=url;});top+=size[i]+8;}
  c.toBlob(b=>save(b,baseName()+'.png'),'image/png');
 }
 return {open(host){if(root!==host||!host.querySelector('#tt-status')){root=host;shell();if(!catalog)loadYear();else{selectOptions('tt-year',catalog.years.map(y=>[y,y+'年']),state.year);loadYear(state.year);}}},close(){cancel();}};
})();
