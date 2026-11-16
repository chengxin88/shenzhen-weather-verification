#!/usr/bin/env python3
"""Offline aggregate A/B, original DB read-only; outputs never enter its directory.

python tools/compare_aggregates.py --samples _sample --output /tmp/aggfast-check
python tools/compare_aggregates.py --database /scratch/working.sqlite --end ... --output /scratch/check
Use --publish-copy to include a full SQLite snapshot backup and fsync timing.
"""
import argparse
from contextlib import closing, contextmanager
import csv
import gzip
import importlib.util
import json
import math
import os
from pathlib import Path
import resource
import re
import sqlite3
import subprocess
import sys
import time
import shutil
import tempfile
from itertools import zip_longest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from compare_station_runs import Comparison, canonical, json_data


class AggregateComparison(Comparison):
    """Exact counters and strict relative tolerance, including SQLite REAL counts."""
    def __init__(self):
        super().__init__(); self.float_differences={}; self.max_absolute_error=0.

    def value(self,a,b,at):
        if a==b: return
        if isinstance(a,(int,float)) and isinstance(b,(int,float)):
            if a==b: return
            key=at.rsplit('.',1)[-1]
            self.max_relative_error=max(self.max_relative_error,abs(a-b)/max(abs(a),abs(b),1e-300))
            self.max_absolute_error=max(self.max_absolute_error,abs(a-b))
            self.float_differences[key]=self.float_differences.get(key,0)+1
            if key in ('n','H','M','F','C','T','GO','GU','N1','N2') or re.fullmatch(r'[hmf]\d+|g\d+[hmf]',key):
                self.fail(at,a,b)
            elif not math.isclose(a,b,rel_tol=1e-9,abs_tol=0.): self.fail(at,a,b)
        else: super().value(a,b,at)

    def sqlite(self,a,b,table,order,exact=False,json_columns=()):
        from itertools import zip_longest
        with closing(readonly(a)) as da,closing(readonly(b)) as db:
            # Explicit grade tie-break avoids reliance on indexes/rowid order.
            if table=='detail_stats': order+=",COALESCE(json_extract(stats,'$.grade'),0)"
            ca=da.execute('SELECT * FROM '+table+' ORDER BY '+order)
            cb=db.execute('SELECT * FROM '+table+' ORDER BY '+order)
            names=[r[0] for r in ca.description]
            self.value(names,[r[0] for r in cb.description],table+'.columns')
            count=0
            for count,(a,b) in enumerate(zip_longest(ca,cb),1):
                if a is None or b is None: self.fail(table+'.rows',a,b);continue
                if a==b: continue
                if exact: self.fail(table+f'[{count}]',a,b);continue
                left,right=dict(zip(names,a)),dict(zip(names,b))
                for col in json_columns: left[col],right[col]=json.loads(left[col]),json.loads(right[col])
                self.value(left,right,table+f'[{count}]')
            self.checked[table]=count


def readonly(path):
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    db.execute('PRAGMA cache_size=-64000'); db.execute('PRAGMA temp_store=FILE')
    return db


def build_samples(samples, target):
    import station_score as score
    with closing(score.database(target)) as db:
        for index in ('pair_valid','pair_series'): db.execute('DROP INDEX '+index)
        for action in ('insert','update','delete'): db.execute('DROP TRIGGER aggregate_pairs_'+action)
        db.commit()
        count = 0
        for path in sorted(samples.glob('*.csv.gz')):
            with gzip.open(path, 'rt') as handle:
                rows = csv.reader(handle); next(rows)
                while True:
                    from itertools import islice
                    batch = list(islice(rows, 10000))
                    if not batch: break
                    db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)', batch)
                    count += len(batch)
            db.commit()
            print('SAMPLE', path.name, count, flush=True)
        end=db.execute('SELECT MAX(valid) FROM pairs').fetchone()[0]
    with closing(score.database(target)) as db: pass
    return end


