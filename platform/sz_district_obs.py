"""Shenzhen district pseudo-stations and 59493 Shenzhen National Basic Station
observations from Shenzhen Meteorological Bureau Open Data APIs:
  - 120238293 (59493 深圳国家基本站 5-minute station observations)
  - 29200_00903509 (0.01 deg automatic-station grid analysis, cache/OBSERVATION/grid)
"""
from datetime import datetime, timedelta
from pathlib import Path
import json
import math
import os
import re
import time

from station_common import UTC, cache_root, iso, stamp

SOURCE = 'szgrid'
SZ_STATION_ID = '59493'
SZ_STATION_SOURCE = 'szopen'
STATION_API_URL = 'https://opendata.sz.gov.cn/api/120238293/1/service.xhtml'
DEFAULT_ROOT = '/home/yjzx/verification-platform/ec-gfs-server'
CODES = {'福田区': 'FT', '罗湖区': 'LH', '南山区': 'NS', '盐田区': 'YT', '宝安区': 'BA',
         '龙岗区': 'LG', '龙华区': 'LHU', '坪山区': 'PS', '光明区': 'GM', '大鹏新区': 'DP',
         '深汕特别合作区': 'SS'}

ALIAS = {'光明新区': '光明区', '龙华新区': '龙华区', '坪山新区': '坪山区', '深汕合作区': '深汕特别合作区'}


def app_root():
    return Path(os.environ.get('STATIONS_GRID_ROOT', DEFAULT_ROOT))


def _app_key(root=None):
    val = os.environ.get('APP_KEY')
    if val:
        return val
    secrets = (root or app_root()) / '.streamlit/secrets.toml'
    if secrets.is_file():
        m = re.search(r'APP_KEY\s*=\s*"([^"]+)"', secrets.read_text(encoding='utf-8'))
        if m:
            return m.group(1)
    return ''


def _fix(text):
    """The geojson attribute table is UTF-8 that was decoded as latin-1."""
    try:
        return text.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def grid_cells(root=None):
    import pandas as pd
    root = root or app_root()
    g = pd.read_excel(root / 'data/grid.xlsx', sheet_name='数据集1')
    cols = {'格网ID（唯一）': 'GRIDID', '格网左下角经度（度）': 'west', '格网右上角经度（度）': 'east',
            '格网左下角纬度（度）': 'south', '格网右上角纬度（度）': 'north'}
    g = g.rename(columns=cols)
    for c in cols.values():
        g[c] = pd.to_numeric(g[c], errors='coerce')
    g = g.dropna(subset=list(cols.values())).drop_duplicates('GRIDID')
    g['GRIDID'] = g.GRIDID.astype(int)
    g['lon'] = (g.west + g.east) / 2; g['lat'] = (g.south + g.north) / 2
    return g[['GRIDID', 'lon', 'lat']].reset_index(drop=True)


def district_stations(root=None):
    from shapely.geometry import shape
    from shapely.ops import unary_union
    root = root or app_root()
    data = json.loads((root / 'data/shenzhen_streets.geojson').read_text(encoding='utf-8'))
    parts = {}
    for feature in data['features']:
        name = ALIAS.get(_fix(feature['properties']['DISTRICT']), _fix(feature['properties']['DISTRICT']))
        parts.setdefault(name, []).append(shape(feature['geometry']))
    result = []
    gov = {"福田区": (114.0550, 22.5229), "罗湖区": (114.1314, 22.5484), "南山区": (113.9304, 22.5333),
           "盐田区": (114.2368, 22.5570), "宝安区": (113.8831, 22.5533), "龙岗区": (114.2469, 22.7210),
           "龙华区": (114.0450, 22.6967), "坪山区": (114.3461, 22.6910), "光明区": (113.9360, 22.7487),
           "大鹏新区": (114.4106, 22.6305)}
    for name, geoms in sorted(parts.items(), key=lambda kv: list(CODES).index(kv[0]) if kv[0] in CODES else 99):
        if name in gov:
            lon, lat = gov[name]
        else:
            area = unary_union(geoms)
            point = area.centroid
            if not area.covers(point):
                point = area.representative_point()
            lon, lat = round(point.x, 4), round(point.y, 4)
        result.append(dict(id="SZ-" + CODES.get(name, name), wmo=None, name=name, lon=lon,
                           lat=lat, elevation=None, type="szgrid", sources=[SOURCE],
                           coordinate_source="深圳各区政府/管委会驻地", model_elevations={}))
    return result


