/* Pure scoring functions. Both charts and exports use these same functions. */
(function(root){
  'use strict';
  const div=(a,b)=>b>0?a/b:null;
  function forecastCoverage(rows){
    return {lead_hours:[...new Set(rows.map(r=>r.lead).filter(Number.isFinite))].sort((a,b)=>a-b).join(';'),
      forecast_count:new Set(rows.map(r=>JSON.stringify([r.lead,r.valid]))).size};
  }
  function legacyScore(rows){
    if(!rows.length)return null;
    const coverage=forecastCoverage(rows);
    if(rows[0].H===undefined){
      const valid_times=[...new Set(rows.map(r=>r.valid))].sort().join(';');
      // A selected station has one forecast/observation pair per row. Compute
      // time correlation from those retained pairs after common-time filtering.
      if(rows.every(r=>r.station&&r.station===rows[0].station&&Number.isFinite(r.fc)&&Number.isFinite(r.ob))){
        const n=rows.length,mf=rows.reduce((s,r)=>s+r.fc,0)/n,mo=rows.reduce((s,r)=>s+r.ob,0)/n;
        let error=0,absolute=0,squared=0,vf=0,vo=0,cov=0;
        for(const r of rows){const e=r.fc-r.ob;error+=e;absolute+=Math.abs(e);squared+=e*e;vf+=(r.fc-mf)**2;vo+=(r.ob-mo)**2;cov+=(r.fc-mf)*(r.ob-mo);}
        return {n,ME:error/n,MAE:absolute/n,RMSE:Math.sqrt(squared/n),
          CC:n>=3&&vf>1e-8&&vo>1e-8?Math.max(-1,Math.min(1,cov/Math.sqrt(vf*vo))):null,
          station:rows[0].station,cycles:new Set(rows.map(r=>r.valid)).size,valid_times,...coverage,aggregation:'station-pairs'};
      }
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
  const percentMetrics=['ACC1','ACC2','PC'];
  const guifanLabels={ACC1:'≤1℃ 准确率',ACC2:'≤2℃ 准确率',PC:'PC · 晴雨准确率'};
  const guifanHelp={ACC1:'温度预报绝对误差 ≤1℃的站次百分率（规范附件7 1.2）',ACC2:'温度预报绝对误差 ≤2℃的站次百分率（规范附件7 1.2）',PC:'按 ≥0.1 mm 判断有无降水，实况微量而预报有雨算报对（规范附件7 表1）'};
  function eligible(r,metric){
    if(metric==='ACC1'||metric==='ACC2')return Number.isFinite(r[metric==='ACC1'?'N1':'N2'])&&r.n>0;
    if(metric==='PC')return !r.grade&&r.threshold===.1&&Number.isFinite(r.T)&&r.n>0;
    return true;
  }
  function metricsFor(ds,mode='cumulative'){
    if(ds.kind==='降水')return mode==='grade'&&ds.grades?.length?['TS','ETS','POD','FAR','MR','BIAS']:
      ['TS',...((ds.source==='weekly'||ds.has_n)?['ETS']:[]),'POD','FAR','MR','BIAS',...(ds.has_trace?['PC']:[])];
    return ['RMSE','ME','MAE','CC',...(ds.has_acc?['ACC2','ACC1']:[])];
  }
  function score(rows){
    const result=legacyScore(rows);if(!result)return result;
    for(const metric of percentMetrics){
      const available=rows.filter(r=>eligible(r,metric)),n=available.reduce((v,r)=>v+r.n,0);
      result[metric]=n?available.reduce((v,r)=>v+(metric==='PC'?r.n-r.M-r.F+r.T:r[metric==='ACC1'?'N1':'N2']),0)/n:null;
      result[metric+'_n']=n;
      result[metric+'_start']=available.map(r=>r.valid).filter(Boolean).sort()[0]??null;
      result[metric+'_end']=available.map(r=>r.valid).filter(Boolean).sort().at(-1)??null;
    }
    for(const field of ['N1','N2','T']){
      const available=rows.filter(r=>Number.isFinite(r[field]));
      if(available.length)result[field]=available.reduce((v,r)=>v+r[field],0);
    }
    if(rows[0].grade)result.grade=rows[0].grade;
    return result;
  }
  function metricCoverage(rows,metric){
    const times=rows.filter(r=>eligible(r,metric)).map(r=>r.valid).filter(Boolean).sort();
    return times.length?`新指标实际覆盖：${times[0]} — ${times.at(-1)}（按有效计数合并）。`:'—：所选范围没有该指标所需的新计数；历史源数据可能已清理。';
  }
  function formatMetric(value,metric,digits=3){return Number.isFinite(value)?percentMetrics.includes(metric)?(value*100).toFixed(1)+'%':value.toFixed(digits):'—';}
  function calendarWindow(days,now=Date.now()){
    const end=new Date(Number(now)+8*3600000).toISOString().slice(0,10);
    const first=new Date(end+'T00:00:00Z');first.setUTCDate(first.getUTCDate()-Number(days)+1);
    return {start:first.toISOString().slice(0,10),end};
  }
  const timeKey=r=>JSON.stringify([r.lead,r.valid]);
  const sampleKey=(lead,valid,threshold)=>JSON.stringify([lead,valid,threshold??null]);
  function commonTimes(rows,models,options={}){
    if(options.metric)rows=rows.filter(r=>eligible(r,options.metric));
    const selected=new Set(models);
    if(!selected.size)return [];
    const groups=new Map(),leads=[...new Set(rows.map(r=>r.lead))];
    for(const r of rows){
      if(!selected.has(r.model))continue;
      const key=sampleKey(r.lead,r.valid, r.grade?'grade:'+r.grade:r.threshold);
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
      ?leads.every(lead=>complete(sampleKey(lead,r.valid, r.grade?'grade:'+r.grade:r.threshold)))
      :complete(sampleKey(r.lead,r.valid, r.grade?'grade:'+r.grade:r.threshold))));
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
    if(rows.some(r=>r.grade)&&!keys.includes('grade'))keys=[...keys,'grade'];
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
    const hidden=['GDAS 分析场'];  // GDAS 暂不展示；清空此数组即可恢复
    const datasets=input.datasets.filter(d=>!d.pilot&&d.source!=='comparison'&&!hidden.some(h=>String(d.reference||"").startsWith(h))),ids=new Set(datasets.map(d=>d.id));
    const hasRows=Array.isArray(input.rows);
    const rows=(input.rows||[]).filter(r=>ids.has(r.dataset)),groups=new Map(),extraSets=[],extraRows=[];
    const uniq=items=>[...new Set(items)];
    for(const d of datasets){
      if(!['hk','weekly','lead'].includes(d.source))continue;
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
        const key=JSON.stringify([r.model,r.run,r.lead,r.valid,r.threshold??null,r.grade??null]),old=dedup.get(key);
        if(!old||(r.created??0)>(old.created??0))dedup.set(key,r);
      }
      const items=[...dedup.values()];if(hasRows&&!items.length)continue;
      const first=sets[0],source_ids=sets.map(d=>d.id).sort(),id='compare:'+source_ids.join('+');
      const supported_models=uniq(sets.flatMap(d=>d.supported_models||d.models));
      const models=supported_models.filter(m=>hasRows?items.some(r=>r.model===m):sets.some(d=>d.models.includes(m)));
      const times=uniq(hasRows?items.map(r=>r.valid):sets.flatMap(d=>d.times||[d.start,d.end]).filter(Boolean)).sort();
      extraSets.push({...first,id,source:'comparison',source_ids,supported_models,models,model_issues:{},
        has_n:hasRows?items.some(r=>Number.isFinite(r.n)):sets.some(d=>d.score_has_n??d.has_n),start:times[0],end:times.at(-1),times,
        score_metrics:uniq(sets.flatMap(d=>d.score_metrics||[])),
        has_acc:sets.some(d=>d.has_acc),has_trace:sets.some(d=>d.has_trace),grades:sets.find(d=>d.grades?.length)?.grades,
        leads:uniq(hasRows?items.map(r=>r.lead):sets.flatMap(d=>d.leads)).sort((a,b)=>a-b),
        thresholds:uniq((hasRows?items.map(r=>r.threshold):sets.flatMap(d=>d.thresholds)).filter(t=>t!==null&&t!==undefined)).sort((a,b)=>a-b),
        updated:sets.map(d=>d.updated).sort().at(-1),
        source_note:[...uniq(sets.filter(d=>d.source==='hk').map(d=>d.source_note).filter(Boolean)),'其他模式使用原 GRIB 评分。同一时次的原 GRIB 记录优先使用逐时次汇总批次，整批选取，不补拼缺失模式。共同有效时间不等于已验证严格共同格点；各模式保留原评分与来源。'].join(' ')});
      for(const r of items)extraRows.push({...r,dataset:id,source_dataset:r.dataset});
    }
    return {...input,datasets:[...extraSets,...datasets],...(hasRows?{rows:[...rows,...extraRows]}:{})};
  }
  function unifiedCatalog(input){
    if(input.origins&&input.datasets.every(d=>d.source==='unified'&&!d.pilot))return input;
    const prepared=comparisonCatalog(input);
    const origins=prepared.datasets.filter(d=>d.source!=='comparison');
    const combined=prepared.datasets.filter(d=>d.source==='comparison');
    const covered=new Set(combined.flatMap(d=>d.source_ids));
    const groups=[...combined,...origins.filter(d=>!covered.has(d.id))];
    const datasets=[],rows=[];
    for(const d of groups){
      // Identity follows scientific conditions, so a channel appearing or
      // disappearing does not reset the user's selected period or models.
      const id='unified:'+JSON.stringify([d.kind,d.accumulation??null,d.level??null,d.reference,d.region,...(d.kind==='地面'?[d.element]:[])]);
      const items=(prepared.rows||[]).filter(r=>r.dataset===d.id),source_ids=d.source_ids||[d.id];
      datasets.push({...d,id,original_source:d.source,source:'unified',source_ids,aliases:[d.id,...source_ids],
        has_n:Array.isArray(input.rows)?items.some(r=>Number.isFinite(r.n)):(d.source==='comparison'?d.has_n:(d.score_has_n??d.has_n)),
        source_note:d.source==='analysis'?d.source_note:[d.source_note,'资料自动整合并去重，同一时次保留完整计算批次，不补拼缺失模式。共同有效时间不等于已验证完全相同的格点；各模式保留原始评分。'].filter(Boolean).join(' ')});
      for(const r of items)rows.push({...r,dataset:id,source_dataset:r.source_dataset||r.dataset});
    }
    return {...input,datasets,...(Array.isArray(input.rows)?{rows}:{}),origins};
  }
  function createRowsLoader(input,request){
    const sources=new Map(),datasets=new Map();
    const source=id=>{
      if(!sources.has(id)){
        const pending=Promise.resolve().then(()=>request('rows?'+new URLSearchParams({dataset:id}))).then(value=>{
          if(!Array.isArray(value.rows)||value.dataset!==id||value.rows.some(r=>r.dataset!==id)
            ||value.snapshot_id&&value.snapshot_id!==input.snapshot_id)throw Error('检验资料暂时无法读取，请稍后点击“刷新资料”重试。');
          return value.rows;
        }).catch(error=>{sources.delete(id);throw error;});
        sources.set(id,pending);
      }
      return sources.get(id);
    };
    return {load(dataset){
      if(!datasets.has(dataset.id)){
        const wanted=new Set(dataset.source_ids||[dataset.id]);
        // Match original source order as well as within-source row order, so
        // existing floating-point reducers and chart ordering stay exact.
        const ids=input.datasets.filter(d=>wanted.has(d.id)).map(d=>d.id);
        const pending=Promise.all(ids.map(source)).then(parts=>unifiedCatalog({...input,
          datasets:input.datasets.filter(d=>ids.includes(d.id)),rows:parts.flat()}))
          .catch(error=>{datasets.delete(dataset.id);throw error;});
        datasets.set(dataset.id,pending);
      }
      return datasets.get(dataset.id);
    }};
  }
  function circulationMetrics(field){return field==='wind'?['VRMSE']:['RMSE','ME','MAE','CC'];}
  function circulationHasScore(row,field){return circulationMetrics(field).some(metric=>Number.isFinite(row[metric]));}
  function demoCatalog(input,profiles,sourceModel){
    // Add selector metadata only; source rows and saved scoring archives stay real.
    const datasets=input.datasets.map(d=>{
      const available=d.kind!=='环流'&&d.models.includes(sourceModel),aliases=profiles.map(p=>p.id);
      return {...d,models:[...d.models,...(available?aliases:[])],
        supported_models:[...new Set([...(d.supported_models||d.models),...aliases])],
        model_issues:{...d.model_issues,...Object.fromEntries(profiles.filter(()=>!available).map(p=>[p.id,
          d.kind==='环流'?'环流检验不提供WN3演示资料':d.model_issues?.[sourceModel]||'WN3暂无本要素的实况配对']))}};
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
  const api={percentMetrics,guifanLabels,guifanHelp,eligible,metricsFor,metricCoverage,formatMetric,forecastCoverage,calendarWindow,score,commonTimes,sampleCoverage,grouped,csv,comparisonCatalog,unifiedCatalog,createRowsLoader,circulationMetrics,circulationHasScore,demoCatalog,demoRows};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.VerificationCore=api;
})(typeof globalThis!=='undefined'?globalThis:this);
