"""Offline scoring of committed Open-Meteo runs against existing observations."""
from datetime import datetime, timedelta
from pathlib import Path
import json
import math
import time

import numpy as np
import pandas as pd
from shapely.geometry import Point, shape
from shapely.ops import unary_union

import openmeteo_ecmwf as ec
import openmeteo_regional as regional

APP = Path('/home/chengyang/ec-gfs-server')
VERIFY = Path('/home/chengyang/verification-test')
THRESHOLDS = [.1, 1, 3, 10, 20, 30, 50]
REVISION = 'openmeteo-verification-v2'


def bjt(value):
    return value.astimezone(ec.BJT).isoformat()[:19]


def signature(path):
    s = path.stat()
    return [str(path), s.st_mtime_ns, s.st_size]


def scalar(forecast, observed):
    error = forecast-observed
    return dict(ME=float(error.mean()), MAE=float(np.abs(error).mean()), RMSE=float(np.sqrt(np.mean(error**2))),
                CC=float(np.corrcoef(forecast, observed)[0,1]) if np.std(forecast)>1e-12 and np.std(observed)>1e-12 else None)


def city_grid():
    grid = pd.read_excel(APP/'data/grid.xlsx', sheet_name='数据集1').rename(columns={
        '格网ID（唯一）':'GRIDID', '格网左下角经度（度）':'west', '格网右上角经度（度）':'east',
        '格网左下角纬度（度）':'south', '格网右上角纬度（度）':'north'})
    for key in ['GRIDID','west','east','south','north']:
        grid[key] = pd.to_numeric(grid[key], errors='coerce')
    grid = grid.dropna(subset=['GRIDID','west','east','south','north']).copy()
    grid['GRIDID'] = grid.GRIDID.astype(int)
    count = grid.GRIDID.nunique()
    grid['lon'] = (grid.west+grid.east)/2
    grid['lat'] = (grid.south+grid.north)/2
    city = unary_union([shape(f['geometry']) for f in json.loads((APP/'data/shenzhen_streets.geojson').read_text())['features']])
    grid = grid.loc[[city.covers(Point(x,y)) for x,y in zip(grid.lon,grid.lat)]]
    return grid, count


def observation_path(valid):
    name = valid.astimezone(ec.BJT).strftime('%Y%m%d%H')+'.pkl'
    return next((root/name for root in [APP/'cache/OBSERVATION/grid', VERIFY/'cache/obs-backfill/grid']
                 if (root/name).is_file()), None)


def observation_inputs(valid, hours):
    if hours in (0,1,3,6):
        paths = [observation_path(valid)]
    else:
        paths = [observation_path(valid-timedelta(hours=offset)) for offset in range(0,hours,6)]
    return paths if all(path is not None for path in paths) else None


def observed_values(paths, grid, total, hours):
    column = 'T' if hours == 0 else f'RAIN{(hours if hours<=6 else 6):02d}H'
    arrays = []
    for path in paths:
        obs = pd.read_pickle(path)
        if obs.GRIDID.nunique() < math.ceil(total*.95) or column not in obs:
            raise ValueError('Observation coverage or accumulation is incomplete')
        a = pd.to_numeric(obs.drop_duplicates('GRIDID').set_index('GRIDID')[column],errors='coerce').reindex(grid.GRIDID).to_numpy(dtype=float,copy=True)
        a[(a < -100) if hours == 0 else (a < 0)] = np.nan
        arrays.append(a)
    return np.sum(arrays,axis=0)


