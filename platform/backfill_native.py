#!/usr/bin/env python3
"""Backfill historical EC-IFS / GFS 0.25° fields (Shenzhen box only) for TEST native scoring.

Downloads only tp (accumulated precip) and 2 m temperature by HTTP Range via the temporary
direct mihomo (127.0.0.1:17960), crops lon 110–119E / lat 19–26N (same box as
native_archive.load_global), stores raw crops in cache/native-backfill/raw/<slug>/<run>.jsonl,
(after `import` reuses ~/hist-forecast tp crops), then `assemble` writes region-v4-style schema-2 frames:
  cache/native-backfill/<slug>/<slug>_<YYYYMMDDHH>_f<LLL>.json
with rain3h = acc(L) - acc(L-3) (mm) and layers.surface.t (°C).
Usage: backfill_native.py download ec|gfs FIRST_RUN LAST_RUN   (runs as YYYYMMDDHH UTC)
       backfill_native.py import
       backfill_native.py assemble
"""
import os, sys, re, json, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np, requests, eccodes

ROOT = Path(__file__).resolve().parent / 'cache' / 'native-backfill'
RAW = ROOT / 'raw'
PROXY = os.environ.get('BF_PROXY', 'http://127.0.0.1:17960')
# Shenzhen grid spans 113.73–114.65E / 22.40–22.86N; this box covers nearest and bilinear
# and matches ~/hist-forecast so its September tp crops can be reused.
LON = np.round(np.arange(113.5, 114.75 + 1e-6, 0.25), 2)
LAT = np.round(np.arange(22.25, 23.0 + 1e-6, 0.25), 2)
HIST = Path.home() / 'hist-forecast' / '2026-09'
LEADS = (12, 24, 36, 48, 60, 72)
NOT_BEFORE = datetime(2026, 8, 31, 18)   # earliest valid UTC needed (= 2026-09-01 02 BJT)
SLUG = {'ec': 'ecmwf', 'gfs': 'gfs'}
NAME = {'ecmwf': 'ECMWF-IFS', 'gfs': 'NCEP-GFS'}
lock = threading.Lock(); local = threading.local()

def S():
    if not hasattr(local, 's'):
        s = requests.Session(); s.trust_env = False; s.proxies.update({'http': PROXY, 'https': PROXY}); local.s = s
    return local.s

def get(url, rng=None, tries=8):
    h = {'Range': f'bytes={rng[0]}-{rng[1]}'} if rng else {}
    wait = 3
    for _ in range(tries):
        try:
            r = S().get(url, headers=h, timeout=60)
            if r.status_code in (200, 206): return r.content
            if r.status_code == 404: return None
        except Exception: pass
        time.sleep(wait); wait = min(wait * 2, 60)
    raise RuntimeError(f'failed {url.rsplit("/", 1)[-1]}')

def crop(msg, scale, offset=0.0):
    g = eccodes.codes_new_from_message(msg)
    try:
        ni = eccodes.codes_get(g, 'Ni'); nj = eccodes.codes_get(g, 'Nj')
        la0 = eccodes.codes_get(g, 'latitudeOfFirstGridPointInDegrees')
        lo0 = eccodes.codes_get(g, 'longitudeOfFirstGridPointInDegrees')
        d = eccodes.codes_get(g, 'iDirectionIncrementInDegrees')
        jpos = eccodes.codes_get(g, 'jScansPositively')
        if abs(d - 0.25) > 1e-6: raise ValueError('not 0.25 grid')
        v = eccodes.codes_get_values(g).reshape(nj, ni)
    finally:
        eccodes.codes_release(g)
    jj = np.rint(((LAT - la0) if jpos else (la0 - LAT)) / d).astype(int)
    ii = np.rint(((LON - lo0) % 360) / d).astype(int)
    return (v[np.ix_(jj, ii)] * scale + offset).round(3)

def raw_path(slug, run): return RAW / slug / f'{run:%Y%m%d%H}.jsonl'

def done(slug, run):
    p = raw_path(slug, run)
    if not p.exists(): return set()
    return {(j['kind'], j['lead']) for j in map(json.loads, p.read_text().splitlines()) if j}

def save(slug, run, kind, lead, arr):
    with lock:
        with raw_path(slug, run).open('a') as f:
            f.write(json.dumps({'kind': kind, 'lead': lead, 'v': arr.ravel().tolist()}) + '\n')

def ec_task(run, lead, kinds):
    b = f'https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/{run:%Y%m%d}/{run:%H}z/ifs/0p25/oper/{run:%Y%m%d%H}0000-{lead}h-oper-fc'
    idx = get(b + '.index')
    if idx is None: return 'missing'
    recs = {j.get('param'): j for j in map(json.loads, idx.decode().splitlines()) if j.get('param') in ('tp', '2t')}
    for kind in kinds:
        j = recs.get('tp' if kind == 'tp' else '2t')
        if not j: return f'no {kind}'
        time.sleep(0.3)
        a = j['_offset']; data = get(b + '.grib2', (a, a + j['_length'] - 1))
        save('ecmwf', run, kind, lead, crop(data, 1000.0) if kind == 'tp' else crop(data, 1.0, -273.15))
    return 'ok'