def query_plans(database,end):
    from station_common import stamp,iso
    from datetime import timedelta
    lower=iso(stamp(end)-timedelta(days=30));args=(lower,end)
    expressions='COUNT(*),SUM(fc-ob),SUM(fc*ob),SUM(trace)'
    queries={
        'legacy_detail':('SELECT station,model,var,lead,'+expressions+' FROM pairs WHERE valid>=? AND valid<=? GROUP BY station,model,var,lead',args),
        'legacy_continuous_lead':("SELECT model,var,lead,"+expressions+" FROM pairs WHERE valid>=? AND valid<=? AND var NOT IN ('rain6','rain12','rain24') GROUP BY model,var,lead",args),
        'legacy_grid_daily':("SELECT model,var,date(valid,'+8 hours'),lead,station,"+expressions+" FROM pairs GROUP BY model,var,date(valid,'+8 hours'),lead,station",()),
        'fast_window':("SELECT station,model,var,lead,valid,fc,ob,CASE WHEN var IN ('rain6','rain12','rain24') AND fc>=0.1 AND ob<0.1 THEN trace ELSE 0 END,run FROM pairs INDEXED BY pair_valid WHERE valid>=? AND valid<=? ORDER BY valid,model,run,lead,station,var",args)}
    with closing(readonly(database)) as db:
        return {name:db.execute('EXPLAIN QUERY PLAN '+sql,params).fetchall() for name,(sql,params) in queries.items()}


def incremental_worker(score,db,directory,end):
    """Synthetic 3h advance on this worker's PRIVATE copy; original stays read-only."""
    from datetime import timedelta
    from station_common import stamp,iso,atomic_json,sync_directory
    import os
    latest=db.execute('SELECT MAX(valid) FROM pairs').fetchone()[0]
    rows=db.execute('SELECT * FROM pairs WHERE valid=? LIMIT 128',(latest,)).fetchall()
    advanced=iso(stamp(end)+timedelta(hours=3));batch=[]
    for record in rows:
        row=list(record);row[1]=iso(stamp(row[1])+timedelta(hours=3))
        row[3]=iso(stamp(row[3])+timedelta(hours=3));batch.append(row)
    with db:
        db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',batch)
        if rows:
            row=rows[0]
            db.execute('UPDATE pairs SET fc=fc+0.01 WHERE model=? AND run=? AND lead=? AND station=? AND var=?',
                       (row[0],row[1],row[2],row[4],row[5]))
        days={r[3][:10] for r in batch+list(rows[:1])}
        db.executemany('INSERT OR IGNORE INTO dirty_days VALUES (?)',((d,) for d in days))
    output=directory/'incremental';output.mkdir()
    with score.phase('incremental_aggregates'): score.aggregates(db,output,stamp(advanced))
    atomic_json(output/'common-report.json',score.common_report(db,stamp(advanced)))
    exports=output/'exports';exports.mkdir()
    with score.phase('incremental_export_days'): score.export_days(db,exports,days)
    with score.phase('incremental_backup'),closing(sqlite3.connect(output/'published.sqlite')) as published:
        db.commit();db.backup(published)
    with score.phase('incremental_publish_fsync'):
        for file in output.iterdir():
            if file.is_file():
                with file.open('rb') as handle: os.fsync(handle.fileno())
        sync_directory(output)
    manifest=dict(end=advanced,inserted=len(batch),rewritten=int(bool(rows)),dirty_utc_days=sorted(days),
                  synthetic=True,original_database_unchanged=True)
    atomic_json(output/'scenario.json',manifest)
    return manifest


