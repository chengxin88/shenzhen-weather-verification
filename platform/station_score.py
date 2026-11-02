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
import numpy as np
from station_common import (ROOT, MODELS, THRESHOLDS, VARIABLES, VERSION, UTC, DAY_WINDOWS, REGION,
                            GRID_MODELS, GRID_DATASETS, GRID_THRESHOLDS, MOMENTS, bjt, moment_score,
                            atomic_json, digest, finite, iso, now, read_json, stamp, sync_directory)
from station_forecasts import accumulate, forecast_runs, file_signature, load_samples, save_samples, sample_state
from station_obs import load_observations


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
    intervals = {h: (float(s['rain_start']), s['rain']) for h, s in samples.items()
                 if 'rain' in s and 'rain_start' in s and math.isfinite(float(s['rain_start']))}
    for lead, sample in sorted(samples.items()):
        valid = run + timedelta(hours=lead)
        if not 0 <= lead <= 168 or not cutoff <= valid <= end:
            continue
        if valid_times is not None and iso(valid) not in valid_times:
            continue
        # Native 0.25-degree archives are admitted only at 3h nominal times.
        if model not in ('ec01', 'wn3') and (lead % 3 or valid.hour % 3):
            continue
        values = {v: sample[v] for v in ('t', 'td', 'msl', 'wind') if v in sample}
        for hours in (6, 24):
            total = accumulate(intervals, lead, hours)
            if total is not None:
                values['rain' + str(hours)] = total
        for variable, array in values.items():
            for i, station in enumerate(stations):
                observation = observations.get((station['id'], iso(valid), variable))
                fc = finite(array[i])
                if observation is None or fc is None:
                    continue
                if variable.startswith('rain') and fc < 0:
                    continue
                yield (model, iso(run), lead, iso(valid), station['id'], variable, fc,
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
    covering = ('valid','model','run','lead','station','var','fc','ob')
    if columns != covering:
        # Keep the old valid/primary-key iteration order exactly, adding only
        # the score inputs. Aggregate scans no longer fetch the large pair row
        # separately for every index entry. DDL rollback protects killed builds.
        with phase('index_migration'):
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('DROP INDEX pair_valid')
                db.execute('CREATE INDEX pair_valid ON pairs(valid,model,run,lead,station,var,fc,ob)')
                db.commit()
            except BaseException:
                db.rollback()
                raise
    return db


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
    rows = db.execute("SELECT lead,station,var,fc FROM pairs WHERE model=? AND run=? AND var IN ('t','td','msl','wind')",(model,run))
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
        rain_rows = list(db.execute("SELECT lead,station,var,fc FROM pairs WHERE model=? AND run=? AND var IN ('rain6','rain24')",(model,run)))
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
    run_text = iso(run)
    current = {iso(run+timedelta(hours=h)): obs_signatures.get(iso(run+timedelta(hours=h)))
               for h in samples if 0 <= h <= 168 and cutoff <= run+timedelta(hours=h) <= end}
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
        batch = list(pair_run(model, run, loaded, observations, stations, cutoff, end,
                              None if full else changed))
    with db:
        days = {at[:10] for at in changed}
        if full:
            days.update(r[0][:10] for r in db.execute('SELECT DISTINCT valid FROM pairs WHERE model=? AND run=?', (model,run_text)))
            db.execute('DELETE FROM pairs WHERE model=? AND run=?', (model,run_text))
        else:
            # valid is determined by run+lead: use the existing primary-key prefix.
            db.executemany('DELETE FROM pairs WHERE model=? AND run=? AND lead=?',
                           ((model,run_text,int((stamp(at)-run).total_seconds()/3600)) for at in changed))
        db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)', batch)
        db.executemany('INSERT OR IGNORE INTO dirty_days VALUES (?)', ((d,) for d in days))
        db.execute('INSERT OR REPLACE INTO job_state VALUES (?,?,?,?)', (model,run_text,VERSION,source_sig))
        db.execute('DELETE FROM job_valid WHERE model=? AND run=?', (model,run_text))
        db.executemany('INSERT INTO job_valid VALUES (?,?,?,?)', ((model,run_text,at,sig) for at,sig in current.items()))
        db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?)', (model,run_text,signature))
    return bool(full or changed), len(batch), full, len(changed)


