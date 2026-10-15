"""Score verified local EC/GFS and WeatherNext 3 fields against observations, offline.

Sources: legacy regional JSON (<model>_<run>_f<lead>.json, synced before the HK
server was retired) and locally prepared global frames
(<model>_<run>_f<lead>_global1.json + .fields/*.npy). When both exist for the
same run/lead, the legacy file is kept (values are identical to ~1e-3).

These rounded JSON fields have a separate dataset from legacy GRIB scores.
No downloads, no extrapolation, no reconstruction of absent rain intervals.
"""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import hashlib,json,os,re,time,math,zipfile
import sys
if '/home/chengyang/gods-eye-weather/weather_backend' not in sys.path:sys.path.append('/home/chengyang/gods-eye-weather/weather_backend')
from global_store import field_directory
import numpy as np
import pandas as pd
from scipy.interpolate import RegularGridInterpolator
from shapely.geometry import shape,Point
from shapely.ops import unary_union

BJT=timezone(timedelta(hours=8))
MODELS={'ecmwf':'ECMWF-IFS','gfs':'NCEP-GFS'}
WN3_MODEL='Google WeatherNext 3（集合均值）'
WN3_SIGNATURE='wn3-native-mean-v1'
NATIVE_MODELS=tuple(MODELS.values())+(WN3_MODEL,)
NATIVE_LEADS=tuple(range(169))
WN3_SOURCES={
    'native-score-v1':('weathernext3-native-score-v1',(108.,18.,120.,27.)),
    'native-southern-china-v1':('weathernext3-native-southern-china-v1',(100.,10.,130.,35.)),
    'native-china-v1':('weathernext3-native-china-v1',(60.,0.,150.,60.)),
}
WN3_FIELDS={'surface_t':('temperature_2m_mean','degC'),
            'rain1h':('total_precipitation_1hr_mean','mm')}
WN3_ERRORS=(ValueError,KeyError,TypeError,OSError,EOFError,zipfile.BadZipFile)
THRESHOLDS=[.1,1,3,10,20,30,50]
GLOBAL_STORAGE={'ecmwf':'ecmwf-global-v1','gfs':'gfs-global-v1'}
BOX=(110.0,119.0,19.0,26.0)  # lon0,lon1,lat0,lat1: Shenzhen plus margin
WINDOW_DAYS=int(os.environ.get('NATIVE_WINDOW_DAYS','31'))  # TEST: 31-day rolling window (was 8)

def load_global(p,slug):
    """Read a local global1 frame and cut a small Shenzhen box (legacy JSON layout)."""
    d=json.loads(p.read_text())
    if d.get('storage') not in (GLOBAL_STORAGE[slug],slug+'-regional-v1') or d.get('native_grid_degrees')!=0.25:
        raise ValueError('本地全球归档元数据不匹配')
    x=np.asarray(d['lon'],float);y=np.asarray(d['lat'],float)
    expected=((49,37) if d.get('bounds')==[108.,18.,120.,27.] else (361,241)) if d['storage'].endswith('-regional-v1') else (1441,721)
    if (len(x),len(y))!=expected or not np.allclose(np.diff(x),.25) or not np.allclose(np.diff(y),.25) or x[0]>BOX[0] or x[-1]<BOX[1] or y[0]>BOX[2] or y[-1]<BOX[3]:raise ValueError('本地区域归档网格不匹配')
    xi=np.flatnonzero((x>=BOX[0])&(x<=BOX[1]));yi=np.flatnonzero((y>=BOX[2])&(y<=BOX[3]))
    fields=field_directory(p)
    def part(name):
        f=fields/(name+'.npy')
        if not f.is_file():return None
        a=np.load(f,mmap_mode='r',allow_pickle=False)
        if a.shape!=(len(y),len(x)):raise ValueError('本地全球归档字段形状不匹配')
        return np.asarray(a[yi[0]:yi[-1]+1,xi[0]:xi[-1]+1],float).ravel().tolist()
    out={k:d.get(k) for k in ('schema','model','run','valid','lead','rain_start')}
    out.update(lon=x[xi].tolist(),lat=y[yi].tolist())
    t=part('surface_t')
    out['layers']={'surface':{'t':t}} if t is not None else {}
    out['rain3h']=part('rain3h') if d.get('interval_hours') in (None,3) else None
    return out

