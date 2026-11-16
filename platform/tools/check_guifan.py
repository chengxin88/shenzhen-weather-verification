#!/usr/bin/env python3
"""Read-only exact comparison and independent attachment 7 checks.

Usage: check_guifan.py OLD NEW [--pairs pairs.sqlite] [--sample 32]
OLD/NEW: station cache/snapshot directory, catalog JSON, or rows JSON (optionally gzip).
No scoring modules are imported. JSON floats compare by their binary64 encoding.
"""
import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime,timedelta,timezone
import gzip
import json
from pathlib import Path
import sqlite3
import struct
import sys

LEGACY=('n','H','M','F','C','TS','ETS','POD','FAR','MR','BIAS','ME','MAE','RMSE','CC','VRMSE')
KEYS=('dataset','station','model','var','run','lead','valid','threshold','grade','lead_min','lead_max','days')
BINS={3:[.1,3,10,20,50,70],24:[.1,10,25,50,100,250]}
MODEL={'ec01':'ECMWF-IFS-9km-OpenMeteo','ecmwf':'ECMWF-IFS','ecens':'ECMWF-ENSEMBLES平均','gfs':'NCEP-GFS','aifs':'ECAI','aigfs':'GFSAI','gefs':'GEFS集合平均','wn3':'Google WeatherNext 3（集合均值）','wn3ml':'WN3 订正（LightGBM）'}
XJ=('52112','52203','51495')


def read(path):
    with (gzip.open(path,'rt') if path.suffix=='.gz' else path.open()) as handle:return json.load(handle)


def snapshot(path):
    return path/'snapshots'/read(path/'current.json')['generation'] if (path/'current.json').exists() else path


def connect(path):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    db.execute('PRAGMA cache_size=-4000')
    return closing(db)


def products(path):
    if path.is_file():
        value=read(path)
        return {'rows':value if isinstance(value,list) else value['rows']}
    directory=snapshot(path);out={}
    for pattern in ('scores-*.json','station-scores-*.json','grid-rows-*.json.gz'):
        for file in sorted(directory.glob(pattern)):out[file.name]=read(file)
    if (directory/'pairs.sqlite').is_file():
        with connect(directory/'pairs.sqlite') as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='detail_stats'").fetchone():
                out['detail_stats']=[dict(json.loads(stats),days=days) for days,stats in db.execute('SELECT days,stats FROM detail_stats')]
    if not out:raise ValueError('No score products found: '+str(path))
    return out


def exact(a,b):
    if isinstance(a,float) or isinstance(b,float):
        return isinstance(a,(int,float)) and isinstance(b,(int,float)) and struct.pack('!d',a)==struct.pack('!d',b)
    return a==b


def identity(row,exclude=()):return tuple(row.get(k) for k in KEYS if k not in exclude)