def worker(args):
    from station_common import stamp, atomic_json
    directory = args.output; directory.mkdir(parents=True, exist_ok=True)
    if args.reference:
        spec = importlib.util.spec_from_file_location('aggregate_reference', args.reference)
        score = importlib.util.module_from_spec(spec); spec.loader.exec_module(score)
    else:
        import station_score as score
    timings = {}
    runs = {}
    if args.publish_copy:
        started=time.monotonic()
        with closing(readonly(args.database)) as source,closing(sqlite3.connect(directory/'detail.sqlite')) as target:
            source.backup(target)
        timings['prepare_input']=time.monotonic()-started
        db=score.database(directory/'detail.sqlite')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='aggregate_state'").fetchone():
            with db:
                db.execute('DELETE FROM aggregate_state')
                db.execute("INSERT OR IGNORE INTO aggregate_dirty VALUES ('*')")
    else: db=sqlite3.connect(directory/'detail.sqlite',uri=True)
    with closing(db):
        db.execute('PRAGMA cache_size=-64000'); db.execute('PRAGMA temp_store=FILE')
        # The attached input remains read-only; legacy unqualified SQL resolves
        # this view. No full working DB copy is needed for an aggregate check.
        if not args.publish_copy:
            uri = args.database.resolve().as_uri()+'?mode=ro'
            db.execute('ATTACH DATABASE ? AS original', (uri,))
            db.execute('PRAGMA original.cache_size=-64000')
            db.execute('CREATE TEMP VIEW pairs AS SELECT * FROM original.pairs')
        original_phase = score.phase
        @contextmanager
        def phase(name, unused=None):
            started=time.monotonic()
            try:
                with original_phase(name,timings): yield
            finally: runs.setdefault(name,[]).append(time.monotonic()-started)
        score.phase = phase
        for name in ('aggregates', 'common_report'):
            with score.phase(name):
                value = getattr(score,name)(db,directory,stamp(args.end)) if name=='aggregates' else score.common_report(db,stamp(args.end))
            if name=='common_report': atomic_json(directory/'common-report.json',value)
        with score.phase('coverage'):
            for model in score.MODELS:
                db.execute('SELECT COUNT(*),MIN(valid),MAX(valid),COUNT(DISTINCT station) FROM pairs WHERE model=?',(model,)).fetchone()
                db.execute('SELECT DISTINCT var FROM pairs WHERE model=?',(model,)).fetchall()
                db.execute('SELECT DISTINCT lead FROM pairs WHERE model=? ORDER BY lead',(model,)).fetchall()
        if args.export_days!='none':
            if args.export_days=='pending': days={r[0] for r in db.execute('SELECT day FROM dirty_days')}
            elif args.export_days=='latest':
                latest=db.execute('SELECT MAX(valid) FROM pairs').fetchone()[0]
                days={latest[:10]} if latest else set()
            else: days={r[0] for r in db.execute('SELECT DISTINCT substr(valid,1,10) FROM pairs')}
            exports=directory/'exports';exports.mkdir()
            with score.phase('export_days'): score.export_days(db,exports,days)
        if args.publish_copy:
            with score.phase('backup'), closing(sqlite3.connect(directory/'published.sqlite')) as published:
                db.commit();db.backup(published)
            with score.phase('publish_fsync'):
                import os
                from station_common import sync_directory
                for product in directory.iterdir():
                    if product.is_file():
                        with product.open('rb') as handle: os.fsync(handle.fileno())
                sync_directory(directory)
        if args.cache_check and not args.reference:
            repeat=directory/'repeat';repeat.mkdir()
            with score.phase('same_end_repeat'): score.aggregates(db,repeat,stamp(args.end))
        scenario=incremental_worker(score,db,directory,args.end) if args.incremental_check and not args.reference else None
    report = dict(timings=timings, phase_runs=runs, peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)
    if scenario is not None: report['incremental']=scenario
    atomic_json(directory/'timing.json',report)


def array_rows(path):
    """Stream JSON arrays, including gzip, without materializing whole products."""
    opener=gzip.open if path.suffix=='.gz' else open
    decoder=json.JSONDecoder();buffer='';position=0;done=False;started=False
    with opener(path,'rt',encoding='utf-8') as handle:
        while True:
            if position>len(buffer)//2: buffer=buffer[position:];position=0
            if not done:
                chunk=handle.read(65536);done=not chunk;buffer+=chunk
            while True:
                while position<len(buffer) and buffer[position].isspace(): position+=1
                if not started:
                    if position==len(buffer): break
                    if buffer[position]!='[': raise ValueError('expected JSON array: '+str(path))
                    position+=1;started=True;continue
                if position==len(buffer): break
                if buffer[position]==']': return
                if buffer[position]==',': position+=1;continue
                try: row,end=decoder.raw_decode(buffer,position)
                except json.JSONDecodeError:
                    if done: raise
                    break
                yield row;position=end
            if done: raise ValueError('truncated JSON array: '+str(path))


def compare_array(result,left,right,name):
    # Disk sort preserves comparison independent of incidental JSON array order.
    with tempfile.TemporaryDirectory(prefix='compare-json-',dir=right.parent) as scratch:
        with closing(sqlite3.connect(Path(scratch)/'sort.sqlite')) as db:
            db.execute('PRAGMA cache_size=-8000');db.execute('PRAGMA temp_store=FILE')
            for side,path in (('a',left),('b',right)):
                db.execute('CREATE TABLE '+side+' (k TEXT,v TEXT)')
                def records():
                    for row in array_rows(path):
                        row={k:v for k,v in row.items() if k not in ('created','updated')}
                        key={k:v for k,v in row.items() if k in ('dataset','id','station','model','var','run','lead','valid','threshold','lead_min','lead_max','day','grade')}
                        yield json.dumps(key,sort_keys=True),json.dumps(row,separators=(',',':'))
                db.executemany('INSERT INTO '+side+' VALUES (?,?)',records())
                db.execute('CREATE INDEX '+side+'_key ON '+side+'(k)')
            count=0
            for count,(a,b) in enumerate(zip_longest(db.execute('SELECT v FROM a ORDER BY k'),db.execute('SELECT v FROM b ORDER BY k')),1):
                if a is None or b is None: result.fail(name+'.length',a,b)
                elif a!=b: result.value(json.loads(a[0]),json.loads(b[0]),name+f'[{count}]')
            result.checked[name]=count


