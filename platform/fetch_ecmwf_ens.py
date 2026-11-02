#!/usr/bin/env python3
"""Fetch ECMWF ENS (0.25°, 50 perturbed members) and store the ensemble mean of 2 m temperature and rain.

ECMWF open data has no ready-made ensemble-mean product for 2t/tp, so each frame range-requests the
50 member messages of 2t and tp, crops them to 108–120°E / 18–27°N and averages them.
Frames go to cache/native-backfill/ecens in the same layout as the GEFS frames
(<slug>_<run>_f<lead>_global1.json + .fields/*.npy): leads 0–144 h every 3 h (rain3h),
150–168 h every 6 h (rain6h). Finished frames are skipped, so the script can be re-run.
Usage: fetch_ecmwf_ens.py [--days N] [--runs 0,12] [--workers N]
"""
import argparse, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_gefs import LON, LAT, session, get, crop, log, iso

OUT = Path(__file__).resolve().parent / 'cache' / 'native-backfill' / 'ecens'
LEADS = list(range(0, 145, 3)) + list(range(150, 169, 6))
PREVIOUS = {lead: LEADS[i - 1] for i, lead in enumerate(LEADS) if i}
MEMBERS = 50
SOURCES = ('https://storage.googleapis.com/ecmwf-open-data',
           'https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com')
PATH = '{run:%Y%m%d}/{run:%H}z/ifs/0p25/enfo/{run:%Y%m%d%H}0000-{lead}h-enfo-ef'

def stem(run, lead):
    return OUT / f'ecens_{run:%Y%m%d%H}_f{lead:03d}_global1'

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
        a = crop(raw).astype(np.float64)
        total = a if total is None else total + a
    return (total / MEMBERS).astype(np.float32)

def download(run, lead, s):
    """Phase 1: surface_t (°C) and tp_cum (mm since run start) for one lead."""
    folder = stem(run, lead).with_suffix('.fields')
    missing = [n for n in ('surface_t', 'tp_cum') if not (folder / (n + '.npy')).is_file()]
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
    if 'surface_t' in missing:
        fields['surface_t'] = member_mean(s, url + '.grib2', entries, '2t') - np.float32(273.15)
    if 'tp_cum' in missing:
        fields['tp_cum'] = member_mean(s, url + '.grib2', entries, 'tp') * np.float32(1000.0)
    folder.mkdir(parents=True, exist_ok=True)
    for name, a in fields.items():
        tmp = folder / (name + '.tmp.npy'); np.save(tmp, a); os.replace(tmp, folder / (name + '.npy'))
    return 'ok'

def finish(run, lead):
    """Phase 2: interval rain from consecutive tp_cum, then the frame json (json last = frame complete)."""
    path = stem(run, lead); meta = path.with_suffix('.json'); folder = path.with_suffix('.fields')
    if meta.is_file():
        return 'cached'
    if not (folder / 'surface_t.npy').is_file() or not (folder / 'tp_cum.npy').is_file():
        return 'incomplete'
    valid = run + timedelta(hours=lead)
    interval = None
    if lead:
        prior = stem(run, PREVIOUS[lead]).with_suffix('.fields') / 'tp_cum.npy'
        if not prior.is_file():
            return 'incomplete'
        interval = lead - PREVIOUS[lead]
        rain = np.load(folder / 'tp_cum.npy') - np.load(prior)
        if (rain < -0.05).any():   # GRIB packing noise on cumulative tp is ~0.01 mm
            raise ValueError(f'negative rain {run:%Y%m%d%H} f{lead:03d}')
        name = f'rain{interval}h'
        tmp = folder / (name + '.tmp.npy'); np.save(tmp, np.maximum(rain, 0).astype(np.float32))
        os.replace(tmp, folder / (name + '.npy'))
    doc = dict(schema=2, model='ECMWF-ENSEMBLES平均', run=iso(run), valid=iso(valid), lead=lead,
               storage='ecens-regional-v1', bounds=[108., 18., 120., 27.], native_grid_degrees=0.25,
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
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc); today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    runs = [today - timedelta(days=d) + timedelta(hours=int(h)) for d in range(a.days + 1) for h in a.runs.split(',')]
    runs = sorted(r for r in runs if r <= now - timedelta(hours=8))   # ENS 168 h is out ~8 h after the run
    jobs = [(r, L) for r in reversed(runs) for L in LEADS]
    if a.limit: jobs = jobs[:a.limit]
    log('runs', len(runs), 'frame jobs', len(jobs))
    local = threading.local()
    def work(job):
        if not hasattr(local, 's'): local.s = session()
        return download(*job, local.s)
    counts = {}; t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        futures = {ex.submit(work, j): j for j in jobs}
        for n, f in enumerate(as_completed(futures), 1):
            try: r = f.result()
            except Exception as e: r = 'err ' + type(e).__name__
            counts[r] = counts.get(r, 0) + 1
            if r.startswith('err') and counts[r] <= 3: log('ERR', futures[f][0], futures[f][1], r)
            if n % 25 == 0 or n == len(jobs): log(f'{n}/{len(jobs)} {time.time()-t0:.0f}s', counts)
    done = {}
    for job in sorted(jobs):
        try: r = finish(*job)
        except Exception as e: r = 'err ' + type(e).__name__; log('ERR finish', job[0], job[1], e)
        done[r] = done.get(r, 0) + 1
    log('FINISHED download', counts, 'frames', done)

if __name__ == '__main__': main()
