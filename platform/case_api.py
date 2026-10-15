"""Native case API. Reuse the installed scientific functions without the Streamlit shell."""
import ast
import gzip
import hashlib
import json
import math
import os
import sys
import threading
import time
import urllib.request
import urllib.error
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

APP = Path(os.getenv('VERIFICATION_APP_ROOT', '/home/chengyang/ec-gfs-server'))
KINDS = ['降水', '2米气温', '10米风速', '等压面', '垂直廓线']
LEVELS = ['925', '850', '700', '500', '200']
SOURCES = ['sz-open-data-v1', 'ana-sfc-alpha', 'ana-sfc-gd', 'ana-3d-alpha']
_engine = None
_engine_lock = threading.Lock()
_jobs = {}
_job_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='case-score')

class QueueBusy(ValueError):
    pass

def engine():
    global _engine
    with _engine_lock:
        if _engine is not None: return _engine
        import numpy as np
        import pandas as pd
        from shapely.geometry import shape, Point
        from shapely.ops import unary_union
        from shapely.prepared import prep
        # This is trusted installed code, not a user-supplied path or expression.
        sys.path.insert(0, str(APP))
        import case_verification_core as core
        scope = dict(np=np, pd=pd, shape=shape, Point=Point, unary_union=unary_union,
            prep=prep, gzip=gzip, hashlib=hashlib, json=json, os=os, time=time,
            urllib=urllib, Path=Path, datetime=datetime, timedelta=timedelta,
            timezone=timezone, defaultdict=defaultdict,
            BASE='http://127.0.0.1:8505', BJT=timezone(timedelta(hours=8)),
            LABELS={'t':'温度','wind':'风速','height':'位势高度','rain':'降水'},
            CACHE_SCHEMA='case-score-v2', CACHE_DIR=APP/'cache'/'case_verification')
        for name in ['VERSION','RUNS','MODELS','values','validate_frame','continuous_stats','continuous_scores','rain_stats','rain_scores']:
            scope[name] = getattr(core, name)
        wanted = {'bjt','request','cache_token','load_disk_result','save_disk_result','master_signature',
                  'subset_result','field','city_points','get_frame','combined_values','calculate','summary_rows'}
        tree = ast.parse((APP/'case_verification.py').read_text(encoding='utf-8'))
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
        assert {n.name for n in nodes} == wanted
        for node in nodes: node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP/'case_verification.py'), 'exec'), scope)
        # Bound RAM use; numerical interpolation and scoring bodies are unchanged.
        native_field=scope['field']
        @lru_cache(maxsize=2)
        def compact_field(generation,identity,level):
            frame=native_field(generation,identity,level)
            # Preserve float64 values while avoiding one Python object per grid cell.
            for fields in frame.get('fields',{}).values():
                for name,values in fields.items():fields[name]=np.asarray(values,dtype=np.float64)
            return frame
        scope['field'] = compact_field
        scope['city_points'] = lru_cache(maxsize=2)(scope['city_points'])
        import case_opendata as opened
        native_get_frame=scope['get_frame']
        def get_frame(catalog,entry,level,hours):
            if entry['model']==opened.SOURCE:
                return scope['validate_frame'](opened.get_frame(entry,level,hours),entry,level,hours)
            return native_get_frame(catalog,entry,level,hours)
        scope['get_frame']=get_frame
        _engine = scope
        return scope

def source_catalog():
    e = engine()
    status = e['request']('/status')
    if not status.get('online'): raise ValueError('个例数据源暂时离线，请连接数据源后重试。')
    catalog = e['request']('/catalog')
    catalog['generation'] = status['generation']
    return catalog

def metadata():
    catalog = source_catalog(); e = engine()
    entries = [x for x in catalog['entries'] if x.get('kind') != 'analysis' and x.get('run') in e['RUNS'] and x['model'] in e['MODELS']]
    labels = {m['id']:m['label'].replace('知己','智霁') for m in catalog['models']}
    labels['sz-open-data-v1']='深圳开放数据格点分析场'
    capabilities = []
    for model in e['MODELS']:
        caps = {run:sorted({lev for x in entries if x['model']==model and x['run']==run for lev in x.get('levels',[])}) for run in e['RUNS']}
        capabilities.append({'id':model, 'label':labels.get(model,model), 'runs':caps})
    return {'title':'深圳市 · 7月25日个例检验', 'date':'2026-07-25', 'generation':catalog['generation'], 'cache_version':'case-view-v1:'+str(e['VERSION']),
        'models':capabilities, 'runs':[{'id':r,'label':'08时' if r.endswith('00') else '20时'} for r in e['RUNS']],
        'kinds':KINDS, 'levels':LEVELS, 'sources':[{'id':s,'label':labels.get(s,s)} for s in SOURCES],
        'reference':'深圳开放数据格点分析场', 'notice':'2026年7月25日08／20时起报；按实际有效时刻配对所选实况分析场，参考资料并非独立站点观测。'}