def compare(left, right):
    result = AggregateComparison()
    for pattern in ('scores-*.json','station-scores-*.json','grid-rows-*.json.gz'):
        names = {p.name for p in left.glob(pattern)} | {p.name for p in right.glob(pattern)}
        for name in sorted(names):
            compare_array(result,left/name,right/name,name)
    def published(directory):
        return directory/'published.sqlite' if (directory/'published.sqlite').is_file() else directory/'detail.sqlite'
    result.sqlite(published(left),published(right),'detail_stats','station,model,var,lead,days,threshold',json_columns=('stats',))
    if (left/'published.sqlite').is_file() and (right/'published.sqlite').is_file():
        result.sqlite(published(left),published(right),'pairs','model,run,lead,station,var',exact=True)
    result.sqlite(left/'grid-map.sqlite',right/'grid-map.sqlite','daily','model,var,day,lead,station')
    for name in ('grid-datasets.json','common-report.json'):
        result.value(canonical(json_data(left/name),('created','updated','gzip_bytes')),
                     canonical(json_data(right/name),('created','updated','gzip_bytes')),name)
    return dict(equal=result.mismatches==0,mismatches=result.mismatches,errors=result.errors,checked=result.checked,
                max_relative_error=result.max_relative_error,
                max_absolute_error=result.max_absolute_error,float_differences=result.float_differences,
                tolerance=dict(relative=1e-9,absolute=0),
                ignored=['created/updated publication timestamps','gzip_bytes','grid-status elapsed_seconds/map_bytes'])


