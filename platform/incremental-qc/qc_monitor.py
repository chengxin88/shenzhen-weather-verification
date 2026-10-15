#!/usr/bin/env python3
"""Read-only, publication-triggered forecast QC. No historical backfill."""
import argparse
import collections
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.parse
import urllib.request
import numpy as np

UTC=dt.timezone.utc
BJT=dt.timezone(dt.timedelta(hours=8))
MODELS=('ecmwf','gfs','aifs','aigfs','weathernext3')
ROOT=Path('/home/chengyang/gods-eye-weather/weather-data/region-v4')

def stamp(value):
    t=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None: raise ValueError('timestamp must contain timezone')
    return t

def signature(p):
    s=p.stat()
    return str(s.st_mtime_ns)+':'+str(s.st_size)

def array_check(a,key,shape,problems):
    if a.shape!=shape or not np.issubdtype(a.dtype,np.number):
        problems.append(('error',key+': invalid shape or dtype'));return
    finite=np.isfinite(a)
    if not finite.all():
        problems.append(('error' if not finite.any() else 'warning',key+': nonfinite '+str(int(a.size-finite.sum()))+'/'+str(a.size)))
    if not finite.any():return
    v=a[finite];lo=float(v.min());hi=float(v.max())
    bounds=None
    if key=='surface_t':bounds=(-100,80)
    elif key.endswith('_t'):bounds=(-150,80)
    elif key.endswith('_rh'):bounds=(-0.1,100.5)
    elif key.endswith(('_u','_v')):bounds=(-200,200)
    elif key=='surface_msl':bounds=(800,1150)
    elif key.startswith('rain'):bounds=(-0.01,20000 if 'accum' in key else 1000)
    elif key=='pwat':bounds=(-0.01,150)
    if bounds and (lo<bounds[0] or hi>bounds[1]):
        problems.append(('warning',key+': range ['+str(round(lo,4))+', '+str(round(hi,4))+']'))
    if lo==hi and not key.startswith('rain'):
        problems.append(('warning',key+': spatially constant'))

