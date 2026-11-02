"""Small shared station contracts; importing this module never creates files."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import math
import os

UTC = timezone.utc
ROOT = Path(__file__).resolve().parent
BOX = (104., 18., 121., 27.)
REGION = f'华南 {BOX[0]:g}–{BOX[2]:g}°E / {BOX[1]:g}–{BOX[3]:g}°N'
PROXY = 'http://127.0.0.1:17908'
VARIABLES = {'t': ('2米气温', '°C'), 'td': ('露点温度', '°C'),
             'msl': ('海平面气压', 'hPa'), 'wind': ('10米风速', 'm/s'),
             'rain6': ('6小时降水', 'mm'), 'rain24': ('24小时降水', 'mm')}
THRESHOLDS = {'rain6': [.1, 1, 5, 10, 25, 50], 'rain24': [.1, 10, 25, 50, 100]}
MODELS = {'ec01': 'EC 0.1°', 'ecmwf': 'EC 0.25°', 'gfs': 'GFS',
          'aifs': 'ECAI', 'aigfs': 'GFSAI', 'gefs': 'GEFS集合平均', 'wn3': 'WeatherNext 3 mean'}
GRID_MODELS = dict(zip(MODELS, ('ECMWF-IFS-9km-OpenMeteo', 'ECMWF-IFS', 'NCEP-GFS',
                               'ECAI', 'GFSAI', 'GEFS集合平均', 'Google WeatherNext 3（集合均值）')))
GRID_DATASETS = {'t': 'station-气温-2m', 'rain6': 'station-降水-6', 'rain24': 'station-降水-24'}
GRID_THRESHOLDS = sorted({t for values in THRESHOLDS.values() for t in values})
# Both offline products use these additive sufficient statistics, in this order.
MOMENTS = ('n', 'error', 'absolute', 'squared', 'sf', 'so', 'sff', 'soo', 'sfo')


def bjt(value):
    return (stamp(value) + timedelta(hours=8)).strftime('%Y-%m-%dT%H:%M:%S')


def moment_score(values):
    n, error, absolute, squared, sf, so, sff, soo, sfo = values
    if not n:
        return dict(n=0, ME=None, MAE=None, RMSE=None, CC=None)
    vf, vo = max(0., sff-sf*sf/n), max(0., soo-so*so/n)
    cc = max(-1., min(1., (sfo-sf*so/n)/math.sqrt(vf*vo))) if n >= 3 and vf > 1e-8 and vo > 1e-8 else None
    return dict(n=n, ME=error/n, MAE=absolute/n, RMSE=math.sqrt(max(0., squared/n)), CC=cc)


def event_score(n, h, m, f):
    chance = (h+m)*(h+f)/n if n else 0
    def divide(a, b): return a/b if b > 0 else None
    return dict(n=n, H=h, M=m, F=f, C=n-h-m-f, TS=divide(h,h+m+f), POD=divide(h,h+m),
                FAR=divide(f,h+f), MR=divide(m,h+m), BIAS=divide(h+f,h+m),
                ETS=divide(h-chance,h+m+f-chance))


VERSION = 'stations-v1.0'
# Offline score windows (days back from the scoring end time).
DAY_WINDOWS = (1, 3, 7, 30)


def cache_root():
    return Path(os.environ.get('STATIONS_CACHE_DIR', str(ROOT / 'cache/stations'))).resolve()


def now():
    return datetime.now(UTC)


def stamp(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('UTC offset required')
    return result.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def in_box(lon, lat):
    return BOX[0] <= lon <= BOX[2] and BOX[1] <= lat <= BOX[3]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value, durable=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.next')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')))
    if durable:
        with temporary.open('rb') as handle:os.fsync(handle.fileno())
    temporary.replace(path)
    if durable:sync_directory(path.parent)


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None
