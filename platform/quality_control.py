"""Read-only, bounded quality checks on published forecast and observation caches.

HTTP callers receive a cached report. Refresh runs this file in a separate worker;
no downloader, adapter or scoring code is invoked and no source file is changed.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse, json, math, os, pickle, re, subprocess, sys, tempfile, threading, time, zipfile

UTC = timezone.utc
BJT = timezone(timedelta(hours=8))
APP = Path(os.getenv('QC_APP_ROOT', '/home/chengyang/ec-gfs-server'))
WEATHER = Path(os.getenv('QC_WEATHER_ROOT', '/home/chengyang/gods-eye-weather/weather-data'))
CACHE = Path(os.getenv('QC_CACHE_ROOT', str(Path(__file__).parent/'cache/quality-control')))
MODELS = {'ecmwf':'ECMWF-IFS', 'gfs':'NCEP-GFS', 'aifs':'ECMWF-AIFS', 'aigfs':'NCEP-AIGFS'}
WN3 = 'Google WeatherNext 3（集合均值）'
WN2 = 'Google WeatherNext 2（集合均值）'
NPZ_LIMIT = 8_000_000
JSON_LIMIT = 2_000_000
CELL_LIMIT = 20_000
SCAN_SECONDS = 120
TTL = 300
RULE_REVISION = 'seasonal-layer-v1'
# Converted cache units are verified from their writers. Bounds are review alerts,
# not an assertion that a rare extreme is impossible.
DEFAULT_RULES = {
    'temperature': {'unit':'degC','min':-100.,'max':70.},
    'rain': {'unit':'mm','min':-.1,'max':2000.},
    'humidity': {'unit':'%','min':-.1,'max':100.1},
    'wind': {'unit':'m/s','min':-250.,'max':250.},
    'wind_speed': {'unit':'m/s','min':0.,'max':250.},
    'wind_direction': {'unit':'deg','min':0.,'max':360.},
    'pressure': {'unit':'hPa','min':650.,'max':1150.},
    'height': {'unit':'m','min':-500.,'max':35000.},
    'rain_windows': {'1':350.,'2':700.,'3':1000.,'6':1500.,'12':1800.,'24':2000.},
    'temperature_jump_per_hour': 20.,
    # Conservative trial review values, not climatological percentiles or official limits.
    'seasonal': {
        'bounds':[108.,18.,120.,27.],
        'profiles': {
            'winter': {'label':'冬季','months':[12,1,2], 'temperature':{'min':-20.,'max':40.},
                       'rain_windows':{'1':80.,'2':140.,'3':180.,'6':250.,'12':350.,'24':500.}},
            'spring': {'label':'春季','months':[3,4,5], 'temperature':{'min':-10.,'max':45.},
                       'rain_windows':{'1':140.,'2':220.,'3':300.,'6':450.,'12':600.,'24':800.}},
            'summer': {'label':'夏季','months':[6,7,8], 'temperature':{'min':0.,'max':50.},
                       'rain_windows':{'1':200.,'2':320.,'3':450.,'6':650.,'12':900.,'24':1200.}},
            'autumn': {'label':'秋季','months':[9,10,11], 'temperature':{'min':-10.,'max':45.},
                       'rain_windows':{'1':160.,'2':260.,'3':360.,'6':550.,'12':750.,'24':1000.}},
        },
        'monthly_overrides':{},
    },
    'wind_levels': {'surface':60.,'1000':80.,'950':80.,'925':80.,'900':85.,'850':90.,
                    '700':100.,'600':110.,'500':120.,'400':140.,'300':160.,'250':180.,
                    '200':180.,'150':180.,'100':170.},
}
_lock = threading.Lock()
_process = None
_job = {}


class ScopeLimit(ValueError):
    """File exceeds this scan's bounded scope; not evidence of corrupt data."""


def stamp(value, naive_utc=False):
    d = datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if d.tzinfo is None:
        if not naive_utc: raise ValueError('timezone')
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


def times(d, prefix):
    return {prefix+'_utc': d.astimezone(UTC).isoformat(), prefix+'_bjt':d.astimezone(BJT).isoformat()}


def load_json(path):
    if path.stat().st_size > JSON_LIMIT: raise ValueError('bounded JSON')
    d = json.loads(path.read_text())
    if not isinstance(d, dict): raise ValueError('object')
    return d


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.quality-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f: json.dump(value, f, ensure_ascii=False, allow_nan=False)
        os.replace(name, path)
    finally:
        try: os.unlink(name)
        except FileNotFoundError: pass


def rules_for(cache):
    rules = json.loads(json.dumps(DEFAULT_RULES))
    p = cache/'rules.json'
    if not p.exists(): return rules
    try:
        supplied = load_json(p)
        for name, val in supplied.items():
            if name=='seasonal' and isinstance(val,dict):
                profiles=val.get('profiles',{});profiles=profiles if isinstance(profiles,dict) else {}
                monthly=val.get('monthly_overrides',{});monthly=monthly if isinstance(monthly,dict) else {}
                for key,profile in profiles.items():
                    if key in rules[name]['profiles']:
                        update_profile(rules[name]['profiles'][key],profile,rules)
                for month,profile in monthly.items():
                    if str(month) in {str(m) for m in range(1,13)} and isinstance(profile,dict):
                        clean={};update_profile(clean,profile,rules)
                        if clean: rules[name]['monthly_overrides'][str(month)]=clean
            elif name=='wind_levels' and isinstance(val,dict):
                for level,upper in val.items():
                    if level in rules[name] and type(upper) in (int,float) and math.isfinite(upper) and 0<upper<=250:
                        rules[name][level]=float(upper)
            elif name == 'temperature_jump_per_hour' and type(val) in (int,float) and 5 <= val <= 100:
                rules[name] = float(val)
            elif name=='rain_windows' and isinstance(val,dict):
                for window,upper in val.items():
                    if window in rules[name] and type(upper) in (int,float) and math.isfinite(upper) and upper>0:
                        rules[name][window]=float(upper)
            elif name in rules and isinstance(val,dict) and isinstance(rules[name],dict) and 'unit' in rules[name] and val.get('unit')==rules[name]['unit']:
                lo,hi = float(val['min']),float(val['max'])
                if math.isfinite(lo) and math.isfinite(hi) and lo < hi: rules[name].update(min=lo,max=hi)
    except (ValueError,TypeError,KeyError,OSError): pass
    for key,profile in rules['seasonal']['profiles'].items():
        rain=profile['rain_windows'];ordered=[rain[w] for w in ('1','2','3','6','12','24')]
        if any(a>b for a,b in zip(ordered,ordered[1:])):
            profile['rain_windows']=dict(DEFAULT_RULES['seasonal']['profiles'][key]['rain_windows'])
    for month,override in rules['seasonal']['monthly_overrides'].items():
        base=next(p for p in rules['seasonal']['profiles'].values() if int(month) in p['months'])
        rain={**base['rain_windows'],**override.get('rain_windows',{})};ordered=[rain[w] for w in ('1','2','3','6','12','24')]
        if any(a>b for a,b in zip(ordered,ordered[1:])):override.pop('rain_windows',None)
    return rules