def add_stations(table, root=None):
    """Idempotently add district stations and normalize 59493 metadata."""
    for s in table.get('stations', []):
        if s.get('id') == SZ_STATION_ID:
            s['name'] = '深圳国家基本站'
            s['lon'] = 114.0
            s['lat'] = round(22 + 32 / 60, 4)
            s['elevation'] = 63.0
            s['coordinate_source'] = '深圳国家基本气象站 (竹子林)'
            if SZ_STATION_SOURCE not in s.get('sources', []):
                s['sources'] = [SZ_STATION_SOURCE] + list(s.get('sources', []))
        elif s.get('id') == 'ZGSZ':
            s['name'] = '深圳宝安机场'
    have = {s['id'] for s in table['stations']}
    new = [s for s in district_stations(root) if s['id'] not in have]
    table['stations'] = table['stations'] + new
    return table


class _Sampler:
    """Precomputed cell indices for one station set."""

    def __init__(self, stations, cells):
        import numpy as np
        self.np = np
        self.stations = stations
        lon = np.round((cells.lon.values - cells.lon.min()) / .01).astype(int)
        lat = np.round((cells.lat.values - cells.lat.min()) / .01).astype(int)
        self.lon0, self.lat0 = float(cells.lon.min()), float(cells.lat.min())
        self.index = np.full((lat.max() + 2, lon.max() + 2), -1, int)
        self.index[lat, lon] = cells.GRIDID.values
        self.plan = {}
        for s in stations:
            fx = (s['lon'] - self.lon0) / .01; fy = (s['lat'] - self.lat0) / .01
            ix, iy = int(math.floor(fx)), int(math.floor(fy)); wx, wy = fx - ix, fy - iy
            ids = []
            for dy, dx in ((0, 0), (0, 1), (1, 0), (1, 1)):
                y, x = iy + dy, ix + dx
                ids.append(int(self.index[y, x]) if 0 <= y < self.index.shape[0] and 0 <= x < self.index.shape[1] else -1)
            weights = [(1 - wx) * (1 - wy), wx * (1 - wy), (1 - wx) * wy, wx * wy]
            near = ids[int(np.argmax(weights))]
            if near < 0:
                d = (cells.lon.values - s['lon']) ** 2 + (cells.lat.values - s['lat']) ** 2
                near = int(cells.GRIDID.values[int(d.argmin())])
            self.plan[s['id']] = dict(ids=ids, weights=weights, nearest=near, full=min(ids) >= 0)

    def _frame_values(self, frame):
        return frame.set_index('GRIDID')

    def sample(self, frame, sid):
        p = self.plan[sid]; f = frame
        def cell(name, gid):
            try:
                v = float(f.at[gid, name])
            except KeyError:
                return None
            return v if math.isfinite(v) else None
        out = {}
        def bilinear(values):
            if p['full'] and all(v is not None for v in values):
                return sum(w * v for w, v in zip(p['weights'], values))
            return None
        ids = p['ids']
        temps = [cell('T', g) for g in ids] if p['full'] else None
        out['t'] = bilinear(temps) if temps else None
        if out['t'] is None:
            out['t'] = cell('T', p['nearest'])
        spd = [cell('WSPD', g) for g in ids] if p['full'] else None
        dirs = [cell('WDIR', g) for g in ids] if p['full'] else None
        wind = None
        if spd and all(v is not None for v in spd + dirs):
            u = [-s * math.sin(math.radians(d)) for s, d in zip(spd, dirs)]
            v = [-s * math.cos(math.radians(d)) for s, d in zip(spd, dirs)]
            wind = math.hypot(sum(w * x for w, x in zip(p['weights'], u)),
                              sum(w * x for w, x in zip(p['weights'], v)))
        if wind is None:
            wind = cell('WSPD', p['nearest'])
        out['wind'] = wind
        out['rain6'] = cell('RAIN06H', p['nearest'])
        return out