def gfs_task(run, lead, kinds):
    b = f'https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f{lead:03d}'
    idx = get(b + '.idx')
    if idx is None: return 'missing'
    lines = idx.decode().splitlines()
    def rng(pred):
        for n, l in enumerate(lines):
            if pred(l): return int(l.split(':')[1]), int(lines[n + 1].split(':')[1]) - 1
        return None
    for kind in kinds:
        if kind == 'tp':
            def pred(l, L=lead):
                m = re.search(r':APCP:surface:0-(\d+) (hour|day) acc', l)
                return bool(m) and int(m.group(1)) * (24 if m.group(2) == 'day' else 1) == L
            r = rng(pred)
            if r is None: return 'no tp'
            save('gfs', run, kind, lead, crop(get(b, r), 1.0))
        else:
            r = rng(lambda l: ':TMP:2 m above ground:' in l)
            if r is None: return 'no 2t'
            save('gfs', run, kind, lead, crop(get(b, r), 1.0, -273.15))
    return 'ok'

def download(model, first, last):
    slug = SLUG[model]; (RAW / slug).mkdir(parents=True, exist_ok=True)
    runs = []; t = first
    while t <= last: runs.append(t); t += timedelta(hours=6)
    tp_jobs, t_jobs = [], []
    for run in runs:
        have = done(slug, run)
        for L in LEADS:
            if run + timedelta(hours=L) < NOT_BEFORE: continue
            for lead in (L - 3, L):
                if ('tp', lead) not in have: tp_jobs.append((run, lead, ('tp',)))
            if ('t2', L) not in have: t_jobs.append((run, L, ('t2',)))
    # de-duplicate tp jobs (L-3 never equals another L here, but keep it safe)
    tp_jobs = list(dict.fromkeys(tp_jobs))
    jobs = tp_jobs + t_jobs
    fn = ec_task if model == 'ec' else gfs_task
    workers = 2 if model == 'ec' else 6
    print(f'[{model}] {len(runs)} runs, {len(tp_jobs)} tp + {len(t_jobs)} t2 jobs', flush=True)
    t0 = time.time(); n = 0; bad = []
    with ThreadPoolExecutor(workers) as ex:
        fut = {ex.submit(fn, *j): j for j in jobs}
        for f in as_completed(fut):
            n += 1
            try: r = f.result()
            except Exception as e: r = f'err {e}'
            if r != 'ok': bad.append([f'{fut[f][0]:%Y%m%d%H}', fut[f][1], fut[f][2][0], r])
            if n % 50 == 0 or n == len(jobs):
                print(f'[{model}] {n}/{len(jobs)} {time.time()-t0:.0f}s bad={len(bad)}', flush=True)
    (ROOT / f'{model}_bad.json').write_text(json.dumps(bad, ensure_ascii=False, indent=1))
    print('ALL DONE', flush=True)

def iso(t): return t.strftime('%Y-%m-%dT%H:%M:%SZ')

def assemble():
    written = 0
    for slug in ('ecmwf', 'gfs'):
        out = ROOT / slug; out.mkdir(parents=True, exist_ok=True)
        for p in sorted((RAW / slug).glob('*.jsonl')):
            run = datetime.strptime(p.stem, '%Y%m%d%H'); data = {}
            for line in p.read_text().splitlines():
                if line.strip():
                    j = json.loads(line); data[(j['kind'], j['lead'])] = np.asarray(j['v'], float)
            for L in LEADS:
                valid = run + timedelta(hours=L)
                if valid < NOT_BEFORE: continue
                frame = dict(schema=2, model=NAME[slug], run=iso(run), valid=iso(valid), lead=L,
                             lon=LON.tolist(), lat=LAT.tolist(), layers={}, source='verification-test backfill (open data, tp/2t only)')
                if ('tp', L) in data and ('tp', L - 3) in data:
                    r = data[('tp', L)] - data[('tp', L - 3)]
                    r[(r < 0) & (r >= -0.01)] = 0.0; r[r < -0.01] = np.nan
                    frame['rain3h'] = [None if not np.isfinite(x) else round(float(x), 3) for x in r]
                    frame['rain_start'] = iso(valid - timedelta(hours=3))
                if ('t2', L) in data:
                    frame['layers'] = {'surface': {'t': [round(float(x), 2) for x in data[('t2', L)]]}}
                if 'rain3h' not in frame and not frame['layers']: continue
                target = out / f'{slug}_{run:%Y%m%d%H}_f{L:03d}.json'
                body = json.dumps(frame, separators=(',', ':'))
                if not target.exists() or target.read_text() != body:
                    tmp = target.with_suffix('.tmp'); tmp.write_text(body); os.replace(tmp, target); written += 1
    print(f'ASSEMBLED {written} frames', flush=True)

def import_hist():
    n = 0
    for model, slug in (('ec', 'ecmwf'), ('gfs', 'gfs')):
        (RAW / slug).mkdir(parents=True, exist_ok=True)
        for p in sorted((HIST / model).glob('*.jsonl')):
            run = datetime.strptime(p.stem, '%Y%m%d%H'); have = done(slug, run)
            for line in p.read_text().splitlines():
                if not line.strip(): continue
                j = json.loads(line); a = np.asarray(j['acc_mm'], float)
                if a.shape != (len(LAT), len(LON)) or ('tp', j['lead']) in have: continue
                save(slug, run, 'tp', j['lead'], a); have.add(('tp', j['lead'])); n += 1
    print(f'IMPORTED {n} tp crops', flush=True)

if __name__ == '__main__':
    if sys.argv[1] == 'download':
        download(sys.argv[2], datetime.strptime(sys.argv[3], '%Y%m%d%H'), datetime.strptime(sys.argv[4], '%Y%m%d%H'))
    elif sys.argv[1] == 'import':
        import_hist()
    elif sys.argv[1] == 'assemble':
        assemble()
