#!/usr/bin/env python3
"""Offline, fixed-end timing harness. Source can be an instrumented main copy."""
import argparse
from collections import defaultdict
import gzip
import json
from pathlib import Path
import pickle
import sys
import time

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1])
parser.add_argument('--end',required=True)
parser.add_argument('--record',type=Path)
parser.add_argument('--record-scored',type=Path,
                    help='Freeze the certified station arrays after each scored run for an independent full pairing replay')
parser.add_argument('--replay',type=Path)
parser.add_argument('--force-full',action='store_true')
parser.add_argument('--skip-forecast',action='store_true',help='Republish an already scored private working DB')
parser.add_argument('--stop-before-publish',action='store_true',help='Measure pairing only; preserve the existing current snapshot')
args=parser.parse_args()
if args.replay:
    # A producer can freeze a complete input set before exposing it to replay.
    while (args.replay/'.incomplete').exists():
        print('Waiting for complete frozen station inputs:',args.replay,flush=True)
        time.sleep(10)
sys.path.insert(0,str(args.source.resolve()))
import refresh_stations
import station_score as score
import station_forecasts as forecasts
from station_common import cache_root, stamp, iso
end=stamp(args.end)
refresh_stations.now=lambda:end
elapsed=defaultdict(float)
class ScoringMeasured(Exception):
    pass

def timed(name,fn):
    def call(*a,**kw):
        started=time.monotonic()
        try:return fn(*a,**kw)
        finally:
            seconds=time.monotonic()-started
            elapsed[name]+=seconds
            print('TIMING',name,round(seconds,3),flush=True)
    return call

for name in ('load_observations','aggregates','grid_aggregates','common_report','shenzhen_sanity','export_days'):
    setattr(score,name,timed(name,getattr(score,name)))
forecasts.discover=timed('discover',forecasts.discover)
original=score.forecast_runs
if args.record:args.record.mkdir(parents=True,exist_ok=True)
if args.record_scored:args.record_scored.mkdir(parents=True,exist_ok=True)
def record(directory,item,scored=False):
    model,run,samples,signature,issues=item
    if hasattr(samples,'load'):
        if scored:
            path=cache_root()/'forecast'/(model+'_'+run.strftime('%Y%m%d%H')+'.npz')
            samples=forecasts.load_samples(path,set(samples)) if path.is_file() else {}
        else:
            samples=samples.load(set(samples))
    name=model+'_'+(iso(run).replace(':','') if run else str(len(list(directory.iterdir()))))
    with gzip.open(directory/(name+'.pkl.gz'),'wb') as handle:
        pickle.dump((model,run,samples,signature,issues),handle)
def runs(*a):
    if args.skip_forecast:return
    if args.replay:
        for p in sorted(args.replay.glob('*.pkl.gz')):
            with gzip.open(p,'rb') as f:item=pickle.load(f)
            before=time.monotonic()
            yield item
            elapsed['score.'+item[0]]+=time.monotonic()-before
        if args.stop_before_publish:raise ScoringMeasured('pairing measured; no publication requested')
        return
    iterator=iter(original(*a))
    while True:
        started=time.monotonic()
        try:item=next(iterator)
        except StopIteration:break
        elapsed['forecast.'+item[0]]+=time.monotonic()-started
        if args.record:record(args.record,item)
        before=time.monotonic()
        yield item
        elapsed['score.'+item[0]]+=time.monotonic()-before
        if args.record_scored:record(args.record_scored,item,scored=True)
    if args.stop_before_publish:raise ScoringMeasured('pairing measured; no publication requested')
score.forecast_runs=runs
original_pair=score.pair_run
def pairs(*a,**kw):
    started=time.monotonic()
    try:yield from original_pair(*a,**kw)
    finally:elapsed['pair.'+a[0]]+=time.monotonic()-started
score.pair_run=pairs
if args.force_full:
    import sqlite3
    with sqlite3.connect(cache_root()/'working.sqlite') as db:
        for table in ('jobs','job_state','job_valid'):
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                db.execute('DELETE FROM '+table)
sys.argv=['refresh_stations.py','--score-only']
try:refresh_stations.main()
except ScoringMeasured:print('SCORE_PROFILE_COMPLETE',flush=True)
finally:print('TIMING_TOTALS',json.dumps(elapsed,sort_keys=True),flush=True)
