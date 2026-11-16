#!/usr/bin/env python3
"""Fetch ECMWF ENS (0.25°, 50 perturbed members) and store ensemble means of surface fields.

ECMWF open data has no ready-made ensemble-mean product for these fields, so each frame range-requests
the 50 member messages of every chosen parameter (--params, default 2t,tp,100u,100v,10u,10v,msl,2d;
2t and tp are always included), crops them to the station box 97–123°E / 18–33°N and averages them.
Frames go to cache/native-backfill/ecens in the same layout as the GEFS frames
(<slug>_<run>_f<lead>_global1.json + .fields/*.npy): leads 0–144 h every 3 h (rain3h),
150–240 h every 6 h (rain6h); the 06/18 UTC runs end at 144 h. Finished frames are skipped, so the script can be re-run; frames of the old
108–120°E box (before 2026-11-02) are re-fetched. A per-run lock lets the timer and a backfill coexist.
Usage: fetch_ecmwf_ens.py [--days N] [--runs 0,6,12,18] [--params 2t,tp,100u,100v] [--workers N]
"""
import argparse, fcntl, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_gefs import session, get, crop, log, iso

OUT = Path(__file__).resolve().parent / 'cache' / 'native-backfill' / 'ecens'
BOUNDS = [97., 18., 123., 33.]   # station_common.BOX
OLD_BOUNDS = [104., 18., 121., 27.]   # finished frames stay valid; only new runs use the wider box
LON = np.arange(BOUNDS[0], BOUNDS[2] + 1e-6, .25)
LAT = np.arange(BOUNDS[1], BOUNDS[3] + 1e-6, .25)
SHAPE = (len(LAT), len(LON))
LEADS = list(range(0, 145, 3)) + list(range(150, 241, 6))
PREVIOUS = {lead: LEADS[i - 1] for i, lead in enumerate(LEADS) if i}
MEMBERS = 50
# GRIB shortName -> (stored field, scale, offset); stored as float32 means.
PARAMS = {'2t': ('surface_t', 1.0, -273.15), 'tp': ('tp_cum', 1000.0, 0.0),
          '100u': ('surface_u100', 1.0, 0.0), '100v': ('surface_v100', 1.0, 0.0),
          '10u': ('surface_u', 1.0, 0.0), '10v': ('surface_v', 1.0, 0.0),
          'msl': ('surface_msl', 0.01, 0.0), '2d': ('surface_td', 1.0, -273.15)}
SOURCES = ('https://storage.googleapis.com/ecmwf-open-data',
           'https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com')
PATH = '{run:%Y%m%d}/{run:%H}z/ifs/0p25/enfo/{run:%Y%m%d%H}0000-{lead}h-enfo-ef'

def stem(run, lead):
    return OUT / f'ecens_{run:%Y%m%d%H}_f{lead:03d}_global1'

def current(path):
    """A field file exists and has the current box shape."""
    try:
        return np.load(path, mmap_mode='r', allow_pickle=False).shape == SHAPE
    except (OSError, ValueError):
        return False

def member_mean(s, url, entries, param):
    """Mean over the 50 perturbed members of one parameter, cropped to the box."""
    chosen = sorted((e for e in entries if e.get('param') == param and e.get('type') == 'pf'),
                    key=lambda e: int(e['number']))
    if len(chosen) != MEMBERS:
        raise ValueError(f'{param}: {len(chosen)} members')
    total = None
    for e in chosen:
        raw = get(s, url, (e['_offset'], e['_offset'] + e['_length'] - 1))
        if raw is None:
            raise ValueError(f'{param} member {e["number"]} missing')
        a = crop(raw, LON, LAT).astype(np.float64)
        total = a if total is None else total + a
    return (total / MEMBERS).astype(np.float32)

def download(run, lead, s, params=tuple(PARAMS)):
    """Phase 1: the chosen ensemble-mean fields for one lead (tp_cum = mm since run start)."""
    folder = stem(run, lead).with_suffix('.fields')
    present = [n for n, *_ in PARAMS.values() if (folder / (n + '.npy')).is_file()]
    if any(not current(folder / (n + '.npy')) for n in present):
        # An old-box frame stops being a complete frame before its fields change.
        stem(run, lead).with_suffix('.json').unlink(missing_ok=True)
    missing = [p for p in params if not current(folder / (PARAMS[p][0] + '.npy'))]
    if not missing:
        return 'cached'
    for base in SOURCES:
        url = base + '/' + PATH.format(run=run, lead=lead)
        index = get(s, url + '.index')
        if index is not None:
            break
    else:
        return 'missing'
    entries = [json.loads(line) for line in index.decode().splitlines() if line.strip()]
    fields = {}
    for p in missing:
        name, scale, offset = PARAMS[p]
        fields[name] = member_mean(s, url + '.grib2', entries, p) * np.float32(scale) + np.float32(offset)
    folder.mkdir(parents=True, exist_ok=True)
    for name, a in fields.items():
        tmp = folder / (name + '.tmp.npy'); np.save(tmp, a); os.replace(tmp, folder / (name + '.npy'))
    return 'ok'