def update_profile(target, supplied, rules):
    if not isinstance(supplied,dict): return
    t=supplied.get('temperature')
    if isinstance(t,dict) and all(type(t.get(k)) in (int,float) and math.isfinite(t[k]) for k in ('min','max')):
        if rules['temperature']['min'] <= t['min'] < t['max'] <= rules['temperature']['max']:
            target['temperature']={k:float(t[k]) for k in ('min','max')}
    rain=supplied.get('rain_windows')
    if isinstance(rain,dict):
        for window,upper in rain.items():
            if (window in rules['rain_windows'] and type(upper) in (int,float)
                and math.isfinite(upper) and 0<upper<=rules['rain_windows'][window]):
                target.setdefault('rain_windows',{})[window]=float(upper)


def seasonal_profile(valid, rules):
    month=valid.astimezone(BJT).month
    for key,profile in rules['seasonal']['profiles'].items():
        if month in profile['months']:
            result=json.loads(json.dumps(profile));override=rules['seasonal']['monthly_overrides'].get(str(month),{})
            if 'temperature' in override: result['temperature']=override['temperature']
            result['rain_windows'].update(override.get('rain_windows',{}))
            result.update(id=key,month=month,label=profile['label']+(f' / {month}月覆盖' if override else ''))
            return result


def field_level(field):
    if field in ('WSPD','T','temperature_2m'): return 'surface'
    match=re.fullmatch(r'(surface|\d{2,4})_(?:t|u|v|wind_speed)',field)
    return match[1] if match else None


def source(id, name):
    return {'id':id,'name':name,'kind':'actual','arrival':{},
            'checks':{'sampled_files':0,'sampled_cells':0,'total_cells':0,'metadata_frames':0},
            'metrics':[],'issues':[]}


def issue(s, severity, code, message, run=None, lead=None, field=None):
    row = {'severity':severity,'code':code,'model':s['name'],'message':message}
    if run is not None: row.update(times(run,'run'))
    if lead is not None: row['lead_hours'] = lead
    if field is not None: row['field'] = field
    if len(s['issues']) < 80: s['issues'].append(row)


def freshness(s, run, now, threshold):
    age = max(0., (now-run).total_seconds()/3600)
    s['arrival'].update(times(run,'latest_run'))
    s['arrival']['age_hours'] = round(age,1)
    s['arrival']['warning_age_hours'] = threshold
    if run > now+timedelta(minutes=5):
        issue(s,'error','future_run','起报时间在当前时刻之后，请检查时区和时钟。',run)
        s['arrival'].update(status='invalid',label='时间异常')
    elif age > threshold:
        s['arrival'].update(status='delayed',label='到报偏旧')
        issue(s,'warning','old_run',f'最新资料的起报或观测时间距检查时已有 {age:.1f} 小时，超过平台设定的 {threshold} 小时更新提醒时限，请核查资料发布及下载进度。',run)


def axes(x, y):
    import numpy as np
    x,y = np.asarray(x,float),np.asarray(y,float)
    if x.ndim!=1 or y.ndim!=1 or len(x)<2 or len(y)<2: raise ValueError('axis shape')
    if not np.isfinite(x).all() or not np.isfinite(y).all(): raise ValueError('axis finite')
    if not (np.diff(x)>0).all() or not (np.diff(y)>0).all(): raise ValueError('axis ordering')
    if x.min() < -180 or x.max() > 360 or y.min() < -90 or y.max() > 90: raise ValueError('coordinates')
    if not np.allclose(np.diff(x),np.diff(x)[0],atol=1e-6) or not np.allclose(np.diff(y),np.diff(y)[0],atol=1e-6):
        raise ValueError('axis spacing')
    return x,y


