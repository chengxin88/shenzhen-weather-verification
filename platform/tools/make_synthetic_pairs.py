#!/usr/bin/env python3
"""Deterministically expand real sample (model,var,lead,fc,ob,trace) series.

Creates the production WITHOUT ROWID table and indexes, never copies caches.
Example: --stations 428 --days 32 --rows 200000000 --output /scratch/pairs.sqlite
--series 700 --stations 2100 --days 2 --hours 12 exercises >1.4M detail groups.
"""
import argparse
import csv
from datetime import timedelta
import gzip
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from station_common import iso,stamp
from station_score import database


def templates(samples,limit):
    series={}
    for path in sorted(samples.glob('*.csv.gz')):
        with gzip.open(path,'rt') as handle:
            for r in csv.DictReader(handle):
                key=(r['model'],r['var'],int(r['lead']))
                if key not in series: series[key]=(float(r['fc']),float(r['ob']),int(r['trace'] or 0))
                if len(series)>=limit: return [(*k,*v) for k,v in sorted(series.items())]
    return [(*k,*v) for k,v in sorted(series.items())]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--samples',type=Path,default=ROOT/'_sample')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--stations',type=int,required=True);p.add_argument('--days',type=int,required=True)
    p.add_argument('--rows',type=int,help='Exact row count; must fit selected series/hour capacity')
    p.add_argument('--series',type=int,default=2000)
    p.add_argument('--hours',type=int,default=3,help='Valid-time spacing within each UTC day (divisor of 24)')
    p.add_argument('--start',default='2026-10-12T00:00:00Z')
    p.add_argument('--analyze',action='store_true')
    a=p.parse_args()
    if min(a.stations,a.days,a.series,a.hours)<=0 or 24%a.hours: p.error('positive sizes and --hours must divide 24')
    if a.output.exists(): p.error('output already exists')
    seeds=templates(a.samples,a.series)
    if not seeds: p.error('no sample templates')
    capacity=a.stations*a.days*(24//a.hours)*len(seeds);target=a.rows or capacity
    if target>capacity: p.error(f'rows exceed capacity {capacity}; add series, days, or reduce --hours')
    if target<=0: p.error('rows must be positive')
    # Pair table plus two indexes, measured on generated production-shaped rows.
    estimate=target*350
    a.output.parent.mkdir(parents=True,exist_ok=True)
    free=shutil.disk_usage(a.output.parent).free
    print('DISK free_GiB',round(free/2**30,2),'estimate_GiB',round(estimate/2**30,2),flush=True)
    wsl='microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()
    if wsl and target>2000000 and not str(a.output.resolve()).startswith('/mnt/e/'):
        p.error('large WSL output must be on E:')
    if wsl and estimate>25*2**30: p.error('WSL task scratch ceiling is 25 GiB; generate the large case on Spark')
    if estimate>free: p.error('insufficient free space')
    started=time.monotonic();db=database(a.output)
    # Defer secondary indexes and mutation triggers until after bulk insertion.
    db.execute('DROP INDEX pair_valid');db.execute('DROP INDEX pair_series')
    for action in ('insert','update','delete'): db.execute('DROP TRIGGER aggregate_pairs_'+action)
    db.commit()
    count=0;batch=[];base=stamp(a.start)
    # Schedule at most days * valid-hours * series records (independent of
    # station count), then insert in WITHOUT ROWID primary-key order. This
    # avoids hundreds of millions of random B-tree writes on a large build.
    schedule=[];per_day,remainder=divmod(target,a.days)
    for day in range(a.days):
        quota=per_day+(day<remainder)
        slots=24//a.hours;per_slot,extra=divmod(quota,slots)
        for slot,hour in enumerate(range(0,24,a.hours)):
            slot_quota=per_slot+(slot<extra);n=0
            valid=base+timedelta(days=day,hours=hour);at=iso(valid)
            # Spread exact --rows across the full day and rotate selected
            # series, rather than accidentally omitting all evening pairs or
            # shrinking the group count when target < capacity.
            offset=slot*len(seeds)//slots
            for model,var,lead,fc,ob,trace in seeds[offset:]+seeds[:offset]:
                size=min(a.stations,slot_quota-n)
                if size<=0: break
                schedule.append((model,iso(valid-timedelta(hours=lead)),lead,var,at,fc,ob,trace,day,size))
                n+=size
    schedule.sort(key=lambda r:r[:4])
    from itertools import groupby
    next_log=1000000
    for key,items in groupby(schedule,key=lambda r:r[:3]):
        events=list(items)
        for st in range(a.stations):
            for model,run,lead,var,at,fc,ob,trace,day,size in events:
                if st>=size: continue
                shift=((st*17+day*11)%101-50)*.0001
                f=max(0.,fc+shift) if var.startswith('rain') else fc+shift
                o=max(0.,ob-shift) if var.startswith('rain') else ob-shift
                batch.append((model,run,lead,at,f'S{st:05d}',var,f,o,'synthetic',trace,at))
                if len(batch)>=20000:
                    db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',batch);count+=len(batch);batch=[]
                    if count>=next_log:
                        db.commit();print('ROWS',count,'seconds',round(time.monotonic()-started,2),flush=True);next_log+=1000000
    db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',batch);count+=len(batch);db.commit()
    db.close()
    # Existing migration path builds exactly the same covering valid index.
    db=database(a.output)
    if a.analyze: db.execute('ANALYZE');db.commit()
    measured=db.execute('SELECT COUNT(*),MIN(valid),MAX(valid),COUNT(DISTINCT station) FROM pairs').fetchone();db.close()
    report=dict(rows=measured[0],start=measured[1],end=measured[2],stations=measured[3],days=a.days,
                series=len(seeds),hours=a.hours,bytes=a.output.stat().st_size,seconds=time.monotonic()-started,analyzed=a.analyze)
    a.output.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)


if __name__=='__main__': main()