def source_signature(p,is_global,obs_path):
    sig=[p.stat().st_mtime_ns]
    if is_global:
        for name in ('surface_t','rain3h'):
            f=field_directory(p)/(name+'.npy')
            sig.append(f.stat().st_mtime_ns if f.is_file() else 0)
    return sig+[obs_path.stat().st_mtime_ns]

def stamp(s):return datetime.fromisoformat(s.replace('Z','+00:00'))

def validate_frame(d,model,run,lead):
    if d.get('schema')!=2 or d.get('model')!=model or stamp(d['run'])!=run or d.get('lead')!=lead or stamp(d['valid'])!=run+timedelta(hours=lead):
        raise ValueError('归档身份或有效时间不匹配')
    x=np.asarray(d['lon'],float);y=np.asarray(d['lat'],float)
    if len(x)<2 or len(y)<2 or not np.allclose(np.diff(x),.25) or not np.allclose(np.diff(y),.25):
        raise ValueError('非0.25度网格，拒绝作为本组检验资料')
    return x,y


def wn3_manifest(path):
    """Admit only committed native ensemble-mean frames, never display JSON fields."""
    match=re.fullmatch(r'run_(\d{10})\.json',path.name)
    if not match:raise ValueError('WN3起报文件名不匹配')
    run=datetime.strptime(match[1],'%Y%m%d%H').replace(tzinfo=timezone.utc)
    data=json.loads(path.read_text())
    if not isinstance(data,dict):raise ValueError('WN3区域目录格式不匹配')
    directory=data.get('native_cache_directory')
    if directory not in WN3_SOURCES:raise ValueError('WN3非已验证区域原生缓存')
    _,bounds=WN3_SOURCES[directory]
    if (data.get('schema')!=2 or data.get('model')!=WN3_MODEL or stamp(data['run'])!=run
        or data.get('statistic')!='mean' or data.get('native_grid_degrees')!=.1
        or data.get('interval_hours')!=1 or data.get('bounds')!=list(bounds)
        or data.get('status') not in ('preparing','complete')):
        raise ValueError('WN3区域目录身份不匹配')
    leads=set()
    for frame in data.get('frames',[]):
        lead=frame.get('lead')
        if (type(lead) is not int or not 1<=lead<=360 or lead in leads
            or stamp(frame['valid'])!=run+timedelta(hours=lead)):
            raise ValueError('WN3已发布有效时间不匹配')
        leads.add(lead)
    if not leads:raise ValueError('WN3区域目录暂无已发布帧')
    return run,directory,leads

def wn3_paths(root,run,lead,directory,committed):
    """The manifest commits each complete forecast hour; absent hours stay absent."""
    run_id=run.strftime('%Y%m%d%H')
    keys=[(lead,'surface_t')]+[(hour,'rain1h') for hour in range(lead-2,lead+1)]
    return {(hour,key):root/directory/run_id/f'f{hour:03}_{key}.npz'
            for hour,key in keys if hour in committed}

def wn3_source_signature(paths,obs_path,reference_signature):
    files=[]
    for (lead,key),path in sorted(paths.items()):
        if not path.is_file():
            files.append([lead,key,None]);continue
        stat=path.stat();metadata=None
        try:
            with np.load(path,allow_pickle=False) as data:
                # Metadata participates in invalidation; validation happens before scoring.
                raw=str(data['meta'].item())
            metadata=hashlib.sha256(raw.encode()).hexdigest()
        except WN3_ERRORS:
            metadata='invalid'
        files.append([lead,key,stat.st_mtime_ns,stat.st_size,metadata])
    obs=obs_path.stat()
    return {'adapter':WN3_SIGNATURE,'files':files,
            'observation':[obs.st_mtime_ns,obs.st_size],'reference':reference_signature}

