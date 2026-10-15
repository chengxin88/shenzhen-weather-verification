"""Shared, exact-run ECMWF IFS HRES cache for the three weather applications.

Only the CLI downloads. Readers serve committed local data and never substitute
another initialization, model, missing interval, or unavailable pressure level.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import argparse
import fcntl
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import requests
from scipy.spatial import Delaunay, cKDTree

UTC = timezone.utc
BJT = timezone(timedelta(hours=8))
MODEL = 'ECMWF-IFS-9km-OpenMeteo'
SLUG = 'ecmwf_openmeteo'
API = 'https://single-runs-api.open-meteo.com/v1/forecast'
ROOT = Path(os.getenv('OPENMETEO_EC_ROOT', '/home/chengyang/openmeteo-ecmwf/cache'))
SCHEMA = 'openmeteo-ifs-exact-run-v1'
# The bounding-box API returns original O1280 cells, including edge cells.
BOUNDS = (113.6, 22.25, 114.8, 23.0)
SEGMENTS = {'hourly': (0, 90, 1), 'three_hourly': (90, 144, 3), 'six_hourly': (144, 168, 6)}
VARIABLES = {
    'temperature_2m': ('°C', -100, 70),
    'precipitation': ('mm', 0, 2000),
    'wind_speed_10m': ('m/s', 0, 150),
    'wind_direction_10m': ('°', 0, 360),
    'pressure_msl': ('hPa', 650, 1150),
    'relative_humidity_2m': ('%', 0, 100),
    'dew_point_2m': ('°C', -110, 70),
}
RAIN_WINDOWS = (1, 3, 6, 12, 24)


def iso(value):
    return value.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def stamp(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('UTC offset is required')
    return result.astimezone(UTC)


def run_time(value):
    if isinstance(value, datetime):
        result = value
    elif re.fullmatch(r'\d{10}', str(value)):
        result = datetime.strptime(value, '%Y%m%d%H').replace(tzinfo=UTC)
    else:
        result = stamp(value)
    if result.tzinfo is None:
        raise ValueError('UTC offset is required')
    result = result.astimezone(UTC)
    if result.minute or result.second or result.microsecond or result.hour % 6:
        raise ValueError('Expected an exact 00/06/12/18 UTC initialization')
    return result.astimezone(UTC)


def run_id(value):
    return run_time(value).strftime('%Y%m%d%H')


def expected_segments(run):
    # The exact-run archive's 06/18 UTC cycles end at F144; 00/12 UTC
    # cycles include the subsequent native six-hour outputs.
    return tuple(SEGMENTS) if run_time(run).hour in (0,12) else ('hourly','three_hourly')


def atomic_bytes(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.openmeteo-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(value)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path, value):
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, allow_nan=False).encode())


def parameters(run, segment):
    start, end, interval = SEGMENTS[segment]
    west, south, east, north = BOUNDS
    return dict(models='ecmwf_ifs', run=run_time(run).strftime('%Y-%m-%dT%H:%M'),
                bounding_box=f'{south},{west},{north},{east}',
                hourly=','.join(VARIABLES), timezone='GMT', elevation='nan',
                cell_selection='nearest', wind_speed_unit='ms', temperature_unit='celsius',
                precipitation_unit='mm', temporal_resolution=f'hourly_{interval}',forecast_hours=end+1)


def validate_response(data, run, segment):
    if not isinstance(data, list) or not 50 <= len(data) <= 300:
        raise ValueError('Incomplete or unexpectedly large native-cell response')
    start, end, interval = SEGMENTS[segment]
    leads = np.arange(0, end+1, interval, dtype=np.int16)
    expected = [(run_time(run)+timedelta(hours=int(h))).strftime('%Y-%m-%dT%H:%M') for h in leads]
    coordinates, fields = [], {name: [] for name in VARIABLES}
    for item in data:
        lon, lat = float(item['longitude']), float(item['latitude'])
        if not (BOUNDS[0]-.15 <= lon <= BOUNDS[2]+.15 and BOUNDS[1]-.15 <= lat <= BOUNDS[3]+.15):
            raise ValueError('Response cell outside the requested region')
        if item.get('utc_offset_seconds') != 0 or item.get('hourly', {}).get('time') != expected:
            raise ValueError('Response time axis does not match the requested run and segment')
        coordinates.append((lon, lat))
        for name, (unit, low, high) in VARIABLES.items():
            if item.get('hourly_units', {}).get(name) != unit:
                raise ValueError('Response unit mismatch: '+name)
            values = item['hourly'].get(name)
            if not isinstance(values, list) or len(values) != len(leads):
                raise ValueError('Response field length mismatch')
            a = np.asarray([np.nan if v is None else v for v in values], dtype=np.float32)
            if np.isinf(a).any() or np.any(np.isfinite(a) & ((a < low) | (a > high))):
                raise ValueError('Invalid response field range: '+name)
            # At segment boundaries the first precipitation interval belongs
            # to the preceding segment. It is never admitted twice.
            fields[name].append(a)
    points = np.asarray(coordinates, dtype=np.float64)
    if len(np.unique(points, axis=0)) != len(points) or np.ptp(points[:, 0]) < 1 or np.ptp(points[:, 1]) < .5:
        raise ValueError('Native coordinates are duplicated or incomplete')
    order = np.lexsort((points[:, 0], points[:, 1]))
    selected = leads >= start
    arrays = {name: np.asarray(values, dtype=np.float32).T[selected][:, order] for name, values in fields.items()}
    if not all(np.isfinite(arrays[name][1:]).any() for name in VARIABLES):
        raise ValueError('Requested initialization is not available yet')
    return leads[selected], points[order], arrays


def reserve_request(cost=180):
    """Conservative local limits, below the public API's published limits."""
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT/'request-budget.json'
    with (ROOT/'budget.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        while True:
            now = time.time()
            try:
                entries = json.loads(path.read_text())
            except (OSError, ValueError):
                entries = []
            entries = [e for e in entries if now-e[0] < 86400]
            if sum(e[1] for e in entries) + cost > 8000:
                raise RuntimeError('Local daily API budget reached; cached forecasts remain available')
            recent = [e for e in entries if now-e[0] < 60]
            if sum(e[1] for e in recent)+cost <= 400:
                break
            delay = min(60, max(1, 61-(now-recent[0][0])))
            time.sleep(delay)
        entries.append([now, cost])
        atomic_json(path, entries)


def acquire_segment(run, segment):
    directory = ROOT/'runs'/run_id(run)
    raw_path = directory/(segment+'.json.gz')
    params = parameters(run, segment)
    raw = None
    if raw_path.is_file():
        try:
            saved = json.loads(gzip.decompress(raw_path.read_bytes()))
            if saved['parameters'] == params:
                validate_response(saved['response'], run, segment)
                raw = saved
        except (ValueError, KeyError, OSError, EOFError):
            pass
    if raw is None:
        reserve_request()
        url = API+'?'+urllib.parse.urlencode(params)
        session = requests.Session()
        session.trust_env = False
        proxy = os.getenv('OPENMETEO_EC_PROXY', 'http://127.0.0.1:17908')
        if proxy:
            session.proxies = {'http':proxy,'https':proxy}
        with session.get(url,headers={'User-Agent':'SZVerify-EC9km/1.0'},timeout=(10,60),stream=True) as response:
            response.raise_for_status()
            content = bytearray()
            for block in response.iter_content(65536):
                content.extend(block)
                if len(content)>8_000_000:
                    raise ValueError('API response exceeded the regional size limit')
            body = bytes(content)
        payload = json.loads(body)
        validate_response(payload, run, segment)
        raw = dict(schema=SCHEMA, endpoint=API, parameters=params, retrieved_at=iso(datetime.now(UTC)),
                   response_sha256=hashlib.sha256(body).hexdigest(), response=payload)
        atomic_bytes(raw_path, gzip.compress(json.dumps(raw, allow_nan=False).encode(), compresslevel=5))
    leads, points, fields = validate_response(raw['response'], run, segment)
    metadata = dict(schema=SCHEMA, model=MODEL, api_model='ecmwf_ifs', endpoint=API, run=iso(run_time(run)),
                    segment=segment, interval_hours=SEGMENTS[segment][2], native_grid='O1280', native_grid_km=9,
                    bounds=BOUNDS, elevation_correction=False, cell_selection='nearest',
                    retrieved_at=raw['retrieved_at'], response_sha256=raw['response_sha256'],
                    parameters=params, units={key: value[0] for key, value in VARIABLES.items()})
    target = directory/(segment+'.npz')
    with tempfile.TemporaryFile() as handle:
        np.savez_compressed(handle, meta=np.array(json.dumps(metadata)), leads=leads, points=points, **fields)
        handle.seek(0)
        content = handle.read()
    atomic_bytes(target, content)
    return dict(file=target.name, sha256=hashlib.sha256(content).hexdigest(),
                retrieved_at=metadata['retrieved_at'], native_cells=len(points))


def read_manifest(run):
    import openmeteo_regional as regional
    if regional.exists(run):
        return regional.manifest(run)
    path = ROOT/'runs'/run_id(run)/'run.json'
    data = json.loads(path.read_text())
    if (data.get('schema') != SCHEMA or data.get('model') != MODEL or
            stamp(data['run']) != run_time(run) or not isinstance(data.get('segments'), dict)):
        raise ValueError('Cached run identity mismatch')
    return data


def ingest(run):
    run = run_time(run)
    directory = ROOT/'runs'/run_id(run)
    try:
        manifest = read_manifest(run)
    except (OSError, ValueError, KeyError):
        manifest = dict(schema=SCHEMA, model=MODEL, run=iso(run), segments={}, bounds=BOUNDS)
    for segment in expected_segments(run):
        entry = manifest['segments'].get(segment)
        if entry:
            try:
                load_segment(run_id(run), segment, entry['sha256'])
                continue
            except (OSError, ValueError, KeyError):
                pass
        atomic_json(ROOT/'status.json', dict(state='downloading', run=iso(run), segment=segment, updated=iso(datetime.now(UTC))))
        print('Open-Meteo', run_id(run), segment, flush=True)
        manifest['segments'][segment] = acquire_segment(run, segment)
        manifest['updated'] = iso(datetime.now(UTC))
        atomic_json(directory/'run.json', manifest)
    atomic_json(ROOT/'status.json', dict(state='ready', run=iso(run), updated=iso(datetime.now(UTC))))
    return manifest


@lru_cache(maxsize=18)
def load_segment(ident, segment, digest):
    if segment not in SEGMENTS or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Invalid cached segment')
    path = ROOT/'runs'/run_id(ident)/(segment+'.npz')
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError('Cached forecast checksum mismatch')
    import io
    with np.load(io.BytesIO(content), allow_pickle=False) as z:
        meta = json.loads(str(z['meta'].item()))
        leads, points = z['leads'], z['points']
        arrays = {name: z[name] for name in VARIABLES}
    if (meta.get('schema') != SCHEMA or meta.get('model') != MODEL or meta.get('api_model') != 'ecmwf_ifs'
            or meta.get('endpoint') != API or meta.get('run') != iso(run_time(ident)) or meta.get('segment') != segment
            or meta.get('parameters') != parameters(run_time(ident), segment) or meta.get('native_grid') != 'O1280'
            or meta.get('elevation_correction') is not False or meta.get('bounds') != list(BOUNDS)
            or meta.get('units') != {key: value[0] for key, value in VARIABLES.items()}):
        raise ValueError('Cached forecast provenance mismatch')
    start, end, interval = SEGMENTS[segment]
    if (not np.array_equal(leads, np.arange(start, end+1, interval)) or points.ndim != 2 or points.shape[1] != 2
            or not 50 <= len(points) <= 300 or not np.isfinite(points).all()
            or any(a.shape != (len(leads), len(points)) for a in arrays.values())):
        raise ValueError('Cached forecast dimensions mismatch')
    for name, a in arrays.items():
        _, low, high = VARIABLES[name]
        if np.isinf(a).any() or np.any(np.isfinite(a) & ((a < low) | (a > high))):
            raise ValueError('Cached forecast values mismatch')
    return meta, leads, points, arrays


def segments(run):
    manifest = read_manifest(run)
    return {name: load_segment(run_id(run), name, entry['sha256'])
            for name, entry in manifest['segments'].items() if name in SEGMENTS}


def native_step(lead):
    if type(lead) is not int or not 0 <= lead <= 168:
        raise ValueError('Forecast hour outside 0–168')
    interval = 1 if lead <= 90 else 3 if lead <= 144 else 6
    if lead % interval:
        raise ValueError('Forecast hour is not an original model output time')
    return interval


def native_field(run, lead, variable):
    import openmeteo_regional as regional
    if regional.exists(run):
        return regional.points(),regional.field(run,lead,variable).ravel()
    native_step(lead)
    if lead>SEGMENTS[expected_segments(run)[-1]][1]:
        raise ValueError('Forecast hour beyond this initialization horizon')
    name = 'hourly' if lead <= 90 else 'three_hourly' if lead <= 144 else 'six_hourly'
    meta, leads, points, arrays = segments(run)[name]
    if variable not in VARIABLES:
        raise ValueError('Unsupported surface variable')
    return points, arrays[variable][int(np.flatnonzero(leads == lead)[0])]


def rain(run, lead, hours):
    import openmeteo_regional as regional
    if regional.exists(run):
        return regional.points(),regional.rain(run,lead,hours).ravel()
    native_step(lead)
    if hours not in RAIN_WINDOWS or lead < hours:
        raise ValueError('Rain interval starts before initialization')
    cache = segments(run)
    end, pieces, points = lead, [], None
    while end > lead-hours:
        width = native_step(end)
        if end-width < lead-hours:
            raise ValueError('Rain interval splits a native forecast interval')
        name = 'hourly' if end <= 90 else 'three_hourly' if end <= 144 else 'six_hourly'
        _, leads, current_points, arrays = cache[name]
        if points is not None and not np.array_equal(points, current_points):
            raise ValueError('Native grids changed between rain intervals')
        points = current_points
        pieces.append(arrays['precipitation'][int(np.flatnonzero(leads == end)[0])])
        end -= width
    return points, np.sum(pieces, axis=0, dtype=np.float64)


def runs():
    import openmeteo_regional as regional
    result = []
    paths={p.parent.name:p for p in (ROOT/'runs').glob('??????????/run.json')}
    paths.update({p.parent.name:p for p in regional.root().glob('??????????/run.json')})
    for path in paths.values():
        try:
            manifest = read_manifest(path.parent.name)
            if not manifest['segments']:
                continue
            wide=manifest.get('schema')==regional.SCHEMA
            result.append(dict(id=path.parent.name, run=manifest['run'], bounds=regional.BOUNDS if wide else BOUNDS,
                               partial=False if wide else not set(expected_segments(path.parent.name))<=set(manifest['segments']),
                               regional=wide,legacy=not wide,frames=len(available_leads(manifest))))
        except (OSError, ValueError, KeyError):
            continue
    return sorted(result, key=lambda item: item['run'], reverse=True)


def available_leads(manifest):
    if manifest.get('schema')=='openmeteo-ifs-regional-v2':
        return manifest['leads']
    return sorted({h for name in manifest['segments'] if name in SEGMENTS
                   for h in range(SEGMENTS[name][0], SEGMENTS[name][1]+1, SEGMENTS[name][2])
                   if h == 0 or name == ('hourly' if h <= 90 else 'three_hourly' if h <= 144 else 'six_hourly')})


def catalog(ident=None):
    if ident is None:
        history = runs()
        if not history:
            raise FileNotFoundError('Open-Meteo forecast cache is being prepared')
        ident = history[0]['id']
    import openmeteo_regional as regional
    if regional.exists(ident):
        return regional.catalog(ident)
    manifest = read_manifest(ident)
    run = run_time(ident)
    return dict(schema=2, model=MODEL, run=iso(run), updated=manifest.get('updated'), bounds=BOUNDS,
                levels=['surface'], interval_hours=1, temporal_segments=[dict(start=a, end=b, hours=c) for key,(a,b,c) in SEGMENTS.items() if key in expected_segments(run)],
                native_grid='O1280', native_grid_km=9, display_grid_degrees=.05,
                frame_base='./api/v4/'+SLUG+'/frames', partial=not set(expected_segments(run))<=set(manifest['segments']),
                capabilities=dict(cyclones=False, pw=False, surface_rh=True, products=['wind','t','td','rh','rain']),
                source='ECMWF via Open-Meteo · CC BY 4.0',
                source_note='深圳及周边原生9 km地面场；0.05°仅为显示插值。0–90小时逐小时，90–144小时每3小时，其后每6小时。',
                frames=[dict(lead=h, valid=iso(run+timedelta(hours=h)), file=f'{SLUG}_{run_id(run)}_f{h:03d}.json')
                        for h in available_leads(manifest)], errors=[])


@lru_cache(maxsize=12)
def triangulation(point_bytes, count):
    points = np.frombuffer(point_bytes, dtype=np.float64).reshape(count, 2)
    return Delaunay(points), cKDTree(points)


def interpolate(points, values, targets, nearest=False):
    import openmeteo_regional as regional
    if points is regional.points():
        return regional.sample(np.asarray(values).reshape(len(regional.LAT),len(regional.LON)),targets,nearest)
    points = np.ascontiguousarray(points, dtype=np.float64)
    tri, tree = triangulation(points.tobytes(), len(points))
    targets = np.asarray(targets, dtype=float)
    simplex = tri.find_simplex(targets)
    inside = simplex >= 0
    out = np.full(len(targets), np.nan)
    if nearest:
        indices = tree.query(targets[inside])[1]
        out[inside] = values[indices]
    elif inside.any():
        s = simplex[inside]
        weights = np.einsum('ijk,ik->ij', tri.transform[s,:2], targets[inside]-tri.transform[s,2])
        weights = np.column_stack((weights, 1-weights.sum(axis=1)))
        neighbours = values[tri.simplices[s]]
        out[inside] = np.sum(neighbours*weights, axis=1)
    return out


def vector(run, lead):
    points, speed = native_field(run, lead, 'wind_speed_10m')
    other, direction = native_field(run, lead, 'wind_direction_10m')
    if not np.array_equal(points, other):
        raise ValueError('Wind grids differ')
    angle = np.deg2rad(direction)
    return points, -speed*np.sin(angle), -speed*np.cos(angle)


def clean(values, digits=3):
    return [round(float(value), digits) if np.isfinite(value) else None for value in np.asarray(values).ravel()]


def frame(ident, lead, hours=3, level='surface'):
    import openmeteo_regional as regional
    if regional.exists(ident):
        return regional.frame(ident,lead,level)
    if level!='surface':
        raise ValueError('Legacy regional run only has surface data')
    run = run_time(ident)
    native_step(lead)
    lon = np.arange(2272,2297)/20
    lat = np.arange(445,461)/20
    x,y = np.meshgrid(lon,lat)
    targets = np.column_stack((x.ravel(),y.ravel()))
    surface = {}
    for source,key in [('temperature_2m','t'),('relative_humidity_2m','rh'),('pressure_msl','msl')]:
        points, values = native_field(run,lead,source)
        surface[key] = clean(interpolate(points,values,targets))
    points,u,v = vector(run,lead)
    surface['u'],surface['v'] = clean(interpolate(points,u,targets)),clean(interpolate(points,v,targets))
    windows = {}
    for window in RAIN_WINDOWS:
        try:
            points, values = rain(run,lead,window)
            windows[window] = dict(values=clean(interpolate(points,values,targets,nearest=True)),
                                   start=iso(run+timedelta(hours=lead-window)),end=iso(run+timedelta(hours=lead)))
        except (ValueError,KeyError):
            continue
    return dict(schema=2, model=MODEL, run=iso(run), lead=lead, valid=iso(run+timedelta(hours=lead)),
                lon=lon.tolist(),lat=lat.tolist(),layers={'surface':surface},bounds=BOUNDS,
                native_grid='O1280',native_grid_km=9,display_grid_degrees=.05,
                interval_hours=native_step(lead),rain3h=windows.get(3,{}).get('values'),rain_windows=windows,
                rain_start=windows.get(3,{}).get('start'),source='ECMWF via Open-Meteo · CC BY 4.0')


def point_forecast(ident, longitude, latitude):
    import openmeteo_regional as regional
    if regional.exists(ident):
        return regional.point_forecast(ident,longitude,latitude)
    run = run_time(ident)
    target = np.array([[float(longitude),float(latitude)]])
    if not (BOUNDS[0] <= target[0,0] <= BOUNDS[2] and BOUNDS[1] <= target[0,1] <= BOUNDS[3]):
        raise ValueError('Point outside the cached Shenzhen region')
    rows = []
    for lead in available_leads(read_manifest(run)):
        points,t = native_field(run,lead,'temperature_2m')
        _,u,v = vector(run,lead)
        temp,uu,vv = (interpolate(points,a,target)[0] for a in (t,u,v))
        _,humidity = native_field(run,lead,'relative_humidity_2m')
        _,pressure = native_field(run,lead,'pressure_msl')
        try:
            points,r = rain(run,lead,native_step(lead))
            precipitation = interpolate(points,r,target,nearest=True)[0]
        except (ValueError,KeyError):
            precipitation = np.nan
        speed = math.hypot(uu,vv)
        direction = (math.degrees(math.atan2(-uu,-vv))+360)%360
        rows.append(dict(lead=lead,valid=iso(run+timedelta(hours=lead)),t=clean([temp])[0],
                         wind=clean([speed])[0],direction=clean([direction])[0],
                         rh=clean(interpolate(points,humidity,target))[0],msl=clean(interpolate(points,pressure,target))[0],
                         precipitation=clean([precipitation])[0],interval_hours=native_step(lead),
                         **{f'rain{native_step(lead)}h':clean([precipitation])[0]}))
    return dict(model=MODEL,run=iso(run),longitude=float(longitude),latitude=float(latitude),
                native_grid_km=9,display_grid_degrees=.05,rows=rows,start=rows[0]['valid'],end=rows[-1]['valid'],
                interval_hours=1,variable_intervals=True,source='ECMWF via Open-Meteo · CC BY 4.0')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run')
    parser.add_argument('--latest', action='store_true')
    parser.add_argument('--max-runs', type=int, default=1)
    parser.add_argument('--score-only', action='store_true')
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT/'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('An Open-Meteo update is already running',flush=True)
            return
        try:
            import openmeteo_regional as regional
            if args.run:
                regional.ingest(run_time(args.run))
            elif args.latest:
                now = datetime.now(UTC)-timedelta(hours=6)
                latest = now.replace(hour=now.hour//6*6, minute=0, second=0, microsecond=0)
                completed = {item['id'] for item in runs() if not item['partial'] and item.get('regional')}
                pending = [latest-timedelta(hours=6*i) for i in range(8)]
                for run in [r for r in pending if run_id(r) not in completed][:max(0,min(args.max_runs,4))]:
                    regional.ingest(run)
        except (OSError,ValueError,KeyError,RuntimeError,requests.RequestException) as error:
            atomic_json(ROOT/'status.json',dict(state='deferred',error_type=type(error).__name__,updated=iso(datetime.now(UTC))))
            print('Update deferred:',type(error).__name__,str(error)[:160],flush=True)
        from openmeteo_verification import refresh
        refresh()
        regional.prune()
        cutoff = datetime.now(UTC)-timedelta(days=40)
        base = (ROOT/'runs').resolve()
        for directory in base.glob('??????????'):
            if (re.fullmatch(r'\d{10}',directory.name) and not directory.is_symlink()
                    and directory.resolve().parent==base and run_time(directory.name)<cutoff):
                shutil.rmtree(directory)


if __name__ == '__main__':
    main()