def dataset_metadata(rows):
    result = []
    for ident in sorted({r['dataset'] for r in rows}):
        items = [r for r in rows if r['dataset']==ident]
        first = items[0]
        surface = ident.startswith('openmeteo-analysis-')
        rain = 'H' in first
        accumulation = int(ident.rsplit('-',1)[1]) if rain else None
        times = sorted({r['valid'] for r in items})
        common = dict(id=ident,models=[ec.MODEL],supported_models=[ec.MODEL],model_issues={},has_n=True,
                      start=times[0],end=times[-1],times=times,leads=sorted({r['lead'] for r in items}),
                      thresholds=THRESHOLDS if rain else [],updated=bjt(datetime.fromtimestamp(max(r['created'] for r in items),ec.UTC)),
                      level=None,accumulation=accumulation,
                      model_sources={ec.MODEL:dict(provider='Open-Meteo',api_model='ecmwf_ifs',native_grid='O1280',native_grid_km=9,
                          statistic='deterministic',elevation_correction=False,note='固定起报；原生9 km网格；未启用高程订正；CC BY 4.0')})
        if surface:
            source = first['reference_source']
            wide='-wide-' in ident
            element = 'wind100' if ident.endswith('wind100') else 'wind10' if ident.endswith('wind10') else 'mslp'
            height=100 if element=='wind100' else 10 if element=='wind10' else None
            common.update(kind='地面',source='analysis',reference=('ERA5 再分析' if source=='era5' else 'GDAS 分析场')+('' if wide else ' · 深圳及周边'),
                          region='108–120°E / 18–27°N' if wide else '113.6–114.8°E / 22.25–23°N',element=element,
                          label=f'{height} 米风场' if height else '海平面气压',height_m=height,
                          unit='m/s' if height else 'hPa',
                          source_note=('EC 9 km · Open-Meteo固定起报地面场，0.1°区域缓存线性插值到与原EC一致的108–120°E / 18–27°N、0.25°分析参考格点。风按10/100米高度分别配对；高空沿用原EC 0.25°，不重复作为9 km模式评分。' if wide else
                          '早期深圳小范围9 km缓存，单独评分；与原全区域通道分开。'))
        else:
            common.update(kind='降水' if rain else '气温',source='hk',reference='深圳开放数据格点分析场',region='深圳市行政区域',
                          source_note='ECMWF IFS HRES原生约9 km（O1280），Open-Meteo固定起报。大区域缓存由原生高斯行重采样到0.1°，气温线性、降水最近邻配对到同一深圳市域格点；早期小范围缓存直接从原生格点配对。不启用高程订正，不外推。降水只相加同一起报完整原生时段；12/24小时实况由完整6小时段相加。90小时后不拆出小时降水，144小时后不拆出3小时降水。ECMWF / Open-Meteo · CC BY 4.0。')
        result.append(common)
    return result


def refresh():
    now = datetime.now(ec.UTC)
    cutoff = now-timedelta(days=31)
    grid,total = city_grid()
    targets = grid[['lon','lat']].to_numpy()
    reference_sig = [signature(APP/'data/grid.xlsx'),signature(APP/'data/shenzhen_streets.geojson')]
    path = ec.ROOT/'score-memo.json'
    try:
        old = json.loads(path.read_text())
        memo = old['entries'] if old.get('revision')==REVISION else {}
    except (OSError,ValueError,KeyError):
        memo = {}
    entries, rows, errors = {}, [], 0
    for record in ec.runs():
        run = ec.run_time(record['id'])
        manifest = ec.read_manifest(run)
        wide=record.get('regional',False)
        source_sig = {k:v['sha256'] for k,v in manifest['segments'].items()}
        for lead in ec.available_leads(manifest):
            valid = run+timedelta(hours=lead)
            if not cutoff <= valid <= now-timedelta(hours=1):
                continue
            base = dict(model=ec.MODEL,run=bjt(run),valid=bjt(valid),lead=lead,
                        provider='Open-Meteo',native_grid_km=9,acquired_at=min(v['retrieved_at'] for v in manifest['segments'].values()))
            for hours in (0,*ec.RAIN_WINDOWS):
                if hours and (hours>lead or hours<ec.native_step(lead)):
                    continue
                inputs = observation_inputs(valid,hours)
                if not inputs:
                    continue
                key = f'{record["id"]}:{lead}:city:{hours}'
                sig = [source_sig,reference_sig,[signature(p) for p in inputs]]
                if key in memo and memo[key]['signature']==sig:
                    saved = memo[key]
                else:
                    try:
                        points,field = ec.native_field(run,lead,'temperature_2m') if hours==0 else ec.rain(run,lead,hours)
                        forecast = ec.interpolate(points,field,targets,nearest=hours!=0)
                        observed = observed_values(inputs,grid,total,hours)
                        mask = np.isfinite(forecast)&np.isfinite(observed)
                        if not mask.any():
                            continue
                        f,o = forecast[mask],observed[mask]
                        item = dict(base,n=int(mask.sum()),created=time.time(),dataset='openmeteo-气温-2m' if hours==0 else f'openmeteo-降水-{hours}')
                        if hours:
                            scored = [dict(item,threshold=t,H=int(((f>=t)&(o>=t)).sum()),M=int(((f<t)&(o>=t)).sum()),F=int(((f>=t)&(o<t)).sum())) for t in THRESHOLDS]
                        else:
                            scored = [dict(item,threshold=None,**scalar(f,o))]
                        saved = dict(signature=sig,rows=scored)
                    except (OSError,ValueError,KeyError,TypeError):
                        errors += 1
                        continue
                entries[key] = saved
                rows.extend(saved['rows'])
            if valid.hour%6:
                continue
            for source in ('gdas','era5'):
                reference = VERIFY/'cache/analysis-verification/references'/f'{source}_{valid:%Y%m%d%H}.npz'
                if not reference.is_file():
                    continue
                sig = [source_sig,signature(reference)]
                key = f'{record["id"]}:{lead}:surface:{source}'
                if key in memo and memo[key]['signature']==sig:
                    saved = memo[key]
                else:
                    try:
                        with np.load(reference,allow_pickle=False) as z:
                            meta = json.loads(str(z['meta'].item()))
                            if meta.get('version')!='analysis-fields-v1' or ec.stamp(meta['valid'])!=valid:
                                raise ValueError('Reference time or schema mismatch')
                            x,y = np.meshgrid(z['lon'],z['lat'])
                            target = np.column_stack((x.ravel(),y.ravel()))
                            observations = {k:z[k].ravel() for k in ('mslp','u10','v10','u100','v100') if k in z.files}
                        points,pressure = ec.native_field(run,lead,'pressure_msl')
                        _,u,v = ec.vector(run,lead)
                        predictions = {key:ec.interpolate(points,a,target) for key,a in [('mslp',pressure),('u10',u),('v10',v)]}
                        if wide:
                            for component in ('u','v'):
                                predictions[component+'100']=regional.sample(regional.field(run,lead,f'wind_{component}_component_100m'),target)
                        scored = []
                        for element,keys in [('mslp',['mslp']),('wind10',['u10','v10'])]+([('wind100',['u100','v100'])] if wide else []):
                            if not all(k in observations for k in keys):
                                continue
                            mask = np.logical_and.reduce([np.isfinite(predictions[k])&np.isfinite(observations[k]) for k in keys])
                            if not mask.any():
                                continue
                            metrics = scalar(predictions['mslp'][mask],observations['mslp'][mask]) if element=='mslp' else dict(VRMSE=float(np.sqrt(np.mean(sum((predictions[k][mask]-observations[k][mask])**2 for k in keys)))))
                            scored.append(dict(base,dataset=f'openmeteo-analysis-{"wide-" if wide else ""}{source}-{element}',reference_source=source,
                                               threshold=None,n=int(mask.sum()),created=time.time(),reference_preliminary=meta.get('preliminary',False),**metrics))
                        saved = dict(signature=sig,rows=scored)
                    except (OSError,ValueError,KeyError,TypeError):
                        errors += 1
                        continue
                entries[key] = saved
                rows.extend(saved['rows'])
    ec.atomic_json(path,dict(revision=REVISION,entries=entries))
    result = dict(revision=REVISION,datasets=dataset_metadata(rows),rows=rows,generated=ec.iso(now),validation_failures=errors)
    ec.atomic_json(ec.ROOT/'verification-catalog.json',result)
    print('Open-Meteo scores',len(rows),'datasets',len(result['datasets']),'validation failures',errors,flush=True)
    return result