def stats_sql(db, columns, where='1', params=(), rain=False, threshold=None):
    keys = ','.join(columns)
    if rain:
        expression = 'COUNT(*),SUM(fc>=? AND ob>=?),SUM(fc<? AND ob>=?),SUM(fc>=? AND ob<?)'
        arguments = (threshold,) * 6 + tuple(params)
    else:
        expression = 'COUNT(*),SUM(fc-ob),SUM(ABS(fc-ob)),SUM((fc-ob)*(fc-ob)),SUM(fc),SUM(ob),SUM(fc*fc),SUM(ob*ob),SUM(fc*ob)'
        arguments = tuple(params)
    query = f'SELECT {keys},{expression} FROM pairs WHERE {where} GROUP BY {keys}'
    rows = []
    for values in db.execute(query, arguments):
        result = dict(zip(columns, values[:len(columns)])); sums = values[len(columns):]; n = sums[0]
        if rain:
            _, h, m, f = sums; chance = (h + m) * (h + f) / n
            def divide(a, b): return a / b if b > 0 else None
            result.update(n=n, threshold=threshold, H=h, M=m, F=f, C=n-h-m-f,
                          TS=divide(h,h+m+f), POD=divide(h,h+m), FAR=divide(f,h+f),
                          ETS=divide(h-chance,h+m+f-chance))
        else:
            _, error, absolute, squared, sf, so, sff, soo, sfo = sums
            vf, vo = max(0., sff-sf*sf/n), max(0., soo-so*so/n)
            cc = max(-1., min(1., (sfo-sf*so/n)/math.sqrt(vf*vo))) if n > 1 and vf > 1e-8 and vo > 1e-8 else None
            result.update(n=n, ME=error/n, MAE=absolute/n, RMSE=math.sqrt(max(0., squared/n)), CC=cc)
        rows.append(result)
    return rows


def pooled_score(values, rain=False, threshold=None):
    n, error, absolute, squared, sf, so, sff, soo, sfo = values[:9]
    n = int(n)
    if rain:
        offset = 9 + 3*GRID_THRESHOLDS.index(threshold)
        h, m, f = map(int, values[offset:offset+3]); chance = (h+m)*(h+f)/n
        def divide(a, b): return a/b if b > 0 else None
        return dict(n=n,threshold=threshold,H=h,M=m,F=f,C=n-h-m-f,TS=divide(h,h+m+f),
                    POD=divide(h,h+m),FAR=divide(f,h+f),ETS=divide(h-chance,h+m+f-chance))
    vf, vo = max(0.,sff-sf*sf/n), max(0.,soo-so*so/n)
    cc = max(-1.,min(1.,(sfo-sf*so/n)/math.sqrt(vf*vo))) if n > 1 and vf > 1e-8 and vo > 1e-8 else None
    return dict(n=n,ME=error/n,MAE=absolute/n,RMSE=math.sqrt(max(0.,squared/n)),CC=cc)


