"""Forecast tracks for one issue time, for the typhoon page's single-issue view.

Every gridded model is followed with pangu_track on its own MSLP and 10 m wind, so the
lines are comparable. Reads local archives only, except Pangu (fetched on demand).
"""
from datetime import timedelta, timezone
from pathlib import Path
import json, math, os, re, sys

import numpy as np

from pangu_track import track

HERE = Path(__file__).resolve().parent
WEATHER = Path('/home/yjzx/verification-platform/gods-eye-weather/weather-data/region-v4')
WN3_ROOTS = (Path('/home/yjzx/verification-platform/wn3-levels'), Path('/home/yjzx/verification-platform/wn3-levels-archive'))
CACHE = HERE/'cache/typhoon-tracks'
LOCAL = {'ECMWF-IFS': 'ecmwf', 'NCEP-GFS': 'gfs', 'ECAI': 'aifs', 'GFSAI': 'aigfs'}
WN3 = 'WN3'
HORIZON = 120
ERROR_LEADS = (24, 48, 72, 96, 120)
VERSION = 1
BJT = timezone(timedelta(hours=8))


def local_fields(source, run, lead_max):
    """Regional 0.25 degree frames written by the main platform (60-150E, 0-60N)."""
    backend = str(HERE.parent/'gods-eye-weather/weather_backend')
    if backend not in sys.path: sys.path.append(backend)
    from global_store import field_directory
    slug = LOCAL[source]; name = run.strftime('%Y%m%d%H'); frames = []
    for lead in range(0, lead_max+1, 6):
        path = WEATHER/slug/f'{slug}_{name}_f{lead:03d}_global1.json'
        if not path.is_file(): continue
        meta = json.loads(path.read_text()); folder = field_directory(path)
        if meta.get('bounds') != [60.0, 0.0, 150.0, 60.0]: continue
        try: fields = [np.load(folder/f'{key}.npy') for key in ('surface_msl', 'surface_u', 'surface_v')]
        except OSError: continue
        frames.append((lead, np.asarray(meta['lon'], float), np.asarray(meta['lat'], float), fields))
    return stack(frames)


def wn3_fields(run, lead_max):
    """Member-mean surface fields kept beside the WN3 pressure levels (00Z/12Z)."""
    name = run.strftime('%Y%m%d_%Hhr_01_preds'); frames = []
    folder = next((root/name for root in WN3_ROOTS if (root/name).is_dir()), None)
    if folder is None: return None
    for lead in range(6, lead_max+1, 6):
        path = folder/f'sfc_{lead:03d}h.npz'
        if not path.is_file(): continue
        with np.load(path, allow_pickle=False) as z:
            if str(z['run']) != name or int(z['lead_h']) != lead or 'scalar:hPa(mslp)' not in set(map(str, z['units'])):
                raise ValueError('WN3 surface identity mismatch')
            frames.append((lead, np.asarray(z['lon'], float), np.asarray(z['lat'], float), [z['scalar'], z['u'], z['v']]))
    return stack(frames)


def stack(frames):
    if not frames: return None
    lon, lat = frames[0][1:3]
    if np.any(np.diff(lat) <= 0) or np.any(np.diff(lon) <= 0): raise ValueError('grid axes must ascend')
    return dict(lon=lon, lat=lat, leads=np.array([f[0] for f in frames]),
                msl=np.stack([f[3][0] for f in frames]), u10=np.stack([f[3][1] for f in frames]), v10=np.stack([f[3][2] for f in frames]))


def cached_native(read, root, source, run, lon, lat):
    """Tracks the landfall table already fetched, for storms older than the local fields."""
    slug = source.lower().replace('/', '-').replace(' ', '-'); best = None
    for path in root.glob(f'model_*_{slug}_{run:%Y%m%d%H}_*.json'):
        match = re.search(r'_(-?\d+\.\d)_(-?\d+\.\d)(?:_f\d+)?\.json$', path.name)
        if match:
            gap = math.hypot(float(match[1])-lon, float(match[2])-lat)
            if gap <= 3 and (best is None or gap < best[0]): best = (gap, path)
    if best is None: return []
    frame = read(best[1])
    return [] if frame is None or frame.empty else frame[['lead', 'time', 'lon', 'lat', 'pressure', 'wind']].to_dict('records')


def stored(source, run, lon, lat, build):
    """Model tracks never change once computed; a failed or empty result is not kept."""
    path = CACHE/f'{source.lower()}_{run:%Y%m%d%H}_{lon:.1f}_{lat:.1f}.json'
    try:
        saved = json.loads(path.read_text())
        if saved.get('version') == VERSION: return saved['rows']
    except (OSError, ValueError, KeyError): pass
    rows = [dict(row, time=row['time'].astimezone(BJT).isoformat()) for row in build()]
    if rows:
        CACHE.mkdir(parents=True, exist_ok=True); temp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
        temp.write_text(json.dumps(dict(version=VERSION, rows=rows), ensure_ascii=False)); os.replace(temp, path)
    return rows


def grid_rows(fields, run, lon, lat, lead_max=HORIZON):
    return track(fields, run.strftime('%Y%m%d%H'), lon, lat, lead_max) if fields else []


def interpolate(observed, moment):
    """Observed position and intensity at a time inside the best track, else None."""
    times = [row['stamp'] for row in observed]
    if not times or not times[0] <= moment <= times[-1]: return None
    point = {'lon': None, 'lat': None, 'pressure': None, 'wind': None}
    for key in point:
        pairs = [(row['stamp'].timestamp(), row[key]) for row in observed if row[key] is not None and math.isfinite(row[key])]
        if pairs: point[key] = float(np.interp(moment.timestamp(), *zip(*pairs)))
    return point


def distance_km(lon1, lat1, lon2, lat2):
    a, b, c, d = map(math.radians, (lon1, lat1, lon2, lat2))
    h = math.sin((d-b)/2)**2+math.cos(b)*math.cos(d)*math.sin((c-a)/2)**2
    return 6371*2*math.asin(math.sqrt(h))


def errors(rows, observed):
    """Position and intensity error at the standard leads; rows lacking truth are left out."""
    result = {}
    for lead in ERROR_LEADS:
        row = next((r for r in rows if r['lead'] == lead), None)
        if row is None: continue
        truth = interpolate(observed, row['stamp'])
        if truth is None: continue
        item = dict(km=round(distance_km(row['lon'], row['lat'], truth['lon'], truth['lat'])))
        for key, name in (('pressure', 'pressure'), ('wind', 'wind')):
            value = row.get(key)
            if value is not None and truth[key] is not None and math.isfinite(value): item[name] = round(value-truth[key], 1)
        result[lead] = item
    return result
