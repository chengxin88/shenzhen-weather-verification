"""Native typhoon verification API using the installed scientific calculations."""
import ast
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from case_api import QueueBusy

APP=Path(os.getenv('VERIFICATION_APP_ROOT','/home/yjzx/verification-platform/ec-gfs-server'))
_engine=None
_engine_lock=threading.Lock()
_jobs={}
_job_lock=threading.Lock()
_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='typhoon-score')
# typhoon.nmc.cn times out from this host without the weather egress proxy.
TYPHOON_PROXY=os.getenv('TYPHOON_PROXY','')
HERE=Path(__file__).resolve().parent
PANGU='PANGU'
PANGU_LABEL='Pangu（NOAA公开版）'
PANGU_PYTHON=os.getenv('PANGU_PYTHON',str(Path.home()/'.venvs/pangu-fetch/bin/python'))
PANGU_ROOT=HERE/'cache/native-backfill/pangu-tc'
# Spark reads NOAA's archive too slowly (over 6 minutes a run), so there another machine pushes these files
# (deploy/pull_pangu_us.py, through the Tencent relay) and PANGU_ON_DEMAND=0 keeps a request from waiting on a fetch.
PANGU_ON_DEMAND=os.getenv('PANGU_ON_DEMAND','1')!='0'
REFRESH_SECONDS=1800
RETRY_SECONDS=300
REFRESH_WAIT=4
_refresh_lock=threading.Lock()
_refreshing={}
_attempted={}
ROOT_FUNCTIONS={'fetch_typhoon_catalog','fetch_typhoon_detail','fetch_agency_typhoon_detail',
 '_typhoon_catalog_frame','_typhoon_track_frame','_agency_landfalls','_storm_number_for_agency',
 '_default_shenzhen_landfall_storm','_typhoon_verification_cache_paths','landfall_verification_frame'}

def needs_refresh(name,payload,age,year):
 """Only lists and unfinished storms change; finished storms stay on the disk archive."""
 if age<REFRESH_SECONDS:return False
 if name.startswith('catalog_'):return int(name[8:12])>=year-1
 if name.startswith('storm_'):
  storm=payload.get('typhoon') if isinstance(payload,dict) else None
  return not(isinstance(storm,list) and len(storm)>7 and storm[7]=='stop')
 if name.startswith('agency_'):return not(isinstance(payload,dict) and str(payload.get('isactive'))=='0')
 return False

def download(url,parse=None):
 import requests
 headers={'User-Agent':'Mozilla/5.0'}
 if parse:headers['Referer']='https://typhoon.nmc.cn/'
 with requests.Session() as session:
  session.trust_env=False;session.proxies.update({'http':TYPHOON_PROXY,'https':TYPHOON_PROXY})
  response=session.get(url,headers=headers,timeout=15)
 response.raise_for_status()
 return parse(response.text) if parse else response.json()

def store(path,name,payload):
 required={'catalog_':'typhoonList','storm_':'typhoon','agency_':'points'}
 key=next((v for k,v in required.items() if name.startswith(k)),None)
 if key and not(isinstance(payload,dict) and key in payload):raise ValueError('台风接口内容不完整')
 temp=path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
 temp.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8');os.replace(temp,path)

def start_refresh(name,fetch):
 def run():
  try:fetch()
  except Exception as exc:print('Typhoon refresh failed',name,type(exc).__name__,flush=True)
  finally:
   with _refresh_lock:_refreshing.pop(name,None)
 with _refresh_lock:
  if name in _refreshing:return _refreshing[name]
  if time.time()-_attempted.get(name,0)<RETRY_SECONDS:return None
  _attempted[name]=time.time();thread=_refreshing[name]=threading.Thread(target=run,daemon=True,name='typhoon-refresh')
 thread.start();return thread

