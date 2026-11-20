"""Incremental offline station pairing, compact daily pairs and score snapshots."""
from collections import defaultdict
from contextlib import ExitStack, contextmanager, closing
from datetime import timedelta
from pathlib import Path
import csv
import gzip
import json
import math
import os
import resource
import re
import shutil
import sqlite3
import time
import guifan_scores as guifan
import numpy as np
from station_common import (ROOT, XINJIANG, MODELS, THRESHOLDS, VARIABLES, VERSION, UTC, DAY_WINDOWS, REGION,
                            PAIR_DAYS, RUN_DAYS, COMMON_DAYS, model_allowed, legacy_exclusions,
                            GRID_MODELS, GRID_DATASETS, GRID_THRESHOLDS, MOMENTS, bjt, moment_score,
                            atomic_json, digest, finite, iso, now, read_json, stamp, sync_directory)
from station_forecasts import accumulate, forecast_runs, file_signature, load_samples, save_samples, sample_state
from station_obs import load_observations


SOUTH_ONLY = 'station NOT IN (' + ','.join(repr(v) for v in XINJIANG) + ')'


def job_scope(model):
    """The pairs model and station clause a job owns. wn3xj is WN3 at the Xinjiang points: one model, disjoint stations."""
    if model == 'wn3xj':
        return 'wn3', ' AND NOT ' + SOUTH_ONLY
    return model, ' AND ' + SOUTH_ONLY if model == 'wn3' else ''


@contextmanager
def phase(name, timings=None):
    started = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - started
        if timings is not None:
            timings[name] = timings.get(name, 0) + elapsed
        print('TIMING', name, round(elapsed, 3), 'seconds', flush=True)


def atomic_score_rows(path, rows):
    """Keep score JSON bytes while avoiding a second full list-sized string."""
    temporary=path.with_name(path.name+'.next')
    with temporary.open('w',encoding='utf-8') as handle:
        handle.write('[')
        for i,row in enumerate(rows):
            if i: handle.write(',')
            handle.write(json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(',',':')))
        handle.write(']')
    temporary.replace(path)


def continuous(fc, ob):
    fc, ob = np.asarray(fc, float), np.asarray(ob, float)
    valid = np.isfinite(fc) & np.isfinite(ob); fc, ob = fc[valid], ob[valid]
    n = len(fc)
    if not n:
        return dict(n=0, ME=None, MAE=None, RMSE=None, CC=None)
    errors = fc - ob
    return dict(n=n, ME=float(np.mean(errors)), MAE=float(np.mean(np.abs(errors))),
                RMSE=float(np.sqrt(np.mean(errors ** 2))),
                CC=float(np.corrcoef(fc, ob)[0, 1]) if n > 1 and np.std(fc) > 1e-10 and np.std(ob) > 1e-10 else None)


def contingency(fc, ob, threshold):
    fc, ob = np.asarray(fc, float), np.asarray(ob, float)
    valid = np.isfinite(fc) & np.isfinite(ob); fc, ob = fc[valid], ob[valid]
    yesf, yeso = fc >= threshold, ob >= threshold
    h, m, f = int(np.sum(yesf & yeso)), int(np.sum(~yesf & yeso)), int(np.sum(yesf & ~yeso))
    n = len(fc); chance = (h + m) * (h + f) / n if n else 0
    def divide(a, b): return a / b if b > 0 else None
    return dict(n=n, H=h, M=m, F=f, C=n-h-m-f, TS=divide(h,h+m+f), POD=divide(h,h+m),
                FAR=divide(f,h+f), ETS=divide(h-chance,h+m+f-chance))


def pair_run(model, run, samples, observations, stations, cutoff, end, valid_times=None):
    run_text = iso(run)
    intervals = {h: (float(s['rain_start']), s['rain']) for h, s in samples.items()
                 if 'rain' in s and 'rain_start' in s and math.isfinite(float(s['rain_start']))}
    for lead, sample in sorted(samples.items()):
        valid = run + timedelta(hours=lead)
        valid_text = iso(valid)
        if not 0 <= lead <= 240 or not cutoff <= valid <= end:
            continue
        if valid_times is not None and valid_text not in valid_times:
            continue
        # Native 0.25-degree archives are admitted only at 3h nominal times.
        if model not in ('ec01', 'wn3') and (lead % 3 or valid.hour % 3):
            continue
        values = {v: sample[v] for v in ('t', 'td', 'msl', 'wind') if v in sample}
        for hours in (6, 12, 24):
            total = accumulate(intervals, lead, hours)
            if total is not None:
                values['rain' + str(hours)] = total
        for variable, array in values.items():
            for i, station in enumerate(stations):
                if not model_allowed(station, model):
                    continue
                observation = observations.get((station['id'], valid_text, variable))
                fc = finite(array[i])
                if observation is None or fc is None:
                    continue
                if variable.startswith('rain') and fc < 0:
                    continue
                yield (model, run_text, lead, valid_text, station['id'], variable, fc,
                       observation['value'], observation['source'], int(observation.get('trace', False)),
                       observation.get('report_time', observation['time']))


