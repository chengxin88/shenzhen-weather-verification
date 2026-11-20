"""Optional EC 9 km point supplements; default CLI invocation only plans.

No credentials, retries, automatic HTTP worker calls or changes to grid files.
Each explicit GET is journalled before sending; a cache-wide budget caps it.
"""
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import fcntl
import json
import os

import numpy as np
import requests
from station_common import UTC, atomic_json, cache_root, digest, iso, read_json, stamp, model_allowed

SCHEMA = 'nmic-ec01-points-v1'
FIELDS = {'t': 'temperature_2m', 'td': 'dew_point_2m', 'msl': 'pressure_msl',
          'wind': 'wind_speed_10m', 'rain': 'precipitation'}
LEADS = tuple(range(91)) + tuple(range(93, 145, 3)) + tuple(range(150, 241, 6))
URL = 'https://single-runs-api.open-meteo.com/v1/forecast'


def decode_response(data, stations, run):
    """Require the exact run-relative hourly axis and one result per point."""
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or len(data) != len(stations):
        raise ValueError('EC01 point count mismatch')
    samples = {h: {k: np.full(len(stations), np.nan) for k in FIELDS} for h in range(241)}
    expected = [int(run.timestamp())+3600*h for h in range(241)]
    units = dict(temperature_2m='°C', dew_point_2m='°C', pressure_msl='hPa', wind_speed_10m='m/s', precipitation='mm')
    for i, item in enumerate(data):
        if item.get('hourly', {}).get('time') != expected or item.get('utc_offset_seconds') != 0:
            raise ValueError('EC01 time axis mismatch')
        if any(item.get('hourly_units', {}).get(k) != v for k, v in units.items()):
            raise ValueError('EC01 units mismatch')
        # Returned coordinates are the selected native grid centre, not the
        # requested point; permit the ~9 km nearest-cell offset, not reordering.
        if abs(float(item['latitude'])-stations[i]['lat']) > .25 or abs(float(item['longitude'])-stations[i]['lon']) > .25:
            raise ValueError('EC01 point coordinates mismatch')
        for key, name in FIELDS.items():
            values = item['hourly'].get(name, [])
            if len(values) != 241:
                raise ValueError('EC01 field length mismatch')
            for h, value in enumerate(values):
                samples[h][key][i] = np.nan if value is None else float(value)
    for h in samples:
        if h:
            samples[h]['rain_start'] = h-1
        else:
            samples[h].pop('rain')
    # Match the existing EC native tiling. Hourly API output can redistribute
    # multi-hour rain; sum complete 3/6 h steps and never score split steps.
    native = {}
    for h in LEADS:
        native[h] = {k:v for k,v in samples[h].items() if k not in ('rain','rain_start')}
        if h:
            step = 1 if h <= 90 else 3 if h <= 144 else 6
            native[h].update(rain=sum((samples[k]['rain'] for k in range(h-step+1,h+1)),
                                      np.zeros(len(stations))), rain_start=h-step)
    return native


def read_points(path, stations, wanted=None):
    from station_forecasts import load_samples, sample_state
    state, _ = sample_state(path, path.with_suffix('.json'))
    if state.get('schema') != SCHEMA:
        raise ValueError('Invalid EC01 point supplement certificate')
    points = state['points']; target = {(s['id'], s['lon'], s['lat']): i for i, s in enumerate(stations)}
    mapping = [(i, target[tuple(p)]) for i, p in enumerate(points) if tuple(p) in target and model_allowed(stations[target[tuple(p)]], 'ec01')]
    result = load_samples(path, wanted)
    for values in result.values():
        for key, array in list(values.items()):
            if key == 'rain_start':
                continue
            if np.shape(array) != (len(points),):
                raise ValueError('EC01 point supplement dimensions mismatch')
            expanded = np.full(len(stations), np.nan)
            for i, dest in mapping:
                expanded[dest] = array[i]
            values[key] = expanded
    return result