def check_frame(p,started):
    problems=[]
    d=json.loads(p.read_text())
    run=stamp(d['run']);valid=stamp(d['valid']);lead=d['lead']
    if run.timestamp()<started-86400:
        return {'status':'skipped','detail':['historical run excluded'],'run':d['run'],'lead':lead,'arrays':0}
    slug=p.parent.name
    if d.get('schema')!=2 or type(lead) is not int or lead<0 or valid!=run+dt.timedelta(hours=lead):
        problems.append(('error','schema/run/lead/valid inconsistent'))
    import re
    m=re.match(re.escape(slug)+r'_(\d{10})_f(\d{3})(?:_|\.)',p.name)
    if not m or m[1]!=run.strftime('%Y%m%d%H') or int(m[2])!=lead:
        problems.append(('error','filename identity mismatch'))
    identities={'ecmwf':'ECMWF-IFS','gfs':'NCEP-GFS','aifs':'ECMWF-AIFS','aigfs':'NCEP-AIGFS','weathernext3':'Google WeatherNext 3（集合均值）'}
    if d.get('model')!=identities[slug]:problems.append(('error','model identity mismatch'))
    x=np.asarray(d['lon'],float);y=np.asarray(d['lat'],float)
    for name,axis in [('lon',x),('lat',y)]:
        if axis.ndim!=1 or len(axis)<2 or not np.isfinite(axis).all() or not (np.diff(axis)>0).all():
            problems.append(('error',name+': invalid coordinate axis'))
        elif not np.allclose(np.diff(axis),np.diff(axis)[0],rtol=0,atol=1e-6):
            problems.append(('error',name+': nonuniform spacing'))
    shape=(len(y),len(x));name=d.get('fields_directory',p.stem+'.fields')
    if name not in (p.stem+'.fields',p.stem+'.score-fields'):
        raise ValueError('invalid field directory')
    fd=p.parent/name
    if fd.is_symlink() or fd.resolve().parent!=p.parent.resolve():raise ValueError('unsafe field directory')
    keys={q.stem:q for q in fd.glob('*.npy')}
    expected={'surface_t','surface_u','surface_v','surface_msl'}
    support_rh=d.get('capabilities',{}).get('surface_rh',slug!='aigfs')
    if support_rh:expected.add('surface_rh')
    for level in d.get('levels',[]):
        if str(level)!='surface':expected.update(str(level)+'_'+f for f in ('t','u','v','rh','gh'))
    if d.get('rain_start'):
        rain_start=stamp(d['rain_start'])
        interval=(valid-rain_start).total_seconds()/3600
        if not run<=rain_start<valid or interval not in (1,3,6) or d.get('interval_hours',interval)!=interval:
            problems.append(('error','rain accumulation time inconsistent'))
        else:expected.add('rain'+str(int(interval))+'h')
    arrays=0
    if keys:
        for key in sorted(expected-set(keys)):problems.append(('error',key+': missing field'))
        for key,q in sorted(keys.items()):
            if key=='surface_rh' and not support_rh:continue
            try:
                array_check(np.load(q,mmap_mode='r',allow_pickle=False),key,shape,problems);arrays+=1
            except Exception:problems.append(('error',key+': unreadable NPY'))
    else:
        embedded={str(level)+'_'+key:v for level,f in d.get('layers',{}).items() for key,v in f.items() if isinstance(v,list)}
        embedded.update({k:d[k] for k in ('rain1h','rain3h','rain6h','rain_accum_mm') if isinstance(d.get(k),list)})
        if not embedded:problems.append(('error','published frame has no data'))
        for key,v in embedded.items():
            try:
                a=np.asarray(v,float)
                if a.size!=shape[0]*shape[1]:problems.append(('error',key+': invalid embedded size'));continue
                array_check(a.reshape(shape),key,shape,problems);arrays+=1
            except Exception:problems.append(('error',key+': unreadable embedded data'))
    if slug=='weathernext3':
        manifest=p.parent/('run_'+run.strftime('%Y%m%d%H')+'.json')
        if not manifest.is_file():
            return {'status':'pending','detail':['native publication manifest not yet available'],'arrays':arrays}
        else:
            manifest_data=json.loads(manifest.read_text());directory=manifest_data.get('native_cache_directory')
            committed={f['lead'] for f in manifest_data.get('frames',[])}
            if directory not in ('native-score-v1','native-china-v1','native-southern-china-v1'):
                problems.append(('warning','unsupported native cache directory'))
            elif lead not in committed:
                return {'status':'pending','detail':['native hour not yet committed'],'arrays':arrays}
            else:
                for key,unit in [('surface_t','degC'),('rain1h','mm')]:
                    q=p.parent/directory/run.strftime('%Y%m%d%H')/('f'+str(lead).zfill(3)+'_'+key+'.npz')
                    try:
                        with np.load(q,allow_pickle=False) as z:
                            meta=json.loads(str(z['meta'].item()));a=z['values'];nx=z['lon'];ny=z['lat']
                            if (stamp(meta['run'])!=run or stamp(meta['valid'])!=valid or meta.get('lead')!=lead or meta.get('field')!=key or meta.get('unit')!=unit or meta.get('statistic')!='mean'):
                                problems.append(('error',key+': native identity/unit mismatch'))
                            bounds=meta.get('bounds')
                            if not bounds or len(bounds)!=4 or not np.allclose(nx,np.arange(round(bounds[0]*10),round(bounds[2]*10)+1)/10,atol=1e-7,rtol=0) or not np.allclose(ny,np.arange(round(bounds[1]*10),round(bounds[3]*10)+1)/10,atol=1e-7,rtol=0):
                                problems.append(('error',key+': native grid mismatch'))
                            array_check(a,key,(len(ny),len(nx)),problems);arrays+=1
                    except Exception:problems.append(('error',key+': missing/unreadable committed native file'))
    status='error' if any(a=='error' for a,b in problems) else 'warning' if problems else 'pass'
    return {'status':status,'detail':[a+': '+b for a,b in problems],'run':d['run'],'lead':lead,'arrays':arrays}