def selection(raw):
    if not isinstance(raw,dict): raise ValueError('检验条件格式不正确。')
    e=engine(); kind=raw.get('kind','降水')
    if kind not in KINDS: raise ValueError('未知检验类别。')
    def choose(name, allowed, default):
        val=raw.get(name,default)
        if not isinstance(val,list) or not val or any(x not in allowed for x in val) or len(val)!=len(set(val)):
            raise ValueError('请正确选择'+name+'。')
        return [x for x in allowed if x in val]
    runs=choose('runs',e['RUNS'],[e['RUNS'][0]])
    models=choose('models',e['MODELS'],list(e['MODELS']))
    window=raw.get('window',0)
    if type(window) is not int or window not in [0,24,48]: raise ValueError('无效预报时效。')
    hours=raw.get('hours',6) if kind=='降水' else 0
    if kind=='降水' and (type(hours) is not int or hours not in [2,6,12,24]): raise ValueError('无效累计时长。')
    thresholds=raw.get('thresholds',[.1,10,25,50]) if kind=='降水' else []
    if kind=='降水' and (not isinstance(thresholds,list) or not 1<=len(thresholds)<=12 or any(type(t) not in [int,float] or not math.isfinite(t) or t<=0 for t in thresholds)):
        raise ValueError('请输入1—12个大于0的有限降水阈值。')
    thresholds=sorted(set(float(t) for t in thresholds))
    fields=choose('fields',['t','wind','height'],['t','wind','height']) if kind in ['等压面','垂直廓线'] else [{'降水':'rain','2米气温':'t','10米风速':'wind'}[kind]]
    levels=choose('levels',LEVELS,['850'] if kind=='等压面' else ['925','850','700']) if kind in ['等压面','垂直廓线'] else ['surface']
    if kind=='等压面' and len(levels)!=1: raise ValueError('等压面检验请选择一层。')
    source=raw.get('source','sz-open-data-v1') if levels==['surface'] else 'ana-3d-alpha'
    if source not in SOURCES or (levels==['surface'] and source=='ana-3d-alpha'): raise ValueError('无效实况来源。')
    spacing=raw.get('spacing',.03); common=raw.get('common',True)
    if spacing not in [.03,.01] or type(common) is not bool: raise ValueError('无效样本设置。')
    return dict(kind=kind,runs=runs,models=models,window=window,hours=hours,thresholds=thresholds,
                fields=fields,levels=levels,source=source,spacing=spacing,common=common)

