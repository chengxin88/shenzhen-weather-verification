#!/usr/bin/env python3
"""Offline warm-run/scaling or >1.4M-group validation on an owned synthetic DB.

MUTATES --database (never use the live or frozen input). Outputs are private.
--state-only verifies 30d detail primitive scores against SQL without expanding
millions of public threshold/detail JSON rows, keeping WSL scratch bounded.
"""
import argparse
from contextlib import closing
from datetime import timedelta
import json
import math
from pathlib import Path
import resource
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import station_aggregate_daily as daily
import station_aggregate as kernel
import station_score as score
import guifan_scores as guifan
from station_common import iso,stamp


def plans(db,end):
    lo=iso(end-timedelta(days=1));hi=iso(end+timedelta(seconds=1))
    queries={
        'window':(daily.RAW+' WHERE valid>=? AND valid<?'+daily.ORDER,(lo,hi)),
        'grid':(daily.RAW+" WHERE var IN ('t','rain24','msl') AND model IN ('ec01','gfs') AND valid>=? AND valid<?"+daily.ORDER,(lo,hi)),
        'export':('SELECT * FROM pairs INDEXED BY pair_valid WHERE valid>=? AND valid<? ORDER BY model,run,lead,station,var',(lo,hi)),
        'rolloff_before':('SELECT DISTINCT valid FROM pairs INDEXED BY pair_valid WHERE valid<?',(lo,)),
        'rolloff_after':('SELECT DISTINCT valid FROM pairs INDEXED BY pair_valid WHERE valid>?',(hi,)),
    }
    result={}
    for analyzed in (False,True):
        if analyzed: db.execute('ANALYZE');db.commit()
        phase={}
        for name,(query,args) in queries.items():
            plan=[row[3] for row in db.execute('EXPLAIN QUERY PLAN '+query,args)]
            assert any('INDEX pair_valid' in row and 'valid' in row for row in plan),plan
            assert not any('PRIMARY KEY' in row for row in plan),plan
            if name in ('window','grid'):
                assert any('COVERING INDEX pair_valid' in row for row in plan),plan
                assert not any('TEMP B-TREE' in row for row in plan),plan
            phase[name]=plan
        result['analyzed' if analyzed else 'not_analyzed']=phase
    return result


def verify(db,cache,end):
    start=time.monotonic();expressions=['COUNT(*)','SUM(fc-ob)','SUM(ABS(fc-ob))','SUM((fc-ob)*(fc-ob))',
        'SUM(fc)','SUM(ob)','SUM(fc*fc)','SUM(ob*ob)','SUM(fc*ob)']
    for threshold in kernel.GRID_THRESHOLDS:
        for condition in (f'fc>={threshold} AND ob>={threshold}',f'fc<{threshold} AND ob>={threshold}',f'fc>={threshold} AND ob<{threshold}'):
            expressions.append(f"SUM(var IN ('rain6','rain12','rain24') AND {condition})")
    expressions.extend(guifan.sql_expressions())
    query='SELECT station,model,var,lead,'+','.join(expressions)+' FROM pairs INDEXED BY pair_valid WHERE valid>=? AND valid<=? GROUP BY station,model,var,lead'
    expected=db.execute(query,(iso(end-timedelta(days=30)),iso(end)))
    from itertools import zip_longest
    count=0;different=0;errors=[];maxerr=0.
    for a,b in zip_longest(expected,cache.rows('detail',3)):
        count+=1
        if a is None or b is None or a[:4]!=b[:4]: raise AssertionError(('key',a,b))
        va,vb=a[4:],b[4:];var=a[2]
        # Rain day states intentionally omit unused continuous moments.
        columns=(0,*range(9,len(va))) if var in score.THRESHOLDS else range(len(va))
        for i in columns:
            if i==0 or i>=9:
                if va[i]!=vb[i]: raise AssertionError(('counter',a[:4],i,va[i],vb[i]))
            elif va[i]!=vb[i]:
                maxerr=max(maxerr,abs(va[i]-vb[i])/max(abs(va[i]),abs(vb[i]),1e-300))
                if not math.isclose(va[i],vb[i],rel_tol=1e-9,abs_tol=0.): raise AssertionError(('moment',a[:4],i,va[i],vb[i]))
        if var not in score.THRESHOLDS:
            left=score.pooled_score(va,variable=var);right=score.pooled_score(vb,variable=var)
            for k,v in left.items():
                w=right[k]
                if v==w: continue
                different+=1
                if v is None or w is None or not math.isclose(v,w,rel_tol=1e-9,abs_tol=0.):
                    if len(errors)<20: errors.append((a[:4],k,v,w))
    assert not errors,errors
    return dict(rows=count,different_floats=different,max_relative_moment_error=maxerr,seconds=time.monotonic()-start)