def load_wn3_field(path,run,lead,key,directory):
    """Validate converted native axes, identity, units and mean provenance, offline."""
    schema,bounds=WN3_SOURCES[directory]
    variable,unit=WN3_FIELDS[key]
    with np.load(path,allow_pickle=False) as data:
        meta=json.loads(str(data['meta'].item()))
        x,y,a=data['lon'],data['lat'],data['values']
    if not isinstance(meta,dict):raise ValueError('WN3字段元数据格式不匹配')
    if (meta.get('schema')!=schema or stamp(meta['run'])!=run or type(meta.get('lead')) is not int
        or meta['lead']!=lead or stamp(meta['valid'])!=run+timedelta(hours=lead)
        or meta.get('field')!=key or meta.get('source_variable')!=variable
        or meta.get('unit')!=unit or meta.get('grid_degrees')!=.1
        or meta.get('bounds')!=list(bounds) or meta.get('statistic')!='mean'
        or meta.get('source_kind') not in ('earth-engine-region','existing-gcs-offline-cache')
        or not re.fullmatch(r'[0-9a-f]{64}',str(meta.get('source_sha256','')))):
        raise ValueError('WN3原生字段身份或来源不匹配')
    expected_x=np.arange(round(bounds[0]*10),round(bounds[2]*10)+1)/10
    expected_y=np.arange(round(bounds[1]*10),round(bounds[3]*10)+1)/10
    if (x.shape!=expected_x.shape or y.shape!=expected_y.shape
        or not np.allclose(x,expected_x,atol=1e-7,rtol=0)
        or not np.allclose(y,expected_y,atol=1e-7,rtol=0)
        or a.shape!=(len(y),len(x)) or a.dtype!=np.float32 or not np.isfinite(a).all()):
        raise ValueError('WN3原生网格或字段不完整')
    if key=='rain1h' and (np.min(a)<0 or np.max(a)>1000):
        raise ValueError('WN3小时降水单位或范围不匹配')
    if key=='surface_t' and (np.min(a)<-100 or np.max(a)>80):
        raise ValueError('WN3摄氏气温单位或范围不匹配')
    xi=np.flatnonzero((x>=BOX[0])&(x<=BOX[1]));yi=np.flatnonzero((y>=BOX[2])&(y<=BOX[3]))
    if len(xi)<2 or len(yi)<2:raise ValueError('WN3原生区域未覆盖检验范围')
    return x[xi],y[yi],np.asarray(a[np.ix_(yi,xi)],float)

def load_wn3_frame(root,run,lead,directory,committed):
    """Temperature and trailing 3h rain are independently admissible."""
    paths=wn3_paths(root,run,lead,directory,committed);valid=run+timedelta(hours=lead)
    frame={'schema':2,'model':WN3_MODEL,'run':run.isoformat(),'valid':valid.isoformat(),
           'lead':lead,'layers':{},'rain3h':None,'rain_start':None}
    issues=[];axes=None
    def read(hour,key):
        path=paths.get((hour,key))
        if path is None or not path.is_file():return None
        return load_wn3_field(path,run,hour,key,directory)
    try:
        temperature=read(lead,'surface_t')
        if temperature is not None:
            axes=temperature[:2];frame['layers']={'surface':{'t':temperature[2]}}
    except WN3_ERRORS:
        issues.append('WN3气温原生字段未通过验证，未生成气温评分')
    try:
        rain=[read(hour,'rain1h') for hour in range(lead-2,lead+1)]
        if all(item is not None for item in rain):
            x,y=rain[0][:2]
            if any(not np.array_equal(x,item[0]) or not np.array_equal(y,item[1]) for item in rain[1:]):
                raise ValueError('WN3小时降水网格不一致')
            if axes is not None and (not np.array_equal(x,axes[0]) or not np.array_equal(y,axes[1])):
                raise ValueError('WN3气温和降水网格不一致')
            axes=(x,y)
            # Each hour ends at run+hour. Sum exactly (valid-3h, valid], never
            # subtract cumulative fields or bridge a missing forecast hour.
            frame['rain3h']=sum((item[2] for item in rain),np.zeros_like(rain[0][2])).ravel().tolist()
            frame['rain_start']=(valid-timedelta(hours=3)).isoformat()
    except WN3_ERRORS:
        issues.append('WN3小时降水原生字段未通过验证，未生成降水评分')
    if axes is not None:frame.update(lon=axes[0],lat=axes[1])
    return frame,issues