def database(path):
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=DELETE')
    db.execute('PRAGMA cache_size=-64000'); db.execute('PRAGMA temp_store=FILE')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS pairs (
          model TEXT, run TEXT, lead INTEGER, valid TEXT, station TEXT, var TEXT,
          fc REAL, ob REAL, source TEXT, trace INTEGER, report_time TEXT,
          PRIMARY KEY(model,run,lead,station,var)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS pair_valid ON pairs(valid);
        CREATE INDEX IF NOT EXISTS pair_series ON pairs(station,model,var,lead,valid);
        CREATE TABLE IF NOT EXISTS jobs (model TEXT,run TEXT,signature TEXT,
          PRIMARY KEY(model,run)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS job_state (model TEXT,run TEXT,version TEXT,source TEXT,
          PRIMARY KEY(model,run)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS job_valid (model TEXT,run TEXT,valid TEXT,signature TEXT,
          PRIMARY KEY(model,run,valid)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS dirty_days (day TEXT PRIMARY KEY) WITHOUT ROWID;
    ''')
    columns = tuple(r[2] for r in db.execute('PRAGMA index_info(pair_valid)'))
    covering = ('valid','model','run','lead','station','var','fc','ob','trace')
    if columns != covering:
        # Keep the old valid/primary-key iteration order exactly, adding only
        # the score inputs. Aggregate scans no longer fetch the large pair row
        # separately for every index entry. DDL rollback protects killed builds.
        with phase('index_migration'):
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('DROP INDEX pair_valid')
                db.execute('CREATE INDEX pair_valid ON pairs(valid,model,run,lead,station,var,fc,ob,trace)')
                db.commit()
            except BaseException:
                db.rollback()
                raise
    aggregate_tracking(db)
    return db


def aggregate_tracking(db):
    """Track every pair mutation, independently of pending CSV exports."""
    db.execute('CREATE TABLE IF NOT EXISTS aggregate_dirty (day TEXT PRIMARY KEY) WITHOUT ROWID')
    db.execute('CREATE TABLE IF NOT EXISTS aggregate_state (key TEXT PRIMARY KEY,value TEXT) WITHOUT ROWID')
    actions = dict(insert=['new'], delete=['old'], update=['old','new'])
    with db:
        for action, aliases in actions.items():
            name = 'aggregate_pairs_'+action
            before = ''
            if action=='insert':
                # REPLACE does not fire DELETE triggers unless the external
                # writer enables recursive_triggers. Capture its old day before
                # SQLite removes the conflicting primary-key row.
                before = 'INSERT OR IGNORE INTO aggregate_dirty SELECT substr(valid,1,10) FROM pairs WHERE model=new.model AND run=new.run AND lead=new.lead AND station=new.station AND var=new.var;'
            sql = 'CREATE TRIGGER '+name+(' BEFORE ' if action=='insert' else ' AFTER ')+action.upper()+' ON pairs BEGIN '+before+''.join(
                'INSERT OR IGNORE INTO aggregate_dirty VALUES (substr('+alias+'.valid,1,10));' for alias in aliases)+' END'
            previous = db.execute('SELECT sql FROM sqlite_master WHERE type=\'trigger\' AND name=?',(name,)).fetchone()
            if previous != (sql,):
                db.execute('INSERT OR IGNORE INTO aggregate_dirty VALUES (\'*\')')
                db.execute('DROP TRIGGER IF EXISTS '+name)
                db.execute(sql)


def valid_diff(previous, current, cutoff, end):
    """Missing and explicitly absent observations differ; ignore rolled-off times."""
    return {at for at in previous.keys() | current.keys()
            if cutoff <= at <= end and
            (at not in previous or at not in current or previous[at] != current[at])}


def upgrade_pair_precision(db, model, run, samples, stations):
    """Recover precision lost only in legacy sample serialization.

    Call only after proving the forecast source unchanged. Published fc values
    are the original samples used for scoring. Restoring their low bits to the
    cache leaves pairs untouched and makes a later full rescore reproducible.
    This is a one-time format migration, including otherwise unchanged runs.
    """
    path = getattr(samples,'cache_path',None)
    if path is None or not path.is_file():return
    sidecar = path.with_suffix('.sample-state.json')
    state,_ = sample_state(path,sidecar)
    from station_precision import legacy_leads,restore_rain
    precision = state.get('pair_precision',0)
    legacy = legacy_leads(state.get('sources',[]))
    if precision >= 2 or (precision == 1 and not legacy):return
    if not state.get('station_signature') or not ('sources' in state or 'source' in state):return
    # This job's samples say nothing about pairs another job owns under the same model.
    model,scope = job_scope(model)
    rows = db.execute("SELECT lead,station,var,fc FROM pairs WHERE model=? AND run=? AND var IN ('t','td','msl','wind')"+scope,(model,run))
    first = next(rows,None)
    if first is None and not legacy:return
    from itertools import chain
    values = load_samples(path)
    for point in values.values():
        for variable in ('t','td','msl','wind'):
            if variable in point:point[variable] = np.asarray(point[variable],dtype=float).copy()
    indices = {s['id']:i for i,s in enumerate(stations)}
    corrected = conflicts = 0
    float32_max = float(np.finfo(np.float32).max)
    for lead,station,variable,fc in chain([] if first is None else [first],rows):
        array = values.get(lead,{}).get(variable)
        if array is None or station not in indices:continue
        i = indices[station]; previous = float(array[i])
        if previous == fc:continue
        if math.isfinite(previous) and max(abs(previous),abs(fc)) <= float32_max and np.float32(previous) == np.float32(fc):
            array[i] = fc; corrected += 1
        else:conflicts += 1
    if conflicts:
        raise ValueError(f'Forecast cache disagrees with unchanged pairs beyond float32 rounding: {model} {run}: {conflicts}')
    rain_restored = 0
    if legacy:
        metadata = getattr(samples,'precip_metadata',None)
        if metadata is None:
            try:
                with gzip.open(path.with_suffix('.sources.json.gz'),'rt') as handle:metadata=json.load(handle)
            except (OSError,ValueError,EOFError):metadata={}
        rain_rows = list(db.execute("SELECT lead,station,var,fc FROM pairs WHERE model=? AND run=? AND var IN ('rain6','rain12','rain24')"+scope,(model,run)))
        rain_restored = restore_rain(values,state.get('sources',[]),metadata,model,run,stations,rain_rows)
    state = dict(state,pair_precision=2)
    save_samples(path,sorted(values),values,state=state)
    published_state = dict(state,sample_file_signature=file_signature(path))
    atomic_json(sidecar,published_state,durable=True)
    if 'sources' in state:
        atomic_json(path.with_suffix('.certified.json'),published_state,durable=True)
    print('SAMPLE_PRECISION',model,run,corrected,'restored values',rain_restored,'rain intervals',flush=True)


def score_job(db, model, run, samples, source_sig, obs_signatures, observations,
              stations, cutoff, end, elevations=None):
    """Commit a run's pairs, digests and pending exports in one transaction.

    An old jobs signature can seed the new metadata only when it matches exactly.
    Otherwise migration conservatively scores that run in full.
    """
    run_text = iso(run); pair_model, scope = job_scope(model)
    current = {iso(run+timedelta(hours=h)): obs_signatures.get(iso(run+timedelta(hours=h)))
               for h in samples if 0 <= h <= 240 and cutoff <= run+timedelta(hours=h) <= end}
    state = db.execute('SELECT version,source FROM job_state WHERE model=? AND run=?',
                       (model, run_text)).fetchone()
    previous = dict(db.execute('SELECT valid,signature FROM job_valid WHERE model=? AND run=?',
                               (model, run_text)))
    full = state != (VERSION, source_sig)
    legacy_sig = getattr(samples, 'legacy_signature', source_sig)
    signature = digest([VERSION, legacy_sig, sorted(current.items())])
    if state is None:
        legacy = db.execute('SELECT signature FROM jobs WHERE model=? AND run=?', (model,run_text)).fetchone()
        if legacy == (signature,):
            full = False
            previous = current
    changed = set(current) if full else valid_diff(previous, current, iso(cutoff), iso(end))
    if not full:upgrade_pair_precision(db,model,run_text,samples,stations)
    if not full and not changed and state is not None and previous == current:
        # The per-valid metadata is already current. Avoid a write transaction
        # and fsync for every unchanged run on every timer tick.
        return False,0,False,0
    batch = []
    if full or changed:
        wanted = {int((stamp(at)-run).total_seconds()/3600) for at in changed if current.get(at) is not None}
        # Include the exact backward accumulation tiles, even before cutoff.
        loaded = samples.load(wanted) if hasattr(samples, 'load') else samples
        elevation = next((s['elevation'] for s in loaded.values() if 'elevation' in s),None)
        if elevations is not None and elevation is not None:elevations[model] = elevation
        batch = list(pair_run(pair_model, run, loaded, observations, stations, cutoff, end,
                              None if full else changed))
    written = len(batch)
    with db:
        days = {at[:10] for at in changed}
        if full:
            # 2026-11-16 整个起报要重评时（站表增减一个站、预报源签名变等）只写有变化的配对。
            # 结束后表内容和「整段删掉再全部插入」完全相同；没变的配对不再重写，汇总的脏日期（触发器记的
            # aggregate_dirty）也只记真正改了的配对；CSV 导出用的 dirty_days 仍按整个起报标，和以前一样。
            # 以前站表多一个站就要把 1391 个起报、6500 万条配对全部重写（约 50 分钟）。
            old = {(lead,station,var):(pair_model,run_text,lead,valid,station,var,fc,ob,source,trace,report)
                   for lead,valid,station,var,fc,ob,source,trace,report in db.execute(
                       'SELECT lead,valid,station,var,fc,ob,source,trace,report_time FROM pairs WHERE model=? AND run=?'+scope,
                       (pair_model,run_text))}
            days.update(row[3][:10] for row in old.values())
            new = {(row[2],row[4],row[5]):row for row in batch}
            if len(new) != len(batch): raise ValueError(f'duplicate pair keys in {model} {run_text}')
            stale = [key for key,row in old.items() if new.get(key) != row]
            batch = [row for key,row in new.items() if old.get(key) != row]
            written = len(batch)
            db.executemany('DELETE FROM pairs WHERE model=? AND run=? AND lead=? AND station=? AND var=?',
                           ((pair_model,run_text,*key) for key in stale))
        else:
            # valid is determined by run+lead: use the existing primary-key prefix.
            db.executemany('DELETE FROM pairs WHERE model=? AND run=? AND lead=?'+scope,
                           ((pair_model,run_text,int((stamp(at)-run).total_seconds()/3600)) for at in changed))
        db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)', batch)
        db.executemany('INSERT OR IGNORE INTO dirty_days VALUES (?)', ((d,) for d in days))
        db.execute('INSERT OR REPLACE INTO job_state VALUES (?,?,?,?)', (model,run_text,VERSION,source_sig))
        db.execute('DELETE FROM job_valid WHERE model=? AND run=?', (model,run_text))
        db.executemany('INSERT INTO job_valid VALUES (?,?,?,?)', ((model,run_text,at,sig) for at,sig in current.items()))
        db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?)', (model,run_text,signature))
    return bool(full or changed), written, full, len(changed)


def stats_sql(db, columns, where='1', params=(), rain=False, threshold=None):
    keys = ','.join(columns)
    if rain:
        expression = 'COUNT(*),SUM(fc>=? AND ob>=?),SUM(fc<? AND ob>=?),SUM(fc>=? AND ob<?),' + ','.join(guifan.sql_expressions())
        arguments = (threshold,) * 6 + tuple(params)
    else:
        expression = 'COUNT(*),SUM(fc-ob),SUM(ABS(fc-ob)),SUM((fc-ob)*(fc-ob)),SUM(fc),SUM(ob),SUM(fc*fc),SUM(ob*ob),SUM(fc*ob),' + ','.join(guifan.sql_expressions())
        arguments = tuple(params)
    query = f'SELECT {keys},{expression} FROM '+_pair_valid(db)+f' WHERE {where} GROUP BY {keys}'
    rows = []
    for values in db.execute(query, arguments):
        result = dict(zip(columns, values[:len(columns)])); sums = values[len(columns):]; n = sums[0]
        if rain:
            _, h, m, f = sums[:4]; chance = (h + m) * (h + f) / n
            def divide(a, b): return a / b if b > 0 else None
            result.update(n=n, threshold=threshold, H=h, M=m, F=f, C=n-h-m-f,
                          TS=divide(h,h+m+f), POD=divide(h,h+m), FAR=divide(f,h+f),
                          ETS=divide(h-chance,h+m+f-chance))
        else:
            _, error, absolute, squared, sf, so, sff, soo, sfo = sums[:9]
            vf, vo = max(0., sff-sf*sf/n), max(0., soo-so*so/n)
            cc = max(-1., min(1., (sfo-sf*so/n)/math.sqrt(vf*vo))) if n > 1 and vf > 1e-8 and vo > 1e-8 else None
            result.update(n=n, ME=error/n, MAE=absolute/n, RMSE=math.sqrt(max(0., squared/n)), CC=cc)
        result.update(guifan.extras(result.get('var',''),sums[4:] if rain else sums[9:],threshold))
        rows.append(guifan.derived(result))
    return rows


def pooled_score(values, rain=False, threshold=None, variable=None):
    n, error, absolute, squared, sf, so, sff, soo, sfo = values[:9]
    n = int(n)
    if rain:
        offset = 9 + 3*GRID_THRESHOLDS.index(threshold)
        h, m, f = map(int, values[offset:offset+3]); chance = (h+m)*(h+f)/n
        def divide(a, b): return a/b if b > 0 else None
        return guifan.derived(dict(n=n,threshold=threshold,H=h,M=m,F=f,C=n-h-m-f,TS=divide(h,h+m+f),
                    POD=divide(h,h+m),FAR=divide(f,h+f),ETS=divide(h-chance,h+m+f-chance),
                    **guifan.extras(variable or 'rain',values[9+3*len(GRID_THRESHOLDS):],threshold)))
    vf, vo = max(0.,sff-sf*sf/n), max(0.,soo-so*so/n)
    cc = max(-1.,min(1.,(sfo-sf*so/n)/math.sqrt(vf*vo))) if n > 1 and vf > 1e-8 and vo > 1e-8 else None
    return guifan.derived(dict(n=n,ME=error/n,MAE=absolute/n,RMSE=math.sqrt(max(0.,squared/n)),CC=cc,
                              **guifan.extras(variable or '',values[9+3*len(GRID_THRESHOLDS):])))


def _valid_sum_order(db,columns,where,params):
    """Do not assume ANALYZE/index changes leave the legacy SUM order intact."""
    sums=['COUNT(*)','SUM(fc-ob)','SUM(ABS(fc-ob))','SUM((fc-ob)*(fc-ob))',
          'SUM(fc)','SUM(ob)','SUM(fc*fc)','SUM(ob*ob)','SUM(fc*ob)',*guifan.sql_expressions()]
    keys=','.join(columns)
    plan=db.execute('EXPLAIN QUERY PLAN SELECT '+keys+','+','.join(sums)+' FROM '+_pair_valid(db)+' WHERE '+where+' GROUP BY '+keys,params)
    return any('pair_valid' in row[3] for row in plan)


NMIC_WINDOW_LAG = timedelta(days=3)


def score_window_ends(end, nmic_end):
    """各窗口的截止时刻（键为天数）。

    2026-11-15 用户要求「近1天改成有资料的时间」：国家站资料（nmic）比本轮评分时刻晚到时，
    近24小时改为截止到国家站最新时次后 55 分钟（和每轮 xx:55 截止同一习惯，含最新时次、不含 24 小时前那一次），
    所有站用同一段时间；国家站资料超过 3 天没更新就退回按本轮评分时刻，免得一直停在旧日期。
    近3/7/15天不变。
    """
    ends = {days: end for days in DAY_WINDOWS}
    if nmic_end and 1 in ends:
        latest = stamp(nmic_end)
        if latest < end and end - latest <= NMIC_WINDOW_LAG:
            ends[1] = min(end, latest + timedelta(minutes=55))
    return ends


def aggregates(db,directory,end,stations=None,ends=None):
    try:
        return _aggregate_run(db,directory,end,stations,ends)
    except sqlite3.DatabaseError as exc:
        if os.environ.get('STATIONS_AGGFAST','1')=='0': raise
        db.rollback()
        db.execute('DROP TABLE IF EXISTS temp.aggregate_series')
        db.execute('DROP TABLE IF EXISTS temp.aggregate_detail_batch')
        db.execute('DROP TABLE IF EXISTS temp.aggregate_detail_stale')
        db.execute('DROP TABLE IF EXISTS main.aggregate_detail_keys')
        main=next(r[2] for r in db.execute('PRAGMA database_list') if r[1]=='main')
        path=Path(main).parent/'aggregate-days.sqlite'
        print('AGGFAST_DAILY_REBUILD corrupt/failed state',str(exc),flush=True)
        path.unlink(missing_ok=True);Path(str(path)+'-journal').unlink(missing_ok=True)
        if db.execute("SELECT 1 FROM main.sqlite_master WHERE name='aggregate_dirty'").fetchone():
            with db: db.execute("INSERT OR IGNORE INTO aggregate_dirty VALUES ('*')")
        return _aggregate_run(db,directory,end,stations,ends)


def _aggregate_run(db, directory, end, stations=None, ends=None):
    """Incremental compensated daily states; STATIONS_AGGFAST=0 retains SQL.

    Keep unrounded sum compensations across merges for low-variance CC.
    The station n>1 rule remains distinct from grid n>=3.
    """
    import station_aggregate as fast
    db.execute('CREATE TEMP TABLE IF NOT EXISTS cmagd_extended (station TEXT PRIMARY KEY) WITHOUT ROWID')
    db.execute('DELETE FROM cmagd_extended')
    if stations is None:
        stations = read_json(Path(directory).parent.parent/'stations.json', {}).get('stations', [])
    db.executemany('INSERT INTO cmagd_extended VALUES (?)',
                   ((s['id'],) for s in stations if s.get('cmagd_extended')))
    db.commit()
    ends = dict(ends) if ends else {days: end for days in DAY_WINDOWS}
    enabled = os.environ.get('STATIONS_AGGFAST', '1') != '0'
    tracked = bool(db.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='aggregate_state'").fetchone())
    signature = aggregate_signature(directory,stations) if tracked else None
    state = dict(db.execute('SELECT key,value FROM aggregate_state')) if tracked else {}
    if tracked and not enabled:
        # A legacy run may replace detail_stats at a different endpoint. Its
        # old fast generation must not be reused when the switch is reenabled.
        with db:
            db.execute('DELETE FROM aggregate_state')
            db.execute("INSERT OR IGNORE INTO aggregate_dirty VALUES ('*')")
        state={}
    if enabled and tracked and aggregate_cached(db,directory,end,signature,state,ends): return
    groups = None
    if enabled:
        import station_aggregate_daily as daily
        main = next(r[2] for r in db.execute('PRAGMA database_list') if r[1]=='main')
        target = Path(directory)
        if target.name.startswith('.incomplete-'): target=target.with_name(target.name[len('.incomplete-'):])
        previous=Path(state.get('directory',''))
        dirty=[r[0] for r in db.execute('SELECT day FROM aggregate_dirty')] if tracked else ['*']
        context=dict(path=Path(main).parent/'aggregate-days.sqlite',signature=signature or aggregate_signature(directory,stations),
                     target=str(target.resolve()),previous=previous,dirty=dirty,ends=tuple(ends[d] for d in DAY_WINDOWS),
                     reusable=tracked and state.get('signature')==signature and (previous/'grid-map.sqlite').is_file())
        if tracked:
            # Keep the captured precise dirty list above. Persist * separately
            # so interrupted, partly committed detail output is never reusable.
            with db: db.execute("INSERT OR IGNORE INTO aggregate_dirty VALUES ('*')")
        groups=daily.Daily(db,end,context)
    # Missing public detail rows must be restored even when primitive fingerprints match.
    preserve = enabled and state.get('signature')==signature and signature is not None
    preserve = preserve and bool(db.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='detail_stats'").fetchone())
    if preserve:
        from station_detail_cache import Details
        preserve=Details.valid(db)
    try:
        _aggregates(db, directory, end, groups, preserve=preserve,
                    signature=signature if enabled else None, ends=ends)
        if enabled and tracked:
            # The pending path is only reusable after the atomic publish rename.
            target = Path(directory)
            if target.name.startswith('.incomplete-'): target = target.with_name(target.name[len('.incomplete-'):])
            with db:
                db.executemany('INSERT OR REPLACE INTO aggregate_state VALUES (?,?)',
                               [('signature',signature),('end',iso(end)),('directory',str(target.resolve())),
                                ('ends',json.dumps([iso(ends[d]) for d in DAY_WINDOWS]))])
                db.execute('DELETE FROM aggregate_dirty')
    finally:
        if groups is not None: groups.close()


def aggregate_signature(directory,stations=None):
    """Code, scoring rules and local station/region metadata invalidate together."""
    import hashlib
    root = Path(directory).parent.parent
    table = read_json(root/'stations.json', read_json(ROOT/'stations.json'))
    if table is not None:
        # Import counters/latest time are diagnostics, not station geography.
        # New NMIC files already dirty their changed pair days; invalidating
        # the entire day cache here would turn every delivery into a cold run.
        table = {k:v for k,v in table.items() if k != 'nmic'}
    files = ('station_score.py','station_aggregate.py','station_aggregate_daily.py','station_aggregate_native.c','station_detail_cache.py',
             'station_common.py','guifan_scores.py','station_api.py','nmic_provinces.json','nmic_obs.py','sz_district_obs.py')
    sites = None if stations is None else [{k:v for k,v in station.items() if k not in ('model_elevations','elevation_flags')} for station in stations]
    return digest([VERSION,guifan.REVISION,THRESHOLDS,GRID_THRESHOLDS,DAY_WINDOWS,VARIABLES,
                   guifan.THRESHOLDS,guifan.BOUNDS,guifan.COLUMNS,guifan.LABELS,guifan.sql_expressions(),
                   GRID_MODELS,GRID_DATASETS,XINJIANG,table,sites,
                   [(name,hashlib.sha256((ROOT/name).read_bytes()).hexdigest()) for name in files]])


def _pair_valid(db):
    # Offline comparison attaches a frozen input via a TEMP pairs view.
    main=db.execute("SELECT 1 FROM main.sqlite_master WHERE name='pairs'").fetchone()
    return ('pairs' if main else 'original.pairs')+' INDEXED BY pair_valid'


def aggregate_cached(db,directory,end,signature,state,ends=None):
    if state.get('signature')!=signature or db.execute('SELECT 1 FROM aggregate_dirty LIMIT 1').fetchone(): return False
    if not db.execute("SELECT 1 FROM main.sqlite_master WHERE name='detail_stats'").fetchone(): return False
    from station_detail_cache import Details
    if not Details.valid(db): return False
    old = Path(state.get('directory',''))
    names = [f'{prefix}-{d}.json' for d in DAY_WINDOWS for prefix in ('scores','station-scores')]
    names += [f'grid-rows-{v}.json.gz' for v in GRID_DATASETS]+['grid-map.sqlite','grid-datasets.json','grid-status.json']
    if not all((old/name).is_file() for name in names): return False
    ends = ends or {days: end for days in DAY_WINDOWS}
    try:
        old_ends = [stamp(v) for v in json.loads(state['ends'])]
        if len(old_ends)!=len(DAY_WINDOWS): return False
    except (KeyError,ValueError,TypeError): return False
    # 每个窗口单独核对：左端和右端挪动的区间里都没有配对，旧结果才能原样沿用。
    for days,previous in zip(DAY_WINDOWS,old_ends):
        current = ends[days]
        if previous == current: continue
        lo,hi=sorted((iso(previous-timedelta(days=days)),iso(current-timedelta(days=days))))
        if db.execute('SELECT 1 FROM '+_pair_valid(db)+' WHERE valid>=? AND valid<? LIMIT 1',(lo,hi)).fetchone(): return False
        lo,hi=sorted((iso(previous),iso(current)))
        if db.execute('SELECT 1 FROM '+_pair_valid(db)+' WHERE valid>? AND valid<=? LIMIT 1',(lo,hi)).fetchone(): return False
    with phase('aggregates.cache_copy'):
        for name in names:
            if (old/name).resolve() != (Path(directory)/name).resolve(): shutil.copyfile(old/name,Path(directory)/name)
    report=read_json(Path(directory)/'grid-status.json');report['elapsed_seconds']=0.
    atomic_json(Path(directory)/'grid-status.json',report)
    print('AGGFAST_CACHE_HIT',flush=True)
    return True


def _aggregates(db, directory, end, groups=None, preserve=False, signature=None, ends=None):
    """Six bounded-memory groupings per window, with shared rain counters.

    Detail groups are streamed to SQLite; rain counters pool exactly as integers.
    Daily groups retain unrounded compensated moments through rolling merges.
    Legacy SQL remains behind the explicit rollback switch. The station n>1 CC
    rule remains distinct from grid moment_score's n>=3 rule.
    """
    mapped=getattr(groups,'pooled',False);details=None
    with phase('aggregates.detail_setup'):
        db.execute('CREATE TABLE IF NOT EXISTS detail_stats (station TEXT,model TEXT,var TEXT,lead INTEGER,days INTEGER,threshold REAL,stats TEXT)')
        if not preserve and not mapped:
            db.execute('DROP INDEX IF EXISTS main.detail_identity')
            db.execute('DELETE FROM detail_stats')
        # COALESCE makes null thresholds and ordinary rows unique; grades retain
        # their six independent identities without changing the table's API schema.
        insert = 'INSERT INTO detail_stats VALUES (?,?,?,?,?,?,?)'
        if preserve:
            # Keep detail_lookup the best index for the API's existing unordered
            # SELECT. A station-first identity index could reorder grades before
            # thresholds, changing its response array despite identical scores.
            if not mapped:
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS detail_identity ON detail_stats(COALESCE(threshold,-1),COALESCE(json_extract(stats,'$.grade'),0),station,model,var,lead,days)")
                insert += ' ON CONFLICT DO UPDATE SET stats=excluded.stats WHERE stats IS NOT excluded.stats'
            db.execute('CREATE TEMP TABLE aggregate_series (station TEXT,model TEXT,var TEXT,lead INTEGER,days INTEGER,PRIMARY KEY(station,model,var,lead,days)) WITHOUT ROWID')
        if mapped:
            from station_detail_cache import Details
            details=Details(db,preserve)
            if not preserve: db.commit()
    def write(batch):
        if details is not None: details.write(batch)
        else: db.executemany(insert,batch)
    expressions = ['COUNT(*)','SUM(fc-ob)','SUM(ABS(fc-ob))','SUM((fc-ob)*(fc-ob))',
                   'SUM(fc)','SUM(ob)','SUM(fc*fc)','SUM(ob*ob)','SUM(fc*ob)']
    for threshold in GRID_THRESHOLDS:
        for condition in (f'fc>={threshold} AND ob>={threshold}',f'fc<{threshold} AND ob>={threshold}',f'fc>={threshold} AND ob<{threshold}'):
            expressions.append(f"SUM(var IN ('rain6','rain12','rain24') AND {condition})")
    expressions.extend(guifan.sql_expressions())
    ranges = [(0,240),(0,24),(25,72),(73,168),(169,240)]
    excluded = legacy_exclusions(db)
    south_only = 'station NOT IN (' + ','.join(repr(v) for v in sorted(excluded)) + ')'
    def add(pool, key, values):
        if key not in pool:
            pool[key] = list(values)
        else:
            total = pool[key]
            for i, value in enumerate(values):total[i] += value
    previous_days = None
    for days in DAY_WINDOWS:
        started = time.monotonic()
        before_write=(details.seconds,details.written) if details else None
        stop = ends[days] if ends else end
        cutoff = iso(stop - timedelta(days=days)); by_lead = []; by_station = []
        # 两个窗口右端不同（近24小时按国家站资料截止）时不能整块沿用上一个窗口。
        if groups is not None and previous_days is not None and (not ends or ends[previous_days]==stop) and not db.execute(
                'SELECT 1 FROM '+_pair_valid(db)+' WHERE valid>=? AND valid<? LIMIT 1',
                (cutoff,iso(stop-timedelta(days=previous_days)))).fetchone():
            select = ' SELECT station,model,var,lead,?,threshold,stats FROM detail_stats WHERE days=?'
            suffix = ' ON CONFLICT DO UPDATE SET stats=excluded.stats WHERE stats IS NOT excluded.stats' if preserve else ''
            if details is not None and preserve:
                cursor=db.execute(select,(days,previous_days))
                while True:
                    batch=cursor.fetchmany(1000)
                    if not batch: break
                    details.write(batch)
            else:
                db.execute('INSERT INTO detail_stats'+select+suffix,(days,previous_days))
            if preserve:
                db.execute('INSERT INTO aggregate_series SELECT station,model,var,lead,? FROM aggregate_series WHERE days=?',(days,previous_days))
            if getattr(groups,'pooled',False):
                groups.reuse_detail(DAY_WINDOWS.index(previous_days),DAY_WINDOWS.index(days))
            for prefix in ('scores','station-scores'):
                shutil.copyfile(directory/f'{prefix}-{previous_days}.json',directory/f'{prefix}-{days}.json')
            print('AGGFAST_WINDOW_REUSE',days,'from',previous_days,flush=True)
            if details: details.report(days,before_write,len(groups.context['dirty']))
            print('TIMING',f'aggregates.{days}',round(time.monotonic()-started,3),'seconds',flush=True)
            previous_days = days
            continue
        leads = {}; stations = {}; batch = [];detail_count=0
        query = 'SELECT station,model,var,lead,'+','.join(expressions)+' FROM '+_pair_valid(db)+' WHERE valid>=? AND valid<=? GROUP BY station,model,var,lead'
        detail_rows = db.execute(query,(cutoff,iso(stop))) if groups is None else groups.rows('detail',DAY_WINDOWS.index(days))
        for station,model,variable,lead,*values in detail_rows:
            detail_count+=1
            if preserve: db.execute('INSERT INTO aggregate_series VALUES (?,?,?,?,?)',(station,model,variable,lead,days))
            if preserve and getattr(groups,'pooled',False) and not groups.detail_changed:
                continue
            if variable in THRESHOLDS and not getattr(groups,'pooled',False):
                if station not in excluded:add(leads,(model,variable,lead),values)
                for lo,hi in ranges:
                    if lo <= lead <= hi:add(stations,(station,model,variable,lo,hi),values)
            for threshold in THRESHOLDS.get(variable,[None]):
                row = dict(station=station,model=model,var=variable,lead=lead,
                           **pooled_score(values,variable in THRESHOLDS,threshold,variable))
                batch.append((station,model,variable,lead,days,threshold,json.dumps(row,allow_nan=False,separators=(',',':'))))
            if variable=='rain24':
                for row in guifan.grade_rows(dict(station=station,model=model,var=variable,lead=lead,n=int(values[0])),values[-len(guifan.COLUMNS):]):
                    batch.append((station,model,variable,lead,days,None,json.dumps(guifan.derived(row),allow_nan=False,separators=(',',':'))))
            if len(batch) >= 1000:
                write(batch);batch.clear()
        write(batch)
        if getattr(groups,'pooled',False):
            window=DAY_WINDOWS.index(days)
            atomic_score_rows(directory/f'scores-{days}.json',groups.score_rows('lead',window))
            atomic_score_rows(directory/f'station-scores-{days}.json',groups.score_rows('station',window))
            previous_days=days
            details.report(days,before_write,len(groups.context['dirty']))
            print('TIMING',f'aggregates.{days}',round(time.monotonic()-started,3),'seconds',detail_count,'rows',len(groups.context['dirty']),'dirty_days',flush=True)
            continue
        where = "valid>=? AND valid<=? AND var NOT IN ('rain6','rain12','rain24')"
        params = (cutoff,iso(stop))
        if groups is None:
            continuous_leads = stats_sql(db,['model','var','lead'],where+' AND '+south_only,params)
            continuous_stations = {(lo,hi):stats_sql(db,['station','model','var'],
                where+' AND lead>=? AND lead<=?',(*params,lo,hi)) for lo,hi in ranges}
        else:
            window = DAY_WINDOWS.index(days)
            continuous_leads = [dict(model=m,var=v,lead=h,**pooled_score(values,variable=v))
                                for m,v,h,*values in groups.rows('lead',window)]
            if not _valid_sum_order(db,['model','var','lead'],where+' AND '+south_only,params):
                print('AGGFAST_SQL_ORDER_FALLBACK',days,'continuous leads',flush=True)
                continuous_leads=stats_sql(db,['model','var','lead'],where+' AND '+south_only,params)
            continuous_stations = {(lo,hi):[] for lo,hi in ranges}
            for st,m,v,lo,hi,*values in groups.rows('station',window):
                continuous_stations[lo,hi].append(dict(station=st,model=m,var=v,**pooled_score(values,variable=v)))
            for lo,hi in ranges:
                clause=where+' AND lead>=? AND lead<=?';arguments=(*params,lo,hi)
                if not _valid_sum_order(db,['station','model','var'],clause,arguments):
                    print('AGGFAST_SQL_ORDER_FALLBACK',days,'continuous stations',lo,hi,flush=True)
                    continuous_stations[lo,hi]=stats_sql(db,['station','model','var'],clause,arguments)
        for variable in VARIABLES:
            rain = variable in THRESHOLDS
            for threshold in THRESHOLDS.get(variable, [None]):
                if rain:
                    by_lead.extend(dict(model=m,var=v,lead=h,**pooled_score(values,True,threshold,variable))
                                   for (m,v,h),values in sorted(leads.items()) if v==variable)
                else:
                    by_lead.extend(r for r in continuous_leads if r['var']==variable)
                for lo,hi in ranges:
                    if rain:
                        by_station.extend(dict(station=s,model=m,var=v,lead_min=lo,lead_max=hi,
                                               **pooled_score(values,True,threshold,variable))
                                          for (s,m,v,l,h),values in sorted(stations.items()) if v==variable and (l,h)==(lo,hi))
                    else:
                        by_station.extend(dict(r,lead_min=lo,lead_max=hi) for r in continuous_stations[lo,hi] if r['var']==variable)
        for (m,v,h),values in sorted(leads.items()):
            if v=='rain24':by_lead.extend(guifan.derived(r) for r in guifan.grade_rows(dict(model=m,var=v,lead=h,n=int(values[0])),values[-len(guifan.COLUMNS):]))
        for (st,m,v,lo,hi),values in sorted(stations.items()):
            if v=='rain24':by_station.extend(guifan.derived(r) for r in guifan.grade_rows(dict(station=st,model=m,var=v,lead_min=lo,lead_max=hi,n=int(values[0])),values[-len(guifan.COLUMNS):]))
        atomic_score_rows(directory / f'scores-{days}.json', by_lead)
        atomic_score_rows(directory / f'station-scores-{days}.json', by_station)
        previous_days = days
        print('TIMING',f'aggregates.{days}',round(time.monotonic()-started,3),'seconds',flush=True)
    with phase('aggregates.detail_cleanup'):
        if preserve:
            if details: details.cleanup()
            else: db.execute('DELETE FROM detail_stats AS d WHERE NOT EXISTS (SELECT 1 FROM aggregate_series s WHERE s.station=d.station AND s.model=d.model AND s.var=d.var AND s.lead=d.lead AND s.days=d.days)')
            db.execute('DROP TABLE aggregate_series')
    with phase('aggregates.detail_finalize'):
        with phase('aggregates.detail_lookup'):
            db.execute('CREATE INDEX IF NOT EXISTS detail_lookup ON detail_stats(station,model,var,lead,days)')
        if details:
            with phase('aggregates.detail_keys'): details.finish()
        db.commit()
    if groups is not None and not getattr(groups,'pooled',False): groups.close()
    with phase('grid_aggregates'):
        if getattr(groups,'pooled',False):
            from station_aggregate_daily import Grid
            grid_aggregates(db,directory,end,groups=Grid(groups))
        elif signature is None: grid_aggregates(db, directory, end)
        else: grid_aggregates(db, directory, end, signature=signature)


def grid_aggregates(db, directory, end, signature=None, groups=None):
    import station_aggregate as fast
    started = time.monotonic()
    if groups is None and os.environ.get('STATIONS_AGGFAST', '1') != '0':
        try:
            context = None
            if db.execute("SELECT 1 FROM main.sqlite_master WHERE name='aggregate_state'").fetchone():
                state = dict(db.execute('SELECT key,value FROM aggregate_state'))
                signature = signature or aggregate_signature(directory)
                dirty = [r[0] for r in db.execute('SELECT day FROM aggregate_dirty')]
                main = next(r[2] for r in db.execute('PRAGMA database_list') if r[1]=='main')
                target = Path(directory)
                if target.name.startswith('.incomplete-'): target=target.with_name(target.name[len('.incomplete-'):])
                previous = Path(state.get('directory',''))
                context = dict(path=Path(main).parent/'aggregate-space.sqlite',signature=signature,
                               target=str(target.resolve()),previous=previous,dirty=dirty,
                               reusable=state.get('signature')==signature and '*' not in dirty and (previous/'grid-map.sqlite').is_file())
                if Path(main).name=='pairs.sqlite': context=None  # --grid-only on an immutable published input
            groups = fast.grid(db,context)
        except fast.Unavailable as exc: print('AGGFAST_GRID_FALLBACK', str(exc), flush=True)
    try:
        report = _grid_aggregates(db, directory, end, groups)
        report['elapsed_seconds'] = round(time.monotonic()-started,3)
        atomic_json(Path(directory)/'grid-status.json',report)
        return report
    finally:
        if groups is not None: groups.close()


def _grid_aggregates(db, directory, end, groups=None):
    """Two bounded-memory GROUP BY passes, independent of the legacy day windows.

    One pass produces spatial scores per forecast; the other produces additive
    station/day/lead statistics. Rain thresholds share each scan. Neither pass
    materializes the station pairs in Python or adds indexes to the large DB.
    """
    started = time.monotonic()
    south_only = 'station NOT IN (' + ','.join(repr(v) for v in sorted(legacy_exclusions(db))) + ')'
    directory = Path(directory)
    expressions = ['COUNT(*)', 'SUM(fc-ob)', 'SUM(ABS(fc-ob))', 'SUM((fc-ob)*(fc-ob))',
                   'SUM(fc)', 'SUM(ob)', 'SUM(fc*fc)', 'SUM(ob*ob)', 'SUM(fc*ob)']
    count_columns = []
    for i, threshold in enumerate(GRID_THRESHOLDS):
        for key, condition in [('h', f'fc>={threshold} AND ob>={threshold}'),
                               ('m', f'fc<{threshold} AND ob>={threshold}'),
                               ('f', f'fc>={threshold} AND ob<{threshold}')]:
            count_columns.append(f'{key}{i}')
            expressions.append(f"SUM(var IN ('rain6','rain12','rain24') AND {condition})")
    count_columns.extend(guifan.COLUMNS)
    expressions.extend(guifan.sql_expressions())
    sums = ','.join(expressions)
    where = 'var IN (' + ','.join('?' for _ in GRID_DATASETS) + ') AND model IN (' + ','.join('?' for _ in GRID_MODELS) + ')'
    where += ' AND fc IS NOT NULL AND ob IS NOT NULL AND ABS(fc)<1e308 AND ABS(ob)<1e308'
    args = (*GRID_DATASETS, *GRID_MODELS)
    created = now().timestamp()
    trackers = {v: dict(models=set(), leads=set(), times=set(), rows=0) for v in GRID_DATASETS}
    temporary_files = {}
    with ExitStack() as stack:
        handles = {}
        for variable in GRID_DATASETS:
            path = directory / f'grid-rows-{variable}.json.gz'
            temporary_files[variable] = path.with_name(path.name + '.next')
            handles[variable] = stack.enter_context(gzip.open(temporary_files[variable], 'wt', encoding='utf-8', compresslevel=6))
            handles[variable].write('[')
        query = f'SELECT model,run,lead,valid,var,{sums} FROM pairs WHERE {where} AND {south_only} GROUP BY model,run,lead,valid,var'
        space_rows = db.execute(query,args) if groups is None else groups.rows('space')
        for model, run, lead, valid, variable, *values in space_rows:
            base = dict(dataset=GRID_DATASETS[variable], model=GRID_MODELS[model], run=bjt(run),
                        lead=lead, valid=bjt(valid), created=created, updated=created)
            track = trackers[variable]
            track['models'].add(base['model']); track['leads'].add(lead); track['times'].add(base['valid'])
            if variable not in THRESHOLDS:
                rows = [guifan.derived(dict(base, threshold=None, **moment_score(values[:9]),**guifan.extras(variable,values[-len(guifan.COLUMNS):])))]
            else:
                rows = []
                for threshold in THRESHOLDS[variable]:
                    offset = 9 + 3*GRID_THRESHOLDS.index(threshold)
                    h, m, f = values[offset:offset+3]
                    rows.append(dict(base, threshold=threshold, n=values[0], H=h, M=m, F=f,**guifan.extras(variable,values[-len(guifan.COLUMNS):],threshold)))
                if variable=='rain24':rows.extend(guifan.grade_rows(dict(base,n=values[0]),values[-len(guifan.COLUMNS):]))
            for row in rows:
                if track['rows']: handles[variable].write(',')
                handles[variable].write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')))
                track['rows'] += 1
        for handle in handles.values(): handle.write(']')
    print('TIMING grid_aggregates.rows',round(time.monotonic()-started,3),'seconds',flush=True)
    # The correction started with Xinjiang only, where no southern row announces it to the page.
    trackers['wind']['models'].add(GRID_MODELS['wn3ml'])
    datasets = []
    for variable, track in trackers.items():
        path = directory / f'grid-rows-{variable}.json.gz'; temporary_files[variable].replace(path)
        print('GRID_ROWS', variable, track['rows'], 'rows', path.stat().st_size, 'gzip bytes', flush=True)
        if not track['rows']: continue
        times = sorted(track['times'])
        surface = variable in ('msl', 'wind')
        fields = dict(element='wind10' if variable=='wind' else 'mslp', label=VARIABLES[variable][0],
                      unit=VARIABLES[variable][1], height_m=10 if variable=='wind' else None) if surface else {}
        datasets.append(dict(id=GRID_DATASETS[variable], kind='地面' if surface else '气温' if variable=='t' else '降水',
            source='station', var=variable, accumulation=int(variable[4:]) if variable in THRESHOLDS else None,
            has_n=True, models=[m for m in GRID_MODELS.values() if m in track['models']],
            supported_models=list(GRID_MODELS.values()), start=times[0], end=times[-1],
            leads=sorted(track['leads']), times=times, thresholds=THRESHOLDS.get(variable, []),
            **guifan.metadata(variable,int(variable[4:]) if variable in THRESHOLDS else None),
            reference='华南站点实况', region=REGION, updated=bjt(iso(now())),
            source_note='站点实况：OGIMET SYNOP / NOAA GHCNh / IEM METAR / 国家站三小时资料（2 分钟平均风）；无海拔订正。共同有效时间不保证各模式配对完全相同的站点。',
            row_count=track['rows'], gzip_bytes=path.stat().st_size, **fields))
    # A separate compact SQLite file keeps HTTP connections and their caches small.
    temporary = directory / 'grid-map.sqlite.next'
    map_started = time.monotonic()
    temporary.unlink(missing_ok=True)
    Path(str(temporary)+'-journal').unlink(missing_ok=True)
    base = getattr(groups,'daily_base',None)
    # Previous maps are committed immutable products. Copy their bytes before
    # opening SQLite; page-by-page backup adds per-page I/O as history grows.
    if base is not None: shutil.copyfile(base,temporary)
    daily = sqlite3.connect(temporary)
    try:
        daily.execute('PRAGMA cache_size=-16000'); daily.execute('PRAGMA temp_store=FILE')
        count = 0
        if base is not None:
            count = read_json(base.parent/'grid-status.json')['map_rows']
            for day in groups.dirty_days:
                for model in GRID_MODELS:
                    for variable in GRID_DATASETS:
                        count -= daily.execute('DELETE FROM daily WHERE model=? AND var=? AND day=?',(model,variable,day)).rowcount
        else:
            daily.execute('CREATE TABLE daily (model TEXT,var TEXT,day TEXT,lead INTEGER,station TEXT,' +
                          ','.join(f'{c} REAL' for c in (*MOMENTS, *count_columns)) +
                          ',PRIMARY KEY(model,var,day,lead,station)) WITHOUT ROWID')
        query = f"SELECT model,var,date(valid,'+8 hours'),lead,station,{sums} FROM pairs WHERE {where} GROUP BY model,var,date(valid,'+8 hours'),lead,station"
        cursor = db.execute(query, args) if groups is None else iter(groups.rows('daily'))
        insert = 'INSERT INTO daily VALUES (' + ','.join('?' for _ in range(5+len(expressions))) + ')'
        while True:
            if groups is None: batch = cursor.fetchmany(2000)
            else:
                from itertools import islice
                batch = list(islice(cursor,2000))
            if not batch: break
            daily.executemany(insert, batch); count += len(batch)
        daily.commit()
    finally:
        daily.close()
    temporary.replace(directory / 'grid-map.sqlite')
    print('TIMING grid_aggregates.map',round(time.monotonic()-map_started,3),'seconds',flush=True)
    atomic_json(directory / 'grid-datasets.json', datasets)
    report = dict(elapsed_seconds=round(time.monotonic()-started, 3), datasets=datasets,
                  map_rows=count, map_bytes=(directory/'grid-map.sqlite').stat().st_size)
    atomic_json(directory/'grid-status.json', report)
    print('GRID_AGGREGATES', json.dumps({k:v for k,v in report.items() if k!='datasets'}), flush=True)
    return report


def export_days(db, root, days):
    directory = root / 'pairs'; directory.mkdir(exist_ok=True)
    for day in sorted(days):
        stop = iso(stamp(day + 'T00:00:00Z') + timedelta(days=1))
        path = directory / (day + '.csv.gz'); temporary = path.with_suffix('.next')
        with gzip.open(temporary, 'wt', newline='', compresslevel=1) as handle:
            writer = csv.writer(handle)
            writer.writerow(['model','run','lead','valid','station','var','fc','ob','source','trace','report_time'])
            for record in db.execute('SELECT * FROM '+_pair_valid(db)+' WHERE valid>=? AND valid<? ORDER BY model,run,lead,station,var', (day+'T00:00:00Z',stop)):
                writer.writerow(record)
        with temporary.open('rb') as handle:os.fsync(handle.fileno())
        temporary.replace(path)
    sync_directory(directory)


def common_report(db, end):
    """Fair D1–D5 comparison: identical run/lead/station/observation for all 3."""
    records = defaultdict(lambda: [[], []])
    # 2026-11-15 用户同意改回提速前写法：只要 ec01 一个模式，按主键 model=? 定位最快；
    # 走 pair_valid 要把 30 天全部模式扫一遍（实测冷库 108 秒→602 秒，热库 4.4→43.6 秒，结果相同）
    query = '''SELECT a.lead,a.var,a.fc,b.fc,c.fc,a.ob FROM pairs a
        JOIN pairs b ON b.model='ecmwf' AND b.run=a.run AND b.lead=a.lead AND b.station=a.station AND b.var=a.var
        JOIN pairs c ON c.model='gfs' AND c.run=a.run AND c.lead=a.lead AND c.station=a.station AND c.var=a.var
        WHERE a.model='ec01' AND a.var IN ('t','rain6') AND a.lead>0 AND a.lead<=120
        AND a.valid>=? AND a.valid<=? AND ABS(a.ob-b.ob)<0.000001 AND ABS(a.ob-c.ob)<0.000001
        AND a.source=b.source AND a.source=c.source AND a.'''+SOUTH_ONLY
    # 2026-11-16 共同样本报告随配对保留缩短，从近30天改为近15天。
    for lead, variable, f1, f2, f3, ob in db.execute(query, (iso(end-timedelta(days=COMMON_DAYS)),iso(end))):
        day = (lead - 1) // 24 + 1
        for model, fc in [('ec01',f1),('ecmwf',f2),('gfs',f3)]:
            records[model, variable, day][0].append(fc); records[model, variable, day][1].append(ob)
    result = []
    for (model, variable, day), (fc, ob) in sorted(records.items()):
        if variable == 't':
            result.append(dict(model=model, var=variable, day=day, **continuous(fc, ob)))
        else:
            for threshold in (.1, 10):
                result.append(dict(model=model, var=variable, day=day, threshold=threshold, **contingency(fc,ob,threshold)))
    return result


def shenzhen_sanity(observations, root):
    import pandas as pd
    app = Path(os.environ.get('STATIONS_GRID_ROOT', '/home/yjzx/verification-platform/ec-gfs-server'))
    table = read_json(root / 'stations.json', read_json(ROOT / 'stations.json'))
    station = next((s for s in table['stations'] if s['id'] == '59493'), None)
    if station is None:
        return dict(n=0, reason='59493 not in frozen station metadata')
    cache_path = root/'sanity-cache.json'
    try:cached = read_json(cache_path,{})
    except (OSError,ValueError):cached = {}
    identity = [str(app),file_signature(app/'data/grid.xlsx'),station['id'],station['lon'],station['lat']]
    if cached.get('identity') == identity:
        location = cached['location']
    else:
        cached = {}
        grid = pd.read_excel(app/'data/grid.xlsx',sheet_name='数据集1')
        x = (pd.to_numeric(grid['格网左下角经度（度）'])+pd.to_numeric(grid['格网右上角经度（度）']))/2
        y = (pd.to_numeric(grid['格网左下角纬度（度）'])+pd.to_numeric(grid['格网右上角纬度（度）']))/2
        index = ((x-station['lon'])**2+(y-station['lat'])**2).idxmin()
        location = dict(grid_id=int(grid.loc[index,'格网ID（唯一）']),longitude=float(x[index]),latitude=float(y[index]))
    ident = location['grid_id']; fc = []; ob = []; times = []; points = {}
    for (sid, at, variable), item in sorted(observations.items()):
        if sid != '59493' or variable != 't':
            continue
        name = (stamp(at)+timedelta(hours=8)).strftime('%Y%m%d%H')+'.pkl'
        path = app / 'cache/OBSERVATION/grid' / name
        if not path.is_file():
            continue
        signature = file_signature(path)
        previous = cached.get('points',{}).get(name,{})
        if previous.get('signature') == signature:
            value = previous['value']
        else:
            data = pd.read_pickle(path)
            values = data.loc[data['GRIDID'] == ident,'T'].dropna()
            value = finite(values.iloc[0]) if len(values)==1 else None
            del data
        points[name] = dict(signature=signature,value=value)
        if value is not None:
            fc.append(item['value']); ob.append(value); times.append(at)
    atomic_json(cache_path,dict(identity=identity,location=location,points=points),durable=True)
    return dict(**continuous(fc,ob), station='59493', **location,
                first=min(times,default=None), last=max(times,default=None),
                sign='station minus grid', station_coordinate_source=station['coordinate_source'])


PREVIOUS_SNAPSHOT_MIN_FREE = 200 * 1024**3   # 盘剩余少于 200 GB 时连上一份快照也不留


def prune_snapshots(root):
    """Only completed generations count toward retention; current is protected."""
    snapshots = root/'snapshots'
    snapshots.mkdir(parents=True,exist_ok=True)
    current = read_json(root/'current.json',{}).get('generation')
    completed = []
    for directory in snapshots.iterdir():
        if not directory.is_dir() or directory.name == current:continue
        if directory.name.startswith('.incomplete-'):
            shutil.rmtree(directory)
        elif re.fullmatch(r'\d{8}T\d{6}(?:\d{6})?',directory.name):
            if (directory/'meta.json').is_file() and (directory/'pairs.sqlite').is_file():
                completed.append(directory)
            else:shutil.rmtree(directory)
    # Current plus the newest other completed generation, even if current was
    # explicitly rolled back to an older generation by an operator.
    # 2026-11-16 扩到 CMA-GD 全域后每份快照要近百 GB：盘剩余不够时上一份也删，只留当前这份。
    keep = 1 if shutil.disk_usage(snapshots).free >= PREVIOUS_SNAPSHOT_MIN_FREE else 0
    for directory in (sorted(completed)[:-1] if keep else sorted(completed)):shutil.rmtree(directory)


def migrate_trace(db, observations):
    """Only update flags on equal published observations; never re-pair historical values.

    Persist dirty export days in the same transaction. Repeating after a crash is safe.
    A separate score revision intentionally does not invalidate source/precision jobs.
    """
    db.execute('CREATE TABLE IF NOT EXISTS score_migrations (revision TEXT PRIMARY KEY)')
    if db.execute('SELECT 1 FROM score_migrations WHERE revision=?',(guifan.REVISION,)).fetchone():return
    with db:
        db.execute('CREATE TEMP TABLE trace_obs (station TEXT,valid TEXT,var TEXT,ob REAL,source TEXT,trace INTEGER,PRIMARY KEY(station,valid,var)) WITHOUT ROWID')
        db.executemany('INSERT INTO trace_obs VALUES (?,?,?,?,?,?)',
            ((st,at,v,o['value'],o['source'],int(bool(o.get('trace')) and o['value']<.1))
             for (st,at,v),o in observations.items() if v.startswith('rain')))
        match='t.station=p.station AND t.valid=p.valid AND t.var=p.var AND t.ob=p.ob AND t.source=p.source'
        db.execute('INSERT OR IGNORE INTO dirty_days SELECT DISTINCT substr(p.valid,1,10) FROM pairs p JOIN trace_obs t ON '+match+' WHERE t.trace IS NOT p.trace')
        db.execute('UPDATE pairs AS p SET trace=(SELECT t.trace FROM trace_obs t WHERE '+match+') WHERE EXISTS (SELECT 1 FROM trace_obs t WHERE '+match+' AND t.trace IS NOT p.trace)')
        db.execute('DROP TABLE trace_obs')
        db.execute('INSERT INTO score_migrations VALUES (?)',(guifan.REVISION,))


def refresh(root, end):
    with closing(database(root/'working.sqlite')) as db:
        return _refresh(root,end,db)


def _refresh(root, end, db):
    started = time.monotonic(); timings = {}
    table = read_json(root/'stations.json',read_json(ROOT/'stations.json'))
    if table is None:raise ValueError('Run build_stations.py / fetch sources first')
    import nmic_obs
    nmic_obs.add_stations(table, root, end)
    if len(table.get('stations', ())) >= 10:
        import sz_district_obs
        sz_district_obs.add_stations(table)
    # 2026-11-16 用户同意配对只留 16 天（页面最长近15天，留 1 天余量），为扩到 CMA-GD 全域省空间；原来 31 天。
    stations = table['stations']; cutoff = end-timedelta(days=PAIR_DAYS)
    with phase('observations',timings):
        observations,sources,agreement = load_observations(root,stations,end,table.get('inactive',()))
    migrate_trace(db, observations)
    print('QC-passed, source-prioritized observations:',len(observations),flush=True)
    by_time = defaultdict(list)
    for key,item in sorted(observations.items()):
        by_time[key[1]].append([key[0],key[2],item['value'],item['source'],item.get('trace',False),item.get('report_time')])
    obs_signatures = {at:digest(items) for at,items in by_time.items()}
    del by_time
    with phase('rolloff',timings), db:
        expired = {r[0][:10] for query,bound in (('valid<?',iso(cutoff)),('valid>?',iso(end)))
                   for r in db.execute('SELECT DISTINCT valid FROM pairs INDEXED BY pair_valid WHERE '+query,(bound,))}
        db.executemany('INSERT OR IGNORE INTO dirty_days VALUES (?)',((d,) for d in expired))
        db.execute('DELETE FROM pairs INDEXED BY pair_valid WHERE valid<?',(iso(cutoff),))
        db.execute('DELETE FROM pairs INDEXED BY pair_valid WHERE valid>?',(iso(end),))
        db.execute('DELETE FROM job_valid WHERE valid<? OR valid>?',(iso(cutoff),iso(end)))
        # 起报登记只留能产生配对的：配对 16 天 + 最长时效 10 天（原来 38 天）。
        for name in ('jobs','job_state'):
            db.execute(f'DELETE FROM {name} WHERE run<?',(iso(end-timedelta(days=RUN_DAYS)),))
    processed = skipped = added = full_runs = partial_runs = changed_valids = 0
    issues = []; elevations = {}
    previous_gen = read_json(root/'current.json',{}).get('generation','')
    previous_meta = read_json(root/'snapshots'/previous_gen/'meta.json',{}) if previous_gen else {}
    previous_sites = {(s['id'],s.get('lon'),s.get('lat')):s for s in previous_meta.get('stations',[])}
    for model in MODELS:
        elevations[model] = [previous_sites.get((s['id'],s.get('lon'),s.get('lat')),{}).
                             get('model_elevations',{}).get(model,float('nan')) for s in stations]
    first_model = None
    iterator = iter(forecast_runs(root,stations,end,True,timings))
    while True:
        before = time.monotonic()
        try:model,run,samples,source_sig,problems = next(iterator)
        except StopIteration:break
        if first_model is None:first_model = model
        key = 'forecast.'+model; timings[key] = timings.get(key,0)+time.monotonic()-before
        if run is None:
            issues.extend(problems);continue
        before = time.monotonic()
        changed,n,full,valids = score_job(db,model,run,samples,source_sig,obs_signatures,observations,stations,cutoff,end,elevations)
        key = 'score.'+model;timings[key] = timings.get(key,0)+time.monotonic()-before
        issues.extend(problems)
        if changed:
            processed += 1;added += n;full_runs += int(full);partial_runs += int(not full);changed_valids += valids
            print('SCORE',model,iso(run),n,'pairs','full' if full else 'partial',valids,'valids',flush=True)
        else:skipped += 1
        if (processed+skipped)%20==0:
            print('RESOURCE peak_rss_mib',round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1),flush=True)
    if first_model is not None:
        timings['forecast.'+first_model] -= timings.get('discover',0)
    for key,value in timings.items():
        if key.startswith(('forecast.','score.')):print('TIMING',key,round(value,3),'seconds',flush=True)
    # Pairing is finished. The all-station observation dictionary can contain
    # millions of rows; keep only the tiny Shenzhen sanity input while merging
    # aggregate states, rather than adding both memory peaks together.
    extended_ids = {s['id'] for s in stations if s.get('cmagd_extended')}
    cmagd_nmic_end = max((r['time'] for r in observations.values() if r['source']=='nmic'),default=None)
    nmic_end = max((r['time'] for k,r in observations.items() if r['source']=='nmic' and k[0] not in extended_ids),default=None)
    stations_with_data = len({key[0] for key in observations})
    sanity_observations = {k:v for k,v in observations.items() if k[0]=='59493' and k[2]=='t'}
    del observations
    prune_snapshots(root)
    print('Publishing aggregates and daily pair files',flush=True)
    generation = now().strftime('%Y%m%dT%H%M%S%f')
    directory = root/'snapshots'/('.incomplete-'+generation); directory.mkdir(parents=True)
    with phase('aggregates',timings):
        ends = score_window_ends(end, nmic_end)
        aggregates(db,directory,end,stations=stations,ends=ends)
    timings['grid_aggregates'] = read_json(directory/'grid-status.json',{}).get('elapsed_seconds',0)
    timings['window_aggregates'] = timings['aggregates']-timings['grid_aggregates']
    with phase('common_report',timings):
        common = common_report(db,end)
        atomic_json(directory/'common-report.json',common)
    try:
        with phase('shenzhen_sanity',timings):
            sanity = shenzhen_sanity(sanity_observations,root)
    except (OSError,KeyError,ValueError) as exc:
        sanity = dict(n=0,reason=str(exc))
    atomic_json(directory/'shenzhen-sanity.json',sanity)
    changed_days = {r[0] for r in db.execute('SELECT day FROM dirty_days')}
    with phase('export_days',timings):
        export_days(db,root,changed_days)
    coverage_started = time.monotonic()
    model_coverage = []
    for model in MODELS:
        n, first, last, ns = db.execute('SELECT COUNT(*),MIN(valid),MAX(valid),COUNT(DISTINCT station) FROM pairs WHERE model=?',(model,)).fetchone()
        variables = [r[0] for r in db.execute('SELECT DISTINCT var FROM pairs WHERE model=?',(model,))]
        leads = [r[0] for r in db.execute('SELECT DISTINCT lead FROM pairs WHERE model=? ORDER BY lead',(model,))]
        model_coverage.append(dict(id=model,label=MODELS[model],n=n,first=first,last=last,stations=ns,variables=variables,leads=leads,
                                   grid_degrees=.1 if model in ('ec01','wn3','wn3ml') else .25))
    timings['coverage'] = time.monotonic()-coverage_started
    print('TIMING coverage',round(timings['coverage'],3),'seconds',flush=True)
    for i,station in enumerate(stations):
        station['model_elevations'] = {m:float(a[i]) for m,a in elevations.items() if math.isfinite(float(a[i]))}
        station['elevation_flags'] = [m for m,z in station['model_elevations'].items()
                                      if station.get('elevation') is not None and abs(z-station['elevation'])>300]
    journal = read_json(root/'ogimet-journal.json',{})
    basemap = read_json(ROOT / 'station-basemap.json')
    meta = dict(schema=1,version=VERSION,score_revision=guifan.REVISION,generated=iso(now()),window_end=iso(end),window_days=PAIR_DAYS,
                nmic_end=nmic_end,cmagd_nmic_end=cmagd_nmic_end,window_ends={str(days):iso(value) for days,value in ends.items()},
                stations=stations,sources=sources,models=model_coverage,**basemap,
                stations_with_data=stations_with_data,
                unlocated_ogimet_ids=table.get('unknown_ogimet_ids',[]),
                variables=[dict(id=k,label=v[0],unit=v[1],thresholds=THRESHOLDS.get(k,[]),**guifan.metadata(k,int(k[4:]) if k in THRESHOLDS else None)) for k,v in VARIABLES.items()],
                station_counts=dict(synop=sum(s['type']=='synop' for s in stations),
                                    ghcnh2026=sum('ghcnh' in s['sources'] for s in stations),
                                    metar=sum(s['type']=='metar' for s in stations),
                                    szgrid=sum(s['type']=='szgrid' for s in stations),
                                    ogimet=sum('ogimet' in s['sources'] for s in stations)),
                ogimet=dict(requests=len(journal.get('requests',[])),stopped=journal.get('stopped')),
                issues_count=len(issues),issues=[dict(model=m,run=r,lead=l,reason=e[:200]) for m,r,l,e in issues[:30]],
                notice='按可用站点配对计分；跨模式样本数可能不同。无海拔订正；未知模式地形不作海拔差判断。时间显示为北京时间。')
    atomic_json(directory/'meta.json',meta)
    # Publish a self-contained immutable SQLite snapshot. Readers never see a
    # partially updated run and never open the writable working database.
    with phase('backup',timings):
        db.commit()
        with closing(sqlite3.connect(directory/'pairs.sqlite')) as published:
            db.backup(published)
    # All writer connections are closed before the directory becomes visible.
    # Working.sqlite is never hard-linked to an immutable snapshot.
    with phase('publish',timings):
        for product in directory.iterdir():
            if product.is_file():
                with product.open('rb') as handle:os.fsync(handle.fileno())
        sync_directory(directory)
        directory.rename(root/'snapshots'/generation)
        sync_directory(root/'snapshots')
        atomic_json(root/'current.json',dict(generation=generation),durable=True)
        atomic_json(root/'common-report.json',common)
        atomic_json(root/'shenzhen-sanity.json',sanity)
        with db:db.execute('DELETE FROM dirty_days')
        prune_snapshots(root)
    atomic_json(root/'validation-report.json',dict(common_pairs=common,shenzhen=sanity,agreement=agreement,
                station_counts=meta['station_counts'],sources=sources,issues=issues[:500]))
    result = dict(processed_runs=processed,unchanged_runs=skipped,inserted_pairs=added,
                  total_pairs=sum(m['n'] for m in model_coverage),issues=len(issues),generation=generation,
                  full_runs=full_runs,partial_runs=partial_runs,changed_valids=changed_valids,
                  timings={k:round(v,3) for k,v in timings.items()},
                  elapsed_seconds=round(time.monotonic()-started,3))
    atomic_json(root/'score-status.json',result)
    print(result,flush=True)
    return result


if __name__ == '__main__':
    import argparse
    from station_common import cache_root
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--grid-only', action='store_true', required=True,
                        help='Build only grid rows/map stats in the current local snapshot; never fetch or pair data')
    parser.parse_args()
    root = cache_root()
    generation = read_json(root/'current.json', {})['generation']
    import re
    if not re.fullmatch(r'\d{8}T\d{6}(?:\d{6})?', generation):
        parser.error('Invalid snapshot generation')
    directory = root/'snapshots'/generation
    # Even offline, the input snapshot is opened immutably; only the new products are written.
    with sqlite3.connect((directory/'pairs.sqlite').as_uri()+'?mode=ro&immutable=1', uri=True) as db:
        db.execute('PRAGMA cache_size=-24000'); db.execute('PRAGMA temp_store=FILE')
        grid_aggregates(db, directory, stamp(read_json(directory/'meta.json')['window_end']))