def verify_grid(db,cache):
    """Stream every grid primitive and derived CC against the legacy SQL."""
    from itertools import zip_longest
    expressions=['COUNT(*)','SUM(fc-ob)','SUM(ABS(fc-ob))','SUM((fc-ob)*(fc-ob))',
                 'SUM(fc)','SUM(ob)','SUM(fc*fc)','SUM(ob*ob)','SUM(fc*ob)']
    for threshold in kernel.GRID_THRESHOLDS:
        for condition in (f'fc>={threshold} AND ob>={threshold}',f'fc<{threshold} AND ob>={threshold}',f'fc>={threshold} AND ob<{threshold}'):
            expressions.append(f"SUM(var IN ('rain6','rain12','rain24') AND {condition})")
    expressions.extend(guifan.sql_expressions())
    where='var IN ('+','.join('?' for _ in score.GRID_DATASETS)+') AND model IN ('+','.join('?' for _ in score.GRID_MODELS)+')'
    args=(*score.GRID_DATASETS,*score.GRID_MODELS);result={}
    for kind,keys,extra in (('space','model,run,lead,valid,var',' AND '+score.SOUTH_ONLY),
                            ('daily',"model,var,date(valid,'+8 hours'),lead,station",'')):
        started=time.monotonic();count=0;different=0;maximum=0.
        expected=db.execute('SELECT '+keys+','+','.join(expressions)+' FROM pairs WHERE '+where+extra+' GROUP BY '+keys+' ORDER BY '+keys,args)
        for left,right in zip_longest(expected,daily.Grid(cache).rows(kind)):
            assert left is not None and right is not None and left[:5]==right[:5],(kind,left,right)
            count+=1
            for i,(a,b) in enumerate(zip(left[5:],right[5:])):
                if i==0 or i>=9: assert a==b,(kind,left[:5],i,a,b)
                elif a!=b:
                    different+=1;maximum=max(maximum,abs(a-b)/max(abs(a),abs(b),1e-300))
                    assert math.isclose(a,b,rel_tol=1e-9,abs_tol=0.),(kind,left[:5],i,a,b)
            for name,a in score.moment_score(left[5:14]).items():
                b=score.moment_score(right[5:14])[name]
                assert a==b or (a is not None and b is not None and math.isclose(a,b,rel_tol=1e-9,abs_tol=0.)),(kind,left[:5],name,a,b)
        result[kind]=dict(rows=count,different_moments=different,max_relative_moment_error=maximum,seconds=time.monotonic()-started)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--state-only',action='store_true');p.add_argument('--skip-plans',action='store_true')
    p.add_argument('--verify-grid',action='store_true',help='With --state-only, also compare every spatial/BJT daily primitive and derived continuous score to legacy SQL')
    p.add_argument('--rounds',type=int,default=2)
    p.add_argument('--dirty-days',type=int,default=1,help='Revise one pair on each of N latest UTC days (owned DB only)')
    p.add_argument('--revision-mode',choices=('one','all'),default='one',help='one: isolate dirty-day read cost; all: rewrite every pair on those days, toggling rain observations wet/dry to stress changed public outputs (owned DB only)')
    p.add_argument('--end',help='Optional scoring endpoint, independent of the newest pair')
    p.add_argument('--warm-wait-for',type=Path,help='Start measured warm rounds only after another offline comparison has finished')
    a=p.parse_args()
    if not 1<=a.dirty_days<=32: p.error('--dirty-days must be 1..32')
    a.output.mkdir(parents=True,exist_ok=False)
    db=score.database(a.database);latest=db.execute('SELECT MAX(valid) FROM pairs').fetchone()[0]
    end=stamp(a.end or latest)
    report=dict(database=str(a.database),rows=db.execute('SELECT COUNT(*) FROM pairs').fetchone()[0])
    if not a.skip_plans: report['plans']=plans(db,end)
    if a.state_only:
        context=dict(path=a.output/'aggregate-days.sqlite',signature='scale-validation',target=str(a.output.resolve()),
                     previous=Path('/nonexistent'),reusable=False,dirty=['*'])
        started=time.monotonic();cache=daily.Daily(db,end,context)
        report['build_seconds']=time.monotonic()-started
        report['verification']=verify(db,cache,end)
        if a.verify_grid: report['grid_verification']=verify_grid(db,cache)
        report['cache_bytes']=context['path'].stat().st_size
        cache.close()
    else:
        revisions=[]
        for age in range(a.dirty_days):
            lo=iso(stamp(latest[:10]+'T00:00:00Z')-timedelta(days=age));hi=iso(stamp(lo)+timedelta(days=1))
            row=db.execute('SELECT model,run,lead,station,var FROM pairs INDEXED BY pair_valid WHERE valid>=? AND valid<? LIMIT 1',(lo,hi)).fetchone()
            if row: revisions.append(row)
        report['requested_dirty_days']=a.dirty_days;report['revision_days']=len(revisions);report['revision_mode']=a.revision_mode
        runs=[]
        for round in range(a.rounds+1):
            directory=a.output/str(round);directory.mkdir()
            rewrite_seconds=0.;rewritten=0
            if round:
                if a.warm_wait_for:
                    while not a.warm_wait_for.is_file(): time.sleep(30)
                # Same density dirty day independent of retained history size.
                revision_started=time.monotonic()
                if a.revision_mode=='one':
                    with db: db.executemany('UPDATE pairs SET fc=fc+.0001 WHERE model=? AND run=? AND lead=? AND station=? AND var=?',revisions)
                    rewritten=len(revisions)
                else:
                    # Deliberately artificial upper workload: make every rain
                    # contingency change, rather than timing mostly-identical
                    # JSON after changing only one pair. Commit each day so the
                    # input rewrite's journal stays bounded too.
                    for age in range(a.dirty_days):
                        lo=iso(stamp(latest[:10]+'T00:00:00Z')-timedelta(days=age));hi=iso(stamp(lo)+timedelta(days=1))
                        with db:
                            rewritten+=db.execute("UPDATE pairs INDEXED BY pair_valid SET fc=fc+.0001,ob=CASE WHEN var IN ('rain6','rain12','rain24') THEN ? ELSE ob+.0002 END WHERE valid>=? AND valid<?",(1000. if round%2 else 0.,lo,hi)).rowcount
                rewrite_seconds=time.monotonic()-revision_started
                print('TIMING benchmark.rewrite_dirty_days',f'{rewrite_seconds:.3f}','seconds',rewritten,'rows',a.dirty_days,'dirty_days',flush=True)
                end+=timedelta(hours=3)
            started=time.monotonic();scans=[];stages=[]
            original_timing=daily.timing
            def timed(name,started,count=0,days=0):
                stages.append(dict(stage=name,seconds=time.monotonic()-started,rows=count,dirty_days=days))
                original_timing(name,started,count,days)
            daily.timing=timed
            def trace(q):
                if q.startswith(daily.RAW): scans.append(q)
            db.set_trace_callback(trace);score.aggregates(db,directory,end);db.set_trace_callback(None)
            daily.timing=original_timing
            runs.append(dict(round=round,seconds=time.monotonic()-started,raw_scans=scans,stages=stages,end=iso(end),rewritten_pairs=rewritten,rewrite_seconds=rewrite_seconds))
            print('BENCHMARK_ROUND',json.dumps(runs[-1]),flush=True)
        report['runs']=runs
        cache=a.database.parent/'aggregate-days.sqlite'
        report['cache_bytes']=cache.stat().st_size
    with closing(sqlite3.connect((a.output/'aggregate-days.sqlite') if a.state_only else (a.database.parent/'aggregate-days.sqlite'))) as cache:
        report['cache_layout']=cache.execute('SELECT kind,COUNT(*),SUM(length(value)) FROM states GROUP BY kind').fetchall()
        report['cache_fingerprints']=cache.execute('SELECT COUNT(*),SUM(length(hash)) FROM fingerprints').fetchone()
    report['rss_mib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    report['input_bytes']=a.database.stat().st_size
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='aggregate_detail_keys'").fetchone():
        report['detail_key_rows']=db.execute('SELECT COUNT(*) FROM aggregate_detail_keys').fetchone()[0]
        try: report['detail_key_bytes']=db.execute("SELECT SUM(pgsize) FROM dbstat WHERE name='aggregate_detail_keys'").fetchone()[0]
        except sqlite3.DatabaseError: report['detail_key_bytes']=None
    (a.output/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
    db.close()


if __name__=='__main__': main()