def aggregates(db, directory, end):
    """Six bounded-memory groupings per window, with shared rain counters.

    Detail groups are streamed to SQLite; rain counters pool exactly as integers.
    Continuous pooled groups retain raw SUM order to avoid cancellation changing
    CC in low-variance pressure groups. The n>1 CC rule remains distinct from
    grid moment_score's n>=3 rule. This replaces 90 scans per window.
    """
    db.execute('CREATE TABLE IF NOT EXISTS detail_stats (station TEXT,model TEXT,var TEXT,lead INTEGER,days INTEGER,threshold REAL,stats TEXT)')
    db.execute('DELETE FROM detail_stats')
    expressions = ['COUNT(*)','SUM(fc-ob)','SUM(ABS(fc-ob))','SUM((fc-ob)*(fc-ob))',
                   'SUM(fc)','SUM(ob)','SUM(fc*fc)','SUM(ob*ob)','SUM(fc*ob)']
    for threshold in GRID_THRESHOLDS:
        for condition in (f'fc>={threshold} AND ob>={threshold}',f'fc<{threshold} AND ob>={threshold}',f'fc>={threshold} AND ob<{threshold}'):
            expressions.append(f"SUM(var IN ('rain6','rain24') AND {condition})")
    ranges = [(0,168),(0,24),(25,72),(73,168)]
    def add(pool, key, values):
        if key not in pool:
            pool[key] = list(values)
        else:
            total = pool[key]
            for i, value in enumerate(values):total[i] += value
    for days in DAY_WINDOWS:
        started = time.monotonic()
        cutoff = iso(end - timedelta(days=days)); by_lead = []; by_station = []
        leads = {}; stations = {}; batch = []
        query = 'SELECT station,model,var,lead,'+','.join(expressions)+' FROM pairs WHERE valid>=? AND valid<=? GROUP BY station,model,var,lead'
        for station,model,variable,lead,*values in db.execute(query,(cutoff,iso(end))):
            if variable in THRESHOLDS:
                add(leads,(model,variable,lead),values)
                for lo,hi in ranges:
                    if lo <= lead <= hi:add(stations,(station,model,variable,lo,hi),values)
            for threshold in THRESHOLDS.get(variable,[None]):
                row = dict(station=station,model=model,var=variable,lead=lead,
                           **pooled_score(values,variable in THRESHOLDS,threshold))
                batch.append((station,model,variable,lead,days,threshold,json.dumps(row,allow_nan=False,separators=(',',':'))))
            if len(batch) >= 1000:
                db.executemany('INSERT INTO detail_stats VALUES (?,?,?,?,?,?,?)',batch);batch.clear()
        db.executemany('INSERT INTO detail_stats VALUES (?,?,?,?,?,?,?)',batch)
        where = "valid>=? AND valid<=? AND var NOT IN ('rain6','rain24')"
        params = (cutoff,iso(end))
        continuous_leads = stats_sql(db,['model','var','lead'],where,params)
        continuous_stations = {(lo,hi):stats_sql(db,['station','model','var'],
            where+' AND lead>=? AND lead<=?',(*params,lo,hi)) for lo,hi in ranges}
        for variable in VARIABLES:
            rain = variable in THRESHOLDS
            for threshold in THRESHOLDS.get(variable, [None]):
                if rain:
                    by_lead.extend(dict(model=m,var=v,lead=h,**pooled_score(values,True,threshold))
                                   for (m,v,h),values in sorted(leads.items()) if v==variable)
                else:
                    by_lead.extend(r for r in continuous_leads if r['var']==variable)
                for lo,hi in ranges:
                    if rain:
                        by_station.extend(dict(station=s,model=m,var=v,lead_min=lo,lead_max=hi,
                                               **pooled_score(values,True,threshold))
                                          for (s,m,v,l,h),values in sorted(stations.items()) if v==variable and (l,h)==(lo,hi))
                    else:
                        by_station.extend(dict(r,lead_min=lo,lead_max=hi) for r in continuous_stations[lo,hi] if r['var']==variable)
        atomic_json(directory / f'scores-{days}.json', by_lead)
        atomic_json(directory / f'station-scores-{days}.json', by_station)
        print('TIMING',f'aggregates.{days}',round(time.monotonic()-started,3),'seconds',flush=True)
    db.execute('CREATE INDEX IF NOT EXISTS detail_lookup ON detail_stats(station,model,var,lead,days)')
    db.commit()
    with phase('grid_aggregates'):
        grid_aggregates(db, directory, end)