class Monitor:
    def __init__(self,root,state,stable=30):
        self.root=Path(root);self.state=Path(state);self.state.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.state/'qc.sqlite');self.stable=stable
        self.db.executescript('CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT); CREATE TABLE IF NOT EXISTS seen(path TEXT PRIMARY KEY,sig TEXT,pending TEXT,changed REAL); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at REAL,path TEXT,sig TEXT,result TEXT,UNIQUE(path,sig)); CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY,k TEXT UNIQUE,content TEXT,tries INTEGER DEFAULT 0,next REAL DEFAULT 0,sent REAL);')
        row=self.db.execute("SELECT v FROM meta WHERE k='started'").fetchone();self.started=float(row[0]) if row else None
    def files(self):
        for slug in MODELS:
            yield from (self.root/slug).glob(slug+'_*.json')
    def init(self):
        if self.started is not None:raise RuntimeError('already initialized; refusing to reset baseline')
        if not self.root.is_dir():raise RuntimeError('data root missing')
        self.started=time.time()
        self.db.execute("INSERT INTO meta VALUES('started',?)",(str(self.started),))
        for p in self.files():self.db.execute('INSERT OR REPLACE INTO seen VALUES(?,?,NULL,NULL)',(str(p),signature(p)))
        self.db.commit()
    def enqueue(self,k,text):
        self.db.execute('INSERT OR IGNORE INTO outbox(k,content) VALUES(?,?)',(k,text));self.db.commit()
    def scan(self):
        if self.started is None:raise RuntimeError('run --init first; history must not be scanned')
        if not self.root.is_dir():raise RuntimeError('data root unavailable')
        now=time.time();done=0
        for p in self.files():
            sig=signature(p);row=self.db.execute('SELECT sig,pending,changed FROM seen WHERE path=?',(str(p),)).fetchone()
            if row and row[0]==sig:continue
            if not row or row[1]!=sig:
                self.db.execute('INSERT OR REPLACE INTO seen VALUES(?,?,?,?)',(str(p),row[0] if row else None,sig,now));continue
            if now-row[2]<self.stable:continue
            try:result=check_frame(p,self.started)
            except Exception as exc:result={'status':'error','detail':['cannot validate frame: '+type(exc).__name__],'arrays':0}
            if signature(p)!=sig:continue
            if result['status']=='pending':
                if now-row[2]<1800:continue
                result['status']='warning';result['detail'].append('native publication delayed over 30 minutes')
            self.db.execute('INSERT OR IGNORE INTO events(at,path,sig,result) VALUES(?,?,?,?)',(now,str(p),sig,json.dumps(result,ensure_ascii=False)))
            self.db.execute('UPDATE seen SET sig=?,pending=NULL,changed=NULL WHERE path=?',(sig,str(p)))
            if result['status'] in ('warning','error'):
                text='### 模式数据质控异常\n> 文件：'+p.name+'\n> 级别：'+result['status']+'\n> 详情：'+'；'.join(result['detail'][:8])+'\n> 检查时间：'+dt.datetime.fromtimestamp(now,BJT).isoformat(timespec='seconds')
                self.enqueue(hashlib.sha256((str(p)+sig).encode()).hexdigest(),text)
            done+=1
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('last_scan',?)",(str(now),));self.db.commit();return done
    def daily(self,now=None):
        now=now or dt.datetime.now(BJT);now=now.astimezone(BJT)
        cutoff=now.replace(hour=20,minute=0,second=0,microsecond=0)
        if now<cutoff or cutoff.timestamp()<self.started:return
        start=cutoff.replace(hour=0).timestamp();rows=self.db.execute('SELECT result FROM events WHERE at>=? AND at<=?',(start,cutoff.timestamp())).fetchall()
        counts=collections.Counter(json.loads(r[0])['status'] for r in rows)
        pending=self.db.execute('SELECT count(*) FROM seen WHERE pending IS NOT NULL').fetchone()[0]
        last=self.db.execute("SELECT v FROM meta WHERE k='last_scan'").fetchone()
        if not last or time.time()-float(last[0])>120:
            conclusion='监控状态异常或检查未完成，不能报告无异常'
        elif counts['error'] or counts['warning']:conclusion='发现异常／待复核，请查看即时告警'
        elif pending:conclusion='仍有数据待质控，暂不能报告无异常'
        elif not counts['pass']:conclusion='今日截至20:00未完成新数据质控（无新数据或仅有跳过项），不能判定无异常'
        else:conclusion='截至20:00，已检查的新数据无异常（不包含历史资料）'
        text='### 模式数据质控日报 '+now.date().isoformat()+'\n> '+conclusion+'\n> 通过 '+str(counts['pass'])+'；警告 '+str(counts['warning'])+'；错误 '+str(counts['error'])+'；跳过历史 '+str(counts['skipped'])+'；待检查 '+str(pending)+'\n> 统计范围：北京时间当日00:00—20:00；20:00后的事件继续即时告警。'
        self.enqueue('daily:'+now.date().isoformat(),text)
    def flush(self):
        url=os.environ.get('WECOM_QC_WEBHOOK','')
        if not url:return
        parts=urllib.parse.urlsplit(url)
        if parts.scheme!='https' or parts.netloc!='qyapi.weixin.qq.com' or parts.path!='/cgi-bin/webhook/send' or not urllib.parse.parse_qs(parts.query).get('key'):
            raise RuntimeError('invalid webhook configuration')
        now=time.time();row=self.db.execute('SELECT id,content,tries FROM outbox WHERE sent IS NULL AND next<=? ORDER BY id LIMIT 1',(now,)).fetchone()
        if not row:return
        ident,content,tries=row
        try:
            while len(content.encode())>3900:content=content[:-100]
            payload=json.dumps({'msgtype':'markdown','markdown':{'content':content}}).encode()
            request=urllib.request.Request(url,data=payload,headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=15) as response:r=json.load(response)
            if r.get('errcode')!=0:raise RuntimeError('message API rejected request')
            self.db.execute('UPDATE outbox SET sent=? WHERE id=?',(now,ident))
        except Exception:
            self.db.execute('UPDATE outbox SET tries=tries+1,next=? WHERE id=?',(now+min(3600,30*2**min(tries,7)),ident))
            print('Notification failed; retained for retry',flush=True)
        self.db.commit()
    def status(self):
        last=self.db.execute("SELECT v FROM meta WHERE k='last_scan'").fetchone()
        return dict(started=self.started,last_scan=float(last[0]) if last else None,events=self.db.execute('SELECT count(*) FROM events').fetchone()[0],pending=self.db.execute('SELECT count(*) FROM seen WHERE pending IS NOT NULL').fetchone()[0],unsent=self.db.execute('SELECT count(*) FROM outbox WHERE sent IS NULL').fetchone()[0],notification_configured=bool(os.environ.get('WECOM_QC_WEBHOOK')))

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default=str(ROOT));p.add_argument('--state',default=str(Path.home()/'.local/state/weather-qc'));p.add_argument('--init',action='store_true');p.add_argument('--once',action='store_true');p.add_argument('--status',action='store_true');p.add_argument('--daemon',action='store_true');a=p.parse_args()
    m=Monitor(a.root,a.state)
    if a.init:m.init()
    elif a.status:pass
    elif a.daemon:
        while True:
            try:m.scan();m.daily();m.flush()
            except Exception as exc:
                print('Monitor unhealthy: '+type(exc).__name__,flush=True)
                m.enqueue('health:'+dt.datetime.now(BJT).strftime('%Y%m%d%H'),'### 质控监控异常\n检查未完成，不能报告数据无异常。请检查监控日志。')
                try:m.flush()
                except Exception:pass
            time.sleep(15)
    elif a.once:m.scan();m.daily();m.flush()
    else:p.error('choose --init/--once/--status/--daemon')
    print(json.dumps(m.status()))
if __name__=='__main__':main()