def cached_reader(path_for,get):
 """Read the archive first; stale lists and active storms refresh, and old data survives any failure."""
 def read(url,name):
  path=path_for(name)
  def fetch():
   payload=get(url);store(path,name,payload);return payload
  try:payload=json.loads(path.read_text(encoding='utf-8'))
  except (OSError,ValueError):return fetch(),False
  if os.environ.get('VERIFICATION_READ_ONLY')=='1':return payload,True
  if needs_refresh(name,payload,time.time()-path.stat().st_mtime,datetime.now(ZoneInfo('Asia/Shanghai')).year):
   thread=start_refresh(name,fetch)
   if thread:
    thread.join(REFRESH_WAIT)
    try:payload=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):pass
  return payload,True
 return read

def pangu_run(issue):
 import pandas as pd
 return pd.Timestamp(issue).tz_convert('UTC').floor('12h')

def pangu_frames(run,lead_hours):
 """Regional fields for one run; fetched on demand, since only a few runs per storm are used."""
 import numpy as np
 name=run.strftime('%Y%m%d%H');path=PANGU_ROOT/f'pangu_tc_{name}.npz'
 def load():
  with np.load(path,allow_pickle=False) as z:return {k:z[k] for k in z.files}
 if path.exists():
  frame=load()
  if int(frame['leads'].max())>=lead_hours:return frame
 if not PANGU_ON_DEMAND:return None
 env={**os.environ,'HTTPS_PROXY':TYPHOON_PROXY,'HTTP_PROXY':TYPHOON_PROXY}
 done=subprocess.run([PANGU_PYTHON,str(HERE/'fetch_pangu.py'),name,'--lead-max',str(lead_hours)],env=env,capture_output=True,text=True,timeout=360)
 if done.returncode:raise RuntimeError('盘古公开资料暂不可用')
 return load()

def pangu_track_frame(issue_text,initial_lon,initial_lat,lead_hours=72):
 import pandas as pd
 from pangu_track import track
 run=pangu_run(issue_text);name=run.strftime('%Y%m%d%H')
 frames=pangu_frames(run,lead_hours)
 if frames is None:raise RuntimeError('盘古公开资料尚未入库')
 rows=track(frames,name,float(initial_lon),float(initial_lat),int(lead_hours))
 if not rows:raise RuntimeError('盘古场中未追踪到台风中心')
 frame=pd.DataFrame(rows);frame['time']=pd.to_datetime(frame['time'],utc=True)
 return frame.assign(source=PANGU,nature='AI模式场识别（非官方）',run_time=run,member='AI-single',cache_status='')