def prepare(p,catalog):
    e=engine(); hours=p['hours']; kind=p['kind']
    required=['verify_compare_surface' if hours==2 else 'verify_compare_surface_6h'] if kind=='降水' else ['verify_'+x for x in p['levels']]
    for model in p['models']:
        if (kind=='2米气温' and model=='feed') or (kind=='降水' and hours==2 and model in ['cma9','pangu','zhiji']):
            raise ValueError('所选模式不支持当前检验条件。')
        if not all(any(x.get('model')==model and x.get('run')==run and all(l in x.get('levels',[]) for l in required) for x in catalog['entries']) for run in p['runs']):
            raise ValueError('所选模式、起报或层次没有可评分资料，请调整条件。')
    step=2 if kind=='降水' and hours==2 else 6
    minimum=max(p['window']+1,hours if kind=='降水' else step)
    leads=range(((minimum+step-1)//step)*step,p['window']+25,step)
    times=sorted({(datetime.strptime(run,'%Y%m%d%H').replace(tzinfo=timezone.utc)+timedelta(hours=lead)).isoformat().replace('+00:00','Z') for run in p['runs'] for lead in leads})
    window=(p['window'],p['window']+24)
    signature=(e['VERSION'],tuple(p['runs']),tuple(p['models']),tuple(times),window,kind,tuple(p['fields']),tuple(p['levels']),hours,tuple(p['thresholds']),p['source'],p['spacing'],p['common'],catalog['generation'])
    return times,window,signature

def clean(value):
    if isinstance(value,dict): return {k:clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [clean(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value): return None
    return value

def result_payload(result,p,cached):
    e=engine(); frame=e['pd'].DataFrame(result['scores'])
    summary=e['summary_rows'](frame,p['kind']=='降水').to_dict('records') if not frame.empty else []
    reference={'sz-open-data-v1':'深圳开放数据格点分析场','ana-sfc-alpha':'本地地面实况分析 · alpha','ana-sfc-gd':'本地地面实况分析 · gd','ana-3d-alpha':'本地三维实况分析 · alpha'}[p['source']]
    run_coverage=[{'run':run,'valid_times':sorted({r['有效时间'] for r in result['scores'] if r['起报']==('07-25 08时' if run.endswith('00') else '07-25 20时')}),'leads':sorted({r['时效(h)'] for r in result['scores'] if r['起报']==('07-25 08时' if run.endswith('00') else '07-25 20时')})} for run in p['runs']]
    return clean({**result,'summary':summary,'selection':p,'cached':cached,'reference':reference,'run_coverage':run_coverage,
                  'method':'双线性插值到深圳市界内共同等权经纬网格；汇总合并原始统计量；零分母为缺测。'})

class Progress:
    def __init__(self,job): self.job=job
    def progress(self,value,text=''):
        with _job_lock: self.job.update(progress=value,message=text)

def retryable_result(result,p):
    reasons=[str(row.get('原因','')) for row in result.get('issues',[])]
    return any('数据源离线' in reason or '本机读取超时' in reason or
               (p['source']=='sz-open-data-v1' and '深圳开放数据' in reason)
               for reason in reasons)

def saved_result(p,catalog,times,signature):
    """Read historical scores before entering the single-worker computation queue."""
    e=engine()
    result,_=e['load_disk_result'](signature)
    if result is None:
        master,_=e['load_disk_result'](e['master_signature'](p['kind'],p['models'],p['hours'],p['thresholds'],p['source'],p['spacing'],p['common'],catalog['generation']))
        if master is not None:
            result=e['subset_result'](master,p['runs'],times,p['fields'],p['levels'])
    return result if result is not None and not retryable_result(result,p) else None

def compute(job,p,catalog,times,window,signature):
    try:
        e=engine()
        with _job_lock: job.update(status='running',message='读取已保存的个例评分')
        result=saved_result(p,catalog,times,signature); cached=result is not None
        if result is None:
            points=e['city_points']((APP/'data'/'shenzhen_streets.geojson').read_text(encoding='utf-8'),p['spacing'])
            if p['source']=='sz-open-data-v1':
                import case_opendata as opened
                segments=set(times)
                if p['kind']=='降水' and p['hours']>6:
                    segments={ (datetime.fromisoformat(t.replace('Z','+00:00'))-timedelta(hours=h)).isoformat().replace('+00:00','Z') for t in times for h in range(p['hours']-6,-1,-6)}
                catalog={**catalog,'entries':[*catalog['entries'],*[opened.entry(t) for t in sorted(segments)]]}
            result=e['calculate'](catalog,points,p['models'],p['runs'],times,p['kind'],p['fields'],p['levels'],p['hours'],p['thresholds'],p['source'],p['common'],Progress(job),window)
            fresh=e['request']('/status')
            if not fresh.get('online') or fresh['generation']!=catalog['generation']: raise ValueError('数据源离线或目录已更新，请刷新后重新计算。')
            if any('数据源离线' in str(x.get('原因','')) for x in result.get('issues',[])): raise ValueError('计算期间数据源离线，请重试。')
            retryable=retryable_result(result,p)
            if not retryable:e['save_disk_result'](signature,result)
        payload=result_payload(result,p,cached)
        retryable=retryable_result(result,p)
        with _job_lock: job.update(status='done',progress=1,message='评分完成',result=payload,retryable=retryable)
    except ValueError as exc:
        with _job_lock: job.update(status='error',message=str(exc))
    except Exception:
        with _job_lock: job.update(status='error',message='本次检验未完成，请检查数据源后重试。')

def submit(raw):
    p=selection(raw); catalog=source_catalog(); times,window,signature=prepare(p,catalog)
    token=engine()['cache_token'](signature)
    with _job_lock:
        if token in _jobs and _jobs[token]['status']!='error' and not _jobs[token].get('retryable'): return public_job(_jobs[token],cached=True)
    saved=saved_result(p,catalog,times,signature)
    payload=result_payload(saved,p,True) if saved is not None else None
    with _job_lock:
        if token in _jobs and _jobs[token]['status']!='error' and not _jobs[token].get('retryable'): return public_job(_jobs[token],cached=True)
        for old in list(_jobs):
            if len(_jobs)<8: break
            if _jobs[old]['status'] in ['done','error']: del _jobs[old]
        job={'id':token,'status':'queued','progress':0,'message':'等待计算','selection':p,'generation':catalog['generation']}
        if payload is not None:
            job.update(status='done',progress=1,message='已读取缓存评分',result=payload,retryable=False)
            _jobs[token]=job
            return public_job(job)
        if sum(j['status'] in ['queued','running'] for j in _jobs.values())>=3: raise QueueBusy('计算队列已满，正在等待已有任务完成。')
        _jobs[token]=job
        _pool.submit(compute,job,p,catalog,times,window,signature)
        return public_job(job)

def public_job(job,cached=False):
    value=dict(job)
    if cached and value.get('result') is not None:
        value['result']={**value['result'],'cached':True}
    return value

def job_status(token):
    with _job_lock:
        if token not in _jobs: raise ValueError('任务已过期，请重新计算。')
        return public_job(_jobs[token])