def paired(d,field,grid,obs,kind):
    x=np.asarray(d['lon'],float);y=np.asarray(d['lat'],float)
    a=np.asarray(field,float).reshape(len(y),len(x))
    column='T' if kind=='气温' else 'RAIN03H'
    records=grid.merge(obs[['GRIDID',column]].drop_duplicates('GRIDID'),on='GRIDID')
    observation=pd.to_numeric(records[column],errors='coerce').to_numpy(dtype=float,copy=True)
    observation[(observation<-100 if kind=='气温' else observation<0)]=np.nan
    method='linear' if kind=='气温' else 'nearest'
    fc=RegularGridInterpolator((y,x),a,method=method,bounds_error=False,fill_value=np.nan)(records[['lat','lon']].to_numpy())
    mask=np.isfinite(fc)&np.isfinite(observation)
    if kind=='降水':mask&=fc>=0
    return fc[mask],observation[mask]

def score_frame(d,grid,obs,created):
    rows=[];run=stamp(d['run']);valid=stamp(d['valid'])
    base=dict(model=d['model'],run=run.astimezone(BJT).isoformat(),valid=valid.astimezone(BJT).isoformat(),lead=d['lead'],created=created)
    # Keep the same naive BJT ISO convention as the legacy catalog.
    base['run']=base['run'][:19];base['valid']=base['valid'][:19]
    for kind in ['气温','降水']:
        if kind=='降水':
            if not d.get('rain3h') or not d.get('rain_start') or stamp(d['rain_start'])!=valid-timedelta(hours=3):continue
            field=d['rain3h']
        else:field=d.get('layers',{}).get('surface',{}).get('t')
        if field is None:continue
        fc,ob=paired(d,field,grid,obs,kind)
        if not len(fc):continue
        row=dict(base,dataset='hk-'+kind+('-3' if kind=='降水' else '-2m'),n=len(fc))
        if kind=='降水':
            for threshold in THRESHOLDS:
                f=fc>=threshold;o=ob>=threshold
                rows.append(dict(row,threshold=threshold,H=int((f&o).sum()),M=int((~f&o).sum()),F=int((f&~o).sum())))
        else:
            delta=fc-ob
            cc=float(np.corrcoef(fc,ob)[0,1]) if np.std(fc)>1e-12 and np.std(ob)>1e-12 else None
            rows.append(dict(row,threshold=None,ME=float(delta.mean()),MAE=float(np.abs(delta).mean()),RMSE=float(np.sqrt((delta*delta).mean())),CC=cc))
    return rows