def engine():
 global _engine
 with _engine_lock:
  if _engine is not None:return _engine
  sys.path.insert(0,str(APP))
  tree=ast.parse((APP/'app.py').read_text(encoding='utf-8'))
  functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
  names=set(ROOT_FUNCTIONS);todo=list(names)
  while todo:
   for n in ast.walk(functions[todo.pop()]):
    if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load) and n.id in functions and n.id not in names:
     names.add(n.id);todo.append(n.id)
  required={n.id for name in names for n in ast.walk(functions[name]) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load)}
  assignments={n.targets[0].id:n for n in tree.body if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name)}
  constants=set(required)&set(assignments);todo=list(constants)
  while todo:
   for n in ast.walk(assignments[todo.pop()].value):
    if isinstance(n,ast.Name) and n.id in assignments and n.id not in constants:
     constants.add(n.id);todo.append(n.id)
  nodes=[]
  for node in tree.body:
   if isinstance(node,(ast.Import,ast.ImportFrom)):
    modules=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or '']
    if not any(m.split('.')[0] in {'streamlit','geopandas','matplotlib'} for m in modules):nodes.append(copy.deepcopy(node))
   elif isinstance(node,ast.Assign) and node in [assignments[n] for n in constants]:nodes.append(copy.deepcopy(node))
   elif isinstance(node,ast.FunctionDef) and node.name in names:
    selected=copy.deepcopy(node);selected.decorator_list=[];nodes.append(selected)
  scope={'__file__':str(APP/'app.py'),'__name__':'native_typhoon_engine'}
  exec(compile(ast.Module(body=nodes,type_ignores=[]),str(APP/'app.py'),'exec'),scope)
  # The hourly worker runs with VERIFICATION_CACHE_WARM_TYPHOONS=0 and never
  # updates this archive, so this module keeps its own lists and active storms fresh.
  path_for=scope['_typhoon_cache_path'];parse=scope['_parse_nmc_jsonp']
  scope['_fetch_nmc_typhoon_json']=cached_reader(path_for,lambda url:download(url,parse))
  scope['_typhoon_fetch_json']=cached_reader(path_for,download)
  colors={'CMA/NMC':'#000000','JMA':'#e69f00','JTWC':'#56b4e9','ECMWF-IFS':'#0072b2','NCEP-GFS':'#d55e00','ECAI':'#009e73','GFSAI':'#cc79a7'}
  scope['TYPHOON_SOURCE_STYLES']={k:{**v,'color':colors.get(k,v['color'])} for k,v in scope['TYPHOON_SOURCE_STYLES'].items()}
  scope['TYPHOON_SOURCE_STYLES'][PANGU]={'label':PANGU_LABEL,'nature':'AI模式场识别（非官方）','color':'#7a5195','linestyle':'-','marker':'*'}
  scope['TYPHOON_WIND_CONVENTIONS'][PANGU]='模式瞬时10米风场（EC初始场）'
  native_track=scope['fetch_model_typhoon_track'];native_issues=scope['_typhoon_actual_issue_times']
  def model_track(source,issue_time,lon,lat,lead_hours=72):
   if source==PANGU:return pangu_track_frame(issue_time,lon,lat,lead_hours)
   return native_track(source,issue_time,lon,lat,lead_hours)
  def issue_times(source,land_time,observed,agency_payload):
   issues=native_issues(source,land_time,observed,agency_payload)
   # Public Pangu runs exist at 00Z/12Z only.
   return [i for i in issues if i.tz_convert('UTC').hour in (0,12)] if source==PANGU else issues
  scope['fetch_model_typhoon_track']=model_track;scope['_typhoon_actual_issue_times']=issue_times
  _engine=scope
  return scope

def clean(value):
 e=engine();pd=e['pd'];np=e['np']
 if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
 if isinstance(value,(list,tuple)):return [clean(v) for v in value]
 if value is None or value is pd.NA or value is pd.NaT:return None
 if isinstance(value,(pd.Timestamp,datetime)):return value.isoformat()
 if isinstance(value,np.generic):value=value.item()
 if isinstance(value,float) and not math.isfinite(value):return None
 return value

def valid_year(value):
 if type(value) not in [int,str]:raise ValueError('请选择有效的台风年份。')
 try:year=int(value)
 except (ValueError,TypeError):raise ValueError('请选择有效的台风年份。')
 if not 1949<=year<=datetime.now(ZoneInfo('Asia/Shanghai')).year:raise ValueError('台风年份超出可查询范围。')
 return year

def catalog(year=None):
 e=engine();year=valid_year(year if year is not None else datetime.now(ZoneInfo('Asia/Shanghai')).year)
 payload,_=e['fetch_typhoon_catalog'](year);frame=e['_typhoon_catalog_frame'](payload)
 rows=frame.to_dict('records') if not frame.empty else []
 for r in rows:
  for k in ('chinese_name','english_name'):
   if isinstance(r.get(k),str):r[k]=r[k].strip()
 default=e['_default_shenzhen_landfall_storm'](tuple((int(r['storm_id']),str(r['number'])) for r in rows),year) if rows else None
 path=e['_typhoon_cache_path'](f'catalog_{year}.json')
 updated=datetime.fromtimestamp(path.stat().st_mtime,ZoneInfo('Asia/Shanghai')).isoformat() if path.exists() else None
 sources=[{'id':k,**{n:v[n] for n in ['label','color','nature']},'wind_convention':e['TYPHOON_WIND_CONVENTIONS'][k]} for k,v in e['TYPHOON_SOURCE_STYLES'].items()]
 return clean({'year':year,'years':list(range(datetime.now(ZoneInfo('Asia/Shanghai')).year,1948,-1)),
  'storms':rows,'default_storm':default or (rows[0]['storm_id'] if rows else None),'sources':sources,'archive_updated':updated,
  'cache_version':'native-typhoon-v2:'+hashlib.sha256((APP/'app.py').read_bytes()).hexdigest()[:12]})