def grid_aggregates(db, directory, end):
    """Two bounded-memory GROUP BY passes, independent of the legacy day windows.

    One pass produces spatial scores per forecast; the other produces additive
    station/day/lead statistics. Rain thresholds share each scan. Neither pass
    materializes the station pairs in Python or adds indexes to the large DB.
    """
    started = time.monotonic()
    directory = Path(directory)
    expressions = ['COUNT(*)', 'SUM(fc-ob)', 'SUM(ABS(fc-ob))', 'SUM((fc-ob)*(fc-ob))',
                   'SUM(fc)', 'SUM(ob)', 'SUM(fc*fc)', 'SUM(ob*ob)', 'SUM(fc*ob)']
    count_columns = []
    for i, threshold in enumerate(GRID_THRESHOLDS):
        for key, condition in [('h', f'fc>={threshold} AND ob>={threshold}'),
                               ('m', f'fc<{threshold} AND ob>={threshold}'),
                               ('f', f'fc>={threshold} AND ob<{threshold}')]:
            count_columns.append(f'{key}{i}')
            expressions.append(f'SUM(var!=\'t\' AND {condition})')
    sums = ','.join(expressions)
    where = "var IN ('t','rain6','rain24') AND model IN (" + ','.join('?' for _ in GRID_MODELS) + ')'
    where += ' AND fc IS NOT NULL AND ob IS NOT NULL AND ABS(fc)<1e308 AND ABS(ob)<1e308'
    args = tuple(GRID_MODELS)
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
        query = f'SELECT model,run,lead,valid,var,{sums} FROM pairs WHERE {where} GROUP BY model,run,lead,valid,var'
        for model, run, lead, valid, variable, *values in db.execute(query, args):
            base = dict(dataset=GRID_DATASETS[variable], model=GRID_MODELS[model], run=bjt(run),
                        lead=lead, valid=bjt(valid), created=created, updated=created)
            track = trackers[variable]
            track['models'].add(base['model']); track['leads'].add(lead); track['times'].add(base['valid'])
            if variable == 't':
                rows = [dict(base, threshold=None, **moment_score(values[:9]))]
            else:
                rows = []
                for threshold in THRESHOLDS[variable]:
                    offset = 9 + 3*GRID_THRESHOLDS.index(threshold)
                    h, m, f = values[offset:offset+3]
                    rows.append(dict(base, threshold=threshold, n=values[0], H=h, M=m, F=f))
            for row in rows:
                if track['rows']: handles[variable].write(',')
                handles[variable].write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')))
                track['rows'] += 1
        for handle in handles.values(): handle.write(']')
    datasets = []
    for variable, track in trackers.items():
        path = directory / f'grid-rows-{variable}.json.gz'; temporary_files[variable].replace(path)
        print('GRID_ROWS', variable, track['rows'], 'rows', path.stat().st_size, 'gzip bytes', flush=True)
        if not track['rows']: continue
        times = sorted(track['times'])
        datasets.append(dict(id=GRID_DATASETS[variable], kind='气温' if variable=='t' else '降水',
            source='station', var=variable, accumulation=int(variable[4:]) if variable!='t' else None,
            has_n=True, models=[m for m in GRID_MODELS.values() if m in track['models']],
            supported_models=list(GRID_MODELS.values()), start=times[0], end=times[-1],
            leads=sorted(track['leads']), times=times, thresholds=THRESHOLDS.get(variable, []),
            reference='华南站点实况', region=REGION, updated=bjt(iso(now())),
            source_note='站点实况：OGIMET SYNOP / NOAA GHCNh / IEM METAR；无海拔订正。共同有效时间不保证各模式配对完全相同的站点。',
            row_count=track['rows'], gzip_bytes=path.stat().st_size))
    # A separate compact SQLite file keeps HTTP connections and their caches small.
    temporary = directory / 'grid-map.sqlite.next'
    temporary.unlink(missing_ok=True)
    daily = sqlite3.connect(temporary)
    try:
        daily.execute('PRAGMA cache_size=-16000'); daily.execute('PRAGMA temp_store=FILE')
        daily.execute('CREATE TABLE daily (model TEXT,var TEXT,day TEXT,lead INTEGER,station TEXT,' +
                      ','.join(f'{c} REAL' for c in (*MOMENTS, *count_columns)) +
                      ',PRIMARY KEY(model,var,day,lead,station)) WITHOUT ROWID')
        query = f"SELECT model,var,date(valid,'+8 hours'),lead,station,{sums} FROM pairs WHERE {where} GROUP BY model,var,date(valid,'+8 hours'),lead,station"
        cursor = db.execute(query, args)
        insert = 'INSERT INTO daily VALUES (' + ','.join('?' for _ in range(5+len(expressions))) + ')'
        count = 0
        while True:
            batch = cursor.fetchmany(2000)
            if not batch: break
            daily.executemany(insert, batch); count += len(batch)
        daily.commit()
    finally:
        daily.close()
    temporary.replace(directory / 'grid-map.sqlite')
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
            for record in db.execute('SELECT * FROM pairs WHERE valid>=? AND valid<? ORDER BY model,run,lead,station,var', (day+'T00:00:00Z',stop)):
                writer.writerow(record)
        with temporary.open('rb') as handle:os.fsync(handle.fileno())
        temporary.replace(path)
    sync_directory(directory)