def build_native(app,weather,cache):
    app=Path(app);weather=Path(weather);cache=Path(cache)
    signature='hk-native-025-v1'
    try:old=json.loads(cache.read_text());old=old if old.get('version')==signature else {}
    except (OSError,ValueError):old={}
    memo=old.get('files',{});updated={};rows=[];issues=[]
    # Match the source grid's column conventions without importing Streamlit.
    grid=pd.read_excel(app/'data/grid.xlsx',sheet_name='数据集1').rename(columns={'格网ID（唯一）':'GRIDID','格网左下角经度（度）':'west','格网右上角经度（度）':'east','格网左下角纬度（度）':'south','格网右上角纬度（度）':'north'})
    for col in ['GRIDID','west','east','south','north']:grid[col]=pd.to_numeric(grid[col],errors='coerce')
    grid=grid.dropna(subset=['GRIDID','west','east','south','north']);grid['GRIDID']=grid.GRIDID.astype(int)
    grid['lon']=(grid.west+grid.east)/2;grid['lat']=(grid.south+grid.north)/2
    if not {'GRIDID','lon','lat'}<=set(grid):raise ValueError('检验格点列名不匹配')
    city=unary_union([shape(f['geometry']) for f in json.loads((app/'data/shenzhen_streets.geojson').read_text())['features']])
    total_grid=grid['GRIDID'].nunique()
    grid=grid.loc[[city.covers(Point(float(x),float(y))) for x,y in zip(grid.lon,grid.lat)]].copy()
    found={};now=datetime.now(timezone.utc);cutoff=now-timedelta(days=WINDOW_DAYS)
    here=Path(__file__).resolve().parent
    # Main-platform files first; TEST-only backfill (cache/native-backfill) fills older runs.
    roots=[(weather/'region-v4',False),(here/'cache'/'native-backfill',True)]
    obs_roots=[app/'cache/OBSERVATION/grid',here/'cache'/'obs-backfill'/'grid']
    for slug,model in MODELS.items():
        for root,backfill in roots:
          for p in sorted((root/slug).glob(slug+'_??????????_f???*.json')):
            match=re.fullmatch(slug+r'_(\d{10})_f(\d{3})(_global1)?\.json',p.name)
            if not match:continue
            run=datetime.strptime(match[1],'%Y%m%d%H').replace(tzinfo=timezone.utc);lead=int(match[2]);valid=run+timedelta(hours=lead)
            if lead not in NATIVE_LEADS or not cutoff<=valid<=now-timedelta(hours=1):continue
            name=valid.astimezone(BJT).strftime('%Y%m%d%H')+'.pkl'
            obs_path=next((r/name for r in obs_roots if (r/name).is_file()),None)
            if obs_path is None:continue
            is_global=bool(match[3]);ident=(slug,run,lead)
            if ident in found and backfill:continue  # never replace a main-platform file
            if ident in found and not found[ident][5]:continue  # legacy file already chosen
            if ident in found and is_global:continue
            found[ident]=(p,model,run,lead,obs_path,is_global,slug)
    candidates=[found[k] for k in sorted(found)]
    for p,model,run,lead,obs_path,is_global,slug in candidates:
        key=str(p);sig=source_signature(p,is_global,obs_path)
        saved=memo.get(key)
        if saved and saved['signature']==sig:
            updated[key]=saved;rows.extend(saved['rows']);continue
        try:
            d=load_global(p,slug) if is_global else json.loads(p.read_text())
            validate_frame(d,model,run,lead)
            obs=pd.read_pickle(obs_path)
            if obs['GRIDID'].nunique()<math.ceil(total_grid*.95):continue
            result=score_frame(d,grid,obs,time.time())
            updated[key]={'signature':sig,'rows':result};rows.extend(result)
        except (ValueError,KeyError,TypeError,OSError) as exc:
            if not issues:print('Native archive validation:',type(exc).__name__,str(exc),flush=True)
            issues.append({'model':model,'run':run.isoformat(),'lead':lead,'reason':'归档字段或观测不完整，未生成评分'})
    # WN3 consumes only committed local NPZ files, independently of all acquisition
    # services. Preserve the EC/GFS memo version so pruned historical scores survive.
    reference_signature=[]
    for path in (app/'data/grid.xlsx',app/'data/shenzhen_streets.geojson'):
        info=path.stat();reference_signature.append([info.st_mtime_ns,info.st_size])
    wn3_root=weather/'region-v4'/'weathernext3'
    attempted_wn3=set();invalid_wn3_runs=set();committed_wn3={}
    for manifest in sorted(wn3_root.glob('run_??????????.json')):
        try:run,directory,committed=wn3_manifest(manifest)
        except WN3_ERRORS:
            # A present invalid catalog must not resurrect an earlier cached score.
            # Absent/pruned source catalogs may retain certified historical results.
            try:
                bad_run=datetime.strptime(manifest.stem[4:],'%Y%m%d%H').replace(tzinfo=timezone.utc)
                invalid_wn3_runs.add(bad_run.astimezone(BJT).isoformat()[:19])
            except ValueError:pass
            continue  # Unsupported old display/raw catalogs are not verification input.
        committed_wn3[run.astimezone(BJT).isoformat()[:19]]=committed
        for lead in NATIVE_LEADS:
            valid=run+timedelta(hours=lead)
            if lead not in committed or not cutoff<=valid<=now-timedelta(hours=1):continue
            name=valid.astimezone(BJT).strftime('%Y%m%d%H')+'.pkl'
            obs_path=next((r/name for r in obs_roots if (r/name).is_file()),None)
            if obs_path is None:continue
            key=WN3_SIGNATURE+':'+str(manifest)+f':f{lead:03}'
            attempted_wn3.add((WN3_MODEL,run.astimezone(BJT).isoformat()[:19],lead))
            try:
                paths=wn3_paths(wn3_root,run,lead,directory,committed)
                sig=wn3_source_signature(paths,obs_path,reference_signature)
                saved=memo.get(key)
                if saved and saved.get('signature')==sig:
                    updated[key]=saved;rows.extend(saved['rows']);continue
                frame,problems=load_wn3_frame(wn3_root,run,lead,directory,committed)
                for reason in problems:
                    issues.append({'model':WN3_MODEL,'run':run.isoformat(),'lead':lead,'reason':reason})
                obs=pd.read_pickle(obs_path)
                if obs['GRIDID'].nunique()<math.ceil(total_grid*.95):continue
                result=score_frame(frame,grid,obs,time.time()) if 'lon' in frame else []
                updated[key]={'signature':sig,'rows':result};rows.extend(result)
            except WN3_ERRORS:
                issues.append({'model':WN3_MODEL,'run':run.isoformat(),'lead':lead,
                    'reason':'WN3原生字段或观测不完整，未生成评分'})
    # The main platform prunes forecasts/observations after 30 days; keep already-computed
    # scores inside the window instead of letting them vanish with their source files.
    chosen={(r['model'],r['run'],r['lead']) for r in rows}
    for key,saved in memo.items():
        if key in updated or not saved.get('rows'):continue
        first=saved['rows'][0]
        if first['model']==WN3_MODEL:
            # Only this adapter's fully validated cached results may survive pruning.
            if (not key.startswith(WN3_SIGNATURE+':')
                or not isinstance(saved.get('signature'),dict)
                or saved['signature'].get('adapter')!=WN3_SIGNATURE
                or first['run'] in invalid_wn3_runs
                or (first['model'],first['run'],first['lead']) in attempted_wn3):continue
            if first['run'] in committed_wn3:
                committed=committed_wn3[first['run']];lead=first['lead']
                # A still-present manifest is authoritative even when observations
                # have been pruned: retracted hours cannot restore old score rows.
                kept=[r for r in saved['rows'] if lead in committed and
                    (r['dataset']!='hk-降水-3' or all(h in committed for h in range(lead-2,lead+1)))]
                if not kept:continue
                if len(kept)!=len(saved['rows']):saved=dict(saved,rows=kept)
        if (first['model'],first['run'],first['lead']) in chosen:continue
        valid=datetime.fromisoformat(first['valid']).replace(tzinfo=BJT)
        if cutoff<=valid<=now:
            updated[key]=saved;rows.extend(saved['rows']);chosen.add((first['model'],first['run'],first['lead']))
    cache.parent.mkdir(parents=True,exist_ok=True)
    temp=cache.with_suffix('.tmp');temp.write_text(json.dumps({'version':signature,'files':updated},ensure_ascii=False,allow_nan=False));os.replace(temp,cache)
    datasets=[]
    for kind in ['降水','气温']:
        items=[r for r in rows if r['dataset']=='hk-'+kind+('-3' if kind=='降水' else '-2m')]
        if not items:continue
        datasets.append(dict(id=items[0]['dataset'],kind=kind,source='hk',accumulation=3 if kind=='降水' else None,level=None,
            has_n=True,models=[m for m in NATIVE_MODELS if any(r['model']==m for r in items)],supported_models=list(NATIVE_MODELS),
            start=min(r['valid'] for r in items),end=max(r['valid'] for r in items),leads=sorted({r['lead'] for r in items}),thresholds=THRESHOLDS if kind=='降水' else [],times=sorted({r['valid'] for r in items}),
            reference='深圳开放数据格点分析场',region='深圳市行政区域',updated=datetime.fromtimestamp(max(r['created'] for r in items),BJT).isoformat(),
            model_sources={
                **{m:dict(grid_degrees=.25,statistic='deterministic',note='本地EC/GFS原生0.25°归档，数值约三位小数') for m in MODELS.values()},
                WN3_MODEL:dict(grid_degrees=.1,statistic='mean',fields=['surface_t','rain1h'],
                    note='官方常规2米气温及小时降水集合均值；3小时降水由末端三份完整小时场相加，只读本机原生NPZ缓存')},
            source_note='EC/GFS为本地0.25°归档（早期为香港同步文件，之后为本机全球0.25°场），数值约三位小数；WeatherNext 3为已验证本机0.1°常规气温/降水集合均值原生NPZ缓存。WN3的3小时降水严格相加末端三个完整小时场，缺任一小时不评分。降水最近邻、气温双线性插值到深圳市域格点；均值产品会平滑极端量值，各模式可用日期和起报不同，比较时请选择共同有效时次。'))
    return datasets,rows,issues

