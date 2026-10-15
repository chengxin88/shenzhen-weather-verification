"""Local forecast verification against explicitly separated ERA5 / GDAS analyses.

Forecast acquisition is never triggered here. ERA5 uses existing daily GRIBs.
GDAS requests only five regional surface fields, capped at 128 KiB per cycle.
"""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import fcntl, json, os, re, sys, threading, time
import sys
if '/home/chengyang/gods-eye-weather/weather_backend' not in sys.path:sys.path.append('/home/chengyang/gods-eye-weather/weather_backend')
from global_store import field_directory
import numpy as np
from scipy.interpolate import RegularGridInterpolator

HERE = Path(__file__).resolve().parent
WEATHER = Path('/home/chengyang/gods-eye-weather/weather-data')
CACHE = HERE / 'cache' / 'analysis-verification'
UTC = timezone.utc
BJT = timezone(timedelta(hours=8))
BOUNDS = (108., 18., 120., 27.)
LEVELS = (925, 850, 700, 500, 300, 200)
LEADS = tuple(range(169))
WN3 = 'Google WeatherNext 3（集合均值）'
MODELS = ('ECMWF-IFS', 'NCEP-GFS', WN3)
APP_CACHE = Path('/home/chengyang/ec-gfs-server/cache')
NATIVE_MODELS = {'ecmwf':('ECMWF-IFS','ECMWF-IFS'), 'gfs':('NCEP-GFS','NCEP-GFS'),
                 'aifs':('ECAI','ECMWF-AIFS'), 'aigfs':('GFSAI','NCEP-AIGFS')}
SURFACE_MODELS = ('ECMWF-IFS','ECAI','NCEP-GFS','GFSAI',WN3)
PRESSURE_MODELS = ('ECMWF-IFS','ECAI','ECMWF-ENSEMBLES平均','NCEP-GFS','GFSAI','Google WeatherNext 2',WN3)
SPECS = {'mslp': ('海平面气压', ('mslp',), 'hPa', None),
         'wind10': ('10 米风场', ('u10', 'v10'), 'm/s', 10),
         'wind100': ('100 米风场', ('u100', 'v100'), 'm/s', 100)}
VERSION = 'analysis-fields-v1'
_thread = None
_guard = threading.Lock()

def stamp(value): return datetime.fromisoformat(value.replace('Z', '+00:00'))
def iso(value): return value.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
def bjt(value): return value.astimezone(BJT).isoformat()[:19]
def signature(path):
    if not path.is_file(): return None
    s = path.stat()
    return [str(path), s.st_mtime_ns, s.st_size]

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))
    os.replace(tmp, path)