def storm_context(year,storm_id):
 e=engine();year=valid_year(year)
 try:storm_id=int(storm_id)
 except (TypeError,ValueError):raise ValueError('请选择有效台风。')
 meta=catalog(year)
 if storm_id not in [r['storm_id'] for r in meta['storms']]:raise ValueError('台风不属于所选年份。')
 detail,_=e['fetch_typhoon_detail'](storm_id);storm=detail['typhoon'];agency=None;warning=None
 try:agency,_=e['fetch_agency_typhoon_detail'](e['_storm_number_for_agency'](storm[3],year))
 except Exception:warning='多机构历史路径资料暂不可用，部分来源可能缺测。'
 landfalls=e['_agency_landfalls'](storm,year,agency)
 return e,meta,storm,agency,landfalls,warning

def detail(year,storm_id):
 e,meta,storm,agency,landfalls,warning=storm_context(year,storm_id)
 nearby=[]
 for i,land in enumerate(landfalls):
  distance,_=e['_haversine_distance_and_bearing'](e['SHENZHEN_STATION_LON'],e['SHENZHEN_STATION_LAT'],land['lon'],land['lat'])
  if math.isfinite(distance) and distance<=300:nearby.append((land['time'],-distance,i))
 default=max(nearby)[2] if nearby else max(len(landfalls)-1,0)
 return clean({'year':meta['year'],'storm_id':int(storm_id),'title':f'{storm[3]}号 {storm[2]}（{storm[1]}）',
  'landfalls':[{'index':i,**land} for i,land in enumerate(landfalls)],'default_landfall':default,'warning':warning})

def selection(raw):
 if not isinstance(raw,dict):raise ValueError('检验条件格式不正确。')
 year=valid_year(raw.get('year'))
 if type(raw.get('storm_id')) is not int or type(raw.get('landfall')) is not int:raise ValueError('请选择台风和登陆记录。')
 sources=raw.get('sources');allowed=list(engine()['TYPHOON_SOURCE_STYLES'])
 if not isinstance(sources,list) or not sources or any(not isinstance(s,str) for s in sources) or len(sources)!=len(set(sources)) or any(s not in allowed for s in sources):raise ValueError('请至少选择一个有效检验来源。')
 return dict(year=year,storm_id=raw['storm_id'],landfall=raw['landfall'],sources=[s for s in allowed if s in sources])

def cached_frame(e,p,landfall):
 from seven_day_verification import _read_disk_result
 path,_=e['_typhoon_verification_cache_paths'](p['year'],p['storm_id'],landfall,tuple(p['sources']))
 frame=_read_disk_result(path)
 if frame is not None:return frame,path
 all_sources=tuple(s for s in e['TYPHOON_SOURCE_STYLES'] if s!=PANGU)
 if PANGU not in p['sources'] and tuple(p['sources'])!=all_sources:
  whole,_=e['_typhoon_verification_cache_paths'](p['year'],p['storm_id'],landfall,all_sources)
  frame=_read_disk_result(whole)
  if frame is not None:
   labels=[e['TYPHOON_SOURCE_STYLES'][s]['label'] for s in p['sources']]
   return (frame[frame['来源'].isin(labels)].copy() if not frame.empty else frame.copy()),whole
 return None,path

