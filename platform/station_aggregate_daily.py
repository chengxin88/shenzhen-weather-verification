"""Disk-backed day states and rolling boundary fragments; one ordered raw scan.

The private cache is disposable. Public score/map schemas remain unchanged.
Only dirty UTC days and changed boundary intervals read pairs on a warm run.
"""
import ctypes
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import numpy as np
import station_aggregate as kernel
from station_common import (DAY_WINDOWS, GRID_DATASETS, GRID_MODELS, THRESHOLDS,
                            VARIABLES, XINJIANG, iso, stamp)

SCHEMA = 'daily-compensated-v8'   # v8：各窗口可有各自的截止时刻（metadata 多存 ends）
CHUNK = 131072
COLS = 'kind,part,station,model,var,lead,at'
KEYS = 'kind,part,model,lead,station,var,at'
TABLE = ('CREATE TABLE states (kind TEXT,part TEXT,station TEXT,model TEXT,var TEXT,'
         'lead INTEGER,at TEXT,value BLOB,PRIMARY KEY('+KEYS+')) WITHOUT ROWID')
RAW = ("SELECT station,model,var,lead,valid,fc,ob,trace,run "
       "FROM pairs INDEXED BY pair_valid")
ORDER = ' ORDER BY valid,model,run,lead,station,var'


def timing(name, started, count=0, days=0):
    print('TIMING', name, round(time.monotonic()-started,3), 'seconds', count,
          'rows', days, 'dirty_days', flush=True)


def extension(db, lib):
    db.enable_load_extension(True)
    try: db.load_extension(lib._name)
    finally: db.enable_load_extension(False)


def window_ends(end):
    """One end per window: a datetime (all windows equal) or a sequence by window index.

    2026-11-15 用户要求「近1天改成有资料的时间」：国家站资料晚到时近24小时单独截止到国家站最新时刻，
    其余窗口仍截止到本轮评分时刻，所以各窗口的右端可以不同。
    """
    if isinstance(end,(list,tuple)):
        if len(end)!=len(DAY_WINDOWS): raise ValueError('window end layout')
        return tuple(end)
    return (end,)*len(DAY_WINDOWS)


def bounds(ends):
    """Half-open ISO ranges exactly representing the legacy closed seconds."""
    result={}
    for w,(days,end) in enumerate(zip(DAY_WINDOWS,window_ends(ends))):
        stop=iso(end+timedelta(seconds=1))
        start=iso(end-timedelta(days=days));day=start[:10]
        nextday=iso(stamp(day+'T00:00:00Z')+timedelta(days=1))
        result[f'{w}:{day}']=(start,min(nextday,stop))
        if stop[:10]!=day:
            last=iso(end)[:10]
            result[f'{w}:{last}']=(last+'T00:00:00Z',stop)
    return result


def difference(a,b):
    """Intervals of a not in b (both half open)."""
    if b is None or b[1]<=a[0] or b[0]>=a[1]: return [a]
    return [(lo,hi) for lo,hi in ((a[0],min(a[1],b[0])),(max(a[0],b[1]),a[1])) if lo<hi]