def save_reference(path, valid, source, lon, lat, fields, inputs, preliminary=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = dict(version=VERSION, valid=iso(valid), source=source, bounds=BOUNDS,
                inputs=inputs, preliminary=preliminary, fields=list(fields))
    tmp = path.with_suffix('.tmp')
    with tmp.open('wb') as handle:
        np.savez_compressed(handle, lon=lon, lat=lat, meta=np.array(json.dumps(meta)), **fields)
    os.replace(tmp, path)

def grib_grid(message):
    from eccodes import codes_get as get, codes_get_values
    if (get(message, 'gridType') != 'regular_ll' or get(message, 'jPointsAreConsecutive')
        or get(message, 'alternativeRowScanning')): raise ValueError('Unsupported analysis grid')
    dx, dy = get(message, 'iDirectionIncrementInDegrees'), get(message, 'jDirectionIncrementInDegrees')
    if dx != .25 or dy != .25: raise ValueError('Analysis must be 0.25 degrees')
    nx, ny = get(message, 'Ni'), get(message, 'Nj')
    xs = (get(message, 'longitudeOfFirstGridPointInDegrees') + np.arange(nx)*dx*(-1 if get(message,'iScansNegatively') else 1)) % 360
    ys = get(message, 'latitudeOfFirstGridPointInDegrees') + np.arange(ny)*dy*(1 if get(message,'jScansPositively') else -1)
    ix = np.flatnonzero((xs >= BOUNDS[0]) & (xs <= BOUNDS[2])); ix = ix[np.argsort(xs[ix])]
    iy = np.flatnonzero((ys >= BOUNDS[1]) & (ys <= BOUNDS[3])); iy = iy[np.argsort(ys[iy])]
    lon, lat = xs[ix], ys[iy]
    if (len(lon) != 49 or len(lat) != 37 or not np.allclose(lon, np.arange(49)*.25+108)
        or not np.allclose(lat, np.arange(37)*.25+18)): raise ValueError('Analysis region incomplete')
    values = codes_get_values(message).reshape(ny,nx)[np.ix_(iy,ix)].copy()
    values[(values == get(message,'missingValue')) | ~np.isfinite(values)] = np.nan
    return lon, lat, values

def grib_time(message):
    from eccodes import codes_get as get
    valid = datetime.strptime(f"{get(message,'validityDate')}{get(message,'validityTime'):04d}", '%Y%m%d%H%M').replace(tzinfo=UTC)
    run = datetime.strptime(f"{get(message,'dataDate')}{get(message,'dataTime'):04d}", '%Y%m%d%H%M').replace(tzinfo=UTC)
    if run != valid or float(get(message,'endStep')) != 0 or get(message,'stepType') != 'instant':
        raise ValueError('Reference is not an instantaneous analysis')
    return valid

def decode_gdas(path, valid):
    from eccodes import codes_grib_new_from_file, codes_get as get, codes_release
    fields = {}; lon = lat = None
    with path.open('rb') as handle:
        while (message := codes_grib_new_from_file(handle)) is not None:
            try:
                if grib_time(message) != valid: raise ValueError('GDAS validity mismatch')
                level, height, name = get(message,'typeOfLevel'), get(message,'level'), get(message,'shortName')
                if level == 'meanSea' and name in ('prmsl','msl'): key, unit = 'mslp', 'Pa'
                elif level == 'heightAboveGround' and height in (10,100) and name in ('u','v','10u','10v','100u','100v'):
                    key, unit = name[-1]+str(height), 'm s**-1'
                else: raise ValueError('Unexpected GDAS field')
                if get(message,'units') != unit or key in fields: raise ValueError('GDAS units or duplicate field')
                lon, lat, a = grib_grid(message)
                fields[key] = a/100 if key == 'mslp' else a
            finally: codes_release(message)
    if not {'mslp','u10','v10'} <= fields.keys(): raise ValueError('GDAS surface fields incomplete')
    return lon, lat, fields

def prepare_era5(valids):
    """Decode each locally cached day once; preserve analysis identity and terrain mask."""
    from eccodes import codes_grib_new_from_file, codes_get as get, codes_release
    produced = 0
    for day in sorted({v.strftime('%Y%m%d') for v in valids}):
        files = [WEATHER/'era5-daily'/f'era5_{day}_{part}.grib' for part in ('sfc','pl')]
        if not all(p.is_file() for p in files): continue
        inputs = [signature(p) for p in files]
        targets = {v for v in valids if v.strftime('%Y%m%d') == day}
        needed = set()
        for valid in targets:
            p = CACHE/'references'/f'era5_{valid:%Y%m%d%H}.npz'
            try:
                with np.load(p,allow_pickle=False) as z: current = json.loads(str(z['meta']))
                if current.get('version') == VERSION and current.get('inputs') == inputs: continue
            except (OSError,ValueError,KeyError): pass
            needed.add(valid)
        if not needed: continue
        values = {}; preliminary = {}; lon = lat = None
        for path in files:
            with path.open('rb') as handle:
                while (message := codes_grib_new_from_file(handle)) is not None:
                    try:
                        valid = grib_time(message)
                        if valid not in needed: continue
                        param, level = get(message,'paramId'), get(message,'typeOfLevel')
                        specs = {151:('mslp','Pa'),134:('sp','Pa'),165:('u10','m s**-1'),166:('v10','m s**-1'),228246:('u100','m s**-1'),228247:('v100','m s**-1')}
                        if path == files[0]:
                            if param not in specs: continue
                            key, unit = specs[param]
                        else:
                            height = int(get(message,'level'))
                            if level != 'isobaricInhPa' or height not in LEVELS or param not in (129,131,132): continue
                            key = {129:'z',131:'u',132:'v'}[param]+str(height)
                            unit = 'm**2 s**-2' if param == 129 else 'm s**-1'
                        if get(message,'units') != unit: raise ValueError('ERA5 units mismatch')
                        fields = values.setdefault(valid,{})
                        if key in fields: raise ValueError('Duplicate ERA5 field')
                        lon, lat, a = grib_grid(message)
                        fields[key] = a/100 if key == 'mslp' else a/9.80665 if key.startswith('z') else a
                        preliminary[valid] = preliminary.get(valid,False) or str(get(message,'experimentVersionNumber')) in ('5','0005')
                    finally: codes_release(message)
        for valid, fields in values.items():
            if not {'mslp','u10','v10','sp'} <= fields.keys(): continue
            for level in LEVELS:
                for key in (f'z{level}',f'u{level}',f'v{level}'):
                    if key in fields: fields[key][~np.isfinite(fields['sp']) | (level*100 > fields['sp'])] = np.nan
            save_reference(CACHE/'references'/f'era5_{valid:%Y%m%d%H}.npz', valid, 'ERA5 再分析', lon, lat, fields, inputs, preliminary.get(valid,False))
            produced += 1
        print('ERA5 references',day,len(values),flush=True)
    return produced

def fetch_gdas(valids, limit):
    import requests
    attempts_path = CACHE/'fetch-attempts.json'
    try: attempts = json.loads(attempts_path.read_text())
    except (OSError,ValueError): attempts = {}
    session = requests.Session(); session.trust_env = False
    proxy = os.getenv('ANALYSIS_REFERENCE_PROXY','http://127.0.0.1:17908')
    if proxy: session.proxies={'https':proxy}
    count = total = 0
    for valid in sorted(valids,reverse=True):
        path = CACHE/'references'/f'gdas_{valid:%Y%m%d%H}.npz'
        if path.exists() or time.time()-attempts.get(iso(valid),0)<1800: continue
        if count >= limit: break
        count += 1; attempts[iso(valid)] = time.time()
        params = dict(file=f'gdas.t{valid:%H}z.pgrb2.0p25.f000',dir=f'/gdas.{valid:%Y%m%d}/{valid:%H}/atmos',
                      subregion='',leftlon=108,rightlon=120,bottomlat=18,toplat=27,var_PRMSL='on',var_UGRD='on',var_VGRD='on',
                      lev_mean_sea_level='on',lev_10_m_above_ground='on',lev_100_m_above_ground='on')
        temp = CACHE/f'gdas_{valid:%Y%m%d%H}.download'
        try:
            with session.get('https://nomads.ncep.noaa.gov/cgi-bin/filter_gdas_0p25.pl',params=params,timeout=(10,25),stream=True) as response:
                response.raise_for_status(); content = bytearray()
                for part in response.iter_content(16384):
                    content.extend(part)
                    if len(content)>131072: raise ValueError('Regional reference exceeded size limit')
            if not content.startswith(b'GRIB'): raise ValueError('Not GRIB data')
            temp.write_bytes(content)
            lon,lat,fields = decode_gdas(temp,valid)
            save_reference(path,valid,'GDAS 分析场',lon,lat,fields,{'regional_bytes':len(content)})
            total += len(content)
            print('GDAS reference',iso(valid),len(content),list(fields),flush=True)
        except Exception as exc: print('GDAS reference unavailable',iso(valid),type(exc).__name__,flush=True)
        finally: temp.unlink(missing_ok=True)
    atomic_json(attempts_path,attempts)
    return total

def candidates(now):
    from native_archive import wn3_manifest
    found = []
    for slug,(model,file_model) in NATIVE_MODELS.items():
        for path in sorted((WEATHER/'region-v4'/slug).glob(f'{slug}_??????????_f???_global1.json')):
            match = re.fullmatch(slug+r'_(\d{10})_f(\d{3})_global1.json',path.name)
            run = datetime.strptime(match[1],'%Y%m%d%H').replace(tzinfo=UTC); lead=int(match[2]); valid=run+timedelta(hours=lead)
            if lead in LEADS and now-timedelta(days=31)<=valid<=now-timedelta(hours=6):
                found.append(dict(path=path,model=model,file_model=file_model,run=run,lead=lead,valid=valid,slug=slug))
    root = WEATHER/'region-v4/weathernext3'
    for path in sorted(root.glob('run_??????????.json')):
        try: run,directory,committed = wn3_manifest(path)
        except Exception: continue
        for lead in LEADS:
            valid=run+timedelta(hours=lead)
            if lead in committed and valid.hour%6==0 and now-timedelta(days=31)<=valid<=now-timedelta(hours=6):
                found.append(dict(path=path,model=WN3,run=run,lead=lead,valid=valid,directory=directory))
    # Preserve older local 500-hPa archives, and use the existing 50-member
    # EC mean cache. These readers never trigger forecast downloads.
    for model in ('ECAI','GFSAI','ECMWF-ENSEMBLES平均'):
        ensemble=model=='ECMWF-ENSEMBLES平均'
        archive=APP_CACHE/'ECMWF-ENSEMBLES' if ensemble else APP_CACHE/'AI_CIRCULATION'/model
        for directory in sorted(archive.glob('??????????')):
            if not re.fullmatch(r'\d{10}',directory.name):continue
            run=datetime.strptime(directory.name,'%Y%m%d%H').replace(tzinfo=UTC)
            if not now-timedelta(days=34)<=run<=now:continue
            paths=(directory/'maps').glob('ensemble_circulation_f*_*.npz') if ensemble else directory.glob('*hPa_f*.grib2')
            for path in sorted(paths):
                pattern=r'ensemble_circulation_f(\d{3})_(\d+)hPa\.npz' if ensemble else r'(\d+)hPa_f(\d{3})\.grib2'
                match=re.fullmatch(pattern,path.name)
                if not match:continue
                lead,level=map(int,match.groups() if ensemble else reversed(match.groups()))
                valid=run+timedelta(hours=lead)
                if level in LEVELS and lead in LEADS and now-timedelta(days=31)<=valid<=now-timedelta(hours=6):
                    found.append(dict(path=path,model=model,run=run,lead=lead,valid=valid,level=level,
                                      archive='ensemble' if ensemble else 'ai-grib'))
    return found

def forecast_keys(element,level):
    if level is not None: return [f'{level}_gh',f'{level}_u',f'{level}_v']
    return {'mslp':['surface_msl'],'wind10':['surface_u','surface_v'],'wind100':['surface_u100','surface_v100']}[element]

def input_paths(candidate,element,level):
    p=candidate['path']; keys=forecast_keys(element,level)
    if candidate.get('archive'):return [p]
    if candidate['model'] != WN3: return [p]+[field_directory(p)/(key+'.npy') for key in keys]
    root=p.parent;run=candidate['run'].strftime('%Y%m%d%H')
    paths=[p]+[root/candidate['directory']/run/f"f{candidate['lead']:03}_{key}.npz" for key in keys]
    if element=='wind100': paths.append(root/'native-extras-v1'/f'run_{run}.json')
    return paths

def archive_forecast(candidate,level):
    """Read only trusted local files, checking time/level/units in AI GRIBs."""
    p=candidate['path']
    if level!=candidate['level']:raise ValueError('Archive pressure level mismatch')
    if candidate['valid']!=candidate['run']+timedelta(hours=candidate['lead']):raise ValueError('Archive valid time mismatch')
    if candidate['archive']=='ensemble':
        expected=APP_CACHE/'ECMWF-ENSEMBLES'/candidate['run'].strftime('%Y%m%d%H')/'maps'/f"ensemble_circulation_f{candidate['lead']:03d}_{level}hPa.npz"
        if p!=expected:raise ValueError('Ensemble cache identity mismatch')
        # This app-owned cache is produced by ecmwf_ensembles.py from 50
        # perturbation members; its run/lead identity is encoded in the path.
        with np.load(p,allow_pickle=False) as z:
            if not {'lon','lat','gh','t','u','v','height_spread'}<=set(z.files):raise ValueError('Ensemble cache incomplete')
            x,y=np.asarray(z['lon'],float),np.asarray(z['lat'],float)
            if x.ndim!=1 or y.ndim!=1:raise ValueError('Ensemble grid axes mismatch')
            ix=np.argsort(x);iy=np.argsort(y);lon,lat=x[ix],y[iy]
            if len(lon)!=49 or len(lat)!=37 or not np.allclose(lon,np.arange(49)*.25+108) or not np.allclose(lat,np.arange(37)*.25+18):raise ValueError('Ensemble grid mismatch')
            arrays=[]
            for key in ('gh','u','v'):
                field=np.asarray(z[key],float)
                if field.shape!=(len(y),len(x)):raise ValueError('Ensemble field shape mismatch')
                arrays.append(field[np.ix_(iy,ix)])
        return lon,lat,arrays
    from eccodes import codes_grib_new_from_file,codes_get as get,codes_release
    expected=APP_CACHE/'AI_CIRCULATION'/candidate['model']/candidate['run'].strftime('%Y%m%d%H')/f"{level}hPa_f{candidate['lead']:03d}.grib2"
    if candidate['model'] not in ('ECAI','GFSAI') or p!=expected:raise ValueError('AI archive identity mismatch')
    source_sig=signature(p)
    cache=CACHE/'forecast-fields'/f"{candidate['model']}_{candidate['run']:%Y%m%d%H}_f{candidate['lead']:03}_{level}.npz"
    try:
        with np.load(cache,allow_pickle=False) as z:
            if json.loads(str(z['signature']))==source_sig:
                return z['lon'],z['lat'],[z[k] for k in ('gh','u','v')]
    except (OSError,ValueError,KeyError):pass
    fields={};lon=lat=None
    with p.open('rb') as handle:
        while (message:=codes_grib_new_from_file(handle)) is not None:
            try:
                run=datetime.strptime(f"{get(message,'dataDate')}{get(message,'dataTime'):04d}",'%Y%m%d%H%M').replace(tzinfo=UTC)
                valid=datetime.strptime(f"{get(message,'validityDate')}{get(message,'validityTime'):04d}",'%Y%m%d%H%M').replace(tzinfo=UTC)
                if run!=candidate['run'] or valid!=candidate['valid'] or get(message,'stepUnits')!=1 or get(message,'endStep')!=candidate['lead'] or get(message,'stepType')!='instant':raise ValueError('AI forecast time mismatch')
                if get(message,'typeOfLevel')!='isobaricInhPa' or get(message,'level')!=level:raise ValueError('AI forecast level mismatch')
                name=get(message,'shortName');unit=get(message,'units')
                if name not in ('gh','z','u','v'):continue
                key='gh' if name=='z' else name
                if key in fields:raise ValueError('Duplicate AI field')
                units={'gh':('gpm','m'),'z':('m**2 s**-2',),'u':('m s**-1',),'v':('m s**-1',)}
                if unit not in units[name]:raise ValueError('AI forecast units mismatch')
                lon,lat,a=grib_grid(message);fields[key]=a/9.80665 if name=='z' else a
            finally:codes_release(message)
    if set(fields)!={'gh','u','v'}:raise ValueError('AI pressure fields incomplete')
    cache.parent.mkdir(parents=True,exist_ok=True)
    temp=cache.with_suffix('.tmp')
    with temp.open('wb') as handle:np.savez_compressed(handle,lon=lon,lat=lat,signature=np.array(json.dumps(source_sig)),**fields)
    os.replace(temp,cache)
    return lon,lat,[fields[k] for k in ('gh','u','v')]


def forecast(candidate, element, level):
    p=candidate['path']; keys=forecast_keys(element,level);arrays=[]
    if candidate.get('archive'):
        if element is not None:raise ValueError('Pressure archive cannot supply surface fields')
        return archive_forecast(candidate,level)
    if candidate['model']==WN3:
        if level is not None: raise ValueError('WN3 pressure fields not connected')
        from native_archive import WN3_SOURCES
        sys.path[:0]=[s for s in ['/home/chengyang/gods-eye-weather/weather_backend','/home/chengyang/ec-gfs-server'] if s not in sys.path]
        from weathernext3_native_cache import china_field,extra_available,VARIABLES
        if element=='wind100' and not extra_available(p.parent,candidate['run'],candidate['lead']): raise ValueError('100m hour not committed')
        schema,bounds=WN3_SOURCES[candidate['directory']]
        for key in keys:
            result=china_field(p.parent,candidate['run'],candidate['lead'],key,BOUNDS,native_bounds=bounds,directory=candidate['directory'],schema=schema)
            if result is None: raise ValueError('Missing native field')
            path=p.parent/candidate['directory']/candidate['run'].strftime('%Y%m%d%H')/f"f{candidate['lead']:03}_{key}.npz"
            with np.load(path,allow_pickle=False) as z: meta=json.loads(str(z['meta']))
            if meta.get('source_variable')!=VARIABLES[key][0] or not re.fullmatch('[0-9a-f]{64}',str(meta.get('source_sha256',''))): raise ValueError('WN3 provenance mismatch')
            lon,lat,a=result;arrays.append(a)
    else:
        d=json.loads(p.read_text())
        if (d.get('schema')!=2 or d.get('model')!=candidate.get('file_model',candidate['model']) or d.get('storage') not in (candidate['slug']+'-global-v1',candidate['slug']+'-regional-v1')
            or stamp(d['run'])!=candidate['run'] or stamp(d['valid'])!=candidate['valid'] or d.get('lead')!=candidate['lead']
            or d.get('native_grid_degrees')!=.25): raise ValueError('Forecast identity mismatch')
        x,y=np.asarray(d['lon']),np.asarray(d['lat'])
        expected=((49,37) if d.get('bounds')==[108.,18.,120.,27.] else (361,241)) if d['storage'].endswith('-regional-v1') else (1441,721)
        if (len(x),len(y))!=expected or x[0]>BOUNDS[0] or x[-1]<BOUNDS[2] or y[0]>BOUNDS[1] or y[-1]<BOUNDS[3] or not np.allclose(np.diff(x),.25) or not np.allclose(np.diff(y),.25): raise ValueError('Forecast grid mismatch')
        ix=np.flatnonzero((x>=BOUNDS[0])&(x<=BOUNDS[2]));iy=np.flatnonzero((y>=BOUNDS[1])&(y<=BOUNDS[3]));lon,lat=x[ix],y[iy]
        for key in keys:
            a=np.load(field_directory(p)/(key+'.npy'),allow_pickle=False,mmap_mode='r')
            if a.shape!=(len(y),len(x)): raise ValueError('Forecast shape mismatch')
            arrays.append(np.asarray(a[np.ix_(iy,ix)],float))
    if element=='mslp' and (np.nanmin(arrays[0])<500 or np.nanmax(arrays[0])>1150): raise ValueError('Pressure unit/range mismatch')
    return lon,lat,arrays

def scalar_scores(f,o):
    error=f-o
    return dict(ME=float(error.mean()),MAE=float(np.abs(error).mean()),RMSE=float(np.sqrt(np.mean(error**2))),
                CC=float(np.corrcoef(f,o)[0,1]) if np.std(f)>1e-12 and np.std(o)>1e-12 else None)

def pair_fields(forecast_data, reference, element, level):
    lon,lat,arrays=forecast_data; x,y,fields=reference
    keys=[f'z{level}',f'u{level}',f'v{level}'] if level is not None else SPECS[element][1]
    if not all(key in fields for key in keys): raise ValueError('Same-height reference missing')
    observations=[fields[key] for key in keys]
    target_x,target_y=np.meshgrid(x,y);points=np.column_stack([target_y.ravel(),target_x.ravel()])
    predictions=[RegularGridInterpolator((lat,lon),a,method='linear',bounds_error=False,fill_value=np.nan)(points) for a in arrays]
    mask=np.logical_and.reduce([np.isfinite(a.ravel()) for a in observations])
    mask &= np.logical_and.reduce([np.isfinite(a) for a in predictions])
    return predictions,observations,mask

def paired_scores(forecast_data, reference, element, level, common_mask=None):
    predictions,observations,mask=pair_fields(forecast_data,reference,element,level)
    if common_mask is not None: mask &= common_mask
    return score_pairs(predictions,observations,mask,element,level)

def score_pairs(predictions,observations,mask,element,level):
    if not mask.any(): raise ValueError('No paired reference grid cells')
    p=[a[mask] for a in predictions];o=[a.ravel()[mask] for a in observations]
    scores=scalar_scores(p[0],o[0]) if level is not None or element=='mslp' else {}
    if level is not None or element.startswith('wind'):
        scores['VRMSE']=float(np.sqrt(np.mean((p[-2]-o[-2])**2+(p[-1]-o[-1])**2)))
    return dict(scores,n=int(mask.sum()))

def datasets_for(rows,now):
    result=[]
    for reference,source in [('ERA5 再分析','era5'),('GDAS 分析场','gdas')]:
        source_times=[r['valid'] for r in rows if r['dataset'].startswith(f'analysis-{source}-')]
        for element,level in [(k,None) for k in SPECS]+([(None,l) for l in LEVELS] if source=='era5' else []):
            ident=f'analysis-{source}-{element or level}';items=[r for r in rows if r['dataset']==ident]
            label=SPECS[element][0] if element else f'{level} hPa'
            supported=list(SURFACE_MODELS if element else PRESSURE_MODELS)
            models=[m for m in supported if any(r['model']==m for r in items)]
            issues={}
            if not element:
                issues={'Google WeatherNext 2':'本地无高空资料',WN3:'本地仅接入地面场'}
                if 'ECMWF-ENSEMBLES平均' not in models:issues['ECMWF-ENSEMBLES平均']='该层暂无本地集合场'
            note=('ERA5 再分析通常延迟约5天；近期 ERA5T 为初步资料。' if source=='era5' else 'GDAS 同有效时刻 f00 分析场。')
            note+='预报双线性插值到固定0.25°分析网格；同一时效、有效时间和要素的已接入模式使用共同有效格点。'
            note+='风场按离地高度配对。' if element and element.startswith('wind') else '等压面地下格点已按ERA5地面气压屏蔽。' if level else '海平面气压已统一为hPa。'
            empty='当前没有已配对评分；不会使用其他高度或参考资料替代。'
            if source=='era5' and element=='wind100' and not items: empty='本地ERA5尚未缓存100米风参考场；可切换GDAS查看已有100米风评分。'
            result.append(dict(id=ident,kind='地面' if element else '环流',element=element,label=label,source='analysis',reference=reference,
                accumulation=None,level=level,height_m=SPECS[element][3] if element else None,unit=SPECS[element][2] if element else 'm',
                models=models,supported_models=supported,model_issues=issues,has_n=True,region='108–120°E / 18–27°N',source_note=note,availability_note=empty,
                start=min((r['valid'] for r in items),default=min(source_times,default=bjt(now))),
                end=max((r['valid'] for r in items),default=max(source_times,default=bjt(now))),
                leads=sorted({r['lead'] for r in items}) or [],thresholds=[],times=sorted({r['valid'] for r in items}),
                updated=bjt(datetime.fromtimestamp(max((r['created'] for r in items),default=now.timestamp()),UTC))))
    return result

def refresh(fetch_limit=4):
    CACHE.mkdir(parents=True,exist_ok=True)
    with (CACHE/'worker.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return
        now=datetime.now(UTC); jobs=candidates(now);valids={j['valid'] for j in jobs}
        prepare_era5(valids)
        transferred=fetch_gdas({v for v in valids if v>=now-timedelta(days=7) and v.hour%6==0},fetch_limit)
        try: old=json.loads((CACHE/'memo.json').read_text());memo=old['entries'] if old.get('version')==VERSION else {}
        except (OSError,ValueError,KeyError):memo={}
        updated={};rows=[];errors=0;groups={}
        @lru_cache(maxsize=64)
        def reference_data(name):
            with np.load(name,allow_pickle=False) as z:
                meta=json.loads(str(z['meta']))
                if meta.get('version')!=VERSION:raise ValueError('Reference schema mismatch')
                return z['lon'],z['lat'],{key:z[key] for key in meta['fields']},meta
        for j in jobs:
            for source in ('gdas','era5'):
                ref=CACHE/'references'/f"{source}_{j['valid']:%Y%m%d%H}.npz"
                if not ref.exists():continue
                x,y,fields,metadata=reference_data(str(ref))
                if stamp(metadata['valid'])!=j['valid']:continue
                specs=([(None,j['level'])] if source=='era5' else []) if j.get('archive') else [(e,None) for e in SPECS]+([(None,l) for l in LEVELS] if source=='era5' and j['model']!=WN3 else [])
                for element,level in specs:
                    paths=input_paths(j,element,level)
                    if any(not p.is_file() for p in paths):continue
                    refkeys=[f'z{level}',f'u{level}',f'v{level}'] if level else SPECS[element][1]
                    if not all(k in fields for k in refkeys):continue
                    group=(source,element,level,j['valid'],j['lead'])
                    entries=groups.setdefault(group,[])
                    # Native exact-run fields take precedence over older GRIB
                    # copies of the same forecast, without duplicate scores.
                    if not any(old['model']==j['model'] for old,_ in entries):entries.append((j,paths))
        print('Scoring groups',len(groups),'forecast candidates',len(jobs),flush=True)
        def compute_group(item):
            (source,element,level,valid,lead),entries=item
            ref=CACHE/'references'/f'{source}_{valid:%Y%m%d%H}.npz'
            x,y,fields,metadata=reference_data(str(ref));ident=f'analysis-{source}-{element or level}'
            sig=[signature(ref)]+[[j['model'],*[signature(p) for p in paths]] for j,paths in entries]
            keys=[f"{ident}:{j['model']}:{iso(j['run'])}:{j['lead']}" for j,_ in entries]
            if all(key in memo and memo[key]['signature']==sig for key in keys):
                return {key:memo[key] for key in keys},0
            paired=[];failures=0
            for (j,_),key in zip(entries,keys):
                try:
                    p,o,mask=pair_fields(forecast(j,element,level),(x,y,fields),element,level)
                    paired.append((j,key,p,o,mask))
                except (ValueError,KeyError,TypeError,OSError):failures+=1
            if not paired:return {},failures
            common=np.logical_and.reduce([item[-1] for item in paired])
            if not common.any():return {},failures+len(paired)
            result={}
            for j,key,p,o,_ in paired:
                scores=score_pairs(p,o,common,element,level)
                row=dict(dataset=ident,model=j['model'],run=bjt(j['run']),valid=bjt(j['valid']),lead=j['lead'],threshold=None,
                         created=time.time(),reference_preliminary=metadata.get('preliminary',False),**scores)
                result[key]=dict(signature=sig,row=row)
            return result,failures
        # Groups are independent; keep their model masks intact. Bounded workers
        # overlap local file I/O, while the main thread alone commits the caches.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as executor:
            for index,(entries,failures) in enumerate(executor.map(compute_group,groups.items())):
                updated.update(entries);rows.extend(item['row'] for item in entries.values());errors+=failures
                if index%100==0:print('Scored groups',index+1,'/',len(groups),'rows',len(rows),'validation failures',errors,flush=True)
        atomic_json(CACHE/'memo.json',dict(version=VERSION,entries=updated))
        out=dict(version=VERSION,datasets=datasets_for(rows,now),rows=rows,generated=iso(now),validation_failures=errors,reference_download_bytes=transferred)
        atomic_json(CACHE/'catalog.json',out)
        print(json.dumps(dict(rows=len(rows),errors=errors,download_bytes=transferred,coverage=[dict(id=d['id'],rows=sum(r['dataset']==d['id'] for r in rows),models=d['models'],end=d['end']) for d in out['datasets']]),ensure_ascii=False),flush=True)
        return out

def catalog():
    global _thread
    path=CACHE/'catalog.json'
    try: data=json.loads(path.read_text())
    except (OSError,ValueError):data=dict(datasets=datasets_for([],datetime.now(UTC)),rows=[])
    if not path.exists() or time.time()-path.stat().st_mtime>300:
        with _guard:
            if _thread is None or not _thread.is_alive():
                _thread=threading.Thread(target=refresh,daemon=True);_thread.start()
    return data

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--fetch-limit',type=int,default=4)
    refresh(parser.parse_args().fetch_limit)