def result_payload(e,p,storm,landfall,frame,path,cached,warning):
 rows=frame.to_dict('records')
 sources=[{'id':s,'label':e['TYPHOON_SOURCE_STYLES'][s]['label'],'color':e['TYPHOON_SOURCE_STYLES'][s]['color'],'wind_convention':e['TYPHOON_WIND_CONVENTIONS'][s]} for s in p['sources']]
 stamp=path.stat().st_mtime if path.exists() else time.time()
 return clean({'selection':p,'title':f'{storm[3]}号 {storm[2]}（{storm[1]}）','landfall':landfall,'rows':rows,'sources':sources,'cached':cached,
  'computed':datetime.fromtimestamp(stamp,ZoneInfo('Asia/Shanghai')).isoformat(),'warning':warning,
  'method':'检验登陆前72小时内各来源的实际起报批次；登陆点误差使用有效时间最接近官方登陆时刻的预报点。到达时间使用路径首次进入官方登陆点150公里范围的时刻；正值为偏晚。气压、风速误差均为预报减实况；缺测不补零。'})

TRACK_EXTRA={'id':'WN3','label':'WN3（集合均值）','color':'#8a9a00','nature':'AI模式场识别（非官方）','wind_convention':'集合平均10米风场'}
OFFICIAL={'CMA/NMC':'BABJ','JMA':'日本','JTWC':'美国'}
AGENCY_NAMES={'CMA/NMC':'中国','JMA':'日本','JTWC':'美国'}

def track_color(source,color):
 return '#d7263d' if source=='CMA/NMC' else color  # black is the observed track here

def observed_rows(e,storm):
 import pandas as pd
 frame=e['_typhoon_track_frame'](storm);rows=[]
 for r in frame.to_dict('records'):
  rows.append({'stamp':pd.Timestamp(r['time']).to_pydatetime(),'time':pd.Timestamp(r['time']).isoformat(),'lon':float(r['lon']),'lat':float(r['lat']),
   'pressure':float(r['pressure']) if pd.notna(r['pressure']) else None,'wind':float(r['wind']) if pd.notna(r['wind']) else None,'grade':r.get('grade')})
 return frame,rows

def issues(year,storm_id):
 import pandas as pd
 e,meta,storm,agency,landfalls,warning=storm_context(year,storm_id);_,rows=observed_rows(e,storm)
 if not rows:raise ValueError('该台风暂无实况路径。')
 first=pd.Timestamp(rows[0]['time']).tz_convert('UTC').ceil('12h');last=pd.Timestamp(rows[-1]['time']).tz_convert('UTC').floor('12h')
 times=[t.tz_convert('Asia/Shanghai').isoformat() for t in pd.date_range(first,last,freq='12h')]
 if not times:raise ValueError('该台风生命史太短，没有00Z/12Z起报。')
 default=times[-1]
 if landfalls:
  target=pd.Timestamp(landfalls[0]['time'])-pd.Timedelta(hours=48)
  default=min(times,key=lambda t:abs(pd.Timestamp(t)-target))
 else:
  known=[r for r in rows if r['pressure'] is not None]
  if str(storm[7])=='stop' and known:
   target=pd.Timestamp(min(known,key=lambda r:r['pressure'])['time'])-pd.Timedelta(hours=24)
   default=min(times,key=lambda t:abs(pd.Timestamp(t)-target))
  elif len(times)>1:default=times[-2]
 sources=[{**s,'color':track_color(s['id'],s['color'])} for s in catalog(year)['sources']]+[TRACK_EXTRA]
 return clean({'year':meta['year'],'storm_id':int(storm_id),'title':f'{storm[3]}号 {str(storm[2]).strip()}（{storm[1]}）','issues':times,'default_issue':default,
  'sources':sources,'warning':warning,'shenzhen':[e['SHENZHEN_STATION_LON'],e['SHENZHEN_STATION_LAT']]})

def track_selection(raw):
 if not isinstance(raw,dict) or set(raw)!={'year','storm_id','issue','sources'}:raise ValueError('起报条件不完整。')
 allowed=list(engine()['TYPHOON_SOURCE_STYLES'])+[TRACK_EXTRA['id']];sources=raw['sources']
 if not isinstance(sources,list) or not sources or any(s not in allowed for s in sources):raise ValueError('请至少选择一个有效来源。')
 return dict(year=valid_year(raw['year']),storm_id=raw['storm_id'],issue=str(raw['issue']),sources=[s for s in allowed if s in sources])

