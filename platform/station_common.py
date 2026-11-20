"""Small shared station contracts; importing this module never creates files."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import math
import os

UTC = timezone.utc
ROOT = Path(__file__).resolve().parent
BOX = (97., 18., 123., 33.)
CMAGD_BOX = (93.64, 16.60, 126.34, 42.88)
CMAGD_MODELS = ('ecmwf', 'gfs', 'aifs', 'aigfs')
CMAGD_NOTICE = '这个区域里新加的国家站只评 EC 0.25°、GFS、ECAI、GFSAI 四个模式。'
REGION = f'中国南方 {BOX[0]:g}–{BOX[2]:g}°E / {BOX[1]:g}–{BOX[3]:g}°N'
PROXY = ''
# Scored on their own: they stay out of the southern pools and only show under their own map view.
XINJIANG = {'52112': dict(name='淖毛湖', lon=95.1333, lat=43.6, elevation=469., ghcnh_id='CHM00052112'),
            '52203': dict(name='哈密', lon=93.5167, lat=42.8167, elevation=739., ghcnh_id='CHI0000ZWHM'),
            '51495': dict(name='十三间房', lon=91.7333, lat=43.2167, elevation=732., ghcnh_id='CHM00051495')}
XJ_BOUNDS = (90., 41.2, 98., 45.8)
VARIABLES = {'t': ('2米气温', '°C'), 'td': ('露点温度', '°C'),
             'msl': ('海平面气压', 'hPa'), 'wind': ('10米风速', 'm/s'),
             'rain6': ('6小时降水', 'mm'), 'rain12': ('12小时降水', 'mm'), 'rain24': ('24小时降水', 'mm')}
THRESHOLDS = {'rain6': [.1, 1, 5, 10, 25, 50], 'rain12': [.1, 1, 5, 10, 25, 50, 100], 'rain24': [.1, 10, 25, 50, 100, 250]}
MODELS = {'ec01': 'EC 9 km', 'ecmwf': 'EC 0.25°', 'ecens': 'EC 集合平均', 'gfs': 'GFS',
          'aifs': 'ECAI', 'aigfs': 'GFSAI', 'gefs': 'GEFS集合平均', 'wn3': 'WeatherNext 3 mean',
          'wn3ml': 'WN3 订正（LightGBM）'}
GRID_MODELS = dict(zip(MODELS, ('ECMWF-IFS-9km-OpenMeteo', 'ECMWF-IFS', 'ECMWF-ENSEMBLES平均', 'NCEP-GFS',
                               'ECAI', 'GFSAI', 'GEFS集合平均', 'Google WeatherNext 3（集合均值）', 'WN3 订正（LightGBM）')))
GRID_DATASETS = {'t': 'station-气温-2m', 'rain6': 'station-降水-6', 'rain12': 'station-降水-12', 'rain24': 'station-降水-24',
                 'msl': 'station-地面-msl', 'wind': 'station-地面-wind10'}
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
DAY_WINDOWS = (1, 3, 7, 15)
# 2026-11-16 用户同意配对只留 16 天（为扩到 CMA-GD 全域省空间）：页面最长近15天，留 1 天余量（原来 31 天）。
PAIR_DAYS = 16
RUN_DAYS = PAIR_DAYS + 10   # 起报保留：配对天数 + 最长时效 240 小时（原来 38 天）
COMMON_DAYS = 15            # 共同样本报告天数（原来 30）


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


def in_cmagd(lon, lat):
    return CMAGD_BOX[0] <= lon <= CMAGD_BOX[2] and CMAGD_BOX[1] <= lat <= CMAGD_BOX[3]


def model_allowed(station, model):
    return not station.get('cmagd_extended', False) or model in CMAGD_MODELS


def legacy_exclusions(db):
    """Offline producers set a private roster; direct legacy callers keep XJ only."""
    extra = ()
    if db.execute("SELECT 1 FROM sqlite_temp_master WHERE name='cmagd_extended'").fetchone():
        extra = (r[0] for r in db.execute('SELECT station FROM cmagd_extended'))
    return frozenset((*XINJIANG, *extra))


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