class Daily:
    pooled=True
    def __init__(self, db, end, context):
        self.db=db;self.end=end;self.context=context;self.cache=None;self.current=None
        self.ends=window_ends(context.get('ends') or end)
        if sqlite3.sqlite_version_info<(3,42,0): raise kernel.Unavailable('daily states require compensated SQLite SUM (>=3.42)')
        self.lib=kernel.native();self.buffer=ctypes.create_string_buffer(1024)
        self.out=np.empty(kernel.COLUMNS,np.float64)
        self.dirty_days=[];self.daily_base=None;self.raw_rows=0;self.raw_days=set()
        try: self.update()
        except BaseException:
            self.close();raise

    def decode(self, value):
        status=self.lib.agg_decode_checked(value,len(value),kernel.COLUMNS,self.out.ctypes.data)
        if status<=0: raise sqlite3.DatabaseError('invalid or nonpositive aggregate state')
        values=self.out.tolist();values[0]=int(values[0]);values[9:]=map(int,values[9:])
        return values

    @staticmethod
    def edge_key(key,edges):
        interval=edges[key]
        return next(k for k,v in edges.items() if v==interval)

    def open(self):
        path=self.context['path'];state={}
        try:
            self.cache=sqlite3.connect(path)
            self.cache.execute('PRAGMA page_size=32768');self.cache.execute('PRAGMA cache_size=-32000');self.cache.execute('PRAGMA temp_store=FILE')
            self.cache.execute('PRAGMA temp.journal_mode=OFF')
            extension(self.cache,self.lib)
            self.cache.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT) WITHOUT ROWID')
            state=dict(self.cache.execute('SELECT key,value FROM metadata'))
            layout=[r[1] for r in self.cache.execute('PRAGMA table_info(states)')]
            if layout!=COLS.split(',')+['value']: state={}
        except sqlite3.DatabaseError as exc:
            print('AGGFAST_DAILY_REBUILD corrupt',str(exc),flush=True)
            if self.cache: self.cache.close()
            path.unlink(missing_ok=True);Path(str(path)+'-journal').unlink(missing_ok=True)
            self.cache=sqlite3.connect(path);extension(self.cache,self.lib)
            self.cache.execute('PRAGMA page_size=32768');self.cache.execute('PRAGMA cache_size=-32000');self.cache.execute('PRAGMA temp_store=FILE')
            self.cache.execute('PRAGMA temp.journal_mode=OFF')
            self.cache.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT) WITHOUT ROWID')
        if state:
            try:
                stamp(state['end'])
                ends=json.loads(state['ends'])
                if not isinstance(ends,list) or len(ends)!=len(DAY_WINDOWS): raise ValueError('window end layout')
                for value in ends: stamp(value)
                edges=json.loads(state['edges'])
                if not isinstance(edges,dict): raise ValueError('edge layout')
                for key,interval in edges.items():
                    if len(interval)!=2 or int(key.split(':')[0]) not in range(len(DAY_WINDOWS)): raise ValueError('edge layout')
                    stamp(interval[0]);stamp(interval[1])
            except (KeyError,ValueError,TypeError) as exc:
                print('AGGFAST_DAILY_REBUILD malformed metadata',str(exc),flush=True);state={}
        return state

    def update(self):
        started=time.monotonic();old=self.open();ctx=self.context
        reusable=(ctx['reusable'] and old.get('schema')==SCHEMA and old.get('signature')==ctx['signature']
                  and old.get('directory')==str(ctx['previous'].resolve()) and '*' not in ctx['dirty'])
        self.edges=bounds(self.ends);dirty=set(ctx['dirty']) if reusable else set()
        previous={k:tuple(v) for k,v in json.loads(old['edges']).items()} if reusable else {}
        old_full=self.full_days(tuple(stamp(v) for v in json.loads(old['ends']))) if reusable else {}
        new_full=self.full_days(self.ends)
        changed_edges={key for key in set(previous)|set(self.edges) if previous.get(key)!=self.edges.get(key) or key[-10:] in dirty}
        changed_days={w: (set(old_full.get(w,())) ^ set(new_full[w])) | dirty for w in range(len(DAY_WINDOWS))}
        if not reusable:
            # Disposable state never needs a second full copy in a rollback
            # journal. Published generations remain independent immutable files.
            self.cache.close();ctx['path'].unlink(missing_ok=True)
            Path(str(ctx['path'])+'-journal').unlink(missing_ok=True)
            self.cache=sqlite3.connect(ctx['path']);extension(self.cache,self.lib)
            self.cache.execute('PRAGMA page_size=32768');self.cache.execute('PRAGMA cache_size=-32000');self.cache.execute('PRAGMA temp_store=FILE')
            self.cache.execute('PRAGMA temp.journal_mode=OFF')
            self.cache.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT) WITHOUT ROWID')
        self.cache.execute('BEGIN IMMEDIATE')
        self.cache.execute("INSERT OR REPLACE INTO metadata VALUES ('schema','building')")
        self.checkpoint()
        if reusable:
            for w,days in changed_days.items():
                for day in sorted(days & set(old_full[w])): self.adjust(w,'day',day,-1)
            for key in sorted(changed_edges & set(previous)): self.adjust(int(key.split(':')[0]),'edge',self.edge_key(key,previous),-1)
        if not reusable:
            print('AGGFAST_DAILY_REBUILD missing/version/generation/unknown-dirty',flush=True)
            self.cache.execute('DROP TABLE IF EXISTS states');self.cache.execute(TABLE)
            self.scan(None,None,full=True)
            ctx['dirty']=sorted(self.raw_days)
        else:
            print('AGGFAST_DAILY_INCREMENTAL',len(dirty),'dirty UTC days',flush=True)
            # Remove entire contributions for changed days. BJT contributions
            # retain their UTC owner, so a neighbouring clean day is never read.
            for day in sorted(dirty):
                self.cache.execute("DELETE FROM states WHERE kind IN ('day','daily','space') AND part=?",(day,))
                self.checkpoint()
            for key in set(previous)|set(self.edges):
                if key not in self.edges or key[-10:] in dirty or key not in previous:
                    self.cache.execute("DELETE FROM states WHERE kind='edge' AND part=?",(key,))
                    self.checkpoint()
            for day in sorted(dirty):
                lo=day+'T00:00:00Z';hi=iso(stamp(lo)+timedelta(days=1))
                self.scan(lo,hi,full=True)
            # Coalesce boundary changes into disjoint raw intervals, even when
            # the four windows share the same right edge.
            changes=[]
            for key,interval in self.edges.items():
                if self.edge_key(key,self.edges)!=key: continue
                if key[-10:] in dirty: continue
                before=previous.get(key) if key in previous and self.edge_key(key,previous)==key else None
                midnight=key[-10:]+'T00:00:00Z'
                if before is None and interval==(midnight,iso(stamp(midnight)+timedelta(days=1))):
                    self.cache.execute("INSERT INTO states SELECT 'edge',?,station,model,var,lead,at,agg_detail(value) FROM states WHERE kind='day' AND part=?",(key,key[-10:]))
                    continue
                for lo,hi in difference(interval,before): changes.append((lo,hi,key,1))
                if before:
                    for lo,hi in difference(before,interval): changes.append((lo,hi,key,-1))
            points=sorted({v for lo,hi,_,_ in changes for v in (lo,hi)})
            for lo,hi in zip(points,points[1:]):
                active=[(key,sign) for a,b,key,sign in changes if a<=lo and hi<=b]
                if active: self.scan(lo,hi,edges=active)
        if reusable:
            for w,days in changed_days.items():
                for day in sorted(days & set(new_full[w])): self.adjust(w,'day',day,1)
            for key in sorted(changed_edges & set(self.edges)): self.adjust(int(key.split(':')[0]),'edge',self.edge_key(key,self.edges),1)
        else:
            for w in range(len(DAY_WINDOWS)):
                for day in new_full[w]: self.adjust(w,'day',day,1)
                for key in self.edges:
                    if key.startswith(str(w)+':'): self.adjust(w,'edge',self.edge_key(key,self.edges),1)
        # Zero rows disappear after subtraction; negative n signals bad state.
        if self.cache.execute("SELECT 1 FROM states WHERE kind IN ('edge','window') AND agg_count(value)<0 LIMIT 1").fetchone():
            raise sqlite3.DatabaseError('negative aggregate count')
        # Restrict zero cleanup to boundaries (the only subtractive states).
        for kind,part in [('window',str(w)) for w in range(len(DAY_WINDOWS))]+[('edge',k) for k in self.edges if self.edge_key(k,self.edges)==k]:
            self.cache.execute("DELETE FROM states WHERE kind=? AND part=? AND agg_count(value)=0",(kind,part))
            self.checkpoint()
        self.cache.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)',[
            ('schema',SCHEMA),('signature',ctx['signature']),('directory',ctx['target']),
            ('edges',json.dumps(self.edges)),('end',iso(self.end)),('ends',json.dumps([iso(e) for e in self.ends]))])
        self.cache.commit()
        # Persist the last published primitive values' digest separately from
        # unrounded compensated states. Recomputed days can have different low
        # compensation bits while producing identical public scores. This
        # avoids rebuilding millions of unchanged detail JSON rows every round.
        self.cache.execute('CREATE TABLE IF NOT EXISTS fingerprints (window INTEGER,station TEXT,model TEXT,var TEXT,lead INTEGER,hash BLOB,'
                           'PRIMARY KEY(window,station,model,var,lead)) WITHOUT ROWID')
        self.cache.commit()
        if reusable:
            self.daily_base=ctx['previous']/'grid-map.sqlite'
            self.dirty_days=sorted({d for day in dirty for d in (day,iso(stamp(day+'T00:00:00Z')+timedelta(days=1))[:10])})
        timing('aggregates.daily_update',started,self.raw_rows,len(dirty) if reusable else len(self.raw_days))

    @staticmethod
    def full_days(ends):
        result={}
        for w,(days,end) in enumerate(zip(DAY_WINDOWS,window_ends(ends))):
            first=stamp(iso(end-timedelta(days=days))[:10]+'T00:00:00Z')+timedelta(days=1)
            last=iso(end)[:10];parts=[]
            while iso(first)[:10]<last: parts.append(iso(first)[:10]);first+=timedelta(days=1)
            result[w]=parts
        return result

    def checkpoint(self):
        # A killed writer leaves schema=building, which is never reusable.
        self.cache.commit();self.cache.execute('BEGIN IMMEDIATE')

    def adjust(self,window,kind,part,sign):
        started=time.monotonic()
        self.cache.execute("INSERT INTO states SELECT 'window',?,station,model,var,lead,'',agg_scale(value,?) "
            "FROM (SELECT station,model,var,lead,agg_detail(value) AS value FROM states WHERE kind=? AND part=?) WHERE true ON CONFLICT("+KEYS+") DO UPDATE SET value=agg_merge(value,excluded.value,1)",
            (str(window),sign,kind,part))
        count=self.cache.execute('SELECT changes()').fetchone()[0]
        self.checkpoint()
        timing('aggregates.window_delta',started,count,len(self.context['dirty']))

    def scan(self,lo,hi,full=False,edges=None):
        started=time.monotonic();count=0;written=0;seen=set();groups=kernel.Groups(1)
        # UTC day keeps full grid moments. Only its late (16..24 UTC) BJT
        # fragment is stored separately; early = whole day - late. Rain window
        # edges omit continuous moments unused by station scores.
        def flush():
            nonlocal written
            batch=[]
            for key in sorted(groups.keys,key=lambda k:(k[0],k[1],k[3],k[5],k[2],k[4],k[6])):
                index=groups.keys[key]
                n=self.lib.agg_pack(groups.handle,index,0,int(key[0]=='edge'),self.buffer)
                batch.append((*key,self.buffer.raw[:n]))
                if len(batch)>=2000: write(batch);batch=[]
            write(batch)
            written+=len(groups.keys)
            self.checkpoint()
        def write(batch):
            self.cache.executemany('INSERT INTO states VALUES (?,?,?,?,?,?,?,?) ON CONFLICT('+KEYS+') '
                'DO UPDATE SET value=agg_merge(value,excluded.value,?)',
                ((*row, -1 if edges and next(s for k,s in edges if k==row[1])<0 else 1) for row in batch))
        query=RAW;args=()
        if not self.db.execute("SELECT 1 FROM main.sqlite_master WHERE name='pairs'").fetchone():
            query=query.replace('FROM pairs INDEXED','FROM original.pairs INDEXED')
        if lo is not None: query+=' WHERE valid>=? AND valid<?';args=(lo,hi)
        try:
            for rows in kernel.batches(self.db.execute(query+ORDER,args)):
                # One bounded accumulator per chunk; merges happen on disk.
                seen.update(r[4][:10] for r in rows)
                active_edges=[(key,a,b) for key,(a,b) in self.edges.items() if self.edge_key(key,self.edges)==key and a<rows[-1][4]+'~' and b>rows[0][4]] if full else []
                days={at:(at[:10],iso(stamp(at)+timedelta(hours=8))[:10]) for at in {r[4] for r in rows}}
                ids=np.full((len(rows),3+len(active_edges) if full else len(edges)),-1,np.int64)
                for i,(st,m,v,h,at,fc,ob,tr,run) in enumerate(rows):
                    day,bj=days[at];column=0
                    if full:
                        ids[i,0]=groups.index(('day',day,st,m,v,h,''));column=3
                        if v in GRID_DATASETS and m in GRID_MODELS:
                            if bj!=day: ids[i,1]=groups.index(('daily',day,st,m,v,h,bj))
                            if st not in XINJIANG: ids[i,2]=groups.index(('space',day,run,m,v,h,at))
                        for key,a,b in active_edges:
                            if a<=at<b: ids[i,column]=groups.index(('edge',key,st,m,v,h,''))
                            column+=1
                    else:
                        for column,(key,sign) in enumerate(edges):
                            ids[i,column]=groups.index(('edge',key,st,m,v,h,''))
                groups.add(ids,np.ones(len(rows),np.uint8),kernel.inputs(rows),rows);count+=len(rows)
                if len(groups.keys)>=CHUNK:
                    flush();groups.close();groups=kernel.Groups(1)
            flush()
        finally: groups.close()
        self.raw_rows+=count;self.raw_days.update(seen)
        timing('aggregates.raw_scan',started,count,len(seen))
        print('AGGFAST_SCAN_RANGE',lo,hi,'full_day' if full else 'boundary','states',written,flush=True)

    def prepare(self,window):
        if self.current==window: return
        started=time.monotonic()
        for name in ('win','pool_lead','pool_station'): self.cache.execute('DROP TABLE IF EXISTS temp.'+name)
        self.cache.execute('CREATE TEMP TABLE win (station TEXT,model TEXT,var TEXT,lead INTEGER,value BLOB,'
                           'PRIMARY KEY(station,model,var,lead)) WITHOUT ROWID')
        self.cache.execute("INSERT INTO win SELECT station,model,var,lead,value FROM states WHERE kind='window' AND part=? ORDER BY station,model,var,lead",(str(window),))
        self.cache.execute('CREATE TEMP TABLE pool_lead (model TEXT,var TEXT,lead INTEGER,value BLOB,PRIMARY KEY(model,var,lead)) WITHOUT ROWID')
        excluded=','.join('?' for _ in XINJIANG)
        self.cache.execute('INSERT INTO pool_lead SELECT model,var,lead,agg_total(value) FROM win WHERE station NOT IN ('+excluded+') GROUP BY model,var,lead',tuple(XINJIANG))
        self.cache.execute('CREATE TEMP TABLE pool_station (station TEXT,model TEXT,var TEXT,lo INTEGER,hi INTEGER,value BLOB,PRIMARY KEY(station,model,var,lo,hi)) WITHOUT ROWID')
        for lo,hi in kernel.RANGES:
            self.cache.execute('INSERT INTO pool_station SELECT station,model,var,?,?,agg_total(value) FROM win WHERE lead>=? AND lead<=? GROUP BY station,model,var',(lo,hi,lo,hi))
        self.current=window
        timing('aggregates.merge_window.'+str(DAY_WINDOWS[window]),started,self.cache.execute('SELECT COUNT(*) FROM win').fetchone()[0],len(self.context['dirty']))

    def rows(self,kind,window=0,variable=None,band=None):
        self.prepare(window)
        columns,table={'detail':('station,model,var,lead','win'),'lead':('model,var,lead','pool_lead'),
                       'station':('station,model,var,lo,hi','pool_station')}[kind]
        where=[];args=[]
        if variable is not None: where.append('var=?');args.append(variable)
        if band is not None: where.extend(('lo=?','hi=?'));args.extend(band)
        if kind=='detail':
            query=('SELECT w.station,w.model,w.var,w.lead,w.value,f.hash FROM win w LEFT JOIN fingerprints f '
                   'ON f.window=? AND f.station=w.station AND f.model=w.model AND f.var=w.var AND f.lead=w.lead '
                   'ORDER BY w.station,w.model,w.var,w.lead')
            pending=[];changed=0
            def flush():
                self.cache.executemany('INSERT INTO fingerprints VALUES (?,?,?,?,?,?) '
                    'ON CONFLICT DO UPDATE SET hash=excluded.hash',pending)
                pending.clear();self.cache.commit()
            for *key,value,previous in self.cache.execute(query,(window,)):
                values=self.decode(value)
                current=hashlib.sha256(self.out.tobytes()).digest()
                self.detail_changed=current!=previous
                if self.detail_changed:
                    changed+=1;pending.append((window,*key,current))
                yield (*key,*values)
                if len(pending)>=2000: flush()
            flush()
            # Remove vanished identities without retaining stale cache growth.
            self.cache.execute('DELETE FROM fingerprints AS f WHERE window=? AND NOT EXISTS '
                '(SELECT 1 FROM win w WHERE w.station=f.station AND w.model=f.model AND w.var=f.var AND w.lead=f.lead)',(window,))
            self.cache.commit()
            print('AGGFAST_DETAIL_CHANGED',DAY_WINDOWS[window],changed,'series',flush=True)
        else:
            query='SELECT '+columns+',value FROM '+table+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY '+columns
            for *key,value in self.cache.execute(query,args): yield (*key,*self.decode(value))

    def reuse_detail(self,previous,window):
        # The public detail rows were replaced from another identical window;
        # keep their fingerprints in the same generation, including emptiness.
        self.cache.execute('DELETE FROM fingerprints WHERE window=?',(window,))
        self.cache.commit()
        self.cache.execute('INSERT INTO fingerprints SELECT ?,station,model,var,lead,hash FROM fingerprints WHERE window=?',(window,previous))
        self.cache.commit()

    def score_rows(self,kind,window):
        import guifan_scores as guifan
        from station_score import pooled_score
        for variable in VARIABLES:
            for threshold in THRESHOLDS.get(variable,[None]):
                bands=kernel.RANGES if kind=='station' else (None,)
                for band in bands:
                    for *key,values in self.scored_inputs(kind,window,variable,band):
                        base=dict(zip(('model','var','lead') if kind=='lead' else ('station','model','var','lead_min','lead_max'),key))
                        yield dict(base,**pooled_score(values,variable in THRESHOLDS,threshold,variable))
        for *key,values in self.scored_inputs(kind,window,'rain24',None):
            base=dict(zip(('model','var','lead') if kind=='lead' else ('station','model','var','lead_min','lead_max'),key),n=int(values[0]))
            yield from (guifan.derived(r) for r in guifan.grade_rows(base,values[-len(guifan.COLUMNS):]))

    def scored_inputs(self,kind,window,variable,band):
        n=3 if kind=='lead' else 5
        for row in self.rows(kind,window,variable,band): yield (*row[:n],row[n:])

    def close(self):
        if self.cache is not None: self.cache.close();self.cache=None