def merge_samples(base, extra):
    """Keep every finite grid/legacy value and its original rain interval."""
    from station_forecasts import accumulate
    result = {h: dict(v) for h, v in base.items()}
    intervals = {h: (float(v['rain_start']), v['rain']) for h, v in extra.items() if 'rain_start' in v}
    for h, values in extra.items():
        if h not in result:
            result[h] = dict(values)
            continue
        out = result[h]
        for key, value in values.items():
            if key in ('rain', 'rain_start'):
                continue
            out[key] = np.where(np.isfinite(out[key]), out[key], value) if key in out else value
        start = float(out.get('rain_start', values.get('rain_start', h)))
        if start < h:
            rain = accumulate(intervals, h, h-start)
            if rain is not None:
                out['rain'] = np.where(np.isfinite(out['rain']), out['rain'], rain) if 'rain' in out else rain
                out['rain_start'] = start
    return result


def supplement_path(root, ident):
    return root / 'forecast' / f'ec01_{ident}.nmic.npz'


def request_stations(stations):
    return [s for s in stations if 'nmic' in s.get('sources', []) and
            not s.get('nmic_cmagd_metadata_only') and model_allowed(s, 'ec01')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True, type=stamp, help='First run, UTC')
    parser.add_argument('--end', required=True, type=stamp, help='Last run, UTC (00/12 cycles)')
    parser.add_argument('--fetch', action='store_true', help='Send the planned GET requests')
    parser.add_argument('--budget', type=int, default=300, help='Persistent cache-wide GET cap, at most 300')
    args = parser.parse_args()
    if not 1 <= args.budget <= 300 or args.start > args.end:
        parser.error('Require start <= end and budget in 1..300')
    root = cache_root()
    import nmic_obs
    table = read_json(root / 'stations.json', read_json(Path(__file__).resolve().parent / 'stations.json'))
    nmic_obs.add_stations(table, root)
    stations = request_stations(table['stations'])
    if not stations:
        parser.error('No active NMIC stations; configure STATIONS_NMIC_DIR / METADATA first')
    # One batch per run: 176 sites fit comfortably in a normal HTTP request.
    points = [[s['id'], s['lon'], s['lat']] for s in stations]
    pending = []; at = args.start.replace(hour=0, minute=0, second=0, microsecond=0)
    while at <= args.end:
        if at >= args.start:
            path = supplement_path(root, at.strftime('%Y%m%d%H'))
            if path.is_file():
                from station_forecasts import sample_state
                state, _ = sample_state(path, path.with_suffix('.json'))
                done = state.get('schema') == SCHEMA and state.get('points') == points
            else:
                done = False
            if not done:
                pending.append(at)
        at += timedelta(hours=12)
    print(json.dumps(dict(stations=len(stations), runs=len(pending), requests=len(pending), fetch=args.fetch,
                          budget=args.budget, dates=[iso(r) for r in pending]), ensure_ascii=False))
    if not args.fetch:
        return
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'refresh.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal_path = root / 'nmic-ec01-requests.json'
        journal = read_json(journal_path, dict(requests=[]))
        if len(journal['requests'])+len(pending) > args.budget:
            raise ValueError('Persistent EC01 request budget would be exceeded')
        client = requests.Session(); client.trust_env = False
        proxy = os.environ.get('OPENMETEO_EC_PROXY', '')
        if proxy:
            client.proxies = dict(http=proxy, https=proxy)
        for run in pending:
            record = dict(run=iso(run), points=digest(points), state='sending')
            journal['requests'].append(record); atomic_json(journal_path, journal, durable=True)
            try:
                response = client.get(URL, params=dict(latitude=','.join(str(s['lat']) for s in stations),
                    longitude=','.join(str(s['lon']) for s in stations), elevation=','.join('nan' for s in stations),
                    models='ecmwf_ifs', run=run.strftime('%Y-%m-%dT%H:%M'), forecast_hours=241,
                    hourly=','.join(FIELDS.values()), wind_speed_unit='ms', temperature_unit='celsius',
                    precipitation_unit='mm', cell_selection='nearest', timezone='GMT', timeformat='unixtime'), timeout=(10, 90),
                    allow_redirects=False)
                response.raise_for_status()
                samples = decode_response(response.json(), stations, run)
                path = supplement_path(root, run.strftime('%Y%m%d%H')); path.parent.mkdir(exist_ok=True)
                from station_forecasts import save_samples
                save_samples(path, sorted(samples), samples, state=dict(schema=SCHEMA, points=points, run=iso(run)))
                record['state'] = 'complete'; print('OK', iso(run), flush=True)
            except Exception as exc:
                record['state'] = 'failed'; record['error'] = type(exc).__name__
                raise
            finally:
                atomic_json(journal_path, journal, durable=True)


if __name__ == '__main__':
    main()