def sample_indices(a):
    import numpy as np
    # Regularly spaced deterministic sample with endpoints, no huge flatten copy.
    if a.ndim == 1:
        idx = np.linspace(0,len(a)-1,min(a.size,CELL_LIMIT),dtype=int)
        return (idx,)
    if a.ndim != 2: raise ValueError('field dimensions')
    if a.size <= CELL_LIMIT: return np.arange(a.shape[0]),np.arange(a.shape[1])
    ny = min(a.shape[0],max(2,int(math.sqrt(CELL_LIMIT*a.shape[0]/a.shape[1]))))
    nx = min(a.shape[1],max(2,CELL_LIMIT//ny))
    iy = np.linspace(0,a.shape[0]-1,ny,dtype=int)
    ix = np.linspace(0,a.shape[1]-1,nx,dtype=int)
    return iy,ix


def sample_array(a):
    import numpy as np
    indices=sample_indices(a)
    return np.asarray(a[indices[0]] if a.ndim==1 else a[np.ix_(*indices)],float).reshape(-1)


def seasonal_mask(a, grid, in_region, rules):
    import numpy as np
    if in_region: return np.ones(len(sample_array(a)),dtype=bool)
    if grid is None: return None
    x,y=grid
    if a.ndim!=2 or a.shape!=(len(y),len(x)): raise ValueError('seasonal grid')
    iy,ix=sample_indices(a);west,south,east,north=rules['seasonal']['bounds']
    return ((y[iy,None]>=south)&(y[iy,None]<=north)&(x[None,ix]>=west)&(x[None,ix]<=east)).reshape(-1)


def accumulation_window(field, accumulation_hours=None):
    if accumulation_hours is not None: return accumulation_hours
    match=re.fullmatch(r'(?:rain(\d+)h|RAIN(\d+)H)',field)
    return int(match[1] or match[2]) if match else None


def field_rule(field, rules, accumulation_hours=None):
    if field.endswith('_t') or field in ('temperature_2m','T'): return rules['temperature']
    if field.startswith('rain') or field in ('precipitation','RAIN01H','RAIN02H','RAIN03H','RAIN06H','RAIN24H'):
        rule=dict(rules['rain']);window=accumulation_window(field,accumulation_hours)
        if str(window) in rules['rain_windows']: rule['max']=rules['rain_windows'][str(window)]
        return rule
    if field.endswith('_rh') or field in ('RH','RH2M'): return rules['humidity']
    if field.endswith(('_u','_v')): return rules['wind']
    if field=='WSPD' or field.endswith('_wind_speed'): return rules['wind_speed']
    if field=='WDIR': return rules['wind_direction']
    if field.endswith('_msl'): return rules['pressure']
    if field.endswith('_gh'): return rules['height']
    return None


def values_check(s, a, field, run, lead, rules, unit=None, accumulation_hours=None,
                 grid=None, in_region=False, count_file=True):
    import numpy as np
    if a.dtype.kind not in 'fiu' or a.dtype.hasobject or not a.size: raise ValueError('numeric data')
    v = sample_array(a); finite = np.isfinite(v); rule = field_rule(field,rules,accumulation_hours)
    expected_unit = rule['unit'] if rule else unit
    if unit is not None and expected_unit is not None and unit != expected_unit:
        issue(s,'error','unit_mismatch','数据单位与预期单位不一致，暂未开展数值范围检查。',run,lead,field)
        return v
    lo = float(v[finite].min()) if finite.any() else None
    hi = float(v[finite].max()) if finite.any() else None
    broad_bad=finite & ((v<rule['min']) | (v>rule['max'])) if rule else np.zeros(len(v),dtype=bool)
    outside=int(broad_bad.sum())
    missing = int(np.sum(~finite))
    metric = dict(field=field,lead_hours=lead,unit=expected_unit,min=lo,max=hi,
                  nonfinite_count=missing,range_count=outside,checked_cells=len(v),total_cells=int(a.size),
                  coverage_fraction=round(len(v)/a.size,4))
    window=accumulation_window(field,accumulation_hours)
    if window is not None: metric['accumulation_hours']=window
    if rule: metric.update(review_min=rule['min'],review_max=rule['max'])
    if run is not None: metric.update(times(run,'run'))
    if run is not None and type(lead) in (int,float):
        valid=run+timedelta(hours=lead);metric.update(times(valid,'valid'))
        profile=seasonal_profile(valid,rules)
        surface_temperature=field in ('surface_t','temperature_2m','T')
        window=accumulation_window(field,accumulation_hours)
        rain=rule is not None and rule['unit']=='mm' and str(window) in profile['rain_windows']
        if surface_temperature or rain:
            mask=seasonal_mask(a,grid,in_region,rules)
            if mask is not None and mask.any():
                contextual=dict(profile['temperature']) if surface_temperature else {'min':rules['rain']['min'],'max':profile['rain_windows'][str(window)]}
                context_label=profile['label']
                if rain:
                    start_profile=seasonal_profile(valid-timedelta(hours=window),rules)
                    contextual['max']=max(contextual['max'],start_profile['rain_windows'][str(window)])
                    if start_profile['month']!=profile['month']:
                        context_label=f'{start_profile["label"]}→{profile["label"]}（跨月累计取较宽上限）'
                contextual['min']=max(contextual['min'],rule['min'])
                contextual['max']=min(contextual['max'],rule['max'])
                context_bad=finite & mask & ~broad_bad & ((v<contextual['min'])|(v>contextual['max']))
                metric.update(review_min=contextual['min'],review_max=contextual['max'],
                    general_min=rule['min'],general_max=rule['max'],context_range_count=int(context_bad.sum()),
                    context_checked_cells=int(mask.sum()),review_context=f'{context_label} · 评分区季节复核',
                    review_scope='108–120°E / 18–27°N；其他地区采用通用范围',season=profile['id'],valid_month_bjt=profile['month'])
                metric['range_count']+=int(context_bad.sum())
                if context_bad.any():
                    issue(s,'warning','seasonal_range',f'{context_label}评分区抽查有 {int(context_bad.sum())} 个值超出试行复核范围 [{contextual["min"]:g}, {contextual["max"]:g}] {rule["unit"]}；按有效时间北京时间选季节，极端天气仍需人工核实。',run,lead,field)
    level=field_level(field)
    if field=='WSPD' or field.endswith('_wind_speed'):
        maximum=rules['wind_levels'].get(level)
        if maximum is not None:
            maximum=min(maximum,rule['max'])
            contextual_bad=finite & ~broad_bad & (v>maximum)
            label='地面10米风' if level=='surface' else f'{level} hPa风'
            metric.update(review_min=0.,review_max=maximum,general_min=rule['min'],general_max=rule['max'],
                context_range_count=int(contextual_bad.sum()),context_checked_cells=len(v),level=level,
                review_context=f'{label} · 分层风速复核',review_scope='按已确认高度层；单位 m/s')
            metric['range_count']+=int(contextual_bad.sum())
            if contextual_bad.any(): issue(s,'warning','wind_level_range',f'{label}抽查有 {int(contextual_bad.sum())} 个合成/实况或合成风速超过试行上限 {maximum:g} m/s；高空急流和台风大风需人工复核。',run,lead,field)
        else:
            metric['review_context']='高度层未知，采用通用范围'
    if not count_file: metric.update(derived=True,derived_from=[f'{level}_u',f'{level}_v'])
    s['metrics'].append(metric)
    if count_file:
        s['checks']['sampled_files'] += 1
        s['checks']['sampled_cells'] += len(v)
        s['checks']['total_cells'] += int(a.size)
    if missing:
        issue(s,'error' if missing==len(v) else 'warning','nonfinite',f'抽查 {len(v)} 个值，发现 {missing} 个缺测或无效值（{missing/len(v):.1%}）。',run,lead,field)
    if outside:
        issue(s,'warning','physical_range',f'抽查有 {outside} 个值超出提醒范围 [{rule["min"]:g}, {rule["max"]:g}] {rule["unit"]}；保留原值待人工复核。',run,lead,field)
    return v


def paired_wind_check(s, u, v, level, run, lead, rules, total_cells=None):
    import numpy as np
    if u.shape!=v.shape: raise ValueError('wind pair shape')
    # Both vectors were sampled at identical indices from the same validated frame.
    values_check(s,np.hypot(u,v),f'{level}_wind_speed',run,lead,rules,'m/s',count_file=False)
    if total_cells is not None:
        metric=s['metrics'][-1];metric['total_cells']=int(total_cells)
        metric['coverage_fraction']=round(len(u)/total_cells,4)


def bounded_npz(path):
    import numpy as np
    if path.stat().st_size > NPZ_LIMIT: raise ScopeLimit('compressed size')
    with zipfile.ZipFile(path) as z:
        entries = z.infolist()
        if len(entries)>12: raise ValueError('archive members')
        if sum(i.file_size for i in entries)>NPZ_LIMIT: raise ScopeLimit('expanded size')
        # Reject forged NPY shapes before np.load can allocate claimed dimensions.
        for entry in entries:
            if not entry.filename.endswith('.npy'): raise ValueError('archive member')
            with z.open(entry) as f:
                version=np.lib.format.read_magic(f)
                if version==(1,0): shape,_,dtype=np.lib.format.read_array_header_1_0(f)
                elif version==(2,0): shape,_,dtype=np.lib.format.read_array_header_2_0(f)
                else: raise ValueError('header version')
                size=math.prod(shape)*dtype.itemsize
                if size>NPZ_LIMIT: raise ScopeLimit('array allocation size')
                if dtype.hasobject or len(shape)>2 or f.tell()+size!=entry.file_size:
                    raise ValueError('bounded array shape')
    return np.load(path,allow_pickle=False)


def native_array(path, expected_shape):
    import numpy as np
    # Cold drvfs mmap page faults can take seconds per small field. Read bounded
    # native fields sequentially; retain mmap only for genuinely large arrays.
    size=path.stat().st_size
    with path.open('rb') as handle:
        version=np.lib.format.read_magic(handle)
        if version==(1,0):shape,_,dtype=np.lib.format.read_array_header_1_0(handle)
        elif version==(2,0):shape,_,dtype=np.lib.format.read_array_header_2_0(handle)
        else:raise ValueError('native header version')
        if tuple(shape)!=tuple(expected_shape) or dtype!=np.float32 or dtype.hasobject:
            raise ValueError('native shape/type')
        if handle.tell()+math.prod(shape)*dtype.itemsize!=size:
            raise ValueError('native byte length')
    return np.load(path,mmap_mode='r' if size>NPZ_LIMIT else None,allow_pickle=False)


def validate_frames(s, d, run):
    frames = d.get('frames',[])
    if not isinstance(frames,list) or len(frames)>400: raise ValueError('frame list')
    out = []; previous = -1
    for f in frames:
        if not isinstance(f,dict):
            issue(s,'error','frame_time','目录中有无法解析的预报帧条目。',run)
            continue
        lead=f.get('lead')
        if type(lead) is not int or not 0<=lead<=360 or lead<=previous or stamp(f.get('valid'))!=run+timedelta(hours=lead):
            issue(s,'error','frame_time','目录存在重复、倒序时效，或有效时间与起报＋时效不一致。',run,lead if type(lead) is int else None)
            continue
        previous=lead; out.append(f)
    s['checks']['metadata_frames']=len(frames)
    return out


def native_models(weather, now, rules, deadline, deferred=None):
    import numpy as np
    sources=[]
    for slug,name in MODELS.items():
        s=source(slug,name); sources.append(s); folder=weather/'region-v4'/slug
        source_deadline=min(deadline,time.monotonic()+12) if deferred is not None else deadline
        try:
            d=load_json(folder/'catalog.json'); run=stamp(d['run'])
            if d.get('schema')!=2 or d.get('model')!=name or d.get('legacy'): raise ValueError('identity')
            frames=validate_frames(s,d,run); leads={f['lead'] for f in frames}
            end=max(leads) if leads else 0
            if slug=='ecmwf': expected=set(range(0,min(end,144)+1,3)) | set(range(150,end+1,6))
            else: expected=set(range(0,end+1,6 if slug in ('aifs','aigfs') else 3))
            absent_meta=[lead for lead in sorted(leads) if not (folder/f'{slug}_{run:%Y%m%d%H}_f{lead:03d}_global1.json').is_file()]
            missing=sorted((expected-leads) | set(absent_meta))
            s['arrival'].update(status='partial' if missing or d.get('partial') else 'available',
                label='部分到报' if missing or d.get('partial') else '已到报',available_frames=len(leads)-len(absent_meta),
                expected_frames=len(expected),missing_leads=missing,forecast_horizon_hours=end,
                expected_label='依据本地下载清单检查预报时效，清单外时效暂不评价。',backlog=None)
            if missing: issue(s,'warning','missing_leads','已发布时效范围内存在缺失预报时效。',run)
            if absent_meta: issue(s,'error','committed_metadata_missing',f'目录已提交的 {len(absent_meta)} 个预报帧文件在本地缺失。',run)
            if d.get('errors'): issue(s,'warning','writer_errors',f'下载目录记录 {len(d["errors"])} 个待重试项目。',run)
            freshness(s,run,now,18)
            choices=[f for f in frames if f['lead']>0][:2]
            prev_t=None;prev_lead=None
            for f in choices:
                if time.monotonic()>source_deadline:
                    s['checks']['partial_scan']=True;break
                lead=f['lead']; p=folder/f'{slug}_{run:%Y%m%d%H}_f{lead:03d}_global1.json'
                try:
                    meta=load_json(p)
                    if meta.get('schema')!=2 or meta.get('model')!=name or stamp(meta['run'])!=run or meta.get('lead')!=lead or stamp(meta['valid'])!=run+timedelta(hours=lead):
                        raise ValueError('frame identity')
                    x,y=axes(meta['lon'],meta['lat'])
                    if meta.get('native_grid_degrees')!=.25 or not np.allclose(np.diff(x),.25) or not np.allclose(np.diff(y),.25): raise ValueError('native resolution')
                    if meta.get('storage') not in (slug+'-regional-v1',slug+'-global-v1'): raise ValueError('storage identity')
                    if not np.allclose(meta.get('bounds',[]),[x[0],y[0],x[-1],y[-1]]): raise ValueError('bounds')
                    directory=meta.get('fields_directory',p.stem+'.fields')
                    if directory not in (p.stem+'.fields',p.stem+'.score-fields'): raise ValueError('field directory')
                    field_keys=['surface_t','surface_msl']
                    if d.get('capabilities',{}).get('surface_rh',True): field_keys.append('surface_rh')
                    if '850' in meta.get('levels',[]): field_keys+=['850_t','850_rh','850_gh']
                    levels=['surface']+[str(level) for level in meta.get('levels',[]) if re.fullmatch(r'\d{2,4}',str(level)) and 0<int(level)<=1100]
                    interval=meta.get('interval_hours',3)
                    if type(interval) is not int or interval not in (1,3,6): raise ValueError('interval')
                    rain=f'rain{interval}h';field_keys.append(rain)
                    if deferred is None:
                        for level in dict.fromkeys(levels):field_keys += [f'{level}_u',f'{level}_v']
                    else:
                        # Core weather first; cold high-level files must not starve later sources.
                        field_keys=['surface_t',rain,'surface_u','surface_v',*field_keys[1:-1]]
                        if '850' in levels:field_keys.append('850_u')
                    if stamp(meta['rain_start'])!=run+timedelta(hours=lead-interval): raise ValueError('rain interval')
                    wind_samples={}
                    for field in dict.fromkeys(field_keys):
                        if time.monotonic()>source_deadline:
                            s['checks']['partial_scan']=True;break
                        try:
                            a=native_array(folder/directory/(field+'.npy'),(len(y),len(x)))
                            if a.shape!=(len(y),len(x)) or a.dtype!=np.float32: raise ValueError('shape/type')
                            v=values_check(s,a,field,run,lead,rules,grid=(x,y))
                            if field.endswith(('_u','_v')):
                                level=field_level(field);pair=wind_samples.setdefault(level,{})
                                pair[field.rsplit('_',1)[-1]]=v
                                if set(pair)=={'u','v'}:
                                    paired_wind_check(s,pair['u'],pair['v'],level,run,lead,rules,a.size)
                                    del wind_samples[level]
                            if field=='surface_t':
                                if prev_t is not None and len(v)==len(prev_t):
                                    jump=np.isfinite(v)&np.isfinite(prev_t)&(np.abs(v-prev_t)>rules['temperature_jump_per_hour']*(lead-prev_lead))
                                    if jump.any(): issue(s,'warning','temperature_jump',f'相邻时效气温抽样有 {int(jump.sum())} 点每小时变化超过 {rules["temperature_jump_per_hour"]:g}°C。',run,lead,field)
                                prev_t=v.copy();prev_lead=lead
                            del a
                        except (OSError,ValueError,KeyError,EOFError):
                            issue(s,'error','field_invalid','已发布字段缺失、损坏或维度不匹配。',run,lead,field)
                    if deferred is not None and f is choices[0]:
                        deferred.append({'source':s,'folder':folder/directory,'grid':(x,y),
                            'run':run,'lead':lead,'levels':list(dict.fromkeys(levels[1:])),
                            'sampled':wind_samples,'total_cells':len(x)*len(y)})
                except (OSError,ValueError,TypeError,KeyError):
                    issue(s,'error','metadata_invalid','抽查帧的元数据、网格、身份或时间区间未通过校验。',run,lead)
        except (OSError,ValueError,TypeError,KeyError):
            s['arrival'].update(status='unavailable',label='资料清单暂不可用',expected_label='本地未读取到资料，不据此认定资料提供方缺报。',backlog=None)
            issue(s,'error','catalog_invalid','本地发布目录缺失或无法验证。')
        if s['checks'].get('partial_scan'):
            issue(s,'info','core_scan_time_limit','该模式读取达到本轮时限，部分要素尚未检查，可稍后重新检查。')
    return sources


def deferred_pressure_winds(jobs, rules, deadline):
    import numpy as np
    for job in jobs:
        s=job['source'];limit=min(deadline,time.monotonic()+10)
        # Preserve 850 hPa and jet-level coverage first; other declared layers rotate.
        levels=job['levels'];anchor=[p for p in ('850','300') if p in levels]
        rest=[p for p in levels if p not in anchor]
        if rest:
            offset=int(time.time()//TTL)%len(rest);rest=rest[offset:]+rest[:offset]
        finished=[]
        for level in anchor+rest:
            if time.monotonic()>limit:break
            pair=job['sampled'].get(level,{})
            for component in ('u','v'):
                if component in pair:continue
                if time.monotonic()>limit:break
                field=f'{level}_{component}'
                try:
                    x,y=job['grid']
                    a=native_array(job['folder']/(field+'.npy'),(len(y),len(x)))
                    if a.shape!=(len(y),len(x)) or a.dtype!=np.float32:raise ValueError('wind shape/type')
                    pair[component]=values_check(s,a,field,job['run'],job['lead'],rules)
                    del a
                except (OSError,ValueError,KeyError,EOFError):
                    issue(s,'error','field_invalid','已发布高空风分量缺失、损坏或维度不匹配。',job['run'],job['lead'],field)
            if set(pair)=={'u','v'}:
                paired_wind_check(s,pair['u'],pair['v'],level,job['run'],job['lead'],rules,job['total_cells'])
                finished.append(level)
        s['checks']['pressure_wind_layers']=finished
        if len(finished)<len(levels):
            issue(s,'info','upper_wind_sample_scope',f'本轮高空风已检查 {len(finished)}/{len(levels)} 个已接入气压层；其余层本轮未检查。')


def wn3_source(weather, now, rules, deadline):
    import numpy as np
    s=source('weathernext3',WN3); root=weather/'region-v4/weathernext3'
    known={'native-score-v1':([108.,18.,120.,27.],'weathernext3-native-score-v1'),
           'native-southern-china-v1':([100.,10.,130.,35.],'weathernext3-native-southern-china-v1'),
           'native-china-v1':([60.,0.,150.,60.],'weathernext3-native-china-v1')}
    try:
        # catalog may lag a preparing run; probe 24 exact hourly manifests only.
        candidates=[]
        for age in range(24):
            run=(now-timedelta(hours=age)).replace(minute=0,second=0,microsecond=0)
            p=root/f'run_{run:%Y%m%d%H}.json'
            if p.exists(): candidates.append((p,run)); break
        if not candidates:
            published=load_json(root/'catalog.json');older_run=stamp(published['run'])
            candidates.append((root/f'run_{older_run:%Y%m%d%H}.json',older_run))
        p,run=candidates[0]; d=load_json(p); directory=d.get('native_cache_directory')
        if directory not in known or d.get('model')!=WN3 or d.get('schema')!=2 or stamp(d['run'])!=run or d.get('native_grid_degrees')!=.1 or d.get('statistic')!='mean' or d.get('interval_hours')!=1 or d.get('bounds')!=known[directory][0] or d.get('status') not in ('preparing','complete'): raise ValueError('identity')
        frames=validate_frames(s,d,run); leads={f['lead'] for f in frames}
        horizon=d.get('forecast_horizon_hours')
        if type(horizon) is not int or not 1<=horizon<=360: raise ValueError('horizon')
        missing=sorted(set(range(1,horizon+1))-leads)
        absent_fields=[lead for lead in sorted(leads) if not all((root/directory/f'{run:%Y%m%d%H}'/f'f{lead:03d}_{key}.npz').is_file() for key in ('surface_t','rain1h'))]
        missing=sorted(set(missing) | set(absent_fields))
        s['arrival'].update(status='preparing' if d['status']=='preparing' else ('partial' if missing else 'available'),
            label='正在补齐' if d['status']=='preparing' else ('资料尚未齐全' if missing else '已到报'),
            available_frames=len(leads)-len(absent_fields),expected_frames=horizon,missing_leads=missing,
            forecast_horizon_hours=horizon,expected_label='按已提交下载清单检查，尚未完成的后续时效标记为待下载。',backlog=None)
        freshness(s,run,now,12)
        if d['status']=='complete' and missing: issue(s,'error','incomplete_commit','标记完成的 WN3 批次仍有缺失时效。',run)
        if absent_fields: issue(s,'error','committed_field_missing',f'WN3 清单已提交的 {len(absent_fields)} 个小时缺少气温或小时降水原生文件。',run)
        for lead in sorted(leads)[:2]:
            for field,var,unit in (('surface_t','temperature_2m_mean','degC'),('rain1h','total_precipitation_1hr_mean','mm')):
                if time.monotonic()>deadline: break
                try:
                    with bounded_npz(root/directory/f'{run:%Y%m%d%H}'/f'f{lead:03d}_{field}.npz') as z:
                        meta=json.loads(str(z['meta'].item())); x,y=axes(z['lon'],z['lat']); a=z['values']
                        if (meta.get('schema')!=known[directory][1] or stamp(meta['run'])!=run or type(meta.get('lead')) is not int or meta.get('lead')!=lead or stamp(meta['valid'])!=run+timedelta(hours=lead) or meta.get('field')!=field or meta.get('source_variable')!=var or meta.get('unit')!=unit or meta.get('statistic')!='mean' or meta.get('grid_degrees')!=.1 or meta.get('bounds')!=known[directory][0] or meta.get('source_kind') not in ('earth-engine-region','existing-gcs-offline-cache') or not re.fullmatch('[0-9a-f]{64}',str(meta.get('source_sha256','')))): raise ValueError('identity/unit')
                        bounds=known[directory][0]
                        if (a.shape!=(len(y),len(x)) or a.dtype!=np.float32 or not np.allclose(np.diff(x),.1)
                            or not np.allclose(np.diff(y),.1) or not np.allclose([x[0],y[0],x[-1],y[-1]],bounds)):
                            raise ValueError('native shape')
                        values_check(s,a,field,run,lead,rules,unit,grid=(x,y))
                except ScopeLimit:
                    issue(s,'info','field_not_sampled','该字段超出本轮大小限制，未检查数值；不据此认定数据损坏。',run,lead,field)
                except (OSError,ValueError,TypeError,KeyError,EOFError,RuntimeError,zipfile.BadZipFile):
                    issue(s,'error','field_invalid','WN3 已提交字段缺失、损坏、单位/身份或网格不匹配。',run,lead,field)
    except (OSError,ValueError,TypeError,KeyError):
        s['arrival'].update(status='unavailable',label='资料清单暂不可用',expected_label='仅承认已提交原生资料',backlog=None)
        issue(s,'error','catalog_invalid','WN3 本地提交目录无法验证。')
    try:
        q=load_json(root/'backfill-queue.json'); cutoff=now-timedelta(days=7)
        rows=[]
        for row in q.get('runs',[]):
            if not isinstance(row,dict): continue
            if not re.fullmatch(r'\d{10}',str(row.get('id',''))): continue
            run=datetime.strptime(row['id'],'%Y%m%d%H').replace(tzinfo=UTC)
            if cutoff <= run <= now: rows.append(row)
        statuses={}
        allowed={'complete','queued','running','partial','verification_ready','waiting_hourly','waiting_budget','waiting_download_slot','paused','error','failed'}
        for row in rows:
            state=row.get('status','unknown');state=state if state in allowed else 'unknown'
            statuses[state]=statuses.get(state,0)+1
        # Counters are from the downloader's existing queue, not inferred source gaps.
        pending=[row for row in rows if row.get('status')!='complete' and not (row.get('status')=='verification_ready' and row.get('target_hours')==48)]
        short=[row for row in rows if row.get('verification_ready') is not True and row.get('status')!='complete']
        extended=[row for row in pending if row.get('verification_ready') is True and row.get('target_hours')==360]
        s['arrival']['backlog']={'runs':len(rows),'pending_runs':len(pending),'by_status':statuses,
            'short_pending_runs':len(short),'extended_pending_runs':len(extended),
            'label':f'48 小时检验资料未就绪 {len(short)} 批；360 小时待延长 {len(extended)} 批',
            'window_days':7,'scope':'现有六小时补下载队列（非全部逐小时起报）',
            'updated_utc':stamp(q['updated_utc']).isoformat() if q.get('updated_utc') else None,
            'oldest_run_utc':min((datetime.strptime(r['id'],'%Y%m%d%H').replace(tzinfo=UTC).isoformat() for r in pending),default=None)}
        if short: issue(s,'warning','backfill_short_backlog',f'现有近 7 天补下载队列中有 {len(short)} 个批次的 48 小时检验资料未就绪。')
        if extended: issue(s,'info','backfill_extended_backlog',f'{len(extended)} 个历史批次已有 48 小时检验资料，等待延长到 360 小时；与最新批次到报状态分开统计。')
    except (OSError,ValueError,TypeError,KeyError):
        issue(s,'info','queue_unavailable','补下载队列未读取，无法确定积压数量。')
    return s


def wn2_source(app, now, rules, deadline):
    import numpy as np
    s=source('weathernext2',WN2); root=app/'cache'
    try:
        d=load_json(root/'weathernext2-prefetch-status.json'); run=stamp(d['run_utc'],naive_utc=True)
        end=d.get('target_hours')
        if type(end) is not int or end<6 or end>360 or end%6: raise ValueError('horizon')
        expected={(lead,var) for lead in range(6,end+1,6) for var in ('temperature_2m','precipitation')}
        ready={(r['lead'],r['variable']) for r in d.get('ready',[]) if isinstance(r,dict) and (r.get('lead'),r.get('variable')) in expected}
        absent=[(lead,var) for lead,var in ready if not (root/'WEATHERNEXT2_NATIVE'/f'{run:%Y%m%d%H}_{lead:03d}_{var}.npz').is_file()]
        ready=ready-set(absent)
        missing=sorted(expected-ready)
        s['arrival'].update(status='partial' if missing else 'available',label='部分到报' if missing else '已到报',
            available_frames=len(ready),expected_frames=len(expected),frame_unit='字段文件',
            missing_leads=sorted({r[0] for r in missing}),forecast_horizon_hours=end,
            expected_label='按当前下载计划检查6小时间隔气温及6小时累计降水。',backlog=None)
        freshness(s,run,now,24)
        if absent: issue(s,'error','committed_field_missing',f'WN2 状态已记为完成的 {len(absent)} 个原生字段文件在本地缺失。',run)
        if d.get('errors'): issue(s,'warning','writer_errors',f'WN2 预下载状态记录 {len(d["errors"])} 个待重试字段。',run)
        for lead in sorted({lead for lead,var in ready})[:2]:
            for var in ('temperature_2m','precipitation'):
                if time.monotonic()>deadline: break
                try:
                    with bounded_npz(root/'WEATHERNEXT2_NATIVE'/f'{run:%Y%m%d%H}_{lead:03d}_{var}.npz') as z:
                        if int(z['schema'])!=1: raise ValueError('schema')
                        x,y=axes(z['lon'],z['lat']); a=z['field']; prov=json.loads(str(z['provenance'].item()))
                        if stamp(prov['run_utc'],naive_utc=True)!=run or prov.get('step')!=lead or a.shape!=(len(y),len(x)) or not np.allclose(np.diff(x),.25) or not np.allclose(np.diff(y),.25): raise ValueError('identity/grid')
                        values_check(s,a,var,run,lead,rules,'degC' if var=='temperature_2m' else 'mm',6 if var=='precipitation' else None,grid=(x,y))
                except ScopeLimit:
                    issue(s,'info','field_not_sampled','该字段超出本轮大小限制，未检查数值；不据此认定数据损坏。',run,lead,var)
                except (OSError,ValueError,TypeError,KeyError,EOFError,RuntimeError,zipfile.BadZipFile):
                    issue(s,'error','field_invalid','WN2 预下载字段缺失、损坏或身份/网格不匹配。',run,lead,var)
    except (OSError,ValueError,TypeError,KeyError):
        s['arrival'].update(status='unavailable',label='状态不可用',expected_label='本地未读取到资料，不据此认定资料提供方缺报。',backlog=None)
        issue(s,'error','catalog_invalid','WN2 本地预下载状态无法验证。')
    return s


def observations(app, now, rules, deadline):
    import numpy as np
    import pandas as pd
    s=source('observations','深圳格点实况'); root=app/'cache/OBSERVATION/grid'
    # File names in this existing observation cache are Beijing time, not UTC.
    found=[]; missing=[]; end=now.astimezone(BJT).replace(minute=0,second=0,microsecond=0)-timedelta(hours=1)
    for age in range(24):
        valid=end-timedelta(hours=age); p=root/f'{valid:%Y%m%d%H}.pkl'
        if p.exists(): found.append((p,valid))
        else: missing.append(valid)
    s['arrival'].update(status='available' if found else 'unavailable',label='实况已到报' if found else '未见实况',
        available_frames=len(found),expected_frames=24,missing_leads=[],
        missing_valid_bjt=[v.isoformat() for v in missing],expected_label='检查最近24个完整小时的实况，近期时次允许仍在下载或入库。',backlog=None)
    if found: freshness(s,found[0][1],now,3)
    older_missing=[v for v in missing if (now-v).total_seconds()>3*3600]
    recent_missing=[v for v in missing if v not in older_missing]
    s['arrival']['pending_recent_hours']=len(recent_missing)
    if older_missing: issue(s,'warning','observation_gaps',f'最近 24 个完整小时有 {len(older_missing)} 个实况文件缺失且距有效时间超过 3 小时。')
    if recent_missing: issue(s,'info','observations_preparing',f'近期 {len(recent_missing)} 个实况小时尚未入库，处于 3 小时到报观察宽限内。')
    reference=None
    try:
        ref=pd.read_excel(app/'data/grid.xlsx',sheet_name='数据集1',usecols=['格网ID（唯一）'])
        reference=set(pd.to_numeric(ref.iloc[:,0],errors='coerce').dropna().astype(int))
    except (OSError,ValueError,KeyError,ImportError):
        issue(s,'info','reference_unavailable','未加载实况参考格点数，缺测覆盖率仅按文件内数据计算。')
    for p,valid in found[:2]:
        if time.monotonic()>deadline: break
        try:
            if p.stat().st_size>NPZ_LIMIT: raise ScopeLimit('observation size')
            # Trusted locally generated pickle at a fixed server-owned location.
            d=pd.read_pickle(p)
            if not isinstance(d,pd.DataFrame) or 'GRIDID' not in d or 'T' not in d or 'RAIN03H' not in d: raise ValueError('observation schema')
            ids=pd.to_numeric(d['GRIDID'],errors='coerce')
            if ids.isna().any() or ids.duplicated().any(): issue(s,'error','grid_ids','实况格点 ID 缺失或重复。',valid)
            if reference:
                ratio=len(set(ids.dropna().astype(int)) & reference)/len(reference)
                s['checks']['observation_grid_coverage']=round(ratio,4)
                if ratio < .95: issue(s,'warning','observation_coverage',f'实况包含参考格点的 {ratio:.1%}，低于 95% 复核阈值。',valid)
            for field in ('T','RAIN01H','RAIN02H','RAIN03H','RAIN06H','RAIN24H','RH','WSPD','WDIR'):
                if field in d: values_check(s,pd.to_numeric(d[field],errors='coerce').to_numpy(),field,valid,0,rules,in_region=True)
            rain_fields=[f for f in ('RAIN01H','RAIN02H','RAIN03H','RAIN06H','RAIN24H') if f in d]
            for small,large in zip(rain_fields,rain_fields[1:]):
                a=sample_array(pd.to_numeric(d[small],errors='coerce').to_numpy())
                b=sample_array(pd.to_numeric(d[large],errors='coerce').to_numpy())
                bad=np.isfinite(a)&np.isfinite(b)&(b<a-.1)
                if bad.any(): issue(s,'warning','rain_interval_consistency',f'{int(bad.sum())} 个抽查点的 {large} 小于同一有效时间的 {small}（容差 0.1 mm），请复核累计时段。',valid,0,large)
        except ScopeLimit:
            issue(s,'info','field_not_sampled','实况文件超出本轮大小限制，未检查数值；不据此认定数据损坏。',valid)
        except (OSError,ValueError,TypeError,KeyError,EOFError,pickle.UnpicklingError):
            issue(s,'error','observation_invalid','抽查实况文件无法读取或字段结构不符合接口。',valid)
    return s


def ensemble_arrival(app, now):
    s=source('ecmwf-ensembles','ECMWF 集合均值')
    root=app/'cache/ECMWF-ENSEMBLES'
    # Same eligibility lag and 00/12 UTC cycles used by the existing map warmer.
    eligible=(now-timedelta(hours=9)).replace(minute=0,second=0,microsecond=0)
    eligible=eligible.replace(hour=eligible.hour//12*12)
    for back in range(4):
        run=eligible-timedelta(hours=12*back);folder=root/f'{run:%Y%m%d%H}'/'maps'
        if not folder.is_dir(): continue
        try:
            files=[]
            for p in folder.iterdir():
                if re.fullmatch(r'ensemble_mean_f\d{3}_(?:2t|tp)\.npz',p.name): files.append(p.name)
                if len(files)>=300: break
            if not files: continue
            s['arrival'].update(status='available',label='集合平均图层已下载',available_frames=len(files),
                expected_frames=None,frame_unit='图层文件',missing_leads=[],backlog=None,
                expected_label='仅检查已下载的集合平均图层，不据此评价全部成员及预报时效的完整性。')
            freshness(s,run,now,30)
            issue(s,'info','ensemble_arrival_only','本次仅检查集合平均图层到报，图层数值及各成员资料未抽查。',run)
            return s
        except OSError: break
    s['arrival'].update(status='unavailable',label='未见集合均值缓存',available_frames=0,expected_frames=None,
        expected_label='仅检查最近 4 个符合现有 9 小时发布观察延迟的 00/12 UTC 批次',backlog=None)
    issue(s,'info','ensemble_unavailable','未读取到近期集合均值图层缓存，未作完整性与数值质量结论。')
    return s


def scan(app=APP, weather=WEATHER, cache=CACHE, now=None):
    now=now or datetime.now(UTC); deadline=time.monotonic()+SCAN_SECONDS; rules=rules_for(cache)
    pressure_jobs=[]
    sources=native_models(weather,now,rules,deadline,deferred=pressure_jobs)
    sources.append(wn2_source(app,now,rules,deadline))
    sources.append(wn3_source(weather,now,rules,deadline))
    sources.append(observations(app,now,rules,deadline))
    sources.append(ensemble_arrival(app,now))
    deferred_pressure_winds(pressure_jobs,rules,deadline)
    for s in sources:
        c=s['checks'];c['derived_wind_fields']=sum(bool(m.get('derived')) for m in s['metrics'])
        c['coverage_label']=f'数值抽查 {c["sampled_files"]} 份要素数据；{c["sampled_cells"]:,}/{c["total_cells"]:,} 个值（只看近期两个预报时间或实况时间）'
        if c['derived_wind_fields']:c['coverage_label']+=f'；另检查 {c["derived_wind_fields"]} 个 u/v 合成风速项'
        if c.get('pressure_wind_layers'):c['coverage_label']+='；高空风已检 '+ '/'.join(c['pressure_wind_layers'])+' hPa'
        if not c['sampled_files']: issue(s,'info','not_sampled','本次未取得可检查的数值字段，暂不作数值质量结论。')
    issues=[row for s in sources for row in s['issues']]
    summary={'sources':len(sources),'issues':len(issues),
        'error':sum(i['severity']=='error' for i in issues),'warning':sum(i['severity']=='warning' for i in issues),
        'sampled_files':sum(s['checks']['sampled_files'] for s in sources)}
    report={'schema':1,'rule_revision':RULE_REVISION,**times(datetime.now(UTC),'generated'),'summary':summary,'sources':sources,
        'rules':[{'id':k,**v,'label':{'temperature':'摄氏气温','rain':'累计降水（允许0.1 mm以内的微小负值）','humidity':'相对湿度','wind':'风分量','wind_speed':'实况或合成风速','wind_direction':'实况风向（360° 有效）','pressure':'海平面气压','height':'位势高度'}[k],
                  'description':'通用复核范围；季节和分层阈值见上表，超范围结果需人工复核。'} for k,v in rules.items() if isinstance(v,dict) and 'unit' in v] +
                [{'id':f'rain_{window}h','label':f'{window} 小时累计降水','unit':'mm','min':rules['rain']['min'],'max':upper,
                  'description':'按累计时段设置复核上限，超限结果提示复核，不直接判为错误数据。'} for window,upper in rules['rain_windows'].items()] +
                [{'id':'temperature_jump','label':'相邻预报时效气温变化','description':f'同一批次抽样每小时变化超过 {rules["temperature_jump_per_hour"]:g}°C 时提示复核。'},
                 {'id':'rain_interval_consistency','label':'实况累计时段一致性','description':'同一有效时间的长时段累计降水不应小于短时段累计降水，允许0.1 mm计算容差。'}],
        'excluded':{'demo_source':'WN3','label':'WN3 演示模式共用同一份资料，不重复统计到报；预留接口未接真实数据，不纳入缺报告警。'},
        'limitations':['本报告基于本地已下载资料，未核对资料提供方的发布状态。更新提醒时限按最新预报起报时间或实况观测时间计算，由平台设定，不代表提供方承诺的到报时间，也与历史资料保留期限无关。',
        '抽查近期两个预报时效或实况时次，每要素最多检查20,000个格点。高空风优先检查850、300 hPa，再轮换其他层；未检查的时效和高度层单独标识，结果不替代全量质控。',
        '超出复核阈值的数据标记为“需核实”。强天气可能产生有效极值，需结合天气背景和资料来源复核；模块仅记录问题，不改动原始数据。',
        '模式未提供的要素不作为缺测。本版暂未开展高空地形屏蔽及观测空间一致性检查。']}
    report['seasonal_rules']=[{'id':key,**profile} for key,profile in rules['seasonal']['profiles'].items()]
    for month in sorted(rules['seasonal']['monthly_overrides'],key=int):
        profile=seasonal_profile(datetime(2026,int(month),15,12,tzinfo=BJT),rules)
        report['seasonal_rules'].append({**profile,'months':[int(month)],'override':True})
    report['wind_layer_rules']=[{'level':level,'label':'地面10米' if level=='surface' else f'{level} hPa',
        'max':maximum,'unit':'m/s'} for level,maximum in rules['wind_levels'].items()]
    report['rule_policy']={'label':'保守试行复核阈值（支持按月调整）','timezone':'Asia/Shanghai',
        'season_scope':'季节阈值适用于评分区域（108°～120°E、18°～27°N）的地面气温和累计降水；区域外及高空气温采用通用范围。',
        'time_basis':'按预报有效时间或实况观测时间的北京时间月份选取季节：春3～5月、夏6～8月、秋9～11月、冬12～2月。',
        'rain_basis':'累计降水跨月时采用覆盖月份中较宽的上限；保留0.1 mm微小负值容差，避免计算误差造成误报。',
        'wind_basis':'风速由同一批次、时效及格点的u/v分量合成，按对应气压层阈值复核。hPa为气压单位，气压层不等同于固定海拔。',
        'basis':'当前阈值为保守试行复核范围，尚未基于本地多年气候资料标定，亦非官方质控标准；超阈值结果需人工复核。'}
    report['limitations'].append('季节阈值仅用于评分区域的地面气温和降水。山地及极端天气需人工复核；高度层未知的风场不套用地面阈值。')
    report['limitations'].append('数值抽查覆盖EC、GFS、AIFS、AI-GFS、WN2、WN3及深圳格点实况。EC集合平均仅检查图层到报；再分析资料和台风路径尚未纳入数值质控。')
    if time.monotonic()>deadline: report['limitations'].append('本轮达到检查时限，剩余数据未检查，可稍后重新检查。')
    return report


def _read_snapshot():
    try:
        value=load_json(CACHE/'snapshot.json')
        if value.get('schema')!=1 or not isinstance(value.get('sources'),list): return None
        stamp(value['generated_utc'])
        return value
    except (OSError,ValueError,KeyError): return None


def status():
    global _process,_job
    with _lock:
        snapshot=_read_snapshot(); running=False
        if _process is not None:
            code=_process.poll()
            if code is None and time.monotonic()-_job.get('_monotonic',0)>SCAN_SECONDS+60:
                _process.kill(); code=-1
            if code is None: running=True
            else:
                _job.update(times(datetime.now(UTC),'finished'),status='done' if code==0 else 'failed')
                if code != 0: _job['message']='质控扫描未完成，保留上次报告；请稍后重新扫描。'
                _process=None
        state='running' if running else ('failed' if _job.get('status')=='failed' else ('ready' if snapshot else 'idle'))
        stale=bool(snapshot) and (snapshot.get('rule_revision')!=RULE_REVISION or (datetime.now(UTC)-stamp(snapshot['generated_utc'])).total_seconds()>TTL)
        return {'schema':1,'state':state,'snapshot':snapshot,'stale':stale,'ttl_seconds':TTL,
                'job':{k:v for k,v in _job.items() if not k.startswith('_')}}


def refresh():
    global _process,_job
    # Reap previous jobs before deciding whether another bounded worker may start.
    status()
    with _lock:
        if _process is None:
            try:
                CACHE.mkdir(parents=True,exist_ok=True)
                env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
                _process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--scan','--output',str(CACHE/'snapshot.json')],
                    env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                _job={**times(datetime.now(UTC),'started'),'status':'running','_monotonic':time.monotonic()}
            except OSError:
                _job={**times(datetime.now(UTC),'started'),'status':'failed','message':'无法启动质控扫描，保留上次报告；请稍后重试。'}
    return status()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--scan',action='store_true');parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not args.scan: parser.error('--scan required')
    output=args.output or CACHE/'snapshot.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    import fcntl
    # Avoid duplicate active scans across an HTTP server restart. A second worker
    # leaves the existing snapshot intact while the original worker finishes.
    with output.with_suffix('.lock').open('a') as handle:
        try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: sys.exit(0)
        try: os.nice(10)
        except OSError: pass
        report=scan();atomic_json(output,report)
        print(json.dumps(report['summary'],ensure_ascii=False))