def official_track(e,source,issue,observed,agency):
 import pandas as pd
 frame=pd.DataFrame()
 if source=='CMA/NMC':
  rows=observed[observed['forecasts'].map(bool)]
  if not rows.empty:
   row=rows.loc[(rows['time']-issue).abs().idxmin()]
   if abs(row['time']-issue)<=pd.Timedelta(hours=3):frame=e['_typhoon_forecast_frame'](row,OFFICIAL[source])
 # NMC drops past forecasts once a storm ends; the agency archive keeps them.
 if frame.empty and agency:
  frame=e['_agency_forecast_frame'](agency,issue,AGENCY_NAMES[source],source)
  if frame.empty or abs(pd.Timestamp(frame.iloc[0]['run_time'])-issue)>pd.Timedelta(hours=3):return []
 if frame.empty:return []
 positive=lambda v:float(v) if pd.notna(v) and v>0 else None  # the archives write missing intensity as 0
 return [{'lead':int(r['lead']),'time':pd.Timestamp(r['time']).isoformat(),'lon':float(r['lon']),'lat':float(r['lat']),
  'pressure':positive(r['pressure']),'wind':positive(r['wind'])} for r in frame.to_dict('records')]

def model_track(e,source,run,lon,lat):
 import typhoon_tracks as K
 def build():
  if source==PANGU:return K.grid_rows(pangu_frames(run,K.HORIZON),run,lon,lat)
  if source==TRACK_EXTRA['id']:return K.grid_rows(K.wn3_fields(run,K.HORIZON),run,lon,lat)
  rows=K.grid_rows(K.local_fields(source,run,K.HORIZON),run,lon,lat)
  return rows or K.cached_native(e['_read_model_track_cache'],e['_typhoon_cache_path']('x').parent,source,run,lon,lat)
 return K.stored(source,run,lon,lat,build)

def compute_tracks(job,p):
 import pandas as pd
 import typhoon_tracks as K
 try:
  e,meta,storm,agency,landfalls,warning=storm_context(p['year'],p['storm_id']);frame,observed=observed_rows(e,storm)
  issue=pd.Timestamp(p['issue']);run=issue.tz_convert('UTC').to_pydatetime();anchor=K.interpolate(observed,issue.to_pydatetime())
  if anchor is None:raise ValueError('该起报时刻不在台风实况时段内。')
  styles={**e['TYPHOON_SOURCE_STYLES'],TRACK_EXTRA['id']:TRACK_EXTRA};tracks=[]
  for source in p['sources']:
   with _job_lock:job.update(status='running',message=f"正在读取 {styles[source]['label']}…")
   entry={'source':source,'label':styles[source]['label'],'color':track_color(source,styles[source]['color']),'status':'ok'}
   try:
    rows=official_track(e,source,issue,frame,agency) if source in OFFICIAL else model_track(e,source,run,anchor['lon'],anchor['lat'])
    if source in OFFICIAL and rows and rows[0]['lead']>0:rows=[{'lead':0,'time':issue.isoformat(),'lon':anchor['lon'],'lat':anchor['lat'],'pressure':anchor['pressure'],'wind':anchor['wind']}]+rows
    for row in rows:row['stamp']=pd.Timestamp(row['time']).to_pydatetime()
    entry['errors']=K.errors(rows,observed)
    for row in rows:row.pop('stamp')
    entry['rows']=rows
    if not rows:entry['status']='无此起报资料'
   except Exception as exc:
    print('Typhoon track failed',source,p['issue'],type(exc).__name__,str(exc)[:120],flush=True)
    entry.update(rows=[],errors={},status='资料读取失败')
   tracks.append(entry)
  for row in observed:row.pop('stamp')
  payload=clean({'title':f'{storm[3]}号 {str(storm[2]).strip()}（{storm[1]}）','issue':issue.isoformat(),'observed':observed,'tracks':tracks,'warning':warning,
   'shenzhen':[e['SHENZHEN_STATION_LON'],e['SHENZHEN_STATION_LAT']],'horizon':K.HORIZON})
  with _job_lock:job.update(status='done',message='路径已加载',result=payload,finished=time.time())
 except ValueError as exc:
  with _job_lock:job.update(status='error',message=str(exc),finished=time.time())
 except Exception as exc:
  print('Typhoon tracks failed',type(exc).__name__,str(exc)[:160],flush=True)
  with _job_lock:job.update(status='error',message='台风路径暂不可用，请刷新重试或更换起报时刻。',finished=time.time())