class Check:
    def __init__(self):self.counts=defaultdict(int);self.errors=[];self.failures=0
    def require(self,ok,message):
        self.counts['checks']+=1
        if not ok:
            self.failures+=1
            if len(self.errors)<20:self.errors.append(message)
    def compare(self,old,new,label):
        index={identity(r):r for r in new}
        self.require(len(index)==len(new),label+': duplicate row identity')
        for r in old:
            key=identity(r);other=index.get(key)
            self.require(other is not None,label+': missing old row '+str(key))
            if other is None:continue
            self.counts['legacy_rows']+=1
            for metric in LEGACY:
                if metric in r:self.require(metric in other and exact(r[metric],other[metric]),f'{label} {key}: changed {metric}')
    def counters(self,rows,label):
        groups=defaultdict(list)
        for r in rows:
            if 'N1' in r or 'N2' in r:
                self.counts['accuracy_rows']+=1
                self.require(all(isinstance(r.get(k),int) for k in ('N1','N2')) and 0<=r['N1']<=r['N2']<=r['n'],label+': N1/N2 bounds')
            if 'T' in r:
                self.counts['trace_rows']+=1
                self.require(r.get('threshold')==.1 and isinstance(r['T'],int) and 0<=r['T']<=r['F']<=r['n'],label+': T/F bounds')
                if 'GO' in r and 'GU' in r:self.require(0<=r['GO'] and 0<=r['GU'] and r['GO']+r['GU']<=r['H'],label+': cross-grade wet bounds')
            for metric,field in [('ACC1','N1'),('ACC2','N2'),('PC','T')]:
                if metric in r and field in r:
                    expected=(r['n']-r['M']-r['F']+r['T'])/r['n'] if metric=='PC' and r['n'] else r[field]/r['n'] if r['n'] else None
                    self.require(exact(r[metric],expected),label+': derived '+metric)
            groups[identity(r,('threshold','grade'))].append(r)
        for key,items in groups.items():
            grades={r['grade']:r for r in items if r.get('grade')}
            if not grades:continue
            self.counts['grade_groups']+=1
            self.require(set(grades)==set(range(1,7)),label+': incomplete grade group '+str(key))
            if len(grades)!=6:continue
            for r in grades.values():
                self.require(r.get('threshold') is None,label+': grade has threshold')
                if r.get('ETS') is not None:
                    h,m,f,nn=r['H'],r['M'],r['F'],r['n'];ch=(h+m)*(h+f)/nn
                    self.require(h+m+f-ch!=0 and abs(r['ETS']-(h-ch)/(h+m+f-ch))<1e-12,label+': grade ETS formula')
                self.require(all(isinstance(r[k],int) and r[k]>=0 for k in ('n','H','M','F')),label+': invalid grade count')
            self.require(len({r['n'] for r in grades.values()})==1,label+': inconsistent grade n')
            n=grades[1]['n']
            self.require(sum(r[k] for r in grades.values() for k in ('H','M','F'))<=n,label+': grade sample counted twice')
            by_thr={r['threshold']:r for r in items if r.get('threshold') is not None}
            low=by_thr.get(.1)
            # Table 3-1 assigns each pair only once. Cross-grade wet pairs
            # make the uncorrected A+C/Owet and A+B/Fwet equalities false.
            if low is not None and 'GO' in low and 'GU' in low:
                self.require(sum(r['H']+r['M'] for r in grades.values())+low['GO']==low['H']+low['M'],label+': observed wet margin (+GO)')
                self.require(sum(r['H']+r['F'] for r in grades.values())+low['GU']==low['H']+low['F'],label+': forecast wet margin (+GU)')
            ds=items[0].get('dataset','');var=items[0].get('var','')
            hours=24 if var=='rain24' or ds.endswith('-24') else 3
            top=by_thr.get(BINS[hours][-1])
            if top is not None:
                self.require(all(top[k]==grades[6][k] for k in ('H','M','F','n')),label+': highest cumulative vs grade 6')

    def sqlite_legacy(self,old,new,table,keys,exclude=()):
        with connect(old) as a,connect(new) as b:
            columns=[r[1] for r in a.execute(f'PRAGMA table_info({table})') if r[1] not in exclude]
            if not columns:return
            cols=','.join(columns);order=','.join(keys)
            ca=iter(a.execute(f'SELECT {cols} FROM {table} ORDER BY {order}'))
            cb=iter(b.execute(f'SELECT {cols} FROM {table} ORDER BY {order}'))
            sentinel=object()
            while True:
                left,right=next(ca,sentinel),next(cb,sentinel)
                if left is sentinel and right is sentinel:break
                self.counts[table+'_rows']+=1
                self.require(left is not sentinel and right is not sentinel and all(exact(x,y) for x,y in zip(left,right)),table+': changed legacy pair/stat row')

    def sample_pairs(self,path,rows,limit):
        # Deterministic spread of whole station forecast groups. Recount ALL
        # members of each selected group, never compare a truncated pair sample.
        groups=defaultdict(list)
        for r in rows:
            if r.get('dataset','').startswith('station-'):groups[identity(r,('threshold','grade'))].append(r)
        keys=sorted(groups,key=repr)
        chosen=keys if len(keys)<=limit else [keys[i*len(keys)//limit] for i in range(limit)]
        with connect(path) as db:
            for key in chosen:
                items=groups[key];first=items[0]
                variable={'station-气温-2m':'t','station-降水-6':'rain6','station-降水-12':'rain12','station-降水-24':'rain24'}.get(first['dataset'])
                if variable is None:continue
                model=next((m for m,label in MODEL.items() if label==first['model']),None)
                if model is None:continue
                utc=lambda s:datetime.fromisoformat(s).replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                pairs=db.execute("SELECT fc,ob,trace FROM pairs WHERE model=? AND run=? AND lead=? AND valid=? AND var=? AND station NOT IN (?,?,?)",(model,utc(first['run']),first['lead'],utc(first['valid']),variable,*XJ)).fetchall()
                self.counts['sample_groups']+=1;self.counts['sample_pairs']+=len(pairs)
                self.require(len(pairs)==first['n'],'sample group n differs')
                for r in items:
                    if variable=='t':
                        for k in (1,2):self.require(r.get('N'+str(k))==sum(abs(f-o)<=k+1e-6 for f,o,t in pairs),'sample N'+str(k))
                    else:
                        hours=int(variable[4:]);k=r.get('grade');thr=r.get('threshold')
                        if k:
                            bounds=BINS[hours];classify=lambda x:sum(x>=v for v in bounds)
                            f_o=[(classify(f),classify(o)) for f,o,t in pairs]
                            expected=dict(H=sum(f==o==k for f,o in f_o),F=sum(f==k and f>o for f,o in f_o),M=sum(o==k and f<o for f,o in f_o))
                        else:
                            expected=dict(H=sum(f>=thr and o>=thr for f,o,t in pairs),M=sum(f<thr and o>=thr for f,o,t in pairs),F=sum(f>=thr and o<thr for f,o,t in pairs))
                            if thr==.1:expected['T']=sum(f>=.1 and o<.1 and bool(t) for f,o,t in pairs)
                        for field,value in expected.items():self.require(r.get(field)==value,'sample '+field)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('old',type=Path);p.add_argument('new',type=Path)
    p.add_argument('--pairs',type=Path);p.add_argument('--sample',type=int,default=32);args=p.parse_args(argv)
    check=Check()
    try:
        old,new=products(args.old),products(args.new)
        for label,rows in old.items():check.compare(rows,new.get(label,[]),label)
        for label,rows in new.items():check.counters(rows,label)
        if args.old.is_dir() and args.new.is_dir():
            a,b=snapshot(args.old),snapshot(args.new)
            for file,table,keys,exclude in [('pairs.sqlite','pairs',['model','run','lead','station','var'],('trace',)),
                                          ('grid-map.sqlite','daily',['model','var','day','lead','station'],())]:
                if (a/file).is_file() and (b/file).is_file():check.sqlite_legacy(a/file,b/file,table,keys,exclude)
        pairs=args.pairs or (snapshot(args.new)/'pairs.sqlite' if args.new.is_dir() else None)
        if pairs and args.sample>0:check.sample_pairs(pairs,[r for label,rows in new.items() if label.startswith('grid-rows-') or label=='rows' for r in rows],args.sample)
    except Exception as exc:check.require(False,f'{type(exc).__name__}: {exc}')
    print(('FAIL' if check.failures else 'PASS')+' '+json.dumps(dict(check.counts,failures=check.failures),ensure_ascii=False))
    for message in check.errors:print('  '+message)
    print('表3-1跨级有雨：检查 Σ(A+C)+GO=实况有雨、Σ(A+B)+GU=预报有雨；未加跨级项的等式不成立。')
    return int(bool(check.failures))


if __name__=='__main__':sys.exit(main())
