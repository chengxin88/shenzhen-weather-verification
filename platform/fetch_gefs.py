#!/usr/bin/env python3
"""Fetch NOAA GEFS ensemble-mean (geavg, 0.25°) 2 m temperature and 6 h rain for the verification platform.

Test-platform data only: frames go to cache/native-backfill/gefs in the same layout as the AI frames
(<slug>_<run>_f<lead>_global1.json + .fields/*.npy, 108–120°E / 18–27°N, 0.25°). Range requests only
fetch the two needed GRIB messages; finished frames are skipped, so the script can be re-run.
Usage: fetch_gefs.py [--days N] [--runs 0,12] [--workers N]
"""
import argparse, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np, requests, eccodes

OUT = Path(__file__).resolve().parent / 'cache' / 'native-backfill' / 'gefs'
PROXY = os.environ.get('DL_PROXY', 'http://127.0.0.1:17908')
LON = np.arange(108, 120 + 1e-6, .25)
LAT = np.arange(18, 27 + 1e-6, .25)
LEADS = list(range(0, 169, 6))
BASE = 'https://noaa-gefs-pds.s3.amazonaws.com/gefs.{run:%Y%m%d}/{run:%H}/atmos/pgrb2sp25/geavg.t{run:%H}z.pgrb2s.0p25.f{lead:03d}'
log = lambda *a: print(time.strftime('%H:%M:%S'), *a, flush=True)
iso = lambda t: t.strftime('%Y-%m-%dT%H:%M:%SZ')

def session():
    s = requests.Session(); s.proxies.update({'http': PROXY, 'https': PROXY}); s.trust_env = False
    return s

def get(s, url, rng=None, tries=6):
    headers = {'Range': 'bytes=%d-%s' % (rng[0], '' if rng[1] is None else rng[1])} if rng else {}
    wait = 3
    for _ in range(tries):
        try:
            r = s.get(url, headers=headers, timeout=90)
            if r.status_code in (200, 206): return r.content
            if r.status_code in (403, 404): return None
        except requests.RequestException:
            pass
        time.sleep(wait); wait = min(wait * 2, 40)
    raise RuntimeError('download failed ' + url)

def message(s, url, lines, pattern):
    """Bytes of the one GRIB message whose .idx line matches pattern (None if the file has none)."""
    for n, line in enumerate(lines):
        if re.search(pattern, line):
            start = int(line.split(':')[1])
            end = int(lines[n + 1].split(':')[1]) - 1 if n + 1 < len(lines) else None
            return get(s, url, (start, end))
    return None

def crop(msg):
    g = eccodes.codes_new_from_message(msg)
    try:
        ni, nj = eccodes.codes_get(g, 'Ni'), eccodes.codes_get(g, 'Nj')
        la0 = eccodes.codes_get(g, 'latitudeOfFirstGridPointInDegrees')
        lo0 = eccodes.codes_get(g, 'longitudeOfFirstGridPointInDegrees')
        d = eccodes.codes_get(g, 'iDirectionIncrementInDegrees')
        positive = eccodes.codes_get(g, 'jScansPositively')
        if d != .25 or eccodes.codes_get(g, 'jDirectionIncrementInDegrees') != .25: raise ValueError('not 0.25 degrees')
        v = eccodes.codes_get_values(g).reshape(nj, ni)
    finally:
        eccodes.codes_release(g)
    jj = np.rint(((LAT - la0) if positive else (la0 - LAT)) / d).astype(int)
    ii = np.rint(((LON - lo0) % 360) / d).astype(int)
    out = v[np.ix_(jj, ii)].astype(np.float32)
    if not np.isfinite(out).all(): raise ValueError('missing values in box')
    return out

FIELDS = {  # name -> (.idx pattern, scale, offset); rain6h is added per lead
    'surface_t': (r':TMP:2 m above ground:', 1.0, -273.15),
    'surface_msl': (r':PRMSL:mean sea level:', 0.01, 0.0),   # Pa -> hPa
    'surface_u': (r':UGRD:10 m above ground:', 1.0, 0.0),
    'surface_v': (r':VGRD:10 m above ground:', 1.0, 0.0),
}

def frame(run, lead, s):
    valid = run + timedelta(hours=lead)
    stem = OUT / f'gefs_{run:%Y%m%d%H}_f{lead:03d}_global1'
    meta = stem.with_suffix('.json'); folder = stem.with_suffix('.fields')
    wanted = list(FIELDS) + (['rain6h'] if lead else [])
    missing = [n for n in wanted if not (folder / (n + '.npy')).is_file()]
    if meta.is_file() and not missing: return 'cached'
    url = BASE.format(run=run, lead=lead)
    idx = get(s, url + '.idx')
    if idx is None: return 'missing'
    lines = idx.decode().splitlines()
    fields = {}
    for name in missing:
        pattern, scale, offset = FIELDS[name] if name != 'rain6h' else (r':APCP:surface:%d-%d hour acc' % (lead - 6, lead), 1.0, 0.0)
        raw = message(s, url, lines, pattern)
        if raw is None: return 'no-' + name
        a = crop(raw) * np.float32(scale) + np.float32(offset)
        if name == 'rain6h':
            if (a < -0.01).any(): raise ValueError('negative rain')
            a = np.maximum(a, 0)
        fields[name] = a
    folder.mkdir(parents=True, exist_ok=True)
    for name, a in fields.items():
        tmp = folder / (name + '.tmp.npy'); np.save(tmp, a.astype(np.float32)); os.replace(tmp, folder / (name + '.npy'))
    if not meta.is_file():
        doc = dict(schema=2, model='GEFS集合平均', run=iso(run), valid=iso(valid), lead=lead, storage='gefs-regional-v1',
                   bounds=[108., 18., 120., 27.], native_grid_degrees=0.25, native_grid_shape=[len(LAT), len(LON)],
                   periodic_longitude=False, lon=LON.tolist(), lat=LAT.tolist(), interval_hours=6,
                   rain_start=iso(valid - timedelta(hours=6)) if lead else None, rain_accum_start=0, **{'global': False},
                   source='NOAA GEFS ensemble mean (geavg) pgrb2s 0.25')
        tmp = meta.with_suffix('.tmp'); tmp.write_text(json.dumps(doc, ensure_ascii=False)); os.replace(tmp, meta)  # json last = frame complete
    return 'ok'

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--runs', default='0,12'); ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc); today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    runs = [today - timedelta(days=d) + timedelta(hours=int(h)) for d in range(a.days + 1) for h in a.runs.split(',')]
    runs = sorted(r for r in runs if r <= now - timedelta(hours=8))   # GEFS needs ~6 h to publish
    jobs = [(r, L) for r in reversed(runs) for L in LEADS if r + timedelta(hours=L) <= now - timedelta(hours=1)]
    if a.limit: jobs = jobs[:a.limit]
    log('runs', len(runs), 'frame jobs', len(jobs))
    local = __import__('threading').local()
    def work(job):
        if not hasattr(local, 's'): local.s = session()
        return frame(*job, local.s)
    counts = {}; t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        futures = {ex.submit(work, j): j for j in jobs}
        for n, f in enumerate(as_completed(futures), 1):
            try: r = f.result()
            except Exception as e: r = 'err ' + type(e).__name__
            counts[r] = counts.get(r, 0) + 1
            if r.startswith('err') and counts[r] <= 3: log('ERR', futures[f][0], futures[f][1], r)
            if n % 50 == 0 or n == len(jobs): log(f'{n}/{len(jobs)} {time.time()-t0:.0f}s', counts)
    log('FINISHED', counts)

if __name__ == '__main__': main()
