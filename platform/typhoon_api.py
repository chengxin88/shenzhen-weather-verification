"""Native typhoon verification API using the installed scientific calculations."""
import ast
import copy
import hashlib
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from case_api import QueueBusy

APP=Path(os.getenv('VERIFICATION_APP_ROOT','/home/chengyang/ec-gfs-server'))
_engine=None
_engine_lock=threading.Lock()
_jobs={}
_job_lock=threading.Lock()
_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='typhoon-score')
ROOT_FUNCTIONS={'fetch_typhoon_catalog','fetch_typhoon_detail','fetch_agency_typhoon_detail',
 '_typhoon_catalog_frame','_typhoon_track_frame','_agency_landfalls','_storm_number_for_agency',
 '_default_shenzhen_landfall_storm','_typhoon_verification_cache_paths','landfall_verification_frame'}

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
  # The hourly original-platform worker already refreshes this archive. Read it
  # first rather than waiting for remote catalog requests on each page opening.
  nmc=scope['_fetch_nmc_typhoon_json'];agency=scope['_typhoon_fetch_json']
  def cache_first(native):
   def read(url,name):
    path=scope['_typhoon_cache_path'](name)
    try:return json.loads(path.read_text(encoding='utf-8')),True
    except (OSError,ValueError):return native(url,name)
   return read
  scope['_fetch_nmc_typhoon_json']=cache_first(nmc)
  scope['_typhoon_fetch_json']=cache_first(agency)
  colors={'CMA/NMC':'#000000','JMA':'#e69f00','JTWC':'#56b4e9','ECMWF-IFS':'#0072b2','NCEP-GFS':'#d55e00','ECAI':'#009e73','GFSAI':'#cc79a7'}
  scope['TYPHOON_SOURCE_STYLES']={k:{**v,'color':colors.get(k,v['color'])} for k,v in scope['TYPHOON_SOURCE_STYLES'].items()}
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
 all_sources=tuple(e['TYPHOON_SOURCE_STYLES'])
 if tuple(p['sources'])!=all_sources:
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

def public_job(job):
 return {k:job[k] for k in ['id','status','message','result'] if k in job}

def compute(job,p):
 try:
  with _job_lock:job.update(status='running',message='正在读取服务器台风检验缓存…')
  e,meta,storm,agency,landfalls,warning=storm_context(p['year'],p['storm_id'])
  if not 0<=p['landfall']<len(landfalls):raise ValueError('所选台风没有这条官方登陆记录。')
  landfall=landfalls[p['landfall']];frame,path=cached_frame(e,p,landfall);cached=frame is not None
  if frame is None:
   with _job_lock:job.update(message='正在生成所选来源的检验结果，首次读取历史路径可能需要几分钟…')
   frame=e['landfall_verification_frame'](storm,p['year'],landfall,p['sources'],None,agency)
   from seven_day_verification import _write_disk_result
   _write_disk_result(path,frame)
  payload=result_payload(e,p,storm,landfall,frame,path,cached,warning)
  with _job_lock:job.update(status='done',message='检验结果已加载',result=payload,finished=time.time())
 except ValueError as exc:
  with _job_lock:job.update(status='error',message=str(exc),finished=time.time())
 except Exception:
  with _job_lock:job.update(status='error',message='台风资料暂不可用，请刷新重试或更换台风。',finished=time.time())

def submit(raw):
 p=selection(raw);token=hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest()
 with _job_lock:
  old=_jobs.get(token)
  if old and (old['status'] in ['queued','running'] or old['status']=='done' and time.time()-old.get('finished',0)<300):return public_job(old)
  if any(j['status'] in ['queued','running'] for j in _jobs.values()):raise QueueBusy('已有台风检验正在读取，请稍后重试。')
  for key in list(_jobs):
   if len(_jobs)<12:break
   if _jobs[key]['status'] not in ['queued','running']:del _jobs[key]
  job=dict(id=token,status='queued',message='等待读取台风检验数据…');_jobs[token]=job;_pool.submit(compute,job,p)
  return public_job(job)

def job_status(token):
 with _job_lock:
  if token not in _jobs:raise ValueError('检验任务已失效，请重新选择条件。')
  return public_job(_jobs[token])