def baseline_missing(directory):
    required=['timing.json','grid-map.sqlite','grid-datasets.json','common-report.json']
    required += [f'{p}-{d}.json' for p in ('scores','station-scores') for d in (1,3,7,30)]
    required += [f'grid-rows-{v}.json.gz' for v in ('t','rain6','rain12','rain24','msl','wind')]
    missing=[name for name in required if not (directory/name).is_file()]
    if not any((directory/n).is_file() for n in ('detail.sqlite','published.sqlite')): missing.append('detail.sqlite or published.sqlite')
    return missing


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--samples',type=Path); source.add_argument('--database',type=Path)
    parser.add_argument('--output',type=Path,required=True); parser.add_argument('--end')
    parser.add_argument('--baseline-ref',default='b37cbd8')
    parser.add_argument('--incremental-baseline-dir',type=Path,help='Reuse before-incremental/ from the identical deterministic incremental scenario')
    parser.add_argument('--baseline-dir',type=Path,help='Reuse immutable before/ from an earlier comparison; needs detail.sqlite or published.sqlite, all score/grid files, common-report.json and timing.json')
    parser.add_argument('--publish-copy',action='store_true')
    parser.add_argument('--cache-check',action='store_true',help='With --publish-copy, also measure a same-end cache hit')
    parser.add_argument('--incremental-check',action='store_true',help='Private copy: add 128 pairs, revise one, advance 3h; verify against legacy again')
    parser.add_argument('--export-days',choices=('none','pending','latest','all'),default='pending',
                        help='CSV export timing; latest emulates one pending UTC day on an already published DB')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--reference',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.worker: return worker(args)
    if args.incremental_baseline_dir and not args.incremental_check: parser.error('--incremental-baseline-dir requires --incremental-check')
    if (args.cache_check or args.incremental_check) and not args.publish_copy: parser.error('cache/incremental checks require --publish-copy')
    args.output=args.output.resolve()
    if args.output.exists(): parser.error('--output must be a new directory (prevents accidental reuse)')
    size=args.database.stat().st_size if args.database else sum(p.stat().st_size for p in args.samples.glob('*.csv.gz'))*20
    try: wsl='microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()
    except OSError: wsl=bool(os.environ.get('WSL_DISTRO_NAME'))
    if wsl and size>=1024**2 and not str(args.output).startswith('/mnt/e/'):
        parser.error('large WSL comparisons require /mnt/e/scratch; if E: is sandbox read-only, stop instead of using HOME or /tmp')
    factor=12 if args.incremental_check else 8 if args.publish_copy else 6
    floor=32 if args.incremental_check else 20 if args.publish_copy else 8
    estimated=max(size*factor,(512*1024**2 if size<1024**2 else floor*1024**3))
    parent=args.output.parent
    while not parent.exists(): parent=parent.parent
    free=shutil.disk_usage(parent).free
    if wsl and not str(args.output).startswith('/mnt/') and Path('/mnt/c').exists():
        free=min(free,shutil.disk_usage('/mnt/c').free)
    print('DISK free_gib',round(free/1024**3,2),'estimated_budget_gib',round(estimated/1024**3,2),flush=True)
    if free<estimated: parser.error('insufficient scratch space for the conservative A/B budget; choose a larger disk')
    args.output.mkdir(parents=True)
    if args.samples:
        args.database=args.output/'sample.sqlite'; end=build_samples(args.samples,args.database)
    else:
        with closing(readonly(args.database)) as db:
            end=db.execute('SELECT MAX(valid) FROM pairs').fetchone()[0]
        pointer=args.database.parent/'current.json'
        if pointer.is_file():
            metadata=args.database.parent/'snapshots'/json_data(pointer)['generation']/'meta.json'
            if metadata.is_file(): end=json_data(metadata)['window_end']
    args.end=args.end or end
    identity=(args.database.stat().st_size,args.database.stat().st_mtime_ns)
    (args.output/'query-plans.json').write_text(json.dumps(query_plans(args.database,args.end),indent=2))
    reference=args.output/'reference.py'
    git=['git','--git-dir=.aggfast-git','--work-tree=.'] if (ROOT/'.aggfast-git').is_dir() else ['git']
    if not args.baseline_dir or (args.incremental_check and not args.incremental_baseline_dir):
        reference.write_bytes(subprocess.check_output([*git,'show',args.baseline_ref+':station_score.py'],cwd=ROOT))
    baseline=args.baseline_dir.resolve() if args.baseline_dir else args.output/'before'
    for label,directory in (('baseline',args.baseline_dir),('incremental baseline',args.incremental_baseline_dir)):
        if directory:
            missing=baseline_missing(directory)
            if missing: parser.error(label+' missing: '+', '.join(missing))
    for label in (('after',) if args.baseline_dir else ('before','after')):
        command=[sys.executable,str(Path(__file__).resolve()),'--worker','--database',str(args.database.resolve()),
                 '--end',args.end,'--output',str(args.output/label)]
        if label=='before': command+=['--reference',str(reference)]
        if args.publish_copy: command+=['--publish-copy']
        if args.cache_check: command+=['--cache-check']
        if args.incremental_check: command+=['--incremental-check']
        command+=['--export-days',args.export_days]
        with (args.output/(label+'.log')).open('w') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
        measured=json_data(args.output/label/'timing.json')
        for line in (args.output/(label+'.log')).read_text().splitlines():
            parts=line.split()
            if len(parts)>2 and parts[0]=='TIMING': measured['timings'].setdefault(parts[1],float(parts[2]))
        (args.output/label/'timing.json').write_text(json.dumps(measured))
        print(label,measured,flush=True)
    report=compare(baseline,args.output/'after')
    if identity!=(args.database.stat().st_size,args.database.stat().st_mtime_ns):
        raise RuntimeError('Input DB changed during comparison; use a frozen private copy')
    report['end']=args.end
    report['measurements']={'before':json_data(baseline/'timing.json'),'after':json_data(args.output/'after/timing.json')}
    report['baseline_dir']=str(baseline)
    if args.incremental_check:
        end=json_data(args.output/'after/timing.json')['incremental']['end']
        incremental_baseline=args.incremental_baseline_dir.resolve() if args.incremental_baseline_dir else args.output/'before-incremental'
        if not args.incremental_baseline_dir:
            command=[sys.executable,str(Path(__file__).resolve()),'--worker','--database',str(args.output/'after/detail.sqlite'),
                     '--end',end,'--output',str(incremental_baseline),'--reference',str(reference),
                     '--export-days',args.export_days]
            with (args.output/'before-incremental.log').open('w') as log:
                subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
        report['incremental_comparison']=compare(incremental_baseline,args.output/'after/incremental')
        report['measurements']['before_incremental']=json_data(incremental_baseline/'timing.json')
        report['incremental_baseline_dir']=str(incremental_baseline)
        report['equal']=report['equal'] and report['incremental_comparison']['equal']
        report['mismatches']+=report['incremental_comparison']['mismatches']
    (args.output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0 if report['equal'] else 1


if __name__=='__main__': raise SystemExit(main())