def public_job(job):
 return {k:job[k] for k in ['id','status','message','result'] if k in job}

def source_frames(e,p,storm,landfall,agency,job):
 """Pangu is scored and cached apart, so existing seven-source results stay valid."""
 import pandas as pd
 from seven_day_verification import _write_disk_result
 frames=[];paths=[];cached=True
 base=[s for s in p['sources'] if s!=PANGU]
 for sources in [base,[PANGU] if PANGU in p['sources'] else []]:
  if not sources:continue
  q={**p,'sources':sources};frame,path=cached_frame(e,q,landfall)
  if frame is None:
   cached=False
   with _job_lock:job.update(message='正在生成所选来源的检验结果，首次读取历史路径可能需要几分钟…')
   frame=e['landfall_verification_frame'](storm,p['year'],landfall,sources,None,agency)
   # A Pangu run that could not be read must be retried, not remembered.
   if sources!=[PANGU] or ('登陆时刻预报点误差(km)' in frame and frame['登陆时刻预报点误差(km)'].notna().any()):_write_disk_result(path,frame)
  frames.append(frame);paths.append(path)
 return (pd.concat(frames,ignore_index=True) if len(frames)>1 else frames[0]),paths[0],cached

def compute(job,p):
 try:
  with _job_lock:job.update(status='running',message='正在读取服务器台风检验缓存…')
  e,meta,storm,agency,landfalls,warning=storm_context(p['year'],p['storm_id'])
  if not 0<=p['landfall']<len(landfalls):raise ValueError('所选台风没有这条官方登陆记录。')
  landfall=landfalls[p['landfall']];frame,path,cached=source_frames(e,p,storm,landfall,agency,job)
  payload=result_payload(e,p,storm,landfall,frame,path,cached,warning)
  with _job_lock:job.update(status='done',message='检验结果已加载',result=payload,finished=time.time())
 except ValueError as exc:
  with _job_lock:job.update(status='error',message=str(exc),finished=time.time())
 except Exception:
  with _job_lock:job.update(status='error',message='台风资料暂不可用，请刷新重试或更换台风。',finished=time.time())

def submit(raw):
 return enqueue(selection(raw),compute)

def submit_tracks(raw):
 return enqueue(track_selection(raw),compute_tracks)

def enqueue(p,work):
 token=hashlib.sha256(json.dumps([work.__name__,p],sort_keys=True).encode()).hexdigest()
 with _job_lock:
  old=_jobs.get(token)
  if old and (old['status'] in ['queued','running'] or old['status']=='done' and time.time()-old.get('finished',0)<300):return public_job(old)
  if any(j['status'] in ['queued','running'] for j in _jobs.values()):raise QueueBusy('已有台风检验正在读取，请稍后重试。')
  for key in list(_jobs):
   if len(_jobs)<12:break
   if _jobs[key]['status'] not in ['queued','running']:del _jobs[key]
  job=dict(id=token,status='queued',message='等待读取台风检验数据…');_jobs[token]=job;_pool.submit(work,job,p)
  return public_job(job)

def job_status(token):
 with _job_lock:
  if token not in _jobs:raise ValueError('检验任务已失效，请重新选择条件。')
  return public_job(_jobs[token])
