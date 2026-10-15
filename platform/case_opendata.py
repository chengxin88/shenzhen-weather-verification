"""Exact-time Shenzhen open-data observations for native case verification."""
from datetime import datetime,timedelta,timezone
from functools import lru_cache
from pathlib import Path
import json,math,os,tomllib
import numpy as np
import pandas as pd
import requests

SOURCE='sz-open-data-v1'
LABEL='深圳开放数据格点分析场'
APP=Path(os.getenv('VERIFICATION_APP_ROOT','/home/chengyang/ec-gfs-server'))
CACHE=Path(__file__).resolve().parent/'cache'/'case_opendata_v1'
BJT=timezone(timedelta(hours=8))
URL='https://opendata.sz.gov.cn/api/29200_00903509/1/service.xhtml'
PAGE_SIZE=3000
COLUMNS=['GRIDID','RAIN01H','RAIN02H','RAIN03H','RAIN06H','RAIN24H','T','WSPD','WDIR']

@lru_cache(maxsize=1)
def grid():
    f=pd.read_excel(APP/'data'/'grid.xlsx',sheet_name='数据集1').rename(columns={
        '格网ID（唯一）':'GRIDID','格网左下角经度（度）':'west','格网右上角经度（度）':'east',
        '格网左下角纬度（度）':'south','格网右上角纬度（度）':'north'})
    for name in ['GRIDID','west','east','south','north']:f[name]=pd.to_numeric(f[name],errors='coerce')
    f=f.dropna(subset=['GRIDID','west','east','south','north']).drop_duplicates('GRIDID')
    f['GRIDID']=f.GRIDID.astype(int);f['lon']=((f.west+f.east)/2).round(7);f['lat']=((f.south+f.north)/2).round(7)
    return f[['GRIDID','lon','lat']]

def api_key():
    key=os.environ.get('APP_KEY','')
    if not key:
        path=APP/'.streamlit'/'secrets.toml'
        if path.is_file():key=str(tomllib.loads(path.read_text()).get('APP_KEY',''))
    if not key:raise ValueError('深圳开放数据接口未配置凭据。')
    return key

class Reader:
    def __init__(self):
        self.session=requests.Session()
        self.session.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://opendata.sz.gov.cn/'})
        self.key=api_key()
        self.page=lru_cache(maxsize=2)(self._page)
    def close(self):self.page.cache_clear();self.session.close()
    def _page(self,day,n):
        try:
            response=self.session.post(URL,data={'appKey':self.key,'page':n,'rows':PAGE_SIZE,'startDate':day,'endDate':day},timeout=40)
            response.raise_for_status();result=response.json()
        except Exception:raise ValueError('深圳开放数据接口请求失败，请稍后重试。') from None
        if result.get('errorCode'):raise ValueError('深圳开放数据接口返回错误，请稍后重试。')
        rows=result.get('data',[])
        return int(result.get('total',0)),rows if isinstance(rows,list) else []
    def exact(self,day,target):
        total,first=self.page(day,1)
        if not first:return []
        maximum=math.ceil(total/PAGE_SIZE);low,high=1,maximum;found=None
        while low<=high:
            mid=(low+high)//2;_,rows=self.page(day,mid)
            times=sorted(str(r['DDATETIME']) for r in rows if r.get('DDATETIME'))
            if not times:return []
            if target<times[0]:high=mid-1
            elif target>times[-1]:low=mid+1
            else:found=mid;break
        if found is None:return []
        output=[r for r in rows if r.get('DDATETIME')==target]
        for direction in [-1,1]:
            n=found+direction
            while 1<=n<=maximum:
                _,neighbors=self.page(day,n)
                matches=[r for r in neighbors if r.get('DDATETIME')==target]
                if not matches:break
                output.extend(matches);n+=direction
        return output