def finish(run, lead):
    """Phase 2: interval rain from consecutive tp_cum, then the frame json (json last = frame complete)."""
    path = stem(run, lead); meta = path.with_suffix('.json'); folder = path.with_suffix('.fields')
    if meta.is_file():
        try:
            if json.loads(meta.read_text()).get('bounds') in (BOUNDS, OLD_BOUNDS):
                return 'cached'
        except ValueError:
            pass
        meta.unlink()
    if not current(folder / 'surface_t.npy') or not current(folder / 'tp_cum.npy'):
        return 'incomplete'
    valid = run + timedelta(hours=lead)
    interval = None
    if lead:
        prior = stem(run, PREVIOUS[lead]).with_suffix('.fields') / 'tp_cum.npy'
        if not current(prior):
            return 'incomplete'
        interval = lead - PREVIOUS[lead]
        rain = np.load(folder / 'tp_cum.npy') - np.load(prior)
        if (rain < -0.05).any():   # GRIB packing noise on cumulative tp is ~0.01 mm
            raise ValueError(f'negative rain {run:%Y%m%d%H} f{lead:03d}')
        name = f'rain{interval}h'
        tmp = folder / (name + '.tmp.npy'); np.save(tmp, np.maximum(rain, 0).astype(np.float32))
        os.replace(tmp, folder / (name + '.npy'))
    doc = dict(schema=2, model='ECMWF-ENSEMBLES平均', run=iso(run), valid=iso(valid), lead=lead,
               storage='ecens-regional-v1', bounds=BOUNDS, native_grid_degrees=0.25,
               native_grid_shape=[len(LAT), len(LON)], periodic_longitude=False,
               lon=LON.tolist(), lat=LAT.tolist(), interval_hours=interval or (3 if lead <= 144 else 6),
               rain_start=iso(valid - timedelta(hours=interval)) if lead else None, rain_accum_start=0,
               members=MEMBERS, statistic='mean', **{'global': False},
               source='ECMWF open data ENS 0.25 (enfo pf 1-50), mean computed locally')
    tmp = meta.with_suffix('.tmp'); tmp.write_text(json.dumps(doc, ensure_ascii=False)); os.replace(tmp, meta)
    return 'ok'

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--days', type=int, default=3)
    ap.add_argument('--runs', default='0,12'); ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--params', default=','.join(PARAMS))
    a = ap.parse_args()
    params = tuple(dict.fromkeys(['2t', 'tp'] + a.params.split(',')))
    unknown = [p for p in params if p not in PARAMS]
    if unknown: ap.error('unknown --params: ' + ','.join(unknown))
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc); today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    runs = [today - timedelta(days=d) + timedelta(hours=int(h)) for d in range(a.days + 1) for h in a.runs.split(',')]
    runs = sorted(r for r in runs if r <= now - timedelta(hours=8))   # ENS 240 h is out ~8 h after the run
    jobs = [(r, L) for r in reversed(runs) for L in LEADS if r.hour in (0, 12) or L <= 144]
    if a.limit: jobs = jobs[:a.limit]
    log('runs', len(runs), 'frame jobs', len(jobs), 'params', ','.join(params))
    local = threading.local()
    def work(job):
        if not hasattr(local, 's'): local.s = session()
        return download(*job, local.s, params)
    counts = {}; done = {}; t0 = time.time(); n = 0
    # One run at a time (newest first), finished right after its downloads, so
    # complete runs appear while a long backfill is still going.
    by_run = {}
    for job in jobs: by_run.setdefault(job[0], []).append(job)
    with ThreadPoolExecutor(a.workers) as ex:
        for run, run_jobs in by_run.items():
            lock = open(OUT / f'.lock-{run:%Y%m%d%H}', 'w')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                log('RUN', f'{run:%Y%m%d%H}', 'busy in another fetch; skipped'); lock.close(); continue
            futures = {ex.submit(work, j): j for j in run_jobs}
            for f in as_completed(futures):
                n += 1
                try: r = f.result()
                except Exception as e: r = 'err ' + type(e).__name__
                counts[r] = counts.get(r, 0) + 1
                if r.startswith('err') and counts[r] <= 3: log('ERR', futures[f][0], futures[f][1], r)
                if n % 25 == 0 or n == len(jobs): log(f'{n}/{len(jobs)} {time.time()-t0:.0f}s', counts)
            for job in sorted(run_jobs):
                try: r = finish(*job)
                except Exception as e: r = 'err ' + type(e).__name__; log('ERR finish', job[0], job[1], e)
                done[r] = done.get(r, 0) + 1
            log('RUN', f'{run:%Y%m%d%H}', 'frames so far', done)
            lock.close()
    log('FINISHED download', counts, 'frames', done)

if __name__ == '__main__': main()
