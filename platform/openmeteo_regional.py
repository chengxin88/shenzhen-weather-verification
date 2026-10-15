"""Regional EC surface cache from Open-Meteo's public, immutable run files.

Original O1280 values are resampled to a 0.1 degree display/cache grid. The
native temporal steps, physical units and exact reference time are preserved.
Pressure layers are read separately from the existing ECMWF 0.25 degree cache.
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time

import numpy as np
import requests
from omfiles import OmFileReader
from scipy.interpolate import RegularGridInterpolator

import openmeteo_ecmwf as ec

BACKEND='/home/chengyang/gods-eye-weather/weather_backend'
if BACKEND not in sys.path:sys.path.append(BACKEND)

SCHEMA='openmeteo-ifs-regional-v2'
BOUNDS=[60.,0.,150.,60.]
LON=np.arange(600,1501,dtype=float)/10
LAT=np.arange(0,601,dtype=float)/10
BASE='https://openmeteo.s3.amazonaws.com/'
UPPER=Path('/home/chengyang/gods-eye-weather/weather-data/region-v4/ecmwf')
SPECS={
    'temperature_2m':('°C',-100,70), 'dew_point_2m':('°C',-110,70),
    'pressure_msl':('hPa',500,1150), 'precipitation':('mm',0,3000),
    'wind_u_component_10m':('m/s',-180,180), 'wind_v_component_10m':('m/s',-180,180),
    'wind_u_component_100m':('m/s',-180,180), 'wind_v_component_100m':('m/s',-180,180),
    'total_column_integrated_water_vapour':('kg/m²',0,200),
}


def root(): return ec.ROOT/'regional'
def directory(ident): return root()/ec.run_id(ident)
def exists(ident): return (directory(ident)/'run.json').is_file()
def urlbase(run): return BASE+'data_run/ecmwf_ifs/'+ec.run_time(run).strftime('%Y/%m/%d/%H00Z/')


def session():
    value=requests.Session();value.trust_env=False
    proxy=os.getenv('OPENMETEO_EC_PROXY','http://127.0.0.1:17908')
    if proxy:value.proxies={'http':proxy,'https':proxy}
    return value


class RangeSource:
    """Bounded range cache; If-Match prevents combining versions of an object."""
    WIDTH=4*1024*1024
    def __init__(self):
        self.session=session();self.cache=OrderedDict();self.objects={};self.bytes=0
    def size(self,path):
        if path not in self.objects:
            response=self.session.head(path,timeout=(10,45));response.raise_for_status()
            length=int(response.headers['Content-Length'])
            if not 1000<=length<=3_000_000_000:raise ValueError('Unexpected OM object size')
            self.objects[path]=(length,response.headers['ETag'])
        return self.objects[path][0]
    def cat_file(self,path,start=None,end=None):
        length=self.size(path);start=0 if start is None else start;end=length if end is None else end
        if not 0<=start<=end<=length:raise ValueError('Invalid source byte range')
        if start==end:return b''
        result=[]
        for i in range(start//self.WIDTH,(end-1)//self.WIDTH+1):
            key=(path,i)
            if key not in self.cache:
                a,b=i*self.WIDTH,min((i+1)*self.WIDTH,length)
                for attempt in range(3):
                    try:
                        response=self.session.get(path,headers={'Range':f'bytes={a}-{b-1}',
                            'If-Match':self.objects[path][1]},timeout=(10,60))
                        response.raise_for_status()
                        if (response.status_code!=206 or len(response.content)!=b-a or
                            response.headers.get('Content-Range')!=f'bytes {a}-{b-1}/{length}' or
                            response.headers.get('ETag')!=self.objects[path][1]):
                            raise ValueError('Object range or version mismatch')
                        self.cache[key]=response.content;self.bytes+=b-a
                        break
                    except requests.RequestException:
                        if attempt==2:raise
                if len(self.cache)>8:self.cache.popitem(last=False)
            self.cache.move_to_end(key)
            block=self.cache[key]
            result.append(block[max(0,start-i*self.WIDTH):min(len(block),end-i*self.WIDTH)])
        return b''.join(result)


def manifest(ident):
    data=json.loads((directory(ident)/'run.json').read_text())
    if (data.get('schema')!=SCHEMA or data.get('run')!=ec.iso(ec.run_time(ident)) or
        data.get('bounds')!=BOUNDS or data.get('model')!=ec.MODEL or data.get('grid_degrees')!=.1 or
        not set(SPECS)<=set(data.get('fields',{}))):raise ValueError('Regional run identity mismatch')
    expected=expected_leads(ident)
    if data.get('leads')!=expected:raise ValueError('Regional time axis mismatch')
    return data


def expected_leads(ident):
    end=168 if ec.run_time(ident).hour in (0,12) else 144
    return list(range(91))+list(range(93,145,3))+(list(range(150,end+1,6)) if end>144 else [])


@lru_cache(maxsize=24)
def field_file(ident,name,digest):
    if name not in SPECS or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Invalid regional field')
    path=directory(ident)/(name+'.npy')
    h=hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(4*1024*1024),b''):h.update(block)
    if h.hexdigest()!=digest:raise ValueError('Regional field checksum mismatch')
    array=np.load(path,allow_pickle=False,mmap_mode='r')
    if array.dtype!=np.float32 or array.shape!=(len(expected_leads(ident)),len(LAT),len(LON)):
        raise ValueError('Regional array dimensions mismatch')
    return array


def field(ident,lead,name):
    data=manifest(ident)
    try:index=data['leads'].index(lead)
    except ValueError:raise ValueError('Unavailable original forecast step') from None
    def read(key):return field_file(ec.run_id(ident),key,data['fields'][key]['sha256'])[index]
    if name=='relative_humidity_2m':
        t,td=read('temperature_2m'),read('dew_point_2m')
        return np.clip(100*np.exp(17.625*td/(243.04+td)-17.625*t/(243.04+t)),0,100)
    if name in ('wind_speed_10m','wind_direction_10m','wind_speed_100m','wind_direction_100m'):
        height=100 if name.endswith('100m') else 10
        u,v=read(f'wind_u_component_{height}m'),read(f'wind_v_component_{height}m')
        return np.hypot(u,v) if name.startswith('wind_speed') else (np.degrees(np.arctan2(-u,-v))+360)%360
    return read(name)


@lru_cache(maxsize=1)
def points():
    x,y=np.meshgrid(LON,LAT)
    return np.column_stack((x.ravel(),y.ravel()))


def sample(array,targets,nearest=False):
    return RegularGridInterpolator((LAT,LON),array,method='nearest' if nearest else 'linear',
        bounds_error=False,fill_value=np.nan)(np.asarray(targets)[:,::-1])


def normalize_values(name,unit,values):
    values=np.asarray(values,dtype=np.float32)
    if name=='pressure_msl' and unit=='Pa':values=values/100
    low,high=SPECS[name][1:]
    if np.isinf(values).any() or np.any(np.isfinite(values)&((values<low)|(values>high))):
        raise ValueError('Bulk physical range mismatch: '+name)
    return values


def rain(ident,lead,hours):
    ec.native_step(lead)
    if hours not in ec.RAIN_WINDOWS or lead<hours:raise ValueError('Rain window before initialization')
    end=lead;parts=[]
    while end>lead-hours:
        width=ec.native_step(end)
        if end-width<lead-hours:raise ValueError('Rain window splits a native time interval')
        parts.append(field(ident,end,'precipitation'))
        end-=width
    return np.sum(parts,axis=0,dtype=np.float32)


def download_field(ident,name,meta,work):
    """Resample two adjacent original Gaussian rows without global triangulation."""
    target=work/(name+'.npy');record=work/(name+'.json')
    if record.exists() and target.exists():
        saved=json.loads(record.read_text())
        if saved.get('reference_time')==meta['reference_time']:return saved
    fs=RangeSource();url=urlbase(ident)+name+'.om'
    print('REGIONAL start',ec.run_id(ident),name,flush=True)
    with OmFileReader.from_fsspec(fs,url) as reader:
        attrs={reader.get_child_by_index(i).name:reader.get_child_by_index(i) for i in range(reader.num_children)}
        if (reader.shape[:2]!=(1,6599680) or attrs['forecast_reference_time'].read_scalar()!=int(ec.run_time(ident).timestamp())
            or 'O1280' not in attrs['crs_wkt'].read_scalar()):raise ValueError('Bulk native grid/run mismatch')
        unit=attrs['unit'].read_scalar()
        accepted={SPECS[name][0]}
        if name=='pressure_msl':accepted.add('Pa')
        if name=='total_column_integrated_water_vapour':accepted|={'mm','kg m-2','kg/m2','kg m**-2'}
        if unit not in accepted:raise ValueError(f'Bulk unit mismatch {name}: {unit!r}')
        times=np.asarray(attrs['time'][:],dtype=np.int64)
        leads=((times-int(ec.run_time(ident).timestamp()))/3600)
        expected=expected_leads(ident)
        if not np.array_equal(leads,leads.astype(int)) or len(np.unique(times))!=len(times):raise ValueError('Invalid bulk timestamps')
        # A backwards sum has no initialization timestep in the bulk format.
        needed=[h for h in expected if h or name!='precipitation']
        if not set(needed)<=set(leads):raise ValueError('Incomplete native time coverage')
        indices=[int(np.flatnonzero(leads==h)[0]) for h in needed]
        if indices!=list(range(indices[0],indices[-1]+1)):raise ValueError('Unexpected native time ordering')
        temporal=slice(indices[0],indices[-1]+1)
        array=np.lib.format.open_memmap(target.with_suffix('.next'),mode='w+',dtype=np.float32,
            shape=(len(expected),len(LAT),len(LON)))
        array[:]=np.nan
        dy=180/(2560+.5)
        yf=1279-(LAT-dy/2)/dy
        top=np.floor(yf).astype(int);bottom=top+1;weight=yf-top
        rows=range(int(top.min()),int(bottom.max())+1)
        previous=None;previous_lon=None
        for row in rows:
            nx=20+4*min(row,2559-row)
            offset=2*row*row+18*row if row<=1280 else 6599680-(2*(2560-row)**2+18*(2560-row))
            xf=LON*nx/360
            left=np.floor(xf).astype(int);right=left+1
            a,b=int(left.min()),int(right.max())+1
            raw=normalize_values(name,unit,reader[0,offset+a:offset+b,temporal])
            if name=='precipitation':
                current=raw[np.rint(xf).astype(int)-a,:].T
                nearest_lon=np.rint(xf)*360/nx
            else:
                f=(xf-left).astype(np.float32)[:,None]
                current=(raw[left-a,:]*(1-f)+raw[right-a,:]*f).T
            if previous is not None:
                dest=np.flatnonzero(bottom==row)
                for iy in dest:
                    if name=='precipitation':
                        previous_lat=(1279-(row-1))*dy+dy/2
                        current_lat=(1279-row)*dy+dy/2
                        prefer_previous=(LON-previous_lon)**2+(LAT[iy]-previous_lat)**2 <= (LON-nearest_lon)**2+(LAT[iy]-current_lat)**2
                        value=np.where(prefer_previous[None,:],previous,current)
                    else:value=previous*(1-weight[iy])+current*weight[iy]
                    array[(slice(None) if name!='precipitation' else slice(1,None)),iy,:]=value
            previous=current
            if name=='precipitation':previous_lon=nearest_lon
        array.flush();del array
        os.replace(target.with_suffix('.next'),target)
        h=hashlib.sha256()
        with target.open('rb') as handle:
            for block in iter(lambda:handle.read(4*1024*1024),b''):h.update(block)
        saved=dict(sha256=h.hexdigest(),url=url,etag=fs.objects[url][1],bytes_downloaded=fs.bytes,
                   reference_time=meta['reference_time'],source_unit=unit,unit=SPECS[name][0],source_times=times.tolist(),
                   native_grid='O1280',processing='nearest native cell for precipitation; Gaussian-row linear interpolation otherwise',
                   retrieved_at=ec.iso(datetime.now(ec.UTC)))
        ec.atomic_json(record,saved)
    print('REGIONAL ready',name,round(fs.bytes/1e6,1),'MB transferred',flush=True)
    return saved


def ingest(ident):
    ident=ec.run_id(ident)
    if exists(ident):return manifest(ident)
    work=root()/(ident+'.preparing');work.mkdir(parents=True,exist_ok=True)
    response=session().get(urlbase(ident)+'meta.json',timeout=(10,45));response.raise_for_status();meta=response.json()
    if (meta.get('reference_time')!=ec.iso(ec.run_time(ident)) or 'O1280' not in meta.get('crs_wkt','')
        or not set(SPECS)<=set(meta.get('variables',[]))):raise ValueError('Bulk catalog identity mismatch')
    ec.atomic_json(work/'source-meta.json',meta)
    ec.atomic_json(ec.ROOT/'status.json',dict(state='downloading-regional',run=meta['reference_time'],updated=ec.iso(datetime.now(ec.UTC))))
    with ThreadPoolExecutor(max_workers=2) as pool:
        values=list(pool.map(lambda name:download_field(ident,name,meta,work),SPECS))
    result=dict(schema=SCHEMA,model=ec.MODEL,run=meta['reference_time'],bounds=BOUNDS,grid_degrees=.1,
        native_grid='O1280',native_grid_km=9,leads=expected_leads(ident),fields=dict(zip(SPECS,values)),
        segments={'regional':dict(sha256=hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest(),
            retrieved_at=ec.iso(datetime.now(ec.UTC)))},updated=ec.iso(datetime.now(ec.UTC)))
    ec.atomic_json(work/'run.json',result)
    os.replace(work,directory(ident))
    ec.atomic_json(ec.ROOT/'status.json',dict(state='ready',run=meta['reference_time'],updated=result['updated'],bounds=BOUNDS))
    return result


def upper_catalog(ident):
    path=UPPER/('run_'+ec.run_id(ident)+'.json')
    try:
        value=json.loads(path.read_text())
        if value.get('model')!='ECMWF-IFS' or value.get('run')!=ec.iso(ec.run_time(ident)):raise ValueError('Upper run mismatch')
        return value
    except OSError:return dict(frames=[],levels=[])


def upper_entry(ident,lead,level):
    if level not in ('925','850','700','600','500','400','300','200','100'):raise ValueError('Unsupported EC pressure level')
    cat=upper_catalog(ident)
    entry=next((item for item in cat['frames'] if item['lead']==lead),None)
    if not entry or level not in cat.get('levels',[]):raise ValueError('Matching EC 0.25 degree layer is not cached')
    name=entry['file']
    if not re.fullmatch(r'ecmwf_'+ec.run_id(ident)+r'_f\d{3}_global1.json',name):raise ValueError('Invalid pressure field path')
    return UPPER/name


def catalog(ident):
    value=manifest(ident);run=ec.run_time(ident);upper=upper_catalog(ident)
    pressure=[level for level in upper.get('levels',[]) if level!='surface']
    matched={item['lead'] for item in upper.get('frames',[]) if item['lead'] in value['leads']}
    levels=['surface','100m']+pressure
    return dict(schema=2,model=ec.MODEL,run=value['run'],updated=value['updated'],bounds=BOUNDS,
        levels=levels,interval_hours=1,frame_loading='per-level',cache_version='ec-regional-20261015-v2',
        upper_leads={level:sorted(matched) for level in pressure},native_grid='O1280',native_grid_km=9,
        display_grid_degrees=.1,frame_base='./api/v4/'+ec.SLUG+'/frames',partial=False,
        capabilities=dict(cyclones=False,pw=True,surface_rh=True,wind100=True,products=['wind','t','td','rh','rain','pw']),
        source='地面/100米：ECMWF 9 km via Open-Meteo；高空：现有ECMWF 0.25°',
        source_note='60–150°E / 0–60°N；地面、100米风和整层水汽：Open-Meteo原生9 km，重采样到0.1°；气压层：同一起报和有效时刻的现有EC 0.25°缓存。',
        frames=[dict(lead=h,valid=ec.iso(run+timedelta(hours=h)),file=f'{ec.SLUG}_{ec.run_id(ident)}_f{h:03d}.json',
            levels=['surface','100m']+(pressure if h in matched else [])) for h in value['leads']],errors=[])


def pressure_frame(ident,lead,level):
    from global_store import layer_frame
    path=upper_entry(ident,lead,level)
    data=layer_frame(path,level,lists=True)
    if data['run']!=ec.iso(ec.run_time(ident)) or data['lead']!=lead or data['valid']!=ec.iso(ec.run_time(ident)+timedelta(hours=lead)):
        raise ValueError('EC pressure field run/validity mismatch')
    data['layers']={level:data['layers'][level]}
    data.update(model=ec.MODEL,source_model='ECMWF-IFS',source='ECMWF Open Data · 高空 0.25°',
        source_note='高空沿用现有EC 0.25°；与地面9 km为同一起报、同一有效时刻。',
        source_grid_degrees=.25)
    return data


def pressure_point(ident,lead,longitude,latitude):
    """Read four neighbours per field, avoiding full-region JSON for a sounding."""
    from global_store import read_meta,field_directory
    records=[]
    for level in upper_catalog(ident).get('levels',[]):
        if level=='surface':continue
        path=upper_entry(ident,lead,level);meta=read_meta(path)
        if (meta.get('model')!='ECMWF-IFS' or meta.get('native_grid_degrees')!=.25 or
            meta['run']!=ec.iso(ec.run_time(ident)) or meta['lead']!=lead or
            meta['valid']!=ec.iso(ec.run_time(ident)+timedelta(hours=lead))):raise ValueError('Sounding pressure identity mismatch')
        xs,ys=meta['lon'],meta['lat'];xf=(longitude-xs[0])/.25;yf=(latitude-ys[0])/.25
        if not 0<=xf<=len(xs)-1 or not 0<=yf<=len(ys)-1:raise ValueError('Sounding outside EC cache')
        ix,iy=min(int(xf),len(xs)-2),min(int(yf),len(ys)-2);wx,wy=xf-ix,yf-iy
        item=dict(level=int(level))
        for key in ['gh','t','rh','u','v']:
            with (field_directory(path,meta)/(level+'_'+key+'.npy')).open('rb') as handle:
                version=np.lib.format.read_magic(handle)
                if version==(1,0):shape,fortran,dtype=np.lib.format.read_array_header_1_0(handle)
                elif version==(2,0):shape,fortran,dtype=np.lib.format.read_array_header_2_0(handle)
                else:raise ValueError('Unsupported pressure array format')
                if shape!=(len(ys),len(xs)) or fortran or dtype!=np.dtype('float32'):raise ValueError('Pressure array layout mismatch')
                offset=handle.tell();cells=[]
                for row in [iy,iy+1]:
                    handle.seek(offset+4*(row*len(xs)+ix));cells.append(np.frombuffer(handle.read(8),dtype=np.float32))
                item[key]=float((cells[0][0]*(1-wx)+cells[0][1]*wx)*(1-wy)+(cells[1][0]*(1-wx)+cells[1][1]*wx)*wy)
        records.append(item)
    return records


def frame(ident,lead,level='surface'):
    if level not in ('surface','100m'):return pressure_frame(ident,lead,level)
    manifest(ident);ec.native_step(lead)
    height=100 if level=='100m' else 10
    layer={key:ec.clean(field(ident,lead,source)) for key,source in [
        ('u',f'wind_u_component_{height}m'),('v',f'wind_v_component_{height}m')]}
    if level=='surface':
        layer.update({key:ec.clean(field(ident,lead,source)) for key,source in [
            ('t','temperature_2m'),('td','dew_point_2m'),('rh','relative_humidity_2m'),('msl','pressure_msl')]})
    return dict(schema=2,model=ec.MODEL,run=ec.iso(ec.run_time(ident)),valid=ec.iso(ec.run_time(ident)+timedelta(hours=lead)),lead=lead,
        lon=LON.tolist(),lat=LAT.tolist(),bounds=BOUNDS,layers={level:layer,**({'surface':{'t':ec.clean(field(ident,lead,'temperature_2m'))}} if level=='100m' else {})},interval_hours=ec.native_step(lead),
        native_grid='O1280',native_grid_km=9,display_grid_degrees=.1,
        source='ECMWF via Open-Meteo · '+('100米风' if level=='100m' else '地面')+' 9 km · CC BY 4.0',
        source_note='原生O1280约9 km，重采样到0.1°显示网格；未启用高程订正。')


def point_forecast(ident,longitude,latitude):
    if not BOUNDS[0]<=longitude<=BOUNDS[2] or not BOUNDS[1]<=latitude<=BOUNDS[3]:raise ValueError('Point outside 60–150E / 0–60N')
    target=np.array([[longitude,latitude]]);rows=[];run=ec.run_time(ident)
    def at(lead,name,nearest=False):return sample(field(ident,lead,name),target,nearest)[0]
    for lead in manifest(ident)['leads']:
        item=dict(lead=lead,valid=ec.iso(run+timedelta(hours=lead)),interval_hours=ec.native_step(lead))
        for key,name in [('t','temperature_2m'),('rh','relative_humidity_2m'),('msl','pressure_msl'),
                         ('wind','wind_speed_10m'),('direction','wind_direction_10m'),
                         ('wind100','wind_speed_100m'),('direction100','wind_direction_100m')]:
            if key in ('wind','direction','wind100','direction100'):
                height=100 if key.endswith('100') else 10
                u,v=(at(lead,f'wind_{c}_component_{height}m') for c in ('u','v'))
                number=math.hypot(u,v) if key.startswith('wind') else (math.degrees(math.atan2(-u,-v))+360)%360
            else:number=at(lead,name)
            item[key]=ec.clean([number])[0]
        number=at(lead,'precipitation',True) if lead else np.nan
        item.update(precipitation=ec.clean([number])[0],**{f'rain{ec.native_step(lead)}h':ec.clean([number])[0]})
        rows.append(item)
    return dict(model=ec.MODEL,run=ec.iso(run),longitude=longitude,latitude=latitude,native_grid_km=9,
        display_grid_degrees=.1,rows=rows,start=rows[0]['valid'],end=rows[-1]['valid'],interval_hours=1,variable_intervals=True,
        source='ECMWF via Open-Meteo · 地面及100米风 9 km · CC BY 4.0')


def prune():
    base=root().resolve()
    cutoff=datetime.now(ec.UTC)-timedelta(days=40)
    for path in base.glob('??????????'):
        if (re.fullmatch(r'\d{10}',path.name) and not path.is_symlink() and path.resolve().parent==base and ec.run_time(path.name)<cutoff):
            shutil.rmtree(path)