def normalize(rows,target):
    # Admit exact-time surface analysis only, never adjacent-time forecasts.
    rows=[r for r in rows if r.get('DDATETIME')==target and str(r.get('YBSX')) in ['0','0.0'] and str(r.get('PLEVEL')) in ['0','0.0'] and r.get('FORECASTTIME')==target]
    f=pd.DataFrame(rows)
    if f.empty:return None
    for name in COLUMNS:
        f[name]=pd.to_numeric(f[name],errors='coerce') if name in f else np.nan
    f=f[COLUMNS].dropna(subset=['GRIDID']);f['GRIDID']=f.GRIDID.astype(int)
    if f.duplicated('GRIDID').any():raise ValueError('深圳开放数据同一时刻有重复格点，拒绝混合评分。')
    for name in ['RAIN01H','RAIN02H','RAIN03H','RAIN06H','RAIN24H','WSPD']:f.loc[f[name]<0,name]=np.nan
    f.loc[f['T']<-9000,'T']=np.nan
    f.loc[(f.WDIR<0)|(f.WDIR>360),'WDIR']=np.nan
    known=grid().GRIDID
    f=f[f.GRIDID.isin(known)]
    if len(f)<math.ceil(len(known)*.95):raise ValueError(f'深圳开放数据 {target} 格点覆盖不足：{len(f)}/{len(known)}。')
    return f

@lru_cache(maxsize=8)
def observation(valid):
    utc=datetime.fromisoformat(valid.replace('Z','+00:00'));bjt=utc.astimezone(BJT)
    if bjt.minute or bjt.second:raise ValueError('个例检验仅使用精确整点实况。')
    target=bjt.strftime('%Y-%m-%d %H:%M:%S');path=CACHE/(bjt.strftime('%Y%m%d%H')+'.json')
    if path.is_file():
        try:
            saved=json.loads(path.read_text(encoding='utf-8'))
            if saved.get('source')==SOURCE and saved.get('valid_utc')==valid:
                frame=normalize(saved['records'],target)
                if frame is not None:return frame
        except (ValueError,KeyError,OSError):pass
    reader=Reader()
    try:
        for offset in [0,1,-1,2,-2]:
            day=(bjt+timedelta(days=offset)).strftime('%Y%m%d')
            rows=reader.exact(day,target)
            frame=normalize(rows,target)
            if frame is None:continue
            CACHE.mkdir(parents=True,exist_ok=True)
            # Preserve analysis identity with normalized numeric fields.
            records=json.loads(frame.to_json(orient='records'))
            for r in records:r.update(DDATETIME=target,FORECASTTIME=target,YBSX=0,PLEVEL=0)
            saved={'source':SOURCE,'valid_utc':valid,'archive_query_date':day,'records':records}
            temporary=path.with_suffix('.json.tmp');temporary.write_text(json.dumps(saved,ensure_ascii=False,allow_nan=False),encoding='utf-8');os.replace(temporary,path)
            return frame
    finally:reader.close()
    raise ValueError(f'深圳开放数据没有 {target} BJT 的精确整点实况（已查邻近日归档）。')

def entry(valid):
    return {'id':'szobs:'+valid,'model':SOURCE,'kind':'analysis','run':'','lead':0,'valid':valid,'levels':['verify_surface','verify_surface_6h']}

def get_frame(item,level,hours):
    if level not in item['levels']:raise ValueError('深圳开放数据只提供地面实况，不能代替高空实况。')
    obs=observation(item['valid']);g=grid();frame=g.merge(obs,on='GRIDID',how='left',validate='one_to_one')
    x=np.sort(g.lon.unique());y=np.sort(g.lat.unique())
    frame['t']=frame['T']
    frame['u']=-frame.WSPD*np.sin(np.deg2rad(frame.WDIR));frame['v']=-frame.WSPD*np.cos(np.deg2rad(frame.WDIR))
    accumulation=6 if level=='verify_surface_6h' else 2
    frame['rain']=frame[f'RAIN{accumulation:02d}H']
    def raster(name):return frame.pivot(index='lat',columns='lon',values=name).reindex(index=y,columns=x).to_numpy(dtype=float).reshape(-1).tolist()
    end=datetime.fromisoformat(item['valid'].replace('Z','+00:00'))
    return {'id':item['id'],'model':SOURCE,'run_utc':'','lead_hours':0,'valid_utc':item['valid'],'level':level,
        'sampling':'native-shenzhen','verification_version':'case-native-v1','native_grid_degrees':.01,'display_grid_degrees':.01,
        'lon':x.tolist(),'lat':y.tolist(),'fields':{level:{k:raster(k) for k in ['rain','t','u','v']}},
        'rain_hours':accumulation,'rain_unit':'mm','rain_start_utc':(end-timedelta(hours=accumulation)).isoformat(),'rain_end_utc':end.isoformat(),
        'warnings':['参考实况统一采用深圳开放数据格点分析场，按记录实际北京时间精确配对；未与本地alpha/gd资料拼接。']}
