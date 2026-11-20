"""Bounded native sums used by the disk-backed daily aggregation.

The daily path requires the local compiler; STATIONS_AGGFAST=0 explicitly uses
SQL. The older standalone helpers retain their own compatibility fallback.
No data, extension or dependency download.
"""
import ctypes
from datetime import timedelta
import hashlib
import inspect
import os
from pathlib import Path
import resource
import sqlite3
import subprocess
import tempfile
import time

import numpy as np
import guifan_scores as guifan
from station_common import (DAY_WINDOWS, GRID_DATASETS, GRID_MODELS, GRID_THRESHOLDS,
                            THRESHOLDS, XINJIANG, iso, stamp, legacy_exclusions)

RANGES = ((0,240),(0,24),(25,72),(73,168),(169,240))
COLUMNS = 9+3*len(GRID_THRESHOLDS)+len(guifan.COLUMNS)
BATCH = 8192
# Leave headroom for Python keys, SQLite caches and JSON output below 2 GiB RSS.
NATIVE_LIMIT = 800*1024**2
GROUP_LIMIT = 1400000
# Changing primitive scoring logic must use SQL until this kernel is reviewed.
SUPPORTED_SQL = 'ff26e5242c572af19c0e70058bbec44a798066b4539dca11a2fe12c0a1801599'


class Unavailable(RuntimeError): pass


def native():
    try: return _native()
    except (OSError,AttributeError) as exc: raise Unavailable(str(exc)) from exc


def _native():
    source=Path(__file__).with_name('station_aggregate_native.c')
    key=hashlib.sha256(source.read_bytes()+os.uname().machine.encode()).hexdigest()[:20]
    directory=Path(os.environ.get('STATIONS_AGG_NATIVE_CACHE',str(Path('/tmp')/('station-aggregate-'+str(os.getuid())))))
    directory.mkdir(mode=0o700,exist_ok=True)
    if directory.stat().st_uid!=os.getuid() or directory.stat().st_mode & 0o022:
        raise Unavailable('native cache directory is not private')
    library=directory/(key+'.so')
    if not library.is_file():
        fd,temporary=tempfile.mkstemp(suffix='.so',dir=directory);os.close(fd)
        try:
            subprocess.run(['cc','-O2','-std=c99','-fPIC','-shared','-fno-fast-math',
                            '-ffp-contract=off',str(source),'-o',temporary],
                           capture_output=True,check=True,timeout=60)
            os.replace(temporary,library)
        except (OSError,subprocess.SubprocessError) as exc:
            raise Unavailable('native compilation failed: '+str(exc)) from exc
        finally: Path(temporary).unlink(missing_ok=True)
    lib=ctypes.CDLL(str(library))
    if not hasattr(lib,'agg_abi') or lib.agg_abi()!=2: raise Unavailable('native ABI mismatch')
    lib.agg_new.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_size_t]
    lib.agg_new.restype=ctypes.c_void_p
    lib.agg_add.argtypes=[ctypes.c_void_p,ctypes.c_int64,ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
    lib.agg_get.argtypes=[ctypes.c_void_p,ctypes.c_int64,ctypes.c_int,ctypes.c_void_p]
    lib.agg_free.argtypes=[ctypes.c_void_p]
    lib.agg_pack.argtypes=[ctypes.c_void_p,ctypes.c_int64,ctypes.c_int,ctypes.c_int,ctypes.c_void_p]
    lib.agg_decode_checked.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_void_p]
    return lib


