#!/usr/bin/env python3
"""Backfill Shenzhen hourly grid observations into the TEST platform's own cache.

Same API/normalisation as ec-gfs-server/prefetch_observation_hour.py, but written to
verification-test/cache/obs-backfill/grid so the main platform's 16-day retention never
deletes them. Only hours missing from both caches are fetched.
Usage (cwd ~/ec-gfs-server for APP_KEY): backfill_obs.py START_BJT END_BJT [HOURS]
  e.g. backfill_obs.py 2026-09-01T00 2026-09-28T00 02,05,08,11,14,17,20,23
"""
import os, sys, tempfile, time
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, '/home/chengyang/ec-gfs-server')
import requests, urllib3
import prefetch_observation_hour as hourly

OUT = Path(__file__).resolve().parent / 'cache' / 'obs-backfill' / 'grid'
MAIN = hourly.CACHE_DIR
PROXY = os.environ.get('BF_PROXY', 'http://127.0.0.1:17960')

def main():
    start = datetime.strptime(sys.argv[1], '%Y-%m-%dT%H')
    end = datetime.strptime(sys.argv[2], '%Y-%m-%dT%H')
    hours = {int(h) for h in sys.argv[3].split(',')} if len(sys.argv) > 3 else set(range(24))
    key = hourly.config_app_key()
    if not key:
        raise SystemExit('APP_KEY is not configured')
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    s = requests.Session(); s.trust_env = False
    s.proxies.update({'http': PROXY, 'https': PROXY})
    s.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'https://opendata.sz.gov.cn/'})
    OUT.mkdir(parents=True, exist_ok=True)
    t = start; ok = skip = bad = 0
    while t <= end:
        name = f'{t:%Y%m%d%H}.pkl'
        if t.hour not in hours or (OUT / name).is_file() or (MAIN / name).is_file():
            skip += 1; t += timedelta(hours=1); continue
        for attempt in range(3):
            try:
                frame = hourly.normalize(hourly.records_for_time(s, key, t))
                if not hourly.is_complete(frame):
                    raise RuntimeError(f'incomplete {frame["GRIDID"].nunique()} grids')
                with tempfile.NamedTemporaryFile(dir=OUT, suffix='.tmp', delete=False) as h:
                    tmp = Path(h.name)
                frame.to_pickle(tmp); tmp.replace(OUT / name)
                ok += 1; print(f'OK {t:%Y-%m-%d %H}', flush=True); break
            except Exception as e:
                msg = str(e).replace(key, '***')
                if attempt == 2:
                    bad += 1; print(f'FAIL {t:%Y-%m-%d %H}: {type(e).__name__} {msg[:120]}', flush=True)
                else:
                    time.sleep(5 * (attempt + 1))
        t += timedelta(hours=1)
    print(f'OBS DONE ok={ok} skip={skip} bad={bad}', flush=True)

if __name__ == '__main__':
    main()
