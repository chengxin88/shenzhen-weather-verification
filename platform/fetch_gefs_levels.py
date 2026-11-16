#!/usr/bin/env python3
"""Fetch NOAA GEFS ensemble-mean (geavg, 0.5 deg) height and wind on pressure levels.

The 0.25 deg pgrb2s file used by fetch_gefs.py has no pressure levels, so this
range-requests 18 GRIB messages per frame from pgrb2a and keeps a small regional box.
Only valid times already passed are fetched (verification data).
"""
import argparse, os, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading

import eccodes
import numpy as np

from fetch_gefs import get, log, session

OUT = Path(__file__).resolve().parent/'cache/native-backfill/gefs-levels'
BASE = 'https://noaa-gefs-pds.s3.amazonaws.com/gefs.{run:%Y%m%d}/{run:%H}/atmos/pgrb2ap5/geavg.t{run:%H}z.pgrb2a.0p50.f{lead:03d}'
LEVELS = (925, 850, 700, 500, 300, 200)
LEADS = list(range(0, 241, 6))
LON = np.arange(104., 124.01, .5)
LAT = np.arange(14., 31.01, .5)
NAMES = {'gh': 'HGT', 'u': 'UGRD', 'v': 'VGRD'}


def crop(msg):
    g = eccodes.codes_new_from_message(msg)
    try:
        ni, nj = eccodes.codes_get(g, 'Ni'), eccodes.codes_get(g, 'Nj')
        la0 = eccodes.codes_get(g, 'latitudeOfFirstGridPointInDegrees')
        lo0 = eccodes.codes_get(g, 'longitudeOfFirstGridPointInDegrees')
        d = eccodes.codes_get(g, 'iDirectionIncrementInDegrees')
        positive = eccodes.codes_get(g, 'jScansPositively')
        if d != .5 or eccodes.codes_get(g, 'jDirectionIncrementInDegrees') != .5: raise ValueError('not 0.5 degrees')
        values = eccodes.codes_get_values(g).reshape(nj, ni)
    finally:
        eccodes.codes_release(g)
    jj = np.rint(((LAT - la0) if positive else (la0 - LAT)) / d).astype(int)
    ii = np.rint(((LON - lo0) % 360) / d).astype(int)
    out = values[np.ix_(jj, ii)].astype(np.float32)
    if not np.isfinite(out).all(): raise ValueError('missing values in box')
    return out


def frame(run, lead, s):
    path = OUT/f'gefs_levels_{run:%Y%m%d%H}_f{lead:03d}.npz'
    if path.exists(): return 'skip'
    url = BASE.format(run=run, lead=lead)
    idx = get(s, url + '.idx')
    if idx is None: return 'missing'
    lines = idx.decode().strip().splitlines()
    fields = {}
    for key, name in NAMES.items():
        layers = []
        for level in LEVELS:
            for n, line in enumerate(lines):
                if re.search(f':{name}:{level} mb:', line):
                    start = int(line.split(':')[1])
                    end = int(lines[n + 1].split(':')[1]) - 1 if n + 1 < len(lines) else None
                    content = get(s, url, (start, end))
                    if content is None: return 'missing'
                    layers.append(crop(content))
                    break
            else:
                return 'incomplete'
        fields[key] = np.stack(layers)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with temp.open('wb') as handle:
        np.savez_compressed(handle, lon=LON, lat=LAT, levels_hpa=np.array(LEVELS), lead_h=np.array(lead),
                            run=np.array(f'{run:%Y%m%d%H}'), source=np.array('NOAA GEFS geavg pgrb2a 0.5'), **fields)
    os.replace(temp, path)
    return 'ok'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=11)
    parser.add_argument('--runs', default='0,12')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc); today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    runs = [today - timedelta(days=d) + timedelta(hours=int(h)) for d in range(args.days + 1) for h in args.runs.split(',')]
    runs = sorted(r for r in runs if r <= now - timedelta(hours=8))
    jobs = [(r, lead) for r in reversed(runs) for lead in LEADS if now - timedelta(days=31) <= r + timedelta(hours=lead) <= now - timedelta(hours=1)]
    if args.limit: jobs = jobs[:args.limit]
    log('runs', len(runs), 'frame jobs', len(jobs))
    local = threading.local()
    def work(job):
        if not hasattr(local, 's'): local.s = session()
        return frame(*job, local.s)
    counts = {}; started = time.time()
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(work, job): job for job in jobs}
        for n, future in enumerate(as_completed(futures), 1):
            try: result = future.result()
            except Exception as exc: result = 'err ' + type(exc).__name__
            counts[result] = counts.get(result, 0) + 1
            if result.startswith('err') and counts[result] <= 3: log('ERR', futures[future], result)
            if n % 50 == 0 or n == len(jobs): log(f'{n}/{len(jobs)} {time.time()-started:.0f}s', counts)
    log('FINISHED', counts)


if __name__ == '__main__': main()
