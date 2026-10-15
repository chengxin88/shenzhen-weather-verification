"""Read existing WN3 hourly fields + exact 20 BJT RAIN24H; never download."""
import argparse
from datetime import datetime,timedelta,timezone
import hashlib,json,os,sys,tempfile
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.interpolate import RegularGridInterpolator
from shapely.geometry import Point,shape
from shapely.ops import unary_union

sys.path.append('/home/chengyang/verification-test')
import native_archive as native
from monthly_precip import counts,scores,WINDOWS,THRESHOLDS,THRESHOLD_NAMES

APP=Path('/home/chengyang/ec-gfs-server')
WEATHER=Path('/home/chengyang/gods-eye-weather/weather-data/region-v4/weathernext3')
BJT=timezone(timedelta(hours=8))
WN3=native.WN3_MODEL


def save(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(dir=path.parent,prefix='.wn3-month-')
    try:
        with os.fdopen(fd,'wb') as handle:handle.write(raw)
        os.replace(temporary,path)
    finally:Path(temporary).unlink(missing_ok=True)


def rain_field(run,start,end):
    path=WEATHER/('run_'+run.strftime('%Y%m%d%H')+'.json')
    checked,directory,committed=native.wn3_manifest(path)
    required=set(range(start+1,end+1))
    if checked!=run or not required<=committed:raise ValueError('连续24小时尚未完整提交')
    total=None;axes=None;inputs=[]
    for lead in sorted(required):
        field_path=WEATHER/directory/run.strftime('%Y%m%d%H')/f'f{lead:03}_rain1h.npz'
        x,y,values=native.load_wn3_field(field_path,run,lead,'rain1h',directory)
        if axes is None:axes=(x,y);total=np.zeros_like(values,dtype=float)
        if not np.array_equal(x,axes[0]) or not np.array_equal(y,axes[1]):raise ValueError('小时段网格不一致')
        total+=values;inputs.append(str(field_path))
    return (*axes,total),inputs


def main(month,destination):
    first=datetime.strptime(month,'%Y-%m').replace(tzinfo=timezone.utc)
    following=(first+timedelta(days=32)).replace(day=1)
    days=[first+timedelta(days=k) for k in range((following-first).days)]
    now=datetime.now(timezone.utc)
    grid=pd.read_excel(APP/'data/grid.xlsx',sheet_name='数据集1').rename(columns={
        '格网ID（唯一）':'GRIDID','格网左下角经度（度）':'west','格网右上角经度（度）':'east',
        '格网左下角纬度（度）':'south','格网右上角纬度（度）':'north'})
    for col in ['GRIDID','west','east','south','north']:grid[col]=pd.to_numeric(grid[col],errors='coerce')
    grid=grid.dropna(subset=['GRIDID','west','east','south','north']);grid.GRIDID=grid.GRIDID.astype(int)
    grid['lon']=(grid.west+grid.east)/2;grid['lat']=(grid.south+grid.north)/2
    city=unary_union([shape(f['geometry']) for f in json.loads((APP/'data/shenzhen_streets.geojson').read_text())['features']])
    inside=np.array([city.covers(Point(float(x),float(y))) for x,y in zip(grid.lon,grid.lat)])
    lon=grid.lon.to_numpy();lat=grid.lat.to_numpy();ids=grid.GRIDID
    gx=np.rint((lon-lon.min())/.01).astype(int);gy=np.rint((lat-lat.min())/.01).astype(int)
    nx,ny=int(gx.max())+1,int(gy.max())+1
    def raster(values):
        output=[None]*(nx*ny)
        for k in np.flatnonzero(inside&np.isfinite(values)):output[int(gy[k])*nx+int(gx[k])]=round(float(values[k]),3)
        return output
    observations={};provenance={'hourly_fields':{},'observation_files':{}}
    for day in days:
        valid=day.replace(hour=12)
        if valid>now-timedelta(hours=1):continue
        filename=valid.astimezone(BJT).strftime('%Y%m%d%H')+'.pkl'
        paths=[APP/'cache/OBSERVATION/grid'/filename,Path('/home/chengyang/verification-test/cache/obs-backfill/grid')/filename]
        path=next((p for p in paths if p.is_file()),None)
        if path is None:continue
        obs=pd.read_pickle(path)
        if not {'GRIDID','RAIN24H'}<=set(obs.columns) or obs.GRIDID.nunique()<np.ceil(grid.GRIDID.nunique()*.95):continue
        values=pd.to_numeric(obs.drop_duplicates('GRIDID').set_index('GRIDID').RAIN24H,errors='coerce').reindex(ids).to_numpy(dtype=float,copy=True)
        values[values<0]=np.nan
        if not (inside&np.isfinite(values)).any():continue
        observations[day]=values;provenance['observation_files'][day.strftime('%Y-%m-%d')]=str(path)
    result=dict(version='wn3-month-demo-v1',is_demo=True,excluded_from_official_scores=True,source_model=WN3,
        month=month,generated=now.astimezone(BJT).isoformat(),grid=dict(nx=nx,ny=ny,lon=sorted(set(lon)),lat=sorted(set(lat))),
        day_definition='北京时间前一日20时至当日20时（24小时）',reference='深圳开放数据格点分析场 RAIN24H（当日20时）',
        thresholds=[dict(value=t,label=THRESHOLD_NAMES[t]) for t in THRESHOLDS],models=[dict(id=WN3,slug='wn3')],windows=[],
        method='WN3原生0.1°集合均值；同一起报连续24个完整小时段逐格相加，最近邻匹配深圳0.01°实况格点。缺一个小时即不评分；只展示缓存覆盖的日期，不代表完整月份。',
        obs_missing=[dict(day=d.strftime('%Y-%m-%d'),reason='对应20时实况尚无本地完整缓存') for d in days if d not in observations])
    obs_sum=np.zeros(len(grid));obs_n=np.zeros(len(grid))
    for value in observations.values():
        good=np.isfinite(value);obs_sum[good]+=value[good];obs_n[good]+=1
    obs_sum[obs_n==0]=np.nan
    daily=[dict(day=d.strftime('%Y-%m-%d'),mean=float(np.nanmean(observations[d][inside])) if d in observations else None,
                max=float(np.nanmax(observations[d][inside])) if d in observations else None) for d in days]
    result['obs']=dict(days=len(observations),daily=daily,total_map=raster(obs_sum),mean_total=sum(d['mean'] for d in daily if d['mean'] is not None))
    for window,cfg in WINDOWS.items():
        fc_sum=np.zeros(len(grid));ob_sum=np.zeros(len(grid));used=np.zeros(len(grid));daily=[];missing=[];paired_obs_daily=[]
        aggregated={t:dict(H=0,M=0,F=0,C=0) for t in THRESHOLDS}
        for day in days:
            valid=day.replace(hour=12);run=(valid-timedelta(hours=24+cfg['lead0'])).replace(hour=cfg['run_hour'])
            reason=None
            if day not in observations:reason='对应20时实况尚无本地完整缓存'
            elif valid>now-timedelta(hours=1):reason='有效时间尚未到达'
            else:
                try:
                    (x,y,array),inputs=rain_field(run,cfg['lead0'],cfg['lead1'])
                    forecast=RegularGridInterpolator((y,x),array,method='nearest',bounds_error=False,fill_value=np.nan)(grid[['lat','lon']].to_numpy())
                    observation=observations[day];mask=inside&np.isfinite(forecast)&np.isfinite(observation)
                    if not mask.any():raise ValueError('没有有效深圳配对')
                except native.WN3_ERRORS:reason='该起报连续24个WN3小时段不完整或未通过校验'
            if reason:
                missing.append(dict(day=day.strftime('%Y-%m-%d'),run_bjt=run.astimezone(BJT).strftime('%Y-%m-%d %H时'),reason=reason))
                daily.append(dict(day=day.strftime('%Y-%m-%d'),mean=None,max=None,obs_mean=None,scores={}))
                paired_obs_daily.append(dict(day=day.strftime('%Y-%m-%d'),mean=None,max=None));continue
            fc_sum[mask]+=forecast[mask];ob_sum[mask]+=observation[mask];used[mask]+=1
            day_scores={}
            for threshold in THRESHOLDS:
                c=counts(forecast[mask],observation[mask],threshold)
                for k in c:aggregated[threshold][k]+=c[k]
                day_scores[f'{threshold:g}']=dict(c,**scores(c))
            daily.append(dict(day=day.strftime('%Y-%m-%d'),run_bjt=run.astimezone(BJT).strftime('%Y-%m-%d %H时'),
                run_utc=run.isoformat(),valid_utc=valid.isoformat(),mean=float(forecast[mask].mean()),max=float(forecast[mask].max()),
                obs_mean=float(observation[mask].mean()),paired_points=int(mask.sum()),scores=day_scores))
            paired_obs_daily.append(dict(day=day.strftime('%Y-%m-%d'),mean=float(observation[mask].mean()),max=float(observation[mask].max())))
            provenance['hourly_fields'][window+':'+day.strftime('%Y-%m-%d')]=inputs
            print(window,day.strftime('%Y-%m-%d'),'paired',int(mask.sum()),flush=True)
        fc_sum[used==0]=np.nan;ob_sum[used==0]=np.nan
        item=dict(days=sum(d['mean'] is not None for d in daily),daily=daily,missing=missing,
            mean_total=sum(d['mean'] for d in daily if d['mean'] is not None) if used.any() else None,
            obs_mean_total=sum(d['obs_mean'] for d in daily if d['obs_mean'] is not None) if used.any() else None,
            total_map=raster(fc_sum),bias_map=raster(fc_sum-ob_sum),
            month_scores={f'{t:g}':dict(aggregated[t],**scores(aggregated[t])) for t in THRESHOLDS})
        paired_obs=dict(days=item['days'],daily=paired_obs_daily,total_map=raster(ob_sum),mean_total=item['obs_mean_total'])
        result['windows'].append(dict(id=window,label=cfg['label'],models={WN3:item},paired_obs=paired_obs))
    if not any(w['models'][WN3]['days'] for w in result['windows']):raise RuntimeError('没有完整24小时WN3降水与20时实况配对日')
    raw=json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    save(destination/(month+'.json'),raw);save(destination/(month+'.sha256'),hashlib.sha256(raw).hexdigest().encode())
    save(destination/(month+'.provenance.json'),json.dumps(provenance,ensure_ascii=False).encode())
    print(json.dumps({'month':month,'obs_days':result['obs']['days'],'forecast_days':{w['id']:w['models'][WN3]['days'] for w in result['windows']}},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--month',required=True);parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args();main(args.month,args.destination)