class Groups:
    def __init__(self,windows):
        expected=('N1','N2','T','GO','GU')+tuple(f'g{k}{c}' for k in range(1,7) for c in ('h','m','f'))
        try: profile=hashlib.sha256(inspect.getsource(guifan.sql_expressions).encode()).hexdigest()
        except (OSError,TypeError) as exc: raise Unavailable('unrecognized primitive scoring logic') from exc
        if profile!=SUPPORTED_SQL or set(THRESHOLDS)!={'rain6','rain12','rain24'} or guifan.COLUMNS!=expected or len(guifan.BOUNDS[24])!=6:
            raise Unavailable('unsupported counter layout')
        self.lib=native();self.keys={};self.windows=windows
        # SQLite 3.42 introduced compensated SUM. Older engines use plain sums.
        self.handle=self.lib.agg_new(COLUMNS,windows,int(sqlite3.sqlite_version_info>=(3,42,0)),NATIVE_LIMIT)
        if not self.handle: raise Unavailable('native allocation failed')

    def close(self):
        if self.handle: self.lib.agg_free(self.handle);self.handle=None
        self.keys.clear()

    def index(self,key):
        found=self.keys.get(key)
        if found is None:
            found=len(self.keys)
            if found>=GROUP_LIMIT: raise Unavailable('group memory limit reached')
            self.keys[key]=found
        return found

    def add(self,ids,masks,values,rows):
        try: rss=int(Path('/proc/self/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
        except (OSError,ValueError,IndexError): rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
        if rss>1450*1024**2:
            raise Unavailable('RSS safety limit reached')
        ids=np.ascontiguousarray(ids,dtype=np.int64)
        types=np.fromiter((0 if r[2]=='t' else 3 if r[2]=='rain24' else 2 if r[2] in THRESHOLDS else 1 for r in rows),np.uint8,count=len(rows))
        if self.lib.agg_add(self.handle,len(values),ids.shape[1],ids.ctypes.data,masks.ctypes.data,values.ctypes.data,types.ctypes.data):
            raise Unavailable('native memory limit reached')

    def rows(self,kind,window=0):
        values=np.empty(COLUMNS,dtype=np.float64)
        for key in sorted(k for k in self.keys if k[0]==kind):
            if self.lib.agg_get(self.handle,self.keys[key],window,values.ctypes.data):
                # Preserve count types in JSON and SQLite products.
                sums=values.tolist();sums[0]=int(sums[0]);sums[9:]=map(int,sums[9:])
                yield (*key[1:],*sums)


def inputs(rows):
    """Evaluate all primitive moments/counters once for the entire batch."""
    try:
        fc=np.fromiter((r[5] for r in rows),float,count=len(rows))
        ob=np.fromiter((r[6] for r in rows),float,count=len(rows))
    except (TypeError,ValueError) as exc: raise Unavailable('non-numeric pair: '+str(exc)) from exc
    if not (np.isfinite(fc).all() and np.isfinite(ob).all()): raise Unavailable('non-finite pair')
    var=np.array([r[2] for r in rows]);rain=np.isin(var,list(THRESHOLDS))
    values=np.zeros((len(rows),COLUMNS),dtype=np.float64)
    error=fc-ob
    values[:,:9]=np.column_stack((np.ones(len(rows)),error,np.abs(error),error*error,fc,ob,fc*fc,ob*ob,fc*ob))
    for i,threshold in enumerate(GRID_THRESHOLDS):
        f,o=fc>=threshold,ob>=threshold
        values[:,9+3*i:12+3*i]=np.column_stack((rain&f&o,rain&~f&o,rain&f&~o))
    offset=9+3*len(GRID_THRESHOLDS)
    gf=np.searchsorted(guifan.BOUNDS[24],fc,side='right')
    go=np.searchsorted(guifan.BOUNDS[24],ob,side='right');r24=var=='rain24'
    trace=np.fromiter((r[7]==1 for r in rows),bool,count=len(rows))
    values[:,offset:offset+5]=np.column_stack(((var=='t')&(np.abs(error)<=1.000001),
        (var=='t')&(np.abs(error)<=2.000001),rain&(fc>=.1)&(ob<.1)&trace,
        r24&(gf>go)&(go>=1),r24&(go>gf)&(gf>=1)))
    for k in range(1,7):
        values[:,offset+5+3*(k-1):offset+8+3*(k-1)]=np.column_stack((
            r24&(gf==k)&(go==k),r24&(go==k)&(gf<go),r24&(gf==k)&(gf>go)))
    return values


def batches(cursor):
    while True:
        rows=cursor.fetchmany(BATCH)
        if not rows: return
        yield rows


def windows(db,end,variable=None):
    excluded=legacy_exclusions(db)
    groups=Groups(len(DAY_WINDOWS));started=time.monotonic();count=0
    try:
        # Explicit order matches the original covering pair_valid index, including
        # tie-breaks. SQLite's stable GROUP BY sorter retains this order per key.
        query="SELECT station,model,var,lead,valid,fc,ob,CASE WHEN var IN ('rain6','rain12','rain24') AND fc>=0.1 AND ob<0.1 THEN trace ELSE 0 END,run FROM pairs INDEXED BY pair_valid WHERE valid>=? AND valid<=?"
        args=(iso(end-timedelta(days=max(DAY_WINDOWS))),iso(end))
        if variable is not None: query+=' AND var=?';args+=(variable,)
        query+=' ORDER BY valid,model,run,lead,station,var'
        cutoffs=[iso(end-timedelta(days=d)) for d in DAY_WINDOWS]
        for rows in batches(db.execute(query,args)):
            ids=np.full((len(rows),4),-1,dtype=np.int64)
            masks=np.zeros(len(rows),dtype=np.uint8)
            for i,(st,m,v,h,at,*_) in enumerate(rows):
                ids[i,0]=groups.index(('detail',st,m,v,h))
                if v not in THRESHOLDS:
                    if st not in excluded: ids[i,1]=groups.index(('lead',m,v,h))
                    if 0<=h<=240:
                        ids[i,2]=groups.index(('station',st,m,v,0,240))
                        lo,hi=next((lo,hi) for lo,hi in RANGES[1:] if lo<=h<=hi)
                        ids[i,3]=groups.index(('station',st,m,v,lo,hi))
                masks[i]=sum(1<<w for w,c in enumerate(cutoffs) if at>=c)
            groups.add(ids,masks,inputs(rows),rows);count+=len(rows)
        print('TIMING aggregates.scan',round(time.monotonic()-started,3),'seconds',count,'pairs',len(groups.keys),'groups',flush=True)
        return groups
    except BaseException:
        groups.close();raise


def typed_rows(cursor,keys):
    for row in cursor:
        values=list(row[keys:]);values[0]=int(values[0]);values[9:]=map(int,values[9:])
        yield (*row[:keys],*values)


class WindowRows:
    def __init__(self,db): self.db=db

    def rows(self,kind,window=0):
        keys=dict(detail='station,model,var,lead',lead='model,var,lead',station='station,model,var,lo,hi')[kind]
        return typed_rows(self.db.execute('SELECT '+keys+','+','.join('s'+str(i) for i in range(COLUMNS))+
            ' FROM agg_window_'+kind+' WHERE window=? ORDER BY '+keys,(window,)),len(keys.split(',')))

    def close(self):
        for kind in ('detail','lead','station'): self.db.execute('DROP TABLE IF EXISTS temp.agg_window_'+kind)


def window_rows(db,end):
    try: return windows(db,end)
    except Unavailable as exc:
        print('AGGFAST_PARTITION',str(exc),flush=True)
    # A very large station/lead population is rescanned one variable at a time.
    # Each filter uses the covering valid index; no raw GROUP BY spill is needed.
    result=WindowRows(db)
    try:
        for kind,keys in (('detail','station TEXT,model TEXT,var TEXT,lead INTEGER'),
                          ('lead','model TEXT,var TEXT,lead INTEGER'),
                          ('station','station TEXT,model TEXT,var TEXT,lo INTEGER,hi INTEGER')):
            db.execute('CREATE TEMP TABLE agg_window_'+kind+' (window INTEGER,'+keys+','+','.join('s'+str(i)+' REAL' for i in range(COLUMNS))+')')
        variables=[r[0] for r in db.execute('SELECT DISTINCT var FROM pairs')]
        for variable in variables:
            groups=windows(db,end,variable)
            try:
                for kind,keys in (('detail',4),('lead',3),('station',5)):
                    sql='INSERT INTO agg_window_'+kind+' VALUES ('+','.join('?' for _ in range(1+keys+COLUMNS))+')'
                    for w in range(len(DAY_WINDOWS)):
                        db.executemany(sql,((w,*row) for row in groups.rows(kind,w)))
            finally: groups.close()
        return result
    except BaseException:
        result.close();raise


class GridRows:
    def __init__(self,db):
        self.db=db;self.space_db=None;self.daily_base=None;self.dirty_days=[]

    def rows(self,kind):
        keys='model,run,lead,valid,var' if kind=='space' else 'model,var,day,lead,station'
        connection=self.space_db if kind=='space' and self.space_db is not None else self.db
        table='space' if connection is self.space_db else 'agg_grid_'+kind
        return typed_rows(connection.execute('SELECT * FROM '+table+' ORDER BY '+keys),5)

    def close(self):
        for kind in ('space','daily'): self.db.execute('DROP TABLE IF EXISTS temp.agg_grid_'+kind)
        if self.space_db is not None: self.space_db.close();self.space_db=None


def grid(db,context=None):
    excluded=legacy_exclusions(db)
    groups=Groups(1);started=time.monotonic();count=0;previous=None
    result=GridRows(db)
    cache=None;expected_valid={}
    try:
        incremental=False
        if context is not None:
            try:
                cache=sqlite3.connect(context['path'])
                cache.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT) WITHOUT ROWID')
                state=dict(cache.execute('SELECT key,value FROM metadata'))
            except sqlite3.DatabaseError as exc:
                if cache is not None: cache.close()
                print('AGGFAST_GRID_CACHE_REBUILD',str(exc),flush=True)
                context['path'].unlink(missing_ok=True)
                Path(str(context['path'])+'-journal').unlink(missing_ok=True)
                cache=sqlite3.connect(context['path']);state={}
                cache.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT) WITHOUT ROWID')
            cache.execute('PRAGMA cache_size=-16000');cache.execute('PRAGMA temp_store=FILE')
            layout=[r[1] for r in cache.execute('PRAGMA table_info(space)')]
            expected=['model','run','lead','valid','var']+['s'+str(i) for i in range(COLUMNS)]
            incremental=context['reusable'] and layout==expected and state==dict(signature=context['signature'],directory=str(context['previous'].resolve()))
            cache.execute('BEGIN IMMEDIATE')
            if not incremental: cache.execute('DROP TABLE IF EXISTS space')
            cache.execute('CREATE TABLE IF NOT EXISTS space (model TEXT,run TEXT,lead INTEGER,valid TEXT,var TEXT,'+
                ','.join('s'+str(i)+' REAL' for i in range(COLUMNS))+',PRIMARY KEY(model,run,lead,valid,var)) WITHOUT ROWID')
            cache.execute('CREATE INDEX IF NOT EXISTS space_valid ON space(valid)')
            if incremental:
                days=set()
                for day in context['dirty']:
                    at=stamp(day+'T00:00:00Z')
                    days.update((day,iso(at+timedelta(days=1))[:10]))
                result.dirty_days=sorted(days)
                result.daily_base=context['previous']/'grid-map.sqlite'
                for day in result.dirty_days:
                    start=iso(stamp(day+'T00:00:00Z')-timedelta(hours=8));stop=iso(stamp(start)+timedelta(days=1))
                    cache.execute('DELETE FROM space WHERE valid>=? AND valid<?',(start,stop))
        for kind,keys in (('space','model TEXT,run TEXT,lead INTEGER,valid TEXT,var TEXT'),
                          ('daily','model TEXT,var TEXT,day TEXT,lead INTEGER,station TEXT')):
            db.execute('CREATE TEMP TABLE agg_grid_'+kind+' ('+keys+','+','.join('s'+str(i)+' REAL' for i in range(COLUMNS))+')')
        def flush():
            for kind in ('space','daily'):
                connection=cache if kind=='space' and cache is not None else db
                table='space' if connection is cache else 'agg_grid_'+kind
                connection.executemany('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in range(5+COLUMNS))+')',groups.rows(kind))
        # Within a spatial group valid/run/lead are fixed. Within a daily group
        # lead is fixed, so valid and run have the same order. A valid-index scan
        # therefore preserves the old primary-key SUM order in both groupings.
        # Flush each BJT day: memory is independent of the history's day count.
        query="SELECT station,model,var,lead,valid,fc,ob,CASE WHEN var IN ('rain6','rain12','rain24') AND fc>=0.1 AND ob<0.1 THEN trace ELSE 0 END,run FROM pairs INDEXED BY pair_valid WHERE var IN ("+','.join('?' for _ in GRID_DATASETS)+') AND model IN ('+','.join('?' for _ in GRID_MODELS)+') AND fc IS NOT NULL AND ob IS NOT NULL AND ABS(fc)<1e308 AND ABS(ob)<1e308'
        def source_rows():
            if incremental:
                for day in result.dirty_days:
                    start=iso(stamp(day+'T00:00:00Z')-timedelta(hours=8));stop=iso(stamp(start)+timedelta(days=1))
                    yield from batches(db.execute(query+' AND valid>=? AND valid<? ORDER BY valid,model,run,lead,station,var',(*GRID_DATASETS,*GRID_MODELS,start,stop)))
            else: yield from batches(db.execute(query+' ORDER BY valid,model,run,lead,station,var',(*GRID_DATASETS,*GRID_MODELS)))
        for rows in source_rows():
            # Daily raw order agrees with the old indexes only when the normal
            # pair contract valid=run+lead holds. Old malformed rows use SQL.
            for run,lead,at in {(r[8],r[3],r[4]) for r in rows}:
                key=(run,lead)
                if key not in expected_valid: expected_valid[key]=iso(stamp(run)+timedelta(hours=lead))
                if expected_valid[key]!=at: raise Unavailable('inconsistent run/lead/valid')
            days={at:iso(stamp(at)+timedelta(hours=8))[:10] for at in {r[4] for r in rows}}
            start=0
            while start<len(rows):
                day=days[rows[start][4]];stop=start+1
                while stop<len(rows) and days[rows[stop][4]]==day: stop+=1
                if previous is not None and previous!=day:
                    flush();groups.close();groups=Groups(1)
                previous=day;part=rows[start:stop]
                ids=np.full((len(part),2),-1,dtype=np.int64)
                for i,(st,m,v,h,at,fc,ob,tr,run) in enumerate(part):
                    if st not in excluded: ids[i,0]=groups.index(('space',m,run,h,at,v))
                    ids[i,1]=groups.index(('daily',m,v,day,h,st))
                groups.add(ids,np.ones(len(part),dtype=np.uint8),inputs(part),part);count+=len(part);start=stop
        flush();groups.close()
        if cache is not None:
            cache.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)',
                [('signature',context['signature']),('directory',context['target'])])
            cache.commit();cache.close();cache=None
            result.space_db=sqlite3.connect(context['path'].resolve().as_uri()+'?mode=ro',uri=True)
            result.space_db.execute('PRAGMA cache_size=-16000')
        print('AGGFAST_GRID', 'incremental' if incremental else 'full',len(result.dirty_days),'dirty BJT days',flush=True)
        print('TIMING grid_aggregates.scan',round(time.monotonic()-started,3),'seconds',count,'pairs',flush=True)
        return result
    except BaseException:
        if cache is not None: cache.rollback();cache.close()
        groups.close();result.close();raise
