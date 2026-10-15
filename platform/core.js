/* Pure scoring functions. Both charts and exports use these same functions. */
(function(root){
  'use strict';
  const div=(a,b)=>b>0?a/b:null;
  const leadGroups=[
    {id:'near',name:'临近预报',label:'临近预报 · 0–24小时',min:0,max:24,includeMin:true},
    {id:'short',name:'短期预报',label:'短期预报 · 24–72小时',min:24,max:72,includeMin:false},
    {id:'medium',name:'中期预报',label:'中期预报 · 72–168小时',min:72,max:168,includeMin:false}
  ];
  function inLeadGroup(lead,id){
    const g=leadGroups.find(g=>g.id===id);
    return Boolean(g&&Number.isFinite(lead)&&lead<=g.max&&(g.includeMin?lead>=g.min:lead>g.min));
  }
  function filterLeadGroup(rows,id){return rows.filter(r=>inLeadGroup(r.lead,id));}
  function forecastCoverage(rows){
    return {lead_hours:[...new Set(rows.map(r=>r.lead).filter(Number.isFinite))].sort((a,b)=>a-b).join(';'),
      forecast_count:new Set(rows.map(r=>JSON.stringify([r.lead,r.valid]))).size};
  }
  function score(rows){
    if(!rows.length)return null;
    const coverage=forecastCoverage(rows);
    if(rows[0].H===undefined){
      const valid_times=[...new Set(rows.map(r=>r.valid))].sort().join(';');
      if(rows.length===1)return {...rows[0],cycles:1,valid_times,...coverage,aggregation:'single'};
      // Each forecast time has equal weight. RMSE combines squared errors;
      // CC is the mean of spatial correlations, not a pooled correlation.
      const result={cycles:new Set(rows.map(r=>r.valid)).size,valid_times,...coverage,
        n:rows.every(r=>Number.isFinite(r.n))?rows.reduce((s,r)=>s+r.n,0):null,
        aggregation:coverage.lead_hours.includes(';')?'equal-forecast':'equal-time'};
      for(const metric of ['ME','MAE','RMSE','VRMSE','CC']){
        if(!rows.some(r=>Object.hasOwn(r,metric)))continue;
        if(!rows.every(r=>Number.isFinite(r[metric]))){result[metric]=null;continue;}
        const squared=metric==='RMSE'||metric==='VRMSE';
        const mean=rows.reduce((s,r)=>s+(squared?r[metric]**2:r[metric]),0)/rows.length;
        result[metric]=squared?Math.sqrt(mean):mean;
      }
      return result;
    }
    const sum=k=>rows.reduce((s,r)=>s+r[k],0), H=sum('H'),M=sum('M'),F=sum('F');
    const n=rows.every(r=>Number.isFinite(r.n))?sum('n'):null;
    const chance=n>0?(H+M)*(H+F)/n:null;
    return {H,M,F,n,C:n!==null?n-H-M-F:null,...coverage,cycles:new Set(rows.map(r=>r.valid)).size,valid_times:[...new Set(rows.map(r=>r.valid))].sort().join(';'),
      TS:div(H,H+M+F),POD:div(H,H+M),FAR:div(F,H+F),MR:div(M,H+M),
      BIAS:div(H+F,H+M),SR:div(H,H+F),ETS:chance!==null?div(H-chance,H+M+F-chance):null};
  }
  function calendarWindow(days,now=Date.now()){
    const end=new Date(Number(now)+8*3600000).toISOString().slice(0,10);
    const first=new Date(end+'T00:00:00Z');first.setUTCDate(first.getUTCDate()-Number(days)+1);
    return {start:first.toISOString().slice(0,10),end};
  }
  const timeKey=r=>JSON.stringify([r.lead,r.valid]);
  const sampleKey=(lead,valid,threshold)=>JSON.stringify([lead,valid,threshold??null]);
  function commonTimes(rows,models,options={}){
    const selected=new Set(models);
    if(!selected.size)return [];
    const groups=new Map(),leads=[...new Set(rows.map(r=>r.lead))];
    for(const r of rows){
      if(!selected.has(r.model))continue;
      const key=sampleKey(r.lead,r.valid,r.threshold);
      if(!groups.has(key))groups.set(key,new Set());
      groups.get(key).add(r.model);
    }
    const complete=key=>{
      const present=groups.get(key);
      return present!==undefined&&[...selected].every(m=>present.has(m));
    };
    // Record presence is independent of score denominators. An archived
    // zero-event contingency table still belongs to the common sample.
    return rows.filter(r=>selected.has(r.model)&&(options.acrossLeads
      ?leads.every(lead=>complete(sampleKey(lead,r.valid,r.threshold)))
      :complete(sampleKey(r.lead,r.valid,r.threshold))));
  }
  function sampleCoverage(rows,models,options={}){
    const selected=[...new Set(models)],available=rows.filter(r=>selected.includes(r.model));
    const common=commonTimes(rows,selected,options);
    const counts=items=>({pairs:new Set(items.map(timeKey)).size,valid:new Set(items.map(r=>r.valid)).size});
    const all=counts(available),used=counts(common);
    // Counts describe archived records, not an assumed observation schedule.
    // Multiple thresholds are deduplicated by lead+valid; pass a single
    // threshold for threshold-specific coverage. A pair/time is participating
    // if any of its passed threshold records is retained by commonTimes.
    return {
      countUnit:'lead-valid',acrossLeads:Boolean(options.acrossLeads),
      leads:[...new Set(rows.map(r=>r.lead))].sort((a,b)=>a-b),
      unionCount:all.pairs,commonCount:used.pairs,excludedCount:all.pairs-used.pairs,
      unionValidCount:all.valid,commonValidCount:used.valid,excludedValidCount:all.valid-used.valid,
      perModel:selected.map(model=>{
        const before=counts(available.filter(r=>r.model===model)),after=counts(common.filter(r=>r.model===model));
        return {model,availableCount:before.pairs,commonCount:after.pairs,excludedCount:before.pairs-after.pairs,
          availableValidCount:before.valid,commonValidCount:after.valid,excludedValidCount:before.valid-after.valid};
      })
    };
  }
  function grouped(rows,keys){
    const groups=new Map();
    for(const r of rows){const key=JSON.stringify(keys.map(k=>r[k]));if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
    return [...groups.values()].map(items=>({...Object.fromEntries(keys.map(k=>[k,items[0][k]])),...score(items)}));
  }
  function csv(rows,columns){
    const cell=x=>'"'+(typeof x==='number'?String(x):String(x??'').replace(/^[=+@\-]/,s=>"'"+s)).replace(/"/g,'""')+'"';
    return '\uFEFF'+[columns.map(c=>cell(c[1])).join(','),...rows.map(r=>columns.map(c=>cell(r[c[0]])).join(','))].join('\r\n');
  }
  function comparisonCatalog(input){
    // A display-only union. Original datasets and all scoring values remain intact.
    const datasets=input.datasets.filter(d=>d.source!=='comparison'),ids=new Set(datasets.map(d=>d.id));
    const rows=input.rows.filter(r=>ids.has(r.dataset)),groups=new Map(),extraSets=[],extraRows=[];
    const uniq=items=>[...new Set(items)];
    for(const d of datasets){
      if(d.pilot||!['hk','weekly','lead'].includes(d.source))continue;
      const key=JSON.stringify([d.kind,d.accumulation??null,d.level??null,d.reference,d.region,...(d.kind==='地面'?[d.element]:[])]);
      if(!groups.has(key))groups.set(key,[]);groups.get(key).push(d);
    }
    for(const sets of groups.values()){
      if(sets.length<2)continue;
      const byId=new Map(sets.map(d=>[d.id,d])),nativeModels=new Set(sets.filter(d=>d.source==='hk').flatMap(d=>d.models));
      const candidates=rows.filter(r=>byId.has(r.dataset));
      // Pick one legacy calculation cohort per run and lead for ALL its models.
      // Prefer the weekly common-grid batch when available; never fill a missing
      // model or threshold from another batch of the same run and lead.
      const legacy=new Map(),cohort=r=>JSON.stringify([r.run,r.lead,r.valid]);
      for(const r of candidates){
        const d=byId.get(r.dataset);if(d.source==='hk'||nativeModels.has(r.model))continue;
        const old=legacy.get(cohort(r)),rank=d.source==='weekly'?0:1;
        if(!old||rank<old.rank||rank===old.rank&&(r.created??0)>old.created)legacy.set(cohort(r),{id:d.id,rank,created:r.created??0});
      }
      const dedup=new Map();
      for(const r of candidates){
        const d=byId.get(r.dataset);
        if(nativeModels.has(r.model)?d.source!=='hk':legacy.get(cohort(r))?.id!==d.id)continue;
        const key=JSON.stringify([r.model,r.run,r.lead,r.valid,r.threshold??null]),old=dedup.get(key);
        if(!old||(r.created??0)>(old.created??0))dedup.set(key,r);
      }
      const items=[...dedup.values()];if(!items.length)continue;
      const first=sets[0],source_ids=sets.map(d=>d.id).sort(),id='compare:'+source_ids.join('+');
      const supported_models=uniq(sets.flatMap(d=>d.supported_models||d.models));
      const models=supported_models.filter(m=>items.some(r=>r.model===m)),times=uniq(items.map(r=>r.valid)).sort();
      extraSets.push({...first,id,source:'comparison',source_ids,supported_models,models,model_issues:{},
        has_n:items.some(r=>Number.isFinite(r.n)),start:times[0],end:times.at(-1),times,
        leads:uniq(items.map(r=>r.lead)).sort((a,b)=>a-b),thresholds:uniq(items.map(r=>r.threshold).filter(t=>t!==null&&t!==undefined)).sort((a,b)=>a-b),
        updated:sets.map(d=>d.updated).sort().at(-1),
        source_note:[...uniq(sets.filter(d=>d.source==='hk').map(d=>d.source_note).filter(Boolean)),'其他模式使用原 GRIB 评分。同一时次的原 GRIB 记录优先使用逐时次汇总批次，整批选取，不补拼缺失模式。共同有效时间不等于已验证严格共同格点；各模式保留原评分与来源。'].join(' ')});
      extraRows.push(...items.map(r=>({...r,dataset:id,source_dataset:r.dataset})));
    }
    return {...input,datasets:[...extraSets,...datasets],rows:[...rows,...extraRows]};
  }
  function unifiedCatalog(input){
    if(input.origins&&input.datasets.every(d=>d.source==='unified'))return input;
    const prepared=comparisonCatalog(input);
    const origins=prepared.datasets.filter(d=>d.source!=='comparison');
    const combined=prepared.datasets.filter(d=>d.source==='comparison');
    const covered=new Set(combined.flatMap(d=>d.source_ids));
    const groups=[...combined,...origins.filter(d=>!covered.has(d.id))];
    const datasets=[],rows=[];
    for(const d of groups){
      // Identity follows scientific conditions, so a channel appearing or
      // disappearing does not reset the user's selected period or models.
      const id='unified:'+JSON.stringify([d.kind,d.accumulation??null,d.level??null,d.reference,d.region,...(d.kind==='地面'?[d.element]:[])])+(d.pilot?':weathernext3-pilot':'');
      const items=prepared.rows.filter(r=>r.dataset===d.id),source_ids=d.source_ids||[d.id];
      datasets.push({...d,id,original_source:d.source,source:'unified',source_ids,aliases:[d.id,...source_ids],
        has_n:items.some(r=>Number.isFinite(r.n)),
        source_note:(d.pilot||d.source==='analysis')?d.source_note:[d.source_note,'资料自动整合并去重，同一时次保留完整计算批次，不补拼缺失模式。共同有效时间不等于已验证完全相同的格点；各模式保留原始评分。'].filter(Boolean).join(' ')});
      rows.push(...items.map(r=>({...r,dataset:id,source_dataset:r.source_dataset||r.dataset})));
    }
    return {...input,datasets,rows,origins};
  }
  function circulationMetrics(field){return field==='wind'?['VRMSE']:['RMSE','ME','MAE','CC'];}
  function circulationHasScore(row,field){return circulationMetrics(field).some(metric=>Number.isFinite(row[metric]));}
  function demoCatalog(input,profiles,sourceModel){
    // Add selector metadata only; source rows and saved scoring archives stay real.
    const datasets=input.datasets.map(d=>{
      const available=d.models.includes(sourceModel),aliases=profiles.map(p=>p.id);
      return {...d,models:[...d.models,...(available?aliases:[])],
        supported_models:[...new Set([...(d.supported_models||d.models),...aliases])],
        model_issues:{...d.model_issues,...Object.fromEntries(profiles.filter(()=>!available).map(p=>[p.id,
          d.kind==='环流'?'WN3暂无等压面高空场':d.model_issues?.[sourceModel]||'WN3暂无本要素的实况配对']))}};
    });
    return {...input,datasets,demo_profiles:profiles};
  }
  function demoRows(rows,models,profiles,sourceModel){
    const aliases=profiles.filter(p=>models.includes(p.id));
    const originals=rows.filter(r=>models.includes(r.model));
    // Expand only actively selected aliases, avoiding a 12-fold cache/RAM copy.
    const demos=aliases.flatMap(p=>rows.filter(r=>r.model===sourceModel).map(r=>({...r,model:p.id,
      is_demo:true,source_model:sourceModel,target_product_id:p.target_product_id,excluded_from_official_scores:true})));
    return [...originals,...demos];
  }
  const api={leadGroups,inLeadGroup,filterLeadGroup,forecastCoverage,calendarWindow,score,commonTimes,sampleCoverage,grouped,csv,comparisonCatalog,unifiedCatalog,circulationMetrics,circulationHasScore,demoCatalog,demoRows};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.VerificationCore=api;
})(typeof globalThis!=='undefined'?globalThis:this);