# Serve the last completed native score snapshot while new leads are scored.
# A complete snapshot is seeded on deployment; subsequent updates are atomic.
import threading
_native_thread=None
_native_guard=threading.Lock()

def _native_refresh(app,weather,cache,snapshot):
    import fcntl
    with snapshot.with_suffix('.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        try:
            datasets,rows,issues=build_native(app,weather,cache)
            payload=dict(datasets=datasets,rows=rows,issues=issues,generated=time.time())
            temp=snapshot.with_suffix('.tmp')
            temp.write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False))
            os.replace(temp,snapshot)
        except Exception as exc:
            print('Native score refresh deferred:',type(exc).__name__,flush=True)

def cached_native(app,weather,cache):
    global _native_thread
    cache=Path(cache);snapshot=cache.with_name('native-catalog.json')
    try:
        data=json.loads(snapshot.read_text())
        if not all(isinstance(data.get(k),list) for k in ('datasets','rows','issues')):raise ValueError('Invalid snapshot')
    except (OSError,ValueError):
        # Only first-time installations lack a completed snapshot.
        datasets,rows,issues=build_native(app,weather,cache)
        snapshot.parent.mkdir(parents=True,exist_ok=True)
        temp=snapshot.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(datasets=datasets,rows=rows,issues=issues,generated=time.time()),ensure_ascii=False,allow_nan=False))
        os.replace(temp,snapshot)
        return datasets,rows,issues
    if time.time()-snapshot.stat().st_mtime>300:
        with _native_guard:
            if _native_thread is None or not _native_thread.is_alive():
                _native_thread=threading.Thread(target=_native_refresh,args=(app,weather,cache,snapshot),daemon=True)
                _native_thread.start()
    return data['datasets'],data['rows'],data['issues']

if __name__=='__main__':
    import sys
    out=build_native('/home/chengyang/ec-gfs-server','/home/chengyang/gods-eye-weather/weather-data',Path(__file__).parent/'native-scores.json')
    Path(__file__).with_name('native-catalog.json').write_text(json.dumps({'datasets':out[0],'rows':out[1],'issues':out[2]},ensure_ascii=False,allow_nan=False))
    print(json.dumps({'datasets':[{'id':d['id'],'models':d['models'],'start':d['start'],'end':d['end']} for d in out[0]],'rows':len(out[1]),'issues':len(out[2])},ensure_ascii=False))