def catalog():
    try:
        value = json.loads((ec.ROOT/'verification-catalog.json').read_text())
        if value.get('revision')!=REVISION or not isinstance(value.get('rows'),list) or not isinstance(value.get('datasets'),list):
            raise ValueError('Invalid score catalog')
        return value
    except (OSError,ValueError):
        return dict(datasets=[],rows=[])


def merge_into_catalog(extra,datasets,rows):
    """Join identical analysis channels; preserve the original score provenance."""
    aliases={}
    for item in extra['datasets']:
        original=item['id']
        target=None
        if original.startswith('openmeteo-analysis-wide-'):
            _,_,_,source,element=original.split('-')
            candidate=datasets.get(f'analysis-{source}-{element}')
            keys=('kind','reference','region','element','height_m','unit')
            if candidate and all(candidate.get(key)==item.get(key) for key in keys):target=candidate
        if target is None:
            datasets[original]=item;aliases[original]=original
            continue
        aliases[original]=target['id']
        for key in ('models','supported_models'):
            target[key]=list(dict.fromkeys([*target.get(key,[]),*item.get(key,[])]))
        target['model_sources']={**target.get('model_sources',{}),**item.get('model_sources',{})}
        target['source_note']=target.get('source_note','')+' '+item['source_note']
        target['updated']=max(target.get('updated',''),item.get('updated',''))
    rows.extend(dict(row,dataset=aliases[row['dataset']],source_dataset=row['dataset']) for row in extra['rows'])
    for ident in set(aliases.values()):
        items=[row for row in rows if row['dataset']==ident]
        if not items:continue
        item=datasets[ident]
        item['times']=sorted({row['valid'] for row in items})
        item['start'],item['end']=item['times'][0],item['times'][-1]
        item['leads']=sorted({row['lead'] for row in items})