class Grid:
    def __init__(self,daily):
        self.daily=daily;self.daily_base=daily.daily_base;self.dirty_days=daily.dirty_days
    def rows(self,kind):
        db=self.daily.cache
        if kind=='space':
            query="SELECT model,station,lead,at,var,agg_total(value) FROM states WHERE kind='space' GROUP BY model,station,lead,at,var ORDER BY model,station,lead,at,var"
            args=()
        else:
            owners=[]
            if self.daily_base is not None:
                if not self.dirty_days: return
                owners=sorted({p for day in self.dirty_days for p in (day,iso(stamp(day+'T00:00:00Z')-timedelta(days=1))[:10])})
            owner_clause=' AND part IN ('+','.join('?' for _ in owners)+')' if owners else ''
            query=("SELECT model,var,at,lead,station,agg_total(value) FROM ("
                "SELECT model,var,part AS at,lead,station,value FROM states WHERE kind='day' AND var IN ("+
                ','.join('?' for _ in GRID_DATASETS)+') AND model IN ('+','.join('?' for _ in GRID_MODELS)+')'+owner_clause+
                " UNION ALL SELECT model,var,part AS at,lead,station,agg_scale(value,-1) FROM states WHERE kind='daily'"+owner_clause+
                " UNION ALL SELECT model,var,at,lead,station,value FROM states WHERE kind='daily'"+owner_clause+
                ')')
            args=(*GRID_DATASETS,*GRID_MODELS,*owners,*owners,*owners)
            if self.daily_base is not None:
                query+=' WHERE at IN ('+','.join('?' for _ in self.dirty_days)+')';args+=tuple(self.dirty_days)
            query+=' GROUP BY model,var,at,lead,station HAVING agg_count(agg_total(value))!=0 ORDER BY model,var,at,lead,station'
        started=time.monotonic();count=0
        for *key,value in db.execute(query,args):
            count+=1;yield (*key,*self.daily.decode(value))
        timing('grid_aggregates.cached_'+kind,started,count,len(self.dirty_days))
    def close(self): pass