def common_report(db, end):
    """Fair D1–D5 comparison: identical run/lead/station/observation for all 3."""
    records = defaultdict(lambda: [[], []])
    query = '''SELECT a.lead,a.var,a.fc,b.fc,c.fc,a.ob FROM pairs a
        JOIN pairs b ON b.model='ecmwf' AND b.run=a.run AND b.lead=a.lead AND b.station=a.station AND b.var=a.var
        JOIN pairs c ON c.model='gfs' AND c.run=a.run AND c.lead=a.lead AND c.station=a.station AND c.var=a.var
        WHERE a.model='ec01' AND a.var IN ('t','rain6') AND a.lead>0 AND a.lead<=120
        AND a.valid>=? AND a.valid<=? AND ABS(a.ob-b.ob)<0.000001 AND ABS(a.ob-c.ob)<0.000001
        AND a.source=b.source AND a.source=c.source'''
    for lead, variable, f1, f2, f3, ob in db.execute(query, (iso(end-timedelta(days=30)),iso(end))):
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
    app = Path(os.environ.get('STATIONS_GRID_ROOT', '/home/chengyang/ec-gfs-server'))
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
    for directory in sorted(completed)[:-1]:shutil.rmtree(directory)


def refresh(root, end):
    with closing(database(root/'working.sqlite')) as db:
        return _refresh(root,end,db)


def _refresh(root, end, db):
    started = time.monotonic(); timings = {}
    table = read_json(root/'stations.json',read_json(ROOT/'stations.json'))
    if table is None:raise ValueError('Run build_stations.py / fetch sources first')
    stations = table['stations']; cutoff = end-timedelta(days=31)
    with phase('observations',timings):
        observations,sources,agreement = load_observations(root,stations,end)
    print('QC-passed, source-prioritized observations:',len(observations),flush=True)
    by_time = defaultdict(list)
    for key,item in sorted(observations.items()):
        by_time[key[1]].append([key[0],key[2],item['value'],item['source'],item.get('trace',False),item.get('report_time')])
    obs_signatures = {at:digest(items) for at,items in by_time.items()}
    del by_time
    with phase('rolloff',timings), db:
        expired = {r[0][:10] for r in db.execute('SELECT DISTINCT valid FROM pairs WHERE valid<? OR valid>?',(iso(cutoff),iso(end)))}
        db.executemany('INSERT OR IGNORE INTO dirty_days VALUES (?)',((d,) for d in expired))
        db.execute('DELETE FROM pairs WHERE valid<? OR valid>?',(iso(cutoff),iso(end)))
        db.execute('DELETE FROM job_valid WHERE valid<? OR valid>?',(iso(cutoff),iso(end)))
        # Discovered runs are bounded to 38 days as before.
        for name in ('jobs','job_state'):
            db.execute(f'DELETE FROM {name} WHERE run<?',(iso(end-timedelta(days=38)),))
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
    prune_snapshots(root)
    print('Publishing aggregates and daily pair files',flush=True)
    generation = now().strftime('%Y%m%dT%H%M%S%f')
    directory = root/'snapshots'/('.incomplete-'+generation); directory.mkdir(parents=True)
    with phase('aggregates',timings):
        aggregates(db,directory,end)
    timings['grid_aggregates'] = read_json(directory/'grid-status.json',{}).get('elapsed_seconds',0)
    timings['window_aggregates'] = timings['aggregates']-timings['grid_aggregates']
    with phase('common_report',timings):
        common = common_report(db,end)
        atomic_json(directory/'common-report.json',common)
    try:
        with phase('shenzhen_sanity',timings):
            sanity = shenzhen_sanity(observations,root)
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
                                   grid_degrees=.1 if model in ('ec01','wn3') else .25))
    timings['coverage'] = time.monotonic()-coverage_started
    print('TIMING coverage',round(timings['coverage'],3),'seconds',flush=True)
    for i,station in enumerate(stations):
        station['model_elevations'] = {m:float(a[i]) for m,a in elevations.items() if math.isfinite(float(a[i]))}
        station['elevation_flags'] = [m for m,z in station['model_elevations'].items()
                                      if station.get('elevation') is not None and abs(z-station['elevation'])>300]
    journal = read_json(root/'ogimet-journal.json',{})
    basemap = read_json(ROOT / 'station-basemap.json')
    meta = dict(schema=1,version=VERSION,generated=iso(now()),window_end=iso(end),window_days=31,
                stations=stations,sources=sources,models=model_coverage,**basemap,
                stations_with_data=len({key[0] for key in observations}),
                unlocated_ogimet_ids=table.get('unknown_ogimet_ids',[]),
                variables=[dict(id=k,label=v[0],unit=v[1],thresholds=THRESHOLDS.get(k,[])) for k,v in VARIABLES.items()],
                station_counts=dict(synop=sum(s['type']=='synop' for s in stations),
                                    ghcnh2026=sum('ghcnh' in s['sources'] for s in stations),
                                    metar=sum(s['type']=='metar' for s in stations),
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