def _load_sz59493_api_records(end, cutoff, root=None):
    cache_file = cache_root() / 'raw/sz59493.json'
    if not cache_file.is_file():
        alt = Path(__file__).resolve().parent / 'cache/stations/raw/sz59493.json'
        if alt.is_file():
            cache_file = alt
    records = {}
    if cache_file.is_file():
        try:
            payload = json.loads(cache_file.read_text(encoding='utf-8'))
            for r in payload.get('data', []):
                dt = r.get('DDATETIME')
                if dt:
                    records[dt] = r
        except Exception:
            pass
    key = _app_key(root)
    stale = (not cache_file.is_file()) or (time.time() - cache_file.stat().st_mtime > 1800)
    if key and stale and os.environ.get('SZ59493_OFFLINE') != '1':
        try:
            import requests, urllib3
            urllib3.disable_warnings()
            s = requests.Session()
            s.proxies = {'http': '', 'https': ''}
            s.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'https://opendata.sz.gov.cn/'})
            q_start = (cutoff + timedelta(hours=8) - timedelta(days=2)).strftime('%Y%m%d')
            q_end = (end + timedelta(hours=8) + timedelta(days=2)).strftime('%Y%m%d')
            resp = s.post(STATION_API_URL, data={'appKey': key, 'page': 1, 'rows': 10000,
                                                 'startDate': q_start, 'endDate': q_end},
                          timeout=15, verify=False)
            if resp.ok:
                for r in resp.json().get('data', []):
                    dt = r.get('DDATETIME')
                    if dt:
                        records[dt] = r
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                ordered = [records[k] for k in sorted(records)]
                cache_file.write_text(json.dumps({'station': SZ_STATION_ID, 'source': STATION_API_URL, 'data': ordered}, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass
    return records


def _dewpoint(t, rh):
    if t is None or rh is None or not (1.0 <= rh <= 100.0):
        return None
    b, c = 17.625, 243.04
    gamma = math.log(rh / 100.0) + (b * t) / (c + t)
    return min(t, (c * gamma) / (b - gamma))


def _sz59493_api_by_time(records, cutoff, end):
    by_utc = {}
    for dt_str, r in records.items():
        try:
            bjt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
        u_time = (bjt - timedelta(hours=8)).replace(tzinfo=UTC)
        by_utc[u_time] = r

    out = {}
    rain6_by_at = {}
    at = (cutoff - timedelta(hours=24)).replace(minute=0, second=0, microsecond=0)
    while at <= end:
        if at.hour % 3 == 0:
            vals = {}
            exact = by_utc.get(at)
            if exact is not None:
                try:
                    t_raw = float(exact.get('T', -9999))
                    if -500 <= t_raw <= 600:
                        vals['t'] = t_raw / 10.0
                except (TypeError, ValueError):
                    pass
                try:
                    u_raw = float(exact.get('U', -9999))
                    if 't' in vals and 1 <= u_raw <= 100:
                        td = _dewpoint(vals['t'], u_raw)
                        if td is not None:
                            vals['td'] = td
                except (TypeError, ValueError):
                    pass
                try:
                    fs_raw = float(exact.get('FS', -9999))
                    if 0 <= fs_raw <= 1000:
                        vals['wind'] = fs_raw / 10.0
                except (TypeError, ValueError):
                    pass
            # 6h precipitation sum over 72 5-minute steps in (at - 6h, at]
            # 2026-11-16：开放数据每 5 分钟一条，但 R 只是那 1 分钟的雨量（0.1 毫米），
            # 直接累加只有真值约 1/5（11-14 与国家站逐 3 小时比稳定差约 5.1 倍）。按 ×5 估 5 分钟雨量，
            # 只作兜底：雨量优先用国家站和地面报文（见 station_obs.select_observations）。
            r_steps = []
            step = at - timedelta(hours=6) + timedelta(minutes=5)
            while step <= at:
                rec = by_utc.get(step)
                if rec is not None:
                    try:
                        rv = float(rec.get('R', -9999))
                        if 0 <= rv <= 5000:
                            r_steps.append(rv / 10.0 * 5)
                    except (TypeError, ValueError):
                        pass
                step += timedelta(minutes=5)
            if len(r_steps) >= 60:
                r6 = sum(r_steps)
                vals['rain6'] = r6
                rain6_by_at[at] = r6
            if at >= cutoff and vals:
                out[at] = vals
        at += timedelta(hours=1)

    for at, vals in list(out.items()):
        parts = [rain6_by_at.get(at - timedelta(hours=6 * k)) for k in range(4)]
        if all(p is not None for p in parts):
            vals['rain24'] = sum(parts)
    return out


def observation_rows(stations, end, cutoff, root=None):
    """Observation rows for SZ-* district stations and 59493 Shenzhen National Basic Station."""
    import pandas as pd
    root = root or app_root()
    mine = [s for s in stations if s.get('type') == 'szgrid']
    sz_basic = [s for s in stations if s.get('id') == SZ_STATION_ID and (
        SZ_STATION_SOURCE in s.get('sources', []) or str(s.get('coordinate_source', '')).startswith('深圳')
    )]
    folder = root / 'cache/OBSERVATION/grid'
    extra = Path(os.environ.get('STATIONS_GRID_EXTRA', Path(__file__).resolve().parent / 'cache/obs-backfill/grid'))
    if not mine and not sz_basic:
        return []
    sampler = _Sampler(mine + sz_basic, grid_cells(root))
    frames = {}

    def load(at):
        name = (at + timedelta(hours=8)).strftime('%Y%m%d%H') + '.pkl'
        if name not in frames:
            frames[name] = None
            for d in (folder, extra):
                if (d / name).is_file():
                    try:
                        frames[name] = pd.read_pickle(d / name).drop_duplicates('GRIDID').set_index('GRIDID')
                    except Exception:
                        frames[name] = None
                    break
        return frames[name]

    api_59493 = _sz59493_api_by_time(_load_sz59493_api_records(end, cutoff, root), cutoff, end) if sz_basic else {}

    rows = []
    at = cutoff.replace(minute=0, second=0, microsecond=0)
    while at <= end:
        if at.hour % 3 == 0:
            frame = load(at)
            parts = [load(at - timedelta(hours=6 * k)) for k in range(4)] if frame is not None else []
            if frame is not None:
                for s in mine:
                    vals = sampler.sample(frame, s['id'])
                    for var in ('t', 'wind', 'rain6'):
                        if vals[var] is not None:
                            rows.append(_row(s['id'], at, var, vals[var], source=SOURCE))
                    if all(p is not None for p in parts):
                        total = [sampler.sample(p, s['id'])['rain6'] for p in parts]
                        if all(v is not None for v in total):
                            rows.append(_row(s['id'], at, 'rain24', sum(total), source=SOURCE, hours=24))
            for s in sz_basic:
                direct = dict(api_59493.get(at, {}))
                if frame is not None:
                    grid_vals = sampler.sample(frame, s['id'])
                    for var in ('t', 'wind', 'rain6'):
                        if direct.get(var) is None and grid_vals.get(var) is not None:
                            direct[var] = grid_vals[var]
                    if direct.get('rain24') is None and all(p is not None for p in parts):
                        total = [sampler.sample(p, s['id'])['rain6'] for p in parts]
                        if all(v is not None for v in total):
                            direct['rain24'] = sum(total)
                for var in ('t', 'td', 'wind', 'rain6'):
                    if direct.get(var) is not None:
                        rows.append(_row(s['id'], at, var, direct[var], source=SZ_STATION_SOURCE))
                if direct.get('rain24') is not None:
                    rows.append(_row(s['id'], at, 'rain24', direct['rain24'], source=SZ_STATION_SOURCE, hours=24))
        at += timedelta(hours=1)
    return rows


def _row(station, at, var, value, source=SOURCE, **extra):
    return dict(station=station, time=iso(at), var=var, value=round(float(value), 3), source=source,
                flags=[], trace=False, **extra)
